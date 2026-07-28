from __future__ import annotations

import tempfile
import threading
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
    Action,
    AuthorizationError,
    AuthorizationService,
    Permission,
    PolicyEffect,
    ROLE_ACTION_MATRIX,
    Role,
    SqliteAuthorizationRepository,
)
from test_identity_support import create_service, native_login


EXPECTED_MATRIX = {
    Role.PERSONAL_OWNER: frozenset(Action),
    Role.ORGANIZATION_ADMIN: frozenset(Action),
    Role.WORKSPACE_ADMIN: frozenset({
        Action.VIEW, Action.QUERY, Action.ANALYZE, Action.GENERATE, Action.EDIT,
        Action.REVIEW, Action.REVISION_REQUEST, Action.POLICY_MANAGE, Action.MEMBER_MANAGE,
    }),
    Role.EDITOR: frozenset({Action.VIEW, Action.QUERY, Action.ANALYZE, Action.GENERATE, Action.EDIT}),
    Role.REVIEWER: frozenset({Action.VIEW, Action.QUERY, Action.ANALYZE, Action.REVIEW, Action.REVISION_REQUEST}),
    Role.APPROVER: frozenset({Action.VIEW, Action.QUERY, Action.ANALYZE, Action.REVIEW, Action.APPROVE, Action.DELIVER, Action.KNOWLEDGE_REGISTER}),
    Role.VIEWER: frozenset({Action.VIEW}),
}


class AuthorizationRolePolicyTests(unittest.TestCase):
    def make_service(self, path: Path, *, audit_store=None, owner_role=Role.ORGANIZATION_ADMIN):
        owner = principal("owner")
        identity = FakeIdentityBoundary(owner)
        audit = audit_store or AuditEventStore()
        repository = SqliteAuthorizationRepository(path)
        repository.bootstrap_workspace(
            tenant_id=owner.tenant_id, workspace_id="workspace-001", owner_user_id=owner.user_id,
            owner_role=owner_role, workspace_kind="organization", data_area="cloud_sync",
            cost_limit_cents=10000, now=FixedClock()(),
        )
        return AuthorizationService(repository=repository, audit_store=audit, clock=FixedClock(), identity_service=identity), repository, audit, identity, owner

    def test_seven_role_matrix_is_explicit_and_deny_by_default(self) -> None:
        self.assertEqual(ROLE_ACTION_MATRIX, EXPECTED_MATRIX)
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, _, owner = self.make_service(Path(directory) / "auth.sqlite3")
            for index, role in enumerate(Role):
                actor = owner if role is Role.ORGANIZATION_ADMIN else principal(f"actor-{index}")
                if actor is not owner:
                    service.set_membership(
                        principal=owner, workspace_id="workspace-001", user_id=actor.user_id,
                        role=role, expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                    )
                for action in Action:
                    if action in EXPECTED_MATRIX[role]:
                        grant = service.authorize_action(
                            principal=actor, workspace_id="workspace-001", action=action,
                            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                        )
                        self.assertTrue(grant.allowed)
                    else:
                        with self.assertRaises(AuthorizationError) as denied:
                            service.authorize_action(
                                principal=actor, workspace_id="workspace-001", action=action,
                                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                            )
                        self.assertEqual(denied.exception.code, "ACTION_DENIED")

    def test_each_of_eight_permissions_can_be_revoked_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, identity, owner = self.make_service(Path(directory) / "auth.sqlite3")
            for index, permission in enumerate(Permission, start=1):
                authorization = f"step-up-permission-{index}"
                identity.grant(authorization, action_group="organization_security_or_connector_policy_change", target_id="workspace-001", policy_version=POLICY_VERSION)
                service.set_permission_policy(
                    access_token=identity.access_token, step_up_authorization=authorization,
                    scope="workspace", workspace_id="workspace-001", permission=permission,
                    effect=PolicyEffect.DENY, locked=False, expected_version=index,
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
                denied = service.evaluate_permission(
                    principal=owner, workspace_id="workspace-001", permission=permission, requested=True,
                )
                self.assertFalse(denied.effective)
                for other in Permission:
                    if other not in tuple(Permission)[:index]:
                        self.assertTrue(service.evaluate_permission(principal=owner, workspace_id="workspace-001", permission=other, requested=True).effective)

    def test_organization_deny_and_lock_cannot_be_relaxed_by_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, identity, owner = self.make_service(Path(directory) / "auth.sqlite3")
            identity.grant("tenant-policy", action_group="organization_security_or_connector_policy_change", target_id=owner.tenant_id, policy_version=POLICY_VERSION)
            service.set_permission_policy(
                access_token=identity.access_token, step_up_authorization="tenant-policy", scope="tenant",
                workspace_id="workspace-001", permission=Permission.EXTERNAL_LLM,
                effect=PolicyEffect.DENY, locked=True, expected_version=1,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            identity.grant("workspace-policy", action_group="organization_security_or_connector_policy_change", target_id="workspace-001", policy_version=POLICY_VERSION)
            service.set_permission_policy(
                access_token=identity.access_token, step_up_authorization="workspace-policy", scope="workspace",
                workspace_id="workspace-001", permission=Permission.EXTERNAL_LLM,
                effect=PolicyEffect.GRANT, locked=False, expected_version=1,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            decision = service.evaluate_permission(principal=owner, workspace_id="workspace-001", permission=Permission.EXTERNAL_LLM, requested=True)
            self.assertFalse(decision.effective)
            self.assertEqual(decision.locked_by, "tenant-policy")

    def test_expected_version_concurrency_allows_exactly_one_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, _, _, owner = self.make_service(Path(directory) / "auth.sqlite3")
            service.set_membership(principal=owner, workspace_id="workspace-001", user_id="viewer", role=Role.VIEWER, expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            barrier = threading.Barrier(3)
            outcomes: list[str] = []

            def update(role: Role) -> None:
                barrier.wait()
                try:
                    service.set_membership(principal=owner, workspace_id="workspace-001", user_id="viewer", role=role, expected_version=1, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
                    outcomes.append("updated")
                except AuthorizationError as error:
                    outcomes.append(error.code)

            threads = [threading.Thread(target=update, args=(Role.EDITOR,)), threading.Thread(target=update, args=(Role.REVIEWER,))]
            for thread in threads: thread.start()
            barrier.wait()
            for thread in threads: thread.join(10)
            self.assertCountEqual(outcomes, ["updated", "VERSION_CONFLICT"])
            self.assertEqual(repository.membership_version(owner.tenant_id, "workspace-001", "viewer"), 2)

    def test_cross_tenant_and_missing_workspace_are_indistinguishable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, _, _, owner = self.make_service(Path(directory) / "auth.sqlite3")
            repository.bootstrap_workspace(tenant_id="tenant-002", workspace_id="workspace-foreign", owner_user_id="foreign", owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=100, now=FixedClock()())
            failures = []
            for workspace_id in ("workspace-foreign", "workspace-missing"):
                with self.assertRaises(AuthorizationError) as error:
                    service.authorize_action(principal=owner, workspace_id=workspace_id, action=Action.VIEW, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
                failures.append((error.exception.code, error.exception.http_status, str(error.exception)))
            self.assertEqual(failures[0], failures[1])
            self.assertNotIn("workspace-foreign", failures[0][2])

    def test_privilege_escalation_uses_current_repository_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, audit, _, owner = self.make_service(Path(directory) / "auth.sqlite3")
            editor = principal("editor")
            service.set_membership(principal=owner, workspace_id="workspace-001", user_id=editor.user_id, role=Role.EDITOR, expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            with self.assertRaises(AuthorizationError) as denied:
                service.set_membership(principal=editor, workspace_id="workspace-001", user_id=editor.user_id, role=Role.ORGANIZATION_ADMIN, expected_version=1, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(denied.exception.code, "PRIVILEGE_ESCALATION_DENIED")
            actions = [event.action for event in audit.list(tenant_id=owner.tenant_id, limit=200).items]
            self.assertIn("authorization.membership.change_denied", actions)

    def test_policy_change_requires_bound_step_up_and_audit_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controlled = SelectiveFailAuditStore()
            service, _, _, identity, _ = self.make_service(Path(directory) / "auth.sqlite3", audit_store=controlled)
            with self.assertRaises(Exception) as missing:
                service.set_permission_policy(
                    access_token=identity.access_token, step_up_authorization=None, scope="workspace",
                    workspace_id="workspace-001", permission=Permission.INTERNET_SEARCH,
                    effect=PolicyEffect.DENY, locked=False, expected_version=1,
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
            self.assertEqual(getattr(missing.exception, "code", None), "STEP_UP_REQUIRED")
            identity.grant("bound-step", action_group="organization_security_or_connector_policy_change", target_id="workspace-001", policy_version=POLICY_VERSION)
            controlled.fail_actions.add("authorization.policy.changed")
            with self.assertRaises(AuthorizationError) as failed:
                service.set_permission_policy(
                    access_token=identity.access_token, step_up_authorization="bound-step", scope="workspace",
                    workspace_id="workspace-001", permission=Permission.INTERNET_SEARCH,
                    effect=PolicyEffect.DENY, locked=False, expected_version=1,
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
            self.assertEqual(failed.exception.code, "AUDIT_WRITE_FAILED")
            self.assertTrue(service.evaluate_permission(principal=identity.principal, workspace_id="workspace-001", permission=Permission.INTERNET_SEARCH, requested=True).effective)

    def test_policy_change_consumes_actual_m4_03_step_up_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared-identity-authorization.sqlite3"
            identity_service, _, audit, identity_clock = create_service(path)
            credentials = native_login(identity_service)
            repository = SqliteAuthorizationRepository(path)
            repository.bootstrap_workspace(
                tenant_id=credentials.tenant_id, workspace_id="workspace-001",
                owner_user_id=credentials.user_id, owner_role=Role.ORGANIZATION_ADMIN,
                workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=100,
                now=identity_clock(),
            )
            service = AuthorizationService(
                repository=repository, audit_store=audit, clock=identity_clock,
                identity_service=identity_service,
            )
            grant = identity_service.issue_step_up(
                access_token=credentials.access_token,
                action_group="organization_security_or_connector_policy_change",
                target_id="workspace-001", policy_version=POLICY_VERSION, trace_id=TRACE_ID,
            )
            service.set_permission_policy(
                access_token=credentials.access_token,
                step_up_authorization=grant.authorization,
                scope="workspace", workspace_id="workspace-001",
                permission=Permission.EXTERNAL_LLM, effect=PolicyEffect.DENY,
                locked=False, expected_version=1,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            decision = service.evaluate_permission(
                principal=principal(credentials.user_id), workspace_id="workspace-001",
                permission=Permission.EXTERNAL_LLM, requested=True,
            )
            self.assertFalse(decision.effective)

    def test_denied_changes_are_audited_without_tokens_or_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, audit, identity, owner = self.make_service(Path(directory) / "auth.sqlite3")
            service.set_membership(
                principal=owner, workspace_id="workspace-001", user_id="viewer",
                role=Role.VIEWER, expected_version=0, trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            with self.assertRaises(AuthorizationError) as conflict:
                service.set_membership(
                    principal=owner, workspace_id="workspace-001", user_id="viewer",
                    role=Role.EDITOR, expected_version=0, trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(conflict.exception.code, "VERSION_CONFLICT")

            viewer = principal("viewer")
            identity.principal = viewer
            with self.assertRaises(AuthorizationError) as denied:
                service.set_permission_policy(
                    access_token=identity.access_token,
                    step_up_authorization="must-not-be-recorded",
                    scope="workspace", workspace_id="workspace-001",
                    permission=Permission.EXTERNAL_LLM, effect=PolicyEffect.DENY,
                    locked=False, expected_version=1, trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(denied.exception.code, "PRIVILEGE_ESCALATION_DENIED")

            events = audit.list(tenant_id=owner.tenant_id, limit=200).items
            denied_actions = {
                event.action for event in events if event.outcome.value == "denied"
            }
            self.assertIn("authorization.membership.change_denied", denied_actions)
            self.assertIn("authorization.policy.change_denied", denied_actions)
            serialized = repr(events)
            self.assertNotIn("must-not-be-recorded", serialized)
            self.assertNotIn(identity.access_token, serialized)


if __name__ == "__main__":
    unittest.main()
