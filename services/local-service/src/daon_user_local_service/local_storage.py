from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import uuid
from dataclasses import dataclass
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
    "EvidenceSpan", "Citation", "StudioOutput", "OutputVersion",
    "PendingOperationReference",
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
class LocalSyncQueueState:
    operation_id: str
    version: int
    approval_state: str
    manifest_digest: str
    batch_cursor: str | None
    conflict_id: str | None
    queued_at: str
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
