from dataclasses import replace
import pytest
from daon_user_api.notebook_model_settings import (NotebookModelSelection, NotebookModelSettingsError, reasoning_profile, validate_effort)


def test_exact_supported_reasoning_and_unknown_relay():
    profile=reasoning_profile("UPSTAGE","UPSTAGE","solar-pro4")
    assert profile["status"]=="supported"
    assert [v["value"] for v in profile["values"]]==["none","low","medium","high"]
    assert reasoning_profile("OMNIROUTE","OMNIROUTE","solar-pro4")["status"]=="unknown"
    assert reasoning_profile("UPSTAGE","UPSTAGE","other")["values"]==[]


@pytest.mark.parametrize("effort", ["minimal","xhigh","max",False,{},[]])
def test_unsupported_effort_is_rejected_before_dispatch(effort):
    with pytest.raises(NotebookModelSettingsError,match="NOTEBOOK_MODEL_SETTINGS_INVALID"):
        validate_effort("UPSTAGE","UPSTAGE","solar-pro4",effort)


def test_selection_is_strict_and_revalidated_on_replace():
    value=NotebookModelSelection("connection-a","org/model",None)
    assert value.as_dict()=={"connection_id":"connection-a","model_id":"org/model","reasoning_effort":None}
    with pytest.raises(NotebookModelSettingsError): replace(value,connection_id="https://unsafe")
    with pytest.raises(NotebookModelSettingsError): replace(value,model_id="has whitespace")


def test_candidate_filter_matches_actual_text_adapter_registry():
    from daon_user_api.notebook_model_settings import project_candidate
    assert project_candidate(("connection-a","OPENAI","Name","model",1,1,"OPENAI",["text_generation"])) is None
    assert project_candidate(("connection-a","UPSTAGE","Name","solar-pro4",1,2,"UPSTAGE",["document_parsing"])) is None
    assert project_candidate(("connection-a","CUSTOM","Name","model",1,2,"asr_base_native",["text_generation"])) is None
    value=project_candidate(("connection-a","UPSTAGE","Name","solar-pro4",1,2,"UPSTAGE",["text_generation"]))
    assert set(value)=={"connection_id","provider_code","display_name","model_id","connection_version","catalog_version","reasoning"}


def test_repository_exact_canonical_scope_rejects_corruption():
    from daon_user_api.notebook_model_settings_postgres import decode_snapshot,scope_aggregate
    from types import SimpleNamespace
    context=SimpleNamespace(tenant_id="tenant-a",workspace_id="workspace-a",actor_id="actor-a")
    assert scope_aggregate(context,"notebook-a")!=scope_aggregate(context,"notebook-b")
    with pytest.raises(NotebookModelSettingsError,match="NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE"):
        decode_snapshot(context,"notebook-a",("record-a",1,None,{},"{}","0"*64,"actor-b"))


@pytest.mark.parametrize("provider,model",[("OMNIROUTE","openrouter/auto"),("OPENROUTER","auto"),("CUSTOM","auto"),("CUSTOM","openrouter/auto"),("UPSTAGE","auto")])
def test_registered_current_model_ids_are_not_reinterpreted_by_name(provider,model):
    from daon_user_api.notebook_model_settings import project_candidate
    # 입력 row는 repository의 현재 등록/허용/권한 검사를 이미 통과한 자료다.
    value=project_candidate(("connection-a",provider,"설정에 등록된 모델",model,1,1,provider,["text_generation"]))
    assert value is not None and value["model_id"]==model
    assert project_candidate(("connection-a",provider,"음성 전용",model,1,1,provider,["speech_to_text"])) is None


def test_exact_resolver_reuses_current_credentials_without_reading_workspace_default():
    from test_workspace_model_defaults import Database,Cursor,row,context
    from contextlib import contextmanager
    from daon_user_api.provider_credentials import ProviderCredentialCipher
    from daon_user_api.workspace_model_defaults import PostgresWorkspaceModelResolver
    from daon_user_api.notebook_model_settings import NotebookModelExecution
    from daon_user_api.question_answering_service import QuestionAdapterRegistry
    cipher=ProviderCredentialCipher(b"r"*32,encryption_key_version=1)
    database=Database(row(cipher,provider_code="UPSTAGE",model_id="solar-pro4",base_url="https://api.upstage.ai/v1",adapter_type="UPSTAGE"))
    class ExactStore:
        @contextmanager
        def _transaction(self,scope): yield self
        def execute(self,sql,args):
            assert "FROM system_provider_connections" in sql
            database.queries.append((sql,args))
            return Cursor(database.row)
    resolver=PostgresWorkspaceModelResolver(ExactStore(),cipher)
    binding=NotebookModelExecution(NotebookModelSelection("eoul-primary","solar-pro4","low"),7,"upstage-solar-pro4-safe-wire-v1")
    with resolver.resolve(context(),"text_generation",selected=binding) as selected:
        prepared=QuestionAdapterRegistry().prepare_general(selected,"안녕하세요","trace-a",None)
        assert prepared.provider_payload["reasoning_effort"]=="low"
        assert selected.model_settings_version==7 and selected.binding_version==3
        assert selected.model_id=="solar-pro4"
    sql,args=database.queries[0]
    assert "workspace_model_defaults" not in sql and args==("eoul-primary","solar-pro4")
    assert selected._released is True


@pytest.mark.parametrize("effort,profile",[("max","upstage-solar-pro4-safe-wire-v1"),("low","wrong-profile")])
def test_invalid_profile_does_not_create_decrypted_credential(monkeypatch,effort,profile):
    from test_workspace_model_defaults import Database,Cursor,row,context
    from contextlib import contextmanager
    from daon_user_api.provider_credentials import ProviderCredentialCipher
    from daon_user_api.workspace_model_defaults import PostgresWorkspaceModelResolver,WorkspaceModelUnavailable
    from daon_user_api.notebook_model_settings import NotebookModelExecution
    cipher=ProviderCredentialCipher(b"r"*32,encryption_key_version=1)
    metadata=row(cipher,provider_code="UPSTAGE",model_id="solar-pro4",base_url="https://api.upstage.ai/v1",adapter_type="UPSTAGE")
    calls=[]
    def decrypt(*args):calls.append(True);raise AssertionError("CREDENTIAL_READ_FORBIDDEN")
    monkeypatch.setattr(cipher,"decrypt",decrypt)
    class MetadataStore:
        @contextmanager
        def _transaction(self,scope):yield self
        def execute(self,*args):return Cursor(metadata)
    binding=NotebookModelExecution(NotebookModelSelection("eoul-primary","solar-pro4",effort),1,profile)
    with pytest.raises((NotebookModelSettingsError,WorkspaceModelUnavailable)):
        with PostgresWorkspaceModelResolver(MetadataStore(),cipher).resolve(context(),"text_generation",selected=binding):pass
    assert calls==[]


@pytest.mark.parametrize("provider,model",[("OMNIROUTE","auto"),("OPENROUTER","openrouter/auto")])
def test_registered_gateway_auto_remains_an_explicit_text_candidate(provider,model):
    from daon_user_api.notebook_model_settings import project_candidate
    value=project_candidate(("connection-gateway",provider,"명시 허용 Gateway",model,3,5,provider,["text_generation"]))
    assert value is not None
    assert value["model_id"]==model and value["connection_id"]=="connection-gateway"
    assert value["reasoning"]["status"]=="unknown" and value["reasoning"]["values"]==[]
    assert project_candidate(("connection-gateway",provider,"음성 전용",model,3,5,provider,["speech_to_text"])) is None
