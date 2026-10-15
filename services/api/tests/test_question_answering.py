from __future__ import annotations

import json
import io
import unittest
from copy import deepcopy
from unittest.mock import patch

from daon_user_api.document_index_postgres import IndexedEvidenceChunk
from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingError, UrlLibDocumentUnderstandingTransport,
)
from daon_user_api.provider_settings import (
    ModelDeploymentView,
    ProviderProfileView,
    ProviderSettingsSnapshot,
)
from daon_user_api.question_answering import (
    AnthropicMessagesTextGenerationAdapter,
    GeneralConversationRequest,
    GroundedQuestionRequest,
    OpenAICompatibleTextGenerationAdapter,
    OllamaTextGenerationAdapter,
    TextModelSelection,
    UpstageTextGenerationAdapter,
    classify_question_intent,
    resolve_text_model_selection,
)


def text_snapshot() -> ProviderSettingsSnapshot:
    return ProviderSettingsSnapshot(
        workspace_id="workspace-cp3",
        profiles=(ProviderProfileView(
            "provider-upstage", "UPSTAGE", "external_api",
            "https://api.upstage.ai/v1", True, True, 2,
        ),),
        deployments=(ModelDeploymentView(
            "deployment-text", "provider-upstage", "UPSTAGE",
            "solar-pro4", ("text",), True, True, 3,
        ),),
        role_bindings={"text": "deployment-text"},
        binding_version=5,
    )


def external_text_snapshot(provider_code: str, base_url: str) -> ProviderSettingsSnapshot:
    return ProviderSettingsSnapshot(
        workspace_id="workspace-cp3",
        profiles=(ProviderProfileView(
            f"provider-{provider_code.lower()}", provider_code, "external_api",
            base_url, True, True, 2,
        ),),
        deployments=(ModelDeploymentView(
            "deployment-text", f"provider-{provider_code.lower()}", provider_code,
            "selected-model", ("text",), True, True, 3,
        ),),
        role_bindings={"text": "deployment-text"}, binding_version=5,
    )


class CanonicalTextQaThinkingTests(unittest.TestCase):
    selection = staticmethod(lambda: WebsiteQaThinkingTests.selection())

    @staticmethod
    def request(count=1):
        # Canonical index IDs for SV a*32, fact 'Example Domain', page 1,
        # original semantic ordinals 1..5 (retrieval order need not match).
        ids = (
            ('chunk-7b20a832eeab13734ede71bae272be21', 'span-4aa4328bad428e9ae69e0b653a2be282'),
            ('chunk-5f10733dfd9c5d9c0d4c89958b2a2339', 'span-535339958514b5964be84174e59ab6ef'),
            ('chunk-fc7c124f4deab85a92b4b501dad1909f', 'span-3182090e49d8de2c3fbe0755e7fa7992'),
            ('chunk-482bdc89bd8095ae434817af299df6da', 'span-96450aad80daa61397ad4d863d0b494b'),
            ('chunk-8fe8d3ed387d50b604daa65f50171142', 'span-04ef6aceaa24adfc17ad2e1150698e3a'),
        )
        return GroundedQuestionRequest('What is the domain title?', tuple(
            IndexedEvidenceChunk(chunk, 'src-'+'a'*32, 'sv-'+'a'*32,
                1, 'Example Domain', span, 0.0, 'section', None)
            for chunk, span in ids[:count]), 'trace-text')

    def test_canonical_text_thinking_budget_only_changes_think_and_retains_grounding(self):
        for count in (1, 5):
            with self.subTest(count=count):
                transport = WebsiteQaThinkingTests.Transport()
                adapter = OllamaTextGenerationAdapter(transport=transport)
                request, selection = self.request(count), self.selection()
                baseline = adapter.provider_payload(request, selection)
                saved = deepcopy(baseline)
                with self.assertRaisesRegex(ValueError, '^TEXT_GENERATION_RESPONSE_INVALID$'):
                    adapter.generate(request, selection, provider_payload=baseline)
                prepared = adapter.prepare_qa_structured_payload(request, selection, baseline)
                self.assertEqual(prepared, {**saved, 'think': False})
                self.assertEqual(baseline, saved)
                self.assertEqual(adapter.generate(request, selection, provider_payload=prepared).answer, 'Example Domain')
                self.assertEqual(prepared['options']['num_predict'], 512)
                self.assertIsNone(transport.calls[-1]['timeout_seconds'])

    def test_arbitrary_forged_mixed_and_empty_sections_never_request_metadata(self):
        from dataclasses import replace
        item = self.request().evidence[0]
        cases = [(), (replace(item, source_id='source-text'),),
            (replace(item, source_version_id='sv-'+'b'*32),),
            (replace(item, chunk_id='chunk-'+'b'*32),),
            (replace(item, evidence_span_id='span-'+'b'*32),),
            (replace(item, text='changed'),), (replace(item, page=2),),
            (replace(item, locator_value='arbitrary'),), (replace(item, locator_kind='page'),),
            (item, replace(item, source_id='src-'+'b'*32)),
            self.request(5).evidence + (item,),
        ]
        for evidence in cases:
            with self.subTest(evidence=evidence):
                transport = WebsiteQaThinkingTests.Transport()
                adapter = OllamaTextGenerationAdapter(transport=transport)
                if not evidence:
                    with self.assertRaisesRegex(ValueError, '^QUESTION_REQUEST_INVALID$'):
                        GroundedQuestionRequest('title?', evidence, 'trace-text')
                    self.assertEqual(transport.calls, [])
                    continue
                request = GroundedQuestionRequest('title?', evidence, 'trace-text')
                payload = adapter.provider_payload(request, self.selection())
                self.assertIs(adapter.prepare_qa_structured_payload(request, self.selection(), payload), payload)
                self.assertEqual(transport.calls, [])

    def test_text_metadata_fail_closed_and_nonthinking_or_other_provider_unchanged(self):
        from dataclasses import replace
        request, selection = self.request(), self.selection()
        for invalid in ('digest', 'remote', 'model', 'metadata'):
            with self.subTest(invalid=invalid):
                transport = WebsiteQaThinkingTests.Transport(invalid=invalid)
                adapter = OllamaTextGenerationAdapter(transport=transport)
                with self.assertRaisesRegex(ValueError, '^TEXT_GENERATION_RESPONSE_INVALID$'):
                    adapter.prepare_qa_structured_payload(request, selection, adapter.provider_payload(request, selection))
                self.assertFalse(any(c['url'].endswith('/api/chat') for c in transport.calls))
        for target in (selection, replace(selection, provider_kind='external_api'), replace(selection, provider_code='CUSTOM')):
            transport = WebsiteQaThinkingTests.Transport(thinking=False)
            adapter = OllamaTextGenerationAdapter(transport=transport)
            payload = adapter.provider_payload(request, target)
            self.assertIs(adapter.prepare_qa_structured_payload(request, target, payload), payload)
            if target != selection: self.assertEqual(transport.calls, [])

    def test_text_service_exact_workspace_and_general_gate(self):
        from daon_user_api.question_answering_service import QuestionAnsweringService, QuestionAdapterRegistry
        from daon_user_api.question_answering_postgres import QuestionContext
        for target, workspace in (('workspace-qa','workspace-other'), ('','workspace-qa'), ('workspace-qa','workspace-qa')):
            for general in (False, True):
                with self.subTest(target=target, workspace=workspace, general=general):
                    transport = WebsiteQaThinkingTests.Transport()
                    adapter = OllamaTextGenerationAdapter(transport=transport)
                    request = GeneralConversationRequest('hello', 'trace-text') if general else self.request()
                    payload = adapter.general_provider_payload(request,self.selection()) if general else adapter.provider_payload(request,self.selection())
                    prepared = QuestionAdapterRegistry.Prepared(request,self.selection(),adapter,payload)
                    service = object.__new__(QuestionAnsweringService)
                    service._qa_structured_ollama_workspace_id = target
                    result = service._prepare_qa_structured_payload(QuestionContext('tenant',workspace,'actor','trace','policy','notebook'), prepared)
                    if target and target == workspace and not general:
                        self.assertEqual(result.provider_payload, {**payload, 'think':False})
                    else:
                        self.assertIs(result, prepared); self.assertEqual(transport.calls, [])

    def test_filtered_last_ordinal_keeps_canonical_identity_and_strict_response_checks(self):
        from dataclasses import replace
        request = replace(self.request(5), evidence=(self.request(5).evidence[-1],))
        for answer, cited in (('Example Domain', [request.evidence[0].chunk_id]),
                              ('Invented SKU-777', [request.evidence[0].chunk_id]),
                              ('Example Domain', ['invented-chunk'])):
            with self.subTest(answer=answer, cited=cited):
                transport = WebsiteQaThinkingTests.Transport()
                original = transport.post_json_no_auth
                def response(**kwargs):
                    if kwargs['url'].endswith('/api/show'): return original(**kwargs)
                    return {'message': {'content': json.dumps({'answer':answer, 'cited_chunk_ids':cited, 'insufficient':False})}}
                transport.post_json_no_auth = response
                adapter = OllamaTextGenerationAdapter(transport=transport)
                payload = adapter.prepare_qa_structured_payload(request, self.selection(), adapter.provider_payload(request,self.selection()))
                self.assertIs(payload.get('think'), False)
                if answer == 'Example Domain' and cited == [request.evidence[0].chunk_id]:
                    self.assertEqual(adapter.generate(request,self.selection(),provider_payload=payload).answer, answer)
                else:
                    with self.assertRaisesRegex(ValueError, '^TEXT_GENERATION_GROUNDING_INVALID$'):
                        adapter.generate(request,self.selection(),provider_payload=payload)


class OllamaQuestionWaitTests(unittest.TestCase):
    def test_internal_ollama_default_and_explicit_none_wait_without_changing_grounding(self):
        request=GroundedQuestionRequest('title?',(IndexedEvidenceChunk('chunk','source','version',1,'Example Domain','span'),),'trace')
        for options,want in (({},None),({'timeout_seconds':None},None),({'timeout_seconds':7.5},7.5)):
            with self.subTest(options=options):
                calls=[]
                class Transport:
                    def post_json_no_auth(self,**kwargs):
                        calls.append(kwargs)
                        return {'message':{'content':json.dumps({'answer':'Example Domain','cited_chunk_ids':['chunk'],'insufficient':False})}}
                adapter=OllamaTextGenerationAdapter(transport=Transport(),**options)
                selection=TextModelSelection('OLLAMA','http://models.example.com','profile','deployment','fixture',1,'server_internal')
                self.assertEqual(adapter.generate(request,selection).answer,'Example Domain')
                self.assertEqual(calls[0]['timeout_seconds'],want)
                self.assertEqual(calls[0]['payload']['options']['num_predict'],512)

    def test_ollama_invalid_waits_rejected_and_external_ollama_keeps_finite_default(self):
        for value in (True,False,0,-1,'90',float('inf'),float('nan'),121):
            with self.subTest(value=value),self.assertRaises(ValueError):
                OllamaTextGenerationAdapter(transport=object(),timeout_seconds=value)
        calls=[]
        class Transport:
            def post_json_no_auth(self,**kwargs):
                calls.append(kwargs);return {'message':{'content':'{"answer":"hello"}'}}
        selection=TextModelSelection('OLLAMA','https://models.example.com','profile','deployment','fixture',1,'external_api')
        OllamaTextGenerationAdapter(transport=Transport()).generate_general(GeneralConversationRequest('hello','trace'),selection)
        self.assertEqual(calls[0]['timeout_seconds'],90.0)


class WebsiteQaThinkingTests(unittest.TestCase):
    @staticmethod
    def request(count=1):
        return GroundedQuestionRequest('What is the domain title?',tuple(
            IndexedEvidenceChunk('web-chunk-'+f'{index:032x}','web-source-'+'a'*32,'web-version-'+'a'*32,
                1,'Example Domain','web-span-'+f'{index:032x}',0.0,'section',None)
            for index in range(count)), 'trace-web')

    @staticmethod
    def selection():
        return TextModelSelection('OLLAMA','http://models.example.com','profile','deployment','fixture-installed',4,'server_internal')

    class Transport:
        def __init__(self,thinking=True,invalid=None):self.calls=[];self.thinking=thinking;self.invalid=invalid
        def get_json_no_auth(self,**kwargs):
            self.calls.append(kwargs)
            row={'name':'fixture-installed','digest':'a'*64}
            if self.invalid=='digest' and len(self.calls)>1:row['digest']='b'*64
            if self.invalid=='remote':row['remote_host']='cloud.example'
            if self.invalid=='model':row['name']='other'
            return {'models':[row]}
        def post_json_no_auth(self,**kwargs):
            self.calls.append(kwargs)
            if kwargs['url'].endswith('/api/show'):
                return {'capabilities':['completion']+(['thinking'] if self.thinking else []),
                    'model_info':{} if self.invalid=='metadata' else {'architecture':'fixture'}}
            if kwargs['payload'].get('think') is not False:
                return {'message':{'content':'','thinking':'not an answer'},'done_reason':'length','eval_count':512}
            chunk=json.loads(kwargs['payload']['messages'][1]['content'])['evidence'][0]['chunk_id']
            return {'message':{'content':json.dumps({'answer':'Example Domain','cited_chunk_ids':[chunk],'insufficient':False})},'eval_count':8}

    def test_web_sections_verified_thinking_only_changes_think_and_yields_grounded_content(self):
        for count in (1,5):
            with self.subTest(count=count):
                request=self.request(count);selection=self.selection();transport=self.Transport()
                adapter=OllamaTextGenerationAdapter(transport=transport)
                baseline=adapter.provider_payload(request,selection);saved=deepcopy(baseline)
                prepared=adapter.prepare_qa_structured_payload(request,selection,baseline)
                self.assertIs(prepared.get('think'),False)
                self.assertEqual(prepared,{**saved,'think':False});self.assertEqual(baseline,saved)
                self.assertEqual(adapter.generate(request,selection,provider_payload=prepared).answer,'Example Domain')
                self.assertEqual([call['url'].rsplit('/',1)[-1] for call in transport.calls],['tags','show','tags','chat'])
                self.assertIsNone(transport.calls[-1]['timeout_seconds'])
                self.assertEqual(prepared['options']['num_predict'],512)

    def test_nonweb_mixed_version_external_and_six_sections_keep_original_without_metadata(self):
        from dataclasses import replace
        item=self.request().evidence[0]
        cases=[(replace(item,source_id='source-txt',source_version_id='version-txt'),),
            (replace(item,source_id='web-source-lookalike'),),
            (replace(item,source_version_id='web-version-'+'b'*32),),
            (replace(item,chunk_id='ordinary-chunk'),),
            (replace(item,evidence_span_id='ordinary-span'),),
            (replace(item,locator_kind='page'),),
            (item,replace(item,source_version_id='web-version-'+'b'*32)),self.request(6).evidence]
        for evidence in cases:
            with self.subTest(evidence=evidence):
                transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport)
                request=GroundedQuestionRequest('title?',evidence,'trace-web')
                payload=adapter.provider_payload(request,self.selection())
                self.assertIs(adapter.prepare_qa_structured_payload(request,self.selection(),payload),payload)
                self.assertEqual(transport.calls,[])
        external=replace(self.selection(),provider_kind='external_api')
        transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport)
        payload=adapter.provider_payload(self.request(),external)
        self.assertIs(adapter.prepare_qa_structured_payload(self.request(),external,payload),payload)
        self.assertEqual(transport.calls,[])

    def test_web_nonthinking_payload_unchanged_and_bad_metadata_or_thinking_content_not_used(self):
        request=self.request();selection=self.selection()
        transport=self.Transport(thinking=False);adapter=OllamaTextGenerationAdapter(transport=transport)
        payload=adapter.provider_payload(request,selection)
        self.assertIs(adapter.prepare_qa_structured_payload(request,selection,payload),payload)
        for invalid in ('digest','remote','model','metadata'):
            with self.subTest(invalid=invalid):
                transport=self.Transport(invalid=invalid);adapter=OllamaTextGenerationAdapter(transport=transport)
                with self.assertRaisesRegex(ValueError,'^TEXT_GENERATION_RESPONSE_INVALID$'):
                    adapter.prepare_qa_structured_payload(request,selection,payload)
                self.assertFalse(any(call['url'].endswith('/api/chat') for call in transport.calls))
        with self.assertRaisesRegex(ValueError,'^TEXT_GENERATION_RESPONSE_INVALID$'):
            OllamaTextGenerationAdapter(transport=self.Transport()).generate(request,selection,provider_payload=payload)

    def test_existing_service_exact_workspace_gate_is_preserved(self):
        from daon_user_api.question_answering_service import QuestionAnsweringService,QuestionAdapterRegistry
        from daon_user_api.question_answering_postgres import QuestionContext
        for target,workspace in (('workspace-qa','workspace-other'),('','workspace-qa'),('workspace-qa','workspace-qa')):
            with self.subTest(target=target,workspace=workspace):
                transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport)
                request=self.request();selection=self.selection();payload=adapter.provider_payload(request,selection)
                prepared=QuestionAdapterRegistry.Prepared(request,selection,adapter,payload)
                service=object.__new__(QuestionAnsweringService);service._qa_structured_ollama_workspace_id=target
                context=QuestionContext('tenant',workspace,'actor','trace','policy','notebook')
                result=service._prepare_qa_structured_payload(context,prepared)
                if target and target==workspace:self.assertIs(result.provider_payload.get('think'),False)
                else:self.assertIs(result,prepared);self.assertEqual(transport.calls,[])

    def test_web_metadata_keeps_one_total_five_second_budget(self):
        transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport)
        request=self.request();selection=self.selection();payload=adapter.provider_payload(request,selection)
        with patch('daon_user_api.question_answering.time.monotonic',side_effect=[100,100,103,105]):
            with self.assertRaisesRegex(ValueError,'^TEXT_GENERATION_RESPONSE_INVALID$'):
                adapter.prepare_qa_structured_payload(request,selection,payload)
        self.assertEqual([call['timeout_seconds'] for call in transport.calls],[5,2])
        self.assertFalse(any(call['url'].endswith('/api/chat') for call in transport.calls))

    def test_web_response_keeps_strict_grounding_rejection(self):
        request=self.request();selection=self.selection()
        for answer,cited in (('Invented SKU-777',[request.evidence[0].chunk_id]),('Example Domain',['invented-chunk'])):
            with self.subTest(answer=answer,cited=cited):
                transport=self.Transport();original=transport.post_json_no_auth
                def response(**kwargs):
                    if kwargs['url'].endswith('/api/show'):return original(**kwargs)
                    transport.calls.append(kwargs)
                    return {'message':{'content':json.dumps({'answer':answer,'cited_chunk_ids':cited,'insufficient':False})}}
                transport.post_json_no_auth=response
                adapter=OllamaTextGenerationAdapter(transport=transport)
                payload=adapter.prepare_qa_structured_payload(request,selection,adapter.provider_payload(request,selection))
                with self.assertRaisesRegex(ValueError,'^TEXT_GENERATION_GROUNDING_INVALID$'):
                    adapter.generate(request,selection,provider_payload=payload)


class OpenLawQaThinkingTests(unittest.TestCase):
    Transport=WebsiteQaThinkingTests.Transport
    selection=staticmethod(WebsiteQaThinkingTests.selection)

    @staticmethod
    def request(count=1,start=0):
        from daon_user_api.open_law_source_postgres import law_chunks
        chunks=law_chunks('law-source-'+'a'*32,'law-version-'+'a'*32,
            ' '*start+'Example Domain'.ljust(1024)*count)
        # Same projection as PostgresDocumentIndex.search for stored verbatim facts.
        return GroundedQuestionRequest('What is the domain title?',tuple(
            IndexedEvidenceChunk(row['chunk_id'],row['source_id'],row['source_version_id'],row['page'],
                row['text'],row['evidence_span_id'],0.0,'section',None) for row in chunks),'trace-law')

    def test_law_verified_thinking_only_changes_think_for_real_canonical_chunks(self):
        from dataclasses import replace
        for count,start,kind in ((1,0,'server_internal'),(5,0,'local'),(1,1024,'local_runtime'),(1,523264,'server_internal')):
            with self.subTest(count=count,start=start,kind=kind):
                request=self.request(count,start);selection=replace(self.selection(),provider_kind=kind)
                transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport)
                baseline=adapter.provider_payload(request,selection);saved=deepcopy(baseline)
                prepared=adapter.prepare_qa_structured_payload(request,selection,baseline)
                self.assertEqual(prepared,{**saved,'think':False});self.assertEqual(baseline,saved)
                self.assertEqual(adapter.generate(request,selection,provider_payload=prepared).answer,'Example Domain')
                self.assertEqual([call['url'].rsplit('/',1)[-1] for call in transport.calls],['tags','show','tags','chat'])
                self.assertIsNone(transport.calls[-1]['timeout_seconds']);self.assertEqual(prepared['options']['num_predict'],512)

    def test_law_contaminated_ids_spans_offsets_or_mixed_sources_never_read_metadata(self):
        import hashlib
        from dataclasses import replace
        item=self.request().evidence[0]
        with self.assertRaisesRegex(ValueError,'^QUESTION_REQUEST_INVALID$'):
            GroundedQuestionRequest('title?',(),'trace-law')
        cases=[self.request(6).evidence,
            (replace(item,source_id='law-source-lookalike'),),(replace(item,source_version_id='law-version-'+'b'*32),),
            (replace(item,chunk_id='law-chunk-'+'b'*32),),(replace(item,evidence_span_id='law-span-'+'b'*32),),
            (replace(item,page=2),),(replace(item,page=True),),(replace(item,locator_value='section'),),
            (replace(item,locator_kind='page'),),(replace(item,text=' '*1024),),(replace(item,text='x'*1025),),
            (item,replace(item,source_id='source-other',source_version_id='version-other'))]
        for start in (1,524288):
            suffix=hashlib.sha256(f'{item.source_version_id}|{start}'.encode()).hexdigest()[:32]
            cases.append((replace(item,chunk_id='law-chunk-'+suffix,evidence_span_id='law-span-'+suffix),))
        for index,evidence in enumerate(cases):
            with self.subTest(case=index):
                transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport)
                request=GroundedQuestionRequest('title?',evidence,'trace-law');payload=adapter.provider_payload(request,self.selection())
                self.assertIs(adapter.prepare_qa_structured_payload(request,self.selection(),payload),payload)
                self.assertEqual(transport.calls,[])

    def test_law_nonthinking_and_other_provider_unchanged_bad_metadata_never_chats(self):
        from dataclasses import replace
        request=self.request();selection=self.selection()
        for other in (replace(selection,provider_kind='external_api'),replace(selection,provider_code='CUSTOM')):
            transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport);payload={}
            self.assertIs(adapter.prepare_qa_structured_payload(request,other,payload),payload);self.assertEqual(transport.calls,[])
        transport=self.Transport(thinking=False);adapter=OllamaTextGenerationAdapter(transport=transport)
        payload=adapter.provider_payload(request,selection)
        self.assertIs(adapter.prepare_qa_structured_payload(request,selection,payload),payload)
        for invalid in ('digest','remote','model','metadata','completion'):
            with self.subTest(invalid=invalid):
                transport=self.Transport(invalid=invalid);adapter=OllamaTextGenerationAdapter(transport=transport)
                if invalid=='completion':
                    original=transport.post_json_no_auth
                    transport.post_json_no_auth=lambda **kw:{'capabilities':['thinking'],'model_info':{'architecture':'fixture'}} if kw['url'].endswith('/api/show') else original(**kw)
                with self.assertRaisesRegex(ValueError,'^TEXT_GENERATION_RESPONSE_INVALID$'):
                    adapter.prepare_qa_structured_payload(request,selection,payload)
                self.assertFalse(any(call['url'].endswith('/api/chat') for call in transport.calls))

    def test_law_existing_exact_workspace_gate_and_total_metadata_budget(self):
        from daon_user_api.question_answering_service import QuestionAnsweringService,QuestionAdapterRegistry
        from daon_user_api.question_answering_postgres import QuestionContext
        for target,workspace in (('workspace-qa','workspace-other'),('','workspace-qa'),('workspace-qa','workspace-qa')):
            with self.subTest(target=target,workspace=workspace):
                transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport);request=self.request();selection=self.selection()
                payload=adapter.provider_payload(request,selection);prepared=QuestionAdapterRegistry.Prepared(request,selection,adapter,payload)
                service=object.__new__(QuestionAnsweringService);service._qa_structured_ollama_workspace_id=target
                result=service._prepare_qa_structured_payload(QuestionContext('tenant',workspace,'actor','trace','policy','notebook'),prepared)
                if target and target==workspace:self.assertIs(result.provider_payload.get('think'),False)
                else:self.assertIs(result,prepared);self.assertEqual(transport.calls,[])
        transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport);selection=self.selection()
        request=GeneralConversationRequest('일반 업무 상담','trace-law')
        prepared=QuestionAdapterRegistry.Prepared(request,selection,adapter,adapter.general_provider_payload(request,selection))
        service=object.__new__(QuestionAnsweringService);service._qa_structured_ollama_workspace_id='workspace-qa'
        self.assertIs(service._prepare_qa_structured_payload(QuestionContext('tenant','workspace-qa','actor','trace','policy','notebook'),prepared),prepared)
        self.assertEqual(transport.calls,[])
        transport=self.Transport();adapter=OllamaTextGenerationAdapter(transport=transport);request=self.request()
        with patch('daon_user_api.question_answering.time.monotonic',side_effect=[100,100,103,105]):
            with self.assertRaisesRegex(ValueError,'^TEXT_GENERATION_RESPONSE_INVALID$'):
                adapter.prepare_qa_structured_payload(request,self.selection(),adapter.provider_payload(request,self.selection()))
        self.assertEqual([call['timeout_seconds'] for call in transport.calls],[5,2])


class AudioQuestionGroundingTests(unittest.TestCase):
    def test_audio_prompt_uses_time_locator_and_not_fake_page(self):
        chunk = IndexedEvidenceChunk('audio', 'source', 'version', 1, 'ABC', 'span', 0.0,
            'audio_segment', '{"segment":"s:1","start_ms":1000,"end_ms":2000,"start":0,"end":3}')
        selection = TextModelSelection('OLLAMA', 'https://models.example.com', 'profile', 'deployment', 'fixture-model', 1, 'server_internal')
        request = GroundedQuestionRequest('read evidence', (chunk,), 'trace')
        payload = OllamaTextGenerationAdapter.provider_payload(request, selection)
        evidence = json.loads(payload['messages'][1]['content'])['evidence']
        self.assertEqual(evidence, [{'chunk_id': 'audio', 'text': 'ABC', 'locator': {'segment': 's:1', 'start_ms': 1000, 'end_ms': 2000, 'start': 0, 'end': 3}}])
        from test_docx_document_understanding import Transport
        prepared = OllamaTextGenerationAdapter(transport=Transport()).prepare_qa_structured_payload(request, selection, payload)
        branches = prepared['format']['anyOf']
        self.assertEqual(branches[0]['properties']['cited_chunk_ids']['const'], ['audio'])
        self.assertEqual(branches[1]['properties']['cited_chunk_ids']['const'], [])
        self.assertEqual(branches[1]['properties']['answer']['const'], '선택한 원문 근거에 질문에 답할 정보가 없습니다.')

    def test_bad_audio_locator_is_rejected_before_http(self):
        selection = TextModelSelection('OLLAMA', 'https://models.example.com', 'profile', 'deployment', 'fixture', 1)
        for value in ('{"segment":"s:1","start_ms":true,"end_ms":2000,"start":0,"end":3}',
                      '{"segment":"s:1","start_ms":1000,"end_ms":2000,"start":0,"end":4}'):
            chunk = IndexedEvidenceChunk('audio', 'source', 'version', 1, 'ABC', 'span', 0.0, 'audio_segment', value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'TEXT_GENERATION_GROUNDING_INVALID'):
                OllamaTextGenerationAdapter.provider_payload(GroundedQuestionRequest('read', (chunk,), 'trace'), selection)


class PptxQuestionGroundingTests(unittest.TestCase):
    def test_base_pptx_and_mixed_evidence_use_original_locator_not_fake_page(self):
        pptx = IndexedEvidenceChunk('pptx','source','version',1,'ABC','span',0.0,
            'pptx_block','{"block":"s:2:sp:3:p:1","start":0,"end":3}')
        selection = TextModelSelection('OLLAMA','https://models.example.com','profile','deployment','fixture',1)
        for others in ((), tuple(IndexedEvidenceChunk(kind,'source','version',7,'old text','span-'+kind,0.0,kind,value)
            for kind,value in [('page','7'),('section','section'),('image_region','0,0,1,1,1,1'),('sheet_cell','{"sheet":"Plan","range":"A1"}')])):
            with self.subTest(mixed=bool(others)):
                request = GroundedQuestionRequest('read evidence',(pptx,*others),'trace')
                payload = OllamaTextGenerationAdapter.provider_payload(request,selection)
                supplied = json.loads(payload['messages'][1]['content'])['evidence']
                self.assertEqual(supplied[0],{'chunk_id':'pptx','text':'ABC','locator':{'block':'s:2:sp:3:p:1','start':0,'end':3}})
                self.assertEqual(supplied[1:],[{'chunk_id':c.chunk_id,'page':7,'text':'old text'} for c in others])
                self.assertNotIn('think',payload)
                if others:
                    class NoMetadata:
                        def get_json_no_auth(self,**kwargs):raise AssertionError('mixed must not call metadata')
                        def post_json_no_auth(self,**kwargs):raise AssertionError('mixed must not call metadata')
                    prepared = OllamaTextGenerationAdapter(transport=NoMetadata()).prepare_qa_structured_payload(request,selection,payload)
                    self.assertEqual(prepared,payload)

    def test_invalid_base_pptx_locator_fails_before_any_http(self):
        selection = TextModelSelection('OLLAMA','https://models.example.com','profile','deployment','fixture',1)
        for value in ('{"block":"s:2:sp:3:p:1","start":true,"end":3}', '{"block":"s:2:sp:3:p:1","start":0,"end":4}'):
            chunk = IndexedEvidenceChunk('pptx','source','version',1,'ABC','span',0.0,'pptx_block',value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError,'TEXT_GENERATION_GROUNDING_INVALID'):
                OllamaTextGenerationAdapter.provider_payload(GroundedQuestionRequest('read',(chunk,),'trace'),selection)

    def test_pptx_currency_changes_are_rejected_by_real_adapter(self):
        chunk = IndexedEvidenceChunk('c1','source','version',1,'57000 KRW','span',0.0,
            'pptx_block','{"block":"s:1:sp:2:p:1","start":0,"end":9}')
        request = GroundedQuestionRequest('amount and currency?',(chunk,),'trace')
        selection = TextModelSelection('OLLAMA','https://models.example.com','profile','deployment','fixture',1)
        class Transport:
            answer='57000USD'
            def post_json_no_auth(self, **kwargs):
                return {'message':{'content':json.dumps({'answer':self.answer,'cited_chunk_ids':['c1'],'insufficient':False})}}
        transport=Transport();adapter=OllamaTextGenerationAdapter(transport=transport)
        for answer in ('57000 USD','57000 EUR','57000$','57000 GBP'):
            transport.answer=answer
            with self.subTest(answer=answer), self.assertRaisesRegex(ValueError,'TEXT_GENERATION_GROUNDING_INVALID'):
                adapter.generate(request,selection)
        for answer in ('57000 KRW','57000₩'):
            transport.answer=answer
            with self.subTest(answer=answer):self.assertEqual(adapter.generate(request,selection).answer,answer)


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post_json(self, *, url: str, api_key: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        self.calls.append((url, api_key, payload))
        return {
            "choices": [{"message": {"content": json.dumps({
                "answer": "The phrase is ORANGE-COMPASS-42.",
                "cited_chunk_ids": ["chunk-page-2"],
                "insufficient": False,
            })}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }


class QuestionAnsweringContractTests(unittest.TestCase):
    def test_qa_docx_schema_correlates_sufficient_and_insufficient_without_changing_other_wire(self):
        from daon_user_api.data_canon import canonical_json_bytes
        request = GroundedQuestionRequest('Read reference and amount', (
            IndexedEvidenceChunk('doc-ref','source-doc','version-doc',1,'REF-17','span-ref',0.0,'docx_block','{"block":"p:2","start":0,"end":6}'),
            IndexedEvidenceChunk('doc-amount','source-doc','version-doc',1,'64 EUR','span-amount',0.0,'docx_block','{"block":"p:3","start":0,"end":6}'),
        ),'trace-doc')
        selection = TextModelSelection('OLLAMA','http://models.example.com','local','local:fixture','fixture',1,'server_internal')
        calls = []
        class Transport:
            def get_json_no_auth(self, **kwargs):
                calls.append(kwargs); return {'models':[{'name':'fixture','digest':'a'*64}]}
            def post_json_no_auth(self, **kwargs):
                calls.append(kwargs)
                if kwargs['url'].endswith('/api/show'): return {'capabilities':['completion','thinking'],'model_info':{'architecture':'fixture'}}
                return {'message':{'content':json.dumps(response)}}
        adapter=OllamaTextGenerationAdapter(transport=Transport())
        baseline=adapter.provider_payload(request,selection)
        snapshot=canonical_json_bytes(baseline)
        original_schema=deepcopy(UpstageTextGenerationAdapter._SCHEMA)
        prepared=adapter.prepare_qa_structured_payload(request,selection,baseline)
        schema=json.loads(canonical_json_bytes(prepared))['format']
        self.assertEqual(set(schema), {'anyOf'})
        self.assertEqual(len(schema['anyOf']), 2)
        for branch, insufficient, ids in zip(schema['anyOf'], (False,True), (['doc-ref','doc-amount'],[]), strict=True):
            self.assertEqual(branch['type'],'object')
            self.assertIs(branch['additionalProperties'],False)
            self.assertEqual(branch['required'],['insufficient','cited_chunk_ids','answer'])
            self.assertEqual(branch['properties']['insufficient'],{'type':'boolean','const':insufficient})
            self.assertEqual(branch['properties']['cited_chunk_ids'],{'type':'array','const':ids})
            self.assertEqual(branch['properties']['answer'],
                {'type':'string','const':'선택한 원문 근거에 질문에 답할 정보가 없습니다.'}
                if insufficient else {'type':'string','minLength':1})
        self.assertEqual(list(prepared['format']['anyOf'][0]['properties']),['insufficient','cited_chunk_ids','answer'])
        # Literal branch predicates: both legitimate states, neither contradictory pairing.
        for insufficient, ids, permitted in [(False,['doc-ref','doc-amount'],True),(True,[],True),(True,['doc-ref','doc-amount'],False),(False,[],False),(False,['doc-ref'],False)]:
            matches=[b for b in schema['anyOf'] if b['properties']['insufficient']['const'] is insufficient and b['properties']['cited_chunk_ids']['const']==ids]
            self.assertEqual(bool(matches),permitted)
        self.assertEqual(canonical_json_bytes(baseline),snapshot)  # QA outside path is unchanged.
        self.assertEqual(UpstageTextGenerationAdapter._SCHEMA,original_schema)
        self.assertIn('Never return insufficient=true while answering with supplied material facts.',prepared['messages'][0]['content'])
        self.assertIn('When insufficient=true, give ONLY a lack-of-evidence explanation; never enumerate or recap numbers, dates, identifiers or quotes from either the source evidence or the question.',prepared['messages'][0]['content'])
        self.assertIn('For insufficient=true, answer must be exactly: 선택한 원문 근거에 질문에 답할 정보가 없습니다.',prepared['messages'][0]['content'])
        self.assertIn('Internal chunk_id strings, block locators and offsets are metadata, never answer text.',prepared['messages'][0]['content'])
        self.assertIn('Preserve real identifiers that occur in the original source text.',prepared['messages'][0]['content'])
        self.assertIs(prepared['think'],False)
        self.assertEqual(prepared['options'],{'num_predict':512,'temperature':0})
        for response, valid in [
            ({'answer':'REF-17; 64 EUR','cited_chunk_ids':['doc-ref','doc-amount'],'insufficient':False},True),
            ({'answer':'Insufficient evidence.','cited_chunk_ids':[],'insufficient':True},True),
            ({'answer':'선택한 원문 근거에 질문에 답할 정보가 없습니다.','cited_chunk_ids':[],'insufficient':True},True),
            ({'answer':'REF-17; 64 EUR','cited_chunk_ids':[],'insufficient':True},False),
            ({'answer':'선택한 원문 근거에 질문에 답할 정보가 없습니다.','cited_chunk_ids':[],'insufficient':False},False),
            ({'answer':'Insufficient evidence. The text includes REF-17 and 64 EUR.','cited_chunk_ids':[],'insufficient':True},False),
            ({'answer':'Unsupported 999 USD','cited_chunk_ids':['doc-ref','doc-amount'],'insufficient':False},False),
            ({'answer':'REF-17; 64 EUR','cited_chunk_ids':['made-up'],'insufficient':False},False),
        ]:
            with self.subTest(response=response):
                if valid: self.assertEqual(adapter.generate(request,selection,provider_payload=prepared).insufficient,response['insufficient'])
                else:
                    with self.assertRaisesRegex(ValueError,'TEXT_GENERATION_GROUNDING_INVALID'):
                        adapter.generate(request,selection,provider_payload=prepared)
        self.assertIsNone(calls[3]['timeout_seconds'])

    def test_docx_internal_chunk_id_echo_is_still_rejected_while_original_identifier_is_valid(self):
        chunk_id = 'chunk-a72cd9210f48b63e'
        request = GroundedQuestionRequest('Read reference', (
            IndexedEvidenceChunk(chunk_id,'source-doc','version-doc',1,'REF-17','span-ref',0.0,
                'docx_block','{"block":"p:2","start":0,"end":6}'),
        ),'trace-doc')
        selection = TextModelSelection('OLLAMA','http://models.example.com','local','local:fixture','fixture',1,'server_internal')
        class Transport:
            def post_json_no_auth(self, **kwargs):
                return {'message':{'content':json.dumps(response)}}
        adapter = OllamaTextGenerationAdapter(transport=Transport())
        for response, valid in [
            ({'answer':f'REF-17 (from {chunk_id})','cited_chunk_ids':[chunk_id],'insufficient':False},False),
            ({'answer':'REF-17','cited_chunk_ids':[chunk_id],'insufficient':False},True),
            ({'answer':'Insufficient evidence.','cited_chunk_ids':[],'insufficient':True},True),
        ]:
            with self.subTest(response=response):
                if valid:
                    result=adapter.generate(request,selection)
                    self.assertEqual(result.answer,response['answer'])
                    self.assertEqual(result.cited_chunk_ids,tuple(response['cited_chunk_ids']))
                else:
                    with self.assertRaisesRegex(ValueError,'TEXT_GENERATION_GROUNDING_INVALID'):
                        adapter.generate(request,selection)

    def test_docx_question_payload_has_original_block_offsets_not_fake_page(self):
        request = GroundedQuestionRequest('Read document values', (
            IndexedEvidenceChunk('doc-1', 'source-doc', 'version-doc', 1, 'REF-17', 'span-doc', 0.0,
                'docx_block', '{"block":"p:2","start":6,"end":12}'),
        ), 'trace-doc')
        selection = TextModelSelection('OLLAMA', 'http://models.example.com', 'local', 'local:fixture', 'fixture', 1, 'server_internal')
        payload = OllamaTextGenerationAdapter.provider_payload(request, selection)
        supplied = json.loads(payload['messages'][1]['content'])['evidence']
        self.assertEqual(supplied, [{'chunk_id': 'doc-1', 'text': 'REF-17', 'locator': {'block': 'p:2', 'start': 6, 'end': 12}}])
        self.assertEqual(payload['format']['properties']['cited_chunk_ids'], {'anyOf': [{'const': ['doc-1']}, {'const': []}]})
        for insufficient, answer, cited in ((False, 'REF-17', ['doc-1']), (True, 'Insufficient evidence.', [])):
            with self.subTest(insufficient=insufficient):
                class Transport:
                    def post_json_no_auth(self, **kwargs):
                        return {'message': {'content': json.dumps({'answer': answer, 'cited_chunk_ids': cited, 'insufficient': insufficient})}}
                result = OllamaTextGenerationAdapter(transport=Transport()).generate(request, selection)
                self.assertEqual(result.insufficient, insufficient)
        bad = GroundedQuestionRequest('Read document values', (IndexedEvidenceChunk('doc-1', 'source-doc', 'version-doc', 1,
            'REF-17', 'span-doc', 0.0, 'docx_block', '{"block":"p:2","start":true,"end":12}'),), 'trace-doc')
        with self.assertRaisesRegex(ValueError, '^TEXT_GENERATION_GROUNDING_INVALID$'):
            OllamaTextGenerationAdapter.provider_payload(bad, selection)

    def test_qa_structured_ollama_checks_installed_metadata_before_payload(self) -> None:
        request = self.cell_request()
        selection = TextModelSelection("OLLAMA", "http://models.example.com", "local", "local:fixture", "fixture", 1, "server_internal")
        for capabilities in (["completion", "thinking"], ["completion"]):
            with self.subTest(capabilities=capabilities):
                calls = []

                class Transport:
                    def get_json_no_auth(self, **kwargs):
                        calls.append(kwargs)
                        return {"models": [{"name": "fixture", "digest": "a" * 64}]}

                    def post_json_no_auth(self, **kwargs):
                        calls.append(kwargs)
                        return {"capabilities": capabilities, "model_info": {
                            "general.architecture": "fixture",
                            "tokenizer.ggml.add_eos_token": True,
                            "tokenizer.ggml.add_bos_token": False,
                            "tokenizer.ggml.add_padding_token": True,
                            "tokenizer.ggml.add_unknown_token": False,
                        }}

                adapter = OllamaTextGenerationAdapter(transport=Transport())
                original = adapter.provider_payload(request, selection)
                snapshot = deepcopy(original)
                prepared = adapter.prepare_qa_structured_payload(request, selection, original)
                expected = deepcopy(snapshot)
                if "thinking" in capabilities:
                    expected["think"] = False
                self.assertEqual(prepared, expected)
                self.assertEqual(original, snapshot)
                self.assertEqual([call["url"] for call in calls], [
                    "http://models.example.com/api/tags", "http://models.example.com/api/show",
                    "http://models.example.com/api/tags",
                ])
                self.assertEqual(calls[1]["payload"], {"model": "fixture"})
                self.assertTrue(all(0 < call["timeout_seconds"] <= 5 for call in calls))

    def test_qa_structured_ollama_invalid_metadata_prevents_chat(self) -> None:
        request = self.cell_request()
        selection = TextModelSelection("OLLAMA", "http://models.example.com", "local", "local:fixture", "fixture", 1, "server_internal")
        valid_tags = {"models": [{"name": "fixture", "digest": "a" * 64}]}
        valid_show = {"capabilities": ["completion", "thinking"], "model_info": {"general.architecture": "fixture"}}
        cases = [
            ({"models": []}, valid_show, valid_tags),
            ({"models": [{"name": "fixture", "digest": "bad"}]}, valid_show, valid_tags),
            ({"models": [{"name": "fixture", "digest": "a" * 64, "remote_host": "cloud.example"}]}, valid_show, valid_tags),
            (valid_tags, {"capabilities": None, "model_info": {}}, valid_tags),
            (valid_tags, {"capabilities": "completion", "model_info": {"a": 1}}, valid_tags),
            (valid_tags, {"capabilities": ["vision"], "model_info": {"a": 1}}, valid_tags),
            (valid_tags, {"capabilities": ["completion"], "model_info": {}}, valid_tags),
            (valid_tags, {**valid_show, "remote_model": "remote"}, valid_tags),
            (valid_tags, {**valid_show, "model_info": {"cloud": True}}, valid_tags),
            (valid_tags, {**valid_show, "model_info": {"tokenizer.ggml.add_eos_token": "credential"}}, valid_tags),
            (valid_tags, {**valid_show, "model_info": {"access_token": "credential"}}, valid_tags),
            (valid_tags, valid_show, {"models": [{"name": "fixture", "digest": "b" * 64}]}),
            (valid_tags, valid_show, {"models": [{"name": "other", "digest": "a" * 64}]}),
        ]
        for index, (before, show, after) in enumerate(cases):
            with self.subTest(case=index):
                calls = []

                class Transport:
                    def get_json_no_auth(self, **kwargs):
                        calls.append(kwargs)
                        return before if len(calls) == 1 else after

                    def post_json_no_auth(self, **kwargs):
                        calls.append(kwargs)
                        if kwargs["url"].endswith("/api/chat"):
                            raise AssertionError("invalid metadata must not generate")
                        return show

                adapter = OllamaTextGenerationAdapter(transport=Transport())
                with self.assertRaisesRegex(ValueError, "^TEXT_GENERATION_RESPONSE_INVALID$"):
                    adapter.prepare_qa_structured_payload(request, selection, adapter.provider_payload(request, selection))
                self.assertFalse(any(call["url"].endswith("/api/chat") for call in calls))

    def test_qa_structured_ollama_metadata_has_one_total_five_second_budget(self) -> None:
        request = self.cell_request()
        selection = TextModelSelection("OLLAMA", "http://models.example.com", "local", "local:fixture", "fixture", 1, "server_internal")
        calls = []

        class Transport:
            def get_json_no_auth(self, **kwargs):
                calls.append(kwargs)
                return {"models": [{"name": "fixture", "digest": "a" * 64}]}

            def post_json_no_auth(self, **kwargs):
                calls.append(kwargs)
                return {"capabilities": ["completion", "thinking"], "model_info": {"architecture": "fixture"}}

        adapter = OllamaTextGenerationAdapter(transport=Transport())
        with patch("daon_user_api.question_answering.time.monotonic", side_effect=[100, 100, 103, 105]):
            with self.assertRaisesRegex(ValueError, "^TEXT_GENERATION_RESPONSE_INVALID$"):
                adapter.prepare_qa_structured_payload(request, selection, adapter.provider_payload(request, selection))
        self.assertEqual(len(calls), 2)
        self.assertEqual([call["timeout_seconds"] for call in calls], [5, 2])

        calls.clear()
        with patch("daon_user_api.question_answering.time.monotonic", side_effect=[100, 100, 102, 104, 105]):
            with self.assertRaisesRegex(ValueError, "^TEXT_GENERATION_RESPONSE_INVALID$"):
                adapter.prepare_qa_structured_payload(request, selection, adapter.provider_payload(request, selection))
        self.assertEqual(len(calls), 3)
        self.assertEqual([call["timeout_seconds"] for call in calls], [5, 3, 1])

    def test_qa_ollama_never_uses_thinking_as_grounded_answer(self) -> None:
        request = self.cell_request()
        selection = TextModelSelection("OLLAMA", "http://models.example.com", "local", "local:fixture", "fixture", 1, "server_internal")

        class Transport:
            def get_json_no_auth(self, **kwargs):
                return {"models": [{"name": "fixture", "digest": "a" * 64}]}

            def post_json_no_auth(self, **kwargs):
                if kwargs["url"].endswith("/api/show"):
                    return {"capabilities": ["completion", "thinking"], "model_info": {"architecture": "fixture"}}
                return {"message": {"content": "", "thinking": json.dumps({
                    "answer": "Insufficient evidence.", "cited_chunk_ids": [], "insufficient": True,
                })}}

        adapter = OllamaTextGenerationAdapter(transport=Transport())
        payload = adapter.prepare_qa_structured_payload(request, selection, adapter.provider_payload(request, selection))
        with self.assertRaisesRegex(ValueError, "^TEXT_GENERATION_RESPONSE_INVALID$"):
            adapter.generate(request, selection, provider_payload=payload)

    def cell_request(self):
        return GroundedQuestionRequest('What are the code, date, amount and currency?', tuple(
            IndexedEvidenceChunk(f'cell-{index}', 'source-unit', 'version-unit', 1, text, f'span-{index}', 0.0, 'sheet_cell', json.dumps({'sheet': sheet, 'range': address}, ensure_ascii=False, separators=(',', ':')))
            for index, (text, sheet, address) in enumerate([
                ('REF-DELTA-17', '계획', 'B2'), ('2026-12-08', '계획', 'B3'),
                ('57000', '예산', 'B3'), ('KRW', '예산', 'B2')])
        ), 'trace-unit')

    def test_xlsx_question_payload_binds_exact_locator_enum_and_prompt_without_global_mutation(self):
        import copy
        request = self.cell_request()
        selection = TextModelSelection('OLLAMA', 'http://ollama.internal:11434', 'local-unit', 'local-unit', 'llama3.2:3b', 1)
        before = copy.deepcopy(UpstageTextGenerationAdapter._SCHEMA)
        calls = []
        class Transport:
            def post_json_no_auth(self, **kwargs):
                calls.append(kwargs)
                return {'message': {'content': json.dumps({'answer': 'REF-DELTA-17; 2026-12-08; 57000 KRW.', 'cited_chunk_ids': ['cell-0','cell-1','cell-2','cell-3'], 'insufficient': False})}}
        result = OllamaTextGenerationAdapter(transport=Transport()).generate(request, selection)
        self.assertEqual(result.cited_chunk_ids, ('cell-0','cell-1','cell-2','cell-3'))
        payload = calls[0]['payload']
        self.assertEqual(payload['format']['properties']['cited_chunk_ids'], {'anyOf': [{'const': ['cell-0','cell-1','cell-2','cell-3']}, {'const': []}]})
        self.assertNotIn('anyOf', payload['format'])
        self.assertEqual(payload['format']['properties']['insufficient'], {'type': 'boolean'})
        user = json.loads(payload['messages'][1]['content'])
        self.assertEqual(user['evidence'][0], {'chunk_id': 'cell-0', 'page': 1, 'text': 'REF-DELTA-17', 'locator': {'sheet': '계획', 'range': 'B2'}})
        self.assertEqual(user['evidence'][2]['locator'], {'sheet': '예산', 'range': 'B3'})
        self.assertEqual(payload['messages'][0]['content'], 'The supplied evidence contains at most five validated original workbook cells. Each text is an exact cell value and locator identifies its sheet and address. '
            'Answer EVERY requested part using those cell values: identifiers, dates, amounts and currency. Do not answer with field labels alone. '
            'If the evidence answers the question, return insufficient=false and cite ALL supplied chunk_id strings so every used value is grounded. '
            'Copy chunk_id strings exactly, never invent or shorten them. If evidence cannot answer the question, return insufficient=true and cited_chunk_ids=[]. '
            'Never invent facts, change values, calculate or execute formulas. Return only JSON answer, cited_chunk_ids and insufficient. '
            'For a question requesting several values, answer in a complete descriptive sentence covering every requested part, not a single identifier. '
            'Format example only with unrelated fictional values: "Project ID: ATLAS-99; date: 2027-04-05; amount: 230 USD." '
            'Never copy example values. Use the actual identifier, date, amount and currency from the supplied evidence when asked for those fields.')
        self.assertEqual(payload['options'], {'num_predict': 512, 'temperature': 0})
        self.assertEqual(calls[0]['timeout_seconds'], 90.0)
        self.assertEqual(UpstageTextGenerationAdapter._SCHEMA, before)
        payload['format']['properties']['cited_chunk_ids']['anyOf'][0]['const'].append('mutated')
        self.assertEqual(UpstageTextGenerationAdapter._SCHEMA, before)

    def test_xlsx_invalid_locator_fails_before_question_http(self):
        from dataclasses import replace
        request = self.cell_request(); calls = []
        class Transport:
            def post_json_no_auth(self, **kwargs): calls.append(kwargs); raise AssertionError('HTTP forbidden')
        selection = TextModelSelection('OLLAMA', 'http://ollama.internal:11434', 'local-unit', 'local-unit', 'llama3.2:3b', 1)
        bad = replace(request.evidence[0], locator_value='{"sheet":"계획","range":"B2:A1"}')
        with self.assertRaisesRegex(ValueError, '^TEXT_GENERATION_GROUNDING_INVALID$'):
            OllamaTextGenerationAdapter(transport=Transport()).generate(replace(request, evidence=(bad, *request.evidence[1:])), selection)
        self.assertEqual(calls, [])

    def test_xlsx_producer_still_rejects_missing_value_citation_unknown_id_or_invented_value(self):
        request = self.cell_request()
        selection = TextModelSelection('OLLAMA', 'http://ollama.internal:11434', 'local-unit', 'local-unit', 'llama3.2:3b', 1)
        for answer, cited in [('REF-DELTA-17', ['cell-1','cell-2','cell-3']), ('REF-DELTA-17', ['unknown']), ('999999 KRW', ['cell-0','cell-1','cell-2','cell-3'])]:
            with self.subTest(answer=answer, cited=cited):
                class Transport:
                    def post_json_no_auth(self, **kwargs): return {'message': {'content': json.dumps({'answer': answer, 'cited_chunk_ids': cited, 'insufficient': False})}}
                with self.assertRaisesRegex(ValueError, '^TEXT_GENERATION_GROUNDING_INVALID$'):
                    OllamaTextGenerationAdapter(transport=Transport()).generate(request, selection)

    def test_ollama_non_xlsx_and_mixed_payloads_preserve_legacy_wire_bytes(self):
        from dataclasses import replace
        request = self.cell_request()
        selection = TextModelSelection('OLLAMA', 'http://ollama.internal:11434', 'local-unit', 'local-unit', 'llama3.2:3b', 1)
        cases = [tuple(replace(item, locator_kind=kind) for item in request.evidence) for kind in ('page', 'section', 'image_region')]
        for evidence in (request.evidence[:1], (*request.evidence, replace(request.evidence[0], chunk_id='cell-extra'))):
            payload = OllamaTextGenerationAdapter.provider_payload(replace(request, evidence=evidence), selection)
            self.assertEqual(payload['format']['properties']['cited_chunk_ids'], {'anyOf': [{'const': [item.chunk_id for item in evidence]}, {'const': []}]})
            self.assertTrue(all('locator' in item for item in json.loads(payload['messages'][1]['content'])['evidence']))
        cases += [(replace(request.evidence[0], locator_kind='page'), *request.evidence[1:]),
                  (replace(request.evidence[0], source_id='source-other'), *request.evidence[1:]),
                  (replace(request.evidence[0], source_version_id='version-other'), *request.evidence[1:]),
                  (*request.evidence, replace(request.evidence[0], chunk_id='cell-4'), replace(request.evidence[1], chunk_id='cell-5'))]
        for evidence in cases:
            with self.subTest(kinds=[item.locator_kind for item in evidence], count=len(evidence)):
                payload = OllamaTextGenerationAdapter.provider_payload(replace(request, evidence=evidence), selection)
                expected = {'model': 'llama3.2:3b', 'stream': False, 'keep_alive': '5m', 'options': {'num_predict': 512, 'temperature': 0},
                    'format': UpstageTextGenerationAdapter._SCHEMA['json_schema']['schema'],
                    'messages': [{'role': 'system', 'content': 'Answer only from evidence JSON. Return answer, cited_chunk_ids and insufficient.'},
                                 {'role': 'user', 'content': json.dumps({'question': request.question, 'evidence': [{'chunk_id': c.chunk_id, 'page': c.page, 'text': c.text} for c in evidence]}, ensure_ascii=False, separators=(',', ':'))}]}
                self.assertEqual(json.dumps(payload).encode(), json.dumps(expected).encode())
        general = OllamaTextGenerationAdapter.general_provider_payload(GeneralConversationRequest('Hello', 'trace-unit'), selection)
        self.assertEqual(general, {'model': 'llama3.2:3b', 'stream': False, 'keep_alive': '5m', 'options': {'num_predict': 64, 'temperature': 0},
            'format': OpenAICompatibleTextGenerationAdapter._GENERAL_SCHEMA['json_schema']['schema'],
            'messages': [{'role': 'system', 'content': "Answer the user's question naturally and briefly without claiming to use Sources. For current external information, suggest a web search and wait for explicit user approval."}, {'role': 'user', 'content': 'Hello'}]})

    def test_xlsx_all_or_empty_ids_survive_canonical_freeze_and_independent_grounding(self):
        from daon_user_api.data_canon import canonical_json_bytes
        request = self.cell_request()
        selection = TextModelSelection('OLLAMA', 'http://ollama.internal:11434', 'local-unit', 'local-unit', 'llama3.2:3b', 1)
        payload = OllamaTextGenerationAdapter.provider_payload(request, selection)
        payload['messages'][1]['content'] = canonical_json_bytes(json.loads(payload['messages'][1]['content'])).decode()
        frozen = json.loads(canonical_json_bytes(payload))
        self.assertEqual(frozen['format']['properties']['cited_chunk_ids'], {'anyOf': [{'const': ['cell-0','cell-1','cell-2','cell-3']}, {'const': []}]})
        self.assertEqual(json.loads(frozen['messages'][1]['content'])['evidence'][0]['locator'], {'sheet': '계획', 'range': 'B2'})
        all_ids = ['cell-0','cell-1','cell-2','cell-3']
        cases = [('REF-DELTA-17; 2026-12-08; 57000 KRW.', all_ids, False, True),
                 ('Insufficient evidence.', [], True, True),
                 ('Insufficient evidence.', all_ids, True, False),
                 ('REF-DELTA-17', [], False, False),
                 ('REF-DELTA-17', ['cell-1','cell-2','cell-3'], False, False),
                 ('REF-DELTA-17', ['unknown'], False, False),
                 ('999999 KRW', all_ids, False, False)]
        for answer, cited, insufficient, valid in cases:
            with self.subTest(cited=cited, insufficient=insufficient, valid=valid):
                calls = []
                class Transport:
                    def post_json_no_auth(self, **kwargs):
                        calls.append(kwargs)
                        return {'message': {'content': json.dumps({'answer': answer, 'cited_chunk_ids': cited, 'insufficient': insufficient})}}
                adapter = OllamaTextGenerationAdapter(transport=Transport())
                if valid:
                    result = adapter.generate(request, selection, provider_payload=frozen)
                    self.assertEqual(result.cited_chunk_ids, tuple(cited))
                    self.assertEqual(result.insufficient, insufficient)
                else:
                    with self.assertRaisesRegex(ValueError, '^TEXT_GENERATION_GROUNDING_INVALID$'):
                        adapter.generate(request, selection, provider_payload=frozen)
                self.assertEqual(calls[0]['payload'], frozen)
                self.assertEqual(calls[0]['timeout_seconds'], 90.0)

    def test_ollama_grounded_consumer_sends_512_budget_with_ten_evidence_ids(self) -> None:
        calls = []
        evidence = tuple(IndexedEvidenceChunk(
            f"chunk-{index}-0123456789abcdef0123456789abcdef", "source-unit", "version-unit", index + 1,
            "납품 예정일은 2026-11-03이며 승인 조건은 검토 완료입니다.", f"span-{index}", 1.0,
        ) for index in range(10))
        chunk_ids = [chunk.chunk_id for chunk in evidence]

        class Transport:
            def post_json_no_auth(self, **kwargs):
                calls.append(kwargs)
                return {"message": {"content": json.dumps({
                    "answer": "납품 예정일은 2026-11-03이며 승인 조건은 검토 완료입니다.",
                    "cited_chunk_ids": chunk_ids, "insufficient": False,
                }, ensure_ascii=False)}, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 128}

        selection = TextModelSelection("OLLAMA", "http://ollama.internal:11434", "local-unit", "local-unit", "llama3.2:3b", 1)
        request = GroundedQuestionRequest("납품 예정일과 승인 조건은?", evidence, "trace-unit")
        result = OllamaTextGenerationAdapter(transport=Transport()).generate(request, selection)
        self.assertEqual(result.cited_chunk_ids, tuple(chunk_ids))
        self.assertEqual(result.usage["completion_tokens"], 128)  # unit transport metadata, not model evidence
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["payload"]["options"], {"num_predict": 512, "temperature": 0})
        self.assertEqual(calls[0]["url"], "http://ollama.internal:11434/api/chat")
        self.assertEqual(calls[0]["timeout_seconds"], 90.0)
        self.assertEqual(calls[0]["payload"]["format"]["required"], ["answer", "cited_chunk_ids", "insufficient"])
        self.assertFalse(calls[0]["payload"]["format"]["additionalProperties"])
        self.assertEqual(json.loads(calls[0]["payload"]["messages"][1]["content"])["evidence"][9]["chunk_id"], chunk_ids[9])

    def test_ollama_larger_budget_still_rejects_truncated_json_and_unknown_citation(self) -> None:
        selection = TextModelSelection("OLLAMA", "http://ollama.internal:11434", "local-unit", "local-unit", "llama3.2:3b", 1)
        request = GroundedQuestionRequest("내용은?", (IndexedEvidenceChunk(
            "chunk-unit", "source-unit", "version-unit", 1, "검토 완료가 필요합니다.", "span-unit", 1.0,
        ),), "trace-unit")
        cases = (
            ('{"answer":"검토 완료가 필요', "length", "TEXT_GENERATION_RESPONSE_INVALID"),
            (json.dumps({"answer": "검토 완료가 필요합니다.", "cited_chunk_ids": ["unknown-chunk"], "insufficient": False}), "stop", "TEXT_GENERATION_GROUNDING_INVALID"),
        )
        for content, done_reason, code in cases:
            with self.subTest(code=code):
                class Transport:
                    def post_json_no_auth(self, **_kwargs):
                        return {"message": {"content": content}, "done_reason": done_reason, "eval_count": 64}
                with self.assertRaisesRegex(ValueError, f"^{code}$"):
                    OllamaTextGenerationAdapter(transport=Transport()).generate(request, selection)

    def test_ollama_general_consumer_keeps_64_budget(self) -> None:
        calls = []
        class Transport:
            def post_json_no_auth(self, **kwargs):
                calls.append(kwargs)
                return {"message": {"content": '{"answer":"안녕하세요."}'}}
        selection = TextModelSelection("OLLAMA", "http://ollama.internal:11434", "local-unit", "local-unit", "llama3.2:3b", 1)
        result = OllamaTextGenerationAdapter(transport=Transport()).generate_general(GeneralConversationRequest("안녕하세요", "trace-unit"), selection)
        self.assertEqual(result.answer, "안녕하세요.")
        self.assertEqual(calls[0]["payload"]["options"], {"num_predict": 64, "temperature": 0})

    def test_messages_header_transport_is_bounded_and_rejects_extra_headers(self) -> None:
        captured = []

        class Opener:
            def open(self, request, timeout):
                captured.append((request, timeout))
                return io.BytesIO(b'{"content":[{"type":"text","text":"ok"}]}')

        with patch("daon_user_api.document_understanding_adapter.urllib.request.build_opener", return_value=Opener()) as build:
            result = UrlLibDocumentUnderstandingTransport().post_json_headers(
                url="https://models.example.com/v1/messages",
                headers={"x-api-key": "fixture-secret", "anthropic-version": "2023-06-01"},
                payload={"model": "manual-a"}, timeout_seconds=5,
            )
        self.assertEqual(result["content"][0]["text"], "ok")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0].get_method(), "POST")
        self.assertEqual(captured[0][0].get_header("X-api-key"), "fixture-secret")
        self.assertEqual(captured[0][1], 5)
        self.assertEqual(type(build.call_args.args[0]).__name__, "_NoRedirectHandler")

        for headers in (
            {"x-api-key": "fixture-secret", "anthropic-version": "2023-06-01", "Authorization": "Bearer bad"},
            {"x-api-key": "fixture-secret\r\nInjected: bad", "anthropic-version": "2023-06-01"},
        ):
            with self.subTest(headers=list(headers)):
                with self.assertRaises(DocumentUnderstandingError):
                    UrlLibDocumentUnderstandingTransport().post_json_headers(
                        url="https://models.example.com/v1/messages", headers=headers,
                        payload={}, timeout_seconds=5,
                    )

    def test_anthropic_messages_grounded_and_general_contract(self) -> None:
        class MessagesTransport:
            def __init__(self) -> None:
                self.calls = []

            def post_json_headers(self, **kwargs):
                self.calls.append(kwargs)
                answer = (
                    {"answer": "ORANGE-COMPASS-42", "cited_chunk_ids": ["chunk-page-2"], "insufficient": False}
                    if len(self.calls) == 1 else {"answer": "일반 답변입니다."}
                )
                return {
                    "content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}],
                    "usage": {"input_tokens": 17, "output_tokens": 9},
                }

        transport = MessagesTransport()
        adapter = AnthropicMessagesTextGenerationAdapter(transport=transport, api_key="fixture-secret")
        selection = TextModelSelection(
            "CUSTOM", "https://models.example.com/v1", "custom-1", "custom-1:manual-a",
            "manual-a", 1,
        )
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Verified code ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)
        grounded = adapter.generate(
            GroundedQuestionRequest("What code?", evidence, "trace-cp3"), selection,
        )
        general = adapter.generate_general(
            GeneralConversationRequest("안녕하세요", "trace-cp3"), selection,
        )
        self.assertEqual(grounded.cited_chunk_ids, ("chunk-page-2",))
        self.assertEqual(grounded.usage["input_tokens"], 17)
        self.assertEqual(general.answer, "일반 답변입니다.")
        for call in transport.calls:
            self.assertEqual(call["url"], "https://models.example.com/v1/messages")
            self.assertEqual(call["headers"], {
                "x-api-key": "fixture-secret", "anthropic-version": "2023-06-01",
            })
            payload = call["payload"]
            self.assertEqual(payload["model"], "manual-a")
            self.assertIn("system", payload)
            self.assertEqual([message["role"] for message in payload["messages"]], ["user"])
            self.assertGreater(payload["max_tokens"], 0)
            self.assertNotIn("response_format", payload)

    def test_anthropic_messages_rejects_unsupported_citation(self) -> None:
        class MessagesTransport:
            def post_json_headers(self, **_kwargs):
                return {"content": [{"type": "text", "text": json.dumps({
                    "answer": "unverified", "cited_chunk_ids": ["other"], "insufficient": False,
                })}]}

        selection = TextModelSelection(
            "CUSTOM", "https://models.example.com/v1", "custom-1", "custom-1:manual-a",
            "manual-a", 1,
        )
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Verified code ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)
        with self.assertRaisesRegex(ValueError, "TEXT_GENERATION_GROUNDING_INVALID"):
            AnthropicMessagesTextGenerationAdapter(
                transport=MessagesTransport(), api_key="fixture-secret",
            ).generate(GroundedQuestionRequest("What code?", evidence, "trace-cp3"), selection)

    def test_anthropic_messages_rejects_malformed_general_answer_types(self) -> None:
        selection = TextModelSelection(
            "CUSTOM", "https://models.example.com/v1", "custom-1", "custom-1:manual-a",
            "manual-a", 1,
        )
        for answer in (["text"], {"text": "answer"}, None, 3):
            with self.subTest(answer=answer):
                class MessagesTransport:
                    def post_json_headers(self, **_kwargs):
                        return {"content": [{"type": "text", "text": json.dumps({"answer": answer})}]}

                with self.assertRaisesRegex(ValueError, "^TEXT_GENERATION_RESPONSE_INVALID$"):
                    AnthropicMessagesTextGenerationAdapter(
                        transport=MessagesTransport(), api_key="fixture-secret",
                    ).generate_general(GeneralConversationRequest("hello", "trace-cp3"), selection)

    def test_anthropic_messages_rejects_malformed_grounded_field_types(self) -> None:
        selection = TextModelSelection(
            "CUSTOM", "https://models.example.com/v1", "custom-1", "custom-1:manual-a",
            "manual-a", 1,
        )
        evidence = (IndexedEvidenceChunk(
            "1", "source-cp3", "source-version-cp3", 2,
            "검증된 내용입니다.", "span-page-2", 1.0,
        ),)
        malformed = (
            {"answer": {"text": "검증된 내용입니다."}, "cited_chunk_ids": ["1"], "insufficient": False},
            {"answer": "검증된 내용입니다.", "cited_chunk_ids": [1], "insufficient": False},
            {"answer": "자료가 없습니다.", "cited_chunk_ids": [], "insufficient": "false"},
        )
        for response in malformed:
            with self.subTest(response=response):
                class MessagesTransport:
                    def post_json_headers(self, **_kwargs):
                        return {"content": [{"type": "text", "text": json.dumps(response, ensure_ascii=False)}]}

                with self.assertRaisesRegex(ValueError, "^TEXT_GENERATION_RESPONSE_INVALID$"):
                    AnthropicMessagesTextGenerationAdapter(
                        transport=MessagesTransport(), api_key="fixture-secret",
                    ).generate(GroundedQuestionRequest("내용은?", evidence, "trace-cp3"), selection)

    def test_ollama_rejects_malformed_grounded_response_without_coercion(self) -> None:
        selection = TextModelSelection(
            "OLLAMA", "http://models.example.com", "fixture", "fixture:revision",
            "revision", 1,
        )
        evidence = (IndexedEvidenceChunk(
            "1", "source-strict", "version-strict", 1,
            "검증된 내용입니다.", "span-strict", 1.0,
        ),)
        valid = {"answer": "검증된 내용입니다.", "cited_chunk_ids": ["1"], "insufficient": False}
        malformed = [
            {**valid, "answer": {"text": "검증된 내용입니다."}},
            {**valid, "cited_chunk_ids": [1]},
            {**valid, "cited_chunk_ids": "1"},
            {**valid, "cited_chunk_ids": {"1": "value"}},
            {**valid, "insufficient": 0},
            {**valid, "insufficient": None},
            {**valid, "extra": "unexpected"},
            {"answer": "검증된 내용입니다.", "cited_chunk_ids": ["1"]},
            [valid],
        ]
        for flag in ("false", "true", 1):
            malformed.append({"answer": "자료가 없습니다.", "cited_chunk_ids": [], "insufficient": flag})
        contents = [json.dumps(item, ensure_ascii=False) for item in malformed] + ["", "{invalid JSON"]
        for index, content in enumerate(contents):
            with self.subTest(case=index):
                class Transport:
                    def post_json_no_auth(self, **_kwargs):
                        return {"message": {"content": content}}

                with self.assertRaisesRegex(ValueError, "^TEXT_GENERATION_RESPONSE_INVALID$"):
                    OllamaTextGenerationAdapter(transport=Transport()).generate(
                        GroundedQuestionRequest("내용은?", evidence, "trace-strict"), selection,
                    )

    def test_ollama_duplicate_citations_remain_grounding_invalid(self) -> None:
        request = self.cell_request()
        selection = TextModelSelection(
            "OLLAMA", "http://models.example.com", "fixture", "fixture:revision",
            "revision", 1,
        )

        class Transport:
            def post_json_no_auth(self, **_kwargs):
                return {"message": {"content": json.dumps({
                    "answer": request.evidence[0].text,
                    "cited_chunk_ids": [request.evidence[0].chunk_id] * 2,
                    "insufficient": False,
                })}}

        with self.assertRaisesRegex(ValueError, "^TEXT_GENERATION_GROUNDING_INVALID$"):
            OllamaTextGenerationAdapter(transport=Transport()).generate(request, selection)

    def test_work_support_and_source_modes_are_classified_without_refusal(self) -> None:
        self.assertEqual(classify_question_intent("다음 작업을 어떻게 진행할까?"), "work_support")
        self.assertEqual(classify_question_intent("선택한 문서에서 보존 기간을 찾아줘"), "explicit_source_lookup")
        self.assertEqual(classify_question_intent("이 자료로 보고서를 만들어줘"), "source_backed_action")
        self.assertEqual(classify_question_intent("최신 정책을 웹에서 찾아줘"), "approved_web_research")

    def test_groq_mistral_and_upstage_share_grounded_openai_compatible_contract(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Citation verification phrase: ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)
        for provider_code, base_url in (
            ("GROQ", "https://api.groq.com/openai/v1"),
            ("MISTRAL", "https://api.mistral.ai/v1"),
            ("UPSTAGE", "https://api.upstage.ai/v1"),
        ):
            with self.subTest(provider_code=provider_code):
                transport = RecordingTransport()
                selection = resolve_text_model_selection(
                    external_text_snapshot(provider_code, base_url)
                )
                result = OpenAICompatibleTextGenerationAdapter(
                    transport=transport, api_key="server-secret",
                ).generate(
                    GroundedQuestionRequest("What is the phrase?", evidence, "trace-cp3"),
                    selection,
                )
                self.assertEqual(transport.calls[0][0], f"{base_url}/chat/completions")
                self.assertEqual(result.cited_chunk_ids, ("chunk-page-2",))

    def test_openai_compatible_adapter_rejects_unapproved_provider_endpoint_pair(self) -> None:
        selection = resolve_text_model_selection(
            external_text_snapshot("GROQ", "https://api.mistral.ai/v1")
        )
        with self.assertRaisesRegex(ValueError, "TEXT_PROVIDER_ENDPOINT_INVALID"):
            OpenAICompatibleTextGenerationAdapter(
                transport=RecordingTransport(), api_key="server-secret",
            ).generate(
                GroundedQuestionRequest(
                    "phrase?", (IndexedEvidenceChunk(
                        "chunk-page-2", "source-cp3", "source-version-cp3", 2,
                        "ORANGE-COMPASS-42", "span-page-2", 1.0,
                    ),), "trace-cp3",
                ),
                selection,
            )
    def test_text_role_is_frozen_to_selected_solar_pro4_deployment(self) -> None:
        selected = resolve_text_model_selection(text_snapshot())

        self.assertEqual(selected.provider_code, "UPSTAGE")
        self.assertEqual(selected.deployment_id, "deployment-text")
        self.assertEqual(selected.model_id, "solar-pro4")
        self.assertEqual(selected.binding_version, 5)

    def test_upstage_uses_exact_model_and_only_supplied_evidence(self) -> None:
        transport = RecordingTransport()
        adapter = UpstageTextGenerationAdapter(transport=transport, api_key="server-secret")
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Citation verification phrase: ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)

        result = adapter.generate(
            GroundedQuestionRequest(
                question="What is the citation verification phrase?",
                evidence=evidence,
                trace_id="trace-cp3",
            ),
            resolve_text_model_selection(text_snapshot()),
        )

        url, _secret, payload = transport.calls[0]
        self.assertEqual(url, "https://api.upstage.ai/v1/chat/completions")
        self.assertEqual(payload["model"], "solar-pro4")
        self.assertNotIn("source-version-cp3", result.answer)
        self.assertEqual(result.cited_chunk_ids, ("chunk-page-2",))
        self.assertEqual(result.answer, "The phrase is ORANGE-COMPASS-42.")

    def test_grounded_prompt_explains_source_scope_and_handles_out_of_scope_without_refusal(self) -> None:
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "Daon 보존 정책은 30일입니다.", "span-page-2", 1.0,
        ),)

        payload = UpstageTextGenerationAdapter.provider_payload(
            GroundedQuestionRequest("최신 주식 가격은?", evidence, "trace-cp3"),
            resolve_text_model_selection(text_snapshot()),
        )
        system_prompt = payload["messages"][0]["content"]
        self.assertIn("source scope", system_prompt.lower())
        self.assertIn("outside", system_prompt.lower())
        self.assertIn("web search", system_prompt.lower())
        self.assertIn("approval", system_prompt.lower())

    def test_adapter_rejects_provider_citation_outside_retrieved_chunks(self) -> None:
        transport = RecordingTransport()
        response = transport.post_json

        def fake_response(**kwargs):
            payload = response(**kwargs)
            payload["choices"][0]["message"]["content"] = json.dumps({
                "answer": "Invented", "cited_chunk_ids": ["fake"], "insufficient": False,
            })
            return payload

        transport.post_json = fake_response
        adapter = UpstageTextGenerationAdapter(transport=transport, api_key="server-secret")
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "ORANGE-COMPASS-42", "span-page-2", 1.0,
        ),)

        with self.assertRaisesRegex(ValueError, "TEXT_GENERATION_GROUNDING_INVALID"):
            adapter.generate(
                GroundedQuestionRequest("phrase?", evidence, "trace-cp3"),
                resolve_text_model_selection(text_snapshot()),
            )

    def test_adapter_rejects_material_answer_anchor_absent_from_cited_chunk(self) -> None:
        transport = RecordingTransport()

        def fake_response(**kwargs):
            transport.calls.append((kwargs["url"], kwargs["api_key"], kwargs["payload"]))
            return {"choices": [{"message": {"content": json.dumps({
                "answer": "The code is ALPHA-999.",
                "cited_chunk_ids": ["chunk-page-2"], "insufficient": False,
            })}}]}

        transport.post_json = fake_response
        evidence = (IndexedEvidenceChunk(
            "chunk-page-2", "source-cp3", "source-version-cp3", 2,
            "The verified code is ORANGE-COMPASS-42.", "span-page-2", 1.0,
        ),)
        with self.assertRaisesRegex(ValueError, "TEXT_GENERATION_GROUNDING_INVALID"):
            UpstageTextGenerationAdapter(
                transport=transport, api_key="server-secret",
            ).generate(
                GroundedQuestionRequest("What is the code?", evidence, "trace-cp3"),
                resolve_text_model_selection(text_snapshot()),
            )


if __name__ == "__main__":
    unittest.main()
