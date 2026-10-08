from pathlib import Path
import importlib.util
import os
import subprocess

import pytest


VERSIONS = Path(__file__).parents[1] / "migrations" / "versions"


def _load_0056_migration():
    migration_path = VERSIONS / "0056_notebook_delete_acl.py"
    spec = importlib.util.spec_from_file_location("notebook_delete_acl_0056", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@pytest.mark.skipif(
    not os.environ.get("DAON_C9_0056_CLONE_DSN"),
    reason="privileged isolated C9 clone DSN required",
)
def test_0056_migration_applies_to_exact_clone_and_rolls_back(monkeypatch):
    import psycopg

    migration = _load_0056_migration()
    with psycopg.connect(os.environ["DAON_C9_0056_CLONE_DSN"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), (SELECT version_num FROM alembic_version)")
            assert cursor.fetchone() == ("daon_user_c9_acl_0056_20261008", "0055")
            class DatabaseOp:
                def execute(self, statement):
                    cursor.execute(statement)

            monkeypatch.setattr(migration, "op", DatabaseOp())
            migration.upgrade()
            cursor.execute("SELECT count(*) FROM pg_class WHERE relowner = 'daon_notebook_delete'::regrole AND relkind = 'r'")
            assert cursor.fetchone() == (19,)
            cursor.execute("SELECT rolcanlogin, rolinherit, rolbypassrls FROM pg_roles WHERE rolname = 'daon_notebook_delete'")
            assert cursor.fetchone() == (False, True, False)
            cursor.execute("SELECT has_table_privilege('daon_app','public.knowledge_registrations','DELETE')")
            assert cursor.fetchone() == (False,)
            cursor.execute("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = 'public.delete_notebook_scope(text,text,text)'::regprocedure")
            assert cursor.fetchone() == ("daon_notebook_delete",)
            cursor.execute("UPDATE alembic_version SET version_num='0056' WHERE version_num='0055'")
            assert cursor.rowcount == 1
            migration.upgrade()  # exact reapplication must preserve the owner/ACL state
        connection.rollback()


@pytest.mark.skipif(
    not all(os.environ.get(name) for name in (
        "DAON_C9_0056_CLONE_DSN", "DAON_C9_0056_QA_TENANT",
        "DAON_C9_0056_QA_WORKSPACE", "DAON_C9_0056_QA_NOTEBOOK",
    )),
    reason="privileged isolated C9 clone and exact QA Notebook scope required",
)
def test_0056_scoped_notebook_delete_succeeds_without_app_direct_delete(monkeypatch):
    import psycopg

    migration = _load_0056_migration()
    tenant = os.environ["DAON_C9_0056_QA_TENANT"]
    workspace = os.environ["DAON_C9_0056_QA_WORKSPACE"]
    notebook = os.environ["DAON_C9_0056_QA_NOTEBOOK"]
    with psycopg.connect(os.environ["DAON_C9_0056_CLONE_DSN"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), (SELECT version_num FROM alembic_version)")
            assert cursor.fetchone() == ("daon_user_c9_acl_0056_20261008", "0055")
            class DatabaseOp:
                def execute(self, statement):
                    cursor.execute(statement)

            monkeypatch.setattr(migration, "op", DatabaseOp())
            migration.upgrade()
            cursor.execute("SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)", (tenant, workspace))
            cursor.execute("SELECT count(*) FROM notebooks WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s", (tenant, workspace, notebook))
            assert cursor.fetchone() == (1,)
            cursor.execute("SELECT * FROM delete_notebook_scope(%s,%s,%s)", (tenant, workspace, notebook))
            cursor.fetchall()
            cursor.execute("SELECT count(*) FROM notebooks WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s", (tenant, workspace, notebook))
            assert cursor.fetchone() == (0,)
            cursor.execute("SELECT has_table_privilege('daon_app','public.knowledge_registrations','DELETE')")
            assert cursor.fetchone() == (False,)
        connection.rollback()


@pytest.mark.skipif(
    os.environ.get("DAON_C9_0056_CLONE_PSQL") != "1",
    reason="explicit opt-in required for the isolated PostgreSQL 15 admin-socket clone",
)
def test_0056_postgres15_clone_rolls_back_permissions_and_exact_qa_delete():
    sql_path = VERSIONS / "0056_notebook_delete_acl.sql"
    clone = "daon_user_c9_acl_0056_20261008"
    qa_notebook = "notebook-d263d7c8a82ac82fb057d226ce789dd3"
    script = f"""
BEGIN;
DO $pre$ BEGIN
  IF current_database() <> '{clone}' OR
     (SELECT version_num FROM alembic_version) <> '0055' OR
     EXISTS (SELECT 1 FROM pg_roles WHERE rolname='daon_notebook_delete') THEN
    RAISE EXCEPTION 'C9_0056_CLONE_PRECONDITION_FAILED';
  END IF;
END $pre$;
SELECT version_num,
  (SELECT count(*) FROM notebooks WHERE notebook_id='{qa_notebook}'),
  (SELECT count(*) FROM pg_roles WHERE rolname='daon_notebook_delete')
FROM alembic_version;
{sql_path.read_text(encoding='utf-8')}
SELECT pg_get_userbyid(proowner),
  has_function_privilege('daon_app', oid, 'EXECUTE')
FROM pg_proc WHERE oid='public.delete_notebook_scope(text,text,text)'::regprocedure;
SELECT count(*) FROM pg_class
WHERE relkind='r' AND relowner='daon_notebook_delete'::regrole;
SELECT has_table_privilege('daon_app','public.knowledge_registrations','DELETE');
SET LOCAL app.tenant_id='qa-c9-r5-tenant-20261007';
SET LOCAL app.workspace_id='qa-c9-r5-workspace-20261007';
SELECT count(*) FROM public.delete_notebook_scope(
  'qa-c9-r5-tenant-20261007','qa-c9-r5-workspace-20261007','{qa_notebook}'
);
SELECT count(*) FROM notebooks WHERE notebook_id='{qa_notebook}';
DO $stamp$ BEGIN
  UPDATE alembic_version SET version_num='0056' WHERE version_num='0055';
  IF NOT FOUND THEN RAISE EXCEPTION 'C9_0056_STAMP_FAILED'; END IF;
END $stamp$;
{sql_path.read_text(encoding='utf-8')}
ROLLBACK;
SELECT version_num,
  (SELECT count(*) FROM notebooks WHERE notebook_id='{qa_notebook}'),
  (SELECT count(*) FROM pg_roles WHERE rolname='daon_notebook_delete')
FROM alembic_version;
"""
    result = subprocess.run(
        [
            "ssh", "WSL-server",
            f"docker exec -i -u postgres local-postgres psql -U postgres -d {clone} "
            "-v ON_ERROR_STOP=1 -X -q -At -f -",
        ],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "0055|1|0", "daon_notebook_delete|t", "19", "f", "0", "0", "0055|1|0"
    ]


def test_notebook_deletion_migration_is_scoped_and_immutable():
    migration = Path(__file__).parents[1] / "migrations/versions/0023_notebook_deletion.py"
    text = migration.read_text(encoding="utf-8")
    assert "notebook_deletion_requests" in text
    assert "state IN ('accepted','deleting','completed','failed')" in text
    assert "UNIQUE (tenant_id,workspace_id,actor_id,idempotency_key)" in text
    assert "ROW LEVEL SECURITY" in text
    assert "GRANT SELECT,INSERT,UPDATE" in text
    assert "DELETE ON notebooks" not in text
    assert "delete_notebook_scope" in text
    assert "SECURITY DEFINER" in text
    assert "DELETE_SHARED_DATA_BLOCKED" in text
    assert "GRANT EXECUTE ON FUNCTION delete_notebook_scope" in text
    assert "claim_notebook_deletion_startup" in text
    assert "FOR UPDATE SKIP LOCKED" in text
    for table in (
        "document_processing_job_attempts", "document_processing_jobs", "knowledge_registrations", "evidence_references",
        "citations", "transcript_segments", "transcript_versions", "transcription_runs",
        "extraction_evidence", "understanding_results", "processing_runs", "index_versions",
        "source_versions", "sources", "object_outbox_events", "object_records",
        "sync_target_versions",
    ):
        assert f"DELETE FROM {table}" in text
    assert "previous_version_id" in text
    assert text.index("DELETE FROM document_processing_job_attempts") < text.index("DELETE FROM document_processing_jobs")
    assert text.index("DELETE FROM job_attempts") < text.index("DELETE FROM durable_jobs")
    assert "ALTER TABLE document_processing_job_attempts DISABLE TRIGGER USER" in text
    assert "ALTER TABLE processing_runs DISABLE TRIGGER USER" in text
    for table in (
        "document_processing_jobs", "knowledge_registrations", "evidence_references", "citations",
        "evidence_spans",
        "transcript_segments", "transcript_versions", "transcription_runs", "extraction_evidence",
        "understanding_results", "index_versions", "sync_target_versions", "durable_jobs",
        "object_outbox_events",
        "job_attempts",
    ):
        assert f"ALTER TABLE {table} DISABLE TRIGGER USER" in text
        assert f"ALTER TABLE {table} ENABLE TRIGGER USER" in text
