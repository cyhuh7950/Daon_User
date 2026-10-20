"""S42 실제 Workspace 정책 source의 소비·현재 조회 시험. 실행·DDL은 main 소유다.

정책/원감사/source는 실제 서비스로 저장한다. NotificationService principal은 합성
포트 입력이며 로그인·HTTP·브라우저·정식 환경 성공이나 조직 scope 완료의 증거가 아니다.
"""
from __future__ import annotations

import hashlib
import importlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.notification import NotificationError, NotificationService
from daon_user_api.notification_events import _account_notification_scope
from daon_user_api.notification_postgres import PostgresNotificationRepository, _time
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
from test_egress_policy_notification_events import (
    _activate,
    _assert_stored_change_and_sources,
    _policy_case,
    _snapshot,
    organization_notification_case,
)


def _consumer():
    module = importlib.import_module('daon_user_api.notification_egress_policy')
    assert callable(getattr(module, 'materialize_pending_workspace_policies', None)), 'EGRESS_POLICY_NOTIFICATION_CONSUMER_NOT_IMPLEMENTED'
    return module


def _new_policy_sources(case, policy):
    stored = _activate(policy, 's42-egress-consumer-version2-0001')
    origin = _assert_stored_change_and_sources(case, policy, stored, 's42-egress-consumer-version2-0001')
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        audit = connection.execute('SELECT event_id,occurred_at,actor_id,workspace_id,action,target_type,target_id,'
            'trace_id,policy_version,metadata FROM public.audit_events WHERE tenant_id=%s AND event_id=%s',
            (case.tenant, origin)).fetchone()
        assert audit is not None
        assert (audit['actor_id'], audit['workspace_id'], audit['action'], audit['target_type'], audit['target_id'],
            audit['trace_id'], audit['policy_version'], audit['metadata']) == (
                case.manager, case.workspace, 'egress_policy.activate', 'EgressPolicyBinding', stored.binding_id,
                case.trace, case.policy, {'scope_type': 'workspace', 'policy_version_id': stored.policy_version_id,
                    'binding_version': 2})
    source_ids = {user: 'egress-policy-event-' + hashlib.sha256(
        f'{origin}|{case.workspace}|{user}'.encode()).hexdigest()[:32] for user in (case.manager, case.recipient)}
    return SimpleNamespace(stored=stored, audit=audit, source_ids=source_ids)


def _context(case, user):
    return CloudAccessContext(case.tenant, case.workspace, user, 'view')


def _target(case, original, user):
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        with _account_notification_scope(connection, tenant_id=case.tenant, workspace_id=case.workspace,
            actor_id=user) as reader:
            names = ('app.tenant_id', 'app.workspace_id', 'app.actor_id', 'app.capability')
            before = [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names]
            row = reader.execute('SELECT public.current_egress_policy_notification_target(%s)',
                (original.source_ids[user],)).fetchone()
            assert [reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names] == before
            assert row is not None and type(row[0]) is dict
            target = dict(row[0])
            # 원감사는 이미 source와 함께 확정됐다. 대기 형식을 정상 응답으로 허용하지 않는다.
            assert type(target.get('accessible')) is bool
            target['occurred_at'] = _time(target['occurred_at'])
            assert target == dict(accessible=target['accessible'], title='외부 전송 정책 변경',
                summary='작업공간의 외부 전송 정책이 변경되었습니다.', deep_link='/notifications',
                source_event_id=original.source_ids[user], origin_event_id=original.audit['event_id'],
                target_type='EgressPolicyBinding', target_id=original.stored.binding_id, kind='policy', severity='info',
                trace_id=case.trace, policy_version=case.policy, occurred_at=_time(original.audit['occurred_at']),
                action='egress_policy.activate')
            return target


def _business(case):
    snapshot = _snapshot(case)
    result = {name: value for name, value in snapshot.items() if name not in {
        'notifications', 'notification_details', 'notification_source_outbox', 'notification_audit_outbox',
        'idempotency_records'}}
    result['policy_receipts'] = [row for row in snapshot['idempotency_records']
        if row[0]['operation'] == 'egress_policy.workspace.activate']
    return result


def _notification_service(case, user):
    context = _context(case, user)
    repository = PostgresNotificationRepository(case.app, context=context)
    authorization_repository = PostgresAuthorizationRepository(case.app)
    audit = AuditEventStore()
    principal = IdentityPrincipal(user, 's42-egress-consumer-session', 's42-egress-consumer-device', case.tenant)
    service = NotificationService(repository=repository, authorization_service=AuthorizationService(
        repository=authorization_repository, audit_store=audit, clock=lambda: datetime.now(timezone.utc)),
        audit_store=audit, clock=lambda: datetime.now(timezone.utc))
    return service, repository, authorization_repository, principal


def _assert_materialized(case, original, user):
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
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
            (case.tenant, case.workspace, user, original.source_ids[user])).fetchall()
        assert len(rows) == 1
        row = rows[0]
        expected_id = 'notification-' + hashlib.sha256(
            f'{case.tenant}|{case.workspace}|{user}|{original.source_ids[user]}'.encode()).hexdigest()[:32]
        expected = dict(notification_id=expected_id, recipient_id=user, source_store='authorization',
            origin_event_id=original.audit['event_id'], source_event_id=original.source_ids[user], notebook_id=None,
            resource_type='EgressPolicyBinding', resource_id=original.stored.binding_id, title='외부 전송 정책 변경',
            summary='작업공간의 외부 전송 정책이 변경되었습니다.', kind='policy', severity='info', deep_link='/notifications',
            trace_id=case.trace, policy_version=case.policy, source_state='delivered',
            source_occurred_at=_time(original.audit['occurred_at']))
        assert {name: row[name] for name in expected} == expected
        assert row['source_delivered_at'] is not None and row['created_at'] >= row['source_delivered_at']
        assert row['same_transaction'] is True
        draft = row['draft']
        assert (draft['event_id'], draft['action'], draft['target_type'], draft['target_id'], draft['trace_id'],
            draft['policy_version'], draft['after'], draft['metadata'], _time(draft['occurred_at'])) == (
                'audit-notification-created-' + hashlib.md5(f'{case.tenant}|{expected_id}'.encode()).hexdigest(),
                'notification.created', 'notification', expected_id, case.trace, case.policy,
                {'recipient_id': user, 'kind': 'policy', 'delivery_state': 'delivered'},
                {'source_event_id': original.source_ids[user]}, row['created_at'])
        return expected_id


def _list(service, principal, case):
    return service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
        trace_id=case.trace, policy_version=case.policy)


@pytest.mark.parametrize('boundary', ['sql', 'dto'])
def test_workspace_policy_consumption_post_insert_failure_rolls_back_and_retries(
    organization_notification_case, monkeypatch, boundary,
):
    """실제 알림 저장 뒤 일부 ack만 확정하거나 잘못된 감사 DTO를 수용하는 결함을 검출한다."""
    case = organization_notification_case
    with _policy_case(case) as policy:
        _new_policy_sources(case, policy)
        consumer = _consumer()
        context = _context(case, case.recipient)
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
                consumer.materialize_pending_workspace_policies(case.app, context=context)
        assert (failed.value.code, failed.value.http_status) == (
            'DATABASE_UNAVAILABLE' if boundary == 'sql' else 'DATABASE_RESPONSE_INVALID', 503)
        assert phases == ['actual_detail_inserted']
        assert _snapshot(case) == before
        assert consumer.materialize_pending_workspace_policies(case.app, context=context) == 1
        assert consumer.materialize_pending_workspace_policies(case.app, context=context) == 0


def test_workspace_policy_notifications_materialize_for_each_viewer_reopen_read_and_keep_original_binding(
    organization_notification_case,
):
    case = organization_notification_case
    with _policy_case(case) as policy:
        original = _new_policy_sources(case, policy)
        consumer = _consumer()  # 실제 정책·원감사·두 source 준비 이후에만 신규 포트 부재를 판정한다.
        business = _business(case)
        saved_items = {}
        targets = {}
        for user in (case.manager, case.recipient):
            targets[user] = _target(case, original, user)
            assert targets[user]['accessible'] is True
            assert consumer.materialize_pending_workspace_policies(case.app, context=_context(case, user)) == 1
            notification_id = _assert_materialized(case, original, user)
            delivered = _snapshot(case)
            assert consumer.materialize_pending_workspace_policies(case.app, context=_context(case, user)) == 0
            assert _snapshot(case) == delivered and _business(case) == business
            service, repository, authorization_repository, principal = _notification_service(case, user)
            try:
                page = _list(service, principal, case)
                assert len(page.items) == page.unread_count == 1
                item = page.items[0]
                assert (item.notification_id, item.source_event_id, item.resource_type, item.resource_id, item.deep_link) == (
                    notification_id, original.source_ids[user], 'EgressPolicyBinding', original.stored.binding_id, '/notifications')
                assert service.get_notification(principal=principal, notification_id=notification_id,
                    trace_id=case.trace, policy_version=case.policy) == item
                reopened = PostgresNotificationRepository(case.app, context=_context(case, user))
                assert reopened.notifications_for(tenant_id=case.tenant, recipient_id=user) == (item,)
                assert reopened.notification_for(tenant_id=case.tenant, recipient_id=user, notification_id=notification_id) == item
                assert _snapshot(case) == delivered
                arguments = dict(principal=principal, notification_id=notification_id, expected_etag=item.etag,
                    idempotency_key='s42-egress-policy-read-0001', requested_state='read',
                    trace_id=case.trace, policy_version=case.policy)
                read = service.mark_read(**arguments)
                assert read.version == 2 and read.read_at is not None
                after_read = _snapshot(case)
                assert service.mark_read(**arguments) == read
                assert reopened.notification_for(tenant_id=case.tenant, recipient_id=user, notification_id=notification_id) == read
                assert _snapshot(case) == after_read and _business(case) == business
                saved_items[user] = read
            finally:
                authorization_repository.close()
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox '
                'WHERE tenant_id=%s GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [
                    ('notification.created', 2), ('notification.read', 2)]

        # 실제 서비스가 다음 deny 정책 버전으로 교체한다. 과거 source/알림의 대상을 최신 binding으로 바꾸면 안 된다.
        old_rows = _snapshot(case)
        next_policy = SimpleNamespace(service=policy.service, context=policy.context, payload=policy.payload, current=original.stored)
        latest = _activate(next_policy, 's42-egress-consumer-version3-0001')
        assert latest.policy_version == latest.binding_version == 3 and latest.binding_id != original.stored.binding_id
        assert policy.repository.current(policy.context, 'workspace') == latest
        replacement = _snapshot(case)
        assert replacement['notifications'] == old_rows['notifications']
        assert replacement['notification_details'] == old_rows['notification_details']
        assert replacement['notification_audit_outbox'] == old_rows['notification_audit_outbox']
        for table in ('egress_policy_versions', 'audit_events', 'idempotency_records'):
            assert all(row in replacement[table] for row in old_rows[table])
        old_sources = [row for row in replacement['notification_source_outbox']
            if row[0]['origin_event_id'] == original.audit['event_id']]
        assert old_sources == old_rows['notification_source_outbox']
        assert len(replacement['notification_source_outbox']) == 4
        for user in (case.manager, case.recipient):
            assert _target(case, original, user) == targets[user]
            reopened = PostgresNotificationRepository(case.app, context=_context(case, user))
            assert reopened.notification_for(tenant_id=case.tenant, recipient_id=user,
                notification_id=saved_items[user].notification_id) == saved_items[user]
        assert _snapshot(case) == replacement


@pytest.mark.parametrize('revocation', ['identity', 'auth'])
def test_workspace_policy_notification_current_revocation_blocks_list_detail_and_read_replay(
    organization_notification_case, revocation,
):
    case = organization_notification_case
    with _policy_case(case) as policy:
        original = _new_policy_sources(case, policy)
        consumer = _consumer()
        for user in (case.manager, case.recipient):
            assert _target(case, original, user)['accessible'] is True
            assert consumer.materialize_pending_workspace_policies(case.app, context=_context(case, user)) == 1
            _assert_materialized(case, original, user)
        service, repository, authorization_repository, principal = _notification_service(case, case.recipient)
        try:
            items = repository.notifications_for(tenant_id=case.tenant, recipient_id=case.recipient)
            assert len(items) == 1 and items[0].source_event_id == original.source_ids[case.recipient]
            item = items[0]
            arguments = dict(principal=principal, notification_id=item.notification_id, expected_etag=item.etag,
                idempotency_key='s42-egress-revoked-read-0001', requested_state='read',
                trace_id=case.trace, policy_version=case.policy)
            assert service.mark_read(**arguments).version == 2
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                if revocation == 'identity':
                    assert connection.execute('DELETE FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s RETURNING role',
                        (case.tenant, case.recipient)).fetchall() == [('member',)]
                    assert connection.execute('SELECT role,state FROM public.identity_auth_memberships WHERE tenant_id=%s '
                        'AND workspace_id=%s AND user_id=%s', (case.tenant, case.workspace, case.recipient)).fetchone() == ('viewer', 'active')
                else:
                    assert connection.execute("UPDATE public.identity_auth_memberships SET state='suspended',version=version+1,updated_at=now() "
                        "WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s AND state='active' RETURNING role,state",
                        (case.tenant, case.workspace, case.recipient)).fetchall() == [('viewer', 'suspended')]
                    assert connection.execute('SELECT role FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s',
                        (case.tenant, case.recipient)).fetchone() == ('member',)
            before = _snapshot(case)
            assert _target(case, original, case.recipient)['accessible'] is False
            assert _target(case, original, case.manager)['accessible'] is True
            reopened = PostgresNotificationRepository(case.app, context=_context(case, case.recipient))
            assert reopened.notifications_for(tenant_id=case.tenant, recipient_id=case.recipient) == ()
            assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
                notification_id=item.notification_id) is None
            page = _list(service, principal, case)
            assert page.items == () and page.unread_count == 0
            with pytest.raises(NotificationError) as unavailable:
                service.get_notification(principal=principal, notification_id=item.notification_id,
                    trace_id=case.trace, policy_version=case.policy)
            assert (unavailable.value.code, unavailable.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
            with pytest.raises(NotificationError) as replay:
                service.mark_read(**arguments)
            assert (replay.value.code, replay.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
            manager_repository = PostgresNotificationRepository(case.app, context=_context(case, case.manager))
            assert len(manager_repository.notifications_for(tenant_id=case.tenant, recipient_id=case.manager)) == 1
            assert _snapshot(case) == before
        finally:
            authorization_repository.close()
