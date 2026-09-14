import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa


MIGRATION = Path(__file__).parents[1] / "migrations/versions/0038_admin_password_change_required.py"


class RecordingOperations:
    def __init__(self) -> None:
        self.added: list[tuple[str, sa.Column[object]]] = []
        self.dropped: list[tuple[str, str]] = []

    def add_column(self, table_name: str, column: sa.Column[object]) -> None:
        self.added.append((table_name, column))

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.dropped.append((table_name, column_name))


def load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("admin_password_change_required_0038", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IdentityAdminMigrationTests(unittest.TestCase):
    def test_migration_0038_adds_and_safely_drops_password_change_required(self) -> None:
        self.assertTrue(MIGRATION.exists(), "0038 forward migration is required")
        module = load_migration()
        self.assertEqual(module.revision, "0038")
        self.assertEqual(module.down_revision, "0037")
        operations = RecordingOperations()
        with patch.object(module, "op", operations):
            module.upgrade()
            module.downgrade()

        self.assertEqual(len(operations.added), 1)
        table_name, column = operations.added[0]
        self.assertEqual(table_name, "identity_users")
        self.assertEqual(column.name, "password_change_required")
        self.assertIsInstance(column.type, sa.Boolean)
        self.assertFalse(column.nullable)
        self.assertEqual(str(column.server_default.arg), "false")
        self.assertEqual(
            operations.dropped,
            [("identity_users", "password_change_required")],
        )
