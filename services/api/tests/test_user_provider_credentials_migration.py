from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


MIGRATION = Path(__file__).parents[1] / "migrations/versions/0047_user_provider_credentials.py"
PERSONAL_V2_MIGRATION = Path(__file__).parents[1] / "migrations/versions/0053_personal_credential_schema_v2.py"


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


def load_personal_v2_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("personal_credential_schema_v2_0053", PERSONAL_V2_MIGRATION)
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


def test_0053_additively_allows_personal_schema_v2_idempotently() -> None:
    module = load_personal_v2_migration()
    operations = RecordingOperations()

    with patch.object(module, "op", operations):
        module.upgrade()

    sql = " ".join("\n".join(operations.statements).lower().split())
    assert module.revision == "0053"
    assert module.down_revision == "0052"
    assert "pg_constraint" in sql and "personal_credential_schema_v2_check" in sql
    assert "check (credential_schema_version in (1, 2))" in sql
    assert "drop constraint if exists user_provider_credentials_credential_schema_version_check;" in sql
    assert "update user_provider_credentials" not in sql
    assert "delete from user_provider_credentials" not in sql
    assert len(operations.statements) == 1


def test_0053_downgrade_refuses_v2_rows_before_changing_constraint() -> None:
    module = load_personal_v2_migration()
    operations = RecordingOperations()

    with patch.object(module, "op", operations):
        module.downgrade()

    sql = " ".join("\n".join(operations.statements).lower().split())
    assert "credential_schema_version = 2" in sql
    assert "raise exception 'c8_personal_credential_v2_downgrade_blocked'" in sql
    guard = sql.index("credential_schema_version = 2")
    refusal = sql.index("raise exception 'c8_personal_credential_v2_downgrade_blocked'")
    restore = sql.index("add constraint user_provider_credentials_credential_schema_version_check ")
    assert guard < refusal < restore
    assert "drop constraint if exists user_provider_credentials_personal_credential_schema_v2_check" in sql
    assert len(operations.statements) == 1
