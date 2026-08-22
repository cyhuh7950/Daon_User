from __future__ import annotations

"""Connector contracts for persistent MCP-like sources.

Connectors are server-owned resources.  The browser receives only safe
metadata and never receives credentials or the upstream URL as a request
target.  A connector can be unavailable without being removed.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable
from uuid import uuid4


CONNECTOR_KINDS = frozenset({"mcp", "daon_approved_knowledge"})
CONNECTOR_STATUSES = frozenset({"connected", "disconnected", "unavailable"})


class ConnectorError(ValueError):
    """Safe, stable connector contract error."""


@dataclass(frozen=True, slots=True)
class ConnectorSource:
    source_id: str
    connector_id: str
    title: str
    source_state: str = "ready"
    usable: bool = True
    content_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectorView:
    connector_id: str
    kind: str
    name: str
    status: str
    source_count: int
    endpoint_label: str
    last_checked_at: str | None
    error_code: str | None = None


@dataclass(slots=True)
class Connector:
    connector_id: str
    kind: str
    name: str
    endpoint_label: str
    reconnect: Callable[[], bool]
    sources: list[ConnectorSource] = field(default_factory=list)
    status: str = "unavailable"
    error_code: str | None = "CONNECTOR_UNAVAILABLE"
    last_checked_at: str | None = None

    def view(self) -> ConnectorView:
        return ConnectorView(
            self.connector_id, self.kind, self.name, self.status,
            len(self.sources), self.endpoint_label, self.last_checked_at,
            self.error_code,
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ConnectorRegistry:
    """Small registry used by the runtime boundary.

    Persistence can be supplied by a later Postgres adapter without changing
    the HTTP contract.  The registry deliberately keeps unavailable records.
    """

    def __init__(self, connectors: Iterable[Connector] = ()) -> None:
        self._connectors: dict[str, Connector] = {item.connector_id: item for item in connectors}

    def list(self) -> tuple[ConnectorView, ...]:
        return tuple(item.view() for item in self._connectors.values())

    def get(self, connector_id: str) -> Connector:
        try:
            return self._connectors[connector_id]
        except KeyError as exc:
            raise ConnectorError("CONNECTOR_NOT_FOUND") from exc

    def register(self, connector: Connector) -> ConnectorView:
        if connector.kind not in CONNECTOR_KINDS:
            raise ConnectorError("CONNECTOR_KIND_UNSUPPORTED")
        if connector.status not in CONNECTOR_STATUSES:
            raise ConnectorError("CONNECTOR_STATUS_INVALID")
        if not connector.connector_id or not connector.name:
            raise ConnectorError("CONNECTOR_INPUT_INVALID")
        self._connectors[connector.connector_id] = connector
        return connector.view()

    def reconnect(self, connector_id: str) -> ConnectorView:
        connector = self.get(connector_id)
        connector.last_checked_at = _now()
        try:
            available = bool(connector.reconnect())
        except Exception:  # connector failures become safe unavailable state
            available = False
        connector.status = "connected" if available else "unavailable"
        connector.error_code = None if available else "CONNECTOR_UNAVAILABLE"
        return connector.view()

    def disconnect(self, connector_id: str) -> ConnectorView:
        connector = self.get(connector_id)
        connector.status = "disconnected"
        connector.error_code = "CONNECTOR_DISCONNECTED"
        connector.last_checked_at = _now()
        return connector.view()

    def unregister(self, connector_id: str) -> None:
        """Remove a user-owned MCP registration immediately.

        The fixed Daon knowledge connector is never user-deletable. Removing
        an MCP registration only removes this local binding; it cannot delete
        anything from the remote MCP server.
        """
        connector = self.get(connector_id)
        if connector.kind == "daon_approved_knowledge":
            raise ConnectorError("CONNECTOR_FIXED")
        if connector.kind != "mcp":
            raise ConnectorError("CONNECTOR_KIND_UNSUPPORTED")
        del self._connectors[connector_id]

    def sources(self, connector_id: str) -> tuple[ConnectorSource, ...]:
        connector = self.get(connector_id)
        if connector.status != "connected":
            return tuple(
                ConnectorSource(item.source_id, item.connector_id, item.title, "unavailable", False, item.content_digest)
                for item in connector.sources
            )
        return tuple(connector.sources)


def create_open_law_connector(*, api_key: str | None = None) -> Connector:
    """Create the National Law Information Center sample Connector.

    The upstream endpoint is intentionally server-side metadata only.  A
    missing key remains ``unavailable`` rather than pretending the connector
    is connected or deleting it.
    """

    def probe() -> bool:
        return bool(api_key)

    return Connector(
        connector_id="mcp-open-law-go-kr",
        kind="mcp",
        name="국가법령정보센터",
        endpoint_label="open.law.go.kr",
        reconnect=probe,
        status="connected" if api_key else "unavailable",
        error_code=None if api_key else "CONNECTOR_CREDENTIAL_REQUIRED",
    )


def connector_source_id(connector_id: str, remote_id: str) -> str:
    if not connector_id or not remote_id:
        raise ConnectorError("CONNECTOR_SOURCE_ID_INVALID")
    return f"{connector_id}:{remote_id}"


__all__ = [
    "CONNECTOR_KINDS", "CONNECTOR_STATUSES", "Connector", "ConnectorError",
    "ConnectorRegistry", "ConnectorSource", "ConnectorView", "connector_source_id",
    "create_open_law_connector",
]
