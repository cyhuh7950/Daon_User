"""Android browser authorization HTTP boundary and cookie separation."""

import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx

from daon_user_api.identity import IdentityError
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeSettings, build_dependencies, create_app


CLIENT = "qa-android"
REDIRECT = "https://auth.qa.example.test/android/callback"
ORIGIN = "https://auth.qa.example.test"
STATE = "synthetic-state-with-enough-entropy-123"
CHALLENGE = "c" * 43
VERIFIER = "v" * 43
VALID_CHALLENGE = base64.urlsafe_b64encode(
    hashlib.sha256(VERIFIER.encode()).digest()
).rstrip(b"=").decode()


def _configured(tmp_path):
    settings = replace(RuntimeSettings.for_test(
        database_path=tmp_path / "android-t2.sqlite", policy_version="android-t2-test",
    ), public_gateway_url=ORIGIN, android_auth_clients={CLIENT: frozenset({REDIRECT})})
    dependencies = build_dependencies(settings)

    class Sender:
        messages = []

        def send(self, **message):
            self.messages.append(message)

    sender = Sender()
    dependencies.identity_service._email_sender = sender
    dependencies.identity_service.signup(
        login_id="android-user", email="android-user@example.test",
        password="correct horse battery staple", trace_id="android-signup",
        policy_version=settings.policy_version,
    )
    token = sender.messages[-1]["body"].split(": ", 1)[1].splitlines()[0]
    dependencies.identity_service.verify_email(
        token=token, trace_id="android-verify", policy_version=settings.policy_version,
    )
    return dependencies


def _start_body(**changes):
    return {
        "client_id": CLIENT, "redirect_uri": REDIRECT, "state": STATE,
        "code_challenge": VALID_CHALLENGE, "code_challenge_method": "S256",
        **changes,
    }


def _exchange_body(transaction_id, code, **changes):
    return {
        "transaction_id": transaction_id, "code": code,
        "code_verifier": VERIFIER, "redirect_uri": REDIRECT, **changes,
    }


def _csrf_headers():
    return {
        "x-daon-bff-transport": "internal", "x-daon-csrf-origin": ORIGIN,
        "x-daon-csrf-referer": f"{ORIGIN}/auth/android/authorize",
    }


def test_android_routes_exist_and_unconfigured_policy_fails_closed(tmp_path):
    dependencies = build_dependencies(RuntimeSettings.for_test(
        database_path=tmp_path / "android-t2.sqlite", policy_version="android-t2-test",
    ))
    try:
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(dependencies)), base_url=ORIGIN,
            ) as client:
                start = await client.post("/api/v1/session/android/transactions", json={
                    "client_id": CLIENT, "redirect_uri": REDIRECT, "state": STATE,
                    "code_challenge": CHALLENGE, "code_challenge_method": "S256",
                })
                authorize = await client.post("/api/v1/session/android/authorize", json={
                    "transaction_id": "and-synthetic",
                })
                exchange = await client.post("/api/v1/session/android/exchange", json={
                    "transaction_id": "and-synthetic", "code": "synthetic-code",
                    "code_verifier": "v" * 43, "redirect_uri": REDIRECT,
                })
                return start, authorize, exchange

        start, authorize, exchange = asyncio.run(run())
        assert start.status_code == 403
        assert authorize.status_code == 401
        assert exchange.status_code == 400
        for response in (start, authorize, exchange):
            assert response.json()["error"]["trace_id"] == response.headers["x-trace-id"]
            assert "set-cookie" not in response.headers
    finally:
        dependencies.close()


def test_android_complete_flow_and_public_channel_separation(tmp_path, caplog):
    dependencies = _configured(tmp_path)
    try:
        async def run():
            app = create_app(dependencies)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as native, \
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as web:
                login = await web.post("/api/v1/auth/login", json={
                    "login_id": "android-user", "password": "correct horse battery staple",
                })
                assert login.status_code == 200
                assert WEB_SESSION_COOKIE in web.cookies
                start = await native.post("/api/v1/session/android/transactions", json=_start_body())
                assert start.status_code == 201, start.json()
                data = start.json()["data"]
                assert set(data) == {"transaction_id", "authorization_url", "expires_at"}
                assert data["authorization_url"].startswith(f"{ORIGIN}/auth/android/authorize?")
                assert "code=" not in data["authorization_url"]
                transaction_id = data["transaction_id"]
                no_cookie = await native.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id,
                }, headers=_csrf_headers())
                no_csrf = await web.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id,
                })
                wrong_origin = await web.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id,
                }, headers={**_csrf_headers(), "x-daon-csrf-origin": "https://other.example.test"})
                forged_tenant = await web.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id, "tenant_id": "forged-tenant",
                }, headers=_csrf_headers())
                bearer = await native.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id,
                }, headers={"authorization": "Bearer synthetic"})
                get = await web.get("/api/v1/session/android/authorize")
                assert [no_cookie.status_code, no_csrf.status_code, wrong_origin.status_code,
                        forged_tenant.status_code, bearer.status_code, get.status_code] == [
                            401, 403, 403, 400, 401, 405,
                        ]
                approved = await web.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id,
                }, headers=_csrf_headers())
                assert approved.status_code == 200, approved.json()
                assert set(approved.json()["data"]) == {"authorization_url"}
                link = approved.json()["data"]["authorization_url"]
                assert urlsplit(link)._replace(query="").geturl() == REDIRECT
                parameters = parse_qs(urlsplit(link).query)
                assert parameters["state"] == [STATE]
                code = parameters["code"][0]
                bad_pkce = await native.post("/api/v1/session/android/exchange", json=_exchange_body(
                    transaction_id, code, code_verifier="x" * 43,
                ))
                bad_redirect = await native.post("/api/v1/session/android/exchange", json=_exchange_body(
                    transaction_id, code, redirect_uri="https://other.example.test/callback",
                ))
                assert bad_pkce.status_code == bad_redirect.status_code == 400
                with_cookie = await web.post("/api/v1/session/android/exchange", json=_exchange_body(
                    transaction_id, code,
                ))
                assert with_cookie.status_code == 400
                forged_platform = await native.post("/api/v1/session/android/exchange", json=_exchange_body(
                    transaction_id, code, platform="web",
                ))
                assert forged_platform.status_code == 400
                first, second = await asyncio.gather(*(
                    native.post("/api/v1/session/android/exchange", json=_exchange_body(transaction_id, code))
                    for _ in range(2)
                ))
                assert sorted((first.status_code, second.status_code)) == [201, 400]
                success = first if first.status_code == 201 else second
                projection = success.json()["data"]
                assert projection["client_kind"] == "native"
                assert projection["tenant_id"] == login.json()["data"]["tenant_id"]
                assert projection["user_id"] == login.json()["data"]["user_id"]
                assert projection["access_credential"] and projection["refresh_credential"]
                for response in (start, approved, success, first, second):
                    assert "set-cookie" not in response.headers
                    assert response.headers["cache-control"] == "no-store"
                assert all(secret not in str(response.json()) for response in
                           (no_cookie, no_csrf, wrong_origin, forged_tenant, bearer, bad_pkce,
                            bad_redirect, with_cookie, forged_platform)
                           for secret in (VERIFIER, code, STATE))
                return transaction_id, projection, code

        transaction_id, projection, code = asyncio.run(run())
        with dependencies.identity_repository.transaction() as connection:
            device = connection.execute("SELECT platform FROM devices WHERE device_id=?", (
                projection["device_id"],
            )).fetchone()
            assert device["platform"] == "android"
            row = connection.execute(
                "SELECT used_at FROM android_auth_transactions WHERE transaction_id=?", (transaction_id,)
            ).fetchone()
            assert row["used_at"] is not None
        audit_projection = str(dependencies.audit_store.list(tenant_id=projection["tenant_id"]))
        for secret in (code, VERIFIER, STATE):
            assert secret not in audit_projection
            assert secret not in caplog.text
    finally:
        dependencies.close()


def test_android_request_contract_rate_limit_and_expiry(tmp_path):
    dependencies = _configured(tmp_path)
    try:
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(dependencies)), base_url=ORIGIN,
            ) as native:
                wrong_client = await native.post("/api/v1/session/android/transactions", json=_start_body(
                    client_id="unregistered",
                ))
                wrong_redirect = await native.post("/api/v1/session/android/transactions", json=_start_body(
                    redirect_uri="https://other.example.test/callback",
                ))
                extra = await native.post("/api/v1/session/android/transactions", json=_start_body(
                    tenant_id="other-tenant", platform="web",
                ))
                wrong_method = await native.post("/api/v1/session/android/transactions", json=_start_body(
                    code_challenge_method="plain",
                ))
                short_state = await native.post("/api/v1/session/android/transactions", json=_start_body(
                    state="short",
                ))
                assert [response.status_code for response in
                        (wrong_client, wrong_redirect, extra, wrong_method, short_state)] == [403, 403, 400, 422, 422]
                start = await native.post("/api/v1/session/android/transactions", json=_start_body())
                assert start.status_code == 201
                transaction_id = start.json()["data"]["transaction_id"]
                with dependencies.identity_repository.transaction() as connection:
                    connection.execute("UPDATE android_auth_transactions SET expires_at=? WHERE transaction_id=?", (
                        (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), transaction_id,
                    ))
                expired = await native.post("/api/v1/session/android/exchange", json=_exchange_body(
                    transaction_id, "synthetic-code",
                ))
                assert expired.status_code == 400
                for _ in range(5):
                    await native.post("/api/v1/session/android/transactions", json=_start_body())
                limited = await native.post("/api/v1/session/android/transactions", json=_start_body())
                assert limited.status_code == 429
                assert limited.json()["error"]["code"] == "RATE_LIMITED"
                cookie_start = await native.post("/api/v1/session/android/transactions", json=_start_body(),
                                                 headers={"cookie": "synthetic_web_cookie=value"})
                bearer_start = await native.post("/api/v1/session/android/transactions", json=_start_body(),
                                                 headers={"authorization": "Bearer synthetic"})
                assert cookie_start.status_code == bearer_start.status_code == 400
                for response in (wrong_client, wrong_redirect, extra, wrong_method, short_state,
                                 expired, limited, cookie_start, bearer_start):
                    assert "set-cookie" not in response.headers
                    assert REDIRECT not in str(response.json())
                    assert STATE not in str(response.json())

        asyncio.run(run())
    finally:
        dependencies.close()


def test_android_authorize_and_code_expiry_reject_without_native_session(tmp_path):
    dependencies = _configured(tmp_path)
    try:
        async def run():
            app = create_app(dependencies)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as native, \
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as web:
                login = await web.post("/api/v1/auth/login", json={
                    "login_id": "android-user", "password": "correct horse battery staple",
                })
                assert login.status_code == 200
                start = await native.post("/api/v1/session/android/transactions", json=_start_body())
                transaction_id = start.json()["data"]["transaction_id"]
                with dependencies.identity_repository.transaction() as connection:
                    connection.execute("UPDATE android_auth_transactions SET expires_at=? WHERE transaction_id=?", (
                        (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), transaction_id,
                    ))
                expired_authorize = await web.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id,
                }, headers=_csrf_headers())
                assert expired_authorize.status_code == 400
                start2 = await native.post("/api/v1/session/android/transactions", json=_start_body())
                transaction_id2 = start2.json()["data"]["transaction_id"]
                approved = await web.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id2,
                }, headers=_csrf_headers())
                assert approved.status_code == 200
                code = parse_qs(urlsplit(approved.json()["data"]["authorization_url"]).query)["code"][0]
                with dependencies.identity_repository.transaction() as connection:
                    connection.execute(
                        "UPDATE android_auth_transactions SET code_expires_at=? WHERE transaction_id=?",
                        ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), transaction_id2),
                    )
                expired_exchange = await native.post("/api/v1/session/android/exchange", json=_exchange_body(
                    transaction_id2, code,
                ))
                assert expired_exchange.status_code == 400
                with dependencies.identity_repository.transaction() as connection:
                    android_sessions = connection.execute(
                        "SELECT COUNT(*) FROM devices WHERE platform='android'"
                    ).fetchone()[0]
                    assert android_sessions == 0
                return expired_authorize, expired_exchange

        for response in asyncio.run(run()):
            assert "set-cookie" not in response.headers
            assert VERIFIER not in str(response.json())
            assert STATE not in str(response.json())
    finally:
        dependencies.close()


def test_android_openapi_contract_has_only_approved_fields():
    document = json.loads((Path(__file__).resolve().parents[3] / "packages/contracts/openapi/v1/openapi.json").read_text(
        encoding="utf-8",
    ))
    paths = document["paths"]
    expected = {
        "transactions": ("AndroidTransactionRequest", 201),
        "authorize": ("AndroidAuthorizeRequest", 200),
        "exchange": ("AndroidExchangeRequest", 201),
    }
    for segment, (schema_name, status) in expected.items():
        operation = paths[f"/api/v1/session/android/{segment}"]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"] == (
            f"#/components/schemas/{schema_name}"
        )
        assert str(status) in operation["responses"]
        assert "parameters" not in operation
        schema = document["components"]["schemas"][schema_name]
        assert schema["additionalProperties"] is False
        assert not ({"tenant_id", "user_id", "platform", "client_kind"} & set(schema["properties"]))


def test_exchange_projection_lookup_failure_rolls_back_code_and_session(tmp_path):
    dependencies = _configured(tmp_path)
    try:
        async def run():
            app = create_app(dependencies)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as native, \
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as web:
                login = await web.post("/api/v1/auth/login", json={
                    "login_id": "android-user", "password": "correct horse battery staple",
                })
                assert login.status_code == 200
                started = await native.post("/api/v1/session/android/transactions", json=_start_body())
                transaction_id = started.json()["data"]["transaction_id"]
                approved = await web.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id,
                }, headers=_csrf_headers())
                code = parse_qs(urlsplit(approved.json()["data"]["authorization_url"]).query)["code"][0]
                original = dependencies.identity_service._selectable_tenant_workspaces
                calls = 0

                def fail_second_lookup(connection, user_id):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise IdentityError("PERSISTENCE_UNAVAILABLE", 503)
                    return original(connection, user_id)

                with patch.object(dependencies.identity_service, "_selectable_tenant_workspaces",
                                  side_effect=fail_second_lookup):
                    failed = await native.post("/api/v1/session/android/exchange", json=_exchange_body(
                        transaction_id, code,
                    ))
                assert failed.status_code == 503
                with dependencies.identity_repository.transaction() as connection:
                    transaction = connection.execute(
                        "SELECT used_at FROM android_auth_transactions WHERE transaction_id=?",
                        (transaction_id,),
                    ).fetchone()
                    devices = connection.execute("SELECT COUNT(*) FROM devices WHERE platform='android'").fetchone()[0]
                    assert transaction["used_at"] is None
                    assert devices == 0
                retried = await native.post("/api/v1/session/android/exchange", json=_exchange_body(
                    transaction_id, code,
                ))
                assert retried.status_code == 201
                assert "set-cookie" not in failed.headers
                assert code not in str(failed.json())

        asyncio.run(run())
    finally:
        dependencies.close()


def test_exchange_rechecks_workspace_after_role_revocation_inside_transaction(tmp_path):
    dependencies = _configured(tmp_path)
    try:
        async def run():
            app = create_app(dependencies)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as native, \
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as web:
                login = await web.post("/api/v1/auth/login", json={
                    "login_id": "android-user", "password": "correct horse battery staple",
                })
                assert login.status_code == 200
                started = await native.post("/api/v1/session/android/transactions", json=_start_body())
                transaction_id = started.json()["data"]["transaction_id"]
                approved = await web.post("/api/v1/session/android/authorize", json={
                    "transaction_id": transaction_id,
                }, headers=_csrf_headers())
                code = parse_qs(urlsplit(approved.json()["data"]["authorization_url"]).query)["code"][0]
                original = dependencies.identity_service._selectable_tenant_workspaces
                calls = 0

                def revoke_before_workspace_projection(connection, user_id):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        connection.execute(
                            "UPDATE auth_tenant_roles SET state='revoked' WHERE user_id=?",
                            (user_id,),
                        )
                    return original(connection, user_id)

                with patch.object(dependencies.identity_service, "_selectable_tenant_workspaces",
                                  side_effect=revoke_before_workspace_projection):
                    denied = await native.post("/api/v1/session/android/exchange", json=_exchange_body(
                        transaction_id, code,
                    ))
                assert denied.status_code == 403
                with dependencies.identity_repository.transaction() as connection:
                    transaction = connection.execute(
                        "SELECT used_at FROM android_auth_transactions WHERE transaction_id=?",
                        (transaction_id,),
                    ).fetchone()
                    devices = connection.execute("SELECT COUNT(*) FROM devices WHERE platform='android'").fetchone()[0]
                    assert transaction["used_at"] is None
                    assert devices == 0
                retried = await native.post("/api/v1/session/android/exchange", json=_exchange_body(
                    transaction_id, code,
                ))
                assert retried.status_code == 201
                assert retried.json()["data"]["workspace_id"] == login.json()["data"]["workspace_id"]

        asyncio.run(run())
    finally:
        dependencies.close()
