from __future__ import annotations

import hashlib
import io
import json
import struct
import unittest
import warnings
import random
import zipfile
from html import escape
from unittest.mock import patch

from daon_user_api.docx_source import (
    DocxDocument, read_docx, encode_docx_block, decode_docx_block, read_docx_block_view,
)
from daon_user_api.source_ingest import SourceRejected


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def paragraph(text="", *, properties=""):
    return f'<w:p>{properties}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def table(rows):
    return '<w:tbl>' + ''.join('<w:tr>' + ''.join('<w:tc>' + cell + '</w:tc>' for cell in row) + '</w:tr>' for row in rows) + '</w:tbl>'


def docx_bytes(body=None, *, styles=None, extras=(), document=None, compression=zipfile.ZIP_STORED):
    parts = [
        ('[Content_Types].xml', f'<Types xmlns="{CT}"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="{MAIN}"/></Types>'),
        ('_rels/.rels', f'<Relationships xmlns="{R}"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'),
        ('word/document.xml', document if document is not None else f'<w:document xmlns:w="{W}"><w:body>{body if body is not None else paragraph("Hello")}</w:body></w:document>'),
    ]
    if styles is not None:
        parts.append(('word/styles.xml', f'<w:styles xmlns:w="{W}">{styles}</w:styles>'))
    parts.extend(extras)
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, 'w', compression=compression) as archive:
            for name, value in parts:
                archive.writestr(name, value.encode() if isinstance(value, str) else value)
    return output.getvalue()


class DocxSourceTests(unittest.TestCase):
    def test_docx_structure_preserves_order_empty_runs_styles_and_code_points(self):
        styles = '<w:style w:type="paragraph" w:styleId="Base"><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style><w:style w:type="paragraph" w:styleId="Title"><w:basedOn w:val="Base"/></w:style>'
        body = paragraph('') + '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t xml:space="preserve"> 가😀</w:t></w:r><w:r><w:t>나</w:t><w:tab/><w:t>x</w:t><w:br/><w:t>끝 </w:t></w:r></w:p>' + table([[paragraph('57000'), paragraph('KRW')], [paragraph('A'), paragraph('B')]])
        content = docx_bytes(body, styles=styles)
        result = read_docx(content)
        self.assertIsInstance(result, DocxDocument)
        self.assertEqual(result.source_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual([b['block_id'] for b in result.blocks], ['p:1', 'p:2', 't:3:r:1:c:1:p:1', 't:3:r:1:c:2:p:1', 't:3:r:2:c:1:p:1', 't:3:r:2:c:2:p:1'])
        self.assertEqual([b['ordinal'] for b in result.blocks], [1, 2, 3, 4, 5, 6])
        self.assertEqual(result.blocks[1], {'block_id': 'p:2', 'ordinal': 2, 'block_type': 'paragraph', 'text': ' 가😀나\tx\n끝 ', 'style_id': 'Title', 'outline_level': 1, 'table': None})
        self.assertEqual(result.blocks[3]['table'], {'body_index': 3, 'row': 1, 'column': 2, 'paragraph': 1})
        self.assertEqual(json.loads(result.native_json), {'serialization_version': 'docx-structure-v1', 'source_sha256': result.source_sha256, 'blocks': list(result.blocks)})
        with self.assertRaises(TypeError): result.blocks[0]['text'] = 'changed'
        with self.assertRaises(TypeError): result.blocks[3]['table']['row'] = 2

    def test_docx_structure_count_limits_include_boundary_and_reject_excess(self):
        fixtures = [
            (paragraph('') * 64, paragraph('') * 65),
            (paragraph('A') * 32, paragraph('A') * 33),
            (paragraph('A' * 2000), paragraph('A' * 2001)),
            (table([[paragraph('') * 2] * 8] * 8), table([[paragraph('') * 2] * 8] * 8) + paragraph('')),
            (table([[paragraph('')] * 8] * 8), table([[paragraph('')] * 13] * 5)),
        ]
        for accepted, rejected in fixtures:
            with self.subTest(limit=fixtures.index((accepted, rejected))):
                read_docx(docx_bytes(accepted))
                with self.assertRaises(SourceRejected): read_docx(docx_bytes(rejected))

    def test_docx_native_json_exact_32k_boundary_is_not_truncated(self):
        texts = ['A' * 1800] * 16 + ['']
        blocks = [{'block_id': f'p:{i}', 'ordinal': i, 'block_type': 'paragraph', 'text': text,
                   'style_id': None, 'outline_level': None, 'table': None} for i, text in enumerate(texts, 1)]
        payload = {'serialization_version': 'docx-structure-v1', 'source_sha256': '0' * 64, 'blocks': blocks}
        length = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())
        texts[-1] = 'B' * (32768 - length)
        self.assertLessEqual(len(texts[-1]), 2000)
        result = read_docx(docx_bytes(''.join(paragraph(text) for text in texts)))
        self.assertEqual(len(result.native_json.encode()), 32768)
        with self.assertRaises(SourceRejected):
            read_docx(docx_bytes(''.join(paragraph(text) for text in texts[:-1]) + paragraph(texts[-1] + 'B')))

    def test_docx_zip_member_and_xml_depth_limits(self):
        extras = [(f'docProps/p{i}.xml', '<meta/>') for i in range(125)]
        read_docx(docx_bytes(extras=extras))
        with self.assertRaises(SourceRejected): read_docx(docx_bytes(extras=extras + [('docProps/extra.xml', '<meta/>')]))
        for depth, accepted in ((64, True), (65, False)):
            content = docx_bytes(extras=[('docProps/custom.xml', '<meta>' * depth + '</meta>' * depth)])
            if accepted: read_docx(content)
            else:
                with self.assertRaises(SourceRejected): read_docx(content)

    def test_docx_actual_uncompressed_32m_boundary_and_bounded_reads(self):
        baseline = docx_bytes(extras=[('docProps/custom.xml', '<meta><!-- --></meta>')])
        with zipfile.ZipFile(io.BytesIO(baseline)) as archive:
            overhead = sum(member.file_size for member in archive.infolist())
        size = 32 * 1024 * 1024 - overhead
        randomizer = random.Random(41)
        seed = ''.join(randomizer.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', k=65536))
        padding = (seed * (size // len(seed) + 1))[:size]
        content = docx_bytes(extras=[('docProps/custom.xml', '<meta><!-- ' + padding + '--></meta>')], compression=zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(sum(m.file_size for m in archive.infolist()), 32 * 1024 * 1024)
        original_read, reads = zipfile.ZipExtFile.read, []

        def bounded(stream, size=-1):
            self.assertGreater(size, 0)
            self.assertLessEqual(size, 65536)
            value = original_read(stream, size)
            reads.append(len(value))
            return value

        with patch.object(zipfile.ZipExtFile, 'read', bounded): read_docx(content)
        self.assertEqual(sum(reads), 32 * 1024 * 1024)
        excess = docx_bytes(extras=[('docProps/custom.xml', '<meta><!-- ' + padding + 'A--></meta>')], compression=zipfile.ZIP_DEFLATED)
        with patch.object(zipfile.ZipFile, 'open', side_effect=AssertionError('declared excess must fail before decompression')):
            with self.assertRaises(SourceRejected): read_docx(excess)

    def test_docx_rejects_unsafe_zip_xml_and_namespaces_before_execution(self):
        normal = docx_bytes()
        encrypted = bytearray(normal)
        struct.pack_into('<H', encrypted, 6, 1)
        central = encrypted.index(b'PK\x01\x02')
        struct.pack_into('<H', encrypted, central + 8, 1)
        corrupted = bytearray(normal)
        corrupted[30 + len('[Content_Types].xml') + 20] ^= 1
        cases = [bytes(encrypted), bytes(corrupted), normal[:-22],
            docx_bytes(extras=[('../escape.xml', '<meta/>')]),
            docx_bytes(extras=[('word/document.xml', '<duplicate/>')]),
            docx_bytes(extras=[('docProps/custom.xml', '<!DOCTYPE meta [<!ENTITY x "value">]><meta>&x;</meta>')]),
            docx_bytes(extras=[('docProps/custom.xml', '<!DOCTYPE meta [<!ENTITY x "value">]><meta>&x;</meta>'.encode('utf-16'))]),
            docx_bytes(extras=[('word/_rels/document.xml.rels', f'<Relationships xmlns="{R}"><Relationship Id="x" Type="hyperlink" TargetMode="External" Target="https://example.com"/></Relationships>')]),
            docx_bytes(document='<document><body><p><r><t>fake</t></r></p></body></document>'),
            docx_bytes('<x:p xmlns:x="urn:foreign"><w:r><w:t>fake</w:t></w:r></x:p>'),
            docx_bytes(extras=[('[Content_Types].xml', f'<Types xmlns="urn:foreign"/>')]),
        ]
        for index, content in enumerate(cases):
            with self.subTest(case=index), patch('urllib.request.urlopen', side_effect=AssertionError('no network')), patch('subprocess.run', side_effect=AssertionError('no execution')):
                with self.assertRaises(SourceRejected): read_docx(content)

    def test_docx_rejects_unsupported_body_tables_and_connected_styles(self):
        invalid_bodies = [
            '<w:p><w:r><w:drawing/></w:r></w:p>', '<w:p><w:fldSimple w:instr="field"/></w:p>',
            '<w:p><w:ins><w:r><w:t>changed</w:t></w:r></w:ins></w:p>', '<w:sdt/>', '<w:altChunk/>',
            '<w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>numbered</w:t></w:r></w:p>',
            '<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>hidden</w:t></w:r></w:p>',
            table([['<w:tcPr><w:gridSpan w:val="2"/></w:tcPr>' + paragraph('A')]]),
            table([[table([[paragraph('nested')]])]]), table([[paragraph('A'), paragraph('B')], [paragraph('C')]]),
            table([['']]), '<w:p><w:r><w:sym w:char="42"/></w:r></w:p>',
        ]
        for index, body in enumerate(invalid_bodies):
            with self.subTest(case=index):
                with self.assertRaises(SourceRejected): read_docx(docx_bytes(body))
        for property_xml in ('<w:numPr><w:numId w:val="1"/></w:numPr>', '<w:rPr><w:vanish/></w:rPr>'):
            styles = '<w:style w:type="paragraph" w:styleId="Base"><w:pPr>' + property_xml + '</w:pPr></w:style><w:style w:type="paragraph" w:styleId="Child"><w:basedOn w:val="Base"/></w:style>'
            with self.subTest(style=property_xml):
                with self.assertRaises(SourceRejected): read_docx(docx_bytes(paragraph('text', properties='<w:pPr><w:pStyle w:val="Child"/></w:pPr>'), styles=styles))
        for name, xml in (('word/header1.xml', f'<w:hdr xmlns:w="{W}">{paragraph("nonempty")}</w:hdr>'), ('word/comments.xml', f'<w:comments xmlns:w="{W}"/>')):
            with self.subTest(part=name):
                with self.assertRaises(SourceRejected): read_docx(docx_bytes(extras=[(name, xml)]))
        read_docx(docx_bytes(extras=[('word/header1.xml', f'<w:hdr xmlns:w="{W}">{paragraph("")}</w:hdr>')]))

    def test_docx_locator_exact_json_unicode_and_three_original_blocks(self):
        document = read_docx(docx_bytes(paragraph('before') + paragraph('가😀나') + paragraph('after') + paragraph('last')))
        locator = encode_docx_block('p:2', 1, 2)
        self.assertEqual(locator, '{"block":"p:2","start":1,"end":2}')
        self.assertEqual(decode_docx_block(locator), ('p:2', 1, 2))
        view = read_docx_block_view(document, locator)
        self.assertEqual(set(view), {'source_sha256', 'serialization_version', 'selected_block_id', 'selected_start', 'selected_end', 'blocks'})
        self.assertEqual([b['block_id'] for b in view['blocks']], ['p:1', 'p:2', 'p:3'])
        self.assertEqual(view['blocks'][1]['text'][view['selected_start']:view['selected_end']], '😀')
        self.assertEqual(len(read_docx_block_view(document, encode_docx_block('p:4', 0, 1))['blocks']), 2)
        bad = ['{"block":"p:2","start":true,"end":2}', '{"block":"p:2","start":1,"end":1}',
               '{"block":"p:2","start":2,"end":1}', '{"block":"p:02","start":0,"end":1}',
               '{"block":"p:2","start":"1","end":2}', '{"block":"p:2","start":0,"end":2,"extra":0}',
               '{"block":"p:2","start":0,"start":1,"end":2}', '{"block":"p:2","start":-1,"end":2}']
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(SourceRejected): decode_docx_block(value)
        for block, start, end in (('p:2', 0, 4), ('p:9', 0, 1), ('p:2', True, 2)):
            with self.subTest(block=block, start=start, end=end):
                with self.assertRaises(SourceRejected): read_docx_block_view(document, encode_docx_block(block, start, end))

    def test_docx_connected_style_and_invalid_xml_text_cannot_hide_meaning(self):
        cases = [
            (paragraph('visible', properties='<w:pPr><w:pStyle w:val="Bad"/></w:pPr>'),
             '<w:style w:type="paragraph" w:styleId="Bad"><w:sdt><w:r><w:t>hidden</w:t></w:r></w:sdt></w:style>'),
            ('<w:tbl><w:tblPr><w:tblStyle w:val="HiddenTable"/></w:tblPr><w:tr><w:tc>' + paragraph('visible') + '</w:tc></w:tr></w:tbl>',
             '<w:style w:type="table" w:styleId="HiddenTable"><w:rPr><w:vanish/></w:rPr></w:style>'),
            ('RAW' + paragraph('visible'), None),
            ('<w:p><w:r><w:t>visible</w:t>RAW</w:r></w:p>', None),
        ]
        for index, (body, styles) in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(SourceRejected): read_docx(docx_bytes(body, styles=styles))

    def test_docx_package_requires_declared_office_document_relationship(self):
        valid = docx_bytes()
        for missing in (True, False):
            with self.subTest(missing=missing):
                output = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(valid)) as source, zipfile.ZipFile(output, 'w') as target:
                    for name in source.namelist():
                        if name == '_rels/.rels':
                            if missing: continue
                            target.writestr(name, f'<Relationships xmlns="{R}"><Relationship Id="r1" Type="unknown" Target="word/document.xml"/></Relationships>')
                        else: target.writestr(name, source.read(name))
                with self.assertRaises(SourceRejected): read_docx(output.getvalue())

    def test_docx_upload_byte_limit_exact_and_excess(self):
        baseline = docx_bytes(extras=[('docProps/custom.xml', '<meta><!-- --></meta>')])
        padding = 'A' * (25 * 1024 * 1024 - len(baseline))
        content = docx_bytes(extras=[('docProps/custom.xml', '<meta><!-- ' + padding + '--></meta>')])
        self.assertEqual(len(content), 25 * 1024 * 1024)
        read_docx(content)
        with self.assertRaises(SourceRejected): read_docx(content + b'A')

    def test_docx_default_character_and_docdefaults_cannot_hide_or_drop_meaning(self):
        rejected = (
            '<w:style w:type="character" w:default="1" w:styleId="DefaultHidden"><w:rPr><w:vanish/></w:rPr></w:style>',
            '<w:docDefaults><w:rPrDefault><w:rPr><w:unknownMeaning/></w:rPr></w:rPrDefault></w:docDefaults>',
        )
        for styles in rejected:
            with self.subTest(styles=styles):
                with self.assertRaisesRegex(SourceRejected, '^UNSUPPORTED_FORMAT$'):
                    read_docx(docx_bytes(paragraph('text'), styles=styles))

    def test_docx_plain_default_styles_and_unused_numbering_stay_supported(self):
        styles = (
            '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri"/></w:rPr></w:rPrDefault>'
            '<w:pPrDefault><w:pPr><w:spacing w:after="0"/></w:pPr></w:pPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:pPr/></w:style>'
            '<w:style w:type="character" w:default="1" w:styleId="Default"><w:rPr><w:b/></w:rPr></w:style>'
            '<w:style w:type="numbering" w:default="1" w:styleId="NoList"><w:name w:val="No List"/></w:style>'
            '<w:style w:type="paragraph" w:styleId="UnusedList"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        )
        content = docx_bytes(paragraph('text'), styles=styles)
        document = read_docx(content)
        self.assertEqual(document.blocks[0]['text'], 'text')
        self.assertEqual(document.source_sha256, hashlib.sha256(content).hexdigest())
        view = read_docx_block_view(document, encode_docx_block('p:1', 0, 4))
        self.assertEqual(view['source_sha256'], hashlib.sha256(content).hexdigest())

