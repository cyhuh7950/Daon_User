from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.knowledge_package import (
    KnowledgePackageRecord,
    KnowledgePackageService,
    ReferenceKnowledgePackageRepository,
)
from daon_user_api.knowledge_package_postgres import PostgresKnowledgePackageService
from daon_user_api.runtime import (
    RuntimeDependencies, RuntimeSettings, _resolve_knowledge_package_service, create_app,
)
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service, native_login


class KnowledgePackageRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        database_path = Path(self.directory.name) / "runtime.sqlite3"
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(database_path, audit_store=audit)
        self.session = native_login(identity)
        authorization_repository = SqliteAuthorizationRepository(database_path)
        authorization_repository.bootstrap_workspace(
            tenant_id=self.session.tenant_id, workspace_id="workspace-knowledge-runtime",
            owner_user_id=self.session.user_id, owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=clock(),
        )
        authorization = AuthorizationService(
            repository=authorization_repository, audit_store=audit,
            clock=clock, identity_service=identity,
        )
        content = b"runtime approved package"
        package_repository = ReferenceKnowledgePackageRepository()
        now = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
        package_repository.add(KnowledgePackageRecord(
            "package-runtime", self.session.tenant_id, "workspace-knowledge-runtime",
            "daon2_5", "2.5", "registration-runtime", "output-runtime",
            "approved", "approved", "registered", hashlib.sha256(content).hexdigest(),
            len(content), "application/json", content,
            now - timedelta(days=1), now + timedelta(days=1),
        ))
        self.identity = identity
        self.content = content
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=database_path, policy_version=POLICY_VERSION),
            identity_service=identity, authorization_service=authorization,
            audit_store=audit, identity_repository=identity_repository,
            authorization_repository=authorization_repository,
            knowledge_package_service=KnowledgePackageService(package_repository, clock=lambda: now),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://api.test.invalid",
            headers={"Authorization": f"Bearer {self.session.access_token}"},
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    async def test_native_list_step_up_copy_and_bounded_content(self) -> None:
        listed = await self.client.get(
            "/api/v1/workspaces/workspace-knowledge-runtime/knowledge-packages"
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        package = listed.json()["data"]["items"][0]
        self.assertEqual(package["producer"], "daon2_5")
        self.assertEqual(package["registration_state"], "registered")
        self.assertEqual(package["review_state"], "approved")
        self.assertEqual(package["effective_at"], "2026-08-13T01:00:00+00:00")
        step_up = self.identity.issue_step_up(
            access_token=self.session.access_token,
            action_group="data_area_move", target_id="package-runtime",
            policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        )
        copied = await self.client.post(
            "/api/v1/workspaces/workspace-knowledge-runtime/knowledge-packages/package-runtime/offline-copies",
            headers={"Idempotency-Key": "knowledge-copy-runtime"},
            json={"device_id": self.session.device_id,
                  "step_up_authorization_id": step_up.authorization},
        )
        self.assertEqual(copied.status_code, 201, copied.text)
        copy_id = copied.json()["data"]["copy_id"]
        content = await self.client.get(
            f"/api/v1/offline-knowledge-copies/{copy_id}/content",
            headers={"X-Daon-Workspace-Id": "workspace-knowledge-runtime"},
        )
        self.assertEqual(content.status_code, 200, content.text)
        self.assertEqual(content.content, self.content)

    def test_production_cloud_store_uses_postgres_knowledge_projection(self) -> None:
        selected = _resolve_knowledge_package_service(None, object())
        self.assertIsInstance(selected, PostgresKnowledgePackageService)
        with self.assertRaisesRegex(Exception, "KNOWLEDGE_PACKAGE_CONTENT_UNAVAILABLE"):
            selected._content_port.read_package(None, None)


if __name__ == "__main__":
    unittest.main()
