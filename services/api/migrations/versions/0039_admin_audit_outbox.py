"""Add transactional audit intents for administrator identity mutations."""

from __future__ import annotations

from alembic import op


revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE identity_admin_audit_outbox (
          event_id text PRIMARY KEY,
          operation text NOT NULL,
          idempotency_scope text,
          request_fingerprint text,
          actor_id text NOT NULL,
          actor_type text NOT NULL,
          tenant_id text NOT NULL,
          action text NOT NULL,
          target_type text NOT NULL,
          target_id text NOT NULL,
          occurred_at timestamptz NOT NULL,
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          before_state text,
          after_state text,
          metadata_json text NOT NULL,
          delivered_at timestamptz,
          created_at timestamptz NOT NULL,
          UNIQUE (operation, idempotency_scope)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS identity_admin_audit_outbox")
