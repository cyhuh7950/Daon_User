"""B4-A 내부 예산 규칙. 발급·저장·추가 인증·DB 경합 보장은 제공하지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import re

_LIMIT=65536
_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
_AMOUNT=re.compile(r'(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?')

class RoutingSourceError(ValueError):
    def __init__(self,code='BUDGET_INPUT_INVALID',status=422):
        self.code=code;self.status=status
        super().__init__(code)

def _check(condition,code='BUDGET_INPUT_INVALID',status=422):
    if not condition:raise RoutingSourceError(code,status)

def _id(value):
    _check(type(value) is str and _ID.fullmatch(value) is not None)

def _utc(value):
    _check(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)

@dataclass(frozen=True,slots=True)
class CurrencyRegistry:
    """서버 호출자가 명시한 검증 단위. 승인·환율·제품 기본 통화를 만들지 않는다."""
    entries:tuple[tuple[str,int],...]
    def __post_init__(self):
        _check(type(self.entries) is tuple and 1<=len(self.entries))
        seen=set()
        for item in self.entries:
            _check(type(item) is tuple and len(item)==2)
            code,precision=item
            _check(type(code) is str and re.fullmatch('[A-Z]{3}',code) is not None and code not in seen)
            _check(type(precision) is int and 0<=precision<=6)
            seen.add(code)
    def precision(self,currency):
        self.__post_init__()
        for code,precision in self.entries:
            if code==currency:return precision
        raise RoutingSourceError()

def _money(value,precision):
    _check(type(value) is str and len(value)<=_LIMIT and _AMOUNT.fullmatch(value) is not None)
    amount=Decimal(value)
    with localcontext() as context:
        context.prec=32
        _check(amount%Decimal(1).scaleb(-precision)==0)
    return amount

def _decimal(value,precision):
    _check(type(value) is Decimal and value.is_finite() and value>=0)
    _money(format(value,'f'),precision)

@dataclass(frozen=True,slots=True)
class BudgetAllocation:
    workspace_id:str
    period_limit:Decimal
    run_limit:Decimal
    def __post_init__(self):
        _id(self.workspace_id)
        _decimal(self.period_limit,6);_decimal(self.run_limit,6)
        _check(self.run_limit<=self.period_limit)
    def validate(self,precision):
        self.__post_init__()
        _id(self.workspace_id);_decimal(self.period_limit,precision);_decimal(self.run_limit,precision)
        _check(self.run_limit<=self.period_limit)

@dataclass(frozen=True,slots=True)
class BudgetInput:
    currency:str
    minor_unit:int
    period_limit:Decimal
    starts_at:datetime
    expires_at:datetime
    allocations:tuple[BudgetAllocation,...]
    def __post_init__(self):
        _check(type(self.currency) is str and re.fullmatch('[A-Z]{3}',self.currency) is not None)
        _check(type(self.minor_unit) is int and 0<=self.minor_unit<=6)
        _decimal(self.period_limit,self.minor_unit)
        _check(_utc(self.starts_at)==self.starts_at and self.starts_at.utcoffset().total_seconds()==0 and _utc(self.expires_at)==self.expires_at and self.expires_at.utcoffset().total_seconds()==0 and self.starts_at<self.expires_at)
        _check(type(self.allocations) is tuple and 1<=len(self.allocations)<=128)
        seen=set()
        for item in self.allocations:
            _check(type(item) is BudgetAllocation);item.validate(self.minor_unit)
            _check(item.workspace_id not in seen);seen.add(item.workspace_id)
        with localcontext() as context:
            context.prec=32
            _check(sum((x.period_limit for x in self.allocations),Decimal(0))<=self.period_limit)
    @property
    def unallocated_amount(self):
        self.__post_init__()
        with localcontext() as context:
            context.prec=32
            return self.period_limit-sum((x.period_limit for x in self.allocations),Decimal(0))
    def as_dict(self):
        self.__post_init__()
        return dict(schema_version=1,currency=self.currency,period_limit=format(self.period_limit,'f'),
            starts_at=_utc(self.starts_at).isoformat(),expires_at=_utc(self.expires_at).isoformat(),
            allocations=[dict(workspace_id=x.workspace_id,period_limit=format(x.period_limit,'f'),run_limit=format(x.run_limit,'f')) for x in self.allocations])

def parse_budget_input(raw,*,registry):
    """raw JSON은 원 UTF-8 바이트, dict는 직렬화 UTF-8 64KiB 제한. HTTP 제한이 아니다."""
    try:
        _check(type(registry) is CurrencyRegistry);registry.__post_init__()
        def pairs(items):
            value={}
            for key,item in items:
                _check(key not in value);value[key]=item
            return value
        def reject(_):raise RoutingSourceError()
        if type(raw) in {str,bytes}:
            encoded=raw.encode('utf-8') if type(raw) is str else raw
            _check(len(encoded)<=_LIMIT)
            value=json.loads(encoded.decode('utf-8',errors='strict'),object_pairs_hook=pairs,parse_constant=reject)
        else:
            _check(type(raw) is dict)
            _check(len(json.dumps(raw,ensure_ascii=False,allow_nan=False).encode('utf-8'))<=_LIMIT)
            value=raw
        _check(type(value) is dict and set(value)=={'schema_version','currency','period_limit','starts_at','expires_at','allocations'})
        _check(type(value['schema_version']) is int and value['schema_version']==1)
        currency=value['currency'];precision=registry.precision(currency)
        _check(type(value['allocations']) is list and 1<=len(value['allocations'])<=128)
        allocations=[]
        for item in value['allocations']:
            _check(type(item) is dict and set(item)=={'workspace_id','period_limit','run_limit'})
            allocations.append(BudgetAllocation(item['workspace_id'],_money(item['period_limit'],precision),_money(item['run_limit'],precision)))
        dates=[]
        for key in ('starts_at','expires_at'):
            _check(type(value[key]) is str);dates.append(_utc(datetime.fromisoformat(value[key])))
        return BudgetInput(currency,precision,_money(value['period_limit'],precision),*dates,tuple(allocations))
    except RoutingSourceError:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,ArithmeticError,RecursionError):raise RoutingSourceError() from None

@dataclass(frozen=True,slots=True)
class BudgetSource:
    """검증된 이력 값. 저장 진실성/발급 권한을 이 객체만으로 증명하지 않는다."""
    tenant_id:str
    organization_id:str
    period_id:str
    source_id:str
    version:int
    issued_by:str
    issued_at:datetime
    budget:BudgetInput
    revoked:bool
    def __post_init__(self):
        for value in (self.tenant_id,self.organization_id,self.period_id,self.source_id,self.issued_by):_id(value)
        _check(self.tenant_id==self.organization_id and type(self.version) is int and self.version==1)
        _check(type(self.revoked) is bool and type(self.budget) is BudgetInput)
        _utc(self.issued_at);self.budget.__post_init__()
    @property
    def identity_digest(self):
        self.__post_init__()
        value=dict(tenant_id=self.tenant_id,organization_id=self.organization_id,period_id=self.period_id,source_id=self.source_id,version=self.version,issued_by=self.issued_by,issued_at=_utc(self.issued_at).isoformat(),budget=self.budget.as_dict())
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def validate_budget_history(proposed,*,tenant_id,organization_id,history,registry):
    """철회 포함 이력의 중첩을 검사한다. DB 경쟁·저장 완전성은 보장하지 않는다."""
    _id(tenant_id);_id(organization_id);_check(tenant_id==organization_id)
    _check(type(proposed) is BudgetInput and type(history) is tuple)
    _check(parse_budget_input(proposed.as_dict(),registry=registry)==proposed)
    seen_periods=set();seen_sources=set();validated=[]
    for item in history:
        _check(type(item) is BudgetSource);item.__post_init__()
        _check((item.tenant_id,item.organization_id)==(tenant_id,organization_id))
        _check(item.period_id not in seen_periods and item.source_id not in seen_sources)
        seen_periods.add(item.period_id);seen_sources.add(item.source_id)
        _check(parse_budget_input(item.budget.as_dict(),registry=registry)==item.budget)
        for prior in validated:
            _check(not (prior.budget.starts_at<item.budget.expires_at and item.budget.starts_at<prior.budget.expires_at))
        validated.append(item)
    for item in validated:
        _check(not (item.budget.starts_at<proposed.expires_at and proposed.starts_at<item.budget.expires_at),'BUDGET_PERIOD_OVERLAP',409)

@dataclass(frozen=True,slots=True)
class OrganizationBudgetManager:
    tenant_id:str
    actor_id:str
    workspace_id:str

def require_organization_budget_manager(authorization,*,principal,organization_id,trace_id,policy_version):
    """현재 기존 권한을 재사용한다. 발급·재인증 또는 두 조회의 원자성은 제공하지 않는다."""
    from .authorization import Action,Role,RoleScope
    _id(organization_id)
    _check(organization_id==principal.tenant_id,'BUDGET_ORGANIZATION_DENIED',403)
    workspace_id=authorization.organization_admin_workspace(principal=principal,trace_id=trace_id,policy_version=policy_version)
    grant=authorization.authorize_action(principal=principal,workspace_id=workspace_id,action=Action.POLICY_MANAGE,trace_id=trace_id,policy_version=policy_version)
    _check(grant.allowed is True and grant.role is Role.ORGANIZATION_ADMIN and grant.role_scope is RoleScope.TENANT and (grant.tenant_id,grant.actor_id,grant.workspace_id)==(principal.tenant_id,principal.user_id,workspace_id),'BUDGET_ORGANIZATION_DENIED',403)
    return OrganizationBudgetManager(grant.tenant_id,grant.actor_id,grant.workspace_id)
