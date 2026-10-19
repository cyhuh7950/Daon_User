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
EVENT_IDS = ("manual_reprocess_pending", "readiness_reprocess_pending", "retry_wait", "backoff_wait",
    "step_up_denied_24h", "step_up_expired_24h", "access_blocked_24h", "access_masked_24h", "egress_denied_24h")
READINESS_EVENT_IDS = ("waiting_text_generation", "waiting_image_understanding", "waiting_document_parsing",
    "waiting_speech_to_text", "waiting_role_unknown", "retry_suppressed_contexts_24h")
UNIMPLEMENTED_PRODUCERS = {
    "waiting_role_producer": "ROLE_READINESS_UNKNOWN", "readiness_event_producer": "AUTOMATIC_REPROCESS_UNOBSERVED",
    "retry_suppression_receipt": "DEDUP_NOT_OBSERVED", "node_capacity_producer": "CAPACITY_UNKNOWN",
    "provider_capacity_reporter": "CAPACITY_UNKNOWN", "deployment_receipt_writer": "DEPLOYMENT_HISTORY_UNKNOWN",
    "cost_receipt_writer": "COST_UNKNOWN",
}
TELEMETRY_FIELDS = ("worker_samples", "resource_samples", "event_counts", "cost_summary", "deployment_summary", "unimplemented")


def unknown_telemetry(state="not_collected", *, include_readiness=False,include_capacity=False):
    from .operations_telemetry import KINDS, project_worker, project_resources
    if state not in {"not_collected", "unavailable"} or type(include_readiness) is not bool or type(include_capacity) is not bool: raise ValueError("OPERATIONS_TELEMETRY_INVALID")
    now=datetime.now(timezone.utc)
    common=dict(state=state,observed_at=None,received_at=None,coverage="actor_accessible_records",
        safe_code="TELEMETRY_"+state.upper())
    workers=[];resources=[]
    for kind in KINDS:
        worker=project_worker(None,now=now,worker_kind=kind);worker.update(state=state,safe_code=common["safe_code"])
        workers.append(worker)
        for row in project_resources(None,now=now,worker_kind=kind):
            row.update(state=state,safe_code=common["safe_code"]);resources.append(row)
    if include_capacity:
        from .operations_host_capacity import project_host_resources
        for row in project_host_resources(None,now=now):
            row.update(state=state,safe_code=common['safe_code']);resources.append(row)
    return dict(worker_samples=workers,resource_samples=resources,
        event_counts=[dict(common,metric_id=key,value=None,unit="count",window_start=None,window_end=None)
                      for key in EVENT_IDS + (READINESS_EVENT_IDS if include_readiness else ())],
        cost_summary=dict(common,known_amounts=[],unknown_attempt_count=None,unconfirmed_run_count=None,coverage_start=None,coverage_end=None),
        deployment_summary=dict(common,operation=None,code_revision=None,image_digest=None,previous_image_digest=None,result=None),
        unimplemented=[dict(producer_id=key,safe_code="PRODUCER_NOT_IMPLEMENTED",impact_code=value) for key,value in UNIMPLEMENTED_PRODUCERS.items()])


def project_deployment_receipt(row):
    from .operations_telemetry import DeploymentReceipt,_utc
    result=unknown_telemetry()["deployment_summary"]
    if row is None:return result
    names=set(DeploymentReceipt.__dataclass_fields__)
    if type(row) is not dict or set(row)!=names|{"received_at","receipt_digest"}:raise ValueError("DEPLOYMENT_RECEIPT_INVALID")
    receipt=DeploymentReceipt(**{key:row[key] for key in names})
    if row["receipt_digest"]!=receipt.digest():raise ValueError("DEPLOYMENT_RECEIPT_INVALID")
    received=_utc(row["received_at"])
    return dict(state="measured",observed_at=receipt.observed_at.isoformat().replace("+00:00","Z"),
        received_at=received.isoformat().replace("+00:00","Z"),coverage="actor_accessible_records",safe_code="TELEMETRY_MEASURED",
        operation=receipt.operation,code_revision=receipt.code_revision,image_digest=receipt.image_digest,
        previous_image_digest=receipt.previous_image_digest,result=receipt.state)


def validate_telemetry(data, *, include_readiness=False,include_capacity=False):
    """서버 경계에서도 추가 필드·내부 식별자와 불확정을 성공 값으로 반환하지 않는다."""
    from decimal import Decimal
    def require(condition):
        if not condition:raise ValueError("OPERATIONS_TELEMETRY_INVALID")
    require(type(include_readiness) is bool and type(include_capacity) is bool)
    event_ids = EVENT_IDS + (READINESS_EVENT_IDS if include_readiness else ())
    def stamp(value):
        require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z",value) is not None)
        return datetime.fromisoformat(value.replace("Z","+00:00"))
    def integer(value,nullable=False):require((nullable and value is None) or (type(value) is int and 0<=value<=9007199254740991))
    common={"state","observed_at","received_at","coverage","safe_code"}
    def base(row,extra,coverage,states):
        require(type(row) is dict and set(row)==common|set(extra) and row["state"] in states
            and row["safe_code"]=="TELEMETRY_"+row["state"].upper() and row["coverage"]==coverage)
        if row["state"] in {"not_collected","not_reported","unavailable"}:
            require(row["observed_at"] is None and row["received_at"] is None)
        else:stamp(row["observed_at"]);stamp(row["received_at"])
    require(type(data) is dict and set(data)==set(TELEMETRY_FIELDS))
    require(type(data["worker_samples"]) in {list,tuple} and len(data["worker_samples"])==3)
    for kind,row in zip(("document","studio","object"),data["worker_samples"]):
        base(row,{"worker_kind","process_state","poll_started_at","poll_finished_at","active_started_at"},"workspace_operational_counts",{"fresh","stale","not_collected","unavailable"})
        require(row["worker_kind"]==kind and row["process_state"] in {"alive","processing","poll_stale","stopped","unknown"})
        for key in ("poll_started_at","poll_finished_at","active_started_at"):
            if row[key] is not None:stamp(row[key])
        if row["state"] in {"not_collected","unavailable"}:require(row["process_state"]=="unknown" and all(row[k] is None for k in ("poll_started_at","poll_finished_at","active_started_at")))
        if row["process_state"]=="processing":require(row["active_started_at"] is not None)
    require(type(data["resource_samples"]) in {list,tuple} and len(data["resource_samples"])==(16 if include_capacity else 12))
    resource_ids=("cpu_quota_millicores","memory_limit_bytes","memory_current_bytes","runtime_available_bytes")
    for (kind,key),row in zip(((k,m) for k in ("document","studio","object") for m in resource_ids),data["resource_samples"]):
        base(row,{"worker_kind","metric_id","value","unit"},"workspace_operational_counts",{"fresh","stale","not_collected","unavailable"})
        require((row["worker_kind"],row["metric_id"],row["unit"])==(kind,key,"millicores" if key==resource_ids[0] else "bytes"))
        if row["state"] in {"not_collected","unavailable"}:require(row["value"] is None)
        else:integer(row["value"])
    if include_capacity:
        from .operations_host_capacity import HOST_METRICS
        for (key,unit),row in zip(HOST_METRICS,data['resource_samples'][12:]):
            base(row,{'worker_kind','metric_id','value','unit'},'server_host_observation',{'fresh','stale','not_collected','unavailable'})
            require((row['worker_kind'],row['metric_id'],row['unit'])==('server_host',key,unit))
            if row['state'] in {'not_collected','unavailable'}:require(row['value'] is None)
            else:
                integer(row['value'])
                if key=='cpu_usage_basis_points':require(row['value']<=10000)
                if key=='cpu_logical_millicores':require(1000<=row['value']<=32768000 and row['value']%1000==0)
                if key=='memory_total_bytes':require(row['value']>0)
        total,available=(row['value'] for row in data['resource_samples'][14:16])
        if total is not None and available is not None:require(available<=total)
    require(type(data["event_counts"]) in {list,tuple} and len(data["event_counts"])==len(event_ids))
    for number,(key,row) in enumerate(zip(event_ids,data["event_counts"])):
        base(row,{"metric_id","value","unit","window_start","window_end"},"actor_accessible_records",{"measured","partial","not_collected","unavailable"})
        require(row["metric_id"]==key and row["unit"]=="count")
        if row["state"] in {"not_collected","unavailable"}:require(row["value"] is None and row["window_start"] is None and row["window_end"] is None)
        else:
            integer(row["value"])
            if number<4 or 9<=number<14:require(row["window_start"] is None and row["window_end"] is None)
            else:require(row["window_end"]==row["observed_at"] and (stamp(row["window_end"])-stamp(row["window_start"])).total_seconds()==86400)
    row=data["cost_summary"]
    base(row,{"known_amounts","unknown_attempt_count","unconfirmed_run_count","coverage_start","coverage_end"},"actor_accessible_records",{"measured","partial","not_collected","not_reported","unavailable"})
    require(type(row["known_amounts"]) in {list,tuple});currencies=[]
    for amount in row["known_amounts"]:
        require(type(amount) is dict and set(amount)=={"currency","amount"} and type(amount["currency"]) is str and re.fullmatch(r"[A-Z]{3}",amount["currency"]) is not None
            and type(amount["amount"]) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,9})?",amount["amount"]) is not None and Decimal(amount["amount"])>=0)
        currencies.append(amount["currency"])
    require(currencies==sorted(set(currencies)))
    for key in ("unknown_attempt_count","unconfirmed_run_count"):integer(row[key],nullable=True)
    for key in ("coverage_start","coverage_end"):
        if row[key] is not None:stamp(row[key])
    if row["state"] in {"not_collected","not_reported","unavailable"}:require(not currencies and all(row[key] is None for key in ("unknown_attempt_count","unconfirmed_run_count","coverage_start","coverage_end")))
    else:
        integer(row["unknown_attempt_count"]);integer(row["unconfirmed_run_count"])
    row=data["deployment_summary"]
    base(row,{"operation","code_revision","image_digest","previous_image_digest","result"},"actor_accessible_records",{"measured","not_collected","unavailable"})
    if row["state"] in {"not_collected","unavailable"}:require(all(row[k] is None for k in ("operation","code_revision","image_digest","previous_image_digest","result")))
    else:
        require(row["operation"] in {"deploy","rollback"} and row["result"] in {"succeeded","failed","uncertain"})
        for key,pattern in (("code_revision",r"[0-9a-f]{40}"),("image_digest",r"sha256:[0-9a-f]{64}"),("previous_image_digest",r"sha256:[0-9a-f]{64}")):
            if row[key] is not None:require(type(row[key]) is str and re.fullmatch(pattern,row[key]) is not None)
        if row["result"]=="succeeded":require(row["code_revision"] is not None and row["image_digest"] is not None and (row["operation"]!="rollback" or row["previous_image_digest"] is not None))
    require(type(data["unimplemented"]) in {list,tuple});ids=[]
    for row in data["unimplemented"]:
        require(type(row) is dict and set(row)=={"producer_id","safe_code","impact_code"} and row["producer_id"] in UNIMPLEMENTED_PRODUCERS
            and row["safe_code"]=="PRODUCER_NOT_IMPLEMENTED" and row["impact_code"]==UNIMPLEMENTED_PRODUCERS[row["producer_id"]])
        ids.append(row["producer_id"])
    require(ids==[key for key in UNIMPLEMENTED_PRODUCERS if key in ids])
    return data


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
    scope: str = "self"
    include_readiness: bool = False
    include_capacity: bool = False

    def __post_init__(self) -> None:
        if (type(self.scope) is not str or self.scope not in {"self","workspace_visible"}
            or type(self.include_readiness) is not bool or type(self.include_capacity) is not bool):
            raise ValueError("OPERATIONS_STATUS_CONTEXT_INVALID")
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
    worker_samples: tuple[dict, ...] = ()
    resource_samples: tuple[dict, ...] = ()
    event_counts: tuple[dict, ...] = ()
    cost_summary: dict | None = None
    deployment_summary: dict | None = None
    unimplemented: tuple[dict, ...] = ()


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
        telemetry_reader=getattr(self._repository,"read_workspace_telemetry",None)
        telemetry=unknown_telemetry(include_readiness=context.include_readiness,include_capacity=context.include_capacity) if telemetry_reader is None else telemetry_reader(context)
        try:telemetry=validate_telemetry(telemetry,include_readiness=context.include_readiness,include_capacity=context.include_capacity)
        except (ValueError,TypeError,KeyError,OverflowError):telemetry=unknown_telemetry("unavailable",include_readiness=context.include_readiness,include_capacity=context.include_capacity)
        return OperationsStatusView(context.workspace_id, overall, checked_at, components, observations,
            tuple(telemetry["worker_samples"]),tuple(telemetry["resource_samples"]),tuple(telemetry["event_counts"]),
            telemetry["cost_summary"],telemetry["deployment_summary"],tuple(telemetry["unimplemented"]))


class SystemOperationsStatusService:
    """현재 system-admin을 전후 검증한 runtime만 소비. Workspace 집계/Provider 호출0."""
    def __init__(self,repository):self._repository=repository

    def read(self,*,api_ready,database_ready,object_storage_ready,checked_at,include_capacity=False):
        if type(include_capacity) is not bool:raise ValueError('OPERATIONS_TELEMETRY_INVALID')
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
        if include_capacity:
            from .operations_host_capacity import project_host_resources
            host_reader=getattr(self._repository,'read_host_capacity',None)
            failed=False
            try:host=host_reader() if host_reader is not None else None
            except Exception:host=None;failed=True
            try:host_rows=project_host_resources(host,now=now)
            except (ValueError,TypeError):
                host_rows=project_host_resources(None,now=now);failed=True
            for row in host_rows:
                if failed:row.update(state='unavailable',safe_code='TELEMETRY_UNAVAILABLE',value=None,observed_at=None,received_at=None)
                row.pop('coverage');resources.append(row)
        deployment=dict(state="not_collected",observed_at=None,received_at=None,safe_code="TELEMETRY_NOT_COLLECTED",
            operation=None,code_revision=None,image_digest=None,previous_image_digest=None,result=None)
        deployment_reader=getattr(self._repository,"read_deployment_receipt",None)
        if deployment_reader is not None:
            try:
                deployment=project_deployment_receipt(deployment_reader());deployment.pop("coverage")
            except Exception:
                deployment.update(state="unavailable",observed_at=None,received_at=None,safe_code="TELEMETRY_UNAVAILABLE")
        health=[]
        for component,ready in (("api",api_ready),("database",database_ready),("object_storage",object_storage_ready)):
            health.append(dict(component_id=component,state="ready" if ready is True else "unavailable",checked_at=checked_at,
                safe_code=component.upper()+("_READY" if ready is True else "_UNAVAILABLE")))
        return dict(schema_version=1,checked_at=checked_at,health=health,worker_samples=workers,resource_samples=resources,
            deployment_summary=deployment,unimplemented=[] if deployment_reader is not None else [dict(producer_id="deployment_receipt_writer",
                safe_code="PRODUCER_NOT_IMPLEMENTED",impact_code="DEPLOYMENT_HISTORY_UNKNOWN")])
