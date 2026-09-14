from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import httpx

from daon_user_api.identity import IdentityError, PASSWORD_HASHER, SqliteIdentityRepository
from daon_user_api.runtime import (
    WEB_SESSION_COOKIE,
    RuntimeSettings,
    build_dependencies,
    create_app,
)


class IdentityAdminRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_rejects_noncanonical_admin_before_workspace_owner_grant(self) -> None:
        directory = tempfile.TemporaryDirectory()
        database_path = Path(directory.name) / "runtime.sqlite3"
        repository = SqliteIdentityRepository(database_path)
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,"
                "password_change_required,state) VALUES (?,?,?,?,?,?,?,?)",
                (
                    "admin", "oidc", "admin", "admin", None,
                    PASSWORD_HASHER.hash("a preexisting strong password"), 0, "active",
                ),
            )
        repository.close()
        settings = replace(
            RuntimeSettings.for_test(
                database_path=database_path,
                policy_version="identity-policy-v1",
            ),
            system_admin_user_ids=frozenset({"admin"}),
        )
        self.addCleanup(directory.cleanup)

        with self.assertRaises(IdentityError) as denied:
            build_dependencies(settings)

        self.assertEqual(denied.exception.code, "INITIAL_ADMIN_CONFLICT")
        connection = sqlite3.connect(database_path)
        try:
            owner = connection.execute(
                "SELECT 1 FROM auth_memberships WHERE user_id=?", ("admin",)
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNone(owner)

    async def test_admin_restricted_session_can_only_read_session_change_password_or_logout(self) -> None:
        directory = tempfile.TemporaryDirectory()
        settings = replace(
            RuntimeSettings.for_test(
                database_path=Path(directory.name) / "runtime.sqlite3",
                policy_version="identity-policy-v1",
            ),
            system_admin_user_ids=frozenset({"admin"}),
        )
        dependencies = build_dependencies(settings)
        admin_workspace_id = dependencies.authorization_repository.primary_workspace_id("admin")
        self.assertIsNotNone(admin_workspace_id)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)),
            base_url="https://app.example.com",
        )
        self.addAsyncCleanup(client.aclose)
        self.addCleanup(dependencies.close)
        self.addCleanup(directory.cleanup)
        login = await client.post(
            "/api/v1/auth/login",
            json={"login_id": "admin", "password": "admin"},
        )
        assert login.status_code == 200
        session_cookie = login.cookies[WEB_SESSION_COOKIE]
        assert session_cookie

        session = await client.get("/api/v1/session")
        assert session.status_code == 200
        assert session.json()["data"]["password_change_required"] is True
        assert session.json()["data"]["is_system_admin"] is True

        blocked = await client.get("/api/v1/screen-preferences")
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

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
        assert changed.json()["data"] == {"status": "password_changed"}
        assert f"{WEB_SESSION_COOKIE}=" in changed.headers["set-cookie"]
        assert "Max-Age=0" in changed.headers["set-cookie"]

        revoked = await client.get("/api/v1/session")
        assert revoked.status_code == 401
        old_login = await client.post(
            "/api/v1/auth/login",
            json={"login_id": "admin", "password": "admin"},
        )
        assert old_login.status_code == 400
        new_login = await client.post(
            "/api/v1/auth/login",
            json={
                "login_id": "admin",
                "password": "a strong replacement password",
            },
        )
        assert new_login.status_code == 200
