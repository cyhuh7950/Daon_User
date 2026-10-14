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
