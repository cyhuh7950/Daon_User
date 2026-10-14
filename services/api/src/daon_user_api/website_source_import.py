"""웹 수집 결과의 멱등 등록. 객체 승격과 DB 확정의 경계를 분리한다."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import base64
from copy import copy
import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import Callable

from .internet_connector import FetchedWebsite, WebsiteImportContext
from .safe_http_fetch import WebsiteFetchError, normalize_public_url
from .cloud_storage import CloudAccessContext
from .object_queue import (ObjectQueueCoordinator, DurableObjectWorker, MinioObjectStorageAdapter,
                           ObjectQueueError, StagedObject, StoredObject)

_store_deadline: ContextVar[float | None] = ContextVar('website_store_deadline', default=None)


class WebsiteImportError(RuntimeError):
    def __init__(self, code: str, status: int = 503):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class WebsiteImportResult:
    snapshot_id: str
    source_id: str
    source_version_id: str
    source_state: str
    replayed: bool


@dataclass(frozen=True)
class WebsiteImportClaim:
    request_id: str
    fingerprint: str
    replayed: bool
    result: WebsiteImportResult | None


@dataclass(frozen=True)
class WebsiteSnapshotView:
    snapshot_id: str
    source_id: str
    source_version_id: str
    requested_url: str
    final_url: str
    title: str
    published_at: str | None
    fetched_at: str
    license_status: str
    license_url: str | None
    raw_sha256: str
    text_sha256: str
    extraction_version: str


def object_id(claim: WebsiteImportClaim, kind: str) -> str:
    return hashlib.sha256(f'{claim.request_id}|{kind}'.encode()).hexdigest()[:32]


def validate_page(page: FetchedWebsite, requested_url: str) -> None:
    if (not isinstance(page, FetchedWebsite) or page.requested_url != requested_url
            or normalize_public_url(page.final_url) != page.final_url
            or type(page.raw_content) is not bytes or not 0 < len(page.raw_content) <= 2097152
            or type(page.text_content) is not bytes or not 0 < len(page.text_content) <= 524288
            or hashlib.sha256(page.raw_content).hexdigest() != page.raw_sha256
            or hashlib.sha256(page.text_content).hexdigest() != page.text_sha256
            or page.extraction_version != 'website-text-v1'
            or page.license_status not in ('unknown', 'publisher_declared')
            or page.license_status == 'unknown' and page.license_url is not None):
        raise WebsiteImportError('WEBSITE_FETCH_FAILED', 502)
    try:
        if not page.text_content.decode('utf-8', errors='strict').strip():
            raise ValueError
        datetime.fromisoformat(page.fetched_at)
    except (ValueError, UnicodeError):
        raise WebsiteImportError('WEBSITE_FETCH_FAILED', 502) from None


class WebsiteSourceImportService:
    def __init__(self, repository, promoter, *, fetch_page: Callable,
                 authorize_context: Callable, clock: Callable | None = None):
        self._repository, self._promoter = repository, promoter
        self._fetch_page, self._authorize = fetch_page, authorize_context
        self._clock = clock or time.monotonic

    def import_url(self, context, url, public_page_confirmed, idempotency_key) -> WebsiteImportResult:
        if public_page_confirmed is not True or not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,255}', idempotency_key):
            raise WebsiteImportError('WEBSITE_URL_INVALID', 400)
        try:
            url = normalize_public_url(url)
        except WebsiteFetchError:
            raise WebsiteImportError('WEBSITE_URL_INVALID', 400) from None
        api_deadline = self._clock() + 20.0
        deadline = api_deadline
        def check():
            if self._clock() >= deadline:
                raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT', 504)
            try:
                if self._authorize(context) is not None:
                    raise PermissionError
            except Exception:
                raise WebsiteImportError('WEBSITE_ACCESS_DENIED', 403) from None
        check()
        claim = self._repository.claim(context, url, idempotency_key)
        if claim.replayed:
            check()  # 완료 replay는 거부되어도 failed로 변경하지 않는다.
            if claim.result is None:
                raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
            return replace(claim.result, replayed=True)
        phase='fetch'
        token=_store_deadline.set(deadline)
        try:
            check()  # 새 claim 확정 뒤 철회/timeout도 fail 기록 경계에 포함한다.
            deadline=min(api_deadline,self._clock()+8.0)
            check()
            page = self._fetch_page(context, url)
            validate_page(page, url)
            check()
            phase='store'
            deadline=min(api_deadline,self._clock()+12.0)
            _store_deadline.set(deadline)
            marker=getattr(self._repository,'mark_storing',None)
            if callable(marker):
                marker(context,claim)
            raw_id = self._promoter.promote(context, claim, 'raw', page.raw_content, deadline)
            check()
            text_id = self._promoter.promote(context, claim, 'text', page.text_content, deadline)
            check()
            return self._repository.complete(context, claim, page, raw_id, text_id)
        except Exception as error:
            if isinstance(error, WebsiteImportError):
                safe_error = error
            elif isinstance(error, WebsiteFetchError):
                status = {'WEBSITE_FETCH_BLOCKED':403,'WEBSITE_FETCH_TIMEOUT':504,
                          'WEBSITE_URL_INVALID':400,'WEBSITE_CONTENT_TOO_LARGE':413,
                          'WEBSITE_MEDIA_UNSUPPORTED':415,'WEBSITE_TEXT_UNAVAILABLE':422}.get(str(error),502)
                safe_error = WebsiteImportError(str(error), status)
            else:
                safe_error = WebsiteImportError('WEBSITE_FETCH_FAILED',502) if phase=='fetch' else WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
            try:
                self._repository.fail(context, claim, safe_error.code)
            except Exception:
                # DB 중단 시 claim과 결정적 raw/text object ID는 DB의 fetching 상태로
                # 남는다. 성공/정리 완료로 위장하지 않으며 자동 재-fetch하지 않는다.
                raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE') from None
            raise safe_error from None
        finally:
            _store_deadline.reset(token)


_STORAGE_INPUT_LIMIT = 3 * 1024 * 1024
_STORAGE_OUTPUT_LIMIT = 8192


def _storage_exchange(child,payload,deadline):
    """Windows/Linux pipe I/O를 bounded로 수행하고 모든 pipe thread도 회수한다.

    thread에서 Storage를 실행하지 않는다. Storage는 반드시 killable child에만 있다.
    """
    output=bytearray(); failed=threading.Event()
    def write_request():
        try:
            child.stdin.write(payload)
            child.stdin.flush()
        except (OSError,ValueError):
            failed.set()
        finally:
            child.stdin.close()
    def read_response():
        try:
            while True:
                chunk=child.stdout.read(min(1024,_STORAGE_OUTPUT_LIMIT-len(output)+1))
                if not chunk:
                    break
                if len(output)+len(chunk)>_STORAGE_OUTPUT_LIMIT:
                    failed.set()
                    if child.poll() is None:
                        child.kill()
                    break
                output.extend(chunk)
        except (OSError,ValueError):
            failed.set()
    writer=threading.Thread(target=write_request,name='website-storage-pipe-write')
    reader=threading.Thread(target=read_response,name='website-storage-pipe-read')
    writer.start(); reader.start()
    try:
        try:
            child.wait(timeout=max(.001,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504) from None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        writer.join(); reader.join()
    if failed.is_set():
        raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
    return bytes(output)


class WebsiteProcessStorage:
    """웹 PUT/COPY/stat만 killable child로 실행. 설정은 argv/로그/파일에 쓰지 않는다.

    W3는 ordinary Storage와 별개로 이 인스턴스를 구성한다. 원격 서버가 이미
    받은 PUT/COPY의 취소까지 보장하지는 않는다. 그 객체는 request의 결정적
    staging/final key로 추적되며 child는 DB에 접근하거나 성공을 확정하지 않는다.
    """
    def __init__(self, *, endpoint, bucket, access_key, secret_key, secure=True):
        config=dict(endpoint=endpoint,bucket=bucket,access_key=access_key,secret_key=secret_key,secure=secure)
        if (type(secure) is not bool or any(not isinstance(v,str) or not v or len(v)>4096
                for k,v in config.items() if k!='secure')):
            raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
        self._config=config

    def _call(self,operation,arguments):
        deadline=_store_deadline.get()
        if deadline is None or time.monotonic()>=deadline:
            raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)
        request={'operation':operation,'config':self._config,'arguments':arguments,'deadline':deadline}
        payload=json.dumps(request,separators=(',',':')).encode('utf-8')
        if len(payload)>_STORAGE_INPUT_LIMIT:
            raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
        # 제어된 child는 stdout에 bounded protocol 응답 한 개만 쓴다.
        # stderr는 외부 오류/credentials가 노출되지 않도록 폐기한다.
        try:
            with subprocess.Popen([sys.executable,'-B','-m',__name__],stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0) as child:
                output=_storage_exchange(child,payload,deadline)
                if time.monotonic()>=deadline:
                    raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)
                if child.returncode!=0 or len(output)>_STORAGE_OUTPUT_LIMIT:
                    raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
            result=json.loads(output)
            if not isinstance(result,dict) or set(result)!={'record'}:
                raise ValueError
            record=result['record']
            expected={'key','digest_sha256','byte_size','content_type','etag','version_id'}
            if not isinstance(record,dict) or set(record)!=expected:
                raise ValueError
            cls=StagedObject if operation=='put' else StoredObject
            value=cls(**record)
            digest=arguments['digest'] if operation=='put' else arguments['expected_digest']
            size=arguments['size'] if operation=='put' else arguments['expected_size']
            key=arguments['key'] if operation=='put' else arguments['final_key']
            if (value.key!=key or value.digest_sha256!=digest or type(value.byte_size) is not int
                    or value.byte_size!=size or value.content_type!=arguments['content_type']
                    or not isinstance(value.etag,str) or len(value.etag)>256
                    or value.version_id is not None and (not isinstance(value.version_id,str) or len(value.version_id)>256)):
                raise ValueError
            return value
        except WebsiteImportError:
            raise
        except Exception:
            raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE') from None

    def put_staged(self,key,content,content_type,digest):
        if (type(content) is not bytes or not 0<len(content)<=2097152
                or hashlib.sha256(content).hexdigest()!=digest):
            raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
        return self._call('put',{'key':key,'content':base64.b64encode(content).decode('ascii'),
            'size':len(content),'content_type':content_type,'digest':digest})

    def promote(self,staged,final_key,*,expected_digest,expected_size,content_type):
        if type(expected_size) is not int or not 0<expected_size<=2097152:
            raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
        return self._call('copy',{'staged':asdict(staged),'final_key':final_key,
            'expected_digest':expected_digest,'expected_size':expected_size,'content_type':content_type})


def _website_storage_child():
    """부모가 보낸 제한 payload만 사용. 오류 원문과 내부 설정을 출력하지 않는다."""
    result={'error':'WEBSITE_STORAGE_UNAVAILABLE'}
    try:
        raw=sys.stdin.buffer.read(_STORAGE_INPUT_LIMIT+1)
        if len(raw)>_STORAGE_INPUT_LIMIT:
            raise ValueError
        request=json.loads(raw)
        remaining=request['deadline']-time.monotonic()
        if remaining<=0:
            raise ValueError
        adapter=MinioObjectStorageAdapter(**request['config'])
        # 기존 adapter 재사용하되 이 웹 child의 client에만 남은시간/재시도0 적용.
        import urllib3
        import certifi
        adapter._client._http.clear()
        adapter._client._http=urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=min(2.0,remaining),read=remaining),retries=False,
            cert_reqs='CERT_REQUIRED',ca_certs=os.environ.get('SSL_CERT_FILE') or certifi.where())
        args=request['arguments']
        if request['operation']=='put':
            content=base64.b64decode(args['content'],validate=True)
            if not 0<len(content)<=2097152 or len(content)!=args['size']:
                raise ValueError
            record=adapter.put_staged(args['key'],content,args['content_type'],args['digest'])
        elif request['operation']=='copy':
            record=adapter.promote(StagedObject(**args['staged']),args['final_key'],
                expected_digest=args['expected_digest'],expected_size=args['expected_size'],content_type=args['content_type'])
        else:
            raise ValueError
        data=asdict(record)
        # 원격 metadata도 응답 JSON allocation 전에 제한한다.
        for name,limit in [('key',4096),('digest_sha256',64),('content_type',255),('etag',256),('version_id',256)]:
            value=data[name]
            if value is None and name=='version_id':
                continue
            if not isinstance(value,str) or len(value)>limit:
                raise ValueError
        result={'record':data}
    except Exception:
        pass
    encoded=json.dumps(result,separators=(',',':')).encode('utf-8')
    if len(encoded)>_STORAGE_OUTPUT_LIMIT:
        encoded=b'{"error":"WEBSITE_STORAGE_UNAVAILABLE"}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


class _WebsiteTransactionQueue:
    """요청별 facade. 기존 store 메서드의 transaction만 현재 connection에 참여시킨다.

    공유 store의 속성/메서드는 변경하지 않는다. 기존 키·License·감사·leaseVersion
    검증은 원 메서드에서 그대로 실행하며 이 객체는 pool/commit을 소유하지 않는다.
    """
    def __init__(self,store,connection,scope,deadline):
        self._connection,self._scope,self._deadline=connection,scope,deadline
        # 요청 전용 얕은 clone은 원 type/메서드와 enforcer를 유지한다. pool은 원 store가
        # 소유하며 clone은 열거나 닫지 않는다. 공유 인스턴스의 _transaction은 불변이다.
        self._store=copy(store)
        self._store._transaction=self._transaction

    def __getattr__(self,name):
        return getattr(self._store,name)

    def check(self):
        if time.monotonic()>=self._deadline:
            raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT',504)

    @contextmanager
    def _transaction(self,scope):
        if scope!=self._scope:
            raise WebsiteImportError('WEBSITE_ACCESS_DENIED',403)
        self.check()
        self._connection.execute("SELECT set_config('statement_timeout',%s,true)",
            (str(max(1,int((self._deadline-time.monotonic())*1000))),))
        yield self._connection
        self.check()


class WebsiteObjectPromoter:
    """현재 request의 두 객체만 승격한다. Workspace 전체 run_once는 호출하지 않는다."""
    def __init__(self, queue_store, storage):
        if not isinstance(storage,WebsiteProcessStorage):
            raise ValueError('WEBSITE_BOUNDED_STORAGE_REQUIRED')
        self._queue = queue_store
        self._storage = storage

    def promote(self, context, claim, kind, content, deadline):
        previous=_store_deadline.get()
        token=_store_deadline.set(min(previous,deadline) if previous is not None else deadline)
        try:
            return self._promote(context,claim,kind,content,_store_deadline.get())
        finally:
            _store_deadline.reset(token)

    def _promote(self, context, claim, kind, content, deadline):
        if kind not in ('raw', 'text') or time.monotonic() >= deadline:
            raise WebsiteImportError('WEBSITE_FETCH_TIMEOUT', 504)
        scope = CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, 'object.write')
        oid = object_id(claim, kind)
        # 등록→소유 lease→bounded COPY→complete/fail 전부 동일 transaction이다.
        # 다른 consumer에는 pending/leased 중간 행이 보이지 않는다. 부모 kill/COPY
        # 실패 시 rollback되고 staging/final 객체는 사전 claim의 결정적 ID로 추적한다.
        with self._queue._transaction(scope) as connection:
            queue=_WebsiteTransactionQueue(self._queue,connection,scope,deadline)
            coordinator=ObjectQueueCoordinator(queue,self._storage,id_factory=lambda:'0'*32)
            worker=DurableObjectWorker(queue,self._storage,'api-website-import-worker')
            submitted=coordinator.submit(scope,area='source',content=content,
                content_type='application/octet-stream' if kind=='raw' else 'text/plain',
                idempotency_key='website-'+oid,trace_id=context.trace_id,object_id=oid)
            if submitted.object_id!=oid:
                raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
            queue.check()
            now=datetime.now(timezone.utc)
            row = connection.execute(
                "UPDATE durable_jobs SET state='leased',attempt=attempt+1,lease_owner=%s,lease_until=%s,"
                "last_safe_error_code=NULL,version=version+1 WHERE tenant_id=%s AND workspace_id=%s "
                "AND job_id=%s AND created_by=%s AND ((state IN ('pending','retry_wait') AND next_attempt_at<=%s) "
                "OR (state='leased' AND lease_until<=%s)) RETURNING job_id,event_id,payload_reference,state,"
                "attempt,max_attempts,next_attempt_at,lease_owner,lease_until,last_safe_error_code,retry_of_job_id,"
                "trace_id,version,created_at",
                ('api-website-import-worker',now+timedelta(seconds=30),context.tenant_id,context.workspace_id,
                 submitted.job_id,context.actor_id,now,now)).fetchone()
            if row is not None:
                connection.execute("UPDATE object_outbox_events SET status='dispatched' WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s",
                    (context.tenant_id,context.workspace_id,submitted.event_id))
            if row is not None:
                job=queue._job_from_row(row)
                try:
                    worker.process_claimed(scope,job,now=now)
                except WebsiteImportError as error:
                    # 감사/fail도 참여 transaction으로 실행. deadline이 이미 만료됐으면
                    # facade가 fail조차 거부하여 전체 rollback한다. ordinary 재시도 없음.
                    failed_at=datetime.now(timezone.utc)
                    queue.fail(scope,job,'api-website-import-worker',
                        ObjectQueueError(error.code,retryable=False),now=failed_at,retry_at=failed_at)
                    raise
            record=queue.get_object(scope,oid)
            if record.status!='completed':
                raise WebsiteImportError('WEBSITE_STORAGE_UNAVAILABLE')
            queue.check()
        return oid


if __name__=='__main__':
    _website_storage_child()
