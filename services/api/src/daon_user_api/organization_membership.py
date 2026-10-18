"""Organization membership request and invitation domain contracts.

This module intentionally stops at the persistence/domain boundary.  HTTP
handlers and the existing identity/authorization services are wired in a
later work item, so existing login and workspace behaviour is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterator


class OrganizationWorkflowError(RuntimeError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class RequestState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MembershipState(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class InvitationState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class OrganizationCreationRequest:
    request_id: str
    applicant_user_id: str
    requested_org_name: str
    requested_org_identifier: str
    state: RequestState
    decision_reason: str | None
    decided_by: str | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OrganizationJoinRequest:
    request_id: str
    tenant_id: str
    user_id: str
    invitation_id: str | None
    state: RequestState
    decision_reason: str | None
    decided_by: str | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InvitationCode:
    invitation_id: str
    tenant_id: str
    created_by: str
    code_digest: str
    expires_at: datetime
    max_uses: int
    used_count: int
    state: InvitationState
    version: int
    created_at: datetime
    updated_at: datetime
    workspace_id: str | None = None


@dataclass(frozen=True, slots=True)
class TenantMembership:
    tenant_id: str
    user_id: str
    role: str
    state: MembershipState
    version: int
    updated_at: datetime
    join_workspace_id: str | None = None
    access_role: str | None = None
    access_state: str | None = None
    access_version: int | None = None


@dataclass(frozen=True, slots=True)
class OrganizationOperationResult:
    value: dict
    replayed: bool


_MANAGEMENT_ACTIONS = {
    "organization.join.create": "organization.join_request.created",
    "organization.join.decide": "organization.join_request.decided",
    "organization.member.role": "organization.membership.role_changed",
    "organization.member.state": "organization.membership.state_changed",
    "organization.invitation.create": "organization.invitation.created",
    "organization.invitation.revoke": "organization.invitation.revoked",
}


@dataclass(frozen=True, slots=True)
class OrganizationAuditRecord:
    action: str
    actor_id: str
    tenant_id: str
    target_id: str
    before: str | None
    after: str | None
    reason: str | None
    occurred_at: datetime


def _text(value: str, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 160:
        raise OrganizationWorkflowError(code)
    return value


def _id(value: str, code: str = "INVALID_ID") -> str:
    return _text(value, code)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OrganizationWorkflowError("INVALID_TIME")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _parse(value: str | datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        return _utc(parsed)
    except (ValueError, OverflowError) as error:
        raise OrganizationWorkflowError("INVALID_TIME") from error


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


class SqliteOrganizationRepository:
    """Transactional, restart-safe repository used by unit and local tests."""

    def __init__(self, path: str | Path, *, audit_sink: Callable[[OrganizationAuditRecord], None] | None = None, audit_store: Any = None, notification_inbox_enabled: bool = False) -> None:
        if type(notification_inbox_enabled) is not bool:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        self._notification_inbox_enabled = notification_inbox_enabled
        from .organization_access import OrganizationAccessBridge
        self._path = Path(path)
        self._lock = RLock()
        self._audit_sink = audit_sink
        self._audit_store = audit_store
        self._access_bridge = OrganizationAccessBridge()
        connection = self._connect()
        self._create_schema(connection)
        connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def close(self) -> None:
        """Repository opens short-lived transactional connections."""
        return None

    def idempotency_replay(self, *, operation: str, actor_id: str, key: str, fingerprint: str) -> str | None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT fingerprint,result_json FROM organization_idempotency WHERE operation=? AND actor_id=? AND idempotency_key=?",
                (_id(operation), _id(actor_id), _id(key)),
            ).fetchone()
        if row is None:
            return None
        if str(row[0]) != fingerprint:
            raise OrganizationWorkflowError("IDEMPOTENCY_KEY_REUSED", 409)
        return str(row[1])

    def idempotency_record(self, *, operation: str, actor_id: str, key: str, fingerprint: str, result: object, now: datetime) -> None:
        payload = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        try:
            with self.transaction() as connection:
                connection.execute(
                    "INSERT INTO organization_idempotency(operation,actor_id,idempotency_key,fingerprint,result_json,created_at) VALUES (?,?,?,?,?,?)",
                    (_id(operation), _id(actor_id), _id(key), fingerprint, payload, _iso(now)),
                )
        except OrganizationWorkflowError as error:
            if error.code != "PERSISTENCE_CONFLICT":
                raise
            replay = self.idempotency_replay(operation=operation, actor_id=actor_id, key=key, fingerprint=fingerprint)
            if replay != payload:
                raise OrganizationWorkflowError("IDEMPOTENCY_KEY_REUSED", 409) from error

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS organization_creation_requests (
              request_id TEXT PRIMARY KEY, applicant_user_id TEXT NOT NULL,
              requested_org_name TEXT NOT NULL, requested_org_identifier TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected')),
              decision_reason TEXT, decided_by TEXT, version INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS organization_creation_pending_unique
              ON organization_creation_requests(applicant_user_id)
              WHERE state = 'pending';
            CREATE TABLE IF NOT EXISTS invitation_codes (
              invitation_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, created_by TEXT NOT NULL,
              code_digest TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL,
              max_uses INTEGER NOT NULL CHECK(max_uses > 0), used_count INTEGER NOT NULL DEFAULT 0,
              state TEXT NOT NULL CHECK(state IN ('active','revoked','expired')),
              version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS organization_join_requests (
              request_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
              invitation_id TEXT REFERENCES invitation_codes(invitation_id),
              state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected')),
              decision_reason TEXT, decided_by TEXT, version INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS organization_join_pending_unique
              ON organization_join_requests(tenant_id,user_id) WHERE state = 'pending';
            CREATE TABLE IF NOT EXISTS tenant_memberships (
              tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('active','suspended')),
              version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
              PRIMARY KEY(tenant_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS tenant_membership_role_history (
              history_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
              actor_id TEXT NOT NULL, previous_role TEXT, next_role TEXT,
              previous_state TEXT, next_state TEXT, reason TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS organization_idempotency (
              operation TEXT NOT NULL, actor_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
              fingerprint TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY(operation, actor_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS organization_operation_outbox (
              actor_id TEXT NOT NULL, operation TEXT NOT NULL, idempotency_key TEXT NOT NULL,
              tenant_id TEXT NOT NULL, target_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
              response_json TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, audit_json TEXT NOT NULL,
              trace_id TEXT NOT NULL, policy_version TEXT NOT NULL, created_at TEXT NOT NULL,
              delivered_at TEXT, PRIMARY KEY(actor_id,operation,idempotency_key)
            );
            CREATE TRIGGER IF NOT EXISTS organization_operation_immutable BEFORE UPDATE ON organization_operation_outbox
            WHEN NEW.actor_id<>OLD.actor_id OR NEW.operation<>OLD.operation OR NEW.idempotency_key<>OLD.idempotency_key
              OR NEW.tenant_id<>OLD.tenant_id OR NEW.target_id<>OLD.target_id OR NEW.request_fingerprint<>OLD.request_fingerprint
              OR NEW.response_json<>OLD.response_json OR NEW.event_id<>OLD.event_id OR NEW.audit_json<>OLD.audit_json
              OR NEW.trace_id<>OLD.trace_id OR NEW.policy_version<>OLD.policy_version OR NEW.created_at<>OLD.created_at
              OR OLD.delivered_at IS NOT NULL OR NEW.delivered_at IS NULL
            BEGIN SELECT RAISE(ABORT,'ORGANIZATION_OPERATION_IMMUTABLE'); END;
            CREATE TRIGGER IF NOT EXISTS organization_operation_no_delete BEFORE DELETE ON organization_operation_outbox
            BEGIN SELECT RAISE(ABORT,'ORGANIZATION_OPERATION_DELETE_BLOCKED'); END;
            """
        )
        for table, column in (("invitation_codes", "workspace_id"), ("tenant_memberships", "join_workspace_id")):
            if column not in {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    @contextmanager
    def _participate(self, connection):
        if connection is not None:
            yield connection
        else:
            with self.transaction() as owned:
                yield owned

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except OrganizationWorkflowError:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            except sqlite3.IntegrityError as error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise OrganizationWorkflowError("PERSISTENCE_CONFLICT", 409) from error
            except sqlite3.Error as error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503) from error
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    @staticmethod
    def _creation(row: sqlite3.Row) -> OrganizationCreationRequest:
        return OrganizationCreationRequest(str(row["request_id"]), str(row["applicant_user_id"]), str(row["requested_org_name"]), str(row["requested_org_identifier"]), RequestState(row["state"]), row["decision_reason"], row["decided_by"], int(row["version"]), _parse(row["created_at"]), _parse(row["updated_at"]))

    @staticmethod
    def _join(row: sqlite3.Row) -> OrganizationJoinRequest:
        return OrganizationJoinRequest(str(row["request_id"]), str(row["tenant_id"]), str(row["user_id"]), row["invitation_id"], RequestState(row["state"]), row["decision_reason"], row["decided_by"], int(row["version"]), _parse(row["created_at"]), _parse(row["updated_at"]))

    @staticmethod
    def _invitation(row) -> InvitationCode:
        return InvitationCode(str(row["invitation_id"]), str(row["tenant_id"]), str(row["created_by"]), str(row["code_digest"]), _parse(row["expires_at"]), int(row["max_uses"]), int(row["used_count"]), InvitationState(row["state"]), int(row["version"]), _parse(row["created_at"]), _parse(row["updated_at"]), row["workspace_id"])

    @staticmethod
    def _member(row) -> TenantMembership:
        return TenantMembership(str(row["tenant_id"]), str(row["user_id"]), str(row["role"]), MembershipState(row["state"]), int(row["version"]), _parse(row["updated_at"]), row["join_workspace_id"])

    @staticmethod
    def _management_view(operation: str, value) -> dict:
        data = dict(value) if isinstance(value, dict) else asdict(value)
        invitation_fields = {"invitation_id", "tenant_id", "workspace_id", "created_by", "expires_at", "max_uses", "used_count", "state", "version", "created_at", "updated_at"}
        join_fields = {"request_id", "tenant_id", "user_id", "invitation_id", "state", "decision_reason", "decided_by", "version", "created_at", "updated_at"}
        member_fields = {"tenant_id", "user_id", "role", "state", "version", "updated_at", "join_workspace_id", "access_role", "access_state", "access_version"}
        if operation.startswith("organization.invitation."):
            data.pop("code_digest", None)
            data.setdefault("workspace_id", None)
            allowed = invitation_fields
        elif operation.startswith("organization.join."):
            allowed = join_fields
        else:
            for key in ("join_workspace_id", "access_role", "access_state", "access_version"):
                data.setdefault(key, None)
            allowed = member_fields
        if set(data) != allowed:
            raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503)
        for key, item in data.items():
            if isinstance(item, Enum): item = item.value
            if key.endswith("_at"):
                item = _iso(_parse(item))
            elif key in {"version", "access_version", "max_uses", "used_count"}:
                if key == "access_version" and item is None: continue
                minimum = 0 if key == "used_count" else 1
                if type(item) is not int or not minimum <= item <= 9007199254740991:
                    raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503)
            elif item is None:
                if key not in {"workspace_id", "join_workspace_id", "access_role", "access_state", "decision_reason", "decided_by", "invitation_id"}:
                    raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503)
            elif not isinstance(item, str) or len(item) > (1000 if key == "decision_reason" else 160) or (key != "decision_reason" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", item) is None):
                raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503)
            data[key] = item
        return data

    def _management_now(self, connection) -> datetime:
        return datetime.now(timezone.utc)

    def _lock_management_key(self, connection, operation, actor_id, key) -> None:
        # SQLite의 BEGIN IMMEDIATE가 공통 DB의 쓰기를 직렬화한다.
        return None

    def _management_scope(self, connection, operation, actor_id, tenant_id, target_id, payload):
        row = None
        workspace_id = payload.get("workspace_id")
        if operation == "organization.join.create":
            if payload.get("invitation_code") is not None:
                row = connection.execute("SELECT * FROM invitation_codes WHERE code_digest=?", (_digest(_text(payload["invitation_code"], "INVALID_INVITATION_CODE")),)).fetchone()
                if row is None: raise OrganizationWorkflowError("INVITATION_INVALID", 400)
                if tenant_id is not None and tenant_id != row["tenant_id"]:
                    raise OrganizationWorkflowError("INVITATION_INVALID", 400)
                tenant_id, workspace_id = str(row["tenant_id"]), row["workspace_id"]
        elif operation == "organization.join.decide":
            row = connection.execute("SELECT * FROM organization_join_requests WHERE request_id=?", (target_id,)).fetchone()
            if row is None or tenant_id != row["tenant_id"]: raise OrganizationWorkflowError("REQUEST_NOT_FOUND", 404)
            if row["invitation_id"] is not None:
                invitation = connection.execute("SELECT workspace_id FROM invitation_codes WHERE invitation_id=?", (row["invitation_id"],)).fetchone()
                workspace_id = None if invitation is None else invitation[0]
        elif operation == "organization.invitation.revoke":
            row = connection.execute("SELECT * FROM invitation_codes WHERE tenant_id=? AND invitation_id=?", (tenant_id, target_id)).fetchone()
            if row is None: raise OrganizationWorkflowError("INVITATION_CONFLICT", 409)
            workspace_id = row["workspace_id"]
        elif operation.startswith("organization.member."):
            row = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, target_id)).fetchone()
            if row is None: raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
            workspace_id = row["join_workspace_id"]
        tenant_id = _id(tenant_id)
        return tenant_id, workspace_id, row

    def _set_management_scope(self, connection, actor_id, tenant_id) -> None:
        return None

    def _lock_management_target(self, connection, operation, actor_id, tenant_id, target_id, payload, workspace_id, row) -> None:
        return None

    def _record_management_operation(self, connection, *, operation, actor_id, tenant_id, target_id, key, fingerprint, value, audit, now, trace_id, policy_version):
        from .audit import AuditValidationError, _validate_draft
        event_id = _new_id("org-event")
        audit_json = json.dumps(audit, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        try:
            # 중앙 sink의 순수 입력 검증을 재사용한다. commit 전 네트워크 I/O는 없다.
            _validate_draft(self._management_audit_draft({"event_id": event_id, "actor_id": actor_id,
                "tenant_id": tenant_id, "operation": operation, "target_id": target_id,
                "audit_json": audit_json, "created_at": now, "trace_id": trace_id, "policy_version": policy_version}))
        except AuditValidationError as error:
            raise OrganizationWorkflowError("AUDIT_INTENT_INVALID") from error
        connection.execute("INSERT INTO organization_operation_outbox(actor_id,operation,idempotency_key,tenant_id,target_id,request_fingerprint,response_json,event_id,audit_json,trace_id,policy_version,created_at,delivered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (actor_id, operation, key, tenant_id, target_id, fingerprint, json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False), event_id, audit_json, trace_id, policy_version, _iso(now)))
        if (getattr(self, "_notification_inbox_enabled", False) is True
                and operation in {"organization.member.role", "organization.member.state"}
                and (operation != "organization.member.state" or (
                    value["join_workspace_id"] is not None and audit["before"] != audit["after"]))
                and hasattr(connection, "_conn")):
            from .notification import NotificationError
            from .notification_events import enqueue_organization_role_event
            try:
                enqueue_organization_role_event(connection, origin_event_id=event_id, tenant_id=tenant_id,
                    recipient_id=target_id, workspace_id=value["join_workspace_id"])
            except NotificationError:
                raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503) from None

    def execute_management_operation(self, *, operation, actor_id, tenant_id, target_id, payload, idempotency_key, request_fingerprint, trace_id, policy_version) -> OrganizationOperationResult:
        if operation not in _MANAGEMENT_ACTIONS or not isinstance(payload, dict):
            raise OrganizationWorkflowError("INVALID_INPUT")
        for identifier in (actor_id, tenant_id, target_id):
            if identifier is not None and (not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", identifier) is None):
                raise OrganizationWorkflowError("INVALID_ID")
        if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128:
            raise OrganizationWorkflowError("IDEMPOTENCY_KEY_INVALID")
        if not isinstance(request_fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", request_fingerprint) is None:
            raise OrganizationWorkflowError("INVALID_INPUT")
        _id(actor_id); _text(trace_id, "INVALID_TRACE"); _text(policy_version, "INVALID_POLICY")
        with self.transaction() as connection:
            self._lock_management_key(connection, operation, actor_id, idempotency_key)
            tenant_id, workspace_id, original = self._management_scope(connection, operation, actor_id, tenant_id, target_id, payload)
            self._set_management_scope(connection, actor_id, tenant_id)
            row = connection.execute("SELECT * FROM organization_operation_outbox WHERE actor_id=? AND operation=? AND idempotency_key=?", (actor_id, operation, idempotency_key)).fetchone()
            legacy = False
            if row is None:
                row = connection.execute("SELECT fingerprint,result_json FROM organization_idempotency WHERE actor_id=? AND operation=? AND idempotency_key=?", (actor_id, operation, idempotency_key)).fetchone()
                legacy = row is not None
            if row is not None:
                if row["fingerprint" if legacy else "request_fingerprint"] != request_fingerprint:
                    raise OrganizationWorkflowError("IDEMPOTENCY_KEY_REUSED", 409)
                try:
                    value = self._management_view(operation, json.loads(row["result_json" if legacy else "response_json"]))
                except (ValueError, TypeError, KeyError) as error:
                    raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503) from error
                value_target = value.get("invitation_id") if operation.startswith("organization.invitation.") else value.get("request_id") if operation.startswith("organization.join.") else value["user_id"]
                if value["tenant_id"] != tenant_id or (target_id is not None and value_target != target_id) or (operation == "organization.join.create" and value["user_id"] != actor_id) or (operation == "organization.invitation.create" and value["created_by"] != actor_id):
                    raise OrganizationWorkflowError("IDEMPOTENCY_KEY_REUSED", 409)
                return OrganizationOperationResult(value, True)
            self._lock_management_target(connection, operation, actor_id, tenant_id, target_id, payload, workspace_id, original)
            locked_tenant, locked_workspace, original = self._management_scope(connection, operation, actor_id, tenant_id, target_id, payload)
            if (locked_tenant, locked_workspace) != (tenant_id, workspace_id):
                raise OrganizationWorkflowError("ORGANIZATION_SCOPE_CHANGED", 409)
            now = self._management_now(connection)
            reason = payload.get("reason")
            if reason is not None and (not isinstance(reason, str) or len(reason) > 1000):
                raise OrganizationWorkflowError("INVALID_INPUT")
            before = None if original is None else str(original["role"] if operation == "organization.member.role" else original["state"])
            access = None
            if operation == "organization.invitation.create":
                expires_at = _parse(payload["expires_at"])
                item = self.create_invitation(tenant_id=tenant_id, created_by=actor_id, code=payload["code"], expires_at=expires_at, max_uses=payload["max_uses"], workspace_id=workspace_id, now=now, connection=connection)
            elif operation == "organization.invitation.revoke":
                item = self.revoke_invitation(tenant_id=tenant_id, invitation_id=target_id, actor_id=actor_id, expected_version=payload["expected_version"], now=now, connection=connection)
            elif operation == "organization.join.create":
                # 확정 영수증 replay 뒤, 신규 신청에만 현재 초대의 정확한 조직 Workspace를 요구한다.
                if original is None or workspace_id is None:
                    raise OrganizationWorkflowError("INVITATION_INVALID", 400)
                try:
                    self._access_bridge._workspace(connection, tenant_id, workspace_id)
                except OrganizationWorkflowError as error:
                    raise OrganizationWorkflowError("INVITATION_INVALID", 400) from error
                pending = connection.execute("SELECT request_id FROM organization_join_requests WHERE tenant_id=? AND user_id=? AND state='pending'", (tenant_id, actor_id)).fetchone()
                if pending is not None: raise OrganizationWorkflowError("JOIN_REQUEST_ALREADY_PENDING", 409)
                item = self.create_join_request(tenant_id=tenant_id, user_id=actor_id, invitation_code=payload.get("invitation_code"), now=now, connection=connection)
            elif operation == "organization.join.decide":
                if original is None or original["state"] != RequestState.PENDING.value or int(original["version"]) != payload["expected_version"]:
                    raise OrganizationWorkflowError("REQUEST_CONFLICT", 409)
                if payload["approved"]:
                    if original["invitation_id"] is None or workspace_id is None:
                        raise OrganizationWorkflowError("ORGANIZATION_INVITATION_SCOPE_REQUIRED", 409)
                    if payload.get("role", "member") != "member":
                        raise OrganizationWorkflowError("INVALID_ROLE", 422)
                    if connection.execute("SELECT 1 FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, original["user_id"])).fetchone() is not None:
                        raise OrganizationWorkflowError("ORGANIZATION_MEMBERSHIP_CONFLICT", 409)
                item = self.decide_join_request(request_id=target_id, actor_id=actor_id, approved=payload["approved"], expected_version=payload["expected_version"], reason=reason, role=payload.get("role", "member"), now=now, connection=connection)
                if payload["approved"]:
                    access = self._access_bridge.approve_join_access(connection=connection, tenant_id=tenant_id, workspace_id=workspace_id, user_id=original["user_id"], now=now)
                    linked = connection.execute("UPDATE tenant_memberships SET join_workspace_id=? WHERE tenant_id=? AND user_id=? AND join_workspace_id IS NULL AND role='member' AND state='active' AND version=1", (workspace_id, tenant_id, original["user_id"]))
                    if linked.rowcount != 1:
                        raise OrganizationWorkflowError("ORGANIZATION_MEMBERSHIP_CONFLICT", 409)
            elif operation == "organization.member.role":
                if workspace_id is None:
                    raise OrganizationWorkflowError("ORGANIZATION_INVITATION_SCOPE_REQUIRED", 409)
                if (payload.get("workspace_id") != workspace_id
                        or type(payload.get("expected_version")) is not int or original["version"] != payload["expected_version"]):
                    raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
                before = self._access_bridge.read_join_access(connection=connection, tenant_id=tenant_id, workspace_id=workspace_id, user_id=target_id)["access_role"]
                access = self._access_bridge.change_join_role(connection=connection, tenant_id=tenant_id, workspace_id=workspace_id,
                    user_id=target_id, role=payload["role"], expected_auth_version=payload.get("expected_auth_version"), now=now)
                changed = connection.execute("UPDATE tenant_memberships SET version=version+1,updated_at=? WHERE tenant_id=? AND user_id=? AND version=? AND role='member' AND state='active'",
                    (_iso(now), tenant_id, target_id, payload["expected_version"]))
                if changed.rowcount != 1:
                    raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
                # 실행 역할은 auth에만 저장한다. workflow member와 과거 역할 이력을 덮지 않는다.
                item = asdict(self._member(connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, target_id)).fetchone())) | access
            else:
                item = self.set_membership(tenant_id=tenant_id, user_id=target_id, actor_id=actor_id, state=MembershipState.ACTIVE if payload.get("active", True) else MembershipState.SUSPENDED, expected_version=payload["expected_version"], reason=reason, now=now, connection=connection)
                if workspace_id is not None:
                    access = self._access_bridge.read_join_access(connection=connection, tenant_id=tenant_id, workspace_id=workspace_id, user_id=target_id)
                    item = asdict(item) | access
            value = self._management_view(operation, item)
            actual_target = value.get("invitation_id") if operation.startswith("organization.invitation.") else value.get("request_id") if operation.startswith("organization.join.") else value["user_id"]
            audit = {"action": _MANAGEMENT_ACTIONS[operation], "workspace_id": workspace_id, "before": before, "after": access["access_role"] if operation == "organization.member.role" else value["state"], "reason": reason, "version": value["version"], "auth_version": access["access_version"] if access is not None else value.get("access_version")}
            self._record_management_operation(connection, operation=operation, actor_id=actor_id, tenant_id=tenant_id, target_id=actual_target, key=idempotency_key, fingerprint=request_fingerprint, value=value, audit=audit, now=now, trace_id=trace_id, policy_version=policy_version)
            return OrganizationOperationResult(value, False)

    def _pending_management_audits(self, limit: int):
        with self.transaction() as connection:
            return connection.execute(
                "SELECT event_id,actor_id,tenant_id,operation,target_id,audit_json,trace_id,policy_version,created_at "
                "FROM organization_operation_outbox WHERE delivered_at IS NULL ORDER BY created_at,event_id LIMIT ?",
                (limit,),
            ).fetchall()

    def _ack_management_audit(self, event_id: str) -> bool:
        with self.transaction() as connection:
            return connection.execute(
                "UPDATE organization_operation_outbox SET delivered_at=? WHERE event_id=? AND delivered_at IS NULL",
                (_iso(datetime.now(timezone.utc)), event_id),
            ).rowcount == 1

    @staticmethod
    def _management_audit_draft(row):
        from .audit import ActorType, AuditEventDraft, AuditOutcome
        audit = json.loads(row["audit_json"])
        return AuditEventDraft(
            event_id=row["event_id"], occurred_at=_parse(row["created_at"]),
            actor_id=row["actor_id"], actor_type=ActorType.USER, tenant_id=row["tenant_id"],
            workspace_id=audit["workspace_id"], action=audit["action"],
            target_type="invitation" if row["operation"].startswith("organization.invitation.") else "join_request" if row["operation"].startswith("organization.join.") else "membership",
            target_id=row["target_id"], outcome=AuditOutcome.SUCCEEDED,
            trace_id=row["trace_id"], policy_version=row["policy_version"],
            before=None if audit["before"] is None else {"value": audit["before"]},
            after=None if audit["after"] is None else {"value": audit["after"]},
            metadata={key: audit[key] for key in ("reason", "version", "auth_version")},
        )

    def dispatch_pending_management_audits(self, *, stop=None, limit: int = 100) -> int:
        """중앙 감사가 확인된 이벤트만 ack한다. 실패는 pending으로 남긴다."""
        from .audit import AuditDuplicateEventError, AuditEventDraft, PostgresSecurityAuditStore
        if self._audit_store is None or (stop is not None and stop.is_set()):
            return 0
        if type(limit) is not int or not 1 <= limit <= 100:
            raise OrganizationWorkflowError("INVALID_LIMIT")
        try:
            rows = self._pending_management_audits(limit)
        except Exception:
            return 0
        delivered = 0
        for row in rows:
            if stop is not None and stop.is_set():
                break
            try:
                draft = self._management_audit_draft(row)
                try:
                    append = getattr(self._audit_store, "_append_admin_role", self._audit_store.append)
                    append(draft)
                except AuditDuplicateEventError:
                    existing = (self._audit_store.read(draft.event_id, tenant_id=draft.tenant_id)
                                if isinstance(self._audit_store, PostgresSecurityAuditStore)
                                else self._audit_store.read(draft.event_id))
                    if existing is None or any(getattr(existing, field.name) != getattr(draft, field.name)
                                               for field in fields(AuditEventDraft)):
                        continue
                if stop is not None and stop.is_set():
                    break
                delivered += int(self._ack_management_audit(draft.event_id))
            except Exception:
                # 저장 완료 응답을 감사 장애로 오염시키지 않고 다음 이벤트도 처리한다.
                continue
        return delivered

    def _emit(self, record: OrganizationAuditRecord) -> None:
        if self._audit_sink is not None:
            self._audit_sink(record)

    def create_organization_request(self, *, applicant_user_id: str, organization_name: str, organization_identifier: str, now: datetime) -> OrganizationCreationRequest:
        applicant_user_id = _id(applicant_user_id); name = _text(organization_name, "INVALID_ORGANIZATION_NAME"); identifier = _text(organization_identifier, "INVALID_ORGANIZATION_IDENTIFIER"); timestamp = _iso(now); request_id = _new_id("orgreq")
        with self.transaction() as connection:
            connection.execute("INSERT INTO organization_creation_requests VALUES (?,?,?,?,?,?,?,?,?,?)", (request_id, applicant_user_id, name, identifier, RequestState.PENDING.value, None, None, 1, timestamp, timestamp))
            row = connection.execute("SELECT * FROM organization_creation_requests WHERE request_id=?", (request_id,)).fetchone()
        return self._creation(row)

    def decide_organization_request(self, *, request_id: str, actor_id: str, approved: bool, expected_version: int, reason: str | None, now: datetime) -> OrganizationCreationRequest:
        request_id = _id(request_id); actor_id = _id(actor_id); timestamp = _iso(now)
        if not isinstance(expected_version, int) or expected_version < 1: raise OrganizationWorkflowError("INVALID_VERSION")
        state = RequestState.APPROVED.value if approved else RequestState.REJECTED.value
        with self.transaction() as connection:
            result = connection.execute("UPDATE organization_creation_requests SET state=?,decision_reason=?,decided_by=?,version=version+1,updated_at=? WHERE request_id=? AND state='pending' AND version=?", (state, reason, actor_id, timestamp, request_id, expected_version))
            if result.rowcount == 0: raise OrganizationWorkflowError("REQUEST_CONFLICT", 409)
            row = connection.execute("SELECT * FROM organization_creation_requests WHERE request_id=?", (request_id,)).fetchone()
            self._emit(OrganizationAuditRecord("organization.creation_request.decided", actor_id, "organization-public", request_id, RequestState.PENDING.value, state, reason, _utc(now)))
        return self._creation(row)

    def create_invitation(self, *, tenant_id: str, created_by: str, code: str, expires_at: datetime, max_uses: int, now: datetime, workspace_id: str | None = None, connection=None) -> InvitationCode:
        tenant_id = _id(tenant_id); created_by = _id(created_by); code = _text(code, "INVALID_INVITATION_CODE")
        expires = _utc(expires_at); timestamp = _iso(now)
        if expires <= _utc(now) or not isinstance(max_uses, int) or max_uses < 1: raise OrganizationWorkflowError("INVALID_INVITATION")
        invitation_id = _new_id("invite"); digest = _digest(code)
        with self._participate(connection) as connection:
            connection.execute("INSERT INTO invitation_codes(invitation_id,tenant_id,created_by,code_digest,expires_at,max_uses,used_count,state,version,created_at,updated_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (invitation_id, tenant_id, created_by, digest, _iso(expires), max_uses, 0, InvitationState.ACTIVE.value, 1, timestamp, timestamp, workspace_id))
            row = connection.execute("SELECT * FROM invitation_codes WHERE invitation_id=?", (invitation_id,)).fetchone()
            return self._invitation(row)

    def revoke_invitation(self, *, tenant_id: str, invitation_id: str, actor_id: str, expected_version: int, now: datetime, connection=None) -> InvitationCode:
        tenant_id = _id(tenant_id); invitation_id = _id(invitation_id); actor_id = _id(actor_id); timestamp = _iso(now)
        standalone = connection is None
        if not isinstance(expected_version, int) or expected_version < 1: raise OrganizationWorkflowError("INVALID_VERSION")
        with self._participate(connection) as connection:
            result = connection.execute("UPDATE invitation_codes SET state='revoked',version=version+1,updated_at=? WHERE invitation_id=? AND tenant_id=? AND state='active' AND version=? AND expires_at>?", (timestamp, invitation_id, tenant_id, expected_version, timestamp))
            if result.rowcount == 0: raise OrganizationWorkflowError("INVITATION_CONFLICT", 409)
            row = connection.execute("SELECT * FROM invitation_codes WHERE invitation_id=?", (invitation_id,)).fetchone()
            if standalone:
                self._emit(OrganizationAuditRecord("organization.invitation.revoked", actor_id, tenant_id, invitation_id, InvitationState.ACTIVE.value, InvitationState.REVOKED.value, None, _utc(now)))
            return self._invitation(row)

    def create_join_request(self, *, tenant_id: str, user_id: str, now: datetime, invitation_code: str | None = None, connection=None) -> OrganizationJoinRequest:
        tenant_id = _id(tenant_id); user_id = _id(user_id); timestamp = _iso(now); request_id = _new_id("joinreq")
        with self._participate(connection) as connection:
            invitation_id = None
            if invitation_code is not None:
                invitation = connection.execute("SELECT * FROM invitation_codes WHERE code_digest=?", (_digest(_text(invitation_code, "INVALID_INVITATION_CODE")),)).fetchone()
                if invitation is None or str(invitation["tenant_id"]) != tenant_id or invitation["state"] != InvitationState.ACTIVE.value or _parse(invitation["expires_at"]) <= _utc(now) or int(invitation["used_count"]) >= int(invitation["max_uses"]):
                    raise OrganizationWorkflowError("INVITATION_INVALID", 400)
                invitation_id = str(invitation["invitation_id"])
            connection.execute("INSERT INTO organization_join_requests VALUES (?,?,?,?,?,?,?,?,?,?)", (request_id, tenant_id, user_id, invitation_id, RequestState.PENDING.value, None, None, 1, timestamp, timestamp))
            row = connection.execute("SELECT * FROM organization_join_requests WHERE request_id=?", (request_id,)).fetchone()
            return self._join(row)

    def create_join_request_by_invitation(self, *, user_id: str, invitation_code: str, now: datetime, connection=None) -> OrganizationJoinRequest:
        """Create a pending join request by resolving an active invitation code.

        The plaintext code is never persisted. Resolving the tenant here keeps
        the user-facing onboarding flow from requiring an internal tenant ID.
        """
        user_id = _id(user_id)
        code = _text(invitation_code, "INVALID_INVITATION_CODE")
        timestamp = _iso(now)
        request_id = _new_id("joinreq")
        with self._participate(connection) as connection:
            invitation = connection.execute(
                "SELECT * FROM invitation_codes WHERE code_digest=?",
                (_digest(code),),
            ).fetchone()
            if (
                invitation is None
                or invitation["state"] != InvitationState.ACTIVE.value
                or _parse(invitation["expires_at"]) <= _utc(now)
                or int(invitation["used_count"]) >= int(invitation["max_uses"])
            ):
                raise OrganizationWorkflowError("INVITATION_INVALID", 400)
            connection.execute(
                "INSERT INTO organization_join_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
                (request_id, str(invitation["tenant_id"]), user_id, str(invitation["invitation_id"]),
                 RequestState.PENDING.value, None, None, 1, timestamp, timestamp),
            )
            row = connection.execute(
                "SELECT * FROM organization_join_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            return self._join(row)

    def decide_join_request(self, *, request_id: str, actor_id: str, approved: bool, expected_version: int, reason: str | None, role: str, now: datetime, connection=None) -> OrganizationJoinRequest:
        request_id = _id(request_id); actor_id = _id(actor_id); role = _text(role, "INVALID_ROLE"); timestamp = _iso(now)
        standalone = connection is None
        if not isinstance(expected_version, int) or expected_version < 1: raise OrganizationWorkflowError("INVALID_VERSION")
        state = RequestState.APPROVED.value if approved else RequestState.REJECTED.value
        with self._participate(connection) as connection:
            row = connection.execute("SELECT * FROM organization_join_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row["state"] != RequestState.PENDING.value or int(row["version"]) != expected_version: raise OrganizationWorkflowError("REQUEST_CONFLICT", 409)
            if approved and row["invitation_id"] is not None:
                invitation = connection.execute("SELECT * FROM invitation_codes WHERE invitation_id=?", (row["invitation_id"],)).fetchone()
                if invitation is None or invitation["state"] != InvitationState.ACTIVE.value or _parse(invitation["expires_at"]) <= _utc(now) or int(invitation["used_count"]) >= int(invitation["max_uses"]): raise OrganizationWorkflowError("INVITATION_INVALID", 400)
                used = connection.execute("UPDATE invitation_codes SET used_count=used_count+1,version=version+1,updated_at=? WHERE invitation_id=? AND state='active' AND version=? AND used_count<max_uses AND expires_at>?", (timestamp, row["invitation_id"], invitation["version"], timestamp))
                if used.rowcount != 1: raise OrganizationWorkflowError("INVITATION_CONFLICT", 409)
            changed = connection.execute("UPDATE organization_join_requests SET state=?,decision_reason=?,decided_by=?,version=version+1,updated_at=? WHERE request_id=? AND version=? AND state='pending'", (state, reason, actor_id, timestamp, request_id, expected_version))
            if changed.rowcount != 1: raise OrganizationWorkflowError("REQUEST_CONFLICT", 409)
            if approved:
                connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role,state,version,updated_at) VALUES (?,?,?,'active',1,?) ON CONFLICT(tenant_id,user_id) DO UPDATE SET role=excluded.role,state='active',version=tenant_memberships.version+1,updated_at=excluded.updated_at", (row["tenant_id"], row["user_id"], role, timestamp))
                connection.execute("INSERT INTO tenant_membership_role_history VALUES (?,?,?,?,?,?,?,?,?,?)", (_new_id("role"), row["tenant_id"], row["user_id"], actor_id, None, role, None, MembershipState.ACTIVE.value, None, timestamp))
            result = connection.execute("SELECT * FROM organization_join_requests WHERE request_id=?", (request_id,)).fetchone()
            if standalone:
                self._emit(OrganizationAuditRecord("organization.join_request.decided", actor_id, str(row["tenant_id"]), request_id, RequestState.PENDING.value, state, reason, _utc(now)))
            return self._join(result)

    def set_membership(self, *, tenant_id: str, user_id: str, actor_id: str, state: MembershipState, expected_version: int, reason: str | None, now: datetime, connection=None) -> TenantMembership:
        tenant_id = _id(tenant_id); user_id = _id(user_id); actor_id = _id(actor_id); timestamp = _iso(now)
        standalone = connection is None
        if not isinstance(state, MembershipState) or not isinstance(expected_version, int) or expected_version < 1: raise OrganizationWorkflowError("INVALID_INPUT")
        with self._participate(connection) as connection:
            row = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, user_id)).fetchone()
            if row is None or int(row["version"]) != expected_version: raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
            changed = connection.execute("UPDATE tenant_memberships SET state=?,version=version+1,updated_at=? WHERE tenant_id=? AND user_id=? AND version=?", (state.value, timestamp, tenant_id, user_id, expected_version))
            if changed.rowcount != 1: raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
            connection.execute("INSERT INTO tenant_membership_role_history VALUES (?,?,?,?,?,?,?,?,?,?)", (_new_id("role"), tenant_id, user_id, actor_id, row["role"], row["role"], row["state"], state.value, reason, timestamp))
            if standalone:
                self._emit(OrganizationAuditRecord("organization.membership.state_changed", actor_id, tenant_id, user_id, str(row["state"]), state.value, reason, _utc(now)))
            result = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, user_id)).fetchone()
            return self._member(result)

    def change_role(self, *, tenant_id: str, user_id: str, actor_id: str, role: str, expected_version: int, reason: str | None, now: datetime, connection=None) -> TenantMembership:
        tenant_id = _id(tenant_id); user_id = _id(user_id); actor_id = _id(actor_id); role = _text(role, "INVALID_ROLE"); timestamp = _iso(now)
        standalone = connection is None
        if not isinstance(expected_version, int) or expected_version < 1: raise OrganizationWorkflowError("INVALID_VERSION")
        with self._participate(connection) as connection:
            row = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, user_id)).fetchone()
            if row is None or int(row["version"]) != expected_version: raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
            changed = connection.execute("UPDATE tenant_memberships SET role=?,version=version+1,updated_at=? WHERE tenant_id=? AND user_id=? AND version=?", (role, timestamp, tenant_id, user_id, expected_version))
            if changed.rowcount != 1: raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
            connection.execute("INSERT INTO tenant_membership_role_history VALUES (?,?,?,?,?,?,?,?,?,?)", (_new_id("role"), tenant_id, user_id, actor_id, row["role"], role, row["state"], row["state"], reason, timestamp))
            if standalone:
                self._emit(OrganizationAuditRecord("organization.membership.role_changed", actor_id, tenant_id, user_id, str(row["role"]), role, reason, _utc(now)))
            result = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, user_id)).fetchone()
            return self._member(result)

    def list_members(self, tenant_id: str) -> tuple[TenantMembership, ...]:
        tenant_id = _id(tenant_id)
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? ORDER BY user_id", (tenant_id,)).fetchall()
            result = []
            for row in rows:
                member = self._member(row)
                if member.join_workspace_id is not None:
                    access = self._access_bridge.read_join_access(connection=connection, tenant_id=tenant_id, workspace_id=member.join_workspace_id, user_id=member.user_id)
                    member = TenantMembership(**(asdict(member) | access))
                result.append(member)
        return tuple(result)

    def list_invitations(self, *, tenant_id: str, now: datetime) -> tuple[dict, ...]:
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM invitation_codes WHERE tenant_id=? ORDER BY created_at DESC,invitation_id ASC", (_id(tenant_id),)).fetchall()
            result = []
            for row in rows:
                item = self._management_view("organization.invitation.create", self._invitation(row))
                if item["state"] == "active" and _parse(item["expires_at"]) <= _utc(now): item["state"] = "expired"
                result.append(item)
        return tuple(result)

    def membership_state(self, *, tenant_id: str, user_id: str) -> MembershipState | None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT state FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                (_id(tenant_id), _id(user_id)),
            ).fetchone()
        return None if row is None else MembershipState(str(row[0]))

    def list_creation_requests(self, *, applicant_user_id: str | None = None) -> tuple[OrganizationCreationRequest, ...]:
        with self.transaction() as connection:
            if applicant_user_id is None:
                rows = connection.execute("SELECT * FROM organization_creation_requests ORDER BY created_at DESC").fetchall()
            else:
                rows = connection.execute("SELECT * FROM organization_creation_requests WHERE applicant_user_id=? ORDER BY created_at DESC", (_id(applicant_user_id),)).fetchall()
        return tuple(self._creation(row) for row in rows)

    def list_join_requests(self, *, tenant_id: str | None = None, user_id: str | None = None) -> tuple[OrganizationJoinRequest, ...]:
        clauses: list[str] = []
        values: list[str] = []
        if tenant_id is not None:
            clauses.append("tenant_id=?"); values.append(_id(tenant_id))
        if user_id is not None:
            clauses.append("user_id=?"); values.append(_id(user_id))
        query = "SELECT * FROM organization_join_requests"
        if clauses: query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self.transaction() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return tuple(self._join(row) for row in rows)
