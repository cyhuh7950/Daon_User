from __future__ import annotations

from dataclasses import dataclass


class CitationError(ValueError):
    pass


@dataclass(frozen=True)
class Citation:
    chunk_id: str
    source_id: str
    source_version: int
    page: int
    context: str


@dataclass(frozen=True)
class CitationResult:
    status: str
    citations: tuple[Citation, ...]


class CitationBuilder:
    def build(
        self, source_id: str, source_version: int, entries: list[tuple[str, int, int, str]]
    ) -> CitationResult:
        citations: list[Citation] = []
        for chunk_id, entry_version, page, context in entries:
            if not chunk_id or page < 1 or not context:
                raise CitationError("CITATION_INVALID")
            if entry_version != source_version:
                raise CitationError("SOURCE_VERSION_MISMATCH")
            citations.append(Citation(chunk_id, source_id, entry_version, page, context))
        status = "insufficient" if not citations else "partial" if len(citations) == 1 else "sufficient"
        return CitationResult(status, tuple(citations))
