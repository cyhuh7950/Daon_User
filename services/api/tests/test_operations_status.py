from __future__ import annotations

import os

import psycopg
import pytest

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.operations_status import (
    OperationsCounts,
    OperationsStatusContext,
    OperationsStatusService,
)
from daon_user_api.operations_status_postgres import PostgresOperationsStatusRepository


class Repository:
    def read_counts(self, context: OperationsStatusContext) -> OperationsCounts:
        assert context.workspace_id == "workspace-operations"
        return OperationsCounts(sync_pending=2, queue_pending=3, queue_failed=1)


def test_operations_status_projects_five_safe_components_and_actions() -> None:
    view = OperationsStatusService(Repository()).read(
        OperationsStatusContext("tenant-1", "workspace-operations", "actor-1"),
        active_providers=1, selected_deployments=1, api_ready=True,
        database_ready=True, object_storage_ready=True,
        checked_at="2026-08-15T00:00:00Z",
    )
    assert view.overall_status == "warning"
    assert [item.component_id for item in view.components] == ["provider", "api", "storage", "sync", "queue"]
    assert view.components[0].safe_code == "PROVIDER_READY"
    assert view.components[3].pending_count == 2
    assert view.components[3].recovery_action == "open_sync_settings"
    assert view.components[4].pending_count == 3
    assert view.components[4].safe_code == "QUEUE_ATTENTION_REQUIRED"
    assert view.components[4].recovery_action == "refresh_status"


def test_operations_status_fails_closed_without_provider_or_storage() -> None:
    view = OperationsStatusService(Repository()).read(
        OperationsStatusContext("tenant-1", "workspace-operations", "actor-1"),
        active_providers=0, selected_deployments=0, api_ready=True,
        database_ready=False, object_storage_ready=False,
        checked_at="2026-08-15T00:00:00Z",
    )
    assert view.overall_status == "error"
    assert view.components[0].safe_code == "PROVIDER_CONFIGURATION_REQUIRED"
    assert view.components[0].recovery_action == "open_llm_settings"
    assert view.components[2].safe_code == "STORAGE_UNAVAILABLE"
    assert all("http" not in item.safe_code.lower() for item in view.components)


@pytest.mark.skipif(not os.getenv("DAON_OPERATIONS_TEST_DSN"), reason="actual PostgreSQL DSN unavailable")
def test_postgres_operations_counts_are_workspace_scoped() -> None:
    dsn = os.environ["DAON_OPERATIONS_TEST_DSN"]
    digest = "a" * 64
    with psycopg.connect(dsn) as connection:
        connection.execute("INSERT INTO tenants (tenant_id,display_name) VALUES ('tenant-operations','Operations'),('tenant-operations-other','Other')")
        connection.execute("INSERT INTO workspaces (tenant_id,workspace_id,display_name) VALUES ('tenant-operations','workspace-operations','Operations'),('tenant-operations-other','workspace-operations-other','Other')")
        for tenant_id, workspace_id, operation_id in (
            ("tenant-operations", "workspace-operations", "operation-1"),
            ("tenant-operations-other", "workspace-operations-other", "operation-other"),
        ):
            connection.execute(
                "INSERT INTO sync_operations (tenant_id,workspace_id,operation_id,actor_id,target_area,state,version,preview_digest,policy_version,idempotency_key,request_fingerprint,trace_id,state_document,created_at,updated_at) VALUES (%s,%s,%s,'actor-1','cloud_sync','awaiting_approval',1,%s,'policy-v1',%s,%s,'trace-1','{}'::jsonb,now(),now())",
                (tenant_id, workspace_id, operation_id, digest, f"idem-{operation_id}", digest),
            )
    store = PostgresCloudStore(dsn)
    try:
        counts = PostgresOperationsStatusRepository(store).read_counts(
            OperationsStatusContext("tenant-operations", "workspace-operations", "actor-1")
        )
    finally:
        store.close()
    assert counts == OperationsCounts(sync_pending=1, queue_pending=0, queue_failed=0)
