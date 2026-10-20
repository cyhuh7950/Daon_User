from __future__ import annotations
import json
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from daon_user_api.pptx_document_understanding import OllamaPptxDocumentAdapter, validate_slide_evidence
from daon_user_api.pptx_source import read_pptx, read_pptx_block_view
from daon_user_api.document_processing import DocumentProcessingContext, StoredSourceDocument, pptx_source_format
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
from daon_user_api.workspace_model_defaults import ResolvedModel
from test_pptx_source import zip_bytes, pptx_parts, slide, shape, paragraph, table
from test_docx_document_understanding import Transport

MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

def original():
    return zip_bytes(pptx_parts([slide(shape(2, [paragraph(''), paragraph('Code: REF-DELTA-17 가😀나'), paragraph('Date: 2026-12-08')])), slide(table(3))]))

def native_value():
    return {'title':'Plan', 'summary':'REF-DELTA-17 2026-12-08 57000 KRW', 'key_facts':[
        {'text':'REF-DELTA-17','block_id':'s:1:sp:2:p:2','start':6,'end':18},
        {'text':'2026-12-08','block_id':'s:1:sp:2:p:3','start':6,'end':16},
        {'text':'57000','block_id':'s:2:tb:3:r:1:c:2:p:1','start':0,'end':5},
        {'text':'KRW','block_id':'s:2:tb:3:r:2:c:2:p:1','start':0,'end':3}],
        'block_coverage':['s:1:sp:2:p:2','s:1:sp:2:p:3','s:2:tb:3:r:1:c:1:p:1','s:2:tb:3:r:1:c:2:p:1','s:2:tb:3:r:2:c:1:p:1','s:2:tb:3:r:2:c:2:p:1']}

def ready_pptx_result():
    transport=Transport(); transport.value=native_value()
    return OllamaPptxDocumentAdapter(transport).understand(
        StoredSourceDocument('source-cp3','source-version-cp3','qa.pptx',original(),MIME),
        ResolvedModel('local','OLLAMA','fixture-model','text_generation','https://models.example.com',0,1,1,'server_internal','provider',True,None),
        DocumentProcessingContext('tenant-cp3','workspace-cp3','actor-cp3','trace-cp3','policy-v1'))

class PptxUnderstandingTests(unittest.TestCase):
    def setUp(self):
        self.context=DocumentProcessingContext('tenant-cp3','workspace-cp3','actor-cp3','trace-cp3','policy-v1')
        self.document=StoredSourceDocument('source-cp3','source-version-cp3','qa.pptx',original(),MIME)
        self.model=ResolvedModel('local','OLLAMA','fixture-model','text_generation','https://models.example.com',0,1,1,'server_internal','provider',True,None)
        self.transport=Transport(); self.transport.value=native_value()

    def test_native_then_original_validation_no_parser_only_ready(self):
        def reread(content):
            self.transport.events.append('original-read')
            return read_pptx(content)
        def validate(document,value):
            self.assertIn('native',self.transport.events)
            self.transport.events.append('validate')
            return read_pptx_block_view(document,value)
        with patch('daon_user_api.pptx_document_understanding.read_pptx_block_view',side_effect=validate), patch('daon_user_api.pptx_document_understanding.read_pptx',side_effect=reread):
            result=OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context)
        self.assertEqual(result.status,'ready')
        self.assertEqual(self.transport.events[:6],['original-read','tags','show','tags','native','original-read'])
        payload=self.transport.requests[3]['payload']
        self.assertEqual(payload['messages'][1]['content'],read_pptx(self.document.content).native_json)
        self.assertEqual((self.transport.requests[3]['timeout_seconds'],payload['options']), (90,{'temperature':0,'num_predict':512}))
        self.assertIs(payload['think'],False)
        self.assertEqual((result.parser.pages,result.parser.page_texts,result.parser.regions,result.parser.cell_evidence,result.parser.block_evidence),((),(),(),(),()))
        self.assertEqual(result.parser.slide_evidence[0],{'text':'REF-DELTA-17','block_id':'s:1:sp:2:p:2','locator_value':'{"block":"s:1:sp:2:p:2","start":6,"end":18}'})
        choices=payload['format']['properties']['key_facts']['items']['anyOf']
        self.assertEqual([c['properties']['block_id']['const'] for c in choices],native_value()['block_coverage'])
        validate_slide_evidence(result.parser.slide_evidence,result.semantic.key_facts)

    def test_invalid_locations_units_and_summary_never_indexable(self):
        for key,value in [('text','USD'),('block_id','s:1:sp:99:p:2'),('start',7),('end',19)]:
            with self.subTest(key=key):
                self.transport.value=native_value();self.transport.value['key_facts'][0][key]=value
                result=OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context)
                self.assertEqual((result.status,result.parser.slide_evidence),('needs_review',()))
        self.transport.value=native_value();self.transport.value['summary']='99999 USD'
        self.assertEqual(OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context).status,'needs_review')

    def test_strict_coverage_types_and_keys(self):
        for key,value in [('block_coverage',[]),('block_coverage',[True]),('key_facts',[]),('title',{}),('summary',None),('extra',0)]:
            with self.subTest(key=key):
                self.transport.value=native_value();self.transport.value[key]=value
                with self.assertRaises(DocumentUnderstandingError):OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context)
        self.transport.value=native_value(); self.transport.value['key_facts'][0]['start']=True
        with self.assertRaises(DocumentUnderstandingError):OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context)

    def test_metadata_failures_prevent_native_and_nonthinking_omits_flag(self):
        for show in ({},{'capabilities':['completion'],'remote_host':'remote'},{'capabilities':['vision']}, {'capabilities':['completion'],'model_info':{'tokenizer.ggml.add_eos_token':'credential'}}):
            with self.subTest(show=show):
                self.transport=Transport();self.transport.show=show
                with self.assertRaises(DocumentUnderstandingError):OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context)
                self.assertNotIn('native',self.transport.events)
        self.transport=Transport();self.transport.value=native_value();self.transport.show['capabilities']=['completion']
        self.assertEqual(OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context).status,'ready')
        self.assertNotIn('think',self.transport.requests[3]['payload'])
        with patch('daon_user_api.question_answering.time.monotonic',side_effect=[100,100,103,105]):
            with self.assertRaises(DocumentUnderstandingError):OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context)

    def test_format_and_empty_original_fail_before_native(self):
        self.assertTrue(pptx_source_format('QA.PPTX',MIME))
        self.assertFalse(pptx_source_format('qa.pptx','text/plain'))
        for doc in (replace(self.document,content=b'bad'), replace(self.document,content=zip_bytes(pptx_parts([slide('')])))):
            with self.assertRaises(DocumentUnderstandingError):OllamaPptxDocumentAdapter(self.transport).understand(doc,self.model,self.context)
            self.assertEqual(self.transport.events,[])

    def test_raw_native_json_utf8_limit_is_checked_before_parsing(self):
        # JSON whitespace pads a valid response to the exact byte boundary.
        # Multibyte text ensures this is bytes, not Python character length.
        value = native_value(); value['title'] = '계획'
        raw = json.dumps(value,ensure_ascii=False)
        raw += ' ' * (32768 - len(raw.encode('utf-8')))
        self.assertEqual(len(raw.encode('utf-8')),32768)
        self.transport.value=raw
        self.assertEqual(OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context).status,'ready')
        self.transport.value=raw+' '
        with patch('daon_user_api.pptx_document_understanding.json.loads',side_effect=AssertionError('oversized response must not be parsed')):
            with self.assertRaisesRegex(DocumentUnderstandingError,'UNDERSTANDING_PROVIDER_RESPONSE_INVALID'):
                OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context)

    def test_prompt_example_has_one_canonical_pptx_block_identity(self):
        OllamaPptxDocumentAdapter(self.transport).understand(self.document,self.model,self.context)
        prompt=self.transport.requests[3]['payload']['messages'][0]['content']
        example=prompt.split('For example, block ',1)[1].split('If separate table blocks',1)[0]
        original_id=example.split(' text ',1)[0]
        generated=json.loads(example.split(' gives ',1)[1].split('. ',1)[0])
        self.assertEqual(original_id,'s:1:sp:2:p:7')
        self.assertEqual(generated,{'text':'Memo NOVA-73','block_id':original_id,'start':0,'end':12})
