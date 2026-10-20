import assert from 'node:assert/strict';
import test from 'node:test';
import { parsePptxBlock } from '../../apps/web/lib/pptx-block-locator.js';
test('PPTX canonical bounded slide/object/table/codepoint locator',()=>{
  const value='{"block":"s:2:tb:4294967295:r:1:c:2:p:1","start":1,"end":2}';
  assert.deepEqual(parsePptxBlock(value),JSON.parse(value));
  for(const invalid of [value+' ',value.replace('4294967295','4294967296'),value.replace('s:2','s:17'),value.replace('c:2','c:65'),value.replace('p:1','p:129'),value.replace('"start":1','"start":true'),value.replace('"end":2','"end":2001'),value.replace('"start":1','"start":2'),value.replace('"start":1','"start":0,"start":1')]) assert.equal(parsePptxBlock(invalid),null);
});
