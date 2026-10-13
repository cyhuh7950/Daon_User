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
        self.understanding_payload = None

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        if sql.startswith("SELECT record_id,canonical_json FROM understanding_results"):
            if not self.understanding_exists:
                return Cursor()
            return Cursor(("understanding-cp3", self.understanding_payload or {
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


def paraphrased_ready_result() -> DocumentUnderstandingResult:
    result = ready_result()
    return DocumentUnderstandingResult(
        result.source_id, result.source_version_id, result.status,
        result.substates, result.semantic,
        ParserValidation(
            "이 계약의 유효 기간은 총 12 개월입니다. 해지는 최소 삼십 일 전에 알려야 합니다.",
            "", "", (1, 2),
            (
                (1, "이 계약의 유효 기간은 총 12 개월입니다."),
                (2, "해지는 최소 삼십 일 전에 알려야 합니다."),
            ),
        ),
        result.lineage,
    )


class PostgresDocumentIndexTests(unittest.TestCase):
    def test_image_region_is_indexed_and_search_preserves_exact_locator(self):
        from dataclasses import replace
        result = ready_result()
        regions = ({"text": "계약 기간은 12개월", "value": "10,20,100,30,1000,200"},
                   {"text": "해지 통보는 30일 전", "value": "10,80,100,30,1000,200"})
        result = replace(result, parser=replace(result.parser, regions=regions), lineage={"source_format": "image", "image_width": "1000", "image_height": "200", "source_digest": "a" * 64, "exif_orientation": "1"})
        self.cloud.connection.understanding_payload = {
            "source_id": result.source_id, "source_version_id": result.source_version_id,
            "status": "ready", "semantic": {"key_facts": list(result.semantic.key_facts)},
            "parser_validation": {"regions": list(regions)}, "lineage": dict(result.lineage),
        }
        self.index.index_result(self.context, result)
        chunks = self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query="계약 기간")
        self.assertEqual(chunks[0].locator_kind, "image_region")
        self.assertEqual(chunks[0].locator_value, "10,20,100,30,1000,200")
        self.assertEqual(self.cloud.connection.index_payload["strategy"], "native_image_facts_with_ocr_region_validation")

    def test_image_canonical_binding_mismatch_has_no_writes_or_ready_transition(self):
        from copy import deepcopy
        for field in ("source_version_id", "regions", "image_width", "image_height", "source_digest", "exif_orientation"):
            with self.subTest(field=field):
                self.setUp()
                self.test_image_region_is_indexed_and_search_preserves_exact_locator()
                payload = deepcopy(self.cloud.connection.understanding_payload)
                result_lineage = dict(payload["lineage"])
                regions = tuple(deepcopy(payload["parser_validation"]["regions"]))
                from dataclasses import replace
                result = replace(ready_result(), parser=replace(ready_result().parser, regions=regions), lineage=result_lineage)
                if field == "source_version_id":
                    payload[field] = "source-version-other"
                elif field == "regions":
                    payload["parser_validation"][field][0]["value"] = "11,20,100,30,1000,200"
                else:
                    payload["lineage"][field] = "different"
                self.cloud.connection.understanding_payload = payload
                self.cloud.connection.inserts.clear()
                self.cloud.connection.source_state = "processing"
                with self.assertRaisesRegex(DocumentUnderstandingError, "UNDERSTANDING_RESULT_MISMATCH"):
                    self.index.index_result(self.context, result)
                self.assertEqual(self.cloud.connection.inserts, [])
                self.assertEqual(self.cloud.connection.source_state, "processing")

    def test_image_without_region_never_falls_back_to_page(self):
        from dataclasses import replace
        with self.assertRaisesRegex(DocumentUnderstandingError, "IMAGE_REGION_INVALID"):
            self.index.index_result(self.context, replace(ready_result(), lineage={"source_format": "image"}))
        self.assertEqual(self.cloud.connection.inserts, [])

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

    def test_txt_semantic_fact_creates_section_locator_without_pdf_fallback(self) -> None:
        result = ready_result()
        result = DocumentUnderstandingResult(
            result.source_id, result.source_version_id, result.status, result.substates,
            result.semantic, result.parser,
            {"provider_code": "OLLAMA", "source_format": "txt"},
        )
        self.index.index_result(self.context, result)
        chunks = self.index.search(
            self.context, source_id=result.source_id,
            source_version_id=result.source_version_id, query="해지 통보",
        )
        self.assertEqual(chunks[0].locator_kind, "section")
        self.assertEqual(
            self.cloud.connection.index_payload["chunks"][0]["evidence_kind"],
            "text_source_verbatim_fact",
        )

    def test_markdown_and_csv_semantic_facts_create_original_text_sections(self) -> None:
        for source_format in ("md", "csv"):
            with self.subTest(source_format=source_format):
                self.cloud = FakeCloudStore()
                self.index = PostgresDocumentIndex(self.cloud)
                original = ready_result()
                result = DocumentUnderstandingResult(
                    original.source_id, original.source_version_id, original.status,
                    original.substates, original.semantic, original.parser,
                    {"provider_code": "OLLAMA", "source_format": source_format},
                )
                self.index.index_result(self.context, result)
                self.assertEqual(
                    self.cloud.connection.index_payload["chunks"][0]["evidence_kind"],
                    "text_source_verbatim_fact",
                )
                self.assertEqual(
                    self.index.search(self.context, source_id=result.source_id,
                                      source_version_id=result.source_version_id, query="해지 통보")[0].locator_kind,
                    "section",
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

    def test_ready_paraphrase_uses_parser_page_text_as_grounded_fallback(self) -> None:
        self.index.index_result(self.context, paraphrased_ready_result())

        self.assertEqual(self.cloud.connection.source_state, "ready")
        self.assertEqual(
            [chunk["page"] for chunk in self.cloud.connection.index_payload["chunks"]],
            [1, 2],
        )
        self.assertEqual(
            [chunk["text"] for chunk in self.cloud.connection.index_payload["chunks"]],
            [
                "이 계약의 유효 기간은 총 12 개월입니다.",
                "해지는 최소 삼십 일 전에 알려야 합니다.",
            ],
        )
        self.assertEqual(
            self.cloud.connection.index_payload["strategy"],
            "vision_llm_facts_with_parser_page_fallback",
        )

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
