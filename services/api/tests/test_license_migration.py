from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/versions/0019_product_license.py"


def test_migration_0019_is_append_only_org_scope_and_force_rls() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0019"' in source
    assert 'down_revision = "0018"' in source
    assert "CREATE TABLE organization_license_versions" in source
    assert "CREATE TABLE license_apply_idempotency" in source
    assert "license_document" not in source
    assert "signature text" not in source
    assert "claims_digest text NOT NULL" in source
    assert "resource_limits jsonb NOT NULL" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "current_setting('app.tenant_id', true)" in source
    assert "reject_license_mutation" in source
    assert "REVOKE UPDATE, DELETE" in source
    assert "LICENSE_DOWNGRADE_BLOCKED" in source
