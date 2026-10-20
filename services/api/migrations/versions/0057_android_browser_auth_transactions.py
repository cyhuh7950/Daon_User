"""Persist one-time Android browser authorization transactions."""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS identity_android_auth_transactions (
          transaction_id text PRIMARY KEY,
          client_id text NOT NULL,
          redirect_uri text NOT NULL,
          state text NOT NULL,
          code_challenge text NOT NULL,
          code_challenge_method text NOT NULL CHECK(code_challenge_method='S256'),
          expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL,
          code_digest text UNIQUE,
          code_expires_at timestamptz,
          user_id text,
          tenant_id text,
          authorized_at timestamptz,
          used_at timestamptz
        );
        CREATE INDEX IF NOT EXISTS identity_android_auth_transactions_expiry_idx
          ON identity_android_auth_transactions(expires_at);
    """)


def downgrade() -> None:
    op.execute("""
        LOCK TABLE identity_android_auth_transactions IN ACCESS EXCLUSIVE MODE;
        DO $android_auth$
        BEGIN
          IF EXISTS (SELECT 1 FROM identity_android_auth_transactions LIMIT 1) THEN
            RAISE EXCEPTION 'ANDROID_AUTH_TRANSACTIONS_DOWNGRADE_BLOCKED';
          END IF;
        END
        $android_auth$;
        DROP TABLE identity_android_auth_transactions;
    """)
