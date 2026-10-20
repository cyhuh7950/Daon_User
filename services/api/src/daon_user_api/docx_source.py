"""Bounded Transitional DOCX structure and code-point views, never semantics."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import posixpath
import re
import zipfile
import zlib
from xml.etree import ElementTree as ET

from .source_ingest import SourceRejected

_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
_MAIN = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
_VERSION = 'docx-structure-v1'
_MAX_BYTES = 32 * 1024 * 1024
_MAX_JSON = 32 * 1024
_ID = re.compile(r'(?:p:[1-9][0-9]?|t:[1-9][0-9]?:r:[1-9][0-9]?:c:[1-9][0-9]?:p:[1-9][0-9]{0,2})\Z')
_FORMAT = frozenset(('pPr rPr pStyle rStyle outlineLvl spacing jc ind tabs tab keepNext keepLines '
    'pageBreakBefore widowControl contextualSpacing mirrorIndents suppressLineNumbers '
    'suppressAutoHyphens textAlignment textDirection bidi adjustRightInd snapToGrid '
    'b bCs i iCs caps smallCaps strike dstrike color highlight u sz szCs rFonts lang '
    'position kern vertAlign rtl cs complexScript effect emboss imprint shadow outline '
    'noProof fitText shd bdr pBdr top left bottom right between bar '
    'tblPr tblStyle tblW tblInd tblBorders insideH insideV tblCellMar tblLayout tblLook '
    'tblGrid gridCol trPr trHeight cantSplit tblHeader jcBefore jcAfter '
    'tcPr tcW vAlign noWrap tcMar tcBorders sectPr pgSz pgMar cols col docGrid '
    'headerReference footerReference titlePg type textWrapping').split())
_STYLE_META = frozenset(('name aliases basedOn next link autoRedefine hidden semiHidden '
    'unhideWhenUsed qFormat uiPriority locked personal personalCompose personalReply rsid').split())


class _FrozenDict(dict):
    def _readonly(self, *args, **kwargs):
        raise TypeError('immutable document')
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly


def _freeze(value):
    if isinstance(value, dict): return _FrozenDict({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)): return tuple(_freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class DocxDocument:
    source_sha256: str
    blocks: tuple[dict[str, object], ...]
    native_json: str

    def __post_init__(self):
        object.__setattr__(self, 'blocks', _freeze(self.blocks))


def _reject(code='CORRUPTED_SOURCE'):
    raise SourceRejected(code)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _word(element):
    if not isinstance(element.tag, str) or not element.tag.startswith('{' + _W + '}'):
        _reject('UNSUPPORTED_FORMAT')
    kind = element.tag[len(_W) + 2:]
    if (kind != 't' and element.text and element.text.strip()) or (element.tail and element.tail.strip()):
        _reject()
    return kind


def _val(element):
    return element.get('{' + _W + '}val')


def _format(element):
    for node in element.iter():
        if _word(node) not in _FORMAT: _reject('UNSUPPORTED_FORMAT')


def _package(content):
    """Preflight every declaration, then bounded CRC/XML streaming; no extraction."""
    if not isinstance(content, bytes) or not 0 < len(content) <= 25 * 1024 * 1024: _reject()
    try:
        roots = {}
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 128: _reject()
            names = set()
            for member in members:
                name = member.filename
                if name in names or '\\' in name or ':' in name or '\x00' in name or name.startswith('/') or any(p in {'', '.', '..'} for p in name.rstrip('/').split('/')):
                    _reject()
                names.add(name)
                offset = member.header_offset
                if offset < 0 or offset + 30 > len(content) or content[offset:offset + 4] != b'PK\x03\x04': _reject()
                flags = int.from_bytes(content[offset + 6:offset + 8], 'little')
                if (flags | member.flag_bits) & 0x41: _reject('ENCRYPTED_SOURCE')
            if any(m.file_size > 100 * m.compress_size for m in members): _reject('COMPRESSION_BOMB')
            if sum(m.file_size for m in members) > _MAX_BYTES: _reject()
            total = 0
            for member in members:
                xml = member.filename.endswith(('.xml', '.rels'))
                parser = ET.XMLPullParser(events=('start', 'end')) if xml else None
                root, depth, scan = None, 0, b''
                with archive.open(member) as stream:
                    while chunk := stream.read(64 * 1024):
                        total += len(chunk)
                        if total > _MAX_BYTES: _reject()
                        if parser is None: continue
                        # NUL removal is only a security scan, covering UTF-16/32
                        # BOM/endianness; the XML parser receives original bytes.
                        check = scan + chunk.replace(b'\0', b'').upper()
                        if b'<!DOCTYPE' in check or b'<!ENTITY' in check: _reject()
                        scan = check[-32:]
                        parser.feed(chunk)
                        for event, node in parser.read_events():
                            if event == 'start':
                                depth += 1
                                if depth > 64: _reject()
                                if root is None: root = node
                            else: depth -= 1
                    if parser is not None:
                        parser.close()
                        # Expat may defer events for a large token until close.
                        for event, node in parser.read_events():
                            if event == 'start':
                                depth += 1
                                if depth > 64: _reject()
                                if root is None: root = node
                            else: depth -= 1
                        if root is None or depth != 0: _reject()
                        roots[member.filename] = root
            for name in names:
                if name.startswith(('word/media/', 'word/embeddings/', 'word/charts/')) or name in {'word/comments.xml', 'word/footnotes.xml', 'word/endnotes.xml'} or name.lower().endswith('.bin'):
                    _reject('UNSUPPORTED_FORMAT')
            for name, root in roots.items():
                if not name.endswith('.rels'): continue
                if root.tag != '{' + _REL + '}Relationships': _reject()
                ids = set()
                for rel in root:
                    if rel.tag != '{' + _REL + '}Relationship' or rel.get('Id') in ids or not rel.get('Id'): _reject()
                    ids.add(rel.get('Id'))
                    target = rel.get('Target', '')
                    if rel.get('TargetMode', 'Internal') != 'Internal' or not target or '\\' in target or ':' in target or target.startswith('/') or '?' in target or '#' in target:
                        _reject('UNSUPPORTED_FORMAT')
                    base = '' if name == '_rels/.rels' else posixpath.dirname(posixpath.dirname(name))
                    resolved = posixpath.normpath(posixpath.join(base, target))
                    if resolved.startswith('../') or resolved not in names: _reject()
            types = roots.get('[Content_Types].xml')
            if types is None or types.tag != '{' + _CT + '}Types': _reject()
            main_types = []
            for node in types:
                if node.tag not in {'{' + _CT + '}Default', '{' + _CT + '}Override'}: _reject()
                if 'macroEnabled' in node.get('ContentType', ''): _reject('UNSUPPORTED_FORMAT')
                if node.get('PartName') == '/word/document.xml': main_types.append(node.get('ContentType'))
            if main_types != [_MAIN]: _reject()
            document = roots.get('word/document.xml')
            if document is None or document.tag != '{' + _W + '}document': _reject()
            relationships = roots.get('_rels/.rels')
            if relationships is None: _reject()
            main_targets = [node.get('Target') for node in relationships
                if node.get('Type') == 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument']
            if main_targets != ['word/document.xml']: _reject()
            return roots
    except SourceRejected: raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ET.ParseError, EOFError, OSError,
            ValueError, RuntimeError, NotImplementedError, zlib.error):
        _reject()


def _styles(root):
    styles, defaults, default_ids = {}, [], {}
    if root is None: return styles, defaults, default_ids
    if _word(root) != 'styles': _reject()
    for child in root:
        kind = _word(child)
        if kind == 'docDefaults':
            seen = set()
            for wrapper in child:
                wrapper_kind = _word(wrapper)
                property_kind = {'rPrDefault': 'rPr', 'pPrDefault': 'pPr'}.get(wrapper_kind)
                if property_kind is None or wrapper_kind in seen or len(wrapper) > 1:
                    _reject('UNSUPPORTED_FORMAT')
                seen.add(wrapper_kind)
                for property_node in wrapper:
                    if _word(property_node) != property_kind: _reject('UNSUPPORTED_FORMAT')
                    _properties(property_node.iter())
                    _format(property_node)
            defaults.extend(child.iter())
        elif kind == 'style':
            key = child.get('{' + _W + '}styleId')
            if not key or key in styles: _reject()
            styles[key] = child
            if child.get('{' + _W + '}default') in {'1', 'true', 'on'}:
                style_type = child.get('{' + _W + '}type')
                # A numbering default is not an implicit paragraph/run style.
                # Any actual numbering connection (numPr) is still rejected.
                if style_type == 'numbering': continue
                if style_type not in {'paragraph', 'character', 'table'} or style_type in default_ids: _reject()
                default_ids[style_type] = key
        elif kind != 'latentStyles': _reject('UNSUPPORTED_FORMAT')
    return styles, defaults, default_ids


def _properties(nodes):
    outline = None
    for node in nodes:
        kind = _word(node)
        if kind == 'numPr': _reject('UNSUPPORTED_FORMAT')
        if kind in {'vanish', 'webHidden', 'specVanish'} and _val(node) not in {'0', 'false', 'off'}:
            _reject('UNSUPPORTED_FORMAT')
        if kind == 'outlineLvl':
            value = _val(node)
            if value not in {str(i) for i in range(10)}: _reject()
            outline = int(value)
    return outline


def read_docx(content: bytes) -> DocxDocument:
    roots = _package(content)
    document = roots['word/document.xml']
    if len(document) != 1 or _word(document[0]) != 'body': _reject('UNSUPPORTED_FORMAT')
    styles, defaults, default_ids = _styles(roots.get('word/styles.xml'))
    _properties(defaults)

    def style_properties(style_id, style_type='paragraph'):
        seen, chain = set(), []
        while style_id is not None:
            if style_id in seen or style_id not in styles: _reject()
            seen.add(style_id)
            node = styles[style_id]
            if node.get('{' + _W + '}type') != style_type: _reject()
            for child in node:
                kind = _word(child)
                if kind in {'pPr', 'rPr', 'tblPr', 'trPr', 'tcPr'}:
                    _properties(child.iter())
                    _format(child)
                elif kind == 'tblStylePr' and style_type == 'table':
                    for property_node in child:
                        _properties(property_node.iter())
                        _format(property_node)
                elif kind not in _STYLE_META: _reject('UNSUPPORTED_FORMAT')
            chain.append(node)
            refs = [child for child in node if _word(child) == 'basedOn']
            if len(refs) > 1: _reject()
            style_id = _val(refs[0]) if refs else None
        outline = _properties(defaults)
        for node in reversed(chain):
            value = _properties(node.iter())
            if value is not None: outline = value
        return outline

    # Character defaults are connected even without an explicit run rStyle.
    style_properties(default_ids.get('character'), 'character')
    blocks, cells = [], 0

    def paragraph(node, block_id, table=None):
        if _word(node) != 'p': _reject('UNSUPPORTED_FORMAT')
        properties = [child for child in node if _word(child) == 'pPr']
        if len(properties) > 1: _reject()
        style_id, direct_outline = None, None
        if properties:
            _properties(properties[0].iter())
            _format(properties[0])
            refs = properties[0].findall('{' + _W + '}pStyle')
            if len(refs) > 1: _reject()
            style_id = _val(refs[0]) if refs else None
            direct_outline = _properties(properties[0].iter())
        outline = style_properties(style_id or default_ids.get('paragraph'))
        if direct_outline is not None: outline = direct_outline
        texts = []
        for child in node:
            kind = _word(child)
            if kind == 'pPr': continue
            if kind != 'r': _reject('UNSUPPORTED_FORMAT')
            for item in child:
                kind = _word(item)
                if kind == 'rPr':
                    _properties(item.iter())
                    _format(item)
                    refs = item.findall('{' + _W + '}rStyle')
                    if len(refs) > 1: _reject()
                    if refs: style_properties(_val(refs[0]), 'character')
                elif kind in {'t', 'tab', 'br', 'cr'}:
                    if len(item): _reject('UNSUPPORTED_FORMAT')
                    texts.append(item.text or '' if kind == 't' else '\t' if kind == 'tab' else '\n')
                else: _reject('UNSUPPORTED_FORMAT')
        text = ''.join(texts)
        if len(text) > 2000: _reject()
        blocks.append({'block_id': block_id, 'ordinal': len(blocks) + 1,
            'block_type': 'paragraph' if table is None else 'table_cell_paragraph', 'text': text,
            'style_id': style_id, 'outline_level': outline, 'table': table})
        if len(blocks) > 128 or sum(bool(b['text']) for b in blocks) > 32: _reject()

    body_index = 0
    for child in document[0]:
        kind = _word(child)
        if kind == 'sectPr':
            _format(child)
            continue
        body_index += 1
        if body_index > 64: _reject()
        if kind == 'p': paragraph(child, f'p:{body_index}')
        elif kind == 'tbl':
            style_properties(default_ids.get('table'), 'table')
            rows = []
            for item in child:
                kind = _word(item)
                if kind in {'tblPr', 'tblGrid'}:
                    _format(item)
                    if kind == 'tblPr':
                        refs = item.findall('{' + _W + '}tblStyle')
                        if len(refs) > 1: _reject()
                        if refs: style_properties(_val(refs[0]), 'table')
                elif kind == 'tr': rows.append(item)
                else: _reject('UNSUPPORTED_FORMAT')
            if not rows: _reject()
            columns = None
            for row_index, row in enumerate(rows, 1):
                row_cells = []
                for item in row:
                    kind = _word(item)
                    if kind == 'trPr': _format(item)
                    elif kind == 'tc': row_cells.append(item)
                    else: _reject('UNSUPPORTED_FORMAT')
                if not row_cells or (columns is not None and columns != len(row_cells)): _reject('UNSUPPORTED_FORMAT')
                columns = len(row_cells)
                cells += columns
                if cells > 64: _reject()
                for column, cell in enumerate(row_cells, 1):
                    paras = []
                    for item in cell:
                        kind = _word(item)
                        if kind == 'tcPr': _format(item)
                        elif kind == 'p': paras.append(item)
                        else: _reject('UNSUPPORTED_FORMAT')
                    if not paras: _reject()
                    for para_index, item in enumerate(paras, 1):
                        paragraph(item, f't:{body_index}:r:{row_index}:c:{column}:p:{para_index}',
                            {'body_index': body_index, 'row': row_index, 'column': column, 'paragraph': para_index})
        else: _reject('UNSUPPORTED_FORMAT')
    for name, root in roots.items():
        if name.startswith(('word/header', 'word/footer')):
            if _word(root) not in {'hdr', 'ftr'}: _reject()
            for node in root.iter():
                kind = _word(node)
                if kind not in _FORMAT | {'hdr', 'ftr', 'p', 'r', 't'} or (kind == 't' and node.text):
                    _reject('UNSUPPORTED_FORMAT')
    digest = hashlib.sha256(content).hexdigest()
    native = _dump({'serialization_version': _VERSION, 'source_sha256': digest, 'blocks': blocks})
    if len(native.encode()) > _MAX_JSON: _reject()
    return DocxDocument(digest, tuple(blocks), native)


def encode_docx_block(block_id: str, start: int, end: int) -> str:
    if not isinstance(block_id, str) or not _ID.fullmatch(block_id): _reject()
    numbers = list(map(int, re.findall(r'[0-9]+', block_id)))
    if numbers[0] > 64 or (len(numbers) == 4 and (max(numbers[1:3]) > 64 or numbers[3] > 128)): _reject()
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= 2000: _reject()
    return _dump({'block': block_id, 'start': start, 'end': end})


def decode_docx_block(value: str) -> tuple[str, int, int]:
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result: _reject()
            result[key] = item
        return result
    try:
        if not isinstance(value, str) or len(value.encode()) > 256: _reject()
        item = json.loads(value, object_pairs_hook=unique)
        if not isinstance(item, dict) or list(item) != ['block', 'start', 'end']: _reject()
        if encode_docx_block(item['block'], item['start'], item['end']) != value: _reject()
        return item['block'], item['start'], item['end']
    except SourceRejected: raise
    except (ValueError, TypeError, UnicodeError): _reject()


def read_docx_block_view(document: DocxDocument, locator_value: str) -> dict[str, object]:
    block_id, start, end = decode_docx_block(locator_value)
    matches = [i for i, b in enumerate(document.blocks) if b['block_id'] == block_id]
    if len(matches) != 1: _reject()
    index = matches[0]
    if end > len(document.blocks[index]['text']): _reject()
    view = {'source_sha256': document.source_sha256, 'serialization_version': _VERSION,
        'selected_block_id': block_id, 'selected_start': start, 'selected_end': end,
        'blocks': list(document.blocks[max(0, index - 1):index + 2])}
    if len(_dump(view).encode()) > _MAX_JSON: _reject()
    return view
