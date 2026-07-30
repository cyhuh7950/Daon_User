"""R1-M5-06 normalized deletion, retention, and Legal Hold contract."""

from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


TABLES = (
    "deletion_request_locator", "deletion_requests", "deletion_cleanup_items",
    "deletion_attempts", "legal_holds", "legal_hold_targets", "retention_lineage",
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE deletion_request_locator (
          tenant_id text NOT NULL,
          request_id text NOT NULL,
          workspace_id text NOT NULL,
          source_id text NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, request_id),
          UNIQUE (tenant_id, workspace_id, source_id)
        );
        CREATE TABLE deletion_requests (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          request_id text NOT NULL,
          source_id text NOT NULL,
          actor_id text NOT NULL,
          state text NOT NULL CHECK (state IN (
            'requested','deactivated','grace_period','cleanup_pending','purged',
            'cancelled','blocked_by_hold','failed'
          )),
          version integer NOT NULL CHECK (version > 0),
          source_active boolean NOT NULL DEFAULT false,
          purge_started boolean NOT NULL DEFAULT false,
          grace_until timestamptz NOT NULL,
          policy_version text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, request_id),
          UNIQUE (tenant_id, workspace_id, actor_id, idempotency_key),
          UNIQUE (tenant_id, workspace_id, source_id),
          FOREIGN KEY (tenant_id, request_id)
            REFERENCES deletion_request_locator(tenant_id, request_id)
        );
        CREATE TABLE deletion_cleanup_items (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          request_id text NOT NULL,
          reference_id text NOT NULL,
          derivative_kind text NOT NULL CHECK (derivative_kind IN (
            'original_content','index','preview','cache','known_local_copy','sync_reference'
          )),
          state text NOT NULL CHECK (state IN ('pending','awaiting_ack','completed','failed')),
          acknowledgement_required boolean NOT NULL,
          attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
          evidence_kind text CHECK (evidence_kind IN ('device_ack','device_revoked','key_destroyed')),
          updated_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, request_id, reference_id),
          FOREIGN KEY (tenant_id, workspace_id, request_id)
            REFERENCES deletion_requests(tenant_id, workspace_id, request_id),
          CHECK ((derivative_kind = 'known_local_copy') = acknowledgement_required)
        );
        CREATE TABLE deletion_attempts (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          attempt_id text NOT NULL,
          request_id text NOT NULL,
          reference_id text NOT NULL,
          attempt_number integer NOT NULL CHECK (attempt_number > 0),
          outcome text NOT NULL CHECK (outcome IN ('completed','failed','awaiting_ack')),
          trace_id text NOT NULL,
          attempted_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, attempt_id),
          UNIQUE (tenant_id, workspace_id, request_id, reference_id, attempt_number),
          FOREIGN KEY (tenant_id, workspace_id, request_id, reference_id)
            REFERENCES deletion_cleanup_items(tenant_id, workspace_id, request_id, reference_id)
        );
        CREATE TABLE legal_holds (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          hold_id text NOT NULL,
          source_id text NOT NULL,
          actor_id text NOT NULL,
          state text NOT NULL CHECK (state IN ('active','released')),
          version integer NOT NULL CHECK (version > 0),
          policy_version text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL,
          released_at timestamptz,
          PRIMARY KEY (tenant_id, workspace_id, hold_id),
          UNIQUE (tenant_id, workspace_id, actor_id, idempotency_key)
        );
        CREATE UNIQUE INDEX legal_holds_one_active_source
          ON legal_holds (tenant_id, workspace_id, source_id) WHERE state = 'active';
        CREATE TABLE legal_hold_targets (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          hold_id text NOT NULL,
          request_id text NOT NULL,
          source_id text NOT NULL,
          attached_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, hold_id, request_id),
          FOREIGN KEY (tenant_id, workspace_id, hold_id)
            REFERENCES legal_holds(tenant_id, workspace_id, hold_id),
          FOREIGN KEY (tenant_id, workspace_id, request_id)
            REFERENCES deletion_requests(tenant_id, workspace_id, request_id)
        );
        CREATE TABLE retention_lineage (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          lineage_id text NOT NULL,
          request_id text NOT NULL,
          actor_id text NOT NULL,
          action text NOT NULL,
          target_id text NOT NULL,
          policy_version text NOT NULL,
          trace_id text NOT NULL,
          previous_hash text NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
          event_hash text NOT NULL CHECK (event_hash ~ '^[0-9a-f]{64}$'),
          occurred_at timestamptz NOT NULL,
          retain_until timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, lineage_id),
          FOREIGN KEY (tenant_id, workspace_id, request_id)
            REFERENCES deletion_requests(tenant_id, workspace_id, request_id)
        );
        CREATE INDEX deletion_requests_state_idx
          ON deletion_requests (tenant_id, workspace_id, state, grace_until);
        CREATE INDEX deletion_cleanup_retry_idx
          ON deletion_cleanup_items (tenant_id, workspace_id, request_id, state);

        CREATE OR REPLACE FUNCTION reject_retention_immutable_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'RETENTION_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
        END;
        $$;
        CREATE OR REPLACE FUNCTION guard_retention_request_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR current_setting('app.retention_transition', true)
             IS DISTINCT FROM 'allowed' THEN
            RAISE EXCEPTION 'RETENTION_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
          END IF;
          IF NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'RETENTION_VERSION_CONFLICT' USING ERRCODE = '40001';
          END IF;
          IF NEW.state IN ('cleanup_pending','purged') AND EXISTS (
            SELECT 1 FROM legal_holds
            WHERE tenant_id = NEW.tenant_id AND workspace_id = NEW.workspace_id
              AND source_id = NEW.source_id AND state = 'active'
          ) THEN
            RAISE EXCEPTION 'LEGAL_HOLD_ACTIVE' USING ERRCODE = '55000';
          END IF;
          IF NEW.state = 'cleanup_pending' AND clock_timestamp() < NEW.grace_until THEN
            RAISE EXCEPTION 'DELETION_GRACE_PERIOD_ACTIVE' USING ERRCODE = '55000';
          END IF;
          IF NEW.state = 'purged' AND EXISTS (
            SELECT 1 FROM deletion_cleanup_items
            WHERE tenant_id = NEW.tenant_id AND workspace_id = NEW.workspace_id
              AND request_id = NEW.request_id AND state <> 'completed'
          ) THEN
            RAISE EXCEPTION 'DELETION_CLEANUP_PENDING' USING ERRCODE = '55000';
          END IF;
          IF NOT ((OLD.state, NEW.state) IN (
            ('requested','deactivated'), ('deactivated','grace_period'),
            ('grace_period','cancelled'), ('grace_period','blocked_by_hold'),
            ('grace_period','cleanup_pending'), ('blocked_by_hold','grace_period'),
            ('blocked_by_hold','cleanup_pending'), ('cleanup_pending','cleanup_pending'),
            ('cleanup_pending','blocked_by_hold'), ('cleanup_pending','failed'),
            ('failed','cleanup_pending'), ('cleanup_pending','purged')
          )) THEN
            RAISE EXCEPTION 'RETENTION_STATE_TRANSITION_INVALID' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE OR REPLACE FUNCTION guard_retention_cleanup_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR current_setting('app.retention_transition', true)
             IS DISTINCT FROM 'allowed' OR NEW.tenant_id <> OLD.tenant_id
             OR NEW.workspace_id <> OLD.workspace_id OR NEW.request_id <> OLD.request_id
             OR NEW.reference_id <> OLD.reference_id OR NEW.derivative_kind <> OLD.derivative_kind
             OR NEW.acknowledgement_required <> OLD.acknowledgement_required
             OR NEW.attempt_count < OLD.attempt_count
             OR OLD.state = 'completed' THEN
            RAISE EXCEPTION 'RETENTION_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE OR REPLACE FUNCTION guard_legal_hold_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR current_setting('app.retention_transition', true)
             IS DISTINCT FROM 'allowed' OR OLD.state <> 'active' OR NEW.state <> 'released'
             OR NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'RETENTION_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER deletion_requests_guard BEFORE UPDATE OR DELETE ON deletion_requests
          FOR EACH ROW EXECUTE FUNCTION guard_retention_request_update();
        CREATE TRIGGER legal_holds_guard BEFORE UPDATE OR DELETE ON legal_holds
          FOR EACH ROW EXECUTE FUNCTION guard_legal_hold_update();
        CREATE TRIGGER deletion_cleanup_items_guard
          BEFORE UPDATE OR DELETE ON deletion_cleanup_items
          FOR EACH ROW EXECUTE FUNCTION guard_retention_cleanup_update();
    """)
    for table in ("deletion_attempts", "legal_hold_targets", "retention_lineage"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_retention_immutable_mutation()"
        )
    predicate = (
        "tenant_id = nullif(current_setting('app.tenant_id', true), '') AND "
        "workspace_id = nullif(current_setting('app.workspace_id', true), '')"
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        table_predicate = (
            "tenant_id = nullif(current_setting('app.tenant_id', true), '')"
            if table == "deletion_request_locator" else predicate
        )
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} USING ({table_predicate}) "
            f"WITH CHECK ({table_predicate})"
        )
    op.execute("GRANT SELECT, INSERT ON " + ", ".join(TABLES) + " TO daon_app")
    op.execute("GRANT UPDATE ON deletion_requests, deletion_cleanup_items, legal_holds TO daon_app")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS guard_legal_hold_update()")
    op.execute("DROP FUNCTION IF EXISTS guard_retention_request_update()")
    op.execute("DROP FUNCTION IF EXISTS guard_retention_cleanup_update()")
    op.execute("DROP FUNCTION IF EXISTS reject_retention_immutable_mutation()")
