from __future__ import annotations

import unittest
import json
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
        self.extraction_payload = None

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        if sql.startswith('SELECT NOT EXISTS (SELECT 1 FROM identity_auth_source_access'):
            # 기존 대역에는 ACL 행이 없다. 실제 deny 판정은 격리 PG 반례에서 검증한다.
            return Cursor((True,))
        if sql.startswith("SELECT canonical_json FROM extraction_evidence"):
            return Cursor(None if self.extraction_payload is None else (self.extraction_payload,))
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
    def audio_fixture(self):
        from copy import deepcopy
        from test_audio_document_understanding import ready_audio_result
        result = ready_audio_result()
        self.cloud.connection.understanding_payload = deepcopy({
            'source_id': result.source_id, 'source_version_id': result.source_version_id, 'status': 'ready',
            'semantic': {'key_facts': list(result.semantic.key_facts)},
            'parser_validation': {'text': '', 'markdown': '', 'html': '', 'pages': [], 'page_texts': [],
                'regions': [], 'role': 'validation_only', 'audio_evidence': list(result.parser.audio_evidence)}, 'lineage': dict(result.lineage)})
        self.cloud.connection.extraction_payload = deepcopy({'source_version_id': result.source_version_id,
            'understanding_result_id': 'understanding-cp3', 'parser_role': 'validation_only',
            'transcript_sha256': result.lineage['transcript_sha256'], 'audio_evidence': list(result.parser.audio_evidence)})
        return result

    def test_audio_index_uses_verified_facts_and_exact_time_locator(self):
        result = self.audio_fixture()
        self.index.index_result(self.context, result)
        payload = self.cloud.connection.index_payload
        self.assertEqual(payload['strategy'], 'native_audio_facts_with_transcript_validation')
        self.assertEqual([c['evidence_kind'] for c in payload['chunks']], ['audio_segment_fact'])
        chunks = self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='unrelated weather')
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].locator_kind, 'audio_segment')
        self.assertEqual(chunks[0].locator_value, result.parser.audio_evidence[0]['locator_value'])
        self.assertEqual(chunks[0].text, 'Code REF-DELTA-17 57000 KRW')

    def test_audio_index_rejects_tampered_canonical_or_extraction_without_writes(self):
        for scope in ('canonical', 'extraction', 'transcript'):
            self.setUp();result = self.audio_fixture()
            if scope == 'canonical': self.cloud.connection.understanding_payload['parser_validation']['audio_evidence'][0]['text'] = 'invented'
            elif scope == 'extraction': self.cloud.connection.extraction_payload['transcript_sha256'] = 'c' * 64
            else: result.lineage['transcript_sha256'] = 'c' * 64
            with self.subTest(scope=scope), self.assertRaises(DocumentUnderstandingError): self.index.index_result(self.context, result)
            self.assertEqual(self.cloud.connection.inserts, [])
            self.assertEqual(self.cloud.connection.source_state, 'processing')

    def test_audio_search_rejects_wrong_kind_time_text_or_version(self):
        for field, value in (('evidence_kind', 'parser_page_grounded_fallback'), ('locator_value', '{"segment":"s:1","start_ms":1,"end_ms":1000,"start":0,"end":27}'), ('text', 'invented'), ('source_version_id', 'other-version')):
            self.setUp();result = self.audio_fixture();self.index.index_result(self.context, result)
            self.cloud.connection.index_payload['chunks'][0][field] = value
            with self.subTest(field=field), self.assertRaises(DocumentUnderstandingError):
                self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='code')

    def pptx_fixture(self):
        from copy import deepcopy
        from test_pptx_document_understanding import ready_pptx_result
        result = ready_pptx_result()
        self.cloud.connection.understanding_payload = deepcopy({
            'source_id': result.source_id, 'source_version_id': result.source_version_id, 'status': 'ready',
            'semantic': {'key_facts': list(result.semantic.key_facts)},
            'parser_validation': {'text': '', 'markdown': '', 'html': '', 'pages': [], 'page_texts': [],
                'regions': [], 'role': 'validation_only', 'slide_evidence': list(result.parser.slide_evidence)}, 'lineage': dict(result.lineage)})
        self.cloud.connection.extraction_payload = deepcopy({'source_version_id': result.source_version_id,
            'understanding_result_id': 'understanding-cp3', 'parser_role': 'validation_only',
            'slide_evidence': list(result.parser.slide_evidence)})
        return result

    def test_pptx_index_search_preserves_only_original_block_locator_without_page_fallback(self):
        result = self.pptx_fixture()
        self.index.index_result(self.context, result)
        payload = self.cloud.connection.index_payload
        self.assertEqual(payload['strategy'], 'native_pptx_facts_with_block_validation')
        self.assertTrue(all(c['evidence_kind'] == 'pptx_block_fact' for c in payload['chunks']))
        chunks = self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='unrelated weather')
        self.assertEqual({c.text for c in chunks}, {'REF-DELTA-17', '2026-12-08', '57000', 'KRW'})
        self.assertTrue(all(c.locator_kind == 'pptx_block' and c.score == 0 and c.page == 1 for c in chunks))
        self.assertEqual(len(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='code', limit=2)), 2)
        self.assertEqual(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query=''), ())
        self.assertEqual(self.index.search(self.context, source_id='other-source', source_version_id=result.source_version_id, query='code'), ())

    def test_pptx_index_rejects_canonical_extraction_and_page_tampering_before_writes(self):
        from dataclasses import replace
        for scope in ('canonical', 'extraction', 'lineage', 'page', 'canonical-page'):
            with self.subTest(scope=scope):
                self.setUp(); result = self.pptx_fixture()
                if scope == 'canonical': self.cloud.connection.understanding_payload['parser_validation']['slide_evidence'][0]['locator_value'] = '{"block":"p:3","start":6,"end":18}'
                elif scope == 'extraction': self.cloud.connection.extraction_payload['source_version_id'] = 'other-version'
                elif scope == 'lineage': result = replace(result, lineage={**result.lineage, 'source_digest': 'sha256:' + 'a' * 64})
                elif scope == 'page': result = replace(result, parser=replace(result.parser, pages=(1,), page_texts=((1, 'fake page'),)))
                else: self.cloud.connection.understanding_payload['parser_validation']['pages'] = [1]
                with self.assertRaises(DocumentUnderstandingError): self.index.index_result(self.context, result)
                self.assertEqual(self.cloud.connection.inserts, [])
                self.assertEqual(self.cloud.connection.source_state, 'processing')

    def test_pptx_search_tampered_kind_locator_and_source_is_invalid_snapshot(self):
        for field, value in (('evidence_kind', 'parser_page_grounded_fallback'), ('locator_value', '{"block":"p:2","start":true,"end":18}'), ('source_id', 'other-source'), ('source_version_id', 'other-version')):
            with self.subTest(field=field):
                self.setUp(); result = self.pptx_fixture(); self.index.index_result(self.context, result)
                self.cloud.connection.index_payload['chunks'][0][field] = value
                with self.assertRaises(DocumentUnderstandingError):
                    self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='REF')

    def test_pptx_index_latest_source_select_runs_real_sql_and_rejects_old_version(self):
        import sqlite3
        result = self.pptx_fixture()
        connection = self.cloud.connection
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close)
        db.executescript('CREATE TABLE sources(tenant_id,workspace_id,record_id,state,version); CREATE TABLE source_versions(tenant_id,workspace_id,source_id,record_id,version);')
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?)', ('tenant-cp3', 'workspace-cp3', 'source-cp3', 'processing', 3))
        db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)', ('tenant-cp3', 'workspace-cp3', 'source-cp3', 'source-version-cp3', 1))
        db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)', ('tenant-cp3', 'workspace-cp3', 'source-cp3', 'new-version', 2))
        execute = connection.execute

        def actual_select(sql, params=()):
            if sql.startswith('SELECT s.state,s.version,sv.source_id'):
                return Cursor(db.execute(sql.replace('%s', '?'), params).fetchone())
            return execute(sql, params)

        connection.execute = actual_select
        with self.assertRaisesRegex(DocumentUnderstandingError, 'SOURCE_NOT_FOUND'):
            self.index.index_result(self.context, result)
        self.assertEqual(connection.inserts, [])
        self.assertEqual(connection.source_state, 'processing')

    def docx_fixture(self):
        from copy import deepcopy
        from test_docx_document_understanding import ready_docx_result
        result = ready_docx_result()
        self.cloud.connection.understanding_payload = deepcopy({
            'source_id': result.source_id, 'source_version_id': result.source_version_id, 'status': 'ready',
            'semantic': {'key_facts': list(result.semantic.key_facts)},
            'parser_validation': {'text': '', 'markdown': '', 'html': '', 'pages': [], 'page_texts': [],
                'regions': [], 'role': 'validation_only', 'block_evidence': list(result.parser.block_evidence)}, 'lineage': dict(result.lineage)})
        self.cloud.connection.extraction_payload = deepcopy({'source_version_id': result.source_version_id,
            'understanding_result_id': 'understanding-cp3', 'parser_role': 'validation_only',
            'block_evidence': list(result.parser.block_evidence)})
        return result

    def test_docx_index_search_preserves_only_original_block_locator_without_page_fallback(self):
        result = self.docx_fixture()
        self.index.index_result(self.context, result)
        payload = self.cloud.connection.index_payload
        self.assertEqual(payload['strategy'], 'native_docx_facts_with_block_validation')
        self.assertTrue(all(c['evidence_kind'] == 'docx_block_fact' for c in payload['chunks']))
        chunks = self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='unrelated weather')
        self.assertEqual({c.text for c in chunks}, {'REF-DELTA-17', '2026-12-08', '57000', 'KRW'})
        self.assertTrue(all(c.locator_kind == 'docx_block' and c.score == 0 and c.page == 1 for c in chunks))
        self.assertEqual(len(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='code', limit=2)), 2)
        self.assertEqual(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query=''), ())
        self.assertEqual(self.index.search(self.context, source_id='other-source', source_version_id=result.source_version_id, query='code'), ())

    def test_docx_index_rejects_canonical_extraction_and_page_tampering_before_writes(self):
        from dataclasses import replace
        for scope in ('canonical', 'extraction', 'lineage', 'page', 'canonical-page'):
            with self.subTest(scope=scope):
                self.setUp(); result = self.docx_fixture()
                if scope == 'canonical': self.cloud.connection.understanding_payload['parser_validation']['block_evidence'][0]['locator_value'] = '{"block":"p:3","start":6,"end":18}'
                elif scope == 'extraction': self.cloud.connection.extraction_payload['source_version_id'] = 'other-version'
                elif scope == 'lineage': result = replace(result, lineage={**result.lineage, 'source_digest': 'sha256:' + 'a' * 64})
                elif scope == 'page': result = replace(result, parser=replace(result.parser, pages=(1,), page_texts=((1, 'fake page'),)))
                else: self.cloud.connection.understanding_payload['parser_validation']['pages'] = [1]
                with self.assertRaises(DocumentUnderstandingError): self.index.index_result(self.context, result)
                self.assertEqual(self.cloud.connection.inserts, [])
                self.assertEqual(self.cloud.connection.source_state, 'processing')

    def test_docx_search_tampered_kind_locator_and_source_is_invalid_snapshot(self):
        for field, value in (('evidence_kind', 'parser_page_grounded_fallback'), ('locator_value', '{"block":"p:2","start":true,"end":18}'), ('source_id', 'other-source'), ('source_version_id', 'other-version')):
            with self.subTest(field=field):
                self.setUp(); result = self.docx_fixture(); self.index.index_result(self.context, result)
                self.cloud.connection.index_payload['chunks'][0][field] = value
                with self.assertRaises(DocumentUnderstandingError):
                    self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='REF')

    def test_docx_index_latest_source_select_runs_real_sql_and_rejects_old_version(self):
        import sqlite3
        result = self.docx_fixture()
        connection = self.cloud.connection
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close)
        db.executescript('CREATE TABLE sources(tenant_id,workspace_id,record_id,state,version); CREATE TABLE source_versions(tenant_id,workspace_id,source_id,record_id,version);')
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?)', ('tenant-cp3', 'workspace-cp3', 'source-cp3', 'processing', 3))
        db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)', ('tenant-cp3', 'workspace-cp3', 'source-cp3', 'source-version-cp3', 1))
        db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)', ('tenant-cp3', 'workspace-cp3', 'source-cp3', 'new-version', 2))
        execute = connection.execute

        def actual_select(sql, params=()):
            if sql.startswith('SELECT s.state,s.version,sv.source_id'):
                return Cursor(db.execute(sql.replace('%s', '?'), params).fetchone())
            return execute(sql, params)

        connection.execute = actual_select
        with self.assertRaisesRegex(DocumentUnderstandingError, 'SOURCE_NOT_FOUND'):
            self.index.index_result(self.context, result)
        self.assertEqual(connection.inserts, [])
        self.assertEqual(connection.source_state, 'processing')

    def single_cell_fixture(self):
        from dataclasses import replace
        from copy import deepcopy
        result = self.xlsx_fixture()
        values = [('REF-ALPHA-17', '계획', 'B2'), ('2026-11-04', '계획', 'B3'), ('57000', '예산', 'B3'), ('KRW', '예산', 'B2'), ('Budget', '예산', 'A3')]
        cells = tuple({'text': text, 'sheet': sheet, 'range': address, 'locator_value': json.dumps({'sheet': sheet, 'range': address}, ensure_ascii=False, separators=(',', ':'))} for text, sheet, address in values)
        result = replace(result, semantic=SemanticUnderstanding('계획', '값', tuple(item[0] for item in values)), parser=replace(result.parser, cell_evidence=cells))
        self.cloud.connection.understanding_payload['semantic']['key_facts'] = list(result.semantic.key_facts)
        self.cloud.connection.understanding_payload['parser_validation']['cell_evidence'] = deepcopy(list(cells))
        self.cloud.connection.extraction_payload['cell_evidence'] = deepcopy(list(cells))
        self.index.index_result(self.context, result)
        return result

    def test_xlsx_no_overlap_query_returns_only_bound_original_five_cell_facts(self):
        result = self.single_cell_fixture()
        for query in ('project code delivery date budget amount currency', 'unrelated weather forecast'):
            with self.subTest(query=query):
                chunks = self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query=query)
                self.assertEqual({c.text for c in chunks}, {'REF-ALPHA-17', '2026-11-04', '57000', 'KRW', 'Budget'})
                self.assertEqual(len(chunks), 5)
                self.assertTrue(all(c.locator_kind == 'sheet_cell' and c.source_version_id == result.source_version_id for c in chunks))
                if query.startswith('project'): self.assertEqual(chunks[0].text, 'Budget'); self.assertGreater(chunks[0].score, 0)
                else: self.assertTrue(all(c.score == 0 for c in chunks))
        self.assertEqual(len(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='amount', limit=2)), 2)
        self.assertEqual(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query=''), ())
        self.assertEqual(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='amount', limit=0), ())
        # Even an oversized snapshot cannot expand the XLSX bounded context.
        payload = self.cloud.connection.index_payload
        payload['chunks'].append({**payload['chunks'][0], 'chunk_id': 'chunk-extra'})
        self.assertEqual(len(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='amount', limit=99)), 5)

    def test_xlsx_no_overlap_still_validates_locator_and_source_binding(self):
        from copy import deepcopy
        result = self.single_cell_fixture()
        original = deepcopy(self.cloud.connection.index_payload)
        for field, wrong in [('locator_value', '{"sheet":"계획","range":"B2:A1"}'), ('source_id', 'source-other')]:
            with self.subTest(field=field):
                self.cloud.connection.index_payload = deepcopy(original)
                self.cloud.connection.index_payload['chunks'][0][field] = wrong
                with self.assertRaises(DocumentUnderstandingError):
                    self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='unrelated')
        self.cloud.connection.index_payload = original
        self.assertEqual(self.index.search(self.context, source_id='source-other', source_version_id=result.source_version_id, query='amount'), ())
        for chunk in original['chunks']: chunk['source_version_id'] = 'version-other'
        self.assertEqual(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='amount'), ())

    def test_xlsx_strategy_rejects_noncell_snapshot_even_with_query_overlap(self):
        from copy import deepcopy
        result = self.single_cell_fixture()
        original = deepcopy(self.cloud.connection.index_payload)
        for kind in ('parser_page_validated_semantic_fact', 'text_source_verbatim_fact', 'image_region_fact'):
            with self.subTest(kind=kind):
                self.cloud.connection.index_payload = deepcopy(original)
                self.cloud.connection.index_payload['chunks'][0]['evidence_kind'] = kind
                with self.assertRaisesRegex(DocumentUnderstandingError, '^DOCUMENT_INDEX_SNAPSHOT_INVALID$'):
                    self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='REF ALPHA')

    def xlsx_fixture(self):
        from dataclasses import replace
        from copy import deepcopy
        result = ready_result()
        cells = ({"text": "계약 기간은 12개월", "sheet": "계약", "range": "A1:B1", "locator_value": '{"sheet":"계약","range":"A1:B1"}'},
                 {"text": "해지 통보는 30일 전", "sheet": "계약", "range": "A2:B2", "locator_value": '{"sheet":"계약","range":"A2:B2"}'})
        result = replace(result, parser=ParserValidation("", "", "", (), cell_evidence=cells), lineage={"source_format": "xlsx", "source_digest": "a" * 64, "native_input_digest": "b" * 64, "serialization_version": "xlsx-structure-v1", "reconciliation": "matched"})
        self.cloud.connection.understanding_payload = deepcopy({"source_id": result.source_id, "source_version_id": result.source_version_id, "status": "ready", "semantic": {"key_facts": list(result.semantic.key_facts)}, "parser_validation": {"cell_evidence": list(cells)}, "lineage": dict(result.lineage)})
        self.cloud.connection.extraction_payload = deepcopy({"source_version_id": result.source_version_id, "understanding_result_id": "understanding-cp3", "parser_role": "validation_only", "cell_evidence": list(cells)})
        return result

    def test_xlsx_cells_index_without_pages_and_search_exact_locator(self):
        result = self.xlsx_fixture()
        self.index.index_result(self.context, result)
        payload = self.cloud.connection.index_payload
        self.assertEqual(payload['strategy'], 'native_xlsx_facts_with_cell_validation')
        self.assertTrue(all(c['evidence_kind'] == 'xlsx_cell_fact' for c in payload['chunks']))
        chunks = self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query='계약 기간')
        self.assertEqual((chunks[0].locator_kind, chunks[0].locator_value), ('sheet_cell', '{"sheet":"계약","range":"A1:B1"}'))

    def test_xlsx_canonical_or_extraction_tampering_has_zero_writes(self):
        from copy import deepcopy
        for scope, field in [('understanding', 'source_version_id'), ('understanding', 'cell_evidence'), ('understanding', 'lineage'), ('extraction', 'cell_evidence'), ('extraction', 'source_version_id')]:
            with self.subTest(scope=scope, field=field):
                self.setUp(); result = self.xlsx_fixture()
                payload = self.cloud.connection.understanding_payload if scope == 'understanding' else self.cloud.connection.extraction_payload
                if field == 'cell_evidence' and scope == 'understanding': payload = payload['parser_validation']
                payload[field] = [] if field == 'cell_evidence' else 'tampered'
                with self.assertRaises(DocumentUnderstandingError): self.index.index_result(self.context, result)
                self.assertEqual(self.cloud.connection.inserts, []); self.assertEqual(self.cloud.connection.source_state, 'processing')

    def test_xlsx_page_fallback_and_invalid_cell_locator_never_index(self):
        from dataclasses import replace
        for malformed in ('pages', 'locator'):
            with self.subTest(malformed=malformed):
                self.setUp(); result = self.xlsx_fixture()
                if malformed == 'pages': result = replace(result, parser=replace(result.parser, pages=(1,), page_texts=((1, 'whole workbook fallback'),)))
                else: result.parser.cell_evidence[0]['locator_value'] = '{"sheet":"契約","range":"A1:A257"}'
                with self.assertRaises(DocumentUnderstandingError): self.index.index_result(self.context, result)
                self.assertEqual(self.cloud.connection.inserts, []); self.assertEqual(self.cloud.connection.source_state, 'processing')

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
        self.assertEqual(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query="unrelated weather"), ())

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
        self.assertEqual(self.index.search(self.context, source_id=result.source_id, source_version_id=result.source_version_id, query="unrelated weather"), ())
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
        self.assertEqual(self.index.search(self.context, source_id="source-cp3", source_version_id="source-version-cp3", query="unrelated weather"), ())

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
