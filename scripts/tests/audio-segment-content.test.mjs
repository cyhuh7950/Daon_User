import assert from 'node:assert/strict';
import test from 'node:test';
import { isAudioSegmentContent } from '../../packages/ui/src/shared-audio-segment.js';
import * as audio from '../../packages/ui/src/shared-audio-segment.js';
import { createHash } from 'node:crypto';

test('S20 audio SHA256 matches independent known vectors without WebCrypto',()=>{
  assert.equal(typeof audio.audioSourceSha256,'function');
  for(const [input,expected] of [
    ['', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'],
    ['abc', 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'],
    ['abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq', '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1'],
  ])assert.equal(audio.audioSourceSha256(new TextEncoder().encode(input)),expected);
});

test('S20 audio SHA256 hashes exact byte views across padding and multiblock boundaries',()=>{
  assert.equal(typeof audio.audioSourceSha256,'function');
  for(const size of [1,55,56,63,64,65,119,120,127,128,129,4097]){
    const raw=Uint8Array.from({length:size+17},(_,i)=>(i*131+7)%256);
    const view=raw.subarray(5,5+size),before=raw.slice();
    assert.equal(audio.audioSourceSha256(view),createHash('sha256').update(view).digest('hex'),`byte length ${size}`);
    assert.deepEqual(raw,before,'입력/주변 바이트는 변경하지 않는다');
  }
});

test('S20 audio SHA256 permits exactly 25MiB and rejects oversized or nonbyte input',()=>{
  assert.equal(typeof audio.audioSourceSha256,'function');
  const raw=new Uint8Array(25*1024*1024+1);
  assert.equal(audio.audioSourceSha256(raw.subarray(0,-1)),createHash('sha256').update(raw.subarray(0,-1)).digest('hex'));
  for(const value of [raw,null,[],new Uint16Array(2),new ArrayBuffer(2),'abc']){
    assert.throws(()=>audio.audioSourceSha256(value),/^Error: AUDIO_CONTENT_UNAVAILABLE$/);
  }
});

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
