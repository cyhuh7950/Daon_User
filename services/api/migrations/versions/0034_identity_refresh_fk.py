"""Point identity refresh families at the identity session table."""

from alembic import op


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE identity_refresh_families DROP CONSTRAINT IF EXISTS identity_refresh_families_tenant_id_session_id_fkey")
    op.execute("ALTER TABLE identity_refresh_families ADD CONSTRAINT identity_refresh_families_identity_session_fkey FOREIGN KEY (session_id) REFERENCES identity_sessions(session_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE identity_refresh_families DROP CONSTRAINT IF EXISTS identity_refresh_families_identity_session_fkey")
