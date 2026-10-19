"""S44 명시 Run 비용/WSL CPU·RAM. 유자료 DB는 forward 보정만 한다."""
from pathlib import Path
from alembic import op

revision='0073'
down_revision='0072'
branch_labels=None
depends_on=None

def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))

def downgrade():
    op.execute("""
    DO $$ BEGIN
      IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
        OR (SELECT count(*) FROM public.alembic_version)<>1
        OR (SELECT version_num FROM public.alembic_version)<>'0073' THEN
        RAISE EXCEPTION 'OPERATIONS_COST_CAPACITY_DOWN_PRECONDITION' USING ERRCODE='55000'; END IF;
      LOCK TABLE public.operations_run_cost_events,public.operations_host_capacity_samples IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.operations_run_cost_events)
        OR EXISTS(SELECT 1 FROM public.operations_host_capacity_samples) THEN
        RAISE EXCEPTION 'OPERATIONS_COST_CAPACITY_FORWARD_RECOVERY_REQUIRED' USING ERRCODE='55000'; END IF;
    END $$;
    DROP FUNCTION public.read_operations_host_capacity();
    DROP FUNCTION public.record_operations_host_capacity(text,text,bigint,timestamptz,bigint,integer,bigint,bigint,bigint[]);
    DROP FUNCTION public.record_operations_price_card_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text);
    DROP FUNCTION public.settle_operations_run_cost(text,text,text,text,text,text,bigint,text,numeric,text,text,text,text,timestamptz);
    DROP FUNCTION public.reserve_operations_run_cost(text,text,text,text,text,text,jsonb,bigint,integer);
    DROP TABLE public.operations_run_cost_events;
    DROP TABLE public.operations_host_capacity_samples;
    DROP FUNCTION public.guard_operations_run_cost_event();
    DROP FUNCTION public.guard_operations_host_capacity();
    DROP FUNCTION public.operations_run_cost_parent_allowed(text,text,text,text,text);
    """)
