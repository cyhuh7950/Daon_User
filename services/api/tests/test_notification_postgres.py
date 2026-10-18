"""S42 저장 경계 시험. 실제 PG 증거는 전용 DSN이 있는 경우에만 생성한다."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.notification import NotificationError, NotificationEvent, NotificationService, ReferenceNotificationRepository
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service, native_login


def test_s42_scoped_repository_combines_native_and_studio_notice_ports_without_copy(monkeypatch):
    """집계·위임 구성 단위 계약이다. 실제 S24 저장/조회 성공 증거가 아니다."""
    from types import SimpleNamespace
    from daon_user_api.cloud_storage import CloudAccessContext
    import daon_user_api.notification_postgres as storage
    import daon_user_api.notification_studio_approval as approval
    context = CloudAccessContext('scope-tenant', 'scope-one', 'scope-user', 'view')
    options = dict(tenant_id=context.tenant_id, recipient_id=context.actor_id)
    normal = SimpleNamespace(notification_id='normal-owned', workspace_id=context.workspace_id, resource_type='Run')
    notice = SimpleNamespace(notification_id='approval-notice-owned', workspace_id=context.workspace_id,
        resource_type='StudioApprovalNotice')
    lifecycle = object()
    calls = []

    class NativePort:
        def __init__(self, dsn, *, context):
            self.context = context
        def notifications_for(self, **scope):
            return (normal,)
        def notification_for(self, *, notification_id, **scope):
            return normal if notification_id == normal.notification_id else None
        def transition_read(self, **scope):
            calls.append(('native', scope))
            return 'native-read'

    class ApprovalPort:
        def __init__(self, repository, *, context):
            assert repository is lifecycle
            assert context == expected_context
        def notifications_for(self, **scope):
            return (notice,)
        def notification_for(self, *, notification_id, **scope):
            return notice if notification_id == notice.notification_id else None
        def transition_read(self, **scope):
            calls.append(('approval', scope))
            return 'approval-read'

    expected_context = context
    monkeypatch.setattr(storage, 'PostgresNotificationRepository', NativePort)
    monkeypatch.setattr(approval, 'StudioApprovalNotificationRepository', ApprovalPort)
    repository = storage.ScopedPostgresNotificationRepository('unused-dsn', **options,
        contexts=(context,), studio_approval_repository=lifecycle)
    assert repository.notifications_for(**options) == (normal, notice)
    assert repository.notification_for(**options, notification_id=notice.notification_id) is notice
    assert repository.transition_read(**options, notification_id=normal.notification_id) == 'native-read'
    assert repository.transition_read(**options, notification_id=notice.notification_id) == 'approval-read'
    assert [target for target, _ in calls] == ['native', 'approval']
    with monkeypatch.context() as patch:
        patch.setattr(ApprovalPort, 'notifications_for', lambda *_args, **_kwargs: (normal,))
        with pytest.raises(NotificationError, match='DATABASE_RESPONSE_INVALID'):
            repository.notifications_for(**options)
    with monkeypatch.context() as patch:
        def unavailable(*_args, **_kwargs):
            raise NotificationError('DATABASE_UNAVAILABLE', 503)
        patch.setattr(ApprovalPort, 'notifications_for', unavailable)
        with pytest.raises(NotificationError, match='DATABASE_UNAVAILABLE'):
            repository.notifications_for(**options)


def test_s42_scoped_pg_adapter_contract_rejects_foreign_context_and_never_swallows_database_error(monkeypatch):
    """집계 port 단위 경계다. 실제 DB/Source 성공으로 계산하지 않는다."""
    from types import SimpleNamespace
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.notification_postgres import PostgresNotificationRepository, ScopedPostgresNotificationRepository
    context=CloudAccessContext('scope-tenant','scope-one','scope-user','view')
    options={'tenant_id':'scope-tenant','recipient_id':'scope-user'}
    for contexts in ((context,context),
        (CloudAccessContext('foreign','scope-one','scope-user','view'),),
        (CloudAccessContext('scope-tenant','scope-one','foreign-user','view'),),
        (CloudAccessContext('scope-tenant','scope-one','scope-user','write'),)):
        with pytest.raises(ValueError,match='NOTIFICATION_SCOPE_INVALID'):
            ScopedPostgresNotificationRepository('unused-dsn',**options,contexts=contexts)
    repository=ScopedPostgresNotificationRepository('unused-dsn',**options,
        contexts=(context,CloudAccessContext('scope-tenant','scope-two','scope-user','view')))
    calls=[]
    def read(current,**scope):
        calls.append(current.context.workspace_id)
        if current.context.workspace_id=='scope-two':
            raise NotificationError('DATABASE_UNAVAILABLE',503)
        return (SimpleNamespace(notification_id='existing-first'),)
    monkeypatch.setattr(PostgresNotificationRepository,'notifications_for',read)
    with pytest.raises(NotificationError) as unavailable:
        repository.notifications_for(**options)
    assert (unavailable.value.code,unavailable.value.http_status)==('DATABASE_UNAVAILABLE',503)
    assert calls==['scope-one','scope-two']


def test_s42_scoped_pg_repository_reads_all_authorized_workspaces_without_global_fallback():
    """실제 Doc 원사건 두 개·같은 사용자/tenant의 서로 다른 Workspace를 사용한다."""
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4
    import psycopg
    from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from test_notification_events import (_existing_0069_studio_case, _pending_l2b_completed,
        _l2_current_read_request, _l2b_completion_snapshot)
    from daon_user_api.notification_events import materialize_pending_completed
    from daon_user_api.notification_postgres import PostgresNotificationRepository
    scope = _existing_0069_studio_case(workspace_kind='organization')
    context = scope.context
    suffix = uuid4().hex
    second_id = 's42-notification-ws-'+suffix
    second_context = replace(context, workspace_id=second_id)
    store = PostgresCloudStore(scope.app, min_size=0, max_size=2)
    try:
        store.seed_scope(CloudAccessContext(context.tenant_id,second_id,context.actor_id,'source.create'))
        with psycopg.connect(scope.admin) as connection:
            connection.execute("INSERT INTO identity_auth_workspaces VALUES(%s,%s,'organization','cloud_sync',1000,1,1,now())",
                (second_id,context.tenant_id))
        notebook,_=PostgresNotebookRepository(store).create(NotebookContext(context.tenant_id,second_id,context.actor_id,
            context.trace_id,context.policy_version),title='S42 알림 두 번째 Workspace',description=None,
            idempotency_key='s42-second-'+suffix,request_fingerprint='d'*64,now=datetime.now(timezone.utc))
        with psycopg.connect(scope.admin) as connection:
            assert connection.execute('SELECT activity_kind,actor_id FROM notebook_activities '
                'WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s ORDER BY sequence',
                (context.tenant_id,second_id,notebook.notebook_id)).fetchall()==[('created',context.actor_id)]
    finally:
        store.close()
    second_context = replace(second_context, notebook_id=notebook.notebook_id)
    second = SimpleNamespace(admin=scope.admin,app=scope.app,context=second_context,suffix=suffix,revision='0069',
        expected_notebook_activity_prefix=(('created',context.actor_id),))
    # 두 원사건을 먼저 확정한다. 기존 helper의 tenant 전체 알림0 준비 단언을 약화하지 않는다.
    scope = _pending_l2b_completed('document',scope=scope)
    second = _pending_l2b_completed('document',scope=second)
    originals=[]
    for current_scope in (scope,second):
        current=current_scope.context
        assert materialize_pending_completed(scope.app,context=current,limit=100)==1
        native=PostgresNotificationRepository(scope.app,context=CloudAccessContext(current.tenant_id,current.workspace_id,current.actor_id,'view'))
        items=native.notifications_for(tenant_id=current.tenant_id,recipient_id=current.actor_id)
        assert len(items)==1 and items[0].read_at is None and items[0].version==1
        current_scope.current_source_version=current_scope.completed_source_version
        originals.append(items[0])
    first_item,second_item=originals
    # 실제 준비와 원사건 검증이 끝난 뒤 새 집계 adapter를 요구한다.
    from daon_user_api.notification_postgres import ScopedPostgresNotificationRepository
    contexts = tuple(CloudAccessContext(context.tenant_id,workspace,context.actor_id,'view')
        for workspace in (context.workspace_id,second_id))
    repository = ScopedPostgresNotificationRepository(scope.app,tenant_id=context.tenant_id,
        recipient_id=context.actor_id,contexts=contexts)
    params={'tenant_id':context.tenant_id,'recipient_id':context.actor_id}
    before=_l2b_completion_snapshot(scope)
    items=repository.notifications_for(**params)
    assert {item.notification_id for item in items}=={first_item.notification_id,second_item.notification_id}
    assert repository.notification_for(**params,notification_id=first_item.notification_id)==first_item
    assert repository.notification_for(**params,notification_id=second_item.notification_id)==second_item
    assert _l2b_completion_snapshot(scope)==before
    assert repository.decode_cursor(repository.encode_cursor(second_item.notification_id))==second_item.notification_id
    changed=_l2_current_read_request(second,repository,second_item)
    assert changed.notification.read_at is not None and changed.notification.workspace_id==second_id
    assert repository.notification_for(**params,notification_id=first_item.notification_id).read_at is None
    with psycopg.connect(scope.admin) as connection:
        connection.execute('INSERT INTO identity_auth_source_access '
            '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
            'VALUES(%s,%s,%s,%s,0,1,now())',(context.tenant_id,context.workspace_id,context.actor_id,scope.current_source_version))
    assert [item.notification_id for item in repository.notifications_for(**params)]==[second_item.notification_id]
    assert repository.notification_for(**params,notification_id=first_item.notification_id) is None
    with pytest.raises(NotificationError) as foreign:
        repository.notifications_for(tenant_id='s42-foreign',recipient_id=context.actor_id)
    assert (foreign.value.code,foreign.value.http_status)==('RESOURCE_UNAVAILABLE',404)
    empty=ScopedPostgresNotificationRepository(scope.app,tenant_id=context.tenant_id,recipient_id='admin',contexts=())
    assert empty.notifications_for(tenant_id=context.tenant_id,recipient_id='admin')==()


def test_s42_runtime_actual_postgres_session_notification_read_and_current_source_denial():
    """실제 PG 세션/권한·원업무·HTTP. OIDC claims는 합성 port이며 실 Provider/브라우저 시험은 아니다."""
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4
    import psycopg
    from psycopg.conninfo import make_conninfo,conninfo_to_dict
    from fastapi.testclient import TestClient
    from daon_user_api.identity import IdentityService,ClientKind,DevicePlatform
    from daon_user_api.identity_postgres import PostgresIdentityRepository
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.notebook import NotebookContext
    from daon_user_api.notebook_postgres import PostgresNotebookRepository
    from daon_user_api.studio_workspace import StudioContext
    from daon_user_api.runtime import RuntimeSettings,RuntimeDependencies,create_app,WEB_SESSION_COOKIE
    from test_identity_support import FakeVerifiedOidcProvider,policy
    from test_notification_events import _PrivateDsn,_pending_l2b_completed
    from daon_user_api.notification_events import materialize_pending_completed
    dsn=os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not dsn:pytest.skip('S42 소유 격리 PG DSN 미제공')
    assert conninfo_to_dict(dsn).get('dbname')=='daon_user_s42_qa_20261017_l2'
    admin=_PrivateDsn(dsn);app=_PrivateDsn(make_conninfo(dsn,options='-c role=daon_app'))
    suffix=uuid4().hex
    tenant,workspace='s42-http-tenant-'+suffix,'s42-http-ws-'+suffix
    trace='s42-http-trace-'+suffix
    identity_repository=PostgresIdentityRepository(app)
    authorization_repository=PostgresAuthorizationRepository(app)
    audit=AuditEventStore();clock=lambda:datetime.now(timezone.utc)
    identity=IdentityService(repository=identity_repository,audit_store=audit,
        oidc_policies=(replace(policy(ClientKind.WEB),tenant_id=tenant),),clock=clock)
    cloud=PostgresCloudStore(app,min_size=0,max_size=2,notification_inbox_enabled=True)
    session=None;dependencies=None;client=None
    try:
        start=identity.begin_oidc_login(issuer='https://login.example.com',client_id='daon-web',audience='daon-user-api',
            redirect_uri='https://app.example.com/auth/callback',client_kind=ClientKind.WEB,tenant_id=tenant,
            trace_id=trace,policy_version=POLICY_VERSION)
        provider=FakeVerifiedOidcProvider(subject='s42-http-subject-'+suffix);provider.expected_nonce=start.nonce
        session=identity.complete_oidc_login(state=start.state,authorization_code=provider.authorization_code,
            code_verifier=start.code_verifier,client_id='daon-web',redirect_uri='https://app.example.com/auth/callback',
            provider=provider,platform=DevicePlatform.WEB,trace_id=trace,policy_version=POLICY_VERSION)
        cloud.seed_scope(CloudAccessContext(tenant,workspace,session.user_id,'source.create'))
        authorization_repository.bootstrap_workspace(tenant_id=tenant,workspace_id=workspace,owner_user_id=session.user_id,
            owner_role=Role.ORGANIZATION_ADMIN,workspace_kind='organization',data_area='cloud_sync',cost_limit_cents=1000,now=clock())
        notebook,_=PostgresNotebookRepository(cloud).create(NotebookContext(tenant,workspace,session.user_id,trace,POLICY_VERSION),
            title='S42 실제 PG HTTP 합성 QA',description=None,idempotency_key='s42-http-notebook-'+suffix,
            request_fingerprint='e'*64,now=clock())
        context=StudioContext(tenant,workspace,session.user_id,trace,POLICY_VERSION,notebook.notebook_id)
        scope=SimpleNamespace(admin=admin,app=app,context=context,suffix=suffix,revision='0069',
            expected_notebook_activity_prefix=(('created',session.user_id),))
        scope=_pending_l2b_completed('document',scope=scope)
        assert materialize_pending_completed(app,context=context,limit=100)==1
        dependencies=RuntimeDependencies(settings=RuntimeSettings(profile='test',bind_host='127.0.0.1',port=8000,
            cloud_database_dsn=app,policy_version=POLICY_VERSION,development_stage=True,notification_inbox_enabled=True),
            identity_service=identity,authorization_service=AuthorizationService(
                repository=authorization_repository,audit_store=audit,clock=clock,identity_service=identity),
            audit_store=audit,identity_repository=identity_repository,authorization_repository=authorization_repository,cloud_store=cloud)
        # lifespan을 시작하지 않아 다른 QA 사용자의 pending dispatcher/원장에 접근하지 않는다.
        client=TestClient(create_app(dependencies),base_url='http://127.0.0.1')
        auth={'cookie':f'{WEB_SESSION_COOKIE}={session.access_token}'}
        listed=client.get('/api/v1/notifications',headers=auth)
        assert listed.status_code==200
        assert listed.json()['data']['unread_count']==1 and len(listed.json()['data']['items'])==1
        item=listed.json()['data']['items'][0];notification_id=item['id']
        assert (item['workspace_id'],item['recipient_id'])==(workspace,session.user_id)
        detail=client.get('/api/v1/notifications/'+notification_id,headers=auth)
        assert detail.status_code==200 and detail.json()['data']==item
        headers={**auth,'If-Match':detail.headers['etag'],'Idempotency-Key':'s42-http-read-'+suffix}
        read=client.patch('/api/v1/notifications/'+notification_id,headers=headers,json={'state':'read'})
        assert read.status_code==200 and read.json()['data']['version']==2
        assert read.json()['data']['read_at'] is not None
        repeated=client.patch('/api/v1/notifications/'+notification_id,headers=headers,json={'state':'read'})
        assert repeated.status_code==200 and repeated.json()['data']==read.json()['data']
        assert client.get('/api/v1/notifications',headers=auth).json()['data']['unread_count']==0
        with psycopg.connect(admin) as connection:
            assert connection.execute('SELECT action,count(*) FROM notification_audit_outbox WHERE tenant_id=%s GROUP BY action ORDER BY action',
                (tenant,)).fetchall()==[('notification.created',1),('notification.read',1)]
            connection.execute('INSERT INTO identity_auth_source_access '
                '(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) '
                'VALUES(%s,%s,%s,%s,0,1,now())',(tenant,workspace,session.user_id,scope.completed_source_version))
        hidden=client.get('/api/v1/notifications',headers=auth)
        assert hidden.status_code==200 and hidden.json()['data']=={'items':[],'next_cursor':None,'unread_count':0}
        assert client.get('/api/v1/notifications/'+notification_id,headers=auth).status_code==404
        assert client.patch('/api/v1/notifications/'+notification_id,headers=headers,json={'state':'read'}).status_code==404
        with psycopg.connect(admin) as connection:
            assert connection.execute('SELECT action,count(*) FROM notification_audit_outbox WHERE tenant_id=%s GROUP BY action ORDER BY action',
                (tenant,)).fetchall()==[('notification.created',1),('notification.read',1)]
    finally:
        if client is not None:client.close()
        if session is not None:
            identity.revoke_current_web_session(access_token=session.access_token,trace_id=trace,policy_version=POLICY_VERSION)
        if dependencies is not None:dependencies.close()
        else:
            cloud.close();authorization_repository.close();identity_repository.close()


def test_notification_service_uses_current_recipient_scope_before_loading(tmp_path):
    """전역 목록을 읽은 뒤 Python에서 거르는 구현은 지속 저장에 사용하지 않는다."""
    class ScopedRepository(ReferenceNotificationRepository):
        def all_notifications(self):
            raise AssertionError("수신자 범위 없는 전체 알림 조회")

        def notification_by_id(self, notification_id):
            raise AssertionError("현재 주체 범위 없는 단건 조회")

        def notifications_for(self, *, tenant_id, recipient_id):
            return tuple(x for x in self._notifications.values()
                         if x.tenant_id == tenant_id and x.recipient_id == recipient_id)

        def notification_for(self, *, tenant_id, recipient_id, notification_id):
            return next((x for x in self.notifications_for(tenant_id=tenant_id, recipient_id=recipient_id)
                         if x.notification_id == notification_id), None)

    audit = AuditEventStore()
    path = tmp_path / "notification-scope.sqlite3"
    identity, _, _, clock = create_service(path, audit_store=audit)
    session = native_login(identity)
    principal = identity.describe_access(session.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION).principal
    auth_repo = SqliteAuthorizationRepository(path)
    auth_repo.bootstrap_workspace(tenant_id=principal.tenant_id, workspace_id="workspace-001",
        owner_user_id=principal.user_id, owner_role=Role.ORGANIZATION_ADMIN,
        workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=1000, now=clock())
    repository = ScopedRepository()
    service = NotificationService(repository=repository,
        authorization_service=AuthorizationService(repository=auth_repo, audit_store=audit, clock=clock, identity_service=identity),
        audit_store=audit, clock=clock)
    item, = service.publish(NotificationEvent(event_id="event-s42-scope", tenant_id=principal.tenant_id,
        workspace_id="workspace-001", kind="run_failed", severity="warning", title="실행 실패",
        summary="실행 상태를 확인하세요.", resource_type="run", resource_id="run-001",
        deep_link="/operations", trace_id=TRACE_ID, policy_version=POLICY_VERSION), candidates=(principal,))
    page = service.list_notifications(principal=principal, limit=20, cursor=None, filters={}, search=None,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    assert [x.notification_id for x in page.items] == [item.notification_id]
    assert service.get_notification(principal=principal, notification_id=item.notification_id,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION) == item


def test_s42_readiness_requires_notification_protection_and_exact_revision():
    from contextlib import contextmanager
    from daon_user_api.cloud_storage import PostgresCloudStore

    class Result:
        def __init__(self, row): self.row = row
        def fetchone(self): return self.row
    class Connection:
        def __init__(self, revision, vector): self.revision, self.vector = revision, vector
        def execute(self, sql, params=()):
            if 'alembic_version' in sql: return Result((self.revision,))
            if 'pg_extension' in sql: return Result(None if self.vector is None else (self.vector,))
            raise AssertionError(sql)
    class Pool:
        closed = False
        def __init__(self, connection): self.conn = connection
        @contextmanager
        def connection(self, **_kwargs): yield self.conn
    for revision in ('0067', '0068', '0069', '0070', 'unknown'):
        for protected in (False, True):
            for enabled in (False, True):
                store = object.__new__(PostgresCloudStore)
                store._pool = Pool(Connection(revision, '0.8.2'))
                store._notification_inbox_enabled = enabled
                store._organization_admin_v2_enabled = False
                store._organization_storage_is_protected = lambda connection: True
                store._notification_storage_is_protected = lambda connection: protected
                assert store.readiness().ready == ((revision == '0069' and protected)
                    or (not enabled and revision in ('0067', '0068'))), (revision, protected, enabled)
    store._pool = Pool(Connection('0069', None))
    assert not store.readiness().ready


def _qa_dsn():
    from psycopg.conninfo import conninfo_to_dict
    value = os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not value:
        pytest.skip('DAON_NOTIFICATION_TEST_DSN 미제공: 실제 PG 미실행')
    if conninfo_to_dict(value).get('dbname') != 'daon_user_s42_qa_20261017_l1':
        pytest.fail('승인된 S42 L1 전용 DB가 아님')
    return value


def _migration():
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/0069_notification_inbox.py'
    spec = importlib.util.spec_from_file_location('s42_notification_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sg_guard_empty_0068_full_upgrade_downgrade_upgrade():
    """별도 main 소유 빈 DB에서만 전체 후보를 적용한다. 기존 L2 DB/전역 역할/자료 삭제는 없다."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from daon_user_api.notification_postgres import notification_storage_is_protected
    from test_notification_events import _PrivateDsn

    dsn=_PrivateDsn(os.environ.get('DAON_NOTIFICATION_GUARD_TEST_DSN') or '')
    if not dsn:
        pytest.skip('DAON_NOTIFICATION_GUARD_TEST_DSN 미제공: 실제 빈 DB migration 미실행')
    expected_db='daon_user_s42_qa_20261018_sg_empty'
    expected_marker='S42 SG terminal guard disposable empty migration QA 2026-10-18 main owned; remove after guard verification'
    if conninfo_to_dict(dsn).get('dbname')!=expected_db:
        pytest.fail('승인된 SG 빈 전용 DB가 아님',pytrace=False)

    def shared_roles(connection):
        return connection.execute("SELECT (SELECT md5(string_agg(row_to_json(r)::text,',' ORDER BY rolname)) FROM pg_roles r),"
            "(SELECT md5(coalesce(string_agg(row_to_json(m)::text,',' ORDER BY roleid,member),'')) FROM pg_auth_members m)").fetchone()

    def job_protection(connection):
        return connection.execute("SELECT relowner::regrole::text,relrowsecurity,relforcerowsecurity,"
            "has_table_privilege('daon_app',oid,'SELECT'),has_table_privilege('daon_app',oid,'INSERT'),"
            "has_table_privilege('daon_app',oid,'UPDATE'),has_table_privilege('daon_app',oid,'DELETE'),"
            "has_table_privilege('daon_app',oid,'TRUNCATE') FROM pg_class WHERE oid='public.studio_generation_jobs'::regclass").fetchone()

    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT current_database(),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()==(expected_db,expected_marker)
        assert connection.execute('SELECT version_num FROM alembic_version').fetchall()==[('0068',)]
        assert connection.execute('SELECT count(*) FROM canon_transition_rules').fetchone()==(84,)
        for table in ('user_accounts','notifications','studio_generation_jobs'):
            assert connection.execute('SELECT count(*) FROM '+table).fetchone()==(0,)
        roles_before=shared_roles(connection)
        jobs_before=job_protection(connection)
        assert jobs_before[0]=='daon_app'
        # claim/purge 등 기존 함수를 수정하지 않았음을 소유권·본문·ACL까지 대조한다.
        old_functions=connection.execute("SELECT p.oid,md5(p.prosrc),p.proowner,p.proacl::text FROM pg_proc p "
            "WHERE p.pronamespace='public'::regnamespace AND p.prosrc LIKE '%studio_generation_jobs%' ORDER BY p.oid").fetchall()

    engine=create_engine('postgresql+psycopg://',creator=lambda:psycopg.connect(dsn))
    migration=_migration()
    def migrate(direction):
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                getattr(migration,direction)()
                revision='0069' if direction=='upgrade' else '0068'
                connection.exec_driver_sql('UPDATE alembic_version SET version_num=%s',(revision,))
    try:
        migrate('upgrade')
        with psycopg.connect(dsn) as connection:
            assert notification_storage_is_protected(connection)
            assert job_protection(connection)==('daon_notebook_delete',*jobs_before[1:3],True,True,True,False,False)
            assert connection.execute("SELECT has_table_privilege('daon_notebook_delete','public.studio_generation_jobs','DELETE'),"
                "pg_has_role('daon_app','daon_notebook_delete','MEMBER')").fetchone()==(True,False)
        # Job이 한 행이라도 있으면 down은 보호를 제거하지 않고 forward recovery를 요구한다.
        with engine.connect() as connection:
            transaction=connection.begin()
            try:
                connection.exec_driver_sql("INSERT INTO tenants(tenant_id,display_name) VALUES('s42-sg-empty','SG 빈 migration 합성 범위')")
                connection.exec_driver_sql("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES('s42-sg-empty','s42-sg-empty','SG 빈 migration 합성 Workspace')")
                connection.exec_driver_sql("INSERT INTO user_accounts(tenant_id,user_id) VALUES('s42-sg-empty','s42-sg-empty')")
                connection.exec_driver_sql("INSERT INTO studio_generation_jobs(tenant_id,workspace_id,job_id,actor_id,trace_id,policy_version,idempotency_key,request_json,state) "
                    "VALUES('s42-sg-empty','s42-sg-empty','s42-sg-empty','s42-sg-empty','s42-sg-empty','s42-sg-empty','s42-sg-empty','{}','queued')")
                with pytest.raises(DBAPIError) as caught:
                    with connection.begin_nested():
                        with Operations.context(MigrationContext.configure(connection)):
                            migration.downgrade()
                assert caught.value.orig.sqlstate=='55000'
                assert connection.exec_driver_sql("SELECT state,version,attempt FROM studio_generation_jobs WHERE job_id='s42-sg-empty'").fetchall()==[('queued',1,0)]
            finally:
                transaction.rollback()
        migrate('downgrade')
        with psycopg.connect(dsn) as connection:
            assert job_protection(connection)==jobs_before
            assert connection.execute("SELECT to_regprocedure('public.guard_studio_generation_job()'),to_regclass('public.notification_details')").fetchone()==(None,None)
            assert shared_roles(connection)==roles_before
        migrate('upgrade')
        with psycopg.connect(dsn) as connection:
            assert notification_storage_is_protected(connection)
            assert connection.execute('SELECT version_num FROM alembic_version').fetchall()==[('0069',)]
            for table in ('user_accounts','notifications','studio_generation_jobs'):
                assert connection.execute('SELECT count(*) FROM '+table).fetchone()==(0,)
            assert shared_roles(connection)==roles_before
            assert connection.execute("SELECT p.oid,md5(p.prosrc),p.proowner,p.proacl::text FROM pg_proc p "
                "WHERE p.oid=ANY(%s::oid[]) ORDER BY p.oid",([row[0] for row in old_functions],)).fetchall()==old_functions
    finally:
        engine.dispose()


def test_password_event_without_workspace_still_blocks_data_downgrade():
    """빈 migration 시험 뒤의 전용 DB만 사용한다. 사건만 있어도 유자료 down을 거부한다."""
    import secrets
    from datetime import datetime, timezone
    from uuid import uuid4
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.identity import IdentityService, DevicePlatform, PASSWORD_HASHER
    from daon_user_api.identity_postgres import PostgresIdentityRepository
    from test_account_notification_events import _PrivateValue
    from test_notification_events import _PrivateDsn

    __tracebackhide__ = True
    configured = os.environ.get('DAON_NOTIFICATION_GUARD_TEST_DSN')
    if not configured:
        pytest.skip('DAON_NOTIFICATION_GUARD_TEST_DSN 미제공: 실제 PG 미실행')
    dsn = _PrivateDsn(configured)
    assert conninfo_to_dict(dsn).get('dbname') == 'daon_user_s42_qa_20261018_sg_empty'
    marker = 'S42 SG terminal guard disposable empty migration QA 2026-10-18 main owned; remove after guard verification'
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT shobj_description(oid,'pg_database'),"
            '(SELECT version_num FROM alembic_version) FROM pg_database WHERE datname=current_database()'
        ).fetchone() == (marker, '0069')
        for table in ('notification_details', 'notification_source_outbox', 'notification_audit_outbox',
            'studio_generation_jobs', 'identity_password_change_events'):
            assert connection.execute('SELECT count(*) FROM public.' + table).fetchone() == (0,)
    actor = 's42-empty-p1-' + uuid4().hex
    app = _PrivateDsn(make_conninfo(dsn, options='-c role=daon_app'))
    password = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    replacement = _PrivateValue('S42!' + secrets.token_urlsafe(24))
    identity_repository = PostgresIdentityRepository(app)
    central = PostgresSecurityAuditStore(app, min_size=0, max_size=1)
    engine = create_engine('postgresql+psycopg://', creator=lambda: psycopg.connect(dsn))
    try:
        clock = lambda: datetime.now(timezone.utc)
        with identity_repository.transaction() as connection:
            identity_repository._ensure_tenant(connection, actor)
            connection.execute('INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,'
                'password_change_required,state) VALUES(?,?,?,?,?,?,?,?,?)',
                (actor, 'local', actor, actor, actor + '@example.test', PASSWORD_HASHER.hash(password),
                    clock().isoformat(), False, 'active'))
            connection.execute('INSERT INTO memberships(tenant_id,user_id,role) VALUES(?,?,?)',
                (actor, actor, 'personal_owner'))
        with psycopg.connect(dsn) as connection:
            connection.execute("INSERT INTO public.tenants(tenant_id,display_name) VALUES(%s,'S42 no-Workspace QA')", (actor,))
        identity = IdentityService(repository=identity_repository, audit_store=central, oidc_policies=(),
            clock=clock, dispatch_pending_audits=False)
        credentials = identity.local_login(login_id=actor, password=password, platform=DevicePlatform.WEB,
            trace_id='s42-empty-p1-login', policy_version='runtime-policy-v1')
        identity._notification_inbox_enabled = True
        identity.change_current_password(access_token=credentials.access_token, current_password=password,
            new_password=replacement, trace_id='s42-empty-p1-change', policy_version='runtime-policy-v1')
        with psycopg.connect(dsn) as connection:
            for table in ('notification_details', 'notification_source_outbox', 'notification_audit_outbox', 'studio_generation_jobs'):
                assert connection.execute('SELECT count(*) FROM public.' + table).fetchone() == (0,)
            assert connection.execute('SELECT count(*) FROM public.identity_password_change_events').fetchone() == (1,)
        with pytest.raises(DBAPIError) as caught:
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    _migration().downgrade()
        assert caught.value.orig.sqlstate == '55000'
        with psycopg.connect(dsn) as connection:
            assert connection.execute('SELECT count(*) FROM public.identity_password_change_events').fetchone() == (1,)
            assert connection.execute('SELECT version_num FROM alembic_version').fetchone() == ('0069',)
    finally:
        engine.dispose()
        central.close()
        identity_repository.close()


def test_0069_migration_executes_fixed_sql_without_external_bind_parameters():
    """SQLAlchemy의 :name 해석으로 고정 SQL 의미가 바뀌는 회귀를 잡는다."""
    from sqlalchemy import text
    module = _migration()
    raw_calls, down_calls, bind_calls, option_calls = [], [], [], []
    source = Path(module.__file__).with_suffix('.sql')
    original = module.op

    class Connection:
        def execution_options(self, *args, **kwargs):
            assert args == () and kwargs == {'no_parameters': True}
            option_calls.append(kwargs)
            return self

        def exec_driver_sql(self, sql, *args, **kwargs):
            assert args == () and kwargs == {}
            assert type(sql) is str and sql.encode('utf-8') == source.read_bytes()
            raw_calls.append(sql)

    connection = Connection()

    class Operations:
        def get_bind(self, *args, **kwargs):
            assert args == () and kwargs == {}
            bind_calls.append(True)
            return connection

        def execute(self, sql, *args, **kwargs):
            assert args == () and kwargs == {}
            compiled = text(sql).compile()
            assert compiled.params == {}
            down_calls.append(sql)
    try:
        module.op = Operations()
        module.upgrade()
        assert bind_calls == [True] and option_calls == [{'no_parameters': True}]
        assert len(raw_calls) == 1 and down_calls == []
        assert raw_calls[0] == source.read_bytes().decode('utf-8')
        module.downgrade()
        assert len(raw_calls) == 1 and len(down_calls) == 1
        assert bind_calls == [True] and option_calls == [{'no_parameters': True}]
    finally:
        module.op = original


def test_l1_isolated_notification_storage_restart_scope_atomicity_and_two_connections(monkeypatch):
    """main 준비 실제0068 schema-only 한 DB에서만 실행. 원본 행·전역 역할 조작은 없다."""
    import psycopg
    from psycopg.conninfo import make_conninfo
    from psycopg.types.json import Jsonb
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from daon_user_api.audit import ActorType, AuditEventDraft, AuditOutcome
    from daon_user_api.cloud_storage import CloudAccessContext
    import daon_user_api.notification_postgres as adapter

    dsn = _qa_dsn()
    with psycopg.connect(dsn) as db:
        assert db.execute('SELECT current_database()').fetchone()[0] == 'daon_user_s42_qa_20261017_l1'
        assert db.execute('SELECT version_num FROM alembic_version').fetchall() == [('0068',)]
        assert db.execute('SELECT count(*) FROM notifications').fetchone()[0] == 0
        roles_before = db.execute("SELECT md5(string_agg(row_to_json(r)::text,',' ORDER BY rolname)) FROM pg_roles r").fetchone()[0]
        edges_before = db.execute("SELECT md5(coalesce(string_agg(row_to_json(r)::text,',' ORDER BY roleid,member),'')) FROM pg_auth_members r").fetchone()[0]
        db.execute("INSERT INTO tenants(tenant_id,display_name) VALUES('s42-t','알림 격리'),('s42-other','다른 격리')")
        db.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES('s42-t','s42-w','범위1'),('s42-t','s42-w2','범위2'),('s42-other','s42-other-w','다른 범위')")
        db.execute("INSERT INTO user_accounts(tenant_id,user_id) VALUES('s42-t','s42-user'),('s42-t','s42-peer'),('s42-other','s42-user')")
        db.execute("INSERT INTO notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id) VALUES('s42-t','s42-w','s42-legacy','s42-user','s42-legacy-event')")
        baseline = db.execute("SELECT row_to_json(n)::text FROM notifications n WHERE notification_id='s42-legacy'").fetchone()[0]
        protections = db.execute("SELECT relname,relowner,relrowsecurity,relforcerowsecurity,relacl::text FROM pg_class WHERE oid IN ('notifications'::regclass,'idempotency_records'::regclass) ORDER BY relname").fetchall()

    engine = create_engine('postgresql+psycopg://', creator=lambda: psycopg.connect(dsn))
    migration = _migration()
    def upgrade():
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                connection.exec_driver_sql("UPDATE alembic_version SET version_num='0069'")
    def downgrade():
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                connection.exec_driver_sql("UPDATE alembic_version SET version_num='0068'")
    # DB 안의 본인 표 owner 변조로만 precondition을 검증한다. 공유 role은 불변이다.
    with psycopg.connect(dsn) as db:
        with pytest.raises(Exception, match='NOTIFICATION_MIGRATION_PRECONDITION'):
            with db.transaction():
                db.execute('ALTER TABLE notifications OWNER TO daon_notebook_delete')
                db.execute(Path(migration.__file__).with_suffix('.sql').read_text(encoding='utf-8'))
        # owner 변조 자체도 savepoint rollback되어 기존 owner/ACL을 재구성하지 않는다.
    upgrade(); downgrade(); upgrade()
    context = CloudAccessContext('s42-t', 's42-w', 's42-user', 'view')
    app_dsn = make_conninfo(dsn, options='-c role=daon_app')
    repo = adapter.PostgresNotificationRepository(app_dsn, context=context)
    assert repo.notifications_for(tenant_id='s42-t', recipient_id='s42-user') == ()

    def seed(key):
        with psycopg.connect(dsn) as db:
            db.execute("INSERT INTO notification_source_outbox(tenant_id,workspace_id,source_event_id,recipient_id,source_store,origin_event_id,action,target_type,target_id,kind,severity,trace_id,policy_version,occurred_at) VALUES('s42-t','s42-w',%s,'s42-user','document',%s,'run.completed','run',%s,'run_state','info','trace-s42','policy-s42',now())", ('event-'+key,'origin-'+key,'run-'+key))
            db.execute("INSERT INTO notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id) VALUES('s42-t','s42-w',%s,'s42-user',%s)", (key,'event-'+key))
            db.execute("INSERT INTO notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,kind,severity,title,summary,resource_type,resource_id,deep_link,trace_id,policy_version,delivery_state,delivered_at) VALUES('s42-t',%s,'document',%s,%s,'run_state','info','실행 완료','저장한 실행을 확인하세요.','run',%s,'/operations','trace-s42','policy-s42','delivered',now())", (key,'origin-'+key,'event-'+key,'run-'+key))

    def draft(before, after):
        return AuditEventDraft(event_id='audit-read-'+before.notification_id, occurred_at=after.read_at,
            actor_id='s42-user', actor_type=ActorType.USER, tenant_id='s42-t',workspace_id='s42-w',
            action='notification.read', target_type='notification',target_id=before.notification_id,
            outcome=AuditOutcome.SUCCEEDED,trace_id='trace-s42-read',policy_version='policy-s42',
            before={'state':'unread','version':before.version},after={'state':'read','version':after.version},
            metadata={'source_event_id':before.source_event_id})
    def read(key, request_key='s42-read-key-0001', fingerprint='a'*64, repository=repo):
        return repository.transition_read(notification_id=key,tenant_id='s42-t',recipient_id='s42-user',
            expected_etag='"notification-1"',idempotency_key=request_key,fingerprint=fingerprint,
            read_at=datetime(2026,10,17,tzinfo=timezone.utc),on_first_transition=lambda *_: pytest.fail('중앙감사를 commit 전에 호출함'),
            audit_draft_factory=draft)
    seed('s42-one')
    original = repo.notification_for(tenant_id='s42-t', recipient_id='s42-user', notification_id='s42-one')
    assert original.title == '실행 완료' and original.version == 1 and original.read_at is None
    for tenant, workspace, actor in [('s42-t','s42-w','s42-peer'),('s42-t','s42-w2','s42-user'),('s42-other','s42-other-w','s42-user')]:
        other = adapter.PostgresNotificationRepository(app_dsn, context=CloudAccessContext(tenant,workspace,actor,'view'))
        assert other.notifications_for(tenant_id=tenant,recipient_id=actor) == ()
        assert other.notification_for(tenant_id=tenant,recipient_id=actor,notification_id='s42-one') is None
    changed = read('s42-one')
    assert changed.first_transition and changed.notification.version == 2
    assert read('s42-one').notification == changed.notification and not read('s42-one').first_transition
    with pytest.raises(NotificationError) as conflict: read('s42-one',fingerprint='b'*64)
    assert conflict.value.http_status == 409
    with pytest.raises(NotificationError) as stale: read('s42-one',request_key='s42-read-key-0002')
    assert stale.value.http_status == 412
    reopened = adapter.PostgresNotificationRepository(app_dsn, context=context)
    assert reopened.notification_for(tenant_id='s42-t',recipient_id='s42-user',notification_id='s42-one') == changed.notification

    # 새 Python 프로세스는 같은 지속 내용/읽음/version을 읽고 부모의 메모리를 상속하지 않는다.
    code = """import os,json
from psycopg.conninfo import make_conninfo
from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.notification_postgres import PostgresNotificationRepository
r=PostgresNotificationRepository(make_conninfo(os.environ['DAON_NOTIFICATION_TEST_DSN'],options='-c role=daon_app'),context=CloudAccessContext('s42-t','s42-w','s42-user','view'))
x=r.notification_for(tenant_id='s42-t',recipient_id='s42-user',notification_id='s42-one')
print(json.dumps([x.title,x.version,x.read_at.isoformat()]))
"""
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'),PYTHONIOENCODING='utf-8')
    process = subprocess.run([sys.executable,'-B','-c',code],env=environment,capture_output=True,text=True,encoding='utf-8',timeout=15)
    assert process.returncode == 0, '새 프로세스 지속 조회 실패(접속 원문 출력 금지)'
    assert json.loads(process.stdout) == ['실행 완료',2,'2026-10-17T00:00:00+00:00']

    # 각 실패는 실제 DB transaction의 notification/receipt/audit 동시 rollback을 확인한다.
    for phase in ('update','receipt','audit','dto'):
        item_id = 's42-fail-'+phase; seed(item_id)
        if phase != 'dto':
            table = {'update':'notifications','receipt':'idempotency_records','audit':'notification_audit_outbox'}[phase]
            with psycopg.connect(dsn) as db:
                db.execute("CREATE FUNCTION s42_test_fail() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'S42_TEST_FAILURE'; END$$")
                db.execute(f'CREATE TRIGGER s42_test_failure BEFORE {"UPDATE" if phase=="update" else "INSERT"} ON {table} FOR EACH ROW EXECUTE FUNCTION s42_test_fail()')
        else:
            original_time = adapter._time
            def fail_after_mutation(value):
                if isinstance(value,str): raise ValueError('S42_DTO_FAILURE')
                return original_time(value)
            monkeypatch.setattr(adapter,'_time',fail_after_mutation)
        with pytest.raises((NotificationError,ValueError)):
            read(item_id,request_key='key-'+item_id+'-0001')
        monkeypatch.undo()
        with psycopg.connect(dsn) as db:
            if phase != 'dto':
                db.execute(f'DROP TRIGGER s42_test_failure ON {table}'); db.execute('DROP FUNCTION s42_test_fail()')
            assert db.execute('SELECT version,read_at FROM notifications WHERE notification_id=%s',(item_id,)).fetchone()==(1,None)
            assert db.execute("SELECT count(*) FROM idempotency_records WHERE result->>'notification_id'=%s",(item_id,)).fetchone()[0]==0
            assert db.execute("SELECT count(*) FROM notification_audit_outbox WHERE notification_id=%s AND action='notification.read'",(item_id,)).fetchone()[0]==0

    for same_key in (True,False):
        item_id='s42-race-'+str(same_key).lower(); seed(item_id); barrier=Barrier(2)
        def race(index):
            barrier.wait(timeout=5)
            try: return read(item_id,request_key='race-'+item_id+'-'+str(0 if same_key else index)).notification.version
            except NotificationError as error: return error.http_status
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(race,range(2)))
        assert sorted(results)==([2,2] if same_key else [2,412])
        with psycopg.connect(dsn) as db:
            assert db.execute("SELECT count(*) FROM notification_audit_outbox WHERE notification_id=%s AND action='notification.read'",(item_id,)).fetchone()[0]==1
    with repo.transaction() as db:
        assert db.execute('SELECT current_user AS role').fetchone()['role']=='daon_app'
        pending = db.execute('SELECT * FROM pending_notification_audits(100)').fetchall()
        assert pending and all(row['draft']['metadata'].keys()=={'source_event_id'} for row in pending)
        first=pending[0]
        assert db.execute('SELECT ack_notification_audit(%s,%s) AS ack',(first['event_id'],first['fingerprint'])).fetchone()['ack']
    assert reopened.notification_for(tenant_id='s42-t',recipient_id='s42-user',notification_id='s42-one').version==2
    for statement in ("UPDATE notification_details SET title='조작'", "DELETE FROM notification_details", "INSERT INTO notification_details SELECT * FROM notification_details",
                      "UPDATE notifications SET read_at=now(),version=version+1 WHERE notification_id='s42-fail-update'"):
        with pytest.raises(NotificationError):
            with repo.transaction() as db: db.execute(statement)
    with pytest.raises(Exception,match='NOTIFICATION_FORWARD_RECOVERY_REQUIRED'): downgrade()
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT row_to_json(n)::text FROM notifications n WHERE notification_id='s42-legacy'").fetchone()[0]==baseline
        assert db.execute("SELECT relname,relowner,relrowsecurity,relforcerowsecurity,relacl::text FROM pg_class WHERE oid IN ('notifications'::regclass,'idempotency_records'::regclass) ORDER BY relname").fetchall()==protections
        assert db.execute("SELECT md5(string_agg(row_to_json(r)::text,',' ORDER BY rolname)) FROM pg_roles r").fetchone()[0]==roles_before
        assert db.execute("SELECT md5(coalesce(string_agg(row_to_json(r)::text,',' ORDER BY roleid,member),'')) FROM pg_auth_members r").fetchone()[0]==edges_before
    engine.dispose()


@pytest.fixture
def existing_notification_0069_db():
    """main의 L1 실제시험이 남긴 전용0069 자료만 사용한다. seed/DDL 재적용은 없다."""
    import psycopg
    with psycopg.connect(_qa_dsn(), autocommit=True) as connection:
        assert connection.execute('SELECT current_database()').fetchone()[0] == 'daon_user_s42_qa_20261017_l1'
        assert connection.execute('SELECT version_num FROM alembic_version').fetchall() == [('0069',)]
        assert connection.execute("SELECT recipient_id FROM notifications WHERE tenant_id='s42-t' AND notification_id='s42-one'").fetchone() == ('s42-user',)
        yield connection


@pytest.mark.parametrize('statement', [
    "UPDATE notifications SET recipient_id='s42-peer' WHERE notification_id='s42-one'",
    "UPDATE notifications SET source_event_id='s42-peer-hijack-event' WHERE notification_id='s42-one'",
    "UPDATE notifications SET read_at='2026-10-18T00:00:00Z' WHERE notification_id='s42-one'",
    "UPDATE notifications SET version=version+1 WHERE notification_id='s42-one'",
    "UPDATE notifications SET read_at=NULL,version=version+1 WHERE notification_id='s42-one'",
    "UPDATE notifications SET created_at='2026-10-18T00:00:00Z' WHERE notification_id='s42-one'",
    "UPDATE notifications SET workspace_id='s42-w2' WHERE notification_id='s42-one'",
    "UPDATE notifications SET tenant_id='s42-other',workspace_id='s42-other-w' WHERE notification_id='s42-one'",
    "DELETE FROM notifications WHERE notification_id='s42-one'",
], ids=['recipient','source-event','read-time','version','unread-reversal','created-time','workspace','tenant','delete'])
def test_l1_existing_0069_rejects_peer_parent_change(existing_notification_0069_db, statement):
    """다른 수신자의 상세가 RLS로 안 보여도 보호 parent 변경을 허용하면 실패한다."""
    import psycopg
    connection = existing_notification_0069_db
    before = connection.execute("SELECT row_to_json(n) FROM notifications n WHERE notification_id IN ('s42-one','s42-legacy') ORDER BY notification_id").fetchall()
    try:
        with connection.transaction(force_rollback=True):
            connection.execute('SET LOCAL ROLE daon_app')
            for name, value in (('tenant_id','s42-t'),('workspace_id','s42-w'),('actor_id','s42-peer'),('capability','view')):
                connection.execute('SELECT set_config(%s,%s,true)', ('app.'+name, value))
            assert connection.execute('SELECT current_user').fetchone()[0] == 'daon_app'
            assert connection.execute("SELECT notification_id FROM notification_details WHERE notification_id='s42-one'").fetchall() == []
            with pytest.raises(psycopg.Error) as denied:
                connection.execute(statement)
            # FK 위반 우연으로 삭제가 막힌 것은 guard 보호 증거로 인정하지 않는다.
            assert denied.value.sqlstate in {'55000','42501'}
    finally:
        assert connection.execute("SELECT row_to_json(n) FROM notifications n WHERE notification_id IN ('s42-one','s42-legacy') ORDER BY notification_id").fetchall() == before


_L1_CONSTRAINT_DRIFT = [
    ('notification_source_outbox','p',('tenant_id','workspace_id','source_event_id','recipient_id')),
    ('notification_source_outbox','f',('tenant_id','workspace_id')),
    ('notification_source_outbox','f',('tenant_id','recipient_id')),
    ('notification_details','p',('tenant_id','notification_id')),
    ('notification_details','f',('tenant_id','notification_id')),
    ('notification_audit_outbox','p',('event_id',)),
    ('notification_audit_outbox','u',('tenant_id','notification_id','action')),
    ('notification_audit_outbox','f',('tenant_id','workspace_id')),
    ('notifications','p',('tenant_id','notification_id')),
    ('notifications','u',('tenant_id','recipient_id','source_event_id')),
    ('notifications','f',('tenant_id','workspace_id')),
    ('notifications','f',('tenant_id','recipient_id')),
    ('idempotency_records','p',('tenant_id','workspace_id','actor_id','operation','idempotency_key')),
    ('idempotency_records','f',('tenant_id','workspace_id')),
]
_L1_PARENT_DRIFT = [
    'ALTER TABLE notifications OWNER TO daon_notebook_delete',
    'ALTER TABLE notifications DISABLE ROW LEVEL SECURITY',
    'ALTER TABLE notifications NO FORCE ROW LEVEL SECURITY',
    'ALTER TABLE idempotency_records OWNER TO daon_notebook_delete',
    'ALTER TABLE idempotency_records DISABLE ROW LEVEL SECURITY',
    'ALTER TABLE idempotency_records NO FORCE ROW LEVEL SECURITY',
    'ALTER TABLE notification_source_outbox ALTER COLUMN origin_event_id DROP NOT NULL',
    'ALTER TABLE notification_source_outbox ALTER COLUMN version SET DEFAULT 2',
    "ALTER TABLE notification_source_outbox ALTER COLUMN state SET DEFAULT 'delivered'",
]


@pytest.mark.parametrize('drift', _L1_CONSTRAINT_DRIFT + _L1_PARENT_DRIFT,
    ids=[f'{table}-{kind}-'+','.join(columns) for table,kind,columns in _L1_CONSTRAINT_DRIFT]
        + ['parent-owner','parent-rls','parent-force','receipt-owner','receipt-rls','receipt-force',
           'source-required-field','source-version-default','source-state-default'])
def test_l1_existing_0069_readiness_detects_constraint_drift(existing_notification_0069_db, drift):
    """정상 True가 선행 조건이다. CHECK 외 관계·parent 보호가 약해지면 False여야 한다."""
    from psycopg import sql
    from daon_user_api.notification_postgres import notification_storage_is_protected
    connection = existing_notification_0069_db
    assert notification_storage_is_protected(connection), '변조 전 정상0069 보호가 True여야 함'
    snapshot_sql = """SELECT c.relname,c.relowner,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,
        (SELECT jsonb_agg(to_jsonb(k) ORDER BY k.oid) FROM pg_constraint k WHERE k.conrelid=c.oid),
        (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum) FROM pg_attribute a WHERE a.attrelid=c.oid),
        (SELECT jsonb_agg(to_jsonb(d) ORDER BY d.adnum) FROM pg_attrdef d WHERE d.adrelid=c.oid)
        FROM pg_class c WHERE c.oid=ANY(ARRAY['notifications'::regclass,'idempotency_records'::regclass,
          'notification_details'::regclass,'notification_source_outbox'::regclass,'notification_audit_outbox'::regclass])
        ORDER BY c.relname"""
    before = connection.execute(snapshot_sql).fetchall()
    try:
        with connection.transaction(force_rollback=True):
            if isinstance(drift, tuple):
                table, kind, columns = drift
                constraint = connection.execute("""SELECT k.conname FROM pg_constraint k
                    WHERE k.conrelid=%s::regclass AND k.contype=%s
                    AND ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(num,pos)
                        JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.num ORDER BY x.pos)=%s::text[]""",
                    ('public.'+table, kind, list(columns))).fetchall()
                assert len(constraint) == 1, '시험 대상 exact 제약이 하나여야 함'
                # parent PK를 참조하는 새 FK도 이 QA savepoint 안에서만 제거·자동 rollback된다.
                connection.execute(sql.SQL('ALTER TABLE {} DROP CONSTRAINT {} CASCADE').format(
                    sql.Identifier('public',table),sql.Identifier(constraint[0][0])))
            else:
                connection.execute(drift)
            assert not notification_storage_is_protected(connection), f'보호 변조 미탐지: {drift}'
    finally:
        assert connection.execute(snapshot_sql).fetchall() == before
        assert notification_storage_is_protected(connection), 'rollback 후 정상0069 보호가 복원되어야 함'


@pytest.mark.parametrize('drift', ['when-false','update-of-read-at','same-name-other-function-schema','same-name-other-table-schema'])
def test_l1_existing_0069_readiness_detects_trigger_drift(existing_notification_0069_db, drift):
    """필수 trigger의 이름뿐 아니라 무조건 실행과 정확한 표/함수 연결을 요구한다."""
    from daon_user_api.notification_postgres import notification_storage_is_protected
    connection = existing_notification_0069_db
    assert notification_storage_is_protected(connection), '변조 전 정상0069 보호가 True여야 함'
    assert connection.execute("SELECT to_regnamespace('s42_l1_trigger_drift')").fetchone() == (None,)
    trigger_names = ['notification_details_guard','notification_details_created','notification_source_guard',
                     'notification_audit_guard','notification_parent_read_guard']
    snapshot_sql = """SELECT t.oid,t.tgrelid,t.tgfoid,t.tgtype,t.tgenabled,t.tgisinternal,t.tgattr::text,
        t.tgqual::text,pg_get_triggerdef(t.oid) FROM pg_trigger t WHERE t.tgname=ANY(%s) ORDER BY t.oid"""
    before = connection.execute(snapshot_sql, (trigger_names,)).fetchall()
    assert len(before) == 5
    try:
        with connection.transaction(force_rollback=True):
            connection.execute('DROP TRIGGER notification_parent_read_guard ON public.notifications')
            if drift == 'when-false':
                connection.execute("""CREATE TRIGGER notification_parent_read_guard BEFORE UPDATE OR DELETE
                    ON public.notifications FOR EACH ROW WHEN(false)
                    EXECUTE FUNCTION public.guard_notification_parent()""")
            elif drift == 'update-of-read-at':
                connection.execute("""CREATE TRIGGER notification_parent_read_guard BEFORE UPDATE OF read_at OR DELETE
                    ON public.notifications FOR EACH ROW EXECUTE FUNCTION public.guard_notification_parent()""")
            else:
                # 한 격리 transaction 안에서만 생성하고 전체를 rollback한다. 공유 role은 변경하지 않는다.
                connection.execute('CREATE SCHEMA s42_l1_trigger_drift')
                if drift == 'same-name-other-function-schema':
                    connection.execute("""CREATE FUNCTION s42_l1_trigger_drift.guard_notification_parent()
                        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                        IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$""")
                    connection.execute("""CREATE TRIGGER notification_parent_read_guard BEFORE UPDATE OR DELETE
                        ON public.notifications FOR EACH ROW
                        EXECUTE FUNCTION s42_l1_trigger_drift.guard_notification_parent()""")
                else:
                    connection.execute('CREATE TABLE s42_l1_trigger_drift.notifications (LIKE public.notifications)')
                    connection.execute("""CREATE TRIGGER notification_parent_read_guard BEFORE UPDATE OR DELETE
                        ON s42_l1_trigger_drift.notifications FOR EACH ROW
                        EXECUTE FUNCTION public.guard_notification_parent()""")
            changed = connection.execute("""SELECT t.tgqual IS NOT NULL,t.tgattr::text<>'',
                t.tgrelid='public.notifications'::regclass,t.tgfoid='public.guard_notification_parent()'::regprocedure
                FROM pg_trigger t WHERE t.tgname='notification_parent_read_guard'""").fetchall()
            expected = {
                'when-false': (True,False,True,True),
                'update-of-read-at': (False,True,True,True),
                'same-name-other-function-schema': (False,False,True,False),
                'same-name-other-table-schema': (False,False,False,True),
            }
            assert changed == [expected[drift]], '의도한 trigger 변조가 실제 catalog에 존재해야 함'
            assert not notification_storage_is_protected(connection), f'trigger 위장 미탐지: {drift}'
    finally:
        assert connection.execute(snapshot_sql, (trigger_names,)).fetchall() == before
        assert connection.execute("SELECT to_regnamespace('s42_l1_trigger_drift')").fetchone() == (None,)
        assert notification_storage_is_protected(connection), 'rollback 후 정상0069 보호가 복원되어야 함'
