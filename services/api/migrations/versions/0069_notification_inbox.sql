-- 0068 기반 후보. 승인된 Studio Job 소유권 분리 외 기존 표/행/RLS/클러스터 역할은 보존한다.
DO $precondition$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT count(*) FROM public.alembic_version)<>1
    OR (SELECT version_num FROM public.alembic_version)<>'0068'
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
      AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb)
    OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
    OR NOT pg_catalog.pg_has_role('daon_notebook_delete','daon_app','MEMBER')
    OR (SELECT count(*) FROM pg_catalog.pg_class WHERE oid IN
      ('public.notifications'::regclass,'public.idempotency_records'::regclass)
      AND relowner='daon_app'::regrole AND relrowsecurity AND relforcerowsecurity)<>2
    OR pg_catalog.to_regclass('public.notification_details') IS NOT NULL
    OR pg_catalog.to_regclass('public.notification_source_outbox') IS NOT NULL
    OR pg_catalog.to_regclass('public.notification_audit_outbox') IS NOT NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_MIGRATION_PRECONDITION' USING ERRCODE='55000';
  END IF;
END $precondition$;

-- S42_SG_TERMINAL_GUARD_BEGIN: 기존 0069 QA에는 이 블록만 동일 transaction으로 적용한다.
DO $sg_preflight$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT count(*) FROM public.alembic_version)<>1
    OR (SELECT version_num FROM public.alembic_version) NOT IN ('0068','0069')
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
      AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb)
    OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
    OR NOT pg_catalog.pg_has_role('daon_notebook_delete','daon_app','MEMBER')
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid='public.studio_generation_jobs'::regclass
      AND relkind='r' AND relowner='daon_app'::regrole AND NOT relrowsecurity AND NOT relforcerowsecurity)
    OR pg_catalog.to_regprocedure('public.guard_studio_generation_job()') IS NOT NULL
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid='public.studio_generation_jobs'::regclass
      AND tgname='studio_generation_jobs_guard') THEN
    RAISE EXCEPTION 'STUDIO_JOB_GUARD_PRECONDITION' USING ERRCODE='55000';
  END IF;
  LOCK TABLE public.studio_generation_jobs IN ACCESS EXCLUSIVE MODE;
END $sg_preflight$;

CREATE FUNCTION public.guard_studio_generation_job() RETURNS trigger LANGUAGE plpgsql
  SET search_path=pg_catalog,pg_temp AS $sg_guard$
DECLARE v_old jsonb; v_new jsonb;
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.state<>'queued' OR NEW.version<>1 OR NEW.attempt<>0
      OR NEW.lease_owner IS NOT NULL OR NEW.lease_until IS NOT NULL
      OR NEW.safe_error_code IS NOT NULL OR NEW.studio_output_id IS NOT NULL
      OR NEW.output_version_id IS NOT NULL OR NEW.completed_at IS NOT NULL THEN
      RAISE EXCEPTION 'STUDIO_JOB_INITIAL_STATE_INVALID' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.state IN ('completed','failed','unavailable') THEN
    RAISE EXCEPTION 'STUDIO_JOB_TERMINAL_IMMUTABLE' USING ERRCODE='23514';
  END IF;
  v_old:=to_jsonb(OLD); v_new:=to_jsonb(NEW);
  -- 각 실제 queue 경로의 변경 열만 허용한다. 입력/범위/actor/생성정보는 모든 UPDATE에서 불변이다.
  IF NEW.state='leased' AND (OLD.state='queued' AND OLD.next_attempt_at<=CURRENT_TIMESTAMP
      OR OLD.state IN ('leased','generating') AND OLD.lease_until<CURRENT_TIMESTAMP)
    AND OLD.attempt<OLD.max_attempts AND NEW.attempt=OLD.attempt+1 AND NEW.version=OLD.version+1
    AND NEW.lease_owner IS NOT NULL AND NEW.lease_until IS NOT NULL
    AND v_old-ARRAY['state','attempt','version','lease_owner','lease_until']
      =v_new-ARRAY['state','attempt','version','lease_owner','lease_until'] THEN
    RETURN NEW;
  ELSIF OLD.state='leased' AND NEW.state='generating' AND v_old-'state'=v_new-'state' THEN
    RETURN NEW;
  ELSIF OLD.state IN ('leased','generating') AND NEW.state='queued'
    AND NEW.version=OLD.version+1 AND NEW.lease_owner IS NULL AND NEW.lease_until IS NULL
    AND v_old-ARRAY['state','version','lease_owner','lease_until','safe_error_code','next_attempt_at']
      =v_new-ARRAY['state','version','lease_owner','lease_until','safe_error_code','next_attempt_at'] THEN
    RETURN NEW;
  ELSIF OLD.state IN ('leased','generating') AND NEW.state IN ('completed','failed','unavailable')
    AND NEW.version=OLD.version+1 AND NEW.lease_owner IS NULL AND NEW.lease_until IS NULL
    AND NEW.completed_at IS NOT DISTINCT FROM CURRENT_TIMESTAMP
    AND v_old-ARRAY['state','version','lease_owner','lease_until','safe_error_code','studio_output_id','output_version_id','completed_at']
      =v_new-ARRAY['state','version','lease_owner','lease_until','safe_error_code','studio_output_id','output_version_id','completed_at'] THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'STUDIO_JOB_MUTATION_INVALID' USING ERRCODE='23514';
END $sg_guard$;
ALTER FUNCTION public.guard_studio_generation_job() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_studio_generation_job() FROM PUBLIC,daon_app;
ALTER TABLE public.studio_generation_jobs OWNER TO daon_notebook_delete;
REVOKE ALL ON TABLE public.studio_generation_jobs FROM PUBLIC,daon_app;
GRANT SELECT,INSERT,UPDATE ON TABLE public.studio_generation_jobs TO daon_app;
-- DELETE는 trigger로 막지 않는다. 기존 owner SECURITY DEFINER의 정상 Notebook purge는 보존한다.
CREATE TRIGGER studio_generation_jobs_guard BEFORE INSERT OR UPDATE ON public.studio_generation_jobs
  FOR EACH ROW EXECUTE FUNCTION public.guard_studio_generation_job();
-- S42_SG_TERMINAL_GUARD_END

CREATE TABLE public.notification_source_outbox (
  tenant_id text NOT NULL,
  workspace_id text NOT NULL,
  source_event_id text NOT NULL CHECK(source_event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  recipient_id text NOT NULL,
  source_store text NOT NULL CHECK(source_store IN ('identity','authorization','organization','document','question','studio','recovery')),
  origin_event_id text NOT NULL CHECK(origin_event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  action text NOT NULL CHECK(action ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  target_type text NOT NULL CHECK(target_type ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  target_id text NOT NULL CHECK(target_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  kind text NOT NULL CHECK(kind IN ('authentication','device','membership','policy','run_state','run_failed','review','approval','delivery','warning','recovery')),
  severity text NOT NULL CHECK(severity IN ('info','warning','critical')),
  notebook_id text CHECK(notebook_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  trace_id text NOT NULL CHECK(trace_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  policy_version text NOT NULL CHECK(policy_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  occurred_at timestamptz NOT NULL,
  state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','delivered','suppressed')),
  delivered_at timestamptz,
  version integer NOT NULL DEFAULT 1 CHECK(version>0),
  PRIMARY KEY(tenant_id,workspace_id,source_event_id,recipient_id),
  FOREIGN KEY(tenant_id,workspace_id) REFERENCES public.workspaces(tenant_id,workspace_id),
  FOREIGN KEY(tenant_id,recipient_id) REFERENCES public.user_accounts(tenant_id,user_id),
  CHECK((state='pending' AND delivered_at IS NULL AND version=1)
    OR (state IN ('delivered','suppressed') AND delivered_at IS NOT NULL AND version=2))
);
CREATE TABLE public.notification_details (
  tenant_id text NOT NULL,
  notification_id text NOT NULL CHECK(notification_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  source_store text NOT NULL CHECK(source_store IN ('identity','authorization','organization','document','question','studio','recovery')),
  origin_event_id text NOT NULL CHECK(origin_event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  source_event_id text NOT NULL CHECK(source_event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  kind text NOT NULL CHECK(kind IN ('authentication','device','membership','policy','run_state','run_failed','review','approval','delivery','warning','recovery')),
  severity text NOT NULL CHECK(severity IN ('info','warning','critical')),
  title text NOT NULL CHECK(length(title) BETWEEN 1 AND 160 AND title=btrim(title) AND title !~ '[<>\x01-\x1f]'),
  summary text NOT NULL CHECK(length(summary) BETWEEN 1 AND 500 AND summary=btrim(summary) AND summary !~ '[<>\x01-\x1f]'),
  resource_type text NOT NULL CHECK(resource_type ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  resource_id text NOT NULL CHECK(resource_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  notebook_id text CHECK(notebook_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  deep_link text NOT NULL CHECK(length(deep_link) BETWEEN 1 AND 512 AND deep_link !~ '[\x01-\x20]'
    AND (deep_link ~ '^/(operations|inbox|notifications|workspaces)([/?:#]|$)'
      OR deep_link ~ '^/notebooks/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}[?](output_version_id|source_version_id|run_id|studio_job_id)=[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      OR deep_link = '/settings/account')
    AND deep_link !~ '^//'),
  trace_id text NOT NULL CHECK(trace_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  policy_version text NOT NULL CHECK(policy_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  delivery_state text NOT NULL CHECK(delivery_state IN ('pending','delivered','failed','suppressed')),
  delivered_at timestamptz,
  PRIMARY KEY(tenant_id,notification_id),
  FOREIGN KEY(tenant_id,notification_id) REFERENCES public.notifications(tenant_id,notification_id),
  CHECK((delivery_state='delivered')=(delivered_at IS NOT NULL))
);
CREATE TABLE public.notification_audit_outbox (
  event_id text PRIMARY KEY CHECK(event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  tenant_id text NOT NULL,
  workspace_id text NOT NULL,
  recipient_id text NOT NULL,
  notification_id text NOT NULL,
  source_event_id text NOT NULL,
  action text NOT NULL CHECK(action IN ('notification.created','notification.read')),
  draft jsonb NOT NULL CHECK(jsonb_typeof(draft)='object'),
  fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
  state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','delivered')),
  delivered_at timestamptz,
  version integer NOT NULL DEFAULT 1 CHECK(version>0),
  FOREIGN KEY(tenant_id,workspace_id) REFERENCES public.workspaces(tenant_id,workspace_id),
  CHECK((state='pending' AND delivered_at IS NULL AND version=1)
    OR (state='delivered' AND delivered_at IS NOT NULL AND version=2)),
  UNIQUE(tenant_id,notification_id,action)
);

ALTER TABLE public.notification_details OWNER TO daon_notebook_delete;
ALTER TABLE public.notification_source_outbox OWNER TO daon_notebook_delete;
ALTER TABLE public.notification_audit_outbox OWNER TO daon_notebook_delete;
ALTER TABLE public.notification_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notification_details FORCE ROW LEVEL SECURITY;
ALTER TABLE public.notification_source_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notification_source_outbox FORCE ROW LEVEL SECURITY;
ALTER TABLE public.notification_audit_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notification_audit_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY notification_detail_recipient ON public.notification_details TO daon_app
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND EXISTS(
    SELECT 1 FROM public.notifications n WHERE n.tenant_id=notification_details.tenant_id
      AND n.notification_id=notification_details.notification_id
      AND n.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND n.recipient_id=nullif(current_setting('app.actor_id',true),'')));
-- 부모 보호 확인에만 쓰는 기존 비로그인 owner의 읽기 범위다. 앱은 이 역할의 구성원이 아니다.
CREATE POLICY notification_detail_guard_scope ON public.notification_details FOR SELECT TO daon_notebook_delete
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND EXISTS(
    SELECT 1 FROM public.notifications n WHERE n.tenant_id=notification_details.tenant_id
      AND n.notification_id=notification_details.notification_id
      AND n.workspace_id=nullif(current_setting('app.workspace_id',true),'')));
CREATE POLICY notification_source_scope ON public.notification_source_outbox TO daon_notebook_delete
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
    AND recipient_id=nullif(current_setting('app.actor_id',true),''));
CREATE POLICY notification_audit_scope ON public.notification_audit_outbox TO daon_notebook_delete
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
    AND recipient_id=nullif(current_setting('app.actor_id',true),''));
REVOKE ALL ON public.notification_details,public.notification_source_outbox,public.notification_audit_outbox FROM PUBLIC,daon_app;
GRANT SELECT ON public.notification_details TO daon_app;
CREATE INDEX notification_source_pending ON public.notification_source_outbox(tenant_id,workspace_id,recipient_id,occurred_at,source_event_id) WHERE state='pending';
CREATE INDEX notification_audit_pending ON public.notification_audit_outbox(tenant_id,workspace_id,recipient_id,event_id) WHERE state='pending';

CREATE FUNCTION public.guard_notification_storage() RETURNS trigger LANGUAGE plpgsql
  SET search_path=pg_catalog,pg_temp AS $$
DECLARE parent public.notifications; source public.notification_source_outbox; payload jsonb; event text;
BEGIN
  IF TG_WHEN='AFTER' THEN
    SELECT * INTO parent FROM public.notifications WHERE tenant_id=NEW.tenant_id AND notification_id=NEW.notification_id;
    event:='audit-notification-created-'||md5(NEW.tenant_id||'|'||NEW.notification_id);
    payload:=jsonb_build_object('event_id',event,'occurred_at',parent.created_at,'actor_id','notification-service',
      'actor_type','service','tenant_id',parent.tenant_id,'workspace_id',parent.workspace_id,
      'action','notification.created','target_type','notification','target_id',NEW.notification_id,
      'outcome','succeeded','trace_id',NEW.trace_id,'policy_version',NEW.policy_version,'before',NULL,
      'after',jsonb_build_object('recipient_id',parent.recipient_id,'kind',NEW.kind,'delivery_state',NEW.delivery_state),
      'metadata',jsonb_build_object('source_event_id',NEW.source_event_id));
    INSERT INTO public.notification_audit_outbox(event_id,tenant_id,workspace_id,recipient_id,notification_id,source_event_id,action,draft,fingerprint)
      VALUES(event,NEW.tenant_id,parent.workspace_id,parent.recipient_id,NEW.notification_id,NEW.source_event_id,
        'notification.created',payload,encode(sha256(convert_to(payload::text,'UTF8')),'hex'));
    RETURN NEW;
  END IF;
  IF TG_OP='DELETE' OR (TG_TABLE_NAME='notification_details' AND TG_OP<>'INSERT') THEN
    RAISE EXCEPTION 'NOTIFICATION_IMMUTABLE' USING ERRCODE='55000';
  END IF;
  IF TG_OP='UPDATE' THEN
    IF (to_jsonb(OLD)-ARRAY['state','delivered_at','version']) IS DISTINCT FROM
        (to_jsonb(NEW)-ARRAY['state','delivered_at','version'])
      OR OLD.state<>'pending' OR NEW.state NOT IN ('delivered','suppressed')
      OR NEW.version<>OLD.version+1 OR NEW.delivered_at IS NULL
      OR (TG_TABLE_NAME='notification_audit_outbox' AND NEW.state<>'delivered') THEN
      RAISE EXCEPTION 'NOTIFICATION_IMMUTABLE' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_TABLE_NAME='notification_details' THEN
    SELECT * INTO parent FROM public.notifications WHERE tenant_id=NEW.tenant_id AND notification_id=NEW.notification_id;
    SELECT * INTO source FROM public.notification_source_outbox WHERE tenant_id=parent.tenant_id
      AND workspace_id=parent.workspace_id AND recipient_id=parent.recipient_id AND source_event_id=NEW.source_event_id;
    IF parent.notification_id IS NULL OR source.source_event_id IS NULL
      OR parent.source_event_id IS DISTINCT FROM NEW.source_event_id
      OR (NEW.source_store,NEW.origin_event_id,NEW.kind,NEW.severity,NEW.resource_type,NEW.resource_id,NEW.notebook_id,NEW.trace_id,NEW.policy_version)
        IS DISTINCT FROM (source.source_store,source.origin_event_id,source.kind,source.severity,source.target_type,source.target_id,source.notebook_id,source.trace_id,source.policy_version) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_MISMATCH' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='notification_source_outbox' THEN
    IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.delivered_at IS NOT NULL THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='notification_audit_outbox' THEN
    IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.delivered_at IS NOT NULL
      OR (NEW.draft->>'event_id',NEW.draft->>'tenant_id',NEW.draft->>'workspace_id',NEW.draft->>'action',NEW.draft->>'target_type',NEW.draft->>'target_id')
        IS DISTINCT FROM (NEW.event_id,NEW.tenant_id,NEW.workspace_id,NEW.action,'notification'::text,NEW.notification_id)
      OR NEW.draft->>'outcome' IS DISTINCT FROM 'succeeded'
      OR NEW.draft->>'trace_id' IS NULL OR NEW.draft->>'trace_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      OR NEW.draft->>'policy_version' IS NULL OR NEW.draft->>'policy_version' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      OR (CASE WHEN NEW.notification_id LIKE 'approval-notice-%'
        THEN public.valid_studio_notice_notification_audit(NEW::public.notification_audit_outbox) IS DISTINCT FROM true
        ELSE NEW.draft->'metadata' IS DISTINCT FROM jsonb_build_object('source_event_id',NEW.source_event_id) END)
      OR (SELECT count(*) FROM jsonb_object_keys(NEW.draft))<>15 THEN
      RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER notification_details_guard BEFORE INSERT OR UPDATE OR DELETE ON public.notification_details FOR EACH ROW EXECUTE FUNCTION public.guard_notification_storage();
CREATE TRIGGER notification_details_created AFTER INSERT ON public.notification_details FOR EACH ROW EXECUTE FUNCTION public.guard_notification_storage();
CREATE TRIGGER notification_source_guard BEFORE INSERT OR UPDATE OR DELETE ON public.notification_source_outbox FOR EACH ROW EXECUTE FUNCTION public.guard_notification_storage();
CREATE TRIGGER notification_audit_guard BEFORE INSERT OR UPDATE OR DELETE ON public.notification_audit_outbox FOR EACH ROW EXECUTE FUNCTION public.guard_notification_storage();

-- INVOKER guard의 current_user는 유지한다. 이 함수는 같은 범위의 보호 여부 bool만 반환한다.
CREATE FUNCTION public.notification_parent_is_protected(p_tenant text,p_workspace text,p_id text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF pg_trigger_depth()<1 THEN
    RAISE EXCEPTION 'NOTIFICATION_GUARD_ONLY' USING ERRCODE='42501';
  END IF;
  IF p_tenant IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
    OR p_workspace IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'') THEN
    RETURN false;
  END IF;
  RETURN EXISTS(SELECT 1 FROM public.notification_details d JOIN public.notifications n
    ON n.tenant_id=d.tenant_id AND n.notification_id=d.notification_id
    WHERE d.tenant_id=p_tenant AND d.notification_id=p_id AND n.workspace_id=p_workspace);
END $$;
ALTER FUNCTION public.notification_parent_is_protected(text,text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.notification_parent_is_protected(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notification_parent_is_protected(text,text,text) TO daon_app;

CREATE FUNCTION public.guard_notification_parent() RETURNS trigger LANGUAGE plpgsql
  SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF public.notification_parent_is_protected(OLD.tenant_id,OLD.workspace_id,OLD.notification_id) THEN
    IF TG_OP='DELETE' OR current_user<>'daon_notebook_delete' THEN RAISE EXCEPTION 'NOTIFICATION_IMMUTABLE' USING ERRCODE='55000'; END IF;
    IF (to_jsonb(OLD)-ARRAY['read_at','version']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['read_at','version'])
      OR OLD.read_at IS NOT NULL OR NEW.read_at IS NULL OR NEW.version<>OLD.version+1 THEN
      RAISE EXCEPTION 'NOTIFICATION_IMMUTABLE' USING ERRCODE='55000';
    END IF;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
ALTER FUNCTION public.guard_notification_parent() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_notification_parent() FROM PUBLIC,daon_app;
CREATE TRIGGER notification_parent_read_guard BEFORE UPDATE OR DELETE ON public.notifications FOR EACH ROW EXECUTE FUNCTION public.guard_notification_parent();

CREATE FUNCTION public.read_notification_inbox(p_id text,p_version integer,p_key text,p_fingerprint text,p_draft jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  parent public.notifications; detail public.notification_details; receipt public.idempotency_records;
  result jsonb;
BEGIN
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL OR p_id IS NULL
    OR p_version IS NULL OR p_version<1 OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$'
    OR p_fingerprint IS NULL OR p_fingerprint !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('notification.read|'||tenant||'|'||workspace||'|'||actor||'|'||p_key,0));
  SELECT * INTO parent FROM public.notifications WHERE tenant_id=tenant AND workspace_id=workspace
    AND notification_id=p_id AND recipient_id=actor FOR UPDATE;
  SELECT * INTO detail FROM public.notification_details WHERE tenant_id=tenant AND notification_id=p_id;
  IF parent.notification_id IS NULL OR detail.notification_id IS NULL
    OR NOT EXISTS(SELECT 1 FROM public.user_accounts WHERE tenant_id=tenant AND user_id=actor AND status='active') THEN
    RAISE EXCEPTION 'NOTIFICATION_UNAVAILABLE' USING ERRCODE='P0002';
  END IF;
  SELECT * INTO receipt FROM public.idempotency_records WHERE tenant_id=tenant AND workspace_id=workspace
    AND actor_id=actor AND operation='notification.read' AND idempotency_key=p_key;
  IF FOUND THEN
    IF receipt.request_fingerprint<>p_fingerprint OR receipt.result->>'notification_id' IS DISTINCT FROM p_id THEN
      RAISE EXCEPTION 'NOTIFICATION_IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
    END IF;
    RETURN receipt.result||jsonb_build_object('first_transition',false);
  END IF;
  IF parent.version<>p_version THEN RAISE EXCEPTION 'NOTIFICATION_VERSION_CONFLICT' USING ERRCODE='P0003'; END IF;
  IF parent.read_at IS NOT NULL THEN RAISE EXCEPTION 'NOTIFICATION_ALREADY_READ' USING ERRCODE='55000'; END IF;
  IF p_draft IS NULL OR p_draft->>'actor_id' IS DISTINCT FROM actor OR p_draft->>'actor_type' IS DISTINCT FROM 'user'
    OR p_draft->>'action' IS DISTINCT FROM 'notification.read'
    OR p_draft->'before' IS DISTINCT FROM jsonb_build_object('state','unread','version',parent.version)
    OR p_draft->'after' IS DISTINCT FROM jsonb_build_object('state','read','version',parent.version+1)
    OR p_draft->'metadata' IS DISTINCT FROM jsonb_build_object('source_event_id',parent.source_event_id) THEN
    RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514';
  END IF;
  UPDATE public.notifications SET read_at=(p_draft->>'occurred_at')::timestamptz,version=version+1
    WHERE tenant_id=tenant AND notification_id=p_id AND recipient_id=actor AND read_at IS NULL AND version=p_version RETURNING * INTO parent;
  IF NOT FOUND THEN RAISE EXCEPTION 'NOTIFICATION_VERSION_CONFLICT' USING ERRCODE='P0003'; END IF;
  result:=jsonb_build_object('notification_id',p_id,'read_at',parent.read_at,'version',parent.version);
  INSERT INTO public.idempotency_records(tenant_id,workspace_id,actor_id,operation,idempotency_key,request_fingerprint,result,status,expires_at)
    VALUES(tenant,workspace,actor,'notification.read',p_key,p_fingerprint,result,'completed','infinity'::timestamptz);
  INSERT INTO public.notification_audit_outbox(event_id,tenant_id,workspace_id,recipient_id,notification_id,source_event_id,action,draft,fingerprint)
    VALUES(p_draft->>'event_id',tenant,workspace,actor,p_id,parent.source_event_id,'notification.read',p_draft,
      encode(sha256(convert_to(p_draft::text,'UTF8')),'hex'));
  RETURN result||jsonb_build_object('first_transition',true);
END $$;
ALTER FUNCTION public.read_notification_inbox(text,integer,text,text,jsonb) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.read_notification_inbox(text,integer,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_notification_inbox(text,integer,text,text,jsonb) TO daon_app;

CREATE FUNCTION public.pending_notification_audits(p_limit integer) RETURNS TABLE(event_id text,draft jsonb,fingerprint text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'NOTIFICATION_BATCH_INVALID' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT a.event_id,a.draft,a.fingerprint FROM public.notification_audit_outbox a
    WHERE a.state='pending' AND a.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND a.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND a.recipient_id=nullif(current_setting('app.actor_id',true),'') ORDER BY a.event_id LIMIT p_limit;
END $$;
CREATE FUNCTION public.ack_notification_audit(p_event text,p_fingerprint text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  UPDATE public.notification_audit_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=version+1
    WHERE event_id=p_event AND fingerprint=p_fingerprint AND state='pending'
      AND tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND recipient_id=nullif(current_setting('app.actor_id',true),'');
  RETURN FOUND;
END $$;
ALTER FUNCTION public.pending_notification_audits(integer) OWNER TO daon_notebook_delete;
ALTER FUNCTION public.ack_notification_audit(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_notification_audits(integer),public.ack_notification_audit(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_notification_audits(integer),public.ack_notification_audit(text,text) TO daon_app;
REVOKE ALL ON FUNCTION public.guard_notification_storage() FROM PUBLIC,daon_app;
ALTER FUNCTION public.guard_notification_storage() OWNER TO daon_notebook_delete;

-- 실제 Studio 기록/원감사에서만 의도를 만든다. 호출자는 수신자/본문을 외부 입력으로 받지 않는다.
CREATE FUNCTION public.enqueue_studio_notification(p_origin text,p_notebook text,p_recipient text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  original public.audit_events; v_notification_output_version_id text; record_actor text; kind text; source_id text; inserted integer;
  v_notification_role text; v_notification_action text; v_notification_permission text; v_notification_allowed boolean;
  v_notification_requester text;
  v_notification_source record; v_notification_organization_policy jsonb; v_notification_workspace_policy jsonb;
  v_notification_previous_notebook text:=current_setting('app.notebook_id',true);
  v_notification_top_xid numeric:=pg_current_xact_id()::text::numeric;
  v_notification_record_xid numeric;
BEGIN
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='42501';
  END IF;
  -- 특별 snapshot의 기존 RLS context다. 성공 시 원래 범위를 복원하며 오류 시 statement rollback된다.
  PERFORM set_config('app.notebook_id',p_notebook,true);
  SELECT * INTO original FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.event_id=p_origin AND a.actor_id=actor AND a.outcome='succeeded'
    -- 이 SELECT에 보이는 미커밋 행은 현재 transaction의 행이다. 완료된 다른 transaction은 거부한다.
    -- xmin의 하위 xid도 허용하되 xid32 wrap을 현재 최상위 xid의 가까운 epoch로 복원한다.
    AND pg_xact_status((v_notification_top_xid-mod(v_notification_top_xid,4294967296)+a.xmin::text::numeric+
      CASE WHEN a.xmin::text::numeric-mod(v_notification_top_xid,4294967296)< -2147483648 THEN 4294967296
        WHEN a.xmin::text::numeric-mod(v_notification_top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8)='in progress';
  kind:=CASE original.action WHEN 'studio.review' THEN 'review' WHEN 'studio.approval_request' THEN 'approval'
    WHEN 'studio.approval' THEN 'approval' WHEN 'studio.delivery' THEN 'delivery' END;
  IF kind IS NULL OR original.target_type IS DISTINCT FROM (CASE original.action
    WHEN 'studio.review' THEN 'review_requests' WHEN 'studio.approval_request' THEN 'approval_requests'
    WHEN 'studio.approval' THEN 'approvals' WHEN 'studio.delivery' THEN 'deliveries' END) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  EXECUTE format('SELECT output_version_id,created_by,xmin::text::numeric FROM public.%I WHERE tenant_id=$1 AND workspace_id=$2 AND record_id=$3',original.target_type)
    INTO v_notification_output_version_id,record_actor,v_notification_record_xid USING tenant,workspace,original.target_id;
  -- 새 감사행만으로 이전 업무를 발행하지 않는다. 실제 기록과 정식 receipt도 같은 Tx여야 한다.
  -- 하위 savepoint의 xid는 원감사와 동일한 epoch 복원 규칙으로 검사한다.
  IF pg_xact_status((v_notification_top_xid-mod(v_notification_top_xid,4294967296)+v_notification_record_xid+
      CASE WHEN v_notification_record_xid-mod(v_notification_top_xid,4294967296)< -2147483648 THEN 4294967296
        WHEN v_notification_record_xid-mod(v_notification_top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8)
      IS DISTINCT FROM 'in progress'
    OR original.metadata->>'result_id' IS DISTINCT FROM original.target_id
    OR NOT EXISTS(SELECT 1 FROM public.idempotency_records r WHERE r.tenant_id=tenant AND r.workspace_id=workspace
      AND r.actor_id=actor AND r.operation=original.action AND r.status='completed'
      AND r.result->>'record_id'=original.target_id
      AND p_origin='audit-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||original.action||'|'||r.idempotency_key,'UTF8')),'hex'),1,32)
      AND original.target_id=(CASE original.action WHEN 'studio.review' THEN 'review'
        WHEN 'studio.approval_request' THEN 'approval-request' WHEN 'studio.approval' THEN 'approval'
        WHEN 'studio.delivery' THEN 'delivery' END)||'-'||substr(encode(sha256(convert_to(
          tenant||'|'||workspace||'|'||actor||'|'||original.action||'|'||r.idempotency_key,'UTF8')),'hex'),1,32)
      AND pg_xact_status((v_notification_top_xid-mod(v_notification_top_xid,4294967296)+r.xmin::text::numeric+
        CASE WHEN r.xmin::text::numeric-mod(v_notification_top_xid,4294967296)< -2147483648 THEN 4294967296
          WHEN r.xmin::text::numeric-mod(v_notification_top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8)='in progress') THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  v_notification_requester:=record_actor;
  IF original.action='studio.approval' THEN
    SELECT r.created_by INTO v_notification_requester FROM public.approvals a JOIN public.approval_requests r
      ON r.tenant_id=a.tenant_id AND r.workspace_id=a.workspace_id AND r.record_id=a.approval_request_id
      AND r.output_version_id=a.output_version_id
      WHERE a.tenant_id=tenant AND a.workspace_id=workspace AND a.record_id=original.target_id;
  END IF;
  IF v_notification_output_version_id IS NULL OR record_actor IS DISTINCT FROM actor
    OR v_notification_requester IS NULL
    OR (kind='delivery' AND p_recipient IS DISTINCT FROM actor)
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m JOIN public.identity_users u ON u.user_id=m.user_id
      JOIN public.user_accounts ua ON ua.tenant_id=m.tenant_id AND ua.user_id=m.user_id
      WHERE m.tenant_id=tenant AND m.user_id=p_recipient AND u.state='active')
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      WHERE o.tenant_id=tenant AND o.user_id=p_recipient AND o.state<>'active')
    OR NOT EXISTS(SELECT 1 FROM public.identity_auth_workspaces w WHERE w.tenant_id=tenant AND w.workspace_id=workspace
      AND (EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles t WHERE t.tenant_id=w.tenant_id
        AND t.user_id=p_recipient AND t.state='active' AND
          ((w.workspace_kind='personal' AND t.role='personal_owner') OR (w.workspace_kind='organization' AND t.role='organization_admin')))
        OR (NOT EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles t WHERE t.tenant_id=w.tenant_id AND t.user_id=p_recipient AND t.state='active')
          AND EXISTS(SELECT 1 FROM public.identity_auth_memberships m WHERE m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
            AND m.user_id=p_recipient AND m.state='active'))))
    OR EXISTS(SELECT 1 FROM public.evidence_references er JOIN public.source_versions sv
      ON sv.tenant_id=er.tenant_id AND sv.workspace_id=er.workspace_id AND sv.record_id=er.source_version_id
      JOIN public.identity_auth_source_access a ON a.tenant_id=sv.tenant_id AND a.workspace_id=sv.workspace_id
        AND a.source_version_id=sv.record_id AND a.user_id=p_recipient AND a.allowed=0
      WHERE er.tenant_id=tenant AND er.workspace_id=workspace AND er.output_version_id=v_notification_output_version_id)
    OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b JOIN public.notebooks n
      ON n.tenant_id=b.tenant_id AND n.workspace_id=b.workspace_id AND n.notebook_id=b.notebook_id
      WHERE b.tenant_id=tenant AND b.workspace_id=workspace AND b.notebook_id=p_notebook
      AND b.binding_kind='output_version' AND b.record_id=v_notification_output_version_id) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='42501';
  END IF;
  -- Python 후보 판정을 건너뛰고 함수를 직접 호출해도 같은 현재 역할/permission이 필요하다.
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN CASE
      WHEN (tr.role='personal_owner' AND w.workspace_kind='personal')
        OR (tr.role='organization_admin' AND w.workspace_kind='organization') THEN tr.role END
      ELSE m.role END INTO v_notification_role
    FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=p_recipient AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id AND m.user_id=p_recipient AND m.state='active'
    WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  v_notification_action:=CASE WHEN original.action='studio.delivery' THEN 'deliver'
    WHEN p_recipient=v_notification_requester THEN 'view' WHEN original.action='studio.review' THEN 'review' ELSE 'approve' END;
  IF v_notification_role IS NULL OR v_notification_role NOT IN ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR (v_notification_action='review' AND v_notification_role NOT IN ('personal_owner','organization_admin','workspace_admin','reviewer','approver'))
    OR (v_notification_action IN ('approve','deliver') AND v_notification_role NOT IN ('personal_owner','organization_admin','approver')) THEN
    RAISE EXCEPTION 'NOTIFICATION_ACTION_DENIED' USING ERRCODE='42501';
  END IF;
  FOR v_notification_permission IN SELECT unnest(CASE v_notification_action
    WHEN 'approve' THEN ARRAY['final_approval_external_delivery']
    WHEN 'deliver' THEN ARRAY['file_download_share','final_approval_external_delivery'] ELSE ARRAY[]::text[] END)
  LOOP
    -- AuthorizationService._effective_permission의 조직 잠금/deny→Workspace→조직 grant→역할 기본값 순서다.
    -- 이 두 Action에 허용된 세 역할은 위 permission 모두 기본 허용이다.
    SELECT CASE WHEN tr.permission IS NOT NULL AND (tr.locked<>0 OR tr.effect='deny') THEN tr.effect='grant'
        WHEN wr.permission IS NOT NULL THEN wr.effect='grant' WHEN tr.permission IS NOT NULL THEN true ELSE true END
      INTO v_notification_allowed FROM (SELECT 1) singleton
      LEFT JOIN public.identity_auth_tenant_permission_rules tr ON tr.tenant_id=tenant AND tr.permission=v_notification_permission
      LEFT JOIN public.identity_auth_workspace_permission_rules wr ON wr.tenant_id=tenant AND wr.workspace_id=workspace AND wr.permission=v_notification_permission;
    IF v_notification_allowed IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'NOTIFICATION_ACTION_DENIED' USING ERRCODE='42501';
    END IF;
  END LOOP;
  IF EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=tenant AND d.workspace_id=workspace
      AND d.notebook_id=p_notebook AND d.state IN ('accepted','deleting','completed'))
    OR EXISTS(SELECT 1 FROM public.evidence_references er
      LEFT JOIN public.source_versions sv ON sv.tenant_id=er.tenant_id AND sv.workspace_id=er.workspace_id AND sv.record_id=er.source_version_id
      LEFT JOIN public.sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id
      WHERE er.tenant_id=tenant AND er.workspace_id=workspace AND er.output_version_id=v_notification_output_version_id
      AND (sv.record_id IS NULL OR s.record_id IS NULL
        OR EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=tenant AND d.workspace_id=workspace AND d.source_id=s.record_id AND d.source_active=false)
        OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=tenant AND b.workspace_id=workspace
          AND b.notebook_id=p_notebook AND b.binding_kind='source' AND b.record_id=s.record_id AND b.version_id=sv.record_id)
        OR EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=tenant AND u.workspace_id=workspace
          AND u.notebook_id=p_notebook AND u.source_id=s.record_id AND u.source_version_id=sv.record_id))) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  -- 원caller·원감사·현재 역할 검증 뒤에만 실제 수신자의 snapshot RLS context를 적용한다.
  PERFORM set_config('app.actor_id',p_recipient,true);
  FOR v_notification_source IN SELECT sv.record_id,sv.source_id,sv.version,sv.canonical_json
    FROM public.evidence_references er JOIN public.source_versions sv
      ON sv.tenant_id=er.tenant_id AND sv.workspace_id=er.workspace_id AND sv.record_id=er.source_version_id
    WHERE er.tenant_id=tenant AND er.workspace_id=workspace AND er.output_version_id=v_notification_output_version_id
  LOOP
    IF v_notification_source.canonical_json->>'origin_kind'='website' THEN
      -- 기존 웹 snapshot 조회의 owner/현재성 조건. 일반 Source에는 owner 조건을 추가하지 않는다.
      IF NOT EXISTS(SELECT 1 FROM public.website_source_snapshots w JOIN public.sources s
          ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id
          JOIN public.notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id
          WHERE w.tenant_id=tenant AND w.workspace_id=workspace AND w.notebook_id=p_notebook
          AND w.source_version_id=v_notification_source.record_id AND w.created_by=p_recipient AND n.created_by=p_recipient
          AND s.state='ready'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.workspace_id=w.workspace_id
            AND newer.source_id=w.source_id AND newer.version>v_notification_source.version)) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    ELSIF v_notification_source.canonical_json->>'origin_kind'='mcp_open_law' THEN
      -- 서버 환경 활성화는 기존 Python gate에서만 검사한다. 이 함수는 저장된 자료 보호를 독립 검사한다.
      IF v_notification_source.canonical_json->>'connector_id' IS DISTINCT FROM 'mcp-open-law-go-kr'
        OR NOT EXISTS(SELECT 1 FROM public.open_law_source_snapshots w JOIN public.sources s
          ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id
          JOIN public.notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id
          JOIN public.workspace_connectors c ON c.tenant_id=w.tenant_id AND c.workspace_id=w.workspace_id AND c.connector_id=w.connector_id
          WHERE w.tenant_id=tenant AND w.workspace_id=workspace AND w.notebook_id=p_notebook
          AND w.snapshot_id=v_notification_source.canonical_json->>'snapshot_id'
          AND w.source_id=v_notification_source.source_id AND w.source_version_id=v_notification_source.record_id
          AND w.created_by=p_recipient AND n.created_by=p_recipient AND s.state='ready'
          AND w.raw_sha256=v_notification_source.canonical_json->>'raw_sha256'
          AND w.text_sha256=v_notification_source.canonical_json->>'text_sha256'
          AND c.status='connected' AND c.verification_json->>'protocol_version'='2025-11-25'
          AND c.verification_json->>'tool_schema_digest'='df8c707ca4e47b8e22645c409d9bfb6a8fbe3038dabe4eaa44d6ee74457a8d88'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.workspace_id=w.workspace_id
            AND newer.source_id=w.source_id AND newer.version>v_notification_source.version) FOR SHARE OF c) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
      SELECT p.canonical_json INTO v_notification_organization_policy
        FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        WHERE b.tenant_id=tenant AND b.organization_id=tenant AND b.workspace_id IS NULL AND b.scope_type='organization'
        AND b.active AND b.current AND p.state='active' AND p.organization_id=tenant AND p.workspace_id IS NULL AND p.scope_type='organization';
      SELECT p.canonical_json INTO v_notification_workspace_policy
        FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        WHERE b.tenant_id=tenant AND b.organization_id=tenant AND b.workspace_id=workspace AND b.scope_type='workspace'
        AND b.active AND b.current AND p.state='active' AND p.organization_id=tenant AND p.workspace_id=workspace AND p.scope_type='workspace';
      -- resolve_effective_payload: 둘 다 allow/public, 목적지/Provider 교집합, 최소 bytes, 더 높은 승인 역할.
      IF v_notification_organization_policy IS NULL OR v_notification_workspace_policy IS NULL
        OR v_notification_organization_policy->>'mode' IS DISTINCT FROM 'allow_approved_external'
        OR v_notification_workspace_policy->>'mode' IS DISTINCT FROM 'allow_approved_external'
        OR v_notification_organization_policy->>'classification' IS DISTINCT FROM 'public'
        OR v_notification_workspace_policy->>'classification' IS DISTINCT FROM 'public'
        OR (v_notification_organization_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
        OR (v_notification_workspace_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
        OR (v_notification_organization_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
        OR (v_notification_workspace_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
        OR coalesce((v_notification_organization_policy->>'max_bytes')::bigint,0)<=0
        OR coalesce((v_notification_workspace_policy->>'max_bytes')::bigint,0)<=0
        OR v_notification_organization_policy->>'required_approver' IS NULL
        OR v_notification_workspace_policy->>'required_approver' IS NULL
        OR v_notification_organization_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
        OR v_notification_workspace_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
        OR (('organization_admin' IN (v_notification_organization_policy->>'required_approver',v_notification_workspace_policy->>'required_approver'))
          AND v_notification_role NOT IN ('personal_owner','organization_admin'))
        OR v_notification_role NOT IN ('personal_owner','organization_admin','workspace_admin') THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    END IF;
  END LOOP;
  source_id:='studio-event-'||substr(encode(sha256(convert_to(p_origin||'|'||workspace,'UTF8')),'hex'),1,32);
  -- 기존 원장 RLS는 그대로 유지하며 제한 함수 내부에서 이 수신자 의도만 기록한다.
  PERFORM set_config('app.actor_id',p_recipient,true);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,recipient_id,source_store,
    origin_event_id,action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
    VALUES(tenant,workspace,source_id,p_recipient,'studio',p_origin,original.action,original.target_type,
      original.target_id,kind,'info',p_notebook,original.trace_id,original.policy_version,original.occurred_at)
    ON CONFLICT(tenant_id,workspace_id,source_event_id,recipient_id) DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF NOT EXISTS(SELECT 1 FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.source_event_id=source_id AND s.recipient_id=p_recipient AND s.origin_event_id=p_origin AND s.source_store='studio'
    AND s.action=original.action AND s.target_type=original.target_type AND s.target_id=original.target_id
    AND s.notebook_id=p_notebook AND s.trace_id=original.trace_id AND s.policy_version=original.policy_version) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_CONFLICT' USING ERRCODE='23505';
  END IF;
  PERFORM set_config('app.actor_id',actor,true);
  PERFORM set_config('app.notebook_id',coalesce(v_notification_previous_notebook,''),true);
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_studio_notification(text,text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_studio_notification(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_studio_notification(text,text,text) TO daon_app;

-- 문서/질문 완료의 제한 producer다. 원요청자·Notebook·자료는 실제 원장에서만 유도한다.
CREATE FUNCTION public.enqueue_completed_notification(p_origin text,p_notebook text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_tenant text:=nullif(current_setting('app.tenant_id',true),'');
  v_workspace text:=nullif(current_setting('app.workspace_id',true),'');
  v_actor text:=nullif(current_setting('app.actor_id',true),'');
  v_previous_notebook text:=current_setting('app.notebook_id',true);
  v_origin public.audit_events; v_run record; v_source record; v_role text;
  v_job public.studio_generation_jobs;
  v_versions text[]; v_store text; v_recipient text; v_event text; v_inserted integer;
  v_items jsonb; v_item jsonb;
  v_kind text:='run_state'; v_severity text:='info';
  v_org_policy jsonb; v_workspace_policy jsonb;
  v_top_xid numeric:=pg_current_xact_id()::text::numeric;
BEGIN
  IF v_tenant IS NULL OR v_workspace IS NULL OR v_actor IS NULL OR p_notebook IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='42501';
  END IF;
  SELECT * INTO v_origin FROM public.audit_events a
    WHERE a.tenant_id=v_tenant AND a.workspace_id=v_workspace AND a.event_id=p_origin
      AND a.actor_id=v_actor AND a.outcome='succeeded'
      AND pg_xact_status((v_top_xid-mod(v_top_xid,4294967296)+a.xmin::text::numeric+
        CASE WHEN a.xmin::text::numeric-mod(v_top_xid,4294967296)< -2147483648 THEN 4294967296
          WHEN a.xmin::text::numeric-mod(v_top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8)='in progress';
  IF v_origin.action='canon.transition' AND v_origin.target_type='ProcessingRun' THEN
    SELECT r.created_by,r.canonical_json->>'notebook_id' AS notebook_id,r.source_version_id,r.state AS result_state INTO v_run
      FROM public.processing_runs r LEFT JOIN public.understanding_results u ON u.tenant_id=r.tenant_id
        AND u.workspace_id=r.workspace_id AND u.processing_run_id=r.record_id AND u.source_version_id=r.source_version_id
      JOIN public.canon_state_transitions t ON t.tenant_id=r.tenant_id AND t.workspace_id=r.workspace_id
        AND t.entity_type='ProcessingRun' AND t.record_id=r.record_id AND t.transition_id=p_origin
        AND t.target_state=r.state AND t.transition_version=r.version AND t.actor_id=v_actor
      WHERE r.tenant_id=v_tenant AND r.workspace_id=v_workspace AND r.record_id=v_origin.target_id
        AND ((r.state='completed' AND u.canonical_json->>'status' IN ('ready','needs_review'))
          OR (r.state='failed'
            AND p_origin='tr-'||substr(encode(sha256(convert_to('ProcessingRun|'||r.record_id||'|failed|'||(r.version-1)::text,'UTF8')),'hex'),1,32)
            AND pg_xact_status((v_top_xid-mod(v_top_xid,4294967296)+r.xmin::text::numeric+
              CASE WHEN r.xmin::text::numeric-mod(v_top_xid,4294967296)< -2147483648 THEN 4294967296
                WHEN r.xmin::text::numeric-mod(v_top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8)='in progress'))
        AND v_origin.after_value->>'state'=r.state;
    IF NOT FOUND OR v_run.notebook_id IS DISTINCT FROM p_notebook THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    v_store:='document'; v_recipient:=v_run.created_by; v_versions:=ARRAY[v_run.source_version_id];
    IF v_run.result_state='failed' THEN v_kind:='run_failed'; v_severity:='warning'; END IF;
    SELECT jsonb_build_array(jsonb_build_object('origin','raw_source','context_item_id',sv.source_id,
      'source_id',sv.source_id,'source_version_id',sv.record_id,'digest_sha256',NULL)) INTO v_items
      FROM public.source_versions sv WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=v_run.source_version_id;
  ELSIF v_origin.action='canon.transition' AND v_origin.target_type='Run' AND v_origin.after_value->>'state'='failed' THEN
    SELECT r.created_by,s.canonical_json->'source_version_ids' AS source_versions,s.canonical_json->'items' AS source_items INTO v_run
      FROM public.runs r JOIN public.run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id
      JOIN public.scope_snapshots s ON s.tenant_id=rs.tenant_id AND s.workspace_id=rs.workspace_id AND s.record_id=rs.scope_snapshot_id
      JOIN public.knowledge_scopes k ON k.tenant_id=s.tenant_id AND k.workspace_id=s.workspace_id AND k.record_id=s.knowledge_scope_id
      JOIN public.notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id
        AND b.binding_kind='conversation_thread' AND b.record_id=r.conversation_id AND b.notebook_id=p_notebook AND b.created_by=r.created_by
      JOIN public.canon_state_transitions t ON t.tenant_id=r.tenant_id AND t.workspace_id=r.workspace_id
        AND t.entity_type='Run' AND t.record_id=r.record_id AND t.transition_id=p_origin
        AND t.target_state='failed' AND t.transition_version=r.version AND t.actor_id=v_actor AND t.reason_code='QUESTION_FAILED'
      JOIN public.canon_transition_attempts receipt ON receipt.tenant_id=t.tenant_id AND receipt.workspace_id=t.workspace_id
        AND receipt.entity_type='Run' AND receipt.record_id=r.record_id AND receipt.attempt_id=t.transition_id
        AND receipt.actor_id=v_actor AND receipt.outcome='succeeded' AND receipt.result_state='failed'
        AND receipt.result_version=r.version AND receipt.expected_version=r.version-1 AND receipt.safe_error_code IS NULL
      WHERE r.tenant_id=v_tenant AND r.workspace_id=v_workspace AND r.record_id=v_origin.target_id
        AND r.state='failed' AND r.created_by=v_actor
        AND p_origin='tr-'||substr(encode(sha256(convert_to(r.record_id||'|failed|'||(r.version-1)::text,'UTF8')),'hex'),1,32)
        AND v_origin.after_value=jsonb_build_object('state','failed','version',r.version)
        AND v_origin.metadata->>'reason_code'='QUESTION_FAILED'
        AND v_origin.trace_id=r.trace_id AND v_origin.policy_version=rs.canonical_json->>'user_policy_version'
        AND s.canonical_json->>'mode'=r.canonical_json->>'context_mode'
        AND ((r.canonical_json->>'context_mode'='raw_only'
            AND jsonb_array_length(s.canonical_json->'items') BETWEEN 1 AND 8
            AND s.canonical_json->'items'->0->>'source_id'=r.canonical_json->>'source_id'
            AND s.canonical_json->'items'->0->>'source_version_id'=r.canonical_json->>'source_version_id'
            AND (SELECT count(DISTINCT (x.value->>'source_id',x.value->>'source_version_id'))
              FROM jsonb_array_elements(s.canonical_json->'items') x(value))=jsonb_array_length(s.canonical_json->'items')
            AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.canonical_json->'items') x(value) WHERE NOT coalesce(
              jsonb_typeof(x.value->'source_id')='string' AND jsonb_typeof(x.value->'source_version_id')='string'
              AND x.value->>'source_id' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
              AND x.value->>'source_version_id' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
              AND x.value=jsonb_build_object('origin','raw_source','context_item_id',x.value->>'source_id',
                'source_id',x.value->>'source_id','source_version_id',x.value->>'source_version_id','digest_sha256',NULL),false)))
          OR (r.canonical_json->>'context_mode'='general_ungrounded'
            AND r.canonical_json->'source_id'='null'::jsonb AND r.canonical_json->'source_version_id'='null'::jsonb
            AND s.canonical_json->'source_version_ids'='[]'::jsonb AND s.canonical_json->'items'='[]'::jsonb
            AND rs.canonical_json->'authority'='[]'::jsonb))
        AND jsonb_typeof(s.canonical_json->'source_version_ids')='array' AND jsonb_typeof(s.canonical_json->'items')='array'
        AND s.canonical_json->'source_version_ids'=(SELECT coalesce(jsonb_agg(x.value->'source_version_id' ORDER BY x.ordinality),'[]'::jsonb)
          FROM jsonb_array_elements(s.canonical_json->'items') WITH ORDINALITY x(value,ordinality))
        AND k.record_id='scope-'||substr(encode(sha256(convert_to(CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
          THEN 'general_ungrounded' ELSE 'raw_only|'||(SELECT string_agg(x.value,'|' ORDER BY x.ordinality)
            FROM jsonb_array_elements_text(s.canonical_json->'source_version_ids') WITH ORDINALITY x(value,ordinality)) END,'UTF8')),'hex'),1,32)
        AND k.canonical_json=jsonb_build_object('mode',r.canonical_json->>'context_mode',
          'source_version_ids',s.canonical_json->'source_version_ids','items',s.canonical_json->'items')
        AND s.canonical_json=k.canonical_json||jsonb_build_object('knowledge_scope_id',k.record_id)
        AND rs.canonical_json->>'knowledge_scope_id'=k.record_id
        AND rs.canonical_json->'source_version_ids'=s.canonical_json->'source_version_ids'
        AND rs.canonical_json->'authority'=CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
          THEN '[]'::jsonb ELSE '["source"]'::jsonb END
        AND rs.canonical_json->>'prompt_version'=CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
          THEN 'general-conversation-v1' ELSE 'cp3-grounded-question-v1' END
        AND rs.canonical_json->>'tool_version'=CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
          THEN 'provider-chat-v1' ELSE 'document-index-v1' END
        AND rs.canonical_json->'frozen_routing_context'=r.canonical_json->'frozen_routing_context'
        AND r.xmin=t.xmin AND t.xmin=receipt.xmin
        AND receipt.xmin=(SELECT a.xmin FROM public.audit_events a WHERE a.tenant_id=v_tenant
          AND a.workspace_id=v_workspace AND a.event_id=p_origin)
        AND pg_xact_status((v_top_xid-mod(v_top_xid,4294967296)+r.xmin::text::numeric+
          CASE WHEN r.xmin::text::numeric-mod(v_top_xid,4294967296)< -2147483648 THEN 4294967296
            WHEN r.xmin::text::numeric-mod(v_top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8)='in progress';
    IF NOT FOUND OR jsonb_typeof(v_run.source_versions) IS DISTINCT FROM 'array'
      OR jsonb_typeof(v_run.source_items) IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    v_store:='question'; v_recipient:=v_run.created_by; v_items:=v_run.source_items;
    v_kind:='run_failed'; v_severity:='warning';
    SELECT coalesce(array_agg(x),ARRAY[]::text[]) INTO v_versions FROM jsonb_array_elements_text(v_run.source_versions) x;
  ELSIF v_origin.action='question.answer' AND v_origin.target_type='Run' THEN
    SELECT r.created_by,s.canonical_json->'source_version_ids' AS source_versions,s.canonical_json->'items' AS source_items INTO v_run
      FROM public.runs r JOIN public.run_results rr ON rr.tenant_id=r.tenant_id AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id
      JOIN public.run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id
      JOIN public.scope_snapshots s ON s.tenant_id=rs.tenant_id AND s.workspace_id=rs.workspace_id AND s.record_id=rs.scope_snapshot_id
      JOIN public.notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id
        AND b.binding_kind='conversation_thread' AND b.record_id=r.conversation_id AND b.notebook_id=p_notebook
        AND b.created_by=r.created_by
      WHERE r.tenant_id=v_tenant AND r.workspace_id=v_workspace AND r.record_id=v_origin.target_id
        AND r.state='completed' AND r.created_by=v_actor
        -- 정식 Run 감사 ID와 실제 완료 업무의 미commit Tx를 모두 검사한다.
        AND p_origin='audit-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
        AND pg_xact_status((v_top_xid-mod(v_top_xid,4294967296)+r.xmin::text::numeric+
          CASE WHEN r.xmin::text::numeric-mod(v_top_xid,4294967296)< -2147483648 THEN 4294967296
            WHEN r.xmin::text::numeric-mod(v_top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8)='in progress'
        AND jsonb_typeof(rr.canonical_json->'insufficient')='boolean'
        AND s.canonical_json->'source_version_ids'=v_origin.metadata->'source_version_ids';
    IF NOT FOUND OR jsonb_typeof(v_run.source_versions) IS DISTINCT FROM 'array'
      OR jsonb_typeof(v_run.source_items) IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    v_store:='question'; v_recipient:=v_run.created_by;
    v_items:=v_run.source_items;
    SELECT coalesce(array_agg(x),ARRAY[]::text[]) INTO v_versions FROM jsonb_array_elements_text(v_run.source_versions) x;
  ELSIF v_origin.action='studio.generation.finish' AND v_origin.target_type='StudioGenerationJob' THEN
    -- 실제 최종 UPDATE와 정식 원감사를 같은 Tx/Job/version/state로 결속한다. 과거 Job 재발행은 거부한다.
    SELECT j.* INTO v_job FROM public.studio_generation_jobs j
      WHERE j.tenant_id=v_tenant AND j.workspace_id=v_workspace AND j.job_id=v_origin.target_id
        AND j.actor_id=v_actor AND j.state IN ('completed','failed','unavailable')
        AND j.lease_owner IS NULL AND j.lease_until IS NULL AND j.completed_at IS NOT NULL AND j.version>1
        AND v_origin.trace_id=j.trace_id AND v_origin.policy_version=j.policy_version
        AND v_origin.after_value=jsonb_build_object('state',j.state,'version',j.version)
        AND p_origin='audit-studio-job-finish-'||substr(encode(sha256(convert_to(
          v_tenant||'|'||v_workspace||'|'||j.job_id||'|'||j.version::text||'|'||j.state,'UTF8')),'hex'),1,32)
        AND pg_xact_status((v_top_xid-mod(v_top_xid,4294967296)+j.xmin::text::numeric+
          CASE WHEN j.xmin::text::numeric-mod(v_top_xid,4294967296)< -2147483648 THEN 4294967296
            WHEN j.xmin::text::numeric-mod(v_top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8)='in progress';
    IF NOT FOUND OR v_job.request_json->>'notebook_id' IS DISTINCT FROM p_notebook
      OR jsonb_typeof(v_job.request_json->'source_version_ids') IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    IF jsonb_array_length(v_job.request_json->'source_version_ids')=0
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_job.request_json->'source_version_ids') x WHERE jsonb_typeof(x)<>'string') THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    v_store:='studio'; v_recipient:=v_job.actor_id;
    SELECT array_agg(x.version_id ORDER BY x.ordinality),jsonb_agg(jsonb_build_object(
        'origin','raw_source','context_item_id',sv.source_id,'source_id',sv.source_id,
        'source_version_id',sv.record_id,'digest_sha256',NULL) ORDER BY x.ordinality)
      INTO v_versions,v_items
      FROM jsonb_array_elements_text(v_job.request_json->'source_version_ids') WITH ORDINALITY x(version_id,ordinality)
      LEFT JOIN public.source_versions sv ON sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=x.version_id;
    IF v_job.request_json->>'source_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.source_versions sv
        WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=ANY(v_versions)
          AND sv.source_id=v_job.request_json->>'source_id') THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    IF v_job.state='completed' THEN
      IF NOT EXISTS(SELECT 1 FROM public.output_versions ov JOIN public.studio_outputs so
          ON so.tenant_id=ov.tenant_id AND so.workspace_id=ov.workspace_id AND so.record_id=ov.studio_output_id
          JOIN public.notebook_bindings b ON b.tenant_id=ov.tenant_id AND b.workspace_id=ov.workspace_id
            AND b.binding_kind='output_version' AND b.record_id=ov.record_id AND b.notebook_id=p_notebook
          WHERE ov.tenant_id=v_tenant AND ov.workspace_id=v_workspace AND ov.record_id=v_job.output_version_id
            AND so.record_id=v_job.studio_output_id
            AND so.record_id='output-'||substr(encode(sha256(convert_to(v_tenant||'|'||v_workspace||'|'||v_actor||
              '|studio.generation.create|'||v_job.idempotency_key,'UTF8')),'hex'),1,32)
            AND ov.record_id='output-version-'||substr(encode(sha256(convert_to(v_tenant||'|'||v_workspace||'|'||v_actor||
              '|studio.generation.create|'||v_job.idempotency_key,'UTF8')),'hex'),1,32))
        OR EXISTS(SELECT 1 FROM public.evidence_references er WHERE er.tenant_id=v_tenant AND er.workspace_id=v_workspace
          AND er.output_version_id=v_job.output_version_id AND NOT (er.source_version_id=ANY(v_versions))) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
      END IF;
    ELSE
      v_kind:='run_failed'; v_severity:='warning';
    END IF;
  ELSE
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  IF v_recipient IS NULL OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id
      WHERE m.tenant_id=v_tenant AND m.user_id=v_recipient AND u.state='active')
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o WHERE o.tenant_id=v_tenant AND o.user_id=v_recipient AND o.state<>'active')
    OR NOT EXISTS(SELECT 1 FROM public.notebooks n WHERE n.tenant_id=v_tenant AND n.workspace_id=v_workspace AND n.notebook_id=p_notebook)
    OR EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=v_tenant AND d.workspace_id=v_workspace
      AND d.notebook_id=p_notebook AND d.state IN ('accepted','deleting','completed')) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  -- VIEW의 기존 실제 tenant-role/Workspace-role 교집합. 일반 Notebook은 owner 전용으로 좁히지 않는다.
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN CASE
      WHEN (tr.role='personal_owner' AND w.workspace_kind='personal')
        OR (tr.role='organization_admin' AND w.workspace_kind='organization') THEN tr.role END
      ELSE m.role END INTO v_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=v_recipient AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id AND m.user_id=v_recipient AND m.state='active'
    WHERE w.tenant_id=v_tenant AND w.workspace_id=v_workspace;
  IF v_role IS NULL OR v_role NOT IN ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR (v_store='question' AND v_kind='run_failed' AND v_role='viewer') THEN
    RAISE EXCEPTION 'NOTIFICATION_ACTION_DENIED' USING ERRCODE='42501';
  END IF;
  IF jsonb_typeof(v_items) IS DISTINCT FROM 'array' OR
    (SELECT coalesce(jsonb_agg(x.value->'source_version_id' ORDER BY x.ordinality),'[]'::jsonb)
      FROM jsonb_array_elements(v_items) WITH ORDINALITY x(value,ordinality)) IS DISTINCT FROM to_jsonb(v_versions) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(v_items)
  LOOP
    IF jsonb_typeof(v_item) IS DISTINCT FROM 'object' THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(v_item))<>5
      OR NOT (v_item ?& ARRAY['origin','context_item_id','source_id','source_version_id','digest_sha256'])
      OR coalesce(v_item->>'origin' NOT IN ('raw_source','daon_knowledge'),true)
      OR v_item->>'context_item_id' IS NULL OR v_item->>'context_item_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      OR v_item->>'source_id' IS NULL OR v_item->>'source_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      OR v_item->>'source_version_id' IS NULL OR v_item->>'source_version_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      OR NOT EXISTS(SELECT 1 FROM public.source_versions sv WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace
        AND sv.record_id=v_item->>'source_version_id' AND sv.source_id=v_item->>'source_id') THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    IF v_item->>'origin'='raw_source' THEN
      IF v_item->>'context_item_id' IS DISTINCT FROM v_item->>'source_id'
        OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=v_tenant AND b.workspace_id=v_workspace
          AND b.notebook_id=p_notebook AND b.binding_kind='source' AND b.record_id=v_item->>'source_id'
          AND b.version_id=v_item->>'source_version_id') THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    ELSE
      -- 실제 knowledge binding과 불변 snapshot 항목을 대조한다. raw Source binding을 합성하지 않는다.
      IF jsonb_typeof(v_item->'digest_sha256') IS DISTINCT FROM 'string'
        OR v_item->>'digest_sha256' !~ '^[0-9a-f]{64}$'
        OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b JOIN public.scope_snapshots k
          ON k.tenant_id=b.tenant_id AND k.workspace_id=b.workspace_id AND k.record_id=b.record_id
          JOIN public.knowledge_scopes ks ON ks.tenant_id=k.tenant_id AND ks.workspace_id=k.workspace_id AND ks.record_id=k.knowledge_scope_id
          WHERE b.tenant_id=v_tenant AND b.workspace_id=v_workspace AND b.notebook_id=p_notebook
          AND b.binding_kind='knowledge_context' AND b.version_id IS NULL AND b.record_id=v_item->>'context_item_id'
          AND EXISTS(SELECT 1 FROM jsonb_array_elements(k.canonical_json->'items') x WHERE x=v_item)) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    END IF;
  END LOOP;
  IF (SELECT count(*) FROM public.source_versions sv JOIN public.sources s
        ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id
      WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=ANY(v_versions))<>cardinality(v_versions)
    OR EXISTS(SELECT 1 FROM public.source_versions sv WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=ANY(v_versions)
      AND (EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=v_tenant AND u.workspace_id=v_workspace
          AND u.notebook_id=p_notebook AND u.source_id=sv.source_id AND u.source_version_id=sv.record_id)
        OR EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=v_tenant AND d.workspace_id=v_workspace AND d.source_id=sv.source_id AND d.source_active=false)
        OR EXISTS(SELECT 1 FROM public.identity_auth_source_access a WHERE a.tenant_id=v_tenant AND a.workspace_id=v_workspace
          AND a.source_version_id=sv.record_id AND a.user_id=v_recipient AND a.allowed=0))) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.actor_id',v_recipient,true);
  PERFORM set_config('app.notebook_id',p_notebook,true);
  FOR v_source IN SELECT sv.record_id,sv.source_id,sv.version,sv.canonical_json FROM public.source_versions sv
    WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=ANY(v_versions)
  LOOP
    IF v_source.canonical_json->>'origin_kind'='website' THEN
      IF NOT EXISTS(SELECT 1 FROM public.website_source_snapshots w JOIN public.sources s
          ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id
          JOIN public.notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id
          WHERE w.tenant_id=v_tenant AND w.workspace_id=v_workspace AND w.notebook_id=p_notebook
          AND w.source_version_id=v_source.record_id AND w.created_by=v_recipient AND n.created_by=v_recipient AND s.state='ready'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.workspace_id=w.workspace_id
            AND newer.source_id=w.source_id AND newer.version>v_source.version)) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    ELSIF v_source.canonical_json->>'origin_kind'='mcp_open_law' THEN
      IF v_source.canonical_json->>'connector_id' IS DISTINCT FROM 'mcp-open-law-go-kr'
        OR NOT EXISTS(SELECT 1 FROM public.open_law_source_snapshots w JOIN public.sources s
          ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id
          JOIN public.notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id
          JOIN public.workspace_connectors c ON c.tenant_id=w.tenant_id AND c.workspace_id=w.workspace_id AND c.connector_id=w.connector_id
          WHERE w.tenant_id=v_tenant AND w.workspace_id=v_workspace AND w.notebook_id=p_notebook
          AND w.snapshot_id=v_source.canonical_json->>'snapshot_id' AND w.source_id=v_source.source_id AND w.source_version_id=v_source.record_id
          AND w.created_by=v_recipient AND n.created_by=v_recipient AND s.state='ready'
          AND w.raw_sha256=v_source.canonical_json->>'raw_sha256' AND w.text_sha256=v_source.canonical_json->>'text_sha256'
          AND c.status='connected' AND c.verification_json->>'protocol_version'='2025-11-25'
          AND c.verification_json->>'tool_schema_digest'='df8c707ca4e47b8e22645c409d9bfb6a8fbe3038dabe4eaa44d6ee74457a8d88'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.workspace_id=w.workspace_id
            AND newer.source_id=w.source_id AND newer.version>v_source.version) FOR SHARE OF c) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
      SELECT p.canonical_json INTO v_org_policy FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p
        ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        WHERE b.tenant_id=v_tenant AND b.organization_id=v_tenant AND b.workspace_id IS NULL AND b.scope_type='organization'
          AND b.active AND b.current AND p.state='active' AND p.organization_id=v_tenant AND p.workspace_id IS NULL AND p.scope_type='organization';
      SELECT p.canonical_json INTO v_workspace_policy FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p
        ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        WHERE b.tenant_id=v_tenant AND b.organization_id=v_tenant AND b.workspace_id=v_workspace AND b.scope_type='workspace'
          AND b.active AND b.current AND p.state='active' AND p.organization_id=v_tenant AND p.workspace_id=v_workspace AND p.scope_type='workspace';
      IF v_org_policy IS NULL OR v_workspace_policy IS NULL
        OR v_org_policy->>'mode' IS DISTINCT FROM 'allow_approved_external' OR v_workspace_policy->>'mode' IS DISTINCT FROM 'allow_approved_external'
        OR v_org_policy->>'classification' IS DISTINCT FROM 'public' OR v_workspace_policy->>'classification' IS DISTINCT FROM 'public'
        OR (v_org_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
        OR (v_workspace_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
        OR (v_org_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
        OR (v_workspace_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
        OR coalesce((v_org_policy->>'max_bytes')::bigint,0)<=0 OR coalesce((v_workspace_policy->>'max_bytes')::bigint,0)<=0
        OR v_org_policy->>'required_approver' IS NULL OR v_workspace_policy->>'required_approver' IS NULL
        OR v_org_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
        OR v_workspace_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
        OR (('organization_admin' IN (v_org_policy->>'required_approver',v_workspace_policy->>'required_approver'))
          AND v_role NOT IN ('personal_owner','organization_admin'))
        OR v_role NOT IN ('personal_owner','organization_admin','workspace_admin') THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    END IF;
  END LOOP;
  v_event:=v_store||'-event-'||substr(encode(sha256(convert_to(p_origin||'|'||v_workspace,'UTF8')),'hex'),1,32);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,recipient_id,source_store,
    origin_event_id,action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
    VALUES(v_tenant,v_workspace,v_event,v_recipient,v_store,p_origin,v_origin.action,v_origin.target_type,v_origin.target_id,
      v_kind,v_severity,p_notebook,v_origin.trace_id,v_origin.policy_version,v_origin.occurred_at)
    ON CONFLICT(tenant_id,workspace_id,source_event_id,recipient_id) DO NOTHING;
  GET DIAGNOSTICS v_inserted=ROW_COUNT;
  IF NOT EXISTS(SELECT 1 FROM public.notification_source_outbox s WHERE s.tenant_id=v_tenant AND s.workspace_id=v_workspace
      AND s.source_event_id=v_event AND s.recipient_id=v_recipient AND s.source_store=v_store AND s.origin_event_id=p_origin
      AND s.action=v_origin.action AND s.target_type=v_origin.target_type AND s.target_id=v_origin.target_id
      AND s.kind=v_kind AND s.severity=v_severity AND s.notebook_id=p_notebook
      AND s.trace_id=v_origin.trace_id AND s.policy_version=v_origin.policy_version AND s.occurred_at=v_origin.occurred_at) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_CONFLICT' USING ERRCODE='23505';
  END IF;
  PERFORM set_config('app.actor_id',v_actor,true);
  PERFORM set_config('app.notebook_id',coalesce(v_previous_notebook,''),true);
  RETURN v_inserted=1;
END $$;
ALTER FUNCTION public.enqueue_completed_notification(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_completed_notification(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_completed_notification(text,text) TO daon_app;

CREATE FUNCTION public.current_completed_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_tenant text:=nullif(current_setting('app.tenant_id',true),'');
  v_workspace text:=nullif(current_setting('app.workspace_id',true),'');
  v_actor text:=nullif(current_setting('app.actor_id',true),'');
  p_origin text; p_notebook text; v_pending public.notification_source_outbox; v_result jsonb;
  v_previous_actor text:=current_setting('app.actor_id',true);
  v_previous_notebook text:=current_setting('app.notebook_id',true);
  v_origin public.audit_events; v_run record; v_source record; v_role text;
  v_versions text[]; v_store text; v_recipient text; v_event text; v_inserted integer;
  v_items jsonb; v_item jsonb;
  v_kind text:='run_state'; v_severity text:='info';
  v_org_policy jsonb; v_workspace_policy jsonb;
  v_job public.studio_generation_jobs;
BEGIN
  IF v_tenant IS NULL OR v_workspace IS NULL OR v_actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_pending FROM public.notification_source_outbox s
    WHERE s.tenant_id=v_tenant AND s.workspace_id=v_workspace AND s.recipient_id=v_actor
      AND s.source_event_id=p_source AND s.state='pending'
      AND (s.source_store IN ('document','question') OR (s.source_store='studio'
        AND s.action='studio.generation.finish' AND s.target_type='StudioGenerationJob'
        AND ((s.kind='run_failed' AND s.severity='warning') OR (s.kind='run_state' AND s.severity='info'))));
  IF NOT FOUND THEN RETURN NULL; END IF;
  p_origin:=v_pending.origin_event_id; p_notebook:=v_pending.notebook_id;
  SELECT * INTO v_origin FROM public.audit_events a
    WHERE a.tenant_id=v_tenant AND a.workspace_id=v_workspace AND a.event_id=p_origin AND a.outcome='succeeded';
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF (v_origin.action,v_origin.target_type,v_origin.target_id,v_origin.trace_id,v_origin.policy_version,v_origin.occurred_at)
    IS DISTINCT FROM (v_pending.action,v_pending.target_type,v_pending.target_id,v_pending.trace_id,v_pending.policy_version,v_pending.occurred_at) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  -- 원처리자는 원감사, 현재 수신자는 원업무 requester다. 둘을 현재 세션으로 대체하지 않는다.
  v_actor:=v_origin.actor_id;
  IF v_origin.action='canon.transition' AND v_origin.target_type='ProcessingRun' THEN
    SELECT r.created_by,r.canonical_json->>'notebook_id' AS notebook_id,r.source_version_id,
      CASE WHEN r.state='failed' THEN 'failed' ELSE u.canonical_json->>'status' END AS result_status INTO v_run
      FROM public.processing_runs r LEFT JOIN public.understanding_results u ON u.tenant_id=r.tenant_id
        AND u.workspace_id=r.workspace_id AND u.processing_run_id=r.record_id AND u.source_version_id=r.source_version_id
      JOIN public.canon_state_transitions t ON t.tenant_id=r.tenant_id AND t.workspace_id=r.workspace_id
        AND t.entity_type='ProcessingRun' AND t.record_id=r.record_id AND t.transition_id=p_origin
        AND t.target_state=r.state AND t.transition_version=r.version AND t.actor_id=v_actor
      WHERE r.tenant_id=v_tenant AND r.workspace_id=v_workspace AND r.record_id=v_origin.target_id
        AND ((r.state='completed' AND u.canonical_json->>'status' IN ('ready','needs_review'))
          OR (r.state='failed' AND p_origin='tr-'||substr(encode(sha256(convert_to(
            'ProcessingRun|'||r.record_id||'|failed|'||(r.version-1)::text,'UTF8')),'hex'),1,32)))
        AND v_origin.after_value->>'state'=r.state;
    IF NOT FOUND OR v_run.notebook_id IS DISTINCT FROM p_notebook THEN RETURN NULL; END IF;
    v_store:='document'; v_recipient:=v_run.created_by; v_versions:=ARRAY[v_run.source_version_id];
    IF v_run.result_status='failed' THEN v_kind:='run_failed'; v_severity:='warning'; END IF;
    SELECT jsonb_build_array(jsonb_build_object('origin','raw_source','context_item_id',sv.source_id,
      'source_id',sv.source_id,'source_version_id',sv.record_id,'digest_sha256',NULL)) INTO v_items
      FROM public.source_versions sv WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=v_run.source_version_id;
  ELSIF v_origin.action='canon.transition' AND v_origin.target_type='Run' AND v_origin.after_value->>'state'='failed' THEN
    -- commit된 원 실패 사건의 계보만 읽는다. 과거 사건에 현재 transaction xmin을 요구하지 않는다.
    SELECT r.created_by,p_notebook::text AS notebook_id,s.canonical_json->'source_version_ids' AS source_versions,
      s.canonical_json->'items' AS source_items,NULL::jsonb AS insufficient INTO v_run
    FROM public.runs r
    JOIN public.conversations c ON c.tenant_id=r.tenant_id AND c.workspace_id=r.workspace_id AND c.record_id=r.conversation_id
    JOIN public.notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id
      AND b.binding_kind='conversation_thread' AND b.record_id=c.record_id AND b.notebook_id=p_notebook AND b.created_by=r.created_by
    JOIN public.run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id
    JOIN public.scope_snapshots s ON s.tenant_id=rs.tenant_id AND s.workspace_id=rs.workspace_id AND s.record_id=rs.scope_snapshot_id
    JOIN public.knowledge_scopes k ON k.tenant_id=s.tenant_id AND k.workspace_id=s.workspace_id AND k.record_id=s.knowledge_scope_id
    JOIN public.canon_state_transitions t ON t.tenant_id=r.tenant_id AND t.workspace_id=r.workspace_id
      AND t.entity_type='Run' AND t.record_id=r.record_id AND t.transition_id=p_origin
    JOIN public.canon_transition_attempts receipt ON receipt.tenant_id=t.tenant_id AND receipt.workspace_id=t.workspace_id
      AND receipt.entity_type='Run' AND receipt.record_id=r.record_id AND receipt.attempt_id=t.transition_id
    JOIN public.audit_events a ON a.tenant_id=t.tenant_id AND a.workspace_id=t.workspace_id AND a.event_id=t.transition_id
    JOIN public.egress_decisions e ON e.tenant_id=r.tenant_id AND e.workspace_id=r.workspace_id AND e.run_id=r.record_id
      AND e.record_id=rs.canonical_json->>'egress_decision_id'
    JOIN public.routing_decisions d ON d.tenant_id=e.tenant_id AND d.workspace_id=e.workspace_id AND d.egress_decision_id=e.record_id
      AND d.run_id=r.record_id
    JOIN public.audit_events ea ON ea.tenant_id=e.tenant_id AND ea.workspace_id=e.workspace_id
      AND ea.event_id='audit-egress-'||substr(encode(sha256(convert_to(r.record_id||'|'||e.record_id,'UTF8')),'hex'),1,32)
    WHERE r.tenant_id=v_tenant AND r.workspace_id=v_workspace AND r.record_id=v_origin.target_id
      AND r.state='failed' AND r.version>1 AND r.created_by=v_actor
      AND r.canonical_json->>'context_mode' IN ('raw_only','general_ungrounded')
      AND (r.canonical_json->>'context_mode'<>'general_ungrounded'
        OR (r.canonical_json->'source_id'='null'::jsonb AND r.canonical_json->'source_version_id'='null'::jsonb))
      AND r.canonical_json->>'request_fingerprint' ~ '^sha256:[0-9a-f]{64}$'
      AND jsonb_typeof(r.canonical_json->'frozen_routing_context')='object'
      AND r.canonical_json->'frozen_routing_context'->>'fingerprint' ~ '^sha256:[0-9a-f]{64}$'
      AND r.canonical_json->'frozen_routing_context'->>'payload_fingerprint' ~ '^sha256:[0-9a-f]{64}$'
      AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['provider_kind','connection_id','provider_code','model_id','credential_version',
          'default_version','catalog_version','routing_owner','daon_fallback_allowed']) field
        WHERE (r.canonical_json->field) IS DISTINCT FROM (r.canonical_json->'frozen_routing_context'->field))
      AND p_origin='tr-'||substr(encode(sha256(convert_to(r.record_id||'|failed|'||(r.version-1)::text,'UTF8')),'hex'),1,32)
      AND t.target_state='failed' AND t.transition_version=r.version AND t.actor_id=r.created_by AND t.trace_id=r.trace_id
      AND t.source_state IN ('accepted','planning','retrieving','generating','validating') AND t.reason_code='QUESTION_FAILED'
      AND receipt.actor_id=t.actor_id AND receipt.trace_id=t.trace_id AND receipt.policy_version=t.policy_version
      AND receipt.source_state=t.source_state AND receipt.target_state=t.target_state AND receipt.reason_code=t.reason_code
      AND receipt.outcome='succeeded' AND receipt.result_state='failed' AND receipt.result_version=r.version
      AND receipt.expected_version=r.version-1 AND receipt.current_version=receipt.expected_version AND receipt.safe_error_code IS NULL
      AND a.actor_id=t.actor_id AND a.action='canon.transition' AND a.target_type='Run' AND a.target_id=r.record_id
      AND a.outcome='succeeded' AND a.trace_id=r.trace_id AND a.policy_version=t.policy_version
      AND a.before_value=jsonb_build_object('state',t.source_state,'version',r.version-1)
      AND a.after_value=jsonb_build_object('state','failed','version',r.version)
      AND a.metadata=jsonb_build_object('attempt_id',t.transition_id,'reason_code','QUESTION_FAILED')
      AND c.record_id='conversation-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
      AND c.created_by=r.created_by AND c.trace_id=r.trace_id
      AND c.canonical_json=jsonb_build_object('current_run_id',r.record_id,
        'current_run_result_id','run-result-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32))
      AND rs.record_id='run-snapshot-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
      AND s.record_id='scope-snapshot-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
      AND k.record_id='scope-'||substr(encode(sha256(convert_to(CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
        THEN 'general_ungrounded' ELSE 'raw_only|'||(SELECT string_agg(x.value,'|' ORDER BY x.ordinality)
          FROM jsonb_array_elements_text(s.canonical_json->'source_version_ids') WITH ORDINALITY x(value,ordinality)) END,'UTF8')),'hex'),1,32)
      AND rs.created_by=r.created_by AND rs.trace_id=r.trace_id AND s.created_by=r.created_by AND s.trace_id=r.trace_id
      AND k.canonical_json=jsonb_build_object('mode',r.canonical_json->>'context_mode',
        'source_version_ids',s.canonical_json->'source_version_ids','items',s.canonical_json->'items')
      AND jsonb_typeof(s.canonical_json->'source_version_ids')='array'
      AND jsonb_typeof(s.canonical_json->'items')='array'
      AND s.canonical_json->'source_version_ids'=(SELECT coalesce(jsonb_agg(x.value->'source_version_id' ORDER BY x.ordinality),'[]'::jsonb)
        FROM jsonb_array_elements(s.canonical_json->'items') WITH ORDINALITY x(value,ordinality))
      AND ((r.canonical_json->>'context_mode'='general_ungrounded'
          AND s.canonical_json->'source_version_ids'='[]'::jsonb AND s.canonical_json->'items'='[]'::jsonb)
        OR (r.canonical_json->>'context_mode'='raw_only' AND jsonb_array_length(s.canonical_json->'items') BETWEEN 1 AND 8
          AND s.canonical_json->'items'->0->>'source_id'=r.canonical_json->>'source_id'
          AND s.canonical_json->'items'->0->>'source_version_id'=r.canonical_json->>'source_version_id'
          AND (SELECT count(DISTINCT (x.value->>'source_id',x.value->>'source_version_id'))
            FROM jsonb_array_elements(s.canonical_json->'items') x(value))=jsonb_array_length(s.canonical_json->'items')
          AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.canonical_json->'items') x(value) WHERE NOT coalesce(
            jsonb_typeof(x.value->'source_id')='string' AND jsonb_typeof(x.value->'source_version_id')='string'
            AND x.value->>'source_id' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            AND x.value->>'source_version_id' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            AND x.value=jsonb_build_object('origin','raw_source','context_item_id',x.value->>'source_id',
              'source_id',x.value->>'source_id','source_version_id',x.value->>'source_version_id','digest_sha256',NULL),false))))
      AND s.canonical_json=k.canonical_json||jsonb_build_object('knowledge_scope_id',k.record_id)
      AND rs.canonical_json->>'knowledge_scope_id'=k.record_id
      AND rs.canonical_json->'source_version_ids'=s.canonical_json->'source_version_ids'
      AND rs.canonical_json->'authority'=CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
        THEN '[]'::jsonb ELSE '["source"]'::jsonb END
      AND rs.canonical_json->>'prompt_version'=CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
        THEN 'general-conversation-v1' ELSE 'cp3-grounded-question-v1' END
      AND rs.canonical_json->>'tool_version'=CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
        THEN 'provider-chat-v1' ELSE 'document-index-v1' END
      AND rs.canonical_json->'frozen_routing_context'=r.canonical_json->'frozen_routing_context'
      AND rs.canonical_json->>'user_policy_version'=t.policy_version
      AND rs.canonical_json->>'organization_policy_version'=t.policy_version
      AND e.record_id='egress-'||substr(encode(sha256(convert_to(r.record_id||'|'||
        (r.canonical_json->'frozen_routing_context'->>'fingerprint')||'|'||
        (r.canonical_json->'frozen_routing_context'->>'payload_fingerprint'),'UTF8')),'hex'),1,32)
      AND e.created_by=r.created_by AND e.trace_id=r.trace_id AND e.canonical_json->>'run_id'=r.record_id
      AND e.canonical_json->'allowed'='true'::jsonb
      AND e.canonical_json->'frozen_routing_context'=r.canonical_json->'frozen_routing_context'
      AND rs.canonical_json->>'egress_policy_fingerprint'=r.canonical_json->'frozen_routing_context'->>'fingerprint'
      AND d.record_id='routing-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
      AND d.created_by=r.created_by AND d.trace_id=r.trace_id AND d.canonical_json->>'run_id'=r.record_id
      AND d.canonical_json->>'routing_status'='selected' AND jsonb_typeof(d.canonical_json->'selected_deployment_id')='string'
      AND d.routing_policy_version_id=rs.routing_policy_version_id
      AND d.routing_policy_version_id='routing-policy-'||substr(encode(sha256(convert_to(
        (r.canonical_json->>'default_version')||'|'||(d.canonical_json->>'selected_deployment_id'),'UTF8')),'hex'),1,32)
      AND rs.canonical_json->>'routing_policy_version_id'=d.routing_policy_version_id
      AND rs.canonical_json->'candidate_order'=jsonb_build_array('deployment-'||substr(encode(sha256(convert_to(
        (d.canonical_json->>'selected_deployment_id')||'|'||(r.canonical_json->>'default_version'),'UTF8')),'hex'),1,32))
      AND ea.actor_id=r.created_by AND ea.action='question.egress.authorize' AND ea.target_type='Run' AND ea.target_id=r.record_id
      AND ea.outcome='succeeded' AND ea.trace_id=r.trace_id AND ea.policy_version=t.policy_version;
    IF NOT FOUND THEN RETURN NULL; END IF;
    v_store:='question'; v_recipient:=v_run.created_by; v_items:=v_run.source_items;
    v_kind:='run_failed'; v_severity:='warning';
    SELECT coalesce(array_agg(x),ARRAY[]::text[]) INTO v_versions FROM jsonb_array_elements_text(v_run.source_versions) x;
  ELSIF v_origin.action='question.answer' AND v_origin.target_type='Run' THEN
    SELECT r.created_by,s.canonical_json->'source_version_ids' AS source_versions,s.canonical_json->'items' AS source_items,rr.canonical_json->'insufficient' AS insufficient INTO v_run
      FROM public.runs r JOIN public.run_results rr ON rr.tenant_id=r.tenant_id AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id
      JOIN public.run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id
      JOIN public.scope_snapshots s ON s.tenant_id=rs.tenant_id AND s.workspace_id=rs.workspace_id AND s.record_id=rs.scope_snapshot_id
      JOIN public.notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id
        AND b.binding_kind='conversation_thread' AND b.record_id=r.conversation_id AND b.notebook_id=p_notebook
        AND b.created_by=r.created_by
      WHERE r.tenant_id=v_tenant AND r.workspace_id=v_workspace AND r.record_id=v_origin.target_id
        AND r.state='completed' AND r.created_by=v_actor
        -- 소비는 이미 commit된 정식 완료 사건을 읽는다. 감사 ID 결속은 유지한다.
        AND p_origin='audit-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
        AND jsonb_typeof(rr.canonical_json->'insufficient')='boolean'
        AND s.canonical_json->'source_version_ids'=v_origin.metadata->'source_version_ids';
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF jsonb_typeof(v_run.source_versions) IS DISTINCT FROM 'array'
      OR jsonb_typeof(v_run.source_items) IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    v_store:='question'; v_recipient:=v_run.created_by;
    v_items:=v_run.source_items;
    SELECT coalesce(array_agg(x),ARRAY[]::text[]) INTO v_versions FROM jsonb_array_elements_text(v_run.source_versions) x;
  ELSIF v_origin.action='studio.generation.finish' AND v_origin.target_type='StudioGenerationJob'
    AND v_origin.after_value->>'state' IN ('completed','failed','unavailable') THEN
    -- commit된 최초 최종 Job과 정식 원감사를 읽는다. producer의 미commit xmin 조건을 복사하지 않는다.
    SELECT j.* INTO v_job FROM public.studio_generation_jobs j
      WHERE j.tenant_id=v_tenant AND j.workspace_id=v_workspace AND j.job_id=v_origin.target_id
        AND j.actor_id=v_actor AND j.state IN ('completed','failed','unavailable') AND j.version>1
        AND j.lease_owner IS NULL AND j.lease_until IS NULL AND j.completed_at IS NOT NULL
        AND v_origin.trace_id=j.trace_id AND v_origin.policy_version=j.policy_version
        AND v_origin.after_value=jsonb_build_object('state',j.state,'version',j.version)
        AND p_origin='audit-studio-job-finish-'||substr(encode(sha256(convert_to(
          v_tenant||'|'||v_workspace||'|'||j.job_id||'|'||j.version::text||'|'||j.state,'UTF8')),'hex'),1,32);
    IF NOT FOUND OR v_job.request_json->>'notebook_id' IS DISTINCT FROM p_notebook THEN RETURN NULL; END IF;
    IF jsonb_typeof(v_job.request_json->'source_version_ids') IS DISTINCT FROM 'array'
      OR jsonb_array_length(v_job.request_json->'source_version_ids')=0
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_job.request_json->'source_version_ids') x WHERE jsonb_typeof(x)<>'string')
      OR (SELECT count(DISTINCT x.value) FROM jsonb_array_elements(v_job.request_json->'source_version_ids') x(value))
        <>jsonb_array_length(v_job.request_json->'source_version_ids') THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    v_store:='studio'; v_recipient:=v_job.actor_id;
    IF v_job.state<>'completed' THEN v_kind:='run_failed'; v_severity:='warning'; END IF;
    SELECT array_agg(x.version_id ORDER BY x.ordinality),jsonb_agg(jsonb_build_object(
        'origin','raw_source','context_item_id',sv.source_id,'source_id',sv.source_id,
        'source_version_id',sv.record_id,'digest_sha256',NULL) ORDER BY x.ordinality)
      INTO v_versions,v_items
      FROM jsonb_array_elements_text(v_job.request_json->'source_version_ids') WITH ORDINALITY x(version_id,ordinality)
      LEFT JOIN public.source_versions sv ON sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=x.version_id;
    IF v_job.request_json->>'source_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.source_versions sv
        WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=ANY(v_versions)
          AND sv.source_id=v_job.request_json->>'source_id') THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    IF v_job.state='completed' AND NOT EXISTS(
      SELECT 1 FROM public.studio_outputs so JOIN public.output_versions ov
        ON ov.tenant_id=so.tenant_id AND ov.workspace_id=so.workspace_id AND ov.studio_output_id=so.record_id
      JOIN public.generation_requests g ON g.tenant_id=so.tenant_id AND g.workspace_id=so.workspace_id AND g.record_id=so.generation_request_id
      JOIN public.generation_settings_snapshots gs ON gs.tenant_id=g.tenant_id AND gs.workspace_id=g.workspace_id
        AND gs.record_id=g.generation_settings_snapshot_id AND gs.record_id=ov.generation_settings_snapshot_id
      JOIN public.notebook_bindings b ON b.tenant_id=ov.tenant_id AND b.workspace_id=ov.workspace_id
        AND b.binding_kind='output_version' AND b.record_id=ov.record_id AND b.notebook_id=p_notebook
      WHERE so.tenant_id=v_tenant AND so.workspace_id=v_workspace AND so.record_id=v_job.studio_output_id AND ov.record_id=v_job.output_version_id
        AND so.created_by=v_recipient AND ov.created_by=v_recipient AND g.created_by=v_recipient AND gs.created_by=v_recipient
        AND so.trace_id=v_job.trace_id AND ov.trace_id=v_job.trace_id AND g.trace_id=v_job.trace_id AND gs.trace_id=v_job.trace_id
        AND g.state='submitted' AND g.canonical_json->>'notebook_id'=p_notebook
        AND g.canonical_json->'source_id'=v_job.request_json->'source_id'
        AND g.canonical_json->'source_version_ids'=v_job.request_json->'source_version_ids'
        AND ov.canonical_json->>'notebook_id'=p_notebook
        AND ov.canonical_json->'source_id'=v_job.request_json->'source_id'
        AND ov.canonical_json->'source_version_ids'=v_job.request_json->'source_version_ids'
        AND so.record_id='output-'||substr(encode(sha256(convert_to(v_tenant||'|'||v_workspace||'|'||v_recipient||
          '|studio.generation.create|'||v_job.idempotency_key,'UTF8')),'hex'),1,32)
        AND ov.record_id='output-version-'||substr(encode(sha256(convert_to(v_tenant||'|'||v_workspace||'|'||v_recipient||
          '|studio.generation.create|'||v_job.idempotency_key,'UTF8')),'hex'),1,32)
        AND g.record_id='generation-'||substr(encode(sha256(convert_to(v_tenant||'|'||v_workspace||'|'||v_recipient||
          '|studio.generation.create|'||v_job.idempotency_key,'UTF8')),'hex'),1,32)
        AND gs.record_id='settings-'||substr(encode(sha256(convert_to(v_tenant||'|'||v_workspace||'|'||v_recipient||
          '|studio.generation.create|'||v_job.idempotency_key,'UTF8')),'hex'),1,32)
        AND NOT EXISTS(SELECT 1 FROM public.evidence_references er LEFT JOIN public.evidence_spans es
          ON es.tenant_id=er.tenant_id AND es.workspace_id=er.workspace_id AND es.record_id=er.evidence_span_id
            AND es.source_version_id=er.source_version_id
          WHERE er.tenant_id=v_tenant AND er.workspace_id=v_workspace AND er.output_version_id=ov.record_id
            AND (es.record_id IS NULL OR NOT (er.source_version_id=ANY(v_versions))))
        AND NOT EXISTS(SELECT 1 FROM unnest(v_versions) x(version_id) WHERE NOT EXISTS(
          SELECT 1 FROM public.evidence_references er JOIN public.evidence_spans es
            ON es.tenant_id=er.tenant_id AND es.workspace_id=er.workspace_id AND es.record_id=er.evidence_span_id
              AND es.source_version_id=er.source_version_id
            WHERE er.tenant_id=v_tenant AND er.workspace_id=v_workspace AND er.output_version_id=ov.record_id
              AND er.source_version_id=x.version_id))) THEN
      RETURN NULL;
    END IF;
  ELSE
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  IF v_recipient IS NULL OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id
      WHERE m.tenant_id=v_tenant AND m.user_id=v_recipient AND u.state='active')
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o WHERE o.tenant_id=v_tenant AND o.user_id=v_recipient AND o.state<>'active')
    OR NOT EXISTS(SELECT 1 FROM public.notebooks n WHERE n.tenant_id=v_tenant AND n.workspace_id=v_workspace AND n.notebook_id=p_notebook)
    OR EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=v_tenant AND d.workspace_id=v_workspace
      AND d.notebook_id=p_notebook AND d.state IN ('accepted','deleting','completed')) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  -- VIEW의 기존 실제 tenant-role/Workspace-role 교집합. 일반 Notebook은 owner 전용으로 좁히지 않는다.
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN CASE
      WHEN (tr.role='personal_owner' AND w.workspace_kind='personal')
        OR (tr.role='organization_admin' AND w.workspace_kind='organization') THEN tr.role END
      ELSE m.role END INTO v_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=v_recipient AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id AND m.user_id=v_recipient AND m.state='active'
    WHERE w.tenant_id=v_tenant AND w.workspace_id=v_workspace;
  IF v_role IS NULL OR v_role NOT IN ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR (v_store='question' AND v_kind='run_failed' AND v_role='viewer') THEN
    RAISE EXCEPTION 'NOTIFICATION_ACTION_DENIED' USING ERRCODE='42501';
  END IF;
  IF jsonb_typeof(v_items) IS DISTINCT FROM 'array' OR
    (SELECT coalesce(jsonb_agg(x.value->'source_version_id' ORDER BY x.ordinality),'[]'::jsonb)
      FROM jsonb_array_elements(v_items) WITH ORDINALITY x(value,ordinality)) IS DISTINCT FROM to_jsonb(v_versions) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(v_items)
  LOOP
    IF jsonb_typeof(v_item) IS DISTINCT FROM 'object' THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(v_item))<>5
      OR NOT (v_item ?& ARRAY['origin','context_item_id','source_id','source_version_id','digest_sha256'])
      OR coalesce(v_item->>'origin' NOT IN ('raw_source','daon_knowledge'),true)
      OR v_item->>'context_item_id' IS NULL OR v_item->>'context_item_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      OR v_item->>'source_id' IS NULL OR v_item->>'source_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      OR v_item->>'source_version_id' IS NULL OR v_item->>'source_version_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      OR NOT EXISTS(SELECT 1 FROM public.source_versions sv WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace
        AND sv.record_id=v_item->>'source_version_id' AND sv.source_id=v_item->>'source_id') THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
    IF v_item->>'origin'='raw_source' THEN
      IF v_item->>'context_item_id' IS DISTINCT FROM v_item->>'source_id'
        OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=v_tenant AND b.workspace_id=v_workspace
          AND b.notebook_id=p_notebook AND b.binding_kind='source' AND b.record_id=v_item->>'source_id'
          AND b.version_id=v_item->>'source_version_id') THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    ELSE
      -- 실제 knowledge binding과 불변 snapshot 항목을 대조한다. raw Source binding을 합성하지 않는다.
      IF jsonb_typeof(v_item->'digest_sha256') IS DISTINCT FROM 'string'
        OR v_item->>'digest_sha256' !~ '^[0-9a-f]{64}$'
        OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b JOIN public.scope_snapshots k
          ON k.tenant_id=b.tenant_id AND k.workspace_id=b.workspace_id AND k.record_id=b.record_id
          JOIN public.knowledge_scopes ks ON ks.tenant_id=k.tenant_id AND ks.workspace_id=k.workspace_id AND ks.record_id=k.knowledge_scope_id
          WHERE b.tenant_id=v_tenant AND b.workspace_id=v_workspace AND b.notebook_id=p_notebook
          AND b.binding_kind='knowledge_context' AND b.version_id IS NULL AND b.record_id=v_item->>'context_item_id'
          AND EXISTS(SELECT 1 FROM jsonb_array_elements(k.canonical_json->'items') x WHERE x=v_item)) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    END IF;
  END LOOP;
  IF (SELECT count(*) FROM public.source_versions sv JOIN public.sources s
        ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id
      WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=ANY(v_versions))<>cardinality(v_versions)
    OR EXISTS(SELECT 1 FROM public.source_versions sv WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=ANY(v_versions)
      AND (EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=v_tenant AND u.workspace_id=v_workspace
          AND u.notebook_id=p_notebook AND u.source_id=sv.source_id AND u.source_version_id=sv.record_id)
        OR EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=v_tenant AND d.workspace_id=v_workspace AND d.source_id=sv.source_id AND d.source_active=false)
        OR EXISTS(SELECT 1 FROM public.identity_auth_source_access a WHERE a.tenant_id=v_tenant AND a.workspace_id=v_workspace
          AND a.source_version_id=sv.record_id AND a.user_id=v_recipient AND a.allowed=0))) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.actor_id',v_recipient,true);
  PERFORM set_config('app.notebook_id',p_notebook,true);
  FOR v_source IN SELECT sv.record_id,sv.source_id,sv.version,sv.canonical_json FROM public.source_versions sv
    WHERE sv.tenant_id=v_tenant AND sv.workspace_id=v_workspace AND sv.record_id=ANY(v_versions)
  LOOP
    IF v_source.canonical_json->>'origin_kind'='website' THEN
      IF NOT EXISTS(SELECT 1 FROM public.website_source_snapshots w JOIN public.sources s
          ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id
          JOIN public.notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id
          WHERE w.tenant_id=v_tenant AND w.workspace_id=v_workspace AND w.notebook_id=p_notebook
          AND w.source_version_id=v_source.record_id AND w.created_by=v_recipient AND n.created_by=v_recipient AND s.state='ready'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.workspace_id=w.workspace_id
            AND newer.source_id=w.source_id AND newer.version>v_source.version)) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    ELSIF v_source.canonical_json->>'origin_kind'='mcp_open_law' THEN
      IF v_source.canonical_json->>'connector_id' IS DISTINCT FROM 'mcp-open-law-go-kr'
        OR NOT EXISTS(SELECT 1 FROM public.open_law_source_snapshots w JOIN public.sources s
          ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id
          JOIN public.notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id
          JOIN public.workspace_connectors c ON c.tenant_id=w.tenant_id AND c.workspace_id=w.workspace_id AND c.connector_id=w.connector_id
          WHERE w.tenant_id=v_tenant AND w.workspace_id=v_workspace AND w.notebook_id=p_notebook
          AND w.snapshot_id=v_source.canonical_json->>'snapshot_id' AND w.source_id=v_source.source_id AND w.source_version_id=v_source.record_id
          AND w.created_by=v_recipient AND n.created_by=v_recipient AND s.state='ready'
          AND w.raw_sha256=v_source.canonical_json->>'raw_sha256' AND w.text_sha256=v_source.canonical_json->>'text_sha256'
          AND c.status='connected' AND c.verification_json->>'protocol_version'='2025-11-25'
          AND c.verification_json->>'tool_schema_digest'='df8c707ca4e47b8e22645c409d9bfb6a8fbe3038dabe4eaa44d6ee74457a8d88'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.workspace_id=w.workspace_id
            AND newer.source_id=w.source_id AND newer.version>v_source.version) FOR SHARE OF c) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
      SELECT p.canonical_json INTO v_org_policy FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p
        ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        WHERE b.tenant_id=v_tenant AND b.organization_id=v_tenant AND b.workspace_id IS NULL AND b.scope_type='organization'
          AND b.active AND b.current AND p.state='active' AND p.organization_id=v_tenant AND p.workspace_id IS NULL AND p.scope_type='organization';
      SELECT p.canonical_json INTO v_workspace_policy FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p
        ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        WHERE b.tenant_id=v_tenant AND b.organization_id=v_tenant AND b.workspace_id=v_workspace AND b.scope_type='workspace'
          AND b.active AND b.current AND p.state='active' AND p.organization_id=v_tenant AND p.workspace_id=v_workspace AND p.scope_type='workspace';
      IF v_org_policy IS NULL OR v_workspace_policy IS NULL
        OR v_org_policy->>'mode' IS DISTINCT FROM 'allow_approved_external' OR v_workspace_policy->>'mode' IS DISTINCT FROM 'allow_approved_external'
        OR v_org_policy->>'classification' IS DISTINCT FROM 'public' OR v_workspace_policy->>'classification' IS DISTINCT FROM 'public'
        OR (v_org_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
        OR (v_workspace_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
        OR (v_org_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
        OR (v_workspace_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
        OR coalesce((v_org_policy->>'max_bytes')::bigint,0)<=0 OR coalesce((v_workspace_policy->>'max_bytes')::bigint,0)<=0
        OR v_org_policy->>'required_approver' IS NULL OR v_workspace_policy->>'required_approver' IS NULL
        OR v_org_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
        OR v_workspace_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
        OR (('organization_admin' IN (v_org_policy->>'required_approver',v_workspace_policy->>'required_approver'))
          AND v_role NOT IN ('personal_owner','organization_admin'))
        OR v_role NOT IN ('personal_owner','organization_admin','workspace_admin') THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
    END IF;
  END LOOP;
  v_event:=v_store||'-event-'||substr(encode(sha256(convert_to(p_origin||'|'||v_workspace,'UTF8')),'hex'),1,32);
  IF v_pending.recipient_id IS DISTINCT FROM v_recipient OR v_pending.source_store IS DISTINCT FROM v_store
    OR v_pending.source_event_id IS DISTINCT FROM v_event OR v_pending.kind IS DISTINCT FROM v_kind
    OR v_pending.severity IS DISTINCT FROM v_severity THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  IF v_store='document' THEN
    v_result:=jsonb_build_object('deep_link','/notebooks/'||p_notebook||'?source_version_id='||v_versions[1],
      'title',CASE WHEN v_run.result_status='failed' THEN '문서 처리 실패'
        WHEN v_run.result_status='needs_review' THEN '문서 처리 검토 필요' ELSE '문서 처리 완료' END,
      'summary',CASE WHEN v_run.result_status='failed' THEN '문서 처리 Run이 실패했습니다. 현재 처리 상태를 확인하세요.'
        WHEN v_run.result_status='needs_review' THEN '문서 처리 Run이 완료되었으며 검토가 필요합니다.' ELSE '문서 처리 Run이 완료되었습니다.' END);
  ELSIF v_store='studio' THEN
    v_result:=jsonb_build_object('deep_link','/notebooks/'||p_notebook||CASE WHEN v_job.state='completed'
        THEN '?output_version_id='||v_job.output_version_id ELSE '?studio_job_id='||v_job.job_id END,
      'title',CASE WHEN v_job.state='completed' THEN 'Studio 생성 완료' WHEN v_job.state='unavailable' THEN 'Studio 생성 사용 불가' ELSE 'Studio 생성 실패' END,
      'summary',CASE WHEN v_job.state='completed' THEN 'Studio 생성 Job이 완료되었습니다.'
        WHEN v_job.state='unavailable' THEN 'Studio 생성 Job을 사용할 수 없습니다. 현재 처리 상태를 확인하세요.'
        ELSE 'Studio 생성 Job이 실패했습니다. 현재 처리 상태를 확인하세요.' END);
  ELSE
    v_result:=jsonb_build_object('deep_link','/notebooks/'||p_notebook||'?run_id='||v_origin.target_id,
      'title',CASE WHEN v_kind='run_failed' THEN '질문 처리 실패' ELSE '질문 처리 완료' END,
      'summary',CASE WHEN v_kind='run_failed' THEN '질문 처리 Run이 실패했습니다. 현재 처리 상태를 확인하세요.'
        WHEN v_run.insufficient='true'::jsonb THEN '질문 처리 Run이 완료되었으며 근거가 부족합니다.' ELSE '질문 처리 Run이 완료되었습니다.' END);
  END IF;
  PERFORM set_config('app.actor_id',coalesce(v_previous_actor,''),true);
  PERFORM set_config('app.notebook_id',coalesce(v_previous_notebook,''),true);
  RETURN v_result;
EXCEPTION WHEN insufficient_privilege THEN
  -- 이 함수가 명시한 현재 자료/권한 거부만 suppressed 후보로 돌린다. 실제 DB 권한/조회 오류는 전파한다.
  IF SQLERRM IN ('NOTIFICATION_SOURCE_ACCESS_DENIED','NOTIFICATION_ACTION_DENIED') THEN RETURN NULL; END IF;
  RAISE;
END $$;
ALTER FUNCTION public.current_completed_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_completed_notification_target(text) FROM PUBLIC,daon_app;

-- 소비는 등록된 본인 의도만 잠근다. 다른 수신자·다른 source 원장은 반환하지 않는다.
CREATE FUNCTION public.pending_studio_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'NOTIFICATION_BATCH_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'')
      AND s.source_store='studio' AND s.state='pending'
      AND (s.action,s.target_type) IN (('studio.review','review_requests'),('studio.approval_request','approval_requests'),
        ('studio.approval','approvals'),('studio.delivery','deliveries'))
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE OF s SKIP LOCKED;
END $$;
ALTER FUNCTION public.pending_studio_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_studio_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_studio_notification_sources(integer) TO daon_app;

-- 제한 소비 함수만 호출하는 현재 DB 자료 보호 검사다. producer의 현재 Tx 조건은 그대로 둔다.
CREATE FUNCTION public.current_studio_notification_version(p_source text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  p_recipient text:=nullif(current_setting('app.actor_id',true),'');
  p_notebook text; source public.notification_source_outbox; original public.audit_events;
  v_notification_output_version_id text; record_actor text; kind text;
  v_notification_role text; v_notification_action text; v_notification_permission text; v_notification_allowed boolean;
  v_notification_requester text;
  v_notification_source record; v_notification_organization_policy jsonb; v_notification_workspace_policy jsonb;
BEGIN
  SELECT * INTO source FROM public.notification_source_outbox s
    WHERE s.tenant_id=tenant AND s.workspace_id=workspace AND s.recipient_id=p_recipient
      AND s.source_event_id=p_source AND s.source_store='studio' AND s.state='pending';
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  p_notebook:=source.notebook_id;
  SELECT * INTO original FROM public.audit_events a
    WHERE a.tenant_id=tenant AND a.workspace_id=workspace AND a.event_id=source.origin_event_id
      AND a.action=source.action AND a.target_type=source.target_type AND a.target_id=source.target_id
      AND a.trace_id=source.trace_id AND a.policy_version=source.policy_version
      AND a.occurred_at=source.occurred_at AND a.outcome='succeeded';
  kind:=CASE original.action WHEN 'studio.review' THEN 'review' WHEN 'studio.approval_request' THEN 'approval'
    WHEN 'studio.approval' THEN 'approval' WHEN 'studio.delivery' THEN 'delivery' END;
  IF kind IS NULL OR original.target_type IS DISTINCT FROM (CASE original.action
    WHEN 'studio.review' THEN 'review_requests' WHEN 'studio.approval_request' THEN 'approval_requests'
    WHEN 'studio.approval' THEN 'approvals' WHEN 'studio.delivery' THEN 'deliveries' END) THEN
    RETURN NULL;
  END IF;
  EXECUTE format('SELECT output_version_id,created_by FROM public.%I WHERE tenant_id=$1 AND workspace_id=$2 AND record_id=$3',original.target_type)
    INTO v_notification_output_version_id,record_actor USING tenant,workspace,original.target_id;
  v_notification_requester:=record_actor;
  IF original.action='studio.approval' THEN
    SELECT r.created_by INTO v_notification_requester FROM public.approvals a JOIN public.approval_requests r
      ON r.tenant_id=a.tenant_id AND r.workspace_id=a.workspace_id AND r.record_id=a.approval_request_id
      AND r.output_version_id=a.output_version_id
      WHERE a.tenant_id=tenant AND a.workspace_id=workspace AND a.record_id=original.target_id;
  END IF;
  IF v_notification_output_version_id IS NULL OR record_actor IS DISTINCT FROM original.actor_id
    OR v_notification_requester IS NULL
    OR (kind='delivery' AND p_recipient IS DISTINCT FROM original.actor_id)
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m JOIN public.identity_users u ON u.user_id=m.user_id
      JOIN public.user_accounts ua ON ua.tenant_id=m.tenant_id AND ua.user_id=m.user_id
      WHERE m.tenant_id=tenant AND m.user_id=p_recipient AND u.state='active')
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      WHERE o.tenant_id=tenant AND o.user_id=p_recipient AND o.state<>'active')
    OR NOT EXISTS(SELECT 1 FROM public.identity_auth_workspaces w WHERE w.tenant_id=tenant AND w.workspace_id=workspace
      AND (EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles t WHERE t.tenant_id=w.tenant_id
        AND t.user_id=p_recipient AND t.state='active' AND
          ((w.workspace_kind='personal' AND t.role='personal_owner') OR (w.workspace_kind='organization' AND t.role='organization_admin')))
        OR (NOT EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles t WHERE t.tenant_id=w.tenant_id AND t.user_id=p_recipient AND t.state='active')
          AND EXISTS(SELECT 1 FROM public.identity_auth_memberships m WHERE m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
            AND m.user_id=p_recipient AND m.state='active'))))
    OR EXISTS(SELECT 1 FROM public.evidence_references er JOIN public.source_versions sv
      ON sv.tenant_id=er.tenant_id AND sv.workspace_id=er.workspace_id AND sv.record_id=er.source_version_id
      JOIN public.identity_auth_source_access a ON a.tenant_id=sv.tenant_id AND a.workspace_id=sv.workspace_id
        AND a.source_version_id=sv.record_id AND a.user_id=p_recipient AND a.allowed=0
      WHERE er.tenant_id=tenant AND er.workspace_id=workspace AND er.output_version_id=v_notification_output_version_id)
    OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b JOIN public.notebooks n
      ON n.tenant_id=b.tenant_id AND n.workspace_id=b.workspace_id AND n.notebook_id=b.notebook_id
      WHERE b.tenant_id=tenant AND b.workspace_id=workspace AND b.notebook_id=p_notebook
      AND b.binding_kind='output_version' AND b.record_id=v_notification_output_version_id) THEN
    RETURN NULL;
  END IF;
  -- Python 후보 판정을 건너뛰고 함수를 직접 호출해도 같은 현재 역할/permission이 필요하다.
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN CASE
      WHEN (tr.role='personal_owner' AND w.workspace_kind='personal')
        OR (tr.role='organization_admin' AND w.workspace_kind='organization') THEN tr.role END
      ELSE m.role END INTO v_notification_role
    FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=p_recipient AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id AND m.user_id=p_recipient AND m.state='active'
    WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  v_notification_action:=CASE WHEN original.action='studio.delivery' THEN 'deliver'
    WHEN p_recipient=v_notification_requester THEN 'view' WHEN original.action='studio.review' THEN 'review' ELSE 'approve' END;
  IF v_notification_role IS NULL OR v_notification_role NOT IN ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR (v_notification_action='review' AND v_notification_role NOT IN ('personal_owner','organization_admin','workspace_admin','reviewer','approver'))
    OR (v_notification_action IN ('approve','deliver') AND v_notification_role NOT IN ('personal_owner','organization_admin','approver')) THEN
    RETURN NULL;
  END IF;
  FOR v_notification_permission IN SELECT unnest(CASE v_notification_action
    WHEN 'approve' THEN ARRAY['final_approval_external_delivery']
    WHEN 'deliver' THEN ARRAY['file_download_share','final_approval_external_delivery'] ELSE ARRAY[]::text[] END)
  LOOP
    -- AuthorizationService._effective_permission의 조직 잠금/deny→Workspace→조직 grant→역할 기본값 순서다.
    -- 이 두 Action에 허용된 세 역할은 위 permission 모두 기본 허용이다.
    SELECT CASE WHEN tr.permission IS NOT NULL AND (tr.locked<>0 OR tr.effect='deny') THEN tr.effect='grant'
        WHEN wr.permission IS NOT NULL THEN wr.effect='grant' WHEN tr.permission IS NOT NULL THEN true ELSE true END
      INTO v_notification_allowed FROM (SELECT 1) singleton
      LEFT JOIN public.identity_auth_tenant_permission_rules tr ON tr.tenant_id=tenant AND tr.permission=v_notification_permission
      LEFT JOIN public.identity_auth_workspace_permission_rules wr ON wr.tenant_id=tenant AND wr.workspace_id=workspace AND wr.permission=v_notification_permission;
    IF v_notification_allowed IS DISTINCT FROM true THEN
      RETURN NULL;
    END IF;
  END LOOP;
  IF EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=tenant AND d.workspace_id=workspace
      AND d.notebook_id=p_notebook AND d.state IN ('accepted','deleting','completed'))
    OR EXISTS(SELECT 1 FROM public.evidence_references er
      LEFT JOIN public.source_versions sv ON sv.tenant_id=er.tenant_id AND sv.workspace_id=er.workspace_id AND sv.record_id=er.source_version_id
      LEFT JOIN public.sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id
      WHERE er.tenant_id=tenant AND er.workspace_id=workspace AND er.output_version_id=v_notification_output_version_id
      AND (sv.record_id IS NULL OR s.record_id IS NULL
        OR EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=tenant AND d.workspace_id=workspace AND d.source_id=s.record_id AND d.source_active=false)
        OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=tenant AND b.workspace_id=workspace
          AND b.notebook_id=p_notebook AND b.binding_kind='source' AND b.record_id=s.record_id AND b.version_id=sv.record_id)
        OR EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=tenant AND u.workspace_id=workspace
          AND u.notebook_id=p_notebook AND u.source_id=s.record_id AND u.source_version_id=sv.record_id))) THEN
    RETURN NULL;
  END IF;
  -- 원caller·원감사·현재 역할 검증 뒤에만 실제 수신자의 snapshot RLS context를 적용한다.
  PERFORM set_config('app.actor_id',p_recipient,true);
  FOR v_notification_source IN SELECT sv.record_id,sv.source_id,sv.version,sv.canonical_json
    FROM public.evidence_references er JOIN public.source_versions sv
      ON sv.tenant_id=er.tenant_id AND sv.workspace_id=er.workspace_id AND sv.record_id=er.source_version_id
    WHERE er.tenant_id=tenant AND er.workspace_id=workspace AND er.output_version_id=v_notification_output_version_id
  LOOP
    IF v_notification_source.canonical_json->>'origin_kind'='website' THEN
      -- 기존 웹 snapshot 조회의 owner/현재성 조건. 일반 Source에는 owner 조건을 추가하지 않는다.
      IF NOT EXISTS(SELECT 1 FROM public.website_source_snapshots w JOIN public.sources s
          ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id
          JOIN public.notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id
          WHERE w.tenant_id=tenant AND w.workspace_id=workspace AND w.notebook_id=p_notebook
          AND w.source_version_id=v_notification_source.record_id AND w.created_by=p_recipient AND n.created_by=p_recipient
          AND s.state='ready'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.workspace_id=w.workspace_id
            AND newer.source_id=w.source_id AND newer.version>v_notification_source.version)) THEN
        RETURN NULL;
      END IF;
    ELSIF v_notification_source.canonical_json->>'origin_kind'='mcp_open_law' THEN
      -- 서버 환경 활성화는 기존 Python gate에서만 검사한다. 이 함수는 저장된 자료 보호를 독립 검사한다.
      IF v_notification_source.canonical_json->>'connector_id' IS DISTINCT FROM 'mcp-open-law-go-kr'
        OR NOT EXISTS(SELECT 1 FROM public.open_law_source_snapshots w JOIN public.sources s
          ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id
          JOIN public.notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id
          JOIN public.workspace_connectors c ON c.tenant_id=w.tenant_id AND c.workspace_id=w.workspace_id AND c.connector_id=w.connector_id
          WHERE w.tenant_id=tenant AND w.workspace_id=workspace AND w.notebook_id=p_notebook
          AND w.snapshot_id=v_notification_source.canonical_json->>'snapshot_id'
          AND w.source_id=v_notification_source.source_id AND w.source_version_id=v_notification_source.record_id
          AND w.created_by=p_recipient AND n.created_by=p_recipient AND s.state='ready'
          AND w.raw_sha256=v_notification_source.canonical_json->>'raw_sha256'
          AND w.text_sha256=v_notification_source.canonical_json->>'text_sha256'
          AND c.status='connected' AND c.verification_json->>'protocol_version'='2025-11-25'
          AND c.verification_json->>'tool_schema_digest'='df8c707ca4e47b8e22645c409d9bfb6a8fbe3038dabe4eaa44d6ee74457a8d88'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.workspace_id=w.workspace_id
            AND newer.source_id=w.source_id AND newer.version>v_notification_source.version) FOR SHARE OF c) THEN
        RETURN NULL;
      END IF;
      SELECT p.canonical_json INTO v_notification_organization_policy
        FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        WHERE b.tenant_id=tenant AND b.organization_id=tenant AND b.workspace_id IS NULL AND b.scope_type='organization'
        AND b.active AND b.current AND p.state='active' AND p.organization_id=tenant AND p.workspace_id IS NULL AND p.scope_type='organization';
      SELECT p.canonical_json INTO v_notification_workspace_policy
        FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        WHERE b.tenant_id=tenant AND b.organization_id=tenant AND b.workspace_id=workspace AND b.scope_type='workspace'
        AND b.active AND b.current AND p.state='active' AND p.organization_id=tenant AND p.workspace_id=workspace AND p.scope_type='workspace';
      -- resolve_effective_payload: 둘 다 allow/public, 목적지/Provider 교집합, 최소 bytes, 더 높은 승인 역할.
      IF v_notification_organization_policy IS NULL OR v_notification_workspace_policy IS NULL
        OR v_notification_organization_policy->>'mode' IS DISTINCT FROM 'allow_approved_external'
        OR v_notification_workspace_policy->>'mode' IS DISTINCT FROM 'allow_approved_external'
        OR v_notification_organization_policy->>'classification' IS DISTINCT FROM 'public'
        OR v_notification_workspace_policy->>'classification' IS DISTINCT FROM 'public'
        OR (v_notification_organization_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
        OR (v_notification_workspace_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
        OR (v_notification_organization_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
        OR (v_notification_workspace_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
        OR coalesce((v_notification_organization_policy->>'max_bytes')::bigint,0)<=0
        OR coalesce((v_notification_workspace_policy->>'max_bytes')::bigint,0)<=0
        OR v_notification_organization_policy->>'required_approver' IS NULL
        OR v_notification_workspace_policy->>'required_approver' IS NULL
        OR v_notification_organization_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
        OR v_notification_workspace_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
        OR (('organization_admin' IN (v_notification_organization_policy->>'required_approver',v_notification_workspace_policy->>'required_approver'))
          AND v_notification_role NOT IN ('personal_owner','organization_admin'))
        OR v_notification_role NOT IN ('personal_owner','organization_admin','workspace_admin') THEN
        RETURN NULL;
      END IF;
    END IF;
  END LOOP;
  IF source.kind IS DISTINCT FROM kind OR source.severity IS DISTINCT FROM 'info'
    OR source.source_event_id IS DISTINCT FROM
      ('studio-event-'||substr(encode(sha256(convert_to(source.origin_event_id||'|'||workspace,'UTF8')),'hex'),1,32)) THEN
    RETURN NULL;
  END IF;
  RETURN v_notification_output_version_id;
END $$;
ALTER FUNCTION public.current_studio_notification_version(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_studio_notification_version(text) FROM PUBLIC,daon_app;

-- 현재 원자료 검증과 알림/상세/created 의도/source ack는 이 호출의 한 transaction 안에 있다.
CREATE FUNCTION public.materialize_studio_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; v_output text; v_id text; v_title text; v_summary text;
  v_previous_notebook text:=current_setting('app.notebook_id',true);
  v_draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO source FROM public.notification_source_outbox s
    WHERE s.tenant_id=tenant AND s.workspace_id=workspace AND s.recipient_id=actor
      AND s.source_event_id=p_source AND s.source_store='studio' AND s.state='pending'
      AND (s.action,s.target_type) IN (('studio.review','review_requests'),('studio.approval_request','approval_requests'),
        ('studio.approval','approvals'),('studio.delivery','deliveries')) FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  PERFORM set_config('app.notebook_id',source.notebook_id,true);
  -- 호출 의도는 현재 접근 판정을 대체하지 않는다. DB 오류는 이 검사에서 그대로 전파한다.
  v_output:=public.current_studio_notification_version(p_source);
  IF v_output IS NOT NULL AND NOT p_deliver THEN
    PERFORM set_config('app.notebook_id',coalesce(v_previous_notebook,''),true);
    RETURN NULL;
  END IF;
  IF v_output IS NULL THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    PERFORM set_config('app.notebook_id',coalesce(v_previous_notebook,''),true);
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  IF source.notebook_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR v_output !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  v_title:=CASE source.action WHEN 'studio.review' THEN '검토 요청' WHEN 'studio.approval_request' THEN '승인 요청'
    WHEN 'studio.approval' THEN '승인 처리' WHEN 'studio.delivery' THEN '전달 기록' END;
  v_summary:=CASE source.action WHEN 'studio.review' THEN '검토할 산출물이 있습니다.'
    WHEN 'studio.approval_request' THEN '승인 요청 상태를 확인하세요.'
    WHEN 'studio.approval' THEN '산출물 승인 처리 상태를 확인하세요.'
    WHEN 'studio.delivery' THEN '산출물 전달 기록을 확인하세요.' END;
  v_id:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  -- 원사건 시각은 source에 보존하고 새 알림 생성은 이 소비 transaction의 기본 now()를 쓴다.
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,v_id,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,
    kind,severity,title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
    VALUES(tenant,v_id,'studio',source.origin_event_id,p_source,source.kind,source.severity,v_title,v_summary,
      source.target_type,source.target_id,source.notebook_id,
      '/notebooks/'||source.notebook_id||'?output_version_id='||v_output,
      source.trace_id,source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO v_draft FROM public.notification_audit_outbox a
    WHERE a.tenant_id=tenant AND a.workspace_id=workspace AND a.recipient_id=actor
      AND a.notification_id=v_id AND a.action='notification.created';
  IF v_draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  PERFORM set_config('app.notebook_id',coalesce(v_previous_notebook,''),true);
  RETURN jsonb_build_object('state','delivered','notification_id',v_id,'draft',v_draft);
END $$;
ALTER FUNCTION public.materialize_studio_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_studio_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_studio_notification(text,boolean) TO daon_app;

-- 완료 source만 본인 범위에서 최대 100건 잠근다. Studio의 기존 소비 경로는 변경하지 않는다.
CREATE FUNCTION public.pending_completed_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'NOTIFICATION_BATCH_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'')
      AND s.source_store IN ('document','question','studio') AND s.state='pending'
      AND ((s.source_store='document' AND s.action='canon.transition' AND s.target_type='ProcessingRun'
          AND ((s.kind='run_state' AND s.severity='info') OR (s.kind='run_failed' AND s.severity='warning')))
        OR (s.source_store='question' AND s.action='question.answer' AND s.target_type='Run'
          AND s.kind='run_state' AND s.severity='info')
        OR (s.source_store='question' AND s.action='canon.transition' AND s.target_type='Run'
          AND s.kind='run_failed' AND s.severity='warning')
        OR (s.source_store='studio' AND s.action='studio.generation.finish' AND s.target_type='StudioGenerationJob'
          AND ((s.kind='run_failed' AND s.severity='warning') OR (s.kind='run_state' AND s.severity='info'))))
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE OF s SKIP LOCKED;
END $$;
ALTER FUNCTION public.pending_completed_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_completed_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_completed_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.materialize_completed_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; v_target jsonb; v_id text; v_draft jsonb;
  v_previous_notebook text:=current_setting('app.notebook_id',true);
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO source FROM public.notification_source_outbox s
    WHERE s.tenant_id=tenant AND s.workspace_id=workspace AND s.recipient_id=actor
      AND s.source_event_id=p_source AND s.state='pending'
      AND (s.source_store IN ('document','question') OR (s.source_store='studio'
        AND s.action='studio.generation.finish' AND s.target_type='StudioGenerationJob'
        AND ((s.kind='run_failed' AND s.severity='warning') OR (s.kind='run_state' AND s.severity='info')))) FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  PERFORM set_config('app.notebook_id',source.notebook_id,true);
  -- bool은 호출 의도일 뿐이다. 현재 DB 권한/원자료는 반드시 독립 재검증한다.
  v_target:=public.current_completed_notification_target(p_source);
  IF v_target IS NOT NULL AND NOT p_deliver THEN
    PERFORM set_config('app.notebook_id',coalesce(v_previous_notebook,''),true);
    RETURN NULL;
  END IF;
  IF v_target IS NULL THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    PERFORM set_config('app.notebook_id',coalesce(v_previous_notebook,''),true);
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  v_id:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  -- 원사건 occurred_at은 보존한다. 알림/created 감사는 이 소비 transaction의 현재 시각이다.
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,v_id,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,
    kind,severity,title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
    VALUES(tenant,v_id,source.source_store,source.origin_event_id,p_source,source.kind,source.severity,
      v_target->>'title',v_target->>'summary',source.target_type,source.target_id,source.notebook_id,v_target->>'deep_link',
      source.trace_id,source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO v_draft FROM public.notification_audit_outbox a
    WHERE a.tenant_id=tenant AND a.workspace_id=workspace AND a.recipient_id=actor
      AND a.notification_id=v_id AND a.action='notification.created';
  IF v_draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  PERFORM set_config('app.notebook_id',coalesce(v_previous_notebook,''),true);
  RETURN jsonb_build_object('state','delivered','notification_id',v_id,'draft',v_draft);
END $$;
ALTER FUNCTION public.materialize_completed_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_completed_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_completed_notification(text,boolean) TO daon_app;

-- S42_ACCOUNT_NOTIFICATION_BEGIN
-- 기존 계정 요청 원장만 읽는다. 부모 표/역할/RLS/grant는 변경하지 않는다.
DO $account_notification_preflight$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
      AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
    OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
    OR NOT pg_catalog.pg_has_role('daon_notebook_delete','daon_app','USAGE')
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.identity_account_requests','SELECT')
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.identity_devices','SELECT')
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.identity_sessions','SELECT') THEN
    RAISE EXCEPTION 'ACCOUNT_NOTIFICATION_PRIVILEGE_PRECONDITION' USING ERRCODE='55000';
  END IF;
END;
$account_notification_preflight$;

CREATE FUNCTION public.current_account_notification_event(p_origin text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  previous_account_actor text:=current_setting('daon.account_actor',true);
  request_row public.identity_account_requests; request_xid text; target_xid text; target_state text;
  origin_parts text[]; events jsonb; primary_event jsonb; primary_index bigint; primary_count bigint;
  event_action text; event_type text; event_kind text; event_severity text; event_metadata jsonb;
  target_updated timestamptz; response jsonb; accessible boolean;
BEGIN
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL OR p_origin IS NULL
    OR p_origin !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='22023';
  END IF;
  origin_parts:=regexp_match(p_origin,'^(.+)-(0|[1-9][0-9]{0,8})$');
  IF origin_parts IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  -- 0067 actor RLS를 같은 요청자의 문맥으로만 읽고 즉시 복원한다.
  -- app GUC는 인증 증거가 아니다. backend의 정식 principal/context가 신뢰 경계다.
  PERFORM set_config('daon.account_actor',actor,true);
  SELECT r.* INTO request_row FROM public.identity_account_requests r
    WHERE r.tenant_id=tenant AND r.actor_id=actor AND r.event_id=origin_parts[1];
  SELECT r.xmin::text INTO request_xid FROM public.identity_account_requests r
    WHERE r.tenant_id=tenant AND r.actor_id=actor AND r.event_id=origin_parts[1];
  PERFORM set_config('daon.account_actor',coalesce(previous_account_actor,''),true);
  IF request_row.event_id IS NULL THEN RETURN NULL; END IF;
  CASE request_row.operation
    WHEN 'device.trust' THEN
      event_action:='identity.device.trusted'; event_type:='device'; event_kind:='device'; event_severity:='info'; event_metadata:='{}'::jsonb;
    WHEN 'device.revoke' THEN
      event_action:='identity.device.revoked'; event_type:='device'; event_kind:='device'; event_severity:='warning';
      event_metadata:='{"sync_key_revoke_required":true}'::jsonb;
    WHEN 'session.revoke' THEN
      event_action:='identity.session.revoked'; event_type:='session'; event_kind:='authentication'; event_severity:='warning';
      event_metadata:='{"reason_code":"USER_REQUESTED"}'::jsonb;
    ELSE RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END CASE;
  events:=request_row.audit_events_json::jsonb;
  IF public.valid_identity_account_events(request_row.audit_events_json,request_row.operation) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  SELECT count(*) INTO primary_count FROM jsonb_array_elements(events) e
    WHERE e->>'action'=event_action AND e->>'target_type'=event_type;
  IF primary_count<>1 THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  SELECT e.value,e.ordinality-1 INTO primary_event,primary_index FROM jsonb_array_elements(events) WITH ORDINALITY e
    WHERE e.value->>'action'=event_action AND e.value->>'target_type'=event_type;
  response:=request_row.response_json::jsonb;
  IF primary_index::text IS DISTINCT FROM origin_parts[2]
    OR primary_event->>'target_id' IS DISTINCT FROM request_row.target_id
    OR primary_event->'metadata' IS DISTINCT FROM event_metadata
    OR response->>(event_type||'_id') IS DISTINCT FROM request_row.target_id
    OR response->'replayed' IS DISTINCT FROM 'false'::jsonb THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  IF event_type='device' THEN
    SELECT d.xmin::text,d.state,d.updated_at INTO target_xid,target_state,target_updated FROM public.identity_devices d
      WHERE d.tenant_id=tenant AND d.user_id=actor AND d.device_id=request_row.target_id;
  ELSE
    SELECT s.xmin::text,s.state,s.updated_at INTO target_xid,target_state,target_updated FROM public.identity_sessions s
      WHERE s.tenant_id=tenant AND s.user_id=actor AND s.session_id=request_row.target_id;
  END IF;
  -- revoked는 정상 본인 보안 사건이다. 현재 조회자는 활성이어야 하나 원 target session의 활성을 요구하지 않는다.
  accessible:=target_xid IS NOT NULL AND EXISTS(
    SELECT 1 FROM public.identity_memberships m JOIN public.identity_users u ON u.user_id=m.user_id
    JOIN public.user_accounts ua ON ua.tenant_id=m.tenant_id AND ua.user_id=m.user_id AND ua.status='active'
    JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id
    JOIN public.workspaces cw ON cw.tenant_id=w.tenant_id AND cw.workspace_id=w.workspace_id
    WHERE m.tenant_id=tenant AND m.user_id=actor AND u.state='active' AND w.workspace_id=workspace
      AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
        WHERE o.tenant_id=tenant AND o.user_id=actor AND w.workspace_kind='organization' AND o.state<>'active')
      AND CASE WHEN EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles r
          WHERE r.tenant_id=tenant AND r.user_id=actor AND r.state='active')
        THEN EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles r
          WHERE r.tenant_id=tenant AND r.user_id=actor AND r.state='active'
            AND ((w.workspace_kind='personal' AND r.role='personal_owner')
              OR (w.workspace_kind='organization' AND r.role='organization_admin')))
        ELSE EXISTS(SELECT 1 FROM public.identity_auth_memberships am
          WHERE am.tenant_id=tenant AND am.workspace_id=workspace AND am.user_id=actor AND am.state='active'
            AND am.role IN ('workspace_admin','editor','reviewer','approver','viewer')) END);
  RETURN jsonb_build_object('origin_event_id',p_origin,'request_event_id',request_row.event_id,
    'actor_id',actor,'operation',request_row.operation,'action',event_action,'target_type',event_type,
    'target_id',request_row.target_id,'kind',event_kind,'severity',event_severity,
    'trace_id',request_row.trace_id,'policy_version',request_row.policy_version,'occurred_at',request_row.created_at,
    'request_xid',request_xid,'request_pending',request_row.delivered_at IS NULL,
    'target_xid',target_xid,'target_state',target_state,'target_updated_at',target_updated,
    'trust_version',CASE WHEN request_row.operation='device.trust' THEN response->>'version' ELSE NULL END,
    'accessible',accessible);
END $$;
ALTER FUNCTION public.current_account_notification_event(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_account_notification_event(text) FROM PUBLIC,daon_app;

CREATE FUNCTION public.enqueue_account_notification(p_origin text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  event jsonb; source_id text; inserted integer; record_xid numeric;
  top_xid numeric:=pg_current_xact_id()::text::numeric;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='42501';
  END IF;
  event:=public.current_account_notification_event(p_origin);
  IF event IS NULL OR event->'request_pending' IS DISTINCT FROM 'true'::jsonb THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  -- 이미 commit된 요청/대상에 새 감사 문자열만 붙여 재발행하지 않는다. 하위 savepoint도 허용한다.
  FOREACH record_xid IN ARRAY ARRAY[(event->>'request_xid')::numeric,(event->>'target_xid')::numeric] LOOP
    IF record_xid IS NULL OR pg_xact_status((top_xid-mod(top_xid,4294967296)+record_xid+
      CASE WHEN record_xid-mod(top_xid,4294967296)< -2147483648 THEN 4294967296
        WHEN record_xid-mod(top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8)
        IS DISTINCT FROM 'in progress' THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
    END IF;
  END LOOP;
  IF event->>'target_state' IS DISTINCT FROM (CASE WHEN event->>'operation'='device.trust' THEN 'trusted' ELSE 'revoked' END)
    OR (event->>'operation'='device.trust' AND (event->>'trust_version')::timestamptz IS DISTINCT FROM (event->>'target_updated_at')::timestamptz)
    OR (event->>'target_updated_at')::timestamptz>(event->>'occurred_at')::timestamptz THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  IF event->'accessible' IS DISTINCT FROM 'true'::jsonb THEN RETURN false; END IF;
  source_id:='identity-event-'||substr(encode(sha256(convert_to(p_origin||'|'||workspace,'UTF8')),'hex'),1,32);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,recipient_id,source_store,
    origin_event_id,action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,source_id,actor,'identity',p_origin,event->>'action',event->>'target_type',event->>'target_id',
    event->>'kind',event->>'severity',NULL,event->>'trace_id',event->>'policy_version',(event->>'occurred_at')::timestamptz)
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF NOT EXISTS(SELECT 1 FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.source_event_id=source_id AND s.recipient_id=actor AND s.source_store='identity'
    AND s.origin_event_id=p_origin AND s.action=event->>'action' AND s.target_type=event->>'target_type'
    AND s.target_id=event->>'target_id' AND s.kind=event->>'kind' AND s.severity=event->>'severity'
    AND s.notebook_id IS NULL AND s.trace_id=event->>'trace_id' AND s.policy_version=event->>'policy_version'
    AND s.occurred_at=(event->>'occurred_at')::timestamptz) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_account_notification(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_account_notification(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_account_notification(text) TO daon_app;
-- S42_ACCOUNT_NOTIFICATION_END

-- S42_ACCOUNT_NOTIFICATION_CONSUMER_BEGIN
CREATE FUNCTION public.pending_account_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit<1 OR p_limit>100 THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'') AND s.state='pending'
      AND s.source_store='identity' AND s.notebook_id IS NULL
      AND ((s.action='identity.device.trusted' AND s.target_type='device' AND s.kind='device' AND s.severity='info')
        OR (s.action='identity.device.revoked' AND s.target_type='device' AND s.kind='device' AND s.severity='warning')
        OR (s.action='identity.session.revoked' AND s.target_type='session' AND s.kind='authentication' AND s.severity='warning'))
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE SKIP LOCKED;
END $$;
ALTER FUNCTION public.pending_account_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_account_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_account_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.current_account_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; event jsonb; title text; summary text;
BEGIN
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_event_id=p_source AND s.state='pending' AND s.source_store='identity';
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  event:=public.current_account_notification_event(source.origin_event_id);
  IF event IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  IF source.notebook_id IS NOT NULL
    OR source.source_event_id IS DISTINCT FROM 'identity-event-'||substr(encode(sha256(convert_to(source.origin_event_id||'|'||workspace,'UTF8')),'hex'),1,32)
    OR (source.action,source.target_type,source.target_id,source.kind,source.severity,source.trace_id,source.policy_version,source.occurred_at)
      IS DISTINCT FROM (event->>'action',event->>'target_type',event->>'target_id',event->>'kind',event->>'severity',
        event->>'trace_id',event->>'policy_version',(event->>'occurred_at')::timestamptz) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END IF;
  -- bool/원 session의 현재 활성 상태가 아니라 현재 본인 자료 권한을 검사한다.
  IF event->'accessible' IS DISTINCT FROM 'true'::jsonb THEN RETURN NULL; END IF;
  CASE event->>'operation'
    WHEN 'device.trust' THEN title:='기기 신뢰 설정'; summary:='본인 기기의 신뢰 설정이 변경되었습니다.';
    WHEN 'device.revoke' THEN title:='기기 철회'; summary:='본인 기기가 철회되었습니다.';
    WHEN 'session.revoke' THEN title:='세션 철회'; summary:='본인 세션이 철회되었습니다.';
    ELSE RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514';
  END CASE;
  RETURN jsonb_build_object('title',title,'summary',summary,'deep_link','/settings/account');
END $$;
ALTER FUNCTION public.current_account_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_account_notification_target(text) FROM PUBLIC,daon_app;

CREATE FUNCTION public.materialize_account_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb; notification text; draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_event_id=p_source AND s.state='pending' AND s.source_store='identity'
    AND s.notebook_id IS NULL AND ((s.action='identity.device.trusted' AND s.target_type='device')
      OR (s.action='identity.device.revoked' AND s.target_type='device')
      OR (s.action='identity.session.revoked' AND s.target_type='session')) FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_account_notification_target(p_source);
  IF target IS NOT NULL AND NOT p_deliver THEN RETURN NULL; END IF;
  IF target IS NULL THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  notification:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,notification,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,
    kind,severity,title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
  VALUES(tenant,notification,'identity',source.origin_event_id,p_source,source.kind,source.severity,
    target->>'title',target->>'summary',source.target_type,source.target_id,NULL,'/settings/account',
    source.trace_id,source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO draft FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=notification AND a.action='notification.created';
  IF draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  RETURN jsonb_build_object('state','delivered','notification_id',notification,'draft',draft);
END $$;
ALTER FUNCTION public.materialize_account_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_account_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_account_notification(text,boolean) TO daon_app;
-- S42_ACCOUNT_NOTIFICATION_CONSUMER_END

-- S42_STUDIO_NOTICE_NOTIFICATION_BEGIN
-- S24 notice/읽음/version은 원장 하나만 유지한다. 부모/detail/source 사본이나
-- 부모 권한을 추가하지 않고 알림 감사 의도만 같은 transaction에 기록한다.
CREATE FUNCTION public.current_studio_notice_notification_target(p_notice text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  selected_notebook text:=nullif(current_setting('app.notebook_id',true),'');
  old_notebook text:=current_setting('app.notebook_id',true);
  notice public.studio_approval_notices; request public.approval_requests;
  notebook text; candidates text[]; role text; source record; allowed boolean:=true;
  organization_policy jsonb; workspace_policy jsonb; expiry timestamptz;
BEGIN
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL OR p_notice IS NULL THEN RETURN NULL; END IF;
  SELECT n.* INTO notice FROM public.studio_approval_notices n
    WHERE n.tenant_id=tenant AND n.workspace_id=workspace AND n.recipient_id=actor AND n.notification_id=p_notice;
  IF notice.notification_id IS NULL THEN RETURN NULL; END IF;
  SELECT r.* INTO request FROM public.approval_requests r WHERE r.tenant_id=tenant
    AND r.workspace_id=workspace AND r.record_id=notice.approval_request_id AND r.created_by=actor;
  IF request.record_id IS NULL OR notice.notification_id IS DISTINCT FROM
    'approval-notice-'||md5(tenant||'|'||workspace||'|'||request.record_id||'|'||notice.event_kind) THEN RETURN NULL; END IF;
  expiry:=public.studio_approval_deadline(request.canonical_json,request.created_at);
  IF (notice.event_kind='due_24h' AND (request.state<>'pending' OR clock_timestamp()<notice.scheduled_at OR clock_timestamp()>=expiry))
    OR (notice.event_kind='expired' AND request.state<>'expired')
    OR (notice.event_kind='withdrawn' AND request.state<>'withdrawn') THEN RETURN NULL; END IF;
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN CASE
      WHEN (tr.role='personal_owner' AND w.workspace_kind='personal')
        OR (tr.role='organization_admin' AND w.workspace_kind='organization') THEN tr.role END
      ELSE m.role END INTO role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=actor AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active'
    WHERE w.tenant_id=tenant AND w.workspace_id=workspace
      AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o WHERE o.tenant_id=tenant
        AND o.user_id=actor AND w.workspace_kind='organization' AND o.state<>'active');
  IF role IS NULL OR role NOT IN ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m JOIN public.identity_users u ON u.user_id=m.user_id
      JOIN public.user_accounts ua ON ua.tenant_id=m.tenant_id AND ua.user_id=m.user_id AND ua.status='active'
      WHERE m.tenant_id=tenant AND m.user_id=actor AND u.state='active') THEN RETURN NULL; END IF;
  -- 정상 호출은 이미 결속된 Notebook을 사용한다. background는 본인 binding
  -- 하나를 정확히 입증해야 하며 임의 첫 행을 선택하지 않는다.
  SELECT array_agg(x.notebook_id) INTO candidates FROM (
    SELECT DISTINCT n.notebook_id FROM public.notebooks n JOIN public.notebook_bindings b
      ON b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id
    JOIN public.output_versions v ON v.tenant_id=b.tenant_id AND v.workspace_id=b.workspace_id AND v.record_id=b.record_id
    WHERE n.tenant_id=tenant AND n.workspace_id=workspace AND n.created_by=actor
      AND b.binding_kind='output_version' AND b.record_id=request.output_version_id
      AND (selected_notebook IS NULL OR n.notebook_id=selected_notebook)
      AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=tenant AND d.workspace_id=workspace
        AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed')) LIMIT 2
  ) x;
  IF coalesce(cardinality(candidates),0)<>1 THEN RETURN NULL; END IF;
  notebook:=candidates[1];
  -- 기존 S24 _version_access/_require_generation_scope의 비어 있지 않은
  -- 근거·현재 binding·명시 사용자 deny·삭제 규칙을 그대로 검사한다.
  IF NOT EXISTS(SELECT 1 FROM public.evidence_references e WHERE e.tenant_id=tenant AND e.workspace_id=workspace
      AND e.output_version_id=request.output_version_id)
    OR EXISTS(SELECT 1 FROM public.evidence_references e
      LEFT JOIN public.source_versions sv ON sv.tenant_id=e.tenant_id AND sv.workspace_id=e.workspace_id AND sv.record_id=e.source_version_id
      LEFT JOIN public.sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id
      WHERE e.tenant_id=tenant AND e.workspace_id=workspace AND e.output_version_id=request.output_version_id
        AND (sv.record_id IS NULL OR s.record_id IS NULL
          OR EXISTS(SELECT 1 FROM public.identity_auth_source_access a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
            AND a.user_id=actor AND a.source_version_id=sv.record_id AND a.allowed=0)
          OR EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=tenant AND d.workspace_id=workspace
            AND d.source_id=s.record_id AND d.source_active=false)
          OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=tenant AND b.workspace_id=workspace
            AND b.notebook_id=notebook AND b.binding_kind='source' AND b.record_id=s.record_id AND b.version_id=sv.record_id)
          OR EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=tenant AND u.workspace_id=workspace
            AND u.notebook_id=notebook AND u.source_id=s.record_id AND u.source_version_id=sv.record_id))) THEN RETURN NULL; END IF;
  PERFORM set_config('app.notebook_id',notebook,true);
  FOR source IN SELECT DISTINCT sv.record_id,sv.source_id,sv.version,sv.canonical_json
    FROM public.evidence_references e JOIN public.source_versions sv
      ON sv.tenant_id=e.tenant_id AND sv.workspace_id=e.workspace_id AND sv.record_id=e.source_version_id
    WHERE e.tenant_id=tenant AND e.workspace_id=workspace AND e.output_version_id=request.output_version_id
  LOOP
    IF source.canonical_json->>'origin_kind'='website' THEN
      allowed:=EXISTS(SELECT 1 FROM public.website_source_snapshots s JOIN public.sources src
        ON src.tenant_id=s.tenant_id AND src.workspace_id=s.workspace_id AND src.record_id=s.source_id
        WHERE s.tenant_id=tenant AND s.workspace_id=workspace AND s.notebook_id=notebook AND s.created_by=actor
          AND s.source_version_id=source.record_id AND src.state='ready'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=tenant AND newer.workspace_id=workspace
            AND newer.source_id=s.source_id AND newer.version>source.version));
    ELSIF source.canonical_json->>'origin_kind'='mcp_open_law' THEN
      -- 환경상 기능 가용성은 기존 Python reader가 검사하고 저장된 자료의
      -- owner/connector/실효 egress 보호는 SQL에서도 독립 검사한다.
      allowed:=source.canonical_json->>'connector_id'='mcp-open-law-go-kr' AND EXISTS(
        SELECT 1 FROM public.open_law_source_snapshots s JOIN public.sources src
          ON src.tenant_id=s.tenant_id AND src.workspace_id=s.workspace_id AND src.record_id=s.source_id
        JOIN public.workspace_connectors c ON c.tenant_id=s.tenant_id AND c.workspace_id=s.workspace_id AND c.connector_id=s.connector_id
        WHERE s.tenant_id=tenant AND s.workspace_id=workspace AND s.notebook_id=notebook AND s.created_by=actor
          AND s.snapshot_id=source.canonical_json->>'snapshot_id' AND s.source_id=source.source_id AND s.source_version_id=source.record_id
          AND src.state='ready' AND s.raw_sha256=source.canonical_json->>'raw_sha256' AND s.text_sha256=source.canonical_json->>'text_sha256'
          AND c.status='connected' AND c.verification_json->>'protocol_version'='2025-11-25'
          AND c.verification_json->>'tool_schema_digest'='df8c707ca4e47b8e22645c409d9bfb6a8fbe3038dabe4eaa44d6ee74457a8d88'
          AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=tenant AND newer.workspace_id=workspace
            AND newer.source_id=s.source_id AND newer.version>source.version) FOR SHARE OF c);
      SELECT p.canonical_json INTO organization_policy FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p
        ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id WHERE b.tenant_id=tenant AND b.organization_id=tenant
          AND b.workspace_id IS NULL AND b.scope_type='organization' AND b.active AND b.current AND p.state='active'
          AND p.organization_id=tenant AND p.workspace_id IS NULL AND p.scope_type='organization';
      SELECT p.canonical_json INTO workspace_policy FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p
        ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id WHERE b.tenant_id=tenant AND b.organization_id=tenant
          AND b.workspace_id=workspace AND b.scope_type='workspace' AND b.active AND b.current AND p.state='active'
          AND p.organization_id=tenant AND p.workspace_id=workspace AND p.scope_type='workspace';
      allowed:=allowed AND organization_policy IS NOT NULL AND workspace_policy IS NOT NULL
        AND organization_policy->>'mode'='allow_approved_external' AND workspace_policy->>'mode'='allow_approved_external'
        AND organization_policy->>'classification'='public' AND workspace_policy->>'classification'='public'
        AND organization_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb
        AND workspace_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb
        AND organization_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb
        AND workspace_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb
        AND coalesce((organization_policy->>'max_bytes')::bigint,0)>0 AND coalesce((workspace_policy->>'max_bytes')::bigint,0)>0
        AND organization_policy->>'required_approver' IN ('workspace_manager','organization_admin')
        AND workspace_policy->>'required_approver' IN ('workspace_manager','organization_admin')
        AND role IN ('personal_owner','organization_admin','workspace_admin')
        AND (('organization_admin' NOT IN (organization_policy->>'required_approver',workspace_policy->>'required_approver'))
          OR role IN ('personal_owner','organization_admin'));
    END IF;
    EXIT WHEN allowed IS DISTINCT FROM true;
  END LOOP;
  PERFORM set_config('app.notebook_id',coalesce(old_notebook,''),true);
  IF allowed IS DISTINCT FROM true THEN RETURN NULL; END IF;
  -- 자료/connector 검사에서 기다린 뒤에는 과거 state/시각을 재사용하지 않는다.
  IF notice.event_kind='due_24h' THEN
    SELECT r.* INTO request FROM public.approval_requests r WHERE r.tenant_id=tenant
      AND r.workspace_id=workspace AND r.record_id=notice.approval_request_id AND r.created_by=actor;
    IF request.record_id IS NULL OR request.state<>'pending' OR clock_timestamp()<notice.scheduled_at
      OR clock_timestamp()>=public.studio_approval_deadline(request.canonical_json,request.created_at) THEN RETURN NULL; END IF;
  END IF;
  RETURN jsonb_build_object('notebook_id',notebook,'output_version_id',request.output_version_id,
    'event_kind',notice.event_kind,'created_at',notice.created_at,'read_at',notice.read_at,'version',notice.version);
END $$;
ALTER FUNCTION public.current_studio_notice_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_studio_notice_notification_target(text) FROM PUBLIC,daon_app;

CREATE FUNCTION public.valid_studio_notice_notification_audit(p_row public.notification_audit_outbox)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE notice public.studio_approval_notices; expected text; projection timestamptz; occurred timestamptz;
  kind text; metadata jsonb:=p_row.draft->'metadata';
BEGIN
  IF p_row.tenant_id IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
    OR p_row.workspace_id IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'')
    OR p_row.recipient_id IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') THEN RETURN false; END IF;
  SELECT n.* INTO notice FROM public.studio_approval_notices n WHERE n.tenant_id=p_row.tenant_id
    AND n.workspace_id=p_row.workspace_id AND n.recipient_id=p_row.recipient_id AND n.notification_id=p_row.notification_id;
  IF notice.notification_id IS NULL OR p_row.source_event_id IS DISTINCT FROM notice.notification_id
    OR jsonb_typeof(metadata) IS DISTINCT FROM 'object'
    OR (SELECT count(*) FROM jsonb_object_keys(metadata))<>4
    OR NOT metadata ?& ARRAY['source_event_id','notice_id','notice_created_at','projection_at']
    OR jsonb_typeof(metadata->'notice_created_at') IS DISTINCT FROM 'string'
    OR jsonb_typeof(metadata->'projection_at') IS DISTINCT FROM 'string'
    OR metadata->>'source_event_id' IS DISTINCT FROM notice.notification_id
    OR metadata->>'notice_id' IS DISTINCT FROM notice.notification_id THEN RETURN false; END IF;
  BEGIN
    projection:=(metadata->>'projection_at')::timestamptz;
    occurred:=(p_row.draft->>'occurred_at')::timestamptz;
    IF (metadata->>'notice_created_at')::timestamptz IS DISTINCT FROM notice.created_at THEN RETURN false; END IF;
  EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN false;
  END;
  IF projection IS NULL OR projection<notice.created_at OR projection>clock_timestamp()
    OR occurred IS NULL OR jsonb_typeof(p_row.draft) IS DISTINCT FROM 'object'
    OR (SELECT count(*) FROM jsonb_object_keys(p_row.draft))<>15 THEN RETURN false; END IF;
  IF p_row.action='notification.created' THEN
    expected:='audit-notification-created-'||md5(p_row.tenant_id||'|'||notice.notification_id);
    kind:='approval';
    IF occurred IS DISTINCT FROM projection OR p_row.draft->>'actor_id' IS DISTINCT FROM 'notification-service'
      OR p_row.draft->>'actor_type' IS DISTINCT FROM 'service' OR p_row.draft->'before' IS DISTINCT FROM 'null'::jsonb
      OR p_row.draft->'after' IS DISTINCT FROM jsonb_build_object('recipient_id',notice.recipient_id,'kind',kind,'delivery_state','delivered') THEN RETURN false; END IF;
  ELSIF p_row.action='notification.read' THEN
    expected:='audit-notification-read-'||substr(encode(sha256(convert_to(p_row.tenant_id||'|'||p_row.workspace_id||'|'||notice.recipient_id||'|'||notice.notification_id,'UTF8')),'hex'),1,32);
    IF notice.read_at IS NULL OR notice.version<>2 OR occurred IS DISTINCT FROM notice.read_at OR projection<notice.read_at
      OR p_row.draft->>'actor_id' IS DISTINCT FROM notice.recipient_id OR p_row.draft->>'actor_type' IS DISTINCT FROM 'user'
      OR p_row.draft->'before' IS DISTINCT FROM jsonb_build_object('read_at',NULL,'version',1)
      OR p_row.draft->'after' IS DISTINCT FROM jsonb_build_object('read_at',notice.read_at,'version',2) THEN RETURN false; END IF;
  ELSE RETURN false;
  END IF;
  RETURN p_row.event_id=expected AND p_row.draft->>'event_id'=expected
    AND p_row.draft->>'tenant_id'=p_row.tenant_id AND p_row.draft->>'workspace_id'=p_row.workspace_id
    AND p_row.draft->>'action'=p_row.action AND p_row.draft->>'target_type'='notification'
    AND p_row.draft->>'target_id'=notice.notification_id AND p_row.draft->>'outcome'='succeeded'
    AND p_row.draft->>'trace_id' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    AND p_row.draft->>'policy_version' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    AND p_row.fingerprint=encode(sha256(convert_to(p_row.draft::text,'UTF8')),'hex');
END $$;
ALTER FUNCTION public.valid_studio_notice_notification_audit(public.notification_audit_outbox) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.valid_studio_notice_notification_audit(public.notification_audit_outbox) FROM PUBLIC,daon_app;

CREATE FUNCTION public.record_studio_notice_notification(p_notice text,p_action text,p_trace text,p_policy text,p_read_key text DEFAULT NULL)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  notice public.studio_approval_notices; existing public.notification_audit_outbox;
  target jsonb; payload jsonb; metadata jsonb; event text; kind text; projection timestamptz;
  top_xid numeric; record_xid numeric; proof record; inserted integer;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF p_notice IS NULL OR p_action IS NULL OR p_action NOT IN ('notification.created','notification.read')
    OR p_trace IS NULL OR p_trace !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR p_policy IS NULL OR p_policy !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  target:=public.current_studio_notice_notification_target(p_notice);
  IF target IS NULL THEN
    IF p_action='notification.read' THEN RAISE EXCEPTION 'NOTIFICATION_RESOURCE_UNAVAILABLE' USING ERRCODE='P0002'; END IF;
    RETURN false;
  END IF;
  SELECT n.* INTO notice FROM public.studio_approval_notices n WHERE n.tenant_id=tenant AND n.workspace_id=workspace
    AND n.recipient_id=actor AND n.notification_id=p_notice;
  IF p_action='notification.read' THEN
    -- 최초 native UPDATE와 정확한 _finish 영수증/원감사는 중첩 savepoint를
    -- 포함하여 모두 현재 transaction의 미commit 행이어야 한다.
    top_xid:=pg_current_xact_id()::text::numeric;
    SELECT n.xmin::text AS notice_xid,i.xmin::text AS receipt_xid,a.xmin::text AS audit_xid INTO proof
      FROM public.studio_approval_notices n JOIN public.idempotency_records i ON i.tenant_id=n.tenant_id
        AND i.workspace_id=n.workspace_id AND i.actor_id=n.recipient_id AND i.operation='studio.approval.notice.read'
        AND i.idempotency_key=p_read_key AND i.status='completed'
      JOIN public.audit_events a ON a.tenant_id=n.tenant_id AND a.workspace_id=n.workspace_id AND a.actor_id=n.recipient_id
        AND a.event_id='audit-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|studio.approval.notice.read|'||p_read_key,'UTF8')),'hex'),1,32)
        AND a.action='studio.approval.notice.read' AND a.target_type='StudioApprovalNotice' AND a.target_id=n.notification_id
        AND a.outcome='succeeded' AND a.trace_id=p_trace AND a.policy_version=p_policy
      WHERE n.tenant_id=tenant AND n.workspace_id=workspace AND n.recipient_id=actor AND n.notification_id=p_notice
        AND n.version=2 AND n.read_at=CURRENT_TIMESTAMP
        AND i.result->>'notification_id'=n.notification_id AND (i.result->>'version')::integer=n.version
        AND (i.result->>'read_at')::timestamptz=n.read_at
        AND i.request_fingerprint=encode(sha256(convert_to(replace(jsonb_build_object('request',jsonb_build_array(
          p_notice,1,target->>'notebook_id'))::text,' ',''),'UTF8')),'hex');
    IF NOT FOUND THEN RAISE EXCEPTION 'NOTIFICATION_READ_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    FOREACH record_xid IN ARRAY ARRAY[proof.notice_xid::numeric,proof.receipt_xid::numeric,proof.audit_xid::numeric] LOOP
      IF record_xid IS NULL OR pg_xact_status((top_xid-mod(top_xid,4294967296)+record_xid+
        CASE WHEN record_xid-mod(top_xid,4294967296)< -2147483648 THEN 4294967296
          WHEN record_xid-mod(top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8) IS DISTINCT FROM 'in progress' THEN
        RAISE EXCEPTION 'NOTIFICATION_READ_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    END LOOP;
    PERFORM public.record_studio_notice_notification(p_notice,'notification.created',p_trace,p_policy,NULL);
  END IF;
  event:=CASE p_action WHEN 'notification.created' THEN 'audit-notification-created-'||md5(tenant||'|'||p_notice)
    ELSE 'audit-notification-read-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_notice,'UTF8')),'hex'),1,32) END;
  SELECT a.* INTO existing FROM public.notification_audit_outbox a WHERE a.event_id=event;
  IF existing.event_id IS NOT NULL THEN
    IF existing.notification_id IS DISTINCT FROM p_notice OR existing.action IS DISTINCT FROM p_action
      OR public.valid_studio_notice_notification_audit(existing) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
    -- read의 true는 정확한 감사 의도의 존재를 보장한다. 최초 INSERT 여부만
    -- 보고 원읽음의 원자성 판단을 누락하지 않는다. created 중복은 false다.
    RETURN p_action='notification.read';
  END IF;
  projection:=clock_timestamp();
  metadata:=jsonb_build_object('source_event_id',p_notice,'notice_id',p_notice,'notice_created_at',notice.created_at,'projection_at',projection);
  kind:='approval';
  payload:=jsonb_build_object('event_id',event,'occurred_at',CASE WHEN p_action='notification.created' THEN projection ELSE notice.read_at END,
    'actor_id',CASE WHEN p_action='notification.created' THEN 'notification-service' ELSE actor END,
    'actor_type',CASE WHEN p_action='notification.created' THEN 'service' ELSE 'user' END,
    'tenant_id',tenant,'workspace_id',workspace,'action',p_action,'target_type','notification','target_id',p_notice,
    'outcome','succeeded','trace_id',p_trace,'policy_version',p_policy,
    'before',CASE WHEN p_action='notification.read' THEN jsonb_build_object('read_at',NULL,'version',1) ELSE NULL END,
    'after',CASE WHEN p_action='notification.read' THEN jsonb_build_object('read_at',notice.read_at,'version',2)
      ELSE jsonb_build_object('recipient_id',actor,'kind',kind,'delivery_state','delivered') END,'metadata',metadata);
  INSERT INTO public.notification_audit_outbox(event_id,tenant_id,workspace_id,recipient_id,notification_id,source_event_id,action,draft,fingerprint)
    VALUES(event,tenant,workspace,actor,p_notice,p_notice,p_action,payload,encode(sha256(convert_to(payload::text,'UTF8')),'hex'))
    ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT a.* INTO existing FROM public.notification_audit_outbox a WHERE a.event_id=event;
    IF existing.event_id IS NULL OR existing.notification_id IS DISTINCT FROM p_notice OR existing.action IS DISTINCT FROM p_action
      OR public.valid_studio_notice_notification_audit(existing) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1 OR p_action='notification.read';
END $$;
ALTER FUNCTION public.record_studio_notice_notification(text,text,text,text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.record_studio_notice_notification(text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_studio_notice_notification(text,text,text,text,text) TO daon_app;

-- 앱은 감사 outbox를 직접 읽지 않는다. ack 여부와 무관한 원 저장값 네 개만
-- 현재 본인 notice/접근/created 의도에 결속하여 읽는다. GET 부수 쓰기0.
CREATE FUNCTION public.current_studio_notice_notification_audit(p_notice text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  stored public.notification_audit_outbox;
BEGIN
  IF public.current_studio_notice_notification_target(p_notice) IS NULL THEN RETURN NULL; END IF;
  SELECT a.* INTO stored FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=p_notice AND a.source_event_id=p_notice AND a.action='notification.created'
    AND a.event_id='audit-notification-created-'||md5(tenant||'|'||p_notice) AND a.state IN ('pending','delivered');
  IF stored.event_id IS NULL THEN RETURN NULL; END IF;
  IF public.valid_studio_notice_notification_audit(stored) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  RETURN jsonb_build_object('audit_event_id',stored.event_id,'trace_id',stored.draft->>'trace_id',
    'policy_version',stored.draft->>'policy_version','projection_at',stored.draft->'metadata'->>'projection_at');
END $$;
ALTER FUNCTION public.current_studio_notice_notification_audit(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_studio_notice_notification_audit(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_studio_notice_notification_audit(text) TO daon_app;

CREATE FUNCTION public.capture_studio_notice_notification() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE trace text; policy text;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN NEW; END IF;
  IF TG_RELID<>'public.studio_approval_notices'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'AFTER'
    OR NEW.tenant_id IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
    OR NEW.workspace_id IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'')
    OR NEW.recipient_id IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  -- HTTP 문맥 없는 DB sweep은 이 projection의 새 실제 trace를 사용한다.
  -- 원 요청의 과거 trace나 과거 중앙 전달 성공을 합성하지 않는다.
  trace:=coalesce(nullif(current_setting('app.notification_trace_id',true),''),'trace-studio-notice-'||replace(gen_random_uuid()::text,'-',''));
  policy:=coalesce(nullif(current_setting('app.notification_policy_version',true),''),'s42-studio-notice-v1');
  PERFORM public.record_studio_notice_notification(NEW.notification_id,'notification.created',trace,policy,NULL);
  RETURN NEW;
END $$;
ALTER FUNCTION public.capture_studio_notice_notification() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.capture_studio_notice_notification() FROM PUBLIC,daon_app;
CREATE TRIGGER studio_notice_notification_created AFTER INSERT ON public.studio_approval_notices
FOR EACH ROW EXECUTE FUNCTION public.capture_studio_notice_notification();
-- S42_STUDIO_NOTICE_NOTIFICATION_END

-- S42_ORGANIZATION_ROLE_NOTIFICATION_BEGIN
-- 기존 조직 원장은 관리자 actor RLS를 유지한다. 원장 SELECT를 앱에 넓히지 않고
-- 기존 조직 owner의 정확 event/tenant/변경대상 조회만 알림 owner에게 위임한다.
DO $organization_notification_preflight$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_organization_owner'
      AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
    OR pg_catalog.pg_has_role('daon_app','daon_organization_owner','MEMBER')
    OR pg_catalog.pg_has_role('daon_notebook_delete','daon_organization_owner','MEMBER')
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid='public.identity_org_operation_outbox'::regclass
      AND relowner='daon_organization_owner'::regrole AND relrowsecurity AND relforcerowsecurity)
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.identity_auth_memberships','SELECT')
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.identity_org_tenant_memberships','SELECT') THEN
    RAISE EXCEPTION 'ORGANIZATION_NOTIFICATION_PRECONDITION' USING ERRCODE='55000';
  END IF;
END $organization_notification_preflight$;

CREATE FUNCTION public.current_organization_role_notification_origin(p_event text,p_recipient text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  origin public.identity_org_operation_outbox; response jsonb; audit jsonb; origin_xid text;
BEGIN
  -- GUC는 backend가 설정한 범위이지 독립 인증 증명이 아니다. 원장 target도 반드시 일치한다.
  IF tenant IS NULL OR actor IS NULL OR actor IS DISTINCT FROM p_recipient
    OR tenant IS DISTINCT FROM nullif(current_setting('daon.organization_tenant',true),'')
    OR p_event IS NULL OR p_event !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR p_recipient IS NULL OR p_recipient !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN RETURN NULL; END IF;
  SELECT o.* INTO origin FROM public.identity_org_operation_outbox o WHERE o.event_id=p_event
    AND o.tenant_id=tenant AND o.target_id=p_recipient
    AND o.operation IN ('organization.member.role','organization.member.state')
    AND o.actor_id=nullif(current_setting('daon.organization_actor',true),'');
  IF origin.event_id IS NULL THEN RETURN NULL; END IF;
  response:=origin.response_json::jsonb; audit:=origin.audit_json::jsonb;
  IF public.valid_identity_org_operation(origin.operation,origin.response_json,origin.audit_json) IS DISTINCT FROM true
    OR response->>'tenant_id' IS DISTINCT FROM tenant OR response->>'user_id' IS DISTINCT FROM p_recipient
    OR response->>'role' IS DISTINCT FROM 'member' OR response->>'state' IS DISTINCT FROM 'active'
    OR response->>'access_state' IS DISTINCT FROM 'active'
    OR response->>'join_workspace_id' IS NULL OR response->>'join_workspace_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR response->>'access_role' IS NULL OR response->>'access_role' NOT IN ('viewer','editor','reviewer','approver')
    OR audit->>'action' IS DISTINCT FROM (CASE origin.operation
      WHEN 'organization.member.role' THEN 'organization.membership.role_changed'
      WHEN 'organization.member.state' THEN 'organization.membership.state_changed' END)
    OR audit->>'workspace_id' IS DISTINCT FROM response->>'join_workspace_id'
    OR (origin.operation='organization.member.role' AND
      (audit->>'before' IS NULL OR audit->>'before' NOT IN ('viewer','editor','reviewer','approver')
        OR audit->'after' IS DISTINCT FROM response->'access_role'))
    OR (origin.operation='organization.member.state' AND
      (audit->>'before' IS DISTINCT FROM 'suspended' OR audit->'after' IS DISTINCT FROM response->'state'))
    OR audit->'version' IS DISTINCT FROM response->'version'
    OR audit->'auth_version' IS DISTINCT FROM response->'access_version'
    OR origin.trace_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR origin.policy_version !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514';
  END IF;
  -- 동일 실제 workflow/auth version에 별도 원장 ID를 붙인 복사 이력을 선택하지 않는다.
  IF EXISTS(SELECT 1 FROM public.identity_org_operation_outbox o WHERE o.tenant_id=tenant AND o.target_id=p_recipient
    AND o.operation=origin.operation AND o.event_id<>origin.event_id
    AND o.response_json::jsonb->>'join_workspace_id'=response->>'join_workspace_id'
    AND o.response_json::jsonb->'version'=response->'version'
    AND o.response_json::jsonb->'access_version'=response->'access_version') THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514';
  END IF;
  SELECT o.xmin::text INTO origin_xid FROM public.identity_org_operation_outbox o WHERE o.event_id=origin.event_id;
  RETURN jsonb_build_object('origin_event_id',origin.event_id,'tenant_id',origin.tenant_id,
    'workspace_id',response->>'join_workspace_id','recipient_id',origin.target_id,'actor_id',origin.actor_id,
    'action',audit->>'action',
    'before_role',CASE WHEN origin.operation='organization.member.role' THEN audit->>'before' END,
    'after_role',response->>'access_role',
    'before_state',CASE WHEN origin.operation='organization.member.state' THEN audit->>'before' END,
    'after_state',CASE WHEN origin.operation='organization.member.state' THEN audit->>'after' END,
    'workflow_version',response->'version','auth_version',response->'access_version',
    'reason_digest',encode(sha256(convert_to((audit->'reason')::text,'UTF8')),'hex'),
    'trace_id',origin.trace_id,'policy_version',origin.policy_version,'occurred_at',origin.created_at,
    'origin_xid',origin_xid,'delivered_at',origin.delivered_at);
END $$;
ALTER FUNCTION public.current_organization_role_notification_origin(text,text) OWNER TO daon_organization_owner;
REVOKE ALL ON FUNCTION public.current_organization_role_notification_origin(text,text) FROM PUBLIC,daon_app;
GRANT EXECUTE ON FUNCTION public.current_organization_role_notification_origin(text,text) TO daon_notebook_delete;

CREATE FUNCTION public.enqueue_organization_role_notification(p_event text,p_recipient text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  origin jsonb; workflow record; membership record; auth_workspace record;
  effective_role text; top_xid numeric; record_xid numeric; occurred timestamptz; source_id text;
  previous public.notification_source_outbox; inserted integer;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL OR actor IS DISTINCT FROM p_recipient
    OR tenant !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR workspace !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  origin:=public.current_organization_role_notification_origin(p_event,p_recipient);
  IF origin IS NULL OR origin->>'workspace_id' IS DISTINCT FROM workspace
    OR origin->>'actor_id' IS DISTINCT FROM nullif(current_setting('daon.organization_actor',true),'')
    OR tenant IS DISTINCT FROM nullif(current_setting('daon.organization_tenant',true),'')
    OR origin->'delivered_at' IS DISTINCT FROM 'null'::jsonb THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  occurred:=(origin->>'occurred_at')::timestamptz;
  SELECT f.user_id,f.role,f.state,f.version,f.join_workspace_id,f.updated_at,f.xmin::text AS xid
    INTO workflow FROM public.identity_org_tenant_memberships f WHERE f.tenant_id=tenant AND f.user_id=actor;
  SELECT m.user_id,m.role,m.state,m.version,m.updated_at,m.xmin::text AS xid INTO membership
    FROM public.identity_auth_memberships m WHERE m.tenant_id=tenant AND m.workspace_id=workspace AND m.user_id=actor;
  SELECT w.workspace_id,w.workspace_kind,w.updated_at,w.xmin::text AS xid INTO auth_workspace
    FROM public.identity_auth_workspaces w WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  IF workflow.user_id IS NULL OR membership.user_id IS NULL OR auth_workspace.workspace_id IS NULL
    OR workflow.role<>'member' OR workflow.state<>'active' OR workflow.join_workspace_id IS DISTINCT FROM workspace
    OR auth_workspace.workspace_kind<>'organization' OR membership.state<>'active'
    OR workflow.version IS DISTINCT FROM (origin->>'workflow_version')::integer
    OR membership.version IS DISTINCT FROM (origin->>'auth_version')::integer
    OR membership.role IS DISTINCT FROM origin->>'after_role'
    OR workflow.updated_at IS DISTINCT FROM occurred
    OR (origin->>'action'='organization.membership.role_changed' AND
      (membership.updated_at IS DISTINCT FROM occurred OR auth_workspace.updated_at IS DISTINCT FROM occurred)) THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  -- 실제 원업무 source만 허용한다. 중첩 savepoint의 xmin도 같은 미commit transaction으로 검사한다.
  top_xid:=pg_current_xact_id()::text::numeric;
  -- 상태 전이는 workflow만 갱신한다. 역할 전이의 기존 auth/Workspace 동일Tx 검사는 유지한다.
  FOREACH record_xid IN ARRAY (CASE WHEN origin->>'action'='organization.membership.state_changed'
    THEN ARRAY[(origin->>'origin_xid')::numeric,workflow.xid::numeric]
    ELSE ARRAY[(origin->>'origin_xid')::numeric,workflow.xid::numeric,membership.xid::numeric,auth_workspace.xid::numeric] END) LOOP
    IF record_xid IS NULL OR pg_xact_status((top_xid-mod(top_xid,4294967296)+record_xid+
      CASE WHEN record_xid-mod(top_xid,4294967296)< -2147483648 THEN 4294967296
        WHEN record_xid-mod(top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8) IS DISTINCT FROM 'in progress' THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  END LOOP;
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN CASE WHEN tr.role='organization_admin' THEN tr.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=actor AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  IF effective_role IS NULL OR effective_role NOT IN ('organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor AND m.role='member') THEN RETURN false; END IF;
  -- 서로 다른 관리자 RLS에 원장이 숨겨져도 같은 실제 전이의 중복 source는 PK로 막는다.
  -- 원Event ID는 별도 origin_event_id로 보존한다. 원장을 복제해도 전이 version은 같아야 한다.
  source_id:='organization-event-'||substr(encode(sha256(convert_to(
    tenant||'|'||workspace||'|'||actor||'|'||workflow.version::text||'|'||membership.version::text,'UTF8')),'hex'),1,32);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,source_store,
    action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,source_id,p_event,'organization',origin->>'action','membership',actor,
    actor,'membership','info',NULL,origin->>'trace_id',origin->>'policy_version',occurred) ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
      AND s.recipient_id=actor AND s.source_event_id=source_id;
    IF previous.source_event_id IS NULL OR (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,
      previous.target_id,previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
      IS DISTINCT FROM (p_event,'organization'::text,origin->>'action','membership'::text,
        actor,'membership'::text,'info'::text,NULL::text,origin->>'trace_id',origin->>'policy_version',occurred) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_organization_role_notification(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_organization_role_notification(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_organization_role_notification(text,text) TO daon_app;
-- S42_ORGANIZATION_ROLE_NOTIFICATION_END

-- S42_ORGANIZATION_NOTIFICATION_CONSUMER_BEGIN
CREATE FUNCTION public.pending_organization_role_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'')
      AND s.state='pending' AND s.source_store='organization'
      AND s.action IN ('organization.membership.role_changed','organization.membership.state_changed')
      AND s.target_type='membership'
      AND s.target_id=s.recipient_id AND s.kind='membership' AND s.severity='info' AND s.notebook_id IS NULL
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE SKIP LOCKED;
END $$;
ALTER FUNCTION public.pending_organization_role_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_organization_role_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_organization_role_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.current_organization_role_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  previous_manager text:=current_setting('daon.organization_actor',true);
  previous_tenant text:=current_setting('daon.organization_tenant',true);
  source public.notification_source_outbox; central public.security_audit_events; origin jsonb;
  accessible boolean; effective_role text;
BEGIN
  IF p_source IS NULL OR p_source !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.source_store='organization' AND s.state IN ('pending','delivered');
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  IF source.action NOT IN ('organization.membership.role_changed','organization.membership.state_changed')
    OR (source.target_type,source.target_id,source.kind,source.severity,source.notebook_id)
    IS DISTINCT FROM ('membership'::text,actor,
      'membership'::text,'info'::text,NULL::text) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  -- 원중앙감사 미전달이면 source를 억제/성공으로 바꾸지 않고 pending으로 남긴다.
  SELECT a.* INTO central FROM public.security_audit_events a
    WHERE a.event_id=source.origin_event_id AND a.tenant_id=tenant;
  IF central.event_id IS NULL THEN RETURN jsonb_build_object('state','awaiting_origin'); END IF;
  IF (central.workspace_id,central.action,central.target_type,central.target_id,central.outcome,central.actor_type,
      central.trace_id,central.policy_version,central.occurred_at)
    IS DISTINCT FROM (workspace,source.action,'membership'::text,actor,
      'succeeded'::text,'user'::text,source.trace_id,source.policy_version,source.occurred_at)
    OR central.actor_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  BEGIN
    PERFORM set_config('daon.organization_actor',central.actor_id,true);
    PERFORM set_config('daon.organization_tenant',tenant,true);
    origin:=public.current_organization_role_notification_origin(source.origin_event_id,actor);
    PERFORM set_config('daon.organization_actor',coalesce(previous_manager,''),true);
    PERFORM set_config('daon.organization_tenant',coalesce(previous_tenant,''),true);
  EXCEPTION WHEN OTHERS THEN
    PERFORM set_config('daon.organization_actor',coalesce(previous_manager,''),true);
    PERFORM set_config('daon.organization_tenant',coalesce(previous_tenant,''),true);
    RAISE;
  END;
  IF origin IS NULL
    OR (origin->>'origin_event_id',origin->>'tenant_id',origin->>'workspace_id',origin->>'recipient_id',
        origin->>'actor_id',origin->>'action',origin->>'trace_id',origin->>'policy_version',(origin->>'occurred_at')::timestamptz)
      IS DISTINCT FROM (source.origin_event_id,tenant,workspace,actor,central.actor_id,source.action,
        source.trace_id,source.policy_version,source.occurred_at)
    OR central.before_value IS DISTINCT FROM jsonb_build_object('value',CASE
      WHEN source.action='organization.membership.state_changed' THEN origin->>'before_state' ELSE origin->>'before_role' END)
    OR central.after_value IS DISTINCT FROM jsonb_build_object('value',CASE
      WHEN source.action='organization.membership.state_changed' THEN origin->>'after_state' ELSE origin->>'after_role' END)
    OR central.metadata->'version' IS DISTINCT FROM origin->'workflow_version'
    OR central.metadata->'auth_version' IS DISTINCT FROM origin->'auth_version'
    OR encode(sha256(convert_to((central.metadata->'reason')::text,'UTF8')),'hex') IS DISTINCT FROM origin->>'reason_digest'
    OR (SELECT count(*) FROM jsonb_object_keys(central.metadata))<>3 OR central.safe_code IS NOT NULL
    OR p_source IS DISTINCT FROM 'organization-event-'||substr(encode(sha256(convert_to(
      tenant||'|'||workspace||'|'||actor||'|'||(origin->>'workflow_version')||'|'||(origin->>'auth_version'),'UTF8')),'hex'),1,32) THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN
      CASE WHEN tr.role='organization_admin' AND w.workspace_kind='organization' THEN tr.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=actor AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  accessible:=effective_role IS NOT NULL AND effective_role IN
      ('organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    AND EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships f
      WHERE f.tenant_id=tenant AND f.user_id=actor AND f.state<>'active');
  RETURN jsonb_build_object('accessible',accessible,'title',CASE source.action
      WHEN 'organization.membership.state_changed' THEN '조직 소속 상태 변경' ELSE '작업공간 역할 변경' END,
    'summary',CASE source.action WHEN 'organization.membership.state_changed'
      THEN '본인의 조직 소속 상태가 변경되었습니다.' ELSE '본인의 작업공간 역할이 변경되었습니다.' END,'deep_link','/notifications',
    'source_event_id',source.source_event_id,'origin_event_id',source.origin_event_id,
    'target_type',source.target_type,'target_id',source.target_id,'kind',source.kind,'severity',source.severity,
    'trace_id',source.trace_id,'policy_version',source.policy_version,'occurred_at',source.occurred_at,'action',source.action);
END $$;
ALTER FUNCTION public.current_organization_role_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_organization_role_notification_target(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_organization_role_notification_target(text) TO daon_app;

CREATE FUNCTION public.materialize_organization_role_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb; notification text; draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.state='pending' AND s.source_store='organization' FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_organization_role_notification_target(p_source);
  IF target->>'state'='awaiting_origin' THEN RETURN NULL; END IF;
  IF target IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  IF target->'accessible'='true'::jsonb AND NOT p_deliver THEN RETURN NULL; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  notification:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,notification,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,
    kind,severity,title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
  VALUES(tenant,notification,'organization',source.origin_event_id,p_source,source.kind,source.severity,
    target->>'title',target->>'summary',source.target_type,source.target_id,NULL,'/notifications',
    source.trace_id,source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO draft FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=notification AND a.action='notification.created';
  IF draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  RETURN jsonb_build_object('state','delivered','notification_id',notification,'draft',draft);
END $$;
ALTER FUNCTION public.materialize_organization_role_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_organization_role_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_organization_role_notification(text,boolean) TO daon_app;
-- S42_ORGANIZATION_NOTIFICATION_CONSUMER_END

-- S42_IDENTITY_LOGIN_NOTIFICATION_BEGIN
CREATE FUNCTION public.enqueue_identity_login_notification(p_event text,p_oidc text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  original public.security_audit_events; login_session record; login_device record; oidc_record record;
  family record; refresh_record record; identity_issuer text; effective_role text;
  top_xid numeric:=pg_current_xact_id()::text::numeric; record_xid numeric; xids text[];
  source_id text; inserted integer; previous public.notification_source_outbox;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL OR p_event IS NULL
    OR p_event !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR (p_oidc IS NOT NULL AND p_oidc !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT a.* INTO original FROM public.security_audit_events a
    WHERE a.event_id=p_event AND a.tenant_id=tenant AND a.actor_id=actor;
  IF original.event_id IS NULL OR original.workspace_id IS NOT NULL
    OR (original.action,original.actor_type,original.target_type,original.outcome)
      IS DISTINCT FROM ('identity.login.succeeded'::text,'user'::text,'session'::text,'succeeded'::text)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL OR original.safe_code IS NOT NULL
    OR original.trace_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR original.policy_version !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT s.session_id,s.device_id,s.user_id,s.tenant_id,s.client_kind,s.state,s.created_at,s.updated_at,s.xmin::text AS xid
    INTO login_session FROM public.identity_sessions s WHERE s.session_id=original.target_id
      AND s.tenant_id=tenant AND s.user_id=actor;
  IF login_session.session_id IS NULL OR login_session.state<>'active'
    OR login_session.created_at IS DISTINCT FROM login_session.updated_at
    OR login_session.client_kind NOT IN ('web','native') THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT d.device_id,d.user_id,d.tenant_id,d.platform,d.state,d.created_at,d.updated_at,d.xmin::text AS xid
    INTO login_device FROM public.identity_devices d WHERE d.device_id=login_session.device_id
      AND d.tenant_id=tenant AND d.user_id=actor;
  IF login_device.device_id IS NULL OR login_device.state<>'registered'
    OR login_device.created_at IS DISTINCT FROM login_session.created_at
    OR login_device.updated_at IS DISTINCT FROM login_device.created_at THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT u.issuer INTO identity_issuer FROM public.identity_users u WHERE u.user_id=actor AND u.state='active';
  IF identity_issuer IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  xids:=ARRAY[login_session.xid,login_device.xid];
  IF original.metadata=jsonb_build_object('client_type',login_session.client_kind,'auth_method','local') THEN
    IF identity_issuer<>'local' OR p_oidc IS NOT NULL
      OR (login_session.client_kind='web' AND login_device.platform<>'web')
      OR (login_session.client_kind='native' AND login_device.platform<>'windows') THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  ELSIF original.metadata=jsonb_build_object('client_type',login_session.client_kind) THEN
    IF identity_issuer='local' OR p_oidc IS NULL THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    SELECT o.transaction_id,o.issuer,o.client_kind,o.used_at,o.xmin::text AS xid
      INTO oidc_record FROM public.identity_oidc_transactions o
      WHERE o.transaction_id=p_oidc AND o.tenant_id=tenant;
    IF oidc_record.transaction_id IS NULL OR oidc_record.issuer IS DISTINCT FROM identity_issuer
      OR oidc_record.client_kind IS DISTINCT FROM login_session.client_kind
      OR oidc_record.used_at IS DISTINCT FROM login_session.created_at THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    xids:=array_append(xids,oidc_record.xid);
  ELSE RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  IF login_session.client_kind='native' THEN
    IF (SELECT count(*) FROM public.identity_refresh_families f WHERE f.session_id=login_session.session_id)<>1 THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    SELECT f.family_id,f.state,f.created_at,f.xmin::text AS xid INTO family
      FROM public.identity_refresh_families f WHERE f.session_id=login_session.session_id;
    IF family.state<>'active' OR family.created_at IS DISTINCT FROM login_session.created_at
      OR (SELECT count(*) FROM public.identity_refresh_tokens t WHERE t.family_id=family.family_id)<>1 THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    SELECT t.refresh_id,t.used_at,t.created_at,t.xmin::text AS xid INTO refresh_record
      FROM public.identity_refresh_tokens t WHERE t.family_id=family.family_id;
    IF refresh_record.used_at IS NOT NULL OR refresh_record.created_at IS DISTINCT FROM login_session.created_at THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    xids:=array_append(array_append(xids,family.xid),refresh_record.xid);
  END IF;
  -- 원중앙감사는 별도 commit이다. session/device/(native·OIDC)의 실제 원업무만 현재 transaction으로 증명한다.
  FOREACH record_xid IN ARRAY xids::numeric[] LOOP
    IF record_xid IS NULL OR pg_xact_status((top_xid-mod(top_xid,4294967296)+record_xid+
      CASE WHEN record_xid-mod(top_xid,4294967296)< -2147483648 THEN 4294967296
        WHEN record_xid-mod(top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8) IS DISTINCT FROM 'in progress' THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  END LOOP;
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN
      CASE WHEN tr.role='personal_owner' AND w.workspace_kind='personal' THEN tr.role
        WHEN tr.role='organization_admin' AND w.workspace_kind='organization' THEN tr.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=actor AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  IF effective_role IS NULL OR effective_role NOT IN
      ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships f
      JOIN public.identity_auth_workspaces w ON w.tenant_id=f.tenant_id AND w.workspace_id=workspace
      WHERE f.tenant_id=tenant AND f.user_id=actor AND w.workspace_kind='organization' AND f.state<>'active') THEN RETURN false; END IF;
  -- 같은 실제 session에 새 감사 ID를 붙여 source를 복제하지 못한다.
  IF EXISTS(SELECT 1 FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_store='identity' AND s.action='identity.login.succeeded'
    AND s.target_type='session' AND s.target_id=login_session.session_id AND s.origin_event_id<>p_event) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  source_id:='identity-login-event-'||substr(encode(sha256(convert_to(p_event||'|'||workspace,'UTF8')),'hex'),1,32);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,source_store,
    action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,source_id,p_event,'identity','identity.login.succeeded','session',login_session.session_id,
    actor,'authentication','info',NULL,original.trace_id,original.policy_version,original.occurred_at) ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
      AND s.recipient_id=actor AND s.source_event_id=source_id;
    IF previous.source_event_id IS NULL OR (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,
      previous.target_id,previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
      IS DISTINCT FROM (p_event,'identity'::text,'identity.login.succeeded'::text,'session'::text,
        login_session.session_id,'authentication'::text,'info'::text,NULL::text,original.trace_id,original.policy_version,original.occurred_at) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_identity_login_notification(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_identity_login_notification(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_identity_login_notification(text,text) TO daon_app;
-- S42_IDENTITY_LOGIN_NOTIFICATION_END

-- S42_IDENTITY_LOGIN_CONSUMER_BEGIN
CREATE FUNCTION public.pending_identity_login_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'')
      AND s.state='pending' AND s.source_store='identity'
      AND s.action='identity.login.succeeded' AND s.target_type='session'
      AND s.kind='authentication' AND s.severity='info' AND s.notebook_id IS NULL
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE SKIP LOCKED;
END $$;
ALTER FUNCTION public.pending_identity_login_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_identity_login_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_identity_login_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.current_identity_login_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; original public.security_audit_events;
  login_session record; issuer text; owned boolean:=false; accessible boolean; effective_role text;
BEGIN
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.source_store='identity' AND s.action='identity.login.succeeded' AND s.state IN ('pending','delivered');
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  IF (source.target_type,source.kind,source.severity,source.notebook_id)
    IS DISTINCT FROM ('session'::text,'authentication'::text,'info'::text,NULL::text)
    OR p_source IS DISTINCT FROM 'identity-login-event-'||substr(encode(sha256(convert_to(
      source.origin_event_id||'|'||workspace,'UTF8')),'hex'),1,32) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  SELECT a.* INTO original FROM public.security_audit_events a WHERE a.tenant_id=tenant
    AND a.event_id=source.origin_event_id AND a.actor_id=actor;
  IF original.event_id IS NULL OR original.workspace_id IS NOT NULL
    OR (original.action,original.actor_type,original.target_type,original.target_id,original.outcome,
        original.trace_id,original.policy_version,original.occurred_at)
      IS DISTINCT FROM (source.action,'user'::text,'session'::text,source.target_id,'succeeded'::text,
        source.trace_id,source.policy_version,source.occurred_at)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL OR original.safe_code IS NOT NULL
    OR original.metadata->>'client_type' IS NULL OR original.metadata->>'client_type' NOT IN ('web','native')
    OR (original.metadata IS DISTINCT FROM jsonb_build_object('client_type',original.metadata->>'client_type')
      AND original.metadata IS DISTINCT FROM jsonb_build_object('client_type',original.metadata->>'client_type','auth_method','local')) THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT s.session_id,s.device_id,s.tenant_id,s.user_id,s.client_kind INTO login_session
    FROM public.identity_sessions s WHERE s.session_id=source.target_id;
  SELECT u.issuer INTO issuer FROM public.identity_users u WHERE u.user_id=actor;
  IF login_session.session_id IS NOT NULL THEN
    IF (login_session.tenant_id,login_session.user_id,login_session.client_kind)
      IS DISTINCT FROM (tenant,actor,original.metadata->>'client_type')
      OR (issuer IS NOT NULL AND
        ((original.metadata ? 'auth_method' AND issuer<>'local')
          OR (NOT (original.metadata ? 'auth_method') AND issuer='local'))) THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    owned:=EXISTS(SELECT 1 FROM public.identity_devices d WHERE d.device_id=login_session.device_id
      AND d.tenant_id=tenant AND d.user_id=actor);
  END IF;
  -- 원로그인 session/device의 현재 철회는 본인 보안 이력을 없애지 않는다.
  -- 현재 본인 identity/auth/Workspace 접근은 별도로 다시 확인한다.
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN
      CASE WHEN tr.role='personal_owner' AND w.workspace_kind='personal' THEN tr.role
        WHEN tr.role='organization_admin' AND w.workspace_kind='organization' THEN tr.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=actor AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  accessible:=owned AND effective_role IS NOT NULL AND effective_role IN
      ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    AND EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships f
      JOIN public.identity_auth_workspaces w ON w.tenant_id=f.tenant_id AND w.workspace_id=workspace
      WHERE f.tenant_id=tenant AND f.user_id=actor AND w.workspace_kind='organization' AND f.state<>'active');
  RETURN jsonb_build_object('accessible',accessible,'title','로그인 완료',
    'summary','본인 계정의 로그인이 완료되었습니다.','deep_link','/settings/account',
    'source_event_id',source.source_event_id,'origin_event_id',source.origin_event_id,
    'target_type',source.target_type,'target_id',source.target_id,'kind',source.kind,'severity',source.severity,
    'trace_id',source.trace_id,'policy_version',source.policy_version,'occurred_at',source.occurred_at,'action',source.action);
END $$;
ALTER FUNCTION public.current_identity_login_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_identity_login_notification_target(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_identity_login_notification_target(text) TO daon_app;

CREATE FUNCTION public.materialize_identity_login_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb; notification text; draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.state='pending' AND s.source_store='identity' FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_identity_login_notification_target(p_source);
  IF target IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  IF target->'accessible'='true'::jsonb AND NOT p_deliver THEN RETURN NULL; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  notification:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,notification,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,
    kind,severity,title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
  VALUES(tenant,notification,'identity',source.origin_event_id,p_source,source.kind,source.severity,
    target->>'title',target->>'summary',source.target_type,source.target_id,NULL,'/settings/account',
    source.trace_id,source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO draft FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=notification AND a.action='notification.created';
  IF draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  RETURN jsonb_build_object('state','delivered','notification_id',notification,'draft',draft);
END $$;
ALTER FUNCTION public.materialize_identity_login_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_identity_login_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_identity_login_notification(text,boolean) TO daon_app;
-- S42_IDENTITY_LOGIN_CONSUMER_END

-- S42_C9_ROLE_SOURCE_BEGIN
CREATE FUNCTION public.enqueue_c9_role_notification(p_event text,p_manager text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  recipient text:=nullif(current_setting('app.actor_id',true),'');
  operation record; membership record; scope record; source_id text; inserted integer;
  top_xid text:=(pg_current_xact_id()::text::numeric % 4294967296)::text;
  previous public.notification_source_outbox;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF tenant IS NULL OR workspace IS NULL OR recipient IS NULL OR p_event IS NULL OR p_manager IS NULL
    OR current_setting('app.capability',true) IS DISTINCT FROM 'view' THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT o.*,o.xmin::text AS row_xid INTO operation FROM public.identity_auth_admin_role_operations o
    WHERE o.event_id=p_event AND o.actor_id=p_manager AND o.tenant_id=tenant
      AND o.workspace_id=workspace AND o.user_id=recipient AND o.outcome='changed';
  SELECT m.*,m.xmin::text AS row_xid INTO membership FROM public.identity_auth_memberships m
    WHERE m.tenant_id=tenant AND m.workspace_id=workspace AND m.user_id=recipient;
  SELECT w.*,w.xmin::text AS row_xid INTO scope FROM public.identity_auth_workspaces w
    WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  IF operation.event_id IS NULL OR membership.user_id IS NULL OR scope.workspace_id IS NULL
    OR operation.delivered_at IS NOT NULL OR operation.old_role=operation.new_role
    OR operation.new_version<>operation.old_version+1 OR NOT isfinite(operation.created_at)
    OR operation.event_id IS DISTINCT FROM 'auth-admin-role-'||encode(sha256(convert_to(
      operation.actor_id||'|'||operation.idempotency_key,'UTF8')),'hex')
    OR (membership.role,membership.state,membership.version,membership.updated_at)
      IS DISTINCT FROM (operation.new_role,'active'::text,operation.new_version,operation.created_at)
    OR scope.acl_version IS DISTINCT FROM operation.acl_version
    OR operation.row_xid IS DISTINCT FROM top_xid OR membership.row_xid IS DISTINCT FROM top_xid
    OR scope.row_xid IS DISTINCT FROM top_xid THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  -- 원장 가변성/DB 앱 자격 위협을 해결하는 인증 증명은 아니다. 서버는 같은 연결의 VIEW도 검사한다.
  IF NOT EXISTS(SELECT 1 FROM public.identity_memberships m
    JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
    JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
    JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
    WHERE m.tenant_id=tenant AND m.user_id=recipient)
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      WHERE o.tenant_id=tenant AND o.user_id=recipient AND scope.workspace_kind='organization' AND o.state<>'active') THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  source_id:='c9-role-event-'||substr(encode(sha256(convert_to(
    p_event||'|'||workspace||'|'||recipient,'UTF8')),'hex'),1,32);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,
    source_store,action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,source_id,p_event,'authorization','authorization.membership.changed','membership',
    recipient,recipient,'membership','info',NULL,'not-recorded','not-recorded',operation.created_at)
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
      AND s.workspace_id=workspace AND s.recipient_id=recipient AND s.source_event_id=source_id;
    IF (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,previous.target_id,
      previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
      IS DISTINCT FROM (p_event,'authorization'::text,'authorization.membership.changed'::text,'membership'::text,
        recipient,'membership'::text,'info'::text,NULL::text,'not-recorded'::text,'not-recorded'::text,operation.created_at) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_c9_role_notification(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_c9_role_notification(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_c9_role_notification(text,text) TO daon_app;
-- S42_C9_ROLE_SOURCE_END

-- S42_C9_ROLE_CONSUMER_BEGIN
CREATE FUNCTION public.pending_c9_role_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'')
      AND s.state='pending' AND s.source_store='authorization'
      AND s.action='authorization.membership.changed' AND s.target_type='membership'
      AND s.kind='membership' AND s.severity='info' AND s.notebook_id IS NULL
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE SKIP LOCKED;
END $$;
ALTER FUNCTION public.pending_c9_role_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_c9_role_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_c9_role_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.current_c9_role_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; operation public.identity_auth_admin_role_operations;
  original public.security_audit_events; effective_role text; accessible boolean;
BEGIN
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.source_store='authorization' AND s.action='authorization.membership.changed'
    AND s.state IN ('pending','delivered');
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  IF (source.target_type,source.target_id,source.kind,source.severity,source.notebook_id,
    source.trace_id,source.policy_version) IS DISTINCT FROM
    ('membership'::text,actor,'membership'::text,'info'::text,NULL::text,'not-recorded'::text,'not-recorded'::text)
    OR p_source IS DISTINCT FROM 'c9-role-event-'||substr(encode(sha256(convert_to(
      source.origin_event_id||'|'||workspace||'|'||actor,'UTF8')),'hex'),1,32) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  SELECT o.* INTO operation FROM public.identity_auth_admin_role_operations o WHERE o.event_id=source.origin_event_id
    AND o.tenant_id=tenant AND o.workspace_id=workspace AND o.user_id=actor AND o.outcome='changed';
  IF operation.event_id IS NULL OR operation.old_role=operation.new_role
    OR operation.new_version<>operation.old_version+1 OR operation.created_at IS DISTINCT FROM source.occurred_at
    OR operation.event_id IS DISTINCT FROM 'auth-admin-role-'||encode(sha256(convert_to(
      operation.actor_id||'|'||operation.idempotency_key,'UTF8')),'hex') THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT a.* INTO original FROM public.security_audit_events a WHERE a.tenant_id=tenant AND a.event_id=operation.event_id;
  IF original.event_id IS NULL THEN RETURN jsonb_build_object('state','awaiting_origin'); END IF;
  IF (original.actor_id,original.actor_type,original.workspace_id,original.action,original.target_type,
    original.target_id,original.outcome,original.trace_id,original.policy_version,original.occurred_at)
    IS DISTINCT FROM (operation.actor_id,'user'::text,workspace,'authorization.membership.changed'::text,
      'membership'::text,actor,'succeeded'::text,'not-recorded'::text,'not-recorded'::text,operation.created_at)
    OR original.before_value IS DISTINCT FROM jsonb_build_object('role',operation.old_role,'version',operation.old_version)
    OR original.after_value IS DISTINCT FROM jsonb_build_object('role',operation.new_role,'version',operation.new_version)
    OR original.metadata IS DISTINCT FROM jsonb_build_object('reason_code',operation.reason,'outcome','changed',
      'acl_version',operation.acl_version) OR original.safe_code IS DISTINCT FROM operation.reason THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN
      CASE WHEN tr.role='personal_owner' AND w.workspace_kind='personal' THEN tr.role
        WHEN tr.role='organization_admin' AND w.workspace_kind='organization' THEN tr.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=actor AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  accessible:=effective_role IS NOT NULL AND effective_role IN
      ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    AND EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=workspace
      WHERE o.tenant_id=tenant AND o.user_id=actor AND w.workspace_kind='organization' AND o.state<>'active');
  RETURN jsonb_build_object('accessible',accessible,'title','작업공간 역할 변경',
    'summary','본인의 작업공간 역할이 변경되었습니다.','deep_link','/notifications',
    'source_event_id',source.source_event_id,'origin_event_id',source.origin_event_id,
    'target_type',source.target_type,'target_id',source.target_id,'kind',source.kind,'severity',source.severity,
    'trace_id',source.trace_id,'policy_version',source.policy_version,'occurred_at',source.occurred_at,'action',source.action);
END $$;
ALTER FUNCTION public.current_c9_role_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_c9_role_notification_target(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_c9_role_notification_target(text) TO daon_app;

CREATE FUNCTION public.materialize_c9_role_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb; notification text; draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.state='pending' AND s.source_store='authorization' FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_c9_role_notification_target(p_source);
  IF target IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  IF target=jsonb_build_object('state','awaiting_origin') THEN RETURN NULL; END IF;
  IF target->'accessible'='true'::jsonb AND NOT p_deliver THEN RETURN NULL; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  notification:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,notification,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,
    kind,severity,title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
  VALUES(tenant,notification,'authorization',source.origin_event_id,p_source,source.kind,source.severity,
    target->>'title',target->>'summary','membership',actor,NULL,'/notifications',source.trace_id,
    source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO draft FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=notification AND a.action='notification.created';
  IF draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  RETURN jsonb_build_object('state','delivered','notification_id',notification,'draft',draft);
END $$;
ALTER FUNCTION public.materialize_c9_role_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_c9_role_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_c9_role_notification(text,boolean) TO daon_app;
-- S42_C9_ROLE_CONSUMER_END

-- S42_EGRESS_POLICY_SOURCE_BEGIN
-- 내부 호출 전용 locator. 새 읽기 권한 없이 기존 Workspace RLS 문맥으로 원감사를 찾는다.
CREATE FUNCTION public.current_egress_policy_notification_origin(p_event text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  old_workspace text:=current_setting('app.workspace_id',true);
  candidate record; cursor_workspace text:=''; batch_count integer; matches integer:=0;
  found_origin jsonb; result jsonb;
BEGIN
  IF current_user<>'daon_notebook_delete' OR tenant IS NULL OR p_event IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  LOOP
    batch_count:=0;
    FOR candidate IN SELECT w.workspace_id FROM public.identity_auth_workspaces w
      WHERE w.tenant_id=tenant AND w.workspace_id>cursor_workspace
      ORDER BY w.workspace_id LIMIT 100
    LOOP
      batch_count:=batch_count+1;
      cursor_workspace:=candidate.workspace_id;
      PERFORM set_config('app.workspace_id',candidate.workspace_id,true);
      IF NOT EXISTS(SELECT 1 FROM public.workspaces w WHERE w.tenant_id=tenant
        AND w.workspace_id=candidate.workspace_id) THEN CONTINUE; END IF;
      SELECT to_jsonb(a)||jsonb_build_object('_row_xid',a.xmin::text) INTO found_origin
        FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=candidate.workspace_id
          AND a.event_id=p_event;
      IF found_origin IS NOT NULL THEN
        matches:=matches+1;
        result:=found_origin;
      END IF;
    END LOOP;
    EXIT WHEN batch_count<100;
  END LOOP;
  PERFORM set_config('app.workspace_id',coalesce(old_workspace,''),true);
  -- 인증 디렉터리/원감사 불완전은 없음이나 suppressed로 확정하지 않는다.
  IF matches<>1 THEN RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  RETURN result;
EXCEPTION WHEN OTHERS THEN
  PERFORM set_config('app.workspace_id',coalesce(old_workspace,''),true);
  RAISE;
END $$;
ALTER FUNCTION public.current_egress_policy_notification_origin(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_egress_policy_notification_origin(text) FROM PUBLIC;

CREATE FUNCTION public.enqueue_egress_policy_notification(p_event text,p_author text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  recipient text:=nullif(current_setting('app.actor_id',true),'');
  original public.audit_events; binding record; policy record; effective_role text; origin jsonb;
  audit_xid text; top_xid text:=(pg_current_xact_id()::text::numeric % 4294967296)::text;
  source_id text; inserted integer; previous public.notification_source_outbox;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF tenant IS NULL OR workspace IS NULL OR recipient IS NULL OR p_event IS NULL OR p_author IS NULL
    OR current_setting('app.capability',true) IS DISTINCT FROM 'view' THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT a.* INTO original FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.event_id=p_event AND a.actor_id=p_author AND a.action='egress_policy.activate'
    AND a.outcome='succeeded' AND a.target_type='EgressPolicyBinding';
  IF original.event_id IS NULL OR original.metadata->>'scope_type'='organization' THEN
    origin:=public.current_egress_policy_notification_origin(p_event);
    SELECT * INTO original FROM jsonb_populate_record(NULL::public.audit_events,origin);
    audit_xid:=origin->>'_row_xid';
  ELSE
    SELECT a.xmin::text INTO audit_xid FROM public.audit_events a WHERE a.tenant_id=tenant AND a.event_id=p_event;
  END IF;
  IF original.event_id IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT b.binding_id,b.policy_version_id,b.binding_version,b.organization_id,b.workspace_id,b.scope_type,
    b.created_by,b.trace_id,b.active,b.current,b.xmin::text AS row_xid INTO binding
    FROM public.egress_policy_bindings b WHERE b.tenant_id=tenant AND b.binding_id=original.target_id;
  SELECT p.policy_version_id,p.policy_version,p.organization_id,p.workspace_id,p.scope_type,p.state,
    p.created_by,p.trace_id,p.xmin::text AS row_xid INTO policy FROM public.egress_policy_versions p
    WHERE p.tenant_id=tenant AND p.policy_version_id=binding.policy_version_id;
  IF binding.binding_id IS NULL OR policy.policy_version_id IS NULL
    OR (original.tenant_id,original.actor_id,original.action,original.target_type,original.outcome)
      IS DISTINCT FROM (tenant,p_author,'egress_policy.activate'::text,'EgressPolicyBinding'::text,'succeeded'::text)
    OR binding.scope_type NOT IN ('workspace','organization')
    OR (binding.scope_type='workspace' AND original.workspace_id IS DISTINCT FROM workspace)
    OR (binding.scope_type='organization' AND NOT EXISTS(
      SELECT 1 FROM public.identity_auth_workspaces w JOIN public.egress_policy_bindings b
        ON b.tenant_id=w.tenant_id AND b.workspace_id=w.workspace_id AND b.organization_id=tenant
        AND b.scope_type='workspace' AND b.active AND b.current
      JOIN public.egress_policy_versions p ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        AND p.workspace_id=w.workspace_id AND p.organization_id=tenant AND p.scope_type='workspace' AND p.state='active'
      WHERE w.tenant_id=tenant AND w.workspace_id=workspace AND w.workspace_kind='organization'))
    OR (binding.organization_id,binding.workspace_id,binding.scope_type,binding.created_by,binding.trace_id,binding.active,binding.current)
      IS DISTINCT FROM (tenant,CASE WHEN binding.scope_type='workspace' THEN workspace END,binding.scope_type,p_author,original.trace_id,true,true)
    OR (policy.organization_id,policy.workspace_id,policy.scope_type,policy.created_by,policy.trace_id,policy.state)
      IS DISTINCT FROM (tenant,CASE WHEN binding.scope_type='workspace' THEN workspace END,binding.scope_type,p_author,original.trace_id,'active'::text)
    OR original.metadata IS DISTINCT FROM jsonb_build_object('scope_type',binding.scope_type,
      'policy_version_id',policy.policy_version_id,'binding_version',binding.binding_version)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL
    OR p_event IS DISTINCT FROM 'audit-egress-'||substr(encode(sha256(convert_to(
      tenant||chr(31)||original.workspace_id||chr(31)||p_author||chr(31)||'egress_policy.activate'||chr(31)||original.trace_id
        ||chr(31)||binding.binding_id||chr(31)||'succeeded','UTF8')),'hex'),1,32)
    OR binding.row_xid IS DISTINCT FROM top_xid OR policy.row_xid IS DISTINCT FROM top_xid
    OR audit_xid IS DISTINCT FROM top_xid THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND w.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND w.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id AND r.user_id=recipient AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=recipient AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  IF effective_role IS NULL OR effective_role NOT IN
    ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m
    JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
    JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
    JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
    WHERE m.tenant_id=tenant AND m.user_id=recipient)
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=workspace
      WHERE o.tenant_id=tenant AND o.user_id=recipient AND w.workspace_kind='organization' AND o.state<>'active') THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  source_id:='egress-policy-event-'||substr(encode(sha256(convert_to(
    p_event||'|'||workspace||'|'||recipient,'UTF8')),'hex'),1,32);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,
    source_store,action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,source_id,p_event,'authorization','egress_policy.activate','EgressPolicyBinding',
    binding.binding_id,recipient,'policy','info',NULL,original.trace_id,original.policy_version,original.occurred_at)
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
      AND s.workspace_id=workspace AND s.recipient_id=recipient AND s.source_event_id=source_id;
    IF (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,previous.target_id,
      previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
      IS DISTINCT FROM (p_event,'authorization'::text,'egress_policy.activate'::text,'EgressPolicyBinding'::text,
        binding.binding_id,'policy'::text,'info'::text,NULL::text,original.trace_id,original.policy_version,original.occurred_at) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_egress_policy_notification(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_egress_policy_notification(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_egress_policy_notification(text,text) TO daon_app;
-- S42_EGRESS_POLICY_SOURCE_END

-- S42_EGRESS_POLICY_CONSUMER_BEGIN
CREATE FUNCTION public.pending_egress_policy_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'')
      AND s.state='pending' AND s.source_store='authorization'
      AND s.action='egress_policy.activate' AND s.target_type='EgressPolicyBinding'
      AND s.kind='policy' AND s.severity='info' AND s.notebook_id IS NULL
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE SKIP LOCKED;
END $$;
ALTER FUNCTION public.pending_egress_policy_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_egress_policy_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_egress_policy_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.current_egress_policy_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; original public.audit_events;
  binding record; policy record; effective_role text; accessible boolean; origin jsonb;
BEGIN
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.source_store='authorization' AND s.action='egress_policy.activate'
    AND s.state IN ('pending','delivered');
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  IF (source.target_type,source.kind,source.severity,source.notebook_id)
    IS DISTINCT FROM ('EgressPolicyBinding'::text,'policy'::text,'info'::text,NULL::text)
    OR p_source IS DISTINCT FROM 'egress-policy-event-'||substr(encode(sha256(convert_to(
      source.origin_event_id||'|'||workspace||'|'||actor,'UTF8')),'hex'),1,32) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  SELECT a.* INTO original FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.event_id=source.origin_event_id;
  SELECT b.binding_id,b.policy_version_id,b.binding_version,b.organization_id,b.workspace_id,b.scope_type,
    b.created_by,b.trace_id INTO binding FROM public.egress_policy_bindings b
    WHERE b.tenant_id=tenant AND b.binding_id=source.target_id;
  SELECT p.policy_version_id,p.policy_version,p.organization_id,p.workspace_id,p.scope_type,p.created_by,p.trace_id
    INTO policy FROM public.egress_policy_versions p WHERE p.tenant_id=tenant AND p.policy_version_id=binding.policy_version_id;
  IF binding.scope_type='organization' THEN
    origin:=public.current_egress_policy_notification_origin(source.origin_event_id);
    SELECT * INTO original FROM jsonb_populate_record(NULL::public.audit_events,origin);
  END IF;
  IF original.event_id IS NULL OR binding.binding_id IS NULL OR policy.policy_version_id IS NULL
    OR original.tenant_id IS DISTINCT FROM tenant
    OR binding.scope_type NOT IN ('workspace','organization')
    OR (binding.scope_type='workspace' AND original.workspace_id IS DISTINCT FROM workspace)
    OR (original.action,original.target_type,original.target_id,original.outcome,original.trace_id,
      original.policy_version,original.occurred_at) IS DISTINCT FROM
      ('egress_policy.activate'::text,'EgressPolicyBinding'::text,source.target_id,'succeeded'::text,
        source.trace_id,source.policy_version,source.occurred_at)
    OR (binding.organization_id,binding.workspace_id,binding.scope_type,binding.created_by,binding.trace_id)
      IS DISTINCT FROM (tenant,CASE WHEN binding.scope_type='workspace' THEN workspace END,binding.scope_type,original.actor_id,source.trace_id)
    OR (policy.organization_id,policy.workspace_id,policy.scope_type,policy.created_by,policy.trace_id)
      IS DISTINCT FROM (tenant,CASE WHEN binding.scope_type='workspace' THEN workspace END,binding.scope_type,original.actor_id,source.trace_id)
    OR original.metadata IS DISTINCT FROM jsonb_build_object('scope_type',binding.scope_type,
      'policy_version_id',policy.policy_version_id,'binding_version',binding.binding_version)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL
    OR original.event_id IS DISTINCT FROM 'audit-egress-'||substr(encode(sha256(convert_to(
      tenant||chr(31)||original.workspace_id||chr(31)||original.actor_id||chr(31)||'egress_policy.activate'||chr(31)||source.trace_id
        ||chr(31)||source.target_id||chr(31)||'succeeded','UTF8')),'hex'),1,32) THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  -- 현재 binding으로 바꾸지 않는다. 과거 원 version의 변경 사실과 현재 수신 권한은 별개다.
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND w.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND w.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id AND r.user_id=actor AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  accessible:=effective_role IS NOT NULL AND effective_role IN
    ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    AND EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=workspace
      WHERE o.tenant_id=tenant AND o.user_id=actor AND w.workspace_kind='organization' AND o.state<>'active');
  IF binding.scope_type='organization' THEN
    accessible:=accessible AND EXISTS(
      SELECT 1 FROM public.identity_auth_workspaces w JOIN public.egress_policy_bindings b
        ON b.tenant_id=w.tenant_id AND b.workspace_id=w.workspace_id AND b.organization_id=tenant
        AND b.scope_type='workspace' AND b.active AND b.current
      JOIN public.egress_policy_versions p ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
        AND p.workspace_id=w.workspace_id AND p.organization_id=tenant AND p.scope_type='workspace' AND p.state='active'
      WHERE w.tenant_id=tenant AND w.workspace_id=workspace AND w.workspace_kind='organization');
  END IF;
  RETURN jsonb_build_object('accessible',accessible,'title','외부 전송 정책 변경',
    'summary','작업공간의 외부 전송 정책이 변경되었습니다.','deep_link','/notifications',
    'source_event_id',source.source_event_id,'origin_event_id',source.origin_event_id,
    'target_type',source.target_type,'target_id',source.target_id,'kind',source.kind,'severity',source.severity,
    'trace_id',source.trace_id,'policy_version',source.policy_version,'occurred_at',source.occurred_at,'action',source.action);
END $$;
ALTER FUNCTION public.current_egress_policy_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_egress_policy_notification_target(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_egress_policy_notification_target(text) TO daon_app;

CREATE FUNCTION public.materialize_egress_policy_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb; notification text; draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.state='pending' AND s.source_store='authorization' FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_egress_policy_notification_target(p_source);
  IF target IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  IF target->'accessible'='true'::jsonb AND NOT p_deliver THEN RETURN NULL; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  notification:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,notification,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,
    kind,severity,title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
  VALUES(tenant,notification,'authorization',source.origin_event_id,p_source,source.kind,source.severity,
    target->>'title',target->>'summary',source.target_type,source.target_id,NULL,'/notifications',source.trace_id,
    source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO draft FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=notification AND a.action='notification.created';
  IF draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  RETURN jsonb_build_object('state','delivered','notification_id',notification,'draft',draft);
END $$;
ALTER FUNCTION public.materialize_egress_policy_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_egress_policy_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_egress_policy_notification(text,boolean) TO daon_app;
-- S42_EGRESS_POLICY_CONSUMER_END

-- S42_SOURCE_ACCESS_SOURCE_BEGIN
CREATE FUNCTION public.source_access_notification_access(p_version text,p_notebook text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  version_row record; org_policy jsonb; workspace_policy jsonb; effective_role text;
BEGIN
  SELECT v.source_id,v.version,v.canonical_json,s.state INTO version_row FROM public.source_versions v
    JOIN public.sources s ON s.tenant_id=v.tenant_id AND s.workspace_id=v.workspace_id AND s.record_id=v.source_id
    WHERE v.tenant_id=tenant AND v.workspace_id=workspace AND v.record_id=p_version;
  IF version_row.source_id IS NULL OR version_row.state IN ('expired','disabled','deleting','deleted')
    OR EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=tenant AND d.workspace_id=workspace
      AND d.source_id=version_row.source_id AND d.source_active=false)
    OR EXISTS(SELECT 1 FROM public.identity_auth_source_access a WHERE a.tenant_id=tenant
      AND a.workspace_id=workspace AND a.user_id=actor AND a.source_version_id=p_version AND a.allowed=0) THEN RETURN false; END IF;
  IF coalesce(version_row.canonical_json->>'origin_kind','') NOT IN ('mcp_open_law','website') THEN
    RETURN p_notebook IS NULL;
  END IF;
  IF p_notebook IS NULL OR nullif(current_setting('app.notebook_id',true),'') IS DISTINCT FROM p_notebook
    OR version_row.state<>'ready'
    OR NOT EXISTS(SELECT 1 FROM public.notebooks n WHERE n.tenant_id=tenant AND n.workspace_id=workspace
      AND n.notebook_id=p_notebook AND n.created_by=actor)
    OR NOT EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=tenant AND b.workspace_id=workspace
      AND b.notebook_id=p_notebook AND b.binding_kind='source' AND b.record_id=version_row.source_id AND b.version_id=p_version)
    OR EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=tenant AND u.workspace_id=workspace
      AND u.notebook_id=p_notebook AND u.source_id=version_row.source_id AND u.source_version_id=p_version)
    OR EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=tenant AND d.workspace_id=workspace
      AND d.notebook_id=p_notebook AND d.state IN ('accepted','deleting','completed'))
    OR EXISTS(SELECT 1 FROM public.source_versions v WHERE v.tenant_id=tenant AND v.workspace_id=workspace
      AND v.source_id=version_row.source_id AND v.version>version_row.version) THEN RETURN false; END IF;
  IF version_row.canonical_json->>'origin_kind'='website' THEN
    RETURN EXISTS(SELECT 1 FROM public.website_source_snapshots w WHERE w.tenant_id=tenant AND w.workspace_id=workspace
      AND w.snapshot_id=version_row.canonical_json->>'snapshot_id' AND w.source_id=version_row.source_id
      AND w.source_version_id=p_version AND w.notebook_id=p_notebook AND w.created_by=actor
      AND w.raw_sha256=version_row.canonical_json->>'raw_sha256' AND w.text_sha256=version_row.canonical_json->>'text_sha256');
  END IF;
  IF version_row.canonical_json->>'connector_id' IS DISTINCT FROM 'mcp-open-law-go-kr'
    OR NOT EXISTS(SELECT 1 FROM public.open_law_source_snapshots w JOIN public.workspace_connectors c
      ON c.tenant_id=w.tenant_id AND c.workspace_id=w.workspace_id AND c.connector_id=w.connector_id
      WHERE w.tenant_id=tenant AND w.workspace_id=workspace AND w.source_version_id=p_version
      AND w.snapshot_id=version_row.canonical_json->>'snapshot_id' AND w.source_id=version_row.source_id
      AND w.notebook_id=p_notebook AND w.created_by=actor
      AND w.raw_sha256=version_row.canonical_json->>'raw_sha256' AND w.text_sha256=version_row.canonical_json->>'text_sha256'
      AND c.status='connected' AND c.verification_json->>'protocol_version'='2025-11-25'
      AND c.verification_json->>'tool_schema_digest'='df8c707ca4e47b8e22645c409d9bfb6a8fbe3038dabe4eaa44d6ee74457a8d88') THEN RETURN false; END IF;
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND w.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND w.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id AND r.user_id=actor AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  SELECT p.canonical_json INTO org_policy FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p
    ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
    WHERE b.tenant_id=tenant AND b.organization_id=tenant AND b.workspace_id IS NULL AND b.scope_type='organization'
      AND b.active AND b.current AND p.state='active' AND p.organization_id=tenant AND p.workspace_id IS NULL AND p.scope_type='organization';
  SELECT p.canonical_json INTO workspace_policy FROM public.egress_policy_bindings b JOIN public.egress_policy_versions p
    ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id
    WHERE b.tenant_id=tenant AND b.organization_id=tenant AND b.workspace_id=workspace AND b.scope_type='workspace'
      AND b.active AND b.current AND p.state='active' AND p.organization_id=tenant AND p.workspace_id=workspace AND p.scope_type='workspace';
  IF org_policy IS NULL OR workspace_policy IS NULL OR effective_role IS NULL
    OR org_policy->>'mode' IS DISTINCT FROM 'allow_approved_external' OR workspace_policy->>'mode' IS DISTINCT FROM 'allow_approved_external'
    OR org_policy->>'classification' IS DISTINCT FROM 'public' OR workspace_policy->>'classification' IS DISTINCT FROM 'public'
    OR (org_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
    OR (workspace_policy->'allowed_provider_kinds' @> '["external_api"]'::jsonb) IS DISTINCT FROM true
    OR (org_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
    OR (workspace_policy->'allowed_destinations' @> '["www.law.go.kr"]'::jsonb) IS DISTINCT FROM true
    OR coalesce((org_policy->>'max_bytes')::bigint,0)<=0 OR coalesce((workspace_policy->>'max_bytes')::bigint,0)<=0
    OR org_policy->>'required_approver' IS NULL OR workspace_policy->>'required_approver' IS NULL
    OR org_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
    OR workspace_policy->>'required_approver' NOT IN ('workspace_manager','organization_admin')
    OR ('organization_admin' IN (org_policy->>'required_approver',workspace_policy->>'required_approver')
      AND effective_role NOT IN ('personal_owner','organization_admin'))
    OR effective_role NOT IN ('personal_owner','organization_admin','workspace_admin') THEN RETURN false; END IF;
  RETURN true;
END $$;
ALTER FUNCTION public.source_access_notification_access(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.source_access_notification_access(text,text) FROM PUBLIC,daon_app;

CREATE FUNCTION public.enqueue_source_access_notification(p_event text,p_author text,p_version text,p_acl_version integer)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  recipient text:=nullif(current_setting('app.actor_id',true),'');
  original public.security_audit_events; acl record; ws record; effective_role text;
  top_xid text:=(pg_current_xact_id()::text::numeric % 4294967296)::text;
  source_id text; inserted integer; previous public.notification_source_outbox; notebook text;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF tenant IS NULL OR workspace IS NULL OR recipient IS NULL OR p_event IS NULL OR p_author IS NULL
    OR p_version IS NULL OR p_acl_version IS NULL OR p_acl_version<1
    OR current_setting('app.capability',true) IS DISTINCT FROM 'view' THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT a.* INTO original FROM public.security_audit_events a WHERE a.tenant_id=tenant
    AND a.workspace_id=workspace AND a.event_id=p_event AND a.actor_id=p_author;
  SELECT a.allowed,a.version,a.xmin::text AS xid INTO acl FROM public.identity_auth_source_access a
    WHERE a.tenant_id=tenant AND a.workspace_id=workspace AND a.user_id=recipient AND a.source_version_id=p_version;
  SELECT w.workspace_kind,w.xmin::text AS xid INTO ws FROM public.identity_auth_workspaces w
    WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  IF original.event_id IS NULL OR acl.version IS NULL OR ws.workspace_kind IS NULL
    OR (original.action,original.actor_type,original.target_type,original.target_id,original.outcome)
      IS DISTINCT FROM ('authorization.source_access.changed'::text,'user'::text,'source_access'::text,p_version,'succeeded'::text)
    OR original.metadata IS DISTINCT FROM '{}'::jsonb OR original.safe_code IS NOT NULL
    OR original.after_value IS DISTINCT FROM jsonb_build_object('allowed',acl.allowed=1,'version',p_acl_version)
    OR (p_acl_version=1 AND original.before_value IS NOT NULL)
    OR (p_acl_version>1 AND (jsonb_typeof(original.before_value->'allowed') IS DISTINCT FROM 'boolean'
      OR original.before_value- 'allowed' IS DISTINCT FROM jsonb_build_object('version',p_acl_version-1)))
    OR acl.version IS DISTINCT FROM p_acl_version OR acl.xid IS DISTINCT FROM top_xid OR ws.xid IS DISTINCT FROM top_xid THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  -- 부모 setter가 확인한 작성자와 수신자 역할을 구분한다. DB 앱 자격의 인증 증명은 아니다.
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND ws.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND ws.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM (SELECT p_author AS id) a
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=tenant AND r.user_id=a.id AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=tenant AND m.workspace_id=workspace
      AND m.user_id=a.id AND m.state='active';
  IF effective_role IS NULL OR effective_role NOT IN ('personal_owner','organization_admin','workspace_admin') THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND ws.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND ws.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM (SELECT recipient AS id) a
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=tenant AND r.user_id=a.id AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=tenant AND m.workspace_id=workspace
      AND m.user_id=a.id AND m.state='active';
  IF effective_role IS NULL OR effective_role NOT IN
    ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=recipient)
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o WHERE o.tenant_id=tenant
      AND o.user_id=recipient AND ws.workspace_kind='organization' AND o.state<>'active') THEN RETURN false; END IF;
  SELECT CASE WHEN v.canonical_json->>'origin_kind' IN ('mcp_open_law','website')
    THEN nullif(current_setting('app.notebook_id',true),'') END INTO notebook
    FROM public.source_versions v WHERE v.tenant_id=tenant AND v.workspace_id=workspace AND v.record_id=p_version;
  IF acl.allowed<>1 OR NOT public.source_access_notification_access(p_version,notebook) THEN RETURN false; END IF;
  source_id:='source-acl-event-'||substr(encode(sha256(convert_to(
    p_event||'|'||workspace||'|'||recipient,'UTF8')),'hex'),1,32);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,
    source_store,action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,source_id,p_event,'authorization','authorization.source_access.changed',
    'source_access',p_version,recipient,'membership','info',notebook,original.trace_id,original.policy_version,original.occurred_at)
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
      AND s.workspace_id=workspace AND s.recipient_id=recipient AND s.source_event_id=source_id;
    IF (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,previous.target_id,
      previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
      IS DISTINCT FROM (p_event,'authorization'::text,'authorization.source_access.changed'::text,'source_access'::text,
        p_version,'membership'::text,'info'::text,notebook,original.trace_id,original.policy_version,original.occurred_at) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_source_access_notification(text,text,text,integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_source_access_notification(text,text,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_source_access_notification(text,text,text,integer) TO daon_app;
-- S42_SOURCE_ACCESS_SOURCE_END

-- S42_SOURCE_ACCESS_CONSUMER_BEGIN
CREATE FUNCTION public.pending_source_access_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit<1 OR p_limit>100 THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'')
      AND s.state='pending' AND s.source_store='authorization'
      AND s.action='authorization.source_access.changed' AND s.target_type='source_access'
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE;
END $$;
ALTER FUNCTION public.pending_source_access_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_source_access_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_source_access_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.current_source_access_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; original public.security_audit_events;
  effective_role text; accessible boolean; source_accessible boolean;
  previous_notebook text:=current_setting('app.notebook_id',true);
BEGIN
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.source_store='authorization' AND s.action='authorization.source_access.changed'
    AND s.state IN ('pending','delivered');
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  IF (source.target_type,source.kind,source.severity)
    IS DISTINCT FROM ('source_access'::text,'membership'::text,'info'::text)
    OR p_source IS DISTINCT FROM 'source-acl-event-'||substr(encode(sha256(convert_to(
      source.origin_event_id||'|'||workspace||'|'||actor,'UTF8')),'hex'),1,32) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  SELECT a.* INTO original FROM public.security_audit_events a WHERE a.tenant_id=tenant
    AND a.workspace_id=workspace AND a.event_id=source.origin_event_id;
  IF original.event_id IS NULL OR (original.action,original.actor_type,original.target_type,original.target_id,
      original.outcome,original.trace_id,original.policy_version,original.occurred_at) IS DISTINCT FROM
      ('authorization.source_access.changed'::text,'user'::text,'source_access'::text,source.target_id,
        'succeeded'::text,source.trace_id,source.policy_version,source.occurred_at)
    OR original.metadata IS DISTINCT FROM '{}'::jsonb OR original.safe_code IS NOT NULL
    OR original.after_value->'allowed' IS DISTINCT FROM 'true'::jsonb
    OR jsonb_typeof(original.after_value->'version') IS DISTINCT FROM 'number'
    OR original.after_value->>'version' !~ '^[1-9][0-9]*$'
    OR (original.after_value-ARRAY['allowed','version']) IS DISTINCT FROM '{}'::jsonb THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  -- 불변 영수증의 원 after 버전과 현재 자료 접근을 구분한다. 최신 ACL 버전으로 원감사를 교체하지 않는다.
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND w.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND w.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id AND r.user_id=actor AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  PERFORM set_config('app.notebook_id',coalesce(source.notebook_id,''),true);
  source_accessible:=public.source_access_notification_access(source.target_id,source.notebook_id);
  PERFORM set_config('app.notebook_id',coalesce(previous_notebook,''),true);
  accessible:=effective_role IS NOT NULL AND effective_role IN
    ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    AND EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=workspace
      WHERE o.tenant_id=tenant AND o.user_id=actor AND w.workspace_kind='organization' AND o.state<>'active')
    AND source_accessible;
  RETURN jsonb_build_object('accessible',accessible,'title','자료 접근권한 변경',
    'summary','본인의 자료 접근권한이 변경되었습니다.','deep_link','/notifications',
    'source_event_id',source.source_event_id,'origin_event_id',source.origin_event_id,
    'target_type',source.target_type,'target_id',source.target_id,'kind',source.kind,'severity',source.severity,
    'trace_id',source.trace_id,'policy_version',source.policy_version,'occurred_at',source.occurred_at,'action',source.action);
END $$;
ALTER FUNCTION public.current_source_access_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_source_access_notification_target(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_source_access_notification_target(text) TO daon_app;

CREATE FUNCTION public.materialize_source_access_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb; notification text; draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.state='pending' AND s.source_store='authorization' FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_source_access_notification_target(p_source);
  IF target IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  IF target->'accessible'='true'::jsonb AND NOT p_deliver THEN RETURN NULL; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  notification:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,notification,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,
    kind,severity,title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
  VALUES(tenant,notification,'authorization',source.origin_event_id,p_source,source.kind,source.severity,
    target->>'title',target->>'summary',source.target_type,source.target_id,source.notebook_id,'/notifications',source.trace_id,
    source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO draft FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=notification AND a.action='notification.created';
  IF draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  RETURN jsonb_build_object('state','delivered','notification_id',notification,'draft',draft);
END $$;
ALTER FUNCTION public.materialize_source_access_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_source_access_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_source_access_notification(text,boolean) TO daon_app;
-- S42_SOURCE_ACCESS_CONSUMER_END

-- S42_RESTORE_COMPLETED_SOURCE_BEGIN
CREATE FUNCTION public.current_terminal_recovery_notification_origin(p_event text,p_author text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  recipient text:=nullif(current_setting('app.actor_id',true),'');
  original public.audit_events; business record; audit_xid text; effective_role text; accessible boolean;
  resource text; event_kind text; severity text; title text; summary text;
BEGIN
  IF current_user<>'daon_notebook_delete' OR tenant IS NULL OR workspace IS NULL OR recipient IS NULL
    OR p_event IS NULL OR p_author IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT a.* INTO original FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.event_id=p_event AND a.actor_id=p_author AND a.action IN ('backup.ready','restore.failed');
  SELECT a.xmin::text INTO audit_xid FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace AND a.event_id=p_event;
  IF original.event_id IS NULL OR original.metadata->>'previous_hash' IS NULL
    OR original.metadata->>'previous_hash' !~ '^[0-9a-f]{64}$'
    OR original.metadata->>'event_hash' IS NULL OR original.metadata->>'event_hash' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  IF original.action='backup.ready' THEN
    SELECT b.actor_id,b.state,b.version,b.audit_event_id,b.verified_at,b.xmin::text AS row_xid
      INTO business FROM public.backup_records b WHERE b.tenant_id=tenant AND b.workspace_id=workspace AND b.backup_id=original.target_id;
    resource:='backup'; event_kind:='recovery'; severity:='info'; title:='백업 준비 완료'; summary:='요청한 백업이 준비되었습니다.';
    IF business.actor_id IS NULL OR (original.target_type,original.outcome,business.state,business.version)
      IS DISTINCT FROM ('backup'::text,'succeeded'::text,'ready'::text,4)
      OR business.verified_at IS NULL
      OR original.before_value IS DISTINCT FROM jsonb_build_object('state','verifying','version',3)
      OR original.after_value IS DISTINCT FROM jsonb_build_object('state','ready','version',4)
      OR original.metadata-ARRAY['previous_hash','event_hash'] IS DISTINCT FROM '{}'::jsonb
      OR NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
        AND a.event_id=business.audit_event_id AND a.actor_id=recipient AND a.action='backup.requested'
        AND a.target_type='backup' AND a.target_id=original.target_id AND a.outcome='succeeded') THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  ELSE
    SELECT r.actor_id,r.state,r.version,r.audit_event_id,NULL::timestamptz AS verified_at,r.xmin::text AS row_xid
      INTO business FROM public.restore_requests r WHERE r.tenant_id=tenant AND r.workspace_id=workspace AND r.request_id=original.target_id;
    resource:='restore_request'; event_kind:='warning'; severity:='warning'; title:='복구 실패'; summary:='요청한 복구 작업을 완료하지 못했습니다.';
    IF business.actor_id IS NULL OR (original.target_type,original.outcome,business.state)
      IS DISTINCT FROM ('restore_request'::text,'failed'::text,'failed'::text)
      OR business.version<2
      OR original.before_value IS DISTINCT FROM jsonb_build_object('state','restoring','version',business.version-1)
      OR original.after_value IS DISTINCT FROM jsonb_build_object('state','failed','version',business.version)
      OR original.metadata-ARRAY['previous_hash','event_hash','reason_code'] IS DISTINCT FROM '{}'::jsonb
      OR original.metadata->>'reason_code' IS DISTINCT FROM 'RESTORE_PROCESSING_FAILED'
      OR NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
        AND a.event_id=business.audit_event_id AND a.actor_id=recipient AND a.action='restore.previewed'
        AND a.target_type='restore_request' AND a.target_id=original.target_id AND a.outcome='succeeded') THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  IF business.actor_id IS DISTINCT FROM recipient THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND w.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND w.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id AND r.user_id=recipient AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=recipient AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  accessible:=effective_role IS NOT NULL AND effective_role IN ('personal_owner','organization_admin','workspace_admin')
    AND EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=recipient)
    AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=workspace
      WHERE o.tenant_id=tenant AND o.user_id=recipient AND w.workspace_kind='organization' AND o.state<>'active');
  RETURN jsonb_build_object('accessible',accessible,'title',title,'summary',summary,
    'deep_link','/operations?'||(CASE WHEN resource='backup' THEN 'backup_id=' ELSE 'restore_request_id=' END)||replace(original.target_id,':','%3A'),
    'source_event_id','recovery-event-'||substr(encode(sha256(convert_to(p_event||'|'||workspace||'|'||recipient,'UTF8')),'hex'),1,32),
    'origin_event_id',p_event,'target_type',resource,'target_id',original.target_id,'kind',event_kind,'severity',severity,
    'trace_id',original.trace_id,'policy_version',original.policy_version,'occurred_at',original.occurred_at,'action',original.action,
    '_record_xid',business.row_xid,'_audit_xid',audit_xid);
END $$;
ALTER FUNCTION public.current_terminal_recovery_notification_origin(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_terminal_recovery_notification_origin(text,text) FROM PUBLIC,daon_app;

CREATE FUNCTION public.enqueue_restore_completed_notification(p_event text,p_author text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  recipient text:=nullif(current_setting('app.actor_id',true),'');
  original public.audit_events; request_row record; verification record; operation record;
  top_xid text:=(pg_current_xact_id()::text::numeric % 4294967296)::text; original_xid text;
  effective_role text; source_id text; inserted integer; previous public.notification_source_outbox; terminal jsonb;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF tenant IS NULL OR workspace IS NULL OR recipient IS NULL OR p_event IS NULL OR p_author IS NULL
    OR current_setting('app.capability',true) IS DISTINCT FROM 'view' THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT a.* INTO original FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.event_id=p_event AND a.actor_id=p_author AND a.action IN ('backup.ready','restore.failed');
  IF original.event_id IS NOT NULL THEN
    terminal:=public.current_terminal_recovery_notification_origin(p_event,p_author);
    IF terminal->>'_record_xid' IS DISTINCT FROM top_xid OR terminal->>'_audit_xid' IS DISTINCT FROM top_xid THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    IF terminal->'accessible' IS DISTINCT FROM 'true'::jsonb THEN RETURN false; END IF;
    source_id:=terminal->>'source_event_id';
    INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,
      source_store,action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
    VALUES(tenant,workspace,source_id,p_event,'recovery',original.action,original.target_type,original.target_id,
      recipient,terminal->>'kind',terminal->>'severity',NULL,original.trace_id,original.policy_version,original.occurred_at)
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS inserted=ROW_COUNT;
    IF inserted=0 THEN
      SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
        AND s.recipient_id=recipient AND s.source_event_id=source_id;
      IF (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,previous.target_id,
        previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
        IS DISTINCT FROM (p_event,'recovery'::text,original.action,original.target_type,original.target_id,
          terminal->>'kind',terminal->>'severity',NULL::text,original.trace_id,original.policy_version,original.occurred_at) THEN
        RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN inserted=1;
  END IF;
  SELECT a.* INTO original FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.event_id=p_event AND a.actor_id=p_author AND a.action='restore.completed'
    AND a.outcome='succeeded' AND a.target_type='restore_request';
  SELECT a.xmin::text INTO original_xid FROM public.audit_events a WHERE a.tenant_id=tenant AND a.event_id=p_event;
  SELECT r.request_id,r.backup_id,r.actor_id,r.state,r.version,r.audit_event_id,r.xmin::text AS xid
    INTO request_row FROM public.restore_requests r WHERE r.tenant_id=tenant AND r.workspace_id=workspace
      AND r.request_id=original.target_id;
  IF original.event_id IS NULL OR request_row.request_id IS NULL
    OR (request_row.actor_id,request_row.state,request_row.version) IS DISTINCT FROM (recipient,'completed'::text,6)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL
    OR (original.metadata-ARRAY['previous_hash','event_hash']) IS DISTINCT FROM '{}'::jsonb
    OR original.metadata->>'previous_hash' IS NULL OR original.metadata->>'previous_hash' !~ '^[0-9a-f]{64}$'
    OR original.metadata->>'event_hash' IS NULL OR original.metadata->>'event_hash' !~ '^[0-9a-f]{64}$'
    OR request_row.xid IS DISTINCT FROM top_xid OR original_xid IS DISTINCT FROM top_xid
    OR NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
      AND a.event_id=request_row.audit_event_id AND a.actor_id=recipient AND a.action='restore.previewed'
      AND a.target_type='restore_request' AND a.target_id=request_row.request_id AND a.outcome='succeeded') THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  IF (SELECT count(*) FROM public.restore_verifications v WHERE v.tenant_id=tenant AND v.workspace_id=workspace
      AND v.request_id=request_row.request_id)<>1
    OR (SELECT count(*) FROM public.idempotency_records i WHERE i.tenant_id=tenant AND i.workspace_id=workspace
      AND i.actor_id=p_author AND i.operation='restore-execute' AND i.status='completed'
      AND i.result=jsonb_build_object('request_id',request_row.request_id))<>1 THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT v.verification_digest,v.lineage_verified,v.rls_verified,v.audit_chain_verified,v.retention_rechecked,
    v.xmin::text AS xid INTO verification FROM public.restore_verifications v
    WHERE v.tenant_id=tenant AND v.workspace_id=workspace AND v.request_id=request_row.request_id;
  SELECT i.xmin::text AS xid INTO operation FROM public.idempotency_records i
    WHERE i.tenant_id=tenant AND i.workspace_id=workspace AND i.actor_id=p_author
      AND i.operation='restore-execute' AND i.status='completed' AND i.result=jsonb_build_object('request_id',request_row.request_id);
  -- 기존 서비스가 저장한 verification 행과 최종 Tx 결속이다. 실제 외부 복구 품질 증명이 아니다.
  IF verification.verification_digest !~ '^[0-9a-f]{64}$'
    OR (verification.lineage_verified,verification.rls_verified,verification.audit_chain_verified,verification.retention_rechecked)
      IS DISTINCT FROM (true,true,true,true)
    OR verification.xid IS DISTINCT FROM top_xid OR operation.xid IS DISTINCT FROM top_xid THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND w.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND w.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id AND r.user_id=recipient AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=recipient AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  IF effective_role IS NULL OR effective_role NOT IN ('personal_owner','organization_admin','workspace_admin')
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=recipient)
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=workspace
      WHERE o.tenant_id=tenant AND o.user_id=recipient AND w.workspace_kind='organization' AND o.state<>'active') THEN RETURN false; END IF;
  source_id:='recovery-event-'||substr(encode(sha256(convert_to(
    p_event||'|'||workspace||'|'||recipient,'UTF8')),'hex'),1,32);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,
    source_store,action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,source_id,p_event,'recovery','restore.completed','restore_request',
    request_row.request_id,recipient,'recovery','info',NULL,original.trace_id,original.policy_version,original.occurred_at)
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
      AND s.workspace_id=workspace AND s.recipient_id=recipient AND s.source_event_id=source_id;
    IF (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,previous.target_id,
      previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
      IS DISTINCT FROM (p_event,'recovery'::text,'restore.completed'::text,'restore_request'::text,
        request_row.request_id,'recovery'::text,'info'::text,NULL::text,original.trace_id,original.policy_version,original.occurred_at) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_restore_completed_notification(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_restore_completed_notification(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_restore_completed_notification(text,text) TO daon_app;
-- S42_RESTORE_COMPLETED_SOURCE_END

-- S42_RESTORE_COMPLETED_CONSUMER_BEGIN
CREATE FUNCTION public.pending_restore_completed_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit<1 OR p_limit>100 THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'')
      AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'')
      AND s.state='pending' AND s.source_store='recovery'
      AND ((s.action IN ('restore.completed','restore.failed') AND s.target_type='restore_request')
        OR (s.action='backup.ready' AND s.target_type='backup'))
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE;
END $$;
ALTER FUNCTION public.pending_restore_completed_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_restore_completed_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_restore_completed_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.current_restore_completed_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; original public.audit_events;
  request_row record; verification record; effective_role text; accessible boolean; terminal jsonb; original_author text;
BEGIN
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.source_store='recovery' AND s.action IN ('restore.completed','backup.ready','restore.failed') AND s.state IN ('pending','delivered');
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  IF source.action IN ('backup.ready','restore.failed') THEN
    SELECT a.actor_id INTO original_author FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
      AND a.event_id=source.origin_event_id;
    terminal:=public.current_terminal_recovery_notification_origin(source.origin_event_id,original_author);
    IF (terminal->>'source_event_id',terminal->>'origin_event_id',terminal->>'action',terminal->>'target_type',
      terminal->>'target_id',terminal->>'kind',terminal->>'severity',terminal->>'trace_id',terminal->>'policy_version',
      (terminal->>'occurred_at')::timestamptz,source.notebook_id)
      IS DISTINCT FROM (p_source,source.origin_event_id,source.action,source.target_type,source.target_id,source.kind,
        source.severity,source.trace_id,source.policy_version,source.occurred_at,NULL::text) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
    RETURN terminal-ARRAY['_record_xid','_audit_xid'];
  END IF;
  IF (source.target_type,source.kind,source.severity,source.notebook_id)
    IS DISTINCT FROM ('restore_request'::text,'recovery'::text,'info'::text,NULL::text)
    OR p_source IS DISTINCT FROM 'recovery-event-'||substr(encode(sha256(convert_to(
      source.origin_event_id||'|'||workspace||'|'||actor,'UTF8')),'hex'),1,32) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  SELECT a.* INTO original FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.event_id=source.origin_event_id;
  SELECT r.actor_id,r.state,r.version,r.audit_event_id INTO request_row FROM public.restore_requests r
    WHERE r.tenant_id=tenant AND r.workspace_id=workspace AND r.request_id=source.target_id;
  IF original.event_id IS NULL OR request_row.actor_id IS NULL
    OR (original.action,original.target_type,original.target_id,original.outcome,original.trace_id,
      original.policy_version,original.occurred_at) IS DISTINCT FROM
      ('restore.completed'::text,'restore_request'::text,source.target_id,'succeeded'::text,
        source.trace_id,source.policy_version,source.occurred_at)
    OR (request_row.actor_id,request_row.state,request_row.version) IS DISTINCT FROM (actor,'completed'::text,6)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL
    OR original.metadata-ARRAY['previous_hash','event_hash'] IS DISTINCT FROM '{}'::jsonb
    OR original.metadata->>'previous_hash' IS NULL OR original.metadata->>'previous_hash' !~ '^[0-9a-f]{64}$'
    OR original.metadata->>'event_hash' IS NULL OR original.metadata->>'event_hash' !~ '^[0-9a-f]{64}$'
    OR NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
      AND a.event_id=request_row.audit_event_id AND a.actor_id=actor AND a.action='restore.previewed'
      AND a.target_type='restore_request' AND a.target_id=source.target_id AND a.outcome='succeeded')
    OR (SELECT count(*) FROM public.restore_verifications v WHERE v.tenant_id=tenant
      AND v.workspace_id=workspace AND v.request_id=source.target_id)<>1 THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT v.verification_digest,v.lineage_verified,v.rls_verified,v.audit_chain_verified,v.retention_rechecked
    INTO verification FROM public.restore_verifications v WHERE v.tenant_id=tenant
      AND v.workspace_id=workspace AND v.request_id=source.target_id;
  IF verification.verification_digest !~ '^[0-9a-f]{64}$'
    OR (verification.lineage_verified,verification.rls_verified,verification.audit_chain_verified,verification.retention_rechecked)
      IS DISTINCT FROM (true,true,true,true) THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  -- 실행 영수증은 emit 시점의 같은 Tx 증거다. 정상 TTL 정리 후 원사건을 지우거나 다시 만들지 않는다.
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND w.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND w.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id AND r.user_id=actor AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  accessible:=effective_role IS NOT NULL AND effective_role IN ('personal_owner','organization_admin','workspace_admin')
    AND EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=workspace
      WHERE o.tenant_id=tenant AND o.user_id=actor AND w.workspace_kind='organization' AND o.state<>'active');
  RETURN jsonb_build_object('accessible',accessible,'title','복구 완료',
    'summary','요청한 복구 작업이 완료되었습니다.','deep_link','/operations?restore_request_id='||replace(source.target_id,':','%3A'),
    'source_event_id',source.source_event_id,'origin_event_id',source.origin_event_id,
    'target_type',source.target_type,'target_id',source.target_id,'kind',source.kind,'severity',source.severity,
    'trace_id',source.trace_id,'policy_version',source.policy_version,'occurred_at',source.occurred_at,'action',source.action);
END $$;
ALTER FUNCTION public.current_restore_completed_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_restore_completed_notification_target(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_restore_completed_notification_target(text) TO daon_app;

CREATE FUNCTION public.materialize_restore_completed_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb; notification text; draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant
    AND s.workspace_id=workspace AND s.recipient_id=actor AND s.source_event_id=p_source
    AND s.state='pending' AND s.source_store='recovery' FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_restore_completed_notification_target(p_source);
  IF target IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  IF target->'accessible'='true'::jsonb AND NOT p_deliver THEN RETURN NULL; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  notification:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,notification,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,
    kind,severity,title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
  VALUES(tenant,notification,'recovery',source.origin_event_id,p_source,source.kind,source.severity,
    target->>'title',target->>'summary',source.target_type,source.target_id,NULL,target->>'deep_link',source.trace_id,
    source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO draft FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=notification AND a.action='notification.created';
  IF draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  RETURN jsonb_build_object('state','delivered','notification_id',notification,'draft',draft);
END $$;
ALTER FUNCTION public.materialize_restore_completed_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_restore_completed_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_restore_completed_notification(text,boolean) TO daon_app;
-- S42_RESTORE_COMPLETED_CONSUMER_END

-- S42_KNOWLEDGE_SELECTION_LOCK_BEGIN
CREATE FUNCTION public.lock_question_knowledge_selection(
    p_tenant text,
    p_workspace text,
    p_notebook text,
    p_actor text,
    p_registration text,
    p_source_version text
) RETURNS boolean
LANGUAGE plpgsql
VOLATILE
PARALLEL UNSAFE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
DECLARE
    v_output text;
    v_source text;
    v_checked_at timestamptz;
BEGIN
    IF EXISTS(SELECT 1 FROM pg_catalog.unnest(ARRAY[p_tenant,p_workspace,p_notebook,p_actor,p_registration,p_source_version]) AS i(value)
        WHERE value IS NULL OR value !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') THEN
        RAISE EXCEPTION 'QUESTION_SELECTION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;

    -- 호출 역할 제한일 뿐 사용자 인증이 아니다.
    -- root가 SET ROLE/SESSION AUTHORIZATION 하는 경우를 차단한다고
    -- 주장하지 않는다.
    IF session_user <> 'daon_app'
       AND current_setting('role', true) IS DISTINCT FROM 'daon_app'
    THEN
        RAISE EXCEPTION 'QUESTION_SELECTION_SCOPE_DENIED'
            USING ERRCODE = '42501';
    END IF;

    IF p_tenant IS NULL OR p_workspace IS NULL
       OR p_notebook IS NULL OR p_actor IS NULL
       OR p_registration IS NULL OR p_source_version IS NULL
       OR p_tenant IS DISTINCT FROM
          nullif(current_setting('app.tenant_id', true), '')
       OR p_workspace IS DISTINCT FROM
          nullif(current_setting('app.workspace_id', true), '')
       OR p_actor IS DISTINCT FROM
          nullif(current_setting('app.actor_id', true), '')
    THEN
        RAISE EXCEPTION 'QUESTION_SELECTION_SCOPE_DENIED'
            USING ERRCODE = '42501';
    END IF;

    -- 기존 Notebook 삭제/해제/binding 키와 동일 순서.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'notebook-delete|' || p_tenant || '|' ||
        p_workspace || '|' || p_notebook, 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'unbind-notebook|' || p_tenant || '|' ||
        p_workspace || '|' || p_notebook, 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        p_tenant || '|' || p_workspace || '|' ||
        p_notebook || '|binding', 0));

    IF NOT EXISTS (
        SELECT 1
        FROM public.notebooks n
        WHERE n.tenant_id = p_tenant
          AND n.workspace_id = p_workspace
          AND n.notebook_id = p_notebook
          AND NOT EXISTS (
              SELECT 1 FROM public.notebook_deletion_requests d
              WHERE d.tenant_id = n.tenant_id
                AND d.workspace_id = n.workspace_id
                AND d.notebook_id = n.notebook_id
                AND d.state IN ('accepted', 'deleting', 'completed')
          )
    ) THEN
        RETURN false;
    END IF;

    -- 잠금 대상 유도만 수행한다. 아래 잠금 후 다시 검증한다.
    SELECT kr.output_version_id, sv.source_id
      INTO v_output, v_source
      FROM public.knowledge_registrations kr
      JOIN public.source_versions sv
        ON sv.tenant_id = kr.tenant_id
       AND sv.workspace_id = kr.workspace_id
       AND sv.record_id = kr.registered_source_version_id
     WHERE kr.tenant_id = p_tenant
       AND kr.workspace_id = p_workspace
       AND kr.record_id = p_registration
       AND kr.registered_source_version_id = p_source_version;

    IF NOT FOUND THEN
        RETURN false;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(
        'retention-source|' || p_tenant || '|' ||
        p_workspace || '|' || v_source, 0));

    -- planner의 join 잠금 순서에 맡기지 않는다.
    PERFORM 1 FROM public.sources s
     WHERE s.tenant_id = p_tenant
       AND s.workspace_id = p_workspace
       AND s.record_id = v_source
     FOR SHARE;
    IF NOT FOUND THEN RETURN false; END IF;

    PERFORM 1 FROM public.output_versions ov
     WHERE ov.tenant_id = p_tenant
       AND ov.workspace_id = p_workspace
       AND ov.record_id = v_output
     FOR SHARE;
    IF NOT FOUND THEN RETURN false; END IF;

    PERFORM 1 FROM public.knowledge_registrations kr
     WHERE kr.tenant_id = p_tenant
       AND kr.workspace_id = p_workspace
       AND kr.record_id = p_registration
     FOR SHARE;
    IF NOT FOUND THEN RETURN false; END IF;

    PERFORM 1 FROM public.source_versions sv
     WHERE sv.tenant_id = p_tenant
       AND sv.workspace_id = p_workspace
       AND sv.record_id = p_source_version
     FOR SHARE;
    IF NOT FOUND THEN RETURN false; END IF;

    -- 잠금 대기 뒤 실제 시각으로 승인·만료·FK를 재검사한다.
    v_checked_at := clock_timestamp();

    RETURN EXISTS (
        SELECT 1
        FROM public.knowledge_registrations kr
        JOIN public.output_versions ov
          ON ov.tenant_id = kr.tenant_id
         AND ov.workspace_id = kr.workspace_id
         AND ov.record_id = kr.output_version_id
        JOIN public.source_versions sv
          ON sv.tenant_id = kr.tenant_id
         AND sv.workspace_id = kr.workspace_id
         AND sv.record_id = kr.registered_source_version_id
        JOIN public.sources s
          ON s.tenant_id = sv.tenant_id
         AND s.workspace_id = sv.workspace_id
         AND s.record_id = sv.source_id
        WHERE kr.tenant_id = p_tenant
          AND kr.workspace_id = p_workspace
          AND kr.record_id = p_registration
          AND kr.output_version_id = v_output
          AND kr.registered_source_version_id = p_source_version
          AND sv.source_id = v_source
          AND kr.state = 'registered'
          AND ov.state = 'approved'
          AND kr.canonical_json->>'authority' = 'approved'
          AND kr.canonical_json->>'producer'
              IN ('daon2', 'daon2_5', 'daon3')
          AND (kr.canonical_json->>'effective_at')::timestamptz
              <= v_checked_at
          AND (kr.canonical_json->>'expires_at')::timestamptz
              > v_checked_at
          AND EXISTS (
              SELECT 1 FROM public.identity_memberships m
              JOIN public.identity_users u ON u.user_id=m.user_id
              JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id
              JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id
              WHERE m.tenant_id=p_tenant AND m.user_id=p_actor AND u.state='active'
                AND w.workspace_id=p_workspace
                AND (EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles r
                    WHERE r.tenant_id=m.tenant_id AND r.user_id=m.user_id AND r.state='active'
                      AND ((w.workspace_kind='personal' AND r.role='personal_owner')
                        OR (w.workspace_kind='organization' AND r.role='organization_admin')))
                  OR EXISTS(SELECT 1 FROM public.identity_auth_memberships am
                    WHERE am.tenant_id=w.tenant_id AND am.workspace_id=w.workspace_id
                      AND am.user_id=m.user_id AND am.state='active'))
          )
          AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
              WHERE o.tenant_id=p_tenant AND o.user_id=p_actor AND o.state<>'active')
          AND NOT EXISTS(SELECT 1 FROM public.identity_auth_source_access ac
              WHERE ac.tenant_id=p_tenant AND ac.workspace_id=p_workspace
                AND ac.user_id=p_actor AND ac.source_version_id=p_source_version AND ac.allowed=0)
          AND NOT EXISTS (
              SELECT 1 FROM public.deletion_requests d
              WHERE d.tenant_id = s.tenant_id
                AND d.workspace_id = s.workspace_id
                AND d.source_id = s.record_id
                AND d.source_active = false
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.notebook_source_unbindings u
              WHERE u.tenant_id = p_tenant
                AND u.workspace_id = p_workspace
                AND u.notebook_id = p_notebook
                AND u.source_id = v_source
                AND u.source_version_id = p_source_version
          )
    );
END;
$function$;

ALTER FUNCTION public.lock_question_knowledge_selection(
    text,text,text,text,text,text
) OWNER TO daon_notebook_delete;

REVOKE ALL ON FUNCTION public.lock_question_knowledge_selection(
    text,text,text,text,text,text
) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.lock_question_knowledge_selection(
    text,text,text,text,text,text
) TO daon_app;
-- S42_KNOWLEDGE_SELECTION_LOCK_END

-- S42_LOGIN_INSERT_SOURCE_DRAFT_BEGIN
CREATE OR REPLACE FUNCTION public.enqueue_identity_login_notification(p_event text,p_oidc text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  original public.security_audit_events; login_session record; login_device record; oidc_record record;
  family record; refresh_record record; identity_issuer text; effective_role text;
  top_xid numeric:=pg_current_xact_id()::text::numeric; record_xid numeric; xids text[];
  source_id text; inserted integer; previous public.notification_source_outbox; draft jsonb;
BEGIN
  IF pg_trigger_depth()<>1 OR current_setting('app.identity_login_emit',true) IS DISTINCT FROM 'true' THEN
    RAISE EXCEPTION 'LOGIN_INSERT_TRIGGER_REQUIRED' USING ERRCODE='42501'; END IF;
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL OR p_event IS NULL
    OR p_event !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR (p_oidc IS NOT NULL AND p_oidc !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  draft:=nullif(current_setting('app.identity_login_draft',true),'')::jsonb;
  IF jsonb_typeof(draft) IS DISTINCT FROM 'object'
    OR NOT (draft ?& ARRAY['event_id','occurred_at','actor_id','actor_type','tenant_id','workspace_id','action',
      'target_type','target_id','outcome','trace_id','policy_version','before','after','metadata'])
    OR (SELECT count(*) FROM jsonb_object_keys(draft))<>15
    OR draft->'before' IS DISTINCT FROM 'null'::jsonb OR draft->'after' IS DISTINCT FROM 'null'::jsonb THEN
    RAISE EXCEPTION 'LOGIN_DRAFT_INVALID' USING ERRCODE='23514'; END IF;
  original:=jsonb_populate_record(NULL::public.security_audit_events,draft-'before'-'after');
  IF (original.event_id,original.tenant_id,original.actor_id) IS DISTINCT FROM (p_event,tenant,actor)
    OR NOT isfinite(original.occurred_at)
    OR EXISTS(SELECT 1 FROM public.security_audit_events a WHERE a.tenant_id=tenant AND
      (a.event_id=p_event OR (a.actor_id=actor AND a.action='identity.login.succeeded'
        AND a.target_type='session' AND a.target_id=original.target_id AND a.outcome='succeeded'))) THEN
    RAISE EXCEPTION 'LOGIN_DRAFT_REPLAY_OR_SESSION_REUSE' USING ERRCODE='23514'; END IF;
  IF original.event_id IS NULL OR original.workspace_id IS NOT NULL
    OR (original.action,original.actor_type,original.target_type,original.outcome)
      IS DISTINCT FROM ('identity.login.succeeded'::text,'user'::text,'session'::text,'succeeded'::text)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL OR original.safe_code IS NOT NULL
    OR original.trace_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR original.policy_version !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT s.session_id,s.device_id,s.user_id,s.tenant_id,s.client_kind,s.state,s.created_at,s.updated_at,s.xmin::text AS xid
    INTO login_session FROM public.identity_sessions s WHERE s.session_id=original.target_id
      AND s.tenant_id=tenant AND s.user_id=actor;
  IF login_session.session_id IS NULL OR login_session.state<>'active'
    OR login_session.created_at IS DISTINCT FROM login_session.updated_at
    OR login_session.client_kind NOT IN ('web','native') THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT d.device_id,d.user_id,d.tenant_id,d.platform,d.state,d.created_at,d.updated_at,d.xmin::text AS xid
    INTO login_device FROM public.identity_devices d WHERE d.device_id=login_session.device_id
      AND d.tenant_id=tenant AND d.user_id=actor;
  IF login_device.device_id IS NULL OR login_device.state<>'registered'
    OR login_device.created_at IS DISTINCT FROM login_session.created_at
    OR login_device.updated_at IS DISTINCT FROM login_device.created_at THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT u.issuer INTO identity_issuer FROM public.identity_users u WHERE u.user_id=actor AND u.state='active';
  IF identity_issuer IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  xids:=ARRAY[login_session.xid,login_device.xid];
  IF original.metadata=jsonb_build_object('client_type',login_session.client_kind,'auth_method','local') THEN
    IF identity_issuer<>'local' OR p_oidc IS NOT NULL
      OR (login_session.client_kind='web' AND login_device.platform<>'web')
      OR (login_session.client_kind='native' AND login_device.platform<>'windows') THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  ELSIF original.metadata=jsonb_build_object('client_type',login_session.client_kind) THEN
    IF identity_issuer='local' OR p_oidc IS NULL THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    SELECT o.transaction_id,o.issuer,o.client_kind,o.used_at,o.xmin::text AS xid
      INTO oidc_record FROM public.identity_oidc_transactions o
      WHERE o.transaction_id=p_oidc AND o.tenant_id=tenant;
    IF oidc_record.transaction_id IS NULL OR oidc_record.issuer IS DISTINCT FROM identity_issuer
      OR oidc_record.client_kind IS DISTINCT FROM login_session.client_kind
      OR oidc_record.used_at IS DISTINCT FROM login_session.created_at THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    xids:=array_append(xids,oidc_record.xid);
  ELSE RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  -- Native 자식 완성·실제 중앙 원감사 대조는 commit 전 읽기 verifier가 담당한다.
  -- 원중앙감사는 별도 commit이다. session/device/(native·OIDC)의 실제 원업무만 현재 transaction으로 증명한다.
  FOREACH record_xid IN ARRAY xids::numeric[] LOOP
    IF record_xid IS NULL OR pg_xact_status((top_xid-mod(top_xid,4294967296)+record_xid+
      CASE WHEN record_xid-mod(top_xid,4294967296)< -2147483648 THEN 4294967296
        WHEN record_xid-mod(top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8) IS DISTINCT FROM 'in progress' THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  END LOOP;
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN
      CASE WHEN tr.role='personal_owner' AND w.workspace_kind='personal' THEN tr.role
        WHEN tr.role='organization_admin' AND w.workspace_kind='organization' THEN tr.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=actor AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  IF effective_role IS NULL OR effective_role NOT IN
      ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships f
      JOIN public.identity_auth_workspaces w ON w.tenant_id=f.tenant_id AND w.workspace_id=workspace
      WHERE f.tenant_id=tenant AND f.user_id=actor AND w.workspace_kind='organization' AND f.state<>'active') THEN RETURN false; END IF;
  -- 같은 실제 session에 새 감사 ID를 붙여 source를 복제하지 못한다.
  IF EXISTS(SELECT 1 FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_store='identity' AND s.action='identity.login.succeeded'
    AND s.target_type='session' AND s.target_id=login_session.session_id AND s.origin_event_id<>p_event) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  source_id:='identity-login-event-'||substr(encode(sha256(convert_to(p_event||'|'||workspace,'UTF8')),'hex'),1,32);
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,source_store,
    action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,source_id,p_event,'identity','identity.login.succeeded','session',login_session.session_id,
    actor,'authentication','info',NULL,original.trace_id,original.policy_version,original.occurred_at) ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
      AND s.recipient_id=actor AND s.source_event_id=source_id;
    IF previous.source_event_id IS NULL OR (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,
      previous.target_id,previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
      IS DISTINCT FROM (p_event,'identity'::text,'identity.login.succeeded'::text,'session'::text,
        login_session.session_id,'authentication'::text,'info'::text,NULL::text,original.trace_id,original.policy_version,original.occurred_at) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_identity_login_notification(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_identity_login_notification(text,text) FROM PUBLIC,daon_app;


CREATE FUNCTION public.verify_identity_login_notification_source(p_event text,p_oidc text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  original public.security_audit_events; login_session record; login_device record; oidc_record record;
  family record; refresh_record record; identity_issuer text; effective_role text;
  top_xid numeric:=pg_current_xact_id()::text::numeric; record_xid numeric; xids text[];
  source_id text; inserted integer; previous public.notification_source_outbox;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL OR p_event IS NULL
    OR p_event !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR (p_oidc IS NOT NULL AND p_oidc !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT a.* INTO original FROM public.security_audit_events a
    WHERE a.event_id=p_event AND a.tenant_id=tenant AND a.actor_id=actor;
  IF original.event_id IS NULL OR original.workspace_id IS NOT NULL
    OR (original.action,original.actor_type,original.target_type,original.outcome)
      IS DISTINCT FROM ('identity.login.succeeded'::text,'user'::text,'session'::text,'succeeded'::text)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL OR original.safe_code IS NOT NULL
    OR original.trace_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR original.policy_version !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT s.session_id,s.device_id,s.user_id,s.tenant_id,s.client_kind,s.state,s.created_at,s.updated_at,s.xmin::text AS xid
    INTO login_session FROM public.identity_sessions s WHERE s.session_id=original.target_id
      AND s.tenant_id=tenant AND s.user_id=actor;
  IF login_session.session_id IS NULL OR login_session.state<>'active'
    OR login_session.created_at IS DISTINCT FROM login_session.updated_at
    OR login_session.client_kind NOT IN ('web','native') THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT d.device_id,d.user_id,d.tenant_id,d.platform,d.state,d.created_at,d.updated_at,d.xmin::text AS xid
    INTO login_device FROM public.identity_devices d WHERE d.device_id=login_session.device_id
      AND d.tenant_id=tenant AND d.user_id=actor;
  IF login_device.device_id IS NULL OR login_device.state<>'registered'
    OR login_device.created_at IS DISTINCT FROM login_session.created_at
    OR login_device.updated_at IS DISTINCT FROM login_device.created_at THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT u.issuer INTO identity_issuer FROM public.identity_users u WHERE u.user_id=actor AND u.state='active';
  IF identity_issuer IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  xids:=ARRAY[login_session.xid,login_device.xid];
  IF original.metadata=jsonb_build_object('client_type',login_session.client_kind,'auth_method','local') THEN
    IF identity_issuer<>'local' OR p_oidc IS NOT NULL
      OR (login_session.client_kind='web' AND login_device.platform<>'web')
      OR (login_session.client_kind='native' AND login_device.platform<>'windows') THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  ELSIF original.metadata=jsonb_build_object('client_type',login_session.client_kind) THEN
    IF identity_issuer='local' OR p_oidc IS NULL THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    SELECT o.transaction_id,o.issuer,o.client_kind,o.used_at,o.xmin::text AS xid
      INTO oidc_record FROM public.identity_oidc_transactions o
      WHERE o.transaction_id=p_oidc AND o.tenant_id=tenant;
    IF oidc_record.transaction_id IS NULL OR oidc_record.issuer IS DISTINCT FROM identity_issuer
      OR oidc_record.client_kind IS DISTINCT FROM login_session.client_kind
      OR oidc_record.used_at IS DISTINCT FROM login_session.created_at THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    xids:=array_append(xids,oidc_record.xid);
  ELSE RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  IF login_session.client_kind='native' THEN
    IF (SELECT count(*) FROM public.identity_refresh_families f WHERE f.session_id=login_session.session_id)<>1 THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    SELECT f.family_id,f.state,f.created_at,f.xmin::text AS xid INTO family
      FROM public.identity_refresh_families f WHERE f.session_id=login_session.session_id;
    IF family.state<>'active' OR family.created_at IS DISTINCT FROM login_session.created_at
      OR (SELECT count(*) FROM public.identity_refresh_tokens t WHERE t.family_id=family.family_id)<>1 THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    SELECT t.refresh_id,t.used_at,t.created_at,t.xmin::text AS xid INTO refresh_record
      FROM public.identity_refresh_tokens t WHERE t.family_id=family.family_id;
    IF refresh_record.used_at IS NOT NULL OR refresh_record.created_at IS DISTINCT FROM login_session.created_at THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    xids:=array_append(array_append(xids,family.xid),refresh_record.xid);
  END IF;
  -- 원중앙감사는 별도 commit이다. session/device/(native·OIDC)의 실제 원업무만 현재 transaction으로 증명한다.
  FOREACH record_xid IN ARRAY xids::numeric[] LOOP
    IF record_xid IS NULL OR pg_xact_status((top_xid-mod(top_xid,4294967296)+record_xid+
      CASE WHEN record_xid-mod(top_xid,4294967296)< -2147483648 THEN 4294967296
        WHEN record_xid-mod(top_xid,4294967296)>2147483647 THEN -4294967296 ELSE 0 END)::text::xid8) IS DISTINCT FROM 'in progress' THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  END LOOP;
  SELECT CASE WHEN tr.user_id IS NOT NULL THEN
      CASE WHEN tr.role='personal_owner' AND w.workspace_kind='personal' THEN tr.role
        WHEN tr.role='organization_admin' AND w.workspace_kind='organization' THEN tr.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles tr ON tr.tenant_id=w.tenant_id AND tr.user_id=actor AND tr.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  IF effective_role IS NULL OR effective_role NOT IN
      ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    OR NOT EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=a.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships f
      JOIN public.identity_auth_workspaces w ON w.tenant_id=f.tenant_id AND w.workspace_id=workspace
      WHERE f.tenant_id=tenant AND f.user_id=actor AND w.workspace_kind='organization' AND f.state<>'active') THEN RETURN false; END IF;
  -- 같은 실제 session에 새 감사 ID를 붙여 source를 복제하지 못한다.
  IF EXISTS(SELECT 1 FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_store='identity' AND s.action='identity.login.succeeded'
    AND s.target_type='session' AND s.target_id=login_session.session_id AND s.origin_event_id<>p_event) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  source_id:='identity-login-event-'||substr(encode(sha256(convert_to(p_event||'|'||workspace,'UTF8')),'hex'),1,32);
  SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_event_id=source_id AND s.state='pending' AND s.version=1 AND s.delivered_at IS NULL;
  IF previous.source_event_id IS NULL THEN RETURN false; END IF;
  RETURN (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,
    previous.target_id,previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
    IS NOT DISTINCT FROM (p_event,'identity'::text,'identity.login.succeeded'::text,'session'::text,
      login_session.session_id,'authentication'::text,'info'::text,NULL::text,original.trace_id,original.policy_version,original.occurred_at);
END $$;
ALTER FUNCTION public.verify_identity_login_notification_source(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.verify_identity_login_notification_source(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.verify_identity_login_notification_source(text,text) TO daon_app;

CREATE FUNCTION public.capture_identity_login_source() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE draft jsonb; scopes jsonb; workspace text;
  saved_tenant text:=current_setting('app.tenant_id',true);
  saved_actor text:=current_setting('app.actor_id',true);
  saved_workspace text:=current_setting('app.workspace_id',true);
  saved_capability text:=current_setting('app.capability',true);
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true'
    OR current_setting('app.identity_login_emit',true) IS DISTINCT FROM 'true' THEN RETURN NEW; END IF;
  IF TG_OP<>'INSERT' OR TG_RELID<>'public.identity_sessions'::regclass OR pg_trigger_depth()<>1 THEN
    RAISE EXCEPTION 'LOGIN_INSERT_TRIGGER_REQUIRED' USING ERRCODE='42501'; END IF;
  draft:=nullif(current_setting('app.identity_login_draft',true),'')::jsonb;
  scopes:=nullif(current_setting('app.identity_login_workspaces',true),'')::jsonb;
  IF jsonb_typeof(draft) IS DISTINCT FROM 'object' OR jsonb_typeof(scopes) IS DISTINCT FROM 'array'
    OR (draft->>'tenant_id',draft->>'actor_id',draft->>'target_type',draft->>'target_id',draft->'metadata'->>'client_type')
      IS DISTINCT FROM (NEW.tenant_id,NEW.user_id,'session'::text,NEW.session_id,NEW.client_kind)
    OR coalesce((SELECT bool_or(jsonb_typeof(value)<>'string') FROM jsonb_array_elements(scopes)),false)
    OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(scopes)) THEN
    RAISE EXCEPTION 'LOGIN_DRAFT_INVALID' USING ERRCODE='23514'; END IF;
  BEGIN
    FOR workspace IN SELECT jsonb_array_elements_text(scopes) LOOP
      IF workspace IS NULL OR workspace !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
        RAISE EXCEPTION 'LOGIN_DRAFT_INVALID' USING ERRCODE='23514'; END IF;
      PERFORM set_config('app.tenant_id',NEW.tenant_id,true);
      PERFORM set_config('app.actor_id',NEW.user_id,true);
      PERFORM set_config('app.workspace_id',workspace,true);
      PERFORM set_config('app.capability','view',true);
      PERFORM public.enqueue_identity_login_notification(draft->>'event_id',
        nullif(current_setting('app.identity_login_oidc',true),''));
    END LOOP;
    PERFORM set_config('app.tenant_id',coalesce(saved_tenant,''),true);
    PERFORM set_config('app.actor_id',coalesce(saved_actor,''),true);
    PERFORM set_config('app.workspace_id',coalesce(saved_workspace,''),true);
    PERFORM set_config('app.capability',coalesce(saved_capability,''),true);
  EXCEPTION WHEN OTHERS THEN
    PERFORM set_config('app.tenant_id',coalesce(saved_tenant,''),true);
    PERFORM set_config('app.actor_id',coalesce(saved_actor,''),true);
    PERFORM set_config('app.workspace_id',coalesce(saved_workspace,''),true);
    PERFORM set_config('app.capability',coalesce(saved_capability,''),true);
    RAISE;
  END;
  RETURN NEW;
END $$;
ALTER FUNCTION public.capture_identity_login_source() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.capture_identity_login_source() FROM PUBLIC,daon_app;
CREATE TRIGGER identity_login_source_insert AFTER INSERT ON public.identity_sessions
FOR EACH ROW EXECUTE FUNCTION public.capture_identity_login_source();
-- S42_LOGIN_INSERT_SOURCE_DRAFT_END

-- S42_DISPATCH_SCOPE_DISCOVERY_BEGIN
CREATE POLICY notification_source_dispatch_scope_read
ON public.notification_source_outbox FOR SELECT TO daon_notebook_delete
USING (current_user='daon_notebook_delete'
  AND current_setting('app.capability',true)='notification.dispatch.scopes');

CREATE POLICY notification_audit_dispatch_scope_read
ON public.notification_audit_outbox FOR SELECT TO daon_notebook_delete
USING (current_user='daon_notebook_delete'
  AND current_setting('app.capability',true)='notification.dispatch.scopes');

CREATE POLICY studio_notice_dispatch_scope_read
ON public.studio_approval_notices FOR SELECT TO daon_notebook_delete
USING (current_user='daon_notebook_delete'
  AND current_setting('app.capability',true)='notification.dispatch.scopes');

CREATE FUNCTION public.pending_notification_dispatch_scopes(
  p_limit integer,p_after_tenant text DEFAULT NULL,
  p_after_workspace text DEFAULT NULL,p_after_recipient text DEFAULT NULL)
RETURNS TABLE(tenant_id text,workspace_id text,recipient_id text)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
DECLARE previous_capability text:=current_setting('app.capability',true);
  cursor_parts text[]:=ARRAY[p_after_tenant,p_after_workspace,p_after_recipient];
  cursor_part text;
BEGIN
  -- SQL 연결 역할은 서비스 실행 경계이며 사람의 인증 수단이 아니다.
  IF session_user<>'daon_app'
    AND nullif(current_setting('role',true),'none') IS DISTINCT FROM 'daon_app' THEN
    RAISE EXCEPTION 'NOTIFICATION_DISPATCH_ROLE_REQUIRED' USING ERRCODE='42501';
  END IF;
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
    OR (num_nonnulls(p_after_tenant,p_after_workspace,p_after_recipient) NOT IN (0,3)) THEN
    RAISE EXCEPTION 'NOTIFICATION_DISPATCH_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  FOREACH cursor_part IN ARRAY cursor_parts LOOP
    IF cursor_part IS NOT NULL AND cursor_part !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
      RAISE EXCEPTION 'NOTIFICATION_DISPATCH_INPUT_INVALID' USING ERRCODE='22023';
    END IF;
  END LOOP;
  PERFORM set_config('app.capability','notification.dispatch.scopes',true);
  RETURN QUERY
    WITH candidates(t,w,u) AS (
      SELECT s.tenant_id,s.workspace_id,s.recipient_id
        FROM public.notification_source_outbox s WHERE s.state='pending'
      UNION
      SELECT a.tenant_id,a.workspace_id,a.recipient_id
        FROM public.notification_audit_outbox a WHERE a.state='pending'
      UNION
      SELECT n.tenant_id,n.workspace_id,n.recipient_id
        FROM public.studio_approval_notices n
        WHERE NOT EXISTS(SELECT 1 FROM public.notification_audit_outbox a
          WHERE a.tenant_id=n.tenant_id AND a.workspace_id=n.workspace_id
            AND a.recipient_id=n.recipient_id AND a.notification_id=n.notification_id
            AND a.source_event_id=n.notification_id AND a.action='notification.created')
    )
    SELECT c.t,c.w,c.u FROM candidates c
      WHERE p_after_tenant IS NULL
        OR (c.t,c.w,c.u)>(p_after_tenant,p_after_workspace,p_after_recipient)
      ORDER BY c.t,c.w,c.u LIMIT p_limit;
  PERFORM set_config('app.capability',coalesce(previous_capability,''),true);
EXCEPTION WHEN OTHERS THEN
  PERFORM set_config('app.capability',coalesce(previous_capability,''),true);
  RAISE;
END $$;
ALTER FUNCTION public.pending_notification_dispatch_scopes(integer,text,text,text)
  OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_notification_dispatch_scopes(integer,text,text,text)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_notification_dispatch_scopes(integer,text,text,text)
  TO daon_app;
-- S42_DISPATCH_SCOPE_DISCOVERY_END

-- S42_PASSWORD_CHANGE_SOURCE_BEGIN
CREATE TABLE public.identity_password_change_events (
  event_id text PRIMARY KEY CHECK (event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  tenant_id text NOT NULL REFERENCES public.identity_tenants(tenant_id),
  user_id text NOT NULL,
  source_session_id text NOT NULL UNIQUE,
  occurred_at timestamptz NOT NULL,
  trace_id text NOT NULL CHECK (trace_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  policy_version text NOT NULL CHECK (policy_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
);
ALTER TABLE public.identity_password_change_events OWNER TO daon_notebook_delete;
ALTER TABLE public.identity_password_change_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.identity_password_change_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.identity_password_change_events FROM PUBLIC,daon_app;
GRANT SELECT,INSERT ON TABLE public.identity_password_change_events TO daon_app;
CREATE POLICY identity_password_change_event_owner_scope
ON public.identity_password_change_events TO daon_app
USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')
  AND user_id=nullif(current_setting('app.actor_id',true),''))
WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')
  AND user_id=nullif(current_setting('app.actor_id',true),''));

CREATE FUNCTION public.guard_identity_password_change_event() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE source_session record; identity_user record;
  top_xid text:=(pg_current_xact_id()::text::numeric % 4294967296)::text;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'PASSWORD_EVENT_IMMUTABLE' USING ERRCODE='23514'; END IF;
  IF current_user<>'daon_app' OR NEW.tenant_id IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
    OR NEW.user_id IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') OR NOT isfinite(NEW.occurred_at) THEN
    RAISE EXCEPTION 'PASSWORD_EVENT_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT s.user_id,s.tenant_id,s.state,s.xmin::text AS row_xid INTO source_session
    FROM public.identity_sessions s WHERE s.session_id=NEW.source_session_id;
  SELECT u.state,u.issuer,u.password_change_required,u.xmin::text AS row_xid INTO identity_user
    FROM public.identity_users u WHERE u.user_id=NEW.user_id;
  IF source_session.user_id IS NULL OR identity_user.state IS NULL
    OR (source_session.user_id,source_session.tenant_id,source_session.state)
      IS DISTINCT FROM (NEW.user_id,NEW.tenant_id,'revoked'::text)
    OR (identity_user.state,identity_user.issuer,identity_user.password_change_required)
      IS DISTINCT FROM ('active'::text,'local'::text,false)
    OR source_session.row_xid IS DISTINCT FROM top_xid OR identity_user.row_xid IS DISTINCT FROM top_xid
    OR NOT EXISTS(SELECT 1 FROM public.security_audit_events a WHERE a.event_id=NEW.event_id
      AND a.tenant_id=NEW.tenant_id AND a.actor_id=NEW.user_id AND a.workspace_id IS NULL
      AND a.actor_type='user' AND a.action='identity.password.changed' AND a.target_type='user' AND a.target_id=NEW.user_id
      AND a.outcome='succeeded' AND a.occurred_at=NEW.occurred_at AND a.trace_id=NEW.trace_id AND a.policy_version=NEW.policy_version
      AND a.before_value IS NULL AND a.after_value IS NULL AND a.safe_code='CURRENT_PASSWORD_CHANGED'
      AND a.metadata=jsonb_build_object('reason_code','CURRENT_PASSWORD_CHANGED')) THEN
    RAISE EXCEPTION 'PASSWORD_EVENT_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  -- xmin은 같은 업무 Tx 검사다. 실제 비밀번호 검증의 신뢰 근거는 backend principal/기존 검증 경로다.
  RETURN NEW;
END $$;
ALTER FUNCTION public.guard_identity_password_change_event() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_identity_password_change_event() FROM PUBLIC,daon_app;
CREATE TRIGGER identity_password_change_event_guard BEFORE INSERT OR UPDATE OR DELETE ON public.identity_password_change_events
FOR EACH ROW EXECUTE FUNCTION public.guard_identity_password_change_event();

CREATE FUNCTION public.current_password_change_notification_event(p_event text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  event public.identity_password_change_events; original public.security_audit_events;
  event_xid text; effective_role text; accessible boolean;
BEGIN
  IF current_user<>'daon_notebook_delete' OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT e.* INTO event FROM public.identity_password_change_events e
    WHERE e.event_id=p_event AND e.tenant_id=tenant AND e.user_id=actor;
  IF event.event_id IS NULL THEN RETURN NULL; END IF;
  SELECT e.xmin::text INTO event_xid FROM public.identity_password_change_events e WHERE e.event_id=p_event;
  SELECT a.* INTO original FROM public.security_audit_events a WHERE a.tenant_id=tenant AND a.event_id=p_event;
  IF original.event_id IS NULL OR (original.actor_id,original.actor_type,original.workspace_id,original.action,
      original.target_type,original.target_id,original.outcome,original.occurred_at,original.trace_id,original.policy_version)
    IS DISTINCT FROM (actor,'user'::text,NULL::text,'identity.password.changed'::text,'user'::text,actor,
      'succeeded'::text,event.occurred_at,event.trace_id,event.policy_version)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL OR original.safe_code IS DISTINCT FROM 'CURRENT_PASSWORD_CHANGED'
    OR original.metadata IS DISTINCT FROM jsonb_build_object('reason_code','CURRENT_PASSWORD_CHANGED') THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND w.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND w.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id AND r.user_id=actor AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  accessible:=effective_role IS NOT NULL AND effective_role IN
      ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    AND EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=workspace
      WHERE o.tenant_id=tenant AND o.user_id=actor AND w.workspace_kind='organization' AND o.state<>'active');
  RETURN jsonb_build_object('accessible',accessible,'title','비밀번호 변경',
    'summary','본인 계정의 비밀번호가 변경되었습니다.','deep_link','/settings/account',
    'source_event_id','identity-password-event-'||substr(encode(sha256(convert_to(p_event||'|'||workspace,'UTF8')),'hex'),1,32),
    'origin_event_id',p_event,'target_type','user','target_id',actor,'kind','authentication','severity','info',
    'trace_id',event.trace_id,'policy_version',event.policy_version,'occurred_at',event.occurred_at,'action','identity.password.changed',
    '_event_xid',event_xid);
END $$;
ALTER FUNCTION public.current_password_change_notification_event(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_password_change_notification_event(text) FROM PUBLIC,daon_app;

CREATE FUNCTION public.enqueue_password_change_notification(p_event text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  target jsonb; inserted integer; previous public.notification_source_outbox;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF current_setting('app.capability',true) IS DISTINCT FROM 'view' THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  target:=public.current_password_change_notification_event(p_event);
  IF target IS NULL OR target->>'_event_xid' IS DISTINCT FROM (pg_current_xact_id()::text::numeric % 4294967296)::text THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN RETURN false; END IF;
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,source_store,
    action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,target->>'source_event_id',p_event,'identity','identity.password.changed','user',actor,actor,
    'authentication','info',NULL,target->>'trace_id',target->>'policy_version',(target->>'occurred_at')::timestamptz)
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
      AND s.recipient_id=actor AND s.source_event_id=target->>'source_event_id';
    IF (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,previous.target_id,
      previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
      IS DISTINCT FROM (p_event,'identity'::text,'identity.password.changed'::text,'user'::text,actor,
        'authentication'::text,'info'::text,NULL::text,target->>'trace_id',target->>'policy_version',(target->>'occurred_at')::timestamptz) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_password_change_notification(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_password_change_notification(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_password_change_notification(text) TO daon_app;

CREATE FUNCTION public.pending_password_change_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'') AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'') AND s.state='pending'
      AND s.source_store='identity' AND s.action='identity.password.changed' AND s.target_type='user'
      AND s.target_id=s.recipient_id AND s.kind='authentication' AND s.severity='info' AND s.notebook_id IS NULL
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE SKIP LOCKED;
END $$;
ALTER FUNCTION public.pending_password_change_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_password_change_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_password_change_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.current_password_change_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb;
BEGIN
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_event_id=p_source AND s.source_store='identity'
    AND s.action='identity.password.changed' AND s.state IN ('pending','delivered');
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_password_change_notification_event(source.origin_event_id);
  IF target IS NULL OR (source.source_event_id,source.action,source.target_type,source.target_id,source.kind,source.severity,
      source.notebook_id,source.trace_id,source.policy_version,source.occurred_at)
    IS DISTINCT FROM (target->>'source_event_id','identity.password.changed'::text,'user'::text,actor,
      'authentication'::text,'info'::text,NULL::text,target->>'trace_id',target->>'policy_version',(target->>'occurred_at')::timestamptz) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  RETURN target-'_event_xid';
END $$;
ALTER FUNCTION public.current_password_change_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_password_change_notification_target(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_password_change_notification_target(text) TO daon_app;

CREATE FUNCTION public.materialize_password_change_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb; notification text; draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_event_id=p_source AND s.state='pending' AND s.source_store='identity'
    AND s.action='identity.password.changed' FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_password_change_notification_target(p_source);
  IF target IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  IF target->'accessible'='true'::jsonb AND NOT p_deliver THEN RETURN NULL; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  notification:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,notification,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,kind,severity,
    title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
  VALUES(tenant,notification,'identity',source.origin_event_id,p_source,'authentication','info',target->>'title',target->>'summary',
    'user',actor,NULL,'/settings/account',source.trace_id,source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO draft FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=notification AND a.action='notification.created';
  IF draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  RETURN jsonb_build_object('state','delivered','notification_id',notification,'draft',draft);
END $$;
ALTER FUNCTION public.materialize_password_change_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_password_change_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_password_change_notification(text,boolean) TO daon_app;
-- S42_PASSWORD_CHANGE_SOURCE_END
-- S42_PASSWORD_RESET_SOURCE_BEGIN
-- 실제 used reset 서버 PK·tenant별 원감사에만 결속한다. token/digest는 읽거나 저장하지 않는다.
CREATE FUNCTION public.current_password_reset_notification_event(p_event text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  original public.security_audit_events; reset_record record;
  event_xid text; user_xid text; effective_role text; accessible boolean; reset_exists boolean;
BEGIN
  IF current_user<>'daon_notebook_delete' OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  SELECT a.* INTO original FROM public.security_audit_events a WHERE a.tenant_id=tenant AND a.event_id=p_event;
  IF original.event_id IS NULL THEN RETURN NULL; END IF;
  IF p_event !~ '^identity-reset-origin-[0-9a-f]{32}$'
    OR (original.actor_id,original.actor_type,original.workspace_id,original.action,
      original.target_type,original.target_id,original.outcome)
    IS DISTINCT FROM (actor,'user'::text,NULL::text,'identity.password.reset.confirmed'::text,'user'::text,actor,'succeeded'::text)
    OR original.before_value IS NOT NULL OR original.after_value IS NOT NULL
    OR original.safe_code IS DISTINCT FROM 'PASSWORD_RESET_CONFIRMED'
    OR original.metadata IS DISTINCT FROM jsonb_build_object('reason_code','PASSWORD_RESET_CONFIRMED') THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT r.token_id,r.user_id,r.used_at,r.expires_at,r.attempts,r.xmin::text AS row_xid INTO reset_record
    FROM public.identity_password_reset_tokens r WHERE r.user_id=actor AND r.used_at IS NOT NULL
      AND p_event='identity-reset-origin-'||substr(encode(sha256(convert_to(r.token_id||'|'||tenant,'UTF8')),'hex'),1,32);
  reset_exists:=FOUND;
  IF reset_exists THEN
    IF reset_record.used_at IS DISTINCT FROM original.occurred_at OR reset_record.expires_at<=reset_record.used_at
      OR reset_record.attempts<1 THEN
      RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
    event_xid:=reset_record.row_xid;
    SELECT u.xmin::text INTO user_xid FROM public.identity_users u WHERE u.user_id=actor;
    -- 생성 호출에만 원reset/사용자 변경의 동일 Tx를 요구한다. 소비 시 과거 xmin은 검사하지 않는다.
    IF user_xid IS DISTINCT FROM event_xid THEN event_xid:=NULL; END IF;
  END IF;
  SELECT CASE WHEN r.user_id IS NOT NULL THEN
      CASE WHEN r.role='personal_owner' AND w.workspace_kind='personal' THEN r.role
        WHEN r.role='organization_admin' AND w.workspace_kind='organization' THEN r.role END
    ELSE m.role END INTO effective_role FROM public.identity_auth_workspaces w
    LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=w.tenant_id AND r.user_id=actor AND r.state='active'
    LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id
      AND m.user_id=actor AND m.state='active' WHERE w.tenant_id=tenant AND w.workspace_id=workspace;
  accessible:=reset_exists AND effective_role IS NOT NULL AND effective_role IN
      ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer')
    AND EXISTS(SELECT 1 FROM public.identity_memberships m
      JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active'
      JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active'
      JOIN public.workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=workspace
      WHERE m.tenant_id=tenant AND m.user_id=actor)
    AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
      JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=workspace
      WHERE o.tenant_id=tenant AND o.user_id=actor AND w.workspace_kind='organization' AND o.state<>'active');
  -- 삭제된 부모는 새 source를 만들 수 없고, 이미 있던 source/알림은 현재 숨김/억제된다.
  RETURN jsonb_build_object('accessible',accessible,'title','비밀번호 재설정 완료',
    'summary','본인 계정의 비밀번호가 재설정되었습니다.','deep_link','/settings/account',
    'source_event_id','identity-reset-event-'||substr(encode(sha256(convert_to(p_event||'|'||workspace,'UTF8')),'hex'),1,32),
    'origin_event_id',p_event,'target_type','user','target_id',actor,'kind','authentication','severity','info',
    'trace_id',original.trace_id,'policy_version',original.policy_version,'occurred_at',original.occurred_at,
    'action','identity.password.reset.confirmed','_event_xid',event_xid);
END $$;
ALTER FUNCTION public.current_password_reset_notification_event(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_password_reset_notification_event(text) FROM PUBLIC,daon_app;

CREATE FUNCTION public.enqueue_password_reset_notification(p_event text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  target jsonb; inserted integer; previous public.notification_source_outbox;
BEGIN
  IF current_setting('app.notification_inbox_enabled',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
  IF current_setting('app.capability',true) IS DISTINCT FROM 'view' THEN
    RAISE EXCEPTION 'NOTIFICATION_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  target:=public.current_password_reset_notification_event(p_event);
  IF target IS NULL OR target->>'_event_xid' IS DISTINCT FROM (pg_current_xact_id()::text::numeric % 4294967296)::text THEN
    RAISE EXCEPTION 'NOTIFICATION_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN RETURN false; END IF;
  INSERT INTO public.notification_source_outbox(tenant_id,workspace_id,source_event_id,origin_event_id,source_store,
    action,target_type,target_id,recipient_id,kind,severity,notebook_id,trace_id,policy_version,occurred_at)
  VALUES(tenant,workspace,target->>'source_event_id',p_event,'identity','identity.password.reset.confirmed','user',actor,actor,
    'authentication','info',NULL,target->>'trace_id',target->>'policy_version',(target->>'occurred_at')::timestamptz)
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN
    SELECT s.* INTO previous FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
      AND s.recipient_id=actor AND s.source_event_id=target->>'source_event_id';
    IF (previous.origin_event_id,previous.source_store,previous.action,previous.target_type,previous.target_id,
      previous.kind,previous.severity,previous.notebook_id,previous.trace_id,previous.policy_version,previous.occurred_at)
      IS DISTINCT FROM (p_event,'identity'::text,'identity.password.reset.confirmed'::text,'user'::text,actor,
        'authentication'::text,'info'::text,NULL::text,target->>'trace_id',target->>'policy_version',(target->>'occurred_at')::timestamptz) THEN
      RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN inserted=1;
END $$;
ALTER FUNCTION public.enqueue_password_reset_notification(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.enqueue_password_reset_notification(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_password_reset_notification(text) TO daon_app;

CREATE FUNCTION public.pending_password_reset_notification_sources(p_limit integer)
RETURNS SETOF public.notification_source_outbox
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  RETURN QUERY SELECT s.* FROM public.notification_source_outbox s
    WHERE s.tenant_id=nullif(current_setting('app.tenant_id',true),'') AND s.workspace_id=nullif(current_setting('app.workspace_id',true),'')
      AND s.recipient_id=nullif(current_setting('app.actor_id',true),'') AND s.state='pending'
      AND s.source_store='identity' AND s.action='identity.password.reset.confirmed' AND s.target_type='user'
      AND s.target_id=s.recipient_id AND s.kind='authentication' AND s.severity='info' AND s.notebook_id IS NULL
    ORDER BY s.occurred_at,s.source_event_id LIMIT p_limit FOR UPDATE SKIP LOCKED;
END $$;
ALTER FUNCTION public.pending_password_reset_notification_sources(integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.pending_password_reset_notification_sources(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_password_reset_notification_sources(integer) TO daon_app;

CREATE FUNCTION public.current_password_reset_notification_target(p_source text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb;
BEGIN
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_event_id=p_source AND s.source_store='identity'
    AND s.action='identity.password.reset.confirmed' AND s.state IN ('pending','delivered');
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_password_reset_notification_event(source.origin_event_id);
  IF target IS NULL OR (source.source_event_id,source.action,source.target_type,source.target_id,source.kind,source.severity,
      source.notebook_id,source.trace_id,source.policy_version,source.occurred_at)
    IS DISTINCT FROM (target->>'source_event_id','identity.password.reset.confirmed'::text,'user'::text,actor,
      'authentication'::text,'info'::text,NULL::text,target->>'trace_id',target->>'policy_version',(target->>'occurred_at')::timestamptz) THEN
    RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  RETURN target-'_event_xid';
END $$;
ALTER FUNCTION public.current_password_reset_notification_target(text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.current_password_reset_notification_target(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_password_reset_notification_target(text) TO daon_app;

CREATE FUNCTION public.materialize_password_reset_notification(p_source text,p_deliver boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
  source public.notification_source_outbox; target jsonb; notification text; draft jsonb;
BEGIN
  IF p_source IS NULL OR p_deliver IS NULL OR tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  SELECT s.* INTO source FROM public.notification_source_outbox s WHERE s.tenant_id=tenant AND s.workspace_id=workspace
    AND s.recipient_id=actor AND s.source_event_id=p_source AND s.state='pending' AND s.source_store='identity'
    AND s.action='identity.password.reset.confirmed' FOR UPDATE;
  IF source.source_event_id IS NULL THEN RETURN NULL; END IF;
  target:=public.current_password_reset_notification_target(p_source);
  IF target IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_SOURCE_INVALID' USING ERRCODE='23514'; END IF;
  IF target->'accessible'='true'::jsonb AND NOT p_deliver THEN RETURN NULL; END IF;
  IF target->'accessible' IS DISTINCT FROM 'true'::jsonb THEN
    UPDATE public.notification_source_outbox SET state='suppressed',delivered_at=CURRENT_TIMESTAMP,version=2
      WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
    RETURN jsonb_build_object('state','suppressed');
  END IF;
  notification:='notification-'||substr(encode(sha256(convert_to(tenant||'|'||workspace||'|'||actor||'|'||p_source,'UTF8')),'hex'),1,32);
  INSERT INTO public.notifications(tenant_id,workspace_id,notification_id,recipient_id,source_event_id)
    VALUES(tenant,workspace,notification,actor,p_source);
  INSERT INTO public.notification_details(tenant_id,notification_id,source_store,origin_event_id,source_event_id,kind,severity,
    title,summary,resource_type,resource_id,notebook_id,deep_link,trace_id,policy_version,delivery_state,delivered_at)
  VALUES(tenant,notification,'identity',source.origin_event_id,p_source,'authentication','info',target->>'title',target->>'summary',
    'user',actor,NULL,'/settings/account',source.trace_id,source.policy_version,'delivered',CURRENT_TIMESTAMP);
  SELECT a.draft INTO draft FROM public.notification_audit_outbox a WHERE a.tenant_id=tenant AND a.workspace_id=workspace
    AND a.recipient_id=actor AND a.notification_id=notification AND a.action='notification.created';
  IF draft IS NULL THEN RAISE EXCEPTION 'NOTIFICATION_AUDIT_INVALID' USING ERRCODE='23514'; END IF;
  UPDATE public.notification_source_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP,version=2
    WHERE tenant_id=tenant AND workspace_id=workspace AND recipient_id=actor AND source_event_id=p_source;
  RETURN jsonb_build_object('state','delivered','notification_id',notification,'draft',draft);
END $$;
ALTER FUNCTION public.materialize_password_reset_notification(text,boolean) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.materialize_password_reset_notification(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.materialize_password_reset_notification(text,boolean) TO daon_app;
-- S42_PASSWORD_RESET_SOURCE_END
