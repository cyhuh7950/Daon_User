"""S42 알림 상세·원사건 의도·알림 감사의도 저장 기반."""
from pathlib import Path

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
        OR (SELECT count(*) FROM public.alembic_version)<>1
        OR (SELECT version_num FROM public.alembic_version)<>'0069' THEN
        RAISE EXCEPTION 'NOTIFICATION_DOWNGRADE_PRECONDITION' USING ERRCODE='55000';
      END IF;
      LOCK TABLE public.notification_details,public.notification_source_outbox,
        public.notification_audit_outbox IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.notification_details)
        OR EXISTS(SELECT 1 FROM public.notification_source_outbox)
        OR EXISTS(SELECT 1 FROM public.notification_audit_outbox) THEN
        RAISE EXCEPTION 'NOTIFICATION_FORWARD_RECOVERY_REQUIRED' USING ERRCODE='55000';
      END IF;
    END $$;
    DROP FUNCTION public.read_notification_inbox(text,integer,text,text,jsonb);
    DROP FUNCTION public.enqueue_studio_notification(text,text,text);
    DROP FUNCTION public.materialize_studio_notification(text,boolean);
    DROP FUNCTION public.current_studio_notification_version(text);
    DROP FUNCTION public.pending_studio_notification_sources(integer);
    DROP FUNCTION public.pending_notification_audits(integer);
    DROP FUNCTION public.ack_notification_audit(text,text);
    DROP TRIGGER notification_parent_read_guard ON public.notifications;
    DROP TABLE public.notification_details,public.notification_source_outbox,public.notification_audit_outbox;
    DROP FUNCTION public.guard_notification_storage();
    DROP FUNCTION public.guard_notification_parent();
    DROP FUNCTION public.notification_parent_is_protected(text,text,text);
    """)
