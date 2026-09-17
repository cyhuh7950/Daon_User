from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


MIGRATION = (
    Path(__file__).parents[1]
    / "migrations/versions/0041_studio_workspace_fk_permissions.py"
)


class RecordingOperations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "studio_workspace_fk_permissions_0041",
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


def test_migration_0041_restores_only_fk_lock_privileges_for_studio_canon() -> None:
    sql = migration_sql()

    assert 'revision = "0041"' in MIGRATION.read_text("utf-8")
    assert 'down_revision = "0040"' in MIGRATION.read_text("utf-8")
    assert "GRANT UPDATE ON" in sql
    for table in (
        "workspace_policies",
        "knowledge_scopes",
        "weight_profiles",
        "ruleset_references",
        "ruleset_version_snapshots",
        "ruleset_bindings",
    ):
        assert table in sql
    assert "TO daon_app" in sql
    assert "GRANT BYPASSRLS" not in sql
    assert "ALTER ROLE daon_app BYPASSRLS" not in sql
    assert "NO FORCE ROW LEVEL SECURITY" not in sql
    assert "DISABLE ROW LEVEL SECURITY" not in sql


def test_migration_0041_fails_closed_without_forced_rls_and_immutable_guards() -> None:
    sql = migration_sql()

    assert "STUDIO_FK_PERMISSION_RLS_PRECONDITION_FAILED" in sql
    assert "STUDIO_FK_PERMISSION_IMMUTABILITY_PRECONDITION_FAILED" in sql
    assert "relrowsecurity" in sql
    assert "relforcerowsecurity" in sql
    assert "reject_canon_immutable_mutation" in sql
    assert "_immutable" in sql


def test_migration_0041_verifies_postcondition_and_is_forward_only() -> None:
    module = load_migration()
    sql = migration_sql()

    assert "STUDIO_FK_PERMISSION_POSTCONDITION_FAILED" in sql
    assert "has_table_privilege" in sql
    assert "'daon_app'" in sql
    with pytest.raises(
        RuntimeError,
        match="^STUDIO_FK_PERMISSION_DOWNGRADE_BLOCKED$",
    ):
        module.downgrade()
