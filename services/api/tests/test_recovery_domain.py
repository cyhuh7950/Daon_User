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
    def test_l2_parent_canon_id_256_preserved_without_expanding_selection_ids(self):
        from daon_user_api.recovery import BackupSelectionInput, _resolved_backup_metadata
        selected = BackupSelectionInput("notebook-a", ("version-a",))
        item = BackupObjectInput("object-a", "a" * 64, 7)
        receipt = _resolved_backup_metadata(self.context, selected, "schema-a", "watermark-a",
            (("version-a", "s" * 256, item, "b" * 64),))
        self.assertEqual(receipt.selection.source_version_ids, ("version-a",))
        for parent in ("s" * 257, "unsafe/path", None):
            with self.assertRaises(RecoveryError):
                _resolved_backup_metadata(self.context, selected, "schema-a", "watermark-a",
                    (("version-a", parent, item, "b" * 64),))
        with self.assertRaises(RecoveryError): BackupSelectionInput("notebook-a", ("v" * 256,))

    def test_l1_selection_is_safe_bounded_unique_and_order_independent(self) -> None:
        from daon_user_api import recovery
        selection_type = getattr(recovery, "BackupSelectionInput", None)
        self.assertTrue(callable(selection_type), "실제 L1 선택 검증 타입 필요")
        first = selection_type("notebook-a", ("version-b", "version-a"))
        self.assertEqual(first.source_version_ids, ("version-a", "version-b"))
        for ids in ((), ("version-a",) * 2, tuple(f"v-{i}" for i in range(101)), ("unsafe/path",), (1,)):
            with self.subTest(ids_count=len(ids)), self.assertRaises(RecoveryError) as caught:
                selection_type("notebook-a", ids)
            self.assertEqual(caught.exception.status, 400)

    def test_l1_resolved_metadata_limit_fingerprint_and_legacy_comparison(self) -> None:
        from daon_user_api import recovery
        resolve = getattr(recovery, "_resolved_backup_metadata", None)
        self.assertTrue(callable(resolve), "정본 metadata 검증/지문 기능 필요")
        selection = recovery.BackupSelectionInput("notebook-a", ("version-b", "version-a"))
        bindings = (("version-a", "source-a", BackupObjectInput("object-a", "a" * 64, 512 * 1024 * 1024), "c" * 64),
                    ("version-b", "source-b", BackupObjectInput("object-b", "b" * 64, 0), "d" * 64))
        first = resolve(self.context, selection, "test-revision", "e" * 64, bindings)
        second = resolve(self.context, selection, "test-revision", "e" * 64, tuple(reversed(bindings)))
        self.assertEqual(first.fingerprint, second.fingerprint)
        first.require_legacy_match(schema_revision="test-revision", retention_watermark="e" * 64, objects=tuple(reversed(first.objects)))
        for change in ({"schema_revision": "forged"}, {"retention_watermark": "forged"},
                       {"objects": (BackupObjectInput("object-a", "f" * 64, 1),)}):
            kwargs = dict(schema_revision=first.schema_revision, retention_watermark=first.retention_watermark, objects=first.objects)
            with self.assertRaises(RecoveryError): first.require_legacy_match(**{**kwargs, **change})
        oversize = (bindings[0], ("version-b", "source-b", BackupObjectInput("object-b", "b" * 64, 1), "d" * 64))
        with self.assertRaises(RecoveryError) as caught:
            resolve(self.context, selection, "test-revision", "e" * 64, oversize)
        self.assertEqual(caught.exception.status, 413)
        changed = resolve(self.context, selection, "test-revision", "f" * 64, bindings)
        with self.assertRaises(RecoveryError) as caught: changed.require_replay(first.fingerprint)
        self.assertEqual((caught.exception.code, caught.exception.status), ("IDEMPOTENCY_KEY_REUSED", 409))


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
