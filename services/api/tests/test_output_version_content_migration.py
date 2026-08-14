from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services/api/migrations/versions/0016_output_version_content_lineage.py"


def test_migration_0016_separates_content_lineage_from_state_version() -> None:
    sql = MIGRATION.read_text("utf-8")

    assert 'revision = "0016"' in sql
    assert 'down_revision = "0015"' in sql
    assert "ADD COLUMN content_version bigint" in sql
    assert "output_versions_content_version_unique" in sql
    assert "previous_row.content_version <> NEW.content_version - 1" in sql
    assert "OUTPUT_VERSION_DOWNGRADE_BLOCKED" in sql
    assert "ERRCODE = '55000'" in sql


def test_repository_uses_content_version_for_lineage_and_latest_projection() -> None:
    source = (
        ROOT / "services/api/src/daon_user_api/studio_workspace_postgres.py"
    ).read_text("utf-8")

    assert "ORDER BY content_version DESC" in source
    assert "SELECT aggregate_id,content_version" in source
    assert "content_version" in source
