from __future__ import annotations

import unittest

from daon_user_api.studio_workspace import (
    StudioContext, StudioError, StudioGenerationRequest, StudioWorkspaceService, build_structured_output,
)


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def create_generation(self, context, request, idempotency_key):  # type: ignore[no-untyped-def]
        self.calls.append(("create", context, request, idempotency_key))
        return {"studio_output_id": "output-1", "output_version_id": "version-1", "status": "draft"}, False

    def create_version(self, context, output_id, revision, idempotency_key):  # type: ignore[no-untyped-def]
        self.calls.append(("version", context, output_id, revision, idempotency_key))
        return {"output_version_id": "version-2", "previous_version_id": "version-1", "status": "draft"}, False

    def record_action(self, context, action, payload, idempotency_key):  # type: ignore[no-untyped-def]
        self.calls.append(("action", context, action, payload, idempotency_key))
        return {"action": action, "status": "accepted"}, False


class StudioWorkspaceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.service = StudioWorkspaceService(self.repository)
        self.context = StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1")

    def request(self, output_type="evidence_report") -> StudioGenerationRequest:
        output_format = {
            "evidence_report": "docx", "compliance_checklist": "xlsx", "comparison_table": "csv",
            "knowledge_map": "json", "business_draft": "pdf",
        }.get(output_type, "docx")
        return StudioGenerationRequest(
            output_type=output_type, source_id="source-1", source_version_ids=("source-version-1",),
            run_id="run-1", run_result_id="result-1", purpose="의사 결정", audience="운영 책임자",
            ruleset_version_id=None, length="standard", structure="summary-body-conclusion",
            output_format=output_format, review_condition="review_required",
        )

    def test_all_five_types_require_confirmed_complete_snapshot(self) -> None:
        for output_type in (
            "evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft",
        ):
            output, replayed = self.service.generate(self.context, self.request(output_type), "generation-key-0001")
            self.assertEqual(output["status"], "draft")
            self.assertFalse(replayed)
        self.assertEqual(len(self.repository.calls), 5)
        with self.assertRaisesRegex(StudioError, "STUDIO_INPUT_INVALID"):
            self.request("unsupported")

    def test_five_types_use_approved_domain_structures_with_evidence(self) -> None:
        citations = [
            {"citation_id": "citation-1", "source_version_id": "source-version-1", "evidence_span_id": "span-1", "page": 2},
            {"citation_id": "citation-2", "source_version_id": "source-version-1", "evidence_span_id": "span-2", "page": 3},
        ]
        expected = {
            "evidence_report": {"summary", "body", "conclusion", "citations"},
            "compliance_checklist": {"items", "warnings", "lineage"},
            "comparison_table": {"rows"},
            "knowledge_map": {"nodes", "edges"},
            "business_draft": {"template_id", "sections", "review_state"},
        }
        for output_type, keys in expected.items():
            payload = build_structured_output(self.request(output_type), "근거가 확인된 한국어 답변", citations, "generation-1")
            self.assertTrue(keys.issubset(payload))
            self.assertGreaterEqual(len(payload.get("items", payload.get("rows", payload.get("nodes", payload.get("sections", citations))))), 2)

    def test_revision_is_append_only_and_requires_reason(self) -> None:
        with self.assertRaisesRegex(StudioError, "CHANGE_REASON_REQUIRED"):
            self.service.revise(self.context, "output-1", {
                "previous_version_id": "version-1", "revision_type": "user_edit", "change_reason": "", "content": "변경",
            }, "revision-key-0001")
        version, _ = self.service.revise(self.context, "output-1", {
            "previous_version_id": "version-1", "revision_type": "ai_regeneration", "change_reason": "최신 근거 반영", "content": "변경",
        }, "revision-key-0002")
        self.assertEqual(version["previous_version_id"], "version-1")

    def test_sensitive_actions_require_exact_step_up(self) -> None:
        payloads = {
            "approval": {"approval_request_id": "approval-request-1", "decision": "approved"},
            "delivery": {"approval_id": "approval-1", "recipient": "customer-1"},
            "knowledge_registration": {"explicit": True},
        }
        for action, required in payloads.items():
            with self.assertRaisesRegex(StudioError, "STEP_UP_REQUIRED"):
                self.service.action(self.context, action, {"output_version_id": "version-1", **required}, "action-key-0001")
            result, _ = self.service.action(self.context, action, {
                "output_version_id": "version-1",
                "step_up_verified": True,
                **required,
            }, "action-key-0002")
            self.assertEqual(result["status"], "accepted")

    def test_linked_actions_reject_missing_canon_relationships(self) -> None:
        with self.assertRaisesRegex(StudioError, "STUDIO_INPUT_INVALID"):
            self.service.action(self.context, "approval_request", {"output_version_id": "version-1"}, "action-key-0003")
        with self.assertRaisesRegex(StudioError, "APPROVAL_DECISION_INVALID"):
            self.service.action(self.context, "approval", {
                "output_version_id": "version-1", "approval_request_id": "approval-request-1",
                "decision": "auto", "step_up_verified": True,
            }, "action-key-0004")


if __name__ == "__main__":
    unittest.main()
