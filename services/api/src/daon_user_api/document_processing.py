"""Application service for one auditable original-PDF processing attempt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingRequest,
    DocumentUnderstandingResult,
    ProviderCredentialResolver,
    UpstageDocumentUnderstandingAdapter,
    UrlLibDocumentUnderstandingTransport,
    resolve_document_model_selection,
)
from .provider_settings import ProviderSettingsContext, ProviderSettingsSnapshot


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

    def provider_context(self) -> ProviderSettingsContext:
        return ProviderSettingsContext(
            self.tenant_id, self.workspace_id, self.actor_id,
            self.trace_id, self.policy_version,
        )


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
        self, context: DocumentProcessingContext, processing_run_id: str,
    ) -> DocumentProcessingStatus: ...


class DocumentProcessingSubmissionService:
    def __init__(self, repository: DocumentProcessingRepository) -> None:
        self._repository = repository

    def submit(
        self, context: DocumentProcessingContext, source_version_id: str,
    ) -> DocumentProcessingStatus:
        processing_run_id = self._repository.start(
            context, source_version_id, enqueue=True,
        )
        return self._repository.get_status(context, processing_run_id)

    def get_status(
        self, context: DocumentProcessingContext, processing_run_id: str,
    ) -> DocumentProcessingStatus:
        return self._repository.get_status(context, processing_run_id)


class ProviderSnapshotPort(Protocol):
    def snapshot(self, context: ProviderSettingsContext) -> ProviderSettingsSnapshot: ...


class DocumentUnderstandingPort(Protocol):
    def understand(self, request: DocumentUnderstandingRequest, selection): ...  # type: ignore[no-untyped-def]


class DocumentAdapterFactory(Protocol):
    def create(self, provider_code: str, credential: str) -> DocumentUnderstandingPort: ...


class DefaultDocumentAdapterFactory:
    def create(self, provider_code: str, credential: str) -> DocumentUnderstandingPort:
        if provider_code != "UPSTAGE":
            raise DocumentUnderstandingError("DOCUMENT_PROVIDER_ADAPTER_UNAVAILABLE", status=503)
        return UpstageDocumentUnderstandingAdapter(
            transport=UrlLibDocumentUnderstandingTransport(), api_key=credential,
        )


class DocumentProcessingService:
    def __init__(
        self,
        repository: DocumentProcessingRepository,
        provider_settings: ProviderSnapshotPort,
        credentials: ProviderCredentialResolver,
        adapter_factory: DocumentAdapterFactory,
    ) -> None:
        self._repository = repository
        self._provider_settings = provider_settings
        self._credentials = credentials
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
            snapshot = self._provider_settings.snapshot(context.provider_context())
            selection = resolve_document_model_selection(snapshot)
            credential = self._credentials.resolve(selection.provider_code)
            adapter = self._adapter_factory.create(selection.provider_code, credential)
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
        except DocumentUnderstandingError as error:
            self._repository.fail(
                context, processing_run_id, error.code, retryable=error.retryable,
            )
            raise
