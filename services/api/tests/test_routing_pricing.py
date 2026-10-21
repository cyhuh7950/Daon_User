"""미신뢰 가격 입력·순수 계산 시험. 실제 가격/과금/adapter 증거가 아니다."""
from copy import deepcopy
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from decimal import Decimal,localcontext,ROUND_DOWN,Inexact,Rounded
import json
import pytest
from daon_user_api.routing_sources import CurrencyRegistry
from daon_user_api.routing_pricing import (RoutingPricingError,PriceInput,PriceEvidence,UnitPrices,parse_price_input,calculate_text_upper_bound)

NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)
REGISTRY=CurrencyRegistry((('USD',2),('JPY',0),('XTS',6)))
def body():
 return dict(schema_version=1,expected_version=0,workspace_id='workspace-qa',connection_id='connection-qa',model_id='organization/model-v1',connection_version=1,catalog_version=2,endpoint_version=1,credential_source='workspace',currency='USD',valid_from=NOW.isoformat(),valid_until=(NOW+timedelta(hours=24)).isoformat(),billing_basis='text_tokens_v1',unit_prices=dict(maximum_input_per_million='0.000001',maximum_output_per_million='2.123456',mandatory_fee_per_request='0.000001'),evidence=dict(source_kind='official_price_document',reference='https://prices.example.com/model/v1',document_version='doc-v1',document_digest='a'*64,reviewed_at=NOW.isoformat()))
def parse(value=None,**kw):return parse_price_input(body() if value is None else value,REGISTRY,now=kw.get('now',NOW))
def calculate(value=None,**kw):return calculate_text_upper_bound(parse() if value is None else value,registry=kw.get('registry',REGISTRY),input_upper=kw.get('input_upper',1),billed_output_upper=kw.get('billed_output_upper',1),now=kw.get('now',NOW))

def test_valid_closed_input_normalizes_utc_and_preserves_rate_precision():
 value=body();value['valid_from']='2026-10-21T21:00:00+09:00';p=parse(value)
 assert p.valid_from==NOW and p.valid_from.utcoffset()==timedelta(0)
 assert p.unit_prices.maximum_input_per_million==Decimal('0.000001') and p.minor_unit==2
 assert calculate(p)==Decimal('0.01')

@pytest.mark.parametrize('field,value',[(key,value) for key,value in [('schema_version',True),('schema_version',2),('expected_version',True),('expected_version',-1),('connection_version',False),('catalog_version',0),('endpoint_version','1'),('credential_source','personal'),('workspace_id','bad space'),('connection_id',''),('model_id','bad\nmodel'),('currency','usd'),('currency','EUR'),('billing_basis','free'),('valid_until','2026-10-22T12:00:01Z'),('valid_from','2026-10-21T12:00:00'),('valid_until','2026-10-21T12:00:00Z')]])
def test_invalid_top_fields_rejected(field,value):
 data=body();data[field]=value
 with pytest.raises(RoutingPricingError):parse(data)

@pytest.mark.parametrize('value',[None,True,0,1.2,'NaN','Infinity','-0','-1','1e2','0.0000001','1000000000000','01','1.'])
def test_invalid_decimal_input_is_not_coerced(value):
 data=body();data['unit_prices']['maximum_input_per_million']=value
 with pytest.raises(RoutingPricingError):parse(data)

@pytest.mark.parametrize('key',['billing_reference','credential_version','issuer','price_id','state','attested','verified','proof','tokenizer','input_upper'])
def test_server_or_approval_fields_never_accepted_from_body(key):
 data=body();data[key]='not-authority'
 with pytest.raises(RoutingPricingError):parse(data)

@pytest.mark.parametrize('field,value',[('source_kind','unknown'),('document_digest','A'*64),('document_version','bad version'),('reviewed_at','2026-10-21T12:00:01Z'),('reference','http://prices.example.com'),('reference','https://user:secret@prices.example.com'),('reference','https://prices.example.com/?key=secret'),('reference','https://prices.example.com/#secret'),('reference','https://prices.example.com/\nsecret'),('reference','https://prices.example.com:'+str(999999)),('reference','https://')])
def test_bad_evidence_has_only_safe_error(field,value):
 data=body();data['evidence'][field]=value
 with pytest.raises(RoutingPricingError) as error:parse(data)
 assert str(error.value)=='PRICE_INPUT_INVALID' and 'secret' not in str(error.value)

@pytest.mark.parametrize('encoding',['utf-16','utf-32'])
def test_raw_requires_utf8(encoding):
 with pytest.raises(RoutingPricingError):parse(json.dumps(body()).encode(encoding))

def test_raw_duplicate_nested_extra_utf8_byte_limit_and_invalid_json():
 for raw in ('{"schema_version":1,"schema_version":1}',json.dumps(body()).replace('"unit_prices": {','"unit_prices": {"unknown":"secret",'),b'\xff',b' '*65537,json.dumps(body()).replace('"expected_version": 0','"expected_version": NaN')):
  with pytest.raises(RoutingPricingError):parse(raw)
 data=body();data['evidence']['reference']='문'*22000
 with pytest.raises(RoutingPricingError):parse(data)
 assert parse(json.dumps(body()).encode('utf8'))==parse()

def test_nested_missing_or_extra_and_registry_now_are_not_defaulted():
 for nested in ('unit_prices','evidence'):
  data=body();data[nested]['extra']='secret'
  with pytest.raises(RoutingPricingError):parse(data)
 data=body();del data['unit_prices']['mandatory_fee_per_request']
 with pytest.raises(RoutingPricingError):parse(data)
 with pytest.raises(RoutingPricingError):parse_price_input(body(),None,now=NOW)
 with pytest.raises(RoutingPricingError):parse_price_input(body(),REGISTRY,now=NOW.replace(tzinfo=None))
 with pytest.raises(TypeError):parse_price_input(body(),REGISTRY)

def test_zero_syntax_is_explicit_not_an_execution_approval():
 data=body();data['billing_basis']='verified_zero_v1';data['evidence']['source_kind']='internal_zero_policy';data['unit_prices']={k:'0' for k in data['unit_prices']}
 p=parse(data)
 assert p.billing_basis=='verified_zero_v1' and not hasattr(p,'attested') and not hasattr(p,'quote')
 with pytest.raises(RoutingPricingError):calculate(p)
 for changed in ('basis','rate','evidence'):
  value=deepcopy(data)
  if changed=='basis':del value['billing_basis']
  elif changed=='rate':value['unit_prices']['mandatory_fee_per_request']='0.000001'
  else:value['evidence']['source_kind']='account_contract'
  with pytest.raises(RoutingPricingError):parse(value)

@pytest.mark.parametrize('currency,expected',[('USD','0.01'),('JPY','1'),('XTS','0.000003')])
def test_sum_three_terms_then_round_up_once(currency,expected):
 data=body();data['currency']=currency;data['unit_prices']={k:'0.000001' for k in data['unit_prices']}
 assert calculate(parse(data),input_upper=1000000,billed_output_upper=1000000)==Decimal(expected)

@pytest.mark.parametrize('value',[True,-1,1.0,Decimal(1),None,2147483648])
def test_token_counts_are_explicit_bounded_integers(value):
 with pytest.raises(RoutingPricingError):calculate(input_upper=value)
 with pytest.raises(RoutingPricingError):calculate(billed_output_upper=value)

def test_no_global_decimal_context_effect_or_operand_rounding():
 p=parse()
 expected=calculate(p,input_upper=2147483647,billed_output_upper=2147483647)
 with localcontext() as ctx:
  ctx.prec=2;ctx.rounding=ROUND_DOWN;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
  before=ctx.copy()
  assert calculate(p,input_upper=2147483647,billed_output_upper=2147483647)==expected
  assert ctx.prec==before.prec and ctx.rounding==before.rounding and ctx.flags==before.flags

def test_period_and_review_currentness_at_calculation_and_registry_revalidation():
 p=parse()
 with pytest.raises(RoutingPricingError):calculate(p,now=NOW-timedelta(microseconds=1))
 with pytest.raises(RoutingPricingError):calculate(p,now=NOW+timedelta(hours=24))
 with pytest.raises(RoutingPricingError):calculate(p,registry=CurrencyRegistry((('USD',6),)))

def test_deep_immutability_replace_and_forged_instance_revalidated():
 data=body();p=parse(data);data['unit_prices']['mandatory_fee_per_request']='999'
 assert p.unit_prices.mandatory_fee_per_request==Decimal('0.000001')
 with pytest.raises(FrozenInstanceError):p.currency='JPY'
 with pytest.raises(RoutingPricingError):replace(p,unit_prices={'untrusted':'dict'})
 with pytest.raises(RoutingPricingError):replace(p.unit_prices,maximum_input_per_million=Decimal('-1'))
 forged=object.__new__(PriceInput)
 for field in p.__dataclass_fields__:object.__setattr__(forged,field,getattr(p,field))
 object.__setattr__(forged,'billing_basis','free')
 with pytest.raises(RoutingPricingError):calculate(forged)

def test_calculation_overflow_and_discount_input_rejected():
 data=body();data['unit_prices']={k:'999999999999.999999' for k in data['unit_prices']}
 with pytest.raises(RoutingPricingError):calculate(parse(data),input_upper=2147483647,billed_output_upper=2147483647)
 data=body();data['unit_prices']['cached_input_per_million']='0'
 with pytest.raises(RoutingPricingError):parse(data)

def test_module_has_no_io_or_execution_producer():
 import ast
 from pathlib import Path
 import daon_user_api.routing_pricing as module
 tree=ast.parse(Path(module.__file__).read_text(encoding='utf8'))
 forbidden={'open','connect','urlopen','post_json','quote','issue_price','TokenBoundProof','datetime.now'}
 assert not any(isinstance(n,ast.Name) and n.id in forbidden for n in ast.walk(tree))
 assert not hasattr(module,'quote') and not hasattr(module,'issue_price') and not hasattr(module,'TokenBoundProof')



def test_exact_utf8_limit_nested_duplicate_and_input_dictionary_not_retained():
 raw=json.dumps(body(),ensure_ascii=False).encode('utf8')
 assert parse(raw+b' '*(65536-len(raw)))==parse()
 with pytest.raises(RoutingPricingError):parse(raw+b' '*(65537-len(raw)))
 duplicate=json.dumps(body()).replace('"maximum_input_per_million":','"maximum_input_per_million":"0", "maximum_input_per_million":')
 with pytest.raises(RoutingPricingError):parse(duplicate)
 value=body();p=parse(value);value['evidence']['document_digest']='b'*64
 assert p.evidence.document_digest=='a'*64


def test_existing_model_id_and_positive_version_boundaries():
 value=body();value['model_id']='a/'+'b'*253
 assert len(parse(value).model_id)==255
 value['model_id']+='c'
 with pytest.raises(RoutingPricingError):parse(value)
 for key in ('connection_version','catalog_version','endpoint_version'):
  for invalid in (True,False,0,1.0,'1'):
   value=body();value[key]=invalid
   with pytest.raises(RoutingPricingError):parse(value)


def test_direct_constructor_nested_forgery_and_replace_are_not_authority():
 p=parse()
 for changes in (dict(schema_version=True),dict(expected_version=False),dict(connection_version=True),dict(model_id='bad model'),dict(minor_unit=7),dict(credential_source='personal')):
  with pytest.raises(RoutingPricingError):replace(p,**changes)
 forged_prices=object.__new__(UnitPrices)
 for field in p.unit_prices.__dataclass_fields__:object.__setattr__(forged_prices,field,getattr(p.unit_prices,field))
 object.__setattr__(forged_prices,'maximum_input_per_million',Decimal('NaN'))
 forged=object.__new__(PriceInput)
 for field in p.__dataclass_fields__:object.__setattr__(forged,field,getattr(p,field))
 object.__setattr__(forged,'unit_prices',forged_prices)
 with pytest.raises(RoutingPricingError):calculate(forged)
 future_evidence=replace(p.evidence,reviewed_at=NOW+timedelta(seconds=1))
 with pytest.raises(RoutingPricingError):calculate(replace(p,evidence=future_evidence))
 malformed_registry=object.__new__(CurrencyRegistry);object.__setattr__(malformed_registry,'entries',[('USD',2)])
 with pytest.raises(RoutingPricingError):calculate(p,registry=malformed_registry)


def test_full_decimal_parent_context_is_untouched():
 p=parse();expected=calculate(p,input_upper=2147483647,billed_output_upper=2147483647)
 with localcontext() as ctx:
  ctx.prec=1;ctx.rounding=ROUND_DOWN;ctx.Emax=2;ctx.Emin=-2;ctx.clamp=1
  ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
  before=(ctx.prec,ctx.rounding,ctx.Emax,ctx.Emin,ctx.clamp,dict(ctx.traps),dict(ctx.flags))
  assert calculate(p,input_upper=2147483647,billed_output_upper=2147483647)==expected
  assert (ctx.prec,ctx.rounding,ctx.Emax,ctx.Emin,ctx.clamp,dict(ctx.traps),dict(ctx.flags))==before


def test_money_maximum_and_zero_count_are_explicit():
 value=body();value['unit_prices']={key:'999999999999.999999' for key in value['unit_prices']};value['unit_prices']['mandatory_fee_per_request']='999999999999'
 assert calculate(parse(value),input_upper=0,billed_output_upper=0)==Decimal('999999999999.00')
 value['unit_prices']['mandatory_fee_per_request']='999999999999.999999'
 with pytest.raises(RoutingPricingError):calculate(parse(value),input_upper=0,billed_output_upper=0)


def test_reference_document_id_and_no_effect_structure():
 value=body();value['evidence']['reference']='price-document:version-2'
 assert parse(value).evidence.reference=='price-document:version-2'
 import ast
 from pathlib import Path
 import daon_user_api.routing_pricing as module
 tree=ast.parse(Path(module.__file__).read_text(encoding='utf8'))
 blocked={'open','connect','request','urlopen','post_json','quote','issue_price','now','utcnow'}
 for node in ast.walk(tree):
  if isinstance(node,ast.Call):
   called=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
   assert called not in blocked
