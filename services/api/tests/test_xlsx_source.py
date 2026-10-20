from __future__ import annotations

import hashlib
import io
import json
import struct
import unittest
import warnings
import zipfile
from unittest.mock import patch
from xml.sax.saxutils import escape, quoteattr

from daon_user_api.source_ingest import SourceRejected
from daon_user_api.xlsx_source import (
    read_xlsx, encode_sheet_cell, decode_sheet_cell, read_sheet_cell_view,
)
from daon_user_api import xlsx_source

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def parts(sheets=None, *, date1904=False):
    if sheets is None:
        sheets = [('납품 "계획"', '<cols><col min="2" max="2" hidden="1"/></cols><sheetData><row r="2" hidden="1"><c r="B2" t="s"><v>0</v></c><c r="C2" t="b"><v>1</v></c><c r="D2" s="1"><v>46000</v></c><c r="E2"><f>SUM(1,2)</f><v>3</v></c><c r="F2"><f>1/0</f></c><c r="G2" t="e"><v>#DIV/0!</v></c></row><row r="3"><c r="B3" t="inlineStr"><is><t>납품 완료</t></is></c></row></sheetData><mergeCells><mergeCell ref="B3:C3"/></mergeCells>', "visible"), ('예산', '<sheetData><row r="1"><c r="A1"><v>57000</v></c></row></sheetData>', "hidden")]
    book_sheets = ''.join(f'<sheet name={quoteattr(name)} sheetId="{i}" state="{visibility}" r:id="rId{i}"/>' for i, (name, _, visibility) in enumerate(sheets, 1))
    relationships = ''.join(f'<Relationship Id="rId{i}" Type="{DOC_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
    result = {
        '[Content_Types].xml': b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        'xl/workbook.xml': f'<workbook xmlns="{NS}" xmlns:r="{DOC_REL}"><workbookPr date1904="{int(date1904)}"/><sheets>{book_sheets}</sheets></workbook>'.encode(),
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{REL}">{relationships}</Relationships>'.encode(),
        'xl/sharedStrings.xml': f'<sst xmlns="{NS}"><si><t>ORANGE-42</t></si></sst>'.encode(),
        'xl/styles.xml': f'<styleSheet xmlns="{NS}"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>'.encode(),
    }
    for i, (_, xml, _) in enumerate(sheets, 1):
        result[f'xl/worksheets/sheet{i}.xml'] = f'<worksheet xmlns="{NS}">{xml}</worksheet>'.encode()
    return result


def zipped(entries, *, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=compression) as archive:
            for name, value in entries.items() if isinstance(entries, dict) else entries:
                archive.writestr(name, value)
    return output.getvalue()


def inline_sheet(text, name="시트", **kwargs):
    return parts([(name, f'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{escape(text)}</t></is></c></row></sheetData>', 'visible')], **kwargs)


class XlsxSourceTests(unittest.TestCase):
    def test_xlsx_bounds_and_structure(self):
        raw = zipped(parts())
        workbook = read_xlsx(raw)
        self.assertEqual(workbook.source_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual([sheet['sheet_name'] for sheet in workbook.sheets], ['납품 "계획"', '예산'])
        self.assertEqual(workbook.sheets[1]['sheet_visibility'], 'hidden')
        native = json.loads(workbook.native_json)
        self.assertFalse(native['date1904'])
        self.assertEqual(native['sheets'][0]['merged_ranges'], ['B3:C3'])
        cells = {c['address']: c for c in workbook.sheets[0]['cells']}
        self.assertEqual(cells['B2']['raw_value'], 'ORANGE-42')
        self.assertTrue(cells['B2']['hidden_row']); self.assertTrue(cells['B2']['hidden_column'])
        self.assertEqual(cells['C2']['display_value'], 'TRUE')
        self.assertEqual(cells['D2']['value_type'], 'date')
        self.assertEqual(cells['D2']['number_format'], 'mm-dd-yy')
        self.assertEqual(cells['D2']['display_value'], '2025-12-09')
        self.assertEqual(cells['E2']['formula'], 'SUM(1,2)'); self.assertEqual(cells['E2']['cached_value'], '3')
        self.assertIsNone(cells['F2']['display_value']); self.assertIsNone(cells['G2']['display_value'])
        self.assertEqual(cells['G2']['value_type'], 'error')
        alternate = read_xlsx(zipped(parts(date1904=True)))
        self.assertTrue(json.loads(alternate.native_json)['date1904'])
        self.assertEqual(alternate.sheets[0]['cells'][2]['display_value'], '2029-12-10')
        with self.assertRaises((AttributeError, TypeError)):
            workbook.native_json = 'modified'

    def test_member_and_uncompressed_boundaries(self):
        entries = parts()
        for i in range(128 - len(entries)):
            entries[f'padding/{i}.bin'] = b''
        self.assertEqual(len(entries), 128)
        read_xlsx(zipped(entries))
        entries['padding/extra.bin'] = b''
        with self.assertRaises(SourceRejected): read_xlsx(zipped(entries))
        entries = parts()
        entries['padding.bin'] = b'x' * (32 * 1024 * 1024 - sum(map(len, entries.values())))
        self.assertEqual(sum(map(len, entries.values())), 32 * 1024 * 1024)
        reads = []; original = zipfile.ZipExtFile.read
        def bounded_read(stream, size=-1):
            self.assertGreater(size, 0); self.assertLessEqual(size, 65536)
            value = original(stream, size); reads.append(len(value)); return value
        with patch.object(zipfile.ZipExtFile, 'read', bounded_read): read_xlsx(zipped(entries))
        self.assertEqual(sum(reads), 32 * 1024 * 1024)
        entries['padding.bin'] += b'x'
        oversized = zipped(entries)
        with patch.object(zipfile.ZipFile, 'open', side_effect=AssertionError('preflight before reads')):
            with self.assertRaises(SourceRejected): read_xlsx(oversized)

    def test_sheet_and_cell_boundaries(self):
        entries = parts([(str(i), '<sheetData/>', 'visible') for i in range(8)])
        self.assertEqual(len(read_xlsx(zipped(entries)).sheets), 8)
        with self.assertRaises(SourceRejected): read_xlsx(zipped(parts([(str(i), '<sheetData/>', 'visible') for i in range(9)])))
        def rows(count):
            return '<sheetData>' + ''.join(f'<row r="{i}"><c r="A{i}"><v>1</v></c></row>' for i in range(1, count + 1)) + '</sheetData>'
        # Isolate the cell guard: the stricter independent JSON budget remains
        # covered below, and no production limit is changed by this fixture.
        with patch('daon_user_api.xlsx_source._MAX_NATIVE_BYTES', 1024 * 1024):
            self.assertEqual(len(read_xlsx(zipped(parts([('cells', rows(2048), 'visible')]))).sheets[0]['cells']), 2048)
            with self.assertRaises(SourceRejected): read_xlsx(zipped(parts([('cells', rows(2049), 'visible')])))

    def test_native_utf8_exact_budget(self):
        base_size = len(read_xlsx(zipped(inline_sheet(''))).native_json.encode())
        date1904 = (32768 - base_size) % 2 == 1
        base_size = len(read_xlsx(zipped(inline_sheet('', date1904=date1904))).native_json.encode())
        text = 'x' * ((32768 - base_size) // 2)
        workbook = read_xlsx(zipped(inline_sheet(text, date1904=date1904)))
        self.assertEqual(len(workbook.native_json.encode()), 32768)
        with self.assertRaises(SourceRejected): read_xlsx(zipped(inline_sheet(text, name='시트a', date1904=date1904)))
        with self.assertRaises(SourceRejected): read_xlsx(zipped(inline_sheet('요' * 6000)))

    def test_xlsx_rejects_unsafe_or_unsupported(self):
        safe = parts()
        cases = [zipped(safe)[:-22]]
        encrypted = bytearray(zipped(safe)); struct.pack_into('<H', encrypted, 6, 1); cases.append(bytes(encrypted))
        corrupt = bytearray(zipped(safe))
        name_size, extra_size = struct.unpack_from('<HH', corrupt, 26)
        corrupt[30 + name_size + extra_size + 10] ^= 1
        cases.append(bytes(corrupt))
        cases.append(zipped(list(safe.items()) + [('xl/workbook.xml', safe['xl/workbook.xml'])]))
        for name in ['../bad.xml', '/bad.xml', 'a\\b.xml', 'a/../b.xml', 'xl/vbaProject.bin', 'xl/charts/chart1.xml', 'xl/media/image1.png', 'xl/pivotTables/pivot1.xml', 'xl/connections.xml', 'xl/externalLinks/link1.xml']:
            cases.append(zipped({**safe, name: b'unsupported'}))
        for xml in [b'<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>', b'<!DOCTYPE x SYSTEM "https://example.invalid/a"><x/>', b'<!ENTITY x "boom"><x/>']:
            cases.append(zipped({**safe, 'extra.xml': xml}))
        utf16 = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>'.encode('utf-16')
        cases.append(zipped({**safe, 'extra.xml': utf16}))
        cases.append(zipped({**safe, 'extra.XML': utf16}))
        external = f'<Relationships xmlns="{REL}"><Relationship Id="x" Type="{DOC_REL}/hyperlink" Target="https://example.invalid" TargetMode="External"/></Relationships>'.encode()
        cases.append(zipped({**safe, 'xl/worksheets/_rels/sheet1.xml.rels': external}))
        cases.append(zipped({**safe, 'padding.bin': b'A' * 200000}, compression=zipfile.ZIP_DEFLATED))
        with patch('socket.socket', side_effect=AssertionError('no network')), patch('subprocess.run', side_effect=AssertionError('no execution')):
            for raw in cases:
                with self.subTest(size=len(raw)):
                    with self.assertRaises(SourceRejected): read_xlsx(raw)

    def test_sheet_cell_roundtrip_and_view_bounds(self):
        self.assertEqual(encode_sheet_cell('납품 "계획"', 'B2:C3'), r'{"sheet":"납품 \"계획\"","range":"B2:C3"}')
        self.assertEqual(decode_sheet_cell('{"sheet":"예산","range":"B2"}'), ('예산', 'B2'))
        for locator in ['{"sheet":"예산","sheet":"다른","range":"B2"}', '{"sheet":"예산","range":"B2","extra":1}', '{"range":"B2","sheet":"예산"}', '{ "sheet":"예산","range":"B2"}', '{"sheet":"","range":"B2"}']:
            with self.subTest(locator=locator):
                with self.assertRaises(SourceRejected): decode_sheet_cell(locator)
        for cell_range in ['C3:B2', '$B$2', 'B2,C3', '[other]B2', 'Sheet!B2', 'XFE1', 'A1048577', 'A1:A257', 'B02', 'b2', 'A0']:
            with self.subTest(cell_range=cell_range):
                with self.assertRaises(SourceRejected): encode_sheet_cell('예산', cell_range)
        workbook = read_xlsx(zipped(parts()))
        view = read_sheet_cell_view(workbook, encode_sheet_cell('납품 "계획"', 'B2:C3'))
        self.assertEqual(set(view), {'source_sha256', 'sheet_name', 'sheet_visibility', 'selected_range', 'view_range', 'cells', 'merged_ranges'})
        self.assertEqual(view['view_range'], 'A1:D4'); self.assertEqual(len(view['cells']), 16)
        self.assertEqual(view['merged_ranges'], ['B3:C3'])
        self.assertIsNone(view['cells'][0]['raw_value'])
        with self.assertRaises(SourceRejected): read_sheet_cell_view(workbook, encode_sheet_cell('missing', 'B2'))
        with self.assertRaises(SourceRejected): read_sheet_cell_view(workbook, encode_sheet_cell('예산', 'XFD1048576'))
        wide = read_xlsx(zipped(parts([('wide', '<sheetData><row r="18"><c r="R18"><v>1</v></c></row><row r="256"><c r="A256"><v>1</v></c></row></sheetData>', 'visible')])))
        view = read_sheet_cell_view(wide, encode_sheet_cell('wide', 'B2:Q17'))
        self.assertEqual(view['view_range'], 'A1:R18'); self.assertEqual(len(view['cells']), 324)
        with self.assertRaises(SourceRejected): read_sheet_cell_view(wide, encode_sheet_cell('wide', 'A1:A256'))
        edge = read_xlsx(zipped(parts([('edge', '<sheetData><row r="1048576"><c r="XFD1048576"><v>1</v></c></row></sheetData>', 'visible')])))
        self.assertEqual(read_sheet_cell_view(edge, encode_sheet_cell('edge', 'XFD1048576'))['view_range'], 'XFC1048575:XFD1048576')

    def test_malformed_values_and_references_fail_closed(self):
        invalid_cells = [
            '<c r="A1"><v>NaN</v></c>', '<c r="A1"><v>Infinity</v></c>',
            '<c r="A1"><v>-Infinity</v></c>', '<c><v>1</v></c>',
            '<c r="A1"><v>1</v></c><c r="A1"><v>2</v></c>',
            '<c r="A1" t="s"><v>1</v></c>', '<c r="A1" t="s"><v>-1</v></c>',
            '<c r="A1" s="2"><v>1</v></c>', '<c r="A1" t="b"><v>yes</v></c>',
            '<c r="A1"><v>1</v><v>2</v></c>',
            '<c r="A1"><f>[other.xlsx]Sheet!A1</f><v>1</v></c>',
            '<c r="A1"><f>WEBSERVICE("https://example.invalid")</f><v>1</v></c>',
        ]
        for cell in invalid_cells:
            with self.subTest(cell=cell):
                with self.assertRaises(SourceRejected): read_xlsx(zipped(parts([('values', f'<sheetData><row r="1">{cell}</row></sheetData>', 'visible')])))

    def test_structure_is_immutable_and_malicious_text_is_only_data(self):
        workbook = read_xlsx(zipped(inline_sheet('<script>alert(1)</script>', name='<script>')))
        self.assertEqual(workbook.sheets[0]['cells'][0]['display_value'], '<script>alert(1)</script>')
        with self.assertRaises(TypeError): workbook.sheets[0]['sheet_name'] = 'modified'
        with self.assertRaises(TypeError): workbook.sheets[0]['cells'][0]['raw_value'] = 'modified'
        view = read_sheet_cell_view(workbook, encode_sheet_cell('<script>', 'A1'))
        view['cells'][0]['raw_value'] = 'local edit'
        self.assertEqual(workbook.sheets[0]['cells'][0]['raw_value'], '<script>alert(1)</script>')

    def test_known_xml_names_require_exact_namespace_and_parent(self):
        safe = parts()
        for body in [
            '<sheetData><row r="1"><x:c xmlns:x="urn:foreign" r="A1"><v>9000</v></x:c></row></sheetData>',
            '<sheetData><x:row xmlns:x="urn:foreign" r="1"><c r="A1"><v>9000</v></c></x:row></sheetData>',
            '<sheetData><row r="1"><c r="A1"><x:v xmlns:x="urn:foreign">9000</x:v></c></row></sheetData>',
            '<sheetData><wrapper><row r="1"><c r="A1"><v>9000</v></c></row></wrapper></sheetData>',
        ]:
            with self.subTest(body=body):
                with self.assertRaises(SourceRejected): read_xlsx(zipped(parts([('bad', body, 'visible')])))
        for part, old, new in [
            ('xl/workbook.xml', b'<workbookPr', b'<x:workbookPr xmlns:x="urn:foreign"'),
            ('xl/_rels/workbook.xml.rels', b'<Relationship ', b'<x:Relationship xmlns:x="urn:foreign" '),
            ('[Content_Types].xml', b'<Override ', b'<x:Override xmlns:x="urn:foreign" '),
            ('xl/styles.xml', b'<xf ', b'<x:xf xmlns:x="urn:foreign" '),
        ]:
            with self.subTest(part=part):
                with self.assertRaises(SourceRejected): read_xlsx(zipped({**safe, part: safe[part].replace(old, new)}))

    def test_utf16_structure_and_ambiguous_package_metadata(self):
        safe = parts()
        utf16 = {name: ('<?xml version="1.0" encoding="UTF-16"?>' + data.decode()).encode('utf-16') for name, data in safe.items()}
        self.assertEqual(read_xlsx(zipped(utf16)).native_json, read_xlsx(zipped(safe)).native_json)
        for overrides in [
            {'[Content_Types].xml': b'<notContentTypes/>'},
            {'xl/workbook.xml': safe['xl/workbook.xml'].replace(b'<sheets>', b'<workbookPr date1904="1"/><sheets>')},
            {'xl/worksheets/sheet1.xml': safe['xl/worksheets/sheet1.xml'].replace(b'<cols>', b'<dimension ref="XFE1"/><cols>')},
        ]:
            with self.subTest(part=list(overrides)):
                with self.assertRaises(SourceRejected): read_xlsx(zipped({**safe, **overrides}))

    def test_date_bounds_before_any_numeric_conversion(self):
        # This double prevents, rather than performs, a giant int allocation.
        for raw in ['1e100000000', '-1e100000000', '2958466']:
            with self.subTest(raw=raw):
                with patch.object(xlsx_source, 'int', side_effect=AssertionError('int before range guard'), create=True), patch.object(xlsx_source, 'float', side_effect=AssertionError('float before range guard'), create=True):
                    with self.assertRaises(SourceRejected): xlsx_source._display_date(raw, False)
        self.assertIsNone(xlsx_source._display_date('60', False))
        self.assertIsNone(xlsx_source._display_date('60.5', False))
        self.assertEqual(xlsx_source._display_date('46000.5', False), '2025-12-09T12:00:00')
        self.assertEqual(xlsx_source._display_date('0', True), '1904-01-01')
        self.assertEqual(xlsx_source._display_date('2958465', False), '9999-12-31')
        self.assertEqual(xlsx_source._display_date('2957003', True), '9999-12-31')

    def test_numeric_lexemes_are_bounded_before_decimal_processing(self):
        for raw in ['9' * 128, '1e308', '1e-324']:
            fixture = zipped(parts([('numbers', f'<sheetData><row r="1"><c r="A1"><v>{raw}</v></c></row></sheetData>', 'visible')]))
            self.assertEqual(read_xlsx(fixture).sheets[0]['cells'][0]['raw_value'], raw)
        for raw in ['1e100000000', '1e-100000000', '9' * 129]:
            fixture = zipped(parts([('numbers', f'<sheetData><row r="1"><c r="A1"><v>{raw}</v></c></row></sheetData>', 'visible')]))
            with self.subTest(raw=raw):
                with self.assertRaises(SourceRejected): read_xlsx(fixture)
