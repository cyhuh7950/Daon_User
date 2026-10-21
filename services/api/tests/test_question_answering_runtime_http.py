from __future__ import annotations

import tempfile
import unittest
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.question_answering_postgres import CitationContent, StoredCitation, StoredQuestionAnswer
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service, identity_session_view


PDF = b"%PDF-1.4\npage one\fpage two\n%%EOF\n"


class QaStructuredOllamaSettingsTests(unittest.TestCase):
    def test_qa_target_environment_preserves_production_auth_and_empty_disables(self) -> None:
        base = {
            "DAON_RUNTIME_PROFILE": "production", "DAON_CLOUD_DATABASE_DSN": "postgresql://fixture",
            "DAON_PUBLIC_GATEWAY_URL": "https://app.example.com", "DAON_TRUSTED_PROXY_IPS": "127.0.0.1",
            "DAON_STEP_UP_TOKEN_KEY_FILE": "fixture-key-reference",
        }
        for target in ("", "workspace-qa"):
            with self.subTest(target=target), patch.dict(os.environ, {
                **base, "DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID": target,
            }, clear=True):
                settings = RuntimeSettings.from_env()
                self.assertEqual(settings.qa_structured_ollama_workspace_id, target)
                self.assertEqual(settings.profile, "production")
                self.assertFalse(settings.dev_auth_bypass)
        for invalid in (" workspace-qa", "workspace-qa ", "qa,other", "*", "x" * 129):
            with self.subTest(invalid=invalid), patch.dict(os.environ, {
                **base, "DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID": invalid,
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "^RUNTIME_QA_STRUCTURED_OLLAMA_TARGET_INVALID$"):
                    RuntimeSettings.from_env()


class FakeQuestionService:
    def __init__(self) -> None:
        self.calls = []
        self.replay_calls = []
        self.replay_answer = None
        self.insufficient_source = False

    def preflight_grounding(self,context,**values):
        # This boundary fixture covers runtime delegation, not retrieval semantics.
        return None

    def replay(self, context, *, run_id, request_fingerprint):  # type: ignore[no-untyped-def]
        self.replay_calls.append((context, run_id, request_fingerprint))
        return self.replay_answer

    def ask(self, context, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((context, kwargs))
        if kwargs["source_id"] is None:
            return StoredQuestionAnswer(
                kwargs["run_id"], "result-general", "안녕하세요. 무엇을 도와드릴까요?", False, (),
            )
        if self.insufficient_source:
            return StoredQuestionAnswer(
                kwargs["run_id"], "result-insufficient", "선택한 Source 범위와 질문이 일치하지 않습니다.", True, (),
            )
        return StoredQuestionAnswer(
            kwargs["run_id"], "result-cp3", "ORANGE-COMPASS-42", False,
            (StoredCitation(
                "citation-cp3", kwargs["source_id"], kwargs["source_version_id"],
                "span-page-2", 2, "raw_source", kwargs["source_id"],
                {"kind": "page", "value": "2"},
            ),),
        )


from notebook_conversation_test_support import ConversationTestLedger


class FakeNotebookService(ConversationTestLedger):
    pass


class FakeKnowledgePackages:
    def resolve_question_sources(self, context, package_ids):  # type: ignore[no-untyped-def]
        self.last = (context, package_ids)
        return tuple(type("KnowledgeQuestionSource", (), {
            "package_id": package_id,
            "source_id": f"source-{package_id}",
            "source_version_id": f"version-{package_id}",
            "digest_sha256": "a" * 64,
        })() for package_id in package_ids)


class FakeCitationContent:
    def __init__(self) -> None:
        self.text_mode = False
        self.image_mode = False
        self.xlsx_mode = False
        self.docx_mode = False

    def read_citation_content(self, context, citation_id, *, representation=None):  # type: ignore[no-untyped-def]
        if citation_id != "citation-cp3":
            raise AssertionError(citation_id)
        if representation == 'audio':
            from test_audio_source import wav_bytes
            return CitationContent('source-cp3', 'source-version-cp3', 'qa.wav', wav_bytes(), 'audio/wav'), {
                'kind':'audio_segment', 'value':'{"segment":"s:1","start_ms":0,"end_ms":1000,"start":0,"end":1}'}
        if self.docx_mode:
            import json
            from test_docx_source import docx_bytes, paragraph
            from daon_user_api.docx_source import read_docx, read_docx_block_view, encode_docx_block
            value = encode_docx_block('p:1', 1, 2)
            view = read_docx_block_view(read_docx(docx_bytes(paragraph('한😀글'))), value)
            view['source_version_id'] = 'source-version-cp3'
            return CitationContent('source-cp3', 'source-version-cp3', 'qa.docx', json.dumps(view, ensure_ascii=False).encode(), 'application/json'), {'kind': 'docx_block', 'value': value}
        if self.xlsx_mode:
            import json
            from test_xlsx_source import parts, zipped
            from daon_user_api.xlsx_source import read_xlsx, read_sheet_cell_view, encode_sheet_cell
            value = encode_sheet_cell('납품 "계획"', 'B2:C3')
            view = read_sheet_cell_view(read_xlsx(zipped(parts())), value)
            view['source_version_id'] = 'source-version-cp3'
            return CitationContent('source-cp3', 'source-version-cp3', 'qa.xlsx', json.dumps(view, ensure_ascii=False).encode(), 'application/json'), {'kind': 'sheet_cell', 'value': value}
        if self.image_mode:
            from test_image_document_understanding import image_bytes
            return CitationContent("source-cp3", "source-version-cp3", "qa.png", image_bytes(), "image/png"), {"kind": "image_region", "value": "5,7,80,20,120,60"}
        if self.text_mode:
            return CitationContent(
                "source-knowledge", "version-knowledge", "approved-knowledge.txt",
                "승인된 Daon 일반 텍스트 지식".encode("utf-8"), "text/plain; charset=utf-8",
            ), {"kind": "section", "value": "span-knowledge"}
        return CitationContent(
            "source-cp3", "source-version-cp3", "report.pdf", PDF, "application/pdf",
        ), {"kind": "page", "value": "2"}


class QuestionRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_c_terminal_notice_replays_before_newly_available_evidence_can_run(self):
        from types import SimpleNamespace
        from test_question_answering_service import (FakeRepository,FakeIndex,FakeCredential,
            GeneralAnswerTransport,StaticWorkspaceModelResolver,FakeEgress)
        from daon_user_api.document_index_postgres import IndexedEvidenceChunk
        from daon_user_api.question_answering_service import QuestionAnsweringService
        resolver,transport,index=StaticWorkspaceModelResolver(),GeneralAnswerTransport(),FakeIndex(())
        service=QuestionAnsweringService(resolver,FakeRepository(),index,FakeCredential(),transport,FakeEgress())
        dependencies=replace(self.dependencies,question_answering_service=service,
            egress_policy_service=SimpleNamespace(get_effective=lambda _:self.fail("no egress")))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)),base_url="http://127.0.0.1") as client:
            with self._authenticated():
                args=dict(cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"c-notice-replay-request"},
                    json={"notebook_id":"notebook-cp3","source_id":"source-cp3","source_version_id":"source-version-cp3","question":"계약 기간은 언제인가요?"})
                first=await client.post("/api/v1/workspaces/workspace-001/questions",**args)
                self.assertEqual(first.status_code,200,first.text)
                index.evidence=(IndexedEvidenceChunk("chunk-c","source-cp3","source-version-cp3",1,"계약 기간 원문","span-c"),)
                second=await client.post("/api/v1/workspaces/workspace-001/questions",**args)
                self.assertEqual(second.json()["data"],first.json()["data"])
                self.assertRegex(first.headers.get("ETag", ""), r'"projection-[0-9a-f]{24}"')
                self.assertEqual(second.headers.get("ETag"),first.headers["ETag"])
                self.assertEqual(second.headers["Cache-Control"],"no-store")
        self.assertEqual((resolver.calls,transport.calls),([],0))

    async def test_c_no_source_fact_is_saved_notice_before_model_or_step_up(self):
        from types import SimpleNamespace
        from test_question_answering_service import (FakeRepository, FakeIndex, FakeCredential,
            GeneralAnswerTransport, StaticWorkspaceModelResolver, FakeEgress)
        from daon_user_api.question_answering_service import QuestionAnsweringService
        resolver, transport, repository = StaticWorkspaceModelResolver(), GeneralAnswerTransport(), FakeRepository()
        service = QuestionAnsweringService(resolver, repository, FakeIndex(()), FakeCredential(), transport, FakeEgress())
        dependencies = replace(self.dependencies, question_answering_service=service,
            egress_policy_service=SimpleNamespace(get_effective=lambda _: self.fail("egress must not be read")))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)), base_url="http://127.0.0.1") as client:
            with self._authenticated(), patch.object(self.identity, "issue_step_up_after_reauthentication", side_effect=AssertionError("step-up issue forbidden")), patch.object(self.identity, "consume_step_up", side_effect=AssertionError("step-up consume forbidden")):
                for suffix in ("", "/authorization"):
                    response = await client.post("/api/v1/workspaces/workspace-001/questions"+suffix,
                        cookies={WEB_SESSION_COOKIE:"opaque-session"}, headers={"Idempotency-Key":"c-notice-request-"+str(len(suffix))},
                        json={"notebook_id":"notebook-cp3", "question":"안녕, 계약기간은 언제까지인가요?"})
                    self.assertEqual(response.status_code, 200, response.text)
                    data=response.json()["data"]
                    self.assertEqual(set(data), {"kind","turn_id","answer","grounding_status","insufficient","citations","next_actions"})
                    self.assertEqual((data["kind"],data["grounding_status"],data["insufficient"]), ("system_notice","no_knowledge",True))
                    self.assertIn(data["turn_id"], self.notebooks.turns)
                    self.assertRegex(response.headers.get("ETag", ""), r'"projection-[0-9a-f]{24}"')
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual((resolver.calls,transport.calls,repository.persisted), ([],0,None))
        self.assertEqual([item[2]["event_kind"] for item in self.notebooks.events], ["no_knowledge","no_knowledge"])

    async def test_question_admitted_before_model_failure_and_history_is_authenticated(self):
        from daon_user_api.question_answering_service import QuestionAnsweringError
        with self._authenticated(), patch.object(self.service,"ask",side_effect=QuestionAnsweringError("QUESTION_PROVIDER_UNAVAILABLE",status=503,retryable=True)):
            response=await self.client.post("/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"question-history-failure"},
                json={"notebook_id":"notebook-cp3","question":"저장되어야 할 원문"})
            history=await self.client.get("/api/v1/workspaces/workspace-001/notebooks/notebook-cp3/conversation-turns",cookies={WEB_SESSION_COOKIE:"opaque-session"})
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(len(self.notebooks.turns),1)
        self.assertEqual(self.notebooks.events[-1][2]["event_kind"],"uncertain")
        self.assertEqual(history.status_code,200,history.text)
        self.assertEqual(history.json()["data"]["items"][0]["question"],"저장되어야 할 원문")
        self.assertEqual(history.json()["data"]["items"][0]["client_request_id"],"question-history-failure")
        self.assertEqual(history.headers["Cache-Control"],"no-store")
        self.assertRegex(history.headers["ETag"],r'^"projection-[0-9a-f]{24}"$')
        denied=await self.client.get("/api/v1/workspaces/workspace-001/notebooks/notebook-cp3/conversation-turns")
        self.assertEqual(denied.status_code,401)

    async def test_conversation_query_strict_and_no_auth_no_admission(self):
        base="/api/v1/workspaces/workspace-001/notebooks/notebook-cp3/conversation-turns"
        with self._authenticated():
            for query in ("?limit=0","?limit=101","?limit=1&limit=2","?unknown=x"):
                response=await self.client.get(base+query,cookies={WEB_SESSION_COOKIE:"opaque-session"})
                self.assertEqual(response.status_code,400,response.text)
                self.assertEqual(response.json()["error"]["code"],"NOTEBOOK_PAGE_INVALID")
        self.assertEqual(self.notebooks.turns,{})
        response=await self.client.post("/api/v1/workspaces/workspace-001/questions",
            headers={"Idempotency-Key":"question-unauthorized"},json={"notebook_id":"notebook-cp3","question":"질문"})
        self.assertEqual(response.status_code,401)
        self.assertEqual(self.notebooks.turns,{})

    async def test_failure_event_storage_error_preserves_original_auth_policy_error(self):
        from daon_user_api.identity import IdentityError
        from daon_user_api.egress_policy import EgressPolicyError
        from daon_user_api.notebook import NotebookError
        for original,secondary,status,code in (
            (IdentityError("REAUTHENTICATION_REQUIRED",401),NotebookError("NOTEBOOK_UNAVAILABLE",503),401,"AUTHENTICATION_REQUIRED"),
            (EgressPolicyError("EGRESS_POLICY_DENIED",403),NotebookError("NOTEBOOK_NOT_FOUND",404),403,"EGRESS_POLICY_DENIED"),
        ):
            with self.subTest(code=code),self._authenticated(),patch.object(self.service,"ask",side_effect=original),patch.object(self.notebooks,"append_question_turn_event",side_effect=secondary),self.assertLogs("daon_user_api.runtime",level="WARNING") as logged:
                response=await self.client.post("/api/v1/workspaces/workspace-001/questions",
                    cookies={WEB_SESSION_COOKIE:"opaque-session"},headers={"Idempotency-Key":"event-failure-"+code},
                    json={"notebook_id":"notebook-cp3","question":"질문"})
            self.assertEqual(response.status_code,status,response.text)
            self.assertEqual(response.json()["error"]["code"],code)
            self.assertTrue(any("question_turn_event_unavailable" in line and secondary.code in line for line in logged.output))

    async def test_runtime_passes_exact_qa_target_to_existing_question_service(self) -> None:
        from daon_user_api.question_answering_service import QuestionAnsweringService

        settings = replace(self.dependencies.settings,
            qa_structured_ollama_workspace_id="workspace-qa",
            provider_credential_key_file=Path("fixture-provider-key"))
        dependencies = replace(self.dependencies, settings=settings,
            cloud_store=object(), object_storage=object(), question_answering_service=None)
        with patch.object(Path, "read_bytes", return_value=b"0" * 32), patch(
            "daon_user_api.runtime.QuestionAnsweringService", wraps=QuestionAnsweringService,
        ) as construct:
            create_app(dependencies)
        self.assertEqual(construct.call_count, 1)
        self.assertEqual(construct.call_args.kwargs["qa_structured_ollama_workspace_id"], "workspace-qa")
        self.assertFalse(settings.dev_auth_bypass)

    async def test_arbitrary_question_without_context_reaches_general_provider_answer(self) -> None:
        with self._authenticated():
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-any-general-0001"},
                json={"notebook_id": "notebook-cp3", "question": "2026년 매출은?"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["citations"], [])
        self.assertFalse(response.json()["data"]["insufficient"])
        self.assertEqual(response.json()["data"]["mode"], "work_support")
        self.assertEqual(response.json()["data"]["grounding"], "ungrounded")
        self.assertIsNone(response.json()["data"]["source_scope_summary"])
        self.assertIsNone(response.json()["data"]["mismatch"])
        self.assertEqual(response.json()["data"]["next_actions"], [])
        self.assertEqual(self.service.calls[-1][1]["source_id"], None)

    async def test_any_question_allows_no_context_and_returns_general_answer(self) -> None:
        with self._authenticated():
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-general-0001"},
                json={"notebook_id": "notebook-cp3", "question": "안녕하세요!"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["citations"], [])
        self.assertIsNone(self.service.calls[-1][1]["source_id"])
        self.assertEqual(self.notebooks.required[-1][2], ())

        self.service.calls.clear()
        with self._authenticated():
            answer = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-general-0002"},
                json={"notebook_id": "notebook-cp3", "question": "2026년 매출은?"},
            )
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["data"]["citations"], [])
        self.assertFalse(answer.json()["data"]["insufficient"])
        self.assertEqual(answer.json()["data"]["mode"], "work_support")
        self.assertEqual(answer.json()["data"]["grounding"], "ungrounded")
        self.assertIsNone(self.service.calls[-1][1]["source_id"])

    async def test_source_scope_mismatch_is_structured_and_not_silent_refusal(self) -> None:
        self.service.insufficient_source = True
        with self._authenticated():
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-mismatch-0001"},
                json={
                    "notebook_id": "notebook-cp3", "source_id": "source-cp3",
                    "source_version_id": "source-version-cp3",
                    "question": "선택한 문서에 없는 최신 시장 소식은?",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["mode"], "explicit_source_lookup")
        self.assertEqual(data["grounding"], "source_evidence_unavailable")
        self.assertEqual(data["source_scope_summary"], "선택한 Source 범위")
        self.assertEqual(data["mismatch"]["code"], "SOURCE_SCOPE_MISMATCH")
        self.assertTrue(data["next_actions"])
        self.assertNotEqual(data["answer"], "근거가 부족하여 답변할 수 없습니다")

    async def test_supported_modes_are_exactly_projected_for_context_free_questions(self) -> None:
        cases = (
            ("work_support", "다음 작업을 어떻게 진행할까?", "question-mode-work"),
            ("source_backed_action", "이 자료로 보고서 만들어줘", "question-mode-action"),
            ("approved_web_research", "최신 정보를 웹에서 검색해줘", "question-mode-web"),
        )
        for expected_mode, question, key in cases:
            with self._authenticated():
                response = await self.client.post(
                    "/api/v1/workspaces/workspace-001/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": key},
                    json={"notebook_id": "notebook-cp3", "question": question},
                )
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()["data"]
            self.assertEqual(data["mode"], expected_mode)
            self.assertEqual(data["grounding"], "ungrounded")
            self.assertIsNone(data["source_scope_summary"])
            self.assertIsNone(data["mismatch"])
            if expected_mode == "approved_web_research":
                self.assertEqual(data["next_actions"], ["승인된 웹 조사 요청"])
            else:
                self.assertEqual(data["next_actions"], [])

    async def test_grounded_response_exposes_source_backed_metadata(self) -> None:
        with self._authenticated():
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-grounded-metadata-0001"},
                json={
                    "notebook_id": "notebook-cp3", "source_id": "source-cp3",
                    "source_version_id": "source-version-cp3",
                    "question": "What is the verified answer?",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["mode"], "explicit_source_lookup")
        self.assertEqual(data["grounding"], "source_backed")
        self.assertEqual(data["source_scope_summary"], "선택한 Source 범위")
        self.assertIsNone(data["mismatch"])
        self.assertEqual(data["next_actions"], [])

    async def test_completed_local_question_replay_revalidates_binding_before_domain_state(self) -> None:
        body = {
            "notebook_id": "notebook-cp3",
            "source_id": "source-cp3", "source_version_id": "source-version-cp3",
            "question": "What is the verified answer?",
        }
        with self._authenticated():
            first = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-replay-0001"}, json=body,
            )
        self.assertEqual(first.status_code, 200, first.text)
        persisted = StoredQuestionAnswer(
            first.json()["data"]["run_id"], first.json()["data"]["run_result_id"],
            first.json()["data"]["answer"], False, (),
            provider_kind="local_runtime",
        )
        self.service.replay_answer = persisted
        self.service.calls.clear()
        self.notebooks.required.clear()

        with self._authenticated():
            replay = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-replay-0001"}, json=body,
            )

        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["data"]["run_result_id"], persisted.run_result_id)
        self.assertEqual(len(self.notebooks.required), 1)
        self.assertEqual(self.service.calls, [])
        self.assertEqual(len(self.service.replay_calls), 2)

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        db_path = Path(self.directory.name) / "runtime.sqlite3"
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
        authorization_repository = SqliteAuthorizationRepository(db_path)
        self.principal = IdentityPrincipal("user-001", "session-001", "device-001", "tenant-001")
        authorization_repository.bootstrap_workspace(
            tenant_id="tenant-001", workspace_id="workspace-001",
            owner_user_id="user-001", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal", data_area="cloud_sync",
            cost_limit_cents=1000, now=clock(),
        )
        authorization = AuthorizationService(
            repository=authorization_repository, audit_store=audit, clock=clock,
            identity_service=identity,
        )
        self.identity = identity
        self.service = FakeQuestionService()
        self.notebooks = FakeNotebookService()
        self.knowledge = FakeKnowledgePackages()
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
            identity_service=identity, authorization_service=authorization,
            audit_store=audit, identity_repository=identity_repository,
            authorization_repository=authorization_repository,
            question_answering_service=self.service,
            citation_content_repository=FakeCitationContent(),
            knowledge_package_service=self.knowledge,
            notebook_service=self.notebooks,  # type: ignore[arg-type]
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="http://127.0.0.1",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    def _authenticated(self):  # type: ignore[no-untyped-def]
        return patch.object(
            self.identity,
            "describe_access",
            return_value=identity_session_view(self.principal),
        )

    def _assert_s42_original_logical_request(self, expected_body, key):
        # 기대 body는 각 HTTP 시험의 literal이다. 서비스/지문 helper로 재조립하지 않는다.
        import hashlib
        import json
        self.assertEqual(len(self.service.calls), 1)
        context, call = self.service.calls[0]
        self.assertEqual((context.tenant_id, context.workspace_id, context.actor_id, context.notebook_id),
            ("tenant-001", "workspace-001", "user-001", "notebook-cp3"))
        expected_fingerprint = "sha256:" + hashlib.sha256(json.dumps({
            "tenant_id": "tenant-001", "workspace_id": "workspace-001", "notebook_id": "notebook-cp3",
            "actor_id": "user-001", "run_id": call["run_id"], "idempotency_key": key,
            "request": expected_body,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        self.assertEqual(call["request_fingerprint"], expected_fingerprint)
        self.assertEqual(len(self.service.replay_calls), 1)
        self.assertEqual(self.service.replay_calls[0][1:], (call["run_id"], expected_fingerprint))
        self.assertTrue(call["replay_checked"])
        self.assertIn("request_payload", call, "QUESTION_RUNTIME_ORIGINAL_LOGICAL_BODY_NOT_FORWARDED")
        self.assertIn("idempotency_key", call, "QUESTION_RUNTIME_ORIGINAL_KEY_NOT_FORWARDED")
        self.assertEqual(call["request_payload"], expected_body)
        self.assertEqual(call["idempotency_key"], key)
        self.assertNotIn("context_mode", call["request_payload"])
        self.assertNotIn("step_up_authorization_id", call["request_payload"])

    async def test_s42_raw_http_forwards_exact_fingerprint_body_and_key_without_step_up(self):
        """기존 ASGI/auth와 FakeQuestionService 포트 사이의 연결 시험이며 실제 Provider/PG 증거가 아니다."""
        expected = {
            "notebook_id": "notebook-cp3", "source_id": "source-cp3",
            "source_version_id": "source-version-cp3", "question": "선택한 원문 근거의 검증 문장은 무엇인가요?",
        }
        key = "question-s42-raw-internal-0001"
        canary = "S42_SYNTHETIC_STEP_UP_NOT_A_REAL_SECRET"
        with self._authenticated(), self.assertLogs(level="INFO") as logs:
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"}, headers={"Idempotency-Key": key},
                json={**expected, "knowledge_context": None, "step_up_authorization_id": canary},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["answer"], "ORANGE-COMPASS-42")
        self.assertNotIn(canary, response.text)
        self.assertNotIn(canary, "\n".join(logs.output))
        self.assertNotIn(canary, repr(self.service.calls))
        self.assertEqual(self.notebooks.required[0][1:],
            ("notebook-cp3", (("source", "source-cp3", "source-version-cp3"),)))
        call = self.service.calls[0][1]
        self.assertEqual((call["source_id"], call["source_version_id"], call["context_mode"], call["question"]),
            ("source-cp3", "source-version-cp3", "raw_only", expected["question"]))
        self._assert_s42_original_logical_request(expected, key)

    async def test_s42_knowledge_http_keeps_original_package_resource_selection_in_logical_body(self):
        """원 logical 선택 전달만 검사한다. 각 mode의 실제 실패 저장/외부 Daon 동작 증거가 아니다."""
        cases = (
            ("raw_only", [{"resource_kind": "source", "resource_id": "source-raw", "version_id": "version-raw"}],
                (("raw_source", "source-raw", "source-raw", "version-raw"),)),
            ("daon_priority", [{"resource_kind": "knowledge_package", "resource_id": "package-daon3"}],
                (("daon_knowledge", "package-daon3", "source-package-daon3", "version-package-daon3"),)),
            ("mixed", [{"resource_kind": "knowledge_package", "resource_id": "package-daon3"},
                       {"resource_kind": "source", "resource_id": "source-raw", "version_id": "version-raw"}],
                (("daon_knowledge", "package-daon3", "source-package-daon3", "version-package-daon3"),
                 ("raw_source", "source-raw", "source-raw", "version-raw"))),
        )
        for mode, resources, expected_sources in cases:
            with self.subTest(mode=mode):
                self.service.calls.clear()
                self.service.replay_calls.clear()
                self.notebooks.required.clear()
                expected = {"notebook_id": "notebook-cp3", "question": "선택한 지식과 원문을 확인해줘",
                    "knowledge_context": {"mode": mode, "resources": resources}}
                # package version의 명시 null도 재귀 exclude_none 대상이며 원 resource ID/순서는 보존된다.
                wire_resources = [{**resource, "version_id": resource.get("version_id")} for resource in resources]
                key = "question-s42-logical-" + mode + "-0001"
                canary = "S42_SYNTHETIC_KNOWLEDGE_STEP_UP_NOT_A_REAL_SECRET"
                with self._authenticated(), self.assertLogs(level="INFO") as logs:
                    response = await self.client.post(
                        "/api/v1/workspaces/workspace-001/questions",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"}, headers={"Idempotency-Key": key},
                        json={"notebook_id": "notebook-cp3", "question": "선택한 지식과 원문을 확인해줘",
                            "source_id": None, "source_version_id": None, "step_up_authorization_id": canary,
                            "knowledge_context": {"mode": mode, "resources": wire_resources}},
                    )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn(canary, response.text)
                self.assertNotIn(canary, "\n".join(logs.output))
                self.assertNotIn(canary, repr(self.service.calls))
                call = self.service.calls[0][1]
                self.assertEqual(call["context_mode"], mode)
                self.assertEqual(call["question"], expected["question"])
                self.assertEqual(tuple((item.origin, item.context_item_id, item.source_id, item.source_version_id)
                    for item in call["context_sources"]), expected_sources)
                self.assertEqual(self.notebooks.required[0][1], "notebook-cp3")
                self._assert_s42_original_logical_request(expected, key)

    async def test_s42_knowledge_registration_metadata_reaches_internal_question_input_only(self):
        """ASGI/포트 전달 시험이다. 실제 package 등록·PG·Provider 검증으로 계산하지 않는다."""
        from types import SimpleNamespace
        registration='s42-exact-registration-not-derived-from-package'
        resolved=SimpleNamespace(package_id='package-daon3',source_id='source-package-daon3',
            source_version_id='version-package-daon3',digest_sha256='a'*64,knowledge_registration_id=registration)
        body={'notebook_id':'notebook-cp3','question':'선택한 지식의 원등록을 확인해줘',
            'knowledge_context':{'mode':'mixed','resources':[
                {'resource_kind':'knowledge_package','resource_id':'package-daon3'},
                {'resource_kind':'source','resource_id':'source-raw','version_id':'version-raw'}]}}
        key='s42-registration-carry-0001'
        with self._authenticated(),patch.object(self.knowledge,'resolve_question_sources',return_value=(resolved,)) as resolve:
            response=await self.client.post('/api/v1/workspaces/workspace-001/questions',
                cookies={WEB_SESSION_COOKIE:'opaque-session'},headers={'Idempotency-Key':key},json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(resolve.call_count,1)
        self.assertEqual(resolve.call_args.args[1],('package-daon3',))
        self._assert_s42_original_logical_request(body,key)
        sources=self.service.calls[0][1]['context_sources']
        self.assertEqual([(item.origin,item.context_item_id,item.source_id,item.source_version_id,item.digest_sha256)
            for item in sources],[('daon_knowledge','package-daon3','source-package-daon3','version-package-daon3','a'*64),
                ('raw_source','source-raw','source-raw','version-raw',None)])
        self.assertEqual(getattr(sources[0],'knowledge_registration_id',None),registration,
            'RUNTIME_EXACT_REGISTRATION_METADATA_NOT_CARRIED')
        self.assertIsNone(getattr(sources[1],'knowledge_registration_id',None))
        self.assertNotIn('knowledge_registration_id',response.text)
        self.assertNotIn(registration,response.text)
        self.assertNotIn(registration,repr(self.service.calls[0][1]['request_payload']))

    async def test_s42_internal_registration_metadata_does_not_change_five_field_authorization_fingerprint(self):
        """metadata가 있는 내부 DTO 대역으로 실제 ASGI 지문/ETag를 검사한다. 추가인증 I/O는 대역이다."""
        import hashlib
        import json
        from dataclasses import dataclass
        from datetime import datetime,timezone
        from types import SimpleNamespace
        from daon_user_api.question_answering import TextModelSelection
        from daon_user_api.question_answering_service import QuestionInputSource

        @dataclass(frozen=True,slots=True)
        class MetadataInputSource(QuestionInputSource):
            knowledge_registration_id:str|None=None

        registration='s42-internal-registration-metadata'
        def metadata_source(*args,**kwargs):
            # 현 DTO 확장 전에도 공통 dataclass serializer의 metadata 노출 반례를 실제 HTTP 경계에 넣는다.
            if len(args)<6 and 'knowledge_registration_id' not in kwargs:
                kwargs['knowledge_registration_id']=registration if args[0]=='daon_knowledge' else None
            return MetadataInputSource(*args,**kwargs)

        selection=TextModelSelection('fixture','https://provider.example.com/v1','profile-s42','deployment-s42',
            'fixture-model',4,connection_id='connection-s42',credential_version=2,catalog_version=3)
        wire=b'{"messages":[{"role":"user","content":"S42 synthetic authorization"}]}'
        prepared_sources=[]
        def prepare(context,**kwargs):
            prepared_sources.append(kwargs['context_sources'])
            return SimpleNamespace(selection=selection,provider_payload=wire)
        effective=SimpleNamespace(fingerprint='s42-policy-existing-five-fields',required_approver='workspace_manager',
            mode='allow_approved_external',allowed_provider_kinds=('external_api',),
            allowed_destinations=('provider.example.com',),max_bytes=65536,classification='internal',
            masking_required=True,redaction_required=True)
        dependencies=replace(self.dependencies,egress_policy_service=SimpleNamespace(get_effective=lambda _:effective))
        self.service.prepare_authorization=prepare
        self.knowledge.resolve_question_sources=lambda context,package_ids:tuple(SimpleNamespace(
            package_id=package,source_id='source-'+package,source_version_id='version-'+package,
            digest_sha256='a'*64,knowledge_registration_id=registration) for package in package_ids)
        cases=(
            ('raw_only',[{'resource_kind':'source','resource_id':'source-raw','version_id':'version-raw'}],
                [{'origin':'raw_source','context_item_id':'source-raw','source_id':'source-raw',
                    'source_version_id':'version-raw','digest_sha256':None}]),
            ('daon_priority',[{'resource_kind':'knowledge_package','resource_id':'package-daon3'}],
                [{'origin':'daon_knowledge','context_item_id':'package-daon3','source_id':'source-package-daon3',
                    'source_version_id':'version-package-daon3','digest_sha256':'a'*64}]),
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)),
            base_url='http://127.0.0.1') as client:
            for mode,resources,items in cases:
                with self.subTest(mode=mode):
                    key='s42-auth-projection-'+mode
                    question=' S42 기존 인증지문 유지 '
                    body={'notebook_id':'notebook-cp3','question':question,
                        'knowledge_context':{'mode':mode,'resources':resources},'password':'S42 synthetic not a real password'}
                    with self._authenticated(),patch('daon_user_api.runtime.QuestionInputSource',side_effect=metadata_source),patch.object(
                        self.identity,'issue_step_up_after_reauthentication',return_value=SimpleNamespace(
                            authorization='s42-synthetic-step-up',expires_at=datetime(2026,10,19,tzinfo=timezone.utc))):
                        response=await client.post('/api/v1/workspaces/workspace-001/questions/authorization',
                            cookies={WEB_SESSION_COOKIE:'opaque-session'},headers={'Idempotency-Key':key},json=body)
                    self.assertEqual(response.status_code,201,response.text)
                    self.assertEqual(len(prepared_sources[-1]),1)
                    self.assertTrue(hasattr(prepared_sources[-1][0],'knowledge_registration_id'))
                    self.assertEqual(prepared_sources[-1][0].knowledge_registration_id,
                        registration if mode=='daon_priority' else None)
                    run='run-'+hashlib.sha256(f'tenant-001|workspace-001|notebook-cp3|user-001|{key}'.encode()).hexdigest()[:32]
                    # 기존 다섯 필드는 literal이다. runtime dataclass serializer/지문 helper로 기대값을 만들지 않는다.
                    expected={'actor_id':'user-001','tenant_id':'tenant-001','workspace_id':'workspace-001','run_id':run,
                        'knowledge_context':{'mode':mode,'items':items},
                        'question_fingerprint':'sha256:'+hashlib.sha256(question.strip().encode()).hexdigest(),
                        'provider_payload_fingerprint':'sha256:'+hashlib.sha256(wire).hexdigest(),
                        'provider_kind':'external_api','deployment_id':'deployment-s42','connection_id':'connection-s42',
                        'credential_version':2,'default_version':4,'catalog_version':3,
                        'effective_policy_fingerprint':'s42-policy-existing-five-fields','idempotency_key':key}
                    fingerprint='sha256:'+hashlib.sha256(json.dumps(expected,sort_keys=True,separators=(',',':'),
                        ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()
                    self.assertEqual(response.json()['data']['run_id'],run)
                    self.assertEqual(response.json()['data']['request_fingerprint'],fingerprint,
                        'INTERNAL_METADATA_CHANGED_EXISTING_AUTHORIZATION_FINGERPRINT')
                    self.assertEqual(response.headers['ETag'],f'"question-authorization:{fingerprint}"')
                    self.assertNotIn('knowledge_registration_id',response.text)
                    self.assertNotIn(registration,response.text)

    async def test_authenticated_question_returns_grounded_lineage_without_internal_url(self) -> None:
        with self._authenticated():
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={
                    "Content-Type": "application/json", "X-Trace-Id": TRACE_ID,
                    "Idempotency-Key": "question-cp3-0001",
                },
                json={
                    "notebook_id": "notebook-cp3",
                    "source_id": "source-cp3", "source_version_id": "source-version-cp3",
                    "question": "What is the citation verification phrase?",
                },
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["answer"], "ORANGE-COMPASS-42")
        self.assertEqual(data["citations"][0]["page"], 2)
        self.assertEqual(data["citations"][0]["locator"], {"kind": "page", "value": "2"})
        self.assertNotIn("minio", response.text.casefold())
        self.assertNotIn("localhost", response.text.casefold())
        self.assertEqual(self.service.calls[0][0].workspace_id, "workspace-001")
        self.assertEqual(self.service.calls[0][0].notebook_id, "notebook-cp3")
        self.assertEqual(self.notebooks.required[0][1], "notebook-cp3")

    async def test_question_requires_notebook_scope_before_service(self) -> None:
        with self._authenticated():
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-cp3-0002"},
                json={
                    "source_id": "source-cp3", "source_version_id": "source-version-cp3",
                    "question": "bounded question",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.service.calls, [])

    async def test_question_rejects_unbounded_or_unsafe_idempotency_before_service(self) -> None:
        for invalid_key in ("x" * 15, "x" * 129, "unsafe/key-value"):
            self.service.calls.clear()
            with self._authenticated():
                response = await self.client.post(
                    "/api/v1/workspaces/workspace-001/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": invalid_key},
                    json={
                        "source_id": "source-cp3", "source_version_id": "source-version-cp3",
                        "question": "bounded question",
                    },
                )
            self.assertEqual(response.status_code, 400, invalid_key)
            self.assertEqual(self.service.calls, [])

    async def test_mixed_knowledge_context_resolves_packages_and_rejects_legacy_pair(self) -> None:
        body = {
            "notebook_id": "notebook-cp3",
            "question": "승인 지식과 원문을 종합해줘",
            "knowledge_context": {
                "mode": "mixed",
                "resources": [
                    {"resource_kind": "knowledge_package", "resource_id": "package-daon3"},
                    {"resource_kind": "source", "resource_id": "source-raw", "version_id": "version-raw"},
                ],
            },
        }
        with self._authenticated():
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-mixed-0001"}, json=body,
            )
        self.assertEqual(response.status_code, 200, response.text)
        call = self.service.calls[-1][1]
        self.assertEqual(call["context_mode"], "mixed")
        self.assertEqual(
            [(item.origin, item.context_item_id) for item in call["context_sources"]],
            [("daon_knowledge", "package-daon3"), ("raw_source", "source-raw")],
        )

        with self._authenticated():
            invalid = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"Idempotency-Key": "question-mixed-0002"},
                json={**body, "source_id": "source-raw", "source_version_id": "version-raw"},
            )
        self.assertEqual(invalid.status_code, 400)

    async def test_citation_content_is_inline_pdf_after_current_access_recheck(self) -> None:
        with self._authenticated():
            response = await self.client.get(
                "/api/v1/workspaces/workspace-001/citations/citation-cp3/content?notebook_id=notebook-001",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertEqual(response.headers["x-citation-page"], "2")

    async def test_daon_text_citation_returns_inline_plain_text(self) -> None:
        self.dependencies.citation_content_repository.text_mode = True
        with self._authenticated():
            response = await self.client.get(
                "/api/v1/workspaces/workspace-001/citations/citation-cp3/content?notebook_id=notebook-001",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "승인된 Daon 일반 텍스트 지식")
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(response.headers["x-citation-locator-kind"], "section")

    async def test_image_citation_returns_inline_original_and_exact_region_header(self) -> None:
        self.dependencies.citation_content_repository.image_mode = True
        from test_image_document_understanding import image_bytes
        with self._authenticated():
            response = await self.client.get("/api/v1/workspaces/workspace-001/citations/citation-cp3/content?notebook_id=notebook-001", cookies={WEB_SESSION_COOKIE: "opaque-session"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, image_bytes())
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["x-citation-region"], "5,7,80,20,120,60")
        self.assertEqual(response.headers["x-citation-locator-kind"], "image_region")
        self.assertNotIn("x-citation-page", response.headers)

    async def test_xlsx_original_is_raw_json_with_existing_safe_headers(self) -> None:
        self.dependencies.citation_content_repository.xlsx_mode = True
        with self._authenticated():
            response = await self.client.get('/api/v1/workspaces/workspace-001/citations/citation-cp3/content?notebook_id=notebook-001', cookies={WEB_SESSION_COOKIE: 'opaque-session'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'application/json')
        self.assertEqual(response.headers['x-citation-locator-kind'], 'sheet_cell')
        self.assertIn('inline', response.headers['content-disposition'])
        self.assertIn('etag', response.headers)
        self.assertNotIn('x-citation-page', response.headers)
        self.assertNotIn('x-citation-region', response.headers)
        self.assertEqual(response.json()['source_version_id'], 'source-version-cp3')
        self.assertEqual(response.json()['selected_range'], 'B2:C3')
        self.assertNotIn('data', response.json())

    async def test_docx_original_is_raw_codepoint_json_without_fake_page_header(self) -> None:
        self.dependencies.citation_content_repository.docx_mode = True
        with self._authenticated():
            response = await self.client.get('/api/v1/workspaces/workspace-001/citations/citation-cp3/content?notebook_id=notebook-001', cookies={WEB_SESSION_COOKIE: 'opaque-session'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'application/json')
        self.assertEqual(response.headers['x-citation-locator-kind'], 'docx_block')
        self.assertIn('inline', response.headers['content-disposition'])
        self.assertIn('etag', response.headers)
        self.assertNotIn('x-citation-page', response.headers)
        self.assertNotIn('x-citation-region', response.headers)
        view = response.json()
        self.assertEqual(view['source_version_id'], 'source-version-cp3')
        self.assertEqual(view['blocks'][0]['text'][view['selected_start']:view['selected_end']], '😀')
        self.assertNotIn('data', view)

    async def test_audio_representation_is_authenticated_no_store_binary_without_page_header(self):
        from test_audio_source import wav_bytes
        path='/api/v1/workspaces/workspace-001/citations/citation-cp3/content?notebook_id=notebook-001'
        with self._authenticated():
            response=await self.client.get(path+'&representation=audio',cookies={WEB_SESSION_COOKIE:'opaque-session'})
            invalid=await self.client.get(path+'&representation=invalid',cookies={WEB_SESSION_COOKIE:'opaque-session'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.content,wav_bytes())
        self.assertEqual(response.headers['content-type'],'audio/wav')
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        self.assertEqual(response.headers['x-content-type-options'],'nosniff')
        self.assertNotIn('x-citation-page',response.headers)
        self.assertEqual(invalid.status_code,422)
        denied=await self.client.get(path+'&representation=audio')
        self.assertEqual(denied.status_code,401)


if __name__ == "__main__":
    unittest.main()
