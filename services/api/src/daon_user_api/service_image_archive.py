"""운영자 전용 service-image-archive-v1. 비압축 legacy Docker-save만 지원.

호스트에 tar를 추출하지 않는다. 실제 Docker 호출은 FixedDockerCLI의 고정 명령만
소비한다. 암호화 보존/실제 load 검증과 단순 daemon cache 존재를 구분한다.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import selectors
import stat
import struct
import subprocess
import sys
import tarfile
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .recovery_capture import _PosixFiles, _WindowsFiles
from .service_snapshot import BoundedSnapshotReader, CHUNK, SnapshotError, SnapshotRuntimeRef, _safe, _hash, _json


KIND = "service-image-archive-v1"
MAGIC = b"DAON-SERVICE-IMAGE-ARCHIVE-V1\n"
_ROLES = {"api", "web", "postgres", "object_storage", "asr"}
_PROOF_SEAL = object()


def _private_stat(info, uid, *, root=False):
    expected = 0o700 if root else 0o600
    kind = stat.S_ISDIR(info.st_mode) if root else stat.S_ISREG(info.st_mode)
    if not kind or stat.S_IMODE(info.st_mode) != expected or info.st_uid != uid or (not root and info.st_nlink != 1):
        raise SnapshotError("SNAPSHOT_IMAGE_PROTECTION_INVALID")


def _check(deadline, cancelled):
    if cancelled(): raise SnapshotError("SNAPSHOT_CANCELLED")
    if time.monotonic() >= deadline: raise SnapshotError("SNAPSHOT_TIMEOUT")


def _exact_json(raw):
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result: raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
            result[name] = value
        return result
    try:
        if not raw or len(raw) > 1048576: raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(SnapshotError("SNAPSHOT_IMAGE_INVALID")))
    except SnapshotError: raise
    except Exception: raise SnapshotError("SNAPSHOT_IMAGE_INVALID") from None


@dataclass(frozen=True, repr=False)
class ImageArchiveSpec:
    source_instance_ref: str
    archive_id: str
    roles: tuple[str, ...]
    image_digest: str
    code_revision: str | None
    key_version: str
    key_reference: str
    maximum_archive_bytes: int
    duration_seconds: int

    def __post_init__(self):
        for value in (self.source_instance_ref, self.archive_id, self.key_version, self.key_reference): _safe(value)
        if (type(self.roles) is not tuple or not self.roles or len(set(self.roles)) != len(self.roles)
                or not set(self.roles) <= _ROLES): raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
        if type(self.image_digest) is not str or not self.image_digest.startswith("sha256:"): raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
        _hash(self.image_digest[7:])
        if set(self.roles) & {"api", "web"}:
            if type(self.code_revision) is not str or re.fullmatch("[0-9a-f]{40}", self.code_revision) is None: raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
        elif self.code_revision is not None: raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
        if any(type(v) is not int or v <= 0 for v in (self.maximum_archive_bytes, self.duration_seconds)): raise SnapshotError("SNAPSHOT_IMAGE_INVALID")


@dataclass(frozen=True, repr=False)
class ImageArchiveReceipt:
    kind: str
    spec_digest: str
    owner_id: str
    archive_bytes: int
    archive_sha256: str
    chunks: int
    os: str
    architecture: str
    layer_digests: tuple[str, ...]
    repo_tags: tuple[str, ...]
    completed_at: str


class LoadedImageProof:
    """메모리 수명의 1회 증명. JSON/boolean/기존 proof 파일을 발급 입력으로 받지 않는다."""
    def __init__(self, seal, spec, receipt, deadline):
        if seal is not _PROOF_SEAL: raise SnapshotError("SNAPSHOT_IMAGE_UNPROVEN")
        self._spec, self._digest, self._deadline, self._used = spec, hashlib.sha256(_json(asdict(receipt))).hexdigest(), deadline, False

    def reference(self, ref_id):
        if self._used or time.monotonic() >= self._deadline: raise SnapshotError("SNAPSHOT_IMAGE_UNPROVEN")
        return SnapshotRuntimeRef(_safe(ref_id), "reconstructible", self._digest, 0)

    def verify_once(self, spec, ref):
        if (self._used or time.monotonic() >= self._deadline or spec != self._spec or type(ref) is not SnapshotRuntimeRef
                or ref.mode != "reconstructible" or ref.byte_size != 0 or ref.digest != self._digest): raise SnapshotError("SNAPSHOT_IMAGE_UNPROVEN")
        self._used = True
        return True


def _identity(value, spec):
    if type(value) is dict and not set(spec.roles) & {"api", "web"}:
        value = {**value, "code_revision": None}
    if (type(value) is not dict or set(value) != {"id", "os", "architecture", "code_revision", "layers"}
            or value["id"] != spec.image_digest or value["code_revision"] != spec.code_revision
            or value["os"] != "linux" or value["architecture"] not in {"amd64", "arm64"}
            or type(value["layers"]) is not list or not 1 <= len(value["layers"]) <= 128
            or any(type(v) is not str or not re.fullmatch("sha256:[0-9a-f]{64}", v) for v in value["layers"])):
        raise SnapshotError("SNAPSHOT_IMAGE_MISMATCH")
    return value


def _read_exact(source, count):
    value = bytearray()
    while len(value) < count:
        chunk = source.read(min(65536, count - len(value)))
        if not isinstance(chunk, bytes) or not chunk: raise SnapshotError("SNAPSHOT_IMAGE_TRUNCATED")
        value.extend(chunk)
    return bytes(value)


def _safe_member(name):
    path = PurePosixPath(name)
    if (not name or len(name) > 1024 or path.is_absolute() or ".." in path.parts or "." in name.split("/")
            or "\\" in name or ":" in name or any(ord(c) < 32 for c in name)
            or str(path) != name.rstrip("/")): raise SnapshotError("SNAPSHOT_IMAGE_TAR_UNSAFE")
    return name.rstrip("/")


def _validate_tar(source, spec):
    """512-byte header를 직접 검사해 PAX/GNU longname/link/device를 자동 해석하지 않는다."""
    members, small, digests = {}, {}, {}
    count = 0
    while True:
        header = _read_exact(source, 512)
        if header == bytes(512):
            if _read_exact(source, 512) != bytes(512): raise SnapshotError("SNAPSHOT_IMAGE_TAR_UNSAFE")
            while value := source.read(65536):
                if value.strip(b"\0"): raise SnapshotError("SNAPSHOT_IMAGE_TAR_UNSAFE")
            break
        try: item = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
        except Exception: raise SnapshotError("SNAPSHOT_IMAGE_TAR_UNSAFE") from None
        name = _safe_member(item.name)
        count += 1
        if (count > 4096 or name in members or not (item.isfile() or item.isdir())
                or item.linkname or item.pax_headers or item.size < 0 or item.size > spec.maximum_archive_bytes
                or (item.isdir() and item.size != 0)): raise SnapshotError("SNAPSHOT_IMAGE_TAR_UNSAFE")
        members[name] = (item.size, item.isdir())
        digest, remaining = hashlib.sha256(), item.size
        metadata = bytearray() if name.endswith(".json") or name == "repositories" else None
        while remaining:
            value = _read_exact(source, min(65536, remaining)); remaining -= len(value); digest.update(value)
            if metadata is not None:
                if len(metadata) + len(value) > 1048576: raise SnapshotError("SNAPSHOT_IMAGE_TAR_UNSAFE")
                metadata.extend(value)
        padding = (-item.size) % 512
        if padding and _read_exact(source, padding) != bytes(padding): raise SnapshotError("SNAPSHOT_IMAGE_TAR_UNSAFE")
        digests[name] = digest.hexdigest()
        if metadata is not None: small[name] = bytes(metadata)
    manifest = _exact_json(small.get("manifest.json", b""))
    if (type(manifest) is not list or len(manifest) != 1 or type(manifest[0]) is not dict
            or set(manifest[0]) != {"Config", "RepoTags", "Layers"}): raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
    manifest = manifest[0]
    config_name = spec.image_digest[7:] + ".json"
    layers, tags = manifest["Layers"], manifest["RepoTags"]
    if (manifest["Config"] != config_name or digests.get(config_name) != spec.image_digest[7:]
            or type(layers) is not list or not 1 <= len(layers) <= 128 or len(set(layers)) != len(layers)
            or any(type(v) is not str or _safe_member(v) != v or not v.endswith("/layer.tar") for v in layers)
            or (tags is not None and (type(tags) is not list or len(tags) > 64 or len(set(tags)) != len(tags)
                or any(type(t) is not str or not t or len(t) > 256 or any(ord(c) < 33 for c in t) for t in tags)))):
        raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
    config = _exact_json(small.get(config_name, b""))
    try:
        if config["rootfs"]["type"] != "layers": raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
        actual = ["sha256:" + digests[name] for name in layers]
        if config["rootfs"]["diff_ids"] != actual: raise SnapshotError("SNAPSHOT_IMAGE_MISMATCH")
        revision = config.get("config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
        identity = _identity({"id": "sha256:" + digests[config_name], "os": config["os"], "architecture": config["architecture"],
            "code_revision": revision if set(spec.roles) & {"api", "web"} else None, "layers": actual}, spec)
    except SnapshotError: raise
    except Exception: raise SnapshotError("SNAPSHOT_IMAGE_INVALID") from None
    allowed = {config_name, "manifest.json"}
    for name in layers:
        parent = str(PurePosixPath(name).parent)
        allowed.update((name, parent, parent + "/VERSION", parent + "/json"))
        if parent + "/VERSION" in members:
            if members[parent + "/VERSION"][0] > 16: raise SnapshotError("SNAPSHOT_IMAGE_TAR_UNSAFE")
    # repositories는 공유 tag를 바꿀 별도 맵이므로 첫 지원 형식에서는 명시 거부한다.
    if set(members) - allowed: raise SnapshotError("SNAPSHOT_IMAGE_TAR_UNSUPPORTED")
    return identity, tuple(tags or ())


class _PlainReader:
    def __init__(self, chunks): self._chunks, self._pending, self.eof, self.total = iter(chunks), b"", False, 0
    def read(self, maximum):
        if type(maximum) is not int or not 0 < maximum <= 65536: raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
        if not self._pending and not self.eof:
            try: self._pending = next(self._chunks)
            except StopIteration: self.eof = True
        value, self._pending = self._pending[:maximum], self._pending[maximum:]
        self.total += len(value)
        return value


class ImageArchiveStore:
    def __init__(self, root, *, master_key):
        if type(master_key) is not bytes or len(master_key) < 32: raise SnapshotError("SNAPSHOT_IMAGE_KEY_UNAVAILABLE")
        self.root, self._key = Path(root), master_key
        if not self.root.is_absolute() or ".." in self.root.parts or self.root == Path(self.root.anchor): raise SnapshotError()
        self._files = _WindowsFiles() if os.name == "nt" else _PosixFiles()
        self._guard = threading.Lock()
        with self._files.pin(self.root) as descriptor:
            if os.name == "posix": _private_stat(os.fstat(descriptor), os.geteuid(), root=True)
            self._root_identity = self._files.identity(self.root)

    def _cipher(self, spec, purpose):
        return AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
            info=_json([KIND, purpose, asdict(spec)])).derive(self._key))

    def _seal(self, spec, purpose, raw, aad, nonces):
        nonce = secrets.token_bytes(12)
        if nonce in nonces: raise SnapshotError("SNAPSHOT_IMAGE_NONCE_REUSE")
        nonces.add(nonce)
        return nonce + self._cipher(spec, purpose).encrypt(nonce, raw, aad)

    def _open(self, spec, purpose, raw, aad, nonces):
        if len(raw) < 28 or raw[:12] in nonces: raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
        nonces.add(raw[:12])
        try: return self._cipher(spec, purpose).decrypt(raw[:12], raw[12:], aad)
        except Exception: raise SnapshotError("SNAPSHOT_IMAGE_AUTH_FAILED") from None

    @contextmanager
    def _operation(self, spec):
        if type(spec) is not ImageArchiveSpec: raise SnapshotError()
        if not self._guard.acquire(blocking=False): raise SnapshotError("SNAPSHOT_BUSY")
        lock, preserve = self.root / ".image-lock.bin", [False]
        created = False
        try:
            with self._files.pin(self.root):
                if self._files.identity(self.root) != self._root_identity: raise SnapshotError()
                with self._files.file(lock, create=True) as output: output.write(secrets.token_bytes(32))
                created = True
                try: yield preserve
                except BaseException as error:
                    if not isinstance(error, Exception) or isinstance(error, SnapshotError) and error.code.endswith("CLEANUP_UNCERTAIN"):
                        preserve[0] = True
                    raise
                finally:
                    if created and not preserve[0]: self._files.unlink(lock)
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_IMAGE_UNAVAILABLE") from None
        finally: self._guard.release()

    def _write(self, path, raw):
        with self._files.file(path, create=True) as output: output.write(raw)

    def _read(self, path, maximum):
        with self._files.file(path) as source:
            if os.name == "posix": _private_stat(os.fstat(source.fileno()), os.geteuid())
            value = source.read(maximum + 1)
            if len(value) > maximum: raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
            return value

    def _chunks(self, spec, folder, receipt=None, *, deadline, cancelled):
        total, count, digest, nonces = 0, 0, hashlib.sha256(), set()
        with self._files.file(folder / "data.bin") as source:
            if os.name == "posix": _private_stat(os.fstat(source.fileno()), os.geteuid())
            if source.read(len(MAGIC)) != MAGIC: raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
            while header := source.read(4):
                _check(deadline, cancelled)
                if len(header) != 4: raise SnapshotError("SNAPSHOT_IMAGE_TRUNCATED")
                length = struct.unpack(">I", header)[0]
                if not 28 < length <= CHUNK + 28: raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
                raw = _read_exact(source, length)
                value = self._open(spec, "archive", raw, _json([KIND, spec.archive_id, count]), nonces)
                total += len(value); count += 1; digest.update(value)
                if total > spec.maximum_archive_bytes: raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
                yield value
        if (not count or receipt is not None and (total, count, digest.hexdigest()) != (
                receipt.archive_bytes, receipt.chunks, receipt.archive_sha256)): raise SnapshotError("SNAPSHOT_IMAGE_MISMATCH")

    def _verify(self, spec, folder, *, deadline, cancelled):
        try:
            body = self._open(spec, "receipt", self._read(folder / "receipt.bin", 1048576), _json([KIND, "receipt"]), set())
            value = _exact_json(body)
            if type(value) is not dict or set(value) != set(ImageArchiveReceipt.__dataclass_fields__): raise SnapshotError()
            value["layer_digests"], value["repo_tags"] = tuple(value["layer_digests"]), tuple(value["repo_tags"])
            receipt = ImageArchiveReceipt(**value)
            if (receipt.kind != KIND or receipt.spec_digest != hashlib.sha256(_json(asdict(spec))).hexdigest()
                    or type(receipt.archive_bytes) is not int or not 0 < receipt.archive_bytes <= spec.maximum_archive_bytes
                    or type(receipt.chunks) is not int or receipt.chunks < 1): raise SnapshotError()
            owner = self._open(spec, "owner", self._read(folder / "owner.bin", 4096), _json([KIND, "owner"]), set())
            if owner.decode() != receipt.owner_id: raise SnapshotError()
            source = _PlainReader(self._chunks(spec, folder, receipt, deadline=deadline, cancelled=cancelled))
            identity, tags = _validate_tar(source, spec)
            if (identity["os"], identity["architecture"], tuple(identity["layers"]), tags) != (
                    receipt.os, receipt.architecture, receipt.layer_digests, receipt.repo_tags): raise SnapshotError()
            return receipt
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_IMAGE_UNAVAILABLE") from None

    def capture(self, spec, *, cli, cancelled):
        deadline = time.monotonic() + spec.duration_seconds
        with self._operation(spec) as preserve:
            folder, owner, created = self.root / spec.archive_id, secrets.token_hex(16), False
            _check(deadline, cancelled)
            try:
                before = _identity(cli.inspect(spec.image_digest, deadline=deadline, cancelled=cancelled), spec)
                tags_before = cli.tag_inventory(deadline=deadline, cancelled=cancelled)
                self._files.mkdir(folder); created = True
                with self._files.pin(folder):
                    nonces, total, count, digest = set(), 0, 0, hashlib.sha256()
                    self._write(folder / "owner.bin", self._seal(spec, "owner", owner.encode(), _json([KIND, "owner"]), nonces))
                    with cli.save(spec.image_digest, deadline=deadline, cancelled=cancelled) as source:
                        if not isinstance(source, BoundedSnapshotReader): raise SnapshotError("SNAPSHOT_BOUNDED_PORT_REQUIRED")
                        with self._files.file(folder / "data.bin", create=True) as output:
                            output.write(MAGIC)
                            while True:
                                block = bytearray()
                                while len(block) < CHUNK:
                                    _check(deadline, cancelled)
                                    value = source.read_bounded(CHUNK - len(block), deadline=deadline, cancelled=cancelled)
                                    if type(value) is not bytes or len(value) > CHUNK - len(block): raise SnapshotError()
                                    if not value: break
                                    block.extend(value)
                                    if total + len(block) > spec.maximum_archive_bytes: raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
                                if not block: break
                                total += len(block); digest.update(block)
                                blob = self._seal(spec, "archive", bytes(block), _json([KIND, spec.archive_id, count]), nonces)
                                output.write(struct.pack(">I", len(blob))); output.write(blob); count += 1
                    plain = _PlainReader(self._chunks(spec, folder, deadline=deadline, cancelled=cancelled))
                    identity, tags = _validate_tar(plain, spec)
                    if identity != before: raise SnapshotError("SNAPSHOT_IMAGE_MISMATCH")
                    if any(tags_before.get(tag) != spec.image_digest for tag in tags): raise SnapshotError("SNAPSHOT_IMAGE_TAG_CONFLICT")
                    if (_identity(cli.inspect(spec.image_digest, deadline=deadline, cancelled=cancelled), spec) != before
                            or cli.tag_inventory(deadline=deadline, cancelled=cancelled) != tags_before): raise SnapshotError("SNAPSHOT_IMAGE_CHANGED")
                    _check(deadline, cancelled)
                    receipt = ImageArchiveReceipt(KIND, hashlib.sha256(_json(asdict(spec))).hexdigest(), owner, total,
                        digest.hexdigest(), count, identity["os"], identity["architecture"], tuple(identity["layers"]), tags, datetime.now(timezone.utc).isoformat())
                    self._write(folder / "receipt.bin", self._seal(spec, "receipt", _json(asdict(receipt)), _json([KIND, "receipt"]), nonces))
                    return self._verify(spec, folder, deadline=deadline, cancelled=cancelled)
            except BaseException as error:
                if not isinstance(error, Exception) or isinstance(error, SnapshotError) and error.code.endswith("CLEANUP_UNCERTAIN"):
                    preserve[0] = True
                elif created:
                    try:
                        with self._files.pin(folder):
                            if self._files.exists(folder / "receipt.bin"): preserve[0] = True
                            else:
                                marker = self._open(spec, "owner", self._read(folder / "owner.bin", 4096), _json([KIND, "owner"]), set())
                                if marker.decode() != owner or self._files.names(folder) - {"owner.bin", "data.bin"}: raise SnapshotError()
                                for name in self._files.names(folder): self._files.unlink(folder / name)
                    except BaseException:
                        preserve[0] = True
                        raise
                if isinstance(error, SnapshotError) or not isinstance(error, Exception): raise
                raise SnapshotError("SNAPSHOT_IMAGE_UNAVAILABLE") from None

    def verify(self, spec, *, deadline=None, cancelled=lambda: False):
        limit = time.monotonic() + spec.duration_seconds
        if deadline is not None:
            if type(deadline) not in (int, float) or not __import__("math").isfinite(deadline): raise SnapshotError("SNAPSHOT_IMAGE_INVALID")
            limit = min(limit, deadline)
        with self._operation(spec), self._files.pin(self.root / spec.archive_id):
            return self._verify(spec, self.root / spec.archive_id, deadline=limit, cancelled=cancelled)

    def reconstruct(self, spec, *, cli, cancelled):
        deadline, folder = time.monotonic() + spec.duration_seconds, self.root / spec.archive_id
        with self._operation(spec), self._files.pin(folder):
            receipt = self._verify(spec, folder, deadline=deadline, cancelled=cancelled)
            tags = cli.tag_inventory(deadline=deadline, cancelled=cancelled)
            if any(tag in tags and tags[tag] != spec.image_digest for tag in receipt.repo_tags): raise SnapshotError("SNAPSHOT_IMAGE_TAG_CONFLICT")
            expected_tags = {**tags, **{tag: spec.image_digest for tag in receipt.repo_tags}}
            source = _PlainReader(self._chunks(spec, folder, receipt, deadline=deadline, cancelled=cancelled))
            cli.load(source, deadline=deadline, cancelled=cancelled)
            if source.read(1) or not source.eof or source.total != receipt.archive_bytes: raise SnapshotError("SNAPSHOT_IMAGE_LOAD_UNPROVEN")
            identity = _identity(cli.inspect(spec.image_digest, deadline=deadline, cancelled=cancelled), spec)
            if ((identity["os"], identity["architecture"], tuple(identity["layers"])) != (receipt.os, receipt.architecture, receipt.layer_digests)
                    or cli.tag_inventory(deadline=deadline, cancelled=cancelled) != expected_tags): raise SnapshotError("SNAPSHOT_IMAGE_CHANGED")
            if self._verify(spec, folder, deadline=deadline, cancelled=cancelled) != receipt: raise SnapshotError("SNAPSHOT_IMAGE_CHANGED")
            _check(deadline, cancelled)
            return LoadedImageProof(_PROOF_SEAL, spec, receipt, deadline)


class _SelectIO:
    @staticmethod
    def prepare(child):
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None: os.set_blocking(stream.fileno(), False)

    @staticmethod
    def ready(child, channels, write, timeout):
        with selectors.DefaultSelector() as selector:
            for channel in channels: selector.register(getattr(child, channel), selectors.EVENT_READ, channel)
            if write: selector.register(child.stdin, selectors.EVENT_WRITE, "stdin")
            return [key.data for key, _ in selector.select(timeout)]

    @staticmethod
    def read(child, channel, count): return os.read(getattr(child, channel).fileno(), count)

    @staticmethod
    def write(child, value): return os.write(child.stdin.fileno(), value)


class OwnedDockerCommand(BoundedSnapshotReader):
    """Popen이 만든 본인 child와 pipe만 관리한다. 전역 process/group 종료는 없다."""
    def __init__(self, child, *, deadline, cancelled, _io=None):
        self.child, self.deadline, self.cancelled, self.io = child, deadline, cancelled, _io or _SelectIO()
        self.channels, self.pending, self.closed, self.input_closed = {"stdout", "stderr"}, b"", False, child.stdin is None
        try: self.io.prepare(child)
        except BaseException as error:
            self.abort_owned()
            if not isinstance(error, Exception): raise
            raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED") from None

    def __enter__(self): return self
    def __exit__(self, *args): self.abort_owned()

    def _check(self, deadline, cancelled):
        _check(min(self.deadline, deadline), lambda: self.cancelled() or cancelled())

    def _read(self, channel, maximum):
        try: raw = self.io.read(self.child, channel, min(maximum, 65536 if channel == "stdout" else 4096))
        except BlockingIOError: return None
        if type(raw) is not bytes or len(raw) > maximum: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED")
        if not raw: self.channels.discard(channel)
        return raw

    def read_bounded(self, maximum, *, deadline, cancelled):
        if type(maximum) is not int or not 0 < maximum <= CHUNK: raise SnapshotError()
        while not self.pending:
            self._check(deadline, cancelled)
            if not self.channels:
                result = self.child.poll()
                if result is not None:
                    if result != 0: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED")
                    return b""
                time.sleep(.005)
                continue
            for channel in self.io.ready(self.child, self.channels, False, .05):
                value = self._read(channel, 65536 if channel == "stdout" else 4096)
                if channel == "stdout" and value: self.pending = value
                # stderr는 최대4KiB씩 즉시 폐기한다. 원문 보존/예외 반사0.
        value, self.pending = self.pending[:maximum], self.pending[maximum:]
        self._check(deadline, cancelled)
        return value

    def collect(self, maximum):
        result = bytearray()
        while True:
            value = self.read_bounded(min(CHUNK, maximum - len(result) + 1), deadline=self.deadline, cancelled=self.cancelled)
            if not value: break
            result.extend(value)
            if len(result) > maximum: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED")
        return bytes(result)

    def send(self, source):
        pending, ended, output = b"", False, 0
        while True:
            self._check(self.deadline, self.cancelled)
            if not pending and not ended:
                pending = source.read(65536)
                if type(pending) is not bytes or len(pending) > 65536: raise SnapshotError()
                ended = not pending
            if ended and not self.input_closed:
                self.child.stdin.close(); self.input_closed = True
            if not self.channels and ended and not pending:
                result = self.child.poll()
                if result is not None:
                    if result != 0: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED")
                    return
                time.sleep(.005)
                continue
            for channel in self.io.ready(self.child, self.channels, bool(pending), .05):
                if channel == "stdin":
                    try: sent = self.io.write(self.child, pending)
                    except BlockingIOError: continue
                    if type(sent) is not int or not 0 < sent <= len(pending): raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED")
                    pending = pending[sent:]
                else:
                    value = self._read(channel, 65536 if channel == "stdout" else 4096)
                    if channel == "stdout" and value:
                        output += len(value)
                        if output > 1048576: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED")

    def abort_owned(self):
        if self.closed: return
        uncertain = False
        try:
            if self.child.poll() is None:
                self.child.terminate()
                try: self.child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    try: self.child.wait(timeout=2)
                    except subprocess.TimeoutExpired: uncertain = True
        except Exception: uncertain = True
        finally:
            for stream in (None if self.input_closed else self.child.stdin, self.child.stdout, self.child.stderr):
                if stream is not None:
                    try: stream.close()
                    except Exception: uncertain = True
            self.pending, self.closed = b"", True
        if uncertain: raise SnapshotError("SNAPSHOT_IMAGE_CHILD_CLEANUP_UNCERTAIN") from None


@contextmanager
def _sealed_executable(path, digest):
    from .service_snapshot_postgres import _validate_ancestor_stat
    if (os.name != "posix" or not sys.platform.startswith("linux") or not hasattr(os, "memfd_create")
            or not hasattr(os, "MFD_ALLOW_SEALING")): raise SnapshotError("SNAPSHOT_IMAGE_PLATFORM_UNSUPPORTED")
    import fcntl
    files, fd = _PosixFiles(), None
    try:
        with ExitStack() as lifetime:
            for ancestor in reversed((path.parent, *path.parent.parents)):
                directory = lifetime.enter_context(files.pin(ancestor)); _validate_ancestor_stat(os.fstat(directory), os.geteuid())
            source = lifetime.enter_context(files.file(path)); info = os.fstat(source.fileno())
            if (info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022 or not info.st_mode & 0o111
                    or not 0 < info.st_size <= 32 * 1024 * 1024): raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
            fd = os.memfd_create("daon-fixed-docker", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
            actual = hashlib.sha256()
            while value := source.read(65536):
                actual.update(value); view = memoryview(value)
                while view:
                    count = os.write(fd, view)
                    if count <= 0: raise SnapshotError()
                    view = view[count:]
            if actual.hexdigest() != digest: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
            os.fchmod(fd, 0o500)
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            yield "/proc/self/fd/" + str(fd), (fd,)
    except SnapshotError: raise
    except Exception: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED") from None
    finally:
        if fd is not None: os.close(fd)


class FixedDockerCLI:
    def __init__(self, executable, executable_sha256, *, _protected=None, _spawn=None, _command=None):
        self.executable, self.digest = Path(executable), _hash(executable_sha256)
        if not self.executable.is_absolute() or ".." in self.executable.parts: raise SnapshotError()
        self._protected = _protected or (lambda: _sealed_executable(self.executable, self.digest))
        self._spawn, self._command = _spawn or subprocess.Popen, _command or OwnedDockerCommand

    @contextmanager
    def _run(self, args, *, deadline, cancelled, input_pipe=False):
        _check(deadline, cancelled)
        try:
            with self._protected() as (executable, fds):
                child = self._spawn([executable, "--host=unix:///var/run/docker.sock", *args], shell=False,
                    stdin=subprocess.PIPE if input_pipe else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env={}, pass_fds=fds)
                with self._command(child, deadline=deadline, cancelled=cancelled) as command: yield command
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED") from None

    def inspect(self, image, *, deadline, cancelled):
        if type(image) is not str or re.fullmatch("sha256:[0-9a-f]{64}", image) is None: raise SnapshotError()
        projection = '{{json .Id}}\n{{json .Os}}\n{{json .Architecture}}\n{{json (index .Config.Labels "org.opencontainers.image.revision")}}\n{{json .RootFS.Layers}}'
        with self._run(["image", "inspect", "--format", projection, image], deadline=deadline, cancelled=cancelled) as command:
            try:
                rows = command.collect(1048576).splitlines()
                if len(rows) != 5: raise SnapshotError()
                identity, system, architecture, revision, layers = [_exact_json(row) for row in rows]
                return {"id": identity, "os": system, "architecture": architecture, "code_revision": revision, "layers": layers}
            except SnapshotError: raise
            except Exception: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED") from None

    def tag_inventory(self, *, deadline, cancelled):
        with self._run(["image", "ls", "--all", "--no-trunc", "--format", '{{json .Repository}} {{json .Tag}} {{json .ID}}'],
                deadline=deadline, cancelled=cancelled) as command:
            rows = command.collect(1048576).splitlines()
        if len(rows) > 4096: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED")
        result = {}
        for line in rows:
            try: repository, tag, image = json.loads(b"[" + line.replace(b"\" \"", b'","') + b"]")
            except Exception: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED") from None
            if not re.fullmatch("sha256:[0-9a-f]{64}", image): raise SnapshotError()
            if repository == "<none>" or tag == "<none>": continue
            name = repository + ":" + tag
            if name in result: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED")
            result[name] = image
        return result

    @contextmanager
    def save(self, image, *, deadline, cancelled):
        if type(image) is not str or re.fullmatch("sha256:[0-9a-f]{64}", image) is None: raise SnapshotError()
        with self._run(["image", "save", image], deadline=deadline, cancelled=cancelled) as command: yield command

    def load(self, source, *, deadline, cancelled):
        with self._run(["image", "load", "--quiet"], deadline=deadline, cancelled=cancelled, input_pipe=True) as command:
            command.send(source)

    def containers(self, *, deadline, cancelled):
        with self._run(["container", "ls", "--all", "--quiet", "--no-trunc"], deadline=deadline, cancelled=cancelled) as command:
            ids = command.collect(65536).decode("ascii").splitlines()
        if len(ids) > 64 or len(set(ids)) != len(ids) or any(re.fullmatch("[0-9a-f]{64}", value) is None for value in ids): raise SnapshotError()
        projection = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
            '"running":{{json .State.Running}},"exit_code":{{json .State.ExitCode}},"finished_at":{{json .State.FinishedAt}},'
            '"started_at":{{json .State.StartedAt}},"pid":{{json .State.Pid}},"mounts":{{json .Mounts}},'
            '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
            '"networks":{{json .NetworkSettings.Networks}},"ports":{{json .NetworkSettings.Ports}}}')
        result = []
        for identity in ids:
            with self._run(["container", "inspect", "--format", projection, identity], deadline=deadline, cancelled=cancelled) as command:
                value = _exact_json(command.collect(1048576))
            if type(value) is not dict or value.get("id") != identity: raise SnapshotError("SNAPSHOT_IMAGE_COMMAND_FAILED")
            result.append(value)
        return tuple(result)
