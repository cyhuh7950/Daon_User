const exact = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const ordinal = value => typeof value === 'string' && /^s:[1-9][0-9]?$/u.test(value)
  && Number(value.slice(2)) <= 64 ? Number(value.slice(2)) : null;
const hash = value => typeof value === 'string' && /^[0-9a-f]{64}$/u.test(value);
const bytes = value => new TextEncoder().encode(value).length;

export function audioSourceSha256(input) {
  // Same bounded SHA-256 pattern as lawSha256; audio hashes the exact binary
  // view, not text or the server digest. No secure-context/WebCrypto dependency.
  // 25MiB also keeps the bit length within the low 32 bits of SHA's length field.
  if (!(input instanceof Uint8Array) || input.byteLength > 25 * 1024 * 1024) {
    throw new Error('AUDIO_CONTENT_UNAVAILABLE');
  }
  const constants=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const state=new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
  const padded=new Uint8Array(Math.ceil((input.length+9)/64)*64);padded.set(input);padded[input.length]=0x80;
  const view=new DataView(padded.buffer);view.setUint32(padded.length-4,input.length*8);
  const words=new Uint32Array(64),rotate=(value,count)=>(value>>>count)|(value<<(32-count));
  for(let offset=0;offset<padded.length;offset+=64){
    for(let i=0;i<16;i++)words[i]=view.getUint32(offset+i*4);
    for(let i=16;i<64;i++){const x=words[i-15],y=words[i-2];words[i]=(words[i-16]+(rotate(x,7)^rotate(x,18)^(x>>>3))+words[i-7]+(rotate(y,17)^rotate(y,19)^(y>>>10)))>>>0;}
    let [a,b,c,d,e,f,g,h]=state;
    for(let i=0;i<64;i++){const t1=(h+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+constants[i]+words[i])>>>0;
      const t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))>>>0;
      h=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;}
    [a,b,c,d,e,f,g,h].forEach((value,i)=>{state[i]=(state[i]+value)>>>0;});
  }
  return Array.from(state,value=>value.toString(16).padStart(8,'0')).join('');
}

export function parseAudioSegment(value) {
  if(typeof value !== 'string' || bytes(value) > 256) return null;
  try {
    const item=JSON.parse(value);
    if(!exact(item,['segment','start_ms','end_ms','start','end']) || !ordinal(item.segment)
        || Object.keys(item).join(',') !== 'segment,start_ms,end_ms,start,end'
        || !['start_ms','end_ms','start','end'].every(key=>Number.isSafeInteger(item[key]))
        || item.start_ms < 0 || item.end_ms <= item.start_ms || item.end_ms > 120000
        || item.start < 0 || item.end <= item.start || item.end > 2000 || JSON.stringify(item) !== value) return null;
    return item;
  } catch { return null; }
}

function parseUniqueJson(raw) {
  if(bytes(raw) > 32768) return null;
  const tokens=raw.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]/gu) ?? [], stack=[];
  for(let i=0;i<tokens.length;i++) {
    const token=tokens[i];
    if(token==='{') stack.push(new Set());
    else if(token==='[') stack.push(null);
    else if(token==='}' || token===']') stack.pop();
    else if(token.startsWith('"') && tokens[i+1]===':' && stack.at(-1) instanceof Set) {
      const key=JSON.parse(token);if(stack.at(-1).has(key))return null;stack.at(-1).add(key);
    }
  }
  return JSON.parse(raw);
}

export function isAudioSegmentContent(value, locatorValue, sourceVersionId) {
  try {
    if(typeof value === 'string') value=parseUniqueJson(value);
    if(!exact(value,['source_version_id','source_sha256','transcript_sha256','duration_ms',
                    'selected_segment_id','selected_start','selected_end','segments'])
        || typeof value.source_version_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(value.source_version_id)
        || (sourceVersionId !== undefined && value.source_version_id !== sourceVersionId)
        || !hash(value.source_sha256) || !hash(value.transcript_sha256)
        || !Number.isSafeInteger(value.duration_ms) || value.duration_ms < 1000 || value.duration_ms > 120000
        || !Array.isArray(value.segments) || value.segments.length < 1 || value.segments.length > 3) return false;
    let previousId=0, previousEnd=0;
    for(const segment of value.segments) {
      const id=ordinal(segment?.segment_id);
      if(!exact(segment,['segment_id','start_ms','end_ms','text']) || !id
          || (previousId && id !== previousId+1)
          || !Number.isSafeInteger(segment.start_ms) || !Number.isSafeInteger(segment.end_ms)
          || segment.start_ms < previousEnd || segment.end_ms <= segment.start_ms || segment.end_ms > value.duration_ms
          || typeof segment.text !== 'string' || !segment.text.trim() || Array.from(segment.text).length > 2000) return false;
      previousId=id;previousEnd=segment.end_ms;
    }
    const selected=value.segments.find(segment=>segment.segment_id===value.selected_segment_id);
    if(!selected || value.selected_end > Array.from(selected.text).length) return false;
    const locator=JSON.stringify({segment:selected.segment_id,start_ms:selected.start_ms,end_ms:selected.end_ms,
                                  start:value.selected_start,end:value.selected_end});
    if(!parseAudioSegment(locator) || (locatorValue !== undefined && locatorValue !== locator)) return false;
    const index=value.segments.indexOf(selected);
    if((ordinal(selected.segment_id) > 1 && index !== 1) || (ordinal(selected.segment_id) === 1 && index !== 0)) return false;
    return bytes(JSON.stringify(value)) <= 32768;
  } catch { return false; }
}
