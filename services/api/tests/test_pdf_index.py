from __future__ import annotations

import unittest

from daon_user_api.pdf_index import PdfIndex


class PdfIndexTests(unittest.TestCase):
    def test_indexes_and_retrieves_relevant_chunks_with_source_lineage(self) -> None:
        index = PdfIndex()
        index.add_chunks("src-1", 3, [(1, "계약 기간은 12개월"), (2, "해지 통보는 30일 전")])
        results = index.search("계약 기간", source_id="src-1", source_version=3)
        self.assertEqual(results[0].page, 1)
        self.assertEqual(results[0].source_version, 3)

    def test_source_version_isolation_prevents_mixing_old_chunks(self) -> None:
        index = PdfIndex()
        index.add_chunks("src-1", 1, [(1, "구 버전 계약")])
        index.add_chunks("src-1", 2, [(1, "신 버전 계약")])
        results = index.search("계약", source_id="src-1", source_version=2)
        self.assertEqual([item.source_version for item in results], [2])

    def test_empty_query_and_unknown_source_return_empty(self) -> None:
        index = PdfIndex()
        self.assertEqual(index.search("", source_id="missing", source_version=1), [])


if __name__ == "__main__":
    unittest.main()
