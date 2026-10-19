from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import hashlib
import os

import pytest


class ReadinessMetadataDB:
    """읽기 SQL/parameter 대역. 실제 PostgreSQL 인가·동시성 증거가 아니다."""
    def __init__(self):
        self.calls = []
        self.source = "source-a"
        self.workspace = {"workspace_kind": "organization", "data_area": "internal", "cost_limit_cents": 1000}
        self.rules = [("workspace", "analyze", "grant", 0)]
        self.binding = {"connection_id": "connection-a", "model_id": "model-a", "default_version": 2,
            "provider_code": "OLLAMA", "base_url": "http://ollama:11434", "enabled": True,
            "verification_status": "verified", "configuration_version": 3, "access_mode": "public",
            "credential_requirement": "none", "adapter_type": "OLLAMA", "has_credential": False,
            "has_nonce": False, "nonce_size": None, "encryption_key_version": None,
            "credential_schema_version": None, "credential_version": 0}
        self.catalog = [{"model_id": "model-a", "effective_capabilities": ["text_generation"],
                         "catalog_status": "ready", "catalog_version": 1, "allowed": True}]
        self.personal = None
        self.policy_counter = 1
        self.egress = {"mode": "deny_external", "allowed_provider_kinds": [], "allowed_destinations": [],
            "classification": "confidential", "max_bytes": 0, "masking_required": True,
            "redaction_required": True, "required_approver": "organization_admin"}

    def execute(self, sql, params=()):
        assert sql.lstrip().startswith("SELECT "), "metadata reader의 업무 쓰기 금지"
        self.calls.append((sql, params))
        if sql.startswith("SELECT version FROM identity_auth_tenant_policies") or sql.startswith("SELECT version FROM identity_auth_workspace_policies"):
            return SimpleNamespace(fetchone=lambda: {"version": self.policy_counter})
        if sql.startswith("SELECT effect,locked FROM identity_auth_tenant_permission_rules") or sql.startswith("SELECT effect,locked FROM identity_auth_workspace_permission_rules"):
            scope = "tenant" if "identity_auth_tenant_permission_rules" in sql else "workspace"
            rows = [row for row in self.rules if row[0] == scope and row[1] == params[-1]]
            assert len(rows) <= 1
            return SimpleNamespace(fetchone=lambda: None if not rows else {"effect": rows[0][2], "locked": rows[0][3]})
        if "/* readiness-metadata-source */" in sql:
            return SimpleNamespace(fetchone=lambda: None if self.source is None else (self.source,))
        if "/* readiness-metadata-workspace */" in sql:
            return SimpleNamespace(fetchone=lambda: (deepcopy(self.workspace),))
        if "/* readiness-metadata-rules */" in sql:
            return SimpleNamespace(fetchall=lambda: deepcopy(self.rules))
        if "/* readiness-metadata-binding */" in sql:
            return SimpleNamespace(fetchone=lambda: None if self.binding is None else (deepcopy(self.binding),))
        if "/* readiness-metadata-catalog */" in sql:
            return SimpleNamespace(fetchall=lambda: [(deepcopy(row),) for row in self.catalog])
        if "/* readiness-metadata-personal */" in sql:
            return SimpleNamespace(fetchone=lambda: None if self.personal is None else (deepcopy(self.personal),))
        if "/* readiness-metadata-egress */" in sql:
            body = deepcopy(self.egress)
            return SimpleNamespace(fetchall=lambda: [] if body is None else [("organization", body), ("workspace", deepcopy(body))])
        raise AssertionError("정의하지 않은 SQL: " + sql[:100])


@pytest.fixture
def readiness_metadata(monkeypatch):
    import daon_user_api.workspace_model_defaults as module
    import daon_user_api.open_law_source_access as law
    import daon_user_api.document_index_postgres as index
    import daon_user_api.notification_source_access as special
    from daon_user_api.authorization import Action, Role, RoleScope
    from daon_user_api.document_readiness import RoleWaitEvidence, ReadinessModeProof, ReadinessDispatcherSettings
    from daon_user_api.organization_access import OrganizationAccessBridge
    now = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
    wait = RoleWaitEvidence("tenant-a", "workspace-a", "actor-a", "notebook-a", "source-a", "version-a", "run-a",
        "text_generation", "connection_availability", ReadinessModeProof("auto", "request"), "connection-a", "a" * 64,
        "MODEL_UNAVAILABLE", now - timedelta(seconds=120), now - timedelta(seconds=60))
    context = WorkspaceModelDefaultsContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a")
    grant = SimpleNamespace(allowed=True, actor_id="actor-a", tenant_id="tenant-a", workspace_id="workspace-a",
        role=Role.EDITOR, role_scope=RoleScope.WORKSPACE, policy_version="tenant-1:workspace-1", acl_version=1,
        membership_version=1, effective_permissions=())
    auth_calls = []
    def authorize(context, connection, action):
        assert action in (Action.EDIT, Action.ANALYZE)
        auth_calls.append((context, connection, action))
        return replace_grant(action)
    def replace_grant(action):
        return SimpleNamespace(**{**vars(grant), "action": action})
    monkeypatch.setattr(law, "_current_auth_grant", authorize)
    monkeypatch.setattr(index, "_source_access_allowed", lambda *args: True)
    monkeypatch.setattr(special, "_special_source_access_allowed", lambda *args: True)
    monkeypatch.setattr(OrganizationAccessBridge, "assert_organization_active", lambda *args, **kwargs: None)
    def forbidden(*args, **kwargs):
        pytest.fail("metadata reader가 resolve/복호화/개인 Key 소비를 호출함")
    monkeypatch.setattr(PostgresWorkspaceModelResolver, "resolve", forbidden)
    monkeypatch.setattr(ProviderCredentialCipher, "decrypt", forbidden)
    from daon_user_api.user_provider_credentials import PostgresUserProviderCredentialService
    monkeypatch.setattr(PostgresUserProviderCredentialService, "resolve_credential", forbidden)
    reader = getattr(module, "read_document_readiness_policy", None)
    def read(db, context, wait, **options):
        assert callable(reader), "현재 정책/FrozenContext metadata producer 미구현"
        return reader(db, context, wait, settings=options.get("settings", settings))
    settings = ReadinessDispatcherSettings(scopes=frozenset({("tenant-a", "workspace-a", "actor-a", "version-a")}))
    return SimpleNamespace(module=module, db=ReadinessMetadataDB(), context=context, wait=wait,
        grant=grant, auth_calls=auth_calls, read=read)


def test_readiness_metadata_refresh_counters_do_not_change_semantic_frozen_digest(readiness_metadata):
    f = readiness_metadata
    first = f.read(f.db, f.context, f.wait)
    f.db.binding.update(default_version=77, configuration_version=88)
    f.grant.policy_version = "tenant-99:workspace-99"
    f.grant.acl_version = 71
    f.grant.membership_version = 81
    second = f.read(f.db, replace(f.context, trace_id="new-trace", policy_version="other-counter"), f.wait)
    assert first.eligibility.frozen_context_digest == second.eligibility.frozen_context_digest
    assert second.eligibility.configuration_version == 88
    assert first.frozen_context_json == second.frozen_context_json
    body = json.loads(first.frozen_context_json)
    assert body["scope"] == {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "actor_id": "actor-a",
                              "notebook_id": "notebook-a", "source_version_id": "version-a"}
    assert body["mode"] == {"mode": "auto", "origin": "request"}
    assert hashlib.sha256(first.frozen_context_json.encode()).hexdigest() == first.eligibility.frozen_context_digest
    body["cost"]["state"] = "allowed"
    assert json.loads(first.frozen_context_json)["cost"]["state"] == "unknown"
    f.db.catalog.append({"model_id": "unused-model", "effective_capabilities": ["text_generation"],
                         "catalog_status": "ready", "catalog_version": 1, "allowed": False})
    unrelated = f.read(f.db, f.context, f.wait)
    assert unrelated.eligibility.frozen_context_digest == first.eligibility.frozen_context_digest
    assert unrelated.eligibility.model_catalog_digest != first.eligibility.model_catalog_digest


def test_readiness_metadata_actual_policy_binding_permissions_and_credential_refs_change_digest(readiness_metadata):
    f = readiness_metadata
    baseline = f.read(f.db, f.context, f.wait).eligibility.frozen_context_digest
    for field, value in (("policy", 1500), ("rules", [("workspace", "analyze", "deny", 1)]),
                         ("allowlist", False), ("binding", "model-b"), ("credential", 4), ("egress", "restricted")):
        db = deepcopy(f.db)
        if field == "policy": db.workspace["cost_limit_cents"] = value
        elif field == "rules": db.rules = value
        elif field == "allowlist": db.catalog[0]["allowed"] = value
        elif field == "binding": db.binding["model_id"] = value
        elif field == "credential":
            db.binding.update(has_credential=True, has_nonce=True, nonce_size=12, encryption_key_version=1,
                              credential_schema_version=1, credential_version=value)
        else: db.egress["classification"] = value
        changed = f.read(db, f.context, f.wait)
        assert changed.eligibility.frozen_context_digest != baseline, field
    changed_mode = f.read(f.db, f.context, replace(f.wait, mode=type(f.wait.mode)("local_only", "request")))
    assert changed_mode.eligibility.frozen_context_digest != baseline


def test_readiness_metadata_cost_unknown_and_external_allow_never_invents_original_approval(readiness_metadata):
    f = readiness_metadata
    local = f.read(f.db, f.context, f.wait)
    assert (local.eligibility.cost_state, local.eligibility.egress_state) == ("unknown", "allowed")
    assert json.loads(local.frozen_context_json)["cost"] == {"state": "unknown"}
    from daon_user_api.document_readiness import ValidatedReadinessObservation, decide_reprocess
    now = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
    observed = ValidatedReadinessObservation(1, "rdy-" + "1" * 32, "connection-a", "OLLAMA", "text_generation",
        local.eligibility.model_catalog_digest, 3, now - timedelta(seconds=61), now,
        "model_catalog_refresh", "healthy", 1, "d" * 64)
    decision = decide_reprocess(f.wait, observed, replace(local.eligibility, latest_observation_sequence=1), db_now=now)
    assert (decision.create_run, decision.safe_code) == (False, "READINESS_COST_UNCONFIRMED")
    f.db.binding.update(provider_code="GROQ", adapter_type="GROQ", base_url="https://api.groq.com/openai/v1",
        credential_requirement="required", has_credential=True, has_nonce=True, nonce_size=12,
        encryption_key_version=1, credential_schema_version=1, credential_version=1)
    f.db.egress.update(mode="allow_approved_external", allowed_provider_kinds=["external_api"],
                       allowed_destinations=["api.groq.com"], max_bytes=1024)
    external = f.read(f.db, f.context, f.wait)
    assert (external.eligibility.cost_state, external.eligibility.egress_state) == ("unknown", "approval_missing")
    assert not f.read(f.db, f.context, replace(f.wait, mode=type(f.wait.mode)("local_only", "request"))).eligibility.candidate_available
    f.db.egress = None
    missing = f.read(f.db, f.context, f.wait)
    assert (missing.eligibility.policy_allowed, missing.eligibility.cost_state, missing.eligibility.egress_state) == (True, "unknown", "approval_missing")
    f.db.egress = {"mode": "deny_external", "allowed_provider_kinds": [], "allowed_destinations": [],
        "classification": "confidential", "max_bytes": 0, "masking_required": True,
        "redaction_required": True, "required_approver": "organization_admin"}
    assert f.read(f.db, f.context, f.wait).eligibility.egress_state == "blocked"


def internal_cost_settings(f, monkeypatch, *, documents=None):
    from daon_user_api.document_readiness import ReadinessDispatcherSettings
    row = dict(tenant_id=f.wait.tenant_id, workspace_id=f.wait.workspace_id, actor_id=f.wait.actor_id,
        source_version_id=f.wait.source_version_id, capability=f.wait.capability,
        connection_id=f.db.binding["connection_id"], model_id=f.db.binding["model_id"],
        configuration_version=f.db.binding["configuration_version"], endpoint=f.db.binding["base_url"],
        policy_version="internal-operating-cost-excluded-v1")
    monkeypatch.setenv("DAON_DOCUMENT_READINESS_SCOPES_JSON", json.dumps([{k: row[k] for k in (
        "tenant_id", "workspace_id", "actor_id", "source_version_id")}]))
    monkeypatch.setenv("DAON_DOCUMENT_READINESS_INTERNAL_COST_POLICIES_JSON", json.dumps(documents or [row]))
    return ReadinessDispatcherSettings.from_env(), row


def test_internal_operating_cost_policy_allows_exact_current_internal_candidate_without_money(readiness_metadata, monkeypatch):
    f = readiness_metadata
    settings, row = internal_cost_settings(f, monkeypatch)
    snapshot = f.read(f.db, f.context, f.wait, settings=settings)
    assert snapshot.eligibility.cost_state == "allowed"
    assert json.loads(snapshot.frozen_context_json)["cost"] == {
        "state": "allowed", "basis": "internal_operating_cost_excluded", "policy": row}
    from daon_user_api.document_readiness import ValidatedReadinessObservation, decide_reprocess
    now = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
    observed = ValidatedReadinessObservation(1, "rdy-" + "1" * 32, "connection-a", "OLLAMA", "text_generation",
        snapshot.eligibility.model_catalog_digest, 3, now - timedelta(seconds=61), now,
        "model_catalog_refresh", "healthy", 1, "d" * 64)
    assert decide_reprocess(f.wait, observed, replace(snapshot.eligibility, latest_observation_sequence=1), db_now=now).safe_code == "READY_REPROCESS_ALLOWED"


@pytest.mark.parametrize("change", ["scope", "actor", "version", "endpoint", "model", "connection", "role",
    "disabled", "unverified", "personal", "credential", "adapter", "external", "catalog", "permission"])
def test_internal_operating_cost_policy_does_not_authorize_changed_or_external_candidates(readiness_metadata, monkeypatch, change):
    f = readiness_metadata
    settings, _ = internal_cost_settings(f, monkeypatch)
    wait = f.wait
    if change == "scope": settings = replace(settings, scopes=frozenset())
    if change == "actor": settings = replace(settings, internal_cost_policies=tuple(
        replace(p, actor_id="other-actor") for p in settings.internal_cost_policies))
    if change == "version": f.db.binding["configuration_version"] += 1
    if change == "endpoint": f.db.binding["base_url"] = "http://other-ollama:11434"
    if change == "model": f.db.binding["model_id"] = "other-model"
    if change == "connection": f.db.binding["connection_id"] = "other-connection"
    if change == "role": wait = replace(wait, capability="image_understanding")
    if change == "disabled": f.db.binding["enabled"] = False
    if change == "unverified": f.db.binding["verification_status"] = "unverified"
    if change == "personal": f.db.binding["access_mode"] = "personal"
    if change == "credential": f.db.binding.update(has_credential=True, has_nonce=True, nonce_size=12,
        encryption_key_version=1, credential_schema_version=1, credential_version=1)
    if change == "adapter": f.db.binding["adapter_type"] = "unconfirmed-adapter"
    if change == "external": f.db.binding.update(provider_code="GROQ", adapter_type="GROQ", base_url="https://api.groq.com/openai/v1")
    if change == "catalog": f.db.catalog[0]["allowed"] = False
    if change == "permission": f.db.rules = [("workspace", "local_internal_llm", "deny", 0)]
    snapshot = f.read(f.db, f.context, wait, settings=settings)
    assert snapshot.eligibility.cost_state == "unknown"
    assert json.loads(snapshot.frozen_context_json)["cost"] == {"state": "unknown"}


def test_internal_cost_policy_unrelated_order_and_removal_preserve_semantic_boundary(readiness_metadata, monkeypatch):
    f = readiness_metadata
    settings, row = internal_cost_settings(f, monkeypatch)
    first = f.read(f.db, f.context, f.wait, settings=settings)
    settings, _ = internal_cost_settings(f, monkeypatch, documents=[{**row, "source_version_id": "unrelated"}, row])
    second = f.read(f.db, f.context, f.wait, settings=settings)
    assert first.frozen_context_json == second.frozen_context_json
    revoked = f.read(f.db, f.context, f.wait, settings=replace(settings, internal_cost_policies=()))
    assert revoked.eligibility.cost_state == "unknown"
    assert revoked.eligibility.frozen_context_digest != first.eligibility.frozen_context_digest


@pytest.mark.parametrize("change", ["missing", "extra", "bool", "version", "duplicate", "userinfo", "query", "fragment", "empty_query", "empty_fragment", "noncanonical"])
def test_internal_cost_policy_parser_rejects_ambiguous_or_unpinned_input(readiness_metadata, monkeypatch, change):
    from daon_user_api.document_readiness import ReadinessDispatcherSettings
    f = readiness_metadata
    _, row = internal_cost_settings(f, monkeypatch)
    if change == "missing": row.pop("actor_id")
    if change == "extra": row["currency"] = "USD"
    if change == "bool": row["configuration_version"] = True
    if change == "version": row["policy_version"] = "v2"
    if change == "userinfo": row["endpoint"] = "http://user:pass@ollama:11434"
    if change == "query": row["endpoint"] += "?x=1"
    if change == "fragment": row["endpoint"] += "#x"
    if change == "empty_query": row["endpoint"] += "?"
    if change == "empty_fragment": row["endpoint"] += "#"
    if change == "noncanonical": row["endpoint"] += "/"
    monkeypatch.setenv("DAON_DOCUMENT_READINESS_INTERNAL_COST_POLICIES_JSON", json.dumps([row, row] if change == "duplicate" else [row]))
    from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
    with pytest.raises(DocumentUnderstandingError): ReadinessDispatcherSettings.from_env()


def test_internal_cost_policy_parser_default_and_json_container_boundaries(readiness_metadata, monkeypatch):
    from daon_user_api.document_readiness import ReadinessDispatcherSettings
    from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
    name = "DAON_DOCUMENT_READINESS_INTERNAL_COST_POLICIES_JSON"
    monkeypatch.delenv(name, raising=False)
    assert ReadinessDispatcherSettings.from_env().internal_cost_policies == ()
    _, row = internal_cost_settings(readiness_metadata, monkeypatch)
    for raw in ("null", "{}", "true", "[true]", "[NaN]",
                json.dumps([row]).replace('"tenant_id": "tenant-a"', '"tenant_id":"tenant-a","tenant_id":"tenant-b"'),
                json.dumps([{**row, "source_version_id": f"version-{i}"} for i in range(101)]),
                json.dumps([row, {**row, "model_id": "different-model"}])):
        # Windows의 실제 단일 환경변수 32767자 제한과 parser 100개 경계를 구분한다.
        with monkeypatch.context() as local:
            local.setattr(os, "environ", {**dict(os.environ), name: raw})
            with pytest.raises(DocumentUnderstandingError): ReadinessDispatcherSettings.from_env()
    raw = json.dumps([{**row, "source_version_id": f"version-{i}"} for i in range(100)])
    with monkeypatch.context() as local:
        local.setattr(os, "environ", {**dict(os.environ), name: raw})
        policies = ReadinessDispatcherSettings.from_env().internal_cost_policies
    assert len(policies) == 100
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError): policies[0].actor_id = "changed"


def test_internal_cost_policy_exact_native_asr_uses_real_supplier_without_probe(readiness_metadata, monkeypatch):
    f = readiness_metadata
    monkeypatch.setenv("DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID", "workspace-a")
    f.wait = replace(f.wait, capability="speech_to_text")
    f.db.binding.update(connection_id="asr-a", provider_code="CUSTOM", adapter_type="asr_base_native",
        base_url="http://asr-base:8080", model_id="faster-whisper-base")
    f.db.catalog[0].update(model_id="faster-whisper-base", effective_capabilities=["speech_to_text"])
    settings, _ = internal_cost_settings(f, monkeypatch)
    snapshot = f.read(f.db, f.context, f.wait, settings=settings)
    assert snapshot.eligibility.cost_state == "allowed"
    assert json.loads(snapshot.frozen_context_json)["binding"]["native_asr_contract"] is not None


def test_readiness_metadata_personal_and_invalid_credential_schema_do_not_resolve_keys(readiness_metadata):
    f = readiness_metadata
    f.db.binding.update(access_mode="personal", credential_requirement="required", provider_code="GROQ",
        adapter_type="GROQ", base_url="https://api.groq.com/openai/v1")
    assert not f.read(f.db, f.context, f.wait).eligibility.candidate_available
    f.db.personal = {"provider_code": "GROQ", "verification_status": "verified", "has_credential": True,
        "has_nonce": True, "nonce_size": 12, "credential_version": 2, "credential_schema_version": 1,
        "encryption_key_version": 1}
    assert not f.read(f.db, f.context, f.wait).eligibility.candidate_available
    f.db.personal["credential_schema_version"] = 2
    result = f.read(f.db, f.context, f.wait)
    assert result.eligibility.candidate_available
    assert (result.eligibility.cost_state, result.eligibility.egress_state) == ("unknown", "blocked")
    assert json.loads(result.frozen_context_json)["binding"]["credential_reference"]["source"] == "user"
    for sql, _ in f.db.calls:
        assert not sql.lstrip().startswith(("INSERT", "UPDATE", "DELETE"))
        assert "c.encrypted_credential," not in sql and "u.encrypted_credential," not in sql


def test_readiness_metadata_native_asr_exact_qa_contract_and_no_live_ready_claim(readiness_metadata, monkeypatch):
    f = readiness_metadata
    wait = replace(f.wait, capability="speech_to_text")
    f.db.binding.update(provider_code="CUSTOM", adapter_type="asr_base_native", base_url="http://asr-base:8080",
                        model_id="faster-whisper-base")
    f.db.catalog[0].update(model_id="faster-whisper-base", effective_capabilities=["speech_to_text"])
    assert not f.read(f.db, f.context, wait).eligibility.candidate_available
    monkeypatch.setenv("DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID", "workspace-a")
    valid = f.read(f.db, f.context, wait)
    assert valid.eligibility.candidate_available
    assert valid.eligibility.cost_state == "unknown"
    monkeypatch.delenv("DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID")
    assert f.read(f.db, f.context, wait).eligibility.frozen_context_digest != valid.eligibility.frozen_context_digest
    monkeypatch.setenv("DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID", "workspace-a")
    assert json.loads(valid.frozen_context_json)["binding"]["native_asr_contract"] == {
        "revision": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
        "model_digest": "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"}
    for key, value in (("base_url", "http://other:8080"), ("adapter_type", "CUSTOM"), ("access_mode", "personal"),
                       ("credential_requirement", "required"), ("model_id", "wrong-model")):
        db = deepcopy(f.db); db.binding[key] = value
        assert not f.read(db, f.context, wait).eligibility.candidate_available


def test_readiness_metadata_strict_schema_absent_binding_current_capability_and_secret_errors(readiness_metadata):
    f = readiness_metadata
    for key, value in (("enabled", False), ("verification_status", "failed"), ("credential_schema_version", 999)):
        db = deepcopy(f.db); db.binding[key] = value
        assert not f.read(db, f.context, f.wait).eligibility.candidate_available
    for change in ("missing", "capability", "duplicate"):
        db = deepcopy(f.db)
        if change == "missing": db.binding = None
        elif change == "capability": db.catalog[0]["effective_capabilities"] = ["document_parsing"]
        else: db.catalog.append(deepcopy(db.catalog[0]))
        if change == "duplicate":
            with pytest.raises(WorkspaceModelDefaultsError): f.read(db, f.context, f.wait)
        else: assert not f.read(db, f.context, f.wait).eligibility.candidate_available
    for key, value in (("credential_version", True), ("enabled", 1), ("raw_secret", "CANARY_PRIVATE")):
        db = deepcopy(f.db); db.binding[key] = value
        with pytest.raises(WorkspaceModelDefaultsError) as caught: f.read(db, f.context, f.wait)
        assert "CANARY_PRIVATE" not in str(caught.value)
    db = deepcopy(f.db)
    db.binding.update(base_url="http://user:CANARY_PRIVATE@ollama:11434")
    with pytest.raises(WorkspaceModelDefaultsError) as caught: f.read(db, f.context, f.wait)
    assert "CANARY_PRIVATE" not in str(caught.value)
    unknown = f.read(f.db, f.context, replace(f.wait, capability=None))
    assert (unknown.eligibility.candidate_available, unknown.eligibility.dependency_connection_id,
            unknown.eligibility.latest_observation_sequence) == (False, None, 0)


def test_readiness_metadata_current_auth_source_and_org_checks_are_not_scope_flags(readiness_metadata, monkeypatch):
    import daon_user_api.notification_source_access as special
    import daon_user_api.open_law_source_access as law
    from daon_user_api.organization_access import OrganizationAccessBridge
    from daon_user_api.organization_membership import OrganizationWorkflowError
    f = readiness_metadata
    f.read(f.db, f.context, f.wait)
    assert len(f.auth_calls) == 2
    assert all(call[1] is f.db for call in f.auth_calls)
    db = deepcopy(f.db); db.source = None
    with pytest.raises(WorkspaceModelDefaultsError) as caught: f.read(db, f.context, f.wait)
    assert caught.value.status == 404
    with monkeypatch.context() as m:
        m.setattr(special, "_special_source_access_allowed", lambda *args: False)
        with pytest.raises(WorkspaceModelDefaultsError) as caught: f.read(f.db, f.context, f.wait)
        assert caught.value.status == 403
    with monkeypatch.context() as m:
        def deny(*args, **kwargs): raise OrganizationWorkflowError("ORGANIZATION_MEMBERSHIP_SUSPENDED", 403)
        m.setattr(OrganizationAccessBridge, "assert_organization_active", deny)
        with pytest.raises(WorkspaceModelDefaultsError) as caught: f.read(f.db, f.context, f.wait)
        assert caught.value.status == 403
    with monkeypatch.context() as m:
        def deny(*args): raise law.OpenLawAccessError("OPEN_LAW_ACCESS_DENIED")
        m.setattr(law, "_current_auth_grant", deny)
        with pytest.raises(WorkspaceModelDefaultsError) as caught: f.read(f.db, f.context, f.wait)
        assert caught.value.status == 403
    with pytest.raises(WorkspaceModelDefaultsError):
        f.read(f.db, replace(f.context, actor_id="actor-b"), f.wait)


def test_readiness_metadata_snapshot_type_binds_body_to_digest_without_mutable_cache(readiness_metadata):
    f = readiness_metadata
    result = f.read(f.db, f.context, f.wait)
    with pytest.raises(WorkspaceModelDefaultsError):
        replace(result, frozen_context_json=result.frozen_context_json.replace('"unknown"', '"allowed"'))
    with pytest.raises(WorkspaceModelDefaultsError):
        replace(result, eligibility=replace(result.eligibility, frozen_context_digest="f" * 64))


def test_readiness_snapshot_cost_state_must_match_body_without_changing_real_unknown_producer(readiness_metadata, monkeypatch):
    f = readiness_metadata
    original = f.read(f.db, f.context, f.wait)
    for state in ("blocked", "unknown"):
        body = json.loads(original.frozen_context_json)
        body["cost"] = {"state": state}
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        # 합성 결정 대역용 일관성이다. 실제 B1 비용 producer를 allowed로 바꾸지 않는다.
        snapshot = f.module.ReadinessPolicySnapshot(replace(original.eligibility, cost_state=state,
            frozen_context_digest=digest), canonical)
        assert snapshot.eligibility.cost_state == state
        for wrong in ("allowed", "blocked", "unknown"):
            if wrong != state:
                with pytest.raises(WorkspaceModelDefaultsError):
                    replace(snapshot, eligibility=replace(snapshot.eligibility, cost_state=wrong))
    fresh = f.read(f.db, f.context, f.wait)
    assert fresh.eligibility.cost_state == "unknown"
    assert json.loads(fresh.frozen_context_json)["cost"] == {"state": "unknown"}


@pytest.mark.parametrize("change", ["bare_allowed", "basis", "actor", "role", "endpoint", "config", "binding_connection", "extra"])
def test_internal_cost_snapshot_rejects_forged_allowed_proof(readiness_metadata, monkeypatch, change):
    f = readiness_metadata
    settings, _ = internal_cost_settings(f, monkeypatch)
    original = f.read(f.db, f.context, f.wait, settings=settings)
    body = json.loads(original.frozen_context_json)
    if change == "bare_allowed": body["cost"] = {"state": "allowed"}
    if change == "basis": body["cost"]["basis"] = "free"
    if change == "actor": body["cost"]["policy"]["actor_id"] = "another-actor"
    if change == "role": body["cost"]["policy"]["capability"] = "image_understanding"
    if change == "endpoint": body["cost"]["policy"]["endpoint"] = "http://other-internal:11434"
    if change == "config": body["cost"]["policy"]["configuration_version"] += 1
    if change == "binding_connection":
        body["binding"]["connection_id"] = body["catalog"]["connection_id"] = "other-connection"
    if change == "extra": body["cost"]["amount"] = "0"
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(WorkspaceModelDefaultsError):
        f.module.ReadinessPolicySnapshot(replace(original.eligibility,
            frozen_context_digest=hashlib.sha256(canonical.encode()).hexdigest()), canonical)


@pytest.mark.parametrize("rules,allowed,reason", [
    ([], True, "ROLE_DEFAULT_GRANT"),
    ([("tenant", "local_internal_llm", "deny", 1), ("workspace", "local_internal_llm", "grant", 0)], False, "ORGANIZATION_LOCK"),
    ([("tenant", "local_internal_llm", "deny", 0), ("workspace", "local_internal_llm", "grant", 0)], False, "ORGANIZATION_DENY"),
    ([("tenant", "local_internal_llm", "grant", 0), ("workspace", "local_internal_llm", "deny", 0)], False, "WORKSPACE_DENY"),
    ([("tenant", "local_internal_llm", "grant", 1), ("workspace", "local_internal_llm", "deny", 0)], True, "ORGANIZATION_LOCK"),
])
def test_readiness_execution_permission_uses_real_core_local_precedence(readiness_metadata, rules, allowed, reason):
    f = readiness_metadata
    f.db.rules = rules
    wait = replace(f.wait, mode=type(f.wait.mode)("local_only", "request"))
    snapshot = f.read(f.db, f.context, wait)
    assert snapshot.eligibility.authorized is allowed
    assert snapshot.eligibility.local_only_allowed is allowed
    permission = json.loads(snapshot.frozen_context_json)["authorization"]["execution_permission"]
    assert (permission["permission"], permission["requested"], permission["effective"], permission["reason_code"]) == (
        "local_internal_llm", True, allowed, reason)
    assert snapshot.eligibility.cost_state == "unknown"


@pytest.mark.parametrize("role,rule,allowed,reason", [
    ("editor", None, False, "ROLE_DEFAULT_DENY"),
    ("organization_admin", None, True, "ROLE_DEFAULT_GRANT"),
    ("editor", "grant", True, "WORKSPACE_GRANT"),
    ("organization_admin", "deny", False, "WORKSPACE_DENY"),
])
def test_readiness_execution_permission_external_personal_key_cannot_override_deny(
        readiness_metadata, role, rule, allowed, reason):
    from daon_user_api.authorization import Role
    f = readiness_metadata
    f.grant.role = Role(role)
    f.db.binding.update(provider_code="GROQ", adapter_type="GROQ", base_url="https://api.groq.com/openai/v1",
        access_mode="personal", credential_requirement="required")
    f.db.personal = {"provider_code": "GROQ", "verification_status": "verified", "has_credential": True,
        "has_nonce": True, "nonce_size": 12, "encryption_key_version": 1, "credential_schema_version": 2, "credential_version": 4}
    f.db.rules = [] if rule is None else [("workspace", "external_llm", rule, 0)]
    snapshot = f.read(f.db, f.context, f.wait)
    assert snapshot.eligibility.authorized is allowed
    assert snapshot.eligibility.local_only_allowed is False
    assert snapshot.eligibility.cost_state == "unknown"
    assert snapshot.eligibility.egress_state == "blocked"
    permission = json.loads(snapshot.frozen_context_json)["authorization"]["execution_permission"]
    assert (permission["permission"], permission["effective"], permission["reason_code"]) == ("external_llm", allowed, reason)


def test_readiness_execution_permission_digest_tracks_semantics_not_policy_counters(readiness_metadata, monkeypatch):
    from daon_user_api.authorization import ROLE_PERMISSION_DEFAULTS, Role
    f = readiness_metadata
    first = f.read(f.db, f.context, f.wait)
    f.db.policy_counter = 88
    refreshed = f.read(f.db, f.context, f.wait)
    assert refreshed.frozen_context_json == first.frozen_context_json
    assert refreshed.eligibility.frozen_context_digest == first.eligibility.frozen_context_digest
    monkeypatch.setitem(ROLE_PERMISSION_DEFAULTS, Role.EDITOR, frozenset())
    changed = f.read(f.db, f.context, f.wait)
    assert changed.eligibility.authorized is False
    assert changed.eligibility.frozen_context_digest != first.eligibility.frozen_context_digest


def test_readiness_metadata_data_area_preserves_authorization_safe_id_contract_not_new_enum(readiness_metadata):
    f = readiness_metadata
    for area in ("local_private", "internal", "cloud_sync", "A", "tenant-zone.v2:private_1", "a" * 128):
        f.db.workspace["data_area"] = area
        result = f.read(f.db, f.context, f.wait)
        assert json.loads(result.frozen_context_json)["workspace_policy"]["data_area"] == area
    for area in (None, True, 1, [], {}, "", "*", " unsafe", "bad/area", "bad\narea", "a" * 129):
        f.db.workspace["data_area"] = area
        with pytest.raises(WorkspaceModelDefaultsError): f.read(f.db, f.context, f.wait)
    f.db.workspace["data_area"] = "local_private"
    f.db.workspace["workspace_kind"] = "local_private"
    with pytest.raises(WorkspaceModelDefaultsError): f.read(f.db, f.context, f.wait)


def test_readiness_catalog_helper_is_shared_db_canonical_not_provider_wire_or_counters(readiness_metadata):
    f = readiness_metadata
    helper = getattr(f.module, "read_readiness_catalog", None)
    assert callable(helper), "관측/현재 판정이 공유할 DB catalog helper 미구현"
    first = helper(f.db, "connection-a")
    expected = '{"connection_id":"connection-a","models":[{"allowed":true,"catalog_status":"ready",'
    expected += '"effective_capabilities":["text_generation"],"model_id":"model-a"}],"schema_version":1}'
    assert first.canonical_json == expected
    assert first.digest_sha256 == hashlib.sha256(expected.encode()).hexdigest()
    f.db.catalog[0]["catalog_version"] = 99
    assert helper(f.db, "connection-a") == first
    assert f.read(f.db, f.context, f.wait).eligibility.model_catalog_digest == first.digest_sha256
    for key, value in (("model_id", "model-b"), ("effective_capabilities", ["image_understanding"]),
                       ("allowed", False), ("catalog_status", "stale")):
        db = deepcopy(f.db); db.catalog[0][key] = value
        assert helper(db, "connection-a").digest_sha256 != first.digest_sha256


def test_readiness_catalog_order_and_capability_order_are_semantic_sets_without_extra_fields(readiness_metadata):
    f = readiness_metadata
    helper = getattr(f.module, "read_readiness_catalog", None)
    assert callable(helper), "관측/현재 판정이 공유할 DB catalog helper 미구현"
    f.db.catalog = [{"model_id": "model-b", "effective_capabilities": ["image_understanding", "text_generation"],
                     "catalog_status": "ready", "catalog_version": 2, "allowed": True}, *f.db.catalog]
    first = helper(f.db, "connection-a")
    f.db.catalog.reverse()
    f.db.catalog[1]["effective_capabilities"].reverse()
    assert helper(f.db, "connection-a") == first
    f.db.catalog[0]["raw_secret"] = "CANARY_PRIVATE"
    with pytest.raises(WorkspaceModelDefaultsError) as caught: helper(f.db, "connection-a")
    assert "CANARY_PRIVATE" not in str(caught.value)

from daon_user_api.provider_credentials import ProviderCredentialCipher
from daon_user_api.user_provider_credentials import UserProviderCredentialResult
from daon_user_api.workspace_model_defaults import (
    PostgresWorkspaceModelDefaultsService,
    PostgresWorkspaceModelResolver,
    WorkspaceModelDefaultsContext,
    WorkspaceModelDefaultsError,
    WorkspaceModelContext,
    WorkspaceModelUnavailable,
)


class Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class Connection:
    def __init__(self, database) -> None:
        self.database = database

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if not normalized.startswith("SELECT d.version,d.connection_id,d.model_id"):
            raise AssertionError(f"unexpected SQL: {normalized}")
        self.database.queries.append((normalized, params))
        return Cursor(self.database.row)


class Database:
    def __init__(self, row) -> None:
        self.row = row
        self.queries = []
        self.contexts = []


class Store:
    def __init__(self, database: Database) -> None:
        self.database = database

    @contextmanager
    def _transaction(self, context):
        self.database.contexts.append(context)
        yield Connection(self.database)


def context() -> WorkspaceModelContext:
    return WorkspaceModelContext(
        tenant_id="tenant-001", workspace_id="workspace-001", actor_id="actor-001",
    )


def test_native_asr_resolves_only_in_explicit_qa_workspace(monkeypatch):
    monkeypatch.setenv("DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID", "workspace-001")
    cipher = ProviderCredentialCipher(b"r" * 32, encryption_key_version=1)
    database = Database(row(cipher, credential=None, provider_code="CUSTOM",
        capability="speech_to_text", model_id="faster-whisper-base",
        base_url="http://asr-base:8080", adapter_type="asr_base_native",
        credential_requirement="none", credential_version=0))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)
    with resolver.resolve(context(), "speech_to_text") as model:
        assert model.provider_kind == "server_internal"
        assert model.routing_owner == "local_runtime"
        assert model.daon_fallback_allowed is False
        assert model.credential_text(required=False) is None
    database.queries.clear()
    with pytest.raises(WorkspaceModelUnavailable):
        with resolver.resolve(WorkspaceModelContext("tenant-001", "other-workspace", "actor-001"), "speech_to_text"):
            pass
    assert database.queries == []
    for index, value in ((17, "openai_compatible"), (4, "https://other.example"),
                         (2, "other-model"), (16, "required"), (15, "personal")):
        altered = list(database.row); altered[index] = value
        with pytest.raises(WorkspaceModelUnavailable):
            with PostgresWorkspaceModelResolver(Store(Database(tuple(altered))), cipher).resolve(context(), "speech_to_text"):
                pass


def test_native_asr_default_catalog_is_qa_scoped_and_pinned(monkeypatch):
    monkeypatch.setenv("DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID", "workspace-001")
    database = DefaultsDatabase()
    database.models = [("asr-qa", "CUSTOM", "CPU base ASR", False, 0, "verified", 1,
        "faster-whisper-base", ["speech_to_text"], "ready", 1,
        "asr_base_native", "http://asr-base:8080", "none")]
    service = PostgresWorkspaceModelDefaultsService(DefaultsStore(database))
    ctx = WorkspaceModelDefaultsContext("tenant-001", "workspace-001", "actor-001", "trace-001", "policy-v1")
    initial = service.read(ctx)
    assert initial["available_models"][0]["effective_capabilities"] == ["speech_to_text"]
    other = WorkspaceModelDefaultsContext("tenant-001", "other-workspace", "actor-001", "trace-001", "policy-v1")
    assert service.read(other)["available_models"] == []
    saved, replay = service.save(ctx, capability="speech_to_text", connection_id="asr-qa", model_id="faster-whisper-base",
        expected_version=0, expected_etag=initial["etag"], idempotency_key="asr-save-qa")
    assert not replay and saved["defaults"][0]["capability"] == "speech_to_text"
    assert any("c.adapter_type='asr_base_native'" in sql and "c.base_url='http://asr-base:8080'" in sql
               for sql in database.query_sql)
    with pytest.raises(WorkspaceModelDefaultsError, match="PROVIDER_CAPABILITY_UNSUPPORTED"):
        service.save(other, capability="speech_to_text", connection_id="asr-qa", model_id="faster-whisper-base",
                     expected_version=0, expected_etag=initial["etag"], idempotency_key="asr-save")


def row(
    cipher: ProviderCredentialCipher, *, credential: bytes | None = b"client-key-v1",
    connection_id: str = "eoul-primary", provider_code: str = "EOUL_GATEWAY",
    capability: str = "text_generation", enabled: bool = True,
    verification_status: str = "verified", catalog_status: str = "ready",
    effective_capabilities: list[str] | None = None, credential_version: int = 4,
    access_mode: str = "public", credential_requirement: str = "required",
    adapter_type: str = "",
    model_id: str = "assistant-default",
    base_url: str = "https://gateway.example.com",
):
    sealed = None if credential is None else cipher.encrypt(
        connection_id, provider_code, credential_version, credential,
    )
    return (
        3, connection_id, model_id, provider_code,
        base_url, None if sealed is None else sealed.ciphertext,
        None if sealed is None else sealed.nonce,
        None if sealed is None else sealed.encryption_key_version,
        None if sealed is None else sealed.schema_version,
        credential_version, enabled, verification_status,
        effective_capabilities if effective_capabilities is not None else [capability],
        catalog_status, 9, access_mode, credential_requirement, adapter_type,
    )


def test_resolve_revalidates_workspace_default_and_releases_latest_credential() -> None:
    cipher = ProviderCredentialCipher(b"r" * 32, encryption_key_version=1)
    database = Database(row(cipher))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with resolver.resolve(context(), "text_generation") as resolved:
        assert resolved.connection_id == "eoul-primary"
        assert resolved.provider_code == "EOUL_GATEWAY"
        assert resolved.model_id == "assistant-default"
        assert resolved.credential_version == 4
        assert resolved.catalog_version == 9
        assert resolved.routing_owner == "gateway"
        assert resolved.daon_fallback_allowed is False
        assert resolved.credential_text() == "client-key-v1"
        assert "client-key-v1" not in repr(resolved)

    with pytest.raises(WorkspaceModelUnavailable, match="^PROVIDER_CREDENTIAL_RELEASED$"):
        resolved.credential_text()
    assert database.contexts[0].tenant_id == "tenant-001"
    assert database.contexts[0].workspace_id == "workspace-001"
    assert database.queries[0][1] == ("tenant-001", "workspace-001", "text_generation")


@pytest.mark.parametrize(("provider_code", "auto_model_id"), [
    ("OMNIROUTE", "auto"), ("OPENROUTER", "openrouter/auto"), ("CUSTOM", "auto"),
])
def test_resolver_preserves_router_auto_id_when_chosen_as_workspace_default(
    provider_code: str, auto_model_id: str,
) -> None:
    cipher = ProviderCredentialCipher(b"a" * 32, encryption_key_version=1)
    database = Database(row(
        cipher, connection_id="router-default", provider_code=provider_code,
        model_id=auto_model_id, adapter_type="openai_compatible" if provider_code == "CUSTOM" else provider_code,
        base_url="https://openrouter.ai/api/v1" if provider_code == "OPENROUTER" else "https://gateway.example.com",
    ))
    with PostgresWorkspaceModelResolver(Store(database), cipher).resolve(context(), "text_generation") as resolved:
        assert resolved.model_id == auto_model_id
        assert resolved.provider_code == provider_code


def test_auto_can_be_saved_as_workspace_default_only_when_in_allowed_catalog() -> None:
    database = DefaultsDatabase()
    database.models = [(
        "router-default", "OPENROUTER", "OpenRouter", True, 1, "verified", 3,
        "openrouter/auto", ["text_generation"], "ready", 2,
    )]
    service = PostgresWorkspaceModelDefaultsService(DefaultsStore(database))
    initial = service.read(defaults_context())
    result, replayed = service.save(
        defaults_context(), capability="text_generation", connection_id="router-default",
        model_id="openrouter/auto", expected_version=0, expected_etag=initial["etag"],
        idempotency_key="workspace-auto-default-001",
    )
    assert replayed is False
    assert result["defaults"] == [{
        "capability": "text_generation", "connection_id": "router-default",
        "model_id": "openrouter/auto", "version": 1,
    }]


def test_openrouter_auto_not_in_allowed_catalog_cannot_be_saved_as_default() -> None:
    database = DefaultsDatabase()
    database.models = [(
        "router-default", "OPENROUTER", "OpenRouter", True, 1, "verified", 3,
        "openai/gpt-4.1", ["text_generation"], "ready", 2,
    )]
    service = PostgresWorkspaceModelDefaultsService(DefaultsStore(database))
    initial = service.read(defaults_context())

    with pytest.raises(WorkspaceModelDefaultsError, match="^WORKSPACE_MODEL_DEFAULT_UNAVAILABLE$"):
        service.save(
            defaults_context(), capability="text_generation", connection_id="router-default",
            model_id="openrouter/auto", expected_version=0, expected_etag=initial["etag"],
            idempotency_key="unallowed-router-auto-001",
        )

    assert database.defaults == []
    assert database.idempotency == {}


def test_custom_resolver_propagates_saved_anthropic_adapter_type() -> None:
    cipher = ProviderCredentialCipher(b"x" * 32, encryption_key_version=1)
    database = Database(row(
        cipher, connection_id="custom-anthropic", provider_code="CUSTOM",
        adapter_type="anthropic_compatible",
    ))
    with PostgresWorkspaceModelResolver(Store(database), cipher).resolve(
        context(), "text_generation",
    ) as resolved:
        assert resolved.adapter_type == "anthropic_compatible"
        assert "c.adapter_type" in database.queries[0][0]


def test_resolve_reads_latest_credential_version_on_each_request() -> None:
    cipher = ProviderCredentialCipher(b"v" * 32, encryption_key_version=1)
    database = Database(row(cipher, credential=b"client-key-v1", credential_version=4))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with resolver.resolve(context(), "text_generation") as first:
        assert (first.credential_version, first.credential_text()) == (4, "client-key-v1")
    database.row = row(cipher, credential=b"client-key-v2", credential_version=5)
    with resolver.resolve(context(), "text_generation") as second:
        assert (second.credential_version, second.credential_text()) == (5, "client-key-v2")

    assert len(database.queries) == 2


@pytest.mark.parametrize(
    ("changed", "code"),
    [
        ({"enabled": False}, "PROVIDER_CONNECTION_UNAVAILABLE"),
        ({"verification_status": "unverified"}, "PROVIDER_CONNECTION_UNAVAILABLE"),
        ({"catalog_status": "stale"}, "PROVIDER_MODEL_NOT_READY"),
        ({"effective_capabilities": ["image_understanding"]}, "PROVIDER_CAPABILITY_UNSUPPORTED"),
        ({"provider_code": "OPENAI"}, "PROVIDER_EXECUTION_NOT_READY"),
    ],
)
def test_resolve_fails_closed_when_selected_model_is_not_executable(changed, code) -> None:
    cipher = ProviderCredentialCipher(b"f" * 32, encryption_key_version=1)
    database = Database(row(cipher, **changed))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with pytest.raises(WorkspaceModelUnavailable, match=f"^{code}$"):
        with resolver.resolve(context(), "text_generation"):
            pass


def test_resolve_fails_closed_for_missing_default_or_required_credential() -> None:
    cipher = ProviderCredentialCipher(b"m" * 32, encryption_key_version=1)
    database = Database(None)
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with pytest.raises(WorkspaceModelUnavailable, match="^WORKSPACE_MODEL_DEFAULT_UNAVAILABLE$"):
        with resolver.resolve(context(), "text_generation"):
            pass

    database.row = row(cipher, credential=None, credential_version=0)
    with pytest.raises(WorkspaceModelUnavailable, match="^PROVIDER_CREDENTIAL_REQUIRED$"):
        with resolver.resolve(context(), "text_generation"):
            pass


def test_ollama_default_allows_nullable_credential_without_legacy_fallback() -> None:
    cipher = ProviderCredentialCipher(b"o" * 32, encryption_key_version=1)
    database = Database(row(
        cipher, credential=None, credential_version=0,
        connection_id="ollama-lan", provider_code="OLLAMA",
    ))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with resolver.resolve(context(), "text_generation") as resolved:
        assert resolved.connection_id == "ollama-lan"
        assert resolved.credential_text(required=False) is None
        assert resolved.routing_owner == "provider"
        assert resolved.daon_fallback_allowed is True


def test_custom_keyless_default_allows_nullable_credential() -> None:
    cipher = ProviderCredentialCipher(b"k" * 32, encryption_key_version=1)
    database = Database(row(
        cipher, credential=None, credential_version=0,
        connection_id="custom-keyless", provider_code="CUSTOM",
        credential_requirement="none", adapter_type="openai_compatible",
    ))
    resolver = PostgresWorkspaceModelResolver(Store(database), cipher)

    with resolver.resolve(context(), "text_generation") as resolved:
        assert resolved.credential_text(required=False) is None
        assert resolved.adapter_type == "openai_compatible"


def test_personal_model_uses_only_verified_own_key_even_if_system_key_exists() -> None:
    cipher = ProviderCredentialCipher(bytes(range(32)), encryption_key_version=1)
    database = Database(row(
        cipher, connection_id="upstage-personal", provider_code="UPSTAGE",
        access_mode="personal", verification_status="unverified",
    ))
    database.row = tuple(
        "https://api.upstage.ai/v1" if index == 4 else value
        for index, value in enumerate(database.row)
    )

    class PersonalKeys:
        def resolve_credential(self, **kwargs):
            assert kwargs["user_id"] == "actor-001"
            return UserProviderCredentialResult(bytes(range(8)), "user", 2)

    resolver = PostgresWorkspaceModelResolver(Store(database), cipher, PersonalKeys())
    with resolver.resolve(context(), "text_generation") as selected:
        assert selected.credential_text() == bytes(range(8)).decode("utf-8")
        assert selected.credential_version == 2

    assert "JOIN system_provider_allowed_models" in database.queries[0][0]


@pytest.mark.parametrize(
    ("provider_code", "base_url"),
    [
        ("GROQ", "https://api.groq.com/openai/v1"),
        ("MISTRAL", "https://api.mistral.ai/v1"),
        ("UPSTAGE", "https://api.upstage.ai/v1"),
        ("OPENROUTER", "https://openrouter.ai/api/v1"),
    ],
)
def test_text_resolution_preserves_every_provider_with_an_execution_adapter(
    provider_code: str, base_url: str,
) -> None:
    cipher = ProviderCredentialCipher(b"a" * 32, encryption_key_version=1)
    values = list(row(
        cipher, connection_id=f"{provider_code.lower()}-primary",
        provider_code=provider_code,
    ))
    values[4] = base_url
    database = Database(tuple(values))

    with PostgresWorkspaceModelResolver(Store(database), cipher).resolve(
        context(), "text_generation",
    ) as resolved:
        assert resolved.provider_code == provider_code


class DefaultsDatabase:
    def __init__(self) -> None:
        self.models = [(
            "ollama-lan", "OLLAMA", "LAN Ollama", False, 0, "verified", 3,
            "qwen3:8b", ["text_generation"], "ready", 2,
        )]
        self.defaults = []
        self.idempotency = {}
        self.contexts = []
        self.query_sql = []


class DefaultsConnection:
    def __init__(self, database: DefaultsDatabase) -> None:
        self.database = database

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.database.query_sql.append(normalized)
        if normalized.startswith("SELECT c.connection_id,c.provider_code,c.display_name"):
            assert "JOIN system_provider_allowed_models" in normalized
            assert "c.access_mode='public'" in normalized
            assert "user_provider_credentials" in normalized
            models = self.database.models
            if "u.credential_schema_version=2" in normalized:
                models = [item for item in models if len(item) <= 11 or item[11] != "personal" or (
                    item[12] == 2 and item[13] == "verified"
                )]
            return CursorRows(models)
        if normalized.startswith("SELECT capability,connection_id,model_id,version"):
            return CursorRows(list(self.database.defaults))
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            return Cursor(None)
        if normalized.startswith("SELECT request_fingerprint,result FROM idempotency_records"):
            return Cursor(self.database.idempotency.get(tuple(params)))
        if normalized.startswith("SELECT version FROM workspace_model_defaults"):
            capability = params[2]
            item = next((row for row in self.database.defaults if row[0] == capability), None)
            return Cursor(None if item is None else (item[3],))
        if normalized.startswith("SELECT 1 FROM system_provider_connections"):
            assert "JOIN system_provider_allowed_models" in normalized
            _, _, connection_id, model_id, capability = params
            allowed = any(
                row[0] == connection_id and row[7] == model_id and capability in row[8]
                and (len(row) <= 11 or row[11] != "personal" or (
                    "u.credential_schema_version=2" in normalized
                    and row[12] == 2 and row[13] == "verified"
                ))
                for row in self.database.models
            )
            return Cursor((1,) if allowed else None)
        if normalized.startswith("INSERT INTO workspace_model_defaults"):
            capability, connection_id, model_id, version = params[2], params[3], params[4], params[5]
            self.database.defaults = [row for row in self.database.defaults if row[0] != capability]
            self.database.defaults.append((capability, connection_id, model_id, version))
            return Cursor(None)
        if normalized.startswith("INSERT INTO idempotency_records"):
            key = tuple(params[:5])
            self.database.idempotency[key] = (params[5], getattr(params[6], "obj", params[6]))
            return Cursor(None)
        raise AssertionError(f"unexpected SQL: {normalized}")


class CursorRows(Cursor):
    def __init__(self, rows):
        super().__init__(None)
        self._rows = rows

    def fetchall(self):
        return self._rows


class DefaultsStore:
    def __init__(self, database: DefaultsDatabase) -> None:
        self.database = database

    @contextmanager
    def _transaction(self, access):
        self.database.contexts.append(access)
        yield DefaultsConnection(self.database)


def defaults_context() -> WorkspaceModelDefaultsContext:
    return WorkspaceModelDefaultsContext(
        tenant_id="tenant-001", workspace_id="workspace-001", actor_id="actor-001",
        trace_id="trace-001", policy_version="policy-v1",
    )


def test_workspace_model_defaults_are_safe_versioned_and_durably_replayed() -> None:
    database = DefaultsDatabase()
    service = PostgresWorkspaceModelDefaultsService(DefaultsStore(database))
    initial = service.read(defaults_context())

    assert initial["available_models"][0]["display_name"] == "LAN Ollama"
    assert "base_url" not in repr(initial)
    assert "credential_digest" not in repr(initial)
    assert "encrypted_credential" not in repr(initial)
    result, replayed = service.save(
        defaults_context(), capability="text_generation", connection_id="ollama-lan",
        model_id="qwen3:8b", expected_version=0, expected_etag=initial["etag"],
        idempotency_key="workspace-default-0001",
    )
    retry, was_replayed = service.save(
        defaults_context(), capability="text_generation", connection_id="ollama-lan",
        model_id="qwen3:8b", expected_version=0, expected_etag=initial["etag"],
        idempotency_key="workspace-default-0001",
    )

    assert replayed is False
    assert was_replayed is True
    assert retry == result
    assert result["defaults"] == [{
        "capability": "text_generation", "connection_id": "ollama-lan",
        "model_id": "qwen3:8b", "version": 1,
    }]
    assert database.contexts[-1].workspace_id == "workspace-001"


def test_workspace_model_defaults_reject_reused_key_and_unavailable_model() -> None:
    database = DefaultsDatabase()
    service = PostgresWorkspaceModelDefaultsService(DefaultsStore(database))
    initial = service.read(defaults_context())
    service.save(
        defaults_context(), capability="text_generation", connection_id="ollama-lan",
        model_id="qwen3:8b", expected_version=0, expected_etag=initial["etag"],
        idempotency_key="workspace-default-0002",
    )
    with pytest.raises(WorkspaceModelDefaultsError, match="^IDEMPOTENCY_KEY_REUSED$"):
        service.save(
            defaults_context(), capability="text_generation", connection_id="ollama-lan",
            model_id="different", expected_version=0, expected_etag=initial["etag"],
            idempotency_key="workspace-default-0002",
        )
    next_snapshot = service.read(defaults_context())
    with pytest.raises(WorkspaceModelDefaultsError, match="^WORKSPACE_MODEL_DEFAULT_UNAVAILABLE$"):
        service.save(
            defaults_context(), capability="text_generation", connection_id="ollama-lan",
            model_id="missing-model", expected_version=1, expected_etag=next_snapshot["etag"],
            idempotency_key="workspace-default-0003",
        )


def test_workspace_model_defaults_require_v2_for_personal_but_keep_system_v1() -> None:
    database = DefaultsDatabase()
    system_v1 = ("system-v1", "UPSTAGE", "System v1", True, 1, "verified", 1,
                 "solar", ["text_generation"], "ready", 1, "public", None, None)
    personal_v1 = ("personal-v1", "UPSTAGE", "Legacy personal", False, 0, "unverified", 1,
                   "solar", ["text_generation"], "ready", 1, "personal", 1, "verified")
    personal_v2 = ("personal-v2", "UPSTAGE", "Personal v2", False, 0, "unverified", 1,
                   "solar", ["text_generation"], "ready", 1, "personal", 2, "verified")
    database.models = [system_v1, personal_v1, personal_v2]
    service = PostgresWorkspaceModelDefaultsService(DefaultsStore(database))

    snapshot = service.read(defaults_context())
    assert {item["connection_id"] for item in snapshot["available_models"]} == {
        "system-v1", "personal-v2",
    }
    assert all("u.credential_schema_version=2" in sql for sql in database.query_sql
               if "user_provider_credentials" in sql)

    with pytest.raises(WorkspaceModelDefaultsError, match="^WORKSPACE_MODEL_DEFAULT_UNAVAILABLE$"):
        service.save(
            defaults_context(), capability="text_generation", connection_id="personal-v1",
            model_id="solar", expected_version=0, expected_etag=snapshot["etag"],
            idempotency_key="legacy-personal-default",
        )
    for connection_id in ("personal-v2", "system-v1"):
        isolated = DefaultsDatabase()
        isolated.models = [system_v1, personal_v1, personal_v2]
        isolated_service = PostgresWorkspaceModelDefaultsService(DefaultsStore(isolated))
        current = isolated_service.read(defaults_context())
        isolated_service.save(
            defaults_context(), capability="text_generation", connection_id=connection_id,
            model_id="solar", expected_version=0, expected_etag=current["etag"],
            idempotency_key=f"allowed-{connection_id}",
        )
