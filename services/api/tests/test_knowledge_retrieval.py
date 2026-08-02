from __future__ import annotations

import unittest

from daon_user_api.knowledge_retrieval import KnowledgeItem, KnowledgeRetriever


class KnowledgeRetrievalTests(unittest.TestCase):
    def test_daon_approved_tier_wins_over_lower_tier_even_with_weight(self) -> None:
        items = [
            KnowledgeItem("approved", "daon_approved", "정책은 12개월", 2.0),
            KnowledgeItem("user", "user_file", "정책은 24개월", 2.0),
        ]
        result = KnowledgeRetriever().search("정책", items)
        self.assertEqual(result.items[0].source_id, "approved")
        self.assertEqual(result.status, "sufficient")

    def test_weight_is_clamped_and_same_tier_is_sorted(self) -> None:
        items = [
            KnowledgeItem("a", "user_file", "계약", 9.0),
            KnowledgeItem("b", "user_file", "계약", 0.1),
        ]
        result = KnowledgeRetriever().search("계약", items)
        self.assertEqual(result.items[0].source_id, "a")
        self.assertEqual(result.items[0].weight, 2.0)
        self.assertEqual(result.items[1].weight, 0.5)

    def test_same_tier_conflict_is_review(self) -> None:
        items = [
            KnowledgeItem("a", "internet", "세율은 10%", 1.0),
            KnowledgeItem("b", "internet", "세율은 20%", 1.0),
        ]
        result = KnowledgeRetriever().search("세율", items)
        self.assertEqual(result.status, "review")
        self.assertEqual(result.conflict, "IMPORTANT_CONFLICT")


if __name__ == "__main__":
    unittest.main()
