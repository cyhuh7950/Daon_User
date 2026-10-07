"""System-admin-only changes to existing, active authorization memberships."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Any, Callable

from .audit import ActorType, AuditDuplicateEventError, AuditEventDraft, AuditEventStore, AuditOutcome
from .authorization import (
    AuthorizationError, Role, SqliteAuthorizationRepository, WORKSPACE_ROLES, _checked_id,
)
from .identity import IdentityPrincipal, INITIAL_ADMIN_USER_ID


REASON_CODES = frozenset({
    "ROLE_DUTY_CHANGE", "ACCESS_REVIEW", "SECURITY_RESTRICTION", "CORRECTION", "OTHER",
})


def _input_id(value: str) -> str:
    try:
        return _checked_id(value)
    except AuthorizationError as error:
        raise AuthorizationError("INVALID_INPUT", 422) from error


@dataclass(frozen=True, slots=True)
class TenantMembershipView:
    tenant_id: str
    role: Role
    state: str
    version: int


@dataclass(frozen=True, slots=True)
class WorkspaceMembershipView:
    workspace_id: str
    role: Role
    state: str
    version: int


@dataclass(frozen=True, slots=True)
class EffectiveMemberships:
    tenant: TenantMembershipView | None
    workspaces: tuple[WorkspaceMembershipView, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceRoleChange:
    tenant_id: str
    workspace_id: str
    user_id: str
    role: Role
    state: str
    version: int
    acl_version: int
    replayed: bool


class AdminMembershipRoleService:
    def __init__(
        self, *, repository: SqliteAuthorizationRepository,
        system_admin_user_ids: frozenset[str], clock: Callable[[], datetime],
        audit_store: AuditEventStore | None = None,
    ) -> None:
        self._repository = repository
        self._system_admin_user_ids = system_admin_user_ids
        self._clock = clock
        self._audit_store = audit_store
        self._audit_cursor: tuple[datetime | str, str] | None = None
        self._audit_high_watermark: tuple[datetime | str, str] | None = None

    def _actor(self, actor: IdentityPrincipal) -> str:
        if not isinstance(actor, IdentityPrincipal):
            raise AuthorizationError("AUTHENTICATION_REQUIRED", 401)
        actor_id = _checked_id(actor.user_id)
        if actor_id not in self._system_admin_user_ids:
            raise AuthorizationError("ACTION_DENIED", 403)
        return actor_id

    def _require_target_user(self, connection: Any, user_id: str) -> None:
        from .postgres_adapters import PostgresAuthorizationRepository

        if isinstance(self._repository, PostgresAuthorizationRepository):
            statement = "SELECT 1 FROM identity_users WHERE user_id=? FOR KEY SHARE"
        else:
            statement = "SELECT 1 FROM users WHERE user_id=?"
        if connection.execute(statement, (user_id,)).fetchone() is None:
            raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)

    def list_tenants(self, actor: IdentityPrincipal) -> tuple[tuple[str, str], ...]:
        self._actor(actor)
        from .postgres_adapters import PostgresAuthorizationRepository

        with self._repository.transaction() as connection:
            rows = connection.execute(
                "SELECT tenant_id FROM auth_workspaces "
                "UNION SELECT tenant_id FROM auth_tenant_roles WHERE state='active' "
                "ORDER BY tenant_id"
            ).fetchall()
            if isinstance(self._repository, PostgresAuthorizationRepository):
                result = []
                for row in rows:
                    tenant_id = str(row["tenant_id"])
                    # Foundation tenants has forced tenant RLS; scope each trusted auth tenant separately.
                    connection.execute("SELECT set_config('app.tenant_id', ?, true)", (tenant_id,))
                    name_row = connection.execute(
                        "SELECT display_name FROM tenants WHERE tenant_id=?", (tenant_id,)
                    ).fetchone()
                    result.append((tenant_id, str(name_row["display_name"]) if name_row else tenant_id))
                return tuple(result)
            return tuple((str(row["tenant_id"]), str(row["tenant_id"])) for row in rows)

    def list_effective_memberships(
        self, actor: IdentityPrincipal, tenant_id: str, user_id: str,
    ) -> EffectiveMemberships:
        self._actor(actor)
        tenant_id, user_id = _input_id(tenant_id), _input_id(user_id)
        with self._repository.transaction() as connection:
            self._require_target_user(connection, user_id)
            tenant_row = connection.execute(
                "SELECT role,state,version FROM auth_tenant_roles "
                "WHERE tenant_id=? AND user_id=? AND state='active'",
                (tenant_id, user_id),
            ).fetchone()
            rows = connection.execute(
                "SELECT m.workspace_id,m.role,m.state,m.version FROM auth_memberships m "
                "JOIN auth_workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=m.workspace_id "
                "WHERE m.tenant_id=? AND m.user_id=? AND m.state='active' ORDER BY m.workspace_id",
                (tenant_id, user_id),
            ).fetchall()
            if tenant_row is None and not rows:
                raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
            tenant = None if tenant_row is None else TenantMembershipView(
                tenant_id, Role(str(tenant_row["role"])), "active", int(tenant_row["version"])
            )
            workspaces = tuple(
                WorkspaceMembershipView(str(row["workspace_id"]), Role(str(row["role"])),
                                        "active", int(row["version"]))
                for row in rows if str(row["role"]) in {role.value for role in WORKSPACE_ROLES}
            )
            return EffectiveMemberships(tenant, workspaces)

    def change_existing_workspace_role(
        self, actor: IdentityPrincipal, tenant_id: str, workspace_id: str,
        user_id: str, role: Role, expected_version: int,
        idempotency_key: str, reason: str,
    ) -> WorkspaceRoleChange:
        return self._change_existing_workspace_role(
            actor, tenant_id, workspace_id, user_id, role, expected_version,
            idempotency_key, reason,
        )

    def _dispatch_pending_audit(self, *, stop: Event | None = None) -> None:
        if self._audit_store is None:
            return
        transaction = getattr(self._repository, "_admin_audit_transaction", self._repository.transaction)
        try:
            with transaction() as connection:
                rows = []
                for _ in range(2):
                    if self._audit_high_watermark is None:
                        last = connection.execute(
                            "SELECT created_at,event_id FROM auth_admin_role_operations "
                            "WHERE delivered_at IS NULL ORDER BY created_at DESC,event_id DESC LIMIT 1"
                        ).fetchone()
                        if last is None:
                            self._audit_cursor = None
                            return
                        self._audit_high_watermark = (last["created_at"], str(last["event_id"]))
                    if self._audit_cursor is None:
                        rows = connection.execute(
                            "SELECT * FROM auth_admin_role_operations WHERE delivered_at IS NULL "
                            "AND (created_at,event_id) <= (?,?) "
                            "ORDER BY created_at,event_id LIMIT 32",
                            self._audit_high_watermark,
                        ).fetchall()
                    else:
                        rows = connection.execute(
                            "SELECT * FROM auth_admin_role_operations WHERE delivered_at IS NULL "
                            "AND (created_at,event_id) > (?,?) AND (created_at,event_id) <= (?,?) "
                            "ORDER BY created_at,event_id LIMIT 32",
                            (*self._audit_cursor, *self._audit_high_watermark),
                        ).fetchall()
                    if rows:
                        self._audit_cursor = (rows[-1]["created_at"], str(rows[-1]["event_id"]))
                        break
                    # Finish this finite sweep before taking a fresh upper bound.
                    self._audit_high_watermark = None
                    if self._audit_cursor is not None:
                        self._audit_cursor = None
        except AuthorizationError:
            return
        for row in rows:
            if stop is not None and stop.is_set():
                return
            try:
                draft = AuditEventDraft(
                    event_id=str(row["event_id"]),
                    occurred_at=datetime.fromisoformat(str(row["created_at"])).astimezone(timezone.utc),
                    actor_id=str(row["actor_id"]), actor_type=ActorType.USER,
                    tenant_id=str(row["tenant_id"]), workspace_id=str(row["workspace_id"]),
                    action=("authorization.membership.changed" if row["outcome"] == "changed"
                            else "authorization.membership.unchanged"),
                    target_type="membership",
                    target_id=str(row["user_id"]), outcome=AuditOutcome.SUCCEEDED,
                    # The operation row has no request trace or policy snapshot.
                    trace_id="not-recorded", policy_version="not-recorded",
                    before={"role": str(row["old_role"]), "version": int(row["old_version"])},
                    after={"role": str(row["new_role"]), "version": int(row["new_version"])},
                    metadata={
                        "reason_code": str(row["reason"]), "outcome": str(row["outcome"]),
                        "acl_version": int(row["acl_version"]),
                    },
                )
                append = getattr(self._audit_store, "_append_admin_role", self._audit_store.append)
                append(draft)
            except AuditDuplicateEventError:
                pass
            except Exception:
                continue
            if stop is not None and stop.is_set():
                return
            try:
                with transaction() as connection:
                    connection.execute(
                        "UPDATE auth_admin_role_operations SET delivered_at=? "
                        "WHERE event_id=? AND delivered_at IS NULL",
                        (self._clock().astimezone(timezone.utc).isoformat(), str(row["event_id"])),
                    )
            except (AuthorizationError, ValueError, TypeError):
                continue

    def _change_existing_workspace_role(
        self, actor: IdentityPrincipal, tenant_id: str, workspace_id: str,
        user_id: str, role: Role, expected_version: int,
        idempotency_key: str, reason: str,
    ) -> WorkspaceRoleChange:
        actor_id = self._actor(actor)
        tenant_id, workspace_id, user_id = (
            _input_id(tenant_id), _input_id(workspace_id), _input_id(user_id)
        )
        if not isinstance(role, Role) or role not in WORKSPACE_ROLES:
            raise AuthorizationError("INVALID_ROLE_SCOPE", 422)
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise AuthorizationError("INVALID_INPUT", 422)
        if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128:
            raise AuthorizationError("INVALID_INPUT", 422)
        _input_id(idempotency_key)
        if not isinstance(reason, str) or reason not in REASON_CODES:
            raise AuthorizationError("INVALID_INPUT", 422)
        request = [actor_id, tenant_id, workspace_id, user_id, role.value, expected_version, reason]
        fingerprint = hashlib.sha256(json.dumps(request, separators=(",", ":")).encode()).hexdigest()
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503)
        timestamp = now.astimezone(timezone.utc).isoformat()
        event_id = "auth-admin-role-" + hashlib.sha256(
            f"{actor_id}|{idempotency_key}".encode()
        ).hexdigest()
        with self._repository.transaction() as connection:
            workspace = self._repository.lock_admin_workspace(connection, tenant_id, workspace_id)
            if workspace is None:
                raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
            self._require_target_user(connection, user_id)
            prior = connection.execute(
                "SELECT * FROM auth_admin_role_operations WHERE actor_id=? AND idempotency_key=?",
                (actor_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if str(prior["request_fingerprint"]) != fingerprint:
                    raise AuthorizationError("IDEMPOTENCY_KEY_REUSED", 409)
                return WorkspaceRoleChange(
                    tenant_id, workspace_id, user_id, Role(str(prior["new_role"])),
                    "active", int(prior["new_version"]), int(prior["acl_version"]), True,
                )
            current = connection.execute(
                "SELECT role,state,version FROM auth_memberships "
                "WHERE tenant_id=? AND workspace_id=? AND user_id=? AND state='active'",
                (tenant_id, workspace_id, user_id),
            ).fetchone()
            if current is None or str(current["role"]) not in {item.value for item in WORKSPACE_ROLES}:
                raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
            if user_id == actor_id or user_id == INITIAL_ADMIN_USER_ID or user_id in self._system_admin_user_ids:
                raise AuthorizationError("ACTION_DENIED", 403)
            old_role = Role(str(current["role"]))
            old_version = int(current["version"])
            if old_version != expected_version:
                raise AuthorizationError("VERSION_CONFLICT", 412)
            if old_role is Role.WORKSPACE_ADMIN and role is not Role.WORKSPACE_ADMIN:
                other = connection.execute(
                    "SELECT 1 FROM auth_memberships WHERE tenant_id=? AND workspace_id=? "
                    "AND user_id<>? AND role='workspace_admin' AND state='active' LIMIT 1",
                    (tenant_id, workspace_id, user_id),
                ).fetchone()
                if other is None:
                    raise AuthorizationError("ACTION_DENIED", 403)
            changed = old_role is not role
            next_version = old_version + 1 if changed else old_version
            acl_version = int(workspace["acl_version"]) + (1 if changed else 0)
            if changed:
                cursor = connection.execute(
                    "UPDATE auth_memberships SET role=?,version=?,updated_at=? "
                    "WHERE tenant_id=? AND workspace_id=? AND user_id=? AND state='active' AND version=?",
                    (role.value, next_version, timestamp, tenant_id, workspace_id, user_id, old_version),
                )
                if cursor.rowcount != 1:
                    raise AuthorizationError("VERSION_CONFLICT", 412)
                cursor = connection.execute(
                    "UPDATE auth_workspaces SET acl_version=acl_version+1,updated_at=? "
                    "WHERE tenant_id=? AND workspace_id=? AND acl_version=?",
                    (timestamp, tenant_id, workspace_id, int(workspace["acl_version"])),
                )
                if cursor.rowcount != 1:
                    raise AuthorizationError("VERSION_CONFLICT", 412)
            try:
                connection.execute(
                    "INSERT INTO auth_admin_role_operations("
                    "actor_id,idempotency_key,request_fingerprint,event_id,tenant_id,workspace_id,user_id,"
                    "old_role,new_role,old_version,new_version,acl_version,reason,outcome,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (actor_id, idempotency_key, fingerprint, event_id, tenant_id, workspace_id,
                     user_id, old_role.value, role.value, old_version, next_version,
                     acl_version, reason, "changed" if changed else "unchanged", timestamp),
                )
            except sqlite3.Error as error:
                raise AuthorizationError("AUDIT_WRITE_FAILED", 503) from error
            return WorkspaceRoleChange(
                tenant_id, workspace_id, user_id, role, "active", next_version,
                acl_version, False,
            )
