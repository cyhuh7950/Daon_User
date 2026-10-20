"""S36 own profile and immutable account request/audit ledger."""
from pathlib import Path

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
        OR (SELECT count(*) FROM public.alembic_version)<>1
        OR (SELECT version_num FROM public.alembic_version)<>'0067' THEN
        RAISE EXCEPTION 'ACCOUNT_DOWNGRADE_PRECONDITION' USING ERRCODE='55000';
      END IF;
      LOCK TABLE public.identity_account_profiles,public.identity_account_requests IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.identity_account_profiles)
        OR EXISTS(SELECT 1 FROM public.identity_account_requests) THEN
        RAISE EXCEPTION 'ACCOUNT_FORWARD_RECOVERY_REQUIRED' USING ERRCODE='55000';
      END IF;
    END $$;
    DROP FUNCTION public.pending_identity_account_audits(text);
    DROP TABLE public.identity_account_requests;
    DROP TABLE public.identity_account_profiles;
    DROP FUNCTION public.valid_identity_account_events(text,text);
    DROP FUNCTION public.guard_identity_account_request();
    DROP FUNCTION public.guard_identity_account_profile();
    """)
    # The non-login owner role is intentionally retained; never drop a cluster role
    # or unrelated privileges during a database-local downgrade.
