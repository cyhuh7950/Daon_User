from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_identity_support import POLICY_VERSION, TRACE_ID, create_service, native_login


class IdentityPersistenceTests(unittest.TestCase):
    def test_iam_entities_refresh_device_and_step_up_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            service, repository, audit, clock = create_service(db_path)
            credentials = native_login(service)
            first_seen = repository.device_last_seen(credentials.device_id)
            clock.advance(seconds=5)
            service.validate_access(
                credentials.access_token,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            self.assertGreater(repository.device_last_seen(credentials.device_id), first_seen)
            service.trust_device(
                access_token=credentials.access_token,
                device_id=credentials.device_id,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
            repository.add_step_up_action("tenant-001", "organization_custom_sensitive_action")
            step_up = service.issue_step_up(
                access_token=credentials.access_token,
                action_group="organization_custom_sensitive_action",
                target_id="policy-001",
                policy_version=POLICY_VERSION,
                trace_id=TRACE_ID,
            )
            counts = repository.entity_counts()
            for table in (
                "users",
                "tenants",
                "memberships",
                "sessions",
                "refresh_families",
                "refresh_tokens",
                "devices",
                "oidc_transactions",
                "step_up_authorizations",
            ):
                self.assertGreaterEqual(counts[table], 1, table)
            repository.close()

            restarted, restarted_repository, _, _ = create_service(
                db_path, clock=clock, audit_store=audit
            )
            self.assertEqual(restarted_repository.device_state(credentials.device_id), "trusted")
            self.assertTrue(
                restarted.requires_step_up("tenant-001", "organization_custom_sensitive_action")
            )
            restarted.consume_step_up(
                step_up_authorization=step_up.authorization,
                access_token=credentials.access_token,
                action_group="organization_custom_sensitive_action",
                target_id="policy-001",
                policy_version=POLICY_VERSION,
                trace_id=TRACE_ID,
            )
            rotated = restarted.rotate_refresh(
                credentials.refresh_token,
                trace_id="trace-restart-refresh",
                policy_version=POLICY_VERSION,
            )
            self.assertEqual(
                restarted.validate_access(
                    rotated.access_token,
                    trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                ).device_id,
                credentials.device_id,
            )


if __name__ == "__main__":
    unittest.main()
