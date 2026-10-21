-- A2 review draft only. No migration/DDL execution is performed in this task.
CREATE TABLE public.routing_budget_periods (
 tenant_id text NOT NULL,anchor_workspace_id text NOT NULL,period_id text NOT NULL,record_kind text NOT NULL,
 anchor_record_id text NOT NULL,anchor_kind text NOT NULL DEFAULT 'anchor',source_id text,version integer,currency text,
 period_limit numeric,starts_at timestamptz,expires_at timestamptz,created_by text NOT NULL,issued_at timestamptz NOT NULL,
 canonical_text text NOT NULL,payload jsonb NOT NULL,content_digest text NOT NULL,
 PRIMARY KEY(tenant_id,anchor_workspace_id,period_id),UNIQUE(tenant_id,anchor_workspace_id,period_id,record_kind),UNIQUE(tenant_id,source_id),
 CONSTRAINT routing_budget_period_workspace_fk FOREIGN KEY(tenant_id,anchor_workspace_id) REFERENCES public.workspaces(tenant_id,workspace_id),
 CONSTRAINT routing_budget_period_anchor_fk FOREIGN KEY(tenant_id,anchor_workspace_id,anchor_record_id,anchor_kind) REFERENCES public.routing_budget_periods(tenant_id,anchor_workspace_id,period_id,record_kind)
);
CREATE UNIQUE INDEX routing_budget_one_anchor ON public.routing_budget_periods(tenant_id) WHERE record_kind='anchor';
CREATE TABLE public.routing_budget_allocations (
 tenant_id text NOT NULL,anchor_workspace_id text NOT NULL,period_id text NOT NULL,parent_kind text NOT NULL DEFAULT 'period',
 budget_scope_id text NOT NULL,period_limit numeric NOT NULL,run_limit numeric NOT NULL,
 PRIMARY KEY(tenant_id,anchor_workspace_id,period_id),UNIQUE(tenant_id,budget_scope_id),
 CONSTRAINT routing_budget_allocation_period_fk FOREIGN KEY(tenant_id,anchor_workspace_id,period_id,parent_kind) REFERENCES public.routing_budget_periods(tenant_id,anchor_workspace_id,period_id,record_kind)
);
CREATE TABLE public.routing_budget_revocations (
 tenant_id text NOT NULL,anchor_workspace_id text NOT NULL,period_id text NOT NULL,parent_kind text NOT NULL DEFAULT 'period',version integer NOT NULL DEFAULT 2,
 created_by text NOT NULL,revoked_at timestamptz NOT NULL,reason_code text NOT NULL,canonical_text text NOT NULL,payload jsonb NOT NULL,content_digest text NOT NULL,
 PRIMARY KEY(tenant_id,anchor_workspace_id,period_id),
 CONSTRAINT routing_budget_revocation_period_fk FOREIGN KEY(tenant_id,anchor_workspace_id,period_id,parent_kind) REFERENCES public.routing_budget_periods(tenant_id,anchor_workspace_id,period_id,record_kind)
);
CREATE FUNCTION public.valid_routing_budget_period(public.routing_budget_periods) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
SELECT (
 ($1).tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' AND ($1).anchor_workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
 AND ($1).period_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' AND ($1).created_by ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
 AND ($1).anchor_kind='anchor' AND octet_length(($1).canonical_text)<=65536 AND ($1).canonical_text::jsonb=($1).payload
 AND encode(sha256(convert_to(($1).canonical_text,'UTF8')),'hex')=($1).content_digest
 AND CASE WHEN ($1).record_kind='anchor' THEN
  num_nonnulls(($1).source_id,($1).version,($1).currency,($1).period_limit,($1).starts_at,($1).expires_at)=0
  AND ($1).anchor_record_id=($1).period_id
  AND ($1).period_id='budget-anchor-'||substr(encode(sha256(convert_to(jsonb_build_array(($1).tenant_id)::text,'UTF8')),'hex'),1,40)
  AND ($1).payload=jsonb_build_object('schema_version',1,'record_kind','anchor','tenant_id',($1).tenant_id,'anchor_workspace_id',($1).anchor_workspace_id,'period_id',($1).period_id)
 WHEN ($1).record_kind='period' THEN
  num_nonnulls(($1).source_id,($1).version,($1).currency,($1).period_limit,($1).starts_at,($1).expires_at)=6
  AND ($1).version=1 AND ($1).source_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' AND ($1).currency ~ '^[A-Z]{3}$'
  AND ($1).period_limit>=0 AND ($1).period_limit<1000000000000 AND scale(($1).period_limit)<=6 AND ($1).starts_at<($1).expires_at
  AND ($1).payload=jsonb_build_object('schema_version',1,'tenant_id',($1).tenant_id,'organization_id',($1).tenant_id,'anchor_workspace_id',($1).anchor_workspace_id,'period_id',($1).period_id,'source_id',($1).source_id,'version',1,'created_by',($1).created_by,'issued_at',($1).payload->>'issued_at','budget',($1).payload->'budget')
  AND jsonb_typeof(($1).payload->'issued_at')='string' AND ($1).payload->>'issued_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$' AND (($1).payload->>'issued_at')::timestamptz=($1).issued_at
  AND ($1).payload->'budget'=jsonb_build_object('schema_version',1,'currency',($1).currency,'period_limit',($1).payload->'budget'->>'period_limit','starts_at',($1).payload->'budget'->>'starts_at','expires_at',($1).payload->'budget'->>'expires_at','allocations',jsonb_build_array(($1).payload->'budget'->'allocations'->0))
  AND jsonb_typeof(($1).payload->'budget'->'period_limit')='string' AND ($1).payload->'budget'->>'period_limit' ~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,6})?$'
  AND (($1).payload->'budget'->>'period_limit')::numeric=($1).period_limit
  AND ($1).payload->'budget'->>'starts_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$' AND ($1).payload->'budget'->>'expires_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
  AND (($1).payload->'budget'->>'starts_at')::timestamptz=($1).starts_at AND (($1).payload->'budget'->>'expires_at')::timestamptz=($1).expires_at
  AND ($1).payload->'budget'->'allocations'->0=jsonb_build_object('workspace_id',($1).anchor_workspace_id,'period_limit',($1).payload->'budget'->'allocations'->0->>'period_limit','run_limit',($1).payload->'budget'->'allocations'->0->>'run_limit')
  AND jsonb_typeof(($1).payload->'budget'->'allocations'->0->'period_limit')='string' AND jsonb_typeof(($1).payload->'budget'->'allocations'->0->'run_limit')='string'
  AND ($1).payload->'budget'->'allocations'->0->>'period_limit' ~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,6})?$' AND ($1).payload->'budget'->'allocations'->0->>'run_limit' ~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,6})?$'
  AND 0<=(($1).payload->'budget'->'allocations'->0->>'run_limit')::numeric
  AND (($1).payload->'budget'->'allocations'->0->>'run_limit')::numeric<=(($1).payload->'budget'->'allocations'->0->>'period_limit')::numeric
  AND (($1).payload->'budget'->'allocations'->0->>'period_limit')::numeric<=($1).period_limit
 ELSE false END
) IS TRUE
$$;
ALTER FUNCTION public.valid_routing_budget_period(public.routing_budget_periods) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.valid_routing_budget_period(public.routing_budget_periods) FROM PUBLIC,daon_app;
GRANT EXECUTE ON FUNCTION public.valid_routing_budget_period(public.routing_budget_periods) TO daon_app;
ALTER TABLE public.routing_budget_periods ADD CONSTRAINT routing_budget_periods_valid CHECK(public.valid_routing_budget_period(routing_budget_periods.*) IS TRUE);
CREATE FUNCTION public.valid_routing_budget_allocation(public.routing_budget_allocations) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
SELECT (($1).parent_kind='period' AND ($1).period_limit>=0 AND ($1).run_limit>=0 AND ($1).run_limit<=($1).period_limit
 AND ($1).period_limit<1000000000000 AND scale(($1).period_limit)<=6 AND scale(($1).run_limit)<=6
 AND ($1).budget_scope_id='budget-scope-'||substr(encode(sha256(convert_to(replace(jsonb_build_array(($1).tenant_id,($1).period_id,($1).anchor_workspace_id)::text,' ',''),'UTF8')),'hex'),1,40)) IS TRUE
$$;
ALTER FUNCTION public.valid_routing_budget_allocation(public.routing_budget_allocations) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.valid_routing_budget_allocation(public.routing_budget_allocations) FROM PUBLIC,daon_app;
GRANT EXECUTE ON FUNCTION public.valid_routing_budget_allocation(public.routing_budget_allocations) TO daon_app;
ALTER TABLE public.routing_budget_allocations ADD CONSTRAINT routing_budget_allocations_valid CHECK(public.valid_routing_budget_allocation(routing_budget_allocations.*) IS TRUE);
CREATE FUNCTION public.valid_routing_budget_revocation(public.routing_budget_revocations) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
SELECT (($1).parent_kind='period' AND ($1).version=2 AND ($1).reason_code IN('ADMIN_REVOKED','POLICY_WITHDRAWN')
 AND octet_length(($1).canonical_text)<=4096 AND ($1).canonical_text::jsonb=($1).payload AND encode(sha256(convert_to(($1).canonical_text,'UTF8')),'hex')=($1).content_digest
 AND ($1).payload=jsonb_build_object('tenant_id',($1).tenant_id,'anchor_workspace_id',($1).anchor_workspace_id,'period_id',($1).period_id,'version',2,'actor_id',($1).created_by,'revoked_at',($1).payload->>'revoked_at','reason_code',($1).reason_code)
 AND jsonb_typeof(($1).payload->'revoked_at')='string' AND ($1).payload->>'revoked_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$' AND (($1).payload->>'revoked_at')::timestamptz=($1).revoked_at) IS TRUE
$$;
ALTER FUNCTION public.valid_routing_budget_revocation(public.routing_budget_revocations) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.valid_routing_budget_revocation(public.routing_budget_revocations) FROM PUBLIC,daon_app;
GRANT EXECUTE ON FUNCTION public.valid_routing_budget_revocation(public.routing_budget_revocations) TO daon_app;
ALTER TABLE public.routing_budget_revocations ADD CONSTRAINT routing_budget_revocations_valid CHECK(public.valid_routing_budget_revocation(routing_budget_revocations.*) IS TRUE);
CREATE FUNCTION public.guard_routing_budget_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'ROUTING_BUDGET_IMMUTABLE' USING ERRCODE='55000';
END $$;
CREATE FUNCTION public.guard_routing_budget_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE item jsonb:=to_jsonb(NEW);parent public.routing_budget_periods;workspace_count integer;expected jsonb;budget jsonb;allocation jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('routing-budget|'||NEW.tenant_id,0));
 IF NEW.tenant_id IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
 OR NEW.anchor_workspace_id IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'')
 OR (TG_TABLE_NAME='routing_budget_revocations' AND current_setting('app.capability',true) IS DISTINCT FROM 'routing_budget.revoke')
 OR (TG_TABLE_NAME<>'routing_budget_revocations' AND current_setting('app.capability',true) IS DISTINCT FROM 'routing_budget.issue')
 OR NOT EXISTS(SELECT 1 FROM public.identity_users u JOIN public.identity_auth_tenant_roles r ON r.user_id=u.user_id
   WHERE u.user_id=current_setting('app.actor_id',true) AND u.state='active' AND r.tenant_id=NEW.tenant_id AND r.role='organization_admin' AND r.state='active')
 OR NOT EXISTS(SELECT 1 FROM public.identity_auth_workspaces w WHERE w.tenant_id=NEW.tenant_id AND w.workspace_id=NEW.anchor_workspace_id AND w.workspace_kind='organization')
 OR EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships m WHERE m.tenant_id=NEW.tenant_id AND m.user_id=current_setting('app.actor_id',true) AND m.state<>'active')
 THEN RAISE EXCEPTION 'ROUTING_BUDGET_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT count(*) INTO workspace_count FROM public.identity_auth_workspaces WHERE tenant_id=NEW.tenant_id AND workspace_kind='organization';
 IF workspace_count<>1 OR (TG_TABLE_NAME<>'routing_budget_allocations' AND item->>'created_by' IS DISTINCT FROM current_setting('app.actor_id',true)) THEN
 RAISE EXCEPTION 'ROUTING_BUDGET_ANCHOR_DENIED' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='routing_budget_periods' THEN
  IF NEW.record_kind='period' AND EXISTS(SELECT 1 FROM public.routing_budget_periods p WHERE p.tenant_id=NEW.tenant_id AND p.record_kind='period' AND p.starts_at<NEW.expires_at AND NEW.starts_at<p.expires_at) THEN
   RAISE EXCEPTION 'ROUTING_BUDGET_PERIOD_OVERLAP' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='routing_budget_allocations' THEN
  SELECT * INTO STRICT parent FROM public.routing_budget_periods WHERE tenant_id=NEW.tenant_id AND anchor_workspace_id=NEW.anchor_workspace_id AND period_id=NEW.period_id AND record_kind='period';
  allocation:=parent.payload->'budget'->'allocations'->0;
  IF NEW.period_limit<>(allocation->>'period_limit')::numeric OR NEW.run_limit<>(allocation->>'run_limit')::numeric
  OR NEW.budget_scope_id<>'budget-scope-'||substr(encode(sha256(convert_to(replace(jsonb_build_array(NEW.tenant_id,NEW.period_id,NEW.anchor_workspace_id)::text,' ',''),'UTF8')),'hex'),1,40) THEN
   RAISE EXCEPTION 'ROUTING_BUDGET_ALLOCATION_INVALID' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
ALTER TABLE public.routing_budget_periods OWNER TO postgres;
REVOKE ALL ON public.routing_budget_periods FROM PUBLIC,daon_app;
GRANT SELECT,INSERT ON public.routing_budget_periods TO daon_app;
ALTER TABLE public.routing_budget_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.routing_budget_periods FORCE ROW LEVEL SECURITY;
CREATE POLICY routing_budget_periods_scope ON public.routing_budget_periods TO daon_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true) IN('routing_budget.issue','routing_budget.revoke','routing_budget.read','routing_budget.upper'))
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true)='routing_budget.issue');
CREATE TRIGGER routing_budget_periods_insert BEFORE INSERT ON public.routing_budget_periods FOR EACH ROW EXECUTE FUNCTION public.guard_routing_budget_insert();
CREATE TRIGGER routing_budget_periods_immutable BEFORE UPDATE OR DELETE ON public.routing_budget_periods FOR EACH ROW EXECUTE FUNCTION public.guard_routing_budget_immutable();
CREATE TRIGGER routing_budget_periods_truncate BEFORE TRUNCATE ON public.routing_budget_periods FOR EACH STATEMENT EXECUTE FUNCTION public.guard_routing_budget_immutable();
ALTER TABLE public.routing_budget_allocations OWNER TO postgres;
REVOKE ALL ON public.routing_budget_allocations FROM PUBLIC,daon_app;
GRANT SELECT,INSERT ON public.routing_budget_allocations TO daon_app;
ALTER TABLE public.routing_budget_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.routing_budget_allocations FORCE ROW LEVEL SECURITY;
CREATE POLICY routing_budget_allocations_scope ON public.routing_budget_allocations TO daon_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true) IN('routing_budget.issue','routing_budget.revoke','routing_budget.read','routing_budget.upper'))
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true)='routing_budget.issue');
CREATE TRIGGER routing_budget_allocations_insert BEFORE INSERT ON public.routing_budget_allocations FOR EACH ROW EXECUTE FUNCTION public.guard_routing_budget_insert();
CREATE TRIGGER routing_budget_allocations_immutable BEFORE UPDATE OR DELETE ON public.routing_budget_allocations FOR EACH ROW EXECUTE FUNCTION public.guard_routing_budget_immutable();
CREATE TRIGGER routing_budget_allocations_truncate BEFORE TRUNCATE ON public.routing_budget_allocations FOR EACH STATEMENT EXECUTE FUNCTION public.guard_routing_budget_immutable();
ALTER TABLE public.routing_budget_revocations OWNER TO postgres;
REVOKE ALL ON public.routing_budget_revocations FROM PUBLIC,daon_app;
GRANT SELECT,INSERT ON public.routing_budget_revocations TO daon_app;
ALTER TABLE public.routing_budget_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.routing_budget_revocations FORCE ROW LEVEL SECURITY;
CREATE POLICY routing_budget_revocations_scope ON public.routing_budget_revocations TO daon_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true) IN('routing_budget.issue','routing_budget.revoke','routing_budget.read','routing_budget.upper'))
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true)='routing_budget.revoke');
CREATE TRIGGER routing_budget_revocations_insert BEFORE INSERT ON public.routing_budget_revocations FOR EACH ROW EXECUTE FUNCTION public.guard_routing_budget_insert();
CREATE TRIGGER routing_budget_revocations_immutable BEFORE UPDATE OR DELETE ON public.routing_budget_revocations FOR EACH ROW EXECUTE FUNCTION public.guard_routing_budget_immutable();
CREATE TRIGGER routing_budget_revocations_truncate BEFORE TRUNCATE ON public.routing_budget_revocations FOR EACH STATEMENT EXECUTE FUNCTION public.guard_routing_budget_immutable();
REVOKE ALL ON FUNCTION public.guard_routing_budget_insert() FROM PUBLIC,daon_app;
REVOKE ALL ON FUNCTION public.guard_routing_budget_immutable() FROM PUBLIC,daon_app;
