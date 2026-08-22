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
        self.states = {"ProcessingRun": "accepted", "Source": "registered"}
        self.versions = {"ProcessingRun": 1, "Source": 1}
        self.source_version_id = "source-version-cp3"
        self.inserts: list[str] = []
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        self.statements.append((sql, tuple(params)))
        if sql.startswith("SELECT sv.record_id"):
            return Cursor((
                self.source_version_id, OBJECT_ID, {"filename": "contract.pdf"},
                OBJECT_KEY, hashlib.sha256(PDF).hexdigest(), len(PDF),
                "application/pdf", "completed",
            ))
        if sql.startswith("INSERT INTO processing_runs"):
            self.inserts.append("processing_runs")
            return Cursor()
        if sql.startswith("INSERT INTO document_processing_jobs"):
            self.inserts.append("document_processing_jobs")
            return Cursor()
        if sql.startswith("SELECT state,version,source_version_id"):
            return Cursor((self.states["ProcessingRun"], self.versions["ProcessingRun"], self.source_version_id))
        if sql.startswith("SELECT sv.source_id,s.state,s.version"):
            return Cursor(("source-cp3", self.states["Source"], self.versions["Source"]))
        if sql.startswith("SELECT state,version,outcome,error_code FROM transition_canon_state"):
            entity_type = params[0]
            self.states[entity_type] = params[3]
            self.versions[entity_type] += 1
            return Cursor((self.states[entity_type], self.versions[entity_type], "succeeded", None))
        if sql.startswith("INSERT INTO understanding_results"):
            self.inserts.append("understanding_results")
            return Cursor()
        if sql.startswith("INSERT INTO extraction_evidence"):
            self.inserts.append("extraction_evidence")
            return Cursor()
        if sql.startswith("SELECT pr.state,pr.version,sv.source_id,s.state,s.version"):
            return Cursor((
                self.states["ProcessingRun"], self.versions["ProcessingRun"],
                "source-cp3", self.states["Source"], self.versions["Source"],
            ))
        if sql.startswith("SELECT pr.record_id,sv.source_id,pr.source_version_id"):
            return Cursor((
                "run-cp3", "source-cp3", self.source_version_id,
                self.states["ProcessingRun"], self.states["Source"], "leased", None,
            ))
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
        self.assertEqual(self.cloud.connection.states["ProcessingRun"], "completed")
        self.assertEqual(self.cloud.connection.versions["ProcessingRun"], 5)
        self.assertEqual(self.cloud.connection.states["Source"], "processing")
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

        self.assertEqual(self.cloud.connection.states["ProcessingRun"], "failed")
        self.assertEqual(self.cloud.connection.states["Source"], "waiting_model")
        self.assertEqual(self.cloud.connection.inserts, ["processing_runs"])

    def test_parser_conflict_moves_source_to_needs_review_instead_of_ready(self) -> None:
        run_id = self.repository.start(self.context, "source-version-cp3")
        result = DocumentUnderstandingResult(
            "source-cp3", "source-version-cp3", "needs_review",
            ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
            SemanticUnderstanding("Daon", "Vision first", ("Unsupported fact",)),
            ParserValidation("Different text", "Different text", "<p>Different text</p>", (1,)),
            {"provider_code": "UPSTAGE", "parser_role": "validation_only"},
            "UNDERSTANDING_PARSER_CONFLICT",
        )

        self.repository.complete(self.context, run_id, result)

        self.assertEqual(self.cloud.connection.states["ProcessingRun"], "completed")
        self.assertEqual(self.cloud.connection.states["Source"], "needs_review")

    def test_async_start_creates_run_and_queue_job_in_the_same_transaction(self) -> None:
        run_id = self.repository.start(
            self.context, "source-version-cp3", enqueue=True,
        )

        self.assertTrue(run_id.startswith("pr-"))
        self.assertEqual(self.cloud.connection.inserts, [
            "processing_runs", "document_processing_jobs",
        ])
        self.assertEqual(self.cloud.capabilities, ["source.process"])

    def test_async_start_is_idempotent_across_upload_request_traces(self) -> None:
        first_run_id = self.repository.start(
            self.context, "source-version-cp3", enqueue=True,
        )
        replay_context = DocumentProcessingContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-replay-cp3", "policy-v1",
        )

        replay_run_id = self.repository.start(
            replay_context, "source-version-cp3", enqueue=True,
        )

        self.assertEqual(replay_run_id, first_run_id)

    def test_async_start_reuses_completed_run_for_ready_digest_replay(self) -> None:
        self.cloud.connection.states["Source"] = "ready"
        self.cloud.connection.states["ProcessingRun"] = "completed"
        run_id = self.repository.start(self.context, "source-version-cp3", enqueue=True)

        self.assertTrue(run_id.startswith("pr-"))
        self.assertEqual(self.cloud.connection.states["Source"], "ready")
        self.assertEqual(self.cloud.connection.states["ProcessingRun"], "completed")
        self.assertEqual(self.cloud.connection.inserts, [
            "processing_runs", "document_processing_jobs",
        ])

    def test_status_is_workspace_scoped_and_omits_worker_lease_identity(self) -> None:
        status = self.repository.get_status(self.context, "run-cp3", notebook_id="notebook-cp3")

        self.assertEqual(status.processing_run_id, "run-cp3")
        self.assertEqual(status.job_state, "leased")
        self.assertFalse(hasattr(status, "lease_owner"))
        self.assertEqual(self.cloud.capabilities, ["source.read"])
        sql, params = next(
            (sql, params) for sql, params in self.cloud.connection.statements
            if sql.startswith("SELECT pr.record_id,sv.source_id,pr.source_version_id")
        )
        self.assertIn("JOIN notebook_bindings", sql)
        self.assertIn("binding_kind='source'", sql)
        self.assertEqual(params, ("notebook-cp3", "run-cp3"))


if __name__ == "__main__":
    unittest.main()
