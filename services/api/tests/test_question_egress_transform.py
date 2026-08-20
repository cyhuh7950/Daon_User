import json

import pytest

from daon_user_api.data_canon import canonical_json_bytes
from daon_user_api.document_index_postgres import IndexedEvidenceChunk
from daon_user_api.egress_policy import EffectiveEgressPolicy
from daon_user_api.provider_settings import (
    ModelDeploymentView,
    ProviderProfileView,
    ProviderSettingsSnapshot,
)
from daon_user_api.question_answering_postgres import QuestionContext
from daon_user_api.question_answering_service import (
    QuestionAdapterRegistry,
    QuestionAnsweringError,
)
from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
from daon_user_api.question_answering import TextModelSelection


class Policy:
    def get_effective(self, context):  # type: ignore[no-untyped-def]
        del context
        return EffectiveEgressPolicy(
            "op", "ob", "wp", "wb", "allow_approved_external",
            ("external_api",), ("provider.example",), "restricted", 4096,
            True, True, "organization_admin", False, "sha256:" + "a" * 64,
            '"effective"', '"organization"', '"workspace"', {}, {},
        )


class CredentialResolver:
    def resolve(self, provider_code: str) -> str:
        assert provider_code == "UPSTAGE"
        return "configured-test-credential"


class NoTransport:
    def post_json(self, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("transport must not run while preparing payload")

    def post_json_no_auth(self, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("transport must not run while preparing payload")


def _snapshot(provider_code: str) -> ProviderSettingsSnapshot:
    provider_kind = "external_api" if provider_code == "UPSTAGE" else "local_runtime"
    base_url = "https://api.upstage.ai/v1" if provider_code == "UPSTAGE" else "http://ollama:11434"
    return ProviderSettingsSnapshot(
        "workspace",
        (ProviderProfileView("profile", provider_code, provider_kind, base_url, True, True, 1),),
        (ModelDeploymentView("deployment", "profile", provider_code, "model", ("text",), True, True, 1),),
        {"text": "deployment"},
        1,
    )


def _prepared_wire(provider_code: str) -> bytes:
    evidence = (IndexedEvidenceChunk(
        "chunk-1", "source", "source-version", 3, "secret evidence", "span-1",
    ),)
    prepared = QuestionAdapterRegistry().prepare(
        _snapshot(provider_code), evidence, "secret question", "trace-1",
        CredentialResolver(), NoTransport(),
    )
    return canonical_json_bytes(prepared.provider_payload)


@pytest.mark.parametrize("provider_code", ["UPSTAGE", "OLLAMA"])
def test_required_masking_and_redaction_transform_actual_adapter_wire(provider_code: str) -> None:
    authorizer = PostgresQuestionEgressAuthorizer.__new__(PostgresQuestionEgressAuthorizer)
    authorizer._policy_service = Policy()
    original = _prepared_wire(provider_code)

    transformed = authorizer.prepare_payload(
        QuestionContext("tenant", "workspace", "actor", "trace", "policy"), original,
    )

    before = json.loads(original)
    after = json.loads(transformed)
    before_user = json.loads(before["messages"][1]["content"])
    after_user = json.loads(after["messages"][1]["content"])
    assert before_user["question"] == "secret question"
    assert after_user["question"] == "[MASKED]"
    assert after_user["evidence"][0]["text"] == "[MASKED]"
    assert after_user["evidence"][0]["chunk_id"] == "chunk-1"
    assert after_user["evidence"][0]["page"] == 3
    assert after["model"] == before["model"]
    assert after["messages"][0] == before["messages"][0]
    assert {key: value for key, value in after.items() if key != "messages"} == {
        key: value for key, value in before.items() if key != "messages"
    }
    assert b"secret question" not in transformed
    assert b"secret evidence" not in transformed
    assert transformed == canonical_json_bytes(after)


def test_required_masking_accepts_only_narrow_general_conversation_wire() -> None:
    authorizer = PostgresQuestionEgressAuthorizer.__new__(PostgresQuestionEgressAuthorizer)
    authorizer._policy_service = Policy()
    wire = canonical_json_bytes({
        "model": "model", "messages": [
            {"role": "system", "content": "safe"},
            {"role": "user", "content": "안녕하세요!"},
        ],
    })
    transformed = json.loads(authorizer.prepare_payload(
        QuestionContext("tenant", "workspace", "actor", "trace", "policy"), wire,
    ))
    assert transformed["messages"][1]["content"] == "[MASKED]"


@pytest.mark.parametrize(
    "malformed",
    [
        {"model": "m", "messages": [{"role": "user", "content": "not-json"}]},
        {"model": "m", "messages": [{"role": "user", "content": "{}"}]},
        {"model": "m", "messages": [{"role": "system", "content": "safe"}]},
    ],
)
def test_required_transform_rejects_unrecognized_wire_before_transport(malformed: dict[str, object]) -> None:
    authorizer = PostgresQuestionEgressAuthorizer.__new__(PostgresQuestionEgressAuthorizer)
    authorizer._policy_service = Policy()
    with pytest.raises(QuestionAnsweringError, match="EGRESS_TRANSFORMATION_FAILED"):
        authorizer.prepare_payload(
            QuestionContext("tenant", "workspace", "actor", "trace", "policy"),
            canonical_json_bytes(malformed),
        )


class _Result:
    def __init__(self, row=None): self.row = row
    def fetchone(self): return self.row


class _ConcurrentConnection:
    def __init__(self, events: list[str], frozen: dict[str, object]):
        self.events, self.frozen = events, frozen
    def execute(self, sql, params=()):
        del params
        if "pg_advisory_xact_lock" in sql:
            self.events.append("lock")
            return _Result()
        if "FROM runs WHERE" in sql:
            self.events.append("reread")
            return _Result((self.frozen,))
        if "question.egress.retry_denied" in sql:
            self.events.append("audit")
            return _Result()
        return _Result()


class _Transaction:
    def __init__(self, connection, events): self.connection, self.events = connection, events
    def __enter__(self): self.events.append("enter"); return self.connection
    def __exit__(self, *_args): self.events.append("exit"); return False


class _Cloud:
    def __init__(self, events, frozen): self.events, self.connection = events, _ConcurrentConnection(events, frozen)
    def _transaction(self, context):
        del context
        return _Transaction(self.connection, self.events)


def test_concurrent_retry_locks_then_rereads_and_audits_only_after_transaction_exit() -> None:
    events: list[str] = []
    authorizer = PostgresQuestionEgressAuthorizer.__new__(PostgresQuestionEgressAuthorizer)
    authorizer._policy_service = Policy()
    authorizer._cloud_store = _Cloud(events, {"different": True})
    with pytest.raises(QuestionAnsweringError, match="QUESTION_NEW_RUN_REQUIRED"):
        authorizer.authorize(
            QuestionContext("tenant", "workspace", "actor", "trace", "policy"),
            run_id="run-1", source_id="source", source_version_id="version",
            selection=TextModelSelection(
                "UPSTAGE", "https://provider.example", "profile", "deployment", "model", 1,
            ),
            provider_payload=b"{}",
            approved_authorization={
                "policy_fingerprint": "sha256:" + "a" * 64,
                "provider_payload_fingerprint": "sha256:" + __import__("hashlib").sha256(b"{}").hexdigest(),
                "provider_kind": "external_api", "deployment_id": "deployment",
            },
        )
    assert events[:4] == ["enter", "lock", "reread", "exit"]
    assert events[4:] == ["enter", "audit", "exit"]
