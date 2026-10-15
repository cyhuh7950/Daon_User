"""Postgres persistence for workspace-scoped Connector registrations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import replace
from types import SimpleNamespace
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

    @staticmethod
    def _law_sources(connection,tenant_id,workspace_id,actor_id):
        from .authorization import Action
        from .open_law_source_access import _current_auth_grant,require_open_law_source_access,OpenLawAccessError
        bindings=connection.execute(
            "SELECT b.notebook_id,b.record_id,b.version_id FROM notebook_bindings b "
            "JOIN notebooks n ON n.tenant_id=b.tenant_id AND n.workspace_id=b.workspace_id AND n.notebook_id=b.notebook_id "
            "JOIN source_versions v ON v.tenant_id=b.tenant_id AND v.workspace_id=b.workspace_id AND v.record_id=b.version_id AND v.source_id=b.record_id "
            "WHERE b.tenant_id=%s AND b.workspace_id=%s AND n.created_by=%s AND b.binding_kind='source' "
            "AND v.canonical_json->>'origin_kind'='mcp_open_law' AND v.canonical_json->>'connector_id'='mcp-open-law-go-kr' "
            "AND NOT EXISTS(SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id AND u.workspace_id=b.workspace_id AND u.notebook_id=b.notebook_id AND u.source_id=b.record_id AND u.source_version_id=b.version_id) "
            "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed')) "
            "AND NOT EXISTS(SELECT 1 FROM source_versions newer WHERE newer.tenant_id=v.tenant_id AND newer.workspace_id=v.workspace_id AND newer.source_id=v.source_id AND newer.version>v.version) "
            "ORDER BY b.notebook_id,b.record_id",(tenant_id,workspace_id,actor_id)).fetchall()
        values=[]
        for notebook,source,version in bindings:
            ctx=SimpleNamespace(tenant_id=tenant_id,workspace_id=workspace_id,actor_id=actor_id,notebook_id=str(notebook),trace_id='connector-projection',policy_version='current')
            _current_auth_grant(ctx,connection,Action.VIEW)
            rule=connection.execute('SELECT allowed FROM identity_auth_source_access WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s AND source_version_id=%s',
                (tenant_id,workspace_id,actor_id,version)).fetchone()
            if rule is not None and not rule[0]:continue
            # Each owned Notebook gets its own normal RLS setting. Never query
            # snapshots at workspace scope or disable FORCE RLS.
            connection.execute("SELECT set_config('app.notebook_id',%s,true)",(str(notebook),))
            snapshot=connection.execute("SELECT w.title,w.text_sha256 FROM open_law_source_snapshots w "
                "JOIN source_versions v ON v.tenant_id=w.tenant_id AND v.workspace_id=w.workspace_id AND v.record_id=w.source_version_id AND v.source_id=w.source_id "
                "WHERE w.tenant_id=%s AND w.workspace_id=%s AND w.created_by=%s AND w.notebook_id=%s AND w.source_id=%s AND w.source_version_id=%s "
                "AND w.snapshot_id=v.canonical_json->>'snapshot_id' AND w.raw_sha256=v.canonical_json->>'raw_sha256' AND w.text_sha256=v.canonical_json->>'text_sha256'",
                (tenant_id,workspace_id,actor_id,notebook,source,version)).fetchone()
            if snapshot is None:continue
            try:require_open_law_source_access(ctx,str(version),connection);usable=True
            except OpenLawAccessError:usable=False
            values.append(ConnectorSource(str(source),'mcp-open-law-go-kr',str(snapshot[0]),'ready' if usable else 'unavailable',usable,str(snapshot[1])))
        return tuple(values)

    def law_verification(self,tenant_id,workspace_id,actor_id):
        with self._store._transaction(self._context(tenant_id,workspace_id,actor_id)) as connection:
            row=connection.execute('SELECT connector_id,kind,name,status,source_count,endpoint_label,last_checked_at,error_code,verification_json,updated_at '
                "FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s AND connector_id='mcp-open-law-go-kr'",(tenant_id,workspace_id)).fetchone()
            if row is None:raise ConnectorError('CONNECTOR_NOT_FOUND')
            return self._view(row[:8]),row[8],row[9]

    def verify_law(self,tenant_id,workspace_id,actor_id,receipt,registration):
        from .open_law_mcp_server import PROTOCOL,SCHEMA_DIGEST
        from psycopg.types.json import Jsonb
        if (not isinstance(receipt,dict) or set(receipt)!={'protocol_version','tool_schema_digest','checked_at'}
            or receipt['protocol_version']!=PROTOCOL or receipt['tool_schema_digest']!=SCHEMA_DIGEST
            or not isinstance(receipt['checked_at'],str) or len(receipt['checked_at'])>64):raise ConnectorError('CONNECTOR_NOT_VERIFIED')
        try:datetime.fromisoformat(receipt['checked_at'].replace('Z','+00:00'))
        except ValueError:raise ConnectorError('CONNECTOR_NOT_VERIFIED') from None
        with self._store._transaction(self._context(tenant_id,workspace_id,actor_id)) as connection:
            row=connection.execute("UPDATE workspace_connectors SET status='connected',error_code=NULL,verification_json=%s,last_checked_at=now(),updated_at=now() "
                "WHERE tenant_id=%s AND workspace_id=%s AND connector_id='mcp-open-law-go-kr' AND updated_at=%s AND status!='disconnected' RETURNING connector_id",
                (Jsonb(receipt),tenant_id,workspace_id,registration)).fetchone()
            if row is None:raise ConnectorError('CONNECTOR_NOT_VERIFIED')

    def mark_law_unavailable(self,tenant_id,workspace_id,actor_id,code,registration):
        # Confirmed credential/transport failure only. Input, policy, size and
        # malformed-content failures never invalidate a working connection.
        if code not in {'OPEN_LAW_CREDENTIAL_REQUIRED','OPEN_LAW_UPSTREAM_FAILED'}:
            raise ConnectorError('CONNECTOR_NOT_VERIFIED')
        with self._store._transaction(self._context(tenant_id,workspace_id,actor_id)) as connection:
            return connection.execute("UPDATE workspace_connectors SET status='unavailable',error_code=%s,verification_json='{}'::jsonb,last_checked_at=now(),updated_at=now() "
                "WHERE tenant_id=%s AND workspace_id=%s AND connector_id='mcp-open-law-go-kr' AND status='connected' AND updated_at=%s RETURNING connector_id",
                (code,tenant_id,workspace_id,registration)).fetchone() is not None

    def get(self, tenant_id: str, workspace_id: str, actor_id: str, connector_id: str) -> ConnectorView:
        """Reconnect must resolve a real scoped registration, never Registry state."""
        with self._store._transaction(self._context(tenant_id, workspace_id, actor_id)) as connection:
            row = connection.execute(
                "SELECT connector_id,kind,name,status,source_count,endpoint_label,last_checked_at,error_code FROM workspace_connectors WHERE tenant_id=%s AND workspace_id=%s AND connector_id=%s",
                (tenant_id, workspace_id, connector_id),
            ).fetchone()
            if row is None:raise ConnectorError('CONNECTOR_NOT_FOUND')
            view=self._view(row)
            if connector_id=='mcp-open-law-go-kr':view=replace(view,source_count=len(self._law_sources(connection,tenant_id,workspace_id,actor_id)))
        return view

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
            views=tuple(replace(self._view(row),source_count=len(self._law_sources(connection,tenant_id,workspace_id,actor_id)))
                if row[0]=='mcp-open-law-go-kr' else self._view(row) for row in rows)
        return views

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
            if row is None:raise ConnectorError('CONNECTOR_NOT_FOUND')
            if connector_id=='mcp-open-law-go-kr':return self._law_sources(connection,tenant_id,workspace_id,actor_id)
        if row is None:
            raise ConnectorError("CONNECTOR_NOT_FOUND")
        values = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        if not isinstance(values, list):
            raise ConnectorError("CONNECTOR_UNAVAILABLE")
        return tuple(ConnectorSource(**item) for item in values)
