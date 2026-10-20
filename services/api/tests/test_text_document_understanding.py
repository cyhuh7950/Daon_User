import json
from dataclasses import replace

import pytest

from daon_user_api.document_processing import DocumentProcessingContext, StoredSourceDocument
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
from daon_user_api.text_document_understanding import OllamaTextDocumentAdapter
from daon_user_api.workspace_model_defaults import ResolvedModel


class Transport:
    def __init__(self, facts):
        self.facts = facts
        self.calls = []

    def post_json_no_auth(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps({
            "title": "Test source", "summary": "The source describes ORANGE-COMPASS-42.",
            "key_facts": self.facts,
        })}, "model": "llama3.2:3b"}


def _selection():
    return ResolvedModel(
        connection_id="ollama-public", provider_code="OLLAMA", model_id="llama3.2:3b",
        capability="text_generation", base_url="http://ollama.internal:11434",
        credential_version=0, default_version=1, catalog_version=1,
        provider_kind="local", routing_owner="provider", daon_fallback_allowed=True,
        _credential=None,
    )


def _document():
    return StoredSourceDocument(
        "source-txt", "source-version-txt", "report.txt",
        "ORANGE-COMPASS-42 is the current source marker.\n".encode(), "text/plain",
    )


def _context():
    return DocumentProcessingContext("tenant-t", "workspace-t", "actor-t", "trace-t", "policy-v1")


def test_txt_calls_selected_ollama_and_produces_verbatim_grounded_result():
    transport = Transport(["ORANGE-COMPASS-42 is the current source marker."])
    result = OllamaTextDocumentAdapter(transport).understand(_document(), _selection(), _context())
    assert result.status == "ready"
    assert result.semantic.key_facts == ("ORANGE-COMPASS-42 is the current source marker.",)
    assert result.parser.page_texts == ((1, "ORANGE-COMPASS-42 is the current source marker."),)
    assert result.lineage["provider_code"] == "OLLAMA"
    assert result.lineage["source_format"] == "txt"
    assert transport.calls[0]["url"] == "http://ollama.internal:11434/api/chat"
    assert "ORANGE-COMPASS-42" in json.dumps(transport.calls[0]["payload"])


@pytest.mark.parametrize("filename,mime,source_format", [
    ("report.md", "text/markdown", "md"),
    ("report.csv", "text/csv", "csv"),
])
def test_registered_utf8_text_formats_use_same_semantic_and_verbatim_contract(filename, mime, source_format):
    transport = Transport(["ORANGE-COMPASS-42 is the current source marker."])
    document = StoredSourceDocument(
        "source-text", "source-version-text", filename,
        b"ORANGE-COMPASS-42 is the current source marker.\n", mime,
    )
    result = OllamaTextDocumentAdapter(transport).understand(document, _selection(), _context())
    assert result.status == "ready"
    assert result.semantic.key_facts == ("ORANGE-COMPASS-42 is the current source marker.",)
    assert result.lineage["source_format"] == source_format
    assert result.parser.page_texts == ((1, "ORANGE-COMPASS-42 is the current source marker."),)
    assert len(transport.calls) == 1


def test_txt_hallucinated_fact_fails_before_ready():
    transport = Transport(["UNSUPPORTED-999 is approved."])
    with pytest.raises(DocumentUnderstandingError, match="TEXT_UNDERSTANDING_UNGROUNDED"):
        OllamaTextDocumentAdapter(transport).understand(_document(), _selection(), _context())


def test_txt_invalid_encoding_or_oversize_is_not_sent_to_model():
    transport = Transport(["safe"])
    for content in (b"\xff", b"a" * (32 * 1024 + 1)):
        document = StoredSourceDocument("source-txt", "source-version-txt", "report.txt", content, "text/plain")
        with pytest.raises(DocumentUnderstandingError):
            OllamaTextDocumentAdapter(transport).understand(document, _selection(), _context())
    assert transport.calls == []


@pytest.fixture(autouse=True)
def isolate_qa_workspace_flag(monkeypatch):
    monkeypatch.delenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID',raising=False)


class MetadataTransport(Transport):
    """실제 helper의 tags/show/tags 검증을 수행하는 전송 경계 double."""
    def __init__(self, *, thinking=True, fault=None, now=None, response_mode='normal'):
        super().__init__(["ORANGE-COMPASS-42 is the current source marker."])
        self.thinking=thinking; self.fault=fault; self.now=now
        self.metadata_calls=[]; self.tags_count=0; self.response_mode=response_mode

    def _tick(self,kwargs):
        self.metadata_calls.append(kwargs)
        if self.now is not None:
            self.now[0]+=2.0

    def get_json_no_auth(self,**kwargs):
        self._tick(kwargs); self.tags_count+=1
        row={'name':'fixture-thinking-model','digest':'a'*64}
        if self.fault=='digest-change' and self.tags_count==2:
            row['digest']='b'*64
        if self.fault=='remote-tag':
            row['remote_host']='remote.invalid'
        if self.fault=='model-mismatch':
            row['name']='different-installed-model'
        return {'models':[row]}

    def post_json_no_auth(self,**kwargs):
        if kwargs['url'].endswith('/api/show'):
            self._tick(kwargs)
            value={'capabilities':['completion','thinking'] if self.thinking else ['completion'],
                'model_info':{'general.architecture':'fixture'}}
            if self.fault=='remote-show':
                value['remote_model']='remote.invalid'
            if self.fault=='badmetadata':
                value['capabilities']='thinking'
            if self.fault=='missing-completion':
                value['capabilities']=['thinking']
            return value
        if ((self.thinking and kwargs['payload'].get('think') is not False)
                or self.response_mode=='thinking-only'):
            self.calls.append(kwargs)
            return {'model':'fixture-thinking-model','done':True,'done_reason':'length','eval_count':512,
                'message':{'content':'','thinking':'not an answer'}}
        value=super().post_json_no_auth(**kwargs)
        if self.response_mode=='bad-json':
            value['message']['content']='{"title":'
        elif self.response_mode=='ungrounded':
            value['message']['content']=json.dumps({'title':'Test','summary':'UNSUPPORTED-999',
                'key_facts':['UNSUPPORTED-999']})
        return value


def thinking_selection():
    return replace(_selection(),model_id='fixture-thinking-model')


def test_qa_verified_thinking_request_prevents_empty_content_without_using_thoughts(monkeypatch):
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    transport=MetadataTransport()
    document=_document(); original=document.content
    result=OllamaTextDocumentAdapter(transport).understand(document,thinking_selection(),_context())
    assert result.status=='ready' and document.content==original
    assert result.semantic.key_facts==('ORANGE-COMPASS-42 is the current source marker.',)
    assert result.parser.text=='ORANGE-COMPASS-42 is the current source marker.'
    assert [call['url'].rsplit('/',1)[-1] for call in transport.metadata_calls]==['tags','show','tags']
    assert transport.metadata_calls[1]['payload']=={'model':'fixture-thinking-model'}
    chat=transport.calls[0]
    assert chat['payload']['think'] is False
    assert chat['payload']['options']=={'temperature':0,'num_predict':512} and chat['timeout_seconds']==90.0


@pytest.mark.parametrize('fault', ['digest-change','remote-tag','remote-show','model-mismatch','badmetadata','missing-completion'])
def test_qa_unverified_metadata_is_failclosed_before_chat(monkeypatch,fault):
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    transport=MetadataTransport(fault=fault)
    with pytest.raises(DocumentUnderstandingError,match='^DOCUMENT_PROVIDER_ADAPTER_UNAVAILABLE$'):
        OllamaTextDocumentAdapter(transport).understand(_document(),thinking_selection(),_context())
    assert transport.calls==[]


@pytest.mark.parametrize('target', [None,'different-workspace'])
def test_nonqa_or_missing_flag_preserves_original_chat_payload(monkeypatch,target):
    if target is not None:
        monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID',target)
    transport=MetadataTransport(thinking=False)
    original=Transport(['ORANGE-COMPASS-42 is the current source marker.'])
    OllamaTextDocumentAdapter(original).understand(_document(),_selection(),_context())
    OllamaTextDocumentAdapter(transport).understand(_document(),_selection(),_context())
    assert transport.metadata_calls==[] and transport.calls==original.calls


def test_qa_verified_nonthinking_preserves_payload(monkeypatch):
    transport=MetadataTransport(thinking=False)
    ordinary=Transport(['ORANGE-COMPASS-42 is the current source marker.'])
    OllamaTextDocumentAdapter(ordinary).understand(_document(),thinking_selection(),_context())
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    OllamaTextDocumentAdapter(transport).understand(_document(),thinking_selection(),_context())
    assert len(transport.metadata_calls)==3 and transport.calls==ordinary.calls


def test_qa_metadata_get_shares_total_five_second_budget(monkeypatch):
    import daon_user_api.question_answering as qa
    now=[100.0]
    monkeypatch.setattr(qa.time,'monotonic',lambda:now[0])
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    transport=MetadataTransport(now=now)
    with pytest.raises(DocumentUnderstandingError,match='^DOCUMENT_PROVIDER_ADAPTER_UNAVAILABLE$'):
        OllamaTextDocumentAdapter(transport).understand(_document(),thinking_selection(),_context())
    assert transport.calls==[]
    assert [c['timeout_seconds'] for c in transport.metadata_calls]==[5.0,3.0,1.0]


def test_qa_missing_get_or_external_selection_never_chats(monkeypatch):
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    for selection in (thinking_selection(),replace(thinking_selection(),provider_kind='external_api')):
        transport=Transport(['ORANGE-COMPASS-42 is the current source marker.'])
        with pytest.raises(DocumentUnderstandingError,match='^DOCUMENT_PROVIDER_ADAPTER_UNAVAILABLE$'):
            OllamaTextDocumentAdapter(transport).understand(_document(),selection,_context())
        assert transport.calls==[]


@pytest.mark.parametrize('mode,code', [('thinking-only','TEXT_UNDERSTANDING_RESPONSE_INVALID'),
    ('bad-json','TEXT_UNDERSTANDING_RESPONSE_INVALID'),('ungrounded','TEXT_UNDERSTANDING_UNGROUNDED')])
def test_qa_request_fix_does_not_relax_strict_results(monkeypatch,mode,code):
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    transport=MetadataTransport(response_mode=mode)
    with pytest.raises(DocumentUnderstandingError,match='^'+code+'$'):
        OllamaTextDocumentAdapter(transport).understand(_document(),thinking_selection(),_context())


_EXACT_SOURCE_LINE = '내용: 복사한 텍스트를 Source로 등록하고 저장 후 다시 조회합니다.'


class ExactFactTransport(MetadataTransport):
    def __init__(self, fact):
        super().__init__()
        self.fact=fact

    def post_json_no_auth(self,**kwargs):
        response=super().post_json_no_auth(**kwargs)
        if kwargs['url'].endswith('/api/chat'):
            response['message']['content']=json.dumps({'title':'시험 자료','summary':'텍스트 등록 시험입니다.',
                'key_facts':[self.fact]})
        return response


@pytest.mark.parametrize('target', ['workspace-t','different-workspace'])
def test_source_spacing_change_remains_ungrounded_even_with_enum(monkeypatch,target):
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID',target)
    transport=ExactFactTransport(_EXACT_SOURCE_LINE.replace('Source로','Source 로'))
    document=replace(_document(),content=_EXACT_SOURCE_LINE.encode())
    with pytest.raises(DocumentUnderstandingError,match='^TEXT_UNDERSTANDING_UNGROUNDED$'):
        OllamaTextDocumentAdapter(transport).understand(document,thinking_selection(),_context())


def test_qa_thinking_schema_offers_exact_lines_but_model_selects_fact(monkeypatch):
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    lines=['시험 제목',_EXACT_SOURCE_LINE,'값: 57000 KRW','내부  공백과\t탭 유지']
    content=('\n\n'.join(lines)+'\n'+lines[1]+'\n').encode()
    transport=ExactFactTransport(lines[2])
    document=replace(_document(),content=content)
    result=OllamaTextDocumentAdapter(transport).understand(document,thinking_selection(),_context())
    schema=transport.calls[0]['payload']['format']
    assert schema['properties']['key_facts']['items'].get('enum')==lines
    assert schema['properties']['key_facts']['minItems']==1 and schema['properties']['key_facts']['maxItems']==5
    assert schema['required']==['title','summary','key_facts'] and schema['additionalProperties'] is False
    assert result.semantic.key_facts==('값: 57000 KRW',), '서버가 첫 후보/전체 후보로 응답을 덮어쓰지 않는다'
    assert document.content==content
    assert transport.calls[0]['payload']['messages'][1]['content']==content.decode().strip()
    from daon_user_api.text_document_understanding import _SCHEMA
    assert _SCHEMA['properties']['key_facts']=={'type':'array','items':{'type':'string'}}, '공유 schema 불변'


def test_qa_line_candidates_include_2000_codepoints(monkeypatch):
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    short='가'*2000
    transport=ExactFactTransport(_EXACT_SOURCE_LINE)
    document=replace(_document(),content=(short+'\n'+_EXACT_SOURCE_LINE).encode())
    OllamaTextDocumentAdapter(transport).understand(document,thinking_selection(),_context())
    assert transport.calls[0]['payload']['format']['properties']['key_facts']['items'].get('enum')==[short,_EXACT_SOURCE_LINE]


def test_qa_mixed_short_title_and_long_body_keeps_all_continuous_excerpts(monkeypatch):
    from daon_user_api.text_document_understanding import _SCHEMA
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    long_line='나'*2001
    content=('짧은 제목\n'+long_line).encode()
    transport=ExactFactTransport('나'*20)
    result=OllamaTextDocumentAdapter(transport).understand(
        replace(_document(),content=content),thinking_selection(),_context())
    payload=transport.calls[0]['payload']
    assert payload['format']==_SCHEMA, '긴 본문의 핵심 사실을 enum 밖으로 누락하지 않는다'
    assert payload['think'] is False
    assert payload['messages'][1]['content']=='짧은 제목\n'+long_line
    assert 'schema enum' not in payload['messages'][0]['content']
    assert result.semantic.key_facts==('나'*20,)


def test_qa_long_single_line_without_candidates_keeps_continuous_excerpt_contract(monkeypatch):
    from daon_user_api.text_document_understanding import _SCHEMA
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    transport=ExactFactTransport('가'*20)
    content=('가'*2001).encode()
    document=replace(_document(),content=content)
    result=OllamaTextDocumentAdapter(transport).understand(document,thinking_selection(),_context())
    assert transport.calls[0]['payload']['format']==_SCHEMA
    assert result.semantic.key_facts==('가'*20,) and document.content==content


def test_qa_enum_does_not_allow_summary_extra_anchors(monkeypatch):
    monkeypatch.setenv('DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID','workspace-t')
    class ExtraSummaryTransport(ExactFactTransport):
        def post_json_no_auth(self,**kwargs):
            response=super().post_json_no_auth(**kwargs)
            if kwargs['url'].endswith('/api/chat'):
                value=json.loads(response['message']['content'])
                value['summary']='지원되지 않은 금액은 58000 KRW입니다.'
                response['message']['content']=json.dumps(value)
            return response
    transport=ExtraSummaryTransport(_EXACT_SOURCE_LINE)
    document=replace(_document(),content=_EXACT_SOURCE_LINE.encode())
    with pytest.raises(DocumentUnderstandingError,match='^TEXT_UNDERSTANDING_UNGROUNDED$'):
        OllamaTextDocumentAdapter(transport).understand(document,thinking_selection(),_context())
