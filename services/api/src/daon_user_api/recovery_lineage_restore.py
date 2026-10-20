"""R3 내부 v3 재생기. HTTP/target 생성/DDL/자동 재시도·삭제는 하지 않는다.

Linux 소유 고정 child와 익명 bounded IPC로 Object당/선택 합계512MiB를 처리한다.
Windows에서는 이 concrete transport를 503 거부한다. 실제 Linux/Object 검증은 별도다.
Foundation/물리 identity/보호 catalog는 운영자가 준비한 고정 포트로 확인한다.
이 모듈의 materialized 결과는 source restore.completed/알림 확정이 아니다.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import re
import select
import signal
import socket
import ssl
import struct
import sys
import threading
import time
from urllib.parse import urlsplit

from minio import Minio
from urllib3 import PoolManager
from urllib3.response import HTTPResponse
from urllib3.util import Timeout

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext
from .data_canon import transition_allowed
from .object_queue import (DurableObjectWorker, MinioObjectStorageAdapter, ObjectKeyPolicy,
    PostgresObjectQueueStore, RetryPolicy, StagedObject, StoredObject, ObjectStorageError, ObjectQueueError)
from .recovery import RecoveryContext, RecoveryError, _fingerprint, _safe
from .recovery_destinations import (DatabaseIdentity, RegisteredDestination, TrustedPhysicalProbe,
    physical_receipt_digest)
from .recovery_lineage import _COLUMNS, _json
from .recovery_lineage_capture import FileLineageCaptureStore, LineageCaptureScope, _metadata
from .service_snapshot_streams import FixedSnapshotStreams, _MinioSource, _close_inherited_fds


MAX_OBJECT_BYTES = 512 * 1024 * 1024
FRAME = 65536
PAYLOAD = FRAME-5
PART = 5 * 1024 * 1024
_TABLES = ("sources", "source_versions", "notebooks", "notebook_metadata_versions", "notebook_bindings")
_RLS_TABLES = (*_TABLES,"object_records","canon_state_transitions","canon_transition_attempts")
_KEYS = {"sources": ("record_id",), "source_versions": ("record_id",), "notebooks": ("notebook_id",),
    "notebook_metadata_versions": ("notebook_id", "version"), "notebook_bindings": ("notebook_id", "binding_kind", "record_id")}


def _invalid(): return RecoveryError("BACKUP_MANIFEST_INVALID", 409)
def _unavailable(): return RecoveryError("RESOURCE_UNAVAILABLE", 503)
def _hash(value): return hashlib.sha256(_json(value)).hexdigest()
def _hex(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value): raise _invalid()
    return value


def _object_budget(rows):
    if not isinstance(rows,list) or len(rows)>100: raise _invalid()
    total=0
    for row in rows:
        size=row["byte_size"]
        if type(size) is not int or size<0: raise _invalid()
        total+=size
        if total>MAX_OBJECT_BYTES: raise RecoveryError("BACKUP_SELECTION_TOO_LARGE",413)


@dataclass(frozen=True,slots=True,repr=False)
class _ObjectWork:
    source: _MinioSource
    mode: str
    staging_key: str
    final_key: str
    size: int
    digest: str
    content_type: str
    deadline: float


def _frame(kind,data=b""):
    if len(data)>PAYLOAD or kind not in {b"D",b"E",b"R",b"X",b"C",b"G"}: raise _invalid()
    return kind+struct.pack(">I",len(data))+data


def _child_exact(fd,count):
    data=bytearray()
    while len(data)<count:
        chunk=os.read(fd,count-len(data))
        if not chunk: raise _invalid()
        data.extend(chunk)
    return bytes(data)


class _UploadReader:
    """SDK pull port. 최종 part는 부모의 전체 보존본 검증 E/EOF 뒤에만 반환한다."""
    def __init__(self,fd,work): self.fd,self.work=fd,work; self.total=0; self.digest=hashlib.sha256(); self.pending=b""; self.ended=False
    def _next(self):
        kind,size=struct.unpack(">cI",_child_exact(self.fd,5))
        if size>PAYLOAD or kind not in {b"D",b"E"} or ((kind==b"D")!=(size>0)): raise _invalid()
        if kind==b"E":
            if self.total!=self.work.size or self.digest.hexdigest()!=self.work.digest or os.read(self.fd,1)!=b"": raise _invalid()
            self.ended=True; return b""
        data=_child_exact(self.fd,size); self.total+=len(data); self.digest.update(data)
        if self.total>self.work.size: raise _invalid()
        return data
    def finish(self):
        if self.pending: raise _invalid()
        if not self.ended and self._next()!=b"": raise _invalid()
    def read(self,count):
        if type(count) is not int or not 0<=count<=PART+1: raise _invalid()
        data=bytearray()
        while len(data)<count:
            if not self.pending:
                if self.ended: break
                self.pending=self._next()
                if self.ended: break
            take=min(count-len(data),len(self.pending)); data.extend(self.pending[:take]); self.pending=self.pending[take:]
        if self.total==self.work.size and not self.pending: self.finish()
        return bytes(data)


class _RestorePool(PoolManager):
    """고정 두 key만. SDK가 error/redirect 본문을 읽기 전에 거부, control XML도 상한."""
    def __init__(self,source,remaining):
        super().__init__(maxsize=1,retries=False,cert_reqs=ssl.CERT_REQUIRED,ca_certs=source.ca_certs,
            timeout=Timeout(connect=max(.001,min(2.,remaining)),read=.25))
        self.allowed=set()
    def urlopen(self,method,url,**kwargs):
        parsed=urlsplit(url)
        if method not in {"GET","HEAD","PUT","POST"} or (parsed.scheme,parsed.netloc,parsed.path) not in self.allowed: raise _unavailable()
        from urllib.parse import parse_qsl
        if any(key not in {"uploads","uploadId","partNumber","versionId"} for key,_ in parse_qsl(parsed.query,keep_blank_values=True)): raise _unavailable()
        kwargs.update(retries=False,redirect=False,preload_content=False,decode_content=False)
        response=super().urlopen(method,url,**kwargs)
        if not 200<=response.status<300:
            try: response.close()
            finally: response.release_conn()
            raise _unavailable()
        if method in {"GET","HEAD"}: return response
        try:
            body=bytearray()
            while True:
                chunk=response.read1(min(4096,PAYLOAD+1-len(body)),decode_content=False)
                if not chunk: break
                body.extend(chunk)
                if len(body)>PAYLOAD: raise _unavailable()
            return HTTPResponse(body=bytes(body),headers=response.headers,status=response.status,preload_content=False,decode_content=False)
        finally:
            try: response.close()
            finally: response.release_conn()


def _stream_hash(client,work,key):
    response=client.get_object(work.source.bucket,key)
    try:
        total=0; digest=hashlib.sha256()
        while True:
            if time.monotonic()>=work.deadline: raise _unavailable()
            chunk=response.read1(min(PAYLOAD,max(1,work.size-total+1)),decode_content=False)
            if not isinstance(chunk,bytes) or len(chunk)>PAYLOAD: raise _invalid()
            if not chunk: break
            total+=len(chunk); digest.update(chunk)
            if total>work.size: raise _invalid()
        if total!=work.size or digest.hexdigest()!=work.digest: raise _invalid()
    finally:
        try: response.close()
        finally: response.release_conn()


def _perform_object_work(work,fd):
    source=work.source; remaining=work.deadline-time.monotonic()
    if remaining<=0 or work.mode not in {"upload","copy","readback"}: raise _unavailable()
    pool=_RestorePool(source,remaining)
    try:
        credentials=source.credentials
        client=Minio(source.endpoint,access_key=credentials.access_key,secret_key=credentials.secret_key,
            session_token=credentials.session_token,secure=source.secure,region=source.region,http_client=pool)
        for key in (work.staging_key,work.final_key):
            url=client._base_url.build(method="GET",region=source.region,bucket_name=source.bucket,object_name=key)
            pool.allowed.add((url.scheme,url.netloc,url.path))
        adapter=object.__new__(MinioObjectStorageAdapter); adapter._bucket=source.bucket; adapter._client=client
        if work.mode=="upload":
            reader=_UploadReader(fd,work)
            if work.size==0: reader.finish()
            client.put_object(source.bucket,work.staging_key,reader,work.size,work.content_type,
                metadata=adapter._metadata(work.digest,work.size,work.content_type),part_size=PART,num_parallel_uploads=1)
            reader.finish()
            stored=adapter._stat_verified(work.staging_key,expected_digest=work.digest,expected_size=work.size,content_type=work.content_type)
            _stream_hash(client,work,work.staging_key)
        elif work.mode=="copy":
            _stream_hash(client,work,work.staging_key)
            if os.write(fd,_frame(b"C"))!=5 or _child_exact(fd,5)!=_frame(b"G"): raise _unavailable()
            stored=adapter.promote(StagedObject(work.staging_key,work.digest,work.size,work.content_type,"",None),work.final_key,
                expected_digest=work.digest,expected_size=work.size,content_type=work.content_type)
            _stream_hash(client,work,work.final_key)
        else:
            stored=adapter._stat_verified(work.final_key,expected_digest=work.digest,expected_size=work.size,content_type=work.content_type)
            _stream_hash(client,work,work.final_key)
        etag=stored.etag
        if not re.fullmatch(r"[0-9a-f]{32}(?:-[0-9]+)?",etag): raise _invalid()
        version=stored.version_id
        if version is not None and (not isinstance(version,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",version)): raise _invalid()
        return _json({"size":work.size,"digest":work.digest,"etag":etag,"version":version})
    finally: pool.clear()


def _object_child(work,fd):
    code=1
    try:
        _close_inherited_fds({fd}); os.set_blocking(fd,True)
        result=_frame(b"R",_perform_object_work(work,fd))
        while result:
            written=os.write(fd,result)
            if written<=0: raise _unavailable()
            result=result[written:]
        code=0
    except BaseException:
        try: os.write(fd,_frame(b"X"))
        except BaseException: pass
    finally:
        try: os.close(fd)
        finally: os._exit(code)  # traceback/log/finalizer/평문 파일·argv/env 전달0


class _R3ForkIO:
    @staticmethod
    def supported():
        return os.name=="posix" and sys.platform.startswith("linux") and hasattr(os,"fork") and os.path.isdir("/proc/self/fd")
    @staticmethod
    def start(work):
        parent,child=socket.socketpair(); parent.setblocking(False); child.setblocking(False)
        try: pid=os.fork()
        except BaseException: parent.close(); child.close(); raise
        if pid==0:
            parent.close(); _object_child(work,child.detach())
        child.close(); return pid,parent.detach()
    @staticmethod
    def wait(fd,writing,timeout): return bool(select.select([] if writing else [fd],[fd] if writing else [],[],timeout)[1 if writing else 0])
    @staticmethod
    def send(fd,data): return os.write(fd,data)
    @staticmethod
    def receive(fd,count): return os.read(fd,count)
    @staticmethod
    def finish_write(fd):
        handle=socket.socket(fileno=fd)
        try: handle.shutdown(socket.SHUT_WR)
        finally: handle.detach()
    @staticmethod
    def poll(pid):
        found,status=os.waitpid(pid,os.WNOHANG)
        if found not in (0,pid): raise _unavailable()
        return found==pid,os.waitstatus_to_exitcode(status) if found else None
    @staticmethod
    def terminate(pid): os.kill(pid,signal.SIGTERM)
    @staticmethod
    def kill(pid): os.kill(pid,signal.SIGKILL)
    @staticmethod
    def close(fd): os.close(fd)
    @staticmethod
    def pause(duration): time.sleep(duration)


class _OwnedObjectCall:
    def __init__(self,transport,work,before_write):
        self.transport,self.work=transport,work; self.pid=self.fd=None; self.exited=False; self.status=None
        self.total=0; self.digest=hashlib.sha256()
        self.before_write=before_write; self.first_write=True
    def check(self):
        if self.transport.cancelled() or self.transport.clock()>=self.work.deadline: raise _unavailable()
    def start(self):
        self.check(); self.pid,self.fd=self.transport.io.start(self.work)
        if type(self.pid) is not int or self.pid<=1: raise _unavailable()
    def send_frame(self,kind,data=b""):
        value=memoryview(_frame(kind,data))
        while value:
            self.check()
            if not self.transport.io.wait(self.fd,True,.02): continue
            try: sent=self.transport.io.send(self.fd,value)
            except BlockingIOError: continue
            if type(sent) is not int or not 0<sent<=len(value): raise _invalid()
            value=value[sent:]
    def write(self,value):
        if not isinstance(value,bytes) or len(value)>4*1024*1024: raise _invalid()
        if self.first_write: self.before_write(); self.first_write=False
        self.total+=len(value); self.digest.update(value)
        if self.total>self.work.size: raise _invalid()
        for offset in range(0,len(value),PAYLOAD): self.send_frame(b"D",value[offset:offset+PAYLOAD])
        return len(value)
    def exact(self,count):
        data=bytearray()
        while len(data)<count:
            self.check()
            if not self.transport.io.wait(self.fd,False,.02): continue
            try: chunk=self.transport.io.receive(self.fd,count-len(data))
            except BlockingIOError: continue
            if not isinstance(chunk,bytes) or not chunk or len(chunk)>count-len(data): raise _invalid()
            data.extend(chunk)
        return bytes(data)
    def poll(self):
        if not self.exited: self.exited,self.status=self.transport.io.poll(self.pid)
        return self.exited
    def result(self):
        kind,size=struct.unpack(">cI",self.exact(5))
        if kind==b"C" and size==0 and self.work.mode=="copy":
            self.before_write(); self.send_frame(b"G")
            kind,size=struct.unpack(">cI",self.exact(5))
        if kind!=b"R" or not 0<size<=2048: raise _unavailable()
        raw=self.exact(size)
        # child의 EOF와 정상 reap까지 성공으로 반환하지 않는다.
        while True:
            self.check()
            if self.transport.io.wait(self.fd,False,.02):
                if self.transport.io.receive(self.fd,1)!=b"": raise _invalid()
                if self.poll():
                    if self.status!=0: raise _unavailable()
                    break
        try: result=json.loads(raw)
        except (ValueError,UnicodeError): raise _invalid() from None
        if (type(result) is not dict or set(result)!={"size","digest","etag","version"}
            or type(result["size"]) is not int or result["size"]!=self.work.size or result["digest"]!=self.work.digest
            or not isinstance(result["etag"],str) or not re.fullmatch(r"[0-9a-f]{32}(?:-[0-9]+)?",result["etag"])
            or (result["version"] is not None and (not isinstance(result["version"],str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",result["version"])))): raise _invalid()
        return result
    def cleanup(self):
        try:
            if self.pid is not None and not self.poll():
                for stop in (self.transport.io.terminate,self.transport.io.kill):
                    stop(self.pid)  # 직전 waitpid로 본인 unreaped child임이 확인된 PID만.
                    for _ in range(40):
                        if self.poll(): break
                        self.transport.io.pause(.005)
                    if self.exited: break
                if not self.exited: raise _unavailable()
        except BaseException as error:
            self.transport.uncertain=True; self.transport.owned_uncertain=self
            if not isinstance(error,Exception): raise
            raise _unavailable() from None
        finally:
            if self.fd is not None:
                try: self.transport.io.close(self.fd)
                except BaseException as error:
                    self.transport.uncertain=True; self.transport.owned_uncertain=self
                    if not isinstance(error,Exception): raise
                    raise _unavailable() from None
                finally: self.fd=None


class BoundedLineageObjects:
    def __init__(self,source,*,_io=None,_clock=time.monotonic,timeout=300.,cancelled=lambda:False):
        if type(source) is not _MinioSource or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=300: raise _unavailable()
        self.source,self.io,self.clock=source,_io or _R3ForkIO(),_clock
        self.timeout,self.cancelled=timeout,cancelled
        self.uncertain=False; self.owned_uncertain=None; self.guard=threading.Lock()
    @classmethod
    def from_storage(cls,storage,*,region,cancelled=lambda:False,timeout=300.):
        if not _R3ForkIO.supported(): raise _unavailable()
        streams=FixedSnapshotStreams.from_storage(storage,source_instance_ref="restore-target",region=region,runtime_files=())
        return cls(streams._source,timeout=timeout,cancelled=cancelled)
    def _run(self,mode,context,object_id,size,digest,content_type,producer=None,staged=None,final=None,before_write=lambda:None):
        if self.uncertain or not self.io.supported(): raise _unavailable()
        _object_budget([{"byte_size":size}]); _hex(digest)
        keys=ObjectKeyPolicy(); staging=keys.staging_key(context,"source",object_id); target=keys.final_key(context,"source",object_id)
        if staged is not None and staged!=staging or final is not None and final!=target: raise _invalid()
        if not isinstance(content_type,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-/]{0,254}",content_type): raise _invalid()
        if not self.guard.acquire(blocking=False): raise _unavailable()
        work=_ObjectWork(self.source,mode,staging,target,size,digest,content_type,self.clock()+self.timeout)
        call=_OwnedObjectCall(self,work,before_write)
        try:
            call.start()
            if mode=="upload":
                producer(call)
                if call.total!=size or call.digest.hexdigest()!=digest: raise _invalid()
                before_write()
                call.send_frame(b"E"); self.io.finish_write(call.fd)
            return call.result()
        except RecoveryError: raise
        except Exception: raise _unavailable() from None
        finally:
            try: call.cleanup()
            finally: self.guard.release()
    def upload(self,context,object_id,size,digest,content_type,producer,*,before_write=lambda:None):
        result=self._run("upload",context,object_id,size,digest,content_type,producer,before_write=before_write)
        return StagedObject(ObjectKeyPolicy().staging_key(context,"source",object_id),digest,size,content_type,result["etag"],result["version"])
    def verify(self,context,object_id,size,digest,content_type):
        return self._run("readback",context,object_id,size,digest,content_type)
    def promote(self,context,object_id,staged,final_key,*,expected_digest,expected_size,content_type,before_write=lambda:None):
        if (staged.digest_sha256,staged.byte_size,staged.content_type)!=(expected_digest,expected_size,content_type): raise _invalid()
        result=self._run("copy",context,object_id,expected_size,expected_digest,content_type,staged=staged.key,final=final_key,before_write=before_write)
        return StoredObject(final_key,expected_digest,expected_size,content_type,result["etag"],result["version"])


class _BoundedMinioAdapter(MinioObjectStorageAdapter):
    def __init__(self,streams,context,before_write=lambda:None):
        self.streams,self.context,self.before_write=streams,context,before_write; self._bucket=streams.source.bucket
    def promote(self,staged,final_key,*,expected_digest,expected_size,content_type):
        identity=final_key.rsplit("/",1)[-1]
        result=self.streams.promote(self.context,identity,staged,final_key,expected_digest=expected_digest,expected_size=expected_size,content_type=content_type,before_write=self.before_write)
        self.before_write()
        return result


def _checked_queue(store):
    # R3 소유의 얕은 facade만 변경한다. shared store/pool/설정/역할은 변경0.
    # 기존 정상 register/claim/complete/fail의 실제 transaction 안에서 읽기 검사.
    guarded=copy(store)
    transaction=store._transaction
    @contextmanager
    def app_transaction(context):
        with transaction(context) as connection:
            row=connection.execute("SELECT current_user,r.rolsuper,r.rolbypassrls FROM pg_catalog.pg_roles r "
                "WHERE r.rolname=current_user").fetchone()
            if row is None or tuple(row)!=("daon_app",False,False): raise _unavailable()
            yield connection
    guarded._transaction=app_transaction
    return guarded


class _RestoreObjectWorker(DurableObjectWorker):
    """정상 queue 계약 유지. copy 이후 complete/fail에 실제 현재 시각 사용."""
    def __init__(self,*args,clock,**kwargs):
        super().__init__(*args,**kwargs); self._clock=clock
    def process_claimed(self,context,job,*,now,jitter_unit=.5):
        record=self._store.get_object(context,job.object_id)
        staged=StagedObject(record.staging_key,record.digest_sha256,record.byte_size,record.content_type,"",None)
        try:
            stored=self._storage.promote(staged,record.object_key,expected_digest=record.digest_sha256,
                expected_size=record.byte_size,content_type=record.content_type)
            return self._store.complete(context,job,self._worker_id,stored,now=self._clock())
        except ObjectQueueError as error:
            finished=self._clock()
            delay=self._retry_policy.delay_seconds(job.attempt,jitter_unit=jitter_unit)
            return self._store.fail(context,job,self._worker_id,error,now=finished,
                retry_at=finished+timedelta(seconds=delay))


class MaterializationError(RecoveryError):
    def __init__(self, phase, outcome="uncertain"):
        super().__init__("RESOURCE_UNAVAILABLE", 503)
        self.phase, self.outcome = phase, outcome


@dataclass(frozen=True, slots=True, repr=False)
class RestoreBinding:
    request_id: str
    backup_id: str
    manifest_digest: str
    receipt_digest: str
    closure_fingerprint: str
    retention_watermark: str
    preview_version: int
    included_roots: tuple[str, ...]
    registry_generation_id: str
    destination_id: str
    destination_version: int
    owner_operation_id: str
    operation_id: str
    idempotency_key: str
    preview_expires_at: datetime
    catalog_digest: str
    current_retention_watermark: str

    def __post_init__(self):
        for name in ("request_id", "backup_id", "registry_generation_id", "destination_id", "owner_operation_id", "operation_id", "idempotency_key"):
            _safe(getattr(self, name))
        for name in ("manifest_digest", "receipt_digest", "closure_fingerprint", "retention_watermark", "catalog_digest", "current_retention_watermark"): _hex(getattr(self, name))
        if any(type(getattr(self, name)) is not int or getattr(self, name) <= 0 for name in ("preview_version", "destination_version")): raise _invalid()
        if (not isinstance(self.included_roots, tuple) or not self.included_roots
                or self.included_roots != tuple(sorted(set(self.included_roots)))): raise _invalid()
        for identity in self.included_roots: _safe(identity)
        if self.preview_expires_at.tzinfo is None: raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class CurrentRestoreState:
    context: RecoveryContext
    binding: RestoreBinding
    current_retention_watermark: str


@dataclass(frozen=True, slots=True, repr=False)
class RestorePlan:
    metadata: dict
    digest: str


@dataclass(frozen=True, slots=True, repr=False)
class MaterializationResult:
    last_phase: int
    state: str
    target_digest: str
    provenance_digest: str
    # 원본 전체 감사/사용자 로그인/모델·색인 준비 완료를 의미하지 않는다.


def build_restore_plan(scope, metadata, included_roots=None):
    if type(scope) is not LineageCaptureScope: raise _invalid()
    checked, _ = _metadata(scope, _json(metadata))
    roots = tuple(included_roots or scope.root_source_version_ids)
    if not roots or set(roots)-set(scope.root_source_version_ids): raise _invalid()
    versions = {row["record_id"]: row for row in checked["source_versions"]}; needed = set()
    def visit(identity):
        if identity in needed: return
        row = versions[identity]
        if row["previous_version_id"] is not None: visit(row["previous_version_id"])
        needed.add(identity)
    for root in roots: visit(root)
    source_ids = {versions[k]["source_id"] for k in needed}; object_ids = {versions[k]["object_id"] for k in needed}
    result = {key: list(rows) for key, rows in checked.items()}
    for table, field, allowed in (("source_versions", "record_id", needed), ("sources", "record_id", source_ids),
            ("object_records", "object_id", object_ids), ("canon_state_transitions", "record_id", source_ids),
            ("canon_transition_attempts", "record_id", source_ids), ("notebook_bindings", "version_id", set(roots))):
        result[table] = [row for row in result[table] if row[field] in allowed]
    for row in result["sources"]:
        if row["previous_version_id"] is not None: raise _invalid()
    # 실제 coordinator/key 정책의 ID 계약. 임의 ID 변환/원 ID 대체는 하지 않는다.
    if any(not re.fullmatch(r"[0-9a-f]{32}", row["object_id"]) for row in result["object_records"]): raise _unavailable()
    _object_budget(result["object_records"])
    return RestorePlan(result, _hash(result))


def _scope(connection, context, *, role=False):
    if role: connection.execute("SET LOCAL ROLE daon_app")
    for key, value in (("app.tenant_id", context.tenant_id), ("app.workspace_id", context.workspace_id),
                       ("app.actor_id", context.actor_id), ("app.capability", "object.write")):
        connection.execute("SELECT set_config(%s,%s,true)", (key, value))


def _rows(connection, context, table, columns):
    # table/columns 모두 모듈 내부 allowlist. 입력 SQL/path를 받지 않는다.
    projection = ",".join(f"'{column}',r.{column}" for column in columns)
    return [row[0] for row in connection.execute(f"SELECT jsonb_build_object({projection}) FROM public.{table} r "
        "WHERE r.tenant_id=%s AND r.workspace_id=%s", (context.tenant_id, context.workspace_id))]


def _insert(connection, table, columns, row):
    values = tuple(Jsonb(row[key]) if key == "canonical_json" else row[key] for key in columns)
    connection.execute(f"INSERT INTO public.{table} ({','.join(columns)}) VALUES ({','.join(['%s']*len(columns))})", values)


class PostgresPhaseJournal:
    """기존 source connection factory만. 새 phase 원장 외 원본 자료 쓰기0."""
    def __init__(self, connection_provider): self._connections = connection_provider

    @contextmanager
    def _transaction(self, context, binding, destination=None, scope=None):
        with self._connections() as connection, connection.transaction():
            _scope(connection, context)
            row = connection.execute("SELECT backup_id,actor_id FROM public.restore_requests "
                "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s FOR UPDATE",
                (context.tenant_id, context.workspace_id, binding.request_id)).fetchone()
            if row is None or tuple(row) != (binding.backup_id, context.actor_id): raise _invalid()
            row = connection.execute("SELECT p.registry_generation_id,p.destination_id,p.destination_version,p.source_manifest_digest,"
                "p.closure_fingerprint,p.retention_watermark,p.expires_at,r.owner_operation_id,"
                "r.physical_receipt_digest,p.included_roots_fingerprint,p.excluded_roots_fingerprint,"
                "r.target_database_fingerprint,r.target_bucket_fingerprint FROM public.registered_restore_previews p "
                "JOIN public.registered_restore_target_reservations r USING(tenant_id,workspace_id,request_id) "
                "JOIN public.backup_manifests m ON m.tenant_id=p.tenant_id AND m.workspace_id=p.workspace_id "
                "AND m.backup_id=p.backup_id AND m.manifest_version=p.manifest_version "
                "WHERE p.tenant_id=%s AND p.workspace_id=%s AND p.request_id=%s AND p.preview_version=%s "
                "AND p.actor_id=%s AND p.manifest_version=3 AND m.manifest_digest=p.source_manifest_digest",
                (context.tenant_id, context.workspace_id, binding.request_id, binding.preview_version, context.actor_id)).fetchone()
            expected = (binding.registry_generation_id,binding.destination_id,binding.destination_version,binding.manifest_digest,
                binding.closure_fingerprint,binding.retention_watermark,binding.preview_expires_at,binding.owner_operation_id)
            if row is None or tuple(row[:8]) != expected: raise _invalid()
            if destination is not None:
                excluded=sorted(set(scope.root_source_version_ids)-set(binding.included_roots))
                if tuple(row[8:]) != (destination.physical_receipt_digest,_hash(list(binding.included_roots)),_hash(excluded),
                        destination.target_database_fingerprint,destination.target_bucket_fingerprint): raise _invalid()
            yield connection

    def load(self, context, binding, destination, scope):
        with self._transaction(context, binding, destination, scope) as connection:
            return list(connection.execute("SELECT sequence,phase,outcome,target_digest,result_digest,operation_id,idempotency_key,trace_id "
                "FROM public.registered_restore_phase_receipts WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s ORDER BY sequence",
                (context.tenant_id, context.workspace_id, binding.request_id)))

    def append(self, context, binding, phase, outcome, target_digest, result_digest=None):
        with self._transaction(context, binding) as connection:
            row = connection.execute("SELECT coalesce(max(sequence),0)+1 FROM public.registered_restore_phase_receipts "
                "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                (context.tenant_id, context.workspace_id, binding.request_id)).fetchone()
            sequence = row[0]
            connection.execute("INSERT INTO public.registered_restore_phase_receipts "
                "(tenant_id,workspace_id,request_id,sequence,actor_id,registry_generation_id,destination_id,destination_version,"
                "preview_version,operation_id,idempotency_key,phase,outcome,target_digest,result_digest,trace_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (context.tenant_id,context.workspace_id,binding.request_id,sequence,context.actor_id,binding.registry_generation_id,
                binding.destination_id,binding.destination_version,binding.preview_version,binding.operation_id,binding.idempotency_key,
                phase,outcome,target_digest,result_digest,context.trace_id))


class PostgresLineageTarget:
    def __init__(self, connection_provider, queue_store: PostgresObjectQueueStore, storage: MinioObjectStorageAdapter,
                 *, probe: TrustedPhysicalProbe, probe_request, clock=None, object_streams=None, region=None,
                 cancelled=lambda:False):
        if not isinstance(queue_store, PostgresObjectQueueStore) or not isinstance(storage, MinioObjectStorageAdapter): raise _unavailable()
        if not isinstance(probe, TrustedPhysicalProbe): raise _unavailable()
        self._connections, self.queue, self.storage = connection_provider, _checked_queue(queue_store), storage
        self.original_storage=storage; self.streams=object_streams; self.region=region; self.cancelled=cancelled
        if object_streams is not None and type(object_streams) is not BoundedLineageObjects: raise _unavailable()
        self.probe, self.probe_request = probe, probe_request
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def transaction(self, context):
        with self._connections() as connection, connection.transaction():
            _scope(connection, context, role=True)
            yield connection

    @staticmethod
    def _global_counts(connection,context):
        # Foundation/인증 설정 표를 포함하지 않는다. 이 8개 자료 표에만 전역 가시성 필요.
        try:
            role=connection.execute("SELECT current_user,r.rolsuper,r.rolbypassrls FROM pg_catalog.pg_roles r "
                "WHERE r.rolname=current_user").fetchone()
            if role is None or not (role[1] is True or role[2] is True): raise _unavailable()
            counts={}
            for table in _RLS_TABLES:
                row=connection.execute(f"SELECT count(*),count(*) FILTER (WHERE tenant_id IS DISTINCT FROM %s "
                    f"OR workspace_id IS DISTINCT FROM %s) FROM public.{table}",
                    (context.tenant_id,context.workspace_id)).fetchone()
                if row is None or len(row)!=2 or any(type(n) is not int or n<0 for n in row): raise _unavailable()
                if row[1]!=0: raise _invalid()
                counts[table]=row[0]
            return counts
        except RecoveryError: raise
        except Exception: raise _unavailable() from None

    def check(self, context, destination, binding, *, fresh):
        if self.streams is None:
            try: self.streams=BoundedLineageObjects.from_storage(self.original_storage,region=self.region,cancelled=self.cancelled)
            except Exception: raise _unavailable() from None
        if not self.streams.io.supported() or self.streams.uncertain: raise _unavailable()
        if self.streams.source.bucket!=self.original_storage._bucket: raise _invalid()
        self.storage=_BoundedMinioAdapter(self.streams,CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,"object.write"))
        observed = self.probe.observe(self.probe_request)
        if (physical_receipt_digest(observed) != destination.physical_receipt_digest
            or observed.owner_operation_id != binding.owner_operation_id or observed.application_enabled is not False
            or observed.outbound_enabled is not False or observed.request != self.probe_request
            or not 0 <= (self.clock()-observed.observed_at).total_seconds() <= 30
            or self.storage._bucket != observed.target_object.bucket): raise _invalid()
        if fresh and (observed.database_empty is not True or observed.bucket_empty is not True): raise _invalid()
        cloud=CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,"object.write")
        with self.queue._transaction(cloud): pass  # 외부 첫 upload 전 잘못된 queue builder도 거부.
        with self._connections() as connection, connection.transaction():
            counts=self._global_counts(connection,context)
            if fresh and any(counts.values()): raise _invalid()
            identity = connection.execute("SELECT c.system_identifier::text,d.oid::bigint FROM pg_catalog.pg_control_system() c "
                "JOIN pg_catalog.pg_database d ON d.datname=current_database()").fetchone()
            if identity is None or DatabaseIdentity(str(identity[0]), identity[1]).fingerprint() != destination.target_database_fingerprint: raise _invalid()
            catalog = list(connection.execute("SELECT c.relname,pg_catalog.pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity,"
                "(SELECT jsonb_agg(jsonb_build_array(CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,"
                "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) ORDER BY "
                "CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,"
                "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) "
                "FROM pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a),"
                "(SELECT jsonb_agg(pg_catalog.pg_get_constraintdef(x.oid,true) ORDER BY x.conname) FROM pg_catalog.pg_constraint x WHERE x.conrelid=c.oid),"
                "(SELECT jsonb_agg(pg_catalog.pg_get_triggerdef(t.oid,true) ORDER BY t.tgname) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),"
                "(SELECT jsonb_agg(jsonb_build_array(p.polname,p.polcmd,p.polpermissive,"
                "(SELECT jsonb_agg(role_name ORDER BY role_name) FROM (SELECT CASE WHEN role_id=0 THEN 'PUBLIC' "
                "ELSE (SELECT r.rolname FROM pg_catalog.pg_roles r WHERE r.oid=role_id) END AS role_name FROM unnest(p.polroles) role_id) named_roles),"
                "pg_catalog.pg_get_expr(p.polqual,p.polrelid),"
                "pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)) ORDER BY p.polname) FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid) "
                "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname",
                (list((*_TABLES,"object_records","canon_state_transitions","canon_transition_attempts")),)))
            if len(catalog) != 8 or any(row[2] is not True or row[3] is not True for row in catalog): raise _unavailable()
            functions = list(connection.execute("SELECT p.proname,pg_catalog.pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,"
                "pg_catalog.pg_get_function_identity_arguments(p.oid),"
                "(SELECT jsonb_agg(jsonb_build_array(CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,"
                "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) ORDER BY "
                "CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,"
                "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) "
                "FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a),"
                "pg_catalog.pg_get_functiondef(p.oid) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname IN ('transition_canon_state','validate_canon_insert','guard_canon_state_mutation') "
                "ORDER BY p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid)"))
            if ({row[0] for row in functions} != {"transition_canon_state","validate_canon_insert","guard_canon_state_mutation"}
                    or _hash([catalog,functions]) != binding.catalog_digest): raise _unavailable()
        return observed

    def foundation(self, context):
        with self.transaction(context) as connection:
            row = connection.execute("SELECT w.tenant_id,w.workspace_id,a.user_id FROM public.workspaces w "
                "JOIN public.tenants t ON t.tenant_id=w.tenant_id JOIN public.user_accounts a ON a.tenant_id=w.tenant_id "
                "WHERE w.tenant_id=%s AND w.workspace_id=%s AND a.user_id=%s AND a.status='active'",
                (context.tenant_id,context.workspace_id,context.actor_id)).fetchone()
            if row is None or tuple(row) != (context.tenant_id,context.workspace_id,context.actor_id): raise _unavailable()

    def insert_phase(self, context, binding, plan, phase):
        meta = plan.metadata
        with self.transaction(context) as connection:
            if phase == 3:
                for row in meta["sources"]:
                    base = {**row,"version":1,"state":"registered","previous_version_id":None}
                    _insert(connection,"sources",_COLUMNS["sources"],base)
                    state, version = "registered", 1
                    for event in sorted((e for e in meta["canon_state_transitions"] if e["record_id"]==row["record_id"]),key=lambda e:e["transition_version"]):
                        if not transition_allowed("Source",state,event["target_state"]): raise _invalid()
                        transition = "restore-replay-"+_hash([binding.request_id,row["record_id"],event["transition_id"]])[:32]
                        result = connection.execute("SELECT state,version,outcome,error_code FROM public.transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)",
                            ("Source",row["record_id"],version,event["target_state"],transition,"RESTORE_REPLAY",context.trace_id,context.policy_version)).fetchone()
                        if result is None or tuple(result) != (event["target_state"],version+1,"succeeded",None): raise _invalid()
                        state, version = result[0],result[1]
                    if (state,version)!=(row["state"],row["version"]): raise _invalid()
            else:
                for table in ({4:("source_versions",),5:("notebooks","notebook_metadata_versions"),6:("notebook_bindings",)}[phase]):
                    for row in meta[table]: _insert(connection,table,_COLUMNS[table],row)

    def objects(self, context, binding, plan, store, scope, receipt, *, before_write):
        cloud = CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,"object.write")
        worker_id = "restore-"+_hash([binding.request_id,binding.operation_id])[:32]
        policy = RetryPolicy(max_attempts=1)
        lease=math.ceil(self.streams.timeout)+30  # copy 총 deadline(최대300초)+유한 DB 확정 여유.
        worker = _RestoreObjectWorker(self.queue,_BoundedMinioAdapter(self.streams,cloud,before_write),worker_id,
            retry_policy=policy,lease_seconds=lease,clock=self.clock)
        for row in plan.metadata["object_records"]:
            staged=self.streams.upload(cloud,row["object_id"],row["byte_size"],row["digest_sha256"],row["content_type"],
                lambda sink:store.read_object(scope,row["object_id"],sink,expected_receipt=receipt),before_write=before_write)
            before_write()
            submission = self.queue.register_object_job(cloud,object_id=row["object_id"],area="source",staged=staged,
                final_key=ObjectKeyPolicy().final_key(cloud,"source",row["object_id"]),
                idempotency_key="restore-object-"+_hash([binding.request_id,row["object_id"]])[:32],trace_id=context.trace_id,max_attempts=1)
            job = self.queue.claim(cloud,worker_id,now=self.clock(),lease_seconds=lease)
            if job is None or job.object_id!=row["object_id"] or job.job_id!=submission.job_id: raise _invalid()
            done = worker.process_claimed(cloud,job,now=self.clock())
            if done.state!="completed": raise _unavailable()
            self.verify_objects(context,plan,object_id=row["object_id"])

    def verify_objects(self,context,plan,object_id=None):
        cloud = CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,"object.write")
        for row in plan.metadata["object_records"]:
            if object_id is not None and row["object_id"]!=object_id: continue
            record = self.queue.get_object(cloud,row["object_id"])
            expected_key=ObjectKeyPolicy().final_key(cloud,"source",row["object_id"])
            if (record.status,record.object_id,record.digest_sha256,record.byte_size,record.object_key)!=("completed",row["object_id"],row["digest_sha256"],row["byte_size"],expected_key): raise _invalid()
            self.streams.verify(cloud,row["object_id"],row["byte_size"],row["digest_sha256"],row["content_type"])

    def audit_phase(self,context,binding,phase,plan):
        # 지금 발생한 복원 사건. 원 transition의 actor/시각을 새 감사로 INSERT하지 않는다.
        with self.transaction(context) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("restore-audit|"+context.tenant_id+"|"+context.workspace_id,))
            previous=connection.execute("SELECT metadata->>'event_hash' FROM public.audit_events WHERE tenant_id=%s AND workspace_id=%s AND action=%s AND target_id=%s ORDER BY sequence DESC LIMIT 1",(context.tenant_id,context.workspace_id,"restore.materialize.phase",binding.request_id)).fetchone()
            previous="0"*64 if previous is None or previous[0] is None else _hex(previous[0])
            event_id="restore-phase-"+_hash([binding.request_id,binding.operation_id,phase])[:32]
            now=self.clock(); action="restore.materialize.phase"
            digest=_fingerprint((previous,event_id,context.tenant_id,context.workspace_id,context.actor_id,action,binding.request_id,context.trace_id,context.policy_version,now.isoformat()))
            metadata={"previous_hash":previous,"event_hash":digest,"phase":phase,"plan_digest":plan.digest,
                "provenance_digest":_hash([plan.metadata["canon_state_transitions"],plan.metadata["canon_transition_attempts"]])}
            connection.execute("INSERT INTO public.audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,metadata,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (event_id,context.tenant_id,context.workspace_id,context.actor_id,action,"restore_request",binding.request_id,"succeeded",context.trace_id,context.policy_version,Jsonb(metadata),now))

    def verify_phase(self,context,binding,plan,phase):
        expected={table:len(plan.metadata[table]) if phase>=minimum else 0 for table,minimum in
            (("sources",3),("source_versions",4),("notebooks",5),("notebook_metadata_versions",5),
             ("notebook_bindings",6),("object_records",2),("canon_state_transitions",3),("canon_transition_attempts",3))}
        with self._connections() as connection,connection.transaction():
            if self._global_counts(connection,context)!=expected: raise _invalid()
        if phase>=1: self.foundation(context)
        if phase>=2: self.verify_objects(context,plan)
        with self.transaction(context) as connection:
            for table,minimum in (("sources",3),("source_versions",4),("notebooks",5),("notebook_metadata_versions",5),("notebook_bindings",6)):
                actual=_rows(connection,context,table,_COLUMNS[table]); expected=plan.metadata[table] if phase>=minimum else []
                key=lambda row: tuple(row[k] for k in _KEYS[table])
                if sorted(actual,key=key)!=sorted(expected,key=key): raise _invalid()
            events=_rows(connection,context,"canon_state_transitions",_COLUMNS["canon_state_transitions"])
            attempts=_rows(connection,context,"canon_transition_attempts",_COLUMNS["canon_transition_attempts"])
            originals=plan.metadata["canon_state_transitions"] if phase>=3 else []
            if len(events)!=len(originals) or len(attempts)!=len(originals): raise _invalid()
            actual_events={row["transition_id"]:row for row in events}; actual_attempts={row["attempt_id"]:row for row in attempts}
            for original in originals:
                event="restore-replay-"+_hash([binding.request_id,original["record_id"],original["transition_id"]])[:32]
                actual=actual_events.get(event); attempt=actual_attempts.get(event)
                required={"tenant_id":context.tenant_id,"workspace_id":context.workspace_id,"entity_type":"Source",
                    "record_id":original["record_id"],"source_state":original["source_state"],"target_state":original["target_state"],
                    "actor_id":context.actor_id,"trace_id":context.trace_id,"policy_version":context.policy_version,"reason_code":"RESTORE_REPLAY"}
                if (actual is None or attempt is None or any(actual.get(k)!=v or attempt.get(k)!=v for k,v in required.items())
                    or actual["transition_version"]!=original["transition_version"]
                    or (attempt["expected_version"],attempt["current_version"],attempt["result_version"],attempt["result_state"],attempt["outcome"],attempt["safe_error_code"])
                        !=(original["transition_version"]-1,original["transition_version"]-1,original["transition_version"],original["target_state"],"succeeded",None)):
                    raise _invalid()
            audit_rows=connection.execute("SELECT event_id,actor_id,trace_id,policy_version,action,target_id,metadata,occurred_at FROM public.audit_events WHERE tenant_id=%s AND workspace_id=%s AND action=%s AND target_id=%s ORDER BY sequence",
                (context.tenant_id,context.workspace_id,"restore.materialize.phase",binding.request_id)).fetchall()
            if len(audit_rows)!=phase: raise _invalid()
            previous="0"*64
            for index,row in enumerate(audit_rows,1):
                event,actor,trace,policy,action,target,metadata,occurred=row
                expected_event="restore-phase-"+_hash([binding.request_id,binding.operation_id,index])[:32]
                if (event,actor,trace,policy)!=(expected_event,context.actor_id,context.trace_id,context.policy_version): raise _invalid()
                digest=_fingerprint((metadata["previous_hash"],event,context.tenant_id,context.workspace_id,actor,action,target,trace,policy,occurred.isoformat()))
                if (metadata["event_hash"]!=digest or metadata["previous_hash"]!=previous or metadata["plan_digest"]!=plan.digest
                    or metadata["phase"]!=index or metadata["provenance_digest"]!=_hash([plan.metadata["canon_state_transitions"],plan.metadata["canon_transition_attempts"]])): raise _invalid()
                previous=digest
        return _hash([plan.digest,phase])


def materialize_lineage(context,scope,store,receipt,binding,destination,target,journal,*,recheck,clock=None):
    now=clock or (lambda:datetime.now(timezone.utc))
    def current():
        result=recheck()
        if (type(result) is not CurrentRestoreState or result.context!=context or result.binding!=binding
            or result.current_retention_watermark!=binding.current_retention_watermark): raise _invalid()
        return result
    if (type(context) is not RecoveryContext or type(scope) is not LineageCaptureScope or not isinstance(store,FileLineageCaptureStore)
        or type(binding) is not RestoreBinding or type(destination) is not RegisteredDestination
        or not isinstance(target,PostgresLineageTarget) or not isinstance(journal,PostgresPhaseJournal)): raise _invalid()
    if ((context.tenant_id,context.workspace_id)!=(scope.tenant_id,scope.workspace_id)
        or (destination.tenant_id,destination.workspace_id,destination.actor_id)!=(context.tenant_id,context.workspace_id,context.actor_id)
        or (binding.registry_generation_id,binding.destination_id,binding.destination_version,binding.owner_operation_id)
          !=(destination.registry_generation_id,destination.destination_id,destination.version,destination.owner_operation_id)
        or binding.closure_fingerprint!=scope.closure_fingerprint or binding.retention_watermark!=scope.retention_watermark
        or binding.receipt_digest!=_hash(receipt)): raise _invalid()
    current()
    verified=store.verify(scope,expected_receipt=receipt)
    plan=build_restore_plan(scope,verified.metadata,binding.included_roots)
    current()  # 무거운 파일/암호 검증 직후 첫 외부쓰기 전 현재 주체·선택·등록 재검사.
    history=journal.load(context,binding,destination,scope); last=0
    execution=context
    if history:
        _safe(history[0][7]); execution=replace(context,trace_id=history[0][7])
    for index,row in enumerate(history):
        sequence,phase,outcome,target_digest,result_digest,operation,key,original_trace=row
        expected_phase=index//2+1; expected_outcome="intent" if index%2==0 else "succeeded"
        if (sequence!=index+1 or phase!=expected_phase or outcome!=expected_outcome or target_digest!=plan.digest
                or operation!=binding.operation_id or key!=binding.idempotency_key or original_trace!=execution.trace_id): raise MaterializationError(phase)
        if outcome=="succeeded":
            if result_digest!=_hash([plan.digest,phase]): raise MaterializationError(phase)
            last=phase
    if len(history)%2: raise MaterializationError(history[-1][1])
    target.check(context,destination,binding,fresh=not history)
    if last:
        target.verify_phase(execution,binding,plan,last)  # 현재 auth와 원실행 trace를 분리해 실제 내용 대조.
    def boundary():
        current()
        if binding.preview_expires_at<=now() or datetime.fromisoformat(destination.expires_at.replace("Z","+00:00"))<=now(): raise _invalid()
        target.check(context,destination,binding,fresh=False)
    for phase in range(last+1,8):
        if binding.preview_expires_at<=now() or datetime.fromisoformat(destination.expires_at.replace("Z","+00:00"))<=now(): raise _invalid()
        current(); target.check(context,destination,binding,fresh=not history and phase==1)
        journal.append(execution,binding,phase,"intent",plan.digest)
        try:
            if phase==1: target.foundation(execution)
            elif phase==2: target.objects(execution,binding,plan,store,scope,receipt,before_write=boundary)
            elif phase<7: target.insert_phase(execution,binding,plan,phase)
            boundary(); target.audit_phase(execution,binding,phase,plan)
            digest=target.verify_phase(execution,binding,plan,phase)
            boundary()
        except BaseException as error:
            try: journal.append(execution,binding,phase,"uncertain",plan.digest,_hash([plan.digest,phase,"uncertain"]))
            except BaseException: pass  # 추가 기록 실패는 intent 보존. 자동 재송신/삭제0.
            if not isinstance(error,Exception): raise
            raise MaterializationError(phase) from None
        try: journal.append(execution,binding,phase,"succeeded",plan.digest,digest)
        except Exception: raise MaterializationError(phase) from None
    current(); target.check(context,destination,binding,fresh=False)
    return MaterializationResult(7,"materialized",plan.digest,_hash([plan.metadata["canon_state_transitions"],plan.metadata["canon_transition_attempts"]]))
