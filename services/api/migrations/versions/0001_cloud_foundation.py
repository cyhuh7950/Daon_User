"""Cloud canonical storage, pgvector, constraints and forced RLS foundation."""

from __future__ import annotations

from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


TENANT_TABLES = (
    "tenants", "workspaces", "user_accounts", "memberships", "devices", "sessions",
    "authorization_policies", "authorization_bindings", "audit_events",
    "idempotency_records", "notifications", "inbox_requests", "vector_entries",
)
WORKSPACE_TABLES = (
    "workspaces", "memberships", "authorization_policies", "authorization_bindings",
    "audit_events", "idempotency_records", "notifications", "inbox_requests", "vector_entries",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'daon_app') THEN
            CREATE ROLE daon_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
        END $$;

        CREATE TABLE tenants (
          tenant_id text PRIMARY KEY CHECK (tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
          display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 160),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE workspaces (
          tenant_id text NOT NULL REFERENCES tenants(tenant_id),
          workspace_id text NOT NULL,
          display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 160),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id),
          UNIQUE (workspace_id, tenant_id)
        );
        CREATE TABLE user_accounts (
          tenant_id text NOT NULL REFERENCES tenants(tenant_id),
          user_id text NOT NULL,
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (tenant_id, user_id)
        );
        CREATE TABLE memberships (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          user_id text NOT NULL,
          role text NOT NULL CHECK (role IN ('personal_owner','organization_admin','workspace_manager','reviewer','member','viewer')),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (tenant_id, workspace_id, user_id),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (tenant_id, user_id) REFERENCES user_accounts(tenant_id, user_id)
        );
        CREATE TABLE devices (
          tenant_id text NOT NULL,
          device_id text NOT NULL,
          user_id text NOT NULL,
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (tenant_id, device_id),
          FOREIGN KEY (tenant_id, user_id) REFERENCES user_accounts(tenant_id, user_id)
        );
        CREATE TABLE sessions (
          tenant_id text NOT NULL,
          session_id text NOT NULL,
          user_id text NOT NULL,
          device_id text NOT NULL,
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked','expired')),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          expires_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, session_id),
          FOREIGN KEY (tenant_id, user_id) REFERENCES user_accounts(tenant_id, user_id),
          FOREIGN KEY (tenant_id, device_id) REFERENCES devices(tenant_id, device_id)
        );
        CREATE TABLE authorization_policies (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          policy_id text NOT NULL,
          policy_version text NOT NULL,
          rules jsonb NOT NULL CHECK (jsonb_typeof(rules) = 'object'),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (tenant_id, workspace_id, policy_id),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id)
        );
        CREATE TABLE authorization_bindings (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          binding_id text NOT NULL,
          user_id text NOT NULL,
          policy_id text NOT NULL,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (tenant_id, workspace_id, binding_id),
          FOREIGN KEY (tenant_id, workspace_id, user_id) REFERENCES memberships(tenant_id, workspace_id, user_id),
          FOREIGN KEY (tenant_id, workspace_id, policy_id) REFERENCES authorization_policies(tenant_id, workspace_id, policy_id)
        );
        CREATE TABLE audit_events (
          sequence bigint GENERATED ALWAYS AS IDENTITY,
          event_id text NOT NULL,
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          actor_id text NOT NULL,
          action text NOT NULL,
          target_type text NOT NULL,
          target_id text NOT NULL,
          outcome text NOT NULL CHECK (outcome IN ('succeeded','denied','failed')),
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          before_value jsonb,
          after_value jsonb,
          metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, event_id),
          UNIQUE (sequence),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id)
        );
        CREATE TABLE idempotency_records (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          actor_id text NOT NULL,
          operation text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL,
          result jsonb NOT NULL,
          status text NOT NULL CHECK (status IN ('completed','failed')),
          expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id, actor_id, operation, idempotency_key),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id)
        );
        CREATE TABLE notifications (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          notification_id text NOT NULL,
          recipient_id text NOT NULL,
          source_event_id text NOT NULL,
          read_at timestamptz,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, notification_id),
          UNIQUE (tenant_id, recipient_id, source_event_id),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (tenant_id, recipient_id) REFERENCES user_accounts(tenant_id, user_id)
        );
        CREATE TABLE inbox_requests (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          request_id text NOT NULL,
          recipient_id text NOT NULL,
          status text NOT NULL,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          PRIMARY KEY (tenant_id, request_id),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (tenant_id, recipient_id) REFERENCES user_accounts(tenant_id, user_id)
        );
        CREATE TABLE vector_entries (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          vector_id text NOT NULL,
          embedding vector(3) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id, vector_id),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id)
        );

        CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'AUDIT_IMMUTABLE' USING ERRCODE = '55000'; END $$;
        CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
          FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
    """)

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        workspace_clause = (
            " AND workspace_id = nullif(current_setting('app.workspace_id', true), '')"
            if table in WORKSPACE_TABLES else ""
        )
        predicate = (
            "tenant_id = nullif(current_setting('app.tenant_id', true), '')"
            + workspace_clause
        )
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )

    op.execute("GRANT USAGE ON SCHEMA public TO daon_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO daon_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO daon_app")
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM daon_app")


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_mutation()")
    op.execute("DROP EXTENSION IF EXISTS vector")
