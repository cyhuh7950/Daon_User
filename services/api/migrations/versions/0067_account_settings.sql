-- Additive only: no existing identity rows, outbox checks, ACLs or policies change.
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_account_owner') THEN
    CREATE ROLE daon_account_owner NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_account_owner'
    AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls
    AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
    OR pg_catalog.pg_has_role('daon_app','daon_account_owner','MEMBER') THEN
    RAISE EXCEPTION 'ACCOUNT_OWNER_PRECONDITION' USING ERRCODE='55000';
  END IF;
END $$;

CREATE TABLE public.identity_account_profiles (
  user_id text PRIMARY KEY REFERENCES public.identity_users(user_id) ON DELETE NO ACTION,
  display_name text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 80
    AND display_name=btrim(display_name) AND display_name !~ U&'[\0001-\001F\007F-\009F]'),
  version bigint NOT NULL CHECK (version BETWEEN 1 AND 9007199254740991),
  updated_at timestamptz NOT NULL
);
-- An authorized administrator explicitly deletes the mutable profile in its
-- existing user-deletion transaction. No implicit cascade or audit-history FK.
CREATE FUNCTION public.valid_identity_account_events(payload text,operation text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
DECLARE events jsonb:=payload::jsonb; event jsonb;
BEGIN
  IF jsonb_typeof(events)<>'array' THEN RETURN false; END IF;
  IF operation='profile.update' THEN RETURN events='[]'::jsonb; END IF;
  IF jsonb_array_length(events)=0 THEN RETURN false; END IF;
  FOR event IN SELECT value FROM jsonb_array_elements(events) LOOP
    IF jsonb_typeof(event)<>'object' OR NOT event ?& ARRAY['action','target_type','target_id','metadata']
      OR (event-ARRAY['action','target_type','target_id','metadata'])<>'{}'::jsonb
      OR jsonb_typeof(event->'action')<>'string' OR jsonb_typeof(event->'target_type')<>'string'
      OR jsonb_typeof(event->'metadata')<>'object'
      OR jsonb_typeof(event->'target_id')<>'string' OR length(event->>'target_id') NOT BETWEEN 1 AND 512 THEN RETURN false; END IF;
    IF NOT coalesce((
      (event->>'action'='identity.step_up.used' AND event->>'target_type'='step_up'
        AND event->'metadata'='{"action_group":"device_session_or_sync_key_revoke"}'::jsonb)
      OR (event->>'action'='identity.session.revoked' AND event->>'target_type'='session'
        AND event->'metadata' IN ('{"reason_code":"USER_REQUESTED"}'::jsonb,'{"reason_code":"DEVICE_REVOKED"}'::jsonb))
      OR (event->>'action'='identity.device.trusted' AND event->>'target_type'='device' AND event->'metadata'='{}'::jsonb)
      OR (event->>'action'='identity.device.revoked' AND event->>'target_type'='device'
        AND event->'metadata'='{"sync_key_revoke_required": true}'::jsonb)
    ),false) THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
END $$;
CREATE TABLE public.identity_account_requests (
  actor_id text NOT NULL CHECK (char_length(actor_id)>0),
  operation text NOT NULL CHECK(operation IN ('profile.update','device.trust','device.revoke','session.revoke')),
  idempotency_key text NOT NULL CHECK(char_length(idempotency_key) BETWEEN 1 AND 512),
  fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
  response_json text NOT NULL CHECK(jsonb_typeof(response_json::jsonb)='object'),
  tenant_id text NOT NULL, target_id text NOT NULL, event_id text NOT NULL UNIQUE,
  trace_id text NOT NULL, policy_version text NOT NULL,
  created_at timestamptz NOT NULL, delivered_at timestamptz,
  audit_events_json text NOT NULL DEFAULT '[]' CHECK(public.valid_identity_account_events(audit_events_json,operation)),
  PRIMARY KEY(actor_id,operation,idempotency_key)
);
CREATE INDEX identity_account_requests_pending ON public.identity_account_requests(created_at,event_id)
  WHERE delivered_at IS NULL;

CREATE FUNCTION public.guard_identity_account_profile() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
  IF TG_OP='INSERT' AND NEW.version<>1 OR TG_OP='UPDATE'
    AND (NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.version<>OLD.version+1) THEN
    RAISE EXCEPTION 'ACCOUNT_PROFILE_VERSION_INVALID' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER identity_account_profile_guard BEFORE INSERT OR UPDATE ON public.identity_account_profiles
  FOR EACH ROW EXECUTE FUNCTION public.guard_identity_account_profile();

CREATE FUNCTION public.guard_identity_account_request() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ACCOUNT_REQUEST_DELETE_BLOCKED' USING ERRCODE='55000'; END IF;
  IF (to_jsonb(NEW)-'delivered_at') IS DISTINCT FROM (to_jsonb(OLD)-'delivered_at') THEN
    RAISE EXCEPTION 'ACCOUNT_REQUEST_IMMUTABLE' USING ERRCODE='55000';
  END IF;
  IF OLD.delivered_at IS NOT NULL OR NEW.delivered_at IS NULL THEN
    RAISE EXCEPTION 'ACCOUNT_DELIVERY_ONE_WAY' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER identity_account_request_guard BEFORE UPDATE OR DELETE ON public.identity_account_requests
  FOR EACH ROW EXECUTE FUNCTION public.guard_identity_account_request();

ALTER TABLE public.identity_account_profiles OWNER TO daon_account_owner;
ALTER TABLE public.identity_account_requests OWNER TO daon_account_owner;
ALTER TABLE public.identity_account_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.identity_account_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE public.identity_account_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.identity_account_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY account_profile_actor ON public.identity_account_profiles TO daon_app
  USING (user_id=current_setting('daon.account_actor',true))
  WITH CHECK (user_id=current_setting('daon.account_actor',true));
CREATE POLICY account_request_actor ON public.identity_account_requests TO daon_app
  USING (actor_id=current_setting('daon.account_actor',true))
  WITH CHECK (actor_id=current_setting('daon.account_actor',true));
CREATE POLICY account_audit_dispatch ON public.identity_account_requests FOR SELECT TO daon_account_owner USING (true);
REVOKE ALL ON public.identity_account_profiles,public.identity_account_requests FROM PUBLIC,daon_app;
GRANT SELECT,INSERT,DELETE ON public.identity_account_profiles TO daon_app;
GRANT UPDATE(display_name,version,updated_at) ON public.identity_account_profiles TO daon_app;
GRANT SELECT,INSERT ON public.identity_account_requests TO daon_app;
GRANT UPDATE(delivered_at) ON public.identity_account_requests TO daon_app;

CREATE FUNCTION public.pending_identity_account_audits(requested_actor text)
RETURNS TABLE(event_id text,actor_id text,tenant_id text,operation text,target_id text,
  trace_id text,policy_version text,created_at timestamptz,audit_events_json text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
  SELECT r.event_id,r.actor_id,r.tenant_id,r.operation,r.target_id,r.trace_id,r.policy_version,r.created_at,r.audit_events_json
    FROM public.identity_account_requests r
    WHERE r.delivered_at IS NULL AND (requested_actor IS NULL OR r.actor_id=requested_actor)
    ORDER BY r.created_at,r.event_id LIMIT 32
$$;
ALTER FUNCTION public.pending_identity_account_audits(text) OWNER TO daon_account_owner;
ALTER FUNCTION public.guard_identity_account_request() OWNER TO daon_account_owner;
ALTER FUNCTION public.guard_identity_account_profile() OWNER TO daon_account_owner;
ALTER FUNCTION public.valid_identity_account_events(text,text) OWNER TO daon_account_owner;
REVOKE ALL ON FUNCTION public.pending_identity_account_audits(text),
  public.guard_identity_account_request(),public.guard_identity_account_profile(),public.valid_identity_account_events(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_identity_account_audits(text),public.valid_identity_account_events(text,text) TO daon_app;
