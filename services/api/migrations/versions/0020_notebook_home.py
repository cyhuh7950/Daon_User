"""Tenant/workspace scoped Notebook Home records."""

from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
      CREATE TABLE notebooks (
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        notebook_id text NOT NULL CHECK (notebook_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        created_by text NOT NULL,
        created_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,workspace_id,notebook_id),
        FOREIGN KEY (tenant_id,workspace_id) REFERENCES workspaces(tenant_id,workspace_id)
      );
      CREATE TABLE notebook_metadata_versions (
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        notebook_id text NOT NULL,
        version bigint NOT NULL CHECK (version > 0),
        title text NOT NULL CHECK (length(title) BETWEEN 1 AND 120 AND title !~ '[[:cntrl:]]'),
        description text CHECK (length(description) <= 500 AND description !~ '[[:cntrl:]]'),
        is_current boolean NOT NULL,
        updated_by text NOT NULL,
        updated_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,workspace_id,notebook_id,version),
        FOREIGN KEY (tenant_id,workspace_id,notebook_id)
          REFERENCES notebooks(tenant_id,workspace_id,notebook_id)
      );
      CREATE UNIQUE INDEX notebook_metadata_one_current
        ON notebook_metadata_versions(tenant_id,workspace_id,notebook_id) WHERE is_current;
      CREATE TABLE notebook_bindings (
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        notebook_id text NOT NULL,
        binding_kind text NOT NULL CHECK (binding_kind IN ('source','knowledge_context','conversation_thread','studio_output','output_version','generation_settings')),
        record_id text NOT NULL,
        version_id text,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,workspace_id,notebook_id,binding_kind,record_id),
        FOREIGN KEY (tenant_id,workspace_id,notebook_id)
          REFERENCES notebooks(tenant_id,workspace_id,notebook_id)
      );
      CREATE TABLE notebook_activities (
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        notebook_id text NOT NULL,
        sequence bigint NOT NULL CHECK (sequence > 0),
        activity_kind text NOT NULL CHECK (activity_kind IN ('created','title_updated','context_bound')),
        actor_id text NOT NULL,
        occurred_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,workspace_id,notebook_id,sequence),
        FOREIGN KEY (tenant_id,workspace_id,notebook_id)
          REFERENCES notebooks(tenant_id,workspace_id,notebook_id)
      );
      CREATE TABLE notebook_idempotency (
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        actor_id text NOT NULL,
        idempotency_key text NOT NULL,
        action text NOT NULL CHECK (action IN ('create','update_title')),
        request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        notebook_id text NOT NULL,
        metadata_version bigint NOT NULL,
        created_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,workspace_id,actor_id,idempotency_key),
        FOREIGN KEY (tenant_id,workspace_id,notebook_id,metadata_version)
          REFERENCES notebook_metadata_versions(tenant_id,workspace_id,notebook_id,version)
      );

      CREATE OR REPLACE FUNCTION reject_notebook_immutable_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'NOTEBOOK_IMMUTABLE' USING ERRCODE='55000'; END $$;
      CREATE OR REPLACE FUNCTION allow_notebook_metadata_current_transition() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='UPDATE' AND OLD.is_current=true AND NEW.is_current=false
          AND OLD.tenant_id=NEW.tenant_id AND OLD.workspace_id=NEW.workspace_id
          AND OLD.notebook_id=NEW.notebook_id AND OLD.version=NEW.version
          AND OLD.title=NEW.title AND OLD.description IS NOT DISTINCT FROM NEW.description
          AND OLD.updated_by=NEW.updated_by AND OLD.updated_at=NEW.updated_at THEN
          RETURN NEW;
        END IF;
        RAISE EXCEPTION 'NOTEBOOK_METADATA_IMMUTABLE' USING ERRCODE='55000';
      END $$;
      CREATE TRIGGER notebooks_immutable BEFORE UPDATE OR DELETE ON notebooks
        FOR EACH ROW EXECUTE FUNCTION reject_notebook_immutable_mutation();
      CREATE TRIGGER notebook_bindings_immutable BEFORE UPDATE OR DELETE ON notebook_bindings
        FOR EACH ROW EXECUTE FUNCTION reject_notebook_immutable_mutation();
      CREATE TRIGGER notebook_activities_immutable BEFORE UPDATE OR DELETE ON notebook_activities
        FOR EACH ROW EXECUTE FUNCTION reject_notebook_immutable_mutation();
      CREATE TRIGGER notebook_idempotency_immutable BEFORE UPDATE OR DELETE ON notebook_idempotency
        FOR EACH ROW EXECUTE FUNCTION reject_notebook_immutable_mutation();
      CREATE TRIGGER notebook_metadata_immutable BEFORE UPDATE OR DELETE ON notebook_metadata_versions
        FOR EACH ROW EXECUTE FUNCTION allow_notebook_metadata_current_transition();

      ALTER TABLE notebooks ENABLE ROW LEVEL SECURITY;
      ALTER TABLE notebooks FORCE ROW LEVEL SECURITY;
      ALTER TABLE notebook_metadata_versions ENABLE ROW LEVEL SECURITY;
      ALTER TABLE notebook_metadata_versions FORCE ROW LEVEL SECURITY;
      ALTER TABLE notebook_bindings ENABLE ROW LEVEL SECURITY;
      ALTER TABLE notebook_bindings FORCE ROW LEVEL SECURITY;
      ALTER TABLE notebook_activities ENABLE ROW LEVEL SECURITY;
      ALTER TABLE notebook_activities FORCE ROW LEVEL SECURITY;
      ALTER TABLE notebook_idempotency ENABLE ROW LEVEL SECURITY;
      ALTER TABLE notebook_idempotency FORCE ROW LEVEL SECURITY;

      CREATE POLICY notebooks_scope ON notebooks USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND
        workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND
        workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );
      CREATE POLICY notebook_metadata_scope ON notebook_metadata_versions USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND
        workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND
        workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );
      CREATE POLICY notebook_bindings_scope ON notebook_bindings USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );
      CREATE POLICY notebook_activities_scope ON notebook_activities USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );
      CREATE POLICY notebook_idempotency_scope ON notebook_idempotency USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );

      GRANT SELECT,INSERT ON notebooks,notebook_metadata_versions,notebook_bindings,notebook_activities,notebook_idempotency TO daon_app;
      GRANT UPDATE(is_current) ON notebook_metadata_versions TO daon_app;
      REVOKE UPDATE,DELETE ON notebooks,notebook_bindings,notebook_activities,notebook_idempotency FROM daon_app;
      REVOKE DELETE ON notebook_metadata_versions FROM daon_app;
    """)


def downgrade() -> None:
    op.execute(r"""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM notebooks) OR EXISTS (SELECT 1 FROM notebook_bindings) THEN
          RAISE EXCEPTION 'NOTEBOOK_DOWNGRADE_BLOCKED' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE notebook_idempotency;
      DROP TABLE notebook_activities;
      DROP TABLE notebook_bindings;
      DROP TABLE notebook_metadata_versions;
      DROP TABLE notebooks;
      DROP FUNCTION allow_notebook_metadata_current_transition();
      DROP FUNCTION reject_notebook_immutable_mutation();
    """)
