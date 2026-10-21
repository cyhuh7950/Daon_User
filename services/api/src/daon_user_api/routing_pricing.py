"""미신뢰 가격 내부 값·순수 계산. 가격 발급·검증 승인·quote/실행 기능이 아니다."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from decimal import Decimal,Context,localcontext,ROUND_CEILING
import json
import re
from urllib.parse import urlsplit
from .routing_sources import CurrencyRegistry,RoutingSourceError

_LIMIT=65536
_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
_MODEL=re.compile(r'[^\x00-\x20\x7f]{1,255}')
_AMOUNT=re.compile(r'(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?')
_PRICE_KEYS={'maximum_input_per_million','maximum_output_per_million','mandatory_fee_per_request'}
_EVIDENCE_KEYS={'source_kind','reference','document_version','document_digest','reviewed_at'}
_INPUT_KEYS={'schema_version','expected_version','workspace_id','connection_id','model_id','connection_version','catalog_version','endpoint_version','credential_source','currency','valid_from','valid_until','billing_basis','unit_prices','evidence'}

class RoutingPricingError(ValueError):
    def __init__(self):
        self.code='PRICE_INPUT_INVALID';self.status=422
        super().__init__(self.code)

def _check(condition):
    if not condition:raise RoutingPricingError()

def _id(value):_check(type(value) is str and _ID.fullmatch(value) is not None)
def _utc(value):
    _check(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)
def _normalized(value):
    _check(_utc(value)==value and value.utcoffset()==timedelta(0))
def _amount(raw):
    _check(type(raw) is str and _AMOUNT.fullmatch(raw) is not None)
    return Decimal(raw)
def _decimal(value):
    _check(type(value) is Decimal and value.is_finite() and not value.is_signed())
    _check(-6<=value.as_tuple().exponent<=11 and value.adjusted()<12)
    _amount(format(value,'f'))
def _precision(registry,currency):
    _check(type(registry) is CurrencyRegistry)
    registry.__post_init__()
    return registry.precision(currency)
def _reference(value):
    _check(type(value) is str and 1<=len(value)<=2048)
    if _ID.fullmatch(value):return
    _check(not re.search(r'[\x00-\x20\x7f]',value) and '?' not in value and '#' not in value and '\\' not in value)
    _check(not re.search(r'%(?:0[0-9a-f]|1[0-9a-f]|7f)',value,re.I))
    parsed=urlsplit(value)
    _check(parsed.scheme=='https' and bool(parsed.netloc) and bool(parsed.hostname) and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment)
    _check(parsed.port is None or 1<=parsed.port<=65535)
    host=parsed.hostname
    if ':' in host:
        import ipaddress
        ipaddress.IPv6Address(host)
    else:
        ascii_host=host.encode('idna').decode('ascii')
        _check(len(ascii_host)<=253 and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',part) for part in ascii_host.rstrip('.').split('.')))

def _date(raw):
    _check(type(raw) is str)
    return _utc(datetime.fromisoformat(raw))

@dataclass(frozen=True,slots=True)
class UnitPrices:
    maximum_input_per_million:Decimal
    maximum_output_per_million:Decimal
    mandatory_fee_per_request:Decimal
    def __post_init__(self):
        for value in (self.maximum_input_per_million,self.maximum_output_per_million,self.mandatory_fee_per_request):_decimal(value)

@dataclass(frozen=True,slots=True)
class PriceEvidence:
    source_kind:str
    reference:str
    document_version:str
    document_digest:str
    reviewed_at:datetime
    def __post_init__(self):
        _check(type(self.source_kind) is str and self.source_kind in {'official_price_document','account_contract','internal_zero_policy'})
        try:_reference(self.reference)
        except (ValueError,UnicodeError,TypeError,OverflowError):raise RoutingPricingError() from None
        _id(self.document_version)
        _check(type(self.document_digest) is str and re.fullmatch('[0-9a-f]{64}',self.document_digest) is not None)
        _normalized(self.reviewed_at)

@dataclass(frozen=True,slots=True)
class PriceInput:
    schema_version:int
    expected_version:int
    workspace_id:str
    connection_id:str
    model_id:str
    connection_version:int
    catalog_version:int
    endpoint_version:int
    credential_source:str
    currency:str
    minor_unit:int
    valid_from:datetime
    valid_until:datetime
    billing_basis:str
    unit_prices:UnitPrices
    evidence:PriceEvidence
    def __post_init__(self):
        _check(type(self.schema_version) is int and self.schema_version==1)
        _check(type(self.expected_version) is int and self.expected_version>=0)
        _id(self.workspace_id);_id(self.connection_id)
        _check(type(self.model_id) is str and _MODEL.fullmatch(self.model_id) is not None)
        for value in (self.connection_version,self.catalog_version,self.endpoint_version):_check(type(value) is int and value>=1)
        _check(type(self.credential_source) is str and self.credential_source=='workspace')
        _check(type(self.currency) is str and re.fullmatch('[A-Z]{3}',self.currency) is not None)
        _check(type(self.minor_unit) is int and 0<=self.minor_unit<=6)
        _normalized(self.valid_from);_normalized(self.valid_until)
        _check(timedelta(0)<self.valid_until-self.valid_from<=timedelta(hours=24))
        _check(type(self.billing_basis) is str and self.billing_basis in {'text_tokens_v1','verified_zero_v1'})
        _check(type(self.unit_prices) is UnitPrices);self.unit_prices.__post_init__()
        _check(type(self.evidence) is PriceEvidence);self.evidence.__post_init__()
        if self.billing_basis=='verified_zero_v1':
            _check(self.evidence.source_kind=='internal_zero_policy' and all(value==0 for value in (self.unit_prices.maximum_input_per_million,self.unit_prices.maximum_output_per_million,self.unit_prices.mandatory_fee_per_request)))
    def validate(self,registry,*,now):
        self.__post_init__()
        _check(_precision(registry,self.currency)==self.minor_unit)
        _check(self.evidence.reviewed_at<=_utc(now))

def parse_price_input(raw,registry,*,now):
    """원 UTF8 bytes/dict 직렬화64KiB. 형식 통과는 실제 대상·가격·증빙의 검증이 아니다."""
    try:
        now=_utc(now)
        def pairs(items):
            value={}
            for key,item in items:
                _check(key not in value);value[key]=item
            return value
        def reject(_):raise RoutingPricingError()
        if type(raw) in {str,bytes}:
            encoded=raw.encode('utf8') if type(raw) is str else raw
            _check(len(encoded)<=_LIMIT)
            data=json.loads(encoded.decode('utf8',errors='strict'),object_pairs_hook=pairs,parse_constant=reject)
        else:
            _check(type(raw) is dict)
            _check(len(json.dumps(raw,ensure_ascii=False,allow_nan=False).encode('utf8'))<=_LIMIT)
            data=raw
        _check(type(data) is dict and set(data)==_INPUT_KEYS)
        precision=_precision(registry,data['currency'])
        prices=data['unit_prices'];evidence=data['evidence']
        _check(type(prices) is dict and set(prices)==_PRICE_KEYS)
        _check(type(evidence) is dict and set(evidence)==_EVIDENCE_KEYS)
        value=PriceInput(**{key:data[key] for key in _INPUT_KEYS-{'unit_prices','evidence','valid_from','valid_until'}},minor_unit=precision,valid_from=_date(data['valid_from']),valid_until=_date(data['valid_until']),unit_prices=UnitPrices(**{key:_amount(prices[key]) for key in _PRICE_KEYS}),evidence=PriceEvidence(**{key:evidence[key] for key in _EVIDENCE_KEYS-{'reviewed_at'}},reviewed_at=_date(evidence['reviewed_at'])))
        value.validate(registry,now=now)
        return value
    except RoutingPricingError:raise
    except (RoutingSourceError,ValueError,TypeError,OverflowError,UnicodeError,ArithmeticError,RecursionError,AttributeError):raise RoutingPricingError() from None

def calculate_text_upper_bound(price_input,*,registry,input_upper,billed_output_upper,now):
    """명시적 내부 수치의 보수적 산술. 토큰 증명·가격 승인·quote 생성은 제공하지 않는다."""
    try:
        _check(type(price_input) is PriceInput);price_input.validate(registry,now=now)
        now=_utc(now)
        _check(price_input.billing_basis=='text_tokens_v1' and price_input.valid_from<=now<price_input.valid_until)
        for value in (input_upper,billed_output_upper):_check(type(value) is int and 0<=value<=2147483647)
        # 부모 context의 precision/rounding/traps/flags/Emin/Emax를 상속하지 않는다.
        with localcontext(Context(prec=50,rounding=ROUND_CEILING)):
            prices=price_input.unit_prices
            total=(Decimal(input_upper)*prices.maximum_input_per_million+Decimal(billed_output_upper)*prices.maximum_output_per_million)/Decimal(1000000)+prices.mandatory_fee_per_request
            quantum=Decimal(1).scaleb(-price_input.minor_unit)
            result=total.quantize(quantum,rounding=ROUND_CEILING)
            _decimal(result)
            return result
    except RoutingPricingError:raise
    except (RoutingSourceError,ValueError,TypeError,OverflowError,ArithmeticError,AttributeError):raise RoutingPricingError() from None


class RoutingPriceStorageError(ValueError):
    def __init__(self,code='PRICE_SOURCE_UNAVAILABLE',status=503):
        self.code=code;self.status=status;super().__init__(code)
@dataclass(frozen=True,slots=True)
class PricePreconditions:
    expected_version:int
    if_match:str|None=None
    if_none_match:str|None=None
    def __post_init__(self):
        if type(self.expected_version) is not int or self.expected_version<0:raise RoutingPriceStorageError('PRICE_INPUT_INVALID',422)
        for value in (self.if_match,self.if_none_match):
            if value is not None and (type(value) is not str or not 1<=len(value)<=256 or any(ord(ch)<32 or ord(ch)==127 for ch in value)):raise RoutingPriceStorageError('PRICE_INPUT_INVALID',422)
        if self.if_match is not None and self.if_none_match is not None:raise RoutingPriceStorageError('PRICE_INPUT_INVALID',422)
    def as_dict(self):
        self.__post_init__();return dict(expected_version=self.expected_version,if_match=self.if_match,if_none_match=self.if_none_match)

def price_input_dict(value):
    _check(type(value) is PriceInput);value.__post_init__()
    return dict(schema_version=value.schema_version,expected_version=value.expected_version,workspace_id=value.workspace_id,connection_id=value.connection_id,model_id=value.model_id,connection_version=value.connection_version,catalog_version=value.catalog_version,endpoint_version=value.endpoint_version,credential_source=value.credential_source,currency=value.currency,valid_from=value.valid_from.isoformat(),valid_until=value.valid_until.isoformat(),billing_basis=value.billing_basis,unit_prices={key:format(getattr(value.unit_prices,key),'f') for key in _PRICE_KEYS},evidence=dict(source_kind=value.evidence.source_kind,reference=value.evidence.reference,document_version=value.evidence.document_version,document_digest=value.evidence.document_digest,reviewed_at=value.evidence.reviewed_at.isoformat()))

@dataclass(frozen=True,slots=True)
class PriceSourceView:
    series_id:str
    price_version:int
    current_revision:int
    price_input:PriceInput
    content_digest:str
    verification_state:str
    revoked:bool
    expired:bool
    target_changed:bool
    @property
    def version_etag(self):return '"routing-price:'+self.series_id+':'+str(self.price_version)+':'+self.content_digest+':'+('revoked' if self.revoked else 'unverified')+'"'
    @property
    def current_etag(self):return '"routing-price-current:'+self.series_id+':'+str(self.current_revision)+'"'
    def __post_init__(self):
        _id(self.series_id)
        _check(type(self.price_version) is int and self.price_version>=1 and type(self.current_revision) is int and self.current_revision>=1)
        _check(type(self.price_input) is PriceInput);self.price_input.__post_init__()
        _check(type(self.content_digest) is str and re.fullmatch('[0-9a-f]{64}',self.content_digest) is not None and type(self.verification_state) is str and self.verification_state=='unverified')
        _check(all(type(value) is bool for value in (self.revoked,self.expired,self.target_changed)))

class PriceSourceService:
    """서버 내부 미검증 자료만 저장한다. HTTP/실행 quote 연결 없음."""
    def __init__(self,repository,reauthentication,*,registry,clock):
        _check(type(registry) is CurrencyRegistry);registry.__post_init__()
        self.repository=repository;self.reauthentication=reauthentication;self.registry=registry;self.clock=clock
    def _body(self,context,body,operation):
        if operation=='routing_price.register':
            value=parse_price_input(body,self.registry,now=self.clock())
            if value.workspace_id!=context.workspace_id:raise RoutingPriceStorageError('PRICE_ANCHOR_UNSUPPORTED',422)
            return price_input_dict(value)
        if operation!='routing_price.revoke' or type(body) is not dict or set(body)!={'series_id','price_version','reason_code'}:raise RoutingPriceStorageError('PRICE_INPUT_INVALID',422)
        _id(body['series_id'])
        if type(body['price_version']) is not int or body['price_version']<1 or type(body['reason_code']) is not str or body['reason_code'] not in {'ADMIN_REVOKED','POLICY_WITHDRAWN'}:raise RoutingPriceStorageError('PRICE_INPUT_INVALID',422)
        return dict(body)
    @staticmethod
    def _conditions(value):
        if type(value) is not PricePreconditions:raise RoutingPriceStorageError('PRICE_INPUT_INVALID',422)
        value.__post_init__()
    def binding(self,context,body,*,operation,preconditions,idempotency_key,access_token):
        self._conditions(preconditions)
        self.reauthentication.current(context,access_token=access_token)
        value=self._body(context,body,operation)
        return self.repository.prepare_binding(context,value,operation=operation,preconditions=preconditions,idempotency_key=idempotency_key,reauthentication=self.reauthentication,access_token=access_token)
    def _write(self,operation,*,context,body,preconditions,idempotency_key,access_token,proof):
        self._conditions(preconditions)
        self.reauthentication.current(context,access_token=access_token)
        value=self._body(context,body,operation)
        return self.repository.write(context,value,operation=operation,preconditions=preconditions,idempotency_key=idempotency_key,reauthentication=self.reauthentication,access_token=access_token,proof=proof)
    def register_price(self,**request):return self._write('routing_price.register',**request)
    def revoke_price(self,**request):return self._write('routing_price.revoke',**request)
    def get_price(self,context,series_id,price_version=None,*,access_token):
        self.reauthentication.current(context,access_token=access_token)
        return self.repository.get(context,series_id,price_version)
