from types import SimpleNamespace
import pytest
from test_question_answering_service import (FakeRepository,FakeIndex,FakeCredential,FakeEgress,
    StaticWorkspaceModelResolver)
from daon_user_api.question_answering_service import QuestionAnsweringService,QuestionAnsweringError
from daon_user_api.question_answering_postgres import QuestionContext


def test_explicit_auto_does_not_fall_through_to_default_model_when_backend_absent():
    class NoCalls:
        def __getattr__(self,name): raise AssertionError('default model/transport must not be used')
    service=QuestionAnsweringService(NoCalls(),FakeRepository(),FakeIndex(()),FakeCredential(),NoCalls(),FakeEgress())
    ctx=QuestionContext('tenant-cp3','workspace-cp3','actor-cp3','trace','policy')
    with pytest.raises(QuestionAnsweringError) as error:
        service.prepare_authorization(ctx,source_id=None,source_version_id=None,question='qa',
            routing_mode='auto',routing_policy_version_id='policy-v1')
    assert (error.value.code,error.value.status)==('ROUTING_EXECUTION_UNAVAILABLE',503)


def execution_api():
    import importlib.util
    assert importlib.util.find_spec('daon_user_api.routing_execution') is not None, 'B2 execution contract is not implemented'
    from daon_user_api import routing_execution as m
    return m


def test_authorization_plan_is_not_a_b1_plan_or_a_single_model_token():
    m=execution_api()
    with pytest.raises(m.RoutingExecutionError):
        m.FrozenAuthorizationPlan('{"execution_enabled":true}', 'a'*64)


def test_no_current_source_proof_means_no_permit_and_no_dispatch():
    m=execution_api()
    service=m.RoutingExecutionService(repository=None, candidate_port=None)
    with pytest.raises(m.RoutingExecutionError) as e:
        service.prepare(None,None,None,None)
    assert e.value.code=='ROUTING_EXECUTION_UNAVAILABLE'

@pytest.mark.parametrize('field,value',[
    ('endpoint_digest',None),('egress_policy_digest',''),('provider_kind','unverified'),
    ('protocol_version',''),('required_approver','anyone')])
def test_unverified_candidate_proof_is_never_accepted(field,value):
    from test_routing_attempts_postgres import CandidatePort
    from test_routing_policy import plan_fixture
    m=execution_api();_,ctx,_,policy,c,o,now=plan_fixture()
    claims=CandidatePort({},now).freeze(ctx,{'candidate':c.references()})
    claims[field]=value
    with pytest.raises(m.RoutingExecutionError):m.validate_quote(claims,now)



def test_auto_candidate_resolver_does_not_consult_or_change_workspace_default():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from daon_user_api.workspace_model_defaults import PostgresWorkspaceModelResolver,WorkspaceModelContext
    from daon_user_api.routing_policy import RoutingCandidateBody
    calls=[]
    row=(3,'connection-a','installed-a','OLLAMA','http://ollama:11434',None,None,None,None,0,
        True,'verified',['text_generation'],'ready',4,'public','none','OLLAMA')
    class Store:
        @contextmanager
        def _transaction(self,ctx):
            class Conn:
                def execute(self,sql,params):
                    calls.append((sql,params));return SimpleNamespace(fetchone=lambda:row)
            yield Conn()
    resolver=PostgresWorkspaceModelResolver(Store(),None,None)
    candidate=RoutingCandidateBody(connection_id='connection-a',model_id='installed-a',credential_source='workspace',
        connection_version=3,catalog_version=4,credential_version=0,endpoint_version=3)
    with resolver.resolve_candidate(WorkspaceModelContext('tenant-a','workspace-a','actor-a'),candidate) as selected:
        assert selected.connection_id=='connection-a' and selected.model_id=='installed-a'
    assert calls and all('workspace_model_defaults' not in sql for sql,_ in calls)
    assert calls[0][1]==('connection-a','installed-a')


@pytest.mark.parametrize('extra',[
    {'routing_mode':'auto','routing_policy_version_id':'policy-v1'},
    {'routing_mode':'auto','routing_policy_version_id':'policy-v1','routing_plan_id':'plan-v1','routing_plan_authorization':'permit','step_up_authorization_id':'legacy'},
    {'routing_plan_id':'plan-v1'},
    {'routing_mode':'pinned','routing_plan_authorization':'permit'},
])
def test_question_dto_rejects_incomplete_auto_and_pinned_plan_inputs(extra):
    from pydantic import ValidationError
    from daon_user_api.runtime import QuestionBody
    with pytest.raises(ValidationError):
        QuestionBody(notebook_id='notebook-a',question='qa',**extra)


@pytest.mark.parametrize('name,extra',[
 ('QuestionBody',{'routing_mode':None}),('QuestionBody',{'routing_plan_id':None}),
 ('QuestionBody',{'routing_plan_authorization':None}),('QuestionAuthorizationBody',{'routing_policy_version_id':None}),
 ('QuestionBody',{'routing_mode':'auto','routing_policy_version_id':'policy-v1','routing_plan_id':'plan-v1','routing_plan_authorization':'permit','step_up_authorization_id':None}),
])
def test_new_routing_fields_reject_explicit_null_and_auto_legacy_key(name,extra):
    from pydantic import ValidationError
    from daon_user_api import runtime
    with pytest.raises(ValidationError):getattr(runtime,name)(notebook_id='notebook-a',question='qa',**extra)
