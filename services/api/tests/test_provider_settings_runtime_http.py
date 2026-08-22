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


if __name__ == "__main__":
    unittest.main()
