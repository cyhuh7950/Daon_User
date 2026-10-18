"""P2-A 실제 PG 실패 원자성·동일 reset 행잠금 경합 3건.

메일은 기존 메모리 sender만 쓴다. 중앙 감사는 별도 commit이므로 source 실패 뒤
남은 감사는 ghost로 구분한다. 실행·QA 수명은 main 소유이며 정식 환경 시험이 아니다.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event
from time import monotonic

import psycopg
import pytest

from daon_user_api.identity import IdentityError, IdentityService
from daon_user_api.identity_postgres import PostgresIdentityRepository
from test_account_notification_events import account_notification_case
from test_identity_password_reset_notification_boundaries import (
    _assert_exact_scopes, _confirm, _notification_rows, _requested_reset,
)


def _parent_state(attempt):
    """비밀번호/토큰/digest 없이 기존 행의 상태와 xmin으로 rollback을 확인한다."""
    __tracebackhide__ = True
    case = attempt.case
    with psycopg.connect(case.admin, connect_timeout=3) as connection:
        return {
            'user': connection.execute('SELECT user_id,state,password_change_required,xmin::text '
                'FROM public.identity_users WHERE user_id=%s', (case.actor,)).fetchall(),
            'reset': connection.execute('SELECT token_id,user_id,used_at,attempts,created_at,xmin::text '
                'FROM public.identity_password_reset_tokens WHERE user_id=%s ORDER BY token_id',
                (case.actor,)).fetchall(),
            'sessions': connection.execute('SELECT session_id,user_id,tenant_id,state,updated_at,xmin::text '
                'FROM public.identity_sessions WHERE user_id=%s ORDER BY session_id', (case.actor,)).fetchall(),
            'devices': connection.execute('SELECT device_id,state,updated_at,xmin::text '
                'FROM public.identity_devices WHERE user_id=%s ORDER BY device_id', (case.actor,)).fetchall(),
            # family의 nullable tenant가 아니라 실제 session FK로 본인을 확인한다.
            'families': connection.execute('SELECT f.family_id,f.session_id,f.state,f.updated_at,f.xmin::text '
                'FROM public.identity_refresh_families f JOIN public.identity_sessions s ON s.session_id=f.session_id '
                'WHERE s.user_id=%s ORDER BY f.family_id', (case.actor,)).fetchall(),
            'refresh': connection.execute('SELECT r.refresh_id,r.family_id,r.used_at,r.created_at,r.xmin::text '
                'FROM public.identity_refresh_tokens r JOIN public.identity_refresh_families f ON f.family_id=r.family_id '
                'JOIN public.identity_sessions s ON s.session_id=f.session_id '
                'WHERE s.user_id=%s ORDER BY r.refresh_id', (case.actor,)).fetchall(),
        }


def _assert_safe_failure(attempt):
    __tracebackhide__ = True
    with pytest.raises(IdentityError) as rejected:
        _confirm(attempt)
    assert (rejected.value.code, rejected.value.http_status) == ('PERSISTENCE_UNAVAILABLE', 503)
    assert attempt.sender.calls == 1  # 실패 주입 대상은 메일이 아니라 실제 저장 SQL이다.


class _AuditInsertFailure:
    """실제 중앙 INSERT 뒤 같은 중앙 Tx에 SQL22012를 발생시킨다. 성공 대역이 아니다."""
    def __init__(self, raw, observed):
        self.raw, self.observed = raw, observed

    def __repr__(self):
        return '<비공개 중앙 연결 실패 관측기>'

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def execute(self, query, params=None):
        __tracebackhide__ = True
        cursor = self.raw.execute(query, params)
        if ' '.join(query.split()).startswith('INSERT INTO security_audit_events '):
            assert cursor.rowcount == 1
            try:
                self.raw.execute('SELECT 1/0')
            except psycopg.Error as error:
                self.observed.append(error.sqlstate)
                raise
            raise AssertionError('AUDIT_SQL_FAILURE_NOT_RAISED')
        return cursor


def test_reset_central_insert_failure_rolls_back_parent_and_source(account_notification_case, monkeypatch):
    """중앙 append 실패를 무시하고 reset을 성공시키거나 부분 commit하면 실패한다."""
    __tracebackhide__ = True
    case = account_notification_case
    with _requested_reset(case) as attempt:
        before = _parent_state(attempt)
        assert _notification_rows(case) == ([], [])
        observed = []
        pool_connection = attempt.central._pool.connection

        @contextmanager
        def fail_insert(*args, **kwargs):
            __tracebackhide__ = True
            with pool_connection(*args, **kwargs) as raw:
                yield _AuditInsertFailure(raw, observed)

        monkeypatch.setattr(attempt.central._pool, 'connection', fail_insert)
        _assert_safe_failure(attempt)
        assert observed == ['22012']
        assert _parent_state(attempt) == before
        assert _notification_rows(case) == ([], [])


class _SourceInsertFailureCursor:
    def __init__(self, cursor, raw, observed):
        self.cursor, self.raw, self.observed = cursor, raw, observed

    def __repr__(self):
        return '<비공개 source 실패 cursor>'

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def execute(self, query, params=None):
        __tracebackhide__ = True
        self.cursor.execute(query, params)
        if query.strip() == 'SELECT public.enqueue_password_reset_notification(%s)':
            # 앱의 private outbox 직접 SELECT 권한은 추가하지 않는다.
            # 실제 RPC True는 SQL ROW_COUNT1 계약이며, 영구 source는 rollback 뒤 관리자 조회로 확인한다.
            assert self.cursor.fetchone() == (True,)
            try:
                self.raw.execute('SELECT 1/0')
            except psycopg.Error as error:
                self.observed.append(error.sqlstate)
                raise
            raise AssertionError('SOURCE_SQL_FAILURE_NOT_RAISED')
        return self


class _SourceInsertFailureConnection:
    def __init__(self, raw, observed):
        self.raw, self.observed = raw, observed

    def __repr__(self):
        return '<비공개 source 연결 실패 관측기>'

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def cursor(self, *args, **kwargs):
        return _SourceInsertFailureCursor(self.raw.cursor(*args, **kwargs), self.raw, self.observed)


class _ObservedIdentityConnection:
    def __init__(self, connection, observed):
        self.original = connection
        self._connection = _SourceInsertFailureConnection(connection._connection, observed)

    def __repr__(self):
        return '<비공개 identity 연결 관측기>'

    def __getattr__(self, name):
        return getattr(self.original, name)


def test_reset_source_sql_failure_rolls_back_parent_but_preserves_separate_central_ghost(
    account_notification_case, monkeypatch,
):
    """실제 source INSERT 뒤 SQL실패를 성공 처리하거나 별도 중앙 ghost를 숨기면 실패한다."""
    __tracebackhide__ = True
    case = account_notification_case
    with _requested_reset(case) as attempt:
        before = _parent_state(attempt)
        assert _notification_rows(case) == ([], [])
        observed, committed = [], []
        original_transaction = case.repository.transaction
        original_append = attempt.central.append

        @contextmanager
        def fail_after_source():
            __tracebackhide__ = True
            with original_transaction() as connection:
                yield _ObservedIdentityConnection(connection, observed)

        def observe_real_append(draft):
            __tracebackhide__ = True
            result = original_append(draft)
            committed.append(draft)
            return result

        monkeypatch.setattr(case.repository, 'transaction', fail_after_source)
        monkeypatch.setattr(attempt.central, 'append', observe_real_append)
        _assert_safe_failure(attempt)
        assert observed == ['22012'] and len(committed) == 1
        assert _parent_state(attempt) == before
        roots, sources = _notification_rows(case)
        assert sources == []
        draft = committed[0]
        origin = 'identity-reset-origin-' + hashlib.sha256(
            (attempt.reset_id + '|' + case.tenant).encode()).hexdigest()[:32]
        assert (draft.event_id, draft.actor_id, draft.actor_type.value, draft.tenant_id,
            draft.workspace_id, draft.action, draft.target_type, draft.target_id, draft.outcome.value,
            draft.trace_id, draft.policy_version, draft.before, draft.after, dict(draft.metadata)) == (
                origin, case.actor, 'user', case.tenant, None, 'identity.password.reset.confirmed',
                'user', case.actor, 'succeeded', attempt.trace, case.policy, None, None,
                {'reason_code': 'PASSWORD_RESET_CONFIRMED'})
        assert draft.occurred_at.tzinfo is not None
        assert roots == [(case.tenant, origin, None, case.actor, 'user', case.actor,
            draft.occurred_at, attempt.trace, case.policy, {'reason_code': 'PASSWORD_RESET_CONFIRMED'})]
        # 이 감사는 별도 commit된 ghost다. 같은 토큰 재확정 성공/중앙 전체 원자성을 주장하지 않는다.


def _confirm_outcome(identity, attempt):
    __tracebackhide__ = True
    try:
        result = identity.confirm_password_reset(token=attempt.sender.token,
            new_password=attempt.replacement, trace_id=attempt.trace, policy_version=attempt.case.policy)
        return ('ok', result)
    except IdentityError as error:
        return ('error', error.code, error.http_status)
    except Exception as error:
        # worker 예외의 입력/연결 repr는 테스트 출력에 넘기지 않는다.
        return ('unexpected', type(error).__name__)


def _observe_transaction(repo, ready, pids, slot):
    original = repo.transaction

    @contextmanager
    def transaction():
        __tracebackhide__ = True
        with original() as connection:
            raw = connection._connection
            raw.execute("SET LOCAL lock_timeout='8s'")
            raw.execute("SET LOCAL statement_timeout='10s'")
            pids[slot] = raw.info.backend_pid
            ready.set()
            yield connection

    return transaction


def _wait_for_two_actual_row_waiters(connection, pids):
    """쿼리 본문/입력 없이 실제 두 backend의 잠금 대기만 제한 시간 관측한다."""
    __tracebackhide__ = True
    deadline = monotonic() + 3
    pause = Event()
    while monotonic() < deadline:
        rows = connection.execute('SELECT pid,wait_event_type,cardinality(pg_blocking_pids(pid))>0 '
            'FROM pg_stat_activity WHERE pid=ANY(%s)', (list(pids.values()),)).fetchall()
        if len(rows) == 2 and all(row[1:] == ('Lock', True) for row in rows):
            return True
        pause.wait(.01)
    return False


def test_same_reset_two_pg_connections_serialize_one_success_and_one_used_rejection(
    account_notification_case, monkeypatch,
):
    """reset FOR UPDATE/사용후 재검사가 없으면 두 확정 성공 또는 실제 잠금 관측 실패다."""
    __tracebackhide__ = True
    case = account_notification_case
    second_repo = PostgresIdentityRepository(case.app)
    try:
        with _requested_reset(case) as attempt:
            second_identity = IdentityService(repository=second_repo, audit_store=attempt.central,
                oidc_policies=(), clock=lambda: datetime.now(timezone.utc),
                email_sender=attempt.sender, notification_inbox_enabled=True, dispatch_pending_audits=False)
            assert _notification_rows(case) == ([], [])
            ready = [Event(), Event()]
            pids = {}
            monkeypatch.setattr(case.repository, 'transaction',
                _observe_transaction(case.repository, ready[0], pids, 0))
            monkeypatch.setattr(second_repo, 'transaction',
                _observe_transaction(second_repo, ready[1], pids, 1))
            # 서로 다른 service/repository를 사용한다. Python RLock 직렬화를 PG경합으로 부르지 않는다.
            with ThreadPoolExecutor(max_workers=2) as executor:
                with psycopg.connect(case.admin, connect_timeout=3, autocommit=True) as holder:
                    with holder.transaction():
                        row = holder.execute('SELECT token_id FROM public.identity_password_reset_tokens '
                            'WHERE token_id=%s FOR UPDATE', (attempt.reset_id,)).fetchone()
                        assert row == (attempt.reset_id,)
                        futures = [executor.submit(_confirm_outcome, identity, attempt)
                            for identity in (attempt.identity, second_identity)]
                        assert all(signal.wait(3) for signal in ready), 'RESET_WORKERS_NOT_READY'
                        assert len(set(pids.values())) == 2
                        with psycopg.connect(case.admin, connect_timeout=3, autocommit=True) as observer:
                            assert _wait_for_two_actual_row_waiters(observer, pids), 'RESET_ROW_LOCK_WAIT_NOT_OBSERVED'
                    # 보유 행잠금을 먼저 해제한 뒤 두 원업무의 실제 commit/거부를 수집한다.
                outcomes = [future.result(timeout=12) for future in futures]
            assert sorted(outcomes) == [('error', 'PASSWORD_RESET_TOKEN_INVALID', 400), ('ok', None)]
            assert attempt.sender.calls == 1
            _assert_exact_scopes(attempt, {(case.tenant, case.workspace)})
    finally:
        second_repo.close()
