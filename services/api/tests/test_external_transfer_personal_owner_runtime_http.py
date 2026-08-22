from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import ClientKind, IdentityPrincipal
from daon_user_api.notebook import NotebookError
from daon_user_api.question_answering_postgres import StoredQuestionAnswer
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app
from test_identity_support import POLICY_VERSION, create_service


class NotebookBoundary:
    def __init__(self) -> None:
        self.calls = 0
        self.error: NotebookError | None = None

    def require_selected_bindings(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.error is not None:
            raise self.error
        return None


class QuestionBoundary:
    def __init__(self, provider_kind: str = "external_api") -> None:
        self.provider_calls = 0
        self.prepare_calls = 0
        self.provider_kind = provider_kind
        self.replay_answer: StoredQuestionAnswer | None = None

    def replay(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return self.replay_answer

    def prepare_authorization(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        self.prepare_calls += 1
        return SimpleNamespace(
            selection=SimpleNamespace(
                provider_kind=self.provider_kind, deployment_id="deployment-upstage",
                provider_code="UPSTAGE", base_url="https://api.upstage.ai/v1",
            ),
            provider_payload=b'{"question":"masked"}',
        )

    def ask(self, _context, **kwargs):  # type: ignore[no-untyped-def]
        self.provider_calls += 1
        authorization = kwargs["approved_authorization"]
        if self.provider_kind == "external_api":
            assert authorization is not None
            assert authorization["provider_kind"] == "external_api"
            assert authorization["deployment_id"] == "deployment-upstage"
        else:
            assert authorization is None
        return SimpleNamespace(
            run_id=kwargs["run_id"], run_result_id="run-result-session-authorized",
            answer="안녕하세요.", insufficient=False, citations=(),
        )


class EffectivePolicyBoundary:
    def __init__(
        self, mode: str = "allow_approved_external", *,
        allowed_provider_kinds: tuple[str, ...] = ("external_api",),
        allowed_destinations: tuple[str, ...] = ("api.upstage.ai",),
        classification: str = "internal", max_bytes: int = 1_048_576,
        masking_required: bool = True, redaction_required: bool = True,
    ) -> None:
        self.mode = mode
        self.allowed_provider_kinds = allowed_provider_kinds
        self.allowed_destinations = allowed_destinations
        self.classification = classification
        self.max_bytes = max_bytes
        self.masking_required = masking_required
        self.redaction_required = redaction_required
        self.calls = 0

    def get_effective(self, _context):  # type: ignore[no-untyped-def]
        self.calls += 1
        return SimpleNamespace(
            mode=self.mode, required_approver="organization_admin",
            fingerprint="sha256:" + "a" * 64,
            allowed_provider_kinds=self.allowed_provider_kinds,
            allowed_destinations=self.allowed_destinations,
            classification=self.classification, max_bytes=self.max_bytes,
            masking_required=self.masking_required,
            redaction_required=self.redaction_required,
        )


def _bootstrap_dependencies(
    db_path: Path, *, tenant_id: str, workspace_id: str,
    owner_user_id: str, owner_role: Role, workspace_kind: str,
    policy_mode: str = "allow_approved_external", provider_kind: str = "external_api",
):
    audit = AuditEventStore()
    identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
    authorization_repository = SqliteAuthorizationRepository(db_path)
    authorization_repository.bootstrap_workspace(
        tenant_id=tenant_id, workspace_id=workspace_id,
        owner_user_id=owner_user_id, owner_role=owner_role,
        workspace_kind=workspace_kind, data_area="cloud_sync",
        cost_limit_cents=1000, now=clock(),
    )
    question = QuestionBoundary(provider_kind)
    dependencies = RuntimeDependencies(
        settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
        identity_service=identity,
        authorization_service=AuthorizationService(
            repository=authorization_repository, audit_store=audit, clock=clock,
            identity_service=identity,
        ),
        audit_store=audit, identity_repository=identity_repository,
        authorization_repository=authorization_repository,
        notebook_service=NotebookBoundary(),  # type: ignore[arg-type]
        question_answering_service=question,
        egress_policy_service=EffectivePolicyBoundary(policy_mode),  # type: ignore[arg-type]
    )
    return dependencies, identity, authorization_repository, clock, question


def _question_payload(*, grounded: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "notebook_id": "notebook-session-auth",
        "question": "선택 Source를 근거로 답해줘" if grounded else "안녕하세요",
    }
    if grounded:
        payload.update({"source_id": "source-1", "source_version_id": "version-1"})
    return payload


def _access(principal: IdentityPrincipal) -> SimpleNamespace:
    return SimpleNamespace(client_kind=ClientKind.WEB, principal=principal)


def _replay_answer(provider_kind: str) -> StoredQuestionAnswer:
    return StoredQuestionAnswer(
        "run-replay", "run-result-replay", "저장된 답변", False, (),
        provider_kind=provider_kind,
        egress_scope={
            "classification": "internal", "destination": "api.upstage.ai",
            "masking_required": True, "max_bytes": 128,
            "redaction_required": True,
        } if provider_kind == "external_api" else None,
    )


def test_personal_owner_personal_workspace_asks_without_question_step_up() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, _, _, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-personal", workspace_id="workspace-personal",
            owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal",
        )
        principal = IdentityPrincipal(
            "personal-owner", "session-personal", "device-personal", "tenant-personal",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)), patch.object(
            identity, "issue_step_up_after_reauthentication",
        ) as issue_step_up, patch.object(identity, "consume_step_up") as consume_step_up:
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-personal/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "personal-question-00001"},
                    json=_question_payload(),
                )
        assert response.status_code == 200, response.text
        assert question.provider_calls == 1
        assert issue_step_up.call_count == consume_step_up.call_count == 0
        dependencies.close()


def test_organization_admin_organization_workspace_grounded_ask_remains_allowed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, _, _, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-organization", workspace_id="workspace-organization",
            owner_user_id="organization-admin", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization",
        )
        principal = IdentityPrincipal(
            "organization-admin", "session-org", "device-org", "tenant-organization",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)), patch.object(
            identity, "consume_step_up",
        ) as consume_step_up:
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-organization/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "organization-question-00001"},
                    json=_question_payload(grounded=True),
                )
        assert response.status_code == 200, response.text
        assert question.provider_calls == 1
        assert consume_step_up.call_count == 0
        dependencies.close()


def test_unauthenticated_question_denies_before_provider_or_write() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, _, _, _, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-personal", workspace_id="workspace-personal",
            owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal",
        )
        with TestClient(create_app(dependencies)) as client:
            response = client.post(
                "/api/v1/workspaces/workspace-personal/questions",
                headers={"Idempotency-Key": "unauth-question-00001"},
                json=_question_payload(),
            )
        assert response.status_code == 401
        assert question.prepare_calls == question.provider_calls == 0
        dependencies.close()


def test_denied_effective_policy_denies_before_provider_or_write() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, _, _, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-personal", workspace_id="workspace-personal",
            owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal", policy_mode="deny_external",
        )
        principal = IdentityPrincipal(
            "personal-owner", "session-personal", "device-personal", "tenant-personal",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)), patch.object(
            identity, "consume_step_up",
        ) as consume_step_up:
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-personal/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "denied-policy-question-00001"},
                    json=_question_payload(),
                )
        assert (response.status_code, response.json()["error"]["code"]) == (403, "EGRESS_POLICY_DENIED")
        assert question.provider_calls == 0
        assert consume_step_up.call_count == 0
        dependencies.close()


def test_missing_external_llm_permission_denies_before_policy_or_provider() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, repository, clock, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-organization", workspace_id="workspace-organization",
            owner_user_id="organization-admin", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization",
        )
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_memberships "
                "(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?, 'active',1,?)",
                (
                    "tenant-organization", "workspace-organization", "workspace-admin",
                    Role.WORKSPACE_ADMIN.value, clock().isoformat(),
                ),
            )
        principal = IdentityPrincipal(
            "workspace-admin", "session-admin", "device-admin", "tenant-organization",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)), patch.object(
            identity, "consume_step_up",
        ) as consume_step_up:
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-organization/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "missing-permission-00001"},
                    json=_question_payload(),
                )
        assert response.status_code == 403
        assert question.prepare_calls == 1
        assert question.provider_calls == 0
        assert consume_step_up.call_count == 0
        dependencies.close()


def test_local_runtime_question_does_not_require_external_llm_permission() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, repository, clock, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-organization", workspace_id="workspace-organization",
            owner_user_id="organization-admin", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", provider_kind="local_runtime",
        )
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_memberships "
                "(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?, 'active',1,?)",
                (
                    "tenant-organization", "workspace-organization", "workspace-admin",
                    Role.WORKSPACE_ADMIN.value, clock().isoformat(),
                ),
            )
        principal = IdentityPrincipal(
            "workspace-admin", "session-admin", "device-admin", "tenant-organization",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)):
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-organization/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "local-runtime-question-0001"},
                    json=_question_payload(),
                )
        assert response.status_code == 200, response.text
        assert question.prepare_calls == question.provider_calls == 1
        dependencies.close()


def test_external_completed_replay_revalidates_binding_permission_and_policy_without_domain_write() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, _, _, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-personal", workspace_id="workspace-personal",
            owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal",
        )
        question.replay_answer = _replay_answer("external_api")
        principal = IdentityPrincipal(
            "personal-owner", "session-personal", "device-personal", "tenant-personal",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)):
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-personal/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "external-replay-valid-01"},
                    json=_question_payload(),
                )
        assert response.status_code == 200, response.text
        assert question.prepare_calls == question.provider_calls == 0
        assert dependencies.notebook_service.calls == 1  # type: ignore[attr-defined]
        assert dependencies.egress_policy_service.calls == 1  # type: ignore[attr-defined]
        dependencies.close()


def test_external_completed_replay_denies_when_current_policy_is_revoked() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, _, _, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-personal", workspace_id="workspace-personal",
            owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal", policy_mode="deny_external",
        )
        question.replay_answer = _replay_answer("external_api")
        principal = IdentityPrincipal(
            "personal-owner", "session-personal", "device-personal", "tenant-personal",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)):
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-personal/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "external-replay-policy-01"},
                    json=_question_payload(),
                )
        assert (response.status_code, response.json()["error"]["code"]) == (403, "EGRESS_POLICY_DENIED")
        assert question.prepare_calls == question.provider_calls == 0
        dependencies.close()


def test_external_completed_replay_denies_after_external_permission_revoke() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, repository, clock, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-organization", workspace_id="workspace-organization",
            owner_user_id="organization-admin", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization",
        )
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_memberships "
                "(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?, 'active',1,?)",
                (
                    "tenant-organization", "workspace-organization", "workspace-admin",
                    Role.WORKSPACE_ADMIN.value, clock().isoformat(),
                ),
            )
        question.replay_answer = _replay_answer("external_api")
        principal = IdentityPrincipal(
            "workspace-admin", "session-admin", "device-admin", "tenant-organization",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)):
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-organization/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "external-replay-permission-01"},
                    json=_question_payload(),
                )
        assert response.status_code == 403
        assert question.prepare_calls == question.provider_calls == 0
        assert dependencies.egress_policy_service.calls == 0  # type: ignore[attr-defined]
        dependencies.close()


def test_completed_replay_denies_when_current_notebook_binding_is_removed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, _, _, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-personal", workspace_id="workspace-personal",
            owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal",
        )
        question.replay_answer = _replay_answer("external_api")
        dependencies.notebook_service.error = NotebookError("NOTEBOOK_NOT_FOUND", 404)  # type: ignore[attr-defined]
        principal = IdentityPrincipal(
            "personal-owner", "session-personal", "device-personal", "tenant-personal",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)):
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-personal/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "external-replay-binding-01"},
                    json=_question_payload(),
                )
        assert response.status_code == 404
        assert question.prepare_calls == question.provider_calls == 0
        assert dependencies.egress_policy_service.calls == 0  # type: ignore[attr-defined]
        dependencies.close()


def test_local_completed_replay_requires_view_only_and_ignores_external_policy() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, repository, clock, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-organization", workspace_id="workspace-organization",
            owner_user_id="organization-admin", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", policy_mode="deny_external",
            provider_kind="local_runtime",
        )
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_memberships "
                "(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?, 'active',1,?)",
                (
                    "tenant-organization", "workspace-organization", "workspace-admin",
                    Role.WORKSPACE_ADMIN.value, clock().isoformat(),
                ),
            )
        question.replay_answer = _replay_answer("local_runtime")
        principal = IdentityPrincipal(
            "workspace-admin", "session-admin", "device-admin", "tenant-organization",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)):
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-organization/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "local-replay-view-only-01"},
                    json=_question_payload(),
                )
        assert response.status_code == 200, response.text
        assert question.prepare_calls == question.provider_calls == 0
        assert dependencies.egress_policy_service.calls == 0  # type: ignore[attr-defined]
        dependencies.close()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("allowed_provider_kinds", ("server_internal",)),
        ("allowed_destinations", ("api.groq.com",)),
        ("classification", "confidential"),
        ("max_bytes", 1),
        ("masking_required", False),
        ("redaction_required", False),
    ),
)
def test_external_question_exact_policy_mismatch_denies_before_domain_write(
    field: str, value: object,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, _, _, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-personal", workspace_id="workspace-personal",
            owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal",
        )
        setattr(dependencies.egress_policy_service, field, value)
        principal = IdentityPrincipal(
            "personal-owner", "session-personal", "device-personal", "tenant-personal",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)):
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-personal/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": f"policy-vector-{field}-01"},
                    json=_question_payload(),
                )
        assert (response.status_code, response.json()["error"]["code"]) == (403, "EGRESS_POLICY_DENIED")
        assert question.provider_calls == 0
        dependencies.close()


def test_cross_tenant_notebook_question_denies_before_provider_or_write() -> None:
    with tempfile.TemporaryDirectory() as directory:
        dependencies, identity, repository, clock, question = _bootstrap_dependencies(
            Path(directory) / "runtime.sqlite3",
            tenant_id="tenant-personal", workspace_id="workspace-personal",
            owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal",
        )
        repository.bootstrap_workspace(
            tenant_id="tenant-foreign", workspace_id="workspace-organization",
            owner_user_id="foreign-admin", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=clock(),
        )
        principal = IdentityPrincipal(
            "personal-owner", "session-personal", "device-personal", "tenant-personal",
        )
        with patch.object(identity, "describe_access", return_value=_access(principal)), patch.object(
            identity, "consume_step_up",
        ) as consume_step_up:
            with TestClient(create_app(dependencies)) as client:
                response = client.post(
                    "/api/v1/workspaces/workspace-organization/questions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"Idempotency-Key": "cross-tenant-question-00001"},
                    json=_question_payload(),
                )
        assert response.status_code == 404
        assert question.prepare_calls == question.provider_calls == 0
        assert consume_step_up.call_count == 0
        dependencies.close()
