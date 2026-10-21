"""B2 frozen authorization and conservative dispatch orchestration.

No global enable flag or permissive default exists. The candidate/pricing/current
access ports must produce exact server proofs before any reservation or dispatch.
"""
from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
import secrets
from uuid import uuid4

from .data_canon import canonical_json_bytes
from .routing_policy import FrozenRoutePlan, digest

_ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$')
_HEX=re.compile(r'^[0-9a-f]{64}$')
_SAFE_CODES=frozenset({'RATE_LIMITED','TEMPORARILY_UNAVAILABLE','CAPACITY_UNAVAILABLE','TIMEOUT',
    'AUTH_DENIED','EGRESS_POLICY_DENIED','POLICY_CHANGED','INVALID_REQUEST','INVALID_RESPONSE',
    'CANCELLED','TRANSPORT_UNCERTAIN','RESULT_PERSISTENCE_UNCERTAIN'})
_TRANSIENT=frozenset({'RATE_LIMITED','TEMPORARILY_UNAVAILABLE','CAPACITY_UNAVAILABLE','TIMEOUT'})


class RoutingExecutionError(RuntimeError):
    def __init__(self,code,status=409):
        self.code,self.status,self.retryable=code,status,False
        super().__init__(code)


def money(value):
    if type(value) is not str or not re.fullmatch(r'(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?',value):
        raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)
    try: amount=Decimal(value)
    except InvalidOperation: raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503) from None
    if not amount.is_finite() or amount<0:
        raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)
    return amount


def scope(context):
    if not context or not getattr(context,'session_id',None) or not getattr(context,'notebook_id',None):
        raise RoutingExecutionError('ROUTING_SESSION_REQUIRED',401)
    values={name:getattr(context,name) for name in ('tenant_id','workspace_id','actor_id','session_id','notebook_id')}
    if any(type(value) is not str or not _ID.fullmatch(value) for value in values.values()):
        raise RoutingExecutionError('ROUTING_SESSION_REQUIRED',401)
    return values


@dataclass(frozen=True,slots=True)
class PlanApproval:
    plan_digest: str
    actor_id: str
    session_id: str
    mode: str


@dataclass(frozen=True,slots=True)
class FrozenAuthorizationPlan:
    canonical_text: str
    digest_sha256: str

    def __post_init__(self):
        try:
            value=json.loads(self.canonical_text)
            if (set(value)!={'schema_version','scope','plan_id','run_id','route_plan_digest','request_digest',
                    'policy_version_id','policy_digest','authorization_policy_version','candidates','budget',
                    'source_scope_digest','settings_digest','realm','expires_at','nonce_digest','parents'}
                    or value['schema_version']!='routing-authorization-v1'
                    or canonical_json_bytes(value).decode()!=self.canonical_text
                    or digest(value)!=self.digest_sha256 or not value['candidates']
                    or not _HEX.fullmatch(value['nonce_digest'])):
                raise ValueError
            validate_plan_projection(value)
        except (TypeError,ValueError,KeyError,AttributeError):
            raise RoutingExecutionError('ROUTING_AUTHORIZATION_INVALID',403) from None

    def as_dict(self): return json.loads(self.canonical_text)


@dataclass(frozen=True,slots=True)
class SuppressedResult:
    safe_code: str = 'ROUTING_RESULT_ACCESS_DENIED'


@dataclass(frozen=True,slots=True)
class PreparedAutoAuthorization:
    plan_id: str
    authorization: str=field(repr=False)
    expires_at: str
    run_id: str
    plan_digest: str
    requires_step_up: bool


@dataclass(frozen=True,slots=True)
class AttemptOutcome:
    outcome: str
    safe_code: str | None = None
    cost_state: str = 'unknown'
    amount: str | None = None
    currency: str | None = None
    receipt_id: str | None = None
    result: object | None = None
    partial_output: bool = False
    # A trusted adapter must establish this; a timeout alone cannot establish it.
    no_effect_uncertainty: bool = False

    def __post_init__(self):
        if (self.outcome not in {'succeeded','failed','uncertain'} or self.cost_state not in {'known','unknown','uncertain'}
                or self.safe_code is not None and self.safe_code not in _SAFE_CODES
                or type(self.partial_output) is not bool or type(self.no_effect_uncertainty) is not bool):
            raise RoutingExecutionError('ROUTING_RESULT_INVALID')
        if self.cost_state=='known':
            money(self.amount)
            if not self.currency or not re.fullmatch('[A-Z]{3}',self.currency) or not self.receipt_id:
                raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)
        elif any(x is not None for x in (self.amount,self.currency,self.receipt_id)):
            raise RoutingExecutionError('ROUTING_RESULT_INVALID')
        if self.outcome=='succeeded' and self.result is None:
            raise RoutingExecutionError('ROUTING_RESULT_INVALID')

    def safe_dict(self):
        return dict(outcome=self.outcome,safe_code=self.safe_code,cost_state=self.cost_state,
            amount=self.amount,currency=self.currency,receipt_id=self.receipt_id,
            partial_output=self.partial_output,no_effect_uncertainty=self.no_effect_uncertainty)

    def allows_fallback(self):
        return (self.outcome=='failed' and self.safe_code in _TRANSIENT and self.cost_state=='known'
            and self.no_effect_uncertainty and not self.partial_output)


def validate_quote(claims,now):
    required={'candidate','provider_kind','provider_code','artifact_digest','license_reference','endpoint_digest','protocol_version','transformed_payload_digest',
        'payload_bytes','egress_policy_digest','authorization_mode','required_approver','quote'}
    if type(claims) is not dict or set(claims)!=required:
        raise RoutingExecutionError('ROUTING_CANDIDATE_PROOF_INVALID',503)
    if (any(type(claims[k]) is not str or not _HEX.fullmatch(claims[k]) for k in
                ('endpoint_digest','egress_policy_digest','transformed_payload_digest'))
            or claims['provider_kind'] not in {'local','local_runtime','server_internal','external_api'}
            or type(claims['protocol_version']) is not str or not _ID.fullmatch(claims['protocol_version'])
            or claims['required_approver'] not in {'workspace_manager','organization_admin'}):
        raise RoutingExecutionError('ROUTING_CANDIDATE_PROOF_INVALID',503)
    if (type(claims['provider_code']) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',claims['provider_code'])
            or type(claims['artifact_digest']) is not str or not re.fullmatch('sha256:[0-9a-f]{64}',claims['artifact_digest'])
            or type(claims['license_reference']) is not str or not _ID.fullmatch(claims['license_reference'])):
        raise RoutingExecutionError('ROUTING_CANDIDATE_PROOF_INVALID',503)
    quote=claims['quote']
    if (type(quote) is not dict or set(quote)!={'maximum_estimate','currency','pricing_reference','price_version',
            'candidate_reference_digest','protocol_version','transformed_payload_digest','expires_at','billing_reference'}
            or quote['candidate_reference_digest']!=digest(claims['candidate'])
            or quote['protocol_version']!=claims['protocol_version']
            or quote['transformed_payload_digest']!=claims['transformed_payload_digest']
            or not quote['pricing_reference'] or not quote['price_version']
            or not _HEX.fullmatch(claims['transformed_payload_digest'])
            or type(claims['payload_bytes']) is not int or not 0<claims['payload_bytes']<=1048576
            or claims['authorization_mode'] not in {'policy_approved','step_up_required'}):
        raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)
    try:
        expiry=datetime.fromisoformat(quote['expires_at'])
        if expiry.tzinfo is None or now>=expiry: raise ValueError
    except (TypeError,ValueError): raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503) from None
    billing=quote['billing_reference']
    if (type(billing) is not dict or set(billing)!={'scope_id','provider_code','account_reference'}
            or any(type(billing[k]) is not str or not _ID.fullmatch(billing[k]) for k in ('scope_id','provider_code'))
            or type(billing['account_reference']) is not str or not _HEX.fullmatch(billing['account_reference'])
            or billing['provider_code']!=claims['provider_code']):
        raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)
    money(quote['maximum_estimate'])
    if type(quote['currency']) is not str or not re.fullmatch('[A-Z]{3}',quote['currency']):
        raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)


def validate_plan_projection(value):
    bound=value['scope']
    if (type(bound) is not dict or set(bound)!={'tenant_id','workspace_id','actor_id','session_id','notebook_id'}
            or any(type(v) is not str or not _ID.fullmatch(v) for v in bound.values())):
        raise ValueError
    for key in ('plan_id','run_id','policy_version_id','authorization_policy_version'):
        if type(value[key]) is not str or not _ID.fullmatch(value[key]):raise ValueError
    for key in ('route_plan_digest','request_digest','policy_digest','source_scope_digest','settings_digest','nonce_digest'):
        if type(value[key]) is not str or not _HEX.fullmatch(value[key]):raise ValueError
    if value['realm'] not in {'cloud_sync','local_private','organization'}:raise ValueError
    if type(value['candidates']) is not list or not 1<=len(value['candidates'])<=8:raise ValueError
    keys={'connection_id','model_id','deployment_id','connection_version','catalog_version','credential_version',
        'endpoint_version','credential_source','upper_budget_reference'}
    identities=[]
    for entry in value['candidates']:
        validate_quote(entry,datetime.min.replace(tzinfo=timezone.utc))
        candidate=entry['candidate']
        if type(candidate) is not dict or set(candidate)!=keys:raise ValueError
        for k in ('connection_id','model_id','deployment_id'):
            if type(candidate[k]) is not str or not candidate[k] or len(candidate[k])>255:raise ValueError
        for k in ('connection_version','catalog_version','credential_version','endpoint_version'):
            if type(candidate[k]) is not int or candidate[k]<(0 if k=='credential_version' else 1):raise ValueError
        if candidate['credential_source'] not in {'workspace','personal'}:raise ValueError
        if type(candidate['upper_budget_reference']) is not str or not _HEX.fullmatch(candidate['upper_budget_reference']):raise ValueError
        identities.append((candidate['connection_id'],candidate['model_id']))
    if len(set(identities))!=len(identities):raise ValueError
    parents=value['parents']
    if type(parents) is not dict or set(parents)!={'conversation_id','scope_id','scope_snapshot_id',
            'routing_decision_id','run_snapshot_id','run_result_id','egress_decision_ids','models'}:raise ValueError
    for k in set(parents)-{'models','egress_decision_ids'}:
        if type(parents[k]) is not str or not _ID.fullmatch(parents[k]):raise ValueError
    if len(parents['models'])!=len(identities) or len(parents['egress_decision_ids'])!=len(identities):raise ValueError
    for model in parents['models']:
        if type(model) is not dict or set(model)!={'profile_id','deployment_id','artifact_id'}:raise ValueError
        if any(type(v) is not str or not _ID.fullmatch(v) for v in model.values()):raise ValueError
    if any(type(v) is not str or not _ID.fullmatch(v) for v in parents['egress_decision_ids']):raise ValueError
    budget=value['budget']
    if type(budget) is not dict or set(budget)!={'scope_id','source_id','version','currency','run_limit',
            'period_limit','starts_at','expires_at','max_attempts'}:raise ValueError
    for k in ('scope_id','source_id','version'):
        if type(budget[k]) is not str or not _ID.fullmatch(budget[k]):raise ValueError
    if type(budget['currency']) is not str or not re.fullmatch('[A-Z]{3}',budget['currency']):raise ValueError
    money(budget['run_limit']);money(budget['period_limit'])
    if type(budget['max_attempts']) is not int or not 1<=budget['max_attempts']<=len(identities):raise ValueError
    start=datetime.fromisoformat(budget['starts_at']);end=datetime.fromisoformat(budget['expires_at'])
    expires=datetime.fromisoformat(value['expires_at'])
    if start.tzinfo is None or end.tzinfo is None or expires.tzinfo is None or not start<expires<=end:raise ValueError


class RoutingExecutionService:
    def __init__(self,repository,candidate_port,*,clock=None):
        self.repository,self.candidate_port=repository,candidate_port
        self.clock=clock or (lambda:datetime.now(timezone.utc))

    def prepare(self,context,route_plan,request_digest,run_id,*,authorize_plan=None):
        if self.repository is None or self.candidate_port is None:
            raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
        bound=scope(context)
        if not isinstance(route_plan,FrozenRoutePlan) or not _HEX.fullmatch(request_digest or '') or not _ID.fullmatch(run_id or ''):
            raise RoutingExecutionError('ROUTING_AUTHORIZATION_INVALID',403)
        route=route_plan.as_dict()
        if any(route.get(k)!=v for k,v in bound.items() if k!='session_id') or not route['candidates']:
            raise RoutingExecutionError('ROUTING_PLAN_NOT_READY',503)
        now=self.clock();entries=[]
        for item in route['candidates']:
            claims=self.candidate_port.freeze(context,item)
            validate_quote(claims,now)
            if claims['candidate']!=item['candidate']:
                raise RoutingExecutionError('ROUTING_CANDIDATE_CHANGED',409)
            entries.append(claims)
        budget=self.candidate_port.budget(context,route)
        if (type(budget) is not dict or set(budget)!={'scope_id','source_id','version','currency','run_limit',
                'period_limit','starts_at','expires_at','max_attempts'} or not budget['source_id'] or not budget['version']
                or budget['currency']!=route['budget']['currency']
                or money(budget['run_limit'])>money(route['budget']['run_limit'])
                or type(budget['max_attempts']) is not int
                or not 0<budget['max_attempts']<=min(len(entries),route['budget']['max_attempts'])):
            raise RoutingExecutionError('ROUTING_BUDGET_UNVERIFIED',503)
        money(budget['period_limit'])
        try:
            starts=datetime.fromisoformat(budget['starts_at'])
            if starts.tzinfo is None or starts>now:raise ValueError
        except (TypeError,ValueError):raise RoutingExecutionError('ROUTING_BUDGET_UNVERIFIED',503) from None
        for entry in entries:
            if (entry['quote']['currency']!=budget['currency']
                    or money(entry['quote']['maximum_estimate'])>money(budget['run_limit'])):
                raise RoutingExecutionError('ROUTING_BUDGET_UNVERIFIED',503)
        expires=now+timedelta(minutes=5)
        for raw in [budget['expires_at'],*(entry['quote']['expires_at'] for entry in entries)]:
            try:
                expiry=datetime.fromisoformat(raw)
                if expiry.tzinfo is None or now>=expiry: raise ValueError
            except (TypeError,ValueError): raise RoutingExecutionError('ROUTING_BUDGET_UNVERIFIED',503) from None
            expires=min(expires,expiry)
        nonce=secrets.token_urlsafe(32);plan_id='routing-plan-'+uuid4().hex
        parents=self.candidate_port.parents(context,route,entries,run_id,request_digest)
        value=dict(schema_version='routing-authorization-v1',scope=bound,plan_id=plan_id,run_id=run_id,
            route_plan_digest=route_plan.digest_sha256,request_digest=request_digest,
            policy_version_id=route['policy_version_id'],policy_digest=route['policy_digest'],
            authorization_policy_version=route['authorization_policy_version'],candidates=entries,budget=budget,
            source_scope_digest=route['source_scope_digest'],settings_digest=route['settings_digest'],realm=route['realm'],
            expires_at=expires.isoformat(),nonce_digest=hashlib.sha256(nonce.encode()).hexdigest(),parents=parents)
        plan=FrozenAuthorizationPlan(canonical_json_bytes(value).decode(),digest(value))
        requires=any(item['authorization_mode']=='step_up_required' for item in entries)
        approval=None if authorize_plan is None else authorize_plan(plan,requires)
        if (not isinstance(approval,PlanApproval) or approval.plan_digest!=plan.digest_sha256
                or (approval.actor_id,approval.session_id)!=(context.actor_id,context.session_id)
                or approval.mode not in {'reauthenticated','policy_approved'}
                or requires and approval.mode!='reauthenticated'):
            raise RoutingExecutionError('ROUTING_AUTHORIZATION_REQUIRED',401)
        self.repository.prepare(context,plan,recheck=self.candidate_port.recheck,materialize=self.candidate_port.materialize)
        return PreparedAutoAuthorization(plan_id,nonce,expires.isoformat(),run_id,plan.digest_sha256,requires)

    def execute(self,context,*,plan_id,authorization,request_digest,idempotency_key):
        if self.repository is None or self.candidate_port is None:
            raise RoutingExecutionError('ROUTING_EXECUTION_UNAVAILABLE',503)
        scope(context)
        plan=self.repository.read(context,plan_id)
        frozen=plan.as_dict()
        if (frozen['scope']!=scope(context) or frozen['request_digest']!=request_digest
                or not isinstance(authorization,str) or not hmac.compare_digest(
                    hashlib.sha256(authorization.encode()).hexdigest(),frozen['nonce_digest'])):
            raise RoutingExecutionError('ROUTING_AUTHORIZATION_INVALID',403)
        owner=self.repository.claim(context,plan,idempotency_key=idempotency_key,
            request_digest=request_digest,recheck=self.candidate_port.recheck)
        if not owner:
            return self.repository.result(context,plan)
        limit=min(frozen['budget']['max_attempts'],len(frozen['candidates']))
        for order,entry in enumerate(frozen['candidates'][:limit],1):
            claimed=False
            try:
                with self.candidate_port.open(context,plan,entry) as call:
                    validate_quote(call.claims,self.clock())
                    if call.claims!=entry:
                        raise RoutingExecutionError('ROUTING_CANDIDATE_CHANGED',409)
                    attempt_id=self.repository.claim_attempt(context,plan,order,recheck=self.candidate_port.recheck)
                    claimed=True
                    try: outcome=call.invoke()
                    except Exception:
                        outcome=AttemptOutcome('uncertain','TRANSPORT_UNCERTAIN','uncertain')
                    if not isinstance(outcome,AttemptOutcome):
                        outcome=AttemptOutcome('uncertain','INVALID_RESPONSE','uncertain')
                    fallback=outcome.allows_fallback() and order<limit
                    result=self.repository.finish(context,plan,order,attempt_id,outcome,
                        final=not fallback,write_result=self.candidate_port.write_result)
                    if isinstance(result,SuppressedResult):raise RoutingExecutionError(result.safe_code,403)
                    if outcome.outcome=='succeeded': return result
                    if not fallback:
                        raise RoutingExecutionError('ROUTING_RUN_UNCERTAIN' if outcome.outcome=='uncertain'
                            else 'ROUTING_RUN_FAILED',409)
            except BaseException:
                if claimed:
                    # Receipt loss/commit uncertainty never authorizes a second dispatch.
                    self.repository.mark_uncertain(context,plan,order)
                raise
        raise RoutingExecutionError('ROUTING_CANDIDATES_EXHAUSTED',409)


def _question_digest(question,source_id,source_version_id,context_mode,context_sources,policy_version_id):
    return digest(dict(question=question,source_id=source_id,source_version_id=source_version_id,
        context_mode=context_mode,policy_version_id=policy_version_id,sources=[dict(origin=x.origin,
            context_item_id=x.context_item_id,source_id=x.source_id,source_version_id=x.source_version_id,
            digest_sha256=x.digest_sha256) for x in (context_sources or ())]))


class QuestionAutoExecution:
    """Production composition. Missing observations/pricing/budget close the real flow.

    A new scoped port is made for each request; question/evidence/credentials are
    never cached in a global engine or duplicated into the authorization ledger.
    """
    def __init__(self,policy_service,repository,model_resolver,store,*,pricing_port=None,budget_source=None,clock=None):
        self.policy_service,self.repository,self.model_resolver,self.store=policy_service,repository,model_resolver,store
        self.pricing_port,self.budget_source=pricing_port,budget_source
        self.clock=clock or (lambda:datetime.now(timezone.utc))

    def _port(self,service,context,options):
        sources,evidence,general=service._prepare_route_inputs(context,source_id=options['source_id'],
            source_version_id=options['source_version_id'],question=options['question'],context_sources=options['context_sources'])
        return _ScopedQuestionPort(self,service,context,options,sources,evidence,general)

    def prepare_question(self,service,context,**options):
        from .routing_policy import RouteRequestSnapshot
        from .workspace_model_defaults import WorkspaceModelDefaultsContext
        port=self._port(service,context,options)
        context_model=WorkspaceModelDefaultsContext(context.tenant_id,context.workspace_id,context.actor_id,
            context.trace_id,context.policy_version)
        request=RouteRequestSnapshot(context.notebook_id,port.input_digest,port.source_digest,port.settings_digest,port.realm())
        route=self.policy_service.prepare(context_model,request,policy_version_id=options['policy_version_id'])
        engine=RoutingExecutionService(self.repository,port,clock=self.clock)
        return engine.prepare(context,route,port.request_digest,options['run_id'],authorize_plan=options.get('authorize_plan'))

    def execute_question(self,service,context,**options):
        port=self._port(service,context,options)
        engine=RoutingExecutionService(self.repository,port,clock=self.clock)
        return engine.execute(context,plan_id=options['plan_id'],authorization=options['authorization'],
            request_digest=port.request_digest,idempotency_key=options['idempotency_key'])


class _ScopedQuestionPort:
    def __init__(self,owner,service,context,options,sources,evidence,general):
        self.owner,self.service,self.context,self.options=owner,service,context,options
        self.sources,self.evidence,self.general=sources,evidence,general
        self.input_digest=digest(dict(question=options['question'],sources=[x.source_version_id for x in sources]))
        self.source_digest=digest(dict(sources=[dict(origin=x.origin,id=x.context_item_id,source_id=x.source_id,
            version=x.source_version_id,digest=x.digest_sha256) for x in sources],
            evidence=[dict(id=x.chunk_id,digest=hashlib.sha256(x.text.encode()).hexdigest()) for x in evidence]))
        self.settings_digest=digest(dict(context_mode=options['context_mode'],general=general,prompt_version='question-auto-v1'))
        self.request_digest=_question_digest(options['question'],options['source_id'],options['source_version_id'],
            options['context_mode'],options['context_sources'],options['policy_version_id'])

    def realm(self):
        from .cloud_storage import CloudAccessContext
        with self.owner.store._transaction(CloudAccessContext(self.context.tenant_id,self.context.workspace_id,
                self.context.actor_id,'routing_execution.preflight')) as conn:
            self._current_access(conn,self.context)
            row=conn.execute('SELECT data_area FROM identity_auth_workspaces WHERE tenant_id=%s AND workspace_id=%s',
                (self.context.tenant_id,self.context.workspace_id)).fetchone()
            if not row or row[0] not in {'cloud_sync','local_private','organization'}:
                raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403)
            return row[0]

    def _current_access(self,conn,ctx):
        from .authorization import Action
        from .open_law_source_access import _current_auth_grant,require_open_law_source_access
        from .question_answering_postgres import PostgresQuestionAnsweringRepository as Q
        _current_auth_grant(ctx,conn,Action.QUERY)
        row=conn.execute('SELECT state,access_expires_at FROM identity_sessions '
            'WHERE tenant_id=%s AND user_id=%s AND session_id=%s',
            (ctx.tenant_id,ctx.actor_id,ctx.session_id)).fetchone()
        if not row or row[0]!='active':raise RoutingExecutionError('ROUTING_SESSION_REQUIRED',401)
        expiry=row[1] if isinstance(row[1],datetime) else datetime.fromisoformat(row[1])
        if expiry.tzinfo is None or self.owner.clock()>=expiry:raise RoutingExecutionError('ROUTING_SESSION_REQUIRED',401)
        for item in self.sources:
            Q._require_source_access(conn,ctx,item.source_version_id)
            require_open_law_source_access(ctx,item.source_version_id,conn,action=Action.QUERY)
        Q.bind_knowledge_selections(conn,ctx,tuple(self.sources))

    @staticmethod
    def _current_egress(conn,ctx):
        from .egress_policy import EgressPolicyContext,EgressPolicyService
        from .egress_policy_postgres import PostgresEgressPolicyRepository
        class Current:
            def current(self,context,kind):
                row=PostgresEgressPolicyRepository._select_current(conn,context,kind,
                    None if kind=='organization' else context.workspace_id,for_update=True)
                return PostgresEgressPolicyRepository._view(row)
        return EgressPolicyService(Current()).get_effective(EgressPolicyContext(ctx.tenant_id,ctx.tenant_id,
            ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version))

    def recheck(self,conn,ctx,plan):
        from .routing_policy import RoutingPolicyBody
        from .workspace_model_defaults import WorkspaceModelDefaultsContext
        p=plan.as_dict()
        if (p['scope']!=scope(ctx) or p['source_scope_digest']!=self.source_digest
                or p['settings_digest']!=self.settings_digest or p['request_digest']!=self.request_digest
                or self.owner.clock()>=datetime.fromisoformat(p['expires_at'])):
            raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403)
        self._current_access(conn,ctx)
        realm=conn.execute('SELECT data_area FROM identity_auth_workspaces WHERE tenant_id=%s AND workspace_id=%s',
            (ctx.tenant_id,ctx.workspace_id)).fetchone()
        if realm is None or realm[0]!=p['realm']:raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403)
        model_context=WorkspaceModelDefaultsContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,ctx.trace_id,ctx.policy_version)
        current=self.owner.policy_service.repository._current(conn,model_context)
        if (current is None or current[0]!=p['policy_version_id'] or current[3]!=p['policy_digest']):
            raise RoutingExecutionError('ROUTING_POLICY_CHANGED',403)
        policy=RoutingPolicyBody.model_validate(current[2])
        if not policy.enabled:raise RoutingExecutionError('ROUTING_POLICY_CHANGED',403)
        self.owner.policy_service.metadata.candidates(model_context,policy,connection=conn)
        effective=self._current_egress(conn,ctx)
        for entry in p['candidates']:
            mode='step_up_required' if entry['provider_kind']=='external_api' and effective.question_step_up_required else 'policy_approved'
            if (effective.fingerprint.removeprefix('sha256:')!=entry['egress_policy_digest']
                    or mode!=entry['authorization_mode'] or effective.required_approver!=entry['required_approver']):
                raise RoutingExecutionError('ROUTING_POLICY_CHANGED',403)
        fresh=self.budget(ctx,dict(budget=p['budget']))
        if fresh!=p['budget']:raise RoutingExecutionError('ROUTING_BUDGET_UNVERIFIED',503)

    def budget(self,ctx,route):
        if self.owner.budget_source is None:raise RoutingExecutionError('ROUTING_BUDGET_UNVERIFIED',503)
        return self.owner.budget_source(ctx,route)

    def _prepared(self,ctx,item,expected_quote=None):
        from contextlib import contextmanager
        from dataclasses import replace
        from .routing_policy import RoutingCandidateBody
        from .workspace_model_defaults import WorkspaceModelContext
        from .egress_policy import EgressPolicyContext
        from .question_egress import external_question_policy_matches
        from .authorization import Action,Permission
        from .open_law_source_access import _current_auth_grant,_CurrentAuthorizationRules,_AuthReadConnection
        from .cloud_storage import CloudAccessContext
        @contextmanager
        def opening():
            candidate=item['candidate'];request=RoutingCandidateBody.model_validate({k:candidate[k] for k in
                ('connection_id','model_id','credential_source','connection_version','catalog_version','credential_version','endpoint_version')})
            model_context=WorkspaceModelContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,
                'user' if request.credential_source=='personal' else 'system')
            with self.owner.model_resolver.resolve_candidate(model_context,request) as selected:
                prepared=(self.service._adapter_registry.prepare_general(selected,self.options['question'],ctx.trace_id,self.service._transport)
                    if self.general else self.service._adapter_registry.prepare(selected,self.evidence,self.options['question'],ctx.trace_id,self.service._transport))
                prepared=self.service._prepare_qa_structured_payload(ctx,prepared)
                wire=canonical_json_bytes(prepared.provider_payload)
                wire=self.service._egress.prepare_payload(ctx,wire)
                prepared=replace(prepared,provider_payload=json.loads(wire))
                policy_service=self.service._egress._policy_service
                effective=policy_service.get_effective(EgressPolicyContext(ctx.tenant_id,ctx.tenant_id,ctx.workspace_id,
                    ctx.actor_id,ctx.trace_id,ctx.policy_version))
                from urllib.parse import urlsplit
                destination=(urlsplit(selected.base_url).hostname or '').casefold()
                external=selected.provider_kind=='external_api'
                if external and not external_question_policy_matches(effective,provider_kind='external_api',destination=destination,
                        payload_bytes=len(wire),classification='internal',masking_required=True,redaction_required=True):
                    raise RoutingExecutionError('EGRESS_POLICY_DENIED',403)
                with self.owner.store._transaction(CloudAccessContext(ctx.tenant_id,ctx.workspace_id,ctx.actor_id,
                        'routing_execution.candidate')) as conn:
                    self._current_access(conn,ctx);grant=_current_auth_grant(ctx,conn,Action.QUERY)
                    permission=Permission.EXTERNAL_LLM if external else Permission.LOCAL_INTERNAL_LLM
                    decision=_CurrentAuthorizationRules()._effective_permission(_AuthReadConnection(conn),role=grant.role,
                        tenant_id=ctx.tenant_id,workspace_id=ctx.workspace_id,permission=permission,requested=True)
                    if not decision.effective:raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403)
                if self.owner.pricing_port is None:raise RoutingExecutionError('ROUTING_PRICE_UNCONFIRMED',503)
                protocol=('responses-v1' if selected.provider_code=='OMNIROUTE' else 'ollama-chat-v1'
                    if selected.provider_code=='OLLAMA' else 'anthropic-messages-v1' if selected.adapter_type=='anthropic_compatible'
                    else 'chat-completions-v1')
                quote=self.owner.pricing_port.quote(ctx,candidate,wire,protocol,expected=expected_quote)
                observed=item.get('observation',{})
                artifact_digest=item.get('artifact_digest') or observed.get('artifact_digest')
                license_reference=item.get('license_reference') or observed.get('license_reference')
                claims=dict(candidate=candidate,provider_kind=selected.provider_kind,provider_code=selected.provider_code,
                    artifact_digest=artifact_digest,license_reference=license_reference,
                    endpoint_digest=hashlib.sha256(selected.base_url.encode()).hexdigest(),protocol_version=protocol,
                    transformed_payload_digest=hashlib.sha256(wire).hexdigest(),payload_bytes=len(wire),
                    egress_policy_digest=effective.fingerprint.removeprefix('sha256:'),
                    authorization_mode='step_up_required' if external and effective.question_step_up_required else 'policy_approved',
                    required_approver=effective.required_approver,quote=quote)
                def invoke():
                    try:
                        result=(self.service._adapter_registry.generate_general(prepared) if self.general
                            else self.service._adapter_registry.generate_prepared(prepared))
                    except ValueError as error:
                        code='INVALID_RESPONSE' if str(error) in {'TEXT_GENERATION_RESPONSE_INVALID','TEXT_GENERATION_RESPONSE_UNSUPPORTED'} else 'TRANSPORT_UNCERTAIN'
                        return AttemptOutcome('failed' if code=='INVALID_RESPONSE' else 'uncertain',code,
                            'unknown' if code=='INVALID_RESPONSE' else 'uncertain')
                    cost=getattr(result,'reported_cost',None)
                    if cost is not None:
                        try:
                            amount=format(cost.amount,'f');money(amount)
                            return AttemptOutcome('succeeded',cost_state='known',amount=amount,currency=cost.currency,
                                receipt_id=cost.receipt_id,result=result)
                        except RoutingExecutionError:pass
                    return AttemptOutcome('succeeded',result=result)
                yield type('PreparedAutoCall',(),{'claims':claims,'invoke':staticmethod(invoke),
                    'selection':selected,'prepared':prepared,'destination':destination})()
        return opening()

    def freeze(self,ctx,item):
        with self._prepared(ctx,item) as call:return call.claims

    def open(self,ctx,plan,entry):
        return self._prepared(ctx,entry,expected_quote=entry['quote'])

    def parents(self,ctx,route,entries,run_id,request_digest):
        from .question_answering_postgres import PostgresQuestionAnsweringRepository as Q
        identify=Q._opaque_id
        models=[dict(profile_id=identify('provider',x['candidate']['connection_id'],str(x['candidate']['connection_version'])),
            artifact_id=identify('artifact',x['candidate']['deployment_id'],str(x['candidate']['catalog_version'])),
            deployment_id=identify('deployment',x['candidate']['deployment_id'],str(x['candidate']['connection_version']),
                str(x['candidate']['catalog_version']))) for x in entries]
        return dict(conversation_id=identify('conversation',run_id),scope_id=identify('scope',run_id),
            scope_snapshot_id=identify('scope-snapshot',run_id),routing_decision_id=identify('routing',run_id),
            run_snapshot_id=identify('run-snapshot',run_id),run_result_id=identify('run-result',run_id),models=models,
            egress_decision_ids=[identify('egress',run_id,str(i)) for i,_ in enumerate(entries)])

    def materialize(self,conn,ctx,plan):
        from .routing_attempts_postgres import insert_canon_exact as insert
        p=plan.as_dict();r=p['parents'];run_id=p['run_id'];versions=[x.source_version_id for x in self.sources]
        mode='general_ungrounded' if self.general else self.options['context_mode']
        source_items=[] if self.general else [dict(origin=x.origin,context_item_id=x.context_item_id,
            source_id=x.source_id,source_version_id=x.source_version_id,digest_sha256=x.digest_sha256) for x in self.sources]
        scope_payload=dict(source_version_ids=[] if self.general else versions,mode=mode,items=source_items)
        insert(conn,ctx,'knowledge_scopes',r['scope_id'],scope_payload)
        insert(conn,ctx,'scope_snapshots',r['scope_snapshot_id'],dict(knowledge_scope_id=r['scope_id'],**scope_payload),
            extra_columns=('knowledge_scope_id',),extra_values=(r['scope_id'],))
        for entry,model in zip(p['candidates'],r['models'],strict=True):
            candidate=entry['candidate']
            insert(conn,ctx,'provider_profiles',model['profile_id'],dict(configured_profile_id=candidate['connection_id'],
                provider_code=entry['provider_code'],binding_version=candidate['connection_version']))
            insert(conn,ctx,'model_artifacts',model['artifact_id'],dict(model_id=candidate['model_id'],catalog_version=candidate['catalog_version'],
                artifact_digest=entry['artifact_digest'],license_reference=entry['license_reference']))
            insert(conn,ctx,'model_deployments',model['deployment_id'],dict(configured_deployment_id=candidate['deployment_id'],
                model_id=candidate['model_id'],binding_version=candidate['connection_version']),
                extra_columns=('provider_profile_id','model_artifact_id'),extra_values=(model['profile_id'],model['artifact_id']))
        insert(conn,ctx,'conversations',r['conversation_id'],dict(current_run_id=run_id,current_run_result_id=r['run_result_id']))
        run_payload=dict(question=self.options['question'],source_id=None if self.general else self.options['source_id'],
            source_version_id=None if self.general else self.options['source_version_id'],context_mode=mode,
            request_fingerprint='sha256:'+p['request_digest'],routing_mode='auto',routing_plan_digest=plan.digest_sha256)
        insert(conn,ctx,'runs',run_id,run_payload,state='accepted',extra_columns=('conversation_id',),extra_values=(r['conversation_id'],))
        for entry,egress_id,model in zip(p['candidates'],r['egress_decision_ids'],r['models'],strict=True):
            insert(conn,ctx,'egress_decisions',egress_id,dict(run_id=run_id,provider_profile_id=model['profile_id'],
                allowed=True,reason='frozen_auto_plan',policy_digest=entry['egress_policy_digest'],
                transformed_payload_digest=entry['transformed_payload_digest']),
                extra_columns=('run_id','provider_profile_id'),extra_values=(run_id,model['profile_id']))
        candidate_order=[x['deployment_id'] for x in r['models']]
        insert(conn,ctx,'routing_decisions',r['routing_decision_id'],dict(run_id=run_id,candidate_order=candidate_order,
            reason='approved_auto_plan',routing_plan_digest=plan.digest_sha256),
            extra_columns=('run_id','routing_policy_version_id','egress_decision_id'),
            extra_values=(run_id,p['policy_version_id'],r['egress_decision_ids'][0]))
        snapshot=dict(source_version_ids=[] if self.general else versions,knowledge_scope_id=r['scope_id'],authority=[] if self.general else ['source'],
            weights_requested={},weights_effective={},weight_clamps=[],ruleset_snapshot_ids=[],routing_policy_version_id=p['policy_version_id'],
            candidate_order=candidate_order,data_area=p['realm'],data_classification='workspace_private',
            egress_decision_id=r['egress_decision_ids'][0],user_policy_version=ctx.policy_version,organization_policy_version=ctx.policy_version,
            cost_limit=p['budget']['run_limit'],currency=p['budget']['currency'],prompt_version='question-auto-v1',tool_version='document-index-v1',
            routing_plan_digest=plan.digest_sha256,scope_snapshot_digest=p['source_scope_digest'])
        insert(conn,ctx,'run_snapshots',r['run_snapshot_id'],snapshot,
            extra_columns=('run_id','scope_snapshot_id','routing_policy_version_id'),extra_values=(run_id,r['scope_snapshot_id'],p['policy_version_id']))

    def write_result(self,conn,ctx,plan,order,attempt,result):
        from .routing_attempts_postgres import insert_canon_exact as insert
        from .question_answering_postgres import PostgresQuestionAnsweringRepository as Q,StoredQuestionAnswer,StoredCitation
        from psycopg.types.json import Jsonb
        p=plan.as_dict();r=p['parents'];evidence={x.chunk_id:x for x in self.evidence};citations=[]
        from .open_law_source_access import OpenLawAccessError
        from .question_answering_postgres import QuestionRepositoryError
        try:
            self._current_access(conn,ctx)
        except (OpenLawAccessError,QuestionRepositoryError,RoutingExecutionError):
            raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403) from None
        for chunk_id in result.cited_chunk_ids:
            if chunk_id not in evidence:raise RoutingExecutionError('ROUTING_RESULT_INVALID')
            item=evidence[chunk_id];citation_id=Q._opaque_id('citation',r['run_result_id'],chunk_id)
            origin=next((x for x in self.sources if x.source_version_id==item.source_version_id),None)
            if origin is None:raise RoutingExecutionError('ROUTING_RESULT_INVALID')
            locator=dict(kind=item.locator_kind or 'page',value=item.locator_value or str(item.page))
            citations.append(StoredCitation(citation_id,item.source_id,item.source_version_id,item.evidence_span_id,
                item.page,origin.origin,origin.context_item_id,locator))
        version=Q._transition(conn,ctx,p['run_id'],4,'validating')
        insert(conn,ctx,'run_results',r['run_result_id'],dict(run_id=p['run_id'],answer=result.answer,
            insufficient=result.insufficient,citation_ids=[x.citation_id for x in citations]),
            extra_columns=('run_id','routing_decision_id','selected_model_attempt_id'),
            extra_values=(p['run_id'],r['routing_decision_id'],attempt))
        for item in citations:
            insert(conn,ctx,'citations',item.citation_id,dict(run_result_id=r['run_result_id'],source_id=item.source_id,
                source_version_id=item.source_version_id,evidence_span_id=item.evidence_span_id,page=item.page,
                origin=item.origin,context_item_id=item.context_item_id,locator=item.locator),
                extra_columns=('run_result_id','source_version_id','evidence_span_id'),
                extra_values=(r['run_result_id'],item.source_version_id,item.evidence_span_id))
        Q._transition(conn,ctx,p['run_id'],version,'completed')
        conn.execute('INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by) '
            "VALUES(%s,%s,%s,'conversation_thread',%s,NULL,%s) ON CONFLICT DO NOTHING",
            (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,r['conversation_id'],ctx.actor_id))
        entry=p['candidates'][order-1]
        provider_kind='local_runtime' if entry['provider_kind']=='local' else entry['provider_kind']
        return StoredQuestionAnswer(p['run_id'],r['run_result_id'],result.answer,result.insufficient,tuple(citations),provider_kind,
            dict(provider_kind=provider_kind,payload_bytes=entry['payload_bytes'],classification='internal',
                masking_required=True,redaction_required=True))
