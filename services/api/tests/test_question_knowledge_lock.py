"""S42 K1-L 실제 등록 자료의 제한 잠금 함수 core RED 후보. 실행·DDL은 main 소유다.

기존 fixture의 초기 합성 metadata와 실제 knowledge_registration 저장을 구분한다.
함수 True는 QUERY/VIEW 승인이나 독립 인증 증명이 아니며, 외부 Daon/Provider 호출은 없다.
두 연결 경합·대기 후 만료 시험을 포함한다. 전체 accepted 저장 rollback·up/down은 별도 검증이다.
"""
from __future__ import annotations

import psycopg
import pytest

from daon_user_api.authorization import Action
from daon_user_api.notification_events import _account_notification_scope
from daon_user_api.open_law_source_access import _current_auth_grant
from test_notification_events import _l2b_registered_knowledge_case


_SIGNATURE = 'public.lock_question_knowledge_selection(text,text,text,text,text,text)'
_CALL = 'SELECT public.lock_question_knowledge_selection(%s,%s,%s,%s,%s,%s)'
_PARENTS = ('sources', 'output_versions', 'knowledge_registrations', 'source_versions')


def _parent_boundary(scope):
    """사용한 QA 범위의 행 ID/xmin과 기존 부모 ACL/RLS만 읽는다. Source 본문·자격증명은 읽지 않는다."""
    with psycopg.connect(scope.admin, connect_timeout=3) as connection:
        privileges = connection.execute('SELECT c.relname,pg_get_userbyid(c.relowner),c.relrowsecurity,'
            "c.relforcerowsecurity,c.relacl::text,has_table_privilege('daon_app',c.oid,'SELECT'),"
            "has_table_privilege('daon_app',c.oid,'UPDATE'),has_table_privilege('daon_app',c.oid,'DELETE') "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname", (list(_PARENTS),)).fetchall()
        assert len(privileges) == 4
        assert all(row[1] == 'daon_notebook_delete' and row[2] is True and row[3] is True
            and row[5] is True and row[6] is False and row[7] is False for row in privileges)
        assert connection.execute("SELECT pg_has_role('daon_app','daon_notebook_delete','MEMBER')").fetchone() == (False,)
        records = {table: connection.execute(f'SELECT record_id,xmin::text FROM public.{table} '
            'WHERE tenant_id=%s AND workspace_id=%s ORDER BY record_id',
            (scope.context.tenant_id, scope.context.workspace_id)).fetchall() for table in _PARENTS}
        return privileges, records


def test_question_knowledge_lock_exact_registration_scope_denial_and_parent_privileges_unchanged():
    """실제 등록/권한 전제 뒤 함수 부재 RED, 정확 잠금 True·범위 오용/ACL 거부·부모 불변을 확인한다."""
    prepared = _l2b_registered_knowledge_case(registration_count=1)
    scope, context = prepared.scope, prepared.scope.context
    assert len(prepared.registrations) == 1
    registration, source_version, source_id, registration_state, output_state, source_state, payload = prepared.registrations[0]
    assert (registration_state, output_state, source_state) == ('registered', 'approved', 'ready')
    assert payload['package_id'] == prepared.package and payload['authority'] == 'approved'
    baseline = _parent_boundary(scope)
    arguments = (context.tenant_id, context.workspace_id, context.notebook_id,
        context.actor_id, registration, source_version)

    with psycopg.connect(scope.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        with _account_notification_scope(connection, tenant_id=context.tenant_id,
            workspace_id=context.workspace_id, actor_id=context.actor_id) as reader:
            assert _current_auth_grant(context, reader, Action.QUERY).allowed is True
            assert _current_auth_grant(context, reader, Action.VIEW).allowed is True
            assert reader.execute('SELECT count(*) FROM public.identity_auth_source_access '
                'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s AND source_version_id=%s AND allowed=0',
                (context.tenant_id, context.workspace_id, context.actor_id, source_version)).fetchone() == (0,)
            # 위 실제 등록/SV/권한 준비 이후에만 아직 없는 제품 함수를 RED로 판정한다.
            assert reader.execute('SELECT to_regprocedure(%s) IS NOT NULL', (_SIGNATURE,)).fetchone() == (True,), \
                'QUESTION_KNOWLEDGE_LOCK_NOT_IMPLEMENTED'
            shape = reader.execute('SELECT pg_get_userbyid(p.proowner),p.prosecdef,p.proretset,'
                'p.prorettype=\'boolean\'::regtype,p.pronargs,p.proargtypes=\'25 25 25 25 25 25\'::oidvector,'
                'p.provolatile,p.proparallel,p.proconfig,'
                "has_function_privilege('daon_app',p.oid,'EXECUTE'),"
                "NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a "
                "WHERE a.grantee<>ALL(ARRAY['daon_app'::regrole::oid,p.proowner]) OR a.is_grantable) "
                'FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)', (_SIGNATURE,)).fetchone()
            assert shape == ('daon_notebook_delete', True, False, True, 6, True, 'v', 'u',
                ['search_path=pg_catalog, pg_temp', 'row_security=on'], True, True)
            names = ('app.tenant_id', 'app.workspace_id', 'app.actor_id', 'app.capability')

            def caller_scope():
                return tuple(reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0] for name in names)

            original_scope = caller_scope()
            row = reader.execute(_CALL, arguments).fetchone()
            assert row is not None and len(row) == 1 and type(row[0]) is bool and row[0] is True
            assert caller_scope() == original_scope
            # 정확 DDL은 인자/GUC scope 불일치를 False가 아니라 42501로 구분한다.
            for index, wrong in ((0, 's42-lock-other-tenant'), (1, 's42-lock-other-workspace'),
                (3, 's42-lock-other-actor')):
                invalid = list(arguments)
                invalid[index] = wrong
                with pytest.raises(psycopg.Error) as denied:
                    with connection.transaction():
                        reader.execute(_CALL, tuple(invalid)).fetchone()
                assert denied.value.sqlstate == '42501'
                assert denied.value.diag.message_primary == 'QUESTION_SELECTION_SCOPE_DENIED'
                assert caller_scope() == original_scope
            for index, missing in ((2, 's42-lock-missing-notebook'), (4, 's42-lock-missing-registration'),
                (5, 's42-lock-missing-source-version')):
                invalid = list(arguments)
                invalid[index] = missing
                assert reader.execute(_CALL, tuple(invalid)).fetchone() == (False,)
                assert caller_scope() == original_scope

            class RollbackAclFixture(Exception):
                pass

            # 자기 QA의 기존 ACL 표에 명시 deny만 한시 준비한다. 부모 권한/불변 Source를 수정하지 않는다.
            with pytest.raises(RollbackAclFixture):
                with connection.transaction():
                    reader.execute('INSERT INTO public.identity_auth_source_access '
                        '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
                        'VALUES(%s,%s,%s,%s,0,1,now()) '
                        'ON CONFLICT(tenant_id,workspace_id,user_id,source_version_id) DO UPDATE '
                        'SET allowed=0,version=identity_auth_source_access.version+1,updated_at=now()',
                        (context.tenant_id, context.workspace_id, context.actor_id, source_version))
                    assert reader.execute(_CALL, arguments).fetchone() == (False,)
                    raise RollbackAclFixture()
            assert reader.execute(_CALL, arguments).fetchone() == (True,)
            assert caller_scope() == original_scope
            # FK 유도의 실제 Source가 원 fixture와 동일하며 호출이 새 등록/자료를 만들지 않는다.
            assert reader.execute('SELECT source_id FROM public.source_versions '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (context.tenant_id, context.workspace_id, source_version)).fetchone() == (source_id,)
    assert _parent_boundary(scope) == baseline


def _wait_for_source_lock(observer, worker_pid, holder_pid, done):
    """시간 추정 대신 실제 두 backend의 Lock 대기/차단 관계를 제한 시간 안에 확인한다."""
    from time import monotonic

    deadline = monotonic() + 2
    while monotonic() < deadline:
        waiting = observer.execute(
            'SELECT wait_event_type,%s=ANY(pg_blocking_pids(pid)) '
            'FROM pg_catalog.pg_stat_activity WHERE pid=%s',
            (holder_pid, worker_pid)).fetchone()
        if waiting == ('Lock', True):
            return
        if done.wait(0.01):
            break
    raise AssertionError('KNOWLEDGE_EXACT_SOURCE_LOCK_WAIT_NOT_OBSERVED')


def _selection_under_source_lock(scope, registration, source_version, source_id, *, expires_at=None):
    """원 부모행은 수정하지 않고 canon owner의 정확 Source FOR UPDATE와 app 함수를 충돌시킨다."""
    from threading import Event, Thread
    from time import monotonic
    from psycopg.pq import TransactionStatus

    context = scope.context
    ready, done = Event(), Event()
    outcome = {}
    arguments = (context.tenant_id, context.workspace_id, context.notebook_id,
        context.actor_id, registration, source_version)
    setting_names = ('app.tenant_id', 'app.workspace_id', 'app.actor_id', 'app.capability')

    def worker():
        try:
            with psycopg.connect(scope.app, connect_timeout=3, autocommit=True) as connection:
                outcome['pid'] = connection.info.backend_pid
                before = tuple(connection.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0]
                    for name in setting_names)
                try:
                    # 최초 SQL보다 먼저 명시 transaction을 연다. 실패를 savepoint만으로 회수하지 않는다.
                    with connection.transaction():
                        connection.execute("SELECT set_config('lock_timeout',%s,true)",
                            ('1500ms' if expires_at is None else '4000ms',))
                        connection.execute("SET LOCAL statement_timeout='5000ms'")
                        with _account_notification_scope(connection, tenant_id=context.tenant_id,
                            workspace_id=context.workspace_id, actor_id=context.actor_id) as reader:
                            assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
                            assert _current_auth_grant(context, reader, Action.QUERY).allowed is True
                            assert _current_auth_grant(context, reader, Action.VIEW).allowed is True
                            ready.set()
                            outcome['result'] = reader.execute(_CALL, arguments).fetchone()
                except psycopg.Error as error:
                    # 오류 본문/연결정보를 보관하거나 출력하지 않는다.
                    outcome['sqlstate'] = error.sqlstate
                outcome['idle'] = connection.info.transaction_status == TransactionStatus.IDLE
                after = tuple(connection.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0]
                    for name in setting_names)
                outcome['scope_restored'] = tuple(value or '' for value in before) == tuple(value or '' for value in after)
        except BaseException as error:
            # thread 오류도 조용히 통과시키지 않으며 민감한 repr는 저장하지 않는다.
            outcome['worker_error_type'] = type(error).__name__
        finally:
            done.set()

    thread = Thread(target=worker, daemon=True)
    started = False
    with psycopg.connect(scope.admin, connect_timeout=3, autocommit=True) as observer, \
            psycopg.connect(scope.admin, connect_timeout=3, autocommit=True) as holder:
        try:
            with holder.transaction():
                holder.execute('SET LOCAL ROLE daon_notebook_delete')
                holder.execute("SET LOCAL statement_timeout='5000ms'")
                with _account_notification_scope(holder, tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id, actor_id=context.actor_id) as reader:
                    assert reader.execute('SELECT record_id FROM public.sources '
                        'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s FOR UPDATE',
                        (context.tenant_id, context.workspace_id, source_id)).fetchone() == (source_id,)
                    thread.start()
                    started = True
                    assert ready.wait(3), 'KNOWLEDGE_LOCK_WORKER_NOT_READY'
                    _wait_for_source_lock(observer, outcome['pid'], holder.info.backend_pid, done)
                    advisory_key = '|'.join(('notebook-delete', context.tenant_id,
                        context.workspace_id, context.notebook_id))
                    assert observer.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                        (advisory_key,)).fetchone() == (False,)
                    if expires_at is None:
                        # Source 잠금을 계속 유지한 상태에서 timeout에 의한 전체 rollback/선행 잠금 해제를 확인한다.
                        assert done.wait(4), 'KNOWLEDGE_LOCK_TIMEOUT_NOT_RETURNED'
                        assert outcome.get('sqlstate') == '55P03'
                        assert 'result' not in outcome
                        assert outcome.get('idle') is True and outcome.get('scope_restored') is True
                        assert observer.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                            (advisory_key,)).fetchone() == (True,)
                    else:
                        checked_at = observer.execute('SELECT clock_timestamp()').fetchone()[0]
                        assert 0 < (expires_at - checked_at).total_seconds() <= 2, \
                            'SHORT_EXPIRY_FIXTURE_EXPIRED_BEFORE_OBSERVED_LOCK_WAIT'
                        deadline = monotonic() + 2.2
                        while observer.execute('SELECT clock_timestamp()').fetchone()[0] < expires_at:
                            assert monotonic() < deadline, 'SHORT_EXPIRY_DATABASE_CLOCK_WAIT_EXCEEDED'
                            assert not done.wait(0.01), 'KNOWLEDGE_LOCK_RETURNED_BEFORE_SOURCE_RELEASE'
                        assert not done.is_set()
            # 위 holder transaction이 끝난 뒤에만 Source가 풀린다.
        finally:
            if started:
                thread.join(timeout=6)
                assert not thread.is_alive(), 'KNOWLEDGE_LOCK_WORKER_NOT_TERMINATED'
    assert 'worker_error_type' not in outcome, outcome.get('worker_error_type')
    assert done.is_set() and outcome.get('idle') is True and outcome.get('scope_restored') is True
    if expires_at is not None:
        assert 'sqlstate' not in outcome
        assert outcome.get('result') == (False,)


def test_question_knowledge_lock_exact_source_conflict_times_out_and_rolls_back_whole_transaction():
    """실제 app 함수의 FOR SHARE 대기→55P03와 transaction 잠금/GUC 해제 및 부모 불변을 확인한다."""
    prepared = _l2b_registered_knowledge_case(registration_count=1)
    scope = prepared.scope
    registration, source_version, source_id = prepared.registrations[0][:3]
    baseline = _parent_boundary(scope)
    _selection_under_source_lock(scope, registration, source_version, source_id)
    assert _parent_boundary(scope) == baseline


def test_question_knowledge_lock_rechecks_database_clock_after_source_wait_and_returns_false_on_expiry():
    """새 실제 등록의 최초 2초 기한만 준비하며, 대기 후 DB 시각 재검사 False를 확인한다."""
    from datetime import timedelta
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository

    prepared = _l2b_registered_knowledge_case(registration_count=1)
    scope, context = prepared.scope, prepared.scope.context
    payload = dict(prepared.registrations[0][6])
    payload['package_id'] = 's42-lock-expiring-package-' + scope.suffix
    store = PostgresCloudStore(scope.app, min_size=0, max_size=1, notification_inbox_enabled=True)
    try:
        with psycopg.connect(scope.admin, connect_timeout=3, autocommit=True) as observer:
            expires_at = observer.execute('SELECT clock_timestamp()').fetchone()[0] + timedelta(seconds=2)
            payload['expires_at'] = expires_at.isoformat()
            result, replayed = PostgresStudioWorkspaceRepository(store).record_action(context,
                'knowledge_registration', payload, 's42-lock-expiring-registration-' + scope.suffix)
            assert replayed is False and result['status'] == 'registered' and result['searchable'] is True
            row = observer.execute('SELECT kr.record_id,sv.record_id,sv.source_id,kr.canonical_json '
                'FROM public.knowledge_registrations kr JOIN public.source_versions sv '
                'ON sv.tenant_id=kr.tenant_id AND sv.workspace_id=kr.workspace_id '
                'AND sv.record_id=kr.registered_source_version_id '
                'WHERE kr.tenant_id=%s AND kr.workspace_id=%s AND kr.record_id=%s',
                (context.tenant_id, context.workspace_id, result['record_id'])).fetchone()
            assert row is not None and row[1] == result['registered_source_version_id'] and row[3] == payload
        baseline = _parent_boundary(scope)
        _selection_under_source_lock(scope, *row[:3], expires_at=expires_at)
        assert _parent_boundary(scope) == baseline
    finally:
        store.close()
