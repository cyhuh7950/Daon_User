"""Application service for one auditable original-PDF processing attempt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ContextManager, Mapping, Protocol, cast

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
from .egress_policy import EgressPolicyContext, EgressPolicyError, EgressPolicyService
from .cloud_storage import CloudDatabaseError


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_TEXT_SOURCE_MIMES = {"txt": "text/plain", "md": "text/markdown", "csv": "text/csv"}


def text_source_format(filename: str, content_type: str) -> str | None:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return suffix if _TEXT_SOURCE_MIMES.get(suffix) == content_type else None


def image_source_format(filename: str, content_type: str) -> bool:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(suffix) == content_type


def xlsx_source_format(filename: str, content_type: str) -> bool:
    return filename.lower().endswith('.xlsx') and content_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def docx_source_format(filename: str, content_type: str) -> bool:
    return filename.lower().endswith('.docx') and content_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


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
    content_type: str = "application/pdf"


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
    def reprocess(
        self, context: DocumentProcessingContext, source_id: str, source_version_id: str,
        *, notebook_id: str, idempotency_key: str, authorization_snapshot: Mapping[str, object],
    ) -> dict[str, object]: ...

    def load_source_document(
        self, context: DocumentProcessingContext, source_id: str,
        *, processing_run_id: str | None = None,
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

    def reprocess(
        self, context: DocumentProcessingContext, source_id: str, source_version_id: str,
        *, notebook_id: str, idempotency_key: str, authorization_snapshot: Mapping[str, object],
    ) -> dict[str, object]:
        if any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value)
               for value in (source_id, source_version_id, notebook_id)) or not idempotency_key:
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        return self._repository.reprocess(
            context, source_id, source_version_id, notebook_id=notebook_id,
            idempotency_key=idempotency_key, authorization_snapshot=authorization_snapshot,
        )

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


class TextDocumentUnderstandingPort(Protocol):
    def understand(
        self, document: StoredSourceDocument, selection: ResolvedModel,
        context: DocumentProcessingContext,
    ) -> DocumentUnderstandingResult: ...


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


class OriginalDocumentEgressGuard:
    """This processing contract supplies no original-transfer approval."""

    def __init__(self, policy_service: EgressPolicyService | None = None) -> None:
        self._policy_service = policy_service

    def authorize(
        self, context: DocumentProcessingContext, selections: tuple[ResolvedModel, ...],
    ) -> None:
        internal_kinds = {"local", "server_internal", "local_runtime"}
        if any(model.provider_kind not in internal_kinds | {"external_api"} for model in selections):
            raise DocumentUnderstandingError("EGRESS_POLICY_DENIED", status=403)
        if not any(model.provider_kind == "external_api" for model in selections):
            return
        if self._policy_service is None:
            raise DocumentUnderstandingError("EGRESS_POLICY_UNAVAILABLE", status=503, retryable=True)
        try:
            effective = self._policy_service.get_effective(EgressPolicyContext(
                context.tenant_id, context.tenant_id, context.workspace_id,
                context.actor_id, context.trace_id, context.policy_version,
            ))
        except EgressPolicyError as error:
            raise DocumentUnderstandingError(
                error.code, status=error.status, retryable=error.status >= 500,
            ) from None
        except CloudDatabaseError:
            raise DocumentUnderstandingError("EGRESS_POLICY_UNAVAILABLE", status=503, retryable=True) from None
        if effective.mode != "allow_approved_external":
            raise DocumentUnderstandingError("EGRESS_POLICY_DENIED", status=403)
        # Policy allow is not approval bound to this SourceVersion/Run/payload.
        # No such approval is provided; never infer it from settings or raw size.
        raise DocumentUnderstandingError("EGRESS_POLICY_DENIED", status=403)


class DocumentProcessingService:
    def __init__(
        self,
        repository: DocumentProcessingRepository,
        model_resolver: WorkspaceModelResolverPort,
        adapter_factory: DocumentAdapterFactory,
        *, text_adapter: TextDocumentUnderstandingPort | None = None,
        egress_guard: OriginalDocumentEgressGuard | None = None,
        image_adapter=None,
        xlsx_adapter=None,
        docx_adapter=None,
    ) -> None:
        self._repository = repository
        self._model_resolver = model_resolver
        self._adapter_factory = adapter_factory
        if text_adapter is None:
            from .text_document_understanding import OllamaTextDocumentAdapter
            text_adapter = OllamaTextDocumentAdapter(UrlLibDocumentUnderstandingTransport())
        self._text_adapter = text_adapter
        self._egress_guard = egress_guard if egress_guard is not None else OriginalDocumentEgressGuard()
        if image_adapter is None:
            from .image_document_understanding import OllamaImageDocumentAdapter
            image_adapter = OllamaImageDocumentAdapter(UrlLibDocumentUnderstandingTransport())
        self._image_adapter = image_adapter
        if xlsx_adapter is None:
            from .xlsx_document_understanding import OllamaXlsxDocumentAdapter
            xlsx_adapter = OllamaXlsxDocumentAdapter(UrlLibDocumentUnderstandingTransport())
        self._xlsx_adapter = xlsx_adapter
        if docx_adapter is None:
            from .docx_document_understanding import OllamaDocxDocumentAdapter
            docx_adapter = OllamaDocxDocumentAdapter(UrlLibDocumentUnderstandingTransport())
        self._docx_adapter = docx_adapter

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
        document = self._repository.load_source_document(
            context, source_id, processing_run_id=processing_run_id,
        )
        return self._execute(context, document, processing_run_id)

    def _execute(
        self, context: DocumentProcessingContext, document: StoredSourceDocument,
        processing_run_id: str,
    ) -> DocumentUnderstandingResult:
        try:
            if docx_source_format(document.filename, document.content_type):
                with self._model_resolver.resolve(context.model_context(), "text_generation") as model:
                    self._egress_guard.authorize(context, (model,))
                    result = self._docx_adapter.understand(document, model, context)
                self._repository.complete(context, processing_run_id, result)
                return result
            if xlsx_source_format(document.filename, document.content_type):
                with self._model_resolver.resolve(context.model_context(), "text_generation") as model:
                    self._egress_guard.authorize(context, (model,))
                    result = self._xlsx_adapter.understand(document, model, context)
                self._repository.complete(context, processing_run_id, result)
                return result
            if image_source_format(document.filename, document.content_type):
                with self._model_resolver.resolve(context.model_context(), "image_understanding") as image_model:
                    self._egress_guard.authorize(context, (image_model,))
                    result = self._image_adapter.understand(document, image_model, context)
                self._repository.complete(context, processing_run_id, result)
                return result
            if text_source_format(document.filename, document.content_type) is not None:
                with self._model_resolver.resolve(
                    context.model_context(), "text_generation",
                ) as text_model:
                    self._egress_guard.authorize(context, (text_model,))
                    result = self._text_adapter.understand(document, text_model, context)
                self._repository.complete(context, processing_run_id, result)
                return result
            with self._model_resolver.resolve(
                context.model_context(), "image_understanding",
            ) as semantic, self._model_resolver.resolve(
                context.model_context(), "document_parsing",
            ) as parser:
                self._egress_guard.authorize(context, (semantic, parser))
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
