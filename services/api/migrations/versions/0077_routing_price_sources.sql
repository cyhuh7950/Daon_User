-- Review draft only. Applying this migration requires separate approval.
CREATE TABLE public.routing_price_versions (
 tenant_id text NOT NULL,anchor_workspace_id text NOT NULL,series_id text NOT NULL,price_version integer NOT NULL,
 created_by text NOT NULL,issued_at timestamptz NOT NULL,canonical_text text NOT NULL,payload jsonb NOT NULL,content_digest text NOT NULL,
 PRIMARY KEY(tenant_id,anchor_workspace_id,series_id,price_version),
 CONSTRAINT routing_price_version_workspace_fk FOREIGN KEY(tenant_id,anchor_workspace_id) REFERENCES public.workspaces(tenant_id,workspace_id)
);
CREATE TABLE public.routing_price_current (
 tenant_id text NOT NULL,anchor_workspace_id text NOT NULL,series_id text NOT NULL,current_revision integer NOT NULL,price_version integer NOT NULL,revoked boolean NOT NULL,
 changed_by text NOT NULL,changed_at timestamptz NOT NULL,operation text NOT NULL,
 PRIMARY KEY(tenant_id,anchor_workspace_id,series_id),
 CONSTRAINT routing_price_current_version_fk FOREIGN KEY(tenant_id,anchor_workspace_id,series_id,price_version) REFERENCES public.routing_price_versions(tenant_id,anchor_workspace_id,series_id,price_version)
);
CREATE TABLE public.routing_price_revocations (
 tenant_id text NOT NULL,anchor_workspace_id text NOT NULL,series_id text NOT NULL,price_version integer NOT NULL,
 created_by text NOT NULL,revoked_at timestamptz NOT NULL,canonical_text text NOT NULL,payload jsonb NOT NULL,content_digest text NOT NULL,
 PRIMARY KEY(tenant_id,anchor_workspace_id,series_id,price_version),
 CONSTRAINT routing_price_revocation_version_fk FOREIGN KEY(tenant_id,anchor_workspace_id,series_id,price_version) REFERENCES public.routing_price_versions(tenant_id,anchor_workspace_id,series_id,price_version)
);
CREATE FUNCTION public.valid_routing_price_version(public.routing_price_versions) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
SELECT (
 ($1).tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' AND ($1).anchor_workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
 AND ($1).created_by ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' AND ($1).price_version>=1
 AND octet_length(($1).canonical_text)<=131072 AND ($1).canonical_text::jsonb=($1).payload
 AND encode(sha256(convert_to(($1).canonical_text,'UTF8')),'hex')=($1).content_digest
 AND ($1).payload=jsonb_build_object('schema_version',1,'tenant_id',($1).tenant_id,'anchor_workspace_id',($1).anchor_workspace_id,'series_id',($1).series_id,'price_version',($1).price_version,'verification_state','unverified','created_by',($1).created_by,'issued_at',($1).payload->>'issued_at','body',($1).payload->'body','target',($1).payload->'target')
 AND ($1).payload->>'verification_state'='unverified'
 AND jsonb_typeof(($1).payload->'issued_at')='string' AND ($1).payload->>'issued_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$' AND (($1).payload->>'issued_at')::timestamptz=($1).issued_at
 AND ($1).payload->'body'=jsonb_build_object('schema_version',1,'expected_version',($1).payload->'body'->'expected_version','workspace_id',($1).anchor_workspace_id,'connection_id',($1).payload->'body'->>'connection_id','model_id',($1).payload->'body'->>'model_id','connection_version',($1).payload->'body'->'connection_version','catalog_version',($1).payload->'body'->'catalog_version','endpoint_version',($1).payload->'body'->'endpoint_version','credential_source','workspace','currency',($1).payload->'body'->>'currency','valid_from',($1).payload->'body'->>'valid_from','valid_until',($1).payload->'body'->>'valid_until','billing_basis',($1).payload->'body'->>'billing_basis','unit_prices',($1).payload->'body'->'unit_prices','evidence',($1).payload->'body'->'evidence')
 AND ($1).payload->'body'->>'connection_id' ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
 AND length(($1).payload->'body'->>'model_id') BETWEEN 1 AND 255 AND ($1).payload->'body'->>'model_id' !~ '[\x01-\x20\x7f]'
 AND jsonb_typeof(($1).payload->'body'->'expected_version')='number' AND ($1).payload->'body'->>'expected_version' ~ '^(0|[1-9][0-9]*)$'
 AND jsonb_typeof(($1).payload->'body'->'connection_version')='number' AND ($1).payload->'body'->>'connection_version' ~ '^[1-9][0-9]*$'
 AND jsonb_typeof(($1).payload->'body'->'catalog_version')='number' AND ($1).payload->'body'->>'catalog_version' ~ '^[1-9][0-9]*$'
 AND ($1).payload->'body'->'endpoint_version'=($1).payload->'body'->'connection_version'
 AND ($1).payload->'body'->>'currency' ~ '^[A-Z]{3}$'
 AND ($1).payload->'body'->>'valid_from' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$' AND ($1).payload->'body'->>'valid_until' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
 AND (($1).payload->'body'->>'valid_from')::timestamptz<(($1).payload->'body'->>'valid_until')::timestamptz
 AND (($1).payload->'body'->>'valid_until')::timestamptz-(($1).payload->'body'->>'valid_from')::timestamptz<=interval '24 hours'
 AND ($1).payload->'body'->>'billing_basis' IN('text_tokens_v1','verified_zero_v1')
 AND ($1).payload->'body'->'unit_prices'=jsonb_build_object('maximum_input_per_million',($1).payload->'body'->'unit_prices'->>'maximum_input_per_million','maximum_output_per_million',($1).payload->'body'->'unit_prices'->>'maximum_output_per_million','mandatory_fee_per_request',($1).payload->'body'->'unit_prices'->>'mandatory_fee_per_request')
 AND ($1).payload->'body'->'unit_prices'->>'maximum_input_per_million' ~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,6})?$'
 AND ($1).payload->'body'->'unit_prices'->>'maximum_output_per_million' ~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,6})?$'
 AND ($1).payload->'body'->'unit_prices'->>'mandatory_fee_per_request' ~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,6})?$'
 AND ($1).payload->'body'->'evidence'=jsonb_build_object('source_kind',($1).payload->'body'->'evidence'->>'source_kind','reference',($1).payload->'body'->'evidence'->>'reference','document_version',($1).payload->'body'->'evidence'->>'document_version','document_digest',($1).payload->'body'->'evidence'->>'document_digest','reviewed_at',($1).payload->'body'->'evidence'->>'reviewed_at')
 AND ($1).payload->'body'->'evidence'->>'source_kind' IN('official_price_document','account_contract','internal_zero_policy')
 AND ($1).payload->'body'->'evidence'->>'document_version' ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
 AND ($1).payload->'body'->'evidence'->>'document_digest' ~ '^[0-9a-f]{64}$'
 AND ($1).payload->'body'->'evidence'->>'reviewed_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$' AND (($1).payload->'body'->'evidence'->>'reviewed_at')::timestamptz<=($1).issued_at
 AND length(($1).payload->'body'->'evidence'->>'reference') BETWEEN 1 AND 2048
 AND (($1).payload->'body'->'evidence'->>'reference' ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' OR (($1).payload->'body'->'evidence'->>'reference' ~* '^https://[^/?#@[:space:]\\]+(/[^?#]*)?$' AND ($1).payload->'body'->'evidence'->>'reference' !~ '[\x01-\x20\x7f]' AND ($1).payload->'body'->'evidence'->>'reference' !~* '%(0[0-9a-f]|1[0-9a-f]|7f)'))
 AND (($1).payload->'body'->>'billing_basis'<>'verified_zero_v1' OR (($1).payload->'body'->'evidence'->>'source_kind'='internal_zero_policy' AND (($1).payload->'body'->'unit_prices'->>'maximum_input_per_million')::numeric=0 AND (($1).payload->'body'->'unit_prices'->>'maximum_output_per_million')::numeric=0 AND (($1).payload->'body'->'unit_prices'->>'mandatory_fee_per_request')::numeric=0))
 AND ($1).series_id='price-series-'||substr(encode(sha256(convert_to(replace(jsonb_build_array('routing-price-series-v1',($1).tenant_id,($1).anchor_workspace_id,($1).payload->'body'->>'connection_id',($1).payload->'body'->>'model_id','workspace')::text,' ',''),'UTF8')),'hex'),1,40)
 AND ($1).payload->'target'=jsonb_build_object('connection_version',($1).payload->'body'->'connection_version','connection_id',($1).payload->'body'->>'connection_id','model_id',($1).payload->'body'->>'model_id','provider_code',($1).payload->'target'->>'provider_code','catalog_version',($1).payload->'body'->'catalog_version','credential_version',($1).payload->'target'->'credential_version','enabled',true,'verification_status','verified','access_mode','public','credential_requirement',($1).payload->'target'->>'credential_requirement','has_credential',($1).payload->'target'->'has_credential','adapter_type',($1).payload->'target'->>'adapter_type','endpoint_digest',($1).payload->'target'->>'endpoint_digest','catalog_status','ready','capabilities',($1).payload->'target'->'capabilities','allowed',true)
 AND ($1).payload->'target'->>'provider_code' ~ '^[A-Z][A-Z0-9_]{0,63}$' AND ($1).payload->'target'->>'endpoint_digest' ~ '^[0-9a-f]{64}$'
 AND jsonb_typeof(($1).payload->'target'->'credential_version')='number' AND ($1).payload->'target'->>'credential_version' ~ '^(0|[1-9][0-9]*)$'
 AND ($1).payload->'target'->>'credential_requirement' IN('required','none') AND jsonb_typeof(($1).payload->'target'->'has_credential')='boolean'
 AND (($1).payload->'target'->>'credential_requirement'<>'required' OR ($1).payload->'target'->'has_credential'='true'::jsonb)
 AND jsonb_typeof(($1).payload->'target'->'capabilities')='array' AND ($1).payload->'target'->'capabilities' ? 'text_generation'
 AND jsonb_typeof(($1).payload->'target'->'adapter_type')='string' AND length(($1).payload->'target'->>'adapter_type')<=128
) IS TRUE
$$;
CREATE FUNCTION public.valid_routing_price_current(public.routing_price_current) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
SELECT (($1).tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' AND ($1).anchor_workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
 AND ($1).series_id ~ '^price-series-[0-9a-f]{40}$' AND ($1).changed_by ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
 AND ($1).current_revision>=1 AND ($1).price_version>=1 AND ($1).operation IN('routing_price.register','routing_price.revoke')) IS TRUE
$$;
CREATE FUNCTION public.valid_routing_price_revocation(public.routing_price_revocations) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
SELECT (($1).price_version>=1 AND ($1).created_by ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
 AND octet_length(($1).canonical_text)<=4096 AND ($1).canonical_text::jsonb=($1).payload
 AND encode(sha256(convert_to(($1).canonical_text,'UTF8')),'hex')=($1).content_digest
 AND ($1).payload=jsonb_build_object('tenant_id',($1).tenant_id,'anchor_workspace_id',($1).anchor_workspace_id,'series_id',($1).series_id,'price_version',($1).price_version,'created_by',($1).created_by,'revoked_at',($1).payload->>'revoked_at','reason_code',($1).payload->>'reason_code')
 AND ($1).payload->>'reason_code' IN('ADMIN_REVOKED','POLICY_WITHDRAWN') AND ($1).payload->>'revoked_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$' AND (($1).payload->>'revoked_at')::timestamptz=($1).revoked_at) IS TRUE
$$;
CREATE FUNCTION public.guard_routing_price_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'ROUTING_PRICE_IMMUTABLE' USING ERRCODE='55000';
END $$;
CREATE FUNCTION public.guard_routing_price_write() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE item jsonb:=to_jsonb(NEW);expected_operation text;current_row public.routing_price_current;maximum_version integer;snapshot jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('routing-price|'||NEW.tenant_id||'|'||NEW.series_id,0));
 expected_operation:=CASE WHEN TG_TABLE_NAME='routing_price_versions' THEN 'routing_price.register' WHEN TG_TABLE_NAME='routing_price_revocations' THEN 'routing_price.revoke' ELSE item->>'operation' END;
 IF NEW.tenant_id IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'') OR NEW.anchor_workspace_id IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'')
 OR current_setting('app.capability',true) IS DISTINCT FROM expected_operation
 OR coalesce(item->>'created_by',item->>'changed_by') IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') THEN
 RAISE EXCEPTION 'ROUTING_PRICE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM user_id FROM public.identity_users WHERE user_id=current_setting('app.actor_id',true) AND state='active' FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'ROUTING_PRICE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM user_id FROM public.identity_auth_tenant_roles WHERE tenant_id=NEW.tenant_id AND user_id=current_setting('app.actor_id',true) AND state='active' AND role='organization_admin' FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'ROUTING_PRICE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM workspace_id FROM public.identity_auth_workspaces WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.anchor_workspace_id AND workspace_kind='organization' FOR SHARE;
 IF NOT FOUND OR (SELECT count(*) FROM public.identity_auth_workspaces WHERE tenant_id=NEW.tenant_id AND workspace_kind='organization')<>1 THEN RAISE EXCEPTION 'ROUTING_PRICE_ANCHOR_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM user_id FROM public.identity_org_tenant_memberships WHERE tenant_id=NEW.tenant_id AND user_id=current_setting('app.actor_id',true) FOR SHARE;
 IF EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships WHERE tenant_id=NEW.tenant_id AND user_id=current_setting('app.actor_id',true) AND state<>'active') THEN RAISE EXCEPTION 'ROUTING_PRICE_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO current_row FROM public.routing_price_current WHERE tenant_id=NEW.tenant_id AND anchor_workspace_id=NEW.anchor_workspace_id AND series_id=NEW.series_id;
 IF TG_TABLE_NAME='routing_price_versions' THEN
  SELECT coalesce(max(price_version),0) INTO maximum_version FROM public.routing_price_versions WHERE tenant_id=NEW.tenant_id AND anchor_workspace_id=NEW.anchor_workspace_id AND series_id=NEW.series_id;
  IF NEW.price_version<>maximum_version+1 OR (NEW.payload->'body'->>'expected_version')::numeric<>coalesce(current_row.current_revision,0)
  OR (NEW.payload->'body'->>'valid_until')::timestamptz<=NEW.issued_at THEN RAISE EXCEPTION 'ROUTING_PRICE_VERSION_CONFLICT' USING ERRCODE='23514'; END IF;
  SELECT jsonb_build_object('connection_version',c.version,'connection_id',c.connection_id,'model_id',m.model_id,'provider_code',c.provider_code,'catalog_version',m.catalog_version,'credential_version',c.credential_version,'enabled',c.enabled,'verification_status',c.verification_status,'access_mode',c.access_mode,'credential_requirement',c.credential_requirement,'has_credential',(c.encrypted_credential IS NOT NULL),'adapter_type',c.adapter_type,'endpoint_digest',encode(sha256(convert_to(c.base_url,'UTF8')),'hex'),'catalog_status',m.catalog_status,'capabilities',m.effective_capabilities,'allowed',true)
  INTO snapshot FROM public.system_provider_connections c JOIN public.system_provider_models m ON m.connection_id=c.connection_id JOIN public.system_provider_allowed_models a ON a.connection_id=m.connection_id AND a.model_id=m.model_id
  WHERE c.connection_id=NEW.payload->'body'->>'connection_id' AND m.model_id=NEW.payload->'body'->>'model_id' FOR SHARE OF c,m,a;
  IF snapshot IS NULL OR (NEW.payload->'target') IS DISTINCT FROM snapshot THEN RAISE EXCEPTION 'ROUTING_PRICE_TARGET_CHANGED' USING ERRCODE='42501'; END IF;
 ELSIF TG_TABLE_NAME='routing_price_current' THEN
  IF TG_OP='INSERT' THEN
   IF NEW.current_revision<>1 OR NEW.price_version<>1 OR NEW.revoked OR NEW.operation<>'routing_price.register' THEN RAISE EXCEPTION 'ROUTING_PRICE_POINTER_INVALID' USING ERRCODE='23514'; END IF;
  ELSE
   IF (NEW.tenant_id,NEW.anchor_workspace_id,NEW.series_id) IS DISTINCT FROM (OLD.tenant_id,OLD.anchor_workspace_id,OLD.series_id)
   OR NEW.current_revision<>OLD.current_revision+1 OR NEW.changed_at<OLD.changed_at
   OR (NEW.operation='routing_price.register' AND (NEW.price_version<>OLD.price_version+1 OR NEW.revoked))
   OR (NEW.operation='routing_price.revoke' AND (NEW.price_version<>OLD.price_version OR OLD.revoked OR NOT NEW.revoked)) THEN RAISE EXCEPTION 'ROUTING_PRICE_POINTER_INVALID' USING ERRCODE='23514'; END IF;
  END IF;
  IF (NEW.revoked IS DISTINCT FROM EXISTS(SELECT 1 FROM public.routing_price_revocations r WHERE r.tenant_id=NEW.tenant_id AND r.anchor_workspace_id=NEW.anchor_workspace_id AND r.series_id=NEW.series_id AND r.price_version=NEW.price_version)) THEN RAISE EXCEPTION 'ROUTING_PRICE_POINTER_INVALID' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION public.valid_routing_price_version(public.routing_price_versions) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.valid_routing_price_version(public.routing_price_versions) FROM PUBLIC,daon_app;
GRANT EXECUTE ON FUNCTION public.valid_routing_price_version(public.routing_price_versions) TO daon_app;
ALTER TABLE public.routing_price_versions ADD CONSTRAINT routing_price_versions_valid CHECK(public.valid_routing_price_version(routing_price_versions.*) IS TRUE);
ALTER FUNCTION public.valid_routing_price_current(public.routing_price_current) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.valid_routing_price_current(public.routing_price_current) FROM PUBLIC,daon_app;
GRANT EXECUTE ON FUNCTION public.valid_routing_price_current(public.routing_price_current) TO daon_app;
ALTER TABLE public.routing_price_current ADD CONSTRAINT routing_price_current_valid CHECK(public.valid_routing_price_current(routing_price_current.*) IS TRUE);
ALTER FUNCTION public.valid_routing_price_revocation(public.routing_price_revocations) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.valid_routing_price_revocation(public.routing_price_revocations) FROM PUBLIC,daon_app;
GRANT EXECUTE ON FUNCTION public.valid_routing_price_revocation(public.routing_price_revocations) TO daon_app;
ALTER TABLE public.routing_price_revocations ADD CONSTRAINT routing_price_revocations_valid CHECK(public.valid_routing_price_revocation(routing_price_revocations.*) IS TRUE);
ALTER FUNCTION public.guard_routing_price_immutable() OWNER TO postgres;
REVOKE ALL ON FUNCTION public.guard_routing_price_immutable() FROM PUBLIC,daon_app;
ALTER FUNCTION public.guard_routing_price_write() OWNER TO postgres;
REVOKE ALL ON FUNCTION public.guard_routing_price_write() FROM PUBLIC,daon_app;
ALTER TABLE public.routing_price_versions OWNER TO postgres;
REVOKE ALL ON public.routing_price_versions FROM PUBLIC,daon_app;
GRANT SELECT,INSERT ON public.routing_price_versions TO daon_app;
ALTER TABLE public.routing_price_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.routing_price_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY routing_price_versions_scope ON public.routing_price_versions TO daon_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true) IN('routing_price.register','routing_price.revoke','routing_price.read'))
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true)='routing_price.register');
CREATE TRIGGER routing_price_versions_write BEFORE INSERT ON public.routing_price_versions FOR EACH ROW EXECUTE FUNCTION public.guard_routing_price_write();
CREATE TRIGGER routing_price_versions_immutable BEFORE UPDATE OR DELETE ON public.routing_price_versions FOR EACH ROW EXECUTE FUNCTION public.guard_routing_price_immutable();
CREATE TRIGGER routing_price_versions_truncate BEFORE TRUNCATE ON public.routing_price_versions FOR EACH STATEMENT EXECUTE FUNCTION public.guard_routing_price_immutable();
ALTER TABLE public.routing_price_current OWNER TO postgres;
REVOKE ALL ON public.routing_price_current FROM PUBLIC,daon_app;
GRANT SELECT,INSERT,UPDATE ON public.routing_price_current TO daon_app;
ALTER TABLE public.routing_price_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.routing_price_current FORCE ROW LEVEL SECURITY;
CREATE POLICY routing_price_current_scope ON public.routing_price_current TO daon_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true) IN('routing_price.register','routing_price.revoke','routing_price.read'))
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true)=ANY(ARRAY['routing_price.register','routing_price.revoke']));
CREATE TRIGGER routing_price_current_write BEFORE INSERT OR UPDATE ON public.routing_price_current FOR EACH ROW EXECUTE FUNCTION public.guard_routing_price_write();
CREATE TRIGGER routing_price_current_immutable BEFORE DELETE ON public.routing_price_current FOR EACH ROW EXECUTE FUNCTION public.guard_routing_price_immutable();
CREATE TRIGGER routing_price_current_truncate BEFORE TRUNCATE ON public.routing_price_current FOR EACH STATEMENT EXECUTE FUNCTION public.guard_routing_price_immutable();
ALTER TABLE public.routing_price_revocations OWNER TO postgres;
REVOKE ALL ON public.routing_price_revocations FROM PUBLIC,daon_app;
GRANT SELECT,INSERT ON public.routing_price_revocations TO daon_app;
ALTER TABLE public.routing_price_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.routing_price_revocations FORCE ROW LEVEL SECURITY;
CREATE POLICY routing_price_revocations_scope ON public.routing_price_revocations TO daon_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true) IN('routing_price.register','routing_price.revoke','routing_price.read'))
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND anchor_workspace_id=nullif(current_setting('app.workspace_id',true),'') AND current_setting('app.capability',true)='routing_price.revoke');
CREATE TRIGGER routing_price_revocations_write BEFORE INSERT ON public.routing_price_revocations FOR EACH ROW EXECUTE FUNCTION public.guard_routing_price_write();
CREATE TRIGGER routing_price_revocations_immutable BEFORE UPDATE OR DELETE ON public.routing_price_revocations FOR EACH ROW EXECUTE FUNCTION public.guard_routing_price_immutable();
CREATE TRIGGER routing_price_revocations_truncate BEFORE TRUNCATE ON public.routing_price_revocations FOR EACH STATEMENT EXECUTE FUNCTION public.guard_routing_price_immutable();
