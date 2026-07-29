"""Notification and Inbox domain contracts for Release 1 M4-07.

The reference repository is intentionally process-local. Durable PostgreSQL,
Outbox and channel workers belong to M5; this module keeps those concerns behind
an explicit port while providing a real executable API contract now.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Callable, Protocol
from urllib.parse import urlsplit

from .audit import ActorType, AuditEventDraft, AuditEventStore, AuditOutcome
from .authorization import Action, AuthorizationError, AuthorizationService
from .identity import IdentityPrincipal


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FILTERS = re.compile(r"^(state:(unread|read)|kind:[a-z][a-z0-9_]{0,63}|severity:(info|warning|critical))$")
_SAFE_WEB_PATHS = frozenset({"operations", "inbox", "notifications", "workspaces"})
_ALLOWED_KINDS = frozenset({
    "authentication", "device", "membership", "policy", "run_state", "run_failed",
    "review", "approval", "delivery", "warning", "recovery",
})
_ALLOWED_SEVERITIES = frozenset({"info", "warning", "critical"})
_ALLOWED_REQUEST_KINDS = frozenset({"review", "approval", "delivery"})
_ALLOWED_REQUEST_STATES = frozenset({
    "pending", "in_review", "approved", "rejected", "expired", "withdrawn",
    "delivered", "failed", "cancelled",
})
_INBOX_FILTERS = re.compile(
    r"^(kind:(review|approval|delivery)|state:(pending|in_review|approved|rejected|expired|withdrawn|delivered|failed|cancelled))$"
)


class DeliveryState(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class ReadState(str, Enum):
    UNREAD = "unread"
    READ = "read"


class NotificationError(RuntimeError):
    def __init__(self, code: str, http_status: int = 400) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    event_id: str
    tenant_id: str
    workspace_id: str
    kind: str
    severity: str
    title: str
    summary: str
    resource_type: str
    resource_id: str
    deep_link: str
    trace_id: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class Notification:
    notification_id: str
    tenant_id: str
    workspace_id: str
    recipient_id: str
    kind: str
    severity: str
    title: str
    summary: str
    source_event_id: str
    resource_type: str
    resource_id: str
    deep_link: str
    audit_event_id: str
    trace_id: str
    delivery_state: DeliveryState
    created_at: datetime
    delivered_at: datetime | None
    read_at: datetime | None
    version: int

    @property
    def read_state(self) -> ReadState:
        return ReadState.READ if self.read_at is not None else ReadState.UNREAD

    @property
    def etag(self) -> str:
        return f'"notification-{self.version}"'


@dataclass(frozen=True, slots=True)
class NotificationPage:
    items: tuple[Notification, ...]
    next_cursor: str | None
    unread_count: int


@dataclass(frozen=True, slots=True)
class InboxRequest:
    request_id: str
    request_kind: str
    status: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    due_at: datetime | None
    resource_type: str
    resource_id: str
    deep_link: str


@dataclass(frozen=True, slots=True)
class InboxPage:
    items: tuple[InboxRequest, ...]
    next_cursor: str | None


class NotificationRepository(Protocol):
    def insert_if_absent(self, dedupe_key: str, item: Notification) -> bool: ...
    def all_notifications(self) -> tuple[Notification, ...]: ...
    def replace_notification(self, item: Notification) -> None: ...
    def notification_by_id(self, notification_id: str) -> Notification | None: ...
    def idempotency_result(self, key: str) -> tuple[str, Notification] | None: ...
    def save_idempotency_result(self, key: str, fingerprint: str, item: Notification) -> None: ...
    def upsert_inbox_request(self, item: InboxRequest) -> None: ...
    def all_inbox_requests(self) -> tuple[InboxRequest, ...]: ...
    def encode_cursor(self, item_id: str) -> str: ...
    def decode_cursor(self, cursor: str) -> str: ...


class ReferenceNotificationRepository:
    """Isolated executable adapter; no durable DB success is claimed."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._secret = secrets.token_bytes(32)
        self._notifications: dict[str, Notification] = {}
        self._dedupe: set[str] = set()
        self._idempotency: dict[str, tuple[str, Notification]] = {}
        self._inbox: dict[str, InboxRequest] = {}

    def insert_if_absent(self, dedupe_key: str, item: Notification) -> bool:
        with self._lock:
            if dedupe_key in self._dedupe:
                return False
            self._dedupe.add(dedupe_key)
            self._notifications[item.notification_id] = item
            return True

    def all_notifications(self) -> tuple[Notification, ...]:
        with self._lock:
            return tuple(self._notifications.values())

    def replace_notification(self, item: Notification) -> None:
        with self._lock:
            if item.notification_id not in self._notifications:
                raise NotificationError("RESOURCE_UNAVAILABLE", 404)
            self._notifications[item.notification_id] = item

    def notification_by_id(self, notification_id: str) -> Notification | None:
        with self._lock:
            return self._notifications.get(notification_id)

    def idempotency_result(self, key: str) -> tuple[str, Notification] | None:
        with self._lock:
            return self._idempotency.get(key)

    def save_idempotency_result(self, key: str, fingerprint: str, item: Notification) -> None:
        with self._lock:
            self._idempotency[key] = (fingerprint, item)

    def upsert_inbox_request(self, item: InboxRequest) -> None:
        with self._lock:
            self._inbox[item.request_id] = item

    def all_inbox_requests(self) -> tuple[InboxRequest, ...]:
        with self._lock:
            return tuple(self._inbox.values())

    def encode_cursor(self, item_id: str) -> str:
        payload = item_id.encode("ascii")
        digest = hmac.new(self._secret, payload, hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(payload + b"." + digest).decode("ascii").rstrip("=")

    def decode_cursor(self, cursor: str) -> str:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
            raise NotificationError("INVALID_CURSOR")
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload, digest = raw.rsplit(b".", 1)
            expected = hmac.new(self._secret, payload, hashlib.sha256).digest()[:16]
            item_id = payload.decode("ascii")
        except (ValueError, UnicodeError):
            raise NotificationError("INVALID_CURSOR") from None
        if not hmac.compare_digest(digest, expected) or not _SAFE_ID.fullmatch(item_id):
            raise NotificationError("INVALID_CURSOR")
        return item_id


def _checked_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise NotificationError("INVALID_INPUT")
    return value


def _checked_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise NotificationError("INVALID_INPUT")
    return value.astimezone(timezone.utc)


def _plain_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > maximum:
        raise NotificationError("INVALID_INPUT")
    if "<" in value or ">" in value or any(ord(character) < 32 for character in value):
        raise NotificationError("INVALID_INPUT")
    return value


def _safe_deep_link(value: object, native_routes: frozenset[str]) -> str:
    if not isinstance(value, str) or len(value) > 512 or value != value.strip():
        raise NotificationError("UNSAFE_DEEP_LINK")
    if value.startswith("/") and not value.startswith("//"):
        parsed = urlsplit(value)
        first = parsed.path.strip("/").split("/", 1)[0]
        if parsed.scheme or parsed.netloc or first not in _SAFE_WEB_PATHS:
            raise NotificationError("UNSAFE_DEEP_LINK")
        return value
    parsed = urlsplit(value)
    if parsed.scheme != "sinsan-daon" or parsed.netloc != "app" or parsed.query or parsed.fragment:
        raise NotificationError("UNSAFE_DEEP_LINK")
    route = parsed.path.strip("/")
    if route not in native_routes:
        raise NotificationError("UNSAFE_DEEP_LINK")
    return value


class NotificationService:
    def __init__(
        self,
        *,
        repository: NotificationRepository,
        authorization_service: AuthorizationService,
        audit_store: AuditEventStore,
        clock: Callable[[], datetime],
        native_route_allowlist: frozenset[str] = frozenset({"operations", "inbox", "notifications"}),
    ) -> None:
        self._repository = repository
        self._authorization = authorization_service
        self._audit = audit_store
        self._clock = clock
        self._native_routes = native_route_allowlist

    def _authorize(self, principal: IdentityPrincipal, workspace_id: str, trace_id: str, policy_version: str) -> None:
        try:
            self._authorization.authorize_action(
                principal=principal, workspace_id=workspace_id, action=Action.VIEW,
                trace_id=trace_id, policy_version=policy_version,
            )
        except AuthorizationError as error:
            status = 404 if error.http_status == 404 else 403
            code = "RESOURCE_UNAVAILABLE" if status == 404 else "CURRENT_ACCESS_DENIED"
            raise NotificationError(code, status) from None

    def _validate_event(self, event: NotificationEvent) -> None:
        if not isinstance(event, NotificationEvent):
            raise NotificationError("INVALID_INPUT")
        for value in (
            event.event_id, event.tenant_id, event.workspace_id, event.resource_type,
            event.resource_id, event.trace_id, event.policy_version,
        ):
            _checked_id(value)
        if event.kind not in _ALLOWED_KINDS or event.severity not in _ALLOWED_SEVERITIES:
            raise NotificationError("INVALID_INPUT")
        _plain_text(event.title, maximum=160)
        _plain_text(event.summary, maximum=500)
        _safe_deep_link(event.deep_link, self._native_routes)

    def publish(
        self, event: NotificationEvent, *, candidates: tuple[IdentityPrincipal, ...]
    ) -> tuple[Notification, ...]:
        self._validate_event(event)
        created: list[Notification] = []
        for principal in candidates:
            if not isinstance(principal, IdentityPrincipal) or principal.tenant_id != event.tenant_id:
                continue
            try:
                self._authorize(principal, event.workspace_id, event.trace_id, event.policy_version)
            except NotificationError:
                continue
            dedupe_key = f"{event.event_id}|{principal.user_id}|{event.kind}"
            digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:24]
            now = _checked_time(self._clock())
            if now is None:  # pragma: no cover - clock contract
                raise NotificationError("INVALID_TIME")
            audit_event_id = f"audit-notification-{digest}"
            item = Notification(
                notification_id=f"notification-{digest}",
                tenant_id=event.tenant_id, workspace_id=event.workspace_id,
                recipient_id=principal.user_id, kind=event.kind, severity=event.severity,
                title=event.title, summary=event.summary, source_event_id=event.event_id,
                resource_type=event.resource_type, resource_id=event.resource_id,
                deep_link=event.deep_link, audit_event_id=audit_event_id,
                trace_id=event.trace_id, delivery_state=DeliveryState.DELIVERED,
                created_at=now, delivered_at=now, read_at=None, version=1,
            )
            if not self._repository.insert_if_absent(dedupe_key, item):
                continue
            self._audit.append(AuditEventDraft(
                event_id=audit_event_id, occurred_at=now, actor_id="notification-service",
                actor_type=ActorType.SERVICE, tenant_id=event.tenant_id,
                workspace_id=event.workspace_id, action="notification.created",
                target_type="notification", target_id=item.notification_id,
                outcome=AuditOutcome.SUCCEEDED, trace_id=event.trace_id,
                policy_version=event.policy_version,
                after={"recipient_id": principal.user_id, "kind": event.kind, "delivery_state": "delivered"},
                metadata={"source_event_id": event.event_id, "channel": "in_app"},
            ))
            created.append(item)
        return tuple(created)

    def _visible_notifications(
        self, principal: IdentityPrincipal, trace_id: str, policy_version: str
    ) -> tuple[Notification, ...]:
        candidates = tuple(
            item for item in self._repository.all_notifications()
            if item.tenant_id == principal.tenant_id and item.recipient_id == principal.user_id
        )
        visible: list[Notification] = []
        for item in candidates:
            self._authorize(principal, item.workspace_id, trace_id, policy_version)
            _safe_deep_link(item.deep_link, self._native_routes)
            visible.append(item)
        return tuple(sorted(visible, key=lambda item: (item.created_at, item.notification_id), reverse=True))

    def list_notifications(
        self, *, principal: IdentityPrincipal, limit: int, cursor: str | None,
        filters: dict[str, str], search: str | None, trace_id: str, policy_version: str,
    ) -> NotificationPage:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise NotificationError("INVALID_INPUT")
        items = list(self._visible_notifications(principal, trace_id, policy_version))
        if filters:
            if len(filters) != 1:
                raise NotificationError("INVALID_FILTER")
            key, value = next(iter(filters.items()))
            if key == "state":
                items = [item for item in items if item.read_state.value == value]
            elif key == "kind":
                items = [item for item in items if item.kind == value]
            elif key == "severity":
                items = [item for item in items if item.severity == value]
            else:
                raise NotificationError("INVALID_FILTER")
        if search is not None:
            term = _plain_text(search, maximum=128).casefold()
            items = [item for item in items if term in item.title.casefold() or term in item.summary.casefold()]
        start = 0
        if cursor is not None:
            after_id = self._repository.decode_cursor(cursor)
            indices = [index for index, item in enumerate(items) if item.notification_id == after_id]
            if not indices:
                raise NotificationError("INVALID_CURSOR")
            start = indices[0] + 1
        page = items[start:start + limit]
        next_cursor = None
        if start + limit < len(items) and page:
            next_cursor = self._repository.encode_cursor(page[-1].notification_id)
        unread = sum(item.read_at is None for item in self._visible_notifications(principal, trace_id, policy_version))
        return NotificationPage(tuple(page), next_cursor, unread)

    def get_notification(
        self, *, principal: IdentityPrincipal, notification_id: str,
        trace_id: str, policy_version: str,
    ) -> Notification:
        item = self._repository.notification_by_id(_checked_id(notification_id))
        if item is None or item.tenant_id != principal.tenant_id or item.recipient_id != principal.user_id:
            raise NotificationError("RESOURCE_UNAVAILABLE", 404)
        self._authorize(principal, item.workspace_id, trace_id, policy_version)
        _safe_deep_link(item.deep_link, self._native_routes)
        return item

    def mark_read(
        self, *, principal: IdentityPrincipal, notification_id: str,
        expected_etag: str, idempotency_key: str, requested_state: str,
        trace_id: str, policy_version: str,
    ) -> Notification:
        _checked_id(idempotency_key)
        if len(idempotency_key) < 16:
            raise NotificationError("INVALID_INPUT")
        if requested_state != ReadState.READ.value:
            raise NotificationError("IDEMPOTENCY_CONFLICT", 409)
        fingerprint = hashlib.sha256(
            f"{principal.user_id}|{notification_id}|{requested_state}|{expected_etag}".encode("utf-8")
        ).hexdigest()
        replay = self._repository.idempotency_result(idempotency_key)
        if replay is not None:
            if replay[0] != fingerprint:
                raise NotificationError("IDEMPOTENCY_CONFLICT", 409)
            return replay[1]
        item = self.get_notification(
            principal=principal, notification_id=notification_id,
            trace_id=trace_id, policy_version=policy_version,
        )
        if not isinstance(expected_etag, str) or expected_etag != item.etag:
            raise NotificationError("VERSION_CONFLICT", 412)
        if item.read_at is not None:
            raise NotificationError("INVALID_STATE_TRANSITION", 409)
        now = _checked_time(self._clock())
        if now is None:  # pragma: no cover
            raise NotificationError("INVALID_TIME")
        updated = replace(item, read_at=now, version=item.version + 1)
        self._repository.replace_notification(updated)
        self._repository.save_idempotency_result(idempotency_key, fingerprint, updated)
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        self._audit.append(AuditEventDraft(
            event_id=f"audit-notification-read-{digest}", occurred_at=now,
            actor_id=principal.user_id, actor_type=ActorType.USER,
            tenant_id=principal.tenant_id, workspace_id=item.workspace_id,
            action="notification.read", target_type="notification",
            target_id=item.notification_id, outcome=AuditOutcome.SUCCEEDED,
            trace_id=trace_id, policy_version=policy_version,
            before={"state": "unread", "version": item.version},
            after={"state": "read", "version": updated.version},
            metadata={"source_event_id": item.source_event_id},
        ))
        return updated

    def project_request(self, item: InboxRequest) -> None:
        if not isinstance(item, InboxRequest):
            raise NotificationError("INVALID_INPUT")
        for value in (
            item.request_id, item.tenant_id, item.workspace_id, item.actor_id,
            item.resource_type, item.resource_id,
        ):
            _checked_id(value)
        if item.request_kind not in _ALLOWED_REQUEST_KINDS or item.status not in _ALLOWED_REQUEST_STATES:
            raise NotificationError("INVALID_INPUT")
        _checked_time(item.due_at)
        _safe_deep_link(item.deep_link, self._native_routes)
        self._repository.upsert_inbox_request(item)

    def list_inbox(
        self, *, principal: IdentityPrincipal, limit: int, cursor: str | None,
        filters: dict[str, str], search: str | None, trace_id: str, policy_version: str,
    ) -> InboxPage:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise NotificationError("INVALID_INPUT")
        items = []
        for item in self._repository.all_inbox_requests():
            if item.tenant_id != principal.tenant_id:
                continue
            self._authorize(principal, item.workspace_id, trace_id, policy_version)
            _safe_deep_link(item.deep_link, self._native_routes)
            items.append(item)
        if filters:
            if len(filters) != 1:
                raise NotificationError("INVALID_FILTER")
            key, value = next(iter(filters.items()))
            if key == "kind":
                items = [item for item in items if item.request_kind == value]
            elif key == "state":
                items = [item for item in items if item.status == value]
            else:
                raise NotificationError("INVALID_FILTER")
        if search is not None:
            term = _plain_text(search, maximum=128).casefold()
            items = [
                item for item in items
                if term in item.request_kind.casefold()
                or term in item.status.casefold()
                or term in item.resource_type.casefold()
            ]
        items.sort(key=lambda item: ((item.due_at or datetime.max.replace(tzinfo=timezone.utc)), item.request_id))
        start = 0
        if cursor is not None:
            after_id = self._repository.decode_cursor(cursor)
            indices = [index for index, item in enumerate(items) if item.request_id == after_id]
            if not indices:
                raise NotificationError("INVALID_CURSOR")
            start = indices[0] + 1
        page = items[start:start + limit]
        next_cursor = None
        if start + limit < len(items) and page:
            next_cursor = self._repository.encode_cursor(page[-1].request_id)
        return InboxPage(tuple(page), next_cursor)


def parse_notification_filter(value: str | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, str) or not _FILTERS.fullmatch(value):
        raise NotificationError("INVALID_FILTER")
    key, item = value.split(":", 1)
    return {key: item}


def parse_inbox_filter(value: str | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, str) or not _INBOX_FILTERS.fullmatch(value):
        raise NotificationError("INVALID_FILTER")
    key, item = value.split(":", 1)
    return {key: item}


def notification_json(item: Notification) -> dict[str, object]:
    return {
        "id": item.notification_id, "workspace_id": item.workspace_id,
        "recipient_id": item.recipient_id, "kind": item.kind, "severity": item.severity,
        "title": item.title, "summary": item.summary,
        "source_event_id": item.source_event_id, "resource_type": item.resource_type,
        "resource_id": item.resource_id, "deep_link": item.deep_link,
        "audit_event_id": item.audit_event_id, "trace_id": item.trace_id,
        "delivery_state": item.delivery_state.value, "read_state": item.read_state.value,
        "created_at": item.created_at.isoformat(),
        "delivered_at": None if item.delivered_at is None else item.delivered_at.isoformat(),
        "read_at": None if item.read_at is None else item.read_at.isoformat(),
        "version": item.version,
    }


def inbox_json(item: InboxRequest) -> dict[str, object]:
    return {
        "request_id": item.request_id, "request_kind": item.request_kind,
        "status": item.status, "workspace_id": item.workspace_id, "actor_id": item.actor_id,
        "due_at": None if item.due_at is None else item.due_at.isoformat(),
        "resource_type": item.resource_type, "resource_id": item.resource_id,
        "deep_link": item.deep_link,
    }
