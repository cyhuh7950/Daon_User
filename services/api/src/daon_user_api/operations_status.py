"""Safe workspace operations status projection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
OBSERVATION_IDS = (
    "document_pending", "document_retry_wait", "document_leased", "document_dead_letter",
    "object_pending", "object_retry_wait", "object_leased", "object_dead_letter", "object_oldest_pending_seconds",
    "source_waiting_model", "index_records_current", "source_storage_bytes", "reported_prompt_tokens",
    "reported_completion_tokens", "reported_total_tokens", "attempts_without_reported_usage",
)
OBSERVATION_STATES = {"measured": "METRIC_RECORDED", "partial": "METRIC_PARTIAL",
    "not_collected": "METRIC_NOT_COLLECTED", "unavailable": "METRIC_UNAVAILABLE"}


def observation_scope(metric_id):
    if metric_id not in OBSERVATION_IDS: raise ValueError("OPERATIONS_OBSERVATION_INVALID")
    unit = "seconds" if metric_id == "object_oldest_pending_seconds" else "bytes" if metric_id == "source_storage_bytes" else "tokens" if metric_id.startswith("reported_") else "count"
    coverage = "workspace_operational_counts" if OBSERVATION_IDS.index(metric_id) < 9 else "actor_accessible_records"
    return unit, coverage


@dataclass(frozen=True, slots=True)
class OperationsObservation:
    metric_id: str
    state: str
    value: int | None
    unit: str
    observed_at: str | None
    coverage: str
    safe_code: str

    def __post_init__(self):
        if (not isinstance(self.state, str) or (self.unit, self.coverage) != observation_scope(self.metric_id)
                or self.state not in OBSERVATION_STATES or self.safe_code != OBSERVATION_STATES[self.state]):
            raise ValueError("OPERATIONS_OBSERVATION_INVALID")
        if self.state in {"not_collected", "unavailable"}:
            if self.value is not None or self.observed_at is not None: raise ValueError("OPERATIONS_OBSERVATION_INVALID")
        else:
            if type(self.value) is not int or not 0 <= self.value <= 9007199254740991:
                raise ValueError("OPERATIONS_OBSERVATION_INVALID")
            try:
                if (not isinstance(self.observed_at, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z", self.observed_at) is None
                        or datetime.fromisoformat(self.observed_at.replace("Z", "+00:00")).utcoffset() != timezone.utc.utcoffset(None)):
                    raise ValueError()
            except (ValueError, TypeError): raise ValueError("OPERATIONS_OBSERVATION_INVALID") from None


def unknown_observations(state="not_collected", ids=OBSERVATION_IDS):
    return tuple(OperationsObservation(metric_id, state, None, observation_scope(metric_id)[0], None,
        observation_scope(metric_id)[1], OBSERVATION_STATES[state]) for metric_id in ids)


def validate_observations(rows):
    if (not isinstance(rows, tuple) or len(rows) != 16 or not all(isinstance(row, OperationsObservation) for row in rows)
            or tuple(row.metric_id for row in rows) != OBSERVATION_IDS): raise ValueError("OPERATIONS_OBSERVATIONS_INVALID")
    for row in rows: row.__post_init__()
    return rows


@dataclass(frozen=True, slots=True)
class OperationsStatusContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value) for value in (self.tenant_id, self.workspace_id, self.actor_id)):
            raise ValueError("OPERATIONS_STATUS_CONTEXT_INVALID")
        if any(value is not None and (not isinstance(value, str) or not _SAFE_ID.fullmatch(value))
                for value in (self.trace_id, self.policy_version)):
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
    observations: tuple[OperationsObservation, ...] = ()


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
        reader = getattr(self._repository, "read_observations", None)
        try:
            observations = validate_observations(reader(context)) if reader is not None else unknown_observations()
        except ValueError:
            observations = unknown_observations("unavailable")
        return OperationsStatusView(context.workspace_id, overall, checked_at, components, observations)


class SystemOperationsStatusService:
    """현재 system-admin을 전후 검증한 runtime만 소비. Workspace 집계/Provider 호출0."""
    def __init__(self,repository):self._repository=repository

    def read(self,*,api_ready,database_ready,object_storage_ready,checked_at):
        from .operations_telemetry import KINDS,project_worker,project_resources,ReceivedWorkerSample,_utc
        checked_now=_utc(datetime.fromisoformat(checked_at.replace("Z","+00:00")));now=checked_now
        state="not_collected";stored=()
        if self._repository is not None:
            try:
                stored,now=self._repository.read_system_observations()
                _utc(now)
                if not isinstance(stored,tuple) or len(stored)>6 or any(type(row) is not ReceivedWorkerSample for row in stored):raise ValueError()
                for kind in KINDS:
                    rows=[r for r in stored if r.sample.worker_kind==kind]
                    if len(rows)>2 or len({r.sample.boot_id for r in rows})!=len(rows) or len({r.sample.process_started_at for r in rows})!=len(rows):raise ValueError()
                if any(r.sample.worker_kind not in KINDS for r in stored):raise ValueError()
                # 아래 projection에서도 시각/형식 검증 후에만 안전한 필드를 복사한다.
                for row in stored:project_worker(row,now=now)
            except Exception:stored=();now=checked_now;state="unavailable"
        workers=[];resources=[]
        for kind in KINDS:
            rows=sorted((r for r in stored if r.sample.worker_kind==kind),key=lambda r:(r.sample.process_started_at,r.received_at),reverse=True)
            row=rows[0] if rows else None
            worker=project_worker(row,now=now,worker_kind=kind)
            values=project_resources(row,now=now,worker_kind=kind)
            if row is None and state=="unavailable":
                for value in (worker,*values):value.update(state="unavailable",safe_code="TELEMETRY_UNAVAILABLE")
            worker.pop("coverage");workers.append(worker)
            for value in values:
                if value["value"] is not None and value["value"]>9007199254740991:
                    value.update(state="unavailable",value=None,observed_at=None,received_at=None,safe_code="TELEMETRY_UNAVAILABLE")
                value.pop("coverage");resources.append(value)
        deployment=dict(state="not_collected",observed_at=None,received_at=None,safe_code="TELEMETRY_NOT_COLLECTED",
            operation=None,code_revision=None,image_digest=None,previous_image_digest=None,result=None)
        health=[]
        for component,ready in (("api",api_ready),("database",database_ready),("object_storage",object_storage_ready)):
            health.append(dict(component_id=component,state="ready" if ready is True else "unavailable",checked_at=checked_at,
                safe_code=component.upper()+("_READY" if ready is True else "_UNAVAILABLE")))
        return dict(schema_version=1,checked_at=checked_at,health=health,worker_samples=workers,resource_samples=resources,
            deployment_summary=deployment,unimplemented=[dict(producer_id="deployment_receipt_writer",
                safe_code="PRODUCER_NOT_IMPLEMENTED",impact_code="DEPLOYMENT_HISTORY_UNKNOWN")])
