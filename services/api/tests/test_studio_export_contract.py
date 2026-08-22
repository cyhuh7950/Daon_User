from __future__ import annotations

import hashlib
import unittest

from daon_user_api.studio_export import export_studio_output


class StudioExportContractTests(unittest.TestCase):
    def test_type_specific_exports_preserve_output_type_and_hash(self) -> None:
        metadata = {"output_version_id": "version-1", "created_at": "2026-08-23T00:00:00Z", "citations": "citation-1"}
        for output_type, content, format_name in (
            ("slides", {"slides": [{"title": "요약", "body": "내용"}]}, "json"),
            ("infographic", {"metrics": [{"label": "근거", "value": "citation-1"}]}, "svg"),
            ("flashcards", {"cards": [{"question": "질문", "answer": "답"}]}, "json"),
            ("quiz", {"questions": [{"question": "문제", "options": ["정답"]}]}, "json"),
        ):
            result = export_studio_output(format_name, "결과", content, metadata, output_type=output_type)
            self.assertGreater(len(result.content), 20)
            self.assertEqual(result.checksum_sha256, hashlib.sha256(result.content).hexdigest())
            if format_name == "json":
                self.assertIn(output_type.encode(), result.content)
