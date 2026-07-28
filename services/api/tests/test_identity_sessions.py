from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from test_identity_support import POLICY_VERSION, TRACE_ID, create_service, native_login
from daon_user_api.identity import IdentityError


class IdentitySessionTests(unittest.TestCase):
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
