"""S42 계정 원업무/source 실패 경계의 실제 PG 회귀. 성공을 합성하지 않는다."""
from __future__ import annotations

import psycopg
import pytest

from daon_user_api.account_settings import AccountSettingsService
from daon_user_api.identity import IdentityError
from test_account_notification_events import account_notification_case, _account_operation


def _snapshot(case):
    tables = ('identity_devices', 'identity_sessions', 'identity_account_requests',
        'identity_step_up_authorizations', 'identity_step_up_consumptions',
        'notifications', 'notification_details', 'notification_source_outbox', 'notification_audit_outbox')
    with psycopg.connect(case.admin) as connection:
        return {table: connection.execute(f'SELECT to_jsonb(r) FROM public.{table} r '
            'WHERE tenant_id=%s ORDER BY to_jsonb(r)::text', (case.tenant,)).fetchall() for table in tables}


@pytest.mark.parametrize('operation', ['device.trust', 'device.revoke', 'session.revoke'])
def test_sql_failure_after_real_account_source_insert_rolls_back_business_request_and_step_up(
    account_notification_case, monkeypatch, operation,
):
    """실제 INSERT 뒤 SQL 오류를 주입한다. 성공 흐름이나 source를 mock하지 않는다."""
    import daon_user_api.notification_events as events
    case = account_notification_case
    mutate, arguments, _ = _account_operation(case, operation)
    before = _snapshot(case)
    original = events.enqueue_account_event
    calls = []

    def fail_after_insert(*args, **kwargs):
        inserted = original(*args, **kwargs)
        assert inserted == 1, '실제 source INSERT 이후에만 SQL 오류를 주입한다'
        calls.append(inserted)
        args[0]._connection.execute('SELECT 1/0')

    with monkeypatch.context() as patch:
        patch.setattr(events, 'enqueue_account_event', fail_after_insert)
        with pytest.raises(IdentityError) as failed:
            mutate(**arguments)
    assert failed.value.http_status == 503 and failed.value.code == 'ACCOUNT_STORAGE_UNAVAILABLE'
    assert calls == [1]
    assert _snapshot(case) == before, '대상 변경·요청·추가 인증 소비·source·알림 의도를 함께 rollback해야 한다'
    # 같은 정상 인증/요청으로 다시 시도하면 실제 성공하고 이전 실패의 부분 원장은 없다.
    assert mutate(**arguments)['replayed'] is False
    with psycopg.connect(case.admin) as connection:
        assert connection.execute('SELECT count(*) FROM public.notification_source_outbox WHERE tenant_id=%s',
            (case.tenant,)).fetchone() == (1,)


@pytest.mark.parametrize('operation', ['device.trust', 'device.revoke', 'session.revoke'])
def test_default_off_account_operations_do_not_create_notification_sources(account_notification_case, operation):
    case = account_notification_case
    case.service = AccountSettingsService(case.identity)
    mutate, arguments, _ = _account_operation(case, operation)
    assert mutate(**arguments)['replayed'] is False
    with psycopg.connect(case.admin) as connection:
        assert connection.execute('SELECT count(*) FROM public.identity_account_requests '
            'WHERE tenant_id=%s AND operation=%s', (case.tenant, operation)).fetchone() == (1,)
        for table in ('notification_source_outbox', 'notifications', 'notification_details', 'notification_audit_outbox'):
            assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',
                (case.tenant,)).fetchone() == (0,)


def test_actual_account_event_runtime_http_read_and_current_role_revocation(account_notification_case):
    """실제 PG 로그인·계정 사건·알림 API다. 브라우저/정식 배포/자동 소비 시험은 아니다."""
    from fastapi.testclient import TestClient
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.authorization import AuthorizationService
    from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
    from daon_user_api.notification_events import materialize_pending_accounts
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, WEB_SESSION_COOKIE, create_app
    case = account_notification_case
    mutate, arguments, target = _account_operation(case, 'device.revoke')
    assert mutate(**arguments)['replayed'] is False
    scope = CloudAccessContext(case.tenant, case.workspace, case.actor, 'view')
    assert materialize_pending_accounts(case.app, context=scope) == 1
    audit = AuditEventStore()
    authorization_repository = PostgresAuthorizationRepository(case.app)
    cloud = PostgresCloudStore(case.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    dependencies = RuntimeDependencies(settings=RuntimeSettings(profile='test', bind_host='127.0.0.1', port=8000,
        cloud_database_dsn=case.app, policy_version=case.policy, development_stage=True,
        notification_inbox_enabled=True), identity_service=case.identity,
        authorization_service=AuthorizationService(repository=authorization_repository, audit_store=audit,
            clock=lambda: case.identity._now(), identity_service=case.identity), audit_store=audit,
        identity_repository=case.repository, authorization_repository=authorization_repository, cloud_store=cloud)
    client = None
    try:
        # lifespan 미기동: 다른 QA의 pending/기한 sweep를 시작하지 않는다.
        client = TestClient(create_app(dependencies), base_url='http://127.0.0.1')
        auth = {'cookie': f'{WEB_SESSION_COOKIE}={case.credentials.access_token}'}
        page = client.get('/api/v1/notifications', headers=auth)
        assert page.status_code == 200
        data = page.json()['data']
        assert data['unread_count'] == 1 and len(data['items']) == 1
        item = data['items'][0]
        assert (item['resource_type'], item['resource_id'], item['deep_link']) == ('device', target, '/settings/account')
        url = '/api/v1/notifications/' + item['id']
        detail = client.get(url, headers=auth)
        assert detail.status_code == 200 and detail.json()['data'] == item
        read_headers = {**auth, 'If-Match': detail.headers['etag'], 'Idempotency-Key': 's42-account-http-read-once'}
        first = client.patch(url, headers=read_headers, json={'state': 'read'})
        assert first.status_code == 200 and first.json()['data']['version'] == 2
        repeated = client.patch(url, headers=read_headers, json={'state': 'read'})
        assert repeated.status_code == 200 and repeated.json()['data'] == first.json()['data']
        with psycopg.connect(case.admin) as connection:
            connection.execute("UPDATE identity_auth_tenant_roles SET state='revoked',version=version+1,updated_at=now() "
                'WHERE tenant_id=%s AND user_id=%s', (case.tenant, case.actor))
        hidden = client.get('/api/v1/notifications', headers=auth)
        assert hidden.status_code == 200 and hidden.json()['data'] == {'items': [], 'next_cursor': None, 'unread_count': 0}
        assert client.get(url, headers=auth).status_code == 404
        assert client.patch(url, headers=read_headers, json={'state': 'read'}).status_code == 404
        with psycopg.connect(case.admin) as connection:
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox '
                'WHERE tenant_id=%s GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [
                    ('notification.created', 1), ('notification.read', 1)]
    finally:
        if client is not None:
            client.close()
        case.identity.revoke_current_web_session(access_token=case.credentials.access_token,
            trace_id=case.trace, policy_version=case.policy)
        dependencies.close()
