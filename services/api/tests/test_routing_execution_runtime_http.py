"""HTTP + real service/auto coordinator/PG adapter; SQL and trusted producers are doubles.
The access-port double is separate from the real SQLite HTTP authorization fixture.
No network, PostgreSQL server, provider or real credential is used.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
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
        if sql.startswith("SELECT to_regclass('public.routing_authorization_plans')"):
            return SimpleNamespace(fetchone=lambda:('routing_authorization_plans',))
        if sql.startswith('SELECT payload,content_digest FROM routing_authorization_plans') and ' AND run_id=%s AND actor_id=%s' in sql:
            matches=[v for (t,w,_),v in self.plans.items() if (t,w,v['run_id'],v['scope']['actor_id'])==tuple(params)]
            return SimpleNamespace(fetchone=lambda:None if not matches else (deepcopy(matches[0]),digest(matches[0])))
        if sql.startswith('SELECT canonical_json,canonical_text,digest_sha256,created_by'):
            table=sql.split(' FROM ')[1].split()[0];record=self.canon.get((table,*params));fields=sql[7:].split(' FROM ')[0].split(',')
            def value(key):
                if key=='state':return self.states[params[2]][0]
                v=record[key];return v.obj if hasattr(v,'obj') else v
            return SimpleNamespace(fetchone=lambda:None if record is None else tuple(value(k) for k in fields))
        if sql.startswith('SELECT notebook_id FROM notebook_bindings'):
            bound=getattr(self,'binding_timestamps',[])
            return SimpleNamespace(fetchall=lambda:[('notebook-cp3',)] if bound else [])
        if sql.startswith('SELECT 1 FROM public.identity_memberships') or sql.startswith('SELECT 1 FROM public.notebooks'):
            return SimpleNamespace(fetchone=lambda:None if getattr(self,'access_revoked',False) else (1,))
        if sql.startswith('SELECT 1 FROM public.notebook_bindings'):
            self.calls.append((sql,params))
            return SimpleNamespace(fetchone=lambda:None)
        if sql.startswith('SELECT record_id FROM routing_decisions') or sql.startswith('SELECT record_id FROM run_snapshots'):
            table=sql.split(' FROM ')[1].split()[0]
            return SimpleNamespace(fetchall=lambda:[(r['record_id'],) for (t,tenant,w,_),r in self.canon.items()
                if t==table and (tenant,w,r['run_id'])==tuple(params)])
        if sql.startswith('SELECT record_id FROM model_attempts'):
            rows=[(r['record_id'],) for (table,t,w,_),r in self.canon.items()
                if table=='model_attempts' and (t,w,r['routing_decision_id'])==tuple(params)]
            return SimpleNamespace(fetchall=lambda:rows)
        if sql.startswith('SELECT selected_model_attempt_id,routing_decision_id FROM run_results'):
            rows=[(r['selected_model_attempt_id'],r['routing_decision_id']) for (table,t,w,_),r in self.canon.items()
                if table=='run_results' and (t,w,r['run_id'])==tuple(params)]
            return SimpleNamespace(fetchall=lambda:rows)
        if sql.startswith('SELECT attempt_id,event_kind,payload,content_digest,created_at FROM routing_attempt_events'):
            return SimpleNamespace(fetchall=lambda:[(v[3],v[4],deepcopy(v[5]),v[6],getattr(self,'event_times',{}).get(v[4],datetime(2026,10,21,12,tzinfo=timezone.utc)))
                for v in self.events if tuple(v[:3])==tuple(params)])
        if sql.startswith('SELECT attempt_id,candidate_order,created_at FROM routing_dispatch_outbox'):
            return SimpleNamespace(fetchall=lambda:[(r[3],r[4],datetime(2026,10,21,12,tzinfo=timezone.utc))
                for r in self.outbox if tuple(r[:3])==tuple(params)])
        if sql.startswith('SELECT data_area FROM identity_auth_workspaces'):
            return SimpleNamespace(fetchone=lambda:('cloud_sync',))
        if sql.startswith('INSERT INTO notebook_bindings'):
            # 0020의 NOT NULL/no default 계약을 대역에서도 그대로 확인한다.
            from psycopg.errors import NotNullViolation
            columns=sql.split('(',1)[1].split(')',1)[0].split(',')
            if 'created_at' not in columns:raise NotNullViolation('created_at is required')
            timestamp=params[-1]
            assert isinstance(timestamp,datetime) and timestamp.tzinfo is not None and timestamp.utcoffset()==timezone.utc.utcoffset(timestamp)
            self.binding_timestamps=getattr(self,'binding_timestamps',[])+[timestamp]
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
        self.store=SQLStore();self.store.close=lambda:None;self.store.db=BridgeSQL();self.revoked=False;self.changed=False;self.calls=[]
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
        self.dependencies=replace(self.dependencies,question_answering_service=self.service,egress_policy_service=egress._policy_service,cloud_store=self.store)
        def access(port,conn,ctx):
            if self.revoked:raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403)
        def current_egress(conn,ctx):
            return SimpleNamespace(fingerprint='sha256:'+('f' if self.changed else 'a')*64,question_step_up_required=False,required_approver='workspace_manager')
        self.patches=[patch('daon_user_api.runtime.PostgresRoutingAttemptsRepository',side_effect=lambda store:self.attempts),
            patch.object(_ScopedQuestionPort,'_current_access',access),
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
        self.assertEqual(set(proof),{'routing_plan_id','routing_plan_authorization','expires_at','run_id','plan_digest','requires_step_up','routing_preview'})
        return {**self.body,**{k:proof[k] for k in ('routing_plan_id','routing_plan_authorization')}}
    async def test_auto_authorization_preview_is_exact_frozen_plan_without_secrets(self):
        body=dict(notebook_id='notebook-cp3',question='QA question',routing_mode='auto',routing_policy_version_id='policy-v1')
        response=await self.request('/authorization',body,'preview-auto-0001')
        self.assertEqual(response.status_code,201,response.text)
        data=response.json()['data'];preview=data.get('routing_preview')
        self.assertIsInstance(preview,dict)
        self.assertEqual(preview,dict(schema_version=1,routing_policy_version_id='policy-v1',plan_digest=data['plan_digest'],
            candidates=[dict(candidate_order=1,model_id=self.candidate.model_id,deployment_id=self.candidate.deployment_id,
                provider_kind='server_internal')],exclusions=[]))
        self.assertNotIn('fixture.invalid',json.dumps(preview));self.assertEqual(self.calls,[])

    async def test_routing_read_restores_actual_attempt_and_current_scope_without_dispatch(self):
        body=await self.prepare();response=await self.request('',body,'question-auto-api-0001')
        self.assertEqual(response.status_code,200,response.text)
        run=response.json()['data']['run_id']
        with self._authenticated():
            read=await self.client.get('/api/v1/runs/'+run+'/routing-decision?workspace_id=workspace-001',
                cookies={WEB_SESSION_COOKIE:'fixture-access-token'})
        self.assertEqual(read.status_code,200,read.text)
        self.assertEqual(read.headers['cache-control'],'no-store')
        data=read.json()['data'];self.assertEqual(data['run_state'],'completed')
        self.assertEqual(len(data['attempts']),1);self.assertEqual(data['attempts'][0]['cost_state'],'unknown')
        self.assertIsNone(data['attempts'][0]['cost_amount']);self.assertIsNotNone(data['final_selection'])
        self.assertNotIn('QA question',read.text);self.assertNotIn('fixture.invalid',read.text)
        self.assertEqual(len(self.calls),1)

    async def routing_read(self,run,query='workspace_id=workspace-001'):
        with self._authenticated():
            return await self.client.get('/api/v1/runs/'+run+'/routing-decision?'+query,
                cookies={WEB_SESSION_COOKIE:'fixture-access-token'})

    async def test_routing_read_prepared_run_without_success_binding_is_read_only(self):
        await self.prepare();plan=next(iter(self.store.db.plans.values()));snapshot=lambda:repr((self.store.db.canon,self.store.db.events,self.store.db.claims));before=snapshot()
        start=len(self.store.db.calls);response=await self.routing_read(plan['run_id'])
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()['data'];self.assertEqual(data['run_state'],'accepted')
        self.assertEqual(data['attempts'],[]);self.assertIsNone(data['final_selection'])
        self.assertEqual(snapshot(),before)
        self.assertTrue(all(sql.startswith('SELECT ') for sql,_ in self.store.db.calls[start:]));self.assertEqual(self.calls,[])

    async def test_routing_read_other_actor_missing_and_revoked_share_404(self):
        await self.prepare();plan=next(iter(self.store.db.plans.values()));run=plan['run_id']
        missing=await self.routing_read('missing-run');self.assertEqual(missing.status_code,404)
        self.store.db.access_revoked=True
        revoked=await self.routing_read(run);self.assertEqual(revoked.status_code,404,revoked.text)
        self.store.db.access_revoked=False
        record=self.store.db.canon[('runs',plan['scope']['tenant_id'],'workspace-001',run)]
        record['created_by']='another-actor'
        other=await self.routing_read(run);self.assertEqual(other.status_code,404,other.text)
        for response in (missing,revoked,other):
            self.assertEqual(response.json()['error']['code'],'RESOURCE_UNAVAILABLE');self.assertEqual(response.headers['cache-control'],'no-store')

    async def test_routing_read_requires_exact_single_workspace_query(self):
        for query in ('','workspace_id=','workspace_id=workspace-001&workspace_id=workspace-001',
                'workspace_id=workspace-001&actor_id=other','workspace_id=invalid%2Fworkspace'):
            with self.subTest(query=query):
                response=await self.routing_read('run-any',query);self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.calls,[])

    async def test_routing_read_missing_run_parent_and_wrong_scope_are_hidden(self):
        await self.prepare();plan=next(iter(self.store.db.plans.values()));run=plan['run_id']
        scope=await self.routing_read(run,'workspace_id=another-workspace');self.assertEqual(scope.status_code,404,scope.text)
        del self.store.db.canon[('runs',plan['scope']['tenant_id'],'workspace-001',run)]
        response=await self.routing_read(run);self.assertEqual(response.status_code,404,response.text)

    async def test_routing_read_latest_receipt_settles_cost_without_rewriting_outcome(self):
        from daon_user_api.routing_execution import AttemptOutcome,FrozenAuthorizationPlan
        from daon_user_api.data_canon import canonical_json_bytes
        from daon_user_api.question_answering_postgres import QuestionContext
        body=await self.prepare();response=await self.request('',body,'question-auto-api-0001')
        self.assertEqual(response.status_code,200,response.text)
        p=next(iter(self.store.db.plans.values()));frozen=FrozenAuthorizationPlan(canonical_json_bytes(p).decode(),digest(p))
        ctx=QuestionContext(p['scope']['tenant_id'],p['scope']['workspace_id'],p['scope']['actor_id'],'read-test','policy-v1',
            p['scope']['notebook_id'],p['scope']['session_id'])
        receipt=AttemptOutcome('succeeded',cost_state='known',amount='0.12',currency='USD',receipt_id='receipt-qa',result=object())
        self.attempts.reconcile(ctx,frozen,1,receipt)
        read=await self.routing_read(p['run_id']);self.assertEqual(read.status_code,200,read.text)
        attempt=read.json()['data']['attempts'][0]
        self.assertEqual((attempt['state'],attempt['cost_state'],attempt['cost_amount'],attempt['currency']),('succeeded','known','0.12','USD'))
        self.assertEqual(len(self.calls),1)
        self.store.db.receipts.clear()
        invalid=await self.routing_read(p['run_id']);self.assertEqual(invalid.status_code,503,invalid.text)

    async def test_routing_read_missing_egress_parent_is_hidden(self):
        await self.prepare();p=next(iter(self.store.db.plans.values()))
        del self.store.db.canon[('egress_decisions',p['scope']['tenant_id'],'workspace-001',p['parents']['egress_decision_ids'][0])]
        response=await self.routing_read(p['run_id']);self.assertEqual(response.status_code,404,response.text)

    async def test_routing_read_orphan_event_is_not_silently_ignored(self):
        await self.prepare();p=next(iter(self.store.db.plans.values()))
        self.attempts._event(self.store.db,SimpleNamespace(**p['scope']),p['plan_id'],'unrelated-attempt','reconciled',{})
        response=await self.routing_read(p['run_id']);self.assertEqual(response.status_code,503,response.text)

    async def test_routing_read_rejects_unconfirmed_attempt_time_order(self):
        body=await self.prepare();await self.request('',body,'question-auto-api-0001');p=next(iter(self.store.db.plans.values()))
        record=next(v for (table,*_),v in self.store.db.canon.items() if table=='model_attempts')
        record['finished_at']=record['started_at']-timedelta(seconds=1)
        response=await self.routing_read(p['run_id']);self.assertEqual(response.status_code,503,response.text)

    async def test_historical_plan_reads_under_new_current_session_after_expiry(self):
        await self.prepare();p=next(iter(self.store.db.plans.values()))
        self.principal=replace(self.principal,session_id='new-current-session');self.now+=timedelta(days=1)
        response=await self.routing_read(p['run_id']);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['data']['routing_plan_id'],p['plan_id']);self.assertEqual(self.calls,[])

    async def test_failed_suppressed_run_without_binding_restores_true_outcome_only(self):
        body=await self.prepare();self.revoke_during_call=True
        response=await self.request('',body,'question-auto-api-0001');self.assertEqual(response.status_code,403)
        p=next(iter(self.store.db.plans.values()));self.revoked=False
        read=await self.routing_read(p['run_id']);self.assertEqual(read.status_code,200,read.text)
        self.assertEqual(read.json()['data']['run_state'],'failed');self.assertIsNone(read.json()['data']['final_selection'])
        self.assertEqual(read.json()['data']['attempts'][0]['state'],'succeeded')
        self.assertNotIn('QA auto answer',read.text);self.assertEqual(len(self.calls),1)

    async def test_current_source_binding_is_checked_by_real_read_only_primitive(self):
        from daon_user_api.question_answering_postgres import QuestionContext
        ctx=QuestionContext('tenant-001','workspace-001','user-001','source-read','policy-v1','notebook-cp3','session-001')
        with self.assertRaises(RoutingExecutionError) as denied:
            self.attempts._read_access(self.store.db,ctx,[dict(origin='raw_source',context_item_id='source-qa',source_id='source-qa',source_version_id='version-qa',digest_sha256=None)])
        self.assertEqual((denied.exception.code,denied.exception.status),('RESOURCE_UNAVAILABLE',404))
        self.assertTrue(any('public.notebook_bindings' in sql and args[-2:]==('source-qa','version-qa') for sql,args in self.store.db.calls))

    async def test_dispatch_claim_without_receipt_is_uncertain_and_never_final_selection(self):
        from daon_user_api.question_answering_postgres import QuestionContext
        from daon_user_api.routing_execution import FrozenAuthorizationPlan
        from daon_user_api.data_canon import canonical_json_bytes
        await self.prepare();p=next(iter(self.store.db.plans.values()));plan=FrozenAuthorizationPlan(canonical_json_bytes(p).decode(),digest(p))
        ctx=QuestionContext(p['scope']['tenant_id'],p['scope']['workspace_id'],p['scope']['actor_id'],'claim-read','policy-v1',p['scope']['notebook_id'],p['scope']['session_id'])
        self.attempts.claim(ctx,plan,idempotency_key='claim-read-key',request_digest=p['request_digest'],recheck=lambda *a:None)
        self.attempts.claim_attempt(ctx,plan,1,recheck=lambda *a:None)
        response=await self.routing_read(p['run_id']);self.assertEqual(response.status_code,200,response.text)
        attempt=response.json()['data']['attempts'][0]
        self.assertEqual((attempt['state'],attempt['cost_state'],attempt['cost_amount']),('dispatch_claimed','uncertain',None))
        self.assertIsNone(response.json()['data']['final_selection']);self.assertEqual(self.calls,[])

    async def test_pinned_historical_selection_has_no_invented_auto_plan(self):
        from daon_user_api.data_canon import canonical_json_bytes
        from psycopg.types.json import Jsonb
        body=await self.prepare();await self.request('',body,'question-auto-api-0001');p=next(iter(self.store.db.plans.values()))
        record=self.store.db.canon[('runs',p['scope']['tenant_id'],'workspace-001',p['run_id'])]
        payload=record['canonical_json'].obj.copy();payload.pop('routing_mode');payload.pop('routing_plan_digest');payload['provider_kind']='server_internal'
        record.update(canonical_json=Jsonb(payload),canonical_text=canonical_json_bytes(payload).decode(),digest_sha256=digest(payload))
        response=await self.routing_read(p['run_id']);self.assertEqual(response.status_code,200,response.text)
        data=response.json()['data'];self.assertEqual(data['routing_mode'],'pinned')
        self.assertIsNone(data['routing_plan_id']);self.assertIsNone(data['plan_digest']);self.assertIsNone(data['routing_policy_version_id'])
        self.assertEqual(len(data['attempts']),1);self.assertIsNotNone(data['final_selection']);self.assertEqual(len(self.calls),1)
        attempt=next(v for (table,*_),v in self.store.db.canon.items() if table=='model_attempts')
        bad=attempt['canonical_json'].obj.copy();bad['deployment_id']='wrong-deployment'
        attempt.update(model_deployment_id='wrong-deployment',canonical_json=Jsonb(bad),
            canonical_text=canonical_json_bytes(bad).decode(),digest_sha256=digest(bad))
        mismatch=await self.routing_read(p['run_id']);self.assertEqual(mismatch.status_code,503,mismatch.text)


    async def test_routing_read_rejects_future_or_reversed_event_times(self):
        body=await self.prepare();await self.request('',body,'question-auto-api-0001');p=next(iter(self.store.db.plans.values()))
        for times in ({'terminal':datetime.now(timezone.utc)+timedelta(days=1)},
                {'prepared':self.now+timedelta(seconds=1)}, {'dispatch_claimed':self.now+timedelta(seconds=1)}):
            with self.subTest(times=tuple(times)):
                self.store.db.event_times=times
                response=await self.routing_read(p['run_id']);self.assertEqual(response.status_code,503,response.text)

    async def test_successful_auto_binding_records_explicit_server_utc_creation_time(self):
        body=await self.prepare();response=await self.request('',body,'question-auto-api-0001')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.store.db.binding_timestamps,[self.now])
        self.assertEqual(len(self.calls),1)

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
