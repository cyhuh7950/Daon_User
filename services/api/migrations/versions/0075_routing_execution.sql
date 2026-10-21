-- B2 draft only: no real database or roles are modified by this development task.
CREATE TABLE public.routing_authorization_plans (
 tenant_id text NOT NULL, workspace_id text NOT NULL, plan_id text NOT NULL,
 actor_id text NOT NULL, session_id text NOT NULL, run_id text NOT NULL,
 routing_policy_version_id text NOT NULL, routing_decision_id text NOT NULL, run_snapshot_id text NOT NULL,
 expires_at timestamptz NOT NULL, nonce_digest text NOT NULL CHECK(nonce_digest ~ '^[0-9a-f]{64}$'),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=262144),
 content_digest text NOT NULL CHECK(content_digest ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,workspace_id,plan_id),
 UNIQUE(tenant_id,workspace_id,run_id), UNIQUE(tenant_id,workspace_id,nonce_digest),
 UNIQUE(tenant_id,workspace_id,plan_id,actor_id,session_id),
 FOREIGN KEY(tenant_id,workspace_id,run_id) REFERENCES public.runs(tenant_id,workspace_id,record_id),
 FOREIGN KEY(tenant_id,workspace_id,routing_policy_version_id) REFERENCES public.routing_policy_versions(tenant_id,workspace_id,record_id),
 FOREIGN KEY(tenant_id,workspace_id,routing_decision_id) REFERENCES public.routing_decisions(tenant_id,workspace_id,record_id),
 FOREIGN KEY(tenant_id,workspace_id,run_snapshot_id) REFERENCES public.run_snapshots(tenant_id,workspace_id,record_id),
 CHECK(payload->>'schema_version'='routing-authorization-v1'),
 CHECK(payload->>'plan_id'=plan_id AND payload->>'run_id'=run_id),
 CHECK(payload->>'nonce_digest'=nonce_digest),
 CHECK(payload->'scope'->>'tenant_id'=tenant_id AND payload->'scope'->>'workspace_id'=workspace_id
   AND payload->'scope'->>'actor_id'=actor_id AND payload->'scope'->>'session_id'=session_id)
);
CREATE TABLE public.routing_execution_claims (
 tenant_id text NOT NULL, workspace_id text NOT NULL, plan_id text NOT NULL,
 actor_id text NOT NULL, session_id text NOT NULL, idempotency_key text NOT NULL,
 request_digest text NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,workspace_id,plan_id), UNIQUE(tenant_id,workspace_id,actor_id,idempotency_key),
 FOREIGN KEY(tenant_id,workspace_id,plan_id,actor_id,session_id)
   REFERENCES public.routing_authorization_plans(tenant_id,workspace_id,plan_id,actor_id,session_id)
);
CREATE TABLE public.routing_attempt_events (
 tenant_id text NOT NULL, workspace_id text NOT NULL, plan_id text NOT NULL,
 attempt_id text NOT NULL, event_kind text NOT NULL CHECK(event_kind IN('prepared','dispatch_claimed','terminal','reconciled')),
 sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
 content_digest text NOT NULL CHECK(content_digest ~ '^[0-9a-f]{64}$'),
 terminal_model_attempt_id text GENERATED ALWAYS AS(CASE WHEN event_kind='terminal' THEN attempt_id END) STORED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,workspace_id,plan_id,attempt_id,event_kind),
 FOREIGN KEY(tenant_id,workspace_id,plan_id) REFERENCES public.routing_execution_claims(tenant_id,workspace_id,plan_id),
 FOREIGN KEY(tenant_id,workspace_id,terminal_model_attempt_id)
   REFERENCES public.model_attempts(tenant_id,workspace_id,record_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE public.routing_dispatch_outbox (
 tenant_id text NOT NULL, workspace_id text NOT NULL, plan_id text NOT NULL, attempt_id text NOT NULL,
 candidate_order integer NOT NULL CHECK(candidate_order BETWEEN 1 AND 8),
 event_kind text GENERATED ALWAYS AS('dispatch_claimed'::text) STORED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,workspace_id,plan_id,attempt_id),
 UNIQUE(tenant_id,workspace_id,plan_id,candidate_order),
 FOREIGN KEY(tenant_id,workspace_id,plan_id,attempt_id,event_kind)
   REFERENCES public.routing_attempt_events(tenant_id,workspace_id,plan_id,attempt_id,event_kind)
);
CREATE FUNCTION public.guard_routing_execution_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN RAISE EXCEPTION 'ROUTING_EXECUTION_IMMUTABLE' USING ERRCODE='55000'; END $$;
REVOKE ALL ON FUNCTION public.guard_routing_execution_immutable() FROM PUBLIC,daon_app;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['routing_authorization_plans','routing_execution_claims','routing_attempt_events','routing_dispatch_outbox'] LOOP
   EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,daon_app',tab);
   EXECUTE format('GRANT SELECT,INSERT ON public.%I TO daon_app',tab);
   EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',tab);
   EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',tab);
   EXECUTE format('CREATE POLICY %I ON public.%I TO daon_app USING '
     ||'(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''') '
     ||'AND workspace_id=nullif(current_setting(''app.workspace_id'',true),'''')) WITH CHECK '
     ||'(tenant_id=nullif(current_setting(''app.tenant_id'',true),'''') '
     ||'AND workspace_id=nullif(current_setting(''app.workspace_id'',true),''''))',tab||'_scope',tab);
   EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW '
     ||'EXECUTE FUNCTION public.guard_routing_execution_immutable()',tab||'_immutable',tab);
   EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON public.%I FOR EACH STATEMENT '
     ||'EXECUTE FUNCTION public.guard_routing_execution_immutable()',tab||'_no_truncate',tab);
 END LOOP;
END $$;
GRANT USAGE,SELECT ON SEQUENCE public.routing_attempt_events_sequence_seq TO daon_app;
CREATE FUNCTION public.guard_routing_authorization_lineage() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.routing_decisions d JOIN public.run_snapshots s
   ON s.tenant_id=d.tenant_id AND s.workspace_id=d.workspace_id
   WHERE d.tenant_id=NEW.tenant_id AND d.workspace_id=NEW.workspace_id
   AND d.record_id=NEW.routing_decision_id AND d.run_id=NEW.run_id
   AND d.routing_policy_version_id=NEW.routing_policy_version_id
   AND s.record_id=NEW.run_snapshot_id AND s.run_id=NEW.run_id
   AND s.routing_policy_version_id=NEW.routing_policy_version_id) THEN
   RAISE EXCEPTION 'ROUTING_LINEAGE_INVALID' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_routing_authorization_lineage() FROM PUBLIC,daon_app;
CREATE TRIGGER routing_authorization_lineage BEFORE INSERT ON public.routing_authorization_plans
 FOR EACH ROW EXECUTE FUNCTION public.guard_routing_authorization_lineage();
-- Provider/account identity is a verified pricing-port projection, never a Key value.
CREATE TABLE public.routing_billing_receipts (
 tenant_id text NOT NULL, billing_scope_id text NOT NULL, provider_code text NOT NULL,
 account_reference text NOT NULL CHECK(account_reference ~ '^[0-9a-f]{64}$'), receipt_id text NOT NULL,
 workspace_id text NOT NULL, plan_id text NOT NULL, attempt_id text NOT NULL,
 content_digest text NOT NULL CHECK(content_digest ~ '^[0-9a-f]{64}$'),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,billing_scope_id,provider_code,account_reference,receipt_id),
 FOREIGN KEY(tenant_id,workspace_id,plan_id) REFERENCES public.routing_authorization_plans(tenant_id,workspace_id,plan_id),
 FOREIGN KEY(tenant_id,workspace_id,attempt_id) REFERENCES public.model_attempts(tenant_id,workspace_id,record_id)
   DEFERRABLE INITIALLY DEFERRED
);
REVOKE ALL ON public.routing_billing_receipts FROM PUBLIC,daon_app;
GRANT SELECT,INSERT ON public.routing_billing_receipts TO daon_app;
ALTER TABLE public.routing_billing_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.routing_billing_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY routing_billing_receipt_scope ON public.routing_billing_receipts TO daon_app
 USING(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),''))
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),''));
CREATE TRIGGER routing_billing_receipt_immutable BEFORE UPDATE OR DELETE ON public.routing_billing_receipts
 FOR EACH ROW EXECUTE FUNCTION public.guard_routing_execution_immutable();
CREATE TRIGGER routing_billing_receipt_no_truncate BEFORE TRUNCATE ON public.routing_billing_receipts
 FOR EACH STATEMENT EXECUTE FUNCTION public.guard_routing_execution_immutable();
CREATE FUNCTION public.guard_routing_terminal_lineage() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE candidate integer; expected_model jsonb; planrow public.routing_authorization_plans;
BEGIN
 SELECT * INTO planrow FROM public.routing_authorization_plans
  WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND plan_id=NEW.plan_id;
 SELECT candidate_order INTO candidate FROM public.routing_dispatch_outbox
  WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND plan_id=NEW.plan_id AND attempt_id=NEW.attempt_id;
 IF candidate IS NULL OR NOT EXISTS(SELECT 1 FROM public.routing_attempt_events e
   WHERE e.tenant_id=NEW.tenant_id AND e.workspace_id=NEW.workspace_id AND e.plan_id=NEW.plan_id
   AND e.attempt_id=NEW.attempt_id AND e.event_kind='prepared' AND (e.payload->>'candidate_order')::integer=candidate) THEN
   RAISE EXCEPTION 'ROUTING_DISPATCH_NOT_CLAIMED' USING ERRCODE='23514'; END IF;
 expected_model=planrow.payload->'parents'->'models'->(candidate-1);
 IF NOT EXISTS(SELECT 1 FROM public.model_attempts m JOIN public.routing_decisions d
   ON d.tenant_id=m.tenant_id AND d.workspace_id=m.workspace_id AND d.record_id=m.routing_decision_id
   WHERE m.tenant_id=NEW.tenant_id AND m.workspace_id=NEW.workspace_id AND m.record_id=NEW.attempt_id
   AND m.created_by=planrow.actor_id AND m.candidate_order=candidate
   AND m.routing_decision_id=planrow.routing_decision_id AND d.run_id=planrow.run_id
   AND m.model_deployment_id=expected_model->>'deployment_id' AND m.model_artifact_id=expected_model->>'artifact_id') THEN
   RAISE EXCEPTION 'ROUTING_TERMINAL_LINEAGE_INVALID' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.guard_routing_terminal_lineage() FROM PUBLIC,daon_app;
CREATE CONSTRAINT TRIGGER routing_terminal_lineage AFTER INSERT ON public.routing_attempt_events
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.event_kind='terminal')
 EXECUTE FUNCTION public.guard_routing_terminal_lineage();
