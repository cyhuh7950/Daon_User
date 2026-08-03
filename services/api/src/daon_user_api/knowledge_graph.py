from dataclasses import dataclass
from typing import Any


class KnowledgeGraphError(ValueError):
    pass


@dataclass(frozen=True)
class KnowledgeGraphResult:
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]


class KnowledgeGraph:
    _CONFIDENCE = {"verified", "unverified", "needs_review"}

    def build(self, nodes, edges):
        node_ids = [node.get("id", "") for node in nodes]
        edge_ids = [edge.get("id", "") for edge in edges]
        if len(node_ids) != len(set(node_ids)) or len(edge_ids) != len(set(edge_ids)):
            raise KnowledgeGraphError("ID_DUPLICATE")
        for node in nodes:
            confidence = node.get("confidence", "")
            if confidence not in self._CONFIDENCE:
                raise KnowledgeGraphError("CONFIDENCE_UNSUPPORTED")
            if confidence == "verified" and not node.get("evidence"):
                raise KnowledgeGraphError("EVIDENCE_REQUIRED")
        return KnowledgeGraphResult(tuple(nodes), tuple(edges))
