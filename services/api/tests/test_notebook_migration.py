from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/versions/0020_notebook_home.py"


def test_migration_0020_notebook_home_is_append_only_scoped_and_downgrade_safe() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0020"' in source
    assert 'down_revision = "0019"' in source
    for table in ["notebooks", "notebook_metadata_versions", "notebook_bindings", "notebook_activities", "notebook_idempotency"]:
        assert f"CREATE TABLE {table}" in source
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in source
    assert "notebook_metadata_one_current" in source
    assert "'created','title_updated','context_bound'" in source
    assert "reject_notebook_immutable_mutation" in source
    assert "NOTEBOOK_DOWNGRADE_BLOCKED" in source
    assert "current_setting('app.tenant_id',true)" in source
    assert "current_setting('app.workspace_id',true)" in source
    assert "REVOKE UPDATE,DELETE" in source
