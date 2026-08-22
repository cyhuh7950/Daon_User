from __future__ import annotations

import json
import unittest

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


class FakeProviderSettings:
    def __init__(self) -> None:
        self.calls = 0

    def snapshot(self, context):  # type: ignore[no-untyped-def]
        self.calls += 1
        return ProviderSettingsSnapshot(
            context.workspace_id,
            (ProviderProfileView(
                "profile-upstage", "UPSTAGE", "external_api",
                "https://api.upstage.ai/v1", True, True, 1,
            ),),
            (ModelDeploymentView(
                "deployment-text", "profile-upstage", "UPSTAGE", "solar-pro4",
                ("text",), True, True, 1,
            ),),
            {"text": "deployment-text"}, 5,
        )


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


class FakeEgress:
    def authorize(self, context, **kwargs):  # type: ignore[no-untyped-def]
        return {"egress_decision_id": "egress-test", "routing_decision_id": "routing-test"}


class QuestionAnsweringServiceTests(unittest.TestCase):
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
        credential = FakeCredential()
        for provider_code, base_url in (
            ("GROQ", "https://api.groq.com/openai/v1"),
            ("MISTRAL", "https://api.mistral.ai/v1"),
            ("UPSTAGE", "https://api.upstage.ai/v1"),
        ):
            snapshot = ProviderSettingsSnapshot(
                "workspace-cp3",
                (ProviderProfileView(
                    f"profile-{provider_code.lower()}", provider_code, "external_api",
                    base_url, True, True, 1,
                ),),
                (ModelDeploymentView(
                    "deployment-text", f"profile-{provider_code.lower()}", provider_code,
                    "selected-model", ("text",), True, True, 1,
                ),),
                {"text": "deployment-text"}, 1,
            )
            prepared = registry.prepare(
                snapshot, evidence, "phrase?", "trace-cp3", credential, FakeTransport(),
            )
            self.assertEqual(prepared.selection.provider_code, provider_code)

    def test_ollama_provider_uses_internal_adapter_without_external_credential(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-local", "source-cp3", "source-version-cp3", 1,
            "정책 보존 기간은 30일입니다.", "span-local", 1.0,
        ),)
        snapshot = ProviderSettingsSnapshot(
            "workspace-cp3",
            (ProviderProfileView(
                "profile-ollama", "OLLAMA", "server_internal",
                "http://ollama.internal:11434", True, True, 1,
            ),),
            (ModelDeploymentView(
                "deployment-local", "profile-ollama", "OLLAMA", "llama3.2:3b",
                ("text",), True, True, 1,
            ),),
            {"text": "deployment-local"}, 1,
        )

        class LocalProviderSettings:
            def snapshot(self, context):  # type: ignore[no-untyped-def]
                return snapshot

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

    def test_no_retrieved_evidence_returns_insufficient_without_provider_call(self) -> None:
        provider, repository, transport = FakeProviderSettings(), FakeRepository(), FakeTransport()
        service = QuestionAnsweringService(
            provider, repository, FakeIndex(()), FakeCredential(), transport, FakeEgress(),
        )

        answer = service.ask(
            QuestionContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1"),
            source_id="source-cp3", source_version_id="source-version-cp3",
            question="unsupported?", run_id="run-cp3",
        )

        self.assertTrue(answer.insufficient)
        self.assertEqual(answer.citations, ())
        self.assertEqual(transport.calls, 0)
        self.assertFalse(repository.persisted["provider_called"])


if __name__ == "__main__":
    unittest.main()
