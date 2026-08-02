from __future__ import annotations

import unittest

from daon_user_api.windows_cloud_routing import CloudRoute, CloudRoutingError


class WindowsCloudRoutingTests(unittest.TestCase):
    def test_selected_deployment_network_and_audit_are_aligned(self) -> None:
        route = CloudRoute().select("external-v", data_realm="cloud_sync", role="vision", mode="pinned")
        self.assertEqual(route.deployment_id, "external-v")
        self.assertEqual(route.egress, "external")
        self.assertEqual(route.audit_reason, "PINNED_SELECTION")

    def test_local_private_blocks_cloud_egress(self) -> None:
        with self.assertRaisesRegex(CloudRoutingError, "LOCAL_PRIVATE_EGRESS_BLOCKED"):
            CloudRoute().select("external-v", data_realm="local_private", role="vision", mode="pinned")

    def test_auto_fallback_requires_approved_candidate(self) -> None:
        with self.assertRaisesRegex(CloudRoutingError, "NO_APPROVED_CANDIDATE"):
            CloudRoute().select("unapproved", data_realm="cloud_sync", role="vision", mode="auto")


if __name__ == "__main__":
    unittest.main()
