"""S42 조직 정책의 실제 저장→범위별 source RED 후보. 실행·DDL은 main 소유다.

기존 소유 QA0069 fixture만 사용한다. 추가 Workspace는 경계 확인용 합성 metadata이며
정식 조직 생성·외부 전송·소비/재시작·전체 앱 자격 방어의 성공 증거가 아니다.
원감사/source는 실제 EgressPolicyService 저장으로만 생성한다.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from daon_user_api.cloud_storage import CloudAccessContext, CloudDatabaseError
from daon_user_api.egress_policy import EgressPolicyContext
from daon_user_api.notification_events import _account_notification_scope, _account_view_allowed
from test_egress_policy_notification_events import _policy_case, _snapshot
from test_notification_events import _studio_case_egress
from test_organization_notification_events import organization_notification_case


@pytest.fixture
def org_policy_case(organization_notification_case):
    # 재사용 fixture가 정확 DB명/comment/0069를 확인한다. 다른 DB fallback은 없다.
    case = organization_notification_case
    suffix = uuid4().hex
    second = 's42-org-policy-second-' + suffix
    personal = 's42-org-policy-personal-' + suffix
    foreign_tenant = 's42-org-policy-other-tenant-' + suffix
    foreign_workspace = 's42-org-policy-other-workspace-' + suffix
    now = datetime.now(timezone.utc)
    with _policy_case(case) as workspace_policy:
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            connection.execute('INSERT INTO public.identity_tenants(tenant_id) VALUES(%s)', (foreign_tenant,))
            connection.execute("INSERT INTO public.tenants(tenant_id,display_name) VALUES(%s,'S42 synthetic other organization')",
                (foreign_tenant,))
            connection.execute("INSERT INTO public.identity_memberships VALUES(%s,%s,'member')",
                (foreign_tenant, case.recipient))
            connection.execute('INSERT INTO public.user_accounts(tenant_id,user_id) VALUES(%s,%s)',
                (foreign_tenant, case.recipient))
            connection.execute('INSERT INTO public.identity_auth_tenant_policies VALUES(%s,1,%s)',
                (foreign_tenant, now))
            for tenant, workspace, kind in ((case.tenant, second, 'organization'),
                (case.tenant, personal, 'personal'), (foreign_tenant, foreign_workspace, 'organization')):
                connection.execute("INSERT INTO public.workspaces(tenant_id,workspace_id,display_name) "
                    "VALUES(%s,%s,'S42 synthetic policy boundary workspace')", (tenant, workspace))
                connection.execute('INSERT INTO public.identity_auth_workspaces '
                    'VALUES(%s,%s,%s,\'cloud_sync\',1000,1,1,%s)', (workspace, tenant, kind, now))
                connection.execute('INSERT INTO public.identity_auth_workspace_policies VALUES(%s,%s,1,%s)',
                    (tenant, workspace, now))
                connection.execute("INSERT INTO public.identity_auth_memberships "
                    "VALUES(%s,%s,%s,'viewer','active',1,%s)", (tenant, workspace, case.recipient, now))
                context = EgressPolicyContext(tenant, tenant, workspace, case.recipient, case.trace, case.policy)
                _studio_case_egress(connection, SimpleNamespace(context=context, suffix=uuid4().hex),
                    'workspace', workspace_policy.payload.as_dict(), 1)
            # 초기 조직 version1만 준비한다. version2/receipt/원감사는 실제 서비스가 만든다.
            _studio_case_egress(connection, SimpleNamespace(context=workspace_policy.context, suffix=uuid4().hex),
                'organization', workspace_policy.payload.as_dict(), 1)
        current = workspace_policy.repository.current(workspace_policy.context, 'organization')
        assert (current.scope_type, current.workspace_id, current.policy_version, current.binding_version,
            current.active, current.current, current.payload) == (
                'organization', None, 1, 1, True, True, workspace_policy.payload)
        expected = sorted((workspace, user) for workspace in (case.workspace, second)
            for user in (case.manager, case.recipient))
        # 개인/타 tenant의 VIEW도 실제로 허용된다. 권한 없음만으로 수신 제외가 통과하지 않게 한다.
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
            checks = [(case.tenant, workspace, user) for workspace, user in expected]
            checks.extend(((case.tenant, personal, case.recipient),
                (foreign_tenant, foreign_workspace, case.recipient)))
            for tenant, workspace, user in checks:
                with _account_notification_scope(connection, tenant_id=tenant, workspace_id=workspace,
                    actor_id=user) as reader:
                    assert _account_view_allowed(reader, CloudAccessContext(tenant, workspace, user, 'view'))
        yield SimpleNamespace(case=case, context=workspace_policy.context,
            service=workspace_policy.service, repository=workspace_policy.repository, store=workspace_policy.store,
            payload=workspace_policy.payload, current=current, expected=expected, second=second,
            personal=personal, foreign_tenant=foreign_tenant, foreign_workspace=foreign_workspace)


def _activate_org(policy, key):
    # 기존 Workspace 전용 _activate의 scope를 대역으로 바꾸지 않고 실제 조직 저장을 호출한다.
    return policy.service.create_and_activate(policy.context, scope_type='organization', payload=policy.payload,
        expected_etag=policy.current.etag, idempotency_key=key)


def _org_snapshot(policy):
    case = policy.case
    foreign = SimpleNamespace(admin=case.admin, tenant=policy.foreign_tenant,
        manager=case.manager, recipient=case.recipient)
    return {'origin_tenant': _snapshot(case), 'foreign_tenant': _snapshot(foreign)}


def _assert_org_change(policy, stored, key):
    case = policy.case
    assert (stored.tenant_id, stored.organization_id, stored.workspace_id, stored.scope_type,
        stored.policy_version, stored.binding_version, stored.policy_state, stored.active, stored.current,
        stored.payload) == (case.tenant, case.tenant, None, 'organization', 2, 2, 'active', True, True, policy.payload)
    assert stored.binding_id != policy.current.binding_id and stored.policy_version_id != policy.current.policy_version_id
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        versions = connection.execute('SELECT policy_version_id,workspace_id,organization_id,policy_version,'
            'state,created_by,trace_id,canonical_json FROM public.egress_policy_versions '
            "WHERE tenant_id=%s AND scope_type='organization' ORDER BY policy_version", (case.tenant,)).fetchall()
        assert versions == [dict(policy_version_id=identifier, workspace_id=None, organization_id=case.tenant,
            policy_version=number, state='active', created_by=case.manager, trace_id=case.trace,
            canonical_json=policy.payload.as_dict()) for identifier, number in (
                (policy.current.policy_version_id, 1), (stored.policy_version_id, 2))]
        bindings = connection.execute('SELECT binding_id,workspace_id,organization_id,policy_version_id,'
            'binding_version,active,current,created_by,trace_id FROM public.egress_policy_bindings '
            "WHERE tenant_id=%s AND scope_type='organization' ORDER BY binding_version", (case.tenant,)).fetchall()
        assert bindings == [dict(binding_id=binding, workspace_id=None, organization_id=case.tenant,
            policy_version_id=version, binding_version=number, active=active, current=active,
            created_by=case.manager, trace_id=case.trace) for binding, version, number, active in (
                (policy.current.binding_id, policy.current.policy_version_id, 1, False),
                (stored.binding_id, stored.policy_version_id, 2, True))]
        receipts = connection.execute('SELECT workspace_id,actor_id,operation,idempotency_key,result,status '
            'FROM public.idempotency_records WHERE tenant_id=%s', (case.tenant,)).fetchall()
        assert receipts == [dict(workspace_id=case.workspace, actor_id=case.manager,
            operation='egress_policy.organization.activate', idempotency_key=key,
            result={'policy_version_id': stored.policy_version_id, 'binding_id': stored.binding_id}, status='completed')]
        audits = connection.execute('SELECT * FROM public.audit_events WHERE tenant_id=%s', (case.tenant,)).fetchall()
        assert len(audits) == 1
        audit = audits[0]
        assert audit['event_id'].startswith('audit-egress-') and audit['occurred_at'].tzinfo is not None
        expected_origin = dict(tenant_id=case.tenant, workspace_id=case.workspace, actor_id=case.manager,
            action='egress_policy.activate', target_type='EgressPolicyBinding', target_id=stored.binding_id,
            outcome='succeeded', trace_id=case.trace, policy_version=case.policy, before_value=None, after_value=None,
            metadata={'scope_type': 'organization', 'policy_version_id': stored.policy_version_id, 'binding_version': 2})
        assert {name: audit[name] for name in expected_origin} == expected_origin
        # 현재 source 부재 RED는 실제 policy/binding/receipt/원감사 확인을 모두 지난 뒤 발생한다.
        sources = connection.execute('SELECT tenant_id,workspace_id,source_event_id,recipient_id,source_store,'
            'origin_event_id,action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,'
            'occurred_at,state,version,delivered_at FROM public.notification_source_outbox '
            'WHERE tenant_id IN (%s,%s) ORDER BY workspace_id,recipient_id',
            (case.tenant, policy.foreign_tenant)).fetchall()
        expected_sources = [dict(tenant_id=case.tenant, workspace_id=workspace,
            source_event_id='egress-policy-event-' + hashlib.sha256(
                f'{audit["event_id"]}|{workspace}|{user}'.encode()).hexdigest()[:32],
            recipient_id=user, source_store='authorization', origin_event_id=audit['event_id'],
            action='egress_policy.activate', target_type='EgressPolicyBinding', target_id=stored.binding_id,
            kind='policy', severity='info', notebook_id=None, trace_id=case.trace, policy_version=case.policy,
            occurred_at=audit['occurred_at'], state='pending', version=1, delivered_at=None)
            for workspace, user in policy.expected]
        assert sources == expected_sources, 'ORG_EGRESS_POLICY_CHANGED_SOURCE_INTENT_MISSING'
        assert any(row['workspace_id'] != audit['workspace_id'] for row in sources)
        atomic = connection.execute('SELECT s.workspace_id,s.recipient_id,'
            'p.xmin=b.xmin AND b.xmin=r.xmin AND r.xmin=a.xmin AND a.xmin=s.xmin AS same_transaction '
            'FROM public.egress_policy_versions p JOIN public.egress_policy_bindings b '
            'ON b.tenant_id=p.tenant_id AND b.policy_version_id=p.policy_version_id '
            'JOIN public.audit_events a ON a.tenant_id=b.tenant_id AND a.target_id=b.binding_id '
            "AND a.action='egress_policy.activate' JOIN public.idempotency_records r "
            'ON r.tenant_id=a.tenant_id AND r.workspace_id=a.workspace_id AND r.actor_id=a.actor_id '
            "AND r.operation='egress_policy.organization.activate' AND r.result->>'binding_id'=b.binding_id "
            'JOIN public.notification_source_outbox s ON s.tenant_id=a.tenant_id AND s.origin_event_id=a.event_id '
            'WHERE p.tenant_id=%s AND p.policy_version_id=%s AND b.binding_id=%s '
            'AND a.event_id=%s AND r.idempotency_key=%s ORDER BY s.workspace_id,s.recipient_id',
            (case.tenant, stored.policy_version_id, stored.binding_id, audit['event_id'], key)).fetchall()
        assert atomic == [dict(workspace_id=workspace, recipient_id=user, same_transaction=True)
            for workspace, user in policy.expected]


def test_org_policy_change_records_only_organization_workspace_sources_and_replay_writes_zero(org_policy_case):
    """조직 binding NULL과 원감사 WS를 보존하며 두 조직 WS의 현재 VIEW만 수신한다."""
    policy = org_policy_case
    before = _org_snapshot(policy)
    for scope in before.values():
        assert scope['notification_source_outbox'] == scope['audit_events'] == scope['idempotency_records'] == []
    key = 's42-org-policy-source-0001'
    stored = _activate_org(policy, key)
    _assert_org_change(policy, stored, key)
    after = _org_snapshot(policy)
    assert after['foreign_tenant'] == before['foreign_tenant']
    for table in before['origin_tenant']:
        if table not in {'egress_policy_versions', 'egress_policy_bindings', 'idempotency_records',
            'audit_events', 'notification_source_outbox'}:
            assert after['origin_tenant'][table] == before['origin_tenant'][table]
    assert _activate_org(policy, key) == stored
    assert policy.repository.current(policy.context, 'organization') == stored
    assert _org_snapshot(policy) == after


def test_org_policy_notifications_reopen_read_keep_v2_origin_and_recheck_current_recipient(org_policy_case):
    """실제 조직 source를 새 연결로 소비한다. principal은 서비스 포트 입력이며 로그인/HTTP 증거가 아니다."""
    from daon_user_api.notification import NotificationError
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    from test_egress_policy_notification_consumer import (
        _assert_materialized, _consumer, _list, _notification_service, _target,
    )

    policy, key = org_policy_case, 's42-org-policy-consumer-v2-0001'
    case = policy.case
    stored = _activate_org(policy, key)
    _assert_org_change(policy, stored, key)
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        audit = connection.execute('SELECT * FROM public.audit_events WHERE tenant_id=%s AND target_id=%s '
            "AND action='egress_policy.activate'", (case.tenant, stored.binding_id)).fetchone()
    assert audit is not None and audit['workspace_id'] == case.workspace
    consumer = _consumer()
    originals = {}
    # 각 소비 호출과 아래 repository 재진입은 실제 새 PG 연결을 사용한다.
    for workspace in (case.workspace, policy.second):
        scoped_case = SimpleNamespace(**{**vars(case), 'workspace': workspace})
        original = SimpleNamespace(stored=stored, audit=audit, source_ids={
            user: 'egress-policy-event-' + hashlib.sha256(
                f'{audit["event_id"]}|{workspace}|{user}'.encode()).hexdigest()[:32]
            for user in (case.manager, case.recipient)})
        originals[workspace] = original
        for user in (case.manager, case.recipient):
            assert _target(scoped_case, original, user)['accessible'] is True
            context = CloudAccessContext(case.tenant, workspace, user, 'view')
            assert consumer.materialize_pending_workspace_policies(case.app, context=context) == 1
            _assert_materialized(scoped_case, original, user)
            delivered = _org_snapshot(policy)
            assert consumer.materialize_pending_workspace_policies(case.app, context=context) == 0
            assert _org_snapshot(policy) == delivered

    scoped_case = SimpleNamespace(**{**vars(case), 'workspace': policy.second})
    original = originals[policy.second]
    service, repository, authorization_repository, principal = _notification_service(scoped_case, case.recipient)
    try:
        before_get = _org_snapshot(policy)
        page = _list(service, principal, scoped_case)
        assert len(page.items) == page.unread_count == 1
        item = page.items[0]
        assert (item.source_event_id, item.resource_type, item.resource_id, item.workspace_id) == (
            original.source_ids[case.recipient], 'EgressPolicyBinding', stored.binding_id, policy.second)
        assert service.get_notification(principal=principal, notification_id=item.notification_id,
            trace_id=case.trace, policy_version=case.policy) == item
        reopened = PostgresNotificationRepository(case.app,
            context=CloudAccessContext(case.tenant, policy.second, case.recipient, 'view'))
        assert reopened.notifications_for(tenant_id=case.tenant, recipient_id=case.recipient) == (item,)
        assert _org_snapshot(policy) == before_get
        arguments = dict(principal=principal, notification_id=item.notification_id, expected_etag=item.etag,
            idempotency_key='s42-org-policy-read-0001', requested_state='read',
            trace_id=case.trace, policy_version=case.policy)
        read = service.mark_read(**arguments)
        assert read.version == 2 and read.read_at is not None
        after_read = _org_snapshot(policy)
        assert service.mark_read(**arguments) == read
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
            notification_id=item.notification_id) == read
        assert _org_snapshot(policy) == after_read
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox '
                'WHERE tenant_id=%s GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [
                    ('notification.created', 4), ('notification.read', 1)]

        # 같은 실제 저장 경로로 V3를 만든 뒤에도 V2 source/알림/원감사를 치환하지 않는다.
        latest = _activate_org(SimpleNamespace(service=policy.service, context=policy.context,
            payload=policy.payload, current=stored), 's42-org-policy-consumer-v3-0001')
        assert latest.policy_version == latest.binding_version == 3 and latest.binding_id != stored.binding_id
        assert policy.repository.current(policy.context, 'organization') == latest
        replaced = _org_snapshot(policy)
        for table in ('notifications', 'notification_details', 'notification_audit_outbox'):
            assert replaced['origin_tenant'][table] == after_read['origin_tenant'][table]
        v2_sources = [row for row in replaced['origin_tenant']['notification_source_outbox']
            if row[0]['origin_event_id'] == audit['event_id']]
        assert v2_sources == after_read['origin_tenant']['notification_source_outbox']
        assert len(replaced['origin_tenant']['notification_source_outbox']) == 8
        assert replaced['foreign_tenant'] == after_read['foreign_tenant']
        assert _target(scoped_case, original, case.recipient)['target_id'] == stored.binding_id
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
            notification_id=item.notification_id) == read
        assert _org_snapshot(policy) == replaced

        # 원작성자의 이후 권한 철회는 원사건을 지우거나 다른 현재 수신자의 VIEW를 철회하지 않는다.
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute("UPDATE public.identity_auth_tenant_roles SET state='suspended',"
                'version=version+1,updated_at=now() WHERE tenant_id=%s AND user_id=%s '
                "AND state='active' RETURNING role", (case.tenant, case.manager)).fetchall() == [('organization_admin',)]
        before_get = _org_snapshot(policy)
        assert _target(scoped_case, original, case.recipient)['accessible'] is True
        assert service.get_notification(principal=principal, notification_id=item.notification_id,
            trace_id=case.trace, policy_version=case.policy) == read
        assert _org_snapshot(policy) == before_get

        # 현재 수신자의 해당 Workspace 권한 철회는 목록/단건/기존 읽음 receipt replay 모두 차단한다.
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute("UPDATE public.identity_auth_memberships SET state='suspended',"
                'version=version+1,updated_at=now() WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s '
                "AND state='active' RETURNING role", (case.tenant, policy.second, case.recipient)).fetchall() == [('viewer',)]
        revoked = _org_snapshot(policy)
        assert _target(scoped_case, original, case.recipient)['accessible'] is False
        assert _target(case, originals[case.workspace], case.recipient)['accessible'] is True
        assert _list(service, principal, scoped_case).items == ()
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
            notification_id=item.notification_id) is None
        with pytest.raises(NotificationError) as unavailable:
            service.get_notification(principal=principal, notification_id=item.notification_id,
                trace_id=case.trace, policy_version=case.policy)
        assert (unavailable.value.code, unavailable.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        with pytest.raises(NotificationError) as replay:
            service.mark_read(**arguments)
        assert (replay.value.code, replay.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        assert _org_snapshot(policy) == revoked
    finally:
        authorization_repository.close()


def test_org_policy_source_rpc_sql_failure_rolls_back_final_transaction_and_same_key_retries(org_policy_case, monkeypatch):
    """실제 RPC True 뒤 SQL22012를 주입한다. 비공개 source의 앱 SELECT나 성공 대역은 없다."""
    policy, phases = org_policy_case, []
    case = policy.case
    before = _org_snapshot(policy)
    real_transaction = policy.store._transaction

    class ObservedCursor:
        def __init__(self, cursor, raw):
            self.cursor, self.raw, self.source_rpc = cursor, raw, False

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def observe(self, query, parameters):
            if not (isinstance(query, str) and query.startswith('SELECT public.enqueue_egress_policy_notification(')
                and isinstance(parameters, (tuple, list))):
                return
            # 원감사 WS에서 정확한 신규 원사건만 읽고 이전 수신자/WS 문맥을 복원한다.
            with _account_notification_scope(self.raw, tenant_id=case.tenant, workspace_id=case.workspace,
                actor_id=case.manager) as reader:
                origins = reader.execute('SELECT event_id FROM public.audit_events WHERE tenant_id=%s '
                    'AND workspace_id=%s AND actor_id=%s AND trace_id=%s '
                    "AND action='egress_policy.activate' AND metadata->>'scope_type'='organization' "
                    "AND metadata->>'binding_version'='2'", (case.tenant, case.workspace,
                    case.manager, case.trace)).fetchall()
            self.source_rpc = len(origins) == 1 and origins[0][0] in parameters

        def execute(self, query, parameters=None):
            self.cursor.execute(query, parameters)
            self.observe(query, parameters)
            return self

        def fetchone(self):
            row = self.cursor.fetchone()
            if self.source_rpc:
                actual = tuple(row.values()) if isinstance(row, dict) else row
                assert actual is not None and len(actual) == 1 and type(actual[0]) is bool and actual[0] is True
                # True는 실제 RPC의 ROW_COUNT=1 계약이며, 원장은 정상 재시도 후 관리자 조회로 확인한다.
                phases.append('source_rpc_true')
                try:
                    self.raw.execute('SELECT 1/0')
                except psycopg.Error as error:
                    assert error.sqlstate == '22012'
                    phases.append('sql_22012')
                    raise
                pytest.fail('ORG_POLICY_SOURCE_SQL_FAILURE_NOT_INJECTED', pytrace=False)
            return row

    class ObservedConnection:
        def __init__(self, raw):
            self.raw = raw

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def cursor(self, *args, **kwargs):
            return ObservedCursor(self.raw.cursor(*args, **kwargs), self.raw)

        def execute(self, query, parameters=None):
            cursor = ObservedCursor(self.raw.execute(query, parameters), self.raw)
            cursor.observe(query, parameters)
            return cursor

    @contextmanager
    def observed_transaction(context):
        with real_transaction(context) as connection:
            yield ObservedConnection(connection)

    key = 's42-org-policy-rollback-0001'
    with monkeypatch.context() as patch:
        patch.setattr(policy.store, '_transaction', observed_transaction)
        with pytest.raises(CloudDatabaseError) as failed:
            _activate_org(policy, key)
    assert (failed.value.code, failed.value.retryable) == ('DATABASE_UNAVAILABLE', True)
    assert phases == ['source_rpc_true', 'sql_22012']
    assert _org_snapshot(policy) == before
    assert policy.repository.current(policy.context, 'organization') == policy.current
    stored = _activate_org(policy, key)
    _assert_org_change(policy, stored, key)
    after = _org_snapshot(policy)
    assert _activate_org(policy, key) == stored
    assert _org_snapshot(policy) == after


def test_org_policy_locator_crosses_hundred_candidates_keeps_personal_origin_and_restores_scope(org_policy_case):
    """실제 source 뒤 QA auth metadata만 바꾼다. 원감사/source 합성·삭제나 신규 권한은 없다."""
    from daon_user_api.notification_postgres import _time

    policy = org_policy_case
    case = policy.case
    key = 's42-org-policy-locator-0001'
    stored = _activate_org(policy, key)
    _assert_org_change(policy, stored, key)
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        audit = connection.execute('SELECT * FROM public.audit_events WHERE tenant_id=%s AND target_id=%s '
            "AND action='egress_policy.activate'", (case.tenant, stored.binding_id)).fetchone()
        assert audit is not None and audit['workspace_id'] == case.workspace
        # 이미 발행된 다른 조직 WS의 source를 사용한다. 원 WS를 개인형으로 바꿔 새 source를 만들지 않는다.
        assert connection.execute("UPDATE public.identity_auth_workspaces SET workspace_kind='personal',"
            'version=version+1,updated_at=now() WHERE tenant_id=%s AND workspace_id=%s '
            "AND workspace_kind='organization' RETURNING workspace_kind",
            (case.tenant, case.workspace)).fetchall() == [{'workspace_kind': 'personal'}]
        prefix = '000-s42-org-locator-' + uuid4().hex + '-'
        # 원감사보다 먼저 정렬되는 100개 실제 후보다. 정책/감사/source 행은 만들지 않는다.
        connection.execute('INSERT INTO public.workspaces(tenant_id,workspace_id,display_name) '
            "SELECT %s,%s||lpad(n::text,3,'0'),'S42 synthetic locator candidate' "
            'FROM generate_series(1,100) AS series(n)', (case.tenant, prefix))
        connection.execute('INSERT INTO public.identity_auth_workspaces '
            "SELECT %s||lpad(n::text,3,'0'),%s,'personal','cloud_sync',1000,1,1,now() "
            'FROM generate_series(1,100) AS series(n)', (prefix, case.tenant))
        assert connection.execute('SELECT count(*) AS preceding FROM public.identity_auth_workspaces '
            'WHERE tenant_id=%s AND workspace_id<%s', (case.tenant, case.workspace)).fetchone()['preceding'] >= 100
    source_id = 'egress-policy-event-' + hashlib.sha256(
        f'{audit["event_id"]}|{policy.second}|{case.recipient}'.encode()).hexdigest()[:32]
    before = _org_snapshot(policy)

    with psycopg.connect(case.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        with _account_notification_scope(connection, tenant_id=case.tenant, workspace_id=policy.second,
            actor_id=case.recipient, enabled=True) as reader:
            names = ('app.tenant_id', 'app.workspace_id', 'app.actor_id', 'app.capability',
                'app.notification_inbox_enabled')

            def scope_values():
                return tuple(reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0]
                    for name in names)

            expected_scope = scope_values()
            # private INVOKER는 public 경로의 내부 owner만 쓴다. 앱 직접 실행은 기존 권한으로 거부한다.
            with pytest.raises(psycopg.Error) as private:
                with connection.transaction():
                    reader.execute('SELECT public.current_egress_policy_notification_origin(%s)',
                        (audit['event_id'],)).fetchone()
            assert private.value.sqlstate == '42501'
            assert scope_values() == expected_scope
            row = reader.execute('SELECT public.current_egress_policy_notification_target(%s)', (source_id,)).fetchone()
            assert row is not None and type(row[0]) is dict
            target = dict(row[0])
            target['occurred_at'] = _time(target['occurred_at'])
            assert target == dict(accessible=True, title='외부 전송 정책 변경',
                summary='작업공간의 외부 전송 정책이 변경되었습니다.', deep_link='/notifications',
                source_event_id=source_id, origin_event_id=audit['event_id'], target_type='EgressPolicyBinding',
                target_id=stored.binding_id, kind='policy', severity='info', trace_id=case.trace,
                policy_version=case.policy, occurred_at=audit['occurred_at'], action='egress_policy.activate')
            assert scope_values() == expected_scope

            class RestoreLocatorDirectory(Exception):
                pass

            # 원감사는 불변으로 둔다. 자기 QA의 auth 디렉터리에서만 원 WS를 한시 숨겨 locator 미발견을 만든다.
            # 바깥 savepoint도 강제 rollback하므로 디렉터리 삭제는 commit하지 않는다.
            with pytest.raises(RestoreLocatorDirectory):
                with connection.transaction():
                    assert reader.execute('DELETE FROM public.identity_auth_workspaces '
                        'WHERE tenant_id=%s AND workspace_id=%s RETURNING workspace_id',
                        (case.tenant, case.workspace)).fetchall() == [(case.workspace,)]
                    with pytest.raises(psycopg.Error) as missing:
                        with connection.transaction():
                            reader.execute('SELECT public.current_egress_policy_notification_target(%s)',
                                (source_id,)).fetchone()
                    assert missing.value.sqlstate == '23514'
                    assert missing.value.diag.message_primary == 'NOTIFICATION_ORIGIN_INVALID'
                    assert scope_values() == expected_scope
                    raise RestoreLocatorDirectory()
            assert scope_values() == expected_scope
            assert reader.execute('SELECT public.current_egress_policy_notification_target(%s)',
                (source_id,)).fetchone()[0]['accessible'] is True
    # 성공/미발견/권한 오류 모두 GET 성격이다. 원업무·알림·source pending/ack가 바뀌면 안 된다.
    assert _org_snapshot(policy) == before
    assert before['origin_tenant']['notifications'] == before['origin_tenant']['notification_details'] == []
    assert all(row[0]['state'] == 'pending' and row[0]['delivered_at'] is None
        for row in before['origin_tenant']['notification_source_outbox'])
