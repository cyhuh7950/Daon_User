from __future__ import annotations

import unittest

from daon_user_api.pdf_understanding import PdfUnderstandingPipeline, UnderstandingRejected


class PdfUnderstandingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = PdfUnderstandingPipeline()

    def test_vision_understanding_then_parser_validation_reaches_ready(self) -> None:
        result = self.pipeline.process(
            source_id="src-pdf-1",
            model_output={"summary": "계약 기간은 12개월", "chunks": ["계약 기간"]},
            parser_output={"page": 1, "text": "계약 기간은 12개월"},
            model_id="vision-local-1",
            prompt_version="p1",
            policy_version="pol1",
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.substates, ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"))
        self.assertEqual(result.evidence_pages, (1,))

    def test_parser_only_never_becomes_ready(self) -> None:
        with self.assertRaisesRegex(UnderstandingRejected, "PARSER_ONLY_NOT_READY"):
            self.pipeline.process(
                source_id="src-pdf-1",
                model_output=None,
                parser_output={"page": 1, "text": "parser text"},
                model_id=None,
                prompt_version=None,
                policy_version=None,
            )

    def test_conflict_is_review_and_lineage_is_preserved(self) -> None:
        result = self.pipeline.process(
            source_id="src-pdf-2",
            model_output={"summary": "12개월", "chunks": ["12개월"]},
            parser_output={"page": 2, "text": "24개월"},
            model_id="vision-cloud-1",
            prompt_version="p2",
            policy_version="pol2",
        )
        self.assertEqual(result.status, "review")
        self.assertEqual(result.conflict, "UNDERSTANDING_PARSER_CONFLICT")
        self.assertEqual(result.lineage["model_id"], "vision-cloud-1")


if __name__ == "__main__":
    unittest.main()
