"""Add the system-wide provider health check interval."""

from __future__ import annotations

from alembic import op


revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE system_provider_health_settings (
          setting_id text PRIMARY KEY CHECK (setting_id = 'global'),
          interval_minutes integer NOT NULL DEFAULT 60 CHECK (interval_minutes BETWEEN 1 AND 1440),
          version integer NOT NULL DEFAULT 0 CHECK (version >= 0),
          updated_by text NOT NULL DEFAULT 'migration:0041',
          trace_id text NOT NULL DEFAULT 'migration:0041',
          policy_version text NOT NULL DEFAULT 'runtime-policy-v1',
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO system_provider_health_settings (setting_id)
        VALUES ('global')
        ON CONFLICT (setting_id) DO NOTHING;
        GRANT SELECT, INSERT, UPDATE ON system_provider_health_settings TO daon_app;
        ALTER TABLE system_provider_health_settings OWNER TO daon_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("PROVIDER_HEALTH_SETTINGS_DOWNGRADE_BLOCKED")
