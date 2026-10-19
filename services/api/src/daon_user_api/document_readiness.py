"""문서 Readiness R1: 닫힌 서버 내부 값과 부작용 없는 재처리 판정.

이름의 Validated/current는 DB 검증을 수행했다는 증명이 아니다. 호출 adapter가
실제 관측·현재 인가/Source·의미별 digest·최신 sequence·억제 이력을 다시 읽어야
한다. 이 모듈은 외부 호출/원장/모델 선택/Run 생성/HTTP 멱등을 구현하지 않는다.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Literal

from .document_understanding_adapter import DocumentUnderstandingError


Mode = Literal["auto", "local_only", "pinned", "legacy_unknown"]
Capability = Literal["text_generation", "image_understanding", "document_parsing", "speech_to_text"]
FailureKind = Literal["connection_availability", "role_runtime", "non_availability", "unknown"]
ProbeKind = Literal["provider_connection_check", "native_asr_ready", "model_catalog_refresh"]
Action = Literal["enqueue", "block_cost", "block_policy", "deny", "suppress", "expire", "defer"]

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_EVENT = re.compile(r"rdy-[0-9a-f]{32}")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,255}")
_CAPABILITIES = ("text_generation", "image_understanding", "document_parsing", "speech_to_text")
_SCOPE_FIELDS = ("tenant_id", "workspace_id", "actor_id", "source_version_id")
_DECISIONS = {
    "READY_REPROCESS_ALLOWED": ("enqueue", True),
    "READINESS_SCOPE_NOT_CONFIGURED": ("deny", False),
    "READINESS_MODE_UNCONFIRMED": ("defer", False),
    "READINESS_MODE_MANUAL": ("suppress", False),
    "READINESS_ROLE_UNKNOWN": ("defer", False),
    "READINESS_DEPENDENCY_CHANGED": ("defer", False),
    "READINESS_NOT_READY": ("defer", False),
    "READINESS_OBSERVATION_EXPIRED": ("expire", False),
    "READINESS_ACCESS_DENIED": ("deny", False),
    "READINESS_SOURCE_CHANGED": ("deny", False),
    "READINESS_POLICY_BLOCKED": ("block_policy", True),
    "COST_LIMIT_EXCEEDED": ("block_cost", True),
    "READINESS_COST_UNCONFIRMED": ("defer", False),
    "READINESS_APPROVAL_MISSING": ("defer", False),
    "READINESS_ACTIVE_RUN_EXISTS": ("suppress", False),
    "READINESS_CONTEXT_SUPPRESSED": ("suppress", False),
    "READINESS_BACKOFF_PENDING": ("defer", False),
}


def _invalid() -> None:
    # 입력/원문/자격/내부 ID를 예외에 반사하지 않는다.
    raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")


def _text(value: object, pattern: re.Pattern[str]) -> None:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _invalid()


def _choice(value: object, allowed: tuple[str, ...], *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if type(value) is not str or value not in allowed:
        _invalid()


def _utc(value: object) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid()


def _integer(value: object, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        _invalid()


def _optional_text(value: object, pattern: re.Pattern[str]) -> None:
    if value is not None:
        _text(value, pattern)


@dataclass(frozen=True, slots=True, repr=False)
class ReadinessModeProof:
    mode: Mode
    origin: Literal["request", "legacy_unknown"]

    def __post_init__(self) -> None:
        _choice(self.mode, ("auto", "local_only", "pinned", "legacy_unknown"))
        _choice(self.origin, ("request", "legacy_unknown"))
        if (self.mode == "legacy_unknown") != (self.origin == "legacy_unknown"):
            _invalid()


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _invalid_json_constant(_: str) -> None:
    _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class InternalCostExclusionPolicy:
    """운영자가 지정한 내부 실행의 장치 운영비 제외 근거. 가격/인가 증거 아님."""
    tenant_id: str
    workspace_id: str
    actor_id: str
    source_version_id: str
    capability: Capability
    connection_id: str
    model_id: str
    configuration_version: int
    endpoint: str
    policy_version: str

    def __post_init__(self):
        for name in (*_SCOPE_FIELDS, "connection_id"):
            _text(getattr(self, name), _ID)
        _choice(self.capability, _CAPABILITIES)
        _integer(self.configuration_version, 1)
        if (type(self.model_id) is not str or not 1 <= len(self.model_id) <= 256
                or any(ord(c) < 32 or ord(c) == 127 for c in self.model_id)
                or type(self.policy_version) is not str or self.policy_version != "internal-operating-cost-excluded-v1"):
            _invalid()
        from .provider_settings import validate_provider_base_url, ProviderSettingsError
        try:
            if (type(self.endpoint) is not str or len(self.endpoint) > 2048 or "?" in self.endpoint or "#" in self.endpoint
                    or validate_provider_base_url("OLLAMA", self.endpoint) != self.endpoint):
                _invalid()
        except (ProviderSettingsError, ValueError, TypeError):
            _invalid()

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True, repr=False)
class ReadinessDispatcherSettings:
    scopes: frozenset[tuple[str, str, str, str]] = frozenset()
    backoff_seconds: int = 60
    event_ttl_seconds: int = 300
    future_tolerance_seconds: int = 5
    max_batch: int = 32
    max_seconds: int = 2
    internal_cost_policies: tuple[InternalCostExclusionPolicy, ...] = ()

    def __post_init__(self) -> None:
        if type(self.scopes) is not frozenset or len(self.scopes) > 100:
            _invalid()
        for scope in self.scopes:
            if type(scope) is not tuple or len(scope) != 4:
                _invalid()
            for value in scope:
                _text(value, _ID)
        if (type(self.internal_cost_policies) is not tuple or len(self.internal_cost_policies) > 100
                or any(type(p) is not InternalCostExclusionPolicy for p in self.internal_cost_policies)):
            _invalid()
        targets = []
        for policy in self.internal_cost_policies:
            policy.__post_init__()
            targets.append(tuple(getattr(policy, k) for k in (*_SCOPE_FIELDS, "capability")))
        if len(set(targets)) != len(targets):
            _invalid()
        # 현재 승인된 운영 상수다. 임의 환경변수/새 튜닝 범위를 열지 않는다.
        for value, expected in ((self.backoff_seconds, 60), (self.event_ttl_seconds, 300),
                                (self.future_tolerance_seconds, 5), (self.max_batch, 32),
                                (self.max_seconds, 2)):
            if type(value) is not int or value != expected:
                _invalid()

    @classmethod
    def from_env(cls) -> ReadinessDispatcherSettings:
        raw = os.environ.get("DAON_DOCUMENT_READINESS_SCOPES_JSON", "[]")
        try:
            rows = json.loads(raw, object_pairs_hook=_unique_json_object,
                              parse_constant=_invalid_json_constant)
        except (ValueError, TypeError, RecursionError):
            _invalid()
        if type(rows) is not list or len(rows) > 100:
            _invalid()
        scopes: set[tuple[str, str, str, str]] = set()
        for row in rows:
            if type(row) is not dict or set(row) != set(_SCOPE_FIELDS):
                _invalid()
            for name in _SCOPE_FIELDS:
                _text(row[name], _ID)
            scope = (row["tenant_id"], row["workspace_id"], row["actor_id"], row["source_version_id"])
            if scope in scopes:
                _invalid()
            scopes.add(scope)
        try:
            policies = json.loads(os.environ.get("DAON_DOCUMENT_READINESS_INTERNAL_COST_POLICIES_JSON", "[]"),
                object_pairs_hook=_unique_json_object, parse_constant=_invalid_json_constant)
            fields = set(InternalCostExclusionPolicy.__dataclass_fields__)
            if (type(policies) is not list or len(policies) > 100
                    or any(type(p) is not dict or set(p) != fields for p in policies)):
                _invalid()
            parsed = tuple(InternalCostExclusionPolicy(**p) for p in policies)
        except (ValueError, TypeError, RecursionError):
            _invalid()
        return cls(scopes=frozenset(scopes), internal_cost_policies=parsed)


@dataclass(frozen=True, slots=True, repr=False)
class RoleWaitEvidence:
    tenant_id: str
    workspace_id: str
    actor_id: str
    notebook_id: str
    source_id: str
    source_version_id: str
    processing_run_id: str
    capability: Capability | None
    failure_kind: FailureKind
    mode: ReadinessModeProof
    dependency_connection_id: str | None
    frozen_context_digest: str
    safe_error_code: str
    failed_at: datetime
    not_before: datetime

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.workspace_id, self.actor_id, self.notebook_id,
                      self.source_id, self.source_version_id, self.processing_run_id):
            _text(value, _ID)
        _choice(self.capability, _CAPABILITIES, nullable=True)
        _choice(self.failure_kind, ("connection_availability", "role_runtime", "non_availability", "unknown"))
        if type(self.mode) is not ReadinessModeProof:
            _invalid()
        _optional_text(self.dependency_connection_id, _ID)
        _text(self.frozen_context_digest, _DIGEST)
        _text(self.safe_error_code, _ERROR_CODE)
        _utc(self.failed_at)
        _utc(self.not_before)
        if self.not_before < self.failed_at:
            _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedReadinessObservation:
    schema_version: int
    event_id: str
    connection_id: str
    adapter_type: str
    capability: Capability | None
    model_catalog_digest: str
    configuration_version: int
    observed_at: datetime
    received_at: datetime
    probe_kind: ProbeKind
    status: Literal["healthy", "not_ready", "unavailable"]
    observation_sequence: int
    content_digest: str

    def __post_init__(self) -> None:
        _integer(self.schema_version, 1)
        if self.schema_version != 1:
            _invalid()
        _text(self.event_id, _EVENT)
        _text(self.connection_id, _ID)
        _text(self.adapter_type, _ID)
        _choice(self.capability, _CAPABILITIES, nullable=True)
        _text(self.model_catalog_digest, _DIGEST)
        _integer(self.configuration_version, 1)
        _utc(self.observed_at)
        _utc(self.received_at)
        _choice(self.probe_kind, ("provider_connection_check", "native_asr_ready", "model_catalog_refresh"))
        if (self.probe_kind == "provider_connection_check" and self.capability is not None
                or self.probe_kind == "native_asr_ready" and self.capability != "speech_to_text"):
            _invalid()
        _choice(self.status, ("healthy", "not_ready", "unavailable"))
        _integer(self.observation_sequence, 1)
        _text(self.content_digest, _DIGEST)


@dataclass(frozen=True, slots=True, repr=False)
class CurrentReprocessEligibility:
    tenant_id: str
    workspace_id: str
    actor_id: str
    notebook_id: str
    source_version_id: str
    capability: Capability | None
    frozen_context_digest: str
    dependency_connection_id: str | None
    model_catalog_digest: str | None
    configuration_version: int
    latest_observation_sequence: int
    scope_enabled: bool
    authorized: bool
    source_current: bool
    policy_allowed: bool
    local_only_allowed: bool
    candidate_available: bool
    active_run: bool
    context_already_decided: bool
    cost_blocked_context_exists: bool
    cost_state: Literal["allowed", "blocked", "unknown"]
    egress_state: Literal["allowed", "blocked", "approval_missing"]
    prior_run_id: str

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.workspace_id, self.actor_id, self.notebook_id,
                      self.source_version_id, self.prior_run_id):
            _text(value, _ID)
        _choice(self.capability, _CAPABILITIES, nullable=True)
        _text(self.frozen_context_digest, _DIGEST)
        _optional_text(self.dependency_connection_id, _ID)
        _optional_text(self.model_catalog_digest, _DIGEST)
        _integer(self.configuration_version, 0)
        _integer(self.latest_observation_sequence, 0)
        for value in (self.scope_enabled, self.authorized, self.source_current, self.policy_allowed,
                      self.local_only_allowed, self.candidate_available, self.active_run,
                      self.context_already_decided, self.cost_blocked_context_exists):
            if type(value) is not bool:
                _invalid()
        _choice(self.cost_state, ("allowed", "blocked", "unknown"))
        _choice(self.egress_state, ("allowed", "blocked", "approval_missing"))


@dataclass(frozen=True, slots=True, repr=False)
class ReprocessDecision:
    action: Action
    safe_code: str
    create_run: bool

    def __post_init__(self) -> None:
        if type(self.safe_code) is not str or type(self.action) is not str or type(self.create_run) is not bool:
            _invalid()
        if _DECISIONS.get(self.safe_code) != (self.action, self.create_run):
            _invalid()


def _decision(code: str) -> ReprocessDecision:
    action, create_run = _DECISIONS[code]
    return ReprocessDecision(action, code, create_run)


def decide_reprocess(
    wait: RoleWaitEvidence, observation: ValidatedReadinessObservation | None,
    current: CurrentReprocessEligibility, *, db_now: datetime,
) -> ReprocessDecision:
    """고정 입력을 판정할 뿐 DB 최신성/접근·같은 조건 유일성을 입증하지 않는다.

    content_digest는 형식을 검사한다. 원 관측의 canonical preimage와 실제 검사
    출처, current의 bool/digest/sequence는 후속 영속 adapter가 검증해야 한다.
    backoff는 저장 not_before와 준비 관측 후 60초 모두 충족해야 한다.
    """
    if (type(wait) is not RoleWaitEvidence or type(current) is not CurrentReprocessEligibility
            or (observation is not None and type(observation) is not ValidatedReadinessObservation)):
        _invalid()
    _utc(db_now)
    if ((wait.tenant_id, wait.workspace_id, wait.actor_id, wait.notebook_id,
         wait.source_version_id, wait.processing_run_id)
            != (current.tenant_id, current.workspace_id, current.actor_id, current.notebook_id,
                current.source_version_id, current.prior_run_id)):
        _invalid()
    if not current.scope_enabled:
        return _decision("READINESS_SCOPE_NOT_CONFIGURED")
    if not current.authorized:
        return _decision("READINESS_ACCESS_DENIED")
    if not current.source_current:
        return _decision("READINESS_SOURCE_CHANGED")
    if wait.mode.mode == "legacy_unknown":
        return _decision("READINESS_MODE_UNCONFIRMED")
    if wait.mode.mode == "pinned":
        return _decision("READINESS_MODE_MANUAL")
    if wait.capability is None or current.capability is None:
        return _decision("READINESS_ROLE_UNKNOWN")
    if wait.failure_kind in ("unknown", "non_availability"):
        return _decision("READINESS_NOT_READY")
    if observation is None:
        return _decision("READINESS_NOT_READY")
    if (wait.capability != current.capability
            or current.dependency_connection_id is None
            or observation.connection_id != current.dependency_connection_id
            or observation.configuration_version != current.configuration_version
            or observation.model_catalog_digest != current.model_catalog_digest
            or observation.observation_sequence != current.latest_observation_sequence
            or (observation.capability is not None and observation.capability != current.capability)):
        return _decision("READINESS_DEPENDENCY_CHANGED")
    if observation.capability is None:
        # 연결 건강만으로는 과거 실패 dependency를 바꿔 재시도할 수 없다.
        # 실제 역할 관측은 위의 현재 binding에 결속되며 과거 A/None에 묶지 않는다.
        if (wait.dependency_connection_id is None
                or wait.dependency_connection_id != current.dependency_connection_id):
            return _decision("READINESS_DEPENDENCY_CHANGED")
        if wait.failure_kind != "connection_availability":
            return _decision("READINESS_NOT_READY")
    tolerance = timedelta(seconds=5)
    if (observation.status != "healthy" or observation.observed_at <= wait.failed_at
            or observation.observed_at - db_now > tolerance
            or observation.received_at - db_now > tolerance
            or observation.observed_at - observation.received_at > tolerance):
        return _decision("READINESS_NOT_READY")
    if db_now - observation.observed_at > timedelta(seconds=300):
        return _decision("READINESS_OBSERVATION_EXPIRED")
    # 두 bool은 현재 semantic digest의 영속 이력이다. 예전 wait digest와 다른
    # 현재 digest는 실제 조건 변경일 수 있으므로 무조건 같도록 강제하지 않는다.
    if current.context_already_decided or current.cost_blocked_context_exists:
        return _decision("READINESS_CONTEXT_SUPPRESSED")
    if current.active_run:
        return _decision("READINESS_ACTIVE_RUN_EXISTS")
    if db_now < wait.not_before or db_now - observation.observed_at < timedelta(seconds=60):
        return _decision("READINESS_BACKOFF_PENDING")
    if not current.policy_allowed or (wait.mode.mode == "local_only" and not current.local_only_allowed):
        return _decision("READINESS_POLICY_BLOCKED")
    if current.cost_state == "blocked":
        return _decision("COST_LIMIT_EXCEEDED")
    if current.cost_state == "unknown":
        return _decision("READINESS_COST_UNCONFIRMED")
    if current.egress_state == "blocked":
        return _decision("READINESS_POLICY_BLOCKED")
    if current.egress_state == "approval_missing":
        return _decision("READINESS_APPROVAL_MISSING")
    if not current.candidate_available:
        return _decision("READINESS_NOT_READY")
    return _decision("READY_REPROCESS_ALLOWED")
