-- 0068 기반의 additive 후보. 기존 표/행/owner/RLS/클러스터 역할은 변경하지 않는다.
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
    AND deep_link ~ '^/(operations|inbox|notifications|workspaces)([/?:#]|$)' AND deep_link !~ '^//'),
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
      OR NEW.draft->'metadata' IS DISTINCT FROM jsonb_build_object('source_event_id',NEW.source_event_id)
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
