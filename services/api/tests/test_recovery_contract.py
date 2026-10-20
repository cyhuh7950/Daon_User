from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from pydantic import ValidationError

from daon_user_api.runtime import BackupCreateBody, _recovery_public_error_code


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services" / "api" / "migrations" / "versions" / "0006_backup_restore_recovery.py"
OPENAPI = ROOT / "packages" / "contracts" / "openapi" / "v1" / "openapi.json"
PATHS = {
    "/api/v1/backups/selection-previews": {"post"},
    "/api/v1/backups": {"get", "post"},
    "/api/v1/backups/{id}": {"get"},
    "/api/v1/backups/{id}/restore-previews": {"post"},
    "/api/v1/restore-requests/{id}": {"get"},
    "/api/v1/restore-requests/{id}/execute": {"post"},
    "/api/v1/restore-requests/{id}/cancel": {"post"},
}


class RecoveryContractTests(unittest.TestCase):
    def test_l1_selection_id_schema_rejects_unsafe_and_over_128_without_changing_opaque_id(self) -> None:
        schemas = json.loads(OPENAPI.read_text(encoding="utf-8"))["components"]["schemas"]
        selected = schemas["BackupSelectionCreateRequest"]["properties"]
        self.assertEqual(schemas["OpaqueId"]["maxLength"], 256)
        self.assertNotIn("pattern", schemas["OpaqueId"])
        for field, reference in (("workspace_id", selected["workspace_id"]), ("notebook_id", selected["notebook_id"]),
                                 ("source_version_ids", selected["source_version_ids"]["items"])):
            schema = schemas[reference["$ref"].rsplit("/", 1)[1]] if "$ref" in reference else reference
            self.assertEqual((schema["type"], schema["minLength"], schema["maxLength"]), ("string", 1, 128))
            # 승인된 string/length/pattern 세 facet을 실제 입력에 적용하는 집중 검사다.
            def accepts(value):
                return (isinstance(value, str) and schema["minLength"] <= len(value) <= schema["maxLength"]
                        and re.search(schema["pattern"], value) is not None)
            def runtime_accepts(value):
                body = {"workspace_id": "workspace-a", "notebook_id": "notebook-a", "trigger": "manual",
                        "source_version_ids": ["version-a"]}
                body[field] = [value] if field == "source_version_ids" else value
                try:
                    BackupCreateBody.model_validate(body)
                    return True
                except ValidationError:
                    return False
            for value in ("a", "a" * 128, "Id_1.a:b-c"):
                self.assertTrue(accepts(value))
                self.assertEqual(accepts(value), runtime_accepts(value))
            for value in ("", "a" * 129, "../path", "with space", "한글", "-bad", "a\n", "a\x00", 1, None):
                with self.subTest(value_type=type(value).__name__, length=len(value) if isinstance(value, str) else None):
                    self.assertFalse(accepts(value))
                    self.assertEqual(accepts(value), runtime_accepts(value))

    def test_l1_static_contract_has_exact_disjoint_one_of_and_selection_bounds(self) -> None:
        document = json.loads(OPENAPI.read_text(encoding="utf-8"))
        schema = document["components"]["schemas"]["BackupCreateRequest"]
        self.assertEqual(len(schema["oneOf"]), 3)
        branches = [document["components"]["schemas"][item["$ref"].rsplit("/", 1)[1]] for item in schema["oneOf"]]
        selected = next(branch for branch in branches if "source_version_ids" in branch["properties"])
        self.assertFalse(selected["additionalProperties"])
        self.assertEqual(set(selected["required"]), {"workspace_id", "notebook_id", "trigger", "source_version_ids"})
        self.assertEqual(set(selected["properties"]), set(selected["required"]))
        self.assertEqual(selected["properties"]["trigger"]["enum"], ["manual"])
        ids = selected["properties"]["source_version_ids"]
        self.assertEqual((ids["minItems"], ids["maxItems"], ids["uniqueItems"]), (1, 100, True))
        self.assertIn("413", document["paths"]["/api/v1/backups"]["post"]["responses"])

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

    def test_openapi_exposes_exact_eight_operations_and_write_guards(self) -> None:
        document = json.loads(OPENAPI.read_text(encoding="utf-8"))
        actual = {
            path: set(document["paths"][path])
            for path in document["paths"]
            if path.startswith("/api/v1/backups")
            or path.startswith("/api/v1/restore-requests")
        }
        self.assertEqual(actual, PATHS)
        self.assertEqual(sum(len(methods) for methods in PATHS.values()), 8)
        for path, methods in PATHS.items():
            for method in methods:
                operation = document["paths"][path][method]
                parameters = {
                    item.get("name") if "$ref" not in item else
                    document["components"]["parameters"][item["$ref"].rsplit("/", 1)[1]]["name"]
                    for item in operation.get("parameters", [])
                }
                if method == "post" and path != "/api/v1/backups/selection-previews":
                    self.assertIn("Idempotency-Key", parameters)
                if path.endswith("/execute") or path.endswith("/cancel"):
                    self.assertIn("If-Match", parameters)

    def test_internal_recovery_codes_map_only_to_existing_safe_codes(self) -> None:
        document = json.loads(OPENAPI.read_text(encoding="utf-8"))
        public_codes = set(document["components"]["schemas"]["SafeErrorCode"]["enum"])
        expected = {
            "BACKUP_CAPTURE_CLEANUP_UNCERTAIN": "RESOURCE_UNAVAILABLE",
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
