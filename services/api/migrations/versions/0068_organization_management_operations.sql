-- 기존 행·owner·RLS는 유지한다. 0037의 pending index는 정확 검증 후 재사용한다.
DO $$ BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT count(*) FROM public.alembic_version)<>1
    OR (SELECT version_num FROM public.alembic_version)<>'0067' THEN
    RAISE EXCEPTION 'ORGANIZATION_MIGRATION_PRECONDITION' USING ERRCODE='55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
    WHERE i.indexrelid=to_regclass('public.identity_org_join_pending_unique')
      AND i.indrelid='public.identity_org_join_requests'::regclass AND i.indisunique
      AND i.indisvalid AND i.indisready AND i.indnkeyatts=2 AND i.indnatts=2
      AND i.indexprs IS NULL AND c.relam=(SELECT oid FROM pg_catalog.pg_am WHERE amname='btree')
      AND pg_get_indexdef(i.indexrelid,1,true)='tenant_id'
      AND pg_get_indexdef(i.indexrelid,2,true)='user_id'
      AND pg_get_expr(i.indpred,i.indrelid)='(state = ''pending''::text)'
  ) THEN RAISE EXCEPTION 'ORGANIZATION_PENDING_INDEX_INVALID' USING ERRCODE='55000'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_organization_owner') THEN
    CREATE ROLE daon_organization_owner NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_organization_owner'
    AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls
    AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
    OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='daon_organization_owner')) THEN
    RAISE EXCEPTION 'ORGANIZATION_OWNER_INVALID' USING ERRCODE='55000';
  END IF;
END $$;

ALTER TABLE public.identity_org_invitation_codes ADD COLUMN workspace_id text;
ALTER TABLE public.identity_org_tenant_memberships ADD COLUMN join_workspace_id text;

CREATE FUNCTION public.guard_identity_org_workspace() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $fn$
DECLARE selected_workspace text;
BEGIN
  IF TG_TABLE_NAME='identity_org_invitation_codes' THEN
    selected_workspace:=NEW.workspace_id;
    IF TG_OP='UPDATE' AND OLD.workspace_id IS NOT NULL
      AND (NEW.workspace_id IS DISTINCT FROM OLD.workspace_id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id) THEN
      RAISE EXCEPTION 'ORGANIZATION_SCOPE_IMMUTABLE' USING ERRCODE='23514';
    END IF;
  ELSE
    selected_workspace:=NEW.join_workspace_id;
    IF TG_OP='UPDATE' AND OLD.join_workspace_id IS NOT NULL
      AND (NEW.join_workspace_id IS DISTINCT FROM OLD.join_workspace_id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.user_id IS DISTINCT FROM OLD.user_id) THEN
      RAISE EXCEPTION 'ORGANIZATION_SCOPE_IMMUTABLE' USING ERRCODE='23514';
    END IF;
  END IF;
  IF selected_workspace IS NOT NULL THEN
    PERFORM 1 FROM public.identity_auth_workspaces WHERE workspace_id=selected_workspace
      AND tenant_id=NEW.tenant_id AND workspace_kind='organization' FOR KEY SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'ORGANIZATION_WORKSPACE_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END
$fn$;
CREATE TRIGGER identity_org_invitation_workspace_guard BEFORE INSERT OR UPDATE ON public.identity_org_invitation_codes
  FOR EACH ROW EXECUTE FUNCTION public.guard_identity_org_workspace();
CREATE TRIGGER identity_org_membership_workspace_guard BEFORE INSERT OR UPDATE ON public.identity_org_tenant_memberships
  FOR EACH ROW EXECUTE FUNCTION public.guard_identity_org_workspace();

CREATE FUNCTION public.valid_identity_org_operation(operation text,response_payload text,audit_payload text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $fn$
DECLARE response jsonb:=response_payload::jsonb; audit jsonb:=audit_payload::jsonb;
  allowed text[]; item record; expected_action text;
BEGIN
  IF operation IN ('organization.invitation.create','organization.invitation.revoke') THEN
    allowed:=ARRAY['invitation_id','tenant_id','workspace_id','created_by','expires_at','max_uses','used_count','state','version','created_at','updated_at'];
    expected_action:=CASE operation WHEN 'organization.invitation.create' THEN 'organization.invitation.created' ELSE 'organization.invitation.revoked' END;
  ELSIF operation IN ('organization.join.create','organization.join.decide') THEN
    allowed:=ARRAY['request_id','tenant_id','user_id','invitation_id','state','decision_reason','decided_by','version','created_at','updated_at'];
    expected_action:=CASE operation WHEN 'organization.join.create' THEN 'organization.join_request.created' ELSE 'organization.join_request.decided' END;
  ELSIF operation IN ('organization.member.role','organization.member.state') THEN
    allowed:=ARRAY['tenant_id','user_id','role','state','version','updated_at','join_workspace_id','access_role','access_state','access_version'];
    expected_action:=CASE operation WHEN 'organization.member.role' THEN 'organization.membership.role_changed' ELSE 'organization.membership.state_changed' END;
  ELSE RETURN false; END IF;
  IF jsonb_typeof(response)<>'object' OR NOT response ?& allowed OR response-allowed<>'{}'::jsonb THEN RETURN false; END IF;
  FOR item IN SELECT key,value FROM jsonb_each(response) LOOP
    IF item.key IN ('version','access_version','max_uses','used_count') THEN
      IF item.key='access_version' AND item.value='null'::jsonb THEN CONTINUE; END IF;
      IF jsonb_typeof(item.value)<>'number' OR item.value::text !~ '^[0-9]+$'
        OR item.value::text::numeric>9007199254740991 THEN RETURN false; END IF;
      IF item.key<>'used_count' AND item.value::text::numeric<1 THEN RETURN false; END IF;
    ELSIF item.value='null'::jsonb THEN
      IF item.key NOT IN ('workspace_id','join_workspace_id','access_role','access_state','decision_reason','decided_by','invitation_id') THEN RETURN false; END IF;
    ELSE
      IF jsonb_typeof(item.value)<>'string' OR length(item.value#>>'{}')>1000 THEN RETURN false; END IF;
      IF item.key IN ('created_at','updated_at','expires_at') THEN
        IF (item.value#>>'{}') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|\+00:00)$' THEN RETURN false; END IF;
        PERFORM (item.value#>>'{}')::timestamptz;
      ELSIF item.key NOT IN ('decision_reason') AND (item.value#>>'{}') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$' THEN RETURN false;
      END IF;
    END IF;
  END LOOP;
  IF jsonb_typeof(audit)<>'object' OR NOT audit ?& ARRAY['action','workspace_id','before','after','reason','version','auth_version']
    OR audit-ARRAY['action','workspace_id','before','after','reason','version','auth_version']<>'{}'::jsonb
    OR jsonb_typeof(audit->'action')<>'string' OR audit->>'action'<>expected_action THEN RETURN false; END IF;
  FOR item IN SELECT key,value FROM jsonb_each(audit) LOOP
    IF item.key='action' THEN CONTINUE; END IF;
    IF item.value='null'::jsonb THEN CONTINUE; END IF;
    IF item.key IN ('version','auth_version') THEN
      IF jsonb_typeof(item.value)<>'number' OR item.value::text !~ '^[1-9][0-9]*$'
        OR item.value::text::numeric>9007199254740991 THEN RETURN false; END IF;
    ELSE
      IF jsonb_typeof(item.value)<>'string' OR length(item.value#>>'{}')>1000 THEN RETURN false; END IF;
      IF item.key<>'reason' AND (item.value#>>'{}') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$' THEN RETURN false; END IF;
    END IF;
  END LOOP;
  RETURN true;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN RETURN false;
END
$fn$;

CREATE TABLE public.identity_org_operation_outbox (
  actor_id text NOT NULL CHECK(actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
  operation text NOT NULL CHECK(operation IN ('organization.join.create','organization.join.decide','organization.member.role','organization.member.state','organization.invitation.create','organization.invitation.revoke')),
  idempotency_key text NOT NULL CHECK(char_length(idempotency_key) BETWEEN 16 AND 128),
  tenant_id text NOT NULL CHECK(tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
  target_id text NOT NULL CHECK(target_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
  request_fingerprint text NOT NULL CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),
  response_json text NOT NULL, event_id text NOT NULL UNIQUE CHECK(event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
  audit_json text NOT NULL, trace_id text NOT NULL, policy_version text NOT NULL,
  created_at timestamptz NOT NULL, delivered_at timestamptz,
  PRIMARY KEY(actor_id,operation,idempotency_key),
  CONSTRAINT identity_org_operation_payload CHECK(public.valid_identity_org_operation(operation,response_json,audit_json)),
  CONSTRAINT identity_org_operation_scope CHECK((response_json::jsonb->>'tenant_id')=tenant_id
    AND CASE WHEN operation IN ('organization.join.create','organization.join.decide') THEN response_json::jsonb->>'request_id'
      WHEN operation IN ('organization.invitation.create','organization.invitation.revoke') THEN response_json::jsonb->>'invitation_id'
      ELSE response_json::jsonb->>'user_id' END=target_id)
);
CREATE INDEX identity_org_operation_pending ON public.identity_org_operation_outbox(created_at,event_id) WHERE delivered_at IS NULL;
CREATE FUNCTION public.guard_identity_org_operation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $fn$
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.delivered_at IS NOT NULL
      OR NEW.actor_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
      OR NEW.tenant_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
      OR NEW.target_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
      OR char_length(NEW.idempotency_key) NOT BETWEEN 16 AND 128
      OR NEW.request_fingerprint !~ '^[0-9a-f]{64}$'
      OR NOT public.valid_identity_org_operation(NEW.operation,NEW.response_json,NEW.audit_json)
      OR (NEW.response_json::jsonb->>'tenant_id') IS DISTINCT FROM NEW.tenant_id
      OR (CASE WHEN NEW.operation IN ('organization.join.create','organization.join.decide') THEN NEW.response_json::jsonb->>'request_id'
        WHEN NEW.operation IN ('organization.invitation.create','organization.invitation.revoke') THEN NEW.response_json::jsonb->>'invitation_id'
        ELSE NEW.response_json::jsonb->>'user_id' END) IS DISTINCT FROM NEW.target_id THEN
      RAISE EXCEPTION 'ORGANIZATION_OPERATION_INVALID' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ORGANIZATION_OPERATION_DELETE_BLOCKED' USING ERRCODE='55000'; END IF;
  IF (to_jsonb(NEW)-'delivered_at') IS DISTINCT FROM (to_jsonb(OLD)-'delivered_at')
    OR current_user<>'daon_organization_owner' OR OLD.delivered_at IS NOT NULL OR NEW.delivered_at IS NULL THEN
    RAISE EXCEPTION 'ORGANIZATION_OPERATION_IMMUTABLE' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END
$fn$;
CREATE TRIGGER identity_org_operation_guard BEFORE INSERT OR UPDATE OR DELETE ON public.identity_org_operation_outbox
  FOR EACH ROW EXECUTE FUNCTION public.guard_identity_org_operation();

ALTER TABLE public.identity_org_operation_outbox OWNER TO daon_organization_owner;
ALTER TABLE public.identity_org_operation_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.identity_org_operation_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY organization_operation_scope ON public.identity_org_operation_outbox TO daon_app
  USING(actor_id=current_setting('daon.organization_actor',true) AND tenant_id=current_setting('daon.organization_tenant',true))
  WITH CHECK(actor_id=current_setting('daon.organization_actor',true) AND tenant_id=current_setting('daon.organization_tenant',true));
CREATE POLICY organization_operation_dispatch ON public.identity_org_operation_outbox TO daon_organization_owner USING(true) WITH CHECK(true);
REVOKE ALL ON public.identity_org_operation_outbox FROM PUBLIC,daon_app;
GRANT SELECT,INSERT ON public.identity_org_operation_outbox TO daon_app;

CREATE FUNCTION public.pending_identity_org_audits(requested_limit integer)
RETURNS TABLE(event_id text,actor_id text,tenant_id text,operation text,target_id text,audit_json text,trace_id text,policy_version text,created_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
  SELECT r.event_id,r.actor_id,r.tenant_id,r.operation,r.target_id,r.audit_json,r.trace_id,r.policy_version,r.created_at
    FROM public.identity_org_operation_outbox r WHERE r.delivered_at IS NULL
    ORDER BY r.created_at,r.event_id LIMIT least(greatest(coalesce(requested_limit,0),0),100)
$fn$;
CREATE FUNCTION public.ack_identity_org_audit(requested_event text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
DECLARE affected integer;
BEGIN
  UPDATE public.identity_org_operation_outbox SET delivered_at=clock_timestamp() WHERE event_id=requested_event AND delivered_at IS NULL;
  GET DIAGNOSTICS affected=ROW_COUNT;
  RETURN affected=1;
END
$fn$;
ALTER FUNCTION public.guard_identity_org_workspace() OWNER TO daon_organization_owner;
ALTER FUNCTION public.valid_identity_org_operation(text,text,text) OWNER TO daon_organization_owner;
ALTER FUNCTION public.guard_identity_org_operation() OWNER TO daon_organization_owner;
ALTER FUNCTION public.pending_identity_org_audits(integer) OWNER TO daon_organization_owner;
ALTER FUNCTION public.ack_identity_org_audit(text) OWNER TO daon_organization_owner;
REVOKE ALL ON FUNCTION public.guard_identity_org_workspace(),public.valid_identity_org_operation(text,text,text),
  public.guard_identity_org_operation(),public.pending_identity_org_audits(integer),public.ack_identity_org_audit(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.valid_identity_org_operation(text,text,text),public.pending_identity_org_audits(integer),public.ack_identity_org_audit(text) TO daon_app;
