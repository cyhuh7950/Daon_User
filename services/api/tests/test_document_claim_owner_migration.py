"""Emitted-SQL contract for 0059; this is not live PostgreSQL claim evidence."""

import hashlib
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace


VERSIONS = Path(__file__).parents[1] / "migrations" / "versions"
SIGNATURE = "public.claim_document_processing_job(text,integer)"


def _migration_sql(action: str) -> tuple[object, str]:
    path = VERSIONS / "0059_document_claim_owner.py"
    assert path.is_file(), "0059 document global claim owner correction is missing"
    spec = importlib.util.spec_from_file_location("document_claim_owner_0059", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements: list[str] = []
    migration.op = SimpleNamespace(execute=statements.append)
    getattr(migration, action)()
    assert len(statements) == 1
    return migration, statements[0]


def test_upgrade_transfers_only_document_claim_owner_and_preserves_caller() -> None:
    migration, sql = _migration_sql("upgrade")
    assert (migration.revision, migration.down_revision) == ("0059", "0058")
    assert f"ALTER FUNCTION {SIGNATURE} OWNER TO postgres" in sql
    assert f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC" in sql
    assert f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO daon_app" in sql
    assert "rolname = 'postgres' AND rolsuper" in sql
    assert "rolname = 'daon_app' AND NOT rolsuper AND NOT rolbypassrls" in sql
    assert "c.relrowsecurity AND c.relforcerowsecurity" in sql
    assert "search_path=pg_catalog, public" in sql
    assert "prosecdef" in sql
    assert "has_function_privilege('daon_app'" in sql
    assert "acl.grantee = 0" in sql
    for forbidden in (
        "studio", "ALTER TABLE", "ALTER ROLE", "CREATE ROLE", "GRANT UPDATE",
        "GRANT SELECT", "CREATE OR REPLACE FUNCTION", "DISABLE ROW LEVEL SECURITY",
    ):
        assert forbidden not in sql


def test_upgrade_checks_exact_existing_bounded_body_before_owner_transfer() -> None:
    _, sql = _migration_sql("upgrade")
    source = (VERSIONS / "0011_document_processing_claim_conflict.py").read_text("utf-8")
    body = re.search(r"AS \$\$(.*?)\$\$;", source, re.S).group(1)
    digest = hashlib.md5(body.encode("utf-8"), usedforsecurity=False).hexdigest()
    assert digest == "c1a1d0d9cc56ecdd9483e43b3ab9d6dc"
    assert f"pg_catalog.md5(prosrc) = '{digest}'" in sql
    assert sql.index("DOCUMENT_CLAIM_FUNCTION_PRECONDITION") < sql.index("ALTER FUNCTION")
    assert "v_body_before" in sql and "DOCUMENT_CLAIM_FUNCTION_POSTCONDITION" in sql
    for token in (
        "requested_worker_id IS NULL", "requested_lease_seconds NOT BETWEEN 10 AND 600",
        "FROM public.document_processing_jobs AS job", "FOR UPDATE SKIP LOCKED LIMIT 1",
        "INSERT INTO public.document_processing_job_attempts",
        "job.tenant_id=candidate.tenant_id", "job.workspace_id=candidate.workspace_id",
        "job.job_id=candidate.job_id",
    ):
        assert token in body


def test_downgrade_restores_app_owner_and_preserves_public_deny() -> None:
    _, sql = _migration_sql("downgrade")
    assert "IS DISTINCT FROM '0059'" in sql
    assert f"ALTER FUNCTION {SIGNATURE} OWNER TO daon_app" in sql
    assert f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC" in sql
    assert f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO daon_app" in sql
    assert "pg_catalog.pg_get_userbyid(proowner) = 'postgres'" in sql
    assert "pg_catalog.pg_get_userbyid(proowner) = 'daon_app'" in sql
    assert "acl.grantee = 0" in sql
    assert "studio" not in sql and "ALTER TABLE" not in sql
