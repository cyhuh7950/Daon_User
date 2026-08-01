from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    source_id: str
    source_version: int
    page: int
    text: str
    score: float = 0.0


class PdfIndex:
    def __init__(self) -> None:
        self._chunks: list[IndexedChunk] = []

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[가-힣A-Za-z0-9]+", text.lower()))

    def add_chunks(self, source_id: str, source_version: int, chunks: list[tuple[int, str]]) -> None:
        if not source_id or source_version < 1:
            raise ValueError("source_id and positive source_version are required")
        self._chunks = [
            chunk
            for chunk in self._chunks
            if not (chunk.source_id == source_id and chunk.source_version == source_version)
        ]
        for ordinal, (page, text) in enumerate(chunks, start=1):
            if page < 1 or not text.strip():
                raise ValueError("page and text are required")
            self._chunks.append(
                IndexedChunk(f"{source_id}:v{source_version}:c{ordinal}", source_id, source_version, page, text)
            )

    def search(self, query: str, *, source_id: str, source_version: int, limit: int = 10) -> list[IndexedChunk]:
        query_tokens = self._tokens(query)
        if not query_tokens or limit < 1:
            return []
        matches: list[IndexedChunk] = []
        for chunk in self._chunks:
            if chunk.source_id != source_id or chunk.source_version != source_version:
                continue
            overlap = query_tokens.intersection(self._tokens(chunk.text))
            if overlap:
                matches.append(IndexedChunk(**{**chunk.__dict__, "score": len(overlap) / len(query_tokens)}))
        return sorted(matches, key=lambda item: (-item.score, item.page, item.chunk_id))[:limit]
