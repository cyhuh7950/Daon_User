import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { MinimalEvent, findElements, installMinimalDom, buttonByText } from './product-studio-dom.mjs';
import { createNotebookContextWorkspaceAdapter } from '../../packages/ui/src/notebook-context-adapter.js';
import { uploadSource } from '../../apps/web/lib/source-upload-api.js';
import { citationContentUrl } from '../../apps/web/lib/question-answering-api.js';

let Shell, output;
const repo = path.resolve(import.meta.dirname, '../..');
test.before(async () => {
  await import('react'); await import('react-dom/client');
  output = await mkdtemp(path.join(repo, '.drive-source-react-'));
  const { build } = await import('vite');
  await build({ configFile:false, logLevel:'silent', root:repo, build:{outDir:output, emptyOutDir:false,
    lib:{entry:path.join(repo,'packages/ui/src/product-workspace-shell.jsx'),formats:['es'],fileName:()=> 'drive.mjs'},
    rollupOptions:{external:['react','react-dom','react-dom/client']}} });
  Shell = (await import(pathToFileURL(path.join(output,'drive.mjs')).href)).ProductWorkspaceShell;
});
test.after(async () => {
  if (output && path.dirname(output) === repo && path.basename(output).startsWith('.drive-source-react-')) {
    await rm(output,{recursive:true,force:true});
  }
});
const file = (name='qa.txt', type='text/plain') => new File(['QA source text'],name,{type});
const props = node => node[Object.keys(node).find(key=>key.startsWith('__reactProps$'))];
const input = w => {
  const node=findElements(w.container,n=>n.tagName==='INPUT' && n.getAttribute('aria-label')==='로컬·Google Drive 파일 선택')[0];
  assert.ok(node,'Drive 표준 file chooser가 있어야 한다'); return node;
};
async function click(w,label) { await w.act(async()=>{ const node=buttonByText(w.container,label);assert.ok(node,label);node.dispatchEvent(new MinimalEvent('click')); }); }
async function select(w, selected) {
  await w.act(async()=>{
    const node=input(w);Object.defineProperty(node,'files',{configurable:true,value:selected?[selected]:[]});
    props(node).onChange({currentTarget:node,target:node});
  });
}
async function mount(adapter={}) {
  const dom=installMinimalDom(); const {act,createElement}=await import('react'); const {createRoot}=await import('react-dom/client');
  const container=dom.document.createElement('div');dom.document.body.appendChild(container);const root=createRoot(container);
  await act(async()=>{root.render(createElement(Shell,{workspaceId:'workspace-1',adapter}));await Promise.resolve();});
  const w={container,act,cleanup:async()=>{await act(async()=>root.unmount());dom.restore();}};
  await click(w,'＋Source 추가');await click(w,'Drive');return w;
}
const submit = {source_id:'source-1',source_version_id:'version-1',processing_run_id:'run-1'};

function textReentryContext() {
  const sourceId='src-'+'a'.repeat(32), versionId='sv-'+'a'.repeat(32), span='span-'+'b'.repeat(32);
  return {notebook_id:'notebook-current',sources:[{source_id:sourceId,source_version_id:versionId}],
    knowledge_context_ids:[],conversation_thread_ids:['thread-text'],studio_output_ids:[],output_version_ids:[],generation_settings_ids:[],source_deletion_requests:[],
    conversation:{conversation_thread_id:'thread-text',answer:{run_id:'run-text',run_result_id:'result-text',answer:'저장된 텍스트 근거 답변',insufficient:false,
      citations:[{citation_id:'citation-text',source_id:sourceId,source_version_id:versionId,evidence_span_id:span,page:1,origin:'raw_source',context_item_id:sourceId,locator:{kind:'section',value:span}}]}}};
}

test('canonical text saved answer reentry restores actual React anchor using current Notebook citation URL',async()=>{
  const context=textReentryContext(), calls=[];
  const adapter=createNotebookContextWorkspaceAdapter({citationUrl:(citation,options)=>{
    calls.push(options);return citationContentUrl('workspace-1',citation,options);
  }},context);
  const w=await mount(adapter);
  try {
    assert.match(w.container.textContent,/저장된 텍스트 근거 답변/);
    const anchor=findElements(w.container,n=>n.tagName==='A')[0];assert.ok(anchor,'재진입 Citation anchor 필요');
    assert.equal(anchor.getAttribute('href'),'/bff/api/workspaces/workspace-1/citations/citation-text/content?notebook_id=notebook-current');
    assert.deepEqual(calls,[{notebookId:'notebook-current'}]);
  }finally{await w.cleanup();}
});

test('canonical text reentry rejects forged Version/span/page/context/binding before URL restoration',async()=>{
  let calls=0;const base={citationUrl:()=>{calls++;return '/must-not-call';}};
  const changes=[
    c=>c.conversation.answer.citations[0].source_version_id='sv-'+'c'.repeat(32),
    c=>c.conversation.answer.citations[0].evidence_span_id='span-invalid',
    c=>c.conversation.answer.citations[0].locator.value='span-'+'c'.repeat(32),
    c=>c.conversation.answer.citations[0].page=2,
    c=>c.conversation.answer.citations[0].context_item_id='other-source',
    c=>c.sources=[],
    c=>c.sources[0].source_version_id='sv-'+'c'.repeat(32),
  ];
  for(const change of changes){const context=textReentryContext();change(context);
    assert.throws(()=>createNotebookContextWorkspaceAdapter(base,context),/NOTEBOOK_CONTEXT_INVALID/);
  }
  assert.equal(calls,0);
  const other=textReentryContext();other.notebook_id='notebook-other';
  const adapter=createNotebookContextWorkspaceAdapter({citationUrl:(_citation,options)=>{
    assert.equal(options.notebookId,'notebook-other');throw Error('NOTEBOOK_ACCESS_DENIED');
  }},other);
  await assert.rejects(adapter.loadNotebookConversation(),/NOTEBOOK_ACCESS_DENIED/);
});

test('other origin and legacy section keep generic restoration behavior',async()=>{
  for(const change of [c=>c.conversation.answer.citations[0].origin='daon_knowledge',
    c=>{const citation=c.conversation.answer.citations[0];citation.source_id='legacy-source';citation.source_version_id='legacy-version';citation.context_item_id='legacy-source';}]){
    const context=textReentryContext();change(context);let calls=0;
    const adapter=createNotebookContextWorkspaceAdapter({citationUrl:()=>{calls++;}},context);
    assert.equal((await adapter.loadNotebookConversation()).citations[0].content_url,undefined);assert.equal(calls,0);
  }
});

test('drive_selection_does_not_upload',async()=>{
  let calls=0;const w=await mount({uploadSource:async()=>{calls++;throw Error('SOURCE_UPLOAD_FAILED');}});
  try {await select(w,file());assert.equal(calls,0);assert.match(w.container.textContent,/qa.txt/);assert.match(w.container.textContent,/14\s*bytes/);assert.equal(buttonByText(w.container,'Drive 파일 가져오기').disabled,false);}
  finally{await w.cleanup();}
});
test('drive_cancel_and_reset',async()=>{
  const w=await mount();try {
    await select(w,file());await select(w,null);assert.equal(buttonByText(w.container,'Drive 파일 가져오기').disabled,true);
    await select(w,file());await w.act(async()=>props(input(w)).onCancel());assert.doesNotMatch(w.container.textContent,/qa.txt/);
    await select(w,file());await w.act(async()=>input(w).dispatchEvent(new MinimalEvent('click')));assert.doesNotMatch(w.container.textContent,/qa.txt/);
    await select(w,file());await click(w,'파일 업로드');await click(w,'Drive');assert.doesNotMatch(w.container.textContent,/qa.txt/);
    await select(w,file());await w.act(async()=>findElements(w.container,n=>n.getAttribute?.('aria-label')==='Source 추가 닫기')[0].dispatchEvent(new MinimalEvent('click')));
    await click(w,'＋Source 추가');assert.doesNotMatch(w.container.textContent,/qa.txt/);
  }finally{await w.cleanup();}
});
test('drive_submit_once',async()=>{
  let resolve,calls=0,received;const wait=new Promise(r=>resolve=r);
  const w=await mount({uploadSource:async f=>{calls++;received=f;return wait;},getProcessingStatus:async()=>{throw Error('PROCESSING_FAILED');}});
  try {await select(w,file('first.txt'));const last=file('last.txt');await select(w,last);
    await w.act(async()=>{const b=buttonByText(w.container,'Drive 파일 가져오기');b.dispatchEvent(new MinimalEvent('click'));b.dispatchEvent(new MinimalEvent('click'));});
    assert.equal(calls,1);assert.equal(received,last);
    await w.act(async()=>resolve(submit));
  }finally{resolve(submit);await w.cleanup();}
});
test('drive_file_rejection',async()=>{
  let calls=0;const w=await mount({uploadSource:async()=>{calls++;}});
  try {const huge=file();Object.defineProperty(huge,'size',{value:25*1024*1024+1});
    for(const invalid of [file('x.gdoc'),file('x.gsheet'),file('x.gslides'),file('x.exe'),file('x.txt',''),file('x.txt','application/vnd.google-apps.document'),new File([],'empty.txt',{type:'text/plain'}),huge]){
      await select(w,invalid);assert.equal(buttonByText(w.container,'Drive 파일 가져오기').disabled,true,invalid.name);assert.match(w.container.textContent,/지원하지|25MiB|빈 파일|MIME/);
    }assert.equal(calls,0);
  }finally{await w.cleanup();}
});
test('drive_no_path_or_origin_invention',async()=>{
  const selected=file();for(const key of ['path','webkitRelativePath','googleId'])Object.defineProperty(selected,key,{get(){throw Error('private path accessed');}});
  const w=await mount({uploadSource:async f=>{assert.equal(f,selected);throw Error('SOURCE_UPLOAD_FAILED');}});
  try {await select(w,selected);await click(w,'Drive 파일 가져오기');assert.doesNotMatch(w.container.textContent,/C:\\|fakepath|Google Drive에서 가져왔/);}
  finally{await w.cleanup();}
});
test('drive_submit_keeps_notebook_scope',async()=>{
  const calls=[];const context={notebook_id:'notebook-current',sources:[],knowledge_context_ids:[],conversation_thread_ids:[],studio_output_ids:[],output_version_ids:[],generation_settings_ids:[],source_deletion_requests:[],conversation:null};
  const adapter=createNotebookContextWorkspaceAdapter({uploadSource:(f,options)=>uploadSource('workspace-1',f,{...options,fetchImpl:async(url,init)=>{
    calls.push({url,init,bytes:await new Response(init.body).text()});return Response.json({data:{...submit,object_id:'a'.repeat(32),digest_sha256:'b'.repeat(64),byte_size:f.size,status:'accepted',replayed:false,processing_state:'accepted',job_state:'pending'}},{status:202});
  }}),getProcessingStatus:async()=>{throw Error('PROCESSING_FAILED');}},context);
  const w=await mount(adapter);try {const selected=file();await select(w,selected);await click(w,'Drive 파일 가져오기');assert.equal(calls.length,1);
    assert.equal(calls[0].init.headers['X-Notebook-Id'],'notebook-current');assert.equal(calls[0].init.headers['Content-Type'],'text/plain');assert.equal(calls[0].init.body,selected);assert.equal(calls[0].bytes,'QA source text');assert.equal(calls[0].url,'/bff/api/workspaces/workspace-1/sources');
    assert.doesNotMatch(w.container.textContent,/이해 완료/);
  }finally{await w.cleanup();}
});
test('drive_upload_failure_not_ready',async()=>{
  const selected=file();Object.defineProperty(selected,'stream',{value:()=>new ReadableStream({start(c){c.error(Error('bytes unavailable'));}})});
  let polls=0;const w=await mount({uploadSource:(f,options)=>uploadSource('workspace-1',f,{...options,notebookId:'notebook-current',fetchImpl:async(_url,init)=>{await new Response(init.body).arrayBuffer();throw Error('must not accept');}}),getProcessingStatus:async()=>{polls++;}});
  try {await select(w,selected);await click(w,'Drive 파일 가져오기');assert.equal(polls,0);assert.match(w.container.textContent,/등록하지 못했습니다/);assert.doesNotMatch(w.container.textContent,/이해 완료|등록 완료/);}
  finally{await w.cleanup();}
});
test('Drive registration waits for existing processing result and canonical ready list',async()=>{
  let accepted=false, finish, reloads=0;
  const pending=new Promise(resolve=>finish=resolve);
  const source={...submit,filename:'qa.txt',source_state:'ready',processing_state:'completed',job_state:'completed'};
  const w=await mount({
    listSources:async()=>{reloads++;return accepted?[{source_id:source.source_id,source_version_id:source.source_version_id,filename:source.filename,source_state:source.source_state,processing_state:source.processing_state,job_state:source.job_state}]:[];},
    uploadSource:async()=>submit,
    getProcessingStatus:async()=>pending,
  });
  try {
    await select(w,file());await click(w,'Drive 파일 가져오기');
    assert.match(w.container.textContent,/등록·처리 중/);
    assert.equal(findElements(w.container,n=>n.tagName==='BUTTON'&&n.textContent.includes('Version version-1')).length,0);
    accepted=true;
    await w.act(async()=>finish({...submit,source_state:'ready',processing_state:'completed',job_state:'completed',safe_error_code:null}));
    const row=findElements(w.container,n=>n.tagName==='BUTTON'&&n.textContent.includes('Version version-1'))[0];
    assert.ok(row);assert.equal(row.disabled,false);assert.ok(reloads>=2);
  }finally{finish({...submit,source_state:'failed',processing_state:'failed',job_state:'failed',safe_error_code:'PROCESSING_FAILED'});await w.cleanup();}
});
test('Drive supported formats and exact 25MiB boundary retain browser MIME',async()=>{
  const w=await mount();try {
    for(const [extension,mime] of Object.entries({pdf:'application/pdf',xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',pptx:'application/vnd.openxmlformats-officedocument.presentationml.presentation',csv:'text/csv',txt:'text/plain',md:'text/markdown',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',wav:'audio/wav'})){
      const selected=file(`QA.${extension.toUpperCase()}`,mime);Object.defineProperty(selected,'size',{value:25*1024*1024});
      await select(w,selected);assert.equal(buttonByText(w.container,'Drive 파일 가져오기').disabled,false,extension);
      assert.equal(selected.type,mime);
    }
  }finally{await w.cleanup();}
});
test('drive_retry_is_explicit and existing file tab still uploads on selection',async()=>{
  let calls=0;const w=await mount({uploadSource:async()=>{calls++;throw Error('SOURCE_UPLOAD_FAILED');}});
  try {await select(w,file());await click(w,'Drive 파일 가져오기');assert.equal(calls,1);await w.act(async()=>{await Promise.resolve();});assert.equal(calls,1);
    await click(w,'Drive 파일 가져오기');assert.equal(calls,2);await click(w,'파일 업로드');
    const node=findElements(w.container,n=>n.tagName==='INPUT'&&n.getAttribute('accept')?.includes('.txt'))[0];assert.ok(node);
    await w.act(async()=>{Object.defineProperty(node,'files',{value:[file()],configurable:true});props(node).onChange({currentTarget:node});});assert.equal(calls,3);assert.equal(node.value,'');
  }finally{await w.cleanup();}
});
