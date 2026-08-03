from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DocumentDraftResult:
    template_id: str
    sections: tuple[dict[str, Any], ...]
    review_state: str
    warnings: tuple[str, ...]
    lineage: dict[str, str]


class DocumentDraft:
    _STATES = {"draft", "in_review", "revision_requested", "approved"}

    def create(self, template_id, sections, request_id, review_state="draft"):
        if review_state not in self._STATES:
            raise ValueError("REVIEW_STATE_UNSUPPORTED")
        warnings = set()
        normalized = []
        for section in sections:
            evidence = list(section.get("evidence", []))
            if not evidence:
                warnings.add("unverified")
            normalized.append({"title": section.get("title", ""), "body": section.get("body", ""), "evidence": evidence})
        return DocumentDraftResult(template_id, tuple(normalized), review_state, tuple(sorted(warnings)), {"request_id": request_id})
