from __future__ import annotations

import binascii
import csv
import hashlib
import json
import struct
import zlib
import zipfile
from dataclasses import dataclass
from io import BytesIO, StringIO
from typing import Mapping
from xml.sax.saxutils import escape

MAX_EXPORT_BYTES = 8 * 1024 * 1024
MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8", "json": "application/json", "svg": "image/svg+xml", "png": "image/png",
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
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>',
    })


def _pdf(text: str) -> bytes:
    encoded = text.encode("utf-16-be").hex().upper()
    stream = f"BT /F1 9 Tf 40 800 Td <{encoded}> Tj ET".encode()
    objects = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj",
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>endobj",
        b"4 0 obj<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream endobj",
        b"5 0 obj<</Type/Font/Subtype/Type0/BaseFont/HYSMyeongJo-Medium/Encoding/UniKS-UCS2-H/DescendantFonts[6 0 R]>>endobj",
        b"6 0 obj<</Type/Font/Subtype/CIDFontType0/BaseFont/HYSMyeongJo-Medium/CIDSystemInfo<</Registry(Adobe)/Ordering(Korea1)/Supplement 2>>>>endobj",
    ]
    result = bytearray(b"%PDF-1.4\n"); offsets = [0]
    for item in objects: offsets.append(len(result)); result.extend(item + b"\n")
    xref = len(result); result.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]: result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer<</Size {len(objects)+1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(result)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)


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
    for edge in edges:
        source, target = positions.get(str(edge.get("source"))), positions.get(str(edge.get("target")))
        if source and target:
            lines.append(f'<line x1="{source[0]}" y1="{source[1]}" x2="{target[0]}" y2="{target[1]}" stroke="#475569" stroke-width="3"/>')
    circles = []
    for index, node in enumerate(nodes):
        x, y = positions[str(node.get("id", index))]
        circles.append(f'<circle cx="{x}" cy="{y}" r="34" fill="#dbeafe" stroke="#1d4ed8" stroke-width="3"/><text x="{x}" y="{y + 5}" text-anchor="middle">{escape(str(node.get("label", node.get("id", ""))))}</text>')
    appendix = " · ".join(f"{key}: {value}" for key, value in metadata.items())
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"><title>{escape(title)}</title>{"".join(lines)}{"".join(circles)}<text x="20" y="580">{escape(appendix)}</text></svg>'.encode()


def _png_graph(text: str, content: object) -> bytes:
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
    scanlines = b"".join(b"\x00" + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + _chunk(b"tEXt", b"Studio\x00" + text.encode("utf-8")) + _chunk(b"IDAT", zlib.compress(scanlines)) + _chunk(b"IEND", b"")


def export_studio_output(format_name: str, title: str, content: object, metadata: dict[str, str], *, output_type: str | None = None) -> StudioExport:
    if format_name not in MEDIA_TYPES:
        raise StudioExportError("EXPORT_FORMAT_UNSUPPORTED")
    encoded_content = json.dumps(content, ensure_ascii=False).encode("utf-8") if not isinstance(content, str) else content.encode("utf-8")
    if len(encoded_content) > MAX_EXPORT_BYTES:
        raise StudioExportError("EXPORT_TOO_LARGE")
    text = _payload(title, content, metadata)
    if format_name == "docx": data = _docx(text)
    elif format_name == "xlsx": data = _xlsx([["제목", title], *_structured_rows(content), [], ["근거 부록", ""], *[[key, value] for key, value in metadata.items()]])
    elif format_name == "pdf": data = _pdf(text)
    elif format_name == "json": data = json.dumps({"title": title, "output_type": output_type, "content": content, "metadata": metadata}, ensure_ascii=False).encode()
    elif format_name == "svg": data = _svg_graph(title, content, metadata)
    elif format_name == "png": data = _png_graph(text, content)
    else:
        output = StringIO(); writer = csv.writer(output, lineterminator="\n"); writer.writerow(["제목", title]); writer.writerows(_structured_rows(content)); writer.writerow([]); writer.writerow(["근거 부록"]); writer.writerows(metadata.items()); data = output.getvalue().encode("utf-8-sig")
    if len(data) > MAX_EXPORT_BYTES:
        raise StudioExportError("EXPORT_TOO_LARGE")
    filename = f"studio-{metadata['output_version_id']}.{format_name}"
    return StudioExport(data, MEDIA_TYPES[format_name], filename, hashlib.sha256(data).hexdigest())
