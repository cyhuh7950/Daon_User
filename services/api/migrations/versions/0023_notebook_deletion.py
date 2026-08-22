"""Durable, scoped Notebook permanent-deletion requests."""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
      CREATE TABLE notebook_deletion_requests (
        request_id text PRIMARY KEY CHECK (request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        notebook_id text NOT NULL,
        actor_id text NOT NULL,
        title_fingerprint text NOT NULL CHECK (title_fingerprint ~ '^[0-9a-f]{64}$'),
        state text NOT NULL CHECK (state IN ('accepted','deleting','completed','failed')),
        current_step text NOT NULL CHECK (current_step ~ '^[a-z][a-z0-9_:-]{0,63}$'),
        attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        safe_error_code text,
        requested_at timestamptz NOT NULL,
        completed_at timestamptz,
        idempotency_key text NOT NULL,
        expected_version bigint NOT NULL CHECK (expected_version > 0),
        request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        UNIQUE (tenant_id,workspace_id,actor_id,idempotency_key),
        UNIQUE (tenant_id,workspace_id,request_id)
      );
      CREATE INDEX notebook_deletion_pending ON notebook_deletion_requests(tenant_id,workspace_id,state,requested_at);
      ALTER TABLE notebook_deletion_requests ENABLE ROW LEVEL SECURITY;
      ALTER TABLE notebook_deletion_requests FORCE ROW LEVEL SECURITY;
      CREATE POLICY notebook_deletion_scope ON notebook_deletion_requests USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );
      GRANT SELECT,INSERT,UPDATE(state,current_step,attempts,safe_error_code,completed_at) ON notebook_deletion_requests TO daon_app;
      CREATE OR REPLACE FUNCTION delete_notebook_scope(p_tenant_id text, p_workspace_id text, p_notebook_id text)
      RETURNS TABLE(source_id text, source_version_id text, object_id text, object_key text)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
      DECLARE
        v_source_ids text[];
        v_source_versions text[];
        v_object_ids text[];
      BEGIN
        IF p_tenant_id IS NULL OR p_workspace_id IS NULL OR p_notebook_id IS NULL THEN
          RAISE EXCEPTION 'NOTEBOOK_SCOPE_INVALID' USING ERRCODE='22023';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM notebooks WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND notebook_id=p_notebook_id) THEN
          RAISE EXCEPTION 'NOTEBOOK_NOT_FOUND' USING ERRCODE='P0002';
        END IF;
        IF EXISTS (
          SELECT 1 FROM notebook_bindings b JOIN notebook_bindings other
            ON other.tenant_id=b.tenant_id AND other.workspace_id=b.workspace_id
            AND other.binding_kind='source' AND other.record_id=b.record_id
            AND (other.notebook_id<>b.notebook_id OR other.version_id IS DISTINCT FROM b.version_id)
          WHERE b.tenant_id=p_tenant_id AND b.workspace_id=p_workspace_id AND b.notebook_id=p_notebook_id AND b.binding_kind='source'
        ) THEN
          RAISE EXCEPTION 'DELETE_SHARED_DATA_BLOCKED' USING ERRCODE='55000';
        END IF;
        IF EXISTS (
          SELECT 1 FROM legal_holds h JOIN notebook_bindings b ON b.tenant_id=h.tenant_id AND b.workspace_id=h.workspace_id AND b.record_id=h.source_id
          WHERE h.tenant_id=p_tenant_id AND h.workspace_id=p_workspace_id AND h.state='active' AND b.notebook_id=p_notebook_id AND b.binding_kind='source'
        ) THEN
          RAISE EXCEPTION 'RETENTION_HOLD' USING ERRCODE='55000';
        END IF;
        IF EXISTS (
          SELECT 1
          FROM source_versions other
          WHERE other.tenant_id=p_tenant_id AND other.workspace_id=p_workspace_id
            AND other.object_id IS NOT NULL
            AND other.object_id IN (
              SELECT selected.object_id FROM source_versions selected
              JOIN notebook_bindings selected_binding
                ON selected_binding.tenant_id=selected.tenant_id
                AND selected_binding.workspace_id=selected.workspace_id
                AND selected_binding.version_id=selected.record_id
              WHERE selected_binding.tenant_id=p_tenant_id
                AND selected_binding.workspace_id=p_workspace_id
                AND selected_binding.notebook_id=p_notebook_id
                AND selected_binding.binding_kind='source'
                AND selected.object_id IS NOT NULL
            )
            AND NOT EXISTS (
              SELECT 1 FROM notebook_bindings other_binding
              WHERE other_binding.tenant_id=other.tenant_id
                AND other_binding.workspace_id=other.workspace_id
                AND other_binding.binding_kind='source'
                AND other_binding.notebook_id=p_notebook_id
                AND other_binding.version_id=other.record_id
            )
          ) THEN
            RAISE EXCEPTION 'DELETE_SHARED_DATA_BLOCKED' USING ERRCODE='55000';
        END IF;
        SELECT coalesce(array_agg(DISTINCT b.record_id), ARRAY[]::text[]),
               coalesce(array_agg(DISTINCT b.version_id), ARRAY[]::text[])
          INTO v_source_ids, v_source_versions
          FROM notebook_bindings b
         WHERE b.tenant_id=p_tenant_id AND b.workspace_id=p_workspace_id
           AND b.notebook_id=p_notebook_id AND b.binding_kind='source';
        SELECT coalesce(array_agg(DISTINCT sv.object_id) FILTER (WHERE sv.object_id IS NOT NULL), ARRAY[]::text[])
          INTO v_object_ids
          FROM source_versions sv
         WHERE sv.tenant_id=p_tenant_id AND sv.workspace_id=p_workspace_id
           AND sv.record_id=ANY(v_source_versions);
        RETURN QUERY SELECT b.record_id, b.version_id, sv.object_id, o.object_key
          FROM notebook_bindings b LEFT JOIN source_versions sv ON sv.tenant_id=b.tenant_id AND sv.workspace_id=b.workspace_id AND sv.record_id=b.version_id
          LEFT JOIN object_records o ON o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id AND o.object_id=sv.object_id
          WHERE b.tenant_id=p_tenant_id AND b.workspace_id=p_workspace_id AND b.notebook_id=p_notebook_id AND b.binding_kind='source';
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
        DELETE FROM document_processing_job_attempts d
         WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id
           AND d.job_id IN (
             SELECT j.job_id FROM document_processing_jobs j
              WHERE j.tenant_id=p_tenant_id AND j.workspace_id=p_workspace_id
                AND j.source_version_id=ANY(v_source_versions)
           );
        DELETE FROM document_processing_jobs d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM knowledge_registrations d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.registered_source_version_id=ANY(v_source_versions);
        DELETE FROM evidence_references d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM citations d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM evidence_spans d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM transcript_segments d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.transcript_version_id IN (SELECT t.record_id FROM transcript_versions t WHERE t.tenant_id=p_tenant_id AND t.workspace_id=p_workspace_id AND t.source_version_id=ANY(v_source_versions));
        DELETE FROM transcript_versions d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM transcription_runs d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM extraction_evidence d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM understanding_results d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM processing_runs d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM index_versions d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.source_version_id=ANY(v_source_versions);
        DELETE FROM sync_target_versions d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.object_id=ANY(v_object_ids);
        WHILE EXISTS (SELECT 1 FROM source_versions WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND record_id=ANY(v_source_versions)) LOOP
          DELETE FROM source_versions sv
           WHERE sv.tenant_id=p_tenant_id AND sv.workspace_id=p_workspace_id AND sv.record_id=ANY(v_source_versions)
             AND NOT EXISTS (SELECT 1 FROM source_versions child WHERE child.tenant_id=sv.tenant_id AND child.workspace_id=sv.workspace_id AND child.previous_version_id=sv.record_id);
          IF NOT FOUND THEN
            RAISE EXCEPTION 'SOURCE_VERSION_DEPENDENCY_BLOCKED' USING ERRCODE='55000';
          END IF;
        END LOOP;
        DELETE FROM sources d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.record_id=ANY(v_source_ids)
          AND NOT EXISTS (SELECT 1 FROM source_versions sv WHERE sv.tenant_id=p_tenant_id AND sv.workspace_id=p_workspace_id AND sv.source_id=d.record_id);
        DELETE FROM job_attempts d
         WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id
           AND d.job_id IN (SELECT j.job_id FROM durable_jobs j JOIN object_outbox_events e
                              ON e.tenant_id=j.tenant_id AND e.workspace_id=j.workspace_id AND e.event_id=j.event_id
                             WHERE j.tenant_id=p_tenant_id AND j.workspace_id=p_workspace_id AND e.object_id=ANY(v_object_ids));
        DELETE FROM durable_jobs d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.event_id IN
          (SELECT e.event_id FROM object_outbox_events e WHERE e.tenant_id=p_tenant_id AND e.workspace_id=p_workspace_id AND e.object_id=ANY(v_object_ids));
        DELETE FROM object_outbox_events d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.object_id=ANY(v_object_ids);
        DELETE FROM object_records d WHERE d.tenant_id=p_tenant_id AND d.workspace_id=p_workspace_id AND d.object_id=ANY(v_object_ids)
          AND NOT EXISTS (SELECT 1 FROM source_versions sv WHERE sv.tenant_id=p_tenant_id AND sv.workspace_id=p_workspace_id AND sv.object_id=d.object_id)
          AND NOT EXISTS (SELECT 1 FROM index_versions iv WHERE iv.tenant_id=p_tenant_id AND iv.workspace_id=p_workspace_id AND iv.object_id=d.object_id);
        ALTER TABLE notebook_source_unbindings DISABLE TRIGGER USER;
        ALTER TABLE notebook_source_unbinding_idempotency DISABLE TRIGGER USER;
        ALTER TABLE notebook_idempotency DISABLE TRIGGER USER;
        ALTER TABLE notebook_activities DISABLE TRIGGER USER;
        ALTER TABLE notebook_bindings DISABLE TRIGGER USER;
        ALTER TABLE notebook_metadata_versions DISABLE TRIGGER USER;
        ALTER TABLE notebooks DISABLE TRIGGER USER;
        DELETE FROM notebook_source_unbinding_idempotency WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND notebook_id=p_notebook_id;
        DELETE FROM notebook_source_unbindings WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND notebook_id=p_notebook_id;
        DELETE FROM notebook_idempotency WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND notebook_id=p_notebook_id;
        DELETE FROM notebook_activities WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND notebook_id=p_notebook_id;
        DELETE FROM notebook_bindings WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND notebook_id=p_notebook_id;
        DELETE FROM notebook_metadata_versions WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND notebook_id=p_notebook_id;
        DELETE FROM notebooks WHERE tenant_id=p_tenant_id AND workspace_id=p_workspace_id AND notebook_id=p_notebook_id;
        ALTER TABLE notebook_source_unbindings ENABLE TRIGGER USER;
        ALTER TABLE notebook_source_unbinding_idempotency ENABLE TRIGGER USER;
        ALTER TABLE notebook_idempotency ENABLE TRIGGER USER;
        ALTER TABLE notebook_activities ENABLE TRIGGER USER;
        ALTER TABLE notebook_bindings ENABLE TRIGGER USER;
        ALTER TABLE notebook_metadata_versions ENABLE TRIGGER USER;
        ALTER TABLE notebooks ENABLE TRIGGER USER;
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
      REVOKE ALL ON FUNCTION delete_notebook_scope(text,text,text) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION delete_notebook_scope(text,text,text) TO daon_app;
      CREATE OR REPLACE FUNCTION claim_notebook_deletion_startup()
      RETURNS TABLE(tenant_id text, workspace_id text, actor_id text, request_id text)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
      BEGIN
        RETURN QUERY
        WITH candidates AS (
          SELECT r.request_id, r.tenant_id, r.workspace_id, r.actor_id
            FROM notebook_deletion_requests r
           WHERE r.state IN ('accepted','deleting')
           ORDER BY r.requested_at, r.request_id
           FOR UPDATE SKIP LOCKED
           LIMIT 32
        )
        UPDATE notebook_deletion_requests d
           SET state='deleting', current_step='claimed', attempts=d.attempts+1
          FROM candidates c
         WHERE d.request_id=c.request_id
           AND d.tenant_id=c.tenant_id
           AND d.workspace_id=c.workspace_id
        RETURNING c.tenant_id, c.workspace_id, c.actor_id, c.request_id;
      END $$;
      REVOKE ALL ON FUNCTION claim_notebook_deletion_startup() FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION claim_notebook_deletion_startup() TO daon_app;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION delete_notebook_scope(text,text,text)")
    op.execute("DROP TABLE notebook_deletion_requests")
