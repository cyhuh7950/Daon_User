from __future__ import annotations

import json
import unittest
import zipfile
import struct
from io import BytesIO

from daon_user_api.studio_export import StudioExportError, export_studio_output


METADATA = {
    "output_version_id": "version-1", "created_at": "2026-08-13T00:00:00Z",
    "knowledge_scope": "source-version-1", "evidence_appendix": "Citation page 2",
}


class StudioExportTests(unittest.TestCase):
    def test_all_approved_formats_return_real_bytes_and_safe_headers(self) -> None:
        for format_name in ("docx", "pdf", "xlsx", "csv", "json", "svg", "png"):
            exported = export_studio_output(format_name, "승인 산출물", "근거 내용", METADATA)
            self.assertGreater(len(exported.content), 20)
            self.assertEqual(exported.checksum_sha256, __import__("hashlib").sha256(exported.content).hexdigest())
            self.assertNotIn("/", exported.filename)
            self.assertNotIn("\\", exported.filename)
            self.assertTrue(exported.media_type)
            if format_name in {"docx", "xlsx"}:
                with zipfile.ZipFile(BytesIO(exported.content)) as archive:
                    self.assertTrue(archive.testzip() is None)
                    joined = b"".join(archive.read(name) for name in archive.namelist())
                    self.assertIn(b"version-1", joined)
            elif format_name == "pdf":
                self.assertTrue(exported.content.startswith(b"%PDF-"))
                self.assertIn("version-1".encode("utf-16-be").hex().upper().encode(), exported.content)
                self.assertIn("근거 부록".encode("utf-16-be").hex().upper().encode(), exported.content)
            elif format_name == "png":
                self.assertTrue(exported.content.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertIn(b"version-1", exported.content)
            elif format_name == "json":
                self.assertEqual(json.loads(exported.content)["metadata"]["output_version_id"], "version-1")
            else:
                self.assertIn(b"version-1", exported.content)

    def test_invalid_format_and_oversized_content_fail_closed(self) -> None:
        with self.assertRaisesRegex(StudioExportError, "EXPORT_FORMAT_UNSUPPORTED"):
            export_studio_output("html", "제목", "내용", METADATA)
        with self.assertRaisesRegex(StudioExportError, "EXPORT_TOO_LARGE"):
            export_studio_output("csv", "제목", "x" * (8 * 1024 * 1024 + 1), METADATA)

    def test_tabular_and_graph_exports_preserve_real_structure_without_truncation(self) -> None:
        structured = {"rows": [
            {"key": "항목 A", "baseline": "기준 A", "current": "현재 A", "state": "same", "evidence": ["인용 1", "인용 2"]},
            {"key": "항목 B", "baseline": "기준 B", "current": "현재 B", "state": "changed", "evidence": ["인용 3", "인용 4"]},
        ]}
        xlsx = export_studio_output("xlsx", "비교표", structured, METADATA, output_type="comparison_table")
        with zipfile.ZipFile(BytesIO(xlsx.content)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn('r="E3"', sheet)
            self.assertIn("항목 B", sheet)
            self.assertIn("인용 4", sheet)
        graph = export_studio_output("json", "지식 구조도", {
            "nodes": [{"id": "n1", "label": "근거", "evidence": "인용 1"}, {"id": "n2", "label": "결론", "evidence": "인용 2"}],
            "edges": [{"id": "e1", "source": "n1", "target": "n2", "condition": "확인"}],
        }, METADATA, output_type="knowledge_map")
        self.assertEqual(len(json.loads(graph.content)["content"]["nodes"]), 2)

    def test_graph_svg_and_png_render_actual_nodes_and_edges(self) -> None:
        content = {
            "nodes": [{"id": "n1", "label": "근거"}, {"id": "n2", "label": "결론"}],
            "edges": [{"id": "e1", "source": "n1", "target": "n2", "condition": "확인"}],
        }
        svg = export_studio_output("svg", "지식 구조도", content, METADATA, output_type="knowledge_map")
        self.assertGreaterEqual(svg.content.count(b"<circle"), 2)
        self.assertGreaterEqual(svg.content.count(b"<line"), 1)
        png = export_studio_output("png", "지식 구조도", content, METADATA, output_type="knowledge_map")
        width, height = struct.unpack(">II", png.content[16:24])
        self.assertGreaterEqual(width, 320)
        self.assertGreaterEqual(height, 240)
        self.assertGreater(len(png.content), 1000)


if __name__ == "__main__":
    unittest.main()
