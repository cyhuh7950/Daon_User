"""Allow a claimed Studio job to be observable while generating."""

from alembic import op


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE studio_generation_jobs
        DROP CONSTRAINT IF EXISTS studio_generation_jobs_lease_state_check;
      ALTER TABLE studio_generation_jobs
        DROP CONSTRAINT IF EXISTS studio_generation_jobs_state_check;
      ALTER TABLE studio_generation_jobs
        ADD CONSTRAINT studio_generation_jobs_state_check
        CHECK (state IN ('queued','leased','generating','completed','failed','unavailable'));
      ALTER TABLE studio_generation_jobs
        ADD CONSTRAINT studio_generation_jobs_lease_state_check
        CHECK ((state IN ('leased','generating')) =
               (lease_owner IS NOT NULL AND lease_until IS NOT NULL));
    """)


def downgrade() -> None:
    op.execute("""
      ALTER TABLE studio_generation_jobs
        DROP CONSTRAINT IF EXISTS studio_generation_jobs_lease_state_check;
      ALTER TABLE studio_generation_jobs
        DROP CONSTRAINT IF EXISTS studio_generation_jobs_state_check;
      ALTER TABLE studio_generation_jobs
        ADD CONSTRAINT studio_generation_jobs_state_check
        CHECK (state IN ('queued','leased','generating','completed','failed','unavailable'));
      ALTER TABLE studio_generation_jobs
        ADD CONSTRAINT studio_generation_jobs_lease_state_check
        CHECK ((state = 'leased') =
               (lease_owner IS NOT NULL AND lease_until IS NOT NULL));
    """)
