"""PostgreSQL cloud source-of-truth adapter with transaction-local RLS context."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping, Sequence, cast

from psycopg import Connection, Error, connect
from psycopg_pool import ConnectionPool, PoolTimeout


_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EXPECTED_SCHEMA_REVISION = "0017"


class _PoolAvailabilityLogFilter(logging.Filter):
    """Keep connection targets and driver failures out of operational logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.msg if isinstance(record.msg, str) else ""
        return not message.startswith(("error connecting", "reconnection attempt"))


_pool_logger = logging.getLogger("psycopg.pool")
if not any(isinstance(item, _PoolAvailabilityLogFilter) for item in _pool_logger.filters):
    _pool_logger.addFilter(_PoolAvailabilityLogFilter())


class CloudDatabaseError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def classify_database_error(sqlstate: str | None) -> CloudDatabaseError:
    if sqlstate == "57014":
        return CloudDatabaseError("DATABASE_TIMEOUT", retryable=True)
    if sqlstate in {"40P01", "40001"}:
        return CloudDatabaseError("DATABASE_RETRYABLE_CONFLICT", retryable=True)
    if sqlstate is not None and sqlstate.startswith("23"):
        return CloudDatabaseError("DATABASE_CONSTRAINT_VIOLATION", retryable=False)
    if sqlstate in {"42501", "55000"}:
        return CloudDatabaseError("DATABASE_ACCESS_DENIED", retryable=False)
    return CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True)


@dataclass(frozen=True, slots=True)
class CloudAccessContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    capability: str

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.workspace_id, self.actor_id, self.capability):
            if not isinstance(value, str) or not _SAFE_SCOPE.fullmatch(value):
                raise ValueError("CLOUD_ACCESS_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class CloudReadiness:
    ready: bool
    schema_revision: str | None
    vector_version: str | None


@dataclass(frozen=True, slots=True)
class CloudNotification:
    notification_id: str
    version: int
    read_at: datetime | None


class PostgresCloudStore:
    """A bounded pool and explicit repository port; callers supply verified scope."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4) -> None:
        if not isinstance(dsn, str) or not dsn:
            raise ValueError("CLOUD_DATABASE_DSN_REQUIRED")
        self._dsn = dsn
        self._open_lock = threading.Lock()
        self._pool = ConnectionPool[tuple[Any, ...]](
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": False},
            timeout=2.0,
            reconnect_timeout=5.0,
            open=False,
        )

    def _ensure_pool_open(self) -> None:
        if not self._pool.closed:
            return
        with self._open_lock:
            if self._pool.closed:
                self._pool.open(wait=False)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _transaction(self, context: CloudAccessContext) -> Iterator[Connection[tuple[Any, ...]]]:
        try:
            self._ensure_pool_open()
            with self._pool.connection(timeout=2.0) as connection:
                with connection.transaction():
                    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (context.tenant_id,))
                    connection.execute("SELECT set_config('app.workspace_id', %s, true)", (context.workspace_id,))
                    connection.execute("SELECT set_config('app.actor_id', %s, true)", (context.actor_id,))
                    connection.execute("SELECT set_config('app.capability', %s, true)", (context.capability,))
                    yield connection
        except CloudDatabaseError:
            raise
        except PoolTimeout:
            raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True) from None
        except Error as error:
            raise classify_database_error(error.sqlstate) from None

    def readiness(self) -> CloudReadiness:
        try:
            if self._pool.closed:
                with connect(self._dsn, connect_timeout=1) as connection:
                    revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
                    vector_row = connection.execute(
                        "SELECT extversion FROM pg_extension WHERE extname = %s", ("vector",)
                    ).fetchone()
                revision = None if revision_row is None else str(revision_row[0])
                vector_version = None if vector_row is None else str(vector_row[0])
                ready = revision == _EXPECTED_SCHEMA_REVISION and vector_version is not None
                if ready:
                    self._ensure_pool_open()
                return CloudReadiness(ready, revision, vector_version)
            with self._pool.connection(timeout=2.0) as connection:
                revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
                vector_row = connection.execute(
                    "SELECT extversion FROM pg_extension WHERE extname = %s", ("vector",)
                ).fetchone()
            revision = None if revision_row is None else str(revision_row[0])
            vector_version = None if vector_row is None else str(vector_row[0])
            return CloudReadiness(
                revision == _EXPECTED_SCHEMA_REVISION and vector_version is not None,
                revision,
                vector_version,
            )
        except (Error, PoolTimeout, OSError):
            return CloudReadiness(False, None, None)

    def context_is_clear(self) -> bool:
        try:
            self._ensure_pool_open()
            with self._pool.connection(timeout=5.0) as connection:
                row = connection.execute(
                    "SELECT nullif(current_setting('app.tenant_id', true), '') AS tenant_id, "
                    "nullif(current_setting('app.workspace_id', true), '') AS workspace_id"
                ).fetchone()
            return row is not None and row[0] is None and row[1] is None
        except PoolTimeout:
            raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True) from None
        except Error as error:
            raise classify_database_error(error.sqlstate) from None

    def seed_scope(self, context: CloudAccessContext) -> None:
        with self._transaction(context) as connection:
            connection.execute(
                "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.tenant_id),
            )
            connection.execute(
                "INSERT INTO workspaces (tenant_id, workspace_id, display_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.workspace_id, context.workspace_id),
            )
            connection.execute(
                "INSERT INTO user_accounts (tenant_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.actor_id),
            )
            connection.execute(
                "INSERT INTO memberships (tenant_id, workspace_id, user_id, role) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.workspace_id, context.actor_id, "member"),
            )

    def put_vector(self, context: CloudAccessContext, vector_id: str, embedding: Sequence[float]) -> None:
        if len(embedding) != 3 or not _SAFE_SCOPE.fullmatch(vector_id):
            raise ValueError("VECTOR_INPUT_INVALID")
        literal = "[" + ",".join(str(float(value)) for value in embedding) + "]"
        with self._transaction(context) as connection:
            connection.execute(
                "INSERT INTO vector_entries (tenant_id, workspace_id, vector_id, embedding) "
                "VALUES (%s, %s, %s, %s::vector) "
                "ON CONFLICT (tenant_id, workspace_id, vector_id) DO UPDATE SET embedding = EXCLUDED.embedding",
                (context.tenant_id, context.workspace_id, vector_id, literal),
            )

    def get_vector(self, context: CloudAccessContext, vector_id: str) -> tuple[float, ...] | None:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT embedding::text AS embedding FROM vector_entries WHERE vector_id = %s",
                (vector_id,),
            ).fetchone()
        if row is None:
            return None
        return tuple(float(value) for value in str(row[0]).strip("[]").split(","))

    def create_notification(
        self, context: CloudAccessContext, notification_id: str, source_event_id: str
    ) -> CloudNotification:
        with self._transaction(context) as connection:
            row = connection.execute(
                "INSERT INTO notifications "
                "(tenant_id, workspace_id, notification_id, recipient_id, source_event_id) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING notification_id, version, read_at",
                (context.tenant_id, context.workspace_id, notification_id, context.actor_id, source_event_id),
            ).fetchone()
        if row is None:
            raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True)
        return CloudNotification(str(row[0]), int(row[1]), cast(datetime | None, row[2]))

    def get_notification(self, context: CloudAccessContext, notification_id: str) -> CloudNotification:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT notification_id, version, read_at FROM notifications WHERE notification_id = %s",
                (notification_id,),
            ).fetchone()
        if row is None:
            raise CloudDatabaseError("DATABASE_ACCESS_DENIED", retryable=False)
        return CloudNotification(str(row[0]), int(row[1]), cast(datetime | None, row[2]))

    def mark_notification_read(
        self,
        context: CloudAccessContext,
        notification_id: str,
        expected_version: int,
        idempotency_key: str,
        *,
        force_audit_failure: bool = False,
    ) -> CloudNotification:
        fingerprint = hashlib.sha256(
            f"{notification_id}|{expected_version}|read".encode("utf-8")
        ).hexdigest()
        operation = "notification.read"
        with self._transaction(context) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{operation}|{idempotency_key}",),
            )
            replay = connection.execute(
                "SELECT request_fingerprint, result FROM idempotency_records "
                "WHERE workspace_id = %s AND actor_id = %s AND operation = %s AND idempotency_key = %s",
                (context.workspace_id, context.actor_id, operation, idempotency_key),
            ).fetchone()
            if replay is not None:
                if replay[0] != fingerprint:
                    raise CloudDatabaseError("IDEMPOTENCY_CONFLICT", retryable=False)
                result = cast(Mapping[str, Any], replay[1])
                return CloudNotification(
                    str(result["notification_id"]), int(result["version"]),
                    datetime.fromisoformat(str(result["read_at"])),
                )
            row = connection.execute(
                "UPDATE notifications SET read_at = now(), version = version + 1 "
                "WHERE notification_id = %s AND recipient_id = %s AND version = %s AND read_at IS NULL "
                "RETURNING notification_id, version, read_at",
                (notification_id, context.actor_id, expected_version),
            ).fetchone()
            if row is None:
                raise CloudDatabaseError("VERSION_CONFLICT", retryable=False)
            if force_audit_failure:
                connection.execute(
                    "INSERT INTO audit_events (event_id, tenant_id, workspace_id, actor_id, action, "
                    "target_type, target_id, outcome, trace_id, policy_version, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (None, context.tenant_id, context.workspace_id, context.actor_id, operation,
                     "notification", notification_id, "succeeded", "forced-failure", "test", "{}"),
                )
            event_id = "audit-" + hashlib.sha256(
                f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{idempotency_key}".encode("utf-8")
            ).hexdigest()[:24]
            connection.execute(
                "INSERT INTO audit_events (event_id, tenant_id, workspace_id, actor_id, action, "
                "target_type, target_id, outcome, trace_id, policy_version, after_value, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (event_id, context.tenant_id, context.workspace_id, context.actor_id, operation,
                 "notification", notification_id, "succeeded", event_id, "cloud-policy-v1",
                 json.dumps({"version": int(row[1])}), json.dumps({"idempotency_key_hash": hashlib.sha256(idempotency_key.encode()).hexdigest()})),
            )
            result = {
                "notification_id": str(row[0]),
                "version": int(row[1]),
                "read_at": cast(datetime, row[2]).isoformat(),
            }
            connection.execute(
                "INSERT INTO idempotency_records (tenant_id, workspace_id, actor_id, operation, "
                "idempotency_key, request_fingerprint, result, status, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed', %s)",
                (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key,
                 fingerprint, json.dumps(result), datetime.now(timezone.utc) + timedelta(hours=24)),
            )
            return CloudNotification(result["notification_id"], result["version"], cast(datetime, row[2]))

    def audit_count(self, context: CloudAccessContext, action: str) -> int:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT count(*) AS count FROM audit_events WHERE action = %s", (action,)
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def idempotency_count(self, context: CloudAccessContext, idempotency_key: str) -> int:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT count(*) AS count FROM idempotency_records "
                "WHERE workspace_id = %s AND actor_id = %s AND idempotency_key = %s",
                (context.workspace_id, context.actor_id, idempotency_key),
            ).fetchone()
        return int(row[0]) if row is not None else 0
