from __future__ import annotations

import unittest
from contextlib import contextmanager

from daon_user_api.document_processing import (
    DocumentProcessingContext,
    DocumentProcessingService,
    StoredSourceDocument,
)
from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingResult,
    ParserValidation,
    SemanticUnderstanding,
)
from daon_user_api.workspace_model_defaults import ResolvedModel
try:
    from tests.test_document_understanding_adapter import provider_snapshot
except ModuleNotFoundError:
    # unittest discovery adds the test directory itself to sys.path.
    from test_document_understanding_adapter import provider_snapshot


PDF = b"%PDF-1.4\n% processing fixture\n%%EOF\n"


class RecordingRepository:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def load_source_document(self, context: DocumentProcessingContext, source_id: str) -> StoredSourceDocument:
        self.events.append(("load", source_id))
        return StoredSourceDocument(source_id, "source-version-cp3", "contract.pdf", PDF)

    def start(self, context: DocumentProcessingContext, source_version_id: str) -> str:
        self.events.append(("start", source_version_id))
        return "processing-run-cp3"

    def complete(self, context: DocumentProcessingContext, processing_run_id: str, result: DocumentUnderstandingResult) -> None:
        self.events.append(("complete", (processing_run_id, result.status)))

    def fail(self, context: DocumentProcessingContext, processing_run_id: str, code: str, *, retryable: bool) -> None:
        self.events.append(("fail", (processing_run_id, code, retryable)))


class ModelResolver:
    def __init__(self) -> None:
        self.calls = 0

        self.capabilities = []

    @contextmanager
    def resolve(self, context, capability):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.capabilities.append(capability)
        semantic = capability == "image_understanding"
        model = ResolvedModel(
            connection_id="upstage-vision" if semantic else "upstage-parser",
            provider_code="UPSTAGE",
            model_id="information-extract" if semantic else "document-parse",
            capability=capability, base_url="https://api.upstage.ai/v1",
            credential_version=4 if semantic else 9,
            default_version=2 if semantic else 3, catalog_version=8,
            provider_kind="external_api", routing_owner="provider",
            daon_fallback_allowed=True,
            _credential=bytearray(b"vision-secret" if semantic else b"parser-secret"),
        )
        try:
            yield model
        finally:
            model.release()


class Adapter:
    def __init__(self, error: DocumentUnderstandingError | None = None) -> None:
        self.error = error
        self.calls = 0

    def understand(self, request, selection):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.error:
            raise self.error
        return DocumentUnderstandingResult(
            request.source_id, request.source_version_id, "ready",
            ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
            SemanticUnderstanding("Daon", "Vision first", ("Vision first",)),
            ParserValidation("Vision first", "Vision first", "<p>Vision first</p>", (1,)),
            {"provider_code": selection.provider_code, "parser_role": "validation_only"},
        )


class AdapterFactory:
    def __init__(self, adapter: Adapter) -> None:
        self.adapter = adapter
        self.calls: list[tuple[str, str]] = []

    def create(self, selection, semantic_credential: str, parser_credential: str):  # type: ignore[no-untyped-def]
        self.calls.append((
            selection.semantic_connection_id, selection.parser_connection_id,
            selection.semantic_credential_version, selection.parser_credential_version,
            semantic_credential == "vision-secret", parser_credential == "parser-secret",
        ))
        return self.adapter


class DocumentProcessingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = DocumentProcessingContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
        )

    def test_frozen_selection_processes_original_pdf_and_persists_completion(self) -> None:
        repository = RecordingRepository()
        snapshots = ModelResolver()
        adapter = Adapter()
        factory = AdapterFactory(adapter)
        service = DocumentProcessingService(repository, snapshots, factory)

        result = service.process(self.context, source_id="source-cp3")

        self.assertEqual(result.status, "ready")
        self.assertEqual(snapshots.calls, 2)
        self.assertEqual(snapshots.capabilities, ["image_understanding", "document_parsing"])
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(factory.calls, [(
            "upstage-vision", "upstage-parser", 4, 9, True, True,
        )])
        self.assertNotIn("vision-secret", repr(result))
        self.assertNotIn("parser-secret", repr(result))
        self.assertEqual([event[0] for event in repository.events], ["load", "start", "complete"])

    def test_retryable_semantic_failure_is_persisted_without_false_completion(self) -> None:
        repository = RecordingRepository()
        adapter = Adapter(DocumentUnderstandingError(
            "UNDERSTANDING_PROVIDER_UNAVAILABLE", status=503, retryable=True,
        ))
        service = DocumentProcessingService(
            repository, ModelResolver(), AdapterFactory(adapter),
        )

        with self.assertRaisesRegex(DocumentUnderstandingError, "UNDERSTANDING_PROVIDER_UNAVAILABLE"):
            service.process(self.context, source_id="source-cp3")

        self.assertEqual([event[0] for event in repository.events], ["load", "start", "fail"])
        self.assertEqual(repository.events[-1][1], (
            "processing-run-cp3", "UNDERSTANDING_PROVIDER_UNAVAILABLE", True,
        ))

    def test_worker_processes_an_existing_run_without_creating_a_second_run(self) -> None:
        repository = RecordingRepository()
        service = DocumentProcessingService(
            repository, ModelResolver(), AdapterFactory(Adapter()),
        )

        result = service.process_existing(
            self.context, source_id="source-cp3", processing_run_id="processing-run-existing",
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual([event[0] for event in repository.events], ["load", "complete"])
        self.assertEqual(repository.events[-1][1], ("processing-run-existing", "ready"))


if __name__ == "__main__":
    unittest.main()
