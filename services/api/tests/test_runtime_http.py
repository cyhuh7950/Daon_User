from __future__ import annotations

import tempfile
import unittest
import asyncio
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from test_identity_support import (
    FakeVerifiedOidcProvider,
    POLICY_VERSION,
    TRACE_ID,
    create_service,
)
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import (
    AccessAction,
    Action,
    AuthorizationError,
    EvidenceDependency,
    HistoricalResultDescriptor,
    Role,
    SqliteAuthorizationRepository,
    AuthorizationService,
)
from daon_user_api.identity import ClientKind, DevicePlatform
from daon_user_api.organization_membership import SqliteOrganizationRepository, MembershipState
from daon_user_api.runtime import (
    WEB_SESSION_COOKIE,
    RuntimeDependencies,
    RuntimeSettings,
    request_timeout_for_path,
    build_dependencies,
    create_app,
)


class UnreadyCloudStore:
    def readiness(self):  # type: ignore[no-untyped-def]
        return type("Status", (), {"ready": False})()

    def close(self) -> None:
        return None


class SlowUnreadyCloudStore(UnreadyCloudStore):
    def readiness(self):  # type: ignore[no-untyped-def]
        time.sleep(0.25)
        return super().readiness()


class ScopeRecordingCloudStore(UnreadyCloudStore):
    def __init__(self) -> None:
        self.scopes = []

    def seed_scope(self, context):  # type: ignore[no-untyped-def]
        self.scopes.append(context)


class UnreadyObjectStorage:
    def health(self) -> bool:
        return False


class RuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_m2s_real_session_without_workspace_reaches_system_home_without_creating_scope(self):
        # 이 시험의 폐기 SQLite에서만 실제 Workspace 부재를 구성한다.
        with self.authorization_repository.transaction() as connection:
            connection.execute("DELETE FROM auth_result_dependencies WHERE result_id IN (SELECT result_id FROM auth_historical_results WHERE tenant_id=?)", (self.web.tenant_id,))
            for table in ("auth_historical_results", "auth_source_access", "auth_memberships", "auth_admin_role_operations",
                    "auth_workspace_permission_rules", "auth_workspace_policies"):
                connection.execute(f"DELETE FROM {table} WHERE tenant_id=?", (self.web.tenant_id,))
            connection.execute("DELETE FROM auth_workspaces WHERE tenant_id=?", (self.web.tenant_id,))
        self.assertEqual(self.identity.selectable_tenant_workspaces(user_id=self.web.user_id), ())
        self.assertIsNone(self.authorization_repository.primary_workspace_id(self.web.tenant_id))
        headers={"cookie":f"{WEB_SESSION_COOKIE}={self.web.access_token}"}
        self.dependencies.settings=replace(self.settings,system_admin_user_ids=frozenset({self.web.user_id}))
        response=await self.client.get("/api/v1/session",headers=headers)
        self.assertEqual(response.status_code,200)
        data=response.json()["data"]
        self.assertIsNone(data["workspace_id"])
        self.assertEqual(data["recovery_operations"],[])
        self.assertIs(data["is_system_admin"],True)
        self.assertEqual((await self.client.get("/api/v1/system/operations/status",headers=headers)).status_code,200)
        self.assertIsNone(self.authorization_repository.primary_workspace_id(self.web.tenant_id))
        self.dependencies.settings=self.settings
        self.assertEqual((await self.client.get("/api/v1/system/operations/status",headers=headers)).status_code,403)

    async def test_m2s_system_operations_real_identity_admin_without_workspace_and_input_denial(self):
        path="/api/v1/system/operations/status"
        headers={"cookie":f"{WEB_SESSION_COOKIE}={self.web.access_token}"}
        self.assertEqual((await self.client.get(path)).status_code,401)
        # Workspace 관리자라는 기존 role만으로 system 권한이 생기지 않는다.
        self.assertEqual((await self.client.get(path,headers=headers)).status_code,403)
        self.dependencies.settings=replace(self.settings,system_admin_user_ids=frozenset({self.web.user_id}))
        allowed=await self.client.get(path,headers=headers)
        self.assertEqual(allowed.status_code,200)
        self.assertRegex(allowed.headers.get("etag", ""), r'^"projection-[0-9a-f]{24}"$')
        self.assertEqual(allowed.headers.get("cache-control"), "no-store")
        self.assertEqual(allowed.headers["x-trace-id"], allowed.json()["meta"]["trace_id"])
        data=allowed.json()["data"]
        self.assertEqual(set(data),{"schema_version","checked_at","health","worker_samples","resource_samples","deployment_summary","unimplemented"})
        self.assertEqual((len(data["health"]),len(data["worker_samples"]),len(data["resource_samples"])),(3,3,12))
        self.assertNotIn("workspace",str(data));self.assertNotIn("provider",str(data))
        self.assertEqual((await self.client.get(path+"?workspace_id=workspace-001",headers=headers)).status_code,400)
        self.assertEqual((await self.client.request("GET",path,headers=headers,content=b"{}")).status_code,400)
        self.dependencies.settings=replace(self.settings,system_admin_user_ids=frozenset())
        self.assertEqual((await self.client.get(path,headers=headers)).status_code,403)

    async def test_m2s_system_operations_rechecks_role_revocation_expiry_and_never_reads_workspace_or_provider(self):
        from types import SimpleNamespace
        from datetime import timedelta
        path="/api/v1/system/operations/status";headers={"cookie":f"{WEB_SESSION_COOKIE}={self.web.access_token}"}
        allowed=replace(self.settings,system_admin_user_ids=frozenset({self.web.user_id}))
        self.dependencies.settings=allowed
        calls=[]
        def readiness():
            calls.append("database-health")
            self.dependencies.settings=replace(allowed,system_admin_user_ids=frozenset())
            return SimpleNamespace(ready=True)
        self.dependencies.cloud_store=SimpleNamespace(readiness=readiness,close=lambda:None)
        with patch.object(self.authorization,"authorize_action",side_effect=AssertionError("NO_WORKSPACE_QUERY")):
            response=await self.client.get(path,headers=headers)
        self.assertEqual(response.status_code,403);self.assertEqual(calls,["database-health"])
        self.dependencies.cloud_store=None;self.dependencies.settings=allowed
        self.identity.revoke_current_web_session(access_token=self.web.access_token,policy_version=POLICY_VERSION,trace_id=TRACE_ID)
        self.assertEqual((await self.client.get(path,headers=headers)).status_code,401)
        native_headers={"Authorization":f"Bearer {self.native.access_token}"}
        self.clock.advance(hours=2)
        self.assertEqual((await self.client.get(path,headers=native_headers)).status_code,401)

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "runtime.sqlite3"
        self.audit = AuditEventStore()
        self.identity, self.identity_repository, _, self.clock = create_service(
            self.db_path, audit_store=self.audit
        )
        web_start = self.identity.begin_oidc_login(
            issuer="https://login.example.com", client_id="daon-web",
            audience="daon-user-api", redirect_uri="https://app.example.com/auth/callback",
            client_kind=ClientKind.WEB, tenant_id="tenant-001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        web_provider = FakeVerifiedOidcProvider()
        web_provider.expected_nonce = web_start.nonce
        self.web = self.identity.complete_oidc_login(
            state=web_start.state, authorization_code=web_provider.authorization_code,
            code_verifier=web_start.code_verifier, client_id="daon-web",
            redirect_uri="https://app.example.com/auth/callback", provider=web_provider,
            platform=DevicePlatform.WEB, trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        native_start = self.identity.begin_oidc_login(
            issuer="https://login.example.com", client_id="daon-native",
            audience="daon-user-api", redirect_uri="com.sinsan.daon:/oidc/callback",
            client_kind=ClientKind.NATIVE, tenant_id="tenant-001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        native_provider = FakeVerifiedOidcProvider()
        native_provider.expected_nonce = native_start.nonce
        self.native = self.identity.complete_oidc_login(
            state=native_start.state, authorization_code=native_provider.authorization_code,
            code_verifier=native_start.code_verifier, client_id="daon-native",
            redirect_uri="com.sinsan.daon:/oidc/callback", provider=native_provider,
            platform=DevicePlatform.ANDROID, trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        self.authorization_repository = SqliteAuthorizationRepository(self.db_path)
        self.authorization_repository.bootstrap_workspace(
            tenant_id=self.web.tenant_id, workspace_id="workspace-001",
            owner_user_id=self.web.user_id, owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=self.clock(),
        )
        self.authorization_repository.bootstrap_workspace(
            tenant_id="tenant-foreign", workspace_id="workspace-foreign",
            owner_user_id="foreign-owner", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=self.clock(),
        )
        self.authorization_repository.insert_historical_result(
            HistoricalResultDescriptor(
                result_id="result-001", result_kind="output",
                tenant_id=self.web.tenant_id, workspace_id="workspace-001",
                source_version_ids=("source-version-001",),
                evidence_reference_ids=("reference-001",),
                dependencies=(
                    EvidenceDependency(
                        "reference-001", "source-version-001", ("segment-001",), True, False
                    ),
                ),
                original_policy_version="historical-policy-v1",
                original_membership_version=1,
            ),
            self.clock(),
        )
        self.authorization = AuthorizationService(
            repository=self.authorization_repository, audit_store=self.audit,
            clock=self.clock, identity_service=self.identity,
        )
        self.settings = RuntimeSettings.for_test(
            database_path=self.db_path, policy_version=POLICY_VERSION
        )
        self.dependencies = RuntimeDependencies(
            settings=self.settings, identity_service=self.identity,
            authorization_service=self.authorization, audit_store=self.audit,
            identity_repository=self.identity_repository,
            authorization_repository=self.authorization_repository,
        )
        self.app = create_app(self.dependencies)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://" + "127.0.0.1",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    async def test_s39_flag_never_opens_unprotected_workflow_and_keeps_suspension_guard(self) -> None:
        repository = SqliteOrganizationRepository(self.db_path.with_name("organization.sqlite3"))
        self.dependencies.organization_repository = repository
        pending = repository.create_join_request(tenant_id=self.web.tenant_id, user_id=self.web.user_id, now=self.clock())
        repository.decide_join_request(request_id=pending.request_id, actor_id="admin", approved=True, expected_version=1, reason=None, role="member", now=self.clock())
        repository.set_membership(tenant_id=self.web.tenant_id, user_id=self.web.user_id, actor_id="admin", state=MembershipState.SUSPENDED, expected_version=1, reason="시험 중지", now=self.clock())
        paths = [("POST", "/api/v1/organization/join-requests"), ("POST", "/api/v1/organization/join-requests/request-a/decision"),
                 ("POST", "/api/v1/organization/tenants/tenant-001/invitations"), ("DELETE", "/api/v1/organization/tenants/tenant-001/invitations/invite-a"),
                 ("PATCH", "/api/v1/organization/tenants/tenant-001/members/user-a/role"), ("PATCH", "/api/v1/organization/tenants/tenant-001/members/user-a/state")]
        headers = {"cookie": f"{WEB_SESSION_COOKIE}={self.web.access_token}"}
        for enabled in (False, True):
            self.dependencies.settings = replace(self.settings, organization_admin_v2_enabled=enabled)
            for method, path in paths:
                response = await self.client.request(method, path, json={}, headers=headers)
                self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "ORGANIZATION_WORKFLOW_UNAVAILABLE"))
            response = await self.client.get("/api/v1/workspaces/workspace-001/knowledge-scope", headers=headers)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "ORGANIZATION_MEMBERSHIP_SUSPENDED"))
            self.assertEqual((await self.client.post(paths[0][1], json={})).status_code, 401)

    async def test_s39_flag_requires_verified_cloud_storage_before_ready(self) -> None:
        self.dependencies.settings = replace(self.settings, organization_admin_v2_enabled=True)
        response = await self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)

    async def test_screen_preferences_are_authenticated_user_scope_and_reset_to_system(self) -> None:
        auth = {"cookie": f"{WEB_SESSION_COOKIE}={self.web.access_token}"}
        initial = await self.client.get("/api/v1/preferences/screen", headers=auth)
        saved = await self.client.put(
            "/api/v1/preferences/screen", headers={**auth, "Content-Type": "application/json"},
            json={"theme": "dark"},
        )
        reset = await self.client.put(
            "/api/v1/preferences/screen", headers={**auth, "Content-Type": "application/json"},
            json={"theme": "system"},
        )
        unauthenticated = await self.client.get("/api/v1/preferences/screen")
        self.assertEqual(initial.json()["data"], {"theme": "system"})
        self.assertEqual(saved.json()["data"], {"theme": "dark"})
        self.assertEqual(reset.json()["data"], {"theme": "system"})
        self.assertEqual((initial.status_code, saved.status_code, reset.status_code), (200, 200, 200))
        self.assertEqual((unauthenticated.status_code, unauthenticated.json()["error"]["code"]), (401, "AUTHENTICATION_REQUIRED"))

    async def test_development_bypass_session_matches_web_contract(self) -> None:
        self.dependencies.settings = replace(self.settings, profile="development", dev_auth_bypass=True)
        app = create_app(self.dependencies)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            response = await client.get("/api/v1/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()["data"]),
            {
                "user_id", "tenant_id", "workspace_id", "session_id", "device_id",
                "login_id",
                "client_kind", "delivery", "expires_at", "recovery_operations",
                "password_change_required", "is_system_admin",
            },
        )

    async def test_notebook_create_list_get_update_title_contract(self) -> None:
        auth = {"cookie": f"{WEB_SESSION_COOKIE}={self.web.access_token}"}
        created = await self.client.post(
            "/api/v1/workspaces/workspace-001/notebooks",
            headers={**auth, "Content-Type": "application/json", "Idempotency-Key": "notebook-runtime-create-0001"},
            json={"title": "  전략 노트  ", "description": "선택 설명"},
        )
        self.assertEqual(created.status_code, 201)
        notebook = created.json()["data"]
        self.assertEqual(set(notebook), {"notebook_id", "title", "source_count", "output_count", "updated_at", "status", "etag"})
        self.assertRegex(notebook["etag"], r'^"notebook:[1-9][0-9]*"$')
        self.assertEqual((notebook["title"], notebook["source_count"], notebook["output_count"], notebook["status"]), ("전략 노트", 0, 0, "empty"))
        self.assertEqual(created.headers["etag"], '"notebook:1"')

        replay = await self.client.post(
            "/api/v1/workspaces/workspace-001/notebooks",
            headers={**auth, "Content-Type": "application/json", "Idempotency-Key": "notebook-runtime-create-0001"},
            json={"title": "전략 노트", "description": "선택 설명"},
        )
        listed = await self.client.get("/api/v1/workspaces/workspace-001/notebooks", headers=auth)
        fetched = await self.client.get(
            f"/api/v1/workspaces/workspace-001/notebooks/{notebook['notebook_id']}", headers=auth,
        )
        selected = await self.client.get(
            f"/api/v1/workspaces/workspace-001/notebooks/{notebook['notebook_id']}/context",
            headers=auth,
        )
        updated = await self.client.patch(
            f"/api/v1/workspaces/workspace-001/notebooks/{notebook['notebook_id']}",
            headers={**auth, "Content-Type": "application/json", "Idempotency-Key": "notebook-runtime-title-0001", "If-Match": created.headers["etag"]},
            json={"title": "전략 노트 2"},
        )
        self.assertEqual((replay.status_code, replay.json()["meta"]["replayed"]), (200, True))
        self.assertEqual((listed.status_code, len(listed.json()["data"])), (200, 1))
        self.assertTrue(listed.headers["etag"].startswith('"projection-'))
        self.assertEqual((fetched.status_code, fetched.headers["etag"]), (200, '"notebook:1"'))
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.headers["etag"], '"notebook-binding:1"')
        self.assertEqual(selected.json()["data"], {
            "notebook_id": notebook["notebook_id"], "sources": [],
            "knowledge_context_ids": [], "conversation_thread_ids": [],
            "studio_output_ids": [], "output_version_ids": [], "generation_settings_ids": [],
            "source_deletion_requests": [],
            "conversation": None,
        })
        self.assertEqual((updated.status_code, updated.json()["data"]["title"], updated.headers["etag"]), (200, "전략 노트 2", '"notebook:2"'))

        unknown = await self.client.post(
            "/api/v1/workspaces/workspace-001/notebooks",
            headers={**auth, "Content-Type": "application/json", "Idempotency-Key": "notebook-runtime-create-0002"},
            json={"title": "노트", "unknown": True},
        )
        unauthenticated = await self.client.get("/api/v1/workspaces/workspace-001/notebooks")
        context_without_auth = await self.client.get(
            f"/api/v1/workspaces/workspace-001/notebooks/{notebook['notebook_id']}/context"
        )
        self.assertEqual((unknown.status_code, unknown.json()["error"]["code"]), (400, "INVALID_REQUEST"))
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(context_without_auth.status_code, 401)

    async def test_s42_account_notification_producer_uses_explicit_runtime_flag(self) -> None:
        """생성자 연결 계약만 확인한다. 계정 사건/실제 PG 저장 성공 증거가 아니다."""
        try:
            for enabled in (False, True):
                with self.subTest(enabled=enabled):
                    self.dependencies.settings = replace(self.settings, notification_inbox_enabled=enabled)
                    with patch('daon_user_api.account_settings.AccountSettingsService') as factory:
                        create_app(self.dependencies)
                    factory.assert_called_once_with(self.identity, notification_inbox_enabled=enabled)
        finally:
            self.dependencies.settings = self.settings

    def test_s42_organization_notification_producer_uses_explicit_runtime_flag(self) -> None:
        """production factory 인자 연결만 확인한다. 실제 PG/조직 역할 성공 증거가 아니다."""
        import secrets
        from types import SimpleNamespace

        class FactoryReached(Exception):
            pass

        for enabled in (False, True):
            settings = SimpleNamespace(database_path=self.db_path, profile='production',
                cloud_database_dsn='postgresql://unused.invalid/unused',
                step_up_token_key_file=SimpleNamespace(read_bytes=lambda: secrets.token_bytes(32)),
                notification_inbox_enabled=enabled)
            with self.subTest(enabled=enabled), \
                patch('daon_user_api.audit.PostgresSecurityAuditStore') as audit, \
                patch('daon_user_api.runtime.PostgresIdentityRepository'), \
                patch('daon_user_api.runtime.PostgresAuthorizationRepository'), \
                patch('daon_user_api.runtime.PostgresOrganizationRepository', side_effect=FactoryReached) as factory:
                with self.assertRaises(FactoryReached):
                    build_dependencies(settings)
                factory.assert_called_once_with(settings.cloud_database_dsn,
                    audit_store=audit.return_value, notification_inbox_enabled=enabled)

    def test_s42_c9_role_notification_uses_explicit_runtime_flag(self) -> None:
        """factory 인자 연결만 확인한다. 실제 역할/source 저장 검증은 별도다."""
        import secrets
        from types import SimpleNamespace

        class FactoryReached(Exception):
            pass

        for enabled in (False, True):
            settings = SimpleNamespace(database_path=self.db_path, profile='production',
                cloud_database_dsn='postgresql://unused.invalid/unused',
                step_up_token_key_file=SimpleNamespace(read_bytes=lambda: secrets.token_bytes(32)),
                notification_inbox_enabled=enabled, system_admin_user_ids=frozenset({'s42-c9-factory-admin'}))
            with self.subTest(enabled=enabled), \
                patch('daon_user_api.audit.PostgresSecurityAuditStore') as audit, \
                patch('daon_user_api.runtime.PostgresIdentityRepository'), \
                patch('daon_user_api.runtime.PostgresAuthorizationRepository') as authorization, \
                patch('daon_user_api.runtime.PostgresOrganizationRepository'), \
                patch('daon_user_api.runtime.IdentityService'), \
                patch('daon_user_api.runtime.AdminUserService'), \
                patch('daon_user_api.runtime.AdminMembershipRoleService', side_effect=FactoryReached) as factory:
                with self.assertRaises(FactoryReached):
                    build_dependencies(settings)
                self.assertEqual(set(factory.call_args.kwargs), {'repository', 'system_admin_user_ids', 'clock',
                    'audit_store', 'notification_inbox_enabled'})
                self.assertIs(factory.call_args.kwargs['repository'], authorization.return_value)
                self.assertIs(factory.call_args.kwargs['audit_store'], audit.return_value)
                self.assertIs(factory.call_args.kwargs['notification_inbox_enabled'], enabled)

    def test_s42_source_access_notification_uses_explicit_runtime_flag(self) -> None:
        """실제 생성자 인자 전달만 확인한다. DB·인증 성공 증거가 아니다."""
        import secrets
        from types import SimpleNamespace

        class FactoryReached(Exception):
            pass

        for enabled in (False, True):
            settings = SimpleNamespace(database_path=self.db_path, profile='production',
                cloud_database_dsn='postgresql://unused.invalid/unused',
                step_up_token_key_file=SimpleNamespace(read_bytes=lambda: secrets.token_bytes(32)),
                notification_inbox_enabled=enabled, system_admin_user_ids=frozenset())
            with self.subTest(enabled=enabled), \
                patch('daon_user_api.audit.PostgresSecurityAuditStore') as audit, \
                patch('daon_user_api.runtime.PostgresIdentityRepository'), \
                patch('daon_user_api.runtime.PostgresAuthorizationRepository') as repository, \
                patch('daon_user_api.runtime.PostgresOrganizationRepository'), \
                patch('daon_user_api.runtime.IdentityService') as identity, \
                patch('daon_user_api.runtime.AdminUserService'), \
                patch('daon_user_api.runtime.AdminMembershipRoleService'), \
                patch('daon_user_api.runtime.AuthorizationService', side_effect=FactoryReached) as factory:
                with self.assertRaises(FactoryReached):
                    build_dependencies(settings)
                arguments = factory.call_args.kwargs
                self.assertEqual(set(arguments), {'repository', 'audit_store', 'clock',
                    'identity_service', 'notification_inbox_enabled'})
                self.assertIs(arguments['repository'], repository.return_value)
                self.assertIs(arguments['audit_store'], audit.return_value)
                self.assertIs(arguments['identity_service'], identity.return_value)
                self.assertIs(arguments['notification_inbox_enabled'], enabled)

    def test_s42_identity_login_notification_uses_explicit_runtime_flag(self) -> None:
        """생성자 flag 연결 계약만 확인한다. 실제 인증/PG 성공 증거는 별도다."""
        import secrets
        from types import SimpleNamespace

        class FactoryReached(Exception):
            pass

        for enabled in (False, True):
            settings = SimpleNamespace(database_path=self.db_path, profile='production',
                cloud_database_dsn='postgresql://unused.invalid/unused',
                step_up_token_key_file=SimpleNamespace(read_bytes=lambda: secrets.token_bytes(32)),
                notification_inbox_enabled=enabled)
            with self.subTest(enabled=enabled), \
                patch('daon_user_api.audit.PostgresSecurityAuditStore') as audit, \
                patch('daon_user_api.runtime.PostgresIdentityRepository') as identity, \
                patch('daon_user_api.runtime.PostgresAuthorizationRepository'), \
                patch('daon_user_api.runtime.PostgresOrganizationRepository'), \
                patch('daon_user_api.runtime.IdentityService', side_effect=FactoryReached) as factory:
                with self.assertRaises(FactoryReached):
                    build_dependencies(settings)
                factory.assert_called_once()
                arguments = factory.call_args.kwargs
                self.assertEqual(set(arguments), {'repository', 'audit_store', 'oidc_policies', 'clock',
                    'step_up_token_key', 'notification_inbox_enabled'})
                self.assertIs(arguments['repository'], identity.return_value)
                self.assertIs(arguments['audit_store'], audit.return_value)
                self.assertIs(arguments['notification_inbox_enabled'], enabled)

    async def test_s42_notification_runtime_flag_and_scoped_repository_contract(self) -> None:
        """실제 세션/권한 + 저장 port 대체 HTTP 계약. 실제 PG 성공은 별도 시험이다."""
        from types import SimpleNamespace
        from daon_user_api.cloud_storage import PostgresCloudStore
        from daon_user_api.notification import ReferenceNotificationRepository, NotificationError
        self.dependencies.settings = replace(self.settings, notification_inbox_enabled=True)
        cloud=PostgresCloudStore('postgresql://unused.invalid/unused',min_size=0,max_size=1,notification_inbox_enabled=True)
        self.dependencies.cloud_store=cloud
        self.authorization_repository.bootstrap_workspace(tenant_id=self.web.tenant_id,workspace_id='workspace-002',
            owner_user_id=self.web.user_id,owner_role=Role.ORGANIZATION_ADMIN,workspace_kind='organization',
            data_area='cloud_sync',cost_limit_cents=1000,now=self.clock())
        scopes=[]
        class ScopedPort(ReferenceNotificationRepository):
            transactional_audit=True
            def __init__(current,dsn,**scope):
                super().__init__();scopes.append(scope)
        app=create_app(self.dependencies)
        auth={'cookie':f'{WEB_SESSION_COOKIE}={self.web.access_token}'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            with patch.object(cloud,'readiness',return_value=SimpleNamespace(ready=True,schema_revision='0069')),\
                patch('daon_user_api.notification_postgres.ScopedPostgresNotificationRepository',ScopedPort):
                unauthorized=await client.get('/api/v1/notifications')
                self.assertEqual(unauthorized.status_code,401);self.assertEqual(scopes,[])
                listed=await client.get('/api/v1/notifications',headers=auth)
                self.assertEqual(listed.status_code,200)
                self.assertEqual(listed.json()['data'],{'items':[],'next_cursor':None,'unread_count':0})
                self.assertEqual(len(scopes),1)
                scope=scopes[0]
                self.assertEqual((scope['tenant_id'],scope['recipient_id']),(self.web.tenant_id,self.web.user_id))
                from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
                self.assertIsInstance(scope['studio_approval_repository'], StudioApprovalLifecycleRepository)
                self.assertIs(scope['studio_approval_repository']._store, cloud, '기존 저장소/원장을 재사용한다')
                self.assertEqual(tuple(item.workspace_id for item in scope['contexts']),('workspace-001','workspace-002'))
                self.assertTrue(all(item.actor_id==self.web.user_id and item.tenant_id==self.web.tenant_id and item.capability=='view'
                    for item in scope['contexts']))
                invalid=await client.get('/api/v1/notifications?workspace_id=workspace-foreign',headers=auth)
                self.assertEqual(invalid.status_code,400);self.assertEqual(len(scopes),1)
                original_authorize=self.authorization.authorize_action
                def authorize(**arguments):
                    if arguments['workspace_id']=='workspace-002':
                        raise AuthorizationError('ROLE_NOT_PERMITTED',403)
                    return original_authorize(**arguments)
                with patch.object(self.authorization,'authorize_action',side_effect=authorize):
                    filtered=await client.get('/api/v1/notifications',headers=auth)
                self.assertEqual(filtered.status_code,200)
                self.assertEqual(tuple(item.workspace_id for item in scopes[-1]['contexts']),('workspace-001',))
            with patch.object(cloud,'readiness',return_value=SimpleNamespace(ready=False,schema_revision='0068')):
                unavailable=await client.get('/api/v1/notifications',headers=auth)
                self.assertEqual((unavailable.status_code,unavailable.json()['error']['code'],unavailable.json()['error']['retryable']),
                    (503,'RESOURCE_UNAVAILABLE',True),'구 DB를 Reference 빈 목록으로 바꾸지 않는다')
            with patch.object(cloud,'readiness',return_value=SimpleNamespace(ready=True,schema_revision='0069')),\
                patch('daon_user_api.notification_postgres.ScopedPostgresNotificationRepository',side_effect=NotificationError('DATABASE_UNAVAILABLE',503)):
                unavailable=await client.get('/api/v1/notifications',headers=auth)
                self.assertEqual((unavailable.status_code,unavailable.json()['error']['code']),(503,'RESOURCE_UNAVAILABLE'))

    async def test_s42_notification_runtime_scope_storage_errors_have_safe_retryable_envelope(self) -> None:
        """새 생성 경계의 5xx 변환만 확인한다. DB 오류를 대체한 HTTP 계약이다."""
        from types import SimpleNamespace
        from daon_user_api.cloud_storage import PostgresCloudStore
        from daon_user_api.identity import IdentityError
        self.dependencies.settings=replace(self.settings,notification_inbox_enabled=True)
        cloud=PostgresCloudStore('postgresql://unused.invalid/unused',min_size=0,max_size=1,notification_inbox_enabled=True)
        self.dependencies.cloud_store=cloud
        app=create_app(self.dependencies)
        auth={'cookie':f'{WEB_SESSION_COOKIE}={self.web.access_token}'}
        cases=((self.identity,'notification_workspace_ids',IdentityError('PERSISTENCE_UNAVAILABLE',503)),
            (self.authorization,'authorize_action',AuthorizationError('PERSISTENCE_UNAVAILABLE',503)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            for owner,method,error in cases:
                with self.subTest(stage=method),patch.object(cloud,'readiness',return_value=SimpleNamespace(ready=True,schema_revision='0069')),\
                    patch.object(owner,method,side_effect=error):
                    for endpoint in ('/api/v1/notifications', '/api/v1/inbox'):
                        response=await client.get(endpoint,headers=auth)
                        self.assertEqual(response.status_code,503,endpoint)
                        self.assertEqual((response.json()['error']['code'],response.json()['error']['retryable']),
                            ('RESOURCE_UNAVAILABLE',True))

    def test_s42_notification_runtime_activation_is_explicit_strict_and_default_off(self) -> None:
        import os
        self.assertFalse(self.settings.notification_inbox_enabled)
        with self.assertRaisesRegex(ValueError,'NOTIFICATION_INBOX_FLAG_INVALID'):
            replace(self.settings,notification_inbox_enabled='true')
        with patch.dict(os.environ,{'DAON_RUNTIME_PROFILE':'test','DAON_API_DATABASE_PATH':str(self.db_path)},clear=True):
            self.assertFalse(RuntimeSettings.from_env().notification_inbox_enabled)
            os.environ['DAON_NOTIFICATION_INBOX_ENABLED']='true'
            self.assertTrue(RuntimeSettings.from_env().notification_inbox_enabled)
            os.environ['DAON_NOTIFICATION_INBOX_ENABLED']='1'
            with self.assertRaisesRegex(ValueError,'NOTIFICATION_INBOX_FLAG_INVALID'):
                RuntimeSettings.from_env()

    async def test_s42_context_exact_selector_http_contract(self) -> None:
        """HTTP 인자/오류 계약만 검증한다. 실제 PG 선택 검증은 별도 직접 시험이다."""
        from daon_user_api.notebook import NotebookService, NotebookError
        auth = {"cookie": f"{WEB_SESSION_COOKIE}={self.web.access_token}"}
        created = await self.client.post('/api/v1/workspaces/workspace-001/notebooks',
            headers={**auth, 'Idempotency-Key':'s42-selector-http-0001'}, json={'title':'S42 정확 선택'})
        self.assertEqual(created.status_code, 201)
        url = f"/api/v1/workspaces/workspace-001/notebooks/{created.json()['data']['notebook_id']}/context"
        original = NotebookService.read_selected_context
        received = []
        def read(service, context, notebook_id, **selectors):
            received.append(selectors)
            if selectors.get('run_id') == 'run-failed':
                raise NotebookError('QUESTION_FAILED', 409)
            return original(service, context, notebook_id)
        with patch.object(NotebookService, 'read_selected_context', autospec=True, side_effect=read):
            normal = await self.client.get(url, headers=auth)
            completed = await self.client.get(url+'?run_id=run-completed', headers=auth)
            job = await self.client.get(url+'?studio_job_id=job-failed', headers=auth)
            failed = await self.client.get(url+'?run_id=run-failed', headers=auth)
            self.assertEqual((normal.status_code, completed.status_code, job.status_code), (200, 200, 200))
            self.assertEqual(completed.json()['data'], normal.json()['data'])
            self.assertEqual(job.json()['data'], normal.json()['data'])
            self.assertEqual(received, [{}, {'run_id':'run-completed'}, {'studio_job_id':'job-failed'}, {'run_id':'run-failed'}])
            error = failed.json()['error']
            self.assertEqual((failed.status_code, error['code'], error['retryable'], error['details']),
                (409, 'QUESTION_FAILED', False, {}))
            for query in ('run_id=a&run_id=b', 'studio_job_id=a&studio_job_id=b',
                'run_id=a&studio_job_id=b', 'run_id=', 'studio_job_id=', 'run_id=a&extra=b'):
                response = await self.client.get(url+'?'+query, headers=auth)
                self.assertEqual((response.status_code, response.json()['error']['code']), (400, 'INVALID_REQUEST'), query)
            self.assertEqual(len(received), 4, '잘못된 선택자는 저장소까지 전달하지 않는다')

    async def test_connector_list_contract_returns_safe_views(self) -> None:
        auth = {"cookie": f"{WEB_SESSION_COOKIE}={self.web.access_token}"}
        response = await self.client.get("/api/v1/workspaces/workspace-001/connectors", headers=auth)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"data", "meta"})
        self.assertEqual(set(payload["data"]), {"connectors"})
        self.assertEqual(payload["meta"]["workspace_id"], "workspace-001")
        self.assertEqual(
            {item["connector_id"] for item in payload["data"]["connectors"]},
            {"mcp-open-law-go-kr", "daon-approved-knowledge"},
        )

    async def test_source_upload_rejects_invalid_pdf_and_cross_scope_before_write(self) -> None:
        auth = {
            "cookie": f"{WEB_SESSION_COOKIE}={self.web.access_token}",
            "Content-Type": "application/pdf", "X-Notebook-Id": "notebook-001",
            "Idempotency-Key": "source-upload-invalid-0001",
        }
        invalid_type = await self.client.post(
            "/api/v1/workspaces/workspace-001/sources",
            headers={**auth, "X-Source-Filename": "notes.txt"}, content=b"not pdf",
        )
        self.assertEqual((invalid_type.status_code, invalid_type.json()["error"]["code"]), (415, "UNSUPPORTED_MEDIA_TYPE"))

        cross_scope = await self.client.post(
            "/api/v1/workspaces/workspace-foreign/sources",
            headers={**auth, "X-Source-Filename": "guide.pdf", "X-Notebook-Id": "notebook-foreign"},
            content=b"%PDF-1.4",
        )
        self.assertIn(cross_scope.status_code, {403, 404})
        self.assertNotIn("stack", cross_scope.text.lower())

    async def test_live_ready_and_import_have_no_listener_side_effect(self) -> None:
        live = await self.client.get("/health/live")
        ready = await self.client.get("/health/ready")
        self.assertEqual((live.status_code, ready.status_code), (200, 200))
        self.assertEqual(live.json()["status"], "live")
        self.assertEqual(ready.json()["status"], "ready")
        self.assertNotIn("access-control-allow-origin", live.headers)

    async def test_signup_preserves_the_verification_required_response_contract(self) -> None:
        with patch.object(self.identity, "signup", return_value=None):
            response = await self.client.post(
                "/api/v1/auth/signup",
                json={
                    "login_id": "new-user",
                    "email": "new-user@example.com",
                    "password": "correct horse battery staple",
                },
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["data"], {"status": "verification_required"})

    async def test_first_local_login_bootstraps_and_returns_personal_workspace(self) -> None:
        cloud_store = ScopeRecordingCloudStore()
        self.dependencies.cloud_store = cloud_store  # type: ignore[assignment]
        app = create_app(self.dependencies)
        credentials = replace(
            self.web,
            user_id="user-local-001",
            tenant_id="tenant-local-001",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
        ) as client:
            with patch.object(self.identity, "local_login", return_value=credentials):
                first = await client.post(
                    "/api/v1/auth/login",
                    json={"login_id": "local-user", "password": "valid-password-001"},
                )
                second = await client.post(
                    "/api/v1/auth/login",
                    json={"login_id": "local-user", "password": "valid-password-001"},
                )
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        workspace_id = first.json()["data"]["workspace_id"]
        self.assertEqual(second.json()["data"]["workspace_id"], workspace_id)
        self.assertEqual(
            self.authorization_repository.primary_workspace_id("tenant-local-001"),
            workspace_id,
        )
        self.assertEqual(len(cloud_store.scopes), 2)
        self.assertTrue(all(scope.tenant_id == "tenant-local-001" for scope in cloud_store.scopes))
        self.assertTrue(all(scope.workspace_id == workspace_id for scope in cloud_store.scopes))
        self.assertTrue(all(scope.actor_id == "user-local-001" for scope in cloud_store.scopes))
        self.assertTrue(all(scope.capability == "workspace.bootstrap" for scope in cloud_store.scopes))

    async def test_native_local_login_returns_opaque_credentials_without_cookie_and_rejects_client_kind_overrides(self) -> None:
        class Sender:
            messages: list[dict[str, str]] = []

            def send(self, **message: str) -> None:
                self.messages.append(message)

        self.identity._email_sender = Sender()
        self.identity.signup(
            login_id="native-local-user", email="native-local-user@example.com",
            password="correct horse battery staple", trace_id="trace-native-signup",
            policy_version=POLICY_VERSION,
        )
        sender = self.identity._email_sender
        verification_token = sender.messages[-1]["body"].split(": ", 1)[1].splitlines()[0]
        self.identity.verify_email(
            token=verification_token, trace_id="trace-native-verify",
            policy_version=POLICY_VERSION,
        )

        response = await self.client.post(
            "/api/v1/auth/native/login",
            json={"login_id": "native-local-user", "password": "correct horse battery staple"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get_list("set-cookie"), [])
        payload = response.json()["data"]
        self.assertEqual(payload["client_kind"], "native")
        self.assertEqual(payload["delivery"], "native_https_opaque_bearer")
        self.assertTrue(payload["access_credential"])
        self.assertTrue(payload["refresh_credential"])
        self.assertTrue(payload["workspace_id"])
        self.assertNotIn("password", response.text)

        for body in (
            {"login_id": "native-local-user", "password": "correct horse battery staple", "platform": "web"},
            {"login_id": "native-local-user", "password": "correct horse battery staple", "client_kind": "web"},
        ):
            rejected = await self.client.post("/api/v1/auth/native/login", json=body)
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(rejected.json()["error"]["code"], "INVALID_REQUEST")
        invalid = await self.client.post(
            "/api/v1/auth/native/login",
            json={"login_id": "native-local-user", "password": "wrong password value"},
        )
        self.assertEqual((invalid.status_code, invalid.json()["error"]["code"]), (401, "AUTHENTICATION_REQUIRED"))

    async def test_native_refresh_rotates_once_and_returns_native_credentials_without_cookie(self) -> None:
        with patch.object(self.identity, "rotate_refresh", wraps=self.identity.rotate_refresh) as rotate:
            response = await self.client.post(
                "/api/v1/session/refresh",
                json={"refresh_credential": self.native.refresh_token},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get_list("set-cookie"), [])
        rotate.assert_called_once()
        payload = response.json()["data"]
        self.assertEqual(payload["user_id"], self.native.user_id)
        self.assertEqual(payload["tenant_id"], self.native.tenant_id)
        self.assertEqual(payload["session_id"], self.native.session_id)
        self.assertEqual(payload["device_id"], self.native.device_id)
        self.assertEqual(payload["client_kind"], "native")
        self.assertEqual(payload["delivery"], "native_https_opaque_bearer")
        self.assertTrue(payload["workspace_id"])
        self.assertTrue(payload["access_credential"])
        self.assertTrue(payload["refresh_credential"])
        self.assertNotEqual(payload["refresh_credential"], self.native.refresh_token)

    async def test_current_web_session_logout_is_csrf_bound_idempotent_and_clears_cookie(self) -> None:
        headers = {
            "Content-Type": "application/json",
            "X-Daon-Bff-Transport": "internal",
            "X-Daon-Csrf-Origin": "https://app.example.com",
            "X-Daon-Csrf-Referer": "https://app.example.com/notebooks",
        }
        cookie = {WEB_SESSION_COOKIE: self.web.access_token}
        rejected = await self.client.post(
            "/api/v1/session/logout", headers={"Content-Type": "application/json"},
            cookies=cookie,
        )
        first = await self.client.post(
            "/api/v1/session/logout", headers=headers, cookies=cookie,
            json={},
        )
        replay = await self.client.post(
            "/api/v1/session/logout", headers=headers, cookies=cookie,
        )
        session = await self.client.get("/api/v1/session", cookies=cookie)

        self.assertEqual((rejected.status_code, rejected.json()["error"]["code"]), (403, "CSRF_VALIDATION_FAILED"))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["data"], {"status": "logged_out", "replayed": False})
        self.assertEqual(replay.json()["data"], {"status": "logged_out", "replayed": True})
        self.assertIn(f"{WEB_SESSION_COOKIE}=", first.headers["set-cookie"])
        self.assertIn("Max-Age=0", first.headers["set-cookie"])
        self.assertIn("HttpOnly", first.headers["set-cookie"])
        self.assertIn("Secure", first.headers["set-cookie"])
        self.assertIn("SameSite=lax", first.headers["set-cookie"])
        self.assertEqual((session.status_code, session.json()["error"]["code"]), (401, "AUTHENTICATION_REQUIRED"))
        events = self.audit.list(tenant_id=self.web.tenant_id, limit=200).items
        self.assertEqual(sum(item.action == "identity.session.self_revoked" for item in events), 1)
        self.assertNotIn(self.web.access_token, first.text + replay.text)

    async def test_native_self_logout_revokes_only_presented_session_and_refresh_once(self) -> None:
        headers = {"Authorization": f"Bearer {self.native.access_token}"}
        other = self.identity.begin_oidc_login(
            issuer="https://login.example.com", client_id="daon-native",
            audience="daon-user-api", redirect_uri="com.sinsan.daon:/oidc/callback",
            client_kind=ClientKind.NATIVE, tenant_id="tenant-001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        provider = FakeVerifiedOidcProvider()
        provider.expected_nonce = other.nonce
        second = self.identity.complete_oidc_login(
            state=other.state, authorization_code=provider.authorization_code,
            code_verifier=other.code_verifier, client_id="daon-native",
            redirect_uri="com.sinsan.daon:/oidc/callback", provider=provider,
            platform=DevicePlatform.ANDROID, trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        first = await self.client.post("/api/v1/session/native/logout", headers=headers, json={})
        replay = await self.client.post("/api/v1/session/native/logout", headers=headers)
        denied = await self.client.get("/api/v1/session", headers=headers)
        refresh = await self.client.post(
            "/api/v1/session/refresh", json={"refresh_credential": self.native.refresh_token},
        )
        other_session = await self.client.get(
            "/api/v1/session", headers={"Authorization": f"Bearer {second.access_token}"},
        )
        self.assertEqual((first.status_code, replay.status_code), (200, 200))
        self.assertEqual(first.json()["data"], {"status": "logged_out", "replayed": False})
        self.assertEqual(replay.json()["data"], {"status": "logged_out", "replayed": True})
        self.assertTrue(first.json()["meta"]["trace_id"])
        self.assertEqual(first.headers.get_list("set-cookie"), [])
        self.assertEqual(first.headers["etag"], '"session:logged-out"')
        self.assertEqual(replay.headers["etag"], '"session:logged-out"')
        self.assertEqual((denied.status_code, refresh.status_code, other_session.status_code), (401, 401, 200))
        events = self.audit.list(tenant_id=self.native.tenant_id, limit=200).items
        self.assertEqual(sum(event.action == "identity.session.self_revoked" for event in events), 1)
        self.assertNotIn(self.native.access_token, first.text + replay.text)
        self.assertNotIn(self.native.refresh_token, first.text + replay.text)

    async def test_native_self_logout_rejects_non_native_credentials_and_input(self) -> None:
        path = "/api/v1/session/native/logout"
        bearer = {"Authorization": f"Bearer {self.native.access_token}"}
        cookie = {WEB_SESSION_COOKIE: self.web.access_token}
        for response in (
            await self.client.post(path, cookies=cookie, json={}),
            await self.client.post(path, headers=bearer, cookies=cookie, json={}),
            await self.client.post(path, headers={"Authorization": "Bearer bad"}, json={}),
        ):
            self.assertEqual(response.status_code, 401)
        for response in (
            await self.client.post(path + "?session_id=x", headers=bearer, json={}),
            await self.client.post(path, headers=bearer, json={"session_id": self.native.session_id}),
            await self.client.post(path, headers=bearer, json={"refresh_credential": self.native.refresh_token}),
            await self.client.post(path, headers={**bearer, "Content-Type": "text/plain"}, content="{}"),
        ):
            self.assertEqual(response.status_code, 400)
        self.identity.validate_access(self.native.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        whitespace = await self.client.post(
            path, headers={**bearer, "Content-Type": "application/json"}, content=b"{ }",
        )
        self.assertEqual(whitespace.status_code, 200)

    async def test_native_self_logout_allows_expired_access_without_restoring_access(self) -> None:
        self.clock.advance(hours=2)
        headers = {"Authorization": f"Bearer {self.native.access_token}"}
        expired = await self.client.get("/api/v1/session", headers=headers)
        logout = await self.client.post("/api/v1/session/native/logout", headers=headers, json={})
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.json()["data"], {"status": "logged_out", "replayed": False})

    async def test_native_self_logout_allows_password_change_required(self) -> None:
        with self.identity_repository.transaction() as connection:
            connection.execute("UPDATE users SET password_change_required=1 WHERE user_id=?", (self.native.user_id,))
        response = await self.client.post(
            "/api/v1/session/native/logout",
            headers={"Authorization": f"Bearer {self.native.access_token}"}, json={},
        )
        self.assertEqual(response.status_code, 200)

    async def test_current_web_session_logout_rejects_referer_mismatch_and_input_fields(self) -> None:
        base = {
            "Content-Type": "application/json",
            "X-Daon-Bff-Transport": "internal",
            "X-Daon-Csrf-Origin": "https://app.example.com",
        }
        cookie = {WEB_SESSION_COOKIE: self.web.access_token}
        mismatch = await self.client.post(
            "/api/v1/session/logout",
            headers={**base, "X-Daon-Csrf-Referer": "https://evil.example/notebooks"},
            cookies=cookie,
        )
        input_attempt = await self.client.post(
            "/api/v1/session/logout",
            headers={**base, "X-Daon-Csrf-Referer": "https://app.example.com/notebooks"},
            cookies=cookie, json={"session_id": self.web.session_id},
        )
        self.assertEqual((mismatch.status_code, mismatch.json()["error"]["code"]), (403, "CSRF_VALIDATION_FAILED"))
        self.assertEqual((input_attempt.status_code, input_attempt.json()["error"]["code"]), (400, "INVALID_REQUEST"))
        self.identity.validate_access(self.web.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)

    async def test_current_web_session_logout_accepts_explicit_private_http_gateway(self) -> None:
        self.dependencies.settings = replace(
            self.settings,
            public_gateway_url="http://172.27.253.53:3330",
            allow_private_http_gateway=True,
        )
        response = await self.client.post(
            "/api/v1/session/logout",
            headers={
                "Content-Type": "application/json",
                "X-Daon-Bff-Transport": "internal",
                "X-Daon-Csrf-Origin": "http://172.27.253.53:3330",
                "X-Daon-Csrf-Referer": "http://172.27.253.53:3330/notebooks",
            },
            cookies={WEB_SESSION_COOKIE: self.web.access_token},
            json={},
        )
        self.assertEqual(response.status_code, 200)

    async def test_native_refresh_rejects_extra_invalid_expired_and_replayed_credentials(self) -> None:
        extra = await self.client.post(
            "/api/v1/session/refresh",
            json={"refresh_credential": self.native.refresh_token, "platform": "web"},
        )
        invalid = await self.client.post(
            "/api/v1/session/refresh",
            json={"refresh_credential": "x" * 64},
        )
        self.assertEqual((extra.status_code, extra.json()["error"]["code"]), (400, "INVALID_REQUEST"))
        self.assertEqual((invalid.status_code, invalid.json()["error"]["code"]), (401, "AUTHENTICATION_REQUIRED"))

        first = await self.client.post(
            "/api/v1/session/refresh",
            json={"refresh_credential": self.native.refresh_token},
        )
        replayed = await self.client.post(
            "/api/v1/session/refresh",
            json={"refresh_credential": self.native.refresh_token},
        )
        session_revoked = await self.client.post(
            "/api/v1/session/refresh",
            json={"refresh_credential": first.json()["data"]["refresh_credential"]},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual((replayed.status_code, replayed.json()["error"]["code"]), (401, "REFRESH_REPLAYED"))
        self.assertEqual((session_revoked.status_code, session_revoked.json()["error"]["code"]), (401, "AUTHENTICATION_REQUIRED"))

        start = self.identity.begin_oidc_login(
            issuer="https://login.example.com", client_id="daon-native",
            audience="daon-user-api", redirect_uri="com.sinsan.daon:/oidc/callback",
            client_kind=ClientKind.NATIVE, tenant_id="tenant-001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        provider = FakeVerifiedOidcProvider()
        provider.expected_nonce = start.nonce
        expired_credentials = self.identity.complete_oidc_login(
            state=start.state, authorization_code=provider.authorization_code,
            code_verifier=start.code_verifier, client_id="daon-native",
            redirect_uri="com.sinsan.daon:/oidc/callback", provider=provider,
            platform=DevicePlatform.WINDOWS, trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        self.clock.advance(days=31)
        expired = await self.client.post(
            "/api/v1/session/refresh",
            json={"refresh_credential": expired_credentials.refresh_token},
        )
        self.assertEqual((expired.status_code, expired.json()["error"]["code"]), (401, "AUTHENTICATION_REQUIRED"))

    async def test_cloud_migration_failure_only_drops_readiness(self) -> None:
        self.dependencies.cloud_store = UnreadyCloudStore()  # type: ignore[assignment]
        live = await self.client.get("/health/live")
        ready = await self.client.get("/health/ready")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json(), {"status": "not_ready"})

    async def test_slow_cloud_readiness_does_not_block_live_event_loop(self) -> None:
        self.dependencies.cloud_store = SlowUnreadyCloudStore()  # type: ignore[assignment]
        started = time.perf_counter()
        ready_task = asyncio.create_task(self.client.get("/health/ready"))
        await asyncio.sleep(0.01)
        live = await self.client.get("/health/live")
        live_elapsed = time.perf_counter() - started
        ready = await ready_task
        self.assertEqual((live.status_code, ready.status_code), (200, 503))
        self.assertLess(live_elapsed, 0.15)

    async def test_object_outage_drops_ready_but_keeps_live(self) -> None:
        self.dependencies.object_storage = UnreadyObjectStorage()  # type: ignore[assignment]
        live, ready = await asyncio.gather(
            self.client.get("/health/live"), self.client.get("/health/ready")
        )
        self.assertEqual((live.status_code, ready.status_code), (200, 503))
        self.assertEqual(ready.json(), {"status": "not_ready"})

    async def test_web_cookie_and_native_bearer_return_same_session_meaning(self) -> None:
        web = await self.client.get(
            "/api/v1/session", cookies={WEB_SESSION_COOKIE: self.web.access_token}
        )
        native = await self.client.get(
            "/api/v1/session", headers={"Authorization": f"Bearer {self.native.access_token}"}
        )
        self.assertEqual((web.status_code, native.status_code), (200, 200))
        self.assertEqual(web.json()["data"]["delivery"], "same_origin_secure_cookie")
        self.assertEqual(native.json()["data"]["delivery"], "native_https_opaque_bearer")
        self.assertEqual(web.json()["data"]["user_id"], native.json()["data"]["user_id"])
        self.assertRegex(web.json()["data"]["expires_at"], r"Z$")
        self.assertRegex(native.json()["data"]["expires_at"], r"Z$")
        joined = f"{web.text}{native.text}"
        self.assertNotIn(self.web.access_token, joined)
        self.assertNotIn(self.native.access_token, joined)

    async def test_session_projects_only_current_workspace_recovery_operations(self) -> None:
        response = await self.client.get(
            "/api/v1/session",
            headers={
                "Authorization": f"Bearer {self.native.access_token}",
                "X-Workspace-Id": "workspace-foreign",
                "X-Role": "viewer",
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["workspace_id"], "workspace-001")
        self.assertEqual(
            data["recovery_operations"],
            [
                "cloud_backup_create",
                "cloud_backup_get",
                "cloud_backup_list",
                "cloud_restore_cancel",
                "cloud_restore_execute",
                "cloud_restore_get",
                "cloud_restore_preview",
            ],
        )
        self.assertEqual(data["recovery_operations"], sorted(set(data["recovery_operations"])))
        forbidden = {
            "role", "role_scope", "effective_permissions", "permission",
            "policy_denial_reason", "access_credential", "refresh_credential",
        }
        self.assertTrue(forbidden.isdisjoint(data))

    async def test_session_permission_denial_is_an_authenticated_empty_projection(self) -> None:
        denied = AuthorizationError("ACTION_DENIED", 403)
        with patch.object(self.authorization, "authorize_action", side_effect=denied):
            response = await self.client.get(
                "/api/v1/session",
                headers={"Authorization": f"Bearer {self.native.access_token}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["recovery_operations"], [])
        self.assertNotIn("ACTION_DENIED", response.text)
        self.assertNotIn("role", response.text.lower())
        self.assertNotIn("permission", response.text.lower())

    async def test_session_does_not_collapse_authorization_failures_into_denial(self) -> None:
        for internal_code in ("AUDIT_WRITE_FAILED", "AUTHORIZATION_STORE_UNAVAILABLE"):
            with self.subTest(internal_code=internal_code), patch.object(
                self.authorization,
                "authorize_action",
                side_effect=AuthorizationError(internal_code, 503),
            ):
                response = await self.client.get(
                    "/api/v1/session",
                    headers={"Authorization": f"Bearer {self.native.access_token}"},
                )

            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")
            self.assertTrue(response.json()["error"]["retryable"])

    async def test_authorization_uses_repository_identity_and_rejects_spoofed_claims(self) -> None:
        trace_id = "trace-runtime-authorize-001"
        response = await self.client.post(
            "/api/v1/workspaces/workspace-001/authorization/evaluations",
            cookies={WEB_SESSION_COOKIE: self.web.access_token},
            headers={
                "X-Trace-Id": trace_id,
                "X-Tenant-Id": "tenant-foreign",
                "X-Role": "viewer",
                "Content-Type": "application/json",
                "Idempotency-Key": "idem-runtime-001",
            },
            json={"action": Action.POLICY_MANAGE.value, "requested_permissions": []},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["role"], Role.ORGANIZATION_ADMIN.value)
        self.assertEqual(
            response.json()["data"]["required_step_up_action"],
            "organization_security_or_connector_policy_change",
        )
        self.assertEqual(response.json()["trace_id"], trace_id)
        self.assertEqual(response.headers["x-trace-id"], trace_id)
        events = self.audit.list(tenant_id=self.web.tenant_id, trace_id=trace_id).items
        self.assertTrue(any(event.action == "authorization.action.allowed" for event in events))

    async def test_access_decision_and_audit_routes_call_current_domain_cores(self) -> None:
        decision = await self.client.post(
            "/api/v1/access-decisions",
            cookies={WEB_SESSION_COOKIE: self.web.access_token},
            headers={"Content-Type": "application/json", "Idempotency-Key": "idem-access-001"},
            json={"resource_id": "result-001", "action": AccessAction.READ.value},
        )
        self.assertEqual(decision.status_code, 201)
        self.assertEqual(decision.json()["data"]["state"], "available")
        audit = await self.client.get(
            "/api/v1/audit-events?tenant_id=tenant-001&workspace_id=workspace-001&limit=50",
            cookies={WEB_SESSION_COOKIE: self.web.access_token},
        )
        self.assertEqual(audit.status_code, 200)
        self.assertGreaterEqual(len(audit.json()["data"]["items"]), 1)
        self.assertNotIn("access_digest", audit.text)
        tenant_audit = await self.client.get(
            "/api/v1/audit-events?tenant_id=tenant-001&limit=50",
            cookies={WEB_SESSION_COOKIE: self.web.access_token},
        )
        self.assertEqual(tenant_audit.status_code, 200)

    async def test_foreign_and_missing_workspace_are_indistinguishable(self) -> None:
        results = []
        for workspace_id in ("workspace-foreign", "workspace-missing"):
            response = await self.client.post(
                f"/api/v1/workspaces/{workspace_id}/authorization/evaluations",
                cookies={WEB_SESSION_COOKIE: self.web.access_token},
                headers={"Content-Type": "application/json", "Idempotency-Key": "idem-safe-404"},
                json={"action": Action.VIEW.value, "requested_permissions": []},
            )
            results.append((response.status_code, response.json()["error"]["code"]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], (404, "RESOURCE_UNAVAILABLE"))

    async def test_traceparent_is_inherited_and_invalid_trace_is_replaced(self) -> None:
        inherited = await self.client.get(
            "/api/v1/session",
            cookies={WEB_SESSION_COOKIE: self.web.access_token},
            headers={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
        )
        self.assertEqual(inherited.headers["x-trace-id"], "4bf92f3577b34da6a3ce929d0e0e4736")
        replaced = await self.client.get(
            "/api/v1/session",
            cookies={WEB_SESSION_COOKIE: self.web.access_token},
            headers={"X-Trace-Id": "bad trace with spaces"},
        )
        self.assertEqual(replaced.status_code, 200)
        self.assertRegex(replaced.headers["x-trace-id"], r"^trace-[A-Za-z0-9_-]{20,}$")

    async def test_request_boundaries_and_safe_errors(self) -> None:
        cases = [
            await self.client.post(
                "/api/v1/access-decisions", content=b"{",
                cookies={WEB_SESSION_COOKIE: self.web.access_token},
                headers={"Content-Type": "application/json", "Idempotency-Key": "idem-json"},
            ),
            await self.client.post(
                "/api/v1/access-decisions", content=b"{}",
                cookies={WEB_SESSION_COOKIE: self.web.access_token},
                headers={"Content-Type": "text/plain", "Idempotency-Key": "idem-type"},
            ),
            await self.client.post(
                "/api/v1/access-decisions", content=b"x" * (self.settings.max_body_bytes + 1),
                cookies={WEB_SESSION_COOKIE: self.web.access_token},
                headers={"Content-Type": "application/json", "Idempotency-Key": "idem-large"},
            ),
            await self.client.delete("/api/v1/session"),
            await self.client.get(
                "/api/v1/session",
                headers={"X-Oversized": "x" * self.settings.max_header_bytes},
            ),
        ]
        self.assertEqual([item.status_code for item in cases], [400, 415, 413, 405, 431])
        for response in cases:
            error = response.json()["error"]
            self.assertEqual(error["trace_id"], response.headers["x-trace-id"])
            self.assertNotIn("traceback", response.text.lower())
            self.assertNotIn(str(self.db_path), response.text)

    async def test_request_timeout_cancels_work_and_returns_safe_error(self) -> None:
        timed_dependencies = RuntimeDependencies(
            settings=replace(self.settings, request_timeout_seconds=0.01),
            identity_service=self.identity,
            authorization_service=self.authorization,
            audit_store=self.audit,
            identity_repository=self.identity_repository,
            authorization_repository=self.authorization_repository,
        )
        timed_app = create_app(timed_dependencies)
        cancelled = False

        @timed_app.get("/test-only-slow")
        async def slow() -> dict[str, bool]:
            nonlocal cancelled
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled = True
                raise
            return {"completed": True}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=timed_app),
            base_url="http://" + "127.0.0.1",
        ) as client:
            response = await client.get("/test-only-slow")
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "REQUEST_TIMEOUT")
        self.assertTrue(cancelled)

    async def test_question_route_waits_past_normal_budget_and_preserves_task_cancellation(self) -> None:
        dependencies=RuntimeDependencies(settings=replace(self.settings,request_timeout_seconds=0.005),
            identity_service=self.identity,authorization_service=self.authorization,audit_store=self.audit,
            identity_repository=self.identity_repository,authorization_repository=self.authorization_repository)
        app=create_app(dependencies);started=asyncio.Event();finished=asyncio.Event();cancel=False
        @app.post('/api/v1/workspaces/test/questions')
        async def fixture_question():
            started.set()
            try:
                if cancel:await asyncio.Event().wait()
                else:await asyncio.sleep(0.02)
                return {'completed':True}
            finally:finished.set()
        # A test-only handler exercises the real timed route, not auth/model success.
        app.router.routes.insert(0,app.router.routes.pop())
        budgets=[];original_timeout=asyncio.timeout
        def timeout_scope(value):budgets.append(value);return original_timeout(value)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            with patch('daon_user_api.runtime.asyncio.timeout',side_effect=timeout_scope):
                result=await client.post('/api/v1/workspaces/test/questions',json={})
                self.assertEqual(result.status_code,200);self.assertEqual(result.json(),{'completed':True})
                cancel=True;started.clear();finished.clear()
                task=asyncio.create_task(client.post('/api/v1/workspaces/test/questions',json={}))
                await started.wait();task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertTrue(finished.is_set())
        self.assertEqual(budgets,[None,None])

    async def test_shutdown_drops_ready_and_rejects_new_business_requests(self) -> None:
        self.dependencies.state.begin_shutdown()
        ready = await self.client.get("/health/ready")
        business = await self.client.get(
            "/api/v1/session", cookies={WEB_SESSION_COOKIE: self.web.access_token}
        )
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(business.status_code, 503)
        self.assertEqual(business.json()["error"]["code"], "SHUTTING_DOWN")


class RuntimeSettingsTests(unittest.TestCase):
    def test_s39_flag_defaults_off_and_rejects_non_boolean_environment(self) -> None:
        with patch.dict("os.environ", {"DAON_RUNTIME_PROFILE": "development"}, clear=True):
            self.assertFalse(RuntimeSettings.from_env().organization_admin_v2_enabled)
        for value, expected in (("true", True), ("false", False)):
            with patch.dict("os.environ", {"DAON_ORGANIZATION_ADMIN_V2_ENABLED": value}, clear=True):
                self.assertIs(RuntimeSettings.from_env().organization_admin_v2_enabled, expected)
        for value in ("1", "yes", "TRUE", "", " false "):
            with patch.dict("os.environ", {"DAON_ORGANIZATION_ADMIN_V2_ENABLED": value}, clear=True):
                with self.assertRaisesRegex(ValueError, "ORGANIZATION_ADMIN_V2_FLAG_INVALID"):
                    RuntimeSettings.from_env()

    def test_only_exact_question_post_has_no_wait_limit(self) -> None:
        settings = RuntimeSettings.for_test(database_path=Path("runtime.sqlite3"), policy_version="policy-v1")
        self.assertIsNone(request_timeout_for_path(settings, "/api/v1/workspaces/workspace-a/questions",method='POST'))
        self.assertEqual(request_timeout_for_path(settings, "/api/v1/workspaces/workspace-a/questions",method='GET'),30.0)
        self.assertEqual(request_timeout_for_path(settings, "/api/v1/workspaces/workspace-a/questions/authorization",method='POST'),30.0)
        self.assertEqual(request_timeout_for_path(settings, "/api/v1/workspaces/workspace-a/connectors/mcp-open-law-go-kr/law-search",method='POST'),25.0)
        self.assertEqual(request_timeout_for_path(settings, "/api/v1/session"), 30.0)

    def test_object_storage_requires_complete_secret_references_and_build_is_lazy(self) -> None:
        with self.assertRaises(ValueError):
            RuntimeSettings(
                profile="development", bind_host="127.0.0.1", port=8000,
                object_storage_endpoint="object.internal:9000",
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            access = root / "access-key"
            secret = root / "secret-key"
            access.write_text("access-reference-value", encoding="utf-8")
            secret.write_text("secret-reference-value", encoding="utf-8")
            settings = RuntimeSettings(
                profile="development", bind_host="127.0.0.1", port=8000,
                database_path=root / "runtime.sqlite3",
                object_storage_endpoint="object.internal:9000", object_storage_bucket="daon-r1-test",
                object_access_key_file=access, object_secret_key_file=secret, object_storage_secure=False,
            )
            dependencies = build_dependencies(settings)
            self.assertIsNotNone(dependencies.object_storage)
            dependencies.close()

    def test_unavailable_cloud_database_does_not_block_dependency_build(self) -> None:
        unavailable_dsn = "postgresql://app@" + "127.0.0.1" + ":1/unavailable?connect_timeout=1"
        with tempfile.TemporaryDirectory() as directory:
            settings = RuntimeSettings(
                profile="development",
                bind_host="127.0.0.1",
                port=8000,
                database_path=Path(directory) / "runtime.sqlite3",
                cloud_database_dsn=unavailable_dsn,
            )
            started = time.perf_counter()
            dependencies = build_dependencies(settings)
            self.assertLess(time.perf_counter() - started, 1.0)
            dependencies.close()

    def test_plaintext_and_proxy_boundaries_fail_close(self) -> None:
        with self.assertRaises(ValueError):
            RuntimeSettings(profile="development", bind_host="0.0.0.0", port=8000)
        with self.assertRaises(ValueError):
            RuntimeSettings(
                profile="production", bind_host="0.0.0.0", port=8000,
                public_gateway_url="http://api.example.com",
                trusted_proxy_ips=("10.0.0.1",),
            )
        with self.assertRaises(ValueError):
            RuntimeSettings(
                profile="production", bind_host="0.0.0.0", port=8000,
                public_gateway_url="https://api.example.com",
                trusted_proxy_ips=("10.0.0.1",),
            )
        with tempfile.TemporaryDirectory() as directory:
            step_up_key = Path(directory) / "step-up.key"
            step_up_key.write_bytes(b"s" * 32)
            valid = RuntimeSettings(
                profile="production", bind_host="0.0.0.0", port=8000,
                public_gateway_url="https://api.example.com",
                trusted_proxy_ips=("10.0.0.1",),
                cloud_database_dsn="postgresql://app@database/daon",
                step_up_token_key_file=step_up_key,
            )
            self.assertEqual(valid.public_gateway_url, "https://api.example.com")

            private_http = RuntimeSettings(
                profile="production", bind_host="0.0.0.0", port=8000,
                public_gateway_url="http://172.27.253.53:3330",
                allow_private_http_gateway=True,
                trusted_proxy_ips=("10.0.0.1",),
                cloud_database_dsn="postgresql://app@database/daon",
                step_up_token_key_file=step_up_key,
            )
            self.assertTrue(private_http.allow_private_http_gateway)
            for invalid in (
                "http://8.8.8.8:3330",
                "http://0.0.0.0:3330",
                "http://daon-user.sinsan.kr:3330",
                "http://172.27.253.53",
            ):
                with self.assertRaises(ValueError):
                    RuntimeSettings(
                        profile="production", bind_host="0.0.0.0", port=8000,
                        public_gateway_url=invalid,
                        allow_private_http_gateway=True,
                        trusted_proxy_ips=("10.0.0.1",),
                        cloud_database_dsn="postgresql://app@database/daon",
                        step_up_token_key_file=step_up_key,
                    )


if __name__ == "__main__":
    unittest.main()
