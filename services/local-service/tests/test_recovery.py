from __future__ import annotations

from pathlib import Path

from daon_user_local_service.local_storage import LocalEncryptedStore
from daon_user_local_service.app import COMMAND_REGISTRY
from daon_user_local_service.recovery import LocalRecoveryService


WORKSPACE = "55555555-5555-4555-8555-555555555555"
MASTER_KEY = bytes(range(32))


def test_local_recovery_job_is_encrypted_restartable_and_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "local-recovery"
    with LocalEncryptedStore.open(root, MASTER_KEY) as store:
        store.append_recovery_job_state(
            WORKSPACE, job_id="fixture-recovery-job", version=1,
            state="quarantined", target_id="fixture-damaged-object",
            snapshot_checksum="a" * 64, actual_checksum="b" * 64,
            journal_present=True, recorded_at="2026-07-31T05:00:00Z",
            previous_version=None,
        )
    with LocalEncryptedStore.open(root, MASTER_KEY) as reopened:
        job = reopened.get_recovery_job_state(WORKSPACE, "fixture-recovery-job")
        assert job.state == "quarantined"
        reopened.append_recovery_job_state(
            WORKSPACE, job_id="fixture-recovery-job", version=2,
            state="manual_recovery_required", target_id="fixture-damaged-object",
            snapshot_checksum="a" * 64, actual_checksum="b" * 64,
            journal_present=False, recorded_at="2026-07-31T05:01:00Z",
            previous_version=1,
        )
        assert reopened.get_recovery_job_state(
            WORKSPACE, "fixture-recovery-job"
        ).state == "manual_recovery_required"
    raw = (root / "metadata.db").read_bytes()
    for canary in (b"fixture-recovery-job", b"fixture-damaged-object", b"manual_recovery_required"):
        assert canary not in raw


def test_local_recovery_registers_exact_three_public_routes_and_repairs_fixture(tmp_path: Path) -> None:
    assert {
        contract.path for contract in COMMAND_REGISTRY.values()
        if contract.path.startswith("/local/v1/recovery")
    } == {
        "/local/v1/recovery/scans",
        "/local/v1/recovery/jobs/{id}",
        "/local/v1/recovery/jobs/{id}/repair",
    }
    root = tmp_path / "local-recovery-service"
    with LocalEncryptedStore.open(root, MASTER_KEY) as store:
        service = LocalRecoveryService(store)
        job = service.scan(
            WORKSPACE, target_id="fixture-damaged-object",
            snapshot_checksum="a" * 64, metadata_checksum="a" * 64,
            actual_checksum="b" * 64, journal_present=True,
        )
        assert job.state == "repairable"
        repaired = service.repair(WORKSPACE, job.job_id, expected_version=job.version)
        assert repaired.state == "verified"
    raw = (root / "metadata.db").read_bytes()
    assert b"fixture-damaged-object" not in raw


def test_local_recovery_missing_journal_requires_manual_recovery(tmp_path: Path) -> None:
    with LocalEncryptedStore.open(tmp_path / "manual-recovery", MASTER_KEY) as store:
        job = LocalRecoveryService(store).scan(
            WORKSPACE, target_id="fixture-damaged-object",
            snapshot_checksum="a" * 64, metadata_checksum="a" * 64,
            actual_checksum="b" * 64, journal_present=False,
        )
        assert job.state == "manual_recovery_required"
