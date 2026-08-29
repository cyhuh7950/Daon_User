"""PostgreSQL adapter for the identity contract.

The domain service intentionally keeps the SQLite contract while this adapter
maps it to the isolated ``identity_*`` PostgreSQL tables.  This keeps the
existing Cloud tables (whose keys have different semantics) untouched.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .identity import IdentityError, SqliteIdentityRepository


_TABLES = (
    "tenants",
    "users",
    "email_verification_tokens",
    "password_reset_tokens",
    "memberships",
    "devices",
    "sessions",
    "refresh_families",
    "refresh_tokens",
    "session_audit_outbox",
    "oidc_transactions",
    "tenant_step_up_actions",
    "step_up_authorizations",
    "step_up_idempotency",
    "step_up_consumptions",
)


class _CursorProxy:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return list(self._cursor.fetchall())

    def __iter__(self):
        return iter(self._cursor)


class _PostgresCompatConnection:
    def __init__(self, dsn: str) -> None:
        self._connection = psycopg.connect(dsn, row_factory=dict_row)

    @staticmethod
    def _sql(statement: str) -> str:
        ignored_insert = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", statement, flags=re.I))
        statement = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", statement, flags=re.I)
        if statement.lstrip().upper().startswith("BEGIN IMMEDIATE"):
            statement = re.sub(r"BEGIN\s+IMMEDIATE", "BEGIN", statement, flags=re.I)
        for table in _TABLES:
            statement = re.sub(rf"\b{table}\b", f"identity_{table}", statement)
        statement = statement.replace("?", "%s")
        if ignored_insert and "ON CONFLICT" not in statement.upper():
            statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return statement

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> _CursorProxy:
        if statement.lstrip().upper().startswith("PRAGMA"):
            pragma = statement.lower()
            value: Any = 1 if "foreign_keys" in pragma or "user_version" in pragma else "read-write"
            class _PragmaCursor:
                def fetchone(self) -> tuple[Any]:
                    return (value,)
                def fetchall(self) -> list[tuple[Any]]:
                    return [(value,)]
            return _CursorProxy(_PragmaCursor())
        try:
            return _CursorProxy(self._connection.execute(self._sql(statement), params))
        except psycopg.Error as error:
            raise sqlite3.OperationalError(str(error)) from error

    def close(self) -> None:
        self._connection.close()


class PostgresIdentityRepository(SqliteIdentityRepository):
    """Identity repository backed by PostgreSQL rather than a runtime file."""

    def __init__(self, dsn: str) -> None:
        self._lock = RLock()
        self._closed = False
        self._dsn = dsn
        connection = None
        try:
            connection = self._connect()
            connection.execute("SELECT 1")
        except (psycopg.Error, sqlite3.Error) as error:
            raise IdentityError("PERSISTENCE_UNAVAILABLE", 503) from error
        finally:
            if connection is not None:
                connection.close()

    def _connect(self) -> _PostgresCompatConnection:
        return _PostgresCompatConnection(self._dsn)
