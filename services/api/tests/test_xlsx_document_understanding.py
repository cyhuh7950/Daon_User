import copy
import hashlib
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from daon_user_api.document_processing import DocumentProcessingContext, StoredSourceDocument
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
from daon_user_api.workspace_model_defaults import ResolvedModel
from daon_user_api.xlsx_document_understanding import OllamaXlsxDocumentAdapter
from daon_user_api.xlsx_source import read_xlsx, read_sheet_cell_view
from test_xlsx_source import parts, zipped


def original():
    return zipped(parts([
        ('납품 계획', '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>프로젝트 코드</t></is></c><c r="B1" t="inlineStr"><is><t>ORANGE-42</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>납품일</t></is></c><c r="B2" s="1"><v>46000</v></c></row></sheetData>', 'visible'),
        ('예산', '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>예산 원</t></is></c><c r="B1"><f>57000</f><v>57000</v></c></row></sheetData>', 'hidden'),
    ]))


def response_value():
    return {'title': 'QA 납품', 'summary': 'ORANGE-42 납품일 2025-12-09 예산 57000 원', 'key_facts': [
        {'text': '프로젝트 코드 ORANGE-42', 'sheet': '납품 계획', 'range': 'A1:B1'},
        {'text': '납품일 2025-12-09', 'sheet': '납품 계획', 'range': 'A2:B2'},
        {'text': '예산 원 57000', 'sheet': '예산', 'range': 'A1:B1'},
    ], 'sheet_coverage': ['납품 계획', '예산']}


class Transport:
    def __init__(self):
        self.events = []; self.requests = []; self.value = response_value()
        self.tags = {'models': [{'name': 'llama3.2:3b', 'digest': 'a' * 64}]}
        self.show = {'capabilities': ['completion'], 'model_info': {'general.architecture': 'llama'}}
        self.error = None

    def get_json_no_auth(self, **request):
        self.events.append('tags'); return self.tags

    def post_json_no_auth(self, **request):
        if request['url'].endswith('/api/show'):
            self.events.append('show'); return self.show
        self.events.append('native'); self.requests.append(request)
        if self.error: raise self.error
        return {'model': 'llama3.2:3b', 'message': {'content': self.value if isinstance(self.value, str) else json.dumps(self.value, ensure_ascii=False)}}


class XlsxUnderstandingTests(unittest.TestCase):
    def setUp(self):
        self.context = DocumentProcessingContext('tenant-1', 'workspace-1', 'actor-1', 'trace-1', 'policy-1')
        self.document = StoredSourceDocument('source-1', 'sv-1', 'qa.xlsx', original(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.model = ResolvedModel('ollama-1', 'OLLAMA', 'llama3.2:3b', 'text_generation', 'https://ollama.example', 0, 1, 1, 'server_internal', 'provider', True, None)
        self.transport = Transport()

    def test_xlsx_native_then_cell_validation(self):
        def validate(*args):
            self.assertIn('native', self.transport.events)
            self.transport.events.append('cell-validation')
            return read_sheet_cell_view(*args)
        with patch('daon_user_api.xlsx_document_understanding.read_sheet_cell_view', side_effect=validate):
            result = OllamaXlsxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)
        self.assertEqual(result.status, 'ready')
        request = self.transport.requests[0]
        self.assertEqual(request['timeout_seconds'], 90)
        self.assertEqual(request['payload']['options'], {'temperature': 0, 'num_predict': 512})
        self.assertEqual(request['payload']['messages'][1]['content'], read_xlsx(self.document.content).native_json)
        self.assertEqual(self.transport.events[:4], ['tags', 'show', 'tags', 'native'])
        self.assertEqual(result.parser.pages, ()); self.assertEqual(result.parser.page_texts, ())
        self.assertEqual(result.parser.cell_evidence[0], {'text': '프로젝트 코드 ORANGE-42', 'sheet': '납품 계획', 'range': 'A1:B1', 'locator_value': '{"sheet":"납품 계획","range":"A1:B1"}'})
        self.assertEqual(result.lineage['source_digest'], hashlib.sha256(self.document.content).hexdigest())
        self.assertEqual(result.lineage['native_input_digest'], hashlib.sha256(read_xlsx(self.document.content).native_json.encode()).hexdigest())
        self.assertEqual(json.loads(result.lineage['sheet_coverage']), ['납품 계획', '예산'])

    def test_xlsx_invalid_evidence_not_ready(self):
        for field, wrong in [('text', '프로젝트 코드 ORANGE-99'), ('sheet', 'missing'), ('range', 'C1'), ('range', 'A1:A256')]:
            with self.subTest(field=field, wrong=wrong):
                transport = Transport(); transport.value['key_facts'][0][field] = wrong
                result = OllamaXlsxDocumentAdapter(transport).understand(self.document, self.model, self.context)
                self.assertEqual(result.status, 'needs_review'); self.assertEqual(result.conflict, 'UNDERSTANDING_PARSER_CONFLICT')
                self.assertEqual(result.parser.cell_evidence, ())
        for text in ['납품일 2025-12-10', '예산 달러 57000', '예산 원 99999']:
            transport = Transport(); transport.value['key_facts'][1 if text.startswith('납품일') else 2]['text'] = text
            self.assertEqual(OllamaXlsxDocumentAdapter(transport).understand(self.document, self.model, self.context).status, 'needs_review')

    def test_strict_response_coverage_and_malformed_metadata_fail_safely(self):
        values = ['', '{"title":', None, {**response_value(), 'extra': True},
            {**response_value(), 'sheet_coverage': ['납품 계획']},
            {**response_value(), 'sheet_coverage': ['납품 계획', '예산', '예산']},
            {**response_value(), 'key_facts': []},
            {**response_value(), 'key_facts': [response_value()['key_facts'][0]] * 2},
            json.dumps(response_value())[:-1] + ',"title":"duplicate"}',
        ]
        for value in values:
            with self.subTest(value=value):
                transport = Transport(); transport.value = value
                with self.assertRaises(DocumentUnderstandingError): OllamaXlsxDocumentAdapter(transport).understand(self.document, self.model, self.context)
        for show in [None, {}, {'capabilities': None}, {'capabilities': 'completion'}, {'capabilities': ['completion'], 'model_info': {'remote_host': 'cloud'}}]:
            transport = Transport(); transport.show = show
            with self.subTest(show=show):
                with self.assertRaises(DocumentUnderstandingError): OllamaXlsxDocumentAdapter(transport).understand(self.document, self.model, self.context)
                self.assertEqual(transport.requests, [])

    def test_invalid_original_and_external_selection_make_no_http_calls(self):
        transport = Transport()
        with self.assertRaises(DocumentUnderstandingError): OllamaXlsxDocumentAdapter(transport).understand(replace(self.document, content=b'not-zip'), self.model, self.context)
        self.assertEqual(transport.events, [])
        for kind in ['external_api', 'unknown']:
            with self.assertRaises(DocumentUnderstandingError): OllamaXlsxDocumentAdapter(transport).understand(self.document, replace(self.model, provider_kind=kind), self.context)
        self.assertEqual(transport.events, [])

    def test_timeout_and_thinking_are_not_semantic_success(self):
        self.transport.error = TimeoutError()
        with self.assertRaisesRegex(DocumentUnderstandingError, 'UNDERSTANDING_PROVIDER_UNAVAILABLE'):
            OllamaXlsxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)
        self.transport.error = None; self.transport.value = ''
        with self.assertRaises(DocumentUnderstandingError): OllamaXlsxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)

    def test_missing_formula_cache_and_error_are_not_fact_evidence(self):
        for cell in ['<c r="A1"><f>57000</f></c>', '<c r="A1" t="e"><v>#DIV/0!</v></c>']:
            transport = Transport(); transport.value = {'title': 'QA', 'summary': '57000', 'key_facts': [{'text': '57000', 'sheet': '예산', 'range': 'A1'}], 'sheet_coverage': ['예산']}
            document = replace(self.document, content=zipped(parts([('예산', f'<sheetData><row r="1">{cell}</row></sheetData>', 'hidden')])))
            self.assertEqual(OllamaXlsxDocumentAdapter(transport).understand(document, self.model, self.context).status, 'needs_review')

    def test_partial_numeric_anchor_is_not_a_different_cell_fact(self):
        transport = Transport(); transport.value = {'title': 'QA', 'summary': '예산', 'key_facts': [{'text': '000', 'sheet': '예산', 'range': 'A1'}], 'sheet_coverage': ['예산']}
        document = replace(self.document, content=zipped(parts([('예산', '<sheetData><row r="1"><c r="A1"><v>57000</v></c></row></sheetData>', 'visible')])))
        self.assertEqual(OllamaXlsxDocumentAdapter(transport).understand(document, self.model, self.context).status, 'needs_review')

    def test_partial_word_and_summary_currency_are_not_original_proof(self):
        transport = Transport(); transport.value = {'title': 'QA', 'summary': '상태', 'key_facts': [{'text': 'PROVED', 'sheet': '상태', 'range': 'A1'}], 'sheet_coverage': ['상태']}
        document = replace(self.document, content=zipped(parts([('상태', '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>APPROVED</t></is></c></row></sheetData>', 'visible')])))
        with self.subTest(boundary='word'):
            self.assertEqual(OllamaXlsxDocumentAdapter(transport).understand(document, self.model, self.context).status, 'needs_review')
        transport = Transport(); transport.value['summary'] = '예산 57000 달러'
        with self.subTest(boundary='currency'):
            self.assertEqual(OllamaXlsxDocumentAdapter(transport).understand(self.document, self.model, self.context).status, 'needs_review')

    def test_remote_or_changed_digest_is_denied_before_native_payload(self):
        for scenario in ('remote', 'changed', 'missing'):
            with self.subTest(scenario=scenario):
                transport = Transport(); original_get = transport.get_json_no_auth
                def tags(**request):
                    value = copy.deepcopy(original_get(**request))
                    if scenario == 'remote': value['models'][0]['remote_model'] = 'cloud-model'
                    if scenario == 'missing': value['models'][0]['digest'] = None
                    if scenario == 'changed' and transport.events.count('tags') == 2: value['models'][0]['digest'] = 'b' * 64
                    return value
                transport.get_json_no_auth = tags
                with self.assertRaises(DocumentUnderstandingError): OllamaXlsxDocumentAdapter(transport).understand(self.document, self.model, self.context)
                self.assertEqual(transport.requests, [])
