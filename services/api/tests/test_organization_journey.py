"""J3: 공통 SQLite 연결의 실제 승인·권한·세션 소비 경계. 외부 OIDC는 시험 대역이다."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import Action, AuthorizationError, AuthorizationService, Permission, Role, SqliteAuthorizationRepository
from daon_user_api.identity import IdentityError, IdentityPrincipal
from daon_user_api.organization_membership import SqliteOrganizationRepository
from test_identity_support import MutableClock, POLICY_VERSION, TRACE_ID, create_service, native_login


@pytest.fixture
def journey(tmp_path):
    path = tmp_path / "j3-common.sqlite"
    now = datetime.now(timezone.utc)
    identity, identities, audit, clock = create_service(path, clock=MutableClock(now))
    original = native_login(identity)
    auth = SqliteAuthorizationRepository(path)
    workflow = SqliteOrganizationRepository(path)
    auth.bootstrap_workspace(tenant_id="tenant-001", workspace_id="personal-workspace", owner_user_id=original.user_id,
        owner_role=Role.PERSONAL_OWNER, workspace_kind="personal", data_area="cloud_sync", cost_limit_cents=100, now=now)
    auth.bootstrap_workspace(tenant_id="j3-org", workspace_id="org-workspace", owner_user_id="admin",
        owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=100, now=now)
    with identities.transaction() as connection:
        connection.execute("UPDATE memberships SET role='personal_owner' WHERE user_id=? AND tenant_id='tenant-001'", (original.user_id,))
        connection.execute("INSERT INTO tenants VALUES ('j3-org')")
    invitation = workflow.create_invitation(tenant_id="j3-org", created_by="admin", code="j3-synthetic-invitation",
        workspace_id="org-workspace", expires_at=now+timedelta(days=1), max_uses=2, now=now)
    request = workflow.create_join_request(tenant_id="j3-org", user_id=original.user_id,
        invitation_code="j3-synthetic-invitation", now=now)
    authorization = AuthorizationService(repository=auth, audit_store=AuditEventStore(), clock=clock)
    yield identity, identities, workflow, authorization, original, request, clock
    workflow.close(); auth.close(); identities.close()


def execute(workflow, operation, target, payload, key):
    return workflow.execute_management_operation(operation=operation, actor_id="admin", tenant_id="j3-org",
        target_id=target, payload=payload, idempotency_key=key,
        request_fingerprint=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        trace_id=TRACE_ID, policy_version=POLICY_VERSION)


def approve(journey):
    identity, _, workflow, _, original, request, _ = journey
    execute(workflow, "organization.join.decide", request.request_id,
        {"approved": True, "expected_version": 1, "role": "member"}, "j3-approve-operation-0001")
    return identity.switch_session_tenant(access_token=original.access_token, tenant_id="j3-org", trace_id=TRACE_ID, policy_version=POLICY_VERSION)[0]


def suspend(journey):
    _, _, workflow, _, original, _, _ = journey
    execute(workflow, "organization.member.state", original.user_id,
        {"active": False, "expected_version": 1, "reason": "접근 중지"}, "j3-suspend-operation-0001")


def test_approved_join_is_selectable_and_suspension_preserves_personal_return(journey):
    identity, _, workflow, authorization, original, request, _ = journey
    assert identity.selectable_tenant_workspaces(user_id=original.user_id) == (("tenant-001", "personal", "personal-workspace"),)
    execute(workflow, "organization.join.decide", request.request_id,
        {"approved": False, "expected_version": 1, "role": "member"}, "j3-reject-operation-0001")
    assert len(identity.selectable_tenant_workspaces(user_id=original.user_id)) == 1
    new = workflow.create_join_request(tenant_id="j3-org", user_id=original.user_id, invitation_code="j3-synthetic-invitation", now=journey[-1]())
    execute(workflow, "organization.join.decide", new.request_id,
        {"approved": True, "expected_version": 1, "role": "member"}, "j3-second-approve-0001")
    assert {row[0] for row in identity.selectable_tenant_workspaces(user_id=original.user_id)} == {"tenant-001", "j3-org"}
    switched, workspace = identity.switch_session_tenant(access_token=original.access_token, tenant_id="j3-org", trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    principal = identity.validate_access(switched.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    assert workspace == "org-workspace"
    assert authorization.authorize_action(principal=principal, workspace_id=workspace, action=Action.VIEW, trace_id=TRACE_ID, policy_version=POLICY_VERSION).role is Role.VIEWER
    for old in (original,):
        with pytest.raises(IdentityError): identity.validate_access(old.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        with pytest.raises(IdentityError): identity.rotate_refresh(old.refresh_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    suspend(journey)
    assert identity.selectable_tenant_workspaces(user_id=original.user_id) == (("tenant-001", "personal", "personal-workspace"),)
    assert identity.describe_access(switched.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION).principal == principal
    with pytest.raises(IdentityError) as denied:
        identity.switch_session_tenant(access_token=switched.access_token, tenant_id="j3-org", trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    assert denied.value.code == "TENANT_UNAVAILABLE"
    returned, workspace = identity.switch_session_tenant(access_token=switched.access_token, tenant_id="tenant-001", trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    assert workspace == "personal-workspace" and returned.session_id != switched.session_id
    with pytest.raises(IdentityError): identity.validate_access(switched.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    with pytest.raises(IdentityError): identity.rotate_refresh(switched.refresh_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)


@pytest.mark.parametrize("boundary", ["action", "permission"])
def test_suspended_workflow_blocks_actual_business_authorization(journey, boundary):
    credentials = approve(journey)
    identity, _, _, authorization, _, _, _ = journey
    principal = identity.validate_access(credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    suspend(journey)
    with pytest.raises(AuthorizationError) as denied:
        if boundary == "action":
            authorization.authorize_action(principal=principal, workspace_id="org-workspace", action=Action.VIEW, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        else:
            authorization.evaluate_permission(principal=principal, workspace_id="org-workspace", permission=Permission.FILE_DOWNLOAD_SHARE, requested=True)
    assert (denied.value.code, denied.value.http_status) == ("ORGANIZATION_MEMBERSHIP_SUSPENDED", 403)


def test_workflow_reactivation_never_restores_separately_revoked_auth(journey):
    approve(journey); suspend(journey)
    identity, identities, workflow, _, original, _, _ = journey
    with identities.transaction() as connection:
        connection.execute("UPDATE auth_memberships SET state='suspended',version=version+1 WHERE tenant_id='j3-org' AND user_id=?", (original.user_id,))
    execute(workflow, "organization.member.state", original.user_id,
        {"active": True, "expected_version": 2, "reason": "업무 복귀"}, "j3-active-operation-0001")
    assert identity.selectable_tenant_workspaces(user_id=original.user_id) == (("tenant-001", "personal", "personal-workspace"),)


def test_identity_postgres_workflow_gate_uses_exact_physical_table():
    from daon_user_api.identity_postgres import _PostgresCompatConnection
    sql = _PostgresCompatConnection._sql("SELECT state FROM tenant_memberships WHERE tenant_id=? AND user_id=?")
    assert sql == "SELECT state FROM identity_org_tenant_memberships WHERE tenant_id=%s AND user_id=%s"


@pytest.mark.parametrize("failure,status", [("ORGANIZATION_MEMBERSHIP_SUSPENDED", 200), ("PERSISTENCE_UNAVAILABLE", 503), ("ACCESS_INVALID", 401)])
def test_runtime_session_only_swallows_exact_suspension(journey, monkeypatch, failure, status):
    from fastapi.testclient import TestClient
    from daon_user_api import runtime
    credentials = approve(journey)
    identity, identities, _, authorization, _, _, _ = journey
    suspend(journey)
    dependencies = runtime.build_dependencies(runtime.RuntimeSettings.for_test(database_path=identities._path, policy_version=POLICY_VERSION))
    dependencies.identity_service = identity
    dependencies.authorization_service = authorization
    if failure != "ORGANIZATION_MEMBERSHIP_SUSPENDED":
        def unavailable(**_kwargs):
            if status == 401:
                raise IdentityError(failure, status)
            raise AuthorizationError(failure, status)
        monkeypatch.setattr(authorization, "authorize_action", unavailable)
    try:
        with TestClient(runtime.create_app(dependencies)) as client:
            response = client.get("/api/v1/session", headers={"Authorization": f"Bearer {credentials.access_token}"})
        assert response.status_code == status
        if status == 200:
            assert response.json()["data"]["recovery_operations"] == []
            assert response.json()["data"]["session_id"] == credentials.session_id
        else:
            assert "data" not in response.json()
    finally:
        dependencies.close()


def test_suspended_historical_access_and_other_scope_stay_separate(journey):
    from daon_user_api.authorization import AccessAction, HistoricalResultDescriptor
    credentials = approve(journey)
    identity, _, _, authorization, original, _, clock = journey
    principal = identity.validate_access(credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    descriptor = HistoricalResultDescriptor("j3-result", "answer", "j3-org", "org-workspace", (), (), (), POLICY_VERSION, 1)
    authorization._repository.insert_historical_result(descriptor, clock())
    assert authorization.evaluate_historical_access(principal=principal, result_id="j3-result", action=AccessAction.READ, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    suspend(journey)
    for action in (AccessAction.READ, AccessAction.OPEN_SOURCE, AccessAction.EXPORT):
        with pytest.raises(AuthorizationError) as denied:
            authorization.evaluate_historical_access(principal=principal, result_id="j3-result", action=action, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        assert denied.value.code == "ORGANIZATION_MEMBERSHIP_SUSPENDED"
    personal = IdentityPrincipal(original.user_id, "personal-session", "device", "tenant-001")
    assert authorization.authorize_action(principal=personal, workspace_id="personal-workspace", action=Action.VIEW, trace_id=TRACE_ID, policy_version=POLICY_VERSION).allowed


def test_workflow_database_failure_is_not_treated_as_legacy_allow(journey):
    credentials = approve(journey)
    identity, identities, _, authorization, original, _, _ = journey
    principal = identity.validate_access(credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    with identities.transaction() as connection:
        connection.execute("ALTER TABLE tenant_memberships RENAME COLUMN state TO broken_state")
    with pytest.raises(IdentityError) as unavailable:
        identity.selectable_tenant_workspaces(user_id=original.user_id)
    assert unavailable.value.http_status == 503
    with pytest.raises(AuthorizationError) as unavailable:
        authorization.authorize_action(principal=principal, workspace_id="org-workspace", action=Action.VIEW, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    assert unavailable.value.http_status == 503


def test_runtime_business_consumers_cannot_bypass_guard_with_global_paths(journey):
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from daon_user_api import runtime
    from daon_user_api.authorization import HistoricalResultDescriptor
    credentials = approve(journey)
    identity, identities, _, authorization, _, _, clock = journey
    authorization._repository.insert_historical_result(
        HistoricalResultDescriptor("j3-http-result", "answer", "j3-org", "org-workspace", (), (), (), POLICY_VERSION, 1), clock())
    suspend(journey)
    calls = []
    def unexpected(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("중지된 자료 소비자가 실행됨")
    consumer = SimpleNamespace(export=unexpected, ask=unexpected, read_citation_content=unexpected)
    # 실제 identity/auth와 runtime handler를 사용한다. prefix middleware의 workflow 저장소는
    # 의도적으로 주입하지 않아 자료별 AuthorizationService gate 자체를 검증한다.
    dependencies = runtime.RuntimeDependencies(settings=runtime.RuntimeSettings.for_test(database_path=identities._path, policy_version=POLICY_VERSION),
        identity_service=identity, identity_repository=identities, authorization_service=authorization,
        authorization_repository=authorization._repository, audit_store=AuditEventStore(),
        question_answering_service=consumer, citation_content_repository=consumer, studio_workspace_service=consumer)
    headers = {"Authorization": f"Bearer {credentials.access_token}", "Idempotency-Key": "j3-http-operation-0001"}
    with TestClient(runtime.create_app(dependencies)) as client:
        for method, path, body in [
            ("GET", "/api/v1/workspaces/org-workspace/citations/citation/content?notebook_id=notebook", None),
            ("POST", "/api/v1/workspaces/org-workspace/questions", {"notebook_id": "notebook", "question": "합성 질문"}),
            ("GET", "/api/v1/studio-outputs/output/versions/version/exports/pdf?workspace_id=org-workspace&notebook_id=notebook", None),
            ("POST", "/api/v1/access-decisions", {"resource_id": "j3-http-result", "action": "open_source"}),
        ]:
            response = client.request(method, path, headers=headers, **({"json": body} if body else {}))
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "ORGANIZATION_MEMBERSHIP_SUSPENDED"
            assert "data" not in response.json()
    assert calls == []


@pytest.mark.parametrize("boundary", ["service", "runtime"])
@pytest.mark.parametrize("state,status", [("suspended", 403), ("active", 200), ("no-row", 200), ("db-error", 503), ("personal", 200), ("no-role", 403), ("configured-admin", 403)])
def test_tenant_wide_audit_read_uses_workflow_and_stops_runtime_consumer(journey, state, status, boundary):
    from fastapi.testclient import TestClient
    from daon_user_api import runtime
    credentials = approve(journey)
    identity, identities, workflow, authorization, original, _, clock = journey
    if state == "suspended": suspend(journey)
    with identities.transaction() as connection:
        if state not in {"no-role", "configured-admin"}:
            connection.execute("INSERT INTO auth_tenant_roles VALUES ('j3-org',?,'organization_admin','active',1,?)", (original.user_id, clock().isoformat()))
        if state == "no-row": connection.execute("DELETE FROM tenant_memberships WHERE tenant_id='j3-org'")
        if state == "db-error": connection.execute("ALTER TABLE tenant_memberships RENAME COLUMN state TO broken_state")
    if state == "personal":
        credentials, _ = identity.switch_session_tenant(access_token=credentials.access_token, tenant_id="tenant-001", trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    principal = identity.validate_access(credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    if boundary == "service":
        if status == 200:
            authorization.authorize_audit_read(principal=principal, workspace_id=None, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        else:
            with pytest.raises(AuthorizationError) as denied:
                authorization.authorize_audit_read(principal=principal, workspace_id=None, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            assert denied.value.http_status == status
        return
    class ObservedAuditStore(AuditEventStore):
        reads = 0
        def list(self, **kwargs):
            self.reads += 1
            return super().list(**kwargs)
    store = ObservedAuditStore()
    dependencies = runtime.RuntimeDependencies(settings=runtime.RuntimeSettings.for_test(database_path=identities._path, policy_version=POLICY_VERSION,
        system_admin_user_ids=frozenset({original.user_id}) if state == "configured-admin" else frozenset({"admin"})),
        identity_service=identity, identity_repository=identities, authorization_service=authorization,
        authorization_repository=authorization._repository, audit_store=store)
    with TestClient(runtime.create_app(dependencies)) as client:
        response = client.get(f"/api/v1/audit-events?tenant_id={principal.tenant_id}", headers={"Authorization": f"Bearer {credentials.access_token}"})
        assert response.status_code == status
        assert store.reads == (1 if status == 200 else 0)
        if state == "suspended": assert response.json()["error"]["code"] == "ORGANIZATION_MEMBERSHIP_SUSPENDED"
