from __future__ import annotations

import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from daon_user_api.document_processing_queue import DocumentProcessingJob
from daon_user_api.document_processing_worker import DocumentProcessingWorker, DocumentWorkerSettings
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
    def test_default_lease_covers_bounded_semantic_parser_and_database_work(self) -> None:
        with patch.dict("os.environ", {
            "DAON_CLOUD_DATABASE_DSN": "postgresql://worker@database/app",
            "DAON_OBJECT_STORAGE_ENDPOINT": "object-storage:9000",
            "DAON_OBJECT_STORAGE_BUCKET": "daon-user",
            "DAON_OBJECT_ACCESS_KEY_FILE": "/run/secrets/access",
            "DAON_OBJECT_SECRET_KEY_FILE": "/run/secrets/secret",
        }, clear=True):
            settings = DocumentWorkerSettings.from_env()

        self.assertEqual(settings.lease_seconds, 600)

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
