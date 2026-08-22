from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "src/daon_user_api/retention_request_postgres.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "migrations/versions/0025_source_immediate_deletion.py").read_text(encoding="utf-8")


def test_source_confirmation_uses_immediate_terminal_state_and_object_cleanup():
    create = SERVICE[SERVICE.index("    def create_request"):]
    assert 'SELECT object_key FROM delete_source_scope' in create
    assert 'self._object_storage.delete(key)' in create
    assert '"purged"' in create
    assert "timedelta(days=30)" not in create


def test_source_cleanup_scope_removes_binding_index_and_retains_shared_objects():
    assert "CREATE OR REPLACE FUNCTION delete_source_scope" in MIGRATION
    assert "DELETE FROM notebook_bindings" in MIGRATION
    assert "DELETE FROM index_versions" in MIGRATION
    assert "DELETE FROM source_versions" in MIGRATION
    assert "DELETE FROM sources" in MIGRATION
    assert "DELETE FROM object_records" in MIGRATION
    assert "NOT EXISTS (SELECT 1 FROM source_versions" in MIGRATION
    assert "sv.source_id <> p_source_id" in MIGRATION
    assert "iv.object_id=o.object_id" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION delete_source_scope" in MIGRATION
