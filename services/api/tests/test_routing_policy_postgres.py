from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from types import SimpleNamespace
import pytest
from test_routing_policy import policy_body, api


class Database:
    """SQL transaction double: not PostgreSQL constraint/RLS evidence."""
    def __init__(self):
        self.versions = {}; self.pointer = {}; self.replay = {}; self.calls = []
        self.ready = True; self.lock = RLock(); self.fail_receipt = False
        self.workspace = (1000, 'cloud_sync')
        self.candidate = (1,1,0,'public',True,'verified','ready',['text_generation'],'OLLAMA','none',False,'http://ollama:11434')
        self.personal = None

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        result = None
        if sql.startswith('SELECT pg_advisory'): pass
        elif sql.startswith('SELECT to_regclass'): result = ('workspace_routing_policy_current' if self.ready else None,)
        elif sql.startswith('SELECT v.record_id'):
            record = self.pointer.get(tuple(params)); result = self.versions.get(record)
        elif sql.startswith('SELECT request_fingerprint'): result = self.replay.get(tuple(params))
        elif sql.startswith('INSERT INTO routing_policy_versions'):
            t,w,record,aggregate,version,prior,payload,text,sha,actor,trace = params
            assert (version == 1 and prior is None) or self.versions[prior][1] == version-1
            self.versions[record] = (record, version, deepcopy(payload.obj), sha)
        elif sql.startswith('INSERT INTO workspace_routing_policy_current'): self.pointer[tuple(params[:2])] = params[2]
        elif sql.startswith('UPDATE workspace_routing_policy_current'): self.pointer[tuple(params[1:])] = params[0]
        elif sql.startswith('INSERT INTO idempotency_records'):
            assert "'completed','infinity'::timestamptz" in sql
            if self.fail_receipt: raise RuntimeError('transaction rollback test')
            self.replay[tuple(params[:5])] = (params[5], deepcopy(params[6].obj))
        elif sql.startswith('SELECT data_area'): result = (self.workspace[1],)
        elif sql.startswith('SELECT c.version'): result = self.candidate
        elif sql.startswith('SELECT credential_version FROM user_provider_credentials'): result = self.personal
        else: raise AssertionError(sql)
        return SimpleNamespace(fetchone=lambda: deepcopy(result))


class Store:
    def __init__(self): self.db = Database(); self.contexts = []
    @contextmanager
    def _transaction(self, context):
        self.contexts.append(context)
        with self.db.lock:
            before = deepcopy((self.db.versions,self.db.pointer,self.db.replay))
            try: yield self.db
            except Exception:
                self.db.versions,self.db.pointer,self.db.replay = before
                raise


@pytest.fixture
def fixture(monkeypatch):
    m = api()
    from daon_user_api import routing_policy_postgres as pg
    from daon_user_api.workspace_model_defaults import WorkspaceModelDefaultsContext
    store = Store(); ctx = WorkspaceModelDefaultsContext('tenant-a','workspace-a','actor-a','trace-a','policy-a')
    monkeypatch.setattr(pg,'_authorize',lambda *args: SimpleNamespace(role='editor', effective_permissions=()))
    monkeypatch.setattr(pg, '_CurrentAuthorizationRules', lambda: SimpleNamespace(
        _effective_permission=lambda *args,**kwargs: SimpleNamespace(effective=True)))
    metadata = pg.PostgresRoutingMetadata(store)
    repo = pg.PostgresRoutingPolicyRepository(store)
    return SimpleNamespace(m=m,pg=pg,store=store,ctx=ctx,metadata=metadata,repo=repo)


def test_existing_cost_limit_is_not_an_authorized_currency_budget(fixture):
    f = fixture
    body = f.m.RoutingPolicyBody.model_validate(policy_body())
    with pytest.raises(f.m.RoutingPolicyError) as e:
        f.metadata.candidates(f.ctx, body)
    assert (e.value.code,e.value.status) == ('ROUTING_BUDGET_UNVERIFIED',503)

def validation_noop(*args, **kwargs): return ()


def save(f, body, key='key-a', etag=None, validate=validation_noop):
    obj=f.m.RoutingPolicyBody.model_validate(body)
    return f.repo.put(f.ctx,obj,if_match=etag,if_none_match='*' if obj.expected_version==0 else None,
        idempotency_key=key,validate=validate)


def test_disable_is_available_after_candidate_and_budget_revocation(fixture):
    f=fixture; body=policy_body(); first,_=save(f,body)
    def revoked(*args,**kwargs): raise f.m.RoutingPolicyError('ROUTING_CANDIDATE_CHANGED',409)
    disabled={**body,'expected_version':1,'enabled':False}
    result,replayed=save(f,disabled,'disable',first['etag'],revoked)
    assert result['version']==2 and result['body']['enabled'] is False and replayed is False
    assert len(f.store.db.versions)==2
    assert save(f,disabled,'disable',first['etag'],revoked)==(result,True)


def test_disable_may_not_hide_candidate_replacement(fixture):
    f=fixture; body=policy_body(); first,_=save(f,body)
    def revoked(*args,**kwargs): raise f.m.RoutingPolicyError('ROUTING_CANDIDATE_CHANGED',409)
    disabled={**body,'expected_version':1,'enabled':False,
        'candidates':[{**body['candidates'][0],'model_id':'hidden-model'}]}
    with pytest.raises(f.m.RoutingPolicyError): save(f,disabled,'disable-hidden',first['etag'],revoked)
    assert len(f.store.db.versions)==1

def test_repository_create_replay_conflict_and_atomic_rollback(fixture):
    f=fixture; body=policy_body(); first,replay=save(f,body)
    assert first['version']==1 and replay is False
    assert save(f,body)==(first,True)
    with pytest.raises(f.m.RoutingPolicyError) as e: save(f,{**body,'enabled':False})
    assert e.value.code=='IDEMPOTENCY_KEY_REUSED'
    f.store.db.fail_receipt=True
    with pytest.raises(RuntimeError): save(f,{**body,'expected_version':1},'rollback',first['etag'])
    assert len(f.store.db.versions)==1 and f.repo.get(f.ctx)==first


def test_repository_update_different_keys_compete_and_history_remains_immutable(fixture):
    from concurrent.futures import ThreadPoolExecutor
    f=fixture; body=policy_body(); first,_=save(f,body)
    historical=deepcopy(f.store.db.versions)
    update={**body,'expected_version':1}
    def concurrent(key):
        try: return save(f,update,key,first['etag'])[0]['version']
        except f.m.RoutingPolicyError as e: return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(concurrent,['update-a','update-b']))
    assert sorted(map(str,results))==['2','VERSION_CONFLICT']
    assert len(f.store.db.versions)==2
    assert all(f.store.db.versions[k]==v for k,v in historical.items())
    assert all(not sql.startswith('UPDATE routing_policy_versions') for sql,_ in f.store.db.calls)


def test_policy_scope_and_revoked_manager_are_rechecked_even_on_replay(fixture,monkeypatch):
    from dataclasses import replace
    f=fixture; body=policy_body(); first,_=save(f,body)
    assert f.repo.get(replace(f.ctx,workspace_id='other-workspace'))['version']==0
    assert f.repo.get(replace(f.ctx,tenant_id='other-tenant'))['version']==0
    def revoked(*args): raise f.m.RoutingPolicyError('ROUTING_POLICY_ACCESS_DENIED',403)
    monkeypatch.setattr(f.pg,'_authorize',revoked)
    with pytest.raises(f.m.RoutingPolicyError) as e: save(f,body)
    assert e.value.status==403 and len(f.store.db.versions)==1


def test_absent_schema_is_safe_unavailable_and_pinned_does_not_touch_it(fixture):
    f=fixture; f.store.db.ready=False
    with pytest.raises(f.m.RoutingPolicyError) as e: f.repo.get(f.ctx)
    assert (e.value.code,e.value.status)==('ROUTING_POLICY_UNAVAILABLE',503)
    calls=len(f.store.db.calls)
    service=f.m.RoutingPolicyService(f.repo,f.metadata)
    with pytest.raises(f.m.RoutingPolicyError) as e:
        service.prepare(f.ctx,None,policy_version_id=None,routing_mode='pinned')
    assert e.value.code=='ROUTING_PINNED_UNCHANGED' and len(f.store.db.calls)==calls


@pytest.mark.parametrize('change,code',[
    ({'credential_source':'personal'},'ROUTING_CREDENTIAL_DENIED'),
    ({'connection_version':2},'ROUTING_CANDIDATE_CHANGED'),
    ({'endpoint_version':2},'ROUTING_CANDIDATE_CHANGED'),
    ({'catalog_version':2},'ROUTING_CANDIDATE_CHANGED'),
    ({'credential_version':2},'ROUTING_CANDIDATE_CHANGED')])
def test_actual_metadata_rejects_credential_source_or_version_mismatch(fixture,change,code):
    f=fixture; body=policy_body();body['enabled']=False
    body['candidates'][0].update(change)
    with pytest.raises(f.m.RoutingPolicyError) as e:
        f.metadata.candidates(f.ctx,f.m.RoutingPolicyBody.model_validate(body))
    assert e.value.code==code


def test_personal_credential_query_is_bound_to_current_actor_and_never_decrypts(fixture):
    f=fixture; body=policy_body();body['enabled']=False
    body['candidates'][0].update(credential_source='personal',credential_version=7)
    row=list(f.store.db.candidate);row[3]='personal';f.store.db.candidate=tuple(row);f.store.db.personal=(7,)
    candidates=f.metadata.candidates(f.ctx,f.m.RoutingPolicyBody.model_validate(body))
    assert candidates[0].credential_owner=='actor-a'
    personal_query=next((sql,params) for sql,params in f.store.db.calls if sql.startswith('SELECT credential_version'))
    from daon_user_api.provider_credentials import PERSONAL_CREDENTIAL_SCHEMA_VERSION
    assert personal_query[1]==('tenant-a','actor-a','connection-a',PERSONAL_CREDENTIAL_SCHEMA_VERSION)
    assert 'decrypt' not in personal_query[0]
    f.store.db.personal=None
    with pytest.raises(f.m.RoutingPolicyError) as e:
        f.metadata.candidates(f.ctx,f.m.RoutingPolicyBody.model_validate(body))
    assert e.value.code=='ROUTING_CREDENTIAL_DENIED'


def test_production_disabled_draft_checks_source_but_does_not_invent_observations(fixture):
    f=fixture; body=policy_body();body['enabled']=False
    service=f.m.RoutingPolicyService(f.repo,f.metadata)
    result,replay=service.put(f.ctx,body,if_none_match='*',idempotency_key='draft')
    assert result['readiness']=='disabled' and result['execution_enabled'] is False
    assert f.metadata.observations(f.ctx,None,())==()
    assert f.store.db.versions

def test_existing_pointer_uses_update_not_insert_trigger_on_conflict(fixture):
    f=fixture; body=policy_body(); first,_=save(f,body)
    f.store.db.calls.clear()
    save(f,{**body,'expected_version':1},'new-key',first['etag'])
    assert any(sql.startswith('UPDATE workspace_routing_policy_current') for sql,_ in f.store.db.calls)
    assert not any(sql.startswith('INSERT INTO workspace_routing_policy_current') for sql,_ in f.store.db.calls)
