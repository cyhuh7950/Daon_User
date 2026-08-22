from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services" / "api" / "migrations" / "versions" / "0014_offline_studio_sync.py"


class OfflineStudioSyncMigrationContractTests(unittest.TestCase):
    def test_migration_declares_versioned_items_grants_rls_and_downgrade_guard(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            'revision = "0014"',
            'down_revision = "0013"',
            "offline_knowledge_copy_grants",
            "item_kind",
            "output_version_id",
            "dependency_item_ids",
            "target_output_version_id",
            "source_version",
            "output_version",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "OFFLINE_STUDIO_DOWNGRADE_BLOCKED",
            "validate_sync_dependency_item_ids",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
