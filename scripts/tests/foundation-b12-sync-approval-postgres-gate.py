from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime

from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.sync import SyncContext, SyncItemInput
from daon_user_api.sync_postgres import PostgresSyncService, UnavailableSyncTransferPort


def main() -> None:
    store = PostgresCloudStore(os.environ["DAON_SYNC_SETTINGS_TEST_DSN"])
    try:
        context = SyncContext("tenant-sync-menu", "workspace-sync-menu", "actor-sync-menu", "trace-sync-menu", "policy-v1")
        foreign = SyncContext("tenant-sync-foreign", "workspace-sync-foreign", "actor-sync-foreign", "trace-sync-foreign", "policy-v1")
        for value in (context, foreign):
            store.seed_scope(CloudAccessContext(value.tenant_id, value.workspace_id, value.actor_id, "sync.write"))
        service = PostgresSyncService(store, UnavailableSyncTransferPort(), clock=lambda: datetime(2026, 8, 15, 7, 45, tzinfo=UTC))
        item = SyncItemInput(
            "item-sync-menu", "source-version-sync-menu", "local-object-sync-menu",
            hashlib.sha256(b"sync-menu").hexdigest(), 9, "text/plain", None, None,
        )
        created = service.create_operation(
            context, target_area="cloud_sync", items=(item,),
            idempotency_key="sync-menu-create-0001", if_match="*",
        )
        listed = service.list_operations(context)
        assert tuple(value.operation_id for value in listed) == (created.operation_id,)
        assert listed[0].item_ids == (item.item_id,)
        assert service.list_operations(foreign) == ()
        approved = service.approve(
            context, operation_id=created.operation_id, approved_item_ids=(item.item_id,),
            step_up_authorization_id="step-up-sync-menu", expected_version=created.version,
            idempotency_key="sync-menu-approve-0001", approval_verified=True,
        )
        replay = service.approve(
            context, operation_id=created.operation_id, approved_item_ids=(item.item_id,),
            step_up_authorization_id="step-up-sync-menu", expected_version=created.version,
            idempotency_key="sync-menu-approve-0001", approval_verified=True,
        )
        assert approved == replay and approved.state == "approved" and approved.version == 2
        assert service.list_operations(context)[0].approved_item_ids == (item.item_id,)
        print("SYNC_APPROVAL_SETTINGS_PG_GATE PASS list=1 item_ids=1 approval=exact replay=exact rls_cross=0")
    finally:
        store.close()


if __name__ == "__main__":
    main()
