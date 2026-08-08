"""Application service for a single ready PDF grounded question run."""

from __future__ import annotations

from typing import Protocol

from .document_processing import DocumentProcessingContext
from .document_understanding_adapter import DocumentUnderstandingError
from .provider_settings import ProviderSettingsContext, ProviderSettingsService
from .question_answering import (
    GroundedQuestionRequest, GroundedTextResult, TextGenerationTransport,
    UpstageTextGenerationAdapter, resolve_text_model_selection,
)
from .question_answering_postgres import (
    PostgresQuestionAnsweringRepository, QuestionContext, StoredQuestionAnswer,
)


class CredentialResolver(Protocol):
    def resolve(self, provider_code: str) -> str: ...


class DocumentIndexPort(Protocol):
    def search(self, context: DocumentProcessingContext, **kwargs): ...  # type: ignore[no-untyped-def]


class QuestionAnsweringError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


class QuestionAnsweringService:
    def __init__(
        self, provider_settings: ProviderSettingsService,
        repository: PostgresQuestionAnsweringRepository,
        document_index: DocumentIndexPort, credential_resolver: CredentialResolver,
        transport: TextGenerationTransport,
    ) -> None:
        self._provider_settings = provider_settings
        self._repository = repository
        self._document_index = document_index
        self._credential_resolver = credential_resolver
        self._transport = transport

    def ask(
        self, context: QuestionContext, *, source_id: str,
        source_version_id: str, question: str, run_id: str,
    ) -> StoredQuestionAnswer:
        replay = self._repository.load_completed(context, run_id)
        if replay is not None:
            return replay
        self._repository.load_ready_source(context, source_id, source_version_id)
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
        processing_context = DocumentProcessingContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version,
        )
        evidence = self._document_index.search(
            processing_context, source_id=source_id,
            source_version_id=source_version_id, query=question, limit=10,
        )
        if not evidence:
            return self._repository.persist_completed(
                context, run_id=run_id, source_id=source_id,
                source_version_id=source_version_id, question=question,
                selection=selection, evidence=(),
                result=GroundedTextResult(
                    "근거가 부족하여 답변할 수 없습니다.", (), True, {},
                ),
                provider_called=False,
            )
        try:
            api_key = self._credential_resolver.resolve(selection.provider_code)
            generated = UpstageTextGenerationAdapter(
                transport=self._transport, api_key=api_key,
            ).generate(
                GroundedQuestionRequest(question.strip(), evidence, context.trace_id),
                selection,
            )
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
        )
