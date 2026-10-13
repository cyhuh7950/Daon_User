import assert from 'node:assert/strict';
import test from 'node:test';
import { parseSheetCell } from '../../apps/web/lib/sheet-cell-locator.js';

test('sheet_cell accepts only compact canonical original sheet and bounded selected rectangle', () => {
  assert.deepEqual(parseSheetCell('{"sheet":"납품 "계획"","range":"B2:C3"}'), null);
  const valid = JSON.stringify({ sheet: '납품 "계획"', range: 'B2:C3' });
  assert.deepEqual(parseSheetCell(valid), { sheet: '납품 "계획"', range: 'B2:C3' });
  for (const range of ['A1', 'A1:P16', 'XFD1048576']) assert.ok(parseSheetCell(JSON.stringify({ sheet: '시트', range })));
  for (const range of ['a1', '$A$1', 'B2:A1', 'A1:A1', 'XFE1', 'A1048577', 'A1:Q16', 'A1,B2', 'name', '[other]A1']) assert.equal(parseSheetCell(JSON.stringify({ sheet: '시트', range })), null, range);
  for (const value of ['{"range":"A1","sheet":"시트"}', '{"sheet":"시트", "range":"A1"}', '{"sheet":"시트","sheet":"시트","range":"A1"}', '{"sheet":"시트","range":"A1","x":1}', '{"sheet":"../시트","range":"A1"}', '{"sheet":"a\\n","range":"A1"}']) assert.equal(parseSheetCell(value), null, value);
});
