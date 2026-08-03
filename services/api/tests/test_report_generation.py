from __future__ import annotations

import unittest

from daon_user_api.report_generation import ReportGenerator, ReportGenerationError


class ReportGenerationTests(unittest.TestCase):
    def test_docx_and_pdf_report_preserve_citations_and_lineage(self) -> None:
        generator = ReportGenerator()
        for format in ("docx", "pdf"):
            result = generator.generate(format, "요약", "본문", "결론", [("src-1", 2, 1)], "req-1", "model-1")
            self.assertEqual(result.status, "generated")
            self.assertEqual(result.lineage["request_id"], "req-1")

    def test_unverified_conclusion_is_flagged(self) -> None:
        result = ReportGenerator().generate("pdf", "요약", "본문", "미확인 결론", [], "req-1", "model-1")
        self.assertEqual(result.status, "unverified")
        self.assertIn("missing_evidence", result.warnings)

    def test_unsupported_format_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReportGenerationError, "FORMAT_UNSUPPORTED"):
            ReportGenerator().generate("xlsx", "요약", "본문", "결론", [], "req-1", "model-1")


if __name__ == "__main__":
    unittest.main()
