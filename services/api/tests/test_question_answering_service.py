from __future__ import annotations

import json
import unittest
from dataclasses import replace
from contextlib import contextmanager

from daon_user_api.document_index_postgres import IndexedEvidenceChunk
from daon_user_api.provider_settings import (
    ModelDeploymentView, ProviderProfileView, ProviderSettingsSnapshot,
)
from daon_user_api.question_answering_postgres import (
    QuestionContext, QuestionRepositoryError, ReadyQuestionSource, StoredCitation,
    StoredQuestionAnswer,
)
from daon_user_api.question_answering_service import (
    QuestionAdapterRegistry, QuestionAnsweringError, QuestionAnsweringService, QuestionInputSource,
    is_general_conversation_intent,
)
from daon_user_api.workspace_model_defaults import ResolvedModel
from daon_user_api.question_answering import GeneralConversationRequest, GroundedQuestionRequest, OllamaTextGenerationAdapter
from daon_user_api.data_canon import canonical_json_bytes


class S18MediaCapDiagnosticTests(unittest.TestCase):
    def test_s18_media_result_insufficient_notice_enum_is_checked_without_repair(self):
        for answer in ("다른 부족 안내입니다.", " 선택한 원문 근거에 질문에 답할 정보가 없습니다. "):
            with self.subTest(answer_length=len(answer)):
                response = {"choices": [{"message": {"content": json.dumps({"result": {
                    "answer": answer, "cited_chunk_ids": [], "insufficient": True,
                }})}}]}
                original = canonical_json_bytes(response)
                service, context, transport, repo, _, _, _ = self.setup_flow(response=response)
                with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as logs:
                    with self.assertRaises(QuestionAnsweringError) as error:
                        service.ask(context, source_id="source-cp3", source_version_id="source-version-cp3", question="QUESTION_CANARY", run_id="run-s18-enum-invalid")
                self.assertEqual(error.exception.code, "TEXT_GENERATION_RESPONSE_INVALID")
                self.assertIn("failure_reason=insufficient_answer_invalid", " ".join(logs.output))
                self.assertNotIn(answer, " ".join(logs.output))
                self.assertEqual(canonical_json_bytes(response), original)
                self.assertIsNone(repo.persisted)
                self.assertEqual(len(transport.calls), 1)

    def test_s18_media_result_schema_has_exclusive_nested_sufficiency_branches(self):
        service, context, transport, _, _, selection, evidence = self.setup_flow()
        prepared = service._adapter_registry.prepare(selection, evidence, "질문", context.trace_id, transport)
        original = canonical_json_bytes(prepared.provider_payload)
        actual = service._prepare_qa_structured_payload(context, prepared).provider_payload
        self.assertEqual(actual["response_format"]["json_schema"]["schema"], {
            "type": "object", "properties": {"result": {"anyOf": [
                {"type": "object", "properties": {
                    "answer": {"type": "string", "enum": ["선택한 원문 근거에 질문에 답할 정보가 없습니다."]},
                    "cited_chunk_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 0},
                    "insufficient": {"type": "boolean", "enum": [True]},
                }, "required": ["answer", "cited_chunk_ids", "insufficient"], "additionalProperties": False},
                {"type": "object", "properties": {
                    "answer": {"type": "string"},
                    "cited_chunk_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "insufficient": {"type": "boolean", "enum": [False]},
                }, "required": ["answer", "cited_chunk_ids", "insufficient"], "additionalProperties": False},
            ]}}, "required": ["result"], "additionalProperties": False,
        })
        self.assertEqual(actual["messages"][1:], prepared.provider_payload["messages"][1:])
        self.assertTrue(actual["messages"][0]["content"].startswith(prepared.provider_payload["messages"][0]["content"]))
        self.assertIn("root result envelope takes precedence", actual["messages"][0]["content"])
        self.assertEqual(canonical_json_bytes(prepared.provider_payload), original)

    def test_s18_media_result_envelope_preserves_values_and_rejects_contradiction(self):
        cases = (
            ({"answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False}, None),
            ({"answer": "선택한 원문 근거에 질문에 답할 정보가 없습니다.", "cited_chunk_ids": [], "insufficient": True}, None),
            ({"answer": "BODY_CANARY", "cited_chunk_ids": ["chunk-page-2"], "insufficient": True}, "TEXT_GENERATION_GROUNDING_INVALID"),
        )
        for inner, error_code in cases:
            with self.subTest(insufficient=inner["insufficient"], contradiction=bool(error_code)):
                response = {"choices": [{"message": {"content": json.dumps({"result": inner})}}]}
                original = canonical_json_bytes(response)
                service, context, transport, repo, egress, _, _ = self.setup_flow(response=response)
                args = dict(source_id="source-cp3", source_version_id="source-version-cp3", question="QUESTION_CANARY")
                authorization = service.prepare_authorization(context, **args)
                with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as logs:
                    if error_code:
                        with self.assertRaises(QuestionAnsweringError) as error:
                            service.ask(context, run_id="run-s18-result", **args)
                        self.assertEqual(error.exception.code, error_code)
                        self.assertIsNone(repo.persisted)
                        self.assertIn("failure_reason=insufficient_with_citations", " ".join(logs.output))
                    else:
                        result = service.ask(context, run_id="run-s18-result", **args)
                        self.assertEqual(result.answer, inner["answer"])
                        self.assertIs(result.insufficient, inner["insufficient"])
                        self.assertEqual(len(result.citations), len(inner["cited_chunk_ids"]))
                        self.assertIsNotNone(repo.persisted)
                        self.assertIn("failure_reason=none", " ".join(logs.output))
                self.assertEqual(canonical_json_bytes(response), original, "원응답 객체는 변경하지 않는다")
                self.assertEqual(authorization.provider_payload, egress.wire)
                self.assertEqual(authorization.provider_payload, canonical_json_bytes(transport.calls[0]["payload"]))
                self.assertEqual(len(transport.calls), 1)
                for canary in ("CANARY", "ORANGE-COMPASS-42", "chunk-page-2"):
                    self.assertNotIn(canary, " ".join(logs.output))

    def test_s18_media_result_envelope_rejects_flat_extra_missing_and_coerced_types(self):
        valid = {"answer": "답변", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False}
        cases = [valid, {"result": valid, "extra": "SECRET_CANARY"}, {"result": {**valid, "extra": "SECRET_CANARY"}},
                 {"result": []}, {"result": None}, [], {"result": {"answer": "답변", "insufficient": False}}]
        cases += [{"result": {**valid, key: value}} for key, value in (
            ("answer", 1), ("answer", []), ("answer", None),
            ("insufficient", 0), ("insufficient", 1), ("insufficient", "false"),
            ("cited_chunk_ids", "chunk-page-2"), ("cited_chunk_ids", [1]), ("cited_chunk_ids", [True]),
        )]
        for index, body in enumerate(cases):
            with self.subTest(case=index):
                service, context, transport, repo, _, _, _ = self.setup_flow(response={
                    "choices": [{"message": {"content": json.dumps(body)}}],
                })
                with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as logs:
                    with self.assertRaises(QuestionAnsweringError) as error:
                        service.ask(context, source_id="source-cp3", source_version_id="source-version-cp3", question="QUESTION_CANARY", run_id="run-s18-result-invalid")
                self.assertEqual(error.exception.code, "TEXT_GENERATION_RESPONSE_INVALID")
                self.assertIsNone(repo.persisted)
                self.assertEqual(len(transport.calls), 1)
                self.assertNotIn("CANARY", " ".join(logs.output))

    def test_s18_media_non_qa_legacy_adapter_without_selection_is_unchanged(self):
        from test_question_egress_binding import build
        for target in ("", self.context.workspace_id):
            with self.subTest(qa_target_enabled=bool(target)):
                events = []
                service, repository = build(events)
                service._qa_structured_ollama_workspace_id = target
                result = service.ask(
                    QuestionContext("tenant", "workspace", "actor", "trace", "policy"),
                    source_id="source", source_version_id="version", question="질문", run_id="run-legacy-s18",
                )
                self.assertEqual(events, ["decision_commit", "transport", "result_commit"])
                self.assertEqual(result["result"].answer, "근거 문장")
                self.assertIsNotNone(repository.persisted)

    context = QuestionContext(
        "tenant-PvH_pofxPyDtfk3wWBrn3ru3", "workspace-2edb96cb84a252046e67307d",
        "usr-fwYlLlWlcP_GvvpPoZIkadvr", "trace-s18-media", "policy-v1", "notebook-s18-media",
    )
    selection = ResolvedModel(
        connection_id="provider-90efb3fd4ea99fd3ff24edc767ca2800", provider_code="CUSTOM",
        model_id="uss/solar-pro4", capability="text_generation",
        base_url="https://media-bridge-gateway.sinsan.kr/v1", credential_version=1,
        default_version=7, catalog_version=1, provider_kind="external_api", routing_owner="provider",
        daon_fallback_allowed=False, _credential=bytearray(b"CREDENTIAL_CANARY_S18"),
        adapter_type="openai_compatible",
    )

    def setup_flow(self, *, context=None, selection=None, response=None, target=None):
        from types import SimpleNamespace
        from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
        selected = replace(selection or self.selection, _credential=bytearray(b"CREDENTIAL_CANARY_S18"))

        class Resolver:
            @contextmanager
            def resolve(self, _context, _capability):
                yield selected

        class Transport:
            calls = []
            def post_json(self, **kwargs):
                self.calls.append(kwargs)
                return response if response is not None else {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"result": {
                    "answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False,
                }})}}], "usage": {"prompt_tokens": 41, "completion_tokens": 29, "total_tokens": 70}}

        class Egress(FakeEgress):
            def prepare_payload(self, current, wire):
                policy = SimpleNamespace(get_effective=lambda _context: SimpleNamespace(masking_required=True, redaction_required=True))
                return PostgresQuestionEgressAuthorizer.prepare_payload(SimpleNamespace(_policy_service=policy), current, wire)
            def authorize(self, current, **kwargs):
                self.wire = kwargs["provider_payload"]
                return super().authorize(current, **kwargs)

        evidence = (IndexedEvidenceChunk("chunk-page-2", "source-cp3", "source-version-cp3", 2,
                    "ORANGE-COMPASS-42", "span-page-2", 1.0),)
        transport, repository, egress = Transport(), FakeRepository(), Egress()
        service = QuestionAnsweringService(Resolver(), repository, FakeIndex(evidence), FakeCredential(), transport, egress,
            qa_structured_ollama_workspace_id=self.context.workspace_id if target is None else target)
        return service, context or self.context, transport, repository, egress, selected, evidence

    def test_s18_media_cap_is_identical_in_authorization_masked_egress_and_wire(self):
        import hashlib
        service, context, transport, repository, egress, _, _ = self.setup_flow()
        args = dict(source_id="source-cp3", source_version_id="source-version-cp3", question="질문의 원문 CANARY_QUESTION")
        authorization = service.prepare_authorization(context, **args)
        self.assertEqual(json.loads(authorization.provider_payload).get("max_tokens"), 512)
        self.assertEqual(transport.calls, [], "인증 준비는 모델을 호출하지 않는다")
        with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as captured:
            result = service.ask(context, run_id="run-s18-media", **args)
        self.assertEqual(result.answer, "ORANGE-COMPASS-42")
        self.assertEqual(len(transport.calls), 1)
        wire = canonical_json_bytes(transport.calls[0]["payload"])
        self.assertEqual(egress.wire, authorization.provider_payload)
        self.assertEqual(wire, authorization.provider_payload)
        self.assertEqual(hashlib.sha256(wire).digest(), hashlib.sha256(authorization.provider_payload).digest())
        self.assertEqual(transport.calls[0]["payload"]["max_tokens"], 512)
        self.assertEqual(transport.calls[0]["timeout_seconds"], 60.0)
        self.assertIsNotNone(repository.persisted)
        logs = " ".join(captured.output)
        self.assertIn("cap=512", logs)
        for canary in ("CANARY_QUESTION", "ORANGE-COMPASS-42", "CREDENTIAL_CANARY_S18"):
            self.assertNotIn(canary, logs)

    def test_s18_media_existing_larger_caps_are_replaced_preserving_prefix_user_and_schema(self):
        service, context, _, _, _, selection, evidence = self.setup_flow()
        prepared = service._adapter_registry.prepare(selection, evidence, "질문", context.trace_id, service._transport)
        original = canonical_json_bytes(prepared.provider_payload)
        changed = replace(prepared, provider_payload={**prepared.provider_payload, "max_tokens": 9000, "max_completion_tokens": 8000})
        result = service._prepare_qa_structured_payload(context, changed)
        self.assertEqual(result.provider_payload.get("max_tokens"), 512)
        self.assertNotIn("max_completion_tokens", result.provider_payload)
        before = json.loads(original)
        after = {k: v for k, v in result.provider_payload.items() if k not in {"max_tokens", "messages", "response_format"}}
        self.assertEqual(after, {k: v for k, v in before.items() if k not in {"messages", "response_format"}})
        self.assertEqual(result.provider_payload["response_format"]["type"], before["response_format"]["type"])
        self.assertEqual(result.provider_payload["response_format"]["json_schema"]["name"], before["response_format"]["json_schema"]["name"])
        self.assertIs(result.provider_payload["response_format"]["json_schema"]["strict"], True)
        self.assertTrue(result.provider_payload["messages"][0]["content"].startswith(before["messages"][0]["content"]))
        self.assertEqual(result.provider_payload["messages"][1:], before["messages"][1:])
        self.assertEqual(changed.provider_payload["max_tokens"], 9000, "원래 준비 객체는 변경하지 않는다")

    def test_s18_media_json_instruction_matches_existing_schema_and_preserves_prefix_and_user(self):
        for grounded in (True, False):
            with self.subTest(grounded=grounded):
                service, context, _, _, _, selection, evidence = self.setup_flow()
                prepared = (service._adapter_registry.prepare(selection, evidence, "원래 질문", context.trace_id, service._transport)
                    if grounded else service._adapter_registry.prepare_general(selection, "원래 질문", context.trace_id, service._transport))
                original = canonical_json_bytes(prepared.provider_payload)
                result = service._prepare_qa_structured_payload(context, prepared)
                original_system = prepared.provider_payload["messages"][0]["content"]
                system = result.provider_payload["messages"][0]["content"]
                self.assertTrue(system.startswith(original_system + "\n\n"))
                appended = system[len(original_system):]
                self.assertIn("ONLY JSON object", appended)
                self.assertIn("no markdown, prose, or code fences", appended)
                schema_text = appended.split("JSON Schema:\n", 1)[1].split("\n", 1)[0]
                schema = json.loads(schema_text)
                self.assertEqual(schema, result.provider_payload["response_format"]["json_schema"]["schema"])
                self.assertEqual(set(schema["properties"]), {"result"} if grounded else {"answer"})
                if grounded:
                    self.assertIn("supplied chunk_id", appended)
                    self.assertIn("insufficient=true", appended)
                    self.assertIn("cited_chunk_ids=[]", appended)
                    self.assertIn("do not repeat question or Source numbers or identifiers", appended)
                    self.assertIn("root result envelope takes precedence", appended)
                    sufficient = schema["properties"]["result"]["anyOf"][1]
                    self.assertEqual(sufficient["properties"]["answer"], {"type": "string"})
                    self.assertEqual(sufficient["properties"]["insufficient"], {"type": "boolean", "enum": [False]})
                else:
                    self.assertNotIn("Only when you choose insufficient=true", appended)
                    self.assertEqual(schema["properties"]["answer"], {"type": "string"})
                    self.assertEqual(result.provider_payload["response_format"], prepared.provider_payload["response_format"])
                self.assertEqual(result.provider_payload["messages"][1:], prepared.provider_payload["messages"][1:])
                self.assertEqual(canonical_json_bytes(prepared.provider_payload), original)

    def test_s18_media_diagnostic_invalid_shapes_and_unknown_reasons_never_log_values(self):
        responses = [
            {"choices": []},
            {"choices": [{"finish_reason": "FINISH_SECRET_CANARY", "message": {"content": "RESPONSE_SECRET_CANARY"}}], "metadata": "METADATA_SECRET_CANARY"},
            {"choices": [{"message": {"content": []}}]},
            {"choices": [{"message": {"content": json.dumps({"cited_chunk_ids": [], "insufficient": False, "metadata": "JSON_VALUE_CANARY"})}}]},
            {"choices": [{"message": {"content": '```json\n{"answer":"RESPONSE_SECRET_CANARY","cited_chunk_ids":[],"insufficient":true}\n```'}}]},
            {"choices": [{"message": {"content": 'prose RESPONSE_SECRET_CANARY {"answer":"답변","cited_chunk_ids":[],"insufficient":true}'}}]},
        ]
        for response in responses:
            with self.subTest(response_index=responses.index(response)):
                service, context, transport, repository, _, _, _ = self.setup_flow(response=response)
                with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as captured:
                    with self.assertRaises(QuestionAnsweringError) as error:
                        service.ask(context, source_id="source-cp3", source_version_id="source-version-cp3", question="QUESTION_CANARY", run_id="run-s18-invalid")
                self.assertEqual(error.exception.code, "TEXT_GENERATION_RESPONSE_INVALID")
                self.assertIsNone(repository.persisted)
                self.assertEqual(len(transport.calls), 1)
                self.assertNotIn("CANARY", " ".join(captured.output))
                if response is responses[1]: self.assertIn("finish_reason=other", " ".join(captured.output))

    def test_s18_media_other_identity_selection_requests_are_unchanged(self):
        import logging
        from unittest.mock import patch
        service, context, _, _, _, selection, evidence = self.setup_flow(response={"choices": [{"message": {"content": json.dumps({
            "answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False,
        })}}]})
        prepared = service._adapter_registry.prepare(selection, evidence, "질문", context.trace_id, service._transport)
        negatives = [
            (replace(context, tenant_id="tenant-other"), selection),
            (replace(context, actor_id="actor-other"), selection),
            (replace(context, workspace_id="workspace-other"), selection),
            (context, replace(selection, connection_id="connection-other")),
            (context, replace(selection, model_id="uss/solar-pro4-other")),
            (context, replace(selection, provider_code="UPSTAGE")),
            (context, replace(selection, provider_kind="server_internal")),
            (context, replace(selection, adapter_type="anthropic_compatible")),
            (context, replace(selection, base_url=selection.base_url + "/")),
            (context, replace(selection, base_url=selection.base_url + "?private=URL_CANARY")),
        ]
        for other_context, other_selection in negatives:
            with self.subTest(context=other_context, connection=other_selection.connection_id), patch.object(logging.getLogger("daon_user_api.question_answering_service"), "warning") as log:
                other = replace(prepared, selection=other_selection)
                self.assertIs(service._prepare_qa_structured_payload(other_context, other), other)
                other.adapter.generate(other.request, selection, provider_payload=other.provider_payload)
                log.assert_not_called()
        general = service._adapter_registry.prepare_general(selection, "Hello", context.trace_id, service._transport)
        self.assertIs(service._prepare_qa_structured_payload(replace(context, actor_id="actor-other"), general), general)
        off, *_ = self.setup_flow(target="")
        self.assertIs(off._prepare_qa_structured_payload(context, prepared), prepared)

    def test_s18_media_general_and_zero_retrieval_fallback_have_same_authorized_wire_cap(self):
        for source_selected in (False, True):
            with self.subTest(source_selected=source_selected):
                service, context, transport, _, egress, _, _ = self.setup_flow(response={"choices": [{
                    "finish_reason": "stop", "message": {"content": json.dumps({"answer": "일반 구조 응답"})},
                }]})
                service._document_index.evidence = ()
                args = dict(source_id="source-cp3" if source_selected else None,
                            source_version_id="source-version-cp3" if source_selected else None, question="자료 밖 질문")
                authorization = service.prepare_authorization(context, **args)
                self.assertEqual(json.loads(authorization.provider_payload).get("max_tokens"), 512)
                self.assertEqual(transport.calls, [])
                with self.assertLogs("daon_user_api.question_answering_service", level="INFO"):
                    result = service.ask(context, run_id="run-s18-fallback", **args)
                self.assertEqual(result.answer, "일반 구조 응답")
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(authorization.provider_payload, egress.wire)
                self.assertEqual(authorization.provider_payload, canonical_json_bytes(transport.calls[0]["payload"]))
                self.assertEqual(transport.calls[0]["payload"]["max_tokens"], 512)

    def test_s18_media_valid_json_with_invented_citation_still_fails_grounding(self):
        service, context, _, repository, _, _, _ = self.setup_flow(response={"choices": [{"message": {"content": json.dumps({"result": {
            "answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["invented-chunk"], "insufficient": False,
        }})}}]})
        with self.assertRaises(QuestionAnsweringError) as error:
            service.ask(context, source_id="source-cp3", source_version_id="source-version-cp3", question="질문", run_id="run-s18-grounding")
        self.assertEqual(error.exception.code, "TEXT_GENERATION_GROUNDING_INVALID")
        self.assertIsNone(repository.persisted)

    def test_s18_media_valid_insufficient_is_not_rewritten_and_diagnostic_counts_are_typed(self):
        service, context, transport, _, _, _, _ = self.setup_flow(response={"choices": [{
            "finish_reason": "length", "message": {"content": json.dumps({"result": {
                "answer": "선택한 원문 근거에 질문에 답할 정보가 없습니다.", "cited_chunk_ids": [], "insufficient": True,
            }})}}], "usage": {"prompt_tokens": True, "completion_tokens": "USAGE_SECRET_CANARY", "total_tokens": 900000000000}})
        with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as captured:
            result = service.ask(context, source_id="source-cp3", source_version_id="source-version-cp3", question="질문", run_id="run-s18-insufficient")
        self.assertTrue(result.insufficient)
        self.assertEqual(result.answer, "선택한 원문 근거에 질문에 답할 정보가 없습니다.")
        self.assertEqual(result.citations, ())
        logs = " ".join(captured.output)
        self.assertIn("finish_reason=length", logs)
        self.assertIn("json_ok=True", logs)
        self.assertIn("prompt_tokens=-1 completion_tokens=-1 total_tokens=-1", logs)
        self.assertNotIn("USAGE_SECRET_CANARY", logs)
        self.assertEqual(len(transport.calls), 1)

    def test_s18_media_insufficient_rule_is_conditional_and_frozen_before_authorization(self):
        service, context, transport, _, egress, selection, evidence = self.setup_flow(response={
            "choices": [{"message": {"content": json.dumps({"result": {
                "answer": "선택한 원문 근거에 질문에 답할 정보가 없습니다.",
                "cited_chunk_ids": [], "insufficient": True,
            }})}}],
        })
        args = dict(source_id="source-cp3", source_version_id="source-version-cp3", question="자료 밖 QUESTION_CANARY 2035")
        prepared = service._adapter_registry.prepare(selection, evidence, args["question"], context.trace_id, transport)
        original = prepared.provider_payload
        qa_prepared = service._prepare_qa_structured_payload(context, prepared)
        before_egress = qa_prepared.provider_payload
        self.assertEqual(before_egress["messages"][1:], original["messages"][1:])
        for canary in (args["question"], "ORANGE-COMPASS-42", "CREDENTIAL_CANARY_S18", "chunk-page-2"):
            self.assertNotIn(canary, repr(qa_prepared.adapter))
        authorization = service.prepare_authorization(context, **args)
        payload = json.loads(authorization.provider_payload)
        system = payload["messages"][0]["content"]
        self.assertTrue(system.startswith(original["messages"][0]["content"] + "\n\n"))
        self.assertIn("Only when you choose insufficient=true", system)
        self.assertIn("takes precedence over the earlier Source scope explanation", system)
        self.assertIn("선택한 원문 근거에 질문에 답할 정보가 없습니다.", system)
        self.assertIn("If evidence supports an answer, answer normally", system)
        self.assertEqual(payload["response_format"], before_egress["response_format"])
        self.assertEqual(set(payload["response_format"]["json_schema"]["schema"]["properties"]), {"result"})
        self.assertEqual(payload["messages"][1]["role"], original["messages"][1]["role"])
        self.assertEqual(json.loads(payload["messages"][1]["content"]), json.loads(original["messages"][1]["content"]))
        self.assertEqual(transport.calls, [])
        result = service.ask(context, run_id="run-s18-insufficient-rule", **args)
        self.assertTrue(result.insufficient)
        self.assertEqual(result.citations, ())
        self.assertEqual(authorization.provider_payload, egress.wire)
        self.assertEqual(authorization.provider_payload, canonical_json_bytes(transport.calls[0]["payload"]))
        self.assertEqual(transport.calls[0]["timeout_seconds"], 60.0)
        self.assertEqual(transport.calls[0]["payload"]["max_tokens"], 512)

    def test_s18_media_normal_and_insufficient_diagnostics_preserve_model_answer_and_replay(self):
        for answer, cited, insufficient in (
            ("ORANGE-COMPASS-42", ["chunk-page-2"], False),
            ("선택한 원문 근거에 질문에 답할 정보가 없습니다.", [], True),
        ):
            with self.subTest(insufficient=insufficient):
                service, context, transport, repo, _, _, _ = self.setup_flow(response={
                    "choices": [{"message": {"content": json.dumps({"result": {
                        "answer": answer, "cited_chunk_ids": cited, "insufficient": insufficient,
                    }})}}],
                })
                with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as logs:
                    result = service.ask(context, source_id="source-cp3", source_version_id="source-version-cp3", question="QUESTION_CANARY", run_id="run-s18-diagnostic-valid")
                self.assertEqual(result.answer, answer)
                self.assertEqual(result.insufficient, insufficient)
                self.assertEqual(len(result.citations), len(cited))
                self.assertIsNotNone(repo.persisted)
                self.assertIs(service.replay(context, run_id=result.run_id, request_fingerprint=repo.persisted["request_fingerprint"]), result)
                self.assertEqual(len(transport.calls), 1)
                output = " ".join(logs.output)
                self.assertIn("failure_reason=none", output)
                self.assertIn(f"insufficient_value={insufficient}", output)
                for canary in (answer, "QUESTION_CANARY", "CREDENTIAL_CANARY_S18", "chunk-page-2", "source-version-cp3"):
                    self.assertNotIn(canary, output)

    def test_s18_media_grounding_failure_enum_matches_unchanged_strict_rejection(self):
        cases = (
            ({"answer": "BODY_CANARY", "cited_chunk_ids": ["UNKNOWN_ID_CANARY"], "insufficient": False}, "unknown_citation", "TEXT_GENERATION_GROUNDING_INVALID"),
            ({"answer": "BODY_CANARY", "cited_chunk_ids": ["chunk-page-2", "chunk-page-2"], "insufficient": False}, "duplicate_citation", "TEXT_GENERATION_GROUNDING_INVALID"),
            ({"answer": "BODY_CANARY 2035", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False}, "unsupported_anchor", "TEXT_GENERATION_GROUNDING_INVALID"),
            ({"answer": "BODY_CANARY", "cited_chunk_ids": ["chunk-page-2"], "insufficient": True}, "insufficient_with_citations", "TEXT_GENERATION_GROUNDING_INVALID"),
            ({"answer": "BODY_CANARY", "cited_chunk_ids": [], "insufficient": False}, "sufficient_without_citations", "TEXT_GENERATION_GROUNDING_INVALID"),
            ({"answer": "", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False}, "answer_empty", "TEXT_GENERATION_GROUNDING_INVALID"),
            ({"answer": "x" * 8001, "cited_chunk_ids": ["chunk-page-2"], "insufficient": False}, "answer_too_long", "TEXT_GENERATION_GROUNDING_INVALID"),
            # New exact-QA enum rejects this value before the unchanged base
            # validator; the sufficient unsupported-anchor case above still
            # exercises the original grounding rejection.
            ({"answer": "BODY_CANARY 2035", "cited_chunk_ids": [], "insufficient": True}, "insufficient_answer_invalid", "TEXT_GENERATION_RESPONSE_INVALID"),
            ({"answer": "BODY_CANARY", "insufficient": True}, "response_invalid", "TEXT_GENERATION_RESPONSE_INVALID"),
        )
        for parsed, reason, public_error in cases:
            with self.subTest(reason=reason, insufficient=parsed["insufficient"]):
                service, context, transport, repo, _, _, _ = self.setup_flow(response={
                    "choices": [{"finish_reason": "REASON_CANARY", "message": {"content": json.dumps({"result": parsed})}}],
                })
                with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as logs:
                    with self.assertRaises(QuestionAnsweringError) as error:
                        service.ask(context, source_id="source-cp3", source_version_id="source-version-cp3", question="QUESTION_CANARY", run_id="run-s18-diagnostic-reject")
                self.assertEqual(error.exception.code, public_error)
                self.assertIsNone(repo.persisted)
                self.assertEqual(len(transport.calls), 1)
                output = " ".join(logs.output)
                self.assertIn(f"failure_reason={reason}", output)
                self.assertIn(f"insufficient_value={parsed['insufficient']}", output)
                self.assertIn("finish_reason=other", output)
                for canary in ("CANARY", "chunk-page-2", "source-version-cp3", "2035", "x" * 100):
                    self.assertNotIn(canary, output)

    def test_s18_media_transport_failure_logs_fixed_structure_only_and_keeps_public_error(self):
        service, context, _, repository, _, _, _ = self.setup_flow()
        def fail(**_kwargs):
            raise ValueError("TRANSPORT_SECRET_CANARY")
        service._transport.post_json = fail
        with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as captured:
            with self.assertRaises(QuestionAnsweringError) as error:
                service.ask(context, source_id="source-cp3", source_version_id="source-version-cp3", question="질문", run_id="run-s18-transport")
        self.assertEqual(error.exception.code, "TEXT_GENERATION_FAILED")
        self.assertIsNone(repository.persisted)
        self.assertIn("response_type=unavailable", " ".join(captured.output))
        self.assertNotIn("CANARY", " ".join(captured.output))

    def test_s18_media_production_transport_decoded_request_matches_authorization_with_unicode(self):
        import hashlib
        import urllib.request
        from unittest.mock import patch
        from daon_user_api.document_understanding_adapter import UrlLibDocumentUnderstandingTransport
        service, context, _, _, egress, _, evidence = self.setup_flow()
        service._transport = UrlLibDocumentUnderstandingTransport()
        service._document_index.evidence = (replace(evidence[0], text="ORANGE-COMPASS-42 · 한글 [마스킹] ███"),)
        args = dict(source_id="source-cp3", source_version_id="source-version-cp3", question="선택한 시험 문서를 설명해 주세요.")
        authorization = service.prepare_authorization(context, **args)
        captured = []
        response_bytes = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"result": {
            "answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False,
        }})}}]}).encode()

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, limit): return response_bytes[:limit]

        def open_boundary(_opener, request, *, timeout):
            captured.append(request.data)
            self.assertEqual(timeout, 60.0)
            return Response()

        with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as diagnostics:
            with patch.object(urllib.request.OpenerDirector, "open", autospec=True, side_effect=open_boundary):
                service.ask(context, run_id="run-s18-production-transport", **args)
        self.assertEqual(len(captured), 1)
        actual = json.loads(captured[0])
        self.assertEqual(actual, json.loads(authorization.provider_payload))
        self.assertEqual(actual["max_tokens"], 512)
        self.assertNotIn("max_completion_tokens", actual)
        self.assertEqual(hashlib.sha256(canonical_json_bytes(actual)).digest(), hashlib.sha256(authorization.provider_payload).digest())
        self.assertEqual(egress.wire, authorization.provider_payload)
        self.assertIn("한글 [마스킹] ███", json.loads(actual["messages"][1]["content"])["evidence"][0]["text"])
        self.assertNotEqual(captured[0], authorization.provider_payload, "raw HTTP encoding과 canonical JSON bytes는 구별한다")
        self.assertIn(f"request_bytes={len(captured[0])}", " ".join(diagnostics.output))

    def test_s18_media_unicode_escaped_body_over_64k_is_rejected_before_transport(self):
        import urllib.request
        from unittest.mock import patch
        from daon_user_api.document_understanding_adapter import UrlLibDocumentUnderstandingTransport
        service, context, _, repository, _, _, evidence = self.setup_flow()
        service._transport = UrlLibDocumentUnderstandingTransport()
        service._document_index.evidence = (replace(evidence[0], text="ORANGE-COMPASS-42 " + "한" * 12000),)
        args = dict(source_id="source-cp3", source_version_id="source-version-cp3", question="선택한 시험 문서의 코드")
        authorization = service.prepare_authorization(context, **args)
        request_bytes = len(json.dumps(json.loads(authorization.provider_payload), separators=(",", ":")).encode("utf-8"))
        self.assertLessEqual(len(authorization.provider_payload), 65536)
        self.assertGreater(request_bytes, 65536)
        response_bytes = json.dumps({"choices": [{"message": {"content": json.dumps({
            "answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False,
        })}}]}).encode()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, limit): return response_bytes[:limit]
        with patch.object(urllib.request.OpenerDirector, "open", return_value=Response()) as open_boundary:
            with self.assertLogs("daon_user_api.question_answering_service", level="INFO") as diagnostics:
                with self.assertRaises(QuestionAnsweringError) as error:
                    service.ask(context, run_id="run-s18-oversize", **args)
        self.assertEqual(error.exception.code, "TEXT_GENERATION_FAILED")
        open_boundary.assert_not_called()
        self.assertIsNone(repository.persisted)
        self.assertIn(f"request_bytes={request_bytes}", " ".join(diagnostics.output))
        self.assertIn("response_type=not_sent", " ".join(diagnostics.output))
        self.assertNotIn("ORANGE-COMPASS-42", " ".join(diagnostics.output))

    def test_s18_media_other_adapter_fallback_keeps_original_egress_behavior(self):
        from daon_user_api.question_answering import OpenAICompatibleTextGenerationAdapter
        class OtherAdapter(OpenAICompatibleTextGenerationAdapter):
            pass
        class OtherRegistry(QuestionAdapterRegistry):
            def prepare_general(self, selection, question, trace_id, transport):
                prepared = super().prepare_general(selection, question, trace_id, transport)
                return replace(prepared, adapter=OtherAdapter(transport=transport, api_key=selection.credential_text()))
        service, context, transport, _, egress, _, _ = self.setup_flow(response={"choices": [{"message": {"content": '{"answer":"일반 응답"}'}}]})
        service._document_index.evidence = ()
        service._adapter_registry = OtherRegistry()
        with self.assertNoLogs("daon_user_api.question_answering_service", level="INFO"):
            service.ask(context, source_id="source-cp3", source_version_id="source-version-cp3", question="자료 밖 질문", run_id="run-other-adapter")
        self.assertTrue(egress.wire)
        self.assertEqual(egress.wire, canonical_json_bytes(transport.calls[0]["payload"]))
        self.assertEqual(json.loads(egress.wire)["messages"][-1], {"role": "user", "content": "자료 밖 질문"})
        self.assertNotIn("max_tokens", transport.calls[0]["payload"])


class FakeProviderSettings:
    def __init__(self) -> None:
        self.calls = 0

    @contextmanager
    def resolve(self, context, capability):  # type: ignore[no-untyped-def]
        self.calls += 1
        model = ResolvedModel(
            connection_id="profile-upstage", provider_code="UPSTAGE",
            model_id="solar-pro4", capability=capability,
            base_url="https://api.upstage.ai/v1", credential_version=1,
            default_version=5, catalog_version=1, provider_kind="external_api",
            routing_owner="provider", daon_fallback_allowed=True,
            _credential=bytearray(b"server-secret"),
        )
        try:
            yield model
        finally:
            model.release()


class PersonalCredentialFallbackResolver:
    supports_user_credential_fallback = True

    def __init__(self) -> None:
        self.sources = []

    @contextmanager
    def resolve(self, context, capability):  # type: ignore[no-untyped-def]
        self.sources.append(context.credential_source)
        model = ResolvedModel(
            connection_id="openrouter-primary", provider_code="OPENROUTER",
            model_id="openai/gpt-4.1", capability=capability,
            base_url="https://openrouter.ai/api/v1", credential_version=1,
            default_version=5, catalog_version=1, provider_kind="external_api",
            routing_owner="provider", daon_fallback_allowed=True,
            _credential=bytearray(
                b"system-secret" if context.credential_source == "system" else b"user-secret"
            ),
        )
        try:
            yield model
        finally:
            model.release()


class SystemCredentialFailureThenPersonalSuccessTransport:
    def __init__(self) -> None:
        self.calls = 0

    def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            raise ValueError("provider unavailable")
        return {"choices": [{"message": {"content": json.dumps({"answer": "personal answer"})}}]}


class FakeRepository:
    def __init__(self) -> None:
        self.persisted = None
        self.completed = None

    def load_completed(self, context, run_id):  # type: ignore[no-untyped-def]
        return self.completed


    def load_completed_for_replay(self, context, run_id, request_fingerprint):  # type: ignore[no-untyped-def]
        return self.completed

    def load_ready_source(self, context, source_id, source_version_id):  # type: ignore[no-untyped-def]
        return ReadyQuestionSource(source_id, source_version_id, "report.pdf")

    def persist_completed(self, context, **kwargs):  # type: ignore[no-untyped-def]
        self.persisted = kwargs
        result = kwargs["result"]
        citations = tuple(
            StoredCitation("citation-cp3", kwargs["source_id"], kwargs["source_version_id"], "span-page-2", 2)
            for _chunk_id in result.cited_chunk_ids
        )
        self.completed = StoredQuestionAnswer(
            kwargs["run_id"], "result-cp3", result.answer, result.insufficient, citations,
        )
        return self.completed


class OpenLawExecutionRecheckTests(unittest.TestCase):
    def test_knowledge_source_revoked_after_search_is_rechecked_before_generation(self):
        """지식 자료도 ready/index 이후 철회되면 생성 직전 현재 ACL 재검사에서 전송 없이 거부한다."""
        version='version-knowledge'
        acl={version:True}
        ready=[]
        checked=[]
        searched=[]
        generated=[]
        class Repository(FakeRepository):
            def load_ready_source(self,context,source_id,source_version_id):
                ready.append((source_version_id,acl[source_version_id]))
                return super().load_ready_source(context,source_id,source_version_id)
            def revalidate_open_law_sources(self,context,versions):
                checked.append(tuple(versions))
                if any(acl[item] is False for item in versions):
                    raise QuestionRepositoryError('QUESTION_SOURCE_UNAVAILABLE',status=404)
        evidence=(IndexedEvidenceChunk('chunk-page-2','source-knowledge',version,2,'ORANGE-COMPASS-42','span-knowledge'),)
        class RevokingIndex(FakeIndex):
            def search(self,context,**kwargs):
                chunks=super().search(context,**kwargs)
                searched.append(kwargs['source_version_id'])
                acl[version]=False
                return chunks
        class Registry(QuestionAdapterRegistry):
            def generate_prepared(self,prepared):
                generated.append('grounded')
                return super().generate_prepared(prepared)
            def generate_general(self,prepared):
                generated.append('general')
                return super().generate_general(prepared)
        repository=Repository()
        transport=FakeTransport()
        service=QuestionAnsweringService(FakeProviderSettings(),repository,RevokingIndex(evidence),
            FakeCredential(),transport,FakeEgress(),adapter_registry=Registry())
        context=QuestionContext('tenant-cp3','workspace-cp3','actor-cp3','trace-cp3','policy-v1','notebook-cp3')
        denial=None
        try:
            service.ask(context,source_id='source-knowledge',source_version_id=version,
                question='승인 지식의 근거를 설명해줘',run_id='run-knowledge-revoked',context_mode='daon_priority',
                context_sources=(QuestionInputSource('daon_knowledge','package-daon3','source-knowledge',version),))
        except QuestionAnsweringError as error:
            denial=(error.code,error.status)
        # 객체/선택/credential을 실패 출력에 담지 않고 실제 포트 도달 여부와 안전 오류만 비교한다.
        self.assertEqual({'denial':denial,'ready_before_deny':len(ready)>=2 and all(allowed for _,allowed in ready),
            'searched':searched,'checked':checked,'generated':generated,'transport':transport.calls,
            'stored':repository.persisted is not None},
            {'denial':('QUESTION_SOURCE_UNAVAILABLE',404),'ready_before_deny':True,'searched':['version-knowledge'],
             'checked':[('version-knowledge',)],'generated':[],'transport':0,'stored':False})

    def test_source_acl_index_error_maps_only_source_not_found_404(self):
        """검색의 자료 거부만 질문 안전 오류로 바꾸며 DB/손상/timeout은 원래 오류로 유지한다."""
        from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
        context=QuestionContext('tenant-cp3','workspace-cp3','actor-cp3','trace','policy','notebook')
        sources=(QuestionInputSource('raw_source','source-cp3','source-cp3','source-version-cp3'),)
        cases=(
            ('source-denied',DocumentUnderstandingError('SOURCE_NOT_FOUND',status=404),True),
            ('other-status',DocumentUnderstandingError('SOURCE_NOT_FOUND',status=409),False),
            ('database',DocumentUnderstandingError('DOCUMENT_INDEX_DATABASE_UNAVAILABLE',status=503,retryable=True),False),
            ('snapshot',DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID',status=409),False),
            ('timeout',TimeoutError('S42 합성 검색 timeout'),False),
        )
        for label,failure,mapped in cases:
            with self.subTest(boundary=label):
                class FailingIndex(FakeIndex):
                    def search(self,context,**kwargs):
                        raise failure
                repository=FakeRepository()
                transport=FakeTransport()
                service=QuestionAnsweringService(FakeProviderSettings(),repository,FailingIndex(()),
                    FakeCredential(),transport,FakeEgress())
                if mapped:
                    with self.assertRaises(QuestionAnsweringError) as caught:
                        service._search_context(context,sources,'S42 합성 질문')
                    self.assertEqual((caught.exception.code,caught.exception.status),('QUESTION_SOURCE_UNAVAILABLE',404))
                else:
                    with self.assertRaises(type(failure)) as caught:
                        service._search_context(context,sources,'S42 합성 질문')
                    self.assertIs(caught.exception,failure)
                self.assertEqual(transport.calls,0)
                self.assertIsNone(repository.persisted)

    def test_disabled_law_selection_never_becomes_general_fallback(self):
        from test_question_answering_postgres import FakeCloudStore, FakeObjectStorage, Cursor
        from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
        from unittest.mock import patch
        cloud = FakeCloudStore()
        execute = cloud.connection.execute
        def law(sql, params=()):
            if sql.startswith('SELECT canonical_json FROM source_versions'):
                return Cursor(({'origin_kind':'mcp_open_law','connector_id':'mcp-open-law-go-kr','snapshot_id':'law-snapshot'},))
            return execute(sql,params)
        cloud.connection.execute = law
        actual = PostgresQuestionAnsweringRepository(cloud,FakeObjectStorage())
        repo = FakeRepository()
        repo.load_ready_source = actual.load_ready_source
        transport = GeneralAnswerTransport()
        service = QuestionAnsweringService(FakeProviderSettings(),repo,FakeIndex(()),
            FakeCredential(),transport,FakeEgress())
        with patch.dict('os.environ',{'DAON_OPEN_LAW_ENABLED':'false'}):
            with self.assertRaises((QuestionAnsweringError,QuestionRepositoryError)):
                service.ask(QuestionContext('tenant-cp3','workspace-cp3','actor-cp3','trace','policy','notebook'),
                    source_id='source-cp3',source_version_id='source-version-cp3',question='선택 문서 내용은?',run_id='run-disabled-law')
        self.assertEqual(transport.calls,0)
        self.assertIsNone(repo.persisted)

    def test_no_evidence_fallback_rechecks_selected_version_before_provider(self):
        repo = FakeRepository()
        transport = GeneralAnswerTransport()
        class RevokingIndex(FakeIndex):
            def search(self, context, **kwargs):
                repo.revoked = True
                return ()
        def check(context, versions):
            if versions and getattr(repo, 'revoked', False):
                raise QuestionRepositoryError('QUESTION_SOURCE_UNAVAILABLE', status=404)
        repo.revalidate_open_law_sources = check
        service = QuestionAnsweringService(FakeProviderSettings(), repo,
            RevokingIndex(()), FakeCredential(), transport, FakeEgress())
        with self.assertRaisesRegex(QuestionAnsweringError, 'QUESTION_SOURCE_UNAVAILABLE'):
            service.ask(QuestionContext('tenant','workspace','actor','trace','policy','notebook'),
                source_id='source-cp3', source_version_id='source-version-cp3',
                question='선택 문서에 없는 내용은?', run_id='run-law-empty')
        self.assertEqual(transport.calls, 0)
        self.assertIsNone(repo.persisted)

    def test_no_evidence_fallback_retains_selected_version_for_commit_guard(self):
        repo = FakeRepository()
        service = QuestionAnsweringService(FakeProviderSettings(), repo,
            FakeIndex(()), FakeCredential(), GeneralAnswerTransport(), FakeEgress())
        service.ask(QuestionContext('tenant','workspace','actor','trace','policy','notebook'),
            source_id='source-cp3', source_version_id='source-version-cp3',
            question='선택 문서에 없는 내용은?', run_id='run-law-empty')
        self.assertEqual(repo.persisted.get('access_source_version_ids'), ('source-version-cp3',))
        self.assertEqual(repo.persisted['context_sources'], ())

    def test_question_source_revoked_by_provider_response_never_persists(self):
        repo=FakeRepository();original=repo.load_ready_source;revoked=[]
        def load(*args):
            if revoked:raise QuestionRepositoryError('QUESTION_SOURCE_UNAVAILABLE',status=404)
            return original(*args)
        repo.load_ready_source=load
        repo.revalidate_open_law_sources=lambda ctx,versions:[load(ctx,'source-cp3',version) for version in versions]
        class Transport(FakeTransport):
            def post_json(self,**kwargs):
                response=super().post_json(**kwargs);revoked.append(True);return response
        service=QuestionAnsweringService(FakeProviderSettings(),repo,
            FakeIndex((IndexedEvidenceChunk('chunk-page-2','source-cp3','source-version-cp3',2,'ORANGE-COMPASS-42','span-page-2'),)),
            FakeCredential(),Transport(),FakeEgress())
        with self.assertRaises(QuestionAnsweringError):
            service.ask(QuestionContext('tenant','workspace','actor','trace','policy','notebook'),
                source_id='source-cp3',source_version_id='source-version-cp3',question='선택한 원문 내용은?',run_id='run-law')
        self.assertIsNone(repo.persisted)


class FakeIndex:
    def __init__(self, evidence):  # type: ignore[no-untyped-def]
        self.evidence = evidence

    def search(self, context, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(self.evidence, dict):
            return self.evidence.get(kwargs["source_version_id"], ())
        return self.evidence


class FakeCredential:
    def resolve(self, provider_code: str) -> str:
        return "server-secret"


class FakeTransport:
    def __init__(self) -> None:
        self.calls = 0

    def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return {"choices": [{"message": {"content": json.dumps({
            "answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"],
            "insufficient": False,
        })}}]}


class GeneralAnswerTransport(FakeTransport):
    def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return {"choices": [{"message": {"content": json.dumps({
            "answer": "일반 답변입니다.",
        }, ensure_ascii=False)}}]}


class FakeEgress:
    def authorize(self, context, **kwargs):  # type: ignore[no-untyped-def]
        return {"egress_decision_id": "egress-test", "routing_decision_id": "routing-test"}


class StaticWorkspaceModelResolver:
    def __init__(self, *, provider_code: str = "EOUL_GATEWAY", model_id: str = "assistant-default") -> None:
        self.provider_code = provider_code
        self.model_id = model_id
        self.calls = []

    @contextmanager
    def resolve(self, context, capability):  # type: ignore[no-untyped-def]
        self.calls.append((context.workspace_id, capability))
        model = ResolvedModel(
            connection_id="eoul-primary", provider_code=self.provider_code,
            model_id=self.model_id, capability=capability,
            base_url="https://gateway.example.com", credential_version=7,
            default_version=3, catalog_version=11, provider_kind="external_api",
            routing_owner="gateway", daon_fallback_allowed=False,
            _credential=bytearray(b"data-api-client-key"),
        )
        try:
            yield model
        finally:
            model.release()


class CapturingGatewayTransport:
    def __init__(self, *, failure: bool = False) -> None:
        self.failure = failure
        self.calls = []

    def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        if self.failure:
            raise ValueError("gateway unavailable")
        return {"choices": [{"message": {"content": json.dumps({"answer": "gateway answer"})}}]}


class OmniRouteTransport(CapturingGatewayTransport):
    def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return {"output_text": json.dumps({"answer": "omni answer"})}


class QuestionAnsweringServiceTests(unittest.TestCase):
    def test_configured_run_cost_reserves_after_egress_and_persists_measured_price_card_receipt(self):
        from decimal import Decimal
        from test_operations_run_cost import NOW,policy
        from daon_user_api.operations_run_cost import UpstageModelContextEvidence
        from daon_user_api.operations_run_cost_postgres import RunCostSessionFactory,CostReservation,settle_reserved_usage
        from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
        context=QuestionContext('tenant-cp3','workspace-cp3','actor-cp3','trace-cp3','policy-v1')
        p=replace(policy(),tenant_id=context.tenant_id,workspace_id=context.workspace_id,actor_id=context.actor_id)
        calls=[]
        class Resolver(FakeProviderSettings):
            @contextmanager
            def resolve(self,context,capability):
                with super().resolve(context,capability) as selected:
                    yield replace(selected,connection_id='provider-upstage')
        class Costs:
            def reserve(self,ctx,scope,current,**kwargs):
                assert kwargs['input_token_upper_bound']==524288 and kwargs['output_token_limit']==512
                calls.append('reserve')
                assert kwargs['attempt_key']==PostgresQuestionAnsweringRepository._opaque_id('attempt','run-cost-guard','provider-upstage:solar-pro4')
                return CostReservation(scope,current,kwargs['attempt_key'],1,Decimal('0.157900800'),NOW)
            def settle(self,ctx,held,**kwargs):calls.append('settle');return settle_reserved_usage(held,**kwargs)
        class Transport(FakeTransport):
            def post_json(self,**kwargs):
                calls.append('post');self.payload=kwargs['payload']
                return {**super().post_json(**kwargs),'model':'solar-pro4-260806','usage':{
                    'prompt_tokens':1000,'completion_tokens':100,'total_tokens':1100,'prompt_tokens_details':{'cached_tokens':200}}}
        class Egress(FakeEgress):
            def authorize(self,ctx,**kwargs):calls.append('egress');self.wire=kwargs['provider_payload'];return super().authorize(ctx,**kwargs)
        evidence=UpstageModelContextEvidence('solar-pro4','solar-pro4-260806',524288,'https://console.upstage.ai/docs/models/solar-pro-4',NOW)
        factory=RunCostSessionFactory(Costs(),policies=(p,),evidence=evidence,clock=lambda:NOW)
        repo,transport,egress=FakeRepository(),Transport(),Egress()
        chunks=(IndexedEvidenceChunk('chunk-page-2','source-cp3','source-version-cp3',2,'ORANGE-COMPASS-42','span-page-2',1.0),)
        service=QuestionAnsweringService(Resolver(),repo,FakeIndex(chunks),FakeCredential(),transport,egress,run_cost_sessions=factory)
        prepared=service.prepare_authorization(context,source_id='source-cp3',source_version_id='source-version-cp3',question='What code?')
        self.assertEqual(calls,[])
        service.ask(context,source_id='source-cp3',source_version_id='source-version-cp3',question='What code?',run_id='run-cost-guard')
        self.assertEqual(calls,['egress','reserve','post','settle'])
        self.assertEqual(prepared.provider_payload,egress.wire)
        self.assertEqual(json.loads(egress.wire),transport.payload)
        self.assertEqual(transport.payload['max_tokens'],512)
        receipt=repo.persisted['result'].price_card_receipt
        self.assertEqual((receipt.basis,receipt.state,receipt.amount),('price_card','known',Decimal('0.000372000')))

    def test_scoped_run_cost_general_paths_failure_and_egress_denial_do_not_auto_resend(self):
        from decimal import Decimal
        from test_operations_run_cost import NOW,policy
        from daon_user_api.operations_run_cost import UpstageModelContextEvidence
        from daon_user_api.operations_run_cost_postgres import RunCostSessionFactory,CostReservation,settle_reserved_usage
        for mode in ('general','empty_evidence','transport_failure','egress_denied'):
            with self.subTest(mode=mode):
                context=QuestionContext('tenant-cp3','workspace-cp3','actor-cp3','trace-cp3','policy-v1')
                p=replace(policy(),tenant_id=context.tenant_id,workspace_id=context.workspace_id,actor_id=context.actor_id)
                events=[]
                class Resolver(FakeProviderSettings):
                    supports_user_credential_fallback=True
                    @contextmanager
                    def resolve(self,context,capability):
                        with super().resolve(context,capability) as selected:yield replace(selected,connection_id='provider-upstage')
                class Costs:
                    def reserve(self,ctx,scope,current,**kwargs):
                        events.append('reserve');return CostReservation(scope,current,kwargs['attempt_key'],1,Decimal('0.157900800'),NOW)
                    def settle(self,ctx,held,**kwargs):
                        receipt=settle_reserved_usage(held,**kwargs);events.append(receipt.state);return receipt
                class Transport:
                    def post_json(self,**kwargs):
                        events.append('post')
                        if mode=='transport_failure':raise TimeoutError('CONTROLLED_FAILURE')
                        return {'choices':[{'message':{'content':json.dumps({'answer':'일반 답변입니다.'})}}],
                            'model':'solar-pro4-260806','usage':{'prompt_tokens':1000,'completion_tokens':100,'total_tokens':1100,'prompt_tokens_details':{'cached_tokens':200}}}
                class Egress(FakeEgress):
                    def authorize(self,ctx,**kwargs):
                        if mode=='egress_denied':raise QuestionAnsweringError('EGRESS_DENIED',status=403)
                        return super().authorize(ctx,**kwargs)
                evidence=UpstageModelContextEvidence('solar-pro4','solar-pro4-260806',524288,'https://console.upstage.ai/docs/models/solar-pro-4',NOW)
                resolver,repo=Resolver(),FakeRepository()
                service=QuestionAnsweringService(resolver,repo,FakeIndex(()),FakeCredential(),Transport(),Egress(),
                    run_cost_sessions=RunCostSessionFactory(Costs(),policies=(p,),evidence=evidence,clock=lambda:NOW))
                args=dict(source_id=None,source_version_id=None,question='안녕하세요',run_id='run-cost-general')
                if mode=='empty_evidence':args.update(source_id='source-cp3',source_version_id='source-version-cp3',question='What code?')
                if mode in ('transport_failure','egress_denied'):
                    with self.assertRaises(QuestionAnsweringError):service.ask(context,**args)
                    self.assertIsNone(repo.persisted)
                    self.assertEqual(events,['reserve','post','uncertain'] if mode=='transport_failure' else [])
                    self.assertEqual(resolver.calls,1)
                else:
                    service.ask(context,**args)
                    self.assertEqual(events,['reserve','post','known'])
                    self.assertEqual(repo.persisted['result'].price_card_receipt.basis,'price_card')

    def test_qa_workspace_xlsx_metadata_and_frozen_wire_are_scoped(self) -> None:
        cells = tuple(IndexedEvidenceChunk(
            f"cell-{i}", "source-cp3", "source-version-cp3", 1, text, f"span-{i}", 0.0,
            "sheet_cell", json.dumps({"sheet": "Plan", "range": address}, separators=(",", ":")),
        ) for i, (text, address) in enumerate((("REF-17", "B2"), ("57000", "B3"))))
        docx = tuple(replace(c, locator_kind='docx_block', locator_value=json.dumps(
            {'block': f'p:{i + 1}', 'start': 0, 'end': len(c.text)}, separators=(',', ':')))
            for i, c in enumerate(cells))
        pptx = tuple(replace(c, locator_kind='pptx_block', locator_value=json.dumps(
            {'block': f's:1:sp:2:p:{i + 1}', 'start': 0, 'end': len(c.text)}, separators=(',', ':')))
            for i, c in enumerate(cells))
        from daon_user_api.open_law_source_postgres import law_chunks
        law = tuple(IndexedEvidenceChunk(row['chunk_id'], row['source_id'], row['source_version_id'],
            row['page'], row['text'], row['evidence_span_id'], 0.0, 'section', None)
            for row in law_chunks('law-source-' + 'a' * 32, 'law-version-' + 'a' * 32, 'REF-17; 57000'))
        selection = ResolvedModel(
            connection_id="local", provider_code="OLLAMA", model_id="fixture",
            capability="text_generation", base_url="http://models.example.com",
            credential_version=0, default_version=1, catalog_version=1,
            provider_kind="server_internal", routing_owner="provider", daon_fallback_allowed=True,
            _credential=None,
        )

        class Resolver:
            @contextmanager
            def resolve(self, context, capability):
                yield selection

        for workspace, target, evidence, general, insufficient, metadata_calls in (
            ("workspace-qa", "workspace-qa", cells, False, False, 6),
            ("workspace-qa", "workspace-qa", cells, False, True, 6),
            ("workspace-qa", "workspace-qa", docx, False, False, 6),
            ("workspace-qa", "workspace-qa", docx, False, True, 6),
            ("workspace-qa", "workspace-qa", pptx, False, False, 6),
            ("workspace-qa", "workspace-qa", pptx, False, True, 6),
            ("workspace-qa", "workspace-qa", law, False, False, 6),
            ("workspace-qa", "workspace-qa", law, False, True, 6),
            ("workspace-other", "workspace-qa", law, False, False, 0),
            ("workspace-qa", "", law, False, False, 0),
            ("workspace-other", "workspace-qa", pptx, False, False, 0),
            ("workspace-qa", "workspace-qa", (pptx[0], docx[1]), False, False, 0),
            ("workspace-other", "workspace-qa", docx, False, False, 0),
            ("workspace-other", "workspace-qa", cells, False, False, 0),
            ("workspace-qa-suffix", "workspace-qa", cells, False, False, 0),
            ("workspace-qa", "", cells, False, False, 0),
            ("workspace-qa", "workspace-qa", tuple(replace(c, locator_kind="page", locator_value="1") for c in cells), False, False, 0),
            ("workspace-qa", "workspace-qa", (cells[0], replace(cells[1], locator_kind="image_region", locator_value="0,0,1,1,1,1")), False, False, 0),
            ("workspace-qa", "workspace-qa", (cells[0], replace(cells[1], source_version_id="other-version")), False, False, 0),
            ("workspace-qa", "workspace-qa", (), True, False, 0),
        ):
            with self.subTest(workspace=workspace, target=target, general=general, insufficient=insufficient, evidence=evidence):
                calls = []

                class Transport:
                    def get_json_no_auth(self, **kwargs):
                        calls.append(kwargs)
                        return {"models": [{"name": "fixture", "digest": "a" * 64}]}

                    def post_json_no_auth(self, **kwargs):
                        calls.append(kwargs)
                        if kwargs["url"].endswith("/api/show"):
                            return {"capabilities": ["completion", "thinking"], "model_info": {"architecture": "fixture"}}
                        result = {"answer": "Hello"} if general else {
                            "answer": ("선택한 원문 근거에 질문에 답할 정보가 없습니다." if evidence == law
                                       else "Insufficient evidence.") if insufficient else "REF-17; 57000",
                            "cited_chunk_ids": [] if insufficient else [c.chunk_id for c in evidence],
                            "insufficient": insufficient,
                        }
                        return {"message": {"content": json.dumps(result)}}

                class Egress(FakeEgress):
                    def prepare_payload(self, context, wire):
                        from types import SimpleNamespace
                        from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
                        policy = SimpleNamespace(get_effective=lambda _context: SimpleNamespace(
                            masking_required=True, redaction_required=True))
                        return PostgresQuestionEgressAuthorizer.prepare_payload(
                            SimpleNamespace(_policy_service=policy), context, wire)

                    def authorize(self, context, **kwargs):
                        self.wire = kwargs["provider_payload"]
                        self.asserted_metadata_count = len(calls)
                        return super().authorize(context, **kwargs)

                repository, transport, egress = FakeRepository(), Transport(), Egress()
                service = QuestionAnsweringService(
                    Resolver(), repository, FakeIndex(evidence), FakeCredential(), transport, egress,
                    qa_structured_ollama_workspace_id=target,
                )
                context = QuestionContext("tenant-cp3", workspace, "actor-cp3", "trace-cp3", "policy-v1")
                args = {"source_id": None if general else "source-cp3", "source_version_id": None if general else "source-version-cp3", "question": "Hello" if general else "Read the workbook values"}
                if evidence == law:
                    args.update(source_id=law[0].source_id, source_version_id=law[0].source_version_id)
                authorization = service.prepare_authorization(context, **args)
                answer = service.ask(context, run_id="run-qa", **args)
                chat = calls[-1]
                self.assertEqual(len(calls) - 1, metadata_calls)
                self.assertEqual(egress.asserted_metadata_count, metadata_calls)
                self.assertEqual(authorization.provider_payload, egress.wire)
                self.assertEqual(egress.wire, canonical_json_bytes(chat["payload"]))
                if evidence in (docx, pptx, law) and workspace == target:
                    # Real service canonical roundtrip must not choose answer-first grammar.
                    frozen = json.loads(egress.wire)
                    for branch, wire_branch in zip(chat["payload"]["format"]["anyOf"], frozen["format"]["anyOf"], strict=True):
                        self.assertEqual(list(wire_branch["properties"]), ["answer", "cited_chunk_ids", "insufficient"])
                        self.assertEqual(list(branch["properties"]), ["insufficient", "cited_chunk_ids", "answer"])
                        self.assertEqual(branch, wire_branch)
                    import hashlib
                    self.assertEqual(hashlib.sha256(egress.wire).hexdigest(),
                                     hashlib.sha256(canonical_json_bytes(chat["payload"])).hexdigest())
                    self.assertEqual(json.loads(chat["payload"]["messages"][1]["content"])["evidence"][0]["text"], evidence[0].text)
                    self.assertEqual(chat["payload"]["messages"][1]["content"],
                                     canonical_json_bytes(json.loads(chat["payload"]["messages"][1]["content"])).decode())
                if metadata_calls:
                    self.assertIs(chat["payload"]["think"], False)
                else:
                    self.assertNotIn("think", chat["payload"])
                    original = (OllamaTextGenerationAdapter.general_provider_payload(
                        GeneralConversationRequest("Hello", "trace-cp3"), selection,
                    ) if general else OllamaTextGenerationAdapter.provider_payload(
                        GroundedQuestionRequest(args["question"], evidence, "trace-cp3"), selection,
                    ))
                    self.assertEqual(egress.wire, egress.prepare_payload(context, canonical_json_bytes(original)))
                self.assertIsNone(chat["timeout_seconds"])
                self.assertEqual(chat["payload"]["options"]["num_predict"], 64 if general else 512)
                self.assertEqual(answer.insufficient, insufficient)
                self.assertIsNotNone(repository.persisted)

    def test_qa_target_does_not_modify_other_provider_wire(self) -> None:
        evidence = (IndexedEvidenceChunk("chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "ORANGE-COMPASS-42", "span-page-2", 1.0),)
        for target in ("", "workspace-cp3"):
            with self.subTest(target=target):
                class Transport(FakeTransport):
                    def post_json(self, **kwargs):
                        self.wire = canonical_json_bytes(kwargs["payload"])
                        return super().post_json(**kwargs)

                    def get_json_no_auth(self, **kwargs):
                        raise AssertionError("non-Ollama must not call metadata")

                repository, transport = FakeRepository(), Transport()
                service = QuestionAnsweringService(FakeProviderSettings(), repository, FakeIndex(evidence),
                    FakeCredential(), transport, FakeEgress(), qa_structured_ollama_workspace_id=target)
                service.ask(QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
                    source_id="source-cp3", source_version_id="source-version-cp3", question="What code?", run_id="run-qa")
                if not target:
                    baseline = transport.wire
                else:
                    self.assertEqual(transport.wire, baseline)

    def test_qa_metadata_failure_never_authorizes_generates_or_persists(self) -> None:
        cells = (IndexedEvidenceChunk("cell-0", "source-cp3", "source-version-cp3", 1,
            "REF-17", "span-0", 0.0, "sheet_cell", '{"sheet":"Plan","range":"B2"}'),)

        class Resolver:
            @contextmanager
            def resolve(self, context, capability):
                yield ResolvedModel(
                    connection_id="local", provider_code="OLLAMA", model_id="fixture", capability=capability,
                    base_url="http://models.example.com", credential_version=0, default_version=1,
                    catalog_version=1, provider_kind="server_internal", routing_owner="provider",
                    daon_fallback_allowed=True, _credential=None,
                )

        class Transport:
            def get_json_no_auth(self, **kwargs):
                raise TimeoutError("upstream detail must not be exposed")

            def post_json_no_auth(self, **kwargs):
                raise AssertionError("metadata failure must not call show or chat")

        class Egress:
            def authorize(self, context, **kwargs):
                raise AssertionError("metadata failure must not authorize")

        repository = FakeRepository()
        service = QuestionAnsweringService(Resolver(), repository, FakeIndex(cells), FakeCredential(),
            Transport(), Egress(), qa_structured_ollama_workspace_id="workspace-qa")
        with self.assertRaises(QuestionAnsweringError) as caught:
            service.ask(QuestionContext("tenant-cp3", "workspace-qa", "actor-cp3", "trace-cp3", "policy-v1"),
                source_id="source-cp3", source_version_id="source-version-cp3", question="Read workbook", run_id="run-qa")
        self.assertEqual(caught.exception.code, "TEXT_GENERATION_RESPONSE_INVALID")
        self.assertIsNone(repository.persisted)

    def test_custom_keyless_compatible_routes_without_authentication(self) -> None:
        class KeylessTransport:
            def __init__(self):
                self.calls = []

            def post_json_no_auth(self, **kwargs):
                self.calls.append(("openai", kwargs))
                return {"choices": [{"message": {"content": json.dumps({"answer": "ready"})}}]}

            def post_json_headers(self, **kwargs):
                self.calls.append(("anthropic", kwargs))
                return {"content": [{"type": "text", "text": json.dumps({"answer": "ready"})}]}

            def post_json(self, **_kwargs):
                raise AssertionError("keyless CUSTOM must not use authenticated transport")

        for adapter_type, expected_path in (
            ("openai_compatible", "/chat/completions"),
            ("anthropic_compatible", "/messages"),
        ):
            with self.subTest(adapter_type=adapter_type):
                selection = ResolvedModel(
                    connection_id="custom-keyless", provider_code="CUSTOM", model_id="manual-a",
                    capability="text_generation", base_url="https://models.example.com/v1",
                    credential_version=0, default_version=1, catalog_version=1,
                    provider_kind="external_api", routing_owner="provider", daon_fallback_allowed=True,
                    _credential=None, adapter_type=adapter_type,
                )
                transport = KeylessTransport()
                registry = QuestionAdapterRegistry()
                result = registry.generate_general(registry.prepare_general(selection, "안녕하세요", "trace-keyless", transport))
                self.assertEqual(result.answer, "ready")
                self.assertEqual(transport.calls[0][1]["url"], f"https://models.example.com/v1{expected_path}")
                if adapter_type == "anthropic_compatible":
                    self.assertNotIn("x-api-key", transport.calls[0][1]["headers"])

    def test_unknown_custom_adapter_type_fails_closed_for_both_question_modes(self) -> None:
        selection = ResolvedModel(
            connection_id="custom-1", provider_code="CUSTOM", model_id="manual-a",
            capability="text_generation", base_url="https://models.example.com/v1",
            credential_version=1, default_version=1, catalog_version=1,
            provider_kind="external_api", routing_owner="provider", daon_fallback_allowed=True,
            _credential=bytearray(b"fixture-secret"), adapter_type="unknown_protocol",
        )
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "ORANGE-COMPASS-42", "span-page-2", 1.0,
        ),)
        registry = QuestionAdapterRegistry()
        transport = CapturingGatewayTransport()
        with self.assertRaisesRegex(ValueError, "^TEXT_PROVIDER_UNAVAILABLE$"):
            registry.prepare(selection, evidence, "What code?", "trace-cp3", transport)
        with self.assertRaisesRegex(ValueError, "^TEXT_PROVIDER_UNAVAILABLE$"):
            registry.prepare_general(selection, "안녕하세요", "trace-cp3", transport)
        self.assertEqual(transport.calls, [])

    def test_custom_openai_and_legacy_adapter_types_keep_chat_completions(self) -> None:
        for adapter_type in ("openai_compatible", "", "CUSTOM"):
            with self.subTest(adapter_type=adapter_type):
                selection = ResolvedModel(
                    connection_id="custom-1", provider_code="CUSTOM", model_id="manual-a",
                    capability="text_generation", base_url="https://models.example.com/v1",
                    credential_version=1, default_version=1, catalog_version=1,
                    provider_kind="external_api", routing_owner="provider", daon_fallback_allowed=True,
                    _credential=bytearray(b"fixture-secret"), adapter_type=adapter_type,
                )
                transport = CapturingGatewayTransport()
                registry = QuestionAdapterRegistry()
                result = registry.generate_general(registry.prepare_general(
                    selection, "안녕하세요", "trace-cp3", transport,
                ))
                self.assertEqual(result.answer, "gateway answer")
                self.assertEqual(transport.calls[0]["url"], "https://models.example.com/v1/chat/completions")

    def test_custom_anthropic_selection_routes_both_question_modes_to_messages(self) -> None:
        class MessagesTransport:
            def __init__(self):
                self.calls = []

            def post_json_headers(self, **kwargs):
                self.calls.append(kwargs)
                answer = (
                    {"answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False}
                    if len(self.calls) == 1 else {"answer": "일반 답변입니다."}
                )
                return {"content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}]}

            def post_json(self, **_kwargs):
                raise AssertionError("CUSTOM anthropic must not use Chat Completions")

        selection = ResolvedModel(
            connection_id="custom-1", provider_code="CUSTOM", model_id="manual-a",
            capability="text_generation", base_url="https://models.example.com/v1",
            credential_version=1, default_version=1, catalog_version=1,
            provider_kind="external_api", routing_owner="provider", daon_fallback_allowed=True,
            _credential=bytearray(b"fixture-secret"), adapter_type="anthropic_compatible",
        )
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Verified code ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)
        transport = MessagesTransport()
        registry = QuestionAdapterRegistry()
        grounded = registry.generate_prepared(registry.prepare(
            selection, evidence, "What code?", "trace-cp3", transport,
        ))
        general = registry.generate_general(registry.prepare_general(
            selection, "안녕하세요", "trace-cp3", transport,
        ))
        self.assertEqual(grounded.cited_chunk_ids, ("chunk-page-2",))
        self.assertEqual(general.answer, "일반 답변입니다.")
        self.assertEqual(len(transport.calls), 2)

    def test_custom_anthropic_grounded_service_persists_validated_citation(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Verified code ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)

        class Resolver:
            @contextmanager
            def resolve(self, _context, capability):
                selection = ResolvedModel(
                    connection_id="custom-1", provider_code="CUSTOM", model_id="manual-a",
                    capability=capability, base_url="https://models.example.com/v1",
                    credential_version=1, default_version=1, catalog_version=1,
                    provider_kind="external_api", routing_owner="provider", daon_fallback_allowed=True,
                    _credential=bytearray(b"fixture-secret"), adapter_type="anthropic_compatible",
                )
                try:
                    yield selection
                finally:
                    selection.release()

        class Transport:
            def __init__(self):
                self.calls = []

            def post_json_headers(self, **kwargs):
                self.calls.append(kwargs)
                return {"content": [{"type": "text", "text": json.dumps({
                    "answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"],
                    "insufficient": False,
                })}]}

        repository, transport = FakeRepository(), Transport()
        service = QuestionAnsweringService(
            Resolver(), repository, FakeIndex(evidence), FakeCredential(), transport, FakeEgress(),
            adapter_registry=QuestionAdapterRegistry(),
        )
        result = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id="source-cp3", source_version_id="source-version-cp3",
            question="What code?", run_id="run-custom-anthropic",
        )
        self.assertEqual(result.answer, "ORANGE-COMPASS-42")
        self.assertEqual(repository.persisted["result"].cited_chunk_ids, ("chunk-page-2",))
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("fixture-secret", repr(repository.persisted))

    def test_question_uses_workspace_text_default_and_latest_connection_credential(self) -> None:
        resolver = StaticWorkspaceModelResolver()
        repository = FakeRepository()
        transport = CapturingGatewayTransport()
        service = QuestionAnsweringService(
            resolver, repository, FakeIndex(()), FakeCredential(), transport, FakeEgress(),
        )

        answer = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id=None, source_version_id=None, question="안녕하세요", run_id="run-workspace-model",
        )

        self.assertEqual(answer.answer, "gateway answer")
        self.assertEqual(resolver.calls, [("workspace-cp3", "text_generation")])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["url"], "https://gateway.example.com/v1/chat/completions")
        self.assertEqual(transport.calls[0]["api_key"], "data-api-client-key")
        self.assertEqual(transport.calls[0]["payload"]["model"], "assistant-default")
        selection = repository.persisted["selection"]
        self.assertEqual(
            (selection.connection_id, selection.provider_code, selection.model_id, selection.credential_version),
            ("eoul-primary", "EOUL_GATEWAY", "assistant-default", 7),
        )
        self.assertNotIn("data-api-client-key", repr(selection))
        self.assertNotIn("data-api-client-key", repr(repository.persisted))

    def test_gateway_failure_has_one_external_attempt_and_no_daon_fallback(self) -> None:
        transport = CapturingGatewayTransport(failure=True)
        service = QuestionAnsweringService(
            StaticWorkspaceModelResolver(), FakeRepository(), FakeIndex(()),
            FakeCredential(), transport, FakeEgress(),
        )

        with self.assertRaisesRegex(QuestionAnsweringError, "TEXT_GENERATION_FAILED"):
            service.ask(
                QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
                source_id=None, source_version_id=None, question="안녕하세요", run_id="run-gateway-failure",
            )

        self.assertEqual(len(transport.calls), 1)

    def test_provider_failure_retries_once_with_same_users_personal_credential(self) -> None:
        resolver = PersonalCredentialFallbackResolver()
        transport = SystemCredentialFailureThenPersonalSuccessTransport()
        service = QuestionAnsweringService(
            resolver, FakeRepository(), FakeIndex(()), FakeCredential(), transport, FakeEgress(),
        )

        answer = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id=None, source_version_id=None, question="안녕하세요", run_id="run-personal-fallback",
        )

        self.assertEqual(answer.answer, "personal answer")
        self.assertEqual(resolver.sources, ["system", "user"])
        self.assertEqual(transport.calls, 2)

    def test_omniroute_uses_responses_endpoint_and_logical_model_once(self) -> None:
        transport = OmniRouteTransport()
        service = QuestionAnsweringService(
            StaticWorkspaceModelResolver(provider_code="OMNIROUTE"), FakeRepository(),
            FakeIndex(()), FakeCredential(), transport, FakeEgress(),
        )

        answer = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id=None, source_version_id=None, question="안녕하세요", run_id="run-omni",
        )

        self.assertEqual(answer.answer, "omni answer")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["url"], "https://gateway.example.com/v1/responses")
        self.assertEqual(transport.calls[0]["payload"]["model"], "assistant-default")

    def test_omniroute_selected_or_auto_model_reaches_responses_unchanged(self) -> None:
        for model_id in ("auto", "cc/claude-sonnet", "combo-writing"):
            with self.subTest(model_id=model_id):
                transport = OmniRouteTransport()
                service = QuestionAnsweringService(
                    StaticWorkspaceModelResolver(provider_code="OMNIROUTE", model_id=model_id),
                    FakeRepository(), FakeIndex(()), FakeCredential(), transport, FakeEgress(),
                )

                answer = service.ask(
                    QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
                    source_id=None, source_version_id=None, question="안녕하세요", run_id="run-omni-model",
                )

                self.assertEqual(answer.answer, "omni answer")
                self.assertEqual(transport.calls[0]["url"], "https://gateway.example.com/v1/responses")
                self.assertEqual(transport.calls[0]["payload"]["model"], model_id)

    def test_router_auto_ids_reach_existing_question_adapters_unchanged(self) -> None:
        cases = (
            ("OMNIROUTE", "auto", "https://omniroute.example/v1", "openai_compatible", "/responses"),
            ("OPENROUTER", "openrouter/auto", "https://openrouter.ai/api/v1", "OPENROUTER", "/chat/completions"),
            ("CUSTOM", "auto", "https://media-bridge-gateway.sinsan.kr/v1", "openai_compatible", "/chat/completions"),
        )
        for provider_code, model_id, base_url, adapter_type, path in cases:
            with self.subTest(provider_code=provider_code):
                selection = ResolvedModel(
                    connection_id=f"{provider_code.lower()}-router", provider_code=provider_code,
                    model_id=model_id, capability="text_generation", base_url=base_url,
                    credential_version=1, default_version=3, catalog_version=11,
                    provider_kind="external_api", routing_owner="gateway" if provider_code == "OMNIROUTE" else "provider",
                    daon_fallback_allowed=provider_code != "OMNIROUTE",
                    _credential=bytearray(b"fixture-key"), adapter_type=adapter_type,
                )
                transport = OmniRouteTransport() if provider_code == "OMNIROUTE" else CapturingGatewayTransport()
                registry = QuestionAdapterRegistry()
                result = registry.generate_general(registry.prepare_general(
                    selection, "안녕하세요", "trace-auto", transport,
                ))
                self.assertTrue(result.answer)
                self.assertEqual(transport.calls[0]["url"], base_url + path)
                self.assertEqual(transport.calls[0]["payload"]["model"], model_id)
                selection.release()

    def test_general_conversation_intent_is_exact_and_factual_suffix_fails_closed(self) -> None:
        for value in ("안녕", "안녕하세요!", "안녕하세요?", "고마워", "감사합니다.", "Daon 사용법 알려줘"):
            self.assertTrue(is_general_conversation_intent(value), value)
        for value in (
            "", "안녕, 삼성 매출 알려줘", "고마워. 이 문서를 요약해줘",
            "2026년 매출은?", "이 Source 사용법을 근거로 알려줘",
            "Ｄａｏｎ 사용법 알려줘", "안녕하세요！", "안녕하세요？", "안녕하세요　",
        ):
            self.assertFalse(is_general_conversation_intent(value), value)

    def test_general_conversation_calls_selected_provider_without_source_or_citation(self) -> None:
        class GeneralTransport(FakeTransport):
            def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                return {"choices": [{"message": {"content": json.dumps({
                    "answer": "안녕하세요. 무엇을 도와드릴까요?",
                }, ensure_ascii=False)}}]}

        provider, repository, transport = FakeProviderSettings(), FakeRepository(), GeneralTransport()
        service = QuestionAnsweringService(
            provider, repository, FakeIndex(()), FakeCredential(), transport, FakeEgress(),
        )

        answer = service.ask(
            QuestionContext(
                "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
                "notebook-cp3",
            ),
            source_id=None, source_version_id=None, question="안녕하세요!", run_id="run-general",
        )

        self.assertFalse(answer.insufficient)
        self.assertEqual(answer.citations, ())
        self.assertEqual(transport.calls, 1)
        self.assertTrue(repository.persisted["provider_called"])
        self.assertEqual(repository.persisted["context_mode"], "general_ungrounded")
        self.assertEqual(repository.persisted["context_sources"], ())

    def test_non_owner_never_calls_provider_and_timeout_requires_safe_new_run(self) -> None:
        class FollowerEgress(FakeEgress):
            def authorize(self, context, **kwargs):  # type: ignore[no-untyped-def]
                del context, kwargs
                return {"provider_owner": False}

        class GeneralTransport(FakeTransport):
            def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                return {"choices": [{"message": {"content": json.dumps({
                    "answer": "새 Run에서 복구되었습니다.",
                }, ensure_ascii=False)}}]}

        context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
            "notebook-cp3",
        )
        transport = GeneralTransport()
        follower = QuestionAnsweringService(
            FakeProviderSettings(), FakeRepository(), FakeIndex(()), FakeCredential(),
            transport, FollowerEgress(), concurrent_wait_seconds=0,
        )
        with self.assertRaises(QuestionAnsweringError) as blocked:
            follower.ask(
                context, source_id=None, source_version_id=None, question="안녕하세요!",
                run_id="run-poisoned-owner",
            )
        self.assertEqual(blocked.exception.code, "QUESTION_NEW_RUN_REQUIRED")
        self.assertEqual(blocked.exception.status, 409)
        self.assertTrue(blocked.exception.retryable)
        self.assertEqual(transport.calls, 0)

        recovered = QuestionAnsweringService(
            FakeProviderSettings(), FakeRepository(), FakeIndex(()), FakeCredential(),
            transport, FakeEgress(),
        ).ask(
            context, source_id=None, source_version_id=None, question="안녕하세요!",
            run_id="run-recovered-with-new-idempotency",
        )
        self.assertEqual(recovered.answer, "새 Run에서 복구되었습니다.")
        self.assertEqual(transport.calls, 1)

    def test_non_owner_completed_result_uses_fingerprint_authoritative_replay(self) -> None:
        class FollowerEgress(FakeEgress):
            def authorize(self, context, **kwargs):  # type: ignore[no-untyped-def]
                del context, kwargs
                return {"provider_owner": False}

        class CompletedRepository(FakeRepository):
            def __init__(self) -> None:
                super().__init__()
                self.completed = StoredQuestionAnswer(
                    "run-follower", "result-follower", "stored", False, (),
                )
                self.replay_fingerprints: list[str] = []

            def load_completed_for_replay(
                self, context, run_id, request_fingerprint,  # type: ignore[no-untyped-def]
            ):
                del context, run_id
                self.replay_fingerprints.append(request_fingerprint)
                raise QuestionRepositoryError("IDEMPOTENCY_KEY_REUSED", status=409)

        repository = CompletedRepository()
        transport = FakeTransport()
        service = QuestionAnsweringService(
            FakeProviderSettings(), repository, FakeIndex(()), FakeCredential(),
            transport, FollowerEgress(),
        )
        fingerprint = "sha256:" + "f" * 64
        with self.assertRaises(QuestionRepositoryError) as mismatch:
            service.ask(
                QuestionContext(
                    "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3",
                    "policy-v1", "notebook-cp3",
                ),
                source_id=None, source_version_id=None, question="안녕하세요!",
                run_id="run-follower", request_fingerprint=fingerprint,
            )
        self.assertEqual((mismatch.exception.code, mismatch.exception.status), (
            "IDEMPOTENCY_KEY_REUSED", 409,
        ))
        self.assertEqual(repository.replay_fingerprints, [fingerprint])
        self.assertEqual(transport.calls, 0)

    def test_general_conversation_uses_the_exact_egress_transformed_payload(self) -> None:
        class RecordingTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self.payload = None

            def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                self.payload = kwargs["payload"]
                return {"choices": [{"message": {"content": json.dumps({
                    "answer": "안녕하세요.",
                }, ensure_ascii=False)}}]}

        class TransformingEgress(FakeEgress):
            def __init__(self) -> None:
                self.authorized_payload = None

            def prepare_payload(self, context, provider_payload):  # type: ignore[no-untyped-def]
                payload = json.loads(provider_payload)
                payload["messages"][-1]["content"] = "[MASKED]"
                return json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")

            def authorize(self, context, **kwargs):  # type: ignore[no-untyped-def]
                self.authorized_payload = kwargs["provider_payload"]
                return super().authorize(context, **kwargs)

        transport, egress = RecordingTransport(), TransformingEgress()
        service = QuestionAnsweringService(
            FakeProviderSettings(), FakeRepository(), FakeIndex(()), FakeCredential(),
            transport, egress,
        )

        service.ask(
            QuestionContext(
                "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
                "notebook-cp3",
            ),
            source_id=None, source_version_id=None, question="안녕하세요!", run_id="run-general",
        )

        self.assertEqual(transport.payload["messages"][-1]["content"], "[MASKED]")
        self.assertEqual(
            egress.authorized_payload,
            json.dumps(
                transport.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8"),
        )

    def test_mixed_context_searches_every_bound_source_and_persists_frozen_context(self) -> None:
        raw = IndexedEvidenceChunk(
            "chunk-raw", "source-raw", "version-raw", 1,
            "원문 근거", "span-raw", 0.8,
        )
        knowledge = IndexedEvidenceChunk(
            "chunk-knowledge", "source-knowledge", "version-knowledge", 2,
            "정제된 승인 지식", "span-knowledge", 1.0,
        )

        class MixedTransport(FakeTransport):
            def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                return {"choices": [{"message": {"content": json.dumps({
                    "answer": "승인 지식과 원문을 함께 확인했습니다.",
                    "cited_chunk_ids": ["chunk-knowledge", "chunk-raw"],
                    "insufficient": False,
                })}}]}

        repository, transport = FakeRepository(), MixedTransport()
        service = QuestionAnsweringService(
            FakeProviderSettings(), repository,
            FakeIndex({"version-raw": (raw,), "version-knowledge": (knowledge,)}),
            FakeCredential(), transport, FakeEgress(),
        )
        sources = (
            QuestionInputSource("daon_knowledge", "package-daon3", "source-knowledge", "version-knowledge"),
            QuestionInputSource("raw_source", "version-raw", "source-raw", "version-raw"),
        )

        service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id="source-knowledge", source_version_id="version-knowledge",
            question="근거를 종합해줘", run_id="run-mixed",
            context_mode="mixed", context_sources=sources,
        )

        self.assertEqual(repository.persisted["context_mode"], "mixed")
        self.assertEqual(repository.persisted["context_sources"], sources)
        self.assertEqual(
            tuple(item.chunk_id for item in repository.persisted["evidence"]),
            ("chunk-knowledge", "chunk-raw"),
        )
        self.assertEqual(transport.calls, 1)

    def test_registry_routes_groq_mistral_and_upstage_without_provider_fallback(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "ORANGE-COMPASS-42", "span-page-2", 1.0,
        ),)
        registry = QuestionAdapterRegistry()
        for provider_code, base_url in (
            ("GROQ", "https://api.groq.com/openai/v1"),
            ("MISTRAL", "https://api.mistral.ai/v1"),
            ("UPSTAGE", "https://api.upstage.ai/v1"),
        ):
            selection = ResolvedModel(
                connection_id=f"connection-{provider_code.lower()}", provider_code=provider_code,
                model_id="selected-model", capability="text_generation", base_url=base_url,
                credential_version=1, default_version=1, catalog_version=1,
                provider_kind="external_api", routing_owner="provider",
                daon_fallback_allowed=True, _credential=bytearray(b"server-secret"),
            )
            prepared = registry.prepare(
                selection, evidence, "phrase?", "trace-cp3", FakeTransport(),
            )
            self.assertEqual(prepared.selection.provider_code, provider_code)
            selection.release()

    def test_ollama_provider_uses_internal_adapter_without_external_credential(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-local", "source-cp3", "source-version-cp3", 1,
            "정책 보존 기간은 30일입니다.", "span-local", 1.0,
        ),)
        class LocalProviderSettings:
            @contextmanager
            def resolve(self, context, capability):  # type: ignore[no-untyped-def]
                yield ResolvedModel(
                    connection_id="ollama-local", provider_code="OLLAMA",
                    model_id="llama3.2:3b", capability=capability,
                    base_url="http://ollama.internal:11434", credential_version=0,
                    default_version=1, catalog_version=1, provider_kind="server_internal",
                    routing_owner="provider", daon_fallback_allowed=True, _credential=None,
                )

        class NoExternalCredential:
            def resolve(self, provider_code: str) -> str:
                raise AssertionError("OLLAMA must not resolve an external credential")

        class OllamaTransport:
            def __init__(self) -> None:
                self.calls = []

            def post_json_no_auth(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls.append(kwargs)
                return {"message": {"content": json.dumps({
                    "answer": "정책 보존 기간은 30일입니다.",
                    "cited_chunk_ids": ["chunk-local"], "insufficient": False,
                }, ensure_ascii=False)}}

        repository = FakeRepository()
        transport = OllamaTransport()
        service = QuestionAnsweringService(
            LocalProviderSettings(), repository, FakeIndex(evidence),
            NoExternalCredential(), transport, FakeEgress(),
            adapter_registry=QuestionAdapterRegistry(),
        )
        answer = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id="source-cp3", source_version_id="source-version-cp3",
            question="정책 보존 기간은?", run_id="run-local",
        )

        self.assertEqual(answer.answer, "정책 보존 기간은 30일입니다.")
        self.assertEqual(repository.persisted["result"].cited_chunk_ids, ("chunk-local",))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["url"], "http://ollama.internal:11434/api/chat")
        self.assertEqual(transport.calls[0]["payload"]["model"], "llama3.2:3b")
        self.assertEqual(transport.calls[0]["payload"]["options"], {"num_predict": 512, "temperature": 0})
        self.assertEqual(transport.calls[0]["payload"]["keep_alive"], "5m")
        self.assertIsNone(transport.calls[0]["timeout_seconds"])
        self.assertNotIn("api_key", transport.calls[0])

    def test_snapshot_is_resolved_once_and_grounded_result_is_persisted(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "ORANGE-COMPASS-42", "span-page-2", 1.0,
        ),)
        provider, repository, transport = FakeProviderSettings(), FakeRepository(), FakeTransport()
        service = QuestionAnsweringService(
            provider, repository, FakeIndex(evidence), FakeCredential(), transport, FakeEgress(),
        )

        answer = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id="source-cp3", source_version_id="source-version-cp3",
            question="What is the citation verification phrase?", run_id="run-cp3",
        )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(answer.answer, "ORANGE-COMPASS-42")
        self.assertTrue(repository.persisted["provider_called"])

        replay = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id="source-cp3", source_version_id="source-version-cp3",
            question="What is the citation verification phrase?", run_id="run-cp3",
        )
        self.assertEqual(replay, answer)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(transport.calls, 1)

    def test_no_retrieved_evidence_falls_back_to_general_provider_answer(self) -> None:
        provider, repository, transport = FakeProviderSettings(), FakeRepository(), GeneralAnswerTransport()
        service = QuestionAnsweringService(
            provider, repository, FakeIndex(()), FakeCredential(), transport, FakeEgress(),
        )

        answer = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id="source-cp3", source_version_id="source-version-cp3",
            question="unsupported?", run_id="run-cp3",
        )

        self.assertFalse(answer.insufficient)
        self.assertEqual(answer.citations, ())
        self.assertEqual(transport.calls, 1)
        self.assertTrue(repository.persisted["provider_called"])

    def test_question_without_retrieved_evidence_falls_back_to_general_answer(self) -> None:
        provider, repository, transport = FakeProviderSettings(), FakeRepository(), GeneralAnswerTransport()
        service = QuestionAnsweringService(
            provider, repository, FakeIndex(()), FakeCredential(), transport, FakeEgress(),
        )

        answer = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id="source-cp3", source_version_id="source-version-cp3",
            question="unsupported?", run_id="run-general-fallback",
        )

        self.assertFalse(answer.insufficient)
        self.assertEqual(answer.citations, ())
        self.assertEqual(transport.calls, 1)
        self.assertTrue(repository.persisted["provider_called"])


class S42ZeroEvidenceOwnerFreezeTests(unittest.TestCase):
    """실제 service/non-QA adapter의 코드 회귀다. 전송/egress는 대역이며 실제 network wire 증거가 아니다."""

    context=QuestionContext('tenant-cp3','workspace-s42-nonqa','actor-cp3','trace-s42-zero-evidence',
        'policy-v1','notebook-s42-zero-evidence')
    question='S42 근거 없는 자료의 내용을 알려주세요.'

    def _flow(self,*,owner,outcome='success'):
        phases=[]

        class Repository(FakeRepository):
            def __init__(self):
                super().__init__()
                self.failed_writes=0
                self.completed_writes=0
            def persist_failed(self,context,**kwargs):
                # 실패 재쓰기는 건수만 확인한다. selection/credential/원예외를 시험 capture에 보관하지 않는다.
                self.failed_writes+=1
            def persist_completed(self,context,**kwargs):
                self.completed_writes+=1
                return super().persist_completed(context,**{key:value for key,value in kwargs.items() if key!='selection'})

        class Index(FakeIndex):
            def __init__(self):
                super().__init__(())
                self.searched=[]
            def search(self,context,**kwargs):
                self.searched.append((kwargs['source_id'],kwargs['source_version_id']))
                return super().search(context,**kwargs)

        class Transport(FakeTransport):
            def __init__(self):
                super().__init__()
                self.sent=[]
            def post_json(self,**kwargs):
                self.calls+=1
                phases.append('transport')
                # 실제 adapter가 넘긴 payload/url만 관측한다. 인증 header는 보관하지 않는다.
                self.sent.append({'url':kwargs['url'],'payload':json.loads(json.dumps(kwargs['payload']))})
                if outcome=='cancelled':
                    from asyncio import CancelledError
                    raise CancelledError()
                if outcome=='response_invalid':
                    return {'choices':[{'message':{'content':'{S42-malformed-response'}}]}
                return {'choices':[{'message':{'content':'{"answer":"S42 일반 합성 응답"}'}}]}

        class Egress(FakeEgress):
            def __init__(self):
                self.authorized=[]
                self.transformed=[]
            def prepare_payload(self,context,provider_payload):
                original=bytes(provider_payload)
                payload=json.loads(original)
                payload['messages'][0]['content']+=' [S42_EGRESS_CANONICAL]'
                transformed=canonical_json_bytes(payload)
                self.transformed.append((original,transformed))
                phases.append('transform')
                return transformed
            def authorize(self,context,**kwargs):
                self.authorized.append({'provider_payload':bytes(kwargs['provider_payload']),
                    'source_id':kwargs['source_id'],'source_version_id':kwargs['source_version_id'],
                    'context_mode':kwargs['context_mode'],'no_external_payload':kwargs.get('no_external_payload',False)})
                phases.append('authorize')
                return {**super().authorize(context,**kwargs),'provider_owner':owner}

        repository,index,transport,egress=Repository(),Index(),Transport(),Egress()
        resolver=FakeProviderSettings()
        service=QuestionAnsweringService(resolver,repository,index,FakeCredential(),transport,egress,
            adapter_registry=QuestionAdapterRegistry(),concurrent_wait_seconds=0)
        return service,repository,index,transport,egress,phases

    def test_nonqa_zero_evidence_nonowner_never_sends_or_rewrites_failure(self):
        service,repository,index,transport,egress,_=self._flow(owner=False)
        failure=None
        try:
            service.ask(self.context,source_id='source-zero-evidence',source_version_id='version-zero-evidence',
                question=self.question,run_id='run-s42-zero-nonowner')
        except QuestionAnsweringError as error:
            failure=(error.code,error.status,error.retryable)
        self.assertEqual(index.searched,[('source-zero-evidence','version-zero-evidence')])
        self.assertEqual(len(egress.authorized),1)
        self.assertEqual(transport.calls,0,'ZERO_EVIDENCE_PROVIDER_SENT_WITHOUT_OWNER')
        self.assertEqual(transport.sent,[])
        self.assertIsNone(repository.persisted)
        self.assertIsNone(repository.completed)
        self.assertEqual(repository.failed_writes,0)
        self.assertEqual(failure,('QUESTION_NEW_RUN_REQUIRED',409,True))

    def test_nonqa_zero_evidence_owner_freezes_transformed_nonempty_canonical_payload(self):
        service,repository,index,transport,egress,phases=self._flow(owner=True)
        answer=service.ask(self.context,source_id='source-zero-evidence',source_version_id='version-zero-evidence',
            question=self.question,run_id='run-s42-zero-owner')
        self.assertEqual(index.searched,[('source-zero-evidence','version-zero-evidence')])
        self.assertEqual((transport.calls,len(egress.authorized)),(1,1))
        sent=transport.sent[0]
        payload=sent['payload']
        self.assertEqual(sent['url'],'https://api.upstage.ai/v1/chat/completions')
        self.assertEqual(set(payload),{'model','messages','response_format'})
        self.assertEqual(payload['model'],'solar-pro4')
        self.assertEqual([message['role'] for message in payload['messages']],['system','user'])
        self.assertEqual(payload['messages'][1],{'role':'user','content':'S42 근거 없는 자료의 내용을 알려주세요.'})
        self.assertNotIn('max_tokens',payload,'non-QA의 기존 출력 한도 의미를 바꾸지 않는다')
        self.assertEqual(payload['response_format'],{'type':'json_schema','json_schema':{
            'name':'daon_general_conversation_answer','strict':True,'schema':{'type':'object',
                'properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}}})
        authorized=egress.authorized[0]
        self.assertEqual({key:authorized[key] for key in ('source_id','source_version_id','context_mode','no_external_payload')},
            {'source_id':None,'source_version_id':None,'context_mode':'general_ungrounded','no_external_payload':False})
        # 준비 helper로 기대 bytes를 만들지 않는다. 서로 다른 승인/송신 포트에서 관측한 표현을 비교한다.
        self.assertTrue(authorized['provider_payload'],'ZERO_EVIDENCE_APPROVED_PAYLOAD_EMPTY')
        self.assertEqual(canonical_json_bytes(payload),authorized['provider_payload'])
        self.assertEqual(len(egress.transformed),1)
        original,transformed=egress.transformed[0]
        self.assertTrue(original and transformed)
        self.assertNotEqual(original,transformed)
        self.assertEqual(transformed,authorized['provider_payload'])
        self.assertTrue(payload['messages'][0]['content'].endswith(' [S42_EGRESS_CANONICAL]'))
        self.assertEqual(json.loads(original)['messages'][1]['content'],'S42 근거 없는 자료의 내용을 알려주세요.')
        self.assertEqual(phases,['transform','authorize','transport'])
        self.assertEqual((answer.answer,answer.citations),('S42 일반 합성 응답',()))
        self.assertEqual(repository.failed_writes,0)
        self.assertEqual((repository.persisted['context_mode'],repository.persisted['context_sources'],
            repository.persisted['provider_called']),('general_ungrounded',(),True))

    def test_nonqa_zero_evidence_completed_reentry_reuses_result_without_another_send(self):
        service,repository,index,transport,egress,phases=self._flow(owner=True)
        request={'source_id':'source-zero-evidence','source_version_id':'version-zero-evidence',
            'question':self.question,'run_id':'run-s42-zero-reentry'}
        first=service.ask(self.context,**request)
        self.assertIs(first,repository.completed)
        self.assertEqual((transport.calls,repository.completed_writes,repository.failed_writes),(1,1,0))
        saved=repository.persisted
        # 실제 service가 저장 포트의 결과를 재조회한다. 완료 결과를 시험에서 사전 주입하지 않는다.
        second=service.ask(replace(self.context,trace_id='trace-s42-zero-reentry'),**request)
        self.assertIs(second,first)
        self.assertIs(repository.persisted,saved)
        self.assertEqual((transport.calls,repository.completed_writes,repository.failed_writes),(1,1,0))
        self.assertEqual(index.searched,[('source-zero-evidence','version-zero-evidence')])
        self.assertEqual((len(egress.authorized),len(egress.transformed),len(transport.sent)),(1,1,1))
        self.assertEqual(phases,['transform','authorize','transport'])
        approved=egress.authorized[0]['provider_payload']
        self.assertTrue(approved)
        self.assertEqual(approved,canonical_json_bytes(transport.sent[0]['payload']))
        self.assertEqual(transport.sent[0]['payload']['messages'][1],{'role':'user','content':self.question})
        self.assertNotIn('max_tokens',transport.sent[0]['payload'])

    def test_nonqa_zero_evidence_cancel_and_invalid_response_never_retry_or_fabricate_failure(self):
        from asyncio import CancelledError
        for outcome,error_type in (('cancelled',CancelledError),('response_invalid',ValueError)):
            with self.subTest(outcome=outcome):
                service,repository,index,transport,egress,phases=self._flow(owner=True,outcome=outcome)
                with self.assertRaises(error_type) as caught:
                    service.ask(self.context,source_id='source-zero-evidence',source_version_id='version-zero-evidence',
                        question=self.question,run_id='run-s42-zero-'+outcome)
                if outcome=='response_invalid':
                    self.assertEqual(str(caught.exception),'TEXT_GENERATION_RESPONSE_INVALID')
                self.assertEqual((transport.calls,repository.completed_writes,repository.failed_writes),(1,0,0))
                self.assertIsNone(repository.completed)
                self.assertIsNone(repository.persisted)
                self.assertEqual(index.searched,[('source-zero-evidence','version-zero-evidence')])
                self.assertEqual((len(egress.authorized),len(egress.transformed),len(transport.sent)),(1,1,1))
                self.assertEqual(phases,['transform','authorize','transport'])
                approved=egress.authorized[0]['provider_payload']
                self.assertTrue(approved)
                self.assertEqual(approved,canonical_json_bytes(transport.sent[0]['payload']))
                self.assertEqual(approved,egress.transformed[0][1])
                self.assertEqual(transport.sent[0]['payload']['messages'][1],{'role':'user','content':self.question})
                self.assertNotIn('max_tokens',transport.sent[0]['payload'])


class S42QuestionFailedServiceTests(unittest.TestCase):
    """실제 service/adapter와 기존 대역 포트의 코드 회귀다. 실제 Provider/PG 증거가 아니다."""

    context = QuestionContext(
        "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-s42-service", "policy-v1", "notebook-cp3",
    )
    question = "선택한 자료의 검증 문장은 무엇인가요?"
    sources = (QuestionInputSource("raw_source", "source-cp3", "source-cp3", "source-version-cp3"),)

    @staticmethod
    def _safe_selection(selection):
        # ResolvedModel 전체/credential/transport 인자를 capture에 보관하지 않는다.
        from daon_user_api.question_answering import TextModelSelection
        return TextModelSelection(
            provider_code=selection.provider_code, base_url=selection.base_url,
            profile_id=selection.profile_id, deployment_id=selection.deployment_id,
            model_id=selection.model_id, binding_version=selection.binding_version,
            provider_kind=selection.provider_kind, connection_id=selection.connection_id,
            credential_version=selection.credential_version, catalog_version=selection.catalog_version,
            routing_owner=selection.routing_owner, daon_fallback_allowed=selection.daon_fallback_allowed,
        )

    def _flow(self, *, fallback=False, owner=True, outcomes=("terminal",), blocked=False, commit_error=False, resolver=None):
        from asyncio import CancelledError
        case = self
        events = []

        class Repository(FakeRepository):
            def __init__(self):
                super().__init__()
                self.failed = []

            def revalidate_open_law_sources(self, context, versions):
                if blocked:
                    raise QuestionRepositoryError("QUESTION_SOURCE_UNAVAILABLE", status=404)

            def persist_failed(self, context, *, run_id, request_fingerprint, request_payload,
                               idempotency_key, context_sources, selection, egress_authorization, question,actual_context_mode=None,
                               resolved_context_sources=None):
                from daon_user_api.question_answering import TextModelSelection
                case.assertIs(type(selection), TextModelSelection, "실패 capture에 credential 보유 객체를 넘기지 않는다")
                case.assertIs(type(context_sources),tuple)
                if resolved_context_sources is not None:
                    case.assertIs(type(resolved_context_sources),tuple)
                events.append("persist_failed")
                self.failed.append({
                    "context": context, "run_id": run_id, "request_fingerprint": request_fingerprint,
                    "request_payload": json.loads(json.dumps(request_payload)), "idempotency_key": idempotency_key,
                    "context_sources": tuple(context_sources), "selection": selection,
                    "egress_authorization": json.loads(json.dumps(egress_authorization)),
                    "question": question,"context_mode":actual_context_mode,
                    "resolved_context_sources":resolved_context_sources,
                })

            def persist_completed(self, context, **kwargs):
                events.append("persist_completed")
                if commit_error:
                    raise QuestionRepositoryError("QUESTION_DATABASE_UNAVAILABLE", status=503, retryable=True)
                return super().persist_completed(context, **{**kwargs, "selection": case._safe_selection(kwargs["selection"])})

        class Egress(FakeEgress):
            def __init__(self):
                self.calls = 0

            def authorize(self, context, **kwargs):
                self.calls += 1
                events.append("authorize:" + str(self.calls))
                return {**super().authorize(context, **kwargs), "provider_owner": owner,
                    "egress_decision_id": "egress-s42-" + str(self.calls),
                    "routing_decision_id": "routing-s42-" + str(self.calls),
                    "frozen_routing_context": {"provider_kind": "external_api", "model_id": kwargs["selection"].model_id}}

        class Transport(FakeTransport):
            def post_json(self, **kwargs):
                # 기존 wire adapter까지 실제 소비한다. 외부 HTTP 호출/자격 인자 보관은 없다.
                index = self.calls
                self.calls += 1
                events.append("transport:" + str(self.calls))
                outcome = outcomes[index]
                if outcome == "terminal":
                    # 실제 adapter의 응답 검증을 통과시키지 못하는 합성 응답이다.
                    return {"choices": [{"message": {"content": "{malformed-response"}}]}
                if outcome == "grounding":
                    return {"choices": [{"message": {"content": json.dumps({
                        "answer": "S42 합성 잘못된 인용", "cited_chunk_ids": ["not-an-evidence-chunk"],
                        "insufficient": False,
                    })}}]}
                if outcome == "arbitrary":
                    raise ValueError("S42 합성 임의 오류")
                if outcome == "temporary":
                    raise ValueError("TEXT_GENERATION_PROVIDER_UNAVAILABLE")
                if outcome == "cancelled":
                    raise CancelledError()
                if outcome == "unexpected":
                    raise RuntimeError("S42 합성 일반 예외")
                case.assertEqual(outcome, "success")
                return {"choices": [{"message": {"content": json.dumps({
                    "answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False,
                })}}]}

        repository, transport, egress = Repository(), Transport(), Egress()
        resolver = resolver or (PersonalCredentialFallbackResolver() if fallback else FakeProviderSettings())
        evidence = (IndexedEvidenceChunk("chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "ORANGE-COMPASS-42", "span-page-2", 1.0),)
        service = QuestionAnsweringService(resolver, repository, FakeIndex(evidence), FakeCredential(),
            transport, egress, concurrent_wait_seconds=0)
        return service, repository, resolver, transport, events

    def _request(self):
        return {"question": self.question, "source_id": "source-cp3",
            "source_version_id": "source-version-cp3", "context_mode": "raw_only"}

    def _ask(self, service, *, run_id="run-s42-terminal", **kwargs):
        return service.ask(self.context, run_id=run_id, context_sources=self.sources, **self._request(), **kwargs)

    def _assert_failed_input(self, repository, *, run_id, key, fingerprint, attempt, personal=False):
        from daon_user_api.question_answering import TextModelSelection
        self.assertEqual(len(repository.failed), 1, "OWNER_TERMINAL_FAILURE_STORAGE_NOT_CALLED")
        saved = repository.failed[0]
        self.assertEqual(saved["question"], self.question)
        self.assertEqual({k: saved[k] for k in ("context", "run_id", "request_fingerprint", "request_payload",
            "idempotency_key", "context_sources")}, {"context": self.context, "run_id": run_id,
            "request_fingerprint": fingerprint, "request_payload": self._request(),
            "idempotency_key": key, "context_sources": self.sources})
        self.assertIs(type(saved["selection"]), TextModelSelection)
        if personal:
            self.assertEqual((saved["selection"].connection_id, saved["selection"].model_id,
                saved["selection"].credential_version, saved["selection"].binding_version),
                ("openrouter-primary", "openai/gpt-4.1", 1, 5))
        else:
            self.assertEqual((saved["selection"].connection_id, saved["selection"].model_id,
                saved["selection"].credential_version, saved["selection"].binding_version),
                ("profile-upstage", "solar-pro4", 1, 5))
        self.assertEqual(saved["egress_authorization"], {
            "provider_owner": True, "egress_decision_id": "egress-s42-" + str(attempt),
            "routing_decision_id": "routing-s42-" + str(attempt),
            "frozen_routing_context": {"provider_kind": "external_api",
                "model_id": "openai/gpt-4.1" if personal else "solar-pro4"},
        })
        self.assertIsNone(repository.persisted)

    def test_http_raw_filtered_failure_carries_original_resolved_and_ordered_ready_subset(self):
        """실제 ready 필터/adapter를 소비한다. 저장·자료 포트 대역이며 실제 PG/Provider 증거가 아니다."""
        from daon_user_api.question_answering import TextModelSelection
        from daon_user_api.question_answering_service import question_request_fingerprint
        case=self
        service,repository,resolver,transport,events=self._flow()
        unavailable=QuestionInputSource('raw_source','source-unavailable-cp3','source-unavailable-cp3','version-unavailable-cp3')
        selected=(unavailable,self.sources[0])
        used=(self.sources[0],)
        payload={'notebook_id':self.context.notebook_id,'question':self.question,'knowledge_context':{
            'mode':'raw_only','resources':[
                {'resource_kind':'source','resource_id':item.source_id,'version_id':item.source_version_id} for item in selected]}}
        run,key='run-s42-http-raw-filtered','s42-http-raw-filtered-original-key'
        fingerprint=question_request_fingerprint(self.context,run_id=run,idempotency_key=key,request_payload=payload)
        ready_reads=[]
        ready_port=repository.load_ready_source
        def current_ready(context,source,version):
            ready_reads.append((context,source,version))
            if (source,version)==(unavailable.source_id,unavailable.source_version_id):
                raise QuestionRepositoryError('QUESTION_SOURCE_UNAVAILABLE',status=404)
            return ready_port(context,source,version)
        repository.load_ready_source=current_ready
        phases=[]
        class GroundedRegistry(QuestionAdapterRegistry):
            def prepare(self,selection,evidence,question,trace_id,transport):
                prepared=super().prepare(selection,evidence,question,trace_id,transport)
                case.assertEqual([(item.source_id,item.source_version_id) for item in evidence],
                    [('source-cp3','source-version-cp3')])
                phases.append('ACTUAL_FILTERED_GROUNDED_PREPARED')
                return prepared
            def generate_prepared(self,prepared):
                try:
                    return super().generate_prepared(prepared)
                except ValueError as error:
                    case.assertEqual(str(error),'TEXT_GENERATION_RESPONSE_INVALID')
                    phases.append('ACTUAL_FILTERED_RESPONSE_INVALID')
                    raise
        service._adapter_registry=GroundedRegistry()
        egress_inputs=[]
        authorize=service._egress.authorize
        def observed_authorize(context,**kwargs):
            egress_inputs.append((kwargs['source_id'],kwargs['source_version_id'],kwargs['context_mode']))
            return authorize(context,**kwargs)
        service._egress.authorize=observed_authorize
        with self.assertRaises(QuestionAnsweringError) as caught:
            service.ask(self.context,source_id=unavailable.source_id,source_version_id=unavailable.source_version_id,
                question=self.question,run_id=run,context_mode='raw_only',context_sources=selected,
                request_fingerprint=fingerprint,request_payload=payload,idempotency_key=key)
        self.assertEqual((caught.exception.code,caught.exception.status,caught.exception.retryable),
            ('TEXT_GENERATION_RESPONSE_INVALID',502,False))
        self.assertEqual(ready_reads[:2],[(self.context,unavailable.source_id,unavailable.source_version_id),
            (self.context,'source-cp3','source-version-cp3')])
        self.assertEqual(sum(version==unavailable.source_version_id for _,_,version in ready_reads),1)
        self.assertTrue(all(context==self.context for context,_,_ in ready_reads))
        self.assertEqual(phases,['ACTUAL_FILTERED_GROUNDED_PREPARED','ACTUAL_FILTERED_RESPONSE_INVALID'])
        self.assertEqual(egress_inputs,[('source-cp3','source-version-cp3','raw_only')])
        self.assertEqual((resolver.calls,transport.calls),(1,1))
        self.assertIsNone(repository.persisted)
        self.assertEqual(len(repository.failed),1,'HTTP_RAW_FILTERED_OWNER_FAILURE_STORAGE_NOT_CALLED')
        saved=repository.failed[0]
        self.assertEqual({key:saved[key] for key in ('context','run_id','request_fingerprint','request_payload','idempotency_key',
            'question','context_mode','resolved_context_sources','context_sources')},
            {'context':self.context,'run_id':run,'request_fingerprint':fingerprint,'request_payload':payload,
                'idempotency_key':key,'question':self.question,'context_mode':'raw_only',
                'resolved_context_sources':selected,'context_sources':used})
        self.assertIs(type(saved['selection']),TextModelSelection)
        self.assertIs(saved['egress_authorization']['provider_owner'],True)
        self.assertEqual(events,['authorize:1','transport:1','persist_failed'])

    def test_http_raw_resources_failure_preserves_original_resolved_and_ready_used_selection(self):
        """실제 service/adapter와 대역 저장 포트의 회귀다. 모두 ready인 원 선택/사용 선택을 별도로 요구한다."""
        from daon_user_api.question_answering import TextModelSelection
        from daon_user_api.question_answering_service import question_request_fingerprint
        case=self
        service,repository,resolver,transport,events=self._flow()
        selected=(QuestionInputSource('raw_source','source-cp3','source-cp3','source-version-cp3'),
            QuestionInputSource('raw_source','source-extra-cp3','source-extra-cp3','source-version-extra-cp3'))
        payload={'notebook_id':self.context.notebook_id,'question':self.question,'knowledge_context':{
            'mode':'raw_only','resources':[
                {'resource_kind':'source','resource_id':'source-cp3','version_id':'source-version-cp3'},
                {'resource_kind':'source','resource_id':'source-extra-cp3','version_id':'source-version-extra-cp3'}]}}
        run,key='run-s42-http-resources','s42-http-resources-original-key'
        fingerprint=question_request_fingerprint(self.context,run_id=run,idempotency_key=key,request_payload=payload)
        ready_reads=[]
        actual_ready_port=repository.load_ready_source
        def observed_ready(context,source,version):
            ready_reads.append((context,source,version))
            return actual_ready_port(context,source,version)
        repository.load_ready_source=observed_ready
        service._document_index=FakeIndex({
            'source-version-cp3':(IndexedEvidenceChunk('chunk-page-2','source-cp3','source-version-cp3',2,
                'ORANGE-COMPASS-42','span-page-2',1.0),),
            'source-version-extra-cp3':(IndexedEvidenceChunk('chunk-extra-page-3','source-extra-cp3',
                'source-version-extra-cp3',3,'S42 두 번째 합성 근거','span-extra-page-3',1.0),)})
        phases=[]

        class GroundedRegistry(QuestionAdapterRegistry):
            def prepare(self,selection,evidence,question,trace_id,transport):
                prepared=super().prepare(selection,evidence,question,trace_id,transport)
                case.assertEqual({(item.source_id,item.source_version_id) for item in evidence},
                    {('source-cp3','source-version-cp3'),('source-extra-cp3','source-version-extra-cp3')})
                phases.append('ACTUAL_RAW_RESOURCES_GROUNDED_PREPARED')
                return prepared
            def generate_prepared(self,prepared):
                try:
                    return super().generate_prepared(prepared)
                except ValueError as error:
                    case.assertEqual(str(error),'TEXT_GENERATION_RESPONSE_INVALID')
                    phases.append('ACTUAL_RAW_RESOURCES_RESPONSE_INVALID')
                    raise

        service._adapter_registry=GroundedRegistry()
        with self.assertRaises(QuestionAnsweringError) as caught:
            service.ask(self.context,source_id='source-cp3',source_version_id='source-version-cp3',
                question=self.question,run_id=run,context_mode='raw_only',context_sources=selected,
                request_fingerprint=fingerprint,request_payload=payload,idempotency_key=key)
        self.assertEqual((caught.exception.code,caught.exception.status,caught.exception.retryable),
            ('TEXT_GENERATION_RESPONSE_INVALID',502,False))
        self.assertEqual(phases,['ACTUAL_RAW_RESOURCES_GROUNDED_PREPARED','ACTUAL_RAW_RESOURCES_RESPONSE_INVALID'])
        self.assertTrue(ready_reads)
        self.assertTrue(all(context==self.context for context,_,_ in ready_reads))
        self.assertEqual({(source,version) for _,source,version in ready_reads},
            {('source-cp3','source-version-cp3'),('source-extra-cp3','source-version-extra-cp3')})
        self.assertEqual((resolver.calls,transport.calls),(1,1))
        self.assertIsNone(repository.persisted)
        self.assertEqual(len(repository.failed),1,'HTTP_RAW_RESOURCES_OWNER_FAILURE_STORAGE_NOT_CALLED')
        saved=repository.failed[0]
        self.assertEqual({k:saved[k] for k in ('context','run_id','request_fingerprint','request_payload','idempotency_key',
            'question','context_mode','resolved_context_sources','context_sources')},
            {'context':self.context,'run_id':run,'request_fingerprint':fingerprint,'request_payload':payload,
                'idempotency_key':key,'question':self.question,'context_mode':'raw_only',
                'resolved_context_sources':selected,'context_sources':selected})
        self.assertIs(type(saved['selection']),TextModelSelection)
        self.assertEqual((saved['selection'].connection_id,saved['selection'].model_id),('profile-upstage','solar-pro4'))
        self.assertEqual(saved['egress_authorization'],{'provider_owner':True,'egress_decision_id':'egress-s42-1',
            'routing_decision_id':'routing-s42-1','frozen_routing_context':{'provider_kind':'external_api','model_id':'solar-pro4'}})
        self.assertEqual(events,['authorize:1','transport:1','persist_failed'])

    def test_general_owner_response_validation_failure_persists_original_input_and_effective_mode(self):
        """실제 general adapter의 malformed 응답 거부를 저장 포트까지 연결한다. 실제 Provider/PG 시험은 아니다."""
        from daon_user_api.question_answering import TextModelSelection
        from daon_user_api.question_answering_service import question_request_fingerprint
        case=self
        service,repository,resolver,transport,events=self._flow()
        phases=[]

        class GeneralRegistry(QuestionAdapterRegistry):
            def prepare_general(self,selection,question,trace_id,transport):
                prepared=super().prepare_general(selection,question,trace_id,transport)
                case.assertIs(type(prepared.request),GeneralConversationRequest)
                phases.append('ACTUAL_GENERAL_PREPARED')
                return prepared
            def generate_general(self,prepared):
                try:
                    return super().generate_general(prepared)
                except ValueError as error:
                    case.assertEqual(str(error),'TEXT_GENERATION_RESPONSE_INVALID')
                    phases.append('ACTUAL_GENERAL_RESPONSE_INVALID')
                    raise

        service._adapter_registry=GeneralRegistry()
        run,key='run-s42-general-terminal','s42-general-original-key'
        question='안녕하세요!'
        payload={'notebook_id':self.context.notebook_id,'question':question}
        fingerprint=question_request_fingerprint(self.context,run_id=run,idempotency_key=key,request_payload=payload)
        with self.assertRaises(QuestionAnsweringError) as caught:
            service.ask(self.context,source_id=None,source_version_id=None,question=question,run_id=run,
                context_mode='general_ungrounded',context_sources=(),request_fingerprint=fingerprint,
                request_payload=payload,idempotency_key=key)
        self.assertEqual((caught.exception.code,caught.exception.status,caught.exception.retryable),
            ('TEXT_GENERATION_RESPONSE_INVALID',502,False))
        self.assertEqual(phases,['ACTUAL_GENERAL_PREPARED','ACTUAL_GENERAL_RESPONSE_INVALID'])
        self.assertEqual(transport.calls,1)
        self.assertEqual(resolver.calls,1)
        self.assertIsNone(repository.persisted)
        self.assertEqual(len(repository.failed),1,'GENERAL_OWNER_TERMINAL_FAILURE_STORAGE_NOT_CALLED')
        saved=repository.failed[0]
        self.assertEqual({k:saved[k] for k in ('context','run_id','request_fingerprint','request_payload','idempotency_key',
            'context_sources','question','context_mode')},{'context':self.context,'run_id':run,'request_fingerprint':fingerprint,
                'request_payload':{'notebook_id':self.context.notebook_id,'question':'안녕하세요!'},'idempotency_key':key,
                'context_sources':(),'question':'안녕하세요!','context_mode':'general_ungrounded'})
        self.assertIs(type(saved['selection']),TextModelSelection)
        self.assertEqual((saved['selection'].connection_id,saved['selection'].model_id,saved['selection'].binding_version),
            ('profile-upstage','solar-pro4',5))
        self.assertEqual(saved['egress_authorization'],{'provider_owner':True,'egress_decision_id':'egress-s42-1',
            'routing_decision_id':'routing-s42-1','frozen_routing_context':{'provider_kind':'external_api','model_id':'solar-pro4'}})
        self.assertEqual(events,['authorize:1','transport:1','persist_failed'])

    def test_owner_terminal_failure_persists_exact_original_input_after_generation(self):
        from daon_user_api.question_answering_service import question_request_fingerprint
        for outcome, code in (("terminal", "TEXT_GENERATION_RESPONSE_INVALID"),
                              ("grounding", "TEXT_GENERATION_GROUNDING_INVALID")):
            with self.subTest(response=outcome):
                service, repository, resolver, transport, events = self._flow(fallback=True, outcomes=(outcome,))
                run = "run-s42-terminal-" + outcome
                fingerprint = question_request_fingerprint(self.context, run_id=run, idempotency_key=run,
                    request_payload=self._request())
                with self.assertRaises(QuestionAnsweringError) as caught:
                    self._ask(service, run_id=run)
                self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.retryable),
                    (code, 502, False))
                self.assertEqual(transport.calls, 1)
                self.assertEqual(resolver.sources, ["system"], "응답 검증 오류로 기존 fallback allowlist를 확대하지 않는다")
                self._assert_failed_input(repository, run_id=run, key=run, fingerprint=fingerprint, attempt=1, personal=True)
                self.assertEqual(events, ["authorize:1", "transport:1", "persist_failed"])

    def test_explicit_internal_logical_payload_and_key_reach_failed_storage_unchanged(self):
        from daon_user_api.question_answering_service import question_request_fingerprint
        service, repository, _, _, events = self._flow()
        run, key = "run-s42-explicit", "s42-original-logical-key"
        payload = self._request()
        fingerprint = question_request_fingerprint(self.context, run_id=run, idempotency_key=key, request_payload=payload)
        with self.assertRaises(QuestionAnsweringError) as caught:
            self._ask(service, run_id=run, request_fingerprint=fingerprint, request_payload=payload, idempotency_key=key)
        self.assertEqual(caught.exception.code, "TEXT_GENERATION_RESPONSE_INVALID")
        self._assert_failed_input(repository, run_id=run, key=key, fingerprint=fingerprint, attempt=1)
        self.assertEqual(payload, self._request())
        self.assertEqual(events, ["authorize:1", "transport:1", "persist_failed"])

    def test_legacy_whitespace_question_is_carried_raw_while_fingerprint_body_stays_stripped(self):
        from unittest.mock import patch
        from daon_user_api.question_answering_service import question_request_fingerprint
        service, repository, _, transport, events = self._flow()
        run = "run-s42-whitespace-question"
        raw = " 선택한 자료의 검증 문장은 무엇인가요? "
        logical = {"question": "선택한 자료의 검증 문장은 무엇인가요?", "source_id": "source-cp3",
            "source_version_id": "source-version-cp3", "context_mode": "raw_only"}
        fingerprint = question_request_fingerprint(self.context, run_id=run, idempotency_key=run, request_payload=logical)
        with patch.object(self, "question", raw):
            with self.assertRaises(QuestionAnsweringError) as caught:
                self._ask(service, run_id=run)
        self.assertEqual((caught.exception.code, caught.exception.status), ("TEXT_GENERATION_RESPONSE_INVALID", 502))
        self.assertEqual(transport.calls, 1)
        self.assertEqual(len(repository.failed), 1)
        saved = repository.failed[0]
        self.assertEqual(saved["question"], raw)
        self.assertEqual(saved["request_payload"], logical)
        self.assertEqual((saved["request_fingerprint"], saved["idempotency_key"], saved["context_sources"]),
            (fingerprint, run, self.sources))
        self.assertEqual(events, ["authorize:1", "transport:1", "persist_failed"])

    def test_explicit_fingerprint_without_original_body_key_never_infers_failed_input(self):
        service, repository, _, transport, events = self._flow()
        with self.assertRaises(QuestionAnsweringError) as caught:
            self._ask(service, run_id="run-s42-fingerprint-only", request_fingerprint="sha256:" + "c" * 64)
        self.assertEqual((caught.exception.code, caught.exception.status), ("TEXT_GENERATION_RESPONSE_INVALID", 502))
        self.assertEqual(repository.failed, [])
        self.assertEqual(transport.calls, 1)
        self.assertEqual(events, ["authorize:1", "transport:1"])

    def test_failed_replay_requires_new_run_without_provider_or_failure_rewrite(self):
        from daon_user_api.question_answering_service import question_request_fingerprint
        service, repository, resolver, transport, events = self._flow()
        run = "run-s42-failed-replay"
        fingerprint = question_request_fingerprint(self.context, run_id=run, idempotency_key=run,
            request_payload=self._request())
        failed_reads = []

        def read_failed(context, run_id, request_fingerprint):
            self.assertIsNone(repository.completed, "정상 completed replay는 기존 경로로 먼저 반환해야 한다")
            failed_reads.append((context, run_id, request_fingerprint))
            return True

        repository.load_failed_for_replay = read_failed
        # 정상 완료 재진입을 보존한다. 이 경우 실패 조회/새 Provider/실패 재쓰기는 모두 없다.
        completed = StoredQuestionAnswer(run, "result-s42-replay", "기존 정상 답변", False, ())
        repository.completed = completed
        self.assertIs(self._ask(service, run_id=run), completed)
        self.assertEqual(failed_reads, [])
        repository.completed = None
        with self.assertRaises(QuestionAnsweringError) as caught:
            self._ask(service, run_id=run)
        self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.retryable),
            ("QUESTION_NEW_RUN_REQUIRED", 409, True))
        self.assertEqual(failed_reads, [(self.context, run, fingerprint)])
        self.assertEqual((resolver.calls, transport.calls), (0, 0))
        self.assertEqual(repository.failed, [])
        self.assertEqual(events, [])

    def test_owner_false_wait_observes_confirmed_failure_without_provider_or_failure_rewrite(self):
        from daon_user_api.question_answering_service import question_request_fingerprint
        service, repository, resolver, transport, events = self._flow(owner=False)
        run = "run-s42-owner-failed-while-waiting"
        fingerprint = question_request_fingerprint(self.context, run_id=run, idempotency_key=run,
            request_payload=self._request())
        failed_reads = []

        def read_failed(context, run_id, request_fingerprint):
            failed_reads.append((context, run_id, request_fingerprint))
            # 최초 replay 때는 미확정이며 egress ownerFalse 대기 경계에서 실패가 확정된다.
            return len(failed_reads) >= 2

        repository.load_failed_for_replay = read_failed
        with self.assertRaises(QuestionAnsweringError) as caught:
            self._ask(service, run_id=run)
        self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.retryable),
            ("QUESTION_NEW_RUN_REQUIRED", 409, True))
        self.assertEqual(failed_reads, [(self.context, run, fingerprint), (self.context, run, fingerprint)])
        self.assertEqual(resolver.calls, 1)
        self.assertEqual(transport.calls, 0)
        self.assertEqual(repository.failed, [])
        self.assertEqual(events, ["authorize:1"])

    def test_fallback_success_never_persists_failed_run(self):
        service, repository, resolver, transport, events = self._flow(fallback=True, outcomes=("arbitrary", "success"))
        answer = self._ask(service)
        self.assertEqual((answer.answer, len(answer.citations)), ("ORANGE-COMPASS-42", 1))
        self.assertEqual(resolver.sources, ["system", "user"])
        self.assertEqual(transport.calls, 2)
        self.assertEqual(repository.failed, [])
        self.assertEqual(events, ["authorize:1", "transport:1", "authorize:2", "transport:2", "persist_completed"])

    def test_exhausted_fallback_persists_only_once_with_final_authorization(self):
        from daon_user_api.question_answering_service import question_request_fingerprint
        service, repository, resolver, transport, events = self._flow(fallback=True, outcomes=("arbitrary", "terminal"))
        run = "run-s42-fallback-terminal"
        fingerprint = question_request_fingerprint(self.context, run_id=run, idempotency_key=run,
            request_payload=self._request())
        with self.assertRaises(QuestionAnsweringError) as caught:
            self._ask(service, run_id=run)
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("TEXT_GENERATION_RESPONSE_INVALID", False))
        self.assertEqual(resolver.sources, ["system", "user"])
        self.assertEqual(transport.calls, 2)
        self._assert_failed_input(repository, run_id=run, key=run, fingerprint=fingerprint, attempt=2, personal=True)
        self.assertEqual(events, ["authorize:1", "transport:1", "authorize:2", "transport:2", "persist_failed"])

    def test_missing_fallback_model_never_promotes_arbitrary_failure_to_terminal_storage(self):
        from daon_user_api.workspace_model_defaults import WorkspaceModelUnavailable

        class MissingPersonalModel(PersonalCredentialFallbackResolver):
            @contextmanager
            def resolve(self, context, capability):
                if context.credential_source == "user":
                    self.sources.append("user")
                    raise WorkspaceModelUnavailable("WORKSPACE_MODEL_DEFAULT_UNAVAILABLE")
                with super().resolve(context, capability) as selection:
                    yield selection

        resolver = MissingPersonalModel()
        service, repository, _, transport, events = self._flow(resolver=resolver, outcomes=("arbitrary",))
        run = "run-s42-fallback-missing"
        with self.assertRaises(QuestionAnsweringError) as caught:
            self._ask(service, run_id=run)
        self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.retryable),
            ("TEXT_GENERATION_FAILED", 502, False))
        self.assertEqual(resolver.sources, ["system", "user"])
        self.assertEqual(transport.calls, 1)
        self.assertEqual(repository.failed, [])
        self.assertIsNone(repository.persisted)
        self.assertEqual(events, ["authorize:1", "transport:1"])

    def test_initial_model_unavailable_has_no_owner_and_never_persists_failed(self):
        from daon_user_api.workspace_model_defaults import WorkspaceModelUnavailable

        class MissingInitialModel(FakeProviderSettings):
            @contextmanager
            def resolve(self, context, capability):
                self.calls += 1
                raise WorkspaceModelUnavailable("WORKSPACE_MODEL_DEFAULT_UNAVAILABLE")
                yield  # pragma: no cover

        resolver = MissingInitialModel()
        service, repository, _, transport, events = self._flow(resolver=resolver)
        with self.assertRaises(QuestionAnsweringError) as caught:
            self._ask(service)
        self.assertEqual((caught.exception.code, caught.exception.status), ("WORKSPACE_MODEL_DEFAULT_UNAVAILABLE", 503))
        self.assertEqual(resolver.calls, 1)
        self.assertEqual(transport.calls, 0)
        self.assertEqual(events, [])
        self.assertEqual(repository.failed, [])
        self.assertIsNone(repository.persisted)

    def test_same_response_error_during_prepare_never_persists_failed(self):
        class PrepareFailureRegistry(QuestionAdapterRegistry):
            def prepare(self, *args, **kwargs):
                super().prepare(*args, **kwargs)
                # 같은 안전 코드라도 생성 응답 검증 전의 실패를 최종 Run 실패로 저장하지 않는다.
                raise ValueError("TEXT_GENERATION_RESPONSE_INVALID")

        service, repository, _, transport, events = self._flow()
        service._adapter_registry = PrepareFailureRegistry()
        with self.assertRaises(QuestionAnsweringError) as caught:
            self._ask(service)
        self.assertEqual((caught.exception.code, caught.exception.status), ("TEXT_GENERATION_RESPONSE_INVALID", 502))
        self.assertEqual(transport.calls, 0)
        self.assertEqual(events, [])
        self.assertEqual(repository.failed, [])
        self.assertIsNone(repository.persisted)

    def test_waiter_transient_cancel_permission_and_result_db_errors_never_persist_failed(self):
        from asyncio import CancelledError
        cases = (
            ("waiter", {"owner": False}, QuestionAnsweringError, "QUESTION_NEW_RUN_REQUIRED", 0),
            ("temporary", {"outcomes": ("temporary",)}, QuestionAnsweringError, "TEXT_GENERATION_PROVIDER_UNAVAILABLE", 1),
            ("arbitrary", {"outcomes": ("arbitrary",)}, QuestionAnsweringError, "TEXT_GENERATION_FAILED", 1),
            ("cancelled", {"outcomes": ("cancelled",)}, CancelledError, None, 1),
            ("permission", {"blocked": True}, QuestionAnsweringError, "QUESTION_SOURCE_UNAVAILABLE", 0),
            ("result_db", {"outcomes": ("success",), "commit_error": True}, QuestionRepositoryError,
                "QUESTION_DATABASE_UNAVAILABLE", 1),
            ("unexpected", {"outcomes": ("unexpected",)}, RuntimeError, None, 1),
        )
        for name, options, error_type, code, calls in cases:
            with self.subTest(case=name):
                service, repository, _, transport, events = self._flow(**options)
                with self.assertRaises(error_type) as caught:
                    self._ask(service, run_id="run-s42-excluded-" + name)
                if code is not None:
                    self.assertEqual(caught.exception.code, code)
                self.assertEqual(transport.calls, calls)
                self.assertEqual(repository.failed, [])
                self.assertIsNone(repository.persisted)
                self.assertNotIn("persist_failed", events)
                if name == "waiter":
                    self.assertEqual((caught.exception.status, caught.exception.retryable), (409, True))
                elif name in {"temporary", "result_db"}:
                    self.assertEqual((caught.exception.status, caught.exception.retryable), (503, True))
                elif name == "permission":
                    self.assertEqual(caught.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
