-- M2 DRAFT ONLY: 작성/직접 정적 검증 승인. 실제 적용/GRANT·정식 수신은 별도 판단.
-- 기존 표/role/membership/owner 변경0. source는 고정 서버 producer이며 worker_id/GUC는 인증 비밀이 아니다.
DO $preconditions$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT count(*) FROM public.alembic_version)<>1
    OR (SELECT version_num FROM public.alembic_version)<>'0070'
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
      AND NOT rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreaterole AND NOT rolcreatedb)
    OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER') THEN
    RAISE EXCEPTION 'OPERATIONS_TELEMETRY_PRECONDITION' USING ERRCODE='55000'; END IF;
  -- 부모/FK 검사에 부족한 기존 권한을 신규 GRANT로 메우지 않는다.
  IF NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.runs','SELECT')
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.model_attempts','SELECT')
    OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.routing_decisions','SELECT') THEN
    RAISE EXCEPTION 'OPERATIONS_TELEMETRY_PARENT_PRIVILEGE_MISSING' USING ERRCODE='55000'; END IF;
END $preconditions$;

CREATE TABLE public.operations_worker_observations (
  worker_id text NOT NULL CHECK(worker_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  boot_id text NOT NULL CHECK(boot_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  worker_kind text NOT NULL CHECK(worker_kind IN ('document','studio','object')),
  process_started_at timestamptz NOT NULL,
  sequence bigint NOT NULL CHECK(sequence>0),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  poll_started_at timestamptz,
  poll_finished_at timestamptz,
  active_started_at timestamptz,
  last_safe_code text CHECK(last_safe_code IN ('WORKER_STOPPED','WORKER_POLL_FAILED')),
  cpu_quota_millicores bigint CHECK(cpu_quota_millicores>=0),
  memory_limit_bytes bigint CHECK(memory_limit_bytes>=0),
  memory_current_bytes bigint CHECK(memory_current_bytes>=0),
  runtime_available_bytes bigint CHECK(runtime_available_bytes>=0),
  producer_version integer NOT NULL CHECK(producer_version=1),
  PRIMARY KEY(worker_id,boot_id),
  CHECK(process_started_at<=observed_at),
  CHECK(poll_started_at IS NULL OR poll_started_at BETWEEN process_started_at AND observed_at),
  CHECK(poll_finished_at IS NULL OR (poll_started_at IS NOT NULL AND poll_finished_at BETWEEN process_started_at AND observed_at)),
  CHECK(active_started_at IS NULL OR (poll_started_at IS NOT NULL AND active_started_at BETWEEN poll_started_at AND observed_at)),
  CHECK(last_safe_code IS DISTINCT FROM 'WORKER_STOPPED' OR active_started_at IS NULL)
);
ALTER TABLE public.operations_worker_observations OWNER TO daon_notebook_delete;
REVOKE ALL ON public.operations_worker_observations FROM PUBLIC,daon_app;

CREATE FUNCTION public.guard_operations_worker_observation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF TG_OP NOT IN ('INSERT','UPDATE') THEN
    RAISE EXCEPTION 'WORKER_OBSERVATION_IMMUTABLE' USING ERRCODE='55000'; END IF;
  IF NEW.observed_at>clock_timestamp()+interval '5 seconds' THEN
    RAISE EXCEPTION 'WORKER_OBSERVATION_INVALID' USING ERRCODE='23514'; END IF;
  IF TG_OP='UPDATE' AND ((NEW.worker_id,NEW.boot_id,NEW.worker_kind,NEW.process_started_at,NEW.producer_version)
    IS DISTINCT FROM (OLD.worker_id,OLD.boot_id,OLD.worker_kind,OLD.process_started_at,OLD.producer_version)
    OR NEW.sequence<=OLD.sequence OR NEW.observed_at<OLD.observed_at) THEN
    RAISE EXCEPTION 'WORKER_OBSERVATION_REPLAY' USING ERRCODE='23514'; END IF;
  NEW.received_at=clock_timestamp();
  RETURN NEW;
END $$;
ALTER FUNCTION public.guard_operations_worker_observation() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_operations_worker_observation() FROM PUBLIC,daon_app;
CREATE TRIGGER operations_worker_observations_guard BEFORE INSERT OR UPDATE OR DELETE ON public.operations_worker_observations
  FOR EACH ROW EXECUTE FUNCTION public.guard_operations_worker_observation();
CREATE TRIGGER operations_worker_observations_no_truncate BEFORE TRUNCATE ON public.operations_worker_observations
  FOR EACH STATEMENT EXECUTE FUNCTION public.guard_operations_worker_observation();

CREATE FUNCTION public.record_operations_worker_sample(
  p_worker_id text,p_boot_id text,p_worker_kind text,p_process_started_at timestamptz,p_sequence bigint,p_observed_at timestamptz,
  p_poll_started_at timestamptz,p_poll_finished_at timestamptz,p_active_started_at timestamptz,p_last_safe_code text,
  p_cpu_quota_millicores bigint,p_memory_limit_bytes bigint,p_memory_current_bytes bigint,p_runtime_available_bytes bigint,p_producer_version integer
) RETURNS public.operations_worker_observations
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE previous public.operations_worker_observations; result public.operations_worker_observations;
BEGIN
  -- 같은 worker의 다른 boot/동시 sequence도 직렬화. caller의 유한 timeout 안에서 실패한다.
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('operations-worker-v1:'||p_worker_id,0));
  SELECT * INTO previous FROM public.operations_worker_observations WHERE worker_id=p_worker_id
    ORDER BY process_started_at DESC,received_at DESC LIMIT 1;
  IF FOUND AND (previous.worker_kind IS DISTINCT FROM p_worker_kind
    OR p_process_started_at<previous.process_started_at
    OR (previous.boot_id<>p_boot_id AND p_process_started_at<=previous.process_started_at)
    OR (previous.boot_id=p_boot_id AND (p_process_started_at IS DISTINCT FROM previous.process_started_at
      OR p_sequence<=previous.sequence OR p_observed_at<previous.observed_at))) THEN
    RAISE EXCEPTION 'WORKER_OBSERVATION_REPLAY' USING ERRCODE='23514'; END IF;
  INSERT INTO public.operations_worker_observations
    (worker_id,boot_id,worker_kind,process_started_at,sequence,observed_at,received_at,poll_started_at,poll_finished_at,
     active_started_at,last_safe_code,cpu_quota_millicores,memory_limit_bytes,memory_current_bytes,runtime_available_bytes,producer_version)
  VALUES (p_worker_id,p_boot_id,p_worker_kind,p_process_started_at,p_sequence,p_observed_at,clock_timestamp(),p_poll_started_at,p_poll_finished_at,
    p_active_started_at,p_last_safe_code,p_cpu_quota_millicores,p_memory_limit_bytes,p_memory_current_bytes,p_runtime_available_bytes,p_producer_version)
  ON CONFLICT(worker_id,boot_id) DO UPDATE SET worker_kind=excluded.worker_kind,process_started_at=excluded.process_started_at,
    producer_version=excluded.producer_version,sequence=excluded.sequence,observed_at=excluded.observed_at,
    poll_started_at=excluded.poll_started_at,poll_finished_at=excluded.poll_finished_at,active_started_at=excluded.active_started_at,
    last_safe_code=excluded.last_safe_code,cpu_quota_millicores=excluded.cpu_quota_millicores,memory_limit_bytes=excluded.memory_limit_bytes,
    memory_current_bytes=excluded.memory_current_bytes,runtime_available_bytes=excluded.runtime_available_bytes
  RETURNING * INTO result;
  RETURN result;
END $$;
ALTER FUNCTION public.record_operations_worker_sample(text,text,text,timestamptz,bigint,timestamptz,timestamptz,timestamptz,timestamptz,text,bigint,bigint,bigint,bigint,integer) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.record_operations_worker_sample(text,text,text,timestamptz,bigint,timestamptz,timestamptz,timestamptz,timestamptz,text,bigint,bigint,bigint,bigint,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_operations_worker_sample(text,text,text,timestamptz,bigint,timestamptz,timestamptz,timestamptz,timestamptz,text,bigint,bigint,bigint,bigint,integer) TO daon_app;

CREATE FUNCTION public.read_operations_worker_samples(p_expected_ids text[]) RETURNS SETOF public.operations_worker_observations
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  -- 기존 서버 코드의 실제 VIEW/조직 활성 재검사 뒤만 소비. expected IDs는 서버 설정, HTTP 입력0.
  IF cardinality(p_expected_ids)<>3 OR (SELECT count(DISTINCT item) FROM unnest(p_expected_ids) item)<>3
    OR EXISTS(SELECT 1 FROM unnest(p_expected_ids) item WHERE item IS NULL OR item !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$') THEN
    RAISE EXCEPTION 'WORKER_OBSERVATION_INVALID' USING ERRCODE='23514'; END IF;
  RETURN QUERY SELECT o.* FROM public.operations_worker_observations o WHERE (o.worker_id,o.boot_id) IN (
    SELECT ranked.worker_id,ranked.boot_id FROM (
      SELECT worker_id,boot_id,row_number() OVER(PARTITION BY worker_id ORDER BY process_started_at DESC,received_at DESC) AS rank
      FROM public.operations_worker_observations WHERE worker_id=ANY(p_expected_ids)) ranked WHERE ranked.rank<=2);
END $$;
ALTER FUNCTION public.read_operations_worker_samples(text[]) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.read_operations_worker_samples(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_operations_worker_samples(text[]) TO daon_app;

CREATE TABLE public.operations_deployment_receipts (
  operation_id text PRIMARY KEY CHECK(operation_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  operation text NOT NULL CHECK(operation IN ('deploy','rollback')),
  code_revision text CHECK(code_revision ~ '^[0-9a-f]{40}$'),
  image_digest text CHECK(image_digest ~ '^sha256:[0-9a-f]{64}$'),
  previous_image_digest text CHECK(previous_image_digest ~ '^sha256:[0-9a-f]{64}$'),
  state text NOT NULL CHECK(state IN ('succeeded','failed','uncertain')),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  safe_code text NOT NULL CHECK(safe_code ~ '^[A-Z][A-Z0-9_]{0,127}$'),
  receipt_digest text NOT NULL CHECK(receipt_digest ~ '^[0-9a-f]{64}$'),
  CHECK(state<>'succeeded' OR (code_revision IS NOT NULL AND image_digest IS NOT NULL)),
  CHECK(operation<>'rollback' OR state<>'succeeded' OR previous_image_digest IS NOT NULL)
);
ALTER TABLE public.operations_deployment_receipts OWNER TO daon_notebook_delete;
REVOKE ALL ON public.operations_deployment_receipts FROM PUBLIC,daon_app;
-- M2-3 운영자 receipt writer의 별도 보호 consumer가 연결되기 전 app 쓰기/조회0.

CREATE TABLE public.operations_cost_receipts (
  tenant_id text NOT NULL,workspace_id text NOT NULL,run_id text NOT NULL,
  receipt_id text NOT NULL CHECK(receipt_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  actor_id text NOT NULL,provider_connection_id text REFERENCES public.system_provider_connections(connection_id),
  model_attempt_id text,basis text NOT NULL CHECK(basis IN ('provider_reported','price_card')),
  amount numeric(24,9),currency text CHECK(currency ~ '^[A-Z]{3}$'),
  usage_digest text CHECK(usage_digest ~ '^[0-9a-f]{64}$'),price_version text,
  state text NOT NULL CHECK(state IN ('known','unknown','uncertain')),
  observed_at timestamptz NOT NULL,received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  receipt_digest text NOT NULL CHECK(receipt_digest ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY(tenant_id,workspace_id,run_id,receipt_id),
  FOREIGN KEY(tenant_id,workspace_id,run_id) REFERENCES public.runs(tenant_id,workspace_id,record_id),
  FOREIGN KEY(tenant_id,workspace_id,model_attempt_id) REFERENCES public.model_attempts(tenant_id,workspace_id,record_id),
  CHECK((state='known' AND amount IS NOT NULL AND amount>=0 AND currency IS NOT NULL AND provider_connection_id IS NOT NULL)
    OR (state IN ('unknown','uncertain') AND amount IS NULL AND currency IS NULL)),
  CHECK(basis<>'price_card' OR (price_version IS NOT NULL AND length(price_version) BETWEEN 1 AND 128))
);
ALTER TABLE public.operations_cost_receipts OWNER TO daon_notebook_delete;
ALTER TABLE public.operations_cost_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operations_cost_receipts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.operations_cost_receipts FROM PUBLIC,daon_app;
CREATE POLICY operations_cost_receipts_scope ON public.operations_cost_receipts
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'') AND actor_id=nullif(current_setting('app.actor_id',true),''))
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'') AND actor_id=nullif(current_setting('app.actor_id',true),''));
-- M2-3 실제 비용 producer/현재 자료 가시성 consumer 연결 전 app INSERT/SELECT0.
-- 위 FK는 성공 attempt가 없는 실제 failed/cancelled Run의 model_attempt_id NULL을 허용한다.

CREATE FUNCTION public.guard_operations_receipt() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE attempt_run text; author text;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'OPERATIONS_RECEIPT_IMMUTABLE' USING ERRCODE='55000'; END IF;
  IF NEW.observed_at>clock_timestamp()+interval '5 seconds' THEN
    RAISE EXCEPTION 'OPERATIONS_RECEIPT_INVALID' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='operations_cost_receipts' THEN
    SELECT created_by INTO author FROM public.runs WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND record_id=NEW.run_id;
    IF NOT FOUND OR author IS DISTINCT FROM NEW.actor_id THEN
      RAISE EXCEPTION 'OPERATIONS_RECEIPT_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
    IF NEW.model_attempt_id IS NOT NULL THEN
      SELECT r.run_id INTO attempt_run FROM public.model_attempts a JOIN public.routing_decisions r
        ON (r.tenant_id,r.workspace_id,r.record_id)=(a.tenant_id,a.workspace_id,a.routing_decision_id)
        WHERE a.tenant_id=NEW.tenant_id AND a.workspace_id=NEW.workspace_id AND a.record_id=NEW.model_attempt_id;
      IF NOT FOUND OR attempt_run IS DISTINCT FROM NEW.run_id THEN
        RAISE EXCEPTION 'OPERATIONS_RECEIPT_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
    END IF;
  END IF;
  NEW.received_at=clock_timestamp();RETURN NEW;
END $$;
ALTER FUNCTION public.guard_operations_receipt() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_operations_receipt() FROM PUBLIC,daon_app;
CREATE TRIGGER operations_deployment_receipts_guard BEFORE INSERT OR UPDATE OR DELETE ON public.operations_deployment_receipts
  FOR EACH ROW EXECUTE FUNCTION public.guard_operations_receipt();
CREATE TRIGGER operations_deployment_receipts_no_truncate BEFORE TRUNCATE ON public.operations_deployment_receipts
  FOR EACH STATEMENT EXECUTE FUNCTION public.guard_operations_receipt();
CREATE TRIGGER operations_cost_receipts_guard BEFORE INSERT OR UPDATE OR DELETE ON public.operations_cost_receipts
  FOR EACH ROW EXECUTE FUNCTION public.guard_operations_receipt();
CREATE TRIGGER operations_cost_receipts_no_truncate BEFORE TRUNCATE ON public.operations_cost_receipts
  FOR EACH STATEMENT EXECUTE FUNCTION public.guard_operations_receipt();

-- 기존 self ALL 정책은 그대로. 현재 관리자/자료 접근은 서버에서 전후 재검증하고
-- owner의 SELECT 한정 범위만 추가한다. app 직접 SELECT/쓰기·RLS 우회는 허용하지 않는다.
CREATE POLICY operations_cost_receipts_manager_select ON public.operations_cost_receipts FOR SELECT TO daon_notebook_delete
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),'')
    AND current_setting('app.operations_cost_scope',true)='workspace_visible'
    AND public.registered_restore_actor_allowed(tenant_id,workspace_id,nullif(current_setting('app.actor_id',true),'')));

CREATE FUNCTION public.record_operations_cost_receipt(
  t text,w text,r text,a text,i text,c text,m text,b text,n numeric,u text,h text,p text,s text,o timestamptz,d text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE result text;
BEGIN
  IF t IS DISTINCT FROM nullif(current_setting('app.tenant_id',true),'')
    OR w IS DISTINCT FROM nullif(current_setting('app.workspace_id',true),'')
    OR a IS DISTINCT FROM nullif(current_setting('app.actor_id',true),'')
    OR b IS DISTINCT FROM 'provider_reported' OR p IS NOT NULL THEN
    RAISE EXCEPTION 'OPERATIONS_RECEIPT_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('operations-cost-v1:'||t||':'||w||':'||r,0));
  IF EXISTS(SELECT 1 FROM public.operations_cost_receipts x WHERE x.tenant_id=t AND x.workspace_id=w AND x.run_id=r
      AND (x.receipt_id=i OR x.model_attempt_id IS NOT DISTINCT FROM m)) THEN
    RAISE EXCEPTION 'OPERATIONS_COST_REPLAY' USING ERRCODE='23505'; END IF;
  INSERT INTO public.operations_cost_receipts(tenant_id,workspace_id,run_id,actor_id,receipt_id,provider_connection_id,
    model_attempt_id,basis,amount,currency,usage_digest,price_version,state,observed_at,receipt_digest)
    VALUES(t,w,r,a,i,c,m,b,n,u,h,p,s,o,d) RETURNING receipt_digest INTO result;
  RETURN result;
END $$;
ALTER FUNCTION public.record_operations_cost_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.record_operations_cost_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_operations_cost_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text) TO daon_app;

CREATE FUNCTION public.read_operations_cost_receipts(ids text[],scope text)
RETURNS TABLE(run_id text,actor_id text,model_attempt_id text,state text,amount numeric,currency text,observed_at timestamptz,received_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE previous_scope text;
BEGIN
  IF scope IS NULL OR scope NOT IN ('self','workspace_visible') OR ids IS NULL
    OR EXISTS(SELECT 1 FROM unnest(ids) v WHERE v IS NULL OR v !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$')
    OR cardinality(ids)<>(SELECT count(DISTINCT v) FROM unnest(ids) v) THEN
    RAISE EXCEPTION 'OPERATIONS_RECEIPT_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  IF scope='workspace_visible' AND NOT public.registered_restore_actor_allowed(
    nullif(current_setting('app.tenant_id',true),''),nullif(current_setting('app.workspace_id',true),''),
    nullif(current_setting('app.actor_id',true),'')) THEN
    RAISE EXCEPTION 'OPERATIONS_RECEIPT_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  previous_scope=current_setting('app.operations_cost_scope',true);
  PERFORM set_config('app.operations_cost_scope',scope,true);
  RETURN QUERY SELECT x.run_id,x.actor_id,x.model_attempt_id,x.state,x.amount,x.currency,x.observed_at,x.received_at
    FROM public.operations_cost_receipts x WHERE x.tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND x.workspace_id=nullif(current_setting('app.workspace_id',true),'') AND x.run_id=ANY(ids)
    AND (scope='workspace_visible' OR x.actor_id=nullif(current_setting('app.actor_id',true),''))
    ORDER BY x.run_id,x.model_attempt_id NULLS FIRST,x.receipt_id;
  PERFORM set_config('app.operations_cost_scope',coalesce(previous_scope,''),true);
END $$;
ALTER FUNCTION public.read_operations_cost_receipts(text[],text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.read_operations_cost_receipts(text[],text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_operations_cost_receipts(text[],text) TO daon_app;

CREATE FUNCTION public.read_operations_deployment_receipt() RETURNS SETOF public.operations_deployment_receipts
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  RETURN QUERY SELECT * FROM public.operations_deployment_receipts ORDER BY observed_at DESC,received_at DESC,operation_id DESC LIMIT 1;
END
$$;
ALTER FUNCTION public.read_operations_deployment_receipt() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.read_operations_deployment_receipt() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_operations_deployment_receipt() TO daon_app;

-- 운영자 연결 전용. daon_app/PUBLIC의 실행 권한은 없으며 기존 role/membership 추가0.
CREATE FUNCTION public.record_operations_deployment_receipt(i text,o text,c text,h text,p text,s text,t timestamptz,k text,d text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE result text;
BEGIN
  INSERT INTO public.operations_deployment_receipts(operation_id,operation,code_revision,image_digest,previous_image_digest,
    state,observed_at,safe_code,receipt_digest) VALUES(i,o,c,h,p,s,t,k,d) RETURNING receipt_digest INTO result;
  RETURN result;
END $$;
ALTER FUNCTION public.record_operations_deployment_receipt(text,text,text,text,text,text,timestamptz,text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.record_operations_deployment_receipt(text,text,text,text,text,text,timestamptz,text,text) FROM PUBLIC,daon_app;
