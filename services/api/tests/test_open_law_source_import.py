from dataclasses import replace
import hashlib
import importlib
from types import SimpleNamespace

import pytest

from daon_user_api.internet_connector import WebsiteImportContext
from daon_user_api.open_law_client import OpenLawDocument,OpenLawSelection


def context():return WebsiteImportContext('tenant-law','workspace-law','notebook-law','actor-law','trace-law','policy-law')
def selection():return OpenLawSelection('123','20260101',None)
def document():
    raw=b'{ "original": "law" }';text='제1조 원문 법령'.encode()
    return OpenLawDocument({'mst':'123','law_id':'456','title':'시험법','effective_date':'20260101',
        'promulgation_date':None,'ministry':None,'article_code':None},raw,text,
        hashlib.sha256(raw).hexdigest(),hashlib.sha256(text).hexdigest(),'2026-10-16T00:00:00Z','open-law-text-v1')


class Repository:
    def __init__(self):self.claimed=False;self.failure=None;self.completes=0;self.result=None;self.fingerprint=None;self.bound=True
    def claim(self,ctx,sel,raw,text,key):
        module=importlib.import_module('daon_user_api.open_law_source_import')
        fp=(ctx,sel,raw,text)
        if self.claimed:
            if self.fingerprint!=fp:raise module.OpenLawImportError('OPEN_LAW_IMPORT_CONFLICT',409)
            if self.result:
                if not self.bound:raise module.OpenLawImportError('OPEN_LAW_ACCESS_DENIED',403)
                return module.OpenLawImportClaim('law-request-'+'a'*32,'a'*64,True,self.result)
            raise module.OpenLawImportError('OPEN_LAW_IMPORT_IN_PROGRESS',409)
        self.claimed=True;self.fingerprint=fp
        return module.OpenLawImportClaim('law-request-'+'a'*32,'a'*64,False,None)
    def mark_storing(self,*_):pass
    def complete(self,*args):
        self.completes+=1
        module=importlib.import_module('daon_user_api.open_law_source_import')
        self.result=module.OpenLawImportResult('law-snapshot-1','law-source-1','law-version-1','ready',False)
        return self.result
    def fail(self,ctx,claim,code):self.failure=code


def service(repo=None,promote=None,read=None,authorize=None):
    module=importlib.import_module('daon_user_api.open_law_source_import')
    repo=repo or Repository();calls=[]
    def fetch(ctx,sel):calls.append((ctx,sel));return document()
    promoter=SimpleNamespace(promote=promote or (lambda ctx,claim,kind,content,deadline:module.object_id(claim,kind)))
    client=SimpleNamespace(read=read or fetch)
    return module.OpenLawSourceImportService(repo,promoter,client=client,authorize_context=authorize or (lambda _:None)),repo,calls


def invoke(instance,key='request-1',**kwargs):
    doc=document()
    return instance.import_selection(context(),selection(),kwargs.get('raw',doc.raw_sha256),doc.text_sha256,key)


def test_preview_change_rejected():
    instance,repo,_=service()
    with pytest.raises(ValueError,match='^OPEN_LAW_PREVIEW_CHANGED$'):invoke(instance,raw='a'*64)
    assert repo.failure=='OPEN_LAW_PREVIEW_CHANGED';assert repo.completes==0


def test_same_key_replay_without_refetch():
    instance,repo,calls=service();first=invoke(instance);second=invoke(instance)
    assert not first.replayed and second.replayed;assert len(calls)==1 and repo.completes==1


def test_replay_never_rebinds_unbound_source():
    instance,repo,calls=service();invoke(instance);repo.bound=False
    with pytest.raises(ValueError,match='^OPEN_LAW_ACCESS_DENIED$'):invoke(instance)
    assert len(calls)==1;assert repo.completes==1


def test_key_conflict_and_concurrent_import():
    instance,repo,_=service();repo.claimed=True;repo.fingerprint=(context(),selection(),document().raw_sha256,document().text_sha256)
    with pytest.raises(ValueError,match='^OPEN_LAW_IMPORT_IN_PROGRESS$'):invoke(instance)
    with pytest.raises(ValueError,match='^OPEN_LAW_IMPORT_CONFLICT$'):invoke(instance,raw='a'*64)


def test_partial_object_failure_never_ready():
    def promote(ctx,claim,kind,content,deadline):
        if kind=='text':raise OSError('private storage error')
        return 'raw-object'
    instance,repo,_=service(promote=promote)
    with pytest.raises(ValueError,match='^OPEN_LAW_STORAGE_UNAVAILABLE$'):invoke(instance)
    assert repo.failure=='OPEN_LAW_STORAGE_UNAVAILABLE';assert repo.completes==0


def test_raw_base64_final_strict_verification_and_claim_failure_record():
    instance,repo,_=service(read=lambda *_:replace(document(),raw_sha256='a'*64))
    with pytest.raises(ValueError,match='^OPEN_LAW_PROTOCOL_INVALID$'):invoke(instance)
    assert repo.failure=='OPEN_LAW_PROTOCOL_INVALID';assert repo.completes==0


def test_authorization_revocation_after_claim_records_failure():
    calls=[]
    def authorize(_):
        calls.append(1)
        if len(calls)>1:raise PermissionError
    instance,repo,fetches=service(authorize=authorize)
    with pytest.raises(ValueError,match='^OPEN_LAW_ACCESS_DENIED$'):invoke(instance)
    assert repo.failure=='OPEN_LAW_ACCESS_DENIED';assert not fetches


@pytest.mark.parametrize('operation',['import','preview'])
def test_unexpected_adapter_error_never_persists_or_returns_raw_message(operation):
    from daon_user_api.open_law_client import OpenLawError
    def read(*_):raise OpenLawError('credential-value-must-not-escape')
    instance,repo,_=service(read=read)
    with pytest.raises(ValueError,match='^OPEN_LAW_UPSTREAM_FAILED$'):
        if operation=='import':invoke(instance)
        else:instance.preview(context(),selection())
    assert repo.failure==('OPEN_LAW_UPSTREAM_FAILED' if operation=='import' else None)
