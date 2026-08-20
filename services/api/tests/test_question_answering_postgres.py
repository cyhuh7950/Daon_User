from __future__ import annotations

import hashlib
import unittest
from contextlib import contextmanager

from daon_user_api.question_answering_postgres import (
    PostgresQuestionAnsweringRepository,
    QuestionContext,
    QuestionRepositoryError,
)
from daon_user_api.question_answering import GroundedTextResult, TextModelSelection
from daon_user_api.question_answering_service import QuestionInputSource
from daon_user_api.document_index_postgres import IndexedEvidenceChunk


PDF = b"%PDF-1.4\npage one\fpage two ORANGE-COMPASS-42\n%%EOF\n"


class Cursor:
    def __init__(self, row=None, rows=None) -> None:  # type: ignore[no-untyped-def]
        self.row = row
        self.rows = [] if rows is None else rows

    def fetchone(self):  # type: ignore[no-untyped-def]
        return self.row

    def fetchall(self):  # type: ignore[no-untyped-def]
        return self.rows


class FakeConnection:
    def __init__(self) -> None:
        self.ready_row = (
            "source-cp3", "source-version-cp3", "ready", "report.pdf",
            "0123456789abcdef0123456789abcdef",
            "tenant-cp3/workspace-cp3/source/0123456789abcdef0123456789abcdef",
            hashlib.sha256(PDF).hexdigest(),
            len(PDF), "application/pdf", "completed",
        )
        self.queries: list[str] = []
        self.run_version = 1
        self.completed_rows = []
        self.replay_fingerprint_row = None
        self.citation_row = None
        self.notebook_scope_row = None
        self.notebook_scope_rows: list[tuple[int] | None] = []

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        self.queries.append(sql)
        if sql.startswith("SELECT s.record_id,sv.record_id,s.state"):
            return Cursor(self.ready_row)
        if sql.startswith("SELECT rr.record_id,rr.canonical_json"):
            return Cursor(rows=self.completed_rows)
        if sql.startswith("SELECT r.canonical_json->>'request_fingerprint'"):
            return Cursor(self.replay_fingerprint_row)
        if sql.startswith("SELECT c.canonical_json->>'source_id'"):
            return Cursor(self.citation_row)
        if sql.startswith("SELECT 1 FROM notebooks n"):
            if self.notebook_scope_rows:
                return Cursor(self.notebook_scope_rows.pop(0))
            return Cursor(self.notebook_scope_row)
        if sql.startswith("SELECT state,version,outcome,error_code FROM transition_canon_state"):
            self.run_version += 1
            return Cursor((params[3], self.run_version, "succeeded", None))
        if sql.startswith("INSERT INTO") or sql.startswith("WITH inserted AS (INSERT INTO"):
            return Cursor()
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
        if key != "tenant-cp3/workspace-cp3/source/0123456789abcdef0123456789abcdef":
            raise AssertionError(key)
        return PDF


class PostgresQuestionAnsweringRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cloud = FakeCloudStore()
        self.repository = PostgresQuestionAnsweringRepository(
            self.cloud, FakeObjectStorage(),  # type: ignore[arg-type]
        )
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
        )

    def test_ready_current_source_version_is_required_and_pdf_is_server_read(self) -> None:
        source = self.repository.load_ready_source(
            self.context, "source-cp3", "source-version-cp3",
        )
        content = self.repository.read_current_pdf(
            self.context, "source-cp3", "source-version-cp3",
        )

        self.assertEqual(source.filename, "report.pdf")
        self.assertEqual(content.content, PDF)
        self.assertEqual(content.page_count_hint, 2)
        self.assertEqual(self.cloud.capabilities, ["question.read", "citation.read"])
        sql = " ".join(self.cloud.connection.queries)
        self.assertIn("s.state='ready'", sql)
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("index_versions", sql)

    def test_non_current_or_unready_source_is_not_disclosed(self) -> None:
        self.cloud.connection.ready_row = None

        with self.assertRaisesRegex(QuestionRepositoryError, "QUESTION_SOURCE_UNAVAILABLE"):
            self.repository.load_ready_source(
                self.context, "source-cp3", "source-version-old",
            )

    def test_completed_answer_appends_canon_run_attempt_result_citation_and_audit(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "ORANGE-COMPASS-42", "span-page-2", 1.0,
        ),)
        selection = TextModelSelection(
            "UPSTAGE", "https://api.upstage.ai/v1", "profile-upstage",
            "deployment-text", "solar-pro4", 5,
        )
        result = GroundedTextResult(
            "The phrase is ORANGE-COMPASS-42.", ("chunk-page-2",), False,
            {"total_tokens": 28},
        )

        stored = self.repository.persist_completed(
            self.context, run_id="run-cp3", source_id="source-cp3",
            source_version_id="source-version-cp3",
            question="What is the citation verification phrase?",
            selection=selection, evidence=evidence, result=result,
        )

        self.assertEqual(stored.run_id, "run-cp3")
        self.assertEqual(stored.citations[0].page, 2)
        self.assertEqual(stored.citations[0].locator, {"kind": "page", "value": "2"})
        sql = " ".join(self.cloud.connection.queries)
        for table in (
            "runs", "run_snapshots", "routing_decisions", "model_attempts",
            "run_results", "citations", "audit_events",
        ):
            self.assertIn(f"INSERT INTO {table}", sql)
        self.assertEqual(sql.count("transition_canon_state"), 5)

    def test_completed_notebook_answer_atomically_validates_source_and_binds_conversation(self) -> None:
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
            notebook_id="notebook-cp3",
        )
        self.cloud.connection.notebook_scope_row = (1,)
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "ORANGE-COMPASS-42", "span-page-2", 1.0,
        ),)
        stored = self.repository.persist_completed(
            self.context, run_id="run-cp3", source_id="source-cp3",
            source_version_id="source-version-cp3", question="verification phrase?",
            selection=TextModelSelection(
                "UPSTAGE", "https://api.upstage.ai/v1", "profile-upstage",
                "deployment-text", "solar-pro4", 5,
            ),
            evidence=evidence,
            result=GroundedTextResult(
                "ORANGE-COMPASS-42", ("chunk-page-2",), False, {"total_tokens": 8},
            ),
        )

        self.assertEqual(stored.run_id, "run-cp3")
        sql = " ".join(self.cloud.connection.queries)
        self.assertIn("FROM notebook_bindings", sql)
        self.assertIn("INSERT INTO conversations", sql)
        self.assertIn("INSERT INTO notebook_bindings", sql)
        self.assertIn("'conversation_thread'", sql)

    def test_general_conversation_persists_provider_lineage_without_source_binding_or_citation(self) -> None:
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
            notebook_id="notebook-cp3",
        )
        stored = self.repository.persist_completed(
            self.context, run_id="run-general", source_id=None,
            source_version_id=None, question="안녕하세요!",
            selection=TextModelSelection(
                "UPSTAGE", "https://api.upstage.ai/v1", "profile-upstage",
                "deployment-text", "solar-pro4", 5,
            ),
            evidence=(), result=GroundedTextResult(
                "안녕하세요. 무엇을 도와드릴까요?", (), False, {"total_tokens": 8},
            ),
            provider_called=True, context_mode="general_ungrounded", context_sources=(),
        )

        self.assertEqual(stored.citations, ())
        sql = " ".join(self.cloud.connection.queries)
        self.assertNotIn("FROM notebook_bindings", sql)
        self.assertIn("INSERT INTO conversations", sql)
        self.assertIn("INSERT INTO model_attempts", sql)
        self.assertIn("INSERT INTO notebook_bindings", sql)

    def test_notebook_answer_rejects_unbound_secondary_knowledge_before_any_write(self) -> None:
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
            notebook_id="notebook-cp3",
        )
        self.cloud.connection.notebook_scope_rows = [(1,), None]
        sources = (
            QuestionInputSource("raw_source", "source-cp3", "source-cp3", "source-version-cp3"),
            QuestionInputSource("daon_knowledge", "scope-snapshot-other", "source-k", "source-version-k"),
        )
        evidence = (
            IndexedEvidenceChunk("chunk-page-2", "source-cp3", "source-version-cp3", 2, "raw", "span-page-2", 1.0),
            IndexedEvidenceChunk("chunk-k", "source-k", "source-version-k", 1, "knowledge", "span-k", 0.9),
        )

        with self.assertRaisesRegex(QuestionRepositoryError, "NOTEBOOK_SCOPE_MISMATCH"):
            self.repository.persist_completed(
                self.context, run_id="run-cp3", source_id="source-cp3",
                source_version_id="source-version-cp3", question="mixed?",
                selection=TextModelSelection(
                    "UPSTAGE", "https://api.upstage.ai/v1", "profile-upstage",
                    "deployment-text", "solar-pro4", 5,
                ),
                evidence=evidence,
                result=GroundedTextResult("mixed", ("chunk-page-2", "chunk-k"), False, {"total_tokens": 8}),
                context_mode="mixed", context_sources=sources,
            )

        sql = " ".join(self.cloud.connection.queries)
        self.assertNotIn("INSERT INTO conversations", sql)

    def test_completed_idempotency_run_replays_persisted_result_without_mutation(self) -> None:
        self.cloud.connection.completed_rows = [(
            "result-cp3",
            {"answer": "ORANGE-COMPASS-42", "insufficient": False},
            "citation-cp3",
            {
                "source_id": "source-cp3", "source_version_id": "source-version-cp3",
                "evidence_span_id": "span-page-2", "page": 2,
            },
        )]

        replay = self.repository.load_completed(self.context, "run-cp3")

        self.assertIsNotNone(replay)
        self.assertEqual(replay.answer, "ORANGE-COMPASS-42")
        self.assertEqual(replay.citations[0].page, 2)

    def test_authoritative_replay_requires_exact_fingerprint_and_notebook_scope(self) -> None:
        fingerprint = "sha256:" + "a" * 64
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
            "notebook-cp3",
        )
        self.cloud.connection.replay_fingerprint_row = (fingerprint,)
        self.cloud.connection.completed_rows = [(
            "result-cp3", {"answer": "stored", "insufficient": False}, None, None,
        )]

        replay = self.repository.load_completed_for_replay(
            self.context, "run-cp3", fingerprint,
        )
        self.assertEqual(replay.answer, "stored")
        with self.assertRaisesRegex(QuestionRepositoryError, "IDEMPOTENCY_KEY_REUSED"):
            self.repository.load_completed_for_replay(
                self.context, "run-cp3", "sha256:" + "b" * 64,
            )
        sql = " ".join(self.cloud.connection.queries)
        self.assertIn("nb.notebook_id=%s", sql)
        self.assertIn("nb.binding_kind='conversation_thread'", sql)

    def test_legacy_completed_run_without_fingerprint_fails_closed(self) -> None:
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
            "notebook-cp3",
        )
        self.cloud.connection.replay_fingerprint_row = (None,)
        with self.assertRaisesRegex(QuestionRepositoryError, "QUESTION_REPLAY_UNAVAILABLE"):
            self.repository.load_completed_for_replay(
                self.context, "run-cp3", "sha256:" + "a" * 64,
            )

    def test_daon_generated_text_citation_is_rendered_without_pdf_object(self) -> None:
        self.cloud.connection.citation_row = (
            "source-knowledge", "version-knowledge", "1", "1",
            {
                "kind": "approved_knowledge_snapshot",
                "text": "Daon이 생성하고 승인한 일반 텍스트 지식",
            },
            "studio_output", "approved-knowledge.txt", "ready",
            "span-knowledge",
        )

        content, locator = self.repository.read_citation_content(
            self.context, "citation-knowledge",
        )

        self.assertEqual(content.media_type, "text/plain; charset=utf-8")
        self.assertEqual(content.content.decode("utf-8"), "Daon이 생성하고 승인한 일반 텍스트 지식")
        self.assertEqual(locator, {"kind": "section", "value": "span-knowledge"})


if __name__ == "__main__":
    unittest.main()
