from __future__ import annotations

import hashlib
import importlib
import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from dataclasses import replace

import pytest

from daon_user_api.internet_connector import FetchedWebsite, WebsiteImportContext


def api():
    assert importlib.util.find_spec('daon_user_api.website_source_import') is not None
    return importlib.import_module('daon_user_api.website_source_import')


def context():
    return WebsiteImportContext('s13-tenant', 's13-workspace', 's13-notebook', 's13-actor', 's13-trace', 'policy-v1')


def page(url='https://example.com/', raw=b'<p>same text</p>', text=b'same text'):
    return FetchedWebsite(url, url, (), 'title', None, 'unknown', None,
        datetime.now(timezone.utc).isoformat(), raw, text, hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(text).hexdigest(), 'website-text-v1')


class Repository:
    """소유한 claim/객체 기록만 보존하는 test 전용 영속 경계 double."""
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()
        self.results = []
        self.fail_complete = False
        self.orphans = []
    def claim(self, ctx, url, key):
        m = api()
        with self.lock:
            scope = (ctx.tenant_id, ctx.workspace_id, ctx.notebook_id, ctx.actor_id, key)
            prior = self.rows.get(scope)
            if prior and prior[0] != url:
                raise m.WebsiteImportError('WEBSITE_IDEMPOTENCY_KEY_REUSED', 409)
            if prior and prior[1] == 'active':
                raise m.WebsiteImportError('WEBSITE_IMPORT_IN_PROGRESS', 409)
            if prior and prior[1] == 'completed':
                return replace(prior[2], replayed=True)
            claim = m.WebsiteImportClaim('request-' + str(len(self.orphans)) + key, hashlib.sha256(url.encode()).hexdigest(), False, None)
            self.rows[scope] = (url, 'active', claim)
            return claim
    def complete(self, ctx, claim, fetched, raw_id, text_id):
        m = api()
        if self.fail_complete:
            raise m.WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE', 503)
        result = m.WebsiteImportResult('snapshot-' + claim.request_id, 'source-' + claim.request_id,
            'version-' + claim.request_id, 'ready', False)
        for scope, (url, state, value) in self.rows.items():
            if value.request_id == claim.request_id:
                self.rows[scope] = (url, 'completed', replace(claim, result=result))
                break
        self.results.append((fetched, raw_id, text_id, result))
        return result
    def fail(self, ctx, claim, code):
        self.orphans.append((claim.request_id, code))
        for scope, (url, state, value) in self.rows.items():
            if value.request_id == claim.request_id:
                self.rows[scope] = (url, 'failed', value)


class Promoter:
    def __init__(self, fail_at=0):
        self.objects = []
        self.fail_at = fail_at
    def promote(self, ctx, claim, kind, content, deadline):
        if len(self.objects) + 1 == self.fail_at:
            raise RuntimeError('private storage detail')
        self.objects.append((claim.request_id, kind, content))
        return hashlib.sha256((claim.request_id + kind).encode()).hexdigest()[:32]


def service(repository=None, promoter=None, fetch=None, authorize=None, clock=None):
    m = api()
    return m.WebsiteSourceImportService(repository or Repository(), promoter or Promoter(),
        fetch_page=fetch or (lambda ctx,url: page(url)), authorize_context=authorize or (lambda ctx: None),
        clock=clock)


def test_request_replay_and_concurrent_claim():
    repository, promoter = Repository(), Promoter()
    calls = []
    svc = service(repository, promoter, fetch=lambda ctx,url: (calls.append(url) or page(url)))
    result = svc.import_url(context(), 'https://example.com/', True, 'key')
    replay = svc.import_url(context(), 'https://example.com/', True, 'key')
    assert result.source_state == 'ready' and replay.replayed
    assert replay.source_version_id == result.source_version_id
    assert calls == ['https://example.com/'] and len(promoter.objects) == 2
    with pytest.raises(api().WebsiteImportError, match='WEBSITE_IDEMPOTENCY_KEY_REUSED'):
        svc.import_url(context(), 'https://example.com/other', True, 'key')
    started, finish = threading.Event(), threading.Event()
    def fetch(ctx,url):
        started.set()
        assert finish.wait(2)
        return page(url)
    svc = service(repository=Repository(), fetch=fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        task = pool.submit(svc.import_url, context(), 'https://example.com/', True, 'race')
        assert started.wait(2)
        try:
            with pytest.raises(api().WebsiteImportError, match='WEBSITE_IMPORT_IN_PROGRESS'):
                svc.import_url(context(), 'https://example.com/', True, 'race')
        finally:
            finish.set()
        assert task.result().source_state == 'ready'


def test_origin_identity_not_plaintext_dedup():
    repository = Repository()
    svc = service(repository=repository)
    first = svc.import_url(context(), 'https://example.com/', True, 'one')
    second = svc.import_url(context(), 'https://example.com/other', True, 'two')
    third = service(repository=repository, fetch=lambda ctx,url: page(url, raw=b'<div>same text</div>')).import_url(
        context(), 'https://example.com/', True, 'three')
    assert len({r.source_id for r in (first, second, third)}) == 3
    assert len({p.text_sha256 for p, *_ in repository.results}) == 1


@pytest.mark.parametrize('stage', ['first-object', 'second-object', 'db'])
def test_failed_storage_never_publishes_source(stage):
    repository = Repository()
    repository.fail_complete = stage == 'db'
    promoter = Promoter(1 if stage == 'first-object' else 2 if stage == 'second-object' else 0)
    svc = service(repository, promoter)
    with pytest.raises(api().WebsiteImportError, match='^WEBSITE_STORAGE_UNAVAILABLE$'):
        svc.import_url(context(), 'https://example.com/', True, 'key')
    assert repository.results == [] and len(repository.orphans) == 1
    repository.fail_complete = False
    promoter.fail_at = 0
    assert svc.import_url(context(), 'https://example.com/', True, 'key').source_state == 'ready'


def test_current_permission_and_confirmation_fail_before_fetch_or_claim():
    repository, promoter = Repository(), Promoter()
    def denied(ctx):
        raise PermissionError('private detail')
    svc = service(repository, promoter, authorize=denied)
    with pytest.raises(api().WebsiteImportError, match='^WEBSITE_ACCESS_DENIED$'):
        svc.import_url(context(), 'https://example.com/', True, 'key')
    svc = service(repository, promoter)
    for flag in (False, 1, 'true'):
        with pytest.raises(api().WebsiteImportError):
            svc.import_url(context(), 'https://example.com/', flag, 'key')
    assert repository.rows == {} and promoter.objects == []


def test_corrupted_page_and_store_deadline_cannot_complete():
    repository = Repository()
    svc = service(repository=repository, fetch=lambda ctx,url: replace(page(url), text_sha256='0' * 64))
    with pytest.raises(api().WebsiteImportError, match='WEBSITE_FETCH_FAILED'):
        svc.import_url(context(), 'https://example.com/', True, 'key')
    clock = [100.0]
    def slow(ctx,url):
        clock[0] = 112.1
        return page(url)
    svc = service(repository=repository, fetch=slow, clock=lambda: clock[0])
    with pytest.raises(api().WebsiteImportError, match='WEBSITE_FETCH_TIMEOUT'):
        svc.import_url(context(), 'https://example.com/', True, 'slow')
    assert repository.results == []


def test_fetch_exception_is_not_reported_as_storage_failure():
    def failed(ctx,url):
        raise RuntimeError('private upstream detail')
    with pytest.raises(api().WebsiteImportError,match='^WEBSITE_FETCH_FAILED$'):
        service(fetch=failed).import_url(context(),'https://example.com/',True,'key')


@pytest.mark.parametrize('reason', ['revoked','timeout'])
def test_new_claim_postcheck_failure_is_recorded_without_fetch(reason):
    repo=Repository(); now=[100.0]; calls=[]
    original=repo.claim
    def claim(ctx,url,key):
        result=original(ctx,url,key)
        if reason=='timeout':
            now[0]=120.1
        return result
    repo.claim=claim
    def authorize(ctx):
        if reason=='revoked' and repo.rows:
            raise PermissionError
    svc=service(repo,fetch=lambda ctx,url:calls.append(url),authorize=authorize,clock=lambda:now[0])
    code='WEBSITE_ACCESS_DENIED' if reason=='revoked' else 'WEBSITE_FETCH_TIMEOUT'
    with pytest.raises(api().WebsiteImportError,match=code):
        svc.import_url(context(),'https://example.com/',True,'key')
    assert calls==[]
    assert len(repo.orphans)==1 and repo.orphans[0][1]==code
    assert next(iter(repo.rows.values()))[1]=='failed'


def test_completed_replay_postcheck_failure_does_not_fail_completed_request():
    repo=Repository(); svc=service(repo)
    result=svc.import_url(context(),'https://example.com/',True,'key')
    checks=[]
    def authorize(ctx):
        checks.append(1)
        if len(checks)==2:
            raise PermissionError
    with pytest.raises(api().WebsiteImportError,match='WEBSITE_ACCESS_DENIED'):
        service(repo,authorize=authorize).import_url(context(),'https://example.com/',True,'key')
    assert repo.orphans==[] and repo.results[0][-1]==result
    assert next(iter(repo.rows.values()))[1]=='completed'


def test_store_phase_records_storing_before_first_promotion():
    m=api(); repo=Repository(); stages=[]
    def storing(ctx,claim):
        stages.append('storing')
    repo.mark_storing=storing
    class Promoted(Promoter):
        def promote(self,*args):
            assert stages==['storing']
            return super().promote(*args)
    service(repo,Promoted()).import_url(context(),'https://example.com/',True,'key')


@pytest.mark.parametrize('fetch_seconds,store_seconds,accepted', [(7,6,True),(7,12.1,False),(8.1,0,False)])
def test_fetch_store_and_api_deadlines_are_separate(fetch_seconds,store_seconds,accepted):
    now=[100.0]; repo=Repository()
    def fetch(ctx,url):
        now[0]+=fetch_seconds
        return page(url)
    class TimedPromoter(Promoter):
        def promote(self,ctx,claim,kind,content,deadline):
            if kind=='raw':
                now[0]+=store_seconds
            return super().promote(ctx,claim,kind,content,deadline)
    svc=service(repo,TimedPromoter(),fetch=fetch,clock=lambda:now[0])
    if accepted:
        assert svc.import_url(context(),'https://example.com/',True,'key').source_state=='ready'
    else:
        with pytest.raises(api().WebsiteImportError,match='WEBSITE_FETCH_TIMEOUT'):
            svc.import_url(context(),'https://example.com/',True,'key')
        assert repo.results==[]


@pytest.mark.parametrize('copy_timeout', [False,True])
def test_production_promoter_uses_existing_coordinator_worker_and_only_owned_job(copy_timeout):
    """진짜 Coordinator/Worker 코드 + 메모리 경계. 실제 PG/MinIO 성공 시험이 아니다."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    import time
    from daon_user_api.object_queue import ObjectRecord, ObjectSubmission, PostgresObjectQueueStore
    from test_object_queue import FakeObjectStorage
    m=api(); ctx=context()
    class MemoryBoundedStorage(FakeObjectStorage, m.WebsiteProcessStorage):
        def promote(self,*args,**kwargs):
            assert queue.depth==1, '등록/전용 lease/COPY/완료를 하나의 원자 transaction으로 실행'
            assert queue.commits==[], 'COPY 전에 pending/leased job commit 금지'
            if copy_timeout:
                raise m.WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)
            return super().promote(*args,**kwargs)
    storage=MemoryBoundedStorage()
    claim=m.WebsiteImportClaim('website-request-'+'2'*32,'b'*64,False,None)
    class Queue:
        def __init__(self):
            self.records={}; self.owned_jobs=[]; self.completed=[]; self.depth=0; self.commits=[]
        def register_object_job(self,scope,**kw):
            oid=kw['object_id']; staged=kw['staged']
            self.records[oid]=ObjectRecord(oid,'source',staged.key,kw['final_key'],
                staged.digest_sha256,staged.byte_size,staged.content_type,'staged',None,None,1)
            return ObjectSubmission(oid,'job-'+oid,'event-'+oid,False)
        @contextmanager
        def _transaction(self,scope):
            queue=self
            class Connection:
                def execute(self,sql,params=()):
                    row=None
                    if sql.startswith('UPDATE durable_jobs'):
                        assert 'AND job_id=%s AND created_by=%s' in sql
                        assert params[2:4]==(ctx.tenant_id,ctx.workspace_id) and params[5]==ctx.actor_id
                        oid=params[4][4:]; queue.owned_jobs.append(params[4])
                        now=datetime.now(timezone.utc)
                        row=(params[4],'event-'+oid,{'schema_version':1,'object_id':oid},'leased',
                            1,4,now,params[0],params[1],None,None,ctx.trace_id,2,now)
                    return SimpleNamespace(fetchone=lambda:row)
            before=dict(self.records); completed=list(self.completed)
            self.depth+=1
            try:
                yield Connection()
            except BaseException:
                self.records=before; self.completed=completed
                raise
            else:
                self.commits.append(tuple(record.status for record in self.records.values()))
            finally:
                self.depth-=1
        _job_from_row=staticmethod(PostgresObjectQueueStore._job_from_row)
        def get_object(self,scope,oid):
            return self.records[oid]
        def complete(self,scope,job,worker,stored,**kw):
            assert worker=='api-website-import-worker'
            self.records[job.object_id]=replace(self.records[job.object_id],status='completed')
            self.completed.append(job.object_id)
            return replace(job,state='completed')
        def fail(self,scope,job,worker,error,**kw):
            assert not error.retryable and error.code=='WEBSITE_FETCH_TIMEOUT'
            self.records[job.object_id]=replace(self.records[job.object_id],status='failed')
            return replace(job,state='dead_letter')
        def claim(self,*args,**kwargs):
            raise AssertionError('Workspace 전체 queue claim 금지')
    queue=Queue(); promoter=m.WebsiteObjectPromoter(queue,storage)
    original_transaction=queue._transaction
    fetched=page()
    if copy_timeout:
        with pytest.raises(m.WebsiteImportError,match='WEBSITE_FETCH_TIMEOUT'):
            promoter.promote(ctx,claim,'raw',fetched.raw_content,time.monotonic()+12)
        assert queue.records=={}, 'COPY 실패 시 해당 Object/job 등록도 rollback'
        assert queue.completed==[]
        assert queue._transaction==original_transaction
        return
    for kind,content in [('raw',fetched.raw_content),('text',fetched.text_content)]:
        queue.commits.clear()
        oid=promoter.promote(ctx,claim,kind,content,time.monotonic()+12)
        record=queue.records[oid]
        assert record.digest_sha256==hashlib.sha256(content).hexdigest()
        assert storage.get(record.object_key)==content
        assert record.content_type==('application/octet-stream' if kind=='raw' else 'text/plain')
        assert queue.depth==0 and all(status=='completed' for status in queue.commits[-1])
    assert queue.completed==[m.object_id(claim,'raw'),m.object_id(claim,'text')]
    assert queue.owned_jobs==['job-'+oid for oid in queue.completed]
    assert queue._transaction==original_transaction


@pytest.mark.parametrize('operation', ['put','copy'])
def test_web_storage_timeout_kills_and_reaps_child_without_late_work(monkeypatch,operation):
    """100ms 소형 deadline + 실제 중단 child. 실제 MinIO I/O 성공이 아니다."""
    import subprocess
    import sys
    import time
    from daon_user_api.object_queue import StagedObject
    m=api()
    assert hasattr(m,'WebsiteProcessStorage'), '웹 전용 강제 종결 I/O 경계 필요'
    processes=[]; real_popen=subprocess.Popen
    def launch(argv,**kwargs):
        assert 'PRIVATE-TEST-KEY' not in repr(argv) and 'PRIVATE-TEST-SECRET' not in repr(argv)
        # Storage 경계 대신 멈추는 child만 대체하며 부모 timeout/kill/reap는 실제 실행.
        process=real_popen([sys.executable,'-I','-c','import time; time.sleep(60)'],**kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(m.subprocess,'Popen',launch)
    storage=m.WebsiteProcessStorage(endpoint='unused:9000',bucket='s13-test',
        access_key='PRIVATE-TEST-KEY',secret_key='PRIVATE-TEST-SECRET',secure=False)
    started=time.monotonic()
    token=m._store_deadline.set(started+.1)
    try:
        with pytest.raises(m.WebsiteImportError,match='^WEBSITE_FETCH_TIMEOUT$'):
            if operation=='put':
                storage.put_staged('stage',b'a','text/plain',hashlib.sha256(b'a').hexdigest())
            else:
                staged=StagedObject('stage','a'*64,1,'text/plain','',None)
                storage.promote(staged,'final',expected_digest='a'*64,expected_size=1,content_type='text/plain')
    finally:
        m._store_deadline.reset(token)
    assert len(processes)==1 and processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed
    assert time.monotonic()-started<1.0
    assert not any(t.name.startswith('website-storage-pipe-') for t in threading.enumerate())


def test_web_storage_rejects_unbounded_payload_before_child(monkeypatch):
    m=api()
    assert hasattr(m,'WebsiteProcessStorage')
    calls=[]
    monkeypatch.setattr(m.subprocess,'Popen',lambda *args,**kwargs:calls.append(args))
    storage=m.WebsiteProcessStorage(endpoint='unused:9000',bucket='s13-test',access_key='k',secret_key='s')
    import time
    token=m._store_deadline.set(time.monotonic()+12)
    try:
        with pytest.raises(m.WebsiteImportError):
            content=b'x'*2097153
            storage.put_staged('stage',content,'text/plain',hashlib.sha256(content).hexdigest())
    finally:
        m._store_deadline.reset(token)
    assert calls==[]


@pytest.mark.parametrize('mode', ['success','digest-mismatch','oversize'])
def test_web_storage_bounded_response_and_memory_only_request(monkeypatch,mode):
    import subprocess
    import sys
    import time
    m=api(); real_popen=subprocess.Popen; processes=[]; read_sizes=[]
    code="""import json,sys
r=json.load(sys.stdin); a=r['arguments']
assert r['config']['access_key']=='PRIVATE-TEST-KEY'
assert r['config']['secret_key']=='PRIVATE-TEST-SECRET'
result={'record':{'key':a['key'],'digest_sha256':a['digest'],'byte_size':a['size'],'content_type':a['content_type'],'etag':'e','version_id':None}}
"""
    if mode=='oversize':
        code+="sys.stdout.write('x'*9000)"
    else:
        if mode=='digest-mismatch':
            code+="result['record']['digest_sha256']='0'*64\n"
        code+="json.dump(result,sys.stdout)"
    def launch(argv,**kwargs):
        assert 'PRIVATE-TEST' not in repr(argv)
        process=real_popen([sys.executable,'-I','-c',code],**kwargs)
        original=process.stdout
        class BoundedReadObserver:
            def read(self,size=-1):
                read_sizes.append(size)
                return original.read(size)
            def __getattr__(self,name):
                return getattr(original,name)
        process.stdout=BoundedReadObserver()
        processes.append(process)
        return process
    monkeypatch.setattr(m.subprocess,'Popen',launch)
    storage=m.WebsiteProcessStorage(endpoint='unused:9000',bucket='s13-test',
        access_key='PRIVATE-TEST-KEY',secret_key='PRIVATE-TEST-SECRET')
    token=m._store_deadline.set(time.monotonic()+2)
    try:
        if mode=='success':
            record=storage.put_staged('owned-stage',b'a','text/plain',hashlib.sha256(b'a').hexdigest())
            assert record.key=='owned-stage' and record.byte_size==1
        else:
            with pytest.raises(m.WebsiteImportError,match='^WEBSITE_STORAGE_UNAVAILABLE$'):
                storage.put_staged('owned-stage',b'a','text/plain',hashlib.sha256(b'a').hexdigest())
    finally:
        m._store_deadline.reset(token)
    assert processes[0].poll() is not None
    assert read_sizes and all(0<size<=1024 for size in read_sizes), 'stdout 할당/읽기 전에 bound 적용'


def test_web_promoter_rejects_unbounded_ordinary_storage():
    from test_object_queue import FakeObjectStorage
    with pytest.raises(ValueError,match='WEBSITE_BOUNDED_STORAGE_REQUIRED'):
        api().WebsiteObjectPromoter(object(),FakeObjectStorage())


@pytest.mark.parametrize('operation', ['put','copy'])
def test_storage_child_reuses_minio_adapter_with_bounded_protocol(monkeypatch,operation):
    import base64
    import io
    import json
    import time
    from types import SimpleNamespace
    from daon_user_api.object_queue import StagedObject, StoredObject
    m=api(); content=b'a'; digest=hashlib.sha256(content).hexdigest(); calls=[]; adapters=[]
    class Adapter:
        def __init__(self,**config):
            assert config=={'endpoint':'unused:9000','bucket':'s13-test','access_key':'k','secret_key':'s','secure':False}
            self._client=SimpleNamespace(_http=SimpleNamespace(clear=lambda:None))
            adapters.append(self)
        def put_staged(self,key,body,content_type,sha):
            calls.append('put')
            assert body==content and sha==digest
            return StagedObject(key,sha,1,content_type,'e',None)
        def promote(self,staged,key,**kw):
            calls.append('copy')
            assert staged.digest_sha256==digest and kw['expected_digest']==digest
            return StoredObject(key,digest,1,kw['content_type'],'e',None)
    args={'key':'stage','content':base64.b64encode(content).decode(),'size':1,'digest':digest,'content_type':'text/plain'}
    if operation=='copy':
        args={'staged':{'key':'stage','digest_sha256':digest,'byte_size':1,'content_type':'text/plain','etag':'e','version_id':None},
            'final_key':'final','expected_digest':digest,'expected_size':1,'content_type':'text/plain'}
    request={'operation':operation,'config':{'endpoint':'unused:9000','bucket':'s13-test','access_key':'k','secret_key':'s','secure':False},
        'arguments':args,'deadline':time.monotonic()+2}
    output=io.BytesIO()
    monkeypatch.setattr(m,'MinioObjectStorageAdapter',Adapter)
    monkeypatch.setattr(m.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
    monkeypatch.setattr(m.sys,'stdout',SimpleNamespace(buffer=output))
    m._website_storage_child()
    record=json.loads(output.getvalue())['record']
    assert record['digest_sha256']==digest and record['key']==('stage' if operation=='put' else 'final')
    assert calls==[operation]
    assert adapters[0]._client._http.connection_pool_kw['cert_reqs']=='CERT_REQUIRED'


def test_actual_storage_child_entry_rejects_invalid_input_without_network():
    """실제 Python child entry/protocol만 확인. 설정 누락으로 MinIO 생성 전 거부."""
    import subprocess
    import sys
    import time
    m=api()
    with subprocess.Popen([sys.executable,'-B','-m',m.__name__],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if __import__('os').name=='nt' else 0) as child:
        output=m._storage_exchange(child,b'{}',time.monotonic()+3)
        assert child.returncode==0
    assert output==b'{"error":"WEBSITE_STORAGE_UNAVAILABLE"}'
