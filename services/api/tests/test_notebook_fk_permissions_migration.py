from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


MIGRATION = (
    Path(__file__).parents[1]
    / "migrations/versions/0043_notebook_fk_permissions.py"
)


class RecordingOperations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "notebook_fk_permissions_0043",
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


def test_migration_0043_restores_only_notebook_fk_lock_privilege() -> None:
    source = MIGRATION.read_text("utf-8")
    sql = migration_sql()

    assert 'revision = "0043"' in source
    assert 'down_revision = "0042"' in source
    assert "GRANT UPDATE ON notebooks TO daon_app" in sql
    for table in (
        "notebook_metadata_versions",
        "notebook_bindings",
        "notebook_activities",
        "notebook_idempotency",
        "notebook_source_unbindings",
    ):
        assert f"GRANT UPDATE ON {table}" not in sql
    assert "CREATE POLICY" not in sql
    assert "ALTER POLICY" not in sql
    assert "ALTER TABLE" not in sql
    assert "ALTER FUNCTION" not in sql
    assert "SECURITY DEFINER" not in sql
    assert "GRANT BYPASSRLS" not in sql
    assert "ALTER ROLE daon_app BYPASSRLS" not in sql


def test_migration_0043_fails_closed_without_notebook_guards() -> None:
    sql = migration_sql()

    assert "NOTEBOOK_FK_PERMISSION_MIGRATION_ROLE_REQUIRED" in sql
    assert "NOTEBOOK_FK_PERMISSION_RLS_PRECONDITION_FAILED" in sql
    assert "NOTEBOOK_FK_PERMISSION_IMMUTABILITY_PRECONDITION_FAILED" in sql
    assert "rolsuper OR rolbypassrls" in sql
    assert "rolname = 'daon_app'" in sql
    assert "NOT rolbypassrls" in sql
    assert "relrowsecurity" in sql
    assert "relforcerowsecurity" in sql
    assert "v_owner <> 'daon_app'" in sql
    assert "notebooks_immutable" in sql
    assert "reject_notebook_immutable_mutation" in sql
    assert "trigger.tgenabled IN ('O', 'A')" in sql


def test_migration_0043_verifies_postcondition_and_is_forward_only() -> None:
    module = load_migration()
    sql = migration_sql()

    assert "NOTEBOOK_FK_PERMISSION_POSTCONDITION_FAILED" in sql
    assert "has_table_privilege" in sql
    assert "'daon_app'" in sql
    assert "'public.notebooks'" in sql
    with pytest.raises(
        RuntimeError,
        match="^NOTEBOOK_FK_PERMISSION_DOWNGRADE_BLOCKED$",
    ):
        module.downgrade()
