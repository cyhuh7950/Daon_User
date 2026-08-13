from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import ClientKind, IdentityPrincipal
from daon_user_api.question_answering_postgres import CitationPdfContent, StoredCitation, StoredQuestionAnswer
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service


PDF = b"%PDF-1.4\npage one\fpage two\n%%EOF\n"


class FakeQuestionService:
    def __init__(self) -> None:
        self.calls = []

    def ask(self, context, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((context, kwargs))
        return StoredQuestionAnswer(
            kwargs["run_id"], "result-cp3", "ORANGE-COMPASS-42", False,
            (StoredCitation(
                "citation-cp3", kwargs["source_id"], kwargs["source_version_id"],
                "span-page-2", 2,
            ),),
        )


class FakeCitationContent:
    def read_citation_pdf(self, context, citation_id):  # type: ignore[no-untyped-def]
        if citation_id != "citation-cp3":
            raise AssertionError(citation_id)
        return CitationPdfContent(
            "source-cp3", "source-version-cp3", "report.pdf", PDF, 2,
        ), 2


class QuestionRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        db_path = Path(self.directory.name) / "runtime.sqlite3"
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
        authorization_repository = SqliteAuthorizationRepository(db_path)
        self.principal = IdentityPrincipal("user-001", "session-001", "device-001", "tenant-001")
        authorization_repository.bootstrap_workspace(
            tenant_id="tenant-001", workspace_id="workspace-001",
            owner_user_id="user-001", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal", data_area="cloud_sync",
            cost_limit_cents=1000, now=clock(),
        )
        authorization = AuthorizationService(
            repository=authorization_repository, audit_store=audit, clock=clock,
            identity_service=identity,
        )
        self.identity = identity
        self.service = FakeQuestionService()
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
            identity_service=identity, authorization_service=authorization,
            audit_store=audit, identity_repository=identity_repository,
            authorization_repository=authorization_repository,
            question_answering_service=self.service,
            citation_content_repository=FakeCitationContent(),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="http://127.0.0.1",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    def _authenticated(self):  # type: ignore[no-untyped-def]
        return patch.object(self.identity, "describe_access", return_value=type(
            "SessionView", (), {"client_kind": ClientKind.WEB, "principal": self.principal},
        )())

    async def test_authenticated_question_returns_grounded_lineage_without_internal_url(self) -> None:
        with self._authenticated():
            response = await self.client.post(
                "/api/v1/workspaces/workspace-001/questions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={
                    "Content-Type": "application/json", "X-Trace-Id": TRACE_ID,
                    "Idempotency-Key": "question-cp3-0001",
                },
                json={
                    "source_id": "source-cp3", "source_version_id": "source-version-cp3",
                    "question": "What is the citation verification phrase?",
                },
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["answer"], "ORANGE-COMPASS-42")
        self.assertEqual(data["citations"][0]["page"], 2)
        self.assertNotIn("minio", response.text.casefold())
        self.assertNotIn("localhost", response.text.casefold())
        self.assertEqual(self.service.calls[0][0].workspace_id, "workspace-001")

    async def test_question_rejects_unbounded_or_unsafe_idempotency_before_service(self) -> None:
        for invalid_key in ("x" * 15, "x" * 129, "unsafe/key-value"):
            self.service.calls.clear()
            with self._authenticated():
                response = await self.client.post(
                    "/api/v1/workspaces/workspace-001/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": invalid_key},
                    json={
                        "source_id": "source-cp3", "source_version_id": "source-version-cp3",
                        "question": "bounded question",
                    },
                )
            self.assertEqual(response.status_code, 400, invalid_key)
            self.assertEqual(self.service.calls, [])

    async def test_citation_content_is_inline_pdf_after_current_access_recheck(self) -> None:
        with self._authenticated():
            response = await self.client.get(
                "/api/v1/workspaces/workspace-001/citations/citation-cp3/content",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertEqual(response.headers["x-citation-page"], "2")


if __name__ == "__main__":
    unittest.main()
