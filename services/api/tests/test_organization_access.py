"""J1: 공통 SQLite connection의 실제 소속·권한 연결과 실패 원자성."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from unittest.mock import patch

import pytest

from daon_user_api.authorization import SqliteAuthorizationRepository
from daon_user_api.identity import MINIMUM_STEP_UP_ACTION_GROUPS, SqliteIdentityRepository
from daon_user_api.organization_membership import OrganizationWorkflowError, SqliteOrganizationRepository


@pytest.fixture
def joined_scope(tmp_path):
    path = tmp_path / "j1-common.sqlite"
    identity = SqliteIdentityRepository(path)
    auth = SqliteAuthorizationRepository(path)
    repository = SqliteOrganizationRepository(path)
    now = datetime.now(timezone.utc)
    with repository.transaction() as connection:
        connection.execute("INSERT INTO tenants VALUES ('j1-tenant')")
        for action in MINIMUM_STEP_UP_ACTION_GROUPS:
            connection.execute("INSERT INTO tenant_step_up_actions VALUES (?,?,1)", ("j1-tenant", action))
        connection.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,email_verified_at,state) VALUES ('j1-user','local','j1-subject','j1-login','j1-synthetic@example.test',?,'active')", (now.isoformat(),))
        for workspace, tenant in (("j1-workspace", "j1-tenant"), ("j1-other-workspace", "j1-tenant"), ("j1-foreign-workspace", "j1-foreign-tenant")):
            connection.execute("INSERT INTO auth_workspaces VALUES (?,?,'organization','cloud_sync',1000,1,1,?)", (workspace, tenant, now.isoformat()))
    invitation = repository.create_invitation(tenant_id="j1-tenant", created_by="j1-admin", code="j1-synthetic-invitation",
        expires_at=now+timedelta(days=1), max_uses=1, workspace_id="j1-workspace", now=now)
    request = repository.create_join_request(tenant_id="j1-tenant", user_id="j1-user", invitation_code="j1-synthetic-invitation", now=now)
    yield repository, request, invitation, now
    repository.close(); auth.close(); identity.close()


def _execute(repository, request_id, *, operation="organization.join.decide", payload=None, key="j1-approve-operation-0001", target=None):
    selected = {"approved": True, "role": "member", "expected_version": 1} if payload is None else payload
    return repository.execute_management_operation(operation=operation, actor_id="j1-admin", tenant_id="j1-tenant",
        target_id=request_id if target is None else target, payload=selected, idempotency_key=key,
        request_fingerprint=hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest(),
        trace_id="j1-trace", policy_version="j1-policy")


def _assert_no_grant(repository, request_id):
    with repository.transaction() as connection:
        for table in ("memberships", "auth_memberships", "auth_tenant_roles", "tenant_memberships", "tenant_membership_role_history", "organization_operation_outbox"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("SELECT state,version FROM organization_join_requests WHERE request_id=?", (request_id,)).fetchone()[:] == ("pending", 1)
        assert connection.execute("SELECT used_count,version FROM invitation_codes").fetchone()[:] == (0, 1)
        assert {row[0] for row in connection.execute("SELECT acl_version FROM auth_workspaces")} == {1}


def test_approved_join_creates_only_bound_workspace_viewer(joined_scope):
    repository, request, invitation, _now = joined_scope
    result = _execute(repository, request.request_id)
    with repository.transaction() as connection:
        assert [tuple(row) for row in connection.execute("SELECT tenant_id,user_id,role FROM memberships")] == [("j1-tenant", "j1-user", "member")]
        assert [tuple(row) for row in connection.execute("SELECT tenant_id,workspace_id,user_id,role,state,version FROM auth_memberships")] == [("j1-tenant", "j1-workspace", "j1-user", "viewer", "active", 1)]
        assert connection.execute("SELECT role,state,join_workspace_id,version FROM tenant_memberships").fetchone()[:] == ("member", "active", "j1-workspace", 1)
        assert connection.execute("SELECT acl_version FROM auth_workspaces WHERE workspace_id='j1-workspace'").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM auth_workspaces WHERE workspace_id<>'j1-workspace' AND acl_version=1").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM auth_tenant_roles").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM memberships WHERE user_id='j1-admin'").fetchone()[0] == 0
        assert connection.execute("SELECT used_count FROM invitation_codes WHERE invitation_id=?", (invitation.invitation_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM tenant_step_up_actions").fetchone()[0] == len(MINIMUM_STEP_UP_ACTION_GROUPS)
    assert result.value["state"] == "approved" and _execute(repository, request.request_id).replayed


@pytest.mark.parametrize("table", ["memberships", "auth_memberships"])
def test_existing_identity_or_auth_membership_is_never_overwritten(joined_scope, table):
    repository, request, _invitation, now = joined_scope
    with repository.transaction() as connection:
        if table == "memberships":
            connection.execute("INSERT INTO memberships VALUES ('j1-tenant','j1-user','organization_admin')")
        else:
            connection.execute("INSERT INTO auth_memberships VALUES ('j1-tenant','j1-other-workspace','j1-user','editor','suspended',8,?)", (now.isoformat(),))
    with pytest.raises(OrganizationWorkflowError) as error:
        _execute(repository, request.request_id)
    assert error.value.status == 409 and error.value.code == "ORGANIZATION_MEMBERSHIP_CONFLICT"
    with repository.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0] == 0
        assert connection.execute("SELECT state FROM organization_join_requests").fetchone()[0] == "pending"
        assert connection.execute("SELECT used_count FROM invitation_codes").fetchone()[0] == 0
        if table == "memberships":
            assert connection.execute("SELECT role FROM memberships").fetchone()[0] == "organization_admin"
        else:
            assert connection.execute("SELECT role,state,version FROM auth_memberships").fetchone()[:] == ("editor", "suspended", 8)


@pytest.mark.parametrize("invalid", ["unbound", "foreign", "without_invitation", "elevated_role", "inactive_user"])
def test_join_access_rejects_unsafe_scope_or_implicit_elevation(joined_scope, invalid):
    repository, request, _invitation, _now = joined_scope
    with repository.transaction() as connection:
        if invalid == "unbound": connection.execute("UPDATE invitation_codes SET workspace_id=NULL")
        elif invalid == "foreign": connection.execute("UPDATE invitation_codes SET workspace_id='j1-foreign-workspace'")
        elif invalid == "without_invitation": connection.execute("UPDATE organization_join_requests SET invitation_id=NULL")
        elif invalid == "inactive_user": connection.execute("UPDATE users SET state='suspended'")
    payload = {"approved": True, "role": "organization_admin" if invalid == "elevated_role" else "member", "expected_version": 1}
    with pytest.raises(OrganizationWorkflowError): _execute(repository, request.request_id, payload=payload)
    _assert_no_grant(repository, request.request_id)


def test_rejected_join_consumes_no_invitation_and_grants_nothing(joined_scope):
    repository, request, _invitation, _now = joined_scope
    result = _execute(repository, request.request_id, payload={"approved": False, "expected_version": 1, "role": "member"})
    assert result.value["state"] == "rejected"
    with repository.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM memberships").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM auth_memberships").fetchone()[0] == 0
        assert connection.execute("SELECT used_count FROM invitation_codes").fetchone()[0] == 0


@pytest.mark.parametrize("boundary", ["identity", "auth", "acl", "workflow_link", "dto", "receipt_audit"])
def test_join_access_stage_failure_rolls_back_all_rows(joined_scope, boundary):
    repository, request, _invitation, _now = joined_scope
    if boundary in {"identity", "auth", "acl", "workflow_link"}:
        table, event, when = {
            "identity": ("memberships", "INSERT", ""),
            "auth": ("auth_memberships", "INSERT", ""),
            "acl": ("auth_workspaces", "UPDATE", "WHEN NEW.acl_version<>OLD.acl_version"),
            "workflow_link": ("tenant_memberships", "UPDATE", "WHEN NEW.join_workspace_id IS NOT NULL"),
        }[boundary]
        with repository.transaction() as connection:
            connection.execute(f"CREATE TRIGGER j1_injected_failure AFTER {event} ON {table} {when} BEGIN SELECT RAISE(ABORT,'J1_INJECTED_STORAGE_FAILURE'); END")
        with pytest.raises(OrganizationWorkflowError): _execute(repository, request.request_id)
    else:
        method = "_management_view" if boundary == "dto" else "_record_management_operation"
        original = getattr(repository, method)
        def fail_after_stage(*args, **kwargs):
            original(*args, **kwargs)
            raise OrganizationWorkflowError("J1_INJECTED_STORAGE_FAILURE", 503)
        with patch.object(repository, method, fail_after_stage), pytest.raises(OrganizationWorkflowError):
            _execute(repository, request.request_id)
    _assert_no_grant(repository, request.request_id)


def test_join_role_change_preserves_other_workspace_and_rejects_stale_auth_version(joined_scope):
    repository, request, _invitation, now = joined_scope
    _execute(repository, request.request_id)
    with repository.transaction() as connection:
        connection.execute("INSERT INTO auth_memberships VALUES ('j1-tenant','j1-other-workspace','j1-user','reviewer','active',7,?)", (now.isoformat(),))
        # 기존 S38 저장 결과와 같은 version 전진을 합성한다. S38 제품코드는 수정하지 않는다.
        connection.execute("UPDATE auth_memberships SET role='reviewer',version=2 WHERE workspace_id='j1-workspace'")
        connection.execute("UPDATE auth_workspaces SET acl_version=3 WHERE workspace_id='j1-workspace'")
    payload = {"workspace_id": "j1-workspace", "role": "editor", "expected_version": 1, "expected_auth_version": 1, "reason": "업무 조정"}
    with pytest.raises(OrganizationWorkflowError) as error:
        _execute(repository, request.request_id, operation="organization.member.role", target="j1-user", payload=payload, key="j1-stale-auth-role-0001")
    assert error.value.code == "MEMBERSHIP_CONFLICT" and error.value.status == 409
    payload["expected_auth_version"] = 2
    result = _execute(repository, request.request_id, operation="organization.member.role", target="j1-user", payload=payload, key="j1-exact-auth-role-0001")
    assert {key: result.value[key] for key in ("role", "version", "join_workspace_id", "access_role", "access_state", "access_version")} == {
        "role": "member", "version": 2, "join_workspace_id": "j1-workspace", "access_role": "editor", "access_state": "active", "access_version": 3}
    with repository.transaction() as connection:
        assert connection.execute("SELECT role,version FROM auth_memberships WHERE workspace_id='j1-other-workspace'").fetchone()[:] == ("reviewer", 7)
        assert connection.execute("SELECT acl_version FROM auth_workspaces WHERE workspace_id='j1-workspace'").fetchone()[0] == 4
        rows = connection.execute("SELECT audit_json FROM organization_operation_outbox WHERE operation='organization.member.role'").fetchall()
        assert len(rows) == 1
        audit = json.loads(rows[0][0])
        assert (audit["before"], audit["after"], audit["auth_version"]) == ("reviewer", "editor", 3)
        assert connection.execute("SELECT role FROM tenant_memberships").fetchone()[0] == "member"


@pytest.mark.parametrize("invalid", ["workspace", "auth_suspended", "missing_auth", "workflow_version", "elevated_role"])
def test_join_role_change_never_rebinds_or_restores_revoked_auth(joined_scope, invalid):
    repository, request, _invitation, _now = joined_scope
    _execute(repository, request.request_id)
    with repository.transaction() as connection:
        if invalid == "auth_suspended": connection.execute("UPDATE auth_memberships SET state='suspended'")
        elif invalid == "missing_auth": connection.execute("DELETE FROM auth_memberships")
    payload = {"workspace_id": "j1-other-workspace" if invalid == "workspace" else "j1-workspace",
        "role": "workspace_admin" if invalid == "elevated_role" else "editor",
        "expected_version": 99 if invalid == "workflow_version" else 1, "expected_auth_version": 1, "reason": "검토"}
    with pytest.raises(OrganizationWorkflowError):
        _execute(repository, request.request_id, operation="organization.member.role", target="j1-user", payload=payload, key="j1-invalid-auth-role-0001")
    with repository.transaction() as connection:
        assert connection.execute("SELECT role,version FROM tenant_memberships").fetchone()[:] == ("member", 1)
        assert connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0] == 1
        assert connection.execute("SELECT acl_version FROM auth_workspaces WHERE workspace_id='j1-workspace'").fetchone()[0] == 2


def test_join_access_read_uses_actual_auth_and_never_owns_the_connection(joined_scope):
    repository, request, _invitation, _now = joined_scope
    _execute(repository, request.request_id)
    with repository.transaction() as connection:
        connection.execute("UPDATE auth_memberships SET role='approver',version=4 WHERE workspace_id='j1-workspace'")
        access = repository._access_bridge.read_join_access(connection=connection, tenant_id="j1-tenant", workspace_id="j1-workspace", user_id="j1-user")
        assert access == {"access_role": "approver", "access_state": "active", "access_version": 4}
        assert connection.in_transaction
        assert connection.execute("SELECT count(*) FROM auth_memberships").fetchone()[0] == 1


@pytest.mark.parametrize("boundary", ["auth", "acl", "workflow", "dto", "receipt_audit"])
def test_join_role_failure_keeps_auth_workflow_acl_history_and_receipt(joined_scope, boundary):
    repository, request, _invitation, _now = joined_scope
    _execute(repository, request.request_id)
    with repository.transaction() as connection:
        history = [tuple(row) for row in connection.execute("SELECT * FROM tenant_membership_role_history")]
    payload = {"workspace_id": "j1-workspace", "role": "editor", "expected_version": 1, "expected_auth_version": 1, "reason": "역할 변경"}
    if boundary in {"auth", "acl", "workflow"}:
        table = {"auth": "auth_memberships", "acl": "auth_workspaces", "workflow": "tenant_memberships"}[boundary]
        with repository.transaction() as connection:
            connection.execute(f"CREATE TRIGGER j1_role_failure AFTER UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'J1_ROLE_STORAGE_FAILURE'); END")
        with pytest.raises(OrganizationWorkflowError):
            _execute(repository, request.request_id, operation="organization.member.role", target="j1-user", payload=payload, key="j1-role-rollback-0001")
    else:
        method = "_management_view" if boundary == "dto" else "_record_management_operation"
        original = getattr(repository, method)
        def fail_after_stage(*args, **kwargs):
            original(*args, **kwargs)
            raise OrganizationWorkflowError("J1_ROLE_STORAGE_FAILURE", 503)
        with patch.object(repository, method, fail_after_stage), pytest.raises(OrganizationWorkflowError):
            _execute(repository, request.request_id, operation="organization.member.role", target="j1-user", payload=payload, key="j1-role-rollback-0001")
    with repository.transaction() as connection:
        assert connection.execute("SELECT role,state,version FROM auth_memberships").fetchone()[:] == ("viewer", "active", 1)
        assert connection.execute("SELECT role,version FROM tenant_memberships").fetchone()[:] == ("member", 1)
        assert connection.execute("SELECT acl_version FROM auth_workspaces WHERE workspace_id='j1-workspace'").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0] == 1
        assert [tuple(row) for row in connection.execute("SELECT * FROM tenant_membership_role_history")] == history
