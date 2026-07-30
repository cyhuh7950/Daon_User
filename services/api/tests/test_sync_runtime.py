from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daon_user_api.runtime import RuntimeSettings, build_dependencies, create_app


class SyncRuntimeContractTests(unittest.TestCase):
    def test_runtime_registers_exact_approved_sync_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dependencies = build_dependencies(RuntimeSettings.for_test(
                database_path=Path(directory) / "runtime.sqlite3",
                policy_version="sync-policy-v1",
            ))
            try:
                paths = create_app(dependencies).openapi()["paths"]
            finally:
                dependencies.close()
        expected = {
            "/api/v1/workspaces/{id}/sync-operations",
            "/api/v1/sync-operations/{id}",
            "/api/v1/sync-operations/{id}/approve",
            "/api/v1/sync-operations/{id}/transfer-batches",
            "/api/v1/sync-operations/{id}/conflicts/{conflict_id}/resolution",
        }
        self.assertEqual({path for path in paths if "sync-operation" in path}, expected)


if __name__ == "__main__":
    unittest.main()
