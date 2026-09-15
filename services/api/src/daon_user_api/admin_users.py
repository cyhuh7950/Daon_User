"""System-administrator user directory and account-state operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping

from .audit import ActorType
from .identity import (
    INITIAL_ADMIN_USER_ID,
    IdentityError,
    IdentityPrincipal,
    SqliteIdentityRepository,
    _checked_text,
    _iso,
    dispatch_admin_audit_outbox,
    is_protected_initial_admin_record,
    revoke_user_sessions,
)


@dataclass(frozen=True, slots=True)
class AdminUserView:
    user_id: str
    login_id: str | None
    email: str | None
    has_email: bool
    state: str
    protected: bool


@dataclass(frozen=True, slots=True)
class AdminUserStateResult:
    user: AdminUserView
    replayed: bool


class AdminUserService:
    """Apply the system-admin allowlist and protected-account invariant."""

    def __init__(
        self,
        *,
        repository: SqliteIdentityRepository,
        audit_store: object,
        system_admin_user_ids: frozenset[str],
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._audit_store = audit_store
        self._system_admin_user_ids = system_admin_user_ids
        self._clock = clock

    def _require_admin(self, principal: IdentityPrincipal) -> None:
        if principal.user_id not in self._system_admin_user_ids:
            raise IdentityError("FORBIDDEN", 403)

    def _view(self, row: Mapping[str, object], *, state: str | None = None) -> AdminUserView:
        user_id = str(row["user_id"])
        return AdminUserView(
            user_id=user_id,
            login_id=None if row["login_id"] is None else str(row["login_id"]),
            email=None if row["email"] is None else str(row["email"]),
            has_email=row["email"] is not None,
            state=(
                self._repository.user_state_for_api(str(row["state"]))
                if state is None
                else state
            ),
            protected=user_id == INITIAL_ADMIN_USER_ID,
        )

    def list_users(self, principal: IdentityPrincipal) -> tuple[AdminUserView, ...]:
        self._require_admin(principal)
        return tuple(self._view(row) for row in self._repository.list_directory_users())

    def change_state(
        self,
        principal: IdentityPrincipal,
        *,
        user_id: str,
        state: str,
        idempotency_key: str,
        trace_id: str,
        policy_version: str,
    ) -> AdminUserStateResult:
        self._require_admin(principal)
        user_id = _checked_text(user_id)
        idempotency_key = _checked_text(idempotency_key)
        _checked_text(trace_id)
        _checked_text(policy_version)
        if state not in {"active", "suspended"}:
            raise IdentityError("INVALID_USER_STATE", 400)
        if not 16 <= len(idempotency_key) <= 128:
            raise IdentityError("IDEMPOTENCY_KEY_INVALID", 400)
        fingerprint = hashlib.sha256(
            f"admin-user-state-v1|{principal.user_id}|{user_id}|{state}".encode("utf-8")
        ).hexdigest()
        idempotency_scope = f"{principal.tenant_id}|{principal.user_id}|{idempotency_key}"
        event_id = "audit-admin-user-state-" + hashlib.sha256(
            idempotency_scope.encode("utf-8")
        ).hexdigest()
        now = self._clock()
        dispatch_admin_audit_outbox(self._repository, self._audit_store, self._clock)
        with self._repository.transaction() as connection:
            row = connection.execute(
                "SELECT user_id,issuer,subject,login_id,email,password_digest,"
                "password_change_required,state FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise IdentityError("USER_NOT_FOUND", 404)
            if user_id == INITIAL_ADMIN_USER_ID:
                if not is_protected_initial_admin_record(row):
                    raise IdentityError("INITIAL_ADMIN_CONFLICT", 503)
                if state == "suspended":
                    raise IdentityError("PROTECTED_ADMIN_ACCOUNT", 409)
            if self._repository.user_state_for_api(str(row["state"])) == "pending_email":
                raise IdentityError("INVALID_USER_STATE", 409)

            def replay_from(prior: Mapping[str, object]) -> AdminUserStateResult:
                if (
                    prior["request_fingerprint"] != fingerprint
                    or prior["target_id"] != user_id
                    or prior["after_state"] != state
                ):
                    raise IdentityError("IDEMPOTENCY_KEY_REUSED", 409)
                return AdminUserStateResult(self._view(row, state=state), replayed=True)

            prior = connection.execute(
                "SELECT request_fingerprint,target_id,after_state FROM admin_audit_outbox "
                "WHERE operation=? AND idempotency_scope=?",
                ("change_user_state", idempotency_scope),
            ).fetchone()
            if prior is not None:
                return replay_from(prior)

            if state == "suspended" and user_id == principal.user_id:
                raise IdentityError("PROTECTED_ADMIN_ACCOUNT", 409)
            before_state = self._repository.user_state_for_api(str(row["state"]))
            reservation = connection.execute(
                "INSERT INTO admin_audit_outbox(event_id,operation,idempotency_scope,"
                "request_fingerprint,actor_id,actor_type,tenant_id,action,target_type,"
                "target_id,occurred_at,trace_id,policy_version,before_state,after_state,"
                "metadata_json,delivered_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(operation,idempotency_scope) DO NOTHING RETURNING event_id",
                (
                    event_id, "change_user_state", idempotency_scope, fingerprint,
                    principal.user_id, ActorType.USER.value, principal.tenant_id,
                    "identity.user.state_changed", "user", user_id, _iso(now), trace_id,
                    policy_version, before_state, state,
                    json.dumps({
                        "reason_code": "SYSTEM_ADMIN_STATE_CHANGE",
                        "request_fingerprint": fingerprint,
                    }, sort_keys=True),
                    None, _iso(now),
                ),
            ).fetchone()
            if reservation is None:
                winner = connection.execute(
                    "SELECT request_fingerprint,target_id,after_state FROM admin_audit_outbox "
                    "WHERE operation=? AND idempotency_scope=?",
                    ("change_user_state", idempotency_scope),
                ).fetchone()
                if winner is None:
                    raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
                return replay_from(winner)
            connection.execute(
                "UPDATE users SET state=? WHERE user_id=?",
                (self._repository.user_state_for_storage(state), user_id),
            )
            if before_state != state:
                revoke_user_sessions(connection, user_id=user_id, updated_at=now)
            result = AdminUserStateResult(self._view(row, state=state), replayed=False)
        dispatch_admin_audit_outbox(
            self._repository, self._audit_store, self._clock, event_id=event_id,
        )
        return result
