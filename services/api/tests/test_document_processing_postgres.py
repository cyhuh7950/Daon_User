from __future__ import annotations

import hashlib
import unittest
from contextlib import contextmanager

from daon_user_api.document_processing import DocumentProcessingContext
from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingResult,
    ParserValidation,
    SemanticUnderstanding,
)


PDF = b"%PDF-1.4\n% postgres processing fixture\n%%EOF\n"
OBJECT_ID = "a" * 32
OBJECT_KEY = f"tenant-cp3/workspace-cp3/source/{OBJECT_ID}"


class Cursor:
    def __init__(self, row=None) -> None:  # type: ignore[no-untyped-def]
        self.row = row

    def fetchone(self):  # type: ignore[no-untyped-def]
        return self.row


class FakeConnection:
    def __init__(self) -> None:
        self.state = "accepted"
        self.version = 1
        self.source_version_id = "source-version-cp3"
        self.inserts: list[str] = []

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        if sql.startswith("SELECT sv.record_id"):
            return Cursor((
                self.source_version_id, OBJECT_ID, {"filename": "contract.pdf"},
                OBJECT_KEY, hashlib.sha256(PDF).hexdigest(), len(PDF),
                "application/pdf", "completed",
            ))
        if sql.startswith("INSERT INTO processing_runs"):
            self.inserts.append("processing_runs")
            return Cursor()
        if sql.startswith("SELECT state,version,source_version_id"):
            return Cursor((self.state, self.version, self.source_version_id))
        if sql.startswith("SELECT state,version,outcome,error_code FROM transition_canon_state"):
            self.state = params[3]
            self.version += 1
            return Cursor((self.state, self.version, "succeeded", None))
        if sql.startswith("INSERT INTO understanding_results"):
            self.inserts.append("understanding_results")
            return Cursor()
        if sql.startswith("INSERT INTO extraction_evidence"):
            self.inserts.append("extraction_evidence")
            return Cursor()
        if sql.startswith("SELECT state,version FROM processing_runs"):
            return Cursor((self.state, self.version))
        raise AssertionError(sql)


class FakeCloudStore:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.capabilities: list[str] = []

    @contextmanager
    def _transaction(self, context):  # type: ignore[no-untyped-def]
        self.capabilities.append(context.capability)
        yield self.connection


class FakeObjectStorage:
    def get(self, key: str) -> bytes:
        if key != OBJECT_KEY:
            raise AssertionError(key)
        return PDF


class PostgresDocumentProcessingRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = DocumentProcessingContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
        )
        self.cloud = FakeCloudStore()
        self.repository = PostgresDocumentProcessingRepository(self.cloud, FakeObjectStorage())  # type: ignore[arg-type]

    def test_load_start_and_complete_write_canonical_lineage_in_order(self) -> None:
        document = self.repository.load_source_document(self.context, "source-cp3")
        run_id = self.repository.start(self.context, document.source_version_id)
        result = DocumentUnderstandingResult(
            document.source_id, document.source_version_id, "ready",
            ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
            SemanticUnderstanding("Daon", "Vision first", ("Vision first",)),
            ParserValidation("Vision first", "Vision first", "<p>Vision first</p>", (1, 2)),
            {"provider_code": "UPSTAGE", "parser_role": "validation_only"},
        )
        self.repository.complete(self.context, run_id, result)

        self.assertEqual(document.content, PDF)
        self.assertEqual(self.cloud.connection.state, "completed")
        self.assertEqual(self.cloud.connection.version, 5)
        self.assertEqual(self.cloud.connection.inserts, [
            "processing_runs", "understanding_results",
            "extraction_evidence", "extraction_evidence",
        ])
        self.assertEqual(self.cloud.capabilities, ["source.read", "source.process", "source.process"])

    def test_failure_transitions_started_run_without_writing_false_result(self) -> None:
        run_id = self.repository.start(self.context, "source-version-cp3")
        self.repository.fail(
            self.context, run_id, "UNDERSTANDING_PROVIDER_UNAVAILABLE", retryable=True,
        )

        self.assertEqual(self.cloud.connection.state, "failed")
        self.assertEqual(self.cloud.connection.inserts, ["processing_runs"])


if __name__ == "__main__":
    unittest.main()
