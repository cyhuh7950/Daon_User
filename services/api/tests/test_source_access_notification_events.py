"""S42 SourceACL 내부 setter RED. 실제 PG 실행/DDL/자원 수명은 main 소유다.

문서 이해 결과는 합성 fixture지만 start/complete/index, ACL 변경, 중앙 감사는
실제 저장 메서드다. Provider/Object I/O, 제품 UI/API 증거는 아니다.
law/web은 후속 필수이며 raw/non-ready metadata 시험으로 완료 처리하지 않는다.
"""
from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from daon_user_api.audit import ActorType, AuditEventDraft, AuditEventStore, AuditOutcome, PostgresSecurityAuditStore
from daon_user_api.authorization import Action, AuthorizationError, AuthorizationService
from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.notification_events import _account_view_allowed
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository

from test_notification_events import (
    _existing_0069_studio_case,
    _l2b_bound_pdf,
    _l2b_prepared_completion,
    _studio_case_member,
)


_ACTION = 'authorization.source_access.changed'


class _SourceAccessCase(SimpleNamespace):
    def __repr__(self):
        return '<S42 owned SourceACL fixture: connection values redacted>'


@pytest.mark.parametrize('invalid', [None, 0, 1, '', 'false', 'true', []])
def test_source_access_notification_flag_rejects_non_boolean_without_database(invalid):
    with pytest.raises(ValueError, match='^NOTIFICATION_INBOX_FLAG_INVALID$'):
        AuthorizationService(repository=object(), audit_store=AuditEventStore(),
            clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=invalid)


@pytest.mark.parametrize('arguments,expected', [({}, False), ({'notification_inbox_enabled': True}, True)])
def test_source_access_notification_flag_is_strict_and_default_off(arguments, expected):
    service = AuthorizationService(repository=object(), audit_store=AuditEventStore(),
        clock=lambda: datetime.now(timezone.utc), **arguments)
    assert service._notification_inbox_enabled is expected


def test_source_access_audit_returns_the_exact_appended_draft_without_database():
    appended = []

    class RecordingAuditStore(AuditEventStore):
        def append(self, draft):
            result = super().append(draft)
            appended.append(draft)
            return result

    store = RecordingAuditStore()
    service = AuthorizationService(repository=object(), audit_store=store,
        clock=lambda: datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc))
    principal = IdentityPrincipal('s42-acl-user', 's42-port-session', 's42-port-device', 's42-acl-tenant')
    returned = service._audit(action=_ACTION, outcome=AuditOutcome.SUCCEEDED,
        principal=principal, trace_id='s42-acl-trace', policy_version='s42-acl-policy',
        workspace_id='s42-acl-workspace', target_type='source_access', target_id='s42-acl-sv',
        before=None, after={'allowed': True, 'version': 1})
    assert len(appended) == 1 and isinstance(appended[0], AuditEventDraft)
    assert returned is appended[0], 'SOURCE_ACCESS_AUDIT_DRAFT_NOT_RETURNED'
    assert (returned.actor_id, returned.actor_type, returned.tenant_id, returned.workspace_id,
        returned.action, returned.target_type, returned.target_id, returned.before, returned.after,
        returned.metadata) == ('s42-acl-user', ActorType.USER, 's42-acl-tenant', 's42-acl-workspace',
            _ACTION, 'source_access', 's42-acl-sv', None, {'allowed': True, 'version': 1}, {})
    assert store.read(returned.event_id) is not None


@pytest.fixture
def source_access_notification_case():
    scope = _existing_0069_studio_case()
    ctx = scope.context
    # helper가 검증한 승인 DB/revision만 사용하며 다른 DB fallback은 없다.
    with psycopg.connect(scope.admin, connect_timeout=3) as connection:
        assert connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database "
            'WHERE datname=current_database()').fetchone() == (
                'S42 L2a disposable QA 2026-10-17 main owned; remove after tests; no production data',)
    store = PostgresCloudStore(scope.app, min_size=0, max_size=1, notification_inbox_enabled=True)
    audit = PostgresSecurityAuditStore(scope.app, min_size=0, max_size=1)
    repository = None
    try:
        _, complete = _l2b_prepared_completion(scope, store, 'document', document_ready=True)
        complete()
        with store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'view')) as connection:
            # 이름을 재조립하지 않고 실제 준비된 raw SV를 읽는다. S24의 registered TXT와 분리한다.
            rows = connection.execute('SELECT sv.source_id,sv.record_id,s.state FROM public.source_versions sv '
                'JOIN public.sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id '
                'AND s.record_id=sv.source_id WHERE sv.tenant_id=%s AND sv.workspace_id=%s '
                "AND sv.canonical_json->>'origin_kind'='native_pdf' AND s.state='ready' "
                'AND EXISTS(SELECT 1 FROM public.index_versions i WHERE i.tenant_id=sv.tenant_id '
                'AND i.workspace_id=sv.workspace_id AND i.source_version_id=sv.record_id)',
                (ctx.tenant_id, ctx.workspace_id)).fetchall()
            assert len(rows) == 1 and rows[0][2] == 'ready'
            assert _account_view_allowed(connection, CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'view'))
        source_id, version_id, _ = rows[0]
        repository = PostgresAuthorizationRepository(scope.app)
        supported = 'notification_inbox_enabled' in inspect.signature(AuthorizationService).parameters
        service = AuthorizationService(repository=repository, audit_store=audit,
            clock=lambda: datetime.now(timezone.utc),
            **({'notification_inbox_enabled': True} if supported else {}))
        # 서버 내부 principal DTO다. 실제 HTTP 인증/세션 검증 증거로 보고하지 않는다.
        principal = IdentityPrincipal(ctx.actor_id, 's42-acl-port-session', 's42-acl-port-device', ctx.tenant_id)
        grant = service.authorize_action(principal=principal, workspace_id=ctx.workspace_id,
            action=Action.VIEW, trace_id=ctx.trace_id, policy_version=ctx.policy_version)
        assert grant.action is Action.VIEW
        yield _SourceAccessCase(scope=scope, context=ctx, store=store, repository=repository, audit=audit,
            service=service, principal=principal, source_id=source_id, version_id=version_id)
    finally:
        if repository is not None:
            repository.close()
        audit.close()
        store.close()


def _grant(case, expected_version=0, *, recipient_id=None, allowed=True):
    ctx = case.context
    return case.service.set_source_access(principal=case.principal, workspace_id=ctx.workspace_id,
        user_id=ctx.actor_id if recipient_id is None else recipient_id,
        source_version_id=case.version_id, allowed=allowed,
        expected_version=expected_version, trace_id=ctx.trace_id, policy_version=ctx.policy_version)


def _business_snapshot(case):
    """중앙 감사는 별도 commit이므로 rollback 비교에서 제외한다. 기존 준비 행은 그대로 보존한다."""
    tables = ('identity_auth_source_access', 'identity_auth_workspaces', 'sources', 'source_versions',
        'processing_runs', 'understanding_results', 'index_versions', 'notebook_bindings', 'notebook_activities',
        'audit_events', 'canon_state_transitions', 'canon_transition_attempts', 'notifications',
        'notification_details', 'notification_source_outbox', 'notification_audit_outbox')
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        return {table: connection.execute(f'SELECT to_jsonb(x) FROM public.{table} x '
            'WHERE tenant_id=%s ORDER BY to_jsonb(x)::text', (case.context.tenant_id,)).fetchall() for table in tables}


def _primary_audits(case):
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        return connection.execute('SELECT event_id,tenant_id,workspace_id,actor_id,actor_type,action,target_type,target_id,'
            'outcome,trace_id,policy_version,before_value,after_value,metadata,occurred_at '
            'FROM public.security_audit_events WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s '
            'AND action=%s AND target_type=%s AND target_id=%s ORDER BY event_id',
            (case.context.tenant_id, case.context.workspace_id, case.context.actor_id,
                _ACTION, 'source_access', case.version_id)).fetchall()


def _assert_change_and_source(case, before, root_before):
    ctx = case.context
    roots = _primary_audits(case)
    new_roots = [row for row in roots if row['event_id'] not in root_before]
    assert len(new_roots) == 1
    root = new_roots[0]
    expected_root = dict(tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id, actor_id=ctx.actor_id,
        actor_type='user', action=_ACTION, target_type='source_access', target_id=case.version_id,
        outcome='succeeded', trace_id=ctx.trace_id, policy_version=ctx.policy_version, before_value=None,
        after_value={'allowed': True, 'version': 1}, metadata={})
    assert {key: root[key] for key in expected_root} == expected_root
    assert root['occurred_at'].tzinfo is not None
    # 원감사에는 target user가 없다. 아래 source 수신자는 같은 setter의 실제 ACL 키에 결속한다.
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        acl = connection.execute('SELECT allowed,version FROM public.identity_auth_source_access '
            'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s AND source_version_id=%s',
            (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, case.version_id)).fetchone()
        assert acl == {'allowed': 1, 'version': 1}
        workspace = connection.execute('SELECT acl_version,version FROM public.identity_auth_workspaces '
            'WHERE tenant_id=%s AND workspace_id=%s', (ctx.tenant_id, ctx.workspace_id)).fetchone()
        assert workspace == {'acl_version': case.initial_acl_version + 1, 'version': case.initial_workspace_version}
        sources = connection.execute('SELECT tenant_id,workspace_id,recipient_id,source_event_id,origin_event_id,'
            'source_store,action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,'
            'occurred_at,state,version,delivered_at FROM public.notification_source_outbox '
            'WHERE tenant_id=%s AND workspace_id=%s AND source_store=%s AND action=%s '
            'AND target_type=%s AND target_id=%s ORDER BY recipient_id',
            (ctx.tenant_id, ctx.workspace_id, 'authorization', _ACTION, 'source_access', case.version_id)).fetchall()
        expected = dict(tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id, recipient_id=ctx.actor_id,
            source_event_id='source-acl-event-' + hashlib.sha256(
                f"{root['event_id']}|{ctx.workspace_id}|{ctx.actor_id}".encode()).hexdigest()[:32],
            origin_event_id=root['event_id'], source_store='authorization', action=_ACTION,
            target_type='source_access', target_id=case.version_id, kind='membership', severity='info',
            notebook_id=None, trace_id=ctx.trace_id, policy_version=ctx.policy_version,
            occurred_at=root['occurred_at'], state='pending', version=1, delivered_at=None)
        assert sources == [expected], 'SOURCE_ACCESS_CHANGE_SOURCE_INTENT_MISSING'
        atomic = connection.execute('SELECT a.xmin=w.xmin AND w.xmin=s.xmin AS same_transaction,'
            'r.xmin<>s.xmin AS central_separate_transaction FROM public.identity_auth_source_access a '
            'JOIN public.identity_auth_workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=a.workspace_id '
            'JOIN public.notification_source_outbox s ON s.tenant_id=a.tenant_id AND s.workspace_id=a.workspace_id '
            'AND s.recipient_id=a.user_id AND s.target_id=a.source_version_id '
            'JOIN public.security_audit_events r ON r.tenant_id=s.tenant_id AND r.event_id=s.origin_event_id '
            'WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.user_id=%s AND a.source_version_id=%s '
            'AND s.source_event_id=%s', (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, case.version_id,
                expected['source_event_id'])).fetchall()
        assert atomic == [{'same_transaction': True, 'central_separate_transaction': True}]
    after = _business_snapshot(case)
    for table in before:
        if table not in {'identity_auth_source_access', 'identity_auth_workspaces', 'notification_source_outbox'}:
            assert after[table] == before[table]
    return root['event_id']


def _initial_state(case):
    before = _business_snapshot(case)
    assert before['identity_auth_source_access'] == []
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        case.initial_acl_version, case.initial_workspace_version = connection.execute(
            'SELECT acl_version,version FROM public.identity_auth_workspaces WHERE tenant_id=%s AND workspace_id=%s',
            (case.context.tenant_id, case.context.workspace_id)).fetchone()
    roots = {row['event_id'] for row in _primary_audits(case)}
    assert roots == set()
    return before, roots


def test_source_access_grant_has_atomic_source_and_conflict_adds_none(source_access_notification_case):
    case = source_access_notification_case
    before, roots = _initial_state(case)
    assert _grant(case) == 1
    _assert_change_and_source(case, before, roots)
    committed = _business_snapshot(case)
    committed_roots = _primary_audits(case)
    with pytest.raises(AuthorizationError) as conflict:
        _grant(case, expected_version=0)
    assert (conflict.value.code, conflict.value.http_status) == ('VERSION_CONFLICT', 412)
    assert _business_snapshot(case) == committed
    assert _primary_audits(case) == committed_roots


def test_source_access_rpc_then_sql_failure_rolls_back_and_retries(source_access_notification_case, monkeypatch):
    case = source_access_notification_case
    before, roots = _initial_state(case)
    appended, phases = [], []
    real_append, real_connect = case.audit.append, case.repository._connect

    def observed_append(draft):
        result = real_append(draft)
        if draft.action == _ACTION:
            appended.append(draft)
        return result

    class ObservedCursor:
        def __init__(self, cursor, raw):
            self.cursor, self.raw, self.source_rpc = cursor, raw, False

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def _observe(self, query, parameters):
            self.source_rpc = (isinstance(query, str) and query.lstrip().startswith('SELECT public.enqueue_')
                and isinstance(parameters, (tuple, list)) and bool(appended)
                and appended[-1].event_id in parameters)

        def execute(self, query, parameters=None):
            self.cursor.execute(query, parameters)
            self._observe(query, parameters)
            return self

        def fetchone(self):
            row = self.cursor.fetchone()
            if self.source_rpc:
                actual = tuple(row.values()) if isinstance(row, dict) else row
                assert actual is not None and len(actual) == 1 and actual[0] is True
                phases.append('actual_source_rpc_true')
                try:
                    self.raw.execute('SELECT 1/0')
                except psycopg.Error as error:
                    assert error.sqlstate == '22012'
                    phases.append('actual_sql_22012')
                    raise
                pytest.fail('SOURCE_ACCESS_SQL_FAILURE_NOT_INJECTED', pytrace=False)
            return row

    class ObservedConnection:
        def __init__(self, raw):
            self.raw = raw

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def cursor(self, *args, **kwargs):
            return ObservedCursor(self.raw.cursor(*args, **kwargs), self.raw)

        def execute(self, query, parameters=None):
            cursor = ObservedCursor(self.raw.execute(query, parameters), self.raw)
            cursor._observe(query, parameters)
            return cursor

    def observed_connect():
        connection = real_connect()
        connection._conn = ObservedConnection(connection._conn)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(case.audit, 'append', observed_append)
        patch.setattr(case.repository, '_connect', observed_connect)
        with pytest.raises(AuthorizationError) as failed:
            _grant(case)
    assert (failed.value.code, failed.value.http_status) == ('PERSISTENCE_UNAVAILABLE', 503)
    assert phases == ['actual_source_rpc_true', 'actual_sql_22012']
    assert _business_snapshot(case) == before
    ghost_ids = {row['event_id'] for row in _primary_audits(case)} - roots
    assert appended and appended[-1].event_id in ghost_ids
    # 별도 중앙 commit의 ghost는 보존한다. 삭제/덮어쓰기하거나 감사 전체를 한 건으로 강제하지 않는다.
    assert _grant(case) == 1
    _assert_change_and_source(case, before, roots | ghost_ids)
    assert ghost_ids <= {row['event_id'] for row in _primary_audits(case)}


def test_source_access_registered_raw_metadata_does_not_require_ready_or_index(source_access_notification_case):
    """잘못 추가된 ready/index 선행 필터가 실제 registered Source 알림을 누락시키면 실패한다."""
    original = source_access_notification_case
    # 별도 suffix의 정상 최초 Canon INSERT다. 기존 ready Source/불변 SV는 수정하지 않는다.
    registered_scope = SimpleNamespace(context=original.context, suffix=uuid4().hex)
    source, version = _l2b_bound_pdf(registered_scope, original.store, expected_activity_count=2)
    fields = dict(vars(original), source_id=source, version_id=version)
    case = _SourceAccessCase(**fields)
    ctx = case.context
    with case.store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'view')) as connection:
        assert connection.execute('SELECT s.state,sv.source_id FROM public.sources s '
            'JOIN public.source_versions sv ON sv.tenant_id=s.tenant_id AND sv.workspace_id=s.workspace_id '
            'AND sv.source_id=s.record_id WHERE s.tenant_id=%s AND s.workspace_id=%s '
            'AND s.record_id=%s AND sv.record_id=%s',
            (ctx.tenant_id, ctx.workspace_id, source, version)).fetchone() == ('registered', source)
        assert connection.execute('SELECT count(*) FROM public.index_versions WHERE tenant_id=%s '
            'AND workspace_id=%s AND source_version_id=%s', (ctx.tenant_id, ctx.workspace_id, version)).fetchone() == (0,)
        assert _account_view_allowed(connection, CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'view'))
    before, roots = _initial_state(case)
    assert _grant(case) == 1
    _assert_change_and_source(case, before, roots)
    # 성공 알림 때문에 Source 상태를 ready로 승격하거나 index를 합성하지 않는다.
    after = _business_snapshot(case)
    for table in ('sources', 'source_versions', 'index_versions', 'notebook_bindings', 'notebook_activities'):
        assert after[table] == before[table]


@pytest.mark.parametrize('boundary', (
    'source_deleted', 'explicit_acl_deny', 'recipient_auth_suspended', 'recipient_identity_disabled',
))
def test_source_access_current_denial_skips_source_without_rejecting_acl_write(
    source_access_notification_case, boundary,
):
    """원 관리자의 ACL 저장 성공과 대상 본인의 현재 자료/VIEW 거부를 혼동하면 실패한다."""
    from daon_user_api.document_index_postgres import _source_access_allowed
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon

    case = source_access_notification_case
    ctx = case.context
    recipient = ctx.actor_id
    allowed = boundary != 'explicit_acl_deny'
    if boundary.startswith('recipient_'):
        recipient = _studio_case_member(case.scope, role='viewer')
        with case.store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, recipient, 'view')) as connection:
            assert _account_view_allowed(connection, CloudAccessContext(ctx.tenant_id, ctx.workspace_id, recipient, 'view'))
        # 기존 가변 권한/identity 표의 자기 QA 대상만 바꾼다. 원 관리자 tenant role은 그대로다.
        with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT count(*) FROM public.identity_auth_tenant_roles '
                'WHERE tenant_id=%s AND user_id=%s', (ctx.tenant_id, recipient)).fetchone() == (0,)
            if boundary == 'recipient_auth_suspended':
                assert connection.execute("UPDATE public.identity_auth_memberships SET state='suspended',"
                    'version=version+1,updated_at=now() WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s '
                    "AND state='active' RETURNING role,state", (ctx.tenant_id, ctx.workspace_id, recipient)).fetchall() == [
                        ('viewer', 'suspended')]
                assert connection.execute('SELECT state FROM public.identity_users WHERE user_id=%s',
                    (recipient,)).fetchone() == ('active',)
            else:
                assert connection.execute("UPDATE public.identity_users SET state='disabled' WHERE user_id=%s "
                    "AND state='active' RETURNING state", (recipient,)).fetchall() == [('disabled',)]
                assert connection.execute('SELECT role,state FROM public.identity_auth_memberships '
                    'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s',
                    (ctx.tenant_id, ctx.workspace_id, recipient)).fetchone() == ('viewer', 'active')
            assert connection.execute('SELECT status FROM public.user_accounts WHERE tenant_id=%s AND user_id=%s',
                (ctx.tenant_id, recipient)).fetchone() == ('active',)
        with case.store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, recipient, 'view')) as connection:
            assert not _account_view_allowed(connection, CloudAccessContext(ctx.tenant_id, ctx.workspace_id, recipient, 'view'))
    elif boundary == 'source_deleted':
        # 실제 Canon 전이만 사용한다. 원행 강제 UPDATE/guard 해제/물리 purge/Object 삭제는 없다.
        with case.store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, 'source.write')) as connection:
            state, version = connection.execute('SELECT state,version FROM public.sources WHERE tenant_id=%s '
                'AND workspace_id=%s AND record_id=%s', (ctx.tenant_id, ctx.workspace_id, case.source_id)).fetchone()
            assert state == 'ready'
            for target in ('disabled', 'deleting', 'deleted'):
                version = Canon._transition(connection, ctx, 'Source', case.source_id, version, target,
                    's42-acl-delete-' + target + '-' + uuid4().hex)
            assert connection.execute('SELECT state,version FROM public.sources WHERE tenant_id=%s AND workspace_id=%s '
                'AND record_id=%s', (ctx.tenant_id, ctx.workspace_id, case.source_id)).fetchone() == ('deleted', version)

    # 이 검사가 통과해야 거부 원인을 원 관리자 권한 상실/다른 scope와 혼동하지 않는다.
    assert case.service.authorize_action(principal=case.principal, workspace_id=ctx.workspace_id,
        action=Action.VIEW, trace_id=ctx.trace_id, policy_version=ctx.policy_version).allowed is True
    before, roots = _initial_state(case)
    assert _grant(case, recipient_id=recipient, allowed=allowed) == 1
    after = _business_snapshot(case)
    for table in before:
        if table not in {'identity_auth_source_access', 'identity_auth_workspaces'}:
            assert after[table] == before[table], 'SOURCE_ACCESS_CURRENT_DENIAL_MUST_NOT_PUBLISH'
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT user_id,source_version_id,allowed,version FROM public.identity_auth_source_access '
            'WHERE tenant_id=%s AND workspace_id=%s', (ctx.tenant_id, ctx.workspace_id)).fetchall() == [
                (recipient, case.version_id, int(allowed), 1)]
        assert connection.execute('SELECT acl_version,version FROM public.identity_auth_workspaces '
            'WHERE tenant_id=%s AND workspace_id=%s', (ctx.tenant_id, ctx.workspace_id)).fetchone() == (
                case.initial_acl_version + 1, case.initial_workspace_version)
    roots_after = _primary_audits(case)
    assert len(roots_after) == 1 and roots_after[0]['event_id'] not in roots
    assert (roots_after[0]['before_value'], roots_after[0]['after_value'], roots_after[0]['metadata']) == (
        None, {'allowed': allowed, 'version': 1}, {})
    if boundary == 'explicit_acl_deny':
        with case.store._transaction(CloudAccessContext(ctx.tenant_id, ctx.workspace_id, recipient, 'view')) as connection:
            access = CloudAccessContext(ctx.tenant_id, ctx.workspace_id, recipient, 'view')
            assert _account_view_allowed(connection, access)
            assert not _source_access_allowed(connection, access, case.version_id)
