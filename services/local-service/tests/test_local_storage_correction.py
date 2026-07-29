from __future__ import annotations

import os
import hashlib
import struct
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlcipher3 import dbapi2 as sqlite  # type: ignore[import-untyped]

from daon_user_local_service.local_storage import LocalEncryptedStore, LocalStorageError


WORKSPACE = "11111111-1111-4111-8111-111111111111"
MODEL_DIGEST = "a" * 64
ARTIFACT_DIGEST = "b" * 64


def _encrypted_rows(root: Path, key: bytes, query: str) -> list[tuple[object, ...]]:
    connection = sqlite.connect(root / "metadata.db")
    try:
        connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        return list(connection.execute(query))
    finally:
        connection.close()


def _create_v1_schema(root: Path, key: bytes) -> None:
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite.connect(root / "metadata.db")
    try:
        connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        connection.executescript(
            """
            CREATE TABLE storage_identity (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1), marker TEXT NOT NULL
            );
            INSERT INTO storage_identity VALUES (1, 'daon-local-encrypted-v1');
            CREATE TABLE area_keys (
                workspace_id TEXT NOT NULL, area TEXT NOT NULL, key_version INTEGER NOT NULL,
                nonce BLOB NOT NULL, wrapped_dek BLOB NOT NULL,
                PRIMARY KEY (workspace_id, area, key_version)
            );
            CREATE TABLE local_objects (
                object_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, area TEXT NOT NULL,
                key_version INTEGER NOT NULL, digest BLOB NOT NULL, plain_size INTEGER NOT NULL,
                blob_name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE vector_items (
                workspace_id TEXT NOT NULL, area TEXT NOT NULL, item_id TEXT NOT NULL,
                vec_table TEXT NOT NULL, vec_rowid INTEGER NOT NULL, dimension INTEGER NOT NULL,
                metric TEXT NOT NULL, key_version INTEGER NOT NULL, model_digest TEXT NOT NULL,
                artifact_digest TEXT NOT NULL, embedding_version TEXT NOT NULL,
                source_version TEXT NOT NULL, object_version TEXT NOT NULL,
                PRIMARY KEY (workspace_id, area, item_id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_v1_schema_upgrades_additively_and_new_objects_store_verified_metadata(
    tmp_path: Path,
) -> None:
    key = os.urandom(32)
    _create_v1_schema(tmp_path, key)

    store = LocalEncryptedStore.open(tmp_path, key)
    object_id = store.put_file(
        WORKSPACE, "source", b"verified text", content_type="text/plain"
    )
    store.close()

    object_columns = {
        str(row[1]) for row in _encrypted_rows(tmp_path, key, "PRAGMA table_info(local_objects)")
    }
    key_columns = {
        str(row[1]) for row in _encrypted_rows(tmp_path, key, "PRAGMA table_info(area_keys)")
    }
    assert {"content_type", "object_version", "created_at", "updated_at", "status"} <= object_columns
    assert {"wrap_algorithm", "created_at", "rotated_at", "status"} <= key_columns
    metadata = _encrypted_rows(
        tmp_path,
        key,
        "SELECT object_id, content_type, object_version, status FROM local_objects",
    )
    assert metadata == [(object_id, "text/plain", "1", "active")]


@pytest.mark.parametrize(
    ("content_type", "payload"),
    [
        ("application/pdf", b"not a pdf"),
        ("image/png", b"not a png"),
        ("text/plain", b"nul\x00text"),
        ("text/plain", b"invalid-utf8-\xff"),
        ("application/x-unknown", b"payload"),
    ],
)
def test_content_type_must_be_allowlisted_and_match_payload(
    tmp_path: Path, content_type: str, payload: bytes
) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    with pytest.raises(LocalStorageError, match="LOCAL_CONTENT_TYPE_INVALID"):
        store.put_file(WORKSPACE, "source", payload, content_type=content_type)
    store.close()


@pytest.mark.parametrize("offset", [8, 9, 14])
def test_unknown_or_tampered_v2_header_fields_fail_closed(tmp_path: Path, offset: int) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    object_id = store.put_file(WORKSPACE, "source", b"header", content_type="text/plain")
    blob = store.blob_path_for_test(WORKSPACE, "source", object_id)
    damaged = bytearray(blob.read_bytes())
    damaged[offset] ^= 0x7F
    blob.write_bytes(damaged)

    with pytest.raises(LocalStorageError, match="LOCAL_CIPHERTEXT_CORRUPT"):
        store.get_file(WORKSPACE, "source", object_id)
    store.close()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), 1e39])
def test_vector_put_and_search_reject_non_finite_or_float32_overflow(
    tmp_path: Path, invalid: float
) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    kwargs = {
        "model_digest": MODEL_DIGEST,
        "artifact_digest": ARTIFACT_DIGEST,
        "embedding_version": "embedding-v1",
        "source_version": "source-v1",
        "object_version": "object-v1",
    }
    with pytest.raises(LocalStorageError, match="LOCAL_VECTOR_INVALID"):
        store.put_vector(WORKSPACE, "source", "bad", [0.0, invalid], **kwargs)
    with pytest.raises(LocalStorageError, match="LOCAL_VECTOR_INVALID"):
        store.search_vectors(WORKSPACE, "source", [0.0, invalid], 1)
    store.close()


def test_hardlink_is_rejected_without_reading_other_link(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    object_id = store.put_file(WORKSPACE, "source", b"hardlink", content_type="text/plain")
    blob = store.blob_path_for_test(WORKSPACE, "source", object_id)
    other = tmp_path / "outside-link.bin"
    os.link(blob, other)
    with pytest.raises(LocalStorageError, match="LOCAL_PATH_UNSAFE"):
        store.get_file(WORKSPACE, "source", object_id)
    store.close()


def test_existing_v1_ciphertext_remains_readable_after_additive_upgrade(tmp_path: Path) -> None:
    key = os.urandom(32)
    _create_v1_schema(tmp_path, key)
    object_id = "33333333-3333-4333-8333-333333333333"
    plaintext = b"legacy ciphertext"
    digest = hashlib.sha256(plaintext).digest()
    nonce = os.urandom(12)
    dek = AESGCM.generate_key(bit_length=256)
    wrap_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"daon-local-wrap-v1",
        info=b"area-dek-envelope",
    ).derive(key)
    wrap_nonce = os.urandom(12)
    area_aad = f"{WORKSPACE}|source|1".encode()
    object_aad = f"{WORKSPACE}|source|{object_id}|1".encode()
    blob_name = f"{object_id}.bin"
    connection = sqlite.connect(tmp_path / "metadata.db")
    try:
        connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        connection.execute(
            "INSERT INTO area_keys VALUES (?, ?, 1, ?, ?)",
            (WORKSPACE, "source", wrap_nonce, AESGCM(wrap_key).encrypt(wrap_nonce, dek, area_aad)),
        )
        connection.execute(
            "INSERT INTO local_objects VALUES (?, ?, 'source', 1, ?, ?, ?)",
            (object_id, WORKSPACE, digest, len(plaintext), blob_name),
        )
        connection.commit()
    finally:
        connection.close()
    scope = hashlib.sha256(f"{WORKSPACE}|source".encode()).hexdigest()
    directory = tmp_path / "files" / scope[:2] / scope[2:]
    directory.mkdir(parents=True)
    header = struct.pack(">8sBI12s32s", b"DAONENC1", 1, len(plaintext), nonce, digest)
    (directory / blob_name).write_bytes(
        header + AESGCM(dek).encrypt(nonce, plaintext, object_aad)
    )

    upgraded = LocalEncryptedStore.open(tmp_path, key)
    assert upgraded.get_file(WORKSPACE, "source", object_id) == plaintext
    upgraded.close()


def test_inactive_area_key_fails_closed(tmp_path: Path) -> None:
    key = os.urandom(32)
    store = LocalEncryptedStore.open(tmp_path, key)
    object_id = store.put_file(WORKSPACE, "source", b"key state", content_type="text/plain")
    store.close()
    connection = sqlite.connect(tmp_path / "metadata.db")
    try:
        connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        connection.execute("UPDATE area_keys SET status = 'retired'")
        connection.commit()
    finally:
        connection.close()
    reopened = LocalEncryptedStore.open(tmp_path, key)
    with pytest.raises(LocalStorageError, match="LOCAL_CIPHERTEXT_CORRUPT"):
        reopened.get_file(WORKSPACE, "source", object_id)
    reopened.close()


def test_database_insert_failure_removes_new_ciphertext(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    store._db().execute(  # noqa: SLF001 - intentional failure injection at transaction boundary
        "CREATE TRIGGER reject_object BEFORE INSERT ON local_objects "
        "BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    store._db().commit()  # noqa: SLF001
    with pytest.raises(LocalStorageError, match="LOCAL_WRITE_FAILED"):
        store.put_file(WORKSPACE, "source", b"rollback", content_type="text/plain")
    assert store.list_object_ids(WORKSPACE, "source") == []
    assert not list((tmp_path / "files").rglob("*.bin"))
    assert not list((tmp_path / "files").rglob("*.tmp"))
    store.close()


def test_restart_recovery_removes_temp_and_uncommitted_orphan(tmp_path: Path) -> None:
    key = os.urandom(32)
    store = LocalEncryptedStore.open(tmp_path, key)
    object_id = store.put_file(WORKSPACE, "source", b"kept", content_type="text/plain")
    directory = store.blob_path_for_test(WORKSPACE, "source", object_id).parent
    store.close()
    temporary = directory / ".interrupted.tmp"
    orphan = directory / "44444444-4444-4444-8444-444444444444.bin"
    temporary.write_bytes(b"partial")
    orphan.write_bytes(b"uncommitted ciphertext")

    reopened = LocalEncryptedStore.open(tmp_path, key)
    assert not temporary.exists()
    assert not orphan.exists()
    assert reopened.get_file(WORKSPACE, "source", object_id) == b"kept"
    reopened.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse contract")
def test_junction_swap_is_rejected_before_file_use(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    outside = tmp_path / "outside"
    store = LocalEncryptedStore.open(root, os.urandom(32))
    object_id = store.put_file(WORKSPACE, "source", b"inside", content_type="text/plain")
    directory = store.blob_path_for_test(WORKSPACE, "source", object_id).parent
    directory.rename(outside)
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(directory), str(outside)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(LocalStorageError, match="LOCAL_PATH_UNSAFE"):
            store.get_file(WORKSPACE, "source", object_id)
        assert (outside / f"{object_id}.bin").exists()
    finally:
        os.rmdir(directory)
        store.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows TOCTOU contract")
def test_junction_swap_immediately_after_path_check_cannot_escape_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import daon_user_local_service.safe_file as safe_file

    root = tmp_path / "storage"
    outside = tmp_path / "outside"
    store = LocalEncryptedStore.open(root, os.urandom(32))
    object_id = store.put_file(WORKSPACE, "source", b"race-safe", content_type="text/plain")
    directory = store.blob_path_for_test(WORKSPACE, "source", object_id).parent
    original_validate = safe_file.validate_directory_chain
    swapped = False

    def swap_after_check(validated_root: Path, validated_directory: Path) -> None:
        nonlocal swapped
        original_validate(validated_root, validated_directory)
        if not swapped and validated_directory == directory:
            swapped = True
            directory.rename(outside)
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(directory), str(outside)],
                capture_output=True,
                check=False,
                text=True,
            )
            assert result.returncode == 0, result.stderr

    monkeypatch.setattr(safe_file, "validate_directory_chain", swap_after_check)
    try:
        with pytest.raises(LocalStorageError, match="LOCAL_PATH_UNSAFE"):
            store.get_file(WORKSPACE, "source", object_id)
        assert swapped
        assert (outside / f"{object_id}.bin").exists()
    finally:
        monkeypatch.setattr(safe_file, "validate_directory_chain", original_validate)
        if directory.exists():
            os.rmdir(directory)
        store.close()
