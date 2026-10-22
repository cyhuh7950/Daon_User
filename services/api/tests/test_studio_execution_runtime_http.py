"""기존 로컬 Identity/Authorization fixture 재사용 + 실제 coordinator/SQL 대역.
실제 PostgreSQL·Provider·브라우저 인수 결과가 아니다.
"""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx
from daon_user_api.runtime import create_app,WEB_SESSION_COOKIE
from daon_user_api.studio_execution import StudioExecutionService
from daon_user_api.studio_execution_postgres import PostgresStudioExecutionRepository
from daon_user_api.notebook_model_settings import NotebookModelExecution,NotebookModelSelection,NotebookModelSettingsError
from test_studio_execution_postgres import Store,NOW
from test_studio_execution import InputPort
import test_studio_workspace_runtime_http as legacy_fixture


class StudioExecutionHttpTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.fixture=legacy_fixture.StudioWorkspaceRuntimeHttpTests("test_s24_export_requires_token_before_file_generation")
  await self.fixture.asyncSetUp()
  self.store=Store();self.repo=PostgresStudioExecutionRepository(self.store,clock=lambda:NOW)
  self.guard=patch.object(self.repo,"_current",return_value=SimpleNamespace(organization_policy_version_id="organization-policy-qa",classification="internal"));self.guard.start()
  self.port=InputPort(self.store);self.service=StudioExecutionService(self.repo,self.port,self.fixture.identity)
  class Settings:
   def require_execution(self,context,notebook,version):
    if type(version) is not int or version!=1:raise NotebookModelSettingsError("NOTEBOOK_MODEL_SETTINGS_CHANGED",409)
    return NotebookModelExecution(NotebookModelSelection("connection-qa","model-qa",None),1,None)
  dependencies=replace(self.fixture.dependencies,studio_execution_service=self.service,notebook_model_settings_service=Settings())
  self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)),base_url="http://127.0.0.1")
  self.body={"workspace_id":"workspace-001","notebook_id":"notebook-qa","source_id":None,"source_version_ids":["version-qa"],
   "source_only":True,"output_type":"slides","model_settings_version":1,
   "settings":{"purpose":"자료 정리","audience":"독자","source_version_ids":["version-qa"],"ruleset_version_id":None,"length":"short","structure":"summary","output_format":"pdf","review_condition":"review_required"}}
 async def asyncTearDown(self):
  await self.client.aclose();self.guard.stop();await self.fixture.asyncTearDown()
 async def test_normal_generation_uses_actual_coordinator_not_legacy_template(self):
  with self.fixture.authenticated():
   response=await self.client.post("/api/v1/studio-generation-requests",cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"typed-http-request-0001"},json=self.body)
  self.assertEqual(response.status_code,201,response.text)
  self.assertEqual(len(self.store.jobs),1)
  self.assertEqual(self.fixture.studio.calls,[]);self.assertEqual(self.port.calls,0)
  self.assertIn("execution_plan_id",next(iter(self.store.jobs.values()))[0])
 async def test_authorization_without_required_stepup_is_readiness_only_and_no_job(self):
  with self.fixture.authenticated(),patch.object(self.fixture.identity,"issue_step_up_after_reauthentication",side_effect=AssertionError("UNNECESSARY_AUTHENTICATION")):
   response=await self.client.post("/api/v1/studio-generation-requests/authorization",cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"typed-http-authorization-1"},json={**self.body,"operation":"generate"})
  self.assertEqual(response.status_code,201,response.text)
  self.assertIsNone(response.json()["data"]["step_up_authorization_id"])
  self.assertEqual(set(response.json()["data"]),{"step_up_authorization_id","expires_at","generation_fingerprint"})
  self.assertEqual(self.store.jobs,{});self.assertEqual(self.port.calls,0)
 async def test_missing_model_selection_never_uses_old_template(self):
  body={k:v for k,v in self.body.items() if k!="model_settings_version"}
  with self.fixture.authenticated():
   response=await self.client.post("/api/v1/studio-generation-requests",cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"typed-http-missing-model-1"},json=body)
  self.assertEqual(response.status_code,409,response.text)
  self.assertEqual(self.store.jobs,{});self.assertEqual(self.fixture.studio.calls,[])
 async def test_ai_revision_without_manual_content_enqueues_202_with_reason_and_selection(self):
  from daon_user_api.studio_workspace import StudioGenerationRequest
  from test_studio_execution_postgres import REQUEST
  restored=StudioGenerationRequest(**{**REQUEST,"source_version_ids":tuple(REQUEST["source_version_ids"])})
  body={"workspace_id":"workspace-001","notebook_id":"notebook-qa","previous_version_id":"previous-qa",
   "revision_type":"settings_change","change_reason":"검토 범위 조정","model_settings_version":1,"settings":self.body["settings"]}
  with self.fixture.authenticated(),patch.object(self.repo.workspace,"load_revision_input",return_value=restored) as restored_input:
   response=await self.client.post("/api/v1/studio-outputs/output-qa/versions",cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"typed-http-revision-0001"},json=body)
  self.assertEqual(response.status_code,202,response.text)
  restored_input.assert_called_once()
  self.assertEqual(restored_input.call_args.args[1:3],("output-qa","previous-qa"))
  self.assertEqual(self.fixture.studio.calls,[]);self.assertEqual(self.port.calls,0)
  saved=next(row[0] for row in self.store.rows.values() if row[0].get("kind")=="studio_execution_plan")
  self.assertEqual(saved["metadata"]["change_reason"],body["change_reason"])
  self.assertEqual(saved["metadata"]["revision_type"],"settings_change")
  self.assertEqual(saved["request"]["intended_model_selection"]["model_settings_version"],1)
  with self.fixture.authenticated():
   denied=await self.client.post("/api/v1/studio-outputs/output-qa/versions",cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"typed-http-revision-0002"},json={**body,"revision_type":"ai_regeneration"})
   self.assertEqual(denied.status_code,400)
  self.assertEqual(len(self.store.jobs),1)

 async def test_regeneration_authorization_uses_closed_minimal_input_and_server_snapshot(self):
  from daon_user_api.studio_workspace import StudioGenerationRequest
  from test_studio_execution_postgres import REQUEST
  restored=StudioGenerationRequest(**{**REQUEST,"source_version_ids":tuple(REQUEST["source_version_ids"])})
  body={"workspace_id":"workspace-001","notebook_id":"notebook-qa","operation":"regenerate","output_id":"output-qa",
   "previous_version_id":"previous-qa","revision_type":"ai_regeneration","change_reason":"자료 재정리","model_settings_version":1}
  with self.fixture.authenticated(),patch.object(self.repo.workspace,"load_revision_input",return_value=restored) as restored_input:
   response=await self.client.post("/api/v1/studio-generation-requests/authorization",cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"typed-http-regen-auth-0001"},json=body)
   self.assertEqual(response.status_code,201,response.text)
   restored_input.assert_called_once()
   for extra in ({"content":"임의 내용"},{"source_version_ids":["other-source"]},{"step_up_authorization_id":"wrong-purpose"}):
    denied=await self.client.post("/api/v1/studio-generation-requests/authorization",cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"typed-http-regen-auth-0001"},json={**body,**extra})
    self.assertEqual(denied.status_code,400)
  self.assertEqual(self.store.jobs,{});self.assertEqual(self.port.calls,0)

 async def test_existing_job_get_rechecks_scope_and_returns_no_store_stable_data_etag(self):
  path="/api/v1/studio-generation-jobs/job-qa?workspace_id=workspace-001&notebook_id=notebook-qa"
  with self.fixture.authenticated():
   first=await self.client.get(path,cookies={WEB_SESSION_COOKIE:"opaque-session"})
   second=await self.client.get(path,cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"If-None-Match":first.headers.get("etag",'"absent"')})
  self.assertEqual(first.status_code,200)
  self.assertEqual(first.headers.get("cache-control"),"no-store")
  self.assertTrue(first.headers.get("etag"))
  self.assertEqual(second.status_code,200);self.assertEqual(first.headers["etag"],second.headers["etag"])
  self.assertEqual(first.json()["data"],second.json()["data"])

 async def test_actual_identity_password_and_plan_bound_consumption_replay(self):
  import secrets
  from test_identity_local_auth import Sender
  from daon_user_api.identity import DevicePlatform
  from daon_user_api.authorization import Role
  sender=Sender();self.fixture.identity._email_sender=sender
  password=secrets.token_urlsafe(24)+"Aa1!"
  identity=self.fixture.identity
  identity.signup(login_id="studio-qa",email="studio-qa@example.invalid",password=password,trace_id="trace-studio-qa",policy_version="policy-qa")
  token=sender.messages[-1]["body"].split(": ",1)[1].splitlines()[0]
  identity.verify_email(token=token,trace_id="trace-studio-qa",policy_version="policy-qa")
  credentials=identity.local_login(login_id="studio-qa",password=password,platform=DevicePlatform.WEB,trace_id="trace-studio-qa",policy_version="policy-qa")
  self.body={**self.body,"workspace_id":"workspace-studio-reauth"}
  self.fixture.dependencies.authorization_repository.bootstrap_workspace(tenant_id=credentials.tenant_id,workspace_id=self.body["workspace_id"],owner_user_id=credentials.user_id,
   owner_role=Role.PERSONAL_OWNER,workspace_kind="personal",data_area="cloud_sync",cost_limit_cents=1000,now=self.fixture.clock())
  self.port.requires_step_up=True
  cookies={WEB_SESSION_COOKIE:credentials.access_token};headers={"Idempotency-Key":"studio-qa-actual-reauth-001"}
  path="/api/v1/studio-generation-requests"
  missing=await self.client.post(path+"/authorization",cookies=cookies,headers=headers,json={**self.body,"operation":"generate"})
  self.assertEqual(missing.status_code,401,"MISSING_PASSWORD_MUST_CHALLENGE")
  self.assertEqual(missing.json()["error"]["code"],"STEP_UP_REQUIRED")
  wrong=await self.client.post(path+"/authorization",cookies=cookies,headers=headers,json={**self.body,"operation":"generate","password":secrets.token_urlsafe(24)})
  self.assertEqual(wrong.status_code,403,"WRONG_PASSWORD_MUST_DENY")
  issued=await self.client.post(path+"/authorization",cookies=cookies,headers=headers,json={**self.body,"operation":"generate","password":password})
  self.assertEqual(issued.status_code,201,"VALID_LOCAL_REAUTH_MUST_ISSUE")
  approval=issued.json()["data"]["step_up_authorization_id"]
  self.assertTrue(type(approval) is str and bool(approval),"SCOPED_APPROVAL_REQUIRED")
  self.assertEqual(self.store.jobs,{})
  generated=await self.client.post(path,cookies=cookies,headers=headers,json={**self.body,"step_up_authorization_id":approval})
  self.assertEqual(generated.status_code,201,"SCOPED_APPROVAL_MUST_ADMIT_ONE_JOB")
  replay=await self.client.post(path,cookies=cookies,headers=headers,json={**self.body,"step_up_authorization_id":approval})
  self.assertEqual(replay.status_code,200,"EXACT_CONSUMPTION_REPLAY_MUST_NOT_RECONSUME")
  wrong_key=await self.client.post(path,cookies=cookies,headers={"Idempotency-Key":"studio-qa-other-request-1"},json={**self.body,"step_up_authorization_id":approval})
  self.assertEqual(wrong_key.status_code,403,"OTHER_TARGET_MUST_DENY")
  with self.fixture.dependencies.identity_repository.transaction() as conn:
   count=conn.execute("SELECT count(*) FROM step_up_consumptions WHERE operation=?",("studio.generate.external_transfer",)).fetchone()[0]
  self.assertEqual(count,1);self.assertEqual(len(self.store.jobs),1);self.assertEqual(self.port.calls,0)
  self.assertTrue(all(password not in row[1] and approval not in row[1] for row in self.store.rows.values()),"NO_PASSWORD_OR_APPROVAL_IN_PLAN")
