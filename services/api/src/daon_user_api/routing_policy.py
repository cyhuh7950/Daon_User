"""B1: explicit candidate policy and immutable preparation, without dispatch.

Observations are supplied by a trusted server port, never by the HTTP policy body.
Missing producers keep auto unavailable. This module cannot call a model.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .data_canon import canonical_json_bytes
from .workspace_model_defaults import WorkspaceModelDefaultsContext

_ID = r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'


class RoutingPolicyError(RuntimeError):
    def __init__(self, code: str, status: int = 422):
        self.code, self.status = code, status
        self.retryable = status == 503
        super().__init__(code)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class RoutingCandidateBody(StrictBody):
    connection_id: str = Field(pattern=_ID)
    model_id: str = Field(min_length=1, max_length=255, pattern=r'^[^\x00-\x20\x7f]+$')
    credential_source: Literal['workspace', 'personal']
    connection_version: int = Field(ge=1)
    catalog_version: int = Field(ge=1)
    credential_version: int = Field(ge=0)
    endpoint_version: int = Field(ge=1)


class RankingProfileBody(StrictBody):
    version: str = Field(pattern=_ID)
    evaluation_set: str = Field(pattern=_ID)
    minimum_quality: int = Field(ge=0, le=10000)
    observation_window: str = Field(pattern=_ID)
    input_class: str = Field(pattern=_ID)
    minimum_samples: int = Field(ge=1, le=10000000)
    max_age_seconds: int = Field(ge=1, le=3600)
    tier_mapping_version: str = Field(pattern=_ID)
    locality_mapping_version: str = Field(pattern=_ID)


class FallbackPolicyBody(StrictBody):
    mode: Literal['transient_only']


class RoutingBudgetBody(StrictBody):
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    run_limit: str = Field(pattern=r'^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$')
    max_attempts: int = Field(ge=1, le=8)


class RoutingPolicyBody(StrictBody):
    schema_version: Literal[1]
    expected_version: int = Field(ge=0)
    enabled: bool
    capability: Literal['text_generation']
    candidates: list[RoutingCandidateBody] = Field(min_length=1, max_length=8)
    ranking_profile: RankingProfileBody
    fallback_policy: FallbackPolicyBody
    budget: RoutingBudgetBody

    @model_validator(mode='after')
    def unique_candidates(self):
        ids = [(c.connection_id, c.model_id) for c in self.candidates]
        if len(set(ids)) != len(ids) or self.budget.max_attempts > len(ids):
            raise ValueError('ROUTING_CANDIDATE_INVALID')
        return self


def validate_precondition(body, if_match, if_none_match):
    if if_match is not None and if_none_match is not None:
        raise RoutingPolicyError('INVALID_REQUEST')
    if if_match is None and if_none_match is None:
        raise RoutingPolicyError('PRECONDITION_REQUIRED', 428)
    if body.expected_version == 0:
        if if_none_match != '*' or if_match is not None:
            raise RoutingPolicyError('VERSION_CONFLICT', 409)
    elif not if_match or if_none_match is not None:
        raise RoutingPolicyError('VERSION_CONFLICT', 409)


def digest(body):
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifiedCandidate:
    """Safe current server metadata. Does not contain endpoint or credential values."""
    connection_id: str
    model_id: str
    deployment_id: str
    connection_version: int
    catalog_version: int
    credential_version: int
    endpoint_version: int
    credential_source: str
    credential_owner: str | None
    # Preparation requires all of these *current* attestations; registration alone is not evidence.
    filter_reasons: tuple[str, ...] = ()
    upper_budget_reference: str | None = None

    def references(self):
        return dict(connection_id=self.connection_id, model_id=self.model_id,
            deployment_id=self.deployment_id, connection_version=self.connection_version,
            catalog_version=self.catalog_version, credential_version=self.credential_version,
            endpoint_version=self.endpoint_version, credential_source=self.credential_source,
            upper_budget_reference=self.upper_budget_reference)


@dataclass(frozen=True, slots=True)
class VerifiedUpperBudget:
    tenant_id: str
    workspace_id: str
    actor_id: str
    version: str
    source_id: str
    currency: str
    run_limit: str
    starts_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedObservation:
    """Internal server producer output. No client observation ingestion endpoint exists."""
    deployment_id: str
    source_id: str
    measurement_version: str
    observed_at: datetime
    expires_at: datetime
    evaluation_set: str
    observation_window: str
    input_class: str
    sample_count: int
    tier_mapping_version: str
    locality_mapping_version: str
    privacy_tier: int
    quality_basis_points: int
    locality_rank: int
    reliability_basis_points: int
    latency_p95_ms: int
    maximum_estimate: str
    currency: str
    pricing_reference: str
    load_basis_points: int
    artifact_digest: str
    license_reference: str
    # Current checks are carried by server producer, not inferred from a ready catalog row.
    authorized: bool
    residency_allowed: bool
    egress_allowed: bool
    health_ready: bool
    capacity_ready: bool
    circuit_closed: bool
    candidate_reference_digest: str
    input_payload_digest: str
    units_version: str

    def safe_dict(self):
        return {name: (getattr(self, name).isoformat() if name in {'observed_at', 'expires_at'}
                       else getattr(self, name)) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class RouteRequestSnapshot:
    notebook_id: str
    payload_digest: str
    source_scope_digest: str
    settings_digest: str
    realm: str
    ruleset_required: bool = False
    ruleset_snapshot_digest: str | None = None

    def __post_init__(self):
        if (not re.fullmatch(_ID, self.notebook_id) or self.realm not in {'local_private', 'cloud_sync', 'organization'}
                or any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in
                       (self.payload_digest, self.source_scope_digest, self.settings_digest))
                or type(self.ruleset_required) is not bool
                or (self.ruleset_snapshot_digest is not None and not re.fullmatch(r'[0-9a-f]{64}', self.ruleset_snapshot_digest))):
            raise RoutingPolicyError('INVALID_REQUEST')


@dataclass(frozen=True, slots=True)
class FrozenRoutePlan:
    """Canonical bytes are immutable; each accessor returns a detached view."""
    canonical_text: str
    digest_sha256: str

    def __post_init__(self):
        try:
            body = json.loads(self.canonical_text)
            if (canonical_json_bytes(body).decode('utf-8') != self.canonical_text
                    or digest(body) != self.digest_sha256 or body.get('schema_version') != 1):
                raise ValueError
        except (TypeError, ValueError):
            raise RoutingPolicyError('ROUTING_PLAN_INVALID') from None

    def as_dict(self):
        return json.loads(self.canonical_text)


class RoutingMetadataPort(Protocol):
    def candidates(self, context, policy: RoutingPolicyBody) -> tuple[VerifiedCandidate, ...]: ...
    def observations(self, context, request, candidates) -> tuple[VerifiedObservation, ...]: ...


def _observation_reason(observation, candidate, profile, budget, now, input_payload_digest):
    if not isinstance(observation, VerifiedObservation):
        return 'OBSERVATION_MISSING'
    o = observation
    if (o.candidate_reference_digest != digest(candidate.references())
            or o.input_payload_digest != input_payload_digest
            or o.units_version != 'basis_points_ms_currency_v1'):
        return 'OBSERVATION_BINDING_INVALID'
    if not candidate.upper_budget_reference:
        return 'BUDGET_UNVERIFIED'
    if o.deployment_id != candidate.deployment_id:
        return 'OBSERVATION_IDENTITY_MISMATCH'
    if (not o.source_id or not o.measurement_version or not o.pricing_reference or not o.license_reference
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', o.artifact_digest)):
        return 'PROVENANCE_UNCONFIRMED'
    if (o.observed_at.tzinfo is None or o.expires_at.tzinfo is None or o.observed_at > now
            or not o.observed_at < o.expires_at or now >= o.expires_at
            or (now - o.observed_at).total_seconds() > profile.max_age_seconds):
        return 'OBSERVATION_STALE'
    if (o.evaluation_set != profile.evaluation_set or o.observation_window != profile.observation_window
            or o.input_class != profile.input_class or o.sample_count < profile.minimum_samples
            or o.tier_mapping_version != profile.tier_mapping_version
            or o.locality_mapping_version != profile.locality_mapping_version):
        return 'OBSERVATION_INCOMPARABLE'
    if (any(type(v) is not int or v < 0 for v in (o.privacy_tier, o.quality_basis_points,
            o.locality_rank, o.reliability_basis_points, o.latency_p95_ms, o.load_basis_points, o.sample_count))
            or any(v > 10000 for v in (o.quality_basis_points, o.reliability_basis_points, o.load_basis_points))):
        return 'OBSERVATION_UNIT_INVALID'
    if o.quality_basis_points < profile.minimum_quality:
        return 'QUALITY_BELOW_MINIMUM'
    if not all(value is True for value in (o.authorized, o.residency_allowed, o.egress_allowed,
            o.health_ready, o.capacity_ready, o.circuit_closed)):
        return 'CURRENT_CANDIDATE_BLOCKED'
    try:
        amount = Decimal(o.maximum_estimate)
        if (type(o.maximum_estimate) is not str or not amount.is_finite() or amount < 0
                or amount.as_tuple().exponent < -6 or o.currency != budget.currency):
            return 'PRICE_UNCONFIRMED'
        if amount > Decimal(budget.run_limit):
            return 'BUDGET_EXCEEDED'
    except (InvalidOperation, ValueError, TypeError):
        return 'PRICE_UNCONFIRMED'
    return None


def build_route_plan(context, request: RouteRequestSnapshot, policy, candidates, observations, *, now):
    """Freeze, never dispatch or reserve. B2 must reauthorize and persist before execution."""
    body = RoutingPolicyBody.model_validate(policy['body'])
    if digest(body.model_dump()) != policy['digest_sha256']:
        raise RoutingPolicyError('ROUTING_PLAN_INVALID')
    if not body.enabled:
        raise RoutingPolicyError('ROUTING_POLICY_DISABLED', 409)
    if request.ruleset_required and not request.ruleset_snapshot_digest:
        raise RoutingPolicyError('RULESET_SNAPSHOT_REQUIRED', 409)
    if now.tzinfo is None:
        raise RoutingPolicyError('INVALID_REQUEST')
    observed = {}
    for observation in observations:
        if observation.deployment_id in observed:
            raise RoutingPolicyError('ROUTING_METADATA_UNAVAILABLE', 503)
        observed[observation.deployment_id] = observation
    ranked, excluded = [], []
    if len(candidates) != len(body.candidates):
        raise RoutingPolicyError('ROUTING_METADATA_UNAVAILABLE', 503)
    for configured, candidate in zip(body.candidates, candidates, strict=True):
        actual = candidate.references()
        expected = configured.model_dump()
        if any(actual[k] != v for k, v in expected.items()):
            raise RoutingPolicyError('ROUTING_CANDIDATE_CHANGED', 409)
        if candidate.credential_source == 'personal' and candidate.credential_owner != context.actor_id:
            raise RoutingPolicyError('ROUTING_CREDENTIAL_DENIED', 403)
        observation = observed.get(candidate.deployment_id)
        reason = (candidate.filter_reasons[0] if candidate.filter_reasons else
            _observation_reason(observation, candidate, body.ranking_profile, body.budget, now, request.payload_digest))
        if request.realm == 'local_private' and observation is not None and observation.locality_rank > 0:
            reason = 'DATA_REALM_BLOCKED'
        if reason:
            excluded.append({'candidate': actual, 'reason': reason})
            continue
        o = observation
        order = (o.privacy_tier, -o.quality_basis_points, o.locality_rank, -o.reliability_basis_points,
                 o.latency_p95_ms, Decimal(o.maximum_estimate), o.load_basis_points, candidate.deployment_id.encode('utf-8'))
        ranked.append((order, {'candidate': actual, 'observation': o.safe_dict(),
            'input_payload_digest': request.payload_digest, 'transformed_payload_digest': None,
            'authorization_state': 'not_prepared', 'budget_reference': body.budget.model_dump()}))
    ranked.sort(key=lambda item: item[0])
    payload = dict(schema_version=1, sorting_version='lexicographic-v1',
        actor_id=context.actor_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        notebook_id=request.notebook_id, input_payload_digest=request.payload_digest,
        source_scope_digest=request.source_scope_digest, settings_digest=request.settings_digest,
        realm=request.realm, ruleset_snapshot_digest=request.ruleset_snapshot_digest,
        policy_version_id=policy['policy_version_id'], policy_digest=policy['digest_sha256'],
        policy_version=policy['version'], authorization_policy_version=context.policy_version,
        candidates=[entry for _, entry in ranked], excluded=excluded,
        budget=body.budget.model_dump(), execution_enabled=False,
        readiness='prepared_execution_disabled' if ranked else 'no_eligible_candidates',
        safe_code='ROUTING_EXECUTION_UNSUPPORTED' if ranked else 'ROUTING_OBSERVATIONS_UNAVAILABLE')
    text = canonical_json_bytes(payload).decode('utf-8')
    return FrozenRoutePlan(text, hashlib.sha256(text.encode()).hexdigest())


class RoutingPolicyService:
    def __init__(self, repository, metadata: RoutingMetadataPort, *, clock=None):
        self.repository, self.metadata = repository, metadata
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def get(self, context):
        return self.repository.get(context)

    def put(self, context, body, *, if_match=None, if_none_match=None, idempotency_key):
        body = RoutingPolicyBody.model_validate(body)
        validate_precondition(body, if_match, if_none_match)
        if not isinstance(idempotency_key, str) or not re.fullmatch(_ID, idempotency_key):
            raise RoutingPolicyError('INVALID_REQUEST')
        # The repository invokes current metadata validation inside the same transaction,
        # including replay, so a revoked actor cannot replay a former grant.
        return self.repository.put(context, body, if_match=if_match, if_none_match=if_none_match,
            idempotency_key=idempotency_key, validate=self.metadata.candidates)

    def prepare(self, context, request, *, policy_version_id, routing_mode='auto'):
        # No resolver/table/producer access for the preexisting pinned path.
        if routing_mode == 'pinned':
            raise RoutingPolicyError('ROUTING_PINNED_UNCHANGED', 409)
        if routing_mode != 'auto':
            raise RoutingPolicyError('INVALID_REQUEST')
        policy = self.repository.get(context)
        if policy['version'] == 0:
            raise RoutingPolicyError('ROUTING_POLICY_ABSENT', 409)
        if policy['policy_version_id'] != policy_version_id:
            raise RoutingPolicyError('VERSION_CONFLICT', 409)
        body = RoutingPolicyBody.model_validate(policy['body'])
        if not body.enabled:
            raise RoutingPolicyError('ROUTING_POLICY_DISABLED', 409)
        candidates = self.metadata.candidates(context, body)
        observations = self.metadata.observations(context, request, candidates)
        return build_route_plan(context, request, policy, candidates, observations, now=self.clock())
