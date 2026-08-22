from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_authorization_support import (
    FixedClock,
    POLICY_VERSION,
    TRACE_ID,
    FakeIdentityBoundary,
    SelectiveFailAuditStore,
    principal,
)
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import (
    AccessAction,
    AccessState,
    Action,
    AuthorizationError,
    AuthorizationService,
    EvidenceDependency,
    HistoricalResultDescriptor,
    Permission,
    PolicyEffect,
    Role,
    RoleScope,
    SqliteAuthorizationRepository,
)


class AuthorizationRoleScopeC01Tests(unittest.TestCase):
    def make_organization(self, path: Path):
        owner = principal("owner")
        identity = FakeIdentityBoundary(owner)
        repository = SqliteAuthorizationRepository(path)
        repository.bootstrap_workspace(
            tenant_id=owner.tenant_id, workspace_id="workspace-a", owner_user_id=owner.user_id,
            owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
            data_area="cloud_sync", cost_limit_cents=100, now=FixedClock()(),
        )
        service = AuthorizationService(
            repository=repository, audit_store=AuditEventStore(), clock=FixedClock(),
            identity_service=identity,
        )
        return service, repository, identity, owner

    def test_bootstrap_rejects_kind_and_tenant_role_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                ("personal", Role.ORGANIZATION_ADMIN),
                ("organization", Role.PERSONAL_OWNER),
                ("arbitrary", Role.ORGANIZATION_ADMIN),
                ("organization", Role.WORKSPACE_ADMIN),
            )
            for index, (kind, role) in enumerate(cases):
                with self.assertRaises(AuthorizationError) as invalid:
                    SqliteAuthorizationRepository(Path(directory) / f"invalid-{index}.sqlite3").bootstrap_workspace(
                        tenant_id=f"tenant-{index}", workspace_id=f"workspace-{index}",
                        owner_user_id=f"owner-{index}", owner_role=role,
                        workspace_kind=kind, data_area="cloud_sync", cost_limit_cents=1,
                        now=FixedClock()(),
                    )
                self.assertEqual(invalid.exception.code, "INVALID_ROLE_SCOPE")

            personal = SqliteAuthorizationRepository(Path(directory) / "personal.sqlite3")
            personal.bootstrap_workspace(
                tenant_id="tenant-personal", workspace_id="workspace-personal",
                owner_user_id="personal-owner", owner_role=Role.PERSONAL_OWNER,
                workspace_kind="personal", data_area="local_private", cost_limit_cents=0,
                now=FixedClock()(),
            )
            self.assertEqual(personal.tenant_role_version("tenant-personal", "personal-owner"), 1)

    def test_workspace_membership_cannot_assign_tenant_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, _, owner = self.make_organization(Path(directory) / "auth.sqlite3")
            for role in (Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN):
                with self.assertRaises(AuthorizationError) as denied:
                    service.set_membership(
                        principal=owner, workspace_id="workspace-a", user_id="target",
                        role=role, expected_version=0, trace_id=TRACE_ID,
                        policy_version=POLICY_VERSION,
                    )
                self.assertEqual(denied.exception.code, "INVALID_ROLE_SCOPE")
            self.assertIsNone(repository.membership_version(owner.tenant_id, "workspace-a", "target"))
            with repository.transaction() as connection:
                connection.execute(
                    """INSERT INTO auth_memberships(
                    tenant_id,workspace_id,user_id,role,state,version,updated_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        owner.tenant_id, "workspace-a", "corrupt-target",
                        Role.ORGANIZATION_ADMIN.value, "active", 1,
                        FixedClock()().isoformat(),
                    ),
                )
            with self.assertRaises(AuthorizationError) as corrupt_denied:
                service.authorize_action(
                    principal=principal("corrupt-target"), workspace_id="workspace-a",
                    action=Action.VIEW, trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(corrupt_denied.exception.code, "ACTION_DENIED")

    def test_tenant_policy_workspace_selection_accepts_each_tenant_owner_role(self) -> None:
        cases = (
            ("personal", Role.PERSONAL_OWNER, "workspace-personal"),
            ("organization", Role.ORGANIZATION_ADMIN, "workspace-organization"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (workspace_kind, owner_role, workspace_id) in enumerate(cases):
                owner = principal(f"owner-{index}", f"tenant-{index}")
                repository = SqliteAuthorizationRepository(Path(directory) / f"auth-{index}.sqlite3")
                audit = AuditEventStore()
                repository.bootstrap_workspace(
                    tenant_id=owner.tenant_id, workspace_id=workspace_id,
                    owner_user_id=owner.user_id, owner_role=owner_role,
                    workspace_kind=workspace_kind, data_area="local_private",
                    cost_limit_cents=0, now=FixedClock()(),
                )
                service = AuthorizationService(
                    repository=repository, audit_store=audit, clock=FixedClock(),
                    identity_service=FakeIdentityBoundary(owner),
                )

                selected = service.organization_admin_workspace(
                    principal=owner, trace_id=f"trace-{index}", policy_version=POLICY_VERSION,
                )

                self.assertEqual(selected, workspace_id)
                event = audit.list(tenant_id=owner.tenant_id, limit=10).items[-1]
                self.assertEqual(event.action, "authorization.organization_policy.allowed")
                self.assertEqual(event.metadata["role"], owner_role.value)

    def test_tenant_policy_workspace_selection_denies_missing_multiple_and_cross_tenant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def make_service(label: str, tenant_id: str, workspace_kind: str, role: Role):
                owner = principal(f"owner-{label}", tenant_id)
                repository = SqliteAuthorizationRepository(Path(directory) / f"{label}.sqlite3")
                audit = AuditEventStore()
                repository.bootstrap_workspace(
                    tenant_id=tenant_id, workspace_id=f"workspace-{label}-1",
                    owner_user_id=owner.user_id, owner_role=role,
                    workspace_kind=workspace_kind, data_area="local_private",
                    cost_limit_cents=0, now=FixedClock()(),
                )
                service = AuthorizationService(
                    repository=repository, audit_store=audit, clock=FixedClock(),
                    identity_service=FakeIdentityBoundary(owner),
                )
                return service, repository, audit, owner

            multiple, multiple_repository, multiple_audit, multiple_owner = make_service(
                "multiple", "tenant-multiple", "personal", Role.PERSONAL_OWNER,
            )
            multiple_repository.bootstrap_workspace(
                tenant_id=multiple_owner.tenant_id, workspace_id="workspace-multiple-2",
                owner_user_id=multiple_owner.user_id, owner_role=Role.PERSONAL_OWNER,
                workspace_kind="personal", data_area="local_private",
                cost_limit_cents=0, now=FixedClock()(),
            )

            missing, missing_repository, missing_audit, missing_owner = make_service(
                "missing", "tenant-missing", "organization", Role.ORGANIZATION_ADMIN,
            )
            with missing_repository.transaction() as connection:
                connection.execute(
                    "UPDATE auth_workspaces SET workspace_kind='personal' WHERE tenant_id=?",
                    (missing_owner.tenant_id,),
                )

            cross, cross_repository, cross_audit, cross_owner = make_service(
                "cross", "tenant-cross", "personal", Role.PERSONAL_OWNER,
            )
            with cross_repository.transaction() as connection:
                connection.execute(
                    "UPDATE auth_workspaces SET workspace_kind='organization' WHERE tenant_id=?",
                    (cross_owner.tenant_id,),
                )
            cross_repository.bootstrap_workspace(
                tenant_id="tenant-other", workspace_id="workspace-other",
                owner_user_id="owner-other", owner_role=Role.PERSONAL_OWNER,
                workspace_kind="personal", data_area="local_private",
                cost_limit_cents=0, now=FixedClock()(),
            )

            for service, audit, owner, expected_count, expected_role in (
                (multiple, multiple_audit, multiple_owner, 2, Role.PERSONAL_OWNER),
                (missing, missing_audit, missing_owner, 0, Role.ORGANIZATION_ADMIN),
                (cross, cross_audit, cross_owner, 0, Role.PERSONAL_OWNER),
            ):
                with self.assertRaises(AuthorizationError) as denied:
                    service.organization_admin_workspace(
                        principal=owner, trace_id=f"trace-{owner.user_id}",
                        policy_version=POLICY_VERSION,
                    )
                self.assertEqual(denied.exception.code, "ACTION_DENIED")
                event = audit.list(tenant_id=owner.tenant_id, limit=10).items[-1]
                self.assertEqual(event.action, "authorization.organization_policy.denied")
                self.assertEqual(
                    dict(event.metadata),
                    {
                        "candidate_count": expected_count,
                        "reason_code": "ACTION_DENIED",
                        "role": expected_role.value,
                    },
                )

    def test_tenant_role_version_conflict_and_audit_failure_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            owner = principal("owner")
            repository = SqliteAuthorizationRepository(Path(directory) / "auth.sqlite3")
            repository.bootstrap_workspace(
                tenant_id=owner.tenant_id, workspace_id="workspace-a",
                owner_user_id=owner.user_id, owner_role=Role.ORGANIZATION_ADMIN,
                workspace_kind="organization", data_area="cloud_sync",
                cost_limit_cents=1, now=FixedClock()(),
            )
            audit = SelectiveFailAuditStore()
            service = AuthorizationService(
                repository=repository, audit_store=audit, clock=FixedClock(),
            )
            audit.fail_actions.add("authorization.tenant_role.changed")
            with self.assertRaises(AuthorizationError) as failed:
                service.set_tenant_role(
                    principal=owner, user_id="second-admin",
                    role=Role.ORGANIZATION_ADMIN, expected_version=0,
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
            self.assertEqual(failed.exception.code, "AUDIT_WRITE_FAILED")
            self.assertIsNone(repository.tenant_role_version(owner.tenant_id, "second-admin"))

            audit.fail_actions.clear()
            service.set_tenant_role(
                principal=owner, user_id="second-admin",
                role=Role.ORGANIZATION_ADMIN, expected_version=0,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            with self.assertRaises(AuthorizationError) as conflict:
                service.set_tenant_role(
                    principal=owner, user_id="second-admin",
                    role=Role.ORGANIZATION_ADMIN, expected_version=0,
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
            self.assertEqual(conflict.exception.code, "VERSION_CONFLICT")
            self.assertEqual(repository.tenant_role_version(owner.tenant_id, "second-admin"), 1)

    def test_organization_admin_has_tenant_scope_across_two_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, _, owner = self.make_organization(Path(directory) / "auth.sqlite3")
            repository.bootstrap_workspace(
                tenant_id=owner.tenant_id, workspace_id="workspace-b", owner_user_id=owner.user_id,
                owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
                data_area="cloud_sync", cost_limit_cents=100, now=FixedClock()(),
            )
            for workspace_id in ("workspace-a", "workspace-b"):
                grant = service.authorize_action(
                    principal=owner, workspace_id=workspace_id, action=Action.POLICY_MANAGE,
                    trace_id=f"trace-{workspace_id}", policy_version=POLICY_VERSION,
                )
                self.assertEqual(grant.role, Role.ORGANIZATION_ADMIN)
                self.assertEqual(grant.role_scope, RoleScope.TENANT)
                self.assertEqual(grant.membership_version, 1)
                self.assertIsNone(
                    repository.membership_version(owner.tenant_id, workspace_id, owner.user_id)
                )

    def test_workspace_admin_cannot_manage_tenant_or_another_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, identity, owner = self.make_organization(Path(directory) / "auth.sqlite3")
            repository.bootstrap_workspace(
                tenant_id=owner.tenant_id, workspace_id="workspace-b", owner_user_id=owner.user_id,
                owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
                data_area="cloud_sync", cost_limit_cents=100, now=FixedClock()(),
            )
            admin = principal("workspace-admin")
            service.set_membership(
                principal=owner, workspace_id="workspace-a", user_id=admin.user_id,
                role=Role.WORKSPACE_ADMIN, expected_version=0, trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            identity.principal = admin
            identity.grant(
                "tenant-step", action_group="organization_security_or_connector_policy_change",
                target_id=owner.tenant_id, policy_version=POLICY_VERSION,
            )
            with self.assertRaises(AuthorizationError) as tenant_denied:
                service.set_permission_policy(
                    access_token=identity.access_token, step_up_authorization="tenant-step",
                    scope="tenant", workspace_id="workspace-a", permission=Permission.EXTERNAL_LLM,
                    effect=PolicyEffect.DENY, locked=True, expected_version=1,
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
            self.assertEqual(tenant_denied.exception.code, "PRIVILEGE_ESCALATION_DENIED")
            with self.assertRaises(AuthorizationError) as other_denied:
                service.set_membership(
                    principal=admin, workspace_id="workspace-b", user_id="target",
                    role=Role.VIEWER, expected_version=0, trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(other_denied.exception.code, "PRIVILEGE_ESCALATION_DENIED")

    def test_tenant_role_revoke_blocks_historical_access_and_snapshot_uses_binding_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, owner = self.make_organization(Path(directory) / "auth.sqlite3")
            admin = principal("tenant-admin")
            service.set_tenant_role(
                principal=owner, user_id=admin.user_id, role=Role.ORGANIZATION_ADMIN,
                expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            descriptor = HistoricalResultDescriptor(
                result_id="output-tenant-role", result_kind="output",
                tenant_id=owner.tenant_id, workspace_id="workspace-a",
                source_version_ids=("source-version",), evidence_reference_ids=("evidence",),
                dependencies=(EvidenceDependency("evidence", "source-version", ("segment",), True, False),),
                original_policy_version="old-policy", original_membership_version=1,
            )
            service.register_historical_result(
                principal=owner, descriptor=descriptor, trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            decision = service.evaluate_historical_access(
                principal=admin, result_id=descriptor.result_id, action=AccessAction.READ,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            self.assertEqual(decision.state, AccessState.AVAILABLE)
            self.assertEqual(decision.role_scope, RoleScope.TENANT)
            self.assertEqual(decision.membership_version, 1)
            rerun = service.authorize_rerun(
                principal=admin, result_id=descriptor.result_id,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            self.assertEqual(rerun.snapshot.role_scope, RoleScope.TENANT)
            self.assertEqual(rerun.snapshot.membership_version, 1)

            service.set_tenant_role(
                principal=owner, user_id=admin.user_id, role=None, expected_version=1,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            blocked = service.evaluate_historical_access(
                principal=admin, result_id=descriptor.result_id, action=AccessAction.RERUN,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            self.assertEqual(blocked.state, AccessState.ACCESS_BLOCKED)

    def test_cross_tenant_tenant_role_is_not_disclosed_or_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, _, owner = self.make_organization(Path(directory) / "auth.sqlite3")
            foreign = principal("foreign-owner", "tenant-foreign")
            repository.bootstrap_workspace(
                tenant_id=foreign.tenant_id, workspace_id="workspace-foreign",
                owner_user_id=foreign.user_id, owner_role=Role.ORGANIZATION_ADMIN,
                workspace_kind="organization", data_area="cloud_sync",
                cost_limit_cents=1, now=FixedClock()(),
            )
            failures = []
            for workspace_id in ("workspace-foreign", "workspace-missing"):
                with self.assertRaises(AuthorizationError) as denied:
                    service.authorize_action(
                        principal=owner, workspace_id=workspace_id, action=Action.VIEW,
                        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                    )
                failures.append((denied.exception.code, denied.exception.http_status, str(denied.exception)))
            self.assertEqual(failures[0], failures[1])


if __name__ == "__main__":
    unittest.main()
