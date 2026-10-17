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


def test_0069_migration_executes_fixed_sql_without_external_bind_parameters():
    """SQLAlchemy의 :name 해석으로 고정 SQL 의미가 바뀌는 회귀를 잡는다."""
    from sqlalchemy import text
    module = _migration()
    calls = []
    original = module.op
    class Operations:
        def execute(self, sql):
            compiled = text(sql).compile()
            assert compiled.params == {}
            calls.append(sql)
    try:
        module.op = Operations()
        module.upgrade()
        assert len(calls) == 1
        assert calls[0] == Path(module.__file__).with_suffix('.sql').read_text(encoding='utf-8')
        module.downgrade()
        assert len(calls) == 2
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
