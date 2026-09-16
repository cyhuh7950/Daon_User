"""Application service for one auditable original-PDF processing attempt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ContextManager, Protocol, cast

from .document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingRequest,
    DocumentUnderstandingResult,
    DocumentModelSelection,
    UpstageDocumentUnderstandingAdapter,
    UrlLibDocumentUnderstandingTransport,
)
from .workspace_model_defaults import (
    ResolvedModel, WorkspaceModelContext, WorkspaceModelUnavailable,
)


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


@dataclass(frozen=True, slots=True)
class DocumentProcessingContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value) for value in (
            self.tenant_id, self.workspace_id, self.actor_id, self.trace_id, self.policy_version,
        )):
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")

    def model_context(self) -> WorkspaceModelContext:
        return WorkspaceModelContext(self.tenant_id, self.workspace_id, self.actor_id)


@dataclass(frozen=True, slots=True)
class StoredSourceDocument:
    source_id: str
    source_version_id: str
    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class DocumentProcessingStatus:
    processing_run_id: str
    source_id: str
    source_version_id: str
    processing_state: str
    source_state: str
    job_state: str | None
    safe_error_code: str | None


class DocumentProcessingRepository(Protocol):
    def load_source_document(
        self, context: DocumentProcessingContext, source_id: str,
    ) -> StoredSourceDocument: ...

    def start(
        self, context: DocumentProcessingContext, source_version_id: str,
        *, enqueue: bool = False,
    ) -> str: ...

    def complete(
        self, context: DocumentProcessingContext, processing_run_id: str,
        result: DocumentUnderstandingResult,
    ) -> None: ...

    def fail(
        self, context: DocumentProcessingContext, processing_run_id: str,
        code: str, *, retryable: bool,
    ) -> None: ...

    def get_status(
        self, context: DocumentProcessingContext, processing_run_id: str, *, notebook_id: str,
    ) -> DocumentProcessingStatus: ...


class DocumentProcessingSubmissionService:
    def __init__(self, repository: DocumentProcessingRepository) -> None:
        self._repository = repository

    def submit(
        self, context: DocumentProcessingContext, source_version_id: str, *, notebook_id: str,
    ) -> DocumentProcessingStatus:
        if not isinstance(notebook_id, str) or _SAFE_ID.fullmatch(notebook_id) is None:
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        processing_run_id = self._repository.start(
            context, source_version_id, enqueue=True,
        )
        return self._repository.get_status(context, processing_run_id, notebook_id=notebook_id)

    def get_status(
        self, context: DocumentProcessingContext, processing_run_id: str, *, notebook_id: str,
    ) -> DocumentProcessingStatus:
        if not isinstance(notebook_id, str) or _SAFE_ID.fullmatch(notebook_id) is None:
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        return self._repository.get_status(context, processing_run_id, notebook_id=notebook_id)


class WorkspaceModelResolverPort(Protocol):
    def resolve(
        self, context: WorkspaceModelContext, capability: str,
    ) -> ContextManager[ResolvedModel]: ...


class DocumentUnderstandingPort(Protocol):
    def understand(self, request: DocumentUnderstandingRequest, selection): ...  # type: ignore[no-untyped-def]


class DocumentAdapterFactory(Protocol):
    def create(
        self, selection: DocumentModelSelection,
        semantic_credential: str, parser_credential: str,
    ) -> DocumentUnderstandingPort: ...


class DefaultDocumentAdapterFactory:
    def create(
        self, selection: DocumentModelSelection,
        semantic_credential: str, parser_credential: str,
    ) -> DocumentUnderstandingPort:
        if selection.provider_code != "UPSTAGE":
            raise DocumentUnderstandingError("DOCUMENT_PROVIDER_ADAPTER_UNAVAILABLE", status=503)
        return UpstageDocumentUnderstandingAdapter(
            transport=UrlLibDocumentUnderstandingTransport(), api_key=semantic_credential,
            parser_api_key=parser_credential,
        )


class DocumentProcessingService:
    def __init__(
        self,
        repository: DocumentProcessingRepository,
        model_resolver: WorkspaceModelResolverPort,
        adapter_factory: DocumentAdapterFactory,
    ) -> None:
        self._repository = repository
        self._model_resolver = model_resolver
        self._adapter_factory = adapter_factory

    def process(
        self, context: DocumentProcessingContext, *, source_id: str,
    ) -> DocumentUnderstandingResult:
        document = self._repository.load_source_document(context, source_id)
        processing_run_id = self._repository.start(context, document.source_version_id)
        return self._execute(context, document, processing_run_id)

    def process_existing(
        self, context: DocumentProcessingContext, *, source_id: str,
        processing_run_id: str,
    ) -> DocumentUnderstandingResult:
        document = self._repository.load_source_document(context, source_id)
        return self._execute(context, document, processing_run_id)

    def _execute(
        self, context: DocumentProcessingContext, document: StoredSourceDocument,
        processing_run_id: str,
    ) -> DocumentUnderstandingResult:
        try:
            with self._model_resolver.resolve(
                context.model_context(), "image_understanding",
            ) as semantic, self._model_resolver.resolve(
                context.model_context(), "document_parsing",
            ) as parser:
                if semantic.provider_code != "UPSTAGE" or parser.provider_code != "UPSTAGE":
                    raise DocumentUnderstandingError(
                        "DOCUMENT_PROVIDER_ADAPTER_UNAVAILABLE", status=503,
                    )
                selection = DocumentModelSelection(
                    semantic.provider_code, semantic.base_url,
                    semantic.deployment_id, semantic.model_id,
                    parser.deployment_id, parser.model_id,
                    max(semantic.binding_version, parser.binding_version),
                    semantic.connection_id, semantic.credential_version, semantic.base_url,
                    parser.connection_id, parser.credential_version, parser.base_url,
                )
                adapter = self._adapter_factory.create(
                    selection, cast(str, semantic.credential_text()),
                    cast(str, parser.credential_text()),
                )
                result = adapter.understand(
                    DocumentUnderstandingRequest(
                        document.source_id, document.source_version_id, document.filename,
                        document.content, context.trace_id, "document-understanding-v1",
                        context.policy_version,
                    ),
                    selection,
                )
            self._repository.complete(context, processing_run_id, result)
            return result
        except WorkspaceModelUnavailable as error:
            mapped = DocumentUnderstandingError(error.code, status=503, retryable=error.retryable)
            self._repository.fail(
                context, processing_run_id, mapped.code, retryable=mapped.retryable,
            )
            raise mapped from None
        except DocumentUnderstandingError as error:
            self._repository.fail(
                context, processing_run_id, error.code, retryable=error.retryable,
            )
            raise
