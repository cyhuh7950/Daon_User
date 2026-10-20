"""P1 실제 PG 비밀번호 변경의 거부·source 후 rollback. 실행/DDL은 main 소유다.

비밀번호/해시/토큰 열은 조회하지 않는다. 중앙 감사의 별도 commit ghost는 남긴다.
직접 SQL 거부 probe는 실제 정상 저장 뒤 savepoint에서 실행한다. DB 앱 문맥은 인증 증명이 아니다.
"""
from __future__ import annotations

import hashlib
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.pq import TransactionStatus

from daon_user_api import notification_password
from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.identity import DevicePlatform, IdentityError, IdentityService
from daon_user_api.notification_events import _account_notification_scope

from test_account_notification_events import account_notification_case, _PrivateValue
from test_identity_security_notification_events import (
    _login_rollback_snapshot, _private_native_login, _shield_credentials,
)


def _password_snapshot(connection, case):
    """값 자체 대신 실제 인증 동작과 기존 행 metadata/xmin으로 원자성을 확인한다."""
    return dict(
        identity=_login_rollback_snapshot(connection, case),
        users=connection.execute('SELECT user_id,state,issuer,password_change_required,xmin::text '
            'FROM public.identity_users WHERE user_id=%s', (case.actor,)).fetchall(),
        events=connection.execute('SELECT event_id,tenant_id,user_id,source_session_id,occurred_at,trace_id,'
            'policy_version,xmin::text FROM public.identity_password_change_events '
            'WHERE tenant_id=%s AND user_id=%s ORDER BY event_id', (case.tenant, case.actor)).fetchall(),
    )


def _central_ids(connection, case):
    return {row[0] for row in connection.execute('SELECT event_id FROM public.security_audit_events '
        'WHERE tenant_id=%s AND actor_id=%s', (case.tenant, case.actor)).fetchall()}


def _assert_observer(connection):
    assert connection.autocommit is True
    assert connection.execute('SHOW transaction_isolation').fetchone() == ('read committed',)
    assert connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database "
        'WHERE datname=current_database()').fetchone() == (
            'S42 L2a disposable QA 2026-10-17 main owned; remove after tests; no production data',)
    # 거부 RLS/고정 snapshot 때문에 행이 안 보이는 것을 rollback 증거로 착각하지 않는다.
    assert connection.execute("SELECT row_security_active('public.identity_users'::regclass),"
        "row_security_active('public.identity_sessions'::regclass),"
        "row_security_active('public.identity_password_change_events'::regclass),"
        "row_security_active('public.notification_source_outbox'::regclass)"
    ).fetchone() == (False, False, False, False)


def _login_with_password(identity, case, password):
    __tracebackhide__ = True
    return _shield_credentials(identity.local_login(login_id=case.actor, password=password,
        platform=DevicePlatform.WEB, trace_id=case.trace + '-password-control', policy_version=case.policy))


def _assert_original_auth_after_snapshot(case, replacement):
    """불변 비교가 끝난 뒤 기존 인증으로 검증한다. 정상 인증의 last_seen 갱신은 별도다."""
    __tracebackhide__ = True
    assert case.identity._notification_inbox_enabled is False
    with pytest.raises(IdentityError) as denied:
        _login_with_password(case.identity, case, replacement)
    assert (denied.value.code, denied.value.http_status) == ('AUTHENTICATION_REQUIRED', 401)
    principal = case.identity.validate_access(case.credentials.access_token,
        trace_id=case.trace, policy_version=case.policy)
    assert (principal.user_id, principal.tenant_id, principal.session_id, principal.device_id) == (
        case.actor, case.tenant, case.credentials.session_id, case.credentials.device_id)
    accepted = _login_with_password(case.identity, case, case.password)
    assert (accepted.user_id, accepted.tenant_id) == (case.actor, case.tenant)


def test_wrong_current_password_keeps_password_sessions_and_emits_no_event_or_source(account_notification_case):
    __tracebackhide__ = True
    case = account_notification_case
    wrong = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    replacement = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    assert wrong != case.password and replacement != case.password and wrong != replacement
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    try:
        identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
            clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=True, dispatch_pending_audits=False)
        with psycopg.connect(case.admin, connect_timeout=3, autocommit=True) as observer:
            _assert_observer(observer)
            before, roots = _password_snapshot(observer, case), _central_ids(observer, case)
            assert len(before['users']) == 1 and before['identity']['sessions']
            assert before['events'] == [] and before['identity']['sources'] == {}
            with pytest.raises(IdentityError) as denied:
                identity.change_current_password(access_token=case.credentials.access_token,
                    current_password=wrong, new_password=replacement, trace_id=case.trace, policy_version=case.policy)
            assert (denied.value.code, denied.value.http_status) == ('AUTHENTICATION_REQUIRED', 401)
            assert _password_snapshot(observer, case) == before
            assert _central_ids(observer, case) == roots
        _assert_original_auth_after_snapshot(case, replacement)
    finally:
        central.close()


@pytest.fixture
def other_password_account():
    """기존 owned QA fixture를 새 UUID로 재사용한다. 전역 역할/새 인증 경로를 만들지 않는다."""
    yield from account_notification_case.__wrapped__()


def test_password_event_same_tx_collision_scope_and_app_permissions_preserve_normal_commit(
    account_notification_case, other_password_account, monkeypatch,
):
    __tracebackhide__ = True
    case, other = account_notification_case, other_password_account
    interim = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    replacement = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    assert interim != case.password and replacement != interim and replacement != case.password
    assert other.actor != case.actor and other.tenant != case.tenant
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    try:
        off = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
            clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=False, dispatch_pending_audits=False)
        # 충돌에 사용할 다른 event도 실제 비밀번호 변경으로 만든다. 원감사 INSERT/복제0.
        first_trace = case.trace + '-off-prior-change'
        off.change_current_password(access_token=case.credentials.access_token, current_password=case.password,
            new_password=interim, trace_id=first_trace, policy_version=case.policy)
        signed_in = _login_with_password(off, case, interim)
        with psycopg.connect(case.admin, connect_timeout=3, autocommit=True) as observer:
            _assert_observer(observer)
            before = _password_snapshot(observer, case)
            assert before['events'] == [] and before['identity']['sources'] == {}
            prior_roots = observer.execute('SELECT event_id,occurred_at,trace_id,policy_version '
                'FROM public.security_audit_events WHERE tenant_id=%s AND actor_id=%s '
                "AND action='identity.password.changed' AND target_type='user' AND target_id=%s",
                (case.tenant, case.actor, case.actor)).fetchall()
            assert len(prior_roots) == 1 and prior_roots[0][2:] == (first_trace, case.policy)
            prior_event = prior_roots[0]
            # 다른 사람의 session은 실제 존재하며 본인/tenant가 다름을 관리자 읽기로 확인한다.
            assert observer.execute('SELECT session_id,tenant_id,user_id FROM public.identity_sessions '
                'WHERE session_id=%s', (other.credentials.session_id,)).fetchall() == [
                    (other.credentials.session_id, other.tenant, other.actor)]
            identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
                clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=True, dispatch_pending_audits=False)
            real_record = notification_password.record_password_change_event
            recorded, phases = [], []
            insert = ('INSERT INTO public.identity_password_change_events '
                '(event_id,tenant_id,user_id,source_session_id,occurred_at,trace_id,policy_version) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s)')

            def probe_after_real_record(connection, service, principal, *, event):
                __tracebackhide__ = True
                assert service is identity
                assert (principal['user_id'], principal['tenant_id'], principal['session_id']) == (
                    case.actor, case.tenant, signed_in.session_id)
                inserted = real_record(connection, service, principal, event=event)
                assert inserted == 1
                assert event.event_id != prior_event[0]
                raw = connection._connection
                with _account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor,
                    workspace_id=case.workspace, enabled=True) as reader:
                    assert reader.execute('SELECT current_user').fetchone() == ('daon_app',)
                    original = (event.event_id, case.tenant, case.actor, signed_in.session_id,
                        event.occurred_at, case.trace, case.policy)
                    rows = reader.execute('SELECT event_id,tenant_id,user_id,source_session_id,occurred_at,'
                        'trace_id,policy_version,xmin::text FROM public.identity_password_change_events '
                        'WHERE tenant_id=%s AND user_id=%s ORDER BY event_id', (case.tenant, case.actor)).fetchall()
                    assert len(rows) == 1 and rows[0][:-1] == original
                    source_id = 'identity-password-event-' + hashlib.sha256(
                        (event.event_id + '|' + case.workspace).encode()).hexdigest()[:32]
                    source = reader.execute('SELECT public.current_password_change_notification_target(%s)',
                        (source_id,)).fetchone()
                    assert source is not None and type(source[0]) is dict
                    assert source[0]['accessible'] is True and source[0]['origin_event_id'] == event.event_id
                    scope_sql = ("SELECT current_setting('daon.account_actor',true),current_setting('app.tenant_id',true),"
                        "current_setting('app.actor_id',true),current_setting('app.workspace_id',true),"
                        "current_setting('app.capability',true),current_setting('app.notification_inbox_enabled',true)")
                    original_scope = reader.execute(scope_sql).fetchone()
                    probes = [
                        ('same_event', original, '23505', 'identity_password_change_events_pkey', case.tenant, case.actor),
                        ('other_real_event_same_session', (prior_event[0], case.tenant, case.actor, signed_in.session_id,
                            prior_event[1], prior_event[2], prior_event[3]), '23505',
                            'identity_password_change_events_source_session_id_key', case.tenant, case.actor),
                        ('missing_session', (*original[:3], 'ses-missing-' + uuid4().hex, *original[4:]),
                            '23514', None, case.tenant, case.actor),
                        ('foreign_session', (*original[:3], other.credentials.session_id, *original[4:]),
                            '23514', None, case.tenant, case.actor),
                        ('wrong_actor_scope', original, '42501', None, case.tenant, other.actor),
                        ('wrong_tenant_scope', original, '42501', None, other.tenant, case.actor),
                    ]
                    for label, values, sqlstate, constraint, tenant, actor in probes:
                        # 예상 밖 성공이어도 force_rollback으로 probe 행을 남기지 않는다.
                        with pytest.raises(psycopg.Error) as rejected:
                            with raw.transaction(force_rollback=True):
                                with _account_notification_scope(connection, tenant_id=tenant, actor_id=actor,
                                    workspace_id=case.workspace, enabled=True) as scoped:
                                    scoped.execute(insert, values)
                        assert rejected.value.sqlstate == sqlstate
                        if constraint is not None:
                            assert rejected.value.diag.constraint_name == constraint
                        assert raw.info.transaction_status == TransactionStatus.INTRANS
                        assert reader.execute(scope_sql).fetchone() == original_scope
                        assert reader.execute('SELECT event_id,tenant_id,user_id,source_session_id,occurred_at,'
                            'trace_id,policy_version,xmin::text FROM public.identity_password_change_events '
                            'WHERE tenant_id=%s AND user_id=%s ORDER BY event_id', (case.tenant, case.actor)).fetchall() == rows
                        assert reader.execute('SELECT public.current_password_change_notification_target(%s)',
                            (source_id,)).fetchone() == source
                        phases.append(label)
                # 아직 commit하지 않은 정상 사건/source의 외부 연결 미가시성을 유지한다.
                assert _password_snapshot(observer, case) == before
                recorded.append(event)
                return inserted

            with monkeypatch.context() as patch:
                patch.setattr(notification_password, 'record_password_change_event', probe_after_real_record)
                identity.change_current_password(access_token=signed_in.access_token, current_password=interim,
                    new_password=replacement, trace_id=case.trace, policy_version=case.policy)
            assert phases == ['same_event', 'other_real_event_same_session', 'missing_session',
                'foreign_session', 'wrong_actor_scope', 'wrong_tenant_scope']
            assert len(recorded) == 1
            event = recorded[0]
            saved = _password_snapshot(observer, case)
            assert len(saved['events']) == len(saved['identity']['sources']) == 1
            assert saved['events'][0][0] == event.event_id and saved['events'][0][3] == signed_in.session_id
            assert next(iter(saved['identity']['sources'].values()))['origin_event_id'] == event.event_id
            assert observer.execute('SELECT count(*) FROM public.security_audit_events WHERE tenant_id=%s '
                "AND actor_id=%s AND action='identity.password.changed'", (case.tenant, case.actor)).fetchone() == (2,)

            # 기존 app 역할의 실제 DML 거부만 검사한다. 로그인 인증/superuser 방어 증명이 아니다.
            with psycopg.connect(case.app, connect_timeout=3) as app:
                with _account_notification_scope(app, tenant_id=case.tenant, actor_id=case.actor,
                    workspace_id=case.workspace) as reader:
                    assert reader.execute('SELECT current_user').fetchone() == ('daon_app',)
                    assert reader.execute("SELECT has_table_privilege('daon_app',"
                        "'public.identity_password_change_events','UPDATE'),has_table_privilege('daon_app',"
                        "'public.identity_password_change_events','DELETE')").fetchone() == (False, False)
                    assert reader.execute('SELECT event_id FROM public.identity_password_change_events WHERE event_id=%s',
                        (event.event_id,)).fetchall() == [(event.event_id,)]
                    for statement in (
                        'UPDATE public.identity_password_change_events SET trace_id=trace_id WHERE event_id=%s',
                        'DELETE FROM public.identity_password_change_events WHERE event_id=%s',
                    ):
                        with pytest.raises(psycopg.Error) as denied:
                            with app.transaction(force_rollback=True):
                                reader.execute(statement, (event.event_id,))
                        assert denied.value.sqlstate == '42501'
            with psycopg.connect(other.app, connect_timeout=3) as app:
                # 실제 별도 actor의 본인 read scope에서 event_id만 질의해 RLS가 숨기는지 확인한다.
                with _account_notification_scope(app, tenant_id=other.tenant, actor_id=other.actor,
                    workspace_id=other.workspace) as reader:
                    assert reader.execute('SELECT current_user').fetchone() == ('daon_app',)
                    assert reader.execute('SELECT event_id FROM public.identity_password_change_events WHERE event_id=%s',
                        (event.event_id,)).fetchall() == []
                # 같은 tenant 조건에서도 실제 별도 actor가 본인 사건을 볼 수 없다.
                # 이 직접 GUC/RLS probe는 해당 actor의 backend 소속/권한 승인을 뜻하지 않는다.
                with _account_notification_scope(app, tenant_id=case.tenant, actor_id=other.actor,
                    workspace_id=case.workspace) as reader:
                    assert reader.execute('SELECT event_id FROM public.identity_password_change_events WHERE event_id=%s',
                        (event.event_id,)).fetchall() == []
            assert _password_snapshot(observer, case) == saved
    finally:
        central.close()


def test_password_change_post_source_sql_failure_rolls_back_business_and_event_but_keeps_central_ghost(
    account_notification_case, monkeypatch,
):
    __tracebackhide__ = True
    case = account_notification_case
    replacement = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    assert replacement != case.password
    # OFF의 실제 Native 로그인도 준비해 기존 refresh family 철회가 함께 rollback됨을 확인한다.
    assert case.identity._notification_inbox_enabled is False
    native = _private_native_login(case.identity, case, case.trace + '-password-native-preparation')
    central = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    try:
        identity = IdentityService(repository=case.repository, audit_store=central, oidc_policies=(),
            clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=True, dispatch_pending_audits=False)
        phases, events = [], []
        real_record = notification_password.record_password_change_event
        real_scope = notification_password._account_notification_scope
        active_record = {}
        with psycopg.connect(case.admin, connect_timeout=3, autocommit=True) as observer:
            _assert_observer(observer)
            observer_pid = observer.execute('SELECT pg_backend_pid()').fetchone()[0]
            before, roots = _password_snapshot(observer, case), _central_ids(observer, case)
            assert before['events'] == [] and before['identity']['sources'] == {}
            assert any(row[0] == native.session_id and row[3] == 'active' for row in before['identity']['sessions'])
            assert any(row[1] == native.session_id and row[2] == 'active' for row in before['identity']['families'])

            def observe_record(connection, service, principal, *, event):
                __tracebackhide__ = True
                assert service is identity
                assert (principal['tenant_id'], principal['user_id'], principal['session_id']) == (
                    case.tenant, case.actor, case.credentials.session_id)
                active_record.update(connection=connection, event=event)
                return real_record(connection, service, principal, event=event)

            def fail_after_real_source():
                __tracebackhide__ = True
                connection, event = active_record['connection'], active_record['event']
                source_id = 'identity-password-event-' + hashlib.sha256(
                    (event.event_id + '|' + case.workspace).encode()).hexdigest()[:32]
                raw = connection._connection
                assert raw.info.transaction_status == TransactionStatus.INTRANS
                with _account_notification_scope(connection, tenant_id=case.tenant, actor_id=case.actor,
                    workspace_id=case.workspace, enabled=True) as reader:
                    assert reader.execute('SELECT pg_backend_pid()').fetchone()[0] != observer_pid
                    assert reader.execute('SELECT event_id,tenant_id,user_id,source_session_id,occurred_at,trace_id,policy_version '
                        'FROM public.identity_password_change_events WHERE event_id=%s', (event.event_id,)).fetchall() == [
                            (event.event_id, case.tenant, case.actor, case.credentials.session_id,
                                event.occurred_at, case.trace, case.policy)]
                    proof = reader.execute('SELECT public.current_password_change_notification_target(%s)',
                        (source_id,)).fetchone()
                    assert proof is not None and len(proof) == 1 and type(proof[0]) is dict
                    target = dict(proof[0])
                    target['occurred_at'] = datetime.fromisoformat(target['occurred_at'].replace('Z', '+00:00'))
                    assert target == dict(accessible=True, title='비밀번호 변경',
                        summary='본인 계정의 비밀번호가 변경되었습니다.', deep_link='/settings/account',
                        source_event_id=source_id, origin_event_id=event.event_id, target_type='user',
                        target_id=case.actor, kind='authentication', severity='info', trace_id=case.trace,
                        policy_version=case.policy, occurred_at=event.occurred_at, action='identity.password.changed')
                    assert reader.execute('SELECT state FROM public.identity_refresh_families WHERE session_id=%s',
                        (native.session_id,)).fetchall() == [('revoked',)]
                # 같은 observer 연결의 새 READ COMMITTED statement: 업무/사건/source는 모두 아직 비가시다.
                assert _password_snapshot(observer, case) == before
                assert _central_ids(observer, case) - roots == {event.event_id}
                phases.append('real_event_insert_and_source_verified_precommit_invisible')
                events.append(event)
                try:
                    raw.execute('SELECT 1/0')
                except psycopg.Error as error:
                    assert error.sqlstate == '22012'
                    assert raw.info.transaction_status == TransactionStatus.INERROR
                    phases.append('sql_22012')
                    raise
                pytest.fail('PASSWORD_POST_SOURCE_SQL_FAILURE_NOT_INJECTED', pytrace=False)

            class ObservedCursor:
                def __init__(self, cursor):
                    self.cursor = cursor

                def __getattr__(self, name):
                    return getattr(self.cursor, name)

                def fetchone(self):
                    row = self.cursor.fetchone()
                    assert row is not None and len(row) == 1 and type(row[0]) is bool and row[0] is True
                    # 원 helper의 try 안에서 실제 INSERT RPC 결과를 받은 뒤에만 SQL 오류를 주입한다.
                    fail_after_real_source()
                    return row

            class ObservedReader:
                def __init__(self, reader):
                    self.reader = reader

                def __getattr__(self, name):
                    return getattr(self.reader, name)

                def execute(self, query, parameters=None):
                    cursor = self.reader.execute(query, parameters)
                    if isinstance(query, str) and query.lstrip().startswith(
                        'SELECT public.enqueue_password_change_notification('):
                        assert parameters == (active_record['event'].event_id,)
                        return ObservedCursor(cursor)
                    return cursor

            @contextmanager
            def observed_scope(connection, **arguments):
                with real_scope(connection, **arguments) as reader:
                    yield ObservedReader(reader)

            with monkeypatch.context() as patch:
                patch.setattr(notification_password, 'record_password_change_event', observe_record)
                patch.setattr(notification_password, '_account_notification_scope', observed_scope)
                with pytest.raises(IdentityError) as failed:
                    identity.change_current_password(access_token=case.credentials.access_token,
                        current_password=case.password, new_password=replacement, trace_id=case.trace,
                        policy_version=case.policy)
            assert (failed.value.code, failed.value.http_status) == ('PERSISTENCE_UNAVAILABLE', 503)
            assert phases == ['real_event_insert_and_source_verified_precommit_invisible', 'sql_22012']
            assert len(events) == 1
            assert _password_snapshot(observer, case) == before
            # 별도 commit된 정상 중앙 root는 남는다. 삭제/보상하거나 원업무 성공으로 계산하지 않는다.
            event = events[0]
            assert _central_ids(observer, case) - roots == {event.event_id}
            assert observer.execute('SELECT action,target_type,target_id,outcome,safe_code,metadata '
                'FROM public.security_audit_events WHERE tenant_id=%s AND event_id=%s',
                (case.tenant, event.event_id)).fetchall() == [('identity.password.changed', 'user', case.actor,
                    'succeeded', 'CURRENT_PASSWORD_CHANGED', {'reason_code': 'CURRENT_PASSWORD_CHANGED'})]
            assert observer.execute('SELECT pg_backend_pid()').fetchone() == (observer_pid,)
        _assert_original_auth_after_snapshot(case, replacement)
    finally:
        central.close()
