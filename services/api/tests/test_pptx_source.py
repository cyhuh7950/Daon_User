"""Real ZIP fixtures for the bounded structural reader, not model evidence."""
from __future__ import annotations

import hashlib
import io
import json
import struct
import zlib
import unittest
import warnings
import zipfile
from xml.sax.saxutils import escape

from daon_user_api.source_ingest import SourceRejected
from daon_user_api.pptx_source import (
    PptxDocument, read_pptx, encode_pptx_block, decode_pptx_block,
    read_pptx_block_view,
)

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
NS = f'xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"'


def paragraph(text='value', *, raw=None):
    return '<a:p>' + (raw if raw is not None else f'<a:r><a:t>{escape(text)}</a:t></a:r>') + '</a:p>'


def shape(object_id=2, paras=None, *, placeholder='', extra=''):
    paras = [paragraph()] if paras is None else paras
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{object_id}" name="text"/>'
            f'<p:cNvSpPr/><p:nvPr>{placeholder}</p:nvPr></p:nvSpPr>'
            f'<p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>{"".join(paras)}</p:txBody>{extra}</p:sp>')


def table(object_id=3, rows=(('amount', '57000'), ('currency', 'KRW'))):
    width = len(rows[0])
    cells = ''.join('<a:tr h="1">' + ''.join(
        '<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>' + paragraph(v) +
        '</a:txBody><a:tcPr/></a:tc>' for v in row) + '</a:tr>' for row in rows)
    return (f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{object_id}" name="table"/>'
            '<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm/>'
            f'<a:graphic><a:graphicData uri="{A.replace("/main", "/table")}"><a:tbl><a:tblPr/>'
            '<a:tblGrid>' + '<a:gridCol w="1"/>' * width + '</a:tblGrid>' + cells +
            '</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')


def slide(objects=None, *, attributes='', extra=''):
    return (f'<p:sld {NS} {attributes}><p:cSld><p:spTree><p:nvGrpSpPr>'
            '<p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr/>' + (shape() if objects is None else objects) +
            '</p:spTree></p:cSld>' + extra + '</p:sld>')


def relationships(items):
    return f'<Relationships xmlns="{REL}">' + ''.join(
        f'<Relationship Id="{key}" Type="{R}/{kind}" Target="{target}"/>'
        for key, kind, target in items) + '</Relationships>'


def pptx_parts(slides=None, *, order=None, layout_objects='', master_objects=''):
    slides = [slide()] if slides is None else slides
    order = list(range(1, len(slides) + 1)) if order is None else order
    parts = {
        '_rels/.rels': relationships([('main', 'officeDocument', 'ppt/presentation.xml')]),
        'ppt/presentation.xml': f'<p:presentation {NS}><p:sldIdLst>' + ''.join(
            f'<p:sldId id="{255 + n}" r:id="s{n}"/>' for n in order) + '</p:sldIdLst></p:presentation>',
        'ppt/_rels/presentation.xml.rels': relationships(
            [(f's{n}', 'slide', f'slides/slide{n}.xml') for n in range(1, len(slides) + 1)]),
        'ppt/slideLayouts/slideLayout1.xml': slide(layout_objects).replace('p:sld', 'p:sldLayout'),
        'ppt/slideLayouts/_rels/slideLayout1.xml.rels': relationships([('master', 'slideMaster', '../slideMasters/slideMaster1.xml')]),
        'ppt/slideMasters/slideMaster1.xml': slide(master_objects).replace('p:sld', 'p:sldMaster'),
        'ppt/slideMasters/_rels/slideMaster1.xml.rels': relationships([('theme', 'theme', '../theme/theme1.xml')]),
        'ppt/theme/theme1.xml': f'<a:theme xmlns:a="{A}" name="plain"><a:themeElements/></a:theme>',
    }
    for n, xml in enumerate(slides, 1):
        parts[f'ppt/slides/slide{n}.xml'] = xml
        parts[f'ppt/slides/_rels/slide{n}.xml.rels'] = relationships([('layout', 'slideLayout', '../slideLayouts/slideLayout1.xml')])
    overrides = [('ppt/presentation.xml', 'presentation.main')]
    overrides += [(f'ppt/slides/slide{n}.xml', 'slide') for n in range(1, len(slides) + 1)]
    overrides += [('ppt/slideLayouts/slideLayout1.xml', 'slideLayout'), ('ppt/slideMasters/slideMaster1.xml', 'slideMaster')]
    parts['[Content_Types].xml'] = f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' + ''.join(
        f'<Override PartName="/{name}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.{kind}+xml"/>' for name, kind in overrides) + f'<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>'
    return parts


def zip_bytes(parts=None, *, duplicate=None, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(output, 'w', compression=compression) as archive:
            for name, data in (pptx_parts() if parts is None else parts).items():
                archive.writestr(name, data)
            if duplicate: archive.writestr(duplicate, '<duplicate/>')
    return output.getvalue()


def pptx_bytes():
    return zip_bytes()


def padded_deflate_zip(parts, *, ratio=100):
    """Valid raw-deflate ZIP with unused compressed padding; exact declared ratio."""
    output, central = bytearray(), bytearray()
    for name, data in parts.items():
        payload = data.encode() if isinstance(data, str) else data
        compressor = zlib.compressobj(wbits=-15)
        compressed = compressor.compress(payload) + compressor.flush()
        target = max(len(compressed), (len(payload) + ratio - 1) // ratio)
        compressed += b'\0' * (target - len(compressed))
        encoded = name.encode(); crc = zlib.crc32(payload); offset = len(output)
        output += struct.pack('<IHHHHHIIIHH', 0x04034b50, 20, 0, 8, 0, 0, crc, len(compressed), len(payload), len(encoded), 0) + encoded + compressed
        central += struct.pack('<IHHHHHHIIIHHHHHII', 0x02014b50, 20, 20, 0, 8, 0, 0, crc, len(compressed), len(payload), len(encoded), 0, 0, 0, 0, 0, offset) + encoded
    offset = len(output); output += central
    output += struct.pack('<IHHHHIIH', 0x06054b50, 0, 0, len(parts), len(parts), len(central), offset, 0)
    return bytes(output)


class PptxSourceTests(unittest.TestCase):
    def reject(self, parts, code=None):
        with self.assertRaisesRegex(SourceRejected, code or 'CORRUPTED_SOURCE|UNSUPPORTED_FORMAT|COMPRESSION_BOMB|ENCRYPTED_SOURCE'):
            read_pptx(zip_bytes(parts))

    def test_slide_list_order_not_filename_order(self):
        content = zip_bytes(pptx_parts([slide(table()), slide(shape(2, [paragraph('', raw='<a:r><a:t> 가</a:t></a:r><a:r><a:t>😀나 </a:t></a:r><a:br/><a:r><a:t>tail</a:t></a:r><a:tab/>'), paragraph('')]))], order=[2, 1]))
        result = read_pptx(content)
        self.assertEqual(result.source_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual([b['block_id'] for b in result.blocks], ['s:1:sp:2:p:1', 's:1:sp:2:p:2', 's:2:tb:3:r:1:c:1:p:1', 's:2:tb:3:r:1:c:2:p:1', 's:2:tb:3:r:2:c:1:p:1', 's:2:tb:3:r:2:c:2:p:1'])
        self.assertEqual([b['text'] for b in result.blocks], [' 가😀나 \ntail\t', '', 'amount', '57000', 'currency', 'KRW'])
        self.assertEqual([b['ordinal'] for b in result.blocks], [1, 2, 3, 4, 5, 6])
        self.assertEqual(set(json.loads(result.native_json)), {'serialization_version', 'source_sha256', 'blocks'})
        with self.assertRaises(TypeError): result.blocks[0]['text'] = 'changed'
        with self.assertRaises(TypeError): result.blocks[2]['table']['row'] = 9

    def test_master_layout_and_notes_do_not_silently_drop_meaning(self):
        guide = shape(2, [paragraph('Template instruction')], placeholder='<p:ph type="title"/>')
        parts = pptx_parts(layout_objects=guide, master_objects=guide)
        self.assertEqual([b['text'] for b in read_pptx(zip_bytes(parts)).blocks], ['value'])
        for objects in (shape(2, [paragraph('visible inherited content')]), '<p:pic/>'):
            with self.subTest(objects=objects): self.reject(pptx_parts(layout_objects=objects))
        parts = pptx_parts()
        parts['ppt/notesSlides/notesSlide1.xml'] = slide(shape(2, [paragraph('user note')], placeholder='<p:ph type="body"/>')).replace('p:sld', 'p:notes')
        self.reject(parts)

    def test_standard_theme_formatting_is_not_document_content(self):
        parts = pptx_parts()
        parts['ppt/theme/theme1.xml'] = f'<a:theme xmlns:a="{A}"><a:themeElements/><a:objectDefaults><a:spDef><a:spPr/><a:bodyPr/><a:lstStyle/><a:style><a:lnRef idx="0"/></a:style></a:spDef></a:objectDefaults></a:theme>'
        self.assertEqual([b['text'] for b in read_pptx(zip_bytes(parts)).blocks], ['value'])

    def test_geometry_extent_is_not_an_arbitrary_extension(self):
        parts = pptx_parts([slide(shape().replace('<p:spPr/>', '<p:spPr><a:xfrm><a:off x="1" y="2"/><a:ext cx="3" cy="4"/></a:xfrm></p:spPr>'))])
        self.assertEqual(read_pptx(zip_bytes(parts)).blocks[0]['text'], 'value')
        parts['ppt/slides/slide1.xml'] = slide(shape(extra='<p:extLst><p:ext uri="urn:unknown"><a:t>hidden</a:t></p:ext></p:extLst>'))
        self.reject(parts)

    def test_every_supported_auxiliary_part_rejects_unknown_meaning(self):
        for name, root, kind in [('ppt/presProps.xml', 'presentationPr', 'presProps'), ('ppt/viewProps.xml', 'viewPr', 'viewProps'), ('ppt/notesMasters/notesMaster1.xml', 'notesMaster', 'notesMaster')]:
            parts = pptx_parts()
            parts[name] = f'<p:{root} {NS}><p:unknownMeaning/></p:{root}>'
            parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace('</Types>', f'<Override PartName="/{name}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.{kind}+xml"/></Types>')
            with self.subTest(name=name): self.reject(parts)
        parts = pptx_parts(); name = 'ppt/tableStyles.xml'
        parts[name] = f'<a:tblStyleLst xmlns:a="{A}"><a:unknownMeaning/></a:tblStyleLst>'
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace('</Types>', f'<Override PartName="/{name}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/></Types>')
        self.reject(parts)

    def test_relationship_owner_is_not_ignored(self):
        parts = pptx_parts()
        parts['ppt/slides/_rels/slide1.xml.rels'] = relationships([('layout', 'slideLayout', '../slideLayouts/slideLayout1.xml'), ('unexpected', 'slide', 'slide1.xml')])
        self.reject(parts)

    def test_metadata_syntax_is_not_ignored(self):
        parts = pptx_parts()
        parts['docProps/core.xml'] = '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"><cp:unknownMeaning/></cp:coreProperties>'
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace('</Types>', '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
        self.reject(parts)

    def test_connected_placeholder_active_bullet_and_footer_are_unsupported(self):
        actual = shape(2, [paragraph('actual source')], placeholder='<p:ph type="body" idx="1"/>')
        guide = shape(2, [paragraph('guide', raw='<a:pPr><a:buChar char="*"/></a:pPr><a:r><a:t>guide</a:t></a:r>')], placeholder='<p:ph type="body" idx="1"/>')
        self.reject(pptx_parts([slide(actual)], layout_objects=guide))
        parts = pptx_parts(layout_objects=shape(3, [paragraph('inactive guide')], placeholder='<p:ph type="dt"/>'))
        self.assertEqual(read_pptx(zip_bytes(parts)).blocks[0]['text'], 'value')
        parts['ppt/slideLayouts/slideLayout1.xml'] = parts['ppt/slideLayouts/slideLayout1.xml'].replace('</p:sldLayout>', '<p:hf dt="1"/></p:sldLayout>')
        self.reject(parts)

    def test_empty_notes_part_and_normal_template_guides_are_not_facts(self):
        parts = pptx_parts()
        name = 'ppt/notesSlides/notesSlide1.xml'
        parts[name] = slide(shape(2, [paragraph('')], placeholder='<p:ph type="body"/>')).replace('p:sld', 'p:notes')
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace('</Types>', f'<Override PartName="/{name}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/></Types>')
        parts['ppt/slides/_rels/slide1.xml.rels'] = relationships([('layout', 'slideLayout', '../slideLayouts/slideLayout1.xml'), ('notes', 'notesSlide', '../notesSlides/notesSlide1.xml')])
        self.assertEqual([b['text'] for b in read_pptx(zip_bytes(parts)).blocks], ['value'])
        parts[name] = parts[name].replace('<a:t></a:t>', '<a:t>user note</a:t>')
        self.reject(parts)
        parts[name] = slide(shape(2, [paragraph('user note')], placeholder='<p:ph type="dt"/>')).replace('p:sld', 'p:notes')
        self.reject(parts)

    def test_auxiliary_known_semantic_tags_in_wrong_part_are_rejected(self):
        parts = pptx_parts()
        parts['ppt/presProps.xml'] = f'<p:presentationPr {NS}><a:pPr><a:buChar char="*"/></a:pPr></p:presentationPr>'
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace('</Types>', '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/></Types>')
        self.reject(parts)

    def test_known_text_elements_in_wrong_parent_are_not_silently_dropped(self):
        for injected in ('<a:p><a:r><a:t>hidden meaning</a:t></a:r></a:p>', '<a:t>hidden meaning</a:t>', '<p:txBody><a:bodyPr/><a:p><a:r><a:t>hidden meaning</a:t></a:r></a:p></p:txBody>'):
            parts = pptx_parts([slide(shape().replace('<p:spPr/>', '<p:spPr>' + injected + '</p:spPr>'))])
            with self.subTest(injected=injected): self.reject(parts)

    def test_unsupported_semantics_are_not_dropped(self):
        for objects in ('<p:pic/>', '<p:grpSp/>', '<p:cxnSp/>',
                        shape(extra='<p:unknownMeaning/>'),
                        shape(paras=[paragraph(raw='<a:pPr><a:buChar char="*"/></a:pPr><a:r><a:t>value</a:t></a:r>')]),
                        shape(paras=[paragraph(raw='<a:fld><a:t>value</a:t></a:fld>')]),
                        shape().replace('name="text"', 'name="text" hidden="1"'),
                        table().replace('<a:tc>', '<a:tc gridSpan="2">', 1),
                        shape().replace('<a:r>', '<x:r xmlns:x="urn:foreign">').replace('</a:r>', '</x:r>')):
            with self.subTest(objects=objects): self.reject(pptx_parts([slide(objects)]))
        for attributes, extra in (('show="0"', ''), ('', '<p:transition/>'), ('', '<p:timing/>')):
            with self.subTest(attributes=attributes, extra=extra): self.reject(pptx_parts([slide(attributes=attributes, extra=extra)]))

    def test_slide_relationship_and_object_identity_binding(self):
        for change in ('unreferenced', 'missing', 'duplicate_id', 'wrong_type', 'external', 'duplicate_object'):
            parts = pptx_parts([slide(), slide()])
            if change == 'unreferenced': parts['ppt/presentation.xml'] = parts['ppt/presentation.xml'].replace('<p:sldId id="257" r:id="s2"/>', '')
            if change == 'missing': del parts['ppt/slides/slide1.xml']
            if change == 'duplicate_id': parts['ppt/presentation.xml'] = parts['ppt/presentation.xml'].replace('id="257"', 'id="256"')
            if change == 'wrong_type': parts['ppt/_rels/presentation.xml.rels'] = parts['ppt/_rels/presentation.xml.rels'].replace('/slide"', '/theme"')
            if change == 'external': parts['ppt/slides/_rels/slide1.xml.rels'] = parts['ppt/slides/_rels/slide1.xml.rels'].replace('Target="', 'TargetMode="External" Target="')
            if change == 'duplicate_object': parts['ppt/slides/slide1.xml'] = slide(shape() + shape())
            with self.subTest(change=change): self.reject(parts)

    def test_codepoint_locator_and_same_slide_view(self):
        result = read_pptx(zip_bytes(pptx_parts([slide(shape(2, [paragraph('before'), paragraph('가😀나'), paragraph('after')])), slide(shape(2, [paragraph('other slide')]))])))
        locator = encode_pptx_block('s:1:sp:2:p:2', 1, 2)
        self.assertEqual(locator, '{"block":"s:1:sp:2:p:2","start":1,"end":2}')
        self.assertEqual(decode_pptx_block(locator), ('s:1:sp:2:p:2', 1, 2))
        view = read_pptx_block_view(result, locator)
        self.assertEqual([b['text'] for b in view['blocks']], ['before', '가😀나', 'after'])
        self.assertEqual(view['blocks'][1]['text'][view['selected_start']:view['selected_end']], '😀')
        self.assertNotIn('source_version_id', view)
        view = read_pptx_block_view(result, encode_pptx_block('s:2:sp:2:p:1', 0, 1))
        self.assertEqual([b['text'] for b in view['blocks']], ['other slide'])
        for block, start, end in [('s:0:sp:2:p:1', 0, 1), ('s:17:sp:2:p:1', 0, 1), ('s:1:sp:4294967296:p:1', 0, 1), ('s:01:sp:2:p:1', 0, 1), ('s:1:tb:3:r:65:c:1:p:1', 0, 1), ('s:1:sp:2:p:129', 0, 1), ('s:1:sp:2:p:1', True, 2), ('s:1:sp:2:p:1', 0, 2001), ('s:1:sp:2:p:1', 1, 1)]:
            with self.subTest(block=block, start=start, end=end):
                with self.assertRaises(SourceRejected): encode_pptx_block(block, start, end)
        for value in ('{"block":"s:1:sp:2:p:1","start":0,"end":1,"extra":0}', '{"block":"s:1:sp:2:p:1","start":0,"start":1,"end":2}', '{"block":"s:1:sp:2:p:1","start":"0","end":1}', '{"end":1,"start":0,"block":"s:1:sp:2:p:1"}'):
            with self.subTest(value=value):
                with self.assertRaises(SourceRejected): decode_pptx_block(value)
        for locator in (encode_pptx_block('s:1:sp:99:p:1', 0, 1), encode_pptx_block('s:1:sp:2:p:2', 0, 4)):
            with self.assertRaises(SourceRejected): read_pptx_block_view(result, locator)
        self.assertEqual(decode_pptx_block('{"block":"s:16:tb:4294967295:r:64:c:64:p:128","start":0,"end":2000}'), ('s:16:tb:4294967295:r:64:c:64:p:128', 0, 2000))

    def test_upload_byte_cap_is_inclusive(self):
        for limit in (25 * 1024 * 1024, 25 * 1024 * 1024 + 1):
            parts = pptx_parts()
            name = 'ppt/printerSettings/printerSettings1.bin'
            parts[name] = b''
            parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace('</Types>', f'<Override PartName="/{name}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.printerSettings"/></Types>')
            parts['ppt/_rels/presentation.xml.rels'] = relationships([('s1', 'slide', 'slides/slide1.xml'), ('printer', 'printerSettings', 'printerSettings/printerSettings1.bin')])
            parts[name] = b'\0' * (limit - len(zip_bytes(parts)))
            content = zip_bytes(parts)
            self.assertEqual(len(content), limit)
            if limit == 25 * 1024 * 1024: self.assertEqual(read_pptx(content).blocks[0]['text'], 'value')
            else:
                with self.assertRaises(SourceRejected): read_pptx(content)

    def test_structural_limits_are_inclusive_and_not_truncated(self):
        for count in (16, 17):
            parts = pptx_parts([slide('') for _ in range(count)])
            if count == 16: self.assertEqual(len(read_pptx(zip_bytes(parts)).blocks), 0)
            else: self.reject(parts)
        for count in (64, 65):
            parts = pptx_parts([slide(''.join(shape(n + 2, []) for n in range(count)))])
            if count == 64: read_pptx(zip_bytes(parts))
            else: self.reject(parts)
        for count in (128, 129):
            parts = pptx_parts([slide(shape(2, [paragraph('') for _ in range(count)]))])
            if count == 128: self.assertEqual(len(read_pptx(zip_bytes(parts)).blocks), 128)
            else: self.reject(parts)
        for count in (32, 33):
            parts = pptx_parts([slide(shape(2, [paragraph('v') for _ in range(count)]))])
            if count == 32: self.assertEqual(len(read_pptx(zip_bytes(parts)).blocks), 32)
            else: self.reject(parts)
        for count in (64, 65):
            parts = pptx_parts([slide(table(rows=(tuple('' for _ in range(count)),)))])
            if count == 64: self.assertEqual(len(read_pptx(zip_bytes(parts)).blocks), 64)
            else: self.reject(parts)
        for count in (2000, 2001):
            parts = pptx_parts([slide(shape(2, [paragraph('😀' * count)]))])
            if count == 2000: self.assertEqual(len(read_pptx(zip_bytes(parts)).blocks[0]['text']), 2000)
            else: self.reject(parts)

    def test_zip_xml_relationship_limits(self):
        for count in (256, 257):
            parts = pptx_parts()
            for n in range(count - len(parts)):
                parts[f'ppt/theme/theme{n + 2}.xml'] = f'<a:theme xmlns:a="{A}"><a:themeElements/></a:theme>'
                parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace('</Types>', f'<Override PartName="/ppt/theme/theme{n + 2}.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>')
            if count == 256: self.assertEqual([b['text'] for b in read_pptx(zip_bytes(parts)).blocks], ['value'])
            else: self.reject(parts)
        for name in ('../escape.xml', '/absolute.xml', 'ppt/../escape.xml', 'ppt\\escape.xml', 'ppt/vbaProject.bin'):
            parts = pptx_parts(); parts[name] = '<part/>'
            with self.subTest(name=name): self.reject(parts)
        with self.assertRaises(SourceRejected): read_pptx(zip_bytes(duplicate='ppt/slides/slide1.xml'))
        for encoding in ('utf-8', 'utf-16', 'utf-32'):
            parts = pptx_parts(); parts['ppt/slides/slide1.xml'] = ('<?xml version="1.0"?><!DOCTYPE p:sld [<!ENTITY x "value">]>' + slide().replace('value', '&x;')).encode(encoding)
            with self.subTest(encoding=encoding): self.reject(parts)
        corrupted = bytearray(pptx_bytes())
        at = corrupted.index(b'value'); corrupted[at] ^= 1
        with self.assertRaises(SourceRejected): read_pptx(bytes(corrupted))
        encrypted = bytearray(pptx_bytes()); struct.pack_into('<H', encrypted, 6, 1)
        with self.assertRaisesRegex(SourceRejected, 'ENCRYPTED_SOURCE'): read_pptx(bytes(encrypted))
        parts = pptx_parts(); parts['docProps/core.xml'] = b'A' * 200000
        with self.assertRaisesRegex(SourceRejected, 'COMPRESSION_BOMB'): read_pptx(zip_bytes(parts, compression=zipfile.ZIP_DEFLATED))

    def test_declared_and_actual_uncompressed_byte_limit(self):
        for limit in (32 * 1024 * 1024, 32 * 1024 * 1024 + 1):
            parts = pptx_parts()
            parts['ppt/theme/theme1.xml'] = f'<a:theme xmlns:a="{A}"><a:themeElements/>' + '</a:theme>'
            size = sum(len(v.encode()) for v in parts.values())
            parts['ppt/theme/theme1.xml'] = parts['ppt/theme/theme1.xml'].replace('</a:theme>', ' ' * (limit - size) + '</a:theme>')
            content = padded_deflate_zip(parts)
            self.assertLess(len(content), 25 * 1024 * 1024)
            if limit == 32 * 1024 * 1024: self.assertEqual(read_pptx(content).blocks[0]['text'], 'value')
            else:
                with self.assertRaises(SourceRejected): read_pptx(content)

    def test_compression_ratio_100_is_inclusive(self):
        parts = pptx_parts()
        opening = f'<a:theme xmlns:a="{A}"><a:themeElements/>'
        parts['ppt/theme/theme1.xml'] = opening + ' ' * (100000 - len(opening.encode()) - len('</a:theme>')) + '</a:theme>'
        content = padded_deflate_zip(parts)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo('ppt/theme/theme1.xml')
            self.assertEqual(info.file_size / info.compress_size, 100)
        self.assertEqual(read_pptx(content).blocks[0]['text'], 'value')
        with self.assertRaisesRegex(SourceRejected, 'COMPRESSION_BOMB'):
            read_pptx(padded_deflate_zip(parts, ratio=101))

    def test_xml_depth_64_is_inclusive(self):
        from daon_user_api.pptx_source import _package
        for depth in (64, 65):
            parts = pptx_parts()
            parts['ppt/theme/theme1.xml'] = f'<a:theme xmlns:a="{A}">' + '<a:effectDag>' * (depth - 1) + '</a:effectDag>' * (depth - 1) + '</a:theme>'
            if depth == 64: _package(zip_bytes(parts))
            else:
                with self.assertRaises(SourceRejected): _package(zip_bytes(parts))

    def test_native_and_view_json_limit_is_not_silently_trimmed(self):
        # Independent exact root/block literals, not the production serializer.
        block = {'block_id': 's:1:sp:2:p:1', 'ordinal': 1, 'slide': 1, 'object_id': 2, 'block_type': 'shape_paragraph', 'text': 'x', 'table': None}
        base = {'serialization_version': 'pptx-structure-v1', 'source_sha256': '0' * 64, 'blocks': [block]}
        # Spread payload across <=2000-character paragraphs to reach the byte cap.
        for size in (32768, 32769):
            blocks = []
            for n in range(16):
                item = dict(block, block_id=f's:1:sp:2:p:{n + 1}', ordinal=n + 1, text='x' * 1900)
                blocks.append(item)
            encoded = json.dumps(dict(base, blocks=blocks), separators=(',', ':'))
            delta = size - len(encoded.encode())
            # Exact byte sizing uses multibyte text without increasing codepoints.
            text = blocks[-1]['text']; blocks[-1]['text'] = 'é' * delta + text[delta:]
            self.assertEqual(len(json.dumps(dict(base, blocks=blocks), ensure_ascii=False, separators=(',', ':')).encode()), size)
            parts = pptx_parts([slide(shape(2, [paragraph(b['text']) for b in blocks]))])
            if size == 32768: self.assertEqual(len(read_pptx(zip_bytes(parts)).native_json.encode()), 32768)
            else: self.reject(parts)


if __name__ == '__main__': unittest.main()
