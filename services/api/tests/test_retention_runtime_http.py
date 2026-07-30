from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import (
    AuthorizationService,
    Role,
    SqliteAuthorizationRepository,
)
from daon_user_api.identity import ClientKind, DevicePlatform
from daon_user_api.retention import (
    ReferenceCleanupPort,
    ReferenceRetentionRepository,
    RetentionContext,
    RetentionService,
)
from daon_user_api.runtime import (
    WEB_SESSION_COOKIE,
    RuntimeDependencies,
    RuntimeSettings,
    create_app,
)
from test_identity_support import (
    FakeVerifiedOidcProvider,
    POLICY_VERSION,
    TRACE_ID,
    create_service,
)


class RetentionRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
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
        self.authorization_repository = SqliteAuthorizationRepository(self.db_path)
        self.authorization_repository.bootstrap_workspace(
            tenant_id=self.credentials.tenant_id, workspace_id="workspace-retention-http",
            owner_user_id=self.credentials.user_id, owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=1000,
            now=self.clock(),
        )
        self.authorization = AuthorizationService(
            repository=self.authorization_repository, audit_store=self.audit,
            clock=self.clock, identity_service=self.identity,
        )
        self.retention = RetentionService(
            ReferenceRetentionRepository(), ReferenceCleanupPort(), clock=self.clock,
        )
        self.context = RetentionContext(
            self.credentials.tenant_id, "workspace-retention-http",
            self.credentials.user_id, TRACE_ID, POLICY_VERSION, organization_admin=True,
        )
        self.source_id = "fixture-source-http"
        self.retention.register_source(self.context, self.source_id)
        settings = RuntimeSettings.for_test(
            database_path=self.db_path, policy_version=POLICY_VERSION
        )
        self.dependencies = RuntimeDependencies(
            settings=settings, identity_service=self.identity,
            authorization_service=self.authorization, audit_store=self.audit,
            identity_repository=self.identity_repository,
            authorization_repository=self.authorization_repository,
            retention_service=self.retention,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://app.example.com",
            cookies={WEB_SESSION_COOKIE: self.credentials.access_token},
        )
        self.inventory = [
            {
                "kind": kind,
                "reference_id": f"fixture-http-{kind}",
                "acknowledgement_required": kind == "known_local_copy",
            }
            for kind in (
                "original_content", "index", "preview", "cache",
                "known_local_copy", "sync_reference",
            )
        ]

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    def _step_up(self, target_id: str) -> str:
        return self.identity.issue_step_up(
            access_token=self.credentials.access_token,
            action_group="permanent_delete_or_restore_rollback", target_id=target_id,
            policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        ).authorization

    async def test_six_route_http_semantics_guards_and_safe_payloads(self) -> None:
        create_headers = {"Idempotency-Key": "idem-http-create", "If-Match": "*"}
        created = await self.client.post(
            f"/api/v1/sources/{self.source_id}/deletion-requests",
            headers=create_headers, json={"inventory": self.inventory},
        )
        replay = await self.client.post(
            f"/api/v1/sources/{self.source_id}/deletion-requests",
            headers=create_headers, json={"inventory": self.inventory},
        )
        self.assertEqual((created.status_code, replay.status_code), (201, 201))
        request_id = created.json()["data"]["request_id"]
        self.assertEqual(request_id, replay.json()["data"]["request_id"])

        fetched = await self.client.get(f"/api/v1/deletion-requests/{request_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["data"]["state"], "grace_period")

        wrong_binding = self._step_up("fixture-wrong-target")
        rejected = await self.client.post(
            f"/api/v1/sources/{self.source_id}/legal-holds",
            headers={"Idempotency-Key": "idem-http-hold-bad", "If-Match": f'"source:{self.source_id}:1"'},
            json={"step_up_authorization_id": wrong_binding},
        )
        self.assertEqual(rejected.status_code, 403)

        hold_response = await self.client.post(
            f"/api/v1/sources/{self.source_id}/legal-holds",
            headers={"Idempotency-Key": "idem-http-hold", "If-Match": f'"source:{self.source_id}:1"'},
            json={"step_up_authorization_id": self._step_up(self.source_id)},
        )
        self.assertEqual(hold_response.status_code, 201)
        hold_id = hold_response.json()["data"]["hold_id"]
        held = await self.client.get(f"/api/v1/deletion-requests/{request_id}")
        self.assertEqual(held.json()["data"]["state"], "blocked_by_hold")

        released = await self.client.post(
            f"/api/v1/legal-holds/{hold_id}/release",
            headers={"Idempotency-Key": "idem-http-release", "If-Match": hold_response.headers["etag"]},
            json={"step_up_authorization_id": self._step_up(hold_id)},
        )
        self.assertEqual(released.status_code, 200)
        current = await self.client.get(f"/api/v1/deletion-requests/{request_id}")

        early = await self.client.post(
            f"/api/v1/deletion-requests/{request_id}/purge",
            headers={"Idempotency-Key": "idem-http-purge", "If-Match": current.headers["etag"]},
            json={"step_up_authorization_id": self._step_up(request_id)},
        )
        self.assertEqual(early.status_code, 409)
        self.assertEqual(early.json()["error"]["code"], "DELETION_GRACE_PERIOD_ACTIVE")

        cancelled = await self.client.post(
            f"/api/v1/deletion-requests/{request_id}/cancel",
            headers={"Idempotency-Key": "idem-http-cancel", "If-Match": current.headers["etag"]},
            json={},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["data"]["state"], "cancelled")
        combined = created.text + held.text + released.text + early.text + cancelled.text
        for forbidden in (str(self.db_path), "fixture/content/path", "content_digest", "secret"):
            self.assertNotIn(forbidden, combined.lower())

    async def test_internal_retention_codes_map_to_openapi_safe_error_codes(self) -> None:
        openapi_path = Path(__file__).resolve().parents[3] / "packages/contracts/openapi/v1/openapi.json"
        safe_codes = set(json.loads(openapi_path.read_text(encoding="utf-8"))[
            "components"
        ]["schemas"]["SafeErrorCode"]["enum"])
        missing = await self.client.post(
            "/api/v1/sources/fixture-source-missing/deletion-requests",
            headers={"Idempotency-Key": "idem-safe-missing", "If-Match": "*"},
            json={"inventory": self.inventory},
        )
        created = await self.client.post(
            f"/api/v1/sources/{self.source_id}/deletion-requests",
            headers={"Idempotency-Key": "idem-safe-create", "If-Match": "*"},
            json={"inventory": self.inventory},
        )
        self.assertEqual(created.status_code, 201)
        request_id = created.json()["data"]["request_id"]
        changed_inventory = [dict(item) for item in self.inventory]
        changed_inventory[0]["reference_id"] = "fixture-http-original-changed"
        reused = await self.client.post(
            f"/api/v1/sources/{self.source_id}/deletion-requests",
            headers={"Idempotency-Key": "idem-safe-create", "If-Match": "*"},
            json={"inventory": changed_inventory},
        )
        conflict = await self.client.post(
            f"/api/v1/deletion-requests/{request_id}/cancel",
            headers={
                "Idempotency-Key": "idem-safe-conflict",
                "If-Match": f'"deletion:{request_id}:99"',
            },
            json={},
        )
        results = {
            "unavailable": (missing.status_code, missing.json()["error"]["code"]),
            "idempotency": (reused.status_code, reused.json()["error"]["code"]),
            "version": (conflict.status_code, conflict.json()["error"]["code"]),
        }
        self.assertEqual(results, {
            "unavailable": (404, "RESOURCE_UNAVAILABLE"),
            "idempotency": (409, "INVALID_REQUEST"),
            "version": (409, "INVALID_REQUEST"),
        })
        self.assertTrue({code for _, code in results.values()}.issubset(safe_codes))
        joined = "".join(response.text for response in (missing, reused, conflict))
        for internal_code in (
            "SOURCE_UNAVAILABLE", "IDEMPOTENCY_KEY_REUSED", "RETENTION_VERSION_CONFLICT"
        ):
            self.assertNotIn(internal_code, joined)


if __name__ == "__main__":
    unittest.main()
