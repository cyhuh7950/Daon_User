from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class RoutingContext:
    actor_id: str
    tenant_id: str
    workspace_id: str
    mode: str
    required_role: str
    data_realm: str
    external_egress_allowed: bool
    policy_version: str
    cost_limit: float
    estimated_cost: float
    payload_bytes: int


@dataclass(frozen=True)
class CandidateDeployment:
    deployment_id: str
    artifact_digest: str
    role: str
    data_realm: str
    health: str
    provider_kind: str


@dataclass(frozen=True)
class EgressDecision:
    destination: str
    payload_bytes: int
    allowed: bool
    reason: str
    policy_version: str


@dataclass(frozen=True)
class RoutingResult:
    status: str
    code: str | None
    deployment_id: str | None
    artifact_digest: str | None
    policy_version: str
    egress: EgressDecision


_SERVER_INTERNAL: Final = "server_internal"


def _blocked(context: RoutingContext, code: str, reason: str) -> RoutingResult:
    return RoutingResult(
        status="policy_blocked",
        code=code,
        deployment_id=None,
        artifact_digest=None,
        policy_version=context.policy_version,
        egress=EgressDecision(
            destination="unknown",
            payload_bytes=context.payload_bytes,
            allowed=False,
            reason=reason,
            policy_version=context.policy_version,
        ),
    )


def route_single_model(
    context: RoutingContext,
    candidates: list[CandidateDeployment],
) -> RoutingResult:
    if context.estimated_cost > context.cost_limit:
        return _blocked(context, "COST_LIMIT_EXCEEDED", "frozen cost limit exceeded")

    allowed: list[CandidateDeployment] = []
    egress_denied = False
    candidates_to_consider = candidates[:1] if context.mode == "pinned" else candidates
    for candidate in candidates_to_consider:
        if candidate.provider_kind == "external_api" and context.data_realm == "local_private" and not context.external_egress_allowed:
            egress_denied = True
            continue
        if candidate.role != context.required_role or candidate.data_realm != context.data_realm:
            continue
        if candidate.health != "ready":
            continue
        if candidate.provider_kind == "external_api" and not context.external_egress_allowed:
            egress_denied = True
            continue
        allowed.append(candidate)

    if not allowed:
        if egress_denied:
            return _blocked(context, "EXTERNAL_EGRESS_DENIED", "external egress is not allowed")
        return RoutingResult(
            status="failed",
            code="NO_AVAILABLE_DEPLOYMENT",
            deployment_id=None,
            artifact_digest=None,
            policy_version=context.policy_version,
            egress=EgressDecision(
                destination="unknown",
                payload_bytes=context.payload_bytes,
                allowed=False,
                reason="no ready candidate matched the frozen context",
                policy_version=context.policy_version,
            ),
        )

    selected = allowed[0]
    destination = selected.provider_kind or _SERVER_INTERNAL
    return RoutingResult(
        status="selected",
        code=None,
        deployment_id=selected.deployment_id,
        artifact_digest=selected.artifact_digest,
        policy_version=context.policy_version,
        egress=EgressDecision(
            destination=destination,
            payload_bytes=context.payload_bytes,
            allowed=True,
            reason="approved candidate matched frozen context",
            policy_version=context.policy_version,
        ),
    )
