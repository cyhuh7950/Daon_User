import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp,readdir,rm } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { MinimalEvent,findElements,installMinimalDom,buttonByText } from './product-studio-dom.mjs';
import { createNotebookContextWorkspaceAdapter } from '../../packages/ui/src/notebook-context-adapter.js';
import { citationContentUrl } from '../../apps/web/lib/question-answering-api.js';
let module,webModule,output;
test.before(async()=>{
  await import('react');await import('react-dom/client');
  const root=path.resolve(import.meta.dirname,'../..');output=await mkdtemp(path.join(root,'.open-law-react-'));
  const {build}=await import('vite');
  await build({configFile:false,logLevel:'silent',root,build:{outDir:output,emptyOutDir:false,
    lib:{entry:{law:path.join(root,'packages/ui/src/product-workspace-shell.jsx'),adapter:path.join(root,'apps/web/components/actual-workspace.jsx')},formats:['es'],fileName:(_format,name)=> `${name}.mjs`},
    rollupOptions:{external:['react','react-dom','react-dom/client']}}});
  module=await import(pathToFileURL(path.join(output,(await readdir(output)).find(name=>name==='law.mjs'))).href);
  webModule=await import(pathToFileURL(path.join(output,'adapter.mjs')).href);
});

test('법령 카드 선택은 현재 Notebook의 서버 검증 Version만 표시한다',async()=>{
  const adapter={listSources:async()=>[item,{source_id:'src-ordinary',source_version_id:'sv-ordinary',filename:'ordinary.txt',source_state:'ready',processing_state:'completed',job_state:'completed'}],
    listConnectors:async()=>[{...connector,status:'connected',source_count:1}],getOpenLawSnapshot:async()=>({snapshot,text})};
  const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-law',adapter});
  try{
    assert.ok(w.container.textContent.includes('ordinary.txt'));
    await w.act(async()=>findElements(w.container,n=>n.tagName==='BUTTON' && n.getAttribute('title')==='연결형 Source 선택')[0].dispatchEvent(new MinimalEvent('click')));
    assert.ok(!w.container.textContent.includes('ordinary.txt'));
    await click(w,'모든 Source 보기');assert.ok(w.container.textContent.includes('ordinary.txt'));
  }finally{await w.cleanup();}
});

test('법령 저장 Citation은 wrong Version/span/current Notebook binding을 복원하지 않는다',()=>{
  const context={notebook_id:'notebook-law',sources:[{source_id:source,source_version_id:version}],knowledge_context_ids:[],conversation_thread_ids:['thread-law'],studio_output_ids:[],output_version_ids:[],generation_settings_ids:[],source_deletion_requests:[],
    conversation:{conversation_thread_id:'thread-law',answer:{run_id:'run-law',run_result_id:'result-law',answer:'복원',insufficient:false,citations:[{citation_id:'cite-law',source_id:source,source_version_id:version,evidence_span_id:span,page:1,origin:'raw_source',context_item_id:source,locator:{kind:'section',value:span}}]}}};
  for(const mutate of [c=>{c.sources=[];},c=>{c.conversation.answer.citations[0].source_version_id='law-version-'+'c'.repeat(32);},c=>{c.conversation.answer.citations[0].locator.value='law-span-'+'c'.repeat(32);},c=>{c.conversation.answer.citations[0].context_item_id='other';},c=>{c.conversation.answer.citations[0].page=2;}]){
    const invalid=structuredClone(context);mutate(invalid);assert.throws(()=>createNotebookContextWorkspaceAdapter({citationUrl:()=>{throw Error('URL 생성 금지');}},invalid),/NOTEBOOK_CONTEXT_INVALID/);
  }
});
test.after(async()=>{if(output)await rm(output,{recursive:true,force:true});});
async function mount(Component,props){
  assert.equal(typeof Component,'function','실제 법령 React consumer가 필요하다');
  const dom=installMinimalDom(),{createElement,act}=await import('react'),{createRoot}=await import('react-dom/client');
  const container=dom.document.createElement('div');dom.document.body.appendChild(container);const root=createRoot(container);
  const render=async next=>act(async()=>{root.render(createElement(Component,next));await Promise.resolve();await Promise.resolve();});
  await render(props);return{container,act,render,cleanup:async()=>{await act(async()=>root.unmount());dom.restore();}};
}
function change(node,value){assert.ok(node,'입력 필요');const props=node[Object.keys(node).find(key=>key.startsWith('__reactProps$'))];if((node.type??node.getAttribute('type'))==='checkbox')node.checked=value;else node.value=value;(props.onChange??props.onInput)({target:node});}
const click=(w,text)=>w.act(async()=>buttonByText(w.container,text).dispatchEvent(new MinimalEvent('click')));
const input=(w,label)=>findElements(w.container,n=>n.tagName==='INPUT'&&n.getAttribute('aria-label')===label)[0];
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const source='law-source-'+'a'.repeat(32),version='law-version-'+'a'.repeat(32),span='law-span-'+'b'.repeat(32);
const text='제1조 합성 원문\n<script>실행 금지</script>';
const law={mst:'107433',law_id:'011268',title:'합성 시험법',effective_date:'20100830',promulgation_date:null,ministry:null,article_code:null};
const preview={law,text,raw_sha256:'a'.repeat(64),text_sha256:createHash('sha256').update(text).digest('hex'),fetched_at:'2026-10-16T00:00:00Z',extraction_version:'open-law-text-v1'};
const snapshot={snapshot_id:'law-snapshot-'+'a'.repeat(32),connector_id:'mcp-open-law-go-kr',source_id:source,source_version_id:version,...law,
  origin_uri:'https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=107433&efYd=20100830',fetched_at:preview.fetched_at,raw_sha256:preview.raw_sha256,text_sha256:preview.text_sha256,extraction_version:preview.extraction_version,license_status:'unknown'};
const connector={connector_id:'mcp-open-law-go-kr',kind:'mcp',name:'국가법령정보센터',status:'unavailable',source_count:0};
const item={source_id:source,source_version_id:version,filename:'law.txt',source_state:'ready',processing_state:'accepted',job_state:'pending'};
async function searchAndSelect(w){await w.act(async()=>{change(input(w,'공개 법령명'),'민법');change(input(w,'공개 법령명 전송 확인'),true);});await click(w,'법령 검색');await click(w,'합성 시험법 · 시행 20100830');}

test('law_search_preview_explicit_import_react',async()=>{
  const calls=[];let imported=0;
  const adapter={searchOpenLaw:async value=>{calls.push(['search',value]);return{items:[law],page:1,total_count:1};},previewOpenLaw:async value=>{calls.push(['preview',value]);return preview;},importOpenLaw:async(value,options)=>{calls.push(['import',value,options]);return{source_version_id:version,source_state:'ready'};}};
  const w=await mount(module.OpenLawSourceForm,{adapter,connector,onImported:()=>imported++});
  try{await searchAndSelect(w);assert.equal(imported,0);await click(w,'본문 미리보기');assert.equal(imported,0);assert.ok(w.container.textContent.includes(text));await click(w,'Notebook에 가져오기');assert.equal(imported,1);assert.deepEqual(calls.map(call=>call[0]),['search','preview','import']);assert.equal(calls[2][1].expected_text_sha256,preview.text_sha256);}finally{await w.cleanup();}
});

test('법령 검색·미리보기는 공포일과 소관부처 응답값 또는 미제공을 표시한다',async t=>{
  for(const [name,fields,want] of [
    ['provided',{promulgation_date:'20100101',ministry:'법무부'},'공포일 20100101 · 소관부처 법무부'],
    ['not-provided',{promulgation_date:null,ministry:null},'공포일 미제공 · 소관부처 미제공'],
  ])await t.test(name,async()=>{
    const selected={...law,...fields};
    const adapter={searchOpenLaw:async()=>({items:[selected],page:1,total_count:1}),previewOpenLaw:async()=>({...preview,law:selected})};
    const w=await mount(module.OpenLawSourceForm,{adapter,connector});
    try{
      await searchAndSelect(w);
      const row=findElements(w.container,n=>n.tagName==='LI')[0];
      assert.equal(findElements(row,n=>n.tagName==='BUTTON')[0].textContent,'합성 시험법 · 시행 20100830');
      assert.equal(findElements(row,n=>n.tagName==='SMALL')[0]?.textContent,want);
      await click(w,'본문 미리보기');
      assert.ok(findElements(w.container,n=>n.tagName==='P' && n.textContent.includes(want)).length===1);
    }finally{await w.cleanup();}
  });
});

test('law_no_duplicate_or_stale_notebook_submit',async()=>{
  const wait=deferred();let calls=0,options;
  const adapter={searchOpenLaw:async(_,o)=>{calls++;options=o;return wait.promise;}};
  const w=await mount(module.OpenLawSourceForm,{adapter,connector});
  try{await w.act(async()=>{change(input(w,'공개 법령명'),'민법');change(input(w,'공개 법령명 전송 확인'),true);});
    await w.act(async()=>{const b=buttonByText(w.container,'법령 검색');b.dispatchEvent(new MinimalEvent('click'));b.dispatchEvent(new MinimalEvent('click'));});assert.equal(calls,1);
    await w.render({adapter:{},connector});assert.equal(options.signal.aborted,true);await w.act(async()=>wait.resolve({items:[law],page:1,total_count:1}));assert.ok(!w.container.textContent.includes('합성 시험법'));
  }finally{await w.cleanup();}
});

test('law_snapshot_reentry_and_citation',async()=>{
  const context={notebook_id:'notebook-law',sources:[{source_id:source,source_version_id:version}],knowledge_context_ids:[],conversation_thread_ids:['thread-law'],studio_output_ids:[],output_version_ids:[],generation_settings_ids:[],source_deletion_requests:[],
    conversation:{conversation_thread_id:'thread-law',answer:{run_id:'run-law',run_result_id:'result-law',answer:'복원 답변',insufficient:false,citations:[{citation_id:'citation-law',source_id:source,source_version_id:version,evidence_span_id:span,page:1,origin:'raw_source',context_item_id:source,locator:{kind:'section',value:span}}]}}};
  const adapter=createNotebookContextWorkspaceAdapter({listSources:async()=>[item],getOpenLawSnapshot:async()=>({snapshot,text}),citationUrl:(citation,options)=>citationContentUrl('workspace-law',citation,options)},context);
  const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-law',adapter});
  try{const anchor=findElements(w.container,n=>n.tagName==='A')[0];assert.ok(anchor);assert.equal(anchor.getAttribute('href'),'/bff/api/workspaces/workspace-law/citations/citation-law/content?notebook_id=notebook-law');assert.ok(w.container.textContent.includes('법령 수집·저장 완료'));}finally{await w.cleanup();}
  const original=await mount(module.OpenLawOriginal,{adapter,source:{sourceId:source,sourceVersionId:version}});
  try{assert.ok(original.container.textContent.includes(text));assert.equal(findElements(original.container,n=>n.tagName==='SCRIPT').length,0);assert.ok(original.container.textContent.includes('라이선스 미상'));}finally{await original.cleanup();}
});

test('law_unavailable_disables_consumption',async()=>{
  const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-law',adapter:{listSources:async()=>[{...item,processing_state:'completed',job_state:'completed'}],getOpenLawSnapshot:async()=>{throw Error('OPEN_LAW_ACCESS_DENIED');}}});
  try{const row=findElements(w.container,n=>n.tagName==='BUTTON'&&n.textContent.includes('law.txt')&&n.getAttribute('aria-pressed')!==null)[0];assert.ok(row);assert.equal(row.disabled,true);assert.ok(w.container.textContent.includes('법령 사용 불가'));}finally{await w.cleanup();}
});

test('law_unregister_scope_confirmation',async()=>{
  const confirmations=[];let removed=0;const prior=globalThis.confirm;globalThis.confirm=value=>{confirmations.push(value);return false;};
  const adapter={listSources:async()=>[item],listConnectors:async()=>[connector],getOpenLawSnapshot:async()=>({snapshot,text}),listConnectorSources:async()=>[{source_id:source},{source_id:'law-source-other'}],unregisterConnector:async()=>removed++};
  const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-law',adapter});
  try{const button=findElements(w.container,n=>n.tagName==='BUTTON'&&n.getAttribute('aria-label')==='국가법령정보센터 연결 삭제')[0];assert.ok(button);await w.act(async()=>button.dispatchEvent(new MinimalEvent('click')));assert.equal(removed,0);assert.equal(confirmations.length,1);assert.match(confirmations[0],/2/);assert.match(confirmations[0],/본인/);}finally{await w.cleanup();globalThis.confirm=prior;}
});

test('실제 Web adapter와 Notebook wrapper는 네 경로에 현재 Notebook만 결속한다',async()=>{
  const prior=globalThis.fetch,calls=[];
  globalThis.fetch=async(url,init)=>{
    calls.push({url,init});
    const data=url.includes('law-search')?{items:[Object.fromEntries(Object.entries(law).filter(([key])=>key!=='article_code'))],page:1,total_count:1}
      :url.includes('law-preview')?preview:url.includes('law-imports')?{kind:'open_law_import',id:snapshot.snapshot_id,version:'1',attributes:{snapshot_id:snapshot.snapshot_id,source_id:source,source_version_id:version,source_state:'ready',replayed:false}}:{snapshot,text};
    return Response.json({data,meta:{trace_id:'trace-law'}},{status:url.includes('law-imports')?201:200});
  };
  try{
    const context={notebook_id:'notebook-current',sources:[{source_id:source,source_version_id:version}],knowledge_context_ids:[],conversation_thread_ids:[],studio_output_ids:[],output_version_ids:[],generation_settings_ids:[],source_deletion_requests:[],conversation:null};
    const adapter=createNotebookContextWorkspaceAdapter(webModule.createWebProductWorkspaceAdapter('workspace-law','notebook-current'),context);
    await adapter.searchOpenLaw({query:'민법',page:1,public_query_confirmed:true},{notebookId:'attacker'});
    await adapter.previewOpenLaw({mst:law.mst,effective_date:law.effective_date,article_code:null},{notebookId:'attacker'});
    await adapter.importOpenLaw({mst:law.mst,effective_date:law.effective_date,article_code:null,expected_raw_sha256:preview.raw_sha256,expected_text_sha256:preview.text_sha256},{idempotencyKey:'law-explicit-current-0001',notebookId:'attacker'});
    await adapter.getOpenLawSnapshot(version,{notebookId:'attacker'});
    assert.equal(calls.length,4);assert.ok(calls.every(call=>call.url.startsWith('/bff/api/')));
    for(const call of calls.slice(0,3))assert.equal(JSON.parse(call.init.body).notebook_id,'notebook-current');
    assert.ok(calls[3].url.endsWith('notebook_id=notebook-current'));
  }finally{globalThis.fetch=prior;}
});

test('끊긴 법령 ↻는 외부조회 없는 reconnect 후에만 명시 검색 화면을 열고 성공 카드를 갱신한다',async()=>{
  const events=[];let status='disconnected',lists=0;
  const adapter={listSources:async()=>[],listConnectors:async()=>{lists++;return[{...connector,status,endpoint_label:'www.law.go.kr'}];},
    reconnectConnector:async id=>{events.push('reconnect');assert.equal(id,'mcp-open-law-go-kr');status='unavailable';return{...connector,status};},
    searchOpenLaw:async()=>{events.push('search');status='connected';return{items:[law],page:1,total_count:1};}};
  const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-law',adapter});
  try{
    const reconnect=findElements(w.container,n=>n.tagName==='BUTTON'&&n.getAttribute('aria-label')==='국가법령정보센터 재연결')[0];
    await w.act(async()=>reconnect.dispatchEvent(new MinimalEvent('click')));
    assert.deepEqual(events,['reconnect']);assert.ok(input(w,'공개 법령명'));
    await w.act(async()=>{change(input(w,'공개 법령명'),'민법');change(input(w,'공개 법령명 전송 확인'),true);});
    await click(w,'법령 검색');assert.deepEqual(events,['reconnect','search']);assert.ok(lists>=2);
    await w.act(async()=>findElements(w.container,n=>n.tagName==='BUTTON'&&n.getAttribute('aria-label')==='Source 추가 닫기')[0].dispatchEvent(new MinimalEvent('click')));
    const card=findElements(w.container,n=>n.tagName==='BUTTON' && n.getAttribute('title')==='연결형 Source 선택')[0];assert.ok(card);assert.equal(card.disabled,false);
  }finally{await w.cleanup();}
});

test('법령 reconnect 실패는 검색 화면이나 자동 외부 재전송을 만들지 않는다',async()=>{
  let searches=0;
  const adapter={listSources:async()=>[],listConnectors:async()=>[{...connector,status:'disconnected'}],reconnectConnector:async()=>{throw Error('CONNECTOR_NOT_FOUND');},searchOpenLaw:async()=>searches++};
  const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-law',adapter});
  try{
    await w.act(async()=>findElements(w.container,n=>n.tagName==='BUTTON'&&n.getAttribute('aria-label')==='국가법령정보센터 재연결')[0].dispatchEvent(new MinimalEvent('click')));
    assert.equal(input(w,'공개 법령명'),undefined);assert.equal(searches,0);assert.match(w.container.textContent,/연결형 Source를 불러오지 못했습니다/);
  }finally{await w.cleanup();}
});

test('법령 재연결 검색은 기존 binding의 Source와 snapshot을 재조회해 선택·질문·Studio를 복구한다',async t=>{
  for(const initial of ['snapshot-denied','source-hidden'])await t.test(initial,async()=>{
    let status='disconnected';const questions=[];
    const adapter={
      listSources:async()=>initial==='source-hidden' && status!=='connected'?[]:[item],
      listConnectors:async()=>[{...connector,status}],
      getOpenLawSnapshot:async()=>{if(status!=='connected')throw Error('OPEN_LAW_ACCESS_DENIED');return{snapshot,text};},
      reconnectConnector:async()=>{status='unavailable';return{...connector,status};},
      searchOpenLaw:async()=>{status='connected';return{items:[law],page:1,total_count:1};},
      askQuestion:async value=>{questions.push(value);return{run_id:'run-law',run_result_id:'result-law',answer:'복구된 질문 응답',insufficient:true,citations:[]};},
    };
    const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-law',adapter});
    const sourceRow=()=>findElements(w.container,n=>n.tagName==='BUTTON' && n.getAttribute('aria-pressed')!==null && n.textContent.includes(version))[0];
    try{
      if(initial==='snapshot-denied')assert.equal(sourceRow().disabled,true);
      else assert.equal(sourceRow(),undefined);
      await w.act(async()=>findElements(w.container,n=>n.tagName==='BUTTON' && n.getAttribute('aria-label')==='국가법령정보센터 재연결')[0].dispatchEvent(new MinimalEvent('click')));
      await w.act(async()=>{change(input(w,'공개 법령명'),'민법');change(input(w,'공개 법령명 전송 확인'),true);});
      await click(w,'법령 검색');
      assert.ok(sourceRow(),'새 import나 전체 새로고침 없이 기존 Source가 보여야 한다');
      assert.equal(sourceRow().disabled,false,'현재 snapshot 접근 성공 후 ready를 다시 투영해야 한다');
      assert.match(w.container.textContent,/법령 수집·저장 완료/);
      await w.act(async()=>findElements(w.container,n=>n.tagName==='BUTTON' && n.getAttribute('aria-label')==='Source 추가 닫기')[0].dispatchEvent(new MinimalEvent('click')));
      if(sourceRow().getAttribute('aria-pressed')!=='true')await w.act(async()=>sourceRow().dispatchEvent(new MinimalEvent('click')));
      assert.equal(sourceRow().getAttribute('aria-pressed'),'true');
      await click(w,'근거 기반 보고서');
      const studio=findElements(w.container,n=>n.tagName==='FIELDSET' && n.getAttribute('class')==='studio-source-selection')[0];
      const checkbox=findElements(studio,n=>n.tagName==='INPUT')[0];
      assert.equal(checkbox.disabled,false);assert.equal(checkbox.checked,true);
      const textarea=findElements(w.container,n=>n.tagName==='TEXTAREA')[0];
      await w.act(async()=>{const props=textarea[Object.keys(textarea).find(key=>key.startsWith('__reactProps$'))];props.onChange({currentTarget:{value:'이 법령의 내용은?'}});});
      const form=findElements(w.container,n=>n.tagName==='FORM' && n.getAttribute('class')==='conversation-composer')[0];
      await w.act(async()=>form[Object.keys(form).find(key=>key.startsWith('__reactProps$'))].onSubmit({preventDefault(){}}));
      assert.equal(questions.length,1);assert.deepEqual(questions[0].knowledgeContext,{mode:'raw_only',resources:[{resourceKind:'source',resourceId:source,versionId:version}]});
      assert.match(w.container.textContent,/복구된 질문 응답/);
    }finally{await w.cleanup();}
  });
});

test('이전 Notebook의 늦은 법령 검색·카드 응답은 새 Notebook Source를 재조회하거나 복구하지 않는다',async()=>{
  for(const stage of ['search','connector-refresh']){
    const wait=deferred();let searchOptions,refreshStarted=false,nextLists=0;
    const adapter={listSources:async()=>[],listConnectors:async()=>refreshStarted && stage==='connector-refresh'?wait.promise:[{...connector,status:'disconnected'}],
      reconnectConnector:async()=>({...connector,status:'unavailable'}),
      searchOpenLaw:async(_,options)=>{searchOptions=options;refreshStarted=true;return stage==='search'?wait.promise:{items:[law],page:1,total_count:1};}};
    const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-law',adapter});
    try{
      await w.act(async()=>findElements(w.container,n=>n.tagName==='BUTTON' && n.getAttribute('aria-label')==='국가법령정보센터 재연결')[0].dispatchEvent(new MinimalEvent('click')));
      await w.act(async()=>{change(input(w,'공개 법령명'),'민법');change(input(w,'공개 법령명 전송 확인'),true);});
      await click(w,'법령 검색');
      const nextAdapter={listSources:async()=>{nextLists++;return[];},listConnectors:async()=>[]};
      await w.render({workspaceId:'workspace-law',adapter:nextAdapter});
      const listsBefore=nextLists;if(stage==='search')assert.equal(searchOptions.signal.aborted,true);
      await w.act(async()=>wait.resolve(stage==='search'?{items:[law],page:1,total_count:1}:[{...connector,status:'connected'}]));
      assert.equal(nextLists,listsBefore,'old Notebook 응답은 현재 Source loadRevision을 변경하면 안 된다');
      assert.doesNotMatch(w.container.textContent,/법령 수집·저장 완료/);
      assert.equal(findElements(w.container,n=>n.tagName==='BUTTON' && n.getAttribute('title')==='연결형 Source 선택').length,0);
    }finally{await w.cleanup();}
  }
});

test('저장 법령 Citation 실제 클릭은 기존 권한 GET과 snapshot 대조 후 escaped codepoint mark를 표시한다',async()=>{
  const original='가😀'.repeat(512)+'<script>제2구간</script>';
  const selectedSpan='law-span-'+createHash('sha256').update(version+'|1024').digest('hex').slice(0,32);
  const metadata={...snapshot,text_sha256:createHash('sha256').update(original).digest('hex')};
  const context={notebook_id:'notebook-law',sources:[{source_id:source,source_version_id:version}],knowledge_context_ids:[],conversation_thread_ids:['thread-law'],studio_output_ids:[],output_version_ids:[],generation_settings_ids:[],source_deletion_requests:[],
    conversation:{conversation_thread_id:'thread-law',answer:{run_id:'run-law',run_result_id:'result-law',answer:'저장 법령 답변',insufficient:false,citations:[{citation_id:'citation-law',source_id:source,source_version_id:version,evidence_span_id:selectedSpan,page:1,origin:'raw_source',context_item_id:source,locator:{kind:'section',value:selectedSpan}}]}}};
  const prior=globalThis.fetch,calls=[];globalThis.fetch=async(url,init)=>{
    calls.push({url,init});return url.includes('/citations/')?new Response(original,{headers:{'Content-Type':'text/plain; charset=utf-8','X-Citation-Locator-Kind':'section'}}):Response.json({data:{snapshot:metadata,text:original},meta:{trace_id:'trace-law'}});
  };
  const actual=webModule.createWebProductWorkspaceAdapter('workspace-law','notebook-law');
  const adapter=createNotebookContextWorkspaceAdapter({listSources:async()=>[item],getOpenLawSnapshot:actual.getOpenLawSnapshot,getOpenLawCitation:actual.getOpenLawCitation,citationUrl:actual.citationUrl},context);
  const w=await mount(module.ProductWorkspaceShell,{workspaceId:'workspace-law',adapter});
  try{
    const anchor=findElements(w.container,n=>n.tagName==='A')[0];const event=new MinimalEvent('click');
    await w.act(async()=>{anchor.dispatchEvent(event);await Promise.resolve();await Promise.resolve();});
    assert.equal(event.defaultPrevented,true);
    const mark=findElements(w.container,n=>n.tagName==='MARK');assert.equal(mark.length,1);assert.equal(mark[0].textContent,'<script>제2구간</script>');
    assert.equal(findElements(w.container,n=>n.tagName==='SCRIPT').length,0);
    assert.ok(calls.some(call=>call.url.endsWith('/citations/citation-law/content?notebook_id=notebook-law')));
    await click(w,'법령 인용 원문 닫기');assert.equal(findElements(w.container,n=>n.tagName==='MARK').length,0);
  }finally{await w.cleanup();globalThis.fetch=prior;}
});
