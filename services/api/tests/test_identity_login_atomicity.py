"""L2-L verifier 뒤 SQL 실패의 실제 identity/source rollback 시험. 실행은 main 소유다.

중앙 감사는 별도 commit되어 ghost가 남는 기존 한계를 검사에서 숨기지 않는다.
OIDC provider는 프로토콜 대역이며 실제 IdP/Native 장치/외부 인증 증거가 아니다.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.identity import ClientKind, DevicePlatform, IdentityError, IdentityService, OidcClientPolicy

from test_account_notification_events import account_notification_case, _PrivateValue
from test_identity_security_notification_events import (
    _observe_login_atomicity, _private_login, _private_native_login,
    _private_oidc_begin, _private_oidc_complete, _source_rows,
)
from test_identity_support import FakeVerifiedOidcProvider


def _oidc_failure_input(identity, case, policy, client_kind):
    """기존 정상 첫 callback→본인 Foundation/editor 준비 순서. 새 역할/전역권한 변경0."""
    __tracebackhide__ = True
    redirect = next(iter(policy.redirect_uris))  # 이 시험의 literal 단일 redirect만 존재한다.
    assert len(policy.redirect_uris) == 1
    platform = DevicePlatform.WEB if client_kind == 'web' else DevicePlatform.WINDOWS
    provider = FakeVerifiedOidcProvider(subject='s42-atomicity-subject-' + uuid4().hex,
        authorization_code=_PrivateValue('s42-atomicity-code-' + uuid4().hex))
    trace = case.trace + '-atomicity-oidc-first'
    first_start = _private_oidc_begin(identity, policy, redirect, trace, case.policy)
    first = _private_oidc_complete(identity, first_start, provider, policy, redirect, platform, trace, case.policy)
    assert first.user_id != case.actor and first.tenant_id == case.tenant
    own = SimpleNamespace(admin=case.admin, app=case.app, actor=first.user_id,
        tenant=case.tenant, workspace=case.workspace, policy=case.policy)
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT issuer,subject FROM public.identity_users WHERE user_id=%s',
            (own.actor,)).fetchall() == [(policy.issuer, provider.subject)]
        assert connection.execute('SELECT role FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s',
            (own.tenant, own.actor)).fetchall() == [('member',)]
        assert _source_rows(connection, own) == {}
        original_roles = connection.execute('SELECT user_id,role,state,version,updated_at '
            'FROM public.identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s',
            (case.tenant, case.actor)).fetchall()
        connection.execute('INSERT INTO public.user_accounts(tenant_id,user_id) VALUES(%s,%s)',
            (own.tenant, own.actor))
        connection.execute('INSERT INTO public.identity_auth_memberships'
            '(tenant_id,workspace_id,user_id,role,state,version,updated_at) '
            "VALUES(%s,%s,%s,'editor','active',1,now())", (own.tenant, own.workspace, own.actor))
        assert connection.execute('SELECT w.workspace_kind,m.role,a.role,a.state '
            'FROM public.identity_auth_workspaces w '
            'JOIN public.identity_memberships m ON m.tenant_id=w.tenant_id AND m.user_id=%s '
            'JOIN public.identity_auth_memberships a ON a.tenant_id=w.tenant_id '
            'AND a.workspace_id=w.workspace_id AND a.user_id=m.user_id '
            'WHERE w.tenant_id=%s AND w.workspace_id=%s', (own.actor, own.tenant, own.workspace)
        ).fetchall() == [('personal', 'member', 'editor', 'active')]
        assert connection.execute('SELECT user_id,role,state,version,updated_at '
            'FROM public.identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s',
            (case.tenant, case.actor)).fetchall() == original_roles
    trace = case.trace + '-atomicity-oidc-failure'
    start = _private_oidc_begin(identity, policy, redirect, trace, case.policy)
    return own, start, provider, redirect, platform, trace


@pytest.mark.parametrize('login_branch', ['web', 'native', 'oidc_web', 'oidc_native'])
def test_login_post_verify_sql_failure_rolls_back_identity_and_source_but_not_central_commit(
    account_notification_case, monkeypatch, login_branch,
):
    case = account_notification_case
    is_oidc = login_branch.startswith('oidc_')
    client_kind = login_branch.removeprefix('oidc_')
    policies = ()
    if is_oidc:
        redirect = 'https://app.example.com/auth/callback' if client_kind == 'web' else 'com.sinsan.daon:/oidc/callback'
        policies = (OidcClientPolicy(issuer='https://login.example.com',
            client_id='s42-atomicity-' + client_kind, audience='daon-user-api',
            redirect_uris=frozenset({redirect}), client_kind=ClientKind(client_kind), tenant_id=case.tenant),)
    central = PostgresSecurityAuditStore(case.app, min_size=1, max_size=1)
    try:
        identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=policies,
            clock=lambda: datetime.now(timezone.utc), dispatch_pending_audits=False, notification_inbox_enabled=True)
        own = case
        if is_oidc:
            own, start, provider, redirect, platform, trace = _oidc_failure_input(identity, case, policies[0], client_kind)
        else:
            trace = case.trace + '-atomicity-' + client_kind
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            before_central = {row[0] for row in connection.execute('SELECT event_id FROM public.security_audit_events '
                'WHERE tenant_id=%s AND actor_id=%s', (own.tenant, own.actor)).fetchall()}
        with _observe_login_atomicity(monkeypatch, identity, own, fail_after_verify=True) as observed:
            with pytest.raises(IdentityError) as failed:
                if is_oidc:
                    _private_oidc_complete(identity, start, provider, policies[0], redirect, platform, trace, case.policy)
                elif client_kind == 'native':
                    _private_native_login(identity, case, trace)
                else:
                    _private_login(identity, case, trace)
            assert (failed.value.code, failed.value.http_status) == ('PERSISTENCE_UNAVAILABLE', 503)
        assert len(observed) == 1 and observed[0]['sqlstate'] == '22012'
        event = observed[0]['event']
        # 동기 중앙 append는 이미 별도 commit됐다. ghost를 삭제하거나 rollback 성공에 포함하지 않는다.
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            after_central = {row[0] for row in connection.execute('SELECT event_id FROM public.security_audit_events '
                'WHERE tenant_id=%s AND actor_id=%s', (own.tenant, own.actor)).fetchall()}
            assert after_central - before_central == {event.event_id}
            assert connection.execute('SELECT target_id,trace_id,outcome FROM public.security_audit_events '
                'WHERE tenant_id=%s AND event_id=%s', (own.tenant, event.event_id)).fetchall() == [
                    (event.target_id, trace, 'succeeded')]
            assert all(row['target_id'] != event.target_id for row in _source_rows(connection, own).values())
    finally:
        central.close()
