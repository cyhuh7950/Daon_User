"""S42 실제 S31 Workspace 정책 저장→알림 source 시험. 실행·DDL은 main 소유다.

버전1은 기존 helper의 합성 초기 fixture이고 버전2는 실제 서비스로 저장한다.
조직 scope·소비·로그인/HTTP·외부 전송 또는 정식 환경 성공 증거는 아니다.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from daon_user_api.cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from daon_user_api.egress_policy import EgressPolicyContext, EgressPolicyPayload, EgressPolicyService
from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
from daon_user_api.notification_events import _account_notification_scope, _account_view_allowed
from test_notification_events import _studio_case_egress
from test_organization_notification_events import organization_notification_case


@contextmanager
def _policy_case(case):
    context = EgressPolicyContext(case.tenant, case.tenant, case.workspace, case.manager, case.trace, case.policy)
    payload = EgressPolicyPayload.deny_external()
    scope = SimpleNamespace(context=context, suffix=uuid4().hex)
    with psycopg.connect(case.admin, connect_timeout=3) as guard:
        for name, value in (('app.tenant_id', case.tenant), ('app.workspace_id', case.workspace),
            ('app.actor_id', case.manager), ('app.capability', 'egress_policy.write'),
            ('app.notification_inbox_enabled', 'false')):
            guard.execute('SELECT set_config(%s,%s,true)', (name, value))
        # 초기 정책만 준비한다. 신규 source나 원감사는 이 시험에서 직접 INSERT하지 않는다.
        _studio_case_egress(guard, scope, 'workspace', payload.as_dict(), 1)
    store = PostgresCloudStore(case.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    try:
        repository = PostgresEgressPolicyRepository(store)
        service = EgressPolicyService(repository)
        current = repository.current(context, 'workspace')
        assert (current.scope_type, current.workspace_id, current.policy_version, current.binding_version,
            current.active, current.current, current.payload) == ('workspace', case.workspace, 1, 1, True, True, payload)
        # 실제 identity/auth/Foundation을 가진 두 사용자이며 권한 판정 대역은 사용하지 않는다.
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
            for user in (case.manager, case.recipient):
                with _account_notification_scope(connection, tenant_id=case.tenant, workspace_id=case.workspace,
                    actor_id=user) as reader:
                    assert _account_view_allowed(reader, CloudAccessContext(case.tenant, case.workspace, user, 'view'))
        yield SimpleNamespace(store=store, repository=repository, service=service, context=context,
            payload=payload, current=current)
    finally:
        store.close()


def _activate(policy, key):
    return policy.service.create_and_activate(policy.context, scope_type='workspace', payload=policy.payload,
        expected_etag=policy.current.etag, idempotency_key=key)


def _snapshot(case):
    # 자기 합성 tenant의 정책·권한·알림 metadata만 비교한다. 자격증명 열은 조회하지 않는다.
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        result = {table: connection.execute(
            f'SELECT to_jsonb(x) FROM public.{table} x WHERE tenant_id=%s ORDER BY to_jsonb(x)::text',
            (case.tenant,)).fetchall() for table in (
                'egress_policy_versions', 'egress_policy_bindings', 'idempotency_records', 'audit_events',
                'notifications', 'notification_details', 'notification_source_outbox', 'notification_audit_outbox',
                'identity_memberships', 'identity_auth_memberships', 'identity_auth_workspaces',
                'identity_org_tenant_memberships', 'identity_org_operation_outbox')}
        result['identity_users'] = connection.execute('SELECT user_id,state,password_change_required,email_verified_at '
            'FROM public.identity_users WHERE user_id IN (%s,%s) ORDER BY user_id', (case.manager, case.recipient)).fetchall()
        result['user_accounts'] = connection.execute('SELECT tenant_id,user_id,status FROM public.user_accounts '
            'WHERE tenant_id=%s ORDER BY user_id', (case.tenant,)).fetchall()
        return result


def _assert_stored_change_and_sources(case, policy, stored, key):
    assert (stored.tenant_id, stored.organization_id, stored.workspace_id, stored.scope_type,
        stored.policy_version, stored.policy_state, stored.binding_version, stored.active, stored.current, stored.payload) == (
            case.tenant, case.tenant, case.workspace, 'workspace', 2, 'active', 2, True, True, policy.payload)
    assert stored.policy_version_id != policy.current.policy_version_id and stored.binding_id != policy.current.binding_id
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        versions = connection.execute('SELECT policy_version_id,policy_version,state,canonical_json,canonical_text,'
            'digest_sha256,created_by,trace_id FROM public.egress_policy_versions '
            'WHERE tenant_id=%s AND workspace_id=%s ORDER BY policy_version', (case.tenant, case.workspace)).fetchall()
        assert versions == [dict(policy_version_id=version_id, policy_version=version, state='active',
            canonical_json=policy.payload.as_dict(), canonical_text=policy.payload.canonical_text,
            digest_sha256=policy.payload.digest_sha256, created_by=case.manager, trace_id=case.trace)
            for version_id, version in ((policy.current.policy_version_id, 1), (stored.policy_version_id, 2))]
        bindings = connection.execute('SELECT binding_id,policy_version_id,binding_version,active,current,created_by,trace_id '
            'FROM public.egress_policy_bindings WHERE tenant_id=%s AND workspace_id=%s ORDER BY binding_version',
            (case.tenant, case.workspace)).fetchall()
        assert bindings == [dict(binding_id=binding_id, policy_version_id=version_id, binding_version=version,
            active=active, current=active, created_by=case.manager, trace_id=case.trace)
            for binding_id, version_id, version, active in (
                (policy.current.binding_id, policy.current.policy_version_id, 1, False),
                (stored.binding_id, stored.policy_version_id, 2, True))]
        receipts = connection.execute('SELECT workspace_id,actor_id,operation,idempotency_key,result,status '
            'FROM public.idempotency_records WHERE tenant_id=%s', (case.tenant,)).fetchall()
        assert receipts == [dict(workspace_id=case.workspace, actor_id=case.manager,
            operation='egress_policy.workspace.activate', idempotency_key=key,
            result={'policy_version_id': stored.policy_version_id, 'binding_id': stored.binding_id}, status='completed')]
        audits = connection.execute('SELECT event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,'
            'trace_id,policy_version,before_value,after_value,metadata,occurred_at FROM public.audit_events '
            'WHERE tenant_id=%s ORDER BY event_id', (case.tenant,)).fetchall()
        assert len(audits) == 1
        audit = audits[0]
        assert audit['event_id'].startswith('audit-egress-') and audit['occurred_at'].tzinfo is not None
        expected_audit = dict(tenant_id=case.tenant, workspace_id=case.workspace, actor_id=case.manager,
            action='egress_policy.activate', target_type='EgressPolicyBinding', target_id=stored.binding_id,
            outcome='succeeded', trace_id=case.trace, policy_version=case.policy, before_value=None, after_value=None,
            metadata={'scope_type': 'workspace', 'policy_version_id': stored.policy_version_id, 'binding_version': 2})
        assert {name: audit[name] for name in expected_audit} == expected_audit

        # 원 event ID는 실제 저장된 감사에서 읽는다. 아직 없는 helper/module을 선행 요구하지 않는다.
        origin = audit['event_id']
        recipients = sorted((case.manager, case.recipient))
        sources = connection.execute('SELECT tenant_id,workspace_id,source_event_id,recipient_id,source_store,origin_event_id,'
            'action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at,state,version,delivered_at '
            'FROM public.notification_source_outbox WHERE tenant_id=%s ORDER BY recipient_id', (case.tenant,)).fetchall()
        expected_sources = [dict(tenant_id=case.tenant, workspace_id=case.workspace,
            source_event_id='egress-policy-event-' + hashlib.sha256(f'{origin}|{case.workspace}|{user}'.encode()).hexdigest()[:32],
            recipient_id=user, source_store='authorization', origin_event_id=origin, action='egress_policy.activate',
            target_type='EgressPolicyBinding', target_id=stored.binding_id, kind='policy', severity='info', notebook_id=None,
            trace_id=case.trace, policy_version=case.policy, occurred_at=audit['occurred_at'], state='pending', version=1,
            delivered_at=None) for user in recipients]
        assert sources == expected_sources, 'EGRESS_POLICY_CHANGED_SOURCE_INTENT_MISSING'
        atomic = connection.execute('SELECT s.recipient_id,p.xmin=b.xmin AND b.xmin=r.xmin AND r.xmin=a.xmin '
            'AND a.xmin=s.xmin AS same_transaction FROM public.egress_policy_versions p '
            'JOIN public.egress_policy_bindings b ON b.tenant_id=p.tenant_id AND b.policy_version_id=p.policy_version_id '
            'JOIN public.idempotency_records r ON r.tenant_id=b.tenant_id AND r.workspace_id=b.workspace_id '
            "AND r.result->>'binding_id'=b.binding_id AND r.operation='egress_policy.workspace.activate' "
            'JOIN public.audit_events a ON a.tenant_id=b.tenant_id AND a.workspace_id=b.workspace_id '
            "AND a.target_id=b.binding_id AND a.action='egress_policy.activate' "
            'JOIN public.notification_source_outbox s ON s.tenant_id=a.tenant_id AND s.workspace_id=a.workspace_id '
            'AND s.origin_event_id=a.event_id WHERE p.tenant_id=%s AND p.policy_version_id=%s '
            'AND b.binding_id=%s AND r.actor_id=%s AND r.idempotency_key=%s ORDER BY s.recipient_id',
            (case.tenant, stored.policy_version_id, stored.binding_id, case.manager, key)).fetchall()
        assert atomic == [dict(recipient_id=user, same_transaction=True) for user in recipients]
    return origin


def test_policy_source_sql_denies_wrong_kind_tenant_role_even_with_active_workspace_membership(
    organization_notification_case, monkeypatch,
):
    """SQL 판정이 기존 tenant role 우선순위 대신 membership으로 fallback하는 결함을 검출한다."""
    case = organization_notification_case
    with _policy_case(case) as policy:
        before = _snapshot(case)
        real_transaction = policy.store._transaction
        observed = {}

        class RollbackNegativeCase(Exception):
            pass

        @contextmanager
        def inspect_new_policy_before_commit(context):
            with real_transaction(context) as connection:
                yield connection
                origin = connection.execute("SELECT event_id FROM public.audit_events WHERE tenant_id=%s "
                    "AND workspace_id=%s AND action='egress_policy.activate'", (case.tenant, case.workspace)).fetchone()[0]
                connection.execute("INSERT INTO public.identity_auth_tenant_roles "
                    "VALUES(%s,%s,'personal_owner','active',1,now())", (case.tenant, case.recipient))
                with _account_notification_scope(connection, tenant_id=case.tenant,
                    workspace_id=case.workspace, actor_id=case.recipient, enabled=True) as reader:
                    observed['python_view'] = _account_view_allowed(reader,
                        CloudAccessContext(case.tenant, case.workspace, case.recipient, 'view'))
                    try:
                        with connection.transaction():
                            observed['result'] = reader.execute('SELECT public.enqueue_egress_policy_notification(%s,%s)',
                                (origin, case.manager)).fetchone()
                        observed['sqlstate'] = None
                    except psycopg.Error as error:
                        observed['sqlstate'] = error.sqlstate
                # 잘못된 허용도 정책/역할/source와 함께 확정하지 않는다.
                raise RollbackNegativeCase()

        with monkeypatch.context() as patch:
            patch.setattr(policy.store, '_notification_inbox_enabled', False)
            patch.setattr(policy.store, '_transaction', inspect_new_policy_before_commit)
            with pytest.raises(RollbackNegativeCase):
                _activate(policy, 's42-egress-wrong-tenant-role')
        assert observed['python_view'] is False
        assert observed['sqlstate'] == '42501', 'SQL_VIEW_MUST_NOT_FALL_BACK_FROM_WRONG_KIND_TENANT_ROLE'
        assert _snapshot(case) == before
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT count(*) FROM public.identity_auth_tenant_roles '
                'WHERE tenant_id=%s AND user_id=%s', (case.tenant, case.recipient)).fetchone() == (0,)


def test_workspace_egress_policy_change_has_atomic_current_view_sources_and_replay_adds_none(organization_notification_case):
    case = organization_notification_case
    with _policy_case(case) as policy:
        before = _snapshot(case)
        assert before['notification_source_outbox'] == before['audit_events'] == before['idempotency_records'] == []
        key = 's42-egress-policy-source-0001'
        stored = _activate(policy, key)
        _assert_stored_change_and_sources(case, policy, stored, key)
        after = _snapshot(case)
        assert len(after['notification_source_outbox']) == 2
        assert len(after['audit_events']) == len(after['idempotency_records']) == 1
        assert _activate(policy, key) == stored
        assert policy.repository.current(policy.context, 'workspace') == stored
        assert _snapshot(case) == after
        for table in ('identity_users', 'identity_memberships', 'identity_auth_memberships', 'identity_auth_workspaces',
            'identity_org_tenant_memberships', 'identity_org_operation_outbox', 'user_accounts',
            'notifications', 'notification_details', 'notification_audit_outbox'):
            assert after[table] == before[table]


def test_workspace_egress_source_rpc_then_sql_failure_rolls_back_and_same_key_retries(
    organization_notification_case, monkeypatch,
):
    case = organization_notification_case
    with _policy_case(case) as policy:
        before = _snapshot(case)
        key = 's42-egress-policy-rollback-0001'
        real_transaction = policy.store._transaction
        phases = []

        class ObservedCursor:
            def __init__(self, cursor, raw):
                self.cursor, self.raw, self.source_rpc = cursor, raw, False

            def __getattr__(self, name):
                return getattr(self.cursor, name)

            def _observe(self, query, parameters):
                if not (isinstance(query, str) and query.lstrip().startswith('SELECT public.')
                    and isinstance(parameters, (tuple, list))):
                    return
                # 앱에 허용된 원감사만 같은 Tx에서 읽는다. 비공개 source SELECT는 하지 않는다.
                origins = self.raw.execute('SELECT event_id FROM public.audit_events WHERE tenant_id=%s '
                    'AND workspace_id=%s AND actor_id=%s AND trace_id=%s '
                    "AND action='egress_policy.activate' AND target_type='EgressPolicyBinding' AND outcome='succeeded' "
                    "AND metadata->>'scope_type'='workspace' AND metadata->>'binding_version'='2'",
                    (case.tenant, case.workspace, case.manager, case.trace)).fetchall()
                self.source_rpc = len(origins) == 1 and origins[0][0] in parameters

            def execute(self, query, parameters=None):
                self.cursor.execute(query, parameters)
                self._observe(query, parameters)
                return self

            def fetchone(self):
                row = self.cursor.fetchone()
                if self.source_rpc:
                    actual = tuple(row.values()) if isinstance(row, dict) else row
                    assert actual is not None and len(actual) == 1 and type(actual[0]) is bool and actual[0] is True
                    # True는 제한 함수 ROW_COUNT=1 계약이다. 실제 원장은 정상 재시도 후 관리자 조회로 검증한다.
                    phases.append('source_rpc_true')
                    try:
                        self.raw.execute('SELECT 1/0')
                    except psycopg.Error as error:
                        assert error.sqlstate == '22012'
                        phases.append('sql_22012')
                        raise
                    pytest.fail('EGRESS_SOURCE_SQL_FAILURE_NOT_INJECTED', pytrace=False)
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
                cursor._observe(query, parameters)
                return cursor

        @contextmanager
        def observed_transaction(context):
            with real_transaction(context) as connection:
                yield ObservedConnection(connection)

        with monkeypatch.context() as patch:
            patch.setattr(policy.store, '_transaction', observed_transaction)
            with pytest.raises(CloudDatabaseError) as failed:
                _activate(policy, key)
        assert (failed.value.code, failed.value.retryable) == ('DATABASE_UNAVAILABLE', True)
        assert phases == ['source_rpc_true', 'sql_22012']
        assert _snapshot(case) == before
        assert policy.repository.current(policy.context, 'workspace') == policy.current
        stored = _activate(policy, key)
        _assert_stored_change_and_sources(case, policy, stored, key)
        after = _snapshot(case)
        assert _activate(policy, key) == stored
        assert _snapshot(case) == after
