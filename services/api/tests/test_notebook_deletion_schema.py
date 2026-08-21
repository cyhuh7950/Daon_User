from pathlib import Path


def test_notebook_deletion_migration_is_scoped_and_immutable():
    migration = Path(__file__).parents[1] / "migrations/versions/0023_notebook_deletion.py"
    text = migration.read_text(encoding="utf-8")
    assert "notebook_deletion_requests" in text
    assert "state IN ('accepted','deleting','completed','failed')" in text
    assert "UNIQUE (tenant_id,workspace_id,actor_id,idempotency_key)" in text
    assert "ROW LEVEL SECURITY" in text
    assert "GRANT SELECT,INSERT,UPDATE" in text
    assert "DELETE ON notebooks" not in text
