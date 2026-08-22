"""Append-only Notebook Source unbinding ledger."""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
      CREATE TABLE notebook_source_unbindings (
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        notebook_id text NOT NULL,
        source_id text NOT NULL,
        source_version_id text NOT NULL,
        binding_version bigint NOT NULL CHECK (binding_version > 1),
        actor_id text NOT NULL,
        occurred_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,workspace_id,notebook_id,source_id,source_version_id),
        FOREIGN KEY (tenant_id,workspace_id,notebook_id)
          REFERENCES notebooks(tenant_id,workspace_id,notebook_id)
      );
      CREATE TABLE notebook_source_unbinding_idempotency (
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        actor_id text NOT NULL,
        idempotency_key text NOT NULL,
        request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        notebook_id text NOT NULL,
        source_id text NOT NULL,
        source_version_id text NOT NULL,
        binding_version bigint NOT NULL,
        created_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,workspace_id,actor_id,idempotency_key),
        FOREIGN KEY (tenant_id,workspace_id,notebook_id,source_id,source_version_id)
          REFERENCES notebook_source_unbindings(tenant_id,workspace_id,notebook_id,source_id,source_version_id)
      );
      CREATE TRIGGER notebook_source_unbindings_immutable BEFORE UPDATE OR DELETE ON notebook_source_unbindings
        FOR EACH ROW EXECUTE FUNCTION reject_notebook_immutable_mutation();
      CREATE TRIGGER notebook_source_unbinding_idempotency_immutable BEFORE UPDATE OR DELETE ON notebook_source_unbinding_idempotency
        FOR EACH ROW EXECUTE FUNCTION reject_notebook_immutable_mutation();
      ALTER TABLE notebook_source_unbindings ENABLE ROW LEVEL SECURITY;
      ALTER TABLE notebook_source_unbindings FORCE ROW LEVEL SECURITY;
      ALTER TABLE notebook_source_unbinding_idempotency ENABLE ROW LEVEL SECURITY;
      ALTER TABLE notebook_source_unbinding_idempotency FORCE ROW LEVEL SECURITY;
      CREATE POLICY notebook_source_unbindings_scope ON notebook_source_unbindings USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );
      CREATE POLICY notebook_source_unbinding_idempotency_scope ON notebook_source_unbinding_idempotency USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );
      ALTER TABLE notebook_activities DROP CONSTRAINT notebook_activities_activity_kind_check;
      ALTER TABLE notebook_activities ADD CONSTRAINT notebook_activities_activity_kind_check
        CHECK (activity_kind IN ('created','title_updated','context_bound','context_unbound'));
      GRANT SELECT,INSERT ON notebook_source_unbindings,notebook_source_unbinding_idempotency TO daon_app;
      REVOKE UPDATE,DELETE ON notebook_source_unbindings,notebook_source_unbinding_idempotency FROM daon_app;
    """)


def downgrade() -> None:
    op.execute(r"""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM notebook_source_unbindings) THEN
          RAISE EXCEPTION 'NOTEBOOK_SOURCE_UNBINDING_DOWNGRADE_BLOCKED' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE notebook_source_unbinding_idempotency;
      DROP TABLE notebook_source_unbindings;
      ALTER TABLE notebook_activities DROP CONSTRAINT notebook_activities_activity_kind_check;
      ALTER TABLE notebook_activities ADD CONSTRAINT notebook_activities_activity_kind_check
        CHECK (activity_kind IN ('created','title_updated','context_bound'));
    """)
