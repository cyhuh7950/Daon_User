"""Add PostgreSQL-backed identity records without changing existing Cloud keys."""

from __future__ import annotations

from alembic import op


revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the existing Cloud user_accounts/memberships contract intact while
    # providing a durable global identity record for the upcoming adapter.
    op.execute(
        """
        CREATE TABLE identity_users (
          user_id text PRIMARY KEY,
          issuer text NOT NULL DEFAULT 'oidc',
          subject text NOT NULL,
          login_id text,
          email text,
          password_digest text,
          email_verified_at timestamptz,
          state text NOT NULL DEFAULT 'active' CHECK (state IN ('pending_email','active','disabled')),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (issuer, subject),
          UNIQUE (login_id),
          UNIQUE (email)
        );
        CREATE TABLE identity_email_verification_tokens (
          token_id text PRIMARY KEY,
          user_id text NOT NULL REFERENCES identity_users(user_id),
          token_digest text NOT NULL UNIQUE,
          expires_at timestamptz NOT NULL,
          used_at timestamptz,
          attempts integer NOT NULL DEFAULT 0,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE identity_password_reset_tokens (
          token_id text PRIMARY KEY,
          user_id text NOT NULL REFERENCES identity_users(user_id),
          token_digest text NOT NULL UNIQUE,
          expires_at timestamptz NOT NULL,
          used_at timestamptz,
          attempts integer NOT NULL DEFAULT 0,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE identity_refresh_families (
          family_id text PRIMARY KEY,
          tenant_id text NOT NULL,
          session_id text NOT NULL,
          state text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, session_id)
        );
        CREATE TABLE identity_refresh_tokens (
          refresh_id text PRIMARY KEY,
          family_id text NOT NULL REFERENCES identity_refresh_families(family_id),
          refresh_digest text NOT NULL UNIQUE,
          expires_at timestamptz NOT NULL,
          used_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS identity_refresh_tokens")
    op.execute("DROP TABLE IF EXISTS identity_refresh_families")
    op.execute("DROP TABLE IF EXISTS identity_password_reset_tokens")
    op.execute("DROP TABLE IF EXISTS identity_email_verification_tokens")
    op.execute("DROP TABLE IF EXISTS identity_users")
