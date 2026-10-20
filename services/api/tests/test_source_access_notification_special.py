"""S42 특별 SourceACL producer RED. 소유 QA0069 실행/DDL은 main만 담당한다.

기존 claim/complete가 저장한 실제 law/web snapshot을 사용한다. 원문 및 Object는
합성 metadata fixture이며 외부 fetch/Provider/Object 전송 증거가 아니다.
원본 fixture의 다른 DB용 pg/seed/migration은 실행하지 않는다.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from daon_user_api.audit import PostgresSecurityAuditStore
from daon_user_api.authorization import AuthorizationService
from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.egress_policy import EgressPolicyContext, EgressPolicyPayload
from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.internet_connector import WebsiteImportContext
from daon_user_api.notebook import NotebookContext
from daon_user_api.notebook_postgres import PostgresNotebookRepository
from daon_user_api.notification import NotificationError
from daon_user_api.notification_events import (
    _account_view_allowed,
    _snapshot_read_scope,
    _website_snapshot_current,
)
from daon_user_api.open_law_source_access import (
    OpenLawAccessError,
    current_open_law_policy,
    require_open_law_source_access,
)
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
from daon_user_api.notification_postgres import PostgresNotificationRepository, _SELECT, _time

from test_notification_events import _existing_0069_studio_case, _studio_case_member, _studio_case_special_source
from test_source_access_notification_events import (
    _ACTION,
    _SourceAccessCase,
    _business_snapshot,
    _grant,
    _initial_state,
    _primary_audits,
)
from test_source_access_notification_consumer import (
    _consumer,
    _list,
    _notification_service,
    _read_arguments,
    _snapshot as _consumer_snapshot,
    _target,
)


@pytest.fixture
def source_access_special_case(request, monkeypatch):
    kind = request.param
    assert kind in ('law', 'website')
    scope = _existing_0069_studio_case()
    ctx = scope.context
    with psycopg.connect(scope.admin, connect_timeout=3) as connection:
        assert connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database "
            'WHERE datname=current_database()').fetchone() == (
                'S42 L2a disposable QA 2026-10-17 main owned; remove after tests; no production data',)
    if kind == 'law':
        # 기존 환경 gate를 시험 프로세스/새 QA Workspace에만 설정하고 종료 시 monkeypatch가 복원한다.
        monkeypatch.setenv('DAON_OPEN_LAW_ENABLED', 'true')
        monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID', ctx.workspace_id)
    stored = _studio_case_special_source(scope, kind)
    store = PostgresCloudStore(scope.app, min_size=0, max_size=1, notification_inbox_enabled=True)
    audit = PostgresSecurityAuditStore(scope.app, min_size=0, max_size=1)
    repository = None
    try:
        repository = PostgresAuthorizationRepository(scope.app)
        service = AuthorizationService(repository=repository, audit_store=audit,
            clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=True)
        special_context = WebsiteImportContext(ctx.tenant_id, ctx.workspace_id, ctx.notebook_id,
            ctx.actor_id, ctx.trace_id, ctx.policy_version)
        # 서버 내부 principal DTO다. 실제 HTTP 인증/세션/모델 호출 증거는 아니다.
        principal = IdentityPrincipal(ctx.actor_id, 's42-acl-special-port-session',
            's42-acl-special-port-device', ctx.tenant_id)
        case = _SourceAccessCase(scope=scope, context=ctx, kind=kind, store=store, audit=audit,
            repository=repository, service=service, principal=principal, special_context=special_context,
            source_id=stored.source_id, version_id=stored.source_version_id, snapshot_id=stored.snapshot_id)
        snapshot = _snapshot_metadata(case)
        assert snapshot[:5] == (stored.snapshot_id, stored.source_id, stored.source_version_id,
            ctx.notebook_id, ctx.actor_id)
        assert _current_special_access(case)
        yield case
    finally:
        if repository is not None:
            repository.close()
        audit.close()
        store.close()


def _snapshot_metadata(case):
    """원 snapshot의 안전한 식별/FK/digest/xmin만 읽는다. 제목/URL/법령·웹 원문은 반환하지 않는다."""
    table = 'open_law_source_snapshots' if case.kind == 'law' else 'website_source_snapshots'
    ctx = case.context
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        rows = connection.execute(f'SELECT snapshot_id,source_id,source_version_id,notebook_id,created_by,'
            f'raw_sha256,text_sha256,xmin::text FROM public.{table} WHERE tenant_id=%s '
            'AND workspace_id=%s AND source_version_id=%s',
            (ctx.tenant_id, ctx.workspace_id, case.version_id)).fetchall()
        assert len(rows) == 1
        return rows[0]


def _current_special_access(case, *, actor_id=None, notebook_id=None):
    ctx = case.context
    special = replace(case.special_context,
        actor_id=ctx.actor_id if actor_id is None else actor_id,
        notebook_id=ctx.notebook_id if notebook_id is None else notebook_id)
    cloud = CloudAccessContext(ctx.tenant_id, ctx.workspace_id, special.actor_id, 'view')
    with case.store._transaction(cloud) as connection:
        assert _account_view_allowed(connection, cloud)
        from daon_user_api.document_index_postgres import _source_access_allowed
        if not _source_access_allowed(connection, cloud, case.version_id):
            return False
        if case.kind == 'website':
            return _website_snapshot_current(connection, special, case.version_id)
        try:
            with _snapshot_read_scope(connection, special):
                require_open_law_source_access(special, case.version_id, connection)
        except OpenLawAccessError as error:
            # fixture DB 장애를 권한 거부 PASS로 바꾸지 않는다.
            cause, seen = error, set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, psycopg.Error):
                    raise cause
                cause = cause.__cause__ or cause.__context__
            if connection.info.transaction_status == TransactionStatus.INERROR:
                pytest.fail('SPECIAL_SOURCE_ACCESS_DATABASE_FAILURE', pytrace=False)
            return False
        return True


def _assert_acl_saved(case, recipient, allowed, before):
    ctx = case.context
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT user_id,source_version_id,allowed,version FROM public.identity_auth_source_access '
            'WHERE tenant_id=%s AND workspace_id=%s', (ctx.tenant_id, ctx.workspace_id)).fetchall() == [
                (recipient, case.version_id, int(allowed), 1)]
        assert connection.execute('SELECT acl_version,version FROM public.identity_auth_workspaces '
            'WHERE tenant_id=%s AND workspace_id=%s', (ctx.tenant_id, ctx.workspace_id)).fetchone() == (
                case.initial_acl_version + 1, case.initial_workspace_version)
    roots = _primary_audits(case)
    assert len(roots) == 1
    root = roots[0]
    expected = dict(tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id, actor_id=ctx.actor_id,
        actor_type='user', action=_ACTION, target_type='source_access', target_id=case.version_id,
        outcome='succeeded', trace_id=ctx.trace_id, policy_version=ctx.policy_version,
        before_value=None, after_value={'allowed': allowed, 'version': 1}, metadata={})
    assert {key: root[key] for key in expected} == expected
    assert root['occurred_at'].tzinfo is not None
    after = _business_snapshot(case)
    for table in before:
        if table not in {'identity_auth_source_access', 'identity_auth_workspaces', 'notification_source_outbox'}:
            assert after[table] == before[table]
    return root, after


@pytest.mark.parametrize('source_access_special_case', ('law', 'website'), indirect=True)
def test_special_source_access_grant_binds_original_snapshot_notebook_atomically(
    source_access_special_case, monkeypatch,
):
    """정상 snapshot도 무조건 발행0이거나 ambient Notebook을 원 Notebook으로 쓰면 실패한다."""
    case = source_access_special_case
    ctx = case.context
    original_snapshot = _snapshot_metadata(case)
    notebook_context = NotebookContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, ctx.trace_id, ctx.policy_version)
    other, replayed = PostgresNotebookRepository(case.store).create(notebook_context,
        title='S42 SourceACL independent notebook', description=None,
        idempotency_key='s42-acl-special-other-notebook', request_fingerprint=hashlib.sha256(b's42-acl-special-other-notebook').hexdigest(),
        now=datetime.now(timezone.utc))
    assert replayed is False and other.notebook_id != ctx.notebook_id
    assert not _current_special_access(case, notebook_id=other.notebook_id)
    assert _current_special_access(case)
    before, roots = _initial_state(case)
    assert roots == set()
    real_transaction = case.repository.transaction
    scopes_after = []

    @contextmanager
    def transaction_with_unrelated_notebook():
        with real_transaction() as connection:
            raw = connection._conn
            raw.execute("SELECT set_config('app.notebook_id',%s,true)", (other.notebook_id,))
            yield connection
            scopes_after.append(raw.execute("SELECT current_setting('app.notebook_id',true)").fetchone()['current_setting'])

    with monkeypatch.context() as patch:
        patch.setattr(case.repository, 'transaction', transaction_with_unrelated_notebook)
        assert _grant(case) == 1
    assert scopes_after == [other.notebook_id]
    root, _ = _assert_acl_saved(case, ctx.actor_id, True, before)
    expected_source_id = 'source-acl-event-' + hashlib.sha256(
        f"{root['event_id']}|{ctx.workspace_id}|{ctx.actor_id}".encode()).hexdigest()[:32]
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        rows = connection.execute('SELECT tenant_id,workspace_id,recipient_id,source_event_id,origin_event_id,'
            'source_store,action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,'
            'occurred_at,state,version,delivered_at FROM public.notification_source_outbox WHERE tenant_id=%s '
            'AND workspace_id=%s AND source_store=%s AND action=%s AND target_id=%s',
            (ctx.tenant_id, ctx.workspace_id, 'authorization', _ACTION, case.version_id)).fetchall()
        assert rows == [dict(tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id, recipient_id=ctx.actor_id,
            source_event_id=expected_source_id, origin_event_id=root['event_id'], source_store='authorization',
            action=_ACTION, target_type='source_access', target_id=case.version_id, kind='membership', severity='info',
            notebook_id=ctx.notebook_id, trace_id=ctx.trace_id, policy_version=ctx.policy_version,
            occurred_at=root['occurred_at'], state='pending', version=1, delivered_at=None)], 'SPECIAL_SOURCE_ACL_INTENT_MISSING'
        assert connection.execute('SELECT a.xmin=w.xmin AND w.xmin=s.xmin AS same_transaction,'
            'r.xmin<>s.xmin AS separate_central FROM public.identity_auth_source_access a '
            'JOIN public.identity_auth_workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=a.workspace_id '
            'JOIN public.notification_source_outbox s ON s.tenant_id=a.tenant_id AND s.workspace_id=a.workspace_id '
            'AND s.recipient_id=a.user_id AND s.target_id=a.source_version_id '
            'JOIN public.security_audit_events r ON r.tenant_id=s.tenant_id AND r.event_id=s.origin_event_id '
            'WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.user_id=%s AND a.source_version_id=%s '
            'AND s.source_event_id=%s', (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, case.version_id,
                expected_source_id)).fetchall() == [{'same_transaction': True, 'separate_central': True}]
    assert _snapshot_metadata(case) == original_snapshot


@pytest.mark.parametrize('source_access_special_case,boundary', (
    ('law', 'non_owner'), ('website', 'non_owner'),
    ('law', 'explicit_acl_deny'), ('website', 'explicit_acl_deny'),
    ('law', 'connector_unavailable'), ('law', 'egress_denied'),
), indirect=['source_access_special_case'])
def test_special_source_access_current_rejection_keeps_acl_success_and_source_zero(
    source_access_special_case, boundary,
):
    """현재 특별자료 거부를 raw fallback하거나 ACL 저장 전체 거부로 바꾸면 실패한다."""
    case = source_access_special_case
    ctx = case.context
    original_snapshot = _snapshot_metadata(case)
    recipient = ctx.actor_id
    allowed = boundary != 'explicit_acl_deny'
    if boundary == 'non_owner':
        recipient = _studio_case_member(case.scope, role='viewer')
        if case.kind == 'law':
            # 기존 정상 fixture와 같은 QA tenant role로 egress approver를 충족시킨다.
            # snapshot/Notebook owner는 바꾸지 않아 owner 거부 하나를 분리한다. global role 변경은 없다.
            with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
                connection.execute('INSERT INTO public.identity_auth_tenant_roles '
                    "VALUES(%s,%s,'personal_owner','active',1,now())", (ctx.tenant_id, recipient))
            peer = replace(case.special_context, actor_id=recipient)
            with case.store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, recipient, 'view')) as connection:
                assert current_open_law_policy(peer, connection).mode == 'allow_approved_external'
        assert not _current_special_access(case, actor_id=recipient)
    elif boundary == 'connector_unavailable':
        with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
            assert connection.execute("UPDATE public.workspace_connectors SET status='unavailable' WHERE tenant_id=%s "
                "AND workspace_id=%s AND connector_id='mcp-open-law-go-kr' AND status='connected' RETURNING status",
                (ctx.tenant_id, ctx.workspace_id)).fetchall() == [('unavailable',)]
        assert not _current_special_access(case)
    elif boundary == 'egress_denied':
        # 기존 정책 버전 저장 API를 같은 실제 연결에 조립한다. 불변 정책 본문 UPDATE/검사 대역은 없다.
        with case.store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'egress_policy.write')) as connection:
            class SameConnectionStore:
                _notification_inbox_enabled = False

                @contextmanager
                def _transaction(self, _context):
                    yield connection

            policies = PostgresEgressPolicyRepository(SameConnectionStore())
            policy_context = EgressPolicyContext(ctx.tenant_id, ctx.tenant_id, ctx.workspace_id,
                ctx.actor_id, ctx.trace_id, ctx.policy_version)
            previous = policies.current(policy_context, 'workspace')
            stored = policies.create_and_activate(policy_context, 'workspace', EgressPolicyPayload.deny_external(),
                previous.etag, 's42-source-acl-special-policy-deny')
            assert stored.policy_version == previous.policy_version + 1 and stored.payload.mode == 'deny_external'
        assert not _current_special_access(case)
    else:
        assert _current_special_access(case)

    before, roots = _initial_state(case)
    assert roots == set()
    assert _grant(case, recipient_id=recipient, allowed=allowed) == 1
    _, after = _assert_acl_saved(case, recipient, allowed, before)
    assert after['notification_source_outbox'] == before['notification_source_outbox']
    assert not _current_special_access(case, actor_id=recipient)
    assert _snapshot_metadata(case) == original_snapshot


def _assert_special_materialized(case, original):
    """raw의 notebook=None 가정 없이 실제 snapshot FK와 알림 detail을 대조한다."""
    ctx = case.context
    notification_id = 'notification-' + hashlib.sha256(
        f'{ctx.tenant_id}|{ctx.workspace_id}|{ctx.actor_id}|{original.source_id}'.encode()).hexdigest()[:32]
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        rows = connection.execute('SELECT n.notification_id,n.created_at,n.recipient_id,d.source_store,d.origin_event_id,'
            'd.source_event_id,d.notebook_id,d.resource_type,d.resource_id,d.title,d.summary,d.kind,d.severity,d.deep_link,'
            'd.trace_id,d.policy_version,s.notebook_id AS source_notebook,s.state AS source_state,'
            's.occurred_at AS source_occurred_at,s.delivered_at,a.draft,'
            'n.xmin=d.xmin AND d.xmin=s.xmin AND s.xmin=a.xmin AS same_transaction '
            'FROM public.notifications n JOIN public.notification_details d ON d.tenant_id=n.tenant_id '
            'AND d.notification_id=n.notification_id JOIN public.notification_source_outbox s ON s.tenant_id=n.tenant_id '
            'AND s.workspace_id=n.workspace_id AND s.recipient_id=n.recipient_id AND s.source_event_id=n.source_event_id '
            'JOIN public.notification_audit_outbox a ON a.tenant_id=n.tenant_id AND a.notification_id=n.notification_id '
            "AND a.action='notification.created' WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s "
            'AND n.source_event_id=%s', (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, original.source_id)).fetchall()
        assert len(rows) == 1
        row = rows[0]
        expected = dict(notification_id=notification_id, recipient_id=ctx.actor_id, source_store='authorization',
            origin_event_id=original.root['event_id'], source_event_id=original.source_id,
            notebook_id=ctx.notebook_id, resource_type='source_access', resource_id=case.version_id,
            title='자료 접근권한 변경', summary='본인의 자료 접근권한이 변경되었습니다.', kind='membership',
            severity='info', deep_link='/notifications', trace_id=ctx.trace_id, policy_version=ctx.policy_version,
            source_notebook=ctx.notebook_id, source_state='delivered', source_occurred_at=original.root['occurred_at'])
        assert {name: row[name] for name in expected} == expected
        assert row['same_transaction'] is True
        assert row['delivered_at'] is not None and row['created_at'] >= row['delivered_at']
        draft = row['draft']
        assert (draft['actor_id'], draft['actor_type'], draft['tenant_id'], draft['workspace_id'],
            _time(draft['occurred_at']), draft['action'], draft['target_type'], draft['target_id'], draft['outcome'],
            draft['trace_id'], draft['policy_version'], draft['before'], draft['after'], draft['metadata']) == (
                'notification-service', 'service', ctx.tenant_id, ctx.workspace_id, row['created_at'],
                'notification.created', 'notification', notification_id, 'succeeded', ctx.trace_id, ctx.policy_version,
                None, {'recipient_id': ctx.actor_id, 'kind': 'membership', 'delivery_state': 'delivered'},
                {'source_event_id': original.source_id})
    return notification_id


@pytest.mark.parametrize('source_access_special_case', ('law', 'website'), indirect=True)
def test_special_source_access_materializes_reads_replays_and_hides_after_current_revocation(source_access_special_case):
    """특별 Notebook pending을 503으로 막거나 저장 후 현재 접근 철회를 무시하면 실패한다."""
    case = source_access_special_case
    ctx = case.context
    original_snapshot = _snapshot_metadata(case)
    before, roots = _initial_state(case)
    assert roots == set() and _grant(case) == 1
    root, _ = _assert_acl_saved(case, ctx.actor_id, True, before)
    source_id = 'source-acl-event-' + hashlib.sha256(
        f"{root['event_id']}|{ctx.workspace_id}|{ctx.actor_id}".encode()).hexdigest()[:32]
    original = SimpleNamespace(recipient=ctx.actor_id, root=root, source_id=source_id,
        context=CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'view'))
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT origin_event_id,recipient_id,target_type,target_id,notebook_id,state,version '
            'FROM public.notification_source_outbox WHERE tenant_id=%s AND workspace_id=%s AND source_event_id=%s',
            (ctx.tenant_id, ctx.workspace_id, source_id)).fetchall() == [
                (root['event_id'], ctx.actor_id, 'source_access', case.version_id, ctx.notebook_id, 'pending', 1)]
        assert connection.execute('SELECT a.xmin=w.xmin AND w.xmin=s.xmin FROM public.identity_auth_source_access a '
            'JOIN public.identity_auth_workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=a.workspace_id '
            'JOIN public.notification_source_outbox s ON s.tenant_id=a.tenant_id AND s.workspace_id=a.workspace_id '
            'AND s.recipient_id=a.user_id AND s.target_id=a.source_version_id WHERE a.tenant_id=%s '
            'AND a.workspace_id=%s AND a.user_id=%s AND a.source_version_id=%s AND s.source_event_id=%s',
            (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, case.version_id, source_id)).fetchall() == [(True,)]
    assert _target(case, original)['accessible'] is True  # 기존 exact14키 계약도 재사용한다.
    assert _consumer().materialize_pending_source_access(case.scope.app, context=original.context) == 1
    notification_id = _assert_special_materialized(case, original)
    delivered = _consumer_snapshot(case)
    assert _consumer().materialize_pending_source_access(case.scope.app, context=original.context) == 0
    assert _consumer_snapshot(case) == delivered
    assert _snapshot_metadata(case) == original_snapshot

    service, repository, authorization_repository, principal = _notification_service(case, original)
    try:
        page = _list(service, principal, case)
        assert len(page.items) == page.unread_count == 1
        item = page.items[0]
        assert (item.notification_id, item.source_event_id, item.resource_type, item.resource_id, item.deep_link) == (
            notification_id, source_id, 'source_access', case.version_id, '/notifications')
        assert service.get_notification(principal=principal, notification_id=notification_id,
            trace_id=ctx.trace_id, policy_version=ctx.policy_version) == item
        # 공개 DTO에 Notebook 필드를 더하지 않고 실제 _SELECT의 private provenance를 확인한다.
        with case.store._transaction(original.context) as connection:
            row = connection.cursor(row_factory=dict_row).execute(_SELECT + ' AND n.notification_id=%s',
                (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, notification_id)).fetchone()
            assert row['_notebook_id'] == original_snapshot[3] == ctx.notebook_id
            assert (row['_origin_event_id'], row['_detail_source_event_id']) == (root['event_id'], source_id)
        assert _consumer_snapshot(case) == delivered
        arguments = _read_arguments(case, original, item, principal)
        read = service.mark_read(**arguments)
        assert read.version == 2 and read.read_at is not None
        after_read = _consumer_snapshot(case)
        assert service.mark_read(**arguments) == read
        assert _consumer_snapshot(case) == after_read
    finally:
        authorization_repository.close()

    # 새 repository/연결로 재진입한다. principal은 합성 서비스 포트이며 로그인/브라우저 증거가 아니다.
    service, reopened, authorization_repository, principal = _notification_service(case, original)
    try:
        assert reopened.notifications_for(tenant_id=ctx.tenant_id, recipient_id=ctx.actor_id) == (read,)
        assert reopened.notification_for(tenant_id=ctx.tenant_id, recipient_id=ctx.actor_id, notification_id=notification_id) == read
        assert service.mark_read(**dict(arguments, principal=principal)) == read
        assert _consumer_snapshot(case) == after_read
        with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT action,count(*) FROM public.notification_audit_outbox '
                'WHERE tenant_id=%s AND notification_id=%s GROUP BY action ORDER BY action',
                (ctx.tenant_id, notification_id)).fetchall() == [('notification.created', 1), ('notification.read', 1)]

        if case.kind == 'law':
            with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
                assert connection.execute("UPDATE public.workspace_connectors SET status='unavailable' WHERE tenant_id=%s "
                    "AND workspace_id=%s AND connector_id='mcp-open-law-go-kr' AND status='connected' RETURNING status",
                    (ctx.tenant_id, ctx.workspace_id)).fetchall() == [('unavailable',)]
        else:
            notebook_context = NotebookContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, ctx.trace_id, ctx.policy_version)
            notebooks = PostgresNotebookRepository(case.store)
            selected = notebooks.read_selected_context(notebook_context, ctx.notebook_id)
            assert (case.source_id, case.version_id) in selected.sources
            changed, replayed = notebooks.unbind_source(notebook_context, ctx.notebook_id,
                source_id=case.source_id, source_version_id=case.version_id, expected_etag=selected.etag,
                idempotency_key='s42-acl-special-notice-unbind', request_fingerprint=hashlib.sha256(
                    b's42-acl-special-notice-unbind').hexdigest(), now=datetime.now(timezone.utc))
            assert replayed is False and changed.status == 'unbound'
        assert not _current_special_access(case)
        assert _snapshot_metadata(case) == original_snapshot
        assert _target(case, original)['accessible'] is False
        revoked = _consumer_snapshot(case)
        fresh = PostgresNotificationRepository(case.scope.app, context=original.context)
        assert fresh.notifications_for(tenant_id=ctx.tenant_id, recipient_id=ctx.actor_id) == ()
        assert fresh.notification_for(tenant_id=ctx.tenant_id, recipient_id=ctx.actor_id, notification_id=notification_id) is None
        page = _list(service, principal, case)
        assert page.items == () and page.unread_count == 0
        with pytest.raises(NotificationError) as detail_denied:
            service.get_notification(principal=principal, notification_id=notification_id,
                trace_id=ctx.trace_id, policy_version=ctx.policy_version)
        assert (detail_denied.value.code, detail_denied.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        with pytest.raises(NotificationError) as replay_denied:
            service.mark_read(**dict(arguments, principal=principal))
        assert (replay_denied.value.code, replay_denied.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        assert _consumer_snapshot(case) == revoked
        assert _snapshot_metadata(case) == original_snapshot
    finally:
        authorization_repository.close()
