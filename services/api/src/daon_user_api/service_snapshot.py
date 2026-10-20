"""D1-A 운영자 내부 service-snapshot-v1. Workspace/API/DRM2와 독립이다.

원본/자격/경로를 탐색하지 않는다. 신뢰한 서버 spec과 명시 주입된 읽기 port만 소비한다.
파일은 기존 OS별 보호 port를 재사용하며, 부분 staging 재구성은 명시 미지원이다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import struct
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .recovery import RecoveryError
from .recovery_capture import _WindowsFiles, _PosixFiles


KIND = "service-snapshot-v1"
CHUNK = 4 * 1024 * 1024
MAGIC = b"DAON-SERVICE-SNAPSHOT-V1\n"


class SnapshotError(Exception):
    def __init__(self, code="SNAPSHOT_INVALID"):
        self.code = code
        super().__init__(code)


class BoundedSnapshotReader(ABC):
    """주입 port는 deadline/cancel 동안 read를 중단하고 본인 stream만 닫을 수 있어야 한다.

    일반 BinaryIO를 thread로 감싸 보장을 가장하지 않는다. 네트워크 adapter는 transport의
    실제 timeout/interrupt 보장을 구현해야 하며 이 계약 없는 port는 소비하지 않는다.
    """
    @abstractmethod
    def read_bounded(self, maximum, *, deadline, cancelled): ...

    @abstractmethod
    def abort_owned(self): ...

    @contextmanager
    def consuming(self):
        try: yield self
        except BaseException:
            self.abort_owned()
            raise


def _safe(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None:
        raise SnapshotError()
    return value


def _hash(value):
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise SnapshotError()
    return value


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise SnapshotError() from None


@dataclass(frozen=True)
class SnapshotLimits:
    archive_bytes: int
    object_bytes: int
    metadata_bytes: int
    duration_seconds: int

    def __post_init__(self):
        for value in asdict(self).values():
            if type(value) is not int or value <= 0:
                raise SnapshotError("SNAPSHOT_LIMIT_INVALID")


@dataclass(frozen=True, repr=False)
class ServiceSnapshotSpec:
    source_instance_ref: str
    credential_ref: str
    client_ref: str
    root_ref: str
    code_revision: str
    image_digest: str
    runtime_allowlist: tuple[str, ...]
    key_version: str
    key_reference: str
    limits: SnapshotLimits

    def __post_init__(self):
        for field in ("source_instance_ref", "credential_ref", "client_ref", "root_ref", "code_revision", "key_version", "key_reference"):
            _safe(getattr(self, field))
        if not isinstance(self.image_digest, str) or not self.image_digest.startswith("sha256:"):
            raise SnapshotError()
        _hash(self.image_digest[7:])
        if (not isinstance(self.limits, SnapshotLimits) or not isinstance(self.runtime_allowlist, tuple)
                or len(set(self.runtime_allowlist)) != len(self.runtime_allowlist)):
            raise SnapshotError()
        for ref in self.runtime_allowlist: _safe(ref)


@dataclass(frozen=True)
class SnapshotObjectRef:
    tenant_id: str
    workspace_id: str
    area: str
    object_id: str
    state: str
    digest: str
    byte_size: int

    def __post_init__(self):
        for value in (self.tenant_id, self.workspace_id, self.object_id): _safe(value)
        if self.area not in {"source", "output"}:
            raise SnapshotError()
        _hash(self.digest)
        if type(self.byte_size) is not int or self.byte_size < 0:
            raise SnapshotError()

    def label(self):
        return "object:" + ":".join((self.tenant_id, self.workspace_id, self.area, self.object_id))


@dataclass(frozen=True)
class SnapshotRuntimeRef:
    ref_id: str
    mode: str
    digest: str
    byte_size: int

    def __post_init__(self):
        _safe(self.ref_id); _hash(self.digest)
        if (self.mode not in {"bytes", "reconstructible"} or type(self.byte_size) is not int
                or self.byte_size < 0 or (self.mode == "reconstructible" and self.byte_size != 0)):
            raise SnapshotError()


@dataclass(frozen=True, repr=False)
class SnapshotLease:
    snapshot_id: str
    schema_revision: str
    object_inventory: tuple[SnapshotObjectRef, ...]
    runtime_inventory: tuple[SnapshotRuntimeRef, ...]
    catalog: dict


@dataclass(frozen=True, repr=False)
class ServiceSnapshotReceipt:
    kind: str
    capture_id: str
    spec_digest: str
    spec: dict
    owner_id: str
    snapshot_id: str
    schema_revision: str
    image_digest: str
    catalog_digest: str
    metadata_digest: str
    streams: tuple[dict, ...]
    completed_at: str


class ServiceSnapshotExecutor:
    def __init__(self, root, *, master_key, ports, monotonic=time.monotonic):
        if not isinstance(master_key, bytes) or len(master_key) < 32:
            raise SnapshotError("SNAPSHOT_KEY_UNAVAILABLE")
        self.root = Path(root)
        if not self.root.is_absolute() or self.root == Path(self.root.anchor):
            raise SnapshotError("SNAPSHOT_ROOT_INVALID")
        self._key, self._ports, self._clock = master_key, ports, monotonic
        self._files = _WindowsFiles() if os.name == "nt" else _PosixFiles()
        self._local_lock = threading.Lock()
        try:
            with self._files.pin(self.root): self._root_identity = self._files.identity(self.root)
        except (OSError, RecoveryError):
            raise SnapshotError("SNAPSHOT_ROOT_UNAVAILABLE") from None

    @contextmanager
    def _pinned(self):
        try:
            with self._files.pin(self.root):
                if self._files.identity(self.root) != self._root_identity:
                    raise SnapshotError()
                yield
        except (OSError, RecoveryError):
            raise SnapshotError("SNAPSHOT_STORAGE_UNAVAILABLE") from None

    def _cipher(self, spec, capture_id, purpose):
        info = _json(["daon.service-snapshot", 1, purpose, capture_id, asdict(spec)])
        return AESGCM(HKDF(algorithm=hashes.SHA256(), length=32,
            salt=hashlib.sha256(info).digest(), info=info).derive(self._key))

    def _seal(self, spec, capture_id, purpose, raw, aad, nonces):
        nonce = secrets.token_bytes(12)
        if nonce in nonces: raise SnapshotError()
        nonces.add(nonce)
        return nonce + self._cipher(spec, capture_id, purpose).encrypt(nonce, raw, _json(aad))

    def _open(self, spec, capture_id, purpose, blob, aad, nonces):
        if len(blob) < 28 or blob[:12] in nonces: raise SnapshotError()
        nonces.add(blob[:12])
        try: return self._cipher(spec, capture_id, purpose).decrypt(blob[:12], blob[12:], _json(aad))
        except InvalidTag: raise SnapshotError() from None

    def _write(self, path, blob):
        with self._files.file(path, create=True) as target: target.write(blob)

    def _read(self, path, limit):
        with self._files.file(path) as source:
            raw = source.read(limit + 1)
        if len(raw) > limit: raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
        return raw

    @staticmethod
    def _metadata(spec, lease):
        if not isinstance(lease, SnapshotLease): raise SnapshotError()
        _safe(lease.snapshot_id); _safe(lease.schema_revision)
        if (not isinstance(lease.object_inventory, tuple) or not isinstance(lease.runtime_inventory, tuple)
                or not isinstance(lease.catalog, dict)
                or set(lease.catalog) != {"roles", "memberships", "relations", "functions"}):
            raise SnapshotError()
        labels = []
        for item in lease.object_inventory:
            if not isinstance(item, SnapshotObjectRef) or item.state != "completed":
                raise SnapshotError("SNAPSHOT_STAGING_UNSUPPORTED")
            labels.append(item.label())
        if len(set(labels)) != len(labels): raise SnapshotError()
        if {ref.ref_id for ref in lease.runtime_inventory} != set(spec.runtime_allowlist): raise SnapshotError()
        if len(lease.runtime_inventory) != len(spec.runtime_allowlist): raise SnapshotError()
        if sum(item.byte_size for item in (*lease.object_inventory, *lease.runtime_inventory)) > spec.limits.object_bytes:
            raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
        value = {"kind": KIND, "capture_id_scope": hashlib.sha256(_json(asdict(spec))).hexdigest(),
            "snapshot_id": lease.snapshot_id, "schema_revision": lease.schema_revision,
            "image_digest": spec.image_digest, "code_revision": spec.code_revision,
            "objects": [asdict(item) for item in lease.object_inventory],
            "runtime": [asdict(item) for item in lease.runtime_inventory], "catalog": lease.catalog}
        raw = _json(value)
        if len(raw) > spec.limits.metadata_bytes: raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
        return value, raw

    def _cleanup_partial(self, spec, capture_id, folder, owner):
        # 최종 receipt 미발행이며 이 호출이 만든 소유 namespace만 정리한다.
        # receipt 존재/조회 불확정은 보존한다. owner marker는 재생성 방지를 위해 유지한다.
        try:
            with self._pinned(), self._files.pin(folder):
                if self._files.exists(folder / "receipt.bin"): return
                actual = self._open(spec, capture_id, "owner", self._read(folder / "owner.bin", 4096), [KIND, "owner"], set())
                if actual != owner: return
                names = self._files.names(folder)
                if not names <= {"owner.bin", "data.bin", "receipt.pending"}: return
                for name in names - {"owner.bin"}: self._files.unlink(folder / name)
        except (SnapshotError, OSError, RecoveryError):
            return  # 소유/상태를 입증할 수 없으면 자동 삭제하지 않는다.

    def capture(self, spec, *, capture_id, cancelled):
        _safe(capture_id)
        if not isinstance(spec, ServiceSnapshotSpec) or self._ports is None: raise SnapshotError("SNAPSHOT_PORT_UNAVAILABLE")
        if not self._local_lock.acquire(blocking=False): raise SnapshotError("SNAPSHOT_BUSY")
        started = self._clock()
        def check():
            if cancelled(): raise SnapshotError("SNAPSHOT_CANCELLED")
            if self._clock() - started >= spec.limits.duration_seconds: raise SnapshotError("SNAPSHOT_TIMEOUT")
        folder, lock = self.root / capture_id, self.root / "service-snapshot.lock"
        owner, created, locked = secrets.token_bytes(32), False, False
        cleanup_uncertain = False
        try:
            with self._pinned():
                # 같은 root의 프로세스 간에도 create-new만 허용한다. stale lock은 자동 탈취하지 않는다.
                self._write(lock, owner); locked = True
                if self._files.exists(folder): raise SnapshotError("SNAPSHOT_ALREADY_EXISTS")
                self._files.mkdir(folder)
                with self._files.pin(folder):
                    self._write(folder / "owner.bin", self._seal(spec, capture_id, "owner", owner, [KIND, "owner"], set()))
                    created = True
                    check()
                    with self._ports.open_snapshot(spec, cancelled=cancelled) as lease:
                        metadata, raw = self._metadata(spec, lease)
                        for ref in lease.runtime_inventory:
                            if ref.mode == "reconstructible" and self._ports.verify_runtime_reference(spec, ref) is not True:
                                raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                        nonces, streams = set(), []
                        with self._files.file(folder / "data.bin", create=True) as target:
                            target.write(MAGIC)
                            sealed = self._seal(spec, capture_id, "manifest", raw, [KIND, capture_id, "metadata"], nonces)
                            target.write(struct.pack(">I", len(sealed)) + sealed)
                            jobs = [("archive", None, spec.limits.archive_bytes, lambda: self._ports.dump_archive(lease))]
                            jobs += [(item.label(), item, item.byte_size, lambda item=item: self._ports.open_object(item)) for item in lease.object_inventory]
                            jobs += [("runtime:" + item.ref_id, item, item.byte_size, lambda item=item: self._ports.open_runtime(item))
                                     for item in lease.runtime_inventory if item.mode == "bytes"]
                            for label, expected, limit, opener in jobs:
                                size, count, digest = 0, 0, hashlib.sha256()
                                with opener() as source:
                                    if not isinstance(source, BoundedSnapshotReader):
                                        raise SnapshotError("SNAPSHOT_BOUNDED_READ_REQUIRED")
                                    with source.consuming():
                                        while True:
                                            check()
                                            chunk = bytearray()
                                            while len(chunk) < CHUNK:
                                                check()
                                                part = source.read_bounded(min(CHUNK - len(chunk), max(1, limit - size - len(chunk) + 1)),
                                                    deadline=started + spec.limits.duration_seconds, cancelled=cancelled)
                                                check()
                                                if not isinstance(part, bytes) or len(part) > min(CHUNK - len(chunk), max(1, limit - size - len(chunk) + 1)):
                                                    raise SnapshotError()
                                                if not part: break
                                                chunk.extend(part)
                                                if size + len(chunk) > limit: raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
                                            if not chunk: break
                                            digest.update(chunk); size += len(chunk)
                                            purpose = "archive" if expected is None else ("object" if isinstance(expected, SnapshotObjectRef) else "runtime")
                                            sealed = self._seal(spec, capture_id, purpose, bytes(chunk), [KIND, capture_id, label, count], nonces)
                                            target.write(struct.pack(">I", len(sealed)) + sealed); count += 1
                                if expected is not None and (size != expected.byte_size or digest.hexdigest() != expected.digest): raise SnapshotError()
                                if label == "archive" and size == 0: raise SnapshotError()
                                streams.append({"label": label, "chunks": count, "byte_size": size, "digest": digest.hexdigest()})
                        check()
                    receipt = ServiceSnapshotReceipt(KIND, capture_id, hashlib.sha256(_json(asdict(spec))).hexdigest(), json.loads(_json(asdict(spec))), owner.hex(),
                        lease.snapshot_id, lease.schema_revision, spec.image_digest, hashlib.sha256(_json(lease.catalog)).hexdigest(),
                        hashlib.sha256(raw).hexdigest(), tuple(streams), datetime.now(timezone.utc).isoformat())
                    # 최종 인증 receipt를 쓰기 전에 보존본 전체를 재읽는다.
                    self._verify_data(spec, capture_id, folder, receipt, check=check)
                    blob = self._seal(spec, capture_id, "receipt", _json(asdict(receipt)), [KIND, capture_id, "receipt"], nonces)
                    if len(blob) > spec.limits.metadata_bytes + 28:
                        raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
                    self._write(folder / "receipt.pending", blob)
                    check()
                    self._files.rename(folder / "receipt.pending", folder / "receipt.bin")
                    return receipt
        except SnapshotError as error:
            cleanup_uncertain = error.code.endswith("CLEANUP_UNCERTAIN")
            raise
        except Exception:
            raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE") from None
        finally:
            try:
                if created and not cleanup_uncertain: self._cleanup_partial(spec, capture_id, folder, owner)
                if locked and not cleanup_uncertain:
                    with self._pinned():
                        if self._read(lock, 4096) == owner: self._files.unlink(lock)
            finally:
                self._local_lock.release()

    @staticmethod
    def _frame(source, maximum):
        prefix = source.read(4)
        if len(prefix) != 4: raise SnapshotError()
        size = struct.unpack(">I", prefix)[0]
        if not 28 <= size <= maximum: raise SnapshotError()
        value = source.read(size)
        if len(value) != size: raise SnapshotError()
        return value

    def _verify_data(self, spec, capture_id, folder, receipt, *, check=lambda: None, sink=None):
        if (receipt.kind != KIND or receipt.capture_id != capture_id or receipt.image_digest != spec.image_digest
                or _json(receipt.spec) != _json(asdict(spec))
                or receipt.spec_digest != hashlib.sha256(_json(asdict(spec))).hexdigest()): raise SnapshotError()
        nonces = set()
        with self._files.file(folder / "data.bin") as source:
            if source.read(len(MAGIC)) != MAGIC: raise SnapshotError()
            raw = self._open(spec, capture_id, "manifest", self._frame(source, spec.limits.metadata_bytes + 28), [KIND, capture_id, "metadata"], nonces)
            if hashlib.sha256(raw).hexdigest() != receipt.metadata_digest: raise SnapshotError()
            try:
                meta = json.loads(raw)
                lease = SnapshotLease(meta["snapshot_id"], meta["schema_revision"],
                    tuple(SnapshotObjectRef(**row) for row in meta["objects"]),
                    tuple(SnapshotRuntimeRef(**row) for row in meta["runtime"]), meta["catalog"])
                _, canonical = self._metadata(spec, lease)
                if (raw != canonical or lease.snapshot_id != receipt.snapshot_id or lease.schema_revision != receipt.schema_revision
                        or hashlib.sha256(_json(lease.catalog)).hexdigest() != receipt.catalog_digest): raise SnapshotError()
                expected = [("archive", None)] + [(item.label(), item) for item in lease.object_inventory] + [
                    ("runtime:" + item.ref_id, item) for item in lease.runtime_inventory if item.mode == "bytes"]
                if len(expected) != len(receipt.streams): raise SnapshotError()
                for record, (label, item) in zip(receipt.streams, expected):
                    size, digest = 0, hashlib.sha256()
                    limit = spec.limits.archive_bytes if item is None else item.byte_size
                    if (set(record) != {"label", "chunks", "byte_size", "digest"} or record["label"] != label
                            or type(record["chunks"]) is not int or record["chunks"] < 0
                            or record["chunks"] > (limit + CHUNK - 1) // CHUNK): raise SnapshotError()
                    for index in range(record["chunks"]):
                        check()
                        purpose = "archive" if item is None else ("object" if isinstance(item, SnapshotObjectRef) else "runtime")
                        chunk = self._open(spec, capture_id, purpose, self._frame(source, CHUNK + 28), [KIND, capture_id, label, index], nonces)
                        if not chunk or (index + 1 < record["chunks"] and len(chunk) != CHUNK): raise SnapshotError()
                        digest.update(chunk); size += len(chunk)
                        if size > limit: raise SnapshotError()
                        if sink is not None and sink[0] == label: sink[1].write(chunk)
                    if (size != record["byte_size"] or digest.hexdigest() != record["digest"]
                            or (item is not None and (size != item.byte_size or digest.hexdigest() != item.digest))
                            or (item is None and size == 0)): raise SnapshotError()
                if source.read(1): raise SnapshotError()
            except (TypeError, ValueError, KeyError): raise SnapshotError() from None

    def verify(self, spec, capture_id):
        _safe(capture_id)
        with self._pinned(), self._files.pin(self.root / capture_id):
            try:
                raw = self._open(spec, capture_id, "receipt", self._read(self.root / capture_id / "receipt.bin", spec.limits.metadata_bytes + 28),
                    [KIND, capture_id, "receipt"], set())
                data = json.loads(raw); data["streams"] = tuple(data["streams"])
                receipt = ServiceSnapshotReceipt(**data)
                datetime.fromisoformat(receipt.completed_at).astimezone(timezone.utc)
            except (ValueError, TypeError, KeyError): raise SnapshotError() from None
            self._verify_data(spec, capture_id, self.root / capture_id, receipt)
            owner = self._open(spec, capture_id, "owner", self._read(self.root / capture_id / "owner.bin", 4096), [KIND, "owner"], set())
            if owner.hex() != receipt.owner_id: raise SnapshotError()
            return receipt

    def read_stream(self, spec, capture_id, label, sink):
        receipt = self.verify(spec, capture_id)
        if label not in {row["label"] for row in receipt.streams}: raise SnapshotError()
        with self._pinned(), self._files.pin(self.root / capture_id):
            self._verify_data(spec, capture_id, self.root / capture_id, receipt, sink=(label, sink))

    def reconcile(self, spec, capture_id, reference_lookup):
        receipt = self.verify(spec, capture_id)
        try: current = reference_lookup()
        except Exception: raise SnapshotError("SNAPSHOT_REFERENCE_UNCERTAIN") from None
        if current is None: return "unreferenced"
        if current != receipt: raise SnapshotError("SNAPSHOT_REFERENCE_MISMATCH")
        return "referenced"

    def discard_unreferenced(self, spec, capture_id, *, owner_id, reference_guard):
        """명시 소유 정리만. guard는 권위 있는 참조 확인의 수명/경합 보호를 소유한다.

        조회 불가/commit 불확정/참조 중이면 삭제하지 않는다. 자동 GC/TTL은 없다.
        """
        _safe(capture_id); _hash(owner_id)
        receipt = self.verify(spec, capture_id)
        if receipt.owner_id != owner_id: raise SnapshotError("SNAPSHOT_OWNER_MISMATCH")
        folder = self.root / capture_id
        try:
            with reference_guard() as current, self._pinned(), self._files.pin(folder):
                if current is not None: raise SnapshotError("SNAPSHOT_REFERENCED")
                owner = self._open(spec, capture_id, "owner", self._read(folder / "owner.bin", 4096), [KIND, "owner"], set())
                if owner.hex() != owner_id: raise SnapshotError("SNAPSHOT_OWNER_MISMATCH")
                names = self._files.names(folder)
                if not names <= {"owner.bin", "data.bin", "receipt.bin", "receipt.pending"}: raise SnapshotError()
                for name in ("data.bin", "receipt.pending", "receipt.bin"):
                    if name in names: self._files.unlink(folder / name)
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_REFERENCE_UNCERTAIN") from None
