"""S42 알림 상세·원사건 의도·알림 감사의도 저장 기반."""
from pathlib import Path

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind().execution_options(no_parameters=True)
    connection.exec_driver_sql(Path(__file__).with_suffix(".sql").read_bytes().decode("utf-8"))


def downgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
        OR (SELECT count(*) FROM public.alembic_version)<>1
        OR (SELECT version_num FROM public.alembic_version)<>'0069' THEN
        RAISE EXCEPTION 'NOTIFICATION_DOWNGRADE_PRECONDITION' USING ERRCODE='55000';
      END IF;
      LOCK TABLE public.notification_details,public.notification_source_outbox,
        public.notification_audit_outbox,public.studio_generation_jobs,public.identity_password_change_events IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.notification_details)
        OR EXISTS(SELECT 1 FROM public.notification_source_outbox)
        OR EXISTS(SELECT 1 FROM public.notification_audit_outbox)
        OR EXISTS(SELECT 1 FROM public.studio_generation_jobs)
        OR EXISTS(SELECT 1 FROM public.identity_password_change_events) THEN
        RAISE EXCEPTION 'NOTIFICATION_FORWARD_RECOVERY_REQUIRED' USING ERRCODE='55000';
      END IF;
    END $$;
    DROP FUNCTION public.materialize_restore_completed_notification(text,boolean);
    DROP FUNCTION public.materialize_password_reset_notification(text,boolean);
    DROP FUNCTION public.current_password_reset_notification_target(text);
    DROP FUNCTION public.pending_password_reset_notification_sources(integer);
    DROP FUNCTION public.enqueue_password_reset_notification(text);
    DROP FUNCTION public.current_password_reset_notification_event(text);
    DROP FUNCTION public.materialize_password_change_notification(text,boolean);
    DROP FUNCTION public.current_password_change_notification_target(text);
    DROP FUNCTION public.pending_password_change_notification_sources(integer);
    DROP FUNCTION public.enqueue_password_change_notification(text);
    DROP FUNCTION public.current_password_change_notification_event(text);
    DROP TRIGGER identity_password_change_event_guard ON public.identity_password_change_events;
    DROP FUNCTION public.guard_identity_password_change_event();
    DROP TABLE public.identity_password_change_events;
    DROP FUNCTION public.pending_notification_dispatch_scopes(integer,text,text,text);
    DROP POLICY studio_notice_dispatch_scope_read ON public.studio_approval_notices;
    DROP POLICY notification_source_dispatch_scope_read ON public.notification_source_outbox;
    DROP POLICY notification_audit_dispatch_scope_read ON public.notification_audit_outbox;
    DROP FUNCTION public.lock_question_knowledge_selection(text,text,text,text,text,text);
    DROP FUNCTION public.current_restore_completed_notification_target(text);
    DROP FUNCTION public.pending_restore_completed_notification_sources(integer);
    DROP FUNCTION public.enqueue_restore_completed_notification(text,text);
    DROP FUNCTION public.current_terminal_recovery_notification_origin(text,text);
    DROP FUNCTION public.materialize_source_access_notification(text,boolean);
    DROP FUNCTION public.current_source_access_notification_target(text);
    DROP FUNCTION public.pending_source_access_notification_sources(integer);
    DROP FUNCTION public.enqueue_source_access_notification(text,text,text,integer);
    DROP FUNCTION public.source_access_notification_access(text,text);
    DROP FUNCTION public.materialize_egress_policy_notification(text,boolean);
    DROP FUNCTION public.current_egress_policy_notification_target(text);
    DROP FUNCTION public.pending_egress_policy_notification_sources(integer);
    DROP FUNCTION public.enqueue_egress_policy_notification(text,text);
    DROP FUNCTION public.current_egress_policy_notification_origin(text);
    DROP FUNCTION public.materialize_c9_role_notification(text,boolean);
    DROP FUNCTION public.current_c9_role_notification_target(text);
    DROP FUNCTION public.pending_c9_role_notification_sources(integer);
    DROP FUNCTION public.enqueue_c9_role_notification(text,text);
    DROP FUNCTION public.materialize_identity_login_notification(text,boolean);
    DROP TRIGGER identity_login_source_insert ON public.identity_sessions;
    DROP FUNCTION public.capture_identity_login_source();
    DROP FUNCTION public.verify_identity_login_notification_source(text,text);
    DROP FUNCTION public.current_identity_login_notification_target(text);
    DROP FUNCTION public.pending_identity_login_notification_sources(integer);
    DROP FUNCTION public.enqueue_identity_login_notification(text,text);
    DROP FUNCTION public.materialize_organization_role_notification(text,boolean);
    DROP FUNCTION public.current_organization_role_notification_target(text);
    DROP FUNCTION public.pending_organization_role_notification_sources(integer);
    DROP FUNCTION public.enqueue_organization_role_notification(text,text);
    DROP FUNCTION public.current_organization_role_notification_origin(text,text);
    DROP TRIGGER studio_generation_jobs_guard ON public.studio_generation_jobs;
    DROP FUNCTION public.guard_studio_generation_job();
    ALTER TABLE public.studio_generation_jobs OWNER TO daon_app;
    REVOKE ALL ON TABLE public.studio_generation_jobs FROM PUBLIC;
    GRANT SELECT,INSERT,UPDATE ON TABLE public.studio_generation_jobs TO daon_app;
    DROP TRIGGER studio_notice_notification_created ON public.studio_approval_notices;
    DROP FUNCTION public.capture_studio_notice_notification();
    DROP FUNCTION public.current_studio_notice_notification_audit(text);
    DROP FUNCTION public.record_studio_notice_notification(text,text,text,text,text);
    DROP FUNCTION public.valid_studio_notice_notification_audit(public.notification_audit_outbox);
    DROP FUNCTION public.current_studio_notice_notification_target(text);
    DROP FUNCTION public.materialize_account_notification(text,boolean);
    DROP FUNCTION public.current_account_notification_target(text);
    DROP FUNCTION public.pending_account_notification_sources(integer);
    DROP FUNCTION public.enqueue_account_notification(text);
    DROP FUNCTION public.current_account_notification_event(text);
    DROP FUNCTION public.read_notification_inbox(text,integer,text,text,jsonb);
    DROP FUNCTION public.enqueue_studio_notification(text,text,text);
    DROP FUNCTION public.enqueue_completed_notification(text,text);
    DROP FUNCTION public.materialize_completed_notification(text,boolean);
    DROP FUNCTION public.current_completed_notification_target(text);
    DROP FUNCTION public.pending_completed_notification_sources(integer);
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
