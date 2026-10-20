"""웹 import 멱등 원장과 불변 snapshot. 기존 Canon/역할의 의미는 변경하지 않는다."""
from alembic import op

revision = '0061'
down_revision = '0060'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE website_import_requests (
          tenant_id text NOT NULL, workspace_id text NOT NULL, actor_id text NOT NULL,
          notebook_id text NOT NULL, idempotency_key text NOT NULL,
          request_id text NOT NULL, fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
          requested_url text NOT NULL CHECK(length(requested_url)<=2048),
          state text NOT NULL CHECK(state IN ('fetching','storing','completed','failed')),
          attempt integer NOT NULL DEFAULT 1 CHECK(attempt>0), history jsonb NOT NULL DEFAULT '[]',
          raw_object_id text NOT NULL, text_object_id text NOT NULL,
          snapshot_id text, safe_error_code text, policy_version text NOT NULL, policy_fingerprint text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,workspace_id,request_id),
          UNIQUE(tenant_id,workspace_id,actor_id,notebook_id,idempotency_key),
          FOREIGN KEY(tenant_id,workspace_id,notebook_id) REFERENCES notebooks(tenant_id,workspace_id,notebook_id),
          CHECK(jsonb_typeof(history)='array'),
          CHECK((state='completed')=(snapshot_id IS NOT NULL))
        );
        CREATE TABLE website_source_snapshots (
          tenant_id text NOT NULL, workspace_id text NOT NULL, snapshot_id text NOT NULL,
          source_id text NOT NULL, source_version_id text NOT NULL, notebook_id text NOT NULL,
          request_id text NOT NULL, raw_object_id text NOT NULL, text_object_id text NOT NULL,
          requested_url text NOT NULL CHECK(length(requested_url)<=2048),
          final_url text NOT NULL CHECK(length(final_url)<=2048), redirect_urls jsonb NOT NULL,
          title text NOT NULL, published_at timestamptz, fetched_at timestamptz NOT NULL,
          license_status text NOT NULL CHECK(license_status IN ('unknown','publisher_declared')),
          license_url text, raw_sha256 text NOT NULL CHECK(raw_sha256 ~ '^[0-9a-f]{64}$'),
          text_sha256 text NOT NULL CHECK(text_sha256 ~ '^[0-9a-f]{64}$'),
          extraction_version text NOT NULL CHECK(extraction_version='website-text-v1'),
          policy_version text NOT NULL, policy_fingerprint text NOT NULL,
          created_by text NOT NULL, trace_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,workspace_id,snapshot_id),
          UNIQUE(tenant_id,workspace_id,source_version_id),
          FOREIGN KEY(tenant_id,workspace_id,request_id) REFERENCES website_import_requests(tenant_id,workspace_id,request_id),
          FOREIGN KEY(tenant_id,workspace_id,source_id) REFERENCES sources(tenant_id,workspace_id,record_id),
          FOREIGN KEY(tenant_id,workspace_id,source_version_id) REFERENCES source_versions(tenant_id,workspace_id,record_id),
          FOREIGN KEY(tenant_id,workspace_id,raw_object_id) REFERENCES object_records(tenant_id,workspace_id,object_id),
          FOREIGN KEY(tenant_id,workspace_id,text_object_id) REFERENCES object_records(tenant_id,workspace_id,object_id),
          FOREIGN KEY(tenant_id,workspace_id,notebook_id) REFERENCES notebooks(tenant_id,workspace_id,notebook_id),
          CHECK(jsonb_typeof(redirect_urls)='array' AND jsonb_array_length(redirect_urls)<=3),
          CHECK(license_status<>'unknown' OR license_url IS NULL)
        );
        ALTER TABLE website_import_requests ADD FOREIGN KEY(tenant_id,workspace_id,snapshot_id)
          REFERENCES website_source_snapshots(tenant_id,workspace_id,snapshot_id);
        CREATE FUNCTION guard_website_request_transition() RETURNS trigger LANGUAGE plpgsql
          SET search_path=pg_catalog,public AS $$ BEGIN
          IF TG_OP='DELETE' OR OLD.state='completed'
            OR NEW.tenant_id<>OLD.tenant_id OR NEW.workspace_id<>OLD.workspace_id
            OR NEW.actor_id<>OLD.actor_id OR NEW.notebook_id<>OLD.notebook_id
            OR NEW.idempotency_key<>OLD.idempotency_key OR NEW.fingerprint<>OLD.fingerprint
            OR NEW.requested_url<>OLD.requested_url OR NEW.created_at<>OLD.created_at
            OR NOT ((OLD.state='failed' AND NEW.state='fetching' AND NEW.attempt=OLD.attempt+1
                      AND NEW.request_id<>OLD.request_id AND jsonb_array_length(NEW.history)=jsonb_array_length(OLD.history)+1)
                OR (OLD.state IN ('fetching','storing') AND NEW.state IN ('storing','completed','failed')
                    AND NEW.attempt=OLD.attempt AND NEW.request_id=OLD.request_id
                    AND NEW.raw_object_id=OLD.raw_object_id AND NEW.text_object_id=OLD.text_object_id
                    AND NEW.policy_version=OLD.policy_version AND NEW.policy_fingerprint=OLD.policy_fingerprint
                    AND NEW.history=OLD.history))
          THEN RAISE EXCEPTION 'WEBSITE_REQUEST_IMMUTABLE' USING ERRCODE='55000'; END IF;
          RETURN NEW; END $$;
        CREATE TRIGGER website_request_transition BEFORE UPDATE OR DELETE ON website_import_requests
          FOR EACH ROW EXECUTE FUNCTION guard_website_request_transition();
        REVOKE ALL ON FUNCTION guard_website_request_transition() FROM PUBLIC;
        CREATE TRIGGER website_snapshot_immutable BEFORE UPDATE OR DELETE ON website_source_snapshots
          FOR EACH ROW EXECUTE FUNCTION reject_canon_immutable_mutation();
        CREATE FUNCTION guard_website_snapshot_binding() RETURNS trigger LANGUAGE plpgsql
          SET search_path=pg_catalog,public AS $$ BEGIN
          IF NOT EXISTS(SELECT 1 FROM public.website_import_requests r WHERE r.tenant_id=NEW.tenant_id
            AND r.workspace_id=NEW.workspace_id AND r.request_id=NEW.request_id
            AND r.actor_id=NEW.created_by AND r.notebook_id=NEW.notebook_id
            AND r.raw_object_id=NEW.raw_object_id AND r.text_object_id=NEW.text_object_id
            AND r.requested_url=NEW.requested_url AND r.policy_version=NEW.policy_version
            AND r.policy_fingerprint=NEW.policy_fingerprint AND r.state IN ('fetching','storing'))
            OR NOT EXISTS(SELECT 1 FROM public.source_versions v JOIN public.sources s ON
            s.tenant_id=v.tenant_id AND s.workspace_id=v.workspace_id AND s.record_id=v.source_id
            WHERE v.tenant_id=NEW.tenant_id AND v.workspace_id=NEW.workspace_id
            AND v.record_id=NEW.source_version_id AND v.source_id=NEW.source_id
            AND v.object_id=NEW.text_object_id AND s.state='ready')
            OR NOT EXISTS(SELECT 1 FROM public.object_records o WHERE o.tenant_id=NEW.tenant_id
            AND o.workspace_id=NEW.workspace_id AND o.object_id=NEW.raw_object_id
            AND o.status='completed' AND o.digest_sha256=NEW.raw_sha256)
            OR NOT EXISTS(SELECT 1 FROM public.object_records o WHERE o.tenant_id=NEW.tenant_id
            AND o.workspace_id=NEW.workspace_id AND o.object_id=NEW.text_object_id
            AND o.status='completed' AND o.digest_sha256=NEW.text_sha256)
            OR NOT EXISTS(SELECT 1 FROM public.index_versions i WHERE i.tenant_id=NEW.tenant_id
            AND i.workspace_id=NEW.workspace_id AND i.source_version_id=NEW.source_version_id)
            OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=NEW.tenant_id
            AND b.workspace_id=NEW.workspace_id AND b.notebook_id=NEW.notebook_id
            AND b.binding_kind='source' AND b.record_id=NEW.source_id AND b.version_id=NEW.source_version_id)
          THEN RAISE EXCEPTION 'WEBSITE_BINDING_INVALID' USING ERRCODE='23514'; END IF;
          RETURN NEW; END $$;
        CREATE TRIGGER website_snapshot_binding BEFORE INSERT ON website_source_snapshots
          FOR EACH ROW EXECUTE FUNCTION guard_website_snapshot_binding();
        REVOKE ALL ON FUNCTION guard_website_snapshot_binding() FROM PUBLIC;
        ALTER TABLE website_import_requests ENABLE ROW LEVEL SECURITY;
        ALTER TABLE website_import_requests FORCE ROW LEVEL SECURITY;
        CREATE POLICY website_request_scope ON website_import_requests USING(
          tenant_id=nullif(current_setting('app.tenant_id',true),'') AND
          workspace_id=nullif(current_setting('app.workspace_id',true),'') AND
          actor_id=nullif(current_setting('app.actor_id',true),'') AND
          notebook_id=nullif(current_setting('app.notebook_id',true),''));
        ALTER TABLE website_source_snapshots ENABLE ROW LEVEL SECURITY;
        ALTER TABLE website_source_snapshots FORCE ROW LEVEL SECURITY;
        CREATE POLICY website_snapshot_scope ON website_source_snapshots USING(
          tenant_id=nullif(current_setting('app.tenant_id',true),'') AND
          workspace_id=nullif(current_setting('app.workspace_id',true),'') AND
          created_by=nullif(current_setting('app.actor_id',true),'') AND
          notebook_id=nullif(current_setting('app.notebook_id',true),'') AND
          EXISTS(SELECT 1 FROM sources s WHERE s.tenant_id=website_source_snapshots.tenant_id
            AND s.workspace_id=website_source_snapshots.workspace_id
            AND s.record_id=website_source_snapshots.source_id AND s.state='ready') AND
          EXISTS(SELECT 1 FROM notebook_bindings b WHERE b.tenant_id=website_source_snapshots.tenant_id
            AND b.workspace_id=website_source_snapshots.workspace_id AND b.notebook_id=website_source_snapshots.notebook_id
            AND b.binding_kind='source' AND b.record_id=website_source_snapshots.source_id
            AND b.version_id=website_source_snapshots.source_version_id) AND
          NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=website_source_snapshots.tenant_id
            AND u.workspace_id=website_source_snapshots.workspace_id AND u.notebook_id=website_source_snapshots.notebook_id
            AND u.source_id=website_source_snapshots.source_id AND u.source_version_id=website_source_snapshots.source_version_id) AND
          NOT EXISTS(SELECT 1 FROM source_versions newer WHERE newer.tenant_id=website_source_snapshots.tenant_id
            AND newer.workspace_id=website_source_snapshots.workspace_id AND newer.source_id=website_source_snapshots.source_id
            AND newer.version>(SELECT version FROM source_versions v WHERE v.tenant_id=website_source_snapshots.tenant_id
              AND v.workspace_id=website_source_snapshots.workspace_id AND v.record_id=website_source_snapshots.source_version_id)) AND
          NOT EXISTS(SELECT 1 FROM deletion_requests d WHERE d.tenant_id=website_source_snapshots.tenant_id
            AND d.workspace_id=website_source_snapshots.workspace_id AND d.source_id=website_source_snapshots.source_id
            AND d.source_active=false));
        GRANT SELECT,INSERT,UPDATE ON website_import_requests TO daon_app;
        GRANT SELECT,INSERT ON website_source_snapshots TO daon_app;
        REVOKE UPDATE,DELETE ON website_source_snapshots FROM daon_app;
        REVOKE DELETE ON website_import_requests FROM daon_app;
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM website_import_requests) OR
      EXISTS(SELECT 1 FROM website_source_snapshots) THEN
      RAISE EXCEPTION 'WEBSITE_DOWNGRADE_REQUIRES_EMPTY_TABLES'; END IF; END $$;
      DROP TABLE website_import_requests,website_source_snapshots;
      DROP FUNCTION guard_website_snapshot_binding();
      DROP FUNCTION guard_website_request_transition();""")
