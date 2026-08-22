from __future__ import annotations

import hashlib
import os
import secrets
import unittest
from datetime import UTC, datetime

from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.object_queue import (
    ObjectQueueCoordinator, ObjectStorageError, PostgresObjectQueueStore,
    StagedObject, StoredObject,
)
from daon_user_api.sync import SyncContext, SyncItemInput, SyncItemKind, TargetVersion, TransferPayload
from daon_user_api.sync_postgres import ObjectQueueSyncTransferPort, PostgresSyncService


class _Storage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str]] = {}

    def health(self) -> bool:
        return True

    def put_staged(self, key: str, content: bytes, content_type: str,
                   digest: str) -> StagedObject:
        if hashlib.sha256(content).hexdigest() != digest:
            raise ObjectStorageError("OBJECT_CHECKSUM_MISMATCH")
        self.objects[key] = (content, content_type, digest)
        return StagedObject(key, digest, len(content), content_type, "etag-stage", "stage-v1")

    def promote(self, staged: StagedObject, final_key: str, *, expected_digest: str,
                expected_size: int, content_type: str) -> StoredObject:
        content, actual_type, digest = self.objects[staged.key]
        if digest != expected_digest or len(content) != expected_size or actual_type != content_type:
            raise ObjectStorageError("OBJECT_CHECKSUM_MISMATCH")
        self.objects[final_key] = (content, content_type, digest)
        return StoredObject(final_key, digest, len(content), content_type, "etag-final", "final-v1")

    def get(self, key: str) -> bytes:
        return self.objects[key][0]


class _NoSourceCoordinator:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("output import must not use source submission")


class _OutputImporter:
    def __init__(self) -> None:
        self.calls = 0

    def import_bundle(self, context, item, content, idempotency_key, *, relation):
        self.calls += 1
        return TargetVersion(
            "cloud-output-version", "cloud-output-object", item.item_id,
            item.digest_sha256, None, relation,
        )


class ObjectQueueSyncTransferPortUnitTests(unittest.TestCase):
    def test_output_item_is_dispatched_to_output_importer(self) -> None:
        content = b"{}"
        importer = _OutputImporter()
        port = ObjectQueueSyncTransferPort(_NoSourceCoordinator(), output_importer=importer)
        item = SyncItemInput(
            "output-item", None, "local-output-object", hashlib.sha256(content).hexdigest(),
            len(content), "application/vnd.daon.offline-studio-output+json", None, None,
            item_kind=SyncItemKind.OUTPUT_VERSION, output_version_id="local-output-version",
            dependency_item_ids=("source-item",),
        )
        target = port.transmit(
            SyncContext("tenant", "workspace", "actor", "trace", "policy"),
            item, content, "output-import-idem", relation="copy",
        )
        self.assertEqual(target.target_version_id, "cloud-output-version")
        self.assertEqual(importer.calls, 1)


@unittest.skipUnless(os.environ.get("DAON_TEST_POSTGRES_DSN"), "isolated PostgreSQL DSN required")
class PostgresSyncIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        dsn = os.environ["DAON_TEST_POSTGRES_DSN"]
        self.cloud = PostgresCloudStore(dsn)
        self.queue = PostgresObjectQueueStore(dsn)
        suffix = secrets.token_hex(6)
        self.context = SyncContext(
            f"tenant-sync-{suffix}", f"workspace-sync-{suffix}",
            f"actor-sync-{suffix}", f"trace-sync-{suffix}", "policy-sync-v1",
        )
        self.cloud.seed_scope(CloudAccessContext(
            self.context.tenant_id, self.context.workspace_id,
            self.context.actor_id, "sync.write",
        ))
        coordinator = ObjectQueueCoordinator(
            self.queue, _Storage(), id_factory=lambda: secrets.token_hex(16)
        )
        self.transfer = ObjectQueueSyncTransferPort(coordinator)
        self.service = PostgresSyncService(
            self.cloud, self.transfer,
            clock=lambda: datetime(2026, 7, 30, 15, 0, tzinfo=UTC),
        )

    def tearDown(self) -> None:
        self.queue.close()
        self.cloud.close()

    def test_persists_approval_manifest_batch_target_and_restart_projection(self) -> None:
        content = b"postgres-sync"
        digest = hashlib.sha256(content).hexdigest()
        item = SyncItemInput(
            "item-postgres", "source-version-postgres", "local-object-postgres",
            digest, len(content), "text/plain", None, None,
        )
        operation = self.service.create_operation(
            self.context, target_area="cloud_sync", items=(item,),
            idempotency_key="create-postgres-sync", if_match="*",
        )
        approved = self.service.approve(
            self.context, operation_id=operation.operation_id,
            approved_item_ids=(item.item_id,), step_up_authorization_id="step-postgres-sync",
            expected_version=operation.version, idempotency_key="approve-postgres-sync",
            approval_verified=True,
        )
        batch = self.service.transfer_batch(
            self.context, operation_id=operation.operation_id,
            expected_version=approved.version, idempotency_key="batch-postgres-sync",
            cursor=None, payloads=(TransferPayload(item.item_id, content, None, None),),
        )
        replay = self.service.transfer_batch(
            self.context, operation_id=operation.operation_id,
            expected_version=approved.version, idempotency_key="batch-postgres-sync",
            cursor=None, payloads=(TransferPayload(item.item_id, content, None, None),),
        )
        self.assertEqual((batch.batch_id, replay.batch_id), (batch.batch_id, batch.batch_id))
        restarted = PostgresSyncService(
            self.cloud, self.transfer,
            clock=lambda: datetime(2026, 7, 30, 15, 1, tzinfo=UTC),
        )
        view = restarted.get_operation(self.context, operation.operation_id)
        self.assertEqual(view.state, "reindex_requested")
        listed = restarted.list_operations(self.context)
        self.assertEqual(tuple(item.operation_id for item in listed), (operation.operation_id,))
        self.assertEqual(listed[0].item_ids, (item.item_id,))
        self.assertEqual((view.source_mutations, view.overwrite_count), (0, 0))
        with self.cloud._transaction(CloudAccessContext(
            self.context.tenant_id, self.context.workspace_id,
            self.context.actor_id, "sync.read",
        )) as connection:
            counts = connection.execute(
                "SELECT (SELECT count(*) FROM sync_approval_snapshots WHERE operation_id=%s),"
                "(SELECT count(*) FROM sync_manifest_items WHERE operation_id=%s),"
                "(SELECT count(*) FROM sync_transfer_batches WHERE operation_id=%s),"
                "(SELECT count(*) FROM sync_target_versions WHERE operation_id=%s),"
                "(SELECT count(*) FROM sync_reindex_requests WHERE operation_id=%s)",
                (operation.operation_id,) * 5,
            ).fetchone()
        self.assertEqual(tuple(counts or ()), (1, 1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
