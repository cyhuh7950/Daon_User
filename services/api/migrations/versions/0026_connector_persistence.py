"""Persist workspace-scoped Connector registrations and status."""

from alembic import op


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE workspace_connectors (
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        connector_id text NOT NULL,
        kind text NOT NULL,
        name text NOT NULL,
        endpoint_label text NOT NULL,
        status text NOT NULL CHECK (status IN ('connected','disconnected','unavailable')),
        source_count integer NOT NULL DEFAULT 0 CHECK (source_count >= 0),
        sources_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(sources_json) = 'array'),
        last_checked_at timestamptz,
        error_code text,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id, workspace_id, connector_id)
      );
      CREATE INDEX workspace_connectors_scope_idx
        ON workspace_connectors (tenant_id, workspace_id, updated_at DESC);
      GRANT SELECT,INSERT,UPDATE,DELETE ON workspace_connectors TO daon_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workspace_connectors")
