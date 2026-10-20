"""R1-B 내부 selected-source-lineage-v3 보존 포트.

선택 원장 해석/현재 세션 검증은 주입한 R1 재조회 책임이다. 보존 receipt는 DB commit,
복원 materialization 또는 공개 API 성공이 아니다. 기존 v1/v2 형식은 소비하지 않는다.
OS 보호 file port만 재사용하며 root/key 탐색, DB 연결, 원본 locator 저장은 하지 않는다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import struct
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .data_canon import transition_allowed
from .recovery import RecoveryContext, RecoveryError, _safe, _safe_canon_id
from .recovery_capture import _PosixFiles, _WindowsFiles
from .recovery_lineage import LineageSelection, _COLUMNS, _EVENT, _canon, _json, _timestamp


KIND = "selected-source-lineage-v3"
MAGIC = b"DAON-SELECTED-SOURCE-LINEAGE-V3\n"
CHUNK_SIZE = 4 * 1024 * 1024
MAX_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
_TABLES = tuple(name for name in _COLUMNS if name not in {"deletion_requests", "legal_holds"})


def _invalid(): return RecoveryError("BACKUP_MANIFEST_INVALID", 409)
def _large(): return RecoveryError("BACKUP_SELECTION_TOO_LARGE", 413)
def _unavailable(): return RecoveryError("RESOURCE_UNAVAILABLE", 503)


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value): raise _invalid()
    return value


def _loads(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise _invalid()
            result[key] = value
        return result
    try:
        result = json.loads(raw, object_pairs_hook=unique)
        if _json(result) != raw: raise _invalid()
        return result
    except (TypeError, ValueError, UnicodeError, RecursionError): raise _invalid() from None


@dataclass(frozen=True, slots=True, repr=False)
class LineageCaptureScope:
    capture_id: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    notebook_id: str
    root_source_version_ids: tuple[str, ...]
    dependency_source_version_ids: tuple[str, ...]
    dependency_source_ids: tuple[str, ...]
    closure_fingerprint: str
    schema_revision: str
    policy_version: str
    retention_watermark: str
    authorization_fingerprint: str

    def __post_init__(self):
        for name in ("capture_id", "tenant_id", "workspace_id", "actor_id", "notebook_id", "schema_revision", "policy_version"):
            _safe(getattr(self, name))
        name = self.capture_id
        if (any(c in name for c in ":/\\") or name.endswith(".")
                or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}): raise _invalid()
        for field in ("closure_fingerprint", "retention_watermark", "authorization_fingerprint"): _digest(getattr(self, field))
        for field in ("root_source_version_ids", "dependency_source_version_ids", "dependency_source_ids"):
            items = getattr(self, field)
            if not isinstance(items, tuple) or len(items) > 100 or len(set(items)) != len(items): raise _invalid()
            for item in items: (_safe if field == "root_source_version_ids" else _safe_canon_id)(item)
            if items != tuple(sorted(items)): raise _invalid()
        if (not self.root_source_version_ids or len(self.root_source_version_ids) + len(self.dependency_source_version_ids) > 100
                or set(self.root_source_version_ids) & set(self.dependency_source_version_ids)): raise _invalid()

    @classmethod
    def from_selection(cls, context, selection, *, capture_id):
        if not isinstance(context, RecoveryContext) or not isinstance(selection, LineageSelection): raise _invalid()
        return cls(capture_id, context.tenant_id, context.workspace_id, context.actor_id, selection._selection.notebook_id,
            selection.root_source_version_ids, selection.dependency_source_version_ids, selection.dependency_source_ids,
            selection.fingerprint, selection.schema_revision, context.policy_version, selection.retention_watermark,
            selection.authorization_fingerprint)

    def binding(self):
        value = asdict(self)
        for field in ("root_source_version_ids", "dependency_source_version_ids", "dependency_source_ids"): value[field] = list(value[field])
        return {"kind": KIND, "format_version": 3, **value}


@dataclass(frozen=True, slots=True, repr=False)
class _Object:
    object_id: str
    checksum_sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedLineageCapture:
    metadata: dict
    receipt: dict


def _fingerprint(scope, raw):
    return hashlib.sha256(_json({"kind": KIND, "tenant_id": scope.tenant_id, "workspace_id": scope.workspace_id,
        "actor_id": scope.actor_id, "policy_version": scope.policy_version, "notebook_id": scope.notebook_id,
        "roots": scope.root_source_version_ids, "dependencies": scope.dependency_source_version_ids,
        "schema_revision": scope.schema_revision, "retention_watermark": scope.retention_watermark,
        "authorization_fingerprint": scope.authorization_fingerprint, "metadata_digest": hashlib.sha256(raw).hexdigest()})).hexdigest()


def _metadata(scope, raw):
    """원본 DB 재조회 대체가 아닌 보존 payload 자체의 exact 관계/Canon 검증이다."""
    if not isinstance(raw, bytes): raise _invalid()
    if len(raw) > MAX_METADATA_BYTES: raise _large()
    try:
        meta = _loads(raw)
        if type(meta) is not dict or set(meta) != set(_TABLES): raise _invalid()
        for table in _TABLES:
            rows = meta[table]
            if type(rows) is not list: raise _invalid()
            maximum = 10000 if table in {"canon_state_transitions", "canon_transition_attempts"} else 100
            if len(rows) > maximum: raise _large()
            if not rows and maximum != 10000: raise _invalid()
            for row in rows:
                if (type(row) is not dict or set(row) != set(_COLUMNS[table])
                        or row["tenant_id"] != scope.tenant_id or row["workspace_id"] != scope.workspace_id): raise _invalid()
                for key in ("created_at", "updated_at", "occurred_at"):
                    if key in row: _timestamp(row[key])
        for table in ("notebooks", "notebook_metadata_versions"):
            if len(meta[table]) != 1 or meta[table][0]["notebook_id"] != scope.notebook_id: raise _invalid()
        current = meta["notebook_metadata_versions"][0]
        if (current["is_current"] is not True or type(current["version"]) is not int or current["version"] < 1
                or not isinstance(current["title"], str) or current["description"] is not None and not isinstance(current["description"], str)): raise _invalid()
        for row in (meta["notebooks"][0], current):
            _safe_canon_id(row.get("created_by", row.get("updated_by")))
        def keyed(table, key):
            rows = meta[table]; index = {}
            for row in rows:
                _safe_canon_id(row[key])
                if row[key] in index: raise _invalid()
                index[row[key]] = row
            return index
        versions, sources, objects = keyed("source_versions", "record_id"), keyed("sources", "record_id"), keyed("object_records", "object_id")
        if (set(versions) != set(scope.root_source_version_ids + scope.dependency_source_version_ids)
                or set(sources) != {r["source_id"] for r in versions.values()}
                or set(objects) != {r["object_id"] for r in versions.values()}
                or set(scope.dependency_source_ids) != {versions[k]["source_id"] for k in scope.dependency_source_version_ids}): raise _invalid()
        items = []
        for identity in sorted(objects):
            row = objects[identity]
            if (row["area"] != "source" or row["status"] != "completed" or type(row["byte_size"]) is not int or row["byte_size"] < 0
                    or not isinstance(row["content_type"], str)): raise _invalid()
            items.append(_Object(identity, _digest(row["digest_sha256"]), row["byte_size"]))
        if sum(item.byte_size for item in items) > MAX_BYTES: raise _large()
        for row in sources.values():
            _canon(row, mutable=True)
            if row["state"] in {"expired", "disabled", "deleting", "deleted"}: raise _invalid()
        reachable, visiting, order = set(), set(), []
        def visit(identity):
            if identity in visiting or identity not in versions: raise _invalid()
            if identity in reachable: return
            visiting.add(identity); row = versions[identity]; _canon(row)
            obj, payload = objects[row["object_id"]], row["canonical_json"]
            digest_key = "text_sha256" if payload.get("origin_kind") in {"website", "mcp_open_law"} else "content_digest_sha256"
            if (payload.get("object_id") != row["object_id"] or type(payload.get("byte_size")) is not int
                    or payload["byte_size"] != obj["byte_size"] or payload.get(digest_key) != obj["digest_sha256"]): raise _invalid()
            previous = row["previous_version_id"]
            if previous is not None:
                visit(previous); parent = versions[previous]
                if (parent["source_id"] != row["source_id"] or parent["aggregate_id"] != row["aggregate_id"] or parent["version"] != row["version"] - 1): raise _invalid()
            visiting.remove(identity); reachable.add(identity); order.append(identity)
        for root in scope.root_source_version_ids: visit(root)
        if reachable != set(versions) or order != [r["record_id"] for r in meta["source_versions"]]: raise _invalid()
        bindings = meta["notebook_bindings"]
        if len(bindings) != len(scope.root_source_version_ids) or {r["version_id"] for r in bindings} != set(scope.root_source_version_ids): raise _invalid()
        for row in bindings:
            if (row["notebook_id"] != scope.notebook_id or row["binding_kind"] != "source" or row["record_id"] != versions[row["version_id"]]["source_id"]): raise _invalid()
            _safe_canon_id(row["created_by"])
        transitions = keyed("canon_state_transitions", "transition_id")
        attempts = keyed("canon_transition_attempts", "attempt_id")
        if set(transitions) != set(attempts): raise _invalid()
        if any(r["record_id"] not in sources for r in [*transitions.values(), *attempts.values()]): raise _invalid()
        for identity, source in sources.items():
            events = sorted((r for r in transitions.values() if r["record_id"] == identity), key=lambda r: r["transition_version"])
            if len(events) != source["version"] - 1: raise _invalid()
            state, counter = "registered", 1
            for event in events:
                attempt = attempts[event["transition_id"]]
                for key in ("actor_id", "trace_id", "policy_version"): _safe_canon_id(event[key])
                if (event["entity_type"] != "Source" or type(event["transition_version"]) is not int or event["transition_version"] != counter+1
                        or event["source_state"] != state or not isinstance(event["target_state"], str)
                        or not transition_allowed("Source", state, event["target_state"])
                        or any(attempt[k] != event[k] for k in _EVENT) or attempt["outcome"] != "succeeded" or attempt["safe_error_code"] is not None
                        or any(type(attempt[k]) is not int or attempt[k] != counter for k in ("expected_version", "current_version"))
                        or type(attempt["result_version"]) is not int or attempt["result_version"] != counter+1 or attempt["result_state"] != event["target_state"]): raise _invalid()
                state, counter = event["target_state"], counter+1
            if state != source["state"] or counter != source["version"]: raise _invalid()
        if not hmac.compare_digest(_fingerprint(scope, raw), scope.closure_fingerprint): raise _invalid()
        return meta, tuple(items)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError): raise _invalid() from None


def _cipher(key, scope, purpose, version, reference):
    if not isinstance(key, bytes) or len(key) < 32: raise _unavailable()
    _safe(version); _safe(reference)
    info = _json(["daon.recovery.selected-source-lineage", 3, purpose, version, reference, scope.binding()])
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(info).digest(), info=info).derive(key)
    return AESGCM(derived)


def _receipt(scope, receipt, version, reference):
    fields = {"key_version", "key_reference", "metadata_digest", "metadata_size", "objects", "data_digest", "data_size", "verified_at",
        "source_version_count", "source_count", "object_count", "total_bytes"}
    if (type(receipt) is not dict or set(receipt) != set(scope.binding()) | fields
            or any(receipt[k] != v for k, v in scope.binding().items()) or receipt["key_version"] != version or receipt["key_reference"] != reference): raise _invalid()
    for key in ("metadata_digest", "data_digest"): _digest(receipt[key])
    for key in ("metadata_size", "data_size", "source_version_count", "source_count", "object_count", "total_bytes"):
        if type(receipt[key]) is not int or receipt[key] < 0: raise _invalid()
    if (not 0 < receipt["metadata_size"] <= MAX_METADATA_BYTES or receipt["total_bytes"] > MAX_BYTES
            or receipt["source_version_count"] != len(scope.root_source_version_ids + scope.dependency_source_version_ids)
            or not 1 <= receipt["source_count"] <= 100 or not 1 <= receipt["object_count"] <= 100
            or type(receipt["objects"]) is not list or len(receipt["objects"]) != receipt["object_count"]): raise _invalid()
    _timestamp(receipt["verified_at"])
    seen, total, data_size = [], 0, len(MAGIC) + 4 + receipt["metadata_size"] + 28
    for row in receipt["objects"]:
        if type(row) is not dict or set(row) != {"object_id", "plaintext_digest", "byte_size", "ciphertext_digest", "chunks"}: raise _invalid()
        _safe_canon_id(row["object_id"]); _digest(row["plaintext_digest"]); _digest(row["ciphertext_digest"])
        if type(row["byte_size"]) is not int or not 0 <= row["byte_size"] <= MAX_BYTES: raise _invalid()
        if type(row["chunks"]) is not int or row["chunks"] != max(1, math.ceil(row["byte_size"] / CHUNK_SIZE)): raise _invalid()
        total += row["byte_size"]; data_size += row["byte_size"] + row["chunks"] * 32; seen.append(row["object_id"])
    if seen != sorted(set(seen)) or total != receipt["total_bytes"] or data_size != receipt["data_size"]: raise _invalid()


def encode_manifest(scope, receipt, *, master_key, key_version, key_reference):
    _receipt(scope, receipt, key_version, key_reference)
    header = _json({"scope": asdict(scope), "key_version": key_version, "key_reference": key_reference})
    nonce = secrets.token_bytes(12)
    blob = _cipher(master_key, scope, "db-manifest", key_version, key_reference).encrypt(nonce, _json(receipt), header)
    return b"DRM3" + struct.pack(">I", len(header)) + header + nonce + blob


def decode_manifest(envelope, *, master_key, key_version, key_reference):
    try:
        if not isinstance(envelope, bytes) or not envelope.startswith(b"DRM3") or len(envelope) > 1024 * 1024: raise _invalid()
        size = struct.unpack(">I", envelope[4:8])[0]
        if not 0 < size <= 128 * 1024 or len(envelope) < 8 + size + 28: raise _invalid()
        header = envelope[8:8+size]; parsed = _loads(header)
        if (type(parsed) is not dict or set(parsed) != {"scope", "key_version", "key_reference"}
                or parsed["key_version"] != key_version or parsed["key_reference"] != key_reference): raise _invalid()
        data = parsed["scope"]
        for field in ("root_source_version_ids", "dependency_source_version_ids", "dependency_source_ids"):
            if type(data[field]) is not list: raise _invalid()
            data[field] = tuple(data[field])
        scope = LineageCaptureScope(**data); blob = envelope[8+size:]
        receipt = _loads(_cipher(master_key, scope, "db-manifest", key_version, key_reference).decrypt(blob[:12], blob[12:], header))
        _receipt(scope, receipt, key_version, key_reference)
        return scope, receipt
    except (InvalidTag, KeyError, TypeError, ValueError, struct.error, UnicodeError): raise _invalid() from None


class FileLineageCaptureStore:
    def __init__(self, root, *, master_key, key_version, key_reference):
        if not isinstance(master_key, bytes) or len(master_key) < 32: raise _unavailable()
        self._key = master_key
        self.key_version, self.key_reference = _safe(key_version), _safe(key_reference)
        self.root = Path(root)
        self._fs = _WindowsFiles() if os.name == "nt" else _PosixFiles()
        with self._fs.pin(self.root): self._root_identity = self._fs.identity(self.root)

    @contextmanager
    def _root(self):
        with self._fs.pin(self.root):
            if self._fs.identity(self.root) != self._root_identity: raise _invalid()
            yield

    @contextmanager
    def _directory(self, scope, *, create=False):
        if not isinstance(scope, LineageCaptureScope): raise _invalid()
        with self._root():
            path = self.root / ("lineage-v3-" + scope.capture_id)
            if create: self._fs.mkdir(path)
            with self._fs.pin(path): yield path

    def _crypt(self, scope, purpose): return _cipher(self._key, scope, purpose, self.key_version, self.key_reference)

    def _encrypt(self, scope, purpose, raw, detail, nonces):
        nonce = secrets.token_bytes(12)
        if nonce in nonces: raise _invalid()
        nonces.add(nonce)
        aad = _json([scope.binding(), self.key_version, self.key_reference, purpose, detail])
        return nonce + self._crypt(scope, purpose).encrypt(nonce, raw, aad)

    def _decrypt(self, scope, purpose, blob, detail, nonces):
        if len(blob) < 28 or blob[:12] in nonces: raise _invalid()
        nonces.add(blob[:12])
        aad = _json([scope.binding(), self.key_version, self.key_reference, purpose, detail])
        try: return self._crypt(scope, purpose).decrypt(blob[:12], blob[12:], aad)
        except InvalidTag: raise _invalid() from None

    def _write(self, path, raw):
        with self._fs.file(path, create=True) as stream: stream.write(raw)

    def _read(self, path, limit):
        with self._fs.file(path) as stream: raw = stream.read(limit+1)
        if len(raw) > limit: raise _invalid()
        return raw

    def _owner(self, scope, path, nonces):
        owner = _loads(self._decrypt(scope, "owner", self._read(path / "owner.bin", 128 * 1024), [], nonces))
        if type(owner) is not dict or set(owner) != {"scope", "owner_id"} or owner["scope"] != scope.binding(): raise _invalid()
        return _safe(owner["owner_id"])

    def _cleanup(self, scope, path, owner_id):
        if self._owner(scope, path, set()) != owner_id: raise _invalid()
        names = self._fs.names(path)
        if not names <= {"owner.bin", "data.bin", "receipt.pending", "receipt.bin"}: raise _invalid()
        for name in names:
            with self._fs.file(path / name): pass
        for name in sorted(names - {"owner.bin"}): self._fs.unlink(path / name)

    @staticmethod
    def _detail(item, index, count): return [item.object_id, item.checksum_sha256, item.byte_size, index, count]

    def _selection(self, scope, selection):
        if not isinstance(selection, LineageSelection): raise _invalid()
        meta, items = _metadata(scope, selection._encoded)
        if (selection.fingerprint != scope.closure_fingerprint or selection._selection.notebook_id != scope.notebook_id
                or selection.root_source_version_ids != scope.root_source_version_ids or selection.dependency_source_version_ids != scope.dependency_source_version_ids
                or selection.dependency_source_ids != scope.dependency_source_ids or selection.schema_revision != scope.schema_revision
                or selection.retention_watermark != scope.retention_watermark or selection.authorization_fingerprint != scope.authorization_fingerprint
                or selection.source_version_count != len(meta["source_versions"]) or selection.source_count != len(meta["sources"])
                or selection.unique_object_count != len(items) or selection.total_bytes != sum(x.byte_size for x in items)
                or selection.metadata_bytes != len(selection._encoded)): raise _invalid()
        return meta, items

    def capture(self, scope, selection, open_source, *, closure_fingerprint, include_prior_versions, recheck, owner_id=None):
        selection.require_consent(closure_fingerprint, include_prior_versions)
        meta, items = self._selection(scope, selection)
        if not callable(recheck): raise _invalid()
        self._selection(scope, recheck())
        owner_id = _safe(owner_id if owner_id is not None else secrets.token_hex(16))
        nonces, locked, created, known_owner, preserve = set(), False, False, False, False
        lock = self.root / "lineage-v3.lock"
        token = self._encrypt(scope, "lock", _json({"owner_id": owner_id}), [], nonces)
        with self._root():
            try:
                self._write(lock, token); locked = True
                with self._directory(scope, create=True) as path:
                    created = True
                    self._write(path / "owner.bin", self._encrypt(scope, "owner", _json({"scope": scope.binding(), "owner_id": owner_id}), [], nonces)); known_owner = True
                    receipt = {**scope.binding(), "key_version": self.key_version, "key_reference": self.key_reference,
                        "metadata_size": len(selection._encoded), "metadata_digest": hashlib.sha256(selection._encoded).hexdigest(),
                        "source_version_count": len(meta["source_versions"]), "source_count": len(meta["sources"]),
                        "object_count": len(items), "total_bytes": sum(x.byte_size for x in items), "objects": []}
                    digest, total = hashlib.sha256(), 0
                    with self._fs.file(path / "data.bin", create=True) as output:
                        def write(raw):
                            nonlocal total
                            output.write(raw); digest.update(raw); total += len(raw)
                        def frame(raw): write(struct.pack(">I", len(raw))); write(raw)
                        write(MAGIC)
                        frame(self._encrypt(scope, "metadata", selection._encoded, [receipt["metadata_size"], receipt["metadata_digest"]], nonces))
                        for item in items:
                            count = max(1, math.ceil(item.byte_size / CHUNK_SIZE)); size = 0
                            plain, cipher = hashlib.sha256(), hashlib.sha256()
                            with open_source(item) as source:
                                for index in range(count):
                                    expected = min(CHUNK_SIZE, item.byte_size-size); chunk = bytearray()
                                    while len(chunk) < expected:
                                        remaining = expected-len(chunk); piece = source.read(remaining)
                                        if not isinstance(piece, bytes) or not piece or len(piece) > remaining: raise _invalid()
                                        chunk.extend(piece)
                                    raw = bytes(chunk); size += len(raw); plain.update(raw)
                                    blob = self._encrypt(scope, "object", raw, self._detail(item, index, count), nonces)
                                    cipher.update(blob); frame(blob)
                                if source.read(1) != b"": raise _invalid()
                            if size != item.byte_size or plain.hexdigest() != item.checksum_sha256: raise _invalid()
                            receipt["objects"].append({"object_id": item.object_id, "plaintext_digest": plain.hexdigest(), "byte_size": size,
                                "ciphertext_digest": cipher.hexdigest(), "chunks": count})
                    receipt.update(data_size=total, data_digest=digest.hexdigest(), verified_at=datetime.now(timezone.utc).isoformat())
                    self._verify_data(scope, path, receipt)
                    # 긴 암호화/파일 전체 검증 뒤 현재 주체/ACL/계보/metadata를 다시 소비한다.
                    # 호출자는 DB 확정 직전 세션도 검증해야 하며 별도 tx의 원자 직렬화 보장은 없다.
                    self._selection(scope, recheck())
                    self._write(path / "receipt.pending", self._encrypt(scope, "file-receipt", _json(receipt), [], nonces))
                    self._fs.rename(path / "receipt.pending", path / "receipt.bin")
                return receipt
            except BaseException as error:
                if not isinstance(error, Exception):
                    # 중단/종료는 사본과 소유 lock을 보존하며 원 예외를 그대로 전달한다.
                    preserve = True
                    raise
                preserve = isinstance(error, RecoveryError) and error.code.endswith("CLEANUP_UNCERTAIN")
                if created and not known_owner: preserve = True
                if created and not preserve:
                    try:
                        with self._directory(scope) as path: self._cleanup(scope, path, owner_id)
                    except BaseException as cleanup_error:
                        preserve = True
                        if not isinstance(cleanup_error, Exception): raise
                if preserve: raise RecoveryError("BACKUP_CAPTURE_CLEANUP_UNCERTAIN", 503) from None
                if isinstance(error, RecoveryError): raise
                if isinstance(error, FileExistsError): raise _invalid() from None
                raise _unavailable() from None
            finally:
                if locked and not preserve:
                    try:
                        if self._read(lock, 128 * 1024) != token: raise _invalid()
                        self._fs.unlink(lock)
                    except Exception: raise RecoveryError("BACKUP_CAPTURE_CLEANUP_UNCERTAIN", 503) from None

    def _verify_data(self, scope, path, receipt, *, object_id=None, sink=None):
        _receipt(scope, receipt, self.key_version, self.key_reference)
        with self._fs.file(path / "data.bin") as stream:
            def consume(deliver):
                nonces = set(); self._owner(scope, path, nonces)
                digest, size = hashlib.sha256(), 0
                def read(count):
                    nonlocal size
                    raw = stream.read(count)
                    if len(raw) != count: raise _invalid()
                    size += len(raw); digest.update(raw); return raw
                def frame(limit):
                    length = struct.unpack(">I", read(4))[0]
                    if not 28 <= length <= limit+28: raise _invalid()
                    return read(length)
                if read(len(MAGIC)) != MAGIC: raise _invalid()
                raw = self._decrypt(scope, "metadata", frame(MAX_METADATA_BYTES), [receipt["metadata_size"], receipt["metadata_digest"]], nonces)
                if len(raw) != receipt["metadata_size"] or hashlib.sha256(raw).hexdigest() != receipt["metadata_digest"]: raise _invalid()
                meta, items = _metadata(scope, raw)
                if (len(meta["sources"]) != receipt["source_count"] or len(meta["source_versions"]) != receipt["source_version_count"]
                        or len(items) != receipt["object_count"] or sum(x.byte_size for x in items) != receipt["total_bytes"]): raise _invalid()
                for item, row in zip(items, receipt["objects"], strict=True):
                    if (item.object_id, item.checksum_sha256, item.byte_size) != (row["object_id"], row["plaintext_digest"], row["byte_size"]): raise _invalid()
                    plain, cipher, plain_size = hashlib.sha256(), hashlib.sha256(), 0
                    for index in range(row["chunks"]):
                        blob = frame(CHUNK_SIZE); cipher.update(blob)
                        chunk = self._decrypt(scope, "object", blob, self._detail(item, index, row["chunks"]), nonces)
                        if len(chunk) != min(CHUNK_SIZE, item.byte_size-plain_size): raise _invalid()
                        plain.update(chunk); plain_size += len(chunk)
                        if deliver and item.object_id == object_id:
                            written = sink.write(chunk)
                            if type(written) is not int or written != len(chunk): raise _unavailable()
                    if plain.hexdigest() != item.checksum_sha256 or cipher.hexdigest() != row["ciphertext_digest"]: raise _invalid()
                if stream.read(1) or size != receipt["data_size"] or digest.hexdigest() != receipt["data_digest"]: raise _invalid()
                return meta
            meta = consume(False)
            if sink is not None:
                if object_id not in {r["object_id"] for r in receipt["objects"]}: raise _invalid()
                stream.seek(0); consume(True)  # 같은 보호 handle에서 검증 후에만 bytes 전달
            return VerifiedLineageCapture(meta, receipt)

    def _load_receipt(self, scope, path, expected):
        receipt = _loads(self._decrypt(scope, "file-receipt", self._read(path / "receipt.bin", 1024 * 1024), [], set()))
        _receipt(scope, receipt, self.key_version, self.key_reference)
        if expected is not None and receipt != expected: raise _invalid()
        return receipt

    def verify(self, scope, *, expected_receipt=None):
        try:
            with self._directory(scope) as path: return self._verify_data(scope, path, self._load_receipt(scope, path, expected_receipt))
        except (OSError, KeyError, TypeError, ValueError, struct.error): raise _invalid() from None

    def read_object(self, scope, object_id, sink, *, expected_receipt=None):
        _safe_canon_id(object_id)
        try:
            with self._directory(scope) as path:
                self._verify_data(scope, path, self._load_receipt(scope, path, expected_receipt), object_id=object_id, sink=sink)
        except (OSError, KeyError, TypeError, ValueError, struct.error): raise _invalid() from None

    def reconcile(self, scope, lookup):
        try: stored = lookup()
        except Exception: raise _unavailable() from None
        local = self.verify(scope)
        if stored is None: return "unreferenced"
        if stored != local.receipt: raise _invalid()
        return "referenced"

    def discard_unreferenced(self, scope, *, owner_id, reference_guard):
        _safe(owner_id)
        try:
            with reference_guard() as referenced:
                if referenced is not None: raise _invalid()
                with self._root():
                    if self._fs.exists(self.root / "lineage-v3.lock"): raise RecoveryError("BACKUP_CAPTURE_CLEANUP_UNCERTAIN", 503)
                    with self._directory(scope) as path: self._cleanup(scope, path, owner_id)
        except RecoveryError: raise
        except Exception: raise _unavailable() from None
