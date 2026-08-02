from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


class LocalConversationError(ValueError):
    pass


@dataclass(frozen=True)
class LocalCitation:
    source_id: str
    source_version: int
    page: int
    context: str


@dataclass(frozen=True)
class LocalConversationResult:
    run_id: str
    status: str
    citations: tuple[LocalCitation, ...]
    egress: str


class LocalConversation:
    def __init__(self, tenant_id: str, workspace_id: str, *, model_id: str | None, network_online: bool) -> None:
        if not tenant_id or not workspace_id:
            raise LocalConversationError("WORKSPACE_SCOPE_INVALID")
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.model_id = model_id
        self.network_online = network_online

    def ask(self, question: str, sources: list[tuple[object, ...]]) -> LocalConversationResult:
        if not self.model_id:
            raise LocalConversationError("LOCAL_MODEL_UNAVAILABLE")
        citations: list[LocalCitation] = []
        for source in sources:
            if len(source) not in {4, 5}:
                raise LocalConversationError("SOURCE_CONTRACT_INVALID")
            source_id, version, page, context = source[:4]
            realm = source[4] if len(source) == 5 else "local_private"
            if realm != "local_private":
                raise LocalConversationError("LOCAL_PRIVATE_SOURCE_REQUIRED")
            if question.lower() in str(context).lower() or str(context).lower() in question.lower():
                citations.append(LocalCitation(str(source_id), int(version), int(page), str(context)))
        return LocalConversationResult(f"run-{uuid4().hex}", "sufficient" if citations else "insufficient", tuple(citations), "none")
