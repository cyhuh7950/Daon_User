from __future__ import annotations

import binascii
import csv
import hashlib
import json
import struct
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO, StringIO
from xml.sax.saxutils import escape

MAX_EXPORT_BYTES = 8 * 1024 * 1024
MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8", "json": "application/json", "svg": "image/svg+xml", "png": "image/png",
}
OUTPUT_FORMATS = {
    "evidence_report": frozenset({"docx", "pdf"}),
    "compliance_checklist": frozenset({"xlsx", "csv", "pdf"}),
    "comparison_table": frozenset({"xlsx", "csv", "pdf"}),
    "knowledge_map": frozenset({"json", "svg", "png", "pdf"}),
    "business_draft": frozenset({"docx", "pdf"}),
    "slides": frozenset({"pdf", "json"}),
    "infographic": frozenset({"svg", "png", "pdf", "json"}),
    "flashcards": frozenset({"json", "csv", "pdf"}),
    "quiz": frozenset({"json", "csv", "pdf"}),
    "audio": frozenset({"json"}),
    "video": frozenset({"json"}),
}


class StudioExportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StudioExport:
    content: bytes
    media_type: str
    filename: str
    checksum_sha256: str


def _structured_rows(content: object) -> list[list[str]]:
    if not isinstance(content, Mapping):
        return [["내용", str(content)]]
    records = content.get("items") or content.get("rows") or content.get("sections")
    records = records or content.get("slides") or content.get("metrics") or content.get("cards") or content.get("questions")
    if isinstance(records, (list, tuple)) and records:
        headers = list(dict.fromkeys(key for item in records if isinstance(item, Mapping) for key in item))
        return [headers, *[[json.dumps(item.get(key), ensure_ascii=False) if isinstance(item.get(key), (list, tuple, dict)) else str(item.get(key, "")) for key in headers] for item in records]]
    if isinstance(content.get("nodes"), (list, tuple)):
        return [["구분", "id", "source", "target", "label", "condition", "evidence"], *[
            ["Node", str(item.get("id", "")), "", "", str(item.get("label", "")), "", str(item.get("evidence", ""))]
            for item in content["nodes"]
        ], *[
            ["Edge", str(item.get("id", "")), str(item.get("source", "")), str(item.get("target", "")), "", str(item.get("condition", "")), ""]
            for item in content.get("edges", ())
        ]]
    return [[str(key), json.dumps(value, ensure_ascii=False) if isinstance(value, (list, tuple, dict)) else str(value)] for key, value in content.items()]


def _content_text(content: object) -> str:
    return content if isinstance(content, str) else "\n".join(" | ".join(row) for row in _structured_rows(content))


def _payload(title: str, content: object, metadata: dict[str, str]) -> str:
    appendix = "\n".join(f"{key}: {value}" for key, value in metadata.items())
    return f"{title}\n\n{_content_text(content)}\n\n근거 부록\n{appendix}"


def _zip(entries: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def _docx(text: str) -> bytes:
    paragraphs = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in text.splitlines())
    return _zip({
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{paragraphs}</w:body></w:document>',
    })


def _column(index: int) -> str:
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _xlsx(rows_data: list[list[str]]) -> bytes:
    rows = "".join(f'<row r="{row_index}">' + "".join(
        f'<c r="{_column(column_index)}{row_index}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
        for column_index, value in enumerate(row, 1)
    ) + "</row>" for row_index, row in enumerate(rows_data, 1))
    return _zip({
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Studio" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols><col min="1" max="2" width="24" customWidth="1"/></cols><sheetData>{rows}</sheetData></worksheet>',
    })


def _pdf(text: str) -> bytes:
    from pathlib import Path

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas

    font_name = "DaonNanumGothic"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(__file__).parent / "assets" / "NanumGothic-Regular.ttf"
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))

    output = BytesIO()
    page = Canvas(output, pagesize=A4, pageCompression=1, invariant=1)
    page.setFont(font_name, 9)
    page_width, page_height = A4
    margin = 40
    line_height = 14
    y = page_height - margin

    def emit(line: str) -> None:
        nonlocal y
        if y < margin + line_height:
            page.showPage()
            page.setFont(font_name, 9)
            y = page_height - margin
        page.drawString(margin, y, line)
        y -= line_height

    for paragraph in text.split("\n"):
        words = paragraph.expandtabs(4).split(" ")
        line = ""
        line_width = 0.0
        for index, word in enumerate(words):
            token = word + (" " if index < len(words) - 1 else "")
            token_width = pdfmetrics.stringWidth(token, font_name, 9)
            if line and line_width + token_width > page_width - 2 * margin:
                emit(line.rstrip())
                line = ""
                line_width = 0.0
            if not line:
                token = token.lstrip()
            for character in token:
                character_width = pdfmetrics.stringWidth(character, font_name, 9)
                if line and line_width + character_width > page_width - 2 * margin:
                    emit(line.rstrip())
                    line = ""
                    line_width = 0.0
                line += character
                line_width += character_width
        emit(line.rstrip())
    page.save()
    return output.getvalue()



# 이미지 압축률과 관계없는 메모리/치수 한도. 초과 시 내용 생략 대신 안전 실패한다.
MAX_RENDER_PIXELS = 8 * 1024 * 1024
MAX_RENDER_DIMENSION = 16384


def _literal_csv_cell(value: object) -> str:
    text = str(value)
    # CSV quoting alone does not prevent spreadsheet formula evaluation.
    # Prefix text without changing/removing any character of the original value.
    return "'" + text if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")) else text


def _display(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (Mapping, list, tuple)) else str(value)



_VISUAL_LABELS = {
    "key":"항목", "baseline":"기준", "current":"현재", "state":"상태", "evidence":"근거",
    "label":"항목", "value":"값", "body":"본문", "title":"제목", "summary":"요약", "warnings":"주의사항",
    "unit":"단위", "description":"설명", "slide_number":"장 번호", "item_id":"점검 항목",
    "judgement":"판정", "action":"필요 조치", "output_version_id":"산출물 버전", "created_at":"작성 시각",
    "knowledge_scope":"지식 범위", "evidence_appendix":"근거 부록",
    "ruleset_id":"규칙집 ID", "ruleset_version":"규칙집 버전", "request_id":"요청 ID", "model_id":"모델 ID",
    "lineage":"생성 계보", "difference":"차이", "baseline_version":"기준 버전", "current_version":"현재 버전",
}


def _visual_label(key: str) -> str:
    return _VISUAL_LABELS.get(key, key)


def _visual_value(value: object) -> str:
    if isinstance(value, Mapping):
        return "\n".join(f"{_visual_label(key)}: {_visual_value(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)) and any(isinstance(item, Mapping) for item in value):
        return "\n".join(_visual_value(item) for item in value)
    return _display(value)


def _validate_svg_xml(value: object) -> None:
    # XML 1.0 Fifth Edition character ranges; reject, never silently delete.
    if isinstance(value, str):
        if any(not (ord(ch) in (9,10,13) or 0x20 <= ord(ch) <= 0xD7FF
                    or 0xE000 <= ord(ch) <= 0xFFFD or 0x10000 <= ord(ch) <= 0x10FFFF) for ch in value):
            raise StudioExportError("STUDIO_OUTPUT_UNAVAILABLE")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_svg_xml(key); _validate_svg_xml(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_svg_xml(item)

def _metric_cards(content: object) -> list[list[str]]:
    def scalar(value: object) -> str:
        return "빈 문자열 (저장값)" if value == "" else _display(value)
    if isinstance(content, Mapping) and "metrics" in content:
        # 실제 저장 DTO의 title/summary/warnings 및 사용자 추가 필드를 지표와 함께 보존한다.
        siblings = [f"{_visual_label(key)}: {_visual_value(value)}" for key, value in content.items() if key != "metrics"]
        cards = [siblings] if siblings else []
        records = content["metrics"]
        if not isinstance(records, (list, tuple)):
            return cards if records is None or records == {} else cards + [["정보", scalar(records)]]
        for item in records:
            if item is None or item == {} or item == []:
                continue
            cards.append([f"{_visual_label(key)}: {_visual_value(value)}" for key, value in item.items()]
                         if isinstance(item, Mapping) else [scalar(item)])
        return cards
    if content is None or content == {} or content == []:
        return []
    return [[f"{_visual_label(key)}: {_visual_value(value)}" for key, value in content.items()]] if isinstance(content, Mapping) else [[scalar(content)]]


_TYPED_LABELS={"criterion":"점검 기준","observation":"확인 기록","question":"질문","answer":"답","explanation":"해설",
    "answer_index":"정답 번호","options":"보기","id":"항목 ID","source":"연결 항목 1","target":"연결 항목 2",
    "condition":"관계 근거","confidence":"원문 확인","template_id":"문서 구성","review_state":"검토 상태","citations":"인용 근거","conclusion":"결론"}


def _typed_display_value(value: object, *, field: str | None = None) -> str:
    if isinstance(value,Mapping):return "\n".join(f"{_TYPED_LABELS.get(key,_visual_label(key))}: {_typed_display_value(item,field=key)}" for key,item in value.items())
    if isinstance(value,(list,tuple)) and any(isinstance(item,Mapping) for item in value):return "\n".join(_typed_display_value(item) for item in value)
    if isinstance(value,str) and field in {"review_state","confidence","status","judgement","state"}:return {"draft":"초안","verified":"원문 확인됨","unverified":"미확인","needs_review":"검토 필요"}.get(value,value)
    return _display(value)


def _typed_document_lines(content: Mapping[str, object], output_type: str) -> list[str]:
    """신규 typed 자료의 본문을 사용자가 읽는 순서로 표시한다. 저장 JSON은 수정하지 않는다."""
    labels={"summary":"요약","body":"본문","conclusion":"결론","evidence":"근거","warnings":"주의사항","lineage":"생성 계보",
            "question":"질문","answer":"답","explanation":"해설","format":"문서 형식","status":"검토 상태","citations":"인용 근거"}
    lines=[];handled=set()
    if output_type=="evidence_report":
        for key in ("summary","body","conclusion","citations"):
            if key in content:lines.extend([labels[key],_typed_display_value(content[key])]);handled.add(key)
    elif output_type in {"business_draft","flashcards","quiz"}:
        collection={"business_draft":"sections","flashcards":"cards","quiz":"questions"}[output_type]
        handled.add(collection)
        for index,item in enumerate(content.get(collection,()),1):
            if not isinstance(item,Mapping):continue
            if output_type=="business_draft":
                lines.extend([str(item.get("title",f"본문 {index}")),str(item.get("body","")),"근거: "+_typed_display_value(item.get("evidence",[]))])
            elif output_type=="flashcards":
                lines.extend([f"질문 {index}",str(item.get("question","")),"답",str(item.get("answer","")),"근거: "+_typed_display_value(item.get("evidence",[]))])
            else:
                lines.extend([f"문제 {index}",str(item.get("question",""))])
                options=item.get("options",[])
                lines.extend(f"{number}. {option}" for number,option in enumerate(options,1))
                answer=item.get("answer_index")
                lines.extend(["정답: "+(str(answer+1) if type(answer) is int else str(item.get("answer","확인 필요"))),"해설",str(item.get("explanation","")),"근거: "+_typed_display_value(item.get("evidence",[]))])
    elif output_type=="knowledge_map":
        lines.append("인용 맥락 연결 · 방향 미확정: 선은 원문에 함께 나타난 항목을 연결하며 인과나 전달 방향을 확정하지 않습니다.")
        handled.update(("nodes","edges","relation_mode"))
        for index,node in enumerate(content.get("nodes",()),1):lines.append(f"항목 {index}: {_typed_display_value(node)}")
        for edge in content.get("edges",()):lines.append("연결 근거: "+_typed_display_value({key:value for key,value in edge.items() if key!="direction"}))
    for key,value in content.items():
        if key == "warnings" and value == []:continue
        if key not in handled:lines.append(f"{labels.get(key,_TYPED_LABELS.get(key,_visual_label(key)))}: {_typed_display_value(value,field=key)}")
    return lines


def _typed_visual_content(value: object) -> object:
    """새 typed의 표시 순서와 내부 상태 라벨만 정렬한다."""
    order=("title","summary","criterion","observation","question","body","answer","options","answer_index","explanation","evidence","judgement","ruleset_id","action")
    if isinstance(value,Mapping):
        result={}
        for key in (*[name for name in order if name in value],*[name for name in value if name not in order]):
            item=value[key]
            if key in {"judgement","state","ruleset_id","status"} and isinstance(item,str):
                item={"needs_review":"검토 필요","none":"지정 없음","same":"원문 동일","changed":"원문 차이 있음","draft":"초안"}.get(item,item)
            result[key]=_typed_visual_content(item)
        return result
    if isinstance(value,(tuple,list)):return [_typed_visual_content(item) for item in value]
    return value


def _typed_pdf(title: str, content: object, metadata: Mapping[str, str], output_type: str, *, modern: bool = False) -> bytes:
    from pathlib import Path
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, LongTable, TableStyle, PageBreak, Spacer

    font = "DaonNanumGothic"
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, str(Path(__file__).parent / "assets" / "NanumGothic-Regular.ttf")))
    body = ParagraphStyle("studio-body", fontName=font, fontSize=9, leading=14, wordWrap="CJK", splitLongWords=1)
    heading = ParagraphStyle("studio-heading", parent=body, fontSize=16, leading=22, spaceAfter=14)
    def paragraph(value: object, style=body):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)
    story = []
    if modern:content=_typed_visual_content(content)
    display_label=lambda key:_TYPED_LABELS.get(key,_visual_label(key)) if modern else _visual_label(key)
    display_value=_typed_display_value if modern else _visual_value
    if output_type == "slides" and isinstance(content, Mapping) and isinstance(content.get("slides"), (list, tuple)) and content["slides"]:
        for index, slide in enumerate(content["slides"]):
            if index:
                story.append(PageBreak())
            story.append(paragraph(f"{title} · {index + 1}", heading))
            if isinstance(slide, Mapping):
                for key, value in slide.items():
                    story.append(paragraph(value if key == "title" else f"{display_label(key)}: {display_value(value)}", heading if key == "title" else body))
                    story.append(Spacer(1, 8))
            else:
                story.append(paragraph(slide))
        for key, value in content.items():
            if key != "slides":
                story.append(paragraph(f"{display_label(key)}: {display_value(value)}"))
    elif modern and output_type in {"evidence_report","business_draft","flashcards","quiz","knowledge_map"} and isinstance(content,Mapping):
        story.append(paragraph(title,heading))
        if output_type=="knowledge_map":
            from reportlab.graphics.shapes import Drawing,Circle,Line,String
            nodes,edges=_graph(content);width=A4[0]-80;height=max(180,((len(nodes)+3)//4)*55+40)
            drawing=Drawing(width,height)
            positions={str(node.get("id",index)):(45+(index%4)*130,height-30-(index//4)*55) for index,node in enumerate(nodes)}
            if len(nodes)>32:raise StudioExportError("EXPORT_TOO_LARGE")
            for edge in edges:
                start,end=positions.get(str(edge.get("source"))),positions.get(str(edge.get("target")))
                if start and end:drawing.add(Line(*start,*end,strokeColor=colors.HexColor("#475569"),strokeWidth=2))
            for index,node in enumerate(nodes):
                x,y=positions[str(node.get("id",index))];drawing.add(Circle(x,y,14,fillColor=colors.HexColor("#dbeafe")))
                drawing.add(String(x,y-4,f"항목 {index+1}",fontName=font,fontSize=7,textAnchor="middle"))
            story.append(drawing)
        for line in _typed_document_lines(content,output_type):story.extend([paragraph(line),Spacer(1,8)])
    elif output_type in {"comparison_table", "compliance_checklist"}:
        story.append(paragraph(title, heading))
        rows = _structured_rows(content)
        rows = [[display_label(key) for key in rows[0]], *rows[1:]]
        columns = max((len(row) for row in rows), default=1)
        if columns > 32:
            raise StudioExportError("EXPORT_TOO_LARGE")
        cells = [[paragraph(value) for value in row] + [paragraph("")] * (columns - len(row)) for row in rows]
        table = LongTable(cells, colWidths=[(A4[0] - 80) / columns] * columns, repeatRows=1, splitInRow=1, hAlign="LEFT")
        table.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .5, colors.HexColor("#94a3b8")),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e2e8f0")), ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 6), ("RIGHTPADDING", (0,0), (-1,-1), 6),
            ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
        story.append(table)
        if isinstance(content, Mapping):
            collection = next((key for key in ("items", "rows", "sections") if content.get(key)), None)
            for key, value in content.items():
                if key != collection:
                    story.append(paragraph(f"{display_label(key)}: {display_value(value)}"))
    else:
        story.append(paragraph(title, heading))
        cards = _metric_cards(content)
        if not cards:
            cards = [["표시할 지표나 정보가 없습니다. 원본 자료를 확인해 주세요."]]
        for card in cards:
            table = LongTable([[paragraph(line)] for line in card], colWidths=[A4[0] - 80], splitInRow=1, hAlign="LEFT")
            table.setStyle(TableStyle([("BOX", (0,0), (-1,-1), .8, colors.HexColor("#94a3b8")),
                ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f8fafc")),
                ("LEFTPADDING", (0,0), (-1,-1), 10), ("RIGHTPADDING", (0,0), (-1,-1), 10),
                ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
            story.extend([table, Spacer(1, 12)])
    story.extend([Spacer(1, 16), paragraph("근거 부록", heading)])
    story.extend(paragraph(f"{_visual_label(key)}: {value}") for key, value in metadata.items())
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40,
                      invariant=1).build(story)
    return output.getvalue()


def _card_layout(title: str, content: object, metadata: Mapping[str, str]):
    from pathlib import Path
    from PIL import ImageFont
    font = ImageFont.truetype(str(Path(__file__).parent / "assets" / "NanumGothic-Regular.ttf"), 16)
    width = 800
    def wrap(text: str, available: int):
        lines = []
        for part in text.expandtabs(4).split("\n"):
            line = ""
            for ch in part:
                if line and font.getlength(line + ch) > available:
                    lines.append(line); line = ""
                line += ch
            lines.append(line)
        return lines
    def check(height: int):
        if height > MAX_RENDER_DIMENSION or width * height > MAX_RENDER_PIXELS:
            raise StudioExportError("EXPORT_TOO_LARGE")
    heading = wrap(title, 720); y = 24 + len(heading) * 24 + 20
    cards = _metric_cards(content) or [["표시할 지표나 정보가 없습니다. 원본 자료를 확인해 주세요."]]
    placed = []
    for card in cards:
        lines = [part for line in card for part in wrap(line, 680)]
        height = 32 + len(lines) * 24
        check(y + height + 20)
        placed.append((y, height, lines)); y += height + 16
    footer = [part for key, value in metadata.items() for part in wrap(f"{_visual_label(key)}: {value}", 720)]
    height = y + len(footer) * 24 + 24
    check(height)
    return width, height, heading, placed, y, footer, font


def _infographic(format_name: str, title: str, content: object, metadata: Mapping[str, str]) -> bytes:
    width, height, heading, cards, footer_y, footer, font = _card_layout(title, content, metadata)
    if format_name == "svg":
        elements = [f'<text x="40" y="{44+i*24}" font-size="16">{escape(line)}</text>' for i,line in enumerate(heading)]
        for y, h, lines in cards:
            elements.append(f'<rect x="40" y="{y}" width="720" height="{h}" rx="8" fill="#f8fafc" stroke="#94a3b8"/>')
            elements.extend(f'<text x="60" y="{y+32+i*24}" font-size="16">{escape(line)}</text>' for i,line in enumerate(lines))
        elements.extend(f'<text x="40" y="{footer_y+24+i*24}" font-size="16">{escape(line)}</text>' for i,line in enumerate(footer))
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
                f'<title>{escape(title)}</title>'+"".join(elements)+"</svg>").encode("utf-8")
    from PIL import Image, ImageDraw, PngImagePlugin
    image = Image.new("RGB", (width, height), "white"); draw = ImageDraw.Draw(image)
    for i,line in enumerate(heading):
        draw.text((40,24+i*24), line, font=font, fill="#0f172a")
    for y,h,lines in cards:
        draw.rounded_rectangle((40,y,760,y+h),radius=8,fill="#f8fafc",outline="#94a3b8")
        for i,line in enumerate(lines):
            draw.text((60,y+16+i*24),line,font=font,fill="#0f172a")
    for i,line in enumerate(footer):
        draw.text((40,footer_y+i*24),line,font=font,fill="#0f172a")
    info=PngImagePlugin.PngInfo()
    info.add_itxt("Studio", _payload(title,content,dict(metadata)) + ("\n표시할 지표나 정보가 없습니다" if not _metric_cards(content) else ""))
    output=BytesIO();image.save(output,format="PNG",pnginfo=info)
    return output.getvalue()

def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)


def _quoted_graph(format_name: str,title: str,content: Mapping[str,object],metadata: Mapping[str,str]) -> bytes:
    """신규 무방향 인용 맥락 그림과 긴 원문 범례를 모두 보존한다."""
    _validate_svg_xml((title,content,metadata))
    nodes,edges=_graph(content)
    if len(nodes)>32:raise StudioExportError("EXPORT_TOO_LARGE")
    extra=80+((len(nodes)+3)//4)*70
    positions={str(node.get("id",index)):(100+(index%4)*200,50+(index//4)*70) for index,node in enumerate(nodes)}
    legend={"metrics":[{"내용":line} for line in _typed_document_lines(content,"knowledge_map")]}
    width,base_height,*_=_card_layout(title,legend,metadata)
    height=base_height+extra
    if height>MAX_RENDER_DIMENSION or width*height>MAX_RENDER_PIXELS:raise StudioExportError("EXPORT_TOO_LARGE")
    if format_name=="svg":
        base=_infographic("svg",title,legend,metadata).decode("utf-8")
        inner=base[base.index(">")+1:base.rindex("</svg>")]
        elements=[]
        for edge in edges:
            start,end=positions.get(str(edge.get("source"))),positions.get(str(edge.get("target")))
            if start and end:elements.append(f'<line x1="{start[0]}" y1="{start[1]}" x2="{end[0]}" y2="{end[1]}" stroke="#475569" stroke-width="2"/>')
        for index,node in enumerate(nodes):
            x,y=positions[str(node.get("id",index))]
            elements.extend([f'<circle cx="{x}" cy="{y}" r="20" fill="#dbeafe" stroke="#1d4ed8"/>',f'<text x="{x}" y="{y+5}" text-anchor="middle" font-size="14">항목 {index+1}</text>'])
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'+"".join(elements)+f'<g transform="translate(0,{extra})">'+inner+'</g></svg>').encode("utf-8")
    from pathlib import Path
    from PIL import Image,ImageDraw,ImageFont,PngImagePlugin
    base=Image.open(BytesIO(_infographic("png",title,legend,metadata)))
    image=Image.new("RGB",(width,height),"white");image.paste(base,(0,extra));draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(str(Path(__file__).parent/"assets"/"NanumGothic-Regular.ttf"),14)
    for edge in edges:
        start,end=positions.get(str(edge.get("source"))),positions.get(str(edge.get("target")))
        if start and end:draw.line((*start,*end),fill="#475569",width=2)
    for index,node in enumerate(nodes):
        x,y=positions[str(node.get("id",index))];draw.ellipse((x-20,y-20,x+20,y+20),fill="#dbeafe",outline="#1d4ed8")
        draw.text((x,y),f"항목 {index+1}",font=font,fill="#0f172a",anchor="mm")
    info=PngImagePlugin.PngInfo();info.add_itxt("Studio",_payload(title,content,dict(metadata)))
    output=BytesIO();image.save(output,format="PNG",pnginfo=info);return output.getvalue()


def _graph(content: object) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
    if not isinstance(content, Mapping):
        return [], []
    nodes = [item for item in content.get("nodes", ()) if isinstance(item, Mapping)]
    edges = [item for item in content.get("edges", ()) if isinstance(item, Mapping)]
    return nodes, edges


def _graph_positions(nodes: list[Mapping[str, object]], width: int, height: int) -> dict[str, tuple[int, int]]:
    count = max(1, len(nodes)); margin = 80
    return {str(node.get("id", index)): (margin + ((width - margin * 2) * index // max(1, count - 1)), height // 2 + (-80 if index % 2 else 80)) for index, node in enumerate(nodes)}


def _svg_graph(title: str, content: object, metadata: Mapping[str, str]) -> bytes:
    nodes, edges = _graph(content); positions = _graph_positions(nodes, 800, 600)
    lines = []
    edge_labels = []
    for edge in edges:
        source, target = positions.get(str(edge.get("source"))), positions.get(str(edge.get("target")))
        if source and target:
            lines.append(f'<line x1="{source[0]}" y1="{source[1]}" x2="{target[0]}" y2="{target[1]}" stroke="#475569" stroke-width="3"/>')
            condition = edge.get("condition")
            if condition is not None and condition != "":
                edge_labels.append(f'<text x="{(source[0] + target[0]) // 2}" y="{(source[1] + target[1]) // 2 - 17}" text-anchor="middle" font-size="13">조건: {escape(str(condition))}</text>')
    circles = []
    for index, node in enumerate(nodes):
        x, y = positions[str(node.get("id", index))]
        circles.append(f'<circle cx="{x}" cy="{y}" r="34" fill="#dbeafe" stroke="#1d4ed8" stroke-width="3"/><text x="{x}" y="{y + 5}" text-anchor="middle">{escape(str(node.get("label", node.get("id", ""))))}</text>')
        for offset, name, value in ((58, "근거", node.get("evidence")), (75, "신뢰", node.get("confidence"))):
            if value is not None and value != "":
                circles.append(f'<text x="{x}" y="{y + offset}" text-anchor="middle" font-size="12">{name}: {escape(str(value))}</text>')
    appendix = [f'<text x="20" y="{580 - 16 * (len(metadata) - index - 1)}" font-size="12">{escape(str(key))}: {escape(str(value))}</text>' for index, (key, value) in enumerate(metadata.items())]
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"><title>{escape(title)}</title>{"".join(lines)}{"".join(edge_labels)}{"".join(circles)}{"".join(appendix)}</svg>'.encode()


def _png_graph(text: str, content: object) -> bytes:
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageFont

    width, height = 640, 480; nodes, edges = _graph(content); positions = _graph_positions(nodes, width, height)
    pixels = bytearray(b"\xff\xff\xff" * width * height)
    def point(x: int, y: int, color: tuple[int, int, int] = (30, 64, 175)) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3; pixels[offset:offset + 3] = bytes(color)
    def line(start: tuple[int, int], finish: tuple[int, int]) -> None:
        x0, y0 = start; x1, y1 = finish; dx = abs(x1 - x0); sx = 1 if x0 < x1 else -1; dy = -abs(y1 - y0); sy = 1 if y0 < y1 else -1; error = dx + dy
        while True:
            point(x0, y0)
            if x0 == x1 and y0 == y1: break
            twice = 2 * error
            if twice >= dy: error += dy; x0 += sx
            if twice <= dx: error += dx; y0 += sy
    for edge in edges:
        source, target = positions.get(str(edge.get("source"))), positions.get(str(edge.get("target")))
        if source and target: line(source, target)
    for index, node in enumerate(nodes):
        cx, cy = positions[str(node.get("id", index))]
        for y in range(cy - 24, cy + 25):
            for x in range(cx - 24, cx + 25):
                distance = (x - cx) ** 2 + (y - cy) ** 2
                if distance <= 24 ** 2: point(x, y, (219, 234, 254) if distance < 21 ** 2 else (29, 78, 216))
    image = Image.frombytes("RGB", (width, height), bytes(pixels))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(Path(__file__).parent / "assets" / "NanumGothic-Regular.ttf"), 14)
    for edge in edges:
        source, target = positions.get(str(edge.get("source"))), positions.get(str(edge.get("target")))
        condition = str(edge.get("condition", ""))
        if source and target and condition:
            draw.text(((source[0] + target[0]) // 2, (source[1] + target[1]) // 2 - 28), condition,
                      font=font, fill=(15, 23, 42), anchor="mm")
    for index, node in enumerate(nodes):
        cx, cy = positions[str(node.get("id", index))]
        draw.text((cx, cy + 29), str(node.get("label", node.get("id", ""))),
                  font=font, fill=(15, 23, 42), anchor="mt")
    pixels = image.tobytes()
    scanlines = b"".join(b"\x00" + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + _chunk(b"tEXt", b"Studio\x00" + text.encode("utf-8")) + _chunk(b"IDAT", zlib.compress(scanlines)) + _chunk(b"IEND", b"")


def export_studio_output(format_name: str, title: str, content: object, metadata: dict[str, str], *, output_type: str | None = None, generator_version: str | None = None) -> StudioExport:
    if format_name not in MEDIA_TYPES:
        raise StudioExportError("EXPORT_FORMAT_UNSUPPORTED")
    if output_type is not None:
        if format_name not in OUTPUT_FORMATS.get(output_type, frozenset()):
            raise StudioExportError("EXPORT_FORMAT_UNSUPPORTED")
        if output_type in {"audio", "video"}:
            # No media provider/encoder is configured. Never manufacture a
            # downloadable placeholder that looks like a completed artifact.
            raise StudioExportError("STUDIO_OUTPUT_UNAVAILABLE")
    if format_name == "svg" and output_type == "infographic":
        _validate_svg_xml((title, content, metadata))
    encoded_content = json.dumps(content, ensure_ascii=False).encode("utf-8") if not isinstance(content, str) else content.encode("utf-8")
    if len(encoded_content) > MAX_EXPORT_BYTES:
        raise StudioExportError("EXPORT_TOO_LARGE")
    text = _payload(title, content, metadata)
    modern=generator_version=="studio-selected-evidence-v1"
    if modern and isinstance(content,Mapping) and format_name in {"docx","pdf"}:
        text="\n\n".join([title,*_typed_document_lines(content,output_type),"근거 부록",*(_visual_label(k)+": "+v for k,v in metadata.items())])
    if format_name == "docx": data = _docx(text)
    elif format_name == "xlsx": data = _xlsx([["제목", title], *_structured_rows(content), [], ["근거 부록", ""], *[[key, value] for key, value in metadata.items()]])
    elif format_name == "pdf": data = _typed_pdf(title, content, metadata, output_type,modern=modern) if modern or output_type in {"comparison_table", "compliance_checklist", "slides", "infographic"} else _pdf(text)
    elif format_name == "json": data = json.dumps({"title": title, "output_type": output_type, "content": content, "metadata": metadata}, ensure_ascii=False).encode()
    elif format_name == "svg" and modern and output_type=="knowledge_map": data=_quoted_graph("svg",title,content,metadata)
    elif format_name == "png" and modern and output_type=="knowledge_map": data=_quoted_graph("png",title,content,metadata)
    elif format_name == "svg": data = _infographic(format_name, title, content, metadata) if output_type == "infographic" else _svg_graph(title, content, metadata)
    elif format_name == "png": data = _infographic(format_name, title, content, metadata) if output_type == "infographic" else _png_graph(text, content)
    else:
        output = StringIO(); writer = csv.writer(output, lineterminator="\n")
        rows = [["제목", title], *_structured_rows(content), [], ["근거 부록"], *metadata.items()]
        writer.writerows([_literal_csv_cell(value) for value in row] for row in rows)
        data = output.getvalue().encode("utf-8-sig")
    if len(data) > MAX_EXPORT_BYTES:
        raise StudioExportError("EXPORT_TOO_LARGE")
    filename = f"studio-{metadata['output_version_id']}.{format_name}"
    return StudioExport(data, MEDIA_TYPES[format_name], filename, hashlib.sha256(data).hexdigest())
