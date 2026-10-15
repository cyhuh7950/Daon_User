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

    def get(self, tenant_id: str, workspace_id: str, actor_id: str, connector_id: str) -> ConnectorView:
        """Reconnect must resolve a real scoped registration, never Registry state."""
        with self._store._transaction(self._context(tenant_id, workspace_id, actor_id)) as connection:
            row = connection.execute(
                "SELECT connector_id,kind,name,status,source_count,endpoint_label,last_checked_at,error_code FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s AND connector_id=%s",
                (tenant_id, workspace_id, connector_id),
            ).fetchone()
        if row is None:
            raise ConnectorError("CONNECTOR_NOT_FOUND")
        return self._view(row)

    def list(self, tenant_id: str, workspace_id: str, actor_id: str, defaults: Iterable[ConnectorView] = ()) -> tuple[ConnectorView, ...]:
        now = datetime.now(timezone.utc)
        context = self._context(tenant_id, workspace_id, actor_id)
        with self._store._transaction(context) as connection:
            existing = connection.execute(
                "SELECT 1 FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s LIMIT 1",
                (tenant_id, workspace_id),
            ).fetchone()
            # Preserve non-law defaults for empty workspaces. Law registration
            # is explicit only, including after removal of the last row.
            if existing is None:
                for item in defaults:
                    if item.connector_id == 'mcp-open-law-go-kr':
                        continue
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
            if connector_id == 'mcp-open-law-go-kr':
                registered = connection.execute(
                    'SELECT connector_id FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s AND connector_id=%s FOR UPDATE',
                    (tenant_id, workspace_id, connector_id),
                ).fetchone()
                if registered is None:
                    raise ConnectorError('CONNECTOR_NOT_FOUND')
                # Query trusted canonical markers rather than snapshot rows:
                # snapshot RLS is actor/Notebook scoped and must not hide a
                # foreign binding from this workspace-wide conflict check.
                bindings = connection.execute(
                    "SELECT b.notebook_id,b.record_id,b.version_id,n.created_by FROM notebook_bindings b "
                    "JOIN notebooks n ON n.tenant_id=b.tenant_id AND n.workspace_id=b.workspace_id AND n.notebook_id=b.notebook_id "
                    "JOIN source_versions v ON v.tenant_id=b.tenant_id AND v.workspace_id=b.workspace_id AND v.record_id=b.version_id AND v.source_id=b.record_id "
                    "WHERE b.tenant_id=%s AND b.workspace_id=%s AND b.binding_kind='source' "
                    "AND v.canonical_json->>'origin_kind'='mcp_open_law' AND v.canonical_json->>'connector_id'=%s "
                    "AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id AND u.workspace_id=b.workspace_id "
                    "AND u.notebook_id=b.notebook_id AND u.source_id=b.record_id AND u.source_version_id=b.version_id) "
                    "ORDER BY b.notebook_id,b.record_id",
                    (tenant_id, workspace_id, connector_id),
                ).fetchall()
                if any(str(row[3]) != actor_id for row in bindings):
                    raise ConnectorError('OPEN_LAW_BINDING_CONFLICT')
                for notebook_id, source_id, version_id, _ in bindings:
                    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                        (f'unbind-notebook|{tenant_id}|{workspace_id}|{notebook_id}',))
                    unbound = connection.execute(
                        'INSERT INTO notebook_source_unbindings(tenant_id,workspace_id,notebook_id,source_id,source_version_id,binding_version,actor_id,occurred_at) '
                        'SELECT %s,%s,%s,%s,%s,2+count(*),%s,now() FROM notebook_source_unbindings WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s '
                        'ON CONFLICT DO NOTHING RETURNING source_id',
                        (tenant_id,workspace_id,notebook_id,source_id,version_id,actor_id,tenant_id,workspace_id,notebook_id),
                    ).fetchone()
                    if unbound is None:
                        continue
                    connection.execute(
                        "INSERT INTO notebook_activities(tenant_id,workspace_id,notebook_id,sequence,activity_kind,actor_id,occurred_at) "
                        "SELECT %s,%s,%s,coalesce(max(sequence),0)+1,'context_unbound',%s,now() FROM notebook_activities "
                        'WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s',
                        (tenant_id,workspace_id,notebook_id,actor_id,tenant_id,workspace_id,notebook_id),
                    )
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
