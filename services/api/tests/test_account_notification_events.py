"""S42 계정 사건의 실제 저장 시험. DB/DDL 수명과 실행은 main이 관리한다.

DAON_NOTIFICATION_TEST_DSN의 기존 소유 QA0069에 새 합성 scope만 만든다.
실제 로그인/추가 인증/계정 변경을 사용하며 Provider/Object/외부 I/O는 없다.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import secrets
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from daon_user_api.account_settings import AccountSettingsService
from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import DevicePlatform, IdentityService, PASSWORD_HASHER
from daon_user_api.identity_postgres import PostgresIdentityRepository


class _PrivateValue(str):
    def __repr__(self):
        return '<private account fixture value>'


class _AccountCase(SimpleNamespace):
    def __repr__(self):
        return '<S42 owned account fixture: private values redacted>'


@pytest.mark.parametrize('invalid', [None, 0, 1, 'true', 'false', [], {}])
def test_account_notification_flag_rejects_non_boolean(invalid):
    with pytest.raises(ValueError, match='^NOTIFICATION_INBOX_FLAG_INVALID$'):
        AccountSettingsService(SimpleNamespace(_repository=object()), notification_inbox_enabled=invalid)


@pytest.fixture
def account_notification_case():
    configured = os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not configured:
        pytest.skip('DAON_NOTIFICATION_TEST_DSN 미제공: 실제 PG 미실행')
    admin = _PrivateValue(configured)
    expected_database = 'daon_user_s42_qa_20261017_l2'
    if conninfo_to_dict(admin).get('dbname') != expected_database:
        pytest.fail('S42_ACCOUNT_OWNED_QA_DATABASE_REQUIRED', pytrace=False)
    with psycopg.connect(admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_database(),(SELECT version_num FROM public.alembic_version)').fetchone() == (
            expected_database, '0069')
        assert connection.execute('SELECT shobj_description(oid,\'pg_database\') FROM pg_database WHERE datname=current_database()').fetchone() == (
            'S42 L2a disposable QA 2026-10-17 main owned; remove after tests; no production data',)
    suffix = uuid4().hex
    actor = 's42-account-' + suffix
    workspace = 's42-account-w-' + suffix
    trace = 's42-account-trace-' + suffix
    policy = 'runtime-policy-v1'
    app = _PrivateValue(make_conninfo(admin, options='-c role=daon_app'))
    repository = PostgresIdentityRepository(app)
    audit = AuditEventStore()
    clock = lambda: datetime.now(timezone.utc)
    password = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    try:
        with repository.transaction() as connection:
            repository._ensure_tenant(connection, actor)
            connection.execute('INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,'
                'password_change_required,state) VALUES(?,?,?,?,?,?,?,?,?)',
                (actor, 'local', actor, actor, actor+'@example.test', PASSWORD_HASHER.hash(password), clock().isoformat(), False, 'active'))
            connection.execute('INSERT INTO memberships(tenant_id,user_id,role) VALUES(?,?,?)', (actor, actor, 'personal_owner'))
            repository._seed_minimum_step_up_actions(connection, actor)
        with psycopg.connect(admin, connect_timeout=3) as connection:
            connection.execute("INSERT INTO public.tenants(tenant_id,display_name) VALUES(%s,'S42 synthetic account tenant')", (actor,))
            connection.execute("INSERT INTO public.workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,'S42 synthetic account workspace')", (actor, workspace))
            connection.execute('INSERT INTO public.user_accounts(tenant_id,user_id) VALUES(%s,%s)', (actor, actor))
            connection.execute("INSERT INTO public.identity_auth_workspaces VALUES(%s,%s,'personal','cloud_sync',1000,1,1,now())", (workspace, actor))
            connection.execute("INSERT INTO public.identity_auth_tenant_roles VALUES(%s,%s,'personal_owner','active',1,now())", (actor, actor))
            connection.execute('INSERT INTO public.identity_auth_tenant_policies VALUES(%s,1,now())', (actor,))
            connection.execute('INSERT INTO public.identity_auth_workspace_policies VALUES(%s,%s,1,now())', (actor, workspace))
        identity = IdentityService(repository=repository, audit_store=audit, oidc_policies=(), clock=clock)
        credentials = identity.local_login(login_id=actor, password=password, platform=DevicePlatform.WEB,
            trace_id=trace, policy_version=policy)
        credentials = replace(credentials, access_token=_PrivateValue(credentials.access_token))
        supported = 'notification_inbox_enabled' in inspect.signature(AccountSettingsService).parameters
        service = AccountSettingsService(identity, **({'notification_inbox_enabled': True} if supported else {}))
        yield _AccountCase(admin=admin, app=app, actor=actor, tenant=actor, workspace=workspace, trace=trace,
            policy=policy, repository=repository, audit=audit, identity=identity, service=service,
            password=password, credentials=credentials, flag_supported=supported)
    finally:
        repository.close()


def _account_operation(case, operation):
    credentials = case.credentials
    arguments = dict(access_token=credentials.access_token, trace_id=case.trace,
        policy_version=case.policy, idempotency_key='s42-account-' + operation.replace('.', '-'))
    if operation == 'device.trust':
        arguments['device_id'] = credentials.device_id
        return case.service.trust_device, arguments, credentials.device_id
    other = case.identity.local_login(login_id=case.actor, password=case.password, platform=DevicePlatform.WEB,
        trace_id=case.trace, policy_version=case.policy)
    target = other.device_id if operation == 'device.revoke' else other.session_id
    authorization = case.identity.issue_step_up_after_reauthentication(access_token=credentials.access_token,
        password=case.password, action_group='device_session_or_sync_key_revoke', target_id=target,
        trace_id=case.trace, policy_version=case.policy)
    arguments['step_up_authorization'] = _PrivateValue(authorization.authorization)
    arguments['device_id' if operation == 'device.revoke' else 'session_id'] = target
    method = case.service.revoke_device if operation == 'device.revoke' else case.service.revoke_session
    return method, arguments, target


@pytest.mark.parametrize('operation,action,target_type,kind', [
    ('device.trust', 'identity.device.trusted', 'device', 'device'),
    ('device.revoke', 'identity.device.revoked', 'device', 'device'),
    ('session.revoke', 'identity.session.revoked', 'session', 'authentication'),
])
def test_account_operation_commits_one_primary_source_and_replay_is_read_only(
    account_notification_case, monkeypatch, operation, action, target_type, kind,
):
    """실제 계정 성공은 주사건 1개만 같은 Tx에 결속한다. 부수 감사와 replay는 새 알림0이다."""
    case = account_notification_case
    mutate, arguments, target = _account_operation(case, operation)
    # 이미 발급한 추가 인증 이후의 중앙 sink만 실패시킨다. 실제 원업무/수집 감사/원장 저장은 대체하지 않는다.
    def unavailable_sink(_draft):
        raise OSError('S42 synthetic central audit unavailable')
    monkeypatch.setattr(case.audit, 'append', unavailable_sink)
    first = mutate(**arguments)
    assert first['replayed'] is False
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        requests = connection.execute('SELECT event_id,audit_events_json,target_id,trace_id,policy_version,created_at,delivered_at '
            'FROM public.identity_account_requests WHERE tenant_id=%s AND actor_id=%s AND operation=%s',
            (case.tenant, case.actor, operation)).fetchall()
        assert len(requests) == 1
        event_id, event_json, recorded_target, trace, policy, occurred_at, delivered_at = requests[0]
        assert (recorded_target, trace, policy, delivered_at) == (target, case.trace, case.policy, None)
        collected = json.loads(event_json)
        primary = [(index, event) for index, event in enumerate(collected)
            if (event['action'], event['target_type'], event['target_id']) == (action, target_type, target)]
        assert len(primary) == 1
        origin = event_id + '-' + str(primary[0][0])
        source_id = 'identity-event-' + hashlib.sha256((origin+'|'+case.workspace).encode()).hexdigest()[:32]
        sources = connection.execute('SELECT source_event_id,origin_event_id,source_store,action,target_type,target_id,'
            'recipient_id,kind,notebook_id,trace_id,policy_version,occurred_at,state,version,delivered_at '
            'FROM public.notification_source_outbox WHERE tenant_id=%s AND workspace_id=%s AND recipient_id=%s',
            (case.tenant, case.workspace, case.actor)).fetchall()
        assert sources == [(source_id, origin, 'identity', action, target_type, target, case.actor, kind, None,
            case.trace, case.policy, occurred_at, 'pending', 1, None)], 'ACCOUNT_PRIMARY_SOURCE_INTENT_MISSING'
        assert case.flag_supported, 'ACCOUNT_NOTIFICATION_FLAG_NOT_SUPPORTED'
        table, column = ('identity_devices', 'device_id') if target_type == 'device' else ('identity_sessions', 'session_id')
        target_row = connection.execute(f'SELECT state FROM public.{table} WHERE tenant_id=%s AND user_id=%s AND {column}=%s',
            (case.tenant, case.actor, target)).fetchone()
        assert target_row == (('trusted',) if operation == 'device.trust' else ('revoked',))
        atomic = connection.execute(f'SELECT r.xmin=s.xmin AND s.xmin=t.xmin FROM public.identity_account_requests r '
            'JOIN public.notification_source_outbox s ON s.tenant_id=r.tenant_id AND s.recipient_id=r.actor_id '
            f'JOIN public.{table} t ON t.tenant_id=r.tenant_id AND t.user_id=r.actor_id AND t.{column}=r.target_id '
            'WHERE r.event_id=%s AND s.source_event_id=%s AND s.workspace_id=%s',
            (event_id, source_id, case.workspace)).fetchall()
        assert atomic == [(True,)], 'ACCOUNT_MUTATION_REQUEST_SOURCE_NOT_ATOMIC'
        before = connection.execute('SELECT to_jsonb(r) FROM public.identity_account_requests r WHERE event_id=%s', (event_id,)).fetchone()
        before_source = connection.execute('SELECT to_jsonb(s) FROM public.notification_source_outbox s WHERE tenant_id=%s AND source_event_id=%s',
            (case.tenant, source_id)).fetchall()
        assert connection.execute('SELECT count(*) FROM public.notifications WHERE tenant_id=%s', (case.tenant,)).fetchone() == (0,)
    assert mutate(**arguments)['replayed'] is True
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT to_jsonb(r) FROM public.identity_account_requests r WHERE event_id=%s', (event_id,)).fetchone() == before
        assert connection.execute('SELECT to_jsonb(s) FROM public.notification_source_outbox s WHERE tenant_id=%s AND source_event_id=%s',
            (case.tenant, source_id)).fetchall() == before_source
