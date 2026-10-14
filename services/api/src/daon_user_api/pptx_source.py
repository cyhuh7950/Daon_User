"""Bounded Transitional PPTX structure. No rendering, extraction or semantics."""
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

_P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
_A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
_VERSION = 'pptx-structure-v1'
_MAX_BYTES = 32 * 1024 * 1024
_MAX_JSON = 32 * 1024
_ID = re.compile(r's:([1-9][0-9]*):(?:sp:([1-9][0-9]*):p:([1-9][0-9]*)|tb:([1-9][0-9]*):r:([1-9][0-9]*):c:([1-9][0-9]*):p:([1-9][0-9]*))\Z')
_PARTS = (
    (r'ppt/presentation\.xml', 'presentation', _P, 'presentation'),
    (r'ppt/slides/slide[1-9][0-9]*\.xml', 'slide', _P, 'sld'),
    (r'ppt/slideLayouts/slideLayout[1-9][0-9]*\.xml', 'slideLayout', _P, 'sldLayout'),
    (r'ppt/slideMasters/slideMaster[1-9][0-9]*\.xml', 'slideMaster', _P, 'sldMaster'),
    (r'ppt/notesSlides/notesSlide[1-9][0-9]*\.xml', 'notesSlide', _P, 'notes'),
    (r'ppt/notesMasters/notesMaster[1-9][0-9]*\.xml', 'notesMaster', _P, 'notesMaster'),
    (r'ppt/handoutMasters/handoutMaster[1-9][0-9]*\.xml', 'handoutMaster', _P, 'handoutMaster'),
    (r'ppt/theme/theme[1-9][0-9]*\.xml', 'theme', _A, 'theme'),
    (r'ppt/presProps\.xml', 'presProps', _P, 'presentationPr'),
    (r'ppt/viewProps\.xml', 'viewProps', _P, 'viewPr'),
    (r'ppt/tableStyles\.xml', 'tableStyles', _A, 'tblStyleLst'),
    (r'ppt/printerSettings/printerSettings[1-9][0-9]*\.bin', 'printerSettings', None, None),
    (r'docProps/core\.xml', 'core-properties', 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties', 'coreProperties'),
    (r'docProps/app\.xml', 'extended-properties', 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties', 'Properties'),
    (r'docProps/thumbnail\.(?:jpeg|png)', 'thumbnail', None, None),
)
_P_TAGS = frozenset(('presentation sld sldLayout sldMaster notes notesMaster handoutMaster '
    'sldIdLst sldId sldMasterIdLst sldMasterId notesMasterIdLst notesMasterId '
    'handoutMasterIdLst handoutMasterId sldLayoutIdLst sldLayoutId sldSz notesSz '
    'defaultTextStyle cSld spTree nvGrpSpPr cNvPr cNvGrpSpPr nvPr grpSpPr '
    'sp nvSpPr cNvSpPr spPr txBody ph graphicFrame nvGraphicFramePr cNvGraphicFramePr '
    'xfrm style lnRef fillRef effectRef fontRef clrMap clrMapOvr bg bgPr bgRef '
    'txStyles titleStyle bodyStyle otherStyle notesStyle hf extLst ext '
    'presentationPr viewPr normalViewPr restoredLeft restoredTop slideViewPr cSldViewPr '
    'cViewPr scale sx sy origin guideLst guide gridSpacing notesTextViewPr sorterViewPr '
    'outlineViewPr outlineViewSlideLst outlineViewSlide notesViewPr').split())
_A_TAGS = frozenset(('txBody spPr style sx sy bodyPr lstStyle p pPr r rPr t br tab endParaRPr defRPr defPPr '
    'normAutofit noAutofit spAutoFit xfrm off ext chOff chExt prstGeom avLst gd '
    'custGeom gdLst ahLst ahXY ahPolar cxnLst cxn pos rect pathLst path moveTo '
    'lnTo arcTo quadBezTo cubicBezTo close pt ln noFill solidFill gradFill gsLst gs '
    'lin path fillToRect tile flip miter round bevel prstDash custDash ds headEnd tailEnd '
    'schemeClr srgbClr sysClr scrgbClr hslClr prstClr tint shade alpha alphaOff alphaMod '
    'hue hueOff hueMod sat satOff satMod lum lumOff lumMod comp inv gray gamma invGamma '
    'effectLst effectDag outerShdw innerShdw glow softEdge reflection blur duotone '
    'scene3d camera lightRig rot backdrop anchor norm up sp3d bevelT bevelB extrusionClr contourClr '
    'spLocks graphicFrameLocks grpSpLocks graphic graphicData tbl tblPr tblGrid gridCol '
    'tr tc tcPr tableStyleId lnL lnR lnT lnB lnTlToBr lnBlToTr cell3D '
    'masterClrMapping overrideClrMapping latin ea cs sym hlinkClick hlinkMouseOver '
    'tabLst spcBef spcAft lnSpc spcPts spcPct buNone buChar buAutoNum buFont buSzPct '
    'buSzPts buSzTx buClr buClrTx buFontTx fld '
    'theme themeElements clrScheme dk1 lt1 dk2 lt2 accent1 accent2 accent3 accent4 accent5 accent6 '
    'hlink folHlink fontScheme majorFont minorFont font fmtScheme fillStyleLst lnStyleLst '
    'effectStyleLst effectStyle bgFillStyleLst objectDefaults spDef lnDef txDef extraClrSchemeLst '
    'extraClrScheme clrMap custClrLst custClr tblStyleLst tblStyle tblBg wholeTbl band1H band2H '
    'band1V band2V lastCol firstCol lastRow firstRow seCell swCell neCell nwCell tcStyle tcTxStyle '
    'tcBdr left right top bottom insideH insideV tl2br tr2bl fill fillRef lnRef effectRef fontRef '
    'extLst ext').split()) | frozenset(f'lvl{n}pPr' for n in range(1, 10))
_BULLETS = frozenset(('buChar', 'buAutoNum'))
_TEXT_PARENTS = {
    f'{{{_P}}}cSld': {f'{{{_P}}}{tag}' for tag in ('sld', 'sldLayout', 'sldMaster', 'notes', 'notesMaster', 'handoutMaster')},
    f'{{{_P}}}nvSpPr': {f'{{{_P}}}sp'},
    f'{{{_P}}}nvGraphicFramePr': {f'{{{_P}}}graphicFrame'},
    f'{{{_P}}}nvGrpSpPr': {f'{{{_P}}}spTree'},
    f'{{{_P}}}grpSpPr': {f'{{{_P}}}spTree'},
    f'{{{_P}}}nvPr': {f'{{{_P}}}{tag}' for tag in ('nvSpPr', 'nvGraphicFramePr', 'nvGrpSpPr')},
    f'{{{_P}}}spPr': {f'{{{_P}}}sp'},
    f'{{{_P}}}txStyles': {f'{{{_P}}}sldMaster'},
    f'{{{_P}}}notesStyle': {f'{{{_P}}}notesMaster'},
    f'{{{_P}}}titleStyle': {f'{{{_P}}}txStyles'},
    f'{{{_P}}}bodyStyle': {f'{{{_P}}}txStyles'},
    f'{{{_P}}}otherStyle': {f'{{{_P}}}txStyles'},
    f'{{{_P}}}defaultTextStyle': {f'{{{_P}}}presentation'},
    f'{{{_P}}}sldIdLst': {f'{{{_P}}}presentation'},
    f'{{{_P}}}sldId': {f'{{{_P}}}sldIdLst'},
    f'{{{_P}}}sldLayoutIdLst': {f'{{{_P}}}sldMaster'},
    f'{{{_P}}}sldLayoutId': {f'{{{_P}}}sldLayoutIdLst'},
    f'{{{_A}}}t': {f'{{{_A}}}r', f'{{{_A}}}fld'},
    f'{{{_A}}}p': {f'{{{_P}}}txBody', f'{{{_A}}}txBody'},
    f'{{{_A}}}bodyPr': {f'{{{_P}}}txBody', f'{{{_A}}}txBody', f'{{{_A}}}spDef', f'{{{_A}}}txDef', f'{{{_A}}}lnDef'},
    f'{{{_A}}}lstStyle': {f'{{{_P}}}txBody', f'{{{_A}}}txBody', f'{{{_A}}}spDef', f'{{{_A}}}txDef', f'{{{_A}}}lnDef'},
    f'{{{_A}}}r': {f'{{{_A}}}p'},
    f'{{{_A}}}br': {f'{{{_A}}}p'},
    f'{{{_A}}}tab': {f'{{{_A}}}p', f'{{{_A}}}tabLst'},
    f'{{{_A}}}fld': {f'{{{_A}}}p'},
    f'{{{_A}}}pPr': {f'{{{_A}}}p'},
    f'{{{_A}}}rPr': {f'{{{_A}}}r', f'{{{_A}}}fld', f'{{{_A}}}br'},
    f'{{{_A}}}endParaRPr': {f'{{{_A}}}p'},
    f'{{{_P}}}txBody': {f'{{{_P}}}sp'},
    f'{{{_A}}}txBody': {f'{{{_A}}}tc'},
    f'{{{_P}}}sp': {f'{{{_P}}}spTree'},
    f'{{{_P}}}graphicFrame': {f'{{{_P}}}spTree'},
    f'{{{_P}}}spTree': {f'{{{_P}}}cSld'},
    f'{{{_P}}}ph': {f'{{{_P}}}nvPr'},
    f'{{{_P}}}cNvPr': {f'{{{_P}}}nvSpPr', f'{{{_P}}}nvGraphicFramePr', f'{{{_P}}}nvGrpSpPr'},
    f'{{{_A}}}graphic': {f'{{{_P}}}graphicFrame'},
    f'{{{_A}}}graphicData': {f'{{{_A}}}graphic'},
    f'{{{_A}}}tbl': {f'{{{_A}}}graphicData'},
    f'{{{_A}}}tr': {f'{{{_A}}}tbl'},
    f'{{{_A}}}tc': {f'{{{_A}}}tr'},
}
_P14 = 'http://schemas.microsoft.com/office/powerpoint/2010/main'
_EXTENSIONS = {
    '{BB962C8B-B14F-4D97-AF65-F5344CB8AC3E}': ('creationId', {f'{{{_P}}}cSld'}),
    '{E76CE94A-603C-4142-B9EB-6D1370010A27}': ('discardImageEditData', {f'{{{_P}}}presentationPr'}),
    '{D31A062A-798A-4329-ABDD-BBA856620510}': ('defaultImageDpi', {f'{{{_P}}}presentationPr'}),
}
_REL_TARGETS = {
    'root': {'presentation', 'core-properties', 'extended-properties', 'thumbnail'},
    'presentation': {'slide', 'slideMaster', 'notesMaster', 'handoutMaster', 'theme', 'presProps', 'viewProps', 'tableStyles', 'printerSettings'},
    'slide': {'slideLayout', 'notesSlide'},
    'slideLayout': {'slideMaster'},
    'slideMaster': {'slideLayout', 'theme'},
    'notesSlide': {'slide', 'notesMaster'},
    'notesMaster': {'theme'},
    'handoutMaster': {'theme'},
}


class _FrozenDict(dict):
    def _readonly(self, *args, **kwargs):
        raise TypeError('immutable document')
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly


def _freeze(value):
    if isinstance(value, dict): return _FrozenDict({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)): return tuple(_freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class PptxDocument:
    source_sha256: str
    blocks: tuple[dict[str, object], ...]
    native_json: str

    def __post_init__(self):
        object.__setattr__(self, 'blocks', _freeze(self.blocks))


def _reject(code='CORRUPTED_SOURCE'):
    raise SourceRejected(code)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _part(name):
    for pattern, kind, namespace, root in _PARTS:
        if re.fullmatch(pattern, name): return kind, namespace, root
    _reject('UNSUPPORTED_FORMAT')


def _rels_name(name):
    return posixpath.join(posixpath.dirname(name), '_rels', posixpath.basename(name) + '.rels')


def _content_type(kind):
    special = {
        'theme': 'application/vnd.openxmlformats-officedocument.theme+xml',
        'tableStyles': 'application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml',
        'core-properties': 'application/vnd.openxmlformats-package.core-properties+xml',
        'extended-properties': 'application/vnd.openxmlformats-officedocument.extended-properties+xml',
        'printerSettings': 'application/vnd.openxmlformats-officedocument.presentationml.printerSettings',
    }
    return special.get(kind, 'application/vnd.openxmlformats-officedocument.presentationml.' + ('presentation.main' if kind == 'presentation' else kind) + '+xml')


def _package(content):
    if not isinstance(content, bytes) or not 0 < len(content) <= 25 * 1024 * 1024: _reject()
    try:
        roots, names = {}, set()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if not 1 <= len(members) <= 256: _reject()
            for member in members:
                name = member.filename
                if name in names or '\\' in name or ':' in name or '\0' in name or name.startswith('/') or any(s in {'', '.', '..'} for s in name.split('/')): _reject()
                names.add(name)
                offset = member.header_offset
                if offset < 0 or offset + 30 > len(content) or content[offset:offset + 4] != b'PK\x03\x04': _reject()
                if (member.flag_bits | int.from_bytes(content[offset + 6:offset + 8], 'little')) & 0x41: _reject('ENCRYPTED_SOURCE')
            if any(m.file_size > 100 * m.compress_size for m in members): _reject('COMPRESSION_BOMB')
            if sum(m.file_size for m in members) > _MAX_BYTES: _reject()
            total = 0
            for member in members:
                parser = ET.XMLPullParser(events=('start', 'end')) if member.filename.endswith(('.xml', '.rels')) else None
                root, depth, scan = None, 0, b''
                with archive.open(member) as stream:
                    while chunk := stream.read(64 * 1024):
                        total += len(chunk)
                        if total > _MAX_BYTES: _reject()
                        if parser is None: continue
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
                        for event, node in parser.read_events():
                            if event == 'start':
                                depth += 1
                                if depth > 64: _reject()
                                if root is None: root = node
                            else: depth -= 1
                        if root is None or depth: _reject()
                        roots[member.filename] = root
        for name in names:
            if name == '[Content_Types].xml' or name == '_rels/.rels': continue
            if name.endswith('.rels'):
                match = re.fullmatch(r'(.*)/_rels/([^/]+)\.rels', name)
                if match is None or f'{match[1]}/{match[2]}' not in names: _reject()
            else:
                _, namespace, root = _part(name)
                if namespace and roots[name].tag != f'{{{namespace}}}{root}': _reject()
        types = roots.get('[Content_Types].xml')
        if types is None or types.tag != f'{{{_CT}}}Types': _reject()
        defaults, overrides = {}, {}
        for item in types:
            if item.tag == f'{{{_CT}}}Default':
                key = item.get('Extension'); target = defaults
            elif item.tag == f'{{{_CT}}}Override':
                key = item.get('PartName'); target = overrides
                if not key or not key.startswith('/') or key[1:] not in names: _reject()
            else: _reject()
            if not key or key in target or not item.get('ContentType'): _reject()
            target[key] = item.get('ContentType')
        for name in names - {'[Content_Types].xml'}:
            declared = overrides.get('/' + name, defaults.get(name.rsplit('.', 1)[-1]))
            if name.endswith('.rels'):
                expected = 'application/vnd.openxmlformats-package.relationships+xml'
            else:
                kind, _, _ = _part(name)
                expected = ('image/jpeg' if name.endswith('.jpeg') else 'image/png') if kind == 'thumbnail' else _content_type(kind)
            if declared != expected: _reject('UNSUPPORTED_FORMAT')
        relations = {}
        for name, root in roots.items():
            if not name.endswith('.rels'): continue
            if root.tag != f'{{{_REL}}}Relationships': _reject()
            records = {}
            base = '' if name == '_rels/.rels' else posixpath.dirname(posixpath.dirname(name))
            owner = 'root' if name == '_rels/.rels' else _part(posixpath.join(base, posixpath.basename(name)[:-5]))[0]
            targets = set()
            for item in root:
                key, target, rel_type = item.get('Id'), item.get('Target', ''), item.get('Type', '')
                if item.tag != f'{{{_REL}}}Relationship' or not key or key in records: _reject()
                if item.get('TargetMode', 'Internal') != 'Internal' or not target or any(c in target for c in '\\:?#\0') or target.startswith('/'): _reject('UNSUPPORTED_FORMAT')
                resolved = posixpath.normpath(posixpath.join(base, target))
                if resolved.startswith('../') or resolved not in names: _reject()
                kind, _, _ = _part(resolved)
                if kind not in _REL_TARGETS.get(owner, set()) or resolved in targets: _reject('UNSUPPORTED_FORMAT')
                targets.add(resolved)
                allowed_type = {
                    'presentation': _R + '/officeDocument',
                    'core-properties': 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties',
                    'thumbnail': 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail',
                }.get(kind, _R + '/' + kind)
                if rel_type != allowed_type: _reject('UNSUPPORTED_FORMAT')
                records[key] = (kind, resolved)
            relations[name] = records
        mains = [path for kind, path in relations.get('_rels/.rels', {}).values() if kind == 'presentation']
        if mains != ['ppt/presentation.xml']: _reject()
        return roots, relations
    except SourceRejected: raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ET.ParseError, EOFError, OSError, ValueError, RuntimeError, NotImplementedError, zlib.error):
        _reject()


def _kind(node, namespace):
    if not isinstance(node.tag, str) or not node.tag.startswith('{' + namespace + '}'): _reject('UNSUPPORTED_FORMAT')
    return node.tag[len(namespace) + 2:]


def _children(node, namespace, allowed):
    for child in node:
        if _kind(child, namespace) not in allowed: _reject('UNSUPPORTED_FORMAT')


def _tree(root, *, template=False):
    """Explicit syntax vocabulary. Content is read only at exact parent positions."""
    parents = {child: parent for parent in root.iter() for child in parent}
    for node in root.iter():
        parent = parents.get(node)
        expected = _TEXT_PARENTS.get(node.tag)
        if expected is not None and (parent is None or parent.tag not in expected): _reject('UNSUPPORTED_FORMAT')
        if node.tag.startswith('{' + _P + '}'):
            kind = _kind(node, _P)
            if kind not in _P_TAGS: _reject('UNSUPPORTED_FORMAT')
        elif node.tag.startswith('{' + _A + '}'):
            kind = _kind(node, _A)
            if kind not in _A_TAGS: _reject('UNSUPPORTED_FORMAT')
            if not template and kind in _BULLETS | {'fld', 'hlinkClick', 'hlinkMouseOver'}: _reject('UNSUPPORTED_FORMAT')
        elif node.tag.startswith('{' + _P14 + '}'):
            kind = _kind(node, _P14)
            if parent is None or parent.tag != f'{{{_P}}}ext': _reject('UNSUPPORTED_FORMAT')
            rule = _EXTENSIONS.get(parent.get('uri'))
            if rule is None or kind != rule[0] or len(node): _reject('UNSUPPORTED_FORMAT')
        else: _reject('UNSUPPORTED_FORMAT')
        if node.tag == f'{{{_P}}}ext':
            rule = _EXTENSIONS.get(node.get('uri'))
            wrapper = parents.get(parent)
            if rule is None or len(node) != 1 or parent is None or parent.tag != f'{{{_P}}}extLst' or wrapper is None or wrapper.tag not in rule[1]: _reject('UNSUPPORTED_FORMAT')
        if node.tag == f'{{{_A}}}ext' and (parent is None or parent.tag not in {f'{{{_A}}}xfrm', f'{{{_P}}}xfrm'}): _reject('UNSUPPORTED_FORMAT')
        if node.tag == f'{{{_A}}}extLst' and len(node): _reject('UNSUPPORTED_FORMAT')
        if node.get('hidden') not in (None, '0', 'false') or node.get('vanish') not in (None, '0', 'false'): _reject('UNSUPPORTED_FORMAT')
        if kind != 't' and node.text and node.text.strip():
            if kind != 'tableStyleId': _reject('UNSUPPORTED_FORMAT')
        if node.tail and node.tail.strip(): _reject()


def _one(node, tag, *, required=True):
    found = node.findall(tag)
    if len(found) > 1 or (required and not found): _reject()
    return found[0] if found else None


def _paragraphs(body):
    _children(body, _A, {'bodyPr', 'lstStyle', 'p'})
    _one(body, f'{{{_A}}}bodyPr')
    _one(body, f'{{{_A}}}lstStyle', required=False)
    result = []
    for para in body.findall(f'{{{_A}}}p'):
        _children(para, _A, {'pPr', 'r', 'br', 'tab', 'endParaRPr'})
        _one(para, f'{{{_A}}}pPr', required=False)
        text = []
        for child in para:
            kind = _kind(child, _A)
            if kind == 'r':
                _children(child, _A, {'rPr', 't'})
                value = _one(child, f'{{{_A}}}t')
                if len(value): _reject('UNSUPPORTED_FORMAT')
                text.append(value.text or '')
            elif kind == 'br':
                _children(child, _A, {'rPr'}); text.append('\n')
            elif kind == 'tab':
                if len(child): _reject()
                text.append('\t')
        value = ''.join(text)
        if len(value) > 2000: _reject()
        result.append(value)
    return result


def _object_id(obj):
    wrapper = _one(obj, f'{{{_P}}}' + ('nvSpPr' if obj.tag == f'{{{_P}}}sp' else 'nvGraphicFramePr'))
    _children(wrapper, _P, {'cNvPr', 'cNvSpPr', 'cNvGraphicFramePr', 'nvPr'})
    _one(wrapper, f'{{{_P}}}' + ('cNvSpPr' if obj.tag == f'{{{_P}}}sp' else 'cNvGraphicFramePr'))
    nonvisual = _one(wrapper, f'{{{_P}}}nvPr')
    _children(nonvisual, _P, {'ph'})
    props = _one(wrapper, f'{{{_P}}}cNvPr')
    raw = props.get('id', '')
    if not re.fullmatch('[1-9][0-9]{0,9}', raw) or int(raw) > 4294967295: _reject()
    return int(raw)


def _placeholder(obj):
    found = obj.findall(f'{{{_P}}}nvSpPr/{{{_P}}}nvPr/{{{_P}}}ph')
    if len(found) > 1: _reject()
    return found[0] if found else None


def _sp_tree(root):
    common = _one(root, f'{{{_P}}}cSld')
    _children(common, _P, {'spTree', 'bg', 'extLst'})
    tree = _one(common, f'{{{_P}}}spTree')
    _children(tree, _P, {'nvGrpSpPr', 'grpSpPr', 'sp', 'graphicFrame'})
    return tree


def _linked(roots, relations, name, kind):
    matches = [target for typ, target in relations.get(_rels_name(name), {}).values() if typ == kind]
    if len(matches) != 1: _reject()
    return matches[0], roots[matches[0]]


def _template(root, *, visible=True):
    _tree(root, template=True)
    _children(root, _P, {'cSld', 'clrMap', 'clrMapOvr', 'sldLayoutIdLst', 'txStyles', 'notesStyle', 'hf', 'extLst'})
    ids = set()
    for obj in _sp_tree(root):
        if obj.tag in {f'{{{_P}}}nvGrpSpPr', f'{{{_P}}}grpSpPr'}: continue
        if obj.tag != f'{{{_P}}}sp': _reject('UNSUPPORTED_FORMAT')
        ident = _object_id(obj)
        if ident in ids: _reject()
        ids.add(ident)
        ph = _placeholder(obj)
        if ph is None and visible and any((n.text or '') for n in obj.findall(f'.//{{{_A}}}t')): _reject('UNSUPPORTED_FORMAT')
        if ph is not None:
            # dt/ftr/sldNum fields are inactive without an explicit hf switch.
            if ph.get('type') in {'dt', 'ftr', 'sldNum'}:
                hf = root.find(f'{{{_P}}}hf')
                flag = {'dt': 'dt', 'ftr': 'ftr', 'sldNum': 'sldNum'}[ph.get('type')]
                if hf is not None and hf.get(flag) in {'1', 'true'}: _reject('UNSUPPORTED_FORMAT')


def _inherited_bullets(obj, layout, master, presentation):
    body = obj.find(f'{{{_P}}}txBody')
    if body is None: return
    ph = _placeholder(obj)
    ph_type = ph.get('type', 'body') if ph is not None else 'other'
    candidates = []
    if ph is not None:
        for root in (layout, master):
            for candidate in _sp_tree(root).findall(f'{{{_P}}}sp'):
                other = _placeholder(candidate)
                if other is not None and (other.get('idx', '0') == ph.get('idx', '0') or other.get('type', 'body') == ph_type):
                    candidates.append(candidate.find(f'{{{_P}}}txBody'))
    style = 'titleStyle' if ph_type in {'title', 'ctrTitle'} else 'bodyStyle' if ph is not None else 'otherStyle'
    candidates += [master.find(f'{{{_P}}}txStyles/{{{_P}}}{style}'), presentation.find(f'{{{_P}}}defaultTextStyle')]
    for para in body.findall(f'{{{_A}}}p'):
        local = para.find(f'{{{_A}}}pPr')
        if local is not None and local.find(f'{{{_A}}}buNone') is not None: continue
        raw = local.get('lvl', '0') if local is not None else '0'
        if not re.fullmatch('[0-8]', raw): _reject()
        level = f'{{{_A}}}lvl{int(raw) + 1}pPr'
        props = [body.find(f'{{{_A}}}lstStyle/{level}')]
        props.append(body.find(f'{{{_A}}}lstStyle/{{{_A}}}defPPr'))
        for candidate in candidates:
            if candidate is None: continue
            props.append(candidate.find(level))
            props.append(candidate.find(f'{{{_A}}}defPPr'))
            props.append(candidate.find(f'{{{_A}}}lstStyle/{level}'))
            # A matching placeholder's first paragraph can supply its default.
            props.append(candidate.find(f'{{{_A}}}p/{{{_A}}}pPr'))
        for prop in props:
            if prop is None: continue
            if prop.find(f'{{{_A}}}buNone') is not None: break
            if any(prop.find(f'{{{_A}}}{tag}') is not None for tag in _BULLETS): _reject('UNSUPPORTED_FORMAT')


def _metadata(root, *, core):
    """Normal package properties are non-source metadata, never model input."""
    cp = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
    ep = 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'
    dc = 'http://purl.org/dc/elements/1.1/'
    dt = 'http://purl.org/dc/terms/'
    vt = 'http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'
    if core:
        allowed = {f'{{{cp}}}{key}' for key in ('coreProperties', 'keywords', 'lastModifiedBy', 'revision', 'lastPrinted', 'category', 'contentStatus', 'version')}
        allowed |= {f'{{{dc}}}{key}' for key in ('title', 'subject', 'creator', 'description', 'identifier', 'language')}
        allowed |= {f'{{{dt}}}{key}' for key in ('created', 'modified')}
    else:
        allowed = {f'{{{ep}}}{key}' for key in ('Properties', 'Template', 'Manager', 'Company', 'Pages', 'Words', 'Characters', 'PresentationFormat', 'Lines', 'Paragraphs', 'Slides', 'Notes', 'TotalTime', 'HiddenSlides', 'MMClips', 'ScaleCrop', 'HeadingPairs', 'TitlesOfParts', 'LinksUpToDate', 'CharactersWithSpaces', 'SharedDoc', 'HyperlinkBase', 'HLinks', 'HyperlinksChanged', 'DigSig', 'Application', 'AppVersion', 'DocSecurity')}
        allowed |= {f'{{{vt}}}{key}' for key in ('vector', 'variant', 'lpstr', 'lpwstr', 'i4', 'ui4', 'bool')}
    for node in root.iter():
        if node.tag not in allowed: _reject('UNSUPPORTED_FORMAT')
        if node is not root and core and len(node): _reject('UNSUPPORTED_FORMAT')


def read_pptx(content: bytes) -> PptxDocument:
    roots, relations = _package(content)
    presentation = roots['ppt/presentation.xml']
    _tree(presentation, template=True)
    _children(presentation, _P, {'sldIdLst', 'sldMasterIdLst', 'notesMasterIdLst', 'handoutMasterIdLst', 'sldSz', 'notesSz', 'defaultTextStyle', 'extLst'})
    listing = _one(presentation, f'{{{_P}}}sldIdLst')
    _children(listing, _P, {'sldId'})
    if not 1 <= len(listing) <= 16: _reject()
    selected, slide_ids, rel_ids = [], set(), set()
    mapping = relations.get('ppt/_rels/presentation.xml.rels', {})
    for node in listing:
        raw, key = node.get('id', ''), node.get(f'{{{_R}}}id')
        if not re.fullmatch('[1-9][0-9]{0,9}', raw) or int(raw) > 4294967295 or raw in slide_ids or key in rel_ids or mapping.get(key, (None,))[0] != 'slide': _reject()
        slide_ids.add(raw); rel_ids.add(key); selected.append(mapping[key][1])
    actual = {name for name in roots if re.fullmatch(r'ppt/slides/slide[1-9][0-9]*\.xml', name)}
    if set(selected) != actual or len(set(selected)) != len(selected): _reject()
    # Even unselected ordinary template parts must use known syntax, but their
    # guide strings/latent numbering are not actual slide facts.
    for name, root in roots.items():
        if name in {'docProps/core.xml', 'docProps/app.xml'}: _metadata(root, core=name.endswith('core.xml'))
        if re.fullmatch(r'ppt/(?:slideLayouts/slideLayout|slideMasters/slideMaster)[1-9][0-9]*\.xml', name): _tree(root, template=True)
        if name.startswith(('ppt/notesSlides/', 'ppt/notesMasters/', 'ppt/handoutMasters/')) and name.endswith('.xml'):
            _tree(root, template=True)
            if name.startswith('ppt/notesSlides/'):
                for obj in _sp_tree(root).findall(f'{{{_P}}}sp'):
                    ph = _placeholder(obj)
                    if ph is None or ph.get('type') == 'body':
                        if any((n.text or '') for n in obj.findall(f'.//{{{_A}}}t')): _reject('UNSUPPORTED_FORMAT')
                    else:
                        # Nonempty user runs remain unsupported even when labelled
                        # as a date/slide-number placeholder. Only the built-in
                        # template field is non-source metadata.
                        allowed = {'dt': 'datetimeFigureOut', 'sldNum': 'slidenum'}.get(ph.get('type'))
                        for para in obj.findall(f'{{{_P}}}txBody/{{{_A}}}p'):
                            for child in para:
                                if child.tag == f'{{{_A}}}fld':
                                    if allowed is None or child.get('type') != allowed: _reject('UNSUPPORTED_FORMAT')
                                elif any((n.text or '') for n in child.iter(f'{{{_A}}}t')): _reject('UNSUPPORTED_FORMAT')
            elif name.startswith('ppt/notesMasters/'):
                _template(root)
            elif any((n.text or '') for n in root.findall(f'.//{{{_A}}}t')): _reject('UNSUPPORTED_FORMAT')
        if (name.startswith('ppt/theme/') or name in {'ppt/presProps.xml', 'ppt/viewProps.xml', 'ppt/tableStyles.xml'}) and name.endswith('.xml'):
            _tree(root, template=True)
            source_nodes = {f'{{{_P}}}{tag}' for tag in ('sp', 'graphicFrame', 'txBody')}
            source_nodes |= {f'{{{_A}}}{tag}' for tag in ('txBody', 'p', 'r', 't', 'fld', 'tbl', 'tr', 'tc')}
            if any(node.tag in source_nodes for node in root.iter()): _reject('UNSUPPORTED_FORMAT')
    blocks, objects, cells = [], 0, 0

    def append(text, ident, slide_no, object_id, table=None):
        blocks.append({'block_id': ident, 'ordinal': len(blocks) + 1, 'slide': slide_no, 'object_id': object_id,
                       'block_type': 'table_cell_paragraph' if table is not None else 'shape_paragraph', 'text': text, 'table': table})
        if len(blocks) > 128 or sum(bool(b['text']) for b in blocks) > 32: _reject()

    for slide_no, name in enumerate(selected, 1):
        root = roots[name]
        if root.get('show') not in (None, '1', 'true'): _reject('UNSUPPORTED_FORMAT')
        _tree(root)
        _children(root, _P, {'cSld', 'clrMapOvr', 'extLst'})
        layout_name, layout = _linked(roots, relations, name, 'slideLayout')
        master_name, master = _linked(roots, relations, layout_name, 'slideMaster')
        _linked(roots, relations, master_name, 'theme')
        _template(layout)
        _template(master, visible=root.get('showMasterSp') not in {'0', 'false'})
        ids = set()
        for obj in _sp_tree(root):
            if obj.tag in {f'{{{_P}}}nvGrpSpPr', f'{{{_P}}}grpSpPr'}: continue
            ident = _object_id(obj)
            if ident in ids: _reject()
            ids.add(ident); objects += 1
            if objects > 64: _reject()
            if obj.tag == f'{{{_P}}}sp':
                _children(obj, _P, {'nvSpPr', 'spPr', 'txBody', 'style'})
                _inherited_bullets(obj, layout, master, presentation)
                body = _one(obj, f'{{{_P}}}txBody', required=False)
                for n, text in enumerate(_paragraphs(body) if body is not None else [], 1):
                    append(text, f's:{slide_no}:sp:{ident}:p:{n}', slide_no, ident)
            else:
                if any(child.tag not in {f'{{{_P}}}nvGraphicFramePr', f'{{{_P}}}xfrm', f'{{{_A}}}graphic'} for child in obj): _reject('UNSUPPORTED_FORMAT')
                graphic = _one(obj, f'{{{_A}}}graphic')
                _children(graphic, _A, {'graphicData'})
                data = _one(graphic, f'{{{_A}}}graphicData')
                if data.get('uri') != 'http://schemas.openxmlformats.org/drawingml/2006/table': _reject('UNSUPPORTED_FORMAT')
                _children(data, _A, {'tbl'})
                tbl = _one(data, f'{{{_A}}}tbl')
                _children(tbl, _A, {'tblPr', 'tblGrid', 'tr'})
                grid = _one(tbl, f'{{{_A}}}tblGrid'); _children(grid, _A, {'gridCol'})
                if not 1 <= len(grid) <= 64: _reject()
                rows = tbl.findall(f'{{{_A}}}tr')
                if not 1 <= len(rows) <= 64: _reject()
                for row_no, row in enumerate(rows, 1):
                    _children(row, _A, {'tc'})
                    if len(row) != len(grid): _reject()
                    for col_no, cell in enumerate(row, 1):
                        if any(key in cell.attrib for key in ('gridSpan', 'rowSpan', 'hMerge', 'vMerge')): _reject('UNSUPPORTED_FORMAT')
                        _children(cell, _A, {'txBody', 'tcPr'})
                        cells += 1
                        if cells > 64: _reject()
                        texts = _paragraphs(_one(cell, f'{{{_A}}}txBody'))
                        if not texts: _reject()
                        for para_no, text in enumerate(texts, 1):
                            append(text, f's:{slide_no}:tb:{ident}:r:{row_no}:c:{col_no}:p:{para_no}', slide_no, ident,
                                   {'row': row_no, 'column': col_no, 'paragraph': para_no})
    digest = hashlib.sha256(content).hexdigest()
    native = _dump({'serialization_version': _VERSION, 'source_sha256': digest, 'blocks': blocks})
    if len(native.encode('utf-8')) > _MAX_JSON: _reject()
    return PptxDocument(digest, tuple(blocks), native)


def _locator(block_id, start, end):
    if not isinstance(block_id, str) or len(block_id) > 100: _reject()
    match = _ID.fullmatch(block_id)
    if match is None: _reject()
    slide, sp, para, tb, row, col, table_para = match.groups()
    if int(slide) > 16 or int(sp or tb) > 4294967295 or int(para or table_para) > 128 or (row and (int(row) > 64 or int(col) > 64)): _reject()
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= 2000: _reject()


def encode_pptx_block(block_id: str, start: int, end: int) -> str:
    _locator(block_id, start, end)
    value = _dump({'block': block_id, 'start': start, 'end': end})
    if len(value.encode('utf-8')) > 256: _reject()
    return value


def decode_pptx_block(value: str) -> tuple[str, int, int]:
    try:
        if not isinstance(value, str) or len(value.encode('utf-8')) > 256: _reject()
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result: _reject()
                result[key] = item
            return result
        parsed = json.loads(value, object_pairs_hook=unique)
        if not isinstance(parsed, dict) or set(parsed) != {'block', 'start', 'end'}: _reject()
        result = parsed['block'], parsed['start'], parsed['end']
        if encode_pptx_block(*result) != value: _reject()
        return result
    except SourceRejected: raise
    except (ValueError, TypeError, UnicodeError): _reject()


def read_pptx_block_view(document: PptxDocument, locator_value: str) -> dict[str, object]:
    block, start, end = decode_pptx_block(locator_value)
    matches = [(i, item) for i, item in enumerate(document.blocks) if item['block_id'] == block]
    if len(matches) != 1: _reject()
    index, selected = matches[0]
    if end > len(selected['text']): _reject()
    nearby = [item for item in document.blocks[max(0, index - 1):index + 2] if item['slide'] == selected['slide']]
    result = {'source_sha256': document.source_sha256, 'serialization_version': _VERSION,
              'selected_block_id': block, 'selected_start': start, 'selected_end': end, 'blocks': nearby}
    if len(_dump(result).encode('utf-8')) > _MAX_JSON: _reject()
    return result
