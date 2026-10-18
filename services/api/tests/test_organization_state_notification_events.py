"""S42 실제 조직 상태 전이의 첫 source RED. PG 실행·DDL·자원 수명은 main 소유다.

초대/가입 초기 metadata는 기존 격리 fixture이고, 정지/재활성화는 실제 workflow
저장 메서드다. 원감사/source 합성, Provider·외부 송신·정식 조직 변경은 하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from daon_user_api.authorization import Action, Role
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.notification_events import _account_notification_scope
from daon_user_api.open_law_source_access import _AuthReadConnection, _current_auth_grant
from daon_user_api.organization_access import OrganizationAccessBridge
from daon_user_api.organization_membership import OrganizationWorkflowError
from test_organization_notification_events import (
    _operation,
    _organization_notification_ledger,
    organization_notification_case,
)


def _current_view(case):
    """현재 조직 중지 검사와 실제 저장 Action 규칙을 함께 사용한다. 가짜 principal은 없다."""
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        with _account_notification_scope(connection, tenant_id=case.tenant,
            workspace_id=case.workspace, actor_id=case.recipient) as reader:
            OrganizationAccessBridge(postgres=True).assert_organization_active(
                connection=_AuthReadConnection(reader), tenant_id=case.tenant, user_id=case.recipient)
            return _current_auth_grant(
                CloudAccessContext(case.tenant, case.workspace, case.recipient, 'view'), reader, Action.VIEW).role


def _auth_rows(case):
    """state 작업이 기존 auth/Workspace 행과 xmin을 바꾸지 않는 실제 계약을 고정한다."""
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        membership = connection.execute('SELECT to_jsonb(m),m.xmin::text '
            'FROM public.identity_auth_memberships m WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s',
            (case.tenant, case.workspace, case.recipient)).fetchone()
        workspace = connection.execute('SELECT to_jsonb(w),w.xmin::text '
            'FROM public.identity_auth_workspaces w WHERE tenant_id=%s AND workspace_id=%s',
            (case.tenant, case.workspace)).fetchone()
        assert membership is not None and workspace is not None
        assert (membership[0]['role'], membership[0]['state'], membership[0]['version']) == ('viewer', 'active', 1)
        return membership, workspace


def test_organization_state_reactivation_records_self_source_and_preserves_workflow_replay(organization_notification_case):
    """정지 VIEW 거부/source0 → 실제 재활성화/VIEW 복원 → state source1의 첫 RED다."""
    case = organization_notification_case
    assert _current_view(case) is Role.VIEWER
    auth_before = _auth_rows(case)
    suspend_payload = {'active': False, 'expected_version': 1, 'reason': 'S42_STATE_SUSPEND_REASON'}
    suspend_key = 's42-org-state-suspend-0001'
    suspended = _operation(case, 'organization.member.state', case.recipient, suspend_payload, suspend_key)
    assert suspended.replayed is False
    assert (suspended.value['tenant_id'], suspended.value['user_id'], suspended.value['role'],
        suspended.value['state'], suspended.value['version'], suspended.value['join_workspace_id'],
        suspended.value['access_role'], suspended.value['access_state'], suspended.value['access_version']) == (
            case.tenant, case.recipient, 'member', 'suspended', 2, case.workspace, 'viewer', 'active', 1)
    assert _auth_rows(case) == auth_before
    with pytest.raises(OrganizationWorkflowError) as denied:
        _current_view(case)
    assert (denied.value.code, denied.value.status) == ('ORGANIZATION_MEMBERSHIP_SUSPENDED', 403)
    suspended_ledger = _organization_notification_ledger(case)
    assert suspended_ledger['notification_source_outbox'] == []
    suspend_replay = _operation(case, 'organization.member.state', case.recipient, suspend_payload, suspend_key)
    assert suspend_replay.replayed is True and suspend_replay.value == suspended.value
    assert _organization_notification_ledger(case) == suspended_ledger

    active_payload = {'active': True, 'expected_version': 2, 'reason': 'S42_STATE_REASON_NOT_NOTIFICATION_TEXT'}
    active_key = 's42-org-state-reactivate-0001'
    activated = _operation(case, 'organization.member.state', case.recipient, active_payload, active_key)
    assert activated.replayed is False
    assert (activated.value['tenant_id'], activated.value['user_id'], activated.value['role'],
        activated.value['state'], activated.value['version'], activated.value['join_workspace_id'],
        activated.value['access_role'], activated.value['access_state'], activated.value['access_version']) == (
            case.tenant, case.recipient, 'member', 'active', 3, case.workspace, 'viewer', 'active', 1)
    assert _auth_rows(case) == auth_before
    assert _current_view(case) is Role.VIEWER

    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        rows = connection.execute('SELECT * FROM public.identity_org_operation_outbox WHERE tenant_id=%s '
            "AND actor_id=%s AND operation='organization.member.state'",
            (case.tenant, case.manager)).fetchall()
        assert len(rows) == 2
        origins = {row['idempotency_key']: row for row in rows}
        assert set(origins) == {suspend_key, active_key}
        for key, result, before, after, version, reason in (
            (suspend_key, suspended, 'active', 'suspended', 2, suspend_payload['reason']),
            (active_key, activated, 'suspended', 'active', 3, active_payload['reason']),
        ):
            origin = origins[key]
            assert (origin['tenant_id'], origin['actor_id'], origin['target_id'], origin['trace_id'],
                origin['policy_version'], origin['delivered_at']) == (
                    case.tenant, case.manager, case.recipient, case.trace, case.policy, None)
            assert json.loads(origin['response_json']) == result.value
            assert json.loads(origin['audit_json']) == {
                'action': 'organization.membership.state_changed', 'workspace_id': case.workspace,
                'before': before, 'after': after, 'reason': reason, 'version': version, 'auth_version': 1}
        origin = origins[active_key]
        assert connection.execute('SELECT o.xmin=f.xmin AS atomic '
            'FROM public.identity_org_operation_outbox o JOIN public.identity_org_tenant_memberships f '
            'ON f.tenant_id=o.tenant_id AND f.user_id=o.target_id '
            'WHERE o.tenant_id=%s AND o.event_id=%s', (case.tenant, origin['event_id'])).fetchone() == {'atomic': True}
        sources = connection.execute('SELECT * FROM public.notification_source_outbox WHERE tenant_id=%s',
            (case.tenant,)).fetchall()
        # 현재 role-only hook은 위 실제 업무/원장/현재 접근까지 통과한 뒤 여기서 RED여야 한다.
        assert len(sources) == 1, 'ORGANIZATION_STATE_REACTIVATION_SOURCE_INTENT_MISSING'
        expected_id = 'organization-event-' + hashlib.sha256('|'.join((
            case.tenant, case.workspace, case.recipient, '3', '1')).encode()).hexdigest()[:32]
        assert sources == [dict(tenant_id=case.tenant, workspace_id=case.workspace,
            source_event_id=expected_id, origin_event_id=origin['event_id'], source_store='organization',
            action='organization.membership.state_changed', target_type='membership', target_id=case.recipient,
            recipient_id=case.recipient, kind='membership', severity='info', notebook_id=None,
            trace_id=case.trace, policy_version=case.policy, occurred_at=origin['created_at'],
            state='pending', version=1, delivered_at=None)]
        assert connection.execute('SELECT o.xmin=s.xmin AND s.xmin=f.xmin AS atomic '
            'FROM public.identity_org_operation_outbox o JOIN public.notification_source_outbox s '
            'ON s.tenant_id=o.tenant_id AND s.origin_event_id=o.event_id '
            'JOIN public.identity_org_tenant_memberships f ON f.tenant_id=o.tenant_id AND f.user_id=o.target_id '
            'WHERE o.tenant_id=%s AND o.event_id=%s', (case.tenant, origin['event_id'])).fetchall() == [{'atomic': True}]

    before_replay = _organization_notification_ledger(case)
    replay = _operation(case, 'organization.member.state', case.recipient, active_payload, active_key)
    assert replay.replayed is True and replay.value == activated.value
    assert _organization_notification_ledger(case) == before_replay
    assert before_replay['notifications'] == before_replay['notification_details'] == before_replay['notification_audit_outbox'] == []
    # 기존 동값 작업의 workflow version 증가는 유지하되 실질 상태 변경이 아니므로 source는 늘리지 않는다.
    unchanged = _operation(case, 'organization.member.state', case.recipient,
        {**active_payload, 'expected_version': 3}, 's42-org-state-unchanged-0001')
    assert unchanged.replayed is False and unchanged.value['state'] == 'active' and unchanged.value['version'] == 4
    assert _organization_notification_ledger(case)['notification_source_outbox'] == before_replay['notification_source_outbox']
    assert _auth_rows(case) == auth_before


def test_organization_state_legacy_member_without_join_workspace_succeeds_without_source(organization_notification_case):
    """원 Workspace가 없는 기존 회원은 상태 저장을 유지하고 알림 때문에 503이 되지 않는다."""
    case = organization_notification_case
    legacy_user = 's42-org-nojoin-' + uuid4().hex
    # 신규 QA 초기 metadata만 준비한다. 기존 가입 회원/불변 원장/권한을 수정하지 않는다.
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        connection.execute("INSERT INTO public.identity_users(user_id,issuer,subject,state) "
            "VALUES(%s,'s42-nojoin-fixture',%s,'active')", (legacy_user, legacy_user))
        connection.execute("INSERT INTO public.identity_memberships VALUES(%s,%s,'member')", (case.tenant, legacy_user))
        connection.execute('INSERT INTO public.user_accounts(tenant_id,user_id) VALUES(%s,%s)',
            (case.tenant, legacy_user))
        connection.execute('INSERT INTO public.identity_org_tenant_memberships '
            '(tenant_id,user_id,role,state,version,updated_at,join_workspace_id) '
            "VALUES(%s,%s,'member','active',1,now(),NULL)", (case.tenant, legacy_user))
        assert connection.execute('SELECT role,state,version,join_workspace_id FROM public.identity_org_tenant_memberships '
            'WHERE tenant_id=%s AND user_id=%s', (case.tenant, legacy_user)).fetchone() == ('member', 'active', 1, None)
        assert connection.execute('SELECT count(*) FROM public.identity_auth_memberships WHERE tenant_id=%s AND user_id=%s',
            (case.tenant, legacy_user)).fetchone() == (0,)
    before = _organization_notification_ledger(case)
    payload = {'active': False, 'expected_version': 1, 'reason': 'S42_NOJOIN_STATE_REASON'}
    key = 's42-org-nojoin-state-0001'
    # 현재 후보의 예상 RED: 새 hook이 None Workspace를 ID 검사해 PERSISTENCE_UNAVAILABLE 503.
    changed = _operation(case, 'organization.member.state', legacy_user, payload, key)
    assert changed.replayed is False
    assert (changed.value['tenant_id'], changed.value['user_id'], changed.value['role'], changed.value['state'],
        changed.value['version'], changed.value['join_workspace_id'], changed.value['access_role'],
        changed.value['access_state'], changed.value['access_version']) == (
            case.tenant, legacy_user, 'member', 'suspended', 2, None, None, None, None)
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        assert connection.execute('SELECT role,state,version,join_workspace_id FROM public.identity_org_tenant_memberships '
            'WHERE tenant_id=%s AND user_id=%s', (case.tenant, legacy_user)).fetchone() == {
                'role': 'member', 'state': 'suspended', 'version': 2, 'join_workspace_id': None}
        rows = connection.execute('SELECT * FROM public.identity_org_operation_outbox WHERE tenant_id=%s '
            "AND actor_id=%s AND operation='organization.member.state' AND idempotency_key=%s",
            (case.tenant, case.manager, key)).fetchall()
        assert len(rows) == 1
        origin = rows[0]
        assert (origin['target_id'], origin['trace_id'], origin['policy_version'], origin['delivered_at']) == (
            legacy_user, case.trace, case.policy, None)
        assert json.loads(origin['response_json']) == changed.value
        assert json.loads(origin['audit_json']) == {
            'action': 'organization.membership.state_changed', 'workspace_id': None,
            'before': 'active', 'after': 'suspended', 'reason': payload['reason'], 'version': 2, 'auth_version': None}
        assert connection.execute('SELECT count(*) AS count FROM public.identity_auth_memberships '
            'WHERE tenant_id=%s AND user_id=%s', (case.tenant, legacy_user)).fetchone() == {'count': 0}
    after = _organization_notification_ledger(case)
    assert len(after['identity_org_operation_outbox']) == len(before['identity_org_operation_outbox']) + 1
    assert after['identity_auth_memberships'] == before['identity_auth_memberships']
    assert after['identity_auth_workspaces'] == before['identity_auth_workspaces']
    assert after['identity_memberships'] == before['identity_memberships']
    for table in ('notifications', 'notification_details', 'notification_source_outbox', 'notification_audit_outbox'):
        assert after[table] == before[table] == []
    replay = _operation(case, 'organization.member.state', legacy_user, payload, key)
    assert replay.replayed is True and replay.value == changed.value
    assert _organization_notification_ledger(case) == after


def test_organization_state_source_sql_failure_rolls_back_transition_origin_and_retries_same_key(
    organization_notification_case, monkeypatch,
):
    """실제 state source RPC True 직후 SQL22012면 전체 rollback하고 같은 key로 다시 저장한다."""
    import daon_user_api.notification_events as events

    case = organization_notification_case
    auth_before = _auth_rows(case)
    suspended = _operation(case, 'organization.member.state', case.recipient,
        {'active': False, 'expected_version': 1, 'reason': 'S42_STATE_ROLLBACK_PREP'},
        's42-org-state-rollback-prepare-0001')
    assert suspended.replayed is False and suspended.value['state'] == 'suspended' and suspended.value['version'] == 2
    assert _auth_rows(case) == auth_before
    with pytest.raises(OrganizationWorkflowError) as denied:
        _current_view(case)
    assert (denied.value.code, denied.value.status) == ('ORGANIZATION_MEMBERSHIP_SUSPENDED', 403)
    before = _organization_notification_ledger(case)
    assert before['notification_source_outbox'] == []
    payload = {'active': True, 'expected_version': 2, 'reason': 'S42_STATE_ROLLBACK_REASON'}
    key = 's42-org-state-source-rollback-0001'
    phases = []
    actual_enqueue = events.enqueue_organization_role_event

    class ObservedCursor:
        def __init__(self, cursor, raw):
            self.cursor, self.raw, self.source_call = cursor, raw, False

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def execute(self, query, params=None):
            self.cursor.execute(query, params)
            self.source_call = query == 'SELECT public.enqueue_organization_role_notification(%s,%s)'
            if self.source_call:
                assert params[1] == case.recipient
            return self

        def fetchone(self):
            row = self.cursor.fetchone()
            if self.source_call:
                # 실제 INSERT=1 반환 뒤에만 실패한다. 원장/source SELECT 권한을 추가하지 않는다.
                assert row == (True,)
                phases.append('actual_state_source_insert_one')
                try:
                    self.raw.execute('SELECT 1/0')
                except psycopg.Error as error:
                    assert error.sqlstate == '22012'
                    phases.append('sql_22012')
                    raise
                pytest.fail('ORGANIZATION_STATE_SOURCE_SQL_FAILURE_NOT_INJECTED', pytrace=False)
            return row

    class ObservedConnection:
        def __init__(self, raw):
            self.raw = raw

        def __repr__(self):
            return '<S42 owned state transaction observer>'

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def cursor(self, *args, **kwargs):
            return ObservedCursor(self.raw.cursor(*args, **kwargs), self.raw)

    def fail_after_actual_source(connection, **arguments):
        raw = connection._conn
        connection._conn = ObservedConnection(raw)
        try:
            # 실제 전이/원장/helper/RPC를 모두 실행하고 실제 SQL 오류만 주입한다.
            return actual_enqueue(connection, **arguments)
        finally:
            connection._conn = raw

    with monkeypatch.context() as patch:
        patch.setattr(events, 'enqueue_organization_role_event', fail_after_actual_source)
        with pytest.raises(OrganizationWorkflowError) as failed:
            _operation(case, 'organization.member.state', case.recipient, payload, key)
    assert (failed.value.code, failed.value.status) == ('PERSISTENCE_UNAVAILABLE', 503)
    assert phases == ['actual_state_source_insert_one', 'sql_22012']
    assert _organization_notification_ledger(case) == before
    assert _auth_rows(case) == auth_before

    retried = _operation(case, 'organization.member.state', case.recipient, payload, key)
    assert retried.replayed is False
    assert (retried.value['state'], retried.value['version'], retried.value['join_workspace_id'],
        retried.value['access_role'], retried.value['access_state'], retried.value['access_version']) == (
            'active', 3, case.workspace, 'viewer', 'active', 1)
    assert _auth_rows(case) == auth_before
    assert _current_view(case) is Role.VIEWER
    after = _organization_notification_ledger(case)
    assert len(after['identity_org_operation_outbox']) == len(before['identity_org_operation_outbox']) + 1
    assert len(after['identity_org_role_history']) == len(before['identity_org_role_history']) + 1
    assert len(after['notification_source_outbox']) == 1
    origins = [row[0] for row in after['identity_org_operation_outbox']
        if row[0]['operation'] == 'organization.member.state' and row[0]['idempotency_key'] == key]
    assert len(origins) == 1
    origin = origins[0]
    assert json.loads(origin['response_json']) == retried.value
    assert json.loads(origin['audit_json']) == {
        'action': 'organization.membership.state_changed', 'workspace_id': case.workspace,
        'before': 'suspended', 'after': 'active', 'reason': payload['reason'], 'version': 3, 'auth_version': 1}
    source = after['notification_source_outbox'][0][0]
    assert source == dict(tenant_id=case.tenant, workspace_id=case.workspace,
        source_event_id='organization-event-' + hashlib.sha256('|'.join((
            case.tenant, case.workspace, case.recipient, '3', '1')).encode()).hexdigest()[:32],
        origin_event_id=origin['event_id'], source_store='organization', action='organization.membership.state_changed',
        target_type='membership', target_id=case.recipient, recipient_id=case.recipient,
        kind='membership', severity='info', notebook_id=None, trace_id=case.trace, policy_version=case.policy,
        occurred_at=origin['created_at'], state='pending', version=1, delivered_at=None)
    assert after['notifications'] == after['notification_details'] == after['notification_audit_outbox'] == []
    replay = _operation(case, 'organization.member.state', case.recipient, payload, key)
    assert replay.replayed is True and replay.value == retried.value
    assert _organization_notification_ledger(case) == after
