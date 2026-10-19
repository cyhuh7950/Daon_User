from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
import socket
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingRequest,
    ServerProviderCredentialResolver,
    UpstageDocumentUnderstandingAdapter,
    UrlLibDocumentUnderstandingTransport,
    resolve_document_model_selection,
)
from daon_user_api.provider_settings import (
    ModelDeploymentView,
    ProviderProfileView,
    ProviderSettingsSnapshot,
)


PDF = b"%PDF-1.4\n% synthetic contract fixture\n%%EOF\n"


class FailureEvidenceTransportTests(unittest.TestCase):
    def test_runtime_unknown_and_typed_connect_failures_do_not_confuse_null_health_requeue(self):
        # 실제 DB 현재 인가/비용 producer가 아닌 R1 순수 판정 경계의 합성 입력이다.
        from daon_user_api.document_readiness import (CurrentReprocessEligibility, ReadinessModeProof,
            RoleWaitEvidence, ValidatedReadinessObservation, decide_reprocess)
        now = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
        for failure, expected in (
            (urllib.error.HTTPError("https://models.example", 503, "private", {}, io.BytesIO()), "READINESS_NOT_READY"),
            (TimeoutError("CANARY_PRIVATE"), "READINESS_NOT_READY"),
            (urllib.error.URLError(ConnectionRefusedError("CANARY_PRIVATE")), "READINESS_COST_UNCONFIRMED")):
            with self.subTest(failure=type(failure).__name__):
                class Opener:
                    def open(inner, *args, **kwargs): raise failure
                with patch("daon_user_api.document_understanding_adapter.urllib.request.build_opener", return_value=Opener()):
                    with self.assertRaises(DocumentUnderstandingError) as caught:
                        UrlLibDocumentUnderstandingTransport().post_json_no_auth(url="https://models.example/api/chat", payload={}, timeout_seconds=1)
                wait = RoleWaitEvidence("tenant-a", "workspace-a", "actor-a", "notebook-a", "source-a", "version-a", "run-a",
                    "text_generation", caught.exception.failure_evidence.failure_kind, ReadinessModeProof("auto", "request"),
                    "connection-a", "a" * 64, caught.exception.code, now - timedelta(seconds=180), now - timedelta(seconds=120))
                observation = ValidatedReadinessObservation(1, "rdy-" + "1" * 32, "connection-a", "CUSTOM", None,
                    "b" * 64, 1, now - timedelta(seconds=61), now, "provider_connection_check", "healthy", 1, "c" * 64)
                current = CurrentReprocessEligibility("tenant-a", "workspace-a", "actor-a", "notebook-a", "version-a",
                    "text_generation", "a" * 64, "connection-a", "b" * 64, 1, 1,
                    True, True, True, True, True, True, False, False, False, "unknown", "allowed", "run-a")
                decision = decide_reprocess(wait, observation, current, db_now=now)
                self.assertEqual((decision.create_run, decision.safe_code), (False, expected))

    def upstream_error(self, status):
        class Opener:
            def open(inner, request, timeout):
                raise urllib.error.HTTPError(request.full_url, status, "private response", {}, io.BytesIO(b"CANARY_PRIVATE"))
        with patch("daon_user_api.document_understanding_adapter.urllib.request.build_opener", return_value=Opener()):
            with self.assertRaises(DocumentUnderstandingError) as caught:
                UrlLibDocumentUnderstandingTransport().post_json_no_auth(url="https://models.example/api/chat", payload={}, timeout_seconds=1)
        return caught.exception

    def test_actual_http_transport_classification_is_separate_from_legacy_error_and_retryable(self):
        for status, kind, code, public_status, retryable in (
            (408, "role_runtime", "UNDERSTANDING_PROVIDER_UNAVAILABLE", 503, True),
            (429, "role_runtime", "UNDERSTANDING_PROVIDER_UNAVAILABLE", 503, True),
            (500, "role_runtime", "UNDERSTANDING_PROVIDER_UNAVAILABLE", 503, True),
            (503, "role_runtime", "UNDERSTANDING_PROVIDER_UNAVAILABLE", 503, True),
            (401, "non_availability", "UNDERSTANDING_PROVIDER_REJECTED", 502, False),
            (400, "non_availability", "UNDERSTANDING_PROVIDER_REJECTED", 502, False)):
            with self.subTest(status=status):
                error = self.upstream_error(status)
                self.assertEqual((error.code, error.status, error.retryable), (code, public_status, retryable))
                evidence = getattr(error, "failure_evidence", None)
                self.assertIsNotNone(evidence, "실제 HTTP 분류 근거 미구현")
                self.assertEqual((evidence.capability, evidence.dependency_connection_id, evidence.failure_kind), (None, None, kind))
                self.assertNotIn("CANARY_PRIVATE", str(error))
                self.assertNotIn("CANARY_PRIVATE", repr(evidence))

    def test_actual_network_and_response_validation_classification_never_uses_exception_message(self):
        for failure in (urllib.error.URLError("CANARY_PRIVATE"), TimeoutError("CANARY_PRIVATE"), OSError("CANARY_PRIVATE")):
            with self.subTest(failure=type(failure).__name__):
                class Opener:
                    def open(inner, *args, **kwargs): raise failure
                with patch("daon_user_api.document_understanding_adapter.urllib.request.build_opener", return_value=Opener()):
                    with self.assertRaises(DocumentUnderstandingError) as caught:
                        UrlLibDocumentUnderstandingTransport().post_json_no_auth(url="https://models.example/api/chat", payload={}, timeout_seconds=1)
                self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.retryable),
                                 ("UNDERSTANDING_PROVIDER_UNAVAILABLE", 503, True))
                self.assertEqual(caught.exception.failure_evidence.failure_kind, "unknown")
                self.assertNotIn("CANARY_PRIVATE", str(caught.exception))
        class Response:
            def __enter__(inner): return inner
            def __exit__(inner, *args): return False
            def read(inner, size): return b'not-json CANARY_PRIVATE'
        class Opener:
            def open(inner, *args, **kwargs): return Response()
        with patch("daon_user_api.document_understanding_adapter.urllib.request.build_opener", return_value=Opener()):
            with self.assertRaises(DocumentUnderstandingError) as caught:
                UrlLibDocumentUnderstandingTransport().post_json_no_auth(url="https://models.example/api/chat", payload={}, timeout_seconds=1)
        self.assertEqual((caught.exception.status, caught.exception.retryable, caught.exception.failure_evidence.failure_kind),
                         (502, False, "non_availability"))
        self.assertNotIn("CANARY_PRIVATE", str(caught.exception))

    def test_pdf_stage_binds_actual_semantic_or_parser_without_mutating_reused_error(self):
        error = self.upstream_error(503)
        selection = replace(resolve_document_model_selection(provider_snapshot()),
                            semantic_connection_id="semantic-a", parser_connection_id="parser-b")
        request = DocumentUnderstandingRequest("source-a", "version-a", "qa.pdf", PDF, "trace-a", "prompt-a", "policy-a")
        class Transport(RecordingTransport):
            failed_stage = "semantic"
            def post_json(inner, **kwargs):
                if inner.failed_stage == "semantic": raise error
                return super().post_json(**kwargs)
            def post_multipart(inner, **kwargs):
                if inner.failed_stage == "parser": raise error
                return super().post_multipart(**kwargs)
        transport = Transport()
        adapter = UpstageDocumentUnderstandingAdapter(transport=transport, api_key="synthetic-memory-key")
        previous = []
        previous_errors = []
        for stage, capability, connection in (("semantic", "image_understanding", "semantic-a"), ("parser", "document_parsing", "parser-b")):
            transport.failed_stage = stage
            with self.assertRaises(DocumentUnderstandingError) as caught: adapter.understand(request, selection)
            evidence = getattr(caught.exception, "failure_evidence", None)
            self.assertIsNotNone(evidence, "PDF 실제 stage 근거 미구현")
            self.assertEqual((evidence.capability, evidence.dependency_connection_id, evidence.failure_kind),
                             (capability, connection, "role_runtime"))
            self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.retryable),
                             ("UNDERSTANDING_PROVIDER_UNAVAILABLE", 503, True))
            self.assertIsNot(caught.exception, error)
            previous.append(evidence)
            previous_errors.append(caught.exception)
        self.assertEqual(previous[0].capability, "image_understanding")
        self.assertIsNone(error.failure_evidence.capability)
        self.assertIsNone(error.failure_evidence.dependency_connection_id)
        error = previous_errors[0]  #  이미 다른 stage에 결속된 오류는 가용성 근거로 재사용하지 않는다.
        with self.assertRaises(DocumentUnderstandingError) as caught: adapter.understand(request, selection)
        self.assertEqual((caught.exception.failure_evidence.capability, caught.exception.failure_evidence.dependency_connection_id,
                          caught.exception.failure_evidence.failure_kind), ("document_parsing", "parser-b", "unknown"))
        self.assertEqual(error.failure_evidence.capability, "image_understanding")

    def test_typed_connect_reason_only_and_response_wait_are_separated_without_message_inference(self):
        for failure, response_started, kind in (
            (urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "CANARY_PRIVATE")), False, "connection_availability"),
            (urllib.error.URLError(ConnectionRefusedError("CANARY_PRIVATE")), False, "connection_availability"),
            (socket.gaierror(socket.EAI_NONAME, "CANARY_PRIVATE"), False, "connection_availability"),
            (ConnectionRefusedError("CANARY_PRIVATE"), False, "connection_availability"),
            (urllib.error.URLError("ConnectionRefusedError DNS CANARY_PRIVATE"), False, "unknown"),
            (urllib.error.URLError(TimeoutError("CANARY_PRIVATE")), False, "unknown"),
            (ConnectionResetError("CANARY_PRIVATE"), False, "unknown"),
            (TimeoutError("CANARY_PRIVATE"), True, "role_runtime"),
            (ConnectionRefusedError("CANARY_PRIVATE"), True, "unknown")):
            with self.subTest(reason=type(failure).__name__, response_started=response_started):
                class Response:
                    def __enter__(inner): return inner
                    def __exit__(inner, *args): return False
                    def read(inner, size): raise failure
                class Opener:
                    def open(inner, *args, **kwargs):
                        if response_started: return Response()
                        raise failure
                with patch("daon_user_api.document_understanding_adapter.urllib.request.build_opener", return_value=Opener()):
                    with self.assertRaises(DocumentUnderstandingError) as caught:
                        UrlLibDocumentUnderstandingTransport().post_json_no_auth(url="https://models.example/api/chat", payload={}, timeout_seconds=1)
                self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.retryable),
                                 ("UNDERSTANDING_PROVIDER_UNAVAILABLE", 503, True))
                self.assertEqual(caught.exception.failure_evidence.failure_kind, kind)
                self.assertNotIn("CANARY_PRIVATE", str(caught.exception))

    def test_stage_validation_failure_is_non_availability_and_missing_connection_stays_none(self):
        transport = RecordingTransport()
        transport.post_json = lambda **kwargs: {}
        request = DocumentUnderstandingRequest("source-a", "version-a", "qa.pdf", PDF, "trace-a", "prompt-a", "policy-a")
        with self.assertRaises(DocumentUnderstandingError) as caught:
            UpstageDocumentUnderstandingAdapter(transport=transport, api_key="synthetic-memory-key").understand(
                request, resolve_document_model_selection(provider_snapshot()))
        evidence = getattr(caught.exception, "failure_evidence", None)
        self.assertIsNotNone(evidence)
        self.assertEqual((evidence.capability, evidence.dependency_connection_id, evidence.failure_kind),
                         ("image_understanding", None, "non_availability"))


def provider_snapshot() -> ProviderSettingsSnapshot:
    return ProviderSettingsSnapshot(
        workspace_id="workspace-cp3",
        profiles=(ProviderProfileView(
            "provider-upstage", "UPSTAGE", "external_api",
            "https://api.upstage.ai/v1", True, True, 2,
        ),),
        deployments=(
            ModelDeploymentView(
                "deployment-understanding", "provider-upstage", "UPSTAGE",
                "information-extract", ("vision",), True, True, 3,
            ),
            ModelDeploymentView(
                "deployment-parser", "provider-upstage", "UPSTAGE",
                "document-parse", ("document_parser",), True, True, 4,
            ),
        ),
        role_bindings={
            "vision": "deployment-understanding",
            "document_parser": "deployment-parser",
        },
        binding_version=5,
    )


class RecordingTransport:
    def __init__(
        self, *, semantic_error: Exception | None = None,
        semantic_facts: tuple[str, ...] | None = None,
        parser_text: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.semantic_error = semantic_error
        self.semantic_facts = semantic_facts or ("Vision first", "Parser validates later")
        self.parser_text = parser_text or "Daon CP3 contract test. Vision first. Parser validates later."

    def post_json(self, *, url: str, api_key: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        self.calls.append(("semantic", url, payload))
        if self.semantic_error is not None:
            raise self.semantic_error
        return {
            "model": "information-extract-260610",
            "choices": [{"message": {"content": json.dumps({
                "title": "Daon CP3",
                "summary": "Vision first. Parser validates later.",
                "key_facts": self.semantic_facts,
            })}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
        }

    def post_multipart(self, *, url: str, api_key: str, fields: dict[str, str], filename: str, content: bytes, timeout_seconds: float) -> dict[str, object]:
        self.calls.append(("parser", url, {"fields": fields, "filename": filename, "content": content}))
        return {
            "model": "document-parse-260630",
            "content": {
                "text": self.parser_text,
                "markdown": self.parser_text,
                "html": f"<p>{self.parser_text}</p>",
            },
            "elements": [{"page": 1, "category": "paragraph", "content": {"text": "Daon CP3 contract test."}}],
            "usage": {"pages": 1},
        }


class OptionalQuestionTransportWaitTests(unittest.TestCase):
    def test_json_question_transport_passes_none_or_finite_wait_to_opener(self):
        waits=[]
        class Opener:
            def open(self,request,timeout):
                waits.append(timeout);return io.BytesIO(b'{"message":{"content":"ok"}}')
        with patch('daon_user_api.document_understanding_adapter.urllib.request.build_opener',return_value=Opener()):
            for wait in (None,5.0):
                result=UrlLibDocumentUnderstandingTransport().post_json_no_auth(url='http://models.example.com/api/chat',payload={},timeout_seconds=wait)
                self.assertEqual(result['message']['content'],'ok')
        self.assertEqual(waits,[None,5.0])


class DocumentModelSelectionTests(unittest.TestCase):
    def test_selected_vision_and_parser_deployments_are_frozen_from_snapshot(self) -> None:
        selection = resolve_document_model_selection(provider_snapshot())

        self.assertEqual(selection.provider_code, "UPSTAGE")
        self.assertEqual(selection.semantic_deployment_id, "deployment-understanding")
        self.assertEqual(selection.semantic_model_id, "information-extract")
        self.assertEqual(selection.parser_deployment_id, "deployment-parser")
        self.assertEqual(selection.parser_model_id, "document-parse")
        self.assertEqual(selection.binding_version, 5)

    def test_missing_parser_binding_fails_closed(self) -> None:
        snapshot = provider_snapshot()
        snapshot.role_bindings.pop("document_parser")

        with self.assertRaisesRegex(DocumentUnderstandingError, "DOCUMENT_PARSER_MODEL_NOT_SELECTED"):
            resolve_document_model_selection(snapshot)


class AnthropicHeaderTransportTests(unittest.TestCase):
    def test_keyless_messages_reach_transport_without_auth_header(self) -> None:
        captured = []

        class Opener:
            def open(self, request, timeout):
                captured.append((request, timeout))
                return io.BytesIO(b'{"content":[{"type":"text","text":"ok"}]}')

        with patch("daon_user_api.document_understanding_adapter.urllib.request.build_opener", return_value=Opener()):
            result = UrlLibDocumentUnderstandingTransport().post_json_headers(
                url="https://models.example.com/v1/messages",
                headers={"anthropic-version": "2023-06-01"},
                payload={"model": "manual-a"}, timeout_seconds=5,
            )

        self.assertEqual(result["content"][0]["text"], "ok")
        self.assertEqual(len(captured), 1)
        request, timeout = captured[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertIsNone(request.get_header("X-api-key"))
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(timeout, 5)

    def test_malformed_provided_key_is_rejected_before_transport(self) -> None:
        with patch("daon_user_api.document_understanding_adapter.urllib.request.build_opener") as build:
            with self.assertRaisesRegex(DocumentUnderstandingError, "UNDERSTANDING_PROVIDER_HEADERS_INVALID"):
                UrlLibDocumentUnderstandingTransport().post_json_headers(
                    url="https://models.example.com/v1/messages",
                    headers={"x-api-key": "bad\r\nInjected: value", "anthropic-version": "2023-06-01"},
                    payload={"model": "manual-a"}, timeout_seconds=5,
                )
        build.assert_not_called()


class UpstageDocumentUnderstandingAdapterTests(unittest.TestCase):
    @staticmethod
    def _understand_with_evidence(
        semantic_facts: tuple[str, ...], parser_text: str,
    ):
        return UpstageDocumentUnderstandingAdapter(
            transport=RecordingTransport(
                semantic_facts=semantic_facts, parser_text=parser_text,
            ),
            api_key="up_test_secret",
        ).understand(
            DocumentUnderstandingRequest(
                source_id="source-cp3", source_version_id="source-version-cp3",
                filename="contract.pdf", content=PDF, trace_id="trace-cp3",
                prompt_version="understanding-prompt-v1", policy_version="policy-v1",
            ),
            resolve_document_model_selection(provider_snapshot()),
        )

    def test_original_pdf_semantic_call_precedes_separate_parser_validation(self) -> None:
        transport = RecordingTransport()
        adapter = UpstageDocumentUnderstandingAdapter(transport=transport, api_key="up_test_secret")

        result = adapter.understand(
            DocumentUnderstandingRequest(
                source_id="source-cp3", source_version_id="source-version-cp3",
                filename="contract.pdf", content=PDF, trace_id="trace-cp3",
                prompt_version="understanding-prompt-v1", policy_version="policy-v1",
            ),
            resolve_document_model_selection(provider_snapshot()),
        )

        self.assertEqual([call[0] for call in transport.calls], ["semantic", "parser"])
        self.assertEqual(
            transport.calls[0][1],
            "https://api.upstage.ai/v1/information-extraction",
        )
        semantic_payload = transport.calls[0][2]
        self.assertEqual(semantic_payload["model"], "information-extract")
        self.assertEqual(len(semantic_payload["messages"]), 1)  # type: ignore[arg-type]
        self.assertEqual(semantic_payload["messages"][0]["role"], "user")  # type: ignore[index]
        self.assertEqual(len(semantic_payload["messages"][0]["content"]), 1)  # type: ignore[index]
        image_part = semantic_payload["messages"][0]["content"][0]  # type: ignore[index]
        self.assertEqual(image_part["type"], "image_url")
        image_url = image_part["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:application/pdf;base64,"))
        self.assertTrue(semantic_payload["response_format"]["json_schema"]["strict"])  # type: ignore[index]
        schema_properties = semantic_payload["response_format"]["json_schema"]["schema"]["properties"]  # type: ignore[index]
        self.assertIn("original source language", schema_properties["title"]["description"].lower())
        self.assertIn("do not translate", schema_properties["summary"]["description"].lower())
        self.assertIn("do not translate or paraphrase", schema_properties["key_facts"]["description"].lower())
        self.assertNotIn("/chat/completions", transport.calls[0][1])
        self.assertNotIn("document-parse", str(semantic_payload))
        self.assertEqual(
            transport.calls[1][1],
            "https://api.upstage.ai/v1/document-digitization",
        )
        self.assertEqual(transport.calls[1][2]["fields"]["model"], "document-parse")  # type: ignore[index]
        self.assertEqual(
            transport.calls[1][2]["fields"]["output_formats"],  # type: ignore[index]
            '["text","html","markdown"]',
        )
        self.assertEqual(transport.calls[1][2]["content"], PDF)  # type: ignore[index]
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.substates, (
            "vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation",
        ))
        self.assertEqual(result.semantic.summary, "Vision first. Parser validates later.")
        self.assertEqual(result.parser.role, "validation_only")
        self.assertEqual(result.parser.pages, (1,))
        self.assertEqual(result.parser.page_texts, ((1, "Daon CP3 contract test. Vision first. Parser validates later."),))
        self.assertEqual(result.lineage["semantic_model_revision"], "information-extract-260610")
        self.assertEqual(result.lineage["parser_model_revision"], "document-parse-260630")
        self.assertEqual(result.lineage["parser_role"], "validation_only")

    def test_parser_is_never_called_when_semantic_understanding_fails(self) -> None:
        transport = RecordingTransport(
            semantic_error=DocumentUnderstandingError(
                "UNDERSTANDING_PROVIDER_UNAVAILABLE", status=503, retryable=True,
            )
        )
        adapter = UpstageDocumentUnderstandingAdapter(transport=transport, api_key="up_test_secret")

        with self.assertRaisesRegex(DocumentUnderstandingError, "UNDERSTANDING_PROVIDER_UNAVAILABLE") as caught:
            adapter.understand(
                DocumentUnderstandingRequest(
                    source_id="source-cp3", source_version_id="source-version-cp3",
                    filename="contract.pdf", content=PDF, trace_id="trace-cp3",
                    prompt_version="understanding-prompt-v1", policy_version="policy-v1",
                ),
                resolve_document_model_selection(provider_snapshot()),
            )

        self.assertTrue(caught.exception.retryable)
        self.assertEqual([call[0] for call in transport.calls], ["semantic"])

    def test_semantic_paraphrases_with_matching_korean_and_english_anchors_are_ready(self) -> None:
        result = self._understand_with_evidence(
            (
                "프로젝트 코드는 ALPHA-731입니다.",
                "검토일은 2026년 8월 8일입니다.",
                "Records are retained for 30 days.",
                "The citation phrase is ORANGE-COMPASS-42.",
                "검토자는 배포 전에 문서를 다시 확인해야 합니다.",
            ),
            (
                "ALPHA-731 프로젝트의 검토일: 2026-08-08. "
                "보존 기간은 30일이며 인용 문구 ORANGE-COMPASS-42가 포함된다. "
                "배포 전 재검토가 필요하다."
            ),
        )

        self.assertEqual(result.status, "ready")
        self.assertIsNone(result.conflict)

    def test_identifier_anchor_mismatch_requires_review(self) -> None:
        result = self._understand_with_evidence(
            ("프로젝트 코드는 ALPHA-731입니다.",),
            "프로젝트 코드는 ALPHA-999입니다.",
        )

        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.conflict, "UNDERSTANDING_PARSER_CONFLICT")

    def test_missing_date_anchor_requires_review(self) -> None:
        result = self._understand_with_evidence(
            ("The review date is 2026-08-08.",),
            "검토 일정은 추후 확정됩니다.",
        )

        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.conflict, "UNDERSTANDING_PARSER_CONFLICT")

    def test_missing_numeric_unit_anchor_requires_review(self) -> None:
        result = self._understand_with_evidence(
            ("Records are retained for 30 days.",),
            "기록은 정책에 따라 보존됩니다.",
        )

        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.conflict, "UNDERSTANDING_PARSER_CONFLICT")

    def test_anchorless_natural_language_difference_is_not_material_conflict(self) -> None:
        result = self._understand_with_evidence(
            ("검토자는 배포 전에 문서를 다시 확인해야 합니다.",),
            "A reviewer should perform another document check before release.",
        )

        self.assertEqual(result.status, "ready")
        self.assertIsNone(result.conflict)

    def test_secret_is_read_server_side_and_never_appears_in_errors(self) -> None:
        with patch.dict(os.environ, {"UPSTAGE_API_KEY": "up_never_expose"}, clear=False):
            secret = ServerProviderCredentialResolver().resolve("UPSTAGE")
        self.assertEqual(secret, "up_never_expose")

        adapter = UpstageDocumentUnderstandingAdapter(
            transport=RecordingTransport(), api_key=secret,
        )
        with self.assertRaises(DocumentUnderstandingError) as caught:
            adapter.understand(
                DocumentUnderstandingRequest(
                    source_id="source-cp3", source_version_id="source-version-cp3",
                    filename="not-pdf.pdf", content=b"not a pdf", trace_id="trace-cp3",
                    prompt_version="understanding-prompt-v1", policy_version="policy-v1",
                ),
                resolve_document_model_selection(provider_snapshot()),
            )
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(adapter))


if __name__ == "__main__":
    unittest.main()
