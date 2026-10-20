"""S42 C9 changed→source 첫 저장 경계. 실행/정확 SQL/QA 수명은 main 소유다.

실제 기존 조직 가입 fixture와 C9 서비스를 사용한다. source/원감사 직접 INSERT,
부모 owner/RLS 강화·소비·HTTP·정식 환경 성공 주장은 포함하지 않는다.
"""
from __future__ import annotations

import hashlib
import inspect
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone

import psycopg
import pytest

from daon_user_api.admin_membership_roles import AdminMembershipRoleService
from daon_user_api.authorization import AuthorizationError, Role
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
from test_organization_notification_events import organization_notification_case


class _NoDatabaseRepository:
    def _connect(self, *args, **kwargs):
        pytest.fail('C9_FLAG_CONSTRUCTOR_MUST_NOT_CONNECT', pytrace=False)

    def transaction(self, *args, **kwargs):
        pytest.fail('C9_FLAG_CONSTRUCTOR_MUST_NOT_START_TRANSACTION', pytrace=False)


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_c9_notification_flag_rejects_non_boolean_without_database(value):
    with pytest.raises(ValueError, match='^NOTIFICATION_INBOX_FLAG_INVALID$'):
        AdminMembershipRoleService(repository=_NoDatabaseRepository(),
            system_admin_user_ids=frozenset({'s42-c9-unit-admin'}), clock=lambda: datetime.now(timezone.utc),
            notification_inbox_enabled=value)


@pytest.mark.parametrize('arguments,expected', [({}, False), ({'notification_inbox_enabled': True}, True)])
def test_c9_notification_flag_defaults_off_and_preserves_explicit_true_without_database(arguments, expected):
    repository = _NoDatabaseRepository()
    service = AdminMembershipRoleService(repository=repository,
        system_admin_user_ids=frozenset({'s42-c9-unit-admin'}), clock=lambda: datetime.now(timezone.utc), **arguments)
    assert service._notification_inbox_enabled is expected
    assert service._repository is repository


def _service(case):
    repository = PostgresAuthorizationRepository(case.app)
    arguments = {'notification_inbox_enabled': True} if (
        'notification_inbox_enabled' in inspect.signature(AdminMembershipRoleService).parameters) else {}
    service = AdminMembershipRoleService(repository=repository, system_admin_user_ids=frozenset({case.manager}),
        clock=lambda: datetime.now(timezone.utc), **arguments)
    # 인증 principal 포트 입력이다. 실제 로그인/HTTP를 실행한 증거가 아니다.
    actor = IdentityPrincipal(case.manager, 's42-c9-fixture-session', 's42-c9-fixture-device', case.tenant)
    return service, repository, actor


def _change(service, actor, case, *, key, role=Role.EDITOR, version=1):
    return service.change_existing_workspace_role(actor=actor, tenant_id=case.tenant, workspace_id=case.workspace,
        user_id=case.recipient, role=role, expected_version=version, idempotency_key=key, reason='ROLE_DUTY_CHANGE')


def _ledger(case):
    # 합성 tenant의 역할·operation·알림 metadata만 조회한다. credential/원문을 읽지 않는다.
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        return {table: connection.execute(
            f'SELECT to_jsonb(x) FROM public.{table} x WHERE tenant_id=%s ORDER BY to_jsonb(x)::text',
            (case.tenant,)).fetchall() for table in (
                'identity_auth_memberships', 'identity_auth_workspaces', 'identity_auth_admin_role_operations',
                'identity_memberships', 'identity_org_tenant_memberships', 'identity_org_operation_outbox',
                'notifications', 'notification_details', 'notification_source_outbox', 'notification_audit_outbox')}


def _assert_initial_viewer(case):
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT role,state,version FROM public.identity_auth_memberships '
            'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s',
            (case.tenant, case.workspace, case.recipient)).fetchall() == [('viewer', 'active', 1)]
        assert connection.execute('SELECT acl_version FROM public.identity_auth_workspaces '
            'WHERE tenant_id=%s AND workspace_id=%s', (case.tenant, case.workspace)).fetchall() == [(2,)]
        assert connection.execute('SELECT role,state,version,join_workspace_id FROM public.identity_org_tenant_memberships '
            'WHERE tenant_id=%s AND user_id=%s', (case.tenant, case.recipient)).fetchall() == [
                ('member', 'active', 1, case.workspace)]


def _assert_changed_source(case, key):
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        operations = connection.execute('SELECT event_id,actor_id,tenant_id,workspace_id,user_id,old_role,new_role,'
            'old_version,new_version,acl_version,reason,outcome,created_at,delivered_at '
            'FROM public.identity_auth_admin_role_operations WHERE actor_id=%s AND idempotency_key=%s',
            (case.manager, key)).fetchall()
        assert len(operations) == 1
        operation = operations[0]
        event_id = operation[0]
        assert operation[1:12] == (case.manager, case.tenant, case.workspace, case.recipient,
            'viewer', 'editor', 1, 2, 3, 'ROLE_DUTY_CHANGE', 'changed')
        assert operation[12].tzinfo is not None and operation[13] is None
        source_id = 'c9-role-event-' + hashlib.sha256(
            f'{event_id}|{case.workspace}|{case.recipient}'.encode()).hexdigest()[:32]
        sources = connection.execute('SELECT tenant_id,workspace_id,source_event_id,recipient_id,source_store,'
            'origin_event_id,action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,'
            'occurred_at,state,delivered_at,version FROM public.notification_source_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s AND recipient_id=%s',
            (case.tenant, case.workspace, case.recipient)).fetchall()
        assert sources == [(case.tenant, case.workspace, source_id, case.recipient, 'authorization', event_id,
            'authorization.membership.changed', 'membership', case.recipient, 'membership', 'info', None,
            'not-recorded', 'not-recorded', operation[12], 'pending', None, 1)], 'C9_CHANGED_SOURCE_INTENT_MISSING'
        assert connection.execute('SELECT m.role,m.state,m.version,w.acl_version '
            'FROM public.identity_auth_memberships m JOIN public.identity_auth_workspaces w '
            'ON w.tenant_id=m.tenant_id AND w.workspace_id=m.workspace_id '
            'WHERE m.tenant_id=%s AND m.workspace_id=%s AND m.user_id=%s',
            (case.tenant, case.workspace, case.recipient)).fetchall() == [('editor', 'active', 2, 3)]
        assert connection.execute('SELECT m.xmin=w.xmin AND w.xmin=o.xmin AND o.xmin=s.xmin '
            'FROM public.identity_auth_admin_role_operations o JOIN public.identity_auth_memberships m '
            'ON m.tenant_id=o.tenant_id AND m.workspace_id=o.workspace_id AND m.user_id=o.user_id '
            'JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=m.workspace_id '
            'JOIN public.notification_source_outbox s ON s.tenant_id=o.tenant_id AND s.workspace_id=o.workspace_id '
            'AND s.recipient_id=o.user_id AND s.origin_event_id=o.event_id '
            'WHERE o.actor_id=%s AND o.idempotency_key=%s AND s.source_event_id=%s',
            (case.manager, key, source_id)).fetchall() == [(True,)]
    return source_id


def test_c9_changed_role_has_one_atomic_source_replay_and_unchanged_add_none(organization_notification_case):
    case = organization_notification_case
    _assert_initial_viewer(case)
    before = _ledger(case)
    service, repository, actor = _service(case)
    key = 's42-c9-role-source-0001'
    try:
        changed = _change(service, actor, case, key=key)
        assert (changed.tenant_id, changed.workspace_id, changed.user_id, changed.role, changed.state,
            changed.version, changed.acl_version, changed.replayed) == (
                case.tenant, case.workspace, case.recipient, Role.EDITOR, 'active', 2, 3, False)
        # flag 인자가 미구현이어도 실제 원업무까지 진행해 source 누락을 RED로 드러낸다.
        _assert_changed_source(case, key)
        after = _ledger(case)
        assert len(after['notification_source_outbox']) == len(before['notification_source_outbox']) + 1
        assert _change(service, actor, case, key=key) == replace(changed, replayed=True)
        assert _ledger(case) == after

        unchanged_key = 's42-c9-role-unchanged-0001'
        unchanged = _change(service, actor, case, key=unchanged_key, version=2)
        assert unchanged == replace(changed, replayed=False)
        after_unchanged = _ledger(case)
        for table in after.keys() - {'identity_auth_admin_role_operations'}:
            assert after_unchanged[table] == after[table]
        assert len(after_unchanged['identity_auth_admin_role_operations']) == len(after['identity_auth_admin_role_operations']) + 1
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT old_role,new_role,old_version,new_version,acl_version,outcome,delivered_at '
                'FROM public.identity_auth_admin_role_operations WHERE actor_id=%s AND idempotency_key=%s',
                (case.manager, unchanged_key)).fetchall() == [('editor', 'editor', 2, 2, 3, 'unchanged', None)]
        assert after['notifications'] == after['notification_details'] == after['notification_audit_outbox'] == []
        for table in ('identity_memberships', 'identity_org_tenant_memberships', 'identity_org_operation_outbox'):
            assert after[table] == before[table]
    finally:
        repository.close()


def test_c9_actual_source_insert_then_sql_failure_rolls_back_and_same_key_retries(organization_notification_case, monkeypatch):
    case = organization_notification_case
    _assert_initial_viewer(case)
    before = _ledger(case)
    service, repository, actor = _service(case)
    key = 's42-c9-source-rollback-0001'
    # 기존 C9 operation의 생성식이다. 미래 알림 helper/함수 이름은 가정하지 않는다.
    event_id = 'auth-admin-role-' + hashlib.sha256(f'{case.manager}|{key}'.encode()).hexdigest()
    real_transaction = repository.transaction
    phases = []

    class ObservedCursor:
        def __init__(self, cursor, raw):
            self.cursor, self.raw, self.source_rpc = cursor, raw, False

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def _observe(self, query, parameters):
            self.source_rpc = (isinstance(query, str) and query.lstrip().startswith('SELECT public.')
                and isinstance(parameters, (tuple, list)) and event_id in parameters)

        def execute(self, query, parameters=None):
            self.cursor.execute(query, parameters)
            self._observe(query, parameters)
            return self

        def fetchone(self):
            row = self.cursor.fetchone()
            if self.source_rpc:
                actual = tuple(row.values()) if isinstance(row, dict) else row
                assert actual == (True,), 'ACTUAL_C9_SOURCE_INSERT_ONE_REQUIRED'
                phases.append('actual_source_insert_one')
                try:
                    self.raw.execute('SELECT 1/0')
                except psycopg.Error as error:
                    assert error.sqlstate == '22012'
                    phases.append('sql_22012')
                    raise
                pytest.fail('C9_SOURCE_SQL_FAILURE_NOT_INJECTED', pytrace=False)
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
    def observed_transaction():
        with real_transaction() as connection:
            raw = connection._conn
            connection._conn = ObservedConnection(raw)
            try:
                yield connection
            finally:
                connection._conn = raw

    try:
        with monkeypatch.context() as patch:
            patch.setattr(repository, 'transaction', observed_transaction)
            with pytest.raises(AuthorizationError) as failed:
                _change(service, actor, case, key=key)
        assert (failed.value.code, failed.value.http_status) == ('PERSISTENCE_UNAVAILABLE', 503)
        assert phases == ['actual_source_insert_one', 'sql_22012']
        assert _ledger(case) == before
        retried = _change(service, actor, case, key=key)
        assert retried.role is Role.EDITOR and retried.version == 2 and retried.acl_version == 3 and retried.replayed is False
        _assert_changed_source(case, key)
        after = _ledger(case)
        assert _change(service, actor, case, key=key) == replace(retried, replayed=True)
        assert _ledger(case) == after
    finally:
        repository.close()
