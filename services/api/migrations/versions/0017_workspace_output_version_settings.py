"""Workspace output format defaults and append-only version policy."""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        CREATE TABLE workspace_output_version_settings (
          tenant_id text NOT NULL, workspace_id text NOT NULL,
          default_formats jsonb NOT NULL,
          version_save_mode text NOT NULL DEFAULT 'append_only' CHECK (version_save_mode='append_only'),
          version integer NOT NULL CHECK (version > 0),
          updated_by text NOT NULL, updated_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id,workspace_id),
          FOREIGN KEY (tenant_id,workspace_id) REFERENCES workspaces(tenant_id,workspace_id),
          CHECK (jsonb_typeof(default_formats)='object' AND default_formats ?& ARRAY['evidence_report','compliance_checklist','comparison_table','knowledge_graph','business_draft'])
        );
        CREATE TABLE workspace_output_version_settings_idempotency (
          tenant_id text NOT NULL, workspace_id text NOT NULL, actor_id text NOT NULL,
          idempotency_key text NOT NULL, request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          response_version integer NOT NULL CHECK (response_version > 0), created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id,workspace_id,actor_id,idempotency_key),
          FOREIGN KEY (tenant_id,workspace_id) REFERENCES workspaces(tenant_id,workspace_id)
        );
        CREATE TRIGGER workspace_output_version_settings_idempotency_immutable
          BEFORE UPDATE OR DELETE ON workspace_output_version_settings_idempotency
          FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
    """)
    predicate = "tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')"
    for table in ("workspace_output_version_settings", "workspace_output_version_settings_idempotency"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_scope ON {table} USING ({predicate}) WITH CHECK ({predicate})")
        permissions = "SELECT,INSERT" if table.endswith("_idempotency") else "SELECT,INSERT,UPDATE"
        op.execute(f"GRANT {permissions} ON {table} TO daon_app")


def downgrade() -> None:
    op.execute("DROP TABLE workspace_output_version_settings_idempotency")
    op.execute("DROP TABLE workspace_output_version_settings")
