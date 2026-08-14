from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import ClientKind, IdentityError, IdentityPrincipal
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app
from daon_user_api.studio_export import export_studio_output
from daon_user_api.studio_workspace import StudioError
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service


class FakeStudioWorkspace:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, context, request, key):  # type: ignore[no-untyped-def]
        self.calls.append(("generate", context, request, key))
        return {"studio_output_id": "output-1", "output_version_id": "version-1", "output_type": "evidence_report", "status": "draft", "title": "산출물", "content": {"body": "생성"}, "settings_snapshot_id": "settings-1", "citations": [{"citation_id": "citation-1"}]}, False

    def list_outputs(self, context):  # type: ignore[no-untyped-def]
        return ({"studio_output_id": "output-1", "output_version_id": "version-1", "status": "draft", "title": "산출물"},)

    def list_versions(self, context, output_id):  # type: ignore[no-untyped-def]
        self.calls.append(("list_versions", context, output_id))
        return ({
            "output_version_id": "version-1", "content_version": 1, "previous_version_id": None,
            "status": "draft", "content": {"body": "생성"}, "revision_type": "initial",
            "change_reason": "initial_generation", "settings_snapshot_id": "settings-1",
            "output_format": "pdf",
            "citations": [{"citation_id": "citation-1", "source_version_id": "source-version-1", "evidence_span_id": "span-1", "origin": "raw_source", "locator": {"kind": "page", "value": "2"}}],
            "review_request_id": None, "approval_request_id": None, "approval_id": None,
            "delivery_id": None, "knowledge_registration_id": None,
        },)

    def revise(self, context, output_id, revision, key):  # type: ignore[no-untyped-def]
        self.calls.append(("revise", context, output_id, revision, key))
        return {"output_version_id": "version-2", "previous_version_id": "version-1", "status": "draft", "content": "변경", "revision_type": "user_edit", "change_reason": "문구 정정", "approval_required": True, "settings_snapshot_id": "settings-1", "generation_request_id": None, "resubmission_of_rejected_version": False}, False

    def action(self, context, action, payload, key):  # type: ignore[no-untyped-def]
        self.calls.append((action, context, payload, key))
        return {"record_id": f"{action}-1", "action": action, "status": "accepted", "output_version_id": payload["output_version_id"]}, False

    def export(self, context, output_id, version_id, format_name):  # type: ignore[no-untyped-def]
        self.calls.append(("export", context, output_id, version_id, format_name))
        return export_studio_output(format_name, "산출물", "근거 내용", {
            "output_version_id": version_id, "created_at": "2026-08-13T00:00:00Z",
            "knowledge_scope": "source-version-1", "evidence_appendix": "Citation page 2",
        })


class StudioWorkspaceRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        db_path = Path(self.directory.name) / "runtime.sqlite3"
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
        authorization_repository = SqliteAuthorizationRepository(db_path)
        self.principal = IdentityPrincipal("user-001", "session-001", "device-001", "tenant-001")
        authorization_repository.bootstrap_workspace(
            tenant_id="tenant-001", workspace_id="workspace-001", owner_user_id="user-001",
            owner_role=Role.PERSONAL_OWNER, workspace_kind="personal", data_area="cloud_sync",
            cost_limit_cents=1000, now=clock(),
        )
        authorization = AuthorizationService(repository=authorization_repository, audit_store=audit, clock=clock, identity_service=identity)
        self.identity = identity
        self.studio = FakeStudioWorkspace()
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
            identity_service=identity, authorization_service=authorization, audit_store=audit,
            identity_repository=identity_repository, authorization_repository=authorization_repository,
            studio_workspace_service=self.studio,
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dependencies)), base_url="http://127.0.0.1")

    async def asyncTearDown(self):
        await self.client.aclose(); self.dependencies.close(); self.directory.cleanup()

    def authenticated(self):
        return patch.object(self.identity, "describe_access", return_value=type("View", (), {"client_kind": ClientKind.WEB, "principal": self.principal})())

    async def test_generation_version_action_and_export_routes_are_real_and_safe(self):
        generation = {
            "workspace_id": "workspace-001", "output_type": "evidence_report", "source_id": "source-1",
            "source_version_ids": ["source-version-1"], "run_id": "run-1", "run_result_id": "result-1",
            "settings": {"purpose": "목적", "audience": "독자", "source_version_ids": ["source-version-1"],
                         "ruleset_version_id": None, "length": "short", "structure": "summary", "output_format": "pdf", "review_condition": "review_required"},
        }
        cookies = {WEB_SESSION_COOKIE: "opaque"}
        with self.authenticated():
            created = await self.client.post("/api/v1/studio-generation-requests", cookies=cookies, headers={"Idempotency-Key": "generation-key-0001", "X-Trace-Id": TRACE_ID}, json=generation)
            listed = await self.client.get("/api/v1/studio-outputs?workspace_id=workspace-001", cookies=cookies)
            versions = await self.client.get("/api/v1/studio-outputs/output-1/versions?workspace_id=workspace-001", cookies=cookies)
            revised = await self.client.post("/api/v1/studio-outputs/output-1/versions", cookies=cookies, headers={"Idempotency-Key": "revision-key-00001"}, json={"workspace_id": "workspace-001", "previous_version_id": "version-1", "revision_type": "user_edit", "change_reason": "문구 정정", "content": "변경"})
            review = await self.client.post("/api/v1/reviews", cookies=cookies, headers={"Idempotency-Key": "review-key-000001"}, json={"workspace_id": "workspace-001", "output_version_id": "version-1"})
            exported = await self.client.get("/api/v1/studio-outputs/output-1/versions/version-1/exports/pdf?workspace_id=workspace-001", cookies=cookies)
        self.assertEqual([created.status_code, listed.status_code, versions.status_code, revised.status_code, review.status_code, exported.status_code], [201, 200, 200, 201, 201, 200])
        self.assertEqual(versions.json()["data"]["versions"][0]["citations"][0]["origin"], "raw_source")
        for response in (created, revised, review): self.assertRegex(response.headers["etag"], r'^"studio-[^"]+"$')
        self.assertEqual(revised.json()["data"]["content"], "변경")
        self.assertTrue(exported.content.startswith(b"%PDF-"))
        self.assertEqual(exported.headers["x-content-type-options"], "nosniff")
        self.assertEqual(exported.headers["cache-control"], "no-store")

    async def test_sensitive_action_without_exact_step_up_writes_zero(self):
        with self.authenticated(), patch.object(self.identity, "consume_step_up", side_effect=IdentityError("STEP_UP_REQUIRED", 403)):
            rejected = await self.client.post("/api/v1/deliveries", cookies={WEB_SESSION_COOKIE: "opaque"}, headers={"Idempotency-Key": "delivery-key-0001"}, json={"workspace_id": "workspace-001", "output_version_id": "version-1"})
        self.assertEqual((rejected.status_code, rejected.json()["error"]["code"]), (403, "STEP_UP_REQUIRED"))
        self.assertFalse(any(call[0] == "delivery" for call in self.studio.calls))

    async def test_missing_default_policy_is_public_fail_closed_409(self):
        with self.authenticated(), patch.object(
            self.studio,
            "list_outputs",
            side_effect=StudioError("POLICY_PROJECTION_UNAVAILABLE", 409),
        ):
            response = await self.client.get(
                "/api/v1/studio-outputs?workspace_id=workspace-001",
                cookies={WEB_SESSION_COOKIE: "opaque"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "POLICY_PROJECTION_UNAVAILABLE")

    async def test_database_failure_remains_public_503(self):
        with self.authenticated(), patch.object(
            self.studio,
            "list_outputs",
            side_effect=StudioError("STUDIO_DATABASE_UNAVAILABLE", 503),
        ):
            response = await self.client.get(
                "/api/v1/studio-outputs?workspace_id=workspace-001",
                cookies={WEB_SESSION_COOKIE: "opaque"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "STUDIO_DATABASE_UNAVAILABLE")

    async def test_step_up_endpoint_requires_and_forwards_current_password(self):
        grant = type("Grant", (), {"authorization": "opaque-step-up", "issued_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc), "expires_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})()
        with self.authenticated(), patch.object(self.identity, "issue_step_up_after_reauthentication", return_value=grant) as issue:
            missing = await self.client.post("/api/v1/session/step-up", cookies={WEB_SESSION_COOKIE: "opaque"}, headers={"Idempotency-Key": "step-key-missing"}, json={"action_group": "external_transfer", "target_id": "version-1"})
            issued = await self.client.post("/api/v1/session/step-up", cookies={WEB_SESSION_COOKIE: "opaque"}, headers={"Idempotency-Key": "step-key-present"}, json={"action_group": "external_transfer", "target_id": "version-1", "password": "current password value"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(issued.status_code, 201)
        self.assertEqual(issue.call_args.kwargs["password"], "current password value")


if __name__ == "__main__":
    unittest.main()
