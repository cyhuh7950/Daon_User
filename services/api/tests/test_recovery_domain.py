from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from daon_user_api.recovery import (
    BackupObjectInput,
    RecoveryContext,
    RecoveryError,
    ReferenceRecoveryRepository,
    ReferenceRestorePort,
    RecoveryService,
    RestoreDestination,
)


NOW = datetime(2026, 7, 31, 5, 0, tzinfo=UTC)


class RecoveryDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = NOW
        self.repository = ReferenceRecoveryRepository()
        self.restore_port = ReferenceRestorePort()
        self.service = RecoveryService(
            self.repository, self.restore_port, clock=lambda: self.now,
            fixture_prefix="fixture-", rpo=timedelta(minutes=15),
        )
        self.context = RecoveryContext(
            "tenant-recovery-a", "workspace-recovery-a", "actor-recovery-a",
            "trace-recovery-a", "policy-recovery-v1", organization_admin=True,
        )
        self.foreign = RecoveryContext(
            "tenant-recovery-b", "workspace-recovery-b", "actor-recovery-b",
            "trace-recovery-b", "policy-recovery-v1", organization_admin=True,
        )
        self.objects = (
            BackupObjectInput("fixture-object-ready", "a" * 64, 12),
            BackupObjectInput("fixture-object-purged", "b" * 64, 13),
            BackupObjectInput("fixture-object-held", "c" * 64, 14),
        )

    def _backup(self):  # type: ignore[no-untyped-def]
        return self.service.create_backup(
            self.context, trigger="manual", schema_revision="0006",
            retention_watermark="retention-v1", objects=self.objects,
            idempotency_key="backup-create-a",
        )

    def _destination(self) -> RestoreDestination:
        return RestoreDestination(
            "fixture-tenant-target", "fixture-workspace-target",
            "fixture-database-target", "fixture-bucket-target",
        )

    def test_backup_is_verified_idempotent_scoped_and_rpo_due(self) -> None:
        backup = self._backup()
        replay = self._backup()
        self.assertEqual((backup.backup_id, backup.state), (replay.backup_id, "ready"))
        self.assertEqual(backup.transitions, ("queued", "capturing", "verifying", "ready"))
        self.assertFalse(self.service.backup_due(self.context))
        self.now += timedelta(minutes=16)
        self.assertTrue(self.service.backup_due(self.context))
        with self.assertRaisesRegex(RecoveryError, "BACKUP_UNAVAILABLE"):
            self.service.get_backup(self.foreign, backup.backup_id)

    def test_restore_requires_preview_fresh_step_up_and_current_retention(self) -> None:
        backup = self._backup()
        self.service.set_current_retention(
            self.context, purged={"fixture-object-purged"},
            held={"fixture-object-held"}, tombstoned=set(),
        )
        request = self.service.create_restore_preview(
            self.context, backup.backup_id, destination=self._destination(),
            idempotency_key="preview-a", step_up_verified=True,
        )
        self.assertEqual(request.state, "preview_ready")
        self.assertEqual(request.preview.included_object_ids, ("fixture-object-ready",))
        self.assertEqual(set(request.preview.excluded_object_ids), {
            "fixture-object-held", "fixture-object-purged",
        })
        with self.assertRaisesRegex(RecoveryError, "STEP_UP_REQUIRED"):
            self.service.execute_restore(
                self.context, request.request_id, expected_version=request.version,
                preview_version=request.preview.version, idempotency_key="execute-no-stepup",
                step_up_verified=False,
            )
        completed = self.service.execute_restore(
            self.context, request.request_id, expected_version=request.version,
            preview_version=request.preview.version, idempotency_key="execute-a",
            step_up_verified=True,
        )
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.transitions[-4:], ("authorized", "restoring", "verifying", "completed"))
        self.assertEqual(self.restore_port.restored_object_ids, ["fixture-object-ready"])
        self.assertEqual(self.restore_port.original_mutations, 0)

    def test_restore_fixture_guard_stale_duplicate_and_purge_recheck_fail_closed(self) -> None:
        backup = self._backup()
        unsafe = RestoreDestination(
            "tenant-production", "fixture-workspace-target",
            "fixture-database-target", "fixture-bucket-target",
        )
        with self.assertRaisesRegex(RecoveryError, "FIXTURE_ONLY_RESTORE_REQUIRED"):
            self.service.create_restore_preview(
                self.context, backup.backup_id, destination=unsafe,
                idempotency_key="preview-unsafe", step_up_verified=True,
            )
        request = self.service.create_restore_preview(
            self.context, backup.backup_id, destination=self._destination(),
            idempotency_key="preview-safe", step_up_verified=True,
        )
        self.service.set_current_retention(
            self.context, purged={"fixture-object-ready"}, held=set(), tombstoned=set(),
        )
        completed = self.service.execute_restore(
            self.context, request.request_id, expected_version=request.version,
            preview_version=request.preview.version, idempotency_key="execute-retention-recheck",
            step_up_verified=True,
        )
        self.assertEqual(completed.state, "completed")
        self.assertEqual(self.restore_port.restored_object_ids, [
            "fixture-object-purged", "fixture-object-held",
        ])
        self.assertNotIn("fixture-object-ready", self.restore_port.restored_object_ids)
        with self.assertRaisesRegex(RecoveryError, "RESTORE_VERSION_CONFLICT"):
            self.service.cancel_restore(
                self.context, request.request_id, expected_version=request.version,
                idempotency_key="cancel-stale",
            )


if __name__ == "__main__":
    unittest.main()
