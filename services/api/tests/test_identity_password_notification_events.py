"""P1 현재 비밀번호 변경의 실제 원업무/PG. 값은 일시 QA 메모리에서만 사용한다."""
import secrets
from datetime import datetime, timezone

import psycopg
import pytest

from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.identity import IdentityService, IdentityError, DevicePlatform
from test_account_notification_events import account_notification_case, _PrivateValue
from test_identity_security_notification_events import _source_rows, _private_native_login


def test_current_password_change_binds_persistent_event_self_sources_and_rejects_old_session(
    account_notification_case,
):
    __tracebackhide__ = True
    case = account_notification_case
    native = _private_native_login(case.identity, case, case.trace + '-p1-native-before-change')
    replacement = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=True, dispatch_pending_audits=False)
    try:
        identity.change_current_password(access_token=case.credentials.access_token, current_password=case.password,
            new_password=replacement, trace_id=case.trace, policy_version=case.policy)
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute("SELECT to_regclass('public.identity_password_change_events')").fetchone()[0] is not None, \
                'PASSWORD_CHANGE_PERSISTENT_EVENT_NOT_IMPLEMENTED'
            events = connection.execute('SELECT event_id,tenant_id,user_id,source_session_id,occurred_at,trace_id,policy_version '
                'FROM public.identity_password_change_events WHERE tenant_id=%s AND user_id=%s',
                (case.tenant, case.actor)).fetchall()
            assert len(events) == 1
            event, = events
            assert event[1:4] == (case.tenant, case.actor, case.credentials.session_id)
            assert event[4].tzinfo is not None and event[5:] == (case.trace, case.policy)
            root = connection.execute('SELECT actor_id,tenant_id,workspace_id,action,target_type,target_id,outcome,'
                'occurred_at,trace_id,policy_version,before_value,after_value,metadata FROM public.security_audit_events '
                'WHERE tenant_id=%s AND event_id=%s', (case.tenant, event[0])).fetchone()
            assert root == (case.actor, case.tenant, None, 'identity.password.changed', 'user', case.actor, 'succeeded',
                event[4], case.trace, case.policy, None, None, {'reason_code': 'CURRENT_PASSWORD_CHANGED'})
            sources = _source_rows(connection, case)
            assert len(sources) == 1
            source, = sources.values()
            assert (source['action'], source['origin_event_id'], source['target_type'], source['target_id'],
                source['kind'], source['severity'], source['state']) == (
                    'identity.password.changed', event[0], 'user', case.actor, 'authentication', 'info', 'pending')
            assert connection.execute('SELECT e.xmin=s.xmin FROM public.identity_password_change_events e '
                'JOIN public.notification_source_outbox s ON s.tenant_id=e.tenant_id AND s.origin_event_id=e.event_id '
                'WHERE e.event_id=%s AND s.workspace_id=%s', (event[0], case.workspace)).fetchall() == [(True,)]
        try:
            identity.validate_access(case.credentials.access_token, trace_id=case.trace, policy_version=case.policy)
        except IdentityError as denied:
            assert denied.http_status == 401
        else:
            raise AssertionError('OLD_PASSWORD_SESSION_STILL_ALLOWED')
        with pytest.raises(IdentityError) as revoked_refresh:
            identity.rotate_refresh(native.refresh_token, trace_id=case.trace, policy_version=case.policy)
        assert (revoked_refresh.value.code, revoked_refresh.value.http_status) == ('SESSION_REVOKED', 401)
        # 값이나 digest는 출력·저장하지 않는다. 실제 새 비밀번호의 정상 인증만 확인한다.
        identity._notification_inbox_enabled = False
        accepted = identity.local_login(login_id=case.actor, password=replacement, platform=DevicePlatform.WEB,
            trace_id=case.trace, policy_version=case.policy)
        assert (accepted.user_id, accepted.tenant_id) == (case.actor, case.tenant)
    finally:
        central.close()
