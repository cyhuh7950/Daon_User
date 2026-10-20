import pytest

from daon_user_api.egress_policy import (
    EgressPolicyContext,
    EgressPolicyError,
    EgressPolicyPayload,
    EgressPolicyService,
    ReferenceEgressPolicyRepository,
)


def context(*, tenant_id: str = "org-1", workspace_id: str = "workspace-1") -> EgressPolicyContext:
    return EgressPolicyContext(
        tenant_id=tenant_id,
        organization_id=tenant_id,
        workspace_id=workspace_id,
        actor_id="admin-1",
        trace_id="trace-1",
        authorization_policy_version="auth-v1",
    )


def test_development_stage_selection_and_registered_destinations():
    from daon_user_api.development_egress_defaults import development_stage_enabled, development_payload
    for profile, explicit, expected in (("production", True, True), ("development", False, False),
            ("test", None, True), ("development", None, True), ("production", None, False)):
        assert development_stage_enabled(profile, explicit) is expected
    payload = development_payload([
        ("OPENAI", "OPENAI", "https://api.openai.com/v1", True),
        ("OMNIROUTE", "openai_compatible", "https://omni.example/v1", True),
        ("OLLAMA", "OLLAMA", "http://internal", True),
        ("CUSTOM", "asr_base_native", "http://asr-base", True),
    ])
    assert payload.mode == "allow_approved_external"
    assert payload.allowed_destinations == ("api.openai.com", "omni.example")
    assert payload.allowed_provider_kinds == ("external_api",)
    assert payload.classification == "internal" and payload.max_bytes == 65536
    assert payload.masking_required and payload.redaction_required
    assert payload.required_approver == "organization_admin"
    assert payload.question_step_up_required is False


@pytest.mark.parametrize('rows', [[], [("OLLAMA", "OLLAMA", "http://internal", True)],
    [("OPENAI", "OPENAI", "https://other.example/v1", True)],
    [("CUSTOM", "openai_compatible", "https://*.example/v1", True)],
    [("UNKNOWN", "openai_compatible", "https://provider.example/v1", True)],
    [("OMNIROUTE", "openai_compatible", "https://user:password@provider.example/v1", True)],
    [("OMNIROUTE", "openai_compatible", "https://provider.example/v1", False)],
    [("CUSTOM", "openai_compatible", f"https://provider-{i}.example/v1", True) for i in range(65)]])
def test_development_destinations_fail_closed(rows):
    from daon_user_api.development_egress_defaults import development_payload
    assert development_payload(rows) is None


def development_maintenance_fixture(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from daon_user_api import development_egress_defaults as defaults
    service, repository = seeded_service()
    creators = {scope: ["runtime:seed_scope", "runtime:seed_scope"] for scope in ("organization", "workspace")}
    reads = []
    class Store:
        _development_egress_defaults = True
        @contextmanager
        def _transaction(self, cloud_context):
            def execute(query, params=()):
                reads.append(query)
                if "system_provider_connections" in query:
                    return SimpleNamespace(fetchall=lambda: [("OPENAI", "OPENAI", "https://api.openai.com/v1", True)])
                scope = params[2]
                view = repository.current(context(), scope)
                row = (view.policy_version_id, view.binding_id, view.policy_version, view.binding_version,
                    view.payload.canonical_text, view.payload.digest_sha256, *creators[scope])
                return SimpleNamespace(fetchone=lambda: row)
            yield SimpleNamespace(execute=execute)
    store = Store()
    monkeypatch.setattr(defaults, "PostgresEgressPolicyRepository", lambda selected: repository)
    return defaults, store, repository, creators, reads


def test_development_maintenance_activates_organization_first_and_is_idempotent(monkeypatch):
    defaults, store, repository, creators, reads = development_maintenance_fixture(monkeypatch)
    order = []
    original = repository.create_and_activate
    def activate(ctx, scope, *args):
        order.append(scope)
        return original(ctx, scope, *args)
    monkeypatch.setattr(repository, "create_and_activate", activate)
    assert defaults.transition_development_defaults(store, context(), expected_etags={scope: repository.current(context(), scope).etag for scope in ("organization", "workspace")}) == ("organization", "workspace")
    assert order == ["organization", "workspace"]
    assert defaults.transition_development_defaults(store, context(), expected_etags={scope: repository.current(context(), scope).etag for scope in ("organization", "workspace")}) == ()
    assert repository.write_count == 2
    assert all("encrypted_credential" not in query for query in reads)


@pytest.mark.parametrize('kind', ['explicit-version', 'mixed-provenance', 'unknown-creator', 'custom-payload', 'nondevelopment'])
def test_development_maintenance_preserves_explicit_or_unproven_defaults(monkeypatch, kind):
    defaults, store, repository, creators, reads = development_maintenance_fixture(monkeypatch)
    for scope in ("organization", "workspace"):
        if kind == 'explicit-version':repository.corrupt_current(context(), scope, policy_version=35, binding_version=35)
        if kind == 'mixed-provenance':creators[scope][1] = "migration:0044"
        if kind == 'unknown-creator':creators[scope] = ["user", "user"]
        if kind == 'custom-payload':repository.corrupt_current(context(), scope, payload=allow())
    if kind == 'nondevelopment':store._development_egress_defaults = False
    assert defaults.transition_development_defaults(store, context(), expected_etags={scope: repository.current(context(), scope).etag for scope in ("organization", "workspace")}) == ()
    assert repository.write_count == 0
    if kind == 'nondevelopment':assert reads == []


def test_development_maintenance_requires_exact_snapshot_etag_and_repository_cas(monkeypatch):
    defaults, store, repository, creators, reads = development_maintenance_fixture(monkeypatch)
    with pytest.raises(EgressPolicyError, match='VERSION_CONFLICT'):
        defaults.transition_development_defaults(store, context(), expected_etags={})
    assert repository.write_count == 0
    pins = {scope: repository.current(context(), scope).etag for scope in ("organization", "workspace")}
    original = repository.create_and_activate
    def raced(ctx, scope, *args):
        repository.corrupt_current(ctx, scope, policy_version=35, binding_version=35)
        return original(ctx, scope, *args)
    monkeypatch.setattr(repository, 'create_and_activate', raced)
    with pytest.raises(EgressPolicyError, match='VERSION_CONFLICT'):
        defaults.transition_development_defaults(store, context(), expected_etags=pins)
    assert repository.write_count == 0


def deny() -> EgressPolicyPayload:
    return EgressPolicyPayload.deny_external()


def test_question_step_up_three_modes_strict_and_canonical_compatible():
    from dataclasses import replace
    from itertools import product
    from daon_user_api.egress_policy import resolve_effective_payload
    legacy = allow()
    assert replace(legacy, question_step_up_required=True).canonical_text == legacy.canonical_text
    assert 'question_step_up_required' not in legacy.as_dict()
    without = replace(legacy, question_step_up_required=False)
    assert without.as_dict()['question_step_up_required'] is False
    raw = EgressPolicyPayload(**without.as_dict())
    assert resolve_effective_payload(raw, raw)[0].as_dict() == without.as_dict()
    for left, right in product((deny(), legacy, without), repeat=2):
        actual, locked = resolve_effective_payload(left, right)
        if 'deny_external' in (left.mode, right.mode):
            assert actual == deny()
        else:
            assert actual.question_step_up_required is (left.question_step_up_required or right.question_step_up_required)
    for value in (None, 'false', 0, 1, []):
        with pytest.raises(EgressPolicyError):replace(legacy, question_step_up_required=value)
    with pytest.raises(EgressPolicyError):replace(deny(), question_step_up_required=False)
    assert deny().digest_sha256 == 'caf695f3de7e3e05feb024b3ff4b8b14cbfad5318b885ac15d8e4da25b819d7f'


def test_effective_question_step_up_false_is_serialized_and_true_is_omitted():
    from dataclasses import replace
    service, repository = seeded_service()
    for scope in ('organization', 'workspace'):
        repository.corrupt_current(context(), scope, payload=replace(allow(), question_step_up_required=False))
    view = service.get_effective(context())
    assert view.question_step_up_required is False
    assert view.frozen_context()['question_step_up_required'] is False
    repository.corrupt_current(context(), 'organization', payload=allow())
    view = service.get_effective(context())
    assert view.question_step_up_required is True
    assert 'question_step_up_required' not in view.frozen_context()


def allow() -> EgressPolicyPayload:
    return EgressPolicyPayload(
        mode="allow_approved_external",
        allowed_provider_kinds=("external_api",),
        allowed_destinations=("provider.example",),
        classification="internal",
        max_bytes=4096,
        masking_required=True,
        redaction_required=True,
        required_approver="organization_admin",
    )


def seeded_service() -> tuple[EgressPolicyService, ReferenceEgressPolicyRepository]:
    repository = ReferenceEgressPolicyRepository()
    service = EgressPolicyService(repository)
    repository.seed(context(), scope_type="organization", payload=deny())
    repository.seed(context(), scope_type="workspace", payload=deny())
    return service, repository


def test_effective_policy_is_fail_closed_for_missing_inactive_stale_or_wrong_scope() -> None:
    service = EgressPolicyService(ReferenceEgressPolicyRepository())
    with pytest.raises(EgressPolicyError, match="EGRESS_POLICY_UNAVAILABLE"):
        service.get_effective(context())

    service, repository = seeded_service()
    repository.corrupt_current(context(), "workspace", active=False)
    with pytest.raises(EgressPolicyError, match="EGRESS_POLICY_STALE"):
        service.get_effective(context())

    service, repository = seeded_service()
    repository.corrupt_current(context(), "workspace", policy_state="superseded")
    with pytest.raises(EgressPolicyError, match="EGRESS_POLICY_STALE"):
        service.get_effective(context())

    service, repository = seeded_service()
    repository.corrupt_current(context(), "workspace", organization_id="org-other")
    with pytest.raises(EgressPolicyError, match="EGRESS_POLICY_UNAVAILABLE"):
        service.get_effective(context())


def test_organization_deny_has_precedence_and_workspace_cannot_relax_it() -> None:
    service, repository = seeded_service()
    projection = service.get_effective(context())
    assert projection.mode == "deny_external"
    assert projection.parent_locked is True
    assert projection.organization_etag == repository.current(context(), "organization").etag
    assert projection.workspace_etag == repository.current(context(), "workspace").etag
    assert projection.organization_policy == deny().as_dict()

    current = repository.current(context(), "workspace")
    with pytest.raises(EgressPolicyError, match="EGRESS_POLICY_DENIED"):
        service.create_and_activate(
            context(),
            scope_type="workspace",
            payload=allow(),
            expected_etag=current.etag,
            idempotency_key="idem-workspace-allow",
        )
    assert repository.audit_outcomes[-1] == ("egress_policy.activate", "denied")


def test_create_activate_requires_exact_etag_and_idempotency_fingerprint() -> None:
    service, repository = seeded_service()
    current = repository.current(context(), "organization")
    created = service.create_and_activate(
        context(), scope_type="organization", payload=allow(),
        expected_etag=current.etag, idempotency_key="idem-org-1",
    )
    replay = service.create_and_activate(
        context(), scope_type="organization", payload=allow(),
        expected_etag=current.etag, idempotency_key="idem-org-1",
    )
    assert replay == created
    assert repository.write_count == 1
    assert repository.audit_outcomes[-1] == ("egress_policy.activate", "succeeded")

    with pytest.raises(EgressPolicyError, match="IDEMPOTENCY_KEY_REUSED"):
        service.create_and_activate(
            context(), scope_type="organization", payload=deny(),
            expected_etag=current.etag, idempotency_key="idem-org-1",
        )
    with pytest.raises(EgressPolicyError, match="VERSION_CONFLICT"):
        service.create_and_activate(
            context(), scope_type="organization", payload=deny(),
            expected_etag='"egress-policy:wrong"', idempotency_key="idem-org-2",
        )


def test_effective_allow_is_intersection_of_authoritative_organization_and_workspace_values() -> None:
    repository = ReferenceEgressPolicyRepository()
    service = EgressPolicyService(repository)
    organization = allow()
    workspace = EgressPolicyPayload(
        mode="allow_approved_external",
        allowed_provider_kinds=("external_api", "server_internal"),
        allowed_destinations=("provider.example", "other.example"),
        classification="confidential",
        max_bytes=2048,
        masking_required=False,
        redaction_required=False,
        required_approver="workspace_manager",
    )
    repository.seed(context(), scope_type="organization", payload=organization)
    repository.seed(context(), scope_type="workspace", payload=workspace)

    effective = service.get_effective(context())
    assert effective.mode == "allow_approved_external"
    assert effective.allowed_provider_kinds == ("external_api",)
    assert effective.allowed_destinations == ("provider.example",)
    assert effective.max_bytes == 2048
    assert effective.classification == "confidential"
    assert effective.masking_required is True
    assert effective.redaction_required is True
    assert effective.required_approver == "organization_admin"
    assert effective.fingerprint.startswith("sha256:")
    assert effective.organization_policy["allowed_provider_kinds"] == ["external_api"]
    assert effective.workspace_policy["max_bytes"] == 2048
