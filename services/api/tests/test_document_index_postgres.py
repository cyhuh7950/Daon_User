from __future__ import annotations

import unittest
from contextlib import contextmanager

from daon_user_api.document_index_postgres import PostgresDocumentIndex
from daon_user_api.document_processing import DocumentProcessingContext
from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingResult,
    ParserValidation,
    SemanticUnderstanding,
)


class Cursor:
    def __init__(self, row=None) -> None:  # type: ignore[no-untyped-def]
        self.row = row

    def fetchone(self):  # type: ignore[no-untyped-def]
        return self.row


class FakeConnection:
    def __init__(self) -> None:
        self.source_state = "processing"
        self.source_version = 3
        self.inserts: list[str] = []
        self.index_payload = None
        self.understanding_exists = True

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        if sql.startswith("SELECT record_id,canonical_json FROM understanding_results"):
            if not self.understanding_exists:
                return Cursor()
            return Cursor(("understanding-cp3", {
                "source_id": "source-cp3",
                "status": "ready",
                "semantic": {
                    "key_facts": ["계약 기간은 12개월", "해지 통보는 30일 전"],
                },
            }))
        if sql.startswith("SELECT s.state,s.version,sv.source_id"):
            return Cursor((self.source_state, self.source_version, "source-cp3"))
        if sql.startswith("SELECT state,version,outcome,error_code FROM transition_canon_state"):
            self.source_state = params[3]
            self.source_version += 1
            return Cursor((self.source_state, self.source_version, "succeeded", None))
        if sql.startswith("INSERT INTO evidence_spans"):
            self.inserts.append("evidence_spans")
            return Cursor()
        if sql.startswith("INSERT INTO index_versions"):
            self.inserts.append("index_versions")
            self.index_payload = params[4].obj
            return Cursor()
        if sql.startswith("SELECT canonical_json FROM index_versions"):
            return Cursor(None if self.index_payload is None else (self.index_payload,))
        raise AssertionError(sql)


class FakeCloudStore:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.capabilities: list[str] = []

    @contextmanager
    def _transaction(self, context):  # type: ignore[no-untyped-def]
        self.capabilities.append(context.capability)
        yield self.connection


def ready_result() -> DocumentUnderstandingResult:
    return DocumentUnderstandingResult(
        "source-cp3", "source-version-cp3", "ready",
        ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
        SemanticUnderstanding(
            "계약서", "계약 기간과 해지 조건", ("계약 기간은 12개월", "해지 통보는 30일 전"),
        ),
        ParserValidation(
            "계약 기간은 12개월. 해지 통보는 30일 전.", "", "", (1, 2),
            ((1, "계약 기간은 12개월"), (2, "해지 통보는 30일 전")),
        ),
        {"provider_code": "UPSTAGE", "parser_role": "validation_only"},
    )


class PostgresDocumentIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = DocumentProcessingContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
        )
        self.cloud = FakeCloudStore()
        self.index = PostgresDocumentIndex(self.cloud)  # type: ignore[arg-type]

    def test_ready_semantic_facts_create_page_evidence_index_and_ready_source(self) -> None:
        index_version_id = self.index.index_result(self.context, ready_result())

        self.assertTrue(index_version_id.startswith("iv-"))
        self.assertEqual(self.cloud.connection.inserts, [
            "evidence_spans", "evidence_spans", "index_versions",
        ])
        self.assertEqual(self.cloud.connection.source_state, "ready")
        self.assertEqual(
            [chunk["page"] for chunk in self.cloud.connection.index_payload["chunks"]],
            [1, 2],
        )
        self.assertEqual(
            self.cloud.connection.index_payload["understanding_result_id"],
            "understanding-cp3",
        )

    def test_search_is_fixed_to_source_version_and_keeps_page_evidence(self) -> None:
        self.index.index_result(self.context, ready_result())

        results = self.index.search(
            self.context, source_id="source-cp3",
            source_version_id="source-version-cp3", query="해지 통보",
        )
        wrong_version = self.index.search(
            self.context, source_id="source-cp3",
            source_version_id="source-version-old", query="해지 통보",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].page, 2)
        self.assertTrue(results[0].evidence_span_id.startswith("span-"))
        self.assertEqual(wrong_version, ())

    def test_needs_review_or_unlocated_fact_never_creates_ready_index(self) -> None:
        conflict = ready_result()
        conflict = DocumentUnderstandingResult(
            conflict.source_id, conflict.source_version_id, "needs_review",
            conflict.substates, conflict.semantic, conflict.parser, conflict.lineage,
            "UNDERSTANDING_PARSER_CONFLICT",
        )
        with self.assertRaisesRegex(
            DocumentUnderstandingError, "DOCUMENT_INDEX_REQUIRES_READY_UNDERSTANDING",
        ):
            self.index.index_result(self.context, conflict)
        self.assertEqual(self.cloud.connection.inserts, [])

    def test_missing_canonical_understanding_result_blocks_index_creation(self) -> None:
        self.cloud.connection.understanding_exists = False

        with self.assertRaisesRegex(
            DocumentUnderstandingError, "UNDERSTANDING_RESULT_NOT_FOUND",
        ):
            self.index.index_result(self.context, ready_result())

        self.assertEqual(self.cloud.connection.inserts, [])
        self.assertEqual(self.cloud.connection.source_state, "processing")


if __name__ == "__main__":
    unittest.main()
