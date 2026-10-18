"""S42 raw SourceACL 알림 소비·현재 접근 시험. 실제 실행/DDL은 main 소유다.

기존 실제 문서 저장 fixture와 ACL setter/중앙감사를 사용한다. principal은 합성
서비스 포트 입력이며 Provider/Object·로그인/HTTP·법령/웹·정식 환경의 증거가 아니다.
"""
from __future__ import annotations

import hashlib
import importlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.notification import NotificationError, NotificationService
from daon_user_api.notification_events import _account_notification_scope, _account_view_allowed
from daon_user_api.notification_postgres import PostgresNotificationRepository, _time
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
from test_notification_events import _studio_case_member
from test_source_access_notification_events import (
    _business_snapshot,
    _grant,
    _primary_audits,
    source_access_notification_case,
)


_ACTION = 'authorization.source_access.changed'


def _consumer():
    module = importlib.import_module('daon_user_api.notification_source_access')
    assert callable(getattr(module, 'materialize_pending_source_access', None)), 'SOURCE_ACCESS_NOTIFICATION_CONSUMER_NOT_IMPLEMENTED'
    return module


def _context(case, recipient):
    return CloudAccessContext(case.context.tenant_id, case.context.workspace_id, recipient, 'view')


def _new_source(case):
    ctx = case.context
    # 원감사 actor와 수신자가 다른 실제 공유 사용자를 만든다. 같은 callback의 user 결속을 확인한다.
    recipient = _studio_case_member(case.scope, role='viewer')
    roots_before = {row['event_id'] for row in _primary_audits(case)}
    assert _grant(case, recipient_id=recipient) == 1
    roots = [row for row in _primary_audits(case) if row['event_id'] not in roots_before]
    assert len(roots) == 1
    root = roots[0]
    expected_root = dict(tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id, actor_id=ctx.actor_id,
        actor_type='user', action=_ACTION, target_type='source_access', target_id=case.version_id,
        outcome='succeeded', trace_id=ctx.trace_id, policy_version=ctx.policy_version, before_value=None,
        after_value={'allowed': True, 'version': 1}, metadata={})
    assert {name: root[name] for name in expected_root} == expected_root
    source_id = 'source-acl-event-' + hashlib.sha256(
        f"{root['event_id']}|{ctx.workspace_id}|{recipient}".encode()).hexdigest()[:32]
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        rows = connection.execute('SELECT source_event_id,origin_event_id,recipient_id,source_store,action,target_type,target_id,'
            'kind,severity,notebook_id,trace_id,policy_version,occurred_at,state,version,delivered_at '
            'FROM public.notification_source_outbox WHERE tenant_id=%s AND workspace_id=%s AND action=%s '
            'AND target_id=%s ORDER BY source_event_id', (ctx.tenant_id, ctx.workspace_id, _ACTION, case.version_id)).fetchall()
        assert rows == [dict(source_event_id=source_id, origin_event_id=root['event_id'], recipient_id=recipient,
            source_store='authorization', action=_ACTION, target_type='source_access', target_id=case.version_id,
            kind='membership', severity='info', notebook_id=None, trace_id=ctx.trace_id, policy_version=ctx.policy_version,
            occurred_at=_time(root['occurred_at']), state='pending', version=1, delivered_at=None)]
        assert connection.execute('SELECT allowed,version FROM public.identity_auth_source_access WHERE tenant_id=%s '
            'AND workspace_id=%s AND user_id=%s AND source_version_id=%s',
            (ctx.tenant_id, ctx.workspace_id, recipient, case.version_id)).fetchone() == {'allowed': 1, 'version': 1}
    return SimpleNamespace(recipient=recipient, source_id=source_id, root=root, context=_context(case, recipient))


def _snapshot(case):
    ctx = case.context
    result = _business_snapshot(case)
    result['central'] = _primary_audits(case)
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        for table in ('idempotency_records', 'identity_memberships', 'identity_auth_memberships',
            'identity_auth_tenant_roles', 'user_accounts'):
            result[table] = connection.execute(f'SELECT to_jsonb(x) FROM public.{table} x '
                'WHERE tenant_id=%s ORDER BY to_jsonb(x)::text', (ctx.tenant_id,)).fetchall()
        result['identity_users'] = connection.execute('SELECT user_id,state FROM public.identity_users '
            'WHERE user_id IN (SELECT user_id FROM public.user_accounts WHERE tenant_id=%s) ORDER BY user_id',
            (ctx.tenant_id,)).fetchall()
    return result


def _business(case):
    return {name: value for name, value in _snapshot(case).items() if name not in {
        'notifications', 'notification_details', 'notification_source_outbox', 'notification_audit_outbox',
        'idempotency_records'}}


def _target(case, original):
    ctx = case.context
    with psycopg.connect(case.scope.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        with _account_notification_scope(connection, tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id,
            actor_id=original.recipient) as reader:
            names = ('app.tenant_id', 'app.workspace_id', 'app.actor_id', 'app.capability')
            before = [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names]
            row = reader.execute('SELECT public.current_source_access_notification_target(%s)',
                (original.source_id,)).fetchone()
            assert [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names] == before
            assert row is not None and type(row[0]) is dict
            target = dict(row[0])
            assert type(target.get('accessible')) is bool
            target['occurred_at'] = _time(target['occurred_at'])
            assert target == dict(accessible=target['accessible'], title='자료 접근권한 변경',
                summary='본인의 자료 접근권한이 변경되었습니다.', deep_link='/notifications',
                source_event_id=original.source_id, origin_event_id=original.root['event_id'], target_type='source_access',
                target_id=case.version_id, kind='membership', severity='info', trace_id=ctx.trace_id,
                policy_version=ctx.policy_version, occurred_at=_time(original.root['occurred_at']), action=_ACTION)
            return target


def _notification_service(case, original):
    ctx = case.context
    repository = PostgresNotificationRepository(case.scope.app, context=original.context)
    authorization_repository = PostgresAuthorizationRepository(case.scope.app)
    audit = AuditEventStore()
    principal = IdentityPrincipal(original.recipient, 's42-acl-consumer-session', 's42-acl-consumer-device', ctx.tenant_id)
    service = NotificationService(repository=repository, authorization_service=AuthorizationService(
        repository=authorization_repository, audit_store=audit, clock=lambda: datetime.now(timezone.utc)),
        audit_store=audit, clock=lambda: datetime.now(timezone.utc))
    return service, repository, authorization_repository, principal


def _list(service, principal, case):
    return service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
        trace_id=case.context.trace_id, policy_version=case.context.policy_version)


def _read_arguments(case, original, item, principal):
    return dict(principal=principal, notification_id=item.notification_id, expected_etag=item.etag,
        idempotency_key='s42-source-access-read-0001', requested_state='read',
        trace_id=case.context.trace_id, policy_version=case.context.policy_version)


def _assert_materialized(case, original):
    ctx = case.context
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        rows = connection.execute('SELECT n.notification_id,n.created_at,n.recipient_id,d.source_store,d.origin_event_id,'
            'd.source_event_id,d.notebook_id,d.resource_type,d.resource_id,d.title,d.summary,d.kind,d.severity,d.deep_link,'
            'd.trace_id,d.policy_version,s.state AS source_state,s.occurred_at AS source_occurred_at,'
            's.delivered_at AS source_delivered_at,a.draft,n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin AS same_transaction '
            'FROM public.notifications n JOIN public.notification_details d ON d.tenant_id=n.tenant_id '
            'AND d.notification_id=n.notification_id JOIN public.notification_source_outbox s '
            'ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id AND s.recipient_id=n.recipient_id '
            'AND s.source_event_id=n.source_event_id JOIN public.notification_audit_outbox a '
            "ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id AND a.action='notification.created' "
            'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s AND n.source_event_id=%s',
            (ctx.tenant_id, ctx.workspace_id, original.recipient, original.source_id)).fetchall()
        assert len(rows) == 1
        row = rows[0]
        expected_id = 'notification-' + hashlib.sha256(
            f'{ctx.tenant_id}|{ctx.workspace_id}|{original.recipient}|{original.source_id}'.encode()).hexdigest()[:32]
        expected = dict(notification_id=expected_id, recipient_id=original.recipient, source_store='authorization',
            origin_event_id=original.root['event_id'], source_event_id=original.source_id, notebook_id=None,
            resource_type='source_access', resource_id=case.version_id, title='자료 접근권한 변경',
            summary='본인의 자료 접근권한이 변경되었습니다.', kind='membership', severity='info', deep_link='/notifications',
            trace_id=ctx.trace_id, policy_version=ctx.policy_version, source_state='delivered',
            source_occurred_at=_time(original.root['occurred_at']))
        assert {name: row[name] for name in expected} == expected
        assert row['source_delivered_at'] is not None and row['created_at'] >= row['source_delivered_at']
        assert row['same_transaction'] is True
        draft = row['draft']
        assert (_time(draft['occurred_at']), draft['action'], draft['target_type'], draft['target_id'],
            draft['trace_id'], draft['policy_version'], draft['after'], draft['metadata']) == (
                row['created_at'], 'notification.created', 'notification', expected_id, ctx.trace_id, ctx.policy_version,
                {'recipient_id': original.recipient, 'kind': 'membership', 'delivery_state': 'delivered'},
                {'source_event_id': original.source_id})
        return expected_id


def test_source_access_notification_materializes_reopens_reads_and_keeps_v1_origin_after_new_grant(
    source_access_notification_case,
):
    case = source_access_notification_case
    ctx = case.context
    original = _new_source(case)
    consumer = _consumer()  # 실제 ACL/중앙감사/source까지 준비한 뒤 신규 포트 부재를 판정한다.
    target = _target(case, original)
    assert target['accessible'] is True
    business = _business(case)
    assert consumer.materialize_pending_source_access(case.scope.app, context=original.context) == 1
    notification_id = _assert_materialized(case, original)
    delivered = _snapshot(case)
    assert consumer.materialize_pending_source_access(case.scope.app, context=original.context) == 0
    assert _snapshot(case) == delivered and _business(case) == business
    service, repository, authorization_repository, principal = _notification_service(case, original)
    try:
        page = _list(service, principal, case)
        assert len(page.items) == page.unread_count == 1
        item = page.items[0]
        assert (item.notification_id, item.source_event_id, item.resource_type, item.resource_id, item.deep_link) == (
            notification_id, original.source_id, 'source_access', case.version_id, '/notifications')
        assert service.get_notification(principal=principal, notification_id=notification_id,
            trace_id=ctx.trace_id, policy_version=ctx.policy_version) == item
        reopened = PostgresNotificationRepository(case.scope.app, context=original.context)
        assert reopened.notifications_for(tenant_id=ctx.tenant_id, recipient_id=original.recipient) == (item,)
        assert reopened.notification_for(tenant_id=ctx.tenant_id, recipient_id=original.recipient,
            notification_id=notification_id) == item
        assert _snapshot(case) == delivered
        arguments = _read_arguments(case, original, item, principal)
        read = service.mark_read(**arguments)
        assert read.version == 2 and read.read_at is not None
        after_read = _snapshot(case)
        assert service.mark_read(**arguments) == read
        assert _snapshot(case) == after_read and _business(case) == business
        assert len(after_read['idempotency_records']) == len(delivered['idempotency_records']) + 1
        with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox '
                'WHERE tenant_id=%s AND notification_id=%s GROUP BY action ORDER BY action',
                (ctx.tenant_id, notification_id)).fetchall() == [('notification.created', 1), ('notification.read', 1)]
        assert _grant(case, expected_version=1, recipient_id=original.recipient) == 2
        roots = _primary_audits(case)
        assert original.root in roots and len(roots) == 2
        newest = next(root for root in roots if root['event_id'] != original.root['event_id'])
        assert newest['before_value'] == {'allowed': True, 'version': 1}
        assert newest['after_value'] == {'allowed': True, 'version': 2}
        after_grant = _snapshot(case)
        assert [row for row in after_grant['notification_source_outbox']
            if row[0]['source_event_id'] == original.source_id] == [row for row in after_read['notification_source_outbox']
                if row[0]['source_event_id'] == original.source_id]
        assert after_grant['notifications'] == after_read['notifications']
        assert after_grant['notification_details'] == after_read['notification_details']
        assert after_grant['notification_audit_outbox'] == after_read['notification_audit_outbox']
        assert _target(case, original) == target
        assert service.get_notification(principal=principal, notification_id=notification_id,
            trace_id=ctx.trace_id, policy_version=ctx.policy_version) == read
        assert reopened.notification_for(tenant_id=ctx.tenant_id, recipient_id=original.recipient,
            notification_id=notification_id) == read
        assert service.mark_read(**arguments) == read
        assert _snapshot(case) == after_grant
    finally:
        authorization_repository.close()


@pytest.mark.parametrize('boundary', ['acl_deny', 'source_deleted', 'identity', 'auth'])
def test_source_access_notification_current_denial_hides_list_detail_and_read_replay(
    source_access_notification_case, boundary,
):
    case = source_access_notification_case
    ctx = case.context
    original = _new_source(case)
    consumer = _consumer()
    assert consumer.materialize_pending_source_access(case.scope.app, context=original.context) == 1
    notification_id = _assert_materialized(case, original)
    service, repository, authorization_repository, principal = _notification_service(case, original)
    try:
        items = repository.notifications_for(tenant_id=ctx.tenant_id, recipient_id=original.recipient)
        assert len(items) == 1 and items[0].notification_id == notification_id
        arguments = _read_arguments(case, original, items[0], principal)
        assert service.mark_read(**arguments).version == 2
        if boundary == 'acl_deny':
            assert _grant(case, expected_version=1, recipient_id=original.recipient, allowed=False) == 2
            with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
                assert connection.execute('SELECT allowed,version FROM public.identity_auth_source_access '
                    'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s AND source_version_id=%s',
                    (ctx.tenant_id, ctx.workspace_id, original.recipient, case.version_id)).fetchone() == (0, 2)
        elif boundary == 'source_deleted':
            # 기존 Canon의 실제 상태 전이만 사용한다. guard 해제/강제 UPDATE/물리 purge는 하지 않는다.
            with case.store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'source.write')) as connection:
                state, version = connection.execute('SELECT state,version FROM public.sources WHERE tenant_id=%s '
                    'AND workspace_id=%s AND record_id=%s', (ctx.tenant_id, ctx.workspace_id, case.source_id)).fetchone()
                assert state == 'ready'
                for target_state in ('disabled', 'deleting', 'deleted'):
                    version = Canon._transition(connection, ctx, 'Source', case.source_id, version, target_state,
                        's42-acl-consumer-delete-' + target_state + '-' + uuid4().hex)
                assert connection.execute('SELECT state FROM public.sources WHERE tenant_id=%s AND workspace_id=%s '
                    'AND record_id=%s', (ctx.tenant_id, ctx.workspace_id, case.source_id)).fetchone() == ('deleted',)
        else:
            with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
                assert connection.execute('SELECT count(*) FROM public.identity_auth_tenant_roles WHERE tenant_id=%s '
                    'AND user_id=%s', (ctx.tenant_id, original.recipient)).fetchone() == (0,)
                if boundary == 'identity':
                    assert connection.execute('DELETE FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s RETURNING role',
                        (ctx.tenant_id, original.recipient)).fetchall() == [('member',)]
                    assert connection.execute('SELECT role,state FROM public.identity_auth_memberships WHERE tenant_id=%s '
                        'AND workspace_id=%s AND user_id=%s', (ctx.tenant_id, ctx.workspace_id, original.recipient)).fetchone() == ('viewer', 'active')
                else:
                    assert connection.execute("UPDATE public.identity_auth_memberships SET state='suspended',version=version+1,updated_at=now() "
                        "WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s AND state='active' RETURNING role,state",
                        (ctx.tenant_id, ctx.workspace_id, original.recipient)).fetchall() == [('viewer', 'suspended')]
                    assert connection.execute('SELECT role FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s',
                        (ctx.tenant_id, original.recipient)).fetchone() == ('member',)
        before = _snapshot(case)
        assert _target(case, original)['accessible'] is False
        reopened = PostgresNotificationRepository(case.scope.app, context=original.context)
        assert reopened.notifications_for(tenant_id=ctx.tenant_id, recipient_id=original.recipient) == ()
        assert reopened.notification_for(tenant_id=ctx.tenant_id, recipient_id=original.recipient,
            notification_id=notification_id) is None
        page = _list(service, principal, case)
        assert page.items == () and page.unread_count == 0
        with pytest.raises(NotificationError) as unavailable:
            service.get_notification(principal=principal, notification_id=notification_id,
                trace_id=ctx.trace_id, policy_version=ctx.policy_version)
        assert (unavailable.value.code, unavailable.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        with pytest.raises(NotificationError) as replay:
            service.mark_read(**arguments)
        assert (replay.value.code, replay.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        assert _snapshot(case) == before
        with case.store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'view')) as connection:
            assert _account_view_allowed(connection, CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'view'))
    finally:
        authorization_repository.close()


@pytest.mark.parametrize('boundary', ['sql', 'dto'])
def test_source_access_consumption_post_insert_failure_rolls_back_and_retries(
    source_access_notification_case, monkeypatch, boundary,
):
    case = source_access_notification_case
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
            consumer.materialize_pending_source_access(case.scope.app, context=original.context)
    assert (failed.value.code, failed.value.http_status) == (
        'DATABASE_UNAVAILABLE' if boundary == 'sql' else 'DATABASE_RESPONSE_INVALID', 503)
    assert phases == ['actual_detail_inserted']
    assert _snapshot(case) == before
    assert consumer.materialize_pending_source_access(case.scope.app, context=original.context) == 1
    _assert_materialized(case, original)
    retried = _snapshot(case)
    assert consumer.materialize_pending_source_access(case.scope.app, context=original.context) == 0
    assert _snapshot(case) == retried
