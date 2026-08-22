from __future__ import annotations

import unittest

from daon_user_api.approved_knowledge_connector import ApprovedKnowledgeConnector
from daon_user_api.mcp_connector import (
    Connector,
    ConnectorError,
    ConnectorRegistry,
    ConnectorSource,
    connector_source_id,
    create_open_law_connector,
)


class ConnectorContractTests(unittest.TestCase):
    def test_open_law_connector_is_unavailable_without_server_credential(self) -> None:
        connector = create_open_law_connector()
        self.assertEqual(connector.kind, "mcp")
        self.assertEqual(connector.endpoint_label, "open.law.go.kr")
        registry = ConnectorRegistry((connector,))
        self.assertEqual(registry.list()[0].status, "unavailable")
        self.assertEqual(registry.sources(connector.connector_id), ())

    def test_reconnect_and_disconnect_are_explicit_and_keep_record(self) -> None:
        state = {"available": False}
        connector = Connector(
            connector_id="mcp-test", kind="mcp", name="테스트",
            endpoint_label="test", reconnect=lambda: state["available"],
            sources=[ConnectorSource("mcp-test:1", "mcp-test", "법령")],
        )
        registry = ConnectorRegistry((connector,))
        state["available"] = True
        self.assertEqual(registry.reconnect("mcp-test").status, "connected")
        self.assertTrue(registry.sources("mcp-test")[0].usable)
        self.assertEqual(registry.disconnect("mcp-test").status, "disconnected")
        unavailable = registry.sources("mcp-test")[0]
        self.assertEqual(unavailable.source_state, "unavailable")
        self.assertFalse(unavailable.usable)
        self.assertEqual(registry.get("mcp-test").name, "테스트")

    def test_approved_knowledge_uses_the_same_connector_shape(self) -> None:
        approved = ApprovedKnowledgeConnector(token="secret", timeout_seconds=3, max_retries=1)
        approved.publish("law-1", "승인 법령", version=1, expires_at="2099-12-31T00:00:00Z")
        connector = approved.as_connector()
        self.assertEqual(connector.kind, "daon_approved_knowledge")
        self.assertEqual(connector.sources[0].source_id, "daon-approved-knowledge:law-1")
        self.assertEqual(connector.sources[0].source_state, "ready")

    def test_invalid_connector_and_source_id_fail_closed(self) -> None:
        with self.assertRaisesRegex(ConnectorError, "CONNECTOR_KIND_UNSUPPORTED"):
            ConnectorRegistry().register(Connector("x", "unknown", "x", "x", lambda: True))
        with self.assertRaisesRegex(ConnectorError, "CONNECTOR_SOURCE_ID_INVALID"):
            connector_source_id("", "remote")


if __name__ == "__main__":
    unittest.main()
