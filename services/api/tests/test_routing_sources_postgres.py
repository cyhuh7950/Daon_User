"""실제 repository SQL/거래 대역. PostgreSQL/RLS/경합 실행 증거가 아니다."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json,sqlite3
import pytest
from daon_user_api.identity import IdentityError
from daon_user_api.authorization import AuthorizationError
from test_routing_budget_reauthentication import qa,counts
from daon_user_api.routing_sources import BudgetPreconditions,RoutingSourceError,CurrencyRegistry,BudgetSourceService
from daon_user_api.routing_sources_postgres import PostgresBudgetSourceRepository

class Row(dict):
 def __getitem__(self,key):return tuple(self.values())[key] if type(key) is int else super().__getitem__(key)
class Cursor:
 def __init__(self,rows=()):self.rows=rows
 def fetchone(self):return self.rows[0] if self.rows else None
 def fetchall(self):return self.rows
class SqlStore:
 def __init__(self,q):self.q=q;self.periods={};self.allocations={};self.revocations={};self.replays={};self.audit=[];self.calls=[];self.fail=None;self.protected=True;self.catalog_fail=None;self.anchors={}
 @contextmanager
 def _transaction(self,context):
  before=deepcopy((self.periods,self.allocations,self.revocations,self.replays,self.audit,self.anchors));self.context=context
  try:yield self
  except BaseException:
   self.periods,self.allocations,self.revocations,self.replays,self.audit,self.anchors=before;raise
 def execute(self,statement,params=()):
  self.calls.append((statement,params));s=' '.join(statement.split())
  if s.startswith('SELECT pg_advisory'):return Cursor([(None,)])
  if 'FROM pg_catalog.pg_policy p JOIN' in s:
   scope="(tenant_id = NULLIF(current_setting('app.tenant_id'::text, true), ''::text)) AND (anchor_workspace_id = NULLIF(current_setting('app.workspace_id'::text, true), ''::text))"
   using="("+scope+" AND (current_setting('app.capability'::text, true) = ANY (ARRAY['routing_budget.issue'::text, 'routing_budget.revoke'::text, 'routing_budget.read'::text, 'routing_budget.upper'::text])))"
   rows=[]
   for name in ['routing_budget_allocations','routing_budget_periods','routing_budget_revocations']:
    cap='routing_budget.revoke' if name.endswith('revocations') else 'routing_budget.issue'
    check="("+scope+" AND (current_setting('app.capability'::text, true) = '"+cap+"'::text))"
    rows.append((name,'*',[16384],True,using,check))
   if self.catalog_fail=='policy':rows[0]=(*rows[0][:4],'true',rows[0][5])
   return Cursor(rows)
  if s=="SELECT oid FROM pg_roles WHERE rolname='daon_app'":return Cursor([(16384,)])
  if 'FROM pg_catalog.pg_constraint k JOIN' in s:return Cursor([(self.catalog_fail!=('foreign' if 'child_columns' in s else 'check'),)])
  if 'FROM pg_catalog.pg_index i WHERE i.indexrelid' in s:return Cursor([(self.catalog_fail!='anchor',)])
  if 'FROM (VALUES' in s and 'pg_catalog.pg_index' in s:return Cursor([(self.catalog_fail!='index',)])
  if 'pg_catalog.pg_class' in s:
   # 부분 catalog SQL 대역: guard 후보가 동일해도 WHEN/인자 조건을 질의하지 않으면 기존 SELECT는 true다.
   protected=self.protected and self.catalog_fail!='table'
   if self.catalog_fail=='conditional_trigger' and 't.tgqual IS NULL' in s:protected=False
   if self.catalog_fail=='trigger_argument' and 't.tgnargs=0' in s:protected=False
   return Cursor([(protected,)])
  if 'pg_catalog.pg_proc' in s:return Cursor([(self.catalog_fail!='function',)])
  if 'identity_' in s:
   import re
   text=re.sub(r'public[.]','',s);text=re.sub(r'identity_org_tenant_memberships','tenant_memberships',text);text=re.sub(r'identity_','',text).replace('%s','?').replace(' FOR SHARE','')
   with sqlite3.connect(self.q['path']) as c:
    c.row_factory=sqlite3.Row;cursor=c.execute(text,params);return Cursor([Row(row) for row in cursor.fetchall()])
  if s.startswith('SELECT request_fingerprint,result FROM idempotency_records'):
   row=self.replays.get(tuple(params));return Cursor([] if row is None else [row])
  if s.startswith('SELECT p.canonical_text'):
   tenant,workspace,period=params;row=self.periods.get((tenant,workspace,period))
   if row is None:return Cursor()
   return Cursor([(row['text'],row['payload'],row['digest'],self.allocations[(tenant,workspace,period)]['scope'],(tenant,workspace,period) in self.revocations)])
  if s.startswith('SELECT canonical_text,payload,content_digest FROM routing_budget_periods'):
   return Cursor([(v['text'],v['payload'],v['digest']) for (t,w,p),v in self.periods.items() if (t,w)==tuple(params)])
  if s.startswith('INSERT INTO routing_budget_periods'):
   if "'anchor'" in s:
    tenant,workspace,root,parent,actor,issued,text,payload,sha=params
    existing=self.anchors.get(tenant)
    if existing is not None and existing[0]!=workspace:raise RoutingSourceError('BUDGET_SOURCE_UNAVAILABLE',503)
    self.anchors[tenant]=(workspace,root,text,sha);return Cursor()
   if self.fail=='insert':raise RuntimeError('SQL_DOUBLE_INSERT_FAILURE')
   tenant,workspace,period,root,source,currency,amount,start,end,actor,issued,text,payload,sha=params
   self.periods[(tenant,workspace,period)]=dict(text=text,payload=payload.obj,digest=sha);return Cursor()
  if s.startswith('INSERT INTO routing_budget_allocations'):
   tenant,workspace,period,scope,amount,run=params;self.allocations[(tenant,workspace,period)]=dict(scope=scope,amount=amount,run=run);return Cursor()
  if s.startswith('INSERT INTO routing_budget_revocations'):
   tenant,workspace,period,actor,at,reason,text,payload,sha=params;self.revocations[(tenant,workspace,period)]=dict(actor=actor,at=at,reason=reason,digest=sha);return Cursor()
  if s.startswith('INSERT INTO idempotency_records'):
   tenant,workspace,actor,op,key,fp,result,expires=params;self.replays[(tenant,workspace,actor,op,key)]=(fp,result.obj);return Cursor()
  if s.startswith('INSERT INTO audit_events'):
   if self.fail=='audit':raise RuntimeError('SQL_DOUBLE_AUDIT_FAILURE')
   self.audit.append(tuple(x.obj if hasattr(x,'obj') else x for x in params));return Cursor()
  raise AssertionError('UNEXPECTED_SQL_DOUBLE_STATEMENT')

@pytest.fixture
def system(qa):
 q=qa;store=SqlStore(q);registry=CurrencyRegistry((('USD',2),));repository=PostgresBudgetSourceRepository(store,registry=registry,clock=q['clock'])
 service=BudgetSourceService(repository,q['adapter'],registry=registry,clock=q['clock']);return q,store,service

def body():return dict(schema_version=1,currency='USD',period_limit='10',starts_at='2026-10-21T00:00:00Z',expires_at='2026-11-21T00:00:00Z',allocations=[dict(workspace_id='workspace-001',period_limit='7',run_limit='2')])
def prepare(system,*,data=None,key='issue-key-0000001',conditions=None,operation='routing_budget.issue'):
 q,store,service=system;data=body() if data is None else data;conditions=conditions or BudgetPreconditions(0,if_none_match='*')
 binding=service.binding(q['ctx'],data,preconditions=conditions,idempotency_key=key,operation=operation)
 proof=q['adapter'].issue(q['ctx'],binding,access_token=q['credentials'].access_token,password=q['password'])
 return dict(context=q['ctx'],body=data,preconditions=conditions,idempotency_key=key,access_token=q['credentials'].access_token,proof=proof)

def test_real_sql_repository_issue_restart_read_projection_and_exact_replay(system):
 q,store,service=system;request=prepare(system);view=service.issue_budget(**request)
 assert view.source.budget.period_limit==10 and len(store.periods)==len(store.allocations)==len(store.audit)==1
 assert counts(q)==(1,1)
 restarted=PostgresBudgetSourceRepository(store,registry=service.registry,clock=q['clock'])
 again=service.issue_budget(**request);assert again.source.period_id==view.source.period_id and counts(q)==(1,1)
 count=len(store.calls);read=service.get_budget(q['ctx'],view.source.period_id,access_token=request['access_token'])
 assert read.source.identity_digest==view.source.identity_digest
 assert all(not sql.lstrip().startswith(('INSERT','UPDATE','DELETE')) for sql,_ in store.calls[count:])
 projection=restarted.upper_budget(store,q['ctx'])
 assert projection.version=='1' and projection.run_limit=='2' and projection.actor_id=='owner'
 assert 'owner' not in view.budget_scope_id
 assert store.calls[0][0].startswith('SELECT pg_advisory')

@pytest.mark.parametrize('failure',['insert','audit'])
def test_identity_consumption_survives_cloud_rollback_and_exact_binding_recovers(system,failure):
 q,store,service=system;request=prepare(system);store.fail=failure
 with pytest.raises(RuntimeError):service.issue_budget(**request)
 assert not store.periods and not store.allocations and not store.replays and not store.audit and not store.anchors and counts(q)==(1,1)
 q['clock'].advance(minutes=6);store.fail=None
 view=service.issue_budget(**request);assert view.source.version==1 and counts(q)==(1,1)

@pytest.mark.parametrize('change',[dict(idempotency_key='issue-key-0000002'),dict(preconditions=BudgetPreconditions(0,if_none_match='different'))])
def test_proof_not_reusable_for_other_operation_key_or_conditions(system,change):
 q,store,service=system;request=prepare(system)
 with pytest.raises((RoutingSourceError,IdentityError)):service.issue_budget(**{**request,**change})
 assert not store.periods and counts(q)==(1,0)

def test_revocation_append_only_current_replay_inactive_and_overlap_still_denied(system):
 q,store,service=system;request=prepare(system);view=service.issue_budget(**request)
 data=dict(period_id=view.source.period_id,reason_code='ADMIN_REVOKED')
 revoke=prepare(system,data=data,key='revoke-key-000001',conditions=BudgetPreconditions(1,if_match=view.etag),operation='routing_budget.revoke')
 revoked=service.revoke_budget(**revoke);assert revoked.source.revoked and len(store.revocations)==1
 assert service.issue_budget(**request).source.revoked and counts(q)==(2,2)
 with pytest.raises(RoutingSourceError):store_repo=service.repository.upper_budget(store,q['ctx'])
 new=prepare(system,key='issue-key-0000002')
 with pytest.raises(RoutingSourceError) as error:service.issue_budget(**new)
 assert error.value.code=='BUDGET_PERIOD_OVERLAP' and len(store.periods)==1

@pytest.mark.parametrize('change',[dict(allocations=[dict(workspace_id='other-workspace',period_limit='7',run_limit='2')]),dict(allocations=[dict(workspace_id='workspace-001',period_limit='3',run_limit='2'),dict(workspace_id='other',period_limit='3',run_limit='2')])])
def test_other_or_multiple_allocation_is_rejected_before_consumption(system,change):
 q,store,service=system
 with pytest.raises(RoutingSourceError):prepare(system,data={**body(),**change})
 assert not store.periods and counts(q)==(0,0)

def test_missing_schema_current_role_workflow_and_session_replay_fail_closed(system):
 q,store,service=system;request=prepare(system);store.protected=False
 with pytest.raises(RoutingSourceError) as error:service.issue_budget(**request)
 assert error.value.status==503 and counts(q)==(1,0)
 store.protected=True;view=service.issue_budget(**request)
 with q['repo'].transaction() as conn:conn.execute("UPDATE tenant_memberships SET state='suspended'")
 with pytest.raises(AuthorizationError):service.issue_budget(**request)
 assert len(store.periods)==1


def test_new_source_schema_draft_has_three_tables_force_rls_and_immutable_guards():
 from pathlib import Path
 sql=Path(__file__).parents[1].joinpath('migrations/versions/0076_routing_budget_sources.sql').read_text(encoding='utf-8')
 assert sql.count('CREATE TABLE public.')==3
 assert 'SECURITY DEFINER' not in sql and 'CREATE ROLE' not in sql
 for name in ('routing_budget_periods','routing_budget_allocations','routing_budget_revocations'):
  assert 'ALTER TABLE public.'+name+' FORCE ROW LEVEL SECURITY' in sql
 assert "record_kind='anchor'" in sql and "parent_kind='period'" in sql
 assert 'BEFORE TRUNCATE' in sql and 'pg_advisory_xact_lock' in sql


def test_missing_write_capability_is_not_nullable_sql_authorization():
 from pathlib import Path
 sql=Path(__file__).parents[1].joinpath('migrations/versions/0076_routing_budget_sources.sql').read_text(encoding='utf-8')
 assert "current_setting('app.capability',true)<>'routing_budget" not in sql
 assert "current_setting('app.capability',true) IS DISTINCT FROM 'routing_budget.issue'" in sql
 assert 'num_nonnulls(($1).source_id,($1).version,($1).currency,($1).period_limit,($1).starts_at,($1).expires_at)=6' in sql
 assert 'jsonb_object_length' not in sql and 'numeric(18,6)' not in sql
 assert 'CHECK(public.valid_routing_budget_period(routing_budget_periods.*) IS TRUE)' in sql

@pytest.mark.parametrize('conditions,code,status',[(BudgetPreconditions(0),'BUDGET_PRECONDITION_REQUIRED',428),(BudgetPreconditions(1,if_none_match='*'),'BUDGET_VERSION_CONFLICT',409),(BudgetPreconditions(0,if_match='wrong'),'BUDGET_ETAG_MISMATCH',412)])
def test_preconditions_are_checked_before_new_consumption(system,conditions,code,status):
 q,store,service=system;request=prepare(system,conditions=conditions)
 with pytest.raises(RoutingSourceError) as error:service.issue_budget(**request)
 assert (error.value.code,error.value.status)==(code,status) and counts(q)==(1,0) and not store.periods

def test_changed_body_after_commit_same_key_conflicts_and_corrupt_canonical_fails_closed(system):
 q,store,service=system;request=prepare(system);view=service.issue_budget(**request)
 changed={**body(),'period_limit':'11'}
 with pytest.raises(RoutingSourceError) as error:service.issue_budget(**{**request,'body':changed})
 assert error.value.code=='IDEMPOTENCY_KEY_REUSED' and len(store.periods)==1 and counts(q)==(1,1)
 only=next(iter(store.periods.values()));only['text']='{}'
 with pytest.raises(RoutingSourceError) as corrupt:service.get_budget(q['ctx'],view.source.period_id,access_token=request['access_token'])
 assert (corrupt.value.code,corrupt.value.status)==('BUDGET_SOURCE_UNAVAILABLE',503)

def test_role_and_session_revocation_deny_replay_without_new_business_write(system):
 from daon_user_api.authorization import AuthorizationError
 from daon_user_api.identity import IdentityError
 q,store,service=system;request=prepare(system);service.issue_budget(**request);before=deepcopy((store.periods,store.allocations,store.replays,store.audit))
 with q['auth_repo'].transaction() as conn:conn.execute("UPDATE auth_tenant_roles SET state='inactive'")
 with pytest.raises(AuthorizationError):service.issue_budget(**request)
 with q['auth_repo'].transaction() as conn:conn.execute("UPDATE auth_tenant_roles SET state='active'")
 with q['repo'].transaction() as conn:conn.execute("UPDATE sessions SET state='revoked'")
 with pytest.raises(IdentityError):service.issue_budget(**request)
 assert (store.periods,store.allocations,store.replays,store.audit)==before and counts(q)==(1,1)

def test_no_password_access_token_or_proof_is_duplicated_in_cloud_rows_or_params(system):
 q,store,service=system;request=prepare(system);service.issue_budget(**request)
 cloud=repr((store.periods,store.allocations,store.replays,store.audit,store.calls))
 for secret in (q['password'],request['access_token'],request['proof']):assert secret not in cloud


def test_actual_issue_payload_is_the_exact_server_schema_and_canonical_digest(system):
 import hashlib
 q,store,service=system;request=prepare(system);view=service.issue_budget(**request)
 row=next(iter(store.periods.values()));payload=row['payload']
 assert set(payload)=={'schema_version','tenant_id','organization_id','anchor_workspace_id','period_id','source_id','version','created_by','issued_at','budget'}
 assert len(payload['budget']['allocations'])==1 and payload['budget']['allocations'][0]['workspace_id']=='workspace-001'
 assert hashlib.sha256(row['text'].encode()).hexdigest()==row['digest'] and json.loads(row['text'])==payload

def test_missing_insert_privilege_must_not_be_treated_as_select_insert_readiness():
 import inspect
 from daon_user_api.routing_sources_postgres import PostgresBudgetSourceRepository
 sql=inspect.getsource(PostgresBudgetSourceRepository._ready)
 assert "has_table_privilege('daon_app',c.oid,'SELECT,INSERT')" not in sql
 assert "has_table_privilege('daon_app',c.oid,'SELECT')" in sql and "has_table_privilege('daon_app',c.oid,'INSERT')" in sql


def test_upper_budget_accepts_real_b1_metadata_context_without_minting_session(system):
 from daon_user_api.workspace_model_defaults import WorkspaceModelDefaultsContext
 q,store,service=system;view=service.issue_budget(**prepare(system))
 context=WorkspaceModelDefaultsContext(q['ctx'].tenant_id,q['ctx'].workspace_id,q['ctx'].actor_id,q['ctx'].trace_id,q['ctx'].policy_version)
 result=service.repository.upper_budget(store,context)
 assert result.version=='1' and result.source_id==view.source.source_id and result.run_limit=='2'
 assert not hasattr(context,'session_id') and not hasattr(context,'organization_id')


@pytest.mark.parametrize('catalog',['table','function','foreign','index','anchor','check','policy'])
def test_each_missing_or_replaced_critical_catalog_protection_denies_before_consumption(system,catalog):
 q,store,service=system;request=prepare(system);store.catalog_fail=catalog
 with pytest.raises(RoutingSourceError) as error:service.issue_budget(**request)
 assert (error.value.code,error.value.status)==('BUDGET_SOURCE_UNAVAILABLE',503)
 assert counts(q)==(1,0) and not store.periods and not store.allocations and not store.replays

def test_exact_sql_predicate_bodies_and_signatures_match_repository_pins():
 import inspect,re,hashlib
 from pathlib import Path
 sql=Path(__file__).parents[1].joinpath('migrations/versions/0076_routing_budget_sources.sql').read_text(encoding='utf-8')
 code=inspect.getsource(PostgresBudgetSourceRepository._ready)
 functions=re.findall(r'CREATE FUNCTION public[.]([a-z_]+)\(([^)]*)\) RETURNS (boolean|trigger)\n(.*?)AS \$\$(.*?)\$\$;',sql,re.S)
 assert len(functions)==5
 for name,args,returns,header,body in functions:
  assert hashlib.md5(body.encode()).hexdigest() in code
  if returns=='boolean':
   assert 'IMMUTABLE SECURITY INVOKER' in header and args.startswith('public.routing_budget_')
   assert not re.search(r'\b(FROM|JOIN|EXECUTE|INSERT|UPDATE|DELETE)\b',body,re.I)
 assert 'routing_budget_one_anchor' in code and 'routing_budget_period_anchor_fk' in code


def test_shared_upper_budget_is_current_workspace_view_not_organization_management(system):
 import secrets
 from daon_user_api.identity import PASSWORD_HASHER,DevicePlatform
 from daon_user_api.workspace_model_defaults import WorkspaceModelDefaultsContext
 q,store,service=system;view=service.issue_budget(**prepare(system));password=secrets.token_urlsafe(28)
 with q['repo'].transaction() as conn:
  conn.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,state) VALUES (?,?,?,?,?,?,?,'active')",('viewer','local','viewer','viewer','viewer@example.com',PASSWORD_HASHER.hash(password),q['clock']().isoformat()))
  conn.execute("INSERT INTO memberships VALUES('tenant-001','viewer','member')")
 with q['auth_repo'].transaction() as conn:conn.execute("INSERT INTO auth_memberships VALUES('tenant-001','workspace-001','viewer','viewer','active',1,?)",(q['clock']().isoformat(),))
 context=WorkspaceModelDefaultsContext('tenant-001','workspace-001','viewer','trace-qa','policy-qa')
 projected=service.repository.upper_budget(store,context)
 assert projected.actor_id=='viewer' and projected.source_id==view.source.source_id and projected.version=='1'
 with q['auth_repo'].transaction() as conn:conn.execute("UPDATE auth_memberships SET state='inactive' WHERE user_id='viewer'")
 from daon_user_api.open_law_source_access import OpenLawAccessError
 with pytest.raises(OpenLawAccessError):service.repository.upper_budget(store,context)

def test_binding_context_cannot_be_mutated_into_another_organization(system):
 q,store,service=system;request=prepare(system)
 with pytest.raises(RoutingSourceError):service.issue_budget(**{**request,'context':replace(q['ctx'],workspace_id='other-workspace')})
 assert not store.periods and counts(q)==(1,0)


@pytest.mark.parametrize('catalog',['conditional_trigger','trigger_argument'])
def test_conditional_or_argument_trigger_cannot_satisfy_guard_manifest(system,catalog):
 q,store,service=system;request=prepare(system);store.catalog_fail=catalog
 with pytest.raises(RoutingSourceError) as error:service.issue_budget(**request)
 assert (error.value.code,error.value.status)==('BUDGET_SOURCE_UNAVAILABLE',503)
 assert not store.periods and not store.allocations and not store.replays and counts(q)==(1,0)

def test_each_exact_trigger_manifest_requires_unconditional_zero_argument_guard():
 import re,inspect
 sql=inspect.getsource(PostgresBudgetSourceRepository._ready)
 trigger_checks=re.findall(r"AND EXISTS\(SELECT 1 FROM pg_catalog[.]pg_trigger t WHERE (.*?)\)\)",sql,re.S)
 assert len(trigger_checks)==3
 assert all('t.tgqual IS NULL' in check and 't.tgnargs=0' in check for check in trigger_checks)
