"""Postgres persistence for workspace-scoped Connector registrations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable

from .cloud_storage import CloudAccessContext
from .mcp_connector import ConnectorError, ConnectorSource, ConnectorView


class PostgresConnectorRepository:
    def __init__(self, store) -> None:  # type: ignore[no-untyped-def]
        self._store = store

    @staticmethod
    def _context(tenant_id: str, workspace_id: str, actor_id: str) -> CloudAccessContext:
        return CloudAccessContext(tenant_id, workspace_id, actor_id, "connector.read_or_write")

    @staticmethod
    def _view(row) -> ConnectorView:  # type: ignore[no-untyped-def]
        return ConnectorView(str(row[0]), str(row[1]), str(row[2]), str(row[3]), int(row[4]), str(row[5]),
                             None if row[6] is None else row[6].isoformat().replace("+00:00", "Z"),
                             None if row[7] is None else str(row[7]))

    def list(self, tenant_id: str, workspace_id: str, actor_id: str, defaults: Iterable[ConnectorView] = ()) -> tuple[ConnectorView, ...]:
        now = datetime.now(timezone.utc)
        context = self._context(tenant_id, workspace_id, actor_id)
        with self._store._transaction(context) as connection:
            existing = connection.execute(
                "SELECT 1 FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s LIMIT 1",
                (tenant_id, workspace_id),
            ).fetchone()
            # Seed defaults only for a never-initialized workspace.  Do not
            # recreate a user-deleted MCP registration on every API restart.
            if existing is None:
                for item in defaults:
                    connection.execute(
                        "INSERT INTO workspace_connectors (tenant_id,workspace_id,connector_id,kind,name,endpoint_label,status,source_count,sources_json,last_checked_at,error_code,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'[]'::jsonb,%s,%s,%s,%s) ON CONFLICT (tenant_id,workspace_id,connector_id) DO NOTHING",
                        (tenant_id, workspace_id, item.connector_id, item.kind, item.name, item.endpoint_label, item.status,
                         item.source_count, None if item.last_checked_at is None else item.last_checked_at, item.error_code, now, now),
                    )
            rows = connection.execute(
                "SELECT connector_id,kind,name,status,source_count,endpoint_label,last_checked_at,error_code FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s ORDER BY created_at,connector_id",
                (tenant_id, workspace_id),
            ).fetchall()
        return tuple(self._view(row) for row in rows)

    def register(self, tenant_id: str, workspace_id: str, actor_id: str, item: ConnectorView) -> ConnectorView:
        now = datetime.now(timezone.utc)
        context = self._context(tenant_id, workspace_id, actor_id)
        with self._store._transaction(context) as connection:
            connection.execute(
                "INSERT INTO workspace_connectors (tenant_id,workspace_id,connector_id,kind,name,endpoint_label,status,source_count,sources_json,last_checked_at,error_code,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'[]'::jsonb,%s,%s,%s,%s) ON CONFLICT (tenant_id,workspace_id,connector_id) DO UPDATE SET kind=EXCLUDED.kind,name=EXCLUDED.name,endpoint_label=EXCLUDED.endpoint_label,status=EXCLUDED.status,source_count=EXCLUDED.source_count,last_checked_at=EXCLUDED.last_checked_at,error_code=EXCLUDED.error_code,updated_at=EXCLUDED.updated_at",
                (tenant_id, workspace_id, item.connector_id, item.kind, item.name, item.endpoint_label, item.status,
                 item.source_count, item.last_checked_at, item.error_code, now, now),
            )
        return item

    def set_status(self, tenant_id: str, workspace_id: str, actor_id: str, connector_id: str, *, status: str, error_code: str | None) -> ConnectorView:
        now = datetime.now(timezone.utc)
        context = self._context(tenant_id, workspace_id, actor_id)
        with self._store._transaction(context) as connection:
            row = connection.execute(
                "UPDATE workspace_connectors SET status=%s,error_code=%s,last_checked_at=%s,updated_at=%s WHERE tenant_id=%s AND workspace_id=%s AND connector_id=%s RETURNING connector_id,kind,name,status,source_count,endpoint_label,last_checked_at,error_code",
                (status, error_code, now, now, tenant_id, workspace_id, connector_id),
            ).fetchone()
        if row is None:
            raise ConnectorError("CONNECTOR_NOT_FOUND")
        return self._view(row)

    def unregister(self, tenant_id: str, workspace_id: str, actor_id: str, connector_id: str) -> None:
        context = self._context(tenant_id, workspace_id, actor_id)
        with self._store._transaction(context) as connection:
            result = connection.execute(
                "DELETE FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s AND connector_id=%s",
                (tenant_id, workspace_id, connector_id),
            )
        if result.rowcount == 0:
            raise ConnectorError("CONNECTOR_NOT_FOUND")

    def sources(self, tenant_id: str, workspace_id: str, actor_id: str, connector_id: str) -> tuple[ConnectorSource, ...]:
        context = self._context(tenant_id, workspace_id, actor_id)
        with self._store._transaction(context) as connection:
            row = connection.execute(
                "SELECT status,sources_json FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s AND connector_id=%s",
                (tenant_id, workspace_id, connector_id),
            ).fetchone()
        if row is None:
            raise ConnectorError("CONNECTOR_NOT_FOUND")
        values = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        if not isinstance(values, list):
            raise ConnectorError("CONNECTOR_UNAVAILABLE")
        return tuple(ConnectorSource(**item) for item in values)
