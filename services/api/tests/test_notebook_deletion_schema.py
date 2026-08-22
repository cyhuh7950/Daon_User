from pathlib import Path


def test_notebook_deletion_migration_is_scoped_and_immutable():
    migration = Path(__file__).parents[1] / "migrations/versions/0023_notebook_deletion.py"
    text = migration.read_text(encoding="utf-8")
    assert "notebook_deletion_requests" in text
    assert "state IN ('accepted','deleting','completed','failed')" in text
    assert "UNIQUE (tenant_id,workspace_id,actor_id,idempotency_key)" in text
    assert "ROW LEVEL SECURITY" in text
    assert "GRANT SELECT,INSERT,UPDATE" in text
    assert "DELETE ON notebooks" not in text
    assert "delete_notebook_scope" in text
    assert "SECURITY DEFINER" in text
    assert "DELETE_SHARED_DATA_BLOCKED" in text
    assert "GRANT EXECUTE ON FUNCTION delete_notebook_scope" in text
    assert "claim_notebook_deletion_startup" in text
    assert "FOR UPDATE SKIP LOCKED" in text
    for table in (
        "document_processing_job_attempts", "document_processing_jobs", "knowledge_registrations", "evidence_references",
        "citations", "transcript_segments", "transcript_versions", "transcription_runs",
        "extraction_evidence", "understanding_results", "processing_runs", "index_versions",
        "source_versions", "sources", "object_outbox_events", "object_records",
        "sync_target_versions",
    ):
        assert f"DELETE FROM {table}" in text
    assert "previous_version_id" in text
    assert text.index("DELETE FROM document_processing_job_attempts") < text.index("DELETE FROM document_processing_jobs")
    assert "ALTER TABLE document_processing_job_attempts DISABLE TRIGGER USER" in text
    assert "ALTER TABLE processing_runs DISABLE TRIGGER USER" in text
    for table in (
        "document_processing_jobs", "knowledge_registrations", "evidence_references", "citations",
        "evidence_spans",
        "transcript_segments", "transcript_versions", "transcription_runs", "extraction_evidence",
        "understanding_results", "index_versions", "sync_target_versions", "durable_jobs",
        "object_outbox_events",
        "job_attempts",
    ):
        assert f"ALTER TABLE {table} DISABLE TRIGGER USER" in text
        assert f"ALTER TABLE {table} ENABLE TRIGGER USER" in text
