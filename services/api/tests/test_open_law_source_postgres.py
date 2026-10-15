from dataclasses import replace
import importlib
import os
from uuid import uuid4

import pytest

from test_open_law_source_import import context,document,selection


def api():return importlib.import_module('daon_user_api.open_law_source_postgres')


@pytest.fixture
def pg():
    value=os.environ.get('DAON_S15_ISOLATED_POSTGRES_DSN')
    if not value:pytest.skip('S15 explicit isolated PostgreSQL DSN not supplied; not PG PASS')
    import psycopg
    with psycopg.connect(value) as conn:
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='0062'
    return value


def seed(dsn,*,auth_role='personal_owner',required_approver='organization_admin'):
    import psycopg
    from psycopg.types.json import Jsonb
    from daon_user_api.open_law_mcp_server import PROTOCOL,SCHEMA_DIGEST
    suffix=uuid4().hex[:10];ctx=replace(context(),tenant_id='s15-t-'+suffix,workspace_id='s15-w-'+suffix,
        actor_id='s15-a-'+suffix,notebook_id='s15-n-'+suffix)
    with psycopg.connect(dsn) as conn:
        conn.execute('INSERT INTO tenants(tenant_id,display_name) VALUES(%s,%s)',(ctx.tenant_id,'S15 QA'))
        conn.execute('INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,%s)',(ctx.tenant_id,ctx.workspace_id,'S15 QA'))
        conn.execute("INSERT INTO user_accounts(tenant_id,user_id,status) VALUES(%s,%s,'active')",(ctx.tenant_id,ctx.actor_id))
        conn.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s15-fixture',%s,'active')",(ctx.actor_id,ctx.actor_id))
        conn.execute("INSERT INTO memberships(tenant_id,workspace_id,user_id,role) VALUES(%s,%s,%s,'personal_owner')",(ctx.tenant_id,ctx.workspace_id,ctx.actor_id))
        conn.execute('INSERT INTO identity_auth_workspaces(workspace_id,tenant_id,workspace_kind,data_area,cost_limit_cents,acl_version,version,updated_at) VALUES(%s,%s,%s,%s,0,1,1,now())',
            (ctx.workspace_id,ctx.tenant_id,'personal' if auth_role=='personal_owner' else 'organization','workspace'))
        if auth_role=='personal_owner':
            conn.execute("INSERT INTO identity_auth_tenant_roles(tenant_id,user_id,role,state,version,updated_at) VALUES(%s,%s,'personal_owner','active',1,now())",(ctx.tenant_id,ctx.actor_id))
        else:
            conn.execute("INSERT INTO identity_auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) VALUES(%s,%s,%s,%s,'active',1,now())",(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,auth_role))
            conn.execute("UPDATE memberships SET role='workspace_manager' WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s",(ctx.tenant_id,ctx.workspace_id,ctx.actor_id))
        conn.execute('INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())',(ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,ctx.actor_id))
        from daon_user_api.data_canon import canonical_json_bytes
        import hashlib
        policy={'mode':'allow_approved_external','allowed_provider_kinds':['external_api'],'allowed_destinations':['www.law.go.kr'],
            'classification':'public','max_bytes':2097152,'masking_required':True,'redaction_required':True,'required_approver':required_approver}
        for scope,workspace in [('organization',None),('workspace',ctx.workspace_id)]:
            pid='s15-policy-'+suffix+'-'+scope;bid='s15-binding-'+suffix+'-'+scope
            canonical=canonical_json_bytes(policy).decode()
            conn.execute("INSERT INTO egress_policy_versions(tenant_id,organization_id,workspace_id,policy_version_id,scope_type,policy_version,state,canonical_json,canonical_text,digest_sha256,created_by,trace_id) "
                "VALUES(%s,%s,%s,%s,%s,1,'active',%s,%s,%s,%s,%s)",
                (ctx.tenant_id,ctx.tenant_id,workspace,pid,scope,Jsonb(policy),canonical,hashlib.sha256(canonical.encode()).hexdigest(),ctx.actor_id,ctx.trace_id))
            conn.execute("INSERT INTO egress_policy_bindings(tenant_id,organization_id,workspace_id,binding_id,scope_type,policy_version_id,binding_version,active,current,created_by,trace_id) "
                "VALUES(%s,%s,%s,%s,%s,%s,1,true,true,%s,%s)",(ctx.tenant_id,ctx.tenant_id,workspace,bid,scope,pid,ctx.actor_id,ctx.trace_id))
        conn.execute("INSERT INTO workspace_connectors(tenant_id,workspace_id,connector_id,kind,name,endpoint_label,status,source_count,sources_json,verification_json,created_at,updated_at) "
            "VALUES(%s,%s,'mcp-open-law-go-kr','mcp','국가법령정보센터','open.law.go.kr','connected',0,'[]',%s,now(),now())",
            (ctx.tenant_id,ctx.workspace_id,Jsonb({'protocol_version':PROTOCOL,'tool_schema_digest':SCHEMA_DIGEST,'checked_at':'2026-10-16T00:00:00Z'})))
    return ctx


def repository(dsn):return api().PostgresOpenLawSourceRepository(dsn,policy_fingerprint=lambda _:'frozen-policy')


def promoted(dsn,ctx,claim):
    import psycopg
    from daon_user_api.open_law_source_import import object_id
    doc=document();ids=[]
    with psycopg.connect(dsn) as conn:
        for kind,content,digest in [('raw',doc.raw_content,doc.raw_sha256),('text',doc.text_content,doc.text_sha256)]:
            oid=object_id(claim,kind);ids.append(oid)
            conn.execute("INSERT INTO object_records(tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,byte_size,content_type,status,created_by,trace_id,idempotency_key,request_fingerprint,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,%s,%s,'completed',%s,%s,%s,%s,now())",
                (ctx.tenant_id,ctx.workspace_id,oid,'staging/'+oid,'source/'+oid,digest,len(content),'application/json' if kind=='raw' else 'text/plain',ctx.actor_id,ctx.trace_id,oid,digest))
    return ids


def claim(repo,ctx,key='request-1'):return repo.claim(ctx,selection(),document().raw_sha256,document().text_sha256,key)


def test_law_connector_projection_counts_owned_active_snapshot_bindings_not_metadata(pg,monkeypatch):
    from daon_user_api.connector_postgres import PostgresConnectorRepository
    from daon_user_api.cloud_storage import PostgresCloudStore
    import psycopg
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg);attempt=claim(repo,ctx);result=repo.complete(ctx,attempt,document(),*promoted(pg,ctx,attempt))
    cloud=PostgresCloudStore(pg);connectors=PostgresConnectorRepository(cloud)
    try:
        # Persisted counters and JSON are deliberately still zero/empty.
        with psycopg.connect(pg) as conn:
            assert conn.execute('SELECT source_count,sources_json FROM workspace_connectors WHERE workspace_id=%s',(ctx.workspace_id,)).fetchone()==(0,[])
        assert connectors.get(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'mcp-open-law-go-kr').source_count==1
        items=connectors.sources(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'mcp-open-law-go-kr')
        assert len(items)==1 and items[0].source_id==result.source_id and items[0].usable
        # Another actor never sees this Notebook's snapshot/count.
        assert connectors.sources(ctx.tenant_id,ctx.workspace_id,'unrelated-actor','mcp-open-law-go-kr')==()
        connectors.unregister(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'mcp-open-law-go-kr')
        with psycopg.connect(pg) as conn:
            assert conn.execute('SELECT count(*) FROM notebook_source_unbindings WHERE source_id=%s',(result.source_id,)).fetchone()[0]==1
            assert conn.execute('SELECT count(*) FROM open_law_source_snapshots WHERE source_id=%s',(result.source_id,)).fetchone()[0]==1
    finally:cloud.close();repo.close()


def test_confirmed_failure_cas_blocks_saved_copy_and_preserves_new_verification(pg,monkeypatch):
    from daon_user_api.connector_postgres import PostgresConnectorRepository
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.open_law_source_import import OpenLawImportError
    from daon_user_api.open_law_mcp_server import PROTOCOL,SCHEMA_DIGEST
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg);attempt=claim(repo,ctx);stored=repo.complete(ctx,attempt,document(),*promoted(pg,ctx,attempt))
    cloud=PostgresCloudStore(pg);connectors=PostgresConnectorRepository(cloud)
    try:
        assert repo.get_snapshot(ctx,stored.source_version_id).source_id==stored.source_id
        _,_,observed=connectors.law_verification(ctx.tenant_id,ctx.workspace_id,ctx.actor_id)
        assert connectors.mark_law_unavailable(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'OPEN_LAW_CREDENTIAL_REQUIRED',observed)
        with pytest.raises(OpenLawImportError,match='OPEN_LAW_ACCESS_DENIED'):repo.get_snapshot(ctx,stored.source_version_id)
        # Existing reconnect remains local/unverified, and explicit successful
        # probe produces a new revision. Old failure must not replace it.
        connectors.set_status(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'mcp-open-law-go-kr',status='unavailable',error_code='CONNECTOR_NOT_VERIFIED')
        _,_,fresh=connectors.law_verification(ctx.tenant_id,ctx.workspace_id,ctx.actor_id)
        receipt={'protocol_version':PROTOCOL,'tool_schema_digest':SCHEMA_DIGEST,'checked_at':'2026-10-16T00:00:00Z'}
        connectors.verify_law(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,receipt,fresh)
        assert not connectors.mark_law_unavailable(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'OPEN_LAW_CREDENTIAL_REQUIRED',observed)
        assert not connectors.mark_law_unavailable(ctx.tenant_id,'other-workspace',ctx.actor_id,'OPEN_LAW_UPSTREAM_FAILED',fresh)
        assert connectors.get(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'mcp-open-law-go-kr').status=='connected'
        assert repo.get_snapshot(ctx,stored.source_version_id).source_id==stored.source_id
    finally:cloud.close();repo.close()


def run_target_queued_job(dsn,queue,studio,job_id):
    """과거 RED의 합성 작업은 잠금만 유지. 실제 global claim/worker를 실행한다."""
    import psycopg
    from daon_user_api.studio_generation_worker import StudioGenerationWorker
    with psycopg.connect(dsn) as competing:
        competing.execute("SET LOCAL lock_timeout='1s'")
        others=competing.execute(
            "SELECT job_id FROM studio_generation_jobs WHERE job_id<>%s "
            "AND tenant_id LIKE 's15-t-%%' AND workspace_id LIKE 's15-w-%%' "
            "AND actor_id LIKE 's15-a-%%' AND request_json->>'purpose'='S15 QA' "
            "AND (state='queued' OR (state IN ('leased','generating') AND lease_until<now())) FOR UPDATE",
            (job_id,)).fetchall()
        print('S15_SYNTHETIC_COMPETING_QUEUE_ROWS_LOCKED='+str(len(others)))
        # SKIP LOCKED는 실제 queue SQL을 그대로 사용하며 어느 과거 행도 변경하지 않는다.
        assert StudioGenerationWorker(queue,studio,worker_id='s15-worker-'+uuid4().hex).run_once()


def test_missing_policy_never_opens_claim_database():
    repo=api().PostgresOpenLawSourceRepository('postgresql://not-used')
    try:
        with pytest.raises(ValueError,match='^OPEN_LAW_EGRESS_BLOCKED$'):claim(repo,context())
    finally:repo.close()


def test_same_law_hash_reuses_version(pg,monkeypatch):
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg)
    try:
        first=claim(repo,ctx);result=repo.complete(ctx,first,document(),*promoted(pg,ctx,first))
        second=claim(repo,ctx,'request-2');same=repo.complete(ctx,second,document(),*promoted(pg,ctx,second))
        assert same.source_version_id==result.source_version_id
        assert repo.claim(ctx,selection(),document().raw_sha256,document().text_sha256,'request-1').result==result
    finally:repo.close()


def test_tenant_notebook_and_connector_isolation(pg,monkeypatch):
    import psycopg
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg)
    try:
        owned=claim(repo,ctx);result=repo.complete(ctx,owned,document(),*promoted(pg,ctx,owned))
        assert repo.get_snapshot(ctx,result.source_version_id).text_sha256==document().text_sha256
        for other in (replace(ctx,actor_id='other'),replace(ctx,notebook_id='other'),replace(ctx,tenant_id='other'),replace(ctx,workspace_id='other')):
            with pytest.raises(ValueError):repo.get_snapshot(other,result.source_version_id)
        with psycopg.connect(pg) as conn:conn.execute("UPDATE workspace_connectors SET status='disconnected' WHERE workspace_id=%s",(ctx.workspace_id,))
        with pytest.raises(ValueError):repo.get_snapshot(ctx,result.source_version_id)
        with pytest.raises(ValueError):claim(repo,ctx)
    finally:repo.close()


def test_snapshot_immutable_and_legacy_unverified(pg,monkeypatch):
    import psycopg
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg)
    try:
        owned=claim(repo,ctx);result=repo.complete(ctx,owned,document(),*promoted(pg,ctx,owned))
        for sql in ('UPDATE open_law_source_snapshots SET title=title','DELETE FROM open_law_source_snapshots'):
            with psycopg.connect(pg) as conn:
                conn.execute('SET LOCAL ROLE daon_app')
                with pytest.raises(psycopg.Error):conn.execute(sql)
                conn.rollback()
        with psycopg.connect(pg) as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("UPDATE workspace_connectors SET verification_json='{}' WHERE workspace_id=%s",(ctx.workspace_id,))
            conn.rollback()
            conn.execute("UPDATE workspace_connectors SET status='unavailable',error_code='CONNECTOR_NOT_VERIFIED',verification_json='{}' WHERE workspace_id=%s",(ctx.workspace_id,))
        with pytest.raises(ValueError):repo.get_snapshot(ctx,result.source_version_id)
    finally:repo.close()


def test_key_conflict_and_concurrent_import(pg,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    ctx=seed(pg);repo=repository(pg)
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    try:
        def attempt():
            try:return claim(repo,ctx)
            except ValueError as error:return str(error)
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:attempt(),range(2)))
        assert results.count('OPEN_LAW_IMPORT_IN_PROGRESS')==1
        current=next(x for x in results if not isinstance(x,str));repo.fail(ctx,current,'OPEN_LAW_STORAGE_UNAVAILABLE')
        retry=claim(repo,ctx);assert retry.request_id!=current.request_id
    finally:repo.close()


def test_question_commit_holds_connector_lock_through_result_writes(pg, monkeypatch):
    import psycopg
    from contextlib import contextmanager
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
    from daon_user_api.question_answering import GroundedTextResult, TextModelSelection
    ctx = seed(pg)
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED', 'true')
    monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID', ctx.workspace_id)
    repo = repository(pg)
    store = PostgresCloudStore(pg)
    try:
        owned = claim(repo, ctx)
        result = repo.complete(ctx, owned, document(), *promoted(pg, ctx, owned))
        checked = []
        class Connection:
            def __init__(self, connection): self.connection = connection
            def execute(self, sql, params=()):
                if sql.startswith('INSERT INTO knowledge_scopes'):
                    with psycopg.connect(pg) as competing:
                        competing.execute("SET LOCAL lock_timeout='100ms'")
                        with pytest.raises(psycopg.errors.LockNotAvailable):
                            competing.execute("UPDATE workspace_connectors SET status='disconnected' WHERE tenant_id=%s AND workspace_id=%s AND connector_id='mcp-open-law-go-kr'",
                                (ctx.tenant_id, ctx.workspace_id))
                    checked.append(True)
                    raise RuntimeError('fixture rollback after lock proof')
                return self.connection.execute(sql, params)
        class Store:
            @contextmanager
            def _transaction(self, context):
                with store._transaction(context) as connection:
                    connection.execute('SET LOCAL ROLE daon_app')
                    yield Connection(connection)
        question = PostgresQuestionAnsweringRepository(Store(), None)
        with pytest.raises(RuntimeError, match='fixture rollback after lock proof'):
            question.persist_completed(ctx, run_id='run-s15-lock-'+uuid4().hex,
                source_id=result.source_id, source_version_id=result.source_version_id,
                question='원문은?', selection=TextModelSelection('OLLAMA','https://models.example.com',
                    'profile','deployment','fixture-model',1), evidence=(),
                result=GroundedTextResult('', (), False, {}))
        assert checked == [True]
        with psycopg.connect(pg) as connection:
            assert connection.execute('SELECT count(*) FROM scope_snapshots WHERE tenant_id=%s', (ctx.tenant_id,)).fetchone()[0] == 0
    finally:
        repo.close()
        store.close()


def test_general_fallback_commit_rejects_disconnected_selected_law(pg, monkeypatch):
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository, QuestionRepositoryError
    from daon_user_api.question_answering import GroundedTextResult, TextModelSelection
    ctx = seed(pg)
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED', 'true')
    monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID', ctx.workspace_id)
    repo = repository(pg)
    store = PostgresCloudStore(pg)
    try:
        owned = claim(repo, ctx)
        result = repo.complete(ctx, owned, document(), *promoted(pg, ctx, owned))
        with psycopg.connect(pg) as connection:
            connection.execute("UPDATE workspace_connectors SET status='disconnected' WHERE workspace_id=%s", (ctx.workspace_id,))
        question = PostgresQuestionAnsweringRepository(store, None)
        with pytest.raises(QuestionRepositoryError, match='QUESTION_SOURCE_UNAVAILABLE'):
            question.persist_completed(ctx, run_id='run-s15-general-'+uuid4().hex,
                source_id=None, source_version_id=None, question='자료 밖 질문',
                selection=TextModelSelection('OLLAMA','https://models.example.com','profile','deployment','fixture-model',1),
                evidence=(), result=GroundedTextResult('일반 응답', (), False, {}),
                context_mode='general_ungrounded', context_sources=(),
                access_source_version_ids=(result.source_version_id,))
        with psycopg.connect(pg) as connection:
            assert connection.execute('SELECT count(*) FROM runs WHERE tenant_id=%s', (ctx.tenant_id,)).fetchone()[0] == 0
    finally:
        repo.close()
        store.close()


@pytest.mark.parametrize('foreign_binding', [False, True])
def test_unregister_unbinds_only_actor_notebooks_without_deleting_originals(pg, monkeypatch, foreign_binding):
    import psycopg
    from contextlib import contextmanager
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.connector_postgres import PostgresConnectorRepository
    from daon_user_api.mcp_connector import ConnectorError
    ctx = seed(pg)
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true')
    monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID', ctx.workspace_id)
    repo = repository(pg)
    store = PostgresCloudStore(pg)
    try:
        owned = claim(repo,ctx)
        result = repo.complete(ctx,owned,document(),*promoted(pg,ctx,owned))
        if foreign_binding:
            with psycopg.connect(pg) as conn:
                conn.execute('INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())',
                    (ctx.tenant_id,ctx.workspace_id,'other-'+ctx.notebook_id,'other-actor'))
                conn.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,now())",
                    (ctx.tenant_id,ctx.workspace_id,'other-'+ctx.notebook_id,result.source_id,result.source_version_id,'other-actor'))
        class AppStore:
            @contextmanager
            def _transaction(self, context):
                with store._transaction(context) as connection:
                    connection.execute('SET LOCAL ROLE daon_app')
                    yield connection
        connectors = PostgresConnectorRepository(AppStore())
        if foreign_binding:
            with pytest.raises(ConnectorError, match='OPEN_LAW_BINDING_CONFLICT'):
                connectors.unregister(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'mcp-open-law-go-kr')
        else:
            connectors.unregister(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'mcp-open-law-go-kr')
        with psycopg.connect(pg) as conn:
            assert conn.execute('SELECT count(*) FROM notebook_source_unbindings WHERE tenant_id=%s', (ctx.tenant_id,)).fetchone()[0] == (0 if foreign_binding else 1)
            assert conn.execute('SELECT count(*) FROM workspace_connectors WHERE tenant_id=%s', (ctx.tenant_id,)).fetchone()[0] == (1 if foreign_binding else 0)
            for table in ('sources','source_versions','open_law_source_snapshots'):
                assert conn.execute('SELECT count(*) FROM '+table+' WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM object_records WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0] == 2
    finally:
        repo.close()
        store.close()


@pytest.mark.parametrize('phase', ['fetching', 'storing'])
def test_stale_killed_import_retries_with_history_and_old_writer_fencing(pg, monkeypatch, phase):
    import psycopg
    from concurrent.futures import ThreadPoolExecutor
    from daon_user_api.open_law_source_import import object_id
    ctx = seed(pg)
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true')
    monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo = repository(pg)
    try:
        template = claim(repo,ctx,'template')
        orphan = replace(template,request_id='law-request-'+uuid4().hex)
        raw_id, text_id = object_id(orphan,'raw'), object_id(orphan,'text')
        # Explicit killed-parent fixture, no real sleeps or production clock edits.
        with psycopg.connect(pg) as conn:
            conn.execute('INSERT INTO open_law_import_requests(tenant_id,workspace_id,actor_id,notebook_id,idempotency_key,request_id,fingerprint,'
                'mst,effective_date,article_code,expected_raw_sha256,expected_text_sha256,state,raw_object_id,text_object_id,policy_version,policy_fingerprint,created_at,updated_at) '
                "SELECT tenant_id,workspace_id,actor_id,notebook_id,'orphan',%s,fingerprint,mst,effective_date,article_code,expected_raw_sha256,expected_text_sha256,%s,%s,%s,policy_version,policy_fingerprint,now()-interval '46 seconds',now()-interval '46 seconds' "
                'FROM open_law_import_requests WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s',
                (orphan.request_id,phase,raw_id,text_id,ctx.tenant_id,ctx.workspace_id,template.request_id))
        old_objects = promoted(pg,ctx,orphan)
        def retry():
            try: return claim(repo,ctx,'orphan')
            except ValueError as error: return str(error)
        with ThreadPoolExecutor(2) as pool:
            attempts = list(pool.map(lambda _:retry(),range(2)))
        recovered = [item for item in attempts if not isinstance(item,str)]
        assert len(recovered) == 1, attempts
        assert attempts.count('OPEN_LAW_IMPORT_IN_PROGRESS') == 1
        current = recovered[0]
        assert current.request_id != orphan.request_id
        repo.fail(ctx,orphan,'OPEN_LAW_STORAGE_UNAVAILABLE')
        with pytest.raises(ValueError,match='OPEN_LAW_IMPORT_IN_PROGRESS'):
            repo.complete(ctx,orphan,document(),*old_objects)
        with psycopg.connect(pg) as conn:
            row = conn.execute('SELECT request_id,state,attempt,history FROM open_law_import_requests WHERE tenant_id=%s AND workspace_id=%s AND idempotency_key=%s',
                (ctx.tenant_id,ctx.workspace_id,'orphan')).fetchone()
            assert row[:3] == (current.request_id,'fetching',2)
            assert row[3] == [{'request_id':orphan.request_id,'raw_object_id':raw_id,'text_object_id':text_id,'attempt':1,'safe_error_code':'OPEN_LAW_TIMEOUT'}]
        result = repo.complete(ctx,current,document(),*promoted(pg,ctx,current))
        assert result.source_state == 'ready'
    finally: repo.close()


def unregister_and_restore_verified_fixture(dsn,ctx):
    """격리 fixture의 연결 영수증만 복원. 실제 MCP probe 성공 증거 아님."""
    import psycopg
    from psycopg.types.json import Jsonb
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.connector_postgres import PostgresConnectorRepository
    from daon_user_api.open_law_mcp_server import PROTOCOL,SCHEMA_DIGEST
    store=PostgresCloudStore(dsn)
    try:PostgresConnectorRepository(store).unregister(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'mcp-open-law-go-kr')
    finally:store.close()
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO workspace_connectors(tenant_id,workspace_id,connector_id,kind,name,endpoint_label,status,source_count,sources_json,verification_json,created_at,updated_at) "
            "VALUES(%s,%s,'mcp-open-law-go-kr','mcp','국가법령정보센터','open.law.go.kr','connected',0,'[]',%s,now(),now())",
            (ctx.tenant_id,ctx.workspace_id,Jsonb({'protocol_version':PROTOCOL,'tool_schema_digest':SCHEMA_DIGEST,'checked_at':'2026-10-16T01:00:00Z'})))


def test_explicit_reimport_after_unregister_creates_new_source_version(pg,monkeypatch):
    import psycopg
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg)
    try:
        first=claim(repo,ctx);old=repo.complete(ctx,first,document(),*promoted(pg,ctx,first))
        unregister_and_restore_verified_fixture(pg,ctx)
        with pytest.raises(ValueError):claim(repo,ctx)
        new_claim=claim(repo,ctx,'explicit-new-key')
        new=repo.complete(ctx,new_claim,document(),*promoted(pg,ctx,new_claim))
        assert new.source_id!=old.source_id and new.source_version_id!=old.source_version_id and new.snapshot_id!=old.snapshot_id
        with pytest.raises(ValueError):repo.get_snapshot(ctx,old.source_version_id)
        again=claim(repo,ctx,'another-key')
        assert repo.complete(ctx,again,document(),*promoted(pg,ctx,again)).source_version_id==new.source_version_id
        with psycopg.connect(pg) as conn:
            assert conn.execute('SELECT count(*) FROM open_law_source_snapshots WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0]==2
            assert conn.execute('SELECT count(*) FROM notebook_source_unbindings WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0]==1
            assert conn.execute("SELECT request_id,state,snapshot_id,source_id,source_version_id FROM open_law_import_requests WHERE tenant_id=%s AND idempotency_key='request-1'",
                (ctx.tenant_id,)).fetchone()==(first.request_id,'completed',old.snapshot_id,old.source_id,old.source_version_id)
            assert conn.execute('SELECT raw_sha256,text_sha256 FROM open_law_source_snapshots WHERE tenant_id=%s AND snapshot_id=%s',
                (ctx.tenant_id,old.snapshot_id)).fetchone()==(document().raw_sha256,document().text_sha256)
    finally:repo.close()


def test_reimport_preserves_history_and_deduplicates_current_binding(pg,monkeypatch):
    import psycopg
    from concurrent.futures import ThreadPoolExecutor
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg)
    try:
        first=claim(repo,ctx);old=repo.complete(ctx,first,document(),*promoted(pg,ctx,first))
        unregister_and_restore_verified_fixture(pg,ctx)
        claims=[claim(repo,ctx,key) for key in ('new-one','new-two')]
        objects=[promoted(pg,ctx,item) for item in claims]
        def complete(i):return repo.complete(ctx,claims[i],document(),*objects[i])
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(complete,range(2)))
        assert results[0].source_version_id==results[1].source_version_id!=old.source_version_id
        with psycopg.connect(pg) as conn:
            assert conn.execute("SELECT count(*) FROM notebook_bindings b WHERE tenant_id=%s AND binding_kind='source' AND NOT EXISTS "
                '(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id AND u.workspace_id=b.workspace_id AND u.notebook_id=b.notebook_id AND u.source_id=b.record_id AND u.source_version_id=b.version_id)',
                (ctx.tenant_id,)).fetchone()[0]==1
            assert conn.execute('SELECT count(*) FROM open_law_source_snapshots WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0]==2
    finally:repo.close()


def test_database_rejects_active_identity_duplicate_without_application_dedup(pg,monkeypatch):
    import psycopg
    from contextlib import contextmanager
    from types import SimpleNamespace
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg)
    try:
        first=claim(repo,ctx);repo.complete(ctx,first,document(),*promoted(pg,ctx,first))
        second=claim(repo,ctx,'bypassed-app-dedup');objects=promoted(pg,ctx,second)
        transaction=repo._transaction
        class Connection:
            def __init__(self,connection):self.connection=connection
            def execute(self,sql,params=()):
                if sql.startswith('SELECT r.snapshot_id,r.source_id,r.source_version_id FROM open_law_import_requests'):
                    return SimpleNamespace(fetchone=lambda:None)
                if sql.startswith('SELECT snapshot_id,source_id,source_version_id FROM open_law_import_requests'):
                    return SimpleNamespace(fetchone=lambda:None)
                return self.connection.execute(sql,params)
        @contextmanager
        def bypass(*args,**kwargs):
            with transaction(*args,**kwargs) as conn:yield Connection(conn)
        monkeypatch.setattr(repo,'_transaction',bypass)
        with pytest.raises(ValueError,match='OPEN_LAW_STORAGE_UNAVAILABLE'):
            repo.complete(ctx,second,document(),*objects)
        with psycopg.connect(pg) as conn:
            for table in ('sources','source_versions','open_law_source_snapshots'):
                assert conn.execute('SELECT count(*) FROM '+table+' WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0]==1
    finally:repo.close()


def test_actual_studio_worker_blocks_law_revoked_after_enqueue(pg,monkeypatch):
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.studio_workspace import StudioContext,StudioGenerationRequest
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_generation_queue import PostgresStudioGenerationQueue
    from daon_user_api.studio_generation_worker import StudioGenerationWorker
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg);store=PostgresCloudStore(pg);queue=PostgresStudioGenerationQueue(pg,store)
    studio=PostgresStudioWorkspaceRepository(store)
    try:
        owned=claim(repo,ctx);source=repo.complete(ctx,owned,document(),*promoted(pg,ctx,owned))
        studio_context=StudioContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
        request=StudioGenerationRequest('evidence_report',source.source_id,(source.source_version_id,),None,None,
            'S15 QA','QA',None,'short','summary','pdf','review_required',source_only=True)
        queued,_=studio.enqueue_generation(studio_context,request,'law-queued')
        with psycopg.connect(pg) as conn:
            conn.execute("UPDATE workspace_connectors SET status='disconnected' WHERE tenant_id=%s AND workspace_id=%s",(ctx.tenant_id,ctx.workspace_id))
        run_target_queued_job(pg,queue,studio,queued['job_id'])
        job=queue.get(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'studio.read'),queued['job_id'])
        assert (job.state,job.safe_error_code,job.studio_output_id)==('failed','RESOURCE_UNAVAILABLE',None)
        with psycopg.connect(pg) as conn:
            for table in ('studio_outputs','output_versions','generation_requests'):
                assert conn.execute('SELECT count(*) FROM '+table+' WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0]==0
    finally:
        repo.close();queue.close();store.close()
        studio._generation_queue.close()


def test_index_failure_rolls_back_all_new_source_relations_and_tracks_objects(pg,monkeypatch):
    import psycopg
    from daon_user_api.open_law_source_import import OpenLawImportError
    ctx=seed(pg);monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg)
    try:
        owned=claim(repo,ctx);objects=promoted(pg,ctx,owned);insert=repo._insert
        def fail_index(conn,ctx,table,*args):
            if table=='index_versions':raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE')
            return insert(conn,ctx,table,*args)
        monkeypatch.setattr(repo,'_insert',fail_index)
        with pytest.raises(ValueError,match='OPEN_LAW_STORAGE_UNAVAILABLE'):
            repo.complete(ctx,owned,document(),*objects)
        repo.fail(ctx,owned,'OPEN_LAW_STORAGE_UNAVAILABLE')
        with psycopg.connect(pg) as conn:
            for table in ('sources','source_versions','notebook_bindings','evidence_spans','index_versions','open_law_source_snapshots'):
                assert conn.execute('SELECT count(*) FROM '+table+' WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0]==0
            assert conn.execute('SELECT state,raw_object_id,text_object_id FROM open_law_import_requests WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()==('failed',*objects)
            assert conn.execute('SELECT count(*) FROM object_records WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0]==2
    finally:repo.close()


def test_current_auth_workspace_admin_allows_law_without_legacy_role_authority(pg,monkeypatch):
    ctx=seed(pg,auth_role='workspace_admin',required_approver='workspace_manager')
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg)
    try:
        owned=claim(repo,ctx);result=repo.complete(ctx,owned,document(),*promoted(pg,ctx,owned))
        assert repo.get_snapshot(ctx,result.source_version_id).snapshot_id==result.snapshot_id
    finally:repo.close()


@pytest.mark.parametrize('revocation',['inactive_auth','viewer_auth','source_acl','disabled_identity'])
def test_auth_revocation_with_legacy_role_remaining_blocks_real_queued_consumer(pg,monkeypatch,revocation):
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.studio_workspace import StudioContext,StudioGenerationRequest,StudioError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_generation_queue import PostgresStudioGenerationQueue
    from daon_user_api.studio_generation_worker import StudioGenerationWorker
    ctx=seed(pg,auth_role='workspace_admin',required_approver='workspace_manager')
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg);store=PostgresCloudStore(pg);queue=PostgresStudioGenerationQueue(pg,store)
    studio=PostgresStudioWorkspaceRepository(store)
    try:
        owned=claim(repo,ctx);source=repo.complete(ctx,owned,document(),*promoted(pg,ctx,owned))
        current=StudioContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
        request=StudioGenerationRequest('evidence_report',source.source_id,(source.source_version_id,),None,None,
            'S15 QA','QA',None,'short','summary','pdf','review_required',source_only=True)
        queued,_=studio.enqueue_generation(current,request,'law-auth-queued')
        with psycopg.connect(pg) as conn:
            if revocation=='inactive_auth':
                conn.execute("UPDATE identity_auth_memberships SET state='inactive',version=version+1 WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s",(ctx.tenant_id,ctx.workspace_id,ctx.actor_id))
            elif revocation=='viewer_auth':
                conn.execute("UPDATE identity_auth_memberships SET role='viewer',version=version+1 WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s",(ctx.tenant_id,ctx.workspace_id,ctx.actor_id))
            elif revocation=='disabled_identity':
                conn.execute("UPDATE identity_users SET state='disabled' WHERE user_id=%s",(ctx.actor_id,))
            else:
                conn.execute('INSERT INTO identity_auth_source_access(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) VALUES(%s,%s,%s,%s,0,1,now())',
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,source.source_version_id))
            assert conn.execute('SELECT role FROM memberships WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s',
                (ctx.tenant_id,ctx.workspace_id,ctx.actor_id)).fetchone()[0]=='workspace_manager'
        with pytest.raises(StudioError,match='RESOURCE_UNAVAILABLE'):
            studio.require_source_access(current,(source.source_version_id,))
        run_target_queued_job(pg,queue,studio,queued['job_id'])
        job=queue.get(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'studio.read'),queued['job_id'])
        assert (job.state,job.safe_error_code,job.studio_output_id)==('failed','RESOURCE_UNAVAILABLE',None)
        with psycopg.connect(pg) as conn:
            for table in ('studio_outputs','output_versions','generation_requests'):
                assert conn.execute('SELECT count(*) FROM '+table+' WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0]==0
    finally:
        repo.close();queue.close();store.close();studio._generation_queue.close()


def test_last_connector_unregister_then_default_list_and_repository_restart_never_resurrects_law(pg):
    import psycopg
    from contextlib import contextmanager
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.connector_postgres import PostgresConnectorRepository
    from daon_user_api.mcp_connector import ConnectorView,ConnectorError
    ctx=seed(pg)
    law=ConnectorView('mcp-open-law-go-kr','mcp','국가법령정보센터','unavailable',0,
        'open.law.go.kr',None,'CONNECTOR_NOT_VERIFIED')
    class AppStore(PostgresCloudStore):
        @contextmanager
        def _transaction(self,context):
            with super()._transaction(context) as conn:
                conn.execute('SET LOCAL ROLE daon_app')
                yield conn
    store=AppStore(pg)
    try:
        connectors=PostgresConnectorRepository(store)
        connectors.unregister(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,law.connector_id)
        with psycopg.connect(pg) as conn:
            assert conn.execute('SELECT count(*) FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s',
                (ctx.tenant_id,ctx.workspace_id)).fetchone()[0]==0
        assert connectors.list(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,(law,))==()
    finally:store.close()
    restarted=AppStore(pg)
    try:
        connectors=PostgresConnectorRepository(restarted)
        assert connectors.list(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,(law,))==()
        with pytest.raises(ConnectorError,match='^CONNECTOR_NOT_FOUND$'):
            connectors.get(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,law.connector_id)
        connectors.register(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,law)
        assert connectors.get(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,law.connector_id)==law
        assert connectors.list(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,(law,))==(law,)
    finally:restarted.close()


def test_settings_change_rechecks_new_law_selection_before_any_new_version_write(pg,monkeypatch):
    import psycopg
    from contextlib import contextmanager
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace import StudioContext,StudioError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    ctx=seed(pg)
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true')
    monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo=repository(pg)
    class AppStore(PostgresCloudStore):
        @contextmanager
        def _transaction(self,context):
            with super()._transaction(context) as conn:
                conn.execute('SET LOCAL ROLE daon_app')
                yield conn
    store=AppStore(pg);studio=PostgresStudioWorkspaceRepository(store)
    current=StudioContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version,ctx.notebook_id)
    suffix=uuid4().hex
    a,va,span='source-A-'+suffix,'version-A-'+suffix,'span-A-'+suffix
    run,result='run-AB-'+suffix,'result-AB-'+suffix
    settings,generation,output,version=('settings-A-'+suffix,'generation-A-'+suffix,'output-A-'+suffix,'output-version-A-'+suffix)
    tables=('output_versions','generation_requests','generation_settings_snapshots','evidence_references')
    try:
        owned=claim(repo,ctx);law=repo.complete(ctx,owned,document(),*promoted(pg,ctx,owned))
        # 저장소의 합성 이전 Run/산출물 fixture. 모델 실행 증거가 아니다.
        with repo._transaction(ctx) as conn:
            def put(table,ident,payload,*,state=None,extra=()):
                studio._insert(conn,current,table,ident,payload,state=state,
                    extra_columns=tuple(key for key,_ in extra),extra_values=tuple(value for _,value in extra))
            def advance(entity,ident,targets):
                version_number=1
                for target in targets:
                    version_number=studio._transition(conn,current,entity,ident,version_number,target,
                        's15-fixture-'+suffix+'-'+entity+'-'+target)
            put('sources',a,{'kind':'upload'},state='registered')
            advance('Source',a,('security_check','processing','indexing','ready'))
            put('source_versions',va,{'filename':'ordinary-A.txt'},extra=(('source_id',a),))
            put('evidence_spans',span,{'text':'일반 A 원문','page':1},extra=(('source_version_id',va),))
            put('runs',run,{'source_version_ids':[va,law.source_version_id]},state='accepted')
            advance('Run',run,('planning','retrieving','generating','validating','completed'))
            put('run_results',result,{'answer':'저장된 A와 B 답변','insufficient':False},extra=(('run_id',run),))
            law_span=conn.execute('SELECT record_id FROM evidence_spans WHERE tenant_id=%s AND workspace_id=%s AND source_version_id=%s',
                (ctx.tenant_id,ctx.workspace_id,law.source_version_id)).fetchone()[0]
            for label,sv,evidence in (('A',va,span),('B',law.source_version_id,law_span)):
                put('citations','citation-'+label+'-'+suffix,{'page':1,'origin':'raw_source'},
                    extra=(('run_result_id',result),('source_version_id',sv),('evidence_span_id',evidence)))
            payload={'purpose':'S15 settings QA','audience':'QA','output_type':'evidence_report','source_id':a,
                'source_version_ids':[va],'run_id':run,'run_result_id':result,'ruleset_version_id':None,
                'length':'short','structure':'summary','output_format':'pdf','review_condition':'review_required','content':'A 기존 산출물'}
            put('generation_settings_snapshots',settings,payload)
            put('generation_requests',generation,payload,state='configuring',extra=(('generation_settings_snapshot_id',settings),))
            advance('GenerationRequest',generation,('confirmed','submitted'))
            put('studio_outputs',output,{'output_type':'evidence_report','title':'S15 settings QA'},extra=(('generation_request_id',generation),))
            put('output_versions',version,payload,state='generating',extra=(('studio_output_id',output),('generation_settings_snapshot_id',settings)))
            advance('OutputVersion',version,('draft',))
            put('evidence_references','reference-A-'+suffix,{'origin':'raw_source','locator':{'kind':'page','value':'1'}},
                extra=(('output_version_id',version),('source_version_id',va),('evidence_span_id',span)))
            for kind,record,sv in (('source',a,va),('studio_output',output,None),('output_version',version,None)):
                conn.execute('INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now())',
                    (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,kind,record,sv,ctx.actor_id))
        with psycopg.connect(pg) as conn:
            before=[conn.execute('SELECT count(*) FROM '+table+' WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0] for table in tables]
            conn.execute("UPDATE workspace_connectors SET status='disconnected' WHERE tenant_id=%s AND workspace_id=%s",(ctx.tenant_id,ctx.workspace_id))
        # A-only 이전 lineage는 여전히 유효. 새 선택 B 때문에 거부되어야 한다.
        studio.require_source_access(current,(va,))
        revision={'previous_version_id':version,'revision_type':'settings_change','change_reason':'B 추가','content':'',
            'settings':{key:payload[key] for key in ('purpose','audience','ruleset_version_id','length','structure','output_format','review_condition')}}
        revision['settings']['source_version_ids']=[va,law.source_version_id]
        with pytest.raises(StudioError,match='^RESOURCE_UNAVAILABLE$'):
            studio.create_version(current,output,revision,'add-revoked-B')
        with psycopg.connect(pg) as conn:
            assert [conn.execute('SELECT count(*) FROM '+table+' WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0] for table in tables]==before
            assert conn.execute('SELECT state FROM runs WHERE tenant_id=%s AND record_id=%s',(ctx.tenant_id,run)).fetchone()[0]=='completed'
            assert conn.execute('SELECT canonical_json FROM output_versions WHERE tenant_id=%s AND record_id=%s',(ctx.tenant_id,version)).fetchone()[0]==payload
    finally:
        repo.close();store.close();studio._generation_queue.close()
