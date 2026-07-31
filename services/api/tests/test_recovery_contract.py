from __future__ import annotations

import json
import unittest
from pathlib import Path

from daon_user_api.runtime import _recovery_public_error_code


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services" / "api" / "migrations" / "versions" / "0006_backup_restore_recovery.py"
OPENAPI = ROOT / "packages" / "contracts" / "openapi" / "v1" / "openapi.json"
PATHS = {
    "/api/v1/backups": {"get", "post"},
    "/api/v1/backups/{id}": {"get"},
    "/api/v1/backups/{id}/restore-previews": {"post"},
    "/api/v1/restore-requests/{id}": {"get"},
    "/api/v1/restore-requests/{id}/execute": {"post"},
    "/api/v1/restore-requests/{id}/cancel": {"post"},
}


class RecoveryContractTests(unittest.TestCase):
    def test_migration_declares_scoped_fixture_only_immutable_contract(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            'revision = "0006"', 'down_revision = "0005"',
            "backup_record_locator", "restore_request_locator",
            "backup_records", "backup_manifests", "restore_requests",
            "restore_previews", "restore_verifications",
            "ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY",
            "RECOVERY_IMMUTABLE_MUTATION", "BACKUP_RECORD_IMMUTABLE_MUTATION",
            "RESTORE_REQUEST_IMMUTABLE_MUTATION", "destination_tenant_id LIKE 'fixture-%'",
            "retention_rechecked boolean NOT NULL", "REFERENCES workspaces(tenant_id, workspace_id)",
            "OLD.state = 'queued'", "OLD.state = 'preview_ready'",
        ):
            self.assertIn(token, source)

    def test_postgres_recovery_forces_least_privilege_role_before_rls_context(self) -> None:
        source = (
            ROOT / "services" / "api" / "src" / "daon_user_api" / "recovery_postgres.py"
        ).read_text(encoding="utf-8")
        self.assertLess(
            source.index('connection.execute("SET LOCAL ROLE daon_app")'),
            source.index("SELECT set_config('app.tenant_id'"),
        )

    def test_openapi_exposes_exact_seven_operations_and_write_guards(self) -> None:
        document = json.loads(OPENAPI.read_text(encoding="utf-8"))
        actual = {
            path: set(document["paths"][path])
            for path in document["paths"]
            if path.startswith("/api/v1/backups")
            or path.startswith("/api/v1/restore-requests")
        }
        self.assertEqual(actual, PATHS)
        self.assertEqual(sum(len(methods) for methods in PATHS.values()), 7)
        for path, methods in PATHS.items():
            for method in methods:
                operation = document["paths"][path][method]
                parameters = {
                    item.get("name") if "$ref" not in item else
                    document["components"]["parameters"][item["$ref"].rsplit("/", 1)[1]]["name"]
                    for item in operation.get("parameters", [])
                }
                if method == "post":
                    self.assertIn("Idempotency-Key", parameters)
                if path.endswith("/execute") or path.endswith("/cancel"):
                    self.assertIn("If-Match", parameters)

    def test_internal_recovery_codes_map_only_to_existing_safe_codes(self) -> None:
        document = json.loads(OPENAPI.read_text(encoding="utf-8"))
        public_codes = set(document["components"]["schemas"]["SafeErrorCode"]["enum"])
        expected = {
            "BACKUP_MANIFEST_INVALID": "INVALID_REQUEST",
            "BACKUP_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
            "CURRENT_ACCESS_DENIED": "CURRENT_ACCESS_DENIED",
            "FIXTURE_ONLY_RESTORE_REQUIRED": "CURRENT_ACCESS_DENIED",
            "IDEMPOTENCY_KEY_REUSED": "INVALID_REQUEST",
            "IN_PLACE_RESTORE_FORBIDDEN": "CURRENT_ACCESS_DENIED",
            "RECOVERY_CONTEXT_INVALID": "INVALID_REQUEST",
            "RECOVERY_INPUT_INVALID": "INVALID_REQUEST",
            "RECOVERY_VERSION_CONFLICT": "INVALID_REQUEST",
            "RESTORE_EXECUTION_FAILED": "INVALID_REQUEST",
            "RESTORE_PREVIEW_STALE": "INVALID_REQUEST",
            "RESTORE_REQUEST_UNAVAILABLE": "RESOURCE_UNAVAILABLE",
            "RESTORE_STATE_INVALID": "INVALID_REQUEST",
            "STEP_UP_REQUIRED": "STEP_UP_REQUIRED",
        }
        mapped = {code: _recovery_public_error_code(code) for code in expected}
        self.assertEqual(mapped, expected)
        self.assertTrue(set(mapped.values()).issubset(public_codes))


if __name__ == "__main__":
    unittest.main()
