"""B4-A 내부 규칙만 검증한다. 발급/저장/API/실예산 시험이 아니다."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import pytest
from daon_user_api.routing_sources import (CurrencyRegistry, RoutingSourceError, parse_budget_input, BudgetAllocation,
    BudgetSource, validate_budget_history, require_organization_budget_manager)
from daon_user_api.authorization import AuthorizationService, AuthorizationError, SqliteAuthorizationRepository, Role, Action
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.audit import AuditEventStore

REGISTRY=CurrencyRegistry((('USD',2),('JPY',0),('TST',6)))
NOW=datetime(2026,10,21,tzinfo=timezone.utc)

def body():
 return dict(schema_version=1,currency='USD',period_limit='10.00',starts_at='2026-10-21T00:00:00Z',expires_at='2026-11-21T00:00:00Z',allocations=[dict(workspace_id='workspace-1',period_limit='7.00',run_limit='2.00')])

def parse(value=None,registry=REGISTRY):return parse_budget_input(body() if value is None else value,registry=registry)
def source(value=None,**extra):
 return BudgetSource(tenant_id='tenant-1',organization_id='tenant-1',period_id='period-1',source_id='source-1',version=1,issued_by='actor-1',issued_at=NOW,budget=parse(value),revoked=False,**extra)

def test_explicit_zero_is_valid_and_unallocated_balance_is_not_a_grant():
 value=body();value['period_limit']='0';value['allocations'][0].update(period_limit='0',run_limit='0')
 result=parse(value)
 assert result.period_limit==0 and result.allocations[0].run_limit==0
 assert parse().unallocated_amount==3
 with pytest.raises(FrozenInstanceError):result.currency='JPY'
 value['allocations'][0]['run_limit']='9'
 assert result.allocations[0].run_limit==0

@pytest.mark.parametrize('amount',[None,True,1,1.2,'','-1','NaN','Infinity','1e2',' 1','+1','1.0000001','0.001'])
def test_non_decimal_or_unapproved_currency_fraction_is_rejected(amount):
 value=body();value['period_limit']=amount
 with pytest.raises(RoutingSourceError) as error:parse(value)
 assert error.value.code=='BUDGET_INPUT_INVALID' and error.value.status==422

@pytest.mark.parametrize('change',[
 {'currency':'EUR'},{'currency':'usd'},{'schema_version':True},{'actor_id':'forged'},
 {'starts_at':'2026-10-21T00:00:00'}, {'expires_at':'2026-10-21T00:00:00Z'},
 {'starts_at':'not-a-date'}, {'allocations':[]}, {'period_limit':'6'},
 {'allocations':[{'workspace_id':'workspace-1','period_limit':'7','run_limit':'8'}]},
 {'allocations':[{'workspace_id':'workspace-1','period_limit':'7','run_limit':'1','currency':'USD'}]},
])
def test_closed_shape_period_and_allocation_limits_are_enforced(change):
 with pytest.raises(RoutingSourceError):parse({**body(),**change})

def test_required_zero_is_not_filled_from_missing_input():
 for field in body():
  value=body();del value[field]
  with pytest.raises(RoutingSourceError):parse(value)

def test_registry_is_explicit_immutable_and_precision_bounded():
 for entries in [(),(('USD',True),),(('USD',7),),(('usd',2),),(('USD',2),('USD',2))]:
  with pytest.raises(RoutingSourceError):CurrencyRegistry(entries)
 with pytest.raises(RoutingSourceError):parse(registry=None)
 for currency,amount in [('JPY','0.1'),('TST','0.0000001')]:
  value=body();value.update(currency=currency,period_limit=amount)
  with pytest.raises(RoutingSourceError):parse(value)
 value=body();value.update(currency='TST',period_limit='0.000001');value['allocations'][0].update(period_limit='0',run_limit='0')
 assert parse(value).period_limit.as_tuple().exponent==-6

def test_duplicate_and_129_allocations_and_oversize_or_duplicate_json_are_rejected():
 value=body();value['allocations']*=2
 with pytest.raises(RoutingSourceError):parse(value)
 value['allocations']=[dict(workspace_id='w-'+str(i),period_limit='0',run_limit='0') for i in range(129)]
 with pytest.raises(RoutingSourceError):parse(value)
 for raw in [' '*65537,'{"schema_version":1,"schema_version":1}',json.dumps({**body(),'extra':'x'*65536})]:
  with pytest.raises(RoutingSourceError):parse(raw)
 value['allocations']=value['allocations'][:128];assert len(parse(value).allocations)==128

def test_offset_dates_normalize_to_utc_and_half_open_boundary_does_not_overlap():
 value=body();value['starts_at']='2026-10-21T09:00:00+09:00'
 assert parse(value).starts_at==NOW
 previous=source();next_body=body();next_body.update(starts_at='2026-11-21T00:00:00Z',expires_at='2026-12-21T00:00:00Z')
 validate_budget_history(parse(next_body),tenant_id='tenant-1',organization_id='tenant-1',history=(previous,),registry=REGISTRY)

@pytest.mark.parametrize('revoked',[False,True])
def test_active_and_revoked_periods_both_prevent_overlapping_reissue(revoked):
 previous=replace(source(),revoked=revoked)
 with pytest.raises(RoutingSourceError) as error:validate_budget_history(parse(),tenant_id='tenant-1',organization_id='tenant-1',history=(previous,),registry=REGISTRY)
 assert error.value.code=='BUDGET_PERIOD_OVERLAP' and error.value.status==409

def test_history_wrong_scope_identity_and_duplicate_period_are_rejected():
 previous=source();next_body=body();next_body.update(starts_at='2026-11-21T00:00:00Z',expires_at='2026-12-21T00:00:00Z')
 for history in [(replace(previous,tenant_id='other',organization_id='other'),),(previous,previous),(SimpleNamespace(**vars(previous)) if hasattr(previous,'__dict__') else object(),)]:
  with pytest.raises(RoutingSourceError):validate_budget_history(parse(next_body),tenant_id='tenant-1',organization_id='tenant-1',history=history,registry=REGISTRY)
 for changes in [dict(version=True),dict(version=2),dict(organization_id='other'),dict(revoked='false'),dict(issued_at=NOW.replace(tzinfo=None)),dict(source_id='../bad')]:
  with pytest.raises(RoutingSourceError):replace(previous,**changes)

def authorization(tmp_path,kind='organization'):
 repository=SqliteAuthorizationRepository(tmp_path/'auth.sqlite3');principal=IdentityPrincipal('owner','session','device','tenant-1')
 repository.bootstrap_workspace(tenant_id=principal.tenant_id,workspace_id='workspace-1',owner_user_id=principal.user_id,owner_role=Role.ORGANIZATION_ADMIN if kind=='organization' else Role.PERSONAL_OWNER,workspace_kind=kind,data_area='cloud_sync',cost_limit_cents=0,now=NOW)
 return AuthorizationService(repository=repository,audit_store=AuditEventStore(),clock=lambda:NOW),repository,principal

def test_actual_organization_admin_is_accepted_but_current_role_revocation_denied(tmp_path):
 service,repository,principal=authorization(tmp_path)
 grant=require_organization_budget_manager(service,principal=principal,organization_id='tenant-1',trace_id='trace-1',policy_version='policy-1')
 assert (grant.tenant_id,grant.actor_id,grant.workspace_id)==('tenant-1','owner','workspace-1')
 with repository.transaction() as connection:connection.execute("UPDATE auth_tenant_roles SET state='inactive' WHERE user_id='owner'")
 with pytest.raises(AuthorizationError) as error:require_organization_budget_manager(service,principal=principal,organization_id='tenant-1',trace_id='trace-1',policy_version='policy-1')
 assert error.value.http_status==403 and error.value.code=='ACTION_DENIED'

@pytest.mark.parametrize('role',[Role.WORKSPACE_ADMIN,Role.VIEWER])
def test_workspace_policy_manager_and_system_admin_name_do_not_issue_org_budget(tmp_path,role):
 service,repository,principal=authorization(tmp_path);actor=IdentityPrincipal('system-admin','session','device','tenant-1')
 with repository.transaction() as connection:connection.execute("INSERT INTO auth_memberships VALUES(?,?,?,?,'active',1,?)",('tenant-1','workspace-1',actor.user_id,role.value,NOW.isoformat()))
 if role is Role.WORKSPACE_ADMIN:assert service.authorize_action(principal=actor,workspace_id='workspace-1',action=Action.POLICY_MANAGE,trace_id='trace-1',policy_version='policy-1')
 with pytest.raises(AuthorizationError) as error:require_organization_budget_manager(service,principal=actor,organization_id='tenant-1',trace_id='trace-1',policy_version='policy-1')
 assert error.value.http_status==403 and error.value.code=='ACTION_DENIED'

def test_personal_owner_existing_helper_passes_but_b4_guard_rejects(tmp_path):
 service,_,principal=authorization(tmp_path,'personal')
 assert service.organization_admin_workspace(principal=principal,trace_id='trace-1',policy_version='policy-1')=='workspace-1'
 with pytest.raises(RoutingSourceError) as error:require_organization_budget_manager(service,principal=principal,organization_id='tenant-1',trace_id='trace-1',policy_version='policy-1')
 assert error.value.status==403

def test_other_tenant_and_safe_error_never_expose_body(tmp_path):
 service,_,principal=authorization(tmp_path)
 with pytest.raises(RoutingSourceError):require_organization_budget_manager(service,principal=principal,organization_id='other',trace_id='trace-1',policy_version='policy-1')
 with pytest.raises(RoutingSourceError) as error:parse({**body(),'password':'private-synthetic-marker'})
 assert 'private-synthetic-marker' not in str(error.value) and 'password' not in str(error.value)


def test_direct_allocation_construction_rejects_invalid_internal_objects():
 from decimal import Decimal
 for values in [('bad/id',Decimal('1'),Decimal('0')),('w',Decimal('NaN'),Decimal('0')),('w',Decimal('1'),Decimal('2')),('w',True,Decimal('0'))]:
  with pytest.raises(RoutingSourceError):BudgetAllocation(*values)

def test_direct_internal_budget_cannot_bypass_64k_limit_or_utc_normalization():
 from decimal import Decimal
 from datetime import timedelta
 with pytest.raises(RoutingSourceError):replace(parse(),period_limit=Decimal('9'*65500))
 with pytest.raises(RoutingSourceError):replace(parse(),starts_at=NOW.astimezone(timezone(timedelta(hours=9))))

def test_revalidates_forged_history_and_registry_at_consuming_boundary():
 previous=source();object.__setattr__(previous,'version',True)
 with pytest.raises(RoutingSourceError):validate_budget_history(parse(),tenant_id='tenant-1',organization_id='tenant-1',history=(previous,),registry=REGISTRY)
 bad=object.__new__(CurrencyRegistry);object.__setattr__(bad,'entries',(('USD',True),))
 with pytest.raises(RoutingSourceError):parse(registry=bad)
 original=body();result=parse(original);original['allocations'].append(dict(workspace_id='w-2',period_limit='99',run_limit='99'))
 assert len(result.allocations)==1 and result.unallocated_amount==3

def test_raw_utf8_byte_limit_duplicate_nested_keys_and_numeric_booleans():
 for raw in [(' '*65536+'é').encode(),'{"schema_version":1,"currency":"USD","period_limit":"1","starts_at":"2026-10-21T00:00:00Z","expires_at":"2026-11-21T00:00:00Z","allocations":[{"workspace_id":"w","workspace_id":"x","period_limit":"0","run_limit":"0"}]}']:
  with pytest.raises(RoutingSourceError):parse(raw)
 assert parse(json.dumps(body()).encode()).period_limit==10

def test_same_period_identity_with_different_body_and_stale_currency_unit_is_rejected():
 previous=source();changed=body();changed['period_limit']='11'
 changed_source=replace(previous,budget=parse(changed))
 assert changed_source.identity_digest!=previous.identity_digest
 future=body();future.update(starts_at='2026-11-21T00:00:00Z',expires_at='2026-12-21T00:00:00Z')
 with pytest.raises(RoutingSourceError):validate_budget_history(parse(future),tenant_id='tenant-1',organization_id='tenant-1',history=(previous,changed_source),registry=REGISTRY)
 with pytest.raises(RoutingSourceError):validate_budget_history(parse(future),tenant_id='tenant-1',organization_id='tenant-1',history=(replace(previous,budget=parse({**body(),'period_limit':'10.01'})),),registry=CurrencyRegistry((('USD',0),)))


def test_existing_route_money_integer_bound_and_exact_sum_are_preserved():
 value=body();value['period_limit']='1000000000000'
 with pytest.raises(RoutingSourceError):parse(value)
 value.update(currency='TST',period_limit='999999999999.999999')
 value['allocations']=[dict(workspace_id='w-a',period_limit='999999999999.999998',run_limit='0'),dict(workspace_id='w-b',period_limit='0.000001',run_limit='0.000001')]
 assert parse(value).unallocated_amount==0
 value['allocations'][1]['period_limit']='0.000002'
 with pytest.raises(RoutingSourceError):parse(value)


@pytest.mark.parametrize('encoding',['utf-16','utf-32'])
def test_raw_bytes_must_be_utf8_and_never_auto_detect_other_encodings(encoding):
 with pytest.raises(RoutingSourceError) as error:parse(json.dumps(body()).encode(encoding))
 assert error.value.code=='BUDGET_INPUT_INVALID' and error.value.status==422


def test_a2_server_context_and_preconditions_are_closed_immutable_values():
 from daon_user_api.routing_sources import BudgetContext,BudgetPreconditions
 principal=IdentityPrincipal('owner','session','device','tenant-1')
 context=BudgetContext.from_principal(principal,workspace_id='workspace-1',trace_id='trace-1',policy_version='policy-1')
 assert context.organization_id==context.tenant_id=='tenant-1'
 for changes in [dict(actor_id=True),dict(session_id='bad/session'),dict(organization_id='other')]:
  with pytest.raises(RoutingSourceError):replace(context,**changes)
 for value in [True,-1,3]:
  with pytest.raises(RoutingSourceError):BudgetPreconditions(value)

@pytest.mark.parametrize('reason',[None,True,[],{}])
def test_a2_revoke_body_cannot_raise_type_error_or_accept_untyped_reason(reason):
 from daon_user_api.routing_sources import BudgetSourceService,BudgetContext
 context=BudgetContext('tenant-1','tenant-1','workspace-1','owner','session','device','trace-1','policy-1')
 service=BudgetSourceService(None,None,registry=REGISTRY,clock=lambda:NOW)
 with pytest.raises(RoutingSourceError):service._body(context,dict(period_id='period-1',reason_code=reason),'routing_budget.revoke')
