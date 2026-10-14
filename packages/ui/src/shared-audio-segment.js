const exact = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const ordinal = value => typeof value === 'string' && /^s:[1-9][0-9]?$/u.test(value)
  && Number(value.slice(2)) <= 64 ? Number(value.slice(2)) : null;
const hash = value => typeof value === 'string' && /^[0-9a-f]{64}$/u.test(value);
const bytes = value => new TextEncoder().encode(value).length;

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
