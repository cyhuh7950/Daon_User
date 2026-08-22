from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services/api/migrations/versions/0013_studio_workspace_default_policy.py"


def migration_sql() -> str:
    return MIGRATION.read_text("utf-8")


def test_migration_0013_declares_deterministic_six_record_default_contract() -> None:
    sql = migration_sql()

    assert 'revision = "0013"' in sql
    assert 'down_revision = "0012"' in sql
    assert "CREATE FUNCTION ensure_studio_workspace_defaults" in sql
    assert "studio-default:workspace-policy:" in sql
    assert "studio-default:knowledge-scope:" in sql
    assert "studio-default:weight-profile:" in sql
    assert "studio-default:ruleset-reference:" in sql
    assert "studio-default:ruleset-snapshot:" in sql
    assert "studio-default:ruleset-binding:" in sql
    for table in (
        "workspace_policies",
        "knowledge_scopes",
        "weight_profiles",
        "ruleset_references",
        "ruleset_version_snapshots",
        "ruleset_bindings",
    ):
        assert f"INSERT INTO {table}" in sql
    assert "sha256(convert_to" in sql
    assert "migration:0013" in sql
    assert "ON CONFLICT DO NOTHING" in sql


def test_migration_0013_backfills_and_provisions_new_workspaces_without_replacing_valid_rows() -> None:
    sql = migration_sql()

    assert "FROM workspaces" in sql
    assert "PERFORM ensure_studio_workspace_defaults" in sql
    assert "AFTER INSERT ON workspaces" in sql
    assert "CREATE TRIGGER studio_workspace_defaults_after_insert" in sql
    assert "ORDER BY version DESC, created_at DESC" in sql
    assert "canonical_json->>'active' = 'true'" in sql
    assert "canonical_json->>'current' = 'true'" in sql
    assert "canonical_json->>'workspace_id' = p_workspace_id" in sql
    assert "v_complete_ruleset" in sql
    assert "IF NOT v_complete_ruleset THEN" in sql
    assert "DELETE FROM workspace_policies" not in sql.split("def downgrade", 1)[0]


def test_migration_0013_fails_closed_on_deterministic_id_conflict_and_postcondition() -> None:
    sql = migration_sql()

    assert "STUDIO_DEFAULT_POLICY_ID_CONFLICT" in sql
    assert "STUDIO_DEFAULT_POLICY_POSTCONDITION_FAILED" in sql
    assert "ERRCODE = '55000'" in sql
    assert "created_by = 'migration:0013'" in sql


def test_migration_0013_rejects_ambiguous_or_invalid_latest_policy_history() -> None:
    sql = migration_sql()

    assert "STUDIO_DEFAULT_POLICY_HISTORY_AMBIGUOUS" in sql
    assert "STUDIO_DEFAULT_POLICY_LATEST_INVALID" in sql
    assert "max(version)" in sql
    assert "v_latest_count" in sql


def test_migration_0013_promotes_only_exact_question_legacy_scope_append_only() -> None:
    sql = migration_sql()

    assert "jsonb_object_keys(v_latest_json)" in sql
    assert "ARRAY['mode','source_version_ids']" in sql
    assert "v_latest_json->>'mode' = 'single_source'" in sql
    assert "jsonb_array_length(v_latest_json->'source_version_ids') = 1" in sql
    assert "'scope-' || substr(encode(sha256(convert_to(v_legacy_source_version_id,'UTF8')),'hex'),1,32)" in sql
    assert "FROM source_versions" in sql
    assert "studio-compat:knowledge-scope-v2:" in sql
    assert "previous_version_id" in sql
    assert "v_legacy_scope_id" in sql
    assert '"mode":"single_source"' in sql
    assert '"version":2' in sql
    assert "knowledge_scope_id = v_selected_scope_id" in sql


def test_migration_0013_downgrade_is_owned_only_reverse_fk_and_fail_closed() -> None:
    sql = migration_sql()

    assert "STUDIO_DEFAULT_POLICY_ROLLBACK_BLOCKED" in sql
    assert "created_by = 'migration:0013'" in sql
    assert "child.previous_version_id" in sql
    assert "DISABLE TRIGGER workspace_policies_immutable" in sql
    assert "ENABLE TRIGGER workspace_policies_immutable" in sql
    delete_order = [
        sql.index("DELETE FROM ruleset_bindings"),
        sql.index("DELETE FROM ruleset_version_snapshots"),
        sql.index("DELETE FROM ruleset_references"),
        sql.index("DELETE FROM weight_profiles"),
        sql.index("DELETE FROM knowledge_scopes"),
        sql.index("DELETE FROM workspace_policies"),
    ]
    assert delete_order == sorted(delete_order)
    assert "DROP TRIGGER IF EXISTS studio_workspace_defaults_after_insert ON workspaces" in sql
    assert "DROP FUNCTION IF EXISTS ensure_studio_workspace_defaults(text, text)" in sql


def test_migration_0013_keeps_egress_and_existing_lineage_unchanged() -> None:
    upgrade_sql = migration_sql().split("def downgrade", 1)[0]

    assert "UPDATE egress_" not in upgrade_sql
    assert "UPDATE runs" not in upgrade_sql
    assert "UPDATE source" not in upgrade_sql
