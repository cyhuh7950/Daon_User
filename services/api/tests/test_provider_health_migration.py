from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


MIGRATION = Path(__file__).parents[1] / "migrations/versions/0041_provider_health_check_settings.py"


class Operations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def test_migration_0041_creates_global_sixty_minute_setting() -> None:
    spec = importlib.util.spec_from_file_location("provider_health_0041", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operations = Operations()
    with patch.object(module, "op", operations):
        module.upgrade()
    sql = "\n".join(operations.statements)
    assert "CREATE TABLE system_provider_health_settings" in sql
    assert "interval_minutes integer NOT NULL DEFAULT 60" in sql
    assert "interval_minutes BETWEEN 1 AND 1440" in sql
    assert "VALUES ('global')" in sql
