"""P2-A 첫 경계 subset: 실제 PG 재설정의 scope0/OFF/여러 tenant 교집합.

메일만 메모리 포트이며 원감사/source를 직접 만들지 않는다. 실행·QA 수명은 main 소유다.
추가 실패 주입/동일 reset 경합은 이 subset 인계 뒤 별도로 작성한다.
"""
import hashlib
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.identity import IdentityService, IdentityError
from test_account_notification_events import account_notification_case, _PrivateValue
from test_identity_password_reset_notification_events import _PrivateResetSender


class _PrivateAttempt(SimpleNamespace):
    def __repr__(self):
        return '<비공개 재설정 시험 입력>'


@contextmanager
def _requested_reset(case, *, enabled=True):
    __tracebackhide__ = True
    sender = _PrivateResetSender(case.actor + '@example.test')
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    try:
        identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
            clock=lambda: datetime.now(timezone.utc), email_sender=sender,
            notification_inbox_enabled=enabled, dispatch_pending_audits=False)
        identity.request_password_reset(identifier=case.actor, trace_id=case.trace + '-boundary-request',
            policy_version=case.policy)
        assert sender.calls == 1 and isinstance(sender.token, _PrivateValue)
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            rows = connection.execute('SELECT token_id,user_id,used_at,attempts '
                'FROM public.identity_password_reset_tokens WHERE user_id=%s', (case.actor,)).fetchall()
            assert len(rows) == 1 and rows[0][1:] == (case.actor, None, 0)
        yield _PrivateAttempt(case=case, identity=identity, sender=sender, central=central,
            reset_id=rows[0][0], replacement=_PrivateValue('S42!' + secrets.token_urlsafe(24)),
            trace=case.trace + '-boundary-confirm')
    finally:
        sender.token = None
        central.close()


def _confirm(attempt):
    __tracebackhide__ = True
    return attempt.identity.confirm_password_reset(token=attempt.sender.token,
        new_password=attempt.replacement, trace_id=attempt.trace, policy_version=attempt.case.policy)


def _reset_state(attempt):
    """토큰/credential digest 없이 사용 처리·기존 세션 철회 metadata만 읽는다."""
    case = attempt.case
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        reset = connection.execute('SELECT user_id,used_at,attempts FROM public.identity_password_reset_tokens '
            'WHERE token_id=%s', (attempt.reset_id,)).fetchone()
        assert reset is not None and reset[0] == case.actor and reset[1] is not None and reset[2] == 1
        assert connection.execute('SELECT session_id,state FROM public.identity_sessions '
            'WHERE tenant_id=%s AND user_id=%s', (case.tenant, case.actor)).fetchall() == [
                (case.credentials.session_id, 'revoked')]
        return reset[1]


def _notification_rows(case):
    """자기 합성 user의 P2 사건만 여러 tenant에서 비교한다. P1/로그인 사건과 섞지 않는다."""
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        roots = connection.execute('SELECT tenant_id,event_id,workspace_id,actor_id,target_type,target_id,'
            'occurred_at,trace_id,policy_version,metadata FROM public.security_audit_events '
            "WHERE actor_id=%s AND action='identity.password.reset.confirmed' ORDER BY tenant_id,event_id",
            (case.actor,)).fetchall()
        sources = connection.execute('SELECT tenant_id,workspace_id,recipient_id,source_event_id,origin_event_id,'
            'source_store,target_type,target_id,kind,severity,notebook_id,occurred_at,trace_id,policy_version,state,version '
            'FROM public.notification_source_outbox '
            "WHERE recipient_id=%s AND action='identity.password.reset.confirmed' ORDER BY tenant_id,workspace_id",
            (case.actor,)).fetchall()
    return roots, sources


def _assert_exact_scopes(attempt, expected):
    case = attempt.case
    occurred_at = _reset_state(attempt)
    roots, sources = _notification_rows(case)
    tenants = {tenant for tenant, _workspace in expected}
    assert len(roots) == len(tenants) and {row[0] for row in roots} == tenants
    origins = {tenant: 'identity-reset-origin-' + hashlib.sha256(
        (attempt.reset_id + '|' + tenant).encode()).hexdigest()[:32] for tenant in tenants}
    for row in roots:
        tenant = row[0]
        assert row == (tenant, origins[tenant], None, case.actor, 'user', case.actor,
            occurred_at, attempt.trace, case.policy, {'reason_code': 'PASSWORD_RESET_CONFIRMED'})
    assert len(sources) == len(expected) and {(row[0], row[1]) for row in sources} == set(expected)
    for row in sources:
        tenant, workspace = row[:2]
        source_id = 'identity-reset-event-' + hashlib.sha256((origins[tenant] + '|' + workspace).encode()).hexdigest()[:32]
        assert row == (tenant, workspace, case.actor, source_id, origins[tenant], 'identity', 'user', case.actor,
            'authentication', 'info', None, occurred_at, attempt.trace, case.policy, 'pending', 1)


@pytest.mark.parametrize('scope_gap', ['identity-membership-absent', 'foundation-user-absent'])
def test_reset_scope_zero_preserves_success_without_virtual_root_or_source(account_notification_case, scope_gap):
    __tracebackhide__ = True
    case = account_notification_case
    with _requested_reset(case) as attempt:
        # 새 QA fixture의 정확 행만 제거한다. identity 사용자/reset/세션·공유 권한/guard는 보존한다.
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            if scope_gap == 'identity-membership-absent':
                changed = connection.execute('DELETE FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s',
                    (case.tenant, case.actor))
            else:
                changed = connection.execute('DELETE FROM public.user_accounts WHERE tenant_id=%s AND user_id=%s',
                    (case.tenant, case.actor))
            assert changed.rowcount == 1
        assert _notification_rows(case) == ([], [])
        assert _confirm(attempt) is None
        _assert_exact_scopes(attempt, set())


def test_reset_flag_off_preserves_legacy_success_and_used_token_rejection_without_notification(account_notification_case):
    __tracebackhide__ = True
    case = account_notification_case
    with _requested_reset(case, enabled=False) as attempt:
        assert _confirm(attempt) is None
        _assert_exact_scopes(attempt, set())
        before = _notification_rows(case)
        used_at = _reset_state(attempt)
        with pytest.raises(IdentityError) as rejected:
            _confirm(attempt)
        assert (rejected.value.code, rejected.value.http_status) == ('PASSWORD_RESET_TOKEN_INVALID', 400)
        assert _reset_state(attempt) == used_at and _notification_rows(case) == before


def _new_organization_scope(case, *, has_identity):
    """새 QA metadata만 기존 표에 준비한다. 실제 소속/권한 판정이나 원사건을 대역 처리하지 않는다."""
    suffix = uuid4().hex
    tenant, workspace = 's42-reset-tenant-' + suffix, 's42-reset-workspace-' + suffix
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        connection.execute('INSERT INTO public.identity_tenants(tenant_id) VALUES(%s)', (tenant,))
        if has_identity:
            connection.execute("INSERT INTO public.identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'member')",
                (tenant, case.actor))
        connection.execute("INSERT INTO public.tenants(tenant_id,display_name) VALUES(%s,'S42 reset synthetic tenant')", (tenant,))
        connection.execute("INSERT INTO public.workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,'S42 reset synthetic workspace')",
            (tenant, workspace))
        connection.execute('INSERT INTO public.user_accounts(tenant_id,user_id) VALUES(%s,%s)', (tenant, case.actor))
        connection.execute("INSERT INTO public.identity_auth_workspaces VALUES(%s,%s,'organization','cloud_sync',1000,1,1,now())",
            (workspace, tenant))
        connection.execute('INSERT INTO public.identity_auth_tenant_policies VALUES(%s,1,now())', (tenant,))
        connection.execute('INSERT INTO public.identity_auth_workspace_policies VALUES(%s,%s,1,now())', (tenant, workspace))
        connection.execute('INSERT INTO public.identity_auth_memberships '
            "(tenant_id,workspace_id,user_id,role,state,version,updated_at) VALUES(%s,%s,%s,'editor','active',1,now())",
            (tenant, workspace, case.actor))
    return tenant, workspace


def test_reset_uses_all_actual_tenant_workspace_intersections_and_preserves_origin_per_tenant(account_notification_case):
    __tracebackhide__ = True
    case = account_notification_case
    eligible_tenant, eligible_workspace = _new_organization_scope(case, has_identity=True)
    auth_only_tenant, auth_only_workspace = _new_organization_scope(case, has_identity=False)
    second_personal = 's42-reset-personal-' + uuid4().hex
    identity_only_workspace = 's42-reset-noauth-' + uuid4().hex
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        # 본인 tenant의 두 개인 Workspace는 기존 personal_owner 권한을 그대로 사용한다.
        for tenant, workspace, kind in ((case.tenant, second_personal, 'personal'),
                (eligible_tenant, identity_only_workspace, 'organization')):
            connection.execute("INSERT INTO public.workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,'S42 reset scope control')",
                (tenant, workspace))
            connection.execute("INSERT INTO public.identity_auth_workspaces VALUES(%s,%s,%s,'cloud_sync',1000,1,1,now())",
                (workspace, tenant, kind))
            connection.execute('INSERT INTO public.identity_auth_workspace_policies VALUES(%s,%s,1,now())', (tenant, workspace))
    with _requested_reset(case) as attempt:
        assert _notification_rows(case) == ([], [])
        assert _confirm(attempt) is None
        _assert_exact_scopes(attempt, {(case.tenant, case.workspace), (case.tenant, second_personal),
            (eligible_tenant, eligible_workspace)})
        _roots, sources = _notification_rows(case)
        assert not any(row[:2] in {(auth_only_tenant, auth_only_workspace),
            (eligible_tenant, identity_only_workspace)} for row in sources)
