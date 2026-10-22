"""SQL/transaction 대역이다. 실제 PostgreSQL 권한·경합 증거가 아니다."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
import pytest
from daon_user_api.notebook_model_settings_postgres import PostgresNotebookModelSettingsService,decode_snapshot
from daon_user_api.notebook_model_settings import NotebookModelSettingsError

class Store:
    def __init__(self):
        self.rows=[];self.bindings=[];self.calls=[];self.denied=False;self.fail_binding=False
        self.candidates=[("connection-a","UPSTAGE","공용 연결","solar-pro4",3,5,"UPSTAGE",["text_generation"],"router_effort_v1")]
    @contextmanager
    def _transaction(self,context):
        old=deepcopy((self.rows,self.bindings))
        try:yield self
        except Exception:self.rows,self.bindings=old;raise
    def execute(self,sql,args=()):
        self.calls.append((sql,args));self.result=[]
        if sql.startswith("SELECT pg_advisory"):pass
        elif sql.startswith("SELECT c.connection_id"):self.result=self.candidates
        elif sql.startswith("SELECT s.record_id,s.version"):
            self.result=sorted([(*r,any(b[2]==r[3]["notebook_id"] and b[3]==b[4]==r[0] and b[5]==r[6] for b in self.bindings))
                for aggregate,r in self.rows if aggregate==args[2] and r[6]==args[3]],key=lambda r:r[1],reverse=True)
        elif sql.startswith("SELECT connection_id,model_id"):pass
        elif sql.startswith("INSERT INTO generation_settings_snapshots"):
            tenant,ws,record,aggregate,version,previous,payload,text,digest,actor,trace,now=args
            self.rows.append((aggregate,(record,version,previous,payload.obj,text,digest,actor)))
        elif sql.startswith("INSERT INTO notebook_bindings"):
            if self.fail_binding:raise RuntimeError("SYNTHETIC_BINDING_FAILURE")
            self.bindings.append(args)
        else:raise AssertionError("UNEXPECTED_TEST_SQL")
        return self
    def fetchall(self):return self.result
    def fetchone(self):return self.result[0] if self.result else None

@pytest.fixture
def setup_service(monkeypatch):
    store=Store();service=PostgresNotebookModelSettingsService(store)
    def guard(conn,context,notebook):
        if store.denied:raise NotebookModelSettingsError("NOTEBOOK_NOT_FOUND",404)
    monkeypatch.setattr(service,"_guard",guard)
    store.permissions={"local","external_api"}
    monkeypatch.setattr(PostgresNotebookModelSettingsService,"_permissions",staticmethod(lambda conn,ctx:set(store.permissions)))
    context=SimpleNamespace(tenant_id="tenant-a",workspace_id="workspace-a",actor_id="actor-a",trace_id="trace-a",policy_version="policy-a")
    return service,store,context

def save(service,context,**changes):
    args=dict(connection_id="connection-a",model_id="solar-pro4",reasoning_effort=None,expected_version=0,idempotency_key="request-first-0001")
    args.update(changes);return service.save(context,"notebook-a",**args)

def test_save_replay_chain_conflict_and_read_has_no_writes(setup_service):
    service,store,context=setup_service
    assert service.read(context,"notebook-a")["selection"] is None
    assert store.rows==[]
    assert save(service,context)["version"]==1
    assert save(service,context)["replayed"] is True
    assert len(store.rows)==len(store.bindings)==1
    with pytest.raises(NotebookModelSettingsError,match="CONFLICT"):save(service,context,reasoning_effort="low")
    with pytest.raises(NotebookModelSettingsError) as error:save(service,context,idempotency_key="request-other-0001")
    assert error.value.status==412
    assert save(service,context,expected_version=1,idempotency_key="request-second-0001",reasoning_effort="low")["version"]==2
    row=store.rows[-1][1];assert row[2]==store.rows[0][1][0]
    before=len(store.calls);view=service.read(context,"notebook-a")
    assert view["effective_selection"]["reasoning_effort"]=="low"
    assert all(not sql.startswith("INSERT") for sql,args in store.calls[before:])

def test_revoked_candidate_retains_saved_selection_but_cannot_execute_or_replay(setup_service):
    service,store,context=setup_service;save(service,context);store.candidates=[]
    view=service.read(context,"notebook-a")
    assert view["selection"]["model_id"]=="solar-pro4" and view["effective_selection"] is None
    with pytest.raises(NotebookModelSettingsError):save(service,context)
    with pytest.raises(NotebookModelSettingsError):service.require_execution(context,"notebook-a",1)
    assert len(store.rows)==1

def test_binding_failure_rolls_back_snapshot_and_denied_replay_cannot_write(setup_service):
    service,store,context=setup_service;store.fail_binding=True
    with pytest.raises(RuntimeError,match="SYNTHETIC_BINDING_FAILURE"):save(service,context)
    assert store.rows==[] and store.bindings==[]
    store.fail_binding=False;save(service,context);store.denied=True
    with pytest.raises(NotebookModelSettingsError):save(service,context)
    assert len(store.rows)==1

def test_scope_and_settings_version_are_exact(setup_service):
    service,store,context=setup_service;save(service,context)
    other=SimpleNamespace(**{**vars(context),"actor_id":"actor-b"})
    assert service.read(other,"notebook-a")["selection"] is None
    assert service.read(context,"notebook-b")["selection"] is None
    for version in (None,0,True,2):
        with pytest.raises(NotebookModelSettingsError):service.require_execution(context,"notebook-a",version)
    bound=service.require_execution(context,"notebook-a",1)
    assert bound.as_dict()["model_settings_version"]==1

def test_profile_change_requires_reselection_instead_of_reinterpreting_stored_effort(setup_service):
    service,store,context=setup_service;save(service,context,reasoning_effort="low")
    aggregate,row=store.rows[0];payload=deepcopy(row[3]);payload["profile_version"]="other-profile-v1"
    from daon_user_api.notebook_model_settings_postgres import canonical,digest
    store.rows[0]=(aggregate,(*row[:3],payload,canonical(payload),digest(payload),row[6]))
    assert service.read(context,"notebook-a")["effective_selection"] is None
    with pytest.raises(NotebookModelSettingsError):service.require_execution(context,"notebook-a",1)


def test_unselected_effort_survives_connection_contract_change_without_snapshot_rewrite(setup_service):
    service,store,context=setup_service
    store.candidates=[(*store.candidates[0][:8],"unknown")]
    save(service,context)
    before=deepcopy(store.rows)
    store.candidates=[(*store.candidates[0][:4],4,*store.candidates[0][5:8],"router_effort_v1")]
    assert service.read(context,"notebook-a")["effective_selection"] is not None
    assert service.require_execution(context,"notebook-a",1).profile_version is None
    assert save(service,context)["replayed"] is True
    assert store.rows==before


def test_contract_and_connection_version_change_invalidate_saved_effort_and_replay(setup_service):
    service,store,context=setup_service
    save(service,context,reasoning_effort="low")
    store.candidates=[(*store.candidates[0][:4],4,*store.candidates[0][5:])]
    assert service.read(context,"notebook-a")["effective_selection"] is None
    with pytest.raises(NotebookModelSettingsError,match="CHANGED"):save(service,context,reasoning_effort="low")
    with pytest.raises(NotebookModelSettingsError):service.require_execution(context,"notebook-a",1)
    store.candidates=[(*store.candidates[0][:8],"unknown")]
    assert service.read(context,"notebook-a")["available_models"][0]["reasoning"]["status"]=="unknown"
    with pytest.raises(NotebookModelSettingsError):save(service,context,reasoning_effort="low",expected_version=1,idempotency_key="new-request")
    assert save(service,context,reasoning_effort=None,expected_version=1,idempotency_key="new-request")["version"]==2
    assert service.require_execution(context,"notebook-a",2).selection.reasoning_effort is None


def test_current_execution_permission_denial_removes_candidate_and_blocks_replay(setup_service):
    service,store,context=setup_service;save(service,context)
    store.permissions=set()
    view=service.read(context,"notebook-a")
    assert view["available_models"]==[] and view["effective_selection"] is None
    with pytest.raises(NotebookModelSettingsError):save(service,context)
    with pytest.raises(NotebookModelSettingsError):service.require_execution(context,"notebook-a",1)
    assert len(store.rows)==1


def test_private_snapshot_without_exact_notebook_binding_is_not_executable(setup_service):
    service,store,context=setup_service;save(service,context);store.bindings=[]
    with pytest.raises(NotebookModelSettingsError,match="UNAVAILABLE"):service.read(context,"notebook-a")

@pytest.mark.parametrize("provider,model",[("OMNIROUTE","auto"),("OMNIROUTE","cc/claude-sonnet"),("OPENROUTER","openrouter/auto")])
def test_registered_gateway_selection_save_execution_and_permission_revocation(setup_service,provider,model):
    service,store,context=setup_service
    store.candidates=[("connection-a",provider,"명시 허용 Gateway",model,3,5,provider,["text_generation"],"router_effort_v1")]
    before=len(store.rows)
    view=service.read(context,"notebook-a")
    assert [candidate["model_id"] for candidate in view["available_models"]]==[model]
    assert len(store.rows)==before
    assert save(service,context,model_id=model)["selection"]["model_id"]==model
    execution=service.require_execution(context,"notebook-a",1)
    assert execution.selection.model_id==model and execution.version==1
    store.permissions=set()
    assert service.read(context,"notebook-a")["available_models"]==[]
    with pytest.raises(NotebookModelSettingsError):service.require_execution(context,"notebook-a",1)
    with pytest.raises(NotebookModelSettingsError):save(service,context,model_id=model)
    assert len(store.rows)==1
