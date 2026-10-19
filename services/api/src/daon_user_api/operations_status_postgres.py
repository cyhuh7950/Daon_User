"""PostgreSQL counts for the safe operations status projection."""

from __future__ import annotations

import math
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from psycopg import Error

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .operations_status import (OperationsCounts, OperationsStatusContext, OperationsObservation,
    OBSERVATION_IDS, OBSERVATION_STATES, READINESS_EVENT_IDS, observation_scope, unknown_observations, unknown_telemetry)
from .document_index_postgres import _source_access_allowed
from .notification_source_access import _special_source_access_allowed
from .notification import NotificationError


class OperationsStatusError(RuntimeError):
    def __init__(self, code: str = "OPERATIONS_STATUS_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


class PostgresOperationsStatusRepository:
    def __init__(self, cloud_store: PostgresCloudStore) -> None:
        self._cloud_store = cloud_store

    def read_host_capacity(self):
        # 현재 Workspace VIEW/visible POLICY_MANAGE 또는 시스템관리 전후 인가는 호출 경계가 소유한다.
        from .operations_telemetry_postgres import PostgresHostCapacityRepository
        return PostgresHostCapacityRepository.using_cloud_store(self._cloud_store).read_host_sample_for_collector()

    def read_system_observations(self):
        """system-admin 전후 인가는 runtime 소유. 보호된 서버 관측만 읽고 scope 자료는 열지 않는다."""
        from dataclasses import fields
        from psycopg.rows import dict_row
        from .operations_telemetry import KINDS,WorkerSample,ReceivedWorkerSample,validate_worker_sample,_id,_utc
        from .operations_telemetry_postgres import expected_worker_ids
        expected=expected_worker_ids()
        if set(expected)!=set(KINDS) or len(set(expected.values()))!=3:raise OperationsStatusError()
        for value in expected.values():_id(value)
        try:
            self._cloud_store._ensure_pool_open()
            with self._cloud_store._pool.connection(timeout=2) as connection,connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL ROLE daon_app")
                connection.execute("SET LOCAL statement_timeout='1500ms'")
                with connection.cursor(row_factory=dict_row) as cursor:
                    rows=cursor.execute("SELECT * FROM public.read_operations_worker_samples(%s) LIMIT 7",([expected[k] for k in KINDS],)).fetchall()
                    now=_utc(cursor.execute("SELECT clock_timestamp() AS checked_at").fetchone()["checked_at"])
            if len(rows)>6:raise ValueError()
            names={field.name for field in fields(WorkerSample)};result=[]
            for row in rows:
                if set(row)!=names|{"received_at"}:raise ValueError()
                sample=WorkerSample(**{name:row[name] for name in names})
                if expected.get(sample.worker_kind)!=sample.worker_id:raise ValueError()
                validate_worker_sample(sample,received_at=row["received_at"])
                result.append(ReceivedWorkerSample(sample,row["received_at"]))
            return tuple(result),now
        except Exception:raise OperationsStatusError() from None

    def read_deployment_receipt(self):
        from .operations_telemetry_postgres import PostgresOperationsTelemetryRepository
        @contextmanager
        def read_only():
            self._cloud_store._ensure_pool_open()
            with self._cloud_store._pool.connection(timeout=2) as connection,connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL ROLE daon_app")
                connection.execute("SET LOCAL statement_timeout='1500ms'")
                yield connection
        return PostgresOperationsTelemetryRepository.using_transaction(read_only).read_deployment_receipt()

    def read_counts(self, context: OperationsStatusContext) -> OperationsCounts:
        try:
            with self._cloud_store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "operations.read")) as connection:
                row = connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM sync_operations WHERE tenant_id=%s AND workspace_id=%s AND state IN ('preview','awaiting_approval','approved','transferring','conflict')),"
                    "(SELECT count(*) FROM document_processing_jobs WHERE tenant_id=%s AND workspace_id=%s AND state IN ('pending','leased','retry_wait')),"
                    "(SELECT count(*) FROM document_processing_jobs WHERE tenant_id=%s AND workspace_id=%s AND state='dead_letter')",
                    (context.tenant_id, context.workspace_id) * 3,
                ).fetchone()
        except CloudDatabaseError as error:
            raise OperationsStatusError() from error
        if row is None:
            raise OperationsStatusError()
        return OperationsCounts(int(row[0]), int(row[1]), int(row[2]))

    def _transaction(self, context):
        return self._cloud_store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "operations.read"))

    @staticmethod
    def _authorize_telemetry(connection,context):
        from .operations_telemetry_postgres import PostgresOperationsTelemetryRepository
        from .authorization import Action,AuthorizationError
        from .open_law_source_access import _current_auth_grant,OpenLawAccessError
        PostgresOperationsTelemetryRepository._authorize(connection,context)
        if context.scope=="workspace_visible":
            try:_current_auth_grant(context,connection,Action.POLICY_MANAGE)
            except OpenLawAccessError:raise AuthorizationError("CURRENT_ACCESS_DENIED",403) from None

    @staticmethod
    def _visible_sources(connection,context):
        rows=connection.execute(
            "SELECT DISTINCT n.notebook_id,n.created_by,sv.record_id,sv.object_id FROM notebooks n JOIN notebook_bindings b ON "
            "(b.tenant_id,b.workspace_id,b.notebook_id)=(n.tenant_id,n.workspace_id,n.notebook_id) AND b.binding_kind='source' "
            "JOIN source_versions sv ON (sv.tenant_id,sv.workspace_id,sv.record_id,sv.source_id)=(b.tenant_id,b.workspace_id,b.version_id,b.record_id) "
            "JOIN sources s ON (s.tenant_id,s.workspace_id,s.record_id)=(sv.tenant_id,sv.workspace_id,sv.source_id) "
            "WHERE n.tenant_id=%s AND n.workspace_id=%s AND (%s='workspace_visible' OR n.created_by=%s) "
            "AND s.state NOT IN ('expired','disabled','deleting','deleted') "
            "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE (d.tenant_id,d.workspace_id,d.notebook_id)=(n.tenant_id,n.workspace_id,n.notebook_id) AND d.state IN ('accepted','deleting','completed')) "
            "AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE (u.tenant_id,u.workspace_id,u.notebook_id,u.source_id,u.source_version_id)=(b.tenant_id,b.workspace_id,b.notebook_id,b.record_id,b.version_id)) "
            "AND NOT EXISTS(SELECT 1 FROM deletion_requests d WHERE (d.tenant_id,d.workspace_id,d.source_id)=(s.tenant_id,s.workspace_id,s.record_id) AND (d.source_active=false OR d.state='purged'))",
            (context.tenant_id,context.workspace_id,context.scope,context.actor_id)).fetchall()
        visible={}
        for notebook,owner,version,obj in rows:
            if context.scope=="self" and owner!=context.actor_id:continue
            scoped=SimpleNamespace(tenant_id=context.tenant_id,workspace_id=context.workspace_id,actor_id=context.actor_id,
                notebook_id=notebook,trace_id=context.trace_id,policy_version=context.policy_version)
            if _source_access_allowed(connection,scoped,version) and _special_source_access_allowed(connection,scoped,version):
                visible.setdefault(version,set()).add((notebook,owner,obj))
        return visible

    @staticmethod
    def _event_metric(key,value,when,*,window_start=None):
        stamp=when.isoformat().replace("+00:00","Z")
        return dict(state="measured",observed_at=stamp,received_at=stamp,coverage="actor_accessible_records",
            safe_code="TELEMETRY_MEASURED",metric_id=key,value=value,unit="count",
            window_start=None if window_start is None else window_start.isoformat().replace("+00:00","Z"),
            window_end=None if window_start is None else stamp)

    def read_pending_events(self,context):
        from .authorization import AuthorizationError
        from .operations_telemetry import _utc
        with self._transaction(context) as connection:
            self._authorize_telemetry(connection,context)
            when=_utc(connection.execute("SELECT clock_timestamp()").fetchone()[0])
            visible=self._visible_sources(connection,context)
            allowed_objects={obj for values in visible.values() for _,_,obj in values if obj is not None}
            eligible=lambda actor:context.scope=="workspace_visible" or actor==context.actor_id
            docs=connection.execute("SELECT j.job_id,j.created_by,j.source_version_id,j.state,p.trigger_type,j.next_attempt_at "
                "FROM document_processing_jobs j JOIN processing_runs p ON (p.tenant_id,p.workspace_id,p.record_id)=(j.tenant_id,j.workspace_id,j.processing_run_id) "
                "WHERE j.tenant_id=%s AND j.workspace_id=%s AND j.state IN ('pending','leased','retry_wait')",
                (context.tenant_id,context.workspace_id)).fetchall()
            manual=set();retry=set();backoff=set()
            for job,actor,version,state,trigger,next_at in docs:
                if not eligible(actor) or version not in visible:continue
                if trigger=="manual_request" and state in {"pending","leased","retry_wait"}:manual.add(job)
                if state=="retry_wait":retry.add(("document",job))
                if state in {"pending","retry_wait"} and _utc(next_at)>when:backoff.add(("document",job))
            objects=connection.execute("SELECT j.job_id,j.created_by,e.object_id,j.state,j.next_attempt_at FROM durable_jobs j "
                "JOIN object_outbox_events e ON (e.tenant_id,e.workspace_id,e.event_id)=(j.tenant_id,j.workspace_id,j.event_id) "
                "WHERE j.tenant_id=%s AND j.workspace_id=%s AND j.state IN ('pending','retry_wait')",
                (context.tenant_id,context.workspace_id)).fetchall()
            for job,actor,obj,state,next_at in objects:
                if not eligible(actor) or obj not in allowed_objects:continue
                if state=="retry_wait":retry.add(("object",job))
                if _utc(next_at)>when:backoff.add(("object",job))
            studio=connection.execute("SELECT job_id,actor_id,request_json->>'notebook_id',request_json->'source_version_ids',state,next_attempt_at "
                "FROM studio_generation_jobs WHERE tenant_id=%s AND workspace_id=%s AND state='queued' AND next_attempt_at>%s",
                (context.tenant_id,context.workspace_id,when)).fetchall()
            for job,actor,notebook,versions,state,next_at in studio:
                if (not eligible(actor) or type(versions) is not list or not versions
                    or any(type(v) is not str or v not in visible or not any(n==notebook for n,_,_ in visible[v]) for v in versions)):continue
                if state=="queued" and _utc(next_at)>when:backoff.add(("studio",job))
            self._authorize_telemetry(connection,context)
            if self._visible_sources(connection,context)!=visible:raise AuthorizationError("CURRENT_ACCESS_DENIED",403)
        missing=unknown_telemetry()["event_counts"][1]
        return (self._event_metric("manual_reprocess_pending",len(manual),when),missing,
            self._event_metric("retry_wait",len(retry),when),self._event_metric("backoff_wait",len(backoff),when))

    def read_workspace_telemetry(self,context):
        from .operations_status import SystemOperationsStatusService,validate_telemetry
        with self._transaction(context) as connection:self._authorize_telemetry(connection,context)
        result=unknown_telemetry(include_readiness=context.include_readiness,include_capacity=context.include_capacity)
        system=SystemOperationsStatusService(self).read(api_ready=True,database_ready=True,object_storage_ready=True,
            checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),include_capacity=context.include_capacity)
        for key in ("worker_samples","resource_samples"):
            result[key]=[dict(row,coverage='server_host_observation' if row['worker_kind']=='server_host'
                else 'workspace_operational_counts') for row in system[key]]
        result["deployment_summary"]=dict(system["deployment_summary"],coverage="actor_accessible_records")
        result["unimplemented"]=[r for r in result["unimplemented"] if r["producer_id"] not in {"cost_receipt_writer","deployment_receipt_writer"}]
        try:result["event_counts"][:4]=self.read_pending_events(context)
        except (CloudDatabaseError,Error,ValueError,OverflowError):
            result["event_counts"][:4]=unknown_telemetry("unavailable")["event_counts"][:4]
        except NotificationError as error:
            if error.code!="DATABASE_UNAVAILABLE" or error.http_status!=503:raise
            result["event_counts"][:4]=unknown_telemetry("unavailable")["event_counts"][:4]
        try:result["event_counts"][4:9]=self.read_decision_events(context)
        except (CloudDatabaseError,Error,ValueError,OverflowError):
            result["event_counts"][4:9]=unknown_telemetry("unavailable")["event_counts"][4:9]
        except NotificationError as error:
            if error.code!="DATABASE_UNAVAILABLE" or error.http_status!=503:raise
            result["event_counts"][4:9]=unknown_telemetry("unavailable")["event_counts"][4:9]
        from .operations_telemetry import TelemetryUnavailable
        try:result["cost_summary"]=self.read_cost_summary(context)
        except (CloudDatabaseError,Error,ValueError,OverflowError,TelemetryUnavailable):
            result["cost_summary"]=unknown_telemetry("unavailable")["cost_summary"]
        except NotificationError as error:
            if error.code!="DATABASE_UNAVAILABLE" or error.http_status!=503:raise
            result["cost_summary"]=unknown_telemetry("unavailable")["cost_summary"]
        if context.include_readiness:
            try:result["event_counts"][9:]=self.read_readiness_metrics(context)
            except (CloudDatabaseError,Error,ValueError,OverflowError):
                result["event_counts"][9:]=unknown_telemetry("unavailable",include_readiness=True)["event_counts"][9:]
        with self._transaction(context) as connection:self._authorize_telemetry(connection,context)
        return validate_telemetry(result,include_readiness=context.include_readiness,include_capacity=context.include_capacity)

    def read_readiness_metrics(self,context):
        """개별 원장을 읽지 않고 현재 인가된 보호 함수의 7필드/6행만 투영한다."""
        from .authorization import AuthorizationError
        from .operations_telemetry import _utc
        if context.include_readiness is not True:
            raise ValueError("OPERATIONS_STATUS_CONTEXT_INVALID")
        unknown = unknown_telemetry(include_readiness=True)["event_counts"][9:]
        with self._transaction(context) as connection:
            self._authorize_telemetry(connection,context)
            visible = self._visible_sources(connection,context)
        # 현재 서버의 특수 Source runtime 조건을 추가 제한으로 전달한다. 인가 근거는 아니다.
        if (type(visible) is not dict or len(visible)>256
                or any(type(version) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}",version)
                       for version in visible)):
            raise ValueError("OPERATIONS_READINESS_FILTER_INVALID")
        visible_filter=json.dumps(sorted(visible),separators=(",",":"))
        if len(visible_filter.encode("utf-8"))>65536:raise ValueError("OPERATIONS_READINESS_FILTER_INVALID")
        # 기존 인가 helper의 FOR SHARE는 앞/뒤 별도 Tx에 유지한다.
        # scope GUC 설정 뒤 첫 업무 조회 전에 읽기 전용을 강제한다.
        with self._transaction(context) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout='2000ms'")
            original_filter=connection.execute("SELECT current_setting('app.readiness_visible_source_versions',true)").fetchone()[0]
            try:
                connection.execute("SELECT set_config('app.readiness_visible_source_versions',%s,true)",(visible_filter,))
                rows = connection.execute(
                    "SELECT metric_id,value,has_records,coverage_partial,checked_at,window_start,window_end "
                    "FROM public.read_document_readiness_metrics(%s,%s,%s,%s)",
                    (context.tenant_id,context.workspace_id,context.actor_id,context.scope)).fetchall()
            finally:
                # SQL 실패 상태에는 명령을 더 보내 원예외를 가리지 않는다. 바깥 Tx rollback이 LOCAL을 회수한다.
                from psycopg.pq import TransactionStatus
                if connection.info.transaction_status!=TransactionStatus.INERROR:
                    connection.execute("SELECT set_config('app.readiness_visible_source_versions',%s,true)",(original_filter or "",))
            if len(rows)!=6 or any(type(row) not in {tuple,list} or len(row)!=7 for row in rows):
                raise ValueError("OPERATIONS_READINESS_METRICS_INVALID")
            indexed={}
            checked_at=None
            for row in rows:
                key,value,has_records,partial,when,start,end=row
                if (type(key) is not str or key not in READINESS_EVENT_IDS or key in indexed
                        or type(has_records) is not bool or type(partial) is not bool):
                    raise ValueError("OPERATIONS_READINESS_METRICS_INVALID")
                when=_utc(when)
                if checked_at is not None and when!=checked_at:raise ValueError("OPERATIONS_READINESS_METRICS_INVALID")
                checked_at=when
                if not has_records:
                    if value is not None or start is not None or end is not None:
                        raise ValueError("OPERATIONS_READINESS_METRICS_INVALID")
                    projected=dict(unknown[READINESS_EVENT_IDS.index(key)])
                else:
                    if type(value) is not int or not 0<=value<=9007199254740991:
                        raise ValueError("OPERATIONS_READINESS_METRICS_INVALID")
                    if key==READINESS_EVENT_IDS[-1]:
                        if _utc(end)!=when or (_utc(end)-_utc(start))!=timedelta(days=1):
                            raise ValueError("OPERATIONS_READINESS_METRICS_INVALID")
                    elif start is not None or end is not None:
                        raise ValueError("OPERATIONS_READINESS_METRICS_INVALID")
                    projected=self._event_metric(key,value,when,window_start=start)
                    # 구 Run/비활성·부분 scope의 미추적 자료를 완전 수집으로 올리지 않는다.
                    projected.update(state="partial",safe_code="TELEMETRY_PARTIAL")
                indexed[key]=projected
        with self._transaction(context) as connection:
            self._authorize_telemetry(connection,context)
            if self._visible_sources(connection,context)!=visible:
                raise AuthorizationError("CURRENT_ACCESS_DENIED",403)
        return tuple(indexed[key] for key in READINESS_EVENT_IDS)

    def _telemetry_reader(self,context):
        from .operations_telemetry_postgres import PostgresOperationsTelemetryRepository
        return PostgresOperationsTelemetryRepository.using_transaction(lambda:self._transaction(context))

    def read_cost_summary(self,context):
        from decimal import Decimal,localcontext
        from .authorization import AuthorizationError
        from .operations_telemetry import _utc
        with self._transaction(context) as connection:
            self._authorize_telemetry(connection,context)
            when=_utc(connection.execute("SELECT clock_timestamp()").fetchone()[0])
            visible=self._visible_sources(connection,context)
            query=("SELECT /* operations-summary:runs */ r.record_id,r.created_by,n.notebook_id,"
                "rs.canonical_json->'source_version_ids',r.state,r.created_at,m.record_id "
                "FROM runs r JOIN run_snapshots rs ON (rs.tenant_id,rs.workspace_id,rs.run_id)=(r.tenant_id,r.workspace_id,r.record_id) "
                "JOIN notebook_bindings b ON (b.tenant_id,b.workspace_id,b.record_id)=(r.tenant_id,r.workspace_id,r.conversation_id) AND b.binding_kind='conversation_thread' "
                "JOIN notebooks n ON (n.tenant_id,n.workspace_id,n.notebook_id)=(b.tenant_id,b.workspace_id,b.notebook_id) "
                "LEFT JOIN routing_decisions rd ON (rd.tenant_id,rd.workspace_id,rd.run_id)=(r.tenant_id,r.workspace_id,r.record_id) "
                "LEFT JOIN model_attempts m ON (m.tenant_id,m.workspace_id,m.routing_decision_id)=(rd.tenant_id,rd.workspace_id,rd.record_id) "
                "WHERE r.tenant_id=%s AND r.workspace_id=%s AND (%s='workspace_visible' OR r.created_by=%s) "
                "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE "
                "(d.tenant_id,d.workspace_id,d.notebook_id)=(n.tenant_id,n.workspace_id,n.notebook_id) AND d.state IN ('accepted','deleting','completed'))")
            params=(context.tenant_id,context.workspace_id,context.scope,context.actor_id)
            rows=connection.execute(query,params).fetchall()
            runs={};attempts=set();starts=[]
            for run,actor,notebook,versions,state,created,attempt in rows:
                if context.scope=="self" and actor!=context.actor_id:continue
                if type(versions) is not list or any(type(v) is not str or not any(n==notebook for n,_,_ in visible.get(v,())) for v in versions):continue
                if run in runs and runs[run]!=(actor,state):raise ValueError("OPERATIONS_COST_INVALID")
                runs[run]=(actor,state);starts.append(_utc(created))
                if attempt is not None:attempts.add((run,attempt))
            before=tuple(rows)
        unknown=unknown_telemetry()["cost_summary"]
        if not runs:return unknown
        receipts=self._telemetry_reader(context).read_cost_receipts(context,tuple(sorted(runs)))
        amounts={};known=set();uncertain=set();seen=set()
        with localcontext() as decimal_context:
            decimal_context.prec=50
            for row in receipts:
                run=row["run_id"];attempt=row["model_attempt_id"];key=(run,attempt)
                if run not in runs or row["actor_id"]!=runs[run][0] or key in seen:raise ValueError("OPERATIONS_COST_INVALID")
                if attempt is not None and key not in attempts:raise ValueError("OPERATIONS_COST_INVALID")
                seen.add(key)
                if row["state"]=="known":
                    from .operations_telemetry import ReportedCost
                    ReportedCost(row["amount"],row["currency"],"summary-cost")
                    amounts[row["currency"]]=amounts.get(row["currency"],Decimal(0))+row["amount"]
                    known.add(key)
                elif row["state"] in {"unknown","uncertain"}:
                    if row["amount"] is not None or row["currency"] is not None:raise ValueError("OPERATIONS_COST_INVALID")
                    uncertain.add(run)
                else:raise ValueError("OPERATIONS_COST_INVALID")
            missing=len(attempts-known)
            unconfirmed=sum(state in {"failed","cancelled","canceled"} and (run in uncertain or not any(k[0]==run for k in known)) for run,(_,state) in runs.items())
            if not amounts and not missing and not unconfirmed:
                return dict(unknown,state="not_reported",safe_code="TELEMETRY_NOT_REPORTED")
            result=dict(state="partial" if missing or unconfirmed else "measured",observed_at=when.isoformat().replace("+00:00","Z"),
                received_at=when.isoformat().replace("+00:00","Z"),coverage="actor_accessible_records",
                safe_code="TELEMETRY_PARTIAL" if missing or unconfirmed else "TELEMETRY_MEASURED",
                known_amounts=[dict(currency=key,amount=format(amounts[key],"f")) for key in sorted(amounts)],
                unknown_attempt_count=missing,unconfirmed_run_count=unconfirmed,
                coverage_start=min(starts).isoformat().replace("+00:00","Z"),coverage_end=when.isoformat().replace("+00:00","Z"))
        with self._transaction(context) as connection:
            self._authorize_telemetry(connection,context)
            if self._visible_sources(connection,context)!=visible:raise AuthorizationError("CURRENT_ACCESS_DENIED",403)
            normalize=lambda values:sorted(json.dumps(row,sort_keys=True,default=str,separators=(",",":")) for row in values)
            if normalize(connection.execute(query,params).fetchall())!=normalize(before):raise AuthorizationError("CURRENT_ACCESS_DENIED",403)
        return result

    def read_decision_events(self,context):
        from .authorization import AuthorizationError
        from .operations_telemetry import _utc
        with self._transaction(context) as connection:
            self._authorize_telemetry(connection,context)
            when=_utc(connection.execute("SELECT clock_timestamp()").fetchone()[0]);start=when-timedelta(days=1)
            visible=self._visible_sources(connection,context)
            eligible=lambda actor:context.scope=="workspace_visible" or actor==context.actor_id
            audits=connection.execute("SELECT event_id,actor_id,action,metadata FROM security_audit_events "
                "WHERE tenant_id=%s AND outcome='denied' AND action IN "
                "('identity.step_up.binding_denied','identity.step_up.reuse_denied','identity.step_up.expired') "
                "AND occurred_at>%s AND occurred_at<=%s AND metadata->'operations_scope'->>'workspace_id'=%s "
                "AND (%s='workspace_visible' OR actor_id=%s)",
                (context.tenant_id,start,when,context.workspace_id,context.scope,context.actor_id)).fetchall()
            step_denied=set();step_expired=set();step_attributed=set()
            from .operations_audit_scope import VerifiedOperationsScope
            fields={"version","workspace_id","scope_basis","resource_type","resource_id","notebook_id","source_version_ids"}
            for event,actor,action,metadata in audits:
                if not eligible(actor) or type(metadata) is not dict:continue
                raw=metadata.get("operations_scope")
                if type(raw) is not dict or set(raw)!=fields or type(raw["source_version_ids"]) is not list:continue
                try:
                    value=VerifiedOperationsScope(context.tenant_id,actor,"audit-scope","audit-scope",raw["workspace_id"],
                        raw["scope_basis"],raw["resource_type"],raw["resource_id"],raw["notebook_id"],tuple(raw["source_version_ids"]),raw["version"])
                except (ValueError,TypeError):continue
                if value.workspace_id!=context.workspace_id:continue
                # 현재 producer는 Workspace 인가만 기록한다. 자료 귀속을 추정하지 않는다.
                if value.scope_basis!="workspace_grant":continue
                if action not in {"identity.step_up.binding_denied","identity.step_up.reuse_denied","identity.step_up.expired"}:continue
                step_attributed.add(event)
                (step_expired if action=="identity.step_up.expired" else step_denied).add(event)
            decisions=connection.execute("SELECT d.decision_id,d.actor_id,d.state,h.source_version_ids FROM identity_auth_access_decisions d "
                "LEFT JOIN identity_auth_historical_results h ON (h.tenant_id,h.workspace_id,h.result_id)=(d.tenant_id,d.workspace_id,d.resource_id) "
                "WHERE d.tenant_id=%s AND d.workspace_id=%s AND d.state IN ('access_blocked','partially_redacted') "
                "AND d.evaluated_at>%s AND d.evaluated_at<=%s AND (%s='workspace_visible' OR d.actor_id=%s)",
                (context.tenant_id,context.workspace_id,start,when,context.scope,context.actor_id)).fetchall()
            blocked=set();masked=set();attributed=set()
            for event,actor,state,raw in decisions:
                if not eligible(actor) or state not in {"access_blocked","partially_redacted"}:continue
                try:versions=json.loads(raw) if isinstance(raw,str) else raw
                except (ValueError,TypeError):continue
                if type(versions) is not list or not versions or any(type(v) is not str or v not in visible for v in versions):continue
                attributed.add(event)
                (blocked if state=="access_blocked" else masked).add(event)
            from .operations_audit_scope import egress_denial_correlation,workspace_grant_metadata
            early=connection.execute("SELECT event_id,actor_id,metadata,occurred_at FROM audit_events "
                "WHERE tenant_id=%s AND workspace_id=%s AND action='operations.egress.denied' AND outcome='denied' "
                "AND occurred_at<=%s AND (%s='workspace_visible' OR actor_id=%s)",
                (context.tenant_id,context.workspace_id,when,context.scope,context.actor_id)).fetchall()
            first={}
            for event,actor,metadata,occurred in early:
                if not eligible(actor) or type(metadata) is not dict or set(metadata)!={"operations_scope","deny_correlation_id","safe_error_code"}:continue
                correlation=metadata["deny_correlation_id"]
                if (type(correlation) is not str or re.fullmatch(r"egress-denial-[0-9a-f]{64}",correlation) is None
                    or event!=correlation or metadata["safe_error_code"]!="EGRESS_POLICY_DENIED"
                    or metadata["operations_scope"]!=workspace_grant_metadata(context.workspace_id)):continue
                instant=_utc(occurred)
                first[correlation]=min(first.get(correlation,instant),instant)
            egress=connection.execute("SELECT e.record_id,r.created_by,r.canonical_json->>'source_version_id',r.record_id,e.created_at FROM egress_decisions e "
                "JOIN runs r ON (r.tenant_id,r.workspace_id,r.record_id)=(e.tenant_id,e.workspace_id,e.run_id) "
                "WHERE e.tenant_id=%s AND e.workspace_id=%s AND e.canonical_json->'allowed'='false'::jsonb "
                "AND e.created_by=r.created_by AND e.created_at<=%s AND (%s='workspace_visible' OR r.created_by=%s)",
                (context.tenant_id,context.workspace_id,when,context.scope,context.actor_id)).fetchall()
            for event,actor,version,run,occurred in egress:
                if not eligible(actor) or type(version) is not str or version not in visible:continue
                try:correlation=egress_denial_correlation(context.tenant_id,context.workspace_id,actor,run)
                except ValueError:continue
                instant=_utc(occurred)
                first[correlation]=min(first.get(correlation,instant),instant)
            denied={key for key,instant in first.items() if start<instant<=when}
            self._authorize_telemetry(connection,context)
            if self._visible_sources(connection,context)!=visible:raise AuthorizationError("CURRENT_ACCESS_DENIED",403)
        # 성공 snapshot/binding을 요구하지 않는다. 귀속 없는 과거·조기 거부의 coverage를 합성하지 않는다.
        unknown=unknown_telemetry()["event_counts"]
        result=[]
        for index,key,value,observed in ((4,"step_up_denied_24h",len(step_denied),bool(step_attributed)),
                (5,"step_up_expired_24h",len(step_expired),bool(step_attributed)),(6,"access_blocked_24h",len(blocked),bool(attributed)),
                (7,"access_masked_24h",len(masked),bool(attributed)),(8,"egress_denied_24h",len(denied),bool(first))):
            if not observed:result.append(unknown[index]);continue
            row=self._event_metric(key,value,when,window_start=start)
            row.update(state="partial",safe_code="TELEMETRY_PARTIAL");result.append(row)
        return tuple(result)

    @staticmethod
    def _metric(metric_id, value, when, state="measured"):
        unit, coverage = observation_scope(metric_id)
        return OperationsObservation(metric_id, state, value, unit, when, coverage, OBSERVATION_STATES[state])

    def _queue(self, context, *, objects):
        table = "durable_jobs" if objects else "document_processing_jobs"
        with self._transaction(context) as connection:
            row = connection.execute("SELECT count(*) FILTER(WHERE state='pending'),count(*) FILTER(WHERE state='retry_wait'),"
                "count(*) FILTER(WHERE state='leased'),count(*) FILTER(WHERE state='dead_letter')" +
                (",COALESCE(extract(epoch FROM (CURRENT_TIMESTAMP-min(created_at) FILTER(WHERE state IN ('pending','retry_wait')))),0)" if objects else "") +
                f" FROM {table} WHERE tenant_id=%s AND workspace_id=%s", (context.tenant_id, context.workspace_id)).fetchone()
        if row is None: raise ValueError()
        values = list(row)
        if objects:
            if isinstance(values[-1], bool) or not math.isfinite(float(values[-1])) or values[-1] < 0: raise ValueError()
            values[-1] = math.floor(values[-1])
        return values

    def _actor_data(self, context):
        if not context.trace_id or not context.policy_version: raise ValueError("OPERATIONS_SOURCE_CONTEXT_UNAVAILABLE")
        with self._transaction(context) as connection:
            source_query = (
                "SELECT DISTINCT n.tenant_id,n.workspace_id,n.notebook_id,n.created_by,s.record_id,s.state,sv.record_id,"
                "o.object_id,o.byte_size,o.status,ix.record_id FROM notebooks n JOIN notebook_bindings b ON "
                "b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id AND b.binding_kind='source' "
                "JOIN source_versions sv ON sv.tenant_id=b.tenant_id AND sv.workspace_id=b.workspace_id AND sv.record_id=b.version_id AND sv.source_id=b.record_id "
                "JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                "LEFT JOIN object_records o ON o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id AND o.object_id=sv.object_id "
                "LEFT JOIN LATERAL (SELECT record_id FROM index_versions i WHERE i.tenant_id=sv.tenant_id AND i.workspace_id=sv.workspace_id "
                "AND i.source_version_id=sv.record_id ORDER BY i.created_at DESC,i.record_id DESC LIMIT 1) ix ON true "
                "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.created_by=%s "
                "AND s.state NOT IN ('expired','disabled','deleting','deleted') "
                "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id "
                "AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed')) "
                "AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id AND u.workspace_id=b.workspace_id "
                "AND u.notebook_id=b.notebook_id AND u.source_id=b.record_id AND u.source_version_id=b.version_id) "
                "AND NOT EXISTS(SELECT 1 FROM deletion_requests d WHERE d.tenant_id=s.tenant_id AND d.workspace_id=s.workspace_id "
                "AND d.source_id=s.record_id AND (d.source_active=false OR d.state='purged'))")
            source_parameters = (context.tenant_id, context.workspace_id, context.actor_id)
            sources = connection.execute(source_query, source_parameters).fetchall()
            valid, waiting, indexes, objects, incomplete = set(), set(), set(), {}, False
            for tenant, workspace, notebook, owner, source, state, version, obj, size, status, index in sources:
                if (tenant, workspace, owner) != (context.tenant_id, context.workspace_id, context.actor_id): continue
                scoped = SimpleNamespace(tenant_id=tenant, workspace_id=workspace, actor_id=context.actor_id, notebook_id=notebook,
                    trace_id=context.trace_id, policy_version=context.policy_version)
                if not _source_access_allowed(connection, scoped, version) or not _special_source_access_allowed(connection, scoped, version): continue
                valid.add((notebook, version))
                if state == "waiting_model": waiting.add(source)
                if index is not None: indexes.add(index)
                if obj is None or status != "completed" or type(size) is not int or size < 0:
                    incomplete = True; continue
                if obj in objects and objects[obj] != size: raise ValueError()
                objects[obj] = size
            attempt_query = (
                "SELECT DISTINCT n.tenant_id,n.workspace_id,n.notebook_id,n.created_by,r.record_id,r.created_by,"
                "rs.canonical_json->'source_version_ids',m.record_id,m.usage_json FROM notebooks n JOIN notebook_bindings b "
                "ON b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id AND b.binding_kind='conversation_thread' "
                "JOIN runs r ON r.tenant_id=b.tenant_id AND r.workspace_id=b.workspace_id AND r.conversation_id=b.record_id "
                "JOIN run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id "
                "JOIN routing_decisions rd ON rd.tenant_id=r.tenant_id AND rd.workspace_id=r.workspace_id AND rd.run_id=r.record_id "
                "JOIN model_attempts m ON m.tenant_id=rd.tenant_id AND m.workspace_id=rd.workspace_id AND m.routing_decision_id=rd.record_id "
                "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.created_by=%s AND r.created_by=%s AND m.created_by=%s "
                "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id "
                "AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed'))")
            attempt_parameters = (context.tenant_id, context.workspace_id, context.actor_id, context.actor_id, context.actor_id)
            attempts = connection.execute(attempt_query, attempt_parameters).fetchall()
            usage = {}
            for tenant, workspace, notebook, owner, run, actor, versions, attempt, value in attempts:
                if (tenant, workspace, owner, actor) != (context.tenant_id, context.workspace_id, context.actor_id, context.actor_id): continue
                if not isinstance(versions, list) or any(not isinstance(version, str) or (notebook, version) not in valid for version in versions): continue
                if attempt in usage and usage[attempt] != value: raise ValueError()
                usage[attempt] = value
            # 결과 직전 ACL을 다시 읽는다. 현재 권한과 commit의 원자 직렬화를 주장하지 않는다.
            for notebook, version in valid:
                scoped = SimpleNamespace(tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor_id=context.actor_id, notebook_id=notebook,
                    trace_id=context.trace_id, policy_version=context.policy_version)
                if not _source_access_allowed(connection, scoped, version) or not _special_source_access_allowed(connection, scoped, version): raise ValueError()
            if set(connection.execute(source_query, source_parameters).fetchall()) != set(sources): raise ValueError()
            normalize = lambda rows: sorted(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) for row in rows)
            if normalize(connection.execute(attempt_query, attempt_parameters).fetchall()) != normalize(attempts): raise ValueError()
        return len(waiting), len(indexes), sum(objects.values()), incomplete, usage

    def read_observations(self, context):
        result = []
        for objects, ids in ((False, OBSERVATION_IDS[:4]), (True, OBSERVATION_IDS[4:9])):
            try:
                values = self._queue(context, objects=objects)
                when = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                if len(values) != len(ids): raise ValueError()
                group = tuple(self._metric(key, value, when) for key, value in zip(ids, values))
                result.extend(group)
            except (CloudDatabaseError, Error, ValueError, OverflowError): result.extend(unknown_observations("unavailable", ids))
        try:
            waiting, indexes, size, incomplete, usage = self._actor_data(context)
            when = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            result.extend((self._metric("source_waiting_model", waiting, when), self._metric("index_records_current", indexes, when),
                self._metric("source_storage_bytes", size, when, "partial" if incomplete else "measured")))
            valid_integer = lambda value: type(value) is int and 0 <= value <= 9007199254740991
            fields = ("prompt_tokens", "completion_tokens", "total_tokens")
            for field, metric in zip(fields, OBSERVATION_IDS[12:15]):
                values = [row[field] for row in usage.values() if isinstance(row, dict) and valid_integer(row.get(field))]
                if not values: result.extend(unknown_observations("not_collected", (metric,)))
                else: result.append(self._metric(metric, sum(values), when, "partial" if len(values) != len(usage) else "measured"))
            missing = sum(not isinstance(row, dict) or any(not valid_integer(row.get(field)) for field in fields) for row in usage.values())
            result.append(self._metric("attempts_without_reported_usage", missing, when))
        except NotificationError as error:
            if error.code != "DATABASE_UNAVAILABLE" or error.http_status != 503: raise
            result = result[:9] + list(unknown_observations("unavailable", OBSERVATION_IDS[9:]))
        except (CloudDatabaseError, Error, ValueError, OverflowError):
            result = result[:9] + list(unknown_observations("unavailable", OBSERVATION_IDS[9:]))
        return tuple(result)
