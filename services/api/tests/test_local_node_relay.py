from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from daon_user_api.local_node import LocalNodeRelay, RelayAuthorizationError


class LocalNodeRelayTests(unittest.TestCase):
    def test_pairing_creates_online_identity_and_short_lived_certificate(self) -> None:
        relay = LocalNodeRelay(certificate_ttl=timedelta(minutes=5))
        identity = relay.pair("tenant-a", "device-a", "public-key-a")
        self.assertEqual(identity.status, "online")
        self.assertEqual(identity.tenant_id, "tenant-a")
        self.assertEqual(identity.device_id, "device-a")
        self.assertGreater(identity.certificate_expires_at, datetime.now(timezone.utc))

    def test_rotation_invalidates_previous_certificate(self) -> None:
        relay = LocalNodeRelay(certificate_ttl=timedelta(minutes=5))
        identity = relay.pair("tenant-a", "device-a", "public-key-a")
        old_certificate = identity.certificate_digest
        rotated = relay.rotate_certificate("tenant-a", "device-a", "public-key-b")
        self.assertNotEqual(old_certificate, rotated.certificate_digest)
        with self.assertRaisesRegex(RelayAuthorizationError, "CERTIFICATE_INVALID"):
            relay.authorize("tenant-a", "device-a", old_certificate, "outbound")
        self.assertTrue(
            relay.authorize("tenant-a", "device-a", rotated.certificate_digest, "outbound")
        )

    def test_public_inbound_is_rejected_and_outbound_is_allowed(self) -> None:
        relay = LocalNodeRelay()
        identity = relay.pair("tenant-a", "device-a", "public-key-a")
        with self.assertRaisesRegex(RelayAuthorizationError, "PUBLIC_INBOUND_FORBIDDEN"):
            relay.authorize("tenant-a", "device-a", identity.certificate_digest, "inbound")
        self.assertTrue(
            relay.authorize("tenant-a", "device-a", identity.certificate_digest, "outbound")
        )

    def test_revocation_invalidates_certificate_and_relay_authorization(self) -> None:
        relay = LocalNodeRelay()
        identity = relay.pair("tenant-a", "device-a", "public-key-a")
        relay.revoke("tenant-a", "device-a")
        with self.assertRaisesRegex(RelayAuthorizationError, "DEVICE_REVOKED"):
            relay.authorize("tenant-a", "device-a", identity.certificate_digest, "outbound")
        with self.assertRaisesRegex(RelayAuthorizationError, "DEVICE_REVOKED"):
            relay.verify_certificate("tenant-a", "device-a", identity.certificate_digest)


if __name__ == "__main__":
    unittest.main()
