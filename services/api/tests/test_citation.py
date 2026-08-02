from __future__ import annotations

import unittest

from daon_user_api.citation import CitationBuilder, CitationError


class CitationTests(unittest.TestCase):
    def test_sufficient_citation_status_and_lineage(self) -> None:
        builder = CitationBuilder()
        result = builder.build("src-1", 2, [("c1", 2, 1, "계약 기간"), ("c2", 2, 2, "해지")])
        self.assertEqual(result.status, "sufficient")
        self.assertEqual(result.citations[0].source_version, 2)

    def test_partial_and_insufficient_states(self) -> None:
        builder = CitationBuilder()
        self.assertEqual(builder.build("src-1", 1, [("c1", 1, 1, "한 근거")]).status, "partial")
        self.assertEqual(builder.build("src-1", 1, []).status, "insufficient")

    def test_mixed_source_versions_are_rejected(self) -> None:
        with self.assertRaisesRegex(CitationError, "SOURCE_VERSION_MISMATCH"):
            CitationBuilder().build("src-1", 2, [("c1", 1, 1, "오래된 근거")])


if __name__ == "__main__":
    unittest.main()
