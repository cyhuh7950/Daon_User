"""S42/S24 단일 알림 원장의 직접 PG 경계. 기존 실제 요청을 처리한다."""
from __future__ import annotations

import psycopg
import pytest

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
from test_notification_events import _existing_0069_studio_case


def _s24_two_unprojected_withdrawn_notices():
    """동일 scope에서 실제 회수→새 요청→회수한다. notice/projection을 직접 만들지 않는다."""
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository

    scope = _existing_0069_studio_case(pending=True)
    context = scope.context
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2)
    try:
        lifecycle = StudioApprovalLifecycleRepository(store)
        first, replayed = lifecycle.withdraw(
            context, scope.request_id, scope.version, 's42-s24-batch-withdraw-first')
        assert replayed is False and first['status'] == 'withdrawn'
        request, replayed = PostgresStudioWorkspaceRepository(store).record_action(
            context, 'approval_request', {
                'output_version_id': scope.version,
                'review_request_id': 's24-review-' + scope.suffix,
            }, 's42-s24-batch-request-second')
        assert replayed is False and request['status'] == 'accepted'
        assert request['record_id'] != scope.request_id
        second, replayed = lifecycle.withdraw(
            context, request['record_id'], scope.version, 's42-s24-batch-withdraw-second')
        assert replayed is False and second['status'] == 'withdrawn'
        notices = lifecycle.list_notices(context)['items']
        assert len(notices) == 2
        assert all(item['event_kind'] == 'withdrawn' and item['version'] == 1
                   and item['read_at'] is None for item in notices)
        assert len({item['notification_id'] for item in notices}) == 2
    finally:
        store.close()
    return scope, notices


@pytest.mark.parametrize('batch_limit', [2, 1], ids=['same-batch-continues', 'limit-one-pages'])
def test_s24_prepare_projections_rolls_back_failed_insert_advances_and_wraps(monkeypatch, batch_limit):
    """실제 SQL INSERT 뒤 22012만 주입한다. 현재 Source/권한검사와 SAVEPOINT는 실제 구현이다."""
    from contextlib import contextmanager
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification_studio_approval import StudioApprovalNotificationRepository

    scope, notices = _s24_two_unprojected_withdrawn_notices()
    context = scope.context
    first_id, second_id = sorted(item['notification_id'] for item in notices)

    def snapshot():
        with psycopg.connect(scope.admin) as connection:
            return {table: connection.execute(
                f'SELECT to_jsonb(r),r.xmin::text FROM public.{table} r '
                'WHERE tenant_id=%s ORDER BY to_jsonb(r)::text', (context.tenant_id,)).fetchall()
                for table in ('studio_approval_notices', 'approval_requests', 'output_versions',
                    'notebook_bindings', 'canon_state_transitions', 'idempotency_records',
                    'audit_events', 'notification_audit_outbox', 'notifications',
                    'notification_details', 'notification_source_outbox')}

    before = snapshot()
    for table in ('notification_audit_outbox', 'notifications', 'notification_details',
                  'notification_source_outbox'):
        assert before[table] == []
    original_rows = {table: rows for table, rows in before.items() if table != 'notification_audit_outbox'}
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    try:
        lifecycle = StudioApprovalLifecycleRepository(store)
        repository = StudioApprovalNotificationRepository(lifecycle, context=CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, 'view'))
        options = dict(tenant_id=context.tenant_id, recipient_id=context.actor_id)
        assert repository.notifications_for(**options) == ()
        assert snapshot() == before
        real_transaction = store._transaction
        observed = []

        class InsertObserver:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, query, params=None):
                cursor = self.connection.execute(query, params)
                if (query == "SELECT public.record_studio_notice_notification(%s,'notification.created',%s,%s,NULL)"
                        and params[0] == first_id and not observed):
                    assert cursor.fetchone() == (True,), '실제 INSERT 성공 뒤에만 실패를 주입한다'
                    stored = self.connection.execute(
                        'SELECT public.current_studio_notice_notification_audit(%s)', (first_id,)).fetchone()
                    assert stored is not None and stored[0] is not None
                    assert stored[0]['trace_id'] == first_trace
                    assert stored[0]['policy_version'] == context.policy_version
                    try:
                        self.connection.execute('SELECT 1/0')
                    except psycopg.Error as error:
                        assert error.sqlstate == '22012'
                        observed.append((first_id, stored[0]['audit_event_id'], error.sqlstate))
                        raise
                    pytest.fail('실제 SQL 오류가 발생해야 한다')
                return cursor

        @contextmanager
        def observe_transaction(cloud_context):
            with real_transaction(cloud_context) as connection:
                yield InsertObserver(connection)

        first_trace = 'trace-s24-batch-first-' + scope.suffix
        second_trace = 'trace-s24-batch-next-' + scope.suffix
        retry_trace = 'trace-s24-batch-wrap-' + scope.suffix
        with monkeypatch.context() as patch:
            patch.setattr(store, '_transaction', observe_transaction)
            first = repository.prepare_projections(limit=batch_limit, after=None,
                trace_id=first_trace, policy_version=context.policy_version)
            if batch_limit == 2:
                assert first == dict(scanned=2, prepared=1, already=0, unavailable=0, failed=1,
                    next_cursor=None, eof=True)
                successful_trace = first_trace
            else:
                assert first == dict(scanned=1, prepared=0, already=0, unavailable=0, failed=1,
                    next_cursor=first_id, eof=False)
                # 실패한 첫 건도 cursor를 전진시켜 뒤의 정상 원notice를 처리한다.
                assert snapshot() == before
                next_page = repository.prepare_projections(limit=1, after=first['next_cursor'],
                    trace_id=second_trace, policy_version=context.policy_version)
                assert next_page == dict(scanned=1, prepared=1, already=0, unavailable=0, failed=0,
                    next_cursor=None, eof=True)
                successful_trace = second_trace
            eof_page = first if batch_limit == 2 else next_page

        assert len(observed) == 1 and observed[0][0] == first_id and observed[0][2] == '22012'

        def assert_projection_state(expected_traces):
            state = snapshot()
            assert {table: rows for table, rows in state.items()
                    if table != 'notification_audit_outbox'} == original_rows
            assert len(state['notification_audit_outbox']) == len(expected_traces)
            with psycopg.connect(scope.admin) as connection:
                rows = connection.execute(
                    'SELECT action,notification_id,source_event_id,draft,event_id FROM notification_audit_outbox '
                    'WHERE tenant_id=%s AND workspace_id=%s AND recipient_id=%s ORDER BY notification_id',
                    (context.tenant_id, context.workspace_id, context.actor_id)).fetchall()
                assert [row[1] for row in rows] == sorted(expected_traces)
                assert len({row[4] for row in rows}) == len(rows)
                for action, notice_id, source_id, draft, _event_id in rows:
                    assert (action, source_id) == ('notification.created', notice_id)
                    if notice_id == first_id:
                        assert _event_id == observed[0][1], '재시도도 동일한 안정 감사 ID를 사용한다'
                    assert draft['trace_id'] == expected_traces[notice_id]
                    assert draft['policy_version'] == context.policy_version
                    assert draft['after']['kind'] == 'approval'
                    assert draft['metadata']['source_event_id'] == draft['metadata']['notice_id'] == notice_id
            assert {item.notification_id for item in repository.notifications_for(**options)} == set(expected_traces)
            for notice in notices:
                item = repository.notification_for(**options, notification_id=notice['notification_id'])
                if notice['notification_id'] not in expected_traces:
                    assert item is None
                else:
                    assert item is not None
                    assert (item.notification_id, item.source_event_id, item.version, item.read_at) == (
                        notice['notification_id'], notice['notification_id'], notice['version'], notice['read_at'])
            assert lifecycle.list_notices(context)['items'] == notices
            assert snapshot() == state, '목록·단건·Native 조회는 새 projection을 만들면 안 된다'
            return state

        # 실제 실패건 INSERT만 rollback됐고, 뒤의 정상 건은 outer transaction에서 commit됐다.
        assert_projection_state({second_id: successful_trace})
        assert eof_page['next_cursor'] is None and eof_page['eof'] is True
        retry = repository.prepare_projections(limit=batch_limit, after=eof_page['next_cursor'],
            trace_id=retry_trace, policy_version=context.policy_version)
        if batch_limit == 2:
            assert retry == dict(scanned=2, prepared=1, already=1, unavailable=0, failed=0,
                next_cursor=None, eof=True)
        else:
            assert retry == dict(scanned=1, prepared=1, already=0, unavailable=0, failed=0,
                next_cursor=first_id, eof=False)
            last_page = repository.prepare_projections(limit=1, after=retry['next_cursor'],
                trace_id=retry_trace, policy_version=context.policy_version)
            assert last_page == dict(scanned=1, prepared=0, already=1, unavailable=0, failed=0,
                next_cursor=None, eof=True)
        after_retry = assert_projection_state({first_id: retry_trace, second_id: successful_trace})
        # 다음 sweep은 두 원notice를 모두 재확인해도 신규 감사/이력 쓰기가 0이다.
        cursor = None
        for index in range(2 if batch_limit == 1 else 1):
            again = repository.prepare_projections(limit=batch_limit, after=cursor,
                trace_id='trace-s24-batch-again-' + scope.suffix, policy_version=context.policy_version)
            assert again == dict(scanned=batch_limit, prepared=0, already=batch_limit,
                unavailable=0, failed=0, next_cursor=first_id if batch_limit == 1 and index == 0 else None,
                eof=batch_limit == 2 or index == 1)
            cursor = again['next_cursor']
            assert snapshot() == after_retry
        assert cursor is None
        assert len(observed) == 1
    finally:
        store.close()


@pytest.mark.parametrize('read_first', [False, True], ids=['unread', 'native-already-read'])
def test_s24_prepare_projections_materializes_old_notice_without_rewriting_native_history(read_first):
    """OFF 원notice를 명시 준비할 때만 현재 created 감사를 추가한다. 과거 read는 합성하지 않는다."""
    from datetime import datetime
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification_studio_approval import StudioApprovalNotificationRepository

    scope = _existing_0069_studio_case(pending=True)
    context = scope.context
    old_store = PostgresCloudStore(scope.app, min_size=0, max_size=2)
    try:
        native = StudioApprovalLifecycleRepository(old_store)
        native.withdraw(context, scope.request_id, scope.version, 's42-s24-old-withdraw-001')
        notice = native.list_notices(context)['items'][0]
        if read_first:
            native.mark_notice_read(context, notice['notification_id'], 1, 's42-s24-old-native-read')
            notice = native.list_notices(context)['items'][0]
    finally:
        old_store.close()

    def snapshot():
        with psycopg.connect(scope.admin) as connection:
            return {table: connection.execute(f'SELECT to_jsonb(r) FROM public.{table} r '
                'WHERE tenant_id=%s ORDER BY to_jsonb(r)::text', (context.tenant_id,)).fetchall()
                for table in ('studio_approval_notices', 'approval_requests', 'canon_state_transitions',
                    'idempotency_records', 'audit_events', 'notification_audit_outbox',
                    'notifications', 'notification_details', 'notification_source_outbox')}

    before = snapshot()
    assert before['notification_audit_outbox'] == []
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    try:
        lifecycle = StudioApprovalLifecycleRepository(store)
        repository = StudioApprovalNotificationRepository(lifecycle, context=CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, 'view'))
        options = dict(tenant_id=context.tenant_id, recipient_id=context.actor_id)
        assert repository.notifications_for(**options) == ()
        assert repository.notification_for(**options, notification_id=notice['notification_id']) is None
        assert snapshot() == before, '조회가 구 notice projection을 생성하면 안 된다'

        trace = 'trace-s24-explicit-prepare-' + scope.suffix
        prepared = repository.prepare_projections(limit=1, after=None, trace_id=trace, policy_version=context.policy_version)
        assert prepared == dict(scanned=1, prepared=1, already=0, unavailable=0, failed=0, next_cursor=None, eof=True)
        after = snapshot()
        assert {key: value for key, value in after.items() if key != 'notification_audit_outbox'} == {
            key: value for key, value in before.items() if key != 'notification_audit_outbox'}
        with psycopg.connect(scope.admin) as connection:
            rows = connection.execute('SELECT a.action,a.source_event_id,a.notification_id,a.draft,n.created_at,n.read_at,n.version,'
                'n.xmin<>a.xmin FROM public.notification_audit_outbox a JOIN public.studio_approval_notices n '
                'ON n.tenant_id=a.tenant_id AND n.workspace_id=a.workspace_id AND n.notification_id=a.notification_id '
                'WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.recipient_id=%s',
                (context.tenant_id, context.workspace_id, context.actor_id)).fetchall()
            assert len(rows) == 1
            action, source_id, notification_id, draft, created, read, version, new_transaction = rows[0]
            assert (action, source_id, notification_id, new_transaction) == (
                'notification.created', notice['notification_id'], notice['notification_id'], True)
            assert draft['trace_id'] == trace and draft['policy_version'] == context.policy_version
            assert draft['after']['kind'] == 'approval'
            metadata = draft['metadata']
            assert set(metadata) == {'source_event_id', 'notice_id', 'notice_created_at', 'projection_at'}
            assert metadata['source_event_id'] == metadata['notice_id'] == notice['notification_id']
            parsed = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
            assert parsed(metadata['notice_created_at']) == created
            assert parsed(metadata['projection_at']) == parsed(draft['occurred_at']) >= created
            assert version == (2 if read_first else 1) and (read is not None) is read_first
        item = repository.notification_for(**options, notification_id=notice['notification_id'])
        assert item is not None
        assert (item.notification_id, item.source_event_id, item.version, item.read_at, item.created_at, item.trace_id) == (
            notice['notification_id'], notice['notification_id'], version, read, created, trace)
        assert lifecycle.list_notices(context)['items'] == [notice]
        # EOF의 None은 다음 호출에서 처음부터 wrap한다. 저장된 trace/감사를 덮어쓰지 않는다.
        again = repository.prepare_projections(limit=1, after=prepared['next_cursor'],
            trace_id='trace-s24-prepare-retry-' + scope.suffix, policy_version=context.policy_version)
        assert again == dict(scanned=1, prepared=0, already=1, unavailable=0, failed=0, next_cursor=None, eof=True)
        assert snapshot() == after
    finally:
        store.close()


def test_s24_prepare_projections_rejects_current_source_acl_without_new_writes():
    """정상 구notice라도 현재 Source 명시 deny이면 준비하지 않고 원행/감사를 그대로 둔다."""
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification_studio_approval import StudioApprovalNotificationRepository

    scope = _existing_0069_studio_case(pending=True)
    context = scope.context
    old_store = PostgresCloudStore(scope.app, min_size=0, max_size=2)
    try:
        native = StudioApprovalLifecycleRepository(old_store)
        native.withdraw(context, scope.request_id, scope.version, 's42-s24-old-deny-withdraw')
        notice = native.list_notices(context)['items'][0]
    finally:
        old_store.close()
    with psycopg.connect(scope.admin) as connection:
        connection.execute('INSERT INTO identity_auth_source_access '
            '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) VALUES(%s,%s,%s,%s,0,1,now())',
            (context.tenant_id, context.workspace_id, context.actor_id, scope.source_version_id))
        original = connection.execute('SELECT to_jsonb(n),n.xmin::text FROM studio_approval_notices n '
            'WHERE tenant_id=%s AND notification_id=%s', (context.tenant_id, notice['notification_id'])).fetchone()
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    try:
        repository = StudioApprovalNotificationRepository(StudioApprovalLifecycleRepository(store), context=CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, 'view'))
        result = repository.prepare_projections(limit=100, after=None, trace_id='trace-s24-denied-prepare-' + scope.suffix,
            policy_version=context.policy_version)
        assert result == dict(scanned=1, prepared=0, already=0, unavailable=1, failed=0, next_cursor=None, eof=True)
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT to_jsonb(n),n.xmin::text FROM studio_approval_notices n '
                'WHERE tenant_id=%s AND notification_id=%s', (context.tenant_id, notice['notification_id'])).fetchone() == original
            for table in ('notifications', 'notification_details', 'notification_source_outbox', 'notification_audit_outbox'):
                assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s', (context.tenant_id,)).fetchone() == (0,)
    finally:
        store.close()


def test_studio_notice_read_does_not_hide_wrapped_database_error_as_not_found(monkeypatch):
    """예외 체인 변환 단위다. 실제 DB/법령 서비스 장애 재현으로 계산하지 않는다."""
    from contextlib import contextmanager
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import pytest
    from daon_user_api.audit import ActorType, AuditEventDraft, AuditOutcome
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification import DeliveryState, Notification, NotificationError
    from daon_user_api.notification_studio_approval import StudioApprovalNotificationRepository
    from daon_user_api.studio_workspace import StudioError
    now = datetime.now(timezone.utc)
    context = CloudAccessContext('scope-tenant', 'scope-workspace', 'scope-user', 'view')
    item = Notification(notification_id='approval-notice-unit', tenant_id=context.tenant_id,
        workspace_id=context.workspace_id, recipient_id=context.actor_id, kind='approval', severity='info',
        title='승인 요청 회수', summary='본인이 승인 요청을 회수했습니다.', source_event_id='approval-notice-unit',
        resource_type='StudioApprovalNotice', resource_id='approval-notice-unit',
        deep_link='/notebooks/notebook-unit?output_version_id=version-unit', audit_event_id='audit-unit',
        trace_id='trace-unit', delivery_state=DeliveryState.DELIVERED, created_at=now, delivered_at=now,
        read_at=None, version=1)

    def fail(*_args):
        try:
            raise psycopg.errors.UndefinedTable('isolated exception-chain test')
        except psycopg.Error as error:
            raise StudioError('RESOURCE_UNAVAILABLE', 404) from error

    lifecycle = SimpleNamespace(_store=SimpleNamespace(_notification_inbox_enabled=True), mark_notice_read=fail)
    repository = StudioApprovalNotificationRepository(lifecycle, context=context)

    @contextmanager
    def read_transaction():
        yield SimpleNamespace(execute=lambda *_: SimpleNamespace(fetchone=lambda: [None]*10 + [['notebook-unit']]))

    monkeypatch.setattr(repository, '_transaction', read_transaction)
    monkeypatch.setattr(repository, '_current', lambda *_: item)
    with pytest.raises(NotificationError) as failed:
        repository.transition_read(tenant_id=context.tenant_id, recipient_id=context.actor_id,
            notification_id=item.notification_id, expected_etag=item.etag, idempotency_key='s42-s24-unit-read-001',
            fingerprint='f'*64, read_at=now, on_first_transition=lambda *_: None,
            audit_draft_factory=lambda before, after: AuditEventDraft(event_id='audit-unit-read', occurred_at=now,
                actor_id=context.actor_id, actor_type=ActorType.USER, tenant_id=context.tenant_id,
                workspace_id=context.workspace_id, action='notification.read', target_type='notification',
                target_id=item.notification_id, outcome=AuditOutcome.SUCCEEDED, trace_id='trace-unit',
                policy_version='policy-unit', metadata={'source_event_id': item.source_event_id}))
    assert (failed.value.http_status, failed.value.code) == (503, 'DATABASE_UNAVAILABLE')


def test_due_notice_read_replay_rechecks_database_state_and_time_after_access_and_receipt_lock(monkeypatch):
    """DB 시각 port를 전진시키는 분기 단위다. 실제 PG 시간 경과/기한 sweep 시험은 아니다."""
    from contextlib import contextmanager
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    import pytest
    from daon_user_api.studio_workspace import StudioContext, StudioError
    before = datetime.now(timezone.utc)
    deadline = before + timedelta(seconds=1)
    calls = []

    class Connection:
        def execute(self, query, params=None):
            if query.startswith('SELECT set_config('):
                return SimpleNamespace(fetchone=lambda: None)
            if query.startswith('SELECT r.output_version_id,'):
                return SimpleNamespace(fetchone=lambda: ('version-clock', 'due_24h', before-timedelta(hours=1),
                    'pending', deadline, before))
            if query.startswith('SELECT r.state,'):
                calls.append('current_state_and_time')
                return SimpleNamespace(fetchone=lambda: ('pending', deadline, deadline+timedelta(seconds=1)))
            pytest.fail('재요청 거부 전에 원장/감사를 변경하면 안 된다: ' + query.split()[0])

    @contextmanager
    def transaction(_context):
        yield Connection()

    repository = StudioApprovalLifecycleRepository(SimpleNamespace(
        _notification_inbox_enabled=True, _transaction=transaction))
    monkeypatch.setattr(repository, '_version_access', lambda *_: calls.append('current_source_access'))
    monkeypatch.setattr(repository, '_notification_identity_access', lambda *_: None)

    def replay(*_args):
        calls.append('receipt_lock_and_read')
        return None, 'f'*64, {'notification_id': 'approval-notice-clock', 'version': 2, 'read_at': before.isoformat()}

    monkeypatch.setattr(repository, '_replay', replay)
    context = StudioContext('scope-tenant', 'scope-workspace', 'scope-user', 'trace-clock', 'policy-clock', 'notebook-clock')
    with pytest.raises(StudioError) as denied:
        repository.mark_notice_read(context, 'approval-notice-clock', 1, 's42-s24-clock-read-001')
    assert (denied.value.code, denied.value.status) == ('RESOURCE_UNAVAILABLE', 404)
    assert calls == ['current_source_access', 'receipt_lock_and_read', 'current_state_and_time']


def test_s42_source_hook_flag_is_transaction_local_and_explicitly_off_by_default():
    """실제 pool 연결의 DB 문맥만 검증한다. trigger/notice 생성 성공 증거는 아니다."""
    scope = _existing_0069_studio_case(pending=True)
    from daon_user_api.cloud_storage import CloudAccessContext
    context = CloudAccessContext(scope.context.tenant_id, scope.context.workspace_id,
        scope.context.actor_id, 'view')
    for enabled, expected in ((False, 'false'), (True, 'true')):
        store = PostgresCloudStore(scope.app, min_size=0, max_size=1, notification_inbox_enabled=enabled)
        try:
            with store._transaction(context) as connection:
                assert connection.execute("SELECT current_setting('app.notification_inbox_enabled',true)").fetchone() == (expected,)
            with store._pool.connection() as connection:
                assert connection.execute("SELECT nullif(current_setting('app.notification_inbox_enabled',true),'')").fetchone() == (None,)
        finally:
            store.close()


def test_s24_new_notice_has_same_transaction_projection_audit_without_notification_copy():
    """실제 본인 요청 회수→notice와 created 의도만 저장하며 일반 알림 사본은 만들지 않는다."""
    scope = _existing_0069_studio_case(pending=True)
    context = scope.context
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    try:
        lifecycle = StudioApprovalLifecycleRepository(store)
        result, replayed = lifecycle.withdraw(context, scope.request_id, scope.version, 's42-s24-withdraw-001')
        assert replayed is False and result['status'] == 'withdrawn'
        notices = lifecycle.list_notices(context)
        assert notices['unread_count'] == 1 and len(notices['items']) == 1
        notice = notices['items'][0]
        assert notice['event_kind'] == 'withdrawn'
        with psycopg.connect(scope.admin) as connection:
            rows = connection.execute(
                'SELECT a.action,a.source_event_id,a.notification_id,a.recipient_id,'
                'a.draft->>\'target_id\',a.draft->>\'trace_id\',n.xmin=a.xmin,a.draft->\'after\'->>\'kind\' '
                'FROM public.notification_audit_outbox a '
                'JOIN public.studio_approval_notices n ON n.tenant_id=a.tenant_id '
                'AND n.workspace_id=a.workspace_id AND n.notification_id=a.notification_id '
                'WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.recipient_id=%s',
                (context.tenant_id, context.workspace_id, context.actor_id),
            ).fetchall()
            assert len(rows) == 1, 'S24_CREATED_PROJECTION_AUDIT_MISSING'
            action, source_id, notification_id, recipient, target, trace, atomic, kind = rows[0]
            assert (action, source_id, notification_id, recipient, target, atomic) == (
                'notification.created', notice['notification_id'], notice['notification_id'],
                context.actor_id, notice['notification_id'], True)
            assert isinstance(trace, str) and trace
            assert kind == 'approval', 'S24 공통 종류와 생성 감사의 종류가 일치해야 한다'
            for table in ('notifications', 'notification_details', 'notification_source_outbox'):
                assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',
                    (context.tenant_id,)).fetchone() == (0,), 'S24_NOTICE_MUST_NOT_BE_COPIED'
        assert lifecycle.withdraw(context, scope.request_id, scope.version, 's42-s24-withdraw-001') == (result, True)
        assert lifecycle.list_notices(context) == notices
    finally:
        store.close()


def test_s24_current_read_uses_exact_bound_notebook_and_restores_caller_context():
    """같은 실제 PG 연결의 이전 Notebook 문맥이 현재 알림을 숨기거나 오염하지 않는다."""
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification_studio_approval import StudioApprovalNotificationRepository, _SELECT
    scope = _existing_0069_studio_case(pending=True)
    context = scope.context
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    try:
        lifecycle = StudioApprovalLifecycleRepository(store)
        lifecycle.withdraw(context, scope.request_id, scope.version, 's42-s24-context-withdraw')
        current = CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, 'view')
        repository = StudioApprovalNotificationRepository(lifecycle, context=current)
        with store._transaction(current) as connection:
            rows = connection.execute(_SELECT, (context.tenant_id, context.workspace_id, context.actor_id)).fetchall()
            assert len(rows) == 1
            connection.execute("SELECT set_config('app.notebook_id','unrelated-prior-notebook',true)")
            item = repository._current(connection, rows[0])
            assert item is not None and item.deep_link.startswith('/notebooks/' + context.notebook_id + '?')
            assert connection.execute("SELECT current_setting('app.notebook_id',true)").fetchone() == ('unrelated-prior-notebook',)
    finally:
        store.close()


def test_s24_common_adapter_uses_original_notice_id_read_state_and_current_source_access():
    """공통 조회/읽음이 실제 S24 원장 하나를 사용하고 원 Source 거부 후 재노출하지 않는다."""
    from datetime import datetime, timezone
    from daon_user_api.audit import ActorType, AuditEventDraft, AuditOutcome
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification import NotificationError
    import pytest
    scope = _existing_0069_studio_case(pending=True)
    context = scope.context
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    try:
        lifecycle = StudioApprovalLifecycleRepository(store)
        lifecycle.withdraw(context, scope.request_id, scope.version, 's42-s24-union-withdraw-001')
        original = lifecycle.list_notices(context)['items'][0]
        from daon_user_api.notification_studio_approval import StudioApprovalNotificationRepository
        repository = StudioApprovalNotificationRepository(lifecycle, context=CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, 'view'))
        options = dict(tenant_id=context.tenant_id, recipient_id=context.actor_id)
        items = repository.notifications_for(**options)
        assert len(items) == 1
        item = items[0]
        assert (item.notification_id, item.source_event_id, item.version, item.read_at) == (
            original['notification_id'], original['notification_id'], original['version'], None)
        assert item.deep_link.endswith('?output_version_id=' + scope.version)
        assert repository.notification_for(**options, notification_id=item.notification_id) == item

        def audit(before, after):
            return AuditEventDraft(event_id='s42-s24-read-draft', occurred_at=after.read_at,
                actor_id=context.actor_id, actor_type=ActorType.USER, tenant_id=context.tenant_id,
                workspace_id=context.workspace_id, action='notification.read', target_type='notification',
                target_id=item.notification_id, outcome=AuditOutcome.SUCCEEDED, trace_id=context.trace_id,
                policy_version=context.policy_version, metadata={'source_event_id': item.source_event_id})

        arguments = dict(**options, notification_id=item.notification_id, expected_etag=item.etag,
            idempotency_key='s42-s24-common-read-001', fingerprint='f'*64,
            read_at=datetime.now(timezone.utc), on_first_transition=lambda *_: pytest.fail('원장 감사 경계를 우회하면 안 된다'),
            audit_draft_factory=audit)
        transition = repository.transition_read(**arguments)
        assert transition.first_transition is True and transition.notification.version == 2
        assert repository.transition_read(**arguments).first_transition is False
        notice = lifecycle.list_notices(context)['items'][0]
        assert notice['notification_id'] == item.notification_id and notice['version'] == 2 and notice['read_at'] is not None
        # 새 S24 metadata의 실제 별도 PG 감사 전달과 ack 뒤 getter 보존을 확인한다.
        from daon_user_api.audit import PostgresSecurityAuditStore
        from daon_user_api.notification_events import dispatch_pending_notification_audits
        central = PostgresSecurityAuditStore(scope.app, min_size=0, max_size=2)
        try:
            assert dispatch_pending_notification_audits(scope.app, context=context, audit_store=central) == 2
            assert dispatch_pending_notification_audits(scope.app, context=context, audit_store=central) == 0
            stored = central.read(item.audit_event_id, tenant_id=context.tenant_id)
            assert stored is not None and stored.action == 'notification.created'
            assert stored.metadata['source_event_id'] == item.notification_id
            assert stored.metadata['notice_id'] == item.notification_id
            assert set(stored.metadata) == {'source_event_id', 'notice_id', 'notice_created_at', 'projection_at'}
        finally:
            central.close()
        after_ack = repository.notification_for(**options, notification_id=item.notification_id)
        assert after_ack == transition.notification
        assert lifecycle.list_notices(context)['items'][0] == notice
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT action,count(*) FROM notification_audit_outbox '
                'WHERE tenant_id=%s GROUP BY action ORDER BY action', (context.tenant_id,)).fetchall() == [
                    ('notification.created', 1), ('notification.read', 1)]
            assert connection.execute('SELECT state,count(*) FROM notification_audit_outbox '
                'WHERE tenant_id=%s GROUP BY state', (context.tenant_id,)).fetchall() == [('delivered', 2)]
            connection.execute('INSERT INTO identity_auth_source_access '
                '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
                'VALUES(%s,%s,%s,%s,0,1,now())',
                (context.tenant_id, context.workspace_id, context.actor_id, scope.source_version_id))
        assert repository.notifications_for(**options) == ()
        assert repository.notification_for(**options, notification_id=item.notification_id) is None
        with pytest.raises(NotificationError) as denied:
            repository.transition_read(**arguments)
        assert denied.value.http_status == 404
        with psycopg.connect(scope.admin) as connection:
            for table in ('notifications', 'notification_details', 'notification_source_outbox'):
                assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',
                    (context.tenant_id,)).fetchone() == (0,)
    finally:
        store.close()


def test_s24_runtime_http_uses_one_notice_and_rolls_back_wrapped_actual_sql_failure(monkeypatch):
    """실제 PG 로그인/API·SQL 실패 경계다. 브라우저/정식 배포/실제 법령 장애 시험은 아니다."""
    from dataclasses import replace
    from datetime import datetime, timezone
    import secrets
    from fastapi.testclient import TestClient
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.authorization import AuthorizationService
    from daon_user_api.identity import DevicePlatform, IdentityService, PASSWORD_HASHER
    from daon_user_api.identity_postgres import PostgresIdentityRepository
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, WEB_SESSION_COOKIE, create_app
    from daon_user_api.studio_workspace import StudioError
    from test_account_notification_events import _PrivateValue
    scope = _existing_0069_studio_case(pending=True)
    context = scope.context
    password = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    # 이 시험이 방금 생성한 합성 사용자에만 정상 local 로그인을 준비한다.
    # 권한/Source owner를 바꾸거나 인증 우회를 사용하지 않는다. 비밀번호는 메모리에만 있다.
    with psycopg.connect(scope.admin) as connection:
        assert connection.execute("UPDATE identity_users SET issuer='local',login_id=%s,password_digest=%s,"
            'email_verified_at=now(),password_change_required=false WHERE user_id=%s AND issuer=\'s24-fixture\' RETURNING user_id',
            (context.actor_id, PASSWORD_HASHER.hash(password), context.actor_id)).fetchall() == [(context.actor_id,)]
    identity_repository = PostgresIdentityRepository(scope.app)
    authorization_repository = PostgresAuthorizationRepository(scope.app)
    audit = AuditEventStore()
    clock = lambda: datetime.now(timezone.utc)
    identity = IdentityService(repository=identity_repository, audit_store=audit, oidc_policies=(), clock=clock)
    credentials = identity.local_login(login_id=context.actor_id, password=password, platform=DevicePlatform.WEB,
        trace_id=context.trace_id, policy_version=context.policy_version)
    credentials = replace(credentials, access_token=_PrivateValue(credentials.access_token))
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    lifecycle = StudioApprovalLifecycleRepository(store)
    dependencies = RuntimeDependencies(settings=RuntimeSettings(profile='test', bind_host='127.0.0.1', port=8000,
        cloud_database_dsn=scope.app, policy_version=context.policy_version, development_stage=True,
        notification_inbox_enabled=True), identity_service=identity,
        authorization_service=AuthorizationService(repository=authorization_repository, audit_store=audit,
            clock=clock, identity_service=identity), audit_store=audit, identity_repository=identity_repository,
        authorization_repository=authorization_repository, cloud_store=store,
        studio_approval_lifecycle_repository=lifecycle)
    client = None

    def snapshot():
        with psycopg.connect(scope.admin) as connection:
            return {table: connection.execute(f'SELECT to_jsonb(r) FROM public.{table} r '
                'WHERE tenant_id=%s ORDER BY to_jsonb(r)::text', (context.tenant_id,)).fetchall()
                for table in ('studio_approval_notices', 'idempotency_records', 'audit_events',
                    'notification_audit_outbox', 'notifications', 'notification_details', 'notification_source_outbox',
                    'approval_requests', 'output_versions')}

    try:
        lifecycle.withdraw(context, scope.request_id, scope.version, 's42-s24-http-withdraw-001')
        # lifespan 미기동: 다른 QA 작업·백그라운드 처리는 시작하지 않는다.
        client = TestClient(create_app(dependencies), base_url='http://127.0.0.1')
        auth = {'cookie': f'{WEB_SESSION_COOKIE}={credentials.access_token}'}
        listed = client.get('/api/v1/notifications', headers=auth)
        assert listed.status_code == 200 and listed.json()['data']['unread_count'] == 1
        items = listed.json()['data']['items']
        assert len(items) == 1 and items[0]['resource_type'] == 'StudioApprovalNotice'
        url = '/api/v1/notifications/' + items[0]['id']
        detail = client.get(url, headers=auth)
        assert detail.status_code == 200
        headers = {**auth, 'If-Match': detail.headers['etag'], 'Idempotency-Key': 's42-s24-http-read-once'}
        before = snapshot()
        original = lifecycle._version_access
        failures = []

        def fail_on_native_read(connection, current, version, action):
            result = original(connection, current, version, action)
            if connection.execute("SELECT current_setting('app.capability',true)").fetchone()[0] == 'studio.approval.notice.read':
                try:
                    connection.execute('SELECT 1/0')
                except psycopg.Error as error:
                    failures.append(error.sqlstate)
                    raise StudioError('RESOURCE_UNAVAILABLE', 404) from error
            return result

        with monkeypatch.context() as patch:
            patch.setattr(lifecycle, '_version_access', fail_on_native_read)
            failed = client.patch(url, headers=headers, json={'state': 'read'})
        assert failures == ['22012']
        assert (failed.status_code, failed.json()['error']['code'], failed.json()['error']['retryable']) == (
            503, 'RESOURCE_UNAVAILABLE', True)
        assert 'division by zero' not in failed.text and 'SELECT 1/0' not in failed.text
        assert snapshot() == before
        read = client.patch(url, headers=headers, json={'state': 'read'})
        assert read.status_code == 200 and read.json()['data']['version'] == 2
        assert client.patch(url, headers=headers, json={'state': 'read'}).json()['data'] == read.json()['data']
        native = client.get(f'/api/v1/workspaces/{context.workspace_id}/studio/approval-notices?notebook_id={context.notebook_id}', headers=auth)
        assert native.status_code == 200
        notice = native.json()['data']['items'][0]
        assert notice['notification_id'] == items[0]['id'] and notice['version'] == 2
        assert datetime.fromisoformat(notice['read_at'].replace('Z', '+00:00')) == datetime.fromisoformat(
            read.json()['data']['read_at'].replace('Z', '+00:00'))
        assert client.get('/api/v1/notifications', headers=auth).json()['data']['unread_count'] == 0
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT action,count(*) FROM notification_audit_outbox '
                'WHERE tenant_id=%s GROUP BY action ORDER BY action', (context.tenant_id,)).fetchall() == [
                    ('notification.created', 1), ('notification.read', 1)]
            for table in ('notifications', 'notification_details', 'notification_source_outbox'):
                assert connection.execute(f'SELECT count(*) FROM public.{table} WHERE tenant_id=%s',
                    (context.tenant_id,)).fetchone() == (0,)
    finally:
        if client is not None:
            client.close()
        identity.revoke_current_web_session(access_token=credentials.access_token,
            trace_id=context.trace_id, policy_version=context.policy_version)
        dependencies.close()
