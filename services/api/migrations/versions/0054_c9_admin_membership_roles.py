"""Add authorization-bound role-change idempotency and audit outbox.

Additive migration: existing membership, ACL and organization workflow rows are
not rewritten. After operations exist, recover forward with the new API disabled
or a previous image; do not drop the audit history on downgrade.
"""

from __future__ import annotations

from alembic import op


revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE identity_auth_admin_role_operations (
          actor_id text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (length(request_fingerprint) = 64),
          event_id text NOT NULL UNIQUE,
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          user_id text NOT NULL,
          old_role text NOT NULL CHECK (old_role IN
            ('workspace_admin','editor','reviewer','approver','viewer')),
          new_role text NOT NULL CHECK (new_role IN
            ('workspace_admin','editor','reviewer','approver','viewer')),
          old_version integer NOT NULL CHECK (old_version >= 1),
          new_version integer NOT NULL CHECK (new_version >= old_version),
          acl_version integer NOT NULL CHECK (acl_version >= 1),
          reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 256),
          outcome text NOT NULL CHECK (outcome IN ('changed','unchanged')),
          created_at timestamptz NOT NULL,
          delivered_at timestamptz,
          PRIMARY KEY (actor_id,idempotency_key),
          FOREIGN KEY (tenant_id,workspace_id)
            REFERENCES identity_auth_workspaces(tenant_id,workspace_id)
        );
        CREATE INDEX identity_auth_admin_role_operations_scope_created_idx
          ON identity_auth_admin_role_operations(tenant_id,workspace_id,created_at);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $c9_role$
        BEGIN
          IF EXISTS (SELECT 1 FROM identity_auth_admin_role_operations LIMIT 1) THEN
            RAISE EXCEPTION 'C9_ADMIN_ROLE_AUDIT_DOWNGRADE_BLOCKED';
          END IF;
        END
        $c9_role$;
        DROP TABLE identity_auth_admin_role_operations;
        """
    )
