from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from test_identity_support import (
    FailingAuditStore,
    POLICY_VERSION,
    TRACE_ID,
    create_service,
    native_login,
)
from daon_user_api.identity import ClientKind, IdentityError, MINIMUM_STEP_UP_ACTION_GROUPS


class IdentityStepUpTests(unittest.TestCase):
    def test_minimum_actions_add_only_binding_ttl_and_one_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, audit, clock = create_service(Path(directory) / "identity.sqlite3")
            credentials = native_login(service)
            self.assertEqual(len(MINIMUM_STEP_UP_ACTION_GROUPS), 7)
            self.assertTrue(
                MINIMUM_STEP_UP_ACTION_GROUPS.issubset(
                    repository.required_step_up_actions("tenant-001")
                )
            )
            repository.add_step_up_action("tenant-001", "organization_custom_sensitive_action")
            self.assertTrue(
                service.requires_step_up("tenant-001", "organization_custom_sensitive_action")
            )
            with self.assertRaises(IdentityError) as remove_denied:
                repository.remove_step_up_action("tenant-001", next(iter(MINIMUM_STEP_UP_ACTION_GROUPS)))
            self.assertEqual(remove_denied.exception.code, "STEP_UP_ACTION_REMOVE_DENIED")

            issued = service.issue_step_up(
                access_token=credentials.access_token,
                action_group="device_session_or_sync_key_revoke",
                target_id=credentials.device_id,
                policy_version=POLICY_VERSION,
                trace_id=TRACE_ID,
            )
            self.assertEqual((issued.expires_at - issued.issued_at).total_seconds(), 300)
            with self.assertRaises(IdentityError) as too_long:
                service.issue_step_up(
                    access_token=credentials.access_token,
                    action_group="device_session_or_sync_key_revoke",
                    target_id=credentials.device_id,
                    policy_version=POLICY_VERSION,
                    trace_id=TRACE_ID,
                    ttl_seconds=601,
                )
            self.assertEqual(too_long.exception.code, "STEP_UP_TTL_INVALID")

            with self.assertRaises(IdentityError) as binding:
                service.consume_step_up(
                    step_up_authorization=issued.authorization,
                    access_token=credentials.access_token,
                    action_group="device_session_or_sync_key_revoke",
                    target_id="device-other",
                    policy_version=POLICY_VERSION,
                    trace_id=TRACE_ID,
                )
            self.assertEqual(binding.exception.code, "STEP_UP_BINDING_DENIED")
            service.consume_step_up(
                step_up_authorization=issued.authorization,
                access_token=credentials.access_token,
                action_group="device_session_or_sync_key_revoke",
                target_id=credentials.device_id,
                policy_version=POLICY_VERSION,
                trace_id=TRACE_ID,
            )
            with self.assertRaises(IdentityError) as reuse:
                service.consume_step_up(
                    step_up_authorization=issued.authorization,
                    access_token=credentials.access_token,
                    action_group="device_session_or_sync_key_revoke",
                    target_id=credentials.device_id,
                    policy_version=POLICY_VERSION,
                    trace_id=TRACE_ID,
                )
            self.assertEqual(reuse.exception.code, "STEP_UP_REUSED")
            self.assertTrue(audit.verify_integrity().valid)
            actions = {event.action for event in audit.list(tenant_id="tenant-001").items}
            self.assertTrue(
                {"identity.step_up.issued", "identity.step_up.used"}.issubset(actions)
            )

            expiring = service.issue_step_up(
                access_token=credentials.access_token,
                action_group="permanent_delete_or_restore_rollback",
                target_id="output-001",
                policy_version=POLICY_VERSION,
                trace_id=TRACE_ID,
                ttl_seconds=30,
            )
            clock.advance(seconds=31)
            with self.assertRaises(IdentityError) as expired:
                service.consume_step_up(
                    step_up_authorization=expiring.authorization,
                    access_token=credentials.access_token,
                    action_group="permanent_delete_or_restore_rollback",
                    target_id="output-001",
                    policy_version=POLICY_VERSION,
                    trace_id=TRACE_ID,
                )
            self.assertEqual(expired.exception.code, "STEP_UP_EXPIRED")

            with self.assertRaises(IdentityError) as missing:
                service.consume_step_up(
                    step_up_authorization=None,
                    access_token=credentials.access_token,
                    action_group="permanent_delete_or_restore_rollback",
                    target_id="output-001",
                    policy_version=POLICY_VERSION,
                    trace_id=TRACE_ID,
                )
            self.assertEqual(missing.exception.code, "STEP_UP_REQUIRED")

    def test_concurrent_step_up_consumption_is_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, _ = create_service(Path(directory) / "identity.sqlite3")
            credentials = native_login(service)
            issued = service.issue_step_up(
                access_token=credentials.access_token,
                action_group="final_approval_or_knowledge_registration",
                target_id="output-001",
                policy_version=POLICY_VERSION,
                trace_id=TRACE_ID,
            )
            barrier = threading.Barrier(3)
            outcomes: list[str] = []

            def worker() -> None:
                barrier.wait()
                try:
                    service.consume_step_up(
                        step_up_authorization=issued.authorization,
                        access_token=credentials.access_token,
                        action_group="final_approval_or_knowledge_registration",
                        target_id="output-001",
                        policy_version=POLICY_VERSION,
                        trace_id="trace-step-up-race",
                    )
                    outcomes.append("used")
                except IdentityError as error:
                    outcomes.append(error.code)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=10)
            self.assertCountEqual(outcomes, ["used", "STEP_UP_REUSED"])

    def test_device_revoke_is_step_up_bound_and_atomically_revokes_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, audit, _ = create_service(Path(directory) / "identity.sqlite3")
            credentials = native_login(service)
            with self.assertRaises(IdentityError) as required:
                service.revoke_device(
                    access_token=credentials.access_token,
                    device_id=credentials.device_id,
                    step_up_authorization=None,
                    policy_version=POLICY_VERSION,
                    trace_id=TRACE_ID,
                )
            self.assertEqual(required.exception.code, "STEP_UP_REQUIRED")
            self.assertEqual(repository.device_state(credentials.device_id), "registered")

            issued = service.issue_step_up(
                access_token=credentials.access_token,
                action_group="device_session_or_sync_key_revoke",
                target_id=credentials.device_id,
                policy_version=POLICY_VERSION,
                trace_id=TRACE_ID,
            )
            event = service.revoke_device(
                access_token=credentials.access_token,
                device_id=credentials.device_id,
                step_up_authorization=issued.authorization,
                policy_version=POLICY_VERSION,
                trace_id=TRACE_ID,
            )
            self.assertTrue(event.sync_key_revoke_required)
            self.assertEqual(repository.device_state(credentials.device_id), "revoked")
            with self.assertRaises(IdentityError) as revoked:
                service.validate_access(
                    credentials.access_token,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(revoked.exception.code, "SESSION_REVOKED")
            self.assertTrue(audit.verify_integrity().valid)
            actions = {event.action for event in audit.list(tenant_id="tenant-001").items}
            self.assertTrue(
                {"identity.session.revoked", "identity.device.revoked"}.issubset(actions)
            )

    def test_audit_append_failure_rolls_back_security_write_without_raw_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, _, _ = create_service(
                Path(directory) / "identity.sqlite3", audit_store=FailingAuditStore()
            )
            with self.assertRaises(IdentityError) as failed:
                service.begin_oidc_login(
                    issuer="https://login.example.com",
                    client_id="daon-native",
                    audience="daon-user-api",
                    redirect_uri="com.sinsan.daon:/oidc/callback",
                    client_kind=ClientKind.NATIVE,
                    tenant_id="tenant-001",
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
            self.assertEqual(failed.exception.code, "AUDIT_WRITE_FAILED")
            self.assertEqual(repository.entity_counts()["oidc_transactions"], 0)
            self.assertNotIn("provider", str(failed.exception))
            self.assertNotIn("credential", str(failed.exception))


if __name__ == "__main__":
    unittest.main()
