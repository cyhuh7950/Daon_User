from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime

from daon_user_api.sync import (
    ConflictResolutionChoice,
    ReferenceSyncRepository,
    ReferenceTransferPort,
    SyncContext,
    SyncError,
    SyncItemInput,
    SyncItemKind,
    SyncService,
    TransferPayload,
)


DIGEST_A = hashlib.sha256(b"local-a").hexdigest()
DIGEST_B = hashlib.sha256(b"local-b").hexdigest()


class SyncDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = SyncContext(
            "tenant-sync-a", "workspace-sync-a", "actor-sync-a",
            "trace-sync-a", "policy-sync-v1",
        )
        self.foreign = SyncContext(
            "tenant-sync-b", "workspace-sync-b", "actor-sync-b",
            "trace-sync-b", "policy-sync-v1",
        )
        self.transfer = ReferenceTransferPort()
        self.service = SyncService(
            ReferenceSyncRepository(), self.transfer,
            clock=lambda: datetime(2026, 7, 30, 13, 0, tzinfo=UTC),
        )
        self.items = (
            SyncItemInput("item-a", "source-version-a", "local-object-a", DIGEST_A, 7, "text/plain", None, None),
            SyncItemInput("item-b", "source-version-b", "local-object-b", DIGEST_B, 7, "text/plain", "cloud-v1", DIGEST_B),
        )

    def _create(self):  # type: ignore[no-untyped-def]
        return self.service.create_operation(
            self.context, target_area="cloud_sync", items=self.items,
            idempotency_key="create-sync-a", if_match="*",
        )

    def _approve(self, operation_id: str):  # type: ignore[no-untyped-def]
        return self.service.approve(
            self.context, operation_id=operation_id, approved_item_ids=("item-a", "item-b"),
            step_up_authorization_id="step-up-a", expected_version=1,
            idempotency_key="approve-sync-a", approval_verified=True,
        )

    def test_unapproved_transfer_scope_expansion_and_cross_scope_are_denied(self) -> None:
        operation = self._create()
        self.assertEqual((operation.state, operation.version), ("awaiting_approval", 1))
        self.assertEqual(operation.item_ids, ("item-a", "item-b"))
        self.assertEqual(
            tuple(item.operation_id for item in self.service.list_operations(self.context)),
            (operation.operation_id,),
        )
        self.assertEqual(self.service.list_operations(self.foreign), ())
        with self.assertRaisesRegex(SyncError, "STEP_UP_REQUIRED"):
            self.service.approve(
                self.context, operation_id=operation.operation_id,
                approved_item_ids=("item-a",), step_up_authorization_id="step-up-invalid",
                expected_version=1, idempotency_key="approve-invalid",
                approval_verified=False,
            )
        with self.assertRaisesRegex(SyncError, "SYNC_APPROVAL_REQUIRED"):
            self.service.transfer_batch(
                self.context, operation_id=operation.operation_id, expected_version=1,
                idempotency_key="batch-before-approval", cursor=None,
                payloads=(TransferPayload("item-a", b"local-a", None, None),),
            )
        self.assertEqual(self.transfer.transmission_count, 0)
        self._approve(operation.operation_id)
        with self.assertRaisesRegex(SyncError, "SYNC_SCOPE_EXPANSION_DENIED"):
            self.service.transfer_batch(
                self.context, operation_id=operation.operation_id, expected_version=2,
                idempotency_key="batch-scope-expansion", cursor=None,
                payloads=(TransferPayload("item-not-approved", b"x", None, None),),
            )
        with self.assertRaisesRegex(SyncError, "SYNC_OPERATION_UNAVAILABLE"):
            self.service.get_operation(self.foreign, operation.operation_id)
        self.assertEqual(self.transfer.transmission_count, 0)

    def test_batch_is_idempotent_resumable_and_lost_update_safe(self) -> None:
        operation = self._create()
        approved = self._approve(operation.operation_id)
        first = self.service.transfer_batch(
            self.context, operation_id=operation.operation_id, expected_version=approved.version,
            idempotency_key="batch-sync-a", cursor=None,
            payloads=(TransferPayload("item-a", b"local-a", None, None),),
        )
        replay = self.service.transfer_batch(
            self.context, operation_id=operation.operation_id, expected_version=approved.version,
            idempotency_key="batch-sync-a", cursor=None,
            payloads=(TransferPayload("item-a", b"local-a", None, None),),
        )
        self.assertEqual(first.batch_id, replay.batch_id)
        self.assertEqual(self.transfer.transmission_count, 1)
        partial = self.service.get_operation(self.context, operation.operation_id)
        resumed = self.service.transfer_batch(
            self.context, operation_id=operation.operation_id,
            expected_version=partial.version, idempotency_key="batch-resume",
            cursor=first.next_cursor,
            payloads=(TransferPayload("item-b", b"local-b", "cloud-v1", DIGEST_B),),
        )
        self.assertEqual(resumed.transferred_item_ids, ("item-b",))
        self.assertEqual(self.transfer.transmission_count, 2)
        with self.assertRaisesRegex(SyncError, "SYNC_VERSION_CONFLICT"):
            self.service.transfer_batch(
                self.context, operation_id=operation.operation_id, expected_version=approved.version,
                idempotency_key="batch-stale", cursor=first.next_cursor,
                payloads=(TransferPayload("item-b", b"local-b", "cloud-v1", DIGEST_B),),
            )
        current = self.service.get_operation(self.context, operation.operation_id)
        self.assertEqual(current.source_mutations, 0)
        self.assertEqual(current.reindex_state, "reindex_requested")

    def test_conflict_requires_explicit_resolution_and_never_overwrites(self) -> None:
        operation = self._create()
        approved = self._approve(operation.operation_id)
        batch = self.service.transfer_batch(
            self.context, operation_id=operation.operation_id, expected_version=approved.version,
            idempotency_key="batch-conflict", cursor=None,
            payloads=(TransferPayload("item-b", b"local-b", "cloud-v2", DIGEST_A),),
        )
        self.assertEqual(batch.state, "conflict")
        self.assertEqual(self.transfer.transmission_count, 0)
        current = self.service.get_operation(self.context, operation.operation_id)
        conflict = current.conflicts[0]
        self.assertEqual(conflict.state, "unresolved")
        resolved = self.service.resolve_conflict(
            self.context, operation_id=operation.operation_id,
            conflict_id=conflict.conflict_id, expected_version=current.version,
            idempotency_key="resolve-conflict", choice=ConflictResolutionChoice.KEEP_BOTH,
            content=b"local-b",
        )
        self.assertEqual(resolved.choice, ConflictResolutionChoice.KEEP_BOTH)
        self.assertEqual(self.transfer.transmission_count, 1)
        final = self.service.get_operation(self.context, operation.operation_id)
        self.assertEqual(final.source_mutations, 0)
        self.assertEqual(final.overwrite_count, 0)

    def test_each_resolution_choice_has_explicit_non_overwrite_semantics(self) -> None:
        for index, choice in enumerate(ConflictResolutionChoice):
            with self.subTest(choice=choice.value):
                transfer = ReferenceTransferPort()
                service = SyncService(
                    ReferenceSyncRepository(), transfer,
                    clock=lambda: datetime(2026, 7, 30, 13, 0, tzinfo=UTC),
                )
                operation = service.create_operation(
                    self.context, target_area="cloud_sync", items=(self.items[1],),
                    idempotency_key=f"create-resolution-{index}", if_match="*",
                )
                approved = service.approve(
                    self.context, operation_id=operation.operation_id,
                    approved_item_ids=("item-b",),
                    step_up_authorization_id=f"step-resolution-{index}",
                    expected_version=1, idempotency_key=f"approve-resolution-{index}",
                    approval_verified=True,
                )
                batch = service.transfer_batch(
                    self.context, operation_id=operation.operation_id,
                    expected_version=approved.version,
                    idempotency_key=f"batch-resolution-{index}", cursor=None,
                    payloads=(TransferPayload("item-b", b"local-b", "cloud-v2", DIGEST_A),),
                )
                current = service.get_operation(self.context, operation.operation_id)
                resolution = service.resolve_conflict(
                    self.context, operation_id=operation.operation_id,
                    conflict_id=batch.conflict_ids[0], expected_version=current.version,
                    idempotency_key=f"resolve-resolution-{index}", choice=choice,
                    content=None if choice is ConflictResolutionChoice.KEEP_CLOUD else b"local-b",
                )
                self.assertEqual(resolution.choice, choice)
                self.assertEqual(
                    transfer.transmission_count,
                    0 if choice is ConflictResolutionChoice.KEEP_CLOUD else 1,
                )
                final = service.get_operation(self.context, operation.operation_id)
                self.assertEqual((final.source_mutations, final.overwrite_count), (0, 0))

    def test_output_item_requires_exact_version_and_sorted_source_dependencies(self) -> None:
        output = SyncItemInput(
            "item-output-1", None, "object-output-1", "a" * 64, 12,
            "application/vnd.daon.offline-studio-output+json", None, None,
            item_kind=SyncItemKind.OUTPUT_VERSION,
            output_version_id="local-output-version-1",
            dependency_item_ids=("item-source-1",),
        )
        self.assertEqual(output.version_id, "local-output-version-1")
        legacy = SyncItemInput(
            "item-source-1", "source-version-1", "object-source-1", "b" * 64,
            12, "application/pdf", None, None,
        )
        self.assertIs(legacy.item_kind, SyncItemKind.SOURCE_VERSION)

    def test_output_dependencies_must_be_sorted_unique_and_approved(self) -> None:
        with self.assertRaisesRegex(SyncError, "SYNC_ITEM_INVALID"):
            SyncItemInput(
                "item-output-1", None, "object-output-1", "a" * 64, 12,
                "application/vnd.daon.offline-studio-output+json", None, None,
                item_kind=SyncItemKind.OUTPUT_VERSION,
                output_version_id="local-output-version-1",
                dependency_item_ids=("item-source-2", "item-source-1"),
            )

    def test_output_transfer_requires_approved_completed_source_dependency(self) -> None:
        source = SyncItemInput(
            "item-source-1", "source-version-1", "object-source-1",
            hashlib.sha256(b"source").hexdigest(), 6, "text/plain", None, None,
        )
        output = SyncItemInput(
            "item-output-1", None, "object-output-1",
            hashlib.sha256(b"output").hexdigest(), 6,
            "application/vnd.daon.offline-studio-output+json", None, None,
            item_kind=SyncItemKind.OUTPUT_VERSION,
            output_version_id="local-output-version-1",
            dependency_item_ids=(source.item_id,),
        )
        operation = self.service.create_operation(
            self.context, target_area="cloud_sync", items=(source, output),
            idempotency_key="create-output-dependency", if_match="*",
        )
        with self.assertRaisesRegex(SyncError, "SYNC_DEPENDENCY_REQUIRED"):
            self.service.approve(
                self.context, operation_id=operation.operation_id,
                approved_item_ids=(output.item_id,), step_up_authorization_id="step-output",
                expected_version=1, idempotency_key="approve-output-only",
                approval_verified=True,
            )
        approved = self.service.approve(
            self.context, operation_id=operation.operation_id,
            approved_item_ids=(source.item_id, output.item_id),
            step_up_authorization_id="step-output", expected_version=1,
            idempotency_key="approve-output-with-source", approval_verified=True,
        )
        with self.assertRaisesRegex(SyncError, "SYNC_DEPENDENCY_REQUIRED"):
            self.service.transfer_batch(
                self.context, operation_id=operation.operation_id,
                expected_version=approved.version, idempotency_key="output-before-source",
                cursor=None, payloads=(TransferPayload(output.item_id, b"output", None, None),),
            )
        source_batch = self.service.transfer_batch(
            self.context, operation_id=operation.operation_id,
            expected_version=approved.version, idempotency_key="source-first",
            cursor=None, payloads=(TransferPayload(source.item_id, b"source", None, None),),
        )
        current = self.service.get_operation(self.context, operation.operation_id)
        output_batch = self.service.transfer_batch(
            self.context, operation_id=operation.operation_id,
            expected_version=current.version, idempotency_key="output-after-source",
            cursor=source_batch.next_cursor,
            payloads=(TransferPayload(output.item_id, b"output", None, None),),
        )
        self.assertEqual(output_batch.transferred_item_ids, (output.item_id,))


if __name__ == "__main__":
    unittest.main()
