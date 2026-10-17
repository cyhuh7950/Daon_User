"""S39 L2의 실제 runtime 감사 루프와 기존 HTTP 차단 경계."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from daon_user_api import runtime


@pytest.mark.parametrize("policy_version", ["runtime-policy-v1", "s39-configured-policy-v7"])
def test_l2_runtime_policy_reaches_api_receipt_and_central_audit(tmp_path, monkeypatch, policy_version):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from daon_user_api.authorization import Role
    from daon_user_api.identity import IdentityPrincipal

    dependencies = runtime.build_dependencies(runtime.RuntimeSettings.for_test(
        database_path=tmp_path / "policy-runtime.sqlite", policy_version=policy_version))
    try:
        dependencies.authorization_repository.bootstrap_workspace(
            tenant_id="s39-policy-tenant", workspace_id="s39-policy-workspace", owner_user_id="s39-policy-admin",
            owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=datetime.now(timezone.utc))
        mounted = []
        original_mount = runtime.OrganizationApi.mount
        def capture_mount(api, router, principal):
            mounted.append(api)
            return original_mount(api, router, principal)
        monkeypatch.setattr(runtime.OrganizationApi, "mount", capture_mount)
        runtime.create_app(dependencies)
        assert len(mounted) == 1
        # 정식 runtime의 503 보호는 열지 않는다. 실제로 구성된 API 소비자만 직접 호출한다.
        app = FastAPI()
        original_mount(mounted[0], app, lambda _request: IdentityPrincipal(
            "s39-policy-admin", "policy-session", "policy-device", "s39-policy-tenant"))
        with TestClient(app) as client:
            response = client.post("/api/v1/organization/tenants/s39-policy-tenant/invitations",
                json={"code": "s39-policy-synthetic", "expires_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), "max_uses": 1, "workspace_id": "s39-policy-workspace"},
                headers={"Idempotency-Key": "s39-policy-receipt-0001", "X-Daon-Expected-Session-Id": "policy-session"})
            assert response.status_code == 201
        with dependencies.organization_repository.transaction() as connection:
            receipt = connection.execute("SELECT event_id,policy_version,delivered_at FROM organization_operation_outbox").fetchone()
        assert receipt["policy_version"] == dependencies.settings.policy_version
        assert receipt["delivered_at"] is None
        assert dependencies.organization_repository.dispatch_pending_management_audits() == 1
        event = dependencies.audit_store.read(receipt["event_id"])
        assert event.policy_version == receipt["policy_version"] == policy_version
        assert event.actor_id == "s39-policy-admin" and event.tenant_id == "s39-policy-tenant"
    finally:
        dependencies.close()


def _seed(dependencies):
    payload = {"code": "s39-runtime-synthetic", "expires_at": datetime.now(timezone.utc)+timedelta(days=1), "max_uses": 1}
    return dependencies.organization_repository.execute_management_operation(
        operation="organization.invitation.create", actor_id="admin", tenant_id="s39-runtime-tenant", target_id=None,
        payload=payload, idempotency_key="s39-runtime-invitation-0001",
        request_fingerprint=hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
        trace_id="s39-runtime-trace", policy_version="s39-runtime-policy",
    )


def test_l2_runtime_restart_delivers_pending_without_another_http_mutation(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(runtime, "_ADMIN_AUDIT_RETRY_INTERVAL_SECONDS", 0.02)
        settings = runtime.RuntimeSettings.for_test(database_path=tmp_path / "runtime.sqlite", policy_version="s39-runtime-policy")
        first = runtime.build_dependencies(settings)
        _seed(first)
        first.close()
        dependencies = runtime.build_dependencies(settings)
        app = runtime.create_app(dependencies)
        async with app.router.lifespan_context(app):
            async def delivered():
                while True:
                    with dependencies.organization_repository.transaction() as connection:
                        row = connection.execute("SELECT event_id,delivered_at FROM organization_operation_outbox").fetchone()
                    if row["delivered_at"] is not None:
                        assert dependencies.audit_store.read(row["event_id"]).trace_id == "s39-runtime-trace"
                        return
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(delivered(), timeout=2)
            assert len(dependencies.audit_store.list(tenant_id="s39-runtime-tenant").items) == 1
    asyncio.run(run())


def test_l2_org_retry_failure_does_not_skip_existing_identity_audit(tmp_path, monkeypatch):
    async def run():
        from threading import Event
        monkeypatch.setattr(runtime, "_ADMIN_AUDIT_RETRY_INTERVAL_SECONDS", 0.02)
        dependencies = runtime.build_dependencies(runtime.RuntimeSettings.for_test(database_path=tmp_path / "runtime.sqlite", policy_version="s39-runtime-policy"))
        entered, identity_after_failure = Event(), Event()
        original = dependencies.identity_service.dispatch_pending_tenant_switch_audits
        def unavailable(**_kwargs):
            entered.set()
            raise OSError("synthetic organization pending outage")
        def identity_dispatch():
            if entered.is_set(): identity_after_failure.set()
            return original()
        monkeypatch.setattr(dependencies.organization_repository, "dispatch_pending_management_audits", unavailable)
        monkeypatch.setattr(dependencies.identity_service, "dispatch_pending_tenant_switch_audits", identity_dispatch)
        app = runtime.create_app(dependencies)
        async with app.router.lifespan_context(app):
            assert await asyncio.to_thread(identity_after_failure.wait, 2)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
                assert (await client.get("/health/live")).status_code == 200
    asyncio.run(run())


def test_l3_runtime_organization_validation_422_is_limited_to_headers_and_revoke_query(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from daon_user_api.identity import IdentityPrincipal

    dependencies = runtime.build_dependencies(runtime.RuntimeSettings.for_test(
        database_path=tmp_path / "l3-validation.sqlite", policy_version="l3-validation-policy"))
    # HTTP handler/middleware를 소비하되 인증·실제 PG 활성화만 명시적 대역으로 통과시킨다.
    monkeypatch.setattr(runtime, "_principal", lambda *_args: IdentityPrincipal("admin", "session", "device", "tenant-a"))
    monkeypatch.setattr(runtime, "_organization_management_ready", lambda _dependencies: True)
    headers = {"Idempotency-Key": "l3-validation-operation-0001", "X-Daon-Expected-Session-Id": "session"}
    paths = [
        ("POST", "/api/v1/organization/join-requests", {"invitation_code": "synthetic"}),
        ("POST", "/api/v1/organization/join-requests/unknown/decision", {"approved": False, "expected_version": 1}),
        ("POST", "/api/v1/organization/tenants/tenant-a/invitations", {"workspace_id": "workspace-a", "code": "synthetic", "expires_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), "max_uses": 1}),
        ("DELETE", "/api/v1/organization/tenants/tenant-a/invitations/unknown?expected_version=1", None),
        ("PATCH", "/api/v1/organization/tenants/tenant-a/members/member/role", {"workspace_id": "workspace-a", "role": "editor", "expected_version": 1, "expected_auth_version": 1, "reason": "검토"}),
        ("PATCH", "/api/v1/organization/tenants/tenant-a/members/member/state", {"expected_version": 1}),
    ]
    try:
        with TestClient(runtime.create_app(dependencies)) as client:
            for method, path, body in paths:
                for header, values in (("X-Daon-Expected-Session-Id", (None, "", "invalid space", "x"*129)), ("Idempotency-Key", (None, "", "short", "x"*129))):
                    for value in values:
                        invalid = dict(headers)
                        if value is None: invalid.pop(header)
                        else: invalid[header] = value
                        result = client.request(method, path, json=body, headers={"Content-Type": "application/json", **invalid})
                        assert result.status_code == 422, (method, path, header, value, result.status_code)
                        assert result.json()["error"]["code"] == "INVALID_REQUEST"
                if method != "DELETE":
                    result = client.request(method, path, json={}, headers=headers)
                    assert result.status_code == 400, (method, path, result.status_code)
            for version in (None, "", "bad", "0", "-1", "1.5"):
                query = "" if version is None else "?expected_version="+version
                result = client.delete("/api/v1/organization/tenants/tenant-a/invitations/unknown"+query, headers={"Content-Type": "application/json", **headers})
                assert result.status_code == 422, (version, result.status_code)
            for path in ("/api/v1/auth/password-reset/request", "/api/v1/organization/creation-requests"):
                assert client.post(path, json={}, headers=headers).status_code == 400
            assert client.patch("/api/v1/account/profile", json={}, headers=headers).status_code == 422
            assert client.post("/api/v1/sources/synthetic/deletion-requests", json={"notebook_id": "synthetic"}, headers=headers).status_code == 403
        with dependencies.organization_repository.transaction() as connection:
            for table in ("organization_operation_outbox", "invitation_codes", "organization_join_requests", "tenant_memberships"):
                assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        dependencies.close()
