from __future__ import annotations

import hashlib
import os
import secrets
import unittest
from datetime import datetime, timezone

from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.object_queue import (
    DurableObjectWorker,
    MinioObjectStorageAdapter,
    ObjectQueueCoordinator,
    PostgresObjectQueueStore,
)
from daon_user_api.recovery import BackupObjectInput, RecoveryContext, RestoreDestination
from daon_user_api.recovery_postgres import (
    MinioRecoveryStorageAdapter,
    PostgresRecoveryService,
)


DSN = os.environ.get("DAON_RECOVERY_INTEGRATION_DSN")
ENDPOINT = os.environ.get("DAON_RECOVERY_INTEGRATION_OBJECT_ENDPOINT")
BUCKET = os.environ.get("DAON_RECOVERY_INTEGRATION_BUCKET")
ACCESS_KEY = os.environ.get("DAON_RECOVERY_INTEGRATION_ACCESS_KEY")
SECRET_KEY = os.environ.get("DAON_RECOVERY_INTEGRATION_SECRET_KEY")
CONFIGURED = all((DSN, ENDPOINT, BUCKET, ACCESS_KEY, SECRET_KEY))


@unittest.skipUnless(CONFIGURED, "R1-M5-07 PostgreSQL/MinIO integration environment required")
class PostgresRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        assert DSN and ENDPOINT and BUCKET and ACCESS_KEY and SECRET_KEY
        suffix = secrets.token_hex(6)
        self.tenant_id = f"fixture-tenant-{suffix}"
        self.workspace_id = f"fixture-workspace-{suffix}"
        self.actor_id = f"actor-{suffix}"
        self.object_id = secrets.token_hex(16)
        self.content = f"R1-M5-07-C02:{suffix}".encode()
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.context = RecoveryContext(
            self.tenant_id,
            self.workspace_id,
            self.actor_id,
            f"trace-{suffix}",
            "policy-c02",
            organization_admin=True,
        )
        self.cloud = PostgresCloudStore(DSN)
        self.queue = PostgresObjectQueueStore(DSN)
        self.object_storage = MinioObjectStorageAdapter(
            endpoint=ENDPOINT,
            bucket=BUCKET,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            secure=False,
        )
        scope = CloudAccessContext(
            self.tenant_id, self.workspace_id, self.actor_id, "object.write"
        )
        self.cloud.seed_scope(scope)
        coordinator = ObjectQueueCoordinator(
            self.queue, self.object_storage, id_factory=lambda: self.object_id
        )
        coordinator.submit(
            scope,
            area="source",
            content=self.content,
            content_type="application/octet-stream",
            idempotency_key=f"seed-{suffix}",
            trace_id=f"trace-seed-{suffix}",
        )
        worker = DurableObjectWorker(self.queue, self.object_storage, f"worker-{suffix}")
        completed = worker.run_once(scope, now=datetime.now(timezone.utc))
        self.assertIsNotNone(completed)
        self.assertEqual(completed.state, "completed")
        self.storage = MinioRecoveryStorageAdapter(self.object_storage)

    def tearDown(self) -> None:
        self.queue.close()
        self.cloud.close()

    def _service(self) -> PostgresRecoveryService:
        assert DSN
        return PostgresRecoveryService(
            DSN,
            self.storage,
            manifest_key=hashlib.sha256(b"r1-m5-07-c02-integration").digest(),
            clock=lambda: datetime.now(timezone.utc),
        )

    def test_backup_and_restore_survive_service_restart_and_touch_real_stores(self) -> None:
        first = self._service()
        backup = first.create_backup(
            self.context,
            trigger="manual",
            schema_revision="0006",
            retention_watermark="watermark-c02",
            objects=(BackupObjectInput(self.object_id, self.digest, len(self.content)),),
            idempotency_key=f"backup-{self.object_id}",
        )
        self.assertEqual(backup.state, "ready")
        first.close()

        restarted = self._service()
        persisted = restarted.get_backup(self.context, backup.backup_id)
        self.assertEqual(persisted.manifest_digest, backup.manifest_digest)
        preview = restarted.create_restore_preview(
            self.context,
            backup.backup_id,
            destination=RestoreDestination(
                f"fixture-target-tenant-{self.object_id[:8]}",
                f"fixture-target-workspace-{self.object_id[:8]}",
                f"fixture-target-database-{self.object_id[:8]}",
                f"fixture-target-bucket-{self.object_id[:8]}",
            ),
            idempotency_key=f"preview-{self.object_id}",
            step_up_verified=True,
        )
        restored = restarted.execute_restore(
            self.context,
            preview.request_id,
            expected_version=preview.version,
            preview_version=preview.preview.version,
            idempotency_key=f"execute-{self.object_id}",
            step_up_verified=True,
        )
        self.assertEqual(restored.state, "completed")
        self.assertEqual(
            self.storage.read_fixture(restored.preview.destination, self.object_id),
            self.content,
        )
        restarted.close()


if __name__ == "__main__":
    unittest.main()
