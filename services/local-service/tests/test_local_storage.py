from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from daon_user_local_service.local_storage import (
    LocalEncryptedStore,
    LocalStorageError,
)


WORKSPACE_A = "11111111-1111-4111-8111-111111111111"
WORKSPACE_B = "22222222-2222-4222-8222-222222222222"
CANARY = b"DAON-LOCAL-PLAINTEXT-CANARY"
MODEL_DIGEST = "a" * 64
ARTIFACT_DIGEST = "b" * 64


def _put_vector(store: LocalEncryptedStore, workspace_id: str, item_id: str) -> None:
    store.put_vector(
        workspace_id,
        "source",
        item_id,
        [1.0, 0.0, 0.0],
        model_digest=MODEL_DIGEST,
        artifact_digest=ARTIFACT_DIGEST,
        embedding_version="embedding-v1",
        source_version="source-v1",
        object_version="object-v1",
    )


def _all_storage_bytes(root: Path) -> bytes:
    return b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())


def test_metadata_file_and_vector_survive_restart_without_plaintext(tmp_path: Path) -> None:
    key = os.urandom(32)
    store = LocalEncryptedStore.open(tmp_path, key)
    object_id = store.put_file(WORKSPACE_A, "source", CANARY)
    _put_vector(store, WORKSPACE_A, "chunk-a")
    store.close()

    assert CANARY not in _all_storage_bytes(tmp_path)
    assert not (tmp_path / "metadata.db").read_bytes().startswith(b"SQLite format 3")

    reopened = LocalEncryptedStore.open(tmp_path, key)
    assert reopened.get_file(WORKSPACE_A, "source", object_id) == CANARY
    assert reopened.search_vectors(WORKSPACE_A, "source", [1.0, 0.0, 0.0], 1) == [
        "chunk-a"
    ]
    reopened.close()


def test_wrong_or_missing_key_never_recreates_existing_ciphertext(tmp_path: Path) -> None:
    key = os.urandom(32)
    store = LocalEncryptedStore.open(tmp_path, key)
    store.put_file(WORKSPACE_A, "source", CANARY)
    store.close()
    before = _all_storage_bytes(tmp_path)

    with pytest.raises(LocalStorageError, match="LOCAL_KEY_UNAVAILABLE"):
        LocalEncryptedStore.open(tmp_path, os.urandom(32))

    assert _all_storage_bytes(tmp_path) == before


def test_lock_and_corruption_fail_closed(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    object_id = store.put_file(WORKSPACE_A, "source", CANARY)
    blob = store.blob_path_for_test(WORKSPACE_A, "source", object_id)
    damaged = bytearray(blob.read_bytes())
    damaged[-1] ^= 1
    blob.write_bytes(damaged)

    with pytest.raises(LocalStorageError, match="LOCAL_CIPHERTEXT_CORRUPT"):
        store.get_file(WORKSPACE_A, "source", object_id)

    store.lock()
    with pytest.raises(LocalStorageError, match="LOCAL_KEY_UNAVAILABLE"):
        store.get_file(WORKSPACE_A, "source", object_id)


def test_workspace_scope_and_path_inputs_are_enforced(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    object_id = store.put_file(WORKSPACE_A, "source", CANARY)
    _put_vector(store, WORKSPACE_A, "chunk-a")

    with pytest.raises(LocalStorageError, match="LOCAL_OBJECT_NOT_FOUND"):
        store.get_file(WORKSPACE_B, "source", object_id)
    assert store.search_vectors(WORKSPACE_B, "source", [1.0, 0.0, 0.0], 5) == []

    for invalid in ("../escape", "C:\\escape", "source/child", ""):
        with pytest.raises(LocalStorageError, match="LOCAL_SCOPE_INVALID"):
            store.put_file(WORKSPACE_A, invalid, CANARY)
    store.close()


def test_failed_atomic_replace_leaves_no_successful_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr("daon_user_local_service.local_storage.os.replace", fail_replace)
    with pytest.raises(LocalStorageError, match="LOCAL_WRITE_FAILED"):
        store.put_file(WORKSPACE_A, "source", CANARY)
    assert store.list_object_ids(WORKSPACE_A, "source") == []
    assert not list((tmp_path / "files").rglob("*.tmp"))
    store.close()


def test_parallel_file_and_vector_operations_share_one_private_connection_lock(
    tmp_path: Path,
) -> None:
    store = LocalEncryptedStore.open(tmp_path, os.urandom(32))
    seed_id = store.put_file(WORKSPACE_A, "source", CANARY)
    _put_vector(store, WORKSPACE_A, "seed")

    def exercise(index: int) -> tuple[bytes, list[str], str]:
        object_id = store.put_file(
            WORKSPACE_A, "source", CANARY + index.to_bytes(2, "big")
        )
        _put_vector(store, WORKSPACE_A, f"chunk-{index}")
        return (
            store.get_file(WORKSPACE_A, "source", seed_id),
            store.search_vectors(WORKSPACE_A, "source", [1.0, 0.0, 0.0], 20),
            object_id,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(exercise, range(16)))

    assert all(payload == CANARY for payload, _matches, _object_id in results)
    assert all("seed" in matches for _payload, matches, _object_id in results)
    assert len(store.list_object_ids(WORKSPACE_A, "source")) == 17
    assert not hasattr(store, "connection")
    store.close()
