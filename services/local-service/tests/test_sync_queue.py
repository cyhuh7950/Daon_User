from __future__ import annotations

import socket
from pathlib import Path

import pytest

from daon_user_local_service.local_storage import LocalEncryptedStore, LocalStorageError


WORKSPACE = "33333333-3333-4333-8333-333333333333"
MASTER_KEY = bytes(range(32))
MANIFEST_DIGEST = "a" * 64


def test_sync_queue_is_encrypted_restartable_and_only_approved_entries_resume(tmp_path: Path) -> None:
    root = tmp_path / "sync-queue"
    with LocalEncryptedStore.open(root, MASTER_KEY) as store:
        store.append_sync_queue_state(
            WORKSPACE, operation_id="sync-local-1", version=1,
            approval_state="draft", manifest_digest=MANIFEST_DIGEST,
            batch_cursor=None, conflict_id=None, queued_at="2026-07-30T13:00:00Z",
            previous_version=None,
        )
        assert store.list_resumable_sync_operations(WORKSPACE) == []
        store.append_sync_queue_state(
            WORKSPACE, operation_id="sync-local-1", version=2,
            approval_state="approved", manifest_digest=MANIFEST_DIGEST,
            batch_cursor="cursor-1", conflict_id=None, queued_at="2026-07-30T13:01:00Z",
            previous_version=1,
        )
        assert store.list_resumable_sync_operations(WORKSPACE) == ["sync-local-1"]
    with LocalEncryptedStore.open(root, MASTER_KEY) as reopened:
        state = reopened.get_sync_queue_state(WORKSPACE, "sync-local-1")
        assert (state.version, state.batch_cursor, state.approval_state) == (2, "cursor-1", "approved")
    raw = (root / "metadata.db").read_bytes()
    assert b"sync-local-1" not in raw
    assert b"cursor-1" not in raw


def test_offline_queue_opens_no_network_and_lock_blocks_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts: list[object] = []

    def reject_network(_socket: socket.socket, address: object) -> None:
        attempts.append(address)
        raise AssertionError("LOCAL_SYNC_NETWORK_FORBIDDEN")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    store = LocalEncryptedStore.open(tmp_path / "sync-offline", MASTER_KEY)
    store.append_sync_queue_state(
        WORKSPACE, operation_id="sync-local-2", version=1,
        approval_state="approved", manifest_digest=MANIFEST_DIGEST,
        batch_cursor=None, conflict_id=None, queued_at="2026-07-30T13:00:00Z",
        previous_version=None,
    )
    assert store.list_resumable_sync_operations(WORKSPACE) == ["sync-local-2"]
    store.lock()
    with pytest.raises(LocalStorageError, match="LOCAL_KEY_UNAVAILABLE"):
        store.list_resumable_sync_operations(WORKSPACE)
    assert attempts == []
