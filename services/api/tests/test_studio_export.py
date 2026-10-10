from __future__ import annotations

import json
import struct
import unittest
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from daon_user_api.studio_export import StudioExportError, export_studio_output
from pypdf import PdfReader
from PIL import Image

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
                extracted = "\n".join(page.extract_text() for page in PdfReader(BytesIO(exported.content)).pages)
                self.assertIn("version-1", extracted)
                self.assertIn("근거 부록", extracted)
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

    def test_pdf_wraps_and_paginates_long_korean_content(self) -> None:
        content = "긴 한국어 문장과 Citation page 2를 포함한 합성 본문입니다. " * 1200
        result = export_studio_output("pdf", "긴 산출물", content, METADATA, output_type="business_draft")
        pages = PdfReader(BytesIO(result.content)).pages
        self.assertGreater(len(pages), 1)
        self.assertIn("긴 산출물", pages[0].extract_text())
        self.assertIn("근거 부록", pages[-1].extract_text())
        font_refs = pages[0]["/Resources"]["/Font"].values()
        self.assertTrue(any(font.get_object().get("/FontDescriptor") and "/FontFile2" in font.get_object()["/FontDescriptor"].get_object() for font in font_refs))
        self.assertNotIn("Cit\nation", pages[0].extract_text())

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

    def test_xlsx_metadata_labels_and_values_have_readable_column_widths(self) -> None:
        exported = export_studio_output(
            "xlsx", "비교표", {"rows": [{"key": "항목 A", "baseline": "기준 A"}]},
            METADATA, output_type="comparison_table",
        )
        with zipfile.ZipFile(BytesIO(exported.content)) as archive:
            root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        columns = root.findall("x:cols/x:col", ns)
        widths = {index: float(column.attrib["width"])
                  for column in columns
                  for index in range(int(column.attrib["min"]), int(column.attrib["max"]) + 1)}
        self.assertGreaterEqual(widths[1], 22)
        self.assertGreaterEqual(widths[2], 22)
        cells = {cell.attrib["r"]: cell.findtext("x:is/x:t", namespaces=ns)
                 for cell in root.findall("x:sheetData/x:row/x:c", ns)}
        self.assertEqual(cells["A6"], "output_version_id")
        self.assertEqual(cells["B6"], "version-1")
        self.assertEqual(cells["A9"], "evidence_appendix")
        self.assertEqual(cells["B9"], "Citation page 2")

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

    def test_knowledge_map_svg_visibly_shows_condition_evidence_and_confidence(self) -> None:
        content = {
            "nodes": [
                {"id": "n1", "label": "합성 근거", "evidence": "인용 A", "confidence": "verified"},
                {"id": "n2", "label": "합성 결론", "evidence": "인용 B & 보강", "confidence": "uncertain"},
            ],
            "edges": [{"id": "e1", "source": "n1", "target": "n2", "condition": "검증 & 확인"}],
        }
        svg = export_studio_output("svg", "지식 구조도", content, METADATA, output_type="knowledge_map")
        root = ElementTree.fromstring(svg.content)
        texts = [element.text for element in root.findall("{http://www.w3.org/2000/svg}text")]
        for expected in ("합성 근거", "합성 결론", "조건: 검증 & 확인", "근거: 인용 A",
                         "근거: 인용 B & 보강", "신뢰: verified", "신뢰: uncertain"):
            with self.subTest(expected=expected):
                self.assertIn(expected, texts)

    def test_knowledge_map_svg_footer_shows_each_metadata_value_on_its_own_line(self) -> None:
        svg = export_studio_output("svg", "지식 구조도", {"nodes": [], "edges": []}, METADATA,
                                   output_type="knowledge_map")
        root = ElementTree.fromstring(svg.content)
        texts = root.findall("{http://www.w3.org/2000/svg}text")
        for key, value in METADATA.items():
            with self.subTest(key=key):
                matches = [element for element in texts if element.text == f"{key}: {value}"]
                self.assertEqual(len(matches), 1)
                self.assertLessEqual(int(matches[0].attrib["y"]), 600)

    def test_knowledge_map_png_shows_korean_node_labels_and_edge_condition(self) -> None:
        content = {
            "nodes": [{"id": "n1", "label": "합성 근거"}, {"id": "n2", "label": "합성 결론"}],
            "edges": [{"id": "e1", "source": "n1", "target": "n2", "condition": "검증"}],
        }
        png = export_studio_output("png", "지식 구조도", content, METADATA, output_type="knowledge_map")
        image = Image.open(BytesIO(png.content)).convert("RGB")
        for box in ((30, 346, 130, 372), (510, 186, 610, 212), (285, 200, 355, 220)):
            with self.subTest(box=box):
                crop = image.crop(box)
                self.assertGreater(sum(pixel != (255, 255, 255) for pixel in crop.get_flattened_data()), 10)


if __name__ == "__main__":
    unittest.main()
