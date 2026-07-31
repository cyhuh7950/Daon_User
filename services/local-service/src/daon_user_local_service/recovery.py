from __future__ import annotations

import re
import secrets
from dataclasses import asdict
from datetime import UTC, datetime

from .local_storage import LocalEncryptedStore, LocalRecoveryJobState, LocalStorageError


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class LocalRecoveryService:
    def __init__(self, storage: LocalEncryptedStore, *, fixture_prefix: str = "fixture-") -> None:
        self._storage = storage
        self._fixture_prefix = fixture_prefix

    @staticmethod
    def view(state: LocalRecoveryJobState) -> dict[str, object]:
        result = asdict(state)
        result.pop("snapshot_checksum", None)
        result.pop("actual_checksum", None)
        result["integrity"] = (
            "verified" if state.state == "verified"
            else "manual_required" if state.state == "manual_recovery_required"
            else "pending"
        )
        return result

    def _append(
        self,
        workspace_id: str,
        job_id: str,
        version: int,
        state: str,
        target_id: str,
        snapshot_checksum: str,
        actual_checksum: str,
        journal_present: bool,
    ) -> None:
        self._storage.append_recovery_job_state(
            workspace_id, job_id=job_id, version=version, state=state,
            target_id=target_id, snapshot_checksum=snapshot_checksum,
            actual_checksum=actual_checksum, journal_present=journal_present,
            recorded_at=_now(), previous_version=None if version == 1 else version - 1,
        )

    def scan(
        self,
        workspace_id: str,
        *,
        target_id: str,
        snapshot_checksum: str,
        metadata_checksum: str,
        actual_checksum: str,
        journal_present: bool,
    ) -> LocalRecoveryJobState:
        if (
            not _ID.fullmatch(target_id)
            or not target_id.startswith(self._fixture_prefix)
            or not all(_DIGEST.fullmatch(value) for value in (
                snapshot_checksum, metadata_checksum, actual_checksum,
            ))
        ):
            raise LocalStorageError("LOCAL_RECOVERY_INPUT_INVALID")
        job_id = f"fixture-recovery-{secrets.token_hex(12)}"
        for version, state in enumerate(("detected", "quarantined", "scanning"), start=1):
            self._append(
                workspace_id, job_id, version, state, target_id,
                snapshot_checksum, actual_checksum, journal_present,
            )
        final = (
            "repairable"
            if journal_present and metadata_checksum == snapshot_checksum
            else "manual_recovery_required"
        )
        self._append(
            workspace_id, job_id, 4, final, target_id,
            snapshot_checksum, actual_checksum, journal_present,
        )
        return self._storage.get_recovery_job_state(workspace_id, job_id)

    def get(self, workspace_id: str, job_id: str) -> LocalRecoveryJobState:
        return self._storage.get_recovery_job_state(workspace_id, job_id)

    def find(self, job_id: str) -> LocalRecoveryJobState:
        return self._storage.find_recovery_job_state(job_id)[1]

    def repair(
        self, workspace_id: str, job_id: str, *, expected_version: int
    ) -> LocalRecoveryJobState:
        current = self.get(workspace_id, job_id)
        if current.version != expected_version or current.state != "repairable":
            raise LocalStorageError("LOCAL_RECOVERY_VERSION_CONFLICT")
        if not current.target_id.startswith(self._fixture_prefix) or not current.journal_present:
            self._append(
                workspace_id, job_id, current.version + 1, "manual_recovery_required",
                current.target_id, current.snapshot_checksum, current.actual_checksum,
                current.journal_present,
            )
            return self.get(workspace_id, job_id)
        self._append(
            workspace_id, job_id, current.version + 1, "repairing",
            current.target_id, current.snapshot_checksum, current.actual_checksum,
            current.journal_present,
        )
        self._append(
            workspace_id, job_id, current.version + 2, "verified",
            current.target_id, current.snapshot_checksum, current.snapshot_checksum,
            current.journal_present,
        )
        return self.get(workspace_id, job_id)
