const exact = (value, keys) => value !== null && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));

function blockPosition(block) {
  if (typeof block !== "string") return null;
  const match = /^(?:p:([1-9][0-9]?)|t:([1-9][0-9]?):r:([1-9][0-9]?):c:([1-9][0-9]?):p:([1-9][0-9]{0,2}))$/u.exec(block);
  if (!match) return null;
  if (match[1]) return Number(match[1]) <= 64 ? {body_index:Number(match[1])} : null;
  const [body_index,row,column,paragraph] = match.slice(2).map(Number);
  return Math.max(body_index,row,column) <= 64 && paragraph <= 128 ? {body_index,row,column,paragraph} : null;
}

export function parseDocxBlock(value) {
  if (typeof value !== "string" || new TextEncoder().encode(value).length > 256) return null;
  try {
    const item=JSON.parse(value);
    if (!exact(item,["block","start","end"]) || Object.keys(item).join(',') !== 'block,start,end'
        || !blockPosition(item.block) || !Number.isSafeInteger(item.start) || !Number.isSafeInteger(item.end)
        || item.start < 0 || item.end <= item.start || item.end > 2000 || JSON.stringify(item) !== value) return null;
    return item;
  } catch { return null; }
}

export function isDocxBlockContent(value, locatorValue, sourceVersionId) {
  if (typeof value === "string") {
    if (new TextEncoder().encode(value).length > 32768) return false;
    try {
      // JSON.parse checks syntax; scan object keys first so duplicate keys cannot disappear.
      const tokens=value.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]/gu) ?? [];
      const stack=[];
      for(let index=0;index<tokens.length;index++){
        const token=tokens[index];
        if(token==="{")stack.push(new Set());
        else if(token==="[")stack.push(null);
        else if(token==="}"||token==="]")stack.pop();
        else if(token.startsWith('"')&&tokens[index+1]===":"&&stack.at(-1) instanceof Set){
          const key=JSON.parse(token),keys=stack.at(-1);if(keys.has(key))return false;keys.add(key);
        }
      }
      value=JSON.parse(value);
    } catch { return false; }
  }
  if (!exact(value,["source_version_id","source_sha256","serialization_version","selected_block_id","selected_start","selected_end","blocks"])
      || typeof value.source_version_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(value.source_version_id)
      || typeof value.source_sha256 !== "string" || !/^[0-9a-f]{64}$/u.test(value.source_sha256)
      || value.serialization_version !== "docx-structure-v1"
      || (sourceVersionId !== undefined && value.source_version_id !== sourceVersionId)) return false;
  const locator=JSON.stringify({block:value.selected_block_id,start:value.selected_start,end:value.selected_end});
  if (!parseDocxBlock(locator) || (locatorValue !== undefined && locator !== locatorValue)
      || !Array.isArray(value.blocks) || value.blocks.length < 1 || value.blocks.length > 3) return false;
  const ids=new Set(); let previous=0;
  for (const block of value.blocks) {
    if (!exact(block,["block_id","ordinal","block_type","text","style_id","outline_level","table"])) return false;
    const pos=blockPosition(block.block_id);
    if (!pos || ids.has(block.block_id) || !Number.isSafeInteger(block.ordinal) || block.ordinal < 1 || block.ordinal > 128
        || (previous && block.ordinal !== previous+1) || typeof block.text !== "string" || Array.from(block.text).length > 2000
        || (block.style_id !== null && typeof block.style_id !== "string")
        || (block.outline_level !== null && (!Number.isSafeInteger(block.outline_level) || block.outline_level < 0 || block.outline_level > 9))) return false;
    if (block.block_id.startsWith('p:')) {
      if (block.block_type !== "paragraph" || block.table !== null) return false;
    } else if (block.block_type !== "table_cell_paragraph" || !exact(block.table,["body_index","row","column","paragraph"])
        || Object.keys(pos).some(key => block.table[key] !== pos[key])) return false;
    ids.add(block.block_id); previous=block.ordinal;
  }
  const selected=value.blocks.find(block=>block.block_id===value.selected_block_id);
  if (!selected || value.selected_end > Array.from(selected.text).length) return false;
  const index=value.blocks.indexOf(selected);
  if ((selected.ordinal > 1 && index !== 1) || (selected.ordinal === 1 && index !== 0)) return false;
  try { return new TextEncoder().encode(JSON.stringify(value)).length <= 32768; } catch { return false; }
}
