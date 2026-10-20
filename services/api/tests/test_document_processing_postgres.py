from __future__ import annotations

import hashlib
import copy
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from test_workspace_model_defaults import readiness_metadata, ReadinessMetadataDB, internal_cost_settings

from daon_user_api.document_processing import DocumentProcessingContext, DocumentProcessingService
from daon_user_api.cloud_storage import CloudDatabaseError
from daon_user_api.object_queue import ObjectStorageError
from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingResult,
    DocumentUnderstandingError,
    ParserValidation,
    SemanticUnderstanding,
)


PDF = b"%PDF-1.4\n% postgres processing fixture\n%%EOF\n"
OBJECT_ID = "a" * 32
OBJECT_KEY = f"tenant-cp3/workspace-cp3/source/{OBJECT_ID}"


@pytest.fixture
def readiness_execution(readiness_metadata, monkeypatch):
    from dataclasses import asdict, replace
    from test_document_readiness_postgres import with_synthetic_cost
    from daon_user_api.document_readiness import ReadinessDispatcherSettings
    f = readiness_metadata
    settings, _ = internal_cost_settings(f, monkeypatch)
    snapshot = f.read(f.db, f.context, f.wait, settings=settings)
    body = json.loads(snapshot.frozen_context_json)
    proof = {"version": 1, "mode": "auto", "mode_origin": "request",
             "frozen_context": body, "frozen_context_digest": snapshot.eligibility.frozen_context_digest,
             "wait_processing_run_id": "run-a"}
    row = {"trigger_type": "readiness_event", "actor_id": "actor-a", "source_version_id": "version-a",
           "state": "vision_llm_understanding", "source_state": "processing", "latest": True,
           "payload": {"source_version_id": "version-a", "notebook_id": "notebook-a",
               "trigger_event_id": "rdy-" + "1" * 32, "readiness_context": proof}}
    wait = asdict(f.wait)
    for key in ("failed_at", "not_before"):
        wait[key] = wait[key].isoformat()
    receipt = {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "actor_id": "actor-a",
        "notebook_id": "notebook-a", "source_id": "source-a", "source_version_id": "version-a",
        "processing_run_id": "run-a", "capability_slot": "text_generation", "event_id": "rdy-" + "1" * 32,
        "frozen_context_digest": proof["frozen_context_digest"], "phase": "final", "outcome": "enqueued",
        "safe_code": "READY_REPROCESS_ALLOWED", "new_run_id": "auto-run-a",
        "received_at": "2026-10-19T10:00:00+00:00"}
    original = f.db.execute
    calls, failures = [], []
    def execute(sql, params=()):
        calls.append(sql)
        if "/* readiness-execution-run */" in sql:
            assert tuple(params) == ("tenant-a", "workspace-a", "auto-run-a", "source-a")
            return Cursor((copy.deepcopy(row),))
        if "public.read_document_role_wait(" in sql:
            return Cursor((copy.deepcopy(wait),))
        if "public.read_document_reprocess_history(" in sql:
            return SimpleNamespace(fetchall=lambda: [(copy.deepcopy(receipt),)])
        return original(sql, params)
    f.db.execute = execute
    class Store:
        @contextmanager
        def _transaction(self, context):
            yield f.db
    repo = PostgresDocumentProcessingRepository(Store(), object(), readiness_settings=settings)
    repo._fail_readiness_guard = lambda *args, **kwargs: failures.append((args, kwargs))
    monkeypatch.setattr(f.module, "read_document_readiness_policy", lambda connection, context, wait, **kwargs:
        f.read(connection, context, wait, **kwargs))
    context = DocumentProcessingContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a")
    return SimpleNamespace(repo=repo, row=row, wait=wait, receipt=receipt, snapshot=snapshot,
        context=context, calls=calls, failures=failures, auth=f, replace=replace)


def test_readiness_execution_requires_current_scope_wait_receipt_and_frozen_body(readiness_execution):
    f = readiness_execution
    assert f.repo.authorize_readiness_run(f.context, "source-a", "auto-run-a") is True
    assert f.failures == []
    assert all(sql.lstrip().startswith("SELECT ") for sql in f.calls)


def test_internal_cost_worker_reassembled_without_policy_rejects_before_model(readiness_execution):
    f = readiness_execution
    assert f.repo.authorize_readiness_run(f.context, "source-a", "auto-run-a") is True
    # 정책은 시작 시 고정된다. 정상 재조립으로 회수된 settings를 사용하는 실행 경계다.
    f.repo._readiness_settings = f.replace(f.repo._readiness_settings, internal_cost_policies=())
    model_calls = []
    with pytest.raises(DocumentUnderstandingError):
        f.repo.authorize_readiness_run(f.context, "source-a", "auto-run-a")
        model_calls.append(True)
    assert model_calls == []


@pytest.mark.parametrize("invalid", ["scope", "actor", "wait_actor", "receipt_actor", "receipt_run",
    "receipt_event", "digest", "mode", "wait_ref", "latest", "terminal", "cost_unknown", "denied", "policy_changed"])
def test_readiness_execution_denies_unbound_or_changed_context_without_model_use(readiness_execution, monkeypatch, invalid):
    from daon_user_api.document_readiness import ReadinessDispatcherSettings
    from test_document_readiness_postgres import with_synthetic_cost
    f = readiness_execution
    if invalid == "scope": f.repo._readiness_settings = ReadinessDispatcherSettings()
    if invalid == "actor": f.row["actor_id"] = "another-actor"
    if invalid == "wait_actor": f.wait["actor_id"] = "another-actor"
    if invalid == "receipt_actor": f.receipt["actor_id"] = "another-actor"
    if invalid == "receipt_run": f.receipt["new_run_id"] = "another-run"
    if invalid == "receipt_event": f.receipt["event_id"] = "rdy-" + "2" * 32
    if invalid == "digest": f.row["payload"]["readiness_context"]["frozen_context_digest"] = "f" * 64
    if invalid == "mode": f.row["payload"]["readiness_context"]["mode"] = "pinned"
    if invalid == "wait_ref": f.row["payload"]["readiness_context"]["wait_processing_run_id"] = "other-wait"
    if invalid == "latest": f.row["latest"] = False
    if invalid == "terminal": f.row["state"] = "completed"
    if invalid == "cost_unknown":
        monkeypatch.setattr(f.auth.module, "read_document_readiness_policy", lambda *args, **kwargs:
                            with_synthetic_cost(f.snapshot, "unknown"))
    if invalid == "denied":
        monkeypatch.setattr(f.auth.module, "read_document_readiness_policy", lambda *args, **kwargs:
            f.replace(f.snapshot, eligibility=f.replace(f.snapshot.eligibility, authorized=False)))
    if invalid == "policy_changed":
        body = json.loads(f.snapshot.frozen_context_json)
        body["workspace_policy"]["cost_limit_cents"] += 1
        from test_document_readiness_postgres import synthetic_snapshot
        changed = synthetic_snapshot(f.snapshot.eligibility, mode=f.auth.wait.mode, body=body)
        monkeypatch.setattr(f.auth.module, "read_document_readiness_policy", lambda *args, **kwargs: changed)
    with pytest.raises(DocumentUnderstandingError):
        f.repo.authorize_readiness_run(f.context, "source-a", "auto-run-a")
    if invalid in {"actor", "latest", "terminal"}:
        assert f.failures == []
    else:
        assert len(f.failures) == 1  # 소유한 현재 Run만 기존 실패 정리. 별도 job은 worker 소유.


def test_manual_processing_does_not_require_new_readiness_ledger(readiness_execution):
    f = readiness_execution
    f.row["trigger_type"] = "manual_retry"
    f.repo._readiness_settings = __import__("daon_user_api.document_readiness", fromlist=["ReadinessDispatcherSettings"]).ReadinessDispatcherSettings()
    assert f.repo.authorize_readiness_run(f.context, "source-a", "auto-run-a") is False
    assert len(f.calls) == 1 and "readiness-execution-run" in f.calls[0]


@pytest.mark.parametrize("changed", ["connection", "model", "kind", "endpoint", "credential_version", "permission"])
def test_readiness_selected_model_must_keep_binding_and_current_permission(readiness_execution, changed):
    f = readiness_execution
    binding = f.row["payload"]["readiness_context"]["frozen_context"]["binding"]
    model = SimpleNamespace(connection_id=binding["connection_id"], model_id=binding["model_id"],
        provider_code=binding["provider_code"], provider_kind=binding["provider_kind"], base_url=binding["endpoint"],
        credential_version=binding["credential_reference"]["version"])
    if changed == "connection": model.connection_id = "other-connection"
    if changed == "model": model.model_id = "other-model"
    if changed == "kind": model.provider_kind = "external_api"
    if changed == "endpoint": model.base_url = "https://other.invalid"
    if changed == "credential_version": model.credential_version += 1
    if changed == "permission": f.auth.db.rules = [("workspace", "local_internal_llm", "deny", 0)]
    with pytest.raises(DocumentUnderstandingError):
        f.repo.authorize_readiness_run(f.context, "source-a", "auto-run-a", model=model, capability="text_generation")


def test_readiness_selected_model_matching_current_binding_passes(readiness_execution):
    f = readiness_execution
    binding = f.row["payload"]["readiness_context"]["frozen_context"]["binding"]
    model = SimpleNamespace(connection_id=binding["connection_id"], model_id=binding["model_id"],
        provider_code=binding["provider_code"], provider_kind=binding["provider_kind"], base_url=binding["endpoint"],
        credential_version=binding["credential_reference"]["version"])
    assert f.repo.authorize_readiness_run(f.context, "source-a", "auto-run-a", model=model, capability="text_generation")


@pytest.mark.parametrize("waiting_role", ["speech_to_text", "text_generation"])
@pytest.mark.parametrize("changed", ["disabled", "connection", "model", "endpoint", "credential_version"])
def test_readiness_other_audio_role_is_current_before_send(readiness_execution, monkeypatch, waiting_role, changed):
    from dataclasses import asdict, replace
    from test_document_readiness_postgres import with_synthetic_cost
    f = readiness_execution
    monkeypatch.setenv("DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID", "workspace-a")
    role_dbs = {role: ReadinessMetadataDB() for role in ("speech_to_text", "text_generation")}
    asr = role_dbs["speech_to_text"]
    asr.binding.update(connection_id="asr-a", provider_code="CUSTOM", adapter_type="asr_base_native",
        base_url="http://asr-base:8080", model_id="faster-whisper-base")
    asr.catalog[0].update(model_id="faster-whisper-base", effective_capabilities=["speech_to_text"])
    settings_by_role = {role: internal_cost_settings(SimpleNamespace(db=db, wait=replace(f.auth.wait, capability=role)), monkeypatch)[0]
                       for role, db in role_dbs.items()}
    settings = replace(f.repo._readiness_settings, internal_cost_policies=tuple(
        p for s in settings_by_role.values() for p in s.internal_cost_policies))
    f.repo._readiness_settings = settings
    def current(connection, context, wait, **kwargs):
        # 실제 B1 metadata/권한/후보 및 승인된 내부 정책 supplier. SQL은 대역이다.
        return f.auth.read(role_dbs[wait.capability], context, wait, settings=kwargs.get("settings", settings))
    wait = replace(f.auth.wait, capability=waiting_role,
                   dependency_connection_id=role_dbs[waiting_role].binding["connection_id"])
    snapshot = current(None, f.context, wait)
    f.wait.clear()
    f.wait.update(asdict(wait))
    for key in ("failed_at", "not_before"):
        f.wait[key] = f.wait[key].isoformat()
    proof = f.row["payload"]["readiness_context"]
    proof.update(frozen_context=json.loads(snapshot.frozen_context_json),
                 frozen_context_digest=snapshot.eligibility.frozen_context_digest)
    f.receipt.update(capability_slot=waiting_role, frozen_context_digest=snapshot.eligibility.frozen_context_digest)
    monkeypatch.setattr(f.auth.module, "read_document_readiness_policy", current)
    actual_role = "text_generation" if waiting_role == "speech_to_text" else "speech_to_text"
    selected = json.loads(current(None, f.context, replace(wait, capability=actual_role)).frozen_context_json)["binding"]
    model = SimpleNamespace(connection_id=selected["connection_id"], model_id=selected["model_id"],
        provider_code=selected["provider_code"], provider_kind=selected["provider_kind"], base_url=selected["endpoint"],
        credential_version=selected["credential_reference"]["version"])
    assert f.repo.authorize_readiness_run(f.context, "source-a", "auto-run-a", model=model, capability=actual_role)
    db = role_dbs[actual_role]
    if changed == "disabled": db.binding["enabled"] = False
    if changed == "connection": db.binding["connection_id"] = "changed-connection"
    if changed == "model": db.binding["model_id"] = "changed-model"
    if changed == "endpoint": db.binding["base_url"] = "http://changed-internal:11434"
    if changed == "credential_version": db.binding["credential_version"] += 1
    model_calls = 0
    with pytest.raises(DocumentUnderstandingError):
        f.repo.authorize_readiness_run(f.context, "source-a", "auto-run-a", model=model, capability=actual_role)
        model_calls += 1
    assert model_calls == 0
    assert proof["frozen_context"]["role"] == waiting_role  # 실패 역할/과거 본문을 다른 역할로 바꾸지 않음.


class FailureWaitConnection(ReadinessMetadataDB):
    """실제 repository/reader/record_wait의 SQL·Tx 대역. 실PG 권한 증거가 아니다."""
    def __init__(self):
        super().__init__()
        self.states = {"ProcessingRun": "vision_llm_understanding", "Source": "processing"}
        self.versions = {"ProcessingRun": 2, "Source": 3}
        self.payload = {"source_version_id": "version-a", "notebook_id": "notebook-a",
                        "readiness_context": {"version": 1, "mode": "auto", "mode_origin": "request"}}
        self.latest_run = "run-a"
        self.source_versions = [("version-a", 1)]
        self.publish_version_on_lock = False
        self.other_runs = {}
        self.actor = "actor-a"
        self.waits, self.notices, self.steps = [], [], []
        self.write_error = None
        self.now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)

    @contextmanager
    def transaction(self):
        snapshot = copy.deepcopy((self.states, self.versions, self.waits, self.notices))
        self.steps.append("savepoint")
        try:
            yield self
        except BaseException:
            self.states, self.versions, self.waits, self.notices = snapshot
            self.steps.append("rollback")
            raise
        else:
            self.steps.append("commit")

    def execute(self, sql, params=()):
        from daon_user_api.data_canon import transition_allowed
        self.steps.append((sql, tuple(params)))
        assert "FOR UPDATE" not in sql and "FOR SHARE" not in sql, "app 직접 rowlock 금지"
        if "/* readiness-execution-run */" in sql:
            return Cursor(({"trigger_type": "readiness_event", "actor_id": self.actor,
                "source_version_id": "version-a", "state": self.states["ProcessingRun"],
                "source_state": self.states["Source"], "latest": True, "payload": copy.deepcopy(self.payload)},))
        if "/* readiness-failure-parent */" in sql:
            assert tuple(params) == ("tenant-a", "workspace-a", "run-a", "actor-a")
            assert "pr.created_by=%s" in sql and "ORDER BY p.created_at DESC,p.record_id DESC" in sql
            return Cursor((self.states["ProcessingRun"], self.versions["ProcessingRun"], "source-a",
                self.states["Source"], self.versions["Source"], copy.deepcopy(self.payload), self.actor,
                "version-a", self.latest_run, not any(version > 1 for _, version in self.source_versions)))
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            if self.publish_version_on_lock:
                self.source_versions.append(("version-b", 2))
                self.publish_version_on_lock = False
            return Cursor()
        if "transition_canon_state" in sql:
            entity, _, version, target = params[:4]
            assert version == self.versions[entity]
            assert transition_allowed(entity, self.states[entity], target), (entity, self.states[entity], target)
            self.states[entity] = target
            self.versions[entity] += 1
            return Cursor((target, self.versions[entity], "succeeded", None))
        if sql == "SELECT clock_timestamp()":
            return Cursor((self.now,))
        if "/* readiness-parent */" in sql:
            return Cursor(({"state": self.states["Source"], "version": self.versions["Source"],
                "source_version_id": "version-a", "run_id": self.latest_run, "run_state": self.states["ProcessingRun"],
                "canonical_json": copy.deepcopy(self.payload), "modality": "document"},))
        if sql == "SELECT public.record_document_role_wait(%s)":
            if self.write_error:
                raise self.write_error
            value = copy.deepcopy(params[0].obj)
            assert self.states == {"ProcessingRun": "failed", "Source": "waiting_model"}
            assert value["processing_run_id"] == self.latest_run and value["actor_id"] == self.actor
            proof = self.payload.get("readiness_context", {"mode": "legacy_unknown", "mode_origin": "legacy_unknown"})
            assert value["mode"] == {"mode": proof["mode"], "origin": proof["mode_origin"]}
            assert value["frozen_context_digest"] != "0" * 64
            self.waits.append(value)
            return Cursor((value,))
        return super().execute(sql, params)


@pytest.fixture
def failure_wait(readiness_metadata, monkeypatch):
    import daon_user_api.document_readiness_postgres as readiness
    import daon_user_api.open_law_source_access as law
    import daon_user_api.document_index_postgres as index
    import daon_user_api.notification_source_access as special
    import daon_user_api.notification_events as events
    from daon_user_api.document_readiness import ReadinessDispatcherSettings
    from daon_user_api.document_understanding_adapter import DocumentFailureEvidence
    connection = FailureWaitConnection()
    class Store:
        _notification_inbox_enabled = True
        @contextmanager
        def _transaction(self, context):
            with connection.transaction():
                yield connection
    store = Store()
    for name, value in (("_current_auth_grant", law._current_auth_grant),
                         ("_source_access_allowed", index._source_access_allowed),
                         ("_special_source_access_allowed", special._special_source_access_allowed)):
        monkeypatch.setattr(readiness, name, value)
    def notify(db, context, *, origin_event_id):
        assert db is connection
        connection.notices.append(origin_event_id)
    monkeypatch.setattr(events, "enqueue_completed_event", notify)
    settings = ReadinessDispatcherSettings()
    port = readiness.PostgresDocumentReadinessRepository(store, settings=settings)
    repository = PostgresDocumentProcessingRepository(store, object(), readiness_port=port)
    context = DocumentProcessingContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a")
    evidence = DocumentFailureEvidence("text_generation", "connection-a", "role_runtime")
    return SimpleNamespace(db=connection, repo=repository, context=context, evidence=evidence,
                           port=port, store=store, auth=readiness_metadata)


def test_failure_wait_same_tx_reader_digest_locks_and_notification(failure_wait):
    f = failure_wait
    f.repo.fail_with_readiness(f.context, "run-a", "UNDERSTANDING_PROVIDER_UNAVAILABLE", retryable=False,
                               failure_evidence=f.evidence)
    assert f.db.states == {"ProcessingRun": "failed", "Source": "waiting_model"}
    assert len(f.db.waits) == len(f.db.notices) == 1
    wait = f.db.waits[0]
    assert (wait["capability"], wait["dependency_connection_id"], wait["failure_kind"]) == (
        "text_generation", "connection-a", "role_runtime")
    assert wait["failed_at"] == "2026-10-19T12:00:00+00:00"
    assert wait["not_before"] == "2026-10-19T12:01:00+00:00"
    assert any("readiness-metadata-catalog" in sql for sql, _ in f.db.calls)
    assert set(wait) == {"tenant_id", "workspace_id", "actor_id", "notebook_id", "source_id",
        "source_version_id", "processing_run_id", "capability", "failure_kind", "mode",
        "dependency_connection_id", "frozen_context_digest", "safe_error_code", "failed_at", "not_before"}
    statements = [step for step in f.db.steps if type(step) is tuple]
    locks = [params[0] for sql, params in statements if "pg_advisory_xact_lock" in sql]
    assert locks[:3] == ["notebook-delete|tenant-a|workspace-a|notebook-a",
                        "unbind-notebook|tenant-a|workspace-a|notebook-a", "retention-source|tenant-a|workspace-a|source-a"]
    first_lock = next(i for i, (sql, _) in enumerate(statements) if "pg_advisory_xact_lock" in sql)
    transition = next(i for i, (sql, _) in enumerate(statements) if "transition_canon_state" in sql)
    recheck = [i for i, (sql, _) in enumerate(statements) if "readiness-failure-parent" in sql]
    assert recheck[0] < first_lock < recheck[1] < transition
    # 0003/0058/0060: app 직접 UPDATE 금지. rowlock은 기존 SECDEF 전이 소유다.
    assert all("FOR UPDATE" not in sql and "FOR SHARE" not in sql for sql, _ in statements)


def test_auto_run_failure_accepts_saved_full_snapshot_without_rewriting_mode(failure_wait):
    f = failure_wait
    snapshot = f.auth.read(f.db, f.context, f.auth.wait)
    f.db.payload["trigger_type"] = "readiness_event"
    f.db.payload["readiness_context"].update(frozen_context=json.loads(snapshot.frozen_context_json),
        frozen_context_digest=snapshot.eligibility.frozen_context_digest, wait_processing_run_id="earlier-wait-run")
    f.repo.fail_with_readiness(f.context, "run-a", "UNDERSTANDING_PROVIDER_UNAVAILABLE", retryable=True,
                               failure_evidence=f.evidence)
    assert f.db.states == {"ProcessingRun": "failed", "Source": "waiting_model"}
    assert len(f.db.waits) == 1
    assert f.db.waits[0]["mode"] == {"mode": "auto", "origin": "request"}


def test_readiness_denial_cleanup_rechecks_latest_version_under_existing_lock(failure_wait):
    f = failure_wait
    snapshot = f.auth.read(f.db, f.context, f.auth.wait)
    f.db.payload["trigger_type"] = "readiness_event"
    f.db.payload["readiness_context"].update(frozen_context=json.loads(snapshot.frozen_context_json),
        frozen_context_digest=snapshot.eligibility.frozen_context_digest, wait_processing_run_id="earlier-wait-run")
    f.db.publish_version_on_lock = True
    with pytest.raises(DocumentUnderstandingError):
        f.repo.authorize_readiness_run(f.context, "source-a", "run-a")
    assert f.db.states == {"ProcessingRun": "failed", "Source": "processing"}
    assert f.db.source_versions == [("version-a", 1), ("version-b", 2)]
    assert f.db.waits == []


@pytest.mark.parametrize("failure", [RuntimeError("protected-write"),
    DocumentUnderstandingError("CURRENT_ACCESS_DENIED", status=503),
    DocumentUnderstandingError("OTHER_DENIED", status=403),
    DocumentUnderstandingError("SOURCE_NOT_FOUND", status=404)])
def test_failure_wait_protection_errors_rollback_original_failure_and_notice(failure_wait, failure):
    f = failure_wait
    f.db.write_error = failure
    with pytest.raises(DocumentUnderstandingError):
        f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=f.evidence)
    assert f.db.states == {"ProcessingRun": "vision_llm_understanding", "Source": "processing"}
    assert f.db.waits == f.db.notices == []


def test_failure_wait_current_access_denied_preserves_original_failure_and_notification(failure_wait):
    f = failure_wait
    f.db.write_error = DocumentUnderstandingError("CURRENT_ACCESS_DENIED", status=403)
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=f.evidence)
    assert f.db.states == {"ProcessingRun": "failed", "Source": "waiting_model"}
    assert not f.db.waits and len(f.db.notices) == 1


def test_failure_wait_real_metadata_reader_access_denial_preserves_failure_without_provider(failure_wait, monkeypatch):
    import daon_user_api.document_index_postgres as index
    f = failure_wait
    monkeypatch.setattr(index, "_source_access_allowed", lambda *args: False)
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=f.evidence)
    assert f.db.states == {"ProcessingRun": "failed", "Source": "waiting_model"}
    assert not f.db.waits and len(f.db.notices) == 1
    assert [call[2].value for call in f.auth.auth_calls] == ["edit", "analyze"]
    assert "rollback" in f.db.steps and f.db.steps[-1] == "commit"


def test_failure_wait_reader_binding_unbound_source_missing_preserves_failure_only(failure_wait):
    f = failure_wait
    # 실제 B1 SQL의 정확 Notebook/binding/source 조회가 None을 반환하는 대역이다.
    # 원 실패 Run/Source는 존재하고 처리 중이므로 원 전이와 알림은 유효하다.
    f.db.source = None
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=f.evidence)
    assert f.db.states == {"ProcessingRun": "failed", "Source": "waiting_model"}
    assert not f.db.waits and len(f.db.notices) == 1
    queries = [(sql, params) for sql, params in f.db.calls if "readiness-metadata-source" in sql]
    assert len(queries) == 1
    sql, params = queries[0]
    assert "JOIN public.notebook_bindings" in sql and "notebook_source_unbindings" in sql
    assert params[:5] == ("notebook-a", "tenant-a", "workspace-a", "source-a", "version-a")
    assert not any(type(step) is tuple and "record_document_role_wait" in step[0] for step in f.db.steps)
    assert "rollback" in f.db.steps and f.db.steps[-1] == "commit"


def test_failure_wait_snapshot_uses_same_connection_real_body_unknown_cost_and_failure_dependency(failure_wait, monkeypatch):
    import daon_user_api.workspace_model_defaults as defaults
    f = failure_wait
    reader = defaults.read_document_readiness_policy
    snapshots = []
    def read(connection, context, wait, **options):
        assert connection is f.db and context is f.context
        assert wait.frozen_context_digest == "0" * 64
        snapshot = reader(connection, context, wait, **options)
        snapshots.append(snapshot)
        return snapshot
    monkeypatch.setattr(defaults, "read_document_readiness_policy", read)
    # 실제 실패 connection은 A, 현재 binding은 B다. 기다림의 provenance를 B로 고치지 않는다.
    f.db.binding["connection_id"] = "connection-b"
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=f.evidence)
    body = json.loads(snapshots[0].frozen_context_json)
    assert body["cost"] == {"state": "unknown"}
    assert body["binding"]["connection_id"] == "connection-b"
    assert body["scope"] == {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "actor_id": "actor-a",
                             "notebook_id": "notebook-a", "source_version_id": "version-a"}
    assert f.db.waits[0]["dependency_connection_id"] == "connection-a"
    assert f.db.waits[0]["frozen_context_digest"] == hashlib.sha256(snapshots[0].frozen_context_json.encode()).hexdigest()


@pytest.mark.parametrize("proof", [None, {"version": True, "mode": "auto", "mode_origin": "request"},
    {"version": 1, "mode": "auto", "mode_origin": "legacy_unknown"},
    {"version": 1, "mode": "auto", "mode_origin": "request", "extra": 1}])
def test_failure_wait_invalid_explicit_mode_proof_cannot_silently_fall_back(failure_wait, proof):
    f = failure_wait
    f.db.payload["readiness_context"] = proof
    with pytest.raises(DocumentUnderstandingError):
        f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=f.evidence)
    assert f.db.states == {"ProcessingRun": "vision_llm_understanding", "Source": "processing"}
    assert f.db.waits == f.db.notices == []


def test_failure_wait_optional_port_absent_keeps_original_sql_and_state_contract():
    from daon_user_api.document_understanding_adapter import DocumentFailureEvidence
    cloud = FakeCloudStore()
    cloud.connection.states = {"ProcessingRun": "vision_llm_understanding", "Source": "processing"}
    repo = PostgresDocumentProcessingRepository(cloud, object())
    context = DocumentProcessingContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1")
    repo.fail_with_readiness(context, "run-cp3", "MODEL_FAILURE", retryable=False,
        failure_evidence=DocumentFailureEvidence("text_generation", "connection-a", "role_runtime"))
    assert cloud.connection.states == {"ProcessingRun": "failed", "Source": "failed"}
    assert not any("readiness" in sql or "clock_timestamp" in sql for sql, _ in cloud.connection.statements)


@pytest.mark.parametrize("mode,kind,role,connection,retryable,target,count", [
    ("auto", "connection_availability", "text_generation", "connection-a", False, "waiting_model", 1),
    ("local_only", "role_runtime", "speech_to_text", None, False, "waiting_model", 1),
    ("pinned", "role_runtime", "text_generation", "connection-a", False, "failed", 0),
    ("legacy_unknown", "role_runtime", "text_generation", "connection-a", True, "waiting_model", 1),
    (None, "role_runtime", "text_generation", "connection-a", False, "failed", 0),
    ("auto", "unknown", "text_generation", None, False, "failed", 0),
    ("auto", "non_availability", "text_generation", "connection-a", True, "waiting_model", 1),
    ("auto", "connection_availability", None, "connection-a", True, "waiting_model", 1),
    ("pinned", "role_runtime", "text_generation", "connection-a", True, "waiting_model", 1),
    (None, "unknown", None, None, True, "waiting_model", 1),
])
def test_failure_wait_explicit_mode_known_role_only_without_legacy_reclassification(
        failure_wait, mode, kind, role, connection, retryable, target, count):
    from daon_user_api.document_understanding_adapter import DocumentFailureEvidence
    f = failure_wait
    if mode is None:
        f.db.payload.pop("readiness_context")
    else:
        f.db.payload["readiness_context"] = {"version": 1, "mode": mode,
            "mode_origin": "legacy_unknown" if mode == "legacy_unknown" else "request"}
    evidence = DocumentFailureEvidence(role, connection, kind)
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=retryable, failure_evidence=evidence)
    assert f.db.states == {"ProcessingRun": "failed", "Source": target}
    assert len(f.db.waits) == count


def test_failure_wait_missing_internal_metadata_collects_unknown_but_missing_notebook_cannot_backfill(failure_wait):
    f = failure_wait
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=None)
    assert len(f.db.waits) == 1
    assert (f.db.waits[0]["capability"], f.db.waits[0]["dependency_connection_id"], f.db.waits[0]["failure_kind"]) == (
        None, None, "unknown")
    f.db.states = {"ProcessingRun": "vision_llm_understanding", "Source": "processing"}
    f.db.waits.clear()
    f.db.payload.pop("notebook_id")
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=None)
    assert f.db.states == {"ProcessingRun": "failed", "Source": "waiting_model"}
    assert not f.db.waits and len(f.db.notices) == 2


@pytest.mark.parametrize("change", ["terminal", "latest", "actor", "source"])
def test_failure_wait_late_terminal_or_changed_parent_writes_nothing(failure_wait, change):
    f = failure_wait
    if change == "terminal": f.db.states["ProcessingRun"] = "failed"
    if change == "latest": f.db.latest_run = "manual-newer-run"
    if change == "actor": f.db.actor = "other-actor"
    if change == "source": f.db.states["Source"] = "ready"
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=f.evidence)
    assert not f.db.waits and not f.db.notices
    assert not any(type(step) is tuple and "transition_canon_state" in step[0] for step in f.db.steps)


@pytest.mark.parametrize("new_source_state,retryable", [("processing", True), ("processing", False),
                                                        ("ready", True), ("ready", False)])
def test_failure_wait_old_version_failure_cleans_only_original_run_not_new_source(
        failure_wait, new_source_state, retryable):
    f = failure_wait
    f.db.source_versions.append(("version-b", 2))
    f.db.states["Source"] = new_source_state
    f.db.versions["Source"] = 8
    f.db.other_runs["run-new-version-b"] = {"state": "vision_llm_understanding" if new_source_state == "processing"
                                          else "completed", "version": 4, "source_version_id": "version-b"}
    before_new_run = copy.deepcopy(f.db.other_runs)
    # 구 Version은 B1 최신성 조회에서 404다. 이 거부 격리가 새 Source 전이까지 commit하면 안 된다.
    f.db.source = None
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=retryable, failure_evidence=f.evidence)
    assert f.db.states == {"ProcessingRun": "failed", "Source": new_source_state}
    assert f.db.versions == {"ProcessingRun": 3, "Source": 8}
    assert f.db.other_runs == before_new_run and not f.db.waits
    assert len(f.db.notices) == 1
    transitions = [params for step in f.db.steps if type(step) is tuple
                   for sql, params in [step] if "transition_canon_state" in sql]
    assert [(p[0], p[1], p[2], p[3]) for p in transitions] == [("ProcessingRun", "run-a", 2, "failed")]
    assert not any("readiness-metadata-source" in sql for sql, _ in f.db.calls)


def test_failure_wait_latest_source_version_is_rechecked_after_advisory_locks(failure_wait):
    f = failure_wait
    f.db.publish_version_on_lock = True
    f.db.source = None
    f.repo.fail_with_readiness(f.context, "run-a", "MODEL_FAILURE", retryable=True, failure_evidence=f.evidence)
    assert f.db.states == {"ProcessingRun": "failed", "Source": "processing"}
    assert f.db.versions["Source"] == 3 and not f.db.waits and len(f.db.notices) == 1
    queries = [(i, step[0]) for i, step in enumerate(f.db.steps)
               if type(step) is tuple and "readiness-failure-parent" in step[0]]
    lock = next(i for i, step in enumerate(f.db.steps) if type(step) is tuple and "retention-source" in str(step[1]))
    transition = next(i for i, step in enumerate(f.db.steps) if type(step) is tuple and "transition_canon_state" in step[0])
    assert queries[0][0] < lock < queries[1][0] < transition
    assert all("newer.version>sv.version" in sql and "NOT EXISTS" in sql for _, sql in queries)


from test_source_reprocessing import Store as ReprocessingStore, Cursor as ReprocessingCursor


class ReprocessingModeStore(ReprocessingStore):
    """실제 repository SQL 소비/rollback 대역. PostgreSQL 실행 증거가 아니다."""
    def __init__(self):
        super().__init__()
        self.saved_runs = {}
        self.saved_replays = {}

    @contextmanager
    def _transaction(self, context):
        before = copy.deepcopy((self.saved_runs, self.saved_replays))
        try:
            with super()._transaction(context) as connection:
                yield connection
        except BaseException:
            self.saved_runs, self.saved_replays = before
            raise

    def execute(self, sql, params=()):
        if sql.startswith("SELECT request_fingerprint,result"):
            self.calls.append((sql, params))
            return ReprocessingCursor(self.saved_replays.get(tuple(params)))
        if sql.startswith("SELECT pr.canonical_json FROM processing_runs pr"):
            self.calls.append((sql, params))
            saved = self.saved_runs.get(params[2])
            if saved is None or (saved[0], saved[1], saved[2], saved[7], saved[9], saved[4].obj["notebook_id"]) != tuple(params):
                return ReprocessingCursor()
            return ReprocessingCursor((copy.deepcopy(saved[4].obj),))
        result = super().execute(sql, params)
        if sql.startswith("INSERT INTO processing_runs"):
            self.saved_runs[params[2]] = copy.deepcopy(params)
        if sql.startswith("INSERT INTO idempotency_records"):
            self.saved_replays[tuple(params[:5])] = (params[5], copy.deepcopy(params[6].obj))
        return result


class ReprocessingModeStorageTests(unittest.TestCase):
    def setUp(self):
        self.store = ReprocessingModeStore()
        self.context = DocumentProcessingContext("tenant-1", "workspace-1", "user-1", "trace-1", "policy-2")
        self.repo = PostgresDocumentProcessingRepository(self.store, object())
        self.args = dict(notebook_id="notebook-1", idempotency_key="retry-1",
                         authorization_snapshot={"acl_version": 7, "membership_version": 2, "policy_version": "policy-2"})

    def test_absent_mode_keeps_legacy_fingerprint_wire_and_only_new_run_unknown(self):
        result = self.repo.reprocess(self.context, "source-1", "version-1", **self.args)
        self.assertEqual(set(result), {"processing_run_id", "source_id", "source_version_id", "retry_of_processing_run_id",
                                     "processing_state", "source_state", "job_state", "replayed"})
        self.assertEqual(next(iter(self.store.saved_replays.values()))[0],
                         "76b683b5297932160d9246e5dbb600248c5d661300841c5a6b1a997d1b50c2e8")
        payload = self.store.saved_runs[result["processing_run_id"]][4].obj
        self.assertEqual(payload["readiness_context"], {"version": 1, "mode": "legacy_unknown", "mode_origin": "legacy_unknown"})
        self.assertEqual(self.store.old_run, ("run-failed-1", "failed", "dead_letter"))
        self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in self.store.writes))
        before = len(self.store.writes)
        replay = self.repo.reprocess(self.context, "source-1", "version-1", **self.args)
        self.assertTrue(replay["replayed"])
        self.assertNotIn("processing_mode", replay)
        self.assertEqual(len(self.store.writes), before)

    def test_explicit_mode_is_saved_reread_on_replay_and_same_key_different_mode_conflicts(self):
        for mode in ("auto", "local_only", "pinned"):
            with self.subTest(mode=mode):
                self.setUp()
                result = self.repo.reprocess(self.context, "source-1", "version-1", processing_mode=mode, **self.args)
                self.assertEqual(result["processing_mode"], mode)
                self.assertEqual(self.store.saved_runs[result["processing_run_id"]][4].obj["readiness_context"],
                                 {"version": 1, "mode": mode, "mode_origin": "request"})
                key = next(iter(self.store.saved_replays))
                fingerprint, cached = self.store.saved_replays[key]
                cached["processing_mode"] = "not-a-saved-mode"
                self.store.saved_replays[key] = (fingerprint, cached)
                before = len(self.store.writes)
                replay = self.repo.reprocess(self.context, "source-1", "version-1", processing_mode=mode, **self.args)
                self.assertEqual((replay["processing_mode"], replay["replayed"]), (mode, True))
                self.assertEqual(len(self.store.writes), before)
                for changed in ("auto", "local_only", "pinned", None):
                    if changed == mode:
                        continue
                    with self.subTest(changed=changed):
                        with self.assertRaises(DocumentUnderstandingError) as caught:
                            self.repo.reprocess(self.context, "source-1", "version-1", **self.args,
                                                **({} if changed is None else {"processing_mode": changed}))
                        self.assertEqual((caught.exception.code, caught.exception.status), ("IDEMPOTENCY_KEY_REUSED", 409))
                self.assertEqual(len(self.store.writes), before)

    def test_invalid_mode_missing_saved_run_and_revocation_never_write_or_fallback(self):
        for mode in (None, True, "legacy_unknown", "AUTO", {}):
            with self.subTest(mode=mode):
                with self.assertRaises(DocumentUnderstandingError):
                    self.repo.reprocess(self.context, "source-1", "version-1", processing_mode=mode, **self.args)
        self.assertEqual(self.store.writes, [])
        result = self.repo.reprocess(self.context, "source-1", "version-1", processing_mode="auto", **self.args)
        before = len(self.store.writes)
        payload = self.store.saved_runs[result["processing_run_id"]][4].obj
        for proof in (None, {"version": True, "mode": "auto", "mode_origin": "request"},
                      {"version": 1, "mode": "pinned", "mode_origin": "request"},
                      {"version": 1, "mode": "auto", "mode_origin": "request", "extra": True}):
            with self.subTest(proof=proof):
                payload["readiness_context"] = proof
                with self.assertRaises(DocumentUnderstandingError) as caught:
                    self.repo.reprocess(self.context, "source-1", "version-1", processing_mode="auto", **self.args)
                self.assertEqual(caught.exception.status, 503)
                self.assertEqual(len(self.store.writes), before)
                # rollback 대역은 snapshot을 새 객체로 복구한다.
                payload = self.store.saved_runs[result["processing_run_id"]][4].obj
        self.store.saved_runs.clear()
        with self.assertRaises(DocumentUnderstandingError):
            self.repo.reprocess(self.context, "source-1", "version-1", processing_mode="auto", **self.args)
        self.assertEqual(len(self.store.writes), before)
        self.store.source = None
        with self.assertRaises(DocumentUnderstandingError) as caught:
            self.repo.reprocess(self.context, "source-1", "version-1", processing_mode="auto", **self.args)
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(len(self.store.writes), before)


class Cursor:
    def __init__(self, row=None) -> None:  # type: ignore[no-untyped-def]
        self.row = row

    def fetchone(self):  # type: ignore[no-untyped-def]
        return self.row


class FakeConnection:
    def __init__(self) -> None:
        self.filename = "contract.pdf"
        self.content_type = "application/pdf"
        self.content = PDF
        self.states = {"ProcessingRun": "accepted", "Source": "registered"}
        self.versions = {"ProcessingRun": 1, "Source": 1}
        self.source_version_id = "source-version-cp3"
        self.inserts: list[str] = []
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        if "/* readiness-execution-run */" in sql:
            self.statements.append((sql, tuple(params)))
            return Cursor(({"trigger_type": "manual_retry"},))
        if sql.startswith("SELECT pr.state,pr.version,pr.source_version_id,sv.source_id"):
            return Cursor((self.states["ProcessingRun"], self.versions["ProcessingRun"], self.source_version_id, "source-cp3"))
        self.statements.append((sql, tuple(params)))
        if sql.startswith("SELECT sv.record_id"):
            return Cursor((
                self.source_version_id, OBJECT_ID, {"filename": self.filename},
                OBJECT_KEY, hashlib.sha256(self.content).hexdigest(), len(self.content),
                self.content_type, "completed",
            ))
        if sql.startswith("INSERT INTO processing_runs"):
            self.inserts.append("processing_runs")
            return Cursor()
        if sql.startswith("INSERT INTO document_processing_jobs"):
            self.inserts.append("document_processing_jobs")
            return Cursor()
        if sql.startswith("SELECT state,version,source_version_id"):
            return Cursor((self.states["ProcessingRun"], self.versions["ProcessingRun"], self.source_version_id))
        if sql.startswith("SELECT sv.source_id,s.state,s.version"):
            return Cursor(("source-cp3", self.states["Source"], self.versions["Source"]))
        if sql.startswith("SELECT state,version,outcome,error_code FROM transition_canon_state"):
            entity_type = params[0]
            self.states[entity_type] = params[3]
            self.versions[entity_type] += 1
            return Cursor((self.states[entity_type], self.versions[entity_type], "succeeded", None))
        if sql.startswith("INSERT INTO understanding_results"):
            self.inserts.append("understanding_results")
            return Cursor()
        if sql.startswith("INSERT INTO extraction_evidence"):
            self.inserts.append("extraction_evidence")
            return Cursor()
        if sql.startswith("SELECT pr.state,pr.version,sv.source_id,s.state,s.version"):
            return Cursor((
                self.states["ProcessingRun"], self.versions["ProcessingRun"],
                "source-cp3", self.states["Source"], self.versions["Source"],
            ))
        if sql.startswith("SELECT pr.record_id,sv.source_id,pr.source_version_id"):
            return Cursor((
                "run-cp3", "source-cp3", self.source_version_id,
                self.states["ProcessingRun"], self.states["Source"], "leased", None,
            ))
        raise AssertionError(sql)


class FakeCloudStore:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.capabilities: list[str] = []

    @contextmanager
    def _transaction(self, context):  # type: ignore[no-untyped-def]
        self.capabilities.append(context.capability)
        yield self.connection


class AudioProcessingStorageTests(unittest.TestCase):
    def test_audio_transmission_revalidates_current_access_binding_and_version_with_real_sql(self):
        from test_audio_source import wav_bytes
        from daon_user_api.document_processing import StoredSourceDocument
        raw = wav_bytes();digest = hashlib.sha256(raw).hexdigest()
        context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
        document = StoredSourceDocument('source-cp3', 'source-version-cp3', 'qa.wav', raw, 'audio/wav')
        class Store:
            def __init__(self):
                self.db = sqlite3.connect(':memory:')
                self.db.executescript('''
                    CREATE TABLE processing_runs(tenant_id,workspace_id,record_id,source_version_id,state,created_by,canonical_json);
                    CREATE TABLE sources(tenant_id,workspace_id,record_id,state,created_by);
                    CREATE TABLE source_versions(tenant_id,workspace_id,record_id,source_id,version,object_id,canonical_json);
                    CREATE TABLE object_records(tenant_id,workspace_id,object_id,digest_sha256,byte_size,content_type,status);
                    CREATE TABLE notebooks(tenant_id,workspace_id,notebook_id);
                    CREATE TABLE notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id);
                    CREATE TABLE notebook_source_unbindings(tenant_id,workspace_id,notebook_id,source_id,source_version_id);
                    CREATE TABLE notebook_deletion_requests(tenant_id,workspace_id,notebook_id);
                    CREATE TABLE deletion_requests(tenant_id,workspace_id,source_id,source_active);
                    CREATE TABLE identity_auth_workspaces(tenant_id,workspace_id,workspace_kind);
                    CREATE TABLE identity_auth_memberships(tenant_id,workspace_id,user_id,role,state);
                    CREATE TABLE identity_auth_tenant_roles(tenant_id,user_id,role,state);
                    CREATE TABLE identity_auth_tenant_permission_rules(tenant_id,permission,effect,locked);
                    CREATE TABLE identity_auth_workspace_permission_rules(tenant_id,workspace_id,permission,effect,locked);
                    CREATE TABLE identity_auth_source_access(tenant_id,workspace_id,user_id,source_version_id,allowed);
                ''')
                scope = ('tenant-cp3', 'workspace-cp3')
                self.db.execute('INSERT INTO processing_runs VALUES(?,?,?,?,?,?,?)', (*scope, 'pr-a', 'source-version-cp3', 'speech_to_text', 'actor-cp3', '{"notebook_id":"nb-a"}'))
                self.db.execute('INSERT INTO sources VALUES(?,?,?,?,?)', (*scope, 'source-cp3', 'processing', 'actor-cp3'))
                self.db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?,?,?)', (*scope, 'source-version-cp3', 'source-cp3', 1, 'object-a', '{"filename":"qa.wav"}'))
                self.db.execute('INSERT INTO object_records VALUES(?,?,?,?,?,?,?)', (*scope, 'object-a', digest, len(raw), 'audio/wav', 'completed'))
                self.db.execute('INSERT INTO notebooks VALUES(?,?,?)', (*scope, 'nb-a'))
                self.db.execute('INSERT INTO notebook_bindings VALUES(?,?,?,?,?,?)', (*scope, 'nb-a', 'source', 'source-cp3', 'source-version-cp3'))
                self.db.execute('INSERT INTO identity_auth_workspaces VALUES(?,?,?)', (*scope, 'organization'))
                self.db.execute('INSERT INTO identity_auth_memberships VALUES(?,?,?,?,?)', (*scope, 'actor-cp3', 'editor', 'active'))
            @contextmanager
            def _transaction(self, context): yield self
            def execute(self, sql, params=()): return self.db.execute(sql.replace('%s', '?'), params)
        mutations = ["UPDATE identity_auth_memberships SET state='inactive'", "UPDATE identity_auth_memberships SET role='viewer'",
            "DELETE FROM notebook_bindings", "INSERT INTO notebook_source_unbindings VALUES('tenant-cp3','workspace-cp3','nb-a','source-cp3','source-version-cp3')",
            "INSERT INTO identity_auth_workspace_permission_rules VALUES('tenant-cp3','workspace-cp3','local_internal_llm','deny',0)",
            "INSERT INTO identity_auth_source_access VALUES('tenant-cp3','workspace-cp3','actor-cp3','source-version-cp3',0)",
            "INSERT INTO source_versions VALUES('tenant-cp3','workspace-cp3','new-version','source-cp3',2,'object-a','{}')"]
        for mutation in mutations:
            store = Store()
            try:
                repository = PostgresDocumentProcessingRepository(store, object())
                repository.authorize_audio_source(context, document, 'pr-a')
                store.db.execute(mutation)
                with self.subTest(mutation=mutation), self.assertRaises(DocumentUnderstandingError):
                    repository.authorize_audio_source(context, document, 'pr-a')
            finally: store.db.close()

    def test_audio_completion_rejects_tampered_snapshot_with_zero_result_writes(self):
        from dataclasses import replace
        from test_audio_document_understanding import ready_audio_result
        from test_audio_source import wav_bytes
        for field, value in (('transcript_sha256', 'c' * 64), ('source_digest', 'c' * 64),
                             ('duration_ms', '2000'), ('asr_model_digest', 'c' * 64), ('policy_version', 'other')):
            cloud = FakeCloudStore();storage = FakeObjectStorage()
            cloud.connection.filename = 'qa.wav';cloud.connection.content_type = 'audio/wav';cloud.connection.content = wav_bytes()
            storage.content = cloud.connection.content
            cloud.connection.states = {'ProcessingRun': 'speech_to_text', 'Source': 'processing'}
            cloud.connection.versions = {'ProcessingRun': 2, 'Source': 3}
            result = ready_audio_result();result = replace(result, lineage={**result.lineage, field: value})
            context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
            with self.subTest(field=field), self.assertRaises(DocumentUnderstandingError):
                PostgresDocumentProcessingRepository(cloud, storage).complete(context, 'run-cp3', result)
            self.assertEqual(cloud.connection.inserts, [])
            self.assertEqual(cloud.connection.states['ProcessingRun'], 'speech_to_text')

    def test_audio_completion_persists_bound_transcript_and_time_evidence(self):
        from test_audio_document_understanding import ready_audio_result
        from test_audio_source import wav_bytes
        cloud = FakeCloudStore()
        cloud.connection.filename = 'qa.wav';cloud.connection.content_type = 'audio/wav';cloud.connection.content = wav_bytes()
        cloud.connection.states = {'ProcessingRun': 'speech_to_text', 'Source': 'processing'}
        cloud.connection.versions = {'ProcessingRun': 2, 'Source': 3}
        storage = FakeObjectStorage();storage.content = cloud.connection.content
        repository = PostgresDocumentProcessingRepository(cloud, storage)
        context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
        result = ready_audio_result()
        repository.complete(context, 'run-cp3', result)
        payload = next(params[4].obj for sql, params in cloud.connection.statements if sql.startswith('INSERT INTO understanding_results'))
        self.assertEqual(payload['parser_validation']['audio_evidence'], list(result.parser.audio_evidence))
        self.assertEqual(payload['parser_validation']['pages'], [])
        self.assertEqual(payload['lineage']['transcript_sha256'], result.lineage['transcript_sha256'])
        self.assertEqual(cloud.connection.states['ProcessingRun'], 'completed')
        targets = [params[3] for sql, params in cloud.connection.statements if sql.startswith('SELECT state,version,outcome,error_code FROM transition_canon_state')]
        self.assertEqual(targets, ['llm_semantic_understanding', 'transcript_timecode_validation', 'evidence_reconciliation', 'completed'])
        evidence = [params[4].obj for sql, params in cloud.connection.statements if sql.startswith('INSERT INTO extraction_evidence')]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['audio_evidence'], list(result.parser.audio_evidence))
        self.assertNotIn('page', evidence[0])

    def test_audio_start_uses_existing_speech_state_not_vision(self):
        cloud = FakeCloudStore()
        class Connection(FakeConnection):
            def execute(self, sql, params=()):
                if sql.startswith('SELECT sv.source_id,s.state,s.version'):
                    return Cursor(('source-cp3', self.states['Source'], self.versions['Source'], {'filename': 'qa.wav'}, 'audio/wav'))
                return super().execute(sql, params)
        cloud.connection = Connection()
        repository = PostgresDocumentProcessingRepository(cloud, FakeObjectStorage())
        context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
        repository.start(context, 'source-version-cp3', enqueue=True)
        self.assertEqual(cloud.connection.states['ProcessingRun'], 'speech_to_text')
        payload = next(params[4].obj for sql, params in cloud.connection.statements if sql.startswith('INSERT INTO processing_runs'))
        self.assertEqual(payload['modality'], 'audio')
        self.assertNotIn('vision_llm_first', payload)


class FakeObjectStorage:
    content = PDF

    def get(self, key: str) -> bytes:
        if key != OBJECT_KEY:
            raise AssertionError(key)
        return self.content


class BoundConnection(FakeConnection):
    """Execute the production read SQL against two real version rows."""
    def __init__(self):
        super().__init__()
        self.states = {"ProcessingRun": "vision_llm_understanding", "Source": "processing"}
        self.versions = {"ProcessingRun": 2, "Source": 3}
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE sources (tenant_id,workspace_id,record_id);
            CREATE TABLE source_versions (tenant_id,workspace_id,record_id,source_id,object_id,canonical_json,version);
            CREATE TABLE processing_runs (tenant_id,workspace_id,record_id,source_version_id,state);
            CREATE TABLE object_records (tenant_id,workspace_id,object_id,object_key,digest_sha256,byte_size,content_type,status);
        """)
        self.db.execute("INSERT INTO sources VALUES (?,?,?)", ("tenant-cp3", "workspace-cp3", "source-cp3"))
        for version, version_id in ((1, "source-version-cp3"), (2, "source-version-newer")):
            self.db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,?)", (
                "tenant-cp3", "workspace-cp3", version_id, "source-cp3", OBJECT_ID,
                json.dumps({"filename": "contract.pdf"}), version,
            ))
        self.db.execute("INSERT INTO processing_runs VALUES (?,?,?,?,?)", (
            "tenant-cp3", "workspace-cp3", "run-cp3", "source-version-cp3", "vision_llm_understanding",
        ))
        self.db.execute("INSERT INTO object_records VALUES (?,?,?,?,?,?,?,?)", (
            "tenant-cp3", "workspace-cp3", OBJECT_ID, OBJECT_KEY,
            hashlib.sha256(PDF).hexdigest(), len(PDF), "application/pdf", "completed",
        ))

    def execute(self, sql, params=()):
        if sql.startswith("SELECT sv.record_id"):
            self.statements.append((sql, tuple(params)))
            row = self.db.execute(sql.replace("%s", "?"), params).fetchone()
            if row is not None:
                row = (*row[:2], json.loads(row[2]), *row[3:])
            return Cursor(row)
        return super().execute(sql, params)


class PostgresDocumentProcessingRepositoryTests(unittest.TestCase):
    def test_pptx_bound_original_accepts_exact_mime_and_preserves_hash_checks(self):
        from test_pptx_document_understanding import original
        raw = original()
        connection = self._bound_fixture()
        connection.db.execute('UPDATE source_versions SET canonical_json=?', (json.dumps({'filename': 'qa.pptx'}),))
        connection.db.execute('UPDATE object_records SET digest_sha256=?,byte_size=?,content_type=?',
            (hashlib.sha256(raw).hexdigest(), len(raw), 'application/vnd.openxmlformats-officedocument.presentationml.presentation'))
        self.repository._object_storage.content = raw
        document = self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3')
        self.assertEqual(document.content, raw)
        self.assertEqual(document.source_version_id, 'source-version-cp3')
        connection.db.execute('UPDATE object_records SET digest_sha256=?', ('sha256:' + hashlib.sha256(raw).hexdigest(),))
        self.assertEqual(self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3').content, raw)
        for source, run in (('other-source', 'run-cp3'), ('source-cp3', 'other-run')):
            with self.subTest(source=source, run=run), self.assertRaisesRegex(DocumentUnderstandingError, 'SOURCE_NOT_FOUND'):
                self.repository.load_source_document(self.context, source, processing_run_id=run)
        self.assertFalse(any('transition_canon_state' in sql for sql, _ in connection.statements))

    def test_pptx_completion_binds_canonical_extraction_without_fake_pages(self):
        from test_pptx_document_understanding import ready_pptx_result
        result = ready_pptx_result()
        self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'
        self.cloud.connection.versions['ProcessingRun'] = 2
        self.cloud.connection.states['Source'] = 'processing'
        self.repository.complete(self.context, 'run-cp3', result)
        statements = self.cloud.connection.statements
        payload = next(params[4].obj for sql, params in statements if sql.startswith('INSERT INTO understanding_results'))
        extraction = [params[4].obj for sql, params in statements if sql.startswith('INSERT INTO extraction_evidence')]
        self.assertEqual(payload['parser_validation']['slide_evidence'], list(result.parser.slide_evidence))
        self.assertEqual(payload['parser_validation']['pages'], [])
        self.assertEqual(payload['parser_validation']['page_texts'], [])
        self.assertEqual(extraction, [{'source_version_id': result.source_version_id,
            'understanding_result_id': payload.get('understanding_result_id', self.repository._opaque_id('ur', 'run-cp3', result.source_version_id)),
            'parser_role': 'validation_only', 'slide_evidence': list(result.parser.slide_evidence)}])
        self.assertEqual(self.cloud.connection.states['Source'], 'processing')

    def test_pptx_completion_wrong_bound_version_and_invalid_evidence_have_zero_writes(self):
        from dataclasses import replace
        from test_pptx_document_understanding import ready_pptx_result
        for invalid in ('version', 'locator', 'page'):
            with self.subTest(invalid=invalid):
                self.setUp(); result = ready_pptx_result()
                self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'
                self.cloud.connection.versions['ProcessingRun'] = 2
                if invalid == 'version': result = replace(result, source_version_id='other-version')
                elif invalid == 'locator': result.parser.slide_evidence[0]['locator_value'] = '{"block":"p:1","start":6,"end":18}'
                else: result = replace(result, parser=replace(result.parser, pages=(1,)))
                with self.assertRaises(DocumentUnderstandingError): self.repository.complete(self.context, 'run-cp3', result)
                self.assertEqual(self.cloud.connection.inserts, [])
                self.assertEqual(self.cloud.connection.states['ProcessingRun'], 'vision_llm_understanding')

    def test_docx_completion_binds_canonical_extraction_without_fake_pages(self):
        from test_docx_document_understanding import ready_docx_result
        result = ready_docx_result()
        self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'
        self.cloud.connection.versions['ProcessingRun'] = 2
        self.cloud.connection.states['Source'] = 'processing'
        self.repository.complete(self.context, 'run-cp3', result)
        statements = self.cloud.connection.statements
        payload = next(params[4].obj for sql, params in statements if sql.startswith('INSERT INTO understanding_results'))
        extraction = [params[4].obj for sql, params in statements if sql.startswith('INSERT INTO extraction_evidence')]
        self.assertEqual(payload['parser_validation']['block_evidence'], list(result.parser.block_evidence))
        self.assertEqual(payload['parser_validation']['pages'], [])
        self.assertEqual(payload['parser_validation']['page_texts'], [])
        self.assertEqual(extraction, [{'source_version_id': result.source_version_id,
            'understanding_result_id': payload.get('understanding_result_id', self.repository._opaque_id('ur', 'run-cp3', result.source_version_id)),
            'parser_role': 'validation_only', 'block_evidence': list(result.parser.block_evidence)}])
        self.assertEqual(self.cloud.connection.states['Source'], 'processing')

    def test_docx_completion_wrong_bound_version_and_invalid_evidence_have_zero_writes(self):
        from dataclasses import replace
        from test_docx_document_understanding import ready_docx_result
        for invalid in ('version', 'locator', 'page'):
            with self.subTest(invalid=invalid):
                self.setUp(); result = ready_docx_result()
                self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'
                self.cloud.connection.versions['ProcessingRun'] = 2
                if invalid == 'version': result = replace(result, source_version_id='other-version')
                elif invalid == 'locator': result.parser.block_evidence[0]['locator_value'] = '{"block":"p:1","start":6,"end":18}'
                else: result = replace(result, parser=replace(result.parser, pages=(1,)))
                with self.assertRaises(DocumentUnderstandingError): self.repository.complete(self.context, 'run-cp3', result)
                self.assertEqual(self.cloud.connection.inserts, [])
                self.assertEqual(self.cloud.connection.states['ProcessingRun'], 'vision_llm_understanding')

    def test_docx_bound_original_accepts_exact_mime_and_preserves_hash_checks(self):
        from test_docx_source import docx_bytes, paragraph
        raw = docx_bytes(paragraph('REF-DELTA-17'))
        connection = self._bound_fixture()
        connection.db.execute('UPDATE source_versions SET canonical_json=?', (json.dumps({'filename': 'qa.docx'}),))
        connection.db.execute('UPDATE object_records SET digest_sha256=?,byte_size=?,content_type=?',
            (hashlib.sha256(raw).hexdigest(), len(raw), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
        self.repository._object_storage.content = raw
        document = self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3')
        self.assertEqual(document.content, raw)
        self.assertEqual(document.source_version_id, 'source-version-cp3')
        connection.db.execute('UPDATE object_records SET digest_sha256=?', ('sha256:' + hashlib.sha256(raw).hexdigest(),))
        self.assertEqual(self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3').content, raw)
        for source, run in (('other-source', 'run-cp3'), ('source-cp3', 'other-run')):
            with self.subTest(source=source, run=run), self.assertRaisesRegex(DocumentUnderstandingError, 'SOURCE_NOT_FOUND'):
                self.repository.load_source_document(self.context, source, processing_run_id=run)
        self.assertFalse(any('transition_canon_state' in sql for sql, _ in connection.statements))

    def test_xlsx_original_and_completion_preserve_exact_cells_without_fake_pages(self):
        from test_xlsx_source import parts, zipped
        connection = self.cloud.connection
        connection.filename, connection.content_type, connection.content = 'qa.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', zipped(parts())
        self.repository._object_storage.content = connection.content
        document = self.repository.load_source_document(self.context, 'source-cp3')
        self.assertEqual(document.content, connection.content)
        run_id = self.repository.start(self.context, document.source_version_id)
        cells = ({'text': 'ORANGE-42', 'sheet': '납품 "계획"', 'range': 'B2', 'locator_value': '{"sheet":"납품 \\"계획\\"","range":"B2"}'.replace('\\\\', '\\')},)
        result = DocumentUnderstandingResult(document.source_id, document.source_version_id, 'ready', ('vision_llm_understanding', 'parser_ocr_validation', 'evidence_reconciliation'), SemanticUnderstanding('QA', 'ORANGE-42', ('ORANGE-42',)), ParserValidation('', '', '', (), cell_evidence=cells), {'source_format': 'xlsx', 'source_digest': hashlib.sha256(document.content).hexdigest()})
        self.repository.complete(self.context, run_id, result)
        payload = next(params[4].obj for sql, params in connection.statements if sql.startswith('INSERT INTO understanding_results'))
        extraction = [params[4].obj for sql, params in connection.statements if sql.startswith('INSERT INTO extraction_evidence')]
        self.assertEqual(payload['parser_validation']['cell_evidence'], list(cells))
        self.assertEqual(len(extraction), 1); self.assertEqual(extraction[0]['cell_evidence'], list(cells))
        self.assertNotIn('page', extraction[0]); self.assertEqual(connection.states['Source'], 'processing')

    def test_xlsx_completion_bound_version_mismatch_has_no_state_write(self):
        result = DocumentUnderstandingResult('source-cp3', 'wrong-version', 'ready', (), SemanticUnderstanding('QA', 'ORANGE-42', ('ORANGE-42',)), ParserValidation('', '', '', (), cell_evidence=({'text': 'ORANGE-42', 'sheet': 's', 'range': 'A1', 'locator_value': '{"sheet":"s","range":"A1"}'},)), {'source_format': 'xlsx'})
        self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'; self.cloud.connection.versions['ProcessingRun'] = 2
        with self.assertRaises(DocumentUnderstandingError): self.repository.complete(self.context, 'run-cp3', result)
        self.assertEqual(self.cloud.connection.inserts, []); self.assertEqual(self.cloud.connection.states['ProcessingRun'], 'vision_llm_understanding')

    def test_bound_xlsx_read_keeps_claimed_version_and_scope_failure_writes_zero(self):
        from test_xlsx_source import parts, zipped
        raw = zipped(parts()); connection = self._bound_fixture()
        connection.db.execute('UPDATE source_versions SET canonical_json=?', (json.dumps({'filename': 'qa.xlsx'}),))
        connection.db.execute('UPDATE object_records SET digest_sha256=?,byte_size=?,content_type=?', (hashlib.sha256(raw).hexdigest(), len(raw), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
        self.repository._object_storage.content = raw
        document = self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3')
        self.assertEqual(document.source_version_id, 'source-version-cp3')
        self.assertEqual(document.content, raw)
        for source, run in [('source-other', 'run-cp3'), ('source-cp3', 'run-other')]:
            with self.assertRaisesRegex(DocumentUnderstandingError, 'SOURCE_NOT_FOUND'): self.repository.load_source_document(self.context, source, processing_run_id=run)
        self.assertFalse(any('transition_canon_state' in sql for sql, _ in connection.statements))


    def setUp(self) -> None:
        self.context = DocumentProcessingContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
        )
        self.cloud = FakeCloudStore()
        self.repository = PostgresDocumentProcessingRepository(self.cloud, FakeObjectStorage())  # type: ignore[arg-type]

    def _bound_fixture(self):
        self.cloud.connection = BoundConnection()
        self.addCleanup(self.cloud.connection.db.close)
        return self.cloud.connection

    def test_bound_read_keeps_queued_version_while_initial_read_uses_latest(self) -> None:
        self._bound_fixture()
        bound = self.repository.load_source_document(self.context, "source-cp3", processing_run_id="run-cp3")
        latest = self.repository.load_source_document(self.context, "source-cp3")
        self.assertEqual(bound.source_version_id, "source-version-cp3")
        self.assertEqual(latest.source_version_id, "source-version-newer")

    def test_missing_or_wrong_bound_scope_never_transitions_run_or_reads_object(self) -> None:
        connection = self._bound_fixture()
        class UnreadableStorage:
            def get(self, key):
                raise AssertionError("unbound request must not read objects")
        self.repository._object_storage = UnreadableStorage()
        for source_id, run_id, tenant, workspace in (
            ("source-other", "run-cp3", "tenant-cp3", "workspace-cp3"),
            ("source-cp3", "run-other", "tenant-cp3", "workspace-cp3"),
            ("source-cp3", "run-cp3", "tenant-other", "workspace-cp3"),
            ("source-cp3", "run-cp3", "tenant-cp3", "workspace-other"),
        ):
            with self.subTest(source=source_id, run=run_id, tenant=tenant, workspace=workspace):
                context = DocumentProcessingContext(tenant, workspace, "actor-cp3", "trace-cp3", "policy-v1")
                with self.assertRaisesRegex(DocumentUnderstandingError, "SOURCE_NOT_FOUND"):
                    self.repository.load_source_document(context, source_id, processing_run_id=run_id)
                self.assertEqual(connection.states, {"ProcessingRun": "vision_llm_understanding", "Source": "processing"})
                self.assertFalse(any("transition_canon_state" in sql for sql, _ in connection.statements))

    def test_bound_query_database_failure_never_transitions_run(self) -> None:
        connection = self._bound_fixture()
        original = connection.execute
        def unavailable(sql, params=()):
            if sql.startswith("SELECT sv.record_id"):
                raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True)
            return original(sql, params)
        connection.execute = unavailable
        with self.assertRaisesRegex(DocumentUnderstandingError, "DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE"):
            self.repository.load_source_document(self.context, "source-cp3", processing_run_id="run-cp3")
        self.assertEqual(self.cloud.capabilities, ["source.read"])

    def test_completed_or_failed_bound_run_checksum_error_never_writes_state(self) -> None:
        for state, source_state in (("completed", "ready"), ("failed", "failed")):
            with self.subTest(state=state):
                connection = self._bound_fixture()
                connection.db.execute("UPDATE processing_runs SET state=?", (state,))
                connection.states = {"ProcessingRun": state, "Source": source_state}
                self.repository._object_storage.content = b"corrupt"
                with self.assertRaisesRegex(DocumentUnderstandingError, "SOURCE_OBJECT_CHECKSUM_MISMATCH"):
                    self.repository.load_source_document(self.context, "source-cp3", processing_run_id="run-cp3")
                self.assertEqual(connection.states, {"ProcessingRun": state, "Source": source_state})
                self.assertFalse(any("transition_canon_state" in sql for sql, _ in connection.statements))

    def test_late_failure_of_terminal_run_never_changes_processing_source(self) -> None:
        for state in ("completed", "failed", "policy_blocked"):
            with self.subTest(state=state):
                connection = self._bound_fixture()
                connection.states = {"ProcessingRun": state, "Source": "processing"}
                self.repository.fail(self.context, "run-cp3", "SOURCE_OBJECT_CHECKSUM_MISMATCH", retryable=False)
                self.assertEqual(connection.states, {"ProcessingRun": state, "Source": "processing"})
                self.assertFalse(any("transition_canon_state" in sql for sql, _ in connection.statements))

    def test_existing_document_active_states_can_still_fail(self) -> None:
        for state in ("accepted", "vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"):
            with self.subTest(state=state):
                connection = self._bound_fixture()
                connection.states = {"ProcessingRun": state, "Source": "processing"}
                self.repository.fail(self.context, "run-cp3", "SOURCE_OBJECT_CHECKSUM_MISMATCH", retryable=False)
                self.assertEqual(connection.states, {"ProcessingRun": "failed", "Source": "failed"})

    def test_bound_original_failures_terminate_run_before_any_model_or_result(self) -> None:
        for kind, expected_code, source_state in (
            ("format", "SOURCE_OBJECT_NOT_READY", "failed"),
            ("digest", "SOURCE_OBJECT_CHECKSUM_MISMATCH", "failed"),
            ("missing", "OBJECT_NOT_FOUND", "failed"),
            ("transient", "OBJECT_STORAGE_UNAVAILABLE", "waiting_model"),
        ):
            with self.subTest(kind=kind):
                connection = self._bound_fixture()
                self.repository._object_storage = FakeObjectStorage()
                if kind == "format":
                    connection.db.execute("UPDATE source_versions SET canonical_json=?", (json.dumps({"filename": "notes.png"}),))
                    connection.db.execute("UPDATE object_records SET content_type='image/png'")
                elif kind == "digest":
                    self.repository._object_storage.content = b"corrupt"
                else:
                    class FailingStorage:
                        def get(self, key):
                            raise ObjectStorageError(expected_code, retryable=kind == "transient")
                    self.repository._object_storage = FailingStorage()
                class NoModel:
                    def resolve(self, *args):
                        raise AssertionError("original failure must precede model resolution")
                service = DocumentProcessingService(self.repository, NoModel(), None)
                with self.assertRaisesRegex(DocumentUnderstandingError, expected_code):
                    service.process_existing(self.context, source_id="source-cp3", processing_run_id="run-cp3")
                self.assertEqual(connection.states, {"ProcessingRun": "failed", "Source": source_state})
                self.assertEqual(connection.inserts, [])

    def test_load_start_and_complete_write_canonical_lineage_in_order(self) -> None:
        document = self.repository.load_source_document(self.context, "source-cp3")
        run_id = self.repository.start(self.context, document.source_version_id)
        result = DocumentUnderstandingResult(
            document.source_id, document.source_version_id, "ready",
            ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
            SemanticUnderstanding("Daon", "Vision first", ("Vision first",)),
            ParserValidation("Vision first", "Vision first", "<p>Vision first</p>", (1, 2)),
            {"provider_code": "UPSTAGE", "parser_role": "validation_only"},
        )
        self.repository.complete(self.context, run_id, result)

        self.assertEqual(document.content, PDF)
        self.assertEqual(self.cloud.connection.states["ProcessingRun"], "completed")
        self.assertEqual(self.cloud.connection.versions["ProcessingRun"], 5)
        self.assertEqual(self.cloud.connection.states["Source"], "processing")
        self.assertEqual(self.cloud.connection.inserts, [
            "processing_runs", "understanding_results",
            "extraction_evidence", "extraction_evidence",
        ])
        self.assertEqual(self.cloud.capabilities, ["source.read", "source.process", "source.process"])

    def test_image_completion_preserves_regions_and_display_lineage_in_canonical_records(self) -> None:
        from test_image_document_understanding import image_bytes
        connection = self.cloud.connection
        connection.filename, connection.content_type, connection.content = "qa.png", "image/png", image_bytes()
        self.repository._object_storage.content = connection.content
        document = self.repository.load_source_document(self.context, "source-cp3")
        run_id = self.repository.start(self.context, document.source_version_id)
        regions = ({"text": "ORANGE-42", "value": "5,7,80,20,120,60"},)
        lineage = {"source_format": "image", "source_digest": hashlib.sha256(document.content).hexdigest(), "image_width": "120", "image_height": "60", "exif_orientation": "1"}
        result = DocumentUnderstandingResult(document.source_id, document.source_version_id, "ready", ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
            SemanticUnderstanding("QA", "ORANGE-42", ("ORANGE-42",)), ParserValidation("ORANGE-42", "", "", (1,), ((1, "ORANGE-42"),), "validation_only", regions), lineage)
        self.repository.complete(self.context, run_id, result)
        payload = next(params[4].obj for sql, params in connection.statements if sql.startswith("INSERT INTO understanding_results"))
        self.assertEqual(payload["source_version_id"], document.source_version_id)
        self.assertEqual(payload["parser_validation"]["regions"], list(regions))
        self.assertEqual(payload["lineage"], lineage)
        self.assertEqual(connection.states["ProcessingRun"], "completed")
        self.assertEqual(connection.states["Source"], "processing", "ready requires actual indexing, not completion alone")

    def test_loads_exact_txt_object_and_rejects_mime_or_digest_mismatch(self) -> None:
        content = b"Actual TXT fact\n"
        self.cloud.connection.filename = "notes.txt"
        self.cloud.connection.content_type = "text/plain"
        self.cloud.connection.content = content
        self.repository._object_storage.content = content
        document = self.repository.load_source_document(self.context, "source-cp3")
        self.assertEqual((document.content, document.content_type), (content, "text/plain"))
        self.cloud.connection.content_type = "application/pdf"
        with self.assertRaisesRegex(Exception, "SOURCE_OBJECT_NOT_READY"):
            self.repository.load_source_document(self.context, "source-cp3")
        self.cloud.connection.content_type = "text/plain"
        self.repository._object_storage.content = b"tampered"
        with self.assertRaisesRegex(Exception, "SOURCE_OBJECT_CHECKSUM_MISMATCH"):
            self.repository.load_source_document(self.context, "source-cp3")

    def test_loads_registered_markdown_and_csv_but_rejects_cross_mime(self) -> None:
        for filename, content_type in (("notes.md", "text/markdown"), ("notes.csv", "text/csv")):
            with self.subTest(filename=filename):
                content = b"ORANGE-COMPASS-42,active\n"
                self.cloud.connection.filename = filename
                self.cloud.connection.content_type = content_type
                self.cloud.connection.content = content
                self.repository._object_storage.content = content
                document = self.repository.load_source_document(self.context, "source-cp3")
                self.assertEqual((document.content, document.content_type), (content, content_type))
                self.cloud.connection.content_type = "text/plain"
                with self.assertRaisesRegex(Exception, "SOURCE_OBJECT_NOT_READY"):
                    self.repository.load_source_document(self.context, "source-cp3")

    def test_failure_transitions_started_run_without_writing_false_result(self) -> None:
        run_id = self.repository.start(self.context, "source-version-cp3")
        self.repository.fail(
            self.context, run_id, "UNDERSTANDING_PROVIDER_UNAVAILABLE", retryable=True,
        )

        self.assertEqual(self.cloud.connection.states["ProcessingRun"], "failed")
        self.assertEqual(self.cloud.connection.states["Source"], "waiting_model")
        self.assertEqual(self.cloud.connection.inserts, ["processing_runs"])

    def test_parser_conflict_moves_source_to_needs_review_instead_of_ready(self) -> None:
        run_id = self.repository.start(self.context, "source-version-cp3")
        result = DocumentUnderstandingResult(
            "source-cp3", "source-version-cp3", "needs_review",
            ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
            SemanticUnderstanding("Daon", "Vision first", ("Unsupported fact",)),
            ParserValidation("Different text", "Different text", "<p>Different text</p>", (1,)),
            {"provider_code": "UPSTAGE", "parser_role": "validation_only"},
            "UNDERSTANDING_PARSER_CONFLICT",
        )

        self.repository.complete(self.context, run_id, result)

        self.assertEqual(self.cloud.connection.states["ProcessingRun"], "completed")
        self.assertEqual(self.cloud.connection.states["Source"], "needs_review")

    def test_async_start_creates_run_and_queue_job_in_the_same_transaction(self) -> None:
        run_id = self.repository.start(
            self.context, "source-version-cp3", enqueue=True,
        )

        self.assertTrue(run_id.startswith("pr-"))
        self.assertEqual(self.cloud.connection.inserts, [
            "processing_runs", "document_processing_jobs",
        ])
        self.assertEqual(self.cloud.capabilities, ["source.process"])

    def test_async_start_is_idempotent_across_upload_request_traces(self) -> None:
        first_run_id = self.repository.start(
            self.context, "source-version-cp3", enqueue=True,
        )
        replay_context = DocumentProcessingContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-replay-cp3", "policy-v1",
        )

        replay_run_id = self.repository.start(
            replay_context, "source-version-cp3", enqueue=True,
        )

        self.assertEqual(replay_run_id, first_run_id)

    def test_async_start_reuses_completed_run_for_ready_digest_replay(self) -> None:
        self.cloud.connection.states["Source"] = "ready"
        self.cloud.connection.states["ProcessingRun"] = "completed"
        run_id = self.repository.start(self.context, "source-version-cp3", enqueue=True)

        self.assertTrue(run_id.startswith("pr-"))
        self.assertEqual(self.cloud.connection.states["Source"], "ready")
        self.assertEqual(self.cloud.connection.states["ProcessingRun"], "completed")
        self.assertEqual(self.cloud.connection.inserts, [
            "processing_runs", "document_processing_jobs",
        ])

    def test_status_is_workspace_scoped_and_omits_worker_lease_identity(self) -> None:
        status = self.repository.get_status(self.context, "run-cp3", notebook_id="notebook-cp3")

        self.assertEqual(status.processing_run_id, "run-cp3")
        self.assertEqual(status.job_state, "leased")
        self.assertFalse(hasattr(status, "lease_owner"))
        self.assertEqual(self.cloud.capabilities, ["source.read"])
        sql, params = next(
            (sql, params) for sql, params in self.cloud.connection.statements
            if sql.startswith("SELECT pr.record_id,sv.source_id,pr.source_version_id")
        )
        self.assertIn("JOIN notebook_bindings", sql)
        self.assertIn("binding_kind='source'", sql)
        self.assertEqual(params, ("notebook-cp3", "run-cp3"))


if __name__ == "__main__":
    unittest.main()
