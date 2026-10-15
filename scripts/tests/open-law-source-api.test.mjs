import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import * as api from '../../apps/web/lib/product-workspace-api.js';

const source='law-source-'+ 'a'.repeat(32), version='law-version-'+ 'a'.repeat(32), snapshotId='law-snapshot-'+ 'a'.repeat(32);
const text='제1조 합성 원문\n<script>실행 금지</script>';
const law={mst:'107433',law_id:'011268',title:'합성 시험법',effective_date:'20100830',promulgation_date:null,ministry:null,article_code:null};
const preview={law,text,raw_sha256:'a'.repeat(64),text_sha256:createHash('sha256').update(text).digest('hex'),fetched_at:'2026-10-16T00:00:00Z',extraction_version:'open-law-text-v1'};
const snapshot={snapshot_id:snapshotId,connector_id:'mcp-open-law-go-kr',source_id:source,source_version_id:version,...law,
  origin_uri:'https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=107433&efYd=20100830',fetched_at:preview.fetched_at,
  raw_sha256:preview.raw_sha256,text_sha256:preview.text_sha256,extraction_version:preview.extraction_version,license_status:'unknown'};
const meta={trace_id:'trace-law'};
const response=data=>Response.json({data,meta});

test('mcp_registration_not_query_success',async()=>{
  assert.equal(typeof api.searchOpenLaw,'function','법령 runtime consumer가 필요하다');
  let calls=0;
  const connector={connector_id:'mcp-open-law-go-kr',kind:'mcp',name:'국가법령정보센터',status:'unavailable',source_count:0,endpoint_label:'open.law.go.kr',last_checked_at:null,error_code:'CONNECTOR_NOT_VERIFIED'};
  const registered=await api.registerWorkspaceConnector('workspace-law',{kind:'mcp',name:'국가법령정보센터'},
    {fetchImpl:async()=>{calls++;return Response.json({data:connector,meta:{...meta,workspace_id:'workspace-law'}});}});
  assert.equal(registered.status,'unavailable');assert.equal(calls,1);
  await assert.rejects(api.searchOpenLaw('workspace-law',{notebook_id:'notebook-law',query:'민법',page:1,public_query_confirmed:false},{fetchImpl:async()=>{throw Error('network forbidden');}}),/OPEN_LAW_INPUT_INVALID/);
});

test('law_search_preview_explicit_import',async()=>{
  for(const name of ['searchOpenLaw','previewOpenLaw','importOpenLaw','getOpenLawSnapshot'])assert.equal(typeof api[name],'function',name);
  const calls=[];
  const fetchImpl=async(url,init)=>{
    calls.push({url,init});
    if(url.endsWith('law-search'))return response({items:[Object.fromEntries(Object.entries(law).filter(([key])=>key!=='article_code'))],page:1,total_count:1});
    if(url.endsWith('law-preview'))return response(preview);
    if(url.endsWith('law-imports'))return Response.json({data:{kind:'open_law_import',id:snapshotId,version:'1',attributes:{snapshot_id:snapshotId,source_id:source,source_version_id:version,source_state:'ready',replayed:false}},meta},{status:201});
    return response({snapshot,text});
  };
  await api.searchOpenLaw('workspace-law',{notebook_id:'notebook-law',query:'민법',page:1,public_query_confirmed:true},{fetchImpl});
  await api.previewOpenLaw('workspace-law',{notebook_id:'notebook-law',mst:law.mst,effective_date:law.effective_date,article_code:null},{fetchImpl});
  const result=await api.importOpenLaw('workspace-law',{notebook_id:'notebook-law',mst:law.mst,effective_date:law.effective_date,article_code:null,expected_raw_sha256:preview.raw_sha256,expected_text_sha256:preview.text_sha256},{fetchImpl,idempotencyKey:'law-explicit-request-0001'});
  assert.equal(result.source_version_id,version);
  assert.deepEqual(await api.getOpenLawSnapshot('workspace-law',version,{fetchImpl,notebookId:'notebook-law'}),{snapshot,text});
  assert.ok(calls.every(call=>call.url.startsWith('/bff/api/')&&call.init.credentials==='same-origin'));
  assert.ok(calls[3].url.endsWith(`/source-versions/${version}/law-snapshot?notebook_id=notebook-law`));
  assert.equal(calls[2].init.headers['Idempotency-Key'],'law-explicit-request-0001');
  assert.equal(JSON.parse(calls[0].init.body).notebook_id,'notebook-law');
});

test('law_changed_preview_requires_review',async()=>{
  assert.equal(typeof api.previewOpenLaw,'function');
  const input={notebook_id:'notebook-law',mst:law.mst,effective_date:law.effective_date,article_code:null};
  for(const invalid of [{...preview,private_key:'forbidden'},{...preview,text:text+'changed'},{...preview,law:{...law,effective_date:'20100901'}}]){
    await assert.rejects(api.previewOpenLaw('workspace-law',input,{fetchImpl:async()=>response(invalid)}),/OPEN_LAW_RESPONSE_INVALID/);
  }
  await assert.rejects(api.getOpenLawSnapshot('workspace-law',version,{notebookId:'notebook-law',fetchImpl:async()=>response({snapshot:{...snapshot,extra:true},text})}),/OPEN_LAW_RESPONSE_INVALID/);
});

test('law_safe_errors_no_external_url_or_key',async()=>{
  assert.equal(typeof api.searchOpenLaw,'function');
  const input={notebook_id:'notebook-law',query:'민법',page:1,public_query_confirmed:true};
  await assert.rejects(api.searchOpenLaw('workspace-law',input,{fetchImpl:async()=>Response.json({error:{code:'https://private.example/?OC=do-not-echo'}},{status:502})}),/^Error: OPEN_LAW_REQUEST_FAILED$/);
  await assert.rejects(api.searchOpenLaw('workspace-law',input,{fetchImpl:async()=>Response.json({error:{code:'OPEN_LAW_EGRESS_BLOCKED'}},{status:403})}),/OPEN_LAW_EGRESS_BLOCKED/);
});

test('HTTP 개발 환경의 subtle 부재에서도 UTF8 원문 전체 해시를 검증한다',async()=>{
  const descriptor=Object.getOwnPropertyDescriptor(globalThis,'crypto');
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:{}});
  try{
    const input={notebook_id:'notebook-law',mst:law.mst,effective_date:law.effective_date,article_code:null};
    for(const value of ['가😀나', 'a'.repeat(55), 'b'.repeat(64), '원문'.repeat(1000)]){
      const data={...preview,text:value,text_sha256:createHash('sha256').update(value).digest('hex')};
      assert.equal((await api.previewOpenLaw('workspace-law',input,{fetchImpl:async()=>response(data)})).text,value);
      await assert.rejects(api.previewOpenLaw('workspace-law',input,{fetchImpl:async()=>response({...data,text:value+'변조'})}),/OPEN_LAW_RESPONSE_INVALID/);
    }
  }finally{Object.defineProperty(globalThis,'crypto',descriptor);}
});

test('삭제 영향 수는 기존 ConnectorSource 6필드 실제 목록으로만 읽는다',async()=>{
  const items=[{source_id:source,connector_id:'mcp-open-law-go-kr',title:'합성 시험법',source_state:'ready',usable:true,content_digest:'a'.repeat(64)}];
  const data=await api.listWorkspaceConnectorSources('workspace-law','mcp-open-law-go-kr',{fetchImpl:async()=>Response.json({data:{sources:items},meta:{trace_id:'trace-law',workspace_id:'workspace-law'}})});
  assert.deepEqual(data,items);
});

test('법령 Citation 구간은 현재 원문과 snapshot 해시를 대조하고 codepoint span exact1개만 반환한다',async()=>{
  assert.equal(typeof api.getOpenLawCitation,'function');
  const original='\uFEFF'+'가😀'.repeat(511)+'가<script>제2구간</script>';
  const digest=createHash('sha256').update(original).digest('hex');
  const selectedSpan='law-span-'+createHash('sha256').update(version+'|1024').digest('hex').slice(0,32);
  const citation={citation_id:'citation-law',source_id:source,source_version_id:version,evidence_span_id:selectedSpan,page:1,origin:'raw_source',context_item_id:source,locator:{kind:'section',value:selectedSpan}};
  const calls=[];
  const fetchImpl=async(url,init)=>{calls.push({url,init});return url.includes('/citations/')?new Response(original,{headers:{'Content-Type':'text/plain; charset=utf-8','X-Citation-Locator-Kind':'section'}}):response({snapshot:{...snapshot,text_sha256:digest},text:original});};
  const result=await api.getOpenLawCitation('workspace-law',citation,{notebookId:'notebook-law',fetchImpl});
  assert.deepEqual(result,{source_version_id:version,text:original,start:1024,end:Array.from(original).length});
  assert.equal(Array.from(result.text).slice(result.start,result.end).join(''),'<script>제2구간</script>');
  assert.equal(calls.length,2);assert.ok(calls[0].url.endsWith('/citations/citation-law/content?notebook_id=notebook-law'));
  assert.ok(calls.every(call=>call.init.credentials==='same-origin'&&call.init.cache==='no-store'));
  for(const invalid of [{...citation,source_version_id:'law-version-'+'b'.repeat(32)},
    {...citation,evidence_span_id:'law-span-'+'b'.repeat(32),locator:{kind:'section',value:'law-span-'+'b'.repeat(32)}},
    {...citation,locator:{kind:'section',value:'law-span-'+'b'.repeat(32)}}]){
    await assert.rejects(api.getOpenLawCitation('workspace-law',invalid,{notebookId:'notebook-law',fetchImpl}),/OPEN_LAW_(?:INPUT|RESPONSE)_INVALID/);
  }
  await assert.rejects(api.getOpenLawCitation('workspace-law',citation,{notebookId:'other-notebook',fetchImpl:async()=>Response.json({error:{code:'OPEN_LAW_ACCESS_DENIED'}},{status:403})}),/OPEN_LAW_ACCESS_DENIED/);
  await assert.rejects(api.getOpenLawCitation('workspace-law',citation,{notebookId:'notebook-law',fetchImpl:async url=>url.includes('/citations/')?new Response(original+'변조',{headers:{'Content-Type':'text/plain','X-Citation-Locator-Kind':'section'}}):response({snapshot:{...snapshot,text_sha256:digest},text:original})}),/OPEN_LAW_RESPONSE_INVALID/);
});
