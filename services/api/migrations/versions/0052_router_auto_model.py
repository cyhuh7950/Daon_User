"""Add explicit router Auto identity and catalog provenance without changing selections."""

from __future__ import annotations

from alembic import op


revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE system_provider_connections ADD COLUMN auto_model_id text")
    op.execute(
        "ALTER TABLE system_provider_models ADD COLUMN catalog_origin text NOT NULL "
        "DEFAULT 'upstream' CHECK (catalog_origin IN ('upstream','logical'))"
    )
    op.execute(
        "UPDATE system_provider_connections SET auto_model_id=CASE provider_code "
        "WHEN 'OMNIROUTE' THEN 'auto' WHEN 'OPENROUTER' THEN 'openrouter/auto' END "
        "WHERE provider_code IN ('OMNIROUTE','OPENROUTER')"
    )
    op.execute(
        "UPDATE system_provider_models m SET catalog_origin='logical' "
        "FROM system_provider_connections c WHERE m.connection_id=c.connection_id "
        "AND c.provider_code='OMNIROUTE' AND m.model_id='auto'"
    )


def downgrade() -> None:
    raise RuntimeError("ROUTER_AUTO_MODEL_DOWNGRADE_BLOCKED")
