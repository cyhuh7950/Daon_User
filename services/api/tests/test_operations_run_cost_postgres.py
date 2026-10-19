"""C2 단위 경계. 실제 PostgreSQL 동시성/권한 검증은 별도 결과로 기록한다."""
from datetime import timedelta
from decimal import Decimal
from dataclasses import replace
from contextlib import contextmanager

from test_operations_run_cost import NOW,card,policy,scope
from daon_user_api.operations_run_cost import price_actual_usage
from daon_user_api.operations_telemetry import make_price_card_cost_receipt
from daon_user_api.operations_run_cost_postgres import (
    CostReservation, bind_cost_receipt_to_attempt, settle_reserved_usage,
)

def reservation():
    return CostReservation(scope(),policy(),'attempt-first',1,Decimal('0.000914400'),NOW)

def test_same_usage_across_runs_or_attempts_is_not_global_dedup_but_replay_is_stable():
    charge=price_actual_usage(card(),usage={'prompt_tokens':1000,'completion_tokens':100,'cached_input_tokens':200},now=NOW)
    original=make_price_card_cost_receipt(charge,price_version=card().version,
        provider_connection_id='provider-upstage',outcome='succeeded',observed_at=NOW)
    first=bind_cost_receipt_to_attempt(original,scope(),'attempt-first')
    again=bind_cost_receipt_to_attempt(original,scope(),'attempt-first')
    other_attempt=bind_cost_receipt_to_attempt(original,scope(),'attempt-second')
    other_run=bind_cost_receipt_to_attempt(original,replace(scope(),run_id='run-second'),'attempt-first')
    assert first.receipt_id==again.receipt_id
    assert len({first.receipt_id,other_attempt.receipt_id,other_run.receipt_id})==3
    assert all(x.amount==Decimal('0.000372000') for x in (first,other_attempt,other_run))

def test_settlement_uses_reserved_price_and_time_even_after_card_expiry():
    held=reservation()
    later=NOW+timedelta(days=2)
    receipt=settle_reserved_usage(held,usage={'prompt_tokens':1000,'completion_tokens':100,'cached_input_tokens':200},
        outcome='succeeded',settled_at=later)
    assert (receipt.state,receipt.amount,receipt.price_version)==('known',Decimal('0.000372000'),card().version)
    assert receipt.observed_at==later

def test_failed_or_missing_usage_settlement_never_becomes_free_or_releases_reservation():
    held=reservation()
    for outcome,state in (('failed','uncertain'),('cancelled','uncertain'),('succeeded','unknown')):
        receipt=settle_reserved_usage(held,usage={},outcome=outcome,settled_at=NOW+timedelta(seconds=5))
        assert (receipt.state,receipt.amount,receipt.currency)==(state,None,None)


def test_actual_usage_above_reserved_maximum_is_uncertain_not_a_known_charge():
    receipt=settle_reserved_usage(reservation(),usage={'prompt_tokens':1000000,'completion_tokens':512,'cached_input_tokens':0},
        outcome='succeeded',settled_at=NOW+timedelta(seconds=5))
    assert (receipt.state,receipt.amount,receipt.currency)==('uncertain',None,None)

def test_reservation_requires_verified_bound_and_uses_one_atomic_db_operation():
    from daon_user_api import operations_run_cost_postgres as storage
    from daon_user_api.cloud_storage import CloudAccessContext
    calls=[]
    class Connection:
        def execute(self,query,args):
            calls.append((query,args));return self
        def fetchone(self):
            return ('reserved','COST_RESERVED',1,Decimal('0.000914400'),'USD',card().version,NOW)
    class Store:
        @contextmanager
        def _transaction(self,context):
            assert context==CloudAccessContext('tenant-qa','workspace-qa','usr-qa','run.cost')
            yield Connection()
    repository=storage.OperationsRunCostRepository(Store())
    context=CloudAccessContext('tenant-qa','workspace-qa','usr-qa','view')
    denied=repository.reserve(context,scope(),policy(),attempt_key='attempt-first',input_token_upper_bound=None,output_token_limit=512,now=NOW)
    assert denied.state=='unknown' and calls==[]
    held=repository.reserve(context,scope(),policy(),attempt_key='attempt-first',input_token_upper_bound=1000,output_token_limit=512,now=NOW)
    assert held==reservation()
    assert len(calls)==1 and calls[0][0].startswith('SELECT * FROM public.reserve_operations_run_cost(')
    assert calls[0][1][:6]==('tenant-qa','workspace-qa','usr-qa','question','run-qa','attempt-first')
    assert calls[0][1][-2:]==(1000,512)

def test_policy_document_roundtrip_is_closed_and_has_explicit_units():
    import pytest
    from daon_user_api import operations_run_cost_postgres as storage
    document=storage.cost_policy_document(policy())
    assert document['currency']=='USD' and document['limit_minor_units']==1000
    assert document['price_card']['input_per_million']=='0.30'
    assert storage.parse_cost_policy_document(document)==policy()
    with pytest.raises(ValueError):storage.parse_cost_policy_document({**document,'legacy_cost_limit_cents':1000})
    with pytest.raises(ValueError):storage.parse_cost_policy_document({**document,'limit_minor_units':True})


def test_settlement_is_one_fixed_db_call_with_exact_reservation_and_measured_receipt():
    from daon_user_api import operations_run_cost_postgres as storage
    from daon_user_api.cloud_storage import CloudAccessContext
    calls=[]
    class Connection:
        def execute(self,query,args):calls.append((query,args));return self
        def fetchone(self):return (2,)
    class Store:
        @contextmanager
        def _transaction(self,context):
            assert context.capability=='run.cost'
            yield Connection()
    held=reservation()
    receipt=storage.OperationsRunCostRepository(Store()).settle(
        CloudAccessContext('tenant-qa','workspace-qa','usr-qa','view'),held,
        usage={'prompt_tokens':1000,'completion_tokens':100,'cached_input_tokens':200},
        outcome='succeeded',settled_at=NOW+timedelta(days=2))
    assert receipt.state=='known' and receipt.amount==Decimal('0.000372000')
    assert len(calls)==1 and calls[0][0].startswith('SELECT public.settle_operations_run_cost(')
    assert calls[0][1][:7]==('tenant-qa','workspace-qa','usr-qa','question','run-qa','attempt-first',1)
    assert calls[0][1][7:]==(receipt.state,receipt.amount,receipt.currency,
        receipt.price_version,receipt.usage_digest,receipt.receipt_id,receipt.observed_at)


def test_repository_rejects_wrong_actor_and_invalid_db_dto_without_calling_provider():
    import pytest
    from daon_user_api import operations_run_cost_postgres as storage
    from daon_user_api.cloud_storage import CloudAccessContext
    class Store:
        @contextmanager
        def _transaction(self,context):
            class Connection:
                def execute(self,*args):return self
                def fetchone(self):return ('reserved','COST_RESERVED',1,Decimal('0'),'USD',card().version,NOW)
            yield Connection()
    repo=storage.OperationsRunCostRepository(Store())
    with pytest.raises(ValueError,match='RUN_COST_SCOPE_INVALID'):
        repo.reserve(CloudAccessContext('tenant-qa','workspace-qa','other-user','view'),scope(),policy(),
            attempt_key='attempt-first',input_token_upper_bound=1000,output_token_limit=512,now=NOW)
    with pytest.raises(ValueError,match='RUN_COST_DB_RESULT_INVALID'):
        repo.reserve(CloudAccessContext('tenant-qa','workspace-qa','usr-qa','view'),scope(),policy(),
            attempt_key='attempt-first',input_token_upper_bound=1000,output_token_limit=512,now=NOW)


def test_price_card_receipt_uses_separate_protected_writer_without_changing_reported_cost_path():
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.operations_telemetry_postgres import record_cost_in_transaction
    calls=[]
    class Connection:
        def execute(self,query,args):calls.append((query,args));self.args=args;return self
        def fetchone(self):return (self.args[-1],)
    receipt=settle_reserved_usage(reservation(),usage={'prompt_tokens':1000,'completion_tokens':100,'cached_input_tokens':200},
        outcome='succeeded',settled_at=NOW+timedelta(seconds=1))
    record_cost_in_transaction(Connection(),CloudAccessContext('tenant-qa','workspace-qa','usr-qa','question.execute'),
        'run-qa',receipt,model_attempt_id='attempt-first')
    assert calls[0][0].startswith('SELECT public.record_operations_price_card_receipt(')
    assert calls[0][1][4:8]==(receipt.receipt_id,'provider-upstage','attempt-first','price_card')


def test_transport_session_reserves_real_wire_before_send_and_settles_explicit_usage_once():
    from daon_user_api import operations_run_cost_postgres as storage
    from daon_user_api.operations_run_cost import UpstageModelContextEvidence
    from daon_user_api.cloud_storage import CloudAccessContext
    calls=[]
    class Repository:
        def reserve(self,context,current,p,**kwargs):
            assert current==scope() and p==policy()
            assert kwargs==dict(attempt_key='attempt-first',input_token_upper_bound=524288,output_token_limit=512,now=NOW)
            calls.append('reserve')
            return CostReservation(current,p,'attempt-first',1,Decimal('0.157900800'),NOW)
        def settle(self,context,held,**kwargs):
            calls.append('settle')
            return settle_reserved_usage(held,**kwargs)
    response={'model':'solar-pro4-260806','usage':{'prompt_tokens':1000,'completion_tokens':100,'total_tokens':1100,'prompt_tokens_details':{'cached_tokens':200}},'choices':[]}
    class Transport:
        def post_json(self,**kwargs):calls.append('post');return response
    evidence=UpstageModelContextEvidence('solar-pro4','solar-pro4-260806',524288,'https://console.upstage.ai/docs/models/solar-pro-4',NOW)
    session=storage.RunCostTransportSession(repository=Repository(),context=CloudAccessContext('tenant-qa','workspace-qa','usr-qa','view'),
        scope=scope(),policy=policy(),evidence=evidence,attempt_key='attempt-first',transport=Transport(),clock=lambda:NOW)
    payload={'model':'solar-pro4','messages':[{'role':'user','content':'비민감 합성 질문'}],'max_tokens':512}
    assert session.post_json(url='https://api.upstage.ai/v1/chat/completions',api_key='test-only-placeholder',payload=payload,timeout_seconds=60) is response
    assert calls==['reserve','post']
    receipt=session.finish(outcome='succeeded')
    assert receipt.amount==Decimal('0.000372000') and receipt.basis=='price_card'
    assert session.finish(outcome='succeeded')==receipt and calls==['reserve','post','settle']
    import pytest
    with pytest.raises(ValueError):session.post_json(url='https://api.upstage.ai/v1/chat/completions',api_key='test-only-placeholder',payload=payload,timeout_seconds=60)
    assert calls==['reserve','post','settle']


def test_transport_session_invalid_bound_never_sends_and_transport_failure_is_uncertain():
    import pytest
    from daon_user_api import operations_run_cost_postgres as storage
    from daon_user_api.operations_run_cost import CostDecision,UpstageModelContextEvidence
    from daon_user_api.cloud_storage import CloudAccessContext
    calls=[]
    class Repository:
        def reserve(self,context,current,p,**kwargs):
            if kwargs['input_token_upper_bound'] is None:return CostDecision('unknown','COST_EVIDENCE_UNCONFIRMED',None,None,None,None,None)
            calls.append('reserve');return CostReservation(current,p,'attempt-first',1,Decimal('0.157900800'),NOW)
        def settle(self,context,held,**kwargs):
            calls.append(('settle',kwargs['outcome']));return settle_reserved_usage(held,**kwargs)
    class Transport:
        def post_json(self,**kwargs):calls.append('post');raise TimeoutError('CONTROLLED_TRANSPORT_FAILURE')
    evidence=UpstageModelContextEvidence('solar-pro4','solar-pro4-260806',524288,'https://console.upstage.ai/docs/models/solar-pro-4',NOW)
    def new(e):return storage.RunCostTransportSession(repository=Repository(),context=CloudAccessContext('tenant-qa','workspace-qa','usr-qa','view'),
        scope=scope(),policy=policy(),evidence=e,attempt_key='attempt-first',transport=Transport(),clock=lambda:NOW)
    payload={'model':'solar-pro4','messages':[{'role':'user','content':'합성 질문'}],'max_tokens':512}
    with pytest.raises(ValueError):new(None).post_json(url='https://api.upstage.ai/v1/chat/completions',api_key='test-only-placeholder',payload=payload,timeout_seconds=60)
    assert calls==[]
    session=new(evidence)
    with pytest.raises(ValueError):session.post_json(url='https://api.upstage.ai/v1/chat/completions',api_key='test-only-placeholder',payload=payload,timeout_seconds=60)
    assert calls==['reserve','post',('settle','failed')]
    assert session.finish(outcome='failed').state=='uncertain'


def test_session_factory_matches_exact_configured_actor_model_and_rejects_duplicate_policy():
    from types import SimpleNamespace
    import pytest
    from daon_user_api import operations_run_cost_postgres as storage
    p=policy()
    factory=storage.RunCostSessionFactory(object(),policies=(p,),evidence=None,clock=lambda:NOW)
    context=SimpleNamespace(tenant_id=p.tenant_id,workspace_id=p.workspace_id,actor_id=p.actor_id)
    selection=SimpleNamespace(connection_id=p.connection_id,model_id=p.model_id,base_url=p.price_card.base_url,
        provider_code='UPSTAGE',provider_kind='external_api',capability='text_generation')
    assert type(factory.for_call(context,selection,run_kind='question',run_id='run-qa',attempt_key='attempt-first',transport=object())) is storage.RunCostTransportSession
    for changes in ({'actor_id':'other-user'},{'workspace_id':'other-workspace'}):
        changed=SimpleNamespace(**{**vars(context),**changes})
        assert factory.for_call(changed,selection,run_kind='question',run_id='run-qa',attempt_key='attempt-first',transport=object()) is None
    with pytest.raises(ValueError):storage.RunCostSessionFactory(object(),policies=(p,p),evidence=None,clock=lambda:NOW)
    for invalid in (replace(p,currency='KRW'),replace(p,price_card=replace(p.price_card,input_per_million=Decimal('0'),cached_input_per_million=Decimal('0'),output_per_million=Decimal('0')))):
        with pytest.raises(ValueError):storage.RunCostSessionFactory(object(),policies=(invalid,),evidence=None,clock=lambda:NOW)


def test_settlement_database_timeout_never_reposts_or_retries_uncertain_commit():
    """정산 전 finished 잠금을 제거하거나 실패 뒤 재호출하면 실패해야 한다."""
    import pytest
    from daon_user_api import operations_run_cost_postgres as storage
    from daon_user_api.operations_run_cost import UpstageModelContextEvidence
    from daon_user_api.cloud_storage import CloudAccessContext,CloudDatabaseError
    calls=[]
    class Repository:
        def reserve(self,context,current,p,**kwargs):
            calls.append('reserve')
            return CostReservation(current,p,'attempt-first',1,Decimal('0.157900800'),NOW)
        def settle(self,context,held,**kwargs):
            calls.append('settle')
            raise CloudDatabaseError('DATABASE_TIMEOUT',retryable=True)
    class Transport:
        def post_json(self,**kwargs):
            calls.append('post')
            return {'model':'solar-pro4-260806','usage':{'prompt_tokens':1000,'completion_tokens':100,
                'total_tokens':1100,'prompt_tokens_details':{'cached_tokens':200}},'choices':[]}
    evidence=UpstageModelContextEvidence('solar-pro4','solar-pro4-260806',524288,
        'https://console.upstage.ai/docs/models/solar-pro-4',NOW)
    session=storage.RunCostTransportSession(repository=Repository(),context=CloudAccessContext(
        'tenant-qa','workspace-qa','usr-qa','view'),scope=scope(),policy=policy(),evidence=evidence,
        attempt_key='attempt-first',transport=Transport(),clock=lambda:NOW)
    request=dict(url='https://api.upstage.ai/v1/chat/completions',api_key='test-only-placeholder',
        payload={'model':'solar-pro4','messages':[{'role':'user','content':'합성 질문'}],'max_tokens':512},timeout_seconds=60)
    session.post_json(**request)
    with pytest.raises(CloudDatabaseError) as failure:session.finish(outcome='succeeded')
    assert failure.value.code=='DATABASE_TIMEOUT'
    for outcome in ('succeeded','failed','cancelled'):
        with pytest.raises(ValueError,match='RUN_COST_SETTLEMENT_UNCONFIRMED'):session.finish(outcome=outcome)
    with pytest.raises(ValueError,match='RUN_COST_ATTEMPT_INVALID'):session.post_json(**request)
    assert calls==['reserve','post','settle']
