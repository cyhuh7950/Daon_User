"""Remove the legacy lease check left by the original Studio job migration."""

from alembic import op


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE studio_generation_jobs
        DROP CONSTRAINT IF EXISTS studio_generation_jobs_check;
    """)


def downgrade() -> None:
    op.execute("""
      ALTER TABLE studio_generation_jobs
        ADD CONSTRAINT studio_generation_jobs_check
        CHECK ((state = 'leased') =
               (lease_owner IS NOT NULL AND lease_until IS NOT NULL));
    """)
