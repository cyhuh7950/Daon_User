import assert from 'node:assert/strict';
import test from 'node:test';
import { parseAudioSegment } from '../../packages/ui/src/shared-audio-segment.js';

test('audio time locator preserves canonical segment, milliseconds and code point offsets', () => {
  const value = {segment:'s:64',start_ms:119000,end_ms:120000,start:0,end:2000};
  assert.deepEqual(parseAudioSegment(JSON.stringify(value)), value);
  for(const change of [{segment:'s:65'},{segment:'s:01'},{start_ms:true},{start_ms:-1},
      {end_ms:120001},{end_ms:119000},{start:0.5},{end:2001},{extra:0}])
    assert.equal(parseAudioSegment(JSON.stringify({...value,...change})),null);
  for(const raw of ['{"segment":"s:1", "start_ms":0,"end_ms":1000,"start":0,"end":1}',
      '{"segment":"s:1","segment":"s:1","start_ms":0,"end_ms":1000,"start":0,"end":1}'])
    assert.equal(parseAudioSegment(raw),null);
});
