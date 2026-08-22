"""Approved Local-private to Cloud-sync copy/publish domain contract."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-/]{0,254}$")


class SyncError(RuntimeError):
    def __init__(self, code: str, status: int = 409, *, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


class ConflictResolutionChoice(str, Enum):
    KEEP_LOCAL_AS_NEW_VERSION = "keep_local_as_new_version"
    KEEP_CLOUD = "keep_cloud"
    KEEP_BOTH = "keep_both"


class SyncItemKind(str, Enum):
    SOURCE_VERSION = "source_version"
    OUTPUT_VERSION = "output_version"


@dataclass(frozen=True, slots=True)
class SyncContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str

    def __post_init__(self) -> None:
        for value in (
            self.tenant_id, self.workspace_id, self.actor_id,
            self.trace_id, self.policy_version,
        ):
            if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
                raise SyncError("SYNC_CONTEXT_INVALID", 400)


@dataclass(frozen=True, slots=True)
class SyncItemInput:
    item_id: str
    source_version_id: str | None
    local_object_id: str
    digest_sha256: str
    byte_size: int
    content_type: str
    base_cloud_version_id: str | None
    base_cloud_digest: str | None
    item_kind: SyncItemKind = SyncItemKind.SOURCE_VERSION
    output_version_id: str | None = None
    dependency_item_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            kind = SyncItemKind(self.item_kind)
        except (TypeError, ValueError):
            raise SyncError("SYNC_ITEM_INVALID", 400) from None
        object.__setattr__(self, "item_kind", kind)
        source_valid = (
            isinstance(self.source_version_id, str)
            and _SAFE_ID.fullmatch(self.source_version_id) is not None
        )
        output_valid = (
            isinstance(self.output_version_id, str)
            and _SAFE_ID.fullmatch(self.output_version_id) is not None
        )
        dependencies_valid = (
            isinstance(self.dependency_item_ids, tuple)
            and tuple(sorted(set(self.dependency_item_ids))) == self.dependency_item_ids
            and all(isinstance(value, str) and _SAFE_ID.fullmatch(value)
                    for value in self.dependency_item_ids)
        )
        version_contract_valid = (
            kind is SyncItemKind.SOURCE_VERSION
            and source_valid and not output_valid and not self.dependency_item_ids
        ) or (
            kind is SyncItemKind.OUTPUT_VERSION
            and output_valid and not source_valid and bool(self.dependency_item_ids)
            and self.content_type == "application/vnd.daon.offline-studio-output+json"
        )
        if (
            any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value)
                for value in (self.item_id, self.local_object_id))
            or not version_contract_valid
            or not dependencies_valid
            or not isinstance(self.digest_sha256, str)
            or not _DIGEST.fullmatch(self.digest_sha256)
            or not isinstance(self.byte_size, int)
            or isinstance(self.byte_size, bool)
            or self.byte_size < 0
            or not isinstance(self.content_type, str)
            or not _CONTENT_TYPE.fullmatch(self.content_type)
            or (self.base_cloud_version_id is None) != (self.base_cloud_digest is None)
            or (
                self.base_cloud_version_id is not None
                and not _SAFE_ID.fullmatch(self.base_cloud_version_id)
            )
            or (
                self.base_cloud_digest is not None
                and not _DIGEST.fullmatch(self.base_cloud_digest)
            )
        ):
            raise SyncError("SYNC_ITEM_INVALID", 400)

    @property
    def version_id(self) -> str:
        value = (
            self.source_version_id
            if self.item_kind is SyncItemKind.SOURCE_VERSION
            else self.output_version_id
        )
        assert value is not None
        return value


@dataclass(frozen=True, slots=True)
class TransferPayload:
    item_id: str
    content: bytes
    current_cloud_version_id: str | None
    current_cloud_digest: str | None


@dataclass(frozen=True, slots=True)
class TargetVersion:
    target_version_id: str
    target_object_id: str
    item_id: str
    digest_sha256: str
    previous_cloud_version_id: str | None
    relation: str


@dataclass(frozen=True, slots=True)
class SyncConflictView:
    conflict_id: str
    item_id: str
    local_version_id: str
    local_digest: str
    cloud_version_id: str | None
    cloud_digest: str | None
    base_version_id: str | None
    base_digest: str | None
    state: str


@dataclass(frozen=True, slots=True)
class TransferBatchView:
    batch_id: str
    state: str
    sequence: int
    cursor: str | None
    next_cursor: str | None
    transferred_item_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ConflictResolutionView:
    resolution_id: str
    conflict_id: str
    choice: ConflictResolutionChoice
    target_version_id: str | None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SyncOperationView:
    operation_id: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    target_area: str
    state: str
    version: int
    manifest_digest: str
    approved_item_ids: tuple[str, ...]
    completed_item_ids: tuple[str, ...]
    batches: tuple[TransferBatchView, ...]
    conflicts: tuple[SyncConflictView, ...]
    target_versions: tuple[TargetVersion, ...]
    reindex_state: str | None
    source_mutations: int
    overwrite_count: int
    item_ids: tuple[str, ...] = ()

    @property
    def etag(self) -> str:
        return f'"sync:{self.operation_id}:{self.version}"'


@dataclass(slots=True)
class _Conflict:
    conflict_id: str
    item_id: str
    local_version_id: str
    local_digest: str
    cloud_version_id: str | None
    cloud_digest: str | None
    base_version_id: str | None
    base_digest: str | None
    state: str = "unresolved"


@dataclass(slots=True)
class _Operation:
    operation_id: str
    context: SyncContext
    target_area: str
    items: dict[str, SyncItemInput]
    manifest_digest: str
    state: str = "awaiting_approval"
    version: int = 1
    approved_item_ids: tuple[str, ...] = ()
    approval_snapshot_id: str | None = None
    batches: list[TransferBatchView] = field(default_factory=list)
    conflicts: list[_Conflict] = field(default_factory=list)
    target_versions: list[TargetVersion] = field(default_factory=list)
    completed_item_ids: set[str] = field(default_factory=set)
    reindex_state: str | None = None


class ReferenceSyncRepository:
    """Thread-safe deterministic repository used when no Cloud DB is configured."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.operations: dict[str, _Operation] = {}
        self.idempotency: dict[tuple[str, str, str, str], tuple[str, object]] = {}
        self.audit: list[tuple[str, str, str]] = []


class ReferenceTransferPort:
    """Bounded transfer adapter for tests; it stores no paths or credentials."""

    def __init__(self) -> None:
        self.transmission_count = 0
        self._replays: dict[tuple[str, str], TargetVersion] = {}

    def transmit(
        self,
        context: SyncContext,
        item: SyncItemInput,
        content: bytes,
        idempotency_key: str,
        *,
        relation: str,
    ) -> TargetVersion:
        del context
        replay = self._replays.get((item.item_id, idempotency_key))
        if replay is not None:
            return replay
        if not isinstance(content, bytes) or len(content) != item.byte_size:
            raise SyncError("SYNC_CONTENT_INVALID", 400)
        digest = hashlib.sha256(content).hexdigest()
        if digest != item.digest_sha256:
            raise SyncError("SYNC_CONTENT_DIGEST_MISMATCH", 400)
        self.transmission_count += 1
        suffix = hashlib.sha256(f"{item.item_id}|{idempotency_key}".encode()).hexdigest()[:24]
        version = TargetVersion(
            f"cloud-version-{suffix}", f"cloud-object-{suffix}", item.item_id,
            digest, item.base_cloud_version_id, relation,
        )
        self._replays[(item.item_id, idempotency_key)] = version
        return version


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class SyncService:
    def __init__(
        self,
        repository: ReferenceSyncRepository,
        transfer_port: ReferenceTransferPort,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._transfer = transfer_port
        self._clock = clock

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(12)}"

    @staticmethod
    def _validate_write(idempotency_key: str, expected_version: int | str) -> None:
        if not isinstance(idempotency_key, str) or not _SAFE_ID.fullmatch(idempotency_key):
            raise SyncError("IDEMPOTENCY_KEY_INVALID", 400)
        if expected_version != "*" and (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 1
        ):
            raise SyncError("IF_MATCH_INVALID", 400)

    def _operation(self, context: SyncContext, operation_id: str) -> _Operation:
        operation = self._repository.operations.get(operation_id)
        if (
            operation is None
            or operation.context.tenant_id != context.tenant_id
            or operation.context.workspace_id != context.workspace_id
        ):
            raise SyncError("SYNC_OPERATION_UNAVAILABLE", 404)
        return operation

    def _audit(self, operation: _Operation, action: str, trace_id: str) -> None:
        self._repository.audit.append((operation.operation_id, action, trace_id))

    def _view(self, operation: _Operation) -> SyncOperationView:
        return SyncOperationView(
            operation.operation_id, operation.context.tenant_id,
            operation.context.workspace_id, operation.context.actor_id,
            operation.target_area, operation.state, operation.version,
            operation.manifest_digest, operation.approved_item_ids,
            tuple(sorted(operation.completed_item_ids)), tuple(operation.batches),
            tuple(SyncConflictView(
                item.conflict_id, item.item_id, item.local_version_id,
                item.local_digest, item.cloud_version_id, item.cloud_digest,
                item.base_version_id, item.base_digest, item.state,
            ) for item in operation.conflicts),
            tuple(operation.target_versions), operation.reindex_state, 0, 0,
            tuple(sorted(operation.items)),
        )

    def create_operation(
        self,
        context: SyncContext,
        *,
        target_area: str,
        items: tuple[SyncItemInput, ...],
        idempotency_key: str,
        if_match: str,
    ) -> SyncOperationView:
        self._validate_write(idempotency_key, if_match)
        if if_match != "*" or target_area != "cloud_sync" or not items:
            raise SyncError("SYNC_PREVIEW_INVALID", 400)
        by_id = {item.item_id: item for item in items}
        if len(by_id) != len(items):
            raise SyncError("SYNC_PREVIEW_INVALID", 400)
        fingerprint = _fingerprint((target_area, items))
        replay_key = (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key)
        with self._repository.lock:
            replay = self._repository.idempotency.get(replay_key)
            if replay is not None:
                if replay[0] != fingerprint:
                    raise SyncError("IDEMPOTENCY_KEY_REUSED")
                return self._view(self._operation(context, str(replay[1])))
            manifest = hashlib.sha256("|".join(
                f"{item.item_id}:{item.item_kind.value}:{item.version_id}:"
                f"{','.join(item.dependency_item_ids)}:{item.digest_sha256}"
                for item in sorted(items, key=lambda value: value.item_id)
            ).encode()).hexdigest()
            operation = _Operation(self._id("sync"), context, target_area, by_id, manifest)
            self._repository.operations[operation.operation_id] = operation
            self._repository.idempotency[replay_key] = (fingerprint, operation.operation_id)
            self._audit(operation, "sync.preview.created", context.trace_id)
            return self._view(operation)

    def get_operation(self, context: SyncContext, operation_id: str) -> SyncOperationView:
        with self._repository.lock:
            return self._view(self._operation(context, operation_id))

    def list_operations(self, context: SyncContext) -> tuple[SyncOperationView, ...]:
        with self._repository.lock:
            operations = (
                operation for operation in self._repository.operations.values()
                if operation.context.tenant_id == context.tenant_id
                and operation.context.workspace_id == context.workspace_id
            )
            return tuple(self._view(operation) for operation in sorted(
                operations, key=lambda item: item.operation_id, reverse=True,
            ))

    def locate_workspace(self, tenant_id: str, operation_id: str) -> str:
        with self._repository.lock:
            operation = self._repository.operations.get(operation_id)
            if operation is None or operation.context.tenant_id != tenant_id:
                raise SyncError("SYNC_OPERATION_UNAVAILABLE", 404)
            return operation.context.workspace_id

    def approve(
        self,
        context: SyncContext,
        *,
        operation_id: str,
        approved_item_ids: tuple[str, ...],
        step_up_authorization_id: str,
        expected_version: int,
        idempotency_key: str,
        approval_verified: bool,
    ) -> SyncOperationView:
        self._validate_write(idempotency_key, expected_version)
        if not approval_verified or not step_up_authorization_id:
            raise SyncError("STEP_UP_REQUIRED", 403)
        fingerprint = _fingerprint((operation_id, approved_item_ids, step_up_authorization_id, expected_version))
        with self._repository.lock:
            operation = self._operation(context, operation_id)
            key = (operation_id, context.actor_id, "approve", idempotency_key)
            replay = self._repository.idempotency.get(key)
            if replay is not None:
                if replay[0] != fingerprint:
                    raise SyncError("IDEMPOTENCY_KEY_REUSED")
                return self._view(operation)
            if operation.version != expected_version:
                raise SyncError("SYNC_VERSION_CONFLICT", 409)
            approved = tuple(dict.fromkeys(approved_item_ids))
            if not approved or any(item_id not in operation.items for item_id in approved):
                raise SyncError("SYNC_SCOPE_EXPANSION_DENIED", 403)
            approved_set = set(approved)
            if any(
                dependency not in approved_set
                for item_id in approved
                for dependency in operation.items[item_id].dependency_item_ids
            ):
                raise SyncError("SYNC_DEPENDENCY_REQUIRED", 409)
            operation.approved_item_ids = approved
            operation.approval_snapshot_id = self._id("sync-approval")
            operation.state = "approved"
            operation.version += 1
            self._repository.idempotency[key] = (fingerprint, operation.approval_snapshot_id)
            self._audit(operation, "sync.approved", context.trace_id)
            return self._view(operation)

    def transfer_batch(
        self,
        context: SyncContext,
        *,
        operation_id: str,
        expected_version: int,
        idempotency_key: str,
        cursor: str | None,
        payloads: tuple[TransferPayload, ...],
    ) -> TransferBatchView:
        self._validate_write(idempotency_key, expected_version)
        if not payloads:
            raise SyncError("SYNC_BATCH_INVALID", 400)
        fingerprint = _fingerprint((operation_id, expected_version, cursor, tuple(
            (item.item_id, hashlib.sha256(item.content).hexdigest(),
             item.current_cloud_version_id, item.current_cloud_digest)
            for item in payloads
        )))
        with self._repository.lock:
            operation = self._operation(context, operation_id)
            key = (operation_id, context.actor_id, "transfer", idempotency_key)
            replay = self._repository.idempotency.get(key)
            if replay is not None:
                if replay[0] != fingerprint:
                    raise SyncError("IDEMPOTENCY_KEY_REUSED")
                prior = next(batch for batch in operation.batches if batch.batch_id == replay[1])
                return TransferBatchView(
                    prior.batch_id, prior.state, prior.sequence, prior.cursor,
                    prior.next_cursor, prior.transferred_item_ids,
                    prior.conflict_ids, True,
                )
            if operation.approval_snapshot_id is None:
                self._audit(operation, "sync.transfer.denied", context.trace_id)
                raise SyncError("SYNC_APPROVAL_REQUIRED", 403)
            if operation.version != expected_version:
                raise SyncError("SYNC_VERSION_CONFLICT", 409)
            approved = set(operation.approved_item_ids)
            if any(payload.item_id not in approved for payload in payloads):
                raise SyncError("SYNC_SCOPE_EXPANSION_DENIED", 403)
            transferred: list[str] = []
            conflict_ids: list[str] = []
            batch_id = self._id("sync-batch")
            for payload in payloads:
                if payload.item_id in operation.completed_item_ids:
                    continue
                item = operation.items[payload.item_id]
                if any(
                    dependency not in operation.completed_item_ids
                    for dependency in item.dependency_item_ids
                ):
                    raise SyncError("SYNC_DEPENDENCY_REQUIRED", 409)
                current = (payload.current_cloud_version_id, payload.current_cloud_digest)
                base = (item.base_cloud_version_id, item.base_cloud_digest)
                if current != base:
                    conflict = _Conflict(
                        self._id("sync-conflict"), item.item_id, item.version_id,
                        item.digest_sha256, payload.current_cloud_version_id,
                        payload.current_cloud_digest, item.base_cloud_version_id,
                        item.base_cloud_digest,
                    )
                    operation.conflicts.append(conflict)
                    conflict_ids.append(conflict.conflict_id)
                    continue
                target = self._transfer.transmit(
                    context, item, payload.content, f"{idempotency_key}:{item.item_id}",
                    relation="copy",
                )
                operation.target_versions.append(target)
                operation.completed_item_ids.add(item.item_id)
                transferred.append(item.item_id)
            state = "conflict" if conflict_ids else "transferred"
            next_cursor = str(len(operation.completed_item_ids))
            batch = TransferBatchView(
                batch_id, state, len(operation.batches) + 1, cursor, next_cursor,
                tuple(transferred), tuple(conflict_ids), False,
            )
            operation.batches.append(batch)
            operation.version += 1
            if conflict_ids:
                operation.state = "conflict"
            elif set(operation.approved_item_ids) <= operation.completed_item_ids:
                operation.state = "reindex_requested"
                operation.reindex_state = "reindex_requested"
            else:
                operation.state = "transferring"
            self._repository.idempotency[key] = (fingerprint, batch_id)
            self._audit(operation, "sync.transfer.batch_recorded", context.trace_id)
            return batch

    def resolve_conflict(
        self,
        context: SyncContext,
        *,
        operation_id: str,
        conflict_id: str,
        expected_version: int,
        idempotency_key: str,
        choice: ConflictResolutionChoice,
        content: bytes | None,
    ) -> ConflictResolutionView:
        self._validate_write(idempotency_key, expected_version)
        if not isinstance(choice, ConflictResolutionChoice):
            raise SyncError("SYNC_RESOLUTION_INVALID", 400)
        fingerprint = _fingerprint((operation_id, conflict_id, expected_version, choice.value,
                                    None if content is None else hashlib.sha256(content).hexdigest()))
        with self._repository.lock:
            operation = self._operation(context, operation_id)
            key = (operation_id, context.actor_id, "resolution", idempotency_key)
            replay = self._repository.idempotency.get(key)
            if replay is not None:
                if replay[0] != fingerprint:
                    raise SyncError("IDEMPOTENCY_KEY_REUSED")
                prior = replay[1]
                assert isinstance(prior, ConflictResolutionView)
                return ConflictResolutionView(
                    prior.resolution_id, prior.conflict_id, prior.choice,
                    prior.target_version_id, True,
                )
            if operation.version != expected_version:
                raise SyncError("SYNC_VERSION_CONFLICT", 409)
            conflict = next(
                (item for item in operation.conflicts if item.conflict_id == conflict_id), None
            )
            if conflict is None or conflict.state != "unresolved":
                raise SyncError("SYNC_CONFLICT_UNAVAILABLE", 404)
            item = operation.items[conflict.item_id]
            target_version_id: str | None = None
            if choice in {
                ConflictResolutionChoice.KEEP_LOCAL_AS_NEW_VERSION,
                ConflictResolutionChoice.KEEP_BOTH,
            }:
                if content is None:
                    raise SyncError("SYNC_CONTENT_REQUIRED", 400)
                target = self._transfer.transmit(
                    context, item, content, f"{idempotency_key}:{item.item_id}",
                    relation=choice.value,
                )
                operation.target_versions.append(target)
                target_version_id = target.target_version_id
            conflict.state = "resolved"
            operation.completed_item_ids.add(item.item_id)
            operation.version += 1
            if (
                all(item.state == "resolved" for item in operation.conflicts)
                and set(operation.approved_item_ids) <= operation.completed_item_ids
            ):
                operation.state = "reindex_requested"
                operation.reindex_state = "reindex_requested"
            resolution = ConflictResolutionView(
                self._id("sync-resolution"), conflict_id, choice, target_version_id,
            )
            self._repository.idempotency[key] = (fingerprint, resolution)
            self._audit(operation, "sync.conflict.resolved", context.trace_id)
            return resolution
