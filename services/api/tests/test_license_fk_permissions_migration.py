from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


MIGRATION = (
    Path(__file__).parents[1]
    / "migrations/versions/0042_license_fk_permissions.py"
)


class RecordingOperations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "license_fk_permissions_0042",
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


def test_migration_0042_restores_only_license_fk_lock_privilege() -> None:
    source = MIGRATION.read_text("utf-8")
    sql = migration_sql()

    assert 'revision = "0042"' in source
    assert 'down_revision = "0041"' in source
    assert "GRANT UPDATE ON organization_license_versions TO daon_app" in sql
    assert "GRANT UPDATE ON license_apply_idempotency" not in sql
    assert "GRANT BYPASSRLS" not in sql
    assert "ALTER ROLE daon_app BYPASSRLS" not in sql
    assert "NO FORCE ROW LEVEL SECURITY" not in sql
    assert "DISABLE ROW LEVEL SECURITY" not in sql


def test_migration_0042_fails_closed_without_license_guards() -> None:
    sql = migration_sql()

    assert "LICENSE_FK_PERMISSION_MIGRATION_ROLE_REQUIRED" in sql
    assert "LICENSE_FK_PERMISSION_RLS_PRECONDITION_FAILED" in sql
    assert "LICENSE_FK_PERMISSION_IMMUTABILITY_PRECONDITION_FAILED" in sql
    assert "rolbypassrls" in sql
    assert "relrowsecurity" in sql
    assert "relforcerowsecurity" in sql
    assert "organization_license_versions_immutable" in sql
    assert "reject_license_mutation" in sql


def test_migration_0042_verifies_postcondition_and_is_forward_only() -> None:
    module = load_migration()
    sql = migration_sql()

    assert "LICENSE_FK_PERMISSION_POSTCONDITION_FAILED" in sql
    assert "has_table_privilege" in sql
    assert "'daon_app'" in sql
    with pytest.raises(
        RuntimeError,
        match="^LICENSE_FK_PERMISSION_DOWNGRADE_BLOCKED$",
    ):
        module.downgrade()
