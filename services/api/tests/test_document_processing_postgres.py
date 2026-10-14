from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from contextlib import contextmanager

from daon_user_api.document_processing import DocumentProcessingContext, DocumentProcessingService
from daon_user_api.cloud_storage import CloudDatabaseError
from daon_user_api.object_queue import ObjectStorageError
from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingResult,
    DocumentUnderstandingError,
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
        self.filename = "contract.pdf"
        self.content_type = "application/pdf"
        self.content = PDF
        self.states = {"ProcessingRun": "accepted", "Source": "registered"}
        self.versions = {"ProcessingRun": 1, "Source": 1}
        self.source_version_id = "source-version-cp3"
        self.inserts: list[str] = []
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        if sql.startswith("SELECT pr.state,pr.version,pr.source_version_id,sv.source_id"):
            return Cursor((self.states["ProcessingRun"], self.versions["ProcessingRun"], self.source_version_id, "source-cp3"))
        self.statements.append((sql, tuple(params)))
        if sql.startswith("SELECT sv.record_id"):
            return Cursor((
                self.source_version_id, OBJECT_ID, {"filename": self.filename},
                OBJECT_KEY, hashlib.sha256(self.content).hexdigest(), len(self.content),
                self.content_type, "completed",
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


class AudioProcessingStorageTests(unittest.TestCase):
    def test_audio_transmission_revalidates_current_access_binding_and_version_with_real_sql(self):
        from test_audio_source import wav_bytes
        from daon_user_api.document_processing import StoredSourceDocument
        raw = wav_bytes();digest = hashlib.sha256(raw).hexdigest()
        context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
        document = StoredSourceDocument('source-cp3', 'source-version-cp3', 'qa.wav', raw, 'audio/wav')
        class Store:
            def __init__(self):
                self.db = sqlite3.connect(':memory:')
                self.db.executescript('''
                    CREATE TABLE processing_runs(tenant_id,workspace_id,record_id,source_version_id,state,created_by,canonical_json);
                    CREATE TABLE sources(tenant_id,workspace_id,record_id,state,created_by);
                    CREATE TABLE source_versions(tenant_id,workspace_id,record_id,source_id,version,object_id,canonical_json);
                    CREATE TABLE object_records(tenant_id,workspace_id,object_id,digest_sha256,byte_size,content_type,status);
                    CREATE TABLE notebooks(tenant_id,workspace_id,notebook_id);
                    CREATE TABLE notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id);
                    CREATE TABLE notebook_source_unbindings(tenant_id,workspace_id,notebook_id,source_id,source_version_id);
                    CREATE TABLE notebook_deletion_requests(tenant_id,workspace_id,notebook_id);
                    CREATE TABLE deletion_requests(tenant_id,workspace_id,source_id,source_active);
                    CREATE TABLE identity_auth_workspaces(tenant_id,workspace_id,workspace_kind);
                    CREATE TABLE identity_auth_memberships(tenant_id,workspace_id,user_id,role,state);
                    CREATE TABLE identity_auth_tenant_roles(tenant_id,user_id,role,state);
                    CREATE TABLE identity_auth_tenant_permission_rules(tenant_id,permission,effect,locked);
                    CREATE TABLE identity_auth_workspace_permission_rules(tenant_id,workspace_id,permission,effect,locked);
                    CREATE TABLE identity_auth_source_access(tenant_id,workspace_id,user_id,source_version_id,allowed);
                ''')
                scope = ('tenant-cp3', 'workspace-cp3')
                self.db.execute('INSERT INTO processing_runs VALUES(?,?,?,?,?,?,?)', (*scope, 'pr-a', 'source-version-cp3', 'speech_to_text', 'actor-cp3', '{"notebook_id":"nb-a"}'))
                self.db.execute('INSERT INTO sources VALUES(?,?,?,?,?)', (*scope, 'source-cp3', 'processing', 'actor-cp3'))
                self.db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?,?,?)', (*scope, 'source-version-cp3', 'source-cp3', 1, 'object-a', '{"filename":"qa.wav"}'))
                self.db.execute('INSERT INTO object_records VALUES(?,?,?,?,?,?,?)', (*scope, 'object-a', digest, len(raw), 'audio/wav', 'completed'))
                self.db.execute('INSERT INTO notebooks VALUES(?,?,?)', (*scope, 'nb-a'))
                self.db.execute('INSERT INTO notebook_bindings VALUES(?,?,?,?,?,?)', (*scope, 'nb-a', 'source', 'source-cp3', 'source-version-cp3'))
                self.db.execute('INSERT INTO identity_auth_workspaces VALUES(?,?,?)', (*scope, 'organization'))
                self.db.execute('INSERT INTO identity_auth_memberships VALUES(?,?,?,?,?)', (*scope, 'actor-cp3', 'editor', 'active'))
            @contextmanager
            def _transaction(self, context): yield self
            def execute(self, sql, params=()): return self.db.execute(sql.replace('%s', '?'), params)
        mutations = ["UPDATE identity_auth_memberships SET state='inactive'", "UPDATE identity_auth_memberships SET role='viewer'",
            "DELETE FROM notebook_bindings", "INSERT INTO notebook_source_unbindings VALUES('tenant-cp3','workspace-cp3','nb-a','source-cp3','source-version-cp3')",
            "INSERT INTO identity_auth_workspace_permission_rules VALUES('tenant-cp3','workspace-cp3','local_internal_llm','deny',0)",
            "INSERT INTO identity_auth_source_access VALUES('tenant-cp3','workspace-cp3','actor-cp3','source-version-cp3',0)",
            "INSERT INTO source_versions VALUES('tenant-cp3','workspace-cp3','new-version','source-cp3',2,'object-a','{}')"]
        for mutation in mutations:
            store = Store()
            try:
                repository = PostgresDocumentProcessingRepository(store, object())
                repository.authorize_audio_source(context, document, 'pr-a')
                store.db.execute(mutation)
                with self.subTest(mutation=mutation), self.assertRaises(DocumentUnderstandingError):
                    repository.authorize_audio_source(context, document, 'pr-a')
            finally: store.db.close()

    def test_audio_completion_rejects_tampered_snapshot_with_zero_result_writes(self):
        from dataclasses import replace
        from test_audio_document_understanding import ready_audio_result
        from test_audio_source import wav_bytes
        for field, value in (('transcript_sha256', 'c' * 64), ('source_digest', 'c' * 64),
                             ('duration_ms', '2000'), ('asr_model_digest', 'c' * 64), ('policy_version', 'other')):
            cloud = FakeCloudStore();storage = FakeObjectStorage()
            cloud.connection.filename = 'qa.wav';cloud.connection.content_type = 'audio/wav';cloud.connection.content = wav_bytes()
            storage.content = cloud.connection.content
            cloud.connection.states = {'ProcessingRun': 'speech_to_text', 'Source': 'processing'}
            cloud.connection.versions = {'ProcessingRun': 2, 'Source': 3}
            result = ready_audio_result();result = replace(result, lineage={**result.lineage, field: value})
            context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
            with self.subTest(field=field), self.assertRaises(DocumentUnderstandingError):
                PostgresDocumentProcessingRepository(cloud, storage).complete(context, 'run-cp3', result)
            self.assertEqual(cloud.connection.inserts, [])
            self.assertEqual(cloud.connection.states['ProcessingRun'], 'speech_to_text')

    def test_audio_completion_persists_bound_transcript_and_time_evidence(self):
        from test_audio_document_understanding import ready_audio_result
        from test_audio_source import wav_bytes
        cloud = FakeCloudStore()
        cloud.connection.filename = 'qa.wav';cloud.connection.content_type = 'audio/wav';cloud.connection.content = wav_bytes()
        cloud.connection.states = {'ProcessingRun': 'speech_to_text', 'Source': 'processing'}
        cloud.connection.versions = {'ProcessingRun': 2, 'Source': 3}
        storage = FakeObjectStorage();storage.content = cloud.connection.content
        repository = PostgresDocumentProcessingRepository(cloud, storage)
        context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
        result = ready_audio_result()
        repository.complete(context, 'run-cp3', result)
        payload = next(params[4].obj for sql, params in cloud.connection.statements if sql.startswith('INSERT INTO understanding_results'))
        self.assertEqual(payload['parser_validation']['audio_evidence'], list(result.parser.audio_evidence))
        self.assertEqual(payload['parser_validation']['pages'], [])
        self.assertEqual(payload['lineage']['transcript_sha256'], result.lineage['transcript_sha256'])
        self.assertEqual(cloud.connection.states['ProcessingRun'], 'completed')
        targets = [params[3] for sql, params in cloud.connection.statements if sql.startswith('SELECT state,version,outcome,error_code FROM transition_canon_state')]
        self.assertEqual(targets, ['llm_semantic_understanding', 'transcript_timecode_validation', 'evidence_reconciliation', 'completed'])
        evidence = [params[4].obj for sql, params in cloud.connection.statements if sql.startswith('INSERT INTO extraction_evidence')]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['audio_evidence'], list(result.parser.audio_evidence))
        self.assertNotIn('page', evidence[0])

    def test_audio_start_uses_existing_speech_state_not_vision(self):
        cloud = FakeCloudStore()
        class Connection(FakeConnection):
            def execute(self, sql, params=()):
                if sql.startswith('SELECT sv.source_id,s.state,s.version'):
                    return Cursor(('source-cp3', self.states['Source'], self.versions['Source'], {'filename': 'qa.wav'}, 'audio/wav'))
                return super().execute(sql, params)
        cloud.connection = Connection()
        repository = PostgresDocumentProcessingRepository(cloud, FakeObjectStorage())
        context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
        repository.start(context, 'source-version-cp3', enqueue=True)
        self.assertEqual(cloud.connection.states['ProcessingRun'], 'speech_to_text')
        payload = next(params[4].obj for sql, params in cloud.connection.statements if sql.startswith('INSERT INTO processing_runs'))
        self.assertEqual(payload['modality'], 'audio')
        self.assertNotIn('vision_llm_first', payload)


class FakeObjectStorage:
    content = PDF

    def get(self, key: str) -> bytes:
        if key != OBJECT_KEY:
            raise AssertionError(key)
        return self.content


class BoundConnection(FakeConnection):
    """Execute the production read SQL against two real version rows."""
    def __init__(self):
        super().__init__()
        self.states = {"ProcessingRun": "vision_llm_understanding", "Source": "processing"}
        self.versions = {"ProcessingRun": 2, "Source": 3}
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE sources (tenant_id,workspace_id,record_id);
            CREATE TABLE source_versions (tenant_id,workspace_id,record_id,source_id,object_id,canonical_json,version);
            CREATE TABLE processing_runs (tenant_id,workspace_id,record_id,source_version_id,state);
            CREATE TABLE object_records (tenant_id,workspace_id,object_id,object_key,digest_sha256,byte_size,content_type,status);
        """)
        self.db.execute("INSERT INTO sources VALUES (?,?,?)", ("tenant-cp3", "workspace-cp3", "source-cp3"))
        for version, version_id in ((1, "source-version-cp3"), (2, "source-version-newer")):
            self.db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,?)", (
                "tenant-cp3", "workspace-cp3", version_id, "source-cp3", OBJECT_ID,
                json.dumps({"filename": "contract.pdf"}), version,
            ))
        self.db.execute("INSERT INTO processing_runs VALUES (?,?,?,?,?)", (
            "tenant-cp3", "workspace-cp3", "run-cp3", "source-version-cp3", "vision_llm_understanding",
        ))
        self.db.execute("INSERT INTO object_records VALUES (?,?,?,?,?,?,?,?)", (
            "tenant-cp3", "workspace-cp3", OBJECT_ID, OBJECT_KEY,
            hashlib.sha256(PDF).hexdigest(), len(PDF), "application/pdf", "completed",
        ))

    def execute(self, sql, params=()):
        if sql.startswith("SELECT sv.record_id"):
            self.statements.append((sql, tuple(params)))
            row = self.db.execute(sql.replace("%s", "?"), params).fetchone()
            if row is not None:
                row = (*row[:2], json.loads(row[2]), *row[3:])
            return Cursor(row)
        return super().execute(sql, params)


class PostgresDocumentProcessingRepositoryTests(unittest.TestCase):
    def test_pptx_bound_original_accepts_exact_mime_and_preserves_hash_checks(self):
        from test_pptx_document_understanding import original
        raw = original()
        connection = self._bound_fixture()
        connection.db.execute('UPDATE source_versions SET canonical_json=?', (json.dumps({'filename': 'qa.pptx'}),))
        connection.db.execute('UPDATE object_records SET digest_sha256=?,byte_size=?,content_type=?',
            (hashlib.sha256(raw).hexdigest(), len(raw), 'application/vnd.openxmlformats-officedocument.presentationml.presentation'))
        self.repository._object_storage.content = raw
        document = self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3')
        self.assertEqual(document.content, raw)
        self.assertEqual(document.source_version_id, 'source-version-cp3')
        connection.db.execute('UPDATE object_records SET digest_sha256=?', ('sha256:' + hashlib.sha256(raw).hexdigest(),))
        self.assertEqual(self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3').content, raw)
        for source, run in (('other-source', 'run-cp3'), ('source-cp3', 'other-run')):
            with self.subTest(source=source, run=run), self.assertRaisesRegex(DocumentUnderstandingError, 'SOURCE_NOT_FOUND'):
                self.repository.load_source_document(self.context, source, processing_run_id=run)
        self.assertFalse(any('transition_canon_state' in sql for sql, _ in connection.statements))

    def test_pptx_completion_binds_canonical_extraction_without_fake_pages(self):
        from test_pptx_document_understanding import ready_pptx_result
        result = ready_pptx_result()
        self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'
        self.cloud.connection.versions['ProcessingRun'] = 2
        self.cloud.connection.states['Source'] = 'processing'
        self.repository.complete(self.context, 'run-cp3', result)
        statements = self.cloud.connection.statements
        payload = next(params[4].obj for sql, params in statements if sql.startswith('INSERT INTO understanding_results'))
        extraction = [params[4].obj for sql, params in statements if sql.startswith('INSERT INTO extraction_evidence')]
        self.assertEqual(payload['parser_validation']['slide_evidence'], list(result.parser.slide_evidence))
        self.assertEqual(payload['parser_validation']['pages'], [])
        self.assertEqual(payload['parser_validation']['page_texts'], [])
        self.assertEqual(extraction, [{'source_version_id': result.source_version_id,
            'understanding_result_id': payload.get('understanding_result_id', self.repository._opaque_id('ur', 'run-cp3', result.source_version_id)),
            'parser_role': 'validation_only', 'slide_evidence': list(result.parser.slide_evidence)}])
        self.assertEqual(self.cloud.connection.states['Source'], 'processing')

    def test_pptx_completion_wrong_bound_version_and_invalid_evidence_have_zero_writes(self):
        from dataclasses import replace
        from test_pptx_document_understanding import ready_pptx_result
        for invalid in ('version', 'locator', 'page'):
            with self.subTest(invalid=invalid):
                self.setUp(); result = ready_pptx_result()
                self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'
                self.cloud.connection.versions['ProcessingRun'] = 2
                if invalid == 'version': result = replace(result, source_version_id='other-version')
                elif invalid == 'locator': result.parser.slide_evidence[0]['locator_value'] = '{"block":"p:1","start":6,"end":18}'
                else: result = replace(result, parser=replace(result.parser, pages=(1,)))
                with self.assertRaises(DocumentUnderstandingError): self.repository.complete(self.context, 'run-cp3', result)
                self.assertEqual(self.cloud.connection.inserts, [])
                self.assertEqual(self.cloud.connection.states['ProcessingRun'], 'vision_llm_understanding')

    def test_docx_completion_binds_canonical_extraction_without_fake_pages(self):
        from test_docx_document_understanding import ready_docx_result
        result = ready_docx_result()
        self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'
        self.cloud.connection.versions['ProcessingRun'] = 2
        self.cloud.connection.states['Source'] = 'processing'
        self.repository.complete(self.context, 'run-cp3', result)
        statements = self.cloud.connection.statements
        payload = next(params[4].obj for sql, params in statements if sql.startswith('INSERT INTO understanding_results'))
        extraction = [params[4].obj for sql, params in statements if sql.startswith('INSERT INTO extraction_evidence')]
        self.assertEqual(payload['parser_validation']['block_evidence'], list(result.parser.block_evidence))
        self.assertEqual(payload['parser_validation']['pages'], [])
        self.assertEqual(payload['parser_validation']['page_texts'], [])
        self.assertEqual(extraction, [{'source_version_id': result.source_version_id,
            'understanding_result_id': payload.get('understanding_result_id', self.repository._opaque_id('ur', 'run-cp3', result.source_version_id)),
            'parser_role': 'validation_only', 'block_evidence': list(result.parser.block_evidence)}])
        self.assertEqual(self.cloud.connection.states['Source'], 'processing')

    def test_docx_completion_wrong_bound_version_and_invalid_evidence_have_zero_writes(self):
        from dataclasses import replace
        from test_docx_document_understanding import ready_docx_result
        for invalid in ('version', 'locator', 'page'):
            with self.subTest(invalid=invalid):
                self.setUp(); result = ready_docx_result()
                self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'
                self.cloud.connection.versions['ProcessingRun'] = 2
                if invalid == 'version': result = replace(result, source_version_id='other-version')
                elif invalid == 'locator': result.parser.block_evidence[0]['locator_value'] = '{"block":"p:1","start":6,"end":18}'
                else: result = replace(result, parser=replace(result.parser, pages=(1,)))
                with self.assertRaises(DocumentUnderstandingError): self.repository.complete(self.context, 'run-cp3', result)
                self.assertEqual(self.cloud.connection.inserts, [])
                self.assertEqual(self.cloud.connection.states['ProcessingRun'], 'vision_llm_understanding')

    def test_docx_bound_original_accepts_exact_mime_and_preserves_hash_checks(self):
        from test_docx_source import docx_bytes, paragraph
        raw = docx_bytes(paragraph('REF-DELTA-17'))
        connection = self._bound_fixture()
        connection.db.execute('UPDATE source_versions SET canonical_json=?', (json.dumps({'filename': 'qa.docx'}),))
        connection.db.execute('UPDATE object_records SET digest_sha256=?,byte_size=?,content_type=?',
            (hashlib.sha256(raw).hexdigest(), len(raw), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
        self.repository._object_storage.content = raw
        document = self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3')
        self.assertEqual(document.content, raw)
        self.assertEqual(document.source_version_id, 'source-version-cp3')
        connection.db.execute('UPDATE object_records SET digest_sha256=?', ('sha256:' + hashlib.sha256(raw).hexdigest(),))
        self.assertEqual(self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3').content, raw)
        for source, run in (('other-source', 'run-cp3'), ('source-cp3', 'other-run')):
            with self.subTest(source=source, run=run), self.assertRaisesRegex(DocumentUnderstandingError, 'SOURCE_NOT_FOUND'):
                self.repository.load_source_document(self.context, source, processing_run_id=run)
        self.assertFalse(any('transition_canon_state' in sql for sql, _ in connection.statements))

    def test_xlsx_original_and_completion_preserve_exact_cells_without_fake_pages(self):
        from test_xlsx_source import parts, zipped
        connection = self.cloud.connection
        connection.filename, connection.content_type, connection.content = 'qa.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', zipped(parts())
        self.repository._object_storage.content = connection.content
        document = self.repository.load_source_document(self.context, 'source-cp3')
        self.assertEqual(document.content, connection.content)
        run_id = self.repository.start(self.context, document.source_version_id)
        cells = ({'text': 'ORANGE-42', 'sheet': '납품 "계획"', 'range': 'B2', 'locator_value': '{"sheet":"납품 \\"계획\\"","range":"B2"}'.replace('\\\\', '\\')},)
        result = DocumentUnderstandingResult(document.source_id, document.source_version_id, 'ready', ('vision_llm_understanding', 'parser_ocr_validation', 'evidence_reconciliation'), SemanticUnderstanding('QA', 'ORANGE-42', ('ORANGE-42',)), ParserValidation('', '', '', (), cell_evidence=cells), {'source_format': 'xlsx', 'source_digest': hashlib.sha256(document.content).hexdigest()})
        self.repository.complete(self.context, run_id, result)
        payload = next(params[4].obj for sql, params in connection.statements if sql.startswith('INSERT INTO understanding_results'))
        extraction = [params[4].obj for sql, params in connection.statements if sql.startswith('INSERT INTO extraction_evidence')]
        self.assertEqual(payload['parser_validation']['cell_evidence'], list(cells))
        self.assertEqual(len(extraction), 1); self.assertEqual(extraction[0]['cell_evidence'], list(cells))
        self.assertNotIn('page', extraction[0]); self.assertEqual(connection.states['Source'], 'processing')

    def test_xlsx_completion_bound_version_mismatch_has_no_state_write(self):
        result = DocumentUnderstandingResult('source-cp3', 'wrong-version', 'ready', (), SemanticUnderstanding('QA', 'ORANGE-42', ('ORANGE-42',)), ParserValidation('', '', '', (), cell_evidence=({'text': 'ORANGE-42', 'sheet': 's', 'range': 'A1', 'locator_value': '{"sheet":"s","range":"A1"}'},)), {'source_format': 'xlsx'})
        self.cloud.connection.states['ProcessingRun'] = 'vision_llm_understanding'; self.cloud.connection.versions['ProcessingRun'] = 2
        with self.assertRaises(DocumentUnderstandingError): self.repository.complete(self.context, 'run-cp3', result)
        self.assertEqual(self.cloud.connection.inserts, []); self.assertEqual(self.cloud.connection.states['ProcessingRun'], 'vision_llm_understanding')

    def test_bound_xlsx_read_keeps_claimed_version_and_scope_failure_writes_zero(self):
        from test_xlsx_source import parts, zipped
        raw = zipped(parts()); connection = self._bound_fixture()
        connection.db.execute('UPDATE source_versions SET canonical_json=?', (json.dumps({'filename': 'qa.xlsx'}),))
        connection.db.execute('UPDATE object_records SET digest_sha256=?,byte_size=?,content_type=?', (hashlib.sha256(raw).hexdigest(), len(raw), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
        self.repository._object_storage.content = raw
        document = self.repository.load_source_document(self.context, 'source-cp3', processing_run_id='run-cp3')
        self.assertEqual(document.source_version_id, 'source-version-cp3')
        self.assertEqual(document.content, raw)
        for source, run in [('source-other', 'run-cp3'), ('source-cp3', 'run-other')]:
            with self.assertRaisesRegex(DocumentUnderstandingError, 'SOURCE_NOT_FOUND'): self.repository.load_source_document(self.context, source, processing_run_id=run)
        self.assertFalse(any('transition_canon_state' in sql for sql, _ in connection.statements))


    def setUp(self) -> None:
        self.context = DocumentProcessingContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
        )
        self.cloud = FakeCloudStore()
        self.repository = PostgresDocumentProcessingRepository(self.cloud, FakeObjectStorage())  # type: ignore[arg-type]

    def _bound_fixture(self):
        self.cloud.connection = BoundConnection()
        self.addCleanup(self.cloud.connection.db.close)
        return self.cloud.connection

    def test_bound_read_keeps_queued_version_while_initial_read_uses_latest(self) -> None:
        self._bound_fixture()
        bound = self.repository.load_source_document(self.context, "source-cp3", processing_run_id="run-cp3")
        latest = self.repository.load_source_document(self.context, "source-cp3")
        self.assertEqual(bound.source_version_id, "source-version-cp3")
        self.assertEqual(latest.source_version_id, "source-version-newer")

    def test_missing_or_wrong_bound_scope_never_transitions_run_or_reads_object(self) -> None:
        connection = self._bound_fixture()
        class UnreadableStorage:
            def get(self, key):
                raise AssertionError("unbound request must not read objects")
        self.repository._object_storage = UnreadableStorage()
        for source_id, run_id, tenant, workspace in (
            ("source-other", "run-cp3", "tenant-cp3", "workspace-cp3"),
            ("source-cp3", "run-other", "tenant-cp3", "workspace-cp3"),
            ("source-cp3", "run-cp3", "tenant-other", "workspace-cp3"),
            ("source-cp3", "run-cp3", "tenant-cp3", "workspace-other"),
        ):
            with self.subTest(source=source_id, run=run_id, tenant=tenant, workspace=workspace):
                context = DocumentProcessingContext(tenant, workspace, "actor-cp3", "trace-cp3", "policy-v1")
                with self.assertRaisesRegex(DocumentUnderstandingError, "SOURCE_NOT_FOUND"):
                    self.repository.load_source_document(context, source_id, processing_run_id=run_id)
                self.assertEqual(connection.states, {"ProcessingRun": "vision_llm_understanding", "Source": "processing"})
                self.assertFalse(any("transition_canon_state" in sql for sql, _ in connection.statements))

    def test_bound_query_database_failure_never_transitions_run(self) -> None:
        connection = self._bound_fixture()
        original = connection.execute
        def unavailable(sql, params=()):
            if sql.startswith("SELECT sv.record_id"):
                raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True)
            return original(sql, params)
        connection.execute = unavailable
        with self.assertRaisesRegex(DocumentUnderstandingError, "DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE"):
            self.repository.load_source_document(self.context, "source-cp3", processing_run_id="run-cp3")
        self.assertEqual(self.cloud.capabilities, ["source.read"])

    def test_completed_or_failed_bound_run_checksum_error_never_writes_state(self) -> None:
        for state, source_state in (("completed", "ready"), ("failed", "failed")):
            with self.subTest(state=state):
                connection = self._bound_fixture()
                connection.db.execute("UPDATE processing_runs SET state=?", (state,))
                connection.states = {"ProcessingRun": state, "Source": source_state}
                self.repository._object_storage.content = b"corrupt"
                with self.assertRaisesRegex(DocumentUnderstandingError, "SOURCE_OBJECT_CHECKSUM_MISMATCH"):
                    self.repository.load_source_document(self.context, "source-cp3", processing_run_id="run-cp3")
                self.assertEqual(connection.states, {"ProcessingRun": state, "Source": source_state})
                self.assertFalse(any("transition_canon_state" in sql for sql, _ in connection.statements))

    def test_late_failure_of_terminal_run_never_changes_processing_source(self) -> None:
        for state in ("completed", "failed", "policy_blocked"):
            with self.subTest(state=state):
                connection = self._bound_fixture()
                connection.states = {"ProcessingRun": state, "Source": "processing"}
                self.repository.fail(self.context, "run-cp3", "SOURCE_OBJECT_CHECKSUM_MISMATCH", retryable=False)
                self.assertEqual(connection.states, {"ProcessingRun": state, "Source": "processing"})
                self.assertFalse(any("transition_canon_state" in sql for sql, _ in connection.statements))

    def test_existing_document_active_states_can_still_fail(self) -> None:
        for state in ("accepted", "vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"):
            with self.subTest(state=state):
                connection = self._bound_fixture()
                connection.states = {"ProcessingRun": state, "Source": "processing"}
                self.repository.fail(self.context, "run-cp3", "SOURCE_OBJECT_CHECKSUM_MISMATCH", retryable=False)
                self.assertEqual(connection.states, {"ProcessingRun": "failed", "Source": "failed"})

    def test_bound_original_failures_terminate_run_before_any_model_or_result(self) -> None:
        for kind, expected_code, source_state in (
            ("format", "SOURCE_OBJECT_NOT_READY", "failed"),
            ("digest", "SOURCE_OBJECT_CHECKSUM_MISMATCH", "failed"),
            ("missing", "OBJECT_NOT_FOUND", "failed"),
            ("transient", "OBJECT_STORAGE_UNAVAILABLE", "waiting_model"),
        ):
            with self.subTest(kind=kind):
                connection = self._bound_fixture()
                self.repository._object_storage = FakeObjectStorage()
                if kind == "format":
                    connection.db.execute("UPDATE source_versions SET canonical_json=?", (json.dumps({"filename": "notes.png"}),))
                    connection.db.execute("UPDATE object_records SET content_type='image/png'")
                elif kind == "digest":
                    self.repository._object_storage.content = b"corrupt"
                else:
                    class FailingStorage:
                        def get(self, key):
                            raise ObjectStorageError(expected_code, retryable=kind == "transient")
                    self.repository._object_storage = FailingStorage()
                class NoModel:
                    def resolve(self, *args):
                        raise AssertionError("original failure must precede model resolution")
                service = DocumentProcessingService(self.repository, NoModel(), None)
                with self.assertRaisesRegex(DocumentUnderstandingError, expected_code):
                    service.process_existing(self.context, source_id="source-cp3", processing_run_id="run-cp3")
                self.assertEqual(connection.states, {"ProcessingRun": "failed", "Source": source_state})
                self.assertEqual(connection.inserts, [])

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

    def test_image_completion_preserves_regions_and_display_lineage_in_canonical_records(self) -> None:
        from test_image_document_understanding import image_bytes
        connection = self.cloud.connection
        connection.filename, connection.content_type, connection.content = "qa.png", "image/png", image_bytes()
        self.repository._object_storage.content = connection.content
        document = self.repository.load_source_document(self.context, "source-cp3")
        run_id = self.repository.start(self.context, document.source_version_id)
        regions = ({"text": "ORANGE-42", "value": "5,7,80,20,120,60"},)
        lineage = {"source_format": "image", "source_digest": hashlib.sha256(document.content).hexdigest(), "image_width": "120", "image_height": "60", "exif_orientation": "1"}
        result = DocumentUnderstandingResult(document.source_id, document.source_version_id, "ready", ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
            SemanticUnderstanding("QA", "ORANGE-42", ("ORANGE-42",)), ParserValidation("ORANGE-42", "", "", (1,), ((1, "ORANGE-42"),), "validation_only", regions), lineage)
        self.repository.complete(self.context, run_id, result)
        payload = next(params[4].obj for sql, params in connection.statements if sql.startswith("INSERT INTO understanding_results"))
        self.assertEqual(payload["source_version_id"], document.source_version_id)
        self.assertEqual(payload["parser_validation"]["regions"], list(regions))
        self.assertEqual(payload["lineage"], lineage)
        self.assertEqual(connection.states["ProcessingRun"], "completed")
        self.assertEqual(connection.states["Source"], "processing", "ready requires actual indexing, not completion alone")

    def test_loads_exact_txt_object_and_rejects_mime_or_digest_mismatch(self) -> None:
        content = b"Actual TXT fact\n"
        self.cloud.connection.filename = "notes.txt"
        self.cloud.connection.content_type = "text/plain"
        self.cloud.connection.content = content
        self.repository._object_storage.content = content
        document = self.repository.load_source_document(self.context, "source-cp3")
        self.assertEqual((document.content, document.content_type), (content, "text/plain"))
        self.cloud.connection.content_type = "application/pdf"
        with self.assertRaisesRegex(Exception, "SOURCE_OBJECT_NOT_READY"):
            self.repository.load_source_document(self.context, "source-cp3")
        self.cloud.connection.content_type = "text/plain"
        self.repository._object_storage.content = b"tampered"
        with self.assertRaisesRegex(Exception, "SOURCE_OBJECT_CHECKSUM_MISMATCH"):
            self.repository.load_source_document(self.context, "source-cp3")

    def test_loads_registered_markdown_and_csv_but_rejects_cross_mime(self) -> None:
        for filename, content_type in (("notes.md", "text/markdown"), ("notes.csv", "text/csv")):
            with self.subTest(filename=filename):
                content = b"ORANGE-COMPASS-42,active\n"
                self.cloud.connection.filename = filename
                self.cloud.connection.content_type = content_type
                self.cloud.connection.content = content
                self.repository._object_storage.content = content
                document = self.repository.load_source_document(self.context, "source-cp3")
                self.assertEqual((document.content, document.content_type), (content, content_type))
                self.cloud.connection.content_type = "text/plain"
                with self.assertRaisesRegex(Exception, "SOURCE_OBJECT_NOT_READY"):
                    self.repository.load_source_document(self.context, "source-cp3")

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
