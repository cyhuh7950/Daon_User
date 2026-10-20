"""S42 로그인 원사건→본인 개인 Workspace source의 저장 경계.

실행/QA/DDL 수명은 main 소유다. 중앙 감사는 실제 PG 저장소를 사용하되
원래 별도 commit인 중앙 감사와 identity 업무의 원자성을 주장하지 않는다.
소비·HTTP·브라우저·정식 환경·다른 보안 사건의 검증은 포함하지 않는다.
"""
from __future__ import annotations

import hashlib
import inspect
import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from test_account_notification_events import account_notification_case, _PrivateValue


def _source_rows(connection, case):
    # source의 허용된 metadata만 읽는다. 원 credential/세션 digest/기기 상세는 조회하지 않는다.
    columns = ('tenant_id', 'workspace_id', 'source_event_id', 'recipient_id', 'source_store',
        'origin_event_id', 'action', 'target_type', 'target_id', 'kind', 'severity', 'notebook_id',
        'trace_id', 'policy_version', 'occurred_at', 'state', 'delivered_at', 'version')
    rows = connection.execute('SELECT ' + ','.join(columns) + ' FROM public.notification_source_outbox '
        'WHERE tenant_id=%s AND recipient_id=%s ORDER BY workspace_id,source_event_id',
        (case.tenant, case.actor)).fetchall()
    return {row[2]: dict(zip(columns, row)) for row in rows}


def _private_login(identity, case, trace):
    from daon_user_api.identity import DevicePlatform

    __tracebackhide__ = True
    credentials = identity.local_login(login_id=case.actor, password=case.password, platform=DevicePlatform.WEB,
        trace_id=trace, policy_version=case.policy)
    # pytest의 실패 표현에서도 반환 토큰을 노출하지 않는다. 값은 메모리에서만 보유한다.
    credentials = replace(credentials, access_token=_PrivateValue(credentials.access_token),
        refresh_token=None if credentials.refresh_token is None else _PrivateValue(credentials.refresh_token))
    return credentials


def test_local_login_records_real_central_origin_and_one_atomic_personal_workspace_source(account_notification_case, monkeypatch):
    """정상 session/device·실제 중앙 감사 뒤 source 부재를 포착한다. 발행/감사 대역0."""
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.identity import IdentityService

    case = account_notification_case
    trace = case.trace + '-security-login'
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT m.role,w.workspace_kind,r.role,r.state FROM public.identity_memberships m '
            'JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id '
            'JOIN public.identity_auth_tenant_roles r ON r.tenant_id=m.tenant_id AND r.user_id=m.user_id '
            'WHERE m.tenant_id=%s AND m.user_id=%s AND w.workspace_id=%s',
            (case.tenant, case.actor, case.workspace)).fetchall() == [
                ('personal_owner', 'personal', 'personal_owner', 'active')]
        before_sources = _source_rows(connection, case)
        before_sessions = connection.execute('SELECT session_id,device_id,user_id,tenant_id,client_kind,state '
            'FROM public.identity_sessions WHERE tenant_id=%s AND user_id=%s ORDER BY session_id',
            (case.tenant, case.actor)).fetchall()
        before_devices = connection.execute('SELECT device_id,tenant_id,user_id,platform,state '
            'FROM public.identity_devices WHERE tenant_id=%s AND user_id=%s ORDER BY device_id',
            (case.tenant, case.actor)).fetchall()
        before_central = {row[0] for row in connection.execute('SELECT event_id FROM public.security_audit_events '
            'WHERE tenant_id=%s AND actor_id=%s', (case.tenant, case.actor)).fetchall()}

    central = PostgresSecurityAuditStore(case.app, min_size=1, max_size=1)
    try:
        supports_flag = 'notification_inbox_enabled' in inspect.signature(IdentityService).parameters
        identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
            clock=lambda: datetime.now(timezone.utc), dispatch_pending_audits=False,
            **({'notification_inbox_enabled': True} if supports_flag else {}))
        # signature 미구현이면 kwargs로 준비 실패시키지 않고 정상 로그인 후 source 부재까지 도달한다.
        with _observe_login_atomicity(monkeypatch, identity, case):
            credentials = _private_login(identity, case, trace)
        assert (credentials.user_id, credentials.tenant_id, credentials.client_kind.value) == (
            case.actor, case.tenant, 'web')
        assert credentials.session_id not in {row[0] for row in before_sessions}
        assert credentials.device_id not in {row[0] for row in before_devices}
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            sessions = connection.execute('SELECT session_id,device_id,user_id,tenant_id,client_kind,state '
                'FROM public.identity_sessions WHERE tenant_id=%s AND user_id=%s ORDER BY session_id',
                (case.tenant, case.actor)).fetchall()
            devices = connection.execute('SELECT device_id,tenant_id,user_id,platform,state '
                'FROM public.identity_devices WHERE tenant_id=%s AND user_id=%s ORDER BY device_id',
                (case.tenant, case.actor)).fetchall()
            assert set(sessions) - set(before_sessions) == {(
                credentials.session_id, credentials.device_id, case.actor, case.tenant, 'web', 'active')}
            assert set(before_sessions) <= set(sessions)
            assert set(devices) - set(before_devices) == {(
                credentials.device_id, case.tenant, case.actor, 'web', 'registered')}
            assert set(before_devices) <= set(devices)

            # 원감사를 직접 INSERT/append하지 않는다. 실제 local_login의 _audit가 저장한 행이다.
            audits = connection.execute('SELECT event_id,actor_id,actor_type,tenant_id,workspace_id,action,'
                'target_type,target_id,outcome,trace_id,policy_version,occurred_at,before_value,after_value,metadata '
                'FROM public.security_audit_events WHERE tenant_id=%s AND actor_id=%s '
                "AND action='identity.login.succeeded' AND target_type='session' AND target_id=%s",
                (case.tenant, case.actor, credentials.session_id)).fetchall()
            assert len(audits) == 1, 'LOCAL_LOGIN_REAL_CENTRAL_AUDIT_MISSING'
            event_id = audits[0][0]
            occurred_at = audits[0][11]
            assert audits[0][1:11] == (case.actor, 'user', case.tenant, None,
                'identity.login.succeeded', 'session', credentials.session_id, 'succeeded', trace, case.policy)
            assert audits[0][12:] == (None, None, {'client_type': 'web', 'auth_method': 'local'})
            assert occurred_at.tzinfo is not None
            after_central = {row[0] for row in connection.execute('SELECT event_id FROM public.security_audit_events '
                'WHERE tenant_id=%s AND actor_id=%s', (case.tenant, case.actor)).fetchall()}
            assert after_central - before_central == {event_id}

            sources = _source_rows(connection, case)
            assert all(sources.get(key) == value for key, value in before_sources.items())
            added = set(sources) - set(before_sources)
            assert len(added) == 1, 'LOCAL_LOGIN_PERSONAL_WORKSPACE_SOURCE_MISSING'
            source_id = next(iter(added))
            assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', source_id) is not None
            # source ID 식은 별도 확정 전이다. 실제 원event/Workspace/수신자 결속만 요구한다.
            assert sources[source_id] == dict(tenant_id=case.tenant, workspace_id=case.workspace,
                source_event_id=source_id, recipient_id=case.actor, source_store='identity',
                origin_event_id=event_id, action='identity.login.succeeded', target_type='session',
                target_id=credentials.session_id, kind='authentication', severity='info', notebook_id=None,
                trace_id=trace, policy_version=case.policy, occurred_at=occurred_at,
                state='pending', delivered_at=None, version=1)
            assert connection.execute('SELECT s.xmin=d.xmin '
                'FROM public.identity_sessions s JOIN public.identity_devices d '
                'ON d.device_id=s.device_id AND d.tenant_id=s.tenant_id AND d.user_id=s.user_id '
                'JOIN public.notification_source_outbox n ON n.tenant_id=s.tenant_id AND n.recipient_id=s.user_id '
                "AND n.source_store='identity' AND n.action='identity.login.succeeded' AND n.target_type='session' AND n.target_id=s.session_id "
                'WHERE s.tenant_id=%s AND s.user_id=%s AND s.session_id=%s AND n.workspace_id=%s AND n.source_event_id=%s',
                (case.tenant, case.actor, credentials.session_id, case.workspace, source_id)).fetchall() == [(True,)]
            # source는 trigger EXCEPTION의 subtransaction XID를 갖는다. commit 가시성은 위 observer로 검증한다.
            # 중앙 security_audit_events는 별도 append/commit이다. xmin 비교에 포함하지 않는다.
            assert _source_rows(connection, case) == sources
    finally:
        central.close()


def _shield_credentials(credentials):
    __tracebackhide__ = True
    return replace(credentials, access_token=_PrivateValue(credentials.access_token),
        refresh_token=None if credentials.refresh_token is None else _PrivateValue(credentials.refresh_token))


def _private_native_login(identity, case, trace):
    __tracebackhide__ = True
    return _shield_credentials(identity.local_native_login(login_id=case.actor, password=case.password,
        trace_id=trace, policy_version=case.policy))


def _private_oidc_begin(identity, policy, redirect, trace, policy_version):
    __tracebackhide__ = True
    start = identity.begin_oidc_login(issuer=policy.issuer, client_id=policy.client_id, audience=policy.audience,
        redirect_uri=redirect, client_kind=policy.client_kind, tenant_id=policy.tenant_id,
        trace_id=trace, policy_version=policy_version)
    return replace(start, state=_PrivateValue(start.state), nonce=_PrivateValue(start.nonce),
        code_verifier=_PrivateValue(start.code_verifier), code_challenge=_PrivateValue(start.code_challenge))


def _private_oidc_complete(identity, start, provider, policy, redirect, platform, trace, policy_version):
    __tracebackhide__ = True
    provider.expected_nonce = start.nonce  # 허용된 메모리 내 프로토콜 대역 전달. DB nonce/digest 조회0.
    return _shield_credentials(identity.complete_oidc_login(state=start.state,
        authorization_code=provider.authorization_code, code_verifier=start.code_verifier,
        client_id=policy.client_id, redirect_uri=redirect, provider=provider, platform=platform,
        trace_id=trace, policy_version=policy_version))


def _login_parent_rows(connection, case, session_id, device_id, oidc_transaction_id):
    """실제 신규 부모/자식과 XID만 읽는다. token/digest/nonce/PKCE 열은 조회하지 않는다."""
    result = {
        'session': connection.execute('SELECT session_id,device_id,tenant_id,user_id,client_kind,state,'
            'created_at,updated_at,xmin::text FROM public.identity_sessions '
            'WHERE tenant_id=%s AND user_id=%s AND session_id=%s',
            (case.tenant, case.actor, session_id)).fetchall(),
        'device': connection.execute('SELECT device_id,tenant_id,user_id,platform,state,created_at,updated_at,xmin::text '
            'FROM public.identity_devices WHERE tenant_id=%s AND user_id=%s AND device_id=%s',
            (case.tenant, case.actor, device_id)).fetchall(),
        'family': connection.execute('SELECT f.family_id,f.session_id,f.state,f.created_at,f.updated_at,f.xmin::text '
            'FROM public.identity_refresh_families f JOIN public.identity_sessions s ON s.session_id=f.session_id '
            'WHERE s.tenant_id=%s AND s.user_id=%s AND s.session_id=%s ORDER BY f.family_id',
            (case.tenant, case.actor, session_id)).fetchall(),
        'refresh': connection.execute('SELECT t.refresh_id,t.family_id,t.used_at,t.created_at,t.xmin::text '
            'FROM public.identity_refresh_tokens t JOIN public.identity_refresh_families f ON f.family_id=t.family_id '
            'JOIN public.identity_sessions s ON s.session_id=f.session_id '
            'WHERE s.tenant_id=%s AND s.user_id=%s AND s.session_id=%s ORDER BY t.refresh_id',
            (case.tenant, case.actor, session_id)).fetchall(),
        'oidc': [],
    }
    if oidc_transaction_id is not None:
        result['oidc'] = connection.execute('SELECT transaction_id,tenant_id,client_kind,used_at,xmin::text '
            'FROM public.identity_oidc_transactions WHERE tenant_id=%s AND transaction_id=%s',
            (case.tenant, oidc_transaction_id)).fetchall()
    return result


def _login_rollback_snapshot(connection, case):
    """기존 행을 포함한 비민감 metadata와 xmin을 비교해 동값 UPDATE도 놓치지 않는다."""
    return {
        'sessions': connection.execute('SELECT session_id,device_id,client_kind,state,created_at,updated_at,xmin::text '
            'FROM public.identity_sessions WHERE tenant_id=%s AND user_id=%s ORDER BY session_id',
            (case.tenant, case.actor)).fetchall(),
        'devices': connection.execute('SELECT device_id,platform,state,created_at,updated_at,xmin::text '
            'FROM public.identity_devices WHERE tenant_id=%s AND user_id=%s ORDER BY device_id',
            (case.tenant, case.actor)).fetchall(),
        'families': connection.execute('SELECT f.family_id,f.session_id,f.state,f.created_at,f.updated_at,f.xmin::text '
            'FROM public.identity_refresh_families f JOIN public.identity_sessions s ON s.session_id=f.session_id '
            'WHERE s.tenant_id=%s AND s.user_id=%s ORDER BY f.family_id', (case.tenant, case.actor)).fetchall(),
        'refresh': connection.execute('SELECT t.refresh_id,t.family_id,t.used_at,t.created_at,t.xmin::text '
            'FROM public.identity_refresh_tokens t JOIN public.identity_refresh_families f ON f.family_id=t.family_id '
            'JOIN public.identity_sessions s ON s.session_id=f.session_id '
            'WHERE s.tenant_id=%s AND s.user_id=%s ORDER BY t.refresh_id', (case.tenant, case.actor)).fetchall(),
        'oidc': connection.execute('SELECT transaction_id,client_kind,used_at,xmin::text '
            'FROM public.identity_oidc_transactions WHERE tenant_id=%s ORDER BY transaction_id',
            (case.tenant,)).fetchall(),
        'sources': _source_rows(connection, case),
        'source_xids': connection.execute('SELECT workspace_id,source_event_id,xmin::text '
            'FROM public.notification_source_outbox WHERE tenant_id=%s AND recipient_id=%s '
            'ORDER BY workspace_id,source_event_id', (case.tenant, case.actor)).fetchall(),
    }


@contextmanager
def _observe_login_atomicity(monkeypatch, identity, case, *, fail_after_verify=False):
    """실제 verifier 뒤, 별도 같은 READ COMMITTED 연결에서 commit/rollback 가시성을 검증한다."""
    __tracebackhide__ = True
    from daon_user_api.notification_events import _account_notification_scope
    from psycopg.pq import TransactionStatus

    real_finalize, observations = identity._enqueue_login_notification, []
    with psycopg.connect(case.admin, connect_timeout=3, autocommit=True) as observer:
        assert observer.autocommit is True
        assert observer.execute('SHOW transaction_isolation').fetchone() == ('read committed',)
        assert observer.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database "
            'WHERE datname=current_database()').fetchone() == (
                'S42 L2a disposable QA 2026-10-17 main owned; remove after tests; no production data',)
        assert observer.execute("SELECT row_security_active('public.identity_sessions'::regclass),"
            "row_security_active('public.identity_devices'::regclass),"
            "row_security_active('public.identity_refresh_families'::regclass),"
            "row_security_active('public.identity_refresh_tokens'::regclass),"
            "row_security_active('public.notification_source_outbox'::regclass)"
        ).fetchone() == (False, False, False, False, False)
        observer_pid = observer.execute('SELECT pg_backend_pid()').fetchone()[0]
        baseline = _login_rollback_snapshot(observer, case)

        def finalize(connection, event, *, oidc_transaction_id=None):
            __tracebackhide__ = True
            # 원 readonly method부터 실행한다. 현재 권한/Source 검사 또는 source 저장을 대체하지 않는다.
            real_finalize(connection, event, oidc_transaction_id=oidc_transaction_id)
            raw = connection._connection
            assert raw.info.transaction_status == TransactionStatus.INTRANS
            assert (event.tenant_id, event.actor_id, event.target_type) == (case.tenant, case.actor, 'session')
            with _account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor,
                workspace_id=case.workspace, enabled=True) as reader:
                assert reader.execute('SELECT pg_backend_pid()').fetchone()[0] != observer_pid
                proof = reader.execute('SELECT public.verify_identity_login_notification_source(%s,%s)',
                    (event.event_id, oidc_transaction_id)).fetchone()
                assert proof is not None and len(proof) == 1 and type(proof[0]) is bool and proof[0] is True
                session = reader.execute('SELECT device_id,client_kind FROM public.identity_sessions '
                    'WHERE tenant_id=%s AND user_id=%s AND session_id=%s',
                    (case.tenant, case.actor, event.target_id)).fetchone()
                assert session is not None
                device_id, client_kind = session
                writer_rows = _login_parent_rows(reader, case, event.target_id, device_id, oidc_transaction_id)
                assert len(writer_rows['session']) == len(writer_rows['device']) == 1
                assert len(writer_rows['family']) == len(writer_rows['refresh']) == (1 if client_kind == 'native' else 0)
                parent_xid = writer_rows['session'][0][-1]
                assert writer_rows['device'][0][-1] == parent_xid
                assert all(row[-1] == parent_xid for key in ('family', 'refresh', 'oidc') for row in writer_rows[key])
            before_commit = _login_parent_rows(observer, case, event.target_id, device_id, oidc_transaction_id)
            assert all(before_commit[key] == [] for key in ('session', 'device', 'family', 'refresh'))
            if client_kind == 'native':
                # 부모 JOIN의 미가시성에 기대지 않고 writer가 확인한 실제 자식 ID도 직접 확인한다.
                assert observer.execute('SELECT family_id FROM public.identity_refresh_families WHERE family_id=%s',
                    (writer_rows['family'][0][0],)).fetchall() == []
                assert observer.execute('SELECT refresh_id FROM public.identity_refresh_tokens WHERE refresh_id=%s',
                    (writer_rows['refresh'][0][0],)).fetchall() == []
            assert _source_rows(observer, case) == baseline['sources']
            if oidc_transaction_id is not None:
                assert len(before_commit['oidc']) == 1 and before_commit['oidc'][0][3] is None
                assert before_commit['oidc'][0][-1] != parent_xid
            # 중앙 원감사는 이미 별도 commit이다. 부모/source 미가시성과 구분한다.
            assert observer.execute('SELECT event_id,actor_id,target_type,target_id,action FROM public.security_audit_events '
                'WHERE tenant_id=%s AND event_id=%s', (case.tenant, event.event_id)).fetchall() == [
                    (event.event_id, case.actor, 'session', event.target_id, 'identity.login.succeeded')]
            observations.append(dict(event=event, device_id=device_id, oidc=oidc_transaction_id,
                writer_rows=writer_rows, sqlstate=None))
            if fail_after_verify:
                try:
                    raw.execute('SELECT 1/0')
                except psycopg.Error as error:
                    assert error.sqlstate == '22012'
                    assert raw.info.transaction_status == TransactionStatus.INERROR
                    observations[-1]['sqlstate'] = error.sqlstate
                    raise
                pytest.fail('LOGIN_POST_VERIFY_SQL_FAILURE_NOT_INJECTED', pytrace=False)

        with monkeypatch.context() as patch:
            patch.setattr(identity, '_enqueue_login_notification', finalize)
            yield observations
        assert len(observations) == 1
        observation = observations[0]
        event = observation['event']
        if fail_after_verify:
            assert observation['sqlstate'] == '22012'
            assert _login_rollback_snapshot(observer, case) == baseline
        else:
            assert observation['sqlstate'] is None
            assert _login_parent_rows(observer, case, event.target_id, observation['device_id'], observation['oidc']) == observation['writer_rows']
            sources = _source_rows(observer, case)
            source_id = 'identity-login-event-' + hashlib.sha256(
                (event.event_id + '|' + case.workspace).encode()).hexdigest()[:32]
            assert set(sources) - set(baseline['sources']) == {source_id}
            assert all(sources[key] == value for key, value in baseline['sources'].items())
            assert (sources[source_id]['origin_event_id'], sources[source_id]['target_id'], sources[source_id]['state'],
                sources[source_id]['version']) == (event.event_id, event.target_id, 'pending', 1)
        assert observer.execute('SELECT pg_backend_pid()').fetchone() == (observer_pid,)


def _assert_extended_login_source(connection, case, credentials, before_sources, *, trace,
    client_kind, platform, metadata, oidc_transaction_id=None):
    """현재 API가 아닌 실제 PG의 안전한 원장 필드만 검사한다. credential 열 조회0."""
    assert (credentials.user_id, credentials.tenant_id, credentials.client_kind.value) == (
        case.actor, case.tenant, client_kind)
    audits = connection.execute('SELECT event_id,occurred_at,actor_id,actor_type,tenant_id,workspace_id,action,'
        'target_type,target_id,outcome,trace_id,policy_version,before_value,after_value,metadata '
        'FROM public.security_audit_events WHERE tenant_id=%s AND actor_id=%s '
        "AND action='identity.login.succeeded' AND target_type='session' AND target_id=%s",
        (case.tenant, case.actor, credentials.session_id)).fetchall()
    assert len(audits) == 1
    event_id, occurred_at = audits[0][:2]
    assert audits[0][2:] == (case.actor, 'user', case.tenant, None, 'identity.login.succeeded', 'session',
        credentials.session_id, 'succeeded', trace, case.policy, None, None, metadata)
    assert occurred_at.tzinfo is not None
    sources = _source_rows(connection, case)
    assert all(sources.get(key) == value for key, value in before_sources.items())
    added = set(sources) - set(before_sources)
    assert len(added) == 1, 'LOGIN_BRANCH_PERSONAL_WORKSPACE_SOURCE_MISSING'
    source_id = next(iter(added))
    assert source_id == 'identity-login-event-' + hashlib.sha256(
        (event_id + '|' + case.workspace).encode()).hexdigest()[:32]
    assert sources[source_id] == dict(tenant_id=case.tenant, workspace_id=case.workspace,
        source_event_id=source_id, recipient_id=case.actor, source_store='identity', origin_event_id=event_id,
        action='identity.login.succeeded', target_type='session', target_id=credentials.session_id,
        kind='authentication', severity='info', notebook_id=None, trace_id=trace, policy_version=case.policy,
        occurred_at=occurred_at, state='pending', delivered_at=None, version=1)
    assert connection.execute('SELECT s.session_id,s.device_id,s.user_id,s.tenant_id,s.client_kind,s.state,d.platform,d.state,'
        's.xmin=d.xmin FROM public.identity_sessions s JOIN public.identity_devices d '
        'ON d.device_id=s.device_id AND d.tenant_id=s.tenant_id AND d.user_id=s.user_id '
        'JOIN public.notification_source_outbox n ON n.tenant_id=s.tenant_id AND n.recipient_id=s.user_id '
        'AND n.target_id=s.session_id WHERE s.tenant_id=%s AND s.user_id=%s AND s.session_id=%s '
        'AND n.workspace_id=%s AND n.source_event_id=%s',
        (case.tenant, case.actor, credentials.session_id, case.workspace, source_id)).fetchall() == [(
            credentials.session_id, credentials.device_id, case.actor, case.tenant, client_kind, 'active', platform, 'registered', True)]
    if client_kind == 'native':
        native = connection.execute('SELECT f.family_id,f.state,t.refresh_id,t.used_at,'
            'f.created_at=s.created_at,t.created_at=s.created_at,s.xmin=f.xmin AND f.xmin=t.xmin '
            'FROM public.identity_sessions s JOIN public.identity_refresh_families f ON f.session_id=s.session_id '
            'JOIN public.identity_refresh_tokens t ON t.family_id=f.family_id '
            'JOIN public.notification_source_outbox n ON n.tenant_id=s.tenant_id AND n.recipient_id=s.user_id '
            'AND n.target_id=s.session_id WHERE s.tenant_id=%s AND s.user_id=%s AND s.session_id=%s '
            'AND n.workspace_id=%s AND n.source_event_id=%s',
            (case.tenant, case.actor, credentials.session_id, case.workspace, source_id)).fetchall()
        assert len(native) == 1
        assert all(isinstance(value, str) and value for value in (native[0][0], native[0][2]))
        assert native[0][1:] == ('active', native[0][2], None, True, True, True)
        assert credentials.refresh_token is not None  # 값 비교/출력/DB digest 조회는 하지 않는다.
    else:
        assert credentials.refresh_token is None
        assert connection.execute('SELECT count(*) FROM public.identity_refresh_families WHERE session_id=%s',
            (credentials.session_id,)).fetchone() == (0,)
    if oidc_transaction_id is not None:
        assert connection.execute('SELECT o.transaction_id,o.client_kind,o.used_at=s.created_at,'
            'o.xmin=s.xmin FROM public.identity_oidc_transactions o '
            'JOIN public.identity_sessions s ON s.tenant_id=o.tenant_id AND s.session_id=%s '
            'JOIN public.notification_source_outbox n ON n.tenant_id=s.tenant_id AND n.recipient_id=s.user_id '
            'AND n.target_id=s.session_id WHERE o.tenant_id=%s AND o.transaction_id=%s AND s.user_id=%s '
            'AND n.workspace_id=%s AND n.source_event_id=%s',
            (credentials.session_id, case.tenant, oidc_transaction_id, case.actor, case.workspace, source_id)).fetchall() == [
                (oidc_transaction_id, client_kind, True, True)]
    # 부모/Native/OIDC의 같은 xmin은 유지한다. source의 subXID 대신 observer가 원자성을 검증한다.
    return sources


def test_local_native_login_records_atomic_source_with_family_and_refresh_record(account_notification_case, monkeypatch):
    """실제 PG local_native_login 분기다. 실제 Windows 장치/HTTP 증거는 아니다."""
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.identity import IdentityService

    case = account_notification_case
    trace = case.trace + '-native-login'
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        before_sources = _source_rows(connection, case)
        before_ids = connection.execute('SELECT session_id,device_id FROM public.identity_sessions '
            'WHERE tenant_id=%s AND user_id=%s', (case.tenant, case.actor)).fetchall()
    central = PostgresSecurityAuditStore(case.app, min_size=1, max_size=1)
    try:
        identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
            clock=lambda: datetime.now(timezone.utc), dispatch_pending_audits=False, notification_inbox_enabled=True)
        with _observe_login_atomicity(monkeypatch, identity, case):
            credentials = _private_native_login(identity, case, trace)
        assert credentials.session_id not in {row[0] for row in before_ids}
        assert credentials.device_id not in {row[1] for row in before_ids}
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            _assert_extended_login_source(connection, case, credentials, before_sources, trace=trace,
                client_kind='native', platform='windows', metadata={'client_type': 'native', 'auth_method': 'local'})
    finally:
        central.close()


@pytest.mark.parametrize('client_kind', ['web', 'native'])
def test_oidc_protocol_callback_records_exact_source_and_rejects_reuse_and_invalid_claims(
    account_notification_case, client_kind, monkeypatch,
):
    """FakeVerifiedOidcProvider는 프로토콜 대역이다. 실제 IdP/브라우저/Native 인증 NOT RUN."""
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.identity import ClientKind, DevicePlatform, IdentityError, IdentityService, OidcClientPolicy
    from test_identity_support import FakeVerifiedOidcProvider

    case = account_notification_case
    kind = ClientKind(client_kind)
    redirect = 'https://app.example.com/auth/callback' if kind is ClientKind.WEB else 'com.sinsan.daon:/oidc/callback'
    platform = DevicePlatform.WEB if kind is ClientKind.WEB else DevicePlatform.WINDOWS
    policy = OidcClientPolicy(issuer='https://login.example.com', client_id='s42-oidc-' + client_kind,
        audience='daon-user-api', redirect_uris=frozenset({redirect}), client_kind=kind, tenant_id=case.tenant)
    provider = FakeVerifiedOidcProvider(subject='s42-login-subject-' + uuid4().hex,
        authorization_code=_PrivateValue('s42-protocol-code-' + uuid4().hex))
    central = PostgresSecurityAuditStore(case.app, min_size=1, max_size=1)
    try:
        identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(policy,),
            clock=lambda: datetime.now(timezone.utc), dispatch_pending_audits=False, notification_inbox_enabled=True)
        first_trace = case.trace + '-oidc-first-' + client_kind
        first_start = _private_oidc_begin(identity, policy, redirect, first_trace, case.policy)
        first = _private_oidc_complete(identity, first_start, provider, policy, redirect, platform, first_trace, case.policy)
        assert first.user_id != case.actor and first.tenant_id == case.tenant
        own = SimpleNamespace(admin=case.admin, app=case.app, actor=first.user_id,
            tenant=case.tenant, workspace=case.workspace, policy=case.policy)
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT issuer,subject FROM public.identity_users WHERE user_id=%s',
                (own.actor,)).fetchall() == [(policy.issuer, provider.subject)]
            assert connection.execute('SELECT role FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s',
                (own.tenant, own.actor)).fetchall() == [('member',)]
            assert _source_rows(connection, own) == {}  # 첫 callback에는 실제 Foundation/auth 범위가 없다.
            assert connection.execute('SELECT count(*) FROM public.security_audit_events WHERE tenant_id=%s '
                "AND actor_id=%s AND action='identity.login.succeeded' AND target_id=%s",
                (own.tenant, own.actor, first.session_id)).fetchone() == (1,)
            original_roles = connection.execute('SELECT user_id,role,state,version,updated_at '
                'FROM public.identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s',
                (case.tenant, case.actor)).fetchall()
            # 이 시험의 새 OIDC user 한 명에게만 실제 FK/기존 editor 역할을 준비한다.
            connection.execute('INSERT INTO public.user_accounts(tenant_id,user_id) VALUES(%s,%s)', (own.tenant, own.actor))
            connection.execute('INSERT INTO public.identity_auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) '
                "VALUES(%s,%s,%s,'editor','active',1,now())", (own.tenant, own.workspace, own.actor))
            assert connection.execute('SELECT w.workspace_kind,m.role,a.role,a.state FROM public.identity_auth_workspaces w '
                'JOIN public.identity_memberships m ON m.tenant_id=w.tenant_id AND m.user_id=%s '
                'JOIN public.identity_auth_memberships a ON a.tenant_id=w.tenant_id AND a.workspace_id=w.workspace_id AND a.user_id=m.user_id '
                'WHERE w.tenant_id=%s AND w.workspace_id=%s', (own.actor, own.tenant, own.workspace)).fetchall() == [
                    ('personal', 'member', 'editor', 'active')]
            assert connection.execute('SELECT user_id,role,state,version,updated_at '
                'FROM public.identity_auth_tenant_roles WHERE tenant_id=%s AND user_id=%s',
                (case.tenant, case.actor)).fetchall() == original_roles
            before_sources = _source_rows(connection, own)

        trace = case.trace + '-oidc-second-' + client_kind
        start = _private_oidc_begin(identity, policy, redirect, trace, case.policy)
        with _observe_login_atomicity(monkeypatch, identity, own):
            credentials = _private_oidc_complete(identity, start, provider, policy, redirect, platform, trace, case.policy)
        assert credentials.user_id == own.actor
        assert credentials.session_id != first.session_id and credentials.device_id != first.device_id
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            sources = _assert_extended_login_source(connection, own, credentials, before_sources, trace=trace,
                client_kind=client_kind, platform=platform.value, metadata={'client_type': client_kind},
                oidc_transaction_id=start.transaction_id)
            sessions = connection.execute('SELECT session_id,device_id FROM public.identity_sessions WHERE tenant_id=%s AND user_id=%s '
                'ORDER BY session_id', (own.tenant, own.actor)).fetchall()
            devices = connection.execute('SELECT device_id FROM public.identity_devices WHERE tenant_id=%s AND user_id=%s ORDER BY device_id',
                (own.tenant, own.actor)).fetchall()
            tenant_sources = connection.execute('SELECT workspace_id,recipient_id,source_event_id,origin_event_id '
                'FROM public.notification_source_outbox WHERE tenant_id=%s ORDER BY workspace_id,recipient_id,source_event_id',
                (own.tenant,)).fetchall()

        with pytest.raises(IdentityError) as reused:
            _private_oidc_complete(identity, start, provider, policy, redirect, platform, trace, case.policy)
        assert (reused.value.code, reused.value.http_status) == ('OIDC_STATE_INVALID', 401)
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert _source_rows(connection, own) == sources
            assert connection.execute('SELECT session_id,device_id FROM public.identity_sessions WHERE tenant_id=%s AND user_id=%s '
                'ORDER BY session_id', (own.tenant, own.actor)).fetchall() == sessions
            assert connection.execute('SELECT device_id FROM public.identity_devices WHERE tenant_id=%s AND user_id=%s ORDER BY device_id',
                (own.tenant, own.actor)).fetchall() == devices
            assert connection.execute('SELECT workspace_id,recipient_id,source_event_id,origin_event_id '
                'FROM public.notification_source_outbox WHERE tenant_id=%s ORDER BY workspace_id,recipient_id,source_event_id',
                (own.tenant,)).fetchall() == tenant_sources

        # 검증 실패는 기존 거부 경로 그대로다. session/device/source 추가0, used_at도 rollback/미사용.
        invalid_start = _private_oidc_begin(identity, policy, redirect, trace + '-invalid', case.policy)
        provider.verification_complete = False
        with pytest.raises(IdentityError) as invalid:
            _private_oidc_complete(identity, invalid_start, provider, policy, redirect, platform, trace + '-invalid', case.policy)
        assert (invalid.value.code, invalid.value.http_status) == ('OIDC_CLAIMS_INVALID', 401)
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert _source_rows(connection, own) == sources
            assert connection.execute('SELECT session_id,device_id FROM public.identity_sessions WHERE tenant_id=%s AND user_id=%s '
                'ORDER BY session_id', (own.tenant, own.actor)).fetchall() == sessions
            assert connection.execute('SELECT device_id FROM public.identity_devices WHERE tenant_id=%s AND user_id=%s ORDER BY device_id',
                (own.tenant, own.actor)).fetchall() == devices
            assert connection.execute('SELECT workspace_id,recipient_id,source_event_id,origin_event_id '
                'FROM public.notification_source_outbox WHERE tenant_id=%s ORDER BY workspace_id,recipient_id,source_event_id',
                (own.tenant,)).fetchall() == tenant_sources
            assert connection.execute('SELECT used_at FROM public.identity_oidc_transactions WHERE tenant_id=%s AND transaction_id=%s',
                (own.tenant, invalid_start.transaction_id)).fetchone() == (None,)
    finally:
        central.close()
