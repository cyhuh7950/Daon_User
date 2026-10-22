"""기존 immutable canonical 표에 본인 Notebook 모델 선택만 저장한다."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime,timezone
from psycopg.types.json import Jsonb
from .cloud_storage import CloudAccessContext,CloudDatabaseError
from .notebook import NotebookError
from .notebook_conversation_postgres import PostgresNotebookConversationRepository
from .notebook_model_settings import NotebookModelSelection,NotebookModelSettingsError,project_candidate,safe_id,validate_effort

_KIND="notebook_model_settings"
_KEYS={"kind","schema_version","tenant_id","workspace_id","actor_id","notebook_id","selection","profile_version","request_digest","key_hash"}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def scope_aggregate(context,notebook_id):
    values=[safe_id(v) for v in (context.tenant_id,context.workspace_id,context.actor_id,notebook_id)]
    return "notebook-model-"+digest(values)[:40]


def decode_snapshot(context,notebook_id,row):
    try:
        record,version,previous,payload,text,hash_value,actor,bound=row
        if (type(payload) is not dict or set(payload)!=_KEYS or payload["kind"]!=_KIND
            or type(payload["schema_version"]) is not int or payload["schema_version"]!=1
            or [payload[k] for k in ("tenant_id","workspace_id","actor_id","notebook_id")]
                !=[context.tenant_id,context.workspace_id,context.actor_id,notebook_id]
            or actor!=context.actor_id or bound is not True or type(version) is not int or version<1
            or canonical(payload)!=text or digest(payload)!=hash_value
            or record!=scope_aggregate(context,notebook_id)+"-"+str(version)
            or previous!=(None if version==1 else scope_aggregate(context,notebook_id)+"-"+str(version-1))
            or type(payload["selection"]) is not dict or set(payload["selection"])!={"connection_id","model_id","reasoning_effort"}
            or any(type(payload[k]) is not str or len(payload[k])!=64 or any(c not in "0123456789abcdef" for c in payload[k]) for k in ("request_digest","key_hash"))):
            raise ValueError
        selection=NotebookModelSelection(**payload["selection"])
        if payload["profile_version"] is not None: safe_id(payload["profile_version"])
        return {"record_id":record,"version":version,"selection":selection.as_dict(),"profile_version":payload["profile_version"],
            "request_digest":payload["request_digest"],"key_hash":payload["key_hash"]}
    except (ValueError,TypeError,KeyError,NotebookModelSettingsError):
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE",503) from None


class PostgresNotebookModelSettingsService:
    def __init__(self,store): self.store=store
    @staticmethod
    def _cloud(context):
        return CloudAccessContext(context.tenant_id,context.workspace_id,context.actor_id,"notebook_model_settings")
    @staticmethod
    def _guard(conn,context,notebook_id):
        try: PostgresNotebookConversationRepository._guard(conn,context,notebook_id)
        except NotebookError as error:
            raise NotebookModelSettingsError(error.code,error.status) from None
    @staticmethod
    def _rows(conn,context,notebook_id):
        return conn.execute("SELECT s.record_id,s.version,s.previous_version_id,s.canonical_json,s.canonical_text,s.digest_sha256,s.created_by,"
            "EXISTS(SELECT 1 FROM notebook_bindings b WHERE b.tenant_id=s.tenant_id AND b.workspace_id=s.workspace_id "
            "AND b.notebook_id=s.canonical_json->>'notebook_id' AND b.binding_kind='generation_settings' "
            "AND b.record_id=s.record_id AND b.version_id=s.record_id AND b.created_by=s.created_by) "
            "FROM generation_settings_snapshots s WHERE s.tenant_id=%s AND s.workspace_id=%s AND s.aggregate_id=%s "
            "AND s.created_by=%s ORDER BY s.version DESC",
            (context.tenant_id,context.workspace_id,scope_aggregate(context,notebook_id),context.actor_id)).fetchall()
    @staticmethod
    def _permissions(conn,context):
        from .authorization import Action,Permission
        from .open_law_source_access import _current_auth_grant,_CurrentAuthorizationRules,_AuthReadConnection,OpenLawAccessError
        try:
            grant=_current_auth_grant(context,conn,Action.QUERY)
            allowed=set()
            for kind,permission in (("local",Permission.LOCAL_INTERNAL_LLM),("external_api",Permission.EXTERNAL_LLM)):
                decision=_CurrentAuthorizationRules()._effective_permission(_AuthReadConnection(conn),role=grant.role,
                    tenant_id=context.tenant_id,workspace_id=context.workspace_id,permission=permission,requested=True)
                if decision.effective:allowed.add(kind)
            return allowed
        except OpenLawAccessError:
            raise NotebookModelSettingsError("NOTEBOOK_NOT_FOUND",404) from None

    @staticmethod
    def _candidates(conn,context):
        permissions=PostgresNotebookModelSettingsService._permissions(conn,context)
        rows=conn.execute("SELECT c.connection_id,c.provider_code,c.display_name,m.model_id,c.version,m.catalog_version,c.adapter_type,m.effective_capabilities,c.reasoning_contract "
            "FROM system_provider_connections c JOIN system_provider_models m ON m.connection_id=c.connection_id "
            "JOIN system_provider_allowed_models a ON (a.connection_id,a.model_id)=(m.connection_id,m.model_id) "
            "LEFT JOIN user_provider_credentials u ON u.connection_id=c.connection_id AND u.tenant_id=%s AND u.user_id=%s "
            "AND u.verification_status='verified' AND u.credential_schema_version=2 "
            "WHERE c.enabled=true AND m.catalog_status='ready' AND 'text_generation'=ANY(m.effective_capabilities) "
            "AND ((c.access_mode='public' AND c.verification_status='verified' AND "
            "(c.credential_requirement='none' OR (c.encrypted_credential IS NOT NULL AND c.credential_version>0))) "
            "OR (c.access_mode='personal' AND u.connection_id IS NOT NULL)) ORDER BY c.display_name,c.connection_id,m.model_id",
            (context.tenant_id,context.actor_id)).fetchall()
        result=[]
        for row in rows:
            view=project_candidate(row)
            if view is not None and ("local" if row[1]=="OLLAMA" else "external_api") in permissions:
                result.append(view)
        if len({(r["connection_id"],r["model_id"]) for r in result})!=len(result):
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE",503)
        return result
    @staticmethod
    def _selected(selection,candidates):
        row=next((v for v in candidates if (v["connection_id"],v["model_id"])==(selection["connection_id"],selection["model_id"])),None)
        if row is None: raise NotebookModelSettingsError("NOTEBOOK_MODEL_UNAVAILABLE",422)
        effort=selection["reasoning_effort"]
        if effort is not None and effort not in [v["value"] for v in row["reasoning"]["values"]]:
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_INVALID",422)
        return row
    def read(self,context,notebook_id):
        scope_aggregate(context,notebook_id)
        try:
            with self.store._transaction(self._cloud(context)) as conn:
                self._guard(conn,context,notebook_id)
                rows=self._rows(conn,context,notebook_id)
                current=None if not rows else decode_snapshot(context,notebook_id,rows[0])
                candidates=self._candidates(conn,context)
                effective=None
                if current is not None:
                    try:
                        candidate=self._selected(current["selection"],candidates)
                        if current["selection"]["reasoning_effort"] is not None and current["profile_version"]!=candidate["reasoning"]["profile_version"]:
                            raise NotebookModelSettingsError("NOTEBOOK_MODEL_UNAVAILABLE",422)
                        effective={**current["selection"],"origin":"notebook"}
                    except NotebookModelSettingsError as error:
                        if error.code not in ("NOTEBOOK_MODEL_UNAVAILABLE","NOTEBOOK_MODEL_SETTINGS_INVALID"):raise
                else:
                    default=conn.execute("SELECT connection_id,model_id FROM workspace_model_defaults WHERE tenant_id=%s AND workspace_id=%s AND capability='text_generation'",
                        (context.tenant_id,context.workspace_id)).fetchone()
                    if default is not None and any((v["connection_id"],v["model_id"])==tuple(default) for v in candidates):
                        effective={"connection_id":default[0],"model_id":default[1],"reasoning_effort":None,"origin":"workspace_default"}
                return {"notebook_id":notebook_id,"version":0 if current is None else current["version"],
                    "selection":None if current is None else current["selection"],"effective_selection":effective,"available_models":candidates}
        except CloudDatabaseError:
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE",503) from None
    def save(self,context,notebook_id,*,connection_id,model_id,reasoning_effort,expected_version,idempotency_key):
        selected=NotebookModelSelection(connection_id,model_id,reasoning_effort).as_dict()
        aggregate=scope_aggregate(context,notebook_id)
        safe_id(idempotency_key)
        if type(expected_version) is not int or expected_version<0:
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_INVALID")
        request_digest=digest({**selected,"expected_version":expected_version})
        key_hash=hashlib.sha256(idempotency_key.encode()).hexdigest()
        try:
            with self.store._transaction(self._cloud(context)) as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(aggregate,))
                self._guard(conn,context,notebook_id)
                candidates=self._candidates(conn,context)
                candidate=self._selected(selected,candidates)
                rows=self._rows(conn,context,notebook_id)
                decoded=[decode_snapshot(context,notebook_id,row) for row in rows]
                for old in decoded:
                    if old["key_hash"]==key_hash:
                        if old["request_digest"]!=request_digest or old["selection"]!=selected:
                            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_CONFLICT",409)
                        if selected["reasoning_effort"] is not None and old["profile_version"]!=candidate["reasoning"]["profile_version"]:
                            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_CHANGED",409)
                        return {"notebook_id":notebook_id,"version":old["version"],"selection":old["selection"],"replayed":True}
                current=decoded[0] if decoded else None
                if (0 if current is None else current["version"])!=expected_version:
                    raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_CHANGED",412)
                version=expected_version+1;record=aggregate+"-"+str(version)
                payload={"kind":_KIND,"schema_version":1,"tenant_id":context.tenant_id,"workspace_id":context.workspace_id,
                    "actor_id":context.actor_id,"notebook_id":notebook_id,"selection":selected,
                    "profile_version":None if reasoning_effort is None else candidate["reasoning"]["profile_version"],"request_digest":request_digest,"key_hash":key_hash}
                now=datetime.now(timezone.utc)
                conn.execute("INSERT INTO generation_settings_snapshots (tenant_id,workspace_id,record_id,aggregate_id,version,previous_version_id,schema_version,canonical_json,canonical_text,digest_sha256,created_by,trace_id,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s)",
                    (context.tenant_id,context.workspace_id,record,aggregate,version,None if current is None else current["record_id"],Jsonb(payload),canonical(payload),digest(payload),context.actor_id,context.trace_id,now))
                conn.execute("INSERT INTO notebook_bindings (tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES (%s,%s,%s,'generation_settings',%s,%s,%s,%s)",
                    (context.tenant_id,context.workspace_id,notebook_id,record,record,context.actor_id,now))
                return {"notebook_id":notebook_id,"version":version,"selection":selected,"replayed":False}
        except CloudDatabaseError:
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE",503) from None

    def require_execution(self,context,notebook_id,version):
        from .notebook_model_settings import NotebookModelExecution
        view=self.read(context,notebook_id)
        if type(version) is not int or version<1 or view["version"]!=version:
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_CHANGED",409)
        if view["selection"] is None or view["effective_selection"] is None:
            raise NotebookModelSettingsError("NOTEBOOK_MODEL_UNAVAILABLE",409)
        candidate=self._selected(view["selection"],view["available_models"])
        return NotebookModelExecution(NotebookModelSelection(**view["selection"]),version,None if view["selection"]["reasoning_effort"] is None else candidate["reasoning"]["profile_version"])


def assert_execution_current(conn,context):
    from .notebook_model_settings import NotebookModelExecution
    binding=context.model_settings
    if type(binding) is not NotebookModelExecution or context.notebook_id is None:
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_CHANGED",409)
    binding.__post_init__()
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(scope_aggregate(context,context.notebook_id),))
    PostgresNotebookModelSettingsService._guard(conn,context,context.notebook_id)
    rows=PostgresNotebookModelSettingsService._rows(conn,context,context.notebook_id)
    if not rows:raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_CHANGED",409)
    current=decode_snapshot(context,context.notebook_id,rows[0])
    if (current["version"]!=binding.version or current["selection"]!=binding.selection.as_dict()
            or binding.selection.reasoning_effort is not None and current["profile_version"]!=binding.profile_version):
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_CHANGED",409)
    candidate=PostgresNotebookModelSettingsService._selected(current["selection"],PostgresNotebookModelSettingsService._candidates(conn,context))
    if binding.selection.reasoning_effort is not None and candidate["reasoning"]["profile_version"]!=binding.profile_version:
        raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_CHANGED",409)
