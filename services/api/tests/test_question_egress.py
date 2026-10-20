from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
from daon_user_api.question_answering import TextModelSelection
from daon_user_api.question_answering_postgres import QuestionContext
from daon_user_api.question_answering_service import QuestionAnsweringError


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
