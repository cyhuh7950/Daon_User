import pytest
from types import SimpleNamespace

from daon_user_api.data_canon import canonical_json_bytes
from daon_user_api.document_index_postgres import IndexedEvidenceChunk
from daon_user_api.provider_settings import (
    ModelDeploymentView, ProviderProfileView, ProviderSettingsSnapshot,
)
from daon_user_api.question_answering_postgres import QuestionContext, ReadyQuestionSource
from daon_user_api.question_answering_service import QuestionAnsweringError, QuestionAnsweringService


class Provider:
    def snapshot(self, context):  # type: ignore[no-untyped-def]
        return ProviderSettingsSnapshot(
            context.workspace_id,
            (ProviderProfileView(
                "profile", "UPSTAGE", "external_api", "https://api.upstage.ai/v1",
                True, True, 1,
            ),),
            (ModelDeploymentView(
                "deployment", "profile", "UPSTAGE", "model", ("text",), True, True, 1,
            ),),
            {"text": "deployment"}, 1,
        )


class Repository:
    def __init__(self, events):  # type: ignore[no-untyped-def]
        self.events = events
        self.persisted = None

    def load_completed(self, context, run_id):  # type: ignore[no-untyped-def]
        return None

    def load_completed_for_replay(self, context, run_id, request_fingerprint):  # type: ignore[no-untyped-def]
        return None

    def load_ready_source(self, context, source_id, source_version_id):  # type: ignore[no-untyped-def]
        return ReadyQuestionSource(source_id, source_version_id, "ready.pdf")

    def persist_completed(self, context, **kwargs):  # type: ignore[no-untyped-def]
        self.events.append("result_commit")
        self.persisted = kwargs
        return kwargs


class Index:
    empty = False
    def search(self, context, **kwargs):  # type: ignore[no-untyped-def]
        if self.empty:
            return ()
        return (IndexedEvidenceChunk(
            "chunk", kwargs["source_id"], kwargs["source_version_id"], 1,
            "근거 문장", "span", 1.0,
        ),)


class Egress:
    def __init__(self, events, *, denied=False):  # type: ignore[no-untyped-def]
        self.events = events
        self.denied = denied

    def authorize(self, context, **kwargs):  # type: ignore[no-untyped-def]
        self.events.append("decision_commit")
        expected = b"" if kwargs.get("no_external_payload") else canonical_json_bytes({
            "evidence": [{"chunk_id": "chunk", "page": 1, "text": "근거 문장"}],
            "model": "model", "question": "질문",
        })
        assert kwargs["provider_payload"] == expected
        if self.denied:
            raise QuestionAnsweringError("EGRESS_POLICY_DENIED", status=403)
        return {"egress_decision_id": "egress-1", "routing_decision_id": "routing-1"}


class Adapter:
    def __init__(self, events):  # type: ignore[no-untyped-def]
        self.events = events

    def prepare(self, snapshot, evidence, question, trace_id, credential_resolver, transport):  # type: ignore[no-untyped-def]
        del snapshot, trace_id, credential_resolver, transport
        payload = {"evidence": [
            {"chunk_id": item.chunk_id, "page": item.page, "text": item.text}
            for item in evidence
        ], "model": "model", "question": question}
        return SimpleNamespace(provider_payload=payload)

    def generate_prepared(self, prepared):  # type: ignore[no-untyped-def]
        from daon_user_api.question_answering import GroundedTextResult
        self.events.append("transport")
        return GroundedTextResult("근거 문장", ("chunk",), False, {})


class Credential:
    def resolve(self, provider_code):  # type: ignore[no-untyped-def]
        return "not-used-by-fake-adapter"


class Transport:
    pass


def build(events, *, denied=False):  # type: ignore[no-untyped-def]
    repository = Repository(events)
    return QuestionAnsweringService(
        Provider(), repository, Index(), Credential(), Transport(),
        egress=Egress(events, denied=denied), adapter_registry=Adapter(events),
    ), repository


def test_egress_and_routing_decisions_commit_before_provider_and_result_is_exactly_bound() -> None:
    events = []
    service, repository = build(events)
    service.ask(
        QuestionContext("tenant", "workspace", "actor", "trace", "policy-v1"),
        source_id="source", source_version_id="source-version",
        question="질문", run_id="run-1",
    )

    assert events == ["decision_commit", "transport", "result_commit"]
    assert repository.persisted["egress_authorization"] == {
        "egress_decision_id": "egress-1", "routing_decision_id": "routing-1",
    }


def test_denied_policy_has_zero_provider_transport_and_zero_result_write() -> None:
    events = []
    service, repository = build(events, denied=True)
    with pytest.raises(QuestionAnsweringError, match="EGRESS_POLICY_DENIED"):
        service.ask(
            QuestionContext("tenant", "workspace", "actor", "trace", "policy-v1"),
            source_id="source", source_version_id="source-version",
            question="질문", run_id="run-1",
        )

    assert events == ["decision_commit"]
    assert repository.persisted is None


def test_no_evidence_freezes_policy_decision_without_provider_transport() -> None:
    events = []
    service, repository = build(events)
    service._document_index.empty = True
    service.ask(
        QuestionContext("tenant", "workspace", "actor", "trace", "policy-v1"),
        source_id="source", source_version_id="source-version",
        question="질문", run_id="run-empty",
    )
    assert events == ["decision_commit", "result_commit"]
    assert repository.persisted["provider_called"] is False
    assert repository.persisted["egress_authorization"]["egress_decision_id"] == "egress-1"
