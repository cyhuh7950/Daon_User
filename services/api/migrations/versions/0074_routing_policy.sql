-- B1 draft only. No SQL is executed by the service or tests.
-- Existing immutable routing_policy_versions and its scoped FK/trigger stay unchanged.
CREATE TABLE public.workspace_routing_policy_current (
  tenant_id text NOT NULL,
  workspace_id text NOT NULL,
  policy_version_id text NOT NULL,
  PRIMARY KEY(tenant_id,workspace_id),
  FOREIGN KEY(tenant_id,workspace_id) REFERENCES public.workspaces(tenant_id,workspace_id),
  FOREIGN KEY(tenant_id,workspace_id,policy_version_id)
    REFERENCES public.routing_policy_versions(tenant_id,workspace_id,record_id)
);
REVOKE ALL ON public.workspace_routing_policy_current FROM PUBLIC,daon_app;
GRANT SELECT,INSERT,UPDATE ON public.workspace_routing_policy_current TO daon_app;
ALTER TABLE public.workspace_routing_policy_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workspace_routing_policy_current FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_routing_policy_scope ON public.workspace_routing_policy_current TO daon_app
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),''))
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
    AND current_setting('app.capability',true)='routing_policy.put');
CREATE FUNCTION public.guard_workspace_routing_policy_pointer() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE next_version integer; prior_version integer; previous_id text;
BEGIN
  IF TG_OP NOT IN ('INSERT','UPDATE') THEN
    RAISE EXCEPTION 'ROUTING_POLICY_PROTECTED' USING ERRCODE='55000';
  END IF;
  IF TG_OP='UPDATE' AND (NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
    OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) THEN
    RAISE EXCEPTION 'ROUTING_POLICY_SCOPE_INVALID' USING ERRCODE='23514';
  END IF;
  SELECT version,previous_version_id INTO next_version,previous_id
    FROM public.routing_policy_versions
    WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND record_id=NEW.policy_version_id;
  IF TG_OP='INSERT' THEN
    IF next_version IS DISTINCT FROM 1 OR previous_id IS NOT NULL THEN
      RAISE EXCEPTION 'ROUTING_POLICY_VERSION_INVALID' USING ERRCODE='23514'; END IF;
  ELSE
    SELECT version INTO prior_version FROM public.routing_policy_versions
      WHERE tenant_id=OLD.tenant_id AND workspace_id=OLD.workspace_id AND record_id=OLD.policy_version_id;
    IF next_version IS DISTINCT FROM prior_version+1 OR previous_id IS DISTINCT FROM OLD.policy_version_id THEN
      RAISE EXCEPTION 'ROUTING_POLICY_VERSION_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_workspace_routing_policy_pointer() FROM PUBLIC,daon_app;
CREATE TRIGGER workspace_routing_policy_pointer_guard
  BEFORE INSERT OR UPDATE OR DELETE ON public.workspace_routing_policy_current
  FOR EACH ROW EXECUTE FUNCTION public.guard_workspace_routing_policy_pointer();
CREATE TRIGGER workspace_routing_policy_no_truncate
  BEFORE TRUNCATE ON public.workspace_routing_policy_current
  FOR EACH STATEMENT EXECUTE FUNCTION public.guard_workspace_routing_policy_pointer();
