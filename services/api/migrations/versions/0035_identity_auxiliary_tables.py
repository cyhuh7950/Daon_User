"""Complete the PostgreSQL identity schema used by the runtime adapter."""

from __future__ import annotations

from alembic import op


revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE identity_refresh_families
          DROP CONSTRAINT IF EXISTS identity_refresh_families_tenant_id_session_id_fkey;
        ALTER TABLE identity_refresh_families
          ADD CONSTRAINT identity_refresh_families_session_fk
          FOREIGN KEY (session_id) REFERENCES identity_sessions(session_id);
        ALTER TABLE identity_refresh_families
          ALTER COLUMN tenant_id DROP NOT NULL;
        CREATE TABLE identity_tenants (
          tenant_id text PRIMARY KEY
        );
        CREATE TABLE identity_session_audit_outbox (
          event_id text PRIMARY KEY,
          session_id text NOT NULL REFERENCES identity_sessions(session_id),
          action text NOT NULL CHECK (action = 'identity.session.self_revoked'),
          tenant_id text NOT NULL REFERENCES identity_tenants(tenant_id),
          actor_id text NOT NULL REFERENCES identity_users(user_id),
          occurred_at timestamptz NOT NULL,
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          delivered_at timestamptz,
          created_at timestamptz NOT NULL,
          UNIQUE (session_id, action)
        );
        CREATE TABLE identity_oidc_transactions (
          transaction_id text PRIMARY KEY,
          state_digest text NOT NULL UNIQUE,
          nonce_digest text NOT NULL,
          code_challenge text NOT NULL,
          issuer text NOT NULL,
          client_id text NOT NULL,
          audience text NOT NULL,
          redirect_uri text NOT NULL,
          client_kind text NOT NULL,
          tenant_id text NOT NULL,
          expires_at timestamptz NOT NULL,
          used_at timestamptz,
          created_at timestamptz NOT NULL
        );
        CREATE TABLE identity_tenant_step_up_actions (
          tenant_id text NOT NULL REFERENCES identity_tenants(tenant_id),
          action_group text NOT NULL,
          is_mandatory integer NOT NULL,
          PRIMARY KEY (tenant_id, action_group)
        );
        CREATE TABLE identity_step_up_authorizations (
          step_up_id text PRIMARY KEY,
          authorization_digest text NOT NULL UNIQUE,
          tenant_id text NOT NULL REFERENCES identity_tenants(tenant_id),
          actor_id text NOT NULL REFERENCES identity_users(user_id),
          session_id text NOT NULL REFERENCES identity_sessions(session_id),
          device_id text NOT NULL REFERENCES identity_devices(device_id),
          action_group text NOT NULL,
          target_id text NOT NULL,
          policy_version text NOT NULL,
          issued_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          used_at timestamptz
        );
        CREATE TABLE identity_step_up_idempotency (
          tenant_id text NOT NULL REFERENCES identity_tenants(tenant_id),
          actor_id text NOT NULL REFERENCES identity_users(user_id),
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL,
          step_up_id text NOT NULL REFERENCES identity_step_up_authorizations(step_up_id),
          authorization_digest text NOT NULL,
          action_group text NOT NULL,
          target_id text NOT NULL,
          policy_version text NOT NULL,
          key_id text NOT NULL,
          key_version integer NOT NULL,
          issued_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, actor_id, idempotency_key)
        );
        CREATE TABLE identity_step_up_consumptions (
          tenant_id text NOT NULL REFERENCES identity_tenants(tenant_id),
          actor_id text NOT NULL REFERENCES identity_users(user_id),
          operation text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL,
          step_up_id text NOT NULL REFERENCES identity_step_up_authorizations(step_up_id),
          consumed_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)
        );
        """
    )


def downgrade() -> None:
    for table in (
        "identity_step_up_consumptions",
        "identity_step_up_idempotency",
        "identity_step_up_authorizations",
        "identity_tenant_step_up_actions",
        "identity_oidc_transactions",
        "identity_session_audit_outbox",
        "identity_tenants",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
