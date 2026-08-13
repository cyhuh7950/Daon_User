from __future__ import annotations

import json
import unittest

from daon_user_api.document_index_postgres import IndexedEvidenceChunk
from daon_user_api.provider_settings import (
    ModelDeploymentView, ProviderProfileView, ProviderSettingsSnapshot,
)
from daon_user_api.question_answering_postgres import (
    QuestionContext, ReadyQuestionSource, StoredCitation, StoredQuestionAnswer,
)
from daon_user_api.question_answering_service import QuestionAdapterRegistry, QuestionAnsweringService


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
