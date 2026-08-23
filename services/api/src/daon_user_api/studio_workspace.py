from __future__ import annotations

import re
from dataclasses import asdict
from dataclasses import dataclass
from typing import Mapping, Protocol

from .compliance_check import ComplianceChecker
from .comparison_table import ComparisonTable
from .document_draft import DocumentDraft
from .knowledge_graph import KnowledgeGraph
from .report_generation import ReportGenerator

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
OUTPUT_TYPES = frozenset({
    "evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft",
    "slides", "infographic", "flashcards", "quiz", "audio", "video",
})
FORMATS = {
    "evidence_report": frozenset({"docx", "pdf"}), "compliance_checklist": frozenset({"xlsx", "csv", "pdf"}),
    "comparison_table": frozenset({"xlsx", "csv", "pdf"}), "knowledge_map": frozenset({"json", "svg", "png", "pdf"}),
    "business_draft": frozenset({"docx", "pdf"}),
    "slides": frozenset({"pdf", "json"}),
    "infographic": frozenset({"svg", "png", "pdf", "json"}),
    "flashcards": frozenset({"json", "csv", "pdf"}),
    "quiz": frozenset({"json", "csv", "pdf"}),
    # Audio/video providers and binary encoders are not present in this runtime.
    # They remain explicit contracts and fail closed instead of fabricating media.
    "audio": frozenset({"json"}),
    "video": frozenset({"json"}),
}


def build_structured_output(
    request: "StudioGenerationRequest", answer: str,
    citations: list[Mapping[str, object]], generation_request_id: str,
) -> dict[str, object]:
    evidence = [
        {
            "citation_id": str(item.get("citation_id", "")),
            "source_version_id": str(item.get("source_version_id", "")),
            "evidence_span_id": str(item.get("evidence_span_id", "")),
            "page": int(item.get("page", 0) or 0),
        }
        for item in citations
    ]
    model_reference = request.run_id or "source_only"
    result_reference = request.run_result_id or generation_request_id
    citation_tuples = [(item["citation_id"], item["page"], item["page"]) for item in evidence]
    if request.output_type == "evidence_report":
        result = ReportGenerator().generate(
            request.output_format, request.purpose, answer, f"{request.purpose}에 대한 근거 기반 결론",
            citation_tuples, generation_request_id, model_reference,
        )
        return asdict(result)
    if request.output_type == "compliance_checklist":
        items = [{
            "item_id": f"check-{index}", "judgement": "needs_review",
            "evidence": f"{item['citation_id']} page {item['page']}", "action": "전문가 검토",
        } for index, item in enumerate(evidence, 1)]
        result = ComplianceChecker().check(
            items, request.ruleset_version_id or "ruleset-none", request.ruleset_version_id or "none",
            generation_request_id, model_reference,
        )
        return asdict(result)
    if request.output_type == "comparison_table":
        baseline = {f"근거 {index}": item["citation_id"] for index, item in enumerate(evidence, 1)}
        current = {f"근거 {index}": answer for index, _item in enumerate(evidence, 1)}
        refs = {key: (f"{item['citation_id']} page {item['page']}", f"{item['citation_id']} page {item['page']}") for key, item in zip(baseline, evidence)}
        return asdict(ComparisonTable().compare(baseline, current, "evidence", result_reference, refs))
    if request.output_type == "knowledge_map":
        nodes = [{"id": f"node-{index}", "label": item["citation_id"], "confidence": "verified", "evidence": f"page {item['page']}"} for index, item in enumerate(evidence, 1)]
        edges = [{"id": f"edge-{index}", "source": nodes[index - 1]["id"], "target": nodes[index]["id"], "condition": "근거 순서"} for index in range(1, len(nodes))]
        return asdict(KnowledgeGraph().build(nodes, edges))
    if request.output_type == "slides":
        return {
            "title": request.purpose,
            "slides": [
                {"slide_number": 1, "title": "핵심 요약", "body": answer, "evidence": [f"{item['citation_id']} page {item['page']}" for item in evidence]},
                *[
                    {"slide_number": index + 2, "title": f"근거 {index}", "body": f"{item['citation_id']}에서 확인된 내용", "evidence": [f"{item['citation_id']} page {item['page']}"]}
                    for index, item in enumerate(evidence)
                ],
            ],
            "warnings": [],
        }
    if request.output_type == "infographic":
        return {
            "title": request.purpose,
            "summary": answer,
            "metrics": [{"label": f"근거 {index}", "value": item["citation_id"], "evidence": f"page {item['page']}"} for index, item in enumerate(evidence, 1)],
            "warnings": [],
        }
    if request.output_type == "flashcards":
        return {
            "title": request.purpose,
            "cards": [{"id": f"card-{index}", "question": f"{item['citation_id']}의 핵심 내용은 무엇인가요?", "answer": answer, "evidence": f"{item['citation_id']} page {item['page']}"} for index, item in enumerate(evidence, 1)],
            "warnings": [],
        }
    if request.output_type == "quiz":
        return {
            "title": request.purpose,
            "questions": [{"id": f"quiz-{index}", "question": f"{item['citation_id']}에 대한 설명으로 옳은 것은?", "options": [answer, "근거에서 확인되지 않음"], "answer_index": 0, "explanation": f"{item['citation_id']} page {item['page']}"} for index, item in enumerate(evidence, 1)],
            "warnings": [],
        }
    if request.output_type in {"audio", "video"}:
        raise StudioError("STUDIO_OUTPUT_UNAVAILABLE", 409)
    sections = [{"title": f"Section {index}", "body": answer, "evidence": [f"{item['citation_id']} page {item['page']}"]} for index, item in enumerate(evidence, 1)]
    return asdict(DocumentDraft().create(request.structure, sections, generation_request_id))


class StudioError(RuntimeError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _identifier(value: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise StudioError("STUDIO_INPUT_INVALID")
    return value


def _required(value: str, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise StudioError("STUDIO_INPUT_INVALID")
    return value.strip()


@dataclass(frozen=True, slots=True)
class StudioContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str
    notebook_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.workspace_id, self.actor_id, self.trace_id, self.policy_version):
            _identifier(value)
        if self.notebook_id is not None:
            _identifier(self.notebook_id)


@dataclass(frozen=True, slots=True)
class StudioGenerationRequest:
    output_type: str
    source_id: str | None
    source_version_ids: tuple[str, ...]
    run_id: str | None
    run_result_id: str | None
    purpose: str
    audience: str
    ruleset_version_id: str | None
    length: str
    structure: str
    output_format: str
    review_condition: str
    language: str = "ko"
    source_only: bool = False

    def __post_init__(self) -> None:
        if self.output_type not in OUTPUT_TYPES or self.output_format not in FORMATS.get(self.output_type, ()):
            raise StudioError("STUDIO_INPUT_INVALID")
        for value in (*self.source_version_ids,):
            _identifier(value)
        if self.source_only:
            if self.source_id is not None:
                _identifier(self.source_id)
        else:
            for value in (self.source_id, self.run_id, self.run_result_id):
                _identifier(value)
        if not self.source_version_ids:
            raise StudioError("STUDIO_INPUT_INVALID")
        if self.ruleset_version_id is not None:
            _identifier(self.ruleset_version_id)
        if self.language not in {"ko", "en"}:
            raise StudioError("STUDIO_INPUT_INVALID")
        for value in (self.purpose, self.audience, self.length, self.structure, self.review_condition):
            _required(value)


class StudioWorkspaceRepository(Protocol):
    def create_generation(self, context: StudioContext, request: StudioGenerationRequest, idempotency_key: str): ...
    def create_version(self, context: StudioContext, output_id: str, revision: Mapping[str, object], idempotency_key: str): ...
    def record_action(self, context: StudioContext, action: str, payload: Mapping[str, object], idempotency_key: str): ...
    def list_outputs(self, context: StudioContext): ...
    def list_versions(self, context: StudioContext, output_id: str): ...
    def export_output(self, context: StudioContext, output_id: str, version_id: str, format_name: str): ...


class StudioWorkspaceService:
    def __init__(self, repository: StudioWorkspaceRepository) -> None:
        self._repository = repository

    @property
    def creation_license_authoritative(self) -> bool:
        return getattr(self._repository, "creation_license_authoritative", False) is True

    def generate(self, context: StudioContext, request: StudioGenerationRequest, idempotency_key: str):
        _identifier(idempotency_key)
        return self._repository.create_generation(context, request, idempotency_key)

    def enqueue(self, context: StudioContext, request: StudioGenerationRequest, idempotency_key: str):
        _identifier(idempotency_key)
        enqueue = getattr(self._repository, "enqueue_generation", None)
        if not callable(enqueue):
            raise StudioError("STUDIO_ASYNC_UNAVAILABLE", 503)
        return enqueue(context, request, idempotency_key)

    def generation_job(self, context: StudioContext, job_id: str):
        _identifier(job_id)
        get_job = getattr(self._repository, "get_generation_job", None)
        if not callable(get_job):
            raise StudioError("STUDIO_ASYNC_UNAVAILABLE", 503)
        return get_job(context, job_id)

    def revise(self, context: StudioContext, output_id: str, revision: Mapping[str, object], idempotency_key: str):
        _identifier(output_id); _identifier(idempotency_key)
        if revision.get("revision_type") not in {"user_edit", "ai_regeneration", "settings_change"}:
            raise StudioError("REVISION_TYPE_INVALID")
        _identifier(str(revision.get("previous_version_id", "")))
        if not isinstance(revision.get("change_reason"), str) or not str(revision["change_reason"]).strip():
            raise StudioError("CHANGE_REASON_REQUIRED")
        if revision.get("revision_type") == "settings_change":
            settings = revision.get("settings")
            required = {"purpose", "audience", "language", "source_version_ids", "ruleset_version_id", "length", "structure", "output_format", "review_condition"}
            if not isinstance(settings, Mapping) or set(settings) != required:
                raise StudioError("STUDIO_SETTINGS_INCOMPLETE")
        return self._repository.create_version(context, output_id, revision, idempotency_key)

    def action(self, context: StudioContext, action: str, payload: Mapping[str, object], idempotency_key: str):
        _identifier(idempotency_key)
        if action not in {"review", "approval_request", "approval", "delivery", "knowledge_registration"}:
            raise StudioError("STUDIO_ACTION_INVALID")
        target_id = _identifier(str(payload.get("output_version_id", "")))
        required_links = {
            "approval_request": "review_request_id", "approval": "approval_request_id",
            "delivery": "approval_id",
        }
        if action in required_links:
            _identifier(str(payload.get(required_links[action], "")))
        if action == "approval" and payload.get("decision") not in {"approved", "rejected"}:
            raise StudioError("APPROVAL_DECISION_INVALID")
        if action == "delivery" and not _required(str(payload.get("recipient", ""))):
            raise StudioError("DELIVERY_RECIPIENT_REQUIRED")
        if action == "knowledge_registration" and payload.get("explicit") is not True:
            raise StudioError("KNOWLEDGE_REGISTRATION_CONFIRMATION_REQUIRED")
        return self._repository.record_action(context, action, payload, idempotency_key)

    def list_outputs(self, context: StudioContext):
        return self._repository.list_outputs(context)

    def list_versions(self, context: StudioContext, output_id: str):
        _identifier(output_id)
        return self._repository.list_versions(context, output_id)

    def export(self, context: StudioContext, output_id: str, version_id: str, format_name: str):
        _identifier(output_id); _identifier(version_id)
        if format_name not in {"docx", "pdf", "xlsx", "csv", "json", "svg", "png"}:
            raise StudioError("EXPORT_FORMAT_UNSUPPORTED")
        return self._repository.export_output(context, output_id, version_id, format_name)
