-- Administrator-only 0064 -> 0066, in one transaction with the Alembic stamp.
-- No existing row rewrite, role creation, canonical UPDATE grant, or external I/O.
DO $precondition$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT count(*) FROM public.alembic_version)<>1
    OR (SELECT version_num FROM public.alembic_version)<>'0064'
    OR pg_catalog.to_regclass('public.studio_approval_policy_versions') IS NOT NULL
    OR pg_catalog.to_regclass('public.studio_approval_notices') IS NOT NULL
    OR pg_catalog.to_regprocedure('public.transition_canon_state(text,text,integer,text,text,text,text,text)') IS NULL
    OR NOT EXISTS(SELECT 1 FROM public.canon_transition_rules WHERE entity_type='ApprovalRequest' AND source_state='pending' AND target_state='expired')
    OR NOT EXISTS(SELECT 1 FROM public.canon_transition_rules WHERE entity_type='ApprovalRequest' AND source_state='pending' AND target_state='withdrawn') THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_MIGRATION_PRECONDITION' USING ERRCODE='55000';
  END IF;
END $precondition$;

-- L1.3a: the one omitted 0060 FK parent. No canonical row rewrite.
DO $approval_fk_owner$
DECLARE
  relation oid:='public.approvals'::regclass;
  definitions_before jsonb; definitions_after jsonb; rows_before text; rows_after text;
  app_privileges text[]; privilege text; guard record;
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
      AND NOT rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb)
    OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid='daon_app'::regrole AND member='daon_notebook_delete'::regrole AND NOT admin_option)
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid=relation AND relrowsecurity AND relforcerowsecurity AND relowner='daon_app'::regrole)
    OR pg_catalog.has_table_privilege('daon_app',relation,'UPDATE') OR pg_catalog.has_table_privilege('daon_app',relation,'DELETE')
    OR NOT pg_catalog.has_table_privilege('daon_app',relation,'SELECT') OR NOT pg_catalog.has_table_privilege('daon_app',relation,'INSERT')
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid=relation AND attacl IS NOT NULL)
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c,LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) acl
      WHERE c.oid=relation AND (acl.grantee=0 OR acl.is_grantable OR acl.grantee NOT IN ('daon_app'::regrole,'daon_notebook_delete'::regrole))) THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_FK_OWNER_PRECONDITION' USING ERRCODE='55000';
  END IF;
  FOR guard IN SELECT * FROM (VALUES
    ('validate_canon_insert','f5cde9f1dd02bbab70ceaabf5590f8ba',7),
    ('reject_canon_immutable_mutation','85dbf2028dbced19cb0bdf00c391d4be',27)
  ) expected(name,digest,trigger_type) LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_trigger t ON t.tgfoid=p.oid
      WHERE p.oid=pg_catalog.to_regprocedure('public.'||guard.name||'()') AND NOT p.prosecdef
      AND md5(p.prosrc)=guard.digest AND t.tgrelid=relation AND NOT t.tgisinternal AND t.tgenabled IN ('O','A') AND t.tgtype=guard.trigger_type) THEN
      RAISE EXCEPTION 'STUDIO_APPROVAL_FK_GUARD_PRECONDITION' USING ERRCODE='55000';
    END IF;
  END LOOP;
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint k WHERE k.conname='deliveries_approval_id_fk'
    AND k.conrelid='public.deliveries'::regclass AND k.confrelid=relation AND k.contype='f' AND k.convalidated
    AND k.conkey=ARRAY(SELECT a.attnum FROM unnest(ARRAY['tenant_id','workspace_id','approval_id']) WITH ORDINALITY s(name,pos)
      JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attname=s.name ORDER BY s.pos)
    AND k.confkey=ARRAY(SELECT a.attnum FROM unnest(ARRAY['tenant_id','workspace_id','record_id']) WITH ORDINALITY s(name,pos)
      JOIN pg_catalog.pg_attribute a ON a.attrelid=k.confrelid AND a.attname=s.name ORDER BY s.pos)) THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_FK_PRECONDITION' USING ERRCODE='55000';
  END IF;
  LOCK TABLE public.approvals IN ACCESS EXCLUSIVE MODE;
  SELECT array_agg(p) INTO app_privileges FROM unnest(ARRAY['SELECT','INSERT','TRUNCATE','REFERENCES','TRIGGER']) p
    WHERE pg_catalog.has_table_privilege('daon_app',relation,p);
  IF current_setting('server_version_num')::integer>=170000 AND pg_catalog.has_table_privilege('daon_app',relation,'MAINTAIN') THEN
    app_privileges:=app_privileges||'MAINTAIN'::text;
  END IF;
  SELECT jsonb_build_object('rls',c.relrowsecurity,'force',c.relforcerowsecurity,
    'columns',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum) FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid),
    'defaults',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.adnum) FROM pg_catalog.pg_attrdef d WHERE d.adrelid=c.oid),
    'constraints',(SELECT jsonb_agg(jsonb_build_array(k.oid,pg_catalog.pg_get_constraintdef(k.oid),k.convalidated) ORDER BY k.oid) FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid OR k.confrelid=c.oid),
    'triggers',(SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid),
    'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid) FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid))
    INTO definitions_before FROM pg_catalog.pg_class c WHERE c.oid=relation;
  SELECT md5(coalesce(string_agg(to_jsonb(a)::text,E'\n' ORDER BY tenant_id,workspace_id,record_id),'')) INTO rows_before FROM public.approvals a;
  ALTER TABLE public.approvals OWNER TO daon_notebook_delete;
  REVOKE ALL ON public.approvals FROM daon_app;
  FOREACH privilege IN ARRAY app_privileges LOOP
    EXECUTE format('GRANT %s ON TABLE public.approvals TO daon_app',privilege);
  END LOOP;
  GRANT UPDATE ON public.approvals TO daon_notebook_delete;
  SELECT jsonb_build_object('rls',c.relrowsecurity,'force',c.relforcerowsecurity,
    'columns',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum) FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid),
    'defaults',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.adnum) FROM pg_catalog.pg_attrdef d WHERE d.adrelid=c.oid),
    'constraints',(SELECT jsonb_agg(jsonb_build_array(k.oid,pg_catalog.pg_get_constraintdef(k.oid),k.convalidated) ORDER BY k.oid) FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid OR k.confrelid=c.oid),
    'triggers',(SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid),
    'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid) FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid))
    INTO definitions_after FROM pg_catalog.pg_class c WHERE c.oid=relation;
  SELECT md5(coalesce(string_agg(to_jsonb(a)::text,E'\n' ORDER BY tenant_id,workspace_id,record_id),'')) INTO rows_after FROM public.approvals a;
  IF definitions_before IS DISTINCT FROM definitions_after OR rows_before IS DISTINCT FROM rows_after
    OR pg_catalog.has_table_privilege('daon_app',relation,'UPDATE') OR pg_catalog.has_table_privilege('daon_app',relation,'DELETE')
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete',relation,'UPDATE')
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid=relation AND relowner='daon_notebook_delete'::regrole AND relrowsecurity AND relforcerowsecurity)
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c,LATERAL pg_catalog.aclexplode(c.relacl) acl WHERE c.oid=relation AND acl.grantee=0) THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_FK_OWNER_POSTCONDITION' USING ERRCODE='55000';
  END IF;
  FOREACH privilege IN ARRAY app_privileges LOOP
    IF NOT pg_catalog.has_table_privilege('daon_app',relation,privilege) THEN
      RAISE EXCEPTION 'STUDIO_APPROVAL_FK_ACL_POSTCONDITION' USING ERRCODE='55000';
    END IF;
  END LOOP;
END $approval_fk_owner$;

CREATE TABLE public.studio_approval_policy_versions (
  tenant_id text NOT NULL REFERENCES public.tenants(tenant_id),
  policy_version bigint NOT NULL CHECK(policy_version>0),
  ttl_days smallint NOT NULL CHECK(ttl_days BETWEEN 1 AND 30),
  changed_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(tenant_id,policy_version),
  FOREIGN KEY(tenant_id,changed_by) REFERENCES public.user_accounts(tenant_id,user_id)
);
CREATE TABLE public.studio_approval_notices (
  tenant_id text NOT NULL,
  workspace_id text NOT NULL,
  approval_request_id text NOT NULL,
  recipient_id text NOT NULL,
  event_kind text NOT NULL CHECK(event_kind IN ('due_24h','expired','withdrawn')),
  notification_id text NOT NULL CHECK(notification_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
  scheduled_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  read_at timestamptz,
  version integer NOT NULL DEFAULT 1 CHECK(version>0),
  PRIMARY KEY(tenant_id,notification_id),
  UNIQUE(tenant_id,workspace_id,approval_request_id,recipient_id,event_kind),
  FOREIGN KEY(tenant_id,workspace_id) REFERENCES public.workspaces(tenant_id,workspace_id),
  FOREIGN KEY(tenant_id,workspace_id,approval_request_id) REFERENCES public.approval_requests(tenant_id,workspace_id,record_id),
  FOREIGN KEY(tenant_id,recipient_id) REFERENCES public.user_accounts(tenant_id,user_id)
);
CREATE INDEX studio_approval_notices_recipient ON public.studio_approval_notices(tenant_id,workspace_id,recipient_id,created_at DESC);

ALTER TABLE public.studio_approval_policy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.studio_approval_policy_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY studio_approval_policy_read ON public.studio_approval_policy_versions FOR SELECT TO daon_app
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),''));
CREATE POLICY studio_approval_policy_insert ON public.studio_approval_policy_versions FOR INSERT TO daon_app
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND changed_by=nullif(current_setting('app.actor_id',true),''));
ALTER TABLE public.studio_approval_notices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.studio_approval_notices FORCE ROW LEVEL SECURITY;
CREATE POLICY studio_approval_notice_recipient ON public.studio_approval_notices TO daon_app
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
        AND recipient_id=nullif(current_setting('app.actor_id',true),''))
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
        AND recipient_id=nullif(current_setting('app.actor_id',true),''));
REVOKE ALL ON public.studio_approval_policy_versions,public.studio_approval_notices FROM PUBLIC,daon_app;
GRANT SELECT,INSERT ON public.studio_approval_policy_versions,public.studio_approval_notices TO daon_app;
GRANT UPDATE(read_at,version) ON public.studio_approval_notices TO daon_app;

CREATE FUNCTION public.studio_approval_deadline(payload jsonb, created timestamptz)
RETURNS timestamptz LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp SET TimeZone='UTC' AS $$
DECLARE ttl integer; expiry timestamptz; reminder timestamptz;
BEGIN
  IF NOT payload ?| ARRAY['ttl_days','ttl_policy_version','expires_at','remind_at'] THEN
    RETURN created+interval '7 days';
  END IF;
  IF NOT payload ?& ARRAY['ttl_days','ttl_policy_version','expires_at','remind_at']
    OR jsonb_typeof(payload->'ttl_days')<>'number' OR payload->>'ttl_days' !~ '^[0-9]+$'
    OR jsonb_typeof(payload->'ttl_policy_version')<>'number' OR payload->>'ttl_policy_version' !~ '^[0-9]+$'
    OR jsonb_typeof(payload->'expires_at')<>'string' OR jsonb_typeof(payload->'remind_at')<>'string'
    OR payload->>'expires_at' !~ '(Z|\+00:00)$' OR payload->>'remind_at' !~ '(Z|\+00:00)$' THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_DEADLINE_INVALID' USING ERRCODE='23514';
  END IF;
  ttl:=(payload->>'ttl_days')::integer;
  expiry:=(payload->>'expires_at')::timestamptz; reminder:=(payload->>'remind_at')::timestamptz;
  IF ttl NOT BETWEEN 1 AND 30 OR (payload->>'ttl_policy_version')::bigint<0
    OR expiry<>created+make_interval(days=>ttl) OR reminder<>expiry-interval '24 hours' THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_DEADLINE_INVALID' USING ERRCODE='23514';
  END IF;
  RETURN expiry;
END $$;

-- App has no canonical UPDATE privilege (row locks require it too). This
-- lock-only entrypoint uses the existing non-login owner, never BYPASSRLS.
-- No caller-supplied tenant, actor, state, recipient, or mutation is accepted.
CREATE FUNCTION public.lock_studio_approval_version(p_version_id text, p_notebook_id text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),'');
  actor text:=nullif(current_setting('app.actor_id',true),'');
BEGIN
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL OR p_version_id IS NULL OR p_notebook_id IS NULL
    OR p_version_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'
    OR p_notebook_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$' THEN
    RETURN false;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.identity_users WHERE user_id=actor AND state='active')
    OR NOT (EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles WHERE tenant_id=tenant AND user_id=actor AND state='active')
      OR EXISTS(SELECT 1 FROM public.identity_auth_memberships WHERE tenant_id=tenant AND workspace_id=workspace AND user_id=actor AND state='active'))
    OR NOT EXISTS(SELECT 1 FROM public.notebooks n JOIN public.notebook_bindings b
      ON b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id
      WHERE n.tenant_id=tenant AND n.workspace_id=workspace AND n.notebook_id=p_notebook_id AND n.created_by=actor
      AND b.binding_kind='output_version' AND b.record_id=p_version_id
      AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=tenant
        AND d.workspace_id=workspace AND d.notebook_id=p_notebook_id AND d.state IN ('accepted','deleting','completed'))) THEN
    RETURN false;
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('studio-approval|'||tenant||'|'||workspace||'|'||p_version_id,0));
  PERFORM 1 FROM public.output_versions WHERE tenant_id=tenant AND workspace_id=workspace AND record_id=p_version_id FOR UPDATE;
  IF NOT FOUND THEN RETURN false; END IF;
  PERFORM 1 FROM public.approval_requests WHERE tenant_id=tenant AND workspace_id=workspace
    AND output_version_id=p_version_id AND state='pending' ORDER BY record_id FOR UPDATE;
  -- Recheck after any lock wait, not only before entering the serialization point.
  IF EXISTS(SELECT 1 FROM public.evidence_references er JOIN public.identity_auth_source_access a
    ON a.tenant_id=er.tenant_id AND a.workspace_id=er.workspace_id AND a.source_version_id=er.source_version_id
    WHERE er.tenant_id=tenant AND er.workspace_id=workspace AND er.output_version_id=p_version_id AND a.user_id=actor AND a.allowed=0) THEN
    RETURN false;
  END IF;
  RETURN true;
END $$;
ALTER FUNCTION public.lock_studio_approval_version(text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.lock_studio_approval_version(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.lock_studio_approval_version(text,text) TO daon_app;

CREATE FUNCTION public.guard_studio_approval_policy() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE current_version bigint; workspace text:=nullif(current_setting('app.workspace_id',true),'');
BEGIN
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_POLICY_IMMUTABLE' USING ERRCODE='55000';
  END IF;
  IF NEW.tenant_id IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
    OR NEW.changed_by IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'')
    OR NEW.created_at<>CURRENT_TIMESTAMP OR NOT EXISTS(
      SELECT 1 FROM public.identity_auth_tenant_roles r JOIN public.identity_users u ON u.user_id=r.user_id
      WHERE r.tenant_id=NEW.tenant_id AND r.user_id=NEW.changed_by AND r.state='active' AND u.state='active'
      AND r.role IN ('personal_owner','organization_admin')) THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_POLICY_DENIED' USING ERRCODE='42501';
  END IF;
  -- Server already verifies current session/grant/Step-up. Direct INSERT also
  -- needs the exact consumed operation receipt, not a caller's purpose flag.
  IF NOT EXISTS(SELECT 1 FROM public.identity_step_up_consumptions c
      JOIN public.identity_step_up_authorizations a ON a.step_up_id=c.step_up_id
      WHERE c.tenant_id=NEW.tenant_id AND c.actor_id=NEW.changed_by
      AND c.operation='studio.approval.policy.save'
      AND c.idempotency_key=nullif(current_setting('app.studio_approval_policy_key',true),'')
      AND a.action_group='organization_security_or_connector_policy_change'
      AND a.target_id=nullif(current_setting('app.studio_approval_policy_target',true),'')
      AND a.target_id='studio-approval-policy-'||encode(sha256(convert_to(replace(jsonb_build_array(NEW.tenant_id,workspace)::text,' ',''),'UTF8')),'hex')
      AND a.policy_version=nullif(current_setting('app.studio_approval_policy_version',true),'')
      AND a.used_at IS NOT NULL AND workspace IS NOT NULL) THEN
    RAISE EXCEPTION 'STEP_UP_REQUIRED' USING ERRCODE='42501';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('studio-approval-policy|'||NEW.tenant_id,0));
  SELECT coalesce(max(policy_version),0) INTO current_version FROM public.studio_approval_policy_versions WHERE tenant_id=NEW.tenant_id;
  IF NEW.policy_version<>current_version+1 THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_POLICY_CONFLICT' USING ERRCODE='40001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER studio_approval_policy_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.studio_approval_policy_versions
FOR EACH ROW EXECUTE FUNCTION public.guard_studio_approval_policy();

CREATE FUNCTION public.guard_studio_approval_notice() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE request public.approval_requests%ROWTYPE; expiry timestamptz; scheduled timestamptz;
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_NOTICE_IMMUTABLE' USING ERRCODE='55000';
  END IF;
  IF TG_OP='UPDATE' THEN
    IF (to_jsonb(NEW)-ARRAY['read_at','version']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['read_at','version'])
      OR OLD.read_at IS NOT NULL OR NEW.read_at IS NULL OR NEW.read_at<>CURRENT_TIMESTAMP
      OR NEW.version<>OLD.version+1 OR NEW.recipient_id IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') THEN
      RAISE EXCEPTION 'STUDIO_APPROVAL_NOTICE_IMMUTABLE' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO request FROM public.approval_requests WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND record_id=NEW.approval_request_id;
  IF NOT FOUND OR NEW.recipient_id<>request.created_by OR NEW.created_at<>CURRENT_TIMESTAMP OR NEW.read_at IS NOT NULL OR NEW.version<>1 THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_NOTICE_DENIED' USING ERRCODE='42501';
  END IF;
  expiry:=public.studio_approval_deadline(request.canonical_json,request.created_at);
  IF NEW.event_kind='due_24h' AND request.state='pending' AND clock_timestamp()>=expiry-interval '24 hours' AND clock_timestamp()<expiry THEN
    scheduled:=expiry-interval '24 hours';
  ELSIF NEW.event_kind='expired' AND request.state='expired' AND clock_timestamp()>=expiry THEN
    scheduled:=expiry;
  ELSIF NEW.event_kind='withdrawn' AND request.state='withdrawn' THEN
    SELECT occurred_at INTO scheduled FROM public.canon_state_transitions WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id
      AND entity_type='ApprovalRequest' AND record_id=request.record_id AND target_state='withdrawn' ORDER BY transition_version DESC LIMIT 1;
  END IF;
  IF scheduled IS NULL OR NEW.scheduled_at<>scheduled THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_NOTICE_DENIED' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER studio_approval_notice_guard BEFORE INSERT OR UPDATE OR DELETE ON public.studio_approval_notices
FOR EACH ROW EXECUTE FUNCTION public.guard_studio_approval_notice();

CREATE FUNCTION public.guard_studio_approval_request() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE days integer; policy bigint;
BEGIN
  IF TG_OP='UPDATE' THEN
    IF OLD.state='pending' AND NEW.state IN ('approved','rejected')
      AND clock_timestamp()>=public.studio_approval_deadline(OLD.canonical_json,OLD.created_at) THEN
      RAISE EXCEPTION 'APPROVAL_REQUEST_EXPIRED' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('studio-approval|'||NEW.tenant_id||'|'||NEW.workspace_id||'|'||NEW.output_version_id,0));
  IF EXISTS(SELECT 1 FROM public.approval_requests r WHERE r.tenant_id=NEW.tenant_id AND r.workspace_id=NEW.workspace_id AND r.output_version_id=NEW.output_version_id AND r.state='pending') THEN
    RAISE EXCEPTION 'APPROVAL_REQUEST_PENDING' USING ERRCODE='23514';
  END IF;
  SELECT ttl_days,policy_version INTO days,policy FROM public.studio_approval_policy_versions WHERE tenant_id=NEW.tenant_id ORDER BY policy_version DESC LIMIT 1;
  days:=coalesce(days,7); policy:=coalesce(policy,0);
  IF NEW.created_at<>CURRENT_TIMESTAMP OR NEW.canonical_json->'ttl_days' IS DISTINCT FROM to_jsonb(days)
    OR NEW.canonical_json->'ttl_policy_version' IS DISTINCT FROM to_jsonb(policy) THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_DEADLINE_INVALID' USING ERRCODE='23514';
  END IF;
  PERFORM public.studio_approval_deadline(NEW.canonical_json,NEW.created_at);
  RETURN NEW;
END $$;
CREATE TRIGGER studio_approval_deadline_insert BEFORE INSERT ON public.approval_requests
FOR EACH ROW EXECUTE FUNCTION public.guard_studio_approval_request();
CREATE TRIGGER studio_approval_deadline_transition BEFORE UPDATE ON public.approval_requests
FOR EACH ROW EXECUTE FUNCTION public.guard_studio_approval_request();

CREATE FUNCTION public.record_studio_approval_notice(request_id text,event text) RETURNS boolean
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE request public.approval_requests%ROWTYPE; scheduled timestamptz; inserted integer;
BEGIN
  SELECT * INTO request FROM public.approval_requests WHERE tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'') AND record_id=request_id;
  IF NOT FOUND OR request.created_by IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_NOTICE_DENIED' USING ERRCODE='42501';
  END IF;
  IF event='due_24h' THEN scheduled:=public.studio_approval_deadline(request.canonical_json,request.created_at)-interval '24 hours';
  ELSIF event='expired' THEN scheduled:=public.studio_approval_deadline(request.canonical_json,request.created_at);
  ELSIF event='withdrawn' THEN
    SELECT occurred_at INTO scheduled FROM public.canon_state_transitions WHERE tenant_id=request.tenant_id AND workspace_id=request.workspace_id
      AND entity_type='ApprovalRequest' AND record_id=request_id AND target_state='withdrawn' ORDER BY transition_version DESC LIMIT 1;
  ELSE RAISE EXCEPTION 'STUDIO_APPROVAL_NOTICE_DENIED' USING ERRCODE='42501'; END IF;
  INSERT INTO public.studio_approval_notices(tenant_id,workspace_id,approval_request_id,recipient_id,event_kind,notification_id,scheduled_at)
    VALUES(request.tenant_id,request.workspace_id,request.record_id,request.created_by,event,
      'approval-notice-'||md5(request.tenant_id||'|'||request.workspace_id||'|'||request.record_id||'|'||event),scheduled)
    ON CONFLICT(tenant_id,workspace_id,approval_request_id,recipient_id,event_kind) DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  RETURN inserted=1;
END $$;

CREATE FUNCTION public.expire_studio_approval_request(request_id text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE request public.approval_requests%ROWTYPE; result record; tenant text:=nullif(current_setting('app.tenant_id',true),'');
  workspace text:=nullif(current_setting('app.workspace_id',true),''); actor text:=nullif(current_setting('app.actor_id',true),'');
BEGIN
  IF tenant IS NULL OR workspace IS NULL OR actor IS NULL THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_SCOPE_DENIED' USING ERRCODE='42501';
  END IF;
  SELECT * INTO request FROM public.approval_requests WHERE tenant_id=tenant AND workspace_id=workspace AND record_id=request_id;
  IF NOT FOUND THEN RETURN false; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('studio-approval|'||tenant||'|'||workspace||'|'||request.output_version_id,0));
  PERFORM 1 FROM public.output_versions WHERE tenant_id=tenant AND workspace_id=workspace AND record_id=request.output_version_id FOR UPDATE;
  SELECT * INTO request FROM public.approval_requests WHERE tenant_id=tenant AND workspace_id=workspace AND record_id=request_id FOR UPDATE;
  IF request.state<>'pending' OR clock_timestamp()<public.studio_approval_deadline(request.canonical_json,request.created_at) THEN RETURN false; END IF;
  SELECT * INTO result FROM public.transition_canon_state('ApprovalRequest',request_id,request.version,'expired',
    'approval-expire-'||md5(tenant||'|'||workspace||'|'||request_id),'APPROVAL_DEADLINE_EXPIRED',
    request.trace_id,'s24-approval-db-v1');
  IF result.outcome<>'succeeded' THEN RAISE EXCEPTION 'STUDIO_STATE_INVALID' USING ERRCODE='55000'; END IF;
  -- Recipient is derived only from the canonical requester. Restore caller scope.
  PERFORM set_config('app.actor_id',request.created_by,true);
  PERFORM public.record_studio_approval_notice(request_id,'expired');
  PERFORM set_config('app.actor_id',actor,true);
  RETURN true;
END $$;

CREATE FUNCTION public.sweep_studio_approval_lifecycle(worker_id text,batch_limit integer DEFAULT 100)
RETURNS TABLE(processed_count integer,notice_count integer,expired_count integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE candidate record; request public.approval_requests%ROWTYPE; added boolean;
  old_t text:=current_setting('app.tenant_id',true); old_w text:=current_setting('app.workspace_id',true); old_a text:=current_setting('app.actor_id',true);
BEGIN
  IF worker_id IS NULL OR worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$' OR batch_limit IS NULL OR batch_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'STUDIO_APPROVAL_SWEEP_INVALID' USING ERRCODE='22023';
  END IF;
  processed_count:=0; notice_count:=0; expired_count:=0;
  FOR candidate IN SELECT r.tenant_id,r.workspace_id,r.record_id,r.output_version_id FROM public.approval_requests r
    WHERE r.state='pending' AND clock_timestamp()>=public.studio_approval_deadline(r.canonical_json,r.created_at)-interval '24 hours'
      AND (clock_timestamp()>=public.studio_approval_deadline(r.canonical_json,r.created_at)
        OR NOT EXISTS(SELECT 1 FROM public.studio_approval_notices n WHERE n.tenant_id=r.tenant_id AND n.workspace_id=r.workspace_id
          AND n.approval_request_id=r.record_id AND n.event_kind='due_24h'))
    ORDER BY public.studio_approval_deadline(r.canonical_json,r.created_at),r.tenant_id,r.workspace_id,r.record_id LIMIT batch_limit
  LOOP
    -- Same version -> request lock order as approval/withdraw. Never hold a
    -- request lock while waiting for a version owned by another consumer.
    IF NOT pg_try_advisory_xact_lock(hashtextextended('studio-approval|'||candidate.tenant_id||'|'||candidate.workspace_id||'|'||candidate.output_version_id,0)) THEN CONTINUE; END IF;
    PERFORM 1 FROM public.output_versions WHERE tenant_id=candidate.tenant_id AND workspace_id=candidate.workspace_id AND record_id=candidate.output_version_id FOR UPDATE;
    SELECT * INTO request FROM public.approval_requests WHERE tenant_id=candidate.tenant_id AND workspace_id=candidate.workspace_id AND record_id=candidate.record_id FOR UPDATE SKIP LOCKED;
    IF NOT FOUND OR request.state<>'pending' THEN CONTINUE; END IF;
    PERFORM set_config('app.tenant_id',request.tenant_id,true);
    PERFORM set_config('app.workspace_id',request.workspace_id,true);
    PERFORM set_config('app.actor_id',request.created_by,true);
    IF clock_timestamp()>=public.studio_approval_deadline(request.canonical_json,request.created_at) THEN
      added:=public.expire_studio_approval_request(request.record_id);
      IF added THEN expired_count:=expired_count+1; notice_count:=notice_count+1; END IF;
    ELSE
      added:=public.record_studio_approval_notice(request.record_id,'due_24h');
      IF added THEN notice_count:=notice_count+1; END IF;
    END IF;
    processed_count:=processed_count+1;
  END LOOP;
  PERFORM set_config('app.tenant_id',coalesce(old_t,''),true);
  PERFORM set_config('app.workspace_id',coalesce(old_w,''),true);
  PERFORM set_config('app.actor_id',coalesce(old_a,''),true);
  RETURN NEXT;
END $$;

-- Both definer entrypoints are owned by the checked installation superuser,
-- exactly like the existing bounded queue claim. No role gains BYPASSRLS.
ALTER FUNCTION public.expire_studio_approval_request(text) OWNER TO CURRENT_USER;
ALTER FUNCTION public.sweep_studio_approval_lifecycle(text,integer) OWNER TO CURRENT_USER;
REVOKE ALL ON FUNCTION public.studio_approval_deadline(jsonb,timestamptz),public.record_studio_approval_notice(text,text),
  public.expire_studio_approval_request(text),public.sweep_studio_approval_lifecycle(text,integer),
  public.guard_studio_approval_policy(),public.guard_studio_approval_notice(),public.guard_studio_approval_request() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.studio_approval_deadline(jsonb,timestamptz),public.record_studio_approval_notice(text,text),
  public.expire_studio_approval_request(text),public.sweep_studio_approval_lifecycle(text,integer) TO daon_app;
