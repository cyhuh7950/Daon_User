"""Run/Attempt 결속과 고정 예약 정산. 실제 SQL 원장은 후속 동일 모듈에 연결한다."""
from __future__ import annotations

from dataclasses import dataclass,replace
from datetime import datetime
from decimal import Decimal
import hashlib
import json

from .operations_run_cost import (PriceCard,RunCostPolicy,RunCostScope,CostDecision,
    _id,_money,_utc,price_actual_usage,decide_run_cost)
from .operations_telemetry import CostReceipt,make_price_card_cost_receipt
from .cloud_storage import CloudAccessContext


@dataclass(frozen=True,slots=True)
class CostReservation:
    scope:RunCostScope
    policy:RunCostPolicy
    attempt_key:str
    sequence:int
    maximum_amount:Decimal
    reserved_at:datetime

    def __post_init__(self):
        _id(self.attempt_key);_utc(self.reserved_at);_money(self.maximum_amount)
        if (type(self.policy) is not RunCostPolicy or not self.policy.matches(self.scope)
            or type(self.sequence) is not int or self.sequence<1
            or not self.policy.price_card.current(self.reserved_at)
            or self.maximum_amount.as_tuple().exponent < -9
            or self.maximum_amount>Decimal(self.policy.limit_minor_units)/Decimal(100)):
            raise ValueError('RUN_COST_RESERVATION_INVALID')


def bind_cost_receipt_to_attempt(receipt,scope,attempt_key):
    if type(receipt) is not CostReceipt or type(scope) is not RunCostScope:
        raise ValueError('RUN_COST_RECEIPT_BINDING_INVALID')
    receipt.__post_init__();_id(attempt_key)
    if receipt.provider_connection_id!=scope.connection_id:
        raise ValueError('RUN_COST_RECEIPT_BINDING_INVALID')
    # 바탕 receipt ID가 같은 실측 usage라도 실제 Run/Attempt마다 비용을 보존한다.
    binding=['run-attempt-cost-receipt-v1',scope.tenant_id,scope.workspace_id,scope.actor_id,
        scope.run_kind,scope.run_id,attempt_key,receipt.receipt_id]
    identity='cost-'+hashlib.sha256(json.dumps(binding,separators=(',',':')).encode()).hexdigest()
    return replace(receipt,receipt_id=identity)


def settle_reserved_usage(reservation,*,usage,outcome,settled_at):
    if type(reservation) is not CostReservation:
        raise ValueError('RUN_COST_RESERVATION_INVALID')
    if _utc(settled_at)<reservation.reserved_at:
        raise ValueError('RUN_COST_SETTLEMENT_TIME_INVALID')
    # 현재 단가/정산 시각으로 과거 실제 사용량을 재가격화하지 않는다.
    charge=price_actual_usage(reservation.policy.price_card,usage=usage,now=reservation.reserved_at)
    if charge is not None and charge.amount>reservation.maximum_amount:
        charge=None
        outcome='failed'
    receipt=make_price_card_cost_receipt(charge,price_version=reservation.policy.price_card.version,
        provider_connection_id=reservation.scope.connection_id,outcome=outcome,observed_at=settled_at)
    return bind_cost_receipt_to_attempt(receipt,reservation.scope,reservation.attempt_key)


def cost_policy_document(policy):
    if type(policy) is not RunCostPolicy:
        raise ValueError('RUN_COST_POLICY_INVALID')
    policy.__post_init__()
    result={name:getattr(policy,name) for name in (
        'policy_id','version','tenant_id','workspace_id','actor_id','connection_id',
        'model_id','run_kind','currency','limit_minor_units')}
    card=policy.price_card
    result['schema_version']=1
    result['price_card']={name:getattr(card,name) for name in ('version','base_url','model_id','currency')}
    for name in ('input_per_million','cached_input_per_million','output_per_million'):
        result['price_card'][name]=format(getattr(card,name),'f')
    for name in ('valid_from','valid_until'):
        result['price_card'][name]=getattr(card,name).isoformat()
    return result


def parse_cost_policy_document(document):
    keys={'schema_version','policy_id','version','tenant_id','workspace_id','actor_id',
        'connection_id','model_id','run_kind','currency','limit_minor_units','price_card'}
    card_keys={'version','base_url','model_id','currency','input_per_million',
        'cached_input_per_million','output_per_million','valid_from','valid_until'}
    try:
        if (type(document) is not dict or set(document)!=keys
            or type(document['schema_version']) is not int or document['schema_version']!=1
            or type(document['price_card']) is not dict or set(document['price_card'])!=card_keys):
            raise ValueError('RUN_COST_POLICY_INVALID')
        card_values=dict(document['price_card'])
        import re
        for name in ('input_per_million','cached_input_per_million','output_per_million'):
            raw=card_values[name]
            if type(raw) is not str or re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,9})?',raw) is None:
                raise ValueError('RUN_COST_POLICY_INVALID')
            card_values[name]=Decimal(raw)
        for name in ('valid_from','valid_until'):
            if type(card_values[name]) is not str:raise ValueError('RUN_COST_POLICY_INVALID')
            card_values[name]=_utc(datetime.fromisoformat(card_values[name]))
        values={k:v for k,v in document.items() if k not in {'schema_version','price_card'}}
        return RunCostPolicy(**values,price_card=PriceCard(**card_values))
    except (ValueError,TypeError,KeyError,ArithmeticError) as exc:
        raise ValueError('RUN_COST_POLICY_INVALID') from exc


class OperationsRunCostRepository:
    """기존 연결의 인가 문맥과 DB 원자 보호 함수를 사용한다. 자체 자격/풀은 없다."""
    def __init__(self,store):
        self._store=store

    @staticmethod
    def _context(context,scope):
        if (type(context) is not CloudAccessContext or type(scope) is not RunCostScope
            or (context.tenant_id,context.workspace_id,context.actor_id)
                !=(scope.tenant_id,scope.workspace_id,scope.actor_id)):
            raise ValueError('RUN_COST_SCOPE_INVALID')
        return CloudAccessContext(scope.tenant_id,scope.workspace_id,scope.actor_id,'run.cost')

    def reserve(self,context,scope,policy,*,attempt_key,input_token_upper_bound,output_token_limit,now):
        access=self._context(context,scope)
        decision=decide_run_cost(policy=policy,scope=scope,input_token_upper_bound=input_token_upper_bound,
            output_token_limit=output_token_limit,events=(),attempt_key=attempt_key,now=now)
        if decision.state!='allowed':return decision
        args=(scope.tenant_id,scope.workspace_id,scope.actor_id,scope.run_kind,scope.run_id,
            attempt_key,json.dumps(cost_policy_document(policy),separators=(',',':')),
            input_token_upper_bound,output_token_limit)
        with self._store._transaction(access) as connection:
            row=connection.execute('SELECT * FROM public.reserve_operations_run_cost('
                '%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)',args).fetchone()
        if type(row) not in (tuple,list) or len(row)!=7:
            raise ValueError('RUN_COST_DB_RESULT_INVALID')
        state,code,sequence,maximum,currency,version,observed=row
        if state=='reserved':
            if (code!='COST_RESERVED' or currency!=policy.currency or version!=policy.price_card.version
                or maximum!=decision.estimated_maximum):raise ValueError('RUN_COST_DB_RESULT_INVALID')
            return CostReservation(scope,policy,attempt_key,sequence,maximum,observed)
        if (state not in {'unknown','blocked'} or code not in {
            'COST_POLICY_UNCONFIRMED','COST_EVIDENCE_UNCONFIRMED','COST_SETTLEMENT_UNCONFIRMED',
            'COST_ATTEMPT_ALREADY_RECORDED','COST_LIMIT_EXCEEDED'}
            or sequence is not None or maximum is not None or currency is not None
            or version is not None or observed is not None):raise ValueError('RUN_COST_DB_RESULT_INVALID')
        return CostDecision(state,code,None,None,None,None,None)

    def settle(self,context,reservation,*,usage,outcome,settled_at):
        if type(reservation) is not CostReservation:
            raise ValueError('RUN_COST_RESERVATION_INVALID')
        access=self._context(context,reservation.scope)
        receipt=settle_reserved_usage(reservation,usage=usage,outcome=outcome,settled_at=settled_at)
        scope=reservation.scope
        args=(scope.tenant_id,scope.workspace_id,scope.actor_id,scope.run_kind,scope.run_id,
            reservation.attempt_key,reservation.sequence,receipt.state,receipt.amount,receipt.currency,
            receipt.price_version,receipt.usage_digest,receipt.receipt_id,receipt.observed_at)
        with self._store._transaction(access) as connection:
            row=connection.execute('SELECT public.settle_operations_run_cost('
                +','.join(['%s']*14)+')',args).fetchone()
        if (type(row) not in (tuple,list) or len(row)!=1 or type(row[0]) is not int
            or row[0]<=reservation.sequence):raise ValueError('RUN_COST_DB_RESULT_INVALID')
        return receipt
