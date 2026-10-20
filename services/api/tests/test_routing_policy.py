from __future__ import annotations
import importlib.util
import pytest


def api():
    assert importlib.util.find_spec('daon_user_api.routing_policy') is not None, 'B1 routing policy service is not implemented'
    from daon_user_api import routing_policy
    return routing_policy


def test_policy_contract_rejects_untrusted_candidate_scope_and_extra():
    m = api()
    body = policy_body()
    assert m.RoutingPolicyBody.model_validate(body).expected_version == 0
    for candidate in ({**body['candidates'][0], 'tenant_id': 'other'},
                      {**body['candidates'][0], 'owner_id': 'victim'},
                      {**body['candidates'][0], 'credential_source': 'other'}):
        with pytest.raises(ValueError):
            m.RoutingPolicyBody.model_validate({**body, 'candidates': [candidate]})


def policy_body():
    return dict(schema_version=1, expected_version=0, enabled=True, capability='text_generation',
        candidates=[dict(connection_id='connection-a', model_id='model-a', credential_source='workspace',
            connection_version=1, catalog_version=1, credential_version=0, endpoint_version=1)],
        ranking_profile=dict(version='rank-v1', evaluation_set='qa-v1', minimum_quality=7000,
            observation_window='window-v1', input_class='short', minimum_samples=10, max_age_seconds=60,
            tier_mapping_version='tier-v1', locality_mapping_version='locality-v1'),
        fallback_policy=dict(mode='transient_only'), budget=dict(currency='USD', run_limit='1.00', max_attempts=1))


def test_policy_limits_preconditions_and_deduplication():
    m = api(); body = policy_body()
    for count in (0, 9):
        with pytest.raises(ValueError): m.RoutingPolicyBody.model_validate({**body, 'candidates': body['candidates'] * count})
    with pytest.raises(ValueError): m.RoutingPolicyBody.model_validate({**body, 'candidates': body['candidates'] * 2})
    with pytest.raises(m.RoutingPolicyError) as caught:
        m.validate_precondition(m.RoutingPolicyBody.model_validate(body), None, None)
    assert caught.value.status == 428
    with pytest.raises(m.RoutingPolicyError):
        m.validate_precondition(m.RoutingPolicyBody.model_validate(body), 'etag', '*')

def plan_fixture():
    from datetime import datetime,timedelta,timezone
    from dataclasses import replace
    from daon_user_api.workspace_model_defaults import WorkspaceModelDefaultsContext
    m=api(); now=datetime(2026,10,21,12,tzinfo=timezone.utc)
    ctx=WorkspaceModelDefaultsContext('tenant-a','workspace-a','actor-a','trace-a','policy-a')
    body=policy_body(); candidate=m.VerifiedCandidate('connection-a','model-a','deployment-a',1,1,0,1,'workspace',None,upper_budget_reference='d'*64)
    observation=m.VerifiedObservation('deployment-a','probe-a','measurement-v1',now-timedelta(seconds=10),
        now+timedelta(seconds=30),'qa-v1','window-v1','short',100,'tier-v1','locality-v1',
        0,9000,0,9900,120,'0.02','USD','price-v1',1000,'sha256:'+'1'*64,'license-v1',
        True,True,True,True,True,True,m.digest(candidate.references()),'a'*64,'basis_points_ms_currency_v1')
    request=m.RouteRequestSnapshot('notebook-a','a'*64,'b'*64,'c'*64,'cloud_sync')
    policy=dict(body=body,version=1,policy_version_id='policy-v1',digest_sha256=m.digest(body))
    return m,ctx,request,policy,candidate,observation,now


def test_route_plan_rejects_tampered_policy_snapshot():
    m,ctx,req,policy,c,o,now=plan_fixture()
    policy['digest_sha256']='f'*64
    with pytest.raises(m.RoutingPolicyError):
        m.build_route_plan(ctx,req,policy,(c,),(o,),now=now)


def test_frozen_plan_rejects_digest_and_json_tampering():
    m=api()
    with pytest.raises(m.RoutingPolicyError): m.FrozenRoutePlan('{"schema_version":1}','f'*64)


def test_service_prepares_detached_frozen_plan_and_does_not_dispatch():
    from types import SimpleNamespace
    m,ctx,req,policy,c,o,now=plan_fixture(); calls=[]
    repository=SimpleNamespace(get=lambda context: policy)
    metadata=SimpleNamespace(candidates=lambda context,body: (c,),
        observations=lambda context,request,candidates: (o,))
    service=m.RoutingPolicyService(repository,metadata,clock=lambda:now)
    plan=service.prepare(ctx,req,policy_version_id='policy-v1')
    assert plan.digest_sha256 == m.digest(plan.as_dict())
    view=plan.as_dict(); assert view['execution_enabled'] is False
    assert view['candidates'][0]['candidate']['deployment_id']=='deployment-a'
    view['candidates'].clear(); assert len(plan.as_dict()['candidates'])==1
    with pytest.raises(m.RoutingPolicyError) as e:
        service.prepare(ctx,req,policy_version_id='other')
    assert e.value.code=='VERSION_CONFLICT'


@pytest.mark.parametrize('field,value,reason',[
    ('source_id','','PROVENANCE_UNCONFIRMED'),('sample_count',0,'OBSERVATION_INCOMPARABLE'),
    ('evaluation_set','unapproved','OBSERVATION_INCOMPARABLE'),('quality_basis_points',6999,'QUALITY_BELOW_MINIMUM'),
    ('health_ready',False,'CURRENT_CANDIDATE_BLOCKED'),('capacity_ready',False,'CURRENT_CANDIDATE_BLOCKED'),
    ('circuit_closed',False,'CURRENT_CANDIDATE_BLOCKED'),('authorized',False,'CURRENT_CANDIDATE_BLOCKED'),
    ('residency_allowed',False,'CURRENT_CANDIDATE_BLOCKED'),('egress_allowed',False,'CURRENT_CANDIDATE_BLOCKED'),
    ('maximum_estimate','NaN','PRICE_UNCONFIRMED'),('maximum_estimate','-1','PRICE_UNCONFIRMED'),
    ('maximum_estimate','2','BUDGET_EXCEEDED'),('currency','KRW','PRICE_UNCONFIRMED'),
    ('load_basis_points',10001,'OBSERVATION_UNIT_INVALID'),('load_basis_points',False,'OBSERVATION_UNIT_INVALID')])
def test_route_plan_records_safe_exclusion_not_zero_fill(field,value,reason):
    from dataclasses import replace
    m,ctx,req,policy,c,o,now=plan_fixture(); o=replace(o,**{field:value})
    plan=m.build_route_plan(ctx,req,policy,(c,),(o,),now=now).as_dict()
    assert plan['candidates']==[] and plan['excluded'][0]['reason']==reason
    assert plan['safe_code']=='ROUTING_OBSERVATIONS_UNAVAILABLE'


def test_missing_and_expired_observations_exclude_and_ruleset_requires_evidence():
    from dataclasses import replace
    from datetime import timedelta
    m,ctx,req,policy,c,o,now=plan_fixture()
    for observations,reason in [((), 'OBSERVATION_MISSING'),((replace(o,expires_at=now),),'OBSERVATION_STALE'),
        ((replace(o,observed_at=now-timedelta(seconds=61)),),'OBSERVATION_STALE')]:
        p=m.build_route_plan(ctx,req,policy,(c,),observations,now=now).as_dict()
        assert p['excluded'][0]['reason']==reason and p['candidates']==[]
    with pytest.raises(m.RoutingPolicyError) as e:
        m.build_route_plan(ctx,replace(req,ruleset_required=True),policy,(c,),(o,),now=now)
    assert e.value.code=='RULESET_SNAPSHOT_REQUIRED'


@pytest.mark.parametrize('winning,losing',[
    ({'privacy_tier':0,'quality_basis_points':7000},{'privacy_tier':1,'quality_basis_points':10000}),
    ({'quality_basis_points':9001,'locality_rank':2},{'quality_basis_points':9000,'locality_rank':0}),
    ({'locality_rank':0,'reliability_basis_points':7000},{'locality_rank':1,'reliability_basis_points':10000}),
    ({'reliability_basis_points':9999,'latency_p95_ms':500},{'reliability_basis_points':9998,'latency_p95_ms':1}),
    ({'latency_p95_ms':100,'maximum_estimate':'0.9'},{'latency_p95_ms':101,'maximum_estimate':'0.01'}),
    ({'maximum_estimate':'0.01','load_basis_points':9999},{'maximum_estimate':'0.02','load_basis_points':1}),
    ({'load_basis_points':1},{'load_basis_points':2}), ({}, {})])
def test_lexicographic_ranking_preserves_all_eight_criteria(winning,losing):
    from dataclasses import replace
    m,ctx,req,policy,c,o,now=plan_fixture()
    c2=replace(c,model_id='model-b',deployment_id='deployment-b')
    o1=replace(o,**winning); o2=replace(o,deployment_id='deployment-b',
        candidate_reference_digest=m.digest(c2.references()),**losing)
    policy['body']['candidates'].append({**policy['body']['candidates'][0],'model_id':'model-b'})
    policy['digest_sha256']=m.digest(policy['body'])
    # Reverse input order to prove stable ID, not insertion order, breaks the final tie.
    policy['body']['candidates'].reverse();policy['digest_sha256']=m.digest(policy['body'])
    plan=m.build_route_plan(ctx,req,policy,(c2,c),(o2,o1),now=now).as_dict()
    assert [x['candidate']['deployment_id'] for x in plan['candidates']]==['deployment-a','deployment-b']

@pytest.mark.parametrize('field,value',[
    ('candidate_reference_digest','f'*64),('input_payload_digest','f'*64),('units_version','unverified-units')])
def test_observation_is_bound_to_exact_candidate_versions_and_input(field,value):
    from dataclasses import replace
    m,ctx,req,policy,c,o,now=plan_fixture()
    plan=m.build_route_plan(ctx,req,policy,(c,),(replace(o,**{field:value}),),now=now).as_dict()
    assert plan['candidates']==[]
    assert plan['excluded'][0]['reason']=='OBSERVATION_BINDING_INVALID'


def test_original_input_digest_is_never_candidate_egress_authorization():
    m,ctx,req,policy,c,o,now=plan_fixture()
    entry=m.build_route_plan(ctx,req,policy,(c,),(o,),now=now).as_dict()['candidates'][0]
    assert entry['input_payload_digest']==req.payload_digest
    assert entry['transformed_payload_digest'] is None and entry['authorization_state']=='not_prepared'
