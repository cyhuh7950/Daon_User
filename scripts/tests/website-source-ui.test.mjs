import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { MinimalEvent, findElements, installMinimalDom, buttonByText } from './product-studio-dom.mjs';
import { validWebsiteUrl } from '../../apps/web/lib/website-source-api.js';

let module, webModule, output;
test.before(async()=>{
  await import('react');
  await import('react-dom/client');
  const root=path.resolve(import.meta.dirname,'../..');
  output=await mkdtemp(path.join(root,'.website-source-react-'));
  const { build }=await import('vite');
  await build({configFile:false,logLevel:'silent',root,build:{outDir:output,emptyOutDir:false,
    lib:{entry:{website:path.join(root,'packages/ui/src/product-workspace-shell.jsx'),adapter:path.join(root,'apps/web/components/actual-workspace.jsx')},formats:['es'],fileName:(_format,name)=>`${name}.mjs`},
    rollupOptions:{external:['react','react-dom','react-dom/client']}}});
  const entry=(await readdir(output)).find(name=>name==='website.mjs');
  module=await import(pathToFileURL(path.join(output,entry)).href);
  webModule=await import(pathToFileURL(path.join(output,'adapter.mjs')).href);
});
test.after(async()=>{if(output) await rm(output,{recursive:true,force:true});});
async function mount(Component,props){
  assert.equal(typeof Component,'function','실제 웹 React 흐름 필요');
  const dom=installMinimalDom();
  const {createElement,act}=await import('react'); const {createRoot}=await import('react-dom/client');
  const container=dom.document.createElement('div');dom.document.body.appendChild(container);
  const root=createRoot(container);
  const render=async next=>act(async()=>{root.render(createElement(Component,next));await Promise.resolve();await Promise.resolve();});
  await render(props);
  return{container,act,render,cleanup:async()=>{await act(async()=>root.unmount());dom.restore();}};
}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
function change(node,value){
  // MinimalDom에는 브라우저 value tracking이 없다. 실제 React 입력 handler를
  // 같은 DOM target으로 호출하되 submit/click/effect는 실제 React로 검증한다.
  const props=node[Object.keys(node).find(key=>key.startsWith('__reactProps$'))];
  if((node.type??node.getAttribute('type'))==='checkbox')node.checked=value;else node.value=value;
  (props.onChange??props.onInput)({target:node});
}
const source={source_id:'web-source-1',source_version_id:'web-version-1',filename:'website.txt',source_state:'ready',processing_state:'accepted',job_state:'pending'};
const snapshot={snapshot_id:'snapshot-1',source_id:source.source_id,source_version_id:source.source_version_id,
  requested_url:'https://example.com/',final_url:'https://example.com/',title:'웹 제목',published_at:null,
  fetched_at:'2026-10-15T10:00:00Z',license_status:'unknown',license_url:null,raw_sha256:'a'.repeat(64),text_sha256:'b'.repeat(64),extraction_version:'website-text-v1'};

test('actual web adapter는 current Notebook의 real consumer POST/GET/query를 연결한다',async()=>{
  const original=globalThis.fetch;const calls=[];
  globalThis.fetch=async(url,init)=>{
    calls.push({url,init});
    if(init.method==='POST')return Response.json({data:{kind:'website_import',id:'snapshot-1',version:'1',attributes:{snapshot_id:'snapshot-1',source_id:source.source_id,source_version_id:source.source_version_id,source_state:'ready',replayed:false}},meta:{trace_id:'trace-1'}},{status:201});
    if(url.includes('website-snapshot'))return Response.json({data:snapshot,meta:{trace_id:'trace-1'}});
    return Response.json({data:{sources:[{...source,website:{...snapshot,text:'공개 원문'}}]},meta:{trace_id:'trace-1',workspace_id:'workspace-1'}});
  };
  try{
    const adapter=webModule.createWebProductWorkspaceAdapter('workspace-1','notebook-current');
    await adapter.importWebsite({url:'https://example.com/',publicPageConfirmed:true},{idempotencyKey:'request-1',notebookId:'wrong'});
    assert.equal(JSON.parse(calls[0].init.body).notebook_id,'notebook-current');
    await adapter.getWebsiteSnapshot(source.source_version_id,{notebookId:'wrong'});
    assert.match(calls[1].url,/notebook_id=notebook-current$/);
    await adapter.listSources({websiteSourceVersionId:source.source_version_id,notebookId:'wrong'});
    assert.match(calls[2].url,/notebook_id=notebook-current&website_source_version_id=web-version-1$/);
    assert.ok(calls.every(call=>call.url.startsWith('/bff/api/')));
  }finally{globalThis.fetch=original;}
});

test('웹 입력 확인/중복 click/실패 멱등키 보존과 입력변경 새키',async()=>{
  const calls=[];let failure=true;const wait=deferred();let completed=0;
  const adapter={validWebsiteUrl,importWebsite:async(input,options)=>{calls.push({input,options});if(failure)throw Error('WEBSITE_FETCH_FAILED');return wait.promise;}};
  const w=await mount(module.WebsiteSourceForm,{adapter,onRegistered:()=>completed++});
  try{
    const input=findElements(w.container,n=>n.tagName==='INPUT'&&(n.type??n.getAttribute('type'))==='url')[0];
    const confirm=findElements(w.container,n=>n.tagName==='INPUT'&&(n.type??n.getAttribute('type'))==='checkbox')[0];
    assert.equal(buttonByText(w.container,'웹사이트 가져오기').disabled,true);
    await w.act(async()=>{change(input,'https://example.com/');change(confirm,true);});
    await w.act(async()=>buttonByText(w.container,'웹사이트 가져오기').dispatchEvent(new MinimalEvent('click')));
    assert.match(w.container.textContent,/WEBSITE_FETCH_FAILED/);
    await w.act(async()=>buttonByText(w.container,'웹사이트 가져오기').dispatchEvent(new MinimalEvent('click')));
    assert.equal(calls[0].options.idempotencyKey,calls[1].options.idempotencyKey);
    await w.act(async()=>change(input,'https://example.com/next'));
    failure=false;
    await w.act(async()=>{const button=buttonByText(w.container,'웹사이트 가져오기');button.dispatchEvent(new MinimalEvent('click'));button.dispatchEvent(new MinimalEvent('click'));});
    assert.equal(calls.length,3);assert.notEqual(calls[2].options.idempotencyKey,calls[0].options.idempotencyKey);
    await w.cleanup();wait.resolve({source_state:'ready'});await Promise.resolve();assert.equal(completed,0);
  }finally{if(!calls[2])await w.cleanup();}
});

test('원문 escape/unknown license/닫기와 선택전환 늦은 응답 차단',async()=>{
  let delayed=false;const wait=deferred();const calls=[];
  const adapter={getWebsiteSnapshot:async()=>snapshot,listSources:async options=>{
    calls.push(options);if(delayed)return wait.promise;return[{...source,website:{...snapshot,text:'  원문\n<script>not executable</script>  '}}];}};
  const w=await mount(module.WebsiteOriginal,{adapter,source:{sourceId:source.source_id,sourceVersionId:source.source_version_id}});
  try{
    assert.match(w.container.textContent,/<script>not executable<\/script>/);assert.match(w.container.textContent,/라이선스 미상/);
    assert.equal(findElements(w.container,n=>n.tagName==='SCRIPT').length,0);
    assert.equal(calls[0].websiteSourceVersionId,source.source_version_id);
    await w.act(async()=>buttonByText(w.container,'원문 닫기').dispatchEvent(new MinimalEvent('click')));
    assert.doesNotMatch(w.container.textContent,/not executable/);
    delayed=true;
    await w.act(async()=>buttonByText(w.container,'원문 보기').dispatchEvent(new MinimalEvent('click')));
    await w.render({adapter,source:{sourceId:'web-source-other',sourceVersionId:'web-version-other'}});
    await w.act(async()=>wait.resolve([{...source,website:{...snapshot,text:'STALE SECRET'}}]));
    assert.doesNotMatch(w.container.textContent,/STALE SECRET/);
  }finally{await w.cleanup();}
});

test('실제 shell reload는 sealed snapshot 확인 후 웹 ready를 분리하고 거부시선택0',async()=>{
  for(const denied of [false,true]){
    const adapter={listSources:async()=>[source],listStudioOutputs:async()=>[],getWebsiteSnapshot:async()=>{if(denied)throw Error('WEBSITE_ACCESS_DENIED');return snapshot;}};
    const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-1',adapter});
    try{
      const button=findElements(w.container,n=>n.tagName==='BUTTON'&&n.textContent.includes('Version web-version-1'))[0];
      assert.equal(button.disabled,denied);
      if(!denied)assert.match(w.container.textContent,/웹 수집·저장 완료/);
      assert.doesNotMatch(w.container.textContent,/웹.*LLM 이해 완료/);
    }finally{await w.cleanup();}
  }
});

test('Source 목록은 검증 snapshot 제목/수집시각을 표시한다',async()=>{
  const adapter={listSources:async()=>[source],listStudioOutputs:async()=>[],getWebsiteSnapshot:async()=>snapshot};
  const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-1',adapter});
  try{
    const button=findElements(w.container,n=>n.tagName==='BUTTON'&&n.textContent.includes('Version web-version-1'))[0];
    assert.match(button.textContent,/웹 제목/);assert.match(button.textContent,/2026-10-15T10:00:00Z/);
  }finally{await w.cleanup();}
});

test('shell 웹 등록 후 canonical 목록 refetch/snapshot 확인으로 선택되고 Native Run을 만들지 않는다',async()=>{
  let registered=false,loads=0,processing=0;
  const adapter={validWebsiteUrl,importWebsite:async()=>{registered=true;return{source_id:source.source_id,source_version_id:source.source_version_id,source_state:'ready',snapshot_id:'snapshot-1',replayed:false};},
    listSources:async options=>{loads++;return registered?[{...source,...(options.websiteSourceVersionId?{website:{...snapshot,text:'등록된 실제 추출 원문 fixture'}}:{})}]:[];},
    listStudioOutputs:async()=>[],getWebsiteSnapshot:async()=>snapshot,getProcessingStatus:async()=>{processing++;throw Error('Native Run 불필요');}};
  const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-1',adapter});
  try{
    await w.act(async()=>findElements(w.container,n=>n.tagName==='BUTTON'&&n.textContent==='＋Source 추가')[0].dispatchEvent(new MinimalEvent('click')));
    await w.act(async()=>buttonByText(w.container,'웹사이트').dispatchEvent(new MinimalEvent('click')));
    await w.act(async()=>{
      change(findElements(w.container,n=>n.tagName==='INPUT'&&(n.type??n.getAttribute('type'))==='url')[0],'https://example.com/');
      change(findElements(w.container,n=>n.tagName==='INPUT'&&(n.type??n.getAttribute('type'))==='checkbox')[0],true);
    });
    await w.act(async()=>buttonByText(w.container,'웹사이트 가져오기').dispatchEvent(new MinimalEvent('click')));
    assert.ok(loads>=2);assert.equal(processing,0);
    assert.match(w.container.textContent,/웹 수집·저장 완료/);
    assert.match(w.container.textContent,/등록된 실제 추출 원문 fixture/);
  }finally{await w.cleanup();}
});
