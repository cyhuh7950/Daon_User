from __future__ import annotations

import hashlib
import json
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from daon_user_api.docx_document_understanding import OllamaDocxDocumentAdapter, validate_block_evidence
from daon_user_api.docx_source import read_docx, read_docx_block_view, encode_docx_block, decode_docx_block
from daon_user_api.document_processing import DocumentProcessingContext, StoredSourceDocument, docx_source_format
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
from daon_user_api.workspace_model_defaults import ResolvedModel
from test_docx_source import docx_bytes, paragraph, table


MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def original():
    return docx_bytes(paragraph('') + paragraph('Code: REF-DELTA-17 가😀나') + paragraph('Date: 2026-12-08') + table([[paragraph('57000'), paragraph('KRW')]]))


def native_value():
    return {'title': 'Plan', 'summary': 'REF-DELTA-17 2026-12-08 57000 KRW', 'key_facts': [
        {'text': 'REF-DELTA-17', 'block_id': 'p:2', 'start': 6, 'end': 18},
        {'text': '2026-12-08', 'block_id': 'p:3', 'start': 6, 'end': 16},
        {'text': '57000', 'block_id': 't:4:r:1:c:1:p:1', 'start': 0, 'end': 5},
        {'text': 'KRW', 'block_id': 't:4:r:1:c:2:p:1', 'start': 0, 'end': 3}],
        'block_coverage': ['p:2', 'p:3', 't:4:r:1:c:1:p:1', 't:4:r:1:c:2:p:1']}


class Transport:
    def __init__(self):
        self.events, self.requests = [], []
        self.value = native_value()
        self.show = {'capabilities': ['completion', 'thinking'], 'model_info': {'architecture': 'fixture', 'tokenizer.ggml.add_eos_token': True}}
        self.digest = 'a' * 64
        self.error = None

    def get_json_no_auth(self, **kwargs):
        self.events.append('tags'); self.requests.append(kwargs)
        return {'models': [{'name': 'fixture-model', 'digest': self.digest}]}

    def post_json_no_auth(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs['url'].endswith('/api/show'):
            self.events.append('show'); return self.show
        self.events.append('native')
        if self.error: raise self.error
        return {'model': 'fixture-model', 'message': {'content': self.value if isinstance(self.value, str) else json.dumps(self.value, ensure_ascii=False)}}


def ready_docx_result():
    context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
    document = StoredSourceDocument('source-cp3', 'source-version-cp3', 'qa.docx', original(), MIME)
    model = ResolvedModel('local', 'OLLAMA', 'fixture-model', 'text_generation', 'https://models.example.com', 0, 1, 1, 'server_internal', 'provider', True, None)
    return OllamaDocxDocumentAdapter(Transport()).understand(document, model, context)


class DocxUnderstandingTests(unittest.TestCase):
    def setUp(self):
        self.context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
        self.document = StoredSourceDocument('source-cp3', 'source-version-cp3', 'qa.docx', original(), MIME)
        self.model = ResolvedModel('local', 'OLLAMA', 'fixture-model', 'text_generation', 'https://models.example.com', 0, 1, 1, 'server_internal', 'provider', True, None)
        self.transport = Transport()

    def test_docx_native_then_original_validation_and_empty_page_contract(self):
        def validate(document, value):
            self.assertIn('native', self.transport.events)
            self.transport.events.append('validate')
            return read_docx_block_view(document, value)
        with patch('daon_user_api.docx_document_understanding.read_docx_block_view', side_effect=validate):
            result = OllamaDocxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(self.transport.events[:4], ['tags', 'show', 'tags', 'native'])
        chat = self.transport.requests[3]
        self.assertEqual(chat['payload']['messages'][1]['content'], read_docx(self.document.content).native_json)
        self.assertEqual(chat['timeout_seconds'], 90)
        self.assertEqual(chat['payload']['options'], {'temperature': 0, 'num_predict': 512})
        self.assertIs(chat['payload']['think'], False)
        self.assertTrue(all(0 < call['timeout_seconds'] <= 5 for call in self.transport.requests[:3]))
        self.assertEqual((result.parser.pages, result.parser.page_texts, result.parser.regions, result.parser.cell_evidence), ((), (), (), ()))
        self.assertEqual(result.lineage['source_digest'], hashlib.sha256(self.document.content).hexdigest())
        self.assertEqual(result.lineage['block_coverage'], json.dumps(native_value()['block_coverage'], ensure_ascii=False, separators=(',', ':')))
        self.assertEqual(result.parser.block_evidence[0], {'text': 'REF-DELTA-17', 'block_id': 'p:2', 'locator_value': '{"block":"p:2","start":6,"end":18}'})
        validate_block_evidence(result.parser.block_evidence, result.semantic.key_facts)

    def test_docx_nonthinking_metadata_omits_flag_and_keeps_original_substring(self):
        self.transport.show['capabilities'] = ['completion']
        self.transport.value['key_facts'] = [{'text': '😀', 'block_id': 'p:2', 'start': 20, 'end': 21}]
        self.transport.value['summary'] = '😀'
        result = OllamaDocxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)
        self.assertEqual(result.status, 'ready')
        self.assertNotIn('think', self.transport.requests[3]['payload'])
        self.assertEqual(result.parser.block_evidence[0]['text'], '😀')

    def test_docx_native_prompt_prefers_whole_paragraph_and_keeps_table_values_in_their_own_blocks(self):
        text = 'Memo 😀 KEEP-81'
        document = replace(self.document, content=docx_bytes(paragraph(text) + table([
            [paragraph('Quantity'), paragraph('64')], [paragraph('Currency'), paragraph('EUR')]])))
        self.transport.value = {'title': 'Memo', 'summary': 'KEEP-81 64 EUR', 'key_facts': [
            {'text': text, 'block_id': 'p:1', 'start': 0, 'end': len(text)},
            {'text': '64', 'block_id': 't:2:r:1:c:2:p:1', 'start': 0, 'end': 2},
            {'text': 'EUR', 'block_id': 't:2:r:2:c:2:p:1', 'start': 0, 'end': 3}],
            'block_coverage': ['p:1', 't:2:r:1:c:1:p:1', 't:2:r:1:c:2:p:1', 't:2:r:2:c:1:p:1', 't:2:r:2:c:2:p:1']}
        result = OllamaDocxDocumentAdapter(self.transport).understand(document, self.model, self.context)
        payload = self.transport.requests[3]['payload']
        prompt = payload['messages'][0]['content']
        self.assertIn('The safest choice is to copy the WHOLE original paragraph text unchanged, with start=0 and end equal to its Unicode code-point length.', prompt)
        self.assertIn('Table labels and values in DIFFERENT block_id entries are separate paragraphs: never merge them into one fact.', prompt)
        self.assertIn('For example, block p:7 text "Memo NOVA-73" gives {"text":"Memo NOVA-73","block_id":"p:7","start":0,"end":12}.', prompt)
        self.assertIn('Do not copy example values or positions.', prompt)
        self.assertEqual(payload['messages'][1]['content'], read_docx(document.content).native_json)
        self.assertEqual((self.transport.requests[3]['timeout_seconds'], payload['options']['num_predict']), (90, 512))
        self.assertEqual((payload['format']['properties']['key_facts']['minItems'], payload['format']['properties']['key_facts']['maxItems']), (1, 5))
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.parser.block_evidence[0]['text'], text)
        self.assertEqual(decode_docx_block(result.parser.block_evidence[0]['locator_value']), ('p:1', 0, 14))

    def test_docx_crossblock_label_value_combinations_remain_conflict_without_repair(self):
        document = replace(self.document, content=docx_bytes(table([
            [paragraph('Amount'), paragraph('64')], [paragraph('Currency'), paragraph('EUR')]])))
        coverage = ['t:1:r:1:c:1:p:1', 't:1:r:1:c:2:p:1', 't:1:r:2:c:1:p:1', 't:1:r:2:c:2:p:1']
        for text, block, end in [('Amount 64', coverage[0], 9), ('64 EUR', coverage[1], 6), ('Currency EUR', coverage[3], 12)]:
            with self.subTest(text=text):
                self.transport.value = {'title': 'Table', 'summary': text, 'key_facts': [
                    {'text': text, 'block_id': block, 'start': 0, 'end': end}], 'block_coverage': coverage}
                result = OllamaDocxDocumentAdapter(self.transport).understand(document, self.model, self.context)
                self.assertEqual(result.status, 'needs_review')
                self.assertEqual(result.conflict, 'UNDERSTANDING_PARSER_CONFLICT')
                self.assertEqual(result.parser.block_evidence, ())
                self.assertEqual(result.semantic.key_facts, (text,), 'rejected Native response must not be repaired')

    def test_docx_invalid_facts_never_become_ready(self):
        for field, value in (('text', '000'), ('text', 'USD'), ('block_id', 'p:1'), ('start', 7), ('end', 19), ('start', True)):
            with self.subTest(field=field, value=value):
                self.transport.value = native_value()
                self.transport.value['key_facts'][0][field] = value
                try:
                    result = OllamaDocxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)
                except DocumentUnderstandingError:
                    continue
                self.assertEqual(result.status, 'needs_review')
                self.assertEqual(result.parser.block_evidence, ())
        self.transport.value = native_value(); self.transport.value['summary'] = '999999 USD'
        result = OllamaDocxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)
        self.assertEqual(result.status, 'needs_review'); self.assertEqual(result.parser.block_evidence, ())

    def test_docx_strict_coverage_types_keys_and_duplicate_json_fail_closed(self):
        cases = []
        for field, value in (('block_coverage', []), ('block_coverage', ['p:2']), ('block_coverage', [True]),
                             ('block_coverage', ['p:2'] * 4), ('key_facts', []), ('summary', None), ('title', {})):
            response = native_value(); response[field] = value; cases.append(response)
        response = native_value(); response['extra'] = 0; cases.append(response)
        response = native_value(); response['key_facts'][0]['extra'] = 0; cases.append(response)
        response = native_value(); response['key_facts'].append(deepcopy(response['key_facts'][0])); cases.append(response)
        response = native_value(); response['key_facts'][0] = {'text': ' ', 'block_id': 'p:2', 'start': 5, 'end': 6}; cases.append(response)
        cases += ['', '[]', '{"title":"A","title":"B"}']
        for response in cases:
            with self.subTest(response=response):
                self.transport.value = response
                with self.assertRaises(DocumentUnderstandingError):
                    OllamaDocxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)

    def test_docx_metadata_and_original_failure_prevent_native_call(self):
        for show in ({}, {'capabilities': ['vision'], 'model_info': {'a': 1}},
                     {'capabilities': ['completion'], 'model_info': {'remote_host': 'cloud'}},
                     {'capabilities': ['completion'], 'model_info': {'tokenizer.ggml.add_eos_token': 'credential'}}):
            with self.subTest(show=show):
                self.transport = Transport(); self.transport.show = show
                with self.assertRaises(DocumentUnderstandingError):
                    OllamaDocxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)
                self.assertNotIn('native', self.transport.events)
        self.transport = Transport()
        with self.assertRaises(DocumentUnderstandingError):
            OllamaDocxDocumentAdapter(self.transport).understand(replace(self.document, content=b'bad'), self.model, self.context)
        self.assertEqual(self.transport.events, [])
        with patch('daon_user_api.question_answering.time.monotonic', side_effect=[100, 100, 103, 105]):
            with self.assertRaises(DocumentUnderstandingError):
                OllamaDocxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)
        self.assertNotIn('native', self.transport.events)

    def test_docx_timeout_and_portable_evidence_validation_do_not_repair(self):
        self.transport.error = TimeoutError()
        with self.assertRaises(DocumentUnderstandingError):
            OllamaDocxDocumentAdapter(self.transport).understand(self.document, self.model, self.context)
        fact = {'text': 'text', 'block_id': 'p:2', 'locator_value': encode_docx_block('p:2', 0, 4)}
        validate_block_evidence((fact,), ('text',))
        for changed in ({**fact, 'locator_value': encode_docx_block('p:3', 0, 4)},
                        {**fact, 'text': 'other'}, {**fact, 'locator_value': encode_docx_block('p:2', 0, 3)},
                        {**fact, 'extra': 'unknown'}):
            with self.subTest(changed=changed), self.assertRaises(DocumentUnderstandingError):
                validate_block_evidence((changed,), ('text',))
        self.assertTrue(docx_source_format('A.DOCX', MIME)); self.assertFalse(docx_source_format('A.docx', 'application/pdf'))

