"""S36: 기존 정상 Web 세션 계약의 로컬 ASGI 확인. 정식 WSL/PG 시험이 아니다."""
from __future__ import annotations

import asyncio
import secrets
import tempfile
from pathlib import Path

import httpx

from daon_user_api.identity import PASSWORD_HASHER
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeSettings, build_dependencies, create_app


def _dependencies(directory: str, password: str):
    dependencies = build_dependencies(RuntimeSettings.for_test(
        database_path=Path(directory) / "runtime.sqlite3", policy_version="identity-policy-v1",
    ))
    with dependencies.identity_repository.transaction() as connection:
        dependencies.identity_repository._ensure_tenant(connection, "s36-user")
        connection.execute(
            "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,"
            "email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
            ("s36-user", "local", "s36-user", "s36-user", "s36@example.test",
             PASSWORD_HASHER.hash(password), "2026-10-17T00:00:00+00:00", False, "active"),
        )
        connection.execute(
            "INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",
            ("s36-user", "s36-user", "personal_owner"),
        )
    return dependencies


HEADERS = {
    "X-Daon-Bff-Transport": "internal",
    "X-Daon-Csrf-Origin": "https://app.example.com",
    "X-Daon-Csrf-Referer": "https://app.example.com/settings/account",
}


def test_s36_normal_web_password_change_updates_credential_and_revokes_all_sessions():
    async def run(directory):
        password, replacement = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        dependencies = _dependencies(directory, password)
        try:
            app = create_app(dependencies)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.com") as first, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.com") as second:
                for client in (first, second):
                    assert (await client.post("/api/v1/auth/login", json={"login_id": "s36-user", "password": password})).status_code == 200
                    session = await client.get("/api/v1/session")
                    assert session.status_code == 200
                    assert session.json()["data"]["password_change_required"] is False
                denied = await first.post("/api/v1/auth/password/change", headers=HEADERS, json={"current_password": secrets.token_urlsafe(24), "new_password": replacement})
                assert denied.status_code == 401
                assert (await first.get("/api/v1/session")).status_code == 200
                changed = await first.post("/api/v1/auth/password/change", headers=HEADERS, json={"current_password": password, "new_password": replacement})
                assert changed.status_code == 200
                assert changed.json()["data"] == {"status": "password_changed"}
                assert "Max-Age=0" in changed.headers["set-cookie"]
                for client in (first, second):
                    assert (await client.get("/api/v1/session")).status_code == 401
                assert (await first.post("/api/v1/auth/login", json={"login_id": "s36-user", "password": password})).status_code != 200
                assert (await first.post("/api/v1/auth/login", json={"login_id": "s36-user", "password": replacement})).status_code == 200
        finally:
            dependencies.close()
    with tempfile.TemporaryDirectory(prefix="s36-password-", dir=".superpowers") as directory:
        asyncio.run(run(directory))


def test_s36_self_logout_revokes_only_current_session():
    async def run(directory):
        password = secrets.token_urlsafe(24)
        dependencies = _dependencies(directory, password)
        try:
            app = create_app(dependencies)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.com") as first, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.com") as second:
                for client in (first, second):
                    assert (await client.post("/api/v1/auth/login", json={"login_id": "s36-user", "password": password})).status_code == 200
                result = await first.post("/api/v1/session/logout", headers=HEADERS, json={})
                assert result.status_code == 200
                assert result.json()["data"] == {"status": "logged_out", "replayed": False}
                assert "Max-Age=0" in result.headers["set-cookie"]
                assert (await first.get("/api/v1/session")).status_code == 401
                assert (await second.get("/api/v1/session")).status_code == 200
                assert WEB_SESSION_COOKIE in second.cookies
        finally:
            dependencies.close()
    with tempfile.TemporaryDirectory(prefix="s36-logout-", dir=".superpowers") as directory:
        asyncio.run(run(directory))


def test_s36_profile_routes_are_exact_csrf_guarded_and_persistent():
    async def run(directory):
        dependencies = _dependencies(directory, secrets.token_urlsafe(24))
        try:
            # Use the existing service to establish the real cookie-bound Web credential.
            from daon_user_api.identity import DevicePlatform
            password = secrets.token_urlsafe(24)
            with dependencies.identity_repository.transaction() as connection:
                connection.execute("UPDATE users SET password_digest=? WHERE user_id='s36-user'", (PASSWORD_HASHER.hash(password),))
            app = create_app(dependencies)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.com") as client:
                assert (await client.get("/api/v1/account/profile")).status_code == 401
                assert (await client.post("/api/v1/auth/login", json={"login_id": "s36-user", "password": password})).status_code == 200
                result = await client.get("/api/v1/account/profile")
                assert result.status_code == 200
                assert result.json()["data"] == {"display_name": None, "version": 0}
                body = {"display_name": "  QA 본인  ", "expected_version": 0}
                current_session = (await client.get("/api/v1/session")).json()["data"]["session_id"]
                headers = {**HEADERS, "Idempotency-Key": "s36-profile-route-0001", "X-Daon-Expected-Session-Id": current_session}
                assert (await client.patch("/api/v1/account/profile", json=body)).status_code in {403, 422}
                saved = await client.patch("/api/v1/account/profile", headers=headers, json=body)
                assert saved.status_code == 200
                assert saved.json()["data"] == {"display_name": "QA 본인", "version": 1, "replayed": False}
                assert (await client.patch("/api/v1/account/profile", headers=headers, json=body)).json()["data"]["replayed"] is True
                assert (await client.get("/api/v1/account/profile")).json()["data"]["version"] == 1
                for forbidden in ({**body, "user_id": "someone"}, {**body, "email": "different@example.test"}, {**body, "expected_version": True}):
                    assert (await client.patch("/api/v1/account/profile", headers=headers, json=forbidden)).status_code == 422
                assert (await client.get("/api/v1/account/profile?actor_id=someone")).status_code == 400
                assert (await client.get("/api/v1/account/devices?tenant_id=someone")).status_code == 400
                for kind in ("devices", "sessions"):
                    response = await client.get(f"/api/v1/account/{kind}")
                    assert response.status_code == 200
                    assert len(response.json()["data"]["items"]) == 1
                    assert response.json()["data"]["items"][0]["is_current"] is True
        finally: dependencies.close()
    with tempfile.TemporaryDirectory(prefix="s36-profile-route-", dir=".superpowers") as directory:
        asyncio.run(run(directory))


def test_s36_existing_sensitive_routes_use_real_step_up_and_safe_receipts():
    async def run(directory):
        password = secrets.token_urlsafe(24)
        dependencies = _dependencies(directory, password)
        try:
            identity = dependencies.identity_service
            native = identity.local_native_login(login_id="s36-user", password=password, trace_id="trace-s36-native", policy_version="identity-policy-v1")
            app = create_app(dependencies)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.com") as client:
                assert (await client.post("/api/v1/auth/login", json={"login_id": "s36-user", "password": password})).status_code == 200
                current = (await client.get("/api/v1/session")).json()["data"]
                trust_headers = {**HEADERS, "Idempotency-Key": "s36-route-trust-01"}
                trust = await client.post(f"/api/v1/devices/{current['device_id']}/trust", headers=trust_headers, json={})
                assert trust.status_code == 200
                assert trust.json()["data"]["id"] == current["device_id"]
                assert trust.json()["data"]["attributes"] == {"state": "trusted", "replayed": False}
                for kind, target in (("device", native.device_id), ("session", current["session_id"])):
                    step_up = await client.post("/api/v1/session/step-up", headers={**HEADERS, "Idempotency-Key": f"s36-step-up-{kind}-01"}, json={
                        "password": password, "action_group": "device_session_or_sync_key_revoke", "target_id": target})
                    assert step_up.status_code == 201
                    body = {"step_up_authorization": step_up.json()["data"]["step_up_authorization"]}
                    if kind == "session": body["session_id"] = target
                    path = f"/api/v1/devices/{target}/revoke" if kind == "device" else "/api/v1/session/revoke"
                    assert (await client.post(path, json=body)).status_code in {400, 403}
                    result = await client.post(path, headers={**HEADERS, "Idempotency-Key": f"s36-route-revoke-{kind}-01"}, json=body)
                    assert result.status_code == 200
                    assert result.json()["data"]["state"] == "revoked"
                    assert result.json()["meta"]["replayed"] is False
                    if kind == "device":
                        assert result.json()["data"]["sync_key_revoke_required"] is True
                        assert (await client.get("/api/v1/session")).status_code == 200
                    else:
                        assert "Max-Age=0" in result.headers["set-cookie"]
                assert (await client.get("/api/v1/session")).status_code == 401
        finally: dependencies.close()
    with tempfile.TemporaryDirectory(prefix="s36-sensitive-route-", dir=".superpowers") as directory:
        asyncio.run(run(directory))
