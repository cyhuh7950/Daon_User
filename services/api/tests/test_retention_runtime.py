from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daon_user_api.runtime import RuntimeSettings, build_dependencies, create_app


class RetentionRuntimeContractTests(unittest.TestCase):
    def test_runtime_registers_exact_approved_retention_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dependencies = build_dependencies(RuntimeSettings.for_test(
                database_path=Path(directory) / "runtime.sqlite3",
                policy_version="retention-policy-v1",
            ))
            try:
                paths = create_app(dependencies).openapi()["paths"]
            finally:
                dependencies.close()
        expected = {
            "/api/v1/sources/{id}/deletion-requests",
            "/api/v1/deletion-requests/{id}",
            "/api/v1/deletion-requests/{id}/cancel",
            "/api/v1/deletion-requests/{id}/purge",
            "/api/v1/sources/{id}/legal-holds",
            "/api/v1/legal-holds/{id}/release",
            "/api/v1/workspaces/{id}/notebooks/{notebook_id}/deletion-requests/{request_id}",
        }
        self.assertEqual(
            {path for path in paths if "deletion-request" in path or "legal-hold" in path},
            expected,
        )


if __name__ == "__main__":
    unittest.main()
