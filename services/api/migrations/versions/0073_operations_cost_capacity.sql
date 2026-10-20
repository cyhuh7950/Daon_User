-- S44 지정 Run 비용과 WSL 한 호스트 CPU/RAM. 기존 원문/역할/보호 함수는 변경하지 않는다.
DO $precondition$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT count(*) FROM public.alembic_version)<>1
    OR (SELECT version_num FROM public.alembic_version)<>'0072'
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
      AND rolinherit AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
    OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.runs','SELECT')
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.processing_runs','SELECT') THEN
    RAISE EXCEPTION 'OPERATIONS_COST_CAPACITY_PRECONDITION' USING ERRCODE='55000';
  END IF;
END $precondition$;

CREATE TABLE public.operations_run_cost_events (
  tenant_id text NOT NULL, workspace_id text NOT NULL, actor_id text NOT NULL,
  run_kind text NOT NULL CHECK(run_kind IN ('question','document')),
  run_id text NOT NULL CHECK(run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  sequence bigint NOT NULL CHECK(sequence>0),
  attempt_key text NOT NULL CHECK(attempt_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  state text NOT NULL CHECK(state IN ('reserved','known','uncertain')),
  amount numeric(24,9), currency text,
  policy jsonb NOT NULL CHECK(jsonb_typeof(policy)='object' AND octet_length(policy::text)<=16384),
  input_token_upper_bound bigint NOT NULL CHECK(input_token_upper_bound BETWEEN 0 AND 2147483647),
  output_token_limit integer NOT NULL CHECK(output_token_limit BETWEEN 1 AND 512),
  price_version text NOT NULL CHECK(price_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  usage_digest text CHECK(usage_digest ~ '^[0-9a-f]{64}$'),
  receipt_id text CHECK(receipt_id ~ '^cost-[0-9a-f]{64}$'),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(tenant_id,workspace_id,run_kind,run_id,sequence),
  UNIQUE(tenant_id,workspace_id,run_kind,run_id,attempt_key,state),
  UNIQUE(tenant_id,workspace_id,run_kind,run_id,receipt_id),
  CHECK((state='uncertain' AND amount IS NULL AND currency IS NULL)
    OR (state IN ('reserved','known') AND amount>=0 AND amount IS NOT NULL AND currency='USD')),
  CHECK((state='reserved' AND receipt_id IS NULL AND usage_digest IS NULL)
    OR (state='known' AND receipt_id IS NOT NULL AND usage_digest IS NOT NULL)
    OR (state='uncertain' AND receipt_id IS NOT NULL))
);
ALTER TABLE public.operations_run_cost_events OWNER TO daon_notebook_delete;
REVOKE ALL ON public.operations_run_cost_events FROM PUBLIC,daon_app;
ALTER TABLE public.operations_run_cost_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operations_run_cost_events FORCE ROW LEVEL SECURITY;
CREATE POLICY operations_run_cost_scope ON public.operations_run_cost_events
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
    AND actor_id=nullif(current_setting('app.actor_id',true),''))
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
    AND actor_id=nullif(current_setting('app.actor_id',true),''));

CREATE FUNCTION public.operations_run_cost_parent_allowed(t text,w text,a text,k text,r text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
  SELECT coalesce(current_setting('app.capability',true)='run.cost'
    AND public.registered_restore_actor_allowed(t,w,a)
    AND CASE k WHEN 'question' THEN EXISTS(SELECT 1 FROM public.runs x
      WHERE x.tenant_id=t AND x.workspace_id=w AND x.record_id=r AND x.created_by=a)
    WHEN 'document' THEN EXISTS(SELECT 1 FROM public.processing_runs x
      WHERE x.tenant_id=t AND x.workspace_id=w AND x.record_id=r AND x.created_by=a)
    ELSE false END,false)
$$;
ALTER FUNCTION public.operations_run_cost_parent_allowed(text,text,text,text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.operations_run_cost_parent_allowed(text,text,text,text,text) FROM PUBLIC,daon_app;

CREATE FUNCTION public.guard_operations_run_cost_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'RUN_COST_IMMUTABLE' USING ERRCODE='55000'; END IF;
  IF NOT public.operations_run_cost_parent_allowed(NEW.tenant_id,NEW.workspace_id,NEW.actor_id,NEW.run_kind,NEW.run_id)
    OR NEW.observed_at>clock_timestamp()+interval '5 seconds' THEN
    RAISE EXCEPTION 'RUN_COST_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  NEW.received_at=clock_timestamp();RETURN NEW;
END $$;
ALTER FUNCTION public.guard_operations_run_cost_event() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_operations_run_cost_event() FROM PUBLIC,daon_app;
CREATE TRIGGER operations_run_cost_events_guard BEFORE INSERT OR UPDATE OR DELETE ON public.operations_run_cost_events
  FOR EACH ROW EXECUTE FUNCTION public.guard_operations_run_cost_event();
CREATE TRIGGER operations_run_cost_events_no_truncate BEFORE TRUNCATE ON public.operations_run_cost_events
  FOR EACH STATEMENT EXECUTE FUNCTION public.guard_operations_run_cost_event();

CREATE FUNCTION public.reserve_operations_run_cost(t text,w text,a text,k text,r text,i text,p jsonb,ib bigint,ob integer)
RETURNS TABLE(state text,safe_code text,sequence bigint,maximum_amount numeric,currency text,price_version text,observed_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE card jsonb; stamp timestamptz; maximum numeric; total numeric; latest bigint; frozen jsonb;
BEGIN
  IF NOT public.operations_run_cost_parent_allowed(t,w,a,k,r) THEN
    RAISE EXCEPTION 'RUN_COST_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  IF i IS NULL OR i !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
    RAISE EXCEPTION 'RUN_COST_INPUT_INVALID' USING ERRCODE='23514'; END IF;
  -- 가격 판단과 현재 인가를 잠금 대기 이후 실제 예약 시점에 다시 확인한다.
  PERFORM pg_advisory_xact_lock(hashtextextended('operations-run-cost-v1|'||t||'|'||w||'|'||k||'|'||r,0));
  IF NOT public.operations_run_cost_parent_allowed(t,w,a,k,r) THEN
    RAISE EXCEPTION 'RUN_COST_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  stamp=clock_timestamp();card=p->'price_card';
  IF p IS NULL OR jsonb_typeof(p) IS DISTINCT FROM 'object' OR octet_length(p::text)>16384
    OR (SELECT array_agg(x ORDER BY x) FROM jsonb_object_keys(p) x) IS DISTINCT FROM
      ARRAY['actor_id','connection_id','currency','limit_minor_units','model_id','policy_id','price_card','run_kind','schema_version','tenant_id','version','workspace_id']
    OR jsonb_typeof(card) IS DISTINCT FROM 'object'
    OR (SELECT array_agg(x ORDER BY x) FROM jsonb_object_keys(card) x) IS DISTINCT FROM
      ARRAY['base_url','cached_input_per_million','currency','input_per_million','model_id','output_per_million','valid_from','valid_until','version']
    OR p->'schema_version' IS DISTINCT FROM '1'::jsonb
    OR jsonb_typeof(p->'version') IS DISTINCT FROM 'number' OR (p->>'version') !~ '^[1-9][0-9]{0,9}$'
    OR (p->>'version')::numeric>2147483647
    OR p->>'tenant_id' IS DISTINCT FROM t OR p->>'workspace_id' IS DISTINCT FROM w
    OR p->>'actor_id' IS DISTINCT FROM a OR p->>'run_kind' IS DISTINCT FROM k
    OR p->>'model_id' IS DISTINCT FROM 'solar-pro4' OR p->>'currency' IS DISTINCT FROM 'USD'
    OR p->>'connection_id' IS DISTINCT FROM 'provider-upstage'
    OR p->>'policy_id' IS NULL OR p->>'policy_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR jsonb_typeof(p->'policy_id') IS DISTINCT FROM 'string'
    OR jsonb_typeof(p->'limit_minor_units') IS DISTINCT FROM 'number'
    OR p->>'limit_minor_units' !~ '^(0|[1-9][0-9]{0,12})$'
    OR (p->>'limit_minor_units')::numeric>1000000000000
    OR card->>'base_url' IS DISTINCT FROM 'https://api.upstage.ai/v1'
    OR card->>'model_id' IS DISTINCT FROM 'solar-pro4' OR card->>'currency' IS DISTINCT FROM 'USD'
    OR card->>'version' IS NULL OR card->>'version' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    OR jsonb_typeof(card->'version') IS DISTINCT FROM 'string'
    OR EXISTS(SELECT 1 FROM unnest(ARRAY['input_per_million','cached_input_per_million','output_per_million']) x
      WHERE jsonb_typeof(card->x) IS DISTINCT FROM 'string' OR card->>x !~ '^(0|[1-9][0-9]*)(\.[0-9]{1,9})?$'
        OR (card->>x)::numeric>1000000)
    OR (card->>'cached_input_per_million')::numeric>(card->>'input_per_million')::numeric
    OR ib IS NULL OR ib NOT BETWEEN 0 AND 2147483647 OR ob IS NULL OR ob NOT BETWEEN 1 AND 512
    OR jsonb_typeof(card->'valid_from') IS DISTINCT FROM 'string' OR jsonb_typeof(card->'valid_until') IS DISTINCT FROM 'string'
    OR card->>'valid_from' !~ '(\+00:00|Z)$' OR card->>'valid_until' !~ '(\+00:00|Z)$'
    OR NOT (stamp>=(card->>'valid_from')::timestamptz AND stamp<(card->>'valid_until')::timestamptz
      AND (card->>'valid_until')::timestamptz-(card->>'valid_from')::timestamptz BETWEEN interval '0.000001 seconds' AND interval '24 hours') THEN
    RETURN QUERY SELECT 'unknown'::text,'COST_POLICY_UNCONFIRMED'::text,NULL::bigint,NULL::numeric,NULL::text,NULL::text,NULL::timestamptz;RETURN;
  END IF;
  IF EXISTS(SELECT 1 FROM public.operations_run_cost_events e WHERE (e.tenant_id,e.workspace_id,e.run_kind,e.run_id,e.attempt_key)=(t,w,k,r,i)) THEN
    RETURN QUERY SELECT 'blocked'::text,'COST_ATTEMPT_ALREADY_RECORDED'::text,NULL::bigint,NULL::numeric,NULL::text,NULL::text,NULL::timestamptz;RETURN;
  END IF;
  SELECT e.policy INTO frozen FROM public.operations_run_cost_events e WHERE (e.tenant_id,e.workspace_id,e.run_kind,e.run_id)=(t,w,k,r)
    ORDER BY e.sequence LIMIT 1;
  IF FOUND AND frozen IS DISTINCT FROM p THEN
    RETURN QUERY SELECT 'unknown'::text,'COST_POLICY_UNCONFIRMED'::text,NULL::bigint,NULL::numeric,NULL::text,NULL::text,NULL::timestamptz;RETURN;
  END IF;
  IF EXISTS(SELECT 1 FROM public.operations_run_cost_events e WHERE (e.tenant_id,e.workspace_id,e.run_kind,e.run_id)=(t,w,k,r)
    AND (e.state='uncertain' OR (e.state='reserved' AND NOT EXISTS(SELECT 1 FROM public.operations_run_cost_events z
      WHERE (z.tenant_id,z.workspace_id,z.run_kind,z.run_id,z.attempt_key)=(t,w,k,r,e.attempt_key) AND z.state='known')))) THEN
    RETURN QUERY SELECT 'unknown'::text,'COST_SETTLEMENT_UNCONFIRMED'::text,NULL::bigint,NULL::numeric,NULL::text,NULL::text,NULL::timestamptz;RETURN;
  END IF;
  maximum=ceil((ib*(card->>'input_per_million')::numeric+ob*(card->>'output_per_million')::numeric)/1000000*1000000000)/1000000000;
  SELECT coalesce(sum(e.amount) FILTER(WHERE e.state='known'),0),coalesce(max(e.sequence),0)+1 INTO total,latest
    FROM public.operations_run_cost_events e WHERE (e.tenant_id,e.workspace_id,e.run_kind,e.run_id)=(t,w,k,r);
  IF total+maximum>(p->>'limit_minor_units')::numeric/100 THEN
    RETURN QUERY SELECT 'blocked'::text,'COST_LIMIT_EXCEEDED'::text,NULL::bigint,NULL::numeric,NULL::text,NULL::text,NULL::timestamptz;RETURN;
  END IF;
  INSERT INTO public.operations_run_cost_events(tenant_id,workspace_id,actor_id,run_kind,run_id,sequence,attempt_key,state,amount,currency,
    policy,input_token_upper_bound,output_token_limit,price_version,observed_at)
    VALUES(t,w,a,k,r,latest,i,'reserved',maximum,'USD',p,ib,ob,card->>'version',stamp);
  RETURN QUERY SELECT 'reserved'::text,'COST_RESERVED'::text,latest,maximum,'USD'::text,card->>'version',stamp;
END $$;
ALTER FUNCTION public.reserve_operations_run_cost(text,text,text,text,text,text,jsonb,bigint,integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.reserve_operations_run_cost(text,text,text,text,text,text,jsonb,bigint,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reserve_operations_run_cost(text,text,text,text,text,text,jsonb,bigint,integer) TO daon_app;

CREATE FUNCTION public.settle_operations_run_cost(t text,w text,a text,k text,r text,i text,q bigint,
  s text,n numeric,u text,p text,h text,d text,o timestamptz) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE reserved public.operations_run_cost_events; terminal public.operations_run_cost_events; latest bigint;
BEGIN
  IF NOT public.operations_run_cost_parent_allowed(t,w,a,k,r) THEN
    RAISE EXCEPTION 'RUN_COST_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('operations-run-cost-v1|'||t||'|'||w||'|'||k||'|'||r,0));
  SELECT * INTO reserved FROM public.operations_run_cost_events e WHERE (e.tenant_id,e.workspace_id,e.run_kind,e.run_id,e.attempt_key,e.sequence,e.state)=(t,w,k,r,i,q,'reserved');
  IF NOT FOUND OR reserved.actor_id IS DISTINCT FROM a OR reserved.price_version IS DISTINCT FROM p
    OR o IS NULL OR o<reserved.observed_at OR o>clock_timestamp()+interval '5 seconds'
    OR d IS NULL OR d !~ '^cost-[0-9a-f]{64}$' OR s IS NULL OR s NOT IN ('known','unknown','uncertain')
    OR (s='known' AND (n IS NULL OR n<0 OR n<>round(n,9) OR u IS DISTINCT FROM 'USD' OR h IS NULL OR h !~ '^[0-9a-f]{64}$'))
    OR (s<>'known' AND (n IS NOT NULL OR u IS NOT NULL)) THEN
    RAISE EXCEPTION 'RUN_COST_SETTLEMENT_INVALID' USING ERRCODE='23514'; END IF;
  -- 잘못된 공급자 usage로 예약 상한을 넘으면 산정액으로 후속 호출을 허용하지 않는다.
  IF s='known' AND n>reserved.amount THEN s='uncertain';n=NULL;u=NULL;h=NULL; END IF;
  IF s='unknown' THEN s='uncertain'; END IF;
  SELECT * INTO terminal FROM public.operations_run_cost_events e WHERE (e.tenant_id,e.workspace_id,e.run_kind,e.run_id,e.attempt_key)=(t,w,k,r,i) AND e.state<>'reserved';
  IF FOUND THEN
    IF (terminal.state,terminal.amount,terminal.currency,terminal.price_version,terminal.usage_digest,terminal.receipt_id,terminal.observed_at)
      IS DISTINCT FROM (s,n,u,p,h,d,o) THEN RAISE EXCEPTION 'RUN_COST_SETTLEMENT_REPLAY' USING ERRCODE='23505'; END IF;
    RETURN terminal.sequence;
  END IF;
  SELECT max(e.sequence)+1 INTO latest FROM public.operations_run_cost_events e WHERE (e.tenant_id,e.workspace_id,e.run_kind,e.run_id)=(t,w,k,r);
  INSERT INTO public.operations_run_cost_events(tenant_id,workspace_id,actor_id,run_kind,run_id,sequence,attempt_key,state,amount,currency,
    policy,input_token_upper_bound,output_token_limit,price_version,usage_digest,receipt_id,observed_at)
    VALUES(t,w,a,k,r,latest,i,s,n,u,reserved.policy,reserved.input_token_upper_bound,reserved.output_token_limit,p,h,d,o);
  RETURN latest;
END $$;
ALTER FUNCTION public.settle_operations_run_cost(text,text,text,text,text,text,bigint,text,numeric,text,text,text,text,timestamptz) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.settle_operations_run_cost(text,text,text,text,text,text,bigint,text,numeric,text,text,text,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.settle_operations_run_cost(text,text,text,text,text,text,bigint,text,numeric,text,text,text,text,timestamptz) TO daon_app;

CREATE FUNCTION public.record_operations_price_card_receipt(
  t text,w text,r text,a text,i text,c text,m text,b text,n numeric,u text,h text,p text,s text,o timestamptz,d text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE result text;
BEGIN
  IF t IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
    OR w IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'')
    OR a IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'') OR b IS DISTINCT FROM 'price_card'
    OR NOT EXISTS(SELECT 1 FROM public.operations_run_cost_events e WHERE
      (e.tenant_id,e.workspace_id,e.actor_id,e.run_kind,e.run_id,e.receipt_id)=(t,w,a,'question',r,i)
      AND e.state<>'reserved' AND e.price_version=p AND e.policy->>'connection_id'=c
      -- 서버가 예약한 실제 model_attempt ID를 그대로 소비한다. 같은 Run의 다른 호출도 금지한다.
      AND ((m IS NOT NULL AND m=e.attempt_key)
        OR (m IS NULL AND s IN ('unknown','uncertain') AND NOT EXISTS(
          SELECT 1 FROM public.model_attempts z WHERE (z.tenant_id,z.workspace_id,z.record_id)=(t,w,e.attempt_key))))
      AND ((s='known' AND (e.state,e.amount,e.currency,e.usage_digest,e.observed_at) IS NOT DISTINCT FROM ('known'::text,n,u,h,o))
        OR (s IN ('unknown','uncertain') AND e.state='uncertain' AND n IS NULL AND u IS NULL AND e.observed_at=o))) THEN
    RAISE EXCEPTION 'OPERATIONS_RECEIPT_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('operations-cost-v1:'||t||':'||w||':'||r,0));
  IF EXISTS(SELECT 1 FROM public.operations_cost_receipts x WHERE x.tenant_id=t AND x.workspace_id=w AND x.run_id=r
    AND (x.receipt_id=i OR x.model_attempt_id IS NOT DISTINCT FROM m)) THEN
    RAISE EXCEPTION 'OPERATIONS_COST_REPLAY' USING ERRCODE='23505'; END IF;
  INSERT INTO public.operations_cost_receipts(tenant_id,workspace_id,run_id,actor_id,receipt_id,provider_connection_id,
    model_attempt_id,basis,amount,currency,usage_digest,price_version,state,observed_at,receipt_digest)
    VALUES(t,w,r,a,i,c,m,b,n,u,h,p,s,o,d) RETURNING receipt_digest INTO result;
  RETURN result;
END $$;
ALTER FUNCTION public.record_operations_price_card_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.record_operations_price_card_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_operations_price_card_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text) TO daon_app;

CREATE TABLE public.operations_host_capacity_samples (
  host_ref text PRIMARY KEY CHECK(host_ref='wsl-server-daon-user'),
  boot_id text NOT NULL CHECK(boot_id ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'),
  sequence bigint NOT NULL CHECK(sequence>0),
  observed_at timestamptz NOT NULL,received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  cpu_logical_millicores bigint NOT NULL CHECK(cpu_logical_millicores BETWEEN 1000 AND 32768000 AND cpu_logical_millicores%1000=0),
  cpu_usage_basis_points integer CHECK(cpu_usage_basis_points BETWEEN 0 AND 10000),
  memory_total_bytes bigint NOT NULL CHECK(memory_total_bytes>0),
  memory_available_bytes bigint NOT NULL CHECK(memory_available_bytes BETWEEN 0 AND memory_total_bytes),
  cpu_counters bigint[] NOT NULL CHECK(cardinality(cpu_counters)=8 AND array_position(cpu_counters,NULL) IS NULL AND 0<=ALL(cpu_counters))
);
ALTER TABLE public.operations_host_capacity_samples OWNER TO daon_notebook_delete;
REVOKE ALL ON public.operations_host_capacity_samples FROM PUBLIC,daon_app;
ALTER TABLE public.operations_host_capacity_samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operations_host_capacity_samples FORCE ROW LEVEL SECURITY;
-- 호스트 전역 관측이며 임의 조직/actor를 소유자로 꾸미지 않는다. app 직접 조회/쓰기는 계속 없다.
CREATE POLICY operations_host_capacity_fixed_host ON public.operations_host_capacity_samples
  TO daon_notebook_delete USING(host_ref='wsl-server-daon-user')
  WITH CHECK(host_ref='wsl-server-daon-user');

CREATE FUNCTION public.guard_operations_host_capacity() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF TG_OP NOT IN ('INSERT','UPDATE') THEN RAISE EXCEPTION 'HOST_CAPACITY_PROTECTED' USING ERRCODE='55000'; END IF;
  IF NEW.observed_at>clock_timestamp()+interval '5 seconds'
    OR (TG_OP='UPDATE' AND (NEW.host_ref IS DISTINCT FROM OLD.host_ref OR NEW.observed_at<=OLD.observed_at
      OR (NEW.boot_id=OLD.boot_id AND (NEW.sequence<=OLD.sequence OR EXISTS(SELECT 1 FROM generate_series(1,8) x WHERE NEW.cpu_counters[x]<OLD.cpu_counters[x]))))) THEN
    RAISE EXCEPTION 'HOST_CAPACITY_REPLAY' USING ERRCODE='23514'; END IF;
  NEW.received_at=clock_timestamp();RETURN NEW;
END $$;
ALTER FUNCTION public.guard_operations_host_capacity() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_operations_host_capacity() FROM PUBLIC,daon_app;
CREATE TRIGGER operations_host_capacity_guard BEFORE INSERT OR UPDATE OR DELETE ON public.operations_host_capacity_samples
  FOR EACH ROW EXECUTE FUNCTION public.guard_operations_host_capacity();
CREATE TRIGGER operations_host_capacity_no_truncate BEFORE TRUNCATE ON public.operations_host_capacity_samples
  FOR EACH STATEMENT EXECUTE FUNCTION public.guard_operations_host_capacity();

CREATE FUNCTION public.record_operations_host_capacity(h text,b text,q bigint,o timestamptz,c bigint,x integer,m bigint,v bigint,j bigint[])
RETURNS public.operations_host_capacity_samples LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE result public.operations_host_capacity_samples;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('operations-host-capacity-v1:'||h,0));
  INSERT INTO public.operations_host_capacity_samples(host_ref,boot_id,sequence,observed_at,cpu_logical_millicores,
    cpu_usage_basis_points,memory_total_bytes,memory_available_bytes,cpu_counters)
    VALUES(h,b,q,o,c,x,m,v,j) ON CONFLICT(host_ref) DO UPDATE SET boot_id=excluded.boot_id,sequence=excluded.sequence,
    observed_at=excluded.observed_at,cpu_logical_millicores=excluded.cpu_logical_millicores,cpu_usage_basis_points=excluded.cpu_usage_basis_points,
    memory_total_bytes=excluded.memory_total_bytes,memory_available_bytes=excluded.memory_available_bytes,cpu_counters=excluded.cpu_counters
    RETURNING * INTO result;
  RETURN result;
END $$;
ALTER FUNCTION public.record_operations_host_capacity(text,text,bigint,timestamptz,bigint,integer,bigint,bigint,bigint[]) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.record_operations_host_capacity(text,text,bigint,timestamptz,bigint,integer,bigint,bigint,bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_operations_host_capacity(text,text,bigint,timestamptz,bigint,integer,bigint,bigint,bigint[]) TO daon_app;

CREATE FUNCTION public.read_operations_host_capacity()
RETURNS SETOF public.operations_host_capacity_samples LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
  SELECT * FROM public.operations_host_capacity_samples WHERE host_ref='wsl-server-daon-user'
$$;
ALTER FUNCTION public.read_operations_host_capacity() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.read_operations_host_capacity() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_operations_host_capacity() TO daon_app;
