from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


MIGRATION = (
    Path(__file__).parents[1]
    / "migrations/versions/0044_egress_policy_seed.py"
)


class RecordingOperations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "egress_policy_seed_0044",
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


def test_migration_0044_restores_only_egress_version_fk_lock_privilege() -> None:
    source = MIGRATION.read_text("utf-8")
    sql = migration_sql()

    assert 'revision = "0044"' in source
    assert 'down_revision = "0043"' in source
    assert "GRANT UPDATE ON egress_policy_versions TO daon_app" in sql
    assert "GRANT UPDATE ON egress_policy_bindings" not in sql
    assert "CREATE POLICY" not in sql
    assert "ALTER POLICY" not in sql
    assert "ALTER ROLE daon_app BYPASSRLS" not in sql
    assert "SECURITY DEFINER" not in sql


def test_migration_0044_fails_closed_without_egress_guards() -> None:
    sql = migration_sql()

    assert "EGRESS_POLICY_SEED_MIGRATION_ROLE_REQUIRED" in sql
    assert "EGRESS_POLICY_SEED_RLS_PRECONDITION_FAILED" in sql
    assert "EGRESS_POLICY_SEED_IMMUTABILITY_PRECONDITION_FAILED" in sql
    assert "rolsuper OR rolbypassrls" in sql
    assert "rolname = 'daon_app'" in sql
    assert "NOT rolbypassrls" in sql
    assert "relrowsecurity" in sql
    assert "relforcerowsecurity" in sql
    assert "v_owner <> 'daon_app'" in sql
    assert "egress_policy_versions_immutable" in sql
    assert "reject_egress_policy_mutation" in sql
    assert "trigger.tgenabled IN ('O', 'A')" in sql


def test_migration_0044_backfills_only_scopes_without_current_policy() -> None:
    module = load_migration()
    sql = migration_sql()
    compact_sql = " ".join(sql.split())

    assert module.DEFAULT_DENY_CANONICAL_TEXT == (
        '{"allowed_destinations":[],"allowed_provider_kinds":[],'
        '"classification":"restricted","masking_required":true,"max_bytes":0,'
        '"mode":"deny_external","redaction_required":true,'
        '"required_approver":"organization_admin"}'
    )
    assert module.DEFAULT_DENY_DIGEST == (
        "caf695f3de7e3e05feb024b3ff4b8b14cbfad5318b885ac15d8e4da25b819d7f"
    )
    assert "'egress-backfill-policy:' || md5(tenant_id || ':organization')" in compact_sql
    assert "'egress-backfill-binding:' || md5(tenant_id || ':organization')" in compact_sql
    assert "'egress-backfill-policy:' || md5(tenant_id || ':' || workspace_id)" in compact_sql
    assert "'egress-backfill-binding:' || md5(tenant_id || ':' || workspace_id)" in compact_sql
    assert sql.count("NOT EXISTS") >= 6
    assert "binding.current" in sql
    assert "EGRESS_POLICY_SEED_BACKFILL_POSTCONDITION_FAILED" in sql


def test_migration_0044_verifies_privilege_and_is_forward_only() -> None:
    module = load_migration()
    sql = migration_sql()

    assert "EGRESS_POLICY_SEED_PRIVILEGE_POSTCONDITION_FAILED" in sql
    assert "has_table_privilege" in sql
    assert "'public.egress_policy_versions'" in sql
    with pytest.raises(
        RuntimeError,
        match="^EGRESS_POLICY_SEED_DOWNGRADE_BLOCKED$",
    ):
        module.downgrade()
