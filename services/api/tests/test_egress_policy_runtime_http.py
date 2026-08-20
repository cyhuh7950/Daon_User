from __future__ import annotations

import tempfile
import asyncio
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.egress_policy import (
    EgressPolicyContext, EgressPolicyPayload, EgressPolicyService,
    ReferenceEgressPolicyRepository,
)
from daon_user_api.identity import ClientKind, IdentityError, IdentityPrincipal
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app
from test_identity_support import POLICY_VERSION, create_service


def test_org_admin_uses_organization_etag_and_workspace_admin_is_denied() -> None:
    asyncio.run(_exercise_org_policy_http())


async def _exercise_org_policy_http() -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "runtime.sqlite3"
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
        auth_repository = SqliteAuthorizationRepository(db_path)
        auth_repository.bootstrap_workspace(
            tenant_id="tenant-001", workspace_id="workspace-001",
            owner_user_id="org-admin", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=1000, now=clock(),
        )
        with auth_repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_memberships "
                "(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?, 'active',1,?)",
                ("tenant-001", "workspace-001", "workspace-admin",
                 Role.WORKSPACE_ADMIN.value, clock().isoformat()),
            )
        authorization = AuthorizationService(
            repository=auth_repository, audit_store=audit, clock=clock,
            identity_service=identity,
        )
        policy_repository = ReferenceEgressPolicyRepository()
        policy_context = EgressPolicyContext(
            "tenant-001", "tenant-001", "workspace-001", "org-admin",
            "trace-001", POLICY_VERSION,
        )
        policy_repository.seed(
            policy_context, scope_type="organization",
            payload=EgressPolicyPayload.deny_external(),
        )
        policy_repository.seed(
            policy_context, scope_type="workspace",
            payload=EgressPolicyPayload.deny_external(),
        )
        dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
            identity_service=identity, authorization_service=authorization,
            audit_store=audit, identity_repository=identity_repository,
            authorization_repository=auth_repository,
            egress_policy_service=EgressPolicyService(policy_repository),
        )
        principal = IdentityPrincipal("org-admin", "session-001", "device-001", "tenant-001")
        session_view = type("SessionView", (), {"client_kind": ClientKind.WEB, "principal": principal})()
        with patch.object(identity, "describe_access", return_value=session_view), patch.object(
            identity, "consume_step_up", return_value=None,
        ) as consume_step_up:
          async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)), base_url="http://test",
          ) as client:
            current = await client.get(
                "/api/v1/workspaces/workspace-001/egress-policy",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
            )
            assert current.status_code == 200
            organization_etag = current.json()["data"]["organization_etag"]
            for invalid_key in ("x" * 15, "x" * 129, "unsafe key value"):
                invalid = await client.post(
                    "/api/v1/organizations/tenant-001/egress-policy-versions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"If-Match": organization_etag, "Idempotency-Key": invalid_key},
                    json={
                        "mode": "deny_external", "allowed_provider_kinds": [],
                        "allowed_destinations": [], "classification": "restricted",
                        "max_bytes": 0, "masking_required": True, "redaction_required": True,
                        "required_approver": "organization_admin",
                        "step_up_authorization_id": "step-up-invalid",
                    },
                )
                assert invalid.status_code == 400
                assert policy_repository.write_count == 0
            response = await client.post(
                "/api/v1/organizations/tenant-001/egress-policy-versions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={"If-Match": organization_etag, "Idempotency-Key": "org-policy-00001"},
                json={
                    "mode": "deny_external", "allowed_provider_kinds": [],
                    "allowed_destinations": [], "classification": "restricted",
                    "max_bytes": 0, "masking_required": True, "redaction_required": True,
                    "required_approver": "organization_admin",
                    "step_up_authorization_id": "step-up-1",
                },
            )
            assert response.status_code == 201, response.text
            assert policy_repository.write_count == 1
            organization_step_up = consume_step_up.call_args_list[-1].kwargs
            assert organization_step_up["action_group"] == "organization_security_or_connector_policy_change"
            assert organization_step_up["target_id"] == "tenant-001"
            assert organization_step_up["operation"] == "egress_policy.organization.activate"
            assert organization_step_up["idempotency_key"] == "org-policy-00001"
            refreshed = await client.get(
                "/api/v1/workspaces/workspace-001/egress-policy",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
            )
            workspace_response = await client.post(
                "/api/v1/workspaces/workspace-001/egress-policy-versions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={
                    "If-Match": refreshed.json()["data"]["workspace_etag"],
                    "Idempotency-Key": "workspace-policy-00001",
                },
                json={
                    "mode": "deny_external", "allowed_provider_kinds": [],
                    "allowed_destinations": [], "classification": "restricted",
                    "max_bytes": 0, "masking_required": True, "redaction_required": True,
                    "required_approver": "organization_admin",
                    "step_up_authorization_id": "step-up-workspace-1",
                },
            )
            assert workspace_response.status_code == 201, workspace_response.text
            assert workspace_response.json()["data"]["scope_type"] == "workspace"
            assert policy_repository.write_count == 2
            workspace_step_up = consume_step_up.call_args_list[-1].kwargs
            assert workspace_step_up["action_group"] == "organization_security_or_connector_policy_change"
            assert workspace_step_up["target_id"] == "workspace-001"
            assert workspace_step_up["operation"] == "egress_policy.workspace.activate"
            assert workspace_step_up["idempotency_key"] == "workspace-policy-00001"
            consume_count_before_denial = consume_step_up.call_count
            workspace_admin = IdentityPrincipal(
                "workspace-admin", "session-002", "device-002", "tenant-001",
            )
            with patch.object(
                identity, "describe_access",
                return_value=type("SessionView", (), {
                    "client_kind": ClientKind.WEB, "principal": workspace_admin,
                })(),
            ):
                denied = await client.post(
                    "/api/v1/organizations/tenant-001/egress-policy-versions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={"If-Match": response.headers["etag"], "Idempotency-Key": "org-policy-00002"},
                    json={
                        "mode": "deny_external", "allowed_provider_kinds": [],
                        "allowed_destinations": [], "classification": "restricted",
                        "max_bytes": 0, "masking_required": True, "redaction_required": True,
                        "required_approver": "organization_admin",
                        "step_up_authorization_id": "step-up-2",
                    },
                )
            assert denied.status_code == 403
            assert policy_repository.write_count == 2
            assert consume_step_up.call_count == consume_count_before_denial

            consume_step_up.side_effect = IdentityError("STEP_UP_SCOPE_MISMATCH", 403)
            wrong_target = await client.post(
                "/api/v1/workspaces/workspace-001/egress-policy-versions",
                cookies={WEB_SESSION_COOKIE: "opaque-session"},
                headers={
                    "If-Match": workspace_response.headers["etag"],
                    "Idempotency-Key": "workspace-policy-00002",
                },
                json={
                    "mode": "deny_external", "allowed_provider_kinds": [],
                    "allowed_destinations": [], "classification": "restricted",
                    "max_bytes": 0, "masking_required": True, "redaction_required": True,
                    "required_approver": "organization_admin",
                    "step_up_authorization_id": "wrong-target-step-up",
                },
            )
            assert wrong_target.status_code == 403
            assert wrong_target.json()["error"]["code"] == "INVALID_REQUEST"
            assert policy_repository.write_count == 2
        dependencies.close()
