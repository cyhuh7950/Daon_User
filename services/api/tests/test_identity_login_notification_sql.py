"""확정 로그인 source의 제한 SQL 소비 경계. 실제 IdP/화면 성공은 아니다."""
from datetime import datetime, timezone

import psycopg

from test_account_notification_events import account_notification_case
from test_identity_security_notification_events import _private_login, _source_rows


def test_login_notification_sql_consumes_actual_login_and_keeps_owned_history(account_notification_case):
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.identity import IdentityService
    from daon_user_api.notification_events import _account_notification_scope

    case = account_notification_case
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=True, dispatch_pending_audits=False)
    try:
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            before = _source_rows(connection, case)
        credentials = _private_login(identity, case, case.trace + '-sql-consumer')
        # 정상 사용은 원 기기의 updated_at을 바꾼다. 이를 원로그인 이력 소실로 오판하지 않는다.
        identity.validate_access(credentials.access_token, trace_id=case.trace, policy_version=case.policy)
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            sources = _source_rows(connection, case)
        added = set(sources) - set(before)
        assert len(added) == 1
        source_id = added.pop()
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            with _account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor,
                workspace_id=case.workspace) as reader:
                assert reader.execute('SELECT source_event_id FROM public.pending_identity_login_notification_sources(1)').fetchall() == [(source_id,)]
                target = reader.execute('SELECT public.current_identity_login_notification_target(%s)', (source_id,)).fetchone()[0]
                assert target['accessible'] is True
                assert (target['source_event_id'], target['origin_event_id'], target['target_type'], target['target_id'],
                    target['kind'], target['severity'], target['title'], target['summary'], target['deep_link']) == (
                    source_id, sources[source_id]['origin_event_id'], 'session', credentials.session_id,
                    'authentication', 'info', '로그인 완료', '본인 계정의 로그인이 완료되었습니다.', '/settings/account')
                delivered = reader.execute('SELECT public.materialize_identity_login_notification(%s,true)', (source_id,)).fetchone()[0]
                assert delivered['state'] == 'delivered' and delivered['draft']['action'] == 'notification.created'
                assert reader.execute('SELECT public.materialize_identity_login_notification(%s,true)', (source_id,)).fetchone() == (None,)
        identity.revoke_current_web_session(access_token=credentials.access_token, trace_id=case.trace, policy_version=case.policy)
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            with _account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor,
                workspace_id=case.workspace) as reader:
                target = reader.execute('SELECT public.current_identity_login_notification_target(%s)', (source_id,)).fetchone()[0]
                assert target['accessible'] is True and target['target_id'] == credentials.session_id
                assert reader.execute('SELECT source_event_id FROM public.pending_identity_login_notification_sources(1)').fetchall() == []
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM notification_audit_outbox WHERE tenant_id=%s '
                'GROUP BY action ORDER BY action', (case.tenant,)).fetchall() == [('notification.created', 1)]
    finally:
        central.close()
