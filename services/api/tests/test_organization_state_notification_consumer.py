"""S42 조직 상태 전이→중앙 원감사→지속 알림의 실제 PG core 소비 시험.

소유 QA fixture와 실제 관리 서비스/dispatcher를 사용한다. NotificationService의
principal은 합성 포트 입력이며 로그인·HTTP·정식 조직 변경의 성공 증거가 아니다.
실행·DDL·자원 수명은 main 소유이며 원 operation/중앙감사/source를 직접 만들지 않는다.
"""
from __future__ import annotations

import hashlib
import json

import psycopg
from psycopg.rows import dict_row
import pytest

from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.authorization import Role
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.notification import NotificationError
from daon_user_api.notification_postgres import PostgresNotificationRepository, _time
from daon_user_api.organization_membership import OrganizationWorkflowError
from daon_user_api.postgres_adapters import PostgresOrganizationRepository
from test_organization_notification_consumer import _consumer, _current_with_caller_restored, _ledger, _service
from test_organization_notification_events import _operation, organization_notification_case
from test_organization_state_notification_events import _auth_rows, _current_view


def test_organization_state_reactivation_waits_for_real_central_origin_reads_and_resuspension_blocks_replay(
    organization_notification_case,
):
    """state 사건을 role 알림으로 바꾸거나 현재 조직 중지를 auth 활성만으로 우회하는 결함을 검출한다."""
    case = organization_notification_case
    auth_before = _auth_rows(case)
    assert _current_view(case) is Role.VIEWER
    stopped = _operation(case, 'organization.member.state', case.recipient,
        {'active': False, 'expected_version': 1, 'reason': 'S42_STATE_CONSUMER_SUSPEND'},
        's42-state-consumer-suspend-0001')
    assert stopped.replayed is False
    assert (stopped.value['state'], stopped.value['version'], stopped.value['access_role'],
        stopped.value['access_state'], stopped.value['access_version']) == ('suspended', 2, 'viewer', 'active', 1)
    assert _auth_rows(case) == auth_before
    with pytest.raises(OrganizationWorkflowError) as denied:
        _current_view(case)
    assert (denied.value.code, denied.value.status) == ('ORGANIZATION_MEMBERSHIP_SUSPENDED', 403)
    assert _ledger(case)['notification_source_outbox'] == []

    reason = 'S42_STATE_CONSUMER_REACTIVATE'
    active_payload = {'active': True, 'expected_version': 2, 'reason': reason}
    active_key = 's42-state-consumer-reactivate-0001'
    activated = _operation(case, 'organization.member.state', case.recipient, active_payload, active_key)
    assert activated.replayed is False
    assert (activated.value['tenant_id'], activated.value['user_id'], activated.value['role'],
        activated.value['state'], activated.value['version'], activated.value['join_workspace_id'],
        activated.value['access_role'], activated.value['access_state'], activated.value['access_version']) == (
            case.tenant, case.recipient, 'member', 'active', 3, case.workspace, 'viewer', 'active', 1)
    assert _auth_rows(case) == auth_before
    assert _current_view(case) is Role.VIEWER
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        operation = connection.execute('SELECT * FROM public.identity_org_operation_outbox '
            "WHERE tenant_id=%s AND actor_id=%s AND operation='organization.member.state' AND idempotency_key=%s",
            (case.tenant, case.manager, active_key)).fetchone()
        assert operation is not None and operation['delivered_at'] is None
        assert json.loads(operation['response_json']) == activated.value
        assert json.loads(operation['audit_json']) == dict(action='organization.membership.state_changed',
            workspace_id=case.workspace, before='suspended', after='active', reason=reason, version=3, auth_version=1)
        origin = operation['event_id']
        source_id = 'organization-event-' + hashlib.sha256('|'.join((
            case.tenant, case.workspace, case.recipient, '3', '1')).encode()).hexdigest()[:32]
        sources = connection.execute('SELECT * FROM public.notification_source_outbox WHERE tenant_id=%s',
            (case.tenant,)).fetchall()
        assert sources == [dict(tenant_id=case.tenant, workspace_id=case.workspace, source_event_id=source_id,
            recipient_id=case.recipient, source_store='organization', origin_event_id=origin,
            action='organization.membership.state_changed', target_type='membership', target_id=case.recipient,
            kind='membership', severity='info', notebook_id=None, trace_id=case.trace, policy_version=case.policy,
            occurred_at=operation['created_at'], state='pending', version=1, delivered_at=None)]
        assert connection.execute('SELECT o.xmin=s.xmin AND s.xmin=w.xmin AS atomic '
            'FROM public.identity_org_operation_outbox o JOIN public.notification_source_outbox s '
            'ON s.tenant_id=o.tenant_id AND s.origin_event_id=o.event_id '
            'JOIN public.identity_org_tenant_memberships w ON w.tenant_id=o.tenant_id AND w.user_id=o.target_id '
            'WHERE o.tenant_id=%s AND o.event_id=%s', (case.tenant, origin)).fetchall() == [{'atomic': True}]
        assert connection.execute('SELECT count(*) AS total FROM public.security_audit_events '
            'WHERE tenant_id=%s AND event_id=%s', (case.tenant, origin)).fetchone() == {'total': 0}
    before_replay = _ledger(case)
    replay = _operation(case, 'organization.member.state', case.recipient, active_payload, active_key)
    assert replay.replayed is True and replay.value == activated.value
    assert _ledger(case) == before_replay

    scope = CloudAccessContext(case.tenant, case.workspace, case.recipient, 'view')
    consumer = _consumer()
    assert _current_with_caller_restored(case, scope, source_id) == {'state': 'awaiting_origin'}
    assert consumer.materialize_pending_organization_roles(case.app, context=scope) == 0
    assert _ledger(case) == before_replay
    # 역할 전용 helper 대신 같은 실제 dispatcher의 상태 원감사를 정확한 action으로 검증한다.
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    dispatcher = None
    try:
        dispatcher = PostgresOrganizationRepository(case.app, audit_store=central, notification_inbox_enabled=True)
        dispatcher.dispatch_pending_management_audits(limit=100)
        with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
            events = connection.execute('SELECT event_id,actor_id,actor_type,tenant_id,workspace_id,action,target_type,'
                'target_id,outcome,trace_id,policy_version,before_value,after_value,metadata,occurred_at '
                'FROM public.security_audit_events WHERE tenant_id=%s AND event_id=%s',
                (case.tenant, origin)).fetchall()
            assert events == [dict(event_id=origin, actor_id=case.manager, actor_type='user', tenant_id=case.tenant,
                workspace_id=case.workspace, action='organization.membership.state_changed', target_type='membership',
                target_id=case.recipient, outcome='succeeded', trace_id=case.trace, policy_version=case.policy,
                before_value={'value': 'suspended'}, after_value={'value': 'active'},
                metadata={'reason': reason, 'version': 3, 'auth_version': 1}, occurred_at=operation['created_at'])]
            assert connection.execute('SELECT delivered_at IS NOT NULL AS delivered FROM public.identity_org_operation_outbox '
                'WHERE tenant_id=%s AND event_id=%s', (case.tenant, origin)).fetchone() == {'delivered': True}
    finally:
        if dispatcher is not None:
            dispatcher.close()
        central.close()

    target = _current_with_caller_restored(case, scope, source_id)
    assert target == dict(accessible=True, title='조직 소속 상태 변경', summary='본인의 조직 소속 상태가 변경되었습니다.',
        deep_link='/notifications', source_event_id=source_id, origin_event_id=origin, target_type='membership',
        target_id=case.recipient, kind='membership', severity='info', trace_id=case.trace, policy_version=case.policy,
        occurred_at=before_replay['notification_source_outbox'][0][0]['occurred_at'],
        action='organization.membership.state_changed')
    assert consumer.materialize_pending_organization_roles(case.app, context=scope) == 1
    materialized = _ledger(case)
    assert consumer.materialize_pending_organization_roles(case.app, context=scope) == 0
    assert _ledger(case) == materialized
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        rows = connection.execute('SELECT n.notification_id,n.created_at,d.source_store,d.origin_event_id,d.source_event_id,'
            'd.notebook_id,d.resource_type,d.resource_id,d.title,d.summary,d.kind,d.severity,d.deep_link,d.trace_id,d.policy_version,'
            's.state AS source_state,s.occurred_at AS source_time,s.delivered_at AS source_delivered_at,a.draft,'
            'n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin AS atomic '
            'FROM public.notifications n JOIN public.notification_details d '
            'ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN public.notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.recipient_id=n.recipient_id AND s.source_event_id=n.source_event_id '
            "JOIN public.notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id AND a.action='notification.created' "
            'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s',
            (case.tenant, case.workspace, case.recipient)).fetchall()
        assert len(rows) == 1
        row = rows[0]
        expected = dict(source_store='organization', origin_event_id=origin, source_event_id=source_id,
            notebook_id=None, resource_type='membership', resource_id=case.recipient, title='조직 소속 상태 변경',
            summary='본인의 조직 소속 상태가 변경되었습니다.', kind='membership', severity='info', deep_link='/notifications',
            trace_id=case.trace, policy_version=case.policy, source_state='delivered', source_time=operation['created_at'])
        assert {name: row[name] for name in expected} == expected
        assert row['atomic'] is True and row['created_at'] >= row['source_delivered_at']
        notification_id = row['notification_id']
        draft = row['draft']
        assert (draft['event_id'], draft['action'], draft['target_id'], draft['trace_id'], draft['policy_version'],
            draft['after'], draft['metadata'], _time(draft['occurred_at'])) == (
                'audit-notification-created-' + hashlib.md5(f'{case.tenant}|{notification_id}'.encode()).hexdigest(),
                'notification.created', notification_id, case.trace, case.policy,
                {'recipient_id': case.recipient, 'kind': 'membership', 'delivery_state': 'delivered'},
                {'source_event_id': source_id}, row['created_at'])

    service, repository, authorization_repository, principal = _service(case, scope)
    try:
        page = service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
            trace_id=case.trace, policy_version=case.policy)
        assert len(page.items) == page.unread_count == 1
        item = page.items[0]
        assert (item.notification_id, item.source_event_id, item.title, item.summary, item.resource_id) == (
            notification_id, source_id, '조직 소속 상태 변경', '본인의 조직 소속 상태가 변경되었습니다.', case.recipient)
        assert service.get_notification(principal=principal, notification_id=notification_id,
            trace_id=case.trace, policy_version=case.policy) == item
        reopened = PostgresNotificationRepository(case.app, context=scope)
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
            notification_id=notification_id) == item
        assert _ledger(case) == materialized
        arguments = dict(principal=principal, notification_id=notification_id, expected_etag=item.etag,
            idempotency_key='s42-state-consumer-read-0001', requested_state='read',
            trace_id=case.trace, policy_version=case.policy)
        read = service.mark_read(**arguments)
        assert read.version == 2 and read.read_at is not None
        read_ledger = _ledger(case)
        assert service.mark_read(**arguments) == read
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
            notification_id=notification_id) == read
        assert _ledger(case) == read_ledger
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox '
                'WHERE tenant_id=%s GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [
                    ('notification.created', 1), ('notification.read', 1)]

        resuspended = _operation(case, 'organization.member.state', case.recipient,
            {'active': False, 'expected_version': 3, 'reason': 'S42_STATE_CONSUMER_RESUSPEND'},
            's42-state-consumer-suspend-0002')
        assert resuspended.replayed is False
        assert (resuspended.value['state'], resuspended.value['version'], resuspended.value['access_role'],
            resuspended.value['access_state'], resuspended.value['access_version']) == ('suspended', 4, 'viewer', 'active', 1)
        assert _auth_rows(case) == auth_before
        with pytest.raises(OrganizationWorkflowError) as denied:
            _current_view(case)
        assert (denied.value.code, denied.value.status) == ('ORGANIZATION_MEMBERSHIP_SUSPENDED', 403)
        revoked = _ledger(case)
        assert revoked['notification_source_outbox'] == read_ledger['notification_source_outbox']
        assert _current_with_caller_restored(case, scope, source_id)['accessible'] is False
        hidden = service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
            trace_id=case.trace, policy_version=case.policy)
        assert hidden.items == () and hidden.unread_count == 0
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.recipient,
            notification_id=notification_id) is None
        with pytest.raises(NotificationError) as unavailable:
            service.get_notification(principal=principal, notification_id=notification_id,
                trace_id=case.trace, policy_version=case.policy)
        assert (unavailable.value.code, unavailable.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        with pytest.raises(NotificationError) as replay:
            service.mark_read(**arguments)
        assert (replay.value.code, replay.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        assert _ledger(case) == revoked
    finally:
        authorization_repository.close()


@pytest.mark.parametrize('corruption', ['wrong_state', 'wrong_version'])
def test_organization_state_wrong_central_draft_is_rejected_without_source_ack(
    organization_notification_case, monkeypatch, corruption,
):
    """새 QA 사건의 전송 draft만 오염시킨다. 실제 중앙 INSERT/dispatcher이며 불변 감사 수정은 없다."""
    from dataclasses import replace
    from daon_user_api.notification_events import _account_notification_scope

    case = organization_notification_case
    auth_before = _auth_rows(case)
    stopped = _operation(case, 'organization.member.state', case.recipient,
        {'active': False, 'expected_version': 1, 'reason': 'S42_STATE_NEGATIVE_STOP'},
        's42-state-negative-stop-0001')
    assert stopped.value['state'] == 'suspended' and stopped.value['version'] == 2
    reason = 'S42_STATE_NEGATIVE_REACTIVATE'
    active_key = 's42-state-negative-active-0001'
    activated = _operation(case, 'organization.member.state', case.recipient,
        {'active': True, 'expected_version': 2, 'reason': reason}, active_key)
    assert (activated.value['state'], activated.value['version'], activated.value['access_version']) == ('active', 3, 1)
    assert _auth_rows(case) == auth_before and _current_view(case) is Role.VIEWER
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        operation = connection.execute('SELECT * FROM public.identity_org_operation_outbox '
            "WHERE tenant_id=%s AND actor_id=%s AND operation='organization.member.state' AND idempotency_key=%s",
            (case.tenant, case.manager, active_key)).fetchone()
        assert operation is not None and operation['delivered_at'] is None
        assert json.loads(operation['response_json']) == activated.value
        assert json.loads(operation['audit_json']) == dict(action='organization.membership.state_changed',
            workspace_id=case.workspace, before='suspended', after='active', reason=reason, version=3, auth_version=1)
        sources = connection.execute('SELECT * FROM public.notification_source_outbox WHERE tenant_id=%s',
            (case.tenant,)).fetchall()
        assert len(sources) == 1
        source = sources[0]
        origin, source_id = operation['event_id'], source['source_event_id']
        assert (source['origin_event_id'], source['action'], source['recipient_id'], source['state'],
            source['version'], source['delivered_at']) == (
                origin, 'organization.membership.state_changed', case.recipient, 'pending', 1, None)
        assert connection.execute('SELECT count(*) AS total FROM public.security_audit_events '
            'WHERE tenant_id=%s AND event_id=%s', (case.tenant, origin)).fetchone() == {'total': 0}

    scope = CloudAccessContext(case.tenant, case.workspace, case.recipient, 'view')
    assert _current_with_caller_restored(case, scope, source_id) == {'state': 'awaiting_origin'}
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    dispatcher, mutated = None, []
    try:
        dispatcher = PostgresOrganizationRepository(case.app, audit_store=central, notification_inbox_enabled=True)
        actual_draft = dispatcher._management_audit_draft

        def corrupted_draft(row):
            draft = actual_draft(row)
            if draft.event_id != origin:
                return draft
            assert draft.action == 'organization.membership.state_changed'
            assert dict(draft.after) == {'value': 'active'}
            assert dict(draft.metadata) == {'reason': reason, 'version': 3, 'auth_version': 1}
            mutated.append(draft.event_id)
            if corruption == 'wrong_state':
                return replace(draft, after={'value': 'suspended'})
            return replace(draft, metadata={**draft.metadata, 'version': 4})

        # 해당 새 원사건의 전송 draft 생성만 바꾼다. append/dispatch/SQL/권한은 실제 구현이다.
        with monkeypatch.context() as patch:
            patch.setattr(dispatcher, '_management_audit_draft', corrupted_draft)
            dispatcher.dispatch_pending_management_audits(limit=100)
        assert mutated == [origin]
    finally:
        if dispatcher is not None:
            dispatcher.close()
        central.close()

    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        saved = connection.execute('SELECT * FROM public.security_audit_events WHERE tenant_id=%s AND event_id=%s',
            (case.tenant, origin)).fetchone()
        assert saved is not None
        assert (saved['actor_id'], saved['actor_type'], saved['tenant_id'], saved['workspace_id'], saved['action'],
            saved['target_type'], saved['target_id'], saved['outcome'], saved['trace_id'], saved['policy_version'],
            saved['before_value'], saved['occurred_at']) == (
                case.manager, 'user', case.tenant, case.workspace, 'organization.membership.state_changed',
                'membership', case.recipient, 'succeeded', case.trace, case.policy,
                {'value': 'suspended'}, operation['created_at'])
        assert saved['after_value'] == {'value': 'suspended' if corruption == 'wrong_state' else 'active'}
        assert saved['metadata'] == {'reason': reason, 'version': 4 if corruption == 'wrong_version' else 3, 'auth_version': 1}
        delivered = connection.execute('SELECT * FROM public.identity_org_operation_outbox '
            'WHERE tenant_id=%s AND event_id=%s', (case.tenant, origin)).fetchone()
        # 원감사 전달 ack는 실제 수행됐다. 아래 ack0은 알림 source 소비 ack를 뜻한다.
        assert delivered['delivered_at'] is not None
        assert {key: value for key, value in delivered.items() if key != 'delivered_at'} == {
            key: value for key, value in operation.items() if key != 'delivered_at'}
    before = _ledger(case)
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        with _account_notification_scope(connection, tenant_id=case.tenant, workspace_id=case.workspace,
            actor_id=case.recipient) as reader:
            reader.execute("SELECT set_config('daon.organization_actor',%s,true)", ('s42-state-original-reader',))
            reader.execute("SELECT set_config('daon.organization_tenant',%s,true)", ('s42-state-original-tenant',))
            names = ('app.tenant_id', 'app.workspace_id', 'app.actor_id', 'app.capability',
                'daon.organization_actor', 'daon.organization_tenant')
            expected_scope = tuple(reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0]
                for name in names)
            with pytest.raises(psycopg.Error) as invalid:
                with connection.transaction():
                    reader.execute('SELECT public.current_organization_role_notification_target(%s)', (source_id,)).fetchone()
            assert invalid.value.sqlstate == '23514'
            assert invalid.value.diag.message_primary == 'NOTIFICATION_ORIGIN_INVALID'
            assert tuple(reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0]
                for name in names) == expected_scope
    consumer = _consumer()
    with pytest.raises(NotificationError) as rejected:
        consumer.materialize_pending_organization_roles(case.app, context=scope)
    assert (rejected.value.code, rejected.value.http_status) == ('DATABASE_UNAVAILABLE', 503)
    assert _ledger(case) == before
    assert len(before['notification_source_outbox']) == 1
    pending = before['notification_source_outbox'][0][0]
    assert (pending['state'], pending['version'], pending['delivered_at']) == ('pending', 1, None)
    assert before['notifications'] == before['notification_details'] == before['notification_audit_outbox'] == []
    assert _auth_rows(case) == auth_before and _current_view(case) is Role.VIEWER
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        assert connection.execute('SELECT * FROM public.notification_source_outbox WHERE tenant_id=%s',
            (case.tenant,)).fetchall() == [source]
        assert connection.execute('SELECT * FROM public.security_audit_events WHERE tenant_id=%s AND event_id=%s',
            (case.tenant, origin)).fetchone() == saved
