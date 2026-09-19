from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


MIGRATION = Path(__file__).parents[1] / "migrations/versions/0047_user_provider_credentials.py"


class RecordingOperations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("user_provider_credentials_0047", MIGRATION)
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


def test_0047_scopes_user_credentials_to_tenant_user_and_connection() -> None:
    sql = migration_sql()

    assert "CREATE TABLE user_provider_credentials" in sql
    assert "PRIMARY KEY (tenant_id, user_id, connection_id)" in sql
    assert "REFERENCES system_provider_connections(connection_id) ON DELETE CASCADE" in sql
    assert "encrypted_credential bytea NOT NULL" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON user_provider_credentials TO daon_app" in sql


def test_0047_registers_media_bridge_and_sentence_transformers() -> None:
    sql = migration_sql()

    assert "'MEDIA_BRIDGE'" in sql
    assert "'SENTENCE_TRANSFORMERS'" in sql


def test_0047_is_forward_only() -> None:
    module = load_migration()

    with pytest.raises(RuntimeError, match="^USER_PROVIDER_CREDENTIALS_DOWNGRADE_BLOCKED$"):
        module.downgrade()
