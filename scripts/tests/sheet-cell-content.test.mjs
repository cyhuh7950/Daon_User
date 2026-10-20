import assert from 'node:assert/strict';
import test from 'node:test';
import { isSheetCellContent } from '../../apps/web/lib/sheet-cell-locator.js';

export function cellContent() {
  return { source_version_id: 'version-1', source_sha256: 'a'.repeat(64), sheet_name: '시트', sheet_visibility: 'visible', selected_range: 'A1', view_range: 'A1:B2', cells: ['A1','B1','A2','B2'].map(address => ({ address, value_type: 'blank', raw_value: null, display_value: null, formula: null, cached_value: null, number_format: null, hidden_row: false, hidden_column: false })), merged_ranges: [] };
}
test('original cells validate exact fields, complete unique preview and citation binding without executing strings', () => {
  const value = cellContent();
  assert.equal(isSheetCellContent(value, '{"sheet":"시트","range":"A1"}', 'version-1'), true);
  value.cells[0] = { ...value.cells[0], value_type: 'string', raw_value: '<img onerror=alert(1)>', display_value: '<script>evil</script>', formula: 'HYPERLINK("https://evil",1)', cached_value: 'saved' };
  assert.equal(isSheetCellContent(value), true);
  assert.equal(isSheetCellContent(value, '{"sheet":"다른시트","range":"A1"}', 'version-1'), false);
  assert.equal(isSheetCellContent(value, '{"sheet":"시트","range":"A1"}', 'version-other'), false);
  const changes = [v => v.cells.push(v.cells[0]), v => v.cells.pop(), v => v.cells[0].address='C1', v => v.cells[0].hidden_row=1, v => v.cells[0].raw_value=57, v => v.cells[0].extra=true, v => v.extra=true, v => v.source_sha256='bad', v => v.source_version_id='', v => v.view_range='A1:C2', v => v.selected_range='A1:Q16', v => v.merged_ranges=['XFE1'], v => v.merged_ranges=['Z99:Z100'], v => v.merged_ranges=['A1:B2','A1:B2']];
  for (const change of changes) { const invalid=structuredClone(value); change(invalid); assert.equal(isSheetCellContent(invalid), false, String(change)); }
});

test('256 selected and 324 preview bounds both apply to actual complete cells', () => {
  const value = cellContent();
  value.selected_range = 'B2:Q17'; value.view_range = 'A1:R18';
  value.cells = Array.from({ length: 324 }, (_, index) => ({ ...cellContent().cells[0], address: `${String.fromCharCode(65 + index % 18)}${Math.floor(index / 18) + 1}` }));
  assert.equal(isSheetCellContent(value), true);
  const oversized = structuredClone(value); oversized.view_range = 'A1:S18';
  assert.equal(isSheetCellContent(oversized), false);
  const narrow = cellContent(); narrow.selected_range = 'A1:A256'; narrow.view_range = 'A1:B257';
  narrow.cells = Array.from({ length: 514 }, (_, index) => ({ ...cellContent().cells[0], address: `${index % 2 ? 'B' : 'A'}${Math.floor(index / 2) + 1}` }));
  assert.equal(isSheetCellContent(narrow), false, '256 selected is insufficient if expanded preview exceeds 324');
});
