from __future__ import annotations

import unittest

from daon_user_api.approved_knowledge_connector import ApprovedKnowledgeConnector, ConnectorError


class ApprovedKnowledgeConnectorTests(unittest.TestCase):
    def test_authorized_read_and_search_preserve_version(self) -> None:
        connector = ApprovedKnowledgeConnector(token="token", timeout_seconds=3, max_retries=2)
        connector.publish("ak-1", "승인 정책", version=7, expires_at="2026-12-31T00:00:00Z")
        self.assertEqual(connector.read("ak-1", permission="read").version, 7)
        self.assertEqual(connector.search("정책", permission="read")[0].version, 7)

    def test_missing_permission_and_expiry_fail_closed(self) -> None:
        connector = ApprovedKnowledgeConnector(token="token", timeout_seconds=3, max_retries=2)
        connector.publish("ak-1", "승인 정책", version=1, expires_at="2020-01-01T00:00:00Z")
        with self.assertRaisesRegex(ConnectorError, "PERMISSION_REQUIRED"):
            connector.read("ak-1", permission="none")
        with self.assertRaisesRegex(ConnectorError, "KNOWLEDGE_EXPIRED"):
            connector.read("ak-1", permission="read")

    def test_disconnect_and_reconnect_are_explicit(self) -> None:
        connector = ApprovedKnowledgeConnector(token="token", timeout_seconds=3, max_retries=2)
        connector.disconnect()
        with self.assertRaisesRegex(ConnectorError, "CONNECTOR_DISCONNECTED"):
            connector.search("정책", permission="read")
        connector.reconnect()
        self.assertEqual(connector.status, "connected")


if __name__ == "__main__":
    unittest.main()
