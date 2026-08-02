from __future__ import annotations

import unittest

from daon_user_api.format_understanding import FormatUnderstanding, FormatRejected


class FormatUnderstandingTests(unittest.TestCase):
    def test_docx_and_csv_ready_with_vision_first_lineage(self) -> None:
        engine = FormatUnderstanding()
        for kind, location in (("docx", "page:2"), ("csv", "cell:B4")):
            result = engine.process(kind, "meaning", "parser-check", location)
            self.assertEqual(result.status, "ready")
            self.assertEqual(result.lineage["parser_role"], "validation_only")
            self.assertEqual(result.evidence_location, location)

    def test_image_region_evidence_and_parser_only_block(self) -> None:
        result = FormatUnderstanding().process("png", "diagram meaning", "ocr text", "region:x10-y20")
        self.assertEqual(result.status, "ready")
        with self.assertRaisesRegex(FormatRejected, "PARSER_ONLY_NOT_READY"):
            FormatUnderstanding().process("xlsx", None, "cell text", "cell:A1")

    def test_unsupported_format_is_rejected(self) -> None:
        with self.assertRaisesRegex(FormatRejected, "UNSUPPORTED_FORMAT"):
            FormatUnderstanding().process("exe", "meaning", "parser", "page:1")


if __name__ == "__main__":
    unittest.main()
