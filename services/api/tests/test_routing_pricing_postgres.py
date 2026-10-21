"""실제 가격 repository SQL/거래 대역. 실제PG/RLS/경합 증거가 아니다."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib,json,sqlite3
import pytest
from test_routing_price_reauthentication import price_qa,qa
from test_routing_budget_reauthentication import counts
from test_routing_sources_postgres import SqlStore as AuthorizationSqlDouble,Cursor
from test_routing_pricing import body
from daon_user_api.routing_sources import CurrencyRegistry
from daon_user_api.routing_pricing import PricePreconditions
from daon_user_api.routing_pricing_postgres import PostgresPriceSourceRepository
from daon_user_api.routing_pricing import PriceSourceService,RoutingPriceStorageError
from daon_user_api.identity import IdentityError

class PriceSqlStore:
 def __init__(self,q):
  self.q=q;self.auth=AuthorizationSqlDouble(q);self.versions={};self.current={};self.revocations={};self.replays={};self.audit=[];self.calls=[];self.fail=None;self.catalog=True
  self.metadata_reads=0;self.rotate_at=None
  self.metadata=(1,'connection-qa','organization/model-v1','CUSTOM',2,1,True,'verified','public','required',True,'openai_compatible','a'*64,'ready',['text_generation'],True)
 @contextmanager
 def _transaction(self,context):
  before=deepcopy((self.versions,self.current,self.revocations,self.replays,self.audit));self.context=context
  try:yield self
  except BaseException:
   self.versions,self.current,self.revocations,self.replays,self.audit=before;raise
 def execute(self,statement,params=()):
  self.calls.append((statement,params));s=' '.join(statement.split())
  if s.startswith('SELECT pg_advisory'):return Cursor([(None,)])
  if 'FROM pg_catalog.' in s:
   if 'price_catalog_' in s:
    import re
    section=re.search(r'price_catalog_([a-z]+)',s).group(1)
    return Cursor([(self.catalog is True or type(self.catalog) is str and self.catalog!=section,)])
  if 'FROM system_provider_connections c' in s:
   self.metadata_reads+=1
   if self.rotate_at==self.metadata_reads:
    row=list(self.metadata);row[5]+=1;self.metadata=tuple(row)
   return Cursor([] if self.metadata is None else [self.metadata])
  if 'identity_' in s:return self.auth.execute(statement,params)
  if s.startswith('SELECT request_fingerprint,result FROM idempotency_records'):return Cursor([] if tuple(params) not in self.replays else [self.replays[tuple(params)]])
  if s.startswith('SELECT current_revision,price_version,revoked FROM routing_price_current'):
   return Cursor([] if tuple(params) not in self.current else [self.current[tuple(params)]])
  if s.startswith('SELECT canonical_text,payload,content_digest FROM routing_price_versions'):
   key=tuple(params);return Cursor([] if key not in self.versions else [self.versions[key]])
  if s.startswith('SELECT price_version FROM routing_price_revocations'):
   return Cursor([] if tuple(params) not in self.revocations else [(params[-1],)])
  if s.startswith('SELECT coalesce(max(price_version),0) FROM routing_price_versions'):
   return Cursor([(max([key[-1] for key in self.versions if key[:3]==tuple(params)],default=0),)])
  if s.startswith('INSERT INTO routing_price_versions'):
   if self.fail=='insert':raise RuntimeError('SQL_DOUBLE_INSERT_FAILURE')
   tenant,workspace,series,version,creator,issued,text,payload,sha=params
   self.versions[(tenant,workspace,series,version)]=(text,payload.obj,sha);return Cursor()
  if s.startswith('INSERT INTO routing_price_current'):
   if self.fail=='cas':return type('Result',(),{'rowcount':0})()
   tenant,workspace,series,revision,version,revoked,actor,at,operation=params
   if (tenant,workspace,series) in self.current:return type('Result',(),{'rowcount':0})()
   self.current[(tenant,workspace,series)]=(revision,version,revoked);return type('Result',(),{'rowcount':1})()
  if s.startswith('UPDATE routing_price_current SET'):
   revision,version,revoked,actor,at,operation,tenant,workspace,series,expected=params
   previous=self.current.get((tenant,workspace,series))
   if previous is None or previous[0]!=expected:return type('Result',(),{'rowcount':0})()
   self.current[(tenant,workspace,series)]=(revision,version,revoked);return type('Result',(),{'rowcount':1})()
  if s.startswith('INSERT INTO routing_price_revocations'):
   tenant,workspace,series,version,actor,at,text,payload,sha=params
   self.revocations[(tenant,workspace,series,version)]=(text,payload.obj,sha);return Cursor()
  if s.startswith('INSERT INTO idempotency_records'):
   *key,fp,result,expiry=params;self.replays[tuple(key)]=(fp,result.obj);return Cursor()
  if s.startswith('INSERT INTO audit_events'):
   if self.fail=='audit':raise RuntimeError('SQL_DOUBLE_AUDIT_FAILURE')
   self.audit.append(tuple(x.obj if hasattr(x,'obj') else x for x in params));return Cursor()
  raise AssertionError('UNEXPECTED_PRICE_SQL')

@pytest.fixture
def system(price_qa):
 q=price_qa;store=PriceSqlStore(q);registry=CurrencyRegistry((('USD',2),))
 repository=PostgresPriceSourceRepository(store,registry=registry,clock=q['clock'])
 service=PriceSourceService(repository,q['price_adapter'],registry=registry,clock=q['clock'])
 return q,store,service

def data(q,expected=0):
 value=body();value.update(workspace_id=q['ctx'].workspace_id,expected_version=expected,valid_from=q['clock']().isoformat(),valid_until=(q['clock']()+__import__('datetime').timedelta(hours=24)).isoformat());value['evidence']['reviewed_at']=q['clock']().isoformat();return value

def prepare(system,*,value=None,key='price-register-key-0001',conditions=None,operation='routing_price.register'):
 q,store,service=system;value=data(q) if value is None else value;conditions=conditions or PricePreconditions(0,if_none_match='*')
 binding=service.binding(q['ctx'],value,operation=operation,preconditions=conditions,idempotency_key=key,access_token=q['credentials'].access_token)
 proof=q['price_adapter'].issue(q['ctx'],binding,access_token=q['credentials'].access_token,password=q['password'])
 return dict(context=q['ctx'],body=value,preconditions=conditions,idempotency_key=key,access_token=q['credentials'].access_token,proof=proof)

def test_register_restart_current_and_read_has_no_business_or_consumption_writes(system):
 q,s,service=system;r=prepare(system);v=service.register_price(**r)
 assert (v.price_version,v.current_revision,v.verification_state,v.revoked,v.target_changed)==(1,1,'unverified',False,False)
 before=(len(s.versions),len(s.current),len(s.replays),len(s.audit),counts(q));n=len(s.calls)
 loaded=service.get_price(q['ctx'],v.series_id,access_token=r['access_token'])
 assert loaded==v and before==(len(s.versions),len(s.current),len(s.replays),len(s.audit),counts(q))
 assert all(sql.lstrip().startswith('SELECT') for sql,_ in s.calls[n:])
 assert service.register_price(**r)==v and counts(q)==(1,1)
 assert not hasattr(v,'billing_reference') and not hasattr(v,'account_reference') and s.calls[0][0].startswith('SELECT pg_advisory')
 metadata_sql=[sql for sql,_ in s.calls if 'FROM system_provider_connections c' in sql]
 assert metadata_sql and all('(c.encrypted_credential IS NOT NULL)' in sql and "encode(sha256(convert_to(c.base_url,'UTF8')),'hex')" in sql for sql in metadata_sql)
 assert all('c.encrypted_credential,' not in sql and 'credential_nonce' not in sql for sql,_ in s.calls)
 for _,payload,_ in s.versions.values():
  assert set(payload['target'])=={'connection_version','connection_id','model_id','provider_code','catalog_version','credential_version','enabled','verification_status','access_mode','credential_requirement','has_credential','adapter_type','endpoint_digest','catalog_status','capabilities','allowed'}
  assert payload['target']['endpoint_digest']=='a'*64
  assert not any('base_url' in key or 'nonce' in key or 'secret' in key for key in payload['target'])

@pytest.mark.parametrize('failure',['insert','audit'])
def test_real_identity_consumed_then_sql_rollback_and_exact_expired_recovery(system,failure):
 q,s,service=system;r=prepare(system);s.fail=failure
 with pytest.raises(RuntimeError):service.register_price(**r)
 assert not s.versions and not s.current and not s.replays and not s.audit and counts(q)==(1,1)
 q['clock'].advance(minutes=6);s.fail=None
 v=service.register_price(**r)
 assert v.price_version==1 and counts(q)==(1,1)


def test_versions_and_current_revision_are_distinct_and_historical_revoke_preserves_pointer(system):
 q,s,service=system;v1=service.register_price(**prepare(system))
 v2=service.register_price(**prepare(system,value=data(q,1),key='price-register-key-0002',conditions=PricePreconditions(1,if_match=v1.current_etag)))
 assert (v2.price_version,v2.current_revision)==(2,2)
 old=service.get_price(q['ctx'],v1.series_id,1,access_token=q['credentials'].access_token)
 revoke=prepare(system,value=dict(series_id=v1.series_id,price_version=1,reason_code='ADMIN_REVOKED'),operation='routing_price.revoke',key='price-revoke-old-key',conditions=PricePreconditions(1,if_match=old.version_etag))
 assert service.revoke_price(**revoke).revoked and s.current[(q['ctx'].tenant_id,q['ctx'].workspace_id,v1.series_id)]==(2,2,False)
 current_revoke=prepare(system,value=dict(series_id=v2.series_id,price_version=2,reason_code='ADMIN_REVOKED'),operation='routing_price.revoke',key='price-revoke-current-key',conditions=PricePreconditions(2,if_match=v2.version_etag))
 result=service.revoke_price(**current_revoke);assert (result.price_version,result.current_revision,result.revoked)==(2,3,True)
 assert service.revoke_price(**current_revoke)==result and len(s.revocations)==2
 v3=service.register_price(**prepare(system,value=data(q,3),key='price-register-key-0003',conditions=PricePreconditions(3,if_match=result.current_etag)))
 assert (v3.price_version,v3.current_revision)==(3,4) and not v3.revoked


def test_metadata_changed_before_proof_consume_and_after_commit_have_different_paths(system):
 q,s,service=system;r=prepare(system);old=s.metadata;s.metadata=(*old[:5],2,*old[6:])
 with pytest.raises(IdentityError):service.register_price(**r)
 assert not s.versions and counts(q)==(1,0)
 s.metadata=old;v=service.register_price(**r);s.metadata=(*old[:5],2,*old[6:])
 replay=service.register_price(**r);assert replay.target_changed and replay.price_version==v.price_version and counts(q)==(1,1)
 s.metadata=None
 assert service.get_price(q['ctx'],v.series_id,access_token=r['access_token']).target_changed
 revoke=prepare(system,value=dict(series_id=v.series_id,price_version=1,reason_code='ADMIN_REVOKED'),operation='routing_price.revoke',key='price-revoke-deleted-key',conditions=PricePreconditions(1,if_match=v.version_etag))
 assert service.revoke_price(**revoke).revoked

@pytest.mark.parametrize('index,value',[(0,2),(4,3),(6,False),(7,'unverified'),(8,'personal'),(10,False),(13,'pending'),(14,[]),(15,False)])
def test_unusable_current_metadata_register_fails_before_proof(system,index,value):
 q,s,service=system;row=list(s.metadata);row[index]=value;s.metadata=tuple(row)
 with pytest.raises(RoutingPriceStorageError):prepare(system)
 assert not s.versions and counts(q)==(0,0)


def test_stale_current_conditions_and_same_key_other_body_cannot_create_second_version(system):
 q,s,service=system;r1=prepare(system);r2=prepare(system,key='price-register-key-0002');v=service.register_price(**r1)
 with pytest.raises(RoutingPriceStorageError):service.register_price(**r2)
 changed=deepcopy(r1);changed['body']['unit_prices']['mandatory_fee_per_request']='1'
 with pytest.raises(RoutingPriceStorageError) as error:service.register_price(**changed)
 assert error.value.code=='IDEMPOTENCY_KEY_REUSED' and len(s.versions)==1


def test_current_role_workflow_session_and_missing_catalog_fail_closed(system):
 q,s,service=system;r=prepare(system);s.catalog=False
 with pytest.raises(RoutingPriceStorageError):service.register_price(**r)
 assert counts(q)==(1,0)
 s.catalog=True;v=service.register_price(**r)
 with q['repo'].transaction() as conn:conn.execute("UPDATE sessions SET state='revoked'")
 with pytest.raises(IdentityError):service.register_price(**r)
 assert len(s.versions)==1



def test_sql_draft_has_only_three_scoped_price_tables_and_forward_only_revision():
 from pathlib import Path
 root=Path(__file__).parents[1]/'migrations/versions'
 sql=(root/'0077_routing_price_sources.sql').read_text(encoding='utf8')
 assert sql.count('CREATE TABLE public.')==3
 assert all('FORCE ROW LEVEL SECURITY' in sql and 'ALTER TABLE public.'+name in sql for name in ('routing_price_versions','routing_price_current','routing_price_revocations'))
 assert 'SECURITY DEFINER' not in sql and 'CREATE ROLE' not in sql and 'CREATE EXTENSION' not in sql
 assert 'REFERENCES public.system_provider' not in sql and 'REFERENCES public.user_provider' not in sql
 assert 'new.current_revision<>old.current_revision+1' in sql.lower()
 assert "verification_state'='unverified'" in sql
 assert 'tgqual IS NULL' in __import__('inspect').getsource(PostgresPriceSourceRepository._ready)
 assert 'tgnargs=0' in __import__('inspect').getsource(PostgresPriceSourceRepository._ready)


def test_critical_catalog_readiness_pins_actual_sql_bodies():
 import inspect,re,hashlib
 from pathlib import Path
 sql=Path(__file__).parents[1].joinpath('migrations/versions/0077_routing_price_sources.sql').read_text(encoding='utf8')
 code=inspect.getsource(PostgresPriceSourceRepository._ready)
 functions=re.findall(r'CREATE FUNCTION public[.]([a-z_]+)\(([^)]*)\) RETURNS (boolean|trigger)\n(.*?)AS \$\$(.*?)\$\$;',sql,re.S)
 assert len(functions)==5
 for name,args,returns,header,body in functions:
  assert hashlib.md5(body.encode()).hexdigest() in code
  if returns=='boolean':assert 'IMMUTABLE SECURITY INVOKER' in header and not re.search(r'\b(FROM|JOIN|EXECUTE|INSERT|UPDATE|DELETE)\b',body,re.I)
 assert 'price_catalog_functions' in code and 'price_catalog_constraints' in code and 'price_catalog_policies' in code


@pytest.mark.parametrize('section',['tables','functions','constraints','indexes','checks','policies'])
def test_each_critical_catalog_section_fails_closed_before_consumption(system,section):
 q,s,service=system;r=prepare(system);s.catalog=section
 with pytest.raises(RoutingPriceStorageError) as error:service.register_price(**r)
 assert error.value.code=='PRICE_SOURCE_UNAVAILABLE' and counts(q)==(1,0) and not s.versions


def test_stored_corruption_has_safe_storage_error_not_original_datetime(system):
 q,s,service=system;r=prepare(system);v=service.register_price(**r)
 key=next(iter(s.versions));_,value,_=s.versions[key];value['issued_at']='sensitive-invalid-date'
 from daon_user_api.routing_price_reauthentication import canonical,digest
 s.versions[key]=(canonical(value),value,digest(value))
 with pytest.raises(RoutingPriceStorageError) as error:service.get_price(q['ctx'],v.series_id,access_token=r['access_token'])
 assert error.value.code=='PRICE_SOURCE_UNAVAILABLE' and 'sensitive' not in str(error.value)


def test_current_disabled_target_history_and_revoke_remain_available(system):
 q,s,service=system;r=prepare(system);v=service.register_price(**r);row=list(s.metadata);row[6]=False;s.metadata=tuple(row)
 loaded=service.get_price(q['ctx'],v.series_id,access_token=r['access_token']);assert loaded.target_changed
 revoked=service.revoke_price(**prepare(system,value=dict(series_id=v.series_id,price_version=1,reason_code='POLICY_WITHDRAWN'),operation='routing_price.revoke',key='price-disable-revoke-key',conditions=PricePreconditions(1,if_match=loaded.version_etag)))
 assert revoked.revoked and revoked.target_changed


def test_currency_basis_and_credential_rotation_cannot_reset_series(system):
 q,s,service=system;v=service.register_price(**prepare(system));value=data(q,1)
 value['billing_basis']='verified_zero_v1';value['unit_prices']={key:'0' for key in value['unit_prices']};value['evidence']['source_kind']='internal_zero_policy'
 row=list(s.metadata);row[5]+=1;s.metadata=tuple(row)
 second=service.register_price(**prepare(system,value=value,key='price-zero-register-key',conditions=PricePreconditions(1,if_match=v.current_etag)))
 assert second.series_id==v.series_id and second.price_version==2 and second.verification_state=='unverified'
 assert not hasattr(second,'billing_reference') and not hasattr(second,'attested')


def test_safe_conditional_trigger_manifest_and_no_crypto_reads():
 import inspect
 code=inspect.getsource(PostgresPriceSourceRepository._ready)
 assert code.count('t.tgqual IS NULL')==3 and code.count('t.tgnargs=0')==3
 metadata=inspect.getsource(PostgresPriceSourceRepository._metadata)
 assert 'FOR SHARE OF c,m,a' in metadata and 'LEFT JOIN' not in metadata
 assert '.decrypt' not in metadata and 'credential_text' not in metadata and 'credential_nonce' not in metadata



def test_metadata_change_after_real_consumption_rolls_back_and_requires_same_snapshot(system):
 q,s,service=system;r=prepare(system);original=s.metadata;s.rotate_at=s.metadata_reads+2
 with pytest.raises(RoutingPriceStorageError) as error:service.register_price(**r)
 assert error.value.code=='PRICE_TARGET_CHANGED' and counts(q)==(1,1)
 assert not s.versions and not s.current and not s.replays
 s.rotate_at=None;s.metadata=original;q['clock'].advance(minutes=6)
 assert service.register_price(**r).price_version==1 and counts(q)==(1,1)


def test_failed_pointer_cas_rolls_back_real_repository_writes(system):
 q,s,service=system;r=prepare(system);s.fail='cas'
 with pytest.raises(RoutingPriceStorageError) as error:service.register_price(**r)
 assert error.value.code=='PRICE_VERSION_CONFLICT' and not s.versions and not s.current and not s.replays and counts(q)==(1,1)




def test_expired_source_history_replay_is_marked_but_new_registration_is_rejected(system):
 from datetime import timedelta
 q,s,service=system;value=data(q);value['valid_until']=(q['clock']()+timedelta(minutes=5)).isoformat()
 r=prepare(system,value=value);v=service.register_price(**r);q['clock'].advance(minutes=6)
 loaded=service.get_price(q['ctx'],v.series_id,access_token=r['access_token'])
 assert loaded.expired and loaded.verification_state=='unverified'
 assert service.register_price(**r).expired and counts(q)==(1,1)
 new=dict(value,expected_version=1)
 with pytest.raises(RoutingPriceStorageError) as error:prepare(system,value=new,key='price-expired-register-key',conditions=PricePreconditions(1,if_match=v.current_etag))
 assert error.value.code=='PRICE_INPUT_EXPIRED' and counts(q)==(1,1) and len(s.versions)==1
