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
            self.assertEqual(branch['properties']['answer'],{'type':'string','minLength':1})
        self.assertEqual(list(prepared['format']['anyOf'][0]['properties']),['insufficient','cited_chunk_ids','answer'])
        # Literal branch predicates: both legitimate states, neither contradictory pairing.
        for insufficient, ids, permitted in [(False,['doc-ref','doc-amount'],True),(True,[],True),(True,['doc-ref','doc-amount'],False),(False,[],False),(False,['doc-ref'],False)]:
            matches=[b for b in schema['anyOf'] if b['properties']['insufficient']['const'] is insufficient and b['properties']['cited_chunk_ids']['const']==ids]
            self.assertEqual(bool(matches),permitted)
        self.assertEqual(canonical_json_bytes(baseline),snapshot)  # QA outside path is unchanged.
        self.assertEqual(UpstageTextGenerationAdapter._SCHEMA,original_schema)
        self.assertIn('Never return insufficient=true while answering with supplied material facts.',prepared['messages'][0]['content'])
        self.assertIs(prepared['think'],False)
        self.assertEqual(prepared['options'],{'num_predict':512,'temperature':0})
        for response, valid in [
            ({'answer':'REF-17; 64 EUR','cited_chunk_ids':['doc-ref','doc-amount'],'insufficient':False},True),
            ({'answer':'Insufficient evidence.','cited_chunk_ids':[],'insufficient':True},True),
            ({'answer':'REF-17; 64 EUR','cited_chunk_ids':[],'insufficient':True},False),
            ({'answer':'Unsupported 999 USD','cited_chunk_ids':['doc-ref','doc-amount'],'insufficient':False},False),
            ({'answer':'REF-17; 64 EUR','cited_chunk_ids':['made-up'],'insufficient':False},False),
        ]:
            with self.subTest(response=response):
                if valid: self.assertEqual(adapter.generate(request,selection,provider_payload=prepared).insufficient,response['insufficient'])
                else:
                    with self.assertRaisesRegex(ValueError,'TEXT_GENERATION_GROUNDING_INVALID'):
                        adapter.generate(request,selection,provider_payload=prepared)
        self.assertEqual(calls[3]['timeout_seconds'],90)

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
