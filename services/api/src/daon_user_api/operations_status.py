"""Safe workspace operations status projection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class OperationsStatusContext:
    tenant_id: str
    workspace_id: str
    actor_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value) for value in (self.tenant_id, self.workspace_id, self.actor_id)):
            raise ValueError("OPERATIONS_STATUS_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class OperationsCounts:
    sync_pending: int
    queue_pending: int
    queue_failed: int

    def __post_init__(self) -> None:
        if any(not isinstance(value, int) or value < 0 for value in (self.sync_pending, self.queue_pending, self.queue_failed)):
            raise ValueError("OPERATIONS_STATUS_COUNTS_INVALID")


@dataclass(frozen=True, slots=True)
class OperationsComponent:
    component_id: str
    status: str
    safe_code: str
    pending_count: int
    recovery_action: str


@dataclass(frozen=True, slots=True)
class OperationsStatusView:
    workspace_id: str
    overall_status: str
    checked_at: str
    components: tuple[OperationsComponent, ...]


class OperationsStatusRepository(Protocol):
    def read_counts(self, context: OperationsStatusContext) -> OperationsCounts: ...


class OperationsStatusService:
    def __init__(self, repository: OperationsStatusRepository) -> None:
        self._repository = repository

    def read(
        self, context: OperationsStatusContext, *, active_providers: int,
        selected_deployments: int, api_ready: bool, database_ready: bool,
        object_storage_ready: bool, checked_at: str,
    ) -> OperationsStatusView:
        counts = self._repository.read_counts(context)
        provider_ready = active_providers > 0 and selected_deployments > 0
        storage_ready = database_ready and object_storage_ready
        components = (
            OperationsComponent("provider", "ready" if provider_ready else "warning", "PROVIDER_READY" if provider_ready else "PROVIDER_CONFIGURATION_REQUIRED", 0, "none" if provider_ready else "open_llm_settings"),
            OperationsComponent("api", "ready" if api_ready else "error", "API_READY" if api_ready else "API_UNAVAILABLE", 0, "none" if api_ready else "refresh_status"),
            OperationsComponent("storage", "ready" if storage_ready else "error", "STORAGE_READY" if storage_ready else "STORAGE_UNAVAILABLE", 0, "none" if storage_ready else "refresh_status"),
            OperationsComponent("sync", "warning" if counts.sync_pending else "ready", "SYNC_PENDING" if counts.sync_pending else "SYNC_READY", counts.sync_pending, "open_sync_settings" if counts.sync_pending else "none"),
            OperationsComponent("queue", "warning" if counts.queue_pending or counts.queue_failed else "ready", "QUEUE_ATTENTION_REQUIRED" if counts.queue_pending or counts.queue_failed else "QUEUE_READY", counts.queue_pending, "refresh_status" if counts.queue_pending or counts.queue_failed else "none"),
        )
        overall = "error" if any(item.status == "error" for item in components) else "warning" if any(item.status == "warning" for item in components) else "ready"
        return OperationsStatusView(context.workspace_id, overall, checked_at, components)
