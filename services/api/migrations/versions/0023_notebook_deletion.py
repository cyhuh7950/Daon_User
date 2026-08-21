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
        UNIQUE (tenant_id,workspace_id,request_id),
        FOREIGN KEY (tenant_id,workspace_id,notebook_id) REFERENCES notebooks(tenant_id,workspace_id,notebook_id)
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
          THEN
            RAISE EXCEPTION 'DELETE_SHARED_DATA_BLOCKED' USING ERRCODE='55000';
        END IF;
        RETURN QUERY SELECT b.record_id, b.version_id, sv.object_id, o.object_key
          FROM notebook_bindings b LEFT JOIN source_versions sv ON sv.tenant_id=b.tenant_id AND sv.workspace_id=b.workspace_id AND sv.record_id=b.version_id
          LEFT JOIN object_records o ON o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id AND o.object_id=sv.object_id
          WHERE b.tenant_id=p_tenant_id AND b.workspace_id=p_workspace_id AND b.notebook_id=p_notebook_id AND b.binding_kind='source';
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
      END $$;
      REVOKE ALL ON FUNCTION delete_notebook_scope(text,text,text) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION delete_notebook_scope(text,text,text) TO daon_app;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION delete_notebook_scope(text,text,text)")
    op.execute("DROP TABLE notebook_deletion_requests")
