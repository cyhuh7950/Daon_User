from __future__ import annotations

import asyncio
import secrets
from dataclasses import replace
from pathlib import Path

import httpx
from daon_user_api.identity import PASSWORD_HASHER
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeSettings, build_dependencies, create_app


def _add_user(dependencies, *, user_id: str, password: str) -> None:
    with dependencies.identity_repository.transaction() as connection:
        dependencies.identity_repository._ensure_tenant(connection, user_id)
        connection.execute(
            "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,"
            "email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                user_id, "local", user_id, user_id, f"{user_id}@example.test",
                PASSWORD_HASHER.hash(password), "2026-07-29T00:00:00+00:00", False, "active",
            ),
        )
        connection.execute(
            "INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",
            (user_id, user_id, "personal_owner"),
        )


async def _admin_cookie(client: httpx.AsyncClient) -> str:
    initial = await client.post(
        "/api/v1/auth/login", json={"login_id": "admin", "password": "admin"}
    )
    assert initial.status_code == 200
    changed = await client.post(
        "/api/v1/auth/password/change",
        headers={
            "X-Daon-Bff-Transport": "internal",
            "X-Daon-Csrf-Origin": "https://app.example.com",
            "X-Daon-Csrf-Referer": "https://app.example.com/password-change",
        },
        json={
            "current_password": "admin",
            "new_password": "a strong replacement password",
        },
    )
    assert changed.status_code == 200
    login = await client.post(
        "/api/v1/auth/login",
        json={"login_id": "admin", "password": "a strong replacement password"},
    )
    assert login.status_code == 200
    return login.cookies[WEB_SESSION_COOKIE]


def test_system_admin_user_api_denies_normal_user_and_protects_admin(tmp_path: Path) -> None:
    asyncio.run(_system_admin_user_api_denies_normal_user_and_protects_admin(tmp_path))


async def _system_admin_user_api_denies_normal_user_and_protects_admin(tmp_path: Path) -> None:
    settings = replace(
        RuntimeSettings.for_test(database_path=tmp_path / "runtime.sqlite3", policy_version="identity-policy-v1"),
        system_admin_user_ids=frozenset({"admin"}),
    )
    dependencies = build_dependencies(settings)
    password = secrets.token_urlsafe(24)
    _add_user(dependencies, user_id="normal-user", password=password)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(dependencies)),
        base_url="https://app.example.com",
    ) as client:
        normal_login = await client.post(
            "/api/v1/auth/login", json={"login_id": "normal-user", "password": password}
        )
        assert normal_login.status_code == 200
        denied = await client.get("/api/v1/admin/users")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "FORBIDDEN"
        denied_patch = await client.patch(
            "/api/v1/admin/users/normal-user/state",
            headers={
                "Origin": "https://app.example.com",
                "X-Daon-Bff-Transport": "internal",
                "Idempotency-Key": "normal-user-denied-0001",
            },
            json={"state": "suspended"},
        )
        assert denied_patch.status_code == 403
        assert denied_patch.json()["error"]["code"] == "FORBIDDEN"
        with dependencies.identity_repository.transaction() as connection:
            assert connection.execute(
                "SELECT state FROM users WHERE user_id='normal-user'"
            ).fetchone()[0] == "active"
            assert connection.execute(
                "SELECT state FROM sessions WHERE user_id='normal-user'"
            ).fetchone()[0] == "active"
            assert connection.execute(
                "SELECT COUNT(*) FROM admin_audit_outbox"
            ).fetchone()[0] == 0
        assert dependencies.audit_store.list(
            tenant_id="normal-user", action="identity.user.state_changed"
        ).items == ()

        admin_cookie = await _admin_cookie(client)
        client.cookies.set(WEB_SESSION_COOKIE, admin_cookie)
        listed = await client.get("/api/v1/admin/users")
        assert listed.status_code == 200
        admin = next(item for item in listed.json()["data"]["users"] if item["user_id"] == "admin")
        assert admin == {
            "user_id": "admin",
            "login_id": "admin",
            "email": None,
            "has_email": False,
            "state": "active",
            "protected": True,
        }
        normal_user = next(
            item for item in listed.json()["data"]["users"]
            if item["user_id"] == "normal-user"
        )
        assert normal_user["email"] == "normal-user@example.test"

        protected = await client.patch(
            "/api/v1/admin/users/admin/state",
            headers={
                "Origin": "https://app.example.com",
                "X-Daon-Bff-Transport": "internal",
                "Idempotency-Key": "protect-admin-0001",
            },
            json={"state": "suspended"},
        )
        assert protected.status_code == 409
        assert protected.json()["error"]["code"] == "PROTECTED_ADMIN_ACCOUNT"
        after = await client.get("/api/v1/admin/users")
        assert next(item for item in after.json()["data"]["users"] if item["user_id"] == "admin")["state"] == "active"

        delete = await client.delete(
            "/api/v1/admin/users/admin",
            headers={"Origin": "https://app.example.com", "X-Daon-Bff-Transport": "internal"},
        )
        assert delete.status_code in {404, 405}
        unchanged = await client.get("/api/v1/admin/users")
        assert next(item for item in unchanged.json()["data"]["users"] if item["user_id"] == "admin")["state"] == "active"
    dependencies.close()


def test_pending_email_patch_is_rejected_without_mutation(tmp_path: Path) -> None:
    asyncio.run(_pending_email_patch_is_rejected_without_mutation(tmp_path))


async def _pending_email_patch_is_rejected_without_mutation(tmp_path: Path) -> None:
    settings = replace(
        RuntimeSettings.for_test(
            database_path=tmp_path / "runtime.sqlite3",
            policy_version="identity-policy-v1",
        ),
        system_admin_user_ids=frozenset({"admin"}),
    )
    dependencies = build_dependencies(settings)
    password = secrets.token_urlsafe(24)
    _add_user(dependencies, user_id="pending-user", password=password)
    app = create_app(dependencies)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example.com"
    ) as admin_client, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example.com"
    ) as target_client:
        target_login = await target_client.post(
            "/api/v1/auth/login", json={"login_id": "pending-user", "password": password}
        )
        assert target_login.status_code == 200
        with dependencies.identity_repository.transaction() as connection:
            connection.execute(
                "UPDATE users SET state='pending_email' WHERE user_id='pending-user'"
            )
        admin_client.cookies.set(WEB_SESSION_COOKIE, await _admin_cookie(admin_client))

        rejected = await admin_client.patch(
            "/api/v1/admin/users/pending-user/state",
            headers={
                "Origin": "https://app.example.com",
                "X-Daon-Bff-Transport": "internal",
                "Idempotency-Key": "pending-user-state-0001",
            },
            json={"state": "active"},
        )

        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "INVALID_USER_STATE"
        with dependencies.identity_repository.transaction() as connection:
            assert connection.execute(
                "SELECT state FROM users WHERE user_id='pending-user'"
            ).fetchone()[0] == "pending_email"
            assert connection.execute(
                "SELECT state FROM sessions WHERE user_id='pending-user'"
            ).fetchone()[0] == "active"
            assert connection.execute(
                "SELECT COUNT(*) FROM admin_audit_outbox "
                "WHERE operation='change_user_state' AND target_id='pending-user'"
            ).fetchone()[0] == 0
        assert dependencies.audit_store.list(
            tenant_id="admin", action="identity.user.state_changed"
        ).items == ()
    dependencies.close()


def test_suspending_user_revokes_target_session_and_is_idempotent(tmp_path: Path) -> None:
    asyncio.run(_suspending_user_revokes_target_session_and_is_idempotent(tmp_path))


async def _suspending_user_revokes_target_session_and_is_idempotent(tmp_path: Path) -> None:
    settings = replace(
        RuntimeSettings.for_test(database_path=tmp_path / "runtime.sqlite3", policy_version="identity-policy-v1"),
        system_admin_user_ids=frozenset({"admin"}),
    )
    dependencies = build_dependencies(settings)
    password = secrets.token_urlsafe(24)
    _add_user(dependencies, user_id="target-user", password=password)
    app = create_app(dependencies)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example.com"
    ) as admin_client, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example.com"
    ) as target_client:
        target_login = await target_client.post(
            "/api/v1/auth/login", json={"login_id": "target-user", "password": password}
        )
        assert target_login.status_code == 200
        admin_client.cookies.set(WEB_SESSION_COOKIE, await _admin_cookie(admin_client))
        headers = {
            "Origin": "https://app.example.com",
            "X-Daon-Bff-Transport": "internal",
            "Idempotency-Key": "suspend-target-0001",
        }
        changed = await admin_client.patch(
            "/api/v1/admin/users/target-user/state", headers=headers, json={"state": "suspended"}
        )
        replayed = await admin_client.patch(
            "/api/v1/admin/users/target-user/state", headers=headers, json={"state": "suspended"}
        )
        assert changed.status_code == 200
        assert changed.json()["data"]["replayed"] is False
        assert replayed.status_code == 200
        assert replayed.json()["data"]["replayed"] is True
        revoked = await target_client.get("/api/v1/session")
        assert revoked.status_code == 401
        blocked_login = await target_client.post(
            "/api/v1/auth/login", json={"login_id": "target-user", "password": password}
        )
        assert blocked_login.status_code == 401
    dependencies.close()
