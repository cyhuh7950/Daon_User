"""L2-A 선택 자료의 내부 v2 보존본. 전체 DB snapshot/materializer가 아니다.

정식 root/key를 탐색하거나 생성하지 않는다. 주입한 기존 root와 메모리 key만 사용한다.
Windows는 no-share-write/delete handle, Linux는 dir_fd/NOFOLLOW/renameat2를 분리한다.
필요한 OS 보장이 없으면 명시 거부한다(성공 fallback 없음).
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import secrets
import struct
import stat
import sys
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .data_canon import CanonError, canonical_json_bytes
from .recovery import BackupObjectInput, RecoveryError, _safe, _safe_canon_id


CHUNK_SIZE = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
MAGIC = b"DAON-CAPTURE-V2\n"


def _invalid():
    return RecoveryError("BACKUP_MANIFEST_INVALID", 409)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise _invalid()
    return value


def _cipher(master_key, scope, purpose, key_version, key_reference):
    if not isinstance(master_key, bytes) or len(master_key) < 32:
        raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
    _safe(key_version); _safe(key_reference)
    info = _json(["daon.recovery", 2, purpose, key_reference, key_version, scope.binding()])
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(info).digest(), info=info).derive(master_key)
    return AESGCM(key)


def encode_manifest(scope, receipt, *, master_key, key_version, key_reference):
    header = _json({"scope": asdict(scope), "key_version": key_version, "key_reference": key_reference})
    if any(receipt.get(key) != value for key, value in scope.binding().items()):
        raise _invalid()
    nonce = secrets.token_bytes(12)
    encrypted = _cipher(master_key, scope, "db-manifest", key_version, key_reference).encrypt(nonce, _json(receipt), header)
    return b"DRM2" + struct.pack(">I", len(header)) + header + nonce + encrypted


def decode_manifest(envelope, *, master_key, key_version, key_reference):
    try:
        if not isinstance(envelope, bytes) or not envelope.startswith(b"DRM2") or len(envelope) > 1024 * 1024:
            raise _invalid()
        size = struct.unpack(">I", envelope[4:8])[0]
        if not 0 < size <= 16384 or len(envelope) < 8 + size + 28:
            raise _invalid()
        header = envelope[8:8 + size]
        parsed = json.loads(header)
        if set(parsed) != {"scope", "key_version", "key_reference"} or parsed["key_version"] != key_version or parsed["key_reference"] != key_reference:
            raise _invalid()
        data = parsed["scope"]
        data["source_version_ids"] = tuple(data["source_version_ids"])
        scope = CaptureScope(**data)
        blob = envelope[8 + size:]
        raw = _cipher(master_key, scope, "db-manifest", key_version, key_reference).decrypt(blob[:12], blob[12:], header)
        receipt = json.loads(raw)
        if (any(receipt.get(key) != value for key, value in scope.binding().items())
                or receipt.get("key_version") != key_version or receipt.get("key_reference") != key_reference):
            raise _invalid()
        return scope, receipt
    except (InvalidTag, KeyError, TypeError, ValueError, struct.error):
        raise _invalid() from None


@dataclass(frozen=True)
class CaptureScope:
    capture_id: str
    tenant_id: str
    workspace_id: str
    notebook_id: str
    source_version_ids: tuple[str, ...]
    selection_fingerprint: str
    schema_revision: str
    policy_version: str
    retention_watermark: str

    def __post_init__(self):
        for value in (self.capture_id, self.tenant_id, self.workspace_id, self.notebook_id,
                      self.schema_revision, self.policy_version, self.retention_watermark):
            _safe(value)
        if (not isinstance(self.source_version_ids, tuple) or not 1 <= len(self.source_version_ids) <= 100
                or len(set(self.source_version_ids)) != len(self.source_version_ids)):
            raise _invalid()
        for value in self.source_version_ids:
            _safe(value)
        _digest(self.selection_fingerprint)
        object.__setattr__(self, "source_version_ids", tuple(sorted(self.source_version_ids)))

    def binding(self):
        return {"format_version": 2, **asdict(self), "source_version_ids": list(self.source_version_ids)}


CANON_COLUMNS = {"tenant_id", "workspace_id", "record_id", "aggregate_id", "version", "schema_version",
                 "canonical_json", "digest_sha256", "previous_version_id", "created_by", "trace_id", "created_at"}
# 실제 0003/0020/Object 원장의 열만 사용한다. 원본 object_key/staging_key는 수집하지 않는다.
METADATA_COLUMNS = {
    "notebooks": {"tenant_id", "workspace_id", "notebook_id", "created_by", "created_at"},
    "notebook_metadata_versions": {"tenant_id", "workspace_id", "notebook_id", "version", "title", "description",
                                   "is_current", "updated_by", "updated_at"},
    "notebook_bindings": {"tenant_id", "workspace_id", "notebook_id", "binding_kind", "record_id", "version_id", "created_by", "created_at"},
    "sources": CANON_COLUMNS | {"state"},
    "source_versions": CANON_COLUMNS | {"source_id", "object_id"},
    "object_records": {"tenant_id", "workspace_id", "object_id", "area", "digest_sha256", "byte_size", "content_type", "status"},
}


def validate_metadata(scope, metadata, objects):
    try:
        return _validate_metadata(scope, metadata, objects)
    except (CanonError, KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise _invalid() from None


def _validate_metadata(scope, metadata, objects):
    if not isinstance(metadata, dict) or set(metadata) != set(METADATA_COLUMNS):
        raise _invalid()
    for table, columns in METADATA_COLUMNS.items():
        rows = metadata[table]
        if not isinstance(rows, list) or not rows or len(rows) > 100:
            raise _invalid()
        for row in rows:
            if (not isinstance(row, dict) or set(row) != columns or row["tenant_id"] != scope.tenant_id
                    or row["workspace_id"] != scope.workspace_id):
                raise _invalid()
            for key in ("version", "schema_version"):
                if key in row and (type(row[key]) is not int or row[key] < 1):
                    raise _invalid()
            for key in ("created_at", "updated_at"):
                if key in row:
                    value = row[key]
                    if (not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value)
                            or datetime.fromisoformat(value).tzinfo is None):
                        raise _invalid()
    for table in ("notebooks", "notebook_metadata_versions"):
        if len(metadata[table]) != 1 or metadata[table][0]["notebook_id"] != scope.notebook_id:
            raise _invalid()
    if metadata["notebook_metadata_versions"][0]["is_current"] is not True:
        raise _invalid()
    sources = {row["record_id"] for row in metadata["sources"]}
    versions = {row["record_id"]: row for row in metadata["source_versions"]}
    records = {row["object_id"]: row for row in metadata["object_records"]}
    by_id = {item.object_id: item for item in objects}
    if (len(sources) != len(metadata["sources"]) or len(versions) != len(metadata["source_versions"])
            or len(records) != len(metadata["object_records"]) or set(versions) != set(scope.source_version_ids)
            or set(records) != set(by_id) or sources != {row["source_id"] for row in versions.values()}):
        raise _invalid()
    bindings = metadata["notebook_bindings"]
    if len(bindings) != len(versions) or {row["version_id"] for row in bindings} != set(versions):
        raise _invalid()
    for row in bindings:
        if (row["notebook_id"] != scope.notebook_id or row["binding_kind"] != "source"
                or row["record_id"] != versions[row["version_id"]]["source_id"]):
            raise _invalid()
    for row in metadata["sources"] + metadata["source_versions"]:
        if hashlib.sha256(canonical_json_bytes(row["canonical_json"])).hexdigest() != row["digest_sha256"]:
            raise _invalid()
        _safe_canon_id(row["record_id"])
        _safe_canon_id(row["aggregate_id"])
        if row["previous_version_id"] is not None:
            # 실제 0003 Canon FK는 최대256이다. 새 선택128 제약으로 provenance를 축소하지 않는다.
            _safe_canon_id(row["previous_version_id"])
    for row in versions.values():
        item = by_id.get(row["object_id"])
        payload = row["canonical_json"]
        if item is None or payload.get("object_id") != item.object_id or type(payload.get("byte_size")) is not int or payload["byte_size"] != item.byte_size:
            raise _invalid()
        expected = payload.get("text_sha256") if payload.get("origin_kind") in {"website", "mcp_open_law"} else payload.get("content_digest_sha256")
        if expected != item.checksum_sha256:
            raise _invalid()
    for object_id, row in records.items():
        item = by_id[object_id]
        if (row["status"] != "completed" or row["area"] != "source" or row["digest_sha256"] != item.checksum_sha256
                or type(row["byte_size"]) is not int or row["byte_size"] != item.byte_size):
            raise _invalid()
    raw = _json(metadata)
    if len(raw) > MAX_METADATA_BYTES:
        raise RecoveryError("BACKUP_SELECTION_TOO_LARGE", 413)
    return raw


class _WindowsFiles:
    """열린 ancestor handle은 rename/reparse 교체를 차단한다. 링크 파일도 handle로 검사한다."""
    def __init__(self):
        if os.name != "nt":
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        from ctypes import wintypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.api.CreateFileW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        class Info(ctypes.Structure):
            _fields_ = [("attrs", wintypes.DWORD), ("created", wintypes.FILETIME), ("accessed", wintypes.FILETIME),
                        ("written", wintypes.FILETIME), ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                        ("size_low", wintypes.DWORD), ("links", wintypes.DWORD), ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]
        self.Info = Info
        self.api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]

    def handle(self, path, *, directory=False, create=False):
        # FILE_SHARE_READ만 허용: 작업 중 write/delete/rename와 reparse 변환을 차단한다.
        access = 0x80 if directory else (0x40000000 if create else 0x80000000)
        handle = self.api.CreateFileW(str(path), access, 1, None,
            1 if create else 3, 0x00200000 | (0x02000000 if directory else 0), None)
        if handle == ctypes.c_void_p(-1).value:
            raise _invalid()
        info = self.Info()
        if (not self.api.GetFileInformationByHandle(handle, ctypes.byref(info)) or info.attrs & 0x400
                or bool(info.attrs & 0x10) != directory or (not directory and info.links != 1)):
            self.api.CloseHandle(handle)
            raise _invalid()
        return handle

    @contextmanager
    def pin(self, path):
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise _invalid()
        handles = []
        try:
            for part in (*reversed(path.parents), path):
                handles.append(self.handle(part, directory=True))
            yield handles[-1]
        finally:
            for handle in reversed(handles):
                self.api.CloseHandle(handle)

    @contextmanager
    def file(self, path, *, create=False):
        import msvcrt
        handle = self.handle(path, create=create)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_WRONLY if create else os.O_RDONLY))
        except BaseException:
            self.api.CloseHandle(handle)
            raise
        with os.fdopen(fd, "wb" if create else "rb") as stream:
            yield stream
            if create:
                stream.flush()
                os.fsync(stream.fileno())

    @staticmethod
    def mkdir(path):
        Path(path).mkdir()

    @staticmethod
    def exists(path):
        try:
            Path(path).lstat()
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def identity(path):
        info = Path(path).stat()
        return info.st_dev, info.st_ino

    def rename(self, source, target):
        from ctypes import wintypes
        self.api.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        self.api.MoveFileExW.restype = wintypes.BOOL
        # MOVEFILE_WRITE_THROUGH만, REPLACE_EXISTING/COPY_ALLOWED는 금지한다.
        if not self.api.MoveFileExW(str(source), str(target), 8):
            raise _invalid()

    @staticmethod
    def names(path):
        return set(os.listdir(path))

    @staticmethod
    def unlink(path):
        os.unlink(path)

class _PosixFiles:
    """dir_fd/openat + NOFOLLOW만 사용한다. renameat2 미지원 환경은 실패한다.

    실제 POSIX 실행은 별도 검증 대상이다. Windows로 동작을 대체하지 않는다.
    열린 root inode에 상대 접근하므로 pathname 교체로 namespace 밖에 쓰지 않는다.
    """
    def __init__(self):
        if (os.name != "posix" or not sys.platform.startswith("linux")
                or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
                or os.open not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd):
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        self._pins = ContextVar("recovery_capture_pinned_directories", default=())
        self.api = ctypes.CDLL(None, use_errno=True)
        if not hasattr(self.api, "renameat2"):
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        self.api.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        self.api.renameat2.restype = ctypes.c_int

    @contextmanager
    def pin(self, path):
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise _invalid()
        inherited = dict(self._pins.get())
        candidates = [parent for parent in (*path.parents, path) if parent in inherited]
        handles = []
        try:
            if candidates:
                base = max(candidates, key=lambda parent: len(parent.parts))
                fd = os.dup(inherited[base]); handles.append(fd)
            else:
                base = Path(path.anchor)
                fd = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                handles.append(fd)
            mapping = {**inherited, base: fd}
            for component in path.relative_to(base).parts:
                fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                handles.append(fd); base = base / component; mapping[base] = fd
                if not stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise _invalid()
            token = self._pins.set(tuple(mapping.items()))
            try:
                yield fd
            finally:
                self._pins.reset(token)
        except OSError:
            raise _invalid() from None
        finally:
            for fd in reversed(handles):
                os.close(fd)

    def _parent(self, path):
        path = Path(path)
        fd = dict(self._pins.get()).get(path.parent)
        if fd is None or path.name in {"", ".", ".."}:
            raise _invalid()
        return fd, path.name

    @contextmanager
    def file(self, path, *, create=False):
        parent, name = self._parent(path)
        flags = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | (os.O_WRONLY | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY)
        try:
            fd = os.open(name, flags, 0o600, dir_fd=parent)
        except OSError:
            raise _invalid() from None
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise _invalid()
        with os.fdopen(fd, "wb" if create else "rb") as stream:
            yield stream
            if create:
                stream.flush(); os.fsync(stream.fileno())
                if os.fstat(stream.fileno()).st_nlink != 1:
                    raise _invalid()
                os.fsync(parent)

    def mkdir(self, path):
        parent, name = self._parent(path)
        os.mkdir(name, mode=0o700, dir_fd=parent)
        os.fsync(parent)

    def exists(self, path):
        parent, name = self._parent(path)
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def identity(self, path):
        fd = dict(self._pins.get()).get(Path(path))
        if fd is None:
            raise _invalid()
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        return info.st_dev, info.st_ino

    def rename(self, source, target):
        parent, source_name = self._parent(source)
        target_parent, target_name = self._parent(target)
        if parent != target_parent or self.api.renameat2(parent, os.fsencode(source_name), target_parent, os.fsencode(target_name), 1) != 0:
            raise _invalid()
        os.fsync(parent)

    def names(self, path):
        fd = dict(self._pins.get()).get(Path(path))
        if fd is None:
            raise _invalid()
        return set(os.listdir(fd))

    def unlink(self, path):
        parent, name = self._parent(path)
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)

@dataclass(frozen=True, repr=False)
class VerifiedCapture:
    metadata: dict
    receipt: dict


class FileCaptureStore:
    def __init__(self, root, *, master_key, key_version, key_reference):
        if not isinstance(master_key, bytes) or len(master_key) < 32:
            raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        self._key = master_key
        self.key_version, self.key_reference = _safe(key_version), _safe(key_reference)
        self.root = Path(root)
        self._fs = _WindowsFiles() if os.name == "nt" else _PosixFiles()
        with self._fs.pin(self.root):
            self._root_identity = self._fs.identity(self.root)

    def _cipher(self, scope, purpose):
        return _cipher(self._key, scope, purpose, self.key_version, self.key_reference)

    def _encrypt(self, scope, purpose, raw, detail, nonces):
        nonce = secrets.token_bytes(12)
        if nonce in nonces:
            raise _invalid()
        nonces.add(nonce)
        aad = _json([scope.binding(), self.key_version, self.key_reference, purpose, detail])
        return nonce + self._cipher(scope, purpose).encrypt(nonce, raw, aad)

    def _decrypt(self, scope, purpose, blob, detail, nonces):
        if len(blob) < 28 or blob[:12] in nonces:
            raise _invalid()
        nonces.add(blob[:12])
        aad = _json([scope.binding(), self.key_version, self.key_reference, purpose, detail])
        try:
            return self._cipher(scope, purpose).decrypt(blob[:12], blob[12:], aad)
        except InvalidTag:
            raise _invalid() from None

    @staticmethod
    def _directory_name(scope):
        name = scope.capture_id
        if any(char in name for char in ":/\\") or name.endswith(".") or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise _invalid()
        return name

    @contextmanager
    def _capture_dir(self, scope, *, create=False):
        path = self.root / self._directory_name(scope)
        with self._fs.pin(self.root):
            if self._fs.identity(self.root) != self._root_identity:
                raise _invalid()
            if create:
                try:
                    self._fs.mkdir(path)
                except OSError:
                    raise _invalid() from None
            with self._fs.pin(path):
                yield path

    def _write(self, path, blob):
        with self._fs.file(path, create=True) as stream:
            stream.write(blob)

    def _read(self, path, limit):
        with self._fs.file(path) as stream:
            blob = stream.read(limit + 1)
        if len(blob) > limit:
            raise _invalid()
        return blob

    @staticmethod
    def _objects(objects):
        if not isinstance(objects, tuple) or not 1 <= len(objects) <= 100 or len({item.object_id for item in objects}) != len(objects):
            raise _invalid()
        if sum(item.byte_size for item in objects) > MAX_SOURCE_BYTES:
            raise RecoveryError("BACKUP_SELECTION_TOO_LARGE", 413)
        return tuple(sorted(objects, key=lambda item: item.object_id))

    @staticmethod
    def _detail(item, index, count):
        return [item.object_id, item.checksum_sha256, item.byte_size, index, count]

    def capture(self, scope, metadata, objects, open_source: Callable[[BackupObjectInput], BinaryIO], *, owner_id=None):
        objects = self._objects(objects)
        raw = validate_metadata(scope, metadata, objects)
        nonces = set()
        owner_id = _safe(owner_id if owner_id is not None else secrets.token_hex(16))
        receipt = {**scope.binding(), "key_version": self.key_version, "key_reference": self.key_reference,
                   "metadata_digest": hashlib.sha256(raw).hexdigest(), "metadata_size": len(raw), "objects": []}
        with self._capture_dir(scope, create=True) as path:
            self._write(path / "owner.bin", self._encrypt(scope, "owner", _json({"scope": scope.binding(), "owner_id": owner_id}), [], nonces))
            digest, total = hashlib.sha256(), 0
            with self._fs.file(path / "data.bin", create=True) as output:
                def write(blob):
                    nonlocal total
                    output.write(blob); digest.update(blob); total += len(blob)
                def frame(blob):
                    write(struct.pack(">I", len(blob))); write(blob)
                write(MAGIC)
                frame(self._encrypt(scope, "metadata", raw, [len(raw), receipt["metadata_digest"]], nonces))
                for item in objects:
                    count = max(1, math.ceil(item.byte_size / CHUNK_SIZE))
                    plain_digest, cipher_digest, size = hashlib.sha256(), hashlib.sha256(), 0
                    with open_source(item) as source:
                        for index in range(count):
                            expected = min(CHUNK_SIZE, item.byte_size - size)
                            buffer = bytearray()
                            while len(buffer) < expected:
                                remaining = expected - len(buffer)
                                piece = source.read(remaining)
                                if not isinstance(piece, bytes) or not piece or len(piece) > remaining:
                                    raise _invalid()
                                buffer.extend(piece)
                            chunk = bytes(buffer)
                            plain_digest.update(chunk); size += len(chunk)
                            blob = self._encrypt(scope, "object", chunk, self._detail(item, index, count), nonces)
                            cipher_digest.update(blob); frame(blob)
                        if source.read(1) != b"":
                            raise _invalid()
                    if size != item.byte_size or plain_digest.hexdigest() != item.checksum_sha256:
                        raise _invalid()
                    receipt["objects"].append({"object_id": item.object_id, "plaintext_digest": item.checksum_sha256,
                        "byte_size": size, "ciphertext_digest": cipher_digest.hexdigest(), "chunks": count})
            receipt.update(data_digest=digest.hexdigest(), data_size=total)
            # 마지막 receipt 전에 보존 파일만 다시 읽어 길이/순서/AEAD/metadata를 검증한다.
            self._verify_data(scope, path, receipt)
            receipt["verified_at"] = datetime.now(timezone.utc).isoformat()
            self._write(path / "receipt.pending", self._encrypt(scope, "manifest", _json(receipt), [], nonces))
            self._fs.rename(path / "receipt.pending", path / "receipt.bin")
        return receipt

    def _verify_data(self, scope, path, receipt, sink=None):
        nonces = set()
        self._read_owner(scope, path, nonces)
        digest, size = hashlib.sha256(), 0
        with self._fs.file(path / "data.bin") as stream:
            def read(count):
                nonlocal size
                raw = stream.read(count)
                if len(raw) != count:
                    raise _invalid()
                digest.update(raw); size += count
                return raw
            def frame(limit):
                count = struct.unpack(">I", read(4))[0]
                if not 28 <= count <= limit + 28:
                    raise _invalid()
                return read(count)
            if read(len(MAGIC)) != MAGIC:
                raise _invalid()
            if type(receipt["metadata_size"]) is not int or not 0 < receipt["metadata_size"] <= MAX_METADATA_BYTES:
                raise _invalid()
            raw = self._decrypt(scope, "metadata", frame(MAX_METADATA_BYTES), [receipt["metadata_size"], receipt["metadata_digest"]], nonces)
            if len(raw) != receipt["metadata_size"] or hashlib.sha256(raw).hexdigest() != receipt["metadata_digest"]:
                raise _invalid()
            metadata = json.loads(raw)
            items = self._objects(tuple(BackupObjectInput(row["object_id"], row["plaintext_digest"], row["byte_size"]) for row in receipt["objects"]))
            if [item.object_id for item in items] != [row["object_id"] for row in receipt["objects"]]:
                raise _invalid()
            validate_metadata(scope, metadata, items)
            for item, row in zip(items, receipt["objects"], strict=True):
                count = max(1, math.ceil(item.byte_size / CHUNK_SIZE))
                if type(row["chunks"]) is not int or row["chunks"] != count:
                    raise _invalid()
                plain_digest, cipher_digest, plain_size = hashlib.sha256(), hashlib.sha256(), 0
                for index in range(count):
                    blob = frame(CHUNK_SIZE); cipher_digest.update(blob)
                    chunk = self._decrypt(scope, "object", blob, self._detail(item, index, count), nonces)
                    if len(chunk) != min(CHUNK_SIZE, item.byte_size - plain_size):
                        raise _invalid()
                    plain_digest.update(chunk); plain_size += len(chunk)
                    if sink is not None:
                        sink(item, index, chunk)
                if plain_size != item.byte_size or plain_digest.hexdigest() != item.checksum_sha256 or cipher_digest.hexdigest() != row["ciphertext_digest"]:
                    raise _invalid()
            if stream.read(1) or size != receipt["data_size"] or digest.hexdigest() != receipt["data_digest"]:
                raise _invalid()
        return VerifiedCapture(metadata, receipt)

    def _read_owner(self, scope, path, nonces):
        try:
            raw = self._decrypt(scope, "owner", self._read(path / "owner.bin", 16384), [], nonces)
            owner = json.loads(raw)
            if set(owner) != {"scope", "owner_id"} or owner["scope"] != scope.binding():
                raise _invalid()
            return _safe(owner["owner_id"])
        except (KeyError, TypeError, ValueError):
            raise _invalid() from None

    def _receipt(self, scope, path, expected_receipt):
        raw = self._decrypt(scope, "manifest", self._read(path / "receipt.bin", MAX_METADATA_BYTES), [], set())
        receipt = json.loads(raw)
        if (any(receipt.get(key) != value for key, value in scope.binding().items())
                or receipt.get("key_version") != self.key_version or receipt.get("key_reference") != self.key_reference
                or (expected_receipt is not None and receipt != expected_receipt)):
            raise _invalid()
        verified = datetime.fromisoformat(receipt["verified_at"])
        if verified.tzinfo is None:
            raise _invalid()
        return receipt

    def verify(self, scope, *, expected_receipt=None):
        try:
            with self._capture_dir(scope) as path:
                return self._verify_data(scope, path, self._receipt(scope, path, expected_receipt))
        except (OSError, KeyError, TypeError, ValueError, OverflowError):
            raise _invalid() from None

    def reconcile(self, scope, ledger_receipt_lookup):
        # callback은 정확 원장 조회다. 조회 실패/결과 불명확이면 artifact를 재생성/삭제하지 않는다.
        stored = ledger_receipt_lookup()
        local = self.verify(scope)
        if stored is None:
            return "unreferenced"
        if stored != local.receipt:
            raise _invalid()
        return "referenced"

    def exists(self, scope):
        with self._fs.pin(self.root):
            if self._fs.identity(self.root) != self._root_identity:
                raise _invalid()
            path = self.root / self._directory_name(scope)
            return self._fs.exists(path)

    def discard_unreferenced(self, scope, *, owner_id, ledger_guard):
        """한 capture의 부분 data만 정리한다. owner marker는 동일 key 재생성 방지용으로 유지한다."""
        _safe(owner_id)
        with ledger_guard() as referenced:
            if referenced is not None:
                raise _invalid()
            with self._fs.pin(self.root):
                if self._fs.identity(self.root) != self._root_identity:
                    raise _invalid()
                path = self.root / self._directory_name(scope)
                with self._fs.pin(path):
                    if self._read_owner(scope, path, set()) != owner_id:
                        raise _invalid()
                    names = self._fs.names(path)
                    if not names <= {"owner.bin", "data.bin", "receipt.pending", "receipt.bin"}:
                        raise _invalid()
                    # 모든 대상의 링크/종류를 먼저 검사한다. 알 수 없는 파일/하위 경로를 지우지 않는다.
                    for name in names:
                        with self._fs.file(path / name):
                            pass
                    for name in sorted(names - {"owner.bin"}):
                        self._fs.unlink(path / name)

    def restore(self, scope, destination, *, expected_receipt):
        # DB materialization과는 별개인 명시 주입 전용 신규 디렉터리. 기존 경로는 절대 덮지 않는다.
        destination = Path(destination)
        outputs = {}
        with self._fs.pin(destination.parent), self._capture_dir(scope) as path:
            receipt = self._receipt(scope, path, expected_receipt)
            self._verify_data(scope, path, receipt)
            try:
                self._fs.mkdir(destination)
            except OSError:
                raise _invalid() from None
            from contextlib import ExitStack
            with self._fs.pin(destination), ExitStack() as stack:
                streams = {}
                def sink(item, index, chunk):
                    if index == 0:
                        # Object ID는 파일 경로로 신뢰하지 않는다(Windows ADS/예약 이름 포함).
                        target = destination / (hashlib.sha256(item.object_id.encode()).hexdigest() + ".bin")
                        streams[item.object_id] = stack.enter_context(self._fs.file(target, create=True))
                        outputs[item.object_id] = target
                    streams[item.object_id].write(chunk)
                self._verify_data(scope, path, receipt, sink)
        return outputs
