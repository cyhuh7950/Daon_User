"""Approved deletion, retention, and Legal Hold domain contract."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_KINDS = frozenset({
    "original_content", "index", "preview", "cache",
    "known_local_copy", "sync_reference",
})


class RetentionError(RuntimeError):
    def __init__(self, code: str, status: int = 409, *, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


def _safe(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise RetentionError("RETENTION_INPUT_INVALID", 400)
    return value


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class RetentionContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str
    organization_admin: bool = False

    def __post_init__(self) -> None:
        for value in (
            self.tenant_id, self.workspace_id, self.actor_id,
            self.trace_id, self.policy_version,
        ):
            _safe(value)
        if not isinstance(self.organization_admin, bool):
            raise RetentionError("RETENTION_CONTEXT_INVALID", 400)


@dataclass(frozen=True, slots=True)
class DerivativeInput:
    kind: str
    reference_id: str
    acknowledgement_required: bool = False
    disposition: str = "present"

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise RetentionError("RETENTION_DERIVATIVE_INVALID", 400)
        _safe(self.reference_id)
        if self.acknowledgement_required != (self.kind == "known_local_copy"):
            raise RetentionError("RETENTION_DERIVATIVE_INVALID", 400)
        if self.disposition not in {"present", "not_present", "not_applicable", "verification_pending"}:
            raise RetentionError("RETENTION_DERIVATIVE_INVALID", 400)


@dataclass(frozen=True, slots=True)
class CleanupItemView:
    kind: str
    reference_id: str
    state: str
    attempt_count: int
    evidence: str | None


@dataclass(frozen=True, slots=True)
class DeletionRequestView:
    request_id: str
    tenant_id: str
    workspace_id: str
    source_id: str
    state: str
    version: int
    requested_at: datetime
    grace_until: datetime
    source_active: bool
    cleanup_items: tuple[CleanupItemView, ...]
    completed_references: tuple[str, ...]
    source_version_mutations: int = 0

    @property
    def etag(self) -> str:
        return f'"deletion:{self.request_id}:{self.version}"'


@dataclass(frozen=True, slots=True)
class LegalHoldView:
    hold_id: str
    tenant_id: str
    workspace_id: str
    source_id: str
    state: str
    version: int

    @property
    def etag(self) -> str:
        return f'"legal-hold:{self.hold_id}:{self.version}"'


@dataclass(slots=True)
class _CleanupItem:
    value: DerivativeInput
    state: str = "pending"
    attempts: int = 0
    evidence: str | None = None


@dataclass(slots=True)
class _DeletionRequest:
    request_id: str
    context: RetentionContext
    source_id: str
    requested_at: datetime
    grace_until: datetime
    inventory: dict[str, _CleanupItem]
    state: str = "grace_period"
    version: int = 1
    source_active: bool = False
    purge_started: bool = False


@dataclass(slots=True)
class _LegalHold:
    hold_id: str
    context: RetentionContext
    source_id: str
    state: str = "active"
    version: int = 1


class ReferenceRetentionRepository:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.requests: dict[str, _DeletionRequest] = {}
        self.source_requests: dict[tuple[str, str, str], str] = {}
        self.source_scopes: dict[tuple[str, str], str] = {}
        self.holds: dict[str, _LegalHold] = {}
        self.idempotency: dict[tuple[str, str, str], tuple[str, object]] = {}
        self.attempts: list[tuple[str, str, str, int, str]] = []
        self.audit: list[dict[str, object]] = []


class ReferenceCleanupPort:
    """Fixture-only cleanup port. It never receives content, paths, or credentials."""

    def __init__(self) -> None:
        self.fail_references: set[str] = set()
        self.attempt_count = 0
        self.attempts_by_reference: dict[str, int] = {}

    def cleanup(self, context: RetentionContext, item: DerivativeInput) -> bool:
        del context
        self.attempt_count += 1
        self.attempts_by_reference[item.reference_id] = (
            self.attempts_by_reference.get(item.reference_id, 0) + 1
        )
        return item.reference_id not in self.fail_references


class RetentionService:
    def __init__(
        self,
        repository: ReferenceRetentionRepository,
        cleanup_port: ReferenceCleanupPort,
        *,
        clock: Callable[[], datetime],
        grace_period: timedelta = timedelta(days=30),
        fixture_prefix: str = "fixture-",
        inventory_provider: Callable[[RetentionContext, str], tuple[DerivativeInput, ...]] | None = None,
    ) -> None:
        self._repository = repository
        self._cleanup = cleanup_port
        self._clock = clock
        self._grace_period = grace_period
        self._inventory_provider = inventory_provider
        self._fixture_prefix = fixture_prefix

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(12)}"

    @staticmethod
    def _write(key: str, expected_version: int | str) -> None:
        _safe(key)
        if expected_version != "*" and (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 1
        ):
            raise RetentionError("IF_MATCH_INVALID", 400)

    @staticmethod
    def _require_sensitive(context: RetentionContext, verified: bool) -> None:
        if not context.organization_admin:
            raise RetentionError("CURRENT_ACCESS_DENIED", 403)
        if not verified:
            raise RetentionError("STEP_UP_REQUIRED", 403)

    def _request(self, context: RetentionContext, request_id: str) -> _DeletionRequest:
        request = self._repository.requests.get(request_id)
        if (
            request is None
            or request.context.tenant_id != context.tenant_id
            or request.context.workspace_id != context.workspace_id
        ):
            raise RetentionError("DELETION_REQUEST_UNAVAILABLE", 404)
        return request

    def _hold(self, context: RetentionContext, hold_id: str) -> _LegalHold:
        hold = self._repository.holds.get(hold_id)
        if (
            hold is None
            or hold.context.tenant_id != context.tenant_id
            or hold.context.workspace_id != context.workspace_id
        ):
            raise RetentionError("LEGAL_HOLD_UNAVAILABLE", 404)
        return hold

    def _active_holds(self, request: _DeletionRequest) -> list[_LegalHold]:
        return [
            hold for hold in self._repository.holds.values()
            if hold.context.tenant_id == request.context.tenant_id
            and hold.context.workspace_id == request.context.workspace_id
            and hold.source_id == request.source_id
            and hold.state == "active"
        ]

    def _audit(self, request: _DeletionRequest, action: str) -> None:
        previous = "0" * 64 if not self._repository.audit else str(
            self._repository.audit[-1]["event_hash"]
        )
        minimal = {
            "request_id": request.request_id,
            "actor_id": request.context.actor_id,
            "action": action,
            "target": request.source_id,
            "timestamp": self._clock().isoformat(),
            "policy_version": request.context.policy_version,
            "trace_id": request.context.trace_id,
            "previous_hash": previous,
        }
        minimal["event_hash"] = _fingerprint(minimal)
        self._repository.audit.append(minimal)

    def _view(self, request: _DeletionRequest) -> DeletionRequestView:
        items = tuple(
            CleanupItemView(
                item.value.kind, item.value.reference_id, item.state,
                item.attempts, item.evidence,
            )
            for item in sorted(request.inventory.values(), key=lambda value: value.value.reference_id)
        )
        return DeletionRequestView(
            request.request_id, request.context.tenant_id, request.context.workspace_id,
            request.source_id, request.state, request.version, request.requested_at,
            request.grace_until, request.source_active, items,
            tuple(item.reference_id for item in items if item.state == "completed"),
        )

    def _replay(
        self, key: tuple[str, str, str], fingerprint: str
    ) -> object | None:
        replay = self._repository.idempotency.get(key)
        if replay is None:
            return None
        if replay[0] != fingerprint:
            raise RetentionError("IDEMPOTENCY_KEY_REUSED")
        return replay[1]

    def create_request(
        self,
        context: RetentionContext,
        *,
        source_id: str,
        inventory: tuple[DerivativeInput, ...] | None,
        idempotency_key: str,
        if_match: str,
    ) -> DeletionRequestView:
        self._write(idempotency_key, if_match)
        _safe(source_id)
        if inventory is None:
            if self._inventory_provider is None:
                raise RetentionError("DELETION_INVENTORY_INVALID", 400)
            inventory = self._inventory_provider(context, source_id)
        if if_match != "*" or not inventory:
            raise RetentionError("DELETION_REQUEST_INVALID", 400)
        by_reference = {item.reference_id: item for item in inventory}
        if len(by_reference) != len(inventory) or {item.kind for item in inventory} != _KINDS:
            raise RetentionError("DELETION_INVENTORY_INVALID", 400)
        fingerprint = _fingerprint((source_id, inventory))
        key = (context.actor_id, "request", idempotency_key)
        with self._repository.lock:
            registered = self._repository.source_scopes.get((context.tenant_id, source_id))
            if registered is not None and registered != context.workspace_id:
                raise RetentionError("CURRENT_ACCESS_DENIED", 403)
            self._repository.source_scopes[(context.tenant_id, source_id)] = context.workspace_id
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return self._view(self._request(context, str(replay)))
            existing_id = self._repository.source_requests.get(
                (context.tenant_id, context.workspace_id, source_id)
            )
            if existing_id is not None:
                raise RetentionError("DELETION_REQUEST_ACTIVE")
            now = self._clock()
            request = _DeletionRequest(
                self._id("deletion"), context, source_id, now,
                now + self._grace_period,
                {ref: _CleanupItem(
                    item,
                    state="completed" if item.disposition in {"not_present", "not_applicable"} else "pending",
                    evidence=item.disposition,
                ) for ref, item in by_reference.items()},
            )
            if any(
                hold.context.tenant_id == context.tenant_id
                and hold.context.workspace_id == context.workspace_id
                and hold.source_id == source_id
                and hold.state == "active"
                for hold in self._repository.holds.values()
            ):
                request.state = "blocked_by_hold"
            self._repository.requests[request.request_id] = request
            self._repository.source_requests[
                (context.tenant_id, context.workspace_id, source_id)
            ] = request.request_id
            self._repository.idempotency[key] = (fingerprint, request.request_id)
            self._audit(request, "deletion.requested")
            self._audit(request, "source.deactivated")
            self._audit(request, "deletion.grace_period.started")
            return self._view(request)

    def get_request(self, context: RetentionContext, request_id: str) -> DeletionRequestView:
        with self._repository.lock:
            return self._view(self._request(context, request_id))

    def locate_workspace(self, tenant_id: str, request_id: str) -> str:
        with self._repository.lock:
            request = self._repository.requests.get(request_id)
            if request is None or request.context.tenant_id != tenant_id:
                raise RetentionError("DELETION_REQUEST_UNAVAILABLE", 404)
            return request.context.workspace_id

    def register_source(self, context: RetentionContext, source_id: str) -> None:
        """Register an already-authorized Source scope for the reference adapter."""
        _safe(source_id)
        with self._repository.lock:
            existing = self._repository.source_scopes.get((context.tenant_id, source_id))
            if existing is not None and existing != context.workspace_id:
                raise RetentionError("CURRENT_ACCESS_DENIED", 403)
            self._repository.source_scopes[(context.tenant_id, source_id)] = context.workspace_id

    def locate_source_workspace(self, tenant_id: str, source_id: str) -> str:
        _safe(source_id)
        with self._repository.lock:
            workspace = self._repository.source_scopes.get((tenant_id, source_id))
            if workspace is None:
                raise RetentionError("SOURCE_UNAVAILABLE", 404)
            return workspace

    def source_etag(self, tenant_id: str, source_id: str) -> tuple[str, int]:
        """Return the current opaque source concurrency token without exposing storage data."""
        _safe(source_id)
        with self._repository.lock:
            workspace = self._repository.source_scopes.get((tenant_id, source_id))
            if workspace is None:
                raise RetentionError("SOURCE_UNAVAILABLE", 404)
            request_id = self._repository.source_requests.get((tenant_id, workspace, source_id))
            version = 1 if request_id is None else self._repository.requests[request_id].version
            return f'"source:{source_id}:{version}"', version

    def locate_hold_workspace(self, tenant_id: str, hold_id: str) -> str:
        with self._repository.lock:
            hold = self._repository.holds.get(hold_id)
            if hold is None or hold.context.tenant_id != tenant_id:
                raise RetentionError("LEGAL_HOLD_UNAVAILABLE", 404)
            return hold.context.workspace_id

    def is_source_use_blocked(self, context: RetentionContext, source_id: str) -> bool:
        with self._repository.lock:
            request_id = self._repository.source_requests.get(
                (context.tenant_id, context.workspace_id, source_id)
            )
            if request_id is None:
                return False
            return self._repository.requests[request_id].state not in {"cancelled"}

    def cancel(
        self, context: RetentionContext, request_id: str, *, expected_version: int,
        idempotency_key: str,
    ) -> DeletionRequestView:
        self._write(idempotency_key, expected_version)
        fingerprint = _fingerprint((request_id, expected_version))
        key = (context.actor_id, "cancel", idempotency_key)
        with self._repository.lock:
            request = self._request(context, request_id)
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return self._view(request)
            if request.version != expected_version:
                raise RetentionError("RETENTION_VERSION_CONFLICT")
            if request.purge_started or request.state in {"purged", "cancelled"}:
                raise RetentionError("DELETION_CLEANUP_PENDING")
            request.state = "cancelled"
            request.source_active = True
            request.version += 1
            self._repository.idempotency[key] = (fingerprint, request.request_id)
            self._audit(request, "deletion.cancelled")
            return self._view(request)

    def apply_legal_hold(
        self, context: RetentionContext, *, source_id: str, expected_version: int,
        idempotency_key: str, step_up_verified: bool,
    ) -> LegalHoldView:
        self._write(idempotency_key, expected_version)
        self._require_sensitive(context, step_up_verified)
        _safe(source_id)
        fingerprint = _fingerprint((source_id, expected_version, context.policy_version))
        key = (context.actor_id, "hold", idempotency_key)
        with self._repository.lock:
            replay = self._replay(key, fingerprint)
            if replay is not None:
                hold = self._hold(context, str(replay))
                return LegalHoldView(
                    hold.hold_id, hold.context.tenant_id, hold.context.workspace_id,
                    hold.source_id, hold.state, hold.version,
                )
            workspace = self._repository.source_scopes.get((context.tenant_id, source_id))
            if workspace != context.workspace_id:
                raise RetentionError("SOURCE_UNAVAILABLE", 404)
            request_id = self._repository.source_requests.get(
                (context.tenant_id, context.workspace_id, source_id)
            )
            request = None if request_id is None else self._request(context, request_id)
            if request is not None and request.version != expected_version:
                raise RetentionError("RETENTION_VERSION_CONFLICT")
            if request is None and expected_version != 1:
                raise RetentionError("RETENTION_VERSION_CONFLICT")
            hold = _LegalHold(self._id("legal-hold"), context, source_id)
            self._repository.holds[hold.hold_id] = hold
            if request is not None:
                request.state = "blocked_by_hold"
                request.version += 1
            self._repository.idempotency[key] = (fingerprint, hold.hold_id)
            if request is not None:
                self._audit(request, "legal_hold.applied")
            return LegalHoldView(
                hold.hold_id, context.tenant_id, context.workspace_id,
                source_id, hold.state, hold.version,
            )

    def release_legal_hold(
        self, context: RetentionContext, hold_id: str, *, expected_version: int,
        idempotency_key: str, step_up_verified: bool,
    ) -> LegalHoldView:
        self._write(idempotency_key, expected_version)
        self._require_sensitive(context, step_up_verified)
        fingerprint = _fingerprint((hold_id, expected_version, context.policy_version))
        key = (context.actor_id, "release", idempotency_key)
        with self._repository.lock:
            hold = self._hold(context, hold_id)
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return LegalHoldView(
                    hold.hold_id, hold.context.tenant_id, hold.context.workspace_id,
                    hold.source_id, hold.state, hold.version,
                )
            if hold.version != expected_version or hold.state != "active":
                raise RetentionError("RETENTION_VERSION_CONFLICT")
            hold.state = "released"
            hold.version += 1
            request_id = self._repository.source_requests.get(
                (context.tenant_id, context.workspace_id, hold.source_id)
            )
            request = None if request_id is None else self._request(context, request_id)
            if request is not None and not self._active_holds(request):
                request.state = (
                    "grace_period" if self._clock() < request.grace_until
                    else "cleanup_pending"
                )
                request.version += 1
            self._repository.idempotency[key] = (fingerprint, hold.hold_id)
            if request is not None:
                self._audit(request, "legal_hold.released")
            return LegalHoldView(
                hold.hold_id, hold.context.tenant_id, hold.context.workspace_id,
                hold.source_id, hold.state, hold.version,
            )

    def acknowledge_local_copy(
        self, context: RetentionContext, request_id: str, *, reference_id: str,
        evidence: str, expected_version: int, idempotency_key: str,
    ) -> DeletionRequestView:
        self._write(idempotency_key, expected_version)
        if evidence not in {"device_ack", "device_revoked", "key_destroyed"}:
            raise RetentionError("LOCAL_COPY_ACK_INVALID", 400)
        fingerprint = _fingerprint((request_id, reference_id, evidence, expected_version))
        key = (context.actor_id, "ack", idempotency_key)
        with self._repository.lock:
            request = self._request(context, request_id)
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return self._view(request)
            if request.version != expected_version:
                raise RetentionError("RETENTION_VERSION_CONFLICT")
            item = request.inventory.get(reference_id)
            if item is None or item.value.kind != "known_local_copy":
                raise RetentionError("LOCAL_COPY_ACK_INVALID", 400)
            item.state = "completed"
            item.evidence = evidence
            request.version += 1
            self._repository.idempotency[key] = (fingerprint, request_id)
            self._audit(request, "local_copy.access_revoked")
            return self._view(request)

    def purge(
        self, context: RetentionContext, request_id: str, *, expected_version: int,
        idempotency_key: str, step_up_verified: bool,
    ) -> DeletionRequestView:
        self._write(idempotency_key, expected_version)
        self._require_sensitive(context, step_up_verified)
        fingerprint = _fingerprint((request_id, expected_version, context.policy_version))
        key = (context.actor_id, "purge", idempotency_key)
        with self._repository.lock:
            request = self._request(context, request_id)
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return self._view(request)
            if request.version != expected_version:
                raise RetentionError("RETENTION_VERSION_CONFLICT")
            if self._active_holds(request):
                request.state = "blocked_by_hold"
                raise RetentionError("LEGAL_HOLD_ACTIVE")
            if self._clock() < request.grace_until:
                raise RetentionError("DELETION_GRACE_PERIOD_ACTIVE")
            if any(
                not item.value.reference_id.startswith(self._fixture_prefix)
                for item in request.inventory.values()
            ):
                raise RetentionError("FIXTURE_ONLY_PURGE_REQUIRED", 403)
            request.purge_started = True
            request.state = "cleanup_pending"
            for item in request.inventory.values():
                if item.state == "completed":
                    continue
                if item.value.acknowledgement_required:
                    item.state = "awaiting_ack"
                    continue
                item.attempts += 1
                completed = self._cleanup.cleanup(context, item.value)
                item.state = "completed" if completed else "failed"
                self._repository.attempts.append((
                    request.request_id, item.value.reference_id, item.value.kind,
                    item.attempts, item.state,
                ))
            request.version += 1
            if all(item.state == "completed" for item in request.inventory.values()):
                request.state = "purged"
                self._audit(request, "deletion.purged")
            else:
                request.state = "cleanup_pending"
                self._audit(request, "deletion.cleanup_pending")
            self._repository.idempotency[key] = (fingerprint, request_id)
            return self._view(request)

    def minimal_lineage(self, request_id: str) -> dict[str, object]:
        with self._repository.lock:
            request = self._repository.requests.get(request_id)
            if request is None:
                raise RetentionError("DELETION_REQUEST_UNAVAILABLE", 404)
            events = [
                {
                    "actor": event["actor_id"], "action": event["action"],
                    "target": event["target"], "timestamp": event["timestamp"],
                    "policy_version": event["policy_version"],
                    "trace_id": event["trace_id"], "event_hash": event["event_hash"],
                    "previous_hash": event["previous_hash"],
                }
                for event in self._repository.audit
                if event["request_id"] == request_id
            ]
            return {"request_id": request_id, "retention_years": 1, "events": events}
