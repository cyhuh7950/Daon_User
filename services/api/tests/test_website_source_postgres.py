from __future__ import annotations

import importlib
import importlib.util
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from daon_user_api.data_canon import CanonicalContext, PostgresDataCanonStore
from test_website_source_import import page, context


def api():
    assert importlib.util.find_spec('daon_user_api.website_source_postgres') is not None
    return importlib.import_module('daon_user_api.website_source_postgres')


def test_transaction_participation_preserves_canonical_license_enforcer():
    assert hasattr(PostgresDataCanonStore, 'create_source_in_transaction')
    assert hasattr(PostgresDataCanonStore, 'append_source_version_in_transaction')
    written, enforced = [], []
    class Connection:
        def execute(self, sql, params=()):
            if sql.startswith('SELECT 1 FROM notebooks'):
                return SimpleNamespace(fetchone=lambda: (1,))
            if sql.startswith('SELECT'):
                return SimpleNamespace(fetchone=lambda: None)
            written.append(sql.split()[2])
            return SimpleNamespace(fetchone=lambda: None)
    store = object.__new__(PostgresDataCanonStore)
    store._creation_enforcer = lambda conn,tenant,action,counts: enforced.append((tenant, action, counts))
    @contextmanager
    def forbidden(ctx):
        raise AssertionError('별도 transaction 금지')
        yield
    store._transaction = forbidden
    from daon_user_api.data_canon import canonical_json_bytes
    import hashlib
    conn=Connection(); ctx=CanonicalContext('t','w','a','source.write','trace')
    payload={'object_id':'o','text_sha256':'a'*64}
    store.create_source_in_transaction(conn,ctx,'s')
    store.append_source_version_in_transaction(conn,ctx,source_id='s',source_version_id='v',
        version_number=1,previous_version_id=None,canonical_payload=payload,
        digest_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        created_at=datetime.now(timezone.utc))
    assert 'sources' in written and 'source_versions' in written
    assert enforced == [('t','source.create',{'source_versions':1})]


def test_website_text_index_uses_existing_section_consumer_without_fake_model():
    m = api()
    chunks = m.website_text_chunks('source', 'version', '가😀나\n57000 KRW')
    assert ''.join(c['text'] for c in chunks) == '가😀나\n57000 KRW'
    assert all(c['evidence_kind'] == 'text_source_verbatim_fact' and c['page'] == 1 for c in chunks)
    assert all('model_id' not in c and c['source_version_id'] == 'version' for c in chunks)


@pytest.fixture
def pg():
    dsn = os.environ.get('DAON_S13_ISOLATED_POSTGRES_DSN')
    if not dsn:
        pytest.skip('NOT RUN: DAON_S13_ISOLATED_POSTGRES_DSN 미설정')
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(dsn).get('dbname') == 'daon_user_s13_qa_20261015', '격리 DB만 허용'
    with psycopg.connect(dsn) as connection:
        assert connection.execute('SELECT current_database()').fetchone()[0] == 'daon_user_s13_qa_20261015'
        revision = connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]
        assert revision in ('0060', '0061')
        if revision == '0060':
            path = __import__('pathlib').Path(__file__).resolve().parents[1] / 'migrations/versions/0061_website_source_snapshots.py'
            spec = importlib.util.spec_from_file_location('s13_migration', path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            from unittest.mock import patch
            with patch.object(migration.op, 'execute', side_effect=lambda sql: connection.execute(sql)):
                migration.upgrade()
            connection.execute("UPDATE alembic_version SET version_num='0061'")
    return dsn


def seed(dsn):
    import psycopg
    from uuid import uuid4
    ctx = context()
    suffix = uuid4().hex[:8]
    ctx = __import__('dataclasses').replace(ctx, tenant_id='s13-t-'+suffix,workspace_id='s13-w-'+suffix,
        actor_id='s13-a-'+suffix,notebook_id='s13-n-'+suffix)
    with psycopg.connect(dsn) as conn:
        conn.execute('INSERT INTO tenants(tenant_id,display_name) VALUES(%s,%s)', (ctx.tenant_id,'S13 QA'))
        conn.execute('INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,%s)', (ctx.tenant_id,ctx.workspace_id,'S13 QA'))
        conn.execute("INSERT INTO user_accounts(tenant_id,user_id,status) VALUES(%s,%s,'active')", (ctx.tenant_id,ctx.actor_id))
        conn.execute("INSERT INTO memberships(tenant_id,workspace_id,user_id,role) VALUES(%s,%s,%s,'personal_owner')", (ctx.tenant_id,ctx.workspace_id,ctx.actor_id))
        conn.execute('INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())', (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,ctx.actor_id))
    return ctx


def promoted_records(dsn, ctx, claim, fetched):
    import psycopg
    from daon_user_api.website_source_import import object_id
    ids=[]
    with psycopg.connect(dsn) as conn:
        for kind, content, digest in [('raw',fetched.raw_content,fetched.raw_sha256),('text',fetched.text_content,fetched.text_sha256)]:
            oid=object_id(claim,kind); ids.append(oid)
            conn.execute("INSERT INTO object_records(tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,byte_size,content_type,status,created_by,trace_id,idempotency_key,request_fingerprint,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,%s,%s,'completed',%s,%s,%s,%s,now())",
                (ctx.tenant_id,ctx.workspace_id,oid,'staging/'+oid,'source/'+oid,digest,len(content),'application/octet-stream' if kind=='raw' else 'text/plain',ctx.actor_id,ctx.trace_id,oid,digest))
    return ids


def test_website_snapshot_rls_and_immutability(pg):
    from dataclasses import replace
    import psycopg
    ctx=seed(pg); repo=api().PostgresWebsiteSourceRepository(pg, policy_fingerprint=lambda ctx:'test-policy-fingerprint-v1')
    claim=repo.claim(ctx,'https://example.com/','key'); fetched=page()
    result=repo.complete(ctx,claim,fetched,*promoted_records(pg,ctx,claim,fetched))
    assert repo.get_snapshot(ctx,result.source_version_id).text_sha256 == fetched.text_sha256
    for other in (replace(ctx,actor_id='other'),replace(ctx,notebook_id='other'),replace(ctx,workspace_id='other')):
        with pytest.raises(Exception, match='WEBSITE_ACCESS_DENIED'):
            repo.get_snapshot(other,result.source_version_id)
    for verb in ('UPDATE website_source_snapshots SET title=title','DELETE FROM website_source_snapshots'):
        with psycopg.connect(pg) as conn:
            conn.execute('SET LOCAL ROLE daon_app')
            with pytest.raises(psycopg.Error):
                conn.execute(verb)
            conn.rollback()
    repo.close()


def test_source_scope_and_atomic_commit(pg):
    from dataclasses import replace
    import psycopg
    ctx=seed(pg); repo=api().PostgresWebsiteSourceRepository(pg, policy_fingerprint=lambda ctx:'test-policy-fingerprint-v1')
    claim=repo.claim(ctx,'https://example.com/','key'); fetched=page()
    raw,text=promoted_records(pg,ctx,claim,fetched)
    with psycopg.connect(pg) as conn:
        conn.execute("ALTER TABLE website_source_snapshots ADD CONSTRAINT s13_snapshot_test_fault CHECK(title<>'S13_FAIL_AT_SNAPSHOT') NOT VALID")
    try:
        with pytest.raises(Exception, match='WEBSITE_STORAGE_UNAVAILABLE'):
            repo.complete(ctx,claim,replace(fetched,title='S13_FAIL_AT_SNAPSHOT'),raw,text)
    finally:
        with psycopg.connect(pg) as conn:
            conn.execute('ALTER TABLE website_source_snapshots DROP CONSTRAINT s13_snapshot_test_fault')
    with psycopg.connect(pg) as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM notebook_bindings WHERE tenant_id=%s',(ctx.tenant_id,)).fetchone()[0] == 0
    result=repo.complete(ctx,claim,fetched,raw,text)
    with psycopg.connect(pg) as conn:
        assert conn.execute('SELECT count(*) FROM index_versions WHERE source_version_id=%s',(result.source_version_id,)).fetchone()[0] == 1
    repo.close()


def test_concurrent_import_and_recovery(pg):
    from concurrent.futures import ThreadPoolExecutor
    from daon_user_api.website_source_import import WebsiteImportError
    ctx=seed(pg); repo=api().PostgresWebsiteSourceRepository(pg, policy_fingerprint=lambda ctx:'test-policy-fingerprint-v1')
    def claim():
        try:
            return repo.claim(ctx,'https://example.com/','race')
        except WebsiteImportError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        values=list(pool.map(lambda _: claim(),range(2)))
    assert values.count('WEBSITE_IMPORT_IN_PROGRESS') == 1
    old=next(x for x in values if not isinstance(x,str))
    repo.fail(ctx,old,'WEBSITE_STORAGE_UNAVAILABLE')
    new=repo.claim(ctx,'https://example.com/','race')
    assert old.request_id != new.request_id
    with pytest.raises(WebsiteImportError):
        repo.complete(ctx,old,page(),'0'*32,'1'*32)
    result=repo.complete(ctx,new,page(),*promoted_records(pg,ctx,new,page()))
    assert repo.claim(ctx,'https://example.com/','race').result == result
    import psycopg
    with psycopg.connect(pg) as conn:
        conn.execute('SET LOCAL ROLE daon_app')
        for name,value in [('tenant_id',ctx.tenant_id),('workspace_id',ctx.workspace_id),
            ('actor_id',ctx.actor_id),('notebook_id',ctx.notebook_id)]:
            conn.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE website_import_requests SET state='failed',snapshot_id=NULL WHERE request_id=%s",(new.request_id,))
        conn.rollback()
    repo.close()


def test_missing_policy_fingerprint_blocks_claim_before_database():
    repo=api().PostgresWebsiteSourceRepository('postgresql://unused/unused')
    @contextmanager
    def forbidden(ctx):
        raise AssertionError('정책 지문 없는 DB claim 금지')
        yield
    repo._transaction=forbidden
    try:
        with pytest.raises(Exception,match='WEBSITE_FETCH_BLOCKED'):
            repo.claim(context(),'https://example.com/','key')
    finally:
        repo.close()


def test_complete_sql_boundary_rolls_back_index_and_notebook_on_snapshot_failure():
    from daon_user_api.website_source_import import WebsiteImportClaim, WebsiteImportError, object_id
    m=api(); ctx=context(); fetched=page()
    claim=WebsiteImportClaim('website-request-'+'1'*32,'a'*64,False,None)
    class Connection:
        def __init__(self):
            self.writes=[]; self.version=1; self.fail_snapshot=False
        def execute(self,sql,params=()):
            if sql.startswith('SELECT 1 FROM notebooks'):
                row=(1,)
            elif sql.startswith('SELECT record_id') or sql.startswith('SELECT source_id, object_id'):
                row=None
            elif sql.startswith('SELECT n.notebook_id'):
                row=(ctx.notebook_id,)
            elif sql.startswith('SELECT fingerprint'):
                row=(claim.fingerprint,'fetching',fetched.requested_url,ctx.policy_version,'pfp')
            elif sql.startswith('SELECT status'):
                raw=params[-1]==object_id(claim,'raw')
                row=('completed',fetched.raw_sha256 if raw else fetched.text_sha256,
                    len(fetched.raw_content if raw else fetched.text_content),ctx.actor_id)
            elif sql.startswith('SELECT state,version'):
                self.version+=1; row=(params[3],self.version,'succeeded',None)
            else:
                if self.fail_snapshot and sql.startswith('INSERT INTO website_source_snapshots'):
                    raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
                self.writes.append((sql,params)); row=None
            return SimpleNamespace(fetchone=lambda:row)
    conn=Connection()
    canon=object.__new__(PostgresDataCanonStore)
    canon._creation_enforcer=None
    @contextmanager
    def forbidden(ctx):
        raise AssertionError('완료 transaction 밖 canon write 금지')
        yield
    canon._transaction=forbidden
    repo=m.PostgresWebsiteSourceRepository('unused',canon_store=canon,policy_fingerprint=lambda ctx:'pfp')
    @contextmanager
    def transaction(ctx):
        prior=list(conn.writes)
        try:
            yield conn
        except Exception:
            conn.writes=prior
            raise
    repo._transaction=transaction
    conn.fail_snapshot=True
    with pytest.raises(WebsiteImportError,match='WEBSITE_STORAGE_UNAVAILABLE'):
        repo.complete(ctx,claim,fetched,object_id(claim,'raw'),object_id(claim,'text'))
    assert conn.writes == []
    conn.fail_snapshot=False; conn.version=1
    result=repo.complete(ctx,claim,fetched,object_id(claim,'raw'),object_id(claim,'text'))
    assert result.source_state=='ready'
    assert sum(sql.startswith('INSERT INTO index_versions') for sql,_ in conn.writes)==1
    assert sum(sql.startswith('INSERT INTO website_source_snapshots') for sql,_ in conn.writes)==1
    import json
    version_params=next(params for sql,params in conn.writes if sql.startswith('INSERT INTO source_versions'))
    payload=next(json.loads(value) for value in version_params if isinstance(value,str) and value.startswith('{'))
    assert 'content_digest_sha256' not in payload, '웹 Version을 일반 TXT digest dedup 후보로 만들지 않는다'
    assert payload['raw_sha256']==fetched.raw_sha256 and payload['text_sha256']==fetched.text_sha256
    assert payload['object_id']==object_id(claim,'text')
    assert sum('INSERT INTO notebook_bindings' in sql for sql,_ in conn.writes)==1


def test_wrong_requested_url_in_persistence_is_rejected_before_storage():
    from daon_user_api.website_source_import import WebsiteImportClaim, WebsiteImportError
    repo=api().PostgresWebsiteSourceRepository('unused',policy_fingerprint=lambda ctx:'pfp')
    claim=WebsiteImportClaim('website-request-'+'1'*32,'a'*64,False,None)
    @contextmanager
    def forbidden(ctx):
        raise AssertionError('잘못된 object identity DB 접근 금지')
        yield
    repo._transaction=forbidden
    try:
        with pytest.raises(WebsiteImportError,match='WEBSITE_STORAGE_UNAVAILABLE'):
            repo.complete(context(),claim,page(),'0'*32,'1'*32)
    finally:
        repo.close()


def scoped_queue(dsn, *, enforcer=None):
    """격리 PG 시험만 app 역할로 실행. 제품 공유 인스턴스 변경이 아니다."""
    from daon_user_api.object_queue import PostgresObjectQueueStore
    class Queue(PostgresObjectQueueStore):
        @contextmanager
        def _transaction(self,scope):
            with super()._transaction(scope) as conn:
                conn.execute('SET LOCAL ROLE daon_app')
                yield conn
    return Queue(dsn,creation_enforcer=enforcer)


@pytest.mark.parametrize('checkpoint', ['registered','copy'])
def test_web_job_registration_and_copy_never_visible_to_generic_consumer(pg,checkpoint):
    """실제 PG/RLS 경쟁 + memory Storage. 실제 MinIO 성공과 구분한다."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.website_source_import import WebsiteObjectPromoter, WebsiteProcessStorage, object_id
    from test_object_queue import FakeObjectStorage
    ctx=seed(pg); repo=api().PostgresWebsiteSourceRepository(pg,policy_fingerprint=lambda ctx:'pfp')
    claim=repo.claim(ctx,'https://example.com/','race'); scope=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'object.write')
    paused=threading.Event(); release=threading.Event(); enforcement=[]
    def enforcer(conn,tenant,action,counts):
        actual=conn.execute("SELECT current_setting('app.tenant_id'),current_setting('app.workspace_id'),current_user").fetchone()
        enforcement.append((tenant,action,counts,actual))
    queue=scoped_queue(pg,enforcer=enforcer); ordinary=scoped_queue(pg)
    # 요청별 시험 subclass는 원 register 전체를 사용하고 반환 경계에서만 멈춘다.
    parent_type=type(queue)
    class PausedQueue(parent_type):
        def register_object_job(self,*args,**kwargs):
            submission=super().register_object_job(*args,**kwargs)
            if checkpoint=='registered':
                paused.set()
                assert release.wait(8)
            return submission
    queue.__class__=PausedQueue
    class Storage(FakeObjectStorage,WebsiteProcessStorage):
        def promote(self,*args,**kwargs):
            if checkpoint=='copy':
                paused.set()
                assert release.wait(8)
            return super().promote(*args,**kwargs)
    promoter=WebsiteObjectPromoter(queue,Storage())
    try:
        with ThreadPoolExecutor(1) as pool:
            task=pool.submit(promoter.promote,ctx,claim,'raw',page().raw_content,time.monotonic()+12)
            try:
                assert paused.wait(8)
                # COPY 중에는 기존 30s lease 만료 이후의 회수 조건도 시험한다.
                now=datetime.now(timezone.utc)+timedelta(seconds=31 if checkpoint=='copy' else 0)
                assert ordinary.claim(scope,'ordinary-upload-worker',now=now,lease_seconds=30) is None
                with ordinary._transaction(scope) as conn:
                    assert conn.execute('SELECT count(*) FROM durable_jobs WHERE job_id=%s',('job-'+object_id(claim,'raw'),)).fetchone()[0]==0
            finally:
                release.set()
            assert task.result()==object_id(claim,'raw')
        record=queue.get_object(scope,object_id(claim,'raw'))
        job=queue.get_job(scope,'job-'+record.object_id)
        assert record.status=='completed' and job.state=='completed' and job.attempt==1 and job.version==3
        assert enforcement==[(ctx.tenant_id,'source.create',{'storage_bytes':len(page().raw_content)},
            (ctx.tenant_id,ctx.workspace_id,'daon_app'))]
        with ordinary._transaction(scope) as conn:
            assert conn.execute('SELECT count(*) FROM audit_events WHERE target_id=%s',(record.object_id,)).fetchone()[0]==2
            assert conn.execute('SELECT count(*) FROM job_attempts WHERE job_id=%s AND outcome=%s',(job.job_id,'completed')).fetchone()[0]==1
    finally:
        release.set(); queue.close(); ordinary.close(); repo.close()


def test_web_copy_failure_rolls_back_job_and_retains_request_tracking(pg):
    import time
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.website_source_import import WebsiteObjectPromoter, WebsiteProcessStorage, WebsiteImportError, object_id
    from test_object_queue import FakeObjectStorage
    ctx=seed(pg); repo=api().PostgresWebsiteSourceRepository(pg,policy_fingerprint=lambda ctx:'pfp')
    claim=repo.claim(ctx,'https://example.com/','copy-failure')
    scope=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'object.write')
    queue=scoped_queue(pg)
    class Storage(FakeObjectStorage,WebsiteProcessStorage):
        def promote(self,*args,**kwargs):
            raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
    try:
        with pytest.raises(WebsiteImportError,match='WEBSITE_STORAGE_UNAVAILABLE'):
            WebsiteObjectPromoter(queue,Storage()).promote(ctx,claim,'raw',page().raw_content,time.monotonic()+12)
        with queue._transaction(scope) as conn:
            for table in ('object_records','durable_jobs','object_outbox_events','job_attempts','audit_events'):
                assert conn.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s AND workspace_id=%s',
                    (ctx.tenant_id,ctx.workspace_id)).fetchone()[0]==0
        with repo._transaction(ctx) as conn:
            row=conn.execute('SELECT state,raw_object_id,text_object_id FROM website_import_requests WHERE request_id=%s',
                (claim.request_id,)).fetchone()
            assert row==('fetching',object_id(claim,'raw'),object_id(claim,'text'))
    finally:
        queue.close(); repo.close()


def _atomic_copy_parent_process():
    """PG 부모 kill 시험 entry. DSN은 환경 메모리, scope/claim은 bounded stdin만."""
    import json
    import sys
    import threading
    import time
    from daon_user_api.internet_connector import WebsiteImportContext
    from daon_user_api.website_source_import import WebsiteImportClaim, WebsiteObjectPromoter, WebsiteProcessStorage
    from test_object_queue import FakeObjectStorage
    request=json.loads(sys.stdin.buffer.read(8193))
    ctx=WebsiteImportContext(**request['context']); claim=WebsiteImportClaim(**request['claim'])
    class Storage(FakeObjectStorage,WebsiteProcessStorage):
        def promote(self,*args,**kwargs):
            print('COPY-TRANSACTION-OPEN',flush=True)
            # 실제 I/O 대신 parent kill 전까지 멈춘다. 정상 처리/MinIO 성공으로 표시하지 않는다.
            threading.Event().wait(30)
            raise AssertionError('부모는 COPY checkpoint에서 kill되어야 한다')
    queue=scoped_queue(os.environ['DAON_S13_ISOLATED_POSTGRES_DSN'])
    try:
        WebsiteObjectPromoter(queue,Storage()).promote(ctx,claim,'raw',page().raw_content,time.monotonic()+12)
    finally:
        queue.close()


def test_web_pipeline_parent_kill_rolls_back_and_generic_consumer_cannot_reclaim(pg):
    import json
    import subprocess
    import sys
    import threading
    from dataclasses import asdict
    from datetime import timedelta
    from pathlib import Path
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.website_source_import import object_id
    ctx=seed(pg); repo=api().PostgresWebsiteSourceRepository(pg,policy_fingerprint=lambda ctx:'pfp')
    claim=repo.claim(ctx,'https://example.com/','parent-kill')
    scope=CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,'object.write')
    ordinary=scoped_queue(pg); env=os.environ.copy()
    env['PYTHONPATH']=os.pathsep.join([str(Path(__file__).parent),env.get('PYTHONPATH','')])
    payload=json.dumps({'context':asdict(ctx),'claim':asdict(claim)}).encode()
    assert len(payload)<=8192
    child=subprocess.Popen([sys.executable,'-B','-c',
        'from test_website_source_postgres import _atomic_copy_parent_process; _atomic_copy_parent_process()'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    signal=threading.Event(); output=[]
    def read_checkpoint():
        output.append(child.stdout.readline(256)); signal.set()
    reader=threading.Thread(target=read_checkpoint,name='s13-pg-copy-checkpoint')
    try:
        child.stdin.write(payload); child.stdin.close(); reader.start()
        assert signal.wait(10) and len(output)==1 and output[0].strip()==b'COPY-TRANSACTION-OPEN'
        assert ordinary.claim(scope,'ordinary-upload-worker',now=datetime.now(timezone.utc)+timedelta(seconds=31),lease_seconds=30) is None
        child.kill(); child.wait(timeout=3); reader.join(timeout=3)
        assert not reader.is_alive()
        with ordinary._transaction(scope) as conn:
            for table in ('object_records','durable_jobs','object_outbox_events','job_attempts','audit_events'):
                assert conn.execute(f'SELECT count(*) FROM {table} WHERE tenant_id=%s AND workspace_id=%s',
                    (ctx.tenant_id,ctx.workspace_id)).fetchone()[0]==0
        assert ordinary.claim(scope,'ordinary-upload-worker',now=datetime.now(timezone.utc)+timedelta(seconds=31),lease_seconds=30) is None
        with repo._transaction(ctx) as conn:
            assert conn.execute('SELECT raw_object_id,text_object_id FROM website_import_requests WHERE request_id=%s',
                (claim.request_id,)).fetchone()==(object_id(claim,'raw'),object_id(claim,'text'))
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)
        if reader.ident is not None:
            reader.join(timeout=3)
        child.stdin.close(); child.stdout.close(); ordinary.close(); repo.close()
