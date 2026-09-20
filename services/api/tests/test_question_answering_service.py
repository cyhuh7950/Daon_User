from __future__ import annotations

import json
import unittest
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
    def __init__(self, *, provider_code: str = "EOUL_GATEWAY") -> None:
        self.provider_code = provider_code
        self.calls = []

    @contextmanager
    def resolve(self, context, capability):  # type: ignore[no-untyped-def]
        self.calls.append((context.workspace_id, capability))
        model = ResolvedModel(
            connection_id="eoul-primary", provider_code=self.provider_code,
            model_id="assistant-default", capability=capability,
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
        self.assertEqual(transport.calls[0]["payload"]["options"], {"num_predict": 64, "temperature": 0})
        self.assertEqual(transport.calls[0]["payload"]["keep_alive"], "5m")
        self.assertEqual(transport.calls[0]["timeout_seconds"], 90.0)
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


if __name__ == "__main__":
    unittest.main()
