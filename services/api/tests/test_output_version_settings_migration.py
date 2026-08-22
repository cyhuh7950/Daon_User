from __future__ import annotations

from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/versions/0017_workspace_output_version_settings.py"


def test_migration_0017_adds_scoped_settings_and_immutable_idempotency() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0017"' in source
    assert 'down_revision = "0016"' in source
    assert "CREATE TABLE workspace_output_version_settings" in source
    assert "version_save_mode='append_only'" in source
    assert "CREATE TABLE workspace_output_version_settings_idempotency" in source
    assert "workspace_output_version_settings_idempotency_immutable" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert 'permissions = "SELECT,INSERT" if table.endswith("_idempotency")' in source
