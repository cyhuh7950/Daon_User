"""Allow identities to remain pending until a system administrator approves them."""

from __future__ import annotations

from alembic import op


revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE identity_users
          DROP CONSTRAINT IF EXISTS identity_users_state_check;
        ALTER TABLE identity_users
          ADD CONSTRAINT identity_users_state_check
          CHECK (state IN ('pending_email','pending_approval','active','disabled'));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE identity_users
          DROP CONSTRAINT IF EXISTS identity_users_state_check;
        ALTER TABLE identity_users
          ADD CONSTRAINT identity_users_state_check
          CHECK (state IN ('pending_email','active','disabled'));
        """
    )
