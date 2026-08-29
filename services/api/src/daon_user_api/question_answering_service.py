"""Application service for immutable multi-source grounded question runs."""

from __future__ import annotations

import json
import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass, replace
from typing import Mapping, Protocol

from .data_canon import canonical_json_bytes
from .document_processing import DocumentProcessingContext
from .document_index_postgres import IndexedEvidenceChunk
from .document_understanding_adapter import DocumentUnderstandingError
from .provider_settings import ProviderSettingsContext, ProviderSettingsService
from .question_answering import (
    GeneralConversationRequest, GroundedQuestionRequest, GroundedTextResult, TextGenerationTransport,
    OllamaTextGenerationAdapter, OpenAICompatibleTextGenerationAdapter,
    resolve_text_model_selection,
)
from .question_answering_postgres import (
    PostgresQuestionAnsweringRepository, QuestionContext, QuestionRepositoryError,
    StoredQuestionAnswer,
)


_GENERAL_CONVERSATION_INTENTS = frozenset({
    "안녕", "안녕하세요", "반가워", "반갑습니다", "고마워", "고마워요", "감사합니다",
    "도움말", "daon 사용법 알려줘", "daon 사용법을 알려줘", "다온 사용법 알려줘",
    "다온 사용법을 알려줘", "이 제품 사용법 알려줘", "이 제품 사용법을 알려줘",
})


def _normalized_intent(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    if normalized != value:
        return ""
    normalized = normalized.strip().casefold()
    normalized = re.sub(r"[.!?。！？]+$", "", normalized).strip()
    return re.sub(r"\s+", " ", normalized)


def is_general_conversation_intent(value: str) -> bool:
    return isinstance(value, str) and _normalized_intent(value) in _GENERAL_CONVERSATION_INTENTS


def question_request_fingerprint(
    context: QuestionContext, *, run_id: str, idempotency_key: str,
    request_payload: Mapping[str, object],
) -> str:
    """Bind replay only to the safe logical request and its exact scoped identity."""
    return "sha256:" + hashlib.sha256(canonical_json_bytes({
        "tenant_id": context.tenant_id,
        "workspace_id": context.workspace_id,
        "notebook_id": context.notebook_id,
        "actor_id": context.actor_id,
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "request": dict(request_payload),
    })).hexdigest()
class CredentialResolver(Protocol):
    def resolve(self, provider_code: str) -> str: ...


class DocumentIndexPort(Protocol):
    def search(self, context: DocumentProcessingContext, **kwargs): ...  # type: ignore[no-untyped-def]


class QuestionEgressPort(Protocol):
    def authorize(self, context: QuestionContext, **kwargs): ...  # type: ignore[no-untyped-def]


class QuestionAdapterRegistry:
    """Selects the grounded generator; local fixture is explicit and never calls a provider."""

    @dataclass(frozen=True, slots=True)
    class Prepared:
        request: GroundedQuestionRequest | GeneralConversationRequest
        selection: object
        adapter: object
        provider_payload: dict[str, object]

    def prepare(
        self, snapshot, evidence, question: str, trace_id: str,  # type: ignore[no-untyped-def]
        credential_resolver: CredentialResolver, transport: TextGenerationTransport,
    ) -> "QuestionAdapterRegistry.Prepared":
        selection = resolve_text_model_selection(snapshot)
        request = GroundedQuestionRequest(question.strip(), evidence, trace_id)
        if selection.provider_code == "OLLAMA":
            adapter = OllamaTextGenerationAdapter(transport=transport)
            return self.Prepared(request, selection, adapter, adapter.provider_payload(request, selection))
        if selection.provider_code not in {"GROQ", "MISTRAL", "UPSTAGE"}:
            raise ValueError("TEXT_PROVIDER_UNAVAILABLE")
        api_key = credential_resolver.resolve(selection.provider_code)
        external_adapter = OpenAICompatibleTextGenerationAdapter(
            transport=transport, api_key=api_key
        )
        return self.Prepared(
            request,
            selection,
            external_adapter,
            external_adapter.provider_payload(request, selection),
        )

    def generate_prepared(self, prepared: "QuestionAdapterRegistry.Prepared") -> GroundedTextResult:
        return prepared.adapter.generate(  # type: ignore[attr-defined]
            prepared.request, prepared.selection, provider_payload=prepared.provider_payload,
        )

    def prepare_general(
        self, snapshot, question: str, trace_id: str,  # type: ignore[no-untyped-def]
        credential_resolver: CredentialResolver, transport: TextGenerationTransport,
    ) -> "QuestionAdapterRegistry.Prepared":
        selection = resolve_text_model_selection(snapshot)
        request = GeneralConversationRequest(question.strip(), trace_id)
        if selection.provider_code == "OLLAMA":
            adapter = OllamaTextGenerationAdapter(transport=transport)
        elif selection.provider_code in {"GROQ", "MISTRAL", "UPSTAGE"}:
            adapter = OpenAICompatibleTextGenerationAdapter(
                transport=transport, api_key=credential_resolver.resolve(selection.provider_code),
            )
        else:
            raise ValueError("TEXT_PROVIDER_UNAVAILABLE")
        return self.Prepared(
            request, selection, adapter, adapter.general_provider_payload(request, selection),
        )

    def generate_general(self, prepared: "QuestionAdapterRegistry.Prepared") -> GroundedTextResult:
        return prepared.adapter.generate_general(  # type: ignore[attr-defined]
            prepared.request, prepared.selection, provider_payload=prepared.provider_payload,
        )


class QuestionAnsweringError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PreparedQuestionAuthorization:
    selection: object
    provider_payload: bytes
    evidence_count: int


@dataclass(frozen=True, slots=True)
class QuestionInputSource:
    origin: str
    context_item_id: str
    source_id: str
    source_version_id: str
    digest_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.origin not in {"raw_source", "daon_knowledge"}:
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")
        if not all((self.context_item_id, self.source_id, self.source_version_id)):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")


class QuestionAnsweringService:
    def __init__(
        self, provider_settings: ProviderSettingsService,
        repository: PostgresQuestionAnsweringRepository,
        document_index: DocumentIndexPort, credential_resolver: CredentialResolver,
        transport: TextGenerationTransport, egress: QuestionEgressPort,
        *, adapter_registry: QuestionAdapterRegistry | None = None,
        concurrent_wait_seconds: float = 2.0,
        concurrent_poll_seconds: float = 0.02,
    ) -> None:
        self._provider_settings = provider_settings
        self._repository = repository
        self._document_index = document_index
        self._credential_resolver = credential_resolver
        self._transport = transport
        self._egress = egress
        self._adapter_registry = adapter_registry or QuestionAdapterRegistry()
        self._concurrent_wait_seconds = max(0.0, concurrent_wait_seconds)
        self._concurrent_poll_seconds = max(0.001, concurrent_poll_seconds)

    def _wait_for_provider_owner(
        self, context: QuestionContext, run_id: str, request_fingerprint: str,
    ) -> StoredQuestionAnswer:
        deadline = time.monotonic() + self._concurrent_wait_seconds
        while True:
            completed = self._repository.load_completed(context, run_id)
            if completed is not None:
                replay = self._repository.load_completed_for_replay(
                    context, run_id, request_fingerprint,
                )
                if replay is None:
                    raise QuestionAnsweringError(
                        "QUESTION_RESULT_INVALID", status=500,
                    )
                return replay
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QuestionAnsweringError(
                    "QUESTION_NEW_RUN_REQUIRED", status=409, retryable=True,
                )
            time.sleep(min(self._concurrent_poll_seconds, remaining))

    @staticmethod
    def _context_sources(
        source_id: str, source_version_id: str,
        context_sources: tuple[QuestionInputSource, ...] | None,
    ) -> tuple[QuestionInputSource, ...]:
        return context_sources or (
            QuestionInputSource(
                "raw_source", source_id, source_id, source_version_id,
            ),
        )

    def _search_context(
        self, context: QuestionContext, sources: tuple[QuestionInputSource, ...], question: str,
    ) -> tuple[IndexedEvidenceChunk, ...]:
        processing_context = DocumentProcessingContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version,
        )
        chunks: dict[str, IndexedEvidenceChunk] = {}
        for item in sources:
            self._repository.load_ready_source(context, item.source_id, item.source_version_id)
            for chunk in self._document_index.search(
                processing_context, source_id=item.source_id,
                source_version_id=item.source_version_id, query=question, limit=10,
            ):
                chunks.setdefault(chunk.chunk_id, chunk)
        return tuple(sorted(
            chunks.values(),
            key=lambda item: (-float(item.score), int(item.page), str(item.chunk_id)),
        )[:10])

    def _ready_context_sources(
        self, context: QuestionContext, sources: tuple[QuestionInputSource, ...],
    ) -> tuple[QuestionInputSource, ...]:
        """Keep unavailable sources visible to the caller, but never search them.

        A stale browser selection can legitimately contain a source that became
        unavailable after the list was loaded.  That source must not poison a
        mixed context: only the ready subset is sent to retrieval and grounding.
        Database failures and other repository errors still propagate.
        """
        ready: list[QuestionInputSource] = []
        for item in sources:
            try:
                self._repository.load_ready_source(
                    context, item.source_id, item.source_version_id,
                )
            except QuestionRepositoryError as error:
                if error.code == "QUESTION_SOURCE_UNAVAILABLE":
                    continue
                raise
            ready.append(item)
        return tuple(ready)

    def prepare_authorization(
        self, context: QuestionContext, *, source_id: str | None,
        source_version_id: str | None, question: str,
        context_mode: str = "raw_only",
        context_sources: tuple[QuestionInputSource, ...] | None = None,
    ) -> PreparedQuestionAuthorization:
        general = is_general_conversation_intent(question) or (source_id is None and source_version_id is None)
        if (source_id is None) != (source_version_id is None):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")
        sources = () if general and source_id is None else self._context_sources(
            source_id or "", source_version_id or "", context_sources,
        )
        if not general:
            sources = self._ready_context_sources(context, sources)
            if not sources:
                general = True
        provider_context = ProviderSettingsContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version,
        )
        snapshot = self._provider_settings.snapshot(provider_context)
        selection = resolve_text_model_selection(snapshot)
        evidence = ()
        if not general:
            # Freeze authorization against the same evidence that `ask` will
            # use.  Otherwise the later grounded payload changes its digest
            # and the egress authorizer rejects an otherwise valid approval.
            evidence = self._search_context(context, sources, question)
            if not evidence:
                general = True
        if general:
            prepared = self._adapter_registry.prepare_general(
                snapshot, question, context.trace_id,
                self._credential_resolver, self._transport,
            )
            wire = canonical_json_bytes(prepared.provider_payload)
            transformer = getattr(self._egress, "prepare_payload", None)
            if callable(transformer):
                wire = transformer(context, wire)
            return PreparedQuestionAuthorization(selection, wire, 0)
        prepared = self._adapter_registry.prepare(
            snapshot, evidence, question, context.trace_id,
            self._credential_resolver, self._transport,
        )
        wire = canonical_json_bytes(prepared.provider_payload)
        transformer = getattr(self._egress, "prepare_payload", None)
        if callable(transformer):
            wire = transformer(context, wire)
        return PreparedQuestionAuthorization(selection, wire, len(evidence))

    def ask(
        self, context: QuestionContext, *, source_id: str | None,
        source_version_id: str | None, question: str, run_id: str,
        approved_authorization: Mapping[str, str] | None = None,
        context_mode: str = "raw_only",
        context_sources: tuple[QuestionInputSource, ...] | None = None,
        request_fingerprint: str | None = None,
        replay_checked: bool = False,
    ) -> StoredQuestionAnswer:
        if request_fingerprint is None:
            request_fingerprint = question_request_fingerprint(
                context, run_id=run_id, idempotency_key=run_id,
                request_payload={
                    "question": question.strip(), "source_id": source_id,
                    "source_version_id": source_version_id, "context_mode": context_mode,
                },
            )
        if not replay_checked:
            replay = self.replay(
                context, run_id=run_id, request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay
        general = is_general_conversation_intent(question) or (source_id is None and source_version_id is None)
        if general and ((source_id is None) != (source_version_id is None)):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")
        sources = () if general and source_id is None else self._context_sources(
            source_id or "", source_version_id or "", context_sources,
        )
        if not general:
            sources = self._ready_context_sources(context, sources)
            if not sources:
                general = True
                source_id = None
                source_version_id = None
            else:
                source_id = sources[0].source_id
                source_version_id = sources[0].source_version_id
        provider_context = ProviderSettingsContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version,
        )
        try:
            snapshot = self._provider_settings.snapshot(provider_context)
            selection = resolve_text_model_selection(snapshot)
        except ValueError as error:
            code = str(error)
            status = 409 if code.startswith("TEXT_") else 503
            raise QuestionAnsweringError(code, status=status) from None
        if general:
            try:
                prepare_general = getattr(self._adapter_registry, "prepare_general", None)
                prepared = (
                    prepare_general(
                        snapshot, question, context.trace_id,
                        self._credential_resolver, self._transport,
                    )
                    if callable(prepare_general)
                    else self._adapter_registry.prepare(
                        snapshot, (), question, context.trace_id,
                        self._credential_resolver, self._transport,
                    )
                )
            except (ValueError, DocumentUnderstandingError) as error:
                raw_code = error.code if isinstance(error, DocumentUnderstandingError) else str(error)
                raise QuestionAnsweringError(
                    raw_code if raw_code.startswith("TEXT_") else "TEXT_GENERATION_FAILED",
                    status=503 if raw_code.startswith("TEXT_PROVIDER_") else 502,
                ) from None
            frozen_bytes = canonical_json_bytes(prepared.provider_payload)
            transformer = getattr(self._egress, "prepare_payload", None)
            if callable(transformer):
                frozen_bytes = transformer(context, frozen_bytes)
                prepared = replace(prepared, provider_payload=json.loads(frozen_bytes))
            egress_authorization = self._egress.authorize(
                context, run_id=run_id, source_id=None, source_version_id=None,
                selection=selection, provider_payload=frozen_bytes,
                approved_authorization=approved_authorization,
                question=question, context_mode="general_ungrounded",
                request_fingerprint=request_fingerprint,
            )
            if egress_authorization.get("provider_owner") is False:
                return self._wait_for_provider_owner(
                    context, run_id, request_fingerprint,
                )
            try:
                generated = self._adapter_registry.generate_general(prepared)
            except (ValueError, DocumentUnderstandingError) as error:
                raw_code = error.code if isinstance(error, DocumentUnderstandingError) else str(error)
                raise QuestionAnsweringError(
                    raw_code if raw_code.startswith("TEXT_") else "TEXT_GENERATION_FAILED", status=502,
                ) from None
            return self._repository.persist_completed(
                context, run_id=run_id, source_id=None, source_version_id=None,
                question=question, selection=selection, evidence=(), result=generated,
                provider_called=True, egress_authorization=egress_authorization,
                context_mode="general_ungrounded", context_sources=(),
                request_fingerprint=request_fingerprint,
            )
        evidence = self._search_context(context, sources, question)
        if not evidence:
            prepare_general = getattr(self._adapter_registry, "prepare_general", None)
            # Legacy/custom evidence adapters do not advertise an ungrounded
            # route; freeze an empty decision without sending the question.
            if not callable(prepare_general):
                egress_authorization = self._egress.authorize(
                    context, run_id=run_id, source_id=source_id,
                    source_version_id=source_version_id, selection=selection,
                    provider_payload=b"", no_external_payload=True,
                    approved_authorization=approved_authorization,
                    question=question, context_mode=context_mode,
                    request_fingerprint=request_fingerprint,
                )
                return self._repository.persist_completed(
                    context, run_id=run_id, source_id=source_id,
                    source_version_id=source_version_id, question=question,
                    selection=selection, evidence=(),
                    result=GroundedTextResult("", (), False, {"no_evidence": True}),
                    provider_called=False, egress_authorization=egress_authorization,
                    context_mode=context_mode, context_sources=sources,
                    request_fingerprint=request_fingerprint,
                )
            egress_authorization = self._egress.authorize(
                context, run_id=run_id, source_id=None, source_version_id=None,
                selection=selection, provider_payload=b"",
                approved_authorization=approved_authorization,
                question=question, context_mode="general_ungrounded",
                request_fingerprint=request_fingerprint,
            )
            prepared = prepare_general(
                snapshot, question, context.trace_id,
                self._credential_resolver, self._transport,
            )
            generated = self._adapter_registry.generate_general(prepared)
            return self._repository.persist_completed(
                context, run_id=run_id, source_id=None, source_version_id=None,
                question=question, selection=selection, evidence=(), result=generated,
                provider_called=True, egress_authorization=egress_authorization,
                context_mode="general_ungrounded", context_sources=(),
                request_fingerprint=request_fingerprint,
            )
        try:
            prepared = self._adapter_registry.prepare(
                snapshot, evidence, question, context.trace_id,
                self._credential_resolver, self._transport,
            )
        except (ValueError, DocumentUnderstandingError) as error:
            raw_code = error.code if isinstance(error, DocumentUnderstandingError) else str(error)
            raise QuestionAnsweringError(
                raw_code if raw_code.startswith("TEXT_") else "TEXT_GENERATION_FAILED",
                status=503 if raw_code.startswith("TEXT_PROVIDER_") else 502,
            ) from None
        frozen_bytes = canonical_json_bytes(prepared.provider_payload)
        transformer = getattr(self._egress, "prepare_payload", None)
        if callable(transformer):
            frozen_bytes = transformer(context, frozen_bytes)
            prepared = replace(prepared, provider_payload=json.loads(frozen_bytes))
        egress_authorization = self._egress.authorize(
            context, run_id=run_id, source_id=source_id,
            source_version_id=source_version_id, selection=selection,
            provider_payload=frozen_bytes,
            approved_authorization=approved_authorization,
            question=question, context_mode=context_mode,
            request_fingerprint=request_fingerprint,
        )
        if egress_authorization.get("provider_owner") is False:
            return self._wait_for_provider_owner(
                context, run_id, request_fingerprint,
            )
        try:
            generated = self._adapter_registry.generate_prepared(prepared)
        except (ValueError, DocumentUnderstandingError) as error:
            raw_code = error.code if isinstance(error, DocumentUnderstandingError) else str(error)
            code = (
                "TEXT_PROVIDER_UNAVAILABLE"
                if raw_code.startswith("UNDERSTANDING_PROVIDER_")
                or raw_code == "PROVIDER_CREDENTIAL_NOT_CONFIGURED"
                else raw_code
            )
            retryable = code in {
                "TEXT_PROVIDER_UNAVAILABLE", "TEXT_GENERATION_PROVIDER_UNAVAILABLE",
            }
            raise QuestionAnsweringError(
                code if code.startswith("TEXT_") else "TEXT_GENERATION_FAILED",
                status=503 if retryable else 502, retryable=retryable,
            ) from None
        return self._repository.persist_completed(
            context, run_id=run_id, source_id=source_id,
            source_version_id=source_version_id, question=question,
            selection=selection, evidence=evidence, result=generated,
            provider_called=True,
            egress_authorization=egress_authorization,
            context_mode=context_mode, context_sources=sources,
            request_fingerprint=request_fingerprint,
        )

    def replay(
        self, context: QuestionContext, *, run_id: str, request_fingerprint: str,
    ) -> StoredQuestionAnswer | None:
        return self._repository.load_completed_for_replay(
            context, run_id, request_fingerprint,
        )
