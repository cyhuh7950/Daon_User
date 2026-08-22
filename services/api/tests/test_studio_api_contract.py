from __future__ import annotations

import unittest

from daon_user_api.studio_workspace import FORMATS, OUTPUT_TYPES, StudioError, StudioGenerationRequest


class StudioApiContractTests(unittest.TestCase):
    def test_matrix_contains_all_workspace_studio_types(self) -> None:
        expected = {
            "evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft",
            "slides", "infographic", "flashcards", "quiz", "audio", "video",
        }
        self.assertEqual(set(OUTPUT_TYPES), expected)
        self.assertEqual(set(FORMATS), expected)

    def test_request_contract_accepts_new_document_types(self) -> None:
        for output_type in ("slides", "infographic", "flashcards", "quiz"):
            request = StudioGenerationRequest(
                output_type=output_type, source_id="source-1", source_version_ids=("version-1",),
                run_id="run-1", run_result_id="result-1", purpose="결과", audience="검토자",
                ruleset_version_id=None, length="standard", structure="summary", output_format=next(iter(FORMATS[output_type])),
                review_condition="review_required",
            )
            self.assertEqual(request.output_type, output_type)

    def test_media_contract_is_registered_but_fails_closed_in_provider_layer(self) -> None:
        request = StudioGenerationRequest(
            output_type="audio", source_id="source-1", source_version_ids=("version-1",),
            run_id="run-1", run_result_id="result-1", purpose="오디오", audience="검토자",
            ruleset_version_id=None, length="standard", structure="summary", output_format="json",
            review_condition="review_required",
        )
        self.assertEqual(request.output_type, "audio")

    def test_invalid_type_is_rejected_explicitly(self) -> None:
        with self.assertRaisesRegex(StudioError, "STUDIO_INPUT_INVALID"):
            StudioGenerationRequest(
                output_type="not_registered", source_id="source-1", source_version_ids=("version-1",),
                run_id="run-1", run_result_id="result-1", purpose="결과", audience="검토자",
                ruleset_version_id=None, length="standard", structure="summary", output_format="json",
                review_condition="review_required",
            )
