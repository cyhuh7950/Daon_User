from __future__ import annotations

import unittest

from daon_user_api.studio_workspace import StudioError, build_structured_output
from daon_user_api.studio_workspace import StudioGenerationRequest


class StudioWorkerContractTests(unittest.TestCase):
    def request(self, output_type: str) -> StudioGenerationRequest:
        formats = {"slides": "json", "infographic": "json", "flashcards": "json", "quiz": "json", "audio": "json", "video": "json"}
        return StudioGenerationRequest(
            output_type=output_type, source_id="source-1", source_version_ids=("version-1",),
            run_id="run-1", run_result_id="result-1", purpose="결과", audience="검토자",
            ruleset_version_id=None, length="standard", structure="summary", output_format=formats[output_type],
            review_condition="review_required",
        )

    def test_structured_document_outputs_have_type_specific_collection(self) -> None:
        citations = [{"citation_id": "citation-1", "source_version_id": "version-1", "evidence_span_id": "span-1", "page": 1}]
        for output_type, key in (("slides", "slides"), ("infographic", "metrics"), ("flashcards", "cards"), ("quiz", "questions")):
            content = build_structured_output(self.request(output_type), "답변", citations, "generation-1")
            self.assertIn(key, content)
            self.assertIn("warnings", content)

    def test_media_without_provider_is_unavailable_not_fake_success(self) -> None:
        with self.assertRaisesRegex(StudioError, "STUDIO_OUTPUT_UNAVAILABLE"):
            build_structured_output(self.request("video"), "답변", [], "generation-1")
