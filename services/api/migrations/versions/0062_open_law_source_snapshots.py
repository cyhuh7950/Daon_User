"""국가법령 요청 원장·불변 원문 snapshot. 기존 역할/RLS는 변경하지 않는다."""
from alembic import op

revision='0062'
down_revision='0061'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      ALTER TABLE workspace_connectors ADD COLUMN verification_json jsonb NOT NULL DEFAULT '{}';
      ALTER TABLE workspace_connectors ADD CONSTRAINT open_law_verification_shape CHECK(
        jsonb_typeof(verification_json)='object' AND
        (verification_json='{}' OR (verification_json-ARRAY['protocol_version','tool_schema_digest','checked_at']='{}'
          AND verification_json ?& ARRAY['protocol_version','tool_schema_digest','checked_at']
          AND verification_json->>'protocol_version'='2025-11-25'
          AND verification_json->>'tool_schema_digest' ~ '^[0-9a-f]{64}$'
          AND jsonb_typeof(verification_json->'checked_at')='string')));
      UPDATE workspace_connectors SET status='unavailable',error_code='CONNECTOR_NOT_VERIFIED'
        WHERE connector_id='mcp-open-law-go-kr' AND status='connected' AND verification_json='{}';
      ALTER TABLE workspace_connectors ADD CONSTRAINT open_law_connected_verified CHECK(
        connector_id<>'mcp-open-law-go-kr' OR status<>'connected' OR verification_json<>'{}');
      CREATE TABLE open_law_import_requests(
        tenant_id text NOT NULL,workspace_id text NOT NULL,actor_id text NOT NULL,notebook_id text NOT NULL,
        idempotency_key text NOT NULL,request_id text NOT NULL,fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
        mst text NOT NULL CHECK(mst ~ '^[0-9]{1,20}$'),effective_date text NOT NULL CHECK(effective_date ~ '^[0-9]{8}$'),
        article_code text CHECK(article_code ~ '^[0-9]{6}$'),
        expected_raw_sha256 text NOT NULL CHECK(expected_raw_sha256 ~ '^[0-9a-f]{64}$'),
        expected_text_sha256 text NOT NULL CHECK(expected_text_sha256 ~ '^[0-9a-f]{64}$'),
        state text NOT NULL CHECK(state IN ('fetching','storing','completed','failed')),
        attempt integer NOT NULL DEFAULT 1 CHECK(attempt>0),history jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(history)='array'),
        raw_object_id text NOT NULL,text_object_id text NOT NULL,snapshot_id text,source_id text,source_version_id text,
        policy_version text NOT NULL,policy_fingerprint text NOT NULL,safe_error_code text,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,workspace_id,request_id),UNIQUE(tenant_id,workspace_id,actor_id,notebook_id,idempotency_key),
        FOREIGN KEY(tenant_id,workspace_id,notebook_id) REFERENCES notebooks(tenant_id,workspace_id,notebook_id),
        CHECK((state='completed')=(snapshot_id IS NOT NULL AND source_id IS NOT NULL AND source_version_id IS NOT NULL))
      );
      CREATE TABLE open_law_source_snapshots(
        tenant_id text NOT NULL,workspace_id text NOT NULL,snapshot_id text NOT NULL,
        connector_id text NOT NULL CHECK(connector_id='mcp-open-law-go-kr'),source_id text NOT NULL,source_version_id text NOT NULL,
        notebook_id text NOT NULL,request_id text NOT NULL,raw_object_id text NOT NULL,text_object_id text NOT NULL,
        mst text NOT NULL CHECK(mst ~ '^[0-9]{1,20}$'),law_id text NOT NULL CHECK(law_id ~ '^[0-9]{1,20}$'),
        title text NOT NULL CHECK(length(title) BETWEEN 1 AND 300),effective_date text NOT NULL CHECK(effective_date ~ '^[0-9]{8}$'),
        promulgation_date text CHECK(promulgation_date ~ '^[0-9]{8}$'),ministry text CHECK(length(ministry)<=200),
        article_code text CHECK(article_code ~ '^[0-9]{6}$'),origin_uri text NOT NULL,
        fetched_at timestamptz NOT NULL,raw_sha256 text NOT NULL CHECK(raw_sha256 ~ '^[0-9a-f]{64}$'),
        text_sha256 text NOT NULL CHECK(text_sha256 ~ '^[0-9a-f]{64}$'),
        extraction_version text NOT NULL CHECK(extraction_version='open-law-text-v1'),license_status text NOT NULL CHECK(license_status='unknown'),
        policy_version text NOT NULL,policy_fingerprint text NOT NULL,created_by text NOT NULL,trace_id text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,workspace_id,snapshot_id),
        UNIQUE(tenant_id,workspace_id,source_version_id),
        FOREIGN KEY(tenant_id,workspace_id,request_id) REFERENCES open_law_import_requests(tenant_id,workspace_id,request_id),
        FOREIGN KEY(tenant_id,workspace_id,source_id) REFERENCES sources(tenant_id,workspace_id,record_id),
        FOREIGN KEY(tenant_id,workspace_id,source_version_id) REFERENCES source_versions(tenant_id,workspace_id,record_id),
        FOREIGN KEY(tenant_id,workspace_id,notebook_id) REFERENCES notebooks(tenant_id,workspace_id,notebook_id),
        FOREIGN KEY(tenant_id,workspace_id,raw_object_id) REFERENCES object_records(tenant_id,workspace_id,object_id),
        FOREIGN KEY(tenant_id,workspace_id,text_object_id) REFERENCES object_records(tenant_id,workspace_id,object_id),
        CHECK(origin_uri='https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq='||mst||'&efYd='||effective_date)
      );
      CREATE INDEX open_law_snapshot_identity ON open_law_source_snapshots(
        tenant_id,workspace_id,created_by,notebook_id,connector_id,mst,effective_date,coalesce(article_code,''),raw_sha256,text_sha256);
      ALTER TABLE open_law_import_requests ADD FOREIGN KEY(tenant_id,workspace_id,snapshot_id)
        REFERENCES open_law_source_snapshots(tenant_id,workspace_id,snapshot_id);
      ALTER TABLE open_law_import_requests ADD FOREIGN KEY(tenant_id,workspace_id,source_id)
        REFERENCES sources(tenant_id,workspace_id,record_id);
      ALTER TABLE open_law_import_requests ADD FOREIGN KEY(tenant_id,workspace_id,source_version_id)
        REFERENCES source_versions(tenant_id,workspace_id,record_id);
      CREATE FUNCTION guard_open_law_request() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
      BEGIN
        IF TG_OP='DELETE' OR OLD.state='completed'
          OR (to_jsonb(NEW)-ARRAY['request_id','state','attempt','history','raw_object_id','text_object_id','snapshot_id','source_id','source_version_id','safe_error_code','policy_version','policy_fingerprint','updated_at'])
             IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['request_id','state','attempt','history','raw_object_id','text_object_id','snapshot_id','source_id','source_version_id','safe_error_code','policy_version','policy_fingerprint','updated_at'])
          OR NOT ((OLD.state='failed' AND NEW.state='fetching' AND NEW.attempt=OLD.attempt+1
            AND NEW.request_id<>OLD.request_id AND jsonb_array_length(NEW.history)=jsonb_array_length(OLD.history)+1)
            OR (OLD.state IN ('fetching','storing') AND NEW.state IN ('storing','failed','completed')
              AND NEW.attempt=OLD.attempt AND NEW.history=OLD.history AND NEW.request_id=OLD.request_id
              AND NEW.raw_object_id=OLD.raw_object_id AND NEW.text_object_id=OLD.text_object_id
              AND NEW.policy_version=OLD.policy_version AND NEW.policy_fingerprint=OLD.policy_fingerprint))
        THEN RAISE EXCEPTION 'OPEN_LAW_REQUEST_IMMUTABLE' USING ERRCODE='55000'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER open_law_request_immutable BEFORE UPDATE OR DELETE ON open_law_import_requests
        FOR EACH ROW EXECUTE FUNCTION guard_open_law_request();
      CREATE TRIGGER open_law_snapshot_immutable BEFORE UPDATE OR DELETE ON open_law_source_snapshots
        FOR EACH ROW EXECUTE FUNCTION reject_canon_immutable_mutation();
      CREATE FUNCTION guard_open_law_snapshot() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended(jsonb_build_array(
          NEW.tenant_id,NEW.workspace_id,NEW.created_by,NEW.notebook_id,NEW.connector_id,
          NEW.mst,NEW.effective_date,coalesce(NEW.article_code,''),NEW.raw_sha256,NEW.text_sha256)::text,0));
        IF EXISTS(SELECT 1 FROM public.open_law_source_snapshots w
          WHERE w.tenant_id=NEW.tenant_id AND w.workspace_id=NEW.workspace_id AND w.created_by=NEW.created_by
          AND w.notebook_id=NEW.notebook_id AND w.connector_id=NEW.connector_id AND w.mst=NEW.mst
          AND w.effective_date=NEW.effective_date AND w.article_code IS NOT DISTINCT FROM NEW.article_code
          AND w.raw_sha256=NEW.raw_sha256 AND w.text_sha256=NEW.text_sha256
          AND EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=w.tenant_id
            AND b.workspace_id=w.workspace_id AND b.notebook_id=w.notebook_id AND b.binding_kind='source'
            AND b.record_id=w.source_id AND b.version_id=w.source_version_id)
          AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=w.tenant_id
            AND u.workspace_id=w.workspace_id AND u.notebook_id=w.notebook_id
            AND u.source_id=w.source_id AND u.source_version_id=w.source_version_id))
        THEN RAISE EXCEPTION 'OPEN_LAW_CURRENT_IDENTITY_DUPLICATE' USING ERRCODE='23505'; END IF;
        IF NOT EXISTS(SELECT 1 FROM public.open_law_import_requests r WHERE r.tenant_id=NEW.tenant_id
          AND r.workspace_id=NEW.workspace_id AND r.request_id=NEW.request_id AND r.actor_id=NEW.created_by
          AND r.notebook_id=NEW.notebook_id AND r.raw_object_id=NEW.raw_object_id AND r.text_object_id=NEW.text_object_id
          AND r.mst=NEW.mst AND r.effective_date=NEW.effective_date AND r.article_code IS NOT DISTINCT FROM NEW.article_code
          AND r.expected_raw_sha256=NEW.raw_sha256 AND r.expected_text_sha256=NEW.text_sha256
          AND r.policy_fingerprint=NEW.policy_fingerprint AND r.policy_version=NEW.policy_version AND r.state IN ('fetching','storing'))
          OR NOT EXISTS(SELECT 1 FROM public.source_versions v JOIN public.sources s ON s.tenant_id=v.tenant_id
            AND s.workspace_id=v.workspace_id AND s.record_id=v.source_id WHERE v.tenant_id=NEW.tenant_id
            AND v.workspace_id=NEW.workspace_id AND v.record_id=NEW.source_version_id AND v.source_id=NEW.source_id
            AND v.object_id=NEW.text_object_id AND s.state='ready' AND v.canonical_json->>'origin_kind'='mcp_open_law'
            AND v.canonical_json->>'connector_id'=NEW.connector_id AND v.canonical_json->>'snapshot_id'=NEW.snapshot_id
            AND v.canonical_json->>'raw_sha256'=NEW.raw_sha256 AND v.canonical_json->>'text_sha256'=NEW.text_sha256)
          OR NOT EXISTS(SELECT 1 FROM public.object_records o WHERE o.tenant_id=NEW.tenant_id AND o.workspace_id=NEW.workspace_id
            AND o.object_id=NEW.raw_object_id AND o.status='completed' AND o.digest_sha256=NEW.raw_sha256
            AND o.created_by=NEW.created_by AND o.byte_size BETWEEN 1 AND 2097152)
          OR NOT EXISTS(SELECT 1 FROM public.object_records o WHERE o.tenant_id=NEW.tenant_id AND o.workspace_id=NEW.workspace_id
            AND o.object_id=NEW.text_object_id AND o.status='completed' AND o.digest_sha256=NEW.text_sha256
            AND o.created_by=NEW.created_by AND o.byte_size BETWEEN 1 AND 524288)
          OR NOT EXISTS(SELECT 1 FROM public.index_versions i WHERE i.tenant_id=NEW.tenant_id
            AND i.workspace_id=NEW.workspace_id AND i.source_version_id=NEW.source_version_id)
          OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=NEW.tenant_id AND b.workspace_id=NEW.workspace_id
            AND b.notebook_id=NEW.notebook_id AND b.binding_kind='source' AND b.record_id=NEW.source_id AND b.version_id=NEW.source_version_id)
        THEN RAISE EXCEPTION 'OPEN_LAW_BINDING_INVALID' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER open_law_snapshot_binding BEFORE INSERT ON open_law_source_snapshots
        FOR EACH ROW EXECUTE FUNCTION guard_open_law_snapshot();
      REVOKE ALL ON FUNCTION guard_open_law_snapshot(),guard_open_law_request() FROM PUBLIC;
      ALTER TABLE open_law_import_requests ENABLE ROW LEVEL SECURITY;
      ALTER TABLE open_law_import_requests FORCE ROW LEVEL SECURITY;
      CREATE POLICY open_law_request_scope ON open_law_import_requests USING(
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
        AND actor_id=nullif(current_setting('app.actor_id',true),'') AND notebook_id=nullif(current_setting('app.notebook_id',true),''));
      ALTER TABLE open_law_source_snapshots ENABLE ROW LEVEL SECURITY;
      ALTER TABLE open_law_source_snapshots FORCE ROW LEVEL SECURITY;
      CREATE POLICY open_law_snapshot_scope ON open_law_source_snapshots USING(
        tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
        AND created_by=nullif(current_setting('app.actor_id',true),'') AND notebook_id=nullif(current_setting('app.notebook_id',true),'')
        AND EXISTS(SELECT 1 FROM notebook_bindings b WHERE b.tenant_id=open_law_source_snapshots.tenant_id
          AND b.workspace_id=open_law_source_snapshots.workspace_id AND b.notebook_id=open_law_source_snapshots.notebook_id
          AND b.binding_kind='source' AND b.record_id=open_law_source_snapshots.source_id AND b.version_id=open_law_source_snapshots.source_version_id)
        AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=open_law_source_snapshots.tenant_id
          AND u.workspace_id=open_law_source_snapshots.workspace_id AND u.notebook_id=open_law_source_snapshots.notebook_id
          AND u.source_id=open_law_source_snapshots.source_id AND u.source_version_id=open_law_source_snapshots.source_version_id));
      GRANT SELECT,INSERT,UPDATE ON open_law_import_requests TO daon_app;
      GRANT SELECT,INSERT ON open_law_source_snapshots TO daon_app;
      REVOKE DELETE ON open_law_import_requests FROM daon_app;
      REVOKE UPDATE,DELETE ON open_law_source_snapshots FROM daon_app;
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM open_law_import_requests)
      OR EXISTS(SELECT 1 FROM open_law_source_snapshots) THEN RAISE EXCEPTION 'OPEN_LAW_DOWNGRADE_REQUIRES_EMPTY_TABLES'; END IF; END $$;
      DROP TABLE open_law_import_requests,open_law_source_snapshots;
      DROP FUNCTION guard_open_law_snapshot();DROP FUNCTION guard_open_law_request();
      ALTER TABLE workspace_connectors DROP CONSTRAINT open_law_connected_verified;
      ALTER TABLE workspace_connectors DROP CONSTRAINT open_law_verification_shape;
      ALTER TABLE workspace_connectors DROP COLUMN verification_json;""")
