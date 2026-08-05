from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from test_identity_support import POLICY_VERSION, TRACE_ID, create_service
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import (
    AuthorizationService,
    Role,
    SqliteAuthorizationRepository,
)
from daon_user_api.identity import ClientKind, IdentityPrincipal
from daon_user_api.runtime import (
    WEB_SESSION_COOKIE,
    RuntimeDependencies,
    RuntimeSettings,
    create_app,
)
from daon_user_api.source_upload import SourceUploadResult


class RecordingSourceUploadService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def register_pdf(self, **values: object) -> SourceUploadResult:
        self.calls.append(values)
        return SourceUploadResult(
            source_id="src-001",
            source_version_id="source-version-001",
            object_id="0" * 32,
            digest_sha256="a" * 64,
            byte_size=23,
            status="accepted",
            replayed=False,
        )


class SourceUploadRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        db_path = Path(self.directory.name) / "runtime.sqlite3"
        self.audit = AuditEventStore()
        self.identity, self.identity_repository, _, self.clock = create_service(
            db_path, audit_store=self.audit
        )
        self.authorization_repository = SqliteAuthorizationRepository(db_path)
        self.authorization = AuthorizationService(
            repository=self.authorization_repository,
            audit_store=self.audit,
            clock=self.clock,
            identity_service=self.identity,
        )
        self.principal = IdentityPrincipal("user-001", "session-001", "device-001", "tenant-001")
        self.authorization_repository.bootstrap_workspace(
            tenant_id="tenant-001",
            workspace_id="workspace-001",
            owner_user_id="user-001",
            owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal",
            data_area="cloud_sync",
            cost_limit_cents=1000,
            now=self.clock(),
        )
        self.uploads = RecordingSourceUploadService()
        self.settings = RuntimeSettings.for_test(
            database_path=db_path, policy_version=POLICY_VERSION
        )
        self.dependencies = RuntimeDependencies(
            settings=self.settings,
            identity_service=self.identity,
            authorization_service=self.authorization,
            audit_store=self.audit,
            identity_repository=self.identity_repository,
            authorization_repository=self.authorization_repository,
            source_upload_service=self.uploads,
        )
        self.app = create_app(self.dependencies)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    def _authenticated(self):  # type: ignore[no-untyped-def]
        return patch.object(
            self.identity,
            "describe_access",
            return_value=type(
                "SessionView",
                (),
                {"client_kind": ClientKind.WEB, "principal": self.principal},
            )(),
        )

    async def test_pdf_upload_requires_authentication(self) -> None:
        response = await self.client.post(
            "/api/v1/workspaces/workspace-001/sources",
            content=b"%PDF-1.7\nsmall fixture",
            headers={
                "Content-Type": "application/pdf",
                "X-Source-Filename": "fixture.pdf",
                "Idempotency-Key": "upload-001",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.uploads.calls, [])

    async def test_authenticated_pdf_upload_passes_verified_scope_to_service(self) -> None:
        with self._authenticated():
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/sources",
                content=b"%PDF-1.7\nsmall fixture",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={
                    "Content-Type": "application/pdf",
                    "X-Source-Filename": "fixture.pdf",
                    "Idempotency-Key": "upload-001",
                    "X-Trace-Id": TRACE_ID,
                },
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["data"]["source_id"], "src-001")
        self.assertEqual(response.headers["etag"], '"source:src-001:1"')
        self.assertNotIn("object_key", response.text)
        call = self.uploads.calls[0]
        self.assertEqual(call["tenant_id"], "tenant-001")
        self.assertEqual(call["workspace_id"], "workspace-001")
        self.assertEqual(call["actor_id"], "user-001")
        self.assertEqual(call["filename"], "fixture.pdf")

    async def test_upload_rejects_mime_mismatch_and_corrupt_pdf_before_service(self) -> None:
        with self._authenticated():
            mime = await self.client.post(
                "/api/v1/workspaces/workspace-001/sources",
                content=b"%PDF-1.7\nfixture",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={
                    "Content-Type": "text/plain",
                    "X-Source-Filename": "fixture.pdf",
                    "Idempotency-Key": "upload-mime",
                },
            )
            corrupt = await self.client.post(
                "/api/v1/workspaces/workspace-001/sources",
                content=b"not-a-pdf",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={
                    "Content-Type": "application/pdf",
                    "X-Source-Filename": "fixture.pdf",
                    "Idempotency-Key": "upload-corrupt",
                },
            )
        self.assertEqual(mime.status_code, 415)
        self.assertEqual(corrupt.status_code, 400)
        self.assertEqual(self.uploads.calls, [])


if __name__ == "__main__":
    unittest.main()
