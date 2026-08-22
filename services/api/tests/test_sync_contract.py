from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services" / "api" / "migrations" / "versions" / "0004_sync_copy_publish.py"
OPENAPI = ROOT / "packages" / "contracts" / "openapi" / "v1" / "openapi.json"

SYNC_PATHS = {
    "/api/v1/workspaces/{id}/sync-operations": {"get", "post"},
    "/api/v1/sync-operations/{id}": {"get"},
    "/api/v1/sync-operations/{id}/approve": {"post"},
    "/api/v1/sync-operations/{id}/transfer-batches": {"post"},
    "/api/v1/sync-operations/{id}/conflicts/{conflict_id}/resolution": {"post"},
}


class SyncContractTests(unittest.TestCase):
    def test_migration_declares_scoped_immutable_sync_contract(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            'revision = "0004"', 'down_revision = "0003"',
            "sync_operations", "sync_approval_snapshots", "sync_manifest_items",
            "sync_transfer_batches", "sync_transfer_attempts", "sync_conflicts",
            "sync_conflict_resolutions", "sync_target_versions", "sync_reindex_requests",
            "ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY",
            "SYNC_IMMUTABLE_MUTATION", "SYNC_VERSION_CONFLICT",
            "reindex_requested", "keep_local_as_new_version", "keep_cloud", "keep_both",
        ):
            self.assertIn(token, source)

    def test_openapi_exposes_only_the_five_approved_sync_paths(self) -> None:
        document = json.loads(OPENAPI.read_text(encoding="utf-8"))
        for path, methods in SYNC_PATHS.items():
            self.assertIn(path, document["paths"])
            self.assertEqual(set(document["paths"][path]), methods)
        sync_paths = {path for path in document["paths"] if "sync-operation" in path}
        self.assertEqual(sync_paths, set(SYNC_PATHS))
        for path, methods in SYNC_PATHS.items():
            for method in methods:
                operation = document["paths"][path][method]
                parameter_names = set()
                for item in operation.get("parameters", []):
                    if "$ref" in item:
                        parameter = document["components"]["parameters"][item["$ref"].rsplit("/", 1)[1]]
                    else:
                        parameter = item
                    parameter_names.add(parameter.get("name"))
                if method != "get":
                    self.assertIn("Idempotency-Key", parameter_names)
                    self.assertIn("If-Match", parameter_names)


if __name__ == "__main__":
    unittest.main()
