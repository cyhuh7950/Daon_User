"""Real repository SQL/transaction boundary tests; not live PostgreSQL evidence."""

from contextlib import contextmanager
from copy import deepcopy

import pytest

from daon_user_api.document_processing import DocumentProcessingContext
from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Store:
    def __init__(self):
        self.source = ("source-1", "waiting_model", 4, "version-1", "notes.txt", "text/plain")
        self.old_run = ("run-failed-1", "failed", "dead_letter")
        self.active = None
        self.replay = None
        self.calls = []
        self.writes = []
        self.contexts = []
        self.fail_job = False

    @contextmanager
    def _transaction(self, context):
        self.contexts.append(context)
        before = deepcopy(self.writes)
        try:
            yield self
        except Exception:
            self.writes = before
            raise

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("SELECT pg_advisory"):
            return Cursor()
        if sql.startswith("SELECT s.record_id"):
            return Cursor(self.source)
        if sql.startswith("SELECT request_fingerprint,result"):
            return Cursor(self.replay)
        if sql.startswith("SELECT pr.record_id,pr.state,job.state"):
            return Cursor(self.old_run)
        if sql.startswith("SELECT 1 FROM processing_runs"):
            return Cursor(self.active)
        if sql.startswith("SELECT data_area,cost_limit_cents,acl_version"):
            return Cursor(("cloud_sync", 1000, 7))
        if sql.startswith("SELECT state,version,outcome,error_code"):
            self.writes.append(("transition", params))
            return Cursor((params[3], params[2] + 1, "succeeded", None))
        if sql.startswith("INSERT INTO"):
            if sql.startswith("INSERT INTO document_processing_jobs") and self.fail_job:
                raise RuntimeError("fixture job write failed")
            self.writes.append((sql, params))
            return Cursor()
        raise AssertionError(sql)


def retry(store, *, key="retry-1", version="version-1"):
    repository = PostgresDocumentProcessingRepository(store, object())
    assert callable(getattr(repository, "reprocess", None)), "manual reprocessing is missing"
    return repository.reprocess(
        DocumentProcessingContext("tenant-1", "workspace-1", "user-1", "trace-1", "policy-2"),
        "source-1", version, notebook_id="notebook-1", idempotency_key=key,
        authorization_snapshot={"acl_version": 7, "membership_version": 2, "policy_version": "policy-2"},
    )


def test_manual_retry_creates_new_run_job_and_audit_without_mutating_failed_lineage():
    store = Store()
    result = retry(store)
    assert result["processing_run_id"] != "run-failed-1"
    assert result["retry_of_processing_run_id"] == "run-failed-1"
    assert result["source_version_id"] == "version-1"
    assert store.old_run == ("run-failed-1", "failed", "dead_letter")
    run_sql, params = next(item for item in store.writes if item[0].startswith("INSERT INTO processing_runs"))
    assert "retry_of_processing_run_id" in run_sql and "'manual_request'" in run_sql
    payload = next(value.obj for value in params if hasattr(value, "obj"))
    assert payload["policy_snapshot"]["cost_limit_cents"] == 1000
    assert payload["authorization_snapshot"]["acl_version"] == 7
    assert payload["retry_of_processing_run_id"] == "run-failed-1"
    assert len([sql for sql, _ in store.writes if sql.startswith("INSERT INTO document_processing_jobs")]) == 1
    assert len([sql for sql, _ in store.writes if sql.startswith("INSERT INTO audit_events")]) == 1
    assert not any(sql.startswith("UPDATE") or "INSERT INTO source_versions" in sql for sql, _ in store.writes)
    assert store.contexts[0].capability == "source.process"
    policy_sql = next(sql for sql, _ in store.calls if sql.startswith("SELECT data_area"))
    assert "FROM identity_auth_workspaces" in policy_sql


def test_retry_revalidates_scope_binding_latest_version_and_deletion_before_replay():
    store = Store()
    retry(store)
    scope_sql, params = next(item for item in store.calls if item[0].startswith("SELECT s.record_id"))
    for boundary in ("notebook_bindings", "notebook_source_unbindings", "deletion_requests", "notebook_deletion_requests", "ORDER BY sv.version DESC"):
        assert boundary in scope_sql
    assert "tenant-1" in params and "workspace-1" in params and "notebook-1" in params
    for source in (None, ("source-1", "deleted", 4, "version-1", "notes.txt", "text/plain")):
        store = Store()
        store.source = source
        with pytest.raises(DocumentUnderstandingError):
            retry(store)
        assert store.writes == []
    store = Store()
    with pytest.raises(DocumentUnderstandingError, match="SOURCE_VERSION_CONFLICT"):
        retry(store, version="stale-version")
    assert store.writes == []


def test_retry_scope_query_preserves_owner_admin_and_explicit_source_denial():
    store = Store()
    retry(store)
    sql, params = next(item for item in store.calls if item[0].startswith("SELECT s.record_id"))
    assert "s.created_by=%s" in sql
    assert "identity_auth_source_access" in sql and "sa.allowed=0" in sql
    assert "sa.user_id=%s" in sql
    assert params.count("user-1") == 2


def test_retry_replay_and_changed_key_payload_do_not_duplicate_work():
    store = Store()
    result = retry(store)
    _, params = next(item for item in store.writes if item[0].startswith("INSERT INTO idempotency_records"))
    fingerprint = params[5]
    store.replay = (fingerprint, result)
    store.writes.clear()
    assert retry(store)["replayed"] is True
    assert store.writes == []
    store.replay = ("different-request", result)
    with pytest.raises(DocumentUnderstandingError, match="IDEMPOTENCY_KEY_REUSED"):
        retry(store)
    assert store.writes == []


def test_active_duplicate_and_job_write_failure_leave_no_partial_retry():
    store = Store()
    store.active = (1,)
    with pytest.raises(DocumentUnderstandingError, match="SOURCE_PROCESSING_ACTIVE"):
        retry(store)
    assert store.writes == []
    store = Store()
    store.fail_job = True
    with pytest.raises(DocumentUnderstandingError, match="DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE"):
        retry(store)
    assert store.writes == []


def test_retry_rejects_completed_or_still_leased_previous_run_and_unsupported_format():
    for previous in (("run-old", "completed", "completed"), ("run-old", "failed", "leased")):
        store = Store()
        store.old_run = previous
        with pytest.raises(DocumentUnderstandingError, match="SOURCE_REPROCESSING_STATE_INVALID"):
            retry(store)
        assert store.writes == []
    store = Store()
    store.source = ("source-1", "failed", 4, "version-1", "office.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    with pytest.raises(DocumentUnderstandingError, match="SOURCE_OBJECT_NOT_READY"):
        retry(store)
    assert store.writes == []
