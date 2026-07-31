from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daon_user_api.runtime import RuntimeSettings, build_dependencies, create_app


class RecoveryRuntimeContractTests(unittest.TestCase):
    def test_runtime_registers_exact_approved_cloud_recovery_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dependencies = build_dependencies(RuntimeSettings.for_test(
                database_path=Path(directory) / "runtime.sqlite3",
                policy_version="recovery-policy-v1",
            ))
            try:
                paths = create_app(dependencies).openapi()["paths"]
            finally:
                dependencies.close()
        expected = {
            "/api/v1/backups": {"post", "get"},
            "/api/v1/backups/{id}": {"get"},
            "/api/v1/backups/{id}/restore-previews": {"post"},
            "/api/v1/restore-requests/{id}": {"get"},
            "/api/v1/restore-requests/{id}/execute": {"post"},
            "/api/v1/restore-requests/{id}/cancel": {"post"},
        }
        actual = {
            path: set(paths[path]) & {"get", "post", "put", "patch", "delete"}
            for path in paths if path.startswith("/api/v1/backups")
            or path.startswith("/api/v1/restore-requests")
        }
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
