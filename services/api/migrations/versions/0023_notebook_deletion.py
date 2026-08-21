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
    """)


def downgrade() -> None:
    op.execute("DROP TABLE notebook_deletion_requests")
