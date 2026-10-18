-- R2 DRAFT ONLY. 0069 -> 0070, 실제 실행/권한 적용 승인과 분리한다.
-- 정확 footprint: 새3표/2함수/신규 각3trigger(INSERT/불변/TRUNCATE), 기존 fixture INSERT guard1.
-- 기존0006 CHECK/immutable/RLS/owner/ACL 변경0, 새 role/membership0.
-- caller는 현재 세션/POLICY_MANAGE/SourceACL/law/web/closure를 정식 resolver로
-- 검증한다. 아래 DB 판정은 추가 교집합이며 GUC는 사용자 인증 비밀이 아니다.
-- 빈 target 검사와 physical fingerprint는 trusted live probe가 수행한다.
-- 동시 동일 request의 fixture/registered 혼용은 같은 부모 FOR UPDATE로 직렬화.
-- 이 가시성 증명은 READ COMMITTED 한정. 다른 isolation은 변경하지 않고 거부.

DO $preconditions$
DECLARE item text;
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT count(*) FROM public.alembic_version)<>1
    OR (SELECT version_num FROM public.alembic_version)<>'0069'
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
      AND NOT rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreaterole AND NOT rolcreatedb)
    OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER') THEN
    RAISE EXCEPTION 'REGISTERED_RESTORE_MIGRATION_PRECONDITION' USING ERRCODE='55000'; END IF;
  FOREACH item IN ARRAY ARRAY['backup_records','backup_record_locator','backup_manifests',
      'restore_requests','restore_request_locator','restore_previews'] LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=item AND c.relkind='r' AND c.relrowsecurity
          AND c.relforcerowsecurity AND pg_catalog.pg_get_userbyid(c.relowner)='daon_app')
      OR NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.'||item,'SELECT') THEN
      RAISE EXCEPTION 'REGISTERED_RESTORE_PARENT_PRECONDITION' USING ERRCODE='55000'; END IF;
  END LOOP;
  -- FOR UPDATE 실효 권한 선행 확인. 부족한 권한을 새 GRANT로 메우지 않는다.
  IF NOT pg_catalog.has_column_privilege('daon_notebook_delete','public.restore_requests','version','UPDATE')
    OR NOT pg_catalog.has_column_privilege('daon_app','public.restore_requests','version','UPDATE')
    OR NOT pg_catalog.has_column_privilege('daon_notebook_delete','public.backup_records','version','UPDATE')
    OR NOT pg_catalog.has_column_privilege('daon_notebook_delete','public.backup_manifests','manifest_digest','UPDATE') THEN
    RAISE EXCEPTION 'REGISTERED_RESTORE_LOCK_PRIVILEGE_MISSING' USING ERRCODE='55000'; END IF;
  FOREACH item IN ARRAY ARRAY['identity_users','identity_auth_workspaces','identity_auth_tenant_roles',
      'identity_auth_memberships','identity_org_tenant_memberships'] LOOP
    IF NOT pg_catalog.has_table_privilege('daon_notebook_delete','public.'||item,'SELECT') THEN
      RAISE EXCEPTION 'REGISTERED_RESTORE_AUTH_PRIVILEGE_MISSING' USING ERRCODE='55000'; END IF;
  END LOOP;
END $preconditions$;

CREATE TABLE public.registered_restore_target_reservations (
  registry_generation_id text NOT NULL CHECK(registry_generation_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  destination_id text NOT NULL CHECK(destination_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  tenant_id text NOT NULL, workspace_id text NOT NULL, request_id text NOT NULL, actor_id text NOT NULL,
  destination_version integer NOT NULL CHECK(destination_version>0),
  backup_id text NOT NULL, manifest_version integer NOT NULL CHECK(manifest_version=3),
  source_manifest_digest text NOT NULL CHECK(source_manifest_digest ~ '^[0-9a-f]{64}$'),
  physical_receipt_digest text NOT NULL CHECK(physical_receipt_digest ~ '^[0-9a-f]{64}$'),
  target_database_fingerprint text NOT NULL CHECK(target_database_fingerprint ~ '^[0-9a-f]{64}$'),
  target_bucket_fingerprint text NOT NULL CHECK(target_bucket_fingerprint ~ '^[0-9a-f]{64}$'),
  owner_operation_id text NOT NULL CHECK(owner_operation_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  reserved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(registry_generation_id,destination_id),
  UNIQUE(target_database_fingerprint),
  UNIQUE(target_bucket_fingerprint),
  UNIQUE(tenant_id,workspace_id,request_id),
  UNIQUE(tenant_id,workspace_id,request_id,registry_generation_id,destination_id,destination_version,
    backup_id,manifest_version,source_manifest_digest,actor_id),
  FOREIGN KEY(tenant_id,workspace_id,request_id) REFERENCES public.restore_requests(tenant_id,workspace_id,request_id),
  FOREIGN KEY(tenant_id,request_id) REFERENCES public.restore_request_locator(tenant_id,request_id),
  FOREIGN KEY(tenant_id,backup_id) REFERENCES public.backup_record_locator(tenant_id,backup_id),
  FOREIGN KEY(tenant_id,workspace_id,backup_id,manifest_version)
    REFERENCES public.backup_manifests(tenant_id,workspace_id,backup_id,manifest_version)
);

CREATE TABLE public.registered_restore_previews (
  tenant_id text NOT NULL, workspace_id text NOT NULL, request_id text NOT NULL,
  preview_version integer NOT NULL CHECK(preview_version>0), actor_id text NOT NULL,
  registry_generation_id text NOT NULL, destination_id text NOT NULL, destination_version integer NOT NULL,
  backup_id text NOT NULL, manifest_version integer NOT NULL CHECK(manifest_version=3),
  source_manifest_digest text NOT NULL CHECK(source_manifest_digest ~ '^[0-9a-f]{64}$'),
  closure_fingerprint text NOT NULL CHECK(closure_fingerprint ~ '^[0-9a-f]{64}$'),
  included_roots_fingerprint text NOT NULL CHECK(included_roots_fingerprint ~ '^[0-9a-f]{64}$'),
  excluded_roots_fingerprint text NOT NULL CHECK(excluded_roots_fingerprint ~ '^[0-9a-f]{64}$'),
  source_policy_version text NOT NULL, retention_watermark text NOT NULL,
  expires_at timestamptz NOT NULL,
  encrypted_preview_receipt bytea NOT NULL CHECK(octet_length(encrypted_preview_receipt) BETWEEN 28 AND 33554432),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(tenant_id,workspace_id,request_id,preview_version),
  UNIQUE(tenant_id,workspace_id,request_id,preview_version,registry_generation_id,destination_id,destination_version,actor_id),
  CHECK(expires_at>created_at AND expires_at<=created_at+interval '24 hours'),
  FOREIGN KEY(tenant_id,workspace_id,request_id,registry_generation_id,destination_id,destination_version,
      backup_id,manifest_version,source_manifest_digest,actor_id)
    REFERENCES public.registered_restore_target_reservations(tenant_id,workspace_id,request_id,registry_generation_id,
      destination_id,destination_version,backup_id,manifest_version,source_manifest_digest,actor_id)
);

CREATE TABLE public.registered_restore_phase_receipts (
  tenant_id text NOT NULL, workspace_id text NOT NULL, request_id text NOT NULL,
  sequence bigint NOT NULL CHECK(sequence>0), actor_id text NOT NULL,
  registry_generation_id text NOT NULL, destination_id text NOT NULL, destination_version integer NOT NULL,
  preview_version integer NOT NULL,
  operation_id text NOT NULL CHECK(operation_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  idempotency_key text NOT NULL CHECK(idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  -- 단계 번호는 R3 소비자가 Foundation→Object→Source→Version→Notebook→Binding→검증에 대응한다.
  phase integer NOT NULL CHECK(phase BETWEEN 1 AND 7),
  outcome text NOT NULL CHECK(outcome IN ('intent','succeeded','failed','uncertain')),
  target_digest text NOT NULL CHECK(target_digest ~ '^[0-9a-f]{64}$'),
  result_digest text CHECK(result_digest ~ '^[0-9a-f]{64}$'),
  trace_id text NOT NULL CHECK(trace_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(tenant_id,workspace_id,request_id,sequence),
  UNIQUE(tenant_id,workspace_id,request_id,operation_id,phase,outcome),
  CHECK((outcome='intent' AND result_digest IS NULL) OR (outcome<>'intent' AND result_digest IS NOT NULL)),
  FOREIGN KEY(tenant_id,workspace_id,request_id,preview_version,registry_generation_id,destination_id,destination_version,actor_id)
    REFERENCES public.registered_restore_previews(tenant_id,workspace_id,request_id,preview_version,
      registry_generation_id,destination_id,destination_version,actor_id)
);

ALTER TABLE public.registered_restore_target_reservations OWNER TO daon_notebook_delete;
ALTER TABLE public.registered_restore_previews OWNER TO daon_notebook_delete;
ALTER TABLE public.registered_restore_phase_receipts OWNER TO daon_notebook_delete;

-- 정식 ROLE_ACTION_MATRIX/_role_binding의 POLICY_MANAGE 교집합.
-- active tenant-role이 있으면 workspace membership으로 우회하지 않는 우선순위도 보존.
CREATE FUNCTION public.registered_restore_actor_allowed(t text,w text,a text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
  SELECT t=nullif(current_setting('app.tenant_id',true),'')
    AND w=nullif(current_setting('app.workspace_id',true),'')
    AND a=nullif(current_setting('app.actor_id',true),'')
    AND EXISTS(SELECT 1 FROM public.identity_users u WHERE u.user_id=a AND u.state='active')
    AND EXISTS(SELECT 1 FROM public.identity_auth_workspaces x
      LEFT JOIN public.identity_auth_tenant_roles r ON r.tenant_id=x.tenant_id AND r.user_id=a AND r.state='active'
      LEFT JOIN public.identity_auth_memberships m ON m.tenant_id=x.tenant_id AND m.workspace_id=x.workspace_id
        AND m.user_id=a AND m.state='active'
      WHERE x.tenant_id=t AND x.workspace_id=w
        AND CASE WHEN r.user_id IS NOT NULL THEN
          (r.role='personal_owner' AND x.workspace_kind='personal')
          OR (r.role='organization_admin' AND x.workspace_kind='organization')
          ELSE m.role='workspace_admin' END
        AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o
          WHERE o.tenant_id=t AND o.user_id=a AND x.workspace_kind='organization' AND o.state<>'active'))
$$;
ALTER FUNCTION public.registered_restore_actor_allowed(text,text,text) OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.registered_restore_actor_allowed(text,text,text) FROM PUBLIC,daon_app;
GRANT EXECUTE ON FUNCTION public.registered_restore_actor_allowed(text,text,text) TO daon_app;

ALTER TABLE public.registered_restore_target_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registered_restore_target_reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY registered_restore_reservations_scope ON public.registered_restore_target_reservations
  USING(public.registered_restore_actor_allowed(tenant_id,workspace_id,actor_id)
    AND EXISTS(SELECT 1 FROM public.restore_requests r WHERE r.tenant_id=registered_restore_target_reservations.tenant_id
      AND r.workspace_id=registered_restore_target_reservations.workspace_id
      AND r.request_id=registered_restore_target_reservations.request_id AND r.actor_id=registered_restore_target_reservations.actor_id))
  WITH CHECK(public.registered_restore_actor_allowed(tenant_id,workspace_id,actor_id)
    AND EXISTS(SELECT 1 FROM public.restore_requests r WHERE r.tenant_id=registered_restore_target_reservations.tenant_id
      AND r.workspace_id=registered_restore_target_reservations.workspace_id
      AND r.request_id=registered_restore_target_reservations.request_id AND r.actor_id=registered_restore_target_reservations.actor_id));
-- 내부 trigger의 fixture 혼용 검사는 다른 actor의 동일 scope 예약도 볼 수 있어야 한다.
-- 앱 policy는 좁게 유지. 기존 internal owner에 앱이 진입 가능한 환경은 적용 전에 거부한다.
CREATE POLICY registered_restore_reservations_owner_read ON public.registered_restore_target_reservations
  FOR SELECT TO daon_notebook_delete USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')
    AND workspace_id=nullif(current_setting('app.workspace_id',true),''));
ALTER TABLE public.registered_restore_previews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registered_restore_previews FORCE ROW LEVEL SECURITY;
CREATE POLICY registered_restore_previews_scope ON public.registered_restore_previews
  USING(public.registered_restore_actor_allowed(tenant_id,workspace_id,actor_id)
    AND EXISTS(SELECT 1 FROM public.registered_restore_target_reservations r
      WHERE r.tenant_id=registered_restore_previews.tenant_id AND r.workspace_id=registered_restore_previews.workspace_id
        AND r.request_id=registered_restore_previews.request_id AND r.actor_id=registered_restore_previews.actor_id
        AND r.registry_generation_id=registered_restore_previews.registry_generation_id AND r.destination_id=registered_restore_previews.destination_id))
  WITH CHECK(public.registered_restore_actor_allowed(tenant_id,workspace_id,actor_id)
    AND EXISTS(SELECT 1 FROM public.registered_restore_target_reservations r
      WHERE r.tenant_id=registered_restore_previews.tenant_id AND r.workspace_id=registered_restore_previews.workspace_id
        AND r.request_id=registered_restore_previews.request_id AND r.actor_id=registered_restore_previews.actor_id
        AND r.registry_generation_id=registered_restore_previews.registry_generation_id AND r.destination_id=registered_restore_previews.destination_id));
ALTER TABLE public.registered_restore_phase_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registered_restore_phase_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY registered_restore_phase_scope ON public.registered_restore_phase_receipts
  USING(public.registered_restore_actor_allowed(tenant_id,workspace_id,actor_id)
    AND EXISTS(SELECT 1 FROM public.registered_restore_previews p
      WHERE p.tenant_id=registered_restore_phase_receipts.tenant_id AND p.workspace_id=registered_restore_phase_receipts.workspace_id
        AND p.request_id=registered_restore_phase_receipts.request_id AND p.actor_id=registered_restore_phase_receipts.actor_id
        AND p.preview_version=registered_restore_phase_receipts.preview_version))
  WITH CHECK(public.registered_restore_actor_allowed(tenant_id,workspace_id,actor_id)
    AND EXISTS(SELECT 1 FROM public.registered_restore_previews p
      WHERE p.tenant_id=registered_restore_phase_receipts.tenant_id AND p.workspace_id=registered_restore_phase_receipts.workspace_id
        AND p.request_id=registered_restore_phase_receipts.request_id AND p.actor_id=registered_restore_phase_receipts.actor_id
        AND p.preview_version=registered_restore_phase_receipts.preview_version));

CREATE FUNCTION public.guard_registered_restore_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE parent public.restore_requests; manifest public.backup_manifests; backup public.backup_records;
  reservation public.registered_restore_target_reservations; previous public.registered_restore_phase_receipts;
  selected_preview public.registered_restore_previews;
  last_preview integer;
BEGIN
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'REGISTERED_RESTORE_IMMUTABLE' USING ERRCODE='55000'; END IF;
  IF current_setting('transaction_isolation')<>'read committed' THEN
    RAISE EXCEPTION 'REGISTERED_RESTORE_ISOLATION_UNSUPPORTED' USING ERRCODE='55000'; END IF;
  SELECT * INTO parent FROM public.restore_requests
    WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND request_id=NEW.request_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'REGISTERED_RESTORE_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
  IF TG_TABLE_NAME='restore_previews' THEN
    -- additive INSERT만: 이전 fixture history/Cancel/immutable trigger 변경0.
    IF EXISTS(SELECT 1 FROM public.registered_restore_target_reservations r WHERE r.tenant_id=NEW.tenant_id
      AND r.workspace_id=NEW.workspace_id AND r.request_id=NEW.request_id) THEN
      RAISE EXCEPTION 'REGISTERED_RESTORE_MODE_CONFLICT' USING ERRCODE='23514'; END IF;
    RETURN NEW;
  END IF;
  IF NEW.actor_id IS DISTINCT FROM parent.actor_id
    OR NOT public.registered_restore_actor_allowed(NEW.tenant_id,NEW.workspace_id,NEW.actor_id)
    OR EXISTS(SELECT 1 FROM public.restore_previews p WHERE p.tenant_id=NEW.tenant_id
      AND p.workspace_id=NEW.workspace_id AND p.request_id=NEW.request_id) THEN
    RAISE EXCEPTION 'REGISTERED_RESTORE_MODE_CONFLICT' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME IN ('registered_restore_target_reservations','registered_restore_previews') THEN
    IF parent.backup_id IS DISTINCT FROM NEW.backup_id OR parent.state NOT IN ('requested','preview_ready','authorized') THEN
      RAISE EXCEPTION 'REGISTERED_RESTORE_PARENT_CONFLICT' USING ERRCODE='23514'; END IF;
    SELECT * INTO backup FROM public.backup_records WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id
      AND backup_id=NEW.backup_id FOR SHARE;
    IF NOT FOUND OR backup.state<>'ready' THEN
      RAISE EXCEPTION 'REGISTERED_RESTORE_BACKUP_CONFLICT' USING ERRCODE='23514'; END IF;
    SELECT * INTO manifest FROM public.backup_manifests WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id
      AND backup_id=NEW.backup_id AND manifest_version=3 FOR SHARE;
    IF NOT FOUND OR manifest.manifest_digest IS DISTINCT FROM NEW.source_manifest_digest
      OR substring(manifest.encrypted_inventory FROM 1 FOR 4)<>decode('44524d33','hex') THEN
      RAISE EXCEPTION 'REGISTERED_RESTORE_MANIFEST_CONFLICT' USING ERRCODE='23514'; END IF;
  END IF;
  IF TG_TABLE_NAME='registered_restore_target_reservations' THEN
    -- ON CONFLICT replay도 immutable exact 값은 caller SELECT로 비교. 예약 release0.
    RETURN NEW;
  END IF;
  SELECT * INTO reservation FROM public.registered_restore_target_reservations
    WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND request_id=NEW.request_id;
  IF NOT FOUND OR (NEW.registry_generation_id,NEW.destination_id,NEW.destination_version,NEW.actor_id)
    IS DISTINCT FROM (reservation.registry_generation_id,reservation.destination_id,reservation.destination_version,reservation.actor_id) THEN
    RAISE EXCEPTION 'REGISTERED_RESTORE_RESERVATION_CONFLICT' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='registered_restore_previews' THEN
    SELECT coalesce(max(preview_version),0) INTO last_preview FROM public.registered_restore_previews
      WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND request_id=NEW.request_id;
    IF NEW.preview_version<>last_preview+1 OR NEW.expires_at<=clock_timestamp()
      OR NEW.closure_fingerprint IS DISTINCT FROM backup.request_fingerprint
      OR NEW.source_policy_version IS DISTINCT FROM backup.policy_version
      OR NEW.retention_watermark IS DISTINCT FROM backup.retention_watermark
      OR EXISTS(SELECT 1 FROM public.registered_restore_phase_receipts p WHERE p.tenant_id=NEW.tenant_id
        AND p.workspace_id=NEW.workspace_id AND p.request_id=NEW.request_id) THEN
      RAISE EXCEPTION 'REGISTERED_RESTORE_PREVIEW_CONFLICT' USING ERRCODE='23514'; END IF;
    RETURN NEW;
  END IF;
  IF parent.state NOT IN ('authorized','restoring','verifying','failed') THEN
    RAISE EXCEPTION 'REGISTERED_RESTORE_PHASE_CONFLICT' USING ERRCODE='23514'; END IF;
  IF parent.state='failed' AND NEW.outcome NOT IN ('failed','uncertain') THEN
    RAISE EXCEPTION 'REGISTERED_RESTORE_PHASE_CONFLICT' USING ERRCODE='23514'; END IF;
  SELECT * INTO selected_preview FROM public.registered_restore_previews
    WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND request_id=NEW.request_id
      AND preview_version=NEW.preview_version;
  IF NOT FOUND THEN RAISE EXCEPTION 'REGISTERED_RESTORE_PHASE_CONFLICT' USING ERRCODE='23514'; END IF;
  SELECT * INTO previous FROM public.registered_restore_phase_receipts
    WHERE tenant_id=NEW.tenant_id AND workspace_id=NEW.workspace_id AND request_id=NEW.request_id
    ORDER BY sequence DESC LIMIT 1;
  IF NOT FOUND THEN
    IF NEW.sequence<>1 OR NEW.phase<>1 OR NEW.outcome<>'intent'
      OR selected_preview.expires_at<=clock_timestamp()
      OR EXISTS(SELECT 1 FROM public.registered_restore_previews p WHERE p.tenant_id=NEW.tenant_id
        AND p.workspace_id=NEW.workspace_id AND p.request_id=NEW.request_id AND p.preview_version>NEW.preview_version) THEN
      RAISE EXCEPTION 'REGISTERED_RESTORE_PHASE_CONFLICT' USING ERRCODE='23514'; END IF;
  ELSE
    IF NEW.sequence<>previous.sequence+1
      OR (NEW.operation_id,NEW.idempotency_key,NEW.preview_version,NEW.target_digest)
        IS DISTINCT FROM (previous.operation_id,previous.idempotency_key,previous.preview_version,previous.target_digest)
      OR previous.outcome IN ('failed','uncertain')
      OR (previous.outcome='intent' AND (NEW.phase<>previous.phase OR NEW.outcome NOT IN ('succeeded','failed','uncertain')))
      OR (previous.outcome='succeeded' AND (NEW.phase<>previous.phase+1 OR NEW.outcome<>'intent')) THEN
      RAISE EXCEPTION 'REGISTERED_RESTORE_PHASE_CONFLICT' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
ALTER FUNCTION public.guard_registered_restore_insert() OWNER TO daon_notebook_delete;
REVOKE ALL ON FUNCTION public.guard_registered_restore_insert() FROM PUBLIC,daon_app;

CREATE TRIGGER registered_restore_reservations_insert BEFORE INSERT ON public.registered_restore_target_reservations
  FOR EACH ROW EXECUTE FUNCTION public.guard_registered_restore_insert();
CREATE TRIGGER registered_restore_reservations_immutable BEFORE UPDATE OR DELETE ON public.registered_restore_target_reservations
  FOR EACH ROW EXECUTE FUNCTION public.guard_registered_restore_insert();
CREATE TRIGGER registered_restore_previews_insert BEFORE INSERT ON public.registered_restore_previews
  FOR EACH ROW EXECUTE FUNCTION public.guard_registered_restore_insert();
CREATE TRIGGER registered_restore_previews_immutable BEFORE UPDATE OR DELETE ON public.registered_restore_previews
  FOR EACH ROW EXECUTE FUNCTION public.guard_registered_restore_insert();
CREATE TRIGGER registered_restore_phases_insert BEFORE INSERT ON public.registered_restore_phase_receipts
  FOR EACH ROW EXECUTE FUNCTION public.guard_registered_restore_insert();
CREATE TRIGGER registered_restore_phases_immutable BEFORE UPDATE OR DELETE ON public.registered_restore_phase_receipts
  FOR EACH ROW EXECUTE FUNCTION public.guard_registered_restore_insert();
CREATE TRIGGER registered_restore_reservations_truncate BEFORE TRUNCATE ON public.registered_restore_target_reservations
  FOR EACH STATEMENT EXECUTE FUNCTION public.guard_registered_restore_insert();
CREATE TRIGGER registered_restore_previews_truncate BEFORE TRUNCATE ON public.registered_restore_previews
  FOR EACH STATEMENT EXECUTE FUNCTION public.guard_registered_restore_insert();
CREATE TRIGGER registered_restore_phases_truncate BEFORE TRUNCATE ON public.registered_restore_phase_receipts
  FOR EACH STATEMENT EXECUTE FUNCTION public.guard_registered_restore_insert();
CREATE TRIGGER registered_restore_fixture_mode_guard BEFORE INSERT ON public.restore_previews
  FOR EACH ROW EXECUTE FUNCTION public.guard_registered_restore_insert();

REVOKE ALL ON public.registered_restore_target_reservations,public.registered_restore_previews,
  public.registered_restore_phase_receipts FROM PUBLIC,daon_app;
-- 열 권한만 부여. 시각은 DB default, UPDATE/DELETE/TRUNCATE/REFERENCES/owner membership0.
GRANT SELECT(registry_generation_id,destination_id,tenant_id,workspace_id,request_id,actor_id,destination_version,
  backup_id,manifest_version,source_manifest_digest,physical_receipt_digest,target_database_fingerprint,
  target_bucket_fingerprint,owner_operation_id,reserved_at),
  INSERT(registry_generation_id,destination_id,tenant_id,workspace_id,request_id,actor_id,destination_version,
  backup_id,manifest_version,source_manifest_digest,physical_receipt_digest,target_database_fingerprint,
  target_bucket_fingerprint,owner_operation_id)
  ON public.registered_restore_target_reservations TO daon_app;
GRANT SELECT(tenant_id,workspace_id,request_id,preview_version,actor_id,registry_generation_id,destination_id,
  destination_version,backup_id,manifest_version,source_manifest_digest,closure_fingerprint,included_roots_fingerprint,
  excluded_roots_fingerprint,source_policy_version,retention_watermark,expires_at,encrypted_preview_receipt,created_at),
  INSERT(tenant_id,workspace_id,request_id,preview_version,actor_id,registry_generation_id,destination_id,
  destination_version,backup_id,manifest_version,source_manifest_digest,closure_fingerprint,included_roots_fingerprint,
  excluded_roots_fingerprint,source_policy_version,retention_watermark,expires_at,encrypted_preview_receipt)
  ON public.registered_restore_previews TO daon_app;
GRANT SELECT(tenant_id,workspace_id,request_id,sequence,actor_id,registry_generation_id,destination_id,destination_version,
  preview_version,operation_id,idempotency_key,phase,outcome,target_digest,result_digest,trace_id,created_at),
  INSERT(tenant_id,workspace_id,request_id,sequence,actor_id,registry_generation_id,destination_id,destination_version,
  preview_version,operation_id,idempotency_key,phase,outcome,target_digest,result_digest,trace_id)
  ON public.registered_restore_phase_receipts TO daon_app;
