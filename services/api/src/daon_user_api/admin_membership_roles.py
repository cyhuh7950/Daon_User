"""System-admin-only changes to existing, active authorization memberships."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .authorization import (
    AuthorizationError, Role, SqliteAuthorizationRepository, WORKSPACE_ROLES, _checked_id,
)
from .identity import IdentityPrincipal, INITIAL_ADMIN_USER_ID


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
    ) -> None:
        self._repository = repository
        self._system_admin_user_ids = system_admin_user_ids
        self._clock = clock

    def _actor(self, actor: IdentityPrincipal) -> str:
        if not isinstance(actor, IdentityPrincipal):
            raise AuthorizationError("AUTHENTICATION_REQUIRED", 401)
        actor_id = _checked_id(actor.user_id)
        if actor_id not in self._system_admin_user_ids:
            raise AuthorizationError("ACTION_DENIED", 403)
        return actor_id

    def list_effective_memberships(
        self, actor: IdentityPrincipal, tenant_id: str, user_id: str,
    ) -> EffectiveMemberships:
        self._actor(actor)
        tenant_id, user_id = _input_id(tenant_id), _input_id(user_id)
        with self._repository.transaction() as connection:
            # The tenant is resolved from persisted workspace rows, never from actor claims.
            if connection.execute(
                "SELECT 1 FROM auth_workspaces WHERE tenant_id=? LIMIT 1", (tenant_id,)
            ).fetchone() is None:
                raise AuthorizationError("RESOURCE_UNAVAILABLE", 404)
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
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 256 or any(ord(c) < 32 for c in reason):
            raise AuthorizationError("INVALID_INPUT", 422)
        reason = reason.strip()
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
