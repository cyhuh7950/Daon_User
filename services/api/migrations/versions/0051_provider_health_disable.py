"""Allow zero minutes to disable periodic Provider health checks."""

from __future__ import annotations

from alembic import op


revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE system_provider_health_settings
          DROP CONSTRAINT system_provider_health_settings_interval_minutes_check,
          ADD CONSTRAINT system_provider_health_settings_interval_minutes_check
            CHECK (interval_minutes BETWEEN 0 AND 1440);
        """
    )


def downgrade() -> None:
    raise RuntimeError("PROVIDER_HEALTH_DISABLE_DOWNGRADE_BLOCKED")
