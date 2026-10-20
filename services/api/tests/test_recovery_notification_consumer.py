"""S42 기존 restore.completed source의 소비·현재 조회 경계. 실행과 SQL은 main 소유다.

원업무/원감사/source는 실제 복구 서비스를 거친다. storage만 외부 I/O 없는 대역이며
NotificationService principal은 합성 포트 입력이다. 실제 로그인/HTTP/외부 복구 증거가 아니다.
"""
from __future__ import annotations

import hashlib
import importlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import Action, AuthorizationService, Role
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.notification import NotificationError, NotificationService
from daon_user_api.notification_events import _account_notification_scope
from daon_user_api.notification_postgres import PostgresNotificationRepository, _time
from daon_user_api.open_law_source_access import OpenLawAccessError, _current_auth_grant
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository

from test_recovery_notification_events import (
    _assert_completed_origin,
    _assert_completed_source,
    _assert_destination_metadata,
    _execute,
    _snapshot as _recovery_snapshot,
    _sources,
    recovery_notification_case,
)


def _consumer():
    module = importlib.import_module('daon_user_api.notification_recovery')
    assert callable(getattr(module, 'materialize_pending_restore_completed', None)), 'RESTORE_COMPLETED_NOTIFICATION_CONSUMER_NOT_IMPLEMENTED'
    return module


def _new_source(case):
    result = _execute(case)
    source_id = _assert_completed_source(case, result, case.context)
    root = _assert_completed_origin(case, result, case.context)
    _assert_destination_metadata(case)
    return SimpleNamespace(result=result, root=root, source_id=source_id,
        context=CloudAccessContext(case.context.tenant_id, case.context.workspace_id, case.context.actor_id, 'view'))


def _snapshot(case):
    snapshot = _recovery_snapshot(case)
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        for table in ('notifications', 'notification_details', 'notification_audit_outbox'):
            snapshot[table] = connection.execute(f'SELECT to_jsonb(x) FROM public.{table} x '
                'WHERE tenant_id=%s ORDER BY to_jsonb(x)::text', (case.context.tenant_id,)).fetchall()
        snapshot['notification_receipts'] = connection.execute('SELECT to_jsonb(x) FROM public.idempotency_records x '
            'WHERE tenant_id=%s AND workspace_id=%s AND operation=%s ORDER BY to_jsonb(x)::text',
            (case.context.tenant_id, case.context.workspace_id, 'notification.read')).fetchall()
    return snapshot


def _business(case):
    return {name: value for name, value in _recovery_snapshot(case).items()
        if name not in {'sources', 'notification_counts'}}


def _target(case, original):
    ctx = case.context
    with psycopg.connect(case.scope.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        with _account_notification_scope(connection, tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id,
            actor_id=ctx.actor_id) as reader:
            names = ('app.tenant_id', 'app.workspace_id', 'app.actor_id', 'app.capability', 'app.notebook_id')
            before = [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names]
            row = reader.execute('SELECT public.current_restore_completed_notification_target(%s)',
                (original.source_id,)).fetchone()
            assert [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names] == before
            assert row is not None and len(row) == 1 and type(row[0]) is dict
            target = dict(row[0])
            assert type(target.get('accessible')) is bool
            target['occurred_at'] = _time(target['occurred_at'])
            # 원감사는 최종 업무/source와 이미 저장됐다. 별도 전달 대기 응답은 허용하지 않는다.
            assert target == dict(accessible=target['accessible'], title='복구 완료',
                summary='요청한 복구 작업이 완료되었습니다.',
                deep_link='/operations?restore_request_id=' + original.result.request_id,
                source_event_id=original.source_id, origin_event_id=original.root['event_id'],
                target_type='restore_request', target_id=original.result.request_id, kind='recovery', severity='info',
                trace_id=original.root['trace_id'], policy_version=original.root['policy_version'],
                occurred_at=_time(original.root['occurred_at']), action='restore.completed')
            return target


@contextmanager
def _service(case, original):
    repository = PostgresNotificationRepository(case.scope.app, context=original.context)
    authorization_repository = PostgresAuthorizationRepository(case.scope.app)
    try:
        audit = AuditEventStore()
        principal = IdentityPrincipal(case.context.actor_id, 's42-restore-consumer-port-session',
            's42-restore-consumer-port-device', case.context.tenant_id)
        service = NotificationService(repository=repository, authorization_service=AuthorizationService(
            repository=authorization_repository, audit_store=audit, clock=lambda: datetime.now(timezone.utc)),
            audit_store=audit, clock=lambda: datetime.now(timezone.utc))
        yield service, repository, principal
    finally:
        authorization_repository.close()


def _list(case, service, principal):
    return service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
        trace_id=case.context.trace_id, policy_version=case.context.policy_version)


def _read_arguments(case, item, principal):
    return dict(principal=principal, notification_id=item.notification_id, expected_etag=item.etag,
        idempotency_key='s42-restore-notification-read-0001', requested_state='read',
        trace_id=case.context.trace_id, policy_version=case.context.policy_version)


def _assert_materialized(case, original):
    ctx = case.context
    notification_id = 'notification-' + hashlib.sha256(
        f'{ctx.tenant_id}|{ctx.workspace_id}|{ctx.actor_id}|{original.source_id}'.encode()).hexdigest()[:32]
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        rows = connection.execute('SELECT n.notification_id,n.created_at,n.recipient_id,d.source_store,d.origin_event_id,'
            'd.source_event_id,d.notebook_id,d.resource_type,d.resource_id,d.title,d.summary,d.kind,d.severity,d.deep_link,'
            'd.trace_id,d.policy_version,d.delivery_state,d.delivered_at,s.state AS source_state,'
            's.occurred_at AS source_occurred_at,s.delivered_at AS source_delivered_at,s.version AS source_version,'
            'a.event_id AS audit_event_id,a.draft,n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin AS same_transaction '
            'FROM public.notifications n JOIN public.notification_details d ON d.tenant_id=n.tenant_id '
            'AND d.notification_id=n.notification_id JOIN public.notification_source_outbox s '
            'ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id AND s.recipient_id=n.recipient_id '
            'AND s.source_event_id=n.source_event_id JOIN public.notification_audit_outbox a '
            "ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id AND a.action='notification.created' "
            'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s AND n.source_event_id=%s',
            (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, original.source_id)).fetchall()
        assert len(rows) == 1
        row = rows[0]
        expected = dict(notification_id=notification_id, recipient_id=ctx.actor_id, source_store='recovery',
            origin_event_id=original.root['event_id'], source_event_id=original.source_id, notebook_id=None,
            resource_type='restore_request', resource_id=original.result.request_id, title='복구 완료',
            summary='요청한 복구 작업이 완료되었습니다.', kind='recovery', severity='info',
            deep_link='/operations?restore_request_id=' + original.result.request_id,
            trace_id=original.root['trace_id'], policy_version=original.root['policy_version'],
            delivery_state='delivered', source_state='delivered', source_version=2,
            source_occurred_at=_time(original.root['occurred_at']))
        assert {name: row[name] for name in expected} == expected
        assert row['same_transaction'] is True
        assert row['delivered_at'] is not None and row['created_at'] >= row['delivered_at']
        assert row['source_delivered_at'] == row['delivered_at']
        draft = row['draft']
        expected_audit_id = 'audit-notification-created-' + hashlib.md5(
            f'{ctx.tenant_id}|{notification_id}'.encode()).hexdigest()
        assert row['audit_event_id'] == expected_audit_id
        assert _time(draft['occurred_at']) == row['created_at']
        assert draft == dict(event_id=expected_audit_id, occurred_at=draft['occurred_at'],
            actor_id='notification-service', actor_type='service', tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id,
            action='notification.created', target_type='notification', target_id=notification_id, outcome='succeeded',
            trace_id=original.root['trace_id'], policy_version=original.root['policy_version'], before=None,
            after={'recipient_id': ctx.actor_id, 'kind': 'recovery', 'delivery_state': 'delivered'},
            metadata={'source_event_id': original.source_id})
    return notification_id


def test_restore_completed_materializes_reopens_and_marks_read_once(recovery_notification_case):
    case = recovery_notification_case
    original = _new_source(case)
    consumer = _consumer()  # 실제 원업무·완료감사·verification·receipt·source 확인 뒤에만 부재 RED로 판정한다.
    business = _business(case)
    assert _target(case, original)['accessible'] is True
    assert consumer.materialize_pending_restore_completed(case.scope.app, context=original.context, limit=100) == 1
    notification_id = _assert_materialized(case, original)
    delivered = _snapshot(case)
    assert consumer.materialize_pending_restore_completed(case.scope.app, context=original.context) == 0
    assert _snapshot(case) == delivered and _business(case) == business
    with _service(case, original) as (service, repository, principal):
        page = _list(case, service, principal)
        assert len(page.items) == page.unread_count == 1
        item = page.items[0]
        assert (item.notification_id, item.source_event_id, item.resource_type, item.resource_id, item.deep_link) == (
            notification_id, original.source_id, 'restore_request', original.result.request_id,
            '/operations?restore_request_id=' + original.result.request_id)
        assert service.get_notification(principal=principal, notification_id=notification_id,
            trace_id=case.context.trace_id, policy_version=case.context.policy_version) == item
        assert repository.notification_for(tenant_id=case.context.tenant_id, recipient_id=case.context.actor_id,
            notification_id=notification_id) == item
        assert _snapshot(case) == delivered
        arguments = _read_arguments(case, item, principal)
        read = service.mark_read(**arguments)
        assert read.version == 2 and read.read_at is not None
        after_read = _snapshot(case)
        assert service.mark_read(**arguments) == read and _snapshot(case) == after_read
    with _service(case, original) as (service, reopened, principal):
        assert reopened.notifications_for(tenant_id=case.context.tenant_id, recipient_id=case.context.actor_id) == (read,)
        assert service.get_notification(principal=principal, notification_id=notification_id,
            trace_id=case.context.trace_id, policy_version=case.context.policy_version) == read
        assert service.mark_read(**dict(arguments, principal=principal)) == read
        assert _snapshot(case) == after_read and _business(case) == business
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox '
            'WHERE tenant_id=%s AND notification_id=%s GROUP BY action ORDER BY action',
            (case.context.tenant_id, notification_id)).fetchall() == [('notification.created', 1), ('notification.read', 1)]


@pytest.mark.parametrize('boundary', ['policy_view_only', 'identity', 'auth'])
def test_restore_completed_current_revocation_hides_list_detail_and_read_replay(recovery_notification_case, boundary):
    case = recovery_notification_case
    ctx = case.context
    original = _new_source(case)
    consumer = _consumer()
    assert consumer.materialize_pending_restore_completed(case.scope.app, context=original.context) == 1
    notification_id = _assert_materialized(case, original)
    with _service(case, original) as (service, repository, principal):
        items = repository.notifications_for(tenant_id=ctx.tenant_id, recipient_id=ctx.actor_id)
        assert len(items) == 1
        arguments = _read_arguments(case, items[0], principal)
        assert service.mark_read(**arguments).version == 2
        with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
            if boundary == 'identity':
                assert connection.execute('DELETE FROM public.identity_memberships WHERE tenant_id=%s '
                    'AND user_id=%s RETURNING role', (ctx.tenant_id, ctx.actor_id)).fetchall() == [('personal_owner',)]
            else:
                assert connection.execute("UPDATE public.identity_auth_tenant_roles SET state='inactive',"
                    "version=version+1,updated_at=now() WHERE tenant_id=%s AND user_id=%s AND state='active' "
                    'RETURNING role,state', (ctx.tenant_id, ctx.actor_id)).fetchall() == [('personal_owner', 'inactive')]
                if boundary == 'policy_view_only':
                    # 소유 QA 본인의 권한 축소다. identity 소속은 보존하고 기존 auth viewer 경로만 사용한다.
                    connection.execute('INSERT INTO public.identity_auth_memberships '
                        '(tenant_id,workspace_id,user_id,role,state,version,updated_at) '
                        "VALUES(%s,%s,%s,'viewer','active',1,now())", (ctx.tenant_id, ctx.workspace_id, ctx.actor_id))
        if boundary == 'policy_view_only':
            with psycopg.connect(case.scope.app, connect_timeout=3) as connection:
                with _account_notification_scope(connection, tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id,
                    actor_id=ctx.actor_id) as reader:
                    assert _current_auth_grant(original.context, reader, Action.VIEW).role is Role.VIEWER
                    with pytest.raises(OpenLawAccessError, match='^OPEN_LAW_ACCESS_DENIED$'):
                        _current_auth_grant(original.context, reader, Action.POLICY_MANAGE)
        before = _snapshot(case)
        assert _target(case, original)['accessible'] is False
        fresh = PostgresNotificationRepository(case.scope.app, context=original.context)
        assert fresh.notifications_for(tenant_id=ctx.tenant_id, recipient_id=ctx.actor_id) == ()
        assert fresh.notification_for(tenant_id=ctx.tenant_id, recipient_id=ctx.actor_id, notification_id=notification_id) is None
        page = _list(case, service, principal)
        assert page.items == () and page.unread_count == 0
        with pytest.raises(NotificationError) as detail:
            service.get_notification(principal=principal, notification_id=notification_id,
                trace_id=ctx.trace_id, policy_version=ctx.policy_version)
        assert (detail.value.code, detail.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        with pytest.raises(NotificationError) as replay:
            service.mark_read(**arguments)
        assert (replay.value.code, replay.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        assert _snapshot(case) == before


def test_restore_completed_receipt_expiry_cleanup_does_not_erase_durable_notification_target(recovery_notification_case):
    case = recovery_notification_case
    ctx = case.context
    original = _new_source(case)
    consumer = _consumer()
    before = _snapshot(case)
    # 기존 일반 receipt의 UPDATE/DELETE 권한과 범위 RLS만 사용한다. trigger/불변 보호를 끄지 않는다.
    with psycopg.connect(case.scope.app, connect_timeout=3) as connection:
        with _account_notification_scope(connection, tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id,
            actor_id=ctx.actor_id) as reader:
            assert reader.execute("SELECT has_table_privilege(current_user,'public.idempotency_records','UPDATE'),"
                "has_table_privilege(current_user,'public.idempotency_records','DELETE')").fetchone() == (True, True)
            parameters = (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, case.execute_key, original.result.request_id)
            assert reader.execute("UPDATE public.idempotency_records SET expires_at=clock_timestamp()-interval '1 day' "
                "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND operation='restore-execute' "
                "AND idempotency_key=%s AND result->>'request_id'=%s AND status='completed' RETURNING result",
                parameters).fetchall() == [({'request_id': original.result.request_id},)]
            assert reader.execute('DELETE FROM public.idempotency_records WHERE tenant_id=%s AND workspace_id=%s '
                "AND actor_id=%s AND operation='restore-execute' AND idempotency_key=%s "
                "AND result->>'request_id'=%s AND expires_at<clock_timestamp() RETURNING result",
                parameters).fetchall() == [({'request_id': original.result.request_id},)]
    cleaned = _snapshot(case)
    assert cleaned['operations'] == [] and len(before['operations']) == 1
    for name in before.keys() - {'operations'}:
        assert cleaned[name] == before[name]
    business = _business(case)
    assert _target(case, original)['accessible'] is True
    assert consumer.materialize_pending_restore_completed(case.scope.app, context=original.context) == 1
    notification_id = _assert_materialized(case, original)
    delivered = _snapshot(case)
    with _service(case, original) as (service, repository, principal):
        page = _list(case, service, principal)
        assert len(page.items) == 1 and page.items[0].notification_id == notification_id
        item = service.get_notification(principal=principal, notification_id=notification_id,
            trace_id=ctx.trace_id, policy_version=ctx.policy_version)
        assert repository.notification_for(tenant_id=ctx.tenant_id, recipient_id=ctx.actor_id, notification_id=notification_id) == item
        assert _snapshot(case) == delivered
        arguments = _read_arguments(case, item, principal)
        read = service.mark_read(**arguments)
        after_read = _snapshot(case)
        assert read.version == 2 and service.mark_read(**arguments) == read
        assert _snapshot(case) == after_read
    assert _sources(case.scope)[0]['origin_event_id'] == original.root['event_id']
    assert _business(case) == business


@pytest.mark.parametrize('boundary', ['sql', 'dto'])
def test_restore_completed_consumption_post_insert_failure_rolls_back_and_retries(
    recovery_notification_case, monkeypatch, boundary,
):
    case = recovery_notification_case
    original = _new_source(case)
    consumer = _consumer()
    before = _snapshot(case)
    real_connect, real_validate = consumer.connect, consumer._validate_created_audit
    connections, phases = [], []

    @contextmanager
    def observed_connect(*args, **kwargs):
        with real_connect(*args, **kwargs) as connection:
            connections.append(connection)
            yield connection

    def fail_after_actual_insert(payload, item, target):
        connection = connections[-1]
        assert connection.execute('SELECT count(*) FROM public.notification_details WHERE tenant_id=%s '
            'AND notification_id=%s', (case.context.tenant_id, item.notification_id)).fetchone() == (1,)
        phases.append('actual_detail_inserted')
        if boundary == 'dto':
            return real_validate({**payload, 'metadata': {
                **payload['metadata'], 'unexpected': 's42-negative-case'}}, item, target)
        real_validate(payload, item, target)
        connection.execute('SELECT 1/0')

    with monkeypatch.context() as patch:
        patch.setattr(consumer, 'connect', observed_connect)
        patch.setattr(consumer, '_validate_created_audit', fail_after_actual_insert)
        with pytest.raises(NotificationError) as failed:
            consumer.materialize_pending_restore_completed(case.scope.app, context=original.context)
    assert (failed.value.code, failed.value.http_status) == (
        'DATABASE_UNAVAILABLE' if boundary == 'sql' else 'DATABASE_RESPONSE_INVALID', 503)
    assert phases == ['actual_detail_inserted']
    assert _snapshot(case) == before and _target(case, original)['accessible'] is True
    assert consumer.materialize_pending_restore_completed(case.scope.app, context=original.context) == 1
    _assert_materialized(case, original)
    delivered = _snapshot(case)
    assert consumer.materialize_pending_restore_completed(case.scope.app, context=original.context) == 0
    assert _snapshot(case) == delivered
