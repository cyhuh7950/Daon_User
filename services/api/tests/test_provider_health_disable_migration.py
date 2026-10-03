from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


MIGRATION = Path(__file__).parents[1] / "migrations/versions/0051_provider_health_disable.py"


def test_migration_allows_zero_without_changing_existing_sixty_minute_default() -> None:
    assert MIGRATION.exists(), "approved migration for disabled health checks is missing"
    spec = importlib.util.spec_from_file_location("provider_health_0051", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements: list[str] = []

    class Operations:
        def execute(self, statement: str) -> None:
            statements.append(statement)

    with patch.object(module, "op", Operations()):
        module.upgrade()
    sql = "\n".join(statements)
    assert module.down_revision == "0050"
    assert "interval_minutes BETWEEN 0 AND 1440" in sql
    assert "SET DEFAULT 60" not in sql
