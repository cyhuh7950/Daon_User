"""Grounded evidence-report domain contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class StudioReportError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


def _id(value: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise StudioReportError("STUDIO_INPUT_INVALID")
    return value


def _text(value: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise StudioReportError("STUDIO_INPUT_INVALID")
    return value.strip()


def compose_grounded_report_content(title: str, answer: str) -> str:
    safe_title = _text(title, 200)
    safe_answer = _text(answer, 8_000)
    summary = safe_answer if len(safe_answer) <= 500 else f"{safe_answer[:497]}..."
    return (
        f"# {safe_title}\n\n"
        f"## 요약\n{summary}\n\n"
        f"## 본문\n{safe_answer}\n\n"
        "## 결론\n위 내용은 연결된 Citation 근거의 범위 안에서 검토해야 합니다."
    )


@dataclass(frozen=True, slots=True)
class StudioReportContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str
    notebook_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.workspace_id, self.actor_id, self.trace_id, self.policy_version):
            _id(value)
        if self.notebook_id is not None:
            _id(self.notebook_id)


@dataclass(frozen=True, slots=True)
class StudioReportCreateRequest:
    source_id: str
    source_version_id: str
    run_id: str
    run_result_id: str
    title: str
    purpose: str

    def __post_init__(self) -> None:
        for value in (self.source_id, self.source_version_id, self.run_id, self.run_result_id):
            _id(value)
        object.__setattr__(self, "title", _text(self.title, 200))
        object.__setattr__(self, "purpose", _text(self.purpose, 500))


@dataclass(frozen=True, slots=True)
class StudioCitation:
    citation_id: str
    source_id: str
    source_version_id: str
    evidence_span_id: str
    page: int


@dataclass(frozen=True, slots=True)
class StudioOutputProjection:
    studio_output_id: str
    output_version_id: str
    output_type: str
    title: str
    purpose: str
    status: str
    content: str
    run_id: str
    run_result_id: str
    citations: tuple[StudioCitation, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceSourceProjection:
    source_id: str
    source_version_id: str
    filename: str
    source_state: str
    processing_state: str
    job_state: str


class StudioReportRepository(Protocol):
    def create_report(self, context: StudioReportContext, request: StudioReportCreateRequest,
                      idempotency_key: str) -> tuple[StudioOutputProjection, bool]: ...
    def list_outputs(self, context: StudioReportContext) -> tuple[StudioOutputProjection, ...]: ...


class StudioReportService:
    def __init__(self, repository: StudioReportRepository) -> None:
        self._repository = repository

    @property
    def creation_license_authoritative(self) -> bool:
        return getattr(self._repository, "creation_license_authoritative", False) is True

    def create(self, context: StudioReportContext, request: StudioReportCreateRequest,
               idempotency_key: str) -> tuple[StudioOutputProjection, bool]:
        _id(idempotency_key)
        return self._repository.create_report(context, request, idempotency_key)

    def list_outputs(self, context: StudioReportContext) -> tuple[StudioOutputProjection, ...]:
        return self._repository.list_outputs(context)
