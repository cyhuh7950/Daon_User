"""기존 로컬 Identity fixture와 실제 설정 SQL repository의 HTTP 경계 시험.
기존 SQLite 인증 fixture를 재사용하며 모델/PG 서버는 호출하지 않는다.
"""
from dataclasses import replace
import unittest
from unittest.mock import patch
import httpx
import test_question_answering_runtime_http as runtime_fixture
from test_notebook_model_settings_postgres import Store
from daon_user_api.notebook_model_settings_postgres import PostgresNotebookModelSettingsService
from daon_user_api.runtime import create_app,WEB_SESSION_COOKIE

class NotebookModelSettingsHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=runtime_fixture.QuestionRuntimeHttpTests("test_c_no_source_fact_is_saved_notice_before_model_or_step_up")
        await self.fixture.asyncSetUp()
        self.store=Store();self.service=PostgresNotebookModelSettingsService(self.store)
        self.permission_reader=patch.object(PostgresNotebookModelSettingsService,"_permissions",return_value={"local","external_api"})
        self.permission_reader.start()
        # SQL 대역은 Notebook 행/인가 엔진을 구현하지 않는다. 현재 HTTP 인가는 실제 기존 AuthorizationService다.
        self.service._guard=lambda conn,ctx,nb: None
        self.dependencies=replace(self.fixture.dependencies,notebook_model_settings_service=self.service)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dependencies)),base_url="http://127.0.0.1")
        self.path="/api/v1/workspaces/workspace-001/notebooks/notebook-cp3/model-settings"
    async def asyncTearDown(self):
        await self.client.aclose();await self.fixture.asyncTearDown();self.permission_reader.stop()
    async def test_get_is_readonly_patch_exact_replay_and_strict_body(self):
        with self.fixture._authenticated():
            headers={"Idempotency-Key":"model-settings-request-0001"}
            cookies={WEB_SESSION_COOKIE:"opaque-session"}
            first=await self.client.get(self.path,cookies=cookies)
            self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(first.json()["data"]["version"],0)
            self.assertEqual(first.headers["Cache-Control"],"no-store")
            self.assertEqual(self.store.rows,[])
            body={"connection_id":"connection-a","model_id":"solar-pro4","reasoning_effort":"low","expected_version":0}
            saved=await self.client.patch(self.path,cookies=cookies,headers=headers,json=body)
            self.assertEqual(saved.status_code,200,saved.text)
            self.assertEqual(saved.json()["data"]["version"],1)
            replay=await self.client.patch(self.path,cookies=cookies,headers=headers,json=body)
            self.assertTrue(replay.json()["data"]["replayed"])
            conflict=await self.client.patch(self.path,cookies=cookies,headers=headers,json={**body,"reasoning_effort":"high"})
            self.assertEqual(conflict.status_code,409)
            malformed=await self.client.patch(self.path,cookies=cookies,headers={"Idempotency-Key":"model-settings-request-0002"},json={**body,"expected_version":True})
            self.assertEqual(malformed.status_code,422)
            view=await self.client.get(self.path,cookies=cookies)
            self.assertEqual(view.json()["data"]["effective_selection"]["reasoning_effort"],"low")
            unknown=await self.client.get(self.path+"?extra=1",cookies=cookies)
            self.assertEqual(unknown.status_code,422)
            self.assertEqual(len(self.store.rows),1)
    async def test_normal_question_requires_saved_version_before_service_ask(self):
        with self.fixture._authenticated():
            response=await self.client.post("/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"model-question-request-001"},
                json={"notebook_id":"notebook-cp3","question":"안녕하세요"})
            self.assertEqual(response.status_code,409,response.text)
            self.assertEqual(response.json()["error"]["code"],"NOTEBOOK_MODEL_SETTINGS_CHANGED")
            self.assertEqual(self.fixture.service.calls,[])
            self.assertEqual(self.fixture.notebooks.events[-1][2]["event_kind"],"failed")

    async def test_missing_version_no_source_fact_notice_but_greeting_requires_selection(self):
        from types import SimpleNamespace
        from test_question_answering_service import (FakeRepository, FakeIndex, FakeCredential,
            GeneralAnswerTransport, StaticWorkspaceModelResolver, FakeEgress)
        from daon_user_api.question_answering_service import QuestionAnsweringService
        resolver,transport,repository=StaticWorkspaceModelResolver(),GeneralAnswerTransport(),FakeRepository()
        service=QuestionAnsweringService(resolver,repository,FakeIndex(()),FakeCredential(),transport,FakeEgress())
        dependencies=replace(self.dependencies,question_answering_service=service)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)),base_url="http://127.0.0.1") as client:
            with self.fixture._authenticated(), patch.object(self.fixture.identity,"issue_step_up_after_reauthentication",side_effect=AssertionError("step-up forbidden")), patch.object(self.fixture.identity,"consume_step_up",side_effect=AssertionError("step-up forbidden")):
                for index,suffix in enumerate(("","/authorization")):
                    for label,question,status in (("fact","안녕, 계약기간은 언제인가요?",200),("greeting","안녕하세요",409)):
                        response=await client.post("/api/v1/workspaces/workspace-001/questions"+suffix,
                            cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":f"d-no-version-{label}-{index}"},
                            json={"notebook_id":"notebook-cp3","question":question})
                        self.assertEqual(response.status_code,status,response.text)
                        if status==200:
                            self.assertEqual(response.json()["data"]["kind"],"system_notice")
                        else:
                            self.assertEqual(response.json()["error"]["code"],"NOTEBOOK_MODEL_SETTINGS_CHANGED")
        self.assertEqual((resolver.calls,transport.calls,repository.persisted),([],0,None))

    async def test_studio_intended_selection_is_server_bound_and_stale_or_forged_is_rejected(self):
        from test_studio_workspace_runtime_http import FakeStudioWorkspace
        studio=FakeStudioWorkspace()
        dependencies=replace(self.dependencies,studio_workspace_service=studio)
        body={"workspace_id":"workspace-001","notebook_id":"notebook-cp3","output_type":"evidence_report",
            "source_id":"source-1","source_version_ids":["source-version-1"],"run_id":"run-1","run_result_id":"result-1",
            "settings":{"purpose":"목적","audience":"독자","source_version_ids":["source-version-1"],"ruleset_version_id":None,
                "length":"short","structure":"summary","output_format":"pdf","review_condition":"review_required"},"model_settings_version":1}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)),base_url="http://127.0.0.1") as client:
            with self.fixture._authenticated():
                cookies={WEB_SESSION_COOKIE:"opaque-session"}
                saved=await self.client.patch(self.path,cookies=cookies,headers={"Idempotency-Key":"studio-model-save-0001"},
                    json={"connection_id":"connection-a","model_id":"solar-pro4","reasoning_effort":"low","expected_version":0})
                self.assertEqual(saved.status_code,200,saved.text)
                result=await client.post("/api/v1/studio-generation-requests",cookies=cookies,
                    headers={"Idempotency-Key":"studio-intended-request-01"},json=body)
                self.assertEqual(result.status_code,201,result.text)
                intent=studio.calls[0][2].intended_model_selection
                self.assertEqual(intent,{"connection_id":"connection-a","model_id":"solar-pro4","reasoning_effort":"low",
                    "model_settings_version":1,"reasoning_profile_version":"upstage-solar-pro4-safe-wire-v1"})
                self.assertEqual(studio.calls[0][2].run_id,"run-1")
                stale=await client.post("/api/v1/studio-generation-requests",cookies=cookies,
                    headers={"Idempotency-Key":"studio-intended-request-02"},json={**body,"model_settings_version":2})
                self.assertEqual(stale.status_code,409)
                forged=await client.post("/api/v1/studio-generation-requests",cookies=cookies,
                    headers={"Idempotency-Key":"studio-intended-request-03"},json={**body,"intended_model_selection":intent})
                self.assertEqual(forged.status_code,400)
                self.assertEqual(len(studio.calls),1)

    async def test_current_real_authorization_revocation_rejects_read_and_save(self):
        from test_identity_support import TRACE_ID,POLICY_VERSION
        with self.fixture._authenticated():
            before=await self.client.get(self.path,cookies={WEB_SESSION_COOKIE:"opaque-session"})
            self.assertEqual(before.status_code,200)
            self.dependencies.authorization_service.set_tenant_role(principal=self.fixture.principal,user_id="user-001",role=None,
                expected_version=1,trace_id=TRACE_ID,policy_version=POLICY_VERSION)
            read=await self.client.get(self.path,cookies={WEB_SESSION_COOKIE:"opaque-session"})
            write=await self.client.patch(self.path,cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"revoked-model-request-01"},
                json={"connection_id":"connection-a","model_id":"solar-pro4","reasoning_effort":None,"expected_version":0})
            self.assertEqual((read.status_code,write.status_code),(403,403))
            self.assertEqual(self.store.rows,[])
