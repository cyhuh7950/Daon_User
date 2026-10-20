import assert from 'node:assert/strict';
import test from 'node:test';
import { parseDocxBlock } from '../../apps/web/lib/docx-block-locator.js';

test('DOCX compact block locator preserves code point offsets and bounded original positions', () => {
  for (const block of ['p:1','p:64','t:3:r:1:c:2:p:1','t:64:r:64:c:64:p:128']) {
    const value = JSON.stringify({block,start:0,end:2000});
    assert.deepEqual(parseDocxBlock(value), {block,start:0,end:2000});
  }
  for (const block of ['p:0','p:01','p:65','t:1:r:65:c:1:p:1','t:1:r:1:c:1:p:129','../word','p:1#page=1'])
    assert.equal(parseDocxBlock(JSON.stringify({block,start:0,end:1})),null,block);
  for (const value of ['{"block":"p:1", "start":0,"end":1}', '{"start":0,"block":"p:1","end":1}', '{"block":"p:1","block":"p:1","start":0,"end":1}', '{"block":"p:1","start":false,"end":1}', '{"block":"p:1","start":0,"end":"1"}', '{"block":"p:1","start":0,"end":1,"extra":0}'])
    assert.equal(parseDocxBlock(value),null,value);
  for (const [start,end] of [[-1,1],[1,1],[2,1],[0,2001],[0,1.5]])
    assert.equal(parseDocxBlock(JSON.stringify({block:'p:1',start,end})),null);
});
