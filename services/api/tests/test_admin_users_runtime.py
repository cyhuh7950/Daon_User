from __future__ import annotations

import asyncio
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
from daon_user_api.authorization import Role
from daon_user_api.admin_membership_roles import AdminMembershipRoleService
from daon_user_api.identity import IdentityPrincipal, PASSWORD_HASHER
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
from daon_user_api import runtime as runtime_module
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


def test_admin_membership_http_cross_tenant_projection_and_change(tmp_path: Path) -> None:
    asyncio.run(_admin_membership_http_cross_tenant_projection_and_change(tmp_path))


def _seed_admin_role_retry_target(dependencies) -> None:
    with dependencies.identity_repository.transaction() as connection:
        connection.execute(
            "INSERT INTO users(user_id,subject,state) VALUES (?,?,?)",
            ("retry-target", "retry-target", "active"),
        )
    dependencies.authorization_repository.bootstrap_workspace(
        tenant_id="retry-tenant", workspace_id="retry-workspace", owner_user_id="admin",
        owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
        data_area="cloud_sync", cost_limit_cents=100, now=datetime.now(timezone.utc),
    )
    with dependencies.authorization_repository.transaction() as connection:
        connection.execute(
            "INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("retry-tenant", "retry-workspace", "retry-target", "viewer", "active", 1,
             datetime.now(timezone.utc).isoformat()),
        )


def _change_retry_role(dependencies) -> None:
    dependencies.admin_membership_role_service.change_existing_workspace_role(
        actor=IdentityPrincipal("admin", "admin-session", "admin-device", "admin"),
        tenant_id="retry-tenant", workspace_id="retry-workspace", user_id="retry-target",
        role=Role.EDITOR, expected_version=1, idempotency_key="retry-background-key-0001",
        reason="ACCESS_REVIEW",
    )


async def _wait_for_role_audit_delivery(dependencies) -> None:
    async def delivered() -> None:
        while True:
            with dependencies.authorization_repository.transaction() as connection:
                marker = connection.execute(
                    "SELECT delivered_at FROM auth_admin_role_operations WHERE user_id='retry-target'"
                ).fetchone()[0]
            if marker is not None and len(dependencies.audit_store.list(tenant_id="retry-tenant").items) == 1:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(delivered(), timeout=3.0)


def test_admin_role_audit_background_retry_needs_no_later_patch(tmp_path: Path, monkeypatch) -> None:
    asyncio.run(_admin_role_audit_background_retry_needs_no_later_patch(tmp_path, monkeypatch))


async def _admin_role_audit_background_retry_needs_no_later_patch(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "_ADMIN_AUDIT_RETRY_INTERVAL_SECONDS", 0.02, raising=False)
    dependencies = build_dependencies(RuntimeSettings.for_test(
        database_path=tmp_path / "runtime.sqlite3", policy_version="identity-policy-v1",
    ))
    _seed_admin_role_retry_target(dependencies)
    original_append = dependencies.audit_store.append
    available = False

    def temporary_failure(draft):
        if not available:
            raise OSError("temporary audit outage")
        return original_append(draft)

    monkeypatch.setattr(dependencies.audit_store, "append", temporary_failure)
    app = create_app(dependencies)
    async with app.router.lifespan_context(app):
        _change_retry_role(dependencies)
        with dependencies.authorization_repository.transaction() as connection:
            assert connection.execute(
                "SELECT delivered_at FROM auth_admin_role_operations WHERE user_id='retry-target'"
            ).fetchone()[0] is None
        available = True
        await _wait_for_role_audit_delivery(dependencies)
        await asyncio.sleep(0.05)
        assert len(dependencies.audit_store.list(tenant_id="retry-tenant").items) == 1


def test_admin_role_audit_startup_recovers_existing_pending_row(tmp_path: Path, monkeypatch) -> None:
    asyncio.run(_admin_role_audit_startup_recovers_existing_pending_row(tmp_path, monkeypatch))


async def _admin_role_audit_startup_recovers_existing_pending_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "_ADMIN_AUDIT_RETRY_INTERVAL_SECONDS", 0.02, raising=False)
    settings = RuntimeSettings.for_test(
        database_path=tmp_path / "runtime.sqlite3", policy_version="identity-policy-v1",
    )
    first = build_dependencies(settings)
    _seed_admin_role_retry_target(first)
    def unavailable_append(draft):
        raise OSError("temporary audit outage")

    monkeypatch.setattr(first.audit_store, "append", unavailable_append)
    _change_retry_role(first)
    first.close()
    restarted = build_dependencies(settings)
    app = create_app(restarted)
    async with app.router.lifespan_context(app):
        await _wait_for_role_audit_delivery(restarted)
        assert len(restarted.audit_store.list(tenant_id="retry-tenant").items) == 1


def test_slow_audit_append_does_not_block_patch_or_unrelated_health(tmp_path: Path, monkeypatch) -> None:
    asyncio.run(_slow_audit_append_does_not_block_patch_or_unrelated_health(tmp_path, monkeypatch))


async def _slow_audit_append_does_not_block_patch_or_unrelated_health(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "_ADMIN_AUDIT_RETRY_INTERVAL_SECONDS", 0.02)
    dependencies = build_dependencies(RuntimeSettings.for_test(
        database_path=tmp_path / "runtime.sqlite3", policy_version="identity-policy-v1",
    ))
    _seed_admin_role_retry_target(dependencies)
    append_entered, release_append = threading.Event(), threading.Event()
    original_append = dependencies.audit_store.append

    def slow_append(draft):
        append_entered.set()
        release_append.wait(1.0)
        return original_append(draft)

    monkeypatch.setattr(dependencies.audit_store, "append", slow_append)
    app = create_app(dependencies)
    timer = threading.Timer(0.5, release_append.set)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://app.example.com",
            ) as client:
                client.cookies.set(WEB_SESSION_COOKIE, await _admin_cookie(client))
                timer.start()
                started = time.monotonic()
                changed = await client.patch(
                    "/api/v1/admin/tenants/retry-tenant/workspaces/retry-workspace/"
                    "memberships/retry-target/role",
                    headers={
                        "X-Daon-Bff-Transport": "internal",
                        "X-Daon-Csrf-Origin": "https://app.example.com",
                        "X-Daon-Csrf-Referer": "https://app.example.com/admin/users",
                        "Idempotency-Key": "retry-health-key-0001",
                    },
                    json={"role": "editor", "expected_version": 1, "reason": "ACCESS_REVIEW"},
                )
                health = await client.get("/health/live")
                elapsed = time.monotonic() - started
                assert changed.status_code == 200
                assert health.status_code == 200
                assert elapsed < 0.35
                assert await asyncio.to_thread(append_entered.wait, 1.0)
    finally:
        release_append.set()
        if timer.is_alive():
            timer.join()


def test_lifespan_waits_for_real_audit_thread_before_closing_dependencies(tmp_path: Path, monkeypatch) -> None:
    asyncio.run(_lifespan_waits_for_real_audit_thread_before_closing_dependencies(tmp_path, monkeypatch))


async def _lifespan_waits_for_real_audit_thread_before_closing_dependencies(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "_ADMIN_AUDIT_RETRY_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(runtime_module, "_ADMIN_AUDIT_RETRY_SHUTDOWN_SECONDS", 0.02)
    dependencies = build_dependencies(RuntimeSettings.for_test(
        database_path=tmp_path / "runtime.sqlite3", policy_version="identity-policy-v1",
    ))
    _seed_admin_role_retry_target(dependencies)
    original_append = dependencies.audit_store.append
    append_entered, release_append = threading.Event(), threading.Event()

    def initially_unavailable(draft):
        raise OSError("temporary audit outage")

    monkeypatch.setattr(dependencies.audit_store, "append", initially_unavailable)
    _change_retry_role(dependencies)

    def blocked_append(draft):
        append_entered.set()
        release_append.wait(1.0)
        return original_append(draft)

    monkeypatch.setattr(dependencies.audit_store, "append", blocked_append)
    closed_while_running = False
    close_observed = threading.Event()
    original_close = runtime_module.RuntimeDependencies.close

    def checked_close(self):
        nonlocal closed_while_running
        if self is dependencies:
            closed_while_running = append_entered.is_set() and not release_append.is_set()
        original_close(self)
        if self is dependencies:
            close_observed.set()

    monkeypatch.setattr(runtime_module.RuntimeDependencies, "close", checked_close)
    app = create_app(dependencies)
    timer = threading.Timer(0.2, release_append.set)
    try:
        async with app.router.lifespan_context(app):
            assert await asyncio.to_thread(append_entered.wait, 1.0)
            timer.start()
    finally:
        release_append.set()
        if timer.is_alive():
            timer.join()
    assert await asyncio.to_thread(close_observed.wait, 1.0)
    assert closed_while_running is False


def test_admin_tenants_list_only_auth_scopes_with_id_fallback(tmp_path: Path) -> None:
    asyncio.run(_admin_tenants_list_only_auth_scopes_with_id_fallback(tmp_path))


def test_admin_tenant_postgres_query_scopes_each_foundation_name() -> None:
    # Contract test for SQL sequencing; actual PostgreSQL RLS still requires an isolated DB.
    class FakeConnection:
        scope = None

        def execute(self, sql, params=()):
            if sql.startswith("SELECT tenant_id FROM auth_workspaces UNION SELECT tenant_id FROM auth_tenant_roles WHERE state='active'"):
                return SimpleNamespace(fetchall=lambda: [
                    {"tenant_id": "tenant-a"}, {"tenant_id": "tenant-b"},
                ])
            if sql.startswith("SELECT set_config"):
                self.scope = params[0]
                return SimpleNamespace()
            assert sql == "SELECT display_name FROM tenants WHERE tenant_id=?"
            assert params == (self.scope,)
            return SimpleNamespace(fetchone=lambda: (
                {"display_name": "Tenant A"} if self.scope == "tenant-a" else None
            ))

    repository = object.__new__(PostgresAuthorizationRepository)
    connection = FakeConnection()

    @contextmanager
    def transaction():
        yield connection

    repository.transaction = transaction
    service = AdminMembershipRoleService(
        repository=repository, system_admin_user_ids=frozenset({"admin"}), clock=lambda: None,
    )
    assert service.list_tenants(IdentityPrincipal("admin", "session", "device", "admin")) == (
        ("tenant-a", "Tenant A"), ("tenant-b", "tenant-b"),
    )


async def _admin_tenants_list_only_auth_scopes_with_id_fallback(tmp_path: Path) -> None:
    settings = replace(
        RuntimeSettings.for_test(database_path=tmp_path / "runtime.sqlite3", policy_version="identity-policy-v1"),
        system_admin_user_ids=frozenset({"admin"}),
    )
    dependencies = build_dependencies(settings)
    _add_user(dependencies, user_id="identity-only", password=secrets.token_urlsafe(24))
    ordinary_password = secrets.token_urlsafe(24)
    _add_user(dependencies, user_id="ordinary", password=ordinary_password)
    _add_user(dependencies, user_id="role-owner", password=secrets.token_urlsafe(24))
    dependencies.authorization_repository.bootstrap_workspace(
        tenant_id="tenant-b", workspace_id="workspace-b", owner_user_id="owner-b",
        owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
        data_area="cloud_sync", cost_limit_cents=100, now=dependencies.admin_user_service._clock(),
    )
    with dependencies.authorization_repository.transaction() as connection:
        connection.execute(
            "INSERT INTO auth_tenant_roles(tenant_id,user_id,role,state,version,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("tenant-a", "role-owner", "organization_admin", "active", 1, "2026-10-06T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO auth_tenant_roles(tenant_id,user_id,role,state,version,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("tenant-c", "inactive-owner", "organization_admin", "revoked", 1, "2026-10-06T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO auth_workspaces(workspace_id,tenant_id,workspace_kind,data_area,cost_limit_cents,"
            "acl_version,version,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("workspace-d", "tenant-d", "organization", "cloud_sync", 100, 1, 1,
             "2026-10-06T00:00:00+00:00"),
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(dependencies)), base_url="https://app.example.com",
    ) as client:
        assert (await client.get("/api/v1/admin/tenants")).status_code == 401
        unauthenticated = await client.get(
            "/api/v1/admin/tenants/tenant-a/users/role-owner/memberships"
        )
        assert unauthenticated.status_code == 401
        assert "target_is_system_admin" not in unauthenticated.json().get("data", {})
        normal = await client.post(
            "/api/v1/auth/login", json={"login_id": "ordinary", "password": ordinary_password},
        )
        assert normal.status_code == 200
        assert (await client.get("/api/v1/admin/tenants")).status_code == 403
        forbidden = await client.get(
            "/api/v1/admin/tenants/tenant-a/users/role-owner/memberships"
        )
        assert forbidden.status_code == 403
        assert "target_is_system_admin" not in forbidden.json().get("data", {})
        client.cookies.set(WEB_SESSION_COOKIE, await _admin_cookie(client))
        listed = await client.get("/api/v1/admin/tenants")
        assert listed.status_code == 200
        assert listed.json()["data"] == {"tenants": [
            {"tenant_id": "admin", "name": "admin"},
            {"tenant_id": "ordinary", "name": "ordinary"},
            {"tenant_id": "tenant-a", "name": "tenant-a"},
            {"tenant_id": "tenant-b", "name": "tenant-b"},
            {"tenant_id": "tenant-d", "name": "tenant-d"},
        ]}
        assert listed.json()["meta"]["trace_id"]
        role_only = await client.get(
            "/api/v1/admin/tenants/tenant-a/users/role-owner/memberships"
        )
        assert role_only.status_code == 200
        assert role_only.json()["data"] == {
            "tenant": {"tenant_id": "tenant-a", "role": "organization_admin", "state": "active", "version": 1},
            "workspaces": [],
            "target_is_system_admin": False,
        }
        hidden = await client.get(
            "/api/v1/admin/tenants/tenant-a/users/missing/memberships"
        )
        assert hidden.status_code == 404
        assert "target_is_system_admin" not in hidden.json().get("data", {})
        assert (await client.get(
            "/api/v1/admin/tenants/tenant-c/users/inactive-owner/memberships"
        )).status_code == 404
        assert (await client.get(
            "/api/v1/admin/tenants/unknown/users/role-owner/memberships"
        )).status_code == 404
    dependencies.close()


async def _admin_membership_http_cross_tenant_projection_and_change(tmp_path: Path) -> None:
    settings = replace(
        RuntimeSettings.for_test(database_path=tmp_path / "runtime.sqlite3", policy_version="identity-policy-v1"),
        system_admin_user_ids=frozenset({"admin", "owner-b"}),
    )
    dependencies = build_dependencies(settings)
    _add_user(dependencies, user_id="owner-b", password=secrets.token_urlsafe(24))
    _add_user(dependencies, user_id="target-b", password=secrets.token_urlsafe(24))
    dependencies.authorization_repository.bootstrap_workspace(
        tenant_id="tenant-b", workspace_id="workspace-b", owner_user_id="owner-b",
        owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
        data_area="cloud_sync", cost_limit_cents=100, now=dependencies.admin_user_service._clock(),
    )
    with dependencies.authorization_repository.transaction() as connection:
        connection.execute(
            "INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("tenant-b", "workspace-b", "target-b", "viewer", "active", 1, "2026-10-06T00:00:00+00:00"),
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(dependencies)), base_url="https://app.example.com",
    ) as client:
        client.cookies.set(WEB_SESSION_COOKIE, await _admin_cookie(client))
        listed = await client.get("/api/v1/admin/tenants/tenant-b/users/target-b/memberships")
        assert listed.status_code == 200
        assert listed.json()["data"] == {
            "tenant": None,
            "workspaces": [{"workspace_id": "workspace-b", "role": "viewer", "state": "active", "version": 1}],
            "target_is_system_admin": False,
        }
        assert listed.json()["meta"]["trace_id"]
        owner = await client.get("/api/v1/admin/tenants/tenant-b/users/owner-b/memberships")
        assert owner.status_code == 200
        assert owner.json()["data"]["target_is_system_admin"] is True
        admin_without_membership = await client.get(
            "/api/v1/admin/tenants/tenant-b/users/admin/memberships"
        )
        assert admin_without_membership.status_code == 404
        assert "target_is_system_admin" not in admin_without_membership.json().get("data", {})
        users = await client.get("/api/v1/admin/users")
        assert users.status_code == 200
        assert all("target_is_system_admin" not in item for item in users.json()["data"]["users"])
        changed = await client.patch(
            "/api/v1/admin/tenants/tenant-b/workspaces/workspace-b/memberships/target-b/role",
            headers={
                "X-Daon-Bff-Transport": "internal",
                "X-Daon-Csrf-Origin": "https://app.example.com",
                "X-Daon-Csrf-Referer": "https://app.example.com/admin/users",
                "Idempotency-Key": "membership-change-0001",
            },
            json={"role": "editor", "expected_version": 1, "reason": "ROLE_DUTY_CHANGE"},
        )
        assert changed.status_code == 200
        assert changed.json()["data"] == {
            "tenant_id": "tenant-b", "workspace_id": "workspace-b", "user_id": "target-b",
            "role": "editor", "state": "active", "version": 2, "acl_version": 2, "replayed": False,
        }
        assert changed.json()["meta"]["trace_id"]
        with dependencies.authorization_repository.transaction() as connection:
            assert connection.execute(
                "SELECT delivered_at FROM auth_admin_role_operations WHERE user_id='target-b'"
            ).fetchone()[0] is None
        await asyncio.to_thread(dependencies.admin_membership_role_service._dispatch_pending_audit)
        projected = dependencies.audit_store.list(tenant_id="tenant-b").items
        assert len(projected) == 1
        assert dict(projected[0].metadata) == {
            "reason_code": "ROLE_DUTY_CHANGE", "outcome": "changed", "acl_version": 2,
        }
        with dependencies.authorization_repository.transaction() as connection:
            delivered_at = connection.execute(
                "SELECT delivered_at FROM auth_admin_role_operations WHERE user_id='target-b'"
            ).fetchone()[0]
        assert delivered_at is not None
        role_path = "/api/v1/admin/tenants/tenant-b/workspaces/workspace-b/memberships/target-b/role"
        headers = {
            "X-Daon-Bff-Transport": "internal",
            "X-Daon-Csrf-Origin": "https://app.example.com",
            "X-Daon-Csrf-Referer": "https://app.example.com/admin/users",
            "Idempotency-Key": "membership-change-0001",
        }
        same_body = {"role": "editor", "expected_version": 1, "reason": "ROLE_DUTY_CHANGE"}
        replayed = await client.patch(role_path, headers=headers, json=same_body)
        assert replayed.status_code == 200
        assert replayed.json()["data"]["replayed"] is True
        assert replayed.json()["data"]["version"] == 2
        assert len(dependencies.audit_store.list(tenant_id="tenant-b").items) == 1
        reused = await client.patch(
            role_path, headers=headers,
            json={"role": "editor", "expected_version": 1, "reason": "ACCESS_REVIEW"},
        )
        assert reused.status_code == 409
        stale = await client.patch(
            role_path, headers={**headers, "Idempotency-Key": "membership-change-0002"},
            json={"role": "viewer", "expected_version": 1, "reason": "CORRECTION"},
        )
        assert stale.status_code == 412
        missing = await client.get(
            "/api/v1/admin/tenants/tenant-b/users/not-a-member/memberships"
        )
        assert missing.status_code == 404
        assert "target_is_system_admin" not in missing.json().get("data", {})
        unknown_tenant = await client.get(
            "/api/v1/admin/tenants/unknown/users/target-b/memberships"
        )
        assert unknown_tenant.status_code == 404
        assert "target_is_system_admin" not in unknown_tenant.json().get("data", {})
        assert (await client.patch(
            role_path, headers=headers,
            json={"role": "viewer", "expected_version": 2, "reason": "free text"},
        )).status_code == 422
        assert (await client.patch(
            role_path, headers=headers,
            json={"role": "viewer", "expected_version": 2, "reason": "OTHER", "note": "not allowed"},
        )).status_code == 422
        assert (await client.patch(
            role_path, headers={"Idempotency-Key": "membership-change-0003"},
            json={"role": "viewer", "expected_version": 2, "reason": "OTHER"},
        )).status_code == 403
        after = await client.get("/api/v1/admin/tenants/tenant-b/users/target-b/memberships")
        assert after.json()["data"]["workspaces"] == [
            {"workspace_id": "workspace-b", "role": "editor", "state": "active", "version": 2}
        ]
        with dependencies.authorization_repository.transaction() as connection:
            connection.execute(
                "CREATE TRIGGER fail_admin_role_audit BEFORE INSERT ON auth_admin_role_operations "
                "BEGIN SELECT RAISE(FAIL, 'isolated audit failure'); END"
            )
        failed = await client.patch(
            role_path, headers={**headers, "Idempotency-Key": "membership-change-0004"},
            json={"role": "viewer", "expected_version": 2, "reason": "CORRECTION"},
        )
        assert failed.status_code == 503
        with dependencies.authorization_repository.transaction() as connection:
            role_row = connection.execute(
                "SELECT role,version FROM auth_memberships WHERE tenant_id=? AND workspace_id=? AND user_id=?",
                ("tenant-b", "workspace-b", "target-b"),
            ).fetchone()
            acl_row = connection.execute(
                "SELECT acl_version FROM auth_workspaces WHERE tenant_id=? AND workspace_id=?",
                ("tenant-b", "workspace-b"),
            ).fetchone()
            assert (role_row["role"], role_row["version"], acl_row["acl_version"]) == ("editor", 2, 2)
    dependencies.close()


def test_system_admin_user_api_denies_normal_user_and_protects_admin(tmp_path: Path) -> None:
    asyncio.run(_system_admin_user_api_denies_normal_user_and_protects_admin(tmp_path))


def test_admin_password_reset_route_rejects_protected_admin_target(tmp_path: Path) -> None:
    asyncio.run(_admin_password_reset_route_rejects_protected_admin_target(tmp_path))


async def _admin_password_reset_route_rejects_protected_admin_target(tmp_path: Path) -> None:
    settings = replace(
        RuntimeSettings.for_test(database_path=tmp_path / "runtime.sqlite3", policy_version="identity-policy-v1"),
        system_admin_user_ids=frozenset({"admin"}),
    )
    dependencies = build_dependencies(settings)
    app = create_app(dependencies)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example.com"
    ) as client:
        client.cookies.set(WEB_SESSION_COOKIE, await _admin_cookie(client))
        response = await client.post(
            "/api/v1/admin/users/admin/password-reset",
            headers={
                "Origin": "https://app.example.com",
                "X-Daon-Bff-Transport": "internal",
                "Idempotency-Key": "admin-reset-protected-01",
            },
            json={},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROTECTED_ADMIN_ACCOUNT"
    dependencies.close()


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
        assert (await client.get("/api/v1/admin/tenants")).status_code == 403
        assert (await client.get(
            "/api/v1/admin/tenants/admin/users/admin/memberships"
        )).status_code == 403
        assert (await client.patch(
            "/api/v1/admin/tenants/admin/workspaces/admin/memberships/admin/role",
            headers={
                "X-Daon-Bff-Transport": "internal",
                "X-Daon-Csrf-Origin": "https://app.example.com",
                "X-Daon-Csrf-Referer": "https://app.example.com/admin/users",
                "Idempotency-Key": "normal-membership-denied-0001",
            },
            json={"role": "viewer", "expected_version": 1, "reason": "OTHER"},
        )).status_code == 403
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
            headers={
                "Origin": "https://app.example.com",
                "X-Daon-Bff-Transport": "internal",
                "Idempotency-Key": "delete-protected-admin-001",
            },
        )
        assert delete.status_code == 409
        assert delete.json()["error"]["code"] == "PROTECTED_ADMIN_ACCOUNT"
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
