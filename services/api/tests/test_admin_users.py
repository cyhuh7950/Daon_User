from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from daon_user_api.admin_users import AdminUserService
from daon_user_api.identity import (
    DevicePlatform,
    IdentityError,
    IdentityPrincipal,
    PASSWORD_HASHER,
)
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service


def _add_local_user(repository, *, user_id: str, password: str) -> None:
    with repository.transaction() as connection:
        repository._ensure_tenant(connection, user_id)
        connection.execute(
            "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,"
            "email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                user_id,
                "local",
                user_id,
                user_id,
                f"{user_id}@example.test",
                PASSWORD_HASHER.hash(password),
                "2026-07-29T00:00:00+00:00",
                False,
                "active",
            ),
        )
        connection.execute(
            "INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",
            (user_id, user_id, "personal_owner"),
        )


def test_reset_initial_password_is_atomic_and_revokes_all_sessions(tmp_path: Path) -> None:
    identity, repository, audit, _clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    first = identity.local_login(
        login_id="admin", password="admin", platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    identity.change_current_password(
        access_token=first.access_token,
        current_password="admin",
        new_password="a strong replacement password",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    active = identity.local_login(
        login_id="admin",
        password="a strong replacement password",
        platform=DevicePlatform.WEB,
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO refresh_families(family_id,session_id,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?)",
            (
                "family-admin-recovery",
                active.session_id,
                "active",
                "2026-07-29T00:00:00+00:00",
                "2026-07-29T00:00:00+00:00",
            ),
        )

    identity.reset_initial_admin_password(
        trace_id="trace-admin-recovery-001", policy_version=POLICY_VERSION,
    )

    with repository.transaction() as connection:
        user = connection.execute(
            "SELECT state,password_change_required FROM users WHERE user_id=?", ("admin",)
        ).fetchone()
        session = connection.execute(
            "SELECT state FROM sessions WHERE session_id=?", (active.session_id,)
        ).fetchone()
        family = connection.execute(
            "SELECT state FROM refresh_families WHERE family_id=?",
            ("family-admin-recovery",),
        ).fetchone()
    assert tuple(user) == ("active", 1)
    assert session["state"] == "revoked"
    assert family["state"] == "revoked"
    with pytest.raises(IdentityError) as revoked:
        identity.validate_access(
            active.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
    assert revoked.value.code == "SESSION_REVOKED"
    recovered = identity.local_login(
        login_id="admin", password="admin", platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    assert recovered.user_id == "admin"
    events = audit.list(
        tenant_id="admin", action="identity.initial_admin_password.reset"
    ).items
    assert len(events) == 1
    assert events[0].metadata == {"reason_code": "SERVER_CONSOLE_RECOVERY"}


def test_reset_initial_password_rolls_back_when_audit_write_fails(tmp_path: Path) -> None:
    class FailingAudit:
        def append(self, _draft: object) -> None:
            raise RuntimeError("audit unavailable")

    identity, repository, _audit, _clock = create_service(
        tmp_path / "identity.sqlite3", audit_store=FailingAudit()
    )
    identity.ensure_initial_admin()
    before = None
    with repository.transaction() as connection:
        before = tuple(connection.execute(
            "SELECT password_digest,password_change_required FROM users WHERE user_id=?",
            ("admin",),
        ).fetchone())

    with pytest.raises(IdentityError) as failed:
        identity.reset_initial_admin_password(
            trace_id="trace-admin-recovery-rollback", policy_version=POLICY_VERSION,
        )

    assert failed.value.code == "AUDIT_WRITE_FAILED"
    with repository.transaction() as connection:
        after = tuple(connection.execute(
            "SELECT password_digest,password_change_required FROM users WHERE user_id=?",
            ("admin",),
        ).fetchone())
    assert after == before


def test_admin_user_state_protection_revocation_and_idempotency(tmp_path: Path) -> None:
    identity, repository, audit, clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    user_password = secrets.token_urlsafe(24)
    _add_local_user(repository, user_id="user-one", password=user_password)
    target = identity.local_login(
        login_id="user-one", password=user_password, platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO refresh_families(family_id,session_id,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?)",
            (
                "family-user-one",
                target.session_id,
                "active",
                "2026-07-29T00:00:00+00:00",
                "2026-07-29T00:00:00+00:00",
            ),
        )
    service = AdminUserService(
        repository=repository,
        audit_store=audit,
        system_admin_user_ids=frozenset({"admin"}),
        clock=clock,
    )
    admin = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")
    normal = IdentityPrincipal("user-one", target.session_id, target.device_id, "user-one")

    with pytest.raises(IdentityError) as forbidden:
        service.list_users(normal)
    assert forbidden.value.code == "FORBIDDEN"

    with pytest.raises(IdentityError) as protected:
        service.change_state(
            admin,
            user_id="admin",
            state="suspended",
            idempotency_key="protect-admin-0001",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
    assert protected.value.code == "PROTECTED_ADMIN_ACCOUNT"
    assert next(user for user in service.list_users(admin) if user.user_id == "admin").state == "active"

    changed = service.change_state(
        admin,
        user_id="user-one",
        state="suspended",
        idempotency_key="suspend-user-one-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    replayed = service.change_state(
        admin,
        user_id="user-one",
        state="suspended",
        idempotency_key="suspend-user-one-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert changed.user.state == "suspended"
    assert changed.replayed is False
    assert replayed.user.state == "suspended"
    assert replayed.replayed is True
    with pytest.raises(IdentityError) as revoked:
        identity.validate_access(
            target.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
    assert revoked.value.code == "SESSION_REVOKED"
    with repository.transaction() as connection:
        family = connection.execute(
            "SELECT state FROM refresh_families WHERE family_id=?", ("family-user-one",)
        ).fetchone()
    assert family["state"] == "revoked"
    events = audit.list(tenant_id="admin", action="identity.user.state_changed").items
    assert len(events) == 1
    assert events[0].before == {"state": "active"}
    assert events[0].after == {"state": "suspended"}

    with pytest.raises(IdentityError) as reused:
        service.change_state(
            admin,
            user_id="user-one",
            state="active",
            idempotency_key="suspend-user-one-0001",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
    assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"

    service.change_state(
        admin,
        user_id="user-one",
        state="active",
        idempotency_key="activate-user-one-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    relogin = identity.local_login(
        login_id="user-one", password=user_password, platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    assert relogin.user_id == "user-one"


def test_admin_cli_uses_fixed_target_and_never_echoes_extra_arguments(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    identity, repository, _audit, _clock = create_service(database_path)
    identity.ensure_initial_admin()
    repository.close()
    environment = os.environ.copy()
    environment.update({
        "DAON_RUNTIME_PROFILE": "development",
        "DAON_API_DATABASE_PATH": str(database_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    })

    completed = subprocess.run(
        [sys.executable, "-m", "daon_user_api.admin_cli", "reset-initial-password"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert completed.returncode == 0
    assert completed.stdout == "INITIAL_ADMIN_PASSWORD_RESET\n"
    assert completed.stderr == ""

    marker = "sensitive-extra-argument"
    rejected = subprocess.run(
        [sys.executable, "-m", "daon_user_api.admin_cli", "reset-initial-password", marker],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert rejected.returncode == 2
    assert marker not in rejected.stdout
    assert marker not in rejected.stderr


def test_admin_cli_fails_closed_when_canonical_admin_is_missing(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    identity, repository, _audit, _clock = create_service(database_path)
    identity.ensure_initial_admin()
    with repository.transaction() as connection:
        connection.execute("DELETE FROM memberships WHERE user_id=?", ("admin",))
        connection.execute("DELETE FROM users WHERE user_id=?", ("admin",))
    repository.close()
    environment = os.environ.copy()
    environment.update({
        "DAON_RUNTIME_PROFILE": "development",
        "DAON_API_DATABASE_PATH": str(database_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    })

    completed = subprocess.run(
        [sys.executable, "-m", "daon_user_api.admin_cli", "reset-initial-password"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "INITIAL_ADMIN_PASSWORD_RESET_FAILED:INITIAL_ADMIN_MISSING\n"
