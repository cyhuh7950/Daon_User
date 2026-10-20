import json
from dataclasses import replace

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
from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
from daon_user_api.question_answering_service import (
    QuestionAdapterRegistry,
    QuestionAnsweringError,
)
from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
from daon_user_api.question_answering import TextModelSelection
from daon_user_api.workspace_model_defaults import ResolvedModel


class Policy:
    def get_effective(self, context):  # type: ignore[no-untyped-def]
        del context
        return EffectiveEgressPolicy(
            "op", "ob", "wp", "wb", "allow_approved_external",
            ("external_api",), ("provider.example",), "internal", 4096,
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


def _prepared_wire(provider_code: str, *, grounded: bool = True) -> bytes:
    evidence = (IndexedEvidenceChunk(
        "chunk-1", "source", "source-version", 3, "secret evidence", "span-1",
    ),)
    selection = ResolvedModel(
        "profile", provider_code, "model", "text_generation",
        ("https://omniroute.example/v1" if provider_code == "OMNIROUTE" else
            "https://api.upstage.ai/v1" if provider_code == "UPSTAGE" else "http://ollama:11434"),
        0 if provider_code == "OLLAMA" else 1, 1, 1,
        "local_runtime" if provider_code == "OLLAMA" else "external_api",
        "provider", True,
        None if provider_code == "OLLAMA" else bytearray(b"configured-test-credential"),
    )
    try:
        registry = QuestionAdapterRegistry()
        prepared = (registry.prepare(selection, evidence, "secret question", "trace-1", NoTransport())
            if grounded else registry.prepare_general(selection, "안녕", "trace-1", NoTransport()))
        return canonical_json_bytes(prepared.provider_payload)
    finally:
        selection.release()


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
    assert after_user["question"] == "secret question"
    assert after_user["evidence"][0]["text"] == "secret evidence"
    assert after_user["evidence"][0]["chunk_id"] == "chunk-1"
    assert after_user["evidence"][0]["page"] == 3
    assert after["model"] == before["model"]
    assert after["messages"][0] == before["messages"][0]
    assert {key: value for key, value in after.items() if key != "messages"} == {
        key: value for key, value in before.items() if key != "messages"
    }
    assert b"secret question" in transformed
    assert b"secret evidence" in transformed
    assert transformed == canonical_json_bytes(after)


def test_required_masking_accepts_any_general_question_wire() -> None:
    authorizer = PostgresQuestionEgressAuthorizer.__new__(PostgresQuestionEgressAuthorizer)
    authorizer._policy_service = Policy()
    wire = canonical_json_bytes({
        "model": "model", "messages": [
                {"role": "system", "content": "Answer the user's question naturally and briefly without claiming to use Sources."},
            {"role": "user", "content": "2026년 매출은?"},
        ],
    })
    transformed = json.loads(authorizer.prepare_payload(
        QuestionContext("tenant", "workspace", "actor", "trace", "policy"), wire,
    ))
    assert transformed["messages"][1]["content"] == "2026년 매출은?"


@pytest.mark.parametrize("provider_code", ["OMNIROUTE", "UPSTAGE", "OLLAMA"])
@pytest.mark.parametrize("grounded", [False, True])
@pytest.mark.parametrize("requirements", [(True, True), (True, False), (False, True)])
def test_required_transform_preserves_actual_general_and_grounded_protocol_wire(
    provider_code: str, grounded: bool, requirements, caplog,
) -> None:
    class NoWrites:
        def _transaction(self, *_args):
            raise AssertionError("payload preparation must not write")
    class RequiredPolicy(Policy):
        def get_effective(self, context):
            return replace(super().get_effective(context),
                masking_required=requirements[0], redaction_required=requirements[1])
    authorizer = PostgresQuestionEgressAuthorizer(NoWrites(), RequiredPolicy())
    original = _prepared_wire(provider_code, grounded=grounded)
    transformed = authorizer.prepare_payload(
        QuestionContext("tenant", "workspace", "actor", "trace", "policy"), original,
    )
    field = "input" if provider_code == "OMNIROUTE" else "messages"
    before, after = json.loads(original), json.loads(transformed)
    if grounded:
        before[field][1]["content"] = canonical_json_bytes(json.loads(before[field][1]["content"])).decode("utf-8")
    assert after == before
    assert ("messages" in after, "input" in after) == (field == "messages", field == "input")
    if grounded:
        user = json.loads(after[field][1]["content"])
        assert user["question"] == "secret question"
        assert user["evidence"] == [{"chunk_id": "chunk-1", "page": 3, "text": "secret evidence"}]
    else:
        assert after[field][1]["content"] == "안녕"
    assert transformed == canonical_json_bytes(after)
    assert caplog.text == ""


@pytest.mark.parametrize("field", ["messages", "input"])
@pytest.mark.parametrize("malformed", [
    None, "text", {}, [], [None], ["text"], [{"role": "user", "content": None}],
    [{"role": "user", "content": [{"type": "input_text", "text": "text"}]}],
    [{"role": "user", "content": "text"}, {"role": "user", "content": "other"}],
    [{"role": "system", "content": "text"}],
    [{"role": "user", "content": "text"}, None],
    [{"role": "user", "content": "text"}, {"role": "system", "content": {}}],
    [{"role": "user", "content": "text"}, {"role": None, "content": "text"}],
    [{"role": "user", "content": "text"}, {"role": "unknown", "content": "text"}],
])
def test_required_transform_rejects_malformed_protocol_before_write_or_transport(field, malformed, caplog):
    class NoWrites:
        def _transaction(self, *_args):
            raise AssertionError("invalid payload must not write")
    authorizer = PostgresQuestionEgressAuthorizer(NoWrites(), Policy())
    with pytest.raises(QuestionAnsweringError) as error:
        authorizer.prepare_payload(
            QuestionContext("tenant", "workspace", "actor", "trace", "policy"),
            canonical_json_bytes({"model": "m", field: malformed}),
        )
    assert error.value.code == "EGRESS_TRANSFORMATION_FAILED" and error.value.status == 403
    assert caplog.text == ""


@pytest.mark.parametrize("other", [None, [], [{"role": "user", "content": "other"}]])
@pytest.mark.parametrize("field", ["messages", "input"])
def test_required_transform_rejects_both_wire_protocols(field, other):
    authorizer = PostgresQuestionEgressAuthorizer.__new__(PostgresQuestionEgressAuthorizer)
    authorizer._policy_service = Policy()
    wire = {field: [{"role": "user", "content": "text"}],
        "input" if field == "messages" else "messages": other}
    with pytest.raises(QuestionAnsweringError, match="EGRESS_TRANSFORMATION_FAILED"):
        authorizer.prepare_payload(
            QuestionContext("tenant", "workspace", "actor", "trace", "policy"), canonical_json_bytes(wire),
        )


@pytest.mark.parametrize(
    "malformed",
    [
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
    def __enter__(self):
        self.events.append("enter")
        return self.connection
    def __exit__(self, *_args):
        self.events.append("exit")
        return False


class _Cloud:
    def __init__(self, events, frozen): self.events, self.connection = events, _ConcurrentConnection(events, frozen)
    def _transaction(self, context):
        del context
        return _Transaction(self.connection, self.events)


def test_concurrent_mismatched_canonical_fails_without_retry_audit_write() -> None:
    events: list[str] = []
    authorizer = PostgresQuestionEgressAuthorizer.__new__(PostgresQuestionEgressAuthorizer)
    authorizer._policy_service = Policy()
    authorizer._cloud_store = _Cloud(events, {"different": True})
    with pytest.raises(QuestionAnsweringError, match="IDEMPOTENCY_KEY_REUSED"):
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
            question="안녕하세요!", context_mode="general_ungrounded",
            request_fingerprint="sha256:" + "b" * 64,
        )
    assert events[:4] == ["enter", "lock", "reread", "exit"]
    assert events[4:] == []


def test_frozen_egress_and_run_snapshot_contain_only_safe_exact_model_metadata() -> None:
    selection = ResolvedModel(
        "eoul-primary", "EOUL_GATEWAY", "assistant-default", "text_generation",
        "https://gateway.example.com", 7, 3, 11, "external_api",
        "gateway", False, bytearray(b"data-api-client-key"),
    )
    frozen = dict(PostgresQuestionEgressAuthorizer._safe_model_context(selection))
    run = PostgresQuestionAnsweringRepository._run_canonical_payload(
        question="hello", source_id=None, source_version_id=None,
        context_mode="general_ungrounded", request_fingerprint="sha256:" + "a" * 64,
        selection=selection, frozen_scope=frozen,
    )

    assert frozen == {
        "connection_id": "eoul-primary", "provider_code": "EOUL_GATEWAY",
        "model_id": "assistant-default", "credential_version": 7,
        "default_version": 3, "catalog_version": 11,
        "routing_owner": "gateway", "daon_fallback_allowed": False,
    }
    assert run["connection_id"] == "eoul-primary"
    assert run["credential_version"] == 7
    assert "data-api-client-key" not in repr(frozen)
    assert "data-api-client-key" not in repr(run)
    selection.release()


@pytest.mark.parametrize('required,changed', [(False,None), (True,None), (False,'policy_fingerprint'),
    (False,'provider_payload_fingerprint'), (False,'deployment_id')])
def test_policy_approved_authorization_requires_current_no_step_up_policy_and_exact_fingerprints(required, changed):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import hashlib
    policy = replace(Policy().get_effective(None), question_step_up_required=required)
    writes = []
    class Connection:
        def execute(self, sql, params=()):
            writes.append((sql, params))
            return _Result()
    class Store:
        @contextmanager
        def _transaction(self, context):yield Connection()
    selection = TextModelSelection('UPSTAGE','https://provider.example','profile','deployment','model',1)
    approved = dict(authorization_mode='policy_approved', policy_fingerprint=policy.fingerprint,
        provider_payload_fingerprint='sha256:'+hashlib.sha256(b'{}').hexdigest(),
        provider_kind='external_api', deployment_id='deployment')
    if changed:approved[changed] = 'changed'
    authorizer = PostgresQuestionEgressAuthorizer(Store(), SimpleNamespace(get_effective=lambda ctx:policy))
    args = dict(run_id='run-policy-mode',source_id=None,source_version_id=None,selection=selection,
        provider_payload=b'{}',approved_authorization=approved,question='안녕',context_mode='general_ungrounded',
        request_fingerprint='sha256:'+'b'*64)
    if required or changed:
        with pytest.raises(QuestionAnsweringError,match='EGRESS_POLICY_DENIED'):
            authorizer.authorize(QuestionContext('tenant','workspace','actor','trace','policy'),**args)
    else:
        result = authorizer.authorize(QuestionContext('tenant','workspace','actor','trace','policy'),**args)
        assert result['frozen_routing_context']['authorization_mode'] == 'policy_approved'
        assert result['frozen_routing_context']['question_step_up_required'] is False
        audit = [params[-1].obj for sql, params in writes if "INSERT INTO audit_events" in sql][-1]
        assert audit['authorization_mode'] == 'policy_approved'


def test_approval_fails_closed_when_credential_or_catalog_version_changes() -> None:
    selection = ResolvedModel(
        "eoul-primary", "EOUL_GATEWAY", "assistant-default", "text_generation",
        "https://gateway.example.com", 8, 3, 12, "external_api",
        "gateway", False, bytearray(b"new-key"),
    )
    approved = {
        "policy_fingerprint": "policy-1", "provider_payload_fingerprint": "payload-1",
        "provider_kind": "external_api", "deployment_id": selection.deployment_id,
        "connection_id": "eoul-primary", "credential_version": "7",
        "default_version": "3", "catalog_version": "11",
    }

    assert PostgresQuestionEgressAuthorizer._approval_matches(
        approved, selection, policy_fingerprint="policy-1", payload_fingerprint="payload-1",
    ) is False
    selection.release()
