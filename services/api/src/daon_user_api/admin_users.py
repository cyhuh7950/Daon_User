"""System-administrator user directory and account-state operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping

from .audit import ActorType, AuditDuplicateEventError, AuditEvent, AuditEventDraft, AuditOutcome
from .identity import (
    INITIAL_ADMIN_USER_ID,
    IdentityError,
    IdentityPrincipal,
    SqliteIdentityRepository,
    _checked_text,
    is_protected_initial_admin_record,
    revoke_user_sessions,
)


@dataclass(frozen=True, slots=True)
class AdminUserView:
    user_id: str
    login_id: str | None
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

    def _read_audit(self, event_id: str, tenant_id: str) -> AuditEvent | None:
        reader = getattr(self._audit_store, "read")
        try:
            return reader(event_id, tenant_id=tenant_id)
        except TypeError:
            return reader(event_id)

    def _replay(
        self,
        *,
        principal: IdentityPrincipal,
        event_id: str,
        fingerprint: str,
        user_id: str,
        state: str,
    ) -> AdminUserStateResult | None:
        event = self._read_audit(event_id, principal.tenant_id)
        if event is None:
            return None
        if (
            event.action != "identity.user.state_changed"
            or event.actor_id != principal.user_id
            or event.target_id != user_id
            or event.metadata.get("request_fingerprint") != fingerprint
            or event.after is None
            or event.after.get("state") != state
        ):
            raise IdentityError("IDEMPOTENCY_KEY_REUSED", 409)
        with self._repository.transaction() as connection:
            row = connection.execute(
                "SELECT user_id,login_id,email,state FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            if row is None:
                raise IdentityError("USER_NOT_FOUND", 404)
            return AdminUserStateResult(self._view(row, state=state), replayed=True)

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
        event_id = "audit-admin-user-state-" + hashlib.sha256(
            f"{principal.user_id}|{idempotency_key}".encode("utf-8")
        ).hexdigest()
        replay = self._replay(
            principal=principal,
            event_id=event_id,
            fingerprint=fingerprint,
            user_id=user_id,
            state=state,
        )
        if replay is not None:
            return replay

        now = self._clock()
        try:
            with self._repository.transaction() as connection:
                row = connection.execute(
                    "SELECT user_id,issuer,subject,login_id,email,password_digest,"
                    "password_change_required,state FROM users WHERE user_id=?",
                    (user_id,),
                ).fetchone()
                if row is None:
                    raise IdentityError("USER_NOT_FOUND", 404)
                protected_initial = is_protected_initial_admin_record(row)
                if state == "suspended" and (
                    protected_initial or user_id == principal.user_id
                ):
                    raise IdentityError("PROTECTED_ADMIN_ACCOUNT", 409)
                before_state = self._repository.user_state_for_api(str(row["state"]))
                connection.execute(
                    "UPDATE users SET state=? WHERE user_id=?",
                    (self._repository.user_state_for_storage(state), user_id),
                )
                if before_state != state:
                    revoke_user_sessions(connection, user_id=user_id, updated_at=now)
                self._audit_store.append(AuditEventDraft(
                    event_id=event_id,
                    occurred_at=now,
                    actor_id=principal.user_id,
                    actor_type=ActorType.USER,
                    tenant_id=principal.tenant_id,
                    workspace_id=None,
                    action="identity.user.state_changed",
                    target_type="user",
                    target_id=user_id,
                    outcome=AuditOutcome.SUCCEEDED,
                    trace_id=trace_id,
                    policy_version=policy_version,
                    before={"state": before_state},
                    after={"state": state},
                    metadata={
                        "reason_code": "SYSTEM_ADMIN_STATE_CHANGE",
                        "request_fingerprint": fingerprint,
                    },
                ))
                return AdminUserStateResult(self._view(row, state=state), replayed=False)
        except AuditDuplicateEventError:
            replay = self._replay(
                principal=principal,
                event_id=event_id,
                fingerprint=fingerprint,
                user_id=user_id,
                state=state,
            )
            if replay is not None:
                return replay
            raise IdentityError("AUDIT_WRITE_FAILED", 503) from None
        except IdentityError:
            raise
        except Exception as error:
            raise IdentityError("AUDIT_WRITE_FAILED", 503) from error
