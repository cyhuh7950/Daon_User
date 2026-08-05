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
from daon_user_api.provider_settings import (
    ProviderSettingsService,
    ReferenceProviderSettingsRepository,
    ServerCredentialPresenceResolver,
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
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=self.db_path, policy_version=POLICY_VERSION),
            identity_service=self.identity, authorization_service=self.authorization,
            audit_store=self.audit, identity_repository=self.identity_repository,
            authorization_repository=self.authorization_repository,
            provider_settings_service=ProviderSettingsService(
                ReferenceProviderSettingsRepository(), ServerCredentialPresenceResolver()
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


if __name__ == "__main__":
    unittest.main()
