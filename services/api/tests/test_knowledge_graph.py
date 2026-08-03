import unittest

from daon_user_api.knowledge_graph import KnowledgeGraph, KnowledgeGraphError


class KnowledgeGraphTests(unittest.TestCase):
    def test_builds_nodes_edges_and_evidence(self):
        graph = KnowledgeGraph().build(
            nodes=[{"id": "n1", "label": "A", "confidence": "verified", "evidence": "p1"}],
            edges=[{"id": "e1", "source": "n1", "target": "n1", "relation": "ref", "evidence": "p1"}],
        )
        self.assertEqual(graph.nodes[0]["id"], "n1")
        self.assertEqual(graph.edges[0]["relation"], "ref")

    def test_rejects_verified_without_evidence(self):
        with self.assertRaisesRegex(KnowledgeGraphError, "EVIDENCE_REQUIRED"):
            KnowledgeGraph().build([{"id": "n1", "label": "A", "confidence": "verified", "evidence": ""}], [])

    def test_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(KnowledgeGraphError, "ID_DUPLICATE"):
            KnowledgeGraph().build([{"id": "n1", "label": "A", "confidence": "unverified", "evidence": ""}, {"id": "n1", "label": "B", "confidence": "unverified", "evidence": ""}], [])
