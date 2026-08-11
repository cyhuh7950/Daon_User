from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import ClientKind, IdentityPrincipal
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app
from daon_user_api.studio_report import (
    StudioCitation, StudioOutputProjection, StudioReportError, WorkspaceSourceProjection,
)
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service


OUTPUT = StudioOutputProjection(
    "output-1", "output-version-1", "evidence_report", "승인 검토 보고서", "근거 기반 요약",
    "draft", "근거 답변", "run-1", "result-1",
    (StudioCitation("citation-1", "source-1", "source-version-1", "span-1", 2),),
)


class FakeStudio:
    def __init__(self):
        self.create_calls = []

    def create(self, context, request, idempotency_key):  # type: ignore[no-untyped-def]
        self.create_calls.append((context, request, idempotency_key))
        return OUTPUT, False

    def list_outputs(self, context):  # type: ignore[no-untyped-def]
        return (OUTPUT,)


class FakeSources:
    def list_sources(self, context):  # type: ignore[no-untyped-def]
        return (WorkspaceSourceProjection(
            "source-1", "source-version-1", "approved.pdf", "ready", "completed", "completed",
        ),)


class StudioRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
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
        authorization = AuthorizationService(
            repository=authorization_repository, audit_store=audit, clock=clock, identity_service=identity,
        )
        self.identity = identity
        self.studio = FakeStudio()
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
            identity_service=identity, authorization_service=authorization, audit_store=audit,
            identity_repository=identity_repository, authorization_repository=authorization_repository,
            studio_report_service=self.studio, studio_report_repository=FakeSources(),
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dependencies)), base_url="http://127.0.0.1")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    def _authenticated(self, principal=None):
        return patch.object(self.identity, "describe_access", return_value=type(
            "SessionView", (), {"client_kind": ClientKind.WEB, "principal": principal or self.principal},
        )())

    async def test_source_list_and_studio_routes_return_exact_safe_projection(self):
        with self._authenticated():
            sources = await self.client.get("/api/v1/workspaces/workspace-001/sources", cookies={WEB_SESSION_COOKIE: "opaque"})
            created = await self.client.post(
                "/api/v1/workspaces/workspace-001/studio/reports",
                cookies={WEB_SESSION_COOKIE: "opaque"},
                headers={"Idempotency-Key": "report-key-00001", "X-Trace-Id": TRACE_ID},
                json={
                    "source_id": "source-1", "source_version_id": "source-version-1", "run_id": "run-1",
                    "run_result_id": "result-1", "title": "승인 검토 보고서", "purpose": "근거 기반 요약",
                },
            )
            listed = await self.client.get("/api/v1/workspaces/workspace-001/studio/outputs", cookies={WEB_SESSION_COOKIE: "opaque"})
        self.assertEqual(sources.status_code, 200)
        self.assertIn("etag", sources.headers)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["etag"], '"studio-output:output-version-1"')
        self.assertEqual(listed.status_code, 200)
        self.assertIn("etag", listed.headers)
        self.assertEqual(created.json()["data"]["output_type"], "evidence_report")
        self.assertNotIn("localhost", created.text.casefold())

    async def test_studio_report_idempotency_key_rejects_15_and_accepts_16_characters(self):
        body = {
            "source_id": "source-1", "source_version_id": "source-version-1", "run_id": "run-1",
            "run_result_id": "result-1", "title": "승인 검토 보고서", "purpose": "근거 기반 요약",
        }
        with self._authenticated():
            rejected = await self.client.post(
                "/api/v1/workspaces/workspace-001/studio/reports",
                cookies={WEB_SESSION_COOKIE: "opaque"}, headers={"Idempotency-Key": "123456789012345"}, json=body,
            )
            accepted = await self.client.post(
                "/api/v1/workspaces/workspace-001/studio/reports",
                cookies={WEB_SESSION_COOKIE: "opaque"}, headers={"Idempotency-Key": "1234567890123456"}, json=body,
            )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(len(self.studio.create_calls), 1)

    async def test_studio_report_negative_http_paths_are_safe_and_do_not_invoke_on_rejection(self):
        body = {
            "source_id": "source-1", "source_version_id": "source-version-1", "run_id": "run-1",
            "run_result_id": "result-1", "title": "승인 검토 보고서", "purpose": "근거 기반 요약",
        }
        path = "/api/v1/workspaces/workspace-001/studio/reports"
        headers = {"Idempotency-Key": "negative-key-0001"}
        unauthorized = await self.client.post(path, headers=headers, json=body)
        outsider = IdentityPrincipal("user-outsider", "session-out", "device-out", "tenant-001")
        with self._authenticated(outsider):
            forbidden = await self.client.post(path, cookies={WEB_SESSION_COOKIE: "opaque"}, headers=headers, json=body)
        with self._authenticated():
            extra = await self.client.post(
                path, cookies={WEB_SESSION_COOKIE: "opaque"}, headers=headers, json={**body, "unexpected": True},
            )
            with patch.object(
                self.studio, "create", side_effect=StudioReportError("RESOURCE_UNAVAILABLE", status=404),
            ):
                lineage = await self.client.post(
                    path, cookies={WEB_SESSION_COOKIE: "opaque"}, headers=headers, json=body,
                )
        self.assertEqual((unauthorized.status_code, unauthorized.json()["error"]["code"]), (401, "AUTHENTICATION_REQUIRED"))
        self.assertEqual((forbidden.status_code, forbidden.json()["error"]["code"]), (403, "FORBIDDEN"))
        self.assertEqual((extra.status_code, extra.json()["error"]["code"]), (400, "INVALID_REQUEST"))
        self.assertEqual((lineage.status_code, lineage.json()["error"]["code"]), (404, "RESOURCE_UNAVAILABLE"))
        self.assertNotIn("source-1", lineage.text)
        self.assertEqual(self.studio.create_calls, [])


if __name__ == "__main__":
    unittest.main()
