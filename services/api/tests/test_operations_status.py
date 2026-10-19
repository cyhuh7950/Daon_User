from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
import pytest

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.operations_status import (
    OperationsCounts,
    OperationsStatusContext,
    OperationsStatusService,
)
from daon_user_api.operations_status_postgres import PostgresOperationsStatusRepository


def test_m2_workspace_missing_telemetry_is_exact_unknown_not_zero():
    context=OperationsStatusContext("tenant-1","workspace-1","actor-1",scope="self")
    service=OperationsStatusService(SimpleNamespace(read_counts=lambda context:OperationsCounts(0,0,0)))
    view=service.read(context,active_providers=0,selected_deployments=0,api_ready=True,
        database_ready=True,object_storage_ready=True,checked_at="2026-10-19T00:00:00Z")
    assert len(view.worker_samples)==3 and len(view.resource_samples)==12
    assert [r["metric_id"] for r in view.event_counts]==[
        "manual_reprocess_pending","readiness_reprocess_pending","retry_wait","backoff_wait",
        "step_up_denied_24h","step_up_expired_24h","access_blocked_24h","access_masked_24h","egress_denied_24h"]
    assert all(r["value"] is None and r["state"]=="not_collected" for r in view.event_counts)
    assert view.cost_summary["known_amounts"]==[] and view.cost_summary["unknown_attempt_count"] is None
    assert view.deployment_summary["result"] is None
    assert "node_capacity_producer" in [r["producer_id"] for r in view.unimplemented]
    with pytest.raises(ValueError):
        OperationsStatusContext("tenant-1","workspace-1","actor-1",scope="all")


def test_m2_workspace_telemetry_access_failure_is_not_hidden_as_unknown():
    from daon_user_api.authorization import AuthorizationError
    def denied(context):raise AuthorizationError("CURRENT_ACCESS_DENIED",403)
    repository=SimpleNamespace(read_counts=lambda context:OperationsCounts(0,0,0),read_workspace_telemetry=denied)
    with pytest.raises(AuthorizationError) as error:
        OperationsStatusService(repository).read(OperationsStatusContext("tenant-1","workspace-1","actor-1"),
            active_providers=0,selected_deployments=0,api_ready=True,database_ready=True,
            object_storage_ready=True,checked_at="2026-10-19T00:00:00Z")
    assert error.value.http_status==403


def test_m2_workspace_malformed_telemetry_does_not_leak_or_change_existing_health():
    repository=SimpleNamespace(read_counts=lambda context:OperationsCounts(0,0,0),
        read_workspace_telemetry=lambda context:{"internal_url":"PRIVATE_TELEMETRY_ENDPOINT"})
    view=OperationsStatusService(repository).read(OperationsStatusContext("tenant-1","workspace-1","actor-1"),
        active_providers=1,selected_deployments=1,api_ready=True,database_ready=True,
        object_storage_ready=True,checked_at="2026-10-19T00:00:00Z")
    assert view.overall_status=="ready"
    assert all(row["state"]=="unavailable" for row in view.event_counts)
    assert "PRIVATE" not in str(view)


def test_m2_pending_events_count_only_current_visible_actor_and_real_backoff():
    from datetime import datetime,timedelta,timezone
    from daon_user_api import operations_status_postgres as pg
    now=datetime(2026,10,19,tzinfo=timezone.utc);calls=[]
    sources=[("notebook-a","actor-1","version-a","object-a"),
        ("notebook-b","actor-2","version-b","object-b"),("notebook-c","actor-1","version-hidden","object-c")]
    docs=[("job-a","actor-1","version-a","pending","manual_request",now),
        ("job-b","actor-2","version-a","pending","manual_request",now),
        ("job-c","actor-1","version-hidden","pending","manual_request",now)]
    class Connection:
        def execute(self,query,params=()):
            calls.append(query)
            if query=="SELECT clock_timestamp()":rows=[(now,)]
            elif "FROM notebooks n JOIN notebook_bindings" in query:rows=sources
            elif "FROM document_processing_jobs j" in query:rows=docs
            elif "FROM durable_jobs j" in query:rows=[("object-job-a","actor-1","object-a","retry_wait",now+timedelta(seconds=5))]
            elif "FROM studio_generation_jobs" in query:rows=[("studio-job-a","actor-1","notebook-a",["version-a"],"queued",now+timedelta(seconds=10))]
            else:raise AssertionError("UNEXPECTED_EVENT_SQL")
            return SimpleNamespace(fetchall=lambda:list(rows),fetchone=lambda:rows[0] if rows else None)
    class Store:
        @contextmanager
        def _transaction(self,context):yield Connection()
    repository=pg.PostgresOperationsStatusRepository(Store())
    context=OperationsStatusContext("tenant-1","workspace-1","actor-1","trace-1","policy-1")
    assert hasattr(repository,"read_pending_events"),"actual producer pending reader missing"
    with patch.object(repository,"_authorize_telemetry"), \
            patch.object(pg,"_source_access_allowed",side_effect=lambda c,ctx,v:v!="version-hidden"), \
            patch.object(pg,"_special_source_access_allowed",return_value=True):
        events=repository.read_pending_events(context)
        assert [(r["metric_id"],r["value"],r["state"]) for r in events]==[
            ("manual_reprocess_pending",1,"measured"),("readiness_reprocess_pending",None,"not_collected"),
            ("retry_wait",1,"measured"),("backoff_wait",2,"measured")]
        visible=repository.read_pending_events(__import__("dataclasses").replace(context,scope="workspace_visible"))
        assert visible[0]["value"]==2
    assert not any(q.lstrip().upper().startswith(("INSERT","UPDATE","DELETE")) for q in calls)


def test_m2_workspace_collector_separates_real_worker_values_from_missing_groups():
    from datetime import datetime,timezone
    from daon_user_api.operations_telemetry import WorkerSample,ReceivedWorkerSample
    from daon_user_api.operations_status import validate_telemetry
    now=datetime(2026,10,19,tzinfo=timezone.utc)
    class Store:
        @contextmanager
        def _transaction(self,context):yield SimpleNamespace()
    repository=PostgresOperationsStatusRepository(Store())
    assert hasattr(repository,"read_workspace_telemetry"),"workspace telemetry consumer missing"
    row=ReceivedWorkerSample(WorkerSample("document-worker-1","PRIVATE_BOOT_IDENTIFIER","document",now,1,now,
        cpu_quota_millicores=1000),now)
    context=OperationsStatusContext("tenant-1","workspace-1","actor-1","trace-1","policy-1")
    pending=[repository._event_metric(key,2,now) for key in ("manual_reprocess_pending","retry_wait","backoff_wait")]
    from daon_user_api.operations_status import unknown_telemetry
    pending.insert(1,unknown_telemetry()["event_counts"][1])
    with patch.object(repository,"_authorize_telemetry"), \
            patch.object(repository,"read_system_observations",return_value=((row,),now)), \
            patch.object(repository,"read_deployment_receipt",return_value=None), \
            patch.object(repository,"read_cost_summary",return_value=unknown_telemetry()["cost_summary"]), \
            patch.object(repository,"read_pending_events",return_value=tuple(pending)), \
            patch.object(repository,"read_decision_events",return_value=tuple(unknown_telemetry()["event_counts"][4:9])):
        data=validate_telemetry(repository.read_workspace_telemetry(context))
    assert data["worker_samples"][0]["state"]=="fresh"
    assert data["resource_samples"][0]["value"]==1000
    assert data["resource_samples"][1]["value"] is None
    assert data["event_counts"][0]["value"]==2
    assert data["event_counts"][4]["value"] is None
    assert "PRIVATE_BOOT" not in str(data)


def test_m2_measured_cost_never_accepts_missing_collection_counts():
    from daon_user_api.operations_status import unknown_telemetry,validate_telemetry
    data=unknown_telemetry()
    data["cost_summary"].update(state="measured",observed_at="2026-10-19T00:00:00Z",
        received_at="2026-10-19T00:00:00Z",safe_code="TELEMETRY_MEASURED")
    with pytest.raises(ValueError):validate_telemetry(data)


def test_m2_existing_decision_events_deduplicate_and_exclude_unattributed_or_inaccessible_records():
    from datetime import datetime,timezone
    from daon_user_api import operations_status_postgres as pg
    now=datetime(2026,10,19,tzinfo=timezone.utc)
    class Connection:
        def execute(self,query,params=()):
            if query=="SELECT clock_timestamp()":rows=[(now,)]
            elif "FROM notebooks n JOIN notebook_bindings" in query:rows=[("notebook-a","actor-1","version-a","object-a")]
            elif "FROM identity_auth_access_decisions d" in query:rows=[("decision-a","actor-1","access_blocked",'["version-a"]'),("decision-a","actor-1","access_blocked",'["version-a"]'),("decision-b","actor-1","partially_redacted",'["version-denied"]'),("decision-c","actor-2","access_blocked",'["version-a"]')]
            elif "FROM egress_decisions e" in query:rows=[("egress-a","actor-1","version-a","run-a",now),("egress-a","actor-1","version-a","run-a",now),("egress-source-free","actor-1",None,"run-b",now),("egress-foreign","actor-2","version-a","run-c",now)]
            elif "FROM security_audit_events" in query:rows=[]
            elif "FROM audit_events" in query:rows=[]
            else:raise AssertionError("UNEXPECTED_DECISION_SQL")
            return SimpleNamespace(fetchall=lambda:list(rows),fetchone=lambda:rows[0] if rows else None)
    class Store:
        @contextmanager
        def _transaction(self,context):yield Connection()
    repository=pg.PostgresOperationsStatusRepository(Store())
    assert hasattr(repository,"read_decision_events"),"existing decision producer consumer missing"
    context=OperationsStatusContext("tenant-1","workspace-1","actor-1","trace-1","policy-1")
    with patch.object(repository,"_authorize_telemetry"),patch.object(pg,"_source_access_allowed",return_value=True),patch.object(pg,"_special_source_access_allowed",return_value=True):
        rows=repository.read_decision_events(context)
    assert all(r["value"] is None for r in rows[:2])
    assert [(r["metric_id"],r["value"],r["state"]) for r in rows[2:]]==[
        ("access_blocked_24h",1,"partial"),("access_masked_24h",0,"partial"),("egress_denied_24h",1,"partial")]
    assert len({r["window_end"] for r in rows[2:]})==1
    assert all(r["window_start"]=="2026-10-18T00:00:00Z" for r in rows[2:])
    assert "source-free" not in str(rows) and "actor-2" not in str(rows)


def test_m2_step_up_events_use_explicit_scope_current_actor_and_one_database_window():
    from datetime import datetime,timezone
    from daon_user_api import operations_status_postgres as pg
    now=datetime(2026,10,19,tzinfo=timezone.utc);queries=[]
    scope=dict(version=1,workspace_id="workspace-1",scope_basis="workspace_grant",resource_type="workspace",
        resource_id="workspace-1",notebook_id=None,source_version_ids=[])
    events=[("event-a","actor-1","identity.step_up.binding_denied",{"operations_scope":scope}),
        ("event-a","actor-1","identity.step_up.binding_denied",{"operations_scope":scope}),
        ("event-b","actor-1","identity.step_up.expired",{"operations_scope":scope}),
        ("event-c","actor-2","identity.step_up.reuse_denied",{"operations_scope":scope}),
        ("event-legacy","actor-1","identity.step_up.binding_denied",{"reason_code":"STEP_UP_BINDING_DENIED"}),
        ("event-forged","actor-1","identity.step_up.binding_denied",{"operations_scope":{**scope,"extra":"PRIVATE"}}),
        ("event-other","actor-1","identity.step_up.binding_denied",{"operations_scope":{**scope,"workspace_id":"other"}}),
        ("event-source","actor-1","identity.step_up.binding_denied",{"operations_scope":{**scope,"scope_basis":"resource_access",
            "resource_type":"source","resource_id":"source-denied","notebook_id":"notebook-a","source_version_ids":["version-denied"]}})]
    class Connection:
        def execute(self,query,params=()):
            queries.append((query,params))
            if query=="SELECT clock_timestamp()":rows=[(now,)]
            elif "FROM security_audit_events" in query:rows=events
            elif "FROM identity_auth_access_decisions" in query or "FROM egress_decisions" in query or "FROM audit_events" in query:rows=[]
            else:raise AssertionError("UNEXPECTED_DECISION_SQL")
            return SimpleNamespace(fetchall=lambda:list(rows),fetchone=lambda:rows[0] if rows else None)
    class Store:
        @contextmanager
        def _transaction(self,context):yield Connection()
    repo=pg.PostgresOperationsStatusRepository(Store())
    context=OperationsStatusContext("tenant-1","workspace-1","actor-1","trace-1","policy-1")
    with patch.object(repo,"_authorize_telemetry"),patch.object(repo,"_visible_sources",return_value={}):
        rows=repo.read_decision_events(context)
    assert [(r["metric_id"],r["value"],r["state"]) for r in rows[:2]]==[
        ("step_up_denied_24h",1,"partial"),("step_up_expired_24h",1,"partial")]
    assert all(r["value"] is None for r in rows[2:])
    assert len([q for q,p in queries if q=="SELECT clock_timestamp()"])==1
    assert rows[0]["window_end"]==rows[1]["window_end"]=="2026-10-19T00:00:00Z"
    assert "PRIVATE" not in str(rows) and "event-" not in str(rows)
    with patch.object(repo,"_authorize_telemetry"),patch.object(repo,"_visible_sources",return_value={}):
        visible=repo.read_decision_events(__import__("dataclasses").replace(context,scope="workspace_visible"))
    assert visible[0]["value"]==2


def test_m2_cost_projection_separates_currencies_missing_attempts_failed_runs_and_private_fields():
    from datetime import datetime,timezone
    from decimal import Decimal
    from daon_user_api import operations_status_postgres as pg
    now=datetime(2026,10,19,tzinfo=timezone.utc)
    runs=[("run-a","actor-1","notebook-a",["version-a"],"completed",now,"attempt-a"),
        ("run-a","actor-1","notebook-a",["version-a"],"completed",now,"attempt-a"),
        ("run-b","actor-1","notebook-a",["version-a"],"completed",now,"attempt-b"),
        ("run-c","actor-1","notebook-a",["version-a"],"completed",now,"attempt-c"),
        ("run-d","actor-1","notebook-a",["version-a"],"failed",now,None),
        ("run-hidden","actor-1","notebook-a",["version-denied"],"completed",now,"attempt-hidden"),
        ("run-foreign","actor-2","notebook-a",["version-a"],"completed",now,"attempt-foreign")]
    def receipt(run,attempt,amount,currency,state="known"):
        return dict(run_id=run,actor_id="actor-1",model_attempt_id=attempt,state=state,amount=amount,currency=currency,observed_at=now,received_at=now)
    receipts=(receipt("run-a","attempt-a",Decimal("0.015"),"USD"),receipt("run-b","attempt-b",Decimal("20"),"KRW"),
        receipt("run-d",None,None,None,"uncertain"))
    class Connection:
        def execute(self,query,params=()):
            if query=="SELECT clock_timestamp()":rows=[(now,)]
            elif "operations-summary:runs" in query:rows=runs
            else:raise AssertionError("UNEXPECTED_COST_SQL")
            return SimpleNamespace(fetchall=lambda:list(rows),fetchone=lambda:rows[0] if rows else None)
    class Store:
        @contextmanager
        def _transaction(self,context):yield Connection()
    repo=pg.PostgresOperationsStatusRepository(Store())
    assert hasattr(repo,"read_cost_summary"),"actual receipt projection missing"
    called=[]
    def read(context,ids):called.append(ids);return receipts
    with patch.object(repo,"_authorize_telemetry"),patch.object(repo,"_visible_sources",return_value={"version-a":{("notebook-a","actor-1","object-a")}}), \
            patch.object(repo,"_telemetry_reader",return_value=SimpleNamespace(read_cost_receipts=read)):
        result=repo.read_cost_summary(OperationsStatusContext("tenant-1","workspace-1","actor-1","trace-1","policy-1"))
    assert called==[("run-a","run-b","run-c","run-d")]
    assert result["known_amounts"]==[{"currency":"KRW","amount":"20"},{"currency":"USD","amount":"0.015"}]
    assert result["unknown_attempt_count"]==1 and result["unconfirmed_run_count"]==1 and result["state"]=="partial"
    assert "run-" not in str(result) and "actor-" not in str(result) and "attempt-" not in str(result)


def test_m2_deployment_projection_requires_real_digest_and_does_not_expose_operator_identifiers():
    from datetime import datetime,timezone
    from daon_user_api.operations_telemetry import DeploymentReceipt
    from daon_user_api import operations_status as domain
    receipt=DeploymentReceipt("PRIVATE-OPERATION","deploy","a"*40,"sha256:"+"b"*64,"sha256:"+"c"*64,"succeeded",
        datetime(2026,10,19,tzinfo=timezone.utc),"DEPLOYMENT_SUCCEEDED")
    row={name:getattr(receipt,name) for name in receipt.__dataclass_fields__}
    row.update(received_at=receipt.observed_at,receipt_digest=receipt.digest())
    repository=SimpleNamespace(read_system_observations=lambda:((),receipt.observed_at),read_deployment_receipt=lambda:row)
    data=domain.SystemOperationsStatusService(repository).read(api_ready=True,database_ready=True,object_storage_ready=True,checked_at="2026-10-19T00:00:00Z")
    assert data["deployment_summary"]["result"]=="succeeded" and data["deployment_summary"]["state"]=="measured"
    assert "PRIVATE" not in str(data) and "receipt_digest" not in str(data)
    assert data["unimplemented"]==[]
    repository.read_deployment_receipt=lambda:{**row,"receipt_digest":"d"*64}
    rejected=domain.SystemOperationsStatusService(repository).read(api_ready=True,database_ready=True,object_storage_ready=True,checked_at="2026-10-19T00:00:00Z")
    assert rejected["deployment_summary"]["state"]=="unavailable"
    assert rejected["deployment_summary"]["result"] is None


def test_m2_egress_dedup_preserves_first_denial_outside_window_even_when_late_record_is_inside():
    from datetime import datetime,timedelta,timezone
    from daon_user_api.operations_audit_scope import egress_denial_correlation,workspace_grant_metadata
    now=datetime(2026,10,19,tzinfo=timezone.utc);start=now-timedelta(days=1)
    def early(run,when):
        key=egress_denial_correlation("tenant-1","workspace-1","actor-1",run)
        return (key,"actor-1",dict(operations_scope=workspace_grant_metadata("workspace-1"),deny_correlation_id=key,safe_error_code="EGRESS_POLICY_DENIED"),when)
    class Connection:
        def execute(self,query,params=()):
            if query=="SELECT clock_timestamp()":rows=[(now,)]
            elif "FROM audit_events" in query:rows=[early("run-outside",start-timedelta(seconds=1)),early("run-inside",start+timedelta(seconds=1))]
            elif "FROM egress_decisions" in query:rows=[("late-a","actor-1","version-a","run-outside",now),
                ("late-b","actor-1","version-a","run-inside",now),("late-c","actor-1","version-a","run-inside",now)]
            elif "FROM identity_auth_access_decisions" in query or "FROM security_audit_events" in query:rows=[]
            else:raise AssertionError("UNEXPECTED_DECISION_SQL")
            return SimpleNamespace(fetchall=lambda:list(rows),fetchone=lambda:rows[0] if rows else None)
    class Store:
        @contextmanager
        def _transaction(self,context):yield Connection()
    repo=PostgresOperationsStatusRepository(Store())
    with patch.object(repo,"_authorize_telemetry"),patch.object(repo,"_visible_sources",return_value={"version-a":{("notebook-a","actor-1","object-a")}}):
        result=repo.read_decision_events(OperationsStatusContext("tenant-1","workspace-1","actor-1","trace-1","policy-1"))[-1]
    assert result["state"]=="partial" and result["value"]==1


def test_system_operations_no_workspace_safe_projection_and_missing_is_not_zero():
    from daon_user_api import operations_status as domain
    assert hasattr(domain,"SystemOperationsStatusService"), "M2-S system projection missing"
    service=domain.SystemOperationsStatusService(None)
    view=service.read(api_ready=True,database_ready=False,object_storage_ready=False,checked_at="2026-10-19T00:00:00Z")
    assert set(view)=={"schema_version","checked_at","health","worker_samples","resource_samples","deployment_summary","unimplemented"}
    assert [r["component_id"] for r in view["health"]]==["api","database","object_storage"]
    assert [r["state"] for r in view["health"]]==["ready","unavailable","unavailable"]
    assert len(view["worker_samples"])==3 and len(view["resource_samples"])==12
    assert all(r["state"]=="not_collected" and r["received_at"] is None for r in view["worker_samples"])
    assert all(r["value"] is None for r in view["resource_samples"])
    assert [r["producer_id"] for r in view["unimplemented"]]==["deployment_receipt_writer"]
    assert "workspace" not in str(view) and "worker_id" not in str(view) and "boot_id" not in str(view)


def test_system_operations_protected_reader_only_and_failure_isolated():
    from daon_user_api import operations_status as domain
    assert hasattr(domain,"SystemOperationsStatusService"), "M2-S system projection missing"
    class Broken:
        def read_system_observations(self):raise RuntimeError("PRIVATE_ENDPOINT_SECRET")
    view=domain.SystemOperationsStatusService(Broken()).read(api_ready=True,database_ready=True,
        object_storage_ready=True,checked_at="2026-10-19T00:00:00Z")
    assert all(r["state"]=="unavailable" for r in view["worker_samples"])
    assert all(r["value"] is None and r["state"]=="unavailable" for r in view["resource_samples"])
    assert "PRIVATE" not in str(view)


def test_system_operations_malformed_clock_or_boot_collision_never_becomes_success():
    from daon_user_api import operations_status as domain
    from daon_user_api.operations_telemetry import WorkerSample,ReceivedWorkerSample
    from datetime import datetime,timezone
    now=datetime(2026,10,19,tzinfo=timezone.utc)
    row=ReceivedWorkerSample(WorkerSample("document-worker-1","boot-a","document",now,1,now),now)
    for rows,clock in (((),None),((row,ReceivedWorkerSample(__import__("dataclasses").replace(row.sample,boot_id="boot-b"),now)),now)):
        repository=SimpleNamespace(read_system_observations=lambda:(rows,clock))
        view=domain.SystemOperationsStatusService(repository).read(api_ready=True,database_ready=True,
            object_storage_ready=True,checked_at="2026-10-19T00:00:00Z")
        assert all(r["state"]=="unavailable" for r in view["worker_samples"])


@contextmanager
def _observation_case(*, sources=None, attempts=None, failed=None):
    from daon_user_api import operations_status as domain, operations_status_postgres as pg
    assert hasattr(domain, "OperationsObservation"), "M1 exact observation DTO 필요"
    context = OperationsStatusContext("tenant-1", "workspace-operations", "actor-1", "trace-observation-test", "policy-test")
    source = ("tenant-1", "workspace-operations", "notebook-a", "actor-1", "source-a", "waiting_model", "version-a", "object-a", 17, "completed", "index-a")
    sources = [source] if sources is None else sources
    attempts = [] if attempts is None else attempts
    log = []
    class Connection:
        def execute(self, sql, parameters=()):
            log.append((sql, parameters))
            if failed and failed in sql:
                from daon_user_api.cloud_storage import CloudDatabaseError
                raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=False)
            if "FROM document_processing_jobs" in sql: rows = [(1, 2, 3, 4)]
            elif "FROM durable_jobs" in sql: rows = [(5, 6, 7, 8, 9.8)]
            elif "JOIN runs r" in sql: rows = attempts
            else: rows = sources
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: list(rows))
    class Store:
        @contextmanager
        def _transaction(self, scope):
            assert (scope.tenant_id, scope.workspace_id, scope.actor_id) == ("tenant-1", "workspace-operations", "actor-1")
            yield Connection()
    with patch.object(pg, "_source_access_allowed", side_effect=lambda conn, ctx, version: version != "revoked"), \
         patch.object(pg, "_special_source_access_allowed", side_effect=lambda conn, ctx, version: version != "law-denied"):
        yield pg.PostgresOperationsStatusRepository(Store()), context, log


def test_operations_observations_are_exact_and_null_is_not_zero():
    with _observation_case() as (repository, context, _):
        rows = repository.read_observations(context)
    assert len(rows) == len({row.metric_id for row in rows}) == 16
    assert rows[8].value == 9 and rows[8].unit == "seconds"
    assert rows[12].state == "not_collected" and rows[12].value is None and rows[12].observed_at is None
    from daon_user_api.operations_status import OperationsObservation
    for value in (True, -1, float("nan")):
        with pytest.raises(ValueError):
            OperationsObservation("reported_total_tokens", "measured", value, "tokens", "2026-10-19T00:00:00Z", "actor_accessible_records", "METRIC_RECORDED")
    with pytest.raises(ValueError):
        OperationsObservation("document_pending", "not_collected", 0, "count", None, "workspace_operational_counts", "METRIC_NOT_COLLECTED")
    with pytest.raises(ValueError):
        OperationsObservation("reported_total_tokens", ["measured"], 7, "tokens", "2026-10-19T00:00:00Z", "actor_accessible_records", "METRIC_RECORDED")
    with pytest.raises(ValueError):
        OperationsObservation("reported_total_tokens", "measured", 7, "tokens", "2026-10-19T00:00:00+00:00", "actor_accessible_records", "METRIC_RECORDED")


def test_reported_usage_missing_bool_negative_or_duplicate_attempts():
    common = ("tenant-1", "workspace-operations", "notebook-a", "actor-1", "run-a", "actor-1", ["version-a"])
    rows = [(*common, "attempt-a", {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7})] * 2
    rows += [(*common, "attempt-b", {"prompt_tokens": True, "completion_tokens": -1}), (*common, "attempt-c", {})]
    with _observation_case(attempts=rows) as (repository, context, _):
        metrics = {row.metric_id: row for row in repository.read_observations(context)}
    assert metrics["reported_prompt_tokens"].value == 3
    assert metrics["reported_completion_tokens"].value == 4
    assert metrics["reported_total_tokens"].value == 7
    assert metrics["reported_total_tokens"].state == "partial"
    assert metrics["attempts_without_reported_usage"].value == 2


def test_source_storage_deduplicates_only_accessible_objects():
    prefix = ("tenant-1", "workspace-operations", "notebook-a", "actor-1")
    rows = [(*prefix, "source-a", "waiting_model", "version-a", "object-a", 17, "completed", "index-a"),
        (*prefix, "source-b", "ready", "version-b", "object-a", 17, "completed", "index-b"),
        (*prefix, "source-c", "ready", "revoked", "object-private", 999, "completed", "index-private")]
    with _observation_case(sources=rows) as (repository, context, _):
        metrics = {row.metric_id: row for row in repository.read_observations(context)}
    assert metrics["source_storage_bytes"].value == 17
    assert metrics["index_records_current"].value == 2
    assert metrics["source_waiting_model"].value == 1


def test_private_notebook_other_actor_and_revoked_source_not_aggregated():
    prefix = ("tenant-1", "workspace-operations", "notebook-private", "other-actor")
    private = (*prefix, "source-secret", "waiting_model", "version-private", "object-private", 999, "completed", "index-private")
    denied = ("tenant-1", "workspace-operations", "notebook-a", "actor-1", "source-law", "ready", "law-denied", "object-law", 777, "completed", "index-law")
    attempts = [("tenant-1", "workspace-operations", "notebook-private", "other-actor", "run-private", "other-actor", ["version-private"], "attempt-private", {"total_tokens": 999})]
    with _observation_case(sources=[private, denied], attempts=attempts) as (repository, context, log):
        rows = {row.metric_id: row for row in repository.read_observations(context)}
    assert rows["source_storage_bytes"].value == 0
    assert rows["reported_total_tokens"].value is None
    assert rows["attempts_without_reported_usage"].value == 0
    source_query = next(sql for sql, _ in log if "JOIN source_versions" in sql)
    assert "n.created_by=%s" in source_query and "notebook_source_unbindings" in source_query
    assert "notebook_deletion_requests" in source_query and "source_active=false" in source_query
    assert all(not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql, _ in log)
    # raw Source ACL만 정상이어도 조회 중 Notebook 결속이 사라지면 이전 집계를 내보내지 않는다.
    changing = [("tenant-1", "workspace-operations", "notebook-a", "actor-1", "source-a", "ready", "version-a", "object-a", 17, "completed", "index-a")]
    checks = []
    def withdraw(connection, scope, version):
        checks.append(version)
        if len(checks) == 2: changing.clear()
        return True
    with _observation_case(sources=changing) as (repository, context, _):
        with patch("daon_user_api.operations_status_postgres._source_access_allowed", side_effect=withdraw):
            result = repository.read_observations(context)
    assert result[11].state == "unavailable" and result[11].value is None
    class DisappearingRuns(list):
        def __iter__(self):
            copy = self.copy(); self.clear(); return iter(copy)
    general = DisappearingRuns([("tenant-1", "workspace-operations", "notebook-a", "actor-1", "run-a", "actor-1", [], "attempt-a", {"total_tokens": 123})])
    with _observation_case(sources=[], attempts=general) as (repository, context, _):
        result = repository.read_observations(context)
    assert result[14].state == "unavailable" and result[14].value is None


def test_unknown_metrics_do_not_change_five_component_health():
    from daon_user_api import operations_status as domain
    assert hasattr(domain, "OperationsObservation"), "M1 exact observation DTO 필요"
    class BadRepository(Repository):
        def read_observations(self, context): return (SimpleNamespace(metric_id="unapproved_metric"),)
    view = OperationsStatusService(BadRepository()).read(OperationsStatusContext("tenant-1", "workspace-operations", "actor-1"),
        active_providers=1, selected_deployments=1, api_ready=True, database_ready=True, object_storage_ready=True,
        checked_at="2026-10-19T00:00:00Z")
    assert view.overall_status == "warning" and len(view.components) == 5
    assert len(view.observations) == 16 and all(row.state == "unavailable" for row in view.observations)
    assert all(row.metric_id != "unapproved_metric" for row in view.observations)


def test_metric_database_failure_is_distinct_from_auth_failure():
    with _observation_case(failed="FROM durable_jobs") as (repository, context, _):
        rows = repository.read_observations(context)
    assert all(row.state == "unavailable" and row.value is None for row in rows[4:9])
    assert rows[0].state == "measured" and rows[9].state == "measured"
    from daon_user_api.authorization import AuthorizationError
    with _observation_case() as (repository, context, _):
        with patch("daon_user_api.operations_status_postgres._source_access_allowed", side_effect=AuthorizationError("ACCESS_DENIED", 403)):
            with pytest.raises(AuthorizationError): repository.read_observations(context)


def test_special_source_database_failure_only_isolates_actor_observations():
    from daon_user_api.notification import NotificationError
    with _observation_case() as (repository, context, _):
        with patch("daon_user_api.operations_status_postgres._special_source_access_allowed",
                side_effect=NotificationError("DATABASE_UNAVAILABLE", 503)):
            rows = repository.read_observations(context)
            class WithCurrentObservations(Repository):
                def read_observations(self, scope): return repository.read_observations(scope)
            options = dict(active_providers=1, selected_deployments=1, api_ready=True, database_ready=True,
                object_storage_ready=True, checked_at="2026-10-19T00:00:00Z")
            before = OperationsStatusService(Repository()).read(context, **options)
            after = OperationsStatusService(WithCurrentObservations()).read(context, **options)
            assert after.components == before.components and after.overall_status == before.overall_status
        assert all(row.state == "measured" and row.value is not None for row in rows[:9])
        assert all(row.state == "unavailable" and row.value is None and row.observed_at is None for row in rows[9:])
        # 같은 종류여도 인증/계약 오류나 다른 HTTP 상태를 개인관측 미수집으로 숨기지 않는다.
        for code, status in (("ACCESS_DENIED", 403), ("DATABASE_RESPONSE_INVALID", 503), ("DATABASE_UNAVAILABLE", 403)):
            with patch("daon_user_api.operations_status_postgres._special_source_access_allowed",
                    side_effect=NotificationError(code, status)):
                with pytest.raises(NotificationError): repository.read_observations(context)


def test_law_observations_use_real_access_helper_and_current_trace_policy(monkeypatch):
    from psycopg.pq import TransactionStatus
    from test_open_law_source_runtime import Connection as LawConnection
    marker = {"origin_kind": "mcp_open_law", "connector_id": "mcp-open-law-go-kr", "snapshot_id": "snapshot-law"}
    law = LawConnection(marker)
    law.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
    source = ("tenant-1", "workspace-operations", "notebook-a", "actor-1", "source-law", "ready", "version-law", "object-law", 23, "completed", "index-law")
    log = []
    class Connection:
        info = law.info
        def execute(self, sql, parameters=()):
            log.append((sql, parameters))
            if "FROM document_processing_jobs" in sql: rows = [(1, 2, 3, 4)]
            elif "FROM durable_jobs" in sql: rows = [(5, 6, 7, 8, 9)]
            elif "SELECT DISTINCT n.tenant_id" in sql: rows = [] if "JOIN runs r" in sql else [source]
            elif "current_setting('app.actor_id'" in sql: rows = [("actor-1", "")]
            elif sql.startswith("SELECT canonical_json FROM public.source_versions"): rows = [(marker,)]
            elif sql.startswith("SELECT NOT EXISTS (SELECT 1 FROM identity_auth_source_access"): rows = [(True,)]
            else: return law.execute(sql, parameters)
            return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)
    class Store:
        @contextmanager
        def _transaction(self, scope): yield Connection()
    context = OperationsStatusContext("tenant-1", "workspace-operations", "actor-1",
        "trace-current-request", "policy-current-runtime")
    monkeypatch.setenv("DAON_OPEN_LAW_ENABLED", "true")
    monkeypatch.setenv("DAON_OPEN_LAW_QA_WORKSPACE_ID", "workspace-operations")
    # policy/법령/SourceACL helper를 mock하지 않는다. SQL port만 합성 현재 원장을 반환한다.
    metrics = {row.metric_id: row for row in PostgresOperationsStatusRepository(Store()).read_observations(context)}
    assert metrics["source_storage_bytes"].value == 23
    assert metrics["index_records_current"].value == 1
    assert sum(sql.startswith("SELECT w.snapshot_id FROM open_law_source_snapshots") for sql, _ in law.queries) == 2
    policy_queries = [params for sql, params in law.queries if sql.startswith("SELECT binding.tenant_id")]
    assert len(policy_queries) == 4  # 최초/최종 각각 조직+Workspace 실제 정책 읽기


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
