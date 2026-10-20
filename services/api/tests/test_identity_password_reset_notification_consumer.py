"""P2 실제 재설정 사건의 소비·현재 권한·읽음. 정식 WSL/브라우저 증거는 아니다."""
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
import pytest

from daon_user_api.audit import AuditEventStore, PostgresSecurityAuditStore
from daon_user_api.authorization import AuthorizationService
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.identity import DevicePlatform, IdentityService
from daon_user_api.notification import NotificationError, NotificationService
from daon_user_api.notification_postgres import PostgresNotificationRepository
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
import daon_user_api.notification_password_reset as password_events
from test_account_notification_events import account_notification_case, _PrivateValue
from test_account_notification_consumer import _account_boundary_ledger
from test_identity_password_reset_notification_events import _PrivateResetSender
from test_identity_password_admin_deletion import deletion_admin_case


@contextmanager
def _reset_completed(case):
    __tracebackhide__ = True
    replacement = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    sender = _PrivateResetSender(case.actor + '@example.test')
    identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc), email_sender=sender,
        notification_inbox_enabled=True, dispatch_pending_audits=False)
    try:
        identity.request_password_reset(identifier=case.actor, trace_id=case.trace, policy_version=case.policy)
        assert sender.calls == 1 and sender.token is not None
        identity.confirm_password_reset(token=sender.token, new_password=replacement,
            trace_id=case.trace, policy_version=case.policy)
        # 변경으로 기존 세션은 철회됐다. 실제 새 비밀번호 인증의 새 세션으로만 후속 조회한다.
        identity._notification_inbox_enabled = False
        credentials = identity.local_login(login_id=case.actor, password=replacement, platform=DevicePlatform.WEB,
            trace_id=case.trace, policy_version=case.policy)
        principal = identity.validate_access(credentials.access_token, trace_id=case.trace, policy_version=case.policy)
        yield identity, principal
    finally:
        sender.token = None
        central.close()


def test_password_reset_materializes_current_self_reopens_reads_and_revocation_hides(account_notification_case):
    case = account_notification_case
    scope = CloudAccessContext(case.tenant, case.workspace, case.actor, 'view')
    with _reset_completed(case) as (identity, principal):
        assert password_events.materialize_pending_password_resets(case.app, context=scope) == 1
        delivered = _account_boundary_ledger(case)
        assert password_events.materialize_pending_password_resets(case.app, context=scope) == 0
        assert _account_boundary_ledger(case) == delivered
        repository = PostgresNotificationRepository(case.app, context=scope)
        authorizations = PostgresAuthorizationRepository(case.app)
        audit = AuditEventStore()
        try:
            authorization = AuthorizationService(repository=authorizations, audit_store=audit,
                clock=lambda: datetime.now(timezone.utc), identity_service=identity)
            service = NotificationService(repository=repository, authorization_service=authorization,
                audit_store=audit, clock=lambda: datetime.now(timezone.utc))
            arguments = dict(principal=principal, trace_id=case.trace, policy_version=case.policy)
            page = service.list_notifications(**arguments, limit=20, cursor=None, filters={}, search=None)
            assert len(page.items) == page.unread_count == 1
            item = page.items[0]
            assert (item.resource_type, item.resource_id, item.kind, item.severity, item.title, item.summary, item.deep_link) == (
                'user', case.actor, 'authentication', 'info', '비밀번호 재설정 완료',
                '본인 계정의 비밀번호가 재설정되었습니다.', '/settings/account')
            assert service.get_notification(**arguments, notification_id=item.notification_id) == item
            reopened = PostgresNotificationRepository(case.app, context=scope)
            assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.actor,
                notification_id=item.notification_id) == item
            assert _account_boundary_ledger(case) == delivered
            read_arguments = dict(**arguments, notification_id=item.notification_id,
                expected_etag=item.etag, idempotency_key='s42-p2-actual-read-0001', requested_state='read')
            read = service.mark_read(**read_arguments)
            assert read.version == 2 and read.read_at is not None
            after_read = _account_boundary_ledger(case)
            assert service.mark_read(**read_arguments) == read and _account_boundary_ledger(case) == after_read
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                assert connection.execute("SELECT count(*) FROM public.security_audit_events WHERE action='identity.password.reset.confirmed' "
                    'AND tenant_id=%s AND actor_id=%s', (case.tenant, case.actor)).fetchone() == (1,)
                assert connection.execute("UPDATE public.identity_auth_tenant_roles SET state='inactive',version=version+1 "
                    "WHERE tenant_id=%s AND user_id=%s AND role='personal_owner' RETURNING user_id",
                    (case.tenant, case.actor)).fetchall() == [(case.actor,)]
            before_denied = _account_boundary_ledger(case)
            assert service.list_notifications(**arguments, limit=20, cursor=None, filters={}, search=None).items == ()
            assert reopened.notification_for(tenant_id=case.tenant, recipient_id=case.actor,
                notification_id=item.notification_id) is None
            with pytest.raises(NotificationError) as denied:
                service.get_notification(**arguments, notification_id=item.notification_id)
            assert (denied.value.code, denied.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
            with pytest.raises(NotificationError) as denied_read:
                service.mark_read(**read_arguments)
            assert (denied_read.value.code, denied_read.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
            assert _account_boundary_ledger(case) == before_denied
        finally:
            authorizations.close()


def test_password_reset_pending_is_discovered_and_consumed_by_real_runtime_tick(account_notification_case):
    from threading import Event
    from types import SimpleNamespace
    from daon_user_api import runtime
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository

    case = account_notification_case
    scope = (case.tenant, case.workspace, case.actor)
    before_scope = (case.tenant, case.workspace, case.actor[:-1])
    with _reset_completed(case):
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            assert connection.execute('SELECT * FROM public.pending_notification_dispatch_scopes(%s,%s,%s,%s)',
                (1, *before_scope)).fetchall() == [scope]
        before = _account_boundary_ledger(case)
        assert before['notifications'] == before['notification_details'] == before['notification_audit_outbox'] == []
        cloud = PostgresCloudStore(case.app, min_size=0, max_size=1, notification_inbox_enabled=True)
        central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
        try:
            dependencies = SimpleNamespace(settings=runtime.RuntimeSettings(profile='test', bind_host='127.0.0.1', port=8000,
                notification_inbox_enabled=True,
                policy_version=case.policy), cloud_store=cloud, audit_store=central,
                studio_approval_lifecycle_repository=StudioApprovalLifecycleRepository(cloud))
            assert runtime._notification_dispatch_tick(dependencies, stop=Event(), after=before_scope,
                projection_cursors={}, limit=1) == scope
            after = _account_boundary_ledger(case)
            assert len(after['notifications']) == len(after['notification_details']) == len(after['notification_audit_outbox']) == 1
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                assert connection.execute('SELECT action,state FROM public.notification_audit_outbox '
                    'WHERE tenant_id=%s AND workspace_id=%s AND recipient_id=%s', scope).fetchall() == [
                        ('notification.created', 'delivered')]
                assert connection.execute("SELECT count(*) FROM public.security_audit_events WHERE tenant_id=%s "
                    "AND workspace_id=%s AND actor_id='notification-service' AND action='notification.created'",
                    scope[:2]).fetchone() == (1,)
                assert connection.execute("SELECT count(*) FROM public.security_audit_events WHERE action='identity.password.reset.confirmed' "
                    'AND tenant_id=%s AND actor_id=%s', (case.tenant, case.actor)).fetchone() == (1,)
            assert password_events.materialize_pending_password_resets(case.app,
                context=CloudAccessContext(*scope, 'view')) == 0
            assert _account_boundary_ledger(case) == after
        finally:
            central.close()
            cloud.close()


@pytest.mark.parametrize('phase', ['sql', 'dto'])
def test_password_materialize_post_insert_error_keeps_pending_and_all_consumption_rolls_back(
    account_notification_case, monkeypatch, phase,
):
    case = account_notification_case
    scope = CloudAccessContext(case.tenant, case.workspace, case.actor, 'view')
    with _reset_completed(case):
        before = _account_boundary_ledger(case)
        observed = []
        real_convert = password_events.notification_from_row
        real_connect = password_events.connect
        active = []

        def observed_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            active.append(connection)
            return connection

        def after_real_materialize(row):
            item = real_convert(row)
            assert (item.resource_id, item.kind, item.delivery_state.value) == (case.actor, 'authentication', 'delivered')
            connection, = active
            assert connection.execute('SELECT n.xmin=d.xmin FROM public.notifications n '
                'JOIN public.notification_details d ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
                'WHERE n.notification_id=%s', (item.notification_id,)).fetchall() == [(True,)]
            drafts = connection.execute('SELECT draft FROM public.pending_notification_audits(100)').fetchall()
            assert len(drafts) == 1 and drafts[0][0]['target_id'] == item.notification_id
            assert connection.execute('SELECT * FROM public.pending_password_reset_notification_sources(100)').fetchall() == []
            observed.append('actual_notification_detail_created_intent_ack')
            if phase == 'dto':
                return real_convert({**row, 'delivery_state': 's42-invalid'})
            # 변환 호출 시점에 실제 SQL materialize는 끝났으나 commit 전이다.
            try:
                connection.execute('SELECT 1/0')
            except psycopg.Error as error:
                assert error.sqlstate == '22012'
                observed.append('actual_sql_22012')
                raise
            pytest.fail('P2_SQL_FAILURE_NOT_INJECTED', pytrace=False)

        with monkeypatch.context() as patch:
            patch.setattr(password_events, 'notification_from_row', after_real_materialize)
            patch.setattr(password_events, 'connect', observed_connect)
            with pytest.raises(NotificationError) as failure:
                password_events.materialize_pending_password_resets(case.app, context=scope)
        assert (failure.value.code, failure.value.http_status) == (
            'DATABASE_UNAVAILABLE' if phase == 'sql' else 'DATABASE_RESPONSE_INVALID', 503)
        assert observed == ['actual_notification_detail_created_intent_ack'] + (['actual_sql_22012'] if phase == 'sql' else [])
        assert _account_boundary_ledger(case) == before
        assert password_events.materialize_pending_password_resets(case.app, context=scope) == 1

@pytest.mark.parametrize('deliver_before_delete', [False, True])
def test_password_reset_deleted_parent_hides_or_suppresses_without_erasing_history(
    account_notification_case, deletion_admin_case, deliver_before_delete,
):
    from daon_user_api.admin_users import AdminUserService
    from daon_user_api.notification_events import _account_notification_scope

    __tracebackhide__ = True
    case, admin = account_notification_case, deletion_admin_case
    principal = admin.identity.validate_access(admin.credentials.access_token,
        trace_id=admin.trace, policy_version=admin.policy)
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    service = AdminUserService(repository=case.repository, audit_store=central,
        system_admin_user_ids=frozenset({admin.actor}), clock=lambda: datetime.now(timezone.utc))
    scope = CloudAccessContext(case.tenant, case.workspace, case.actor, 'view')
    try:
        with _reset_completed(case):
            if deliver_before_delete:
                assert password_events.materialize_pending_password_resets(case.app, context=scope) == 1
            repository = PostgresNotificationRepository(case.app, context=scope)
            items = repository.notifications_for(tenant_id=case.tenant, recipient_id=case.actor)
            assert len(items) == int(deliver_before_delete)
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                root, = connection.execute("SELECT event_id,to_jsonb(a) FROM public.security_audit_events a "
                    "WHERE tenant_id=%s AND actor_id=%s AND action='identity.password.reset.confirmed'",
                    (case.tenant, case.actor)).fetchall()
                assert connection.execute('SELECT count(*) FROM public.identity_password_reset_tokens WHERE user_id=%s',
                    (case.actor,)).fetchone() == (1,)
            result = service.delete_user(principal, user_id=case.actor,
                idempotency_key='s42-p2-existing-admin-delete-0001', trace_id=admin.trace, policy_version=admin.policy)
            assert result.user_id == case.actor and result.replayed is False
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                assert connection.execute('SELECT count(*) FROM public.identity_users WHERE user_id=%s',
                    (case.actor,)).fetchone() == (0,)
                assert connection.execute('SELECT count(*) FROM public.identity_password_reset_tokens WHERE user_id=%s',
                    (case.actor,)).fetchone() == (0,)
                assert connection.execute('SELECT event_id,to_jsonb(a) FROM public.security_audit_events a WHERE event_id=%s',
                    (root[0],)).fetchall() == [root]
            assert repository.notifications_for(tenant_id=case.tenant, recipient_id=case.actor) == ()
            if items:
                assert repository.notification_for(tenant_id=case.tenant, recipient_id=case.actor,
                    notification_id=items[0].notification_id) is None
            assert password_events.materialize_pending_password_resets(case.app, context=scope) == 0
            with psycopg.connect(case.app, connect_timeout=3) as connection:
                with _account_notification_scope(connection, tenant_id=case.tenant, workspace_id=case.workspace,
                    actor_id=case.actor, enabled=True) as reader:
                    with pytest.raises(psycopg.Error) as denied:
                        with connection.transaction():
                            reader.execute('SELECT public.enqueue_password_reset_notification(%s)', (root[0],))
                    assert denied.value.sqlstate == '23514'
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                assert connection.execute('SELECT state FROM public.notification_source_outbox '
                    'WHERE tenant_id=%s AND recipient_id=%s', (case.tenant, case.actor)).fetchall() == [
                        ('delivered' if deliver_before_delete else 'suppressed',)]
            assert admin.identity.validate_access(admin.credentials.access_token,
                trace_id=admin.trace, policy_version=admin.policy).user_id == admin.actor
    finally:
        central.close()


def test_password_reset_new_source_rejects_old_transaction_wrong_actor_and_private_execution(account_notification_case):
    from daon_user_api.notification_events import _account_notification_scope

    __tracebackhide__ = True
    case = account_notification_case
    with _reset_completed(case):
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            root, = connection.execute("SELECT event_id FROM public.security_audit_events "
                "WHERE tenant_id=%s AND actor_id=%s AND action='identity.password.reset.confirmed'",
                (case.tenant, case.actor)).fetchall()
        before = _account_boundary_ledger(case)
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            with _account_notification_scope(connection, tenant_id=case.tenant, workspace_id=case.workspace,
                actor_id=case.actor, enabled=True) as reader:
                for sql, arguments, state in (
                    ('SELECT public.enqueue_password_reset_notification(%s)', (root[0],), '23514'),
                    ('SELECT public.current_password_reset_notification_event(%s)', (root[0],), '42501'),
                ):
                    with pytest.raises(psycopg.Error) as denied:
                        with connection.transaction():
                            reader.execute(sql, arguments)
                    assert denied.value.sqlstate == state
            with _account_notification_scope(connection, tenant_id=case.tenant, workspace_id=case.workspace,
                actor_id=case.actor + '-other', enabled=True) as reader:
                with pytest.raises(psycopg.Error) as denied:
                    with connection.transaction():
                        reader.execute('SELECT public.enqueue_password_reset_notification(%s)', (root[0],))
                assert denied.value.sqlstate == '23514'
        assert _account_boundary_ledger(case) == before
