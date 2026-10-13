"""Bounded OOXML structure input and original-cell views, never semantics.

No formula engine, Office process, filesystem extraction or network resolver.
The cached value is the value stored in the immutable original, not a recalculation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import posixpath
import re
import zipfile
import zlib
from xml.etree import ElementTree as ET

from .source_ingest import SourceRejected

_MAX_UNCOMPRESSED = 32 * 1024 * 1024
_MAX_NATIVE_BYTES = 32 * 1024
_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_DOC_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_PACKAGE_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
_CONTENT_TYPES = 'http://schemas.openxmlformats.org/package/2006/content-types'
_XML_PARENTS = {
    'workbook': (None,), 'worksheet': (None,), 'sst': (None,), 'styleSheet': (None,),
    'workbookPr': ('workbook',), 'sheets': ('workbook',), 'sheet': ('sheets',),
    'sheetData': ('worksheet',), 'row': ('sheetData',), 'c': ('row',),
    'v': ('c',), 'f': ('c',), 'is': ('c',), 'si': ('sst',),
    'r': ('si', 'is'), 't': ('si', 'is', 'r'),
    'cols': ('worksheet',), 'col': ('cols',), 'dimension': ('worksheet',),
    'mergeCells': ('worksheet',), 'mergeCell': ('mergeCells',),
    'cellXfs': ('styleSheet',), 'cellStyleXfs': ('styleSheet',),
    'xf': ('cellXfs', 'cellStyleXfs'), 'numFmts': ('styleSheet',), 'numFmt': ('numFmts', 'dxf'),
}
_CELL = re.compile(r'([A-Z]{1,3})([1-9][0-9]{0,6})\Z')
_BUILTIN_FORMATS = {0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00', 9: '0%', 10: '0.00%', 11: '0.00E+00', 12: '# ?/?', 13: '# ??/??', 14: 'mm-dd-yy', 15: 'd-mmm-yy', 16: 'd-mmm', 17: 'mmm-yy', 18: 'h:mm AM/PM', 19: 'h:mm:ss AM/PM', 20: 'h:mm', 21: 'h:mm:ss', 22: 'm/d/yy h:mm', 37: '#,##0 ;(#,##0)', 38: '#,##0 ;[Red](#,##0)', 39: '#,##0.00;(#,##0.00)', 40: '#,##0.00;[Red](#,##0.00)', 45: 'mm:ss', 46: '[h]:mm:ss', 47: 'mmss.0', 48: '##0.0E+0', 49: '@'}


class _FrozenDict(dict):
    def _readonly(self, *args, **kwargs):
        raise TypeError('immutable workbook')
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly


def _freeze(value):
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class XlsxWorkbook:
    source_sha256: str
    sheets: tuple[dict[str, object], ...]
    native_json: str

    def __post_init__(self):
        object.__setattr__(self, 'sheets', _freeze(self.sheets))


def _reject(code='CORRUPTED_SOURCE'):
    raise SourceRejected(code)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _sheet_name(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 31 or re.search(r'[\x00-\x1f\x7f\[\]:*?/\\]', name):
        _reject()
    return name


def _coordinate(value):
    match = _CELL.fullmatch(value) if isinstance(value, str) else None
    if not match:
        _reject()
    col = 0
    for letter in match[1]: col = col * 26 + ord(letter) - 64
    row = int(match[2])
    if col > 16384 or row > 1048576: _reject()
    return col, row


def _address(col, row):
    letters = ''
    while col:
        col, digit = divmod(col - 1, 26); letters = chr(65 + digit) + letters
    return f'{letters}{row}'


def _bounds(value, *, selected=False):
    if not isinstance(value, str) or value.count(':') > 1: _reject()
    parts = value.split(':')
    start = _coordinate(parts[0]); end = _coordinate(parts[-1])
    if end[0] < start[0] or end[1] < start[1] or (len(parts) == 2 and start == end): _reject()
    if selected and (end[0] - start[0] + 1) * (end[1] - start[1] + 1) > 256: _reject()
    return *start, *end


def _range(bounds):
    a, b, c, d = bounds
    start, end = _address(a, b), _address(c, d)
    return start if start == end else f'{start}:{end}'


def encode_sheet_cell(sheet: str, cell_range: str) -> str:
    _sheet_name(sheet); _bounds(cell_range, selected=True)
    return _dump({'sheet': sheet, 'range': cell_range})


def decode_sheet_cell(value: str) -> tuple[str, str]:
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result: _reject()
            result[key] = item
        return result
    try:
        if not isinstance(value, str) or len(value.encode('utf-8')) > 512: _reject()
        item = json.loads(value, object_pairs_hook=unique)
        if not isinstance(item, dict) or list(item) != ['sheet', 'range']: _reject()
        if encode_sheet_cell(item['sheet'], item['range']) != value: _reject()
        return item['sheet'], item['range']
    except SourceRejected: raise
    except (ValueError, TypeError, UnicodeError): _reject()


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _xml_records(stream, total):
    """Pull parse with completed records removed; scan UTF-8/16/32 ASCII markup.

    Removing NUL bytes only for the security scan covers BOM/endianness without
    changing the bytes supplied to the XML parser. No entity declaration reaches
    feed(), including one split across a read boundary.
    """
    parser = ET.XMLPullParser(events=('start', 'end'))
    stack = []; records = []; tail = b''; root_tag = None; root_attrs = {}
    relevant = {'sheet', 'workbookPr', 'Relationship', 'Override', 'Default', 'numFmt', 'xf', 'si', 'c', 'row', 'col', 'mergeCell', 'dimension'}
    raw_cell_bytes = 0
    while True:
        chunk = stream.read(65536)
        if not chunk: break
        total[0] += len(chunk)
        if total[0] > _MAX_UNCOMPRESSED: _reject('COMPRESSION_BOMB')
        scan = tail + chunk.replace(b'\x00', b'').lower()
        if b'<!doctype' in scan or b'<!entity' in scan: _reject()
        tail = scan[-32:]
        parser.feed(chunk)
        for event, elem in parser.read_events():
            tag = _local(elem.tag)
            if event == 'start':
                if root_tag is None: root_tag, root_attrs = elem.tag, dict(elem.attrib)
                # A recognized local name is never sufficient to create facts.
                # Parent checks use expanded names, not namespace-stripped tags.
                namespace = _NS; parents = _XML_PARENTS.get(tag)
                if tag in {'Relationships', 'Relationship'}:
                    namespace = _PACKAGE_REL; parents = (None,) if tag == 'Relationships' else ('Relationships',)
                elif tag in {'Types', 'Override', 'Default'}:
                    namespace = _CONTENT_TYPES; parents = (None,) if tag == 'Types' else ('Types',)
                if parents is not None:
                    parent_tag = stack[-1].tag if stack else None
                    allowed = tuple(None if parent is None else f'{{{namespace}}}{parent}' for parent in parents)
                    if elem.tag != f'{{{namespace}}}{tag}' or parent_tag not in allowed: _reject()
                if tag in {'drawing', 'legacyDrawing', 'picture', 'pivotTable', 'tableParts', 'oleObjects', 'externalReferences', 'hyperlink', 'extLst'}:
                    _reject('UNSUPPORTED_FORMAT')
                stack.append(elem)
                continue
            parent = stack[-2] if len(stack) > 1 else None
            inside_value = any(_local(ancestor.tag) in {'si', 'c'} for ancestor in stack[:-1])
            if tag in relevant and not inside_value:
                record = {'tag': tag, 'parent': _local(parent.tag) if parent is not None else None, 'attrs': dict(elem.attrib)}
                if tag == 'si': record['text'] = ''.join(node.text or '' for node in elem.iter() if _local(node.tag) == 't')
                if tag == 'c':
                    # Exact namespace and single v/f/is prevent ambiguous values.
                    values = {}
                    for node in elem:
                        key = _local(node.tag)
                        if key in {'v', 'f', 'is'}:
                            if node.tag != f'{{{_NS}}}{key}' or key in values: _reject()
                            values[key] = node
                    record['value'] = values['v'].text if 'v' in values else None
                    record['formula'] = values['f'].text if 'f' in values else None
                    record['has_formula'] = 'f' in values
                    record['inline'] = ''.join(node.text or '' for node in values['is'].iter() if _local(node.tag) == 't') if 'is' in values else None
                    record['row'] = dict(parent.attrib) if parent is not None and _local(parent.tag) == 'row' else None
                    raw_cell_bytes += len(_dump(record['attrs']).encode('utf-8'))
                    if raw_cell_bytes > _MAX_NATIVE_BYTES: _reject('UNSUPPORTED_FORMAT')
                records.append(record)
            stack.pop()
            if not inside_value:
                if parent is not None: parent.remove(elem)
                elem.clear()
    parser.close()
    return root_tag, root_attrs, records


def _parts(content):
    result = {}; total = [0]
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > 128: _reject('UNSUPPORTED_FORMAT')
        if sum(member.file_size for member in members) > _MAX_UNCOMPRESSED: _reject('COMPRESSION_BOMB')
        names = set()
        for member in members:
            name = member.filename
            if name in names or name.startswith('/') or '\\' in name or re.search(r'[\x00-\x1f\x7f:]', name) or any(part in {'', '.', '..'} for part in name.rstrip('/').split('/')): _reject()
            names.add(name)
            local_flags = int.from_bytes(content[member.header_offset + 6:member.header_offset + 8], 'little')
            if (member.flag_bits | local_flags) & 0x41: _reject('ENCRYPTED_SOURCE')
            if member.file_size > 100 * member.compress_size: _reject('COMPRESSION_BOMB')
            lowered = name.lower()
            if any(term in lowered for term in ('vbaproject', 'charts/', 'drawings/', 'media/', 'pivottable', 'pivotcache', 'externallinks/', 'connections.xml', 'embeddings/', 'querytables/')): _reject('UNSUPPORTED_FORMAT')
        for member in members:
            with archive.open(member) as stream:
                if member.filename.lower().endswith(('.xml', '.rels')):
                    result[member.filename] = _xml_records(stream, total)
                else:
                    while chunk := stream.read(65536):
                        total[0] += len(chunk)
                        if total[0] > _MAX_UNCOMPRESSED: _reject('COMPRESSION_BOMB')
        # No relationship may dereference an external destination, regardless
        # of which unused part contains it. Targets are package paths only.
        for name, (_, _, records) in result.items():
            if not name.lower().endswith('.rels'): continue
            base = '' if name == '_rels/.rels' else posixpath.dirname(name).rsplit('/_rels', 1)[0]
            for record in records:
                if record['tag'] != 'Relationship': continue
                attrs = record['attrs']; target = attrs.get('Target', '')
                if attrs.get('TargetMode', 'Internal') != 'Internal' or not target or ':' in target or '\\' in target or '#' in target or attrs.get('Type', '').endswith('/hyperlink'): _reject('UNSUPPORTED_FORMAT')
                resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(base, target))
                if resolved.startswith('../') or resolved not in names: _reject()
    return result


def _get_part(parts, name, root):
    part = parts.get(name)
    if part is None or part[0] != f'{{{_NS}}}{root}': _reject()
    return part[2]


def _integer(value, *, minimum=0, maximum=1048576):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+', value): _reject()
    result = int(value)
    if not minimum <= result <= maximum: _reject()
    return result


def _boolean(value):
    if value not in {'0', '1', 'false', 'true'}: _reject()
    return value in {'1', 'true'}


def _display_date(raw, date1904):
    value = _number(raw)
    # Exclusive upper bound includes the last day's fractional time. Compare
    # Decimals before any int/float conversion; no giant integer is allocated.
    upper = Decimal('2957004' if date1904 else '2958466')
    if value < 0 or value >= upper: _reject()
    if not date1904 and Decimal(60) <= value < Decimal(61): return None
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 31)
    if not date1904 and value >= 60: value -= 1
    try:
        date = base + timedelta(days=float(value))
        return date.isoformat(timespec='seconds') if value % 1 else date.date().isoformat()
    except (ValueError, OverflowError): _reject()


def _number(raw):
    # XLSX numeric cells are finite binary64 values, not arbitrary-precision
    # expressions. Bound lexical work before constructing a Decimal.
    if not isinstance(raw, str) or len(raw) > 128: _reject()
    match = re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee]([+-]?[0-9]{1,3}))?', raw)
    if not match: _reject()
    if match[1] is not None and not -324 <= int(match[1]) <= 308: _reject()
    try:
        value = Decimal(raw)
        if not value.is_finite() or value.copy_abs() > Decimal('1.7976931348623157E+308'): _reject()
        return value
    except InvalidOperation: _reject()


def _cell(record, strings, formats, date1904, hidden_columns):
    attrs = record['attrs']; address = attrs.get('r'); col, row = _coordinate(address)
    row_attrs = record['row']
    if row_attrs is None or _integer(row_attrs.get('r'), minimum=1) != row: _reject()
    style = _integer(attrs.get('s', '0'))
    if style >= len(formats): _reject()
    number_format = formats[style]
    kind = attrs.get('t', 'n'); raw = record['value']; formula = record['formula']
    if record['has_formula'] and not formula: _reject()
    if formula is not None and ('[' in formula or re.search(r'\b(?:HYPERLINK|WEBSERVICE|RTD|DDE)\s*\(', formula, re.I)):
        _reject('UNSUPPORTED_FORMAT')
    if kind == 's':
        index = _integer(raw)
        if index >= len(strings): _reject()
        raw = strings[index]; value_type = 'string'
    elif kind == 'inlineStr':
        if record['inline'] is None or raw is not None: _reject()
        raw = record['inline']; value_type = 'string'
    elif kind == 'str': value_type = 'string'
    elif kind == 'b':
        if raw is not None and raw not in {'0', '1'}: _reject()
        value_type = 'boolean'
    elif kind == 'e': value_type = 'error'
    elif kind == 'd':
        if raw is not None:
            try: datetime.fromisoformat(raw.replace('Z', '+00:00'))
            except ValueError: _reject()
        value_type = 'date'
    elif kind == 'n':
        if raw is not None:
            _number(raw)
        format_tokens = re.sub(r'"[^"]*"|\\.|\[[^]]*\]', '', number_format)
        value_type = 'date' if re.search(r'[ymdhis]', format_tokens, re.I) and number_format != 'General' else 'number'
    else: _reject('UNSUPPORTED_FORMAT')
    display = raw
    if value_type == 'error': display = None
    elif value_type == 'boolean' and raw is not None: display = 'TRUE' if raw == '1' else 'FALSE'
    elif value_type == 'date' and kind == 'n' and raw is not None: display = _display_date(raw, date1904)
    return {'address': address, 'value_type': value_type if raw is not None or formula is not None else 'blank', 'raw_value': raw, 'display_value': display, 'formula': formula, 'cached_value': raw if formula is not None else None, 'number_format': number_format, 'hidden_row': _boolean(row_attrs.get('hidden', '0')), 'hidden_column': any(a <= col <= b for a, b in hidden_columns)}


def read_xlsx(content: bytes) -> XlsxWorkbook:
    try:
        if not isinstance(content, bytes) or not content.startswith(b'PK\x03\x04'): _reject()
        parts = _parts(content)
        types = parts.get('[Content_Types].xml')
        if types is None or types[0] != f'{{{_CONTENT_TYPES}}}Types': _reject()
        if any('macroEnabled' in r['attrs'].get('ContentType', '') for r in types[2]): _reject('UNSUPPORTED_FORMAT')
        workbook = _get_part(parts, 'xl/workbook.xml', 'workbook')
        date1904 = False
        if sum(record['tag'] == 'workbookPr' for record in workbook) > 1: _reject()
        for record in workbook:
            if record['tag'] == 'workbookPr': date1904 = _boolean(record['attrs'].get('date1904', '0'))
        rel_part = parts.get('xl/_rels/workbook.xml.rels')
        if rel_part is None: _reject()
        relationships = {}
        for record in rel_part[2]:
            if record['tag'] != 'Relationship': continue
            attrs = record['attrs']; rid = attrs.get('Id')
            if not rid or rid in relationships: _reject()
            relationships[rid] = attrs
        strings = [r['text'] for r in _get_part(parts, 'xl/sharedStrings.xml', 'sst') if r['tag'] == 'si'] if 'xl/sharedStrings.xml' in parts else []
        formats = ['General']
        if 'xl/styles.xml' in parts:
            styles = _get_part(parts, 'xl/styles.xml', 'styleSheet'); num_formats = dict(_BUILTIN_FORMATS)
            for record in styles:
                if record['tag'] == 'numFmt':
                    attrs = record['attrs']; ident = _integer(attrs.get('numFmtId'))
                    if ident in num_formats or not attrs.get('formatCode'): _reject()
                    num_formats[ident] = attrs['formatCode']
            formats = []
            for record in styles:
                if record['tag'] == 'xf' and record['parent'] == 'cellXfs':
                    ident = _integer(record['attrs'].get('numFmtId', '0'))
                    if ident not in num_formats: _reject('UNSUPPORTED_FORMAT')
                    formats.append(num_formats[ident])
            if not formats: _reject()
        sheet_records = [r for r in workbook if r['tag'] == 'sheet']
        if not 1 <= len(sheet_records) <= 8: _reject('UNSUPPORTED_FORMAT')
        sheets = []; names = set(); targets = set(); nonempty = 0
        for record in sheet_records:
            attrs = record['attrs']; name = _sheet_name(attrs.get('name')); visibility = attrs.get('state', 'visible')
            if name in names or visibility not in {'visible', 'hidden', 'veryHidden'}: _reject()
            names.add(name)
            rel = relationships.get(attrs.get(f'{{{_DOC_REL}}}id'))
            if rel is None or rel.get('Type') != f'{_DOC_REL}/worksheet': _reject()
            target = rel['Target']; target = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('xl', target))
            if target in targets: _reject()
            targets.add(target); rows = _get_part(parts, target, 'worksheet')
            hidden_columns = []; hidden_rows = []; merged = []; seen_rows = set()
            for item in rows:
                if item['tag'] == 'dimension': _bounds(item['attrs'].get('ref'))
                if item['tag'] == 'col':
                    a = _integer(item['attrs'].get('min'), minimum=1, maximum=16384); b = _integer(item['attrs'].get('max'), minimum=a, maximum=16384)
                    if _boolean(item['attrs'].get('hidden', '0')): hidden_columns.append((a, b))
                if item['tag'] == 'row':
                    row = _integer(item['attrs'].get('r'), minimum=1)
                    if row in seen_rows: _reject()
                    seen_rows.add(row)
                    if _boolean(item['attrs'].get('hidden', '0')): hidden_rows.append(row)
                if item['tag'] == 'mergeCell':
                    value = item['attrs'].get('ref'); _bounds(value)
                    if value in merged: _reject()
                    merged.append(value)
            cells = []; addresses = set(); max_col = max_row = 1
            for item in rows:
                if item['tag'] != 'c': continue
                cell = _cell(item, strings, formats, date1904, hidden_columns)
                if cell['address'] in addresses: _reject()
                addresses.add(cell['address']); col, row = _coordinate(cell['address']); max_col = max(max_col, col); max_row = max(max_row, row)
                if cell['raw_value'] not in {None, ''} or cell['formula'] is not None: nonempty += 1
                if nonempty > 2048: _reject('UNSUPPORTED_FORMAT')
                cells.append(cell)
            for value in merged:
                _, _, col, row = _bounds(value); max_col = max(max_col, col); max_row = max(max_row, row)
            sheets.append({'sheet_name': name, 'sheet_visibility': visibility, 'cells': cells, 'merged_ranges': merged, 'hidden_rows': hidden_rows, 'hidden_columns': hidden_columns, 'used_range': f'A1:{_address(max_col, max_row)}' if (max_col, max_row) != (1, 1) else 'A1'})
        native_json = _dump({'serialization_version': 'xlsx-structure-v1', 'date1904': date1904, 'sheets': sheets})
        if len(native_json.encode('utf-8')) > _MAX_NATIVE_BYTES: _reject('UNSUPPORTED_FORMAT')
        return XlsxWorkbook(hashlib.sha256(content).hexdigest(), tuple(sheets), native_json)
    except SourceRejected: raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ET.ParseError, OSError, EOFError, ValueError, TypeError, KeyError, RuntimeError, NotImplementedError, UnicodeError, zlib.error):
        _reject()


def read_sheet_cell_view(workbook: XlsxWorkbook, locator_value: str) -> dict[str, object]:
    name, selected = decode_sheet_cell(locator_value)
    sheet = next((item for item in workbook.sheets if item['sheet_name'] == name), None)
    if sheet is None: _reject()
    a, b, c, d = _bounds(selected, selected=True)
    _, _, max_col, max_row = _bounds(sheet['used_range'])
    if c > max_col or d > max_row: _reject()
    view = max(1, a - 1), max(1, b - 1), min(16384, c + 1), min(1048576, d + 1)
    x, y, end_x, end_y = view
    if (end_x - x + 1) * (end_y - y + 1) > 324: _reject()
    cells = {cell['address']: cell for cell in sheet['cells']}
    displayed = []
    for row in range(y, end_y + 1):
        for col in range(x, end_x + 1):
            address = _address(col, row)
            cell = cells.get(address)
            displayed.append(dict(cell) if cell is not None else {'address': address, 'value_type': 'blank', 'raw_value': None, 'display_value': None, 'formula': None, 'cached_value': None, 'number_format': None, 'hidden_row': row in sheet['hidden_rows'], 'hidden_column': any(start <= col <= end for start, end in sheet['hidden_columns'])})
    merged = []
    for value in sheet['merged_ranges']:
        ma, mb, mc, md = _bounds(value)
        if ma <= end_x and mc >= x and mb <= end_y and md >= y: merged.append(value)
    return {'source_sha256': workbook.source_sha256, 'sheet_name': name, 'sheet_visibility': sheet['sheet_visibility'], 'selected_range': selected, 'view_range': _range(view), 'cells': displayed, 'merged_ranges': merged}
