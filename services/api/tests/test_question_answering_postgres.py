from __future__ import annotations

import hashlib
import json
import sqlite3
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
        self.source_binding_active = True

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        self.queries.append(sql)
        if sql.startswith("SELECT s.record_id,sv.record_id,s.state"):
            if "nb.binding_kind='source'" in sql and not self.source_binding_active:
                return Cursor()
            if "COALESCE(sv.canonical_json" in sql:
                if self.ready_row is None: return Cursor()
                # This method's SELECT is distinct from _row's object-byte DTO.
                kind = self.ready_row[4] if len(self.ready_row) == 5 else 'uploaded'
                row = (*self.ready_row[:4], kind)
                if 'o.content_type,o.status' in sql.split(' FROM sources', 1)[0]:
                    row += (self.ready_row[8], self.ready_row[9]) if len(self.ready_row) == 10 else (None, None)
                return Cursor(row)
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
    content = PDF

    def get(self, key: str) -> bytes:
        if key != "tenant-cp3/workspace-cp3/source/0123456789abcdef0123456789abcdef":
            raise AssertionError(key)
        return self.content


class PostgresQuestionAnsweringRepositoryTests(unittest.TestCase):
    def test_xlsx_ready_metadata_matches_real_select_row_shape_and_scope_guards(self):
        from dataclasses import replace
        class SqlConnection:
            def __init__(self):
                self.db = sqlite3.connect(':memory:'); self.columns = (); self.rows = []
                self.db.executescript('''
                    CREATE TABLE sources(tenant_id,workspace_id,record_id,state,canonical_json);
                    CREATE TABLE source_versions(tenant_id,workspace_id,record_id,source_id,object_id,version,canonical_json);
                    CREATE TABLE object_records(tenant_id,workspace_id,object_id,content_type,status);
                    CREATE TABLE index_versions(tenant_id,workspace_id,source_version_id);
                    CREATE TABLE deletion_requests(tenant_id,workspace_id,source_id,source_active);
                    CREATE TABLE notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id);
                    CREATE TABLE notebook_source_unbindings(tenant_id,workspace_id,notebook_id,source_id,source_version_id);
                ''')
                scope = ('tenant-cp3', 'workspace-cp3')
                self.db.execute('INSERT INTO sources VALUES(?,?,?,?,?)', (*scope, 'source-cp3', 'ready', json.dumps({'kind': 'uploaded'})))
                self.db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?,?,?)', (*scope, 'source-version-cp3', 'source-cp3', 'object-xlsx', 1, json.dumps({'filename': 'qa.xlsx'})))
                self.db.execute('INSERT INTO object_records VALUES(?,?,?,?,?)', (*scope, 'object-xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'completed'))
                self.db.execute('INSERT INTO index_versions VALUES(?,?,?)', (*scope, 'source-version-cp3'))
                self.db.execute('INSERT INTO notebook_bindings VALUES(?,?,?,?,?,?)', (*scope, 'notebook-cp3', 'source', 'source-cp3', 'source-version-cp3'))
            def execute(self, sql, params=()):
                cursor = self.db.execute(sql.replace('%s', '?'), params)
                self.columns = tuple(column[0] for column in cursor.description)
                row = cursor.fetchone(); self.rows.append(row)
                return Cursor(row)
        context = replace(self.context, notebook_id='notebook-cp3')
        for scenario in ('ready', 'wrong-mime', 'incomplete', 'missing-manifest', 'other-object-scope', 'unbound', 'old-version', 'deleted', 'other-notebook'):
            with self.subTest(scenario=scenario):
                connection = SqlConnection(); self.cloud.connection = connection
                try:
                    if scenario == 'wrong-mime': connection.db.execute("UPDATE object_records SET content_type='text/plain'")
                    if scenario == 'incomplete': connection.db.execute("UPDATE object_records SET status='pending'")
                    if scenario == 'missing-manifest': connection.db.execute('DELETE FROM object_records')
                    if scenario == 'other-object-scope': connection.db.execute("UPDATE object_records SET workspace_id='workspace-other'")
                    if scenario == 'unbound': connection.db.execute('DELETE FROM notebook_bindings')
                    if scenario == 'old-version': connection.db.execute("INSERT INTO source_versions SELECT tenant_id,workspace_id,'new-version',source_id,object_id,2,canonical_json FROM source_versions")
                    if scenario == 'deleted': connection.db.execute("INSERT INTO deletion_requests VALUES('tenant-cp3','workspace-cp3','source-cp3',false)")
                    selected_context = replace(context, notebook_id='notebook-other') if scenario == 'other-notebook' else context
                    if scenario == 'ready':
                        source = self.repository.load_ready_source(selected_context, 'source-cp3', 'source-version-cp3')
                        self.assertEqual((source.source_id, source.source_version_id, source.filename), ('source-cp3', 'source-version-cp3', 'qa.xlsx'))
                        self.assertEqual(connection.rows[-1], ('source-cp3', 'source-version-cp3', 'ready', 'qa.xlsx', 'uploaded', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'completed'))
                        self.assertEqual(connection.columns[-2:], ('content_type', 'status'))
                    else:
                        with self.assertRaisesRegex(QuestionRepositoryError, '^QUESTION_SOURCE_UNAVAILABLE$'):
                            self.repository.load_ready_source(selected_context, 'source-cp3', 'source-version-cp3')
                finally: connection.db.close()

    def test_xlsx_ready_source_and_answer_preserve_sheet_cell_locator(self):
        from test_xlsx_source import parts, zipped
        raw = zipped(parts()); row = list(self.cloud.connection.ready_row)
        row[3], row[6], row[7], row[8] = 'qa.xlsx', hashlib.sha256(raw).hexdigest(), len(raw), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        self.cloud.connection.ready_row = tuple(row); self.repository._object_storage.content = raw
        self.repository.load_ready_source(self.context, 'source-cp3', 'source-version-cp3')
        locator = '{"sheet":"예산","range":"A1"}'
        evidence = (IndexedEvidenceChunk('chunk-cell', 'source-cp3', 'source-version-cp3', 1, '57000', 'span-cell', 1.0, 'sheet_cell', locator),)
        stored = self.repository.persist_completed(self.context, run_id='run-cell', source_id='source-cp3', source_version_id='source-version-cp3', question='예산?', selection=TextModelSelection('OLLAMA', 'https://ollama.example', 'profile', 'deployment', 'llama3.2:3b', 1), evidence=evidence, result=GroundedTextResult('57000', ('chunk-cell',), False, {'total_tokens': 10}))
        self.assertEqual(stored.citations[0].locator, {'kind': 'sheet_cell', 'value': locator})
        self.cloud.connection.completed_rows = [('rr-cell', {'answer': '57000', 'insufficient': False}, stored.citations[0].citation_id,
            {'source_id': 'source-cp3', 'source_version_id': 'source-version-cp3', 'evidence_span_id': 'span-cell', 'page': 1, 'locator': {'kind': 'sheet_cell', 'value': locator}}, 'local', None)]
        reread = self.repository.load_completed(self.context, 'run-cell')
        self.assertEqual(reread.citations[0].locator, {'kind': 'sheet_cell', 'value': locator})

    def test_image_citation_returns_original_mime_and_bound_display_region(self):
        from test_image_document_understanding import image_bytes
        from dataclasses import replace
        self.context = replace(self.context, notebook_id="notebook-cp3")
        original = image_bytes()
        locator = {"kind": "image_region", "value": "5,7,80,20,120,60"}
        self.cloud.connection.citation_row = ("source-cp3", "source-version-cp3", "1", "1",
            {"kind": "image_region_fact", "text": "ORANGE-42", "source_id": "source-cp3", "source_version_id": "source-version-cp3", "locator": locator},
            "uploaded", "qa.png", "ready", "span-image", locator)
        row = list(self.cloud.connection.ready_row)
        row[3], row[6], row[7], row[8] = "qa.png", hashlib.sha256(original).hexdigest(), len(original), "image/png"
        self.cloud.connection.ready_row = tuple(row)
        self.repository._object_storage.content = original
        content, actual_locator = self.repository.read_citation_content(self.context, "citation-image")
        self.assertEqual((content.content, content.media_type, actual_locator), (original, "image/png", locator))
        self.assertIn("es.source_version_id=c.source_version_id", " ".join(self.cloud.connection.queries))
        for run_state in ("failed", "accepted", "vision_llm_understanding"):
            with self.subTest(run_state=run_state):
                self.cloud.connection.ready_row = (*row[:9], run_state)
                with self.assertRaisesRegex(QuestionRepositoryError, "CITATION_CONTENT_UNAVAILABLE"):
                    self.repository.read_citation_content(self.context, "citation-image")
        self.cloud.connection.ready_row = tuple(row)
        for invalid in ("5,7,180,20,120,60", "5,7,80,20,121,60"):
            with self.subTest(region=invalid):
                wrong_locator = {"kind": "image_region", "value": invalid}
                saved = self.cloud.connection.citation_row
                self.cloud.connection.citation_row = (*saved[:4], {**saved[4], "locator": wrong_locator}, *saved[5:9], wrong_locator)
                with self.assertRaisesRegex(QuestionRepositoryError, "CITATION_CONTENT_UNAVAILABLE"):
                    self.repository.read_citation_content(self.context, "citation-image")
                self.cloud.connection.citation_row = saved
        self.cloud.connection.source_binding_active = False
        with self.assertRaises(QuestionRepositoryError):
            self.repository.read_citation_content(self.context, "citation-image")
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

    def test_indexed_ready_images_are_accepted_with_existing_notebook_version_guards(self) -> None:
        from dataclasses import replace
        self.context = replace(self.context, notebook_id="notebook-cp3")
        for suffix in ("png", "jpg", "jpeg"):
            with self.subTest(suffix=suffix):
                self.cloud.connection.ready_row = ("source-cp3", "source-version-cp3", "ready", "qa." + suffix, "uploaded")
                source = self.repository.load_ready_source(self.context, "source-cp3", "source-version-cp3")
                self.assertEqual((source.source_id, source.source_version_id, source.filename),
                                 ("source-cp3", "source-version-cp3", "qa." + suffix))
                sql = self.cloud.connection.queries[-1]
                for guard in ("s.state='ready'", "FROM index_versions iv", "iv.source_version_id=sv.record_id",
                              "newer.version>sv.version", "dr.source_active=false", "nb.binding_kind='source'",
                              "nb.version_id=sv.record_id", "nb.notebook_id=%s", "FROM notebook_source_unbindings u"):
                    self.assertIn(guard, sql)

    def test_image_ready_context_preserves_unready_unbound_and_unsupported_rejections(self) -> None:
        from dataclasses import replace
        self.context = replace(self.context, notebook_id="notebook-cp3")
        for filename, state, bound in (
            ("qa.png", "processing", True), ("qa.jpg", "needs_review", True),
            ("qa.jpeg", "ready", False), ("qa.png", "failed", True),
            ("qa.docx", "ready", True), ("qa.pptx", "ready", True),
            ("qa.xlsx", "ready", True), ("qa.wav", "ready", True),
            ("qa.mp3", "ready", True), ("qa.m4a", "ready", True),
        ):
            with self.subTest(filename=filename, state=state, bound=bound):
                self.cloud.connection.ready_row = ("source-cp3", "source-version-cp3", state, filename, "uploaded")
                self.cloud.connection.source_binding_active = bound
                with self.assertRaisesRegex(QuestionRepositoryError, "^QUESTION_SOURCE_UNAVAILABLE$"):
                    self.repository.load_ready_source(self.context, "source-cp3", "source-version-cp3")

    def test_question_rejects_source_unbound_from_selected_notebook(self) -> None:
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1", "notebook-other",
        )
        self.cloud.connection.source_binding_active = False
        with self.assertRaisesRegex(QuestionRepositoryError, "QUESTION_SOURCE_UNAVAILABLE"):
            self.repository.load_ready_source(self.context, "source-cp3", "source-version-cp3")
        self.assertIn("nb.binding_kind='source'", " ".join(self.cloud.connection.queries))

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
            egress_authorization={"frozen_routing_context": {
                "classification": "internal", "destination": "api.upstage.ai",
                "masking_required": True, "payload_bytes": 512,
                "redaction_required": True,
            }},
        )

        self.assertEqual(stored.run_id, "run-cp3")
        self.assertEqual(stored.citations[0].page, 2)
        self.assertEqual(stored.citations[0].locator, {"kind": "page", "value": "2"})
        self.assertEqual(stored.provider_kind, "external_api")
        self.assertEqual(stored.egress_scope, {
            "classification": "internal", "destination": "api.upstage.ai",
            "masking_required": True, "max_bytes": 512,
            "redaction_required": True,
        })
        sql = " ".join(self.cloud.connection.queries)
        for table in (
            "runs", "run_snapshots", "routing_decisions", "model_attempts",
            "run_results", "citations", "audit_events",
        ):
            self.assertIn(f"INSERT INTO {table}", sql)
        self.assertEqual(sql.count("transition_canon_state"), 5)

    def test_completed_image_answer_persists_exact_region_not_page_locator(self) -> None:
        evidence = (IndexedEvidenceChunk("chunk-image", "source-cp3", "source-version-cp3", 1, "ORANGE-42", "span-image", 1.0, "image_region", "5,7,80,20,120,60"),)
        stored = self.repository.persist_completed(self.context, run_id="run-image", source_id="source-cp3", source_version_id="source-version-cp3", question="image fact?",
            selection=TextModelSelection("OLLAMA", "https://ollama.example", "profile", "deployment", "llama3.2:3b", 1), evidence=evidence,
            result=GroundedTextResult("ORANGE-42", ("chunk-image",), False, {"total_tokens": 10}))
        self.assertEqual(stored.citations[0].locator, {"kind": "image_region", "value": "5,7,80,20,120,60"})
        self.assertEqual(stored.citations[0].source_version_id, "source-version-cp3")

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
            "local_runtime", None,
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
        self.cloud.connection.replay_fingerprint_row = (fingerprint, True)
        self.cloud.connection.completed_rows = [(
            "result-cp3", {"answer": "stored", "insufficient": False}, None, None,
            "local_runtime", None,
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
        self.cloud.connection.replay_fingerprint_row = (None, True)
        with self.assertRaisesRegex(QuestionRepositoryError, "QUESTION_REPLAY_UNAVAILABLE"):
            self.repository.load_completed_for_replay(
                self.context, "run-cp3", "sha256:" + "a" * 64,
            )

    def test_authoritative_replay_fails_closed_when_current_conversation_binding_is_missing(self) -> None:
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
            "notebook-cp3",
        )
        self.cloud.connection.replay_fingerprint_row = ("sha256:" + "a" * 64, False)

        with self.assertRaisesRegex(QuestionRepositoryError, "NOTEBOOK_NOT_FOUND"):
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

    def test_uploaded_txt_citation_reads_current_bound_original_not_model_text(self) -> None:
        original = b"ORANGE-COMPASS-42 is the source marker.\n"
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1", "notebook-cp3",
        )
        self.cloud.connection.citation_row = (
            "source-cp3", "source-version-cp3", "1", "1",
            {"kind": "text_source_verbatim_fact", "text": "ORANGE-COMPASS-42"},
            "uploaded", "report.txt", "ready", "span-txt",
        )
        self.cloud.connection.ready_row = (
            "source-cp3", "source-version-cp3", "ready", "report.txt",
            "0123456789abcdef0123456789abcdef",
            "tenant-cp3/workspace-cp3/source/0123456789abcdef0123456789abcdef",
            hashlib.sha256(original).hexdigest(), len(original), "text/plain", "completed",
        )
        self.repository._object_storage.content = original

        content, locator = self.repository.read_citation_content(self.context, "citation-txt")
        self.assertEqual((content.content, content.media_type), (original, "text/plain; charset=utf-8"))
        self.assertEqual(locator, {"kind": "section", "value": "span-txt"})
        self.assertIn("nb.binding_kind='source'", " ".join(self.cloud.connection.queries))

        self.repository._object_storage.content = b"tampered"
        with self.assertRaisesRegex(QuestionRepositoryError, "CITATION_CONTENT_UNAVAILABLE"):
            self.repository.read_citation_content(self.context, "citation-txt")

    def test_uploaded_markdown_and_csv_citation_reads_exact_current_bound_original(self) -> None:
        self.context = QuestionContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1", "notebook-cp3",
        )
        for filename, object_mime in (("report.md", "text/markdown"), ("report.csv", "text/csv")):
            with self.subTest(filename=filename):
                original = b"ORANGE-COMPASS-42 is the source marker.\n"
                self.cloud.connection.citation_row = (
                    "source-cp3", "source-version-cp3", "1", "1",
                    {"kind": "text_source_verbatim_fact", "text": "ORANGE-COMPASS-42"},
                    "uploaded", filename, "ready", "span-text",
                )
                self.cloud.connection.ready_row = (
                    "source-cp3", "source-version-cp3", "ready", filename,
                    "0123456789abcdef0123456789abcdef",
                    "tenant-cp3/workspace-cp3/source/0123456789abcdef0123456789abcdef",
                    hashlib.sha256(original).hexdigest(), len(original), object_mime, "completed",
                )
                self.repository._object_storage.content = original
                content, locator = self.repository.read_citation_content(self.context, "citation-text")
                self.assertEqual((content.content, content.media_type), (original, "text/plain; charset=utf-8"))
                self.assertEqual(locator, {"kind": "section", "value": "span-text"})
                self.cloud.connection.source_binding_active = False
                with self.assertRaisesRegex(QuestionRepositoryError, "QUESTION_SOURCE_UNAVAILABLE"):
                    self.repository.read_citation_content(self.context, "citation-text")
                self.cloud.connection.source_binding_active = True


if __name__ == "__main__":
    unittest.main()
