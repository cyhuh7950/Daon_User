const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
export function pptxBlockPosition(id){
 if(typeof id!=='string')return null;
 const shape=/^s:([1-9][0-9]*):sp:([1-9][0-9]*):p:([1-9][0-9]*)$/u.exec(id);
 const table=/^s:([1-9][0-9]*):tb:([1-9][0-9]*):r:([1-9][0-9]*):c:([1-9][0-9]*):p:([1-9][0-9]*)$/u.exec(id);
 const match=shape??table;if(!match)return null;
 const values=match.slice(1).map(Number);if(!values.every(Number.isSafeInteger))return null;
 const [slide,object_id]=values, paragraph=values.at(-1);
 if(slide>16||object_id>4294967295||paragraph>128||(table&&(values[2]>64||values[3]>64)))return null;
 return {slide,object_id,paragraph,...(table?{row:values[2],column:values[3]}:{})};
}
export function parsePptxBlock(value){
 if(typeof value!=='string'||new TextEncoder().encode(value).length>256)return null;
 try{const v=JSON.parse(value);
  if(!exact(v,['block','start','end'])||Object.keys(v).join(',')!=='block,start,end'||!pptxBlockPosition(v.block)||!Number.isSafeInteger(v.start)||!Number.isSafeInteger(v.end)||v.start<0||v.end<=v.start||v.end>2000||JSON.stringify(v)!==value)return null;
  return v;
 }catch{return null;}
}
export function isPptxBlockContent(value,locatorValue,sourceVersionId){
 if(typeof value==='string'){
  if(new TextEncoder().encode(value).length>32768)return false;
  try{
   const tokens=value.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]/gu)??[],stack=[];
   for(let i=0;i<tokens.length;i++){const t=tokens[i];if(t==='{')stack.push(new Set());else if(t==='[')stack.push(null);else if(t==='}'||t===']')stack.pop();else if(t.startsWith('"')&&tokens[i+1]===':'&&stack.at(-1) instanceof Set){const k=JSON.parse(t);if(stack.at(-1).has(k))return false;stack.at(-1).add(k);}}
   value=JSON.parse(value);
  }catch{return false;}
 }
 if(!exact(value,['source_version_id','source_sha256','serialization_version','selected_block_id','selected_start','selected_end','blocks'])||typeof value.source_version_id!=='string'||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(value.source_version_id)||typeof value.source_sha256!=='string'||!/^[0-9a-f]{64}$/u.test(value.source_sha256)||value.serialization_version!=='pptx-structure-v1'||(sourceVersionId!==undefined&&sourceVersionId!==value.source_version_id))return false;
 const locator=JSON.stringify({block:value.selected_block_id,start:value.selected_start,end:value.selected_end});
 const pos=pptxBlockPosition(value.selected_block_id);
 if(!parsePptxBlock(locator)||(locatorValue!==undefined&&locatorValue!==locator)||!Array.isArray(value.blocks)||value.blocks.length<1||value.blocks.length>3)return false;
 const ids=new Set();let previous=0;
 for(const block of value.blocks){
  if(!exact(block,['block_id','ordinal','slide','object_id','block_type','text','table']))return false;
  const p=pptxBlockPosition(block.block_id);
  if(!p||p.slide!==pos.slide||block.slide!==p.slide||block.object_id!==p.object_id||ids.has(block.block_id)||!Number.isSafeInteger(block.ordinal)||block.ordinal<1||block.ordinal>128||(previous&&block.ordinal!==previous+1)||typeof block.text!=='string'||Array.from(block.text).length>2000)return false;
  if(p.row!==undefined){if(block.block_type!=='table_cell_paragraph'||!exact(block.table,['row','column','paragraph'])||['row','column','paragraph'].some(k=>block.table[k]!==p[k]))return false;}
  else if(block.block_type!=='shape_paragraph'||block.table!==null)return false;
  ids.add(block.block_id);previous=block.ordinal;
 }
 const selected=value.blocks.find(b=>b.block_id===value.selected_block_id);
 if(!selected||value.selected_end>Array.from(selected.text).length)return false;
 const index=value.blocks.indexOf(selected);if(index>1||(index===0&&value.blocks.length>2))return false;
 try{return new TextEncoder().encode(JSON.stringify(value)).length<=32768;}catch{return false;}
}
export function pptxBlockLabel(value){const p=pptxBlockPosition(parsePptxBlock(value)?.block);return p?`슬라이드 ${p.slide} · 객체 ${p.object_id}${p.row!==undefined?` · 표 행 ${p.row} · 열 ${p.column}`:''} · 문단 ${p.paragraph}`:'PPTX 구간';}
