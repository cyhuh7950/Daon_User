from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from test_identity_support import POLICY_VERSION, TRACE_ID, create_service, native_login
from daon_user_api.admin_membership_roles import AdminMembershipRoleService
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import (
    Action, AuthorizationError, AuthorizationService, Role, SqliteAuthorizationRepository,
)
from daon_user_api.identity import IdentityError, IdentityPrincipal


class IdentitySessionTests(unittest.TestCase):
    def test_admin_role_downgrade_rechecks_existing_access_and_rotated_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity, _, _, clock = create_service(root / "identity.sqlite3")
            credentials = native_login(identity)
            repository = SqliteAuthorizationRepository(root / "authorization.sqlite3")
            repository.bootstrap_workspace(
                tenant_id="tenant-001", workspace_id="workspace-001", owner_user_id="admin",
                owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
                data_area="cloud_sync", cost_limit_cents=100, now=clock(),
            )
            authorization = AuthorizationService(
                repository=repository, audit_store=AuditEventStore(), clock=clock,
            )
            admin = IdentityPrincipal("admin", "admin-session", "admin-device", "tenant-other")
            owner = IdentityPrincipal("admin", "owner-session", "owner-device", "tenant-001")
            authorization.set_membership(
                principal=owner, workspace_id="workspace-001", user_id=credentials.user_id,
                role=Role.EDITOR, expected_version=0, trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            original = identity.validate_access(
                credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            before = authorization.authorize_action(
                principal=original, workspace_id="workspace-001", action=Action.EDIT,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            self.assertEqual((before.role, before.membership_version, before.acl_version), (Role.EDITOR, 1, 2))

            changed = AdminMembershipRoleService(
                repository=repository, system_admin_user_ids=frozenset({"admin"}), clock=clock,
            ).change_existing_workspace_role(
                actor=admin, tenant_id="tenant-001", workspace_id="workspace-001",
                user_id=credentials.user_id, role=Role.VIEWER, expected_version=1,
                idempotency_key="r5-session-downgrade-0001", reason="ACCESS_REVIEW",
            )
            self.assertEqual((changed.version, changed.acl_version), (2, 3))
            still_valid = identity.validate_access(
                credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            self.assertEqual(still_valid.session_id, original.session_id)
            with self.assertRaises(AuthorizationError) as old_session_denied:
                authorization.authorize_action(
                    principal=still_valid, workspace_id="workspace-001", action=Action.EDIT,
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
            self.assertEqual(old_session_denied.exception.http_status, 403)
            view = authorization.authorize_action(
                principal=still_valid, workspace_id="workspace-001", action=Action.VIEW,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            self.assertEqual((view.role, view.membership_version, view.acl_version), (Role.VIEWER, 2, 3))

            refreshed = identity.rotate_refresh(
                credentials.refresh_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            refreshed_principal = identity.validate_access(
                refreshed.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            self.assertEqual(refreshed_principal.session_id, original.session_id)
            with self.assertRaises(AuthorizationError) as refreshed_denied:
                authorization.authorize_action(
                    principal=refreshed_principal, workspace_id="workspace-001", action=Action.EDIT,
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
            self.assertEqual(refreshed_denied.exception.http_status, 403)
            refreshed_view = authorization.authorize_action(
                principal=refreshed_principal, workspace_id="workspace-001", action=Action.VIEW,
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            self.assertEqual((refreshed_view.role, refreshed_view.membership_version, refreshed_view.acl_version),
                             (Role.VIEWER, 2, 3))

    def test_access_refresh_rotation_replay_expiry_and_session_revoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, audit, clock = create_service(Path(directory) / "identity.sqlite3")
            credentials = native_login(service)
            principal = service.validate_access(
                credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION
            )
            self.assertEqual(principal.session_id, credentials.session_id)

            rotated = service.rotate_refresh(
                credentials.refresh_token,
                trace_id="trace-refresh-001",
                policy_version=POLICY_VERSION,
            )
            self.assertNotEqual(rotated.refresh_token, credentials.refresh_token)
            with self.assertRaises(IdentityError) as old_access:
                service.validate_access(
                    credentials.access_token,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(old_access.exception.http_status, 401)

            with self.assertRaises(IdentityError) as replay:
                service.rotate_refresh(
                    credentials.refresh_token,
                    trace_id="trace-refresh-replay",
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(replay.exception.code, "REFRESH_REPLAYED")
            with self.assertRaises(IdentityError) as family_revoked:
                service.validate_access(
                    rotated.access_token,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(family_revoked.exception.code, "SESSION_REVOKED")
            self.assertTrue(audit.verify_integrity().valid)
            actions = {event.action for event in audit.list(tenant_id="tenant-001").items}
            self.assertTrue(
                {"identity.refresh.rotated", "identity.refresh.replay_denied", "identity.session.revoked"}.issubset(actions)
            )

            second = native_login(service)
            clock.advance(hours=2)
            with self.assertRaises(IdentityError) as expired:
                service.validate_access(
                    second.access_token,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(expired.exception.code, "ACCESS_EXPIRED")
            self.assertEqual(expired.exception.http_status, 401)

    def test_concurrent_refresh_allows_one_rotation_then_revokes_family_on_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, _ = create_service(Path(directory) / "identity.sqlite3")
            credentials = native_login(service)
            barrier = threading.Barrier(3)
            successes = []
            failures: list[str] = []

            def worker() -> None:
                barrier.wait()
                try:
                    successes.append(
                        service.rotate_refresh(
                            credentials.refresh_token,
                            trace_id="trace-refresh-race",
                            policy_version=POLICY_VERSION,
                        )
                    )
                except IdentityError as error:
                    failures.append(error.code)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(len(successes), 1)
            self.assertEqual(failures, ["REFRESH_REPLAYED"])
            with self.assertRaises(IdentityError) as revoked:
                service.validate_access(
                    successes[0].access_token,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(revoked.exception.code, "SESSION_REVOKED")


if __name__ == "__main__":
    unittest.main()
