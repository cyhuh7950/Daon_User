from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


MIGRATION = (
    Path(__file__).parents[1]
    / "migrations/versions/0040_system_provider_connections.py"
)


class RecordingOperations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "system_provider_connections_0040",
        MIGRATION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migration_sql() -> str:
    module = load_migration()
    operations = RecordingOperations()
    with patch.object(module, "op", operations):
        module.upgrade()
    return "\n".join(operations.statements)


def test_migration_0040_creates_named_connection_catalog_and_workspace_defaults() -> None:
    sql = migration_sql()

    assert "CREATE TABLE system_provider_connections" in sql
    assert "connection_id text PRIMARY KEY" in sql
    assert "UNIQUE (provider_code)" not in sql
    assert "encrypted_credential bytea" in sql
    assert "credential_nonce bytea" in sql
    assert "credential_schema_version integer" in sql
    assert "CREATE TABLE system_provider_models" in sql
    assert "PRIMARY KEY (connection_id, model_id)" in sql
    assert "CREATE TABLE workspace_model_defaults" in sql
    assert "PRIMARY KEY (tenant_id, workspace_id, capability)" in sql


def test_migration_0040_allows_nullable_credentials_but_requires_complete_envelope() -> None:
    sql = migration_sql()

    assert "encrypted_credential IS NULL" in sql
    assert "credential_nonce IS NULL" in sql
    assert "encryption_key_version IS NULL" in sql
    assert "credential_schema_version IS NULL" in sql
    assert "credential_version = 0" in sql
    assert "credential_schema_version = 1" in sql
    assert "octet_length(credential_nonce) = 12" in sql


def test_migration_0040_maps_active_legacy_defaults_without_deleting_history() -> None:
    sql = migration_sql()

    assert "FROM provider_setting_role_bindings" in sql
    assert "JOIN provider_setting_deployments" in sql
    assert "JOIN provider_setting_profiles" in sql
    assert "'text' THEN 'text_generation'" in sql
    assert "'vision' THEN 'image_understanding'" in sql
    assert "'document_parser' THEN 'document_parsing'" in sql
    assert "PROVIDER_DEFAULT_MIGRATION_UNSUPPORTED_ROLE" in sql
    assert "PROVIDER_DEFAULT_MIGRATION_COUNT_MISMATCH" in sql
    upgrade_sql = MIGRATION.read_text("utf-8").split("def downgrade", 1)[0]
    assert "DELETE FROM provider_setting_" not in upgrade_sql
    assert "DROP TABLE provider_setting_" not in upgrade_sql


def test_migration_0040_preserves_system_scope_and_workspace_rls_permissions() -> None:
    sql = migration_sql()

    assert "ALTER TABLE system_provider_connections OWNER TO daon_app" in sql
    assert "ALTER TABLE system_provider_models OWNER TO daon_app" in sql
    assert "ALTER TABLE workspace_model_defaults OWNER TO daon_app" in sql
    assert "ALTER TABLE workspace_model_defaults ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE workspace_model_defaults FORCE ROW LEVEL SECURITY" in sql
    assert "current_setting('app.tenant_id', true)" in sql
    assert "current_setting('app.workspace_id', true)" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON system_provider_connections" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON system_provider_models" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_model_defaults" in sql
    assert "system_provider_connections ENABLE ROW LEVEL SECURITY" not in sql
    assert "system_provider_models ENABLE ROW LEVEL SECURITY" not in sql


def test_migration_0040_is_forward_only() -> None:
    module = load_migration()

    with pytest.raises(RuntimeError, match="^PROVIDER_CONNECTIONS_DOWNGRADE_BLOCKED$"):
        module.downgrade()
