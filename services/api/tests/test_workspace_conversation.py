from __future__ import annotations

import unittest

from daon_user_api.workspace_conversation import ConversationError, WorkspaceConversation


class WorkspaceConversationTests(unittest.TestCase):
    def test_cloud_sync_query_returns_run_and_citations(self) -> None:
        conversation = WorkspaceConversation("tenant-a", "ws-a", data_realm="cloud_sync")
        result = conversation.ask("계약 기간", [("src-1", 2, 1, "계약 기간은 12개월")])
        self.assertTrue(result.run_id)
        self.assertEqual(result.citations[0].source_version, 2)

    def test_local_private_source_is_not_auto_included(self) -> None:
        conversation = WorkspaceConversation("tenant-a", "ws-a", data_realm="cloud_sync")
        result = conversation.ask("계약", [("local-1", 1, 1, "Local 계약", "local_private")])
        self.assertEqual(result.citations, ())
        self.assertEqual(result.status, "insufficient")

    def test_llm_general_answer_has_no_fake_citation(self) -> None:
        conversation = WorkspaceConversation("tenant-a", "ws-a", data_realm="cloud_sync")
        result = conversation.ask("일반 상식", [])
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(result.citations, ())


if __name__ == "__main__":
    unittest.main()
