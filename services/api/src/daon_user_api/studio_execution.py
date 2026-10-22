"""명시 Studio 실행 범위와 정상 인증에 결속된 내부 서비스."""
from __future__ import annotations
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import re
from .data_canon import canonical_json_bytes
from .studio_workspace import StudioContext,StudioError


from .document_understanding_adapter import UrlLibDocumentUnderstandingTransport


class _StudioCanonicalWireTransport(UrlLibDocumentUnderstandingTransport):
    """기존 HTTP 보안 규칙을 유지하며 승인된 Studio 본문 바이트만 전송한다."""
    def __init__(self,transport,approved_bytes):
        import json
        if type(approved_bytes) is not bytes or not 1<=len(approved_bytes)<=65536:
            raise StudioError("EGRESS_POLICY_DENIED",403)
        self._transport=transport
        self._approved=approved_bytes
        self._payload=json.loads(approved_bytes)

    def _request(self,request,timeout_seconds):
        import json
        if request.get_method()!="POST" or json.loads(request.data)!=self._payload:
            raise StudioError("STUDIO_INPUT_CHANGED",409)
        request.data=self._approved
        return self._transport._request(request,timeout_seconds)


def digest(value):
    return hashlib.sha256(canonical_json_bytes({"studio_execution":value})).hexdigest()


def safe_id(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",value) is None:
        raise StudioError("STUDIO_INPUT_INVALID")
    return value


@dataclass(frozen=True,slots=True)
class StudioExecutionContext:
    tenant_id:str
    workspace_id:str
    actor_id:str
    trace_id:str
    policy_version:str
    notebook_id:str
    session_id:str
    device_id:str

    def __post_init__(self):
        for name in self.__dataclass_fields__:safe_id(getattr(self,name))

    def scope(self):
        return {name:getattr(self,name) for name in ("tenant_id","workspace_id","actor_id","notebook_id","session_id","device_id")}

    def studio(self):
        return StudioContext(self.tenant_id,self.workspace_id,self.actor_id,self.trace_id,self.policy_version,self.notebook_id)


class StudioExecutionService:
    """정상 준비 port와 단회 저장소를 조립한다. worker의 자동 재전송은 없다."""
    OPERATION="studio.generate.external_transfer"

    def __init__(self,repository,input_port,identity):
        self.repository,self.input_port,self.identity=repository,input_port,identity

    def revision_request(self,context,output_id,previous_id,settings,*,key,revision_type,change_reason,model_settings_version):
        from .studio_workspace import StudioGenerationRequest
        restored=self.repository.read_replay(context,key,revision_type=revision_type,change_reason=change_reason,output_id=output_id,
            previous_version_id=previous_id,settings=settings,model_settings_version=model_settings_version)
        if restored is not None:
            values={**restored[0]["request"],"intended_model_selection":None}
            values["source_version_ids"]=tuple(values["source_version_ids"])
            return StudioGenerationRequest(**values)
        return self.repository.workspace.load_revision_input(context.studio(),output_id,previous_id,settings)

    @staticmethod
    def _request(request):
        from dataclasses import asdict
        from .studio_workspace import StudioGenerationRequest
        if type(request) is not StudioGenerationRequest or request.intended_model_selection is None:
            raise StudioError("NOTEBOOK_MODEL_SELECTION_REQUIRED",409)
        payload=asdict(request);payload["source_version_ids"]=list(request.source_version_ids)
        return payload

    def _plan(self,context,request,key,prepared,*,revision_type,change_reason,output_id,previous_version_id):
        metadata={**prepared.metadata,"operation":"generate" if revision_type=="initial" else "regenerate",
                  "revision_type":revision_type,"change_reason":change_reason,"output_id":output_id,"previous_version_id":previous_version_id}
        return self.repository.prepare(context,self._request(request),key,metadata)

    @staticmethod
    def _target(plan):return "studio-generation-"+plan["fingerprint"]

    def authorize(self,context,request,key,*,access_token,password,revision_type="initial",change_reason=None,output_id=None,previous_version_id=None):
        self._request(request);safe_id(key)
        with self.input_port.open(context,request) as prepared:
            plan=self._plan(context,request,key,prepared,revision_type=revision_type,change_reason=change_reason,output_id=output_id,previous_version_id=previous_version_id)
            if not prepared.requires_step_up:
                return {"step_up_authorization_id":None,"expires_at":plan["expires_at"],"generation_fingerprint":plan["fingerprint"]}
            if not password:
                raise StudioError("STEP_UP_REQUIRED",401)
            grant=self.identity.issue_step_up_after_reauthentication(access_token=access_token,password=password,
                action_group="external_transfer",target_id=self._target(plan),policy_version="sha256:"+plan["metadata"]["policy_fingerprint"],
                trace_id=context.trace_id,ttl_seconds=300,idempotency_key=key)
            return {"step_up_authorization_id":grant.authorization,"expires_at":grant.expires_at,
                    "generation_fingerprint":plan["fingerprint"]}

    def enqueue(self,context,request,key,*,access_token,step_up_authorization_id=None,revision_type="initial",change_reason=None,output_id=None,previous_version_id=None):
        payload=self._request(request);safe_id(key)
        replay=self.repository.read_replay(context,key,request=payload,revision_type=revision_type,change_reason=change_reason,output_id=output_id,previous_version_id=previous_version_id)
        if replay is not None:return replay[1],True
        with self.input_port.open(context,request) as prepared:
            plan=self._plan(context,request,key,prepared,revision_type=revision_type,change_reason=change_reason,output_id=output_id,previous_version_id=previous_version_id)
            mode="policy_approved"
            if prepared.requires_step_up:
                self.identity.consume_step_up(step_up_authorization=step_up_authorization_id,access_token=access_token,
                    action_group="external_transfer",target_id=self._target(plan),policy_version="sha256:"+plan["metadata"]["policy_fingerprint"],
                    trace_id=context.trace_id,operation=self.OPERATION,idempotency_key=key)
                mode="step_up"
            elif step_up_authorization_id is not None:
                raise StudioError("STUDIO_AUTHORIZATION_INVALID",409)
            # Identity's separate transaction is not a distributed Cloud commit.
            return self.repository.enqueue(context,plan,key,authorization_mode=mode)

    def process(self,job):
        import json
        from .studio_workspace import StudioGenerationRequest
        from .studio_typed_generation import parse_typed_output,extract_typed_response,observe_provider_usage
        from .document_processing import DocumentUnderstandingError
        context,plan=self.repository.load_job(job)
        if self.repository.has_started(context,plan,job_id=job.job_id):
            # Cleanup must remain possible even when the original session/Source is revoked.
            return self.repository.mark_uncertain(context,plan,job.idempotency_key,job_id=job.job_id,job_version=job.version)
        request=StudioGenerationRequest(**{**plan["request"],"source_version_ids":tuple(plan["request"]["source_version_ids"])})
        with self.input_port.open(context,request) as prepared:
            current={**prepared.metadata,**{name:plan["metadata"][name] for name in ("operation","revision_type","change_reason","output_id","previous_version_id")}}
            if current!=plan["metadata"]:raise StudioError("STUDIO_INPUT_CHANGED",409)
            claim=self.repository.claim(context,plan,job.idempotency_key,job_id=job.job_id,job_version=job.version)
            if not claim["dispatch_owner"]:
                return self.repository.mark_uncertain(context,plan,job.idempotency_key,job_id=job.job_id,job_version=job.version)
            usage={};reported=None;typed=None;code=None;outcome="failed";actions=();response_digest=None
            try:
                response=prepared.send()  # start committed; no database transaction is held here.
                outcome="succeeded"
                response_digest=hashlib.sha256(json.dumps(response,ensure_ascii=True,sort_keys=True,separators=(",",":")).encode("ascii")).hexdigest()
                usage,reported=observe_provider_usage(prepared.selection.provider_code,response)
                extracted=extract_typed_response(prepared.selection.provider_code,response,adapter_type=prepared.selection.adapter_type)
                typed=parse_typed_output(request,extracted.raw,prepared.evidence,request_id=plan["plan_id"],model_id=prepared.selection.model_id)
                if typed.insufficient:code="STUDIO_OUTPUT_UNAVAILABLE";actions=typed.next_actions
            except StudioError:
                code="STUDIO_OUTPUT_UNAVAILABLE"
                actions=("선택한 자료를 보완해 주세요.",)
            except (OSError,TimeoutError,ValueError,DocumentUnderstandingError):
                code="STUDIO_GENERATION_UNCERTAIN";actions=("생성 요청의 처리 여부를 확인해 주세요.",)
            publish=None
            if typed is not None and not typed.insufficient and code is None:
                publish=lambda conn:self.repository.workspace.persist_typed_generation(conn,context,request,job.idempotency_key,typed,
                    prepared.evidence,execution_ids=claim["ids"],metadata=plan["metadata"])
            return self.repository.finish(context,plan,job.idempotency_key,job_id=job.job_id,job_version=job.version,
                provider_outcome=outcome,usage=usage,reported_cost=reported,safe_error_code=code,next_actions=actions,publish=publish,response_digest=response_digest)

    def recover_uncertain(self,job):
        context,plan=self.repository.load_job(job)
        if not self.repository.has_started(context,plan,job_id=job.job_id):return None
        return self.repository.mark_uncertain(context,plan,job.idempotency_key,job_id=job.job_id,job_version=job.version)


@dataclass(slots=True,repr=False)
class StudioPreparedInput:
    metadata:dict
    selection:object
    evidence:tuple
    requires_step_up:bool
    send:object


class PostgresStudioInputPort:
    """정상 모델 resolver/현재 원문 index/정책 변환/adapter 전송을 연결한다."""
    def __init__(self,repository,resolver,index,payload_transformer,transport,*,cost_sessions):
        self.repository,self.resolver,self.index=repository,resolver,index
        self.transformer,self.transport,self.cost_sessions=payload_transformer,transport,cost_sessions

    @contextmanager
    def open(self,context,request):
        import json
        from types import SimpleNamespace
        from urllib.parse import urlsplit
        from .authorization import Action,Role
        from .open_law_source_access import _current_auth_grant
        from .notebook_model_settings import NotebookModelExecution,NotebookModelSelection
        from .notebook_model_settings_postgres import assert_execution_current
        from .workspace_model_defaults import WorkspaceModelContext
        from .document_processing import DocumentProcessingContext
        from .question_egress import external_question_policy_matches
        from .question_answering import OpenAICompatibleTextGenerationAdapter,AnthropicMessagesTextGenerationAdapter,OllamaTextGenerationAdapter,_append_provider_path
        from .question_answering_service import _OmniRouteTextGenerationAdapter
        from .studio_typed_generation import build_typed_payload
        StudioExecutionService._request(request)
        intended=request.intended_model_selection
        binding=NotebookModelExecution(NotebookModelSelection(intended["connection_id"],intended["model_id"],intended["reasoning_effort"]),
                                       intended["model_settings_version"],intended["reasoning_profile_version"])
        scoped=SimpleNamespace(**{name:getattr(context,name) for name in context.__dataclass_fields__},model_settings=binding)
        sources=[];searches=[]
        with self.repository.store._transaction(self.repository._cloud(context)) as conn:
            assert_execution_current(conn,scoped)
            self.repository.workspace._require_generation_scope(conn,context,request.source_version_ids)
            self.repository.workspace.require_typed_origin_current(conn,context,request)
            grant=_current_auth_grant(context,conn,Action.EDIT)
            effective=self.repository._effective_policy(conn,context)
            for version in request.source_version_ids:
                row=conn.execute("SELECT /* studio-input:materials */ sv.source_id,sv.record_id,sv.digest_sha256,i.record_id,i.digest_sha256,i.canonical_json "
                    "FROM source_versions sv JOIN LATERAL(SELECT record_id,digest_sha256,canonical_json FROM index_versions "
                    "WHERE tenant_id=sv.tenant_id AND workspace_id=sv.workspace_id AND source_version_id=sv.record_id "
                    "ORDER BY created_at DESC,record_id DESC LIMIT 1)i ON true WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND sv.record_id=%s",
                    (context.tenant_id,context.workspace_id,version)).fetchone()
                if row is None or type(row[5]) is not dict or type(row[5].get("chunks")) is not list:
                    raise StudioError("STUDIO_OUTPUT_UNAVAILABLE",409)
                sources.append(dict(zip(("source_id","source_version_id","source_digest","index_version_id","index_digest"),row[:5])))
                # This query derives from current source text, never an earlier LLM answer.
                texts=[v.get("text") for v in row[5]["chunks"] if type(v) is dict]
                if any(type(v) is not str for v in texts):raise StudioError("STUDIO_OUTPUT_UNAVAILABLE",409)
                searches.append((row[0],version,"\n".join(texts)))
        processing=DocumentProcessingContext(context.tenant_id,context.workspace_id,context.actor_id,context.trace_id,context.policy_version)
        evidence=[]
        if len(searches)>10:raise StudioError("STUDIO_OUTPUT_UNAVAILABLE",409)
        for source,version,query in searches:
            chunks=self.index.search(processing,source_id=source,source_version_id=version,query=query,limit=max(1,10//len(searches)))
            for chunk in chunks:
                evidence.append({name:getattr(chunk,name) for name in ("chunk_id","source_id","source_version_id","page","text","evidence_span_id","locator_kind","locator_value")})
        if not evidence:raise StudioError("STUDIO_OUTPUT_UNAVAILABLE",409)
        model_context=WorkspaceModelContext(context.tenant_id,context.workspace_id,context.actor_id)
        with self.resolver.resolve(model_context,"text_generation",selected=binding) as selection:
            if self.cost_sessions is not None and any(self.cost_sessions.applies(context,selection,run_kind=kind) for kind in ("question","document")):
                # Read-only matches only; Studio is never charged to a question/document reservation.
                raise StudioError("STUDIO_COST_LIMIT_UNAVAILABLE",403)
            original=build_typed_payload(request,evidence,selection)
            transformed=self.transformer.prepare_payload(context,canonical_json_bytes(original))
            if type(transformed) is not bytes or len(transformed)>65536:raise StudioError("EGRESS_POLICY_DENIED",403)
            payload=json.loads(transformed)
            field="input" if "input" in payload else "messages"
            if set(payload)!=set(original) or payload["model"]!=selection.model_id:raise StudioError("STUDIO_INPUT_CHANGED",409)
            original_user=[v for v in original[field] if v["role"]=="user"]
            users=[v for v in payload[field] if type(v) is dict and v.get("role")=="user"]
            if len(users)!=1 or len(original_user)!=1 or type(users[0].get("content")) is not str:raise StudioError("STUDIO_INPUT_CHANGED",409)
            supplied=json.loads(users[0]["content"]);before=json.loads(original_user[0]["content"])
            if type(supplied) is not dict or set(supplied)!=set(before) or type(supplied.get("evidence")) is not list or len(supplied["evidence"])!=len(evidence):raise StudioError("STUDIO_INPUT_CHANGED",409)
            # Only the user text data may be transformed, never model/schema/limit/system roles.
            from copy import deepcopy
            expected=deepcopy(original)
            next(v for v in expected[field] if v["role"]=="user")["content"]=users[0]["content"]
            if expected!=payload:raise StudioError("STUDIO_INPUT_CHANGED",409)
            masked=[]
            for item,prior in zip(supplied["evidence"],evidence,strict=True):
                if (type(item) is not dict or set(item)!={"chunk_id","page","text"} or item["chunk_id"]!=prior["chunk_id"]
                    or type(item["page"]) is not int or item["page"]!=prior["page"] or type(item["text"]) is not str or not 1<=len(item["text"])<=8000):raise StudioError("STUDIO_INPUT_CHANGED",409)
                masked.append({**prior,"text":item["text"]})
            external=selection.provider_kind=="external_api"
            if external and not external_question_policy_matches(effective,provider_kind=selection.provider_kind,
                destination=(urlsplit(selection.base_url).hostname or "").casefold(),payload_bytes=len(transformed),classification="internal",masking_required=True,redaction_required=True):
                raise StudioError("EGRESS_POLICY_DENIED",403)
            required=external and getattr(effective,"question_step_up_required",True) is not False
            if required:
                allowed={Role.PERSONAL_OWNER,Role.WORKSPACE_ADMIN,Role.ORGANIZATION_ADMIN} if effective.required_approver=="workspace_manager" else {Role.ORGANIZATION_ADMIN}
                if grant.role not in allowed:raise StudioError("FORBIDDEN",403)
            selected={"connection_id":selection.connection_id,"model_id":selection.model_id,"provider_code":selection.provider_code,
                "provider_kind":selection.provider_kind,"adapter_type":selection.adapter_type,"connection_version":selection.default_version,
                "catalog_version":selection.catalog_version,"credential_version":selection.credential_version,"reasoning_effort":selection.reasoning_effort,
                "model_settings_version":selection.model_settings_version,"reasoning_profile_version":selection.reasoning_profile_version,
                "endpoint_digest":hashlib.sha256(selection.base_url.encode()).hexdigest()}
            metadata={"operation":"generate","revision_type":"initial","change_reason":None,"output_id":None,"previous_version_id":None,"selection":selected,
                      "policy_fingerprint":effective.fingerprint.removeprefix("sha256:"),"payload_digest":hashlib.sha256(transformed).hexdigest(),
                      "evidence_digest":digest(masked),"sources":sources}
            wire_transport=_StudioCanonicalWireTransport(self.transport,transformed) if isinstance(self.transport,UrlLibDocumentUnderstandingTransport) else self.transport
            adapter=None
            try:
                if selection.provider_code=="OLLAMA":
                    adapter=OllamaTextGenerationAdapter(transport=wire_transport)
                    send=lambda:wire_transport.post_json_no_auth(url=adapter._base_url(selection)+"/api/chat",payload=payload,timeout_seconds=adapter._generation_timeout(selection))
                elif selection.provider_code=="OMNIROUTE":
                    adapter=_OmniRouteTextGenerationAdapter(transport=wire_transport,api_key=selection.credential_text())
                    send=lambda:adapter._call(selection,payload)
                else:
                    cls=AnthropicMessagesTextGenerationAdapter if selection.provider_code=="CUSTOM" and selection.adapter_type=="anthropic_compatible" else OpenAICompatibleTextGenerationAdapter
                    adapter=cls(transport=wire_transport,api_key=selection.credential_text(required=selection.provider_code!="CUSTOM"))
                    if cls is AnthropicMessagesTextGenerationAdapter:
                        # Reuse its normal URL/header/timeout rules, but observe raw usage
                        # before the question adapter's body validation can reject it.
                        send=lambda:wire_transport.post_json_headers(url=_append_provider_path(adapter._base_url(selection),"/messages"),
                            headers=({"x-api-key":adapter._api_key,"anthropic-version":"2023-06-01"} if adapter._api_key is not None else {"anthropic-version":"2023-06-01"}),
                            payload=payload,timeout_seconds=adapter._timeout_seconds)
                    else:send=lambda:adapter._post(selection,payload)
                yield StudioPreparedInput(metadata,selection,tuple(masked),required,send)
            finally:
                if adapter is not None and hasattr(adapter,"_api_key"):adapter._api_key=None


def build_studio_execution_service(store,*,cipher,user_credentials,identity,policy_service,cost_sessions,
                                   transport=None,workspace_repository=None):
    """API와 worker가 함께 쓰는 정상 구성. 생성 시 DB/Provider에 접속하지 않는다."""
    from .studio_execution_postgres import PostgresStudioExecutionRepository
    from .workspace_model_defaults import PostgresWorkspaceModelResolver
    from .document_index_postgres import PostgresDocumentIndex
    from .question_egress import PostgresQuestionEgressAuthorizer
    from .document_understanding_adapter import UrlLibDocumentUnderstandingTransport
    repository=PostgresStudioExecutionRepository(store,workspace_repository=workspace_repository)
    port=PostgresStudioInputPort(repository,PostgresWorkspaceModelResolver(store,cipher,user_credentials),PostgresDocumentIndex(store),
        PostgresQuestionEgressAuthorizer(store,policy_service),transport or UrlLibDocumentUnderstandingTransport(),cost_sessions=cost_sessions)
    return StudioExecutionService(repository,port,identity)
