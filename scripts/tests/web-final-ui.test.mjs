import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { createProductStudioState, selectOutputType } from "../../packages/ui/src/product-studio-model.js";
import { createProductWorkspaceState } from "../../packages/ui/src/product-workspace-model.js";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";
import { getWorkspaceOutputVersionSettings, saveWorkspaceOutputVersionSettings } from '../../apps/web/lib/product-workspace-api.js';
import { listWorkspaceSyncOperations, approveWorkspaceSyncOperation } from '../../apps/web/lib/sync-approval-settings-api.js';

const PROVIDERS = ["CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI", "OPENROUTER", "ANTHROPIC", "OLLAMA", "OMNIROUTE", "EOUL_GATEWAY"];

async function bundle(entry, fileName, output) {
  const root = path.resolve(import.meta.dirname, "../..");
  const { build } = await import("vite");
  await build({
    configFile: false,
    logLevel: "silent",
    root,
    build: {
      outDir: output,
      emptyOutDir: false,
      lib: { entry: path.join(root, entry), formats: ["es"], fileName },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client", "react-dom/server"] },
    },
  });
  const built = (await readdir(output)).find((name) => name.startsWith(fileName) && /\.m?js$/u.test(name));
  return import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
}

test("Web Workspace는 단일 App Bar와 3면 최종형을 렌더하고 상시 설명·내부 정책 코드를 숨긴다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-shell-react-"));
  try {
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { ProductWorkspaceShell } = await bundle("packages/ui/src/product-workspace-shell.jsx", "web-final-shell", output);
    const state = {
      ...createProductWorkspaceState({ status: "ready" }),
      sources: [{ sourceId: "source-1", sourceVersionId: "version-1", filename: "운영 기준.pdf", ready: true }],
      selectedSource: { sourceId: "source-1", sourceVersionId: "version-1", filename: "운영 기준.pdf", ready: true },
      answer: { run_id: "run-1", run_result_id: "result-1", answer: "근거가 결속된 답변입니다.", insufficient: false, citations: [] },
    };
    const html = renderToStaticMarkup(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state, adapter: {} }));
    assert.match(html, /class="[^"]*workspace-app-bar[^"]*"/u);
    assert.match(html, /class="workspace-panes[^"]*"[^>]*data-layout="source-conversation-studio"/u);
    assert.match(html, /class="source-list-row/u);
    assert.match(html, /class="conversation-transcript/u);
    assert.match(html, /class="conversation-composer/u);
    assert.match(html, /설정/u);
    assert.doesNotMatch(html, /visible-state|WORKSPACE_POLICY|WEIGHT_PROFILE|RULESET_BINDING/u);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("보고서 Tile 선택은 설정 View를 열고 3열 Grid·Library·상태 배타성을 유지한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-studio-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProductStudioPane } = await bundle("packages/ui/src/product-studio-pane.jsx", "web-final-studio", output);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => {
      reactRoot.render(createElement(ProductStudioPane, {
        adapter: null,
        state: createProductStudioState({ status: "ready", grounded: { sourceId: "source-1", sourceVersionId: "version-1", runId: "run-1", runResultId: "result-1" } }),
      }));
    });
    assert.equal(findElements(container, (node) => node.getAttribute?.("data-studio-view") === "config").length, 0);
    const grid = findElements(container, (node) => node.getAttribute?.("data-columns") === "3")[0];
    assert.ok(grid, "Studio 생성 유형은 3열 Grid여야 한다");
    for (const label of ["슬라이드", "인포그래픽", "플래시카드", "퀴즈", "AI 오디오", "동영상"]) assert.match(container.textContent, new RegExp(label, "u"));
    await act(async () => { buttonByText(container, "근거 기반 보고서").dispatchEvent(new MinimalEvent("click")); });
    const config = findElements(container, (node) => node.getAttribute?.("data-studio-view") === "config")[0];
    assert.ok(config);
    for (const label of ["목적", "독자", "분량", "구성", "출력 형식", "검토 조건", "현재 모델", "정책 요약", "뒤로", "설정 확인", "생성"]) {
      assert.match(config.textContent, new RegExp(label, "u"));
    }
    assert.equal(findElements(container, (node) => node.getAttribute?.("data-generation-state") === "idle").length, 1);
    assert.equal(findElements(container, (node) => ["pending", "failed", "completed"].includes(node.getAttribute?.("data-generation-state"))).length, 0);
    assert.match(container.textContent, /저장된 산출물/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("설정 anchored menu에서 LLM 설정 accessible modal을 열고 승인된 Provider 유형을 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-settings-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProductWorkspaceShell } = await bundle("packages/ui/src/product-workspace-shell.jsx", "web-final-settings", output);
    const providerSettings = createElement("div", { className: "provider-card-grid" }, PROVIDERS.map((provider) => createElement("button", { key: provider, type: "button" }, provider)));
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state: createProductWorkspaceState({ status: "ready" }), adapter: {}, providerSettings })); });
    const settingsButton = buttonByText(container, "설정");
    await act(async () => { settingsButton.dispatchEvent(new MinimalEvent("click")); });
    assert.match(container.textContent, /LLM 설정/u);
    const llmSettings = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("LLM 설정"))[0];
    await act(async () => { llmSettings.dispatchEvent(new MinimalEvent("click")); });
    const dialog = findElements(container, (node) => node.getAttribute?.("role") === "dialog" && node.getAttribute?.("aria-modal") === "true")[0];
    assert.ok(dialog);
    for (const provider of PROVIDERS) assert.match(dialog.textContent, new RegExp(provider, "u"));
    assert.ok(findElements(container, (node) => node.getAttribute?.("inert") !== null).length >= 1);
    const focusables = findElements(dialog, (node) => node.tagName === "BUTTON" && !node.disabled);
    dialog.querySelectorAll = () => focusables;
    focusables.at(-1).focus();
    const tab = Object.assign(new MinimalEvent("keydown"), { key: "Tab", shiftKey: false });
    await act(async () => { dialog.dispatchEvent(tab); });
    assert.equal(dom.document.activeElement, focusables[0]);
    assert.equal(tab.defaultPrevented, true);
    focusables[0].focus();
    const shiftTab = Object.assign(new MinimalEvent("keydown"), { key: "Tab", shiftKey: true });
    await act(async () => { dialog.dispatchEvent(shiftTab); });
    assert.equal(dom.document.activeElement, focusables.at(-1));
    assert.equal(shiftTab.defaultPrevented, true);
    const escape = Object.assign(new MinimalEvent("keydown"), { key: "Escape", shiftKey: false });
    await act(async () => { dialog.dispatchEvent(escape); await Promise.resolve(); });
    assert.equal(findElements(container, (node) => node.getAttribute?.("role") === "dialog").length, 0);
    assert.equal(dom.document.activeElement, settingsButton);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("운영상태 Popup은 Provider·API·Storage·Sync·Queue와 안전 복구 동작을 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-operations-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProductWorkspaceShell } = await bundle("packages/ui/src/product-workspace-shell.jsx", "web-operations", output);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    const adapter = {
      getOperationsStatus: async () => ({
        workspace_id: "workspace-1", overall_status: "warning", checked_at: "2026-08-15T00:00:00Z",
        components: [
          { component_id: "provider", status: "ready", safe_code: "PROVIDER_READY", pending_count: 0, recovery_action: "none" },
          { component_id: "api", status: "ready", safe_code: "API_READY", pending_count: 0, recovery_action: "none" },
          { component_id: "storage", status: "ready", safe_code: "STORAGE_READY", pending_count: 0, recovery_action: "none" },
          { component_id: "sync", status: "warning", safe_code: "SYNC_PENDING", pending_count: 2, recovery_action: "open_sync_settings" },
          { component_id: "queue", status: "warning", safe_code: "QUEUE_ATTENTION_REQUIRED", pending_count: 3, recovery_action: "refresh_status" },
        ],
      }),
    };
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state: createProductWorkspaceState({ status: "ready" }), adapter })); });
    await act(async () => { buttonByText(container, "운영상태").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    for (const label of ["Provider", "API", "Storage", "Sync", "Queue", "동기화 설정 열기", "상태 새로고침"]) {
      assert.match(container.textContent, new RegExp(label, "u"));
    }
    assert.doesNotMatch(container.textContent, /postgres|localhost|http:\/\//iu);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

const s28Defaults={evidence_report:'pdf',compliance_checklist:'xlsx',comparison_table:'xlsx',knowledge_map:'json',business_draft:'docx'};
function s28Settings(version=0,formats=s28Defaults,workspace='workspace-1') {
  return {workspace_id:workspace,default_formats:{...formats},version_save_mode:'append_only',version,etag:`"output-version-settings:${workspace}:${version}"`};
}
function s28Deferred() {let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function s28Props(node) {return node[Object.keys(node).find(key=>key.startsWith('__reactProps'))];}
async function withS28Output(adapter,run) {
  const root=path.resolve(import.meta.dirname,'../..'),output=await mkdtemp(path.join(root,'.web-S28-output-'));
  const dom=installMinimalDom();let reactRoot,unmounted=false;
  try {
    const {createElement,act}=await import('react'),{createRoot}=await import('react-dom/client');
    const {ProductWorkspaceShell}=await bundle('packages/ui/src/product-workspace-shell.jsx','web-S28-output',output);
    const container=dom.document.createElement('div');dom.document.body.appendChild(container);reactRoot=createRoot(container);
    const render=(next=adapter,workspaceId='workspace-1')=>act(async()=>reactRoot.render(createElement(ProductWorkspaceShell,{workspaceId,state:createProductWorkspaceState({status:'ready'}),adapter:next})));
    await render();
    const dialog=()=>findElements(container,node=>node.getAttribute?.('role')==='dialog')[0];
    const click=(label,scope=container)=>act(async()=>{const button=buttonByText(scope,label);assert.ok(button,label);button.dispatchEvent(new MinimalEvent('click'));});
    const open=async()=>{await click('설정');const item=findElements(container,node=>node.tagName==='BUTTON'&&node.getAttribute?.('role')==='menuitem'&&node.textContent.includes('출력·버전'))[0];assert.ok(item);await act(async()=>item.dispatchEvent(new MinimalEvent('click')));};
    const close=()=>act(async()=>{const button=findElements(dialog(),node=>node.tagName==='BUTTON'&&node.getAttribute?.('aria-label')==='닫기')[0];assert.ok(button);button.dispatchEvent(new MinimalEvent('click'));});
    const field=(index=0)=>findElements(dialog(),node=>node.tagName==='SELECT')[index];
    const edit=(value,index=0)=>act(async()=>{const select=field(index);select.value=value;select.dispatchEvent(new MinimalEvent('change'));});
    const unmount=async()=>{await act(async()=>reactRoot.unmount());unmounted=true;};
    await run({container,act,render,dialog,click,open,close,field,edit,unmount});
  } finally {if(reactRoot&&!unmounted){const {act}=await import('react');await act(async()=>reactRoot.unmount());}dom.restore();await rm(output,{recursive:true,force:true});}
}

test('S28 old load success or failure cannot replace edited reentry draft dirty state or current status',async t=>{
  for(const outcome of ['success','failure'])await t.test(outcome,async()=>{
    const first=s28Deferred();let reads=0;
    await withS28Output({getOutputVersionSettings:()=>++reads===1?first.promise:Promise.resolve(s28Settings(2)),saveOutputVersionSettings:async()=>s28Settings(3)},async view=>{
      await view.open();await view.close();await view.open();assert.equal(reads,2);
      await view.edit('docx');assert.equal(buttonByText(view.dialog(),'저장').disabled,false);
      try {await view.act(async()=>outcome==='success'?first.resolve(s28Settings()):first.reject(new Error('private old read canary')));
        assert.equal(view.field().value,'docx');assert.equal(buttonByText(view.dialog(),'저장').disabled,false);
        assert.equal(findElements(view.dialog(),node=>node.getAttribute?.('role')==='alert').length,0);
      } finally {first.resolve(s28Settings());}
    });
  });
});

test('S28 close guard saving is single flight and pending buttons cannot discard or close the active write',async()=>{
  const save=s28Deferred();let writes=0;
  await withS28Output({getOutputVersionSettings:async()=>s28Settings(),saveOutputVersionSettings:async()=>{writes++;return save.promise;}},async view=>{
    await view.open();await view.edit('docx');await view.close();
    const guard=findElements(view.dialog(),node=>node.getAttribute?.('role')==='alertdialog')[0];assert.ok(guard);
    const handler=s28Props(buttonByText(guard,'저장')).onClick;
    try {await view.act(async()=>{handler();handler();});assert.equal(writes,1);
      for(const node of findElements(guard,node=>node.tagName==='BUTTON'))assert.equal(node.disabled,true);
      assert.equal(findElements(view.dialog(),node=>node.getAttribute?.('aria-label')==='닫기')[0].disabled,true);
      await view.click('버리기',guard);
      assert.ok(view.dialog());assert.equal(view.field().value,'docx');
    } finally {await view.act(async()=>save.resolve(s28Settings(1,{...s28Defaults,evidence_report:'docx'})));}
    assert.equal(view.dialog(),undefined);assert.equal(writes,1);
  });
});

test('S28 an old closeAfter write cannot close or overwrite a newly opened edited output dialog',async()=>{
  const save=s28Deferred();let reads=0,writes=0;
  await withS28Output({getOutputVersionSettings:async()=>s28Settings(reads++),saveOutputVersionSettings:async()=>{writes++;return save.promise;}},async view=>{
    await view.open();await view.edit('docx');await view.close();
    const guard=findElements(view.dialog(),node=>node.getAttribute?.('role')==='alertdialog')[0];
    await view.click('저장',guard);assert.equal(writes,1);
    // Re-entry through the existing menu creates a new dialog generation.
    await view.open();await view.edit('pdf');
    try {await view.act(async()=>save.resolve(s28Settings(1,{...s28Defaults,evidence_report:'docx'})));
      assert.ok(view.dialog(),'old closeAfter may not close the new dialog');
      assert.equal(view.field().value,'pdf');assert.equal(buttonByText(view.dialog(),'저장').disabled,false);
    } finally {save.resolve(s28Settings(1));}
  });
});

test('S28 response loss or invalid save response preserves edits and never claims the write did not happen',async t=>{
  for(const code of ['OUTPUT_VERSION_SETTINGS_UNAVAILABLE','OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID'])await t.test(code,async()=>{
    let writes=0;
    await withS28Output({getOutputVersionSettings:async()=>s28Settings(),saveOutputVersionSettings:async()=>{writes++;throw new Error(code);}},async view=>{
      await view.open();await view.edit('docx');await view.click('저장',view.dialog());
      assert.equal(writes,1);assert.equal(view.field().value,'docx');assert.equal(buttonByText(view.dialog(),'저장').disabled,false);
      assert.doesNotMatch(view.dialog().textContent,/저장되지 않았습니다/);
      assert.match(view.dialog().textContent,/저장 여부|저장 상태/);
    });
  });
});

test('S28 deferred load loses ownership on adapter Workspace replacement or unmount without clearing a newer pending dialog',async t=>{
  for(const boundary of ['adapter','workspace','unmount'])await t.test(boundary,async()=>{
    const old=s28Deferred(),next=s28Deferred();const signals=[];let reads=0;
    const adapter={getOutputVersionSettings:options=>{signals.push(options.signal);return ++reads===1?old.promise:next.promise;}};
    await withS28Output(adapter,async view=>{
      try {
        await view.open();assert.equal(reads,1);
        if(boundary==='unmount') {
          await view.unmount();assert.equal(signals[0].aborted,true);
          await view.act(async()=>old.reject(new Error('private old load')));
          assert.equal(view.container.textContent,'');return;
        }
        const replacement=boundary==='adapter'?{getOutputVersionSettings:options=>{signals.push(options.signal);return next.promise;}}:adapter;
        await view.render(replacement,boundary==='workspace'?'workspace-2':'workspace-1');await view.open();
        assert.equal(signals[0].aborted,true);
        await view.act(async()=>old.resolve(s28Settings()));
        assert.match(view.dialog().textContent,/출력 설정을 불러오고 있습니다/);
        assert.equal(findElements(view.dialog(),node=>node.tagName==='SELECT').length,0);
        assert.equal(signals[1].aborted,false);
        await view.act(async()=>next.resolve(s28Settings(2,{...s28Defaults,evidence_report:'docx'},boundary==='workspace'?'workspace-2':'workspace-1')));
        assert.deepEqual(view.field().options.filter(option=>option.selected).map(option=>option.value),['docx']);assert.equal(buttonByText(view.dialog(),'저장').disabled,true);
      } finally {old.resolve(s28Settings());next.resolve(s28Settings(2));}
    });
  });
});

test('S28 scope replacement without reopening removes the old draft and cannot send its save to the previous adapter',async t=>{
  for(const boundary of ['adapter','workspace'])await t.test(boundary,async()=>{
    let oldWrites=0,newWrites=0;
    const adapter={getOutputVersionSettings:async()=>s28Settings(),saveOutputVersionSettings:async()=>{oldWrites++;return s28Settings(1);}};
    const replacement={getOutputVersionSettings:async()=>s28Settings(),saveOutputVersionSettings:async()=>{newWrites++;return s28Settings(1);}};
    await withS28Output(adapter,async view=>{
      await view.open();await view.edit('docx');
      const oldForm=findElements(view.dialog(),node=>node.tagName==='FORM')[0];
      await view.render(boundary==='adapter'?replacement:adapter,boundary==='workspace'?'workspace-2':'workspace-1');
      assert.equal(view.dialog(),undefined,'scope change must invalidate the open output window before any reopen');
      assert.equal(findElements(view.container,node=>node.tagName==='SELECT').length,0);
      await view.act(async()=>oldForm.dispatchEvent(new MinimalEvent('submit')));
      assert.equal(oldWrites,0);assert.equal(newWrites,0);
    });
  });
});

test('S28 pending save scope replacement aborts old ownership and never leaves the next window close blocked',async t=>{
  for(const boundary of ['adapter','workspace'])await t.test(boundary,async()=>{
    const saved=s28Deferred();let oldSignal,writes=0;
    const adapter={getOutputVersionSettings:async()=>s28Settings(),saveOutputVersionSettings:async(_input,options)=>{writes++;oldSignal=options.signal;return saved.promise;}};
    const replacement={getOutputVersionSettings:async()=>s28Settings()};
    await withS28Output(adapter,async view=>{
      try {
        await view.open();await view.edit('docx');await view.click('저장',view.dialog());assert.equal(writes,1);
        await view.render(boundary==='adapter'?replacement:adapter,boundary==='workspace'?'workspace-2':'workspace-1');
        assert.equal(view.dialog(),undefined,'scope change must remove the old pending window without reopening');
        assert.equal(oldSignal.aborted,true);
        await view.act(async()=>saved.resolve(s28Settings(1,{...s28Defaults,evidence_report:'docx'})));
        assert.equal(view.dialog(),undefined);
        await view.open();await view.close();assert.equal(view.dialog(),undefined,'new window is not locked by the previous write');
        assert.equal(writes,1);
      } finally {saved.resolve(s28Settings(1));}
    });
  });
});

test('S28 current knowledge_map SVG and PDF travel through the real exact Web helper without a knowledge_graph field',async()=>{
  const requests=[];
  const fetchImpl=async(url,init)=>{
    requests.push({url,init});const body=init.body?JSON.parse(init.body):null;
    const data={workspace_id:'workspace-1',default_formats:body?.default_formats??s28Defaults,version_save_mode:'append_only',version:body?requests.filter(row=>row.init.method==='PATCH').length:0};
    return Response.json({data,meta:{trace_id:'trace-s28',workspace_id:'workspace-1'}},{headers:{ETag:`"output-version-settings:workspace-1:${data.version}"`}});
  };
  const adapter={getOutputVersionSettings:options=>getWorkspaceOutputVersionSettings('workspace-1',{...options,fetchImpl}),saveOutputVersionSettings:(input,options)=>saveWorkspaceOutputVersionSettings('workspace-1',input,{...options,fetchImpl})};
  await withS28Output(adapter,async view=>{
    await view.open();await view.edit('svg',3);await view.click('저장',view.dialog());
    const writes=requests.filter(row=>row.init.method==='PATCH');assert.equal(writes.length,1,'exact helper must accept the five canonical keys');
    const body=JSON.parse(writes[0].init.body);assert.deepEqual(Object.keys(body.default_formats),['evidence_report','compliance_checklist','comparison_table','knowledge_map','business_draft']);
    assert.equal(body.default_formats.knowledge_map,'svg');assert.equal(Object.hasOwn(body.default_formats,'knowledge_graph'),false);
    assert.deepEqual(findElements(view.field(3),node=>node.tagName==='OPTION').map(node=>node.value),['json','svg','png','pdf']);
    await view.edit('pdf',3);await view.click('저장',view.dialog());assert.equal(requests.filter(row=>row.init.method==='PATCH').length,2);
    assert.equal(view.field(3).value,'pdf');assert.ok(requests.every(row=>row.url.startsWith('/bff/api/')));
  });
});

test("출력·버전 Popup은 유형별 형식과 append-only 정책을 저장하고 미저장 닫기를 보호한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-output-settings-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProductWorkspaceShell } = await bundle("packages/ui/src/product-workspace-shell.jsx", "web-output-settings", output);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    const defaults = { evidence_report: "pdf", compliance_checklist: "xlsx", comparison_table: "xlsx", knowledge_map: "json", business_draft: "docx" };
    const saves = [];
    const adapter = {
      getOutputVersionSettings: async () => ({ workspace_id: "workspace-1", default_formats: defaults, version_save_mode: "append_only", version: 0, etag: '"output-version-settings:workspace-1:0"' }),
      saveOutputVersionSettings: async (input) => { saves.push(input); return { workspace_id: "workspace-1", default_formats: input.default_formats, version_save_mode: "append_only", version: 1, etag: '"output-version-settings:workspace-1:1"' }; },
    };
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state: createProductWorkspaceState({ status: "ready" }), adapter })); });
    await act(async () => { buttonByText(container, "설정").dispatchEvent(new MinimalEvent("click")); });
    const outputSettingsButton = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("출력·버전"))[0];
    await act(async () => { outputSettingsButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    for (const label of ["근거 기반 보고서", "제약·준수 점검표", "비교·데이터 표", "지식 구조도", "업무 문서 초안", "Append-only"]) assert.match(container.textContent, new RegExp(label, "u"));
    const selects = findElements(container, (node) => node.tagName === "SELECT");
    assert.equal(selects.length, 5);
    selects[0].value = "docx";
    await act(async () => { selects[0].dispatchEvent(new MinimalEvent("change")); });
    assert.equal(buttonByText(container, "저장").disabled, false);
    await act(async () => { buttonByText(container, "저장").dispatchEvent(new MinimalEvent("click")); await new Promise((resolve) => setTimeout(resolve, 0)); });
    assert.equal(saves.length, 1);
    assert.equal(saves[0].default_formats.evidence_report, "docx");
    selects[0].value = "pdf";
    await act(async () => { selects[0].dispatchEvent(new MinimalEvent("change")); });
    await act(async () => { findElements(container, (node) => node.tagName === "BUTTON" && node.getAttribute?.("aria-label") === "닫기")[0].dispatchEvent(new MinimalEvent("click")); });
    assert.match(container.textContent, /저장하지 않은 변경/u);
    await act(async () => { buttonByText(container, "계속 편집").dispatchEvent(new MinimalEvent("click")); });
    assert.doesNotMatch(container.textContent, /저장하지 않은 변경/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

function s29Operation(overrides={}) {
  return {operation_id:'sync-operation-s29',tenant_id:'tenant-1',workspace_id:'workspace-1',actor_id:'actor-1',target_area:'cloud_sync',state:'awaiting_approval',version:1,manifest_digest:'a'.repeat(64),item_ids:['item-s29-a','item-s29-b'],approved_item_ids:[],completed_item_ids:[],batches:[],conflicts:[],target_versions:[],reindex_state:null,source_mutations:0,overwrite_count:0,...overrides};
}
async function withS29Sync(adapter,run) {
  const output=await mkdtemp(path.join(path.resolve(import.meta.dirname,'../..'),'.web-S29-sync-'));
  const dom=installMinimalDom();let reactRoot,unmounted=false;
  try {
    const {createElement,act}=await import('react'),{createRoot}=await import('react-dom/client');
    const {ProductWorkspaceShell}=await bundle('packages/ui/src/product-workspace-shell.jsx','web-S29-sync',output);
    const container=dom.document.createElement('div');dom.document.body.appendChild(container);reactRoot=createRoot(container);
    const render=(next=adapter,workspaceId='workspace-1')=>act(async()=>reactRoot.render(createElement(ProductWorkspaceShell,{workspaceId,state:createProductWorkspaceState({status:'ready'}),adapter:next})));
    await render();
    const dialog=()=>findElements(container,node=>node.getAttribute?.('role')==='dialog')[0];
    const click=(label,scope=container)=>act(async()=>{const button=buttonByText(scope,label);assert.ok(button,label);button.dispatchEvent(new MinimalEvent('click'));});
    const open=async()=>{await click('설정');const item=findElements(container,node=>node.getAttribute?.('role')==='menuitem'&&node.textContent.includes('동기화·승인'))[0];assert.ok(item);await act(async()=>item.dispatchEvent(new MinimalEvent('click')));};
    const close=()=>act(async()=>{const button=findElements(dialog(),node=>node.getAttribute?.('aria-label')==='닫기')[0];assert.ok(button);button.dispatchEvent(new MinimalEvent('click'));});
    const password=()=>findElements(dialog(),node=>node.tagName==='INPUT'&&node.type==='password')[0];
    const checkboxes=()=>findElements(dialog(),node=>node.tagName==='INPUT'&&node.type==='checkbox');
    const deselect=async(index)=>{const checkbox=checkboxes()[index];await act(async()=>{checkbox.checked=false;checkbox.dispatchEvent(new MinimalEvent('click'));});};
    const unmount=async()=>{await act(async()=>reactRoot.unmount());unmounted=true;};
    await run({container,act,render,dialog,click,open,close,password,checkboxes,deselect,unmount});
  } finally {if(reactRoot&&!unmounted){const {act}=await import('react');await act(async()=>reactRoot.unmount());}dom.restore();await rm(output,{recursive:true,force:true});}
}

test('S29 old list success or failure cannot replace reentry selections or current status',async t=>{
  for(const outcome of ['success','failure'])await t.test(outcome,async()=>{
    const old=s28Deferred();let reads=0;const approved=[];
    const adapter={listSyncOperations:()=>++reads===1?old.promise:Promise.resolve([s29Operation()]),issueStudioStepUp:async()=> 'step-up-s29',approveSyncOperation:async(operation,input)=>{approved.push(input.approvedItemIds);return {...operation,state:'approved',version:2,approved_item_ids:input.approvedItemIds};}};
    await withS29Sync(adapter,async view=>{
      try {
        await view.open();await view.close();await view.open();await view.deselect(1);
        assert.deepEqual(view.checkboxes().map(node=>node.checked),[true,false]);
        await view.act(async()=>outcome==='success'?old.resolve([s29Operation()]):old.reject(new Error('private late list')));
        assert.deepEqual(view.checkboxes().map(node=>node.checked),[true,false]);
        assert.equal(findElements(view.dialog(),node=>node.getAttribute?.('role')==='alert').length,0);
        view.password().value='synthetic-password';await view.click('선택 항목 승인',view.dialog());
        assert.deepEqual(approved,[['item-s29-a']]);
      } finally {await view.act(async()=>old.resolve([]));}
    });
  });
});

test('S29 synchronous duplicate approval authenticates and submits the selected snapshot exactly once through the real Web helper',async()=>{
  const auth=s28Deferred();const requests=[];let authorizations=0,authorizationKey;
  const fetchImpl=async(url,init)=>{requests.push({url,init});const input=init.body?JSON.parse(init.body):null;return Response.json({data:input?s29Operation({state:'approved',version:2,approved_item_ids:input.approved_item_ids}):{operations:[s29Operation()]},meta:{trace_id:'trace-s29'}});};
  const adapter={listSyncOperations:options=>listWorkspaceSyncOperations('workspace-1',{...options,fetchImpl}),issueStudioStepUp:async(group,target,password,options)=>{authorizations++;authorizationKey=options.idempotencyKey;assert.equal(group,'data_area_move');assert.equal(target,'sync-operation-s29');assert.equal(password,'synthetic-password');assert.ok(options.signal);return auth.promise;},approveSyncOperation:(operation,input,options)=>approveWorkspaceSyncOperation('workspace-1',operation,input,{...options,fetchImpl})};
  await withS29Sync(adapter,async view=>{
    try {
      await view.open();await view.deselect(1);const password=view.password();password.value='synthetic-password';
      // Captured props are used only to reproduce two handler calls in one React tick.
      const handler=s28Props(buttonByText(view.dialog(),'선택 항목 승인')).onClick;
      await view.act(async()=>{handler();handler();});assert.equal(authorizations,1);
      assert.equal(buttonByText(view.dialog(),'선택 항목 승인').disabled,true);
      await view.act(async()=>auth.resolve('step-up-s29'));
      const writes=requests.filter(row=>row.init.method==='POST');assert.equal(writes.length,1);
      assert.deepEqual(JSON.parse(writes[0].init.body),{approved_item_ids:['item-s29-a'],step_up_authorization_id:'step-up-s29'});
      assert.equal(writes[0].init.headers['If-Match'],'"sync:sync-operation-s29:1"');
      assert.match(writes[0].init.headers['Idempotency-Key'],/^[0-9a-f-]{36}$/);
      assert.equal(writes[0].init.headers['Idempotency-Key'],authorizationKey);
      assert.equal(password.value,'');assert.match(view.dialog().textContent,/승인됨/);
      assert.ok(requests.every(row=>row.url.startsWith('/bff/api/')&&!row.url.includes('transfer-batches')));
    } finally {await view.act(async()=>auth.resolve('step-up-s29'));}
  });
});

test('S29 closing during Step-up prevents a later authorization from submitting and preserves the new dialog password',async t=>{
  for(const outcome of ['success','failure'])await t.test(outcome,async()=>{
    const auth=s28Deferred();let approvals=0;let signal;
    const adapter={listSyncOperations:async()=>[s29Operation()],issueStudioStepUp:async(_group,_target,_password,options)=>{signal=options.signal;return auth.promise;},approveSyncOperation:async()=>{approvals++;return s29Operation({state:'approved',version:2});}};
    await withS29Sync(adapter,async view=>{
      try {
        await view.open();const oldPassword=view.password();oldPassword.value='synthetic-old';await view.click('선택 항목 승인',view.dialog());await view.close();
        assert.equal(oldPassword.value,'');assert.equal(signal.aborted,true);
        await view.open();view.password().value='synthetic-new';
        await view.act(async()=>outcome==='success'?auth.resolve('step-up-s29'):auth.reject(new Error('private late authentication')));
        assert.equal(approvals,0);assert.equal(view.password().value,'synthetic-new');
        assert.equal(buttonByText(view.dialog(),'선택 항목 승인').disabled,false);
        assert.equal(findElements(view.dialog(),node=>node.getAttribute?.('role')==='alert').length,0);
      } finally {await view.act(async()=>auth.resolve('step-up-s29'));}
    });
  });
});

test('S29 late approval response never clears a newer list pending or changes its operations',async t=>{
  for(const outcome of ['success','failure'])await t.test(outcome,async()=>{
    const approval=s28Deferred(),next=s28Deferred();let reads=0;
    const adapter={listSyncOperations:()=>++reads===1?Promise.resolve([s29Operation()]):next.promise,issueStudioStepUp:async()=> 'step-up-s29',approveSyncOperation:()=>approval.promise};
    await withS29Sync(adapter,async view=>{
      try {
        await view.open();view.password().value='synthetic-password';await view.click('선택 항목 승인',view.dialog());await view.close();await view.open();
        await view.act(async()=>outcome==='success'?approval.resolve(s29Operation({state:'approved',version:2})):approval.reject(new Error('private old approval')));
        assert.match(view.dialog().textContent,/동기화 상태를 불러오고 있습니다/);
        assert.equal(buttonByText(view.dialog(),'새로고침').disabled,true);
        assert.equal(findElements(view.dialog(),node=>node.getAttribute?.('role')==='alert').length,0);
        await view.act(async()=>next.resolve([s29Operation({operation_id:'sync-operation-new'})]));
        assert.doesNotMatch(view.dialog().textContent,/승인됨/);assert.equal(buttonByText(view.dialog(),'선택 항목 승인').disabled,false);
      } finally {await view.act(async()=>{approval.resolve(s29Operation({state:'approved',version:2}));next.resolve([]);});}
    });
  });
});

test('S29 approval response loss reports uncertainty and does not claim approval or transfer never ran',async()=>{
  let writes=0;
  await withS29Sync({listSyncOperations:async()=>[s29Operation()],issueStudioStepUp:async()=> 'step-up-s29',approveSyncOperation:async()=>{writes++;throw new Error('SYNC_SETTINGS_RESPONSE_INVALID');}},async view=>{
    await view.open();const password=view.password();password.value='synthetic-password';await view.click('선택 항목 승인',view.dialog());
    const alert=findElements(view.dialog(),node=>node.getAttribute?.('role')==='alert')[0];assert.ok(alert);assert.equal(writes,1);
    assert.doesNotMatch(alert.textContent,/실행되지 않았|저장되지 않았|private/);
    assert.match(alert.textContent,/승인 결과.*확인할 수 없/);assert.match(alert.textContent,/전송.*별도/);
    assert.equal(password.value,'');assert.deepEqual(view.checkboxes().map(node=>node.checked),[true,true]);
  });
});

test('S29 adapter Workspace or unmount during authentication invalidates approval ownership',async t=>{
  for(const boundary of ['adapter','workspace','unmount'])await t.test(boundary,async()=>{
    const auth=s28Deferred();let approvals=0,signal;
    const adapter={listSyncOperations:async()=>[s29Operation()],issueStudioStepUp:async(_g,_t,_p,options)=>{signal=options.signal;return auth.promise;},approveSyncOperation:async()=>{approvals++;return s29Operation();}};
    await withS29Sync(adapter,async view=>{
      try {
        await view.open();const password=view.password();password.value='synthetic-password';await view.click('선택 항목 승인',view.dialog());
        if(boundary==='unmount')await view.unmount();else await view.render(boundary==='adapter'?{...adapter}:adapter,boundary==='workspace'?'workspace-2':'workspace-1');
        assert.equal(password.value,'');assert.equal(signal.aborted,true);
        await view.act(async()=>auth.resolve('step-up-s29'));assert.equal(approvals,0);assert.equal(view.dialog(),undefined);
      } finally {await view.act(async()=>auth.resolve('step-up-s29'));}
    });
  });
});

test("동기화·승인 Popup은 Preview 항목을 표시하고 Step-up 뒤 선택 항목만 승인한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-sync-settings-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProductWorkspaceShell } = await bundle("packages/ui/src/product-workspace-shell.jsx", "web-sync-settings", output);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    const operation = {
      operation_id: "sync-operation-1", tenant_id: "tenant-1", workspace_id: "workspace-1", actor_id: "actor-1",
      target_area: "cloud_sync", state: "awaiting_approval", version: 1, manifest_digest: "a".repeat(64),
      item_ids: ["item-source-1", "item-output-1"], approved_item_ids: [], completed_item_ids: [],
      batches: [], conflicts: [], target_versions: [], reindex_state: null, source_mutations: 0, overwrite_count: 0,
    };
    const calls = [];
    const adapter = {
      listSyncOperations: async () => [operation],
      issueStudioStepUp: async (group, target, password) => { calls.push([group, target, password]); return "step-up-sync-1"; },
      approveSyncOperation: async (_operation, input) => { calls.push(input); return { ...operation, state: "approved", version: 2, approved_item_ids: input.approvedItemIds }; },
    };
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state: createProductWorkspaceState({ status: "ready" }), adapter })); });
    await act(async () => { buttonByText(container, "설정").dispatchEvent(new MinimalEvent("click")); });
    const syncSettingsButton = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("동기화·승인"))[0];
    await act(async () => { syncSettingsButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.match(container.textContent, /Preview|item-source-1|item-output-1|자동 전송하지 않습니다/u);
    const password = findElements(container, (node) => node.tagName === "INPUT" && node.type === "password").at(-1);
    password.value = "correct-password-1";
    await act(async () => { buttonByText(container, "선택 항목 승인").dispatchEvent(new MinimalEvent("click")); await new Promise((resolve) => setTimeout(resolve, 0)); });
    assert.deepEqual(calls[0], ["data_area_move", "sync-operation-1", "correct-password-1"]);
    assert.deepEqual(calls[1].approvedItemIds, ["item-source-1", "item-output-1"]);
    assert.equal(password.value, "");
    assert.match(container.textContent, /승인됨/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("조직 정책 Popup은 조직 강제 8필드와 Workspace 적용 결과를 읽기 전용으로 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-organization-policy-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProductWorkspaceShell } = await bundle("packages/ui/src/product-workspace-shell.jsx", "web-organization-policy", output);
    const organizationPolicy = {
      mode: "allow_approved_external", allowed_provider_kinds: ["external_api"], allowed_destinations: ["api.example.com"],
      classification: "internal", max_bytes: 4096, masking_required: true, redaction_required: false,
      required_approver: "organization_admin",
    };
    const adapter = {
      getEgressPolicy: async () => ({
        organization_policy_version_id: "org-policy-v1", organization_binding_id: "org-binding-v1",
        workspace_policy_version_id: "workspace-policy-v1", workspace_binding_id: "workspace-binding-v1",
        ...organizationPolicy, parent_locked: false, fingerprint: `sha256:${"a".repeat(64)}`,
        organization_etag: '"org:1"', workspace_etag: '"workspace:1"',
        organization_policy: organizationPolicy,
        workspace_policy: { ...organizationPolicy, max_bytes: 2048 },
      }),
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state: createProductWorkspaceState({ status: "ready" }), adapter })); });
    await act(async () => { buttonByText(container, "설정").dispatchEvent(new MinimalEvent("click")); });
    const policyButton = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("조직 정책"))[0];
    assert.equal(policyButton.disabled, false);
    await act(async () => { policyButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const dialog = findElements(container, (node) => node.getAttribute?.("role") === "dialog")[0];
    for (const label of ["조직 강제 정책", "승인된 외부 전송", "external_api", "api.example.com", "internal", "4,096 bytes", "마스킹 필수", "삭제 처리 선택", "조직 관리자", "Workspace 적용 결과"]) {
      assert.match(dialog.textContent, new RegExp(label, "u"));
    }
    assert.equal(findElements(dialog, (node) => ["INPUT", "SELECT", "TEXTAREA"].includes(node.tagName)).length, 0);
    assert.doesNotMatch(dialog.textContent, /org-policy-v1|org-binding-v1|sha256:|workspace:1|internal\.invalid/iu);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("생성 중·실패·완료는 동시에 노출되지 않고 안전한 사용자 상태만 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-states-react-"));
  try {
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { ProductStudioPane } = await bundle("packages/ui/src/product-studio-pane.jsx", "web-final-states", output);
    const grounded = { sourceId: "source-1", sourceVersionId: "version-1", runId: "run-1", runResultId: "result-1" };
    const base = selectOutputType(createProductStudioState({ grounded }), "evidence_report");
    const states = [
      ["pending", { ...base, pending: true }],
      ["failed", { ...base, safeError: "STUDIO_CREATE_FAILED" }],
      ["completed", { ...base, outputs: [{ studio_output_id: "output-1", output_version_id: "version-1", title: "완료 보고서", status: "draft" }], selectedOutputId: "output-1" }],
    ];
    for (const [expected, state] of states) {
      const html = renderToStaticMarkup(createElement(ProductStudioPane, { state, adapter: null }));
      assert.equal((html.match(/data-generation-state=/gu) ?? []).length, 1);
      assert.match(html, new RegExp(`data-generation-state="${expected}"`, "u"));
      assert.doesNotMatch(html, /STUDIO_CREATE_FAILED|WORKSPACE_POLICY|RULESET_BINDING/u);
    }
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("실제 Provider 설정 View는 credential-safe 초기 화면과 capability 기본값 구조를 렌더한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-provider-react-"));
  try {
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { ProviderSettingsWorkspace, formatModelChoice, projectProviderConnection, safeProviderErrorMessage } = await bundle("apps/web/components/provider-settings-workspace.jsx", "web-final-provider", output);
    const html = renderToStaticMarkup(createElement(ProviderSettingsWorkspace, { workspaceId: "workspace-1", embedded: true }));
    assert.match(html, /Workspace 기본 모델/u);
    assert.doesNotMatch(html, /역할 매핑 저장|api[_-]?key|secret_value|type="password"/iu);
    assert.deepEqual(projectProviderConnection({ enabled: true, configured: true, verification_status: "verified" }), { label: "활성 · Credential 설정됨 · 확인됨", verified: true });
    assert.equal(formatModelChoice({ display_name: "LAN Ollama" }, { model_id: "qwen3" }), "LAN Ollama · qwen3");
    assert.equal(safeProviderErrorMessage("provider", { code: "INTERNAL_DOCKER_HOST_api:8000" }), "Provider 연결을 저장하지 못했습니다. 다시 시도해 주세요.");
    const source = await readFile(path.join(root, "apps/web/components/provider-settings-workspace.jsx"), "utf8");
    assert.doesNotMatch(source, /\$\{error\.code/u);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("Workspace 시각 규칙은 reduced motion과 9px 이상 보조 글꼴을 유지한다", async () => {
  const css = await readFile(path.resolve(import.meta.dirname, "../../packages/ui/src/workspace.css"), "utf8");
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*animation-duration:\s*\.01ms/u);
  const finalWorkspaceCss = css.slice(css.indexOf(".adaptive-workspace[data-product-workspace-state]"));
  assert.doesNotMatch(finalWorkspaceCss, /font-size:\s*8px/u);
});
