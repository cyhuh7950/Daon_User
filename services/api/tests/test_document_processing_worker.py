from __future__ import annotations

import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from contextlib import ExitStack
from pathlib import Path

from daon_user_api.document_processing_queue import DocumentProcessingJob
from daon_user_api.document_processing_worker import DocumentProcessingWorker, DocumentWorkerSettings, build_worker
from daon_user_api.egress_policy import EgressPolicyContext, EgressPolicyPayload, ReferenceEgressPolicyRepository
from test_document_processing import RecordingRepository, ModelResolver, Adapter, AdapterFactory
from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingResult,
    ParserValidation,
    SemanticUnderstanding,
)


NOW = datetime(2026, 8, 6, 6, 0, tzinfo=timezone.utc)


def job() -> DocumentProcessingJob:
    return DocumentProcessingJob(
        "tenant-cp3", "workspace-cp3", "job-cp3", "source-cp3",
        "source-version-cp3", "run-cp3", "leased", 1, 3, "worker-cp3",
        NOW + timedelta(minutes=2), "trace-cp3", "policy-v1", "actor-cp3",
        NOW - timedelta(minutes=1), 2,
    )


def result() -> DocumentUnderstandingResult:
    return DocumentUnderstandingResult(
        "source-cp3", "source-version-cp3", "ready",
        ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
        SemanticUnderstanding("계약서", "계약 요약", ("계약 기간은 12개월",)),
        ParserValidation("계약 기간은 12개월", "", "", (1,), ((1, "계약 기간은 12개월"),)),
        {"provider_code": "UPSTAGE", "parser_role": "validation_only"},
    )


class FakeQueue:
    def __init__(self, claimed: DocumentProcessingJob | None) -> None:
        self.claimed = claimed
        self.events: list[tuple[str, object]] = []

    def claim(self, worker_id: str, *, lease_seconds: int = 120):  # type: ignore[no-untyped-def]
        self.events.append(("claim", worker_id))
        return self.claimed

    def complete(self, claimed, worker_id: str, *, now):  # type: ignore[no-untyped-def]
        self.events.append(("complete", claimed.processing_run_id))

    def fail_terminal(self, claimed, worker_id: str, code: str, *, now):  # type: ignore[no-untyped-def]
        self.events.append(("dead_letter", code))


class FakeProcessingService:
    def __init__(self, *, failure: DocumentUnderstandingError | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, str]] = []

    def process_existing(self, context, *, source_id: str, processing_run_id: str):  # type: ignore[no-untyped-def]
        self.calls.append((source_id, processing_run_id))
        if self.failure is not None:
            raise self.failure
        return result()


class FakeIndex:
    def __init__(self, events: list[tuple[str, object]]) -> None:
        self.events = events

    def index_result(self, context, understanding):  # type: ignore[no-untyped-def]
        self.events.append(("index", understanding.source_version_id))
        return "index-version-cp3"


class DocumentProcessingWorkerTests(unittest.TestCase):
    def test_xlsx_worker_indexes_only_cell_verified_native_completion(self):
        from contextlib import contextmanager
        from daon_user_api.workspace_model_defaults import ResolvedModel
        from daon_user_api.document_processing import DocumentProcessingService
        from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
        from daon_user_api.xlsx_document_understanding import OllamaXlsxDocumentAdapter
        from test_document_processing_postgres import FakeCloudStore, FakeObjectStorage
        from test_xlsx_document_understanding import Transport, original
        class Resolver:
            @contextmanager
            def resolve(self, context, capability):
                yield ResolvedModel('ollama-1', 'OLLAMA', 'llama3.2:3b', capability, 'https://ollama.example', 0, 1, 1, 'server_internal', 'provider', True, None)
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch):
                store = FakeCloudStore(); storage = FakeObjectStorage(); transport = Transport(); queue = FakeQueue(job())
                store.connection.filename = 'qa.xlsx'; store.connection.content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                store.connection.content = storage.content = original()
                store.connection.states = {'ProcessingRun': 'vision_llm_understanding', 'Source': 'processing'}
                store.connection.versions = {'ProcessingRun': 2, 'Source': 3}
                if mismatch: transport.value['key_facts'][0]['text'] = 'ORANGE-99'
                service = DocumentProcessingService(PostgresDocumentProcessingRepository(store, storage), Resolver(), AdapterFactory(Adapter()), xlsx_adapter=OllamaXlsxDocumentAdapter(transport))
                worker = DocumentProcessingWorker('worker-cp3', queue, service, FakeIndex(queue.events), clock=lambda: NOW)
                self.assertTrue(worker.run_once())
                self.assertEqual(sum(event[0] == 'index' for event in queue.events), 0 if mismatch else 1)
                self.assertEqual(queue.events[-1], ('complete', 'run-cp3'))
                self.assertEqual(store.connection.states['Source'], 'needs_review' if mismatch else 'processing')

    def test_needs_review_result_never_indexes_or_becomes_ready(self):
        from dataclasses import replace
        queue = FakeQueue(job())
        service = FakeProcessingService()
        service.process_existing = lambda *args, **kwargs: replace(result(), status="needs_review", conflict="UNDERSTANDING_PARSER_CONFLICT")
        worker = DocumentProcessingWorker("worker-cp3", queue, service, FakeIndex(queue.events), clock=lambda: NOW)
        self.assertTrue(worker.run_once())
        self.assertEqual(queue.events, [("claim", "worker-cp3"), ("complete", "run-cp3")])
    def test_built_worker_consumes_real_policy_service_before_external_adapter(self) -> None:
        settings = DocumentWorkerSettings("worker-cp3", "unused-fixture-dsn", "unused-fixture-storage",
            "fixture-bucket", Path("fixture-access"), Path("fixture-secret"), Path("fixture-provider"), True, 600, 0.5)
        queue = FakeQueue(job())
        repository = RecordingRepository()
        adapter = Adapter()
        factory = AdapterFactory(adapter)
        policy_repository = ReferenceEgressPolicyRepository()
        context = EgressPolicyContext("tenant-cp3", "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1")
        for scope in ("organization", "workspace"):
            policy_repository.seed(context, scope_type=scope, payload=EgressPolicyPayload.deny_external())
        prefix = "daon_user_api.document_processing_worker."
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, "read_text", return_value="fixture-only"))
            stack.enter_context(patch.object(Path, "read_bytes", return_value=b"x" * 32))
            for name, value in (
                ("PostgresCloudStore", object()), ("MinioObjectStorageAdapter", object()),
                ("ProviderCredentialCipher", object()), ("PostgresDocumentProcessingRepository", repository),
                ("PostgresWorkspaceModelResolver", ModelResolver()), ("DefaultDocumentAdapterFactory", factory),
                ("PostgresDocumentProcessingQueue", queue), ("PostgresDocumentIndex", FakeIndex(queue.events)),
                ("PostgresEgressPolicyRepository", policy_repository),
            ):
                stack.enter_context(patch(prefix + name, return_value=value, create=True))
            worker, _, _ = build_worker(settings)
            worker._clock = lambda: NOW
            self.assertTrue(worker.run_once())
        self.assertEqual(factory.calls, [])
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(queue.events, [("claim", "worker-cp3"), ("dead_letter", "EGRESS_POLICY_DENIED")])
        self.assertEqual(repository.events, [("load", "source-cp3"), ("fail", ("run-cp3", "EGRESS_POLICY_DENIED", False))])

    def test_default_lease_covers_bounded_semantic_parser_and_database_work(self) -> None:
        with patch.dict("os.environ", {
            "DAON_CLOUD_DATABASE_DSN": "postgresql://worker@database/app",
            "DAON_OBJECT_STORAGE_ENDPOINT": "object-storage:9000",
            "DAON_OBJECT_STORAGE_BUCKET": "daon-user",
            "DAON_OBJECT_ACCESS_KEY_FILE": "/run/secrets/access",
            "DAON_OBJECT_SECRET_KEY_FILE": "/run/secrets/secret",
            "DAON_PROVIDER_CREDENTIAL_KEY_FILE": "/run/secrets/provider-credential-key",
        }, clear=True):
            settings = DocumentWorkerSettings.from_env()

        self.assertEqual(settings.lease_seconds, 600)
        self.assertEqual(
            settings.provider_credential_key_file,
            __import__("pathlib").Path("/run/secrets/provider-credential-key"),
        )

    def test_claimed_existing_run_is_processed_indexed_then_completed(self) -> None:
        queue = FakeQueue(job())
        service = FakeProcessingService()
        worker = DocumentProcessingWorker(
            "worker-cp3", queue, service, FakeIndex(queue.events), clock=lambda: NOW,
        )

        worked = worker.run_once()

        self.assertTrue(worked)
        self.assertEqual(service.calls, [("source-cp3", "run-cp3")])
        self.assertEqual(queue.events, [
            ("claim", "worker-cp3"),
            ("index", "source-version-cp3"),
            ("complete", "run-cp3"),
        ])

    def test_no_job_returns_without_processing(self) -> None:
        queue = FakeQueue(None)
        service = FakeProcessingService()
        worker = DocumentProcessingWorker(
            "worker-cp3", queue, service, FakeIndex(queue.events), clock=lambda: NOW,
        )

        self.assertFalse(worker.run_once())
        self.assertEqual(service.calls, [])

    def test_processing_failure_dead_letters_same_terminal_run(self) -> None:
        queue = FakeQueue(job())
        service = FakeProcessingService(
            failure=DocumentUnderstandingError(
                "NO_AVAILABLE_UNDERSTANDING_MODEL", status=503, retryable=True,
            ),
        )
        worker = DocumentProcessingWorker(
            "worker-cp3", queue, service, FakeIndex(queue.events), clock=lambda: NOW,
        )

        self.assertTrue(worker.run_once())

        self.assertEqual(queue.events, [
            ("claim", "worker-cp3"),
            ("dead_letter", "NO_AVAILABLE_UNDERSTANDING_MODEL"),
        ])


if __name__ == "__main__":
    unittest.main()
