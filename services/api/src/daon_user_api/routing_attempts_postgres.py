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
