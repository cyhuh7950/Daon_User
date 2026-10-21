"""B2 PostgreSQL append-only permit, reservation, dispatch and terminal adapter.

The SQL in migration 0075 is an unapplied draft. One dispatch claim commits
before the external effect; an ambiguous effect is never automatically retried.
"""
from __future__ import annotations
from datetime import datetime,timezone
from decimal import Decimal
import json
from uuid import uuid4
from psycopg.types.json import Jsonb
from .cloud_storage import CloudAccessContext,CloudDatabaseError
from .data_canon import canonical_json_bytes
from .question_answering_postgres import PostgresQuestionAnsweringRepository as Questions
from .routing_execution import (FrozenAuthorizationPlan,RoutingExecutionError,AttemptOutcome,SuppressedResult,money,scope)
from .routing_policy import digest


class PostgresRoutingAttemptsRepository:
    def __init__(self,store,*,clock=None):
        self.store=store;self.clock=clock or (lambda:datetime.now(timezone.utc))

    @staticmethod
    def _cloud(ctx,operation):
        return CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,operation)

    @staticmethod
    def _lock(conn,ctx,key):
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
            ('routing-execution:'+digest(dict(tenant_id=ctx.tenant_id,workspace_id=ctx.workspace_id,key=key)),))

    @staticmethod
    def _scope(plan,ctx):
        if plan.as_dict()['scope']!=scope(ctx):
            raise RoutingExecutionError('ROUTING_AUTHORIZATION_INVALID',403)

    def _stored(self,conn,ctx,plan):
        self._scope(plan,ctx)
        p=plan.as_dict()
        # 불변 행은 SELECT로 대조한다. 호출자의 Run 잠금과 claim 고유키가 실행 경합을 제어한다.
        row=conn.execute('SELECT payload,content_digest FROM routing_authorization_plans '
            'WHERE tenant_id=%s AND workspace_id=%s AND plan_id=%s AND actor_id=%s AND session_id=%s',
            (ctx.tenant_id,ctx.workspace_id,p['plan_id'],ctx.actor_id,ctx.session_id)).fetchone()
        if (row is None or row[1]!=plan.digest_sha256
                or canonical_json_bytes(row[0]).decode()!=plan.canonical_text):
            raise RoutingExecutionError('ROUTING_AUTHORIZATION_INVALID',403)

    @staticmethod
    def _event(conn,ctx,plan_id,attempt_id,kind,payload):
        sha=digest(payload)
        prior=conn.execute('SELECT content_digest FROM routing_attempt_events '
            'WHERE tenant_id=%s AND workspace_id=%s AND plan_id=%s AND attempt_id=%s AND event_kind=%s',
            (ctx.tenant_id,ctx.workspace_id,plan_id,attempt_id,kind)).fetchone()
        if prior:
            if prior[0]!=sha: raise RoutingExecutionError('IDEMPOTENCY_KEY_REUSED')
            return False
        conn.execute('INSERT INTO routing_attempt_events(tenant_id,workspace_id,plan_id,attempt_id,event_kind,payload,content_digest) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s)',
            (ctx.tenant_id,ctx.workspace_id,plan_id,attempt_id,kind,Jsonb(payload),sha))
        return True

    def prepare(self,ctx,plan,*,recheck,materialize):
        self._scope(plan,ctx);p=plan.as_dict()
        try:
            with self.store._transaction(self._cloud(ctx,'routing_execution.prepare')) as conn:
                self._lock(conn,ctx,p['run_id']);self._lock(conn,ctx,'budget:'+p['budget']['scope_id'])
                recheck(conn,ctx,plan)
                shared=conn.execute("SELECT payload->'budget' FROM routing_authorization_plans WHERE tenant_id=%s AND workspace_id=%s "
                    "AND payload->'budget'->>'scope_id'=%s LIMIT 1",
                    (ctx.tenant_id,ctx.workspace_id,p['budget']['scope_id'])).fetchone()
                if shared and any(shared[0].get(k)!=p['budget'][k] for k in
                        ('source_id','version','currency','period_limit','starts_at','expires_at')):
                    raise RoutingExecutionError('ROUTING_BUDGET_UNVERIFIED',503)
                existing=conn.execute('SELECT plan_id FROM routing_authorization_plans '
                    'WHERE tenant_id=%s AND workspace_id=%s AND run_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,p['run_id'])).fetchone()
                if existing: raise RoutingExecutionError('ROUTING_NEW_PREPARE_REQUIRED')
                materialize(conn,ctx,plan)
                refs=p['parents']
                conn.execute('INSERT INTO routing_authorization_plans(tenant_id,workspace_id,plan_id,actor_id,session_id,'
                    'run_id,routing_policy_version_id,routing_decision_id,run_snapshot_id,expires_at,nonce_digest,'
                    'payload,content_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (ctx.tenant_id,ctx.workspace_id,p['plan_id'],ctx.actor_id,ctx.session_id,p['run_id'],
                     p['policy_version_id'],refs['routing_decision_id'],refs['run_snapshot_id'],p['expires_at'],
                     p['nonce_digest'],Jsonb(p),plan.digest_sha256))
        except CloudDatabaseError:
            raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503) from None

    def read(self,ctx,plan_id):
        try:
            with self.store._transaction(self._cloud(ctx,'routing_execution.read')) as conn:
                row=conn.execute('SELECT payload,content_digest FROM routing_authorization_plans '
                    'WHERE tenant_id=%s AND workspace_id=%s AND plan_id=%s AND actor_id=%s AND session_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,plan_id,ctx.actor_id,ctx.session_id)).fetchone()
                if not row: raise RoutingExecutionError('ROUTING_AUTHORIZATION_INVALID',403)
                plan=FrozenAuthorizationPlan(canonical_json_bytes(row[0]).decode(),row[1]);self._scope(plan,ctx)
                return plan
        except CloudDatabaseError:
            raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503) from None

    def claim(self,ctx,plan,*,idempotency_key,request_digest,recheck):
        self._scope(plan,ctx);p=plan.as_dict()
        try:
            with self.store._transaction(self._cloud(ctx,'routing_execution.claim')) as conn:
                self._lock(conn,ctx,p['run_id'])
                self._stored(conn,ctx,plan)
                if request_digest!=p['request_digest']:
                    raise RoutingExecutionError('ROUTING_AUTHORIZATION_INVALID',403)
                recheck(conn,ctx,plan)
                prior=conn.execute('SELECT plan_id,request_digest FROM routing_execution_claims '
                    'WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND idempotency_key=%s',
                    (ctx.tenant_id,ctx.workspace_id,ctx.actor_id,idempotency_key)).fetchone()
                if prior:
                    if prior!=(p['plan_id'],request_digest):raise RoutingExecutionError('IDEMPOTENCY_KEY_REUSED')
                    return False
                prior=conn.execute('SELECT plan_id FROM routing_execution_claims '
                    'WHERE tenant_id=%s AND workspace_id=%s AND plan_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,p['plan_id'])).fetchone()
                if prior:raise RoutingExecutionError('ROUTING_AUTHORIZATION_CONSUMED')
                if self.clock()>=datetime.fromisoformat(p['expires_at']):
                    raise RoutingExecutionError('ROUTING_AUTHORIZATION_EXPIRED',403)
                # This insertion consumes the Cloud permit and claims its Run in one transaction.
                conn.execute('INSERT INTO routing_execution_claims(tenant_id,workspace_id,plan_id,actor_id,'
                    'session_id,idempotency_key,request_digest) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                    (ctx.tenant_id,ctx.workspace_id,p['plan_id'],ctx.actor_id,ctx.session_id,idempotency_key,request_digest))
                version=Questions._transition(conn,ctx,p['run_id'],1,'planning')
                version=Questions._transition(conn,ctx,p['run_id'],version,'retrieving')
                Questions._transition(conn,ctx,p['run_id'],version,'generating')
                return True
        except CloudDatabaseError:
            raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503) from None

    def _events(self,conn,ctx,*,budget_scope=None,plan_id=None):
        if budget_scope is not None:
            return conn.execute('SELECT e.attempt_id,e.event_kind,e.payload FROM routing_attempt_events e '
                'JOIN routing_authorization_plans p ON p.tenant_id=e.tenant_id AND p.workspace_id=e.workspace_id AND p.plan_id=e.plan_id '
                "WHERE p.tenant_id=%s AND p.workspace_id=%s AND p.payload->'budget'->>'scope_id'=%s ORDER BY e.sequence",
                (ctx.tenant_id,ctx.workspace_id,budget_scope)).fetchall()
        return conn.execute('SELECT attempt_id,event_kind,payload FROM routing_attempt_events '
            'WHERE tenant_id=%s AND workspace_id=%s AND plan_id=%s ORDER BY sequence',
            (ctx.tenant_id,ctx.workspace_id,plan_id)).fetchall()

    @staticmethod
    def _obligation(events,currency):
        attempts={}
        for attempt,kind,payload in events:
            state=attempts.setdefault(attempt,{})
            if kind=='prepared':state['reserved']=money(payload['reserved_amount'])
            if kind in {'terminal','reconciled'} and payload.get('cost_state')=='known':
                if payload['currency']!=currency:raise RoutingExecutionError('ROUTING_BUDGET_UNVERIFIED',503)
                state['settled']=money(payload['amount'])
        return sum((v.get('settled',v.get('reserved',Decimal(0))) for v in attempts.values()),Decimal(0))

    def claim_attempt(self,ctx,plan,order,*,recheck):
        p=plan.as_dict();attempt='route-attempt-'+digest(dict(plan=p['plan_id'],order=order))
        try:
            with self.store._transaction(self._cloud(ctx,'routing_execution.dispatch')) as conn:
                self._lock(conn,ctx,p['run_id']);self._lock(conn,ctx,'budget:'+p['budget']['scope_id'])
                self._stored(conn,ctx,plan)
                if type(order) is not int or not 1<=order<=len(p['candidates']):
                    raise RoutingExecutionError('ROUTING_DISPATCH_ORDER_INVALID')
                if not datetime.fromisoformat(p['budget']['starts_at'])<=self.clock()<min(
                        datetime.fromisoformat(p['budget']['expires_at']),datetime.fromisoformat(p['expires_at'])):
                    raise RoutingExecutionError('ROUTING_AUTHORIZATION_EXPIRED',403)
                recheck(conn,ctx,plan)
                claimed=conn.execute('SELECT plan_id FROM routing_execution_claims '
                    'WHERE tenant_id=%s AND workspace_id=%s AND plan_id=%s AND actor_id=%s AND session_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,p['plan_id'],ctx.actor_id,ctx.session_id)).fetchone()
                if not claimed:raise RoutingExecutionError('ROUTING_AUTHORIZATION_REQUIRED',401)
                events=self._events(conn,ctx,plan_id=p['plan_id'])
                if any(a==attempt for a,_,_ in events):raise RoutingExecutionError('ROUTING_DISPATCH_ALREADY_CLAIMED')
                terminals=[v for _,k,v in events if k=='terminal']
                if order!=len(terminals)+1 or any(not AttemptOutcome(**v).allows_fallback() for v in terminals):
                    raise RoutingExecutionError('ROUTING_DISPATCH_ORDER_INVALID')
                entry=p['candidates'][order-1];estimate=money(entry['quote']['maximum_estimate']);budget=p['budget']
                if (order>budget['max_attempts'] or entry['quote']['currency']!=budget['currency']
                        or self._obligation(events,budget['currency'])+estimate>money(budget['run_limit'])
                        or self._obligation(self._events(conn,ctx,budget_scope=budget['scope_id']),budget['currency'])+estimate
                            >money(budget['period_limit'])):
                    raise RoutingExecutionError('COST_LIMIT_EXCEEDED')
                prepared=dict(candidate_order=order,reserved_amount=str(estimate),currency=budget['currency'],
                    quote_digest=digest(entry['quote']),candidate_digest=digest(entry['candidate']))
                self._event(conn,ctx,p['plan_id'],attempt,'prepared',prepared)
                self._event(conn,ctx,p['plan_id'],attempt,'dispatch_claimed',dict(candidate_order=order))
                conn.execute('INSERT INTO routing_dispatch_outbox(tenant_id,workspace_id,plan_id,attempt_id,candidate_order) '
                    'VALUES(%s,%s,%s,%s,%s)',(ctx.tenant_id,ctx.workspace_id,p['plan_id'],attempt,order))
                return attempt
        except CloudDatabaseError:
            raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503) from None

    def finish(self,ctx,plan,order,attempt,outcome,*,final,write_result):
        p=plan.as_dict()
        try:
            with self.store._transaction(self._cloud(ctx,'routing_execution.terminal')) as conn:
                self._lock(conn,ctx,p['run_id']);self._lock(conn,ctx,'budget:'+p['budget']['scope_id'])
                self._stored(conn,ctx,plan)
                expected='route-attempt-'+digest(dict(plan=p['plan_id'],order=order))
                if attempt!=expected or type(order) is not int or not 1<=order<=len(p['candidates']):
                    raise RoutingExecutionError('ROUTING_LINEAGE_INVALID',403)
                dispatched=conn.execute('SELECT candidate_order,created_at FROM routing_dispatch_outbox '
                    'WHERE tenant_id=%s AND workspace_id=%s AND plan_id=%s AND attempt_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,p['plan_id'],attempt)).fetchone()
                if dispatched is None or dispatched[0]!=order:
                    raise RoutingExecutionError('ROUTING_DISPATCH_NOT_CLAIMED',403)
                if outcome.cost_state=='known' and outcome.currency!=p['budget']['currency']:
                    raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)
                if outcome.cost_state=='known':self._bind_receipt(conn,ctx,plan,attempt,order,outcome)
                safe=outcome.safe_dict()
                created=self._event(conn,ctx,p['plan_id'],attempt,'terminal',safe)
                if not created:return self.result(ctx,plan)
                parents=p['parents'];models=parents['models'][order-1]
                usage=getattr(outcome.result,'usage',{}) if outcome.result is not None else {}
                usage={k:v for k,v in usage.items() if k in {'input_tokens','output_tokens','total_tokens','prompt_tokens','completion_tokens'}
                    and type(v) is int and v>=0} if isinstance(usage,dict) else {}
                payload=dict(run_id=p['run_id'],deployment_id=models['deployment_id'],
                    model_artifact_id=models['artifact_id'],usage=usage,**safe)
                insert_canon_exact(conn,ctx,'model_attempts',attempt,payload,
                    extra_columns=('routing_decision_id','model_deployment_id','model_artifact_id','candidate_order',
                        'started_at','finished_at','safe_error_code','cost_amount','usage_json'),
                    extra_values=(parents['routing_decision_id'],models['deployment_id'],models['artifact_id'],order,
                        dispatched[1],self.clock(),outcome.safe_code,
                        None if outcome.amount is None else money(outcome.amount),Jsonb(usage)))
                if outcome.outcome=='succeeded':
                    try:
                        with conn.transaction():
                            return write_result(conn,ctx,plan,order,attempt,outcome.result)
                    except RoutingExecutionError as error:
                        if error.status not in {401,403,404}:raise
                        Questions._transition(conn,ctx,p['run_id'],4,'failed')
                        return SuppressedResult()
                if final:
                    Questions._transition(conn,ctx,p['run_id'],4,'failed')
                return None
        except CloudDatabaseError:
            raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503) from None

    def _bind_receipt(self,conn,ctx,plan,attempt,order,outcome):
        p=plan.as_dict();billing=p['candidates'][order-1]['quote']['billing_reference']
        payload=dict(plan_id=p['plan_id'],attempt_id=attempt,candidate_order=order,
            amount=outcome.amount,currency=outcome.currency,billing_reference=billing,receipt_id=outcome.receipt_id)
        sha=digest(payload)
        key=(ctx.tenant_id,billing['scope_id'],billing['provider_code'],billing['account_reference'],outcome.receipt_id)
        row=conn.execute('INSERT INTO routing_billing_receipts(tenant_id,billing_scope_id,provider_code,account_reference,'
            'receipt_id,workspace_id,plan_id,attempt_id,content_digest,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT(tenant_id,billing_scope_id,provider_code,account_reference,receipt_id) DO NOTHING '
            'RETURNING workspace_id,plan_id,attempt_id,content_digest',
            (*key,ctx.workspace_id,p['plan_id'],attempt,sha,Jsonb(payload))).fetchone()
        if row is None:
            # 불변 receipt의 정확한 내용 대조에는 UPDATE 행 잠금 권한이 필요하지 않다.
            row=conn.execute('SELECT workspace_id,plan_id,attempt_id,content_digest FROM routing_billing_receipts '
                'WHERE tenant_id=%s AND billing_scope_id=%s AND provider_code=%s AND account_reference=%s AND receipt_id=%s',key).fetchone()
        if row!=(ctx.workspace_id,p['plan_id'],attempt,sha):
            raise RoutingExecutionError('ROUTING_RECEIPT_REUSED')
        return payload

    def reconcile(self,ctx,plan,order,receipt):
        p=plan.as_dict();attempt='route-attempt-'+digest(dict(plan=p['plan_id'],order=order))
        if receipt.cost_state!='known':raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)
        with self.store._transaction(self._cloud(ctx,'routing_execution.reconcile')) as conn:
            self._lock(conn,ctx,p['run_id']);self._lock(conn,ctx,'budget:'+p['budget']['scope_id'])
            events=self._events(conn,ctx,plan_id=p['plan_id'])
            terminal=[v for a,k,v in events if a==attempt and k=='terminal']
            if not terminal:raise RoutingExecutionError('ROUTING_DISPATCH_NOT_CLAIMED',403)
            if receipt.currency!=p['budget']['currency']:raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)
            self._bind_receipt(conn,ctx,plan,attempt,order,receipt)
            return self._event(conn,ctx,p['plan_id'],attempt,'reconciled',receipt.safe_dict())

    def mark_uncertain(self,ctx,plan,order):
        p=plan.as_dict();attempt='route-attempt-'+digest(dict(plan=p['plan_id'],order=order))
        try:
            with self.store._transaction(self._cloud(ctx,'routing_execution.uncertain')) as conn:
                self._lock(conn,ctx,p['run_id'])
                events=self._events(conn,ctx,plan_id=p['plan_id'])
                if any(a==attempt and kind=='terminal' for a,kind,_ in events):return
            self.finish(ctx,plan,order,attempt,AttemptOutcome('uncertain','RESULT_PERSISTENCE_UNCERTAIN','uncertain'),
                final=True,write_result=None)
        except (CloudDatabaseError,RoutingExecutionError):
            # The durable dispatch claim already closes retries even if recovery storage is down.
            return

    def result(self,ctx,plan):
        p=plan.as_dict()
        # Reuse the real canonical answer reader; the caller has just rechecked current scope.
        repository=Questions.__new__(Questions);repository._cloud_store=self.store
        result=repository.load_completed(ctx,p['run_id'])
        if result is None:raise RoutingExecutionError('ROUTING_RUN_UNCERTAIN')
        return result


    @staticmethod
    def _read_record(conn,ctx,table,record_id,extra=()):
        # table/columns are internal constants, never request strings.
        fields=('canonical_json','canonical_text','digest_sha256','created_by',*extra)
        row=conn.execute('SELECT '+','.join(fields)+' FROM '+table+
            ' WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
            (ctx.tenant_id,ctx.workspace_id,record_id)).fetchone()
        if row is None:raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
        result=dict(zip(fields,row,strict=True))
        if (canonical_json_bytes(result['canonical_json']).decode()!=result['canonical_text']
                or digest(result['canonical_json'])!=result['digest_sha256']):
            raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
        return result

    @staticmethod
    def _read_access(conn,ctx,items):
        from types import SimpleNamespace
        from .question_answering_postgres import QuestionRepositoryError
        from .open_law_source_access import OpenLawAccessError
        try:
            selected=[]
            for item in items:
                if set(item)!={'origin','context_item_id','source_id','source_version_id','digest_sha256'}:
                    raise ValueError
                registration=None
                if item['origin']=='daon_knowledge':
                    rows=conn.execute("SELECT record_id FROM knowledge_registrations WHERE tenant_id=%s AND workspace_id=%s "
                        "AND registered_source_version_id=%s AND canonical_json->>'package_id'=%s",
                        (ctx.tenant_id,ctx.workspace_id,item['source_version_id'],item['context_item_id'])).fetchall()
                    if len(rows)!=1:raise ValueError
                    registration=rows[0][0]
                selected.append(SimpleNamespace(**item,knowledge_registration_id=registration))
            # write=False checks notebook deletion, current VIEW/QUERY, bindings, source ACL,
            # retention, law/website freshness and approved knowledge registration; inserts nothing.
            Questions._knowledge_selections(conn,ctx,tuple(selected),write=False)
        except (QuestionRepositoryError,OpenLawAccessError,KeyError,ValueError,TypeError):
            raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404) from None

    def read_routing(self,ctx,run_id):
        from dataclasses import replace
        from .routing_execution import routing_preview
        try:
            with self.store._transaction(self._cloud(ctx,'routing_execution.projection')) as conn:
                run=self._read_record(conn,ctx,'runs',run_id,('state','conversation_id'))
                if run['created_by']!=ctx.actor_id:raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                state=run['state']
                if state not in {'accepted','planning','retrieving','generating','validating','completed','failed','cancelled'}:
                    raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                auto=run['canonical_json'].get('routing_mode')=='auto'
                ready=conn.execute("SELECT to_regclass('public.routing_authorization_plans')").fetchone()
                plan=None
                if auto:
                    if ready is None or ready[0] is None:raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                    row=conn.execute('SELECT payload,content_digest FROM routing_authorization_plans '
                        'WHERE tenant_id=%s AND workspace_id=%s AND run_id=%s AND actor_id=%s',
                        (ctx.tenant_id,ctx.workspace_id,run_id,ctx.actor_id)).fetchone()
                    if row is None:raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    try:plan=FrozenAuthorizationPlan(canonical_json_bytes(row[0]).decode(),row[1])
                    except RoutingExecutionError:raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404) from None
                    p=plan.as_dict();bound=p['scope'];parents=p['parents']
                    if (any(bound[k]!=getattr(ctx,k) for k in ('tenant_id','workspace_id','actor_id'))
                            or p['run_id']!=run_id or run['canonical_json'].get('routing_plan_digest')!=plan.digest_sha256
                            or run['conversation_id']!=parents['conversation_id']):
                        raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    ctx=replace(ctx,notebook_id=bound['notebook_id'])
                    decision_id=parents['routing_decision_id'];snapshot_id=parents['run_snapshot_id']
                else:
                    if state!='completed':raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    ids=conn.execute('SELECT record_id FROM routing_decisions WHERE tenant_id=%s AND workspace_id=%s AND run_id=%s',
                        (ctx.tenant_id,ctx.workspace_id,run_id)).fetchall()
                    snapshots=conn.execute('SELECT record_id FROM run_snapshots WHERE tenant_id=%s AND workspace_id=%s AND run_id=%s',
                        (ctx.tenant_id,ctx.workspace_id,run_id)).fetchall()
                    if len(ids)!=1 or len(snapshots)!=1:raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    decision_id=ids[0][0];snapshot_id=snapshots[0][0]
                bindings=conn.execute("SELECT notebook_id FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s "
                    "AND binding_kind='conversation_thread' AND record_id=%s AND created_by=%s",
                    (ctx.tenant_id,ctx.workspace_id,run['conversation_id'],ctx.actor_id)).fetchall()
                if not auto:
                    if len(bindings)!=1:raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    ctx=replace(ctx,notebook_id=bindings[0][0])
                elif state=='completed' and (ctx.notebook_id,) not in bindings:
                    raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                decision=self._read_record(conn,ctx,'routing_decisions',decision_id,('run_id','routing_policy_version_id','egress_decision_id'))
                snapshot=self._read_record(conn,ctx,'run_snapshots',snapshot_id,('run_id','scope_snapshot_id','routing_policy_version_id'))
                if (decision['run_id']!=run_id or snapshot['run_id']!=run_id
                        or any(x['created_by']!=ctx.actor_id for x in (decision,snapshot))):
                    raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                ss=self._read_record(conn,ctx,'scope_snapshots',snapshot['scope_snapshot_id'],('knowledge_scope_id',))
                ks=self._read_record(conn,ctx,'knowledge_scopes',ss['knowledge_scope_id'])
                scope_data=ks['canonical_json']
                if (ss['canonical_json']!={**scope_data,'knowledge_scope_id':ss['knowledge_scope_id']}
                        or snapshot['canonical_json'].get('knowledge_scope_id')!=ss['knowledge_scope_id']
                        or not isinstance(scope_data.get('items'),list)):
                    raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                self._read_access(conn,ctx,scope_data['items'])
                entries=[]
                if auto:
                    if (decision['routing_policy_version_id']!=p['policy_version_id']
                            or snapshot['routing_policy_version_id']!=p['policy_version_id']
                            or snapshot['scope_snapshot_id']!=parents['scope_snapshot_id']
                            or ss['knowledge_scope_id']!=parents['scope_id']
                            or decision['canonical_json'].get('routing_plan_digest')!=plan.digest_sha256
                            or snapshot['canonical_json'].get('routing_plan_digest')!=plan.digest_sha256
                            or decision['canonical_json'].get('candidate_order')!=[m['deployment_id'] for m in parents['models']]):
                        raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    if (ss['created_by']!=ctx.actor_id or snapshot['canonical_json'].get('scope_snapshot_digest')!=p['source_scope_digest']
                            or decision['egress_decision_id']!=parents['egress_decision_ids'][0]):
                        raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    for entry,model,egress_id in zip(p['candidates'],parents['models'],parents['egress_decision_ids'],strict=True):
                        deployment=self._read_record(conn,ctx,'model_deployments',model['deployment_id'],('provider_profile_id','model_artifact_id'))
                        artifact=self._read_record(conn,ctx,'model_artifacts',model['artifact_id'])
                        profile=self._read_record(conn,ctx,'provider_profiles',model['profile_id'])
                        egress=self._read_record(conn,ctx,'egress_decisions',egress_id,('run_id','provider_profile_id'))
                        if (deployment['provider_profile_id']!=model['profile_id'] or deployment['model_artifact_id']!=model['artifact_id']
                                or deployment['canonical_json'].get('configured_deployment_id')!=entry['candidate']['deployment_id']
                                or deployment['canonical_json'].get('model_id')!=entry['candidate']['model_id']
                                or artifact['canonical_json'].get('model_id')!=entry['candidate']['model_id']
                                or profile['canonical_json'].get('configured_profile_id')!=entry['candidate']['connection_id']
                                or egress['run_id']!=run_id or egress['provider_profile_id']!=model['profile_id']
                                or egress['created_by']!=ctx.actor_id or egress['canonical_json'].get('policy_digest')!=entry['egress_policy_digest']):
                            raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    preview=routing_preview(plan);entries=preview['candidates']
                else:
                    for i,model_id in enumerate(decision['canonical_json'].get('candidate_order',[]),1):
                        model=self._read_record(conn,ctx,'model_deployments',model_id)
                        entries.append(dict(candidate_order=i,model_id=model['canonical_json']['model_id'],
                            deployment_id=model['canonical_json']['configured_deployment_id'],
                            provider_kind=run['canonical_json'].get('provider_kind')))
                if len(entries)>8 or any(e['provider_kind'] not in {'local','local_runtime','server_internal','external_api'}
                        or type(e['model_id']) is not str or not 1<=len(e['model_id'])<=255
                        or any(ord(c)<32 or ord(c)==127 for c in e['model_id']) for e in entries):
                    raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                attempts=self._project_attempts(conn,ctx,run_id,decision_id,plan,entries)
                result=conn.execute('SELECT selected_model_attempt_id,routing_decision_id FROM run_results '
                    'WHERE tenant_id=%s AND workspace_id=%s AND run_id=%s',
                    (ctx.tenant_id,ctx.workspace_id,run_id)).fetchall()
                final=None
                if state=='completed' and len(result)!=1:raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                if state=='completed' and result[0][0] is not None:
                    if len(result)!=1 or result[0][1]!=decision_id:raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    selected=[a for a in attempts if a['attempt_id']==result[0][0] and a['state']=='succeeded']
                    if len(selected)!=1:raise RoutingExecutionError('RESOURCE_UNAVAILABLE',404)
                    a=selected[0];final=dict(candidate_order=a['candidate_order'],model_id=entries[a['candidate_order']-1]['model_id'],attempt_id=a['attempt_id'])
                return dict(schema_version=1,run_id=run_id,routing_mode='auto' if auto else 'pinned',
                    routing_policy_version_id=p['policy_version_id'] if auto else None,
                    routing_plan_id=p['plan_id'] if auto else None,plan_digest=plan.digest_sha256 if auto else None,
                    run_state=state,candidates=entries,exclusions=preview['exclusions'] if auto else [],
                    attempts=attempts,final_selection=final)
        except CloudDatabaseError:
            raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503) from None
        except (KeyError,ValueError,TypeError,IndexError):
            raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503) from None

    def _project_attempts(self,conn,ctx,run_id,decision_id,plan,entries):
        from .routing_execution import _SAFE_CODES
        rows=conn.execute('SELECT record_id FROM model_attempts WHERE tenant_id=%s AND workspace_id=%s AND routing_decision_id=%s ORDER BY candidate_order',
            (ctx.tenant_id,ctx.workspace_id,decision_id)).fetchall()
        if len(rows)>8:raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
        result=[];events={};outboxes=[];p=None
        if plan:
            p=plan.as_dict()
            for attempt,kind,payload,sha,created in conn.execute('SELECT attempt_id,event_kind,payload,content_digest,created_at FROM routing_attempt_events '
                    'WHERE tenant_id=%s AND workspace_id=%s AND plan_id=%s ORDER BY created_at,event_kind',
                    (ctx.tenant_id,ctx.workspace_id,p['plan_id'])).fetchall():
                self._timestamp(created)
                if digest(payload)!=sha or kind in events.setdefault(attempt,{}):raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                events[attempt][kind]=(payload,created)
            outboxes=conn.execute('SELECT attempt_id,candidate_order,created_at FROM routing_dispatch_outbox '
                'WHERE tenant_id=%s AND workspace_id=%s AND plan_id=%s ORDER BY candidate_order',
                (ctx.tenant_id,ctx.workspace_id,p['plan_id'])).fetchall()
        for (attempt,) in rows:
            record=self._read_record(conn,ctx,'model_attempts',attempt,('routing_decision_id','model_deployment_id','model_artifact_id','candidate_order','started_at','finished_at','safe_error_code','cost_amount'))
            payload=record['canonical_json'];order=record['candidate_order']
            if (record['created_by']!=ctx.actor_id or record['routing_decision_id']!=decision_id
                    or type(order) is not int or not 1<=order<=len(entries) or payload.get('run_id')!=run_id
                    or payload.get('deployment_id')!=record['model_deployment_id']
                    or payload.get('model_artifact_id')!=record['model_artifact_id']):raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
            if payload.get('outcome') not in {'succeeded','failed','uncertain'}:
                raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
            if record['safe_error_code']!=payload.get('safe_code'):
                raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
            if plan is None:
                fixed=self._read_record(conn,ctx,'routing_decisions',decision_id)['canonical_json'].get('candidate_order',[])
                if order>len(fixed) or record['model_deployment_id']!=fixed[order-1]:
                    raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
            cost_state='unknown';amount=None;currency=None
            if plan:
                expected='route-attempt-'+digest(dict(plan=p['plan_id'],order=order));ev=events.get(attempt,{})
                if (attempt!=expected or 'terminal' not in ev or 'dispatch_claimed' not in ev or 'prepared' not in ev
                        or (attempt,order,record['started_at']) not in outboxes
                        or record['model_deployment_id']!=p['parents']['models'][order-1]['deployment_id']
                        or record['model_artifact_id']!=p['parents']['models'][order-1]['artifact_id']):
                    raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                if not ev['prepared'][1]<=ev['dispatch_claimed'][1]<=record['started_at']<=ev['terminal'][1]<=record['finished_at']:
                    raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                terminal=ev['terminal'][0]
                if any(payload.get(k)!=v for k,v in terminal.items()):raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                if 'reconciled' in ev and ev['reconciled'][1]<ev['terminal'][1]:raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                latest=ev.get('reconciled',ev['terminal'])[0]
                outcome=AttemptOutcome(**latest,result=object() if latest.get('outcome')=='succeeded' else None)
                cost_state=outcome.cost_state;amount=outcome.amount;currency=outcome.currency
                if cost_state=='known':
                    billing=p['candidates'][order-1]['quote']['billing_reference']
                    receipt=conn.execute('SELECT workspace_id,plan_id,attempt_id,content_digest FROM routing_billing_receipts '
                        'WHERE tenant_id=%s AND billing_scope_id=%s AND provider_code=%s AND account_reference=%s AND receipt_id=%s',
                        (ctx.tenant_id,billing['scope_id'],billing['provider_code'],billing['account_reference'],outcome.receipt_id)).fetchone()
                    bound=dict(plan_id=p['plan_id'],attempt_id=attempt,candidate_order=order,amount=amount,currency=currency,billing_reference=billing,receipt_id=outcome.receipt_id)
                    if currency!=p['budget']['currency'] or receipt!=(ctx.workspace_id,p['plan_id'],attempt,digest(bound)):
                        raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
            if record['finished_at']<record['started_at']:raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
            failure=record['safe_error_code']
            if failure is not None and failure not in _SAFE_CODES:raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
            result.append(dict(attempt_id=attempt,candidate_order=order,state=payload['outcome'],started_at=self._timestamp(record['started_at']),
                finished_at=self._timestamp(record['finished_at']),failure_code=failure,cost_state=cost_state,cost_amount=amount,currency=currency))
        if plan:
            if set(events)!={r[0] for r in outboxes}:raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
            for attempt,order,created in outboxes:
                if attempt in {a['attempt_id'] for a in result}:continue
                expected='route-attempt-'+digest(dict(plan=p['plan_id'],order=order));ev=events.get(attempt,{})
                if (not 1<=order<=len(entries) or attempt!=expected or 'dispatch_claimed' not in ev or 'prepared' not in ev or 'terminal' in ev):
                    raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                if not ev['prepared'][1]<=ev['dispatch_claimed'][1]<=created:
                    raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
                result.append(dict(attempt_id=attempt,candidate_order=order,state='dispatch_claimed',started_at=self._timestamp(created),
                    finished_at=None,failure_code=None,cost_state='uncertain',cost_amount=None,currency=None))
        if len({a['candidate_order'] for a in result})!=len(result):raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
        return sorted(result,key=lambda x:x['candidate_order'])

    def _timestamp(self,value):
        if not isinstance(value,datetime) or value.tzinfo is None or value>self.clock():raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
        return value.isoformat().replace('+00:00','Z')


def insert_canon_exact(conn,ctx,table,record_id,payload,*,extra_columns=(),extra_values=(),state=None):
    allowed={'knowledge_scopes','scope_snapshots','provider_profiles','model_artifacts','model_deployments',
        'conversations','runs','egress_decisions','routing_decisions','run_snapshots','model_attempts','run_results','citations'}
    if table not in allowed:raise RoutingExecutionError('ROUTING_LINEAGE_INVALID')
    encoded=canonical_json_bytes(payload).decode();sha=digest(payload)
    if len(extra_columns)!=len(extra_values) or any(not __import__('re').fullmatch('[a-z_]+',key) for key in extra_columns):
        raise RoutingExecutionError('ROUTING_LINEAGE_INVALID')
    columns=('tenant_id','workspace_id','record_id','aggregate_id','version','schema_version',
        'canonical_json','canonical_text','digest_sha256',*(('state',) if state is not None else ()),
        'created_by','trace_id',*extra_columns)
    values=(ctx.tenant_id,ctx.workspace_id,record_id,record_id,1,1,Jsonb(payload),encoded,sha,
        *((state,) if state is not None else ()),ctx.actor_id,ctx.trace_id,*extra_values)
    selected=('canonical_text','digest_sha256','aggregate_id','version','schema_version','created_by','trace_id',
        *(('state',) if state is not None else ()),*extra_columns)
    selection=','.join(selected)
    row=conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s']*len(columns))}) "
        f"ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING RETURNING {selection}",values).fetchone()
    if row is None:
        row=conn.execute(f'SELECT {selection} FROM {table} WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s FOR SHARE',
            (ctx.tenant_id,ctx.workspace_id,record_id)).fetchone()
    if row is None or len(row)!=len(selected):raise RoutingExecutionError('ROUTING_LINEAGE_INVALID')
    found=dict(zip(selected,row))
    # Shared immutable catalog parents retain their original creator/trace; Run/Attempt ownership is exact.
    shared=table in {'provider_profiles','model_artifacts','model_deployments','knowledge_scopes'}
    normalize=lambda value:value.obj if isinstance(value,Jsonb) else value
    if (any(normalize(found.get(k))!=normalize(v) for k,v in {'canonical_text':encoded,'digest_sha256':sha,'aggregate_id':record_id,
                'version':1,'schema_version':1,**dict(zip(extra_columns,extra_values))}.items())
            or not shared and found['created_by']!=ctx.actor_id
            or state is not None and found['state']!=state
            or any(type(found[k]) is not str or not found[k] for k in ('created_by','trace_id'))):
        raise RoutingExecutionError('IDEMPOTENCY_KEY_REUSED')
