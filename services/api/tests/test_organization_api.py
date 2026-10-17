from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from daon_user_api.authorization import Role, SqliteAuthorizationRepository
from daon_user_api.identity import IdentityPrincipal, SqliteIdentityRepository
from daon_user_api.organization_api import OrganizationApi
from daon_user_api.organization_membership import SqliteOrganizationRepository
from daon_user_api.organization_membership import OrganizationWorkflowError


def _app(tmp_path: Path, principal: IdentityPrincipal) -> tuple[FastAPI, SqliteOrganizationRepository, SqliteAuthorizationRepository]:
    path = tmp_path / "organization-common.sqlite"
    identity = SqliteIdentityRepository(path)
    auth = SqliteAuthorizationRepository(path)
    now = datetime.now(timezone.utc)
    auth.bootstrap_workspace(
        tenant_id="tenant-a", workspace_id="workspace-a", owner_user_id="admin",
        owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync",
        cost_limit_cents=1000, now=now,
    )
    repo = SqliteOrganizationRepository(path)
    with repo.transaction() as connection:
        connection.execute("INSERT INTO tenants VALUES ('tenant-a')")
        connection.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,email_verified_at,state) VALUES ('member','local','api-member','api-member','api-member@example.test',?,'active')", (now.isoformat(),))
    identity.close()
    app = FastAPI()
    @app.exception_handler(OrganizationWorkflowError)
    async def workflow_error(_request: Request, error: OrganizationWorkflowError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content={"code": error.code})
    OrganizationApi(repo, auth, system_admin_checker=lambda value: value.user_id == "configured-admin", policy_version="organization-api-test-policy").mount(app, lambda _request: principal)
    return app, repo, auth


def test_creation_request_and_cross_tenant_join_isolation(tmp_path: Path) -> None:
    principal = IdentityPrincipal("member", "session", "device", "tenant-a")
    app, repo, auth = _app(tmp_path, principal)
    with TestClient(app) as client:
        created = client.post("/api/v1/organization/creation-requests", json={
            "organization_name": "New Org", "organization_identifier": "new-org",
        }, headers={"Idempotency-Key": "creation-request-0001"})
        assert created.status_code == 202
        assert created.json()["data"]["applicant_user_id"] == "member"
        replay = client.post("/api/v1/organization/creation-requests", json={
            "organization_name": "New Org", "organization_identifier": "new-org",
        }, headers={"Idempotency-Key": "creation-request-0001"})
        assert replay.status_code == 202
        assert replay.json()["data"]["request_id"] == created.json()["data"]["request_id"]
        denied = client.get("/api/v1/organization/join-requests?tenant_id=tenant-b")
        assert denied.status_code == 403
    repo.close(); auth.close()


def test_organization_admin_can_create_and_revoke_invitation(tmp_path: Path) -> None:
    principal = IdentityPrincipal("admin", "session", "device", "tenant-a")
    app, repo, auth = _app(tmp_path, principal)
    with TestClient(app) as client:
        created = client.post("/api/v1/organization/tenants/tenant-a/invitations", json={
            "workspace_id": "workspace-a",
            "code": "invite-secret", "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "max_uses": 1,
        }, headers={"Idempotency-Key": "invitation-create-0001", "X-Daon-Expected-Session-Id": "session"})
        assert created.status_code == 201
        data = created.json()["data"]
        assert "code_digest" not in data
        assert "code" not in data
        revoked = client.delete(f"/api/v1/organization/tenants/tenant-a/invitations/{data['invitation_id']}?expected_version=1", headers={"Idempotency-Key": "invitation-revoke-0001", "X-Daon-Expected-Session-Id": "session"})
        assert revoked.status_code == 200
        assert revoked.json()["data"]["state"] == "revoked"
    repo.close(); auth.close()


def test_l2_dto_failure_rolls_back_the_http_mutation(tmp_path: Path, monkeypatch) -> None:
    from daon_user_api import organization_membership
    principal = IdentityPrincipal("admin", "session", "device", "tenant-a")
    app, repo, auth = _app(tmp_path, principal)
    def reject_timestamp(_value):
        raise OrganizationWorkflowError("INVALID_TIME")
    monkeypatch.setattr(organization_membership, "_parse", reject_timestamp)
    with TestClient(app) as client:
        response = client.post("/api/v1/organization/tenants/tenant-a/invitations", json={
            "workspace_id": "workspace-a",
            "code": "l2-synthetic-invitation", "expires_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), "max_uses": 1,
        }, headers={"Idempotency-Key": "l2-dto-rollback-0001", "X-Daon-Expected-Session-Id": "session"})
        assert response.status_code == 400
    with repo.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM invitation_codes").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM organization_idempotency").fetchone()[0] == 0
    repo.close(); auth.close()


def test_l2_receipt_failure_rolls_back_http_business_and_audit(tmp_path: Path, monkeypatch) -> None:
    app, repo, auth = _app(tmp_path, IdentityPrincipal("admin", "session", "device", "tenant-a"))
    original = repo._record_management_operation
    def fail_after_receipt(connection, **values):
        original(connection, **values)
        raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503)
    monkeypatch.setattr(repo, "_record_management_operation", fail_after_receipt)
    with TestClient(app) as client:
        result = client.post("/api/v1/organization/tenants/tenant-a/invitations", json={
            "workspace_id": "workspace-a",
            "code": "l2-rollback-synthetic", "expires_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), "max_uses": 1,
        }, headers={"Idempotency-Key": "l2-receipt-rollback-0001", "X-Daon-Expected-Session-Id": "session"})
        assert result.status_code == 503
    with repo.transaction() as connection:
        for table in ("invitation_codes", "organization_operation_outbox", "organization_idempotency"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    repo.close(); auth.close()


def test_user_can_submit_join_request_with_invitation_code_only(tmp_path: Path) -> None:
    principal = IdentityPrincipal("member", "session", "device", "tenant-personal")
    app, repo, auth = _app(tmp_path, principal)
    repo.create_invitation(
        tenant_id="tenant-a", created_by="admin", code="AAAAA-TEST-2026",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1), max_uses=1,
        now=datetime.now(timezone.utc),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/organization/join-requests",
            json={"invitation_code": "AAAAA-TEST-2026"},
            headers={"Idempotency-Key": "join-code-only-0001", "X-Daon-Expected-Session-Id": "session"},
        )
        assert created.status_code == 202
        assert created.json()["data"]["tenant_id"] == "tenant-a"
    repo.close(); auth.close()


def test_l3_http_approval_actual_role_state_and_safe_membership_projection(tmp_path: Path) -> None:
    app, repo, auth = _app(tmp_path, IdentityPrincipal("admin", "session", "device", "tenant-a"))
    now = datetime.now(timezone.utc)
    repo.create_invitation(tenant_id="tenant-a", created_by="admin", code="j1-api-invitation", workspace_id="workspace-a", expires_at=now+timedelta(days=1), max_uses=1, now=now)
    request = repo.create_join_request(tenant_id="tenant-a", user_id="member", invitation_code="j1-api-invitation", now=now)
    with TestClient(app) as client:
        for path, payload, key in (
            (f"/api/v1/organization/join-requests/{request.request_id}/decision", {"approved": True, "expected_version": 1}, "l2-http-decision-0001"),
        ):
            method = client.post if path.endswith("/decision") else client.patch
            response = method(path, json=payload, headers={"Idempotency-Key": key, "X-Daon-Expected-Session-Id": "session"})
            assert response.status_code == 200
            replay = method(path, json=payload, headers={"Idempotency-Key": key, "X-Daon-Expected-Session-Id": "session"})
            assert replay.json()["data"] == response.json()["data"]
        with repo.transaction() as connection:
            assert connection.execute("SELECT role,state,version FROM auth_memberships WHERE workspace_id='workspace-a' AND user_id='member'").fetchone()[:] == ("viewer", "active", 1)
            history = [tuple(row) for row in connection.execute("SELECT * FROM tenant_membership_role_history")]
        # 연결된 실제 권한은 Workspace/auth version 없는 구 body로 편집하지 않는다.
        denied = client.patch("/api/v1/organization/tenants/tenant-a/members/member/role", json={"role": "editor", "expected_version": 1, "reason": "업무 검토"}, headers={"Idempotency-Key": "j1-api-role-closed-0001", "X-Daon-Expected-Session-Id": "session"})
        assert denied.status_code == 422
        payload = {"workspace_id": "workspace-a", "role": "editor", "expected_version": 1, "expected_auth_version": 1, "reason": "업무 검토"}
        role_headers = {"Idempotency-Key": "l3-api-role-0001", "X-Daon-Expected-Session-Id": "session"}
        role_result = client.patch("/api/v1/organization/tenants/tenant-a/members/member/role", json=payload, headers=role_headers)
        assert role_result.status_code == 200
        saved = role_result.json()["data"]
        assert (saved["role"], saved["access_role"], saved["access_version"]) == ("member", "editor", 2)
        assert client.patch("/api/v1/organization/tenants/tenant-a/members/member/role", json=payload, headers=role_headers).json()["data"] == saved
        stale = client.patch("/api/v1/organization/tenants/tenant-a/members/member/role", json=payload, headers=role_headers | {"Idempotency-Key": "l3-api-role-stale-0001"})
        assert stale.status_code == 409
        with repo.transaction() as connection:
            assert [tuple(row) for row in connection.execute("SELECT * FROM tenant_membership_role_history")] == history
        for _ in range(2):
            changed = client.patch("/api/v1/organization/tenants/tenant-a/members/member/state?active=false", json={"expected_version": 2, "reason": "중지"}, headers={"Idempotency-Key": "l2-http-state-0001", "X-Daon-Expected-Session-Id": "session"})
            assert changed.status_code == 200 and changed.json()["data"]["state"] == "suspended"
            assert (changed.json()["data"]["access_role"], changed.json()["data"]["access_version"]) == ("editor", 2)
        members = client.get("/api/v1/organization/tenants/tenant-a/members").json()["data"]
        assert len(members) == 1 and members[0]["state"] == "suspended"
        assert set(members[0]) == {"tenant_id", "user_id", "role", "state", "version", "updated_at", "join_workspace_id", "access_role", "access_state", "access_version"}
        assert (members[0]["role"], members[0]["access_role"], members[0]["access_version"]) == ("member", "editor", 2)
    with repo.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0] == 3
        # auth 역할 변경은 과거 표기 역할 이력을 쓰지 않는다. 승인·상태 이력만 두 건이다.
        assert connection.execute("SELECT count(*) FROM tenant_membership_role_history").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM organization_idempotency").fetchone()[0] == 0
    repo.close(); auth.close()


def test_system_approval_invokes_organization_provisioner(tmp_path: Path) -> None:
    applicant = IdentityPrincipal("applicant", "session", "device", "tenant-personal")
    system = IdentityPrincipal("configured-admin", "session", "device", "tenant-system")
    auth = SqliteAuthorizationRepository(tmp_path / "auth.sqlite")
    repo = SqliteOrganizationRepository(tmp_path / "org.sqlite")
    app = FastAPI()
    calls = []

    @app.exception_handler(OrganizationWorkflowError)
    async def workflow_error(_request: Request, error: OrganizationWorkflowError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content={"code": error.code})

    current = {"principal": applicant}
    OrganizationApi(
        repo, auth, system_admin_checker=lambda value: value.user_id == "configured-admin",
        organization_provisioner=lambda request: calls.append(request),
        policy_version="organization-api-test-policy",
    ).mount(app, lambda _request: current["principal"])
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/organization/creation-requests",
            json={"organization_name": "New Org", "organization_identifier": "new-org"},
            headers={"Idempotency-Key": "creation-provision-0001"},
        )
        assert created.status_code == 202
        current["principal"] = system
        decided = client.post(
            f"/api/v1/organization/creation-requests/{created.json()['data']['request_id']}/decision",
            json={"approved": True, "expected_version": 1},
            headers={"Idempotency-Key": "creation-provision-0002"},
        )
        assert decided.status_code == 200
        assert len(calls) == 1
        assert calls[0].requested_org_identifier == "new-org"
    repo.close(); auth.close()


def test_system_directory_is_available_only_to_system_admin(tmp_path: Path) -> None:
    principal = IdentityPrincipal("configured-admin", "session", "device", "tenant-system")
    app, repo, auth = _app(tmp_path, principal)
    with TestClient(app) as client:
        directory = client.get("/api/v1/admin/directory")
        assert directory.status_code == 200
        assert set(directory.json()["data"]) == {"organizations", "users"}
    repo.close(); auth.close()


def test_l3_system_admin_without_membership_lists_only_real_organization_scopes(tmp_path):
    actor = IdentityPrincipal("configured-admin", "system-session", "system-device", "internal-admin-tenant")
    app, repo, auth = _app(tmp_path, actor)
    now = datetime.now(timezone.utc)
    auth.bootstrap_workspace(tenant_id="tenant-a", workspace_id="workspace-a2", owner_user_id="admin", owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=1000, now=now)
    auth.bootstrap_workspace(tenant_id="personal-only", workspace_id="personal-workspace", owner_user_id="personal-user", owner_role=Role.PERSONAL_OWNER, workspace_kind="personal", data_area="cloud_sync", cost_limit_cents=1000, now=now)
    with TestClient(app) as client:
        response = client.get("/api/v1/organization/admin/tenants")
        assert response.status_code == 200
        assert response.json()["data"] == [{"tenant_id": "tenant-a", "display_name": "tenant-a"}]
        workspaces = client.get("/api/v1/organization/tenants/tenant-a/workspaces")
        assert workspaces.status_code == 200
        assert workspaces.json()["data"] == [{"workspace_id": "workspace-a", "display_name": "workspace-a"}, {"workspace_id": "workspace-a2", "display_name": "workspace-a2"}]
        assert client.get("/api/v1/organization/tenants/tenant-a/members").status_code == 200
        assert client.get("/api/v1/organization/admin/tenants?tenant_id=foreign").status_code == 422
        assert client.get("/api/v1/organization/tenants/tenant-a/workspaces?tenant_id=foreign").status_code == 422
        assert client.get("/api/v1/organization/tenants/tenant-a/invitations?limit=10").status_code == 422
        missing = client.get("/api/v1/organization/tenants/missing/workspaces")
        assert missing.status_code == 404 and missing.json()["code"] == "ORGANIZATION_NOT_FOUND"
    with repo.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM memberships WHERE user_id='configured-admin'").fetchone()[0] == 0
        for table in ("invitation_codes", "tenant_memberships", "organization_operation_outbox"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    repo.close(); auth.close()


def test_l3_regular_admin_never_gets_global_or_foreign_scope(tmp_path):
    app, repo, auth = _app(tmp_path, IdentityPrincipal("admin", "session", "device", "tenant-a"))
    with TestClient(app) as client:
        assert client.get("/api/v1/organization/admin/tenants").status_code == 403
        assert client.get("/api/v1/organization/tenants/tenant-a/workspaces").status_code == 200
        assert client.get("/api/v1/organization/tenants/foreign/workspaces").status_code == 403
    repo.close(); auth.close()


def test_l3_invitation_safe_projection_expiry_order_and_required_current_version(tmp_path):
    app, repo, auth = _app(tmp_path, IdentityPrincipal("configured-admin", "system-session", "device", "internal-admin-tenant"))
    now = datetime.now(timezone.utc)
    expired = repo.create_invitation(tenant_id="tenant-a", created_by="admin", code="l3-synthetic-expired", workspace_id="workspace-a", expires_at=now+timedelta(hours=1), max_uses=1, now=now-timedelta(days=1))
    with repo.transaction() as connection:
        connection.execute("UPDATE invitation_codes SET expires_at=? WHERE invitation_id=?", ((now-timedelta(seconds=1)).isoformat(), expired.invitation_id))
    headers = {"Idempotency-Key": "l3-invitation-create-0001", "X-Daon-Expected-Session-Id": "system-session"}
    with TestClient(app) as client:
        created = client.post("/api/v1/organization/tenants/tenant-a/invitations", json={"workspace_id": "workspace-a", "code": "l3-synthetic-current", "expires_at": (now+timedelta(days=1)).isoformat(), "max_uses": 1}, headers=headers)
        assert created.status_code == 201
        row = created.json()["data"]
        assert set(row) == {"invitation_id", "tenant_id", "workspace_id", "created_by", "expires_at", "max_uses", "used_count", "state", "version", "created_at", "updated_at"}
        listed = client.get("/api/v1/organization/tenants/tenant-a/invitations")
        assert listed.status_code == 200
        assert [item["invitation_id"] for item in listed.json()["data"]] == [row["invitation_id"], expired.invitation_id]
        assert listed.json()["data"][1]["state"] == "expired"
        assert "code_digest" not in listed.text and "l3-synthetic" not in listed.text
        for field in ("created_at", "updated_at", "expires_at"):
            assert "T" in row[field] and row[field].endswith("+00:00")
        path = f"/api/v1/organization/tenants/tenant-a/invitations/{row['invitation_id']}"
        headers = headers | {"Idempotency-Key": "l3-invitation-revoke-0001"}
        assert client.delete(path, headers=headers).status_code == 422
        assert client.delete(path+"?expected_version=bad", headers=headers).status_code == 422
        assert client.delete(path+"?expected_version=2", headers=headers).status_code == 409
        revoked = client.delete(path+"?expected_version=1", headers=headers)
        assert revoked.status_code == 200 and revoked.json()["data"]["state"] == "revoked"
        expired_delete = client.delete(f"/api/v1/organization/tenants/tenant-a/invitations/{expired.invitation_id}?expected_version=1", headers=headers | {"Idempotency-Key": "l3-expired-revoke-0001"})
        assert expired_delete.status_code == 409
    repo.close(); auth.close()


def test_l3_six_writes_require_original_session_before_mutation_or_replay(tmp_path):
    app, repo, auth = _app(tmp_path, IdentityPrincipal("admin", "session", "device", "tenant-a"))
    paths = [("POST", "/api/v1/organization/join-requests", {"invitation_code": "synthetic"}),
        ("POST", "/api/v1/organization/join-requests/unknown/decision", {"approved": False, "expected_version": 1}),
        ("POST", "/api/v1/organization/tenants/tenant-a/invitations", {"workspace_id": "workspace-a", "code": "synthetic", "expires_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), "max_uses": 1}),
        ("DELETE", "/api/v1/organization/tenants/tenant-a/invitations/unknown?expected_version=1", None),
        ("PATCH", "/api/v1/organization/tenants/tenant-a/members/member/role", {"workspace_id": "workspace-a", "role": "editor", "expected_version": 1, "expected_auth_version": 1, "reason": "검토"}),
        ("PATCH", "/api/v1/organization/tenants/tenant-a/members/member/state?active=false", {"expected_version": 1})]
    with TestClient(app) as client:
        for method, path, body in paths:
            for expected, status in ((None, 422), ("invalid space", 422), ("different-session", 409)):
                headers = {"Idempotency-Key": "l3-original-session-0001"}
                if expected is not None: headers["X-Daon-Expected-Session-Id"] = expected
                result = client.request(method, path, json=body, headers=headers)
                assert result.status_code == status, (method, path, result.status_code)
                if status == 409: assert result.json()["code"] == "ACCOUNT_SESSION_CHANGED"
    with repo.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0] == 0
    repo.close(); auth.close()


def test_l3_runtime_flag_does_not_authorize_fake_or_split_storage(tmp_path):
    from dataclasses import replace
    from daon_user_api import runtime
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    for flag in (False, True):
        settings = replace(runtime.RuntimeSettings.for_test(database_path=tmp_path / f"runtime-{flag}.sqlite", policy_version="l3-runtime-policy"), organization_admin_v2_enabled=flag)
        dependencies = runtime.build_dependencies(settings)
        try:
            app = runtime.create_app(dependencies)
            with patch.object(runtime, "_principal", return_value=IdentityPrincipal("admin", "session", "device", "tenant-a")), TestClient(app) as client:
                result = client.post("/api/v1/organization/join-requests", json={"invitation_code": "synthetic"}, headers={"Idempotency-Key": "l3-flag-closed-0001", "X-Daon-Expected-Session-Id": "session"})
                assert result.status_code == 503
                assert result.json()["error"]["code"] == "ORGANIZATION_WORKFLOW_UNAVAILABLE"
        finally:
            dependencies.close()


def test_l3_nine_operation_etags_cover_safe_body_trace_status_and_replay(tmp_path):
    admin = IdentityPrincipal("configured-admin", "system-session", "device", "internal-admin-tenant")
    _, repo, auth = _app(tmp_path, admin)
    current = {"principal": admin}
    app = FastAPI()

    @app.middleware("http")
    async def trace(request, call_next):
        request.state.trace_id = request.headers.get("X-Trace-Id", "etag-trace-a")
        return await call_next(request)

    OrganizationApi(repo, auth, system_admin_checker=lambda actor: actor.user_id == "configured-admin",
        policy_version="organization-api-test-policy").mount(app, lambda _request: current["principal"])
    secret = "s39-etag-synthetic-secret"
    responses = []

    def checked(result, status):
        assert result.status_code == status, result.text
        etag = result.headers.get("ETag", "")
        assert re.fullmatch(r'"[\x21\x23-\x7e]+"', etag), etag
        # 응답에서 독립적으로 얻은 실제 bytes를 해시해 trace까지 포함한 표현 결속을 확인한다.
        assert etag == '"organization:' + hashlib.sha256(result.content).hexdigest() + '"'
        assert secret not in result.text + etag and "code_digest" not in result.text
        responses.append(result)
        return result

    try:
        with TestClient(app) as client:
            tenants = checked(client.get("/api/v1/organization/admin/tenants"), 200)
            repeated = checked(client.get("/api/v1/organization/admin/tenants"), 200)
            assert repeated.content == tenants.content and repeated.headers["ETag"] == tenants.headers["ETag"]
            traced = checked(client.get("/api/v1/organization/admin/tenants", headers={"X-Trace-Id": "etag-trace-b"}), 200)
            assert traced.json()["data"] == tenants.json()["data"]
            assert traced.json()["meta"]["trace_id"] == "etag-trace-b"
            assert traced.headers["ETag"] != tenants.headers["ETag"]
            checked(client.get("/api/v1/organization/tenants/tenant-a/workspaces"), 200)
            before = checked(client.get("/api/v1/organization/tenants/tenant-a/invitations"), 200)
            invitation_body = {"workspace_id": "workspace-a", "code": secret,
                "expires_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), "max_uses": 2}
            headers = {"Idempotency-Key": "l3-etag-invitation-0001", "X-Daon-Expected-Session-Id": "system-session"}
            created = checked(client.post("/api/v1/organization/tenants/tenant-a/invitations", json=invitation_body, headers=headers), 201)
            replay = checked(client.post("/api/v1/organization/tenants/tenant-a/invitations", json=invitation_body, headers=headers), 201)
            assert replay.json() == created.json()
            # 기존 영수증의 정렬된 키 순서는 보존한다. bytes가 다르면 표현 ETag도 달라야 한다.
            assert (replay.headers["ETag"] == created.headers["ETag"]) == (replay.content == created.content)
            after = checked(client.get("/api/v1/organization/tenants/tenant-a/invitations"), 200)
            assert before.json()["data"] == [] and len(after.json()["data"]) == 1
            assert after.headers["ETag"] != before.headers["ETag"]
            current["principal"] = IdentityPrincipal("member", "member-session", "member-device", "personal")
            joined = checked(client.post("/api/v1/organization/join-requests", json={"invitation_code": secret},
                headers={"Idempotency-Key": "l3-etag-join-0001", "X-Daon-Expected-Session-Id": "member-session"}), 202)
            current["principal"] = admin
            checked(client.post(f"/api/v1/organization/join-requests/{joined.json()['data']['request_id']}/decision",
                json={"approved": True, "expected_version": 1}, headers=headers | {"Idempotency-Key": "l3-etag-decision-0001"}), 200)
            role = checked(client.patch("/api/v1/organization/tenants/tenant-a/members/member/role",
                json={"workspace_id": "workspace-a", "role": "editor", "expected_version": 1, "expected_auth_version": 1, "reason": "업무 조정"},
                headers=headers | {"Idempotency-Key": "l3-etag-role-0001"}), 200)
            state = checked(client.patch("/api/v1/organization/tenants/tenant-a/members/member/state?active=false",
                json={"expected_version": 2}, headers=headers | {"Idempotency-Key": "l3-etag-state-0001"}), 200)
            assert state.json()["data"]["state"] == "suspended" and state.headers["ETag"] != role.headers["ETag"]
            revoked = checked(client.delete(f"/api/v1/organization/tenants/tenant-a/invitations/{created.json()['data']['invitation_id']}?expected_version=2",
                headers=headers | {"Idempotency-Key": "l3-etag-revoke-0001"}), 200)
            assert revoked.json()["data"]["state"] == "revoked" and revoked.headers["ETag"] != created.headers["ETag"]
            # 범위 밖 기존 응답은 ETag/조건부 GET 계약을 새로 붙이지 않는다.
            legacy = client.post("/api/v1/organization/creation-requests", json={"organization_name": "ETag 범위 밖", "organization_identifier": "etag-excluded"},
                headers={"Idempotency-Key": "l3-etag-excluded-0001"})
            assert legacy.status_code == 202 and "ETag" not in legacy.headers
            assert "ETag" not in client.get("/api/v1/organization/tenants/tenant-a/members").headers
            assert "ETag" not in client.get("/api/v1/organization/join-requests?tenant_id=tenant-a").headers
        with repo.transaction() as connection:
            digest = connection.execute("SELECT code_digest FROM invitation_codes").fetchone()[0]
            assert connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0] == 6
        assert all(digest not in result.text + result.headers["ETag"] for result in responses)
    finally:
        repo.close(); auth.close()
