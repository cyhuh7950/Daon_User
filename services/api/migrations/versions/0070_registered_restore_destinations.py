"""R2 additive 저장 초안. 작성/정적 검증만 승인, 실제 적용/GRANT는 별도 승인.

부모0006 보호/owner/권한은 변경하지 않는다. fixture 표에는 INSERT mode guard
하나만 추가한다. 새 표에 행이 있으면 down 금지(예약 해제/대상 재사용 아님).
"""
from pathlib import Path
from alembic import op

revision = "0070"
down_revision = "0069"
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
        OR (SELECT version_num FROM public.alembic_version)<>'0070' THEN
        RAISE EXCEPTION 'REGISTERED_RESTORE_DOWN_PRECONDITION' USING ERRCODE='55000'; END IF;
      LOCK TABLE public.restore_requests,public.restore_previews,
        public.registered_restore_target_reservations,public.registered_restore_previews,
        public.registered_restore_phase_receipts IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.registered_restore_target_reservations)
        OR EXISTS(SELECT 1 FROM public.registered_restore_previews)
        OR EXISTS(SELECT 1 FROM public.registered_restore_phase_receipts) THEN
        RAISE EXCEPTION 'REGISTERED_RESTORE_FORWARD_RECOVERY_REQUIRED' USING ERRCODE='55000'; END IF;
    END $$;
    DROP TRIGGER registered_restore_fixture_mode_guard ON public.restore_previews;
    DROP TABLE public.registered_restore_phase_receipts;
    DROP TABLE public.registered_restore_previews;
    DROP TABLE public.registered_restore_target_reservations;
    DROP FUNCTION public.guard_registered_restore_insert();
    DROP FUNCTION public.registered_restore_actor_allowed(text,text,text);
    """)
