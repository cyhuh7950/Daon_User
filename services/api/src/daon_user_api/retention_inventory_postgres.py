"""Authoritative derivative inventory projection for Source deletion requests."""

from __future__ import annotations

import hashlib

from .cloud_storage import CloudAccessContext, PostgresCloudStore
from .retention import DerivativeInput, RetentionContext, RetentionError


class PostgresRetentionInventoryProvider:
    def __init__(self, store: PostgresCloudStore) -> None:
        self._store = store

    @staticmethod
    def _item_id(context: RetentionContext, source_id: str, kind: str) -> str:
        digest = hashlib.sha256(f"{context.tenant_id}|{context.workspace_id}|{source_id}|{kind}".encode()).hexdigest()[:28]
        return f"inventory-{kind}-{digest}"

    def __call__(self, context: RetentionContext, source_id: str) -> tuple[DerivativeInput, ...]:
        cloud = CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "retention.inventory.read")
        with self._store._transaction(cloud) as connection:
            return self.read_in_transaction(connection, context, source_id)

    def read_in_transaction(self, connection, context: RetentionContext, source_id: str) -> tuple[DerivativeInput, ...]:  # type: ignore[no-untyped-def]
        source = connection.execute(
            "SELECT sv.record_id,sv.object_id FROM sources s JOIN source_versions sv ON sv.tenant_id=s.tenant_id "
            "AND sv.workspace_id=s.workspace_id AND sv.source_id=s.record_id WHERE s.tenant_id=%s AND s.workspace_id=%s "
            "AND s.record_id=%s ORDER BY sv.version DESC LIMIT 1",
            (context.tenant_id, context.workspace_id, source_id),
        ).fetchone()
        if source is None or source[1] is None:
            raise RetentionError("DELETION_INVENTORY_INVALID", 400)
        version_id, object_id = str(source[0]), str(source[1])
        original = connection.execute(
            "SELECT object_id FROM object_records WHERE tenant_id=%s AND workspace_id=%s AND object_id=%s",
            (context.tenant_id, context.workspace_id, object_id),
        ).fetchone()
        if original is None:
            raise RetentionError("DELETION_INVENTORY_INVALID", 400)
        index = connection.execute(
            "SELECT record_id FROM index_versions WHERE tenant_id=%s AND workspace_id=%s AND source_version_id=%s ORDER BY created_at DESC,record_id DESC LIMIT 1",
            (context.tenant_id, context.workspace_id, version_id),
        ).fetchone()
        sync = connection.execute(
            "SELECT item_id FROM sync_preview_items WHERE tenant_id=%s AND workspace_id=%s AND source_version_id=%s ORDER BY item_id LIMIT 1",
            (context.tenant_id, context.workspace_id, version_id),
        ).fetchone()
        item = lambda kind: self._item_id(context, source_id, kind)
        return (
            DerivativeInput("original_content", str(original[0]), disposition="present"),
            DerivativeInput("index", str(index[0]) if index else item("index"), disposition="present" if index else "not_present"),
            DerivativeInput("preview", item("preview"), disposition="not_applicable"),
            DerivativeInput("cache", item("cache"), disposition="not_applicable"),
            DerivativeInput("known_local_copy", item("known_local_copy"), acknowledgement_required=True, disposition="verification_pending"),
            DerivativeInput("sync_reference", str(sync[0]) if sync else item("sync_reference"), disposition="present" if sync else "not_present"),
        )
