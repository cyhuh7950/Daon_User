"""Focused contract for the 0058 canonical-transition owner correction.

These checks inspect emitted migration SQL; live PostgreSQL behavior is tested
separately on the isolated restored database.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace


MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0058_canon_transition_owner.py"
SIGNATURE = "public.transition_canon_state(text,text,integer,text,text,text,text,text)"


def _migration_sql(action: str) -> tuple[object, str]:
    spec = importlib.util.spec_from_file_location("canon_transition_owner_0058", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements: list[str] = []
    module.op = SimpleNamespace(execute=statements.append)
    getattr(module, action)()
    assert len(statements) == 1
    return module, statements[0]


def test_0058_upgrade_changes_only_transition_function_owner_with_guards() -> None:
    migration, sql = _migration_sql("upgrade")
    assert (migration.revision, migration.down_revision) == ("0058", "0057")
    assert f"ALTER FUNCTION {SIGNATURE}\n  OWNER TO daon_notebook_delete" in sql
    assert f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC" in sql
    assert f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO daon_app" in sql
    assert "SECURITY DEFINER" not in sql  # body and security mode stay unchanged
    assert "prosecdef" in sql
    assert "relforcerowsecurity" in sql
    assert "CANON_SCOPE_DENIED" in sql
    assert "has_function_privilege('daon_app'" in sql
    assert "has_table_privilege('daon_app'" in sql
    assert "'UPDATE'" in sql and "'DELETE'" in sql
    assert "GRANT UPDATE" not in sql
    assert "DISABLE ROW LEVEL SECURITY" not in sql
    assert "BYPASSRLS" not in sql


def test_0058_downgrade_restores_original_owner_only() -> None:
    migration, sql = _migration_sql("downgrade")
    assert (migration.revision, migration.down_revision) == ("0058", "0057")
    assert f"ALTER FUNCTION {SIGNATURE}\n  OWNER TO daon_app" in sql
    assert f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC" in sql
    assert f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO daon_app" in sql
    assert "GRANT UPDATE" not in sql


def test_0058_grants_only_new_android_transaction_table_to_app_and_reverses_it() -> None:
    _, upgrade_sql = _migration_sql("upgrade")
    _, downgrade_sql = _migration_sql("downgrade")
    target = "public.identity_android_auth_transactions"
    assert f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {target} TO daon_app" in upgrade_sql
    assert f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE {target} FROM daon_app" in downgrade_sql
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert f"has_table_privilege('daon_app', '{target}', '{privilege}')" in upgrade_sql
    assert "GRANT UPDATE ON TABLE public.sources" not in upgrade_sql
    assert "GRANT UPDATE ON TABLE public.processing_runs" not in upgrade_sql


def test_0058_android_table_accepts_only_known_owners_and_denies_public() -> None:
    _, upgrade_sql = _migration_sql("upgrade")
    _, downgrade_sql = _migration_sql("downgrade")
    assert "pg_catalog.pg_get_userbyid(c.relowner) IN ('daon_app', 'postgres')" in upgrade_sql
    assert "REVOKE ALL ON TABLE public.identity_android_auth_transactions FROM PUBLIC" in upgrade_sql
    assert "acl.grantee = 0" in upgrade_sql
    assert ") <> 'daon_app' AND (" in downgrade_sql
