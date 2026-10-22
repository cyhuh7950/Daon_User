import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";

import {
  assertProductWorkspaceAdapter,
  createProductWorkspaceState,
  canCreateGroundedReport,
  projectQuestionFailureState,
  questionFailureMessage,
} from "../../packages/ui/src/product-workspace-model.js";
import { classifyConversationIntent, buildSourceScopeMismatch } from "../../packages/ui/src/conversation-intent.js";
import { uploadSource } from "../../apps/web/lib/source-upload-api.js";
import * as sourceRetryApi from "../../apps/web/lib/source-upload-api.js";
import { citationContentUrl } from "../../apps/web/lib/question-answering-api.js";
import { unbindWorkspaceSource } from "../../apps/web/lib/product-workspace-api.js";

test('SIX-A Provider popup requires discard confirmation for X and Escape and blocks close during save',async()=>{
  const root=path.resolve(import.meta.dirname,'../..'),output=await mkdtemp(path.join(tmpdir(),'.six-a-shell-'));
  const dom=installMinimalDom();let reactRoot;
  try{
    const {createElement,act}=await import('react'),{createRoot}=await import('react-dom/client'),{build}=await import('vite');
    await build({configFile:false,logLevel:'silent',root,build:{outDir:output,emptyOutDir:false,lib:{entry:path.join(root,'packages/ui/src/product-workspace-shell.jsx'),formats:['es'],fileName:'six-a-shell'},rollupOptions:{external:['react','react-dom','react-dom/client'],output:{paths:Object.fromEntries(['react','react-dom','react-dom/client'].map(n=>[n,import.meta.resolve(n)]))}}}});
    const entry=(await readdir(output)).find(n=>/\.m?js$/u.test(n));
    const {ProductWorkspaceShell}=await import(pathToFileURL(path.join(output,entry)).href);
    const container=dom.document.createElement('div');dom.document.body.appendChild(container);reactRoot=createRoot(container);
    const current=s17Adapter();
    const render=(dirty,saving)=>act(async()=>reactRoot.render(createElement(ProductWorkspaceShell,{workspaceId:'workspace-1',adapter:current,providerSettings:createElement('div',{key:'provider-settings'},'QA Provider settings'),providerSettingsDirty:dirty,providerSettingsSaving:saving})));
    const button=t=>findElements(container,n=>n.tagName==='BUTTON'&&n.textContent.trim()===t)[0];
    const click=n=>act(async()=>{assert.ok(n);n.dispatchEvent(new MinimalEvent('click'));});
    const dialog=()=>findElements(container,n=>n.getAttribute?.('role')==='dialog')[0];
    const props=n=>n[Object.keys(n).find(k=>k.startsWith('__reactProps'))];
    await render(true,false);await click(button('설정'));await click(findElements(container,n=>n.getAttribute?.('role')==='menuitem'&&n.textContent.includes('LLM'))[0]);
    await click(findElements(container,n=>n.getAttribute?.('aria-label')==='닫기')[0]);
    assert.ok(button('계속 편집'));assert.match(container.textContent,/QA Provider settings/);
    const disclosure=findElements(container,n=>n.getAttribute?.('aria-label')==='저장하지 않은 Provider 변경')[0];
    assert.equal(disclosure.getAttribute('role'),'group');assert.equal(disclosure.getAttribute('aria-modal'),null);
    await click(button('계속 편집'));
    await act(async()=>props(dialog()).onKeyDown({key:'Escape',preventDefault(){},stopPropagation(){}}));
    assert.ok(button('변경 버리고 닫기'));await click(button('변경 버리고 닫기'));assert.equal(dialog(),undefined);
    await render(true,true);await click(button('설정'));await click(findElements(container,n=>n.getAttribute?.('role')==='menuitem'&&n.textContent.includes('LLM'))[0]);
    await act(async()=>props(dialog()).onKeyDown({key:'Escape',preventDefault(){},stopPropagation(){}}));
    assert.ok(dialog());assert.equal(findElements(container,n=>n.getAttribute?.('aria-label')==='닫기')[0].disabled,true);
    await render(false,false);await click(findElements(container,n=>n.getAttribute?.('aria-label')==='닫기')[0]);assert.equal(dialog(),undefined);
  }finally{if(reactRoot){const {act}=await import('react');await act(async()=>reactRoot.unmount());}dom.restore();await rm(output,{recursive:true,force:true});}
});

test("질문 정책 차단 안내는 입력 수정 대신 조직·Workspace 정책 원인을 표시한다", () => {
  assert.equal(questionFailureMessage("EGRESS_POLICY_DENIED"),
    "조직 또는 Workspace의 외부 전송 정책으로 차단되었습니다. 정책 설정을 확인해 주세요. (EGRESS_POLICY_DENIED)");
  for (const code of ["EGRESS_POLICY_UNAVAILABLE", "EGRESS_POLICY_STALE"]) {
    assert.equal(questionFailureMessage(code),
      `조직 또는 Workspace의 외부 전송 정책을 확인할 수 없습니다. 관리자에게 정책 상태 확인을 요청해 주세요. (${code})`);
    assert.doesNotMatch(questionFailureMessage(code), /입력|제출/u);
  }
  assert.equal(questionFailureMessage("QUESTION_FAILED"),
    "질문 실행에 실패했습니다. (QUESTION_FAILED) 입력을 확인한 뒤 다시 제출해 주세요.");
});

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
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } },
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
      assert.equal(calls.length, 0, "설정 확인 전에는 재처리하지 않는다");
      const confirm = findElements(workspace.container, node => node.tagName === "BUTTON" && node.textContent === "설정 확인·재처리")[0];
      assert.ok(confirm);
      await workspace.act(async () => confirm.dispatchEvent(new MinimalEvent("click")));
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
        assert.equal(calls.length,0,'확인 전 POST 금지');
        const confirm=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='설정 확인·재처리')[0];
        assert.ok(confirm);await workspace.act(async()=>confirm.dispatchEvent(new MinimalEvent('click')));
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
      assert.equal(keys.length, index, "확인 전에는 새 요청을 보내지 않는다");
      const confirm = findElements(workspace.container, node => node.tagName === "BUTTON" && node.textContent === "설정 확인·재처리")[0];
      assert.ok(confirm); await workspace.act(async () => confirm.dispatchEvent(new MinimalEvent("click")));
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

test("승인 정책 설정 메뉴는 전용 modal 안에서 조회하고 닫기·Workspace 전환의 늦은 인증을 폐기한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(tmpdir(), "daon-user-approval-policy-shell-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"), { createElement, act } = await import("react"), { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "approval-policy-shell" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } } } });
    const entry = (await readdir(output)).find(name => /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(pathToFileURL(path.join(output, entry)).href);
    let reads=0, saves=0; const challenges=[];
    const adapterFor = days => ({ ...adapter, async listSources(){return [];}, async listStudioOutputs(){return [];},
      async listStudioApprovalNotices(){return {items:[],unread_count:0};},
      async getStudioApprovalPolicy(){reads++;return {ttl_days:days,policy_version:2,can_manage:true,step_up_target_id:'server-policy-target'};},
      async issueStudioApprovalPolicyStepUp(target,password,options){assert.equal(target,'server-policy-target');assert.equal(password,'fixture password only');const waiting=deferred();challenges.push({...waiting,signal:options.signal});return waiting.promise;},
      async saveStudioApprovalPolicy(){saves++;throw new Error('UNEXPECTED_SAVE');},
    });
    const currentAdapter=adapterFor(7), nextAdapter=adapterFor(30);
    const container=dom.document.createElement('div');dom.document.body.appendChild(container);reactRoot=createRoot(container);
    const render=(workspaceId,sourceAdapter)=>act(async()=>{reactRoot.render(createElement(ProductWorkspaceShell,{workspaceId,adapter:sourceAdapter}));await Promise.resolve();await Promise.resolve();});
    const button=text=>findElements(container,node=>node.tagName==='BUTTON'&&node.textContent.trim()===text)[0];
    const click=async element=>{assert.ok(element,'expected control');await act(async()=>element.dispatchEvent(new MinimalEvent('click')));};
    const field=text=>{const label=findElements(container,node=>node.tagName==='LABEL'&&node.textContent.includes(text))[0];assert.ok(label,text);return findElements(label,node=>node.tagName==='INPUT')[0];};
    const props=node=>node[Object.keys(node).find(key=>key.startsWith('__reactProps'))];
    const open=async()=>{await click(button('설정'));await click(findElements(container,node=>node.tagName==='BUTTON'&&node.getAttribute('role')==='menuitem'&&node.textContent.includes('승인 정책'))[0]);};
    const authorize=async()=>{await click(button('승인 기한 변경'));await act(async()=>props(field('추가 인증 비밀번호')).onChange({currentTarget:{value:'fixture password only'}}));await click(button('추가 인증 확인'));};
    await render('workspace-1',currentAdapter);assert.equal(reads,0);
    assert.ok(!button('승인 기한 변경'));await open();assert.equal(reads,1);assert.match(container.textContent,/조직 승인 기한 7일/);
    await authorize();
    const dialogs=findElements(container,node=>node.getAttribute?.('role')==='dialog');assert.equal(dialogs.length,1);
    assert.ok(findElements(dialogs[0],node=>node===field('추가 인증 비밀번호')).length===1,'policy authentication stays inside settings dialog');
    assert.equal(field('추가 인증 비밀번호').value,'');
    await click(findElements(container,node=>node.tagName==='BUTTON'&&node.getAttribute('aria-label')==='닫기')[0]);
    assert.equal(challenges[0].signal.aborted,true);
    await act(async()=>challenges[0].resolve('late-closed-grant'));assert.equal(saves,0);
    await open();await authorize();await render('workspace-2',nextAdapter);
    assert.equal(challenges[1].signal.aborted,true);
    await act(async()=>challenges[1].resolve('late-old-workspace-grant'));
    assert.equal(saves,0);assert.ok(!button('추가 인증 확인'));
    await open();assert.match(container.textContent,/조직 승인 기한 30일/);
    assert.doesNotMatch(container.textContent,/late-closed-grant|late-old-workspace-grant/);
  } finally {
    if(reactRoot){const {act}=await import('react');await act(async()=>reactRoot.unmount());}
    dom.restore();assert.equal(path.dirname(output),path.resolve(tmpdir()));await rm(output,{recursive:true,force:true});
  }
});

const s17Connector = Object.freeze({ connector_id: "daon-approved", kind: "daon_approved_knowledge",
  name: "Daon 승인 지식", status: "unavailable", source_count: 0, endpoint_label: "Daon",
  last_checked_at: null, error_code: "CONNECTOR_UNAVAILABLE" });
function s17Adapter(overrides = {}) {
  return { ...adapter, notebookContext: { notebook_id: "notebook-s17", sources: [] },
    listSources: async () => [], listKnowledgePackages: async () => [], listStudioOutputs: async () => [],
    listConnectors: async () => [{ ...s17Connector }], ...overrides };
}
async function mountS17ReconnectWorkspace(initialAdapter, options = {}) {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(options.outputRoot ?? root, ".workspace-s17-reconnect-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "s17-reconnect" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } },
    } });
    const entry = (await readdir(output)).find(name => name.startsWith("s17-reconnect") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(pathToFileURL(path.join(output, entry)).href);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    const render = async nextAdapter => act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: nextAdapter }));
      await Promise.resolve(); await Promise.resolve();
    });
    await render(initialAdapter);
    return { container, act, render,
      button: () => findElements(container, node => node.tagName === "BUTTON"
        && node.getAttribute("aria-label") === "Daon 승인 지식 재연결")[0],
      cleanup: async () => { await act(async () => reactRoot.unmount()); dom.restore(); await rm(output, { recursive: true, force: true }); },
    };
  } catch (error) {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true }); throw error;
  }
}

test("S17 재연결 동기 중복 클릭은 한 요청만 보내고 성공 후 현재 목록을 갱신한다", async () => {
  const pending = deferred(); let calls = 0, lists = 0, connected = false;
  const workspace = await mountS17ReconnectWorkspace(s17Adapter({
    listConnectors: async () => { lists += 1; return [{ ...s17Connector, status: connected ? "connected" : "unavailable" }]; },
    reconnectConnector: async id => { assert.equal(id, "daon-approved"); calls += 1; await pending.promise; connected = true; },
  }));
  try {
    await workspace.act(async () => {
      workspace.button().dispatchEvent(new MinimalEvent("click"));
      workspace.button().dispatchEvent(new MinimalEvent("click"));
    });
    assert.equal(calls, 1, "React 재렌더 전에도 중복 요청이 없어야 한다");
    assert.equal(workspace.button().disabled, true);
    assert.equal(lists, 1);
    await workspace.act(async () => { pending.resolve(); await Promise.resolve(); });
    assert.equal(lists, 2);
    assert.equal(workspace.button(), undefined);
    assert.doesNotMatch(workspace.container.textContent, /연결형 Source를 불러오지 못했습니다/u);
  } finally {
    await workspace.act(async () => { pending.resolve(); await new Promise(resolve => setImmediate(resolve)); });
    await workspace.cleanup();
  }
});

test("S17 재연결 실패는 안전 경고로 처리하고 pending을 풀어 명시 재시도를 허용한다", async () => {
  const pending = deferred(); let calls = 0, lists = 0;
  const workspace = await mountS17ReconnectWorkspace(s17Adapter({
    listConnectors: async () => { lists += 1; return [{ ...s17Connector }]; },
    reconnectConnector: async () => { calls += 1; if (calls === 1) await pending.promise; },
  }));
  try {
    await workspace.act(async () => workspace.button().dispatchEvent(new MinimalEvent("click")));
    let escapedError;
    try {
      await workspace.act(async () => {
        pending.reject(new Error("PRIVATE_ERROR http://internal.invalid secret-content"));
        await new Promise(resolve => setImmediate(resolve));
      });
    } catch (error) { escapedError = error; }
    assert.equal(escapedError, undefined, "재연결 rejection은 event handler 밖으로 유출하지 않는다");
    assert.match(workspace.container.textContent, /연결형 Source를 불러오지 못했습니다/u);
    assert.doesNotMatch(workspace.container.textContent, /PRIVATE_ERROR|internal\.invalid|secret-content/u);
    assert.equal(workspace.button().disabled, false);
    assert.equal(lists, 1, "실패 시 목록 갱신이나 자동 재전송을 하지 않는다");
    await workspace.act(async () => workspace.button().dispatchEvent(new MinimalEvent("click")));
    assert.equal(calls, 2); assert.equal(lists, 2);
    assert.doesNotMatch(workspace.container.textContent, /연결형 Source를 불러오지 못했습니다/u);
  } finally { await workspace.cleanup(); }
});

for (const outcome of ["resolve", "reject"]) test(`S17 재연결 Notebook 전환 후 늦은 ${outcome}는 새 요청과 화면을 변경하지 않는다`, async () => {
  const oldRequest = deferred(), newRequest = deferred(); let oldSignal, newSignal, newLists = 0;
  const workspace = await mountS17ReconnectWorkspace(s17Adapter({ reconnectConnector: async (_id, options) => {
    oldSignal = options?.signal; await oldRequest.promise;
  } }));
  try {
    await workspace.act(async () => workspace.button().dispatchEvent(new MinimalEvent("click")));
    await workspace.render(s17Adapter({ notebookContext: { notebook_id: "notebook-new", sources: [] },
      listConnectors: async () => { newLists += 1; return [{ ...s17Connector }]; },
      reconnectConnector: async (_id, options) => { newSignal = options?.signal; await newRequest.promise; },
    }));
    await workspace.act(async () => workspace.button().dispatchEvent(new MinimalEvent("click")));
    let escapedError;
    try {
      await workspace.act(async () => {
        if (outcome === "resolve") oldRequest.resolve(); else oldRequest.reject(new Error("PRIVATE_OLD_ERROR"));
        await new Promise(resolve => setImmediate(resolve));
      });
    } catch (error) { escapedError = error; }
    assert.equal(escapedError, undefined, "이전 Notebook의 rejection도 밖으로 유출하지 않는다");
    assert.equal(oldSignal?.aborted, true, "이전 Notebook 재연결 요청은 취소돼야 한다");
    assert.equal(newSignal?.aborted, false);
    assert.equal(workspace.button().disabled, true, "이전 finally는 새 요청의 pending을 풀지 않는다");
    assert.equal(newLists, 1, "이전 응답으로 새 Notebook 목록을 갱신하지 않는다");
    assert.doesNotMatch(workspace.container.textContent, /연결형 Source를 불러오지 못했습니다|PRIVATE_OLD_ERROR/u);
    await workspace.act(async () => { newRequest.resolve(); await Promise.resolve(); });
    assert.equal(workspace.button().disabled, false); assert.equal(newLists, 2);
  } finally {
    await workspace.act(async () => { oldRequest.resolve(); newRequest.resolve(); await new Promise(resolve => setImmediate(resolve)); });
    await workspace.cleanup();
  }
});

const s18Answer = (answer = "새 답변") => ({ run_id: "run-s18", run_result_id: "result-s18", answer, insufficient: false, citations: [] });
const s18Sources = [
  { source_id: "source-1", source_version_id: "version-1", filename: "first.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" },
  { source_id: "source-2", source_version_id: "version-2", filename: "other.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" },
];
function dModelView(version=1,effort=null) {
  const selection={connection_id:"connection-d",model_id:"solar-pro4",reasoning_effort:effort};
  return {notebook_id:"notebook-s18",version,selection:version?selection:null,effective_selection:version?{...selection,origin:"notebook"}:null,
    available_models:[{connection_id:"connection-d",provider_code:"UPSTAGE",display_name:"승인 연결",model_id:"solar-pro4",connection_version:1,catalog_version:1,
      reasoning:{status:"supported",values:[{value:"none",label:"사용 안 함"},{value:"low",label:"낮음"},{value:"medium",label:"보통"},{value:"high",label:"높음"}],profile_version:"upstage-solar-pro4-safe-wire-v1"}}]};
}
function s18Adapter(overrides = {}) {
  return { ...adapter, notebookContext: { notebook_id: "notebook-s18", sources: [] },
    getModelSettings: async()=>dModelView(),
    listSources: async () => s18Sources, listConnectors: async () => [], listStudioOutputs: async () => [],
    listKnowledgePackages: async () => [{ package_id: "package-s18", producer: "daon3", producer_version: "3.0", authority: "approved", registration_state: "registered" }],
    ...overrides };
}

test("HC2 미지원 추론은 숨기고 미확인은 비활성 상태로 미지정 질문을 실행한다",async()=>{
  for(const status of ["unsupported","unknown"]){
    const calls=[];const view=dModelView();view.available_models[0].reasoning={status,values:[],profile_version:null};
    const workspace=await mountS17ReconnectWorkspace(s18Adapter({getModelSettings:async()=>view,askQuestion:async(input)=>{calls.push(input);return s18Answer();}}));
    try{
      const controls=findElements(workspace.container,node=>node.getAttribute?.("aria-label")==="추론 단계");
      assert.equal(controls.length,status==="unsupported"?0:1);if(controls[0])assert.equal(controls[0].disabled,true);
      await s18Type(workspace,"안녕하세요");await workspace.act(async()=>s18Submit(workspace));
      assert.equal(calls.length,1);assert.equal(calls[0].modelSettingsVersion,1);
    }finally{await workspace.cleanup();}
  }
});

test("HC2 Workspace 기본값과 명시 선택의 추론 변경은 허용 선택 필드만 저장하고 재조회한다",async()=>{
  for(const version of [0,1]){
    let current=dModelView(version);const saves=[];
    if(version===0)current.effective_selection={connection_id:"connection-d",model_id:"solar-pro4",reasoning_effort:null,origin:"workspace_default"};
    const workspace=await mountS17ReconnectWorkspace(s18Adapter({getModelSettings:async()=>current,
      saveModelSettings:async(input)=>{
        saves.push(structuredClone(input));
        assert.deepEqual(Object.keys(input).sort(),["connection_id","expected_version","model_id","reasoning_effort"]);
        current=dModelView(input.expected_version+1,input.reasoning_effort);
        return {notebook_id:current.notebook_id,version:current.version,selection:current.selection,replayed:false};
      }}));
    try{
      assert.equal(saves.length,0);
      const reasoning=()=>findElements(workspace.container,node=>node.getAttribute?.("aria-label")==="추론 단계")[0];
      await workspace.act(async()=>{await s18Props(reasoning()).onChange({target:{value:"medium"}});});
      assert.deepEqual(saves[0],{connection_id:"connection-d",model_id:"solar-pro4",reasoning_effort:"medium",expected_version:version});
      assert.match(workspace.container.textContent,/모델 설정 저장됨/u);
      assert.equal(s18Props(reasoning()).value,"medium");
      await workspace.act(async()=>{await s18Props(reasoning()).onChange({target:{value:""}});});
      assert.deepEqual(saves[1],{connection_id:"connection-d",model_id:"solar-pro4",reasoning_effort:null,expected_version:version+1});
      assert.equal(s18Props(reasoning()).value,"");
    }finally{await workspace.cleanup();}
  }
});
const s18Composer = workspace => findElements(workspace.container, node => node.tagName === "FORM" && node.getAttribute("class") === "conversation-composer")[0];
const s18Props = node => node[Object.keys(node).find(key => key.startsWith("__reactProps$"))];
const s18Submit = workspace => s18Props(s18Composer(workspace)).onSubmit({ preventDefault() {} });
async function s18Type(workspace, text = "선택 자료를 설명해 주세요") {
  const textarea = findElements(workspace.container, node => node.tagName === "TEXTAREA")[0];
  textarea.value = text;
  await workspace.act(async () => s18Props(textarea).onChange({ currentTarget: textarea, target: textarea }));
  return textarea;
}

test("S18 질문 즉시 중복 제출은 한 요청이고 대기 중 이전 답변을 숨기며 입력을 잠근다", async () => {
  const pending = deferred(), calls = []; let first, second;
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({ loadNotebookConversation: async () => s18Answer("이전 답변"),
    askQuestion: async (input, options) => { calls.push({ input, options }); return pending.promise; },
  }));
  try {
    assert.match(workspace.container.textContent, /이전 답변/u);
    const textarea = await s18Type(workspace);
    await workspace.act(async () => { first = s18Submit(workspace); second = s18Submit(workspace); });
    assert.equal(calls.length, 1);
    assert.equal(textarea.disabled, true);
    assert.equal(findElements(workspace.container, node => node.getAttribute?.("aria-label") === "질문 실행")[0].disabled, true);
    assert.ok(findElements(workspace.container, node => node.getAttribute?.("role") === "status" && /답변.*기다/u.test(node.textContent)).length);
    assert.doesNotMatch(workspace.container.textContent, /이전 답변/u);
    await workspace.act(async () => { pending.resolve(s18Answer()); await Promise.all([first, second]); });
    assert.match(workspace.container.textContent, /새 답변/u); assert.equal(textarea.disabled, false);
    assert.doesNotMatch(workspace.container.textContent, /답변을 기다/u);
  } finally {
    await workspace.act(async () => { pending.resolve(s18Answer()); await Promise.all([first, second]); });
    await workspace.cleanup();
  }
});

test("S18 질문 실패는 안전 경고와 입력을 유지하고 명시 재제출만 새 key로 실행한다", async () => {
  const pending = deferred(), calls = []; let submission;
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({ askQuestion: async (input, options) => {
    calls.push({ input, options }); return calls.length === 1 ? pending.promise : s18Answer();
  } }));
  try {
    const textarea = await s18Type(workspace, "재제출 질문");
    await workspace.act(async () => { submission = s18Submit(workspace); });
    await workspace.act(async () => { pending.reject(new Error("PRIVATE_ERROR http://internal.invalid credential")); await submission; });
    assert.match(workspace.container.textContent, /질문.*실패/u);
    assert.doesNotMatch(workspace.container.textContent, /PRIVATE_ERROR|internal\.invalid|credential/u);
    assert.equal(textarea.value, "재제출 질문"); assert.equal(textarea.disabled, false); assert.equal(calls.length, 1);
    await workspace.act(async () => s18Submit(workspace));
    assert.equal(calls.length, 2); assert.notEqual(calls[0].options.idempotencyKey, calls[1].options.idempotencyKey);
    assert.match(workspace.container.textContent, /새 답변/u);
  } finally { pending.resolve(s18Answer()); if (submission) await submission; await workspace.cleanup(); }
});

test("S18 질문 대기 중 늦은 저장 답변 조회는 현재 질문을 이전 답변으로 덮지 않는다", async () => {
  const history = deferred(), pending = deferred(); let submission;
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({ loadNotebookConversation: async () => history.promise,
    askQuestion: async () => pending.promise,
  }));
  try {
    await s18Type(workspace); await workspace.act(async () => { submission = s18Submit(workspace); });
    await workspace.act(async () => { history.resolve(s18Answer("늦게 조회된 이전 답변")); await Promise.resolve(); });
    assert.doesNotMatch(workspace.container.textContent, /늦게 조회된 이전 답변/u);
    assert.ok(findElements(workspace.container, node => node.getAttribute?.("role") === "status" && /답변.*기다/u.test(node.textContent)).length);
    await workspace.act(async () => { pending.resolve(s18Answer()); await submission; });
    assert.match(workspace.container.textContent, /새 답변/u);
  } finally {
    await workspace.act(async () => { history.resolve(s18Answer()); pending.resolve(s18Answer()); if (submission) await submission; });
    await workspace.cleanup();
  }
});

test("S18 질문 부족 답변은 서버 원문과 기존 다음 행동을 그대로 표시한다", async () => {
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({ askQuestion: async () => ({
    ...s18Answer("서버가 반환한 부족 안내 <원문>"), insufficient: true,
    mode: "explicit_source_lookup", grounding: "source_evidence_unavailable", source_scope_summary: "선택한 자료 범위",
    mismatch: { code: "SOURCE_SCOPE_MISMATCH", detail: "질문에 필요한 근거가 없습니다." }, next_actions: ["다른 Source 선택", "Source 추가"],
  }) }));
  try {
    await s18Type(workspace); await workspace.act(async () => s18Submit(workspace));
    assert.match(workspace.container.textContent, /서버가 반환한 부족 안내 <원문>/u);
    assert.match(workspace.container.textContent, /근거 부족/u);
    assert.match(workspace.container.textContent, /다른 Source 선택/u);
    assert.equal(findElements(workspace.container, node => node.tagName === "A" && node.parentNode?.getAttribute?.("class") === "citation-row").length, 0);
  } finally { await workspace.cleanup(); }
});

for (const scope of ["Source", "Knowledge", "Notebook"]) for (const outcome of ["resolve", "reject"]) {
  test(`S18 질문 ${scope} 전환은 이전 요청을 취소하고 늦은 ${outcome}와 finally가 새 대기를 변경하지 않는다`, async () => {
    const oldRequest = deferred(), newRequest = deferred(), calls = []; let oldSubmission, newSubmission;
    const askQuestion = async (input, options) => { calls.push({ input, options }); return calls.length === 1 ? oldRequest.promise : newRequest.promise; };
    const workspace = await mountS17ReconnectWorkspace(s18Adapter({ askQuestion }));
    try {
      await s18Type(workspace); await workspace.act(async () => { oldSubmission = s18Submit(workspace); });
      if (scope === "Notebook") {
        await workspace.render(s18Adapter({ notebookContext: { notebook_id: "notebook-new", sources: [] }, askQuestion }));
        await s18Type(workspace);
      } else {
        const button = findElements(workspace.container, node => node.tagName === "BUTTON" && node.textContent.includes(scope === "Source" ? "other.pdf" : "Daon 3 · 3.0"))[0];
        assert.ok(button); await workspace.act(async () => button.dispatchEvent(new MinimalEvent("click")));
      }
      await workspace.act(async () => { newSubmission = s18Submit(workspace); });
      assert.equal(calls.length, 2); assert.equal(calls[0].options.signal.aborted, true);
      assert.equal(calls[1].options.signal.aborted, false);
      await workspace.act(async () => {
        if (outcome === "resolve") oldRequest.resolve(s18Answer("OLD_ANSWER")); else oldRequest.reject(new Error("PRIVATE_OLD_ERROR"));
        await oldSubmission;
      });
      assert.equal(findElements(workspace.container, node => node.tagName === "TEXTAREA")[0].disabled, true);
      assert.doesNotMatch(workspace.container.textContent, /OLD_ANSWER|PRIVATE_OLD_ERROR|질문.*실패/u);
      await workspace.act(async () => { newRequest.resolve(s18Answer("NEW_ANSWER")); await newSubmission; });
      assert.match(workspace.container.textContent, /NEW_ANSWER/u);
      assert.equal(findElements(workspace.container, node => node.tagName === "TEXTAREA")[0].disabled, false);
    } finally {
      await workspace.act(async () => { oldRequest.resolve(s18Answer()); newRequest.resolve(s18Answer()); await Promise.all([oldSubmission, newSubmission]); });
      await workspace.cleanup();
    }
  });
}

test("S18 질문 unmount는 요청을 취소하고 늦은 실패를 처리한다", async () => {
  const pending = deferred(); let signal, submission;
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({ askQuestion: async (_input, options) => { signal = options.signal; return pending.promise; } }));
  try { await s18Type(workspace); await workspace.act(async () => { submission = s18Submit(workspace); }); }
  finally { await workspace.cleanup(); }
  pending.reject(new Error("PRIVATE_UNMOUNT_ERROR")); await submission;
  assert.equal(signal.aborted, true);
});

const s18AuthPassword = workspace => findElements(workspace.container, node => node.tagName === "INPUT" && node.getAttribute("aria-label") === "외부 질문 추가 인증 비밀번호")[0];
const s18AuthConfirm = workspace => findElements(workspace.container, node => node.tagName === "BUTTON" && node.textContent === "인증 후 질문 제출")[0];
const s18AuthCancel = workspace => findElements(workspace.container, node => node.tagName === "BUTTON" && node.textContent === "질문 인증 취소")[0];

test("S18 질문 STEP_UP_REQUIRED에서만 재인증하고 같은 입력/key로 한 번 제출하며 비밀번호를 즉시 비운다", async () => {
  const calls = [], grants = [], pending = deferred(); let submission, confirmation;
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({
    askQuestion: async (input, options) => { calls.push({ input, options }); if (calls.length === 1) throw new Error("STEP_UP_REQUIRED"); return s18Answer("인증된 답변"); },
    authorizeQuestion: async (input, options) => { grants.push({ input, options }); return pending.promise; },
  }));
  try {
    await s18Type(workspace);
    await workspace.act(async () => { submission = s18Submit(workspace); await new Promise(resolve => setImmediate(resolve)); });
    assert.equal(calls.length, 1); assert.equal(grants.length, 0);
    const password = s18AuthPassword(workspace);
    assert.ok(password, "추가 인증을 위한 실제 password 입력을 표시한다");
    password.value = "synthetic-password";
    await workspace.act(async () => {
      const onClick = s18Props(s18AuthConfirm(workspace)).onClick;
      confirmation = onClick(); onClick();
      await Promise.resolve();
    });
    assert.equal(grants.length, 1); assert.equal(password.value, "");
    assert.deepEqual(grants[0].input, { ...calls[0].input, password: "synthetic-password" });
    assert.equal(grants[0].options.idempotencyKey, calls[0].options.idempotencyKey);
    assert.equal(grants[0].options.signal, calls[0].options.signal);
    await workspace.act(async () => { pending.resolve({ step_up_authorization_id: "grant-s18" }); await confirmation; await submission; });
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[1].input, calls[0].input);
    assert.equal(calls[1].options.idempotencyKey, calls[0].options.idempotencyKey);
    assert.equal(calls[1].options.stepUpAuthorizationId, "grant-s18");
    assert.match(workspace.container.textContent, /인증된 답변/u);
    assert.equal(s18AuthPassword(workspace), undefined);
  } finally {
    pending.resolve({ step_up_authorization_id: "grant-s18" });
    await workspace.cleanup(); await submission; await confirmation;
  }
});

test("S18 질문 재인증 실패는 안전 안내와 질문을 유지하고 취소는 추가 제출 없이 닫는다", async () => {
  let calls = 0, submission;
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({
    askQuestion: async () => { calls++; throw new Error("STEP_UP_REQUIRED"); },
    authorizeQuestion: async () => { throw new Error("PRIVATE_AUTH_CREDENTIAL_FAILURE"); },
  }));
  try {
    await s18Type(workspace, "보존할 질문");
    await workspace.act(async () => { submission = s18Submit(workspace); await new Promise(resolve => setImmediate(resolve)); });
    assert.ok(s18AuthPassword(workspace));
    s18AuthPassword(workspace).value = "synthetic-password";
    await workspace.act(async () => { await s18Props(s18AuthConfirm(workspace)).onClick(); });
    assert.equal(s18AuthPassword(workspace).value, "");
    assert.doesNotMatch(workspace.container.textContent, /PRIVATE_AUTH_CREDENTIAL_FAILURE|synthetic-password/u);
    assert.ok(findElements(workspace.container, node => node.getAttribute("role") === "alert").length);
    await workspace.act(async () => { s18Props(s18AuthCancel(workspace)).onClick(); await submission; });
    assert.equal(calls, 1); assert.equal(s18AuthPassword(workspace), undefined);
    assert.equal(findElements(workspace.container, node => node.tagName === "TEXTAREA")[0].value, "보존할 질문");
  } finally { await workspace.cleanup(); await submission; }
});

for (const scope of ["Notebook", "Source"]) for (const outcome of ["resolve", "reject"]) {
  test(`S18 질문 ${scope} 전환은 재인증을 취소하고 늦은 ${outcome}의 제출을 차단한다`, async () => {
    const pending = deferred(); let calls = 0, submission, confirmation, signal;
    const workspace = await mountS17ReconnectWorkspace(s18Adapter({
      askQuestion: async () => { calls++; throw new Error("STEP_UP_REQUIRED"); },
      authorizeQuestion: async (_input, options) => { signal = options.signal; return pending.promise; },
    }));
    try {
      await s18Type(workspace);
      await workspace.act(async () => { submission = s18Submit(workspace); await new Promise(resolve => setImmediate(resolve)); });
      assert.ok(s18AuthPassword(workspace)); s18AuthPassword(workspace).value = "synthetic-password";
      await workspace.act(async () => { confirmation = s18Props(s18AuthConfirm(workspace)).onClick(); await Promise.resolve(); });
      if (scope === "Notebook") await workspace.render(s18Adapter({ notebookContext: { notebook_id: "notebook-other", sources: [] } }));
      else await workspace.act(async () => {
        findElements(workspace.container, node => node.tagName === "BUTTON" && node.textContent.includes("other.pdf"))[0].dispatchEvent(new MinimalEvent("click"));
      });
      assert.equal(signal.aborted, true); assert.equal(s18AuthPassword(workspace), undefined);
      await workspace.act(async () => {
        if (outcome === "resolve") pending.resolve({ step_up_authorization_id: "grant-stale" });
        else pending.reject(new Error("PRIVATE_STALE_AUTH"));
        await confirmation; await submission;
      });
      assert.equal(calls, 1); assert.doesNotMatch(workspace.container.textContent, /PRIVATE_STALE_AUTH/u);
    } finally { pending.resolve({ step_up_authorization_id: "grant-stale" }); await workspace.cleanup(); await submission; await confirmation; }
  });
}

test("S18 질문 재인증 중 unmount는 비밀번호를 비우고 늦은 인증 실패를 처리한다", async () => {
  const pending = deferred(); let submission, confirmation, signal, password;
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({
    askQuestion: async () => { throw new Error("STEP_UP_REQUIRED"); },
    authorizeQuestion: async (_input, options) => { signal = options.signal; return pending.promise; },
  }));
  try {
    await s18Type(workspace);
    await workspace.act(async () => { submission = s18Submit(workspace); await new Promise(resolve => setImmediate(resolve)); });
    password = s18AuthPassword(workspace); assert.ok(password); password.value = "synthetic-password";
    await workspace.act(async () => { confirmation = s18Props(s18AuthConfirm(workspace)).onClick(); await Promise.resolve(); });
  } finally { await workspace.cleanup(); }
  pending.reject(new Error("PRIVATE_AUTH_AFTER_UNMOUNT")); await confirmation; await submission;
  assert.equal(signal.aborted, true); assert.equal(password.value, "");
});

test("S18 질문 정책/권한의 다른 안전 오류는 재인증을 자동 요청하지 않는다", async () => {
  let authCalls = 0;
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({
    askQuestion: async () => { throw new Error("EGRESS_POLICY_DENIED"); },
    authorizeQuestion: async () => { authCalls++; },
  }));
  try {
    await s18Type(workspace); await workspace.act(async () => { await s18Submit(workspace); });
    assert.equal(authCalls, 0); assert.equal(s18AuthPassword(workspace), undefined);
    assert.ok(findElements(workspace.container, node => node.getAttribute("role") === "alert").length);
  } finally { await workspace.cleanup(); }
});

test("작업 상담과 명시 Source 확인은 서로 다른 conversation mode로 분류한다", () => {
  assert.equal(classifyConversationIntent("다음 작업을 어떻게 진행할까?"), "work_support");
  assert.equal(classifyConversationIntent("선택한 문서에서 보존 기간을 찾아줘"), "explicit_source_lookup");
  assert.equal(classifyConversationIntent("이 자료로 보고서를 만들어줘"), "source_backed_action");
  assert.equal(classifyConversationIntent("최신 정책을 웹에서 찾아줘"), "approved_web_research");
});

test("S18 질문 내부 모델의 정상 답변은 추가 인증을 요구하지 않는다", async () => {
  let authCalls = 0;
  const workspace = await mountS17ReconnectWorkspace(s18Adapter({
    askQuestion: async () => s18Answer("내부 모델 답변"),
    authorizeQuestion: async () => { authCalls++; throw new Error("UNEXPECTED_AUTHORIZATION"); },
  }));
  try {
    await s18Type(workspace); await workspace.act(async () => { await s18Submit(workspace); });
    assert.equal(authCalls, 0); assert.equal(s18AuthPassword(workspace), undefined);
    assert.match(workspace.container.textContent, /내부 모델 답변/u);
  } finally { await workspace.cleanup(); }
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
  const output = await mkdtemp(path.join(tmpdir(), `daon-workspace-source-retry-${suffix}-`));
  const dom = installMinimalDom();
  const { build } = await import("vite");
  const { createElement, act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  await build({ configFile: false, logLevel: "silent", root, build: {
    outDir: output, emptyOutDir: false,
    lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-retry" },
    rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } },
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

test('S20 HTTP audio without SubtleCrypto creates AUDIO only after verifying exact original SHA',async()=>{
  const {createHash}=await import('node:crypto');
  // Valid one-second mono PCM16 WAV; no ASR/model/browser decoding is simulated.
  const raw=Buffer.alloc(32044);
  raw.write('RIFF',0);raw.writeUInt32LE(32036,4);raw.write('WAVE',8);
  raw.write('fmt ',12);raw.writeUInt32LE(16,16);raw.writeUInt16LE(1,20);
  raw.writeUInt16LE(1,22);raw.writeUInt32LE(16000,24);raw.writeUInt32LE(32000,28);
  raw.writeUInt16LE(2,32);raw.writeUInt16LE(16,34);raw.write('data',36);raw.writeUInt32LE(32000,40);
  const hash=createHash('sha256').update(raw).digest('hex');
  const oldFetch=globalThis.fetch,originalCrypto=Object.getOwnPropertyDescriptor(globalThis,'crypto');
  const originalCryptoValue=globalThis.crypto,oldCreate=URL.createObjectURL,oldRevoke=URL.revokeObjectURL;
  const calls=[],revoked=[];let workspace;
  const locator={kind:'audio_segment',value:'{"segment":"s:1","start_ms":200,"end_ms":800,"start":0,"end":1}'};
  const citation={citation_id:'citation-audio',source_id:'source-1',source_version_id:'version-1',evidence_span_id:'span-audio',page:1,origin:'raw_source',context_item_id:'source-1',locator};
  const content={source_version_id:'version-1',source_sha256:hash,transcript_sha256:'b'.repeat(64),duration_ms:1000,
    selected_segment_id:'s:1',selected_start:0,selected_end:1,segments:[{segment_id:'s:1',start_ms:200,end_ms:800,text:'원문'}]};
  URL.createObjectURL=()=> 'blob:s20-http-audio';URL.revokeObjectURL=url=>revoked.push(url);
  globalThis.fetch=async(url,init)=>{calls.push({url,init});return url.endsWith('&representation=audio')?
    new Response(raw,{headers:{'Content-Type':'audio/wav'}}):Response.json(content);};
  try{
    Object.defineProperty(globalThis,'crypto',{configurable:true,value:{
      getRandomValues:originalCryptoValue.getRandomValues.bind(originalCryptoValue),
    }});
    assert.equal(globalThis.crypto.subtle,undefined,'HTTP capability double, not a live browser assertion');
    workspace=await mountSourceRetryWorkspace({...adapter,
      listSources:async()=>[{...sourceRetryPayload,filename:'qa.wav'}],listStudioOutputs:async()=>[],
      citationUrl:value=>citationContentUrl('workspace-1',value,{notebookId:'notebook-1'}),
      loadNotebookConversation:async()=>({run_id:'run-audio',run_result_id:'result-audio',answer:'저장된 음성 답변',insufficient:false,
        citations:[{...citation,content_url:citationContentUrl('workspace-1',citation,{notebookId:'notebook-1'})}]})
    },'s20-http-audio');
    const link=findElements(workspace.container,node=>node.tagName==='A'&&node.textContent.includes('Citation'))[0];
    await workspace.act(async()=>link.dispatchEvent(new MinimalEvent('click')));
    assert.equal(calls.length,1,'전사 표시만으로 바이너리를 가져오지 않는다');
    const mark=findElements(workspace.container,node=>node.tagName==='MARK')[0];
    assert.ok(mark);assert.equal(mark.textContent,'원');
    const foreground=mark.style.color,background=mark.style.backgroundColor;
    assert.equal(typeof foreground,'string','음성 강조는 상속된 밝은 글자색에 의존하지 않는다');
    assert.match(foreground,/^#[0-9a-f]{6}$/iu);
    assert.equal(background,'#fff0a5','기존 음성 강조 배경은 유지한다');
    const luminance=color=>{
      const channels=[1,3,5].map(offset=>parseInt(color.slice(offset,offset+2),16)/255)
        .map(value=>value<=0.04045?value/12.92:((value+0.055)/1.055)**2.4);
      return channels[0]*0.2126+channels[1]*0.7152+channels[2]*0.0722;
    };
    const light=luminance(background),dark=luminance(foreground);
    const contrast=(Math.max(light,dark)+0.05)/(Math.min(light,dark)+0.05);
    assert.ok(contrast>=4.5,`렌더된 음성 mark 색상 대비 ${contrast}:1`);
    const load=findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='원본 불러오기')[0];
    assert.ok(load);
    await workspace.act(async()=>{load.dispatchEvent(new MinimalEvent('click'));await new Promise(resolve=>setTimeout(resolve,20));});
    assert.equal(calls.length,2);assert.equal(calls[1].url,'/bff/api/workspaces/workspace-1/citations/citation-audio/content?notebook_id=notebook-1&representation=audio');
    assert.equal(calls[1].init.credentials,'same-origin');assert.equal(calls[1].init.cache,'no-store');
    const player=findElements(workspace.container,node=>node.tagName==='AUDIO')[0];
    assert.ok(player,`검증된 WAV는 SubtleCrypto 없이도 AUDIO를 생성해야 한다: ${workspace.container.textContent}`);
    player.pause=()=>{}; // MinimalDom has no browser media methods; cleanup still runs in React.
    assert.equal(player.getAttribute('src'),'blob:s20-http-audio');
    assert.doesNotMatch(workspace.container.textContent,/AUDIO_CONTENT_UNAVAILABLE/);
    await workspace.act(async()=>findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='원문 닫기')[0].dispatchEvent(new MinimalEvent('click')));
    assert.deepEqual(revoked,['blob:s20-http-audio']);
  }finally{
    if(workspace)await workspace.cleanup();
    globalThis.fetch=oldFetch;URL.createObjectURL=oldCreate;URL.revokeObjectURL=oldRevoke;
    if(originalCrypto)Object.defineProperty(globalThis,'crypto',originalCrypto);else delete globalThis.crypto;
  }
});

test('S20 audio wrong SHA creates no AUDIO or Blob URL in HTTP and secure capabilities',async(t)=>{
  for(const secure of [false,true])await t.test(secure?'secure':'HTTP',async()=>{
    const raw=Buffer.alloc(32044);
    raw.write('RIFF',0);raw.writeUInt32LE(32036,4);raw.write('WAVE',8);
    raw.write('fmt ',12);raw.writeUInt32LE(16,16);raw.writeUInt16LE(1,20);
    raw.writeUInt16LE(1,22);raw.writeUInt32LE(16000,24);raw.writeUInt32LE(32000,28);
    raw.writeUInt16LE(2,32);raw.writeUInt16LE(16,34);raw.write('data',36);raw.writeUInt32LE(32000,40);
    const originalCrypto=Object.getOwnPropertyDescriptor(globalThis,'crypto'),originalCryptoValue=globalThis.crypto;
    const oldFetch=globalThis.fetch,oldCreate=URL.createObjectURL;let workspace,created=0;
    const calls=[];
    const citation={citation_id:'citation-audio',source_id:'source-1',source_version_id:'version-1',evidence_span_id:'span-audio',page:1,
      origin:'raw_source',context_item_id:'source-1',locator:{kind:'audio_segment',value:'{"segment":"s:1","start_ms":200,"end_ms":800,"start":0,"end":1}'}};
    const content={source_version_id:'version-1',source_sha256:'0'.repeat(64),transcript_sha256:'b'.repeat(64),duration_ms:1000,
      selected_segment_id:'s:1',selected_start:0,selected_end:1,segments:[{segment_id:'s:1',start_ms:200,end_ms:800,text:'원문'}]};
    URL.createObjectURL=()=>{created++;return 'blob:must-not-exist';};
    globalThis.fetch=async(url,init)=>{calls.push({url,init});return url.endsWith('&representation=audio')?
      new Response(raw,{headers:{'Content-Type':'audio/wav'}}):Response.json(content);};
    try{
      if(!secure)Object.defineProperty(globalThis,'crypto',{configurable:true,value:{getRandomValues:originalCryptoValue.getRandomValues.bind(originalCryptoValue)}});
      workspace=await mountSourceRetryWorkspace({...adapter,
        listSources:async()=>[{...sourceRetryPayload,filename:'qa.wav'}],listStudioOutputs:async()=>[],
        citationUrl:value=>citationContentUrl('workspace-1',value,{notebookId:'notebook-1'}),
        loadNotebookConversation:async()=>({run_id:'run-audio',run_result_id:'result-audio',answer:'저장된 음성 답변',insufficient:false,
          citations:[{...citation,content_url:citationContentUrl('workspace-1',citation,{notebookId:'notebook-1'})}]})
      },'s20-wrong-sha');
      await workspace.act(async()=>findElements(workspace.container,node=>node.tagName==='A'&&node.textContent.includes('Citation'))[0].dispatchEvent(new MinimalEvent('click')));
      assert.equal(calls.length,1);
      await workspace.act(async()=>{findElements(workspace.container,node=>node.tagName==='BUTTON'&&node.textContent==='원본 불러오기')[0].dispatchEvent(new MinimalEvent('click'));await new Promise(resolve=>setTimeout(resolve,20));});
      assert.equal(calls.length,2);
      assert.match(workspace.container.textContent,/AUDIO_CONTENT_UNAVAILABLE/);
      assert.equal(findElements(workspace.container,node=>node.tagName==='AUDIO').length,0);
      assert.equal(created,0,'해시 불일치 원본의 Blob URL 생성 금지');
    }finally{
      if(workspace)await workspace.cleanup();globalThis.fetch=oldFetch;URL.createObjectURL=oldCreate;
      if(originalCrypto)Object.defineProperty(globalThis,'crypto',originalCrypto);else delete globalThis.crypto;
    }
  });
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

test("질문 실제 React 이벤트는 정책 차단 원인·수동 재질문·저장 답변 재조회를 보존한다", async () => {
  let writes = 0, reads = 0, stored = null, workspace;
  const effectAdapter = { ...adapter,
      getModelSettings:async()=>dModelView(),
    async listSources() { return [sourceRetryPayload]; },
    async listStudioOutputs() { return []; },
    async loadNotebookConversation() { reads++; return stored; },
    async askQuestion(input, options) {
      writes++;
      assert.equal(input.question, "자료를 요약해 주세요");
      assert.equal(input.modelSettingsVersion,1);
      assert.ok(options.signal instanceof AbortSignal);
      assert.ok(options.idempotencyKey);
      if (writes === 1) throw new Error("EGRESS_POLICY_DENIED");
      stored = { run_id: "run-policy-retry", run_result_id: "result-policy-retry", answer: "확인된 자료의 답변", insufficient: false, citations: [] };
      return stored;
    },
  };
  try {
    workspace = await mountSourceRetryWorkspace(effectAdapter, "policy-question");
    const textarea = findElements(workspace.container, node => node.tagName === "TEXTAREA")[0];
    const props = node => node[Object.keys(node).find(key => key.startsWith("__reactProps$"))];
    await workspace.act(async () => props(textarea).onChange({ currentTarget: { value: "자료를 요약해 주세요" }, target: { value: "자료를 요약해 주세요" } }));
    const submit = async () => {
      const form = findElements(workspace.container, node => node.tagName === "FORM" && node.getAttribute("class") === "conversation-composer")[0];
      await workspace.act(async () => props(form).onSubmit({ preventDefault() {} }));
    };
    await submit();
    assert.equal(writes, 1, "정책 차단은 자동 재전송하지 않는다");
    const alert = findElements(workspace.container, node => node.getAttribute?.("role") === "alert").find(node => node.textContent.includes("EGRESS_POLICY_DENIED"));
    assert.equal(alert?.textContent, "조직 또는 Workspace의 외부 전송 정책으로 차단되었습니다. 정책 설정을 확인해 주세요. (EGRESS_POLICY_DENIED)");
    assert.doesNotMatch(alert.textContent, /입력.*다시 제출/u);
    assert.match(workspace.container.textContent, /ready.pdf/u);
    await submit();
    assert.equal(writes, 2);
    assert.match(workspace.container.textContent, /확인된 자료의 답변/u);
    const currentAlerts=findElements(workspace.container,node=>node.getAttribute?.("role")==="alert");
    assert.equal(currentAlerts.some(node=>node.textContent.includes("EGRESS_POLICY_DENIED")),false,"현재 실행 배너의 이전 오류는 해제한다");
    const turns=findElements(workspace.container,node=>node.getAttribute?.("data-conversation-turn")!==null);
    assert.equal(turns.length,2,"서로 다른 명시 요청의 두 질문을 보존한다");
    assert.match(turns[0].textContent,/EGRESS_POLICY_DENIED/u,"이전 실패는 대화 이력에 남긴다");
    assert.match(turns[1].textContent,/확인된 자료의 답변/u);
    const users=findElements(workspace.container,node=>node.getAttribute?.("class")==="user-message");
    assert.equal(users.filter(node=>node.textContent.includes("자료를 요약해 주세요")).length,2);
    await workspace.cleanup(); workspace = null;
    const before = reads;
    workspace = await mountSourceRetryWorkspace(effectAdapter, "policy-question-reentry");
    assert.ok(reads > before);
    assert.equal(writes, 2, "재진입은 저장 답변만 조회한다");
    assert.match(workspace.container.textContent, /확인된 자료의 답변/u);
  } finally { if (workspace) await workspace.cleanup(); }
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
      getModelSettings:async()=>dModelView(),
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
    assert.equal(calls[0].input.modelSettingsVersion,1);
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

test("Source 업로드 실제 React 실패는 안전 오류를 표시하고 기존 목록만 유지한다", async () => {
  const calls = [];
  const workspace = await mountSourceRetryWorkspace({ ...adapter,
    async listSources() { return [sourceRetryPayload]; },
    async listStudioOutputs() { return []; },
    async uploadPdf(file) {
      return uploadSource("workspace-1", file, { notebookId: "notebook-1", fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return Response.json({ error: { code: "ACCESS_DENIED", message: "PRIVATE_SERVER_DETAIL" } }, { status: 403 });
      } });
    },
  }, "upload-denied");
  try {
    const add = findElements(workspace.container, node => node.tagName === "BUTTON" && node.getAttribute?.("class") === "source-add-button")[0];
    await workspace.act(async () => add.dispatchEvent(new MinimalEvent("click")));
    const input = findElements(workspace.container, node => node.tagName === "INPUT" && node.getAttribute?.("accept")?.includes(".txt"))[0];
    Object.defineProperty(input, "files", { configurable: true, value: [new File(["fixture"], "rejected.txt", { type: "text/plain" })] });
    await workspace.act(async () => { input.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); await Promise.resolve(); });
    assert.equal(calls.length, 1, "권한 실패는 자동 재업로드하지 않는다");
    assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/sources");
    assert.match(workspace.container.textContent, /ACCESS_DENIED/u);
    assert.match(workspace.container.textContent, /ready.pdf/u);
    assert.doesNotMatch(workspace.container.textContent, /PRIVATE_SERVER_DETAIL|rejected.txt/u);
  } finally { await workspace.cleanup(); }
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
  const output = await mkdtemp(path.join(tmpdir(), "daon-workspace-knowledge-link-react-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "knowledge-link" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } } } });
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
        return { record_id: "registration-1", action: "knowledge_registration", output_version_id: payload.output_version_id, status: "registered", registered_source_version_id: "version-k", searchable: true }; },
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
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } },
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
      async issueSourceDeletionStepUp(sourceId, password) { calls.push(["step-up", sourceId, password]); return "source-delete-grant"; },
      async requestSourceDeletion(source, options) { calls.push(["request", source.sourceId, options.idempotencyKey, options.stepUpAuthorization]); return { data: { request_id: "request-1", source_id: source.sourceId, state: "purged" }, etag: '"deletion:request-1:1"' }; },
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
    assert.equal(calls.length, 0, "삭제 확인 전 추가 인증과 삭제 요청 모두 실행하지 않는다");
    const password = findElements(container, (node) => node.tagName === "INPUT" && node.getAttribute("aria-label") === "Source 삭제 추가 인증 비밀번호")[0];
    assert.ok(password); password.value = "fixture-source-delete-confirmation";
    await act(async () => { confirm.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls, [["step-up", "source-1", "fixture-source-delete-confirmation"],
      ["request", "source-1", "source-1", "source-delete-grant"]]);
    assert.match(container.textContent, /Source 원문과 파생 자료의 실제 정리가 완료되었습니다/u);
    assert.equal(findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 취소").length, 0);
    assert.equal(calls.length, 2);
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
    assert.match(container.textContent, /Source 원문과 파생 자료의 실제 정리가 완료되었습니다/u);
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
      async issueSourceDeletionStepUp(sourceId, password) { assert.equal(sourceId, "source-old"); assert.equal(password, "fixture-old-source-confirmation"); return "old-source-grant"; },
      async listStudioOutputs() { return []; }, async requestSourceDeletion() { calls.push("old-request"); return pending.promise; } };
    const newAdapter = { ...adapter, notebookContext: { notebook_id: "notebook-new" }, async listSources() { return []; }, async listStudioOutputs() { return []; },
      async listSourceDeletionRequests() { return [{ request_id: "request-new", source_id: "source-new", state: "grace_period", version: 1, grace_until: "2026-09-20T00:00:00Z", legal_hold_active: false }]; },
      async cancelSourceDeletionRequest() { calls.push("new-cancel"); return { data: { request_id: "request-new", source_id: "source-new", state: "cancelled" }, etag: '"deletion:request-new:2"' }; } };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-old", adapter: oldAdapter })); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => findElements(container, (node) => node.tagName === "BUTTON" && /old\.pdf 작업/u.test(node.getAttribute?.("aria-label") ?? ""))[0].dispatchEvent(new MinimalEvent("click")));
    await act(async () => findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "Source 삭제 요청")[0].dispatchEvent(new MinimalEvent("click")));
    const password = findElements(container, (node) => node.tagName === "INPUT" && node.getAttribute("aria-label") === "Source 삭제 추가 인증 비밀번호")[0];
    assert.ok(password); password.value = "fixture-old-source-confirmation";
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
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } },
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
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } },
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
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } },
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
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("general-conversation") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?general=${Date.now()}`);
    const calls = [];
    let authorizeCalls = 0;
    const effectAdapter = {
      ...adapter,
      getModelSettings:async()=>dModelView(),
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
    assert.deepEqual(calls, [{ knowledgeContext: null, question: "다음 작업을 어떻게 진행할까?",modelSettingsVersion:1 }]);
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
      question: "선택 Source를 근거로 답해줘",modelSettingsVersion:1,
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
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: Object.fromEntries(["react", "react-dom", "react-dom/client"].map(name => [name, import.meta.resolve(name)])) } },
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

const bTurn = (id,question,text,client=null,state='completed') => ({turn_id:id,question,response:text?{kind:state==='completed'?'answer':'system',text,insufficient:state!=='completed',next_actions:[],citations:[]}:null,state,submitted_at:'2026-10-21T00:00:00.123456Z',model:null,client_request_id:client});

test('C 순수 이력 병합은 Run 없는 두 근거 안내 상태와 엄격 system 조합만 보존한다',async()=>{
  const {mergeNotebookConversationTurns}=await import('../../packages/ui/src/product-workspace-model.js');
  for(const state of ['no_knowledge','no_relevant_evidence']) {
    const turn=bTurn('turn-c','계약기간은?','질문을 확인할 자료를 선택해 주세요.','request-c',state);
    turn.response.next_actions=['질문의 기간을 확인할 자료를 선택해 주세요.'];
    assert.deepEqual(mergeNotebookConversationTurns([], [turn]),[turn]);
    for(const value of [{...turn,model:{provider_code:'forged',model_id:'unexecuted'}},
        {...turn,response:{...turn.response,kind:'answer'}},{...turn,response:null},
        {...turn,response:{...turn.response,next_actions:[]}}]) {
      assert.throws(()=>mergeNotebookConversationTurns([],[value]),/NOTEBOOK_CONVERSATION_INVALID/);
    }
  }
});

test('C 실제 DOM 근거 안내는 실패 배너·가짜 Run 없이 누적하고 재진입 GET만 복원하며 Studio를 실행하지 않는다',async()=>{
  let posts=0,studio=0,reads=0;
  let page=[bTurn('turn-old-c','이전 완료 질문','이전 완료 답변','request-old-c')];
  const notice={kind:'system_notice',turn_id:'turn-c',answer:'질문을 확인할 자료가 선택되지 않았습니다.',
    grounding_status:'no_knowledge',insufficient:true,citations:[],next_actions:['질문의 기간을 확인할 자료를 선택해 주세요.']};
  const list=async()=>{reads++;return {items:page,next_cursor:null};};
  const nextAdapter=()=>s18Adapter({loadNotebookConversation:async()=>s18Answer('이전 완료 답변'),listConversationTurns:list,
    askQuestion:async(input,options)=>{
      posts++;
      page=[{...bTurn('turn-c',input.question,notice.answer,options.idempotencyKey,'no_knowledge'),
        response:{kind:'system',text:notice.answer,insufficient:true,citations:[],next_actions:notice.next_actions}},...page];
      return notice;
    },createGeneration:async()=>{studio++;},createReport:async()=>{studio++;}});
  const workspace=await mountS17ReconnectWorkspace(nextAdapter(),{outputRoot:tmpdir()});
  try {
    await s18Type(workspace,'안녕, 계약기간은 언제까지인가요?');
    await workspace.act(async()=>{await s18Submit(workspace);await Promise.resolve();});
    assert.match(workspace.container.textContent,/질문을 확인할 자료가 선택되지 않았습니다/u);
    assert.match(workspace.container.textContent,/이전 완료 답변/u);
    assert.doesNotMatch(workspace.container.textContent,/QUESTION_RESPONSE_INVALID|질문 실행에 실패/u);
    assert.equal(findElements(workspace.container,node=>node.getAttribute?.('data-conversation-turn')!==null).length,2);
    assert.equal(posts,1);assert.equal(studio,0);assert(reads>=2);
    await workspace.render(nextAdapter());
    assert.match(workspace.container.textContent,/질문을 확인할 자료가 선택되지 않았습니다/u);
    assert.equal(posts,1);assert.equal(studio,0);
  }finally{await workspace.cleanup();}
});

test('B 두 질문 누적·즉시 user 표시·응답 유실 뒤 GET exact key 병합은 재실행하지 않는다',async()=>{
  const pending=deferred();let submission,key,posts=0,reads=0;
  let page=[bTurn('turn-old','이전 질문','이전 답변','request-old')];
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({
    listConversationTurns:async()=>{reads++;return {items:page,next_cursor:null};},
    askQuestion:async(input,options)=>{posts++;key=options.idempotencyKey;return pending.promise;},
  }));
  try {
    assert.match(workspace.container.textContent,/이전 질문/u);
    await s18Type(workspace,'응답 유실 질문');await workspace.act(async()=>{submission=s18Submit(workspace);});
    assert.match(workspace.container.textContent,/응답 유실 질문/u);assert.match(workspace.container.textContent,/이전 답변/u);
    page=[bTurn('turn-new','응답 유실 질문','처리 결과를 확인해 주세요.',key,'uncertain'),...page];
    await workspace.act(async()=>{pending.reject(new Error('UPSTREAM_FAILURE'));await submission;await Promise.resolve();});
    const bubbles=findElements(workspace.container,node=>node.getAttribute?.('data-conversation-turn')!==null);
    assert.equal(bubbles.length,2);assert.equal(posts,1);assert(reads>=2);
    assert.match(workspace.container.textContent,/처리 결과를 확인/u);assert.match(workspace.container.textContent,/이전 답변/u);
  } finally {pending.resolve(s18Answer());if(submission)await submission;await workspace.cleanup();}
});

test('B 질문 실패는 마지막 완료 답변을 Studio용으로 보존한다',async()=>{
  const {projectQuestionFailureState}=await import('../../packages/ui/src/product-workspace-model.js');
  const answer={answer:'완료 답변',citations:[]};
  const projected=projectQuestionFailureState({answer,answerIntent:'work_support_source_backed'},new Error('QUESTION_FAILED'));
  assert.equal(projected.answer,answer);assert.equal(projected.answerIntent,'work_support_source_backed');
});

test('B 실제 두 답변 누적·이전 페이지·Source 선택 보존과 재진입 복원은 GET만 사용한다',async()=>{
  let posts=0;const reads=[];
  const old=bTurn('turn-older','가장 이전 질문','가장 이전 답변','request-older');
  let page=[bTurn('turn-saved','저장 질문','저장 답변','request-saved')];
  const list=async(options)=>{reads.push(options);return options.before?{items:[old],next_cursor:null}:{items:page,next_cursor:'b2xk'};};
  const active=s18Adapter({listConversationTurns:list,askQuestion:async(input,options)=>{
    posts++;page=[bTurn('turn-'+posts,input.question,posts===1?'첫 답변':'둘째 답변',options.idempotencyKey),...page];
    return s18Answer(posts===1?'첫 답변':'둘째 답변');
  }});
  const workspace=await mountS17ReconnectWorkspace(active);
  try {
    const more=findElements(workspace.container,n=>n.tagName==='BUTTON'&&n.textContent==='이전 대화 더 보기')[0];
    assert(more);await workspace.act(async()=>more.dispatchEvent(new MinimalEvent('click')));
    assert.equal(reads[1].before,'b2xk');assert.match(workspace.container.textContent,/가장 이전 질문/u);
    await s18Type(workspace,'첫 질문');await workspace.act(async()=>s18Submit(workspace));
    await s18Type(workspace,'둘째 질문');await workspace.act(async()=>s18Submit(workspace));
    for(const text of ['첫 질문','첫 답변','둘째 질문','둘째 답변'])assert(workspace.container.textContent.includes(text));
    const source=findElements(workspace.container,n=>n.tagName==='BUTTON'&&n.textContent.includes('other.pdf'))[0];
    await workspace.act(async()=>source.dispatchEvent(new MinimalEvent('click')));
    assert.match(workspace.container.textContent,/첫 질문/u);assert.match(workspace.container.textContent,/둘째 답변/u);
    await workspace.render(s18Adapter({listConversationTurns:list,askQuestion:async()=>{throw new Error('MUST_NOT_REEXECUTE');}}));
    assert.match(workspace.container.textContent,/첫 질문/u);assert.match(workspace.container.textContent,/둘째 답변/u);assert.equal(posts,2);
  }finally{await workspace.cleanup();}
});

test('B 이전 페이지를 조회한 후 새로고침은 철회된 옛 답변을 보유하지 않는다',async()=>{
  let refreshed=false;
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({listConversationTurns:async({before})=>before
    ?{items:[bTurn('turn-old-access','옛 질문','철회될 답변','request-old')],next_cursor:null}
    :{items:[bTurn('turn-current','현재 질문',refreshed?'답변에 현재 접근할 수 없습니다.':'현재 답변','request-current',refreshed?'unavailable':'completed')],next_cursor:refreshed?null:'b2xk'}}));
  try{
    await workspace.act(async()=>findElements(workspace.container,n=>n.tagName==='BUTTON'&&n.textContent==='이전 대화 더 보기')[0].dispatchEvent(new MinimalEvent('click')));
    assert.match(workspace.container.textContent,/철회될 답변/u);refreshed=true;
    await workspace.act(async()=>findElements(workspace.container,n=>n.tagName==='BUTTON'&&n.textContent==='대화 새로고침')[0].dispatchEvent(new MinimalEvent('click')));
    assert.doesNotMatch(workspace.container.textContent,/철회될 답변|현재 답변/u);
    assert.match(workspace.container.textContent,/현재 질문|답변에 현재 접근/u);
  }finally{await workspace.cleanup();}
});

test('B Notebook 전환 후 늦은 이전 이력은 표시하지 않는다',async()=>{
  const history=deferred();let posts=0;
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({listConversationTurns:async()=>history.promise}));
  try{
    await workspace.render(s18Adapter({notebookContext:{notebook_id:'notebook-new',sources:[]},
      listConversationTurns:async()=>({items:[bTurn('turn-new-nb','새 Notebook 질문','새 Notebook 답변','request-new')],next_cursor:null}),
      askQuestion:async()=>{posts++;return s18Answer();}}));
    await workspace.act(async()=>{history.resolve({items:[bTurn('turn-old-nb','이전 Notebook 비공개 질문','이전 Notebook 비공개 답변','request-old')],next_cursor:null});await Promise.resolve();});
    assert.match(workspace.container.textContent,/새 Notebook 질문/u);assert.doesNotMatch(workspace.container.textContent,/이전 Notebook 비공개/u);assert.equal(posts,0);
  }finally{history.resolve({items:[],next_cursor:null});await workspace.cleanup();}
});

test('B Source 전환 취소 뒤 같은 요청의 저장 이력은 GET으로 병합하고 늦은 실행 응답을 무시한다',async()=>{
  const pending=deferred();let key,submission,posts=0,cancelled=false;
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({
    listConversationTurns:async()=>({items:cancelled?[bTurn('turn-cancel','취소 확인 질문',null,key,'pending')]:[],next_cursor:null}),
    askQuestion:async(input,options)=>{key=options.idempotencyKey;posts++;return pending.promise;},
  }));
  try{
    await s18Type(workspace,'취소 확인 질문');await workspace.act(async()=>{submission=s18Submit(workspace);});
    await workspace.act(async()=>findElements(workspace.container,n=>n.tagName==='BUTTON'&&n.textContent.includes('other.pdf'))[0].dispatchEvent(new MinimalEvent('click')));
    assert.match(workspace.container.textContent,/서버 처리를 중단했음을 보장하지 않습니다/u);cancelled=true;
    await workspace.act(async()=>findElements(workspace.container,n=>n.tagName==='BUTTON'&&n.textContent==='대화 새로고침')[0].dispatchEvent(new MinimalEvent('click')));
    assert.equal(findElements(workspace.container,n=>n.getAttribute?.('data-conversation-turn')!==null).length,1);
    await workspace.act(async()=>{pending.resolve(s18Answer('늦은 취소 답변'));await submission;});
    assert.doesNotMatch(workspace.container.textContent,/늦은 취소 답변/u);assert.equal(posts,1);
  }finally{pending.resolve(s18Answer());if(submission)await submission;await workspace.cleanup();}
});

test('B 순수 병합은 상태 위조·중복 요청ID·모호한 turn 결합을 거부하고 시각을 추측 ID로 쓰지 않는다',async()=>{
  const {mergeNotebookConversationTurns}=await import('../../packages/ui/src/product-workspace-model.js');
  const one=bTurn('turn-1','같은 질문','답변1','request-1');
  const two=bTurn('turn-2','같은 질문','답변2','request-2');
  assert.equal(mergeNotebookConversationTurns([], [one,two]).length,2);
  const actions=count=>({...one,response:{...one.response,next_actions:Array.from({length:count},(_,i)=>`행동 ${i+1}`)}});
  assert.equal(mergeNotebookConversationTurns([],[actions(8)])[0].response.next_actions.length,8);
  assert.throws(()=>mergeNotebookConversationTurns([],[actions(9)]),/NOTEBOOK_CONVERSATION_INVALID/u);
  for(const bad of [{...one,state:'failed'},{...one,state:'pending'}, {...one,response:{...one.response,text:''}},
    {...one,model:{api_key:'private'}},{...one,submitted_at:'yesterday'}]){
    assert.throws(()=>mergeNotebookConversationTurns([],[bad]),/NOTEBOOK_CONVERSATION_INVALID/u);
  }
  assert.throws(()=>mergeNotebookConversationTurns([one],[{...two,client_request_id:'request-1'}]),/NOTEBOOK_CONVERSATION_INVALID/u);
  assert.throws(()=>mergeNotebookConversationTurns([],[one,one]),/NOTEBOOK_CONVERSATION_INVALID/u);
});


test("D 실제 DOM 모델 선택 자동저장 중 잠금·정확 version 질문·재진입 복원",async()=>{
  let current=dModelView(0),saves=0,posts=0;const pending=deferred();const inputs=[];
  const make=()=>s18Adapter({getModelSettings:async()=>structuredClone(current),
    saveModelSettings:async(input)=>{saves++;await pending.promise;current=dModelView(1,input.reasoning_effort);return {notebook_id:"notebook-s18",version:1,selection:current.selection,replayed:false};},
    askQuestion:async(input)=>{posts++;inputs.push(input);return s18Answer("확인된 답변");}});
  const workspace=await mountS17ReconnectWorkspace(make(),{outputRoot:tmpdir()});
  const select=()=>findElements(workspace.container,n=>n.tagName==="SELECT"&&n.getAttribute("aria-label")==="Provider · 모델")[0];
  const submit=()=>findElements(workspace.container,n=>n.tagName==="BUTTON"&&n.getAttribute("aria-label")==="질문 실행")[0];
  try{
    await s18Type(workspace);assert.equal(submit().disabled,true);
    let saving;await workspace.act(async()=>{saving=s18Props(select()).onChange({target:{value:JSON.stringify(["connection-d","solar-pro4"])}});});
    assert.equal(saves,1);assert.equal(submit().disabled,true);assert.match(workspace.container.textContent,/모델 설정 저장 중/u);
    await workspace.act(async()=>{pending.resolve();await saving;await Promise.resolve();await Promise.resolve();});
    assert.match(workspace.container.textContent,/모델 설정 저장됨/u);
    await workspace.act(async()=>{await s18Submit(workspace);});
    assert.equal(posts,1);assert.equal(inputs[0].modelSettingsVersion,1);
    await workspace.render(make());assert.match(workspace.container.textContent,/모델 설정 저장됨/u);
    assert.equal(saves,1);assert.equal(posts,1);
  }finally{await workspace.cleanup();}
});


test("D 질문 오류는 모델 설정 원인과 명시 재선택 조치를 안내한다",()=>{
  assert.match(questionFailureMessage("NOTEBOOK_MODEL_SETTINGS_CHANGED"),/다시 조회.*모델/u);
  assert.match(questionFailureMessage("NOTEBOOK_MODEL_SELECTION_REQUIRED"),/직접 선택/u);
  assert.match(questionFailureMessage("NOTEBOOK_MODEL_UNAVAILABLE"),/다른 허용 모델.*직접 선택/u);
  assert.doesNotMatch(questionFailureMessage("NOTEBOOK_MODEL_UNAVAILABLE"),/입력을 확인/u);
});


test("D 실제 StudioPane 생성은 Web POST에 의도한 선택 version만 전달한다",async()=>{
  const {createStudioGeneration,authorizeStudioGeneration}=await import("../../apps/web/lib/product-workspace-api.js");
  const calls=[];
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({loadNotebookConversation:async()=>({...s18Answer("근거 답변"),citations:[{citation_id:"citation-d",source_id:"source-1",source_version_id:"version-1",evidence_span_id:"span-d",page:1,origin:"raw_source",context_item_id:"source-1",origin_label:"원본 자료",authority_label:"Source",locator:{kind:"page",value:"1"}}]}),
    authorizeGeneration:(input,options)=>authorizeStudioGeneration("workspace-1",input,{...options,notebookId:"notebook-s18",fetchImpl:async(url,options)=>{
      calls.push({url,body:JSON.parse(options.body),key:options.headers['Idempotency-Key']});return Response.json({data:{step_up_authorization_id:null,expires_at:"2099-01-01T00:00:00Z",generation_fingerprint:'a'.repeat(64)},meta:{trace_id:"trace-1",workspace_id:"workspace-1"}},{status:201});}}),
    createGeneration:(input,options)=>createStudioGeneration("workspace-1",input,{...options,notebookId:"notebook-s18",fetchImpl:async(url,options)=>{
      calls.push({url,body:JSON.parse(options.body),key:options.headers["Idempotency-Key"]});return Response.json({data:{job_id:"job-d",status:"queued",output_type:"evidence_report"},meta:{trace_id:"trace-1",workspace_id:"workspace-1",replayed:false}},{status:201});}}),
  }),{outputRoot:tmpdir()});
  const button=text=>findElements(workspace.container,n=>n.tagName==="BUTTON"&&n.textContent.trim()===text)[0];
  const field=label=>findElements(workspace.container,n=>n.tagName==="LABEL"&&n.textContent.startsWith(label))[0];
  const set=async(label,value)=>{
    const control=findElements(field(label),n=>n.tagName==="INPUT"||n.tagName==="SELECT")[0];
    assert(control,`FIELD_${label}`);await workspace.act(async()=>s18Props(control).onChange({currentTarget:{value},target:{value}}));
  };
  try{
    const type=findElements(workspace.container,n=>n.tagName==="BUTTON"&&n.textContent.includes("근거 기반 보고서"))[0];
    await workspace.act(async()=>type.dispatchEvent(new MinimalEvent("click")));
    await set("목적","근거 정리");await set("독자","검토자");await set("분량","short");await set("구성","summary-body-conclusion");
    await set("출력 형식","pdf");await set("검토 조건","review_required");
    await workspace.act(async()=>button("설정 확인").dispatchEvent(new MinimalEvent("click")));
    const form=findElements(workspace.container,n=>n.tagName==="FORM"&&n.getAttribute("class")==="studio-config-form")[0];
    await workspace.act(async()=>{s18Props(form).onSubmit({preventDefault(){}});await Promise.resolve();await Promise.resolve();});
    assert.equal(calls.length,2);
    assert.equal(calls[0].url,"/bff/api/studio-generation-requests/authorization");
    assert.equal(calls[0].key,calls[1].key);
    assert.equal(calls[1].url,"/bff/api/studio-generation-requests");
    assert.equal(calls[1].body.model_settings_version,1);
    assert.equal(calls[1].body.run_id,"run-s18");
    assert.equal(Object.hasOwn(calls[1].body,"intended_model_selection"),false);
    assert.equal(Object.hasOwn(calls[1].body,"model_id"),false);
  }finally{await workspace.cleanup();}
});


test("D version0 무Source 사실만 사전판정에 보내고 인사는 명시 모델 선택을 요구한다",async()=>{
  let posts=0,studio=0,input;
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({getModelSettings:async()=>dModelView(0),listSources:async()=>[],listKnowledgePackages:async()=>[],
    askQuestion:async(value)=>{posts++;input=value;return {kind:"system_notice",turn_id:"turn-notice-d",answer:"질문을 확인할 자료가 선택되지 않았습니다.",grounding_status:"no_knowledge",insufficient:true,citations:[],next_actions:["자료를 선택해 주세요."]};},
    createGeneration:async()=>{studio++;}}),{outputRoot:tmpdir()});
  try{
    await s18Type(workspace,"안녕하세요");await workspace.act(async()=>{await s18Submit(workspace);});assert.equal(posts,0);
    await s18Type(workspace,"안녕, 계약기간은 언제인가요?");await workspace.act(async()=>{await s18Submit(workspace);});
    assert.equal(posts,1);assert.equal(Object.hasOwn(input,"modelSettingsVersion"),false);assert.equal(studio,0);
    assert.match(workspace.container.textContent,/질문을 확인할 자료가 선택되지 않았습니다/u);
  }finally{await workspace.cleanup();}
});


test("D 질문 설정 변경 오류 뒤 실행을 잠그고 명시 GET으로만 복구한다",async()=>{
  let version=1,posts=0,reads=0;
  const adapter=s18Adapter({getModelSettings:async()=>{reads++;return dModelView(version);},askQuestion:async()=>{posts++;throw new Error("NOTEBOOK_MODEL_SETTINGS_CHANGED");}});
  const workspace=await mountS17ReconnectWorkspace(adapter,{outputRoot:tmpdir()});
  try{
    await s18Type(workspace);await workspace.act(async()=>{await s18Submit(workspace);});
    const requery=()=>findElements(workspace.container,n=>n.tagName==="BUTTON"&&n.textContent==="마지막 저장값 다시 조회")[0];
    assert(requery());await workspace.act(async()=>{await s18Submit(workspace);});assert.equal(posts,1);assert.equal(reads,1);
    version=2;await workspace.act(async()=>{s18Props(requery()).onClick();await Promise.resolve();await Promise.resolve();});
    assert.match(workspace.container.textContent,/모델 설정 저장됨/u);assert.equal(reads,2);assert.equal(posts,1);
  }finally{await workspace.cleanup();}
});


test("D 저장 실패는 질문을 잠그고 같은 요청 재시도 또는 명시 조회만 허용한다",async()=>{
  let saves=0,posts=0,reads=0;const keys=[];
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({getModelSettings:async()=>{reads++;return dModelView(1);},
    saveModelSettings:async(input,options)=>{saves++;keys.push(options.idempotencyKey);throw new Error("NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE");},
    askQuestion:async()=>{posts++;return s18Answer("답변");}}),{outputRoot:tmpdir()});
  const button=text=>findElements(workspace.container,n=>n.tagName==="BUTTON"&&n.textContent===text)[0];
  try{
    const select=findElements(workspace.container,n=>n.tagName==="SELECT"&&n.getAttribute("aria-label")==="추론 단계")[0];
    await workspace.act(async()=>{await s18Props(select).onChange({target:{value:"low"}});});
    await s18Type(workspace);await workspace.act(async()=>{await s18Submit(workspace);});assert.equal(posts,0);assert.equal(saves,1);
    const retry=findElements(workspace.container,n=>n.tagName==="BUTTON"&&n.textContent.includes("같은 저장 요청"))[0];
    assert(retry);await workspace.act(async()=>{await s18Props(retry).onClick();});assert.equal(saves,2);assert.equal(keys[0],keys[1]);
    await workspace.act(async()=>{await s18Props(button("마지막 저장값 다시 조회")).onClick();await Promise.resolve();});
    assert.equal(reads,2);assert.equal(posts,0);assert.match(workspace.container.textContent,/모델 설정 저장됨/u);
  }finally{await workspace.cleanup();}
});

test("D Notebook 전환 뒤 늦은 모델 설정 조회는 현재 선택을 덮지 않는다",async()=>{
  const old=deferred();let posts=0;
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({getModelSettings:()=>old.promise}),{outputRoot:tmpdir()});
  try{
    const current={...dModelView(2),notebook_id:"notebook-new"};
    await workspace.render(s18Adapter({notebookContext:{notebook_id:"notebook-new",sources:[]},getModelSettings:async()=>current,
      askQuestion:async(input)=>{posts++;assert.equal(input.modelSettingsVersion,2);return s18Answer("답변");}}));
    await workspace.act(async()=>{old.resolve(dModelView(1));await Promise.resolve();await Promise.resolve();});
    assert.match(workspace.container.textContent,/모델 설정 저장됨/u);
    await s18Type(workspace);await workspace.act(async()=>{await s18Submit(workspace);});assert.equal(posts,1);
  }finally{await workspace.cleanup();}
});

function defaultModelView(){
  const view=dModelView(0);
  const candidate={...view.available_models[0],connection_id:"connection-workspace-default",provider_code:"CUSTOM",display_name:"기존 기준모델",model_id:"uss/solar-pro4",
    reasoning:{status:"unsupported",values:[],profile_version:null}};
  return {...view,available_models:[...view.available_models,candidate],effective_selection:{connection_id:candidate.connection_id,model_id:candidate.model_id,reasoning_effort:null,origin:"workspace_default"}};
}
function confirmedDefault(view,input){
  const selection={connection_id:input.connection_id,model_id:input.model_id,reasoning_effort:input.reasoning_effort};
  return {...view,version:1,selection,effective_selection:{...selection,origin:"notebook"}};
}
const modelControl=workspace=>findElements(workspace.container,n=>n.tagName==="SELECT"&&n.getAttribute("aria-label")==="Provider · 모델")[0];

test("D 기본값 실제 DOM은 저장 Workspace 모델을 표시하고 질문 명령에만 한번 확정한다",async()=>{
  let current=defaultModelView();const saves=[],posts=[];
  const make=()=>s18Adapter({getModelSettings:async()=>structuredClone(current),saveModelSettings:async(input,options)=>{
    saves.push({input,key:options.idempotencyKey});current=confirmedDefault(current,input);return {notebook_id:current.notebook_id,version:1,selection:current.selection,replayed:false};},
    askQuestion:async input=>{posts.push(input);return s18Answer("기본값으로 확인한 답변");}});
  const workspace=await mountS17ReconnectWorkspace(make(),{outputRoot:tmpdir()});
  try{
    assert.equal(s18Props(modelControl(workspace)).value,JSON.stringify(["connection-workspace-default","uss/solar-pro4"]));
    assert.match(workspace.container.textContent,/Workspace 기본값/u);assert.equal(saves.length,0);
    await s18Type(workspace,"안녕");await workspace.act(async()=>{await s18Submit(workspace);});
    assert.equal(saves.length,1);assert.deepEqual(saves[0].input,{connection_id:"connection-workspace-default",model_id:"uss/solar-pro4",reasoning_effort:null,expected_version:0});
    assert.equal(posts.length,1);assert.equal(posts[0].modelSettingsVersion,1);
    assert.equal(findElements(workspace.container,n=>n.tagName==="TEXTAREA")[0].value,"");
    assert.match(workspace.container.textContent,/안녕/u);assert.match(workspace.container.textContent,/기본값으로 확인한 답변/u);
    await workspace.render(make());assert.equal(saves.length,1);assert.equal(s18Props(modelControl(workspace)).value,JSON.stringify(["connection-workspace-default","uss/solar-pro4"]));
  }finally{await workspace.cleanup();}
});

test("D 기본값 실제 DOM 저장 실패와 늦은 Notebook 저장은 질문을 실행하지 않는다",async()=>{
  for(const late of [false,true]){
    let current=defaultModelView(),posts=0,saves=0;const pending=deferred();let submission;
    const workspace=await mountS17ReconnectWorkspace(s18Adapter({getModelSettings:async()=>structuredClone(current),saveModelSettings:async input=>{
      saves++;if(!late)throw Error("NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE");await pending.promise;current=confirmedDefault(current,input);return {notebook_id:current.notebook_id,version:1,selection:current.selection,replayed:false};},
      askQuestion:async()=>{posts++;return s18Answer();}}),{outputRoot:tmpdir()});
    try{
      await s18Type(workspace,"안녕");await workspace.act(async()=>{submission=s18Submit(workspace);s18Submit(workspace);if(!late)await submission;});
      assert.equal(saves,1);assert.equal(posts,0);
      if(late){await workspace.render(s18Adapter({getModelSettings:async()=>({...dModelView(2),notebook_id:"notebook-new"})}));await workspace.act(async()=>{pending.resolve();await submission;});assert.equal(posts,0);}
      else assert.equal(findElements(workspace.container,n=>n.tagName==="TEXTAREA")[0].value,"안녕");
    }finally{pending.resolve();await workspace.cleanup();}
  }
});

test("D 기본값 실제 DOM은 본인 선택 우선과 사용불가 선택의 대체 금지를 유지한다",async()=>{
  for(const unavailable of [false,true]){
    const view=dModelView(1);if(unavailable)view.effective_selection=null;let saves=0,posts=0;
    const workspace=await mountS17ReconnectWorkspace(s18Adapter({getModelSettings:async()=>view,saveModelSettings:async()=>{saves++;},askQuestion:async()=>{posts++;return s18Answer();}}),{outputRoot:tmpdir()});
    try{
      assert.equal(s18Props(modelControl(workspace)).value,JSON.stringify(["connection-d","solar-pro4"]));
      await s18Type(workspace,"안녕");await workspace.act(async()=>{await s18Submit(workspace);});
      assert.equal(saves,0);assert.equal(posts,unavailable?0:1);
    }finally{await workspace.cleanup();}
  }
});

test("D 입력 정리는 정상 안내만 비우고 새 입력과 실패는 보존한다",async()=>{
  for(const outcome of ["notice","changed","failed"]){
    const pending=deferred();let submission;
    const workspace=await mountS17ReconnectWorkspace(s18Adapter({askQuestion:()=>pending.promise}),{outputRoot:tmpdir()});
    try{
      const textarea=await s18Type(workspace,"안녕");await workspace.act(async()=>{submission=s18Submit(workspace);});
      if(outcome==="changed")await s18Type(workspace,"새로 입력한 질문");
      await workspace.act(async()=>{
        if(outcome==="failed")pending.reject(Error("PROVIDER_TIMEOUT"));
        else if(outcome==="notice")pending.resolve({kind:"system_notice",turn_id:"turn-input-notice",answer:"자료를 선택해 주세요.",grounding_status:"no_knowledge",insufficient:true,citations:[],next_actions:["자료를 선택해 주세요."]});
        else pending.resolve(s18Answer("이전 질문 답변"));await submission;
      });
      assert.equal(textarea.value,outcome==="notice"?"":outcome==="changed"?"새로 입력한 질문":"안녕");
      assert.match(workspace.container.textContent,/안녕/u);
    }finally{pending.resolve(s18Answer());await workspace.cleanup();}
  }
});

test("D 기본값 실제 Studio 명령은 저장 확인 뒤 같은 version으로만 인증과 생성한다",async()=>{
  let current=defaultModelView();const calls=[];
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({getModelSettings:async()=>structuredClone(current),
    saveModelSettings:async(input,options)=>{calls.push({kind:"save",input,key:options.idempotencyKey});current=confirmedDefault(current,input);return {notebook_id:current.notebook_id,version:1,selection:current.selection,replayed:false};},
    loadNotebookConversation:async()=>({...s18Answer("근거 답변"),citations:[{citation_id:"citation-default",source_id:"source-1",source_version_id:"version-1",evidence_span_id:"span-default",page:1,origin:"raw_source",context_item_id:"source-1",origin_label:"원본 자료",authority_label:"Source",locator:{kind:"page",value:"1"}}]}),
    authorizeGeneration:async(input,options)=>{calls.push({kind:"authorize",input,key:options.idempotencyKey});return {step_up_authorization_id:null,expires_at:"2099-01-01T00:00:00Z",generation_fingerprint:"a".repeat(64)};},
    createGeneration:async(input,options)=>{calls.push({kind:"generate",input,key:options.idempotencyKey});return {job_id:"job-default",status:"queued",output_type:"evidence_report"};},
    getGenerationJob:async()=>({job_id:"job-default",status:"unavailable",output_type:"evidence_report",error_code:"STUDIO_OUTPUT_UNAVAILABLE",next_actions:["필요한 자료를 확인해 주세요."]}),
  }),{outputRoot:tmpdir()});
  const button=text=>findElements(workspace.container,n=>n.tagName==="BUTTON"&&n.textContent.trim()===text)[0];
  const set=async(label,value)=>{
    const field=findElements(workspace.container,n=>n.tagName==="LABEL"&&n.textContent.startsWith(label))[0];
    const control=findElements(field,n=>n.tagName==="INPUT"||n.tagName==="SELECT")[0];
    await workspace.act(async()=>s18Props(control).onChange({currentTarget:{value},target:{value}}));
  };
  try{
    assert.equal(calls.length,0);
    const type=findElements(workspace.container,n=>n.tagName==="BUTTON"&&n.textContent.includes("근거 기반 보고서"))[0];
    await workspace.act(async()=>type.dispatchEvent(new MinimalEvent("click")));
    for(const [label,value] of [["목적","근거 정리"],["독자","검토자"],["분량","short"],["구성","summary-body-conclusion"],["출력 형식","pdf"],["검토 조건","review_required"]])await set(label,value);
    await workspace.act(async()=>button("설정 확인").dispatchEvent(new MinimalEvent("click")));
    const form=findElements(workspace.container,n=>n.tagName==="FORM"&&n.getAttribute("class")==="studio-config-form")[0];
    await workspace.act(async()=>{s18Props(form).onSubmit({preventDefault(){}});s18Props(form).onSubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));});
    assert.deepEqual(calls.map(call=>call.kind),["save","authorize","generate"]);
    assert.equal(calls[0].input.expected_version,0);assert.equal(calls[0].input.model_id,"uss/solar-pro4");
    assert.equal(calls[1].input.model_settings_version,1);assert.equal(calls[2].input.model_settings_version,1);
    assert.equal(calls[1].key,calls[2].key);
  }finally{await workspace.cleanup();}
});

test("D 기본값이 있어도 무Source 사실 안내는 설정 저장과 모델 실행을 요구하지 않는다",async()=>{
  let saves=0,posts=0;const inputs=[];
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({getModelSettings:async()=>defaultModelView(),listSources:async()=>[],listKnowledgePackages:async()=>[],
    saveModelSettings:async()=>{saves++;throw Error("UNEXPECTED_SAVE");},askQuestion:async input=>{posts++;inputs.push(input);return {kind:"system_notice",turn_id:"turn-default-no-source",answer:"자료를 선택해 주세요.",grounding_status:"no_knowledge",insufficient:true,citations:[],next_actions:["자료를 선택해 주세요."]};}}),{outputRoot:tmpdir()});
  try{
    await s18Type(workspace,"계약 기간을 확인해 주세요");await workspace.act(async()=>{await s18Submit(workspace);});
    assert.equal(saves,0);assert.equal(posts,1);assert.equal(Object.hasOwn(inputs[0],"modelSettingsVersion"),false);
    assert.equal(findElements(workspace.container,n=>n.tagName==="TEXTAREA")[0].value,"");
  }finally{await workspace.cleanup();}
});

test("D 입력 정리는 trim 전 제출 원문과 다르게 바뀐 공백도 보존한다",async()=>{
  const pending=deferred();let submission;
  const workspace=await mountS17ReconnectWorkspace(s18Adapter({askQuestion:()=>pending.promise}),{outputRoot:tmpdir()});
  try{
    const textarea=await s18Type(workspace,"  안녕  ");await workspace.act(async()=>{submission=s18Submit(workspace);});
    await s18Type(workspace,"안녕 ");await workspace.act(async()=>{pending.resolve(s18Answer("인사 답변"));await submission;});
    assert.equal(textarea.value,"안녕 ");assert.match(workspace.container.textContent,/인사 답변/u);
  }finally{pending.resolve(s18Answer());await workspace.cleanup();}
});
