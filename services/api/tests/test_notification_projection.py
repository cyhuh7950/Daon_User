"""S43 L3 실제 Studio 원장 → 현재 Inbox 읽기 projection의 첫 PG RED.

기존 immutable 합성 Source/Output fixture 위에서 실제 record_action/withdraw를 호출한다.
모델·Object·외부 전달은 실행하지 않으며 생성 Provider/브라우저 증거로 보고하지 않는다.
DB/DSN 주입·실행·정리는 main 소유다. migration/role/기존 scope를 변경하지 않는다.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import psycopg
import pytest

from daon_user_api.authorization import Action
from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.notification import InboxRequest
from daon_user_api.notification_postgres import ScopedPostgresNotificationRepository
from daon_user_api.open_law_source_access import _current_auth_grant
from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
from test_notification_events import _existing_0069_studio_case, _studio_case_member, _l2b_prepared_completion


def _snapshot(scope):
    """현재 시험 tenant의 원장/감사/outbox만 해시 비교한다. 원문·자격은 진단에 싣지 않는다."""
    tables = (
        'review_requests', 'approval_requests', 'approvals', 'deliveries',
        'output_versions', 'evidence_references', 'source_versions', 'sources',
        'notebook_bindings', 'canon_state_transitions', 'idempotency_records',
        'audit_events', 'studio_approval_notices', 'notifications',
        'notification_details', 'notification_source_outbox', 'notification_audit_outbox',
    )
    with psycopg.connect(scope.admin) as connection:
        result = {}
        for table in tables:
            rows = connection.execute(
                f'SELECT to_jsonb(r),r.xmin::text FROM public.{table} r '
                'WHERE tenant_id=%s ORDER BY to_jsonb(r)::text',
                (scope.context.tenant_id,),
            ).fetchall()
            encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
            result[table] = (len(rows), hashlib.sha256(encoded).hexdigest())
        return result


@pytest.fixture
def projection_case():
    # 이 helper가 env DSN의 exact owned L2 QA 이름/revision0069/static84를 검사한다.
    # 미제공 DSN은 SKIP이며 PASS가 아니다. 다른 DB fallback/전체 migration 재실행0.
    scope = _existing_0069_studio_case(pending=True)
    context = scope.context
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2, notification_inbox_enabled=True)
    try:
        studio = PostgresStudioWorkspaceRepository(store)
        lifecycle = StudioApprovalLifecycleRepository(store)
        # seed의 pending 요청을 덮거나 삭제하지 않는다. 기존 owner API로 먼저 회수한다.
        withdrawn, replayed = lifecycle.withdraw(
            context, scope.request_id, scope.version, 's43-inbox-close-initial-request')
        assert replayed is False and withdrawn['status'] == 'withdrawn'
        # 오래된 최소 S24 seed Output에는 원Notebook metadata가 없다.
        # 불변 seed를 수정하거나 임의 binding을 원근거로 삼지 않고 실제 생성 경로로 새 Version을 만든다.
        from daon_user_api.studio_workspace import StudioGenerationRequest
        store.seed_scope(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, 'source.create'))
        run, complete = _l2b_prepared_completion(scope, store, 'document', document_ready=True)
        complete()  # 실제 문서 완료/색인 저장, 모델 이해 결과만 기존 합성 fixture다.
        with psycopg.connect(scope.admin) as connection:
            source, source_version = connection.execute('SELECT sv.source_id,sv.record_id FROM processing_runs r '
                'JOIN source_versions sv ON sv.tenant_id=r.tenant_id AND sv.workspace_id=r.workspace_id '
                'AND sv.record_id=r.source_version_id WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s',
                (context.tenant_id, context.workspace_id, run)).fetchone()
        generation_request = StudioGenerationRequest(
            'comparison_table', source, (source_version,), None, None,
            'S43 원장 연결 합성 QA', 'reviewer', None, 'short', 'summary', 'csv',
            'review_required', source_only=True)
        generated, replayed = studio.create_generation(context, generation_request, 's43-inbox-actual-generation')
        assert replayed is False
        scope.version = generated['output_version_id']
        with store._transaction(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, 'view')) as connection:
            for action in (Action.REVIEW, Action.APPROVE, Action.DELIVER):
                _current_auth_grant(context, connection, action)
            studio._require_output_sources(connection, context, scope.version, action=Action.REVIEW)

        review, replayed = studio.record_action(context, 'review',
            {'output_version_id': scope.version}, 's43-inbox-review')
        assert replayed is False and review['action'] == 'review'
        request, replayed = studio.record_action(context, 'approval_request',
            {'output_version_id': scope.version, 'review_request_id': review['record_id']},
            's43-inbox-approval-request')
        assert replayed is False and request['action'] == 'approval_request'
        approval, replayed = studio.record_action(context, 'approval',
            {'output_version_id': scope.version, 'approval_request_id': request['record_id'],
             'decision': 'approved'}, 's43-inbox-approval')
        assert replayed is False and approval['action'] == 'approval'
        delivery_payload = {'output_version_id': scope.version, 'approval_id': approval['record_id'],
                            'recipient': 'workspace_recipient'}
        delivery, replayed = studio.record_action(
            context, 'delivery', delivery_payload, 's43-inbox-delivery')
        assert replayed is False and delivery['action'] == 'delivery'
        before_replay = _snapshot(scope)
        repeated, replayed = studio.record_action(
            context, 'delivery', delivery_payload, 's43-inbox-delivery')
        assert replayed is True and repeated == delivery
        assert _snapshot(scope) == before_replay

        with psycopg.connect(scope.admin) as connection:
            actual = connection.execute(
                'SELECT state,canonical_json,created_by,output_version_id FROM public.approval_requests '
                'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (context.tenant_id, context.workspace_id, request['record_id']),
            ).fetchone()
            assert actual[0] == 'approved'
            assert actual[2:] == (context.actor_id, scope.version)
            deadline = datetime.fromisoformat(actual[1]['expires_at'].replace('Z', '+00:00'))
            assert deadline.tzinfo is not None
            assert connection.execute(
                'SELECT state FROM public.output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (context.tenant_id, context.workspace_id, scope.version),
            ).fetchone() == ('delivered',)
            # UI의 임의 projection이나 source INSERT가 아니라 원 서비스의 실제 감사/source다.
            for action, table, record in (
                ('studio.review', 'review_requests', review),
                ('studio.approval_request', 'approval_requests', request),
                ('studio.approval', 'approvals', approval),
                ('studio.delivery', 'deliveries', delivery),
            ):
                roots = connection.execute(
                    'SELECT a.event_id,a.actor_id,a.target_type,a.outcome,s.origin_event_id,s.recipient_id,'
                    's.notebook_id,s.target_id,s.source_store,s.action FROM public.audit_events a '
                    'JOIN public.notification_source_outbox s ON s.tenant_id=a.tenant_id '
                    'AND s.workspace_id=a.workspace_id AND s.origin_event_id=a.event_id '
                    'WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.action=%s AND a.target_id=%s',
                    (context.tenant_id, context.workspace_id, action, record['record_id']),
                ).fetchall()
                assert len(roots) == 1
                root = roots[0]
                assert root[1:] == (context.actor_id, table, 'succeeded', root[0], context.actor_id,
                                    context.notebook_id, record['record_id'], 'studio', action)
        yield SimpleNamespace(scope=scope, store=store, lifecycle=lifecycle, studio=studio,
                              generation_request=generation_request,
                              review=review, request=request, delivery=delivery, deadline=deadline)
    finally:
        store.close()


def _repository(case, actor_id=None):
    context = case.scope.context
    actor_id = actor_id or context.actor_id
    return ScopedPostgresNotificationRepository(
        case.scope.app, tenant_id=context.tenant_id, recipient_id=actor_id,
        contexts=(CloudAccessContext(context.tenant_id, context.workspace_id, actor_id, 'view'),),
        studio_approval_repository=case.lifecycle,
    )


def test_s43_projection_actual_studio_records_reopen_read_only(projection_case, monkeypatch):
    """실제 review/승인/전달 후 scoped PG port 누락을 재현한다. GET 원장 쓰기0·새 연결도 요구한다."""
    import daon_user_api.notification_postgres as postgres

    case = projection_case
    context = case.scope.context
    expected = {case.review['record_id']: 'review', case.request['record_id']: 'approval',
                case.delivery['record_id']: 'delivery'}
    before = _snapshot(case.scope)
    real_connect = postgres.connect
    backends = []

    @contextmanager
    def observed_connect(*args, **kwargs):
        # 연결/SQL/현재 권한은 실제 구현이며 backend PID만 관측한다.
        with real_connect(*args, **kwargs) as connection:
            backends.append(connection.info.backend_pid)
            yield connection

    with monkeypatch.context() as patch:
        patch.setattr(postgres, 'connect', observed_connect)
        projections = []
        for _ in range(2):
            repository = _repository(case)
            # 현재 후보의 실제 예상 RED: all_inbox_requests 부재 AttributeError.
            items = tuple(repository.all_inbox_requests())
            assert all(isinstance(item, InboxRequest) for item in items)
            assert len({item.request_id for item in items}) == len(items)
            selected = {item.request_id: item for item in items if item.request_id in expected}
            assert set(selected) == set(expected), 'STUDIO_INBOX_PROJECTION_MISSING'
            for request_id, kind in expected.items():
                item = selected[request_id]
                assert item.status in {'pending', 'in_review', 'approved', 'rejected', 'expired',
                                       'withdrawn', 'delivered', 'failed', 'cancelled'}
                assert (item.tenant_id, item.workspace_id, item.recipient_id, item.actor_id,
                        item.request_kind, item.resource_type, item.resource_id, item.deep_link) == (
                    context.tenant_id, context.workspace_id, context.actor_id, context.actor_id,
                    kind, 'output_version', case.scope.version,
                    f'/notebooks/{context.notebook_id}?output_version_id={case.scope.version}')
                assert item.due_at == (case.deadline.astimezone(timezone.utc) if kind == 'approval' else None)
            assert selected[case.request['record_id']].status == 'approved'
            assert selected[case.delivery['record_id']].status == 'delivered'
            projections.append(selected)
            assert _snapshot(case.scope) == before
        assert projections[0] == projections[1]
    assert len(set(backends)) >= 2, '두 새 repository의 실제 PG 연결이 필요함'
    assert _snapshot(case.scope) == before


def test_s43_projection_filters_current_role_and_never_treats_delivery_recipient_as_user(projection_case):
    case = projection_case
    # 원 작성자와 다른 현재 reviewer/approver는 처리 권한에 맞는 요청만 읽는다.
    # delivery payload의 workspace_recipient 문자열은 본인 계정으로 해석하지 않는다.
    for role, expected_kinds in [('viewer', set()), ('reviewer', {'review'}),
                                  ('approver', {'review', 'approval'})]:
        actor = _studio_case_member(case.scope, role=role)
        before = _snapshot(case.scope)
        items = tuple(_repository(case, actor).all_inbox_requests())
        assert {item.request_kind for item in items} == expected_kinds
        assert all(item.recipient_id == actor and item.actor_id == case.scope.context.actor_id for item in items)
        assert all(item.request_kind != 'delivery' for item in items)
        assert _snapshot(case.scope) == before


def test_s43_projection_denies_current_source_acl_and_distinguishes_database_failure(projection_case, monkeypatch):
    from daon_user_api.authorization import AuthorizationService
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.identity import IdentityPrincipal
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    from daon_user_api.notification import NotificationError
    import daon_user_api.notification_postgres as postgres

    case = projection_case
    ctx = case.scope.context
    assert tuple(_repository(case).all_inbox_requests())
    with psycopg.connect(case.scope.admin) as connection:
        source_version = connection.execute('SELECT source_version_id FROM evidence_references '
            'WHERE tenant_id=%s AND workspace_id=%s AND output_version_id=%s',
            (ctx.tenant_id, ctx.workspace_id, case.scope.version)).fetchone()[0]
    authorization = AuthorizationService(repository=PostgresAuthorizationRepository(case.scope.app),
        audit_store=AuditEventStore(), clock=lambda: datetime.now(timezone.utc))
    authorization.set_source_access(principal=IdentityPrincipal(ctx.actor_id, 's43-session', 's43-device', ctx.tenant_id),
        workspace_id=ctx.workspace_id, source_version_id=source_version, user_id=ctx.actor_id,
        allowed=False, expected_version=0, trace_id=ctx.trace_id, policy_version=ctx.policy_version)
    before = _snapshot(case.scope)
    assert tuple(_repository(case).all_inbox_requests()) == ()
    assert _snapshot(case.scope) == before
    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError('synthetic database unavailable; never display this')
    with monkeypatch.context() as patch:
        patch.setattr(postgres, 'connect', unavailable)
        with pytest.raises(NotificationError) as failure:
            _repository(case).all_inbox_requests()
        assert failure.value.http_status == 503
    assert _snapshot(case.scope) == before


def test_s43_projection_reflects_pending_then_actual_withdrawal_or_rejection_without_get_writes(projection_case):
    case = projection_case
    ctx = case.scope.context
    for decision in ('withdrawn', 'rejected'):
        generated, replayed = case.studio.create_generation(ctx, case.generation_request,
            's43-state-generation-' + decision)
        assert replayed is False
        version = generated['output_version_id']
        review, _ = case.studio.record_action(ctx, 'review', {'output_version_id': version},
            's43-state-review-' + decision)
        request, _ = case.studio.record_action(ctx, 'approval_request',
            {'output_version_id': version, 'review_request_id': review['record_id']},
            's43-state-request-' + decision)
        before = _snapshot(case.scope)
        listed = {item.request_id: item for item in _repository(case).all_inbox_requests()}
        assert listed[request['record_id']].status == 'pending'
        assert listed[review['record_id']].status == 'in_review'
        assert listed[request['record_id']].due_at is not None
        assert _snapshot(case.scope) == before


        if decision == 'withdrawn':
            result, replayed = case.lifecycle.withdraw(ctx, request['record_id'], version,
                's43-state-withdraw')
            assert replayed is False and result['status'] == 'withdrawn'
        else:
            result, replayed = case.studio.record_action(ctx, 'approval',
                {'output_version_id': version, 'approval_request_id': request['record_id'], 'decision': 'rejected'},
                's43-state-reject')
            assert replayed is False and result['action'] == 'approval'
        before = _snapshot(case.scope)
        reopened = {item.request_id: item for item in _repository(case).all_inbox_requests()}
        assert reopened[request['record_id']].status == decision
        assert reopened[review['record_id']].status == decision
        assert reopened[request['record_id']].deep_link == f'/notebooks/{ctx.notebook_id}?output_version_id={version}'
        assert _snapshot(case.scope) == before


def test_s43_runtime_http_reads_actual_studio_request_and_preserves_current_access():
    """실제 PG 로그인/권한/Studio/HTTP. OIDC claims만 대역, 브라우저·실 Provider 증거 아님."""
    import os
    from dataclasses import replace
    from uuid import uuid4
    from psycopg.conninfo import make_conninfo, conninfo_to_dict
    from fastapi.testclient import TestClient
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.authorization import AuthorizationService, Role
    from daon_user_api.identity import IdentityService, ClientKind, DevicePlatform
    from daon_user_api.identity_postgres import PostgresIdentityRepository
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    from daon_user_api.runtime import RuntimeSettings, RuntimeDependencies, create_app, WEB_SESSION_COOKIE
    from daon_user_api.studio_workspace import StudioContext, StudioGenerationRequest
    from test_identity_support import FakeVerifiedOidcProvider, policy, POLICY_VERSION
    from test_notification_events import _PrivateDsn

    dsn = os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not dsn:
        pytest.skip('소유 QA DSN 미제공: S43 실제 HTTP 미실행')
    assert conninfo_to_dict(dsn).get('dbname') == 'daon_user_s42_qa_20261017_l2'
    admin, app = _PrivateDsn(dsn), _PrivateDsn(make_conninfo(dsn, options='-c role=daon_app'))
    suffix = uuid4().hex
    tenant, workspace, trace = 's43-http-tenant-' + suffix, 's43-http-ws-' + suffix, 's43-http-trace-' + suffix
    identity_repository, auth_repository = PostgresIdentityRepository(app), PostgresAuthorizationRepository(app)
    audit, clock = AuditEventStore(), lambda: datetime.now(timezone.utc)
    identity = IdentityService(repository=identity_repository, audit_store=audit,
        oidc_policies=(replace(policy(ClientKind.WEB), tenant_id=tenant),), clock=clock)
    cloud = PostgresCloudStore(app, min_size=0, max_size=2, notification_inbox_enabled=True)
    credentials = dependencies = client = None
    try:
        start = identity.begin_oidc_login(issuer='https://login.example.com', client_id='daon-web', audience='daon-user-api',
            redirect_uri='https://app.example.com/auth/callback', client_kind=ClientKind.WEB, tenant_id=tenant,
            trace_id=trace, policy_version=POLICY_VERSION)
        provider = FakeVerifiedOidcProvider(subject='s43-http-subject-' + suffix)
        provider.expected_nonce = start.nonce
        credentials = identity.complete_oidc_login(state=start.state, authorization_code=provider.authorization_code,
            code_verifier=start.code_verifier, client_id='daon-web', redirect_uri='https://app.example.com/auth/callback',
            provider=provider, platform=DevicePlatform.WEB, trace_id=trace, policy_version=POLICY_VERSION)
        actor = credentials.user_id
        cloud.seed_scope(CloudAccessContext(tenant, workspace, actor, 'source.create'))
        auth_repository.bootstrap_workspace(tenant_id=tenant, workspace_id=workspace, owner_user_id=actor,
            owner_role=Role.ORGANIZATION_ADMIN, workspace_kind='organization', data_area='cloud_sync',
            cost_limit_cents=1000, now=clock())
        notebook, _ = PostgresNotebookRepository(cloud).create(NotebookContext(tenant, workspace, actor, trace, POLICY_VERSION),
            title='S43 실제 HTTP 합성 QA', description=None, idempotency_key='s43-http-notebook-' + suffix,
            request_fingerprint='d' * 64, now=clock())
        context = StudioContext(tenant, workspace, actor, trace, POLICY_VERSION, notebook.notebook_id)
        scope = SimpleNamespace(admin=admin, app=app, context=context, suffix=suffix, revision='0069',
            expected_notebook_activity_prefix=(('created', actor),))
        run, complete = _l2b_prepared_completion(scope, cloud, 'document', document_ready=True)
        complete()
        with psycopg.connect(admin) as connection:
            source, version = connection.execute('SELECT sv.source_id,sv.record_id FROM processing_runs r '
                'JOIN source_versions sv ON sv.tenant_id=r.tenant_id AND sv.workspace_id=r.workspace_id '
                'AND sv.record_id=r.source_version_id WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s',
                (tenant, workspace, run)).fetchone()
        studio = PostgresStudioWorkspaceRepository(cloud)
        generated, _ = studio.create_generation(context, StudioGenerationRequest('comparison_table', source, (version,),
            None, None, 'S43 HTTP 원장 확인', 'reviewer', None, 'short', 'summary', 'csv', 'review_required',
            source_only=True), 's43-http-generation')
        output = generated['output_version_id']
        review, _ = studio.record_action(context, 'review', {'output_version_id': output}, 's43-http-review')
        request, _ = studio.record_action(context, 'approval_request',
            {'output_version_id': output, 'review_request_id': review['record_id']}, 's43-http-request')
        authorization = AuthorizationService(repository=auth_repository, audit_store=audit, clock=clock, identity_service=identity)
        dependencies = RuntimeDependencies(settings=RuntimeSettings(profile='test', bind_host='127.0.0.1', port=8000,
            cloud_database_dsn=app, policy_version=POLICY_VERSION, development_stage=True, notification_inbox_enabled=True),
            identity_service=identity, authorization_service=authorization, audit_store=audit,
            identity_repository=identity_repository, authorization_repository=auth_repository, cloud_store=cloud)
        # lifespan 미기동: 다른 QA의 pending dispatcher/정기 sweep를 시작하지 않는다.
        client = TestClient(create_app(dependencies), base_url='http://127.0.0.1')
        auth = {'cookie': f'{WEB_SESSION_COOKIE}={credentials.access_token}'}
        url = '/api/v1/inbox?filter=kind:approval&limit=1'
        before = _snapshot(scope)
        assert client.get(url).status_code == 401
        response = client.get(url, headers=auth)
        assert response.status_code == 200
        item, = response.json()['data']['items']
        assert (item['request_id'], item['status'], item['actor_id'], item['workspace_id'], item['deep_link']) == (
            request['record_id'], 'pending', actor, workspace, f'/notebooks/{notebook.notebook_id}?output_version_id={output}')
        assert client.get(url, headers=auth).json()['data'] == response.json()['data']
        assert client.get('/api/v1/inbox?workspace_id=foreign', headers=auth).status_code == 400
        assert _snapshot(scope) == before
        authorization.set_source_access(principal=identity.describe_access(credentials.access_token,
            trace_id=trace, policy_version=POLICY_VERSION).principal, workspace_id=workspace, user_id=actor,
            source_version_id=version, allowed=False, expected_version=0, trace_id=trace, policy_version=POLICY_VERSION)
        hidden = client.get(url, headers=auth)
        assert hidden.status_code == 200 and hidden.json()['data'] == {'items': [], 'next_cursor': None}
    finally:
        if client is not None:
            client.close()
        if credentials is not None:
            identity.revoke_current_web_session(access_token=credentials.access_token, trace_id=trace, policy_version=POLICY_VERSION)
        if dependencies is not None:
            dependencies.close()
        else:
            cloud.close()
            auth_repository.close()
            identity_repository.close()
