"""HTTP-facing organization membership workflow contract.

The handlers are mounted by ``runtime.create_app``.  They deliberately use the
existing identity principal and authorization repository; no new login or
workspace authorization path is introduced here.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .authorization import Role, SqliteAuthorizationRepository
from .identity import IdentityPrincipal, SqliteIdentityRepository
from .organization_membership import (
    OrganizationWorkflowError, SqliteOrganizationRepository, TenantMembership,
)


class OrganizationCreationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_name: str = Field(min_length=1, max_length=160)
    organization_identifier: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._:-]{0,127}$"
    )


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)
    role: str = "member"


class JoinBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitation_code: str = Field(min_length=1, max_length=160)


class InvitationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=160)
    expires_at: datetime
    max_uses: int = Field(ge=1, le=1000000)
    workspace_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class MembershipBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class RoleBody(MembershipBody):
    role: str = Field(pattern=r"^(viewer|editor|reviewer|approver)$")
    workspace_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    expected_auth_version: int = Field(ge=1)


class OrganizationApi:
    def __init__(self, repository: SqliteOrganizationRepository, authorization: SqliteAuthorizationRepository,
                 now: Callable[[], datetime] | None = None,
                 system_admin_checker: Callable[[IdentityPrincipal], bool] | None = None,
                 organization_provisioner: Callable[[Any], None] | None = None,
                 identity_repository: SqliteIdentityRepository | None = None,
                 management_available: Callable[[], bool] | None = None,
                 *, policy_version: str) -> None:
        self.repository = repository
        self.authorization = authorization
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.system_admin_checker = system_admin_checker or (lambda _principal: False)
        self.organization_provisioner = organization_provisioner
        self.identity_repository = identity_repository
        self.policy_version = policy_version
        self.management_available = management_available

    @staticmethod
    def _tenant_id(identifier: str) -> str:
        normalized = identifier.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,127}", normalized):
            return ""
        return f"tenant-{normalized}"

    def _tenant_role(self, tenant_id: str, user_id: str) -> str | None:
        with self.authorization.transaction() as connection:
            row = connection.execute(
                "SELECT role FROM auth_tenant_roles WHERE tenant_id=? AND user_id=? AND state='active'",
                (tenant_id, user_id),
            ).fetchone()
        return None if row is None else str(row[0])

    def require_org_admin(self, principal: IdentityPrincipal, tenant_id: str) -> None:
        if self.system_admin_checker(principal):
            if not self._workspaces(tenant_id):
                raise OrganizationWorkflowError("ORGANIZATION_NOT_FOUND", 404)
            return
        if principal.tenant_id != tenant_id:
            raise OrganizationWorkflowError("CROSS_TENANT_ACCESS", 403)
        if self._tenant_role(tenant_id, principal.user_id) not in {Role.ORGANIZATION_ADMIN.value, Role.PERSONAL_OWNER.value}:
            raise OrganizationWorkflowError("ORGANIZATION_ADMIN_REQUIRED", 403)

    def _workspaces(self, tenant_id: str) -> list[dict]:
        from .postgres_adapters import PostgresAuthorizationRepository
        with self.authorization.transaction() as connection:
            rows = connection.execute("SELECT workspace_id FROM auth_workspaces WHERE tenant_id=? AND workspace_kind='organization' ORDER BY workspace_id", (tenant_id,)).fetchall()
            result = []
            for row in rows:
                identifier = str(row["workspace_id"])
                name = identifier
                if isinstance(self.authorization, PostgresAuthorizationRepository):
                    connection.execute("SELECT set_config('app.tenant_id',?,true),set_config('app.workspace_id',?,true)", (tenant_id, identifier))
                    named = connection.execute("SELECT display_name FROM public.workspaces WHERE tenant_id=? AND workspace_id=?", (tenant_id, identifier)).fetchone()
                    if named is not None: name = str(named["display_name"])
                result.append({"workspace_id": identifier, "display_name": name})
            return result

    def _managed_tenants(self) -> list[dict]:
        from .postgres_adapters import PostgresAuthorizationRepository
        with self.authorization.transaction() as connection:
            rows = connection.execute("SELECT DISTINCT tenant_id FROM auth_workspaces WHERE workspace_kind='organization' ORDER BY tenant_id").fetchall()
            result = []
            for row in rows:
                identifier = str(row["tenant_id"])
                name = identifier
                if isinstance(self.authorization, PostgresAuthorizationRepository):
                    # 검증된 대상마다 Foundation RLS scope만 설정하며 principal은 바꾸지 않는다.
                    connection.execute("SELECT set_config('app.tenant_id',?,true)", (identifier,))
                    named = connection.execute("SELECT display_name FROM public.tenants WHERE tenant_id=?", (identifier,)).fetchone()
                    if named is not None: name = str(named["display_name"])
                result.append({"tenant_id": identifier, "display_name": name})
            return result

    @staticmethod
    def _json(value: Any) -> Any:
        if is_dataclass(value):
            return {key: OrganizationApi._json(item) for key, item in asdict(value).items()}
        if isinstance(value, (tuple, list)):
            return [OrganizationApi._json(item) for item in value]
        if hasattr(value, "value"):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def mount(self, router: APIRouter, principal: Callable[[Request], IdentityPrincipal]) -> None:
        def mutation_actor(request: Request,
            expected_session: str = Header(alias="X-Daon-Expected-Session-Id", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
            operation_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128)) -> IdentityPrincipal:
            actor = principal(request)
            if len(request.headers.getlist("x-daon-expected-session-id")) != 1 or len(request.headers.getlist("idempotency-key")) != 1:
                raise OrganizationWorkflowError("INVALID_INPUT", 422)
            if actor.session_id != expected_session:
                raise OrganizationWorkflowError("ACCOUNT_SESSION_CHANGED", 409)
            return actor

        def response(request: Request, data: Any) -> dict[str, Any]:
            return {"data": self._json(data), "meta": {"trace_id": getattr(request.state, "trace_id", "test-trace")}}

        def management_response(request: Request, data: Any, status_code: int = 200) -> JSONResponse:
            result = JSONResponse(content=response(request, data), status_code=status_code)
            # trace를 포함한 실제 전송 표현 전체를 결속하며 입력 원문은 해시하지 않는다.
            result.headers["ETag"] = '"organization:' + hashlib.sha256(result.body).hexdigest() + '"'
            return result

        def mutation(request: Request, actor: IdentityPrincipal, operation: str, payload: Any) -> tuple[str, str, Any | None]:
            key = request.headers.get("Idempotency-Key", "")
            if not 16 <= len(key) <= 128:
                raise OrganizationWorkflowError("IDEMPOTENCY_KEY_INVALID")
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
            replay = self.repository.idempotency_replay(operation=operation, actor_id=actor.user_id, key=key, fingerprint=fingerprint)
            return key, fingerprint, None if replay is None else json.loads(replay)

        def record(actor: IdentityPrincipal, operation: str, key: str, fingerprint: str, value: Any) -> None:
            self.repository.idempotency_record(operation=operation, actor_id=actor.user_id, key=key, fingerprint=fingerprint, result=self._json(value), now=self.now())

        def management(request: Request, actor: IdentityPrincipal, operation: str,
                       tenant_id: str | None, target_id: str | None, payload: dict,
                       status_code: int = 200) -> JSONResponse:
            if self.management_available is not None and not self.management_available():
                raise OrganizationWorkflowError("ORGANIZATION_WORKFLOW_UNAVAILABLE", 503)
            # 기존 요청의 지문을 유지하고 업무·안전 DTO·영수증·감사의도를 함께 저장한다.
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
            result = self.repository.execute_management_operation(
                operation=operation, actor_id=actor.user_id, tenant_id=tenant_id,
                target_id=target_id, payload=payload,
                idempotency_key=request.headers.get("Idempotency-Key", ""), request_fingerprint=fingerprint,
                trace_id=getattr(request.state, "trace_id", "test-trace"), policy_version=self.policy_version,
            )
            return management_response(request, result.value, status_code)

        @router.get("/api/v1/organization/admin/tenants")
        async def managed_tenants(request: Request) -> JSONResponse:
            actor = principal(request)
            if not self.system_admin_checker(actor):
                raise OrganizationWorkflowError("SYSTEM_ADMIN_REQUIRED", 403)
            if request.query_params: raise OrganizationWorkflowError("INVALID_INPUT", 422)
            return management_response(request, self._managed_tenants())

        @router.get("/api/v1/organization/tenants/{tenant_id}/workspaces")
        async def managed_workspaces(tenant_id: str, request: Request) -> JSONResponse:
            self.require_org_admin(principal(request), tenant_id)
            if request.query_params: raise OrganizationWorkflowError("INVALID_INPUT", 422)
            values = self._workspaces(tenant_id)
            if not values: raise OrganizationWorkflowError("ORGANIZATION_NOT_FOUND", 404)
            return management_response(request, values)

        @router.get("/api/v1/organization/tenants/{tenant_id}/invitations")
        async def invitations(tenant_id: str, request: Request) -> JSONResponse:
            self.require_org_admin(principal(request), tenant_id)
            if request.query_params: raise OrganizationWorkflowError("INVALID_INPUT", 422)
            if not self._workspaces(tenant_id): raise OrganizationWorkflowError("ORGANIZATION_NOT_FOUND", 404)
            return management_response(request, self.repository.list_invitations(tenant_id=tenant_id, now=self.now()))

        @router.post("/api/v1/organization/creation-requests", status_code=202)
        async def create(body: OrganizationCreationBody, request: Request) -> dict[str, Any]:
            actor = principal(request)
            key, fingerprint, replay = mutation(request, actor, "organization.creation.create", body.model_dump())
            if replay is not None:
                return response(request, replay)
            item = self.repository.create_organization_request(
                applicant_user_id=actor.user_id, organization_name=body.organization_name,
                organization_identifier=body.organization_identifier, now=self.now(),
            )
            record(actor, "organization.creation.create", key, fingerprint, item)
            return response(request, item)

        @router.get("/api/v1/organization/creation-requests")
        async def list_creation(request: Request) -> dict[str, Any]:
            actor = principal(request)
            # The system administrator identity is provisioned outside tenant scope.
            items = self.repository.list_creation_requests(
                applicant_user_id=None if self.system_admin_checker(actor) else actor.user_id
            )
            return response(request, items)

        @router.get("/api/v1/admin/directory")
        async def directory(request: Request) -> dict[str, Any]:
            actor = principal(request)
            if not self.system_admin_checker(actor):
                raise OrganizationWorkflowError("SYSTEM_ADMIN_REQUIRED", 403)
            organizations = [
                {
                    "tenant_id": tenant_id,
                    "name": item.requested_org_name,
                    "identifier": item.requested_org_identifier,
                    "state": item.state.value,
                    "applicant_user_id": item.applicant_user_id,
                }
                for item in self.repository.list_creation_requests()
                if item.state.value == "approved"
                and (tenant_id := self._tenant_id(item.requested_org_identifier))
            ]
            users = () if self.identity_repository is None else self.identity_repository.list_directory_users()
            return response(request, {"organizations": organizations, "users": users})

        @router.post("/api/v1/organization/creation-requests/{request_id}/decision")
        async def decide_creation(request_id: str, body: DecisionBody, request: Request) -> dict[str, Any]:
            actor = principal(request)
            if not self.system_admin_checker(actor):
                raise OrganizationWorkflowError("SYSTEM_ADMIN_REQUIRED", 403)
            key, fingerprint, replay = mutation(request, actor, "organization.creation.decide", {"request_id": request_id, **body.model_dump()})
            if replay is not None:
                return response(request, replay)
            item = self.repository.decide_organization_request(
                request_id=request_id, actor_id=actor.user_id, approved=body.approved,
                expected_version=body.expected_version, reason=body.reason, now=self.now(),
            )
            if body.approved and self.organization_provisioner is not None:
                self.organization_provisioner(item)
            record(actor, "organization.creation.decide", key, fingerprint, item)
            return response(request, item)

        @router.post("/api/v1/organization/join-requests", status_code=202)
        async def join(body: JoinBody, request: Request, actor: IdentityPrincipal = Depends(mutation_actor)) -> JSONResponse:
            if request.query_params:
                raise OrganizationWorkflowError("INVALID_INPUT", 400)
            # 공개 입력은 code뿐이며 기존 code-only 영수증의 지문은 유지한다.
            payload = {"tenant_id": None, "invitation_code": body.invitation_code}
            return management(request, actor, "organization.join.create", None, None, payload, status_code=202)

        @router.get("/api/v1/organization/join-requests")
        async def list_join(request: Request, tenant_id: str | None = None) -> dict[str, Any]:
            actor = principal(request)
            if any(name != "tenant_id" for name in request.query_params) or len(request.query_params.getlist("tenant_id")) > 1:
                raise OrganizationWorkflowError("INVALID_INPUT", 400)
            if tenant_id is not None:
                self.require_org_admin(actor, tenant_id)
                items = self.repository.list_join_requests(tenant_id=tenant_id)
            else:
                items = self.repository.list_join_requests(user_id=actor.user_id)
                return management_response(request, items)
            return response(request, items)

        @router.post("/api/v1/organization/join-requests/{request_id}/decision")
        async def decide_join(request_id: str, body: DecisionBody, request: Request, actor: IdentityPrincipal = Depends(mutation_actor)) -> JSONResponse:
            # Resolve tenant from the request itself without accepting a caller tenant.
            with self.repository.transaction() as connection:
                row = connection.execute("SELECT tenant_id FROM organization_join_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise OrganizationWorkflowError("REQUEST_NOT_FOUND", 404)
            tenant_id = str(row[0]); self.require_org_admin(actor, tenant_id)
            return management(request, actor, "organization.join.decide", tenant_id, request_id, {"request_id": request_id, **body.model_dump()})

        @router.post("/api/v1/organization/tenants/{tenant_id}/invitations", status_code=201)
        async def invite(tenant_id: str, body: InvitationBody, request: Request, actor: IdentityPrincipal = Depends(mutation_actor)) -> JSONResponse:
            self.require_org_admin(actor, tenant_id)
            if body.workspace_id not in {item["workspace_id"] for item in self._workspaces(tenant_id)}:
                raise OrganizationWorkflowError("ORGANIZATION_INVITATION_SCOPE_REQUIRED", 409)
            return management(request, actor, "organization.invitation.create", tenant_id, None, {"tenant_id": tenant_id, **body.model_dump()}, status_code=201)

        @router.delete("/api/v1/organization/tenants/{tenant_id}/invitations/{invitation_id}")
        async def revoke(tenant_id: str, invitation_id: str, request: Request, expected_version: int = Query(ge=1), actor: IdentityPrincipal = Depends(mutation_actor)) -> JSONResponse:
            self.require_org_admin(actor, tenant_id)
            return management(request, actor, "organization.invitation.revoke", tenant_id, invitation_id, {"tenant_id": tenant_id, "invitation_id": invitation_id, "expected_version": expected_version})

        @router.get("/api/v1/organization/tenants/{tenant_id}/members")
        async def members(tenant_id: str, request: Request) -> dict[str, Any]:
            actor = principal(request); self.require_org_admin(actor, tenant_id)
            return response(request, self.repository.list_members(tenant_id))

        @router.patch("/api/v1/organization/tenants/{tenant_id}/members/{user_id}/role")
        async def role(tenant_id: str, user_id: str, body: RoleBody, request: Request, actor: IdentityPrincipal = Depends(mutation_actor)) -> JSONResponse:
            self.require_org_admin(actor, tenant_id)
            return management(request, actor, "organization.member.role", tenant_id, user_id, {"tenant_id": tenant_id, "user_id": user_id, **body.model_dump()})

        @router.patch("/api/v1/organization/tenants/{tenant_id}/members/{user_id}/state")
        async def state(tenant_id: str, user_id: str, body: MembershipBody, request: Request, active: bool = True, actor: IdentityPrincipal = Depends(mutation_actor)) -> JSONResponse:
            self.require_org_admin(actor, tenant_id)
            return management(request, actor, "organization.member.state", tenant_id, user_id, {"tenant_id": tenant_id, "user_id": user_id, "active": active, **body.model_dump()})
