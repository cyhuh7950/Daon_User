-- M2-R2 미적용 초안. 실제 PG/RLS/경합 검증·적용 승인은 별도다.
-- 기존 Source/Version/Run/감사에 live FK·CASCADE·owner/GRANT 변경을 만들지 않는다.
-- 보존: 원장별 최초시각부터 Audit 1년. active Legal Hold가 있는 Source의 wait/receipt
-- 는 정리하지 않는다. 관측은 1년 경과·참조 receipt 없음일 때만 운영자 정리한다.
-- cleanup_document_readiness는 운영자 전용 수동 port이며 scheduler/정식 적용은 없다.
DO $precondition$
DECLARE name text;
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT count(*) FROM public.alembic_version)<>1
    OR (SELECT version_num FROM public.alembic_version)<>'0071'
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
      AND rolinherit AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
    OR NOT pg_catalog.pg_has_role('daon_notebook_delete','daon_app','MEMBER')
    OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER') THEN
    RAISE EXCEPTION 'DOCUMENT_READINESS_PRECONDITION' USING ERRCODE='55000';
  END IF;
  FOREACH name IN ARRAY ARRAY['sources','source_versions','processing_runs','document_processing_jobs',
    'notebooks','notebook_bindings','notebook_source_unbindings','notebook_deletion_requests',
    'deletion_requests','legal_holds','audit_events','identity_users','identity_auth_workspaces',
    'identity_auth_tenant_roles','identity_auth_memberships','identity_org_tenant_memberships',
    'identity_auth_source_access','workspace_model_defaults','system_provider_connections'] LOOP
    IF NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.'||name,'SELECT') THEN
      RAISE EXCEPTION 'DOCUMENT_READINESS_PARENT_PRIVILEGE_MISSING' USING ERRCODE='55000';
    END IF;
  END LOOP;
END $precondition$;

CREATE TABLE public.document_role_waits (
 tenant_id text NOT NULL CHECK(tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 workspace_id text NOT NULL CHECK(workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 actor_id text NOT NULL CHECK(actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 notebook_id text NOT NULL CHECK(notebook_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 source_id text NOT NULL CHECK(source_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 source_version_id text NOT NULL CHECK(source_version_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 processing_run_id text NOT NULL CHECK(processing_run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 capability text CHECK(capability IN ('text_generation','image_understanding','document_parsing','speech_to_text')),
 capability_slot text NOT NULL CHECK(capability_slot=coalesce(capability,'unknown')),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
 content_digest text NOT NULL CHECK(content_digest ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,workspace_id,processing_run_id,capability_slot),
 UNIQUE(tenant_id,workspace_id,processing_run_id,capability_slot,actor_id,notebook_id,source_id,source_version_id)
);
CREATE TABLE public.document_readiness_observations (
 event_id text PRIMARY KEY CHECK(event_id ~ '^rdy-[0-9a-f]{32}$'),
 connection_id text NOT NULL CHECK(connection_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 adapter_type text NOT NULL CHECK(adapter_type ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 capability text CHECK(capability IN ('text_generation','image_understanding','document_parsing','speech_to_text')),
 model_catalog_digest text NOT NULL CHECK(model_catalog_digest ~ '^[0-9a-f]{64}$'),
 configuration_version bigint NOT NULL CHECK(configuration_version>0),
 observed_at timestamptz NOT NULL,
 received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 probe_kind text NOT NULL CHECK(probe_kind IN ('provider_connection_check','native_asr_ready','model_catalog_refresh')),
 status text NOT NULL CHECK(status IN ('healthy','not_ready','unavailable')),
 observation_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 content_digest text NOT NULL CHECK(content_digest ~ '^[0-9a-f]{64}$'),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
 CHECK(probe_kind<>'provider_connection_check' OR capability IS NULL),
 CHECK(probe_kind<>'native_asr_ready' OR capability IS NOT DISTINCT FROM 'speech_to_text'),
 CHECK(observed_at<=received_at+'00:00:05'::interval)
);
CREATE INDEX document_readiness_latest ON public.document_readiness_observations(connection_id,observation_sequence DESC);
CREATE TABLE public.document_reprocess_receipts (
 tenant_id text NOT NULL, workspace_id text NOT NULL, actor_id text NOT NULL,
 notebook_id text NOT NULL, source_id text NOT NULL, source_version_id text NOT NULL,
 processing_run_id text NOT NULL, capability_slot text NOT NULL,
 event_id text NOT NULL REFERENCES public.document_readiness_observations(event_id),
 frozen_context_digest text NOT NULL CHECK(frozen_context_digest ~ '^[0-9a-f]{64}$'),
 phase text NOT NULL CHECK(phase IN ('deferred','final')),
 outcome text NOT NULL CHECK(outcome IN ('enqueued','blocked_cost','blocked_policy','denied','suppressed','expired','deferred')),
 safe_code text NOT NULL CHECK(safe_code IN ('READY_REPROCESS_ALLOWED','READINESS_SCOPE_NOT_CONFIGURED',
   'READINESS_MODE_UNCONFIRMED','READINESS_MODE_MANUAL','READINESS_ROLE_UNKNOWN','READINESS_DEPENDENCY_CHANGED',
   'READINESS_NOT_READY','READINESS_OBSERVATION_EXPIRED','READINESS_ACCESS_DENIED','READINESS_SOURCE_CHANGED',
   'READINESS_POLICY_BLOCKED','COST_LIMIT_EXCEEDED','READINESS_COST_UNCONFIRMED','READINESS_APPROVAL_MISSING',
   'READINESS_ACTIVE_RUN_EXISTS','READINESS_CONTEXT_SUPPRESSED','READINESS_BACKOFF_PENDING')),
 new_run_id text CHECK(new_run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
 received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
 content_digest text NOT NULL CHECK(content_digest ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(tenant_id,workspace_id,processing_run_id,capability_slot,event_id,phase),
 FOREIGN KEY(tenant_id,workspace_id,processing_run_id,capability_slot,actor_id,notebook_id,source_id,source_version_id)
   REFERENCES public.document_role_waits(tenant_id,workspace_id,processing_run_id,capability_slot,actor_id,notebook_id,source_id,source_version_id),
 CHECK((phase='deferred')=(outcome='deferred')),
 CHECK((new_run_id IS NOT NULL)=(outcome IN ('enqueued','blocked_cost','blocked_policy'))),
 CHECK((outcome='enqueued' AND safe_code='READY_REPROCESS_ALLOWED')
   OR (outcome='blocked_cost' AND safe_code='COST_LIMIT_EXCEEDED')
   OR (outcome='blocked_policy' AND safe_code='READINESS_POLICY_BLOCKED')
   OR (outcome='denied' AND safe_code='READINESS_SCOPE_NOT_CONFIGURED')
   OR (outcome='denied' AND safe_code='READINESS_ACCESS_DENIED')
   OR (outcome='denied' AND safe_code='READINESS_SOURCE_CHANGED')
   OR (outcome='suppressed' AND safe_code='READINESS_MODE_MANUAL')
   OR (outcome='suppressed' AND safe_code='READINESS_ACTIVE_RUN_EXISTS')
   OR (outcome='suppressed' AND safe_code='READINESS_CONTEXT_SUPPRESSED')
   OR (outcome='expired' AND safe_code='READINESS_OBSERVATION_EXPIRED')
   OR (outcome='deferred' AND safe_code='READINESS_MODE_UNCONFIRMED')
   OR (outcome='deferred' AND safe_code='READINESS_ROLE_UNKNOWN')
   OR (outcome='deferred' AND safe_code='READINESS_DEPENDENCY_CHANGED')
   OR (outcome='deferred' AND safe_code='READINESS_NOT_READY')
   OR (outcome='deferred' AND safe_code='READINESS_COST_UNCONFIRMED')
   OR (outcome='deferred' AND safe_code='READINESS_APPROVAL_MISSING')
   OR (outcome='deferred' AND safe_code='READINESS_BACKOFF_PENDING'))
);
CREATE UNIQUE INDEX document_reprocess_one_run_context ON public.document_reprocess_receipts
 (tenant_id,workspace_id,actor_id,source_version_id,capability_slot,frozen_context_digest) WHERE new_run_id IS NOT NULL;
CREATE UNIQUE INDEX document_reprocess_first_suppression ON public.document_reprocess_receipts
 (tenant_id,workspace_id,actor_id,source_version_id,capability_slot,frozen_context_digest,safe_code) WHERE outcome='suppressed';

-- GUC는 scope 제한일 뿐 인가가 아니다. 실제 identity/role/workflow/부모를 읽는다.
-- EDIT와 ANALYZE를 모두 가진 기존 role만: reviewer의 ANALYZE만으로는 허용하지 않는다.
CREATE FUNCTION public.document_readiness_parent_allowed(t text,w text,a text,n text,s text,v text,r text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT coalesce((SELECT
   (CASE WHEN tr.user_id IS NOT NULL THEN CASE
      WHEN aw.workspace_kind='personal' AND tr.role='personal_owner' THEN tr.role
      WHEN aw.workspace_kind='organization' AND tr.role='organization_admin' THEN tr.role END
     ELSE m.role END) IN ('personal_owner','organization_admin','workspace_admin','editor')
   AND (src.created_by=a OR (CASE WHEN tr.user_id IS NOT NULL THEN tr.role ELSE m.role END)
        IN ('personal_owner','organization_admin','workspace_admin'))
   AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      WHERE o.tenant_id=t AND o.user_id=a AND o.state<>'active')
   AND NOT EXISTS(SELECT 1 FROM public.identity_auth_source_access x
      WHERE x.tenant_id=t AND x.workspace_id=w AND x.user_id=a AND x.source_version_id=v AND x.allowed=0)
   AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=t
      AND newer.workspace_id=w AND newer.source_id=s AND newer.version>sv.version)
   AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings x WHERE x.tenant_id=t
      AND x.workspace_id=w AND x.notebook_id=n AND x.source_id=s AND x.source_version_id=v)
   AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests x WHERE x.tenant_id=t
      AND x.workspace_id=w AND x.notebook_id=n)
   AND NOT EXISTS(SELECT 1 FROM public.deletion_requests x WHERE x.tenant_id=t
      AND x.workspace_id=w AND x.source_id=s AND NOT x.source_active)
 FROM public.identity_users u JOIN public.identity_auth_workspaces aw ON aw.tenant_id=t AND aw.workspace_id=w
 LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=t AND tr.user_id=a AND tr.state='active'
 LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=t AND m.workspace_id=w AND m.user_id=a AND m.state='active'
 JOIN public.sources src ON src.tenant_id=t AND src.workspace_id=w AND src.record_id=s
 JOIN public.source_versions sv ON sv.tenant_id=t AND sv.workspace_id=w AND sv.record_id=v AND sv.source_id=s
 JOIN public.processing_runs pr ON pr.tenant_id=t AND pr.workspace_id=w AND pr.record_id=r
    AND pr.source_version_id=v AND pr.created_by=a AND pr.canonical_json->>'notebook_id'=n
 JOIN public.notebooks nb ON nb.tenant_id=t AND nb.workspace_id=w AND nb.notebook_id=n
 JOIN public.notebook_bindings b ON b.tenant_id=t AND b.workspace_id=w AND b.notebook_id=n
    AND b.binding_kind='source' AND b.record_id=s AND b.version_id=v
 WHERE u.user_id=a AND u.state='active' AND src.state NOT IN ('disabled','expired','deleting','deleted')),false)
$$;

CREATE FUNCTION public.guard_document_readiness_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP='INSERT' AND current_user='daon_notebook_delete' THEN RETURN NEW; END IF;
 IF TG_OP='DELETE' AND current_user='daon_notebook_delete'
   AND (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=session_user) THEN
   IF (to_jsonb(OLD)->>CASE WHEN TG_TABLE_NAME='document_role_waits' THEN 'created_at' ELSE 'received_at' END)::timestamptz
       >clock_timestamp()-interval '1 year' THEN
     RAISE EXCEPTION 'DOCUMENT_READINESS_RETENTION_NOT_EXPIRED' USING ERRCODE='55000';
   END IF;
   IF TG_TABLE_NAME<>'document_readiness_observations' AND EXISTS(SELECT 1 FROM public.legal_holds h
      WHERE h.tenant_id=to_jsonb(OLD)->>'tenant_id' AND h.workspace_id=to_jsonb(OLD)->>'workspace_id'
        AND h.source_id=to_jsonb(OLD)->>'source_id' AND h.state='active') THEN
     RAISE EXCEPTION 'DOCUMENT_READINESS_LEGAL_HOLD' USING ERRCODE='55000';
   END IF;
   RETURN OLD;
 END IF;
 RAISE EXCEPTION 'DOCUMENT_READINESS_APPEND_ONLY' USING ERRCODE='42501';
END $$;

DO $tables$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY['document_role_waits','document_readiness_observations','document_reprocess_receipts'] LOOP
   EXECUTE format('ALTER TABLE public.%I OWNER TO daon_notebook_delete',name);
   EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',name);
   EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',name);
   EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,daon_app',name);
   -- 비로그인 owner만 global 내부 발견/보호함수 소비. app은 owner의 member가 아니다.
   EXECUTE format('CREATE POLICY %I ON public.%I TO daon_notebook_delete USING(true) WITH CHECK(true)',name||'_owner',name);
   EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.guard_document_readiness_immutable()',name||'_guard',name);
   EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON public.%I FOR EACH STATEMENT EXECUTE FUNCTION public.guard_document_readiness_immutable()',name||'_no_truncate',name);
 END LOOP;
END $tables$;
REVOKE ALL ON SEQUENCE public.document_readiness_observations_observation_sequence_seq FROM PUBLIC,daon_app;

CREATE FUNCTION public.record_document_role_wait(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t text:=p->>'tenant_id'; w text:=p->>'workspace_id'; a text:=p->>'actor_id';
 n text:=p->>'notebook_id'; s text:=p->>'source_id'; v text:=p->>'source_version_id'; r text:=p->>'processing_run_id';
 c text:=p->>'capability'; existing public.document_role_waits%ROWTYPE; mode jsonb:=p->'mode'; latest text;
BEGIN
 IF p IS NULL OR jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>15
 OR NOT p ?& ARRAY['tenant_id','workspace_id','actor_id','notebook_id','source_id','source_version_id',
  'processing_run_id','capability','failure_kind','mode','dependency_connection_id','frozen_context_digest','safe_error_code','failed_at','not_before']
 OR EXISTS(SELECT 1 FROM jsonb_each(p) x WHERE
    (x.key NOT IN ('mode','capability','dependency_connection_id') AND jsonb_typeof(x.value) IS DISTINCT FROM 'string')
    OR (x.key IN ('capability','dependency_connection_id') AND jsonb_typeof(x.value) NOT IN ('string','null')))
 OR ((p->>'dependency_connection_id') IS NOT NULL AND (p->>'dependency_connection_id') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$')
 OR octet_length(p::text)>16384 OR jsonb_typeof(mode) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(mode))<>2
 OR jsonb_typeof(mode->'mode') IS DISTINCT FROM 'string' OR jsonb_typeof(mode->'origin') IS DISTINCT FROM 'string'
 OR NOT mode ?& ARRAY['mode','origin'] OR mode->>'mode' NOT IN ('auto','local_only','pinned','legacy_unknown')
 OR (mode->>'mode'='legacy_unknown') IS DISTINCT FROM (mode->>'origin'='legacy_unknown')
 OR mode->>'origin' NOT IN ('request','legacy_unknown')
 OR p->>'failure_kind' NOT IN ('connection_availability','role_runtime','non_availability','unknown')
 OR (p->>'frozen_context_digest') !~ '^[0-9a-f]{64}$' OR (p->>'safe_error_code') !~ '^[A-Z][A-Z0-9_]{0,255}$'
 OR (p->>'failed_at') !~ '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?(\+00:00|Z)$'
 OR (p->>'not_before') !~ '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?(\+00:00|Z)$'
 OR (p->>'not_before')::timestamptz<(p->>'failed_at')::timestamptz
 OR (p->>'failed_at')::timestamptz>clock_timestamp()+interval '5 seconds' THEN
  RAISE EXCEPTION 'DOCUMENT_READINESS_INPUT_INVALID' USING ERRCODE='23514'; END IF;
 IF t IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
 OR w IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'')
 OR a IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') THEN
  RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('notebook-delete|'||t||'|'||w||'|'||n,0));
 PERFORM pg_advisory_xact_lock(hashtextextended('unbind-notebook|'||t||'|'||w||'|'||n,0));
 PERFORM pg_advisory_xact_lock(hashtextextended('retention-source|'||t||'|'||w||'|'||s,0));
 IF NOT public.document_readiness_parent_allowed(t,w,a,n,s,v,r) THEN
  RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT pr.record_id INTO latest FROM public.processing_runs pr WHERE pr.tenant_id=t AND pr.workspace_id=w
  AND pr.source_version_id=v ORDER BY pr.created_at DESC,pr.record_id DESC LIMIT 1;
 IF latest IS DISTINCT FROM r OR NOT EXISTS(SELECT 1 FROM public.processing_runs pr JOIN public.sources src
  ON src.tenant_id=pr.tenant_id AND src.workspace_id=pr.workspace_id AND src.record_id=s
  WHERE pr.tenant_id=t AND pr.workspace_id=w AND pr.record_id=r AND pr.state IN ('failed','policy_blocked')
   AND src.state IN ('waiting_model','needs_review')
   AND coalesce(pr.canonical_json->'readiness_context'->>'mode','legacy_unknown')=mode->>'mode'
   AND coalesce(pr.canonical_json->'readiness_context'->>'mode_origin','legacy_unknown')=mode->>'origin') THEN
  RAISE EXCEPTION 'DOCUMENT_READINESS_PARENT_CHANGED' USING ERRCODE='23514'; END IF;
 SELECT * INTO existing FROM public.document_role_waits WHERE tenant_id=t AND workspace_id=w
  AND processing_run_id=r AND capability_slot=coalesce(c,'unknown');
 IF FOUND THEN
   IF existing.payload IS DISTINCT FROM p THEN RAISE EXCEPTION 'DOCUMENT_READINESS_REPLAY_CHANGED' USING ERRCODE='23514'; END IF;
   RETURN existing.payload;
 END IF;
 INSERT INTO public.document_role_waits(tenant_id,workspace_id,actor_id,notebook_id,source_id,source_version_id,
  processing_run_id,capability,capability_slot,payload,content_digest)
 VALUES(t,w,a,n,s,v,r,c,coalesce(c,'unknown'),p,encode(sha256(convert_to(p::text,'UTF8')),'hex'));
 RETURN p;
END $$;

CREATE FUNCTION public.record_document_readiness_observation(p jsonb,canonical text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE existing public.document_readiness_observations%ROWTYPE; saved public.document_readiness_observations%ROWTYPE;
 current_version bigint;
BEGIN
 IF p IS NULL OR jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>11
 OR NOT p ?& ARRAY['schema_version','event_id','connection_id','adapter_type','capability','model_catalog_digest',
   'configuration_version','observed_at','probe_kind','status','content_digest'] OR p->'schema_version'<>'1'::jsonb
 OR EXISTS(SELECT 1 FROM jsonb_each(p) x WHERE
   (x.key NOT IN ('schema_version','configuration_version','capability') AND jsonb_typeof(x.value) IS DISTINCT FROM 'string')
   OR (x.key='capability' AND jsonb_typeof(x.value) NOT IN ('string','null')))
 OR jsonb_typeof(p->'configuration_version')<>'number' OR (p->>'configuration_version') !~ '^[1-9][0-9]*$'
 OR (p->>'observed_at') !~ '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?(\+00:00|Z)$'
 OR octet_length(p::text)>16384 THEN RAISE EXCEPTION 'DOCUMENT_READINESS_INPUT_INVALID' USING ERRCODE='23514'; END IF;
 -- Python compact canonical 본문의 정확 bytes를 인증한다. jsonb::text와 직접 hash 비교하지 않는다.
 IF canonical IS NULL OR octet_length(canonical)>16384
 OR canonical::jsonb IS DISTINCT FROM p-'content_digest'
 OR encode(sha256(convert_to(canonical,'UTF8')),'hex') IS DISTINCT FROM p->>'content_digest' THEN
  RAISE EXCEPTION 'DOCUMENT_READINESS_OBSERVATION_DIGEST_INVALID' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('document-readiness-observation|'||(p->>'connection_id'),0));
 SELECT * INTO existing FROM public.document_readiness_observations WHERE event_id=p->>'event_id';
 IF FOUND THEN
   IF existing.payload IS DISTINCT FROM p THEN RAISE EXCEPTION 'DOCUMENT_READINESS_REPLAY_CHANGED' USING ERRCODE='23514'; END IF;
   RETURN existing.payload||jsonb_build_object('observation_sequence',existing.observation_sequence,'received_at',to_char(existing.received_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00');
 END IF;
 SELECT version INTO current_version FROM public.system_provider_connections WHERE connection_id=p->>'connection_id';
 IF current_version IS DISTINCT FROM (p->>'configuration_version')::bigint OR EXISTS(
  SELECT 1 FROM public.document_readiness_observations o WHERE o.connection_id=p->>'connection_id'
  AND o.observed_at>=(p->>'observed_at')::timestamptz) THEN
  RAISE EXCEPTION 'DOCUMENT_READINESS_OBSERVATION_STALE' USING ERRCODE='23514'; END IF;
 INSERT INTO public.document_readiness_observations(event_id,connection_id,adapter_type,capability,model_catalog_digest,
   configuration_version,observed_at,probe_kind,status,content_digest,payload)
 VALUES(p->>'event_id',p->>'connection_id',p->>'adapter_type',p->>'capability',p->>'model_catalog_digest',
   (p->>'configuration_version')::bigint,(p->>'observed_at')::timestamptz,p->>'probe_kind',p->>'status',p->>'content_digest',p)
 RETURNING * INTO saved;
 RETURN p||jsonb_build_object('observation_sequence',saved.observation_sequence,'received_at',to_char(saved.received_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00');
END $$;

CREATE FUNCTION public.discover_document_role_waits(scopes jsonb,after_cursor text[],maximum integer)
RETURNS TABLE(tenant_id text,workspace_id text,actor_id text,notebook_id text,source_id text,source_version_id text,processing_run_id text,capability_slot text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF scopes IS NULL OR jsonb_typeof(scopes) IS DISTINCT FROM 'array' OR jsonb_array_length(scopes)>100
 OR maximum IS NULL OR maximum NOT BETWEEN 1 AND 32
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(scopes) s WHERE jsonb_typeof(s)<>'object'
   OR NOT s ?& ARRAY['tenant_id','workspace_id','actor_id','source_version_id']
   OR (SELECT count(*) FROM jsonb_object_keys(s))<>4
   OR EXISTS(SELECT 1 FROM jsonb_each(s) kv WHERE jsonb_typeof(kv.value)<>'string'
      OR kv.value#>>'{}' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'))
 OR (SELECT count(*) FROM jsonb_array_elements(scopes))<>(SELECT count(DISTINCT s) FROM jsonb_array_elements(scopes) s) THEN
  RAISE EXCEPTION 'DOCUMENT_READINESS_SCOPE_INVALID' USING ERRCODE='23514'; END IF;
 IF after_cursor IS NOT NULL AND (
  array_ndims(after_cursor) IS DISTINCT FROM 1 OR array_lower(after_cursor,1) IS DISTINCT FROM 1
  OR cardinality(after_cursor)<>4
  OR EXISTS(SELECT 1 FROM unnest(after_cursor) v WHERE v IS NULL
    OR v !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$')
  OR after_cursor[4] NOT IN ('text_generation','image_understanding','document_parsing','speech_to_text','unknown')) THEN
  RAISE EXCEPTION 'DOCUMENT_READINESS_CURSOR_INVALID' USING ERRCODE='23514'; END IF;
 -- 발견은 인가가 아니다. 검증한 서버 scope를 LIMIT 이전에 적용한다.
 RETURN QUERY SELECT x.tenant_id,x.workspace_id,x.actor_id,x.notebook_id,x.source_id,x.source_version_id,x.processing_run_id,x.capability_slot
 FROM public.document_role_waits x WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(scopes) s
  WHERE (s->>'tenant_id',s->>'workspace_id',s->>'actor_id',s->>'source_version_id')=
    (x.tenant_id,x.workspace_id,x.actor_id,x.source_version_id))
 AND (after_cursor IS NULL OR (x.tenant_id,x.workspace_id,x.processing_run_id,x.capability_slot)>
      (after_cursor[1],after_cursor[2],after_cursor[3],after_cursor[4]))
 ORDER BY x.tenant_id,x.workspace_id,x.processing_run_id,x.capability_slot LIMIT maximum;
END $$;

CREATE FUNCTION public.read_document_role_wait(t text,w text,r text,c text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT x.payload FROM public.document_role_waits x WHERE x.tenant_id=t AND x.workspace_id=w
 AND x.processing_run_id=r AND x.capability_slot=c
 AND t=nullif(current_setting('app.tenant_id',true),'') AND w=nullif(current_setting('app.workspace_id',true),'')
 AND x.actor_id=nullif(current_setting('app.actor_id',true),'')
 AND public.document_readiness_parent_allowed(t,w,x.actor_id,x.notebook_id,x.source_id,x.source_version_id,r)
$$;
CREATE FUNCTION public.read_latest_document_readiness_observation(c text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT x.payload||jsonb_build_object('observation_sequence',x.observation_sequence,
   'received_at',to_char(x.received_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00')
 FROM public.document_readiness_observations x WHERE x.connection_id=c ORDER BY x.observation_sequence DESC LIMIT 1
$$;

CREATE FUNCTION public.read_document_reprocess_history(t text,w text,a text,n text,s text,v text,r text,c text,d text)
RETURNS SETOF jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT x.payload FROM public.document_reprocess_receipts x WHERE
 (x.tenant_id,x.workspace_id,x.actor_id,x.source_version_id,x.capability_slot,x.frozen_context_digest)=(t,w,a,v,c,d)
 AND t=nullif(current_setting('app.tenant_id',true),'') AND w=nullif(current_setting('app.workspace_id',true),'')
 AND a=nullif(current_setting('app.actor_id',true),'')
 AND public.document_readiness_parent_allowed(t,w,a,n,s,v,r) ORDER BY x.received_at
$$;

CREATE FUNCTION public.record_document_reprocess_receipt(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t text:=p->>'tenant_id'; w text:=p->>'workspace_id'; a text:=p->>'actor_id';
 n text:=p->>'notebook_id'; s text:=p->>'source_id'; v text:=p->>'source_version_id'; r text:=p->>'processing_run_id';
 c text:=p->>'capability_slot'; d text:=p->>'frozen_context_digest'; saved public.document_reprocess_receipts%ROWTYPE;
 existing public.document_reprocess_receipts%ROWTYPE; body jsonb; run text:=p->>'new_run_id';
BEGIN
 IF p IS NULL OR jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>15
 OR NOT p ?& ARRAY['tenant_id','workspace_id','actor_id','notebook_id','source_id','source_version_id',
   'processing_run_id','capability_slot','event_id','frozen_context_digest','phase','outcome','safe_code','new_run_id','received_at']
 OR EXISTS(SELECT 1 FROM jsonb_each(p) x WHERE
   (x.key<>'new_run_id' AND jsonb_typeof(x.value) IS DISTINCT FROM 'string')
   OR (x.key='new_run_id' AND jsonb_typeof(x.value) NOT IN ('string','null'))) THEN
  RAISE EXCEPTION 'DOCUMENT_READINESS_INPUT_INVALID' USING ERRCODE='23514';
 END IF;
 IF t IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
 OR w IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'')
 OR a IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'')
 OR NOT public.document_readiness_parent_allowed(t,w,a,n,s,v,r) THEN
  RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('document-readiness-context|'||t||'|'||w||'|'||a||'|'||v||'|'||c||'|'||d,0));
 IF p->>'outcome'='suppressed' THEN
  SELECT * INTO existing FROM public.document_reprocess_receipts x WHERE
   (x.tenant_id,x.workspace_id,x.actor_id,x.source_version_id,x.capability_slot,x.frozen_context_digest,x.safe_code)=
   (t,w,a,v,c,d,p->>'safe_code') AND x.outcome='suppressed';
  IF FOUND THEN RETURN existing.payload; END IF;
 END IF;
 SELECT * INTO existing FROM public.document_reprocess_receipts x WHERE
  (x.tenant_id,x.workspace_id,x.processing_run_id,x.capability_slot,x.event_id,x.phase)=(t,w,r,c,p->>'event_id',p->>'phase');
 IF FOUND THEN
  IF existing.payload-'received_at' IS DISTINCT FROM p-'received_at' THEN
   RAISE EXCEPTION 'DOCUMENT_READINESS_REPLAY_CHANGED' USING ERRCODE='23514'; END IF;
  RETURN existing.payload;
 END IF;
 IF run IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM public.processing_runs pr
   WHERE pr.tenant_id=t AND pr.workspace_id=w AND pr.record_id=run AND pr.created_by=a
    AND pr.source_version_id=v AND pr.trigger_type='readiness_event' AND pr.trigger_event_id=p->>'event_id'
    AND pr.canonical_json->'readiness_context'->>'frozen_context_digest'=d
    AND pr.state=CASE WHEN p->>'outcome'='enqueued' THEN
      CASE WHEN pr.modality='audio' THEN 'speech_to_text' ELSE 'vision_llm_understanding' END ELSE 'policy_blocked' END)
 OR (SELECT count(*) FROM public.document_processing_jobs j WHERE j.tenant_id=t AND j.workspace_id=w AND j.processing_run_id=run)
    <>CASE WHEN p->>'outcome'='enqueued' THEN 1 ELSE 0 END
 OR NOT EXISTS(SELECT 1 FROM public.audit_events au WHERE au.tenant_id=t AND au.workspace_id=w AND au.actor_id=a
      AND au.action='source.readiness_reprocess' AND au.target_type='processing_run' AND au.target_id=run)) THEN
  RAISE EXCEPTION 'DOCUMENT_READINESS_RESULT_UNCONFIRMED' USING ERRCODE='23514'; END IF;
 body:=p||jsonb_build_object('received_at',to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00');
 INSERT INTO public.document_reprocess_receipts(tenant_id,workspace_id,actor_id,notebook_id,source_id,source_version_id,
  processing_run_id,capability_slot,event_id,frozen_context_digest,phase,outcome,safe_code,new_run_id,received_at,payload,content_digest)
 VALUES(t,w,a,n,s,v,r,c,p->>'event_id',d,p->>'phase',p->>'outcome',p->>'safe_code',run,(body->>'received_at')::timestamptz,
  body,encode(sha256(convert_to(body::text,'UTF8')),'hex')) RETURNING * INTO saved;
 RETURN saved.payload;
END $$;

CREATE FUNCTION public.read_document_blocked_parent(t text,w text,a text,n text,s text,v text,r text,new_r text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT t=nullif(current_setting('app.tenant_id',true),'') AND w=nullif(current_setting('app.workspace_id',true),'')
 AND a=nullif(current_setting('app.actor_id',true),'') AND public.document_readiness_parent_allowed(t,w,a,n,s,v,r)
 AND EXISTS(SELECT 1 FROM public.document_reprocess_receipts x WHERE
  (x.tenant_id,x.workspace_id,x.actor_id,x.notebook_id,x.source_id,x.source_version_id,x.processing_run_id,x.new_run_id)=
  (t,w,a,n,s,v,r,new_r) AND x.outcome IN ('blocked_cost','blocked_policy'))
$$;

-- 보존 정리: 운영자 전용. 실행/스케줄/상시 role·membership 추가는 없다.
CREATE FUNCTION public.cleanup_document_readiness(t text,w text,maximum integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item record; removed integer:=0;
BEGIN
 IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=session_user)
 OR maximum IS NULL OR maximum NOT BETWEEN 1 AND 32 OR t IS NULL OR w IS NULL
 OR t !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$' OR w !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$' THEN
  RAISE EXCEPTION 'CURRENT_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM set_config('app.tenant_id',t,true); PERFORM set_config('app.workspace_id',w,true);
 FOR item IN SELECT DISTINCT x.source_id FROM public.document_role_waits x WHERE x.tenant_id=t AND x.workspace_id=w
   AND x.created_at<clock_timestamp()-interval '1 year' ORDER BY x.source_id LIMIT maximum LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended('retention-source|'||t||'|'||w||'|'||item.source_id,0));
  IF EXISTS(SELECT 1 FROM public.legal_holds h WHERE h.tenant_id=t AND h.workspace_id=w AND h.source_id=item.source_id AND h.state='active') THEN CONTINUE; END IF;
  DELETE FROM public.document_reprocess_receipts x WHERE x.tenant_id=t AND x.workspace_id=w AND x.source_id=item.source_id
    AND x.received_at<clock_timestamp()-interval '1 year';
  DELETE FROM public.document_role_waits x WHERE x.tenant_id=t AND x.workspace_id=w AND x.source_id=item.source_id
    AND x.created_at<clock_timestamp()-interval '1 year' AND NOT EXISTS(SELECT 1 FROM public.document_reprocess_receipts c
       WHERE (c.tenant_id,c.workspace_id,c.processing_run_id,c.capability_slot)=(x.tenant_id,x.workspace_id,x.processing_run_id,x.capability_slot));
  removed:=removed+1;
 END LOOP;
 DELETE FROM public.document_readiness_observations o WHERE o.event_id IN (
  SELECT e.event_id FROM public.document_readiness_observations e WHERE e.received_at<clock_timestamp()-interval '1 year'
   AND NOT EXISTS(SELECT 1 FROM public.document_reprocess_receipts c WHERE c.event_id=e.event_id)
   ORDER BY e.received_at LIMIT maximum);
 RETURN removed;
END $$;

-- 집계만 반환한다. 업무 DML/행 잠금 없이 READ ONLY transaction에서 호출한다.
-- 기존 private helper의 현재 원문/특수 Source 정책은 같은 내부 owner로만 재사용한다.
CREATE FUNCTION public.read_document_readiness_metrics(t text,w text,a text,scope text)
RETURNS TABLE(metric_id text,value bigint,has_records boolean,coverage_partial boolean,
 checked_at timestamptz,window_start timestamptz,window_end timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $readiness_metrics$
DECLARE original_notebook text:=current_setting('app.notebook_id',true);
 filter_text text:=nullif(current_setting('app.readiness_visible_source_versions',true),'');
 visible_filter jsonb;
 query_time timestamptz:=clock_timestamp(); effective_role text; kind text; item record;
 visible_wait_keys jsonb:='[]'::jsonb; source_allowed boolean; role_records boolean:=false;
 suppression_records boolean:=false; suppression_count bigint:=0; role_count bigint; i integer;
 counts bigint[]:=ARRAY[0,0,0,0,0]::bigint[];
 ids text[]:=ARRAY['waiting_text_generation','waiting_image_understanding','waiting_document_parsing',
   'waiting_speech_to_text','waiting_role_unknown'];
BEGIN
 -- 이 목록은 서버가 현재 확인한 추가 제한일 뿐이다. 이후 실제 DB 인가를 대체하지 않는다.
 IF filter_text IS NULL OR octet_length(filter_text)>65536 THEN
   RAISE EXCEPTION 'DOCUMENT_READINESS_FILTER_INVALID' USING ERRCODE='22023'; END IF;
 visible_filter:=filter_text::jsonb;
 IF jsonb_typeof(visible_filter) IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'DOCUMENT_READINESS_FILTER_INVALID' USING ERRCODE='22023'; END IF;
 IF jsonb_array_length(visible_filter)>256
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(visible_filter) AS items(entry)
     WHERE jsonb_typeof(entry)<>'string' OR (entry#>>'{}') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$')
   OR (SELECT count(DISTINCT entry) FROM jsonb_array_elements(visible_filter) AS items(entry))<>jsonb_array_length(visible_filter) THEN
   RAISE EXCEPTION 'DOCUMENT_READINESS_FILTER_INVALID' USING ERRCODE='22023'; END IF;
 IF t IS NULL OR w IS NULL OR a IS NULL OR scope IS NULL OR scope NOT IN ('self','workspace_visible')
   OR t IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
   OR w IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'')
   OR a IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') THEN
   RAISE EXCEPTION 'DOCUMENT_READINESS_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT aw.workspace_kind,CASE WHEN tr.user_id IS NOT NULL THEN CASE
    WHEN aw.workspace_kind='personal' AND tr.role='personal_owner' THEN tr.role
    WHEN aw.workspace_kind='organization' AND tr.role='organization_admin' THEN tr.role END
   ELSE CASE WHEN m.role IN ('workspace_admin','editor','reviewer','approver','viewer') THEN m.role END END INTO kind,effective_role
 FROM public.identity_users u JOIN public.identity_auth_workspaces aw ON aw.tenant_id=t AND aw.workspace_id=w
 LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=t AND tr.user_id=a AND tr.state='active'
 LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=t AND m.workspace_id=w AND m.user_id=a AND m.state='active'
 WHERE u.user_id=a AND u.state='active';
 IF effective_role IS NULL OR effective_role NOT IN
   ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
   OR (scope='workspace_visible' AND effective_role NOT IN ('personal_owner','organization_admin','workspace_admin'))
   OR (kind='organization' AND EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
       WHERE o.tenant_id=t AND o.user_id=a AND o.state<>'active')) THEN
   RAISE EXCEPTION 'DOCUMENT_READINESS_ACCESS_DENIED' USING ERRCODE='42501'; END IF;

 -- has_records 자체도 동일한 현재 가시성 안에서만 결정한다. 다른 자료 존재를 누설하지 않는다.
 FOR item IN
  SELECT x.processing_run_id,x.capability_slot,x.source_version_id,x.notebook_id,src.state,
   sv.canonical_json->>'origin_kind' AS origin_kind,latest.record_id=x.processing_run_id AS current_run
  FROM public.document_role_waits x
  JOIN public.sources src ON (src.tenant_id,src.workspace_id,src.record_id)=(x.tenant_id,x.workspace_id,x.source_id)
  JOIN public.source_versions sv ON (sv.tenant_id,sv.workspace_id,sv.record_id,sv.source_id)=
    (x.tenant_id,x.workspace_id,x.source_version_id,x.source_id)
  JOIN public.processing_runs pr ON (pr.tenant_id,pr.workspace_id,pr.record_id,pr.source_version_id)=
    (x.tenant_id,x.workspace_id,x.processing_run_id,x.source_version_id) AND pr.created_by=x.actor_id
    AND pr.canonical_json->>'notebook_id'=x.notebook_id
  JOIN public.notebooks nb ON (nb.tenant_id,nb.workspace_id,nb.notebook_id)=(x.tenant_id,x.workspace_id,x.notebook_id)
  JOIN public.notebook_bindings b ON (b.tenant_id,b.workspace_id,b.notebook_id,b.record_id,b.version_id)=
    (x.tenant_id,x.workspace_id,x.notebook_id,x.source_id,x.source_version_id) AND b.binding_kind='source'
  JOIN LATERAL (SELECT lp.record_id FROM public.processing_runs lp
    WHERE (lp.tenant_id,lp.workspace_id,lp.source_version_id)=(x.tenant_id,x.workspace_id,x.source_version_id)
    ORDER BY lp.created_at DESC,lp.record_id DESC LIMIT 1) latest ON true
  WHERE x.tenant_id=t AND x.workspace_id=w AND (scope='workspace_visible' OR (x.actor_id=a AND nb.created_by=a))
   AND sv.record_id IN (SELECT jsonb_array_elements_text(visible_filter))
   AND src.state NOT IN ('expired','disabled','deleting','deleted')
   AND NOT EXISTS(SELECT 1 FROM public.identity_auth_source_access acl WHERE acl.tenant_id=t
     AND acl.workspace_id=w AND acl.user_id=a AND acl.source_version_id=x.source_version_id AND acl.allowed=0)
   AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=t AND newer.workspace_id=w
     AND newer.source_id=x.source_id AND newer.version>sv.version)
   AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=t AND u.workspace_id=w
     AND u.notebook_id=x.notebook_id AND u.source_id=x.source_id AND u.source_version_id=x.source_version_id)
   AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=t AND d.workspace_id=w
     AND d.notebook_id=x.notebook_id AND d.state IN ('accepted','deleting','completed'))
   AND NOT EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=t AND d.workspace_id=w
     AND d.source_id=x.source_id AND (d.source_active=false OR d.state='purged'))
 LOOP
  IF coalesce(item.origin_kind,'') IN ('website','mcp_open_law') THEN
   -- Notebook는 현재 binding에서 읽었다. caller의 ambient Notebook를 자료 근거로 사용하지 않는다.
   PERFORM set_config('app.notebook_id',item.notebook_id,true);
   source_allowed:=public.source_access_notification_access(item.source_version_id,item.notebook_id);
   PERFORM set_config('app.notebook_id',coalesce(original_notebook,''),true);
  ELSE
   source_allowed:=public.source_access_notification_access(item.source_version_id,NULL);
  END IF;
  IF source_allowed IS DISTINCT FROM true THEN CONTINUE; END IF;
  role_records:=true;
  visible_wait_keys:=visible_wait_keys||jsonb_build_array(jsonb_build_object(
    'processing_run_id',item.processing_run_id,'capability_slot',item.capability_slot));
 END LOOP;

 -- 같은 Version/역할의 여러 과거 실패는 현재 대기 수를 늘리지 않는다.
 FOR i IN 1..5 LOOP
  SELECT count(DISTINCT x.source_version_id) INTO role_count
  FROM public.document_role_waits x JOIN jsonb_to_recordset(visible_wait_keys)
    AS v(processing_run_id text,capability_slot text)
    ON (v.processing_run_id,v.capability_slot)=(x.processing_run_id,x.capability_slot)
  JOIN public.sources src ON (src.tenant_id,src.workspace_id,src.record_id)=(x.tenant_id,x.workspace_id,x.source_id)
  JOIN LATERAL (SELECT lp.record_id FROM public.processing_runs lp
    WHERE (lp.tenant_id,lp.workspace_id,lp.source_version_id)=(x.tenant_id,x.workspace_id,x.source_version_id)
    ORDER BY lp.created_at DESC,lp.record_id DESC LIMIT 1) latest ON true
  WHERE x.tenant_id=t AND x.workspace_id=w AND src.state='waiting_model' AND latest.record_id=x.processing_run_id
    AND x.capability_slot=(ARRAY['text_generation','image_understanding','document_parsing','speech_to_text','unknown'])[i];
  counts[i]:=role_count;
 END LOOP;
 -- 24h로 먼저 자르지 않는다. 과거 최초 억제가 창 밖이면 후속 시도로 최근 수를 부풀리지 않는다.
 SELECT count(*)>0,count(*) FILTER(WHERE first_received>query_time-interval '24 hours' AND first_received<=query_time)
 INTO suppression_records,suppression_count FROM (
  SELECT min(c.received_at) AS first_received FROM public.document_reprocess_receipts c
  JOIN jsonb_to_recordset(visible_wait_keys) AS v(processing_run_id text,capability_slot text)
    ON (v.processing_run_id,v.capability_slot)=(c.processing_run_id,c.capability_slot)
  WHERE c.tenant_id=t AND c.workspace_id=w AND c.outcome='suppressed'
  GROUP BY c.actor_id,c.source_version_id,c.capability_slot,c.frozen_context_digest
 ) first_suppression;
 PERFORM set_config('app.notebook_id',coalesce(original_notebook,''),true);
 FOR i IN 1..5 LOOP
  RETURN QUERY SELECT ids[i],CASE WHEN role_records THEN counts[i] ELSE NULL::bigint END,
    role_records,true,query_time,NULL::timestamptz,NULL::timestamptz;
 END LOOP;
 RETURN QUERY SELECT 'retry_suppressed_contexts_24h'::text,
   CASE WHEN suppression_records THEN suppression_count ELSE NULL::bigint END,suppression_records,true,query_time,
   CASE WHEN suppression_records THEN query_time-interval '24 hours' ELSE NULL::timestamptz END,
   CASE WHEN suppression_records THEN query_time ELSE NULL::timestamptz END;
EXCEPTION WHEN OTHERS THEN
 PERFORM set_config('app.notebook_id',coalesce(original_notebook,''),true);
 RAISE;
END $readiness_metrics$;

DO $function_permissions$
DECLARE signature text;
BEGIN
 FOREACH signature IN ARRAY ARRAY['document_readiness_parent_allowed(text,text,text,text,text,text,text)',
  'guard_document_readiness_immutable()','record_document_role_wait(jsonb)',
  'record_document_readiness_observation(jsonb,text)','discover_document_role_waits(jsonb,text[],integer)',
  'read_document_role_wait(text,text,text,text)','read_latest_document_readiness_observation(text)',
  'read_document_reprocess_history(text,text,text,text,text,text,text,text,text)',
  'record_document_reprocess_receipt(jsonb)','read_document_blocked_parent(text,text,text,text,text,text,text,text)',
  'cleanup_document_readiness(text,text,integer)','read_document_readiness_metrics(text,text,text,text)'] LOOP
  EXECUTE 'ALTER FUNCTION public.'||signature||' OWNER TO daon_notebook_delete';
  EXECUTE 'REVOKE ALL ON FUNCTION public.'||signature||' FROM PUBLIC,daon_app';
 END LOOP;
 FOREACH signature IN ARRAY ARRAY['record_document_role_wait(jsonb)','record_document_readiness_observation(jsonb,text)',
  'discover_document_role_waits(jsonb,text[],integer)','read_document_role_wait(text,text,text,text)',
  'read_latest_document_readiness_observation(text)',
  'read_document_reprocess_history(text,text,text,text,text,text,text,text,text)',
  'record_document_reprocess_receipt(jsonb)','read_document_blocked_parent(text,text,text,text,text,text,text,text)',
  'read_document_readiness_metrics(text,text,text,text)'] LOOP
  EXECUTE 'GRANT EXECUTE ON FUNCTION public.'||signature||' TO daon_app';
 END LOOP;
END $function_permissions$;
