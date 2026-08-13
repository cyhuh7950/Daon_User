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


def deny() -> EgressPolicyPayload:
    return EgressPolicyPayload.deny_external()


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
