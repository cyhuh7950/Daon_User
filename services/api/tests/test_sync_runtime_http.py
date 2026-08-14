from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

import httpx

from test_identity_support import FakeVerifiedOidcProvider, POLICY_VERSION, TRACE_ID, create_service
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import ClientKind, DevicePlatform
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app


class SyncRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        database_path = Path(self.directory.name) / "runtime.sqlite3"
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(database_path, audit_store=audit)
        login = identity.begin_oidc_login(
            issuer="https://login.example.com", client_id="daon-web",
            audience="daon-user-api", redirect_uri="https://app.example.com/auth/callback",
            client_kind=ClientKind.WEB, tenant_id="tenant-001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        provider = FakeVerifiedOidcProvider()
        provider.expected_nonce = login.nonce
        self.session = identity.complete_oidc_login(
            state=login.state, authorization_code=provider.authorization_code,
            code_verifier=login.code_verifier, client_id="daon-web",
            redirect_uri="https://app.example.com/auth/callback", provider=provider,
            platform=DevicePlatform.WEB, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        authorization_repository = SqliteAuthorizationRepository(database_path)
        authorization_repository.bootstrap_workspace(
            tenant_id=self.session.tenant_id, workspace_id="workspace-sync-runtime",
            owner_user_id=self.session.user_id, owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=clock(),
        )
        authorization = AuthorizationService(
            repository=authorization_repository, audit_store=audit,
            clock=clock, identity_service=identity,
        )
        self.identity = identity
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(
                database_path=database_path, policy_version=POLICY_VERSION
            ),
            identity_service=identity, authorization_service=authorization,
            audit_store=audit, identity_repository=identity_repository,
            authorization_repository=authorization_repository,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://api.test.invalid",
            cookies={WEB_SESSION_COOKIE: self.session.access_token},
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    async def test_preview_requires_step_up_approval_then_transfers_exact_manifest(self) -> None:
        content = b"runtime-sync"
        digest = hashlib.sha256(content).hexdigest()
        created = await self.client.post(
            "/api/v1/workspaces/workspace-sync-runtime/sync-operations",
            headers={"Idempotency-Key": "sync-create-runtime", "If-Match": "*"},
            json={"target_area": "cloud_sync", "items": [{
                "item_id": "runtime-item", "source_version_id": "runtime-source-version",
                "local_object_id": "runtime-local-object", "digest_sha256": digest,
                "byte_size": len(content), "content_type": "text/plain",
            }]},
        )
        self.assertEqual(created.status_code, 201, created.text)
        operation_id = created.json()["data"]["operation_id"]
        listed = await self.client.get(
            "/api/v1/workspaces/workspace-sync-runtime/sync-operations"
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["data"]["operations"][0]["operation_id"], operation_id)
        self.assertEqual(listed.json()["data"]["operations"][0]["item_ids"], ["runtime-item"])
        denied = await self.client.post(
            f"/api/v1/sync-operations/{operation_id}/approve",
            headers={"Idempotency-Key": "sync-approve-denied", "If-Match": created.headers["etag"]},
            json={"approved_item_ids": ["runtime-item"], "step_up_authorization_id": "invalid-step-up"},
        )
        self.assertEqual(denied.status_code, 403)
        step_up = self.identity.issue_step_up(
            access_token=self.session.access_token, action_group="data_area_move",
            target_id=operation_id, policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        )
        approved = await self.client.post(
            f"/api/v1/sync-operations/{operation_id}/approve",
            headers={"Idempotency-Key": "sync-approve-runtime", "If-Match": created.headers["etag"]},
            json={"approved_item_ids": ["runtime-item"],
                  "step_up_authorization_id": step_up.authorization},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        transferred = await self.client.post(
            f"/api/v1/sync-operations/{operation_id}/transfer-batches",
            headers={"Idempotency-Key": "sync-batch-runtime", "If-Match": approved.headers["etag"]},
            json={"items": [{
                "item_id": "runtime-item",
                "content_base64": base64.b64encode(content).decode(),
            }]},
        )
        self.assertEqual(transferred.status_code, 200, transferred.text)
        self.assertEqual(transferred.json()["operation"]["state"], "reindex_requested")
        self.assertEqual(transferred.json()["operation"]["source_mutations"], 0)


if __name__ == "__main__":
    unittest.main()
