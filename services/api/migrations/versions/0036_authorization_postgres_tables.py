"""PostgreSQL tables for the authorization repository contract."""
from __future__ import annotations

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE identity_auth_schema_metadata (singleton integer PRIMARY KEY CHECK (singleton = 1), schema_version integer NOT NULL);
        INSERT INTO identity_auth_schema_metadata(singleton, schema_version) VALUES (1, 1) ON CONFLICT DO NOTHING;
        CREATE TABLE identity_auth_workspaces (workspace_id text PRIMARY KEY, tenant_id text NOT NULL, workspace_kind text NOT NULL, data_area text NOT NULL, cost_limit_cents integer NOT NULL, acl_version integer NOT NULL, version integer NOT NULL, updated_at timestamptz NOT NULL, UNIQUE(tenant_id, workspace_id));
        CREATE TABLE identity_auth_tenant_roles (tenant_id text NOT NULL, user_id text NOT NULL, role text NOT NULL, state text NOT NULL, version integer NOT NULL, updated_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,user_id));
        CREATE TABLE identity_auth_memberships (tenant_id text NOT NULL, workspace_id text NOT NULL, user_id text NOT NULL, role text NOT NULL, state text NOT NULL, version integer NOT NULL, updated_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,workspace_id,user_id));
        CREATE TABLE identity_auth_tenant_policies (tenant_id text PRIMARY KEY, version integer NOT NULL, updated_at timestamptz NOT NULL);
        CREATE TABLE identity_auth_tenant_permission_rules (tenant_id text NOT NULL, permission text NOT NULL, effect text NOT NULL, locked integer NOT NULL, PRIMARY KEY(tenant_id,permission));
        CREATE TABLE identity_auth_workspace_policies (tenant_id text NOT NULL, workspace_id text NOT NULL, version integer NOT NULL, updated_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,workspace_id));
        CREATE TABLE identity_auth_workspace_permission_rules (tenant_id text NOT NULL, workspace_id text NOT NULL, permission text NOT NULL, effect text NOT NULL, locked integer NOT NULL, PRIMARY KEY(tenant_id,workspace_id,permission));
        CREATE TABLE identity_auth_source_access (tenant_id text NOT NULL, workspace_id text NOT NULL, user_id text NOT NULL, source_version_id text NOT NULL, allowed integer NOT NULL, version integer NOT NULL, updated_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,workspace_id,user_id,source_version_id));
        CREATE TABLE identity_auth_historical_results (result_id text PRIMARY KEY, result_kind text NOT NULL, tenant_id text NOT NULL, workspace_id text NOT NULL, source_version_ids text NOT NULL, evidence_reference_ids text NOT NULL, original_policy_version text NOT NULL, original_membership_version integer NOT NULL, created_at timestamptz NOT NULL);
        CREATE TABLE identity_auth_result_dependencies (result_id text NOT NULL, reference_id text NOT NULL, source_version_id text NOT NULL, segment_ids text NOT NULL, decisive integer NOT NULL, safe_separation integer NOT NULL, PRIMARY KEY(result_id,reference_id));
        CREATE TABLE identity_auth_access_decisions (decision_id text PRIMARY KEY, actor_id text NOT NULL, action text NOT NULL, resource_id text NOT NULL, tenant_id text NOT NULL, workspace_id text NOT NULL, role_scope text, membership_version integer NOT NULL, acl_version integer NOT NULL, policy_version text NOT NULL, evaluated_at timestamptz NOT NULL, state text NOT NULL, reason_codes text NOT NULL, allowed_reference_ids text NOT NULL, masked_reference_ids text NOT NULL, allowed_segment_ids text NOT NULL, masked_segment_ids text NOT NULL);
        CREATE TABLE identity_auth_rerun_requests (run_request_id text PRIMARY KEY, result_id text NOT NULL, access_decision_id text NOT NULL, tenant_id text NOT NULL, workspace_id text NOT NULL, actor_id text NOT NULL, snapshot text NOT NULL, created_at timestamptz NOT NULL);
        """
    )


def downgrade() -> None:
    for table in ("rerun_requests", "access_decisions", "result_dependencies", "historical_results", "source_access", "workspace_permission_rules", "workspace_policies", "tenant_permission_rules", "tenant_policies", "memberships", "tenant_roles", "workspaces", "schema_metadata"):
        op.execute(f"DROP TABLE IF EXISTS identity_auth_{table}")
