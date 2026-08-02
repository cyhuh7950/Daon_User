from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


class ConnectorError(ValueError):
    pass


@dataclass(frozen=True)
class ApprovedKnowledge:
    knowledge_id: str
    text: str
    version: int
    expires_at: str


class ApprovedKnowledgeConnector:
    def __init__(self, *, token: str | None, timeout_seconds: int, max_retries: int) -> None:
        if timeout_seconds <= 0 or max_retries < 0:
            raise ValueError("invalid connector policy")
        self._token = token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.status = "connected" if token else "disconnected"
        self._items: dict[str, ApprovedKnowledge] = {}

    def publish(self, knowledge_id: str, text: str, *, version: int, expires_at: str) -> None:
        if not knowledge_id or not text or version < 1:
            raise ValueError("invalid approved knowledge")
        self._items[knowledge_id] = ApprovedKnowledge(knowledge_id, text, version, expires_at)

    def _authorize(self, permission: str) -> None:
        if self.status != "connected":
            raise ConnectorError("CONNECTOR_DISCONNECTED")
        if not self._token or permission != "read":
            raise ConnectorError("PERMISSION_REQUIRED")

    @staticmethod
    def _valid(item: ApprovedKnowledge) -> bool:
        expires = datetime.fromisoformat(item.expires_at.replace("Z", "+00:00"))
        return expires > datetime.now(timezone.utc)

    def read(self, knowledge_id: str, *, permission: str) -> ApprovedKnowledge:
        self._authorize(permission)
        item = self._items.get(knowledge_id)
        if item is None:
            raise ConnectorError("KNOWLEDGE_NOT_FOUND")
        if not self._valid(item):
            raise ConnectorError("KNOWLEDGE_EXPIRED")
        return item

    def search(self, query: str, *, permission: str) -> list[ApprovedKnowledge]:
        self._authorize(permission)
        return [item for item in self._items.values() if query.lower() in item.text.lower() and self._valid(item)]

    def disconnect(self) -> None:
        self.status = "disconnected"

    def reconnect(self) -> None:
        if not self._token:
            raise ConnectorError("PERMISSION_REQUIRED")
        self.status = "connected"
