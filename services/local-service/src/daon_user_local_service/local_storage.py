from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from collections.abc import Callable, Mapping
from typing import Final

import sqlite_vec  # type: ignore[import-untyped]
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from sqlcipher3 import dbapi2 as sqlite  # type: ignore[import-untyped]

from .safe_file import (
    UnsafePathError,
    atomic_write,
    delete_file,
    read_file,
    validate_directory_chain,
)


_WORKSPACE_ID: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_AREAS: Final = frozenset({"source", "artifact", "cache", "index"})
_LEGACY_MAGIC: Final = b"DAONENC1"
_LEGACY_HEADER: Final = struct.Struct(">8sBI12s32s")
_MAGIC: Final = b"DAONENC2"
_HEADER_VERSION: Final = 2
_ALGORITHM_ID: Final = 1
_HEADER: Final = struct.Struct(">8sBBIHQ12s32s")
_KEY_VERSION: Final = 1
_DIGEST: Final = re.compile(r"^[0-9a-f]{64}$")
_FLOAT32_MAX: Final = 3.4028234663852886e38
_WRAP_ALGORITHM: Final = "AES-256-GCM+HKDF-SHA256"
_CANON_ENTITY_TYPES: Final = frozenset({
    "Source", "SourceVersion", "ProcessingRun", "Run", "RunSnapshot", "RunResult",
    "IndexVersion", "EvidenceSpan", "Citation", "StudioOutput", "OutputVersion",
    "PendingOperationReference", "GenerationRequest", "GenerationSettingsSnapshot",
    "ScopeSnapshot", "ProviderSettingsSnapshot",
})
_CANON_FORBIDDEN_FIELDS: Final = frozenset({
    "organization_policy", "approval", "approval_request", "provider_secret",
    "provider_credential", "secret", "secret_reference", "cloud_access_token",
})
_CANON_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_UTC_TIMESTAMP: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


@dataclass(frozen=True, slots=True)
class LocalCanonicalEnvelope:
    entity_type: str
    entity_id: str
    aggregate_id: str
    version: int
    schema_version: int
    digest_sha256: str
    created_at: str
    previous_version_id: str | None
    payload: dict[str, object]
    data_area: str = "local_private"


@dataclass(frozen=True, slots=True)
class RawSourceCanonicalInput:
    entity_type: str
    entity_id: str
    aggregate_id: str
    payload: Mapping[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class LocalSyncQueueState:
    operation_id: str
    version: int
    approval_state: str
    manifest_digest: str
    batch_cursor: str | None
    conflict_id: str | None
    queued_at: str
    previous_version: int | None


@dataclass(frozen=True, slots=True)
class LocalKnowledgeCopy:
    copy_id: str
    package_id: str
    object_id: str
    content_digest_sha256: str
    manifest_digest_sha256: str
    state: str
    version: int


@dataclass(frozen=True, slots=True)
class LocalDeletionTombstone:
    request_id: str
    reference_id: str
    version: int
    state: str
    evidence: str | None
    recorded_at: str
    previous_version: int | None


@dataclass(frozen=True, slots=True)
class LocalRecoveryJobState:
    job_id: str
    version: int
    state: str
    target_id: str
    snapshot_checksum: str
    actual_checksum: str
    journal_present: bool
    recorded_at: str
    previous_version: int | None


def _valid_utf8_text(value: bytes) -> bool:
    if b"\x00" in value:
        return False
    try:
        value.decode("utf-8", errors="strict")
        return True
    except UnicodeDecodeError:
        return False


_CONTENT_TYPES: Final[dict[str, tuple[int, Callable[[bytes], bool]]]] = {
    "text/plain": (1, _valid_utf8_text),
    "application/pdf": (2, lambda value: value.startswith(b"%PDF-")),
    "image/png": (3, lambda value: value.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/jpeg": (4, lambda value: value.startswith(b"\xff\xd8\xff")),
    "application/vnd.daon.knowledge-package+json": (5, _valid_utf8_text),
}
_CONTENT_TYPES_BY_ID: Final = {value[0]: name for name, value in _CONTENT_TYPES.items()}


class LocalStorageError(RuntimeError):
    """Stable fail-closed local storage error."""


def _fail(code: str) -> LocalStorageError:
    return LocalStorageError(code)


def _scope(workspace_id: str, area: str) -> None:
    if not _WORKSPACE_ID.fullmatch(workspace_id) or area not in _AREAS:
        raise _fail("LOCAL_SCOPE_INVALID")


def _has_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & 0x400)


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _wipe(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0
    value.clear()


def _validate_content_type(content_type: str, plaintext: bytes) -> int:
    contract = _CONTENT_TYPES.get(content_type)
    if contract is None or not contract[1](plaintext):
        raise _fail("LOCAL_CONTENT_TYPE_INVALID")
    return contract[0]


def _validate_embedding(embedding: list[float]) -> None:
    if not embedding or not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and abs(float(value)) <= _FLOAT32_MAX
        for value in embedding
    ):
        raise _fail("LOCAL_VECTOR_INVALID")


class LocalEncryptedStore:
    """SQLCipher metadata/vector store and AEAD file store for one local profile."""

    def __init__(
        self,
        root: Path,
        master_key: bytes | bytearray,
        connection: sqlite.Connection,
        operation_lock: RLock,
    ) -> None:
        self._root = root
        self._master_key = bytearray(master_key)
        self._connection: sqlite.Connection | None = connection
        self._operation_lock = operation_lock

    @classmethod
    def open(cls, root: Path, master_key: bytes | bytearray) -> "LocalEncryptedStore":
        if len(master_key) != 32:
            raise _fail("LOCAL_KEY_UNAVAILABLE")
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or _has_reparse_point(root):
            raise _fail("LOCAL_PATH_UNSAFE")
        database_path = root / "metadata.db"
        existed = database_path.exists()
        # FastAPI may dispatch synchronous handlers on different worker threads.
        # The private operation lock serializes every connection use while allowing
        # ownership to transfer safely from the bootstrap thread.
        operation_lock = RLock()
        with operation_lock:
            connection = sqlite.connect(database_path, check_same_thread=False)
            try:
                connection.execute(f"PRAGMA key = \"x'{master_key.hex()}'\"")
                connection.execute("PRAGMA temp_store = MEMORY")
                connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
                connection.enable_load_extension(True)
                try:
                    sqlite_vec.load(connection)
                finally:
                    connection.enable_load_extension(False)
                store = cls(root, master_key, connection, operation_lock)
                store._migrate()
                return store
            except (sqlite.DatabaseError, sqlite.OperationalError) as error:
                connection.close()
                if not existed:
                    for suffix in ("", "-wal", "-shm", "-journal"):
                        try:
                            (Path(f"{database_path}{suffix}")).unlink()
                        except FileNotFoundError:
                            pass
                raise _fail("LOCAL_KEY_UNAVAILABLE") from error

    def _db(self) -> sqlite.Connection:
        if self._connection is None or not self._master_key:
            raise _fail("LOCAL_KEY_UNAVAILABLE")
        return self._connection

    def _migrate(self) -> None:
        database = self._db()
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS storage_identity (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                marker TEXT NOT NULL
            );
            INSERT OR IGNORE INTO storage_identity(singleton, marker)
                VALUES (1, 'daon-local-encrypted-v1');
            CREATE TABLE IF NOT EXISTS area_keys (
                workspace_id TEXT NOT NULL,
                area TEXT NOT NULL,
                key_version INTEGER NOT NULL,
                nonce BLOB NOT NULL,
                wrapped_dek BLOB NOT NULL,
                wrap_algorithm TEXT NOT NULL,
                created_at TEXT NOT NULL,
                rotated_at TEXT,
                status TEXT NOT NULL,
                PRIMARY KEY (workspace_id, area, key_version)
            );
            CREATE TABLE IF NOT EXISTS local_objects (
                object_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                area TEXT NOT NULL,
                key_version INTEGER NOT NULL,
                digest BLOB NOT NULL,
                plain_size INTEGER NOT NULL,
                blob_name TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                object_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vector_items (
                workspace_id TEXT NOT NULL,
                area TEXT NOT NULL,
                item_id TEXT NOT NULL,
                vec_table TEXT NOT NULL,
                vec_rowid INTEGER NOT NULL,
                dimension INTEGER NOT NULL,
                metric TEXT NOT NULL,
                key_version INTEGER NOT NULL,
                model_digest TEXT NOT NULL,
                artifact_digest TEXT NOT NULL,
                embedding_version TEXT NOT NULL,
                source_version TEXT NOT NULL,
                object_version TEXT NOT NULL,
                PRIMARY KEY (workspace_id, area, item_id)
            );
            CREATE TABLE IF NOT EXISTS canonical_envelopes (
                workspace_id TEXT NOT NULL,
                area TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version > 0),
                schema_version INTEGER NOT NULL CHECK (schema_version > 0),
                digest_sha256 TEXT NOT NULL CHECK (length(digest_sha256) = 64),
                created_at TEXT NOT NULL,
                previous_version_id TEXT,
                payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
                data_area TEXT NOT NULL CHECK (data_area = 'local_private'),
                PRIMARY KEY (workspace_id, area, entity_type, entity_id),
                UNIQUE (workspace_id, area, entity_type, aggregate_id, version)
            );
            CREATE TRIGGER IF NOT EXISTS canonical_envelopes_update_immutable
            BEFORE UPDATE ON canonical_envelopes
            BEGIN SELECT RAISE(ABORT, 'LOCAL_CANON_IMMUTABLE'); END;
            CREATE TRIGGER IF NOT EXISTS canonical_envelopes_delete_immutable
            BEFORE DELETE ON canonical_envelopes
            BEGIN SELECT RAISE(ABORT, 'LOCAL_CANON_IMMUTABLE'); END;
            CREATE TABLE IF NOT EXISTS sync_queue_states (
                workspace_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version > 0),
                approval_state TEXT NOT NULL CHECK (approval_state IN (
                    'draft','awaiting_approval','approved','transferring','conflict',
                    'reindex_requested','blocked','cancelled'
                )),
                manifest_digest TEXT NOT NULL CHECK (length(manifest_digest) = 64),
                batch_cursor TEXT,
                conflict_id TEXT,
                queued_at TEXT NOT NULL,
                previous_version INTEGER,
                PRIMARY KEY (workspace_id, operation_id, version),
                CHECK ((version = 1) = (previous_version IS NULL)),
                CHECK (previous_version IS NULL OR previous_version = version - 1)
            );
            CREATE TRIGGER IF NOT EXISTS sync_queue_states_update_immutable
            BEFORE UPDATE ON sync_queue_states
            BEGIN SELECT RAISE(ABORT, 'LOCAL_SYNC_QUEUE_IMMUTABLE'); END;
            CREATE TRIGGER IF NOT EXISTS sync_queue_states_delete_immutable
            BEFORE DELETE ON sync_queue_states
            BEGIN SELECT RAISE(ABORT, 'LOCAL_SYNC_QUEUE_IMMUTABLE'); END;
            CREATE TABLE IF NOT EXISTS deletion_tombstones (
                workspace_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                reference_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version > 0),
                state TEXT NOT NULL CHECK (state IN (
                    'pending_ack','acknowledged','device_revoked','key_destroyed'
                )),
                evidence TEXT CHECK (evidence IN (
                    'device_ack','device_revoked','key_destroyed'
                )),
                recorded_at TEXT NOT NULL,
                previous_version INTEGER,
                PRIMARY KEY (workspace_id, request_id, reference_id, version),
                CHECK ((version = 1) = (previous_version IS NULL)),
                CHECK (previous_version IS NULL OR previous_version = version - 1),
                CHECK ((state = 'pending_ack') = (evidence IS NULL))
            );
            CREATE TRIGGER IF NOT EXISTS deletion_tombstones_update_immutable
            BEFORE UPDATE ON deletion_tombstones
            BEGIN SELECT RAISE(ABORT, 'LOCAL_DELETION_TOMBSTONE_IMMUTABLE'); END;
            CREATE TRIGGER IF NOT EXISTS deletion_tombstones_delete_immutable
            BEFORE DELETE ON deletion_tombstones
            BEGIN SELECT RAISE(ABORT, 'LOCAL_DELETION_TOMBSTONE_IMMUTABLE'); END;
            CREATE TABLE IF NOT EXISTS recovery_job_states (
                workspace_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version > 0),
                state TEXT NOT NULL CHECK (state IN (
                    'detected','quarantined','scanning','repairable','repairing',
                    'verified','manual_recovery_required','failed'
                )),
                target_id TEXT NOT NULL,
                snapshot_checksum TEXT NOT NULL CHECK (length(snapshot_checksum) = 64),
                actual_checksum TEXT NOT NULL CHECK (length(actual_checksum) = 64),
                journal_present INTEGER NOT NULL CHECK (journal_present IN (0,1)),
                recorded_at TEXT NOT NULL,
                previous_version INTEGER,
                PRIMARY KEY (workspace_id, job_id, version),
                CHECK ((version = 1) = (previous_version IS NULL)),
                CHECK (previous_version IS NULL OR previous_version = version - 1)
            );
            CREATE TRIGGER IF NOT EXISTS recovery_job_states_update_immutable
            BEFORE UPDATE ON recovery_job_states
            BEGIN SELECT RAISE(ABORT, 'LOCAL_RECOVERY_JOB_IMMUTABLE'); END;
            CREATE TRIGGER IF NOT EXISTS recovery_job_states_delete_immutable
            BEFORE DELETE ON recovery_job_states
            BEGIN SELECT RAISE(ABORT, 'LOCAL_RECOVERY_JOB_IMMUTABLE'); END;
            """
        )
        now = _utc_now()
        area_columns = {
            str(row[1]) for row in database.execute("PRAGMA table_info(area_keys)")
        }
        for name, definition in (
            ("wrap_algorithm", "TEXT"),
            ("created_at", "TEXT"),
            ("rotated_at", "TEXT"),
            ("status", "TEXT"),
        ):
            if name not in area_columns:
                database.execute(f"ALTER TABLE area_keys ADD COLUMN {name} {definition}")
        database.execute(
            "UPDATE area_keys SET wrap_algorithm = COALESCE(wrap_algorithm, ?), "
            "created_at = COALESCE(created_at, ?), status = COALESCE(status, 'active')",
            (_WRAP_ALGORITHM, now),
        )
        object_columns = {
            str(row[1]) for row in database.execute("PRAGMA table_info(local_objects)")
        }
        for name, definition in (
            ("content_type", "TEXT"),
            ("object_version", "TEXT"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("status", "TEXT"),
        ):
            if name not in object_columns:
                database.execute(f"ALTER TABLE local_objects ADD COLUMN {name} {definition}")
        database.execute(
            "UPDATE local_objects SET content_type = COALESCE(content_type, 'application/octet-stream'), "
            "object_version = COALESCE(object_version, 'legacy-v1'), "
            "created_at = COALESCE(created_at, ?), updated_at = COALESCE(updated_at, ?), "
            "status = COALESCE(status, 'active')",
            (now, now),
        )
        marker = database.execute(
            "SELECT marker FROM storage_identity WHERE singleton = 1"
        ).fetchone()
        if marker not in (("daon-local-encrypted-v1",), ("daon-local-encrypted-v2",)):
            raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
        database.execute(
            "UPDATE storage_identity SET marker = 'daon-local-encrypted-v2' WHERE singleton = 1"
        )
        database.commit()
        # SQLCipher 4.12 on Windows must initialize the encrypted schema before
        # switching a new file to WAL mode. Doing this on a zero-page database
        # terminates the native driver before Python can surface an exception.
        database.execute("PRAGMA journal_mode = WAL").fetchone()
        self._recover_orphans()

    def _recover_orphans(self) -> None:
        files_root = self._root / "files"
        if not files_root.exists():
            return
        referenced = {
            str(row[0]) for row in self._db().execute("SELECT blob_name FROM local_objects")
        }
        try:
            validate_directory_chain(self._root, files_root)
            for prefix_directory in files_root.iterdir():
                validate_directory_chain(self._root, prefix_directory)
                for area_directory in prefix_directory.iterdir():
                    validate_directory_chain(self._root, area_directory)
                    for candidate in area_directory.iterdir():
                        if candidate.name.endswith(".tmp") or (
                            candidate.suffix == ".bin" and candidate.name not in referenced
                        ):
                            delete_file(self._root, candidate)
        except FileNotFoundError:
            # Another bounded recovery pass may have already removed the orphan.
            return
        except (OSError, UnsafePathError) as error:
            raise _fail("LOCAL_PATH_UNSAFE") from error

    def _wrap_key(self) -> bytearray:
        return bytearray(HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"daon-local-wrap-v1",
            info=b"area-dek-envelope",
        ).derive(bytes(self._master_key)))

    def _area_dek(self, workspace_id: str, area: str) -> bytearray:
        database = self._db()
        row = database.execute(
            "SELECT nonce, wrapped_dek, wrap_algorithm, status FROM area_keys "
            "WHERE workspace_id = ? AND area = ? AND key_version = ?",
            (workspace_id, area, _KEY_VERSION),
        ).fetchone()
        aad = f"{workspace_id}|{area}|{_KEY_VERSION}".encode()
        wrap_key = self._wrap_key()
        try:
            wrapper = AESGCM(bytes(wrap_key))
            if row is None:
                dek = bytearray(AESGCM.generate_key(bit_length=256))
                nonce = os.urandom(12)
                now = _utc_now()
                database.execute(
                    "INSERT INTO area_keys "
                    "(workspace_id, area, key_version, nonce, wrapped_dek, wrap_algorithm, "
                    "created_at, rotated_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'active')",
                    (
                        workspace_id,
                        area,
                        _KEY_VERSION,
                        nonce,
                        wrapper.encrypt(nonce, bytes(dek), aad),
                        _WRAP_ALGORITHM,
                        now,
                    ),
                )
                database.commit()
                return dek
            if row[2] != _WRAP_ALGORITHM or row[3] != "active":
                raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
            return bytearray(wrapper.decrypt(bytes(row[0]), bytes(row[1]), aad))
        except InvalidTag as error:
            raise _fail("LOCAL_CIPHERTEXT_CORRUPT") from error
        finally:
            _wipe(wrap_key)

    def _area_directory(self, workspace_id: str, area: str) -> Path:
        scope = hashlib.sha256(f"{workspace_id}|{area}".encode()).hexdigest()
        directory = self._root / "files" / scope[:2] / scope[2:]
        directory.mkdir(parents=True, exist_ok=True)
        try:
            validate_directory_chain(self._root, directory)
        except (OSError, UnsafePathError) as error:
            raise _fail("LOCAL_PATH_UNSAFE") from error
        return directory

    def put_file(
        self, workspace_id: str, area: str, plaintext: bytes, *, content_type: str
    ) -> str:
        with self._operation_lock:
            return self._put_file(workspace_id, area, plaintext, content_type=content_type)

    def _put_file(
        self, workspace_id: str, area: str, plaintext: bytes, *, content_type: str
    ) -> str:
        _scope(workspace_id, area)
        if not isinstance(plaintext, bytes):
            raise _fail("LOCAL_INPUT_INVALID")
        content_type_id = _validate_content_type(content_type, plaintext)
        database = self._db()
        object_id = str(uuid.uuid4())
        blob_name = f"{object_id}.bin"
        directory = self._area_directory(workspace_id, area)
        destination = directory / blob_name
        temporary = directory / f".{object_id}.{uuid.uuid4().hex}.tmp"
        digest = hashlib.sha256(plaintext).digest()
        nonce = os.urandom(12)
        header = _HEADER.pack(
            _MAGIC,
            _HEADER_VERSION,
            _ALGORITHM_ID,
            _KEY_VERSION,
            content_type_id,
            len(plaintext),
            nonce,
            digest,
        )
        context = f"{workspace_id}|{area}|{object_id}".encode()
        dek = self._area_dek(workspace_id, area)
        try:
            ciphertext = AESGCM(bytes(dek)).encrypt(nonce, plaintext, header + context)
            payload = header + ciphertext
            atomic_write(self._root, directory, temporary, destination, payload)
            now = _utc_now()
            database.execute(
                "INSERT INTO local_objects "
                "(object_id, workspace_id, area, key_version, digest, plain_size, blob_name, "
                "content_type, object_version, created_at, updated_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '1', ?, ?, 'active')",
                (
                    object_id,
                    workspace_id,
                    area,
                    _KEY_VERSION,
                    digest,
                    len(plaintext),
                    blob_name,
                    content_type,
                    now,
                    now,
                ),
            )
            database.commit()
            return object_id
        except (OSError, UnsafePathError, sqlite.DatabaseError) as error:
            database.rollback()
            for candidate in (temporary, destination):
                try:
                    delete_file(self._root, candidate)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            raise _fail("LOCAL_WRITE_FAILED") from error
        finally:
            _wipe(dek)

    def blob_path_for_test(self, workspace_id: str, area: str, object_id: str) -> Path:
        _scope(workspace_id, area)
        return self._area_directory(workspace_id, area) / f"{object_id}.bin"

    def get_file(self, workspace_id: str, area: str, object_id: str) -> bytes:
        with self._operation_lock:
            return self._get_file(workspace_id, area, object_id)

    def _get_file(self, workspace_id: str, area: str, object_id: str) -> bytes:
        _scope(workspace_id, area)
        database = self._db()
        row = database.execute(
            "SELECT key_version, digest, plain_size, blob_name, content_type, object_version, status "
            "FROM local_objects "
            "WHERE object_id = ? AND workspace_id = ? AND area = ?",
            (object_id, workspace_id, area),
        ).fetchone()
        if row is None:
            raise _fail("LOCAL_OBJECT_NOT_FOUND")
        path = self._area_directory(workspace_id, area) / str(row[3])
        try:
            if row[6] != "active":
                raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
            payload = read_file(self._root, path)
            if payload.startswith(_LEGACY_MAGIC):
                magic, key_version, plain_size, nonce, digest = _LEGACY_HEADER.unpack_from(payload)
                header_size = _LEGACY_HEADER.size
                aad = f"{workspace_id}|{area}|{object_id}|{key_version}".encode()
            else:
                (
                    magic,
                    header_version,
                    algorithm_id,
                    key_version,
                    content_type_id,
                    plain_size,
                    nonce,
                    digest,
                ) = _HEADER.unpack_from(payload)
                if (
                    magic != _MAGIC
                    or header_version != _HEADER_VERSION
                    or algorithm_id != _ALGORITHM_ID
                    or _CONTENT_TYPES_BY_ID.get(content_type_id) != row[4]
                ):
                    raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
                header_size = _HEADER.size
                aad = payload[:header_size] + f"{workspace_id}|{area}|{object_id}".encode()
            if key_version != row[0] or plain_size != row[2] or digest != bytes(row[1]):
                raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
            dek = self._area_dek(workspace_id, area)
            try:
                plaintext = AESGCM(bytes(dek)).decrypt(nonce, payload[header_size:], aad)
            finally:
                _wipe(dek)
            if len(plaintext) != plain_size or hashlib.sha256(plaintext).digest() != digest:
                raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
            return plaintext
        except LocalStorageError:
            raise
        except UnsafePathError as error:
            raise _fail("LOCAL_PATH_UNSAFE") from error
        except (OSError, struct.error, InvalidTag) as error:
            raise _fail("LOCAL_CIPHERTEXT_CORRUPT") from error

    def list_object_ids(self, workspace_id: str, area: str) -> list[str]:
        with self._operation_lock:
            _scope(workspace_id, area)
            return [
                str(row[0])
                for row in self._db().execute(
                    "SELECT object_id FROM local_objects WHERE workspace_id = ? AND area = ?",
                    (workspace_id, area),
                )
            ]

    def _vector_table(self, workspace_id: str, area: str, dimension: int) -> str:
        if not 1 <= dimension <= 4096:
            raise _fail("LOCAL_VECTOR_INVALID")
        suffix = hashlib.sha256(f"{workspace_id}|{area}".encode()).hexdigest()[:24]
        table = f"vec_{suffix}_{dimension}"
        self._db().execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0(embedding float[{dimension}] distance_metric=cosine)"
        )
        return table

    @staticmethod
    def _canonical_payload(payload: Mapping[str, object]) -> tuple[dict[str, object], str]:
        if not isinstance(payload, Mapping):
            raise _fail("LOCAL_CANON_SNAPSHOT_INVALID")

        def visit(value: object) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    if not isinstance(key, str) or key.lower() in _CANON_FORBIDDEN_FIELDS:
                        raise _fail("LOCAL_CANON_FIELD_FORBIDDEN")
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
            elif value is not None and not isinstance(value, (str, int, float, bool)):
                raise _fail("LOCAL_CANON_SNAPSHOT_INVALID")

        normalized = dict(payload)
        visit(normalized)
        try:
            encoded = json.dumps(
                normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise _fail("LOCAL_CANON_SNAPSHOT_INVALID") from error
        return normalized, encoded

    def put_canonical_envelope(
        self,
        workspace_id: str,
        area: str,
        *,
        entity_type: str,
        entity_id: str,
        aggregate_id: str,
        version: int,
        schema_version: int,
        digest_sha256: str,
        created_at: str,
        previous_version_id: str | None,
        payload: Mapping[str, object],
    ) -> None:
        with self._operation_lock:
            _scope(workspace_id, area)
            if (
                entity_type not in _CANON_ENTITY_TYPES
                or not _CANON_ID.fullmatch(entity_id)
                or not _CANON_ID.fullmatch(aggregate_id)
                or version < 1
                or schema_version < 1
                or not _DIGEST.fullmatch(digest_sha256)
                or not _UTC_TIMESTAMP.fullmatch(created_at)
                or (
                    previous_version_id is not None
                    and not _CANON_ID.fullmatch(previous_version_id)
                )
            ):
                raise _fail("LOCAL_CANON_SNAPSHOT_INVALID")
            if (version == 1) != (previous_version_id is None):
                raise _fail("LOCAL_CANON_PREVIOUS_VERSION_INVALID")
            normalized, encoded = self._canonical_payload(payload)
            actual = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if actual != digest_sha256:
                raise _fail("LOCAL_CANON_DIGEST_MISMATCH")
            database = self._db()
            if previous_version_id is not None:
                previous = database.execute(
                    "SELECT aggregate_id, version FROM canonical_envelopes "
                    "WHERE workspace_id = ? AND area = ? AND entity_type = ? AND entity_id = ?",
                    (workspace_id, area, entity_type, previous_version_id),
                ).fetchone()
                if previous != (aggregate_id, version - 1):
                    raise _fail("LOCAL_CANON_PREVIOUS_VERSION_INVALID")
            try:
                database.execute(
                    "INSERT INTO canonical_envelopes "
                    "(workspace_id, area, entity_type, entity_id, aggregate_id, version, "
                    "schema_version, digest_sha256, created_at, previous_version_id, "
                    "payload_json, data_area) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local_private')",
                    (
                        workspace_id, area, entity_type, entity_id, aggregate_id, version,
                        schema_version, digest_sha256, created_at, previous_version_id, encoded,
                    ),
                )
                database.commit()
            except sqlite.IntegrityError as error:
                database.rollback()
                raise _fail("LOCAL_CANON_IMMUTABLE") from error
            del normalized

    def get_canonical_envelope(
        self,
        workspace_id: str,
        area: str,
        entity_type: str,
        entity_id: str,
    ) -> LocalCanonicalEnvelope:
        with self._operation_lock:
            _scope(workspace_id, area)
            if entity_type not in _CANON_ENTITY_TYPES or not _CANON_ID.fullmatch(entity_id):
                raise _fail("LOCAL_CANON_SNAPSHOT_INVALID")
            row = self._db().execute(
                "SELECT aggregate_id, version, schema_version, digest_sha256, created_at, "
                "previous_version_id, payload_json, data_area FROM canonical_envelopes "
                "WHERE workspace_id = ? AND area = ? AND entity_type = ? AND entity_id = ?",
                (workspace_id, area, entity_type, entity_id),
            ).fetchone()
            if row is None:
                raise _fail("LOCAL_CANON_NOT_FOUND")
            payload = json.loads(str(row[6]))
            if not isinstance(payload, dict):
                raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
            return LocalCanonicalEnvelope(
                entity_type, entity_id, str(row[0]), int(row[1]), int(row[2]),
                str(row[3]), str(row[4]), None if row[5] is None else str(row[5]),
                payload, str(row[7]),
            )

    def list_canonical_envelopes(
        self, workspace_id: str, area: str, entity_type: str | None = None
    ) -> tuple[LocalCanonicalEnvelope, ...]:
        """Return immutable local Canon in append order for restart projections."""
        with self._operation_lock:
            _scope(workspace_id, area)
            if entity_type is not None and entity_type not in _CANON_ENTITY_TYPES:
                raise _fail("LOCAL_CANON_SNAPSHOT_INVALID")
            statement = (
                "SELECT entity_type,entity_id,aggregate_id,version,schema_version,digest_sha256,"
                "created_at,previous_version_id,payload_json,data_area FROM canonical_envelopes "
                "WHERE workspace_id = ? AND area = ?"
            )
            parameters: tuple[object, ...] = (workspace_id, area)
            if entity_type is not None:
                statement += " AND entity_type = ?"
                parameters += (entity_type,)
            rows = self._db().execute(statement + " ORDER BY rowid", parameters).fetchall()
            envelopes: list[LocalCanonicalEnvelope] = []
            for row in rows:
                payload = json.loads(str(row[8]))
                if not isinstance(payload, dict):
                    raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
                envelopes.append(LocalCanonicalEnvelope(
                    str(row[0]), str(row[1]), str(row[2]), int(row[3]), int(row[4]),
                    str(row[5]), str(row[6]), None if row[7] is None else str(row[7]),
                    payload, str(row[9]),
                ))
            return tuple(envelopes)

    def list_canonical_types(self, workspace_id: str, area: str) -> tuple[str, ...]:
        return tuple(
            envelope.entity_type
            for envelope in self.list_canonical_envelopes(workspace_id, area)
        )

    @staticmethod
    def _knowledge_manifest_digest(manifest: Mapping[str, object]) -> str:
        try:
            encoded = json.dumps(
                dict(manifest), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise _fail("LOCAL_KNOWLEDGE_COPY_INVALID") from error
        return hashlib.sha256(encoded).hexdigest()

    def import_knowledge_copy(
        self,
        *,
        manifest: Mapping[str, object],
        manifest_digest_sha256: str,
        canonical_package: bytes,
        idempotency_key: str,
    ) -> LocalKnowledgeCopy:
        """Atomically expose only canonical, digest-bound encrypted Knowledge copies."""
        with self._operation_lock:
            workspace_id = manifest.get("workspace_id")
            copy_id = manifest.get("copy_id")
            package_id = manifest.get("package_id")
            schema_version = manifest.get("schema_version")
            content_digest = manifest.get("content_digest_sha256")
            required_text = (
                "producer_product", "producer_version", "knowledge_registration_id",
                "output_version_id", "authority", "registration_state", "review_state",
                "effective_at", "expires_at",
            )
            if (
                not isinstance(workspace_id, str)
                or not isinstance(copy_id, str) or not _CANON_ID.fullmatch(copy_id)
                or not isinstance(package_id, str) or not _CANON_ID.fullmatch(package_id)
                or not isinstance(content_digest, str) or not _DIGEST.fullmatch(content_digest)
                or not _DIGEST.fullmatch(manifest_digest_sha256)
                or not _CANON_ID.fullmatch(idempotency_key)
                or any(not isinstance(manifest.get(key), str) for key in required_text)
                or not isinstance(schema_version, int) or isinstance(schema_version, bool)
                or schema_version < 1
                or not _UTC_TIMESTAMP.fullmatch(str(manifest.get("expires_at", "")))
                or not _UTC_TIMESTAMP.fullmatch(str(manifest.get("effective_at", "")))
                or manifest.get("registration_state") != "registered"
                or manifest.get("review_state") != "approved"
                or manifest.get("authority") != "approved"
                or not isinstance(canonical_package, bytes)
                or not canonical_package or len(canonical_package) > 12 * 1024 * 1024
            ):
                raise _fail("LOCAL_KNOWLEDGE_COPY_INVALID")
            try:
                effective_at = datetime.fromisoformat(
                    str(manifest["effective_at"]).replace("Z", "+00:00")
                )
                expires_at = datetime.fromisoformat(
                    str(manifest["expires_at"]).replace("Z", "+00:00")
                )
            except ValueError as error:
                raise _fail("LOCAL_KNOWLEDGE_COPY_INVALID") from error
            if effective_at >= expires_at:
                raise _fail("LOCAL_KNOWLEDGE_COPY_INVALID")
            _scope(workspace_id, "artifact")
            if hashlib.sha256(canonical_package).hexdigest() != content_digest:
                raise _fail("LOCAL_KNOWLEDGE_COPY_DIGEST_MISMATCH")
            try:
                parsed = json.loads(canonical_package)
                canonical = json.dumps(
                    parsed, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False, allow_nan=False,
                ).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
                raise _fail("LOCAL_KNOWLEDGE_COPY_INVALID") from error
            if canonical != canonical_package or not isinstance(parsed, dict):
                raise _fail("LOCAL_KNOWLEDGE_COPY_INVALID")
            if self._knowledge_manifest_digest(manifest) != manifest_digest_sha256:
                raise _fail("LOCAL_KNOWLEDGE_COPY_DIGEST_MISMATCH")

            existing = self.list_canonical_envelopes(workspace_id, "artifact", "ScopeSnapshot")
            replay = [item for item in existing if item.payload.get("idempotency_key") == idempotency_key]
            if replay:
                prior = replay[-1]
                if (
                    prior.aggregate_id != copy_id
                    or prior.payload.get("manifest_digest_sha256") != manifest_digest_sha256
                    or prior.payload.get("content_digest_sha256") != content_digest
                ):
                    raise _fail("LOCAL_KNOWLEDGE_COPY_IDEMPOTENCY_CONFLICT")
                return LocalKnowledgeCopy(
                    copy_id, package_id, str(prior.payload["object_id"]), content_digest,
                    manifest_digest_sha256, str(prior.payload["state"]), prior.version,
                )
            if any(item.aggregate_id == copy_id for item in existing):
                raise _fail("LOCAL_KNOWLEDGE_COPY_IMMUTABLE")

            object_id = self._put_file(
                workspace_id, "artifact", canonical_package,
                content_type="application/vnd.daon.knowledge-package+json",
            )
            created_at = _utc_now()
            payload: dict[str, object] = {
                **dict(manifest), "object_id": object_id, "state": "approved",
                "idempotency_key": idempotency_key,
                "manifest_digest_sha256": manifest_digest_sha256,
            }
            normalized, encoded = self._canonical_payload(payload)
            envelope_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            try:
                self.put_canonical_envelope(
                    workspace_id, "artifact", entity_type="ScopeSnapshot",
                    entity_id=f"{copy_id}:1", aggregate_id=copy_id, version=1,
                    schema_version=schema_version,
                    digest_sha256=envelope_digest, created_at=created_at,
                    previous_version_id=None, payload=normalized,
                )
            except LocalStorageError:
                self._remove_object_after_failed_import(workspace_id, object_id)
                raise
            return LocalKnowledgeCopy(
                copy_id, package_id, object_id, content_digest, manifest_digest_sha256,
                "approved", 1,
            )

    def _remove_object_after_failed_import(self, workspace_id: str, object_id: str) -> None:
        database = self._db()
        row = database.execute(
            "SELECT blob_name FROM local_objects WHERE workspace_id = ? AND area = 'artifact' "
            "AND object_id = ?", (workspace_id, object_id),
        ).fetchone()
        database.execute(
            "DELETE FROM local_objects WHERE workspace_id = ? AND area = 'artifact' "
            "AND object_id = ?", (workspace_id, object_id),
        )
        database.commit()
        if row is not None:
            try:
                delete_file(self._root, self._area_directory(workspace_id, "artifact") / str(row[0]))
            except (FileNotFoundError, OSError):
                pass

    def put_raw_source_bundle(
        self, workspace_id: str, plaintext: bytes, *, content_type: str,
        build_envelopes: Callable[[str], tuple[RawSourceCanonicalInput, ...]],
    ) -> str:
        """Commit one encrypted raw object and its immutable Canon projection."""
        with self._operation_lock:
            _scope(workspace_id, "source")
            object_id = self._put_file(
                workspace_id, "source", plaintext, content_type=content_type
            )
            allowed = {"SourceVersion", "IndexVersion", "EvidenceSpan"}
            prepared: list[tuple[RawSourceCanonicalInput, str, str]] = []
            try:
                envelopes = build_envelopes(object_id)
                for envelope in envelopes:
                    if (
                        envelope.entity_type not in allowed
                        or not _CANON_ID.fullmatch(envelope.entity_id)
                        or not _CANON_ID.fullmatch(envelope.aggregate_id)
                        or not _UTC_TIMESTAMP.fullmatch(envelope.created_at)
                    ):
                        raise _fail("LOCAL_CANON_SNAPSHOT_INVALID")
                    normalized, encoded = self._canonical_payload(envelope.payload)
                    prepared.append((envelope, json.dumps(
                        normalized, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=False, allow_nan=False,
                    ), hashlib.sha256(encoded.encode("utf-8")).hexdigest()))
                database = self._db()
                database.execute("BEGIN")
                for envelope, encoded, digest in prepared:
                    database.execute(
                        "INSERT INTO canonical_envelopes "
                        "(workspace_id,area,entity_type,entity_id,aggregate_id,version,"
                        "schema_version,digest_sha256,created_at,previous_version_id,"
                        "payload_json,data_area) VALUES (?,?,?,?,?,1,1,?,?,NULL,?,'local_private')",
                        (
                            workspace_id, "source", envelope.entity_type,
                            envelope.entity_id, envelope.aggregate_id, digest,
                            envelope.created_at, encoded,
                        ),
                    )
                database.commit()
                return object_id
            except (LocalStorageError, sqlite.DatabaseError, ValueError, TypeError) as error:
                self._db().rollback()
                row = self._db().execute(
                    "SELECT blob_name FROM local_objects WHERE workspace_id = ? "
                    "AND area = 'source' AND object_id = ?", (workspace_id, object_id),
                ).fetchone()
                self._db().execute(
                    "DELETE FROM local_objects WHERE workspace_id = ? AND area = 'source' "
                    "AND object_id = ?", (workspace_id, object_id),
                )
                self._db().commit()
                try:
                    if row is not None:
                        delete_file(
                            self._root,
                            self._area_directory(workspace_id, "source") / str(row[0]),
                        )
                except (FileNotFoundError, OSError):
                    pass
                if isinstance(error, LocalStorageError):
                    raise
                raise _fail("LOCAL_CANON_IMMUTABLE") from error

    def refresh_knowledge_copy(
        self, workspace_id: str, copy_id: str, *, state: str, recorded_at: str,
    ) -> LocalKnowledgeCopy:
        with self._operation_lock:
            _scope(workspace_id, "artifact")
            if (
                not _CANON_ID.fullmatch(copy_id)
                or state not in {"approved", "revoked", "expired"}
                or not _UTC_TIMESTAMP.fullmatch(recorded_at)
            ):
                raise _fail("LOCAL_KNOWLEDGE_COPY_INVALID")
            try:
                owner = self.find_canonical_workspace("artifact", "ScopeSnapshot", copy_id)
            except LocalStorageError as error:
                raise _fail("LOCAL_KNOWLEDGE_COPY_NOT_FOUND") from error
            if owner != workspace_id:
                raise _fail("LOCAL_KNOWLEDGE_COPY_NOT_FOUND")
            prior = [
                item for item in self.list_canonical_envelopes(
                    workspace_id, "artifact", "ScopeSnapshot"
                ) if item.aggregate_id == copy_id
            ][-1]
            if prior.payload.get("state") == state:
                return LocalKnowledgeCopy(
                    copy_id, str(prior.payload["package_id"]), str(prior.payload["object_id"]),
                    str(prior.payload["content_digest_sha256"]),
                    str(prior.payload["manifest_digest_sha256"]), state, prior.version,
                )
            payload = {**prior.payload, "state": state, "recorded_at": recorded_at}
            normalized, encoded = self._canonical_payload(payload)
            version = prior.version + 1
            self.put_canonical_envelope(
                workspace_id, "artifact", entity_type="ScopeSnapshot",
                entity_id=f"{copy_id}:{version}", aggregate_id=copy_id, version=version,
                schema_version=prior.schema_version,
                digest_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                created_at=recorded_at, previous_version_id=prior.entity_id,
                payload=normalized,
            )
            return LocalKnowledgeCopy(
                copy_id, str(payload["package_id"]), str(payload["object_id"]),
                str(payload["content_digest_sha256"]),
                str(payload["manifest_digest_sha256"]), state, version,
            )

    def find_canonical_workspace(
        self, area: str, entity_type: str, aggregate_id: str
    ) -> str:
        """Resolve one opaque aggregate to its workspace without exposing a broad listing."""
        with self._operation_lock:
            if (
                area not in _AREAS or entity_type not in _CANON_ENTITY_TYPES
                or not _CANON_ID.fullmatch(aggregate_id)
            ):
                raise _fail("LOCAL_CANON_SNAPSHOT_INVALID")
            rows = self._db().execute(
                "SELECT DISTINCT workspace_id FROM canonical_envelopes "
                "WHERE area = ? AND entity_type = ? AND aggregate_id = ? LIMIT 2",
                (area, entity_type, aggregate_id),
            ).fetchall()
            if len(rows) != 1:
                raise _fail("LOCAL_CANON_NOT_FOUND")
            return str(rows[0][0])

    def execute_canonical_mutation_for_test(
        self, statement: str, parameters: tuple[object, ...]
    ) -> None:
        """Exercise DB immutability in tests without exposing a product mutation API."""
        with self._operation_lock:
            if not statement.startswith(("UPDATE canonical_envelopes", "DELETE FROM canonical_envelopes")):
                raise _fail("LOCAL_CANON_SNAPSHOT_INVALID")
            self._db().execute(statement, parameters)

    def append_sync_queue_state(
        self,
        workspace_id: str,
        *,
        operation_id: str,
        version: int,
        approval_state: str,
        manifest_digest: str,
        batch_cursor: str | None,
        conflict_id: str | None,
        queued_at: str,
        previous_version: int | None,
    ) -> None:
        """Append encrypted reconnect metadata without copying payloads or credentials."""
        with self._operation_lock:
            _scope(workspace_id, "cache")
            if (
                not _CANON_ID.fullmatch(operation_id)
                or not isinstance(version, int)
                or isinstance(version, bool)
                or version < 1
                or approval_state not in {
                    "draft", "awaiting_approval", "approved", "transferring",
                    "conflict", "reindex_requested", "blocked", "cancelled",
                }
                or not _DIGEST.fullmatch(manifest_digest)
                or (batch_cursor is not None and not _CANON_ID.fullmatch(batch_cursor))
                or (conflict_id is not None and not _CANON_ID.fullmatch(conflict_id))
                or not _UTC_TIMESTAMP.fullmatch(queued_at)
                or (version == 1) != (previous_version is None)
                or (previous_version is not None and previous_version != version - 1)
            ):
                raise _fail("LOCAL_SYNC_QUEUE_INVALID")
            database = self._db()
            owners = database.execute(
                "SELECT DISTINCT workspace_id FROM sync_queue_states WHERE operation_id = ? LIMIT 2",
                (operation_id,),
            ).fetchall()
            if owners and owners != [(workspace_id,)]:
                raise _fail("LOCAL_SYNC_QUEUE_NOT_FOUND")
            replay = database.execute(
                "SELECT approval_state, manifest_digest, batch_cursor, conflict_id, queued_at, "
                "previous_version FROM sync_queue_states WHERE workspace_id = ? "
                "AND operation_id = ? AND version = ?",
                (workspace_id, operation_id, version),
            ).fetchone()
            if replay is not None:
                if replay[:4] == (
                    approval_state, manifest_digest, batch_cursor, conflict_id,
                ) and replay[5] == previous_version:
                    return
                raise _fail("LOCAL_SYNC_QUEUE_IMMUTABLE")
            if previous_version is not None:
                prior = database.execute(
                    "SELECT version FROM sync_queue_states WHERE workspace_id = ? "
                    "AND operation_id = ? AND version = ?",
                    (workspace_id, operation_id, previous_version),
                ).fetchone()
                if prior != (previous_version,):
                    raise _fail("LOCAL_SYNC_QUEUE_PREVIOUS_INVALID")
            try:
                database.execute(
                    "INSERT INTO sync_queue_states "
                    "(workspace_id, operation_id, version, approval_state, manifest_digest, "
                    "batch_cursor, conflict_id, queued_at, previous_version) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        workspace_id, operation_id, version, approval_state,
                        manifest_digest, batch_cursor, conflict_id, queued_at,
                        previous_version,
                    ),
                )
                database.commit()
            except sqlite.IntegrityError as error:
                database.rollback()
                raise _fail("LOCAL_SYNC_QUEUE_IMMUTABLE") from error

    def get_sync_queue_state(
        self, workspace_id: str, operation_id: str
    ) -> LocalSyncQueueState:
        with self._operation_lock:
            _scope(workspace_id, "cache")
            if not _CANON_ID.fullmatch(operation_id):
                raise _fail("LOCAL_SYNC_QUEUE_INVALID")
            row = self._db().execute(
                "SELECT version, approval_state, manifest_digest, batch_cursor, "
                "conflict_id, queued_at, previous_version FROM sync_queue_states "
                "WHERE workspace_id = ? AND operation_id = ? ORDER BY version DESC LIMIT 1",
                (workspace_id, operation_id),
            ).fetchone()
            if row is None:
                raise _fail("LOCAL_SYNC_QUEUE_NOT_FOUND")
            return LocalSyncQueueState(
                operation_id, int(row[0]), str(row[1]), str(row[2]),
                None if row[3] is None else str(row[3]),
                None if row[4] is None else str(row[4]), str(row[5]),
                None if row[6] is None else int(row[6]),
            )

    def get_sync_queue_state_global(self, operation_id: str) -> LocalSyncQueueState:
        """Resolve an opaque operation without accepting a caller-controlled workspace."""
        with self._operation_lock:
            if not _CANON_ID.fullmatch(operation_id):
                raise _fail("LOCAL_SYNC_QUEUE_INVALID")
            rows = self._db().execute(
                "SELECT DISTINCT workspace_id FROM sync_queue_states WHERE operation_id = ? LIMIT 2",
                (operation_id,),
            ).fetchall()
            if len(rows) != 1:
                raise _fail("LOCAL_SYNC_QUEUE_NOT_FOUND")
            return self.get_sync_queue_state(str(rows[0][0]), operation_id)

    def list_resumable_sync_operations(self, workspace_id: str) -> list[str]:
        with self._operation_lock:
            _scope(workspace_id, "cache")
            rows = self._db().execute(
                "SELECT current.operation_id FROM sync_queue_states AS current "
                "JOIN (SELECT operation_id, max(version) AS version FROM sync_queue_states "
                "WHERE workspace_id = ? GROUP BY operation_id) AS latest "
                "ON latest.operation_id = current.operation_id AND latest.version = current.version "
                "WHERE current.workspace_id = ? AND current.approval_state IN "
                "('approved','transferring','conflict') ORDER BY current.operation_id",
                (workspace_id, workspace_id),
            ).fetchall()
            return [str(row[0]) for row in rows]

    def append_deletion_tombstone(
        self,
        workspace_id: str,
        *,
        request_id: str,
        reference_id: str,
        version: int,
        state: str,
        evidence: str | None,
        recorded_at: str,
        previous_version: int | None,
    ) -> None:
        """Append encrypted Local Copy access-revocation evidence."""
        with self._operation_lock:
            _scope(workspace_id, "cache")
            allowed = {
                "pending_ack": None,
                "acknowledged": "device_ack",
                "device_revoked": "device_revoked",
                "key_destroyed": "key_destroyed",
            }
            if (
                not _CANON_ID.fullmatch(request_id)
                or not _CANON_ID.fullmatch(reference_id)
                or not isinstance(version, int)
                or isinstance(version, bool)
                or version < 1
                or state not in allowed
                or evidence != allowed[state]
                or not _UTC_TIMESTAMP.fullmatch(recorded_at)
                or (version == 1) != (previous_version is None)
                or (previous_version is not None and previous_version != version - 1)
            ):
                raise _fail("LOCAL_DELETION_TOMBSTONE_INVALID")
            database = self._db()
            if previous_version is not None:
                prior = database.execute(
                    "SELECT version FROM deletion_tombstones WHERE workspace_id = ? "
                    "AND request_id = ? AND reference_id = ? AND version = ?",
                    (workspace_id, request_id, reference_id, previous_version),
                ).fetchone()
                if prior != (previous_version,):
                    raise _fail("LOCAL_DELETION_TOMBSTONE_PREVIOUS_INVALID")
            try:
                database.execute(
                    "INSERT INTO deletion_tombstones "
                    "(workspace_id,request_id,reference_id,version,state,evidence,"
                    "recorded_at,previous_version) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        workspace_id, request_id, reference_id, version, state,
                        evidence, recorded_at, previous_version,
                    ),
                )
                database.commit()
            except sqlite.IntegrityError as error:
                database.rollback()
                raise _fail("LOCAL_DELETION_TOMBSTONE_IMMUTABLE") from error

    def get_deletion_tombstone(
        self, workspace_id: str, request_id: str, reference_id: str
    ) -> LocalDeletionTombstone:
        with self._operation_lock:
            _scope(workspace_id, "cache")
            if not _CANON_ID.fullmatch(request_id) or not _CANON_ID.fullmatch(reference_id):
                raise _fail("LOCAL_DELETION_TOMBSTONE_INVALID")
            row = self._db().execute(
                "SELECT version,state,evidence,recorded_at,previous_version "
                "FROM deletion_tombstones WHERE workspace_id=? AND request_id=? "
                "AND reference_id=? ORDER BY version DESC LIMIT 1",
                (workspace_id, request_id, reference_id),
            ).fetchone()
            if row is None:
                raise _fail("LOCAL_DELETION_TOMBSTONE_NOT_FOUND")
            return LocalDeletionTombstone(
                request_id, reference_id, int(row[0]), str(row[1]),
                None if row[2] is None else str(row[2]), str(row[3]),
                None if row[4] is None else int(row[4]),
            )

    def list_completed_deletion_tombstones(
        self, workspace_id: str, request_id: str
    ) -> list[str]:
        with self._operation_lock:
            _scope(workspace_id, "cache")
            if not _CANON_ID.fullmatch(request_id):
                raise _fail("LOCAL_DELETION_TOMBSTONE_INVALID")
            rows = self._db().execute(
                "SELECT current.reference_id FROM deletion_tombstones AS current "
                "JOIN (SELECT reference_id,max(version) AS version FROM deletion_tombstones "
                "WHERE workspace_id=? AND request_id=? GROUP BY reference_id) AS latest "
                "ON latest.reference_id=current.reference_id AND latest.version=current.version "
                "WHERE current.workspace_id=? AND current.request_id=? "
                "AND current.state IN ('acknowledged','device_revoked','key_destroyed') "
                "ORDER BY current.reference_id",
                (workspace_id, request_id, workspace_id, request_id),
            ).fetchall()
            return [str(row[0]) for row in rows]

    def append_recovery_job_state(
        self,
        workspace_id: str,
        *,
        job_id: str,
        version: int,
        state: str,
        target_id: str,
        snapshot_checksum: str,
        actual_checksum: str,
        journal_present: bool,
        recorded_at: str,
        previous_version: int | None,
    ) -> None:
        """Append an encrypted recovery/quarantine state without storing raw paths."""
        with self._operation_lock:
            _scope(workspace_id, "cache")
            allowed = {
                "detected", "quarantined", "scanning", "repairable", "repairing",
                "verified", "manual_recovery_required", "failed",
            }
            if (
                not _CANON_ID.fullmatch(job_id)
                or not _CANON_ID.fullmatch(target_id)
                or not isinstance(version, int)
                or isinstance(version, bool)
                or version < 1
                or state not in allowed
                or not _DIGEST.fullmatch(snapshot_checksum)
                or not _DIGEST.fullmatch(actual_checksum)
                or not isinstance(journal_present, bool)
                or not _UTC_TIMESTAMP.fullmatch(recorded_at)
                or (version == 1) != (previous_version is None)
                or (previous_version is not None and previous_version != version - 1)
            ):
                raise _fail("LOCAL_RECOVERY_JOB_INVALID")
            database = self._db()
            if previous_version is not None:
                prior = database.execute(
                    "SELECT version FROM recovery_job_states WHERE workspace_id=? "
                    "AND job_id=? AND version=?",
                    (workspace_id, job_id, previous_version),
                ).fetchone()
                if prior != (previous_version,):
                    raise _fail("LOCAL_RECOVERY_JOB_PREVIOUS_INVALID")
            try:
                database.execute(
                    "INSERT INTO recovery_job_states "
                    "(workspace_id,job_id,version,state,target_id,snapshot_checksum,"
                    "actual_checksum,journal_present,recorded_at,previous_version) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        workspace_id, job_id, version, state, target_id,
                        snapshot_checksum, actual_checksum, int(journal_present),
                        recorded_at, previous_version,
                    ),
                )
                database.commit()
            except sqlite.IntegrityError as error:
                database.rollback()
                raise _fail("LOCAL_RECOVERY_JOB_IMMUTABLE") from error

    def get_recovery_job_state(
        self, workspace_id: str, job_id: str
    ) -> LocalRecoveryJobState:
        with self._operation_lock:
            _scope(workspace_id, "cache")
            if not _CANON_ID.fullmatch(job_id):
                raise _fail("LOCAL_RECOVERY_JOB_INVALID")
            row = self._db().execute(
                "SELECT version,state,target_id,snapshot_checksum,actual_checksum,"
                "journal_present,recorded_at,previous_version FROM recovery_job_states "
                "WHERE workspace_id=? AND job_id=? ORDER BY version DESC LIMIT 1",
                (workspace_id, job_id),
            ).fetchone()
            if row is None:
                raise _fail("LOCAL_RECOVERY_JOB_NOT_FOUND")
            return LocalRecoveryJobState(
                job_id, int(row[0]), str(row[1]), str(row[2]), str(row[3]),
                str(row[4]), bool(row[5]), str(row[6]),
                None if row[7] is None else int(row[7]),
            )

    def find_recovery_job_state(self, job_id: str) -> tuple[str, LocalRecoveryJobState]:
        with self._operation_lock:
            if not _CANON_ID.fullmatch(job_id):
                raise _fail("LOCAL_RECOVERY_JOB_INVALID")
            rows = self._db().execute(
                "SELECT workspace_id,version,state,target_id,snapshot_checksum,"
                "actual_checksum,journal_present,recorded_at,previous_version "
                "FROM recovery_job_states WHERE job_id=? ORDER BY version DESC",
                (job_id,),
            ).fetchall()
            if not rows:
                raise _fail("LOCAL_RECOVERY_JOB_NOT_FOUND")
            workspace_id = str(rows[0][0])
            if any(str(row[0]) != workspace_id for row in rows):
                raise _fail("LOCAL_RECOVERY_JOB_SCOPE_CONFLICT")
            row = rows[0]
            return workspace_id, LocalRecoveryJobState(
                job_id, int(row[1]), str(row[2]), str(row[3]), str(row[4]),
                str(row[5]), bool(row[6]), str(row[7]),
                None if row[8] is None else int(row[8]),
            )

    def put_vector(
        self,
        workspace_id: str,
        area: str,
        item_id: str,
        embedding: list[float],
        *,
        model_digest: str,
        artifact_digest: str,
        embedding_version: str,
        source_version: str,
        object_version: str,
    ) -> None:
        with self._operation_lock:
            self._put_vector(
                workspace_id,
                area,
                item_id,
                embedding,
                model_digest=model_digest,
                artifact_digest=artifact_digest,
                embedding_version=embedding_version,
                source_version=source_version,
                object_version=object_version,
            )

    def _put_vector(
        self,
        workspace_id: str,
        area: str,
        item_id: str,
        embedding: list[float],
        *,
        model_digest: str,
        artifact_digest: str,
        embedding_version: str,
        source_version: str,
        object_version: str,
    ) -> None:
        _scope(workspace_id, area)
        _validate_embedding(embedding)
        if (
            not item_id
            or len(item_id) > 256
            or not embedding
            or not _DIGEST.fullmatch(model_digest)
            or not _DIGEST.fullmatch(artifact_digest)
            or not all(
                isinstance(value, str) and 1 <= len(value) <= 128
                for value in (embedding_version, source_version, object_version)
            )
        ):
            raise _fail("LOCAL_VECTOR_INVALID")
        database = self._db()
        table = self._vector_table(workspace_id, area, len(embedding))
        prior = database.execute(
            "SELECT vec_table, vec_rowid FROM vector_items "
            "WHERE workspace_id = ? AND area = ? AND item_id = ?",
            (workspace_id, area, item_id),
        ).fetchone()
        try:
            if prior is not None:
                database.execute(f"DELETE FROM {prior[0]} WHERE rowid = ?", (prior[1],))
                database.execute(
                    "DELETE FROM vector_items WHERE workspace_id = ? AND area = ? AND item_id = ?",
                    (workspace_id, area, item_id),
                )
            cursor = database.execute(
                f"INSERT INTO {table}(embedding) VALUES (?)",
                (sqlite_vec.serialize_float32(embedding),),
            )
            database.execute(
                "INSERT INTO vector_items VALUES "
                "(?, ?, ?, ?, ?, ?, 'cosine', ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    area,
                    item_id,
                    table,
                    cursor.lastrowid,
                    len(embedding),
                    _KEY_VERSION,
                    model_digest,
                    artifact_digest,
                    embedding_version,
                    source_version,
                    object_version,
                ),
            )
            database.commit()
        except sqlite.DatabaseError as error:
            database.rollback()
            raise _fail("LOCAL_VECTOR_WRITE_FAILED") from error

    def search_vectors(
        self,
        workspace_id: str,
        area: str,
        embedding: list[float],
        limit: int,
    ) -> list[str]:
        with self._operation_lock:
            return self._search_vectors(workspace_id, area, embedding, limit)

    def _search_vectors(
        self,
        workspace_id: str,
        area: str,
        embedding: list[float],
        limit: int,
    ) -> list[str]:
        _scope(workspace_id, area)
        _validate_embedding(embedding)
        if not 1 <= limit <= 100:
            raise _fail("LOCAL_VECTOR_INVALID")
        database = self._db()
        table = self._vector_table(workspace_id, area, len(embedding))
        rows = database.execute(
            f"SELECT rowid FROM {table} WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(embedding), limit),
        ).fetchall()
        result: list[str] = []
        for (rowid,) in rows:
            item = database.execute(
                "SELECT item_id FROM vector_items WHERE workspace_id = ? AND area = ? "
                "AND vec_table = ? AND vec_rowid = ?",
                (workspace_id, area, table, rowid),
            ).fetchone()
            if item is not None:
                result.append(str(item[0]))
        return result

    def lock(self) -> None:
        with self._operation_lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            for index in range(len(self._master_key)):
                self._master_key[index] = 0
            self._master_key.clear()

    def close(self) -> None:
        self.lock()

    def is_unlocked(self) -> bool:
        with self._operation_lock:
            return self._connection is not None and bool(self._master_key)

    def __enter__(self) -> "LocalEncryptedStore":
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()
