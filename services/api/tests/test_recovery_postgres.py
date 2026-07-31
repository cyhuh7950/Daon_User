from __future__ import annotations

import hashlib
import os
import secrets
import unittest
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.object_queue import (
    DurableObjectWorker,
    MinioObjectStorageAdapter,
    ObjectQueueCoordinator,
    PostgresObjectQueueStore,
)
from daon_user_api.recovery import BackupObjectInput, RecoveryContext, RestoreDestination
from daon_user_api.runtime import RuntimeSettings, build_dependencies
from daon_user_api.recovery_postgres import (
    MinioRecoveryStorageAdapter,
    PostgresRecoveryService,
)


def _environment_or_file(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    file_name = os.environ.get(f"{name}_FILE")
    if not file_name:
        return None
    return Path(file_name).read_text(encoding="utf-8").strip()


def _environment_or_bytes(name: str) -> bytes | None:
    value = os.environ.get(name)
    if value:
        return value.encode("utf-8")
    file_name = os.environ.get(f"{name}_FILE")
    if not file_name:
        return None
    return Path(file_name).read_bytes()


DSN = _environment_or_file("DAON_RECOVERY_INTEGRATION_DSN")
ENDPOINT = os.environ.get("DAON_RECOVERY_INTEGRATION_OBJECT_ENDPOINT")
BUCKET = os.environ.get("DAON_RECOVERY_INTEGRATION_BUCKET")
ACCESS_KEY = _environment_or_file("DAON_RECOVERY_INTEGRATION_ACCESS_KEY")
SECRET_KEY = _environment_or_file("DAON_RECOVERY_INTEGRATION_SECRET_KEY")
MANIFEST_KEY = _environment_or_bytes("DAON_RECOVERY_INTEGRATION_MANIFEST_KEY")
CONFIGURED = all((DSN, ENDPOINT, BUCKET, ACCESS_KEY, SECRET_KEY, MANIFEST_KEY))


@unittest.skipUnless(CONFIGURED, "R1-M5-07 PostgreSQL/MinIO integration environment required")
class PostgresRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        assert DSN and ENDPOINT and BUCKET and ACCESS_KEY and SECRET_KEY and MANIFEST_KEY
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
        assert DSN and MANIFEST_KEY
        return PostgresRecoveryService(
            DSN,
            self.storage,
            manifest_key=MANIFEST_KEY,
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
        with psycopg.connect(DSN) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                (restored.preview.destination.tenant_id,),
            )
            connection.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (restored.preview.destination.workspace_id,),
            )
            target = connection.execute(
                "SELECT digest_sha256,byte_size,status FROM object_records WHERE object_id=%s",
                (self.object_id,),
            ).fetchone()
        self.assertEqual(target, (self.digest, len(self.content), "completed"))

        wrong_workspace = RecoveryContext(
            self.tenant_id,
            f"wrong-{self.workspace_id}",
            self.actor_id,
            self.context.trace_id,
            self.context.policy_version,
            organization_admin=True,
        )
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.get_backup(wrong_workspace, backup.backup_id)
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.locate_backup_workspace(
                f"wrong-{self.tenant_id}", backup.backup_id
            )
        restarted.close()

    def test_missing_and_corrupt_source_objects_fail_closed(self) -> None:
        service = self._service()
        try:
            with self.assertRaisesRegex(Exception, "RESOURCE_UNAVAILABLE"):
                service.create_backup(
                    self.context,
                    trigger="manual",
                    schema_revision="0006",
                    retention_watermark="watermark-c02",
                    objects=(
                        BackupObjectInput(
                            secrets.token_hex(16), self.digest, len(self.content)
                        ),
                    ),
                    idempotency_key=f"missing-{self.object_id}",
                )
            with self.assertRaisesRegex(Exception, "RESOURCE_UNAVAILABLE"):
                service.create_backup(
                    self.context,
                    trigger="manual",
                    schema_revision="0006",
                    retention_watermark="watermark-c02",
                    objects=(
                        BackupObjectInput(
                            self.object_id, "0" * 64, len(self.content)
                        ),
                    ),
                    idempotency_key=f"corrupt-{self.object_id}",
                )
        finally:
            service.close()


class RecoveryRuntimeFailCloseTests(unittest.TestCase):
    def test_build_dependencies_never_installs_reference_recovery_fallback(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            dependencies = build_dependencies(RuntimeSettings.for_test(
                database_path=Path(directory) / "runtime.sqlite3",
                policy_version="policy-c02",
            ))
            try:
                self.assertIsNotNone(dependencies.recovery_service)
                self.assertNotEqual(
                    type(dependencies.recovery_service).__name__, "RecoveryService"
                )
                with self.assertRaisesRegex(Exception, "RESOURCE_UNAVAILABLE"):
                    dependencies.recovery_service.create_backup(  # type: ignore[union-attr]
                        RecoveryContext(
                            "fixture-tenant", "fixture-workspace", "fixture-actor",
                            "fixture-trace", "policy-c02", organization_admin=True,
                        ),
                        trigger="manual", schema_revision="0006",
                        retention_watermark="fixture-watermark",
                        objects=(BackupObjectInput("a" * 32, "b" * 64, 1),),
                        idempotency_key="fixture-idempotency",
                    )
            finally:
                dependencies.close()


if __name__ == "__main__":
    unittest.main()
