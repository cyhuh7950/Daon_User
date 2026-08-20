from __future__ import annotations

from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/versions/0018_screen_preferences.py"


def test_migration_0018_persists_only_user_screen_preferences_with_force_rls() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0018"' in source
    assert 'down_revision = "0017"' in source
    assert "CREATE TABLE user_screen_preferences" in source
    assert "theme text NOT NULL CHECK (theme IN ('system','light','dark'))" in source
    assert "PRIMARY KEY (tenant_id,actor_id)" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "current_setting('app.tenant_id',true)" in source
    assert "current_setting('app.actor_id',true)" in source
    assert "GRANT SELECT,INSERT,UPDATE ON user_screen_preferences TO daon_app" in source
    assert "DROP TABLE user_screen_preferences" in source
