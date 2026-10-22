"""기존 immutable snapshot/job/Run을 쓰는 Studio 전용 실행 저장소. 새 DDL 없음."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
import json
import hashlib
import re
from types import SimpleNamespace
from psycopg import Error as PostgresError
from psycopg.types.json import Jsonb
from .authorization import Action,Permission
from .cloud_storage import CloudAccessContext,CloudDatabaseError
from .data_canon import canonical_json_bytes
from .studio_execution import StudioExecutionContext,digest,safe_id
from .studio_workspace import StudioError,StudioGenerationRequest
from .studio_workspace_postgres import PostgresStudioWorkspaceRepository
from .notebook_model_settings import NotebookModelExecution,NotebookModelSelection,NotebookModelSettingsError
from .notebook_model_settings_postgres import assert_execution_current
from .open_law_source_access import _current_auth_grant,OpenLawAccessError
from .operations_telemetry import make_cost_receipt
from .operations_telemetry_postgres import record_cost_in_transaction

_META_KEYS={"operation","revision_type","change_reason","output_id","previous_version_id","selection","policy_fingerprint","payload_digest","evidence_digest","sources"}
_SELECTION_KEYS={"connection_id","model_id","provider_code","provider_kind","adapter_type","connection_version","catalog_version","credential_version","reasoning_effort","model_settings_version","reasoning_profile_version","endpoint_digest"}
_SOURCE_KEYS={"source_id","source_version_id","source_digest","index_version_id","index_digest"}
_REQUEST_KEYS={"output_type","source_id","source_version_ids","run_id","run_result_id","purpose","audience","ruleset_version_id","length","structure","output_format","review_condition","language","source_only","intended_model_selection"}
_PLAN_KEYS={"kind","schema_version","scope","request","metadata","key_digest","fingerprint","created_at","expires_at","plan_id"}


def _unavailable():raise StudioError("STUDIO_EXECUTION_UNAVAILABLE",503)


def _utc(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset()!=timedelta(0):_unavailable()
    return value


def _hex(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}",value) is None:_unavailable()


def validate_inputs(request,metadata):
    if type(request) is not dict or set(request)!=_REQUEST_KEYS or type(metadata) is not dict or set(metadata)!=_META_KEYS:_unavailable()
    try:
        checked=StudioGenerationRequest(**{**request,"source_version_ids":tuple(request["source_version_ids"])})
        if checked.intended_model_selection is None:_unavailable()
        if metadata["operation"] not in {"generate","regenerate"}:_unavailable()
        if metadata["operation"]=="generate":
            if metadata["output_id"] is not None or metadata["previous_version_id"] is not None or metadata["revision_type"]!="initial" or metadata["change_reason"] is not None:_unavailable()
        else:
            safe_id(metadata["output_id"]);safe_id(metadata["previous_version_id"])
            if metadata["revision_type"] not in {"ai_regeneration","settings_change"}:_unavailable()
            reason=metadata["change_reason"]
            if type(reason) is not str or not 1<=len(reason.strip())<=500:_unavailable()
        selected=metadata["selection"]
        if type(selected) is not dict or set(selected)!=_SELECTION_KEYS:_unavailable()
        for key in ("connection_id","provider_code","provider_kind","adapter_type"):
            if selected[key]!="":safe_id(selected[key])
        if type(selected["model_id"]) is not str or re.fullmatch(r"[^\x00-\x20\x7f]{1,255}",selected["model_id"]) is None:_unavailable()
        for key in ("connection_version","catalog_version","model_settings_version"):
            if type(selected[key]) is not int or selected[key]<1:_unavailable()
        if type(selected["credential_version"]) is not int or selected["credential_version"]<0:_unavailable()
        binding=NotebookModelExecution(NotebookModelSelection(selected["connection_id"],selected["model_id"],selected["reasoning_effort"]),selected["model_settings_version"],selected["reasoning_profile_version"])
        if binding.as_dict()!=checked.intended_model_selection:_unavailable()
        for key in ("policy_fingerprint","payload_digest","evidence_digest"):_hex(metadata[key])
        _hex(selected["endpoint_digest"])
        sources=metadata["sources"]
        if type(sources) is not list or not 1<=len(sources)<=100:_unavailable()
        for item in sources:
            if type(item) is not dict or set(item)!=_SOURCE_KEYS:_unavailable()
            for key in ("source_id","source_version_id","index_version_id"):safe_id(item[key])
            for key in ("source_digest","index_digest"):_hex(item[key])
        if len({item["source_version_id"] for item in sources})!=len(sources) or set(request["source_version_ids"])!={item["source_version_id"] for item in sources}:_unavailable()
    except (TypeError,ValueError,KeyError):_unavailable()


class PostgresStudioExecutionRepository:
    def __init__(self,store,*,clock=None,workspace_repository=None):
        self.store=store;self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.workspace=workspace_repository or PostgresStudioWorkspaceRepository(store)

    @staticmethod
    def _cloud(context):return CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,"studio.create")

    @staticmethod
    def _lock(conn,context,key):
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("studio-execution|"+digest([context.tenant_id,context.workspace_id,context.actor_id,key]),))

    def _current(self,conn,context,request,metadata):
        # D saves lock the model aggregate before Notebook delete/unbind locks.
        # The caller already owns the Studio execution-scope lock.
        selected=request["intended_model_selection"]
        binding=NotebookModelExecution(NotebookModelSelection(selected["connection_id"],selected["model_id"],selected["reasoning_effort"]),selected["model_settings_version"],selected["reasoning_profile_version"])
        scoped=SimpleNamespace(**{name:getattr(context,name) for name in context.__dataclass_fields__},model_settings=binding)
        try:assert_execution_current(conn,scoped)
        except NotebookModelSettingsError:
            raise StudioError("STUDIO_MODEL_CHANGED",409) from None
        # Same current normal role/Notebook/Source rules as the existing Studio writer.
        self.workspace._require_generation_scope(conn,context,tuple(request["source_version_ids"]))
        self.workspace.require_typed_origin_current(conn,context,SimpleNamespace(**request))
        try:_current_auth_grant(context,conn,Action.EDIT)
        except OpenLawAccessError:raise StudioError("RESOURCE_UNAVAILABLE",404) from None
        session=conn.execute("SELECT /* studio-execution:session */ 1 FROM identity_sessions s JOIN identity_devices d "
            "ON (d.tenant_id,d.user_id,d.device_id)=(s.tenant_id,s.user_id,s.device_id) "
            "WHERE s.tenant_id=%s AND s.user_id=%s AND s.session_id=%s AND s.device_id=%s "
            "AND s.state='active' AND d.state='active' AND s.access_expires_at::timestamptz>%s",
            (context.tenant_id,context.actor_id,context.session_id,context.device_id,_utc(self.clock()))).fetchone()
        if session is None:raise StudioError("RESOURCE_UNAVAILABLE",404)
        expected=metadata["selection"]
        # Compare only safe metadata. No credential decryption or encrypted envelope read.
        # A Notebook setting can stay unchanged while an administrator rotates its target.
        current_model=conn.execute("SELECT /* studio-execution:model-metadata */ c.version,m.catalog_version,"
            "CASE WHEN c.access_mode='personal' THEN u.credential_version ELSE c.credential_version END,"
            "encode(sha256(convert_to(c.base_url,'UTF8')),'hex'),c.provider_code,c.adapter_type "
            "FROM system_provider_connections c JOIN system_provider_models m ON m.connection_id=c.connection_id "
            "JOIN system_provider_allowed_models a ON (a.connection_id,a.model_id)=(m.connection_id,m.model_id) "
            "LEFT JOIN user_provider_credentials u ON u.connection_id=c.connection_id AND u.tenant_id=%s AND u.user_id=%s "
            "AND u.verification_status='verified' AND u.credential_schema_version=2 "
            "WHERE c.connection_id=%s AND m.model_id=%s AND c.enabled=true AND m.catalog_status='ready' "
            "AND 'text_generation'=ANY(m.effective_capabilities) AND ((c.access_mode='public' AND c.verification_status='verified') "
            "OR (c.access_mode='personal' AND u.connection_id IS NOT NULL))",
            (context.tenant_id,context.actor_id,selected["connection_id"],selected["model_id"])).fetchone()
        if current_model is None or tuple(current_model)!=tuple(expected[name] for name in
                ("connection_version","catalog_version","credential_version","endpoint_digest","provider_code","adapter_type")):
            raise StudioError("STUDIO_MODEL_CHANGED",409)
        for source in metadata["sources"]:
            current=conn.execute("SELECT /* studio-execution:source */ s.record_id,sv.digest_sha256,i.record_id,i.digest_sha256 "
                "FROM source_versions sv JOIN sources s ON (s.tenant_id,s.workspace_id,s.record_id)=(sv.tenant_id,sv.workspace_id,sv.source_id) "
                "JOIN LATERAL(SELECT record_id,digest_sha256 FROM index_versions WHERE tenant_id=sv.tenant_id AND workspace_id=sv.workspace_id "
                "AND source_version_id=sv.record_id ORDER BY created_at DESC,record_id DESC LIMIT 1)i ON true "
                "WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND sv.record_id=%s AND s.state='ready'",
                (context.tenant_id,context.workspace_id,source["source_version_id"])).fetchone()
            if current is None or tuple(current)!=(source["source_id"],source["source_digest"],source["index_version_id"],source["index_digest"]):
                raise StudioError("STUDIO_SOURCE_CHANGED",409)
        if metadata["operation"]=="regenerate":
            self.workspace.require_typed_revision_current(conn,context,request["output_type"],metadata["output_id"],metadata["previous_version_id"])
        effective=self._effective_policy(conn,context)
        if effective.fingerprint!="sha256:"+metadata["policy_fingerprint"]:
            raise StudioError("STUDIO_POLICY_CHANGED",409)
        return effective

    @staticmethod
    def _effective_policy(conn,context):
        from .egress_policy import EgressPolicyContext,EgressPolicyService
        from .egress_policy_postgres import PostgresEgressPolicyRepository
        policy_context=EgressPolicyContext(context.tenant_id,context.tenant_id,context.workspace_id,context.actor_id,context.trace_id,context.policy_version)
        class TransactionPolicyReader:
            def current(self,ctx,scope):
                return PostgresEgressPolicyRepository._view(PostgresEgressPolicyRepository._select_current(
                    conn,ctx,scope,None if scope=="organization" else ctx.workspace_id,for_update=True))
        return EgressPolicyService(TransactionPolicyReader()).get_effective(policy_context)

    @staticmethod
    def _read(conn,context,record_id):
        row=conn.execute("SELECT /* studio-execution:plan-read */ canonical_json,canonical_text,digest_sha256,created_by "
            "FROM generation_settings_snapshots WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",
            (context.tenant_id,context.workspace_id,record_id)).fetchone()
        if row is None:return None
        payload,text,checksum,actor=row
        if actor!=context.actor_id or type(payload) is not dict or canonical_json_bytes(payload).decode()!=text or hashlib.sha256(text.encode()).hexdigest()!=checksum:_unavailable()
        return payload

    @staticmethod
    def _insert(conn,context,record_id,payload):
        text=canonical_json_bytes(payload).decode();checksum=hashlib.sha256(text.encode()).hexdigest()
        created=conn.execute("INSERT /* studio-execution:plan-insert */ INTO generation_settings_snapshots "
            "(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,canonical_json,canonical_text,digest_sha256,created_by,trace_id) "
            "VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING RETURNING record_id",
            (context.tenant_id,context.workspace_id,record_id,record_id,Jsonb(payload),text,checksum,context.actor_id,context.trace_id)).fetchone()
        if created is None and PostgresStudioExecutionRepository._read(conn,context,record_id)!=payload:
            raise StudioError("IDEMPOTENCY_CONFLICT",409)

    def read_replay(self,context,key,*,request=None,revision_type="initial",change_reason=None,output_id=None,previous_version_id=None,settings=None,model_settings_version=None):
        """실제로 접수된 같은 요청의 영수증만 현재 권한 아래 읽는다. 새 승인/전송이 아니다."""
        safe_id(key)
        root=digest([context.tenant_id,context.workspace_id,context.actor_id,key])[:48]
        with self.store._transaction(self._cloud(context)) as conn:
            self._lock(conn,context,key)
            plan=self._read(conn,context,"studio-plan-"+root)
            if plan is None:return None
            if set(plan)!=_PLAN_KEYS or plan.get("kind")!="studio_execution_plan":_unavailable()
            validate_inputs(plan["request"],plan["metadata"])
            if plan.get("schema_version")!=1 or plan.get("plan_id")!="studio-plan-"+root or plan["fingerprint"]!=digest({name:plan[name] for name in ("scope","request","metadata","key_digest")}):_unavailable()
            if plan["scope"]!=context.scope() or plan["key_digest"]!=digest(key):raise StudioError("IDEMPOTENCY_CONFLICT",409)
            expected={"revision_type":revision_type,"change_reason":change_reason,"output_id":output_id,"previous_version_id":previous_version_id}
            if any(plan["metadata"].get(k)!=v for k,v in expected.items()):raise StudioError("IDEMPOTENCY_CONFLICT",409)
            if request is not None and plan["request"]!=request:raise StudioError("IDEMPOTENCY_CONFLICT",409)
            if model_settings_version is not None and plan["request"]["intended_model_selection"]["model_settings_version"]!=model_settings_version:
                raise StudioError("IDEMPOTENCY_CONFLICT",409)
            if settings is not None and (type(settings) is not dict or settings!={name:plan["request"][name] for name in
                ("purpose","audience","language","source_version_ids","ruleset_version_id","length","structure","output_format","review_condition")}):
                raise StudioError("IDEMPOTENCY_CONFLICT",409)
            job=conn.execute("SELECT /* studio-execution:job-replay-read */ request_json,state,actor_id,studio_output_id,output_version_id "
                "FROM studio_generation_jobs WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s",
                (context.tenant_id,context.workspace_id,"studio-job-"+root)).fetchone()
            if job is None:return None
            if len(job)!=5 or job[1] not in {"queued","leased","generating","completed","failed","unavailable"}:_unavailable()
            payload={**plan["request"],"notebook_id":context.notebook_id,"execution_plan_id":plan["plan_id"]}
            admission=self._read(conn,context,plan["plan_id"]+"-admitted")
            if job[0]!=payload or job[2]!=context.actor_id or admission is None or admission!={"kind":"studio_execution_admission",
                "plan_id":plan["plan_id"],"plan_fingerprint":plan["fingerprint"],"scope":context.scope(),"authorization_mode":admission.get("authorization_mode"),"key_digest":digest(key)} or admission.get("authorization_mode") not in {"step_up","policy_approved"}:
                raise StudioError("IDEMPOTENCY_CONFLICT",409)
            metadata=plan["metadata"]
            if metadata["operation"]=="regenerate" and job[1]=="completed":
                if job[3]!=metadata["output_id"]:raise StudioError("IDEMPOTENCY_CONFLICT",409)
                safe_id(job[4]);metadata={**metadata,"previous_version_id":job[4]}
            self._current(conn,context,plan["request"],metadata)
            return plan,{"job_id":"studio-job-"+root,"status":job[1],"output_type":plan["request"]["output_type"],
                "studio_output_id":job[3],"output_version_id":job[4]}

    def prepare(self,context,request,key,metadata):
        safe_id(key);validate_inputs(request,metadata)
        _utc(self.clock());identity={"scope":context.scope(),"request":request,"metadata":metadata,"key_digest":digest(key)}
        fingerprint=digest(identity);plan_id="studio-plan-"+digest([context.tenant_id,context.workspace_id,context.actor_id,key])[:48]
        with self.store._transaction(self._cloud(context)) as conn:
            self._lock(conn,context,key);self._current(conn,context,request,metadata)
            old=self._read(conn,context,plan_id)
            if old is not None:
                if set(old)!=_PLAN_KEYS or old.get("kind")!="studio_execution_plan" or old.get("schema_version")!=1 or old.get("plan_id")!=plan_id:_unavailable()
                if any(old.get(k)!=v for k,v in identity.items()) or old.get("fingerprint")!=fingerprint:
                    raise StudioError("IDEMPOTENCY_CONFLICT",409)
                if _utc(datetime.fromisoformat(old["expires_at"]))<=_utc(self.clock()):raise StudioError("STUDIO_AUTHORIZATION_EXPIRED",409)
                return old
            now=_utc(self.clock());plan={"kind":"studio_execution_plan","schema_version":1,**identity,"fingerprint":fingerprint,"plan_id":plan_id,
                "created_at":now.isoformat(),"expires_at":(now+timedelta(seconds=300)).isoformat()}
            self._insert(conn,context,plan_id,plan)
        return plan

    def enqueue(self,context,plan,key,*,authorization_mode):
        safe_id(key)
        if authorization_mode not in {"policy_approved","step_up"}:_unavailable()
        if type(plan) is not dict or set(plan)!=_PLAN_KEYS or plan["scope"]!=context.scope() or plan["key_digest"]!=digest(key):_unavailable()
        validate_inputs(plan["request"],plan["metadata"])
        job_id="studio-job-"+digest([context.tenant_id,context.workspace_id,context.actor_id,key])[:48]
        with self.store._transaction(self._cloud(context)) as conn:
            self._lock(conn,context,key);self._current(conn,context,plan["request"],plan["metadata"])
            if self._read(conn,context,plan["plan_id"])!=plan:raise StudioError("IDEMPOTENCY_CONFLICT",409)
            payload={**plan["request"],"notebook_id":context.notebook_id,"execution_plan_id":plan["plan_id"]}
            old=conn.execute("SELECT /* studio-execution:job-read */ request_json,state,actor_id FROM studio_generation_jobs "
                "WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s",(context.tenant_id,context.workspace_id,job_id)).fetchone()
            if old is not None:
                if tuple(old)[0]!=payload or old[2]!=context.actor_id:raise StudioError("IDEMPOTENCY_CONFLICT",409)
                return {"job_id":job_id,"status":old[1],"output_type":plan["request"]["output_type"]},True
            if _utc(datetime.fromisoformat(plan["expires_at"]))<=_utc(self.clock()):raise StudioError("STUDIO_AUTHORIZATION_EXPIRED",409)
            # Immutable Cloud admission + job commit together. Identity consumption is a separate transaction.
            self._insert(conn,context,plan["plan_id"]+"-admitted",{"kind":"studio_execution_admission","plan_id":plan["plan_id"],"plan_fingerprint":plan["fingerprint"],
                "scope":context.scope(),"authorization_mode":authorization_mode,"key_digest":digest(key)})
            conn.execute("INSERT /* studio-execution:job-insert */ INTO studio_generation_jobs "
                "(tenant_id,workspace_id,job_id,request_json,actor_id,trace_id,policy_version,idempotency_key,state) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued') RETURNING job_id",
                (context.tenant_id,context.workspace_id,job_id,Jsonb(payload),context.actor_id,context.trace_id,context.policy_version,key)).fetchone()
        return {"job_id":job_id,"status":"queued","output_type":plan["request"]["output_type"],"title":plan["request"]["purpose"],"created_at":_utc(self.clock())},False


    @staticmethod
    def _ids(context,plan):
        root=plan["fingerprint"]
        ids={name:"studio-"+name+"-"+digest([context.tenant_id,context.workspace_id,root,name])[:40]
             for name in ("run","scope","scope-snapshot","run-snapshot","routing-policy","routing","egress","start","terminal","attempt","result")}
        selection=plan["metadata"]["selection"]
        ids["artifact"]="studio-artifact-"+digest([context.tenant_id,context.workspace_id,selection["provider_code"],selection["model_id"]])[:40]
        ids["profile"]="studio-profile-"+digest([context.tenant_id,context.workspace_id,selection["connection_id"],selection["connection_version"]])[:40]
        ids["deployment"]="studio-deployment-"+digest([ids["profile"],ids["artifact"]])[:40]
        return ids

    def load_job(self,job):
        """현재 공개 접근 허가가 아닌 내부 불변 admission의 소유 범위만 복원한다."""
        scope=SimpleNamespace(tenant_id=job.tenant_id,workspace_id=job.workspace_id,actor_id=job.actor_id)
        plan_id=job.request_json.get("execution_plan_id");safe_id(plan_id)
        with self.store._transaction(CloudAccessContext(job.tenant_id,job.workspace_id,job.actor_id,"studio.create")) as conn:
            plan=self._read(conn,scope,plan_id)
        if (type(plan) is not dict or set(plan)!=_PLAN_KEYS or plan["kind"]!="studio_execution_plan" or plan["schema_version"]!=1
            or plan["plan_id"]!=plan_id or plan["key_digest"]!=digest(job.idempotency_key)
            or plan["scope"].get("tenant_id")!=job.tenant_id or plan["scope"].get("workspace_id")!=job.workspace_id
            or plan["scope"].get("actor_id")!=job.actor_id or plan["scope"].get("notebook_id")!=job.request_json.get("notebook_id")):_unavailable()
        validate_inputs(plan["request"],plan["metadata"])
        expected={**plan["request"],"notebook_id":plan["scope"]["notebook_id"],"execution_plan_id":plan_id}
        if dict(job.request_json)!=expected:raise StudioError("IDEMPOTENCY_CONFLICT",409)
        context=StudioExecutionContext(**plan["scope"],trace_id=job.trace_id,policy_version=job.policy_version)
        return context,plan

    def has_started(self,context,plan,*,job_id):
        ids=self._ids(context,plan)
        with self.store._transaction(self._cloud(context)) as conn:
            start=self._canon_read(conn,context,"run_steps",ids["start"],("run_id",))
            if start is None:return False
            if (start[3]!=context.actor_id or start[4]!=ids["run"] or start[0].get("plan_fingerprint")!=plan["fingerprint"]
                or start[0].get("job_id")!=job_id or canonical_json_bytes(start[0]).decode()!=start[1]
                or hashlib.sha256(start[1].encode()).hexdigest()!=start[2]):_unavailable()
            return True

    @staticmethod
    def _canon_read(conn,context,table,record_id,extras=()):
        allowed={"knowledge_scopes","scope_snapshots","provider_profiles","model_artifacts","model_deployments","routing_policy_versions",
                 "runs","egress_decisions","routing_decisions","run_snapshots","run_steps","model_attempts","run_results"}
        if table not in allowed or any(re.fullmatch(r"[a-z_]+",key) is None for key in extras):_unavailable()
        columns="canonical_json,canonical_text,digest_sha256,created_by"+"".join(","+key for key in extras)
        return conn.execute("SELECT /* studio-execution:canon-read */ "+columns+" FROM "+table+
            " WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",(context.tenant_id,context.workspace_id,record_id)).fetchone()

    @classmethod
    def _canon(cls,conn,context,table,record_id,payload,*,state=None,extras=None):
        extra=dict(extras or {})
        # Dynamic table/column names are strictly internal and allowlisted by the reader above.
        existing=cls._canon_read(conn,context,table,record_id,tuple(extra))
        text=canonical_json_bytes(payload).decode();checksum=hashlib.sha256(text.encode()).hexdigest()
        def matches(row):
            return row is not None and row[0]==payload and row[1]==text and row[2]==checksum and (
                table in {"provider_profiles","model_artifacts","model_deployments"} or row[3]==context.actor_id) and tuple(row[4:])==tuple(extra.values())
        if existing is not None:
            if not matches(existing):raise StudioError("IDEMPOTENCY_CONFLICT",409)
            return False
        values={"tenant_id":context.tenant_id,"workspace_id":context.workspace_id,"record_id":record_id,"aggregate_id":record_id,
                "version":1,"schema_version":1,"canonical_json":Jsonb(payload),"canonical_text":text,"digest_sha256":checksum,
                "created_by":context.actor_id,"trace_id":context.trace_id,**({"state":state} if state else {}),**extra}
        row=conn.execute("INSERT /* studio-execution:canon-insert */ INTO "+table+" ("+",".join(values)+") VALUES ("+
            ",".join(["%s"]*len(values))+") ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING RETURNING record_id",tuple(values.values())).fetchone()
        if row is None and not matches(cls._canon_read(conn,context,table,record_id,tuple(extra))):
            raise StudioError("IDEMPOTENCY_CONFLICT",409)
        return row is not None

    def claim(self,context,plan,key,*,job_id,job_version):
        safe_id(key);safe_id(job_id)
        if type(job_version) is not int or job_version<1 or plan["scope"]!=context.scope() or plan["key_digest"]!=digest(key):_unavailable()
        ids=self._ids(context,plan)
        with self.store._transaction(self._cloud(context)) as conn:
            self._lock(conn,context,key)
            effective=self._current(conn,context,plan["request"],plan["metadata"])
            if self._read(conn,context,plan["plan_id"])!=plan:raise StudioError("IDEMPOTENCY_CONFLICT",409)
            admission=self._read(conn,context,plan["plan_id"]+"-admitted")
            if (admission is None or admission.get("kind")!="studio_execution_admission" or admission.get("scope")!=context.scope()
                or admission.get("plan_fingerprint")!=plan["fingerprint"] or admission.get("key_digest")!=digest(key)):_unavailable()
            job=conn.execute("SELECT /* studio-execution:job-claim-read */ request_json,state,actor_id,version FROM studio_generation_jobs "
                "WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s FOR UPDATE",
                (context.tenant_id,context.workspace_id,job_id)).fetchone()
            expected={**plan["request"],"notebook_id":context.notebook_id,"execution_plan_id":plan["plan_id"]}
            if job is None or tuple(job)!=(expected,"generating",context.actor_id,job_version):raise StudioError("STUDIO_JOB_LEASE_LOST",409)
            start=self._canon_read(conn,context,"run_steps",ids["start"])
            if start is not None:
                old=start[0]
                if old.get("kind")!="studio_provider_start" or old.get("plan_fingerprint")!=plan["fingerprint"] or old.get("job_id")!=job_id:_unavailable()
                return {"dispatch_owner":False,"ids":ids,"started_at":old["started_at"]}
            if _utc(datetime.fromisoformat(plan["expires_at"]))<=_utc(self.clock()):raise StudioError("STUDIO_AUTHORIZATION_EXPIRED",409)
            selection=plan["metadata"]["selection"];request=plan["request"]
            self._canon(conn,context,"knowledge_scopes",ids["scope"],{"kind":"studio_source_scope","source_version_ids":request["source_version_ids"]})
            self._canon(conn,context,"scope_snapshots",ids["scope-snapshot"],{"kind":"studio_source_scope","source_bindings":plan["metadata"]["sources"]},extras={"knowledge_scope_id":ids["scope"]})
            self._canon(conn,context,"provider_profiles",ids["profile"],{"configured_profile_id":selection["connection_id"],"provider_code":selection["provider_code"],"binding_version":selection["connection_version"]})
            self._canon(conn,context,"model_artifacts",ids["artifact"],{"provider_code":selection["provider_code"],"model_id":selection["model_id"]})
            self._canon(conn,context,"model_deployments",ids["deployment"],{"configured_deployment_id":selection["connection_id"]+":"+selection["model_id"],"model_id":selection["model_id"],"role":"text","binding_version":selection["connection_version"]},
                extras={"provider_profile_id":ids["profile"],"model_artifact_id":ids["artifact"]})
            self._canon(conn,context,"routing_policy_versions",ids["routing-policy"],{"kind":"studio_explicit_selection","candidate_order":[ids["deployment"]],"egress_policy_fingerprint":plan["metadata"]["policy_fingerprint"]})
            self._canon(conn,context,"runs",ids["run"],{"kind":"studio_execution","notebook_id":context.notebook_id,"job_id":job_id,"output_type":request["output_type"],
                "plan_id":plan["plan_id"],"plan_fingerprint":plan["fingerprint"],"originating_run_id":request["run_id"],"originating_run_result_id":request["run_result_id"]},state="accepted",extras={"conversation_id":None})
            self._canon(conn,context,"egress_decisions",ids["egress"],{"kind":"studio_execution","run_id":ids["run"],"allowed":True,
                "plan_fingerprint":plan["fingerprint"],"payload_digest":plan["metadata"]["payload_digest"],"policy_fingerprint":plan["metadata"]["policy_fingerprint"],"authorization_mode":admission["authorization_mode"]},
                extras={"run_id":ids["run"],"provider_profile_id":ids["profile"]})
            self._canon(conn,context,"routing_decisions",ids["routing"],{"kind":"studio_execution","run_id":ids["run"],"selected_deployment_id":ids["deployment"],"candidate_order":[ids["deployment"]]},
                extras={"run_id":ids["run"],"routing_policy_version_id":ids["routing-policy"],"egress_decision_id":ids["egress"]})
            self._canon(conn,context,"run_snapshots",ids["run-snapshot"],{"kind":"studio_execution","source_version_ids":request["source_version_ids"],"knowledge_scope_id":ids["scope"],
                "authority":["source"],"weights_requested":{},"weights_effective":{},"weight_clamps":[],"ruleset_snapshot_ids":[],
                "routing_policy_version_id":ids["routing-policy"],"candidate_order":[ids["deployment"]],"data_area":"workspace","data_classification":effective.classification,
                "egress_decision_id":ids["egress"],"user_policy_version":context.policy_version,"organization_policy_version":effective.organization_policy_version_id,
                "cost_limit":None,"currency":None,"prompt_version":"studio-selected-evidence-v1","tool_version":"studio-typed-v1",
                "notebook_model_settings":request["intended_model_selection"],"plan_id":plan["plan_id"]},
                extras={"run_id":ids["run"],"scope_snapshot_id":ids["scope-snapshot"],"routing_policy_version_id":ids["routing-policy"],"generation_settings_snapshot_id":plan["plan_id"]})
            state_version=1
            for state in ("planning","retrieving","generating"):
                state_version=self.workspace._transition(conn,context,"Run",ids["run"],state_version,state,"studio-transition-"+digest([ids["run"],state])[:40])
            now=_utc(self.clock()).isoformat()
            created=self._canon(conn,context,"run_steps",ids["start"],{"kind":"studio_provider_start","plan_fingerprint":plan["fingerprint"],"job_id":job_id,
                "started_at":now,"notebook_id":context.notebook_id,"dispatch_job_version":job_version},extras={"run_id":ids["run"]})
            if not created:_unavailable()
        # This commit is the only dispatch ownership evidence; it is not proof the Provider received bytes.
        return {"dispatch_owner":True,"ids":ids,"started_at":now}

    def finish(self,context,plan,key,*,job_id,job_version,provider_outcome,usage,
               reported_cost=None,safe_error_code=None,next_actions=(),publish=None,response_digest=None):
        """실제 호출의 회계와 현재 산출물 공개 권한을 분리한다.

        publish는 현재 검사를 통과한 경우에만 같은 connection/savepoint에서
        실행하는 내부 typed writer다. Provider HTTP나 원응답은 이 경계에 없다.
        """
        safe_id(key);safe_id(job_id)
        if (type(job_version) is not int or job_version<1 or provider_outcome not in {"succeeded","failed","cancelled"}
            or plan["scope"]!=context.scope() or plan["key_digest"]!=digest(key)):_unavailable()
        if safe_error_code not in {None,"STUDIO_OUTPUT_UNAVAILABLE","STUDIO_GENERATION_UNCERTAIN","STUDIO_PROVIDER_FAILED"}:_unavailable()
        if response_digest is not None:_hex(response_digest)
        if type(next_actions) not in (tuple,list) or len(next_actions)>4 or any(type(v) is not str or not 1<=len(v)<=120 for v in next_actions):_unavailable()
        counters={name:usage[name] for name in ("prompt_tokens","completion_tokens","total_tokens")
                  if type(usage) is dict and type(usage.get(name)) is int and 0<=usage[name]<=2**63-1}
        now=_utc(self.clock())
        receipt=make_cost_receipt(reported_cost,counters,provider_connection_id=plan["metadata"]["selection"]["connection_id"],
                                  outcome=provider_outcome,observed_at=now)
        observation=digest({"provider_outcome":provider_outcome,"usage":counters,"receipt_id":receipt.receipt_id,
                            "safe_error_code":safe_error_code,"next_actions":list(next_actions),"response_digest":response_digest})
        ids=self._ids(context,plan)
        with self.store._transaction(self._cloud(context)) as conn:
            self._lock(conn,context,key)
            if self._read(conn,context,plan["plan_id"])!=plan:raise StudioError("IDEMPOTENCY_CONFLICT",409)
            start=self._canon_read(conn,context,"run_steps",ids["start"],("run_id",))
            if (start is None or start[3]!=context.actor_id or start[4]!=ids["run"] or start[0].get("kind")!="studio_provider_start"
                or start[0].get("plan_fingerprint")!=plan["fingerprint"] or start[0].get("job_id")!=job_id
                or start[0].get("dispatch_job_version")!=job_version
                or canonical_json_bytes(start[0]).decode()!=start[1] or hashlib.sha256(start[1].encode()).hexdigest()!=start[2]):_unavailable()
            terminal=self._canon_read(conn,context,"run_steps",ids["terminal"],("run_id",))
            late=terminal is not None and terminal[0].get("kind")=="studio_execution_uncertain"
            if late:
                prior=terminal[0]
                if (terminal[3]!=context.actor_id or terminal[4]!=ids["run"] or prior.get("plan_fingerprint")!=plan["fingerprint"]
                    or prior.get("job_id")!=job_id or canonical_json_bytes(prior).decode()!=terminal[1]
                    or hashlib.sha256(terminal[1].encode()).hexdigest()!=terminal[2]):_unavailable()
                terminal=self._canon_read(conn,context,"run_steps",ids["terminal"]+"-observed",("run_id",))
            if terminal is not None:
                payload=terminal[0]
                if (terminal[3]!=context.actor_id or terminal[4]!=ids["run"] or payload.get("observation_digest")!=observation
                    or payload.get("plan_fingerprint")!=plan["fingerprint"] or payload.get("job_id")!=job_id
                    or canonical_json_bytes(payload).decode()!=terminal[1] or hashlib.sha256(terminal[1].encode()).hexdigest()!=terminal[2]):
                    raise StudioError("IDEMPOTENCY_CONFLICT",409)
                return payload["result"]
            job=conn.execute("SELECT /* studio-execution:job-claim-read */ request_json,state,actor_id,version FROM studio_generation_jobs "
                "WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s FOR UPDATE",(context.tenant_id,context.workspace_id,job_id)).fetchone()
            expected={**plan["request"],"notebook_id":context.notebook_id,"execution_plan_id":plan["plan_id"]}
            if (job is None or job[0]!=expected or job[2]!=context.actor_id or type(job[3]) is not int
                or job[3]<job_version or job[1] not in ({"failed"} if late else {"generating","leased"})):
                raise StudioError("STUDIO_JOB_LEASE_LOST",409)
            started=_utc(datetime.fromisoformat(start[0]["started_at"]))
            if now<started:_unavailable()
            lease_current=not late and job[1]=="generating" and job[3]==job_version
            self._canon(conn,context,"model_attempts",ids["attempt"],{"kind":"studio_execution","run_id":ids["run"],
                "plan_fingerprint":plan["fingerprint"],"provider_outcome":provider_outcome,"model_selection":plan["metadata"]["selection"]},
                extras={"routing_decision_id":ids["routing"],"model_deployment_id":ids["deployment"],"model_artifact_id":ids["artifact"],
                        "candidate_order":1,"started_at":started,"finished_at":now,"safe_error_code":safe_error_code,
                        "cost_amount":receipt.amount,"usage_json":Jsonb(counters)})
            record_cost_in_transaction(conn,context,ids["run"],receipt,model_attempt_id=ids["attempt"])
            code=safe_error_code if lease_current else "STUDIO_GENERATION_UNCERTAIN";output=None
            if provider_outcome=="succeeded" and code is None and publish is not None:
                # Savepoint rollback cannot erase the already observed call's accounting.
                try:
                    with conn.transaction():
                        self._current(conn,context,plan["request"],plan["metadata"])
                        self._canon(conn,context,"run_results",ids["result"],{"kind":"studio_execution","plan_fingerprint":plan["fingerprint"],"notebook_id":context.notebook_id},
                            extras={"run_id":ids["run"],"routing_decision_id":ids["routing"],"selected_model_attempt_id":ids["attempt"]})
                        output=publish(conn)
                        if (type(output) is not dict or set(output)!={"studio_output_id","output_version_id"}):_unavailable()
                        for value in output.values():safe_id(value)
                except StudioError as error:
                    output=None
                    code=error.code if error.code in {"STUDIO_MODEL_CHANGED","STUDIO_SOURCE_CHANGED","STUDIO_POLICY_CHANGED"} else (
                        "STUDIO_CURRENT_ACCESS_DENIED" if error.code=="RESOURCE_UNAVAILABLE" else "STUDIO_OUTPUT_SAVE_FAILED")
                except (PostgresError,CloudDatabaseError):
                    output=None;code="STUDIO_OUTPUT_SAVE_FAILED"
            elif code is None:code="STUDIO_GENERATION_UNCERTAIN"
            status="completed" if output is not None else "unavailable" if code=="STUDIO_OUTPUT_UNAVAILABLE" else "failed"
            result={"job_id":job_id,"status":status,"safe_error_code":code,"next_actions":list(next_actions),
                    "studio_output_id":None if output is None else output["studio_output_id"],
                    "output_version_id":None if output is None else output["output_version_id"]}
            if late:
                # Only the actual dispatch owner's observation can fill this slot.
                # It never rewrites the failed Run/job or publishes a late artifact.
                result=prior["result"]
                self._canon(conn,context,"run_steps",ids["terminal"]+"-observed",{"kind":"studio_execution_late_observation",
                    "plan_fingerprint":plan["fingerprint"],"job_id":job_id,"observation_digest":observation,
                    "finished_at":now.isoformat(),"result":result,"model_attempt_id":ids["attempt"]},extras={"run_id":ids["run"]})
                return result
            run=conn.execute("SELECT /* studio-execution:run-state */ state,version FROM runs WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s FOR UPDATE",
                             (context.tenant_id,context.workspace_id,ids["run"])).fetchone()
            if run is None or run[0]!="generating":_unavailable()
            version=run[1]
            if status=="completed":
                version=self.workspace._transition(conn,context,"Run",ids["run"],version,"validating","studio-transition-"+digest([ids["run"],"validating"])[:40])
            self.workspace._transition(conn,context,"Run",ids["run"],version,"completed" if status=="completed" else "failed",
                                       "studio-transition-"+digest([ids["run"],status])[:40])
            self._canon(conn,context,"run_steps",ids["terminal"],{"kind":"studio_execution_terminal","plan_fingerprint":plan["fingerprint"],
                "job_id":job_id,"observation_digest":observation,"finished_at":now.isoformat(),"result":result},extras={"run_id":ids["run"]})
            updated=conn.execute("UPDATE /* studio-execution:job-finish */ studio_generation_jobs SET state=%s,safe_error_code=%s,"
                "studio_output_id=%s,output_version_id=%s,completed_at=now(),lease_owner=NULL,lease_until=NULL,version=version+1 "
                "WHERE tenant_id=%s AND job_id=%s AND workspace_id=%s AND actor_id=%s AND state IN ('generating','leased') AND version=%s RETURNING job_id",
                (status,code,result["studio_output_id"],result["output_version_id"],context.tenant_id,job_id,context.workspace_id,context.actor_id,job[3])).fetchone()
            if updated is None:raise StudioError("STUDIO_JOB_LEASE_LOST",409)
        return result

    def mark_uncertain(self,context,plan,key,*,job_id,job_version):
        """재회수자는 전송하지 않고 시작만 확인된 Run의 불확정을 기록한다."""
        from dataclasses import replace
        safe_id(key);safe_id(job_id)
        if plan["scope"]!=context.scope() or plan["key_digest"]!=digest(key):_unavailable()
        ids=self._ids(context,plan);now=_utc(self.clock())
        with self.store._transaction(self._cloud(context)) as conn:
            self._lock(conn,context,key)
            if self._read(conn,context,plan["plan_id"])!=plan:raise StudioError("IDEMPOTENCY_CONFLICT",409)
            start=self._canon_read(conn,context,"run_steps",ids["start"],("run_id",))
            if (start is None or start[3]!=context.actor_id or start[4]!=ids["run"]
                or start[0].get("plan_fingerprint")!=plan["fingerprint"] or start[0].get("job_id")!=job_id
                or canonical_json_bytes(start[0]).decode()!=start[1] or hashlib.sha256(start[1].encode()).hexdigest()!=start[2]):_unavailable()
            terminal=self._canon_read(conn,context,"run_steps",ids["terminal"],("run_id",))
            if terminal is not None:
                if (terminal[3]!=context.actor_id or terminal[4]!=ids["run"] or terminal[0].get("plan_fingerprint")!=plan["fingerprint"]
                    or terminal[0].get("job_id")!=job_id or canonical_json_bytes(terminal[0]).decode()!=terminal[1]
                    or hashlib.sha256(terminal[1].encode()).hexdigest()!=terminal[2]):_unavailable()
                return terminal[0]["result"]
            job=conn.execute("SELECT /* studio-execution:job-claim-read */ request_json,state,actor_id,version FROM studio_generation_jobs "
                "WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s FOR UPDATE",(context.tenant_id,context.workspace_id,job_id)).fetchone()
            expected={**plan["request"],"notebook_id":context.notebook_id,"execution_plan_id":plan["plan_id"]}
            if job is None or job[0]!=expected or job[1] not in {"generating","leased"} or job[2]!=context.actor_id or job[3]!=job_version:
                raise StudioError("STUDIO_JOB_LEASE_LOST",409)
            result={"job_id":job_id,"status":"failed","safe_error_code":"STUDIO_GENERATION_UNCERTAIN",
                    "next_actions":["생성 요청의 처리 여부를 확인해 주세요."],"studio_output_id":None,"output_version_id":None}
            # A start is not proof a Provider received bytes. Never invent an actual Attempt.
            receipt=replace(make_cost_receipt(None,{},provider_connection_id=plan["metadata"]["selection"]["connection_id"],
                outcome="failed",observed_at=now),receipt_id="studio-start-"+digest(plan["fingerprint"])[:48])
            record_cost_in_transaction(conn,context,ids["run"],receipt,model_attempt_id=None)
            run=conn.execute("SELECT /* studio-execution:run-state */ state,version FROM runs WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s FOR UPDATE",
                             (context.tenant_id,context.workspace_id,ids["run"])).fetchone()
            if run is None or run[0]!="generating":_unavailable()
            self.workspace._transition(conn,context,"Run",ids["run"],run[1],"failed","studio-transition-"+digest([ids["run"],"uncertain"])[:40])
            self._canon(conn,context,"run_steps",ids["terminal"],{"kind":"studio_execution_uncertain","plan_fingerprint":plan["fingerprint"],
                "job_id":job_id,"finished_at":now.isoformat(),"result":result},extras={"run_id":ids["run"]})
            updated=conn.execute("UPDATE /* studio-execution:job-finish */ studio_generation_jobs SET state=%s,safe_error_code=%s,"
                "studio_output_id=%s,output_version_id=%s,completed_at=now(),lease_owner=NULL,lease_until=NULL,version=version+1 "
                "WHERE tenant_id=%s AND job_id=%s AND workspace_id=%s AND actor_id=%s AND state IN ('generating','leased') AND version=%s RETURNING job_id",
                ("failed",result["safe_error_code"],None,None,context.tenant_id,job_id,context.workspace_id,context.actor_id,job_version)).fetchone()
            if updated is None:raise StudioError("STUDIO_JOB_LEASE_LOST",409)
        return result
