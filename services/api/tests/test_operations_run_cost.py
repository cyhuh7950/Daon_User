"""명시적 QA 비용 정책의 순수 판정. 실제 과금/Provider/DB 시험이 아니다."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from daon_user_api.operations_run_cost import (
    CostEvent, PriceCard, RunCostPolicy, RunCostScope,
    decide_run_cost, price_actual_usage, quote_maximum,
)

NOW=datetime(2026,10,20,tzinfo=timezone.utc)

def card():
    return PriceCard('upstage-solar-pro4-20261020','https://api.upstage.ai/v1','solar-pro4','USD',
        Decimal('0.30'),Decimal('0.06'),Decimal('1.20'),NOW,NOW+timedelta(hours=24))


def test_runtime_cost_configuration_is_closed_explicit_and_preserves_checked_timestamp():
    import json
    from daon_user_api import operations_run_cost as cost
    from daon_user_api.operations_run_cost_postgres import cost_policy_document
    raw=json.dumps([cost_policy_document(policy())])
    context={'model_alias':'solar-pro4','resolved_model':'solar-pro4-260806','context_tokens':524288,
        'source_url':'https://console.upstage.ai/docs/models/solar-pro-4','checked_at':NOW.isoformat()}
    policies,evidence=cost.parse_run_cost_settings(raw,json.dumps(context))
    assert policies==(policy(),) and evidence.checked_at==NOW
    assert cost.parse_run_cost_settings('[]','{}')==((),None)
    for broken,model in (('[{},{}]','{}'),('[true]','{}'),('[', '{}'),(raw,'{"context_tokens":1,"context_tokens":2}'),
        (raw,json.dumps({**context,'legacy_cost_limit_cents':1000})),(raw,json.dumps({**context,'context_tokens':True}))):
        with pytest.raises(ValueError):cost.parse_run_cost_settings(broken,model)

def scope():
    return RunCostScope('tenant-qa','workspace-qa','usr-qa','provider-upstage','solar-pro4','question','run-qa','https://api.upstage.ai/v1')

def policy(limit=1000):
    return RunCostPolicy('qa-upstage-usd-run',1,'tenant-qa','workspace-qa','usr-qa',
        'provider-upstage','solar-pro4','question','USD',limit,card())

def decide(p=None,**changes):
    args=dict(policy=policy() if p is None else p,scope=scope(),input_token_upper_bound=1000,
        output_token_limit=512,events=(),attempt_key='attempt-next',now=NOW)
    args.update(changes)
    return decide_run_cost(**args)

def test_explicit_scoped_policy_never_reinterprets_legacy_cents():
    assert decide().state=='allowed'
    assert decide().currency=='USD'
    assert decide_run_cost(policy=None,scope=scope(),input_token_upper_bound=1000,
        output_token_limit=512,events=(),attempt_key='attempt-next',now=NOW).state=='unknown'
    for field,value in (('tenant_id','tenant-other'),('workspace_id','workspace-other'),
        ('actor_id','usr-other'),('connection_id','other-provider'),('model_id','other-model'),('run_kind','document'),('base_url','https://another.invalid/v1')):
        assert decide(scope=replace(scope(),**{field:value})).state=='unknown'

def test_quote_uses_decimal_ceil_and_includes_output_bound():
    assert quote_maximum(card(),input_token_upper_bound=1000,output_token_limit=512,now=NOW)==Decimal('0.000914400')
    assert quote_maximum(card(),input_token_upper_bound=1,output_token_limit=1,now=NOW)==Decimal('0.000001500')
    assert decide().estimated_maximum==Decimal('0.000914400')

def test_missing_bound_expired_price_wrong_model_or_currency_is_unknown():
    assert decide(input_token_upper_bound=None).state=='unknown'
    assert decide(now=NOW+timedelta(hours=24)).state=='unknown'
    assert decide(now=NOW-timedelta(microseconds=1)).state=='unknown'
    assert decide(replace(policy(),currency='KRW')).state=='unknown'
    assert decide(replace(policy(),price_card=replace(card(),model_id='another-model'))).state=='unknown'

def test_cumulative_known_and_reserved_cannot_exceed_run_budget():
    events=(CostEvent(1,'attempt-first','reserved',Decimal('0.01'),'USD'),
        CostEvent(2,'attempt-first','known',Decimal('0.0099'),'USD'))
    result=decide(policy(limit=1),events=events)
    assert result.known_total==Decimal('0.0099')
    assert result.state=='blocked' and result.safe_code=='COST_LIMIT_EXCEEDED'
    pending=(CostEvent(1,'attempt-first','reserved',Decimal('0.005'),'USD'),)
    result=decide(policy(limit=1),events=pending)
    assert result.state=='unknown' and result.reserved_total==Decimal('0.005')

def test_uncertain_or_replayed_attempt_never_releases_budget():
    reserved=CostEvent(1,'attempt-first','reserved',Decimal('0.005'),'USD')
    uncertain=CostEvent(2,'attempt-first','uncertain',None,None)
    assert decide(events=(reserved,uncertain)).state=='unknown'
    assert decide(events=(reserved,),attempt_key='attempt-first').state=='blocked'
    known=CostEvent(2,'attempt-first','known',Decimal('0.004'),'USD')
    assert decide(events=(reserved,known),attempt_key='attempt-first').state=='blocked'
    assert decide(events=(reserved,known,replace(known,sequence=3))).state=='unknown'

def test_price_card_actual_usage_is_not_provider_reported_or_estimate():
    usage={'prompt_tokens':1000,'completion_tokens':100,'cached_input_tokens':200}
    result=price_actual_usage(card(),usage=usage,now=NOW)
    assert result.amount==Decimal('0.000372000')
    assert result.currency=='USD' and result.basis=='price_card'
    assert result.price_version=='upstage-solar-pro4-20261020'
    assert len(result.usage_digest)==64
    assert price_actual_usage(card(),usage={'prompt_tokens':1000,'completion_tokens':100},now=NOW) is None

@pytest.mark.parametrize('bad',[True,1.5,-1,'1000'])
def test_untrusted_input_token_bounds_are_never_accepted(bad):
    assert decide(input_token_upper_bound=bad).state=='unknown'

@pytest.mark.parametrize('bad',[True,Decimal('1.0'),-1])
def test_budget_requires_explicit_integer_minor_units(bad):
    with pytest.raises(ValueError):
        policy(limit=bad)

def test_wrong_event_currency_and_sequence_fail_closed():
    first=CostEvent(1,'attempt-first','reserved',Decimal('0.005'),'USD')
    assert decide(events=(first,replace(first,sequence=1))).state=='unknown'
    assert decide(events=(replace(first,currency='KRW'),)).state=='unknown'

def test_non_exact_output_bound_and_boolean_usage_never_become_known_cost():
    for bad in (True,0,513,None):
        assert decide(output_token_limit=bad).state=='unknown'
    assert price_actual_usage(card(),usage={'prompt_tokens':True,'completion_tokens':10,'cached_input_tokens':0},now=NOW) is None
    assert price_actual_usage(card(),usage={'prompt_tokens':10,'completion_tokens':10,'cached_input_tokens':11},now=NOW) is None

def test_cache_price_cannot_exceed_the_undiscounted_quote_rate():
    with pytest.raises(ValueError):
        replace(card(),cached_input_per_million=Decimal('0.31'))

def test_actual_charge_above_reserved_bound_blocks_followup_even_with_budget_left():
    events=(CostEvent(1,'attempt-first','reserved',Decimal('0.001'),'USD'),
        CostEvent(2,'attempt-first','known',Decimal('0.002'),'USD'))
    assert decide(events=events).state=='unknown'

def test_price_card_receipt_preserves_basis_version_and_uncertain_failures():
    from daon_user_api import operations_telemetry as telemetry
    charge=price_actual_usage(card(),usage={'prompt_tokens':1000,'completion_tokens':100,'cached_input_tokens':200},now=NOW)
    receipt=telemetry.make_price_card_cost_receipt(charge,price_version=card().version,
        provider_connection_id='provider-upstage',outcome='succeeded',observed_at=NOW)
    assert (receipt.basis,receipt.price_version,receipt.state,receipt.amount)==('price_card',card().version,'known',Decimal('0.000372000'))
    assert receipt.usage_digest==charge.usage_digest
    for outcome in ('failed','cancelled'):
        receipt=telemetry.make_price_card_cost_receipt(charge,price_version=card().version,
            provider_connection_id='provider-upstage',outcome=outcome,observed_at=NOW)
        assert (receipt.state,receipt.amount,receipt.currency)==('uncertain',None,None)
    missing=telemetry.make_price_card_cost_receipt(None,price_version=card().version,
        provider_connection_id='provider-upstage',outcome='succeeded',observed_at=NOW)
    assert (missing.state,missing.amount,missing.currency)==('unknown',None,None)

def test_price_card_receipt_rejects_missing_or_different_price_identity():
    from daon_user_api import operations_telemetry as telemetry
    charge=price_actual_usage(card(),usage={'prompt_tokens':1,'completion_tokens':1,'cached_input_tokens':0},now=NOW)
    for version in (None,'different-price'):
        with pytest.raises(ValueError):
            telemetry.make_price_card_cost_receipt(charge,price_version=version,
                provider_connection_id='provider-upstage',outcome='succeeded',observed_at=NOW)

def test_full_text_request_bound_uses_verified_model_context_not_bytes_as_tokens():
    from daon_user_api import operations_run_cost as cost
    evidence=cost.UpstageModelContextEvidence('solar-pro4','solar-pro4-260806',524288,
        'https://console.upstage.ai/docs/models/solar-pro-4',NOW)
    payload={'model':'solar-pro4','messages':[{'role':'system','content':'system instructions'},
        {'role':'user','content':'QA source'}],'response_format':{'type':'json_object'},'max_tokens':512}
    assert cost.verified_upstage_input_bound(scope(),payload,evidence,now=NOW)==524288
    assert cost.verified_upstage_input_bound(scope(),{**payload,'max_tokens':513},evidence,now=NOW) is None
    assert cost.verified_upstage_input_bound(scope(),{**payload,'tools':[]},evidence,now=NOW) is None
    assert cost.verified_upstage_input_bound(scope(),{**payload,'messages':[{'role':'user','content':[{'type':'image_url'}]}]},evidence,now=NOW) is None
    assert cost.verified_upstage_input_bound(scope(),payload,evidence,now=NOW+timedelta(hours=24)) is None
    assert cost.verified_upstage_input_bound(scope(),{**payload,'messages':[{'role':'user','content':'a'*65536}]},evidence,now=NOW) is None

def test_actual_upstage_usage_requires_exact_model_nested_cache_and_token_totals():
    from daon_user_api import operations_run_cost as cost
    evidence=cost.UpstageModelContextEvidence('solar-pro4','solar-pro4-260806',524288,
        'https://console.upstage.ai/docs/models/solar-pro-4',NOW)
    response={'model':'solar-pro4-260806','usage':{'prompt_tokens':1000,'completion_tokens':100,
        'total_tokens':1100,'prompt_tokens_details':{'cached_tokens':200}}}
    assert cost.normalize_upstage_usage(response,evidence)=={'prompt_tokens':1000,'completion_tokens':100,'cached_input_tokens':200}
    assert cost.normalize_upstage_usage({**response,'model':'different-version'},evidence) is None
    assert cost.normalize_upstage_usage({**response,'usage':{**response['usage'],'total_tokens':1101}},evidence) is None
    assert cost.normalize_upstage_usage({**response,'usage':{**response['usage'],'prompt_tokens_details':{}}},evidence) is None
    assert cost.normalize_upstage_usage({**response,'usage':{**response['usage'],'completion_tokens':True}},evidence) is None
