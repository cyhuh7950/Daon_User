"""B2-C Linux 전용 고정 Object/runtime worker. 공개 API/범용 실행기 아님.

parent는 nonblocking pipe의 상한 frame만 소비한다. DNS/open/read/close는 스트림별
소유 child 안에서만 수행한다. 실제 fork/MinIO/POSIX 검증은 별도 실행 증거가 필요하다.
"""
from __future__ import annotations

import math
import os
import select
import signal
import ssl
import struct
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlunsplit

from minio import Minio
from minio.credentials import StaticProvider
from urllib3 import PoolManager
from urllib3.util import Timeout

from .cloud_storage import CloudAccessContext
from .object_queue import MinioObjectStorageAdapter, ObjectKeyPolicy
from .recovery_capture import _PosixFiles
from .service_snapshot import (BoundedSnapshotReader, CHUNK, SnapshotError, SnapshotObjectRef,
    SnapshotRuntimeRef, _safe)


FRAME = 65536
PAYLOAD = FRAME - 5
_CLEANUP = "SNAPSHOT_WORKER_CLEANUP_UNCERTAIN"


@dataclass(frozen=True, repr=False)
class FixedRuntimeFile:
    ref: SnapshotRuntimeRef
    path: Path

    def __post_init__(self):
        if (not isinstance(self.ref, SnapshotRuntimeRef) or self.ref.mode != "bytes" or not isinstance(self.path, Path)
                or not self.path.is_absolute() or ".." in self.path.parts): raise SnapshotError()


@dataclass(frozen=True, repr=False)
class _MinioSource:
    endpoint: str
    bucket: str
    region: str
    secure: bool
    credentials: object
    ca_certs: str | None


@dataclass(frozen=True, repr=False)
class _ObjectWork:
    source: _MinioSource
    ref: SnapshotObjectRef
    key: str
    deadline: float


@dataclass(frozen=True, repr=False)
class _RuntimeWork:
    file: FixedRuntimeFile
    deadline: float


class _GuardedPool(PoolManager):
    """SDK의 오류 XML 전체 읽기/region·redirect·retry 전에 응답을 거부한다."""
    def __init__(self, source, remaining):
        super().__init__(maxsize=1, retries=False, cert_reqs=ssl.CERT_REQUIRED, ca_certs=source.ca_certs,
            timeout=Timeout(connect=max(.001, min(2., remaining)), read=.25))
        self.allowed_url = None

    def urlopen(self, method, url, **kwargs):
        if method != "GET" or url != self.allowed_url: raise SnapshotError("SNAPSHOT_OBJECT_UNAVAILABLE")
        kwargs.update(retries=False, redirect=False, preload_content=False, decode_content=False)
        response = super().urlopen(method, url, **kwargs)
        if response.status != 200:
            try: response.close()
            finally: response.release_conn()
            raise SnapshotError("SNAPSHOT_OBJECT_UNAVAILABLE")
        return response


def _check_deadline(deadline):
    if time.monotonic() >= deadline: raise SnapshotError("SNAPSHOT_TIMEOUT")


@contextmanager
def _minio_chunks(work):
    _check_deadline(work.deadline)
    source = work.source
    pool, response = _GuardedPool(source, work.deadline - time.monotonic()), None
    try:
        # 새 pool/SDK만 child에서 만든다. 기존 shared client/trace/provider 설정은 변경0.
        credentials = source.credentials
        client = Minio(source.endpoint, access_key=credentials.access_key, secret_key=credentials.secret_key,
            session_token=credentials.session_token, secure=source.secure, region=source.region, http_client=pool)
        pool.allowed_url = urlunsplit(client._base_url.build(method="GET", region=source.region,
            bucket_name=source.bucket, object_name=work.key))
        response = client.get_object(source.bucket, work.key)
        def chunks():
            total = 0
            while True:
                _check_deadline(work.deadline)
                value = response.read1(min(PAYLOAD, max(1, work.ref.byte_size - total + 1)), decode_content=False)
                if not isinstance(value, bytes) or len(value) > PAYLOAD: raise SnapshotError()
                if not value: break
                total += len(value)
                if total > work.ref.byte_size: raise SnapshotError()
                yield value
        yield chunks()
    finally:
        try:
            if response is not None:
                try: response.close()
                finally: response.release_conn()
        except Exception: raise SnapshotError(_CLEANUP) from None
        finally: pool.clear()


@contextmanager
def _runtime_chunks(work):
    _check_deadline(work.deadline)
    files = _PosixFiles()
    # pin/open/NOFOLLOW/regular/nlink=1 검사는 모두 child 안에서 수행한다.
    with files.pin(work.file.path.parent), files.file(work.file.path) as source:
        info = os.fstat(source.fileno())
        if info.st_size != work.file.ref.byte_size: raise SnapshotError("SNAPSHOT_RUNTIME_UNAVAILABLE")
        def chunks():
            total = 0
            while True:
                _check_deadline(work.deadline)
                value = source.read(min(PAYLOAD, max(1, work.file.ref.byte_size - total + 1)))
                if not isinstance(value, bytes) or len(value) > PAYLOAD: raise SnapshotError()
                if not value: break
                total += len(value)
                if total > work.file.ref.byte_size: raise SnapshotError()
                yield value
        yield chunks()


def _close_inherited_fds(keep):
    # /proc 보장이 없는 OS는 start 전에 거부한다. parent PG/socket/출력 FD는 상속 사용0.
    for name in os.listdir("/proc/self/fd"):
        if not name.isdecimal(): raise SnapshotError()
        fd = int(name)
        if fd not in keep:
            try: os.close(fd)
            except OSError as error:
                if error.errno != 9: raise


def _send(fd, kind, data=b""):
    if len(data) > PAYLOAD or kind not in {b"D", b"E", b"X", b"U"}: raise SnapshotError()
    frame = memoryview(kind + struct.pack(">I", len(data)) + data)
    while frame:
        sent = os.write(fd, frame)
        if sent <= 0: raise SnapshotError()
        frame = frame[sent:]


def _worker(work, fd):
    code = 1
    try:
        _close_inherited_fds({fd})
        os.set_blocking(fd, True)
        if type(work) is _ObjectWork: source = _minio_chunks(work)
        elif type(work) is _RuntimeWork: source = _runtime_chunks(work)
        else: raise SnapshotError()
        with source as chunks:
            for chunk in chunks: _send(fd, b"D", chunk)
        # transport/file cleanup 성공 후에만 terminal frame을 발행한다.
        _send(fd, b"E"); code = 0
    except BaseException as error:
        try: _send(fd, b"U" if isinstance(error, SnapshotError) and error.code.endswith("CLEANUP_UNCERTAIN") else b"X")
        except BaseException: pass
    finally:
        try: os.close(fd)
        finally: os._exit(code)  # traceback/log/finalizer/상속된 평문 buffer flush0


class _ForkIO:
    @staticmethod
    def supported():
        return (os.name == "posix" and sys.platform.startswith("linux") and hasattr(os, "fork")
            and hasattr(os, "pipe2") and os.path.isdir("/proc/self/fd"))

    @staticmethod
    def start(work):
        read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        try: pid = os.fork()
        except BaseException:
            os.close(read_fd); os.close(write_fd); raise
        if pid == 0: _worker(work, write_fd)
        os.close(write_fd)
        return pid, read_fd

    @staticmethod
    def readable(fd, timeout): return bool(select.select([fd], [], [], timeout)[0])
    @staticmethod
    def receive(fd, count): return os.read(fd, count)  # O_NONBLOCK 유지, recv/pickle 없음
    @staticmethod
    def poll(pid):
        found, status = os.waitpid(pid, os.WNOHANG)
        if found not in (0, pid): raise SnapshotError(_CLEANUP)
        return found == pid, os.waitstatus_to_exitcode(status) if found else None
    @staticmethod
    def terminate(pid): os.kill(pid, signal.SIGTERM)
    @staticmethod
    def kill(pid): os.kill(pid, signal.SIGKILL)
    @staticmethod
    def close(fd): os.close(fd)
    @staticmethod
    def pause(duration): time.sleep(duration)


class _OwnedReader(BoundedSnapshotReader):
    def __init__(self, work, io, clock, deadline, cancelled):
        self.work, self.io, self.clock = work, io, clock
        self.deadline, self.cancelled = deadline, cancelled
        self.pid = self.fd = None
        self.exited, self.status, self.ended, self.closed = False, None, False, False
        self.pending = b""

    def _poll(self):
        if self.exited: return True
        try: self.exited, self.status = self.io.poll(self.pid)
        except Exception: raise SnapshotError(_CLEANUP) from None
        return self.exited

    def _check(self, deadline, cancelled):
        if self.cancelled() or cancelled(): raise SnapshotError("SNAPSHOT_CANCELLED")
        if self.clock() >= deadline: raise SnapshotError("SNAPSHOT_TIMEOUT")

    def _exact(self, count, deadline, cancelled):
        data = bytearray()
        while len(data) < count:
            self._check(deadline, cancelled)
            if not self.io.readable(self.fd, min(.02, max(0., deadline - self.clock()))): continue
            try: chunk = self.io.receive(self.fd, count - len(data))
            except BlockingIOError: continue
            if not isinstance(chunk, bytes) or not chunk or len(chunk) > count - len(data): raise SnapshotError("SNAPSHOT_FRAME_INVALID")
            data.extend(chunk)
        return bytes(data)

    def _terminal(self, deadline, cancelled):
        # EOF 뒤 trailing frame/비정상 exit/종료되지 않은 child를 성공으로 세지 않는다.
        while True:
            self._check(deadline, cancelled)
            if self.io.readable(self.fd, min(.02, max(0., deadline - self.clock()))):
                try: trailing = self.io.receive(self.fd, 1)
                except BlockingIOError: continue
                if trailing != b"": raise SnapshotError("SNAPSHOT_FRAME_INVALID")
                if self._poll():
                    if self.status != 0: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
                    self.ended = True; return
                self.io.pause(.005)

    def read_bounded(self, maximum, *, deadline, cancelled):
        if type(maximum) is not int or not 0 < maximum <= CHUNK or not isinstance(deadline, (int, float)) or not math.isfinite(deadline): raise SnapshotError()
        deadline = min(deadline, self.deadline)
        self._check(deadline, cancelled)
        if self.ended: return b""
        if self.closed: raise SnapshotError()
        if self.pid is None: self.pid, self.fd = self.io.start(replace(self.work, deadline=deadline))
        if not self.pending:
            kind, size = struct.unpack(">cI", self._exact(5, deadline, cancelled))
            if size > PAYLOAD or kind not in {b"D", b"E", b"X", b"U"} or ((kind == b"D") != (size > 0)):
                raise SnapshotError("SNAPSHOT_FRAME_INVALID")
            if kind == b"U": raise SnapshotError(_CLEANUP)
            if kind == b"X": raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            if kind == b"E": self._terminal(deadline, cancelled); return b""
            self.pending = self._exact(size, deadline, cancelled)
        value, self.pending = self.pending[:maximum], self.pending[maximum:]
        self._check(deadline, cancelled)
        return value

    def _wait_cleanup(self, duration):
        until = self.clock() + duration
        while not self._poll():
            if self.clock() >= until: return False
            self.io.pause(.005)
        return True

    def abort_owned(self):
        if self.closed: return
        try:
            if self.pid is not None and not self._poll():
                # waitpid로 여전히 본인 child임을 확인한 PID만 신호한다. 그룹/공유 서비스0.
                self.io.terminate(self.pid)
                if not self._wait_cleanup(.2):
                    self.io.kill(self.pid)
                    if not self._wait_cleanup(.2): raise SnapshotError(_CLEANUP)
        except Exception: raise SnapshotError(_CLEANUP) from None
        finally:
            if self.fd is not None:
                try: self.io.close(self.fd)
                except Exception: raise SnapshotError(_CLEANUP) from None
                finally: self.fd = None
            self.pending = b""
        self.closed = True


class FixedSnapshotStreams:
    def __init__(self, *, runtime_files, source_instance_ref=None, _io=None, _clock=time.monotonic):
        if not isinstance(runtime_files, tuple) or any(not isinstance(item, FixedRuntimeFile) for item in runtime_files): raise SnapshotError()
        if len({item.ref.ref_id for item in runtime_files}) != len(runtime_files): raise SnapshotError()
        if source_instance_ref is not None: _safe(source_instance_ref)
        self.source_instance_ref, self.runtime_files = source_instance_ref, runtime_files
        self._source, self._io, self._clock = None, _io or _ForkIO(), _clock
        self._guard, self._uncertain = threading.Lock(), False

    @classmethod
    def from_storage(cls, storage, *, source_instance_ref, region, runtime_files, _io=None, _clock=time.monotonic):
        if type(storage) is not MinioObjectStorageAdapter: raise SnapshotError()
        _safe(region); _safe(source_instance_ref)
        client = storage._client
        # network refresh provider를 parent에서 호출하거나 shared pool을 child에서 재사용0.
        if type(client._provider) is not StaticProvider: raise SnapshotError("SNAPSHOT_OBJECT_CREDENTIAL_UNAVAILABLE")
        credentials = client._provider.retrieve()
        if credentials.is_expired(): raise SnapshotError("SNAPSHOT_OBJECT_CREDENTIAL_UNAVAILABLE")
        options = client._http.connection_pool_kw
        if client._base_url.is_https and options.get("cert_reqs") not in {"CERT_REQUIRED", ssl.CERT_REQUIRED}:
            raise SnapshotError("SNAPSHOT_OBJECT_TLS_UNVERIFIED")
        result = cls(runtime_files=runtime_files, source_instance_ref=source_instance_ref, _io=_io, _clock=_clock)
        result._source = _MinioSource(client._base_url.host, storage._bucket, region, client._base_url.is_https,
            credentials, options.get("ca_certs"))
        return result

    def _object_work(self, ref, deadline):
        if self._source is None or not isinstance(ref, SnapshotObjectRef) or ref.state != "completed": raise SnapshotError("SNAPSHOT_OBJECT_PORT_UNAVAILABLE")
        context = CloudAccessContext(ref.tenant_id, ref.workspace_id, "snapshot-operator", "object.read")
        return _ObjectWork(self._source, ref, ObjectKeyPolicy().final_key(context, ref.area, ref.object_id), deadline)

    @contextmanager
    def _open(self, work, *, deadline, cancelled):
        if self._uncertain: raise SnapshotError(_CLEANUP)
        if not self._guard.acquire(blocking=False): raise SnapshotError("SNAPSHOT_BUSY")
        reader = _OwnedReader(work, self._io, self._clock, deadline, cancelled)
        try: yield reader
        finally:
            try: reader.abort_owned()
            except SnapshotError:
                self._uncertain = True; raise
            finally: self._guard.release()

    def _supported(self, deadline):
        if not self._io.supported(): raise SnapshotError("SNAPSHOT_STREAM_PLATFORM_UNSUPPORTED")
        if type(deadline) not in (int, float) or not math.isfinite(deadline): raise SnapshotError()

    @contextmanager
    def open_object(self, ref, *, deadline, cancelled):
        self._supported(deadline)
        with self._open(self._object_work(ref, deadline), deadline=deadline, cancelled=cancelled) as reader: yield reader

    @contextmanager
    def open_runtime(self, ref, *, deadline, cancelled):
        self._supported(deadline)
        configured = next((item for item in self.runtime_files if item.ref == ref), None)
        if configured is None: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        with self._open(_RuntimeWork(configured, deadline), deadline=deadline, cancelled=cancelled) as reader: yield reader
