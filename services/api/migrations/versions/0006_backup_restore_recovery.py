"""R1-M5-07 verified backup and fixture-only isolated restore contract."""

from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


TABLES = (
    "backup_records", "backup_manifests", "restore_requests",
    "restore_previews", "restore_verifications",
)
LOCATOR_TABLES = ("backup_record_locator", "restore_request_locator")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE backup_record_locator (
          tenant_id text NOT NULL,
          backup_id text NOT NULL,
          workspace_id text NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, backup_id),
          FOREIGN KEY (tenant_id, workspace_id)
            REFERENCES workspaces(tenant_id, workspace_id)
        );
        CREATE TABLE backup_records (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          backup_id text NOT NULL,
          actor_id text NOT NULL,
          trigger_type text NOT NULL CHECK (trigger_type IN ('automatic','manual')),
          state text NOT NULL CHECK (state IN (
            'queued','capturing','verifying','ready','failed','expired'
          )),
          version integer NOT NULL CHECK (version > 0),
          schema_revision text NOT NULL,
          retention_watermark text NOT NULL,
          policy_version text NOT NULL,
          trace_id text NOT NULL,
          audit_event_id text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL,
          verified_at timestamptz,
          PRIMARY KEY (tenant_id, workspace_id, backup_id),
          UNIQUE (tenant_id, workspace_id, actor_id, idempotency_key),
          FOREIGN KEY (tenant_id, workspace_id)
            REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (tenant_id, backup_id)
            REFERENCES backup_record_locator(tenant_id, backup_id)
        );
        CREATE TABLE backup_manifests (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          backup_id text NOT NULL,
          manifest_version integer NOT NULL CHECK (manifest_version > 0),
          manifest_digest text NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
          encrypted_inventory bytea NOT NULL,
          object_count integer NOT NULL CHECK (object_count >= 0),
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, backup_id, manifest_version),
          FOREIGN KEY (tenant_id, workspace_id, backup_id)
            REFERENCES backup_records(tenant_id, workspace_id, backup_id)
        );
        CREATE TABLE restore_requests (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          request_id text NOT NULL,
          backup_id text NOT NULL,
          actor_id text NOT NULL,
          state text NOT NULL CHECK (state IN (
            'requested','preview_ready','authorized','restoring','verifying','completed',
            'cancelled','failed','blocked'
          )),
          version integer NOT NULL CHECK (version > 0),
          policy_version text NOT NULL,
          trace_id text NOT NULL,
          audit_event_id text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, request_id),
          UNIQUE (tenant_id, workspace_id, actor_id, idempotency_key),
          FOREIGN KEY (tenant_id, workspace_id)
            REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (tenant_id, workspace_id, backup_id)
            REFERENCES backup_records(tenant_id, workspace_id, backup_id)
        );
        CREATE TABLE restore_request_locator (
          tenant_id text NOT NULL,
          request_id text NOT NULL,
          workspace_id text NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, request_id),
          FOREIGN KEY (tenant_id, workspace_id)
            REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (tenant_id, workspace_id, request_id)
            REFERENCES restore_requests(tenant_id, workspace_id, request_id)
        );
        CREATE TABLE restore_previews (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          request_id text NOT NULL,
          preview_version integer NOT NULL CHECK (preview_version > 0),
          destination_tenant_id text NOT NULL,
          destination_workspace_id text NOT NULL,
          destination_database_id text NOT NULL,
          destination_bucket_id text NOT NULL,
          encrypted_adjustment_manifest bytea NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, request_id, preview_version),
          FOREIGN KEY (tenant_id, workspace_id, request_id)
            REFERENCES restore_requests(tenant_id, workspace_id, request_id),
          CHECK (destination_tenant_id LIKE 'fixture-%'),
          CHECK (destination_workspace_id LIKE 'fixture-%'),
          CHECK (destination_database_id LIKE 'fixture-%'),
          CHECK (destination_bucket_id LIKE 'fixture-%')
        );
        CREATE TABLE restore_verifications (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          verification_id text NOT NULL,
          request_id text NOT NULL,
          verification_digest text NOT NULL CHECK (verification_digest ~ '^[0-9a-f]{64}$'),
          lineage_verified boolean NOT NULL,
          rls_verified boolean NOT NULL,
          audit_chain_verified boolean NOT NULL,
          retention_rechecked boolean NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, verification_id),
          FOREIGN KEY (tenant_id, workspace_id, request_id)
            REFERENCES restore_requests(tenant_id, workspace_id, request_id)
        );
        CREATE FUNCTION reject_recovery_immutable_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'RECOVERY_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
        END;
        $$;
        CREATE FUNCTION guard_backup_record_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR NEW.tenant_id <> OLD.tenant_id
             OR NEW.workspace_id <> OLD.workspace_id OR NEW.backup_id <> OLD.backup_id
             OR NEW.actor_id <> OLD.actor_id OR NEW.trigger_type <> OLD.trigger_type
             OR NEW.schema_revision <> OLD.schema_revision
             OR NEW.retention_watermark <> OLD.retention_watermark
             OR NEW.policy_version <> OLD.policy_version OR NEW.trace_id <> OLD.trace_id
             OR NEW.audit_event_id <> OLD.audit_event_id
             OR NEW.idempotency_key <> OLD.idempotency_key
             OR NEW.request_fingerprint <> OLD.request_fingerprint
             OR NEW.created_at <> OLD.created_at OR NEW.version <> OLD.version + 1
             OR NOT ((OLD.state = 'queued' AND NEW.state IN ('capturing','failed'))
               OR (OLD.state = 'capturing' AND NEW.state IN ('verifying','failed'))
               OR (OLD.state = 'verifying' AND NEW.state IN ('ready','failed'))
               OR (OLD.state = 'ready' AND NEW.state = 'expired')) THEN
            RAISE EXCEPTION 'BACKUP_RECORD_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE FUNCTION guard_restore_request_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR NEW.tenant_id <> OLD.tenant_id
             OR NEW.workspace_id <> OLD.workspace_id OR NEW.request_id <> OLD.request_id
             OR NEW.backup_id <> OLD.backup_id OR NEW.actor_id <> OLD.actor_id
             OR NEW.policy_version <> OLD.policy_version OR NEW.trace_id <> OLD.trace_id
             OR NEW.audit_event_id <> OLD.audit_event_id
             OR NEW.idempotency_key <> OLD.idempotency_key
             OR NEW.request_fingerprint <> OLD.request_fingerprint
             OR NEW.created_at <> OLD.created_at OR NEW.version <> OLD.version + 1
             OR NOT ((OLD.state = 'requested' AND NEW.state IN ('preview_ready','cancelled','blocked','failed'))
               OR (OLD.state = 'preview_ready' AND NEW.state IN ('authorized','cancelled','blocked','failed'))
               OR (OLD.state = 'authorized' AND NEW.state IN ('restoring','cancelled','blocked','failed'))
               OR (OLD.state = 'restoring' AND NEW.state IN ('verifying','failed'))
               OR (OLD.state = 'verifying' AND NEW.state IN ('completed','failed'))) THEN
            RAISE EXCEPTION 'RESTORE_REQUEST_IMMUTABLE_MUTATION' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER backup_records_guard BEFORE UPDATE OR DELETE ON backup_records
          FOR EACH ROW EXECUTE FUNCTION guard_backup_record_update();
        CREATE TRIGGER restore_requests_guard BEFORE UPDATE OR DELETE ON restore_requests
          FOR EACH ROW EXECUTE FUNCTION guard_restore_request_update();
    """)
    for table in ("backup_manifests", "restore_previews", "restore_verifications"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_recovery_immutable_mutation()"
        )
    for table in LOCATOR_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_recovery_immutable_mutation()"
        )
    predicate = (
        "tenant_id = nullif(current_setting('app.tenant_id', true), '') AND "
        "workspace_id = nullif(current_setting('app.workspace_id', true), '')"
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} USING ({predicate}) "
            f"WITH CHECK ({predicate})"
        )
    tenant_predicate = "tenant_id = nullif(current_setting('app.tenant_id', true), '')"
    for table in LOCATOR_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_scope ON {table} USING ({tenant_predicate}) "
            f"WITH CHECK ({tenant_predicate})"
        )
    op.execute(
        "GRANT SELECT, INSERT ON " + ", ".join(LOCATOR_TABLES + TABLES) + " TO daon_app"
    )
    op.execute("GRANT UPDATE ON backup_records, restore_requests TO daon_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS restore_request_locator CASCADE")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP TABLE IF EXISTS backup_record_locator CASCADE")
    op.execute("DROP FUNCTION IF EXISTS guard_restore_request_update()")
    op.execute("DROP FUNCTION IF EXISTS guard_backup_record_update()")
    op.execute("DROP FUNCTION IF EXISTS reject_recovery_immutable_mutation()")
