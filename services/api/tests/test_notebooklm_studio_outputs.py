from __future__ import annotations

import unittest

from daon_user_api.studio_export import export_studio_output
from daon_user_api.studio_workspace import (
    OUTPUT_TYPES,
    StudioError,
    StudioGenerationRequest,
    build_structured_output,
)


class NotebookLmStudioOutputsTests(unittest.TestCase):
    def request(self, output_type: str) -> StudioGenerationRequest:
        formats = {
            "evidence_report": "docx", "compliance_checklist": "xlsx", "comparison_table": "csv",
            "knowledge_map": "json", "business_draft": "pdf", "slides": "json",
            "infographic": "svg", "flashcards": "json", "quiz": "json", "audio": "json", "video": "json",
        }
        return StudioGenerationRequest(
            output_type=output_type, source_id="source-1", source_version_ids=("source-version-1",),
            run_id="run-1", run_result_id="result-1", purpose="작업 결과", audience="운영 담당자",
            ruleset_version_id=None, length="standard", structure="summary-body", output_format=formats[output_type],
            review_condition="review_required",
        )

    def test_all_eleven_output_contracts_are_registered(self) -> None:
        self.assertEqual(len(OUTPUT_TYPES), 11)
        self.assertIn("slides", OUTPUT_TYPES)
        self.assertIn("video", OUTPUT_TYPES)

    def test_supported_new_outputs_preserve_grounding_shape(self) -> None:
        citations = [{
            "citation_id": "citation-1", "source_version_id": "source-version-1",
            "evidence_span_id": "span-1", "page": 2,
        }]
        expected = {
            "slides": "slides", "infographic": "metrics", "flashcards": "cards", "quiz": "questions",
        }
        for output_type, collection in expected.items():
            payload = build_structured_output(self.request(output_type), "확인된 답변", citations, "generation-1")
            self.assertIn(collection, payload)
            self.assertGreaterEqual(len(payload[collection]), 1)

    def test_audio_and_video_fail_closed_without_provider(self) -> None:
        for output_type in ("audio", "video"):
            with self.assertRaisesRegex(StudioError, "STUDIO_OUTPUT_UNAVAILABLE"):
                build_structured_output(self.request(output_type), "확인된 답변", [], "generation-1")

    def test_new_structures_are_exportable_without_fake_media(self) -> None:
        for output_type, content in (
            ("slides", {"slides": [{"title": "요약", "body": "내용"}]}),
            ("infographic", {"metrics": [{"label": "근거", "value": "citation-1"}]}),
            ("flashcards", {"cards": [{"question": "질문", "answer": "답"}]}),
            ("quiz", {"questions": [{"question": "문제", "options": ["정답"]}]}),
        ):
            result = export_studio_output("json", "작업 결과", content, {"output_version_id": "version-1"}, output_type=output_type)
            self.assertEqual(result.media_type, "application/json")
            self.assertIn(output_type.encode(), result.content)


if __name__ == "__main__":
    unittest.main()
