"""Persist legacy identity memberships, devices and sessions in PostgreSQL."""

from __future__ import annotations

from alembic import op


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE identity_memberships (
          tenant_id text NOT NULL,
          user_id text NOT NULL REFERENCES identity_users(user_id),
          role text NOT NULL,
          PRIMARY KEY (tenant_id, user_id)
        );
        CREATE TABLE identity_devices (
          device_id text PRIMARY KEY,
          tenant_id text NOT NULL,
          user_id text NOT NULL REFERENCES identity_users(user_id),
          platform text NOT NULL,
          state text NOT NULL,
          last_seen_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL
        );
        CREATE TABLE identity_sessions (
          session_id text PRIMARY KEY,
          tenant_id text NOT NULL,
          user_id text NOT NULL REFERENCES identity_users(user_id),
          device_id text NOT NULL REFERENCES identity_devices(device_id),
          client_kind text NOT NULL,
          access_digest text NOT NULL UNIQUE,
          access_expires_at timestamptz NOT NULL,
          state text NOT NULL,
          created_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS identity_sessions")
    op.execute("DROP TABLE IF EXISTS identity_devices")
    op.execute("DROP TABLE IF EXISTS identity_memberships")
