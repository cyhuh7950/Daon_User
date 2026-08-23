"""Persist organization creation/join requests and invitation state."""

from alembic import op


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organization_creation_requests (
          request_id text PRIMARY KEY,
          applicant_user_id text NOT NULL,
          requested_org_name text NOT NULL CHECK (length(requested_org_name) BETWEEN 1 AND 160),
          requested_org_identifier text NOT NULL CHECK (requested_org_identifier ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
          state text NOT NULL CHECK (state IN ('pending','approved','rejected')),
          decision_reason text,
          decided_by text,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX organization_creation_pending_unique
          ON organization_creation_requests(applicant_user_id) WHERE state = 'pending';

        CREATE TABLE invitation_codes (
          invitation_id text PRIMARY KEY,
          tenant_id text NOT NULL REFERENCES tenants(tenant_id),
          created_by text NOT NULL,
          code_digest text NOT NULL UNIQUE CHECK (length(code_digest) = 64),
          expires_at timestamptz NOT NULL,
          max_uses integer NOT NULL CHECK (max_uses > 0),
          used_count integer NOT NULL DEFAULT 0 CHECK (used_count >= 0 AND used_count <= max_uses),
          state text NOT NULL CHECK (state IN ('active','revoked','expired')),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX invitation_codes_tenant_state_idx ON invitation_codes(tenant_id,state);

        CREATE TABLE organization_join_requests (
          request_id text PRIMARY KEY,
          tenant_id text NOT NULL REFERENCES tenants(tenant_id),
          user_id text NOT NULL,
          invitation_id text REFERENCES invitation_codes(invitation_id),
          state text NOT NULL CHECK (state IN ('pending','approved','rejected')),
          decision_reason text,
          decided_by text,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX organization_join_pending_unique
          ON organization_join_requests(tenant_id,user_id) WHERE state = 'pending';
        CREATE INDEX organization_join_requests_tenant_state_idx
          ON organization_join_requests(tenant_id,state,created_at);

        CREATE TABLE tenant_memberships (
          tenant_id text NOT NULL REFERENCES tenants(tenant_id),
          user_id text NOT NULL,
          role text NOT NULL CHECK (role IN ('organization_admin','workspace_admin','editor','reviewer','approver','viewer')),
          state text NOT NULL CHECK (state IN ('active','suspended')),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id,user_id)
        );
        CREATE INDEX tenant_memberships_tenant_state_idx ON tenant_memberships(tenant_id,state);

        CREATE TABLE tenant_membership_role_history (
          history_id text PRIMARY KEY,
          tenant_id text NOT NULL REFERENCES tenants(tenant_id),
          user_id text NOT NULL,
          actor_id text NOT NULL,
          previous_role text,
          next_role text,
          previous_state text,
          next_state text,
          reason text,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX tenant_membership_role_history_lookup_idx
          ON tenant_membership_role_history(tenant_id,user_id,created_at);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS tenant_membership_role_history;
        DROP TABLE IF EXISTS tenant_memberships;
        DROP TABLE IF EXISTS organization_join_requests;
        DROP TABLE IF EXISTS invitation_codes;
        DROP TABLE IF EXISTS organization_creation_requests;
        """
    )
