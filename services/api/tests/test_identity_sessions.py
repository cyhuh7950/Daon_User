from __future__ import annotations

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg

from test_identity_support import (
    POLICY_VERSION, TRACE_ID, FakeVerifiedOidcProvider, create_service, native_login, policy,
)
from daon_user_api.admin_membership_roles import AdminMembershipRoleService
from daon_user_api.admin_users import AdminUserService
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import (
    Action, AuthorizationError, AuthorizationService, Role, SqliteAuthorizationRepository,
)
from daon_user_api.identity import ClientKind, IdentityError, IdentityPrincipal, IdentityService
from daon_user_api.identity_postgres import PostgresIdentityRepository
from daon_user_api.postgres_adapters import PostgresAuthorizationRepository


class IdentitySessionTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("DAON_TEST_POSTGRES_DSN"), "isolated PostgreSQL DSN required")
    def test_postgres_audit_insert_failure_preserves_old_native_session(self) -> None:
        dsn = os.environ["DAON_TEST_POSTGRES_DSN"]
        suffix = uuid4().hex[:12]
        target_tenant = f"rollback-organization-{suffix}"
        workspace_id = f"rollback-workspace-{suffix}"
        trace_id = f"rollback-trace-{suffix}"
        function_name = f"scope_audit_block_{suffix}"
        trigger_name = f"scope_audit_block_trigger_{suffix}"
        identity_repository = PostgresIdentityRepository(dsn)
        authorization_repository = PostgresAuthorizationRepository(dsn)
        identity = IdentityService(
            repository=identity_repository, audit_store=AuditEventStore(),
            oidc_policies=(policy(),), clock=lambda: datetime.now(timezone.utc),
        )
        trigger_created = False
        try:
            original = native_login(identity, FakeVerifiedOidcProvider(subject=f"rollback-user-{suffix}"))
            identity_repository.add_tenant_membership(
                tenant_id=target_tenant, user_id=original.user_id, role="member",
            )
            authorization_repository.bootstrap_workspace(
                tenant_id=target_tenant, workspace_id=workspace_id, owner_user_id="admin",
                owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
                data_area="cloud_sync", cost_limit_cents=100, now=identity._now(),
            )
            AuthorizationService(
                repository=authorization_repository, audit_store=AuditEventStore(), clock=identity._now,
            ).set_membership(
                principal=IdentityPrincipal("admin", "admin-session", "admin-device", target_tenant),
                workspace_id=workspace_id, user_id=original.user_id, role=Role.VIEWER,
                expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            with identity_repository.transaction() as connection:
                before_sessions = connection.execute(
                    "SELECT COUNT(*) FROM sessions WHERE user_id=?", (original.user_id,),
                ).fetchone()[0]
            with psycopg.connect(dsn, autocommit=True) as connection:
                connection.execute(
                    f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$BEGIN RAISE EXCEPTION 'injected audit insert failure'; END$$"
                )
                connection.execute(
                    f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON identity_session_tenant_switch_outbox "
                    f"FOR EACH ROW WHEN (NEW.trace_id = '{trace_id}') "
                    f"EXECUTE FUNCTION {function_name}()"
                )
                trigger_created = True
            with self.assertRaises(IdentityError) as failed:
                identity.switch_session_tenant(
                    access_token=original.access_token, tenant_id=target_tenant,
                    trace_id=trace_id, policy_version=POLICY_VERSION,
                )
            self.assertEqual(failed.exception.http_status, 503)
            self.assertEqual(identity.validate_access(
                original.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            ).session_id, original.session_id)
            with identity_repository.transaction() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM sessions WHERE user_id=?", (original.user_id,),
                ).fetchone()[0], before_sessions)
                self.assertEqual(connection.execute(
                    "SELECT state FROM refresh_families WHERE session_id=?",
                    (original.session_id,),
                ).fetchone()["state"], "active")
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM session_tenant_switch_outbox WHERE prior_session_id=?",
                    (original.session_id,),
                ).fetchone()[0], 0)
        finally:
            with psycopg.connect(dsn, autocommit=True) as connection:
                if trigger_created:
                    connection.execute(
                        f"DROP TRIGGER {trigger_name} ON identity_session_tenant_switch_outbox"
                    )
                connection.execute(f"DROP FUNCTION IF EXISTS {function_name}()")
            authorization_repository.close()
            identity_repository.close()

    @unittest.skipUnless(os.environ.get("DAON_TEST_POSTGRES_DSN"), "isolated PostgreSQL DSN required")
    def test_postgres_native_switch_commits_session_revocation_and_outbox(self) -> None:
        dsn = os.environ["DAON_TEST_POSTGRES_DSN"]
        identity_repository = PostgresIdentityRepository(dsn)
        authorization_repository = PostgresAuthorizationRepository(dsn)
        identity = IdentityService(
            repository=identity_repository, audit_store=AuditEventStore(),
            oidc_policies=(policy(),), clock=lambda: datetime.now(timezone.utc),
        )
        suffix = uuid4().hex[:12]
        target_tenant = f"scope-organization-{suffix}"
        workspace_id = f"scope-workspace-{suffix}"
        try:
            original = native_login(identity, FakeVerifiedOidcProvider(subject=f"scope-user-{suffix}"))
            identity_repository.add_tenant_membership(
                tenant_id=target_tenant, user_id=original.user_id, role="member",
            )
            authorization_repository.bootstrap_workspace(
                tenant_id=target_tenant, workspace_id=workspace_id, owner_user_id="admin",
                owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
                data_area="cloud_sync", cost_limit_cents=100, now=identity._now(),
            )
            AuthorizationService(
                repository=authorization_repository, audit_store=AuditEventStore(),
                clock=identity._now,
            ).set_membership(
                principal=IdentityPrincipal("admin", "admin-session", "admin-device", target_tenant),
                workspace_id=workspace_id, user_id=original.user_id, role=Role.VIEWER,
                expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            choices = identity.selectable_tenant_workspaces(user_id=original.user_id)
            self.assertIn((target_tenant, "organization", workspace_id), choices)
            other_repository = PostgresIdentityRepository(dsn)
            try:
                other_identity = IdentityService(
                    repository=other_repository, audit_store=AuditEventStore(),
                    oidc_policies=(policy(),), clock=lambda: datetime.now(timezone.utc),
                )
                barrier = threading.Barrier(2)
                def competing_switch(service: IdentityService):
                    barrier.wait(timeout=5)
                    try:
                        return service.switch_session_tenant(
                            access_token=original.access_token, tenant_id=target_tenant,
                            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                        )
                    except IdentityError as error:
                        return error
                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(competing_switch, (identity, other_identity)))
                successes = [outcome for outcome in outcomes if isinstance(outcome, tuple)]
                failures = [outcome for outcome in outcomes if isinstance(outcome, IdentityError)]
                self.assertEqual(len(successes), 1)
                self.assertEqual([error.http_status for error in failures], [401])
                switched, selected = successes[0]
            finally:
                other_repository.close()
            self.assertEqual(selected, workspace_id)
            self.assertEqual(switched.client_kind, ClientKind.NATIVE)
            with identity_repository.transaction() as connection:
                old_state = connection.execute(
                    "SELECT state FROM sessions WHERE session_id=?", (original.session_id,),
                ).fetchone()["state"]
                intent = connection.execute(
                    "SELECT prior_session_id,new_session_id,target_tenant_id FROM session_tenant_switch_outbox "
                    "WHERE prior_session_id=?", (original.session_id,),
                ).fetchone()
                intent_count = connection.execute(
                    "SELECT COUNT(*) FROM session_tenant_switch_outbox WHERE prior_session_id=?",
                    (original.session_id,),
                ).fetchone()[0]
            self.assertEqual(old_state, "revoked")
            self.assertEqual(tuple(intent.values()), (original.session_id, switched.session_id, target_tenant))
            self.assertEqual(intent_count, 1)
            with self.assertRaises(IdentityError) as stale:
                identity.rotate_refresh(
                    original.refresh_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
            self.assertEqual(stale.exception.http_status, 401)
            AdminUserService(
                repository=identity_repository, audit_store=AuditEventStore(),
                system_admin_user_ids=frozenset({"admin"}), clock=identity._now,
            ).delete_user(
                IdentityPrincipal("admin", "admin-session", "admin-device", "admin"),
                user_id=original.user_id, idempotency_key=f"scope-delete-{suffix}",
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            with identity_repository.transaction() as connection:
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM users WHERE user_id=?", (original.user_id,),
                ).fetchone())
                self.assertIsNotNone(connection.execute(
                    "SELECT 1 FROM session_tenant_switch_outbox WHERE prior_session_id=?",
                    (original.session_id,),
                ).fetchone())
        finally:
            authorization_repository.close()
            identity_repository.close()

    def test_native_switch_replaces_session_refresh_and_persists_audit_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "runtime.sqlite3"
            identity, identity_repository, audit, clock = create_service(database_path)
            original = native_login(identity)
            identity_repository.add_tenant_membership(
                tenant_id="tenant-002", user_id=original.user_id, role="member",
            )
            repository = SqliteAuthorizationRepository(database_path)
            repository.bootstrap_workspace(
                tenant_id="tenant-002", workspace_id="workspace-002", owner_user_id="admin",
                owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
                data_area="cloud_sync", cost_limit_cents=100, now=clock(),
            )
            authorization = AuthorizationService(
                repository=repository, audit_store=AuditEventStore(), clock=clock,
            )
            authorization.set_membership(
                principal=IdentityPrincipal("admin", "admin-session", "admin-device", "tenant-002"),
                workspace_id="workspace-002", user_id=original.user_id, role=Role.VIEWER,
                expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )

            class FailingCentralAudit:
                def append(self, _draft: object) -> None:
                    raise RuntimeError("central audit unavailable")
            identity._audit_store = FailingCentralAudit()
            switched, workspace_id = identity.switch_session_tenant(
                access_token=original.access_token, tenant_id="tenant-002",
                trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            with identity_repository.transaction() as connection:
                pending = connection.execute(
                    "SELECT delivered_at FROM session_tenant_switch_outbox"
                ).fetchone()
            self.assertIsNone(pending[0])
            identity._audit_store = audit
            identity.dispatch_pending_tenant_switch_audits()
            identity.dispatch_pending_tenant_switch_audits()
            self.assertEqual(len([
                event for event in audit.list(tenant_id="tenant-002").items
                if event.action == "identity.session.tenant_switched"
            ]), 1)

            self.assertEqual(workspace_id, "workspace-002")
            self.assertEqual(switched.tenant_id, "tenant-002")
            self.assertNotEqual(switched.session_id, original.session_id)
            self.assertNotEqual(switched.refresh_token, original.refresh_token)
            self.assertEqual(identity.validate_access(
                switched.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            ).tenant_id, "tenant-002")
            with self.assertRaises(IdentityError) as stale_access:
                identity.validate_access(original.access_token, trace_id=TRACE_ID,
                                         policy_version=POLICY_VERSION)
            self.assertEqual(stale_access.exception.http_status, 401)
            with self.assertRaises(IdentityError) as stale_refresh:
                identity.rotate_refresh(original.refresh_token, trace_id=TRACE_ID,
                                        policy_version=POLICY_VERSION)
            self.assertEqual(stale_refresh.exception.http_status, 401)
            with identity_repository.transaction() as connection:
                intent = connection.execute(
                    "SELECT prior_session_id,new_session_id,prior_tenant_id,target_tenant_id "
                    "FROM session_tenant_switch_outbox"
                ).fetchone()
            self.assertEqual(tuple(intent), (
                original.session_id, switched.session_id, "tenant-001", "tenant-002",
            ))

            # A lost response cannot authorize a replay with the old bearer.
            with self.assertRaises(IdentityError) as lost_response_retry:
                identity.switch_session_tenant(
                    access_token=original.access_token, tenant_id="tenant-002",
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
            self.assertEqual(lost_response_retry.exception.http_status, 401)

            racing_session = native_login(identity)
            def race_switch() -> int:
                try:
                    identity.switch_session_tenant(
                        access_token=racing_session.access_token, tenant_id="tenant-002",
                        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                    )
                except IdentityError as error:
                    return error.http_status
                return 200
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(lambda _: race_switch(), range(2)))
            self.assertEqual(sorted(outcomes), [200, 401])
            with identity_repository.transaction() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM session_tenant_switch_outbox"
                ).fetchone()[0], 2)

    def test_switch_outbox_insert_failure_rolls_back_old_session_and_new_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "runtime.sqlite3"
            identity, identity_repository, _, clock = create_service(database_path)
            original = native_login(identity)
            identity_repository.add_tenant_membership(
                tenant_id="tenant-002", user_id=original.user_id, role="member",
            )
            repository = SqliteAuthorizationRepository(database_path)
            repository.bootstrap_workspace(
                tenant_id="tenant-002", workspace_id="workspace-002", owner_user_id="admin",
                owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
                data_area="cloud_sync", cost_limit_cents=100, now=clock(),
            )
            AuthorizationService(repository=repository, audit_store=AuditEventStore(), clock=clock).set_membership(
                principal=IdentityPrincipal("admin", "admin-session", "admin-device", "tenant-002"),
                workspace_id="workspace-002", user_id=original.user_id, role=Role.VIEWER,
                expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
            with identity_repository.transaction() as connection:
                connection.execute(
                    "CREATE TRIGGER block_switch_intent BEFORE INSERT ON session_tenant_switch_outbox "
                    "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
                )
            before = identity_repository.entity_counts()

            with self.assertRaises(IdentityError) as failed:
                identity.switch_session_tenant(
                    access_token=original.access_token, tenant_id="tenant-002",
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )

            self.assertEqual(failed.exception.http_status, 503)
            self.assertEqual(identity.validate_access(
                original.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            ).session_id, original.session_id)
            self.assertEqual(identity_repository.entity_counts(), before)
            with identity_repository.transaction() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM session_tenant_switch_outbox"
                ).fetchone()[0], 0)
                self.assertEqual(connection.execute(
                    "SELECT state FROM refresh_families WHERE session_id=?",
                    (original.session_id,),
                ).fetchone()[0], "active")

    def test_admin_role_downgrade_rechecks_existing_access_and_rotated_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "runtime.sqlite3"
            identity, _, _, clock = create_service(database_path)
            credentials = native_login(identity)
            repository = SqliteAuthorizationRepository(database_path)
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
