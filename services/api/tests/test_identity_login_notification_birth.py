"""기존 로그인 행의 동값 UPDATE/원감사 복제로 신규 source를 합성하지 못한다."""
from dataclasses import fields, replace
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest

from test_account_notification_events import account_notification_case
from test_identity_security_notification_events import _private_login, _source_rows


@pytest.mark.parametrize('origin_case', ['old', 'cloned'])
def test_login_source_rejects_old_session_noop_update_and_audit_clone(account_notification_case, origin_case):
    from daon_user_api.audit import AuditEventDraft, PostgresSecurityAuditStore
    from daon_user_api.identity import IdentityService
    from daon_user_api.notification_events import _account_notification_scope

    case = account_notification_case
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=False, dispatch_pending_audits=False)
    accepted = []
    try:
        # 정상 과거 로그인·실제 중앙 감사는 만들되 flag OFF이므로 source는 없다.
        credentials = _private_login(identity, case, case.trace + '-past-login')
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            before = _source_rows(connection, case)
            event_id = connection.execute('SELECT event_id FROM security_audit_events WHERE tenant_id=%s '
                "AND actor_id=%s AND target_id=%s AND action='identity.login.succeeded'",
                (case.tenant, case.actor, credentials.session_id)).fetchone()[0]
        original = central.read(event_id, tenant_id=case.tenant)
        assert original is not None
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            assert connection.execute("SELECT has_function_privilege('daon_app',"
                "'public.enqueue_identity_login_notification(text,text)','EXECUTE')").fetchone() == (False,)
            with _account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor,
                workspace_id=case.workspace, enabled=True) as reader:
                try:
                    with connection.transaction(force_rollback=True):
                        # 동값 UPDATE는 생성 시각을 유지하지만 실제 새 xmin을 만든다.
                        connection.execute('UPDATE public.identity_sessions SET state=state WHERE session_id=%s',
                            (credentials.session_id,))
                        connection.execute('UPDATE public.identity_devices SET state=state WHERE device_id=%s',
                            (credentials.device_id,))
                        candidate = event_id
                        if origin_case == 'cloned':
                            draft = AuditEventDraft(**{field.name: getattr(original, field.name) for field in fields(AuditEventDraft)})
                            candidate = 'audit-login-clone-' + uuid4().hex
                            # 부정 원감사는 실제 중앙 저장기를 사용한다. 정상 로그인 성공 증거가 아니다.
                            central.append(replace(draft, event_id=candidate))
                        row = reader.execute('SELECT public.enqueue_identity_login_notification(%s,NULL)',
                            (candidate,)).fetchone()
                        accepted.append(row)
                except psycopg.Error as rejected:
                    assert rejected.sqlstate == '42501'
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert _source_rows(connection, case) == before
        assert accepted == [], 'PAST_LOGIN_NOOP_OR_CLONED_ORIGIN_ACCEPTED'
    finally:
        central.close()
