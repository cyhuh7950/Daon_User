from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


MIGRATION = (
    Path(__file__).parents[1]
    / "migrations/versions/0045_question_fk_permissions.py"
)
EXPECTED_TABLES = (
    "egress_decisions",
    "generation_settings_snapshots",
    "model_artifacts",
    "model_deployments",
    "model_installations",
    "provider_profiles",
    "routing_decisions",
    "routing_policy_versions",
    "run_snapshots",
    "runs",
    "runtime_nodes",
    "scope_snapshots",
)


class RecordingOperations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "question_fk_permissions_0045",
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


def test_migration_0045_grants_only_question_fk_parent_update_privileges() -> None:
    source = MIGRATION.read_text("utf-8")
    sql = migration_sql()

    assert 'revision = "0045"' in source
    assert 'down_revision = "0044"' in source
    for table in EXPECTED_TABLES:
        assert f"GRANT UPDATE ON {table} TO daon_app" in sql
    assert "GRANT UPDATE ON workspaces" not in sql
    assert "ALTER ROLE daon_app BYPASSRLS" not in sql
    assert "CREATE POLICY" not in sql
    assert "SECURITY DEFINER" not in sql


def test_migration_0045_fails_closed_on_owner_rls_and_immutability_guards() -> None:
    source = MIGRATION.read_text("utf-8")
    sql = migration_sql()

    assert "QUESTION_FK_PRIVILEGE_MIGRATION_ROLE_REQUIRED" in sql
    assert "QUESTION_FK_PRIVILEGE_RLS_PRECONDITION_FAILED" in sql
    assert "QUESTION_FK_PRIVILEGE_IMMUTABILITY_PRECONDITION_FAILED" in sql
    assert "QUESTION_FK_PRIVILEGE_POSTCONDITION_FAILED" in sql
    assert "rolsuper OR rolbypassrls" in sql
    assert "rolname = 'daon_app'" in sql
    assert "NOT rolbypassrls" in sql
    assert "relrowsecurity" in sql
    assert "relforcerowsecurity" in sql
    assert "v_owner <> 'daon_app'" in sql
    assert "reject_canon_immutable_mutation" in sql
    assert "guard_canon_state_mutation" in sql
    assert "QUESTION_FK_PRIVILEGE_DOWNGRADE_BLOCKED" in source


def test_migration_0045_is_forward_only() -> None:
    module = load_migration()
    with pytest.raises(
        RuntimeError,
        match="^QUESTION_FK_PRIVILEGE_DOWNGRADE_BLOCKED$",
    ):
        module.downgrade()
