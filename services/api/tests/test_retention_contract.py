from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services" / "api" / "migrations" / "versions" / "0005_retention_legal_hold.py"
OPENAPI = ROOT / "packages" / "contracts" / "openapi" / "v1" / "openapi.json"
PATHS = {
    "/api/v1/sources/{id}/deletion-requests": {"post"},
    "/api/v1/deletion-requests/{id}": {"get"},
    "/api/v1/deletion-requests/{id}/cancel": {"post"},
    "/api/v1/deletion-requests/{id}/purge": {"post"},
    "/api/v1/sources/{id}/legal-holds": {"post"},
    "/api/v1/legal-holds/{id}/release": {"post"},
}


class RetentionContractTests(unittest.TestCase):
    def test_migration_declares_normalized_scoped_append_only_contract(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            'revision = "0005"', 'down_revision = "0004"', "deletion_requests",
            "deletion_cleanup_items", "deletion_attempts", "legal_holds",
            "legal_hold_targets", "retention_lineage", "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY", "RETENTION_IMMUTABLE_MUTATION",
            "RETENTION_VERSION_CONFLICT", "blocked_by_hold", "cleanup_pending",
        ):
            self.assertIn(token, source)

    def test_openapi_exposes_exact_six_routes_and_write_guards(self) -> None:
        document = json.loads(OPENAPI.read_text(encoding="utf-8"))
        for path, methods in PATHS.items():
            self.assertEqual(set(document["paths"][path]), methods)
            for method in methods:
                operation = document["paths"][path][method]
                parameters = {
                    item.get("name") if "$ref" not in item else
                    document["components"]["parameters"][item["$ref"].rsplit("/", 1)[1]]["name"]
                    for item in operation.get("parameters", [])
                }
                if method != "get":
                    self.assertIn("Idempotency-Key", parameters)
                    self.assertIn("If-Match", parameters)
        self.assertEqual(
            {path for path in document["paths"] if "deletion-request" in path or "legal-hold" in path},
            set(PATHS),
        )


if __name__ == "__main__":
    unittest.main()
