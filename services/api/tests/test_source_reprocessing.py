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
        self.review_result = {'source_id': 'source-1', 'source_version_id': 'version-1',
            'status': 'needs_review', 'conflict': 'UNDERSTANDING_PARSER_CONFLICT',
            'lineage': {'source_format': 'docx', 'reconciliation': 'mismatch'}}

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
        if sql.startswith("SELECT ur.canonical_json"):
            return Cursor((self.review_result,)) if self.review_result is not None else Cursor()
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


def test_failed_wav_retry_preserves_original_run_and_starts_asr_state():
    store = Store()
    store.source = ('source-1', 'waiting_model', 4, 'version-1', 'memo.wav', 'audio/wav')
    result = retry(store)
    assert result['processing_state'] == 'speech_to_text'
    assert result['retry_of_processing_run_id'] == 'run-failed-1'
    assert store.old_run == ('run-failed-1', 'failed', 'dead_letter')
    payload = next(value.obj for sql, params in store.writes if sql.startswith('INSERT INTO processing_runs') for value in params if hasattr(value, 'obj'))
    assert payload['modality'] == 'audio'
    assert payload['asr_then_llm'] is True
    assert 'vision_llm_first' not in payload
    assert not any(sql.startswith('UPDATE') or 'INSERT INTO source_versions' in sql for sql, _ in store.writes)


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
    store.source = ("source-1", "failed", 4, "version-1", "office.odp", "application/vnd.oasis.opendocument.presentation")
    with pytest.raises(DocumentUnderstandingError, match="SOURCE_OBJECT_NOT_READY"):
        retry(store)
    assert store.writes == []


def test_xlsx_manual_retry_preserves_six_column_source_failed_run_and_exact_replay():
    store = Store()
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    store.source = ("source-1", "waiting_model", 4, "version-1", "qa.xlsx", mime)
    result = retry(store)
    assert result["source_version_id"] == "version-1"
    assert result["processing_run_id"] != "run-failed-1"
    assert result["retry_of_processing_run_id"] == "run-failed-1"
    assert store.old_run == ("run-failed-1", "failed", "dead_letter")
    _, run_params = next(item for item in store.writes if item[0].startswith("INSERT INTO processing_runs"))
    payload = next(value.obj for value in run_params if hasattr(value, "obj"))
    assert payload["source_version_id"] == "version-1"
    assert payload["trigger_type"] == "manual_request"
    assert payload["retry_of_processing_run_id"] == "run-failed-1"
    assert len([sql for sql, _ in store.writes if sql.startswith("INSERT INTO document_processing_jobs")]) == 1
    assert not any(sql.startswith("UPDATE") or "INSERT INTO source_versions" in sql for sql, _ in store.writes)
    _, receipt_params = next(item for item in store.writes if item[0].startswith("INSERT INTO idempotency_records"))
    store.replay = (receipt_params[5], result)
    store.writes.clear()
    assert retry(store)["replayed"] is True
    assert store.writes == []
    store.replay = ("different-request", result)
    with pytest.raises(DocumentUnderstandingError, match="IDEMPOTENCY_KEY_REUSED"):
        retry(store)
    assert store.writes == []


def test_pptx_manual_retry_preserves_six_columns_failed_run_lineage_and_replay():
    store = Store()
    store.source = ('source-1', 'failed', 4, 'version-1', 'qa.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation')
    result = retry(store)
    assert result['source_version_id'] == 'version-1'
    assert result['processing_run_id'] != 'run-failed-1'
    assert result['retry_of_processing_run_id'] == 'run-failed-1'
    assert store.old_run == ('run-failed-1', 'failed', 'dead_letter')
    _, run_params = next(item for item in store.writes if item[0].startswith('INSERT INTO processing_runs'))
    payload = next(value.obj for value in run_params if hasattr(value, 'obj'))
    assert payload['trigger_type'] == 'manual_request'
    assert payload['retry_of_processing_run_id'] == 'run-failed-1'
    assert len([sql for sql, _ in store.writes if sql.startswith('INSERT INTO document_processing_jobs')]) == 1
    assert not any(sql.startswith('UPDATE') or 'INSERT INTO source_versions' in sql for sql, _ in store.writes)
    _, receipt = next(item for item in store.writes if item[0].startswith('INSERT INTO idempotency_records'))
    store.replay = (receipt[5], result); store.writes.clear()
    assert retry(store)['replayed'] is True
    assert store.writes == []
    store.replay = ('wrong-request', result)
    with pytest.raises(DocumentUnderstandingError, match='IDEMPOTENCY_KEY_REUSED'): retry(store)
    assert store.writes == []


def pptx_review_store():
    store = Store()
    store.source = ('source-1', 'needs_review', 4, 'version-1', 'qa.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation')
    store.old_run = ('run-review-1', 'completed', 'completed')
    store.review_result['lineage']['source_format'] = 'pptx'
    return store


def test_pptx_completed_review_retry_creates_new_lineage_and_exact_replay_without_reset():
    store = pptx_review_store()
    original = deepcopy((store.source, store.old_run, store.review_result))
    result = retry(store)
    assert result['retry_of_processing_run_id'] == 'run-review-1'
    assert result['processing_run_id'] != 'run-review-1'
    assert (store.source, store.old_run, store.review_result) == original
    assert len([sql for sql, _ in store.writes if sql.startswith('INSERT INTO processing_runs')]) == 1
    assert len([sql for sql, _ in store.writes if sql.startswith('INSERT INTO document_processing_jobs')]) == 1
    assert not any(sql.startswith('UPDATE') or 'INSERT INTO source_versions' in sql or 'INSERT INTO understanding_results' in sql for sql, _ in store.writes)
    _, receipt = next(item for item in store.writes if item[0].startswith('INSERT INTO idempotency_records'))
    store.replay = (receipt[5], result); store.writes.clear(); store.active = (1,)
    assert retry(store)['replayed'] is True
    assert store.writes == []
    store.replay = None
    with pytest.raises(DocumentUnderstandingError, match='SOURCE_PROCESSING_ACTIVE'): retry(store, key='different')
    assert store.writes == []


def test_pptx_review_result_query_executes_exact_scoped_run_version_source_join():
    import json
    import sqlite3
    store = pptx_review_store()
    retry(store)
    sql, params = next(item for item in store.calls if item[0].startswith('SELECT ur.canonical_json'))
    assert params == ('tenant-1', 'workspace-1', 'run-review-1', 'version-1', 'source-1')
    with sqlite3.connect(':memory:') as connection:
        connection.execute('CREATE TABLE source_versions (tenant_id TEXT, workspace_id TEXT, record_id TEXT, source_id TEXT)')
        connection.execute('CREATE TABLE understanding_results (tenant_id TEXT, workspace_id TEXT, processing_run_id TEXT, source_version_id TEXT, canonical_json TEXT)')
        connection.execute('INSERT INTO source_versions VALUES (?,?,?,?)', ('tenant-1','workspace-1','version-1','source-1'))
        connection.execute('INSERT INTO understanding_results VALUES (?,?,?,?,?)',
                           ('tenant-1','workspace-1','run-review-1','version-1',json.dumps(store.review_result)))
        assert json.loads(connection.execute(sql.replace('%s','?'), params).fetchone()[0]) == store.review_result
        for index, wrong in enumerate(('other-tenant','other-workspace','other-run','old-version','other-source')):
            changed = list(params); changed[index] = wrong
            assert connection.execute(sql.replace('%s','?'), changed).fetchone() is None


@pytest.mark.parametrize('scenario', ['no-result','wrong-result-source','wrong-result-version','not-conflict',
    'not-mismatch','not-pptx-lineage','ready-result','ready-source','failed-run','leased-job',
    'active','old-version','denied','xlsx','pdf','text','image','wrong-mime'])
def test_pptx_review_retry_rejects_unbound_or_nonapproved_state_without_writes(scenario):
    store = pptx_review_store()
    version = 'version-1'
    if scenario == 'no-result': store.review_result = None
    if scenario == 'wrong-result-source': store.review_result['source_id'] = 'other'
    if scenario == 'wrong-result-version': store.review_result['source_version_id'] = 'old'
    if scenario == 'not-conflict': store.review_result['conflict'] = None
    if scenario == 'not-mismatch': store.review_result['lineage']['reconciliation'] = 'matched'
    if scenario == 'not-pptx-lineage': store.review_result['lineage']['source_format'] = 'xlsx'
    if scenario == 'ready-result': store.review_result['status'] = 'ready'
    if scenario == 'ready-source': store.source = (store.source[0], 'ready', *store.source[2:])
    if scenario == 'failed-run': store.old_run = ('run-review-1', 'failed', 'completed')
    if scenario == 'leased-job': store.old_run = ('run-review-1', 'completed', 'leased')
    if scenario == 'active': store.active = (1,)
    if scenario == 'old-version': version = 'old'
    if scenario == 'denied': store.source = None
    formats = {'xlsx': ('qa.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        'pdf': ('qa.pdf','application/pdf'), 'text': ('qa.txt','text/plain'), 'image': ('qa.png','image/png'),
        'wrong-mime': ('qa.pptx','text/plain')}
    if scenario in formats: store.source = (*store.source[:4], *formats[scenario])
    with pytest.raises(DocumentUnderstandingError): retry(store, version=version)
    assert store.writes == []

def test_docx_manual_retry_preserves_six_columns_failed_run_lineage_and_replay():
    store = Store()
    store.source = ('source-1', 'failed', 4, 'version-1', 'qa.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    result = retry(store)
    assert result['source_version_id'] == 'version-1'
    assert result['processing_run_id'] != 'run-failed-1'
    assert result['retry_of_processing_run_id'] == 'run-failed-1'
    assert store.old_run == ('run-failed-1', 'failed', 'dead_letter')
    _, run_params = next(item for item in store.writes if item[0].startswith('INSERT INTO processing_runs'))
    payload = next(value.obj for value in run_params if hasattr(value, 'obj'))
    assert payload['trigger_type'] == 'manual_request'
    assert payload['retry_of_processing_run_id'] == 'run-failed-1'
    assert len([sql for sql, _ in store.writes if sql.startswith('INSERT INTO document_processing_jobs')]) == 1
    assert not any(sql.startswith('UPDATE') or 'INSERT INTO source_versions' in sql for sql, _ in store.writes)
    _, receipt = next(item for item in store.writes if item[0].startswith('INSERT INTO idempotency_records'))
    store.replay = (receipt[5], result); store.writes.clear()
    assert retry(store)['replayed'] is True
    assert store.writes == []
    store.replay = ('wrong-request', result)
    with pytest.raises(DocumentUnderstandingError, match='IDEMPOTENCY_KEY_REUSED'): retry(store)
    assert store.writes == []


def review_store():
    store = Store()
    store.source = ('source-1', 'needs_review', 4, 'version-1', 'qa.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    store.old_run = ('run-review-1', 'completed', 'completed')
    return store


def test_docx_completed_review_retry_creates_new_lineage_and_exact_replay_without_reset():
    store = review_store()
    original = deepcopy((store.source, store.old_run, store.review_result))
    result = retry(store)
    assert result['retry_of_processing_run_id'] == 'run-review-1'
    assert result['processing_run_id'] != 'run-review-1'
    assert (store.source, store.old_run, store.review_result) == original
    assert len([sql for sql, _ in store.writes if sql.startswith('INSERT INTO processing_runs')]) == 1
    assert len([sql for sql, _ in store.writes if sql.startswith('INSERT INTO document_processing_jobs')]) == 1
    assert not any(sql.startswith('UPDATE') or 'INSERT INTO source_versions' in sql or 'INSERT INTO understanding_results' in sql for sql, _ in store.writes)
    _, receipt = next(item for item in store.writes if item[0].startswith('INSERT INTO idempotency_records'))
    store.replay = (receipt[5], result); store.writes.clear(); store.active = (1,)
    assert retry(store)['replayed'] is True
    assert store.writes == []
    store.replay = None
    with pytest.raises(DocumentUnderstandingError, match='SOURCE_PROCESSING_ACTIVE'): retry(store, key='different')
    assert store.writes == []


def test_docx_review_result_query_executes_exact_scoped_run_version_source_join():
    import json
    import sqlite3
    store = review_store()
    retry(store)
    sql, params = next(item for item in store.calls if item[0].startswith('SELECT ur.canonical_json'))
    assert params == ('tenant-1', 'workspace-1', 'run-review-1', 'version-1', 'source-1')
    with sqlite3.connect(':memory:') as connection:
        connection.execute('CREATE TABLE source_versions (tenant_id TEXT, workspace_id TEXT, record_id TEXT, source_id TEXT)')
        connection.execute('CREATE TABLE understanding_results (tenant_id TEXT, workspace_id TEXT, processing_run_id TEXT, source_version_id TEXT, canonical_json TEXT)')
        connection.execute('INSERT INTO source_versions VALUES (?,?,?,?)', ('tenant-1','workspace-1','version-1','source-1'))
        connection.execute('INSERT INTO understanding_results VALUES (?,?,?,?,?)',
                           ('tenant-1','workspace-1','run-review-1','version-1',json.dumps(store.review_result)))
        assert json.loads(connection.execute(sql.replace('%s','?'), params).fetchone()[0]) == store.review_result
        for index, wrong in enumerate(('other-tenant','other-workspace','other-run','old-version','other-source')):
            changed = list(params); changed[index] = wrong
            assert connection.execute(sql.replace('%s','?'), changed).fetchone() is None


@pytest.mark.parametrize('scenario', ['no-result','wrong-result-source','wrong-result-version','not-conflict',
    'not-mismatch','not-docx-lineage','ready-result','ready-source','failed-run','leased-job',
    'active','old-version','denied','xlsx','pdf','text','image','wrong-mime'])
def test_docx_review_retry_rejects_unbound_or_nonapproved_state_without_writes(scenario):
    store = review_store()
    version = 'version-1'
    if scenario == 'no-result': store.review_result = None
    if scenario == 'wrong-result-source': store.review_result['source_id'] = 'other'
    if scenario == 'wrong-result-version': store.review_result['source_version_id'] = 'old'
    if scenario == 'not-conflict': store.review_result['conflict'] = None
    if scenario == 'not-mismatch': store.review_result['lineage']['reconciliation'] = 'matched'
    if scenario == 'not-docx-lineage': store.review_result['lineage']['source_format'] = 'xlsx'
    if scenario == 'ready-result': store.review_result['status'] = 'ready'
    if scenario == 'ready-source': store.source = (store.source[0], 'ready', *store.source[2:])
    if scenario == 'failed-run': store.old_run = ('run-review-1', 'failed', 'completed')
    if scenario == 'leased-job': store.old_run = ('run-review-1', 'completed', 'leased')
    if scenario == 'active': store.active = (1,)
    if scenario == 'old-version': version = 'old'
    if scenario == 'denied': store.source = None
    formats = {'xlsx': ('qa.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        'pdf': ('qa.pdf','application/pdf'), 'text': ('qa.txt','text/plain'), 'image': ('qa.png','image/png'),
        'wrong-mime': ('qa.docx','text/plain')}
    if scenario in formats: store.source = (*store.source[:4], *formats[scenario])
    with pytest.raises(DocumentUnderstandingError): retry(store, version=version)
    assert store.writes == []


@pytest.mark.parametrize('filename,mime', [('qa.docx', 'text/plain'), ('qa.xlsx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')])
def test_docx_retry_cross_format_and_mime_have_zero_writes(filename, mime):
    store = Store(); store.source = ('source-1', 'failed', 4, 'version-1', filename, mime)
    with pytest.raises(DocumentUnderstandingError, match='SOURCE_OBJECT_NOT_READY'): retry(store)
    assert store.writes == []


@pytest.mark.parametrize("scenario,code", [
    ("wrong-mime", "SOURCE_OBJECT_NOT_READY"), ("wrong-filename", "SOURCE_OBJECT_NOT_READY"),
    ("missing-source", "SOURCE_NOT_FOUND"), ("disabled", "SOURCE_NOT_FOUND"),
    ("wrong-version", "SOURCE_VERSION_CONFLICT"), ("active", "SOURCE_PROCESSING_ACTIVE"),
    ("completed", "SOURCE_REPROCESSING_STATE_INVALID"), ("leased", "SOURCE_REPROCESSING_STATE_INVALID"),
])
def test_xlsx_manual_retry_rejects_format_scope_version_and_terminal_mismatch_without_writes(scenario, code):
    store = Store()
    store.source = ("source-1", "failed", 4, "version-1", "qa.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if scenario == "wrong-mime": store.source = (*store.source[:5], "text/plain")
    if scenario == "wrong-filename": store.source = (*store.source[:4], "qa.docx", store.source[5])
    if scenario == "missing-source": store.source = None
    if scenario == "disabled": store.source = (store.source[0], "disabled", *store.source[2:])
    if scenario == "active": store.active = (1,)
    if scenario == "completed": store.old_run = ("run-failed-1", "completed", "completed")
    if scenario == "leased": store.old_run = ("run-failed-1", "failed", "leased")
    with pytest.raises(DocumentUnderstandingError, match=code):
        retry(store, version="version-other" if scenario == "wrong-version" else "version-1")
    assert store.writes == []
