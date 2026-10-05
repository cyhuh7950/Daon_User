"""Allow the scope-bound personal Provider credential envelope v2.

The migration changes only the accepted envelope versions. Existing rows remain
untouched. PostgreSQL executes this DDL transactionally through Alembic.
"""

from __future__ import annotations

from alembic import op


revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


_TABLE = "user_provider_credentials"
_V1_CONSTRAINT = "user_provider_credentials_credential_schema_version_check"
_V2_CONSTRAINT = "user_provider_credentials_personal_credential_schema_v2_check"


def upgrade() -> None:
    """Idempotently replace the v1-only check without rewriting credential rows."""
    op.execute(
        f"""
        DO $c8_migration$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = '{_TABLE}'::regclass AND conname = '{_V2_CONSTRAINT}'
          ) THEN
            ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_V1_CONSTRAINT};
            ALTER TABLE {_TABLE}
              ADD CONSTRAINT {_V2_CONSTRAINT}
              CHECK (credential_schema_version IN (1, 2));
          END IF;
          ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_V1_CONSTRAINT};
        END
        $c8_migration$;
        """
    )


def downgrade() -> None:
    """Restore v1 only when no v2 rows exist; never strand v2 ciphertext."""
    op.execute(
        f"""
        DO $c8_migration$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM {_TABLE} WHERE credential_schema_version = 2
          ) THEN
            RAISE EXCEPTION 'C8_PERSONAL_CREDENTIAL_V2_DOWNGRADE_BLOCKED';
          END IF;

          ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_V1_CONSTRAINT};
          ALTER TABLE {_TABLE}
            ADD CONSTRAINT {_V1_CONSTRAINT}
            CHECK (credential_schema_version = 1);
          ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_V2_CONSTRAINT};
        END
        $c8_migration$;
        """
    )
