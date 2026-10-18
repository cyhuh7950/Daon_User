"""S42 새 로그인 source의 실제 PG 소비와 합성 principal 서비스 포트 시험.

로그인·중앙 원감사·source는 실제 서버 hook으로 생성한다. principal 전달은
인증 포트 입력이며 실제 HTTP/브라우저 증거가 아니다. 과거 로그인 동값 UPDATE/
감사 clone의 producer I1은 별도 미해소이며 이 소비 시험으로 닫지 않는다.
실행·DDL·QA 수명은 main 소유다. 원감사·session·source를 직접 INSERT하지 않는다.
"""
from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest

from test_account_notification_events import account_notification_case
from test_identity_security_notification_events import _private_login, _source_rows


def _consumer():
    name = 'daon_user_api.notification_identity_login'
    assert importlib.util.find_spec(name) is not None, 'IDENTITY_LOGIN_NOTIFICATION_CONSUMER_NOT_IMPLEMENTED'
    module = importlib.import_module(name)
    assert callable(getattr(module, 'materialize_pending_identity_logins', None)), 'IDENTITY_LOGIN_CONSUMER_PORT_MISSING'
    return module


def test_login_notification_actual_authenticated_http_read_and_reopen(account_notification_case):
    """실제 인증·PG·ASGI 경로다. 브라우저·정식 WSL·자동 소비는 미검증이다."""
    from fastapi.testclient import TestClient
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.authorization import AuthorizationService
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, WEB_SESSION_COOKIE, create_app

    case = account_notification_case
    login = _pending_login(case)
    assert _consumer().materialize_pending_identity_logins(case.app, context=login.context) == 1
    audit = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    authorization_repository = PostgresAuthorizationRepository(case.app)
    cloud = PostgresCloudStore(case.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    dependencies = RuntimeDependencies(settings=RuntimeSettings(profile='test', bind_host='127.0.0.1', port=8000,
        cloud_database_dsn=case.app, policy_version=case.policy, development_stage=True, notification_inbox_enabled=True),
        identity_service=case.identity, authorization_service=AuthorizationService(repository=authorization_repository,
            audit_store=audit, clock=lambda: datetime.now(timezone.utc), identity_service=case.identity), audit_store=audit,
        identity_repository=case.repository, authorization_repository=authorization_repository, cloud_store=cloud)
    client = None
    try:
        # 기존 실제 인증 토큰을 재사용한다. 별도 로그인/합성 principal 주입은 하지 않는다.
        client = TestClient(create_app(dependencies), base_url='http://127.0.0.1')
        headers = {'cookie': f'{WEB_SESSION_COOKIE}={case.credentials.access_token}'}
        before = _ledger(case)
        response = client.get('/api/v1/notifications', headers=headers)
        assert response.status_code == 200
        page = response.json()['data']
        assert page['unread_count'] == len(page['items']) == 1
        item = page['items'][0]
        assert (item['source_event_id'], item['kind'], item['resource_type'], item['resource_id'], item['deep_link']) == (
            login.source_id, 'authentication', 'session', login.session_id, '/settings/account')
        url = '/api/v1/notifications/' + item['id']
        detail = client.get(url, headers=headers)
        assert detail.status_code == 200 and detail.json()['data'] == item
        assert _ledger(case) == before
        read_headers = {**headers, 'If-Match': detail.headers['etag'], 'Idempotency-Key': 's42-login-actual-http-read-once'}
        first = client.patch(url, headers=read_headers, json={'state': 'read'})
        assert first.status_code == 200 and first.json()['data']['version'] == 2
        after = _ledger(case)
        replay = client.patch(url, headers=read_headers, json={'state': 'read'})
        assert replay.status_code == 200 and replay.json()['data'] == first.json()['data']
        reopened = client.get(url, headers=headers)
        assert reopened.status_code == 200 and reopened.json()['data'] == first.json()['data']
        assert _ledger(case) == after
        assert len(after['idempotency_records']) == 1
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox WHERE tenant_id=%s '
                'GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [
                    ('notification.created', 1), ('notification.read', 1)]
    finally:
        if client is not None:
            client.close()
        dependencies.close()


@pytest.mark.parametrize('boundary', ['sql', 'dto'])
def test_login_consumer_failure_after_real_insert_rolls_back_source_ack_and_retries(
    account_notification_case, monkeypatch, boundary,
):
    """실제 INSERT 후 오류를 잡아 성공으로 commit하는 결함을 검출한다."""
    from contextlib import contextmanager
    from daon_user_api.notification import NotificationError

    case = account_notification_case
    login = _pending_login(case)
    consumer = _consumer()
    before = _ledger(case)
    real_connect, real_validate = consumer.connect, consumer._validate_created_audit
    connections, phases = [], []

    @contextmanager
    def observed_connect(*args, **kwargs):
        with real_connect(*args, **kwargs) as connection:
            connections.append(connection)
            yield connection

    def fail_after_actual_insert(payload, item, target):
        connection = connections[-1]
        assert connection.execute('SELECT count(*) FROM public.notification_details '
            'WHERE tenant_id=%s AND notification_id=%s',
            (case.tenant, item.notification_id)).fetchone() == (1,)
        phases.append('actual_detail_inserted')
        if boundary == 'dto':
            altered = {**payload, 'metadata': {**payload['metadata'], 'unexpected': 's42-negative-case'}}
            return real_validate(altered, item, target)
        real_validate(payload, item, target)
        connection.execute('SELECT 1/0')

    with monkeypatch.context() as patch:
        patch.setattr(consumer, 'connect', observed_connect)
        patch.setattr(consumer, '_validate_created_audit', fail_after_actual_insert)
        with pytest.raises(NotificationError) as failed:
            consumer.materialize_pending_identity_logins(case.app, context=login.context)
    assert (failed.value.code, failed.value.http_status) == (
        'DATABASE_UNAVAILABLE' if boundary == 'sql' else 'DATABASE_RESPONSE_INVALID', 503)
    assert phases == ['actual_detail_inserted']
    assert _ledger(case) == before
    assert consumer.materialize_pending_identity_logins(case.app, context=login.context) == 1
    assert consumer.materialize_pending_identity_logins(case.app, context=login.context) == 0


def _pending_login(case):
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.identity import IdentityService
    from daon_user_api.notification_postgres import _time

    trace = case.trace + '-login-consumer'
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        before = _source_rows(connection, case)
    central = PostgresSecurityAuditStore(case.app, min_size=1, max_size=1)
    try:
        identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
            clock=lambda: datetime.now(timezone.utc), dispatch_pending_audits=False, notification_inbox_enabled=True)
        credentials = _private_login(identity, case, trace)
    finally:
        central.close()
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        sources = _source_rows(connection, case)
        assert all(sources.get(key) == value for key, value in before.items())
        added = set(sources) - set(before)
        assert len(added) == 1, 'REAL_NEW_LOGIN_SOURCE_NOT_PREPARED'
        source_id = next(iter(added))
        source = sources[source_id]
        assert (source['workspace_id'], source['recipient_id'], source['source_store'], source['action'],
            source['target_type'], source['target_id'], source['state'], source['version'], source['delivered_at']) == (
                case.workspace, case.actor, 'identity', 'identity.login.succeeded', 'session', credentials.session_id,
                'pending', 1, None)
        original = connection.execute('SELECT event_id,occurred_at,actor_id,actor_type,tenant_id,workspace_id,action,'
            'target_type,target_id,outcome,trace_id,policy_version,before_value,after_value,metadata '
            'FROM public.security_audit_events WHERE tenant_id=%s AND event_id=%s',
            (case.tenant, source['origin_event_id'])).fetchall()
        assert len(original) == 1
        assert original[0][2:] == (case.actor, 'user', case.tenant, None, 'identity.login.succeeded', 'session',
            credentials.session_id, 'succeeded', trace, case.policy, None, None,
            {'client_type': 'web', 'auth_method': 'local'})
        assert _time(source['occurred_at']) == _time(original[0][1])
        # 안전한 metadata만 사용한다. credential/token/digest 열은 원행 불변 비교에도 포함하지 않는다.
        session = connection.execute('SELECT session_id,tenant_id,user_id,device_id,client_kind,state,created_at,updated_at '
            'FROM public.identity_sessions WHERE tenant_id=%s AND user_id=%s AND session_id=%s',
            (case.tenant, case.actor, credentials.session_id)).fetchone()
        assert session is not None and session[3:6] == (credentials.device_id, 'web', 'active')
    return SimpleNamespace(context=CloudAccessContext(case.tenant, case.workspace, case.actor, 'view'),
        source_id=source_id, origin=source['origin_event_id'], source=source, trace=trace,
        session_id=credentials.session_id, device_id=credentials.device_id, session=session, original=original)


def _ledger(case):
    # 합성 QA tenant의 알림 metadata만 읽는다. session/device/credential 전체 행 JSON 조회0.
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        return {table: connection.execute(
            f'SELECT to_jsonb(x) FROM public.{table} x WHERE tenant_id=%s ORDER BY to_jsonb(x)::text',
            (case.tenant,)).fetchall() for table in (
                'notifications', 'notification_details', 'notification_source_outbox',
                'notification_audit_outbox', 'idempotency_records')}


def _target(case, login):
    from daon_user_api.notification_events import _account_notification_scope
    from daon_user_api.notification_postgres import _time

    with psycopg.connect(case.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        with _account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor,
            workspace_id=case.workspace) as reader:
            row = reader.execute('SELECT public.current_identity_login_notification_target(%s)',
                (login.source_id,)).fetchone()
            assert row is not None and type(row[0]) is dict
            target = dict(row[0])
            assert type(target.get('accessible')) is bool
            target['occurred_at'] = _time(target['occurred_at'])
            assert target == dict(accessible=target['accessible'], title='로그인 완료',
                summary='본인 계정의 로그인이 완료되었습니다.', deep_link='/settings/account',
                source_event_id=login.source_id, origin_event_id=login.origin, target_type='session',
                target_id=login.session_id, kind='authentication', severity='info', trace_id=login.trace,
                policy_version=case.policy, occurred_at=_time(login.source['occurred_at']), action='identity.login.succeeded')
            return target


def _service(case, login):
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.authorization import AuthorizationService
    from daon_user_api.identity import IdentityPrincipal
    from daon_user_api.notification import NotificationService
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository

    repository = PostgresNotificationRepository(case.app, context=login.context)
    authorization_repository = PostgresAuthorizationRepository(case.app)
    central = PostgresSecurityAuditStore(case.app, min_size=1, max_size=1)
    # principal은 포트 입력이다. 실제 로그인 ID를 쓰지만 session middleware/HTTP 실행 증거는 아니다.
    principal = IdentityPrincipal(case.actor, login.session_id, login.device_id, case.tenant)
    service = NotificationService(repository=repository, authorization_service=AuthorizationService(
        repository=authorization_repository, audit_store=central, clock=lambda: datetime.now(timezone.utc)),
        audit_store=central, clock=lambda: datetime.now(timezone.utc))
    return service, repository, authorization_repository, central, principal


def test_new_local_login_source_materializes_reopens_and_marks_read_once(account_notification_case):
    from daon_user_api.notification_postgres import PostgresNotificationRepository, _time

    case = account_notification_case
    login = _pending_login(case)
    consumer = _consumer()  # 정상 source/실제 중앙 감사를 확인한 뒤 제품 미구현 RED에 도달한다.
    assert _target(case, login)['accessible'] is True
    assert consumer.materialize_pending_identity_logins(case.app, context=login.context, limit=100) == 1
    after = _ledger(case)
    assert consumer.materialize_pending_identity_logins(case.app, context=login.context, limit=100) == 0
    assert _ledger(case) == after
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        actual = connection.execute('SELECT n.notification_id,n.created_at,d.source_store,d.origin_event_id,d.source_event_id,'
            'd.notebook_id,d.title,d.summary,d.kind,d.severity,d.resource_type,d.resource_id,d.deep_link,d.trace_id,d.policy_version,'
            's.state,s.occurred_at,s.delivered_at,a.event_id,a.draft,'
            'n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin '
            'FROM public.notifications n JOIN public.notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN public.notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.recipient_id=n.recipient_id AND s.source_event_id=n.source_event_id '
            'JOIN public.notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id '
            "AND a.action='notification.created' WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s AND n.source_event_id=%s",
            (case.tenant, case.workspace, case.actor, login.source_id)).fetchall()
        assert len(actual) == 1
        notification_id, created = actual[0][:2]
        assert actual[0][2:17] == ('identity', login.origin, login.source_id, None, '로그인 완료',
            '본인 계정의 로그인이 완료되었습니다.', 'authentication', 'info', 'session', login.session_id,
            '/settings/account', login.trace, case.policy, 'delivered', login.source['occurred_at'])
        assert _time(created) >= _time(actual[0][17])
        draft = actual[0][19]
        assert draft == dict(event_id=actual[0][18], occurred_at=draft['occurred_at'], actor_id='notification-service',
            actor_type='service', tenant_id=case.tenant, workspace_id=case.workspace, action='notification.created',
            target_type='notification', target_id=notification_id, outcome='succeeded', trace_id=login.trace,
            policy_version=case.policy, before=None,
            after={'recipient_id': case.actor, 'kind': 'authentication', 'delivery_state': 'delivered'},
            metadata={'source_event_id': login.source_id})
        assert _time(draft['occurred_at']) == _time(created) and actual[0][20] is True
    service, repository, authorization_repository, central, principal = _service(case, login)
    try:
        page = service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
            trace_id=login.trace, policy_version=case.policy)
        assert len(page.items) == page.unread_count == 1
        item = page.items[0]
        assert (item.notification_id, item.source_event_id, item.title, item.summary, item.kind, item.severity,
            item.resource_type, item.resource_id, item.deep_link, item.trace_id) == (
                notification_id, login.source_id, '로그인 완료', '본인 계정의 로그인이 완료되었습니다.',
                'authentication', 'info', 'session', login.session_id, '/settings/account', login.trace)
        assert service.get_notification(principal=principal, notification_id=notification_id,
            trace_id=login.trace, policy_version=case.policy) == item
        # 기존 authorization 중앙 감사와 구분해 GET의 알림/receipt/원업무 쓰기0을 확인한다.
        assert _ledger(case) == after
        arguments = dict(principal=principal, notification_id=notification_id, expected_etag=item.etag,
            idempotency_key='s42-login-consumer-read-0001', requested_state='read',
            trace_id=login.trace, policy_version=case.policy)
        read = service.mark_read(**arguments)
        assert read.version == 2 and read.read_at is not None
        read_ledger = _ledger(case)
        assert service.mark_read(**arguments) == read
        assert _ledger(case) == read_ledger
        reopened = PostgresNotificationRepository(case.app, context=login.context)
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.actor, notification_id=notification_id) == read
        assert reopened.notifications_for(tenant_id=case.tenant, recipient_id=case.actor) == (read,)
        assert _ledger(case) == read_ledger
        assert len(read_ledger['idempotency_records']) == 1
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox WHERE tenant_id=%s '
                'GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [
                    ('notification.created', 1), ('notification.read', 1)]
            assert connection.execute('SELECT session_id,tenant_id,user_id,device_id,client_kind,state,created_at,updated_at '
                'FROM public.identity_sessions WHERE tenant_id=%s AND user_id=%s AND session_id=%s',
                (case.tenant, case.actor, login.session_id)).fetchone() == login.session
            assert connection.execute('SELECT event_id,occurred_at,actor_id,actor_type,tenant_id,workspace_id,action,'
                'target_type,target_id,outcome,trace_id,policy_version,before_value,after_value,metadata '
                'FROM public.security_audit_events WHERE tenant_id=%s AND event_id=%s',
                (case.tenant, login.origin)).fetchall() == login.original
    finally:
        authorization_repository.close()
        central.close()


@pytest.mark.parametrize('revocation', ['identity', 'auth'])
def test_delivered_login_notification_current_revocation_hides_reads_and_receipt_replay(
    account_notification_case, revocation,
):
    from daon_user_api.notification import NotificationError
    from daon_user_api.notification_postgres import PostgresNotificationRepository

    case = account_notification_case
    login = _pending_login(case)
    consumer = _consumer()
    assert consumer.materialize_pending_identity_logins(case.app, context=login.context) == 1
    service, repository, authorization_repository, central, principal = _service(case, login)
    try:
        items = repository.notifications_for(tenant_id=case.tenant, recipient_id=case.actor)
        assert len(items) == 1 and items[0].source_event_id == login.source_id
        item = items[0]
        arguments = dict(principal=principal, notification_id=item.notification_id, expected_etag=item.etag,
            idempotency_key='s42-login-revoked-read-0001', requested_state='read',
            trace_id=login.trace, policy_version=case.policy)
        assert service.mark_read(**arguments).version == 2
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            if revocation == 'identity':
                assert connection.execute('DELETE FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s RETURNING role',
                    (case.tenant, case.actor)).fetchall() == [('personal_owner',)]
                assert connection.execute('SELECT role,state FROM public.identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s',
                    (case.tenant, case.actor)).fetchone() == ('personal_owner', 'active')
            else:
                assert connection.execute("UPDATE public.identity_auth_tenant_roles SET state='inactive',version=version+1,updated_at=now() "
                    "WHERE tenant_id=%s AND user_id=%s AND role='personal_owner' AND state='active' RETURNING role,state",
                    (case.tenant, case.actor)).fetchall() == [('personal_owner', 'inactive')]
                assert connection.execute('SELECT role FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s',
                    (case.tenant, case.actor)).fetchone() == ('personal_owner',)
                assert connection.execute('SELECT count(*) FROM public.identity_auth_memberships WHERE tenant_id=%s '
                    'AND workspace_id=%s AND user_id=%s', (case.tenant, case.workspace, case.actor)).fetchone() == (0,)
        before = _ledger(case)
        assert _target(case, login)['accessible'] is False
        reopened = PostgresNotificationRepository(case.app, context=login.context)
        assert reopened.notifications_for(tenant_id=case.tenant, recipient_id=case.actor) == ()
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.actor, notification_id=item.notification_id) is None
        page = service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
            trace_id=login.trace, policy_version=case.policy)
        assert page.items == () and page.unread_count == 0
        with pytest.raises(NotificationError) as unavailable:
            service.get_notification(principal=principal, notification_id=item.notification_id,
                trace_id=login.trace, policy_version=case.policy)
        assert (unavailable.value.code, unavailable.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        with pytest.raises(NotificationError) as replay:
            service.mark_read(**arguments)
        assert (replay.value.code, replay.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        assert _ledger(case) == before
        assert len(before['idempotency_records']) == 1
        assert len(before['notification_audit_outbox']) == 2
    finally:
        authorization_repository.close()
        central.close()
