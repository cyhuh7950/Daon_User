import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";

import {
  assertProductWorkspaceAdapter,
  createProductWorkspaceState,
  canCreateGroundedReport,
  projectQuestionFailureState,
} from "../../packages/ui/src/product-workspace-model.js";
import { classifyConversationIntent, buildSourceScopeMismatch } from "../../packages/ui/src/conversation-intent.js";
import { uploadSource } from "../../apps/web/lib/source-upload-api.js";
import * as sourceRetryApi from "../../apps/web/lib/source-upload-api.js";
import { citationContentUrl } from "../../apps/web/lib/question-answering-api.js";
import { unbindWorkspaceSource } from "../../apps/web/lib/product-workspace-api.js";

test("actual Web adapter는 UI Source를 exact 두 ID로 투영해 Notebook 제거를 요청한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-unbind-adapter-"));
  const originalFetch = globalThis.fetch;
  try {
    await import("react");
    await import("react-dom/client");
    const { build } = await import("vite");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/components/actual-workspace.jsx"), formats: ["es"], fileName: "source-unbind-adapter" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("source-unbind-adapter") && /\.m?js$/u.test(name));
    assert.ok(entry);
    const { createWebProductWorkspaceAdapter } = await import(pathToFileURL(path.join(output, entry)).href);
    const actual = createWebProductWorkspaceAdapter("workspace-1", "notebook-current");
    const source = Object.freeze({
      sourceId: "source-1", sourceVersionId: "version-1", filename: "ready.pdf",
      sourceState: "ready", processingState: "completed", jobState: "completed",
      websiteSnapshot: null, lawSnapshot: null, lawCandidate: false, ready: true,
    });
    const calls = [];
    globalThis.fetch = async (url, init) => {
      calls.push({ url, init });
      return Response.json({
        data: { notebook_id: "notebook-current", source_id: "source-1", source_version_id: "version-1", status: "unbound" },
        meta: { trace_id: "trace-1", workspace_id: "workspace-1", replayed: false },
      }, { headers: { ETag: '"notebook-binding:2"' } });
    };
    const controller = new AbortController();
    const result = await actual.unbindSource(source, {
      notebookId: "notebook-other", bindingEtag: '"notebook-binding:1"',
      idempotencyKey: "idempotency-unbind-0001", signal: controller.signal,
    });
    assert.deepEqual(result, {
      notebook_id: "notebook-current", source_id: "source-1", source_version_id: "version-1",
      status: "unbound", etag: '"notebook-binding:2"',
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/notebooks/notebook-current/source-unbindings");
    assert.equal(calls[0].init.method, "POST");
    assert.equal(calls[0].init.credentials, "same-origin");
    assert.equal(calls[0].init.cache, "no-store");
    assert.equal(calls[0].init.signal, controller.signal);
    assert.deepEqual(calls[0].init.headers, {
      "Content-Type": "application/json", "Idempotency-Key": "idempotency-unbind-0001", "If-Match": '"notebook-binding:1"',
    });
    assert.deepEqual(JSON.parse(calls[0].init.body), { source_id: "source-1", source_version_id: "version-1" });
    await actual.unbindSource(source, {
      etag: '"notebook-binding:3"', bindingEtag: '"notebook-binding:1"',
      idempotencyKey: "idempotency-unbind-0002", signal: controller.signal,
    });
    assert.equal(calls[1].init.headers["If-Match"], '"notebook-binding:3"');
    assert.equal(calls[1].init.signal, controller.signal);
    assert.equal(source.filename, "ready.pdf");
    assert.equal(source.ready, true);
    for (const invalid of [
      { ...source, sourceId: "../source" }, { ...source, sourceVersionId: "../version" },
      { ...source, sourceId: undefined }, { ...source, sourceVersionId: null },
    ]) {
      await assert.rejects(actual.unbindSource(invalid, {
        bindingEtag: '"notebook-binding:1"', idempotencyKey: "idempotency-unbind-invalid", signal: controller.signal,
      }), /SOURCE_UNBIND_INPUT_INVALID/u);
    }
    // UI projection must not loosen the existing lower-level exact-input contract.
    await assert.rejects(unbindWorkspaceSource("workspace-1", source, {
      notebookId: "notebook-current", etag: '"notebook-binding:1"', idempotencyKey: "idempotency-unbind-invalid",
    }), /SOURCE_UNBIND_INPUT_INVALID/u);
    await assert.rejects(actual.unbindSource(source, {
      bindingEtag: "*", idempotencyKey: "idempotency-unbind-invalid",
    }), /SOURCE_UNBIND_INPUT_INVALID/u);
    assert.equal(calls.length, 2, "invalid ID/ETag and unprojected direct input must never reach fetch");
  } finally {
    globalThis.fetch = originalFetch;
    await rm(output, { recursive: true, force: true });
  }
});

test("실패 Source 실제 React 재처리 클릭은 새 Run POST 후 상태를 조회한다", async () => {
  for (const failsAgain of [false, true]) {
    let workspace;
    let state = "waiting_model";
    const calls = [];
    try {
      workspace = await mountSourceRetryWorkspace({
        ...adapter,
        async listSources() { return [{ source_id: "source-1", source_version_id: "version-1", filename: "notes.txt", source_state: state,
          processing_state: state === "ready" ? "completed" : "failed", job_state: state === "ready" ? "completed" : "dead_letter" }]; },
        async listStudioOutputs() { return []; },
        async reprocessSource(source, options) {
          return sourceRetryApi.reprocessSource("workspace-1", source, { ...options, notebookId: "notebook-1", fetchImpl: async (url, init) => {
            calls.push({ url, init });
            return Response.json({ data: { id: "run-retry-1", kind: "processing_run", version: "2", attributes: {
              processing_run_id: "run-retry-1", source_id: "source-1", source_version_id: "version-1", retry_of_processing_run_id: "run-old-1",
              processing_state: "vision_llm_understanding", source_state: "processing", job_state: "pending", replayed: false,
            } }, trace_id: "trace-1" }, { status: 201 });
          } });
        },
        async getProcessingStatus(runId) {
          calls.push({ runId });
          state = failsAgain ? "waiting_model" : "ready";
          return { processing_run_id: runId, source_id: "source-1", source_version_id: "version-1", source_state: state,
            processing_state: failsAgain ? "failed" : "completed", job_state: failsAgain ? "dead_letter" : "completed", safe_error_code: failsAgain ? "UNDERSTANDING_PROVIDER_UNAVAILABLE" : null };
        },
      }, `source-manual-retry-${failsAgain}`);
      const retry = findElements(workspace.container, (node) => node.tagName === "BUTTON" && node.textContent === "재처리")[0];
      assert.ok(retry, "failed Source must expose manual reprocessing");
      await workspace.act(async () => { retry.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
      assert.equal(calls[0].url, "/bff/api/sources/source-1/processing-runs");
      assert.match(calls[0].init.headers["Idempotency-Key"], /^source-retry-/u);
      assert.equal(calls[1].runId, "run-retry-1");
      assert.match(workspace.container.textContent, /notes.txt/u);
      if (failsAgain) assert.match(workspace.container.textContent, /모델 대기/u);
      else assert.match(workspace.container.textContent, /사용 가능/u);
    } finally { if (workspace) await workspace.cleanup(); }
  }
});

test("DOCX 검토 필요 completed 자료만 실제 재처리 클릭으로 새 Run을 요청한다",async(t)=>{
  for(const scenario of ['docx','pptx','xlsx','pdf','text','image','active-job','active-run','ready'])await t.test(scenario,async()=>{
    let workspace;const calls=[];let finished=false;
    const filenames={docx:'qa.docx',pptx:'qa.pptx',xlsx:'qa.xlsx',pdf:'qa.pdf',text:'qa.txt',image:'qa.png'};
    try{
      workspace=await mountSourceRetryWorkspace({...adapter,
        listSources:async()=>[{source_id:'source-review',source_version_id:'version-review',filename:filenames[scenario]??'qa.docx',
          source_state:finished||scenario==='ready'?'ready':'needs_review',
          processing_state:scenario==='active-run'?'vision_llm_understanding':'completed',job_state:scenario==='active-job'?'leased':'completed'}],
        listStudioOutputs:async()=>[],
        reprocessSource:async(source,options)=>sourceRetryApi.reprocessSource('workspace-1',source,{...options,notebookId:'notebook-1',fetchImpl:async(url,init)=>{
          calls.push({url,body:JSON.parse(init.body)});return Response.json({data:{id:'run-review-new',kind:'processing_run',version:'2',attributes:{
            processing_run_id:'run-review-new',source_id:'source-review',source_version_id:'version-review',retry_of_processing_run_id:'run-review-old',
            processing_state:'vision_llm_understanding',source_state:'processing',job_state:'pending',replayed:false}},trace_id:'trace-1'},{status:201});
        }}),
        getProcessingStatus:async id=>{finished=true;calls.push({runId:id});return {processing_run_id:id,source_id:'source-review',source_version_id:'version-review',source_state:'ready',processing_state:'completed',job_state:'completed',safe_error_code:null};}
      },`docx-review-retry-${scenario}`);
      const retry=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='재처리')[0];
      if(scenario==='docx'||scenario==='pptx'){
        assert.ok(retry);await workspace.act(async()=>retry.dispatchEvent(new MinimalEvent('click')));
        assert.equal(calls[0].url,'/bff/api/sources/source-review/processing-runs');
        assert.equal(calls[0].body.attributes.source_version_id,'version-review');
        assert.equal(calls[1].runId,'run-review-new');assert.match(workspace.container.textContent,/사용 가능/);
      }else{assert.equal(retry,undefined);assert.equal(calls.length,0);}
    }finally{if(workspace)await workspace.cleanup();}
  });
});

test("재처리 응답 유실 뒤 다시 클릭하면 같은 멱등 키를 보존한다", async () => {
  let workspace;
  const keys = [];
  try {
    workspace = await mountSourceRetryWorkspace({ ...adapter,
      async listSources() { return [{ source_id: "source-1", source_version_id: "version-1", filename: "notes.txt", source_state: "waiting_model", processing_state: "failed", job_state: "dead_letter" }]; },
      async listStudioOutputs() { return []; },
      async reprocessSource(_source, options) { keys.push(options.idempotencyKey); throw new Error("SOURCE_REPROCESS_FAILED"); },
    }, "source-retry-lost-response");
    for (let index = 0; index < 2; index += 1) {
      const retry = findElements(workspace.container, (node) => node.tagName === "BUTTON" && node.textContent === "재처리")[0];
      await workspace.act(async () => { retry.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
      assert.match(workspace.container.textContent, /재처리를 요청하지 못했습니다/u);
    }
    assert.equal(keys.length, 2);
    assert.equal(keys[0], keys[1]);
    assert.doesNotMatch(workspace.container.textContent, /처리 완료/u);
  } finally { if (workspace) await workspace.cleanup(); }
});

const adapter = Object.freeze({
  listSources() {}, uploadPdf() {}, getProcessingStatus() {}, askQuestion() {},
  citationUrl() {}, createReport() {}, listStudioOutputs() {},
});
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};

test("작업 상담과 명시 Source 확인은 서로 다른 conversation mode로 분류한다", () => {
  assert.equal(classifyConversationIntent("다음 작업을 어떻게 진행할까?"), "work_support");
  assert.equal(classifyConversationIntent("선택한 문서에서 보존 기간을 찾아줘"), "explicit_source_lookup");
  assert.equal(classifyConversationIntent("이 자료로 보고서를 만들어줘"), "source_backed_action");
  assert.equal(classifyConversationIntent("최신 정책을 웹에서 찾아줘"), "approved_web_research");
});

test("질문 실행 UI는 세션 인증 외 Source 질문 승인 modal을 노출하지 않는다", async () => {
  const source = await readFile(new URL("../../packages/ui/src/product-workspace-shell.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /Source 질문 승인|question-authorization|authorizeQuestion/u);
});

test("Source 범위 불일치는 단독 refusal이 아니라 구조화된 다음 행동을 반환한다", () => {
  const result = buildSourceScopeMismatch({
    sourceScopeSummary: "보존 정책과 문서 처리 절차",
    mismatch: "질문은 최신 주가를 요구하지만 선택 Source에는 주가 정보가 없습니다.",
  });
  assert.deepEqual(result, {
    source_scope_summary: "보존 정책과 문서 처리 절차",
    mismatch: "질문은 최신 주가를 요구하지만 선택 Source에는 주가 정보가 없습니다.",
    next_actions: ["다른 Source 선택", "Source 추가", "승인된 웹 조사 요청"],
  });
});

async function mountSourceRetryWorkspace(effectAdapter, suffix) {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, `.workspace-source-retry-${suffix}-`));
  const dom = installMinimalDom();
  const { build } = await import("vite");
  const { createElement, act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  await build({ configFile: false, logLevel: "silent", root, build: {
    outDir: output, emptyOutDir: false,
    lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-retry" },
    rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
  } });
  const entry = (await readdir(output)).find((name) => name.startsWith("source-retry") && /\.m?js$/u.test(name));
  const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?sourceRetry=${Date.now()}`);
  const container = dom.document.createElement("div");
  dom.document.body.appendChild(container);
  const reactRoot = createRoot(container);
  await act(async () => {
    reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter }));
    await Promise.resolve();
    await Promise.resolve();
  });
  return {
    container,
    act,
    wait: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    cleanup: async () => {
      await act(async () => reactRoot.unmount());
      dom.restore();
      await rm(output, { recursive: true, force: true });
    },
  };
}

const sourceRetryPayload = {
  source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf",
  source_state: "ready", processing_state: "completed", job_state: "completed",
};

test("sheet_cell 실제 React 원문 표는 재진입·강조·safe text·늦은 응답 결속을 유지한다", async (t) => {
  for (const scenario of ['success', 'sheet-markup', 'invalid', 'late-selection', 'late-other-source', 'late-close']) await t.test(scenario, async () => {
    const originalFetch = globalThis.fetch;
    const pending = deferred(); const calls = []; let workspace;
    const sheetName = scenario === 'sheet-markup' ? '<img src=x>' : '납품 계획';
    const locator = { kind: 'sheet_cell', value: JSON.stringify({ sheet: sheetName, range: 'A1' }) };
    const citation = { citation_id: 'citation-cell', source_id: 'source-1', source_version_id: 'version-1', evidence_span_id: 'span-cell', page: 1, origin: 'raw_source', context_item_id: 'source-1', locator };
    const content = { source_version_id: 'version-1', source_sha256: 'a'.repeat(64), sheet_name: sheetName, sheet_visibility: 'visible', selected_range: 'A1', view_range: 'A1:B2', cells: ['A1','B1','A2','B2'].map(address => ({ address, value_type: 'blank', raw_value: null, display_value: null, formula: null, cached_value: null, number_format: null, hidden_row: false, hidden_column: false })), merged_ranges: ['A1:B1'] };
    content.cells[0] = { ...content.cells[0], value_type: 'string', raw_value: '<img onerror=evil()>', display_value: '<script>literal</script>', formula: 'HYPERLINK("https://evil",1)', cached_value: 'saved original' };
    if (scenario === 'invalid') content.source_version_id = 'version-other';
    globalThis.fetch = async (url, init) => { calls.push({ url, init }); if (scenario.startsWith('late-')) await pending.promise; return Response.json(content); };
    try {
      workspace = await mountSourceRetryWorkspace({ ...adapter, async listSources() { return [{ ...sourceRetryPayload, filename: 'qa.xlsx' }, { ...sourceRetryPayload, source_id: 'source-other', source_version_id: 'version-other', filename: 'other.xlsx' }]; }, async listStudioOutputs() { return []; }, citationUrl(value) { return citationContentUrl('workspace-1', value, { notebookId: 'notebook-1' }); }, async loadNotebookConversation() { return { run_id: 'run-cell', run_result_id: 'result-cell', answer: '저장된 셀 근거 답변', insufficient: false, citations: [{ ...citation, content_url: citationContentUrl('workspace-1', citation, { notebookId: 'notebook-1' }) }] }; } }, `xlsx-${scenario}`);
      const link = findElements(workspace.container, node => node.tagName === 'A' && node.textContent.includes('Citation'))[0];
      assert.ok(link, '저장된 셀 citation이 실제 DOM에 복원되어야 한다');
      assert.ok(link.textContent.includes(`${sheetName} · A1`));
      await workspace.act(async () => link.dispatchEvent(new MinimalEvent('click')));
      assert.equal(calls.length, 1);
      assert.equal(calls[0].url, '/bff/api/workspaces/workspace-1/citations/citation-cell/content?notebook_id=notebook-1');
      assert.equal(calls[0].init.credentials, 'same-origin');
      if (scenario === 'late-selection') {
        const selected = findElements(workspace.container, node => node.tagName === 'BUTTON' && node.getAttribute?.('aria-pressed') === 'true' && node.textContent.includes('qa.xlsx'))[0];
        await workspace.act(async () => selected.dispatchEvent(new MinimalEvent('click')));
      }
      if (scenario === 'late-other-source') {
        const other = findElements(workspace.container, node => node.tagName === 'BUTTON' && node.textContent.includes('other.xlsx'))[0];
        await workspace.act(async () => other.dispatchEvent(new MinimalEvent('click')));
        assert.equal(other.getAttribute('aria-pressed'), 'true');
      }
      if (scenario === 'late-close') {
        const close = findElements(workspace.container, node => node.tagName === 'BUTTON' && node.textContent === '원문 닫기')[0];
        assert.ok(close); await workspace.act(async () => close.dispatchEvent(new MinimalEvent('click')));
      }
      if (scenario.startsWith('late-')) await workspace.act(async () => { pending.resolve(); await Promise.resolve(); });
      const tables = findElements(workspace.container, node => node.tagName === 'TABLE');
      if (['success', 'sheet-markup'].includes(scenario)) {
        assert.equal(tables.length, 1);
        assert.equal(findElements(tables[0], node => node.tagName === 'TD').length, 4);
        assert.equal(findElements(tables[0], node => node.getAttribute?.('aria-selected') === 'true').length, 1);
        assert.match(tables[0].textContent, /<script>literal<\/script>/);
        assert.match(tables[0].textContent, /HYPERLINK\("https:\/\/evil",1\)/);
        assert.equal(findElements(tables[0], node => ['IMG','SCRIPT','A'].includes(node.tagName)).length, 0);
        const viewer = findElements(workspace.container, node => node.getAttribute?.('aria-label') === '셀 원문')[0];
        assert.ok(viewer.textContent.includes(sheetName));
        assert.equal(findElements(viewer, node => ['IMG','SCRIPT','A'].includes(node.tagName)).length, 0);
        assert.match(workspace.container.textContent, /cached.*재계산/);
        assert.match(workspace.container.textContent, /A1:B1/);
        assert.ok(findElements(workspace.container, node => node.getAttribute?.('class') === 'citation-sheet-scroll').length);
      } else { assert.equal(tables.length, 0); if (scenario === 'invalid') assert.match(workspace.container.textContent, /CITATION_CONTENT_UNAVAILABLE/); }
      if (!['late-selection', 'late-other-source'].includes(scenario)) assert.match(workspace.container.textContent, /저장된 셀 근거 답변/);
    } finally { pending.resolve(); if (workspace) await workspace.cleanup(); globalThis.fetch = originalFetch; }
  });
});

test("PPTX 실제 React 원문은 emoji codepoint 강조·표 위치·safe text·늦은 응답을 결속한다", async(t)=>{
  for(const scenario of ['success','invalid','late-selection','late-other-source','late-close']) await t.test(scenario,async()=>{
    const oldFetch=globalThis.fetch;const pending=deferred();const calls=[];let workspace;
    const locator={kind:'pptx_block',value:'{"block":"s:2:tb:3:r:1:c:2:p:1","start":1,"end":2}'};
    const citation={citation_id:'citation-pptx',source_id:'source-1',source_version_id:'version-1',evidence_span_id:'span-pptx',page:1,origin:'raw_source',context_item_id:'source-1',locator};
    const content={source_version_id:'version-1',source_sha256:'a'.repeat(64),serialization_version:'pptx-structure-v1',selected_block_id:'s:2:tb:3:r:1:c:2:p:1',selected_start:1,selected_end:2,blocks:[
      {block_id:'s:2:sp:2:p:1',ordinal:2,slide:2,object_id:2,block_type:'shape_paragraph',text:'',table:null},
      {block_id:'s:2:tb:3:r:1:c:2:p:1',ordinal:3,slide:2,object_id:3,block_type:'table_cell_paragraph',text:'한😀글 <script>literal</script> https://evil',table:{row:1,column:2,paragraph:1}},
    ]};
    if(scenario==='invalid')content.source_version_id='wrong';
    globalThis.fetch=async(url,init)=>{calls.push({url,init});if(scenario.startsWith('late'))await pending.promise;return Response.json(content);};
    try{
      workspace=await mountSourceRetryWorkspace({...adapter,listSources:async()=>[{...sourceRetryPayload,filename:'qa.pptx'},{...sourceRetryPayload,source_id:'other-source',source_version_id:'other-version',filename:'other.pptx'}],listStudioOutputs:async()=>[],citationUrl:value=>citationContentUrl('workspace-1',value,{notebookId:'notebook-1'}),loadNotebookConversation:async()=>({run_id:'run-pptx',run_result_id:'result-pptx',answer:'저장된 PPTX 답변',insufficient:false,citations:[{...citation,content_url:citationContentUrl('workspace-1',citation,{notebookId:'notebook-1'})}]})},`pptx-${scenario}`);
      const link=findElements(workspace.container,node=>node.tagName==='A'&&node.textContent.includes('Citation'))[0];
      assert.ok(link);assert.match(link.textContent,/슬라이드 2 · 객체 3 · 표 행 1 · 열 2/);assert.doesNotMatch(link.getAttribute('href'),/#page/);
      await workspace.act(async()=>link.dispatchEvent(new MinimalEvent('click')));
      assert.equal(calls.length,1);assert.equal(calls[0].init.credentials,'same-origin');
      if(scenario.startsWith('late')) assert.match(workspace.container.textContent,/PPTX 원문을 불러오는 중입니다/u);
      if(scenario==='late-selection'){const selected=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.getAttribute?.('aria-pressed')==='true'&&node.textContent.includes('qa.pptx'))[0];await workspace.act(async()=>selected.dispatchEvent(new MinimalEvent('click')));}
      if(scenario==='late-other-source'){const other=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent.includes('other.pptx'))[0];await workspace.act(async()=>other.dispatchEvent(new MinimalEvent('click')));assert.equal(other.getAttribute('aria-pressed'),'true');}
      if(scenario==='late-close'){const close=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='원문 닫기')[0];await workspace.act(async()=>close.dispatchEvent(new MinimalEvent('click')));}
      if(scenario.startsWith('late'))await workspace.act(async()=>{pending.resolve();await Promise.resolve();});
      const viewer=findElements(workspace.container,node=>node.getAttribute?.('aria-label')==='PPTX 원문')[0];
      if(scenario==='success'){
        assert.ok(viewer);assert.match(viewer.textContent,/행 1 · 열 2/);assert.doesNotMatch(viewer.textContent,/1쪽|page=1/);
        assert.equal(findElements(viewer,node=>node.tagName==='MARK')[0].textContent,'😀');
        assert.match(viewer.textContent,/<script>literal<\/script>/);assert.equal(findElements(viewer,node=>['A','SCRIPT','IMG'].includes(node.tagName)).length,0);
        assert.ok(findElements(viewer,node=>node.getAttribute?.('class')==='citation-docx-scroll').length);
      }else if(scenario==='invalid'){
        assert.match(viewer.textContent,/PPTX 원문을 확인할 수 없습니다/u);
        assert.match(viewer.textContent,/CITATION_CONTENT_UNAVAILABLE/);
      }
      else assert.equal(viewer,undefined);
    }finally{pending.resolve();if(workspace)await workspace.cleanup();globalThis.fetch=oldFetch;}
  });
});

test('audio React transcript keeps time, Unicode, lazy binary and stale-response boundary',async(t)=>{
  for(const scenario of ['success','invalid','late-close','late-other-source'])await t.test(scenario,async()=>{
    const oldFetch=globalThis.fetch,pending=deferred(),calls=[];let workspace;
    const locator={kind:'audio_segment',value:'{"segment":"s:1","start_ms":0,"end_ms":1000,"start":1,"end":2}'};
    const citation={citation_id:'citation-audio',source_id:'source-1',source_version_id:'version-1',evidence_span_id:'span-audio',page:1,origin:'raw_source',context_item_id:'source-1',locator};
    const content={source_version_id:'version-1',source_sha256:'a'.repeat(64),transcript_sha256:'b'.repeat(64),duration_ms:1000,
      selected_segment_id:'s:1',selected_start:1,selected_end:2,segments:[{segment_id:'s:1',start_ms:0,end_ms:1000,text:'한😀글 <script>literal</script>'}]};
    if(scenario==='invalid')content.source_version_id='other';
    globalThis.fetch=async(url,init)=>{calls.push({url,init});if(scenario.startsWith('late'))await pending.promise;return Response.json(content);};
    try{
      workspace=await mountSourceRetryWorkspace({...adapter,
        listSources:async()=>[{...sourceRetryPayload,filename:'qa.wav'},{...sourceRetryPayload,source_id:'other-source',source_version_id:'other-version',filename:'other.wav'}],
        listStudioOutputs:async()=>[],citationUrl:value=>citationContentUrl('workspace-1',value,{notebookId:'notebook-1'}),
        loadNotebookConversation:async()=>({run_id:'run-audio',run_result_id:'result-audio',answer:'저장된 음성 답변',insufficient:false,
          citations:[{...citation,content_url:citationContentUrl('workspace-1',citation,{notebookId:'notebook-1'})}]})},`audio-${scenario}`);
      const link=findElements(workspace.container,node=>node.tagName==='A'&&node.textContent.includes('Citation'))[0];
      assert.ok(link);assert.match(link.textContent,/음성 00:00.000–00:01.000/u);assert.equal(calls.length,0);
      await workspace.act(async()=>link.dispatchEvent(new MinimalEvent('click')));
      assert.equal(calls.length,1);assert.doesNotMatch(calls[0].url,/representation=audio/);
      if(scenario==='late-close'){const close=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='원문 닫기')[0];await workspace.act(async()=>close.dispatchEvent(new MinimalEvent('click')));}
      if(scenario==='late-other-source'){const other=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent.includes('other.wav'))[0];await workspace.act(async()=>other.dispatchEvent(new MinimalEvent('click')));}
      if(scenario.startsWith('late'))await workspace.act(async()=>{pending.resolve();await Promise.resolve();});
      const viewer=findElements(workspace.container,node=>node.getAttribute?.('aria-label')==='음성 전사 원문')[0];
      if(scenario==='success'){
        assert.ok(viewer);assert.equal(findElements(viewer,node=>node.tagName==='MARK')[0].textContent,'😀');
        assert.match(viewer.textContent,/<script>literal<\/script>/);assert.equal(findElements(viewer,node=>['SCRIPT','AUDIO'].includes(node.tagName)).length,0);
        assert.doesNotMatch(viewer.textContent,/1쪽|page=1/);
      }else if(scenario==='invalid')assert.match(viewer.textContent,/CITATION_CONTENT_UNAVAILABLE/);
      else assert.equal(viewer,undefined);
    }finally{pending.resolve();if(workspace)await workspace.cleanup();globalThis.fetch=oldFetch;}
  });
});

test('audio React binary verifies SHA, seeks only after metadata, ends at bound and releases playback',async()=>{
  const {createHash}=await import('node:crypto');
  const raw=new Uint8Array([82,73,70,70]),hash=createHash('sha256').update(raw).digest('hex');
  const oldFetch=globalThis.fetch,oldCreate=URL.createObjectURL,oldRevoke=URL.revokeObjectURL;
  const oldTimer=globalThis.setTimeout;
  const calls=[],revoked=[];let workspace,pauses=0,plays=0,stopAtEnd;
  globalThis.setTimeout=(callback,delay,...args)=>{if(delay===600)stopAtEnd=callback;return oldTimer(callback,delay,...args);};
  const locator={kind:'audio_segment',value:'{"segment":"s:1","start_ms":200,"end_ms":800,"start":0,"end":1}'};
  const citation={citation_id:'citation-audio',source_id:'source-1',source_version_id:'version-1',evidence_span_id:'span-audio',page:1,origin:'raw_source',context_item_id:'source-1',locator};
  const content={source_version_id:'version-1',source_sha256:hash,transcript_sha256:'b'.repeat(64),duration_ms:1000,
    selected_segment_id:'s:1',selected_start:0,selected_end:1,segments:[{segment_id:'s:1',start_ms:200,end_ms:800,text:'fixture'}]};
  URL.createObjectURL=()=> 'blob:audio-test';URL.revokeObjectURL=url=>revoked.push(url);
  globalThis.fetch=async(url,init)=>{calls.push({url,init});return url.endsWith('&representation=audio')?
    new Response(raw,{headers:{'Content-Type':'audio/wav'}}):Response.json(content);};
  try{
    workspace=await mountSourceRetryWorkspace({...adapter,listSources:async()=>[{...sourceRetryPayload,filename:'qa.wav'}],listStudioOutputs:async()=>[],
      citationUrl:value=>citationContentUrl('workspace-1',value,{notebookId:'notebook-1'}),
      loadNotebookConversation:async()=>({run_id:'run-audio',run_result_id:'result-audio',answer:'fixture',insufficient:false,
        citations:[{...citation,content_url:citationContentUrl('workspace-1',citation,{notebookId:'notebook-1'})}]})},'audio-binary');
    const link=findElements(workspace.container,node=>node.tagName==='A'&&node.textContent.includes('Citation'))[0];
    await workspace.act(async()=>link.dispatchEvent(new MinimalEvent('click')));
    const prepare=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='원본 불러오기')[0];
    assert.ok(prepare);assert.equal(calls.length,1);
    await workspace.act(async()=>{prepare.dispatchEvent(new MinimalEvent('click'));await new Promise(resolve=>setTimeout(resolve,20));});
    assert.equal(calls.length,2);assert.match(calls[1].url,/&representation=audio$/);
    const player=findElements(workspace.container,node=>node.tagName==='AUDIO')[0];assert.ok(player);
    player.pause=()=>pauses++;player.play=async()=>{plays++;};player.duration=1;player.currentTime=0;
    assert.equal(plays,0);
    await workspace.act(async()=>player.dispatchEvent(new MinimalEvent('loadedmetadata')));
    assert.equal(player.currentTime,0.2);assert.equal(plays,0);
    const play=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='선택 구간 재생')[0];
    await workspace.act(async()=>play.dispatchEvent(new MinimalEvent('click')));assert.equal(plays,1);
    assert.equal(typeof stopAtEnd,'function');
    await workspace.act(async()=>stopAtEnd());
    assert.equal(player.currentTime,0.8);assert.equal(pauses,1);
    player.currentTime=0.9;await workspace.act(async()=>player.dispatchEvent(new MinimalEvent('timeupdate')));
    assert.equal(player.currentTime,0.8);assert.equal(pauses,2);
    player.duration=1.06;
    await workspace.act(async()=>player.dispatchEvent(new MinimalEvent('loadedmetadata')));
    assert.match(workspace.container.textContent,/AUDIO_DURATION_MISMATCH/);
    await workspace.act(async()=>play.dispatchEvent(new MinimalEvent('click')));
    assert.equal(plays,1);assert.equal(pauses,3);
    const close=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='원문 닫기')[0];
    await workspace.act(async()=>close.dispatchEvent(new MinimalEvent('click')));
    assert.equal(pauses,4);assert.deepEqual(revoked,['blob:audio-test']);
  }finally{if(workspace)await workspace.cleanup();globalThis.fetch=oldFetch;URL.createObjectURL=oldCreate;URL.revokeObjectURL=oldRevoke;globalThis.setTimeout=oldTimer;}
});

test("DOCX 실제 React 원문은 emoji codepoint 강조·표 위치·safe text·늦은 응답을 결속한다", async(t)=>{
  for(const scenario of ['success','invalid','late-selection','late-other-source','late-close']) await t.test(scenario,async()=>{
    const oldFetch=globalThis.fetch;const pending=deferred();const calls=[];let workspace;
    const locator={kind:'docx_block',value:'{"block":"t:3:r:1:c:2:p:1","start":1,"end":2}'};
    const citation={citation_id:'citation-docx',source_id:'source-1',source_version_id:'version-1',evidence_span_id:'span-docx',page:1,origin:'raw_source',context_item_id:'source-1',locator};
    const content={source_version_id:'version-1',source_sha256:'a'.repeat(64),serialization_version:'docx-structure-v1',selected_block_id:'t:3:r:1:c:2:p:1',selected_start:1,selected_end:2,blocks:[
      {block_id:'p:2',ordinal:2,block_type:'paragraph',text:'',style_id:null,outline_level:null,table:null},
      {block_id:'t:3:r:1:c:2:p:1',ordinal:3,block_type:'table_cell_paragraph',text:'한😀글 <script>literal</script> https://evil',style_id:null,outline_level:null,table:{body_index:3,row:1,column:2,paragraph:1}},
    ]};
    if(scenario==='invalid')content.source_version_id='wrong';
    globalThis.fetch=async(url,init)=>{calls.push({url,init});if(scenario.startsWith('late'))await pending.promise;return Response.json(content);};
    try{
      workspace=await mountSourceRetryWorkspace({...adapter,listSources:async()=>[{...sourceRetryPayload,filename:'qa.docx'},{...sourceRetryPayload,source_id:'other-source',source_version_id:'other-version',filename:'other.docx'}],listStudioOutputs:async()=>[],citationUrl:value=>citationContentUrl('workspace-1',value,{notebookId:'notebook-1'}),loadNotebookConversation:async()=>({run_id:'run-docx',run_result_id:'result-docx',answer:'저장된 DOCX 답변',insufficient:false,citations:[{...citation,content_url:citationContentUrl('workspace-1',citation,{notebookId:'notebook-1'})}]})},`docx-${scenario}`);
      const link=findElements(workspace.container,node=>node.tagName==='A'&&node.textContent.includes('Citation'))[0];
      assert.ok(link);assert.match(link.textContent,/t:3:r:1:c:2:p:1/);assert.doesNotMatch(link.getAttribute('href'),/#page/);
      await workspace.act(async()=>link.dispatchEvent(new MinimalEvent('click')));
      assert.equal(calls.length,1);assert.equal(calls[0].init.credentials,'same-origin');
      if(scenario==='late-selection'){const selected=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.getAttribute?.('aria-pressed')==='true'&&node.textContent.includes('qa.docx'))[0];await workspace.act(async()=>selected.dispatchEvent(new MinimalEvent('click')));}
      if(scenario==='late-other-source'){const other=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent.includes('other.docx'))[0];await workspace.act(async()=>other.dispatchEvent(new MinimalEvent('click')));assert.equal(other.getAttribute('aria-pressed'),'true');}
      if(scenario==='late-close'){const close=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='원문 닫기')[0];await workspace.act(async()=>close.dispatchEvent(new MinimalEvent('click')));}
      if(scenario.startsWith('late'))await workspace.act(async()=>{pending.resolve();await Promise.resolve();});
      const viewer=findElements(workspace.container,node=>node.getAttribute?.('aria-label')==='DOCX 원문')[0];
      if(scenario==='success'){
        assert.ok(viewer);assert.match(viewer.textContent,/행 1 · 열 2/);assert.doesNotMatch(viewer.textContent,/1쪽|page=1/);
        assert.equal(findElements(viewer,node=>node.tagName==='MARK')[0].textContent,'😀');
        assert.match(viewer.textContent,/<script>literal<\/script>/);assert.equal(findElements(viewer,node=>['A','SCRIPT','IMG'].includes(node.tagName)).length,0);
        assert.ok(findElements(viewer,node=>node.getAttribute?.('class')==='citation-docx-scroll').length);
      }else if(scenario==='invalid')assert.match(viewer.textContent,/CITATION_CONTENT_UNAVAILABLE/);
      else assert.equal(viewer,undefined);
    }finally{pending.resolve();if(workspace)await workspace.cleanup();globalThis.fetch=oldFetch;}
  });
});

test("Image Citation은 같은 출처 원문과 EXIF 표시 기준 Region을 화면에서 연다", async () => {
  const citation = { citation_id: "citation-image", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-image", page: 1, origin: "raw_source", context_item_id: "source-1", locator: { kind: "image_region", value: "5,7,80,20,120,60" } };
  const workspace = await mountSourceRetryWorkspace({ ...adapter,
    citationUrl(value) { return citationContentUrl("workspace-1", value, { notebookId: "notebook-1" }); },
    async listSources() { return [{ ...sourceRetryPayload, filename: "qa.png" }]; },
    async listStudioOutputs() { return []; },
    async loadNotebookConversation() { return { run_id: "run-image", run_result_id: "result-image", answer: "이미지의 근거 답변", insufficient: false, citations: [{ ...citation, content_url: citationContentUrl("workspace-1", citation, { notebookId: "notebook-1" }) }] }; },
  }, "image-region");
  try {
    const link = findElements(workspace.container, (node) => node.tagName === "A" && node.textContent.includes("Citation"))[0];
    assert.ok(link);
    await workspace.act(async () => link.dispatchEvent(new MinimalEvent("click")));
    const image = findElements(workspace.container, (node) => node.tagName === "IMG")[0];
    assert.ok(image, "원문 이미지가 실제 DOM에 있어야 한다");
    assert.equal(image.getAttribute("src"), link.getAttribute("href"));
    assert.match(image.getAttribute("src"), /^\/bff\//u);
    Object.defineProperties(image, { naturalWidth: { value: 120 }, naturalHeight: { value: 60 } });
    await workspace.act(async () => image.dispatchEvent(new MinimalEvent("load")));
    const highlight = findElements(workspace.container, (node) => node.getAttribute?.("class") === "citation-image-region")[0];
    assert.ok(highlight);
    assert.equal(highlight.style.left, `${5 / 120 * 100}%`);
    assert.equal(highlight.style.top, `${7 / 60 * 100}%`);
    assert.equal(highlight.style.width, `${80 / 120 * 100}%`);
    assert.equal(highlight.style.height, `${20 / 60 * 100}%`);
    assert.match(workspace.container.textContent, /이미지의 근거 답변/u);
    const selection = findElements(workspace.container, (node) => node.tagName === "BUTTON" && node.getAttribute?.("aria-pressed") === "true" && node.textContent.includes("qa.png"))[0];
    assert.ok(selection);
    await workspace.act(async () => selection.dispatchEvent(new MinimalEvent("click")));
    assert.equal(findElements(workspace.container, (node) => node.tagName === "IMG").length, 0, "선택 해제 후 이전 원문 영역이 남지 않아야 한다");
  } finally { await workspace.cleanup(); }
});

test("Image Citation 닫기는 원문 표시만 제거한다", async () => {
  const citation = { citation_id: "citation-image", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-image", page: 1, origin: "raw_source", context_item_id: "source-1", locator: { kind: "image_region", value: "5,7,80,20,120,60" }, content_url: "/bff/api/workspaces/workspace-1/citations/citation-image/content?notebook_id=notebook-1" };
  const workspace = await mountSourceRetryWorkspace({ ...adapter,
    async listSources() { return [sourceRetryPayload]; }, async listStudioOutputs() { return []; },
    async loadNotebookConversation() { return { run_id: "run-image", run_result_id: "result-image", answer: "원문 근거 답변", insufficient: false, citations: [citation] }; },
  }, "image-close");
  try {
    const link = findElements(workspace.container, (node) => node.tagName === "A" && node.textContent.includes("Citation"))[0];
    await workspace.act(async () => link.dispatchEvent(new MinimalEvent("click")));
    const close = findElements(workspace.container, (node) => node.tagName === "BUTTON" && node.textContent === "원문 닫기")[0];
    await workspace.act(async () => close.dispatchEvent(new MinimalEvent("click")));
    assert.equal(findElements(workspace.container, (node) => node.tagName === "IMG").length, 0);
    assert.match(workspace.container.textContent, /원문 근거 답변/u);
  } finally { await workspace.cleanup(); }
});

test("HTTP QA React 질문은 randomUUID 없이 실제 adapter와 secure idempotency key를 사용한다", async () => {
  const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const calls = [];
  let workspace;
  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: { getRandomValues(bytes) { bytes.fill(7); return bytes; } },
    });
    workspace = await mountSourceRetryWorkspace({
      ...adapter,
      async listSources() { return []; },
      async listStudioOutputs() { return []; },
      async askQuestion(input, options) {
        calls.push({ input, options });
        return { run_id: "run-1", run_result_id: "result-1", answer: "응답", insufficient: false, citations: [] };
      },
    }, "http-qa-question");
    const textarea = findElements(workspace.container, (node) => node.tagName === "TEXTAREA")[0];
    const textareaProps = textarea[Object.keys(textarea).find((key) => key.startsWith("__reactProps$"))];
    textarea.value = "다음 작업은?";
    await workspace.act(async () => textareaProps.onChange({ currentTarget: textarea, target: textarea }));
    const form = findElements(workspace.container, (node) => node.tagName === "FORM" && node.getAttribute("class") === "conversation-composer")[0];
    const formProps = form[Object.keys(form).find((key) => key.startsWith("__reactProps$"))];
    await workspace.act(async () => formProps.onSubmit({ preventDefault() {} }));
    assert.equal(calls.length, 1);
    assert.equal(calls[0].input.question, "다음 작업은?");
    assert.match(calls[0].options.idempotencyKey, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u);
  } finally {
    if (workspace) await workspace.cleanup();
    if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto);
    else delete globalThis.crypto;
  }
});

test("HTTP QA React TXT 선택은 randomUUID 없이 실제 Source POST를 전송한다", async () => {
  const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const calls = [];
  let workspace;
  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: { getRandomValues(bytes) { bytes.fill(7); return bytes; } },
    });
    workspace = await mountSourceRetryWorkspace({
      ...adapter,
      async listSources() { return []; },
      async listStudioOutputs() { return []; },
      async uploadPdf(file) {
        return uploadSource("workspace-1", file, {
          notebookId: "notebook-1",
          fetchImpl: async (url, init) => {
            calls.push({ url, init });
            return Response.json({ data: {
              source_id: "source-1", source_version_id: "version-1", object_id: "a".repeat(32),
              digest_sha256: "b".repeat(64), byte_size: file.size, status: "accepted", replayed: false,
              processing_run_id: "processing-1", processing_state: "processing", job_state: "queued",
            }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } }, { status: 202 });
          },
        });
      },
      async getProcessingStatus() { return {
        processing_run_id: "processing-1", source_id: "source-1", source_version_id: "version-1",
        processing_state: "completed", source_state: "ready", job_state: "completed", safe_error_code: null,
      }; },
    }, "http-qa-upload");
    const addButton = findElements(workspace.container, (node) => node.tagName === "BUTTON" && node.getAttribute?.("class") === "source-add-button")[0];
    await workspace.act(async () => addButton.dispatchEvent(new MinimalEvent("click")));
    assert.match(workspace.container.textContent, /질문·Studio 처리 지원: PDF\(문서 모델 필요\), TXT\/MD\/CSV/u);
    assert.match(workspace.container.textContent, /PNG\/JPG\/JPEG\(내부 Vision 모델·OCR 필요\)/u);
    assert.match(workspace.container.textContent, /XLSX\(내부 모델·셀 검증 필요\)/u);
    assert.match(workspace.container.textContent, /DOCX\(제한형 문단·병합 없는 단순 표, 내부 모델·구간 검증 필요\)/u);
    assert.match(workspace.container.textContent, /PPTX\(제한형 텍스트·병합 없는 단순 표/u);
    assert.match(workspace.container.textContent, /WAV\(PCM16·1~120초·25MiB, QA 내부 ASR와 의미 모델 필요\)/u);
    assert.match(workspace.container.textContent, /MP3\/M4A는 미지원/u);
    assert.ok(findElements(workspace.container,node=>node.tagName==='INPUT'&&node.getAttribute?.('accept')?.split(',').includes('.wav')).length);
    const fileInput = findElements(workspace.container, (node) => node.tagName === "INPUT" && node.getAttribute?.("accept")?.includes(".txt"))[0];
    assert.ok(fileInput.getAttribute('accept').split(',').includes('.xlsx'));
    assert.ok(fileInput.getAttribute('accept').split(',').includes('.docx'));
    assert.ok(fileInput.getAttribute('accept').split(',').includes('.pptx'));
    assert.equal(fileInput.getAttribute('accept').split(',').includes('.mp3'), false);
    Object.defineProperty(fileInput, "files", {
      configurable: true, value: [new File(["ORANGE-COMPASS-42"], "notes.txt", { type: "text/plain" })],
    });
    await workspace.act(async () => { fileInput.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); await Promise.resolve(); });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/sources");
    assert.match(calls[0].init.headers["Idempotency-Key"], /^source-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u);
  } finally {
    if (workspace) await workspace.cleanup();
    if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto);
    else delete globalThis.crypto;
  }
});

test("같은 PDF를 다시 선택해도 Source 업로드 change가 재실행된다", async () => {
  let uploadCalls = 0;
  let listSourcesCalls = 0;
  const submissions = new Map();
  const workspace = await mountSourceRetryWorkspace({
    ...adapter,
    async listSources() {
      listSourcesCalls += 1;
      const latest = [...submissions.values()].at(-1);
      return latest ? [{
        source_id: latest.source_id,
        source_version_id: latest.source_version_id,
        filename: "same.pdf",
        source_state: "ready",
        processing_state: "completed",
        job_state: "completed",
      }] : [];
    },
    async listStudioOutputs() { return []; },
    async uploadPdf() {
      uploadCalls += 1;
      const submission = {
        processing_run_id: `processing-${uploadCalls}`,
        source_id: `source-${uploadCalls}`,
        source_version_id: `version-${uploadCalls}`,
      };
      submissions.set(submission.processing_run_id, submission);
      return submission;
    },
    async getProcessingStatus(processingRunId) {
      const submission = submissions.get(processingRunId);
      return {
        ...submission,
        processing_state: "completed",
        source_state: "ready",
        job_state: "completed",
        safe_error_code: null,
      };
    },
  }, "same-file");
  try {
    const fileInput = findElements(workspace.container, (node) => (
      node.tagName === "INPUT" && node.getAttribute?.("accept") === "application/pdf"
    ))[0];
    const sameFile = { name: "same.pdf", type: "application/pdf", size: 4 };
    const selectSameFile = () => {
      const browserValue = "C:\\fakepath\\same.pdf";
      if (fileInput.value === browserValue) return;
      fileInput.value = browserValue;
      Object.defineProperty(fileInput, "files", { configurable: true, value: [sameFile] });
      fileInput.dispatchEvent(new MinimalEvent("change"));
    };

    await workspace.act(async () => { selectSameFile(); await Promise.resolve(); await Promise.resolve(); });
    await workspace.act(async () => { selectSameFile(); await Promise.resolve(); await Promise.resolve(); });
    await workspace.act(async () => workspace.wait(50));

    assert.equal(uploadCalls, 2);
    assert.equal(fileInput.value, "");
    assert.equal(listSourcesCalls, 3);
    assert.match(workspace.container.textContent, /same\.pdf/u);
    assert.doesNotMatch(workspace.container.textContent, /처리 중 · 잠시만 기다려 주세요/u);
  } finally {
    await workspace.cleanup();
  }
});

test("Source transient fetch TypeError는 bounded 1회 retry 후 정상 복구한다", async () => {
  let calls = 0;
  const workspace = await mountSourceRetryWorkspace({
    ...adapter,
    async listSources() {
      calls += 1;
      if (calls === 1) throw new TypeError("Failed to fetch");
      return [sourceRetryPayload];
    },
    async listStudioOutputs() { return []; },
  }, "transient");
  try {
    await workspace.act(async () => workspace.wait(350));
    assert.equal(calls, 2);
    assert.match(workspace.container.textContent, /ready\.pdf/u);
    assert.doesNotMatch(workspace.container.textContent, /Source를 불러오지 못했습니다/u);
  } finally {
    await workspace.cleanup();
  }
});

test("Source retry 대기 중 AbortError는 재호출하지 않는다", async () => {
  let calls = 0;
  let observedSignal;
  const workspace = await mountSourceRetryWorkspace({
    ...adapter,
    async listSources({ signal }) {
      calls += 1;
      observedSignal = signal;
      await new Promise((resolve, reject) => {
        signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
      });
      return [sourceRetryPayload];
    },
    async listStudioOutputs() { return []; },
  }, "abort");
  try {
    assert.equal(calls, 1);
    assert.equal(observedSignal.aborted, false);
  } finally {
    await workspace.cleanup();
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.equal(calls, 1);
  }
});

test("Source contract 오류는 retry하지 않고 1회 호출로 안전 오류를 유지한다", async () => {
  let calls = 0;
  const workspace = await mountSourceRetryWorkspace({
    ...adapter,
    async listSources() {
      calls += 1;
      throw new Error("SOURCE_LIST_RESPONSE_INVALID");
    },
    async listStudioOutputs() { return []; },
  }, "contract");
  try {
    await workspace.act(async () => workspace.wait(350));
    assert.equal(calls, 1);
    assert.match(workspace.container.textContent, /Source를 불러오지 못했습니다/u);
  } finally {
    await workspace.cleanup();
  }
});

test("질문 실패는 로드된 Source·Studio 잠금을 보존하고 안전 오류만 투영한다", () => {
  const current = {
    ...createProductWorkspaceState({ status: "ready" }),
    sources: [{ sourceId: "source-1", sourceVersionId: "version-1", filename: "ready.pdf", ready: true }],
    selectedSource: { sourceId: "source-1", sourceVersionId: "version-1", filename: "ready.pdf", ready: true },
    studioLocks: [{ lock_type: "ruleset", version: "v1" }],
    studioOutputs: [{ studio_output_id: "output-1" }],
  };

  const failed = projectQuestionFailureState(current, new Error("TEXT_MODEL_NOT_SELECTED"));

  assert.equal(failed.status, "ready");
  assert.equal(failed.safeError, null);
  assert.equal(failed.conversationSafeError, "TEXT_MODEL_NOT_SELECTED");
  assert.deepEqual(failed.sources, current.sources);
  assert.deepEqual(failed.selectedSource, current.selectedSource);
  assert.deepEqual(failed.studioLocks, current.studioLocks);
  assert.deepEqual(failed.studioOutputs, current.studioOutputs);
});

test("Product Workspace Adapter는 exact 7개 메서드를 요구한다", () => {
  assert.equal(assertProductWorkspaceAdapter(adapter), adapter);
  assert.throws(
    () => assertProductWorkspaceAdapter({ ...adapter, createReport: undefined }),
    /WORKSPACE_ADAPTER_INVALID/,
  );
});

test("근거 보고서는 Raw·Daon 혼합 Context의 sufficient Citation 답변이면 생성 가능하다", () => {
  const ready = {
    ...createProductWorkspaceState({ status: "ready" }),
    selectedSource: { sourceId: "source-1", sourceVersionId: "version-1" },
    answer: {
      run_id: "run-1", run_result_id: "result-1", answer: "근거 답변",
      insufficient: false,
      citations: [{ citation_id: "citation-1", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-1", page: 2, origin: "raw_source", context_item_id: "source-1", locator: { kind: "page", value: "2" } }],
    },
  };
  assert.equal(canCreateGroundedReport(ready), true);
  assert.equal(canCreateGroundedReport({ ...ready, answer: { ...ready.answer, insufficient: true } }), false);
  assert.equal(canCreateGroundedReport({ ...ready, answer: { ...ready.answer, citations: [] } }), false);
  const daonOnly = {
    ...ready, selectedSource: null,
    answer: { ...ready.answer, citations: [{
      ...ready.answer.citations[0], source_id: "source-daon", source_version_id: "version-daon",
      origin: "daon_knowledge", context_item_id: "package-daon", locator: { kind: "section", value: "summary" },
    }] },
  };
  assert.equal(canCreateGroundedReport(daonOnly), true);
  assert.equal(canCreateGroundedReport({ ...daonOnly, answer: { ...daonOnly.answer, citations: [{ ...daonOnly.answer.citations[0], source_version_id: "" }] } }), false);
});

test("재진입은 명시 선택이 없을 때만 최근 Citation의 정확한 ready Notebook Source를 복원한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-citation-selection-react-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "citation-selection" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(pathToFileURL(path.join(output, entry)).href);
    const first = { source_id: "source-original", source_version_id: "version-original", filename: "original.txt", source_state: "ready", processing_state: "completed", job_state: "completed" };
    const knowledge = { ...first, source_id: "source-knowledge", source_version_id: "version-knowledge", filename: "registered.json" };
    const citation = { citation_id: "citation-1", source_id: knowledge.source_id, source_version_id: knowledge.source_version_id,
      evidence_span_id: "span-1", page: 1, origin: "raw_source", context_item_id: knowledge.source_id, locator: { kind: "page", value: "1" } };
    for (const scenario of [
      { name: "restore", expected: knowledge, citation },
      { name: "wrong-version", expected: first, citation: { ...citation, source_version_id: "old-version" } },
      { name: "not-in-notebook", expected: first, citation: { ...citation, source_id: "private-source" } },
      { name: "unready", expected: first, citation, unready: true },
      { name: "history-failed", expected: first, failure: true },
      { name: "explicit", expected: first, citation, explicit: first },
      { name: "explicit-knowledge", expected: knowledge, citation: { ...citation, source_id: first.source_id, source_version_id: first.source_version_id }, explicit: knowledge },
    ]) {
      const effectAdapter = { ...adapter,
        async listSources() { return [first, scenario.unready ? { ...knowledge, job_state: "pending" } : knowledge]; },
        async listStudioOutputs() { return []; },
        async loadNotebookConversation() { if (scenario.failure) throw new Error("CONVERSATION_UNAVAILABLE");
          return { run_id: "run-1", run_result_id: "result-1", answer: "저장된 지식 답변", insufficient: false, citations: [scenario.citation] }; },
      };
      const state = createProductWorkspaceState({ status: "loading" });
      if (scenario.explicit) state.selectedSource = { sourceId: scenario.explicit.source_id, sourceVersionId: scenario.explicit.source_version_id, ready: true };
      const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
      await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter, state })); await Promise.resolve(); await Promise.resolve(); });
      const sourceButton = (filename) => findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes(filename) && node.getAttribute("aria-pressed") !== null)[0];
      assert.equal(sourceButton(scenario.expected.filename).getAttribute("aria-pressed"), "true", scenario.name);
      if (scenario.name === "restore") {
        assert.match(container.textContent, /빠른 근거 보고서/);
        await act(async () => { sourceButton(first.filename).dispatchEvent(new MinimalEvent("click")); });
        assert.equal(sourceButton(first.filename).getAttribute("aria-pressed"), "true", "explicit click must select original Source");
      }
      await act(async () => reactRoot.unmount()); reactRoot = null; dom.document.body.removeChild(container);
    }
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("생산 지식 등록 성공은 실제 Notebook Source 재조회 후 새 자료를 선택 가능하게 한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-knowledge-link-react-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "knowledge-link" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(pathToFileURL(path.join(output, entry)).href);
    let registered = false; let listCalls = 0; const writes = [];
    const source = { source_id: "source-1", source_version_id: "version-1", filename: "original.txt", source_state: "ready", processing_state: "completed", job_state: "completed" };
    const knowledge = { source_id: "source-k", source_version_id: "version-k", filename: "approved.knowledge.json", source_state: "ready", processing_state: "completed", job_state: "completed" };
    const effectAdapter = { ...adapter,
      async listSources() { listCalls++; return registered ? [source, knowledge] : [source]; },
      async listStudioOutputs() { return []; },
      async listProductStudioOutputs() { return { outputs: [{ studio_output_id: "output-1", output_version_id: "output-version-1", output_type: "evidence_report", title: "승인 보고서", status: "approved" }], studioLocks: [] }; },
      async listStudioVersions() { return [{ output_version_id: "output-version-1", status: "approved", content: { summary: "승인 본문" } }]; },
      async issueStudioStepUp(group, target) { assert.equal(group, "final_approval_or_knowledge_registration"); assert.equal(target, "output-version-1"); return "fixture-grant"; },
      async createStudioAction(action, payload) { writes.push([action, payload]); registered = true;
        return { record_id: "registration-1", status: "registered", registered_source_version_id: "version-k", searchable: true }; },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const click = async (label) => { const button = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.trim() === label)[0]; assert.ok(button, label);
      await act(async () => { button.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); }); };
    assert.equal(listCalls, 1); assert.doesNotMatch(container.textContent, /approved\.knowledge\.json/);
    const originalButton = () => findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("original.txt") && node.getAttribute("aria-pressed") !== null)[0];
    await act(async () => { originalButton().dispatchEvent(new MinimalEvent("click")); });
    await act(async () => { originalButton().dispatchEvent(new MinimalEvent("click")); });
    assert.equal(originalButton().getAttribute("aria-pressed"), "true");
    await click("승인 보고서"); await click("생산 지식 등록");
    const label = findElements(container, (node) => node.tagName === "LABEL" && node.textContent.includes("추가 인증 비밀번호"))[0];
    const input = findElements(label, (node) => node.tagName === "INPUT")[0];
    const props = input[Object.keys(input).find((key) => key.startsWith("__reactProps"))];
    await act(async () => props.onChange({ currentTarget: { value: "fixture password only" } }));
    await click("추가 인증 확인");
    assert.equal(writes.length, 1); assert.equal(writes[0][0], "knowledge-registrations");
    assert.equal(writes[0][1].step_up_authorization, "fixture-grant");
    assert.equal(listCalls, 2);
    assert.equal(originalButton().getAttribute("aria-pressed"), "true", "same-adapter Source refresh must preserve explicit selection");
    assert.match(container.textContent, /생산 지식이 등록되었습니다/);
    assert.match(container.textContent, /approved\.knowledge\.json/);
    const sourceButton = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("approved.knowledge.json") && node.getAttribute("aria-pressed") !== null)[0];
    assert.ok(sourceButton); assert.equal(sourceButton.disabled, false);
    await act(async () => sourceButton.dispatchEvent(new MinimalEvent("click")));
    assert.equal(sourceButton.getAttribute("aria-pressed"), "true");
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("Source 작업은 Notebook 연결 해제와 즉시 삭제를 분리한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-actions-react-"));
  const dom = installMinimalDom();
  const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  let reactRoot;
  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true, value: { getRandomValues(bytes) { bytes.fill(7); return bytes; } },
    });
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-actions" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("source-actions") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?sourceActions=${Date.now()}`);
    const calls = [];
    const effectAdapter = {
      ...adapter,
      bindingEtag: '"notebook-binding:1"',
      async listSources() { return [{ source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }]; },
      async listStudioOutputs() { return []; },
      async unbindSource(source, options) { calls.push(["unbind", source.sourceId, options.bindingEtag]); },
      async requestSourceDeletion(source, options) { calls.push(["request", source.sourceId, options.idempotencyKey]); return { data: { request_id: "request-1", source_id: source.sourceId, state: "purged" }, etag: '"deletion:request-1:1"' }; },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const action = findElements(container, (node) => node.tagName === "BUTTON" && /ready\.pdf 작업/u.test(node.getAttribute?.("aria-label") ?? ""))[0];
    await act(async () => action.dispatchEvent(new MinimalEvent("click")));
    assert.match(container.textContent, /원본 Source는 보존/u);
    const deletion = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "Source 삭제 요청")[0];
    await act(async () => deletion.dispatchEvent(new MinimalEvent("click")));
    const confirm = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 확인")[0];
    await act(async () => { confirm.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0].slice(0, 2), ["request", "source-1"]);
    assert.match(calls[0][2], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u);
    assert.match(container.textContent, /Source가 즉시 삭제되었습니다/u);
    assert.equal(findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 취소").length, 0);
    assert.equal(calls.length, 1);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto);
    else delete globalThis.crypto;
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("새로고침 뒤 완료된 즉시 삭제 상태를 복원하고 취소 동작을 제공하지 않는다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-deletion-restore-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "deletion-restore" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("deletion-restore") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?restore=${Date.now()}`);
    const calls = [];
    const effectAdapter = { ...adapter, async listSources() { return []; }, async listStudioOutputs() { return []; },
      async listSourceDeletionRequests() { return [{ request_id: "request-1", source_id: "source-1", state: "purged", version: 2, grace_until: "2026-09-20T00:00:00Z", legal_hold_active: false }]; } };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const action = findElements(container, (node) => node.tagName === "BUTTON" && /source-1 삭제 요청 상태/u.test(node.getAttribute?.("aria-label") ?? ""))[0];
    assert.ok(action); await act(async () => action.dispatchEvent(new MinimalEvent("click")));
    assert.match(container.textContent, /Source가 즉시 삭제되었습니다/u);
    assert.equal(findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 취소").length, 0);
    assert.deepEqual(calls, []);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("Source lifecycle pending 응답은 Adapter·Notebook 전환 즉시 폐기된다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-lifecycle-epoch-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "lifecycle-epoch" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("lifecycle-epoch") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?epoch=${Date.now()}`);
    const pending = deferred(); const calls = [];
    const oldAdapter = { ...adapter, notebookContext: { notebook_id: "notebook-old" },
      async listSources() { return [{ source_id: "source-old", source_version_id: "version-old", filename: "old.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }]; },
      async listStudioOutputs() { return []; }, async requestSourceDeletion() { calls.push("old-request"); return pending.promise; } };
    const newAdapter = { ...adapter, notebookContext: { notebook_id: "notebook-new" }, async listSources() { return []; }, async listStudioOutputs() { return []; },
      async listSourceDeletionRequests() { return [{ request_id: "request-new", source_id: "source-new", state: "grace_period", version: 1, grace_until: "2026-09-20T00:00:00Z", legal_hold_active: false }]; },
      async cancelSourceDeletionRequest() { calls.push("new-cancel"); return { data: { request_id: "request-new", source_id: "source-new", state: "cancelled" }, etag: '"deletion:request-new:2"' }; } };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-old", adapter: oldAdapter })); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => findElements(container, (node) => node.tagName === "BUTTON" && /old\.pdf 작업/u.test(node.getAttribute?.("aria-label") ?? ""))[0].dispatchEvent(new MinimalEvent("click")));
    await act(async () => findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "Source 삭제 요청")[0].dispatchEvent(new MinimalEvent("click")));
    await act(async () => findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 확인")[0].dispatchEvent(new MinimalEvent("click")));
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-new", adapter: newAdapter })); await Promise.resolve(); await Promise.resolve(); });
    assert.doesNotMatch(container.textContent, /old\.pdf|요청을 처리하지 못/u);
    pending.resolve({ data: { request_id: "request-old", source_id: "source-old", state: "grace_period" }, etag: '"deletion:request-old:1"' });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    assert.doesNotMatch(container.textContent, /old\.pdf|request-old/u);
    const action = findElements(container, (node) => node.tagName === "BUTTON" && /source-new 삭제 요청 상태/u.test(node.getAttribute?.("aria-label") ?? ""))[0];
    await act(async () => action.dispatchEvent(new MinimalEvent("click")));
    await act(async () => { findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 취소")[0].dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls, ["old-request", "new-cancel"]);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("Studio 목록 실패는 ready Source 질문을 보존하고 별도 안전 경고로 투영한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-studio-unavailable-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "studio-unavailable" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("studio-unavailable") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?studioUnavailable=${Date.now()}`);
    const effectAdapter = {
      ...adapter,
      async listSources() { return [{ source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }]; },
      async listStudioOutputs() { throw new Error("STUDIO_DATABASE_UNAVAILABLE http://internal.invalid stack"); },
      async loadNotebookConversation() { throw new Error("CONVERSATION_DATABASE_UNAVAILABLE http://internal.invalid stack"); },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /ready\.pdf/u);
    const questionInput = findElements(container, (node) => node.tagName === "TEXTAREA" && node.parentNode?.textContent.startsWith("질문"))[0];
    assert.equal(questionInput.disabled, false);
    assert.match(container.textContent, /불러오지 못했습니다/u);
    assert.doesNotMatch(container.textContent, /대화를 불러오지 못했습니다/u);
    assert.doesNotMatch(container.textContent, /STUDIO_LIST_FAILED/u);
    assert.doesNotMatch(container.textContent, /internal\.invalid|stack/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    let sourceLoads = 0;
    const retryAdapter = {
      ...adapter,
      async listSources() {
        sourceLoads += 1;
        if (sourceLoads === 1) throw new Error("SOURCE_LIST_FAILED http://internal.invalid stack");
        return [{ source_id: "source-1", source_version_id: "version-1", filename: "retried.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }];
      },
      async listStudioOutputs() { return []; },
    };
    const retryContainer = dom.document.createElement("div"); dom.document.body.appendChild(retryContainer);
    reactRoot = createRoot(retryContainer);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: retryAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const retryButton = findElements(retryContainer, (node) => node.tagName === "BUTTON" && node.textContent === "다시 시도")[0];
    assert.ok(retryButton);
    assert.doesNotMatch(retryContainer.textContent, /Source를 추가해 주세요/u);
    await act(async () => { retryButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
    assert.equal(sourceLoads, 2);
    assert.match(retryContainer.textContent, /retried\.pdf/u);
    assert.doesNotMatch(retryContainer.textContent, /internal\.invalid|stack/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    let transientLoads = 0;
    const initialSourceLoad = deferred();
    const transientAdapter = {
      ...adapter,
      async listSources() {
        transientLoads += 1;
        if (transientLoads === 1) return initialSourceLoad.promise;
        return [];
      },
      async listStudioOutputs() { return []; },
    };
    const transientContainer = dom.document.createElement("div"); dom.document.body.appendChild(transientContainer);
    reactRoot = createRoot(transientContainer);
    await act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: transientAdapter }));
      await Promise.resolve(); await Promise.resolve();
    });
    assert.equal(transientLoads, 1);
    const initialError = new Error("STUDIO_DATABASE_UNAVAILABLE");
    initialError.retryable = true;
    await act(async () => { initialSourceLoad.reject(initialError); await Promise.resolve(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    assert.equal(transientLoads, 2);
    assert.match(transientContainer.textContent, /Source를 추가해 주세요/u);
    assert.doesNotMatch(transientContainer.textContent, /Source를 불러오지 못했습니다/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    const delayedKnowledge = deferred();
    const delayedStudio = deferred();
    let emptyContextSourceLoads = 0;
    const emptyContextContainer = dom.document.createElement("div"); dom.document.body.appendChild(emptyContextContainer);
    const emptyContextAdapter = {
      ...adapter,
      notebookContext: { notebook_id: "notebook-empty", sources: [] },
      async listSources() {
        emptyContextSourceLoads += 1;
        return [{ source_id: "source-after-upload", source_version_id: "version-after-upload", filename: "after-upload.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }];
      },
      async listKnowledgePackages() { return delayedKnowledge.promise; },
      async listStudioOutputs() { return delayedStudio.promise; },
    };
    reactRoot = createRoot(emptyContextContainer);
    await act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, {
        workspaceId: "workspace-1",
        adapter: emptyContextAdapter,
        state: createProductWorkspaceState({ status: "error", safeError: "SOURCE_LIST_FAILED" }),
      }));
      await Promise.resolve(); await Promise.resolve();
    });
    assert.equal(emptyContextSourceLoads, 1);
    delayedKnowledge.resolve([]);
    delayedStudio.resolve([]);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    assert.match(emptyContextContainer.textContent, /after-upload\.pdf/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    let cloudOverallStatus = "ready";
    const splitStatusContainer = dom.document.createElement("div"); dom.document.body.appendChild(splitStatusContainer);
    const splitStatusAdapter = {
      ...adapter,
      async listSources() { throw new Error("SOURCE_LIST_FAILED"); },
      async listStudioOutputs() { return []; },
      async getOperationsStatus() { return { overall_status: cloudOverallStatus, components: [] }; },
    };
    reactRoot = createRoot(splitStatusContainer);
    await act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: splitStatusAdapter }));
      await Promise.resolve(); await Promise.resolve();
    });
    assert.match(splitStatusContainer.textContent, /Source 확인 필요/u);
    assert.match(splitStatusContainer.textContent, /Cloud 미확인/u);
    assert.doesNotMatch(splitStatusContainer.textContent, /주의 · Cloud/u);
    const operationsButton = findElements(splitStatusContainer, (node) => node.tagName === "BUTTON" && node.textContent === "운영상태")[0];
    await act(async () => { operationsButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
    assert.match(splitStatusContainer.textContent, /Cloud 정상/u);
    assert.match(splitStatusContainer.textContent, /Source 확인 필요/u);
    cloudOverallStatus = "error";
    const closeButton = findElements(splitStatusContainer, (node) => node.tagName === "BUTTON" && node.getAttribute?.("aria-label") === "닫기")[0];
    await act(async () => { closeButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    await act(async () => { operationsButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
    assert.match(splitStatusContainer.textContent, /Cloud 주의/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("Source 로드는 다른 pane projection 오류와 stale 결과에서 독립적이다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-ownership-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-ownership" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("source-ownership") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?sourceOwnership=${Date.now()}`);
    const source = { source_id: "source-current", source_version_id: "version-current", filename: "current.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" };
    const projectionFailureAdapter = {
      ...adapter,
      async listSources() { return [source]; },
      async listKnowledgePackages() { return { invalid: true }; },
      async listStudioOutputs() { return { outputs: null, studioLocks: [{ lock_type: "ruleset" }] }; },
      async loadNotebookConversation() { throw new Error("CONVERSATION_UNAVAILABLE"); },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-current", adapter: projectionFailureAdapter }));
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    assert.match(container.textContent, /current\.pdf/u);
    assert.doesNotMatch(container.textContent, /Source를 불러오지 못했습니다/u);
    assert.match(container.textContent, /지식을 불러오지 못했습니다/u);

    const oldSource = deferred();
    const oldAdapter = { ...adapter, async listSources() { return oldSource.promise; }, async listStudioOutputs() { return []; } };
    const newAdapter = { ...adapter, async listSources() { return [source]; }, async listStudioOutputs() { return []; } };
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-old", adapter: oldAdapter })); await Promise.resolve(); });
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-current", adapter: newAdapter })); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { oldSource.reject(new Error("OLD_SOURCE_FAILED")); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /current\.pdf/u);
    assert.doesNotMatch(container.textContent, /OLD_SOURCE_FAILED|Source를 불러오지 못했습니다/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("Source 재로드 오류는 기존 populated 목록을 빈 목록으로 덮지 않는다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-preserve-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-preserve" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("source-preserve") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?sourcePreserve=${Date.now()}`);
    const source = { source_id: "source-current", source_version_id: "version-current", filename: "current.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" };
    const initialState = {
      ...createProductWorkspaceState({ status: "ready" }),
      sources: [{ sourceId: source.source_id, sourceVersionId: source.source_version_id, filename: source.filename, ready: true }],
      selectedSource: { sourceId: source.source_id, sourceVersionId: source.source_version_id, filename: source.filename, ready: true },
    };
    const failedAdapter = { ...adapter, async listSources() { throw new Error("SOURCE_LIST_UNAVAILABLE"); }, async listStudioOutputs() { return []; } };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-current", adapter: failedAdapter, state: initialState })); await new Promise((resolve) => setTimeout(resolve, 350)); });
    assert.match(container.textContent, /current\.pdf/u);
    assert.match(container.textContent, /Source를 불러오지 못했습니다/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("빈 Notebook은 작업 상담을 실행하고 근거 미사용을 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-general-conversation-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "general-conversation" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("general-conversation") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?general=${Date.now()}`);
    const calls = [];
    let authorizeCalls = 0;
    const effectAdapter = {
      ...adapter,
      async authorizeQuestion() { authorizeCalls += 1; throw new Error("QUESTION_AUTHORIZATION_MUST_NOT_RUN"); },
      async listSources() { return []; },
      async listStudioOutputs() { return []; },
      async askQuestion(input) {
        calls.push(input);
        return { run_id: "run-general", run_result_id: "result-general", answer: "안녕하세요.", insufficient: false, citations: [] };
      },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    assert.equal(findElements(container, (node) => node.tagName === "INPUT" && node.getAttribute("autocomplete") === "current-password").length, 0);
    assert.doesNotMatch(container.textContent, /추가 인증|현재 비밀번호/u);
    const textarea = findElements(container, (node) => node.tagName === "TEXTAREA")[0];
    assert.equal(textarea.disabled, false);
    const textareaProps = textarea[Object.keys(textarea).find((key) => key.startsWith("__reactProps$"))];
    textarea.value = "다음 작업을 어떻게 진행할까?";
    await act(async () => textareaProps.onChange({ currentTarget: textarea, target: textarea }));
    const submit = findElements(container, (node) => node.tagName === "BUTTON" && node.getAttribute("aria-label") === "질문 실행")[0];
    assert.equal(submit.disabled, false);
    const form = findElements(container, (node) => node.tagName === "FORM" && node.getAttribute("class") === "conversation-composer")[0];
    const formProps = form[Object.keys(form).find((key) => key.startsWith("__reactProps$"))];
    await act(async () => formProps.onSubmit({ preventDefault() {} }));
    assert.deepEqual(calls, [{ knowledgeContext: null, question: "다음 작업을 어떻게 진행할까?" }]);
    assert.equal(authorizeCalls, 0);
    assert.match(container.textContent, /작업 상담 · 근거 미사용/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    const groundedContainer = dom.document.createElement("div"); dom.document.body.appendChild(groundedContainer);
    const groundedCalls = [];
    const groundedAdapter = {
      ...effectAdapter,
      async listSources() {
        return [{ source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }];
      },
      async askQuestion(input) {
        groundedCalls.push(input);
        return { run_id: "run-grounded", run_result_id: "result-grounded", answer: "근거 부족 모양", insufficient: false, citations: [] };
      },
    };
    reactRoot = createRoot(groundedContainer);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: groundedAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const groundedTextarea = findElements(groundedContainer, (node) => node.tagName === "TEXTAREA")[0];
    const groundedTextareaProps = groundedTextarea[Object.keys(groundedTextarea).find((key) => key.startsWith("__reactProps$"))];
    groundedTextarea.value = "선택 Source를 근거로 답해줘";
    await act(async () => groundedTextareaProps.onChange({ currentTarget: groundedTextarea, target: groundedTextarea }));
    const groundedForm = findElements(groundedContainer, (node) => node.tagName === "FORM" && node.getAttribute("class") === "conversation-composer")[0];
    const groundedFormProps = groundedForm[Object.keys(groundedForm).find((key) => key.startsWith("__reactProps$"))];
    await act(async () => groundedFormProps.onSubmit({ preventDefault() {} }));
    assert.equal(authorizeCalls, 0);
    assert.deepEqual(groundedCalls, [{
      knowledgeContext: {
        mode: "raw_only",
        resources: [{ resourceKind: "source", resourceId: "source-1", versionId: "version-1" }],
      },
      question: "선택 Source를 근거로 답해줘",
    }]);
    assert.doesNotMatch(groundedContainer.textContent, /일반 대화 · 근거 미사용/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    let resolveOldQuestion;
    const oldQuestion = new Promise((resolve) => { resolveOldQuestion = resolve; });
    const staleAdapter = { ...effectAdapter, async askQuestion() { return oldQuestion; } };
    const staleContainer = dom.document.createElement("div"); dom.document.body.appendChild(staleContainer);
    reactRoot = createRoot(staleContainer);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-old", adapter: staleAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const staleTextarea = findElements(staleContainer, (node) => node.tagName === "TEXTAREA")[0];
    const staleTextareaProps = staleTextarea[Object.keys(staleTextarea).find((key) => key.startsWith("__reactProps$"))];
    staleTextarea.value = "안녕하세요!";
    await act(async () => staleTextareaProps.onChange({ currentTarget: staleTextarea, target: staleTextarea }));
    const staleForm = findElements(staleContainer, (node) => node.tagName === "FORM" && node.getAttribute("class") === "conversation-composer")[0];
    const staleFormProps = staleForm[Object.keys(staleForm).find((key) => key.startsWith("__reactProps$"))];
    let oldSubmission;
    await act(async () => { oldSubmission = staleFormProps.onSubmit({ preventDefault() {} }); await Promise.resolve(); });
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-new", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { resolveOldQuestion({ run_id: "run-old", run_result_id: "result-old", answer: "오래된 응답", insufficient: false, citations: [] }); await oldSubmission; });
    assert.doesNotMatch(staleContainer.textContent, /오래된 응답|일반 대화 · 근거 미사용/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("Source·지식 Pane은 검증된 Knowledge와 Raw Source 상태·Version·선택을 함께 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-knowledge-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-knowledge" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("source-knowledge") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell, buildQuestionKnowledgeContext } = await import(`${pathToFileURL(path.join(output, entry)).href}?sourceKnowledge=${Date.now()}`);
    const effectAdapter = {
      ...adapter,
      async listSources() {
        return [
          { source_id: "source-ready", source_version_id: "version-ready", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" },
          { source_id: "source-review", source_version_id: "version-review", filename: "review.pdf", source_state: "needs_review", processing_state: "completed", job_state: "completed" },
        ];
      },
      async listKnowledgePackages() {
        return [{
          package_id: "knowledge-package-1", producer: "daon2_5", producer_version: "2.5.7",
          knowledge_registration_id: "knowledge-registration-1", output_version_id: "output-version-7",
          authority: "approved", registration_state: "registered", review_state: "approved",
          digest_sha256: "a".repeat(64), byte_size: 4096,
          content_type: "application/vnd.daon.knowledge+json",
          effective_at: "2026-08-14T00:00:00Z", expires_at: "2027-08-14T00:00:00Z",
        }];
      },
      async listStudioOutputs() { return []; },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /Daon 승인 지식/u);
    assert.match(container.textContent, /Daon 2\.5 · 2\.5\.7/u);
    assert.match(container.textContent, /승인 · 등록됨/u);
    assert.match(container.textContent, /Raw Source/u);
    assert.match(container.textContent, /ready\.pdf/u);
    assert.match(container.textContent, /Version version-ready · 사용 가능/u);
    assert.match(container.textContent, /review\.pdf/u);
    assert.match(container.textContent, /검토 필요/u);

    const knowledgeButton = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("Daon 2.5"))[0];
    assert.ok(knowledgeButton);
    assert.equal(knowledgeButton.getAttribute("aria-pressed"), "false");
    await act(async () => { knowledgeButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(knowledgeButton.getAttribute("aria-pressed"), "true");
    assert.deepEqual(buildQuestionKnowledgeContext(
      { sourceId: "source-ready", sourceVersionId: "version-ready" }, "knowledge-package-1",
    ), {
      mode: "mixed",
      resources: [
        { resourceKind: "knowledge_package", resourceId: "knowledge-package-1" },
        { resourceKind: "source", resourceId: "source-ready", versionId: "version-ready" },
      ],
    });
    assert.match(container.textContent, /질문 컨텍스트 · Daon 승인 지식 \+ Raw Source/u);
    assert.doesNotMatch(container.textContent, /internal\.invalid|digest_sha256|knowledge-registration-1/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("Product Studio 실제 React는 근거 충족 전 생성 비활성, 충족 후 보고서 입력·목록을 렌더한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".stage-b-product-react-"));
  try {
    const { build } = await import("vite");
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    await build({
      configFile: false, logLevel: "silent", root,
      build: {
        outDir: output, emptyOutDir: false,
        lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "product-workspace" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/server"] },
      },
    });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-workspace") && /\.m?js$/u.test(name));
    const {
      ProductWorkspaceShell, projectSafeQuestionAnswer, submitGroundedReport,
      submitGroundedReportForm, openNativeCitation,
    } = await import(`${pathToFileURL(path.join(output, entry)).href}?stageB=${Date.now()}`);
    const disabled = renderToStaticMarkup(createElement(ProductWorkspaceShell, {
      workspaceId: "workspace-1", state: createProductWorkspaceState({ status: "loading" }), adapter,
    }));
    assert.match(disabled, /근거 기반 보고서/);
    assert.doesNotMatch(disabled, /빠른 근거 보고서/);
    assert.match(disabled, /disabled/);
    const ready = {
      ...createProductWorkspaceState({ status: "ready" }),
      selectedSource: { sourceId: "source-1", sourceVersionId: "version-1" },
      answer: { run_id: "run-1", run_result_id: "result-1", answer: "근거", insufficient: false,
        citations: [{ citation_id: "citation-1", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-1", page: 2, origin: "raw_source", context_item_id: "source-1", locator: { kind: "page", value: "2" } }] },
      studioOutputs: [{ studio_output_id: "output-1", title: "기존 보고서", status: "draft", citations: [] }],
    };
    const enabled = renderToStaticMarkup(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state: ready, adapter }));
    assert.match(enabled, /보고서 제목/);
    assert.match(enabled, /결과 목적/);
    assert.match(enabled, /기존 보고서/);
    assert.doesNotMatch(enabled, /Prototype|Fixture|Delivery|Registration/);
    const enrichedAnswer = projectSafeQuestionAnswer({
      ...ready.answer,
      mode: "explicit_source_lookup",
      grounding: "source_backed",
      source_scope_summary: "선택한 Source 범위",
      mismatch: null,
      next_actions: [],
    }, "workspace-1", (citation) => `/bff/api/workspaces/workspace-1/citations/${citation.citation_id}/content#page=${citation.page}`, ready.selectedSource);
    assert.equal(enrichedAnswer.mode, "explicit_source_lookup");
    assert.equal(enrichedAnswer.grounding, "source_backed");
    assert.throws(() => projectSafeQuestionAnswer({
      ...ready.answer,
      mode: "unknown_mode",
      grounding: "source_backed",
      source_scope_summary: null,
      mismatch: null,
      next_actions: [],
    }, "workspace-1", (citation) => `/bff/api/workspaces/workspace-1/citations/${citation.citation_id}/content#page=${citation.page}`, ready.selectedSource), /QUESTION_RESPONSE_INVALID/);
    assert.throws(() => projectSafeQuestionAnswer(
      { ...ready.answer, citations: [{ ...ready.answer.citations[0], source_version_id: "version-other" }] },
      "workspace-1",
      (citation) => `/bff/api/workspaces/workspace-1/citations/${citation.citation_id}/content#page=${citation.page}`,
      ready.selectedSource,
    ), /QUESTION_RESPONSE_INVALID/);

    let resolveCitation;
    let opened = 0;
    const citationController = new AbortController();
    const lateCitation = openNativeCitation({ preventDefault() {} }, {
      adapter: { citationContent: async () => new Promise((resolve) => { resolveCitation = resolve; }) },
      citation: ready.answer.citations[0], signal: citationController.signal,
      openWindow: () => { opened += 1; }, objectUrl: { createObjectURL: () => "blob:late", revokeObjectURL() {} },
      BlobType: Blob, schedule() {}
    });
    citationController.abort();
    resolveCitation({ content_type: "application/pdf", page: 2, bytes: [0x25, 0x50, 0x44, 0x46, 0x2d] });
    assert.equal(await lateCitation, false);
    assert.equal(opened, 0, "Session 종료 뒤 늦은 Citation PDF를 열면 안 된다");

    let createCalls = 0;
    let listCalls = 0;
    const keys = [];
    const stored = [];
    const behaviorAdapter = {
      ...adapter,
      async createReport(_request, options) {
        createCalls += 1;
        keys.push(options.idempotencyKey);
        if (!stored.length) stored.push({ studio_output_id: "output-2", title: "새 보고서", status: "draft", citations: [] });
      },
      async listStudioOutputs() { listCalls += 1; return [...stored]; },
    };
    const blocked = await submitGroundedReport({
      adapter: behaviorAdapter, state: createProductWorkspaceState({ status: "loading" }),
      title: "차단 보고서", purpose: "차단", idempotencyKey: "report-blocked",
    });
    assert.deepEqual(blocked, { submitted: false, outputs: [] });
    assert.equal(createCalls, 0);
    const mismatched = await submitGroundedReport({
      adapter: behaviorAdapter,
      state: {
        ...ready,
        answer: { ...ready.answer, citations: [{ ...ready.answer.citations[0], source_id: "source-other" }] },
      },
      title: "불일치 보고서", purpose: "불일치", idempotencyKey: "report-mismatch-0001",
    });
    assert.deepEqual(mismatched, { submitted: false, outputs: ready.studioOutputs });
    assert.equal(createCalls, 0);
    const first = await submitGroundedReport({
      adapter: behaviorAdapter, state: ready, title: "새 보고서", purpose: "근거 정리",
      idempotencyKey: "report-replay-1",
    });
    const replay = await submitGroundedReport({
      adapter: behaviorAdapter, state: ready, title: "새 보고서", purpose: "근거 정리",
      idempotencyKey: "report-replay-1",
    });
    assert.equal(first.outputs.length, 1);
    assert.equal(replay.outputs.length, 1);
    assert.deepEqual(keys, ["report-replay-1", "report-replay-1"]);
    assert.equal(createCalls, 2);
    assert.equal(listCalls, 2);

    const formKeys = [];
    const formOutputs = [];
    const formAdapter = {
      ...adapter,
      async createReport(request, options) {
        formKeys.push(options.idempotencyKey);
        if (!formOutputs.some((output) => output.idempotency_key === options.idempotencyKey)) {
          formOutputs.push({
            studio_output_id: `form-output-${formOutputs.length + 1}`, title: request.title,
            status: "draft", citations: [], idempotency_key: options.idempotencyKey,
          });
        }
      },
      async listStudioOutputs() { return formOutputs.map(({ idempotency_key: _key, ...output }) => output); },
    };
    const idempotencyRef = { current: null };
    const uuids = [
      "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002",
      "00000000-0000-4000-8000-000000000003", "00000000-0000-4000-8000-000000000004",
      "00000000-0000-4000-8000-000000000005",
    ];
    let prevented = 0;
    const event = { preventDefault() { prevented += 1; } };
    const submitForm = (nextState, title, purpose) => submitGroundedReportForm(event, {
      adapter: formAdapter, state: nextState, title, purpose, idempotencyRef, uuid: () => uuids.shift(),
    });
    await submitForm(ready, "양식 보고서", "목적 1");
    await submitForm(ready, "양식 보고서", "목적 1");
    await submitForm(ready, "양식 보고서 변경", "목적 1");
    await submitForm(ready, "양식 보고서 변경", "목적 2");
    const sourceChanged = {
      ...ready,
      selectedSource: { sourceId: "source-2", sourceVersionId: "version-2" },
      answer: {
        ...ready.answer,
        citations: [{ ...ready.answer.citations[0], source_id: "source-2", source_version_id: "version-2" }],
      },
    };
    await submitForm(sourceChanged, "양식 보고서 변경", "목적 2");
    const runChanged = { ...sourceChanged, answer: { ...sourceChanged.answer, run_id: "run-2", run_result_id: "result-2" } };
    const accumulated = await submitForm(runChanged, "양식 보고서 변경", "목적 2");
    assert.equal(prevented, 6);
    assert.equal(formKeys[0], formKeys[1]);
    assert.equal(new Set(formKeys).size, 5);
    assert.equal(accumulated.outputs.length, 5);
    assert.ok(formKeys.every((key) => /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/u.test(key)));
    const failedOutputs = [];
    await assert.rejects(() => submitGroundedReport({
      adapter: { ...behaviorAdapter, async createReport() { throw new Error("STUDIO_DATABASE_UNAVAILABLE"); }, async listStudioOutputs() { failedOutputs.push("unexpected"); return []; } },
      state: ready, title: "실패 보고서", purpose: "실패", idempotencyKey: "report-failure-1",
    }), /STUDIO_DATABASE_UNAVAILABLE/);
    assert.deepEqual(failedOutputs, []);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("저장된 공통 Studio DTO 1건으로 재진입해도 신규 Pane과 기존 보고서 계보가 함께 렌더된다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".studio-reentry-react-"));
  try {
    const { build } = await import("vite");
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "product-reentry" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/server"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-reentry") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?reentry=${Date.now()}`);
    const stored = {
      studio_output_id: "output-1", output_version_id: "version-1", output_type: "comparison_table",
      title: "저장 비교표", status: "draft", citations: 2,
      version: { output_format: "xlsx", rows: [{ key: "항목", baseline: "A", current: "B", evidence: ["citation-1", "citation-2"] }] },
    };
    const text = renderToStaticMarkup(createElement(ProductWorkspaceShell, {
      workspaceId: "workspace-1", adapter,
      state: { ...createProductWorkspaceState({ status: "ready" }), studioOutputs: [stored] },
    }));
    assert.match(text, /저장 비교표/);
    assert.match(text, /저장된 산출물/);
    assert.match(text, /저장된 보고서/);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});
