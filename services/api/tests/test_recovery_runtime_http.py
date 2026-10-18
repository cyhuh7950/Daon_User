from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import (
    AuthorizationService,
    Role,
    SqliteAuthorizationRepository,
)
from daon_user_api.identity import ClientKind, DevicePlatform
from daon_user_api.recovery import (
    RecoveryService,
    ReferenceRecoveryRepository,
    ReferenceRestorePort,
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


class RecoveryRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_l2_selection_passes_exact_resolver_metadata_and_rechecks_session_before_capture_commit(self) -> None:
        """ASGI 포트 연결 시험. 실제 capture/PG/ready 성공을 대체하지 않는다."""
        from types import SimpleNamespace
        from unittest.mock import Mock
        from daon_user_api import runtime
        from daon_user_api.recovery import BackupObjectInput, BackupSelectionInput, RecoveryError, _ResolvedBackupSelection
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        resolved = _ResolvedBackupSelection(BackupSelectionInput("notebook-a", ("version-a",)),
            "schema-a", "watermark-a", (BackupObjectInput("a" * 32, "a" * 64, 7),), "a" * 64)
        metadata = {"synthetic-port-only": True}
        for changed in (False, True):
            service = object.__new__(PostgresRecoveryService)
            reached = []
            def capture(context, actual, relations, **kwargs):
                self.assertEqual((actual, relations), (resolved, metadata))
                current, final = kwargs["recheck"]()
                self.assertEqual((current, final), (resolved, metadata))
                reached.append(True)
                raise RecoveryError("RESOURCE_UNAVAILABLE", 503)  # 실제 파일 port는 이 HTTP 시험에 주입하지 않는다.
            service.create_selected_backup = Mock(side_effect=capture)
            resolver = SimpleNamespace(resolve=Mock(return_value=resolved), capture_metadata=Mock(return_value=metadata))
            dependencies = replace(self.dependencies, recovery_service=service, cloud_store=SimpleNamespace())
            original = runtime._principal
            calls = []
            def principal(request, deps):
                current = original(request, deps)
                calls.append(True)
                return replace(current, session_id="changed-session") if changed and len(calls) > 1 else current
            with patch.object(runtime, "PostgresBackupSelectionResolver", return_value=resolver), patch.object(runtime, "_principal", side_effect=principal):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)),
                    base_url="https://app.example.com", cookies={WEB_SESSION_COOKIE: self.credentials.access_token}) as client:
                    result = await client.post("/api/v1/backups", headers={"Idempotency-Key": "l2-callback"},
                        json={"workspace_id": self.workspace_id, "notebook_id": "notebook-a", "trigger": "manual", "source_version_ids": ["version-a"]})
            self.assertEqual(service.create_selected_backup.call_count, 1)
            self.assertEqual(result.status_code, 403 if changed else 503)
            self.assertEqual(len(reached), 0 if changed else 1)

    async def test_l1_durable_runtime_legacy_without_capture_is_503_not_reference_ready(self) -> None:
        from daon_user_api.recovery import UnavailableRecoveryService
        unavailable = UnavailableRecoveryService()
        isolated_dependencies = replace(self.dependencies, recovery_service=unavailable, cloud_store=None)
        # 기존 별도 Reference 7route 시험의 성공을 durable runtime fallback으로 사용하지 않는다.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(isolated_dependencies)),
            base_url="https://app.example.com", cookies={WEB_SESSION_COOKIE: self.credentials.access_token}) as client:
            with patch.object(unavailable, "create_backup", side_effect=AssertionError("inventory-only create forbidden")) as create:
                result = await client.post("/api/v1/backups", headers={"Idempotency-Key": "l1-legacy-no-capture"},
                    json={"workspace_id": self.workspace_id, "trigger": "manual", "schema_revision": "0006",
                          "retention_watermark": "fixture-watermark", "objects": [{"object_id": "fixture-object-http", "checksum_sha256": "a" * 64, "byte_size": 128}]})
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()["error"]["code"], "RESOURCE_UNAVAILABLE")
                create.assert_not_called()
        self.assertEqual(self.recovery._repository.backups, {})

    async def test_l1_selection_without_capture_never_uses_inventory_ready_fallback(self) -> None:
        body = {"workspace_id": self.workspace_id, "notebook_id": "notebook-a", "trigger": "manual",
                "source_version_ids": ["version-a"]}
        result = await self.client.post("/api/v1/backups", headers={"Idempotency-Key": "l1-no-capture"}, json=body)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"]["code"], "RESOURCE_UNAVAILABLE")
        self.assertEqual(self.recovery._repository.backups, {})
        for changes in ({"source_version_ids": []}, {"source_version_ids": ["v"] * 2},
                        {"source_version_ids": [f"v-{i}" for i in range(101)]}, {"source_version_ids": "v"},
                        {"source_version_ids": [1]}, {"trigger": "automatic"}, {"schema_revision": "forged"}):
            result = await self.client.post("/api/v1/backups", headers={"Idempotency-Key": "l1-invalid"}, json={**body, **changes})
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.recovery._repository.backups, {})

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="daon-s44-l1-runtime-")
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
            platform=DevicePlatform.WEB, trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        self.workspace_id = "workspace-recovery-http"
        self.authorization_repository = SqliteAuthorizationRepository(self.db_path)
        self.authorization_repository.bootstrap_workspace(
            tenant_id=self.credentials.tenant_id, workspace_id=self.workspace_id,
            owner_user_id=self.credentials.user_id, owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=self.clock(),
        )
        self.authorization = AuthorizationService(
            repository=self.authorization_repository, audit_store=self.audit,
            clock=self.clock, identity_service=self.identity,
        )
        self.restore_port = ReferenceRestorePort()
        self.recovery = RecoveryService(
            ReferenceRecoveryRepository(), self.restore_port, clock=self.clock
        )
        settings = RuntimeSettings.for_test(
            database_path=self.db_path, policy_version=POLICY_VERSION
        )
        self.dependencies = RuntimeDependencies(
            settings=settings, identity_service=self.identity,
            authorization_service=self.authorization, audit_store=self.audit,
            identity_repository=self.identity_repository,
            authorization_repository=self.authorization_repository,
            recovery_service=self.recovery,
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

    def _step_up(self, target_id: str) -> str:
        return self.identity.issue_step_up(
            access_token=self.credentials.access_token,
            action_group="permanent_delete_or_restore_rollback", target_id=target_id,
            policy_version=POLICY_VERSION, trace_id=TRACE_ID,
        ).authorization

    async def test_seven_route_flow_requires_two_step_ups_and_isolates_restore(self) -> None:
        created = await self.client.post(
            "/api/v1/backups", headers={"Idempotency-Key": "idem-backup-http"},
            json={
                "workspace_id": self.workspace_id,
                "trigger": "manual",
                "schema_revision": "0006",
                "retention_watermark": "fixture-watermark",
                "objects": [{
                    "object_id": "fixture-object-http",
                    "checksum_sha256": "a" * 64,
                    "byte_size": 128,
                }],
            },
        )
        self.assertEqual(created.status_code, 201)
        backup_id = created.json()["data"]["backup_id"]
        self.assertEqual(created.json()["data"]["state"], "ready")

        listed = await self.client.get(
            "/api/v1/backups", params={"workspace_id": self.workspace_id}
        )
        fetched = await self.client.get(f"/api/v1/backups/{backup_id}")
        self.assertEqual((listed.status_code, fetched.status_code), (200, 200))
        self.assertEqual(listed.json()["data"][0]["backup_id"], backup_id)

        destination = {
            "tenant_id": "fixture-tenant-http",
            "workspace_id": "fixture-workspace-http",
            "database_id": "fixture-database-http",
            "bucket_id": "fixture-bucket-http",
        }
        preview_step_up = self._step_up(backup_id)
        preview = await self.client.post(
            f"/api/v1/backups/{backup_id}/restore-previews",
            headers={"Idempotency-Key": "idem-preview-http"},
            json={
                "destination": destination,
                "step_up_authorization_id": preview_step_up,
            },
        )
        self.assertEqual(preview.status_code, 201)
        request_id = preview.json()["data"]["request_id"]
        preview_version = preview.json()["data"]["preview"]["version"]

        reused = await self.client.post(
            f"/api/v1/restore-requests/{request_id}/execute",
            headers={"Idempotency-Key": "idem-execute-reused", "If-Match": preview.headers["etag"]},
            json={
                "preview_version": preview_version,
                "step_up_authorization_id": preview_step_up,
            },
        )
        self.assertEqual(reused.status_code, 403)

        executed = await self.client.post(
            f"/api/v1/restore-requests/{request_id}/execute",
            headers={"Idempotency-Key": "idem-execute-http", "If-Match": preview.headers["etag"]},
            json={
                "preview_version": preview_version,
                "step_up_authorization_id": self._step_up(request_id),
            },
        )
        self.assertEqual(executed.status_code, 200)
        self.assertEqual(executed.json()["data"]["state"], "completed")
        fetched_restore = await self.client.get(f"/api/v1/restore-requests/{request_id}")
        self.assertEqual(fetched_restore.json()["data"]["state"], "completed")
        self.assertEqual(self.restore_port.restored_object_ids, ["fixture-object-http"])
        self.assertEqual(self.restore_port.original_mutations, 0)

        openapi_path = Path(__file__).resolve().parents[3] / "packages/contracts/openapi/v1/openapi.json"
        safe_codes = set(json.loads(openapi_path.read_text(encoding="utf-8"))[
            "components"
        ]["schemas"]["SafeErrorCode"]["enum"])
        self.assertIn(reused.json()["error"]["code"], safe_codes)
        combined = created.text + preview.text + reused.text + executed.text
        for forbidden in (str(self.db_path), "fixture/content/path", "content_digest", "secret"):
            self.assertNotIn(forbidden.lower(), combined.lower())


if __name__ == "__main__":
    unittest.main()
