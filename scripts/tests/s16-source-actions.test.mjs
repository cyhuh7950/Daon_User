import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";
import { createNotebookContextWorkspaceAdapter } from "../../packages/ui/src/notebook-context-adapter.js";

const repo = path.resolve(import.meta.dirname, "../..");
const source = { source_id: "src-s16-source-one", source_version_id: "sv-s16-source-one", filename: "S16-QA.txt",
  source_state: "ready", processing_state: "completed", job_state: "completed" };
const view = state => ({ request_id: "request-s16", tenant_id: "tenant-1", workspace_id: "workspace-1",
  source_id: source.source_id, state, version: 1, requested_at: "2026-10-16T00:00:00Z", grace_until: "2026-10-16T00:00:00Z",
  source_active: false, cleanup_items: ["original_content", "index", "preview", "cache", "known_local_copy", "sync_reference"]
    .map(kind => ({ kind, reference_id: `inventory-${kind}`, state: "pending", attempt_count: 0, evidence: "present" })),
  completed_references: [], source_version_mutations: 0 });
let output, Shell, actual;
test.before(async () => {
  await import("react"); await import("react-dom/client");
  output = await mkdtemp(path.join(repo, ".s16-source-actions-"));
  const { build } = await import("vite");
  await build({ configFile: false, logLevel: "silent", root: repo, build: {
    outDir: output, emptyOutDir: false,
    lib: { entry: { shell: path.join(repo, "packages/ui/src/product-workspace-shell.jsx"),
      actual: path.join(repo, "apps/web/components/actual-workspace.jsx") }, formats: ["es"], fileName: (_format, name) => `${name}.mjs` },
    rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
  } });
  Shell = (await import(pathToFileURL(path.join(output, "shell.mjs")).href)).ProductWorkspaceShell;
  actual = (await import(pathToFileURL(path.join(output, "actual.mjs")).href)).createWebProductWorkspaceAdapter;
});
test.after(async () => { if (output) await rm(output, { recursive: true, force: true }); });
const context = () => ({ notebook_id: "notebook-s16", sources: [{ source_id: source.source_id, source_version_id: source.source_version_id }],
  knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [], source_deletion_requests: [], conversation: null });
const props = node => node[Object.keys(node).find(key => key.startsWith("__reactProps$"))];
async function mount(extra = {}) {
  const dom = installMinimalDom();
  const { act, createElement } = await import("react"), { createRoot } = await import("react-dom/client");
  const base = actual("workspace-1", "notebook-s16");
  const adapter = { ...base, listSources: async () => [source], listStudioOutputs: async () => [], listProductStudioOutputs: async () => ({ outputs: [], studioLocks: [] }),
    listConnectors: async () => [], listKnowledgePackages: async () => [], getOperationsStatus: async () => null, listSyncOperations: async () => [],
    getLicense: async () => null, getEgressPolicy: async () => null,
    // 삭제/재처리 시험의 통신 계수에 무관한 Studio 조회를 섞지 않는다. 성공 근거로 사용하지 않음.
    getStudioApprovalPolicy: async () => { throw Error("STUDIO_POLICY_NOT_UNDER_TEST"); },
    listStudioApprovalNotices: async () => { throw Error("STUDIO_NOTICES_NOT_UNDER_TEST"); }, ...extra };
  const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
  const root = createRoot(container);
  const w = { container, act, cleanup: async () => { await act(async () => root.unmount()); dom.restore(); } };
  await act(async () => { root.render(createElement(Shell, { workspaceId: "workspace-1", adapter })); });
  return w;
}
async function click(w, label) {
  await w.act(async () => {
    const button = findElements(w.container, node => node.tagName === "BUTTON"
      && (node.textContent === label || node.getAttribute("aria-label") === label))[0];
    assert.ok(button, label); button.dispatchEvent(new MinimalEvent("click"));
  });
}
async function password(w, value) {
  await w.act(async () => {
    const input = findElements(w.container, node => node.tagName === "INPUT" && node.getAttribute("aria-label") === "Source 삭제 추가 인증 비밀번호")[0];
    assert.ok(input, "영구 삭제 전에 실제 추가 인증 입력이 필요하다");
    input.value = value;
    props(input).onChange?.({ target: input, currentTarget: input });
  });
}

test("R4 reprocess confirms explicit mode before submission and reports only actual configured state",async()=>{
  const calls=[];
  const w=await mount({listSources:async()=>[{...source,source_state:"waiting_model",processing_state:"failed"}],
    reprocessSource:async(s,options)=>{calls.push({s,options});return {processing_run_id:"run-r4",source_id:source.source_id,
      source_version_id:source.source_version_id,processing_mode:options.processingMode,auto_requeue_configured:false};},
    getProcessingStatus:async()=>{throw Error("synthetic polling unavailable");}});
  try{
    await click(w,"재처리");
    assert.equal(calls.length,0);
    const select=findElements(w.container,n=>n.tagName==="SELECT"&&n.getAttribute("aria-label")==="Source 재처리 Mode")[0];
    assert.ok(select);
    await w.act(async()=>props(select).onChange({target:{value:"auto"}}));
    await click(w,"설정 확인·재처리");
    assert.equal(calls.length,1);
    assert.equal(calls[0].options.processingMode,"auto");
    assert.match(w.container.textContent,/자동 재큐는 현재 비활성/u);
    assert.doesNotMatch(w.container.textContent,/자동 재처리 완료/u);
  }finally{await w.cleanup();}
});

test("R4 actual Notebook adapter preserves explicit Mode and binds the current Notebook",async()=>{
  const previous=globalThis.fetch,calls=[];
  globalThis.fetch=async(url,init)=>{
    calls.push({url,init});return Response.json({data:{id:"run-r4",kind:"processing_run",version:"2",attributes:{
      processing_run_id:"run-r4",source_id:source.source_id,source_version_id:source.source_version_id,
      retry_of_processing_run_id:"run-prior",processing_state:"vision_llm_understanding",source_state:"processing",job_state:"pending",
      replayed:false,processing_mode:"local_only",auto_requeue_configured:false}}},{status:201});
  };
  try{
    const adapter=createNotebookContextWorkspaceAdapter(actual("workspace-1","notebook-s16"),context());
    const result=await adapter.reprocessSource({sourceId:source.source_id,sourceVersionId:source.source_version_id},
      {processingMode:"local_only",idempotencyKey:"retry-r4-adapter"});
    assert.equal(calls.length,1);
    assert.equal(calls[0].url,`/bff/api/sources/${source.source_id}/processing-runs`);
    assert.deepEqual(JSON.parse(calls[0].init.body).attributes,{workspace_id:"workspace-1",notebook_id:"notebook-s16",
      source_version_id:source.source_version_id,processing_mode:"local_only"});
    assert.equal(result.auto_requeue_configured,false);
  }finally{globalThis.fetch=previous;}
});

test("R4 uncertain reprocess keeps semantic mode and idempotency key and blocks immediate duplicate",async()=>{
  const calls=[];let reject;
  const waiting=new Promise((_,no)=>{reject=no;});
  const w=await mount({listSources:async()=>[{...source,source_state:"waiting_model",processing_state:"failed"}],
    reprocessSource:async(s,options)=>{calls.push(options);if(calls.length===1)return waiting;throw Error("NETWORK_UNCERTAIN");}});
  try{
    await click(w,"재처리");
    let select=findElements(w.container,n=>n.tagName==="SELECT"&&n.getAttribute("aria-label")==="Source 재처리 Mode")[0];
    assert.ok(select);
    await w.act(async()=>props(select).onChange({target:{value:"local_only"}}));
    await w.act(async()=>{const b=findElements(w.container,n=>n.tagName==="BUTTON"&&n.textContent==="설정 확인·재처리")[0];props(b).onClick();props(b).onClick();});
    assert.equal(calls.length,1);
    await w.act(async()=>reject(Error("NETWORK_UNCERTAIN")));
    select=findElements(w.container,n=>n.tagName==="SELECT"&&n.getAttribute("aria-label")==="Source 재처리 Mode")[0];
    assert.ok(select.disabled);
    await click(w,"설정 확인·재처리");
    assert.equal(calls.length,2);
    assert.equal(calls[0].idempotencyKey,calls[1].idempotencyKey);
    assert.equal(calls[1].processingMode,"local_only");
  }finally{reject?.(Error("test cleanup"));await w.cleanup();}
});

test("S16 actual adapter는 현재 Notebook을 삭제 POST에 결속한다", async () => {
  const old = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return Response.json({ data: view("cleanup_pending"), meta: { trace_id: "trace-1" } }, { headers: { ETag: '"deletion:request-s16:1"' } }); };
  try {
    const adapter = createNotebookContextWorkspaceAdapter(actual("workspace-1"), context());
    await adapter.requestSourceDeletion({ sourceId: source.source_id }, { idempotencyKey: "s16-delete-business-key", stepUpAuthorization: "opaque-grant" });
    assert.deepEqual(JSON.parse(calls[0].init.body), { notebook_id: "notebook-s16", step_up_authorization_id: "opaque-grant" });
  } finally { globalThis.fetch = old; }
});

test("S16 React 영구 삭제는 추가 인증 후에만 접수하고 재시도는 같은 key와 새 인증을 사용한다", async () => {
  const old = globalThis.fetch, calls = [];
  let attempt = 0;
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    if (url === "/bff/api/session/step-up") return Response.json({ data: { step_up_authorization: `opaque-grant-${++attempt}` } });
    assert.equal(url, `/bff/api/sources/${source.source_id}/deletion-requests`);
    return Response.json({ data: view(attempt === 1 ? "failed" : "purged"), meta: { trace_id: "trace-1" } }, { status: 201, headers: { ETag: '"deletion:request-s16:1"' } });
  };
  const w = await mount();
  try {
    await click(w, "S16-QA.txt 작업"); await click(w, "Source 삭제 요청");
    assert.equal(calls.length, 0);
    await password(w, "fixture-confirmation-not-real"); await click(w, "삭제 요청 확인");
    assert.match(w.container.textContent, /정리가 완료되지 않았/u);
    assert.equal(findElements(w.container, n => n.tagName === "BUTTON" && n.textContent === "삭제 요청 취소").length, 0);
    assert.equal(findElements(w.container, n => n.tagName === "INPUT" && n.value === "fixture-confirmation-not-real").length, 0);
    await password(w, "fixture-confirmation-second"); await click(w, "삭제 정리 재시도");
    const deletes = calls.filter(c => c.url.includes("deletion-requests"));
    assert.equal(deletes.length, 2);
    assert.equal(deletes[0].init.headers["Idempotency-Key"], deletes[1].init.headers["Idempotency-Key"]);
    assert.deepEqual(deletes.map(c => JSON.parse(c.init.body)), [
      { notebook_id: "notebook-s16", step_up_authorization_id: "opaque-grant-1" },
      { notebook_id: "notebook-s16", step_up_authorization_id: "opaque-grant-2" },
    ]);
    assert.match(w.container.textContent, /실제 정리가 완료/u);
  } finally { await w.cleanup(); globalThis.fetch = old; }
});

test("S16 삭제 확인 취소는 비밀번호를 폐기하고 네트워크를 호출하지 않는다", async () => {
  let calls = 0;
  const w = await mount({ issueSourceDeletionStepUp: async () => { calls++; throw Error("no network"); } });
  try {
    await click(w, "S16-QA.txt 작업"); await click(w, "Source 삭제 요청");
    await password(w, "fixture-password-discard"); await click(w, "취소");
    await click(w, "S16-QA.txt 작업"); await click(w, "Source 삭제 요청");
    const inputs = findElements(w.container, n => n.tagName === "INPUT" && n.getAttribute("aria-label") === "Source 삭제 추가 인증 비밀번호");
    assert.equal(inputs.length, 1); assert.equal(inputs[0].value ?? "", ""); assert.equal(calls, 0);
  } finally { await w.cleanup(); }
});

test("S16 새로고침은 원본 binding 없이 failed 원장을 복원하고 같은 요청을 재시도한다", async () => {
  const value = context(); value.sources = [];
  value.source_deletion_requests = [{ request_id: "request-s16", source_id: source.source_id, state: "failed", version: 1,
    grace_until: "2026-10-16T00:00:00Z", legal_hold_active: false }];
  const old = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return url.includes("session/step-up") ? Response.json({ data: { step_up_authorization: "fresh-grant" } })
      : Response.json({ data: view("purged"), meta: { trace_id: "trace-1" } }, { headers: { ETag: '"deletion:request-s16:1"' } });
  };
  let w;
  try {
    const adapter = createNotebookContextWorkspaceAdapter({ ...actual("workspace-1"), listSources: async () => [] }, value);
    w = await mount({ listSources: async () => [], listSourceDeletionRequests: adapter.listSourceDeletionRequests,
      requestSourceDeletion: adapter.requestSourceDeletion });
    await click(w, `${source.source_id} 삭제 요청 상태`);
    await password(w, "fixture-fresh-confirmation"); await click(w, "삭제 정리 재시도");
    assert.equal(calls.length, 2);
    assert.equal(calls[1].init.headers["Idempotency-Key"], source.source_id);
    assert.equal(JSON.parse(calls[1].init.body).notebook_id, "notebook-s16");
    assert.match(w.container.textContent, /실제 정리가 완료/u);
  } finally { if (w) await w.cleanup(); globalThis.fetch = old; }
});
