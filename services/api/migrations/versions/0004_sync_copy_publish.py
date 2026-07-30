"""Approved sync copy/publish, resumable batches and explicit conflicts."""

from __future__ import annotations

from alembic import op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


TABLES = (
    "sync_operation_locator", "sync_operations", "sync_preview_items", "sync_approval_snapshots", "sync_manifest_items",
    "sync_transfer_batches", "sync_transfer_attempts", "sync_conflicts",
    "sync_conflict_resolutions", "sync_target_versions", "sync_reindex_requests",
)

IMMUTABLE_TABLES = tuple(table for table in TABLES if table != "sync_operations")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sync_operation_locator (
          tenant_id text NOT NULL,
          operation_id text NOT NULL,
          workspace_id text NOT NULL,
          actor_id text NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, operation_id),
          FOREIGN KEY (tenant_id, workspace_id)
            REFERENCES workspaces(tenant_id, workspace_id)
        );

        CREATE TABLE sync_operations (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          operation_id text NOT NULL,
          actor_id text NOT NULL,
          target_area text NOT NULL CHECK (target_area = 'cloud_sync'),
          state text NOT NULL CHECK (state IN (
            'preview','awaiting_approval','approved','transferring','conflict',
            'reindex_requested','failed','cancelled'
          )),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          preview_digest text NOT NULL CHECK (preview_digest ~ '^[0-9a-f]{64}$'),
          policy_version text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          trace_id text NOT NULL,
          state_document jsonb NOT NULL,
          created_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, operation_id),
          UNIQUE (tenant_id, workspace_id, actor_id, idempotency_key),
          FOREIGN KEY (tenant_id, workspace_id)
            REFERENCES workspaces(tenant_id, workspace_id)
        );

        CREATE TABLE sync_approval_snapshots (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          approval_snapshot_id text NOT NULL,
          operation_id text NOT NULL,
          actor_id text NOT NULL,
          target_area text NOT NULL CHECK (target_area = 'cloud_sync'),
          manifest_digest text NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
          policy_version text NOT NULL,
          step_up_authorization_digest text NOT NULL CHECK (
            step_up_authorization_digest ~ '^[0-9a-f]{64}$'
          ),
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          approved_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, approval_snapshot_id),
          UNIQUE (tenant_id, workspace_id, operation_id),
          UNIQUE (tenant_id, workspace_id, actor_id, idempotency_key),
          FOREIGN KEY (tenant_id, workspace_id, operation_id)
            REFERENCES sync_operations(tenant_id, workspace_id, operation_id)
        );

        CREATE TABLE sync_preview_items (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          operation_id text NOT NULL,
          item_id text NOT NULL,
          source_version_id text NOT NULL,
          local_object_id text NOT NULL,
          object_digest text NOT NULL CHECK (object_digest ~ '^[0-9a-f]{64}$'),
          byte_size bigint NOT NULL CHECK (byte_size >= 0),
          content_type text NOT NULL,
          base_cloud_version_id text,
          base_cloud_digest text CHECK (
            base_cloud_digest IS NULL OR base_cloud_digest ~ '^[0-9a-f]{64}$'
          ),
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, operation_id, item_id),
          CHECK ((base_cloud_version_id IS NULL) = (base_cloud_digest IS NULL)),
          FOREIGN KEY (tenant_id, workspace_id, operation_id)
            REFERENCES sync_operations(tenant_id, workspace_id, operation_id)
        );

        CREATE TABLE sync_manifest_items (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          approval_snapshot_id text NOT NULL,
          operation_id text NOT NULL,
          item_id text NOT NULL,
          source_version_id text NOT NULL,
          local_object_id text NOT NULL,
          object_digest text NOT NULL CHECK (object_digest ~ '^[0-9a-f]{64}$'),
          byte_size bigint NOT NULL CHECK (byte_size >= 0),
          content_type text NOT NULL,
          base_cloud_version_id text,
          base_cloud_digest text CHECK (
            base_cloud_digest IS NULL OR base_cloud_digest ~ '^[0-9a-f]{64}$'
          ),
          approved_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, approval_snapshot_id, item_id),
          UNIQUE (tenant_id, workspace_id, operation_id, item_id),
          CHECK ((base_cloud_version_id IS NULL) = (base_cloud_digest IS NULL)),
          FOREIGN KEY (tenant_id, workspace_id, approval_snapshot_id)
            REFERENCES sync_approval_snapshots(tenant_id, workspace_id, approval_snapshot_id),
          FOREIGN KEY (tenant_id, workspace_id, operation_id)
            REFERENCES sync_operations(tenant_id, workspace_id, operation_id)
        );

        CREATE TABLE sync_transfer_batches (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          batch_id text NOT NULL,
          operation_id text NOT NULL,
          approval_snapshot_id text NOT NULL,
          actor_id text NOT NULL,
          sequence integer NOT NULL CHECK (sequence > 0),
          cursor_value text,
          next_cursor text,
          state text NOT NULL CHECK (state IN ('transferred','partial','conflict','failed')),
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, batch_id),
          UNIQUE (tenant_id, workspace_id, operation_id, sequence),
          UNIQUE (tenant_id, workspace_id, actor_id, idempotency_key),
          FOREIGN KEY (tenant_id, workspace_id, operation_id)
            REFERENCES sync_operations(tenant_id, workspace_id, operation_id),
          FOREIGN KEY (tenant_id, workspace_id, approval_snapshot_id)
            REFERENCES sync_approval_snapshots(tenant_id, workspace_id, approval_snapshot_id)
        );

        CREATE TABLE sync_transfer_attempts (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          attempt_id text NOT NULL,
          batch_id text NOT NULL,
          operation_id text NOT NULL,
          approval_snapshot_id text NOT NULL,
          item_id text NOT NULL,
          outcome text NOT NULL CHECK (outcome IN (
            'transferred','already_completed','conflict','denied','failed'
          )),
          transferred_digest text CHECK (
            transferred_digest IS NULL OR transferred_digest ~ '^[0-9a-f]{64}$'
          ),
          safe_error_code text,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, attempt_id),
          FOREIGN KEY (tenant_id, workspace_id, batch_id)
            REFERENCES sync_transfer_batches(tenant_id, workspace_id, batch_id),
          FOREIGN KEY (tenant_id, workspace_id, operation_id, item_id)
            REFERENCES sync_manifest_items(tenant_id, workspace_id, operation_id, item_id)
        );

        CREATE TABLE sync_conflicts (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          conflict_id text NOT NULL,
          operation_id text NOT NULL,
          approval_snapshot_id text NOT NULL,
          item_id text NOT NULL,
          local_version_id text NOT NULL,
          local_digest text NOT NULL CHECK (local_digest ~ '^[0-9a-f]{64}$'),
          cloud_version_id text,
          cloud_digest text CHECK (cloud_digest IS NULL OR cloud_digest ~ '^[0-9a-f]{64}$'),
          base_version_id text,
          base_digest text CHECK (base_digest IS NULL OR base_digest ~ '^[0-9a-f]{64}$'),
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, conflict_id),
          UNIQUE (tenant_id, workspace_id, operation_id, item_id),
          CHECK ((cloud_version_id IS NULL) = (cloud_digest IS NULL)),
          CHECK ((base_version_id IS NULL) = (base_digest IS NULL)),
          FOREIGN KEY (tenant_id, workspace_id, operation_id, item_id)
            REFERENCES sync_manifest_items(tenant_id, workspace_id, operation_id, item_id),
          FOREIGN KEY (tenant_id, workspace_id, approval_snapshot_id)
            REFERENCES sync_approval_snapshots(tenant_id, workspace_id, approval_snapshot_id)
        );

        CREATE TABLE sync_conflict_resolutions (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          resolution_id text NOT NULL,
          conflict_id text NOT NULL,
          operation_id text NOT NULL,
          actor_id text NOT NULL,
          choice text NOT NULL CHECK (choice IN (
            'keep_local_as_new_version','keep_cloud','keep_both'
          )),
          resulting_target_version_id text,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          trace_id text NOT NULL,
          resolved_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, resolution_id),
          UNIQUE (tenant_id, workspace_id, conflict_id),
          UNIQUE (tenant_id, workspace_id, actor_id, idempotency_key),
          FOREIGN KEY (tenant_id, workspace_id, conflict_id)
            REFERENCES sync_conflicts(tenant_id, workspace_id, conflict_id),
          FOREIGN KEY (tenant_id, workspace_id, operation_id)
            REFERENCES sync_operations(tenant_id, workspace_id, operation_id)
        );

        CREATE TABLE sync_target_versions (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          target_version_id text NOT NULL,
          operation_id text NOT NULL,
          approval_snapshot_id text NOT NULL,
          item_id text NOT NULL,
          object_id text NOT NULL,
          digest_sha256 text NOT NULL CHECK (digest_sha256 ~ '^[0-9a-f]{64}$'),
          previous_cloud_version_id text,
          relation text NOT NULL CHECK (relation IN (
            'copy','keep_local_as_new_version','keep_both'
          )),
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, target_version_id),
          UNIQUE (tenant_id, workspace_id, operation_id, item_id),
          FOREIGN KEY (tenant_id, workspace_id, operation_id, item_id)
            REFERENCES sync_manifest_items(tenant_id, workspace_id, operation_id, item_id),
          FOREIGN KEY (tenant_id, workspace_id, approval_snapshot_id)
            REFERENCES sync_approval_snapshots(tenant_id, workspace_id, approval_snapshot_id),
          FOREIGN KEY (tenant_id, workspace_id, object_id)
            REFERENCES object_records(tenant_id, workspace_id, object_id)
        );

        CREATE TABLE sync_reindex_requests (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          reindex_request_id text NOT NULL,
          operation_id text NOT NULL,
          target_version_id text NOT NULL,
          state text NOT NULL CHECK (state = 'reindex_requested'),
          trace_id text NOT NULL,
          requested_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, reindex_request_id),
          UNIQUE (tenant_id, workspace_id, operation_id, target_version_id),
          FOREIGN KEY (tenant_id, workspace_id, operation_id)
            REFERENCES sync_operations(tenant_id, workspace_id, operation_id),
          FOREIGN KEY (tenant_id, workspace_id, target_version_id)
            REFERENCES sync_target_versions(tenant_id, workspace_id, target_version_id)
        );

        CREATE INDEX sync_operations_state_idx
          ON sync_operations (tenant_id, workspace_id, state, updated_at);
        CREATE INDEX sync_batches_resume_idx
          ON sync_transfer_batches (tenant_id, workspace_id, operation_id, sequence);

        CREATE OR REPLACE FUNCTION reject_sync_immutable_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'SYNC_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
        END;
        $$;

        CREATE OR REPLACE FUNCTION guard_sync_operation_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'SYNC_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
          END IF;
          IF current_setting('app.sync_transition', true) IS DISTINCT FROM 'allowed' THEN
            RAISE EXCEPTION 'SYNC_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
          END IF;
          IF NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'SYNC_VERSION_CONFLICT' USING ERRCODE = '40001';
          END IF;
          IF NOT ((OLD.state, NEW.state) IN (
            ('preview','awaiting_approval'),
            ('awaiting_approval','approved'), ('awaiting_approval','cancelled'),
            ('awaiting_approval','failed'), ('approved','transferring'),
            ('approved','conflict'), ('approved','reindex_requested'),
            ('approved','cancelled'), ('approved','failed'),
            ('transferring','transferring'), ('transferring','conflict'),
            ('transferring','reindex_requested'), ('transferring','cancelled'),
            ('transferring','failed'), ('conflict','transferring'),
            ('conflict','conflict'),
            ('conflict','reindex_requested'), ('conflict','cancelled'),
            ('conflict','failed')
          )) THEN
            RAISE EXCEPTION 'SYNC_STATE_TRANSITION_INVALID' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER sync_operations_guard
          BEFORE UPDATE OR DELETE ON sync_operations
          FOR EACH ROW EXECUTE FUNCTION guard_sync_operation_update();
    """)

    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_sync_immutable_mutation()"
        )

    predicate = (
        "tenant_id = nullif(current_setting('app.tenant_id', true), '') "
        "AND workspace_id = nullif(current_setting('app.workspace_id', true), '')"
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        table_predicate = (
            "tenant_id = nullif(current_setting('app.tenant_id', true), '')"
            if table == "sync_operation_locator" else predicate
        )
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} USING ({table_predicate}) "
            f"WITH CHECK ({table_predicate})"
        )

    op.execute("GRANT SELECT, INSERT ON " + ", ".join(TABLES) + " TO daon_app")
    op.execute("GRANT UPDATE ON sync_operations TO daon_app")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS guard_sync_operation_update()")
    op.execute("DROP FUNCTION IF EXISTS reject_sync_immutable_mutation()")
