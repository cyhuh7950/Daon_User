"""Immutable, append-only Audit Event domain core.

HTTP, authorization and persistence adapters intentionally live in later work orders.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import Lock
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit


GENESIS_HASH = "0" * 64
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_TEXT_LENGTH = 128
MAX_JSON_DEPTH = 8
MAX_JSON_BYTES = 16_384
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

_FORBIDDEN_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "credential",
    "apikey",
    "rawprovidererror",
    "internalurl",
    "internalhost",
    "dbhost",
    "dockerhost",
)
_FORBIDDEN_VALUE_HOSTS = {"localhost", "host.docker.internal", "docker.internal"}
_ABSOLUTE_ENDPOINT_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_RAW_HOST_PATTERN = re.compile(
    r"(?=.{1,253}\.?$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?",
    re.IGNORECASE,
)
_HOST_PORT_PATTERN = re.compile(
    r"^(?:\[(?P<bracketed>[^\]]+)\]|(?P<plain>[a-z0-9.-]+)):(?P<port>[0-9]{1,5})$",
    re.IGNORECASE,
)


class ActorType(str, Enum):
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"


class AuditOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"


class IntegrityCode(str, Enum):
    OK = "OK"
    INVALID_EVENT = "INVALID_EVENT"
    DUPLICATE_EVENT_ID = "DUPLICATE_EVENT_ID"
    SEQUENCE_MISMATCH = "SEQUENCE_MISMATCH"
    PREVIOUS_HASH_MISMATCH = "PREVIOUS_HASH_MISMATCH"
    EVENT_HASH_MISMATCH = "EVENT_HASH_MISMATCH"


class AuditValidationError(ValueError):
    """Fail-close input rejection with a stable, value-free code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"AUDIT_VALIDATION_FAILED:{code}")


class AuditDuplicateEventError(AuditValidationError):
    def __init__(self) -> None:
        super().__init__("DUPLICATE_EVENT_ID")


@dataclass(frozen=True, slots=True)
class AuditEventDraft:
    event_id: str
    occurred_at: datetime
    actor_id: str
    actor_type: ActorType
    tenant_id: str
    workspace_id: str | None
    action: str
    target_type: str
    target_id: str
    outcome: AuditOutcome
    trace_id: str
    policy_version: str
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    sequence: int
    event_id: str
    occurred_at: datetime
    actor_id: str
    actor_type: ActorType
    tenant_id: str
    workspace_id: str | None
    action: str
    target_type: str
    target_id: str
    outcome: AuditOutcome
    trace_id: str
    policy_version: str
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    metadata: Mapping[str, Any]
    previous_event_hash: str
    event_hash: str


@dataclass(frozen=True, slots=True)
class AuditPage:
    items: tuple[AuditEvent, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class IntegrityResult:
    valid: bool
    code: IntegrityCode
    position: int | None
    checked_count: int
    head_hash: str
    message: str


def _fail(code: str) -> None:
    raise AuditValidationError(code)


def _validate_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        _fail(f"INVALID_{field_name.upper()}")
    if value != value.strip() or not value or len(value) > MAX_TEXT_LENGTH:
        _fail(f"INVALID_{field_name.upper()}")
    if any(ord(character) < 32 for character in value):
        _fail(f"INVALID_{field_name.upper()}")
    return value


def _validate_utc(value: object, field_name: str = "occurred_at") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(f"INVALID_{field_name.upper()}")
    if value.utcoffset() != timedelta(0):
        _fail(f"INVALID_{field_name.upper()}")
    return value.astimezone(timezone.utc)


def _canonical_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _is_forbidden_host(hostname: str) -> bool:
    normalized = hostname.casefold().rstrip(".")
    if normalized in _FORBIDDEN_VALUE_HOSTS:
        return True
    if normalized.endswith((".internal", ".local")):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def _validate_string_value(value: str) -> None:
    candidate = value.strip()
    raw_address = (
        candidate[1:-1]
        if len(candidate) >= 2 and candidate.startswith("[") and candidate.endswith("]")
        else candidate
    )
    try:
        address = ipaddress.ip_address(raw_address)
    except ValueError:
        address = None
    if address is not None and _is_forbidden_host(str(address)):
        _fail("UNSAFE_JSON_VALUE")

    if candidate.startswith("//") or _ABSOLUTE_ENDPOINT_PATTERN.match(candidate):
        try:
            endpoint_host = urlsplit(candidate).hostname
        except ValueError:
            _fail("UNSAFE_JSON_VALUE")
        if endpoint_host and _is_forbidden_host(endpoint_host):
            _fail("UNSAFE_JSON_VALUE")
        return

    host_port = _HOST_PORT_PATTERN.fullmatch(candidate)
    if host_port:
        endpoint_host = host_port.group("bracketed") or host_port.group("plain")
        if endpoint_host and _is_forbidden_host(endpoint_host):
            _fail("UNSAFE_JSON_VALUE")
        return

    if _RAW_HOST_PATTERN.fullmatch(candidate) and _is_forbidden_host(candidate):
        _fail("UNSAFE_JSON_VALUE")


def _freeze_json(value: object, *, depth: int = 0) -> object:
    if depth > MAX_JSON_DEPTH:
        _fail("JSON_DEPTH_EXCEEDED")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str):
            _validate_string_value(value)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("NON_FINITE_JSON_NUMBER")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > MAX_TEXT_LENGTH:
                _fail("INVALID_JSON_KEY")
            normalized = _normalized_key(key)
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                _fail("FORBIDDEN_JSON_KEY")
            frozen[key] = _freeze_json(item, depth=depth + 1)
        return MappingProxyType(frozen)
    if isinstance(value, list):
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    _fail("NON_JSON_VALUE")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_projection(value: object, field_name: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        _fail(f"INVALID_{field_name.upper()}")
    frozen = _freeze_json(value)
    encoded = json.dumps(
        _thaw_json(frozen),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        _fail(f"{field_name.upper()}_TOO_LARGE")
    return frozen  # type: ignore[return-value]


def _validate_draft(draft: object) -> dict[str, object]:
    if not isinstance(draft, AuditEventDraft):
        _fail("INVALID_DRAFT")
    if not isinstance(draft.actor_type, ActorType):
        _fail("INVALID_ACTOR_TYPE")
    if not isinstance(draft.outcome, AuditOutcome):
        _fail("INVALID_OUTCOME")
    workspace_id = (
        None
        if draft.workspace_id is None
        else _validate_text(draft.workspace_id, "workspace_id")
    )
    metadata = _validate_projection(draft.metadata, "metadata")
    if metadata is None:
        _fail("INVALID_METADATA")
    return {
        "event_id": _validate_text(draft.event_id, "event_id"),
        "occurred_at": _validate_utc(draft.occurred_at),
        "actor_id": _validate_text(draft.actor_id, "actor_id"),
        "actor_type": draft.actor_type,
        "tenant_id": _validate_text(draft.tenant_id, "tenant_id"),
        "workspace_id": workspace_id,
        "action": _validate_text(draft.action, "action"),
        "target_type": _validate_text(draft.target_type, "target_type"),
        "target_id": _validate_text(draft.target_id, "target_id"),
        "outcome": draft.outcome,
        "trace_id": _validate_text(draft.trace_id, "trace_id"),
        "policy_version": _validate_text(draft.policy_version, "policy_version"),
        "before": _validate_projection(draft.before, "before"),
        "after": _validate_projection(draft.after, "after"),
        "metadata": metadata,
    }


def _event_payload(event: AuditEvent) -> dict[str, object]:
    return {
        "sequence": event.sequence,
        "event_id": event.event_id,
        "occurred_at": _canonical_time(event.occurred_at),
        "actor_id": event.actor_id,
        "actor_type": event.actor_type.value,
        "tenant_id": event.tenant_id,
        "workspace_id": event.workspace_id,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "outcome": event.outcome.value,
        "trace_id": event.trace_id,
        "policy_version": event.policy_version,
        "before": _thaw_json(event.before),
        "after": _thaw_json(event.after),
        "metadata": _thaw_json(event.metadata),
        "previous_event_hash": event.previous_event_hash,
    }


def _calculate_event_hash(event: AuditEvent) -> str:
    canonical = json.dumps(
        _event_payload(event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_event_contract(event: AuditEvent) -> None:
    if not isinstance(event.sequence, int) or isinstance(event.sequence, bool) or event.sequence < 1:
        _fail("INVALID_SEQUENCE")
    if not HASH_PATTERN.fullmatch(event.previous_event_hash):
        _fail("INVALID_PREVIOUS_EVENT_HASH")
    if not HASH_PATTERN.fullmatch(event.event_hash):
        _fail("INVALID_EVENT_HASH")
    _validate_draft(
        AuditEventDraft(
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            action=event.action,
            target_type=event.target_type,
            target_id=event.target_id,
            outcome=event.outcome,
            trace_id=event.trace_id,
            policy_version=event.policy_version,
            before=_thaw_json(event.before),
            after=_thaw_json(event.after),
            metadata=_thaw_json(event.metadata),
        )
    )


def _encode_cursor(sequence: int) -> str:
    payload = f"audit-v1:{sequence}".encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
        _fail("INVALID_CURSOR")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        prefix, raw_sequence = decoded.split(":", 1)
        sequence = int(raw_sequence)
    except (UnicodeError, ValueError, TypeError):
        _fail("INVALID_CURSOR")
    if prefix != "audit-v1" or sequence < 0 or _encode_cursor(sequence) != cursor:
        _fail("INVALID_CURSOR")
    return sequence


def _integrity_failure(
    code: IntegrityCode, position: int | None, checked_count: int, head_hash: str
) -> IntegrityResult:
    return IntegrityResult(
        valid=False,
        code=code,
        position=position,
        checked_count=checked_count,
        head_hash=head_hash,
        message=f"AUDIT_INTEGRITY_FAILED:{code.value}",
    )


class AuditEventStore:
    """Thread-safe in-memory append contract; durable storage is owned by M5."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._events: list[AuditEvent] = []
        self._event_ids: set[str] = set()

    def append(self, draft: AuditEventDraft) -> AuditEvent:
        validated = _validate_draft(draft)
        with self._lock:
            event_id = validated["event_id"]
            if event_id in self._event_ids:
                raise AuditDuplicateEventError()
            sequence = len(self._events) + 1
            previous_hash = self._events[-1].event_hash if self._events else GENESIS_HASH
            provisional = AuditEvent(
                sequence=sequence,
                previous_event_hash=previous_hash,
                event_hash=GENESIS_HASH,
                **validated,
            )
            event = replace(provisional, event_hash=_calculate_event_hash(provisional))
            self._events.append(event)
            self._event_ids.add(event.event_id)
            return event

    def read(self, event_id: str) -> AuditEvent | None:
        checked_id = _validate_text(event_id, "event_id")
        with self._lock:
            return next((event for event in self._events if event.event_id == checked_id), None)

    def list(
        self,
        *,
        tenant_id: str,
        workspace_id: str | None = None,
        action: str | None = None,
        outcome: AuditOutcome | None = None,
        trace_id: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> AuditPage:
        checked_tenant = _validate_text(tenant_id, "tenant_id")
        checked_workspace = (
            None if workspace_id is None else _validate_text(workspace_id, "workspace_id")
        )
        checked_action = None if action is None else _validate_text(action, "action")
        checked_trace = None if trace_id is None else _validate_text(trace_id, "trace_id")
        if outcome is not None and not isinstance(outcome, AuditOutcome):
            _fail("INVALID_OUTCOME")
        after = None if occurred_after is None else _validate_utc(occurred_after, "occurred_after")
        before = None if occurred_before is None else _validate_utc(occurred_before, "occurred_before")
        if after is not None and before is not None and after > before:
            _fail("INVALID_TIME_RANGE")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_LIMIT:
            _fail("INVALID_LIMIT")
        after_sequence = _decode_cursor(cursor)

        with self._lock:
            matched = tuple(
                event
                for event in self._events
                if event.sequence > after_sequence
                and event.tenant_id == checked_tenant
                and (checked_workspace is None or event.workspace_id == checked_workspace)
                and (checked_action is None or event.action == checked_action)
                and (outcome is None or event.outcome == outcome)
                and (checked_trace is None or event.trace_id == checked_trace)
                and (after is None or event.occurred_at >= after)
                and (before is None or event.occurred_at <= before)
            )
        items = matched[:limit]
        next_cursor = _encode_cursor(items[-1].sequence) if len(matched) > limit else None
        return AuditPage(items=items, next_cursor=next_cursor)

    def verify_integrity(
        self, events: Sequence[AuditEvent] | None = None
    ) -> IntegrityResult:
        if events is None:
            with self._lock:
                candidate = tuple(self._events)
        else:
            candidate = tuple(events)

        expected_previous = GENESIS_HASH
        seen_ids: set[str] = set()
        for index, event in enumerate(candidate, start=1):
            if not isinstance(event, AuditEvent):
                return _integrity_failure(
                    IntegrityCode.INVALID_EVENT, index, index - 1, expected_previous
                )
            try:
                _validate_event_contract(event)
            except (AuditValidationError, TypeError, ValueError):
                return _integrity_failure(
                    IntegrityCode.INVALID_EVENT, index, index - 1, expected_previous
                )
            if event.event_id in seen_ids:
                return _integrity_failure(
                    IntegrityCode.DUPLICATE_EVENT_ID, index, index - 1, expected_previous
                )
            if event.sequence != index:
                return _integrity_failure(
                    IntegrityCode.SEQUENCE_MISMATCH, index, index - 1, expected_previous
                )
            if event.previous_event_hash != expected_previous:
                return _integrity_failure(
                    IntegrityCode.PREVIOUS_HASH_MISMATCH, index, index - 1, expected_previous
                )
            try:
                expected_hash = _calculate_event_hash(event)
            except (AuditValidationError, TypeError, ValueError):
                return _integrity_failure(
                    IntegrityCode.INVALID_EVENT, index, index - 1, expected_previous
                )
            if event.event_hash != expected_hash:
                return _integrity_failure(
                    IntegrityCode.EVENT_HASH_MISMATCH, index, index - 1, expected_previous
                )
            seen_ids.add(event.event_id)
            expected_previous = event.event_hash

        return IntegrityResult(
            valid=True,
            code=IntegrityCode.OK,
            position=None,
            checked_count=len(candidate),
            head_hash=expected_previous,
            message="AUDIT_CHAIN_VALID",
        )


def audit_contract_summary() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "audit_contract_version": "1.0.0",
        "hash_algorithm": "SHA-256",
        "canonical_json": "sort_keys=true,separators=comma-colon,utf8,allow_nan=false",
        "genesis_hash": GENESIS_HASH,
        "event_fields": list(AuditEvent.__dataclass_fields__),
        "actor_types": [value.value for value in ActorType],
        "outcomes": [value.value for value in AuditOutcome],
        "integrity_codes": [value.value for value in IntegrityCode],
        "public_store_api": ["append", "read", "list", "verify_integrity"],
        "max_projection_bytes": MAX_JSON_BYTES,
        "max_page_limit": MAX_PAGE_LIMIT,
        "retention_days": 365,
        "retention_owner": "R1-D009/M5",
    }


def _main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--summary-json", action="store_true")
    arguments = parser.parse_args()
    if not arguments.summary_json:
        parser.error("--summary-json is required")
    print(
        json.dumps(
            audit_contract_summary(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )


if __name__ == "__main__":
    _main()
