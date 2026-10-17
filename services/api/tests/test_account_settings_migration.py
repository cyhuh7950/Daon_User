"""Offline checks for the exact Alembic op.execute(str) SQL boundary."""
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects import postgresql


def test_account_migration_sql_has_no_accidental_bind_parameters():
    source = Path(__file__).resolve().parents[1] / "migrations/versions/0067_account_settings.sql"
    statement = text(source.read_text(encoding="utf-8"))
    assert statement._bindparams == {}
    assert statement.compile(dialect=postgresql.dialect()).params == {}
