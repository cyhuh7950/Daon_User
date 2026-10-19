"""L2-D 내부 tick의 실제 소유 QA 저장 경계. HTTP/정식 worker/Provider 시험이 아니다.

핵심/종류별 실패 격리와 S24/lifespan 시험이며 runtime 제품과 실행은 main 소유다.
직접 tick은 자기 scope 바로 앞 cursor와 limit=1로 제한하고, lifespan은 실제 선두
scope 확인 후 자기 중앙 ack 직후 stop하여 전역 잔여 범위의 소비를 방지한다.
"""
from pathlib import Path
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import psycopg

from daon_user_api import runtime
from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.notification_postgres import PostgresNotificationRepository
from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
from test_notification_events import _pending_l2a_delivery, _pending_l2b_completed, _studio_case_egress


_DISCOVER = 'SELECT * FROM public.pending_notification_dispatch_scopes(%s,%s,%s,%s)'


def _scope_snapshot(scope):
    """소유 QA의 관리자 연결로 자기 합성 범위의 저장 결과만 확인한다."""
    context = scope.context
    with psycopg.connect(scope.admin, connect_timeout=3) as connection:
        scope_args = (context.tenant_id, context.workspace_id)
        return {
            'source': connection.execute('SELECT source_event_id,origin_event_id,state,version '
                'FROM public.notification_source_outbox WHERE tenant_id=%s AND workspace_id=%s '
                'ORDER BY source_event_id', scope_args).fetchall(),
            'notifications': connection.execute('SELECT notification_id,recipient_id,version,read_at '
                'FROM public.notifications WHERE tenant_id=%s AND workspace_id=%s '
                'ORDER BY notification_id', scope_args).fetchall(),
            'details': connection.execute('SELECT d.notification_id,d.source_event_id '
                'FROM public.notification_details d JOIN public.notifications n '
                'ON n.tenant_id=d.tenant_id AND n.notification_id=d.notification_id '
                'WHERE n.tenant_id=%s AND n.workspace_id=%s '
                'ORDER BY d.notification_id', scope_args).fetchall(),
            'audit': connection.execute('SELECT event_id,state,version,fingerprint,draft '
                'FROM public.notification_audit_outbox WHERE tenant_id=%s AND workspace_id=%s '
                'ORDER BY event_id', scope_args).fetchall(),
            'central': connection.execute('SELECT event_id,event_hash FROM public.security_audit_events '
                'WHERE tenant_id=%s AND workspace_id=%s ORDER BY event_id', scope_args).fetchall(),
            'origin': connection.execute('SELECT event_id,xmin::text FROM public.audit_events '
                'WHERE tenant_id=%s AND workspace_id=%s ORDER BY event_id', scope_args).fetchall(),
        }


def _tick():
    # 정상 실제 원업무/source/발견 SQL 준비 뒤 제품 미연결을 명시적으로 분류한다.
    method = getattr(runtime, '_notification_dispatch_tick', None)
    assert callable(method), 'NOTIFICATION_DISPATCH_RUNTIME_NOT_IMPLEMENTED'
    return method


def test_notification_dispatch_requires_current_ready_0072_before_discovery():
    """현재 DB를 옛 revision으로 오인해 실제 알림 발견을 막는 회귀를 잡는다."""
    import pytest
    from daon_user_api.notification import NotificationError

    class DiscoveryReached(RuntimeError):
        pass

    for ready,revision in ((True,'0072'),(True,'0069'),(True,'0071'),(True,'0073'),(False,'0072')):
        cloud=SimpleNamespace(_notification_inbox_enabled=True,_dsn='postgresql://unused.invalid/unused',
            readiness=lambda:SimpleNamespace(ready=ready,schema_revision=revision))
        dependencies=SimpleNamespace(settings=SimpleNamespace(notification_inbox_enabled=True),cloud_store=cloud)
        with patch('psycopg.connect',side_effect=DiscoveryReached) as connect:
            if ready and revision=='0072':
                with pytest.raises(DiscoveryReached):
                    runtime._notification_dispatch_tick(dependencies,stop=Event(),limit=1)
                assert connect.call_count==1
            else:
                with pytest.raises(NotificationError) as failure:
                    runtime._notification_dispatch_tick(dependencies,stop=Event(),limit=1)
                assert failure.value.code=='DATABASE_UNAVAILABLE'
                assert connect.call_count==0


def test_notification_dispatch_tick_real_scope_materializes_and_retries_central_after_restart_without_duplicate():
    """실제 새 source→알림 확정과 중앙 SQL 실패 pending→새 저장소 재시도를 구분한다."""
    scope = _pending_l2a_delivery()
    context = scope.context
    exact_scope = (context.tenant_id, context.workspace_id, context.actor_id)
    # 같은 tenant/Workspace 안의 유일한 실제 actor 바로 앞에서 한 scope만 읽는다.
    # Python 정렬로 DB cursor를 재정렬하거나 다른 QA의 잔여 scope를 소비하지 않는다.
    before_scope = (context.tenant_id, context.workspace_id, context.actor_id[:-1])
    with psycopg.connect(scope.app, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
        assert connection.execute(_DISCOVER, (1, *before_scope)).fetchall() == [exact_scope]
    baseline = _scope_snapshot(scope)
    assert baseline['source'] == [(scope.source_event_id, scope.origin_event_id, 'pending', 1)]
    assert baseline['notifications'] == baseline['details'] == baseline['audit'] == baseline['central'] == []
    cloud = PostgresCloudStore(scope.app, min_size=0, max_size=1, notification_inbox_enabled=True)
    audit = PostgresSecurityAuditStore(scope.app, min_size=0, max_size=1)
    try:
        ready = cloud.readiness()
        assert ready.ready is True and ready.schema_revision == '0069'
        # scope는 DB가 반환하며 사용자 Principal/가상 소속을 만들지 않는다.
        # namespace는 tick 구성 포트일 뿐 인증·SQL·소비 성공을 대체하지 않는다.
        dependencies = SimpleNamespace(
            settings=runtime.RuntimeSettings(profile='test', bind_host='127.0.0.1', port=8000,
                database_path=Path('unused-notification-dispatch-test.db'),
                notification_inbox_enabled=True, policy_version=context.policy_version),
            cloud_store=cloud, audit_store=audit,
            studio_approval_lifecycle_repository=StudioApprovalLifecycleRepository(cloud),
        )
        tick = _tick()
        sqlstates = []

        def actual_central_sql_failure(_draft):
            # 알림 확정 후 별도 중앙 connection의 실제 실패다. 성공 대역/가짜 ack가 아니다.
            with audit._pool.connection(timeout=2) as connection:
                try:
                    connection.execute('SELECT 1/0')
                except psycopg.Error as error:
                    sqlstates.append(error.sqlstate)
                    raise

        audit._ensure_open()
        with patch.object(audit, '_append_admin_role', side_effect=actual_central_sql_failure):
            cursor = tick(dependencies, stop=Event(), after=before_scope,
                projection_cursors={}, limit=1)
        assert cursor == exact_scope
        assert sqlstates == ['22012']
        pending = _scope_snapshot(scope)
        assert pending['source'] == [(scope.source_event_id, scope.origin_event_id, 'delivered', 2)]
        assert len(pending['notifications']) == len(pending['details']) == len(pending['audit']) == 1
        notification_id = pending['notifications'][0][0]
        assert pending['notifications'][0][1:] == (context.actor_id, 1, None)
        assert pending['details'] == [(notification_id, scope.source_event_id)]
        assert pending['audit'][0][1:3] == ('pending', 1) and pending['central'] == []
        assert pending['origin'] == baseline['origin']
        assert pending['audit'][0][4]['action'] == 'notification.created'
        assert pending['audit'][0][4]['target_id'] == notification_id
        # 중앙 pending만 남은 같은 실제 scope를 재시작 때도 다시 발견해야 한다.
        with psycopg.connect(scope.app, connect_timeout=3) as connection:
            assert connection.execute(_DISCOVER, (1, *before_scope)).fetchall() == [exact_scope]
        audit.close()
        audit = PostgresSecurityAuditStore(scope.app, min_size=0, max_size=1)
        dependencies.audit_store = audit
        assert tick(dependencies, stop=Event(), after=before_scope,
            projection_cursors={}, limit=1) == exact_scope
        delivered = _scope_snapshot(scope)
        assert delivered['source'] == pending['source']
        assert delivered['notifications'] == pending['notifications']
        assert delivered['details'] == pending['details'] and delivered['origin'] == baseline['origin']
        assert len(delivered['audit']) == len(delivered['central']) == 1
        assert delivered['audit'][0][:3] == (pending['audit'][0][0], 'delivered', 2)
        assert delivered['audit'][0][3:] == pending['audit'][0][3:]
        assert delivered['central'][0][0] == pending['audit'][0][0]
        # GET 재진입에 준비/ack를 붙이지 않는다. 실제 현재 읽기 전후 원장 전체가 같다.
        repository = PostgresNotificationRepository(scope.app, context=CloudAccessContext(*exact_scope, 'view'))
        items = repository.notifications_for(tenant_id=context.tenant_id, recipient_id=context.actor_id)
        assert len(items) == 1 and items[0].notification_id == notification_id
        assert repository.notification_for(tenant_id=context.tenant_id, recipient_id=context.actor_id,
            notification_id=notification_id) == items[0]
        assert _scope_snapshot(scope) == delivered
    finally:
        audit.close()
        cloud.close()


def test_notification_dispatch_tick_s24_real_hundred_id_cursor_eof_pop_and_restart_from_none():
    """101개 원notice를 실제 저장하고 ID page100 이후 runtime의 마지막 준비/EOF/재시작을 확인한다."""
    from daon_user_api.notification_studio_approval import StudioApprovalNotificationRepository
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from test_notification_studio_approval import _s24_two_unprojected_withdrawn_notices

    scope, _ = _s24_two_unprojected_withdrawn_notices()
    context = scope.context
    exact_scope = (context.tenant_id, context.workspace_id, context.actor_id)
    before_scope = (context.tenant_id, context.workspace_id, context.actor_id[:-1])
    old_store = PostgresCloudStore(scope.app, min_size=0, max_size=1)
    try:
        native = StudioApprovalLifecycleRepository(old_store)
        studio = PostgresStudioWorkspaceRepository(old_store)
        for index in range(2, 101):
            request, replayed = studio.record_action(context, 'approval_request', {
                'output_version_id': scope.version, 'review_request_id': 's24-review-' + scope.suffix,
            }, 's42-dispatch-s24-request-' + str(index))
            assert replayed is False and request['status'] == 'accepted'
            withdrawn, replayed = native.withdraw(context, request['record_id'], scope.version,
                's42-dispatch-s24-withdraw-' + str(index))
            assert replayed is False and withdrawn['status'] == 'withdrawn'
    finally:
        old_store.close()

    def native_rows():
        with psycopg.connect(scope.admin, connect_timeout=3) as connection:
            return connection.execute('SELECT notification_id,read_at,version,created_at,xmin::text '
                'FROM public.studio_approval_notices WHERE tenant_id=%s AND workspace_id=%s '
                'AND recipient_id=%s ORDER BY notification_id', exact_scope).fetchall()

    original = native_rows()
    assert len(original) == 101 and all(row[1:3] == (None, 1) for row in original)
    ids = [row[0] for row in original]  # DB ID 순서 그대로이며 생성 시각 watermark를 쓰지 않는다.
    before = _scope_snapshot(scope)
    assert before['source'] == before['notifications'] == before['details'] == before['audit'] == before['central'] == []
    cloud = PostgresCloudStore(scope.app, min_size=0, max_size=1, notification_inbox_enabled=True)
    audit = PostgresSecurityAuditStore(scope.app, min_size=0, max_size=1)
    try:
        lifecycle = StudioApprovalLifecycleRepository(cloud)
        repository = StudioApprovalNotificationRepository(lifecycle, context=CloudAccessContext(*exact_scope, 'view'))
        trace = 'trace-s24-runtime-first-page-' + scope.suffix
        first = repository.prepare_projections(limit=100, after=None,
            trace_id=trace, policy_version=context.policy_version)
        assert first == dict(scanned=100, prepared=100, already=0, unavailable=0, failed=0,
            next_cursor=ids[99], eof=False)
        assert native_rows() == original
        first_state = _scope_snapshot(scope)
        assert len(first_state['audit']) == 100 and first_state['central'] == []
        dependencies = SimpleNamespace(
            settings=runtime.RuntimeSettings(profile='test', bind_host='127.0.0.1', port=8000,
                notification_inbox_enabled=True, policy_version=context.policy_version),
            cloud_store=cloud, audit_store=audit, studio_approval_lifecycle_repository=lifecycle,
        )
        with psycopg.connect(scope.app, connect_timeout=3) as connection:
            assert connection.execute(_DISCOVER, (1, *before_scope)).fetchall() == [exact_scope]
        # scope 1개만 소비한다. 별도 실제 page100의 cursor를 넘겨 뒤 정상 notice가 가려지지 않는지 본다.
        cursors = {exact_scope: first['next_cursor']}
        assert _tick()(dependencies, stop=Event(), after=before_scope, projection_cursors=cursors, limit=1) == exact_scope
        assert exact_scope not in cursors
        with psycopg.connect(scope.admin, connect_timeout=3) as connection:
            created = connection.execute('SELECT notification_id,source_event_id,draft->>\'trace_id\','
                "draft->'after'->>'kind',state FROM public.notification_audit_outbox "
                "WHERE tenant_id=%s AND workspace_id=%s AND recipient_id=%s AND action='notification.created' "
                'ORDER BY notification_id', exact_scope).fetchall()
        assert len(created) == 101
        assert [(row[0], row[1]) for row in created] == [(notice_id, notice_id) for notice_id in ids]
        assert all(row[2] == trace and row[3] == 'approval' for row in created[:100])
        assert created[-1][2].startswith('trace-notification-dispatch-') and created[-1][3] == 'approval'
        state = _scope_snapshot(scope)
        assert len(state['central']) == 1 and sum(row[1] == 'delivered' for row in state['audit']) == 1
        # 새 내부 cursor 저장소로 재시작: 같은 원notice를 처음부터 읽되 안정 event ID/저장 내용은 그대로다.
        restarted_cursors = {}
        assert _tick()(dependencies, stop=Event(), after=before_scope,
            projection_cursors=restarted_cursors, limit=1) == exact_scope
        assert restarted_cursors == {exact_scope: ids[0]}
        restarted = _scope_snapshot(scope)
        assert len(restarted['audit']) == 101 and len(restarted['central']) == 2
        assert [(row[0], *row[3:]) for row in restarted['audit']] == [(row[0], *row[3:]) for row in state['audit']]
        assert restarted['origin'] == before['origin'] and native_rows() == original
        assert restarted['source'] == restarted['notifications'] == restarted['details'] == []
    finally:
        audit.close()
        cloud.close()


def _first_owned_pending_delivery():
    """원 QA 검증 뒤 새 선두 scope만 준비한다. 전역 잔여행을 수정하거나 복사하지 않는다."""
    import os
    from uuid import uuid4
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from daon_user_api.studio_workspace import StudioContext
    from test_notification_events import _PrivateDsn
    from test_studio_approval_lifecycle_migration import seed_delivery
    import pytest

    dsn = os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not dsn:
        pytest.skip('DAON_NOTIFICATION_TEST_DSN 미제공: 실제 PG 미실행')
    dsn = _PrivateDsn(dsn)
    assert conninfo_to_dict(dsn).get('dbname') == 'daon_user_s42_qa_20261017_l2'
    with psycopg.connect(dsn, connect_timeout=3) as connection:
        assert connection.execute('SELECT current_database(),(SELECT version_num FROM alembic_version)').fetchone() == (
            'daon_user_s42_qa_20261017_l2', '0069')
    suffix = uuid4().hex
    context = StudioContext('000-s42-dispatch-' + suffix, 's42-dispatch-workspace-' + suffix,
        's42-dispatch-actor-' + suffix, 'trace-dispatch-lifespan-' + suffix,
        'runtime-policy-v1', 's42-dispatch-notebook-' + suffix)
    scope = SimpleNamespace(admin=dsn, app=_PrivateDsn(make_conninfo(dsn, options='-c role=daon_app')),
        context=context, suffix=suffix, revision='0069')
    scope.version, scope.approval, scope.original = seed_delivery(scope)
    with psycopg.connect(dsn, connect_timeout=3) as connection:
        connection.execute('INSERT INTO public.identity_tenants(tenant_id) VALUES(%s)', (context.tenant_id,))
        connection.execute("INSERT INTO public.identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'personal_owner')",
            (context.tenant_id, context.actor_id))
    return _pending_l2a_delivery(scope)


def test_notification_dispatch_real_lifespan_thread_start_stop_restart_resets_cursors_without_duplicate(tmp_path):
    """실제 ASGI lifespan/thread와 PG를 재생성한다. OS 프로세스/배포/60초 지연 자체의 증거는 아니다."""
    import asyncio
    from dataclasses import replace
    from daon_user_api.egress_policy import EgressPolicyContext, EgressPolicyPayload, EgressPolicyService
    from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
    from daon_user_api.notification_events import _account_notification_scope
    from daon_user_api import notification_events as events

    scope = _first_owned_pending_delivery()
    context = scope.context
    exact_scope = (context.tenant_id, context.workspace_id, context.actor_id)
    original_tick = _tick()

    async def one_start(index):
        # 먼저 실제 발견 SQL의 첫 scope를 검증한다. 불일치면 다른 범위를 처리하기 전에 준비 실패다.
        with psycopg.connect(scope.app, connect_timeout=3) as connection:
            assert connection.execute(_DISCOVER, (1, None, None, None)).fetchall() == [exact_scope], \
                'OWNED_SCOPE_MUST_BE_FIRST_BEFORE_LIFESPAN_START'
        settings = runtime.RuntimeSettings.for_test(database_path=tmp_path / ('runtime-' + str(index) + '.sqlite'),
            policy_version=context.policy_version)
        # 제어용 SQLite 서비스도 실제 구현이다. DB 알림 source/소비/중앙 저장은 아래 실제 PG다.
        dependencies = runtime.build_dependencies(settings)
        dependencies.settings = replace(settings, notification_inbox_enabled=True)
        dependencies.cloud_store = PostgresCloudStore(scope.app, min_size=0, max_size=1, notification_inbox_enabled=True)
        dependencies.studio_approval_lifecycle_repository = StudioApprovalLifecycleRepository(dependencies.cloud_store)
        finished = Event()
        observed = {}

        audit = PostgresSecurityAuditStore(scope.app, min_size=0, max_size=1)
        dependencies.audit_store = audit
        actual_ack = events._ack_notification_audit

        def observe_committed_ack(*args, **kwargs):
            current = kwargs['context']
            assert (current.tenant_id, current.workspace_id, current.actor_id) == exact_scope
            try:
                result = actual_ack(*args, **kwargs)
                observed['acknowledged'] = result
                observed['central_event_id'] = kwargs['event_id']
                return result
            finally:
                # 실제 중앙 저장/별도 ack 경계가 끝난 뒤 shutdown 시점만 제어한다.
                # 실제 반환값/SQL 오류는 그대로 전달하며 성공 대역을 만들지 않는다.
                observed['stop'].set()

        def observe_real_tick(actual_dependencies, **kwargs):
            assert actual_dependencies is dependencies
            assert 'after' not in observed, '한 lifespan에서 제어한 tick 한 번만 실행되어야 한다'
            observed['after'] = kwargs['after']
            observed['projection_cursors'] = dict(kwargs['projection_cursors'])
            observed['stop'] = kwargs['stop']
            try:
                return original_tick(actual_dependencies, **kwargs)
            finally:
                finished.set()

        try:
            # observer는 실제 함수를 그대로 실행한다. 60초 상수/권한/SQL/HTTP를 대역으로 바꾸지 않는다.
            with patch.object(runtime, '_notification_dispatch_tick', new=observe_real_tick), \
                    patch.object(events, '_ack_notification_audit', new=observe_committed_ack):
                app = runtime.create_app(dependencies)
                async with app.router.lifespan_context(app):
                    assert await asyncio.to_thread(finished.wait, 30), 'NOTIFICATION_LIFESPAN_TICK_NOT_FINISHED'
                    assert observed['after'] is None and observed['projection_cursors'] == {}
                    assert observed['central_event_id'] and observed['acknowledged'] is True
                assert observed['stop'].is_set() and dependencies._closed is True
        finally:
            if 'stop' in observed:
                observed['stop'].set()
                assert finished.is_set() or await asyncio.to_thread(finished.wait, 8), \
                    'NOTIFICATION_TICK_STILL_ACTIVE_STORES_NOT_CLOSED_UNDER_WORKER'
            if finished.is_set() or 'stop' not in observed:
                dependencies.close()
                audit.close()
        return _scope_snapshot(scope)

    first = asyncio.run(one_start(1))
    assert first['source'] == [(scope.source_event_id, scope.origin_event_id, 'delivered', 2)]
    assert len(first['notifications']) == len(first['details']) == len(first['audit']) == len(first['central']) == 1
    assert first['audit'][0][1:3] == ('delivered', 2)
    # 재기동할 새 정상 사건도 실제 서비스 저장이다. 이전 source/알림/원감사는 변경하지 않는다.
    payload = EgressPolicyPayload.deny_external()
    with psycopg.connect(scope.admin, connect_timeout=3) as connection:
        with _account_notification_scope(connection, tenant_id=context.tenant_id,
            workspace_id=context.workspace_id, actor_id=context.actor_id, enabled=False):
            _studio_case_egress(connection, scope, 'workspace', payload.as_dict(), 1)
    store = PostgresCloudStore(scope.app, min_size=0, max_size=1, notification_inbox_enabled=True)
    try:
        policy_context = EgressPolicyContext(context.tenant_id, context.tenant_id, context.workspace_id,
            context.actor_id, context.trace_id, context.policy_version)
        policy_repository = PostgresEgressPolicyRepository(store)
        current = policy_repository.current(policy_context, 'workspace')
        stored = EgressPolicyService(policy_repository).create_and_activate(policy_context, scope_type='workspace',
            payload=payload, expected_etag=current.etag, idempotency_key='s42-dispatch-lifespan-policy-' + scope.suffix)
        assert stored.policy_version == 2
    finally:
        store.close()
    second = asyncio.run(one_start(2))
    assert len(second['source']) == len(second['notifications']) == len(second['details']) == len(second['audit']) == len(second['central']) == 2
    assert all(row[2:] == ('delivered', 2) for row in second['source'])
    assert all(row[1:3] == ('delivered', 2) for row in second['audit'])
    assert all(row in second['notifications'] for row in first['notifications'])
    assert all(row in second['details'] for row in first['details'])
    assert all(row in second['audit'] for row in first['audit'])
    assert all(row in second['central'] for row in first['central'])


def test_notification_dispatch_tick_completed_sql_failure_keeps_pending_and_other_kind_delivers_and_acks():
    """한 scope의 실제 completed 삽입 후 SQL rollback이 정상 정책 알림/중앙 전달을 막지 않는다."""
    from daon_user_api.egress_policy import EgressPolicyContext, EgressPolicyPayload, EgressPolicyService
    from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
    from daon_user_api.notification_events import _account_notification_scope

    scope = _pending_l2b_completed('document')
    context = scope.context
    exact_scope = (context.tenant_id, context.workspace_id, context.actor_id)
    before_scope = (context.tenant_id, context.workspace_id, context.actor_id[:-1])
    payload = EgressPolicyPayload.deny_external()
    # 버전1만 합성 초기 metadata다. 아래 버전2/원감사/source는 실제 서비스가 같은 Tx에 저장한다.
    with psycopg.connect(scope.admin, connect_timeout=3) as connection:
        with _account_notification_scope(connection, tenant_id=context.tenant_id,
            workspace_id=context.workspace_id, actor_id=context.actor_id, enabled=False):
            _studio_case_egress(connection, scope, 'workspace', payload.as_dict(), 1)
    cloud = PostgresCloudStore(scope.app, min_size=0, max_size=1, notification_inbox_enabled=True)
    audit = PostgresSecurityAuditStore(scope.app, min_size=0, max_size=1)
    try:
        policy_context = EgressPolicyContext(context.tenant_id, context.tenant_id, context.workspace_id,
            context.actor_id, context.trace_id, context.policy_version)
        policy_repository = PostgresEgressPolicyRepository(cloud)
        current = policy_repository.current(policy_context, 'workspace')
        assert current.policy_version == current.binding_version == 1 and current.payload == payload
        stored = EgressPolicyService(policy_repository).create_and_activate(policy_context,
            scope_type='workspace', payload=payload, expected_etag=current.etag,
            idempotency_key='s42-dispatch-kind-isolation-policy-' + scope.suffix)
        assert stored.policy_version == stored.binding_version == 2 and stored.payload == payload
        with psycopg.connect(scope.admin, connect_timeout=3) as connection:
            policy_sources = connection.execute('SELECT s.source_event_id,s.origin_event_id,s.recipient_id,'
                's.state,s.version,a.target_id,s.occurred_at=a.occurred_at '
                'FROM public.notification_source_outbox s JOIN public.audit_events a '
                'ON a.tenant_id=s.tenant_id AND a.workspace_id=s.workspace_id AND a.event_id=s.origin_event_id '
                "WHERE s.tenant_id=%s AND s.workspace_id=%s AND s.action='egress_policy.activate'",
                (context.tenant_id, context.workspace_id)).fetchall()
            assert len(policy_sources) == 1
            policy_source = policy_sources[0][0]
            assert policy_sources[0][2:] == (context.actor_id, 'pending', 1, stored.binding_id, True)
        baseline = _scope_snapshot(scope)
        assert len(baseline['source']) == 2
        assert all(row[2:] == ('pending', 1) for row in baseline['source'])
        assert baseline['notifications'] == baseline['details'] == baseline['audit'] == baseline['central'] == []
        with psycopg.connect(scope.app, connect_timeout=3) as connection:
            assert connection.execute(_DISCOVER, (1, *before_scope)).fetchall() == [exact_scope]
        dependencies = SimpleNamespace(
            settings=runtime.RuntimeSettings(profile='test', bind_host='127.0.0.1', port=8000,
                notification_inbox_enabled=True, policy_version=context.policy_version),
            cloud_store=cloud, audit_store=audit,
            studio_approval_lifecycle_repository=StudioApprovalLifecycleRepository(cloud),
        )
        ready = cloud.readiness()
        assert ready.ready is True and ready.schema_revision == '0069'
        tick = _tick()
        actual_connect = psycopg.connect
        phases, sqlstates = [], []

        @contextmanager
        def fail_only_completed_after_insert(*args, **kwargs):
            with actual_connect(*args, **kwargs) as actual:
                class ObservedConnection:
                    def __getattr__(self, name):
                        return getattr(actual, name)

                    def execute(self, query, params=None):
                        cursor = actual.execute(query, params)
                        if query == 'SELECT public.materialize_completed_notification(%s,%s)':
                            assert params[0] == scope.source_event_id
                            result = cursor.fetchone()[0]
                            assert result is not None and result['state'] == 'delivered'
                            # 앱의 private outbox 직접 SELECT 권한을 만들지 않는다.
                            parents = actual.execute('SELECT n.notification_id,d.resource_id '
                                'FROM public.notifications n JOIN public.notification_details d '
                                'ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id '
                                'WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s',
                                exact_scope).fetchall()
                            assert parents == [(result['notification_id'], scope.completed_run)]
                            assert actual.execute("SELECT draft->>'action',draft->>'target_id' "
                                'FROM public.pending_notification_audits(100)').fetchall() == [
                                    ('notification.created', result['notification_id'])]
                            phases.append('ACTUAL_COMPLETED_DETAIL_AND_CREATED_INTENT')
                            try:
                                actual.execute('SELECT 1/0')
                            except psycopg.Error as error:
                                sqlstates.append(error.sqlstate)
                                raise
                        return cursor

                yield ObservedConnection()

        with patch('psycopg.connect', new=fail_only_completed_after_insert):
            assert tick(dependencies, stop=Event(), after=before_scope,
                projection_cursors={}, limit=1) == exact_scope
        assert phases == ['ACTUAL_COMPLETED_DETAIL_AND_CREATED_INTENT'] and sqlstates == ['22012']
        partial = _scope_snapshot(scope)
        states = {row[0]: row[2:] for row in partial['source']}
        assert states == {scope.source_event_id: ('pending', 1), policy_source: ('delivered', 2)}
        assert partial['origin'] == baseline['origin']
        assert len(partial['notifications']) == len(partial['details']) == len(partial['audit']) == len(partial['central']) == 1
        policy_notification = partial['notifications'][0][0]
        assert partial['details'] == [(policy_notification, policy_source)]
        assert partial['audit'][0][1:3] == ('delivered', 2)
        assert partial['central'][0][0] == partial['audit'][0][0]
        # 실패 종류만 실제 pending으로 재발견된다. 실패 주입 제거 뒤 정상 source 재소비/중앙 전달한다.
        assert tick(dependencies, stop=Event(), after=before_scope,
            projection_cursors={}, limit=1) == exact_scope
        final = _scope_snapshot(scope)
        assert all(row[2:] == ('delivered', 2) for row in final['source']) and len(final['source']) == 2
        assert len(final['notifications']) == len(final['details']) == len(final['audit']) == len(final['central']) == 2
        assert all(row[1:3] == ('delivered', 2) for row in final['audit'])
        assert {row[0] for row in final['central']} == {row[0] for row in final['audit']}
        assert (policy_notification, policy_source) in final['details']
        assert {row[1] for row in final['details']} == {scope.source_event_id, policy_source}
        assert final['origin'] == baseline['origin']
    finally:
        audit.close()
        cloud.close()
