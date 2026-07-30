from __future__ import annotations

from pathlib import Path

from daon_user_local_service.local_storage import LocalEncryptedStore


WORKSPACE = "44444444-4444-4444-8444-444444444444"
MASTER_KEY = bytes(range(32))


def test_local_copy_tombstone_is_encrypted_restartable_and_ack_gated(tmp_path: Path) -> None:
    root = tmp_path / "retention-local"
    with LocalEncryptedStore.open(root, MASTER_KEY) as store:
        store.append_deletion_tombstone(
            WORKSPACE, request_id="fixture-delete-local", reference_id="fixture-local-copy",
            version=1, state="pending_ack", evidence=None,
            recorded_at="2026-07-31T04:00:00Z", previous_version=None,
        )
        assert store.list_completed_deletion_tombstones(WORKSPACE, "fixture-delete-local") == []
    with LocalEncryptedStore.open(root, MASTER_KEY) as reopened:
        pending = reopened.get_deletion_tombstone(
            WORKSPACE, "fixture-delete-local", "fixture-local-copy"
        )
        assert pending.state == "pending_ack"
        reopened.append_deletion_tombstone(
            WORKSPACE, request_id="fixture-delete-local", reference_id="fixture-local-copy",
            version=2, state="acknowledged", evidence="device_ack",
            recorded_at="2026-07-31T04:01:00Z", previous_version=1,
        )
        assert reopened.list_completed_deletion_tombstones(
            WORKSPACE, "fixture-delete-local"
        ) == ["fixture-local-copy"]
    raw = (root / "metadata.db").read_bytes()
    assert b"fixture-delete-local" not in raw
    assert b"fixture-local-copy" not in raw
    assert b"device_ack" not in raw
