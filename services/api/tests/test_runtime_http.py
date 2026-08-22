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


class UnreadyObjectStorage:
    def health(self) -> bool:
        return False


class RuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertEqual((invalid_type.status_code, invalid_type.json()["error"]["code"]), (400, "SOURCE_FILENAME_INVALID"))

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
        credentials = replace(
            self.web,
            user_id="user-local-001",
            tenant_id="tenant-local-001",
        )
        with patch.object(self.identity, "local_login", return_value=credentials):
            first = await self.client.post(
                "/api/v1/auth/login",
                json={"login_id": "local-user", "password": "valid-password-001"},
            )
            second = await self.client.post(
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
    def test_grounded_question_only_gets_bounded_model_timeout(self) -> None:
        settings = RuntimeSettings.for_test(database_path=Path("runtime.sqlite3"), policy_version="policy-v1")
        self.assertEqual(request_timeout_for_path(settings, "/api/v1/workspaces/workspace-a/questions"), 95.0)
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


if __name__ == "__main__":
    unittest.main()
