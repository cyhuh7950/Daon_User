import assert from 'node:assert/strict';
import test from 'node:test';
import { isAudioSegmentContent } from '../../packages/ui/src/shared-audio-segment.js';

const content = () => ({source_version_id:'sv-1',source_sha256:'a'.repeat(64),transcript_sha256:'b'.repeat(64),
  duration_ms:3000,selected_segment_id:'s:2',selected_start:1,selected_end:2,segments:[
    {segment_id:'s:1',start_ms:0,end_ms:1000,text:'first'},
    {segment_id:'s:2',start_ms:1000,end_ms:2000,text:'한😀글 <script>plain</script>'},
    {segment_id:'s:3',start_ms:2000,end_ms:3000,text:'last'}]});
const locator='{"segment":"s:2","start_ms":1000,"end_ms":2000,"start":1,"end":2}';
test('audio transcript binds hash, version, adjacent segments, actual times and Unicode',()=>{
  assert.equal(isAudioSegmentContent(content(),locator,'sv-1'),true);
  assert.equal(isAudioSegmentContent(content(),locator,'other'),false);
  for(const mutate of [v=>v.extra=0,v=>v.source_sha256='invalid',v=>v.transcript_sha256='sha256:'+v.transcript_sha256,
      v=>v.duration_ms=120001,v=>v.selected_start=false,v=>v.selected_end=2000,
      v=>v.segments[1].end_ms=3001,v=>v.segments[1].start_ms=999,v=>v.segments[1].text='a'.repeat(2001),
      v=>v.segments[2].segment_id='s:4',v=>v.segments[0].segment_id='s:2',v=>v.segments.splice(1,1)]){
    const value=content();mutate(value);assert.equal(isAudioSegmentContent(value,locator,'sv-1'),false);
  }
  const raw=JSON.stringify(content());
  assert.equal(isAudioSegmentContent(raw,locator,'sv-1'),true);
  assert.equal(isAudioSegmentContent(raw.replace('"start_ms":1000','"start_ms":1,"start_ms":1000'),locator,'sv-1'),false);
  assert.equal(isAudioSegmentContent(' '.repeat(32769)+raw),false);
});
