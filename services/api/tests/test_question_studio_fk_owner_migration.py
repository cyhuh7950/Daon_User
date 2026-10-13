"""0060 emitted-SQL boundary checks; live PostgreSQL evidence is separate."""

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace


TARGETS = {
    "model_attempts", "run_results", "generation_requests", "studio_outputs",
    "output_versions", "review_requests", "approval_requests",
}
PATH = Path(__file__).parents[1] / "migrations/versions/0060_question_studio_fk_owner.py"


def emitted(action):
    assert PATH.is_file(), "0060 question/Studio FK owner correction is missing"
    spec = importlib.util.spec_from_file_location("question_studio_fk_owner_0060", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    module.op = SimpleNamespace(execute=statements.append)
    getattr(module, action)()
    assert len(statements) == 1
    return module, statements[0]


def test_upgrade_and_downgrade_change_only_seven_approved_table_owners():
    module, upgrade = emitted("upgrade")
    _, downgrade = emitted("downgrade")
    assert (module.revision, module.down_revision) == ("0060", "0059")
    for sql in (upgrade, downgrade):
        array = re.search(r"v_tables text\[\] := ARRAY\[(.*?)\]", sql, re.S).group(1)
        assert set(re.findall(r"'([^']+)'", array)) == TARGETS
        assert len(re.findall(r"'([^']+)'", array)) == 7
        assert sql.count("ALTER TABLE public.%I OWNER TO") == 1
        for forbidden in ("CREATE ROLE", "ALTER ROLE", "DROP ROLE", "DISABLE ROW LEVEL SECURITY", "DISABLE TRIGGER", "ADD COLUMN", "UPDATE public.", "DELETE FROM", "INSERT INTO"):
            assert forbidden not in sql
    assert "ALTER TABLE public.%I OWNER TO daon_notebook_delete" in upgrade
    assert "ALTER TABLE public.%I OWNER TO daon_app" in downgrade
    assert "IS DISTINCT FROM '0059'" in upgrade
    assert "IS DISTINCT FROM '0060'" in downgrade


def test_owner_transfer_preserves_app_capabilities_but_denies_raw_writes():
    _, upgrade = emitted("upgrade")
    _, downgrade = emitted("downgrade")
    assert "GRANT UPDATE ON TABLE public.%I TO daon_notebook_delete" in upgrade
    assert "GRANT UPDATE ON TABLE public.%I TO daon_app" not in upgrade + downgrade
    for sql in (upgrade, downgrade):
        assert "REVOKE ALL ON TABLE public.%I FROM daon_app" in sql
        assert "GRANT SELECT, INSERT, TRUNCATE, REFERENCES, TRIGGER" in sql
        assert "MAINTAIN" in sql
        assert "has_table_privilege('daon_app', v_relation, 'UPDATE')" in sql
        assert "has_table_privilege('daon_app', v_relation, 'DELETE')" in sql
        assert "acl.grantee = 0" in sql
        assert "a.attacl IS NOT NULL" in sql
    assert "REVOKE ALL ON TABLE public.%I FROM daon_notebook_delete" in downgrade


def test_preconditions_pin_existing_role_rls_and_canonical_guard_bodies():
    _, sql = emitted("upgrade")
    for token in ("NOT rolcanlogin", "rolinherit", "NOT rolsuper", "NOT rolbypassrls", "NOT rolcreaterole", "NOT rolcreatedb", "pg_catalog.pg_auth_members", "pg_catalog.pg_has_role", "c.relrowsecurity AND c.relforcerowsecurity", "t.tgenabled IN ('O', 'A')"):
        assert token in sql
    for name, digest in (
        ("validate_canon_insert", "f5cde9f1dd02bbab70ceaabf5590f8ba"),
        ("reject_canon_immutable_mutation", "85dbf2028dbced19cb0bdf00c391d4be"),
        ("guard_canon_state_mutation", "fda7bf31006011d2ce3d6b5cca168ecf"),
    ):
        assert name in sql and digest in sql
    assert "pg_catalog.md5(p.prosrc)" in sql
    assert "QUESTION_STUDIO_FK_OWNER_PRECONDITION" in sql
    assert "QUESTION_STUDIO_FK_OWNER_POSTCONDITION" in sql


def test_declared_core_foreign_keys_and_definition_fingerprint_are_preserved():
    _, sql = emitted("upgrade")
    for name in (
        "run_results_selected_model_attempt_id_fk", "citations_run_result_id_fk",
        "studio_outputs_generation_request_id_fk", "output_versions_studio_output_id_fk",
        "evidence_references_output_version_id_fk", "review_requests_output_version_id_fk",
        "approval_requests_review_request_id_fk", "approvals_approval_request_id_fk",
    ):
        assert name in sql
    assert "fk.convalidated" in sql
    assert "pg_catalog.pg_policy" in sql and "pg_catalog.pg_attribute" in sql
    assert "pg_catalog.pg_get_constraintdef" in sql
    assert "v_definitions_before" in sql and "v_definitions_after" in sql
    assert "IS DISTINCT FROM v_definitions_before" in sql
