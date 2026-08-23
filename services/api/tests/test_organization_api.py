from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from daon_user_api.authorization import Role, SqliteAuthorizationRepository
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.organization_api import OrganizationApi
from daon_user_api.organization_membership import SqliteOrganizationRepository
from daon_user_api.organization_membership import OrganizationWorkflowError


def _app(tmp_path: Path, principal: IdentityPrincipal) -> tuple[FastAPI, SqliteOrganizationRepository, SqliteAuthorizationRepository]:
    auth = SqliteAuthorizationRepository(tmp_path / "auth.sqlite")
    now = datetime.now(timezone.utc)
    auth.bootstrap_workspace(
        tenant_id="tenant-a", workspace_id="workspace-a", owner_user_id="admin",
        owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync",
        cost_limit_cents=1000, now=now,
    )
    repo = SqliteOrganizationRepository(tmp_path / "org.sqlite")
    app = FastAPI()
    @app.exception_handler(OrganizationWorkflowError)
    async def workflow_error(_request: Request, error: OrganizationWorkflowError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content={"code": error.code})
    OrganizationApi(repo, auth, system_admin_checker=lambda value: value.user_id == "configured-admin").mount(app, lambda _request: principal)
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
            "code": "invite-secret", "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "max_uses": 1,
        }, headers={"Idempotency-Key": "invitation-create-0001"})
        assert created.status_code == 201
        data = created.json()["data"]
        assert data["code_digest"] != "invite-secret"
        revoked = client.delete(f"/api/v1/organization/tenants/tenant-a/invitations/{data['invitation_id']}", headers={"Idempotency-Key": "invitation-revoke-0001"})
        assert revoked.status_code == 200
        assert revoked.json()["data"]["state"] == "revoked"
    repo.close(); auth.close()
