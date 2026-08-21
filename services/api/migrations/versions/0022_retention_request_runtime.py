"""Durable Source deletion request runtime locator and idempotency."""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
      CREATE OR REPLACE FUNCTION guard_retention_request_update()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' OR current_setting('app.retention_transition',true) IS DISTINCT FROM 'allowed' THEN
          RAISE EXCEPTION 'RETENTION_IMMUTABLE_MUTATION' USING ERRCODE='55000';
        END IF;
        IF NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'RETENTION_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
        IF NEW.state IN ('cleanup_pending','purged') AND EXISTS (SELECT 1 FROM legal_holds WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND source_id=NEW.source_id AND state='active') THEN
          RAISE EXCEPTION 'LEGAL_HOLD_ACTIVE' USING ERRCODE='55000';
        END IF;
        IF NEW.state='cleanup_pending' AND clock_timestamp()<NEW.grace_until THEN RAISE EXCEPTION 'DELETION_GRACE_PERIOD_ACTIVE' USING ERRCODE='55000'; END IF;
        IF NEW.state='purged' AND EXISTS (SELECT 1 FROM deletion_cleanup_items WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND request_id=NEW.request_id AND state<>'completed') THEN
          RAISE EXCEPTION 'DELETION_CLEANUP_PENDING' USING ERRCODE='55000';
        END IF;
        IF NOT ((OLD.state,NEW.state) IN (
          ('requested','deactivated'),('deactivated','grace_period'),('grace_period','cancelled'),
          ('blocked_by_hold','cancelled'),('grace_period','blocked_by_hold'),('grace_period','cleanup_pending'),
          ('blocked_by_hold','grace_period'),('blocked_by_hold','cleanup_pending'),('cleanup_pending','cleanup_pending'),
          ('cleanup_pending','blocked_by_hold'),('cleanup_pending','failed'),('failed','cleanup_pending'),('cleanup_pending','purged')
        )) THEN RAISE EXCEPTION 'RETENTION_STATE_TRANSITION_INVALID' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END $$;
      ALTER TABLE deletion_request_locator
        DROP CONSTRAINT deletion_request_locator_tenant_id_workspace_id_source_id_key;
      ALTER TABLE deletion_requests
        DROP CONSTRAINT deletion_requests_tenant_id_workspace_id_source_id_key;
      CREATE UNIQUE INDEX deletion_requests_one_current_source
        ON deletion_requests (tenant_id,workspace_id,source_id)
        WHERE state NOT IN ('cancelled','purged');
      CREATE TABLE source_retention_locator (
        tenant_id text NOT NULL, source_id text NOT NULL, workspace_id text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id,source_id,workspace_id)
      );
      INSERT INTO source_retention_locator (tenant_id,source_id,workspace_id,created_at)
        SELECT tenant_id,record_id,workspace_id,created_at FROM sources ON CONFLICT DO NOTHING;
      CREATE FUNCTION register_source_retention_locator() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        INSERT INTO source_retention_locator (tenant_id,source_id,workspace_id,created_at)
        VALUES (NEW.tenant_id,NEW.record_id,NEW.workspace_id,NEW.created_at) ON CONFLICT DO NOTHING;
        RETURN NEW;
      END $$;
      CREATE TRIGGER sources_retention_locator AFTER INSERT ON sources
        FOR EACH ROW EXECUTE FUNCTION register_source_retention_locator();
      ALTER TABLE source_retention_locator ENABLE ROW LEVEL SECURITY;
      ALTER TABLE source_retention_locator FORCE ROW LEVEL SECURITY;
      CREATE POLICY source_retention_locator_scope ON source_retention_locator USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'')
      ) WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),''));
      GRANT SELECT,INSERT ON source_retention_locator TO daon_app;
      REVOKE UPDATE,DELETE ON source_retention_locator FROM daon_app;

      ALTER TABLE deletion_cleanup_items ADD COLUMN inventory_disposition text NOT NULL DEFAULT 'present'
        CHECK (inventory_disposition IN ('present','not_present','not_applicable','verification_pending'));
      CREATE TABLE retention_request_idempotency (
        tenant_id text NOT NULL, workspace_id text NOT NULL, actor_id text NOT NULL,
        action text NOT NULL CHECK (action IN ('create','cancel')),
        idempotency_key text NOT NULL, request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        request_id text NOT NULL, result_version integer NOT NULL CHECK (result_version > 0), created_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,workspace_id,actor_id,action,idempotency_key),
        FOREIGN KEY (tenant_id,workspace_id,request_id) REFERENCES deletion_requests(tenant_id,workspace_id,request_id)
      );
      CREATE TRIGGER retention_request_idempotency_immutable BEFORE UPDATE OR DELETE ON retention_request_idempotency
        FOR EACH ROW EXECUTE FUNCTION reject_retention_immutable_mutation();
      ALTER TABLE retention_request_idempotency ENABLE ROW LEVEL SECURITY;
      ALTER TABLE retention_request_idempotency FORCE ROW LEVEL SECURITY;
      CREATE POLICY retention_request_idempotency_scope ON retention_request_idempotency USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );
      GRANT SELECT,INSERT ON retention_request_idempotency TO daon_app;
      REVOKE UPDATE,DELETE ON retention_request_idempotency FROM daon_app;

      CREATE TABLE legal_hold_locator (
        tenant_id text NOT NULL, hold_id text NOT NULL, workspace_id text NOT NULL,
        source_id text NOT NULL, created_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,hold_id)
      );
      CREATE TABLE retention_sensitive_idempotency (
        tenant_id text NOT NULL, workspace_id text NOT NULL, actor_id text NOT NULL,
        action text NOT NULL CHECK (action IN ('hold','release','purge')),
        idempotency_key text NOT NULL, request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        target_id text NOT NULL, result_id text NOT NULL, result_version integer NOT NULL CHECK (result_version > 0),
        created_at timestamptz NOT NULL,
        PRIMARY KEY (tenant_id,workspace_id,actor_id,action,idempotency_key)
      );
      CREATE TRIGGER legal_hold_locator_immutable BEFORE UPDATE OR DELETE ON legal_hold_locator
        FOR EACH ROW EXECUTE FUNCTION reject_retention_immutable_mutation();
      CREATE TRIGGER retention_sensitive_idempotency_immutable BEFORE UPDATE OR DELETE ON retention_sensitive_idempotency
        FOR EACH ROW EXECUTE FUNCTION reject_retention_immutable_mutation();
      ALTER TABLE legal_hold_locator ENABLE ROW LEVEL SECURITY;
      ALTER TABLE legal_hold_locator FORCE ROW LEVEL SECURITY;
      CREATE POLICY legal_hold_locator_scope ON legal_hold_locator USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'')
      ) WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),''));
      ALTER TABLE retention_sensitive_idempotency ENABLE ROW LEVEL SECURITY;
      ALTER TABLE retention_sensitive_idempotency FORCE ROW LEVEL SECURITY;
      CREATE POLICY retention_sensitive_idempotency_scope ON retention_sensitive_idempotency USING (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      ) WITH CHECK (
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      );
      GRANT SELECT,INSERT ON legal_hold_locator,retention_sensitive_idempotency TO daon_app;
      REVOKE UPDATE,DELETE ON legal_hold_locator,retention_sensitive_idempotency FROM daon_app;

      CREATE FUNCTION lock_retention_source_inventory_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE locked_source_id text;
      BEGIN
        IF TG_TABLE_NAME='source_versions' THEN
          locked_source_id := NEW.source_id;
        ELSE
          SELECT source_id INTO locked_source_id FROM source_versions
          WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id
            AND record_id=NEW.source_version_id;
        END IF;
        IF locked_source_id IS NULL THEN
          RAISE EXCEPTION 'RETENTION_SOURCE_SCOPE_INVALID' USING ERRCODE='23503';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'retention-source|'||NEW.tenant_id||'|'||NEW.workspace_id||'|'||locked_source_id, 0
        ));
        RETURN NEW;
      END $$;
      CREATE TRIGGER source_versions_retention_inventory_lock BEFORE INSERT ON source_versions
        FOR EACH ROW EXECUTE FUNCTION lock_retention_source_inventory_mutation();
      CREATE TRIGGER index_versions_retention_inventory_lock BEFORE INSERT ON index_versions
        FOR EACH ROW EXECUTE FUNCTION lock_retention_source_inventory_mutation();
      CREATE TRIGGER sync_preview_retention_inventory_lock BEFORE INSERT ON sync_preview_items
        FOR EACH ROW EXECUTE FUNCTION lock_retention_source_inventory_mutation();
    """)


def downgrade() -> None:
    op.execute(r"""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM retention_request_idempotency) OR EXISTS (SELECT 1 FROM retention_sensitive_idempotency)
           OR EXISTS (SELECT 1 FROM deletion_requests) OR EXISTS (SELECT 1 FROM legal_holds) THEN
          RAISE EXCEPTION 'RETENTION_RUNTIME_DOWNGRADE_BLOCKED' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE retention_sensitive_idempotency;
      DROP TABLE legal_hold_locator;
      DROP TABLE retention_request_idempotency;
      ALTER TABLE deletion_cleanup_items DROP COLUMN inventory_disposition;
      DROP TRIGGER sources_retention_locator ON sources;
      DROP FUNCTION register_source_retention_locator();
      DROP TABLE source_retention_locator;
      DROP TRIGGER sync_preview_retention_inventory_lock ON sync_preview_items;
      DROP TRIGGER index_versions_retention_inventory_lock ON index_versions;
      DROP TRIGGER source_versions_retention_inventory_lock ON source_versions;
      DROP FUNCTION lock_retention_source_inventory_mutation();
      DROP INDEX deletion_requests_one_current_source;
      ALTER TABLE deletion_requests
        ADD CONSTRAINT deletion_requests_tenant_id_workspace_id_source_id_key
        UNIQUE (tenant_id,workspace_id,source_id);
      ALTER TABLE deletion_request_locator
        ADD CONSTRAINT deletion_request_locator_tenant_id_workspace_id_source_id_key
        UNIQUE (tenant_id,workspace_id,source_id);
      CREATE OR REPLACE FUNCTION guard_retention_request_update()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' OR current_setting('app.retention_transition',true) IS DISTINCT FROM 'allowed' THEN RAISE EXCEPTION 'RETENTION_IMMUTABLE_MUTATION' USING ERRCODE='55000'; END IF;
        IF NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'RETENTION_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
        IF NEW.state IN ('cleanup_pending','purged') AND EXISTS (SELECT 1 FROM legal_holds WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND source_id=NEW.source_id AND state='active') THEN RAISE EXCEPTION 'LEGAL_HOLD_ACTIVE' USING ERRCODE='55000'; END IF;
        IF NEW.state='cleanup_pending' AND clock_timestamp()<NEW.grace_until THEN RAISE EXCEPTION 'DELETION_GRACE_PERIOD_ACTIVE' USING ERRCODE='55000'; END IF;
        IF NEW.state='purged' AND EXISTS (SELECT 1 FROM deletion_cleanup_items WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND request_id=NEW.request_id AND state<>'completed') THEN RAISE EXCEPTION 'DELETION_CLEANUP_PENDING' USING ERRCODE='55000'; END IF;
        IF NOT ((OLD.state,NEW.state) IN (('requested','deactivated'),('deactivated','grace_period'),('grace_period','cancelled'),('grace_period','blocked_by_hold'),('grace_period','cleanup_pending'),('blocked_by_hold','grace_period'),('blocked_by_hold','cleanup_pending'),('cleanup_pending','cleanup_pending'),('cleanup_pending','blocked_by_hold'),('cleanup_pending','failed'),('failed','cleanup_pending'),('cleanup_pending','purged'))) THEN RAISE EXCEPTION 'RETENTION_STATE_TRANSITION_INVALID' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END $$;
    """)
