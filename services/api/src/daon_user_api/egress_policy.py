"""Versioned organization/workspace egress policy domain and service."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re
from threading import RLock
from typing import Mapping, Protocol

from .data_canon import canonical_json_bytes


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DESTINATION = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_CLASSIFICATION_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
_APPROVER_RANK = {"workspace_manager": 0, "organization_admin": 1}
_PROVIDER_KINDS = frozenset({"external_api", "server_internal", "local_runtime"})


class EgressPolicyError(ValueError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EgressPolicyContext:
    tenant_id: str
    organization_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    authorization_policy_version: str

    def __post_init__(self) -> None:
        values = (
            self.tenant_id, self.organization_id, self.workspace_id,
            self.actor_id, self.trace_id, self.authorization_policy_version,
        )
        if any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value) for value in values):
            raise EgressPolicyError("EGRESS_POLICY_CONTEXT_INVALID")
        if self.tenant_id != self.organization_id:
            raise EgressPolicyError("EGRESS_POLICY_SCOPE_MISMATCH")


@dataclass(frozen=True, slots=True)
class EgressPolicyPayload:
    mode: str
    allowed_provider_kinds: tuple[str, ...]
    allowed_destinations: tuple[str, ...]
    classification: str
    max_bytes: int
    masking_required: bool
    redaction_required: bool
    required_approver: str

    def __post_init__(self) -> None:
        if self.mode not in {"deny_external", "allow_approved_external"}:
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if self.classification not in _CLASSIFICATION_RANK:
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if self.required_approver not in _APPROVER_RANK:
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if not isinstance(self.max_bytes, int) or isinstance(self.max_bytes, bool) or not 0 <= self.max_bytes <= 104_857_600:
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if not isinstance(self.masking_required, bool) or not isinstance(self.redaction_required, bool):
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if len(self.allowed_provider_kinds) > 32 or len(self.allowed_destinations) > 64:
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if len(set(self.allowed_provider_kinds)) != len(self.allowed_provider_kinds):
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if any(value not in _PROVIDER_KINDS for value in self.allowed_provider_kinds):
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if len(set(self.allowed_destinations)) != len(self.allowed_destinations):
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if any(not _DESTINATION.fullmatch(value) for value in self.allowed_destinations):
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")
        if self.mode == "deny_external" and (
            self.allowed_provider_kinds or self.allowed_destinations or self.max_bytes != 0
        ):
            raise EgressPolicyError("EGRESS_POLICY_PAYLOAD_INVALID")

    @classmethod
    def deny_external(cls) -> "EgressPolicyPayload":
        return cls(
            mode="deny_external", allowed_provider_kinds=(), allowed_destinations=(),
            classification="restricted", max_bytes=0, masking_required=True,
            redaction_required=True, required_approver="organization_admin",
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed_destinations": list(self.allowed_destinations),
            "allowed_provider_kinds": list(self.allowed_provider_kinds),
            "classification": self.classification,
            "masking_required": self.masking_required,
            "max_bytes": self.max_bytes,
            "mode": self.mode,
            "redaction_required": self.redaction_required,
            "required_approver": self.required_approver,
        }

    @property
    def canonical_text(self) -> str:
        return canonical_json_bytes(self.as_dict()).decode("utf-8")

    @property
    def digest_sha256(self) -> str:
        return hashlib.sha256(self.canonical_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EgressPolicyBindingView:
    tenant_id: str
    organization_id: str
    workspace_id: str | None
    scope_type: str
    policy_version_id: str
    policy_version: int
    policy_state: str
    binding_id: str
    binding_version: int
    active: bool
    current: bool
    payload: EgressPolicyPayload

    @property
    def etag(self) -> str:
        return f'"egress-policy:{self.scope_type}:{self.binding_version}:{self.payload.digest_sha256}"'


@dataclass(frozen=True, slots=True)
class EffectiveEgressPolicy:
    organization_policy_version_id: str
    organization_binding_id: str
    workspace_policy_version_id: str
    workspace_binding_id: str
    mode: str
    allowed_provider_kinds: tuple[str, ...]
    allowed_destinations: tuple[str, ...]
    classification: str
    max_bytes: int
    masking_required: bool
    redaction_required: bool
    required_approver: str
    parent_locked: bool
    fingerprint: str
    etag: str
    organization_etag: str
    workspace_etag: str
    organization_policy: Mapping[str, object]
    workspace_policy: Mapping[str, object]

    def frozen_context(self) -> dict[str, object]:
        return {
            "organization_policy_version_id": self.organization_policy_version_id,
            "organization_binding_id": self.organization_binding_id,
            "workspace_policy_version_id": self.workspace_policy_version_id,
            "workspace_binding_id": self.workspace_binding_id,
            "mode": self.mode,
            "allowed_provider_kinds": list(self.allowed_provider_kinds),
            "allowed_destinations": list(self.allowed_destinations),
            "classification": self.classification,
            "max_bytes": self.max_bytes,
            "masking_required": self.masking_required,
            "redaction_required": self.redaction_required,
            "required_approver": self.required_approver,
            "fingerprint": self.fingerprint,
        }


class EgressPolicyRepository(Protocol):
    def current(self, context: EgressPolicyContext, scope_type: str) -> EgressPolicyBindingView: ...
    def create_and_activate(
        self, context: EgressPolicyContext, scope_type: str, payload: EgressPolicyPayload,
        expected_etag: str, idempotency_key: str,
    ) -> EgressPolicyBindingView: ...
    def record_denial(self, context: EgressPolicyContext, action: str, code: str) -> None: ...


def _validate_binding(
    context: EgressPolicyContext, binding: EgressPolicyBindingView, scope_type: str,
) -> None:
    expected_workspace = None if scope_type == "organization" else context.workspace_id
    if (
        binding.tenant_id != context.tenant_id
        or binding.organization_id != context.organization_id
        or binding.workspace_id != expected_workspace
        or binding.scope_type != scope_type
    ):
        raise EgressPolicyError("EGRESS_POLICY_UNAVAILABLE", 503)
    if not binding.active or not binding.current or binding.policy_state != "active":
        raise EgressPolicyError("EGRESS_POLICY_STALE", 503)


def resolve_effective_payload(
    organization: EgressPolicyPayload, workspace: EgressPolicyPayload,
) -> tuple[EgressPolicyPayload, bool]:
    parent_locked = organization.mode == "deny_external"
    if parent_locked or workspace.mode == "deny_external":
        return EgressPolicyPayload.deny_external(), parent_locked
    return EgressPolicyPayload(
        mode="allow_approved_external",
        allowed_provider_kinds=tuple(sorted(set(organization.allowed_provider_kinds) & set(workspace.allowed_provider_kinds))),
        allowed_destinations=tuple(sorted(set(organization.allowed_destinations) & set(workspace.allowed_destinations))),
        classification=max((organization.classification, workspace.classification), key=_CLASSIFICATION_RANK.__getitem__),
        max_bytes=min(organization.max_bytes, workspace.max_bytes),
        masking_required=organization.masking_required or workspace.masking_required,
        redaction_required=organization.redaction_required or workspace.redaction_required,
        required_approver=max((organization.required_approver, workspace.required_approver), key=_APPROVER_RANK.__getitem__),
    ), False


class EgressPolicyService:
    def __init__(self, repository: EgressPolicyRepository) -> None:
        self._repository = repository

    def get_effective(self, context: EgressPolicyContext) -> EffectiveEgressPolicy:
        try:
            organization = self._repository.current(context, "organization")
            workspace = self._repository.current(context, "workspace")
        except EgressPolicyError:
            raise
        _validate_binding(context, organization, "organization")
        _validate_binding(context, workspace, "workspace")
        payload, parent_locked = resolve_effective_payload(organization.payload, workspace.payload)
        frozen = {
            "organization_policy_version_id": organization.policy_version_id,
            "organization_binding_id": organization.binding_id,
            "workspace_policy_version_id": workspace.policy_version_id,
            "workspace_binding_id": workspace.binding_id,
            **payload.as_dict(),
        }
        fingerprint = "sha256:" + hashlib.sha256(canonical_json_bytes(frozen)).hexdigest()
        etag_seed = f"{organization.etag}|{workspace.etag}|{fingerprint}"
        etag = f'"egress-effective:{hashlib.sha256(etag_seed.encode()).hexdigest()}"'
        return EffectiveEgressPolicy(
            organization.policy_version_id, organization.binding_id,
            workspace.policy_version_id, workspace.binding_id,
            payload.mode, payload.allowed_provider_kinds, payload.allowed_destinations,
            payload.classification, payload.max_bytes, payload.masking_required,
            payload.redaction_required, payload.required_approver, parent_locked,
            fingerprint, etag, organization.etag, workspace.etag,
            organization.payload.as_dict(), workspace.payload.as_dict(),
        )

    def create_and_activate(
        self, context: EgressPolicyContext, *, scope_type: str,
        payload: EgressPolicyPayload, expected_etag: str, idempotency_key: str,
    ) -> EgressPolicyBindingView:
        if scope_type not in {"organization", "workspace"}:
            raise EgressPolicyError("EGRESS_POLICY_SCOPE_INVALID")
        if scope_type == "workspace" and payload.mode == "allow_approved_external":
            organization = self._repository.current(context, "organization")
            _validate_binding(context, organization, "organization")
            if organization.payload.mode == "deny_external":
                self._repository.record_denial(context, "egress_policy.activate", "EGRESS_POLICY_DENIED")
                raise EgressPolicyError("EGRESS_POLICY_DENIED", 403)
        try:
            return self._repository.create_and_activate(
                context, scope_type, payload, expected_etag, idempotency_key,
            )
        except EgressPolicyError as error:
            if error.code == "VERSION_CONFLICT":
                self._repository.record_denial(
                    context, "egress_policy.activate", "VERSION_CONFLICT",
                )
            raise


class ReferenceEgressPolicyRepository:
    """Deterministic repository used by domain tests; production uses PostgreSQL."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._current: dict[tuple[str, str, str, str | None], EgressPolicyBindingView] = {}
        self._idempotency: dict[tuple[str, str, str, str], tuple[str, EgressPolicyBindingView]] = {}
        self.audit_outcomes: list[tuple[str, str]] = []
        self.write_count = 0

    @staticmethod
    def _key(context: EgressPolicyContext, scope_type: str) -> tuple[str, str, str, str | None]:
        return (
            context.tenant_id, context.organization_id, scope_type,
            None if scope_type == "organization" else context.workspace_id,
        )

    def seed(
        self, context: EgressPolicyContext, *, scope_type: str, payload: EgressPolicyPayload,
    ) -> EgressPolicyBindingView:
        key = self._key(context, scope_type)
        workspace_id = key[-1]
        view = EgressPolicyBindingView(
            context.tenant_id, context.organization_id, workspace_id, scope_type,
            f"policy-{scope_type}-1", 1, "active", f"binding-{scope_type}-1", 1,
            True, True, payload,
        )
        self._current[key] = view
        return view

    def current(self, context: EgressPolicyContext, scope_type: str) -> EgressPolicyBindingView:
        try:
            return self._current[self._key(context, scope_type)]
        except KeyError:
            raise EgressPolicyError("EGRESS_POLICY_UNAVAILABLE", 503) from None

    def corrupt_current(self, context: EgressPolicyContext, scope_type: str, **changes: object) -> None:
        key = self._key(context, scope_type)
        self._current[key] = replace(self._current[key], **changes)

    def create_and_activate(
        self, context: EgressPolicyContext, scope_type: str, payload: EgressPolicyPayload,
        expected_etag: str, idempotency_key: str,
    ) -> EgressPolicyBindingView:
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "scope_type": scope_type, "payload": payload.as_dict(), "expected_etag": expected_etag,
        })).hexdigest()
        replay_key = (context.tenant_id, context.actor_id, scope_type, idempotency_key)
        with self._lock:
            replay = self._idempotency.get(replay_key)
            if replay is not None:
                if replay[0] != fingerprint:
                    raise EgressPolicyError("IDEMPOTENCY_KEY_REUSED", 409)
                return replay[1]
            current = self.current(context, scope_type)
            if current.etag != expected_etag:
                raise EgressPolicyError("VERSION_CONFLICT", 409)
            version = current.binding_version + 1
            workspace_id = None if scope_type == "organization" else context.workspace_id
            stored = EgressPolicyBindingView(
                context.tenant_id, context.organization_id, workspace_id, scope_type,
                f"policy-{scope_type}-{version}", version, "active",
                f"binding-{scope_type}-{version}", version, True, True, payload,
            )
            self._current[self._key(context, scope_type)] = stored
            self._idempotency[replay_key] = (fingerprint, stored)
            self.audit_outcomes.append(("egress_policy.activate", "succeeded"))
            self.write_count += 1
            return stored

    def record_denial(self, context: EgressPolicyContext, action: str, code: str) -> None:
        del context, code
        self.audit_outcomes.append((action, "denied"))
