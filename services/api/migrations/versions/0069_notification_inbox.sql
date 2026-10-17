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
    AND (deep_link ~ '^/(operations|inbox|notifications|workspaces)([/?:#]|$)'
      OR deep_link ~ '^/notebooks/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}[?](output_version_id|source_version_id|run_id)=[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
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
  IF v_role IS NULL OR v_role NOT IN ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer') THEN
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
BEGIN
  IF v_tenant IS NULL OR v_workspace IS NULL OR v_actor IS NULL THEN
    RAISE EXCEPTION 'NOTIFICATION_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_pending FROM public.notification_source_outbox s
    WHERE s.tenant_id=v_tenant AND s.workspace_id=v_workspace AND s.recipient_id=v_actor
      AND s.source_event_id=p_source AND s.source_store IN ('document','question') AND s.state='pending';
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
  IF v_role IS NULL OR v_role NOT IN ('personal_owner','organization_admin','workspace_admin','editor','reviewer','approver','viewer') THEN
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
  ELSE
    v_result:=jsonb_build_object('deep_link','/notebooks/'||p_notebook||'?run_id='||v_origin.target_id,'title','질문 처리 완료',
      'summary',CASE WHEN v_run.insufficient='true'::jsonb THEN '질문 처리 Run이 완료되었으며 근거가 부족합니다.' ELSE '질문 처리 Run이 완료되었습니다.' END);
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
      AND s.source_event_id=p_source AND s.source_store='studio' AND s.state='pending' FOR UPDATE;
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
      AND s.source_store IN ('document','question') AND s.state='pending'
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
      AND s.source_event_id=p_source AND s.source_store IN ('document','question') AND s.state='pending' FOR UPDATE;
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
