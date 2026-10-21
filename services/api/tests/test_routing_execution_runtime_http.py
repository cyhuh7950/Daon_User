"""HTTP + real service/auto coordinator/PG adapter; SQL and trusted producers are doubles.
The access-port double is separate from the real SQLite HTTP authorization fixture.
No network, PostgreSQL server, provider or real credential is used.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
import hashlib,json,unittest
import httpx
import test_question_answering_runtime_http as runtime_fixture
from test_question_answering_service import FakeRepository,FakeIndex,FakeCredential
from test_routing_attempts_postgres import SQLDouble,SQLStore
from test_routing_policy import plan_fixture
from daon_user_api.runtime import create_app,WEB_SESSION_COOKIE
from daon_user_api.routing_execution import QuestionAutoExecution,_ScopedQuestionPort,RoutingExecutionError
from daon_user_api.routing_attempts_postgres import PostgresRoutingAttemptsRepository
from daon_user_api.routing_policy import RoutingPolicyService,digest
from daon_user_api.question_answering_service import QuestionAnsweringService
from daon_user_api.workspace_model_defaults import ResolvedModel

class BridgeSQL(SQLDouble):
    def execute(self,sql,params=()):
        if sql.startswith('SELECT data_area FROM identity_auth_workspaces'):
            return SimpleNamespace(fetchone=lambda:('cloud_sync',))
        if sql.startswith('INSERT INTO notebook_bindings'):
            return SimpleNamespace(fetchone=lambda:None)
        if sql.startswith('INSERT INTO ') and ' RETURNING canonical_text,' in sql:
            table=sql.split()[2];columns=sql.split('(',1)[1].split(')',1)[0].split(',')
            record=dict(zip(columns,params));key=(table,*params[:3])
            if key in self.canon:return SimpleNamespace(fetchone=lambda:None)
            self.canon[key]=record
            if table=='runs':self.states[params[2]]=('accepted',1)
            return SimpleNamespace(fetchone=lambda:tuple(record[k] for k in sql.split(' RETURNING ')[1].split(',')))
        if sql.startswith('SELECT canonical_text,digest_sha256,aggregate_id'):
            table=sql.split(' FROM ')[1].split()[0];record=self.canon.get((table,*params))
            fields=sql[7:].split(' FROM ')[0].split(',')
            return SimpleNamespace(fetchone=lambda:None if record is None else tuple(record[k] for k in fields))
        if sql.startswith('SELECT rr.record_id,rr.canonical_json'):
            rows=[]
            if self.states.get(params[0],('',0))[0]=='completed':
                for (table,*_),record in self.canon.items():
                    if table=='run_results' and record['run_id']==params[0]:
                        rows.append((record['record_id'],record['canonical_json'].obj,None,None,None,None))
            return SimpleNamespace(fetchall=lambda:rows)
        return super().execute(sql,params)

class AutoRuntimeTests(unittest.IsolatedAsyncioTestCase):
    _authenticated=runtime_fixture.QuestionRuntimeHttpTests._authenticated
    async def asyncSetUp(self):
        await runtime_fixture.QuestionRuntimeHttpTests.asyncSetUp(self)
        await self.client.aclose()
        m,_,_,policy,c,o,self.now=plan_fixture();self.candidate=c;self.policy=policy
        self.store=SQLStore();self.store.db=BridgeSQL();self.revoked=False;self.changed=False;self.calls=[]
        owner=self
        class Metadata:
            def candidates(self,*args,**kwargs):return (c,)
            def observations(self,ctx,request,candidates):return (replace(o,input_payload_digest=request.payload_digest),)
        repository=SimpleNamespace(get=lambda ctx:policy,_current=lambda conn,ctx:('policy-v1',1,policy['body'],policy['digest_sha256']))
        policies=RoutingPolicyService(repository,Metadata(),clock=lambda:self.now)
        self.effective=SimpleNamespace(fingerprint='sha256:'+'a'*64,question_step_up_required=False,required_approver='workspace_manager')
        class Resolver:
            @contextmanager
            def resolve_candidate(self,ctx,candidate):
                yield ResolvedModel(connection_id=c.connection_id,provider_code='CUSTOM',model_id=c.model_id,
                    capability='text_generation',base_url='https://fixture.invalid/v1',credential_version=0,
                    default_version=1,catalog_version=1,provider_kind='server_internal',routing_owner='provider',
                    daon_fallback_allowed=True,_credential=None,adapter_type='openai_compatible')
        class Pricing:
            def quote(self,ctx,candidate,wire,protocol,expected=None):
                return dict(maximum_estimate='0.20',currency='USD',pricing_reference='qa-price',price_version='v1',
                    candidate_reference_digest=digest(candidate),protocol_version=protocol,transformed_payload_digest=hashlib.sha256(wire).hexdigest(),
                    expires_at=(owner.now+timedelta(seconds=50)).isoformat(),
                    billing_reference=dict(scope_id='qa-billing',provider_code='CUSTOM',account_reference='d'*64))
        def budget(ctx,route):
            return dict(scope_id='qa-budget-scope',source_id='qa-budget',version='v1',currency='USD',run_limit='1.00',period_limit='1.00',
                starts_at=(self.now-timedelta(seconds=10)).isoformat(),expires_at=(self.now+timedelta(seconds=60)).isoformat(),max_attempts=1)
        class Transport:
            def post_json_no_auth(self,**kwargs):
                owner.calls.append(deepcopy(kwargs))
                if getattr(owner,'revoke_during_call',False):owner.revoked=True
                return {'choices':[{'message':{'content':json.dumps({'answer':'QA auto answer'})}}]}
        egress=SimpleNamespace(prepare_payload=lambda ctx,wire:wire,_policy_service=SimpleNamespace(get_effective=lambda ctx:self.effective))
        self.attempts=PostgresRoutingAttemptsRepository(self.store,clock=lambda:self.now)
        self.auto=QuestionAutoExecution(policies,self.attempts,Resolver(),self.store,pricing_port=Pricing(),budget_source=budget,clock=lambda:self.now)
        self.service=QuestionAnsweringService(Resolver(),FakeRepository(),FakeIndex(()),FakeCredential(),Transport(),egress,auto_execution=self.auto)
        self.dependencies=replace(self.dependencies,question_answering_service=self.service,egress_policy_service=egress._policy_service)
        def access(port,conn,ctx):
            if self.revoked:raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403)
        def current_egress(conn,ctx):
            return SimpleNamespace(fingerprint='sha256:'+('f' if self.changed else 'a')*64,question_step_up_required=False,required_approver='workspace_manager')
        self.patches=[patch.object(_ScopedQuestionPort,'_current_access',access),
            patch.object(_ScopedQuestionPort,'_current_egress',staticmethod(current_egress)),
            patch('daon_user_api.open_law_source_access._current_auth_grant',return_value=SimpleNamespace(role=SimpleNamespace())),
            patch('daon_user_api.open_law_source_access._CurrentAuthorizationRules._effective_permission',return_value=SimpleNamespace(effective=True))]
        for item in self.patches:item.start()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dependencies)),base_url='http://127.0.0.1')
    async def asyncTearDown(self):
        for item in reversed(self.patches):item.stop()
        await runtime_fixture.QuestionRuntimeHttpTests.asyncTearDown(self)
    async def request(self,path,body,key):
        with self._authenticated():
            return await self.client.post('/api/v1/workspaces/workspace-001/questions'+path,json=body,
                headers={'Idempotency-Key':key},cookies={WEB_SESSION_COOKIE:'fixture-access-token'})
    async def prepare(self,key='question-auto-api-0001'):
        self.body=dict(notebook_id='notebook-cp3',question='QA question',routing_mode='auto',routing_policy_version_id='policy-v1')
        response=await self.request('/authorization',self.body,key)
        self.assertEqual(response.status_code,201,response.text)
        proof=response.json()['data'];self.assertFalse(proof['requires_step_up'])
        self.assertEqual(set(proof),{'routing_plan_id','routing_plan_authorization','expires_at','run_id','plan_digest','requires_step_up'})
        return {**self.body,**{k:proof[k] for k in ('routing_plan_id','routing_plan_authorization')}}
    async def test_real_service_bridge_executes_and_exact_replay_never_sends_twice(self):
        body=await self.prepare();response=await self.request('',body,'question-auto-api-0001')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('QA auto answer',response.text);self.assertEqual(len(self.calls),1)
        replay=await self.request('',body,'question-auto-api-0001');self.assertEqual(replay.status_code,200,replay.text)
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.store.db.outbox),1)
        self.assertEqual(sum(k[0]=='model_attempts' for k in self.store.db.canon),1)
        self.assertEqual(sum(k[0]=='run_results' for k in self.store.db.canon),1)
        changed=await self.request('',{**body,'question':'changed'},'question-auto-api-0001');self.assertGreaterEqual(changed.status_code,400)
        self.assertEqual(len(self.calls),1)
    async def test_current_egress_change_after_prepare_prevents_transport(self):
        body=await self.prepare();self.changed=True;response=await self.request('',body,'question-auto-api-0001')
        self.assertEqual(response.status_code,403,response.text);self.assertEqual(len(self.calls),0)
    async def test_current_access_revocation_after_prepare_prevents_transport(self):
        body=await self.prepare();self.revoked=True;response=await self.request('',body,'question-auto-api-0001')
        self.assertEqual(response.status_code,403,response.text);self.assertEqual(len(self.calls),0)
    async def test_response_access_revocation_suppresses_answer_but_keeps_actual_attempt(self):
        body=await self.prepare();self.revoke_during_call=True;response=await self.request('',body,'question-auto-api-0001')
        self.assertEqual(response.status_code,403,response.text);self.assertNotIn('QA auto answer',response.text)
        self.assertEqual(len(self.calls),1);self.assertEqual(sum(k[0]=='model_attempts' for k in self.store.db.canon),1)
        self.assertEqual(sum(k[0]=='run_results' for k in self.store.db.canon),0)
        self.assertEqual(next(iter(self.store.db.states.values()))[0],'failed')


class PinnedCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=runtime_fixture.QuestionRuntimeHttpTests.asyncSetUp
    asyncTearDown=runtime_fixture.QuestionRuntimeHttpTests.asyncTearDown
    _authenticated=runtime_fixture.QuestionRuntimeHttpTests._authenticated
    async def test_omitted_and_explicit_pinned_keep_identical_original_logical_fingerprint(self):
        body={'notebook_id':'notebook-cp3','question':'QA question'}
        with self._authenticated():
            for extra in ({},{'routing_mode':'pinned'}):
                response=await self.client.post('/api/v1/workspaces/workspace-001/questions',json={**body,**extra},
                    headers={'Idempotency-Key':'question-pinned-0001'},cookies={WEB_SESSION_COOKIE:'fixture-access-token'})
                self.assertEqual(response.status_code,200,response.text)
        calls=[kw for ctx,kw in self.service.calls]
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0]['request_fingerprint'],calls[1]['request_fingerprint'])
        self.assertEqual(calls[0]['request_payload'],calls[1]['request_payload'])
        self.assertEqual(calls[0]['request_payload'],body)


import test_question_authorization_runtime_http as identity_fixture
from daon_user_api.identity import IdentityError
from daon_user_api.authorization import Role
from daon_user_api.routing_execution import PreparedAutoAuthorization

class PlanIdentityBoundary:
    """Only Plan/backend substituted: runtime and Identity SQLite reauth are real."""
    def __init__(self):self.permits=[];self.plan=None;self.fail_cloud=False
    def prepare_authorization(self,ctx,**kwargs):
        value=dict(plan_id='plan-identity-test',candidates=[dict(authorization_mode='step_up_required',required_approver='organization_admin')])
        plan=SimpleNamespace(as_dict=lambda:value,digest_sha256=digest(value));self.plan=plan
        proof=kwargs['authorize_plan'](plan,True)
        assert proof.plan_digest==plan.digest_sha256 and proof.actor_id==ctx.actor_id and proof.session_id==ctx.session_id
        if self.fail_cloud:raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
        self.permits.append(proof)
        return PreparedAutoAuthorization(value['plan_id'],'synthetic-one-use-permit','2026-10-21T12:05:00+00:00',kwargs['run_id'],plan.digest_sha256,True)

class PlanReauthenticationTests(unittest.IsolatedAsyncioTestCase):
    login=identity_fixture.S18QuestionAuthorizationTests.login
    post=identity_fixture.S18QuestionAuthorizationTests.post
    consumption_count=identity_fixture.S18QuestionAuthorizationTests.consumption_count
    asyncTearDown=identity_fixture.S18QuestionAuthorizationTests.asyncTearDown
    async def asyncSetUp(self):
        await identity_fixture.S18QuestionAuthorizationTests.asyncSetUp(self)
        await self.client.aclose();self.boundary=PlanIdentityBoundary()
        self.dependencies=replace(self.dependencies,question_answering_service=self.boundary)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dependencies)),base_url='http://127.0.0.1')
        self.body={**identity_fixture.BODY,'routing_mode':'auto','routing_policy_version_id':'policy-v1'}
    async def test_required_plan_reauth_missing_and_wrong_password_issue_no_permit(self):
        for extra,status in (({},401),({'password':'wrong synthetic password'},403)):
            response=await self.post(authorization=True,body={**self.body,**extra})
            self.assertEqual(response.status_code,status,response.text)
        self.assertEqual(self.boundary.permits,[]);self.assertEqual(self.consumption_count(),0)
    async def test_correct_password_consumes_only_exact_plan_and_digest_once(self):
        original=self.identity.consume_step_up;observed=[]
        def consume(**kwargs):
            observed.append({k:v for k,v in kwargs.items() if k not in {'access_token','step_up_authorization'}})
            for change in ({'target_id':'different-plan'},{'policy_version':'f'*64}):
                with self.assertRaises(IdentityError):original(**{**kwargs,**change})
            original(**kwargs)
            original(**kwargs)  # exact same operation/key is an idempotent receipt replay
            with self.assertRaises(IdentityError):original(**{**kwargs,"idempotency_key":"different-consume-key-0001"})
        with patch.object(self.identity,'consume_step_up',side_effect=consume):
            response=await self.post(authorization=True,body={**self.body,'password':identity_fixture.PASSWORD})
        self.assertEqual(response.status_code,201,response.text);self.assertEqual(self.consumption_count(),1)
        self.assertEqual(len(self.boundary.permits),1);self.assertEqual(observed[0]['target_id'],'plan-identity-test')
        self.assertEqual(observed[0]['policy_version'],self.boundary.plan.digest_sha256)
        self.assertEqual(observed[0]['operation'],'question.auto.plan.authorize')
    async def test_unqualified_role_does_not_issue_identity_proof_or_permit(self):
        original=self.dependencies.authorization_service.authorize_action
        def editor(**kwargs):return replace(original(**kwargs),role=Role.EDITOR)
        with patch.object(self.dependencies.authorization_service,'authorize_action',side_effect=editor):
            response=await self.post(authorization=True,body={**self.body,'password':identity_fixture.PASSWORD})
        self.assertEqual(response.status_code,403,response.text);self.assertEqual(self.consumption_count(),0)
        self.assertEqual(self.boundary.permits,[])
    async def test_cloud_prepare_failure_after_identity_consumption_never_issues_permit(self):
        self.boundary.fail_cloud=True
        response=await self.post(authorization=True,body={**self.body,'password':identity_fixture.PASSWORD})
        self.assertEqual(response.status_code,503,response.text);self.assertEqual(self.consumption_count(),1)
        self.assertEqual(self.boundary.permits,[])
