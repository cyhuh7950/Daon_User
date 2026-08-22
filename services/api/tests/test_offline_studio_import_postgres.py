from __future__ import annotations

import hashlib
import os
import secrets
import unittest
from datetime import UTC, datetime

from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.data_canon import canonical_json_bytes
from daon_user_api.object_queue import ObjectQueueCoordinator, PostgresObjectQueueStore
from daon_user_api.offline_studio_import import PostgresOfflineStudioImportService
from daon_user_api.sync import SyncContext, SyncItemInput, SyncItemKind, TransferPayload
from daon_user_api.sync_postgres import ObjectQueueSyncTransferPort, PostgresSyncService
from test_sync_postgres import _Storage


def _signed(payload: dict[str, object]) -> dict[str, object]:
    return {**payload, "digest": hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}


def _bundle(source_version_id: str, source_item_id: str, source_digest: str) -> bytes:
    return canonical_json_bytes({
        "schema_version": 1,
        "local_workspace_id": "local-workspace-import",
        "knowledge_context_snapshot": _signed({
            "snapshot_id": "scope-import", "mode": "mixed",
            "items": [{"origin": "raw_source", "version_id": source_version_id,
                       "digest": source_digest}],
        }),
        "model_selection_snapshot": _signed({
            "provider_kind": "local_runtime", "deployment_id": "deployment-local",
            "artifact_digest": "c" * 64, "deployment_digest": "d" * 64,
        }),
        "generation_settings_snapshot": _signed({"snapshot_id": "settings-local"}),
        "run_snapshot": _signed({
            "run_id": "run-local", "workspace_id": "local-workspace-import", "egress": "none",
        }),
        "studio_output": _signed({"output_id": "output-local", "title": "Imported"}),
        "output_version": _signed({
            "output_version_id": "output-version-local", "previous_version_id": None,
            "sections": [{"title": "Summary", "body": "Imported", "unverified": True}],
        }),
        "source_dependencies": [{
            "item_id": source_item_id, "source_version_id": source_version_id,
            "digest": source_digest,
        }],
    })


@unittest.skipUnless(os.environ.get("DAON_TEST_POSTGRES_DSN"), "isolated PostgreSQL DSN required")
class OfflineStudioImportPostgresTests(unittest.TestCase):
    def test_source_dependency_then_output_creates_exact_cloud_canon_and_target(self) -> None:
        dsn = os.environ["DAON_TEST_POSTGRES_DSN"]
        cloud = PostgresCloudStore(dsn)
        queue = PostgresObjectQueueStore(dsn)
        suffix = secrets.token_hex(6)
        context = SyncContext(
            f"tenant-import-{suffix}", f"workspace-import-{suffix}",
            f"actor-import-{suffix}", f"trace-import-{suffix}", "policy-import-v1",
        )
        cloud.seed_scope(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "sync.write"
        ))
        coordinator = ObjectQueueCoordinator(queue, _Storage(), id_factory=lambda: secrets.token_hex(16))
        importer = PostgresOfflineStudioImportService(
            cloud, coordinator, clock=lambda: datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
        )
        transfer = ObjectQueueSyncTransferPort(coordinator, output_importer=importer)
        service = PostgresSyncService(
            cloud, transfer, clock=lambda: datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
        )
        source_content = b"source import"
        source_digest = hashlib.sha256(source_content).hexdigest()
        source = SyncItemInput(
            "source-item-import", "source-version-import", "source-object-import",
            source_digest, len(source_content), "text/plain", None, None,
        )
        output_content = _bundle(source.source_version_id or "", source.item_id, source_digest)
        output = SyncItemInput(
            "output-item-import", None, "output-object-import",
            hashlib.sha256(output_content).hexdigest(), len(output_content),
            "application/vnd.daon.offline-studio-output+json", None, None,
            item_kind=SyncItemKind.OUTPUT_VERSION,
            output_version_id="output-version-local",
            dependency_item_ids=(source.item_id,),
        )
        operation = service.create_operation(
            context, target_area="cloud_sync", items=(source, output),
            idempotency_key="create-import-operation", if_match="*",
        )
        approved = service.approve(
            context, operation_id=operation.operation_id,
            approved_item_ids=(source.item_id, output.item_id),
            step_up_authorization_id="step-import", expected_version=1,
            idempotency_key="approve-import-operation", approval_verified=True,
        )
        service.transfer_batch(
            context, operation_id=operation.operation_id, expected_version=approved.version,
            idempotency_key="transfer-import-source", cursor=None,
            payloads=(TransferPayload(source.item_id, source_content, None, None),),
        )
        current = service.get_operation(context, operation.operation_id)
        service.transfer_batch(
            context, operation_id=operation.operation_id, expected_version=current.version,
            idempotency_key="transfer-import-output", cursor="1",
            payloads=(TransferPayload(output.item_id, output_content, None, None),),
        )
        final = service.get_operation(context, operation.operation_id)
        output_target = next(item for item in final.target_versions if item.item_id == output.item_id)
        with cloud._transaction(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "sync.read"
        )) as connection:
            counts = connection.execute(
                "SELECT (SELECT count(*) FROM generation_settings_snapshots WHERE tenant_id=%s AND workspace_id=%s AND record_id LIKE 'settings-%%'),"
                "(SELECT count(*) FROM generation_requests WHERE tenant_id=%s AND workspace_id=%s AND record_id LIKE 'generation-%%'),"
                "(SELECT count(*) FROM studio_outputs WHERE tenant_id=%s AND workspace_id=%s AND record_id LIKE 'output-%%'),"
                "(SELECT count(*) FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s),"
                "(SELECT count(*) FROM sync_target_versions WHERE tenant_id=%s AND workspace_id=%s AND target_output_version_id=%s)",
                (context.tenant_id, context.workspace_id) * 3
                + (context.tenant_id, context.workspace_id, output_target.target_version_id) * 2,
            ).fetchone()
        self.assertEqual(tuple(counts or ()), (1, 1, 1, 1, 1))
        queue.close()
        cloud.close()


if __name__ == "__main__":
    unittest.main()
