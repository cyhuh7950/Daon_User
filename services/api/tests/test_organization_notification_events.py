"""S42 조직 역할 변경의 실제 PG 원장/source 경계. DB 수명·실행은 main 소유다.

기존 소유 QA0069에 고유 합성 scope만 준비한다. 정식 조직/역할·Provider·HTTP
성공 증거가 아니며 migration/role/부모 권한·RLS는 변경하지 않는다.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
import pytest

from daon_user_api.postgres_adapters import PostgresOrganizationRepository
from test_notification_events import _PrivateDsn


@pytest.mark.parametrize('value', [None, 0, 1, '', 'false', 'true', []])
def test_postgres_organization_notification_flag_rejects_non_boolean_before_connect(value, monkeypatch):
    def unexpected_connect(self):
        pytest.fail('INVALID_NOTIFICATION_FLAG_MUST_NOT_OPEN_DATABASE')

    monkeypatch.setattr(PostgresOrganizationRepository, '_connect', unexpected_connect)
    with pytest.raises(ValueError, match='^NOTIFICATION_INBOX_FLAG_INVALID$'):
        PostgresOrganizationRepository('not-used', notification_inbox_enabled=value)


@pytest.mark.parametrize('arguments,expected', [({}, False), ({'notification_inbox_enabled': True}, True)])
def test_postgres_organization_notification_flag_is_explicit_and_default_off(arguments, expected, monkeypatch):
    class Connection:
        def execute(self, query):
            assert query == 'SELECT 1'

        def close(self):
            pass

    monkeypatch.setattr(PostgresOrganizationRepository, '_connect', lambda self: Connection())
    repository = PostgresOrganizationRepository('not-used', **arguments)
    assert repository._notification_inbox_enabled is expected


def _operation(case, operation, target_id, payload, key):
    return case.repository.execute_management_operation(operation=operation,
        actor_id=case.manager, tenant_id=case.tenant, target_id=target_id, payload=payload,
        idempotency_key=key,
        request_fingerprint=hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        trace_id=case.trace, policy_version=case.policy)


@pytest.fixture
def organization_notification_case():
    from daon_user_api.identity import MINIMUM_STEP_UP_ACTION_GROUPS

    configured = os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not configured:
        pytest.skip('DAON_NOTIFICATION_TEST_DSN 미제공: 실제 PG 미실행')
    admin = _PrivateDsn(configured)
    database = 'daon_user_s42_qa_20261017_l2'
    if conninfo_to_dict(admin).get('dbname') != database:
        pytest.fail('S42_ORGANIZATION_OWNED_QA_DATABASE_REQUIRED', pytrace=False)
    with psycopg.connect(admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_database(),(SELECT version_num FROM public.alembic_version)').fetchone() == (
            database, '0069')
        assert connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone() == (
            'S42 L2a disposable QA 2026-10-17 main owned; remove after tests; no production data',)
    suffix = uuid4().hex
    case = SimpleNamespace(admin=admin, app=_PrivateDsn(make_conninfo(admin, options='-c role=daon_app')),
        tenant='s42-org-tenant-' + suffix, workspace='s42-org-workspace-' + suffix,
        manager='s42-org-admin-' + suffix, recipient='s42-org-user-' + suffix,
        trace='s42-org-trace-' + suffix, policy='runtime-policy-v1')
    now = datetime.now(timezone.utc)
    # 실제 기존 가입/권한 연결 API가 소비할 비민감 초기 metadata다. 실행은 main만 한다.
    with psycopg.connect(admin, connect_timeout=3) as connection:
        connection.execute('INSERT INTO public.identity_tenants(tenant_id) VALUES(%s)', (case.tenant,))
        for action in MINIMUM_STEP_UP_ACTION_GROUPS:
            connection.execute('INSERT INTO public.identity_tenant_step_up_actions(tenant_id,action_group,is_mandatory) VALUES(%s,%s,1)',
                (case.tenant, action))
        for user in (case.manager, case.recipient):
            connection.execute('INSERT INTO public.identity_users(user_id,issuer,subject,login_id,email,email_verified_at,'
                "password_change_required,state) VALUES(%s,'local',%s,%s,%s,%s,false,'active')",
                (user, user, user, user+'@example.test', now))
        connection.execute("INSERT INTO public.identity_memberships VALUES(%s,%s,'organization_admin')", (case.tenant, case.manager))
        connection.execute("INSERT INTO public.tenants(tenant_id,display_name) VALUES(%s,'S42 synthetic organization')", (case.tenant,))
        connection.execute("INSERT INTO public.workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,'S42 synthetic organization workspace')",
            (case.tenant, case.workspace))
        for user in (case.manager, case.recipient):
            connection.execute('INSERT INTO public.user_accounts(tenant_id,user_id) VALUES(%s,%s)', (case.tenant, user))
        connection.execute("INSERT INTO public.identity_auth_workspaces VALUES(%s,%s,'organization','cloud_sync',1000,1,1,%s)",
            (case.workspace, case.tenant, now))
        connection.execute("INSERT INTO public.identity_auth_tenant_roles VALUES(%s,%s,'organization_admin','active',1,%s)",
            (case.tenant, case.manager, now))
        connection.execute('INSERT INTO public.identity_auth_tenant_policies VALUES(%s,1,%s)', (case.tenant, now))
        connection.execute('INSERT INTO public.identity_auth_workspace_policies VALUES(%s,%s,1,%s)', (case.tenant, case.workspace, now))
    supported = 'notification_inbox_enabled' in inspect.signature(PostgresOrganizationRepository).parameters
    case.flag_supported = supported
    case.repository = PostgresOrganizationRepository(case.app, **({'notification_inbox_enabled': True} if supported else {}))
    try:
        code = 's42-org-synthetic-invitation-' + suffix
        invitation = case.repository.create_invitation(tenant_id=case.tenant, created_by=case.manager,
            code=code, expires_at=now+timedelta(days=1), max_uses=1, workspace_id=case.workspace, now=now)
        request = case.repository.create_join_request(tenant_id=case.tenant, user_id=case.recipient,
            invitation_code=code, now=now)
        approved = _operation(case, 'organization.join.decide', request.request_id,
            {'approved': True, 'expected_version': 1, 'role': 'member'}, 's42-org-prepare-approval-0001')
        assert approved.replayed is False and approved.value['state'] == 'approved'
        with psycopg.connect(admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT role,state,version,join_workspace_id FROM public.identity_org_tenant_memberships '
                'WHERE tenant_id=%s AND user_id=%s', (case.tenant, case.recipient)).fetchone() == (
                    'member', 'active', 1, case.workspace)
            assert connection.execute('SELECT role,state,version FROM public.identity_auth_memberships '
                'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s',
                (case.tenant, case.workspace, case.recipient)).fetchone() == ('viewer', 'active', 1)
            assert connection.execute('SELECT used_count FROM public.identity_org_invitation_codes WHERE invitation_id=%s',
                (invitation.invitation_id,)).fetchone() == (1,)
            assert connection.execute('SELECT count(*) FROM public.notification_source_outbox WHERE tenant_id=%s',
                (case.tenant,)).fetchone() == (0,)
        yield case
    finally:
        case.repository.close()


def _organization_notification_ledger(case):
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        return {table: connection.execute(
            f'SELECT to_jsonb(x) FROM public.{table} x WHERE tenant_id=%s ORDER BY to_jsonb(x)::text',
            (case.tenant,)).fetchall() for table in (
                'identity_org_operation_outbox', 'identity_org_tenant_memberships', 'identity_auth_memberships',
                'identity_auth_workspaces', 'identity_memberships', 'identity_org_role_history',
                'notifications', 'notification_details', 'notification_source_outbox', 'notification_audit_outbox')}


def test_organization_role_change_records_one_target_source_in_business_transaction_and_replay_writes_zero(organization_notification_case):
    """실제 viewer→editor 및 원감사 의도/source는 같은 Tx, replay는 원장 전체 쓰기0이다."""
    from daon_user_api.authorization import Action, Role
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification_events import _NotificationTupleReadConnection
    from daon_user_api.open_law_source_access import _current_auth_grant

    case = organization_notification_case
    payload = dict(tenant_id=case.tenant, user_id=case.recipient, workspace_id=case.workspace,
        role='editor', expected_version=1, expected_auth_version=1, reason='S42_REASON_NOT_NOTIFICATION_TEXT')
    key = 's42-org-role-change-0001'
    # 실제 저장 역할로 현재 VIEW를 확인한다. principal/권한 판정 대역을 만들지 않는다.
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        reader = _NotificationTupleReadConnection(connection)
        assert _current_auth_grant(CloudAccessContext(case.tenant, case.workspace, case.manager, 'view'), reader, Action.VIEW).role is Role.ORGANIZATION_ADMIN
        assert _current_auth_grant(CloudAccessContext(case.tenant, case.workspace, case.recipient, 'view'), reader, Action.VIEW).role is Role.VIEWER
    changed = _operation(case, 'organization.member.role', case.recipient, payload, key)
    assert changed.replayed is False
    assert (changed.value['role'], changed.value['state'], changed.value['version'], changed.value['join_workspace_id'],
        changed.value['access_role'], changed.value['access_state'], changed.value['access_version']) == (
            'member', 'active', 2, case.workspace, 'editor', 'active', 2)
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        rows = connection.execute('SELECT * FROM public.identity_org_operation_outbox WHERE tenant_id=%s '
            "AND actor_id=%s AND operation='organization.member.role' AND idempotency_key=%s",
            (case.tenant, case.manager, key)).fetchall()
        assert len(rows) == 1
        origin = rows[0]
        assert (origin['actor_id'], origin['target_id'], origin['trace_id'], origin['policy_version'], origin['delivered_at']) == (
            case.manager, case.recipient, case.trace, case.policy, None)
        assert json.loads(origin['response_json']) == changed.value
        assert json.loads(origin['audit_json']) == {'action': 'organization.membership.role_changed',
            'workspace_id': case.workspace, 'before': 'viewer', 'after': 'editor',
            'reason': payload['reason'], 'version': 2, 'auth_version': 2}
        assert connection.execute('SELECT role,state,version FROM public.identity_auth_memberships WHERE tenant_id=%s '
            'AND workspace_id=%s AND user_id=%s', (case.tenant, case.workspace, case.recipient)).fetchone() == {
                'role': 'editor', 'state': 'active', 'version': 2}
        source = connection.execute('SELECT * FROM public.notification_source_outbox WHERE tenant_id=%s', (case.tenant,)).fetchall()
        assert len(source) == 1, 'ORGANIZATION_ROLE_CHANGE_SOURCE_INTENT_MISSING'
        expected_id = 'organization-event-' + hashlib.sha256('|'.join((
            case.tenant, case.workspace, case.recipient, '2', '2')).encode()).hexdigest()[:32]
        assert source == [dict(tenant_id=case.tenant, workspace_id=case.workspace,
            source_event_id=expected_id, origin_event_id=origin['event_id'], source_store='organization',
            action='organization.membership.role_changed', target_type='membership', target_id=case.recipient,
            recipient_id=case.recipient, kind='membership', severity='info', notebook_id=None,
            trace_id=case.trace, policy_version=case.policy, occurred_at=origin['created_at'], state='pending', version=1, delivered_at=None)]
        assert connection.execute('SELECT o.xmin=s.xmin AND s.xmin=m.xmin AND m.xmin=w.xmin AND w.xmin=f.xmin AS atomic '
            'FROM public.identity_org_operation_outbox o JOIN public.notification_source_outbox s '
            'ON s.tenant_id=o.tenant_id AND s.origin_event_id=o.event_id '
            'JOIN public.identity_auth_memberships m ON m.tenant_id=s.tenant_id AND m.workspace_id=s.workspace_id AND m.user_id=s.recipient_id '
            'JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=m.workspace_id '
            'JOIN public.identity_org_tenant_memberships f ON f.tenant_id=m.tenant_id AND f.user_id=m.user_id '
            'WHERE o.event_id=%s', (origin['event_id'],)).fetchall() == [{'atomic': True}]
        assert case.flag_supported, 'ORGANIZATION_NOTIFICATION_FLAG_NOT_CONNECTED'
    before = _organization_notification_ledger(case)
    replay = _operation(case, 'organization.member.role', case.recipient, payload, key)
    assert replay.replayed is True and replay.value == changed.value
    assert _organization_notification_ledger(case) == before
    assert before['notifications'] == before['notification_details'] == before['notification_audit_outbox'] == []
    # 실제 다음 전이는 새 workflow/auth version이므로 정상 source를 추가할 수 있다.
    second_payload = {**payload, 'role': 'viewer', 'expected_version': 2, 'expected_auth_version': 2}
    second = _operation(case, 'organization.member.role', case.recipient, second_payload, 's42-org-role-change-0002')
    assert (second.value['version'], second.value['access_version'], second.value['access_role']) == (3, 3, 'viewer')
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        sources = connection.execute('SELECT source_event_id,origin_event_id FROM public.notification_source_outbox '
            'WHERE tenant_id=%s ORDER BY source_event_id', (case.tenant,)).fetchall()
    assert len(sources) == len({row[0] for row in sources}) == len({row[1] for row in sources}) == 2
    second_id = 'organization-event-' + hashlib.sha256('|'.join((
        case.tenant, case.workspace, case.recipient, '3', '3')).encode()).hexdigest()[:32]
    assert {row[0] for row in sources} == {expected_id, second_id}


def test_organization_role_source_insert_sql_failure_rolls_back_business_origin_and_can_retry(organization_notification_case, monkeypatch):
    """실제 제한 함수의 INSERT=1 직후 SQL22012면 역할·원장·source 전체가 rollback한다."""
    from daon_user_api.organization_membership import OrganizationWorkflowError
    import daon_user_api.notification_events as events

    case = organization_notification_case
    payload = dict(tenant_id=case.tenant, user_id=case.recipient, workspace_id=case.workspace,
        role='editor', expected_version=1, expected_auth_version=1, reason='S42_ROLLBACK_REASON_NOT_NOTIFICATION_TEXT')
    key = 's42-org-role-rollback-0001'
    before = _organization_notification_ledger(case)
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
                # candidate 함수의 실제 ROW_COUNT=1 반환이다. SELECT 금지 표를 관리자 문맥으로 읽지 않는다.
                assert row == (True,)
                phases.append('actual_source_insert_one')
                try:
                    self.raw.execute('SELECT 1/0')
                except psycopg.Error as error:
                    assert error.sqlstate == '22012'
                    phases.append('sql_22012')
                    raise
                pytest.fail('ORGANIZATION_SOURCE_SQL_FAILURE_NOT_INJECTED', pytrace=False)
            return row

    class ObservedConnection:
        def __init__(self, raw):
            self.raw = raw

        def __repr__(self):
            return '<S42 owned organization transaction observer>'

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def cursor(self, *args, **kwargs):
            return ObservedCursor(self.raw.cursor(*args, **kwargs), self.raw)

    def fail_after_actual_source(connection, **arguments):
        raw = connection._conn
        connection._conn = ObservedConnection(raw)
        try:
            # 실제 원업무·원장·알림 helper는 대체하지 않고, 실제 source 실행 뒤 오류만 주입한다.
            return actual_enqueue(connection, **arguments)
        finally:
            connection._conn = raw

    with monkeypatch.context() as patch:
        patch.setattr(events, 'enqueue_organization_role_event', fail_after_actual_source)
        with pytest.raises(OrganizationWorkflowError) as failed:
            _operation(case, 'organization.member.role', case.recipient, payload, key)
    assert (failed.value.code, failed.value.status) == ('PERSISTENCE_UNAVAILABLE', 503)
    assert phases == ['actual_source_insert_one', 'sql_22012']
    assert _organization_notification_ledger(case) == before
    retried = _operation(case, 'organization.member.role', case.recipient, payload, key)
    assert retried.replayed is False and retried.value['access_role'] == 'editor'
    assert retried.value['version'] == retried.value['access_version'] == 2
    after = _organization_notification_ledger(case)
    assert len(after['identity_org_operation_outbox']) == len(before['identity_org_operation_outbox']) + 1
    assert len(after['notification_source_outbox']) == 1
    source = after['notification_source_outbox'][0][0]
    assert (source['recipient_id'], source['target_id'], source['state'], source['version']) == (
        case.recipient, case.recipient, 'pending', 1)
    assert after['notifications'] == after['notification_details'] == after['notification_audit_outbox'] == []


def test_organization_role_source_rejects_private_exec_wrong_scope_and_committed_reemission(organization_notification_case):
    """기존 app 원장 RLS/private ACL 및 commit된 원업무 재발행 금지를 실제 SQL로 확인한다."""
    case = organization_notification_case
    payload = dict(tenant_id=case.tenant, user_id=case.recipient, workspace_id=case.workspace,
        role='editor', expected_version=1, expected_auth_version=1, reason='S42_BOUNDARY_REASON')
    _operation(case, 'organization.member.role', case.recipient, payload, 's42-org-boundary-0001')
    before = _organization_notification_ledger(case)
    records = [row[0] for row in before['identity_org_operation_outbox']]
    origin = next(row['event_id'] for row in records if row['operation'] == 'organization.member.role')
    unrelated = next(row['event_id'] for row in records if row['operation'] == 'organization.join.decide')
    cases = (
        ('private_exec', 'SELECT public.current_organization_role_notification_origin(%s,%s)',
            (origin, case.recipient), {}, '42501'),
        ('committed_origin', 'SELECT public.enqueue_organization_role_notification(%s,%s)',
            (origin, case.recipient), {}, '23514'),
        ('different_event', 'SELECT public.enqueue_organization_role_notification(%s,%s)',
            (unrelated, case.recipient), {}, '23514'),
        ('different_tenant', 'SELECT public.enqueue_organization_role_notification(%s,%s)',
            (origin, case.recipient), {'app.tenant_id': 's42-other-tenant'}, '23514'),
        ('different_manager', 'SELECT public.enqueue_organization_role_notification(%s,%s)',
            (origin, case.recipient), {'daon.organization_actor': 's42-other-manager'}, '23514'),
        ('different_recipient', 'SELECT public.enqueue_organization_role_notification(%s,%s)',
            (origin, 's42-other-recipient'), {}, '42501'),
    )
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        for label, query, parameters, overrides, state in cases:
            with pytest.raises(psycopg.Error) as rejected:
                with connection.transaction():
                    settings = {'app.tenant_id': case.tenant, 'app.workspace_id': case.workspace,
                        'app.actor_id': case.recipient, 'app.capability': 'view',
                        'app.notification_inbox_enabled': 'true', 'daon.organization_tenant': case.tenant,
                        'daon.organization_actor': case.manager, **overrides}
                    for name, value in settings.items():
                        connection.execute('SELECT set_config(%s,%s,true)', (name, value))
                    connection.execute(query, parameters).fetchone()
            assert rejected.value.sqlstate == state, label
    assert _organization_notification_ledger(case) == before


def test_organization_notification_probe_rejects_private_security_and_acl_changes(organization_notification_case):
    from daon_user_api.notification_postgres import notification_storage_is_protected

    case = organization_notification_case

    class RollbackProbe(Exception):
        pass

    mutations = (
        'ALTER FUNCTION public.current_organization_role_notification_origin(text,text) SECURITY DEFINER',
        'GRANT EXECUTE ON FUNCTION public.current_organization_role_notification_origin(text,text) TO daon_app',
        'ALTER FUNCTION public.enqueue_organization_role_notification(text,text) STABLE',
    )
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        assert notification_storage_is_protected(connection) is True
        for statement in mutations:
            with pytest.raises(RollbackProbe):
                with connection.transaction():
                    connection.execute(statement)
                    assert notification_storage_is_protected(connection) is False
                    raise RollbackProbe()
            assert notification_storage_is_protected(connection) is True


def test_organization_role_source_rejects_cross_manager_origin_clone_in_same_transaction(organization_notification_case, monkeypatch):
    """앱의 관리자별 RLS에 원장이 숨겨져도 같은 실제 전이의 source는 한 개여야 한다."""
    from daon_user_api.organization_membership import OrganizationWorkflowError
    import daon_user_api.notification_events as events
    from psycopg.rows import tuple_row

    case = organization_notification_case
    peer = case.manager + '-peer'
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        connection.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s42-fixture',%s,'active')", (peer, peer))
        connection.execute("INSERT INTO identity_memberships VALUES(%s,%s,'organization_admin')", (case.tenant, peer))
        connection.execute('INSERT INTO user_accounts(tenant_id,user_id) VALUES(%s,%s)', (case.tenant, peer))
        connection.execute("INSERT INTO identity_auth_tenant_roles VALUES(%s,%s,'organization_admin','active',1,now())", (case.tenant, peer))
    before = _organization_notification_ledger(case)
    actual_enqueue = events.enqueue_organization_role_event
    observed = []

    class UnsafeCloneAccepted(Exception):
        pass

    def clone_after_first_source(connection, **arguments):
        actual_enqueue(connection, **arguments)
        observed.append('actual_first_source')
        cursor = connection._conn.cursor(row_factory=tuple_row)
        columns = ('actor_id,operation,idempotency_key,tenant_id,target_id,request_fingerprint,'
            'response_json,event_id,audit_json,trace_id,policy_version,created_at,delivered_at')
        original = cursor.execute('SELECT '+columns+' FROM public.identity_org_operation_outbox WHERE event_id=%s',
            (arguments['origin_event_id'],)).fetchone()
        assert original is not None and original[0] == case.manager
        clone = list(original)
        clone[0], clone[2], clone[7] = peer, 's42-org-cloned-origin-key', 's42-org-clone-' + uuid4().hex
        cursor.execute("SELECT set_config('daon.organization_actor',%s,true)", (peer,))
        assert cursor.execute('SELECT count(*) FROM public.identity_org_operation_outbox WHERE event_id=%s',
            (arguments['origin_event_id'],)).fetchone() == (0,), '원관리자 이력이 다른 관리자 RLS에서 숨겨지는 실제 반례'
        cursor.execute('INSERT INTO public.identity_org_operation_outbox('+columns+') VALUES('
            + ','.join(['%s'] * len(clone)) + ')', clone)
        observed.append('actual_cross_manager_clone')
        actual_enqueue(connection, **{**arguments, 'origin_event_id': clone[7]})
        observed.append('unsafe_second_source_accepted')
        # 반례가 허용돼도 새 업무/원장/source를 실제로 commit하지 않는다.
        raise UnsafeCloneAccepted()

    outcome = None
    with monkeypatch.context() as patch:
        patch.setattr(events, 'enqueue_organization_role_event', clone_after_first_source)
        try:
            _operation(case, 'organization.member.role', case.recipient,
                dict(tenant_id=case.tenant, user_id=case.recipient, workspace_id=case.workspace,
                    role='editor', expected_version=1, expected_auth_version=1, reason='S42_CLONE_BOUNDARY'),
                's42-org-clone-boundary-0001')
        except UnsafeCloneAccepted:
            outcome = 'accepted'
        except OrganizationWorkflowError as error:
            assert (error.code, error.status) == ('PERSISTENCE_UNAVAILABLE', 503)
            outcome = 'rejected'
    assert _organization_notification_ledger(case) == before
    assert observed[:2] == ['actual_first_source', 'actual_cross_manager_clone']
    assert outcome == 'rejected', 'CROSS_MANAGER_ORIGIN_CLONE_ACCEPTED'
    assert observed == ['actual_first_source', 'actual_cross_manager_clone']
