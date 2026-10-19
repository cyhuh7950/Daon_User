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
