"""S42 C9 실제 전이·중앙감사·알림 소비 시험. 실행·DDL은 main 소유다.

NotificationService의 principal은 합성 포트 입력이다. 실제 C9/PG/중앙 dispatcher를
사용하지만 로그인·HTTP·브라우저 또는 가변 부모 원장 전체의 보안 증거는 아니다.
"""
from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
import pytest

from daon_user_api.audit import AuditEventStore, PostgresSecurityAuditStore
from daon_user_api.authorization import AuthorizationService, Role
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.notification import NotificationError, NotificationService
from daon_user_api.notification_events import _account_notification_scope
from daon_user_api.notification_postgres import PostgresNotificationRepository, _time
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
from test_c9_role_notification_events import (
    _assert_changed_source,
    _change,
    _ledger,
    _service,
    organization_notification_case,
)


_CENTRAL_COLUMNS = ('event_id,occurred_at,actor_id,actor_type,tenant_id,workspace_id,action,'
    'target_type,target_id,outcome,trace_id,policy_version,before_value,after_value,metadata,safe_code')


def _consumer():
    module = importlib.import_module('daon_user_api.notification_c9_roles')
    assert callable(getattr(module, 'materialize_pending_c9_roles', None)), 'C9_ROLE_NOTIFICATION_CONSUMER_NOT_IMPLEMENTED'
    return module


def _snapshot(case):
    result = _ledger(case)
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        result['idempotency_records'] = connection.execute(
            'SELECT to_jsonb(x) FROM public.idempotency_records x WHERE tenant_id=%s ORDER BY to_jsonb(x)::text',
            (case.tenant,)).fetchall()
        result['central'] = connection.execute('SELECT ' + _CENTRAL_COLUMNS +
            ' FROM public.security_audit_events WHERE tenant_id=%s ORDER BY event_id', (case.tenant,)).fetchall()
    return result


@contextmanager
def _pending_role_source(case):
    service, repository, actor = _service(case)
    key = 's42-c9-consumer-role-0001'
    try:
        changed = _change(service, actor, case, key=key)
        assert (changed.role, changed.version, changed.acl_version, changed.replayed) == (Role.EDITOR, 2, 3, False)
        source_id = _assert_changed_source(case, key)
        with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
            operation = connection.execute('SELECT event_id,created_at,actor_id,tenant_id,workspace_id,user_id,'
                'old_role,new_role,old_version,new_version,acl_version,reason,outcome,delivered_at '
                'FROM public.identity_auth_admin_role_operations WHERE actor_id=%s AND idempotency_key=%s',
                (case.manager, key)).fetchone()
            assert operation is not None and operation['delivered_at'] is None
            assert connection.execute('SELECT count(*) AS count FROM public.security_audit_events '
                'WHERE tenant_id=%s AND event_id=%s', (case.tenant, operation['event_id'])).fetchone() == {'count': 0}
        yield SimpleNamespace(service=service, source_id=source_id, operation=operation,
            context=CloudAccessContext(case.tenant, case.workspace, case.recipient, 'view'))
    finally:
        repository.close()


def _target(case, role):
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        with _account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.recipient,
            workspace_id=case.workspace) as reader:
            names = ('app.tenant_id', 'app.workspace_id', 'app.actor_id', 'app.capability')
            before = [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names]
            row = reader.execute('SELECT public.current_c9_role_notification_target(%s)', (role.source_id,)).fetchone()
            assert [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names] == before
            assert row is not None and type(row[0]) is dict
            target = dict(row[0])
            if target == {'state': 'awaiting_origin'}:
                return target
            assert type(target.get('accessible')) is bool
            target['occurred_at'] = _time(target['occurred_at'])
            assert target == dict(accessible=target['accessible'], title='작업공간 역할 변경',
                summary='본인의 작업공간 역할이 변경되었습니다.', deep_link='/notifications',
                source_event_id=role.source_id, origin_event_id=role.operation['event_id'], target_type='membership',
                target_id=case.recipient, kind='membership', severity='info', trace_id='not-recorded',
                policy_version='not-recorded', occurred_at=_time(role.operation['created_at']),
                action='authorization.membership.changed')
            return target


def _dispatch_origin(case, role):
    # 원감사를 직접 INSERT/append하지 않는다. 기존 C9 dispatcher의 실제 저장을 확인한다.
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    role.service._audit_store = central
    try:
        role.service._dispatch_pending_audit()
        operation = role.operation
        with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
            rows = connection.execute('SELECT ' + _CENTRAL_COLUMNS +
                ' FROM public.security_audit_events WHERE tenant_id=%s AND event_id=%s',
                (case.tenant, operation['event_id'])).fetchall()
            assert rows == [dict(event_id=operation['event_id'], occurred_at=_time(operation['created_at']),
                actor_id=case.manager, actor_type='user', tenant_id=case.tenant, workspace_id=case.workspace,
                action='authorization.membership.changed', target_type='membership', target_id=case.recipient,
                outcome='succeeded', trace_id='not-recorded', policy_version='not-recorded',
                before_value={'role': operation['old_role'], 'version': operation['old_version']},
                after_value={'role': operation['new_role'], 'version': operation['new_version']},
                metadata={'reason_code': operation['reason'], 'outcome': operation['outcome'],
                    'acl_version': operation['acl_version']}, safe_code=operation['reason'])]
            current = connection.execute('SELECT event_id,created_at,actor_id,tenant_id,workspace_id,user_id,'
                'old_role,new_role,old_version,new_version,acl_version,reason,outcome,delivered_at '
                'FROM public.identity_auth_admin_role_operations WHERE event_id=%s', (operation['event_id'],)).fetchone()
            assert current is not None and current['delivered_at'] is not None
            assert {**current, 'delivered_at': None} == operation
        delivered = _snapshot(case)
        role.service._dispatch_pending_audit()
        assert _snapshot(case) == delivered
    finally:
        role.service._audit_store = None
        central.close()


def _notification_service(case, context):
    repository = PostgresNotificationRepository(case.app, context=context)
    authorization_repository = PostgresAuthorizationRepository(case.app)
    audit = AuditEventStore()
    principal = IdentityPrincipal(case.recipient, 's42-c9-consumer-session', 's42-c9-consumer-device', case.tenant)
    service = NotificationService(repository=repository, authorization_service=AuthorizationService(
        repository=authorization_repository, audit_store=audit, clock=lambda: datetime.now(timezone.utc)),
        audit_store=audit, clock=lambda: datetime.now(timezone.utc))
    return service, repository, authorization_repository, principal


def _assert_materialized(case, role):
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        rows = connection.execute('SELECT n.notification_id,n.created_at,d.source_store,d.origin_event_id,d.source_event_id,'
            'd.notebook_id,d.resource_type,d.resource_id,d.title,d.summary,d.kind,d.severity,d.deep_link,d.trace_id,d.policy_version,'
            's.state AS source_state,s.occurred_at AS source_occurred_at,s.delivered_at AS source_delivered_at,'
            "(a.draft->>'occurred_at')::timestamptz AS created_audit_at,"
            'n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin AS same_transaction '
            'FROM public.notifications n JOIN public.notification_details d ON d.tenant_id=n.tenant_id '
            'AND d.notification_id=n.notification_id JOIN public.notification_source_outbox s '
            'ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id AND s.recipient_id=n.recipient_id '
            'AND s.source_event_id=n.source_event_id JOIN public.notification_audit_outbox a '
            "ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id AND a.action='notification.created' "
            'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s',
            (case.tenant, case.workspace, case.recipient)).fetchall()
        assert len(rows) == 1
        row = rows[0]
        expected = dict(source_store='authorization', origin_event_id=role.operation['event_id'],
            source_event_id=role.source_id, notebook_id=None, resource_type='membership', resource_id=case.recipient,
            title='작업공간 역할 변경', summary='본인의 작업공간 역할이 변경되었습니다.', kind='membership', severity='info',
            deep_link='/notifications', trace_id='not-recorded', policy_version='not-recorded',
            source_state='delivered', source_occurred_at=_time(role.operation['created_at']))
        assert {name: row[name] for name in expected} == expected
        assert row['source_delivered_at'] is not None and row['created_at'] >= row['source_delivered_at']
        assert row['created_audit_at'] == row['created_at'] and row['same_transaction'] is True
        return row['notification_id']


def _list(service, principal, case):
    return service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
        trace_id=case.trace, policy_version=case.policy)


@pytest.mark.parametrize('boundary', ['sql', 'dto'])
def test_c9_consumption_failure_after_actual_insert_rolls_back_and_retries(
    organization_notification_case, monkeypatch, boundary,
):
    """실제 알림 저장 후 오류를 삼키거나 일부 ack만 commit하는 결함을 검출한다."""
    case = organization_notification_case
    with _pending_role_source(case) as role:
        _dispatch_origin(case, role)
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
            assert connection.execute('SELECT count(*) FROM public.notification_details '
                'WHERE tenant_id=%s AND notification_id=%s',
                (case.tenant, item.notification_id)).fetchone() == (1,)
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
                consumer.materialize_pending_c9_roles(case.app, context=role.context)
        assert (failed.value.code, failed.value.http_status) == (
            'DATABASE_UNAVAILABLE' if boundary == 'sql' else 'DATABASE_RESPONSE_INVALID', 503)
        assert phases == ['actual_detail_inserted']
        assert _snapshot(case) == before
        assert consumer.materialize_pending_c9_roles(case.app, context=role.context) == 1
        assert consumer.materialize_pending_c9_roles(case.app, context=role.context) == 0


def test_c9_role_notification_waits_for_central_then_materializes_reopens_and_reads(organization_notification_case):
    case = organization_notification_case
    with _pending_role_source(case) as role:
        before = _snapshot(case)
        # SQL 포트 부재는 이 실제 source 준비 이후 그대로 드러난다. 대역으로 통과시키지 않는다.
        assert _target(case, role) == {'state': 'awaiting_origin'}
        consumer = _consumer()
        assert consumer.materialize_pending_c9_roles(case.app, context=role.context) == 0
        assert _snapshot(case) == before
        _dispatch_origin(case, role)
        assert _target(case, role)['accessible'] is True
        origin_ledger = _snapshot(case)['identity_auth_admin_role_operations']
        assert consumer.materialize_pending_c9_roles(case.app, context=role.context) == 1
        notification_id = _assert_materialized(case, role)
        after = _snapshot(case)
        assert consumer.materialize_pending_c9_roles(case.app, context=role.context) == 0
        assert _snapshot(case) == after
        service, repository, authorization_repository, principal = _notification_service(case, role.context)
        try:
            page = _list(service, principal, case)
            assert len(page.items) == page.unread_count == 1
            item = page.items[0]
            assert (item.notification_id, item.source_event_id, item.resource_type, item.resource_id, item.deep_link) == (
                notification_id, role.source_id, 'membership', case.recipient, '/notifications')
            assert service.get_notification(principal=principal, notification_id=notification_id,
                trace_id=case.trace, policy_version=case.policy) == item
            reopened = PostgresNotificationRepository(case.app, context=role.context)
            assert reopened.notifications_for(tenant_id=case.tenant, recipient_id=case.recipient) == (item,)
            assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
                notification_id=notification_id) == item
            assert _snapshot(case) == after  # 목록·단건·새 저장소 인스턴스 조회는 쓰기0이다.
            arguments = dict(principal=principal, notification_id=notification_id, expected_etag=item.etag,
                idempotency_key='s42-c9-notification-read-0001', requested_state='read',
                trace_id=case.trace, policy_version=case.policy)
            read = service.mark_read(**arguments)
            assert read.version == 2 and read.read_at is not None
            read_ledger = _snapshot(case)
            assert service.mark_read(**arguments) == read
            assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
                notification_id=notification_id) == read
            assert _snapshot(case) == read_ledger
            assert read_ledger['identity_auth_admin_role_operations'] == origin_ledger
            assert len(read_ledger['idempotency_records']) == 1
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox '
                    'WHERE tenant_id=%s GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [
                        ('notification.created', 1), ('notification.read', 1)]
        finally:
            authorization_repository.close()


@pytest.mark.parametrize('revocation', ['identity', 'auth'])
def test_c9_delivered_notification_current_revocation_blocks_list_detail_and_read_replay(
    organization_notification_case, revocation,
):
    case = organization_notification_case
    with _pending_role_source(case) as role:
        assert _target(case, role) == {'state': 'awaiting_origin'}
        _dispatch_origin(case, role)
        assert _target(case, role)['accessible'] is True
        consumer = _consumer()
        assert consumer.materialize_pending_c9_roles(case.app, context=role.context) == 1
        notification_id = _assert_materialized(case, role)
        service, repository, authorization_repository, principal = _notification_service(case, role.context)
        try:
            items = repository.notifications_for(tenant_id=case.tenant, recipient_id=case.recipient)
            assert len(items) == 1 and items[0].notification_id == notification_id
            item = items[0]
            arguments = dict(principal=principal, notification_id=notification_id, expected_etag=item.etag,
                idempotency_key='s42-c9-revoked-read-0001', requested_state='read',
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
            before = _snapshot(case)
            assert _target(case, role)['accessible'] is False
            reopened = PostgresNotificationRepository(case.app, context=role.context)
            assert reopened.notifications_for(tenant_id=case.tenant, recipient_id=case.recipient) == ()
            assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
                notification_id=notification_id) is None
            page = _list(service, principal, case)
            assert page.items == () and page.unread_count == 0
            with pytest.raises(NotificationError) as unavailable:
                service.get_notification(principal=principal, notification_id=notification_id,
                    trace_id=case.trace, policy_version=case.policy)
            assert (unavailable.value.code, unavailable.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
            with pytest.raises(NotificationError) as replay:
                service.mark_read(**arguments)
            assert (replay.value.code, replay.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
            assert _snapshot(case) == before
        finally:
            authorization_repository.close()
