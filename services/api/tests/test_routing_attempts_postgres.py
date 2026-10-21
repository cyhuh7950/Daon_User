from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from threading import RLock
from types import SimpleNamespace
import json
import pytest
from test_routing_policy import plan_fixture
from daon_user_api.question_answering_postgres import QuestionContext


class SQLDouble:
    """Executed SQL transaction double; does not emulate real PG FK/RLS/trigger enforcement."""
    def __init__(self):
        self.plans={};self.claims={};self.events=[];self.outbox=[];self.canon={};self.states={}
        self.lock=RLock();self.calls=[];self.fail_terminal=False;self.receipts={}
    @contextmanager
    def transaction(self):
        before=deepcopy((self.canon,self.states))
        try:yield self
        except BaseException:
            self.canon,self.states=before
            raise
    def execute(self,sql,params=()):
        self.calls.append((sql,params));one=None;rows=[]
        if sql.startswith('SELECT pg_advisory'):pass
        elif sql.startswith("SELECT payload->'budget' FROM routing_authorization_plans"):
            found=[p for (t,w,_),p in self.plans.items() if (t,w)==tuple(params[:2]) and p['budget']['scope_id']==params[2]]
            one=None if not found else (found[0]['budget'],)
        elif sql.startswith('SELECT plan_id FROM routing_authorization_plans'):
            found=[v for v in self.plans.values() if (v['scope']['tenant_id'],v['scope']['workspace_id'],v['run_id'])==tuple(params)]
            one=None if not found else (found[0]['plan_id'],)
        elif sql.startswith('INSERT INTO routing_authorization_plans'):
            self.plans[tuple(params[:3])]=deepcopy(params[-2].obj)
        elif sql.startswith('SELECT payload,content_digest FROM routing_authorization_plans'):
            p=self.plans.get(tuple(params[:3]));one=None if not p or (p['scope']['actor_id'],p['scope']['session_id'])!=tuple(params[3:]) else (p,self._digest(p))
        elif sql.startswith('SELECT plan_id,request_digest FROM routing_execution_claims'):
            found=[c for c in self.claims.values() if (c[0],c[1],c[3],c[5])==tuple(params)]
            one=None if not found else (found[0][2],found[0][6])
        elif sql.startswith('SELECT plan_id FROM routing_execution_claims'):
            c=self.claims.get(tuple(params[:3]));one=None if c is None or len(params)>3 and tuple(c[3:5])!=tuple(params[3:5]) else (c[2],)
        elif sql.startswith('INSERT INTO routing_execution_claims'): self.claims[tuple(params[:3])]=params
        elif sql.startswith('SELECT state,version,outcome,error_code FROM transition_canon_state'):
            _,run,version,target,*_=params;current=self.states.get(run,('accepted',1))
            assert current[1]==version
            self.states[run]=(target,version+1);one=(target,version+1,'succeeded',None)
        elif sql.startswith('SELECT content_digest FROM routing_attempt_events'):
            found=[v for v in self.events if tuple(v[:5])==tuple(params)]
            one=None if not found else (found[0][6],)
        elif sql.startswith('INSERT INTO routing_attempt_events'):
            if self.fail_terminal and params[4]=='terminal':raise RuntimeError('commit interruption test')
            self.events.append((*params[:5],deepcopy(params[5].obj),params[6]))
        elif sql.startswith('SELECT attempt_id,event_kind,payload FROM routing_attempt_events'):
            rows=[(v[3],v[4],deepcopy(v[5])) for v in self.events if tuple(v[:3])==tuple(params)]
        elif sql.startswith('SELECT e.attempt_id,e.event_kind,e.payload FROM routing_attempt_events'):
            rows=[(v[3],v[4],deepcopy(v[5])) for v in self.events if v[0]==params[0] and v[1]==params[1]
                and self.plans[tuple(v[:3])]['budget']['scope_id']==params[2]]
        elif sql.startswith('INSERT INTO routing_dispatch_outbox'): self.outbox.append(params)
        elif sql.startswith('SELECT candidate_order,created_at FROM routing_dispatch_outbox'):
            found=[r for r in self.outbox if tuple(r[:4])==tuple(params)]
            one=None if not found else (found[0][4],datetime(2026,10,21,12,tzinfo=timezone.utc))
        elif sql.startswith('INSERT INTO routing_billing_receipts'):
            key=tuple(params[:5])
            if key not in self.receipts:self.receipts[key]=tuple(params[5:9]);one=self.receipts[key]
        elif sql.startswith('SELECT workspace_id,plan_id,attempt_id,content_digest FROM routing_billing_receipts'):
            one=self.receipts.get(tuple(params))
        elif sql.startswith('SELECT rr.record_id,rr.canonical_json'):
            rows=[]
        elif sql.startswith('SELECT canonical_text,digest_sha256 FROM '):
            table=sql.split(' FROM ')[1].split()[0];one=self.canon.get((table,*params))
        elif sql.startswith('INSERT INTO model_attempts'):
            columns=sql.split('(',1)[1].split(')',1)[0].split(',');record=dict(zip(columns,params))
            self.canon[('model_attempts',*params[:3])]=record
            one=tuple(record[k] for k in sql.split(' RETURNING ')[1].split(','))
        else:raise AssertionError(sql)
        return SimpleNamespace(fetchone=lambda:deepcopy(one),fetchall=lambda:deepcopy(rows))
    @staticmethod
    def _digest(value):
        from daon_user_api.routing_policy import digest
        return digest(value)


class SQLStore:
    def __init__(self):self.db=SQLDouble()
    @contextmanager
    def _transaction(self,context):
        with self.db.lock:
            before=deepcopy((self.db.plans,self.db.claims,self.db.events,self.db.outbox,self.db.canon,self.db.states,self.db.receipts))
            try:yield self.db
            except BaseException:
                self.db.plans,self.db.claims,self.db.events,self.db.outbox,self.db.canon,self.db.states,self.db.receipts=before
                raise


class CandidatePort:
    def __init__(self,route,now):
        self.route=route;self.now=now;self.calls=[];self.outcomes=[];self.revoked=False;self.drift=False
        self.result=SimpleNamespace(answer='qa answer',run_id='run-auto',run_result_id='result-auto',citations=(),insufficient=False)
    def freeze(self,ctx,item):
        from daon_user_api.routing_policy import digest
        c=item['candidate'];wire='f'*64
        quote=dict(maximum_estimate='0.20',currency='USD',pricing_reference='qa-price',price_version='v1',
            candidate_reference_digest=digest(c),protocol_version='chat-v1',transformed_payload_digest=wire,
            expires_at=(self.now+timedelta(seconds=50)).isoformat(),
            billing_reference=dict(scope_id='qa-billing',provider_code='OLLAMA',account_reference='d'*64))
        return dict(candidate=c,provider_kind='local',provider_code='OLLAMA',artifact_digest='sha256:'+'1'*64,
            license_reference='qa-license',endpoint_digest='e'*64,protocol_version='chat-v1',
            transformed_payload_digest=wire,payload_bytes=100,egress_policy_digest='a'*64,
            authorization_mode='policy_approved',required_approver='workspace_manager',quote=quote)
    def budget(self,ctx,route):
        return dict(scope_id='shared-budget-a',source_id='qa-budget',version='v1',currency='USD',run_limit='1.00',
            period_limit='1.00',starts_at=(self.now-timedelta(minutes=1)).isoformat(),
            expires_at=(self.now+timedelta(seconds=60)).isoformat(),max_attempts=len(route['candidates']))
    def parents(self,ctx,route,entries,run_id,request_digest):
        return dict(conversation_id='conversation-'+run_id,scope_id='scope-'+run_id,scope_snapshot_id='scope-snapshot-'+run_id,
            routing_decision_id='decision-'+run_id,run_snapshot_id='snapshot-'+run_id,run_result_id='result-'+run_id,
            egress_decision_ids=['egress-'+run_id+str(i) for i,_ in enumerate(entries)],
            models=[dict(profile_id='provider-'+str(i),deployment_id='deployment-'+str(i),artifact_id='artifact-'+str(i)) for i,_ in enumerate(entries)])
    def materialize(self,conn,ctx,plan):conn.states[plan.as_dict()['run_id']]=('accepted',1)
    def recheck(self,conn,ctx,plan):
        from daon_user_api.routing_execution import RoutingExecutionError
        if self.revoked:raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403)
    @contextmanager
    def open(self,ctx,plan,entry):
        claims=deepcopy(entry)
        if self.drift:claims['candidate']['credential_version']+=1
        def invoke():
            self.calls.append(deepcopy(entry))
            return self.outcomes.pop(0)
        yield SimpleNamespace(claims=claims,invoke=invoke)
    def write_result(self,conn,ctx,plan,order,attempt,result):
        from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository as Q
        version=Q._transition(conn,ctx,plan.as_dict()['run_id'],4,'validating')
        Q._transition(conn,ctx,plan.as_dict()['run_id'],version,'completed')
        return result


@pytest.fixture
def execution():
    from daon_user_api.routing_execution import RoutingExecutionService,PlanApproval
    from daon_user_api.routing_attempts_postgres import PostgresRoutingAttemptsRepository
    from dataclasses import replace
    m,_,req,policy,c,o,now=plan_fixture()
    ctx=QuestionContext('tenant-a','workspace-a','actor-a','trace-a','policy-a','notebook-a','session-a')
    route=m.build_route_plan(ctx,req,policy,(c,),(o,),now=now)
    store=SQLStore();port=CandidatePort(route.as_dict(),now)
    repo=PostgresRoutingAttemptsRepository(store,clock=lambda:now)
    service=RoutingExecutionService(repo,port,clock=lambda:now)
    approve=lambda plan,required:PlanApproval(plan.digest_sha256,ctx.actor_id,ctx.session_id,'policy_approved')
    authorization=service.prepare(ctx,route,'b'*64,'run-auto',authorize_plan=approve)
    return SimpleNamespace(m=m,ctx=ctx,route=route,now=now,store=store,port=port,repo=repo,service=service,approval=authorization,approve=approve)


def test_real_execution_repository_consumes_permit_and_dispatches_at_most_once(execution,monkeypatch):
    from daon_user_api.routing_execution import AttemptOutcome,RoutingExecutionError
    f=execution;f.port.outcomes=[AttemptOutcome('succeeded',cost_state='known',amount='0.10',currency='USD',receipt_id='receipt-a',result=f.port.result)]
    kwargs=dict(plan_id=f.approval.plan_id,authorization=f.approval.authorization,request_digest='b'*64,idempotency_key='execute-a')
    answer=f.service.execute(f.ctx,**kwargs)
    assert answer.answer=='qa answer' and len(f.port.calls)==1
    monkeypatch.setattr(f.repo,'result',lambda ctx,plan:f.port.result)
    assert f.service.execute(f.ctx,**kwargs).answer=='qa answer' and len(f.port.calls)==1
    assert len(f.store.db.outbox)==1 and len(f.store.db.canon)==1
    assert f.store.db.states['run-auto']==('completed',6)
    with pytest.raises(RoutingExecutionError):f.service.execute(f.ctx,**{**kwargs,'idempotency_key':'different-key'})
    with pytest.raises(RoutingExecutionError):f.service.execute(f.ctx,**{**kwargs,'authorization':'single-model-token'})


def test_current_acl_revocation_or_candidate_drift_causes_zero_calls(execution):
    from daon_user_api.routing_execution import RoutingExecutionError
    f=execution;f.port.revoked=True
    with pytest.raises(RoutingExecutionError):f.service.execute(f.ctx,plan_id=f.approval.plan_id,
        authorization=f.approval.authorization,request_digest='b'*64,idempotency_key='execute-a')
    assert not f.port.calls and not f.store.db.claims


def test_exact_canonical_replay_must_compare_actual_foreign_keys():
    from daon_user_api.routing_attempts_postgres import insert_canon_exact
    from daon_user_api.routing_execution import RoutingExecutionError
    from daon_user_api.routing_policy import digest
    from daon_user_api.data_canon import canonical_json_bytes
    ctx=QuestionContext('tenant-a','workspace-a','actor-a','trace-a','policy-a')
    payload={'run_id':'run-a'};text=canonical_json_bytes(payload).decode();sha=digest(payload)
    class Existing:
        def execute(self,sql,params=()):
            if sql.startswith('INSERT '):return SimpleNamespace(fetchone=lambda:None)
            assert sql.startswith('SELECT ')
            row=(text,sha,'attempt-a',1,1,'actor-a','original-trace','wrong-decision')
            return SimpleNamespace(fetchone=lambda:row)
    with pytest.raises(RoutingExecutionError):
        insert_canon_exact(Existing(),ctx,'model_attempts','attempt-a',payload,
            extra_columns=('routing_decision_id',),extra_values=('expected-decision',))


def test_actual_frozen_policy_attempt_limit_cannot_be_expanded(execution):
    from dataclasses import replace
    from daon_user_api.routing_policy import FrozenRoutePlan,digest
    from daon_user_api.data_canon import canonical_json_bytes
    from daon_user_api import routing_execution as m
    f=execution;body=f.route.as_dict()
    body['candidates']=body['candidates']*2;body['candidates'][1]=dict(body['candidates'][1])
    body['budget']['max_attempts']=1
    route=FrozenRoutePlan(canonical_json_bytes(body).decode(),digest(body))
    with pytest.raises(m.RoutingExecutionError):
        f.service.prepare(f.ctx,route,'c'*64,'another-run',authorize_plan=f.approve)


def test_terminal_requires_its_exact_durable_dispatch(execution):
    from daon_user_api.routing_execution import AttemptOutcome,RoutingExecutionError
    f=execution;plan=f.repo.read(f.ctx,f.approval.plan_id)
    # A valid permit/Run is not an Attempt dispatch authorization.
    with pytest.raises(RoutingExecutionError):
        f.repo.finish(f.ctx,plan,1,'not-dispatched',AttemptOutcome('failed','INVALID_RESPONSE'),
            final=True,write_result=f.port.write_result)
    assert not f.store.db.events and not f.store.db.canon


def test_same_receipt_cannot_bill_two_attempts(execution):
    from daon_user_api.routing_execution import AttemptOutcome,RoutingExecutionError
    f=execution;plan=f.repo.read(f.ctx,f.approval.plan_id)
    # Direct receipt ledger contract is independent of outcome persistence retries.
    receipt=AttemptOutcome('failed','RATE_LIMITED','known','0.10','USD','provider-receipt-a',no_effect_uncertainty=True)
    with f.store._transaction(None) as conn:
        f.repo._bind_receipt(conn,f.ctx,plan,'attempt-a',1,receipt)
        with pytest.raises(RoutingExecutionError):
            f.repo._bind_receipt(conn,f.ctx,plan,'attempt-b',1,receipt)


def make_execution_fixture(*,multiple=False,period_limit='1.00'):
    from dataclasses import replace
    from daon_user_api.routing_execution import RoutingExecutionService,PlanApproval
    from daon_user_api.routing_attempts_postgres import PostgresRoutingAttemptsRepository
    m,_,req,policy,c,o,now=plan_fixture()
    ctx=QuestionContext('tenant-a','workspace-a','actor-a','trace-a','policy-a','notebook-a','session-a')
    candidates=(c,);observations=(o,)
    if multiple:
        c2=replace(c,model_id='model-b',deployment_id='deployment-b')
        o2=replace(o,deployment_id=c2.deployment_id,candidate_reference_digest=m.digest(c2.references()))
        policy['body']['candidates'].append({**policy['body']['candidates'][0],'model_id':'model-b'})
        policy['body']['budget']['max_attempts']=2;policy['digest_sha256']=m.digest(policy['body'])
        candidates=(c,c2);observations=(o,o2)
    route=m.build_route_plan(ctx,req,policy,candidates,observations,now=now)
    store=SQLStore();port=CandidatePort(route.as_dict(),now)
    original=port.budget
    port.budget=lambda context,route:{**original(context,route),'period_limit':period_limit}
    repo=PostgresRoutingAttemptsRepository(store,clock=lambda:now)
    service=RoutingExecutionService(repo,port,clock=lambda:now)
    approve=lambda plan,required:PlanApproval(plan.digest_sha256,ctx.actor_id,ctx.session_id,'policy_approved')
    auth=service.prepare(ctx,route,'b'*64,'run-auto',authorize_plan=approve)
    return SimpleNamespace(m=m,ctx=ctx,route=route,now=now,store=store,port=port,repo=repo,service=service,approval=auth,approve=approve)


def execute(f,key='execute-a'):
    return f.service.execute(f.ctx,plan_id=f.approval.plan_id,authorization=f.approval.authorization,
        request_digest='b'*64,idempotency_key=key)


@pytest.mark.parametrize('safe_code,known,partial,no_effect,expected_calls',[
    ('RATE_LIMITED',True,False,True,2),('TEMPORARILY_UNAVAILABLE',True,False,True,2),
    ('CAPACITY_UNAVAILABLE',True,False,True,2),('TIMEOUT',True,False,True,2),
    ('TIMEOUT',False,False,False,1),('RATE_LIMITED',False,False,True,1),
    ('AUTH_DENIED',True,False,True,1),('EGRESS_POLICY_DENIED',True,False,True,1),
    ('INVALID_REQUEST',True,False,True,1),('INVALID_RESPONSE',True,False,True,1),
    ('RATE_LIMITED',True,True,True,1)])
def test_only_exact_settled_transient_failure_allows_next_candidate(safe_code,known,partial,no_effect,expected_calls):
    from daon_user_api.routing_execution import AttemptOutcome,RoutingExecutionError
    f=make_execution_fixture(multiple=True)
    first=AttemptOutcome('failed',safe_code,'known' if known else 'uncertain',
        '0.10' if known else None,'USD' if known else None,'receipt-first' if known else None,
        partial_output=partial,no_effect_uncertainty=no_effect)
    f.port.outcomes=[first,AttemptOutcome('succeeded',result=f.port.result)]
    if expected_calls==2:assert execute(f).answer=='qa answer'
    else:
        with pytest.raises(RoutingExecutionError):execute(f)
    assert len(f.port.calls)==expected_calls
    assert len(f.store.db.outbox)==expected_calls


def test_claim_commit_then_process_interruption_never_replays_dispatch():
    from daon_user_api.routing_execution import RoutingExecutionError
    f=make_execution_fixture(multiple=True)
    @contextmanager
    def open_crash(ctx,plan,entry):
        def crash():f.port.calls.append(entry);raise KeyboardInterrupt('simulated process interruption')
        yield SimpleNamespace(claims=entry,invoke=crash)
    f.port.open=open_crash
    with pytest.raises(KeyboardInterrupt):execute(f)
    assert len(f.port.calls)==1 and len(f.store.db.outbox)==1
    terminals=[v[5] for v in f.store.db.events if v[4]=='terminal']
    assert terminals[0]['outcome']=='uncertain' and terminals[0]['amount'] is None
    with pytest.raises(RoutingExecutionError):execute(f)
    assert len(f.port.calls)==1


def test_unknown_cost_reservation_blocks_another_run_and_late_receipt_settles_once():
    from daon_user_api.routing_execution import AttemptOutcome,RoutingExecutionError
    f=make_execution_fixture(period_limit='0.20')
    f.port.outcomes=[AttemptOutcome('uncertain','TIMEOUT','uncertain')]
    with pytest.raises(RoutingExecutionError):execute(f)
    auth2=f.service.prepare(f.ctx,f.route,'c'*64,'run-next',authorize_plan=f.approve)
    with pytest.raises(RoutingExecutionError) as error:
        f.service.execute(f.ctx,plan_id=auth2.plan_id,authorization=auth2.authorization,
            request_digest='c'*64,idempotency_key='execute-next')
    assert error.value.code=='COST_LIMIT_EXCEEDED' and len(f.port.calls)==1
    plan=f.repo.read(f.ctx,f.approval.plan_id)
    late=AttemptOutcome('failed','TIMEOUT','known','0.10','USD','late-invoice')
    assert f.repo.reconcile(f.ctx,plan,1,late) is True
    assert f.repo.reconcile(f.ctx,plan,1,late) is False
    assert len(f.store.db.receipts)==1
    before=deepcopy(f.store.db.events)
    with pytest.raises(RoutingExecutionError):
        f.repo.reconcile(f.ctx,plan,1,AttemptOutcome('failed','TIMEOUT','known','0.11','USD','late-invoice'))
    assert f.store.db.events==before
    attempt=next(iter(f.store.db.canon.values()))
    assert attempt['cost_amount'] is None


def test_candidate_change_after_prepare_prevents_dispatch(execution):
    from daon_user_api.routing_execution import RoutingExecutionError
    f=execution;f.port.drift=True
    with pytest.raises(RoutingExecutionError):execute(f)
    assert not f.port.calls and not f.store.db.outbox


def test_lost_terminal_transaction_keeps_reservation_and_never_resends(execution):
    from daon_user_api.routing_execution import AttemptOutcome,RoutingExecutionError
    f=execution;f.port.outcomes=[AttemptOutcome('succeeded',result=f.port.result)];f.store.db.fail_terminal=True
    with pytest.raises(RuntimeError):execute(f)
    assert len(f.port.calls)==1 and len(f.store.db.outbox)==1
    f.store.db.fail_terminal=False
    with pytest.raises(RoutingExecutionError):execute(f)
    assert len(f.port.calls)==1


def test_revocation_during_provider_call_suppresses_answer_but_keeps_attempt_and_actual_cost(execution):
    from daon_user_api.routing_execution import AttemptOutcome,RoutingExecutionError
    f=execution;f.port.outcomes=[AttemptOutcome('succeeded',cost_state='known',amount='0.12',currency='USD',receipt_id='receipt-after-revoke',result=f.port.result)]
    def revoked_writer(*args):raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403)
    f.port.write_result=revoked_writer
    with pytest.raises(RoutingExecutionError):execute(f)
    assert len(f.port.calls)==1
    assert len(f.store.db.receipts)==1 and len(f.store.db.canon)==1
    assert next(iter(f.store.db.canon.values()))['cost_amount']==__import__('decimal').Decimal('0.12')
    assert f.store.db.states['run-auto'][0]=='failed'


@pytest.mark.parametrize('changed',[
    {'fingerprint':'sha256:'+'f'*64,'question_step_up_required':False,'required_approver':'workspace_manager'},
    {'fingerprint':'sha256:'+'a'*64,'question_step_up_required':True,'required_approver':'workspace_manager'},
    {'fingerprint':'sha256:'+'a'*64,'question_step_up_required':False,'required_approver':'organization_admin'}])
def test_claim_rechecks_exact_current_egress_mode_and_approver(execution,changed):
    from daon_user_api.routing_execution import _ScopedQuestionPort,RoutingExecutionError
    from test_routing_policy import policy_body
    f=execution;plan=f.repo.read(f.ctx,f.approval.plan_id);p=plan.as_dict()
    from daon_user_api.routing_execution import FrozenAuthorizationPlan
    from daon_user_api.routing_policy import digest
    from daon_user_api.data_canon import canonical_json_bytes
    p['candidates'][0]['provider_kind']='external_api';p['candidates'][0]['provider_code']='GROQ'
    p['candidates'][0]['quote']['billing_reference']['provider_code']='GROQ'
    plan=FrozenAuthorizationPlan(canonical_json_bytes(p).decode(),digest(p))
    port=_ScopedQuestionPort.__new__(_ScopedQuestionPort)
    port.source_digest=p['source_scope_digest'];port.settings_digest=p['settings_digest'];port.request_digest=p['request_digest']
    port.owner=SimpleNamespace(clock=lambda:f.now,policy_service=SimpleNamespace(repository=SimpleNamespace(
        _current=lambda conn,ctx:('policy-v1',1,policy_body(),p['policy_digest'])),metadata=SimpleNamespace(candidates=lambda *args,**kw:())))
    port._current_access=lambda conn,ctx:None
    port.budget=lambda ctx,route:p['budget']
    port._current_egress=lambda conn,ctx:SimpleNamespace(**changed)
    conn=SimpleNamespace(execute=lambda *args:SimpleNamespace(fetchone=lambda:('cloud_sync',)))
    with pytest.raises(RoutingExecutionError) as error:port.recheck(conn,f.ctx,plan)
    assert error.value.status==403


def test_repository_rejects_forged_plan_snapshot_and_changed_claim_request(execution):
    from daon_user_api.routing_execution import FrozenAuthorizationPlan,RoutingExecutionError
    from daon_user_api.data_canon import canonical_json_bytes
    from daon_user_api.routing_policy import digest
    f=execution;original=f.repo.read(f.ctx,f.approval.plan_id);p=original.as_dict()
    p['budget']['period_limit']='2.00'
    forged=FrozenAuthorizationPlan(canonical_json_bytes(p).decode(),digest(p))
    with pytest.raises(RoutingExecutionError):
        f.repo.claim(f.ctx,forged,idempotency_key='forged',request_digest='b'*64,recheck=f.port.recheck)
    with pytest.raises(RoutingExecutionError):
        f.repo.claim(f.ctx,original,idempotency_key='changed-request',request_digest='f'*64,recheck=f.port.recheck)
    assert not f.store.db.claims


def test_no_second_dispatch_after_plan_expires_during_first_attempt():
    from daon_user_api.routing_execution import AttemptOutcome,RoutingExecutionError
    f=make_execution_fixture(multiple=True)
    current=[f.now];f.repo.clock=lambda:current[0];f.service.clock=lambda:current[0]
    original=f.port.open
    @contextmanager
    def advancing(ctx,plan,entry):
        with original(ctx,plan,entry) as call:
            def invoke():
                value=call.invoke();current[0]=f.now+timedelta(seconds=51);return value
            yield SimpleNamespace(claims=call.claims,invoke=invoke)
    f.port.open=advancing
    f.port.outcomes=[AttemptOutcome('failed','RATE_LIMITED','known','0.1','USD','receipt-a',no_effect_uncertainty=True),
        AttemptOutcome('succeeded',result=f.port.result)]
    with pytest.raises(RoutingExecutionError):execute(f)
    assert len(f.port.calls)==1


def select_insert_only_execution_tables(db):
    """실PG42501 재현용 ACL 대역. 행 잠금 외의 SQL 처리·원자성 대역은 유지한다."""
    execute=db.execute
    def restricted(sql,params=()):
        if sql.startswith('SELECT ') and ' FOR SHARE' in sql and any(
                ' FROM '+table+' ' in sql for table in ('routing_authorization_plans','routing_billing_receipts')):
            from daon_user_api.cloud_storage import CloudDatabaseError
            raise CloudDatabaseError('CURRENT_ACCESS_DENIED',retryable=False)
        return execute(sql,params)
    db.execute=restricted


def test_plan_claim_with_select_insert_only_acl_keeps_exact_replay(execution):
    f=execution;select_insert_only_execution_tables(f.store.db)
    plan=f.repo.read(f.ctx,f.approval.plan_id)
    options=dict(idempotency_key='qa-select-only-claim',request_digest='b'*64,recheck=f.port.recheck)
    assert f.repo.claim(f.ctx,plan,**options) is True
    assert f.repo.claim(f.ctx,plan,**options) is False
    assert len(f.store.db.claims)==1


def test_same_receipt_replay_with_select_insert_only_acl_does_not_need_update(execution):
    from daon_user_api.routing_execution import AttemptOutcome
    f=execution;plan=f.repo.read(f.ctx,f.approval.plan_id)
    f.repo.claim(f.ctx,plan,idempotency_key='qa-receipt-claim',request_digest='b'*64,recheck=f.port.recheck)
    attempt=f.repo.claim_attempt(f.ctx,plan,1,recheck=f.port.recheck)
    outcome=AttemptOutcome('failed','AUTH_DENIED','known','0.12','USD','qa-select-only-receipt')
    f.repo.finish(f.ctx,plan,1,attempt,outcome,final=True,write_result=None)
    select_insert_only_execution_tables(f.store.db)
    assert f.repo.reconcile(f.ctx,plan,1,outcome) is True
    assert f.repo.reconcile(f.ctx,plan,1,outcome) is False
    assert len(f.store.db.receipts)==1
    assert sum(event[4]=='reconciled' for event in f.store.db.events)==1
