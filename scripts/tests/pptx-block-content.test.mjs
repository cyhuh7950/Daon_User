import assert from 'node:assert/strict';
import test from 'node:test';
import { isPptxBlockContent } from '../../apps/web/lib/pptx-block-locator.js';
export const pptxContent=()=>({source_version_id:'sv-1',source_sha256:'a'.repeat(64),serialization_version:'pptx-structure-v1',selected_block_id:'s:2:tb:3:r:1:c:2:p:1',selected_start:1,selected_end:2,blocks:[{block_id:'s:2:tb:3:r:1:c:2:p:1',ordinal:4,slide:2,object_id:3,block_type:'table_cell_paragraph',text:'가😀나 <script>bad</script>',table:{row:1,column:2,paragraph:1}}]});
test('PPTX original range, same slide, actual fields and source binding',()=>{
 const value=pptxContent(), locator=JSON.stringify({block:value.selected_block_id,start:1,end:2});
 assert.equal(isPptxBlockContent(value,locator,'sv-1'),true);
 assert.equal(Array.from(value.blocks[0].text).slice(1,2).join(''),'😀');
 assert.equal(isPptxBlockContent(value,locator,'sv-other'),false);
 for(const mutate of [v=>v.extra=1,v=>v.blocks[0].slide=1,v=>v.blocks[0].object_id=2,v=>v.blocks[0].table.column=1,v=>v.blocks[0].text='a',v=>v.selected_start=true,v=>v.blocks=[],v=>v.blocks.push({...v.blocks[0]}),v=>v.source_sha256='invalid',v=>v.blocks[0].text='a'.repeat(2001)]){const next=pptxContent();mutate(next);assert.equal(isPptxBlockContent(next),false,String(mutate));}
 const raw=JSON.stringify(value);assert.equal(isPptxBlockContent(raw),true);
 assert.equal(isPptxBlockContent(raw.replace('"slide":2','"slide":1,"slide":2')),false);
 assert.equal(isPptxBlockContent(raw+' '.repeat(32768)),false);
 const mixed=pptxContent();mixed.blocks.push({block_id:'s:3:sp:2:p:1',ordinal:5,slide:3,object_id:2,block_type:'shape_paragraph',text:'next',table:null});assert.equal(isPptxBlockContent(mixed),false);
});
