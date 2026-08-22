from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "services/api/src/daon_user_api/egress_policy_postgres.py"


def test_repository_uses_policy_canon_idempotency_audit_and_rls_transaction() -> None:
    source = MODULE.read_text("utf-8")

    assert "class PostgresEgressPolicyRepository" in source
    assert "with self._store._transaction(cloud_context) as connection:" in source
    assert "FROM egress_policy_bindings AS binding" in source
    assert "JOIN egress_policy_versions AS policy" in source
    assert 'suffix = " FOR UPDATE OF binding" if for_update else ""' in source
    assert 'suffix = " FOR UPDATE" if for_update else ""' not in source
    assert "if current_row is None:" in source
    assert "latest_row = self._select_current(" in source
    assert 'raise EgressPolicyError("VERSION_CONFLICT", 409)' in source
    assert "pg_advisory_xact_lock" in source
    assert "INSERT INTO egress_policy_versions" in source
    assert "UPDATE egress_policy_bindings" in source
    assert "INSERT INTO egress_policy_bindings" in source
    assert "INSERT INTO idempotency_records" in source
    assert "INSERT INTO audit_events" in source
    assert "egress_decisions" not in source


def test_repository_uses_canonical_helper_and_never_serializes_secrets_or_raw_payload() -> None:
    source = MODULE.read_text("utf-8")

    assert "canonical_json_bytes" in source
    assert "digest_sha256" in source
    assert "Jsonb" in source
    assert "api_key" not in source.casefold()
    assert "credential" not in source.casefold()
