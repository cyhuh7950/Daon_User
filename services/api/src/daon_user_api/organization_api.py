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
from typing import Any, Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from .authorization import Role, SqliteAuthorizationRepository
from .identity import IdentityPrincipal
from .organization_membership import (
    MembershipState, OrganizationWorkflowError, SqliteOrganizationRepository,
)


class OrganizationCreationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_name: str = Field(min_length=1, max_length=160)
    organization_identifier: str = Field(min_length=1, max_length=160)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)
    role: str = "member"


class JoinBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str | None = Field(default=None, min_length=1, max_length=160)
    invitation_code: str | None = Field(default=None, min_length=1, max_length=160)


class InvitationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=160)
    expires_at: datetime
    max_uses: int = Field(ge=1, le=1000000)


class MembershipBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class RoleBody(MembershipBody):
    role: str = Field(min_length=1, max_length=64)


class OrganizationApi:
    def __init__(self, repository: SqliteOrganizationRepository, authorization: SqliteAuthorizationRepository,
                 now: Callable[[], datetime] | None = None,
                 system_admin_checker: Callable[[IdentityPrincipal], bool] | None = None) -> None:
        self.repository = repository
        self.authorization = authorization
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.system_admin_checker = system_admin_checker or (lambda _principal: False)

    def _tenant_role(self, tenant_id: str, user_id: str) -> str | None:
        with self.authorization.transaction() as connection:
            row = connection.execute(
                "SELECT role FROM auth_tenant_roles WHERE tenant_id=? AND user_id=? AND state='active'",
                (tenant_id, user_id),
            ).fetchone()
        return None if row is None else str(row[0])

    def require_org_admin(self, principal: IdentityPrincipal, tenant_id: str) -> None:
        if principal.tenant_id != tenant_id:
            raise OrganizationWorkflowError("CROSS_TENANT_ACCESS", 403)
        if self._tenant_role(tenant_id, principal.user_id) not in {Role.ORGANIZATION_ADMIN.value, Role.PERSONAL_OWNER.value}:
            raise OrganizationWorkflowError("ORGANIZATION_ADMIN_REQUIRED", 403)

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
        def response(request: Request, data: Any) -> dict[str, Any]:
            return {"data": self._json(data), "meta": {"trace_id": getattr(request.state, "trace_id", "test-trace")}}

        def mutation(request: Request, actor: IdentityPrincipal, operation: str, payload: Any) -> tuple[str, str, Any | None]:
            key = request.headers.get("Idempotency-Key", "")
            if not 16 <= len(key) <= 128:
                raise OrganizationWorkflowError("IDEMPOTENCY_KEY_INVALID")
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
            replay = self.repository.idempotency_replay(operation=operation, actor_id=actor.user_id, key=key, fingerprint=fingerprint)
            return key, fingerprint, None if replay is None else json.loads(replay)

        def record(actor: IdentityPrincipal, operation: str, key: str, fingerprint: str, value: Any) -> None:
            self.repository.idempotency_record(operation=operation, actor_id=actor.user_id, key=key, fingerprint=fingerprint, result=self._json(value), now=self.now())

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
            record(actor, "organization.creation.decide", key, fingerprint, item)
            return response(request, item)

        @router.post("/api/v1/organization/join-requests", status_code=202)
        async def join(body: JoinBody, request: Request) -> dict[str, Any]:
            actor = principal(request)
            if body.tenant_id is None and body.invitation_code is None:
                raise OrganizationWorkflowError("INVITATION_REQUIRED", 400)
            key, fingerprint, replay = mutation(request, actor, "organization.join.create", body.model_dump())
            if replay is not None:
                return response(request, replay)
            if body.tenant_id is None:
                item = self.repository.create_join_request_by_invitation(
                    user_id=actor.user_id, invitation_code=body.invitation_code or "", now=self.now(),
                )
            else:
                item = self.repository.create_join_request(
                    tenant_id=body.tenant_id, user_id=actor.user_id, invitation_code=body.invitation_code, now=self.now(),
                )
            record(actor, "organization.join.create", key, fingerprint, item)
            return response(request, item)

        @router.get("/api/v1/organization/join-requests")
        async def list_join(request: Request, tenant_id: str | None = None) -> dict[str, Any]:
            actor = principal(request)
            if tenant_id is not None:
                self.require_org_admin(actor, tenant_id)
                items = self.repository.list_join_requests(tenant_id=tenant_id)
            else:
                items = self.repository.list_join_requests(user_id=actor.user_id)
            return response(request, items)

        @router.post("/api/v1/organization/join-requests/{request_id}/decision")
        async def decide_join(request_id: str, body: DecisionBody, request: Request) -> dict[str, Any]:
            actor = principal(request)
            # Resolve tenant from the request itself without accepting a caller tenant.
            with self.repository.transaction() as connection:
                row = connection.execute("SELECT tenant_id FROM organization_join_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise OrganizationWorkflowError("REQUEST_NOT_FOUND", 404)
            tenant_id = str(row[0]); self.require_org_admin(actor, tenant_id)
            key, fingerprint, replay = mutation(request, actor, "organization.join.decide", {"request_id": request_id, **body.model_dump()})
            if replay is not None:
                return response(request, replay)
            item = self.repository.decide_join_request(
                request_id=request_id, actor_id=actor.user_id, approved=body.approved,
                expected_version=body.expected_version, reason=body.reason, role=body.role, now=self.now(),
            )
            record(actor, "organization.join.decide", key, fingerprint, item)
            return response(request, item)

        @router.post("/api/v1/organization/tenants/{tenant_id}/invitations", status_code=201)
        async def invite(tenant_id: str, body: InvitationBody, request: Request) -> dict[str, Any]:
            actor = principal(request); self.require_org_admin(actor, tenant_id)
            key, fingerprint, replay = mutation(request, actor, "organization.invitation.create", {"tenant_id": tenant_id, **body.model_dump()})
            if replay is not None:
                return response(request, replay)
            item = self.repository.create_invitation(
                tenant_id=tenant_id, created_by=actor.user_id, code=body.code,
                expires_at=body.expires_at, max_uses=body.max_uses, now=self.now(),
            )
            # Never return the plaintext invitation code or digest as a secret.
            record(actor, "organization.invitation.create", key, fingerprint, item)
            return response(request, item)

        @router.delete("/api/v1/organization/tenants/{tenant_id}/invitations/{invitation_id}")
        async def revoke(tenant_id: str, invitation_id: str, request: Request, expected_version: int = 1) -> dict[str, Any]:
            actor = principal(request); self.require_org_admin(actor, tenant_id)
            key, fingerprint, replay = mutation(request, actor, "organization.invitation.revoke", {"tenant_id": tenant_id, "invitation_id": invitation_id, "expected_version": expected_version})
            if replay is not None:
                return response(request, replay)
            item = self.repository.revoke_invitation(
                tenant_id=tenant_id, invitation_id=invitation_id, actor_id=actor.user_id,
                expected_version=expected_version, now=self.now(),
            )
            record(actor, "organization.invitation.revoke", key, fingerprint, item)
            return response(request, item)

        @router.get("/api/v1/organization/tenants/{tenant_id}/members")
        async def members(tenant_id: str, request: Request) -> dict[str, Any]:
            actor = principal(request); self.require_org_admin(actor, tenant_id)
            return response(request, self.repository.list_members(tenant_id))

        @router.patch("/api/v1/organization/tenants/{tenant_id}/members/{user_id}/role")
        async def role(tenant_id: str, user_id: str, body: RoleBody, request: Request) -> dict[str, Any]:
            actor = principal(request); self.require_org_admin(actor, tenant_id)
            key, fingerprint, replay = mutation(request, actor, "organization.member.role", {"tenant_id": tenant_id, "user_id": user_id, **body.model_dump()})
            if replay is not None:
                return response(request, replay)
            item = self.repository.change_role(
                tenant_id=tenant_id, user_id=user_id, actor_id=actor.user_id,
                role=body.role, expected_version=body.expected_version, reason=body.reason, now=self.now(),
            )
            record(actor, "organization.member.role", key, fingerprint, item)
            return response(request, item)

        @router.patch("/api/v1/organization/tenants/{tenant_id}/members/{user_id}/state")
        async def state(tenant_id: str, user_id: str, body: MembershipBody, request: Request, active: bool = True) -> dict[str, Any]:
            actor = principal(request); self.require_org_admin(actor, tenant_id)
            key, fingerprint, replay = mutation(request, actor, "organization.member.state", {"tenant_id": tenant_id, "user_id": user_id, "active": active, **body.model_dump()})
            if replay is not None:
                return response(request, replay)
            item = self.repository.set_membership(
                tenant_id=tenant_id, user_id=user_id, actor_id=actor.user_id,
                state=MembershipState.ACTIVE if active else MembershipState.SUSPENDED,
                expected_version=body.expected_version, reason=body.reason, now=self.now(),
            )
            record(actor, "organization.member.state", key, fingerprint, item)
            return response(request, item)
