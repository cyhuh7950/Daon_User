from __future__ import annotations

import unittest

from daon_user_api.local_conversation import LocalConversation, LocalConversationError


class LocalConversationTests(unittest.TestCase):
    def test_offline_local_query_returns_local_citation(self) -> None:
        conversation = LocalConversation("tenant-a", "ws-local", model_id="local-v1", network_online=False)
        result = conversation.ask("계약", [("src-local", 3, 1, "Local 계약 기간")])
        self.assertEqual(result.status, "sufficient")
        self.assertEqual(result.citations[0].source_id, "src-local")
        self.assertEqual(result.egress, "none")

    def test_cloud_or_external_source_is_blocked(self) -> None:
        conversation = LocalConversation("tenant-a", "ws-local", model_id="local-v1", network_online=False)
        with self.assertRaisesRegex(LocalConversationError, "LOCAL_PRIVATE_SOURCE_REQUIRED"):
            conversation.ask("계약", [("src-cloud", 1, 1, "Cloud 계약", "cloud_sync")])

    def test_missing_local_model_fails_closed(self) -> None:
        conversation = LocalConversation("tenant-a", "ws-local", model_id=None, network_online=False)
        with self.assertRaisesRegex(LocalConversationError, "LOCAL_MODEL_UNAVAILABLE"):
            conversation.ask("계약", [("src-local", 1, 1, "Local 계약")])


if __name__ == "__main__":
    unittest.main()
