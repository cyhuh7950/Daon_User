"""P1와 기존 S37 삭제의 실제 PG 호환 회귀. 정식 계정/관리자 화면 시험이 아니다."""
from datetime import datetime, timezone

import psycopg
import pytest

from daon_user_api.admin_users import AdminUserService
from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.identity import IdentityError
from daon_user_api.notification_events import _account_notification_scope
from daon_user_api.notification_password import materialize_pending_password_changes
from daon_user_api.notification_postgres import PostgresNotificationRepository
from test_account_notification_events import account_notification_case
from test_identity_password_notification_consumer import _changed
from test_identity_password_notification_boundaries import _password_snapshot


@pytest.fixture
def deletion_admin_case():
    # 별도 임시 UUID 사용자이며 서버의 실제 admin/역할/설정은 변경하지 않는다.
    yield from account_notification_case.__wrapped__()


def test_password_event_preserves_audit_but_does_not_block_existing_admin_delete(
    account_notification_case, deletion_admin_case,
):
    __tracebackhide__ = True
    case, admin = account_notification_case, deletion_admin_case
    assert case.actor != admin.actor and case.tenant != admin.tenant
    principal = admin.identity.validate_access(admin.credentials.access_token,
        trace_id=admin.trace, policy_version=admin.policy)
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    # 기존 서비스의 관리자 allowlist 구성 포트다. 정식 권한 부여나 인증 우회로 보고하지 않는다.
    service = AdminUserService(repository=case.repository, audit_store=central,
        system_admin_user_ids=frozenset({admin.actor}), clock=lambda: datetime.now(timezone.utc))
    scope = CloudAccessContext(case.tenant, case.workspace, case.actor, 'view')
    try:
        with _changed(case):
            assert materialize_pending_password_changes(case.app, context=scope) == 1
            repository = PostgresNotificationRepository(case.app, context=scope)
            item, = repository.notifications_for(tenant_id=case.tenant, recipient_id=case.actor)
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                event, = connection.execute('SELECT event_id,tenant_id,user_id,source_session_id,occurred_at,trace_id,policy_version '
                    'FROM public.identity_password_change_events WHERE tenant_id=%s AND user_id=%s',
                    (case.tenant, case.actor)).fetchall()
                root = connection.execute('SELECT to_jsonb(a) FROM public.security_audit_events a '
                    'WHERE event_id=%s', (event[0],)).fetchone()
                assert root is not None
                before_delete = _password_snapshot(connection, case)
            try:
                result = service.delete_user(principal, user_id=case.actor,
                    idempotency_key='s42-p1-existing-admin-delete-0001', trace_id=admin.trace, policy_version=admin.policy)
            except IdentityError:
                # 미승인 FK가 남아 있는 RED에서도 실제 삭제 Tx의 원상복구를 확인하고 실패는 유지한다.
                with psycopg.connect(case.admin, connect_timeout=3, autocommit=True) as observer:
                    assert _password_snapshot(observer, case) == before_delete
                raise
            assert result.user_id == case.actor and result.replayed is False
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                assert connection.execute('SELECT count(*) FROM public.identity_users WHERE user_id=%s', (case.actor,)).fetchone() == (0,)
                assert connection.execute('SELECT count(*) FROM public.identity_sessions WHERE user_id=%s', (case.actor,)).fetchone() == (0,)
                assert connection.execute('SELECT event_id,tenant_id,user_id,source_session_id,occurred_at,trace_id,policy_version '
                    'FROM public.identity_password_change_events WHERE event_id=%s', (event[0],)).fetchall() == [event]
                assert connection.execute('SELECT to_jsonb(a) FROM public.security_audit_events a WHERE event_id=%s',
                    (event[0],)).fetchone() == root
            assert repository.notifications_for(tenant_id=case.tenant, recipient_id=case.actor) == ()
            assert repository.notification_for(tenant_id=case.tenant, recipient_id=case.actor,
                notification_id=item.notification_id) is None
            with psycopg.connect(case.app, connect_timeout=3) as connection:
                with _account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor, enabled=True) as reader:
                    with pytest.raises(psycopg.Error) as rejected:
                        with connection.transaction():
                            reader.execute('INSERT INTO public.identity_password_change_events '
                                '(event_id,tenant_id,user_id,source_session_id,occurred_at,trace_id,policy_version) '
                                'VALUES(%s,%s,%s,%s,%s,%s,%s)', ('deleted-parent-probe-' + event[0], *event[1:]))
                    assert rejected.value.sqlstate == '23514'
            assert admin.identity.validate_access(admin.credentials.access_token,
                trace_id=admin.trace, policy_version=admin.policy).user_id == admin.actor
    finally:
        central.close()
