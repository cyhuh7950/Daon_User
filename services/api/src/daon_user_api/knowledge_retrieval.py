from __future__ import annotations

from dataclasses import dataclass, replace
import re


@dataclass(frozen=True)
class KnowledgeItem:
    source_id: str
    tier: str
    text: str
    weight: float
    freshness: float = 1.0


@dataclass(frozen=True)
class RetrievalResult:
    status: str
    items: tuple[KnowledgeItem, ...]
    conflict: str | None = None


class KnowledgeRetriever:
    _TIER_RANK = {
        "daon_approved": 5,
        "user_registered": 4,
        "user_file": 3,
        "internet": 2,
        "llm_general": 1,
    }

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[가-힣A-Za-z0-9]+", text.lower()))

    def search(self, query: str, items: list[KnowledgeItem], limit: int = 10) -> RetrievalResult:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return RetrievalResult("insufficient", ())
        matches: list[KnowledgeItem] = []
        for item in items:
            if item.tier not in self._TIER_RANK:
                continue
            if query.lower().strip() in item.text.lower() or query_tokens.intersection(self._tokens(item.text)):
                matches.append(replace(item, weight=min(2.0, max(0.5, item.weight))))
        matches.sort(key=lambda item: (-self._TIER_RANK[item.tier], -item.weight, -item.freshness, item.source_id))
        matches = matches[:limit]
        if not matches:
            return RetrievalResult("insufficient", ())
        conflict = None
        top_tier = matches[0].tier
        top_texts = {item.text for item in matches if item.tier == top_tier}
        if len(top_texts) > 1:
            conflict = "IMPORTANT_CONFLICT"
        return RetrievalResult("review" if conflict else "sufficient", tuple(matches), conflict)
