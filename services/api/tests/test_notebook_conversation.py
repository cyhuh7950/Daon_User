"""Conversation SQL-boundary tests; no PostgreSQL server or Provider is used."""
from dataclasses import replace
from datetime import datetime, timezone
import pytest
from daon_user_api.notebook import NotebookContext, NotebookError
from daon_user_api.notebook_conversation_postgres import encode_cursor, decode_cursor, admission_payload

CTX = NotebookContext('tenant-b','workspace-b','actor-b','trace-b','policy-b')
NOW = datetime(2026,10,21,tzinfo=timezone.utc)
FP = 'sha256:' + 'a'*64


def test_cursor_exact_scope_and_order():
    encoded = encode_cursor(CTX,'notebook-b',NOW,'turn-b')
    assert decode_cursor(CTX,'notebook-b',encoded) == (NOW,'turn-b')
    assert decode_cursor(CTX,'notebook-b',None) is None
    for other in (replace(CTX,actor_id='actor-other'),replace(CTX,workspace_id='workspace-other')):
        with pytest.raises(NotebookError,match='NOTEBOOK_CURSOR_INVALID'):
            decode_cursor(other,'notebook-b',encoded)
    for malformed in ('',encoded+'=',encoded+'!',encoded[:-2],True):
        with pytest.raises(NotebookError,match='NOTEBOOK_CURSOR_INVALID'):
            decode_cursor(CTX,'notebook-b',malformed)


def test_admission_closed_metadata_and_exact_question():
    payload = admission_payload(CTX,'notebook-b','run-b',' 원문 질문\n둘째 줄 ',FP,{'source_id':'source-b','source_version_id':'version-b'})
    assert payload['question'] == ' 원문 질문\n둘째 줄 '
    assert payload['kind'] == 'notebook_question_turn'
    assert payload['actor_id'] == CTX.actor_id
    for metadata in ({'password':'never-store'}, {'step_up_authorization_id':'never-store'}, {'source_id':'https://unsafe'}):
        with pytest.raises(NotebookError,match='NOTEBOOK_TURN_INVALID'):
            admission_payload(CTX,'notebook-b','run-b','질문',FP,metadata)
    for question in ('',True,'x'*32769):
        with pytest.raises(NotebookError,match='NOTEBOOK_TURN_INVALID'):
            admission_payload(CTX,'notebook-b','run-b',question,FP,{})

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from daon_user_api.notebook_conversation_postgres import PostgresNotebookConversationRepository

class Rows:
    def __init__(self, rows=()): self.rows=list(rows)
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows

class Ledger:
    def __init__(self):
        self.rows={}; self.bindings={}; self.calls=[]; self.fail_binding=False
    def execute(self, sql, params=()):
        self.calls.append((sql,params))
        if sql.startswith('INSERT INTO conversations') or sql.startswith('INSERT INTO messages'):
            table=sql.split()[2]; key=(table,*params[:3])
            self.rows.setdefault(key, (params[6].obj,params[7],params[8],params[9],params[3],params[4],params[5],*params[12:]))
            return Rows()
        if sql.startswith('SELECT r.created_by,r.canonical_json,r.conversation_id'):
            value=getattr(self,'existing_run',None); return Rows([value]) if value else Rows()
        if sql.startswith('SELECT canonical_json,canonical_text'):
            table=sql.split(' FROM ')[1].split()[0]
            return Rows([self.rows[(table,*params)]]) if (table,*params) in self.rows else Rows()
        if sql.startswith('INSERT INTO notebook_bindings'):
            if self.fail_binding: raise NotebookError('NOTEBOOK_TURN_CONFLICT',409)
            self.bindings.setdefault(tuple(params[:4]), (params[4],None))
            return Rows()
        if sql.startswith('SELECT created_by,version_id'):
            value=self.bindings.get(tuple(params)); return Rows([value]) if value else Rows()
        if sql.startswith('SELECT canonical_json,created_by FROM conversations'):
            row=self.rows.get(('conversations',*params)); return Rows([(row[0],row[3])]) if row else Rows()
        raise AssertionError('unexpected SQL boundary')
    @contextmanager
    def _transaction(self, context):
        before=deepcopy((self.rows,self.bindings))
        try: yield self
        except BaseException:
            self.rows,self.bindings=before
            raise

@pytest.fixture
def ledger_repo(monkeypatch):
    ledger=Ledger(); guards=[]
    def guard(conn,scope,sources,*,write):
        guards.append((conn,scope,sources,write))
        if getattr(ledger,'denied',False):
            from daon_user_api.question_answering_postgres import QuestionRepositoryError
            raise QuestionRepositoryError('QUESTION_SOURCE_UNAVAILABLE',status=404)
    monkeypatch.setattr('daon_user_api.question_answering_postgres.PostgresQuestionAnsweringRepository._knowledge_selections',guard)
    owner=SimpleNamespace(_store=ledger,_context=lambda value:value)
    return ledger,PostgresNotebookConversationRepository(owner),guards


def admit(repo, **changes):
    args=dict(run_id='run-b',question='질문',request_fingerprint=FP,context_identifiers={},now=NOW)
    args.update(changes)
    return repo.admit_question_turn(CTX,'notebook-b',**args)


def test_admission_exact_replay_conflict_current_guard_and_atomic_binding(ledger_repo):
    ledger,repo,guards=ledger_repo
    turn=admit(repo)
    before=deepcopy(ledger.rows)
    assert admit(repo) == turn and ledger.rows == before
    assert len(guards)==2 and all(item[0] is ledger and item[2:]==((),False) for item in guards)
    with pytest.raises(NotebookError,match='NOTEBOOK_TURN_CONFLICT'):
        admit(repo,question='다른 질문')
    assert ledger.rows == before
    ledger.denied=True
    with pytest.raises(NotebookError,match='NOTEBOOK_NOT_FOUND'):
        admit(repo,run_id='run-denied')
    assert ledger.rows == before
    ledger.denied=False; ledger.fail_binding=True
    with pytest.raises(NotebookError,match='NOTEBOOK_TURN_CONFLICT'):
        admit(repo,run_id='run-rollback')
    assert ledger.rows == before


def test_canon_corruption_and_event_parent_scope_rejected(ledger_repo):
    ledger,repo,_=ledger_repo
    turn=admit(repo)
    repo.append_question_turn_event(CTX,'notebook-b',run_id='run-b',request_fingerprint=FP,event_kind='awaiting_authorization',now=NOW)
    count=len(ledger.rows)
    repo.append_question_turn_event(CTX,'notebook-b',run_id='run-b',request_fingerprint=FP,event_kind='awaiting_authorization',now=NOW)
    assert len(ledger.rows)==count
    with pytest.raises(NotebookError,match='NOTEBOOK_TURN_CONFLICT'):
        repo.append_question_turn_event(CTX,'notebook-b',run_id='run-b',request_fingerprint='sha256:'+'b'*64,event_kind='failed',now=NOW)
    key=('conversations',CTX.tenant_id,CTX.workspace_id,turn)
    row=list(ledger.rows[key]); row[2]='f'*64; ledger.rows[key]=tuple(row)
    with pytest.raises(NotebookError,match='NOTEBOOK_TURN_CONFLICT'):
        admit(repo)


def test_paging_validates_limit_before_sql(ledger_repo):
    ledger,repo,_=ledger_repo
    for limit in (0,101,True,'50'):
        with pytest.raises(NotebookError,match='NOTEBOOK_PAGE_INVALID'):
            repo.list_conversation_turns(CTX,'notebook-b',limit=limit,before=None)
    assert not ledger.calls

class PageLedger(Ledger):
    def __init__(self, pages,events=None,citations=()):
        super().__init__(); self.pages=pages; self.events=events or {}; self.citations=citations
    def execute(self,sql,params=()):
        if sql.startswith('WITH actual_runs'):
            self.calls.append((sql,params))
            assert 'LIMIT %(size)s' in sql and 'NOT EXISTS' in sql
            return Rows(self.pages[:params['size']])
        if sql.startswith('SELECT canonical_json FROM messages'):
            self.calls.append((sql,params)); assert "WHEN 'uncertain' THEN 3" in sql
            return Rows([(self.events[params[2]],)]) if params[2] in self.events else Rows()
        if sql.startswith('SELECT rr.canonical_json,c.record_id'):
            self.calls.append((sql,params)); assert 'LIMIT' not in sql
            return Rows(self.citations)
        if sql.startswith('SELECT a.canonical_json'):
            self.calls.append((sql,params)); return Rows([('provider-b','model/b')])
        return super().execute(sql,params)


def page_repo(monkeypatch,rows,**kwargs):
    store=PageLedger(rows,**kwargs)
    owner=SimpleNamespace(_store=store,_context=lambda value:value)
    monkeypatch.setattr(PostgresNotebookConversationRepository,'_guard',staticmethod(lambda conn,ctx,nb:None))
    return store,PostgresNotebookConversationRepository(owner)


def test_page_same_time_cursor_all_citations_and_completed_over_waiting(monkeypatch):
    from contextlib import nullcontext
    import daon_user_api.notification_events as access
    from daon_user_api.notebook_conversation_postgres import _turn_id
    payload=admission_payload(CTX,'notebook-b','run-b','질문',FP,{})
    turn_id=_turn_id(CTX,'notebook-b','run-b')
    event={'turn_id':turn_id,'run_id':'run-b','request_fingerprint':FP,'event_kind':'awaiting_authorization','text':'추가 인증을 기다리고 있습니다.'}
    citation={'source_id':'source-b','source_version_id':'version-b','evidence_span_id':'span-b','page':1}
    store,repo=page_repo(monkeypatch,[(turn_id,NOW,payload,'run-b','completed',{'question':'질문'}),
        ('run-older',NOW,None,'run-older','failed',{'question':'이전 질문'})],events={turn_id:event},
        citations=[({'answer':'원문 답변','insufficient':False},f'citation-{i}',citation) for i in range(10)])
    monkeypatch.setattr(access,'_snapshot_read_scope',lambda *args:nullcontext())
    checks=[]
    def current(conn,ctx,**kwargs):
        checks.append(kwargs); return SimpleNamespace(actor_id=CTX.actor_id)
    monkeypatch.setattr(access,'_completed_event_access',current)
    page=repo.list_conversation_turns(CTX,'notebook-b',limit=1,before=None)
    item=page['items'][0]
    assert item['state']=='completed' and item['response']['text']=='원문 답변'
    assert len(item['response']['citations'])==10 and item['model']=={'provider_code':'provider-b','model_id':'model/b'}
    assert decode_cursor(CTX,'notebook-b',page['next_cursor'])==(NOW,turn_id)
    assert len(checks)==1 and checks[0]['notebook_id']=='notebook-b'
    assert store.calls[0][1]['size']==2


def test_answer_current_access_boundary_denial_retains_own_question_only(monkeypatch):
    from contextlib import nullcontext
    from daon_user_api.notification import NotificationError
    import daon_user_api.notification_events as access
    store,repo=page_repo(monkeypatch,[('run-b',NOW,None,'run-b','completed',{'question':'본인 원문 질문'})])
    monkeypatch.setattr(access,'_snapshot_read_scope',lambda *args:nullcontext())
    def denied(*args,**kwargs):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    monkeypatch.setattr(access,'_completed_event_access',denied)
    page=repo.list_conversation_turns(CTX,'notebook-b')
    item=page['items'][0]
    assert item['question']=='본인 원문 질문' and item['state']=='unavailable'
    assert item['model'] is None and item['response']['citations']==[]
    assert all(not sql.startswith('SELECT rr.') for sql,_ in store.calls)


def test_existing_run_mismatch_is_not_admitted_or_hidden(ledger_repo):
    ledger,repo,_=ledger_repo
    ledger.existing_run=(CTX.actor_id,{'question':'과거 질문','request_fingerprint':FP},'conversation-b',True)
    with pytest.raises(NotebookError,match='NOTEBOOK_TURN_CONFLICT'):
        admit(repo)
    assert ledger.rows=={} and ledger.bindings=={}


def test_client_request_id_is_exact_immutable_and_old_admission_remains_readable(ledger_repo):
    ledger,repo,_=ledger_repo
    admit(repo,client_request_id='request-key-1')
    with pytest.raises(NotebookError,match='NOTEBOOK_TURN_CONFLICT'):
        admit(repo,client_request_id='request-key-2')
    original=admission_payload(CTX,'notebook-b','run-old','과거',FP,{})
    assert 'client_request_id' not in original
    assert admission_payload(CTX,'notebook-b','run-old','과거',FP,{},None)==original
    for key in ('short',True,'../unsafe'):
        with pytest.raises(NotebookError,match='NOTEBOOK_TURN_INVALID'):
            admission_payload(CTX,'notebook-b','run-old','과거',FP,{},key)
