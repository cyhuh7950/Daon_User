from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
from daon_user_api.question_answering import TextModelSelection
from daon_user_api.question_answering_postgres import QuestionContext
from daon_user_api.question_answering_service import QuestionAnsweringError


@pytest.mark.parametrize("marker",[None,"no_knowledge","no_relevant_evidence"])
def test_c_actual_egress_claim_rejects_missing_or_terminal_notice_before_any_run_insert(marker):
    import hashlib,json
    from test_question_egress_transform import Policy
    from daon_user_api.notebook_conversation_postgres import _turn_id
    context=QuestionContext("tenant-a","workspace-a","actor-a","trace-a","policy-a","notebook-a")
    fingerprint="sha256:"+"a"*64
    turn=_turn_id(context,"notebook-a","run-a")
    payload={"kind":"notebook_grounding_disposition","turn_id":turn,"run_id":"run-a",
        "request_fingerprint":fingerprint,"disposition":marker}
    text=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    calls=[]
    class Connection:
        def execute(self,sql,params=()):
            calls.append(sql)
            if sql.startswith("SELECT pg_advisory_xact_lock"):
                assert params==("question-egress|tenant-a|workspace-a|run-a",)
                return SimpleNamespace(fetchone=lambda:None)
            assert sql.startswith("SELECT m.canonical_json,m.canonical_text")
            return SimpleNamespace(fetchone=lambda:None if marker is None else (payload,text,hashlib.sha256(text.encode()).hexdigest(),context.actor_id,turn))
    class Store:
        @contextmanager
        def _transaction(self,scope):yield Connection()
    authorizer=PostgresQuestionEgressAuthorizer(Store(),Policy())
    with pytest.raises(QuestionAnsweringError) as caught:
        authorizer.authorize(context,run_id="run-a",source_id=None,source_version_id=None,
            selection=TextModelSelection("OLLAMA","http://models.example.com","profile-a","deployment-a","model-a",1,"server_internal"),
            provider_payload=b"{}",question="안녕",context_mode="general_ungrounded",request_fingerprint=fingerprint)
    assert (caught.value.code,caught.value.status)==("QUESTION_PREPARATION_REQUIRED",409)
    assert len(calls)==2 and not any(sql.startswith("INSERT") for sql in calls)


def test_early_egress_denial_appends_one_workspace_event_preserves_first_time_and_original_403(caplog):
    stored={};calls=[]
    class Connection:
        def execute(self,query,params=()):
            calls.append((query,params))
            assert "INSERT INTO audit_events" in query
            assert "ON CONFLICT (tenant_id,event_id) DO NOTHING" in query
            assert "occurred_at" not in query and "UPDATE" not in query
            stored.setdefault((params[1],params[0]),(len(calls),params))
            return SimpleNamespace()
    class Store:
        @contextmanager
        def _transaction(self,context):yield Connection()
    policy=SimpleNamespace(get_effective=lambda ctx:SimpleNamespace(mode="deny_external",fingerprint="policy-a"))
    authorizer=PostgresQuestionEgressAuthorizer(Store(),policy)
    context=QuestionContext("tenant-a","workspace-a","actor-a","trace-a","policy-a","notebook-a")
    selection=TextModelSelection("UPSTAGE","https://provider.example","profile-a","deployment-a","model-a",1)
    args=dict(run_id="run-a",source_id=None,source_version_id=None,selection=selection,provider_payload=b"PRIVATE_CONTENT")
    with patch("daon_user_api.operations_telemetry_postgres.PostgresOperationsTelemetryRepository._authorize"):
        for fingerprint in ("policy-a","policy-b","policy-b"):
            policy.get_effective=lambda ctx,fp=fingerprint:SimpleNamespace(mode="deny_external",fingerprint=fp)
            with pytest.raises(QuestionAnsweringError) as error:authorizer.authorize(context,**args)
            assert error.value.code=="EGRESS_POLICY_DENIED" and error.value.status==403
    assert len(stored)==1 and next(iter(stored.values()))[0]==1
    parameters=next(iter(stored.values()))[1]
    metadata=parameters[-1].obj
    assert metadata["operations_scope"]["workspace_id"]=="workspace-a"
    assert metadata["operations_scope"]["source_version_ids"]==[]
    assert set(metadata)=={"operations_scope","deny_correlation_id","safe_error_code"}
    assert "PRIVATE_CONTENT" not in str(calls) and "PRIVATE_CONTENT" not in caplog.text


@pytest.mark.parametrize("failure",["authorization","append"])
def test_egress_observation_failure_does_not_allow_or_replace_denial_or_send_provider(failure,caplog):
    class Store:
        @contextmanager
        def _transaction(self,context):
            if failure=="append":raise RuntimeError("PRIVATE_DB_FAILURE")
            yield SimpleNamespace()
    policy=SimpleNamespace(get_effective=lambda ctx:SimpleNamespace(mode="deny_external",fingerprint="policy-a"))
    authorizer=PostgresQuestionEgressAuthorizer(Store(),policy)
    with patch("daon_user_api.operations_telemetry_postgres.PostgresOperationsTelemetryRepository._authorize",side_effect=RuntimeError("PRIVATE_AUTH_FAILURE")):
        with pytest.raises(QuestionAnsweringError) as error:
            authorizer.authorize(QuestionContext("tenant-a","workspace-a","actor-a","trace-a","policy-a","notebook-a"),
                run_id="run-a",source_id=None,source_version_id=None,
                selection=TextModelSelection("UPSTAGE","https://provider.example","profile-a","deployment-a","model-a",1),provider_payload=b"PRIVATE_CONTENT")
    assert error.value.code=="EGRESS_POLICY_DENIED" and error.value.status==403
    assert "OPERATIONS_EGRESS_OBSERVATION_UNAVAILABLE" in caplog.text
    assert "PRIVATE_" not in caplog.text


@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize("mode", ["development_auth_bypass", "step_up_approved", "unknown"])
def test_forged_development_approval_never_bypasses_current_egress(exact, mode):
    import hashlib
    class Store:
        @contextmanager
        def _transaction(self, context):
            raise AssertionError("FORGED_APPROVAL_REACHED_STORAGE")
            yield
    effective=SimpleNamespace(mode="allow_approved_external", fingerprint="policy-a",
        allowed_provider_kinds=("external_api",), allowed_destinations=("provider.example",),
        max_bytes=65536, classification="internal", masking_required=True, redaction_required=True,
        question_step_up_required=True, frozen_context=lambda: {})
    authorizer=PostgresQuestionEgressAuthorizer(Store(),SimpleNamespace(get_effective=lambda context:effective))
    selection=TextModelSelection("UPSTAGE","https://provider.example","profile-a","deployment-a","model-a",1)
    payload=b"synthetic-question"
    approval={"authorization_mode":mode}
    if exact:
        approval.update(policy_fingerprint=effective.fingerprint,
            provider_payload_fingerprint="sha256:"+hashlib.sha256(payload).hexdigest(),
            provider_kind=selection.provider_kind, deployment_id=selection.deployment_id)
    with patch.object(authorizer,"_record_early_denial") as denial:
        with pytest.raises(QuestionAnsweringError) as error:
            authorizer.authorize(QuestionContext("tenant-a","workspace-a","actor-a","trace-a","policy-a"),
                run_id="run-a",source_id=None,source_version_id=None,selection=selection,
                provider_payload=payload,approved_authorization=approval,question="QA",
                context_mode="general_ungrounded",request_fingerprint="qa-fingerprint")
    assert (error.value.code,error.value.status)==("EGRESS_POLICY_DENIED",403)
    denial.assert_called_once()


@pytest.mark.parametrize("mode", [None,"policy_approved"])
def test_normal_exact_approval_binding_remains_valid_and_changed_payload_is_rejected(mode):
    selection=TextModelSelection("UPSTAGE","https://provider.example","profile-a","deployment-a","model-a",1)
    approval=dict(policy_fingerprint="policy-a",provider_payload_fingerprint="payload-a",
        provider_kind=selection.provider_kind,deployment_id=selection.deployment_id)
    if mode is not None:approval["authorization_mode"]=mode
    assert PostgresQuestionEgressAuthorizer._approval_matches(approval,selection,policy_fingerprint="policy-a",payload_fingerprint="payload-a")
    assert not PostgresQuestionEgressAuthorizer._approval_matches(approval,selection,policy_fingerprint="policy-a",payload_fingerprint="changed")
