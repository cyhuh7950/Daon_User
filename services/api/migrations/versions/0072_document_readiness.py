"""R2 세 원장/보호 함수 초안. 실제 적용은 exact SQL/권한/격리 검증 이후 별도다."""
from pathlib import Path

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    op.execute("""
    DO $$ BEGIN
      IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
        OR (SELECT count(*) FROM public.alembic_version)<>1
        OR (SELECT version_num FROM public.alembic_version)<>'0072' THEN
        RAISE EXCEPTION 'DOCUMENT_READINESS_DOWN_PRECONDITION' USING ERRCODE='55000'; END IF;
      LOCK TABLE public.document_role_waits,public.document_readiness_observations,
        public.document_reprocess_receipts IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.document_role_waits)
        OR EXISTS(SELECT 1 FROM public.document_readiness_observations)
        OR EXISTS(SELECT 1 FROM public.document_reprocess_receipts) THEN
        RAISE EXCEPTION 'DOCUMENT_READINESS_FORWARD_RECOVERY_REQUIRED' USING ERRCODE='55000'; END IF;
    END $$;
    DROP FUNCTION public.cleanup_document_readiness(text,text,integer);
    DROP FUNCTION public.read_document_readiness_metrics(text,text,text,text);
    DROP FUNCTION public.read_document_blocked_parent(text,text,text,text,text,text,text,text);
    DROP FUNCTION public.record_document_reprocess_receipt(jsonb);
    DROP FUNCTION public.read_document_reprocess_history(text,text,text,text,text,text,text,text,text);
    DROP FUNCTION public.read_latest_document_readiness_observation(text);
    DROP FUNCTION public.read_document_role_wait(text,text,text,text);
    DROP FUNCTION public.discover_document_role_waits(jsonb,text[],integer);
    DROP FUNCTION public.record_document_readiness_observation(jsonb,text);
    DROP FUNCTION public.record_document_role_wait(jsonb);
    DROP TABLE public.document_reprocess_receipts;
    DROP TABLE public.document_readiness_observations;
    DROP TABLE public.document_role_waits;
    DROP FUNCTION public.guard_document_readiness_immutable();
    DROP FUNCTION public.document_readiness_parent_allowed(text,text,text,text,text,text,text);
    """)
