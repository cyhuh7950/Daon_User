"""PostgreSQL tables for organization membership workflow."""
from __future__ import annotations
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE identity_org_creation_requests (request_id text PRIMARY KEY, applicant_user_id text NOT NULL, requested_org_name text NOT NULL, requested_org_identifier text NOT NULL, state text NOT NULL, decision_reason text, decided_by text, version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL);
        CREATE UNIQUE INDEX identity_org_creation_pending_unique ON identity_org_creation_requests(applicant_user_id) WHERE state = 'pending';
        CREATE TABLE identity_org_invitation_codes (invitation_id text PRIMARY KEY, tenant_id text NOT NULL, created_by text NOT NULL, code_digest text NOT NULL UNIQUE, expires_at timestamptz NOT NULL, max_uses integer NOT NULL, used_count integer NOT NULL DEFAULT 0, state text NOT NULL, version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL);
        CREATE TABLE identity_org_join_requests (request_id text PRIMARY KEY, tenant_id text NOT NULL, user_id text NOT NULL, invitation_id text REFERENCES identity_org_invitation_codes(invitation_id), state text NOT NULL, decision_reason text, decided_by text, version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL);
        CREATE UNIQUE INDEX identity_org_join_pending_unique ON identity_org_join_requests(tenant_id,user_id) WHERE state = 'pending';
        CREATE TABLE identity_org_tenant_memberships (tenant_id text NOT NULL, user_id text NOT NULL, role text NOT NULL, state text NOT NULL, version integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,user_id));
        CREATE TABLE identity_org_role_history (history_id text PRIMARY KEY, tenant_id text NOT NULL, user_id text NOT NULL, actor_id text NOT NULL, previous_role text, next_role text, previous_state text, next_state text, reason text, created_at timestamptz NOT NULL);
        CREATE TABLE identity_org_idempotency (operation text NOT NULL, actor_id text NOT NULL, idempotency_key text NOT NULL, fingerprint text NOT NULL, result_json text NOT NULL, created_at timestamptz NOT NULL, PRIMARY KEY(operation,actor_id,idempotency_key));
        """
    )


def downgrade() -> None:
    for table in ("idempotency", "role_history", "tenant_memberships", "join_requests", "invitation_codes", "creation_requests"):
        op.execute(f"DROP TABLE IF EXISTS identity_org_{table}")
