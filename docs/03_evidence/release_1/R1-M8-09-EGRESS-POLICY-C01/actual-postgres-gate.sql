\set ON_ERROR_STOP on

DO $gate$
DECLARE
  count_value integer;
  text_value text;
BEGIN
  SELECT count(*) INTO count_value FROM egress_policy_versions;
  IF count_value <> 3 THEN
    RAISE EXCEPTION 'BACKFILL_VERSION_COUNT:%', count_value;
  END IF;
  SELECT count(*) INTO count_value FROM egress_policy_bindings WHERE active AND current;
  IF count_value <> 3 THEN
    RAISE EXCEPTION 'BACKFILL_BINDING_COUNT:%', count_value;
  END IF;
  SELECT count(*) INTO count_value
  FROM egress_policy_versions
  WHERE canonical_text::jsonb = canonical_json
    AND encode(sha256(convert_to(canonical_text, 'UTF8')), 'hex') = digest_sha256
    AND canonical_json->>'mode' = 'deny_external';
  IF count_value <> 3 THEN
    RAISE EXCEPTION 'CANON_DIGEST_OR_MODE_COUNT:%', count_value;
  END IF;
  SELECT count(*) INTO count_value
  FROM egress_policy_versions
  WHERE policy_version_id IN (
    'egress-backfill-policy:' || md5('egress_it_org:organization'),
    'egress-backfill-policy:' || md5('egress_it_org:egress_it_ws_a'),
    'egress-backfill-policy:' || md5('egress_it_org:egress_it_ws_b')
  );
  IF count_value <> 3 THEN
    RAISE EXCEPTION 'DETERMINISTIC_BACKFILL_ID_COUNT:%', count_value;
  END IF;
  SELECT count(*) INTO count_value
  FROM pg_class
  WHERE relname IN ('egress_policy_versions','egress_policy_bindings')
    AND relrowsecurity AND relforcerowsecurity;
  IF count_value <> 2 THEN
    RAISE EXCEPTION 'RLS_FORCE_COUNT:%', count_value;
  END IF;

  BEGIN
    UPDATE egress_policy_versions SET state='superseded'
    WHERE policy_version_id='egress-backfill-policy:' || md5('egress_it_org:organization');
    RAISE EXCEPTION 'IMMUTABLE_UPDATE_ACCEPTED';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    INSERT INTO egress_policy_versions (
      tenant_id,organization_id,workspace_id,policy_version_id,scope_type,
      policy_version,state,canonical_json,canonical_text,digest_sha256,created_by,trace_id
    ) SELECT tenant_id,organization_id,workspace_id,'invalid-digest','workspace',
      2,'active',canonical_json,canonical_text,repeat('0',64),'gate','gate'
      FROM egress_policy_versions WHERE workspace_id='egress_it_ws_a' LIMIT 1;
    RAISE EXCEPTION 'INVALID_DIGEST_ACCEPTED';
  EXCEPTION WHEN SQLSTATE '22023' THEN NULL;
  END;

  BEGIN
    INSERT INTO egress_policy_bindings (
      tenant_id,organization_id,workspace_id,binding_id,scope_type,policy_version_id,
      binding_version,active,current,created_by,trace_id
    ) VALUES (
      'egress_it_org','egress_it_org','egress_it_ws_b','scope-mismatch','workspace',
      'egress-backfill-policy:' || md5('egress_it_org:egress_it_ws_a'),
      2,true,true,'gate','gate'
    );
    RAISE EXCEPTION 'SCOPE_MISMATCH_ACCEPTED';
  EXCEPTION WHEN SQLSTATE '23514' THEN NULL;
  END;

  INSERT INTO egress_policy_versions (
    tenant_id,organization_id,workspace_id,policy_version_id,scope_type,
    policy_version,state,canonical_json,canonical_text,digest_sha256,created_by,trace_id
  ) SELECT tenant_id,organization_id,workspace_id,'egress-it-org-v2','organization',
    2,'active',canonical_json,canonical_text,digest_sha256,'gate','gate'
    FROM egress_policy_versions WHERE workspace_id IS NULL LIMIT 1;
  BEGIN
    INSERT INTO egress_policy_bindings (
      tenant_id,organization_id,workspace_id,binding_id,scope_type,policy_version_id,
      binding_version,active,current,created_by,trace_id
    ) VALUES (
      'egress_it_org','egress_it_org',NULL,'egress-it-org-binding-v2','organization',
      'egress-it-org-v2',2,true,true,'gate','gate'
    );
    RAISE EXCEPTION 'SECOND_CURRENT_BINDING_ACCEPTED';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
END
$gate$;

BEGIN;
SET LOCAL ROLE daon_app;
SELECT set_config('app.tenant_id','egress_it_org',true);
SELECT set_config('app.workspace_id','egress_it_ws_a',true);
DO $rls$
DECLARE count_value integer;
BEGIN
  SELECT count(*) INTO count_value FROM egress_policy_versions;
  IF count_value <> 3 THEN
    RAISE EXCEPTION 'RLS_EXPECTED_ORG_PLUS_WORKSPACE_AND_GATE_VERSION:%', count_value;
  END IF;
END
$rls$;
ROLLBACK;

BEGIN;
SET LOCAL ROLE daon_app;
SELECT set_config('app.tenant_id','wrong_tenant',true);
SELECT set_config('app.workspace_id','egress_it_ws_a',true);
DO $rls$
DECLARE count_value integer;
BEGIN
  SELECT count(*) INTO count_value FROM egress_policy_versions;
  IF count_value <> 0 THEN
    RAISE EXCEPTION 'RLS_CROSS_TENANT_VISIBLE:%', count_value;
  END IF;
END
$rls$;
ROLLBACK;

SELECT 'ACTUAL_POSTGRES_SCHEMA_GATE_PASS' AS result;
