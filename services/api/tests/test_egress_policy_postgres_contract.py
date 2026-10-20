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
def test_optional_question_step_up_is_strict_in_http_and_persisted_payload():
    import pytest
    from pydantic import ValidationError
    from daon_user_api.runtime import EgressPolicyVersionBody
    from daon_user_api.egress_policy import EgressPolicyPayload, EgressPolicyError
    from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
    legacy = EgressPolicyPayload('allow_approved_external', ('external_api',), ('provider.example',),
        'internal', 65536, True, True, 'organization_admin').as_dict()
    assert PostgresEgressPolicyRepository._payload(legacy).question_step_up_required is True
    for value in (False, True):
        payload = {**legacy, 'question_step_up_required': value}
        assert PostgresEgressPolicyRepository._payload(payload).question_step_up_required is value
        assert EgressPolicyVersionBody(**payload, step_up_authorization_id='fixture').question_step_up_required is value
    for value in (None, 'false', 0):
        payload = {**legacy, 'question_step_up_required': value}
        with pytest.raises(EgressPolicyError):PostgresEgressPolicyRepository._payload(payload)
        with pytest.raises(ValidationError):EgressPolicyVersionBody(**payload, step_up_authorization_id='fixture')
