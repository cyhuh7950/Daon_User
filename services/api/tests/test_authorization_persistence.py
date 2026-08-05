from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_authorization_support import FixedClock, POLICY_VERSION, TRACE_ID, principal
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import Action, AuthorizationService, Role, SqliteAuthorizationRepository


class AuthorizationPersistenceTests(unittest.TestCase):
    def test_personal_workspace_bootstrap_is_idempotent_and_discoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authorization.sqlite3"
            repository = SqliteAuthorizationRepository(path)
            values = dict(
                tenant_id="tenant-personal",
                workspace_id="workspace-personal",
                owner_user_id="user-personal",
                owner_role=Role.PERSONAL_OWNER,
                workspace_kind="personal",
                data_area="cloud_sync",
                cost_limit_cents=1000,
                now=FixedClock()(),
            )
            repository.bootstrap_workspace(**values)
            repository.bootstrap_workspace(**{**values, "cost_limit_cents": 2500})
            self.assertEqual(
                repository.primary_workspace_id("tenant-personal"),
                "workspace-personal",
            )

    def test_restart_fk_wal_and_parameterized_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authorization.sqlite3"
            owner = principal("owner")
            repository = SqliteAuthorizationRepository(path)
            repository.bootstrap_workspace(tenant_id=owner.tenant_id, workspace_id="workspace-001", owner_user_id=owner.user_id, owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=100, now=FixedClock()())
            service = AuthorizationService(repository=repository, audit_store=AuditEventStore(), clock=FixedClock())
            service.set_membership(principal=owner, workspace_id="workspace-001", user_id="safe-user", role=Role.VIEWER, expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            repository.close()

            reopened = SqliteAuthorizationRepository(path)
            self.assertEqual(reopened.foreign_keys_enabled(), 1)
            self.assertEqual(reopened.journal_mode().lower(), "wal")
            self.assertEqual(reopened.membership_version(owner.tenant_id, "workspace-001", "safe-user"), 1)
            restarted = AuthorizationService(repository=reopened, audit_store=AuditEventStore(), clock=FixedClock())
            grant = restarted.authorize_action(principal=principal("safe-user"), workspace_id="workspace-001", action=Action.VIEW, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertTrue(grant.allowed)

            with self.assertRaises(Exception):
                restarted.authorize_action(principal=owner, workspace_id="workspace-001' OR 1=1 --", action=Action.VIEW, trace_id=TRACE_ID, policy_version=POLICY_VERSION)


if __name__ == "__main__":
    unittest.main()
