from __future__ import annotations

from dataclasses import dataclass


class ReportGenerationError(ValueError):
    pass


@dataclass(frozen=True)
class ReportResult:
    format: str
    status: str
    summary: str
    body: str
    conclusion: str
    citations: tuple[tuple[str, int, int], ...]
    warnings: tuple[str, ...]
    lineage: dict[str, str]


class ReportGenerator:
    def generate(
        self,
        format: str,
        summary: str,
        body: str,
        conclusion: str,
        citations: list[tuple[str, int, int]],
        request_id: str,
        model_id: str,
    ) -> ReportResult:
        if format not in {"docx", "pdf"}:
            raise ReportGenerationError("FORMAT_UNSUPPORTED")
        if not summary or not body or not conclusion or not request_id or not model_id:
            raise ReportGenerationError("REPORT_INPUT_INCOMPLETE")
        warnings = () if citations else ("missing_evidence",)
        return ReportResult(
            format, "generated" if citations else "unverified", summary, body, conclusion,
            tuple(citations), warnings, {"request_id": request_id, "model_id": model_id},
        )
