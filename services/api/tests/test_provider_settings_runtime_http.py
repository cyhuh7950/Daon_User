from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import ClientKind, DevicePlatform
from daon_user_api.operations_status import (
    OperationsComponent,
    OperationsStatusView,
)
from daon_user_api.output_version_settings import (
    DEFAULT_OUTPUT_FORMATS,
    OutputVersionSettingsService,
    ReferenceOutputVersionSettingsRepository,
)
from daon_user_api.provider_settings import (
    ProviderSettingsService,
    ReferenceProviderSettingsRepository,
    ServerCredentialPresenceResolver,
    ProviderConnectionStatus,
)
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app
from test_identity_support import FakeVerifiedOidcProvider, POLICY_VERSION, TRACE_ID, create_service


class ProviderSettingsRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "runtime.sqlite3"
        self.audit = AuditEventStore()
        self.identity, self.identity_repository, _, self.clock = create_service(
            self.db_path, audit_store=self.audit
        )
        login = self.identity.begin_oidc_login(
            issuer="https://login.example.com", client_id="daon-web",
            audience="daon-user-api", redirect_uri="https://app.example.com/auth/callback",
            client_kind=ClientKind.WEB, tenant_id="tenant-001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        provider = FakeVerifiedOidcProvider()
        provider.expected_nonce = login.nonce
        self.credentials = self.identity.complete_oidc_login(
            state=login.state, authorization_code=provider.authorization_code,
            code_verifier=login.code_verifier, client_id="daon-web",
            redirect_uri="https://app.example.com/auth/callback", provider=provider,
            platform=DevicePlatform.WEB, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        self.workspace_id = "workspace-provider-http"
        self.authorization_repository = SqliteAuthorizationRepository(self.db_path)
        self.authorization_repository.bootstrap_workspace(
            tenant_id=self.credentials.tenant_id, workspace_id=self.workspace_id,
            owner_user_id=self.credentials.user_id, owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=1000,
            now=self.clock(),
        )
        self.authorization = AuthorizationService(
            repository=self.authorization_repository, audit_store=self.audit,
            clock=self.clock, identity_service=self.identity,
        )
        class Checker:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def check(self, profile, credential):
                self.calls.append(profile.provider_code)
                return ProviderConnectionStatus(
                    profile.provider_code, "ready", "2026-08-14T00:00:00Z",
                )

        self.connection_checker = Checker()
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=self.db_path, policy_version=POLICY_VERSION),
            identity_service=self.identity, authorization_service=self.authorization,
            audit_store=self.audit, identity_repository=self.identity_repository,
            authorization_repository=self.authorization_repository,
            provider_settings_service=ProviderSettingsService(
                ReferenceProviderSettingsRepository(), ServerCredentialPresenceResolver(),
                self.connection_checker,
            ),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    async def test_provider_deployment_and_role_binding_flow_never_returns_key_value(self) -> None:
        secret = "runtime-secret-must-stay-server-side"
        with patch.dict(os.environ, {"UPSTAGE_API_KEY": secret}, clear=False):
            profiles = await self.client.get(
                "/api/v1/model-profiles", params={"workspace_id": self.workspace_id}
            )
            self.assertEqual(profiles.status_code, 200)
            self.assertEqual(len(profiles.json()["data"]), 9)
            upstage = next(item for item in profiles.json()["data"] if item["provider_code"] == "UPSTAGE")
            self.assertTrue(upstage["credential_configured"])
            self.assertNotIn(secret, profiles.text)

            profile = await self.client.post(
                "/api/v1/model-profiles", headers={"Idempotency-Key": "idem-provider-upstage"},
                json={"workspace_id": self.workspace_id, "provider_code": "UPSTAGE",
                      "base_url": "https://api.upstage.ai/v1", "active": True,
                      "expected_version": 0},
            )
            self.assertEqual(profile.status_code, 201)
            self.assertNotIn(secret, profile.text)
            connection = await self.client.get(
                "/api/v1/model-profiles/UPSTAGE/connection-check",
                params={"workspace_id": self.workspace_id},
            )
            self.assertEqual(connection.status_code, 200)
            self.assertEqual(connection.json()["data"], {
                "provider_code": "UPSTAGE", "status": "ready",
                "checked_at": "2026-08-14T00:00:00Z",
            })
            self.assertIn("etag", connection.headers)
            self.assertNotIn(secret, connection.text)
            self.assertNotIn("api.upstage.ai", connection.text)
            deployment = await self.client.post(
                "/api/v1/model-deployments", headers={"Idempotency-Key": "idem-deployment-upstage"},
                json={"workspace_id": self.workspace_id,
                      "deployment_id": "deployment-upstage-solar", "provider_code": "UPSTAGE",
                      "model_id": "solar-pro3", "roles": ["text", "vision"],
                      "active": True, "selected": True, "expected_version": 0},
            )
            self.assertEqual(deployment.status_code, 201)
            policy = await self.client.get(f"/api/v1/workspaces/{self.workspace_id}/model-policy")
            updated = await self.client.patch(
                f"/api/v1/workspaces/{self.workspace_id}/model-policy",
                headers={"If-Match": policy.headers["etag"], "Idempotency-Key": "idem-policy-upstage"},
                json={"bindings": {"text": "deployment-upstage-solar",
                                   "vision": "deployment-upstage-solar"},
                      "expected_version": 0},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["data"]["bindings"]["vision"], "deployment-upstage-solar")
            self.assertNotIn(secret, updated.text)

    async def test_provider_connection_check_is_safe_and_requires_configuration(self) -> None:
        missing = await self.client.get(
            "/api/v1/model-profiles/GROQ/connection-check",
            params={"workspace_id": self.workspace_id},
        )
        self.assertEqual(missing.status_code, 409)
        self.assertEqual(missing.json()["error"]["code"], "PROVIDER_PROFILE_REQUIRED")

    async def test_workspace_operations_status_returns_five_safe_components(self) -> None:
        class OperationsService:
            def __init__(self) -> None:
                self.calls = []

            def read(self, context, **signals):
                self.calls.append((context, signals))
                return OperationsStatusView(
                    context.workspace_id,
                    "warning",
                    "2026-08-15T00:00:00Z",
                    (
                        OperationsComponent("provider", "ready", "PROVIDER_READY", 0, "none"),
                        OperationsComponent("api", "ready", "API_READY", 0, "none"),
                        OperationsComponent("storage", "ready", "STORAGE_READY", 0, "none"),
                        OperationsComponent("sync", "warning", "SYNC_PENDING", 2, "open_sync_settings"),
                        OperationsComponent("queue", "warning", "QUEUE_ATTENTION_REQUIRED", 3, "refresh_status"),
                    ),
                )

        service = OperationsService()
        self.dependencies.operations_status_service = service
        await self.client.aclose()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )
        response = await self.client.get(
            f"/api/v1/workspaces/{self.workspace_id}/operations/status"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["workspace_id"], self.workspace_id)
        self.assertEqual(
            [item["component_id"] for item in data["components"]],
            ["provider", "api", "storage", "sync", "queue"],
        )
        self.assertEqual(data["components"][3]["pending_count"], 2)
        self.assertNotIn("http://", response.text)
        self.assertNotIn("postgres", response.text.lower())
        self.assertEqual(service.calls[0][0].workspace_id, self.workspace_id)

    async def test_workspace_output_version_settings_get_save_replay_and_stale_guard(self) -> None:
        self.dependencies.output_version_settings_service = OutputVersionSettingsService(
            ReferenceOutputVersionSettingsRepository()
        )
        await self.client.aclose()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )
        path = f"/api/v1/workspaces/{self.workspace_id}/output-version-settings"
        initial = await self.client.get(path)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["data"]["default_formats"], DEFAULT_OUTPUT_FORMATS)
        self.assertEqual(initial.json()["data"]["version_save_mode"], "append_only")
        self.assertEqual(initial.headers["etag"], f'"output-version-settings:{self.workspace_id}:0"')

        formats = {**DEFAULT_OUTPUT_FORMATS, "evidence_report": "docx"}
        headers = {
            "If-Match": initial.headers["etag"],
            "Idempotency-Key": "output-settings-save-0001",
        }
        saved = await self.client.patch(
            path, headers=headers, json={"default_formats": formats, "expected_version": 0},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["data"]["default_formats"], formats)
        self.assertEqual(saved.json()["data"]["version"], 1)
        replay = await self.client.patch(
            path, headers=headers, json={"default_formats": formats, "expected_version": 0},
        )
        self.assertEqual(replay.status_code, 200)
        stale = await self.client.patch(
            path,
            headers={**headers, "Idempotency-Key": "output-settings-save-0002"},
            json={"default_formats": formats, "expected_version": 0},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "VERSION_CONFLICT")

    async def test_workspace_model_defaults_get_and_patch_use_safe_projection_and_etag(self) -> None:
        class WorkspaceDefaultsService:
            def __init__(self) -> None:
                self.calls = []
                self.result = {
                    "workspace_id": self_workspace_id,
                    "available_models": [{
                        "connection_id": "ollama-lan",
                        "provider_code": "OLLAMA",
                        "display_name": "사내 Ollama",
                        "configured": False,
                        "credential_version": 0,
                        "verification_status": "verified",
                        "connection_version": 3,
                        "model_id": "qwen3:8b",
                        "effective_capabilities": ["text_generation"],
                        "catalog_status": "ready",
                        "catalog_version": 2,
                    }],
                    "defaults": [],
                    "version": 0,
                    "etag": '"workspace-model-defaults-test-v0"',
                }

            def read(self, context):
                self.calls.append(("read", context))
                return self.result

            def save(self, context, **values):
                self.calls.append(("save", context, values))
                self.result = {
                    **self.result,
                    "defaults": [{
                        "capability": values["capability"],
                        "connection_id": values["connection_id"],
                        "model_id": values["model_id"],
                        "version": 1,
                    }],
                    "version": 1,
                    "etag": '"workspace-model-defaults-test-v1"',
                }
                return self.result, False

        self_workspace_id = self.workspace_id
        service = WorkspaceDefaultsService()
        self.dependencies.workspace_model_defaults_service = service
        await self.client.aclose()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )
        path = f"/api/v1/workspaces/{self.workspace_id}/model-defaults"
        initial = await self.client.get(path)
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(initial.headers["etag"], '"workspace-model-defaults-test-v0"')
        self.assertNotIn("base_url", initial.text)
        self.assertNotIn("credential_digest", initial.text)
        self.assertNotIn("etag", initial.json()["data"])

        saved = await self.client.patch(
            path,
            headers={
                "If-Match": initial.headers["etag"],
                "Idempotency-Key": "workspace-model-default-save-0001",
            },
            json={
                "capability": "text_generation",
                "connection_id": "ollama-lan",
                "model_id": "qwen3:8b",
                "expected_version": 0,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.headers["etag"], '"workspace-model-defaults-test-v1"')
        self.assertEqual(saved.json()["data"]["defaults"][0]["model_id"], "qwen3:8b")
        self.assertFalse(saved.json()["meta"]["replayed"])
        operation = service.calls[-1][2]
        self.assertEqual(operation["expected_etag"], initial.headers["etag"])
        self.assertEqual(operation["idempotency_key"], "workspace-model-default-save-0001")

    async def test_workspace_admin_can_read_but_cannot_manage_system_provider_connections(self) -> None:
        class SafeProviderService:
            def list_connections(self, context):
                return [{
                    "connection_id": "shared-ollama", "provider_code": "OLLAMA",
                    "display_name": "공유 Ollama", "enabled": True, "configured": True,
                    "credential_version": 1, "version": 1, "verification_status": "verified",
                    "verified_at": "2026-09-18T00:00:00Z", "catalog_status": "ready",
                    "catalog_version": 1, "models": [],
                }]

        self.dependencies.provider_connection_service = SafeProviderService()
        await self.client.aclose()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )
        listed = await self.client.get("/api/v1/admin/provider-connections")
        created = await self.client.post(
            "/api/v1/admin/provider-connections",
            headers={"Idempotency-Key": "provider-create-denied-0001"},
            json={
                "connection_id": "ollama-lan", "provider_code": "OLLAMA",
                "display_name": "LAN Ollama", "base_url": "http://ollama.internal:11434",
                "enabled": True, "expected_version": 0,
                "step_up_authorization_id": "not-a-grant",
            },
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(created.status_code, 403)

    async def test_authenticated_workspace_user_can_rotate_shared_provider_credential(self) -> None:
        class SafeProviderService:
            def replace_credential(self, context, connection_id, body, idempotency_key):
                return {
                    "connection_id": connection_id, "provider_code": "OLLAMA",
                    "display_name": "공유 Ollama", "enabled": True, "configured": True,
                    "credential_version": 2, "version": 2,
                    "verification_status": "verified", "verified_at": "2026-09-18T00:00:00Z",
                    "catalog_status": "ready", "catalog_version": 1, "models": [],
                }, False

        self.dependencies.provider_connection_service = SafeProviderService()
        self.dependencies.settings = RuntimeSettings.for_test(
            database_path=self.db_path, policy_version=POLICY_VERSION,
            system_admin_user_ids=frozenset(),
        )
        await self.client.aclose()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )
        grant = self.identity.issue_step_up(
            access_token=self.credentials.access_token,
            action_group="organization_security_or_connector_policy_change",
            target_id="provider-connection:shared-ollama",
            policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        )
        response = await self.client.post(
            "/api/v1/admin/provider-connections/shared-ollama/credential",
            headers={"Idempotency-Key": "provider-credential-replace-user-0001"},
            json={
                "credential": "user-rotated-secret",
                "expected_version": 1,
                "step_up_authorization_id": grant.authorization,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("user-rotated-secret", response.text)

    async def test_system_admin_connection_crud_is_step_up_versioned_and_secret_safe(self) -> None:
        class ProviderAdminService:
            def __init__(self) -> None:
                self.item = None
                self.deleted_replay = None

            def list_connections(self, context):
                return [] if self.item is None else [self.item]

            def create_connection(self, context, body, idempotency_key):
                if self.item is not None:
                    return self.item, True
                self.item = {
                    "connection_id": body.connection_id, "provider_code": body.provider_code,
                    "display_name": body.display_name, "enabled": body.enabled,
                    "configured": body.credential is not None, "credential_version": 1,
                    "version": 1, "verification_status": "verified",
                    "verified_at": "2026-09-16T00:00:00Z", "catalog_status": "ready",
                    "catalog_version": 1, "models": [],
                }
                return self.item, False

            def update_connection(self, context, connection_id, body, idempotency_key):
                if body.expected_version != self.item["version"]:
                    from daon_user_api.runtime import ProviderConnectionAdminError
                    raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
                self.item = {**self.item, "display_name": body.display_name,
                             "enabled": body.enabled, "version": body.expected_version + 1}
                return self.item, False

            def delete_connection(self, context, connection_id, expected_version, idempotency_key):
                if self.deleted_replay is not None:
                    return self.deleted_replay, True
                if expected_version != self.item["version"]:
                    from daon_user_api.runtime import ProviderConnectionAdminError
                    raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
                self.item = {
                    **self.item, "configured": False,
                    "credential_version": self.item["credential_version"] + 1,
                    "verification_status": "unverified", "verified_at": None,
                    "version": expected_version + 1,
                }
                self.deleted_replay = self.item
                return self.item, False

            def refresh_catalog(self, context, connection_id, expected_version, idempotency_key):
                self.item = {**self.item, "version": expected_version + 1, "catalog_version": 2}
                return self.item, False

            def correct_capabilities(self, context, connection_id, model_id, body, idempotency_key):
                return {"connection_id": connection_id, "model_id": model_id,
                        "reported_capabilities": ["text_generation"],
                        "effective_capabilities": body.effective_capabilities,
                        "override_applied": True, "catalog_version": body.expected_version + 1}, False

        service = ProviderAdminService()
        self.dependencies.provider_connection_service = service
        self.dependencies.settings = RuntimeSettings.for_test(
            database_path=self.db_path, policy_version=POLICY_VERSION,
            system_admin_user_ids=frozenset({self.credentials.user_id}),
        )
        await self.client.aclose()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )

        secret = "raw-provider-secret-must-not-return"
        create_step = self.identity.issue_step_up(
            access_token=self.credentials.access_token,
            action_group="organization_security_or_connector_policy_change",
            target_id="provider-connection:ollama-lan",
            policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        )
        created = await self.client.post(
            "/api/v1/admin/provider-connections",
            headers={"Idempotency-Key": "provider-create-admin-0001"},
            json={
                "connection_id": "ollama-lan", "provider_code": "OLLAMA",
                "display_name": "LAN Ollama", "base_url": "http://ollama.internal:11434",
                "credential": secret, "enabled": True, "expected_version": 0,
                "step_up_authorization_id": create_step.authorization,
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertIn("etag", created.headers)
        self.assertNotIn(secret, created.text)
        self.assertNotIn("ollama.internal", created.text)
        self.assertTrue(created.json()["data"]["configured"])
        replay = await self.client.post(
            "/api/v1/admin/provider-connections",
            headers={"Idempotency-Key": "provider-create-admin-0001"},
            json={
                "connection_id": "ollama-lan", "provider_code": "OLLAMA",
                "display_name": "LAN Ollama", "base_url": "http://ollama.internal:11434",
                "credential": secret, "enabled": True, "expected_version": 0,
                "step_up_authorization_id": create_step.authorization,
            },
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.headers["etag"], created.headers["etag"])
        self.assertTrue(replay.json()["meta"]["replayed"])
        self.assertNotIn(secret, replay.text)

        listed = await self.client.get("/api/v1/admin/provider-connections")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn("etag", listed.headers)
        self.assertNotIn(secret, listed.text)
        self.assertNotIn("base_url", listed.text)

        stale_step = self.identity.issue_step_up(
            access_token=self.credentials.access_token,
            action_group="organization_security_or_connector_policy_change",
            target_id="provider-connection:ollama-lan",
            policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        )
        stale = await self.client.put(
            "/api/v1/admin/provider-connections/ollama-lan",
            headers={"Idempotency-Key": "provider-update-admin-0001"},
            json={"display_name": "LAN Ollama 2", "base_url": "http://ollama.internal:11434",
                  "enabled": True, "expected_version": 2,
                  "step_up_authorization_id": stale_step.authorization},
        )
        self.assertEqual(stale.status_code, 409, stale.text)

        delete_step = self.identity.issue_step_up(
            access_token=self.credentials.access_token,
            action_group="organization_security_or_connector_policy_change",
            target_id="provider-connection:ollama-lan",
            policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        )
        deleted = await self.client.request(
            "DELETE", "/api/v1/admin/provider-connections/ollama-lan",
            headers={"Idempotency-Key": "provider-credential-delete-0001"},
            json={"expected_version": 1, "step_up_authorization_id": delete_step.authorization},
        )
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(deleted.headers["etag"], '"projection-d12d910806b0e61fe29bd7ae"')
        self.assertIsNotNone(service.item)
        self.assertFalse(service.item["configured"])
        self.assertEqual(service.item["credential_version"], 2)
        self.assertEqual(service.item["version"], 2)
        delete_replay = await self.client.request(
            "DELETE", "/api/v1/admin/provider-connections/ollama-lan",
            headers={"Idempotency-Key": "provider-credential-delete-0001"},
            json={"expected_version": 1, "step_up_authorization_id": delete_step.authorization},
        )
        self.assertEqual(delete_replay.status_code, 204, delete_replay.text)
        self.assertEqual(delete_replay.headers["etag"], deleted.headers["etag"])

    async def test_catalog_refresh_and_capability_correction_require_step_up(self) -> None:
        self.dependencies.settings = RuntimeSettings.for_test(
            database_path=self.db_path, policy_version=POLICY_VERSION,
            system_admin_user_ids=frozenset({self.credentials.user_id}),
        )
        await self.client.aclose()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )
        refresh = await self.client.post(
            "/api/v1/admin/provider-catalog/ollama-lan/refresh",
            headers={"Idempotency-Key": "provider-refresh-admin-0001"},
            json={"expected_version": 1, "step_up_authorization_id": "missing"},
        )
        correction = await self.client.patch(
            "/api/v1/admin/provider-models/ollama-lan/qwen3/capabilities",
            headers={"Idempotency-Key": "provider-capability-admin-0001"},
            json={"effective_capabilities": ["text_generation"], "expected_version": 1,
                  "step_up_authorization_id": "missing"},
        )
        self.assertEqual(refresh.status_code, 403)
        self.assertEqual(correction.status_code, 403)

    async def test_system_admin_replaces_only_provider_credential_without_endpoint_reentry(self) -> None:
        class ProviderAdminService:
            def __init__(self) -> None:
                self.calls = []

            def replace_credential(self, context, connection_id, body, idempotency_key):
                self.calls.append((context, connection_id, body, idempotency_key))
                return {
                    "connection_id": connection_id,
                    "provider_code": "UPSTAGE",
                    "display_name": "Upstage 운영",
                    "enabled": True,
                    "configured": True,
                    "credential_version": 5,
                    "version": 8,
                    "verification_status": "verified",
                    "verified_at": "2026-09-17T00:00:00Z",
                    "catalog_status": "ready",
                    "catalog_version": 4,
                    "models": [],
                }, False

        service = ProviderAdminService()
        self.dependencies.provider_connection_service = service
        self.dependencies.settings = RuntimeSettings.for_test(
            database_path=self.db_path, policy_version=POLICY_VERSION,
            system_admin_user_ids=frozenset({self.credentials.user_id}),
        )
        await self.client.aclose()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )
        grant = self.identity.issue_step_up(
            access_token=self.credentials.access_token,
            action_group="organization_security_or_connector_policy_change",
            target_id="provider-connection:upstage-primary",
            policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        )
        secret = "replacement-provider-secret"
        response = await self.client.post(
            "/api/v1/admin/provider-connections/upstage-primary/credential",
            headers={"Idempotency-Key": "provider-credential-replace-0001"},
            json={
                "credential": secret,
                "expected_version": 7,
                "step_up_authorization_id": grant.authorization,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(secret, response.text)
        self.assertNotIn("base_url", response.text)
        self.assertEqual(response.json()["data"]["credential_version"], 5)
        self.assertEqual(service.calls[0][1], "upstage-primary")
        self.assertEqual(service.calls[0][2].credential, secret)
        self.assertEqual(service.calls[0][2].expected_version, 7)


if __name__ == "__main__":
    unittest.main()
