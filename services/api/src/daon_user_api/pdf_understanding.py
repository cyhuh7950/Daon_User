from __future__ import annotations

from dataclasses import dataclass


class UnderstandingRejected(ValueError):
    """Raised when a PDF cannot pass the Vision/LLM-first understanding gate."""


@dataclass(frozen=True)
class UnderstandingResult:
    source_id: str
    status: str
    substates: tuple[str, ...]
    evidence_pages: tuple[int, ...]
    lineage: dict[str, str]
    conflict: str | None = None


class PdfUnderstandingPipeline:
    _SUBSTATES = (
        "vision_llm_understanding",
        "parser_ocr_validation",
        "evidence_reconciliation",
    )

    def process(
        self,
        *,
        source_id: str,
        model_output: dict[str, object] | None,
        parser_output: dict[str, object] | None,
        model_id: str | None,
        prompt_version: str | None,
        policy_version: str | None,
    ) -> UnderstandingResult:
        if not source_id:
            raise UnderstandingRejected("SOURCE_REQUIRED")
        if not model_output or not model_id or not prompt_version or not policy_version:
            raise UnderstandingRejected("PARSER_ONLY_NOT_READY")
        if not parser_output or not isinstance(parser_output.get("page"), int):
            raise UnderstandingRejected("EVIDENCE_RECONCILIATION_REQUIRED")
        summary = str(model_output.get("summary", "")).strip()
        parser_text = str(parser_output.get("text", "")).strip()
        if not summary:
            raise UnderstandingRejected("UNDERSTANDING_EMPTY")
        lineage = {
            "model_id": model_id,
            "prompt_version": prompt_version,
            "policy_version": policy_version,
            "parser_role": "validation_only",
        }
        conflict = None if summary == parser_text else "UNDERSTANDING_PARSER_CONFLICT"
        return UnderstandingResult(
            source_id=source_id,
            status="review" if conflict else "ready",
            substates=self._SUBSTATES,
            evidence_pages=(parser_output["page"],),
            lineage=lineage,
            conflict=conflict,
        )
