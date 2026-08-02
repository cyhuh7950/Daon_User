from __future__ import annotations

from dataclasses import dataclass


class FormatRejected(ValueError):
    pass


@dataclass(frozen=True)
class FormatResult:
    format: str
    status: str
    evidence_location: str
    lineage: dict[str, str]


class FormatUnderstanding:
    _SUPPORTED = {"docx", "pptx", "xlsx", "csv", "txt", "markdown", "png", "jpeg"}

    def process(
        self,
        format: str,
        model_meaning: str | None,
        parser_evidence: str | None,
        evidence_location: str,
    ) -> FormatResult:
        normalized = format.lower().lstrip(".")
        if normalized not in self._SUPPORTED:
            raise FormatRejected("UNSUPPORTED_FORMAT")
        if not model_meaning:
            raise FormatRejected("PARSER_ONLY_NOT_READY")
        if not parser_evidence or not evidence_location:
            raise FormatRejected("EVIDENCE_REQUIRED")
        location_kind = evidence_location.split(":", 1)[0]
        expected = {"xlsx": "cell", "csv": "cell", "png": "region", "jpeg": "region"}
        if normalized in expected and location_kind != expected[normalized]:
            raise FormatRejected("EVIDENCE_LOCATION_INVALID")
        return FormatResult(
            normalized,
            "ready",
            evidence_location,
            {"understanding": "vision_llm_first", "parser_role": "validation_only"},
        )
