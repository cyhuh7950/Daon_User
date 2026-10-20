"""P2-A 실제 PG 비밀번호 재설정 확정→원감사/source의 첫 정상 RED.

메일 sender만 비공개 메모리 포트다. 실제 SMTP/사용자/정식 환경/소비·HTTP 시험이 아니다.
기존 reset 서버 PK를 사용하며 token/digest로 원사건 ID를 만들지 않는다.
원업무와 별도 commit인 중앙 감사의 원자성은 주장하지 않는다.
"""
import hashlib
import secrets
from datetime import datetime, timezone

import psycopg

from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.identity import IdentityService
from test_account_notification_events import account_notification_case, _PrivateValue
from test_identity_security_notification_events import _private_native_login, _source_rows


class _PrivateResetSender:
    """실제 요청의 토큰을 메모리 안에서만 다음 confirm 입력으로 넘긴다. 송신·본문 보관0."""

    def __init__(self, recipient):
        self.recipient = recipient
        self.token = None
        self.calls = 0

    def __repr__(self):
        return '<비공개 재설정 메모리 sender>'

    def send(self, *, recipient, subject, body):
        __tracebackhide__ = True
        prefix = 'Daon 비밀번호 재설정 토큰: '
        if (recipient != self.recipient or subject != 'Daon 비밀번호 재설정'
            or not isinstance(body, str) or not body.startswith(prefix) or self.calls != 0):
            raise AssertionError('RESET_MEMORY_SENDER_CONTRACT_INVALID')
        token = _PrivateValue(body[len(prefix):])
        if not token or any(character.isspace() for character in token):
            raise AssertionError('RESET_MEMORY_TOKEN_INVALID')
        self.token = token
        self.calls += 1


def test_password_reset_confirm_records_exact_server_token_origin_and_self_workspace_source(account_notification_case):
    """실제 요청/확정·부모 변경에 도달한 뒤 정확 reset 원감사/source 누락을 RED로 판정한다."""
    __tracebackhide__ = True
    case = account_notification_case
    # 기존 OFF 서비스로 실제 Native 세션/family도 준비한다. 로그인 source와 reset source를 섞지 않는다.
    native = _private_native_login(case.identity, case, case.trace + '-reset-native')
    sender = _PrivateResetSender(case.actor + '@example.test')
    replacement = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc), email_sender=sender,
        notification_inbox_enabled=True, dispatch_pending_audits=False)
    trace = case.trace + '-reset-confirm'
    try:
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            before_sources = _source_rows(connection, case)
            before_tokens = connection.execute('SELECT token_id FROM public.identity_password_reset_tokens '
                'WHERE user_id=%s', (case.actor,)).fetchall()
            assert before_tokens == []
            sessions = connection.execute('SELECT session_id,state FROM public.identity_sessions '
                'WHERE tenant_id=%s AND user_id=%s ORDER BY session_id', (case.tenant, case.actor)).fetchall()
            assert set(sessions) == {(case.credentials.session_id, 'active'), (native.session_id, 'active')}
            # 기존 family는 nullable tenant가 아니라 실제 session FK의 tenant/user로 범위를 확인한다.
            families = connection.execute('SELECT f.family_id,f.state FROM public.identity_refresh_families f '
                'JOIN public.identity_sessions s ON s.session_id=f.session_id '
                'WHERE s.tenant_id=%s AND s.user_id=%s AND s.session_id=%s',
                (case.tenant, case.actor, native.session_id)).fetchall()
            assert len(families) == 1 and families[0][1] == 'active'
            family_id = families[0][0]
            # refresh credential/digest는 읽지 않는다. 기존 기록의 서버 PK와 비민감 상태만 비교한다.
            refresh_records = connection.execute('SELECT refresh_id,family_id,used_at,created_at,xmin::text '
                'FROM public.identity_refresh_tokens WHERE family_id=%s ORDER BY refresh_id', (family_id,)).fetchall()
            assert len(refresh_records) == 1 and refresh_records[0][2] is None
            assert connection.execute('SELECT event_id FROM public.security_audit_events '
                "WHERE actor_id=%s AND action='identity.password.reset.confirmed'", (case.actor,)).fetchall() == []

        # 이메일이 아니라 실제 canonical login ID를 전달한다. 기존 공통 입력검사를 변경하지 않는다.
        identity.request_password_reset(identifier=case.actor, trace_id=case.trace + '-reset-request',
            policy_version=case.policy)
        assert sender.calls == 1 and isinstance(sender.token, _PrivateValue)
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            issued = connection.execute('SELECT token_id,user_id,used_at,attempts FROM public.identity_password_reset_tokens '
                'WHERE user_id=%s', (case.actor,)).fetchall()
            assert len(issued) == 1 and issued[0][1:] == (case.actor, None, 0)
            reset_id = issued[0][0]  # 실제 서버가 생성한 PK만 사용한다. 토큰/해시 조회·파생0.

        result = identity.confirm_password_reset(token=sender.token, new_password=replacement,
            trace_id=trace, policy_version=case.policy)
        assert result is None
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            used = connection.execute('SELECT token_id,user_id,used_at,attempts FROM public.identity_password_reset_tokens '
                'WHERE token_id=%s', (reset_id,)).fetchone()
            assert used is not None and used[:2] == (reset_id, case.actor) and used[3] == 1
            occurred_at = used[2]
            assert occurred_at is not None and occurred_at.tzinfo is not None
            revoked = connection.execute('SELECT session_id,state FROM public.identity_sessions '
                'WHERE tenant_id=%s AND user_id=%s ORDER BY session_id', (case.tenant, case.actor)).fetchall()
            assert set(revoked) == {(case.credentials.session_id, 'revoked'), (native.session_id, 'revoked')}
            assert connection.execute('SELECT state FROM public.identity_refresh_families WHERE family_id=%s',
                (family_id,)).fetchone() == ('revoked',)
            assert connection.execute('SELECT refresh_id,family_id,used_at,created_at,xmin::text '
                'FROM public.identity_refresh_tokens WHERE family_id=%s ORDER BY refresh_id', (family_id,)).fetchall() == refresh_records
            # 기존 user/reset/세션/family 갱신은 같은 원업무 Tx다. 별도 중앙 append는 이 비교에 넣지 않는다.
            assert connection.execute('SELECT r.xmin=u.xmin AND u.xmin=s.xmin AND s.xmin=f.xmin '
                'FROM public.identity_password_reset_tokens r JOIN public.identity_users u ON u.user_id=r.user_id '
                'JOIN public.identity_sessions s ON s.user_id=u.user_id '
                'JOIN public.identity_refresh_families f ON f.session_id=s.session_id '
                'WHERE r.token_id=%s AND s.tenant_id=%s AND s.session_id=%s AND f.family_id=%s',
                (reset_id, case.tenant, native.session_id, family_id)).fetchall() == [(True,)]

            origin = 'identity-reset-origin-' + hashlib.sha256((reset_id + '|' + case.tenant).encode()).hexdigest()[:32]
            roots = connection.execute('SELECT event_id,actor_id,actor_type,tenant_id,workspace_id,action,target_type,target_id,'
                'outcome,occurred_at,trace_id,policy_version,before_value,after_value,metadata '
                "FROM public.security_audit_events WHERE actor_id=%s AND action='identity.password.reset.confirmed'",
                (case.actor,)).fetchall()
            assert roots == [(origin, case.actor, 'user', case.tenant, None, 'identity.password.reset.confirmed',
                'user', case.actor, 'succeeded', occurred_at, trace, case.policy, None, None,
                {'reason_code': 'PASSWORD_RESET_CONFIRMED'})], 'PASSWORD_RESET_CONFIRMED_ORIGIN_MISSING'
            sources = _source_rows(connection, case)
            assert all(sources.get(key) == value for key, value in before_sources.items())
            added = set(sources) - set(before_sources)
            source_id = 'identity-reset-event-' + hashlib.sha256((origin + '|' + case.workspace).encode()).hexdigest()[:32]
            assert added == {source_id}, 'PASSWORD_RESET_CONFIRMED_SOURCE_MISSING'
            assert sources[source_id] == dict(tenant_id=case.tenant, workspace_id=case.workspace,
                source_event_id=source_id, recipient_id=case.actor, source_store='identity', origin_event_id=origin,
                action='identity.password.reset.confirmed', target_type='user', target_id=case.actor,
                kind='authentication', severity='info', notebook_id=None, trace_id=trace, policy_version=case.policy,
                occurred_at=occurred_at, state='pending', delivered_at=None, version=1)
            assert connection.execute('SELECT count(*) FROM public.notifications WHERE tenant_id=%s',
                (case.tenant,)).fetchone() == (0,)
    finally:
        sender.token = None
        central.close()
