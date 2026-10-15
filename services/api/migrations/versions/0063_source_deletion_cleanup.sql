-- Administrator/socket-only, transaction-owned 0062 -> 0063 migration.
-- Alembic stamps normally; a psql -1 caller must stamp exactly one 0062 row
-- in THIS transaction. No application role membership/DELETE is introduced.
DO $preconditions$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
     OR (SELECT version_num FROM public.alembic_version) <> '0062'
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
         AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
     OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER') THEN
    RAISE EXCEPTION 'SOURCE_CLEANUP_MIGRATION_PRECONDITION' USING ERRCODE='55000';
  END IF;
END $preconditions$;

-- The existing request UPDATE guard resolves retention tables unqualified.
-- Give only that guard its own path when called below strict-path functions;
-- its Hold/version/cleanup/state checks and execution privileges stay intact.
ALTER FUNCTION public.guard_retention_request_update()
  SET search_path=pg_catalog,public,pg_temp;

CREATE TABLE public.source_cleanup_requests (
  tenant_id text NOT NULL, workspace_id text NOT NULL, request_id text NOT NULL,
  actor_id text NOT NULL, notebook_id text NOT NULL, source_id text NOT NULL,
  policy_version text NOT NULL, idempotency_key text NOT NULL,
  request_fingerprint text NOT NULL CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),
  source_version_ids text[] NOT NULL, object_ids text[] NOT NULL,
  delete_rows jsonb NOT NULL CHECK(jsonb_typeof(delete_rows)='object'),
  database_removed boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,workspace_id,request_id),
  UNIQUE(tenant_id,workspace_id,source_id),
  UNIQUE(tenant_id,workspace_id,actor_id,idempotency_key),
  FOREIGN KEY(tenant_id,workspace_id,request_id) REFERENCES public.deletion_requests(tenant_id,workspace_id,request_id)
);
CREATE TABLE public.source_cleanup_objects (
  tenant_id text NOT NULL, workspace_id text NOT NULL, request_id text NOT NULL,
  actor_id text NOT NULL, notebook_id text NOT NULL,
  object_id text NOT NULL, area text NOT NULL, object_key text NOT NULL,
  key_kind text NOT NULL CHECK(key_kind IN ('staging','final')),
  state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','completed','failed')),
  attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
  safe_error_code text CHECK(safe_error_code IN ('OBJECT_STORAGE_UNAVAILABLE','OBJECT_STORAGE_ACCESS_DENIED','OBJECT_KEY_INVALID')),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,workspace_id,request_id,object_key),
  FOREIGN KEY(tenant_id,workspace_id,request_id) REFERENCES public.source_cleanup_requests(tenant_id,workspace_id,request_id)
);
ALTER TABLE public.source_cleanup_requests OWNER TO daon_notebook_delete;
ALTER TABLE public.source_cleanup_objects OWNER TO daon_notebook_delete;
ALTER TABLE public.source_cleanup_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.source_cleanup_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE public.source_cleanup_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.source_cleanup_objects FORCE ROW LEVEL SECURITY;
CREATE POLICY source_cleanup_request_scope ON public.source_cleanup_requests USING (
  tenant_id=nullif(current_setting('app.tenant_id',true),'')
  AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
  AND (actor_id=nullif(current_setting('app.actor_id',true),'') OR current_user='daon_notebook_delete')
);
CREATE POLICY source_cleanup_object_scope ON public.source_cleanup_objects USING (
  tenant_id=nullif(current_setting('app.tenant_id',true),'')
  AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
  AND (actor_id=nullif(current_setting('app.actor_id',true),'') OR current_user='daon_notebook_delete')
  AND EXISTS(SELECT 1 FROM public.source_cleanup_requests r WHERE r.tenant_id=source_cleanup_objects.tenant_id
    AND r.workspace_id=source_cleanup_objects.workspace_id AND r.request_id=source_cleanup_objects.request_id
    AND r.notebook_id=source_cleanup_objects.notebook_id AND r.actor_id=source_cleanup_objects.actor_id)
);
REVOKE ALL ON public.source_cleanup_requests,public.source_cleanup_objects FROM PUBLIC,daon_app;
GRANT SELECT ON public.source_cleanup_requests,public.source_cleanup_objects TO daon_app;

-- Guard admission, not the Notebook DELETE trigger (the existing deletion
-- function disables user DELETE triggers). Purged opaque history may survive.
CREATE FUNCTION public.guard_notebook_source_cleanup() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF NEW.state NOT IN ('accepted','deleting') THEN RETURN NEW; END IF;
  IF NEW.tenant_id IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
    OR NEW.workspace_id IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'') THEN
    RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'notebook-delete|'||NEW.tenant_id||'|'||NEW.workspace_id||'|'||NEW.notebook_id,0));
  IF EXISTS(SELECT 1 FROM public.source_cleanup_requests c
    JOIN public.deletion_requests d ON d.tenant_id=c.tenant_id AND d.workspace_id=c.workspace_id AND d.request_id=c.request_id
    WHERE c.tenant_id=NEW.tenant_id AND c.workspace_id=NEW.workspace_id AND c.notebook_id=NEW.notebook_id
      AND (NOT c.database_removed OR d.state<>'purged' OR EXISTS(
        SELECT 1 FROM public.source_cleanup_objects o WHERE o.tenant_id=c.tenant_id
          AND o.workspace_id=c.workspace_id AND o.request_id=c.request_id AND o.state<>'completed'))) THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETION_IN_PROGRESS' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END $$;
ALTER FUNCTION public.guard_notebook_source_cleanup() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_notebook_source_cleanup() FROM PUBLIC,daon_app;
CREATE TRIGGER notebook_source_cleanup_admission BEFORE INSERT OR UPDATE OF state ON public.notebook_deletion_requests
  FOR EACH ROW EXECUTE FUNCTION public.guard_notebook_source_cleanup();

-- Original immutable/state UPDATE triggers remain the original functions.
-- DELETE is allowed only for exact rows of an already authenticated request,
-- executing inside the existing non-login owner, never by an app-set GUC alone.
CREATE FUNCTION public.guard_source_cleanup_delete() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE v jsonb:=to_jsonb(OLD); k text;
BEGIN
  k:=coalesce(v->>'record_id',v->>'object_id',v->>'job_id',v->>'event_id',v->>'snapshot_id',v->>'source_id');
  IF TG_TABLE_NAME IN ('website_import_requests','open_law_import_requests') THEN k:=v->>'request_id'; END IF;
  IF TG_TABLE_NAME='notebook_bindings' THEN k:=v->>'record_id'; END IF;
  IF current_user='daon_notebook_delete' AND EXISTS(
      SELECT 1 FROM public.source_cleanup_requests r
      WHERE r.tenant_id=v->>'tenant_id' AND r.workspace_id=v->>'workspace_id'
        AND r.request_id=nullif(current_setting('app.source_cleanup_request',true),'')
        AND NOT r.database_removed
        AND r.delete_rows->TG_TABLE_NAME @> jsonb_build_array(k)
        AND (TG_TABLE_NAME<>'notebook_bindings' OR (v->>'notebook_id'=r.notebook_id AND v->>'binding_kind'='source'))
  ) THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'SOURCE_CLEANUP_DELETE_DENIED' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.guard_source_cleanup_delete() FROM PUBLIC;

DO $delete_guards$
DECLARE t text; g record; definition text;
BEGIN
  FOREACH t IN ARRAY ARRAY['sources','source_versions','processing_runs','understanding_results',
      'extraction_evidence','transcription_runs','transcript_versions','transcript_segments',
      'evidence_spans','index_versions','document_processing_jobs','document_processing_job_attempts',
      'notebook_bindings','object_records','object_outbox_events','durable_jobs','job_attempts',
      'website_import_requests','website_source_snapshots','open_law_import_requests','open_law_source_snapshots',
      'source_retention_locator'] LOOP
    FOR g IN SELECT tgname,pg_get_triggerdef(oid) AS definition FROM pg_catalog.pg_trigger
        WHERE tgrelid=format('public.%I',t)::regclass AND NOT tgisinternal AND tgtype=27 LOOP
      definition:=regexp_replace(g.definition,'BEFORE (DELETE OR UPDATE|UPDATE OR DELETE)','BEFORE UPDATE');
      IF definition=g.definition THEN RAISE EXCEPTION 'SOURCE_CLEANUP_TRIGGER_SHAPE' USING ERRCODE='55000'; END IF;
      EXECUTE format('DROP TRIGGER %I ON public.%I',g.tgname,t);
      EXECUTE definition;
    END LOOP;
    EXECUTE format('CREATE TRIGGER s16_scoped_delete BEFORE DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.guard_source_cleanup_delete()',t);
  END LOOP;
END $delete_guards$;
GRANT DELETE ON public.source_retention_locator TO daon_notebook_delete;

-- Only the four real cyclic edges become deferrable, still validated at
-- commit. No nullable FK, RI disable, broad CASCADE or general guard change.
DO $snapshot_cycles$
DECLARE edge record; n integer:=0;
BEGIN
  FOR edge IN SELECT conname,conrelid FROM pg_catalog.pg_constraint WHERE contype='f' AND (
    (conrelid='public.website_import_requests'::regclass AND confrelid='public.website_source_snapshots'::regclass)
    OR (conrelid='public.website_source_snapshots'::regclass AND confrelid='public.website_import_requests'::regclass)
    OR (conrelid='public.open_law_import_requests'::regclass AND confrelid='public.open_law_source_snapshots'::regclass)
    OR (conrelid='public.open_law_source_snapshots'::regclass AND confrelid='public.open_law_import_requests'::regclass)
  ) LOOP
    EXECUTE format('ALTER TABLE %s ALTER CONSTRAINT %I DEFERRABLE INITIALLY IMMEDIATE',edge.conrelid::regclass,edge.conname);
    n:=n+1;
  END LOOP;
  IF n<>4 THEN RAISE EXCEPTION 'SOURCE_CLEANUP_SNAPSHOT_FK_DRIFT' USING ERRCODE='55000'; END IF;
END $snapshot_cycles$;
GRANT DELETE ON public.website_import_requests,public.website_source_snapshots,public.open_law_import_requests,public.open_law_source_snapshots TO daon_notebook_delete;
DO $snapshot_read_scope$
DECLARE tab text;
BEGIN
  FOREACH tab IN ARRAY ARRAY['website_source_snapshots','open_law_source_snapshots'] LOOP
    EXECUTE format($policy$CREATE POLICY s16_internal_source_cleanup_read ON public.%I FOR SELECT USING(
      current_user='daon_notebook_delete' AND tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND source_id=nullif(current_setting('app.source_cleanup_source',true),'')
      AND notebook_id=nullif(current_setting('app.notebook_id',true),'')
      AND created_by=nullif(current_setting('app.actor_id',true),''))$policy$,tab);
    EXECUTE format($policy$CREATE POLICY s16_internal_source_cleanup_delete ON public.%I FOR DELETE USING(
      current_user='daon_notebook_delete' AND tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND source_id=nullif(current_setting('app.source_cleanup_source',true),'')
      AND notebook_id=nullif(current_setting('app.notebook_id',true),'')
      AND created_by=nullif(current_setting('app.actor_id',true),''))$policy$,tab);
  END LOOP;
END $snapshot_read_scope$;

CREATE FUNCTION public.guard_source_cleanup_new_use() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s text; o text;
BEGIN
  IF TG_TABLE_NAME='notebook_bindings' THEN
    IF NEW.binding_kind<>'source' THEN RETURN NEW; END IF;
    s:=NEW.record_id;
  ELSIF TG_TABLE_NAME='source_versions' THEN s:=NEW.source_id; o:=NEW.object_id;
  ELSIF TG_TABLE_NAME='object_records' THEN o:=NEW.object_id;
  ELSE
    SELECT source_id INTO s FROM public.source_versions WHERE tenant_id=NEW.tenant_id
      AND workspace_id=NEW.workspace_id AND record_id=NEW.source_version_id;
    IF TG_TABLE_NAME='index_versions' THEN o:=NEW.object_id; END IF;
  END IF;
  IF s IS NOT NULL THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('retention-source|'||NEW.tenant_id||'|'||NEW.workspace_id||'|'||s,0));
    -- Ledger visibility is actor-scoped. deletion_requests is scope-wide and
    -- therefore also fences another actor's new binding to this same Source.
    IF EXISTS(SELECT 1 FROM public.deletion_requests r WHERE r.tenant_id=NEW.tenant_id
      AND r.workspace_id=NEW.workspace_id AND r.source_id=s AND NOT r.source_active AND r.state<>'cancelled') THEN
      RAISE EXCEPTION 'SOURCE_DELETION_ACTIVE' USING ERRCODE='55000';
    END IF;
  END IF;
  IF o IS NOT NULL THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('source-cleanup-object|'||NEW.tenant_id||'|'||NEW.workspace_id||'|'||o,0));
    -- The internal role reads pending identifiers within this workspace.
    -- No keys or private ledger rows are returned to the inserting actor.
    IF EXISTS(SELECT 1 FROM public.source_cleanup_objects c WHERE c.tenant_id=NEW.tenant_id
      AND c.workspace_id=NEW.workspace_id AND c.object_id=o AND c.state<>'completed') THEN
      RAISE EXCEPTION 'SOURCE_DELETION_ACTIVE' USING ERRCODE='55000';
    END IF;
  END IF;
  RETURN NEW;
END $$;
ALTER FUNCTION public.guard_source_cleanup_new_use() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_source_cleanup_new_use() FROM PUBLIC;
CREATE TRIGGER a_s16_source_binding_lock BEFORE INSERT OR UPDATE ON public.notebook_bindings
  FOR EACH ROW EXECUTE FUNCTION public.guard_source_cleanup_new_use();
CREATE TRIGGER a_s16_source_version_lock BEFORE INSERT ON public.source_versions
  FOR EACH ROW EXECUTE FUNCTION public.guard_source_cleanup_new_use();
CREATE TRIGGER a_s16_index_lock BEFORE INSERT ON public.index_versions
  FOR EACH ROW EXECUTE FUNCTION public.guard_source_cleanup_new_use();
CREATE TRIGGER a_s16_object_lock BEFORE INSERT ON public.object_records
  FOR EACH ROW EXECUTE FUNCTION public.guard_source_cleanup_new_use();

CREATE FUNCTION public.assert_source_cleanup_authorization(p_source text,p_notebook text,p_policy text,p_authorization_key text)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE t text:=nullif(current_setting('app.tenant_id',true),'');
        w text:=nullif(current_setting('app.workspace_id',true),'');
        a text:=nullif(current_setting('app.actor_id',true),'');
BEGIN
  -- The same lock as Notebook admission, always before the Source lock.
  PERFORM pg_advisory_xact_lock(hashtextextended('notebook-delete|'||t||'|'||w||'|'||p_notebook,0));
  IF t IS NULL OR w IS NULL OR a IS NULL OR NOT EXISTS(
    SELECT 1 FROM public.notebooks n WHERE n.tenant_id=t AND n.workspace_id=w
      AND n.notebook_id=p_notebook AND n.created_by=a
  ) OR EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=t AND d.workspace_id=w
      AND d.notebook_id=p_notebook AND d.state IN ('accepted','deleting','completed')) THEN
    RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.identity_users u WHERE u.user_id=a AND u.state='active')
    OR NOT EXISTS(SELECT 1 FROM public.identity_auth_workspaces x WHERE x.tenant_id=t AND x.workspace_id=w)
    OR NOT (EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles r WHERE r.tenant_id=t AND r.user_id=a
      AND r.state='active' AND r.role IN ('personal_owner','organization_admin'))
      OR EXISTS(SELECT 1 FROM public.identity_auth_memberships r WHERE r.tenant_id=t AND r.workspace_id=w
        AND r.user_id=a AND r.state='active' AND r.role IN ('workspace_admin','editor'))) THEN
    RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.identity_step_up_consumptions c
    JOIN public.identity_step_up_authorizations g ON g.step_up_id=c.step_up_id
    JOIN public.identity_sessions s ON s.session_id=g.session_id
    JOIN public.identity_devices d ON d.device_id=g.device_id
    WHERE c.tenant_id=t AND c.actor_id=a AND c.operation='retention.source.delete'
      AND c.idempotency_key=p_authorization_key AND g.tenant_id=t AND g.actor_id=a
      AND g.action_group='permanent_delete_or_restore_rollback' AND g.target_id=p_source
      AND g.policy_version=p_policy AND g.used_at IS NOT NULL
      AND s.tenant_id=t AND s.user_id=a AND s.state='active' AND s.access_expires_at>clock_timestamp()
      AND d.tenant_id=t AND d.user_id=a AND d.state='active') THEN
    RAISE EXCEPTION 'STEP_UP_REQUIRED' USING ERRCODE='42501';
  END IF;
END $$;
REVOKE ALL ON FUNCTION public.assert_source_cleanup_authorization(text,text,text,text) FROM PUBLIC,daon_app;
GRANT EXECUTE ON FUNCTION public.assert_source_cleanup_authorization(text,text,text,text) TO daon_notebook_delete;

CREATE FUNCTION public.stage_source_cleanup(p_request text,p_source text,p_notebook text,p_policy text,
  p_key text,p_fingerprint text,p_trace text,p_authorization_key text,p_inventory jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t text:=nullif(current_setting('app.tenant_id',true),''); w text:=nullif(current_setting('app.workspace_id',true),'');
  a text:=nullif(current_setting('app.actor_id',true),''); r public.source_cleanup_requests%ROWTYPE;
  versions text[]; objects text[]; removable text[]; rows jsonb:='{}'; ids jsonb; item jsonb; q record;
BEGIN
  PERFORM public.assert_source_cleanup_authorization(p_source,p_notebook,p_policy,p_authorization_key);
  PERFORM pg_advisory_xact_lock(hashtextextended('retention-source|'||t||'|'||w||'|'||p_source,0));
  SELECT * INTO r FROM public.source_cleanup_requests WHERE tenant_id=t AND workspace_id=w AND source_id=p_source FOR UPDATE;
  IF FOUND THEN
    IF r.actor_id<>a OR r.notebook_id<>p_notebook OR r.idempotency_key<>p_key OR r.request_fingerprint<>p_fingerprint THEN
      RAISE EXCEPTION 'RETENTION_IDEMPOTENCY_CONFLICT' USING ERRCODE='23514';
    END IF;
    RETURN r.request_id;
  END IF;
  PERFORM 1 FROM public.sources WHERE tenant_id=t AND workspace_id=w AND record_id=p_source FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'SOURCE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
  PERFORM 1 FROM public.source_versions WHERE tenant_id=t AND workspace_id=w AND source_id=p_source ORDER BY record_id FOR UPDATE;
  SELECT array_agg(record_id ORDER BY record_id),array_agg(DISTINCT object_id) FILTER(WHERE object_id IS NOT NULL)
    INTO versions,objects FROM public.source_versions WHERE tenant_id=t AND workspace_id=w AND source_id=p_source;
  IF versions IS NULL OR cardinality(versions)>1024 THEN RAISE EXCEPTION 'DELETION_INVENTORY_INVALID' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM public.identity_auth_source_access x WHERE x.tenant_id=t AND x.workspace_id=w
    AND x.user_id=a AND x.source_version_id=ANY(versions) AND x.allowed=0) THEN
    RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=t AND b.workspace_id=w AND b.notebook_id=p_notebook
    AND b.binding_kind='source' AND b.record_id=p_source AND b.version_id=ANY(versions)
    AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=t AND u.workspace_id=w
      AND u.notebook_id=b.notebook_id AND u.source_id=p_source AND u.source_version_id=b.version_id)) THEN
    RAISE EXCEPTION 'SOURCE_UNAVAILABLE' USING ERRCODE='P0002';
  END IF;
  IF EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=t AND b.workspace_id=w AND b.notebook_id<>p_notebook
    AND b.binding_kind='source' AND b.record_id=p_source
    AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=t AND u.workspace_id=w
      AND u.notebook_id=b.notebook_id AND u.source_id=p_source AND u.source_version_id=b.version_id)) THEN
    RAISE EXCEPTION 'SOURCE_SHARED_BINDING' USING ERRCODE='23514';
  END IF;
  PERFORM set_config('app.notebook_id',p_notebook,true);
  PERFORM set_config('app.source_cleanup_source',p_source,true);
  IF EXISTS(SELECT 1 FROM public.legal_holds WHERE tenant_id=t AND workspace_id=w AND source_id=p_source AND state='active') THEN
    RAISE EXCEPTION 'LEGAL_HOLD_ACTIVE' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.document_processing_jobs WHERE tenant_id=t AND workspace_id=w AND source_id=p_source
      AND state IN ('pending','leased','retry_wait'))
    OR EXISTS(SELECT 1 FROM public.processing_runs WHERE tenant_id=t AND workspace_id=w AND source_version_id=ANY(versions)
      AND state NOT IN ('completed','failed')) THEN
    RAISE EXCEPTION 'SOURCE_PROCESSING_ACTIVE' USING ERRCODE='23514';
  END IF;
  -- Preserve immutable consumer history. Deleting references would make old
  -- consumer denial predicates vacuously true. Never skip/alter those rows.
  IF EXISTS(SELECT 1 FROM public.citations WHERE tenant_id=t AND workspace_id=w AND source_version_id=ANY(versions))
    OR EXISTS(SELECT 1 FROM public.evidence_references WHERE tenant_id=t AND workspace_id=w AND source_version_id=ANY(versions))
    OR EXISTS(SELECT 1 FROM public.knowledge_registrations WHERE tenant_id=t AND workspace_id=w AND registered_source_version_id=ANY(versions))
    OR EXISTS(SELECT 1 FROM public.generation_requests WHERE tenant_id=t AND workspace_id=w AND canonical_json->'source_version_ids' ?| versions)
    OR EXISTS(SELECT 1 FROM public.run_snapshots WHERE tenant_id=t AND workspace_id=w AND canonical_json->'source_version_ids' ?| versions)
    OR EXISTS(SELECT 1 FROM public.external_references WHERE tenant_id=t AND workspace_id=w AND source_id=p_source)
    OR EXISTS(SELECT 1 FROM public.sync_preview_items WHERE tenant_id=t AND workspace_id=w AND source_version_id=ANY(versions))
    OR EXISTS(SELECT 1 FROM public.sync_manifest_items WHERE tenant_id=t AND workspace_id=w AND source_version_id=ANY(versions)) THEN
    RAISE EXCEPTION 'SOURCE_RETAINED_REFERENCE' USING ERRCODE='23514';
  END IF;
  SELECT array_agg(DISTINCT object_id) INTO objects FROM (
    SELECT unnest(coalesce(objects,ARRAY[]::text[])) object_id
    UNION SELECT object_id FROM public.index_versions WHERE tenant_id=t AND workspace_id=w AND source_version_id=ANY(versions)
    UNION SELECT raw_object_id FROM public.website_source_snapshots WHERE tenant_id=t AND workspace_id=w AND source_id=p_source
    UNION SELECT text_object_id FROM public.website_source_snapshots WHERE tenant_id=t AND workspace_id=w AND source_id=p_source
    UNION SELECT raw_object_id FROM public.open_law_source_snapshots WHERE tenant_id=t AND workspace_id=w AND source_id=p_source
    UNION SELECT text_object_id FROM public.open_law_source_snapshots WHERE tenant_id=t AND workspace_id=w AND source_id=p_source
    UNION SELECT raw_object_id FROM public.website_import_requests WHERE tenant_id=t AND workspace_id=w AND snapshot_id IN
      (SELECT snapshot_id FROM public.website_source_snapshots WHERE tenant_id=t AND workspace_id=w AND source_id=p_source)
    UNION SELECT text_object_id FROM public.website_import_requests WHERE tenant_id=t AND workspace_id=w AND snapshot_id IN
      (SELECT snapshot_id FROM public.website_source_snapshots WHERE tenant_id=t AND workspace_id=w AND source_id=p_source)
    UNION SELECT raw_object_id FROM public.open_law_import_requests WHERE tenant_id=t AND workspace_id=w AND source_id=p_source
    UNION SELECT text_object_id FROM public.open_law_import_requests WHERE tenant_id=t AND workspace_id=w AND source_id=p_source
  ) owned_objects WHERE object_id IS NOT NULL;
  PERFORM pg_advisory_xact_lock(hashtextextended('source-cleanup-object|'||t||'|'||w||'|'||id,0))
    FROM unnest(coalesce(objects,ARRAY[]::text[])) id ORDER BY id;
  IF EXISTS(SELECT 1 FROM public.sync_target_versions WHERE tenant_id=t AND workspace_id=w AND object_id=ANY(objects)) THEN
    RAISE EXCEPTION 'SOURCE_RETAINED_REFERENCE' USING ERRCODE='23514';
  END IF;
  SELECT array_agg(o.object_id) INTO removable FROM public.object_records o WHERE o.tenant_id=t AND o.workspace_id=w
    AND o.object_id=ANY(objects) AND NOT EXISTS(SELECT 1 FROM public.source_versions v WHERE v.tenant_id=t AND v.workspace_id=w
      AND v.object_id=o.object_id AND v.source_id<>p_source)
    AND NOT EXISTS(SELECT 1 FROM public.index_versions i WHERE i.tenant_id=t AND i.workspace_id=w
      AND i.object_id=o.object_id AND NOT (i.source_version_id=ANY(versions)))
    AND NOT EXISTS(SELECT 1 FROM public.website_source_snapshots x WHERE x.tenant_id=t AND x.workspace_id=w
      AND (x.raw_object_id=o.object_id OR x.text_object_id=o.object_id) AND x.source_id<>p_source)
    AND NOT EXISTS(SELECT 1 FROM public.open_law_source_snapshots x WHERE x.tenant_id=t AND x.workspace_id=w
      AND (x.raw_object_id=o.object_id OR x.text_object_id=o.object_id) AND x.source_id<>p_source);
  removable:=coalesce(removable,ARRAY[]::text[]);
  IF cardinality(removable)>1024 THEN RAISE EXCEPTION 'DELETION_INVENTORY_INVALID' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM public.durable_jobs j JOIN public.object_outbox_events e ON e.tenant_id=j.tenant_id
    AND e.workspace_id=j.workspace_id AND e.event_id=j.event_id WHERE j.tenant_id=t AND j.workspace_id=w
    AND e.object_id=ANY(removable) AND j.state NOT IN ('completed','dead_letter')) THEN
    RAISE EXCEPTION 'SOURCE_PROCESSING_ACTIVE' USING ERRCODE='23514';
  END IF;
  IF p_inventory IS NULL OR jsonb_typeof(p_inventory)<>'array' OR jsonb_array_length(p_inventory)<>6
    OR (SELECT count(DISTINCT x->>'kind') FROM jsonb_array_elements(p_inventory) x)<>6
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_inventory) x WHERE x->>'disposition'='verification_pending') THEN
    RAISE EXCEPTION 'DELETION_INVENTORY_INVALID' USING ERRCODE='23514';
  END IF;
  FOR q IN SELECT * FROM (VALUES
    ('document_processing_job_attempts','job_id','job_id IN (SELECT job_id FROM public.document_processing_jobs WHERE tenant_id=$1 AND workspace_id=$2 AND source_id=$3)'),
    ('document_processing_jobs','job_id','source_id=$3'),
    ('extraction_evidence','record_id','source_version_id=ANY($4)'),
    ('evidence_spans','record_id','source_version_id=ANY($4)'),
    ('transcript_segments','record_id','transcript_version_id IN (SELECT record_id FROM public.transcript_versions WHERE tenant_id=$1 AND workspace_id=$2 AND source_version_id=ANY($4))'),
    ('transcript_versions','record_id','source_version_id=ANY($4)'),
    ('transcription_runs','record_id','source_version_id=ANY($4)'),
    ('understanding_results','record_id','source_version_id=ANY($4)'),
    ('processing_runs','record_id','source_version_id=ANY($4)'),
    ('index_versions','record_id','source_version_id=ANY($4)'),
    ('source_versions','record_id','source_id=$3'),('sources','record_id','record_id=$3'),
    ('notebook_bindings','record_id','binding_kind=''source'' AND record_id=$3 AND notebook_id=$6'),
    ('job_attempts','job_id','job_id IN (SELECT j.job_id FROM public.durable_jobs j JOIN public.object_outbox_events e ON e.tenant_id=j.tenant_id AND e.workspace_id=j.workspace_id AND e.event_id=j.event_id WHERE j.tenant_id=$1 AND j.workspace_id=$2 AND e.object_id=ANY($5))'),
    ('durable_jobs','job_id','event_id IN (SELECT event_id FROM public.object_outbox_events WHERE tenant_id=$1 AND workspace_id=$2 AND object_id=ANY($5))'),
    ('object_outbox_events','event_id','object_id=ANY($5)'),('object_records','object_id','object_id=ANY($5)'),
    ('website_source_snapshots','snapshot_id','source_id=$3'),('open_law_source_snapshots','snapshot_id','source_id=$3'),
    ('source_retention_locator','source_id','source_id=$3'),
    ('website_import_requests','request_id','snapshot_id IN (SELECT snapshot_id FROM public.website_source_snapshots WHERE tenant_id=$1 AND workspace_id=$2 AND source_id=$3)'),
    ('open_law_import_requests','request_id','source_id=$3')
  ) v(tab,key_col,predicate) LOOP
    EXECUTE format('SELECT coalesce(jsonb_agg(DISTINCT %I),''[]'') FROM public.%I WHERE tenant_id=$1 AND workspace_id=$2 AND (%s)',q.key_col,q.tab,q.predicate)
      INTO ids USING t,w,p_source,versions,removable,p_notebook;
    rows:=rows||jsonb_build_object(q.tab,ids);
  END LOOP;
  INSERT INTO public.deletion_request_locator(tenant_id,request_id,workspace_id,source_id,created_at) VALUES(t,p_request,w,p_source,now());
  INSERT INTO public.deletion_requests(tenant_id,workspace_id,request_id,source_id,actor_id,state,version,source_active,purge_started,
    grace_until,policy_version,idempotency_key,request_fingerprint,trace_id,created_at,updated_at)
    VALUES(t,w,p_request,p_source,a,'cleanup_pending',1,false,true,now(),p_policy,p_key,p_fingerprint,p_trace,now(),now());
  INSERT INTO public.source_cleanup_requests(tenant_id,workspace_id,request_id,actor_id,notebook_id,source_id,policy_version,
    idempotency_key,request_fingerprint,source_version_ids,object_ids,delete_rows)
    VALUES(t,w,p_request,a,p_notebook,p_source,p_policy,p_key,p_fingerprint,versions,objects,rows);
  INSERT INTO public.source_cleanup_objects(tenant_id,workspace_id,request_id,actor_id,notebook_id,object_id,area,object_key,key_kind)
    SELECT t,w,p_request,a,p_notebook,o.object_id,o.area,k.key,k.kind FROM public.object_records o
    CROSS JOIN LATERAL (VALUES(o.staging_key,'staging'),(o.object_key,'final')) k(key,kind)
    WHERE o.tenant_id=t AND o.workspace_id=w AND o.object_id=ANY(removable)
    ON CONFLICT DO NOTHING;
  FOR item IN SELECT * FROM jsonb_array_elements(p_inventory) LOOP
    INSERT INTO public.deletion_cleanup_items(tenant_id,workspace_id,request_id,reference_id,derivative_kind,state,
      acknowledgement_required,attempt_count,inventory_disposition,updated_at)
      VALUES(t,w,p_request,item->>'reference_id',item->>'kind',
        CASE WHEN item->>'kind'='original_content' AND EXISTS(SELECT 1 FROM public.source_cleanup_objects WHERE tenant_id=t AND workspace_id=w AND request_id=p_request)
          THEN 'pending' ELSE 'completed' END,(item->>'acknowledgement_required')::boolean,0,item->>'disposition',now());
  END LOOP;
  INSERT INTO public.retention_request_idempotency(tenant_id,workspace_id,actor_id,action,idempotency_key,request_fingerprint,request_id,result_version,created_at)
    VALUES(t,w,a,'create',p_key,p_fingerprint,p_request,1,now());
  PERFORM set_config('app.source_cleanup_request',p_request,true);
  FOR q IN SELECT conname FROM pg_catalog.pg_constraint WHERE contype='f' AND (
    (conrelid='public.website_import_requests'::regclass AND confrelid='public.website_source_snapshots'::regclass)
    OR (conrelid='public.website_source_snapshots'::regclass AND confrelid='public.website_import_requests'::regclass)
    OR (conrelid='public.open_law_import_requests'::regclass AND confrelid='public.open_law_source_snapshots'::regclass)
    OR (conrelid='public.open_law_source_snapshots'::regclass AND confrelid='public.open_law_import_requests'::regclass)
  ) LOOP EXECUTE format('SET CONSTRAINTS public.%I DEFERRED',q.conname); END LOOP;
  -- FK dependencies are removed in the same transaction as their durable keys.
  FOR q IN SELECT * FROM (VALUES
    (-4,'website_source_snapshots','snapshot_id'),(-3,'website_import_requests','request_id'),
    (-2,'open_law_source_snapshots','snapshot_id'),(-1,'open_law_import_requests','request_id'),
    (1,'document_processing_job_attempts','job_id'),(2,'document_processing_jobs','job_id'),
    (3,'extraction_evidence','record_id'),(4,'evidence_spans','record_id'),(5,'transcript_segments','record_id'),
    (6,'transcript_versions','record_id'),(7,'transcription_runs','record_id'),(8,'understanding_results','record_id'),
    (9,'processing_runs','record_id'),(10,'index_versions','record_id'),(11,'source_versions','record_id'),(12,'sources','record_id'),
    (13,'notebook_bindings','record_id'),(14,'job_attempts','job_id'),(15,'durable_jobs','job_id'),
    (16,'object_outbox_events','event_id'),(17,'object_records','object_id')
    ,(18,'source_retention_locator','source_id')
  ) v(ordinal,tab,key_col) ORDER BY ordinal LOOP
    EXECUTE format('DELETE FROM public.%I WHERE tenant_id=$1 AND workspace_id=$2 AND %I IN (SELECT jsonb_array_elements_text($3))%s',
      q.tab,q.key_col,CASE WHEN q.tab='notebook_bindings' THEN ' AND binding_kind=''source'' AND notebook_id=$4' ELSE '' END)
      USING t,w,rows->q.tab,p_notebook;
  END LOOP;
  UPDATE public.source_cleanup_requests SET database_removed=true WHERE tenant_id=t AND workspace_id=w AND request_id=p_request;
  PERFORM set_config('app.source_cleanup_request','',true);
  PERFORM set_config('app.source_cleanup_source','',true);
  RETURN p_request;
END $$;
ALTER FUNCTION public.stage_source_cleanup(text,text,text,text,text,text,text,text,jsonb) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.stage_source_cleanup(text,text,text,text,text,text,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.stage_source_cleanup(text,text,text,text,text,text,text,text,jsonb) TO daon_app;

CREATE FUNCTION public.lock_source_cleanup_request(p_request text,p_policy text,p_authorization_key text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t text:=nullif(current_setting('app.tenant_id',true),''); w text:=nullif(current_setting('app.workspace_id',true),'');
  a text:=nullif(current_setting('app.actor_id',true),''); r public.source_cleanup_requests%ROWTYPE;
BEGIN
  SELECT * INTO r FROM public.source_cleanup_requests WHERE tenant_id=t AND workspace_id=w AND request_id=p_request;
  IF NOT FOUND OR r.actor_id<>a THEN RAISE EXCEPTION 'DELETION_REQUEST_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
  PERFORM public.assert_source_cleanup_authorization(r.source_id,r.notebook_id,p_policy,p_authorization_key);
  PERFORM pg_advisory_xact_lock(hashtextextended('retention-source|'||t||'|'||w||'|'||r.source_id,0));
  IF EXISTS(SELECT 1 FROM public.legal_holds h WHERE h.tenant_id=t AND h.workspace_id=w AND h.source_id=r.source_id AND h.state='active') THEN
    RAISE EXCEPTION 'LEGAL_HOLD_ACTIVE' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.identity_auth_source_access x WHERE x.tenant_id=t AND x.workspace_id=w AND x.user_id=a
      AND x.source_version_id=ANY(r.source_version_ids) AND x.allowed=0) THEN
    RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  PERFORM 1 FROM public.source_cleanup_requests WHERE tenant_id=t AND workspace_id=w AND request_id=p_request FOR UPDATE;
END $$;
ALTER FUNCTION public.lock_source_cleanup_request(text,text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.lock_source_cleanup_request(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.lock_source_cleanup_request(text,text,text) TO daon_app;

CREATE FUNCTION public.progress_source_cleanup(p_request text,p_policy text,p_authorization_key text,p_object_key text,p_safe_error text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t text:=nullif(current_setting('app.tenant_id',true),''); w text:=nullif(current_setting('app.workspace_id',true),'');
  a text:=nullif(current_setting('app.actor_id',true),''); r public.source_cleanup_requests%ROWTYPE;
  d public.deletion_requests%ROWTYPE; n integer; original_ref text; all_done boolean;
BEGIN
  PERFORM public.lock_source_cleanup_request(p_request,p_policy,p_authorization_key);
  SELECT * INTO r FROM public.source_cleanup_requests WHERE tenant_id=t AND workspace_id=w AND request_id=p_request;
  IF NOT FOUND OR r.actor_id<>a THEN RAISE EXCEPTION 'DELETION_REQUEST_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
  SELECT * INTO STRICT d FROM public.deletion_requests WHERE tenant_id=t AND workspace_id=w AND request_id=p_request FOR UPDATE;
  IF d.state='purged' THEN RETURN; END IF;
  IF NOT r.database_removed THEN RAISE EXCEPTION 'DELETION_CLEANUP_PENDING' USING ERRCODE='55000'; END IF;
  IF d.state='failed' AND p_object_key IS NULL AND EXISTS(SELECT 1 FROM public.source_cleanup_objects
    WHERE tenant_id=t AND workspace_id=w AND request_id=p_request AND state<>'completed') THEN RETURN; END IF;
  PERFORM set_config('app.retention_transition','allowed',true);
  IF d.state='failed' THEN
    UPDATE public.deletion_requests SET state='cleanup_pending',version=version+1,updated_at=now()
      WHERE tenant_id=t AND workspace_id=w AND request_id=p_request;
  END IF;
  IF p_object_key IS NOT NULL THEN
    IF p_safe_error IS NOT NULL AND p_safe_error NOT IN ('OBJECT_STORAGE_UNAVAILABLE','OBJECT_STORAGE_ACCESS_DENIED','OBJECT_KEY_INVALID') THEN
      RAISE EXCEPTION 'DELETION_REQUEST_INVALID' USING ERRCODE='22023';
    END IF;
    UPDATE public.source_cleanup_objects SET state=CASE WHEN p_safe_error IS NULL THEN 'completed' ELSE 'failed' END,
      attempt_count=attempt_count+1,safe_error_code=p_safe_error,updated_at=now()
      WHERE tenant_id=t AND workspace_id=w AND request_id=p_request AND object_key=p_object_key AND state<>'completed';
    IF NOT FOUND THEN
      IF EXISTS(SELECT 1 FROM public.source_cleanup_objects WHERE tenant_id=t AND workspace_id=w AND request_id=p_request
        AND object_key=p_object_key AND state='completed') THEN RETURN; END IF;
      RAISE EXCEPTION 'DELETION_REQUEST_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT reference_id,attempt_count+1 INTO original_ref,n FROM public.deletion_cleanup_items
      WHERE tenant_id=t AND workspace_id=w AND request_id=p_request AND derivative_kind='original_content';
    UPDATE public.deletion_cleanup_items SET state=CASE WHEN p_safe_error IS NULL THEN 'pending' ELSE 'failed' END,
      attempt_count=n,updated_at=now() WHERE tenant_id=t AND workspace_id=w AND request_id=p_request AND reference_id=original_ref AND state<>'completed';
    INSERT INTO public.deletion_attempts(tenant_id,workspace_id,attempt_id,request_id,reference_id,attempt_number,outcome,trace_id,attempted_at)
      VALUES(t,w,'source-cleanup-'||md5(p_request||'|'||n::text),p_request,original_ref,n,
        CASE WHEN p_safe_error IS NULL THEN 'completed' ELSE 'failed' END,d.trace_id,now());
    IF p_safe_error IS NOT NULL THEN
      UPDATE public.deletion_requests SET state='failed',version=version+1,updated_at=now()
        WHERE tenant_id=t AND workspace_id=w AND request_id=p_request;
      RETURN;
    END IF;
  END IF;
  SELECT NOT EXISTS(SELECT 1 FROM public.source_cleanup_objects WHERE tenant_id=t AND workspace_id=w AND request_id=p_request
    AND state<>'completed') INTO all_done;
  IF NOT all_done THEN RETURN; END IF;
  UPDATE public.deletion_cleanup_items SET state='completed',updated_at=now()
    WHERE tenant_id=t AND workspace_id=w AND request_id=p_request AND state<>'completed';
  UPDATE public.deletion_requests SET state='purged',version=version+1,updated_at=now()
    WHERE tenant_id=t AND workspace_id=w AND request_id=p_request;
  INSERT INTO public.audit_events(event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,
    trace_id,policy_version,after_value,metadata)
    VALUES('source-purged-'||md5(p_request),t,w,a,'deletion.purged','source',r.source_id,'succeeded',d.trace_id,p_policy,
      jsonb_build_object('request_id',p_request,'state','purged'),jsonb_build_object('durable_cleanup',true));
END $$;
ALTER FUNCTION public.progress_source_cleanup(text,text,text,text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.progress_source_cleanup(text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.progress_source_cleanup(text,text,text,text,text) TO daon_app;

CREATE FUNCTION public.read_source_cleanup_scope(p_request text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE r public.source_cleanup_requests%ROWTYPE;
BEGIN
  SELECT * INTO r FROM public.source_cleanup_requests WHERE tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'') AND request_id=p_request;
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF r.actor_id IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') THEN
    RAISE EXCEPTION 'DELETION_REQUEST_UNAVAILABLE' USING ERRCODE='P0002';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.identity_users u WHERE u.user_id=r.actor_id AND u.state='active')
    OR NOT EXISTS(SELECT 1 FROM public.notebooks n WHERE n.tenant_id=r.tenant_id AND n.workspace_id=r.workspace_id
      AND n.notebook_id=r.notebook_id AND n.created_by=r.actor_id)
    OR EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=r.tenant_id AND d.workspace_id=r.workspace_id
      AND d.notebook_id=r.notebook_id AND d.state IN ('accepted','deleting','completed'))
    OR NOT EXISTS(SELECT 1 FROM public.identity_auth_workspaces x WHERE x.tenant_id=r.tenant_id AND x.workspace_id=r.workspace_id)
    OR NOT (EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles x WHERE x.tenant_id=r.tenant_id AND x.user_id=r.actor_id
        AND x.state='active' AND x.role IN ('personal_owner','organization_admin'))
      OR EXISTS(SELECT 1 FROM public.identity_auth_memberships x WHERE x.tenant_id=r.tenant_id AND x.workspace_id=r.workspace_id
        AND x.user_id=r.actor_id AND x.state='active' AND x.role IN ('workspace_admin','editor','reviewer','approver','viewer')))
    OR EXISTS(SELECT 1 FROM public.identity_auth_source_access x WHERE x.tenant_id=r.tenant_id AND x.workspace_id=r.workspace_id
      AND x.user_id=r.actor_id AND x.source_version_id=ANY(r.source_version_ids) AND x.allowed=0) THEN
    RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  RETURN jsonb_build_object('notebook_id',r.notebook_id,'source_version_ids',r.source_version_ids);
END $$;
ALTER FUNCTION public.read_source_cleanup_scope(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.read_source_cleanup_scope(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_source_cleanup_scope(text) TO daon_app;

-- The obsolete destructive function must not remain an app-callable bypass.
REVOKE EXECUTE ON FUNCTION public.delete_source_scope(text,text,text) FROM PUBLIC,daon_app;
