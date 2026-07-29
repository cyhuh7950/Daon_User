from __future__ import annotations

import hashlib
import os
import re
import struct
import uuid
from pathlib import Path
from threading import RLock
from typing import Final

import sqlite_vec  # type: ignore[import-untyped]
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from sqlcipher3 import dbapi2 as sqlite  # type: ignore[import-untyped]


_WORKSPACE_ID: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_AREAS: Final = frozenset({"source", "artifact", "cache", "index"})
_MAGIC: Final = b"DAONENC1"
_HEADER: Final = struct.Struct(">8sBI12s32s")
_KEY_VERSION: Final = 1
_DIGEST: Final = re.compile(r"^[0-9a-f]{64}$")


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


class LocalEncryptedStore:
    """SQLCipher metadata/vector store and AEAD file store for one local profile."""

    def __init__(
        self,
        root: Path,
        master_key: bytes,
        connection: sqlite.Connection,
        operation_lock: RLock,
    ) -> None:
        self._root = root
        self._master_key = bytearray(master_key)
        self._connection: sqlite.Connection | None = connection
        self._operation_lock = operation_lock

    @classmethod
    def open(cls, root: Path, master_key: bytes) -> "LocalEncryptedStore":
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
                PRIMARY KEY (workspace_id, area, key_version)
            );
            CREATE TABLE IF NOT EXISTS local_objects (
                object_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                area TEXT NOT NULL,
                key_version INTEGER NOT NULL,
                digest BLOB NOT NULL,
                plain_size INTEGER NOT NULL,
                blob_name TEXT NOT NULL UNIQUE
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
            """
        )
        marker = database.execute(
            "SELECT marker FROM storage_identity WHERE singleton = 1"
        ).fetchone()
        if marker != ("daon-local-encrypted-v1",):
            raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
        database.commit()
        # SQLCipher 4.12 on Windows must initialize the encrypted schema before
        # switching a new file to WAL mode. Doing this on a zero-page database
        # terminates the native driver before Python can surface an exception.
        database.execute("PRAGMA journal_mode = WAL").fetchone()

    def _wrap_key(self) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"daon-local-wrap-v1",
            info=b"area-dek-envelope",
        ).derive(bytes(self._master_key))

    def _area_dek(self, workspace_id: str, area: str) -> bytes:
        database = self._db()
        row = database.execute(
            "SELECT nonce, wrapped_dek FROM area_keys "
            "WHERE workspace_id = ? AND area = ? AND key_version = ?",
            (workspace_id, area, _KEY_VERSION),
        ).fetchone()
        aad = f"{workspace_id}|{area}|{_KEY_VERSION}".encode()
        wrapper = AESGCM(self._wrap_key())
        if row is None:
            dek = AESGCM.generate_key(bit_length=256)
            nonce = os.urandom(12)
            database.execute(
                "INSERT INTO area_keys VALUES (?, ?, ?, ?, ?)",
                (workspace_id, area, _KEY_VERSION, nonce, wrapper.encrypt(nonce, dek, aad)),
            )
            database.commit()
            return dek
        try:
            return wrapper.decrypt(bytes(row[0]), bytes(row[1]), aad)
        except InvalidTag as error:
            raise _fail("LOCAL_CIPHERTEXT_CORRUPT") from error

    def _area_directory(self, workspace_id: str, area: str) -> Path:
        scope = hashlib.sha256(f"{workspace_id}|{area}".encode()).hexdigest()
        directory = self._root / "files" / scope[:2] / scope[2:]
        directory.mkdir(parents=True, exist_ok=True)
        current = directory
        while current != self._root:
            if current.is_symlink() or _has_reparse_point(current):
                raise _fail("LOCAL_PATH_UNSAFE")
            current = current.parent
        return directory

    def put_file(self, workspace_id: str, area: str, plaintext: bytes) -> str:
        with self._operation_lock:
            return self._put_file(workspace_id, area, plaintext)

    def _put_file(self, workspace_id: str, area: str, plaintext: bytes) -> str:
        _scope(workspace_id, area)
        if not isinstance(plaintext, bytes):
            raise _fail("LOCAL_INPUT_INVALID")
        database = self._db()
        object_id = str(uuid.uuid4())
        blob_name = f"{object_id}.bin"
        directory = self._area_directory(workspace_id, area)
        destination = directory / blob_name
        temporary = directory / f".{object_id}.{uuid.uuid4().hex}.tmp"
        digest = hashlib.sha256(plaintext).digest()
        nonce = os.urandom(12)
        aad = f"{workspace_id}|{area}|{object_id}|{_KEY_VERSION}".encode()
        ciphertext = AESGCM(self._area_dek(workspace_id, area)).encrypt(nonce, plaintext, aad)
        payload = _HEADER.pack(_MAGIC, _KEY_VERSION, len(plaintext), nonce, digest) + ciphertext
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.stat().st_size != len(payload):
                raise OSError("short encrypted write")
            os.replace(temporary, destination)
            database.execute(
                "INSERT INTO local_objects VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    object_id,
                    workspace_id,
                    area,
                    _KEY_VERSION,
                    digest,
                    len(plaintext),
                    blob_name,
                ),
            )
            database.commit()
            return object_id
        except (OSError, sqlite.DatabaseError) as error:
            database.rollback()
            for candidate in (temporary, destination):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
            raise _fail("LOCAL_WRITE_FAILED") from error

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
            "SELECT key_version, digest, plain_size, blob_name FROM local_objects "
            "WHERE object_id = ? AND workspace_id = ? AND area = ?",
            (object_id, workspace_id, area),
        ).fetchone()
        if row is None:
            raise _fail("LOCAL_OBJECT_NOT_FOUND")
        path = self._area_directory(workspace_id, area) / str(row[3])
        try:
            stat = path.lstat()
            if path.is_symlink() or _has_reparse_point(path) or stat.st_nlink != 1:
                raise _fail("LOCAL_PATH_UNSAFE")
            payload = path.read_bytes()
            magic, key_version, plain_size, nonce, digest = _HEADER.unpack_from(payload)
            if (
                magic != _MAGIC
                or key_version != row[0]
                or plain_size != row[2]
                or digest != bytes(row[1])
            ):
                raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
            aad = f"{workspace_id}|{area}|{object_id}|{key_version}".encode()
            plaintext = AESGCM(self._area_dek(workspace_id, area)).decrypt(
                nonce, payload[_HEADER.size :], aad
            )
            if len(plaintext) != plain_size or hashlib.sha256(plaintext).digest() != digest:
                raise _fail("LOCAL_CIPHERTEXT_CORRUPT")
            return plaintext
        except LocalStorageError:
            raise
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
        if not 1 <= limit <= 100 or not embedding:
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
