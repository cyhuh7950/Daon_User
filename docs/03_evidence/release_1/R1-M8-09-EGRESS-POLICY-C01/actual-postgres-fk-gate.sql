\set ON_ERROR_STOP on

INSERT INTO tenants(tenant_id,display_name)
VALUES ('fk_gate_org','FK Gate Org');

DO $gate$
BEGIN
  BEGIN
    INSERT INTO egress_policy_versions(
      tenant_id,organization_id,workspace_id,policy_version_id,scope_type,
      policy_version,state,canonical_json,canonical_text,digest_sha256,created_by,trace_id
    ) VALUES (
      'fk_gate_org','fk_gate_org','missing_workspace','fk-gate-policy','workspace',
      1,'active',
      '{"allowed_destinations":[],"allowed_provider_kinds":[],"classification":"restricted","masking_required":true,"max_bytes":0,"mode":"deny_external","redaction_required":true,"required_approver":"organization_admin"}'::jsonb,
      '{"allowed_destinations":[],"allowed_provider_kinds":[],"classification":"restricted","masking_required":true,"max_bytes":0,"mode":"deny_external","redaction_required":true,"required_approver":"organization_admin"}',
      'caf695f3de7e3e05feb024b3ff4b8b14cbfad5318b885ac15d8e4da25b819d7f',
      'gate','gate'
    );
    RAISE EXCEPTION 'FK_ACCEPTED';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'EXPLICIT_FK_REJECTION_PASS sqlstate=%',SQLSTATE;
  END;
END
$gate$;

SELECT count(*) AS invalid_fk_rows
FROM egress_policy_versions
WHERE policy_version_id='fk-gate-policy';
