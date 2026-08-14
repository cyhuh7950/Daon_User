"""Dedicated append-only security audit persistence."""

from __future__ import annotations

from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE security_audit_events (
        tenant_id text NOT NULL, sequence bigint NOT NULL, event_id text NOT NULL UNIQUE,
        occurred_at timestamptz NOT NULL, actor_id text NOT NULL, actor_type text NOT NULL,
        workspace_id text, action text NOT NULL, target_type text NOT NULL, target_id text NOT NULL,
        outcome text NOT NULL CHECK (outcome IN ('succeeded','denied','failed')),
        trace_id text NOT NULL, policy_version text NOT NULL, before_value jsonb,
        after_value jsonb, metadata jsonb NOT NULL DEFAULT '{}'::jsonb, safe_code text,
        previous_event_hash text NOT NULL CHECK (previous_event_hash ~ '^[0-9a-f]{64}$'),
        event_hash text NOT NULL CHECK (event_hash ~ '^[0-9a-f]{64}$'),
        PRIMARY KEY (tenant_id, sequence)
      );
      CREATE OR REPLACE FUNCTION reject_security_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'SECURITY_AUDIT_IMMUTABLE' USING ERRCODE='55000'; END $$;
      CREATE TRIGGER security_audit_events_immutable BEFORE UPDATE OR DELETE ON security_audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_security_audit_mutation();
      ALTER TABLE security_audit_events ENABLE ROW LEVEL SECURITY;
      ALTER TABLE security_audit_events FORCE ROW LEVEL SECURITY;
      CREATE POLICY security_audit_events_scope ON security_audit_events
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), ''))
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), ''));
      GRANT SELECT, INSERT ON security_audit_events TO daon_app;
      REVOKE UPDATE, DELETE ON security_audit_events FROM daon_app;
    """)


def downgrade() -> None:
    op.execute("""
      DO $$
      BEGIN
        IF EXISTS (SELECT 1 FROM security_audit_events) THEN
          RAISE EXCEPTION 'SECURITY_AUDIT_DOWNGRADE_BLOCKED' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE IF EXISTS security_audit_events;
      DROP FUNCTION IF EXISTS reject_security_audit_mutation();
    """)
