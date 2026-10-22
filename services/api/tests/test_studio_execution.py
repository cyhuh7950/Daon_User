"""실 Studio coordinator+repository SQL 거래 대역. 실제 PG/Provider 증거 아님."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch
from daon_user_api.studio_execution import StudioExecutionService
from daon_user_api.studio_workspace import StudioGenerationRequest,StudioError
from daon_user_api.studio_execution_postgres import PostgresStudioExecutionRepository
from daon_user_api.studio_generation_queue import StudioGenerationJob
from test_studio_execution_postgres import Store,CTX,META,REQUEST,NOW


class InputPort:
 def __init__(self,store):
  self.store=store;self.calls=0;self.opens=0;self.reject=False;self.requires_step_up=False
  self.raw=json.dumps({"content":{"slides":[{"title":"원문","body":"원문 전체입니다.","evidence":["chunk-qa"]}]},"cited_chunk_ids":["chunk-qa"],"insufficient":False,"next_actions":[]},ensure_ascii=False)
 @contextmanager
 def open(self,context,request):
  self.opens+=1
  if self.reject:raise StudioError("RESOURCE_UNAVAILABLE",404)
  yield SimpleNamespace(metadata=deepcopy(META),requires_step_up=self.requires_step_up,selection=SimpleNamespace(provider_code="CUSTOM",adapter_type="openai_compatible",model_id="model-qa"),
   evidence=({"chunk_id":"chunk-qa","text":"원문 전체입니다.","page":1,"source_id":"source-qa","source_version_id":"version-qa","evidence_span_id":"span-qa"},),send=self.send)
 def send(self):
  if self.store.active:raise AssertionError("NETWORK_INSIDE_TRANSACTION")
  self.calls+=1
  return {"choices":[{"message":{"content":self.raw}}],"usage":{"prompt_tokens":14,"cost":"0.02","currency":"USD"}}


class StudioExecutionServiceTests(unittest.TestCase):
 def setUp(self):
  self.store=Store();self.repo=PostgresStudioExecutionRepository(self.store,clock=lambda:NOW)
  self.guard=patch.object(self.repo,"_current",return_value=SimpleNamespace(organization_policy_version_id="policy-qa",classification="internal"))
  self.guard.start();self.addCleanup(self.guard.stop)
  self.port=InputPort(self.store);self.identity=SimpleNamespace(consume_step_up=lambda **kw:self.fail("UNNECESSARY_STEPUP"))
  self.service=StudioExecutionService(self.repo,self.port,self.identity)
  self.request=StudioGenerationRequest(**{**REQUEST,"source_version_ids":tuple(REQUEST["source_version_ids"])})
 def job(self):
  result,_=self.service.enqueue(CTX,self.request,"studio-request-key-1",access_token="test-owned-memory-only")
  payload,state,actor=self.store.jobs[result["job_id"]]
  self.store.jobs[result["job_id"]]=(payload,"generating",actor)
  return StudioGenerationJob(CTX.tenant_id,CTX.workspace_id,result["job_id"],CTX.actor_id,CTX.trace_id,CTX.policy_version,"studio-request-key-1",payload,"generating",1,1)
 def test_committed_response_loss_replay_after_expiry_never_prepares_or_dispatches_again(self):
  from datetime import timedelta
  first,replayed=self.service.enqueue(CTX,self.request,"studio-request-key-1",access_token="test-owned-memory-only")
  self.assertFalse(replayed);opens=self.port.opens
  self.repo.clock=lambda:NOW+timedelta(seconds=301)
  self.port.reject=True
  recovered,replayed=self.service.enqueue(CTX,self.request,"studio-request-key-1",access_token="test-owned-memory-only")
  self.assertTrue(replayed);self.assertEqual(recovered["job_id"],first["job_id"])
  self.assertEqual(self.port.opens,opens);self.assertEqual(self.port.calls,0)
  with patch.object(self.repo,"_current",side_effect=StudioError("RESOURCE_UNAVAILABLE",404)):
   with self.assertRaises(StudioError):self.service.enqueue(CTX,self.request,"studio-request-key-1",access_token="test-owned-memory-only")

 def test_actual_coordinator_single_dispatch_and_typed_persistence_binding(self):
  job=self.job();published=[]
  with patch.object(self.repo.workspace,"persist_typed_generation",side_effect=lambda *a,**kw:published.append((a,kw)) or {"studio_output_id":"output-qa","output_version_id":"output-version-qa"}):
   result=self.service.process(job)
   again=self.service.process(job)
  self.assertEqual(result["status"],"completed");self.assertEqual(again,result)
  self.assertEqual(self.port.calls,1);self.assertEqual(len(published),1)
  self.assertEqual(published[0][1]["metadata"]["selection"]["model_id"],"model-qa")
  self.assertEqual(len(self.store.costs),1)
 def test_bad_typed_body_preserves_actual_provider_reported_cost_without_output(self):
  job=self.job();self.port.raw='{"content": "bad"}'
  with patch.object(self.repo.workspace,"persist_typed_generation",side_effect=AssertionError("INVALID_OUTPUT_MUST_NOT_PUBLISH")):
   result=self.service.process(job)
  self.assertEqual(result["status"],"unavailable");self.assertEqual(self.port.calls,1)
  self.assertEqual(next(iter(self.store.costs.values()))[12],"known")
  self.assertEqual(sum(table=="model_attempts" for table,_ in self.store.canons),1)
 def test_started_job_reclaim_after_source_access_revoked_never_opens_or_calls_provider(self):
  job=self.job();context,plan=self.repo.load_job(job)
  self.repo.claim(context,plan,job.idempotency_key,job_id=job.job_id,job_version=job.version)
  self.port.reject=True;before=self.port.opens
  self.store.job_versions[job.job_id]=2
  result=self.service.process(replace(job,version=2))
  self.assertEqual(result["safe_error_code"],"STUDIO_GENERATION_UNCERTAIN")
  self.assertEqual(self.port.opens,before);self.assertEqual(self.port.calls,0)
  self.assertEqual(sum(table=="model_attempts" for table,_ in self.store.canons),0)
 def test_missing_explicit_selection_does_not_open_provider(self):
  with self.assertRaises(StudioError):self.service.enqueue(CTX,replace(self.request,intended_model_selection=None),"studio-request-key-1",access_token="test-owned-memory-only")
  self.assertEqual(self.port.opens,0);self.assertEqual(self.port.calls,0)
 def test_actual_worker_uses_typed_service_and_never_legacy_template_or_retry(self):
  from daon_user_api.studio_generation_worker import StudioGenerationWorker
  job=self.job()
  class Queue:
   def claim(self,*a,**kw):return job
   def start(self,*a):pass
   def finish(self,*a,**kw):raise AssertionError("SERVICE_OWNS_TYPED_TERMINAL")
   def retry(self,*a,**kw):raise AssertionError("TYPED_JOB_MUST_NOT_RETRY")
  legacy=SimpleNamespace(create_generation=lambda *a:self.fail("LEGACY_TEMPLATE_MUST_NOT_RUN"))
  worker=StudioGenerationWorker(Queue(),legacy,worker_id="worker-qa",execution_service=self.service)
  with patch.object(self.repo.workspace,"persist_typed_generation",return_value={"studio_output_id":"output-qa","output_version_id":"output-version-qa"}):
   self.assertTrue(worker.run_once());self.assertTrue(worker.run_once())
  self.assertEqual(self.port.calls,1)

 def test_production_factory_assembles_normal_postgres_ports_without_connecting(self):
  from daon_user_api.studio_execution import build_studio_execution_service,PostgresStudioInputPort
  from daon_user_api.workspace_model_defaults import PostgresWorkspaceModelResolver
  from daon_user_api.document_index_postgres import PostgresDocumentIndex
  service=build_studio_execution_service(self.store,cipher=object(),user_credentials=None,identity=self.identity,
   policy_service=object(),cost_sessions=None)
  self.assertIs(type(service),StudioExecutionService);self.assertIs(type(service.input_port),PostgresStudioInputPort)
  self.assertIs(type(service.repository),PostgresStudioExecutionRepository)
  self.assertIs(type(service.input_port.resolver),PostgresWorkspaceModelResolver)
  self.assertIs(type(service.input_port.index),PostgresDocumentIndex)
 def test_worker_bootstrap_assembles_same_production_service_without_new_key(self):
  from daon_user_api.studio_generation_worker import _build_execution_service,StudioWorkerSettings
  settings=StudioWorkerSettings("postgresql://test-owned.invalid/unused","worker-qa",600,0.5)
  service=_build_execution_service(settings,self.store,self.repo.workspace)
  self.assertIs(type(service),StudioExecutionService)
  self.assertIs(service.repository.workspace,self.repo.workspace)
  self.assertEqual(self.store.sql,[])


class StudioInputPortTests(unittest.TestCase):
 def test_foreign_origin_is_rejected_before_source_read_resolver_identity_or_transport(self):
  from unittest.mock import Mock
  from daon_user_api.studio_execution import PostgresStudioInputPort
  from daon_user_api.authorization import Role
  from test_studio_execution_postgres import Result
  class OriginStore(Store):
   def execute(self,sql,params=()):
    self.sql.append((sql,params))
    if "studio-typed:origin" in sql:return Result()
    raise AssertionError("SOURCE_READ_BEFORE_ORIGIN_CHECK")
  store=OriginStore();repo=PostgresStudioExecutionRepository(store);resolver=Mock();index=Mock();transform=Mock();transport=Mock();identity=Mock()
  port=PostgresStudioInputPort(repo,resolver,index,transform,transport,cost_sessions=None)
  service=StudioExecutionService(repo,port,identity)
  request=StudioGenerationRequest(**{**REQUEST,"source_version_ids":tuple(REQUEST["source_version_ids"]),"source_only":False,"source_id":"source-qa","run_id":"foreign-run","run_result_id":"foreign-result"})
  with patch("daon_user_api.notebook_model_settings_postgres.assert_execution_current"),patch.object(repo.workspace,"_require_generation_scope"),patch("daon_user_api.open_law_source_access._current_auth_grant",return_value=SimpleNamespace(role=Role.ORGANIZATION_ADMIN)),patch.object(repo,"_effective_policy",return_value=object()):
   for execute in (lambda:service.authorize(CTX,request,"origin-qa-key",access_token="memory-test",password=None),lambda:service.enqueue(CTX,request,"origin-qa-key",access_token="memory-test")):
    # read_replay is not an authorization substitute; this fixture has no prior admission.
    with patch.object(repo,"read_replay",return_value=None),self.assertRaises(StudioError) as error:execute()
    self.assertEqual(error.exception.code,"RESOURCE_UNAVAILABLE")
  resolver.resolve.assert_not_called();index.search.assert_not_called();transform.prepare_payload.assert_not_called()
  self.assertEqual(identity.mock_calls,[]);self.assertEqual(transport.mock_calls,[])

 def test_normal_adapter_port_binds_transformed_evidence_and_releases_model(self):
  from daon_user_api.studio_execution import PostgresStudioInputPort
  from daon_user_api.document_index_postgres import IndexedEvidenceChunk
  from daon_user_api.authorization import Role
  from daon_user_api.data_canon import canonical_json_bytes
  from test_studio_execution_postgres import Result
  class MaterialsStore(Store):
   def execute(self,sql,params=()):
    if "studio-input:materials" in sql:
     return Result([("source-qa","version-qa","e"*64,"index-qa","f"*64,{"chunks":[{"text":"원문 전체입니다."}]})])
    return super().execute(sql,params)
  store=MaterialsStore();repo=PostgresStudioExecutionRepository(store,clock=lambda:NOW);released=[];calls=[]
  selected=SimpleNamespace(connection_id="connection-qa",provider_code="CUSTOM",model_id="model-qa",adapter_type="openai_compatible",reasoning_effort=None,
   reasoning_profile_version=None,model_settings_version=1,base_url="https://qa.example.invalid/v1",credential_version=0,default_version=1,catalog_version=1,provider_kind="external_api",credential_text=lambda **kw:None)
  class Resolver:
   @contextmanager
   def resolve(self,*a,**kw):
    try:yield selected
    finally:released.append(True)
  class Index:
   def search(self,*a,**kw):return (IndexedEvidenceChunk("chunk-qa","source-qa","version-qa",1,"원문 전체입니다.","span-qa",locator_kind="docx_block",locator_value="block-qa"),)
  class Transform:
   def prepare_payload(self,context,raw):
    value=json.loads(raw);body=json.loads(value["messages"][-1]["content"])
    body["evidence"][0]["text"]="가린 전체입니다."
    value["messages"][-1]["content"]=json.dumps(body,ensure_ascii=False)
    return canonical_json_bytes(value)
  class Transport:
   def post_json_no_auth(self,**kwargs):
    if store.active:raise AssertionError("NETWORK_INSIDE_TRANSACTION")
    calls.append(kwargs);return {"choices":[{"message":{"content":"{}"}}]}
   def post_json_headers(self,**kwargs):
    if store.active:raise AssertionError("NETWORK_INSIDE_TRANSACTION")
    calls.append(kwargs);return {"content":[],"usage":{"input_tokens":9,"output_tokens":4,"cost":"0.03","currency":"USD"}}
  policy=SimpleNamespace(organization_policy_version_id="organization-policy-qa",fingerprint="sha256:"+"b"*64,mode="allow_approved_external",allowed_provider_kinds=("external_api",),
   allowed_destinations=("qa.example.invalid",),max_bytes=65536,classification="internal",masking_required=True,redaction_required=True,
   required_approver="workspace_manager",question_step_up_required=False)
  port=PostgresStudioInputPort(repo,Resolver(),Index(),Transform(),Transport(),cost_sessions=None)
  request=StudioGenerationRequest(**{**REQUEST,"source_version_ids":tuple(REQUEST["source_version_ids"])})
  with patch("daon_user_api.notebook_model_settings_postgres.assert_execution_current"),patch.object(repo.workspace,"_require_generation_scope"), \
       patch("daon_user_api.open_law_source_access._current_auth_grant",return_value=SimpleNamespace(role=Role.ORGANIZATION_ADMIN)),patch.object(repo,"_effective_policy",return_value=policy):
   with port.open(CTX,request) as prepared:
    self.assertFalse(prepared.requires_step_up);self.assertEqual(calls,[])
    self.assertEqual(prepared.evidence[0]["text"],"가린 전체입니다.")
    self.assertEqual(prepared.evidence[0]["locator_kind"],"docx_block")
    self.assertEqual(prepared.evidence[0]["locator_value"],"block-qa")
    self.assertEqual(prepared.metadata["sources"][0]["source_digest"],"e"*64)
    self.assertEqual(prepared.metadata["selection"]["credential_version"],0)
    prepared.send()
  self.assertEqual(len(calls),1);self.assertEqual(released,[True])
  body=json.loads(calls[0]["payload"]["messages"][-1]["content"])
  self.assertEqual(body["evidence"][0]["text"],"가린 전체입니다.")
  selected.adapter_type="anthropic_compatible"
  service=StudioExecutionService(repo,port,None)
  with patch("daon_user_api.notebook_model_settings_postgres.assert_execution_current"),patch.object(repo.workspace,"_require_generation_scope"), \
       patch("daon_user_api.open_law_source_access._current_auth_grant",return_value=SimpleNamespace(role=Role.ORGANIZATION_ADMIN)), \
       patch.object(repo,"_effective_policy",return_value=policy),patch.object(repo,"_current",return_value=policy):
   accepted,_=service.enqueue(CTX,request,"studio-request-key-message",access_token="memory-only-test-owned")
   payload,_,actor=store.jobs[accepted["job_id"]];store.jobs[accepted["job_id"]]=(payload,"generating",actor)
   job=StudioGenerationJob(CTX.tenant_id,CTX.workspace_id,accepted["job_id"],CTX.actor_id,CTX.trace_id,CTX.policy_version,"studio-request-key-message",payload,"generating",1,1)
   result=service.process(job)
  self.assertEqual(result["status"],"unavailable")
  self.assertEqual(next(iter(store.costs.values()))[12],"known")
  attempt=next(row for (table,_),row in store.canons.items() if table=="model_attempts")
  self.assertEqual(attempt["usage_json"],{"prompt_tokens":9,"completion_tokens":4})
  self.assertFalse(any(table=="output_versions" for table,_ in store.canons))


class StudioCanonicalWireTests(unittest.TestCase):
 def test_actual_request_bytes_equal_approved_korean_payload_and_boundary(self):
  import hashlib
  from daon_user_api.studio_execution import _StudioCanonicalWireTransport
  from daon_user_api.document_understanding_adapter import UrlLibDocumentUnderstandingTransport
  from daon_user_api.data_canon import canonical_json_bytes
  base=UrlLibDocumentUnderstandingTransport();observed=[]
  for text in ("한글 원문", "가"*21000):
   payload={"model":"model-qa","text":text};raw=canonical_json_bytes(payload)
   self.assertLessEqual(len(raw),65536)
   with patch.object(base,"_request",side_effect=lambda request,timeout:observed.append((request.data,timeout,request.get_header("Content-type"))) or {}):
    transport=_StudioCanonicalWireTransport(base,raw)
    transport.post_json_no_auth(url="https://qa.example.invalid/chat",payload=payload,timeout_seconds=30)
   self.assertTrue(observed[-1][0]==raw)
   self.assertEqual(hashlib.sha256(observed[-1][0]).hexdigest(),hashlib.sha256(raw).hexdigest())
   self.assertEqual(observed[-1][1:],(30,"application/json"))
  with self.assertRaises(StudioError):_StudioCanonicalWireTransport(base,canonical_json_bytes({"text":"가"*22000}))
  self.assertEqual(len(observed),2)
