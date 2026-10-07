"""PostgreSQL compatibility adapters for the local domain repositories."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from threading import RLock
from typing import Any, Iterable, Iterator

import psycopg
from psycopg.rows import dict_row

from .authorization import AuthorizationError, SqliteAuthorizationRepository
from .organization_membership import OrganizationWorkflowError, SqliteOrganizationRepository

_ADMIN_ROLE_AUDIT_LOCK_TIMEOUT_SECONDS = 2.0


class _RowProxy(dict[str, Any]):
    """Allow SQLite-style positional and PostgreSQL mapping row access."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def _compat_row(row: Any) -> Any:
    return _RowProxy(row) if isinstance(row, dict) else row


class _CursorProxy:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> Any:
        return _compat_row(self._cursor.fetchone())

    def fetchall(self) -> list[Any]:
        return [_compat_row(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        return (_compat_row(row) for row in self._cursor)

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class PostgresCompatConnection:
    def __init__(
        self, dsn: str, prefixes: Iterable[str], *,
        connect_timeout: int | None = None, options: str | None = None,
    ) -> None:
        connection_options: dict[str, Any] = {}
        if connect_timeout is not None:
            connection_options["connect_timeout"] = connect_timeout
        if options is not None:
            connection_options["options"] = options
        self._conn = psycopg.connect(dsn, row_factory=dict_row, **connection_options)
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
            return _CursorProxy(self._conn.execute(self._sql(statement), params))
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

    @contextmanager
    def _admin_audit_transaction(self) -> Iterator[PostgresCompatConnection]:
        """Bound only C9 outbox projection I/O, not ordinary authorization calls."""
        if not self._lock.acquire(timeout=_ADMIN_ROLE_AUDIT_LOCK_TIMEOUT_SECONDS):
            raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503)
        try:
            self._ensure_open()
            connection: PostgresCompatConnection | None = None
            try:
                connection = PostgresCompatConnection(
                    self._dsn, ("auth_",), connect_timeout=2,
                    options="-c statement_timeout=5000 -c lock_timeout=2000",
                )
                connection.execute("BEGIN")
                yield connection
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                if connection is not None:
                    connection.execute("ROLLBACK")
                raise AuthorizationError("PERSISTENCE_CONFLICT", 409) from error
            except (sqlite3.Error, psycopg.Error) as error:
                if connection is not None:
                    connection.execute("ROLLBACK")
                raise AuthorizationError("PERSISTENCE_UNAVAILABLE", 503) from error
            except Exception:
                if connection is not None:
                    connection.execute("ROLLBACK")
                raise
            finally:
                if connection is not None:
                    connection.close()
        finally:
            self._lock.release()

    def lock_admin_workspace(self, connection: PostgresCompatConnection, tenant_id: str, workspace_id: str):
        return connection.execute(
            "SELECT * FROM auth_workspaces WHERE tenant_id=? AND workspace_id=? FOR UPDATE",
            (tenant_id, workspace_id),
        ).fetchone()


class PostgresOrganizationRepository(SqliteOrganizationRepository):
    def __init__(self, dsn: str, *, audit_sink: Any = None) -> None:
        self._dsn, self._lock, self._closed, self._audit_sink = dsn, RLock(), False, audit_sink
        connection = self._connect()
        try: connection.execute("SELECT 1")
        finally: connection.close()

    def _connect(self) -> PostgresCompatConnection:
        return PostgresCompatConnection(self._dsn, ("organization_",))
