from __future__ import annotations

import unittest

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
from tests.test_document_understanding_adapter import provider_snapshot


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


class SnapshotService:
    def __init__(self) -> None:
        self.calls = 0

    def snapshot(self, context):  # type: ignore[no-untyped-def]
        self.calls += 1
        return provider_snapshot()


class CredentialResolver:
    def resolve(self, provider_code: str) -> str:
        if provider_code != "UPSTAGE":
            raise AssertionError(provider_code)
        return "up_test_secret"


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

    def create(self, provider_code: str, credential: str):
        self.calls.append((provider_code, credential))
        return self.adapter


class DocumentProcessingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = DocumentProcessingContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
        )

    def test_frozen_selection_processes_original_pdf_and_persists_completion(self) -> None:
        repository = RecordingRepository()
        snapshots = SnapshotService()
        adapter = Adapter()
        factory = AdapterFactory(adapter)
        service = DocumentProcessingService(repository, snapshots, CredentialResolver(), factory)

        result = service.process(self.context, source_id="source-cp3")

        self.assertEqual(result.status, "ready")
        self.assertEqual(snapshots.calls, 1)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(factory.calls, [("UPSTAGE", "up_test_secret")])
        self.assertEqual([event[0] for event in repository.events], ["load", "start", "complete"])

    def test_retryable_semantic_failure_is_persisted_without_false_completion(self) -> None:
        repository = RecordingRepository()
        adapter = Adapter(DocumentUnderstandingError(
            "UNDERSTANDING_PROVIDER_UNAVAILABLE", status=503, retryable=True,
        ))
        service = DocumentProcessingService(
            repository, SnapshotService(), CredentialResolver(), AdapterFactory(adapter),
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
            repository, SnapshotService(), CredentialResolver(), AdapterFactory(Adapter()),
        )

        result = service.process_existing(
            self.context, source_id="source-cp3", processing_run_id="processing-run-existing",
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual([event[0] for event in repository.events], ["load", "complete"])
        self.assertEqual(repository.events[-1][1], ("processing-run-existing", "ready"))


if __name__ == "__main__":
    unittest.main()
