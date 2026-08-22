from __future__ import annotations

import json
import unittest

from daon_user_api.document_index_postgres import IndexedEvidenceChunk
from daon_user_api.provider_settings import ModelDeploymentView, ProviderProfileView, ProviderSettingsSnapshot
from daon_user_api.question_answering_postgres import (
    QuestionContext,
    QuestionRepositoryError,
    ReadyQuestionSource,
    StoredQuestionAnswer,
)
from daon_user_api.question_answering_service import QuestionAnsweringService, QuestionInputSource


class _ProviderSettings:
    def snapshot(self, context):  # type: ignore[no-untyped-def]
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
            {"text": "deployment-text"}, 1,
        )


class _Repository:
    def __init__(self, unavailable: set[str] = set()) -> None:
        self.unavailable = unavailable
        self.persisted = None

    def load_completed_for_replay(self, context, run_id, request_fingerprint):  # type: ignore[no-untyped-def]
        return None

    def load_completed(self, context, run_id):  # type: ignore[no-untyped-def]
        return None

    def load_ready_source(self, context, source_id, source_version_id):  # type: ignore[no-untyped-def]
        if source_id in self.unavailable:
            raise QuestionRepositoryError("QUESTION_SOURCE_UNAVAILABLE", status=404)
        return ReadyQuestionSource(source_id, source_version_id, "source.pdf")

    def persist_completed(self, context, **kwargs):  # type: ignore[no-untyped-def]
        self.persisted = kwargs
        result = kwargs["result"]
        answer = StoredQuestionAnswer(
            kwargs["run_id"], "result-grounding", result.answer, result.insufficient, (),
        )
        return answer


class _Index:
    def __init__(self, evidence):  # type: ignore[no-untyped-def]
        self.evidence = evidence

    def search(self, context, **kwargs):  # type: ignore[no-untyped-def]
        return self.evidence.get(kwargs["source_version_id"], ())


class _Credentials:
    def resolve(self, provider_code: str) -> str:
        return "server-secret"


class _Transport:
    def __init__(self, answer: str, cited_chunk_ids: list[str] | None = None) -> None:
        self.answer = answer
        self.cited_chunk_ids = cited_chunk_ids or []
        self.calls = 0

    def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        payload = {"answer": self.answer}
        if "evidence" in json.dumps(kwargs["payload"], ensure_ascii=False):
            payload.update({
                "cited_chunk_ids": self.cited_chunk_ids,
                "insufficient": False,
            })
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


class _Egress:
    def authorize(self, context, **kwargs):  # type: ignore[no-untyped-def]
        return {"egress_decision_id": "egress-grounding"}


def _context() -> QuestionContext:
    return QuestionContext("tenant-grounding", "workspace-grounding", "actor-grounding", "trace-grounding", "policy-v1", "notebook-grounding")


class NotebookLmChatGroundingTests(unittest.TestCase):
    def test_mixed_selection_skips_unavailable_source_and_grounds_ready_source(self) -> None:
        evidence = IndexedEvidenceChunk(
            "chunk-ready", "source-ready", "version-ready", 1,
            "사용 가능한 Source 근거", "span-ready", 1.0,
        )
        sources = (
            QuestionInputSource("raw_source", "source-missing", "source-missing", "version-missing"),
            QuestionInputSource("raw_source", "source-ready", "source-ready", "version-ready"),
        )
        repository = _Repository({"source-missing"})
        service = QuestionAnsweringService(
            _ProviderSettings(), repository, _Index({"version-ready": (evidence,)}),
            _Credentials(), _Transport("사용 가능한 근거로 답변합니다.", ["chunk-ready"]), _Egress(),
        )

        answer = service.ask(
            _context(), source_id="source-missing", source_version_id="version-missing",
            question="선택한 자료를 바탕으로 정리해줘", run_id="run-partial-unavailable",
            context_mode="mixed", context_sources=sources,
        )

        self.assertFalse(answer.insufficient)
        self.assertEqual(repository.persisted["context_sources"], (sources[1],))
        self.assertEqual(repository.persisted["context_mode"], "mixed")

    def test_all_unavailable_selection_uses_general_conversation(self) -> None:
        sources = (
            QuestionInputSource("raw_source", "source-missing", "source-missing", "version-missing"),
        )
        repository = _Repository({"source-missing"})
        transport = _Transport("선택한 자료가 없어 일반 상담으로 답변합니다.")
        service = QuestionAnsweringService(
            _ProviderSettings(), repository, _Index({}), _Credentials(), transport, _Egress(),
        )

        answer = service.ask(
            _context(), source_id="source-missing", source_version_id="version-missing",
            question="다음 작업은 어떻게 진행하지?", run_id="run-all-unavailable",
            context_mode="raw_only", context_sources=sources,
        )

        self.assertFalse(answer.insufficient)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(repository.persisted["context_mode"], "general_ungrounded")
        self.assertEqual(repository.persisted["context_sources"], ())
        self.assertIsNone(repository.persisted["source_id"])
        self.assertNotIn("근거가 부족하여 답변할 수 없습니다", answer.answer)


if __name__ == "__main__":
    unittest.main()
