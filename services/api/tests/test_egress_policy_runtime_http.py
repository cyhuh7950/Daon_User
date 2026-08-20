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


def test_personal_owner_uses_server_selected_personal_workspace() -> None:
    asyncio.run(_exercise_personal_owner_policy_http())


def test_tenant_policy_multiple_workspace_candidates_fail_before_step_up() -> None:
    asyncio.run(_exercise_ambiguous_tenant_policy_http())


async def _exercise_ambiguous_tenant_policy_http() -> None:
    cases = (
        ("personal", Role.PERSONAL_OWNER),
        ("organization", Role.ORGANIZATION_ADMIN),
    )
    with tempfile.TemporaryDirectory() as directory:
        for index, (workspace_kind, owner_role) in enumerate(cases):
            db_path = Path(directory) / f"runtime-{index}.sqlite3"
            audit = AuditEventStore()
            identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
            auth_repository = SqliteAuthorizationRepository(db_path)
            tenant_id = f"tenant-ambiguous-{index}"
            user_id = f"owner-ambiguous-{index}"
            for workspace_index in (1, 2):
                auth_repository.bootstrap_workspace(
                    tenant_id=tenant_id,
                    workspace_id=f"workspace-ambiguous-{index}-{workspace_index}",
                    owner_user_id=user_id, owner_role=owner_role,
                    workspace_kind=workspace_kind, data_area="local_private",
                    cost_limit_cents=0, now=clock(),
                )
            policy_repository = ReferenceEgressPolicyRepository()
            policy_context = EgressPolicyContext(
                tenant_id, tenant_id, f"workspace-ambiguous-{index}-1",
                user_id, f"trace-ambiguous-{index}", POLICY_VERSION,
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
                settings=RuntimeSettings.for_test(
                    database_path=db_path, policy_version=POLICY_VERSION,
                ),
                identity_service=identity,
                authorization_service=AuthorizationService(
                    repository=auth_repository, audit_store=audit, clock=clock,
                    identity_service=identity,
                ),
                audit_store=audit, identity_repository=identity_repository,
                authorization_repository=auth_repository,
                egress_policy_service=EgressPolicyService(policy_repository),
            )
            principal = IdentityPrincipal(
                user_id, f"session-{index}", f"device-{index}", tenant_id,
            )
            session_view = type(
                "SessionView", (), {"client_kind": ClientKind.WEB, "principal": principal},
            )()
            with patch.object(identity, "describe_access", return_value=session_view), patch.object(
                identity, "consume_step_up", return_value=None,
            ) as consume_step_up:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(dependencies)),
                    base_url="http://test",
                ) as client:
                    denied = await client.post(
                        f"/api/v1/organizations/{tenant_id}/egress-policy-versions",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={
                            "If-Match": '"egress-policy:1"',
                            "Idempotency-Key": f"ambiguous-policy-{index:05d}",
                        },
                        json={
                            "mode": "deny_external", "allowed_provider_kinds": [],
                            "allowed_destinations": [], "classification": "restricted",
                            "max_bytes": 0, "masking_required": True,
                            "redaction_required": True,
                            "required_approver": "organization_admin",
                            "step_up_authorization_id": f"step-up-ambiguous-{index}",
                        },
                    )
                    assert consume_step_up.call_count == 0
                    assert policy_repository.write_count == 0
                    assert denied.status_code == 403
                    assert denied.json()["error"]["code"] == "FORBIDDEN"
            dependencies.close()


async def _exercise_personal_owner_policy_http() -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "runtime.sqlite3"
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
        auth_repository = SqliteAuthorizationRepository(db_path)
        auth_repository.bootstrap_workspace(
            tenant_id="tenant-personal", workspace_id="workspace-personal",
            owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
            workspace_kind="personal", data_area="local_private",
            cost_limit_cents=0, now=clock(),
        )
        authorization = AuthorizationService(
            repository=auth_repository, audit_store=audit, clock=clock,
            identity_service=identity,
        )
        policy_repository = ReferenceEgressPolicyRepository()
        context = EgressPolicyContext(
            "tenant-personal", "tenant-personal", "workspace-personal",
            "personal-owner", "trace-personal", POLICY_VERSION,
        )
        policy_repository.seed(
            context, scope_type="organization", payload=EgressPolicyPayload.deny_external(),
        )
        policy_repository.seed(
            context, scope_type="workspace", payload=EgressPolicyPayload.deny_external(),
        )
        dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
            identity_service=identity, authorization_service=authorization,
            audit_store=audit, identity_repository=identity_repository,
            authorization_repository=auth_repository,
            egress_policy_service=EgressPolicyService(policy_repository),
        )
        principal = IdentityPrincipal(
            "personal-owner", "session-personal", "device-personal", "tenant-personal",
        )
        session_view = type(
            "SessionView", (), {"client_kind": ClientKind.WEB, "principal": principal},
        )()
        with patch.object(identity, "describe_access", return_value=session_view), patch.object(
            identity, "consume_step_up", return_value=None,
        ) as consume_step_up:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(dependencies)), base_url="http://test",
            ) as client:
                current = await client.get(
                    "/api/v1/workspaces/workspace-personal/egress-policy",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                )
                assert current.status_code == 200
                response = await client.post(
                    "/api/v1/organizations/tenant-personal/egress-policy-versions",
                    cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    headers={
                        "If-Match": current.json()["data"]["organization_etag"],
                        "Idempotency-Key": "personal-policy-00001",
                    },
                    json={
                        "mode": "deny_external", "allowed_provider_kinds": [],
                        "allowed_destinations": [], "classification": "restricted",
                        "max_bytes": 0, "masking_required": True, "redaction_required": True,
                        "required_approver": "organization_admin",
                        "step_up_authorization_id": "step-up-personal",
                    },
                )
                assert response.status_code == 201, response.text
                assert response.json()["data"]["scope_type"] == "organization"
                assert policy_repository.write_count == 1
                consumed = consume_step_up.call_args.kwargs
                assert consumed["action_group"] == "organization_security_or_connector_policy_change"
                assert consumed["target_id"] == "tenant-personal"
                assert consumed["operation"] == "egress_policy.organization.activate"
                assert consumed["idempotency_key"] == "personal-policy-00001"
        dependencies.close()


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
            connection.execute(
                "INSERT INTO auth_memberships "
                "(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?, 'active',1,?)",
                ("tenant-001", "workspace-001", "workspace-member",
                 Role.VIEWER.value, clock().isoformat()),
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
            for index, user_id in enumerate(("workspace-admin", "workspace-member"), start=2):
                workspace_actor = IdentityPrincipal(
                    user_id, f"session-00{index}", f"device-00{index}", "tenant-001",
                )
                with patch.object(
                    identity, "describe_access",
                    return_value=type("SessionView", (), {
                        "client_kind": ClientKind.WEB, "principal": workspace_actor,
                    })(),
                ):
                    denied = await client.post(
                        "/api/v1/organizations/tenant-001/egress-policy-versions",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={
                            "If-Match": response.headers["etag"],
                            "Idempotency-Key": f"org-policy-0000{index}",
                        },
                        json={
                            "mode": "deny_external", "allowed_provider_kinds": [],
                            "allowed_destinations": [], "classification": "restricted",
                            "max_bytes": 0, "masking_required": True, "redaction_required": True,
                            "required_approver": "organization_admin",
                            "step_up_authorization_id": f"step-up-{index}",
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
