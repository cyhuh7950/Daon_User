"""M2 최소 세 표/worker 보호 함수 초안. 실제 SQL 적용은 별도 승인이다."""
from pathlib import Path
from alembic import op

revision="0071"
down_revision="0070"
branch_labels=None
depends_on=None


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    op.execute("""
    DO $$ BEGIN
      IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
        OR (SELECT count(*) FROM public.alembic_version)<>1
        OR (SELECT version_num FROM public.alembic_version)<>'0071' THEN
        RAISE EXCEPTION 'OPERATIONS_TELEMETRY_DOWN_PRECONDITION' USING ERRCODE='55000'; END IF;
      LOCK TABLE public.operations_worker_observations,public.operations_deployment_receipts,
        public.operations_cost_receipts IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.operations_worker_observations)
        OR EXISTS(SELECT 1 FROM public.operations_deployment_receipts)
        OR EXISTS(SELECT 1 FROM public.operations_cost_receipts) THEN
        RAISE EXCEPTION 'OPERATIONS_TELEMETRY_FORWARD_RECOVERY_REQUIRED' USING ERRCODE='55000'; END IF;
    END $$;
    DROP FUNCTION public.read_operations_worker_samples(text[]);
    DROP FUNCTION public.record_operations_cost_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text);
    DROP FUNCTION public.read_operations_cost_receipts(text[],text);
    DROP FUNCTION public.read_operations_deployment_receipt();
    DROP FUNCTION public.record_operations_deployment_receipt(text,text,text,text,text,text,timestamptz,text,text);
    DROP FUNCTION public.record_operations_worker_sample(text,text,text,timestamptz,bigint,timestamptz,timestamptz,timestamptz,timestamptz,text,bigint,bigint,bigint,bigint,integer);
    DROP TABLE public.operations_cost_receipts;
    DROP TABLE public.operations_deployment_receipts;
    DROP TABLE public.operations_worker_observations;
    DROP FUNCTION public.guard_operations_receipt();
    DROP FUNCTION public.guard_operations_worker_observation();
    """)
