"""Application service for immutable multi-source grounded question runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Mapping, Protocol

from .data_canon import canonical_json_bytes
from .document_processing import DocumentProcessingContext
from .document_index_postgres import IndexedEvidenceChunk
from .document_understanding_adapter import DocumentUnderstandingError
from .provider_settings import ProviderSettingsContext, ProviderSettingsService
from .question_answering import (
    GroundedQuestionRequest, GroundedTextResult, TextGenerationTransport,
    OllamaTextGenerationAdapter, OpenAICompatibleTextGenerationAdapter,
    resolve_text_model_selection,
)
from .question_answering_postgres import (
    PostgresQuestionAnsweringRepository, QuestionContext, StoredQuestionAnswer,
)


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
        request: GroundedQuestionRequest
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
    ) -> None:
        self._provider_settings = provider_settings
        self._repository = repository
        self._document_index = document_index
        self._credential_resolver = credential_resolver
        self._transport = transport
        self._egress = egress
        self._adapter_registry = adapter_registry or QuestionAdapterRegistry()

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

    def prepare_authorization(
        self, context: QuestionContext, *, source_id: str,
        source_version_id: str, question: str,
        context_mode: str = "raw_only",
        context_sources: tuple[QuestionInputSource, ...] | None = None,
    ) -> PreparedQuestionAuthorization:
        del context_mode
        sources = self._context_sources(source_id, source_version_id, context_sources)
        provider_context = ProviderSettingsContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version,
        )
        snapshot = self._provider_settings.snapshot(provider_context)
        selection = resolve_text_model_selection(snapshot)
        evidence = self._search_context(context, sources, question)
        if not evidence:
            return PreparedQuestionAuthorization(selection, b"", 0)
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
        self, context: QuestionContext, *, source_id: str,
        source_version_id: str, question: str, run_id: str,
        approved_authorization: Mapping[str, str] | None = None,
        context_mode: str = "raw_only",
        context_sources: tuple[QuestionInputSource, ...] | None = None,
    ) -> StoredQuestionAnswer:
        replay = self._repository.load_completed(context, run_id)
        if replay is not None:
            return replay
        sources = self._context_sources(source_id, source_version_id, context_sources)
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
        evidence = self._search_context(context, sources, question)
        if not evidence:
            egress_authorization = self._egress.authorize(
                context, run_id=run_id, source_id=source_id,
                source_version_id=source_version_id, selection=selection,
                provider_payload=b"", no_external_payload=True,
                approved_authorization=approved_authorization,
            )
            return self._repository.persist_completed(
                context, run_id=run_id, source_id=source_id,
                source_version_id=source_version_id, question=question,
                selection=selection, evidence=(),
                result=GroundedTextResult(
                    "근거가 부족하여 답변할 수 없습니다.", (), True, {},
                ),
                provider_called=False,
                egress_authorization=egress_authorization,
                context_mode=context_mode, context_sources=sources,
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
        )
