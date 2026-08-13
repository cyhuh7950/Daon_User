from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services/api/migrations/versions/0012_egress_policy_version_binding.py"


def test_migration_0012_declares_versioned_scoped_immutable_policy_contract() -> None:
    sql = MIGRATION.read_text("utf-8")

    assert 'revision = "0012"' in sql
    assert 'down_revision = "0011"' in sql
    assert "CREATE TABLE egress_policy_versions" in sql
    assert "CREATE TABLE egress_policy_bindings" in sql
    assert "deny_external" in sql
    assert "allow_approved_external" in sql
    assert "organization_id" in sql
    assert "workspace_id" in sql
    assert "policy_version_id" in sql
    assert "binding_version" in sql
    assert "CREATE UNIQUE INDEX egress_policy_bindings_one_current_scope" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "reject_egress_policy_mutation" in sql
    assert "BEFORE UPDATE OR DELETE ON egress_policy_versions" in sql
    assert "BEFORE UPDATE OR DELETE ON egress_policy_bindings" in sql
    assert "validate_egress_policy_version_insert" in sql
    assert "NEW.canonical_text::jsonb <> NEW.canonical_json" in sql
    assert "sha256(convert_to(NEW.canonical_text, 'UTF8'))" in sql
    assert "CANON_DIGEST_MISMATCH" in sql


def test_migration_0012_backfill_is_deterministic_idempotent_and_denies_external() -> None:
    sql = MIGRATION.read_text("utf-8")

    assert "egress-backfill-policy:" in sql
    assert "egress-backfill-binding:" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    assert "FROM workspaces" in sql
    assert "'deny_external'" in sql
    assert "existing RunSnapshot" not in sql
    assert "UPDATE egress_decisions" not in sql
    assert "op.get_bind().exec_driver_sql(" in sql


def test_migration_0012_has_fk_scope_checks_indexes_grants_and_clean_rollback() -> None:
    sql = MIGRATION.read_text("utf-8")

    assert "REFERENCES tenants(tenant_id)" in sql
    assert "REFERENCES workspaces(tenant_id, workspace_id)" in sql
    assert "REFERENCES egress_policy_versions" in sql
    assert "CHECK (tenant_id = organization_id)" in sql
    assert "CHECK ((scope_type = 'organization'" in sql
    assert "GRANT SELECT, INSERT ON egress_policy_versions TO daon_app" in sql
    assert "REVOKE UPDATE, DELETE ON egress_policy_versions FROM daon_app" in sql
    assert "DROP TABLE IF EXISTS egress_policy_bindings" in sql
    assert "DROP TABLE IF EXISTS egress_policy_versions" in sql
