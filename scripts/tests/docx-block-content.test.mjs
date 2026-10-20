import assert from 'node:assert/strict';
import test from 'node:test';
import { isDocxBlockContent } from '../../apps/web/lib/docx-block-locator.js';

export function docxContent() {
  return {source_version_id:'sv-1',source_sha256:'a'.repeat(64),serialization_version:'docx-structure-v1',selected_block_id:'t:3:r:1:c:2:p:1',selected_start:1,selected_end:2,blocks:[
    {block_id:'p:2',ordinal:2,block_type:'paragraph',text:'',style_id:null,outline_level:null,table:null},
    {block_id:'t:3:r:1:c:2:p:1',ordinal:3,block_type:'table_cell_paragraph',text:'한😀글 <script>bad</script> https://evil',style_id:'Body',outline_level:null,table:{body_index:3,row:1,column:2,paragraph:1}},
    {block_id:'p:4',ordinal:4,block_type:'paragraph',text:'last',style_id:null,outline_level:0,table:null},
  ]};
}
test('DOCX content exact structure, table position, adjacent blocks and Unicode selected span binding', () => {
  const locator='{"block":"t:3:r:1:c:2:p:1","start":1,"end":2}';
  assert.equal(isDocxBlockContent(docxContent(),locator,'sv-1'),true);
  assert.equal(Array.from(docxContent().blocks[1].text).slice(1,2).join(''),'😀');
  assert.equal(isDocxBlockContent(docxContent(),locator,'wrong'),false);
  assert.equal(isDocxBlockContent(docxContent(),'{"block":"p:2","start":1,"end":2}','sv-1'),false);
  for (const mutate of [v=>v.extra=1,v=>v.source_sha256='sha256:'+v.source_sha256,v=>v.selected_start=true,v=>v.selected_end=2000,v=>v.blocks.splice(1,1),v=>v.blocks.push(v.blocks[0]),v=>v.blocks[0].extra=1,v=>v.blocks[0].ordinal=1,v=>v.blocks[1].table.column=1,v=>v.blocks[1].outline_level=10,v=>v.blocks[1].text='a'.repeat(2001),v=>v.blocks[1].block_id='p:3']) {
    const value=docxContent();mutate(value);assert.equal(isDocxBlockContent(value),false,String(mutate));
  }
});
test('DOCX content enforces final UTF8 32KiB bound without truncating strings', () => {
  const value=docxContent();value.blocks[1].style_id='a'.repeat(33000);
  assert.equal(isDocxBlockContent(value),false);
  value.blocks[1].style_id='';
  const base=new TextEncoder().encode(JSON.stringify(value)).length;
  value.blocks[1].style_id='a'.repeat(32768-base);
  assert.equal(new TextEncoder().encode(JSON.stringify(value)).length,32768);
  assert.equal(isDocxBlockContent(value),true);
  value.blocks[1].style_id+='a';
  assert.equal(isDocxBlockContent(value),false);
});
test('raw DOCX JSON rejects duplicate keys before native JSON.parse loses them',()=>{
  const raw=JSON.stringify(docxContent());
  assert.equal(isDocxBlockContent(raw),true);
  for(const invalid of [raw.replace('"selected_start":1','"selected_start":0,"selected_start":1'),raw.replace('"column":2','"column":1,"column":2')])
    assert.equal(isDocxBlockContent(invalid),false);
});
