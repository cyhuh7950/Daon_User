"""S42 조직 역할 source의 실제 PG 소비/현재 조회 경계. 실행·DDL은 main 소유다.

조직 fixture의 실제 원업무/source와 실제 중앙 감사 dispatcher를 사용한다.
로그인·HTTP·Provider·정식 환경 또는 clone-first 안전성의 증거가 아니다.
"""
from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timezone

import psycopg
import pytest

from test_organization_notification_events import organization_notification_case, _operation


def _consumer():
    name = 'daon_user_api.notification_organization'
    assert importlib.util.find_spec(name) is not None, 'ORGANIZATION_NOTIFICATION_CONSUMER_NOT_IMPLEMENTED'
    module = importlib.import_module(name)
    assert callable(getattr(module, 'materialize_pending_organization_roles', None)), 'ORGANIZATION_CONSUMER_PORT_MISSING'
    return module


def test_organization_notification_actual_local_login_http_read_and_revocation(organization_notification_case):
    """실제 로컬 인증·PG·알림 HTTP이며 브라우저/정식 배포/자동 소비 시험은 아니다."""
    import secrets
    from fastapi.testclient import TestClient
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.authorization import AuthorizationService
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.identity import IdentityService, PASSWORD_HASHER, DevicePlatform
    from daon_user_api.identity_postgres import PostgresIdentityRepository
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, WEB_SESSION_COOKIE, create_app

    case = organization_notification_case
    scope, source_id, origin = _role_source(case)
    _dispatch_origin(case, origin)
    assert _consumer().materialize_pending_organization_roles(case.app, context=scope) == 1
    password = secrets.token_urlsafe(32)
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        connection.execute('UPDATE identity_users SET password_digest=%s WHERE user_id=%s',
            (PASSWORD_HASHER.hash(password), case.recipient))
    audit = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    identity_repository = PostgresIdentityRepository(case.app)
    identity = IdentityService(repository=identity_repository, audit_store=audit, oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc), step_up_token_key=secrets.token_bytes(32), dispatch_pending_audits=False)
    authorization_repository = PostgresAuthorizationRepository(case.app)
    cloud = PostgresCloudStore(case.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    dependencies = RuntimeDependencies(settings=RuntimeSettings(profile='test', bind_host='127.0.0.1', port=8000,
        cloud_database_dsn=case.app, policy_version=case.policy, development_stage=True, notification_inbox_enabled=True),
        identity_service=identity, authorization_service=AuthorizationService(repository=authorization_repository,
            audit_store=audit, clock=lambda: datetime.now(timezone.utc), identity_service=identity), audit_store=audit,
        identity_repository=identity_repository, authorization_repository=authorization_repository, cloud_store=cloud)
    credentials = client = None
    try:
        credentials = identity.local_login(login_id=case.recipient, password=password,
            platform=DevicePlatform.WEB, trace_id=case.trace, policy_version=case.policy)
        password = None
        # lifespan 미기동: 다른 QA 업무/자동 sweep는 시작하지 않는다.
        client = TestClient(create_app(dependencies), base_url='http://127.0.0.1')
        auth = {'cookie': f'{WEB_SESSION_COOKIE}={credentials.access_token}'}
        response = client.get('/api/v1/notifications', headers=auth)
        assert response.status_code == 200
        page = response.json()['data']
        assert page['unread_count'] == len(page['items']) == 1
        item = page['items'][0]
        assert (item['source_event_id'], item['resource_type'], item['resource_id'], item['deep_link']) == (
            source_id, 'membership', case.recipient, '/notifications')
        url = '/api/v1/notifications/' + item['id']
        detail = client.get(url, headers=auth)
        assert detail.status_code == 200 and detail.json()['data'] == item
        headers = {**auth, 'If-Match': detail.headers['etag'], 'Idempotency-Key': 's42-org-actual-http-read-once'}
        first = client.patch(url, headers=headers, json={'state': 'read'})
        assert first.status_code == 200 and first.json()['data']['version'] == 2
        replay = client.patch(url, headers=headers, json={'state': 'read'})
        assert replay.status_code == 200 and replay.json()['data'] == first.json()['data']
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            connection.execute("UPDATE identity_auth_memberships SET state='revoked',version=version+1,updated_at=now() "
                'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s', (case.tenant, case.workspace, case.recipient))
        hidden = client.get('/api/v1/notifications', headers=auth)
        assert hidden.status_code == 200 and hidden.json()['data'] == {'items': [], 'next_cursor': None, 'unread_count': 0}
        assert client.get(url, headers=auth).status_code == 404
        assert client.patch(url, headers=headers, json={'state': 'read'}).status_code == 404
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM notification_audit_outbox WHERE tenant_id=%s '
                'GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [
                    ('notification.created', 1), ('notification.read', 1)]
    finally:
        password = None
        if client is not None:
            client.close()
        if credentials is not None:
            identity.revoke_current_web_session(access_token=credentials.access_token,
                trace_id=case.trace, policy_version=case.policy)
        dependencies.close()


def _role_source(case):
    from daon_user_api.cloud_storage import CloudAccessContext

    result = _operation(case, 'organization.member.role', case.recipient,
        dict(tenant_id=case.tenant, user_id=case.recipient, workspace_id=case.workspace,
            role='editor', expected_version=1, expected_auth_version=1,
            reason='S42_SYNTHETIC_REASON_NOT_NOTIFICATION_BODY'), 's42-org-consumer-role-0001')
    assert result.replayed is False and result.value['access_role'] == 'editor'
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        rows = connection.execute('SELECT source_event_id,origin_event_id,state,version,delivered_at '
            'FROM public.notification_source_outbox WHERE tenant_id=%s AND workspace_id=%s AND recipient_id=%s',
            (case.tenant, case.workspace, case.recipient)).fetchall()
        assert len(rows) == 1 and rows[0][2:] == ('pending', 1, None)
        source_id, origin = rows[0][:2]
        assert connection.execute('SELECT actor_id,target_id,operation,delivered_at '
            'FROM public.identity_org_operation_outbox WHERE event_id=%s', (origin,)).fetchone() == (
                case.manager, case.recipient, 'organization.member.role', None)
        assert connection.execute('SELECT count(*) FROM public.security_audit_events WHERE tenant_id=%s AND event_id=%s',
            (case.tenant, origin)).fetchone() == (0,)
    return CloudAccessContext(case.tenant, case.workspace, case.recipient, 'view'), source_id, origin


@pytest.mark.parametrize('boundary', ['sql', 'dto'])
def test_organization_consumer_post_materialization_failure_rolls_back_and_retries(
    organization_notification_case, monkeypatch, boundary,
):
    from contextlib import contextmanager
    from daon_user_api.notification import NotificationError

    case = organization_notification_case
    scope, _source_id, origin = _role_source(case)
    _dispatch_origin(case, origin)
    consumer = _consumer()
    before = _ledger(case)
    real_connect, real_validate = consumer.connect, consumer._validate_created_audit
    connections, phases = [], []

    @contextmanager
    def observed_connect(*args, **kwargs):
        with real_connect(*args, **kwargs) as connection:
            connections.append(connection)
            yield connection

    def fail_after_actual_materialization(payload, item, target):
        connection = connections[-1]
        assert connection.execute('SELECT count(*) FROM public.notification_details WHERE tenant_id=%s '
            'AND notification_id=%s', (case.tenant, item.notification_id)).fetchone() == (1,)
        phases.append('actual_detail_inserted')
        if boundary == 'dto':
            altered = {**payload, 'metadata': {**payload['metadata'], 'unexpected': 's42-negative-case'}}
            return real_validate(altered, item, target)
        real_validate(payload, item, target)
        connection.execute('SELECT 1/0')

    with monkeypatch.context() as patch:
        patch.setattr(consumer, 'connect', observed_connect)
        patch.setattr(consumer, '_validate_created_audit', fail_after_actual_materialization)
        with pytest.raises(NotificationError) as failed:
            consumer.materialize_pending_organization_roles(case.app, context=scope)
    assert failed.value.http_status == 503
    assert failed.value.code == ('DATABASE_UNAVAILABLE' if boundary == 'sql' else 'DATABASE_RESPONSE_INVALID')
    assert phases == ['actual_detail_inserted']
    assert _ledger(case) == before
    assert consumer.materialize_pending_organization_roles(case.app, context=scope) == 1
    assert consumer.materialize_pending_organization_roles(case.app, context=scope) == 0


def _ledger(case):
    # QA 자체 범위의 비민감 업무/알림 행만 비교한다. DSN/토큰/원문은 읽지 않는다.
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        return {table: connection.execute(
            f'SELECT to_jsonb(x) FROM public.{table} x WHERE tenant_id=%s ORDER BY to_jsonb(x)::text',
            (case.tenant,)).fetchall() for table in (
                'identity_org_operation_outbox', 'identity_org_tenant_memberships',
                'identity_auth_memberships', 'identity_auth_workspaces', 'identity_memberships',
                'notifications', 'notification_details', 'notification_source_outbox',
                'notification_audit_outbox', 'idempotency_records')}


def _dispatch_origin(case, origin):
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.postgres_adapters import PostgresOrganizationRepository

    central = PostgresSecurityAuditStore(case.app, min_size=1, max_size=1)
    repository = PostgresOrganizationRepository(case.app, audit_store=central, notification_inbox_enabled=True)
    try:
        # 실제 dispatcher이며 원감사/원업무를 알림 시험에서 합성하지 않는다.
        repository.dispatch_pending_management_audits(limit=100)
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT actor_id,action,target_type,target_id,trace_id,policy_version '
                'FROM public.security_audit_events WHERE tenant_id=%s AND event_id=%s',
                (case.tenant, origin)).fetchall() == [(case.manager, 'organization.membership.role_changed',
                    'membership', case.recipient, case.trace, case.policy)]
            assert connection.execute('SELECT delivered_at IS NOT NULL FROM public.identity_org_operation_outbox '
                'WHERE tenant_id=%s AND event_id=%s', (case.tenant, origin)).fetchone() == (True,)
    finally:
        repository.close()
        central.close()


def _current_with_caller_restored(case, scope, source_id):
    import daon_user_api.notification_events as events

    # 원manager 문맥과 수신자 app 문맥은 다르다. RPC 성공/대기/거부 모두 원값을 보존해야 한다.
    names = ('daon.organization_actor', 'daon.organization_tenant', 'app.actor_id',
        'app.tenant_id', 'app.workspace_id', 'app.capability')
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        with events._account_notification_scope(connection, tenant_id=scope.tenant_id,
            actor_id=scope.actor_id, workspace_id=scope.workspace_id) as reader:
            reader.execute("SELECT set_config('daon.organization_actor',%s,true)", ('s42-original-reader',))
            reader.execute("SELECT set_config('daon.organization_tenant',%s,true)", ('s42-original-reader-tenant',))
            before = [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names]
            result = reader.execute('SELECT public.current_organization_role_notification_target(%s)',
                (source_id,)).fetchone()[0]
            assert [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names] == before
            return result


def _service(case, scope):
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.authorization import AuthorizationService
    from daon_user_api.identity import IdentityPrincipal
    from daon_user_api.notification import NotificationService
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository

    repository = PostgresNotificationRepository(case.app, context=scope)
    authorization_repository = PostgresAuthorizationRepository(case.app)
    audit = AuditEventStore()
    # 인증된 principal 포트 입력은 합성이다. 실제 역할·현재 접근은 PG에서 판정한다.
    principal = IdentityPrincipal(case.recipient, 's42-org-consumer-session', 's42-org-consumer-device', case.tenant)
    service = NotificationService(repository=repository, authorization_service=AuthorizationService(
        repository=authorization_repository, audit_store=audit, clock=lambda: datetime.now(timezone.utc)),
        audit_store=audit, clock=lambda: datetime.now(timezone.utc))
    return service, repository, authorization_repository, principal


def test_organization_role_notification_waits_for_central_origin_then_materializes_reopens_and_reads(
    organization_notification_case,
):
    case = organization_notification_case
    scope, source_id, origin = _role_source(case)
    consumer = _consumer()
    before = _ledger(case)
    assert _current_with_caller_restored(case, scope, source_id) == {'state': 'awaiting_origin'}
    assert consumer.materialize_pending_organization_roles(case.app, context=scope) == 0
    assert _ledger(case) == before  # pending/ack0: 중앙 원감사 미전달을 권한 거부로 억제하지 않는다.
    _dispatch_origin(case, origin)  # 소비가 먼저 성공해야만 dispatch되는 순환 의존을 허용하지 않는다.
    origin_ledger = _ledger(case)['identity_org_operation_outbox']
    target = _current_with_caller_restored(case, scope, source_id)
    assert target == dict(accessible=True, title='작업공간 역할 변경', summary='본인의 작업공간 역할이 변경되었습니다.',
        deep_link='/notifications', source_event_id=source_id, origin_event_id=origin, target_type='membership',
        target_id=case.recipient, kind='membership', severity='info', trace_id=case.trace, policy_version=case.policy,
        occurred_at=before['notification_source_outbox'][0][0]['occurred_at'], action='organization.membership.role_changed')
    assert consumer.materialize_pending_organization_roles(case.app, context=scope) == 1
    after = _ledger(case)
    assert consumer.materialize_pending_organization_roles(case.app, context=scope) == 0
    assert _ledger(case) == after
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        rows = connection.execute('SELECT n.notification_id,n.created_at,d.source_store,d.origin_event_id,d.source_event_id,'
            'd.notebook_id,d.resource_type,d.resource_id,d.title,d.summary,d.kind,d.severity,d.deep_link,d.trace_id,d.policy_version,'
            's.state,s.occurred_at,s.delivered_at,(a.draft->>\'occurred_at\')::timestamptz,'
            'n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin '
            'FROM public.notifications n JOIN public.notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN public.notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.recipient_id=n.recipient_id AND s.source_event_id=n.source_event_id '
            "JOIN public.notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id AND a.action='notification.created' "
            'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s',
            (case.tenant, case.workspace, case.recipient)).fetchall()
        assert len(rows) == 1
        notification_id, created = rows[0][:2]
        from daon_user_api.notification_postgres import _time
        assert rows[0][2:17] == ('organization', origin, source_id, None, 'membership', case.recipient,
            '작업공간 역할 변경', '본인의 작업공간 역할이 변경되었습니다.', 'membership', 'info', '/notifications',
            case.trace, case.policy, 'delivered', _time(before['notification_source_outbox'][0][0]['occurred_at']))
        assert created >= rows[0][17] and rows[0][18] == created and rows[0][19] is True
    service, repository, authorization_repository, principal = _service(case, scope)
    try:
        page = service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
            trace_id=case.trace, policy_version=case.policy)
        assert len(page.items) == page.unread_count == 1
        item = page.items[0]
        assert (item.notification_id, item.source_event_id, item.resource_type, item.resource_id, item.deep_link) == (
            notification_id, source_id, 'membership', case.recipient, '/notifications')
        assert service.get_notification(principal=principal, notification_id=notification_id,
            trace_id=case.trace, policy_version=case.policy) == item
        assert _ledger(case) == after  # 재접속 list/detail/현재 원장 검사는 GET 쓰기0이다.
        arguments = dict(principal=principal, notification_id=notification_id, expected_etag=item.etag,
            idempotency_key='s42-org-notification-read-0001', requested_state='read',
            trace_id=case.trace, policy_version=case.policy)
        read = service.mark_read(**arguments)
        assert read.version == 2 and read.read_at is not None
        read_ledger = _ledger(case)
        assert service.mark_read(**arguments) == read
        assert _ledger(case) == read_ledger
        assert repository.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
            notification_id=notification_id) == read
        assert read_ledger['identity_org_operation_outbox'] == origin_ledger
        assert len(read_ledger['idempotency_records']) == 1
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox '
                'WHERE tenant_id=%s GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [
                    ('notification.created', 1), ('notification.read', 1)]
    finally:
        authorization_repository.close()


@pytest.mark.parametrize('revocation', ['identity', 'auth'])
def test_organization_delivered_notification_current_revocation_blocks_list_detail_and_read_replay(
    organization_notification_case, revocation,
):
    from daon_user_api.notification import NotificationError
    from daon_user_api.notification_postgres import PostgresNotificationRepository

    case = organization_notification_case
    scope, source_id, origin = _role_source(case)
    consumer = _consumer()
    _dispatch_origin(case, origin)
    assert consumer.materialize_pending_organization_roles(case.app, context=scope) == 1
    service, repository, authorization_repository, principal = _service(case, scope)
    try:
        items = repository.notifications_for(tenant_id=case.tenant, recipient_id=case.recipient)
        assert len(items) == 1
        item = items[0]
        arguments = dict(principal=principal, notification_id=item.notification_id, expected_etag=item.etag,
            idempotency_key='s42-org-revoked-read-0001', requested_state='read',
            trace_id=case.trace, policy_version=case.policy)
        assert service.mark_read(**arguments).version == 2
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            if revocation == 'identity':
                assert connection.execute('DELETE FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s RETURNING role',
                    (case.tenant, case.recipient)).fetchall() == [('member',)]
                assert connection.execute('SELECT role,state FROM public.identity_auth_memberships WHERE tenant_id=%s '
                    'AND workspace_id=%s AND user_id=%s', (case.tenant, case.workspace, case.recipient)).fetchone() == ('editor', 'active')
            else:
                assert connection.execute("UPDATE public.identity_auth_memberships SET state='suspended',version=version+1,updated_at=now() "
                    "WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s AND state='active' RETURNING role,state",
                    (case.tenant, case.workspace, case.recipient)).fetchall() == [('editor', 'suspended')]
                assert connection.execute('SELECT role FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s',
                    (case.tenant, case.recipient)).fetchone() == ('member',)
        before = _ledger(case)
        current = _current_with_caller_restored(case, scope, source_id)
        assert isinstance(current, dict) and current.get('accessible') is False
        reopened = PostgresNotificationRepository(case.app, context=scope)
        assert reopened.notifications_for(tenant_id=case.tenant, recipient_id=case.recipient) == ()
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
            notification_id=item.notification_id) is None
        page = service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
            trace_id=case.trace, policy_version=case.policy)
        assert page.items == () and page.unread_count == 0
        with pytest.raises(NotificationError) as unavailable:
            service.get_notification(principal=principal, notification_id=item.notification_id,
                trace_id=case.trace, policy_version=case.policy)
        assert (unavailable.value.code, unavailable.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        with pytest.raises(NotificationError) as replay:
            service.mark_read(**arguments)
        assert (replay.value.code, replay.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        assert _ledger(case) == before
    finally:
        authorization_repository.close()
