"""PostgreSQL source-of-truth adapter for approved Sync copy/publish operations."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Callable, cast

from psycopg import Connection
from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .object_queue import ObjectQueueCoordinator, ObjectQueueError
from .sync import (
    ConflictResolutionChoice,
    ConflictResolutionView,
    ReferenceSyncRepository,
    SyncContext,
    SyncError,
    SyncItemInput,
    SyncOperationView,
    SyncService,
    TargetVersion,
    TransferBatchView,
    TransferPayload,
    _Conflict,
    _Operation,
    _fingerprint,
)


def _cloud_context(context: SyncContext, capability: str) -> CloudAccessContext:
    return CloudAccessContext(
        context.tenant_id, context.workspace_id, context.actor_id, capability
    )


class ObjectQueueSyncTransferPort:
    """Creates only server-owned Cloud objects through the durable Object Queue."""

    def __init__(self, coordinator: ObjectQueueCoordinator) -> None:
        self._coordinator = coordinator

    def transmit(
        self,
        context: SyncContext,
        item: SyncItemInput,
        content: bytes,
        idempotency_key: str,
        *,
        relation: str,
    ) -> TargetVersion:
        if len(content) != item.byte_size:
            raise SyncError("SYNC_CONTENT_INVALID", 400)
        digest = hashlib.sha256(content).hexdigest()
        if digest != item.digest_sha256:
            raise SyncError("SYNC_CONTENT_DIGEST_MISMATCH", 400)
        try:
            submitted = self._coordinator.submit(
                _cloud_context(context, "object.write"), area="source", content=content,
                content_type=item.content_type, idempotency_key=idempotency_key,
                trace_id=context.trace_id,
            )
        except ObjectQueueError as error:
            raise SyncError(error.code, 503 if error.retryable else 409,
                            retryable=error.retryable) from None
        target_version_id = "sync-version-" + hashlib.sha256(
            f"{submitted.object_id}|{relation}".encode()
        ).hexdigest()[:24]
        return TargetVersion(
            target_version_id, submitted.object_id, item.item_id, digest,
            item.base_cloud_version_id, relation,
        )


class UnavailableSyncTransferPort:
    def transmit(self, *_args: object, **_kwargs: object) -> TargetVersion:
        raise SyncError("OBJECT_STORAGE_UNAVAILABLE", 503, retryable=True)


def _dump_operation(operation: _Operation, repository: ReferenceSyncRepository) -> dict[str, object]:
    idempotency: list[dict[str, object]] = []
    for key, value in repository.idempotency.items():
        if len(key) != 4 or key[0] != operation.operation_id:
            continue
        result = value[1]
        if isinstance(result, ConflictResolutionView):
            encoded_result: object = {
                "resolution_id": result.resolution_id,
                "conflict_id": result.conflict_id,
                "choice": result.choice.value,
                "target_version_id": result.target_version_id,
            }
        else:
            encoded_result = str(result)
        idempotency.append({
            "actor_id": key[1], "kind": key[2], "key": key[3],
            "fingerprint": value[0], "result": encoded_result,
        })
    return {
        "items": [{
            "item_id": item.item_id, "source_version_id": item.source_version_id,
            "local_object_id": item.local_object_id, "digest_sha256": item.digest_sha256,
            "byte_size": item.byte_size, "content_type": item.content_type,
            "base_cloud_version_id": item.base_cloud_version_id,
            "base_cloud_digest": item.base_cloud_digest,
        } for item in operation.items.values()],
        "approved_item_ids": list(operation.approved_item_ids),
        "approval_snapshot_id": operation.approval_snapshot_id,
        "completed_item_ids": sorted(operation.completed_item_ids),
        "batches": [{
            "batch_id": item.batch_id, "state": item.state, "sequence": item.sequence,
            "cursor": item.cursor, "next_cursor": item.next_cursor,
            "transferred_item_ids": list(item.transferred_item_ids),
            "conflict_ids": list(item.conflict_ids),
        } for item in operation.batches],
        "conflicts": [{
            "conflict_id": item.conflict_id, "item_id": item.item_id,
            "local_version_id": item.local_version_id, "local_digest": item.local_digest,
            "cloud_version_id": item.cloud_version_id, "cloud_digest": item.cloud_digest,
            "base_version_id": item.base_version_id, "base_digest": item.base_digest,
            "state": item.state,
        } for item in operation.conflicts],
        "target_versions": [{
            "target_version_id": item.target_version_id,
            "target_object_id": item.target_object_id, "item_id": item.item_id,
            "digest_sha256": item.digest_sha256,
            "previous_cloud_version_id": item.previous_cloud_version_id,
            "relation": item.relation,
        } for item in operation.target_versions],
        "reindex_state": operation.reindex_state,
        "idempotency": idempotency,
    }


def _hydrate(row: tuple[object, ...]) -> tuple[_Operation, ReferenceSyncRepository]:
    operation_id, tenant_id, workspace_id, actor_id, target_area, state, version, digest, policy, document = row
    data = cast(dict[str, object], document)
    context = SyncContext(str(tenant_id), str(workspace_id), str(actor_id), "trace-load", str(policy))
    items = {
        str(raw["item_id"]): SyncItemInput(**raw)
        for raw in cast(list[dict[str, object]], data["items"])
    }
    operation = _Operation(
        str(operation_id), context, str(target_area), items, str(digest),
        state=str(state), version=int(cast(int, version)),
        approved_item_ids=tuple(cast(list[str], data["approved_item_ids"])),
        approval_snapshot_id=cast(str | None, data["approval_snapshot_id"]),
        batches=[TransferBatchView(
            str(raw["batch_id"]), str(raw["state"]), int(cast(int, raw["sequence"])),
            cast(str | None, raw["cursor"]), cast(str | None, raw["next_cursor"]),
            tuple(cast(list[str], raw["transferred_item_ids"])),
            tuple(cast(list[str], raw["conflict_ids"])),
        ) for raw in cast(list[dict[str, object]], data["batches"])],
        conflicts=[_Conflict(**raw) for raw in cast(list[dict[str, object]], data["conflicts"])],
        target_versions=[TargetVersion(**raw) for raw in cast(list[dict[str, object]], data["target_versions"])],
        completed_item_ids=set(cast(list[str], data["completed_item_ids"])),
        reindex_state=cast(str | None, data["reindex_state"]),
    )
    repository = ReferenceSyncRepository()
    repository.operations[operation.operation_id] = operation
    for raw in cast(list[dict[str, object]], data.get("idempotency", [])):
        result: object = raw["result"]
        if raw["kind"] == "resolution":
            encoded = cast(dict[str, object], result)
            result = ConflictResolutionView(
                str(encoded["resolution_id"]), str(encoded["conflict_id"]),
                ConflictResolutionChoice(str(encoded["choice"])),
                cast(str | None, encoded["target_version_id"]),
            )
        repository.idempotency[(operation.operation_id, str(raw["actor_id"]),
                                str(raw["kind"]), str(raw["key"]))] = (
            str(raw["fingerprint"]), result,
        )
    return operation, repository


class PostgresSyncService:
    """Normalized PostgreSQL metadata plus durable Object Queue transfer."""

    def __init__(self, cloud_store: PostgresCloudStore,
                 transfer_port: ObjectQueueSyncTransferPort | UnavailableSyncTransferPort,
                 *, clock: Callable[[], datetime]) -> None:
        self._cloud_store = cloud_store
        self._transfer = transfer_port
        self._clock = clock

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(12)}"

    def _transaction(self, context: SyncContext, capability: str):
        return self._cloud_store._transaction(_cloud_context(context, capability))

    @staticmethod
    def _row(connection: Connection[tuple[object, ...]], operation_id: str,
             *, lock: bool = False) -> tuple[object, ...] | None:
        suffix = " FOR UPDATE" if lock else ""
        return connection.execute(
            "SELECT operation_id,tenant_id,workspace_id,actor_id,target_area,state,"
            "version,preview_digest,policy_version,state_document FROM sync_operations "
            "WHERE operation_id=%s" + suffix, (operation_id,),
        ).fetchone()

    def locate_workspace(self, tenant_id: str, operation_id: str) -> str:
        context = SyncContext(tenant_id, "locator", "runtime", "trace-locator", "locator")
        try:
            with self._transaction(context, "sync.read") as connection:
                row = connection.execute(
                    "SELECT workspace_id FROM sync_operation_locator WHERE operation_id=%s",
                    (operation_id,),
                ).fetchone()
        except CloudDatabaseError as error:
            raise SyncError(error.code, 503, retryable=error.retryable) from None
        if row is None:
            raise SyncError("SYNC_OPERATION_UNAVAILABLE", 404)
        return str(row[0])

    def create_operation(self, context: SyncContext, *, target_area: str,
                         items: tuple[SyncItemInput, ...], idempotency_key: str,
                         if_match: str) -> SyncOperationView:
        fingerprint = _fingerprint((target_area, items))
        try:
            with self._transaction(context, "sync.write") as connection:
                replay = connection.execute(
                    "SELECT operation_id,request_fingerprint FROM sync_operations "
                    "WHERE actor_id=%s AND idempotency_key=%s",
                    (context.actor_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay[1]) != fingerprint:
                        raise SyncError("IDEMPOTENCY_KEY_REUSED")
                    row = self._row(connection, str(replay[0]))
                    assert row is not None
                    operation, repository = _hydrate(row)
                    return SyncService(repository, self._transfer, clock=self._clock)._view(operation)
                repository = ReferenceSyncRepository()
                service = SyncService(repository, self._transfer, clock=self._clock)
                view = service.create_operation(
                    context, target_area=target_area, items=items,
                    idempotency_key=idempotency_key, if_match=if_match,
                )
                operation = repository.operations[view.operation_id]
                now = self._clock()
                connection.execute(
                    "INSERT INTO sync_operation_locator "
                    "(tenant_id,operation_id,workspace_id,actor_id,created_at) VALUES (%s,%s,%s,%s,%s)",
                    (context.tenant_id, view.operation_id, context.workspace_id, context.actor_id, now),
                )
                connection.execute(
                    "INSERT INTO sync_operations (tenant_id,workspace_id,operation_id,actor_id,"
                    "target_area,state,version,preview_digest,policy_version,idempotency_key,"
                    "request_fingerprint,trace_id,state_document,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, view.operation_id, context.actor_id,
                     target_area, operation.state, operation.version, operation.manifest_digest,
                     context.policy_version, idempotency_key, fingerprint, context.trace_id,
                     Jsonb(_dump_operation(operation, repository)), now, now),
                )
                for item in items:
                    connection.execute(
                        "INSERT INTO sync_preview_items (tenant_id,workspace_id,operation_id,item_id,"
                        "source_version_id,local_object_id,object_digest,byte_size,content_type,"
                        "base_cloud_version_id,base_cloud_digest,created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (context.tenant_id, context.workspace_id, view.operation_id, item.item_id,
                         item.source_version_id, item.local_object_id, item.digest_sha256,
                         item.byte_size, item.content_type, item.base_cloud_version_id,
                         item.base_cloud_digest, now),
                    )
                return view
        except SyncError:
            raise
        except CloudDatabaseError as error:
            raise SyncError(error.code, 503, retryable=error.retryable) from None

    def get_operation(self, context: SyncContext, operation_id: str) -> SyncOperationView:
        try:
            with self._transaction(context, "sync.read") as connection:
                row = self._row(connection, operation_id)
                if row is None:
                    raise SyncError("SYNC_OPERATION_UNAVAILABLE", 404)
                operation, repository = _hydrate(row)
                operation.context = context
                return SyncService(repository, self._transfer, clock=self._clock)._view(operation)
        except SyncError:
            raise
        except CloudDatabaseError as error:
            raise SyncError(error.code, 503, retryable=error.retryable) from None

    def _mutate(self, context: SyncContext, operation_id: str,
                callback: Callable[[SyncService], object],
                *, step_up_authorization_id: str | None = None) -> object:
        try:
            with self._transaction(context, "sync.write") as connection:
                row = self._row(connection, operation_id, lock=True)
                if row is None:
                    raise SyncError("SYNC_OPERATION_UNAVAILABLE", 404)
                operation, repository = _hydrate(row)
                operation.context = context
                before_version = operation.version
                before_approval = operation.approval_snapshot_id
                before_batches = len(operation.batches)
                before_conflicts = len(operation.conflicts)
                before_targets = len(operation.target_versions)
                service = SyncService(repository, self._transfer, clock=self._clock)
                result = callback(service)
                if operation.version == before_version:
                    return result
                now = self._clock()
                if before_approval is None and operation.approval_snapshot_id is not None:
                    key, value = next(
                        (key, value) for key, value in repository.idempotency.items()
                        if key[0] == operation_id and key[2] == "approve"
                    )
                    connection.execute(
                        "INSERT INTO sync_approval_snapshots (tenant_id,workspace_id,approval_snapshot_id,"
                        "operation_id,actor_id,target_area,manifest_digest,policy_version,"
                        "step_up_authorization_digest,idempotency_key,request_fingerprint,approved_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (context.tenant_id, context.workspace_id, operation.approval_snapshot_id,
                         operation_id, context.actor_id, operation.target_area,
                         operation.manifest_digest, context.policy_version,
                         hashlib.sha256(cast(str, step_up_authorization_id).encode()).hexdigest(),
                         key[3], value[0], now),
                    )
                    for item_id in operation.approved_item_ids:
                        item = operation.items[item_id]
                        connection.execute(
                            "INSERT INTO sync_manifest_items (tenant_id,workspace_id,approval_snapshot_id,"
                            "operation_id,item_id,source_version_id,local_object_id,object_digest,"
                            "byte_size,content_type,base_cloud_version_id,base_cloud_digest,approved_at) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (context.tenant_id, context.workspace_id, operation.approval_snapshot_id,
                             operation_id, item.item_id, item.source_version_id, item.local_object_id,
                             item.digest_sha256, item.byte_size, item.content_type,
                             item.base_cloud_version_id, item.base_cloud_digest, now),
                        )
                new_batches = operation.batches[before_batches:]
                new_conflicts = operation.conflicts[before_conflicts:]
                new_targets = operation.target_versions[before_targets:]
                for conflict in new_conflicts:
                    connection.execute(
                        "INSERT INTO sync_conflicts (tenant_id,workspace_id,conflict_id,operation_id,"
                        "approval_snapshot_id,item_id,local_version_id,local_digest,cloud_version_id,"
                        "cloud_digest,base_version_id,base_digest,trace_id,created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (context.tenant_id, context.workspace_id, conflict.conflict_id, operation_id,
                         operation.approval_snapshot_id, conflict.item_id, conflict.local_version_id,
                         conflict.local_digest, conflict.cloud_version_id, conflict.cloud_digest,
                         conflict.base_version_id, conflict.base_digest, context.trace_id, now),
                    )
                for target in new_targets:
                    connection.execute(
                        "INSERT INTO sync_target_versions (tenant_id,workspace_id,target_version_id,"
                        "operation_id,approval_snapshot_id,item_id,object_id,digest_sha256,"
                        "previous_cloud_version_id,relation,trace_id,created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (context.tenant_id, context.workspace_id, target.target_version_id,
                         operation_id, operation.approval_snapshot_id, target.item_id,
                         target.target_object_id, target.digest_sha256,
                         target.previous_cloud_version_id, target.relation, context.trace_id, now),
                    )
                for batch in new_batches:
                    key, value = next(
                        (key, value) for key, value in repository.idempotency.items()
                        if key[0] == operation_id and key[2] == "transfer" and value[1] == batch.batch_id
                    )
                    connection.execute(
                        "INSERT INTO sync_transfer_batches (tenant_id,workspace_id,batch_id,operation_id,"
                        "approval_snapshot_id,actor_id,sequence,cursor_value,next_cursor,state,"
                        "idempotency_key,request_fingerprint,trace_id,created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (context.tenant_id, context.workspace_id, batch.batch_id, operation_id,
                         operation.approval_snapshot_id, context.actor_id, batch.sequence,
                         batch.cursor, batch.next_cursor, batch.state, key[3], value[0],
                         context.trace_id, now),
                    )
                    conflict_items = {item.conflict_id: item.item_id for item in new_conflicts}
                    for item_id in batch.transferred_item_ids:
                        connection.execute(
                            "INSERT INTO sync_transfer_attempts (tenant_id,workspace_id,attempt_id,"
                            "batch_id,operation_id,approval_snapshot_id,item_id,outcome,"
                            "transferred_digest,trace_id,created_at) VALUES "
                            "(%s,%s,%s,%s,%s,%s,%s,'transferred',%s,%s,%s)",
                            (context.tenant_id, context.workspace_id, self._id("sync-attempt"),
                             batch.batch_id, operation_id, operation.approval_snapshot_id, item_id,
                             operation.items[item_id].digest_sha256, context.trace_id, now),
                        )
                    for conflict_id in batch.conflict_ids:
                        connection.execute(
                            "INSERT INTO sync_transfer_attempts (tenant_id,workspace_id,attempt_id,"
                            "batch_id,operation_id,approval_snapshot_id,item_id,outcome,trace_id,created_at) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,'conflict',%s,%s)",
                            (context.tenant_id, context.workspace_id, self._id("sync-attempt"),
                             batch.batch_id, operation_id, operation.approval_snapshot_id,
                             conflict_items[conflict_id], context.trace_id, now),
                        )
                if isinstance(result, ConflictResolutionView):
                    key, value = next(
                        (key, value) for key, value in repository.idempotency.items()
                        if key[0] == operation_id and key[2] == "resolution"
                        and isinstance(value[1], ConflictResolutionView)
                        and value[1].resolution_id == result.resolution_id
                    )
                    connection.execute(
                        "INSERT INTO sync_conflict_resolutions (tenant_id,workspace_id,resolution_id,"
                        "conflict_id,operation_id,actor_id,choice,resulting_target_version_id,"
                        "idempotency_key,request_fingerprint,trace_id,resolved_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (context.tenant_id, context.workspace_id, result.resolution_id,
                         result.conflict_id, operation_id, context.actor_id, result.choice.value,
                         result.target_version_id, key[3], value[0], context.trace_id, now),
                    )
                if operation.reindex_state == "reindex_requested":
                    for target in new_targets:
                        connection.execute(
                            "INSERT INTO sync_reindex_requests (tenant_id,workspace_id,reindex_request_id,"
                            "operation_id,target_version_id,state,trace_id,requested_at) "
                            "VALUES (%s,%s,%s,%s,%s,'reindex_requested',%s,%s)",
                            (context.tenant_id, context.workspace_id, self._id("sync-reindex"),
                             operation_id, target.target_version_id, context.trace_id, now),
                        )
                connection.execute("SELECT set_config('app.sync_transition','allowed',true)")
                connection.execute(
                    "UPDATE sync_operations SET state=%s,version=%s,state_document=%s,"
                    "trace_id=%s,updated_at=%s WHERE operation_id=%s AND version=%s",
                    (operation.state, operation.version,
                     Jsonb(_dump_operation(operation, repository)),
                     context.trace_id, now, operation_id, before_version),
                )
                return result
        except SyncError:
            raise
        except CloudDatabaseError as error:
            raise SyncError(error.code, 503, retryable=error.retryable) from None

    def approve(self, context: SyncContext, **kwargs: object) -> SyncOperationView:
        return cast(SyncOperationView, self._mutate(
            context, cast(str, kwargs["operation_id"]),
            lambda service: service.approve(context, **kwargs),
            step_up_authorization_id=cast(str, kwargs["step_up_authorization_id"]),
        ))

    def transfer_batch(self, context: SyncContext, **kwargs: object) -> TransferBatchView:
        return cast(TransferBatchView, self._mutate(
            context, cast(str, kwargs["operation_id"]),
            lambda service: service.transfer_batch(context, **kwargs),
        ))

    def resolve_conflict(self, context: SyncContext, **kwargs: object) -> ConflictResolutionView:
        return cast(ConflictResolutionView, self._mutate(
            context, cast(str, kwargs["operation_id"]),
            lambda service: service.resolve_conflict(context, **kwargs),
        ))
