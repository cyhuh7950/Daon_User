from __future__ import annotations

import json
import csv
from io import StringIO
from unittest.mock import patch
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

    def test_e_table_pdf_has_real_columns_repeated_headers_and_full_korean_rows(self):
        content={"rows":[{"항목":f"항목 {i}","확인 내용":"긴 한국어 확인 내용 "*12+f"끝표식{i}"} for i in range(55)]}
        pdf=PdfReader(BytesIO(export_studio_output("pdf","실제 비교표",content,METADATA,output_type="comparison_table").content))
        self.assertGreater(len(pdf.pages),1)
        all_text="".join(page.extract_text() for page in pdf.pages).replace(" ","").replace("\n","")
        for i in range(55):self.assertIn(f"끝표식{i}",all_text)
        first=pdf.pages[0];coords=[]
        first.extract_text(visitor_text=lambda text,cm,tm,font,size:coords.append((text,cm[4]+tm[4])))
        left=[x for text,x in coords if "항목 0" in text];right=[x for text,x in coords if "긴 한국어" in text]
        self.assertTrue(left and right)
        self.assertGreater(min(right)-min(left),100,"행 구분자 텍스트가 아니라 실제 다른 열 위치여야 한다")
        for page in pdf.pages[:-1]:self.assertIn("확인 내용",page.extract_text())

    def test_e_slides_pdf_starts_each_slide_on_a_page_and_preserves_overflow(self):
        content={"slides":[{"title":"첫 장","body":"첫 내용"},{"title":"둘째 장","body":"한글 긴 설명 "*2500+"마지막장본문끝"},{"title":"셋째 장","body":"셋째 내용"}]}
        pdf=PdfReader(BytesIO(export_studio_output("pdf","발표",content,METADATA,output_type="slides").content))
        texts=[page.extract_text() for page in pdf.pages]
        self.assertGreaterEqual(len(texts),4)
        self.assertIn("첫 내용",texts[0]);self.assertNotIn("둘째 장",texts[0])
        self.assertIn("마지막장본문끝","".join(texts).replace("\n",""))
        last=next(text for text in texts if "셋째 장" in text)
        self.assertIn("셋째 내용",last);self.assertNotIn("마지막장본문끝",last)
        self.assertIn("version-1",texts[-1])

    def test_e_infographic_all_formats_show_actual_metric_cards_and_escaped_information(self):
        content={"metrics":[{"label":"확인 수치","value":"37","unit":"건","description":"실제 집계"},{"label":"확인 상태 <script>","value":"미확인 & 검토 필요"}]}
        svg=export_studio_output("svg","정보 카드",content,METADATA,output_type="infographic")
        root=ElementTree.fromstring(svg.content);ns={"s":"http://www.w3.org/2000/svg"}
        self.assertGreaterEqual(len(root.findall("s:rect",ns)),2)
        texts=" ".join(t.text or "" for t in root.findall("s:text",ns))
        for value in ("확인 수치","37","건","실제 집계","확인 상태 <script>","미확인 & 검토 필요"):self.assertIn(value,texts)
        self.assertEqual(root.findall("s:script",ns),[])
        png=Image.open(BytesIO(export_studio_output("png","정보 카드",content,METADATA,output_type="infographic").content)).convert("RGB")
        self.assertGreater(sum(p!=(255,255,255) for p in png.crop((40,80,760,160)).get_flattened_data()),100)
        pdf=PdfReader(BytesIO(export_studio_output("pdf","정보 카드",content,METADATA,output_type="infographic").content))
        text="".join(page.extract_text() for page in pdf.pages)
        for value in ("확인 수치","37","건","미확인 & 검토 필요"):self.assertIn(value,text)

    def test_e_empty_infographic_reports_missing_information_without_fake_chart(self):
        for format_name in ("svg","pdf","png"):
            with self.subTest(format=format_name):
                result=export_studio_output(format_name,"빈 카드",{"metrics":[]},METADATA,output_type="infographic")
                if format_name=="svg":
                    self.assertIn("표시할 지표나 정보가 없습니다",result.content.decode())
                    self.assertNotIn(b"<circle",result.content)
                elif format_name=="pdf":self.assertIn("표시할 지표나 정보가 없습니다",PdfReader(BytesIO(result.content)).pages[0].extract_text())
                else:self.assertIn("표시할 지표나 정보가 없습니다",Image.open(BytesIO(result.content)).info["Studio"])

    def test_e_csv_formula_cells_are_literal_and_original_text_is_preserved(self):
        dangerous=["=1+1","+SUM(A1:A2)","-2+3","@cmd"," \t=HYPERLINK(\"https://invalid\")","\r=1","\t=2"]
        content={"rows":[{"값":value,"설명":"일반 한글"} for value in dangerous]}
        result=export_studio_output("csv","=제목",content,{**METADATA,"evidence_appendix":"+위험"},output_type="comparison_table")
        rows=list(csv.reader(StringIO(result.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0],["제목","'=제목"])
        for original,row in zip(dangerous,rows[2:]):self.assertEqual(row,["'"+original,"일반 한글"])
        self.assertEqual(rows[-1],["evidence_appendix","'+위험"])

    def test_e_checklist_pdf_splits_one_long_row_without_losing_text(self):
        content={"items":[{"항목":"검토 대상","판정":"미확인","필요 조치":"조건과 근거를 함께 확인해야 합니다. "*350+"긴행마지막표식"}]}
        pdf=PdfReader(BytesIO(export_studio_output("pdf","검토표",content,METADATA,output_type="compliance_checklist").content))
        self.assertGreater(len(pdf.pages),1)
        # 반복 header가 페이지 중간에서 분할된 셀 문장을 끊으므로 표 header만 제외해 원문을 비교한다.
        text="".join(page.extract_text().replace("항목\n판정\n필요 조치\n","") for page in pdf.pages).replace("\n","").replace(" ","")
        self.assertEqual(text.count("조건과근거를함께확인해야합니다."),350)
        self.assertIn("긴행마지막표식",text)
        self.assertIn("version-1",text)

    def test_e_long_information_card_wraps_preserves_zero_and_bounds_image_allocation(self):
        content={"metrics":[{"label":"긴 카드","value":"한글 정보 "*100+"카드끝표식"},{"label":"확인된 수","value":0}]}
        svg=ElementTree.fromstring(export_studio_output("svg","한글 카드",content,METADATA,output_type="infographic").content)
        text="".join(t.text or "" for t in svg.findall("{http://www.w3.org/2000/svg}text"))
        self.assertIn("카드끝표식",text);self.assertIn("값: 0",text)
        self.assertGreater(int(svg.attrib["height"]),400)
        for kind in ("png","svg"):
            for size in (10000,100000):
                # 첫 크기는 변 길이 한도 이내에서도 픽셀 상한을 넘는다. 두 번째는 치수도 초과한다.
                with self.subTest(format=kind,size=size), patch("PIL.Image.new",side_effect=AssertionError("oversized allocation forbidden")):
                    with self.assertRaisesRegex(StudioExportError,"^EXPORT_TOO_LARGE$"):
                        export_studio_output(kind,"카드",{"metrics":[{"label":"긴 정보","value":"한글"*size}]},METADATA,output_type="infographic")

    def test_e_legacy_scalar_zero_false_and_empty_string_metrics_are_not_dropped(self):
        content={"metrics":[0,False,""]}
        svg=ElementTree.fromstring(export_studio_output("svg","이전 정보",content,METADATA,output_type="infographic").content)
        ns={"s":"http://www.w3.org/2000/svg"}
        self.assertEqual(len(svg.findall("s:rect",ns)),3)
        text=" ".join(t.text or "" for t in svg.findall("s:text",ns))
        self.assertIn("0",text);self.assertIn("False",text);self.assertIn("빈 문자열",text)
        self.assertNotIn("표시할 지표나 정보가 없습니다",text)

    def test_e_actual_infographic_shape_preserves_sibling_summary_in_visible_formats(self):
        from PIL import ImageDraw
        from copy import deepcopy
        content={"title":"저장한 카드 제목","summary":"실제 저장 요약 고유끝표식","metrics":[{"label":"근거 1","value":"인용 1","evidence":"page 2"}],"warnings":["추가 확인 필요"],"사용자 항목":"사용자 값 <원문>"}
        original=deepcopy(content)
        svg=ElementTree.fromstring(export_studio_output("svg","출력 제목",content,METADATA,output_type="infographic").content)
        text=" ".join(t.text or "" for t in svg.findall("{http://www.w3.org/2000/svg}text"))
        for value in ("저장한 카드 제목","실제 저장 요약 고유끝표식","추가 확인 필요","사용자 값 <원문>"):self.assertIn(value,text)
        pdf=PdfReader(BytesIO(export_studio_output("pdf","출력 제목",content,METADATA,output_type="infographic").content))
        self.assertIn("실제 저장 요약 고유끝표식","".join(page.extract_text() for page in pdf.pages))
        rendered=[];draw_text=ImageDraw.ImageDraw.text
        def record_text(draw,xy,text,*args,**kwargs):
            rendered.append(str(text));return draw_text(draw,xy,text,*args,**kwargs)
        with patch.object(ImageDraw.ImageDraw,"text",record_text):
            image=Image.open(BytesIO(export_studio_output("png","출력 제목",content,METADATA,output_type="infographic").content))
        self.assertIn("실제 저장 요약 고유끝표식"," ".join(rendered),"PNG metadata가 아니라 실제 draw.text 본문이어야 한다")
        self.assertGreater(image.height,200);self.assertEqual(content,original)

    def test_e_typed_visual_labels_are_korean_but_original_json_csv_xlsx_keys_are_unchanged(self):
        content={"rows":[{"key":"대상","baseline":"기준값","current":"현재값","state":"미확인","evidence":"인용","사용자 열 <원문>":"안전값"}]}
        pdf=PdfReader(BytesIO(export_studio_output("pdf","표",content,METADATA,output_type="comparison_table").content))
        text="".join(page.extract_text() for page in pdf.pages)
        for label in ("항목","기준","현재","상태","근거","산출물 버전","사용자 열 <원문>"):self.assertIn(label,text)
        for raw in ("baseline","current","state","output_version_id"):self.assertNotIn(raw,text)
        cards={"metrics":[{"label":"수치","value":0}]}
        svg=ElementTree.fromstring(export_studio_output("svg","카드",cards,METADATA,output_type="infographic").content)
        text=" ".join(t.text or "" for t in svg.findall("{http://www.w3.org/2000/svg}text"))
        self.assertIn("항목: 수치",text);self.assertIn("값: 0",text);self.assertNotIn("label:",text);self.assertNotIn("value:",text)
        slides=PdfReader(BytesIO(export_studio_output("pdf","발표",{"slides":[{"title":"장 제목","body":"장 본문"}]},METADATA,output_type="slides").content))
        self.assertIn("본문: 장 본문",slides.pages[0].extract_text());self.assertNotIn("body:",slides.pages[0].extract_text())
        self.assertEqual(json.loads(export_studio_output("json","카드",cards,METADATA,output_type="infographic").content)["content"],cards)
        csv_rows=list(csv.reader(StringIO(export_studio_output("csv","표",content,METADATA,output_type="comparison_table").content.decode("utf-8-sig"))))
        self.assertEqual(csv_rows[1],list(content["rows"][0]))
        with zipfile.ZipFile(BytesIO(export_studio_output("xlsx","표",content,METADATA,output_type="comparison_table").content)) as archive:
            self.assertIn(b"baseline",archive.read("xl/worksheets/sheet1.xml"))

    def test_e_infographic_svg_rejects_invalid_xml_without_silently_deleting_text(self):
        valid={"metrics":[{"label":"한글 😀","value":"줄1\t줄2\n줄3\r줄4"}]}
        ElementTree.fromstring(export_studio_output("svg","유니코드 제목",valid,METADATA,output_type="infographic").content)
        for invalid in ("\u0001","\ud800"):
            for location in ("title","body","metadata"):
                with self.subTest(location=location,character=ord(invalid)):
                    title="제목"+invalid if location=="title" else "제목"
                    content={"metrics":[{"label":"항목","value":"값"+invalid}]} if location=="body" else valid
                    metadata={**METADATA,"추가 정보":"값"+invalid} if location=="metadata" else METADATA
                    with self.assertRaisesRegex(StudioExportError,"^STUDIO_OUTPUT_UNAVAILABLE$"):
                        export_studio_output("svg",title,content,metadata,output_type="infographic")
        for fmt in ("png","pdf"):
            self.assertTrue(export_studio_output(fmt,"한글 제목",valid,METADATA,output_type="infographic").content)

    def test_e_slide_sibling_warnings_and_unknown_user_fields_are_visible(self):
        content={"title":"저장한 발표 제목","slides":[{"title":"첫 장","body":"첫 내용"}],"warnings":["발표주의끝표식"],"사용자 메모":"추가 정보 <원문>"}
        pdf=PdfReader(BytesIO(export_studio_output("pdf","다운로드 제목",content,METADATA,output_type="slides").content))
        text="".join(page.extract_text() for page in pdf.pages)
        for value in ("저장한 발표 제목","발표주의끝표식","사용자 메모","추가 정보 <원문>"):self.assertIn(value,text)

    def test_e_actual_compliance_and_comparison_siblings_preserve_warnings_and_lineage(self):
        from dataclasses import asdict,replace
        from daon_user_api.compliance_check import ComplianceChecker
        from daon_user_api.comparison_table import ComparisonTable
        result=ComplianceChecker().check([{"item_id":"check-qa","judgement":"compliant","evidence":"","action":"추가 검토"}],"ruleset-qa","ruleset-v2","request-qa","model-qa")
        content=asdict(replace(result,warnings=result.warnings+("경고고유끝표식",)))
        pdf=PdfReader(BytesIO(export_studio_output("pdf","준수 점검",content,METADATA,output_type="compliance_checklist").content))
        text="".join(page.extract_text() for page in pdf.pages)
        for value in ("경고고유끝표식","missing_evidence","ruleset-qa","ruleset-v2","request-qa","model-qa","규칙집 ID","생성 계보","요청 ID"):
            self.assertIn(value,text)
        for key in ("ruleset_id","ruleset_version","request_id","lineage"):
            self.assertNotIn(key,text)
        comparison=asdict(ComparisonTable().compare({"기준":"값1"},{"기준":"값2"},"baseline-v1","current-v2",{"기준":("인용1","인용2")}))
        comparison["사용자 요약"]="비교근거끝표식"
        pdf=PdfReader(BytesIO(export_studio_output("pdf","비교",comparison,METADATA,output_type="comparison_table").content))
        text="".join(page.extract_text() for page in pdf.pages)
        for value in ("비교근거끝표식","사용자 요약","기준 버전","현재 버전","차이"):self.assertIn(value,text)

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
