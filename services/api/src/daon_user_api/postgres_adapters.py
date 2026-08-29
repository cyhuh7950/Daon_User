"""PostgreSQL compatibility adapters for the local domain repositories."""
from __future__ import annotations

import re
import sqlite3
from threading import RLock
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from .authorization import AuthorizationError, SqliteAuthorizationRepository
from .organization_membership import OrganizationWorkflowError, SqliteOrganizationRepository


class PostgresCompatConnection:
    def __init__(self, dsn: str, prefixes: Iterable[str]) -> None:
        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        self._prefixes = tuple(prefixes)

    def _sql(self, statement: str) -> str:
        ignored = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", statement, re.I))
        statement = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", statement, flags=re.I)
        statement = re.sub(r"BEGIN\s+IMMEDIATE", "BEGIN", statement, flags=re.I)
        for prefix in self._prefixes:
            if prefix == "organization_":
                continue
            statement = re.sub(rf"\b{re.escape(prefix)}([A-Za-z0-9_]+)\b", rf"identity_{prefix}\1", statement)
        if "organization_" in self._prefixes:
            for table in ("organization_creation_requests", "invitation_codes", "organization_join_requests", "tenant_memberships", "tenant_membership_role_history", "organization_idempotency"):
                statement = re.sub(rf"\b{table}\b", f"identity_org_{table.removeprefix('organization_')}", statement)
        statement = statement.replace("?", "%s")
        if ignored and "ON CONFLICT" not in statement.upper():
            statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return statement

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> Any:
        if statement.lstrip().upper().startswith("PRAGMA"):
            value = 1
            class _Pragma:
                def fetchone(self) -> tuple[int]: return (value,)
                def fetchall(self) -> list[tuple[int]]: return [(value,)]
            return _Pragma()
        try:
            return self._conn.execute(self._sql(statement), params)
        except psycopg.errors.UniqueViolation as error:
            raise sqlite3.IntegrityError(str(error)) from error
        except psycopg.errors.ForeignKeyViolation as error:
            raise sqlite3.IntegrityError(str(error)) from error
        except psycopg.Error as error:
            raise sqlite3.OperationalError(str(error)) from error

    @property
    def in_transaction(self) -> bool:
        return self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def close(self) -> None:
        self._conn.close()


class PostgresAuthorizationRepository(SqliteAuthorizationRepository):
    def __init__(self, dsn: str) -> None:
        self._dsn, self._lock, self._closed = dsn, RLock(), False
        connection = self._connect()
        try: connection.execute("SELECT 1")
        finally: connection.close()

    def _connect(self) -> PostgresCompatConnection:
        return PostgresCompatConnection(self._dsn, ("auth_",))


class PostgresOrganizationRepository(SqliteOrganizationRepository):
    def __init__(self, dsn: str, *, audit_sink: Any = None) -> None:
        self._dsn, self._lock, self._closed, self._audit_sink = dsn, RLock(), False, audit_sink
        connection = self._connect()
        try: connection.execute("SELECT 1")
        finally: connection.close()

    def _connect(self) -> PostgresCompatConnection:
        return PostgresCompatConnection(self._dsn, ("organization_",))
