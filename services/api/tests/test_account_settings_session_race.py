"""S36 approved profile-only binding against cross-tab cookie/principal drift."""
from __future__ import annotations

import asyncio
import secrets
import tempfile

import httpx

from daon_user_api.identity import PASSWORD_HASHER
from daon_user_api.runtime import create_app
from test_account_settings_contract import HEADERS, _dependencies


def test_profile_patch_rejects_cookie_identity_changed_after_displayed_session():
    async def run(directory):
        password = secrets.token_urlsafe(24)
        dependencies = _dependencies(directory, password)
        try:
            with dependencies.identity_repository.transaction() as connection:
                dependencies.identity_repository._ensure_tenant(connection, "s36-other")
                connection.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("s36-other", "local", "s36-other", "s36-other", "other@example.test", PASSWORD_HASHER.hash(password), "2026-10-17T00:00:00+00:00", False, "active"))
                connection.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)", ("s36-other", "s36-other", "personal_owner"))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)), base_url="https://app.example.com") as client:
                assert (await client.post("/api/v1/auth/login", json={"login_id": "s36-user", "password": password})).status_code == 200
                displayed = (await client.get("/api/v1/session")).json()["data"]
                assert (await client.get("/api/v1/account/profile")).json()["data"]["version"] == 0
                # Another tab replaces the shared cookie after A's session response
                # was captured, but before A's preflight response is delivered.
                assert (await client.post("/api/v1/auth/login", json={"login_id": "s36-other", "password": password})).status_code == 200
                actual = (await client.get("/api/v1/session")).json()["data"]
                assert displayed["user_id"] != actual["user_id"]
                assert (await client.get("/api/v1/account/profile")).json()["data"]["version"] == 0
                before_events = len(dependencies.audit_store.list(tenant_id="s36-other").items)
                response = await client.patch("/api/v1/account/profile", headers={**HEADERS, "Idempotency-Key": "s36-cookie-race-0001", "X-Daon-Expected-Session-Id": displayed["session_id"]},
                    json={"display_name": "stale-display-submission", "expected_version": 0})
                after = (await client.get("/api/v1/account/profile")).json()["data"]
                observed = {"status": response.status_code, "current_profile_version": after["version"]}
                assert observed == {"status": 409, "current_profile_version": 0}, "displayed A must not write cookie B"
                assert response.json()["error"]["code"] == "ACCOUNT_SESSION_CHANGED"
                with dependencies.identity_repository.transaction() as connection:
                    assert connection.execute("SELECT count(*) FROM account_profiles").fetchone()[0] == 0
                    assert connection.execute("SELECT count(*) FROM account_requests").fetchone()[0] == 0
                assert len(dependencies.audit_store.list(tenant_id="s36-other").items) == before_events
                body = {"display_name": "own-current-session", "expected_version": 0}
                headers = {**HEADERS, "Idempotency-Key": "s36-current-profile-01"}
                for invalid in (None, "", "bad/session", "x" * 129, " has-space", "ses-a, ses-b"):
                    malformed = {**headers, **({"X-Daon-Expected-Session-Id": invalid} if invalid is not None else {})}
                    assert (await client.patch("/api/v1/account/profile", headers=malformed, json=body)).status_code == 422
                headers["X-Daon-Expected-Session-Id"] = actual["session_id"]
                assert (await client.patch("/api/v1/account/profile", headers=headers, json=body)).json()["data"]["replayed"] is False
                assert (await client.patch("/api/v1/account/profile", headers=headers, json=body)).json()["data"]["replayed"] is True
                # A stale binding cannot even read/replay B's successful request.
                headers["X-Daon-Expected-Session-Id"] = displayed["session_id"]
                assert (await client.patch("/api/v1/account/profile", headers=headers, json=body)).json()["error"]["code"] == "ACCOUNT_SESSION_CHANGED"
        finally: dependencies.close()
    with tempfile.TemporaryDirectory(prefix="s36-session-race-", dir=".superpowers") as directory:
        asyncio.run(run(directory))
