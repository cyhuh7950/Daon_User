"""S39 조직 작업 원장과 기존 초대·회원의 nullable Workspace 연결 기반."""
from pathlib import Path

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
        OR (SELECT count(*) FROM public.alembic_version)<>1
        OR (SELECT version_num FROM public.alembic_version)<>'0068' THEN
        RAISE EXCEPTION 'ORGANIZATION_DOWNGRADE_PRECONDITION' USING ERRCODE='55000';
      END IF;
      LOCK TABLE public.identity_org_operation_outbox,public.identity_org_invitation_codes,
        public.identity_org_tenant_memberships IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.identity_org_operation_outbox)
        OR EXISTS(SELECT 1 FROM public.identity_org_invitation_codes WHERE workspace_id IS NOT NULL)
        OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships WHERE join_workspace_id IS NOT NULL) THEN
        RAISE EXCEPTION 'ORGANIZATION_FORWARD_RECOVERY_REQUIRED' USING ERRCODE='55000';
      END IF;
    END $$;
    DROP FUNCTION public.pending_identity_org_audits(integer);
    DROP FUNCTION public.ack_identity_org_audit(text);
    DROP TABLE public.identity_org_operation_outbox;
    DROP FUNCTION public.guard_identity_org_operation();
    DROP FUNCTION public.valid_identity_org_operation(text,text,text);
    DROP TRIGGER identity_org_invitation_workspace_guard ON public.identity_org_invitation_codes;
    DROP TRIGGER identity_org_membership_workspace_guard ON public.identity_org_tenant_memberships;
    DROP FUNCTION public.guard_identity_org_workspace();
    ALTER TABLE public.identity_org_invitation_codes DROP COLUMN workspace_id;
    ALTER TABLE public.identity_org_tenant_memberships DROP COLUMN join_workspace_id;
    """)
    # 클러스터 역할과 기존 pending index는 회수하지 않는다.
