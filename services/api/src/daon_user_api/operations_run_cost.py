"""명시 정책에 한정한 비용 판정. DB/Provider/호출·통화 추정은 하지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import re

_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
_USD_ENDPOINT='https://api.upstage.ai/v1'
_NANO=Decimal('0.000000001')
_MILLION=Decimal(1_000_000)
_ZERO=Decimal(0)

def _id(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError('RUN_COST_INPUT_INVALID')


def parse_run_cost_settings(policies_json,model_context_json):
    """기동 설정만 파싱한다. 정책/확인 시각을 만들어 내거나 외부 조회하지 않는다."""
    from .operations_run_cost_postgres import parse_cost_policy_document,RunCostSessionFactory
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('RUN_COST_CONFIGURATION_INVALID')
            result[key]=value
        return result
    def constant(_value):raise ValueError('RUN_COST_CONFIGURATION_INVALID')
    try:
        if (type(policies_json) is not str or len(policies_json.encode('utf-8'))>65536
            or type(model_context_json) is not str or len(model_context_json.encode('utf-8'))>8192):
            raise ValueError('RUN_COST_CONFIGURATION_INVALID')
        raw=json.loads(policies_json,object_pairs_hook=pairs,parse_constant=constant)
        context=json.loads(model_context_json,object_pairs_hook=pairs,parse_constant=constant)
        if type(raw) is not list or len(raw)>64 or type(context) is not dict:
            raise ValueError('RUN_COST_CONFIGURATION_INVALID')
        policies=tuple(parse_cost_policy_document(item) for item in raw)
        evidence=None
        if context:
            if set(context)!={'model_alias','resolved_model','context_tokens','source_url','checked_at'} or type(context['checked_at']) is not str:
                raise ValueError('RUN_COST_CONFIGURATION_INVALID')
            evidence=UpstageModelContextEvidence(**{**context,'checked_at':_utc(datetime.fromisoformat(context['checked_at']))})
        # 첫 adapter/요율의 닫힌 실행 계약·중복을 설정 시점에도 검증한다. DB/clock 호출0.
        RunCostSessionFactory(None,policies=policies,evidence=evidence,clock=None)
        return policies,evidence
    except (ValueError,TypeError,KeyError,UnicodeError,ArithmeticError):
        raise ValueError('RUN_COST_CONFIGURATION_INVALID') from None

def _utc(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset()!=timedelta(0):
        raise ValueError('RUN_COST_TIME_INVALID')
    return value

def _money(value):
    if type(value) is not Decimal or not value.is_finite() or not _ZERO<=value<Decimal('1000000000000'):
        raise ValueError('RUN_COST_AMOUNT_INVALID')
    return value

@dataclass(frozen=True,slots=True)
class PriceCard:
    version:str
    base_url:str
    model_id:str
    currency:str
    input_per_million:Decimal
    cached_input_per_million:Decimal
    output_per_million:Decimal
    valid_from:datetime
    valid_until:datetime

    def __post_init__(self):
        _id(self.version);_id(self.model_id)
        if type(self.base_url) is not str or type(self.currency) is not str or re.fullmatch('[A-Z]{3}',self.currency) is None:
            raise ValueError('RUN_COST_PRICE_INVALID')
        for amount in (self.input_per_million,self.cached_input_per_million,self.output_per_million):
            _money(amount)
            if amount>Decimal('1000000') or amount.as_tuple().exponent < -9:
                raise ValueError('RUN_COST_PRICE_INVALID')
        if self.cached_input_per_million>self.input_per_million:
            raise ValueError('RUN_COST_PRICE_INVALID')
        if not timedelta(0)<_utc(self.valid_until)-_utc(self.valid_from)<=timedelta(hours=24):
            raise ValueError('RUN_COST_PRICE_INVALID')

    def current(self,now):
        return self.valid_from<=_utc(now)<self.valid_until

@dataclass(frozen=True,slots=True)
class RunCostScope:
    tenant_id:str
    workspace_id:str
    actor_id:str
    connection_id:str
    model_id:str
    run_kind:str
    run_id:str
    base_url:str

    def __post_init__(self):
        for value in (self.tenant_id,self.workspace_id,self.actor_id,self.connection_id,self.model_id,self.run_id):_id(value)
        if self.run_kind not in {'question','document'} or type(self.base_url) is not str:
            raise ValueError('RUN_COST_SCOPE_INVALID')

@dataclass(frozen=True,slots=True)
class RunCostPolicy:
    policy_id:str
    version:int
    tenant_id:str
    workspace_id:str
    actor_id:str
    connection_id:str
    model_id:str
    run_kind:str
    currency:str
    limit_minor_units:int
    price_card:PriceCard

    def __post_init__(self):
        for value in (self.policy_id,self.tenant_id,self.workspace_id,self.actor_id,self.connection_id,self.model_id):_id(value)
        if (type(self.version) is not int or not 1<=self.version<=2**31-1
            or type(self.limit_minor_units) is not int or not 0<=self.limit_minor_units<=10**12
            or self.run_kind not in {'question','document'} or type(self.currency) is not str
            or re.fullmatch('[A-Z]{3}',self.currency) is None or type(self.price_card) is not PriceCard):
            raise ValueError('RUN_COST_POLICY_INVALID')

    def matches(self,scope):
        return type(scope) is RunCostScope and (
            self.tenant_id,self.workspace_id,self.actor_id,self.connection_id,self.model_id,self.run_kind
        )==(
            scope.tenant_id,scope.workspace_id,scope.actor_id,scope.connection_id,scope.model_id,scope.run_kind
        ) and self.currency==self.price_card.currency=='USD' and scope.model_id==self.price_card.model_id=='solar-pro4' and scope.base_url==self.price_card.base_url==_USD_ENDPOINT

@dataclass(frozen=True,slots=True)
class CostEvent:
    sequence:int
    attempt_key:str
    state:str
    amount:Decimal|None
    currency:str|None

    def __post_init__(self):
        _id(self.attempt_key)
        if type(self.sequence) is not int or not 1<=self.sequence<=2**63-1 or self.state not in {'reserved','known','uncertain'}:
            raise ValueError('RUN_COST_EVENT_INVALID')
        if self.state=='uncertain':
            if self.amount is not None or self.currency is not None:raise ValueError('RUN_COST_EVENT_INVALID')
        elif type(self.currency) is not str or re.fullmatch('[A-Z]{3}',self.currency) is None:
            raise ValueError('RUN_COST_EVENT_INVALID')
        else:_money(self.amount)

@dataclass(frozen=True,slots=True)
class CostDecision:
    state:str
    safe_code:str
    estimated_maximum:Decimal|None
    known_total:Decimal|None
    reserved_total:Decimal|None
    currency:str|None
    price_version:str|None

@dataclass(frozen=True,slots=True)
class PriceCardCharge:
    amount:Decimal
    currency:str
    basis:str
    price_version:str
    usage_digest:str

    def __post_init__(self):
        _money(self.amount);_id(self.price_version)
        if (self.amount.as_tuple().exponent < -9 or self.basis!='price_card'
            or type(self.currency) is not str or re.fullmatch('[A-Z]{3}',self.currency) is None
            or type(self.usage_digest) is not str or re.fullmatch('[a-f0-9]{64}',self.usage_digest) is None):
            raise ValueError('RUN_COST_CHARGE_INVALID')

def quote_maximum(price_card,*,input_token_upper_bound,output_token_limit,now):
    if (type(price_card) is not PriceCard or not price_card.current(now)
        or type(input_token_upper_bound) is not int or not 0<=input_token_upper_bound<=2**31-1
        or type(output_token_limit) is not int or not 1<=output_token_limit<=512):
        raise ValueError('RUN_COST_BOUND_UNCONFIRMED')
    # 사전 견적은 cache 할인을 가정하지 않는다.
    return ((Decimal(input_token_upper_bound)*price_card.input_per_million
        +Decimal(output_token_limit)*price_card.output_per_million)/_MILLION).quantize(_NANO,rounding=ROUND_CEILING)

def _totals(events,currency):
    if type(events) is not tuple:raise ValueError('RUN_COST_LEDGER_INVALID')
    latest={}
    for number,event in enumerate(events,1):
        if type(event) is not CostEvent or event.sequence!=number:
            raise ValueError('RUN_COST_LEDGER_INVALID')
        prior=latest.get(event.attempt_key)
        if (event.currency is not None and event.currency!=currency
            or prior is None and event.state!='reserved'
            or prior is not None and (prior.state!='reserved' or event.state=='reserved'
                or event.state=='known' and event.amount>prior.amount)):
            raise ValueError('RUN_COST_LEDGER_INVALID')
        latest[event.attempt_key]=event
    known=sum((x.amount for x in latest.values() if x.state=='known'),_ZERO)
    reserved=sum((x.amount for x in latest.values() if x.state=='reserved'),_ZERO)
    unconfirmed=any(x.state in {'reserved','uncertain'} for x in latest.values())
    return known,reserved,unconfirmed,latest

def decide_run_cost(*,policy,scope,input_token_upper_bound,output_token_limit,events,attempt_key,now):
    currency=None if type(policy) is not RunCostPolicy else policy.currency
    version=None if type(policy) is not RunCostPolicy else policy.price_card.version
    def result(state,code,estimate=None,known=None,reserved=None):
        return CostDecision(state,code,estimate,known,reserved,currency,version)
    if type(policy) is not RunCostPolicy or not policy.matches(scope):
        return result('unknown','COST_POLICY_UNCONFIRMED')
    try:
        _id(attempt_key)
        estimate=quote_maximum(policy.price_card,input_token_upper_bound=input_token_upper_bound,output_token_limit=output_token_limit,now=now)
        known,reserved,unconfirmed,latest=_totals(events,policy.currency)
    except (ValueError,ArithmeticError,TypeError):
        return result('unknown','COST_EVIDENCE_UNCONFIRMED')
    if attempt_key in latest:
        return result('blocked','COST_ATTEMPT_ALREADY_RECORDED',estimate,known,reserved)
    if unconfirmed:
        return result('unknown','COST_SETTLEMENT_UNCONFIRMED',estimate,known,reserved)
    if known+reserved+estimate>Decimal(policy.limit_minor_units)/Decimal(100):
        return result('blocked','COST_LIMIT_EXCEEDED',estimate,known,reserved)
    return result('allowed','COST_WITHIN_RUN_BUDGET',estimate,known,reserved)

def price_actual_usage(price_card,*,usage,now):
    """정확 usage+승인 단가의 산정액. 청구서·사전 견적이 아니다."""
    if type(price_card) is not PriceCard or type(usage) is not dict:
        return None
    try:
        if not price_card.current(now):return None
        prompt,completion,cached=(usage.get(k) for k in ('prompt_tokens','completion_tokens','cached_input_tokens'))
        if any(type(x) is not int or not 0<=x<=2**31-1 for x in (prompt,completion,cached)) or cached>prompt:
            return None
        value=(Decimal(prompt-cached)*price_card.input_per_million+Decimal(cached)*price_card.cached_input_per_million
            +Decimal(completion)*price_card.output_per_million)/_MILLION
        digest=hashlib.sha256(json.dumps(['measured-usage-price-card-v1',price_card.version,prompt,completion,cached],separators=(',',':')).encode()).hexdigest()
        return PriceCardCharge(value.quantize(_NANO,rounding=ROUND_CEILING),price_card.currency,'price_card',price_card.version,digest)
    except (ValueError,ArithmeticError,TypeError):
        return None


@dataclass(frozen=True,slots=True)
class UpstageModelContextEvidence:
    model_alias:str
    resolved_model:str
    context_tokens:int
    source_url:str
    checked_at:datetime

    def __post_init__(self):
        _utc(self.checked_at)
        if (self.model_alias!='solar-pro4' or self.resolved_model!='solar-pro4-260806'
            or type(self.context_tokens) is not int or self.context_tokens!=524288
            or self.source_url!='https://console.upstage.ai/docs/models/solar-pro-4'):
            raise ValueError('UPSTAGE_CONTEXT_EVIDENCE_INVALID')


def verified_upstage_input_bound(scope,payload,evidence,*,now):
    """검증 문서의 전체 context 상한. bytes를 실제 token 수로 환산하지 않는다."""
    if (type(scope) is not RunCostScope or scope.model_id!='solar-pro4' or scope.base_url!=_USD_ENDPOINT
        or type(evidence) is not UpstageModelContextEvidence or type(payload) is not dict):
        return None
    try:
        if not timedelta(0)<=_utc(now)-evidence.checked_at<timedelta(hours=24):return None
        allowed={'model','messages','max_tokens','response_format','temperature','top_p','reasoning_effort'}
        if (set(payload)-allowed or not {'model','messages','max_tokens'}<=set(payload)
            or payload['model']!=evidence.model_alias or type(payload['max_tokens']) is not int
            or not 1<=payload['max_tokens']<=512 or type(payload['messages']) is not list
            or not 1<=len(payload['messages'])<=256):return None
        for message in payload['messages']:
            if (type(message) is not dict or set(message)!={'role','content'}
                or message['role'] not in {'system','user','assistant'} or type(message['content']) is not str):return None
        # ASCII escaping은 비ASCII wire보다 같거나 크다. system/schema/전체messages를 포함한다.
        wire=json.dumps(payload,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('utf-8')
        if len(wire)>65536:return None
        if 'response_format' in payload and type(payload['response_format']) is not dict:return None
        for name,maximum in (('temperature',2),('top_p',1)):
            if name in payload and (type(payload[name]) not in {int,float} or not 0<=payload[name]<=maximum):return None
        if 'reasoning_effort' in payload and payload['reasoning_effort'] not in {'none','low','medium','high'}:return None
        # Provider context에는 입력/출력이 함께 포함된다. 전체context를입력한도로잡고
        # 출력은별도가산해프레이밍을누락하지않는보수상한으로만사용한다.
        return evidence.context_tokens
    except (ValueError,TypeError,OverflowError,UnicodeError):
        return None


def normalize_upstage_usage(response,evidence):
    """공식 raw 응답의 버전·명시 nested cache만 소비한다. 누락=0 아님."""
    if type(response) is not dict or type(evidence) is not UpstageModelContextEvidence or response.get('model')!=evidence.resolved_model:
        return None
    usage=response.get('usage')
    if type(usage) is not dict or type(usage.get('prompt_tokens_details')) is not dict:return None
    prompt=usage.get('prompt_tokens');completion=usage.get('completion_tokens');total=usage.get('total_tokens')
    cached=usage['prompt_tokens_details'].get('cached_tokens')
    if (any(type(x) is not int or x<0 for x in (prompt,completion,total,cached))
        or cached>prompt or completion>512 or total!=prompt+completion or total>evidence.context_tokens):return None
    return {'prompt_tokens':prompt,'completion_tokens':completion,'cached_input_tokens':cached}
