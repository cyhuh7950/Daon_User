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


class FakeNotebookService:
    def __init__(self) -> None:
        self.required = []

    def require_selected_bindings(self, context, notebook_id, *, required):  # type: ignore[no-untyped-def]
        self.required.append((context, notebook_id, required))


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

    def read_citation_content(self, context, citation_id):  # type: ignore[no-untyped-def]
        if citation_id != "citation-cp3":
            raise AssertionError(citation_id)
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


if __name__ == "__main__":
    unittest.main()
