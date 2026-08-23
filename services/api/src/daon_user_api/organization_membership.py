"""Organization membership request and invitation domain contracts.

This module intentionally stops at the persistence/domain boundary.  HTTP
handlers and the existing identity/authorization services are wired in a
later work item, so existing login and workspace behaviour is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator


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


@dataclass(frozen=True, slots=True)
class TenantMembership:
    tenant_id: str
    user_id: str
    role: str
    state: MembershipState
    version: int
    updated_at: datetime


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
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OrganizationWorkflowError("INVALID_TIME")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


class SqliteOrganizationRepository:
    """Transactional, restart-safe repository used by unit and local tests."""

    def __init__(self, path: str | Path, *, audit_sink: Callable[[OrganizationAuditRecord], None] | None = None) -> None:
        self._path = Path(path)
        self._lock = RLock()
        self._audit_sink = audit_sink
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
            if error.code == "PERSISTENCE_CONFLICT":
                return
            raise

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
            """
        )

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
            finally:
                connection.close()

    @staticmethod
    def _creation(row: sqlite3.Row) -> OrganizationCreationRequest:
        return OrganizationCreationRequest(str(row["request_id"]), str(row["applicant_user_id"]), str(row["requested_org_name"]), str(row["requested_org_identifier"]), RequestState(row["state"]), row["decision_reason"], row["decided_by"], int(row["version"]), _parse(row["created_at"]), _parse(row["updated_at"]))

    @staticmethod
    def _join(row: sqlite3.Row) -> OrganizationJoinRequest:
        return OrganizationJoinRequest(str(row["request_id"]), str(row["tenant_id"]), str(row["user_id"]), row["invitation_id"], RequestState(row["state"]), row["decision_reason"], row["decided_by"], int(row["version"]), _parse(row["created_at"]), _parse(row["updated_at"]))

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

    def create_invitation(self, *, tenant_id: str, created_by: str, code: str, expires_at: datetime, max_uses: int, now: datetime) -> InvitationCode:
        tenant_id = _id(tenant_id); created_by = _id(created_by); code = _text(code, "INVALID_INVITATION_CODE")
        expires = _utc(expires_at); timestamp = _iso(now)
        if expires <= _utc(now) or not isinstance(max_uses, int) or max_uses < 1: raise OrganizationWorkflowError("INVALID_INVITATION")
        invitation_id = _new_id("invite"); digest = _digest(code)
        with self.transaction() as connection:
            connection.execute("INSERT INTO invitation_codes VALUES (?,?,?,?,?,?,?,?,?,?,?)", (invitation_id, tenant_id, created_by, digest, _iso(expires), max_uses, 0, InvitationState.ACTIVE.value, 1, timestamp, timestamp))
            row = connection.execute("SELECT * FROM invitation_codes WHERE invitation_id=?", (invitation_id,)).fetchone()
        return InvitationCode(str(row["invitation_id"]), str(row["tenant_id"]), str(row["created_by"]), str(row["code_digest"]), _parse(row["expires_at"]), int(row["max_uses"]), int(row["used_count"]), InvitationState(row["state"]), int(row["version"]), _parse(row["created_at"]), _parse(row["updated_at"]))

    def revoke_invitation(self, *, tenant_id: str, invitation_id: str, actor_id: str, expected_version: int, now: datetime) -> InvitationCode:
        tenant_id = _id(tenant_id); invitation_id = _id(invitation_id); actor_id = _id(actor_id); timestamp = _iso(now)
        if not isinstance(expected_version, int) or expected_version < 1: raise OrganizationWorkflowError("INVALID_VERSION")
        with self.transaction() as connection:
            result = connection.execute("UPDATE invitation_codes SET state='revoked',version=version+1,updated_at=? WHERE invitation_id=? AND tenant_id=? AND state='active' AND version=?", (timestamp, invitation_id, tenant_id, expected_version))
            if result.rowcount == 0: raise OrganizationWorkflowError("INVITATION_CONFLICT", 409)
            row = connection.execute("SELECT * FROM invitation_codes WHERE invitation_id=?", (invitation_id,)).fetchone()
            self._emit(OrganizationAuditRecord("organization.invitation.revoked", actor_id, tenant_id, invitation_id, InvitationState.ACTIVE.value, InvitationState.REVOKED.value, None, _utc(now)))
        return InvitationCode(str(row["invitation_id"]), str(row["tenant_id"]), str(row["created_by"]), str(row["code_digest"]), _parse(row["expires_at"]), int(row["max_uses"]), int(row["used_count"]), InvitationState(row["state"]), int(row["version"]), _parse(row["created_at"]), _parse(row["updated_at"]))

    def create_join_request(self, *, tenant_id: str, user_id: str, now: datetime, invitation_code: str | None = None) -> OrganizationJoinRequest:
        tenant_id = _id(tenant_id); user_id = _id(user_id); timestamp = _iso(now); request_id = _new_id("joinreq")
        with self.transaction() as connection:
            invitation_id = None
            if invitation_code is not None:
                invitation = connection.execute("SELECT * FROM invitation_codes WHERE code_digest=?", (_digest(_text(invitation_code, "INVALID_INVITATION_CODE")),)).fetchone()
                if invitation is None or str(invitation["tenant_id"]) != tenant_id or invitation["state"] != InvitationState.ACTIVE.value or _parse(invitation["expires_at"]) <= _utc(now) or int(invitation["used_count"]) >= int(invitation["max_uses"]):
                    raise OrganizationWorkflowError("INVITATION_INVALID", 400)
                invitation_id = str(invitation["invitation_id"])
            connection.execute("INSERT INTO organization_join_requests VALUES (?,?,?,?,?,?,?,?,?,?)", (request_id, tenant_id, user_id, invitation_id, RequestState.PENDING.value, None, None, 1, timestamp, timestamp))
            row = connection.execute("SELECT * FROM organization_join_requests WHERE request_id=?", (request_id,)).fetchone()
        return self._join(row)

    def create_join_request_by_invitation(self, *, user_id: str, invitation_code: str, now: datetime) -> OrganizationJoinRequest:
        """Create a pending join request by resolving an active invitation code.

        The plaintext code is never persisted. Resolving the tenant here keeps
        the user-facing onboarding flow from requiring an internal tenant ID.
        """
        user_id = _id(user_id)
        code = _text(invitation_code, "INVALID_INVITATION_CODE")
        timestamp = _iso(now)
        request_id = _new_id("joinreq")
        with self.transaction() as connection:
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

    def decide_join_request(self, *, request_id: str, actor_id: str, approved: bool, expected_version: int, reason: str | None, role: str, now: datetime) -> OrganizationJoinRequest:
        request_id = _id(request_id); actor_id = _id(actor_id); role = _text(role, "INVALID_ROLE"); timestamp = _iso(now)
        if not isinstance(expected_version, int) or expected_version < 1: raise OrganizationWorkflowError("INVALID_VERSION")
        state = RequestState.APPROVED.value if approved else RequestState.REJECTED.value
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM organization_join_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row["state"] != RequestState.PENDING.value or int(row["version"]) != expected_version: raise OrganizationWorkflowError("REQUEST_CONFLICT", 409)
            if approved and row["invitation_id"] is not None:
                invitation = connection.execute("SELECT * FROM invitation_codes WHERE invitation_id=?", (row["invitation_id"],)).fetchone()
                if invitation is None or invitation["state"] != InvitationState.ACTIVE.value or _parse(invitation["expires_at"]) <= _utc(now) or int(invitation["used_count"]) >= int(invitation["max_uses"]): raise OrganizationWorkflowError("INVITATION_INVALID", 400)
                connection.execute("UPDATE invitation_codes SET used_count=used_count+1,version=version+1,updated_at=? WHERE invitation_id=?", (timestamp, row["invitation_id"]))
            connection.execute("UPDATE organization_join_requests SET state=?,decision_reason=?,decided_by=?,version=version+1,updated_at=? WHERE request_id=? AND version=? AND state='pending'", (state, reason, actor_id, timestamp, request_id, expected_version))
            if approved:
                connection.execute("INSERT INTO tenant_memberships(tenant_id,user_id,role,state,version,updated_at) VALUES (?,?,?,'active',1,?) ON CONFLICT(tenant_id,user_id) DO UPDATE SET role=excluded.role,state='active',version=tenant_memberships.version+1,updated_at=excluded.updated_at", (row["tenant_id"], row["user_id"], role, timestamp))
                connection.execute("INSERT INTO tenant_membership_role_history VALUES (?,?,?,?,?,?,?,?,?,?)", (_new_id("role"), row["tenant_id"], row["user_id"], actor_id, None, role, None, MembershipState.ACTIVE.value, None, timestamp))
            result = connection.execute("SELECT * FROM organization_join_requests WHERE request_id=?", (request_id,)).fetchone()
            self._emit(OrganizationAuditRecord("organization.join_request.decided", actor_id, str(row["tenant_id"]), request_id, RequestState.PENDING.value, state, reason, _utc(now)))
        return self._join(result)

    def set_membership(self, *, tenant_id: str, user_id: str, actor_id: str, state: MembershipState, expected_version: int, reason: str | None, now: datetime) -> TenantMembership:
        tenant_id = _id(tenant_id); user_id = _id(user_id); actor_id = _id(actor_id); timestamp = _iso(now)
        if not isinstance(state, MembershipState) or not isinstance(expected_version, int) or expected_version < 1: raise OrganizationWorkflowError("INVALID_INPUT")
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, user_id)).fetchone()
            if row is None or int(row["version"]) != expected_version: raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
            connection.execute("UPDATE tenant_memberships SET state=?,version=version+1,updated_at=? WHERE tenant_id=? AND user_id=? AND version=?", (state.value, timestamp, tenant_id, user_id, expected_version))
            connection.execute("INSERT INTO tenant_membership_role_history VALUES (?,?,?,?,?,?,?,?,?,?)", (_new_id("role"), tenant_id, user_id, actor_id, row["role"], row["role"], row["state"], state.value, reason, timestamp))
            self._emit(OrganizationAuditRecord("organization.membership.state_changed", actor_id, tenant_id, user_id, str(row["state"]), state.value, reason, _utc(now)))
            result = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, user_id)).fetchone()
        return TenantMembership(tenant_id, user_id, str(result["role"]), MembershipState(result["state"]), int(result["version"]), _parse(result["updated_at"]))

    def change_role(self, *, tenant_id: str, user_id: str, actor_id: str, role: str, expected_version: int, reason: str | None, now: datetime) -> TenantMembership:
        tenant_id = _id(tenant_id); user_id = _id(user_id); actor_id = _id(actor_id); role = _text(role, "INVALID_ROLE"); timestamp = _iso(now)
        if not isinstance(expected_version, int) or expected_version < 1: raise OrganizationWorkflowError("INVALID_VERSION")
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, user_id)).fetchone()
            if row is None or int(row["version"]) != expected_version: raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
            connection.execute("UPDATE tenant_memberships SET role=?,version=version+1,updated_at=? WHERE tenant_id=? AND user_id=? AND version=?", (role, timestamp, tenant_id, user_id, expected_version))
            connection.execute("INSERT INTO tenant_membership_role_history VALUES (?,?,?,?,?,?,?,?,?,?)", (_new_id("role"), tenant_id, user_id, actor_id, row["role"], role, row["state"], row["state"], reason, timestamp))
            self._emit(OrganizationAuditRecord("organization.membership.role_changed", actor_id, tenant_id, user_id, str(row["role"]), role, reason, _utc(now)))
            result = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND user_id=?", (tenant_id, user_id)).fetchone()
        return TenantMembership(tenant_id, user_id, str(result["role"]), MembershipState(result["state"]), int(result["version"]), _parse(result["updated_at"]))

    def list_members(self, tenant_id: str) -> tuple[TenantMembership, ...]:
        tenant_id = _id(tenant_id)
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? ORDER BY user_id", (tenant_id,)).fetchall()
        return tuple(TenantMembership(tenant_id, str(row["user_id"]), str(row["role"]), MembershipState(row["state"]), int(row["version"]), _parse(row["updated_at"])) for row in rows)

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
