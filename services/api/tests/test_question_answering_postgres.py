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
        self.audio_understanding_row = None
        self.source_access_allowed = True
        self.revision = '0069'
        self.cost_calls = []
        self.cost_error = False

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        if sql.startswith('SELECT canonical_json FROM source_versions'):
            return Cursor(({},))
        if sql.startswith('SELECT NOT EXISTS (SELECT 1 FROM identity_auth_source_access'):
            with sqlite3.connect(':memory:') as connection:
                connection.execute('CREATE TABLE identity_auth_source_access(tenant_id TEXT,workspace_id TEXT,user_id TEXT,source_version_id TEXT,allowed INTEGER)')
                connection.execute('INSERT INTO identity_auth_source_access VALUES(?,?,?,?,?)',
                    (*params, int(self.source_access_allowed)))
                return Cursor(connection.execute(sql.replace('%s','?'),params).fetchone())
        if sql.startswith('SELECT record_id,canonical_json FROM understanding_results'):
            return Cursor(self.audio_understanding_row)
        self.queries.append(sql)
        if sql == 'SELECT version_num FROM public.alembic_version':
            return Cursor(rows=[(self.revision,)])
        if sql.startswith('SELECT public.record_operations_cost_receipt('):
            self.cost_calls.append(params)
            if self.cost_error:
                from psycopg.errors import InsufficientPrivilege
                raise InsufficientPrivilege('PRIVATE_COST_CANARY')
            return Cursor((params[-1],))
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
    def test_cost_failed_run_records_uncertain_without_fabricated_attempt_and_rolls_back_on_error(self):
        from test_recovery_lineage import Ledger
        from daon_user_api.question_answering_service import question_request_fingerprint
        from types import SimpleNamespace
        ledger=Ledger();ctx=QuestionContext(ledger.context.tenant_id,ledger.context.workspace_id,ledger.context.actor_id,
            ledger.context.trace_id,ledger.context.policy_version,'notebook-a')
        selection=TextModelSelection('OLLAMA','https://models.example','profile','deployment','model',1,connection_id='connection-a')
        run='run-failed-cost';payload={'question':'합성 질문','context_mode':'general_ungrounded'}
        fingerprint=question_request_fingerprint(ctx,run_id=run,idempotency_key=run,request_payload=payload)
        frozen={'fingerprint':'sha256:'+'a'*64,'payload_fingerprint':'sha256:'+'b'*64}
        ids=self.repository._opaque_id
        egress=ids('egress',run,frozen['fingerprint'],frozen['payload_fingerprint']);routing=ids('routing',run)
        policy=ids('routing-policy','1','deployment')
        authorization=dict(provider_owner=True,policy_fingerprint=frozen['fingerprint'],frozen_routing_context=frozen,
            egress_decision_id=egress,routing_decision_id=routing,routing_policy_version_id=policy)
        expected=self.repository._run_canonical_payload(question=payload['question'],source_id=None,source_version_id=None,
            context_mode='general_ungrounded',request_fingerprint=fingerprint,selection=selection,frozen_scope=frozen)
        pending={};receipts=[];events=[];fail=[False];queries=[]
        class Connection:
            info=ledger.info
            def execute(inner,sql,params=()):
                queries.append(sql)
                if sql.startswith('SELECT pg_advisory_xact_lock'):return Cursor()
                if sql.startswith('SELECT state,version,created_by'):return Cursor(('generating',4,ctx.actor_id,ctx.trace_id,expected,ids('conversation',run)))
                if sql.startswith('SELECT e.canonical_json'):return Cursor((dict(allowed=True,run_id=run,frozen_routing_context=frozen),
                    dict(run_id=run,routing_status='selected',selected_deployment_id='deployment'),policy,ctx.actor_id,'succeeded',ctx.trace_id,ctx.policy_version))
                if sql.startswith(('SELECT 1 FROM identity_memberships','SELECT 1 FROM notebooks n')):return Cursor((1,))
                if sql.startswith('INSERT INTO '):
                    table=sql.split()[2];pending[table]=(params[6].obj,params[9],params[10],*params[11:]);return Cursor()
                if sql.startswith('SELECT canonical_json,created_by,trace_id'):
                    table=sql.split(' FROM ',1)[1].split()[0];return Cursor(pending[table])
                if sql.startswith('WITH inserted AS (INSERT INTO notebook_bindings'):return Cursor()
                if sql.startswith('SELECT created_by FROM notebook_bindings'):return Cursor((ctx.actor_id,))
                if sql.startswith('SELECT state,version,outcome,error_code FROM transition_canon_state'):return Cursor(('failed',5,'succeeded',None))
                if sql=='SELECT version_num FROM public.alembic_version':return Cursor(rows=[('0071',)])
                if sql.startswith('SELECT public.record_operations_cost_receipt('):
                    if fail[0]:
                        from psycopg.errors import CheckViolation
                        raise CheckViolation('PRIVATE_COST_CANARY')
                    receipts.append(params);return Cursor((params[-1],))
                return ledger.execute(sql,params)
        @contextmanager
        def transaction(context):
            old=list(receipts)
            try:yield Connection()
            except BaseException:
                receipts[:]=old;events.append('rollback');raise
            else:events.append('commit')
        repository=PostgresQuestionAnsweringRepository(SimpleNamespace(_transaction=transaction),FakeObjectStorage())
        args=dict(run_id=run,request_fingerprint=fingerprint,context_sources=(),selection=selection,
            egress_authorization=authorization,request_payload=payload,idempotency_key=run)
        repository.persist_failed(ctx,**args)
        self.assertEqual(len(receipts),1);self.assertEqual((receipts[0][6],receipts[0][8:10],receipts[0][12]),(None,(None,None),'uncertain'))
        self.assertFalse(any(sql.startswith('INSERT INTO model_attempts') for sql in queries))
        fail[0]=True
        with self.assertRaises(QuestionRepositoryError) as failed:repository.persist_failed(ctx,**args)
        self.assertEqual((failed.exception.status,str(failed.exception)),(503,'QUESTION_DATABASE_UNAVAILABLE'))
        self.assertEqual((len(receipts),events),(1,['commit','rollback']))

    def _complete_cost(self, *, provider_called=True):
        from decimal import Decimal
        from daon_user_api.operations_telemetry import ReportedCost
        self.repository.persist_completed(self.context, run_id='run-cost', source_id='source-cp3',
            source_version_id='source-version-cp3', question='cost?',
            selection=TextModelSelection('OLLAMA', 'https://models.example', 'profile', 'deployment', 'model', 1,
                connection_id='connection-a'), evidence=(), provider_called=provider_called,
            result=GroundedTextResult('', (), False, {'total_tokens': 3}, ReportedCost(Decimal('1.25'),'KRW','receipt-a')))

    def test_cost_completed_is_in_real_run_transaction_and_old_revision_unchanged(self):
        self.cloud.connection.revision='0071'
        self._complete_cost()
        calls=self.cloud.connection.cost_calls
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][:4],('tenant-cp3','workspace-cp3','run-cost','actor-cp3'))
        self.assertEqual(calls[0][6],self.repository._opaque_id('attempt','run-cost','deployment'))
        self.assertEqual((str(calls[0][8]),calls[0][9],calls[0][12]),('1.25','KRW','known'))
        self.assertEqual(self.cloud.capabilities,['question.execute'])
        self.cloud.connection.cost_calls.clear();self.cloud.connection.revision='0070'
        self._complete_cost();self.assertEqual(self.cloud.connection.cost_calls,[])
        self.cloud.connection.revision='0071'
        self._complete_cost(provider_called=False);self.assertEqual(self.cloud.connection.cost_calls,[])

    def test_cost_append_failure_aborts_run_transaction_without_retry_or_secret(self):
        events=[]
        @contextmanager
        def transaction(context):
            try:yield self.cloud.connection
            except BaseException:
                events.append('rollback');raise
            else:events.append('commit')
        self.cloud._transaction=transaction
        self.cloud.connection.revision='0071';self.cloud.connection.cost_error=True
        with self.assertRaises(QuestionRepositoryError) as failed:self._complete_cost()
        self.assertEqual((failed.exception.status,str(failed.exception)),(503,'QUESTION_DATABASE_UNAVAILABLE'))
        self.assertEqual(events,['rollback']);self.assertEqual(len(self.cloud.connection.cost_calls),1)

    def test_cost_receipts_support_exact_0072_0073_but_reject_unknown_future_revision(self):
        for revision in ('0072','0073'):
            with self.subTest(revision=revision):
                self.cloud.connection.revision=revision
                self.cloud.connection.cost_calls.clear()
                self._complete_cost()
                self.assertEqual(len(self.cloud.connection.cost_calls),1)
        self.cloud.connection.revision='0074'
        with self.assertRaisesRegex(QuestionRepositoryError,'QUESTION_DATABASE_UNAVAILABLE'):
            self._complete_cost()

    def test_commit_rechecks_selected_source_before_any_result_write(self):
        # Revocation occurs after the service recheck but before this transaction.
        execute = self.cloud.connection.execute
        def revoked(sql, params=()):
            if sql.startswith('SELECT canonical_json FROM source_versions'):
                return Cursor(({'origin_kind': 'mcp_open_law',
                    'connector_id': 'mcp-open-law-go-kr', 'snapshot_id': 'law-snapshot'},))
            return execute(sql, params)
        self.cloud.connection.execute = revoked
        with self.assertRaisesRegex(QuestionRepositoryError, 'QUESTION_SOURCE_UNAVAILABLE'):
            self.repository.persist_completed(self.context, run_id='run-law-commit',
                source_id='source-cp3', source_version_id='source-version-cp3',
                question='원문은?', selection=TextModelSelection('OLLAMA', 'https://models.example.com',
                    'profile', 'deployment', 'fixture-model', 1), evidence=(),
                result=GroundedTextResult('', (), False, {}))
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in self.cloud.connection.queries))

    def test_audio_citation_keeps_time_locator_and_reads_bound_transcript(self):
        from dataclasses import replace
        from test_audio_document_understanding import ready_audio_result
        from test_audio_source import wav_bytes
        result = ready_audio_result();raw = wav_bytes()
        row = list(self.cloud.connection.ready_row)
        row[3], row[6], row[7], row[8] = 'qa.wav', hashlib.sha256(raw).hexdigest(), len(raw), 'audio/wav'
        self.cloud.connection.ready_row = tuple(row);self.repository._object_storage.content = raw
        locator = {'kind': 'audio_segment', 'value': result.parser.audio_evidence[0]['locator_value']}
        chunk = IndexedEvidenceChunk('chunk-audio', 'source-cp3', 'source-version-cp3', 1,
            result.semantic.key_facts[0], 'span-audio', 1.0, 'audio_segment', locator['value'])
        stored = self.repository.persist_completed(self.context, run_id='run-audio', source_id='source-cp3',
            source_version_id='source-version-cp3', question='What code?',
            selection=TextModelSelection('OLLAMA', 'https://models.example.com', 'profile', 'deployment', 'fixture-model', 1),
            evidence=(chunk,), result=GroundedTextResult(result.semantic.key_facts[0], ('chunk-audio',), False, {}))
        self.assertEqual(stored.citations[0].locator, locator)
        payload = {'source_id': result.source_id, 'source_version_id': result.source_version_id, 'status': result.status,
            'semantic': {'title': result.semantic.title, 'summary': result.semantic.summary, 'key_facts': list(result.semantic.key_facts)},
            'parser_validation': {'text': '', 'markdown': '', 'html': '', 'pages': [], 'page_texts': [],
                'regions': [], 'role': 'validation_only', 'audio_evidence': list(result.parser.audio_evidence)}, 'lineage': dict(result.lineage), 'conflict': None}
        self.cloud.connection.audio_understanding_row = ('ur-audio', payload)
        evidence = {'kind': 'audio_segment_fact', 'source_id': 'source-cp3', 'source_version_id': 'source-version-cp3',
            'understanding_result_id': 'ur-audio', 'transcript_sha256': result.lineage['transcript_sha256'], 'locator': locator, 'text': result.semantic.key_facts[0]}
        self.cloud.connection.citation_row = ('source-cp3', 'source-version-cp3', '1', '1', evidence, 'uploaded', 'qa.wav', 'ready', 'span-audio', locator)
        context = replace(self.context, notebook_id='notebook-cp3')
        content, actual = self.repository.read_citation_content(context, 'citation-audio')
        view = json.loads(content.content)
        self.assertEqual(actual, locator)
        self.assertEqual(view['transcript_sha256'], result.lineage['transcript_sha256'])
        binary, binary_locator = self.repository.read_citation_content(context, 'citation-audio', representation='audio')
        self.assertEqual((binary.content, binary.media_type, binary_locator), (raw, 'audio/wav', locator))
        self.cloud.connection.source_access_allowed = False
        for representation in (None, 'audio'):
            with self.subTest(revoked=representation), self.assertRaises(QuestionRepositoryError):
                self.repository.read_citation_content(context, 'citation-audio', representation=representation)
        self.cloud.connection.source_access_allowed = True
        with self.assertRaises(QuestionRepositoryError):
            self.repository.read_citation_content(context, 'citation-audio', representation='unsupported')
        self.assertEqual(view['segments'][0]['text'], result.semantic.key_facts[0])
        self.assertEqual(view['source_version_id'], 'source-version-cp3')
        self.assertEqual(self.repository.load_ready_source(context, 'source-cp3', 'source-version-cp3').filename, 'qa.wav')
        from copy import deepcopy
        citation = self.cloud.connection.citation_row
        original_payload = deepcopy(payload)
        for invalid in ('source-hash', 'transcript-hash', 'mime', 'binding', 'time', 'text', 'understanding', 'version', 'notebook'):
            current = row.copy();ev = dict(evidence)
            self.cloud.connection.audio_understanding_row = ('ur-audio', deepcopy(original_payload))
            self.cloud.connection.source_binding_active = True
            if invalid == 'source-hash': current[6] = 'c' * 64
            if invalid == 'transcript-hash': ev['transcript_sha256'] = 'c' * 64
            if invalid == 'mime': current[8] = 'text/plain'
            if invalid == 'binding': self.cloud.connection.source_binding_active = False
            if invalid == 'time': ev['locator'] = {'kind': 'audio_segment', 'value': '{"segment":"s:1","start_ms":1,"end_ms":1000,"start":0,"end":27}'}
            if invalid == 'text': ev['text'] = 'invented'
            if invalid == 'understanding': self.cloud.connection.audio_understanding_row = None
            if invalid == 'version': self.cloud.connection.audio_understanding_row[1]['source_version_id'] = 'other-version'
            self.cloud.connection.ready_row = tuple(current)
            self.cloud.connection.citation_row = None if invalid == 'notebook' else (*citation[:4], ev, *citation[5:])
            with self.subTest(invalid=invalid), self.assertRaises(QuestionRepositoryError):
                self.repository.read_citation_content(context, 'citation-audio')

    def test_pptx_citation_storage_replay_and_original_view_keep_exact_block_and_scope(self):
        from dataclasses import replace
        from test_pptx_document_understanding import original, MIME
        from daon_user_api.pptx_source import encode_pptx_block
        raw = original(); row = list(self.cloud.connection.ready_row)
        row[3], row[6], row[7], row[8] = 'qa.pptx', hashlib.sha256(raw).hexdigest(), len(raw), MIME
        self.cloud.connection.ready_row = tuple(row); self.repository._object_storage.content = raw
        locator = {'kind': 'pptx_block', 'value': encode_pptx_block('s:1:sp:2:p:2', 6, 18)}
        chunk = IndexedEvidenceChunk('chunk-pptx', 'source-cp3', 'source-version-cp3', 1,
            'REF-DELTA-17', 'span-pptx', 1.0, 'pptx_block', locator['value'])
        stored = self.repository.persist_completed(self.context, run_id='run-pptx', source_id='source-cp3',
            source_version_id='source-version-cp3', question='What code?',
            selection=TextModelSelection('OLLAMA', 'https://models.example.com', 'profile', 'deployment', 'fixture-model', 1),
            evidence=(chunk,), result=GroundedTextResult('REF-DELTA-17', ('chunk-pptx',), False, {}))
        self.assertEqual(stored.citations[0].locator, locator)
        self.cloud.connection.completed_rows = [('rr-pptx', {'answer': 'REF-DELTA-17', 'insufficient': False}, stored.citations[0].citation_id,
            {'source_id': 'source-cp3', 'source_version_id': 'source-version-cp3', 'evidence_span_id': 'span-pptx', 'page': 1, 'locator': locator}, 'server_internal', None)]
        self.assertEqual(self.repository.load_completed(self.context, 'run-pptx').citations[0].locator, locator)
        context = replace(self.context, notebook_id='notebook-cp3')
        evidence = {'kind': 'pptx_block_fact', 'source_id': 'source-cp3', 'source_version_id': 'source-version-cp3', 'locator': locator, 'text': 'REF-DELTA-17'}
        citation = ('source-cp3', 'source-version-cp3', '1', '1', evidence, 'uploaded', 'qa.pptx', 'ready', 'span-pptx', locator)
        self.cloud.connection.citation_row = citation
        content, actual = self.repository.read_citation_content(context, 'citation-pptx')
        view = json.loads(content.content)
        self.assertEqual(content.media_type, 'application/json'); self.assertEqual(actual, locator)
        self.assertEqual(view['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(view['source_version_id'], 'source-version-cp3')
        self.assertEqual(view['selected_block_id'], 's:1:sp:2:p:2')
        selected = next(b for b in view['blocks'] if b['block_id'] == 's:1:sp:2:p:2')
        self.assertEqual(selected['text'][view['selected_start']:view['selected_end']], 'REF-DELTA-17')
        for invalid in ('digest', 'size', 'mime', 'manifest', 'binding', 'locator', 'text', 'source-version', 'other-notebook'):
            with self.subTest(invalid=invalid):
                current = row.copy(); ev = dict(evidence); stored_locator = locator
                self.cloud.connection.source_binding_active = True
                if invalid == 'digest': current[6] = '0' * 64
                if invalid == 'size': current[7] += 1
                if invalid == 'mime': current[8] = 'text/plain'
                if invalid == 'manifest': current[9] = 'pending'
                if invalid == 'binding': self.cloud.connection.source_binding_active = False
                if invalid == 'locator': stored_locator = {'kind': 'pptx_block', 'value': encode_pptx_block('s:1:sp:2:p:3', 6, 16)}
                if invalid == 'text': ev['text'] = 'forged'
                if invalid == 'source-version': ev['source_version_id'] = 'wrong-version'
                if invalid == 'other-notebook': self.cloud.connection.citation_row = None
                else: self.cloud.connection.citation_row = (*citation[:4], ev, *citation[5:9], stored_locator)
                self.cloud.connection.ready_row = tuple(current)
                with self.assertRaises(QuestionRepositoryError): self.repository.read_citation_content(context, 'citation-pptx')

    def test_pptx_ready_source_uses_real_seven_column_select_and_existing_scope(self):
        from dataclasses import replace
        connection = self.cloud.connection
        row = list(connection.ready_row)
        row[3] = 'qa.pptx'; row[8] = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        context = replace(self.context, notebook_id='notebook-cp3')
        connection.ready_row = tuple(row)
        ready = self.repository.load_ready_source(context, 'source-cp3', 'source-version-cp3')
        self.assertEqual(ready.filename, 'qa.pptx')
        sql = connection.queries[-1]
        self.assertIn('o.content_type,o.status', sql.split(' FROM sources', 1)[0])
        self.assertIn("nb.binding_kind='source'", sql)
        self.assertIn('newer.version>sv.version', sql)
        self.assertIn('dr.source_active=false', sql)
        for field, invalid in ((8, 'application/pdf'), (9, 'pending'), (2, 'processing')):
            with self.subTest(field=field):
                bad = row.copy(); bad[field] = invalid; connection.ready_row = tuple(bad)
                with self.assertRaisesRegex(QuestionRepositoryError, 'QUESTION_SOURCE_UNAVAILABLE'):
                    self.repository.load_ready_source(context, 'source-cp3', 'source-version-cp3')
        connection.ready_row = tuple(row); connection.source_binding_active = False
        with self.assertRaisesRegex(QuestionRepositoryError, 'QUESTION_SOURCE_UNAVAILABLE'):
            self.repository.load_ready_source(context, 'source-cp3', 'source-version-cp3')

    def test_docx_citation_storage_replay_and_original_view_keep_exact_block_and_scope(self):
        from dataclasses import replace
        from test_docx_document_understanding import original, MIME
        from daon_user_api.docx_source import encode_docx_block
        raw = original(); row = list(self.cloud.connection.ready_row)
        row[3], row[6], row[7], row[8] = 'qa.docx', hashlib.sha256(raw).hexdigest(), len(raw), MIME
        self.cloud.connection.ready_row = tuple(row); self.repository._object_storage.content = raw
        locator = {'kind': 'docx_block', 'value': encode_docx_block('p:2', 6, 18)}
        chunk = IndexedEvidenceChunk('chunk-docx', 'source-cp3', 'source-version-cp3', 1,
            'REF-DELTA-17', 'span-docx', 1.0, 'docx_block', locator['value'])
        stored = self.repository.persist_completed(self.context, run_id='run-docx', source_id='source-cp3',
            source_version_id='source-version-cp3', question='What code?',
            selection=TextModelSelection('OLLAMA', 'https://models.example.com', 'profile', 'deployment', 'fixture-model', 1),
            evidence=(chunk,), result=GroundedTextResult('REF-DELTA-17', ('chunk-docx',), False, {}))
        self.assertEqual(stored.citations[0].locator, locator)
        self.cloud.connection.completed_rows = [('rr-docx', {'answer': 'REF-DELTA-17', 'insufficient': False}, stored.citations[0].citation_id,
            {'source_id': 'source-cp3', 'source_version_id': 'source-version-cp3', 'evidence_span_id': 'span-docx', 'page': 1, 'locator': locator}, 'server_internal', None)]
        self.assertEqual(self.repository.load_completed(self.context, 'run-docx').citations[0].locator, locator)
        context = replace(self.context, notebook_id='notebook-cp3')
        evidence = {'kind': 'docx_block_fact', 'source_id': 'source-cp3', 'source_version_id': 'source-version-cp3', 'locator': locator, 'text': 'REF-DELTA-17'}
        citation = ('source-cp3', 'source-version-cp3', '1', '1', evidence, 'uploaded', 'qa.docx', 'ready', 'span-docx', locator)
        self.cloud.connection.citation_row = citation
        content, actual = self.repository.read_citation_content(context, 'citation-docx')
        view = json.loads(content.content)
        self.assertEqual(content.media_type, 'application/json'); self.assertEqual(actual, locator)
        self.assertEqual(view['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(view['source_version_id'], 'source-version-cp3')
        self.assertEqual(view['selected_block_id'], 'p:2')
        selected = next(b for b in view['blocks'] if b['block_id'] == 'p:2')
        self.assertEqual(selected['text'][view['selected_start']:view['selected_end']], 'REF-DELTA-17')
        for invalid in ('digest', 'size', 'mime', 'manifest', 'binding', 'locator', 'text', 'source-version', 'other-notebook'):
            with self.subTest(invalid=invalid):
                current = row.copy(); ev = dict(evidence); stored_locator = locator
                self.cloud.connection.source_binding_active = True
                if invalid == 'digest': current[6] = '0' * 64
                if invalid == 'size': current[7] += 1
                if invalid == 'mime': current[8] = 'text/plain'
                if invalid == 'manifest': current[9] = 'pending'
                if invalid == 'binding': self.cloud.connection.source_binding_active = False
                if invalid == 'locator': stored_locator = {'kind': 'docx_block', 'value': encode_docx_block('p:3', 6, 16)}
                if invalid == 'text': ev['text'] = 'forged'
                if invalid == 'source-version': ev['source_version_id'] = 'wrong-version'
                if invalid == 'other-notebook': self.cloud.connection.citation_row = None
                else: self.cloud.connection.citation_row = (*citation[:4], ev, *citation[5:9], stored_locator)
                self.cloud.connection.ready_row = tuple(current)
                with self.assertRaises(QuestionRepositoryError): self.repository.read_citation_content(context, 'citation-docx')

    def test_docx_ready_source_uses_real_seven_column_select_and_existing_scope(self):
        from dataclasses import replace
        connection = self.cloud.connection
        row = list(connection.ready_row)
        row[3] = 'qa.docx'; row[8] = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        context = replace(self.context, notebook_id='notebook-cp3')
        connection.ready_row = tuple(row)
        ready = self.repository.load_ready_source(context, 'source-cp3', 'source-version-cp3')
        self.assertEqual(ready.filename, 'qa.docx')
        sql = connection.queries[-1]
        self.assertIn('o.content_type,o.status', sql.split(' FROM sources', 1)[0])
        self.assertIn("nb.binding_kind='source'", sql)
        self.assertIn('newer.version>sv.version', sql)
        self.assertIn('dr.source_active=false', sql)
        for field, invalid in ((8, 'application/pdf'), (9, 'pending'), (2, 'processing')):
            with self.subTest(field=field):
                bad = row.copy(); bad[field] = invalid; connection.ready_row = tuple(bad)
                with self.assertRaisesRegex(QuestionRepositoryError, 'QUESTION_SOURCE_UNAVAILABLE'):
                    self.repository.load_ready_source(context, 'source-cp3', 'source-version-cp3')
        connection.ready_row = tuple(row); connection.source_binding_active = False
        with self.assertRaisesRegex(QuestionRepositoryError, 'QUESTION_SOURCE_UNAVAILABLE'):
            self.repository.load_ready_source(context, 'source-cp3', 'source-version-cp3')

    def test_docx_original_json_limit_includes_added_source_version_field(self):
        from dataclasses import replace
        from test_docx_source import docx_bytes, paragraph
        from test_docx_document_understanding import MIME
        from daon_user_api.docx_source import read_docx, read_docx_block_view, encode_docx_block
        from daon_user_api.data_canon import canonical_json_bytes

        def raw_with_style(name):
            return docx_bytes(paragraph('REF', properties=f'<w:pPr><w:pStyle w:val="{name}"/></w:pPr>'),
                styles=f'<w:style w:type="paragraph" w:styleId="{name}"/>')

        locator = {'kind': 'docx_block', 'value': encode_docx_block('p:1', 0, 3)}
        initial = read_docx_block_view(read_docx(raw_with_style('s')), locator['value'])
        raw = raw_with_style('s' * (32768 - len(canonical_json_bytes(initial)) + 1))
        self.assertEqual(len(canonical_json_bytes(read_docx_block_view(read_docx(raw), locator['value']))), 32768)
        row = list(self.cloud.connection.ready_row)
        row[3], row[6], row[7], row[8] = 'qa.docx', hashlib.sha256(raw).hexdigest(), len(raw), MIME
        self.cloud.connection.ready_row = tuple(row); self.repository._object_storage.content = raw
        evidence = {'kind': 'docx_block_fact', 'source_id': 'source-cp3', 'source_version_id': 'source-version-cp3', 'text': 'REF', 'locator': locator}
        self.cloud.connection.citation_row = ('source-cp3', 'source-version-cp3', '1', '1', evidence, 'uploaded', 'qa.docx', 'ready', 'span-docx', locator)
        with self.assertRaisesRegex(QuestionRepositoryError, '^CITATION_CONTENT_UNAVAILABLE$'):
            self.repository.read_citation_content(replace(self.context, notebook_id='notebook-cp3'), 'citation-docx')

    def test_xlsx_citation_content_is_bound_original_cell_json(self):
        from dataclasses import replace
        from test_xlsx_source import parts, zipped
        from daon_user_api.xlsx_source import read_xlsx, read_sheet_cell_view, encode_sheet_cell
        self.context = replace(self.context, notebook_id='notebook-cp3')
        raw = zipped(parts())
        locator = {'kind': 'sheet_cell', 'value': encode_sheet_cell('납품 "계획"', 'B2:C3')}
        evidence = {'kind': 'xlsx_cell_fact', 'source_id': 'source-cp3', 'source_version_id': 'source-version-cp3', 'locator': locator, 'text': 'ORANGE-42'}
        citation = ('source-cp3', 'source-version-cp3', '1', '1', evidence, 'uploaded', 'qa.xlsx', 'ready', 'span-cell', locator)
        original = list(self.cloud.connection.ready_row)
        original[3], original[6], original[7], original[8] = 'qa.xlsx', hashlib.sha256(raw).hexdigest(), len(raw), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        self.cloud.connection.ready_row = tuple(original)
        self.cloud.connection.citation_row = citation
        self.repository._object_storage.content = raw
        content, actual_locator = self.repository.read_citation_content(self.context, 'citation-cell')
        expected = read_sheet_cell_view(read_xlsx(raw), locator['value'])
        expected['source_version_id'] = 'source-version-cp3'
        self.assertEqual(json.loads(content.content), expected)
        self.assertEqual(content.media_type, 'application/json')
        self.assertEqual(actual_locator, locator)
        for scenario in ('digest', 'size', 'mime', 'manifest', 'binding', 'citation', 'evidence', 'range', 'source-version'):
            with self.subTest(scenario=scenario):
                row = list(original); ev = dict(evidence); stored_locator = locator
                self.cloud.connection.source_binding_active = True
                if scenario == 'digest': row[6] = '0' * 64
                if scenario == 'size': row[7] += 1
                if scenario == 'mime': row[8] = 'text/plain'
                if scenario == 'manifest': row[9] = 'pending'
                if scenario == 'binding': self.cloud.connection.source_binding_active = False
                if scenario == 'citation': stored_locator = {'kind': 'sheet_cell', 'value': encode_sheet_cell('예산', 'A1')}
                if scenario == 'evidence': ev['kind'] = 'xlsx_not_evidence'
                if scenario == 'range': ev['locator'] = stored_locator = {'kind': 'sheet_cell', 'value': encode_sheet_cell('예산', 'Z99')}
                if scenario == 'source-version': ev['source_version_id'] = 'source-version-other'
                self.cloud.connection.ready_row = tuple(row)
                self.cloud.connection.citation_row = (*citation[:4], ev, *citation[5:9], stored_locator)
                with self.assertRaises(QuestionRepositoryError):
                    self.repository.read_citation_content(self.context, 'citation-cell')

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
                    CREATE TABLE identity_auth_source_access(tenant_id,workspace_id,user_id,source_version_id,allowed);
                ''')
                scope = ('tenant-cp3', 'workspace-cp3')
                self.db.execute('INSERT INTO sources VALUES(?,?,?,?,?)', (*scope, 'source-cp3', 'ready', json.dumps({'kind': 'uploaded'})))
                self.db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?,?,?)', (*scope, 'source-version-cp3', 'source-cp3', 'object-xlsx', 1, json.dumps({'filename': 'qa.xlsx'})))
                self.db.execute('INSERT INTO object_records VALUES(?,?,?,?,?)', (*scope, 'object-xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'completed'))
                self.db.execute('INSERT INTO index_versions VALUES(?,?,?)', (*scope, 'source-version-cp3'))
                self.db.execute('INSERT INTO notebook_bindings VALUES(?,?,?,?,?,?)', (*scope, 'notebook-cp3', 'source', 'source-cp3', 'source-version-cp3'))
            def execute(self, sql, params=()):
                cursor = self.db.execute(sql.replace('%s', '?'), params)
                if sql.startswith('SELECT NOT EXISTS (SELECT 1 FROM identity_auth_source_access'):
                    # 실제 ACL SELECT를 실행하되 이 시험의 source DTO 열 진단과 분리한다.
                    return Cursor(cursor.fetchone())
                if sql.startswith('SELECT canonical_json FROM source_versions'):
                    row=cursor.fetchone()
                    return Cursor((json.loads(row[0]),) if row else None)
                self.columns = tuple(column[0] for column in cursor.description)
                row = cursor.fetchone(); self.rows.append(row)
                return Cursor(row)
        context = replace(self.context, notebook_id='notebook-cp3')
        scenarios = ('ready', 'wrong-mime', 'incomplete', 'missing-manifest', 'other-object-scope', 'unbound', 'old-version', 'deleted', 'other-notebook', 'acl-denied')
        for scenario in (*scenarios, *('docx-' + s for s in scenarios), *('pptx-' + s for s in scenarios)):
            with self.subTest(scenario=scenario):
                connection = SqlConnection(); self.cloud.connection = connection
                try:
                    docx = scenario.startswith('docx-')
                    pptx = scenario.startswith('pptx-')
                    if docx:
                        scenario = scenario.removeprefix('docx-')
                        connection.db.execute('UPDATE source_versions SET canonical_json=?', (json.dumps({'filename': 'qa.docx'}),))
                        connection.db.execute('UPDATE object_records SET content_type=?', ('application/vnd.openxmlformats-officedocument.wordprocessingml.document',))
                    if pptx:
                        scenario = scenario.removeprefix('pptx-')
                        connection.db.execute('UPDATE source_versions SET canonical_json=?', (json.dumps({'filename': 'qa.pptx'}),))
                        connection.db.execute('UPDATE object_records SET content_type=?', ('application/vnd.openxmlformats-officedocument.presentationml.presentation',))
                    if scenario == 'wrong-mime': connection.db.execute("UPDATE object_records SET content_type='text/plain'")
                    if scenario == 'incomplete': connection.db.execute("UPDATE object_records SET status='pending'")
                    if scenario == 'missing-manifest': connection.db.execute('DELETE FROM object_records')
                    if scenario == 'other-object-scope': connection.db.execute("UPDATE object_records SET workspace_id='workspace-other'")
                    if scenario == 'unbound': connection.db.execute('DELETE FROM notebook_bindings')
                    if scenario == 'old-version': connection.db.execute("INSERT INTO source_versions SELECT tenant_id,workspace_id,'new-version',source_id,object_id,2,canonical_json FROM source_versions")
                    if scenario == 'deleted': connection.db.execute("INSERT INTO deletion_requests VALUES('tenant-cp3','workspace-cp3','source-cp3',false)")
                    if scenario == 'acl-denied': connection.db.execute("INSERT INTO identity_auth_source_access VALUES('tenant-cp3','workspace-cp3','actor-cp3','source-version-cp3',0)")
                    selected_context = replace(context, notebook_id='notebook-other') if scenario == 'other-notebook' else context
                    if scenario == 'ready':
                        source = self.repository.load_ready_source(selected_context, 'source-cp3', 'source-version-cp3')
                        filename = 'qa.docx' if docx else 'qa.xlsx'
                        mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' if docx else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                        if pptx:
                            filename, mime = 'qa.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                        self.assertEqual((source.source_id, source.source_version_id, source.filename), ('source-cp3', 'source-version-cp3', filename))
                        self.assertEqual(connection.rows[-1], ('source-cp3', 'source-version-cp3', 'ready', filename, 'uploaded', mime, 'completed'))
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
