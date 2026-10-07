"""Add a durable, session-FK-free audit intent for explicit tenant switches."""

from __future__ import annotations

from alembic import op


revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE identity_session_tenant_switch_outbox (
          event_id text PRIMARY KEY,
          prior_session_id text NOT NULL UNIQUE,
          new_session_id text NOT NULL UNIQUE,
          prior_tenant_id text NOT NULL,
          target_tenant_id text NOT NULL,
          actor_id text NOT NULL,
          outcome text NOT NULL CHECK (outcome = 'succeeded'),
          reason_code text NOT NULL CHECK (reason_code = 'USER_SELECTED'),
          occurred_at timestamptz NOT NULL,
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          delivered_at timestamptz,
          created_at timestamptz NOT NULL
        );
        CREATE INDEX identity_session_tenant_switch_outbox_pending_idx
          ON identity_session_tenant_switch_outbox(created_at,event_id)
          WHERE delivered_at IS NULL;
        CREATE FUNCTION identity_tenant_switch_outbox_guard() RETURNS trigger
        LANGUAGE plpgsql AS $switch_outbox$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'SESSION_TENANT_SWITCH_OUTBOX_DELETE_BLOCKED';
          END IF;
          IF (to_jsonb(NEW) - 'delivered_at') IS DISTINCT FROM
             (to_jsonb(OLD) - 'delivered_at') THEN
            RAISE EXCEPTION 'SESSION_TENANT_SWITCH_OUTBOX_INTENT_IMMUTABLE';
          END IF;
          IF OLD.delivered_at IS NOT NULL OR NEW.delivered_at IS NULL THEN
            RAISE EXCEPTION 'SESSION_TENANT_SWITCH_OUTBOX_DELIVERY_ONE_WAY';
          END IF;
          RETURN NEW;
        END
        $switch_outbox$;
        CREATE TRIGGER identity_tenant_switch_outbox_guard_trigger
          BEFORE UPDATE OR DELETE ON identity_session_tenant_switch_outbox
          FOR EACH ROW EXECUTE FUNCTION identity_tenant_switch_outbox_guard();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $switch_outbox$
        BEGIN
          IF EXISTS (SELECT 1 FROM identity_session_tenant_switch_outbox LIMIT 1) THEN
            RAISE EXCEPTION 'SESSION_TENANT_SWITCH_OUTBOX_DOWNGRADE_BLOCKED';
          END IF;
        END
        $switch_outbox$;
        DROP TABLE identity_session_tenant_switch_outbox;
        DROP FUNCTION identity_tenant_switch_outbox_guard();
        """
    )
