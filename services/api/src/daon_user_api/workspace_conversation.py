from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


class ConversationError(ValueError):
    pass


@dataclass(frozen=True)
class ConversationCitation:
    source_id: str
    source_version: int
    page: int
    context: str


@dataclass(frozen=True)
class ConversationResult:
    run_id: str
    status: str
    citations: tuple[ConversationCitation, ...]


class WorkspaceConversation:
    def __init__(self, tenant_id: str, workspace_id: str, *, data_realm: str) -> None:
        if not tenant_id or not workspace_id or data_realm not in {"cloud_sync", "local_private"}:
            raise ConversationError("WORKSPACE_SCOPE_INVALID")
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.data_realm = data_realm

    def ask(self, question: str, sources: list[tuple[object, ...]]) -> ConversationResult:
        if not question.strip():
            raise ConversationError("QUESTION_REQUIRED")
        citations: list[ConversationCitation] = []
        for source in sources:
            if len(source) not in {4, 5}:
                raise ConversationError("SOURCE_CONTRACT_INVALID")
            source_id, version, page, context = source[:4]
            source_realm = source[4] if len(source) == 5 else "cloud_sync"
            if source_realm != self.data_realm:
                continue
            if question.lower() in str(context).lower() or str(context).lower() in question.lower():
                citations.append(ConversationCitation(str(source_id), int(version), int(page), str(context)))
        return ConversationResult(f"run-{uuid4().hex}", "sufficient" if citations else "insufficient", tuple(citations))
