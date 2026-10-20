"""S42 계정 source의 실제 PG 소비·현재 조회·읽음. HTTP/Provider/실기기 증거가 아니다."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import psycopg
import pytest

from test_account_notification_events import account_notification_case, _account_operation


@pytest.mark.parametrize('operation,title,summary,kind,severity', [
    ('device.trust', '기기 신뢰 설정', '본인 기기의 신뢰 설정이 변경되었습니다.', 'device', 'info'),
    ('device.revoke', '기기 철회', '본인 기기가 철회되었습니다.', 'device', 'warning'),
    ('session.revoke', '세션 철회', '본인 세션이 철회되었습니다.', 'authentication', 'warning'),
])
def test_account_source_materializes_reopens_and_marks_read_without_duplicate(
    account_notification_case, operation, title, summary, kind, severity,
):
    """실제 주사건→동일 Tx 소비→새 PG repo 현재 조회→실제 권한 서비스 읽음을 검증한다."""
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.authorization import AuthorizationService
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification import NotificationService
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    import daon_user_api.notification_events as events

    case = account_notification_case
    mutate, arguments, target = _account_operation(case, operation)
    assert mutate(**arguments)['replayed'] is False
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        original = connection.execute('SELECT event_id,audit_events_json,created_at FROM public.identity_account_requests '
            'WHERE tenant_id=%s AND actor_id=%s AND operation=%s', (case.tenant, case.actor, operation)).fetchall()
        assert len(original) == 1
        parent_event, audit_json, occurred_at = original[0]
        expected_action = {'device.trust': 'identity.device.trusted', 'device.revoke': 'identity.device.revoked',
            'session.revoke': 'identity.session.revoked'}[operation]
        primary = [(i, e) for i, e in enumerate(json.loads(audit_json)) if e['action'] == expected_action]
        assert len(primary) == 1 and primary[0][1]['target_id'] == target
        source = connection.execute('SELECT source_event_id,origin_event_id,state,notebook_id FROM public.notification_source_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s AND recipient_id=%s', (case.tenant, case.workspace, case.actor)).fetchall()
        assert len(source) == 1 and source[0][1:] == (parent_event+'-'+str(primary[0][0]), 'pending', None)
        source_id = source[0][0]
        assert connection.execute('SELECT count(*) FROM public.notifications WHERE tenant_id=%s', (case.tenant,)).fetchone() == (0,)

    scope = CloudAccessContext(case.tenant, case.workspace, case.actor, 'view')
    assert events.materialize_pending_accounts(case.app, context=scope, limit=100) == 1
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        actual = connection.execute('SELECT n.notification_id,n.created_at,d.title,d.summary,d.kind,d.severity,d.deep_link,'
            'd.notebook_id,d.resource_type,d.resource_id,s.state,s.occurred_at,s.delivered_at,'
            "(a.draft->>'occurred_at')::timestamptz,n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin "
            'FROM public.notifications n JOIN public.notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
            'JOIN public.notification_source_outbox s ON s.tenant_id=n.tenant_id AND s.workspace_id=n.workspace_id '
            'AND s.recipient_id=n.recipient_id AND s.source_event_id=n.source_event_id '
            'JOIN public.notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id '
            "AND a.action='notification.created' WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s",
            (case.tenant, case.workspace, case.actor)).fetchall()
        assert len(actual) == 1
        notification_id, created = actual[0][:2]
        assert actual[0][2:12] == (title, summary, kind, severity, '/settings/account', None,
            primary[0][1]['target_type'], target, 'delivered', occurred_at)
        assert created >= actual[0][12] and actual[0][13] == created and actual[0][14] is True
        immutable = connection.execute('SELECT to_jsonb(r) FROM public.identity_account_requests r WHERE event_id=%s', (parent_event,)).fetchone()
        def notification_rows():
            return {table: connection.execute(f'SELECT to_jsonb(x) FROM public.{table} x WHERE tenant_id=%s ORDER BY to_jsonb(x)::text',
                (case.tenant,)).fetchall() for table in ('notifications', 'notification_details', 'notification_source_outbox', 'notification_audit_outbox')}
        before = notification_rows()
    assert events.materialize_pending_accounts(case.app, context=scope, limit=100) == 0

    # 새 객체/connection에서 읽는다. 별도 process나 HTTP 성공으로 보고하지 않는다.
    repository = PostgresNotificationRepository(case.app, context=scope)
    item = repository.notification_for(tenant_id=case.tenant, recipient_id=case.actor, notification_id=notification_id)
    assert item is not None and (item.title, item.summary, item.deep_link, item.resource_id) == (title, summary, '/settings/account', target)
    authorization_repository = PostgresAuthorizationRepository(case.app)
    audit = AuditEventStore()
    try:
        authorization = AuthorizationService(repository=authorization_repository, audit_store=audit,
            clock=lambda: datetime.now(timezone.utc), identity_service=case.identity)
        service = NotificationService(repository=repository, authorization_service=authorization, audit_store=audit,
            clock=lambda: datetime.now(timezone.utc))
        principal = case.identity.validate_access(case.credentials.access_token, trace_id=case.trace, policy_version=case.policy)
        page = service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
            trace_id=case.trace, policy_version=case.policy)
        assert page.items == (item,) and page.unread_count == 1
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert notification_rows() == before
        read_arguments = dict(principal=principal, notification_id=notification_id, expected_etag=item.etag,
            idempotency_key='s42-account-read-once', requested_state='read', trace_id=case.trace, policy_version=case.policy)
        read = service.mark_read(**read_arguments)
        assert read.version == 2 and read.read_at is not None
        assert service.mark_read(**read_arguments) == read
        assert repository.notification_for(tenant_id=case.tenant, recipient_id=case.actor, notification_id=notification_id) == read
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT to_jsonb(r) FROM public.identity_account_requests r WHERE event_id=%s', (parent_event,)).fetchone() == immutable
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox WHERE tenant_id=%s GROUP BY action ORDER BY action',
                (case.tenant,)).fetchall() == [('notification.created', 1), ('notification.read', 1)]
    finally:
        authorization_repository.close()


def _pending_account_boundary(case):
    """성공 원장을 합성하지 않고 실제 trust와 source를 준비한다."""
    from daon_user_api.cloud_storage import CloudAccessContext

    mutate, arguments, target = _account_operation(case, 'device.trust')
    assert mutate(**arguments)['replayed'] is False
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        sources = connection.execute('SELECT source_event_id,state,version,delivered_at '
            'FROM public.notification_source_outbox WHERE tenant_id=%s AND workspace_id=%s AND recipient_id=%s',
            (case.tenant, case.workspace, case.actor)).fetchall()
        assert len(sources) == 1 and sources[0][1:] == ('pending', 1, None)
        assert connection.execute('SELECT count(*) FROM public.identity_account_requests '
            "WHERE tenant_id=%s AND actor_id=%s AND operation='device.trust' AND target_id=%s",
            (case.tenant, case.actor, target)).fetchone() == (1,)
    return CloudAccessContext(case.tenant, case.workspace, case.actor, 'view'), sources[0][0]


def _account_boundary_ledger(case):
    """자격/세션 토큰 없이 해당 QA tenant의 원장·receipt·소비 전체를 비교한다."""
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        return {table: connection.execute(
            f'SELECT to_jsonb(x) FROM public.{table} x WHERE tenant_id=%s ORDER BY to_jsonb(x)::text',
            (case.tenant,)).fetchall() for table in (
                'identity_account_requests', 'notifications', 'notification_details',
                'notification_source_outbox', 'notification_audit_outbox', 'idempotency_records')}


def _revoke_account_membership(case):
    # 이 시험이 생성한 소속만 철회한다. 원업무/원감사/기존 소속의 불변행은 수정하지 않는다.
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        assert connection.execute('DELETE FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s '
            'RETURNING role', (case.tenant, case.actor)).fetchall() == [('personal_owner',)]
        assert connection.execute('SELECT role,state FROM public.identity_auth_tenant_roles '
            'WHERE tenant_id=%s AND user_id=%s', (case.tenant, case.actor)).fetchall() == [('personal_owner', 'active')]


@pytest.mark.parametrize('revocation', ['view', 'identity_membership'])
def test_account_pending_source_sql_true_cannot_bypass_current_revocation(account_notification_case, revocation):
    """원업무는 정상 commit, 소비 시점 현재 VIEW/소속 철회는 SQL 자체가 거부한다."""
    from daon_user_api.authorization import Action
    from daon_user_api.open_law_source_access import OpenLawAccessError, _current_auth_grant
    import daon_user_api.notification_events as events

    case = account_notification_case
    scope, source_id = _pending_account_boundary(case)
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        with events._account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor,
            workspace_id=case.workspace) as reader:
            assert _current_auth_grant(scope, reader, Action.VIEW) is not None
    if revocation == 'view':
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute("UPDATE public.identity_auth_tenant_roles SET state='inactive',version=version+1,updated_at=now() "
                "WHERE tenant_id=%s AND user_id=%s AND role='personal_owner' AND state='active' RETURNING role,state,version",
                (case.tenant, case.actor)).fetchall() == [('personal_owner', 'inactive', 2)]
            assert connection.execute('SELECT count(*) FROM public.identity_auth_memberships '
                'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s',
                (case.tenant, case.workspace, case.actor)).fetchone() == (0,)
    else:
        _revoke_account_membership(case)
    before = _account_boundary_ledger(case)
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        with events._account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor,
            workspace_id=case.workspace) as reader:
            if revocation == 'view':
                with pytest.raises(OpenLawAccessError):
                    _current_auth_grant(scope, reader, Action.VIEW)
            else:
                # auth 역할만 남아도 실제 identity 교집합 없이는 알림 허용이 아니다.
                assert _current_auth_grant(scope, reader, Action.VIEW) is not None
            assert reader.execute('SELECT public.materialize_account_notification(%s,%s)',
                (source_id, True)).fetchone() == ({'state': 'suppressed'},)
    after = _account_boundary_ledger(case)
    for table in before.keys() - {'notification_source_outbox'}:
        assert after[table] == before[table]
    assert after['notifications'] == after['notification_details'] == after['notification_audit_outbox'] == []
    old_source, new_source = before['notification_source_outbox'][0][0], after['notification_source_outbox'][0][0]
    assert (new_source['state'], new_source['version']) == ('suppressed', 2)
    assert new_source['delivered_at'] is not None
    changed = {'state', 'version', 'delivered_at'}
    assert {k: v for k, v in new_source.items() if k not in changed} == {
        k: v for k, v in old_source.items() if k not in changed}
    assert events.materialize_pending_accounts(case.app, context=scope) == 0
    assert _account_boundary_ledger(case) == after


def test_account_delivered_read_membership_revocation_hides_list_detail_and_read_replay(account_notification_case):
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.authorization import AuthorizationService
    from daon_user_api.notification import NotificationError, NotificationService
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    import daon_user_api.notification_events as events

    case = account_notification_case
    scope, _source_id = _pending_account_boundary(case)
    assert events.materialize_pending_accounts(case.app, context=scope) == 1
    repository = PostgresNotificationRepository(case.app, context=scope)
    items = repository.notifications_for(tenant_id=case.tenant, recipient_id=case.actor)
    assert len(items) == 1
    item = items[0]
    authorization_repository = PostgresAuthorizationRepository(case.app)
    audit = AuditEventStore()
    try:
        service = NotificationService(repository=repository,
            authorization_service=AuthorizationService(repository=authorization_repository, audit_store=audit,
                clock=lambda: datetime.now(timezone.utc), identity_service=case.identity),
            audit_store=audit, clock=lambda: datetime.now(timezone.utc))
        principal = case.identity.validate_access(case.credentials.access_token, trace_id=case.trace, policy_version=case.policy)
        read_arguments = dict(principal=principal, notification_id=item.notification_id, expected_etag=item.etag,
            idempotency_key='s42-account-revoked-read', requested_state='read', trace_id=case.trace, policy_version=case.policy)
        read = service.mark_read(**read_arguments)
        assert read.version == 2 and read.read_at is not None
        assert service.mark_read(**read_arguments) == read
        before_revoke = _account_boundary_ledger(case)
        assert len(before_revoke['idempotency_records']) == 1
        assert len(before_revoke['notification_audit_outbox']) == 2
        _revoke_account_membership(case)
        before_reads = _account_boundary_ledger(case)
        assert before_reads == before_revoke
        # 새 repository/connection도 예전 delivered/read 본문을 복원하지 않는다.
        reopened = PostgresNotificationRepository(case.app, context=scope)
        assert reopened.notifications_for(tenant_id=case.tenant, recipient_id=case.actor) == ()
        assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.actor,
            notification_id=item.notification_id) is None
        page = service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
            trace_id=case.trace, policy_version=case.policy)
        assert page.items == () and page.unread_count == 0
        with pytest.raises(NotificationError) as detail_error:
            service.get_notification(principal=principal, notification_id=item.notification_id,
                trace_id=case.trace, policy_version=case.policy)
        assert (detail_error.value.code, detail_error.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        with pytest.raises(NotificationError) as replay_error:
            service.mark_read(**read_arguments)
        assert (replay_error.value.code, replay_error.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        assert _account_boundary_ledger(case) == before_reads
    finally:
        authorization_repository.close()


@pytest.mark.parametrize('failure_phase', ['sql', 'dto'])
def test_account_materialize_post_insert_failure_rolls_back_all_consumption(account_notification_case, monkeypatch, failure_phase):
    """실제 detail/created 의도/ack 뒤의 SQL 또는 DTO 오류는 전체 Tx를 되돌린다."""
    from daon_user_api.notification import NotificationError
    import daon_user_api.notification_events as events
    import daon_user_api.notification_postgres as storage

    case = account_notification_case
    scope, source_id = _pending_account_boundary(case)
    before = _account_boundary_ledger(case)
    assert before['notifications'] == before['notification_details'] == before['notification_audit_outbox'] == []
    phases = []
    real_connect = psycopg.connect
    real_convert = storage.notification_from_row

    class ObservedCursor:
        def __init__(self, cursor, connection):
            self.cursor = cursor
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def execute(self, query, params=None):
            self.cursor.execute(query, params)
            if query == 'SELECT public.materialize_account_notification(%s,%s)':
                assert params == (source_id, True)
                # 앱이 SELECT 금지인 source/audit 표를 우회하지 않고 허용된 실제 조회 함수로 확인한다.
                rows = self.connection.execute('SELECT n.notification_id,n.xmin=d.xmin FROM public.notifications n '
                    'JOIN public.notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
                    'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s',
                    (case.tenant, case.workspace, case.actor)).fetchall()
                assert len(rows) == 1 and rows[0][1] is True
                audits = self.connection.execute('SELECT event_id,draft FROM public.pending_notification_audits(100)').fetchall()
                assert len(audits) == 1 and audits[0][1]['action'] == 'notification.created'
                assert audits[0][1]['target_id'] == rows[0][0]
                assert self.connection.execute('SELECT * FROM public.pending_account_notification_sources(100)').fetchall() == []
                phases.append('actual_detail_created_intent_and_ack')
                if failure_phase == 'sql':
                    try:
                        self.connection.execute('SELECT 1/0')
                    except psycopg.Error as error:
                        assert error.sqlstate == '22012'
                        phases.append('sql_22012')
                        raise
                    pytest.fail('ACCOUNT_CONSUMER_SQL_FAILURE_NOT_INJECTED', pytrace=False)
            return self

    class ObservedConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def cursor(self, *args, **kwargs):
            return ObservedCursor(self.connection.cursor(*args, **kwargs), self.connection)

    def observed_connect(*args, **kwargs):
        return ObservedConnection(real_connect(*args, **kwargs))

    def invalid_dto(row):
        item = real_convert(row)
        assert item.source_event_id == source_id and item.deep_link == '/settings/account'
        assert phases == ['actual_detail_created_intent_and_ack']
        phases.append('dto_validation')
        # 원 DB 행은 건드리지 않고 실제 DTO 변환기의 enum 검증 실패를 주입한다.
        return real_convert({**row, 'delivery_state': 's42-invalid-delivery-state'})

    with monkeypatch.context() as patch:
        patch.setattr(psycopg, 'connect', observed_connect)
        if failure_phase == 'dto':
            patch.setattr(storage, 'notification_from_row', invalid_dto)
        with pytest.raises(NotificationError) as failure:
            events.materialize_pending_accounts(case.app, context=scope)
    assert (failure.value.code, failure.value.http_status) == (
        'DATABASE_UNAVAILABLE' if failure_phase == 'sql' else 'DATABASE_RESPONSE_INVALID', 503)
    assert phases == ['actual_detail_created_intent_and_ack', 'sql_22012' if failure_phase == 'sql' else 'dto_validation']
    assert _account_boundary_ledger(case) == before
