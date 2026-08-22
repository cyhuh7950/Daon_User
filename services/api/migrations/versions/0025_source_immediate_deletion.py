"""Immediate, scoped Source deletion for the Notebook Source contract."""

from alembic import op


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
      CREATE OR REPLACE FUNCTION delete_source_scope(
        p_tenant_id text, p_workspace_id text, p_source_id text
      ) RETURNS TABLE(object_key text)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
      DECLARE
        v_source_versions text[];
        v_object_ids text[];
      BEGIN
        IF p_tenant_id IS NULL OR p_workspace_id IS NULL OR p_source_id IS NULL THEN
          RAISE EXCEPTION 'SOURCE_SCOPE_INVALID' USING ERRCODE='22023';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM sources
           WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND record_id=p_source_id
        ) THEN
          RAISE EXCEPTION 'SOURCE_NOT_FOUND' USING ERRCODE='P0002';
        END IF;

        SELECT coalesce(array_agg(record_id), ARRAY[]::text[]),
               coalesce(array_agg(DISTINCT object_id) FILTER (WHERE object_id IS NOT NULL), ARRAY[]::text[])
          INTO v_source_versions, v_object_ids
          FROM source_versions
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_id=p_source_id;

        RETURN QUERY
          SELECT DISTINCT o.object_key
            FROM object_records o
           WHERE o.tenant_id=p_tenant_id AND o.workspace_id=p_workspace_id
             AND o.object_id=ANY(v_object_ids) AND o.object_key IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM source_versions sv
                WHERE sv.tenant_id=p_tenant_id AND sv.workspace_id=p_workspace_id
                  AND sv.object_id=o.object_id AND sv.source_id <> p_source_id
             )
             AND NOT EXISTS (
               SELECT 1 FROM index_versions iv
                WHERE iv.tenant_id=p_tenant_id AND iv.workspace_id=p_workspace_id
                  AND iv.object_id=o.object_id
             );

        -- This function is the single privileged database boundary for a user
        -- confirmed Source deletion.  The API deletes returned object keys
        -- through the existing server-only ObjectStoragePort after commit.
        ALTER TABLE external_references DISABLE TRIGGER USER;
        ALTER TABLE source_retention_locator DISABLE TRIGGER USER;
        ALTER TABLE notebook_source_unbindings DISABLE TRIGGER USER;
        ALTER TABLE notebook_source_unbinding_idempotency DISABLE TRIGGER USER;
        ALTER TABLE notebook_bindings DISABLE TRIGGER USER;
        ALTER TABLE sources DISABLE TRIGGER USER;
        ALTER TABLE source_versions DISABLE TRIGGER USER;
        ALTER TABLE object_records DISABLE TRIGGER USER;
        ALTER TABLE document_processing_job_attempts DISABLE TRIGGER USER;
        ALTER TABLE document_processing_jobs DISABLE TRIGGER USER;
        ALTER TABLE processing_runs DISABLE TRIGGER USER;
        ALTER TABLE knowledge_registrations DISABLE TRIGGER USER;
        ALTER TABLE evidence_references DISABLE TRIGGER USER;
        ALTER TABLE citations DISABLE TRIGGER USER;
        ALTER TABLE evidence_spans DISABLE TRIGGER USER;
        ALTER TABLE transcript_segments DISABLE TRIGGER USER;
        ALTER TABLE transcript_versions DISABLE TRIGGER USER;
        ALTER TABLE transcription_runs DISABLE TRIGGER USER;
        ALTER TABLE extraction_evidence DISABLE TRIGGER USER;
        ALTER TABLE understanding_results DISABLE TRIGGER USER;
        ALTER TABLE index_versions DISABLE TRIGGER USER;
        ALTER TABLE sync_target_versions DISABLE TRIGGER USER;
        ALTER TABLE durable_jobs DISABLE TRIGGER USER;
        ALTER TABLE object_outbox_events DISABLE TRIGGER USER;
        ALTER TABLE job_attempts DISABLE TRIGGER USER;

        DELETE FROM external_references
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_id=p_source_id;
        DELETE FROM source_retention_locator
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_id=p_source_id;
        DELETE FROM document_processing_job_attempts
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id
           AND job_id IN (SELECT job_id FROM document_processing_jobs
                           WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id
                             AND source_version_id=ANY(v_source_versions));
        DELETE FROM document_processing_jobs
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM knowledge_registrations
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND registered_source_version_id=ANY(v_source_versions);
        DELETE FROM evidence_references
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM citations
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM evidence_spans
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM transcript_segments
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id
           AND transcript_version_id IN (SELECT record_id FROM transcript_versions
                                          WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id
                                            AND source_version_id=ANY(v_source_versions));
        DELETE FROM transcript_versions
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM transcription_runs
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM extraction_evidence
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM understanding_results
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM processing_runs
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM index_versions
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_version_id=ANY(v_source_versions);
        DELETE FROM sync_target_versions
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND object_id=ANY(v_object_ids);
        DELETE FROM source_versions
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND record_id=ANY(v_source_versions);
        DELETE FROM sources
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND record_id=p_source_id;
        DELETE FROM job_attempts
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id
           AND job_id IN (SELECT j.job_id FROM durable_jobs j JOIN object_outbox_events e
                            ON e.tenant_id=j.tenant_id AND e.workspace_id=j.workspace_id AND e.event_id=j.event_id
                           WHERE j.tenant_id=p_tenant_id AND j.workspace_id=p_workspace_id
                             AND e.object_id=ANY(v_object_ids));
        DELETE FROM durable_jobs
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND event_id IN
           (SELECT event_id FROM object_outbox_events
             WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND object_id=ANY(v_object_ids));
        DELETE FROM object_outbox_events
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND object_id=ANY(v_object_ids);
        DELETE FROM object_records
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND object_id=ANY(v_object_ids)
           AND NOT EXISTS (SELECT 1 FROM source_versions sv
                            WHERE sv.tenant_id=p_tenant_id AND sv.workspace_id=p_workspace_id AND sv.object_id=object_records.object_id)
           AND NOT EXISTS (SELECT 1 FROM index_versions iv
                            WHERE iv.tenant_id=p_tenant_id AND iv.workspace_id=p_workspace_id AND iv.object_id=object_records.object_id);
        DELETE FROM notebook_source_unbinding_idempotency
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_id=p_source_id;
        DELETE FROM notebook_source_unbindings
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND source_id=p_source_id;
        DELETE FROM notebook_bindings
         WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id
           AND binding_kind='source' AND record_id=p_source_id;

        ALTER TABLE external_references ENABLE TRIGGER USER;
        ALTER TABLE source_retention_locator ENABLE TRIGGER USER;
        ALTER TABLE notebook_source_unbindings ENABLE TRIGGER USER;
        ALTER TABLE notebook_source_unbinding_idempotency ENABLE TRIGGER USER;
        ALTER TABLE notebook_bindings ENABLE TRIGGER USER;
        ALTER TABLE sources ENABLE TRIGGER USER;
        ALTER TABLE source_versions ENABLE TRIGGER USER;
        ALTER TABLE object_records ENABLE TRIGGER USER;
        ALTER TABLE document_processing_job_attempts ENABLE TRIGGER USER;
        ALTER TABLE document_processing_jobs ENABLE TRIGGER USER;
        ALTER TABLE processing_runs ENABLE TRIGGER USER;
        ALTER TABLE knowledge_registrations ENABLE TRIGGER USER;
        ALTER TABLE evidence_references ENABLE TRIGGER USER;
        ALTER TABLE citations ENABLE TRIGGER USER;
        ALTER TABLE evidence_spans ENABLE TRIGGER USER;
        ALTER TABLE transcript_segments ENABLE TRIGGER USER;
        ALTER TABLE transcript_versions ENABLE TRIGGER USER;
        ALTER TABLE transcription_runs ENABLE TRIGGER USER;
        ALTER TABLE extraction_evidence ENABLE TRIGGER USER;
        ALTER TABLE understanding_results ENABLE TRIGGER USER;
        ALTER TABLE index_versions ENABLE TRIGGER USER;
        ALTER TABLE sync_target_versions ENABLE TRIGGER USER;
        ALTER TABLE durable_jobs ENABLE TRIGGER USER;
        ALTER TABLE object_outbox_events ENABLE TRIGGER USER;
        ALTER TABLE job_attempts ENABLE TRIGGER USER;
      END $$;
      REVOKE ALL ON FUNCTION delete_source_scope(text,text,text) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION delete_source_scope(text,text,text) TO daon_app;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS delete_source_scope(text,text,text)")
