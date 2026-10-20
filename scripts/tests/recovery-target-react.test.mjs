import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test, { after, before } from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";

// 실제 컴포넌트/Vite/React 경로이며 HTTP 포트만 대역이다. DB·외부 복구 증거가 아니다.
const require = createRequire(import.meta.url);
const canary = "S42_표시금지_합성원문_목적지_자격정보";
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const response = data => ({ payload: { data, meta: { trace_id: "trace-recovery-read" } }, etag: '"recovery-read-1"' });
// 실제 GET/session 공개 필드. primary Workspace의 권한을 대상 Workspace 권한으로 추측하지 않는다.
const session = (changes = {}) => response({
  user_id: "reader-a", login_id: "reader-a", tenant_id: "tenant-a", workspace_id: "workspace-a",
  session_id: "session-a", device_id: "device-a", client_kind: "web", delivery: "same_origin_secure_cookie",
  expires_at: "2099-01-01T00:00:00Z", password_change_required: false, is_system_admin: false,
  recovery_operations: ["cloud_backup_create", "cloud_backup_get", "cloud_backup_list", "cloud_restore_cancel",
    "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview"], ...changes,
});
const backup = (changes = {}) => ({
  backup_id: "backup-a", tenant_id: "tenant-a", workspace_id: "workspace-a", state: "ready", version: 3,
  trigger: "manual", created_at: "2026-10-18T00:00:00Z", verified_at: "2026-10-18T00:00:01Z",
  schema_revision: "0068", retention_watermark: "watermark-a", manifest_digest: "a".repeat(64),
  object_count: 1, transitions: ["queued", "capturing", "verifying", "ready"], ...changes,
});
const restore = (changes = {}) => ({
  request_id: "restore-a", backup_id: "backup-a", tenant_id: "tenant-a", workspace_id: "workspace-a",
  state: "completed", version: 6, verification_digest: "b".repeat(64),
  preview: { version: 1, included_object_ids: ["object-a"], excluded_object_ids: [], exclusion_reasons: [],
    destination: { tenant_id: "destination-tenant", workspace_id: "destination-workspace", database_id: "destination-db", bucket_id: "destination-bucket" },
    created_at: "2026-10-18T00:00:00Z" },
  transitions: ["requested", "preview_ready", "authorized", "restoring", "verifying", "completed"], ...changes,
});
function failure(status, code = "RESOURCE_UNAVAILABLE") {
  return Object.assign(new Error(canary), { status, code, traceId: "trace-safe-error" });
}
function port(overrides = {}) {
  const calls = [], writes = [];
  const api = {
    getSession: async () => { calls.push(["session"]); return session(); },
    getBackup: async id => { calls.push(["backup", id]); return response(backup()); },
    getRestore: async id => { calls.push(["restore", id]); return response(restore()); },
    ...overrides,
  };
  for (const name of ["listBackups", "createBackup", "previewRestore", "executeRestore", "cancelRestore"]) {
    api[name] = () => { writes.push(name); throw new Error("읽기 선택에서 호출 금지"); };
  }
  return { api, calls, writes };
}
function visibleText(node) {
  if (!node || node.hidden || node.inert || node.style?.display === "none"
    || node.style?.visibility === "hidden" || node.getAttribute?.("aria-hidden") === "true") return "";
  return node.nodeType === 3 ? node.nodeValue : [...node.childNodes].map(visibleText).join("");
}
function noMutations(view, writes = []) {
  assert.deepEqual(writes, []);
  const controls = findElements(view.container, node => ["BUTTON", "A", "INPUT", "SELECT", "TEXTAREA", "FORM"].includes(node.tagName));
  assert.equal(controls.some(node => /생성|실행|취소|설정|복구 시작|백업 시작/u.test(node.textContent)), false);
  assert.doesNotMatch(view.container.textContent, /S42_표시금지|destination-tenant|destination-workspace|destination-db|destination-bucket|object-a/u);
}

let output, component;
before(async () => {
  output = await mkdtemp(path.join(tmpdir(), "daon-s42-recovery-target-react-"));
});
async function loadComponent() {
  if (component) return;
  const root = path.resolve(import.meta.dirname, "../..");
  const priorNodeEnv = process.env.NODE_ENV;
  try {
    const { build } = await import("vite");
    await build({ configFile: false, logLevel: "silent", root, cacheDir: path.join(output, "cache"), build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/app/operations/recovery-workspace.jsx"), formats: ["es"], fileName: "recovery-target" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"],
        output: { paths: id => pathToFileURL(require.resolve(id)).href } } } });
    const built = (await readdir(output)).find(name => /^recovery-target.*\.m?js$/u.test(name));
    assert.ok(built, "실제 RecoveryOperationsWorkspace 번들");
    component = (await import(pathToFileURL(path.join(output, built)).href)).RecoveryOperationsWorkspace;
  } finally {
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
  }
}
after(async () => {
  if (output) {
    assert.equal(path.dirname(path.resolve(output)), path.resolve(tmpdir()));
    assert.ok(path.basename(output).startsWith("daon-s42-recovery-target-react-"));
    await rm(output, { recursive: true, force: true });
  }
});

async function render(search = "", api) {
  const dom = installMinimalDom(), browser = dom.document.defaultView;
  const priorLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
  const location = new URL(`https://app.example/operations${search}`);
  browser.location = location;
  browser.history = { replaceState: (_state, _title, url) => { location.href = new URL(url, location).href; } };
  browser.dispatchEvent = event => dom.document.dispatchEvent(event);
  Object.defineProperty(globalThis, "location", { configurable: true, value: location });
  dom.document.visibilityState = "visible";
  let root, container, createElement, act;
  let mounted = false;
  const rerender = async nextApi => { await act(async () => {
    root.render(createElement(component, nextApi === undefined ? {} : { api: nextApi }));
  }); };
  const unmount = async () => { if (root && mounted) { mounted = false; await act(async () => root.unmount()); } };
  const cleanup = async () => {
    try { await unmount(); } finally {
      if (priorLocation) Object.defineProperty(globalThis, "location", priorLocation); else delete globalThis.location;
      dom.restore();
    }
  };
  try {
    // 기존 정상 하네스처럼 DOM과 React/renderer를 Vite build보다 먼저 초기화한다.
    // Vite의 build 기본 NODE_ENV=production 때문에 첫 React import에서 act가 사라지는 것을 방지한다.
    const priorNodeEnv = process.env.NODE_ENV;
    let createRoot;
    try {
      process.env.NODE_ENV = "test";
      ({ createElement, act } = await import("react"));
      assert.equal(typeof act, "function", "React 테스트 act 초기화");
      ({ createRoot } = await import("react-dom/client"));
    } finally {
      if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    }
    await loadComponent();
    container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    root = createRoot(container); mounted = true;
    await rerender(api);
  } catch (error) {
    // 초기 import/build/render 오류가 있으면 정리는 수행하되 2차 정리 오류로 원오류를 가리지 않는다.
    try { await cleanup(); } catch { /* 최초 오류는 아래에서 그대로 실패 처리한다. */ }
    throw error;
  }
  return { container, dom, browser, act, rerender, unmount, cleanup,
    navigate: async next => { await act(async () => {
      browser.history.replaceState(null, "", `/operations${next}`);
      browser.dispatchEvent(new MinimalEvent("popstate"));
    }); } };
}

const operations = () => ({ workspace_id: "workspace-a", overall_status: "warning", checked_at: "2026-10-18T00:00:00Z",
  components: [
    { component_id: "provider", status: "warning", safe_code: "PROVIDER_CONFIGURATION_REQUIRED", pending_count: 0, recovery_action: "open_llm_settings" },
    { component_id: "api", status: "ready", safe_code: "API_READY", pending_count: 0, recovery_action: "none" },
    { component_id: "storage", status: "ready", safe_code: "STORAGE_READY", pending_count: 0, recovery_action: "none" },
    { component_id: "sync", status: "warning", safe_code: "SYNC_PENDING", pending_count: 1, recovery_action: "open_sync_settings" },
    { component_id: "queue", status: "warning", safe_code: "QUEUE_ATTENTION_REQUIRED", pending_count: 0, recovery_action: "refresh_status" },
  ], observations: ["document_pending", "document_retry_wait", "document_leased", "document_dead_letter", "object_pending", "object_retry_wait", "object_leased", "object_dead_letter", "object_oldest_pending_seconds", "source_waiting_model", "index_records_current", "source_storage_bytes", "reported_prompt_tokens", "reported_completion_tokens", "reported_total_tokens", "attempts_without_reported_usage"].map((metric_id, index) => ({ metric_id,
    state: "not_collected", value: null, unit: index === 8 ? "seconds" : index === 11 ? "bytes" : index >= 12 && index <= 14 ? "tokens" : "count",
    observed_at: null, coverage: index < 9 ? "workspace_operational_counts" : "actor_accessible_records", safe_code: "METRIC_NOT_COLLECTED" })),...workspaceTelemetry() });
function homePort(overrides = {}) {
  const p = port();
  p.api.getOperationsStatus = async workspace => { p.calls.push(["status", workspace]); return operations(); };
  p.api.listBackups = async workspace => { p.calls.push(["list", workspace]); return response([]); };
  Object.assign(p.api, overrides);
  return p;
}
const homeCalls = [["session"], ["status", "workspace-a"], ["list", "workspace-a"], ["session"]];
const systemStatus = () => {
  const common={state:"not_collected",observed_at:null,received_at:null,safe_code:"TELEMETRY_NOT_COLLECTED"};
  const kinds=["document","studio","object"],metrics=["cpu_quota_millicores","memory_limit_bytes","memory_current_bytes","runtime_available_bytes"];
  return {schema_version:1,checked_at:"2026-10-19T00:00:00Z",
    health:["api","database","object_storage"].map(component_id=>({component_id,state:"ready",checked_at:"2026-10-19T00:00:00Z",safe_code:component_id.toUpperCase()+"_READY"})),
    worker_samples:kinds.map(worker_kind=>({...common,worker_kind,process_state:"unknown",poll_started_at:null,poll_finished_at:null,active_started_at:null})),
    resource_samples:kinds.flatMap(worker_kind=>metrics.map((metric_id,i)=>({...common,worker_kind,metric_id,value:null,unit:i===0?"millicores":"bytes"}))),
    deployment_summary:{...common,operation:null,code_revision:null,image_digest:null,previous_image_digest:null,result:null},
    unimplemented:[{producer_id:"deployment_receipt_writer",safe_code:"PRODUCER_NOT_IMPLEMENTED",impact_code:"DEPLOYMENT_HISTORY_UNKNOWN"}]};
};

const withHostCapacity = (data,coverage=false,measured=false) => ({...data,resource_samples:[...data.resource_samples.slice(0,12),
  ...[['cpu_logical_millicores','millicores',2000],['cpu_usage_basis_points','basis_points',4000],
    ['memory_total_bytes','bytes',1073741824],['memory_available_bytes','bytes',536870912]].map(([metric_id,unit,value])=>({
      worker_kind:'server_host',metric_id,unit,value:measured?value:null,state:measured?'fresh':'not_collected',safe_code:measured?'TELEMETRY_FRESH':'TELEMETRY_NOT_COLLECTED',
      observed_at:measured?'2026-10-20T00:00:00Z':null,received_at:measured?'2026-10-20T00:00:01Z':null,
      ...(coverage?{coverage:'server_host_observation'}:{})}))]});

test('S44 Workspace host capacity is explicitly requested and renders measured percent source and time',async()=>{
  const data=withHostCapacity(operations(),true,true);let options;
  const p=homePort({getOperationsStatus:async(workspace,current)=>{options=current;return data;}});
  const view=await render('',p.api);
  try{
    assert.equal(options.includeCapacity,true);
    const text=visibleText(view.container);
    for(const label of ['WSL-server 실측','논리 CPU','CPU 사용률','40.00%','가용 RAM','2026-10-20T00:00:00Z'])assert.ok(text.includes(label),label);
    assert.deepEqual(p.writes,[]);
  }finally{await view.cleanup();}
});

test('S44 noWorkspace admin explicitly requests and validates host capacity without source metadata',async()=>{
  let options;
  const p=homePort({getSession:async()=>session({workspace_id:null,is_system_admin:true}),
    getSystemOperationsStatus:async current=>{options=current;return withHostCapacity(systemStatus(),false,true);}});
  const view=await render('',p.api);
  try{
    assert.equal(options.includeCapacity,true);
    assert.match(visibleText(view.container),/WSL-server 실측/u);
    assert.match(visibleText(view.container),/40\.00%/u);
    assert.doesNotMatch(visibleText(view.container),/boot_id|cpu_counters/u);
    assert.deepEqual(p.writes,[]);
  }finally{await view.cleanup();}
});

function workspaceTelemetry(){
  const system=withHostCapacity(systemStatus()),common={state:"not_collected",observed_at:null,received_at:null,coverage:"actor_accessible_records",safe_code:"TELEMETRY_NOT_COLLECTED"};
  return {worker_samples:system.worker_samples.map(r=>({...r,coverage:"workspace_operational_counts"})),
    resource_samples:system.resource_samples.map(r=>({...r,coverage:r.worker_kind==='server_host'?'server_host_observation':"workspace_operational_counts"})),
    event_counts:["manual_reprocess_pending","readiness_reprocess_pending","retry_wait","backoff_wait","step_up_denied_24h","step_up_expired_24h","access_blocked_24h","access_masked_24h","egress_denied_24h"].map(metric_id=>({...common,metric_id,value:null,unit:"count",window_start:null,window_end:null})),
    cost_summary:{...common,known_amounts:[],unknown_attempt_count:null,unconfirmed_run_count:null,coverage_start:null,coverage_end:null},
    deployment_summary:{...common,operation:null,code_revision:null,image_digest:null,previous_image_digest:null,result:null},
    unimplemented:[{producer_id:"readiness_event_producer",safe_code:"PRODUCER_NOT_IMPLEMENTED",impact_code:"AUTOMATIC_REPROCESS_UNOBSERVED"}]};
}

test("R4 workspace home explicitly requests readiness and distinguishes current waits partial zero and uncollected",async()=>{
  const calls=[];
  const data=operations();
  const ids=["waiting_text_generation","waiting_image_understanding","waiting_document_parsing","waiting_speech_to_text","waiting_role_unknown","retry_suppressed_contexts_24h"];
  data.event_counts.push(...ids.map((metric_id,i)=>({metric_id,state:i===0?"partial":"not_collected",value:i===0?0:null,unit:"count",
    observed_at:i===0?"2026-10-19T00:00:00Z":null,received_at:i===0?"2026-10-19T00:00:00Z":null,
    coverage:"actor_accessible_records",safe_code:i===0?"TELEMETRY_PARTIAL":"TELEMETRY_NOT_COLLECTED",window_start:null,window_end:null})));
  const p=homePort({getOperationsStatus:async(workspace,options)=>{calls.push([workspace,options]);return data;}});
  const view=await render("",p.api);
  try{
    assert.deepEqual(calls,[["workspace-a",{scope:"self",includeReadiness:true,includeCapacity:true}]]);
    const text=visibleText(view.container);
    for(const label of ["텍스트 생성 역할 대기","이미지 이해 역할 대기","문서 파싱 역할 대기","음성 전사 역할 대기","역할 미확인 대기","최초 억제 조건 · 최근 24시간"])
      assert.ok(text.includes(label),label);
    assert.match(text,/부분 집계.*0건/u);
    assert.match(text,/미집계.*값 미확인/u);
    assert.deepEqual(p.writes,[]);
  }finally{await view.cleanup();}
});

test("M2 workspace home renders typed observations and discards prior scope while visible request waits",async()=>{
  const late=deferred(),scopes=[];
  const p=homePort({getOperationsStatus:async(workspace,{scope}={})=>{scopes.push(scope);return scope==="workspace_visible"?late.promise:operations();}});
  const view=await render("",p.api);
  try{
    assert.match(visibleText(view.container),/Worker·자원 관측/u);
    assert.match(visibleText(view.container),/차단·재처리 관측/u);
    assert.match(visibleText(view.container),/미집계/u);
    await click(view,"현재 열람 가능한 Workspace 기록");
    assert.doesNotMatch(visibleText(view.container),/차단·재처리 관측/u);
    await view.act(async()=>late.resolve(operations()));
    assert.deepEqual(scopes,["self","workspace_visible"]);
    assert.match(visibleText(view.container),/차단·재처리 관측/u);
    assert.doesNotMatch(visibleText(view.container),/기본 USD|PRIVATE_BOOT/u);
    assert.deepEqual(p.writes,[]);
  }finally{await view.cleanup();}
});

test("M2 explicit Workspace scope remains denied on privilege withdrawal without silently reading self",async()=>{
  let revoked=false;const scopes=[];
  const p=homePort({getSession:async()=>session(revoked?{recovery_operations:[]}:{}),
    getOperationsStatus:async(workspace,{scope}={})=>{scopes.push(scope);return operations();}});
  const view=await render("",p.api);
  try{
    assert.deepEqual(scopes,["self"]);
    revoked=true;
    await click(view,"현재 열람 가능한 Workspace 기록");
    assert.deepEqual(scopes,["self"],"revoked explicit scope must not fetch a replacement self projection");
    assert.match(visibleText(view.container),/권한|접근/u);
    assert.doesNotMatch(visibleText(view.container),/차단·재처리 관측/u);
    assert.deepEqual(p.writes,[]);
  }finally{await view.cleanup();}
});

test("M2-S noWS admin system home reads only system status with session before/after",async()=>{
  const p=homePort({getSession:async()=>{p.calls.push(["session"]);return session({workspace_id:null,is_system_admin:true});},
    getSystemOperationsStatus:async()=>{p.calls.push(["system"]);return withHostCapacity(systemStatus());}});
  const view=await render("",p.api);
  try{
    assert.match(visibleText(view.container),/시스템 운영 상태/u);
    assert.match(visibleText(view.container),/미수집/u);
    assert.doesNotMatch(visibleText(view.container),/현재 Workspace|백업 목록/u);
    assert.deepEqual(p.calls,[["session"],["system"],["session"]]);assert.deepEqual(p.writes,[]);
  }finally{await view.cleanup();}
});

test("M2-S noWS fallback never overrides Workspace or target query and client nonadmin",async()=>{
  for(const [address,changes] of [["",{is_system_admin:true}],["?backup_id=backup-a",{workspace_id:null,is_system_admin:true}],["",{workspace_id:null,is_system_admin:false}]]){
    let systems=0;const p=homePort({getSession:async()=>session(changes),getSystemOperationsStatus:async()=>{++systems;return withHostCapacity(systemStatus());}});
    const view=await render(address,p.api);
    try{assert.equal(systems,0);assert.doesNotMatch(visibleText(view.container),/시스템 운영 상태/u);}
    finally{await view.cleanup();}
  }
});

test("M2-S system response is discarded on session/admin change and pagehide late result",async()=>{
  for(const mode of ["session","admin","pagehide","api"]){
    const late=deferred();let reads=0,signal;
    const p=homePort({getSession:async()=>session({workspace_id:null,is_system_admin:mode!=="admin"||reads<1,
      session_id:mode==="session"&&reads++>0?"session-b":"session-a"}),
      getSystemOperationsStatus:async options=>{signal=options?.signal;return late.promise;}});
    const view=await render("",p.api);
    try{
      assert.ok(signal,"시스템 GET의 AbortSignal 전달이 필요하다");
      if(mode==="admin")reads=1;
      if(mode==="pagehide")await view.act(async()=>view.browser.dispatchEvent(new MinimalEvent("pagehide")));
      if(mode==="api")await view.rerender(homePort().api);
      await view.act(async()=>late.resolve(withHostCapacity(systemStatus())));
      assert.doesNotMatch(visibleText(view.container),/시스템 운영 상태/u);
      if(["pagehide","api"].includes(mode))assert.equal(signal.aborted,true);
    }finally{await view.cleanup();}
  }
});

async function click(view, label) {
  const node = findElements(view.container, n => n.tagName === "BUTTON" && n.textContent === label)[0];
  assert.ok(node, label);
  await view.act(async () => node.dispatchEvent(new MinimalEvent("click")));
}

const l1Sources = (changes = {}) => ({ notebookId: "notebook-a", bindingEtag: '"notebook-binding:1"',
  sources: [{ sourceId: "source-a", versionId: "version-a" }, { sourceId: "source-b", versionId: "version-b" }], ...changes });
function l1Port(overrides = {}) {
  const calls = [], p = homePort();
  Object.assign(p.api, {
    listSelectionNotebooks: async (...args) => { calls.push(["notebooks", ...args]); return [{ id: "notebook-a", title: "Notebook" }]; },
    getSelectionSources: async (...args) => { calls.push(["sources", ...args]); return l1Sources(); },
    createBackupSelection: async (...args) => { calls.push(["post", ...args]); throw failure(503); },
    ...overrides,
  });
  return { ...p, selectionCalls: calls };
}
async function l1Select(view) {
  await click(view, "백업 자료 선택"); await click(view, "Notebook · notebook-a"); await click(view, "SourceVersion version-a");
}
async function l1Submit(view, count = 1) {
  const form = findElements(view.container, n => n.tagName === "FORM")[0]; assert.ok(form, "L1 명시 요청 form");
  await view.act(async () => { for (let i = 0; i < count; i++) form.dispatchEvent(new MinimalEvent("submit")); });
}

test("S44 label 실제 React는 동명 filename과 Version 보조표시로 정확 선택 POST를 유지한다", async () => {
  const p = l1Port({ getSelectionSources: async () => l1Sources({ sources: [
    { sourceId: "source-b", versionId: "version-b", filename: "동일이름.txt" },
    { sourceId: "source-a", versionId: "version-a", filename: "동일이름.txt" },
  ] }) }), view = await render("", p.api);
  try {
    await click(view, "백업 자료 선택"); await click(view, "Notebook · notebook-a");
    const labels = findElements(view.container, n => n.tagName === "BUTTON" && n.textContent.startsWith("동일이름.txt"));
    assert.deepEqual(labels.map(n => n.textContent), ["동일이름.txt · SourceVersion version-b", "동일이름.txt · SourceVersion version-a"]);
    await click(view, "동일이름.txt · SourceVersion version-b"); await click(view, "동일이름.txt · SourceVersion version-a");
    await l1Submit(view, 2);
    const posts = p.selectionCalls.filter(c => c[0] === "post");
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0][1], { workspace_id: "workspace-a", notebook_id: "notebook-a", trigger: "manual", source_version_ids: ["version-a", "version-b"] });
    assert.doesNotMatch(visibleText(view.container), /백업 생성 성공|백업 생성 완료/u);
  } finally { await view.cleanup(); }
});

test("S44 label filename은 bounded React text만 표시하고 누락은 Version ID로 유지한다", async () => {
  for (const filename of ['<img src=x onerror="alert(1)">.txt', "긴파일".repeat(70) + ".txt"]) {
    const p = l1Port({ getSelectionSources: async () => l1Sources({ sources: [
      { sourceId: "source-a", versionId: "version-a", filename }, { sourceId: "source-b", versionId: "version-b" },
    ] }) }), view = await render("", p.api);
    try {
      await click(view, "백업 자료 선택"); await click(view, "Notebook · notebook-a");
      const short = [...filename].slice(0, 60).join("") + ([...filename].length > 60 ? "…" : "");
      await click(view, `${short} · SourceVersion version-a`); await click(view, "SourceVersion version-b");
      assert.equal(findElements(view.container, n => ["IMG", "SCRIPT", "A"].includes(n.tagName)).length, 0);
      if (filename.length > 60) assert.equal(view.container.textContent.includes(filename), false);
      assert.deepEqual(p.selectionCalls.filter(c => c[0] === "post"), []);
    } finally { await view.cleanup(); }
  }
});

function labelHttpPort(sourceResponse, currentSession = () => session()) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push([url, init]);
    assert.equal(init.credentials, "same-origin"); assert.equal(init.cache, "no-store");
    if (url === "/bff/api/session") return Response.json(currentSession().payload);
    if (url.endsWith("/operations/status")) return Response.json({ data: operations(), meta: { workspace_id: "workspace-a", trace_id: "trace-label" } });
    if (url.startsWith("/bff/api/backups?")) return Response.json(response([]).payload);
    if (url === "/bff/api/restore-requests/restore-a") return Response.json(response(restore()).payload);
    if (url.endsWith("/notebooks")) return Response.json({ data: [{ notebook_id: "notebook-a", title: "Notebook", source_count: 1, output_count: 0,
      updated_at: "2026-10-18T00:00:00Z", status: "active", etag: '"notebook:1"' }], meta: { workspace_id: "workspace-a", trace_id: "trace-label" } });
    if (url.endsWith("/context")) return Response.json({ data: { notebook_id: "notebook-a", sources: [{ source_id: "source-a", source_version_id: "version-a" }],
      knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [],
      source_deletion_requests: [], conversation: null }, meta: { workspace_id: "workspace-a", trace_id: "trace-label" } }, { headers: { ETag: '"notebook-binding:1"' } });
    assert.equal(url, "/bff/api/workspaces/workspace-a/sources?notebook_id=notebook-a");
    return sourceResponse(init);
  };
  return { calls, fetchImpl };
}
const labelHttpResponse = () => Response.json({ data: { sources: [{ source_id: "source-a", source_version_id: "version-a", filename: "늦은파일.txt",
  source_state: "ready", processing_state: "completed", job_state: "completed" }] }, meta: { workspace_id: "workspace-a", trace_id: "trace-label" } });

test("S44 label 실제 Context 복수Version 최신목록 fallback은 원래Version POST를 보존한다", async () => {
  const previous = globalThis.fetch, posted = [];
  const p = labelHttpPort(() => labelHttpResponse());
  globalThis.fetch = async (url, init) => {
    if (url.endsWith("/context")) return Response.json({ data: { notebook_id: "notebook-a", sources: [
      { source_id: "source-a", source_version_id: "version-old" }, { source_id: "source-a", source_version_id: "version-a" },
    ], knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [],
    source_deletion_requests: [], conversation: null }, meta: { workspace_id: "workspace-a", trace_id: "trace-label" } }, { headers: { ETag: '"notebook-binding:1"' } });
    if (url === "/bff/api/backups" && init.method === "POST") {
      posted.push(JSON.parse(init.body));
      return Response.json({ error: { code: "RESOURCE_UNAVAILABLE" } }, { status: 503 });
    }
    return p.fetchImpl(url, init);
  };
  let view;
  try {
    view = await render(""); await click(view, "백업 자료 선택"); await click(view, "Notebook · notebook-a");
    await click(view, "SourceVersion version-old"); await click(view, "늦은파일.txt · SourceVersion version-a");
    await l1Submit(view, 2);
    assert.deepEqual(posted, [{ workspace_id: "workspace-a", notebook_id: "notebook-a", trigger: "manual", source_version_ids: ["version-a", "version-old"] }]);
    assert.doesNotMatch(view.container.textContent, /백업 생성 성공|백업 생성 완료/u);
  } finally { if (view) await view.cleanup(); globalThis.fetch = previous; }
});

test("S44 label 실제 adapter 목록권한 실패와 모호한 자료는 UI 선택을 거부한다", async () => {
  const previous = globalThis.fetch;
  try {
    for (const mode of [401, 403, "duplicate", "mismatch"]) {
      const p = labelHttpPort(() => {
        if (typeof mode === "number") return Response.json({ error: { code: canary } }, { status: mode });
        const row = { source_id: "source-a", source_version_id: mode === "mismatch" ? "version-new" : "version-a", filename: "채택금지.txt",
          source_state: "ready", processing_state: "completed", job_state: "completed" };
        return Response.json({ data: { sources: mode === "duplicate" ? [row, row] : [row] }, meta: { workspace_id: "workspace-a", trace_id: "trace-label" } });
      });
      globalThis.fetch = p.fetchImpl;
      const view = await render("");
      try {
        await click(view, "백업 자료 선택"); await click(view, "Notebook · notebook-a");
        assert.equal(findElements(view.container, n => n.tagName === "FORM").length, 0);
        assert.doesNotMatch(view.container.textContent, /채택금지|S42_표시금지/u);
        assert.equal(p.calls.filter(([, init]) => init.method === "POST").length, 0);
        assert.match(visibleText(view.container), mode === 401 ? /로그인/u : mode === 403 ? /권한/u : /확인할 수 없습니다/u);
      } finally { await view.cleanup(); }
    }
  } finally { globalThis.fetch = previous; }
});

test("S44 label 실제 adapter 지연목록은 취소 계정 Workspace API 주소교체 뒤 표시와 POST0이다", async () => {
  const previous = globalThis.fetch;
  try {
    for (const mode of ["pagehide", "account", "workspace", "api", "target", "unmount"]) {
      const gate = deferred(); let changed = false, signal;
      const p = labelHttpPort(init => { signal = init.signal; return gate.promise; },
        () => session(changed ? mode === "workspace" ? { workspace_id: "workspace-other" } : { user_id: "reader-other", session_id: "session-other" } : {}));
      globalThis.fetch = p.fetchImpl;
      const view = await render("");
      try {
        await click(view, "백업 자료 선택"); await click(view, "Notebook · notebook-a");
        assert.ok(signal, "실제 filename 목록 요청 진입");
        if (mode === "pagehide") await view.act(async () => view.browser.dispatchEvent(new MinimalEvent("pagehide")));
        if (["account", "workspace"].includes(mode)) changed = true;
        if (mode === "api") await view.rerender(homePort().api);
        if (mode === "target") await view.navigate("?restore_request_id=restore-a");
        if (mode === "unmount") await view.unmount();
        if (!["account", "workspace"].includes(mode)) assert.equal(signal.aborted, true);
        await view.act(async () => gate.resolve(labelHttpResponse()));
        assert.doesNotMatch(view.container.textContent, /늦은파일|SourceVersion version-a/u);
        assert.equal(p.calls.filter(([, init]) => init.method === "POST").length, 0);
      } finally { gate.resolve(labelHttpResponse()); await view.cleanup(); }
    }
  } finally { globalThis.fetch = previous; }
});

test("S44 L1 실제 React는 선택 전 POST0·명시 선택 후 정확4필드1회·503 성공0이다", async () => {
  const p = l1Port(), view = await render("", p.api);
  try {
    assert.deepEqual(p.selectionCalls, []);
    await l1Select(view); await click(view, "SourceVersion version-b"); await l1Submit(view, 2);
    const posts = p.selectionCalls.filter(c => c[0] === "post"); assert.equal(posts.length, 1);
    assert.deepEqual(posts[0][1], { workspace_id: "workspace-a", notebook_id: "notebook-a", trigger: "manual", source_version_ids: ["version-a", "version-b"] });
    assert.ok(posts[0][2].length >= 16); assert.ok(posts[0][3].signal);
    assert.match(visibleText(view.container), /독립 보존.*확인|준비/u);
    assert.doesNotMatch(visibleText(view.container), /백업 생성 완료|백업 생성 성공/u);
    assert.doesNotMatch(view.container.textContent, /S42_표시금지|schema_revision|retention_watermark/u);
    await click(view, "자료 다시 조회"); assert.equal(p.selectionCalls.filter(c => c[0] === "post").length, 1);
  } finally { await view.cleanup(); }
});

test("S44 L1 검증된 Notebook 제목은 짧은 React text이며 ID는 보조·없는 파일명은 생성0이다", async () => {
  for (const title of ["현재 접근 가능한 자료 Notebook", '<img src=x onerror="unsafe()">', "긴제목".repeat(30)]) {
    const p = l1Port({ listSelectionNotebooks: async () => [{ id: "notebook-a", title, raw_text: canary }],
      getSelectionSources: async () => l1Sources({ sources: [{ sourceId: "source-a", versionId: "version-a", raw_text: canary }] }) });
    const view = await render("", p.api);
    try {
      await click(view, "백업 자료 선택");
      const short = [...title].slice(0, 60).join("") + ([...title].length > 60 ? "…" : "");
      assert.ok(visibleText(view.container).includes(short));
      await click(view, `${short} · notebook-a`);
      assert.match(visibleText(view.container), /SourceVersion ID/u);
      assert.match(visibleText(view.container), /SourceVersion version-a/u);
      assert.equal(findElements(view.container, node => ["IMG", "SCRIPT"].includes(node.tagName)).length, 0);
      assert.doesNotMatch(view.container.textContent, /S42_표시금지/u);
      assert.deepEqual(p.selectionCalls.filter(c => c[0] === "post"), []);
    } finally { await view.cleanup(); }
  }
});

test("S44 L1 변경된 binding·선택 Version 부재는 POST0이며 빈선택·101개도 거부한다", async () => {
  for (const mode of ["binding", "missing", "empty", "101"]) {
    let reads = 0;
    const p = l1Port({ getSelectionSources: async () => {
      ++reads;
      if (mode === "101") return l1Sources({ sources: Array.from({ length: 101 }, (_, i) => ({ sourceId: `s-${i}`, versionId: `v-${i}` })) });
      return l1Sources(reads > 1 ? (mode === "binding" ? { bindingEtag: '"notebook-binding:2"' } : mode === "missing" ? { sources: [] } : {}) : {});
    } });
    const view = await render("", p.api);
    try {
      await click(view, "백업 자료 선택"); await click(view, "Notebook · notebook-a");
      if (["binding", "missing"].includes(mode)) await click(view, "SourceVersion version-a");
      if (mode === "101") for (let i = 0; i < 101; i++) await click(view, `SourceVersion v-${i}`);
      await l1Submit(view);
      assert.equal(p.selectionCalls.filter(c => c[0] === "post").length, 0);
      assert.match(visibleText(view.container), /100|다시 조회|선택/u);
    } finally { await view.cleanup(); }
  }
});

test("S44 L1 매 action의 세션·기기·권한 변경은 이전 선택 POST0이다", async () => {
  for (const field of ["user_id", "tenant_id", "workspace_id", "session_id", "device_id", "recovery_operations"]) {
    let changed = false;
    const p = l1Port({ getSession: async () => session(changed ? { [field]: field === "recovery_operations" ? [] : "changed" } : {}) });
    const view = await render("", p.api);
    try {
      await l1Select(view); changed = true; await l1Submit(view);
      assert.equal(p.selectionCalls.filter(c => c[0] === "post").length, 0);
      assert.doesNotMatch(visibleText(view.container), /SourceVersion version-a/u);
    } finally { await view.cleanup(); }
  }
});

test("S44 L1 늦은 선택 조회·POST는 pagehide/주소/API/unmount 뒤 복원·자동재송신0이다", async () => {
  for (const stage of ["read", "post"]) for (const mode of ["pagehide", "target", "adapter", "unmount"]) {
    const gate = deferred(); let signal, writes = 0;
    const p = l1Port({ ...(stage === "read" ? { listSelectionNotebooks: async (_ws, options) => { signal = options.signal; return gate.promise; } }
      : { createBackupSelection: async (_input, _key, options) => { ++writes; signal = options.signal; return gate.promise; } }) });
    const view = await render("", p.api);
    try {
      if (stage === "read") await click(view, "백업 자료 선택"); else { await l1Select(view); await l1Submit(view); }
      assert.ok(signal);
      if (mode === "pagehide") await view.act(async () => view.browser.dispatchEvent(new MinimalEvent("pagehide")));
      if (mode === "target") await view.navigate("?restore_request_id=restore-a");
      if (mode === "adapter") await view.rerender(homePort().api);
      if (mode === "unmount") await view.unmount();
      assert.equal(signal.aborted, true);
      await view.act(async () => gate.resolve(stage === "read" ? [{ id: "notebook-a" }] : response(backup({ backup_id: "must-not-publish" }))));
      assert.equal(writes, stage === "post" ? 1 : 0);
      assert.doesNotMatch(view.container.textContent, /must-not-publish|백업 생성 성공|백업 생성 완료/u);
    } finally { gate.resolve([]); await view.cleanup(); }
  }
});

test("S44 L1 201 ready 대역 응답만으로 독립 보존 성공·새목록행을 합성하지 않는다", async () => {
  const p = l1Port({ createBackupSelection: async () => response(backup({ backup_id: "must-not-publish" })) });
  const view = await render("", p.api);
  try {
    await l1Select(view); await l1Submit(view);
    assert.match(visibleText(view.container), /독립 보존.*확인/u);
    assert.doesNotMatch(view.container.textContent, /must-not-publish|백업 생성 성공|백업 생성 완료/u);
  } finally { await view.cleanup(); }
});

test("S44 L1 기본adapter의 실제 React→HTTP 대역은 context선택·정확POST1·503 안전실패다", async () => {
  const previous = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push([url, init]);
    assert.equal(init.credentials, "same-origin"); assert.equal(init.cache, "no-store");
    if (url === "/bff/api/session") return Response.json(session().payload);
    if (url.endsWith("/operations/status")) return Response.json({ data: operations(), meta: { workspace_id: "workspace-a", trace_id: "trace-a" } });
    if (url === "/bff/api/backups?workspace_id=workspace-a") return Response.json(response([]).payload);
    if (url.endsWith("/notebooks")) return Response.json({ data: [{ notebook_id: "notebook-a", title: "Notebook", source_count: 1, output_count: 0,
      updated_at: "2026-10-18T00:00:00Z", status: "active", etag: '"notebook:1"' }], meta: { workspace_id: "workspace-a", trace_id: "trace-a" } });
    if (url.endsWith("/notebooks/notebook-a/context")) return Response.json({ data: { notebook_id: "notebook-a", sources: [{ source_id: "source-a", source_version_id: "version-a" }],
      knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [],
      source_deletion_requests: [], conversation: null }, meta: { workspace_id: "workspace-a", trace_id: "trace-a" } }, { headers: { ETag: '"notebook-binding:1"' } });
    if (url.endsWith("/sources?notebook_id=notebook-a")) return Response.json({ data: { sources: [] }, meta: { workspace_id: "workspace-a", trace_id: "trace-a" } });
    assert.equal(url, "/bff/api/backups"); assert.equal(init.method, "POST");
    assert.deepEqual(JSON.parse(init.body), { workspace_id: "workspace-a", notebook_id: "notebook-a", trigger: "manual", source_version_ids: ["version-a"] });
    assert.ok(init.signal); assert.ok(init.headers["Idempotency-Key"].length >= 16);
    return Response.json({ error: { code: canary } }, { status: 503 });
  };
  let view;
  try {
    view = await render(""); await l1Select(view); await l1Submit(view, 2);
    assert.equal(calls.filter(([, init]) => init.method === "POST").length, 1);
    assert.match(visibleText(view.container), /처리 결과를 확정할 수 없습니다.*독립 보존/u);
    assert.doesNotMatch(view.container.textContent, /S42_표시금지|백업 생성 완료|백업 생성 성공/u);
  } finally { if (view) await view.cleanup(); globalThis.fetch = previous; }
});

test("S44 L1 POST 직전 context 재조회 도중 세션 변경도 쓰기0이다", async () => {
  let changed = false, reads = 0;
  const p = l1Port({ getSession: async () => session(changed ? { device_id: "different-device" } : {}),
    getSelectionSources: async () => { if (++reads === 2) changed = true; return l1Sources(); } });
  const view = await render("", p.api);
  try {
    await l1Select(view); await l1Submit(view);
    assert.equal(p.selectionCalls.filter(c => c[0] === "post").length, 0);
    assert.doesNotMatch(visibleText(view.container), /SourceVersion version-a/u);
  } finally { await view.cleanup(); }
});

test("S44 L1 malformed 목록/context는 선택·POST를 차단하고 413·409·통신실패는 자동retry0이다", async () => {
  for (const input of ["duplicate-notebook", "unsafe-notebook", "duplicate-version", "wrong-notebook", "etag", "unsafe-version"]) {
    const p = l1Port({ listSelectionNotebooks: async () => input === "duplicate-notebook" ? [{ id: "notebook-a", title: "Notebook" }, { id: "notebook-a", title: "Notebook" }]
      : input === "unsafe-notebook" ? [{ id: "https://private.example", title: "Notebook" }] : [{ id: "notebook-a", title: "Notebook" }],
      getSelectionSources: async () => l1Sources(input === "duplicate-version" ? { sources: [{ sourceId: "s1", versionId: "v1" }, { sourceId: "s2", versionId: "v1" }] }
        : input === "wrong-notebook" ? { notebookId: "other-notebook" } : input === "etag" ? { bindingEtag: '"notebook-binding:0"' }
          : { sources: [{ sourceId: "s1", versionId: "https://private.example" }] }) });
    const view = await render("", p.api);
    try {
      await click(view, "백업 자료 선택");
      if (!["duplicate-notebook", "unsafe-notebook"].includes(input)) await click(view, "Notebook · notebook-a");
      assert.match(visibleText(view.container), /확인할 수 없습니다/u);
      assert.equal(p.selectionCalls.filter(c => c[0] === "post").length, 0);
      assert.equal(findElements(view.container, n => n.tagName === "FORM").length, 0);
      assert.doesNotMatch(view.container.textContent, /private.example/u);
    } finally { await view.cleanup(); }
  }
  for (const status of [413, 409, null]) {
    let writes = 0;
    const p = l1Port({ createBackupSelection: async () => { ++writes; throw failure(status); } });
    const view = await render("", p.api);
    try {
      await l1Select(view); await l1Submit(view, 2); await click(view, "자료 다시 조회");
      assert.equal(writes, 1); assert.doesNotMatch(view.container.textContent, /S42_표시금지|백업 생성 성공|백업 생성 완료/u);
    } finally { await view.cleanup(); }
  }
});

const restoreResponse = data => ({ payload: { data }, etag: `"restore:${data.request_id}:${data.version}"` });
const pendingRestore = (changes = {}) => restore({ state: "preview_ready", version: 2, verification_digest: null,
  transitions: ["requested", "preview_ready"], ...changes });
const registeredPreview = (changes={}) => ({kind:"selected_source_lineage",version:1,created_at:"2026-10-19T00:00:00Z",expires_at:"2099-01-01T00:00:00Z",
  destination:{destination_id:"destination-a",version:1,display_name:"소유 격리 목적지",expires_at:"2099-01-02T00:00:00Z"},
  source_manifest_digest:"a".repeat(64),closure_fingerprint:"b".repeat(64),included_root_count:1,excluded_root_count:0,
  exclusion_counts:{purged:0,held:0,tombstoned:0},...changes});
function actionPort(overrides = {}) {
  let current = pendingRestore();
  const calls = [];
  const api = {
    getSession: async () => { calls.push(["session"]); return session(); },
    getRestore: async id => { calls.push(["get", id]); return restoreResponse(current); },
    issueRestoreStepUp: async (...args) => { calls.push(["stepup", ...args]); return "synthetic-new-token"; },
    executeRestore: async (...args) => { calls.push(["execute", ...args]); current = restore(); return restoreResponse(current); },
    cancelRestore: async (...args) => { calls.push(["cancel", ...args]); current = pendingRestore({ state: "cancelled", version: 3 }); return restoreResponse(current); },
    ...overrides,
  };
  return { api, calls, setCurrent: value => { current = value; } };
}
const reactProps = node => node[Object.keys(node).find(key => key.startsWith("__reactProps"))];
const passwordField = view => findElements(view.container, node => node.tagName === "INPUT" && reactProps(node)?.type === "password")[0];
async function submitExecute(view, count = 1) {
  const field = passwordField(view);
  assert.ok(field, "추가 인증 비밀번호 입력"); field.value = "synthetic-password";
  const form = findElements(view.container, node => node.tagName === "FORM")[0];
  assert.ok(form, "명시적 복구 실행 form");
  await view.act(async () => { for (let n = 0; n < count; n++) form.dispatchEvent(new MinimalEvent("submit")); });
  assert.equal(field.value, "", "제출 직후 비밀번호 폐기");
}

test("R4 등록 목적지 Preview와 Execute는 별도 인증·정확GET·현재세션으로 순차 소비한다",async()=>{
  const value=registeredPreview(),p=actionPort();
  p.setCurrent(pendingRestore({preview:value}));
  Object.assign(p.api,{
    getBackup:async()=>({payload:{data:backup()},etag:'"backup:backup-a:3"'}),
    getRestoreDestinations:async()=>response([value.destination]),
    previewRegisteredRestore:async(...args)=>{p.calls.push(["preview",...args]);return restoreResponse(pendingRestore({preview:value}));},
    executeRestore:async(...args)=>{p.calls.push(["execute",...args]);p.setCurrent(restore({preview:value}));return restoreResponse(restore({preview:value}));},
  });
  const view=await render("?backup_id=backup-a",p.api);
  try{
    await click(view,"등록 복원 목적지 조회");
    await click(view,"소유 격리 목적지 · destination-a");
    await click(view,"등록 목적지 Preview 준비");await submitExecute(view,2);
    const sent=p.calls.filter(c=>c[0]==="preview");assert.equal(sent.length,1);
    assert.deepEqual(sent[0][2],{workspace_id:"workspace-a",destination_id:"destination-a",destination_version:1,step_up_authorization_id:"synthetic-new-token"});
    assert.equal(view.browser.location.search,"?restore_request_id=restore-a");
    assert.match(visibleText(view.container),/포함 1건/u);
    await click(view,"복구 실행 준비");await submitExecute(view,2);
    assert.deepEqual(p.calls.filter(c=>c[0]==="stepup").map(c=>c[1]),["backup-a","restore-a"]);
    assert.equal(p.calls.filter(c=>c[0]==="execute").length,1);
    assert.match(visibleText(view.container),/상태completed/u);
    assert.doesNotMatch(view.container.textContent,/synthetic-new-token|synthetic-password|raw_ref/u);
  }finally{await view.cleanup();}
});

test("R4 등록 Preview의 잘못된 집합수·시각·목적지·digest는 실행준비를 남기지 않는다",async()=>{
  for(const changes of [{included_root_count:0},{excluded_root_count:2},{exclusion_counts:{purged:0,held:1,tombstoned:0}},
    {expires_at:"invalid"},{closure_fingerprint:"invalid"},{destination:{...registeredPreview().destination,database_ref:canary}}]){
    const p=actionPort();p.setCurrent(pendingRestore({preview:registeredPreview(changes)}));
    const view=await render("?restore_request_id=restore-a",p.api);
    try{assert.doesNotMatch(visibleText(view.container),/복구 실행 준비/u);assert.doesNotMatch(view.container.textContent,/S42_표시금지/u);
      assert.equal(p.calls.filter(c=>c[0]==="execute").length,0);
    }finally{await view.cleanup();}
  }
});

test("R4 Preview 인증 뒤 늦은 목적지 조회와 version 교체는 body0·중단·비밀폐기를 유지한다",async()=>{
  for(const mode of ["pagehide","target","adapter","unmount","version"]){
    const gate=deferred(),bodies=[];let issued=false,signal;
    const destination=registeredPreview().destination,p=actionPort({
      getBackup:async()=>({payload:{data:backup()},etag:'"backup:backup-a:3"'}),
      issueRestoreStepUp:async()=>{issued=true;return "synthetic-preview-token";},
      getRestoreDestinations:async(_id,options)=>{if(issued){signal=options.signal;return gate.promise;}return response([destination]);},
      previewRegisteredRestore:async(_id,body)=>{bodies.push(body);return restoreResponse(pendingRestore({preview:registeredPreview()}));},
    });
    const view=await render("?backup_id=backup-a",p.api);
    try{
      await click(view,"등록 복원 목적지 조회");await click(view,"소유 격리 목적지 · destination-a");
      await click(view,"등록 목적지 Preview 준비");await submitExecute(view);
      assert.equal(issued,true);assert.ok(signal);
      if(mode==="pagehide")await view.act(async()=>view.browser.dispatchEvent(new MinimalEvent("pagehide")));
      if(mode==="target")await view.navigate("?backup_id=backup-b");
      if(mode==="adapter")await view.rerender(actionPort().api);
      if(mode==="unmount")await view.unmount();
      if(mode!=="version")assert.equal(signal.aborted,true);
      await view.act(async()=>gate.resolve(response([{...destination,version:mode==="version"?2:1}])));
      assert.deepEqual(bodies,[]);assert.doesNotMatch(view.container.textContent,/synthetic-preview-token|synthetic-password/u);
    }finally{gate.resolve(response([destination]));await view.cleanup();}
  }
});

test("R4 계보 사전확인 뒤 기본OFF 동의와 exact6만 보내며503을성공으로표시하지 않는다",async()=>{
  const calls=[],p=l1Port({
    previewBackupSelection:async(...args)=>{calls.push(["preflight",...args]);return response({root_source_version_ids:["version-a"],dependency_source_version_ids:["prior-a"],
      dependency_source_ids:["source-a"],source_version_count:2,source_count:1,unique_object_count:2,total_bytes:30,metadata_bytes:1000,closure_fingerprint:"a".repeat(64)});},
    createLineageBackup:async(...args)=>{calls.push(["create",...args]);throw failure(503);},
  });
  const view=await render("",p.api);
  try{
    await l1Select(view);await click(view,"계보 사전확인");
    const submit=findElements(view.container,n=>n.tagName==="BUTTON"&&n.textContent==="계보 포함 백업 요청")[0];
    assert.ok(submit);assert.equal(reactProps(submit).disabled,true);
    assert.equal(calls.filter(c=>c[0]==="create").length,0);
    await click(view,"선행 버전 포함에 동의");await click(view,"계보 포함 백업 요청");
    const writes=calls.filter(c=>c[0]==="create");assert.equal(writes.length,1);
    assert.deepEqual(writes[0][1],{workspace_id:"workspace-a",notebook_id:"notebook-a",source_version_ids:["version-a"],lineage_mode:"exact",trigger:"manual",
      closure_consent:{include_prior_versions:true,closure_fingerprint:"a".repeat(64)}});
    assert.doesNotMatch(view.container.textContent,/백업 생성 성공|백업 생성 완료|S42_표시금지/u);
  }finally{await view.cleanup();}
});

test("S44 fixpass 발급 뒤 session/target 지연은 pagehide/주소/API/unmount 시 abort되고 Execute body0이다", async () => {
  for (const stage of ["session-before", "target", "session-after"]) for (const mode of ["pagehide", "target", "adapter", "unmount"]) {
    const gate = deferred(), bodies = []; let issued = false, gated = false, sessionsAfterIssue = 0, signal;
    const p = actionPort({
      issueRestoreStepUp: async () => { issued = true; return "synthetic-issued-token"; },
      getSession: async options => {
        if (issued) {
          ++sessionsAfterIssue;
          if (!gated && ((stage === "session-before" && sessionsAfterIssue === 1) || (stage === "session-after" && sessionsAfterIssue === 2))) {
            gated = true; signal = options?.signal; return gate.promise;
          }
        }
        return session();
      },
      getRestore: async (_id, options) => {
        if (issued && stage === "target" && !gated) { gated = true; signal = options?.signal; return gate.promise; }
        return restoreResponse(pendingRestore());
      },
      executeRestore: async (_id, body) => { bodies.push(body); return restoreResponse(restore()); },
    });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      await click(view, "복구 실행 준비"); await submitExecute(view);
      assert.equal(issued, true); assert.equal(gated, true); assert.ok(signal, "발급 후 정확 조회도 AbortSignal 결속");
      if (mode === "pagehide") await view.act(async () => view.browser.dispatchEvent(new MinimalEvent("pagehide")));
      if (mode === "target") await view.navigate("?restore_request_id=restore-b");
      if (mode === "adapter") await view.rerender(actionPort().api);
      if (mode === "unmount") await view.unmount();
      assert.equal(signal.aborted, true);
      await view.act(async () => gate.resolve(stage === "target" ? restoreResponse(pendingRestore()) : session()));
      assert.deepEqual(bodies, []);
      assert.doesNotMatch(view.container.textContent, /synthetic-issued-token|synthetic-password/u);
    } finally { gate.resolve(stage === "target" ? restoreResponse(pendingRestore()) : session()); await view.cleanup(); }
  }
});

test("S44 fixpass 목적지 실4필드 누락·오염과 잘못된 RFC3339 시각은 본문/실행준비0이다", async () => {
  const base = pendingRestore().preview, invalid = [];
  for (const field of ["tenant_id", "workspace_id", "database_id", "bucket_id"]) {
    const missing = { ...base.destination }; delete missing[field]; invalid.push({ ...base, destination: missing });
    for (const value of [null, "", "with space", "https://private.example/path", canary]) {
      invalid.push({ ...base, destination: { ...base.destination, [field]: value } });
    }
  }
  for (const destination of [null, [], "destination"]) invalid.push({ ...base, destination });
  for (const created_at of [undefined, null, 0, "", "2026-10-18", "2026-02-29T00:00:00Z", "2026-02-30T00:00:00Z",
    "2026-13-01T00:00:00Z", "2026-10-18T24:00:00Z", "2026-10-18T00:00:00+99:00", "2026-10-18T00:00:00Z\n"]) invalid.push({ ...base, created_at });
  for (const preview of invalid) {
    const p = actionPort({ getRestore: async () => restoreResponse(pendingRestore({ preview })) });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      assert.match(visibleText(view.container), /확인할 수 없/u);
      assert.doesNotMatch(visibleText(view.container), /restore-a|private.example|S42_표시금지/u);
      assert.equal(findElements(view.container, n => n.tagName === "BUTTON" && /복구 실행 준비|복구 요청 취소/u.test(n.textContent)).length, 0);
      assert.equal(p.calls.filter(c => ["stepup", "execute", "cancel"].includes(c[0])).length, 0);
    } finally { await view.cleanup(); }
  }
});

test("S44 fixpass 실제 목적지4필드·서버 RFC3339 시각은 안전 요약만 유지한다", async () => {
  for (const created_at of ["2026-10-18T00:00:00Z", "2026-10-18T09:00:00.123456+09:00", "2024-02-29T00:00:00+00:00"]) {
    const p = actionPort({ getRestore: async () => restoreResponse(pendingRestore({ preview: { ...pendingRestore().preview, created_at } })) });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      assert.ok(findElements(view.container, n => n.tagName === "BUTTON" && n.textContent === "복구 실행 준비")[0]);
      assert.match(visibleText(view.container), /포함 1건.*제외 0건/u);
      assert.doesNotMatch(view.container.textContent, /destination-tenant|destination-workspace|destination-db|destination-bucket/u);
    } finally { await view.cleanup(); }
  }
});

test("S44 fixpass 목적지 unknown extra는 접근·비교·표시하지 않고 known DTO만 결속한다", async () => {
  let extraReads = 0, reads = 0;
  const p = actionPort({ getRestore: async () => {
    ++reads;
    const destination = { ...pendingRestore().preview.destination };
    Object.defineProperty(destination, "unknown_sensitive", { enumerable: true, get: () => { ++extraReads; return `${canary}-${reads}`; } });
    return restoreResponse(pendingRestore({ preview: { ...pendingRestore().preview, destination } }));
  } });
  const view = await render("?restore_request_id=restore-a", p.api);
  try {
    await click(view, "복구 실행 준비"); await submitExecute(view);
    assert.equal(extraReads, 0, "미정의 필드를 비교 문자열에 복사하지 않음");
    assert.equal(p.calls.filter(c => c[0] === "execute").length, 1, "미정의 추가값의 변화로 동일 known Preview를 재정의하지 않음");
    assert.doesNotMatch(view.container.textContent, /S42_표시금지|unknown_sensitive|destination-db|destination-bucket/u);
  } finally { await view.cleanup(); }
});

test("S44 기존복구 안전 Preview는 개수·고정 제외 사유만 보이고 원장 완료와 전체 복원을 구분한다", async () => {
  const data = restore({ preview: { ...restore().preview, included_object_ids: ["object-a"],
    excluded_object_ids: ["private-one", "private-two", "private-three"],
    exclusion_reasons: [["private-one", "purged"], ["private-two", "held"], ["private-three", "tombstoned"]] } });
  const p = actionPort({ getRestore: async () => restoreResponse(data) }), view = await render("?restore_request_id=restore-a", p.api);
  try {
    assert.match(visibleText(view.container), /포함 1건.*제외 3건/u);
    for (const label of ["영구 삭제", "보존", "삭제 표시"]) assert.ok(visibleText(view.container).includes(label));
    assert.match(visibleText(view.container), /전체 DB 복원 성공을 의미하지 않습니다/u);
    assert.doesNotMatch(view.container.textContent, /private-one|private-two|private-three|object-a|destination-tenant|fixture-target/u);
    noMutations(view);
  } finally { await view.cleanup(); }
});

test("S44 기존복구 명시 Execute는 새 인증·정확 ETag/preview를 보내고 정확 GET으로 결과를 확인한다", async () => {
  const p = actionPort(), view = await render("?restore_request_id=restore-a", p.api);
  try {
    await click(view, "복구 실행 준비"); await submitExecute(view, 2);
    const writes = p.calls.filter(c => ["stepup", "execute"].includes(c[0]));
    assert.equal(writes.length, 2, "연속 submit도 인증·실행 각1회");
    assert.equal(writes[0][1], "restore-a");
    assert.equal(writes[1][1], "restore-a");
    assert.deepEqual(writes[1][2], { preview_version: 1, step_up_authorization_id: "synthetic-new-token" });
    assert.equal(writes[1][3], '"restore:restore-a:2"');
    assert.ok(writes[1][4].length >= 16);
    assert.ok(p.calls.slice(p.calls.indexOf(writes[1]) + 1).some(c => c[0] === "get"));
    assert.match(visibleText(view.container), /completed/u);
    assert.doesNotMatch(view.container.textContent, /synthetic-password|synthetic-new-token/u);
  } finally { await view.cleanup(); }
});

test("S44 기존복구 malformed Preview·중복·겹침·미정의 제외 사유는 본문과 쓰기를 차단한다", async () => {
  const base = pendingRestore().preview;
  for (const preview of [null, { ...base, version: 0 }, { ...base, version: "1" },
    { ...base, included_object_ids: ["object-a", "object-a"] },
    { ...base, excluded_object_ids: ["object-a"], exclusion_reasons: [["object-a", "held"]] },
    { ...base, excluded_object_ids: ["x"], exclusion_reasons: [["x", canary]] },
    { ...base, excluded_object_ids: ["x"], exclusion_reasons: [] },
    { ...base, excluded_object_ids: ["x"], exclusion_reasons: [["x", "held"], ["x", "held"]] },
    { ...base, excluded_object_ids: ["x"], exclusion_reasons: [["x", ["held"]]] },
    { ...base, included_object_ids: ["https://private.example/path"] }]) {
    const p = actionPort({ getRestore: async () => restoreResponse(pendingRestore({ preview })) });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      assert.match(visibleText(view.container), /확인할 수 없/u);
      assert.doesNotMatch(visibleText(view.container), /restore-a|S42_표시금지|private.example/u);
      assert.equal(findElements(view.container, n => n.tagName === "FORM").length, 0);
    } finally { await view.cleanup(); }
  }
});

test("S44 기존복구 상태·권한과 다른 primary Workspace의 서버 허용 대상을 구별한다", async () => {
  for (const state of ["requested", "preview_ready", "authorized", "restoring", "verifying", "completed", "cancelled", "failed", "blocked"]) {
    const p = actionPort({ getRestore: async () => restoreResponse(pendingRestore({ state })) });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      const labels = findElements(view.container, n => n.tagName === "BUTTON").map(n => n.textContent);
      assert.equal(labels.includes("복구 실행 준비"), state === "preview_ready");
      assert.equal(labels.includes("복구 요청 취소"), ["requested", "preview_ready", "authorized"].includes(state));
    } finally { await view.cleanup(); }
  }
  for (const otherWorkspace of [false, true]) {
    const p = actionPort({ getSession: async () => session({ recovery_operations: [], workspace_id: otherWorkspace ? "primary-other" : "workspace-a" }) });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      const labels = findElements(view.container, n => n.tagName === "BUTTON").map(n => n.textContent);
      assert.equal(labels.includes("복구 실행 준비"), otherWorkspace);
      assert.match(visibleText(view.container), /restore-a/u);
    } finally { await view.cleanup(); }
  }
});

test("S44 기존복구 잘못된 ETag는 실행·취소를 허용하지 않는다", async () => {
  for (const etag of [null, '"restore:restore-b:2"', '"restore:restore-a:3"', '"restore:restore-a:02"']) {
    const p = actionPort({ getRestore: async () => ({ ...restoreResponse(pendingRestore()), etag }) });
    const view = await render("?restore_request_id=restore-a", p.api);
    try { assert.equal(findElements(view.container, n => n.tagName === "BUTTON" && /복구 실행 준비|복구 요청 취소/u.test(n.textContent)).length, 0); }
    finally { await view.cleanup(); }
  }
});

test("S44 기존복구 추가 인증 실패는 Execute0·고정 오류·비밀번호 폐기다", async () => {
  const p = actionPort({ issueRestoreStepUp: async () => { throw failure(403); } });
  const view = await render("?restore_request_id=restore-a", p.api);
  try {
    await click(view, "복구 실행 준비"); await submitExecute(view);
    assert.equal(p.calls.filter(c => c[0] === "execute").length, 0);
    assert.match(visibleText(view.container), /권한|인증/u);
    assert.doesNotMatch(view.container.textContent, /synthetic-password|S42_표시금지/u);
  } finally { await view.cleanup(); }
});

test("S44 기존복구 쓰기 전 모든 주체 교체와 새 인증 뒤 세션 교체는 POST0이다", async () => {
  for (const field of ["user_id", "tenant_id", "workspace_id", "session_id", "device_id"]) {
    let current = session();
    const p = actionPort({ getSession: async () => current,
      issueRestoreStepUp: async () => { current = session({ [field]: "changed-scope" }); return "synthetic-new-token"; } });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      await click(view, "복구 실행 준비"); await submitExecute(view);
      assert.equal(p.calls.filter(c => c[0] === "execute").length, 0);
      assert.doesNotMatch(visibleText(view.container), /restore-a/u);
    } finally { await view.cleanup(); }
  }
});

test("S44 기존복구 사전 GET의 version·Preview 변경은 재인증·Execute0이다", async () => {
  for (const changes of [{ version: 3 }, { state: "authorized" }, { preview: { ...pendingRestore().preview, version: 2 } },
    { preview: { ...pendingRestore().preview, included_object_ids: ["different-object"] } }]) {
    let reads = 0;
    const p = actionPort({ getRestore: async () => restoreResponse(pendingRestore(++reads === 1 ? {} : changes)) });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      await click(view, "복구 실행 준비"); await submitExecute(view);
      assert.equal(p.calls.filter(c => ["stepup", "execute"].includes(c[0])).length, 0);
      assert.match(visibleText(view.container), /다시 조회/u);
      assert.doesNotMatch(visibleText(view.container), /restore-a/u);
    } finally { await view.cleanup(); }
  }
});

test("S44 기존복구 Cancel은 명시 화면 확인과 허용상태·정확 ETag로 한번만 보낸다", async () => {
  const p = actionPort(), view = await render("?restore_request_id=restore-a", p.api);
  try {
    await click(view, "복구 요청 취소");
    assert.equal(p.calls.filter(c => c[0] === "cancel").length, 0);
    assert.match(visibleText(view.container), /취소하시겠습니까/u);
    const button = findElements(view.container, n => n.tagName === "BUTTON" && n.textContent === "취소 확정")[0];
    assert.ok(button);
    await view.act(async () => { button.dispatchEvent(new MinimalEvent("click")); button.dispatchEvent(new MinimalEvent("click")); });
    const calls = p.calls.filter(c => c[0] === "cancel");
    assert.equal(calls.length, 1); assert.equal(calls[0][1], "restore-a"); assert.equal(calls[0][2], '"restore:restore-a:2"');
    assert.ok(calls[0][3].length >= 16);
    assert.equal(p.calls.filter(c => c[0] === "stepup").length, 0);
    assert.match(visibleText(view.container), /cancelled/u);
  } finally { await view.cleanup(); }
});

test("S44 기존복구 POST 실패는 401·403·409·412·503·네트워크 모두 자동 재전송 없이 본문을 숨긴다", async () => {
  for (const kind of ["execute", "cancel"]) for (const status of [401, 403, 409, 412, 503, null]) {
    let writes = 0;
    const p = actionPort({ [kind === "execute" ? "executeRestore" : "cancelRestore"]: async () => { ++writes; throw failure(status); } });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      if (kind === "execute") { await click(view, "복구 실행 준비"); await submitExecute(view); }
      else { await click(view, "복구 요청 취소"); await click(view, "취소 확정"); }
      await view.act(async () => { await Promise.resolve(); });
      assert.equal(writes, 1); assert.doesNotMatch(visibleText(view.container), /restore-a/u);
      assert.doesNotMatch(view.container.textContent, /S42_표시금지|synthetic-password|synthetic-new-token/u);
      await click(view, "다시 조회"); assert.equal(writes, 1, "명시 GET도 POST를 자동 재시도하지 않음");
    } finally { await view.cleanup(); }
  }
});

test("S44 기존복구 pagehide 입력 즉시 비움·늦은 인증은 주소/API/unmount 교체 뒤 실행0이다", async () => {
  for (const mode of ["pagehide", "target", "adapter", "unmount"]) {
    const gate = deferred(); let signal;
    const p = actionPort({ issueRestoreStepUp: async (_id, _password, options) => { signal = options.signal; return gate.promise; } });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      await click(view, "복구 실행 준비"); await submitExecute(view);
      if (mode === "pagehide") await view.act(async () => view.browser.dispatchEvent(new MinimalEvent("pagehide")));
      if (mode === "target") await view.navigate("?restore_request_id=restore-b");
      if (mode === "adapter") await view.rerender(actionPort().api);
      if (mode === "unmount") await view.unmount();
      assert.equal(signal.aborted, true);
      await view.act(async () => gate.resolve("synthetic-new-token"));
      assert.equal(p.calls.filter(c => c[0] === "execute").length, 0);
      assert.doesNotMatch(view.container.textContent, /synthetic-password|synthetic-new-token/u);
    } finally { gate.resolve("synthetic-new-token"); await view.cleanup(); }
  }
  const p = actionPort(), view = await render("?restore_request_id=restore-a", p.api);
  try {
    await click(view, "복구 실행 준비"); const field = passwordField(view); field.value = "unsubmitted-password";
    await view.act(async () => { view.browser.dispatchEvent(new MinimalEvent("pagehide")); assert.equal(field.value, ""); });
    assert.equal(p.calls.filter(c => c[0] === "stepup").length, 0);
  } finally { await view.cleanup(); }
});

test("S44 기존복구 Execute receipt만 completed여도 후속 GET의 실제 상태를 표시한다", async () => {
  const p = actionPort({ executeRestore: async () => restoreResponse(restore()) });
  const view = await render("?restore_request_id=restore-a", p.api);
  try {
    await click(view, "복구 실행 준비"); await submitExecute(view);
    assert.match(visibleText(view.container), /preview_ready/u);
    assert.doesNotMatch(visibleText(view.container), /상태completed/u);
  } finally { await view.cleanup(); }
});

test("S44 기존복구 실제 기본 adapter의 React→HTTP 대역은 새 인증/Execute와 정확 후속 GET만 연결한다", async () => {
  const previous = globalThis.fetch, calls = []; let current = pendingRestore();
  globalThis.fetch = async (url, init) => {
    calls.push([url, init]);
    assert.equal(init.credentials, "same-origin"); assert.equal(init.cache, "no-store");
    if (url === "/bff/api/session") return Response.json(session().payload);
    if (url === "/bff/api/restore-requests/restore-a") return Response.json({ data: current }, { headers: { ETag: `"restore:restore-a:${current.version}"` } });
    assert.equal(init.method, "POST"); assert.ok(init.headers["Idempotency-Key"].length >= 16);
    if (url === "/bff/api/session/step-up") {
      assert.deepEqual(JSON.parse(init.body), { action_group: "permanent_delete_or_restore_rollback", target_id: "restore-a", password: "synthetic-password", ttl_seconds: 300 });
      return Response.json({ data: { step_up_authorization: "synthetic-http-token" } });
    }
    assert.equal(url, "/bff/api/restore-requests/restore-a/execute");
    assert.deepEqual(JSON.parse(init.body), { preview_version: 1, step_up_authorization_id: "synthetic-http-token" });
    assert.equal(init.headers["If-Match"], '"restore:restore-a:2"');
    current = restore(); return Response.json({ data: current });
  };
  let view;
  try {
    view = await render("?restore_request_id=restore-a"); await click(view, "복구 실행 준비"); await submitExecute(view, 2);
    const posts = calls.filter(([, init]) => init.method === "POST");
    assert.deepEqual(posts.map(([url]) => url), ["/bff/api/session/step-up", "/bff/api/restore-requests/restore-a/execute"]);
    assert.notEqual(posts[0][1].headers["Idempotency-Key"], posts[1][1].headers["Idempotency-Key"]);
    assert.deepEqual(calls.slice(-3).map(([url]) => url), ["/bff/api/session", "/bff/api/restore-requests/restore-a", "/bff/api/session"]);
    assert.match(visibleText(view.container), /상태completed/u);
    assert.doesNotMatch(view.container.textContent, /synthetic-http-token|synthetic-password/u);
  } finally { if (view) await view.cleanup(); globalThis.fetch = previous; }
});

test("S44 기존복구 Preview 목적지 변경도 같은 version이라는 이유로 쓰기하지 않는다", async () => {
  for (const field of ["tenant_id", "workspace_id", "database_id", "bucket_id"]) {
    let reads = 0;
    const p = actionPort({ getRestore: async () => restoreResponse(pendingRestore(++reads === 1 ? {} : {
      preview: { ...pendingRestore().preview, destination: { ...pendingRestore().preview.destination, [field]: "different-destination" } },
    })) });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      await click(view, "복구 실행 준비"); await submitExecute(view);
      assert.equal(p.calls.filter(c => ["stepup", "execute"].includes(c[0])).length, 0);
      assert.doesNotMatch(visibleText(view.container), /restore-a|different-destination/u);
    } finally { await view.cleanup(); }
  }
});

test("S44 기존복구 성공 후 GET의 version 역행·세션 교체·GET 실패는 완료 본문을 남기지 않는다", async () => {
  for (const mode of ["version", "session", "read-error"]) {
    let sent = false;
    const p = actionPort({
      getSession: async () => session(sent && mode === "session" ? { session_id: "different-session" } : {}),
      getRestore: async () => {
        if (sent && mode === "read-error") throw failure(503);
        return restoreResponse(sent ? restore(mode === "version" ? { version: 1 } : {}) : pendingRestore());
      },
      executeRestore: async (...args) => { p.calls.push(["execute", ...args]); sent = true; return restoreResponse(restore()); },
    });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      await click(view, "복구 실행 준비"); await submitExecute(view);
      assert.equal(p.calls.filter(c => c[0] === "execute").length, 1);
      assert.doesNotMatch(visibleText(view.container), /restore-a|상태completed/u);
      assert.match(visibleText(view.container), /다시 조회/u);
    } finally { await view.cleanup(); }
  }
});

test("S44 기존복구 늦은 POST 성공·실패는 pagehide/visibility/target/API/unmount 뒤 본문을 복원하지 않는다", async () => {
  for (const kind of ["execute", "cancel"]) for (const mode of ["pagehide", "visibility", "target", "adapter", "unmount"]) for (const rejects of [false, true]) {
    const gate = deferred(); let writes = 0, signal;
    const p = actionPort({ [kind === "execute" ? "executeRestore" : "cancelRestore"]: async (...args) => {
      ++writes; signal = args.at(-1).signal; return gate.promise;
    } });
    const view = await render("?restore_request_id=restore-a", p.api);
    try {
      if (kind === "execute") { await click(view, "복구 실행 준비"); await submitExecute(view); }
      else { await click(view, "복구 요청 취소"); await click(view, "취소 확정"); }
      assert.equal(writes, 1);
      if (mode === "pagehide") await view.act(async () => view.browser.dispatchEvent(new MinimalEvent("pagehide")));
      if (mode === "visibility") await view.act(async () => { view.dom.document.visibilityState = "hidden"; view.dom.document.dispatchEvent(new MinimalEvent("visibilitychange")); });
      if (mode === "target") await view.navigate("?restore_request_id=restore-b");
      if (mode === "adapter") await view.rerender(port({ getRestore: async () => { throw failure(403); } }).api);
      if (mode === "unmount") await view.unmount();
      assert.equal(signal.aborted, true);
      await view.act(async () => { if (rejects) gate.reject(failure(503)); else gate.resolve(restoreResponse(restore())); });
      assert.equal(writes, 1); assert.doesNotMatch(visibleText(view.container), /restore-a|상태completed/u);
      assert.doesNotMatch(view.container.textContent, /synthetic-password|synthetic-new-token|S42_표시금지/u);
    } finally { gate.resolve(restoreResponse(restore())); await view.cleanup(); }
  }
});

test("S44 기존복구 제출 전 입력은 visibility·대상·API 교체에서 즉시 폐기한다", async () => {
  for (const mode of ["visibility", "target", "adapter"]) {
    const p = actionPort(), view = await render("?restore_request_id=restore-a", p.api);
    try {
      await click(view, "복구 실행 준비"); const field = passwordField(view); field.value = "unsubmitted-password";
      if (mode === "visibility") await view.act(async () => { view.dom.document.visibilityState = "hidden"; view.dom.document.dispatchEvent(new MinimalEvent("visibilitychange")); });
      if (mode === "target") await view.navigate("?restore_request_id=restore-b");
      if (mode === "adapter") await view.rerender(actionPort().api);
      assert.equal(field.value, ""); assert.equal(p.calls.filter(c => ["stepup", "execute", "cancel"].includes(c[0])).length, 0);
    } finally { await view.cleanup(); }
  }
});

test("M1 운영 홈은 16관측의 partial/null과 남은 미수집 범위를 구분한다", async () => {
  const data = operations();
  data.observations[12] = { ...data.observations[12], state: "partial", value: 7, observed_at: "2026-10-19T00:00:00Z", safe_code: "METRIC_PARTIAL" };
  const p = homePort();
  p.api.getOperationsStatus = async workspace => { p.calls.push(["status", workspace]); return data; };
  const view = await render("", p.api);
  try {
    const text = visibleText(view.container);
    assert.match(text, /기존 5종 상태/u);
    assert.match(text, /보고된 Prompt Token.*부분 기록.*7/u);
    assert.match(text, /보고된 Total Token.*미수집/u);
    assert.deepEqual(findElements(view.container, node => node.tagName === "LI" && /^(문서|Studio|Object) · /u.test(node.textContent))
      .map(node => node.textContent), ["문서 · 미집계 · 실행 미확인", "Studio · 미집계 · 실행 미확인", "Object · 미집계 · 실행 미확인"]);
    for (const kind of ["문서", "Studio", "Object"]) for (const metric of ["CPU 할당", "메모리 한도", "메모리 사용", "영속 저장 여유"]) {
      const label = `${kind} · ${metric}`;
      const term = findElements(view.container, node => node.tagName === "DT" && node.textContent === label);
      assert.equal(term.length, 1, label);
      assert.equal(term[0].parentNode.textContent, `${label}미집계 · 값 미확인`);
    }
    const paragraphs = findElements(view.container, node => node.tagName === "P").map(node => node.textContent);
    assert.ok(paragraphs.includes("비용: 미집계 · 확정 금액 없음"));
    assert.ok(paragraphs.includes("배포·Rollback: 확인된 기록 미수집"));
    assert.ok(paragraphs.includes("미구현 수집: Readiness 자동 재처리"));
    assert.ok(paragraphs.includes("lease와 Index 기록 수는 실제 생존이나 Build 진행률이 아닙니다. 미집계·미보고는 정상 0건이 아닙니다."));
    assert.doesNotMatch(text, /처리 중|프로세스 관측|확정 금액 0|USD 0|0 bytes|0 millicores/u);
    assert.deepEqual(p.calls, homeCalls);
    assert.deepEqual(p.writes, []);
    assert.equal(findElements(view.container, node => node.getAttribute?.("data-metric-id")).length, 16);
    assert.doesNotMatch(text, /S42_표시금지/u);
  } finally { await view.cleanup(); }
});

test("S44 실제 기본 adapter는 홈 GET만 호출하고 목록 선택은 정확 backup GET으로 이어진다", async () => {
  const previous = globalThis.fetch, calls = [];
  const statusPath='/bff/api/workspaces/workspace-a/operations/status?include_readiness=1&include_capacity=1';
  globalThis.fetch = async (url, init) => {
    calls.push([url, init.method, init.credentials, init.cache]);
    if (url === "/bff/api/session") return Response.json(session().payload);
    if (url === statusPath) {
      const data=operations();
      data.event_counts.push(...['waiting_text_generation','waiting_image_understanding','waiting_document_parsing',
        'waiting_speech_to_text','waiting_role_unknown','retry_suppressed_contexts_24h'].map(metric_id=>({
          metric_id,state:'not_collected',value:null,unit:'count',observed_at:null,received_at:null,
          coverage:'actor_accessible_records',safe_code:'TELEMETRY_NOT_COLLECTED',window_start:null,window_end:null})));
      return Response.json({data,meta:{trace_id:'trace-ops',workspace_id:'workspace-a'}});
    }
    if (url === "/bff/api/backups?workspace_id=workspace-a") return Response.json(response([backup({ raw_text: canary })]).payload);
    assert.equal(url, "/bff/api/backups/backup-a");
    return Response.json(response(backup()).payload);
  };
  let view;
  try {
    view = await render("");
    assert.deepEqual(calls.map(c => c[0]), ["/bff/api/session", statusPath, "/bff/api/backups?workspace_id=workspace-a", "/bff/api/session"]);
    const link = findElements(view.container, n => n.tagName === "A" && n.getAttribute("href") === "?backup_id=backup-a")[0];
    assert.ok(link, "실제 목록의 정확 백업 링크");
    await view.navigate(link.getAttribute("href"));
    assert.deepEqual(calls.slice(4).map(c => c[0]), ["/bff/api/session", "/bff/api/backups/backup-a", "/bff/api/session"]);
    for (const call of calls) assert.deepEqual(call.slice(1), ["GET", "same-origin", "no-store"]);
    assert.match(visibleText(view.container), /backup-a/u); noMutations(view);
  } finally { if (view) await view.cleanup(); globalThis.fetch = previous; }
});

test("S44 홈 상태·목록의 독립 장애를 빈 목록 성공으로 바꾸지 않고 명시 재시도한다", async () => {
  for (const part of ["status", "list"]) {
    let rejected = true;
    const p = homePort();
    if (part === "status") p.api.getOperationsStatus = async () => { if (rejected) throw failure(503); return operations(); };
    else p.api.listBackups = async () => { if (rejected) throw failure(403); return response([]); };
    const view = await render("", p.api);
    try {
      const text = visibleText(view.container);
      assert.match(text, part === "status" ? /확인할 수 없/u : /권한/u);
      if (part === "status") assert.match(text, /조회된 백업이 없습니다/u);
      else { assert.match(text, /API_READY/u); assert.doesNotMatch(text, /조회된 백업이 없습니다/u); }
      rejected = false; await click(view, "다시 조회");
      assert.match(visibleText(view.container), /API_READY/u);
      assert.match(visibleText(view.container), /조회된 백업이 없습니다/u);
      noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S44 추가 회귀: status 또는 list의 401은 양쪽 본문과 백업 링크를 모두 차단한다", async () => {
  for (const part of ["status", "list"]) {
    const p = homePort();
    p.api.getOperationsStatus = async workspace => {
      p.calls.push(["status", workspace]);
      if (part === "status") throw failure(401, "ACCESS_INVALID");
      return operations();
    };
    p.api.listBackups = async workspace => {
      p.calls.push(["list", workspace]);
      if (part === "list") throw failure(401, "ACCESS_INVALID");
      return response([backup()]);
    };
    const view = await render("", p.api);
    try {
      assert.deepEqual(p.calls, homeCalls);
      assert.match(visibleText(view.container), /로그인/u);
      assert.doesNotMatch(visibleText(view.container), /API_READY|PROVIDER_CONFIGURATION_REQUIRED|backup-a|현재 Workspace 운영 상태|백업 목록/u);
      assert.equal(findElements(view.container, node => node.tagName === "A").length, 0);
      noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S44 추가 회귀: 중복 backup ID는 목록 전체를 거부하고 독립 상태만 유지한다", async () => {
  const p = homePort({ listBackups: async workspace => {
    p.calls.push(["list", workspace]); return response([backup(), backup()]);
  } });
  const view = await render("", p.api);
  try {
    assert.deepEqual(p.calls, homeCalls);
    assert.match(visibleText(view.container), /API_READY/u);
    assert.match(visibleText(view.container), /백업 목록:.*확인할 수 없/u);
    assert.doesNotMatch(visibleText(view.container), /backup-a|조회된 백업이 없습니다/u);
    assert.equal(findElements(view.container, node => node.tagName === "A").length, 0);
    noMutations(view, p.writes);
  } finally { await view.cleanup(); }
});

test("S44 추가 회귀: hash-only 주소는 session·status·list GET 없이 거부한다", async () => {
  const p = homePort(), view = await render("#fragment", p.api);
  try {
    assert.match(visibleText(view.container), /올바른 복구 대상 주소가 아닙니다/u);
    assert.deepEqual(p.calls, []);
    assert.equal(findElements(view.container, node => node.tagName === "A").length, 0);
    noMutations(view, p.writes);
  } finally { await view.cleanup(); }
});

test("S44 홈은 잘못된 session에서 GET0·조회 전후 모든 scope 교체에서 본문0이다", async () => {
  for (const changes of [{ workspace_id: null }, { user_id: "" }, { session_id: null }]) {
    const p = homePort({ getSession: async () => { p.calls.push(["session"]); return session(changes); } });
    const view = await render("", p.api);
    try { assert.deepEqual(p.calls, [["session"]]); assert.match(visibleText(view.container), /확인할 수 없/u); noMutations(view, p.writes); }
    finally { await view.cleanup(); }
  }
  for (const changes of [{ workspace_id: "workspace-b" }, { user_id: "reader-b" }, { tenant_id: "tenant-b" }, { session_id: "session-b" }]) {
    let count = 0;
    const p = homePort({ getSession: async () => ++count === 1 ? session() : session(changes) });
    const view = await render("", p.api);
    try { assert.match(visibleText(view.container), /권한/u); assert.doesNotMatch(visibleText(view.container), /API_READY|조회된 백업/u); noMutations(view, p.writes); }
    finally { await view.cleanup(); }
  }
});

test("S44 홈 목록의 다른 범위·malformed 응답을 버리고 전체 상태를 합성하지 않는다", async () => {
  for (const data of [null, {}, [backup({ tenant_id: "tenant-b" })], [backup({ workspace_id: "workspace-b" })], [backup({ backup_id: canary, version: 0 })]]) {
    const p = homePort({ listBackups: async () => response(data) }), view = await render("", p.api);
    try {
      assert.match(visibleText(view.container), /API_READY/u);
      assert.match(visibleText(view.container), /확인할 수 없/u);
      assert.doesNotMatch(view.container.textContent, /backup-a|tenant-b|workspace-b|S42_표시금지/u);
      assert.doesNotMatch(visibleText(view.container), /조회된 백업이 없습니다/u); noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S44 홈의 늦은 응답은 target 이동·pagehide·adapter 교체·unmount 뒤 복원되지 않는다", async () => {
  for (const mode of ["target", "pagehide", "adapter", "unmount"]) {
    const late = deferred(), p = homePort({ listBackups: () => late.promise }), view = await render("", p.api);
    try {
      if (mode === "target") await view.navigate("?restore_request_id=restore-a");
      if (mode === "pagehide") await view.act(async () => view.browser.dispatchEvent(new MinimalEvent("pagehide")));
      if (mode === "adapter") await view.rerender(homePort().api);
      if (mode === "unmount") await view.unmount();
      await view.act(async () => late.resolve(response([backup()])));
      assert.doesNotMatch(visibleText(view.container), /backup-a/u);
      if (mode === "target") assert.match(visibleText(view.container), /restore-a/u);
      if (mode === "adapter") assert.match(visibleText(view.container), /조회된 백업이 없습니다/u);
      noMutations(view, p.writes);
    } finally { late.resolve(response([])); await view.cleanup(); }
  }
});

test("S44 홈 표시도 pagehide·focus 직후 숨기고 session 재확인 전 되살리지 않는다", async () => {
  const gate = deferred(); let count = 0;
  const p = homePort({ getSession: () => ++count <= 2 ? session() : gate.promise }), view = await render("", p.api);
  try {
    assert.match(visibleText(view.container), /API_READY/u);
    await view.act(async () => {
      view.browser.dispatchEvent(new MinimalEvent("pagehide"));
      assert.doesNotMatch(visibleText(view.container), /API_READY/u);
      view.browser.dispatchEvent(new MinimalEvent("focus"));
      assert.doesNotMatch(visibleText(view.container), /API_READY/u);
    });
    await view.act(async () => gate.resolve(session({ workspace_id: null })));
    assert.doesNotMatch(visibleText(view.container), /API_READY/u); noMutations(view, p.writes);
  } finally { gate.resolve(session()); await view.cleanup(); }
});

test("S44 홈은 정상 session 전후를 확인하고 실제 5상태·빈 백업 목록을 표시한다", async () => {
  const p = homePort(), view = await render("", p.api);
  try {
    assert.match(view.container.textContent, /운영 상태·복구/u);
    assert.deepEqual(p.calls, homeCalls);
    const text = visibleText(view.container);
    for (const code of ["PROVIDER_CONFIGURATION_REQUIRED", "API_READY", "STORAGE_READY", "SYNC_PENDING", "QUEUE_ATTENTION_REQUIRED"]) assert.ok(text.includes(code));
    assert.match(text, /백업은 자료 선택·계보 사전확인·명시 동의 후 독립 보존 준비가 된 서버에서만 생성됩니다/u);
    assert.match(text, /v3 백업의 등록 목적지 Preview와 별도 인증 Execute·Cancel은 정확 ID 상세에서 사용합니다/u);
    assert.match(text, /독립 전체 DB 복원은 지원하지 않습니다/u);
    assert.doesNotMatch(text, /백업 생성·복구 실행은 아직 연결되지 않았습니다/u);
    assert.match(text, /조회된 백업이 없습니다/u);
    assert.match(text, /설정 상태/u); assert.match(text, /모델.*호출.*아/u);
    assert.doesNotMatch(text, /실패\s*0\s*건/u);
    noMutations(view, p.writes);
  } finally { await view.cleanup(); }
});

test("S42 기본 recoveryApi로 Session 뒤 정확 backup GET만 읽고 민감 추가필드는 표시하지 않는다", async () => {
  const priorFetch = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push([url, init.method, init.credentials, init.cache]);
    if (url === "/bff/api/session") return Response.json(session().payload);
    assert.equal(url, "/bff/api/backups/backup-a");
    return Response.json({ data: backup({ raw_text: canary, secret: canary, destination: canary }), meta: { trace_id: "trace-read" } });
  };
  let view;
  try {
    view = await render("?backup_id=backup-a");
    assert.deepEqual(calls, [["/bff/api/session", "GET", "same-origin", "no-store"],
      ["/bff/api/backups/backup-a", "GET", "same-origin", "no-store"],
      ["/bff/api/session", "GET", "same-origin", "no-store"]]);
    assert.match(visibleText(view.container), /backup-a/u);
    assert.match(visibleText(view.container), /tenant-a/u); assert.match(visibleText(view.container), /workspace-a/u);
    assert.match(visibleText(view.container), /버전\s*[:·]?\s*3|version\s*[:·]?\s*3/iu);
    assert.match(visibleText(view.container), /ready|준비|완료/u); noMutations(view);
  } finally { if (view) await view.cleanup(); globalThis.fetch = priorFetch; }
});

test("S42 restore_request_id는 정상 Session 확정 전 GET하지 않고 정확 request_id를 읽기 선택한다", async () => {
  const gate = deferred(), confirmation = deferred(); let sessions = 0;
  const p = port({ getSession: () => { p.calls.push(["session"]); return ++sessions === 1 ? gate.promise : confirmation.promise; } });
  const view = await render("?restore_request_id=restore-a", p.api);
  try {
    assert.deepEqual(p.calls, [["session"]]); assert.doesNotMatch(visibleText(view.container), /restore-a/u);
    const current = session({ workspace_id: "primary-other-workspace", recovery_operations: [] });
    await view.act(async () => gate.resolve(current));
    assert.deepEqual(p.calls, [["session"], ["restore", "restore-a"], ["session"]]);
    assert.doesNotMatch(visibleText(view.container), /restore-a/u, "대상 GET 성공만으로 현재 Session 재확인 전에 표시하지 않음");
    await view.act(async () => confirmation.resolve(current));
    assert.match(visibleText(view.container), /restore-a/u);
    assert.match(visibleText(view.container), /workspace-a/u, "실제 대상 Workspace를 표시함");
    assert.match(visibleText(view.container), /버전\s*[:·]?\s*6|version\s*[:·]?\s*6/iu);
    assert.match(visibleText(view.container), /completed|완료/u); noMutations(view, p.writes);
  } finally { gate.resolve(session()); confirmation.resolve(session()); await view.cleanup(); }
});

test("S42 표준 URL 인코딩된 안전 대상 ID도 정확한 원 ID로 조회한다", async () => {
  for (const [query, id, kind] of [["?backup_id=backup%3Aread", "backup:read", "backup"],
    ["?restore_request_id=restore%3Aread", "restore:read", "restore"]]) {
    const p = port({
      getBackup: async value => { p.calls.push(["backup", value]); return response(backup({ backup_id: id })); },
      getRestore: async value => { p.calls.push(["restore", value]); return response(restore({ request_id: id })); },
    });
    const view = await render(query, p.api);
    try {
      assert.deepEqual(p.calls, [["session"], [kind, id], ["session"]]);
      assert.match(visibleText(view.container), new RegExp(id, "u"));
      noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S42 중복·동시·미정의 query 및 오염 ID는 target GET을 보내지 않는다", async () => {
  for (const search of ["?backup_id=a&backup_id=b", "?backup_id=a&restore_request_id=b", "?unknown=a",
    "?backup_id=a&redirect=https://evil.example", "?restore_request_id=", "?backup_id=https://evil.example/a",
    "?backup_id=..", "?backup_id=a%2Fb", "?backup_id=a%5Cb", "?backup_id=a%0Ab", "?backup_id=%20a",
    "?restore_request_id=a%23b"]) {
    const p = port(), view = await render(search, p.api);
    try {
      assert.equal(p.calls.some(([method]) => method !== "session"), false, search);
      assert.doesNotMatch(visibleText(view.container), /backup-a|restore-a/u); noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S42 대상 404·401·403·서버 장애를 구별하고 오류 원문·이전 본문을 표시하지 않는다", async () => {
  for (const [status, code, label] of [[404, "RESOURCE_UNAVAILABLE", /찾을 수 없|존재하지 않|대상.*없|404/u],
    [401, "ACCESS_INVALID", /로그인|인증|401/u], [403, "FORBIDDEN", /권한|접근.*거부|403/u],
    [503, "RESOURCE_UNAVAILABLE", /서비스|일시|잠시|503/u],
    ["network", "RESOURCE_UNAVAILABLE", /서비스|일시|잠시|503/u]]) {
    const priorFetch = globalThis.fetch, calls = [];
    globalThis.fetch = async (url, init = {}) => {
      calls.push([url, init.method]);
      if (url === "/bff/api/session") return Response.json(session().payload);
      assert.equal(url, "/bff/api/backups/backup-a");
      if (status === "network") throw new Error(canary);
      // 실제 recoveryApi가 status를 잃으면 동일 코드의 404/503 표시 구별이 실패해야 한다.
      return Response.json({ error: { code, message: canary, details: { raw_text: canary }, trace_id: "trace-safe-error" } }, { status });
    };
    let view;
    try {
      view = await render("?backup_id=backup-a");
      assert.deepEqual(calls, [["/bff/api/session", "GET"], ["/bff/api/backups/backup-a", "GET"]]);
      assert.match(visibleText(view.container), label);
      assert.doesNotMatch(view.container.textContent, /S42_표시금지|manifest_digest/u); noMutations(view);
    } finally { if (view) await view.cleanup(); globalThis.fetch = priorFetch; }
  }
});

test("S42 ID·tenant·Workspace 불일치와 잘못된 DTO는 정상 대상 본문이 아니다", async () => {
  for (const data of [backup({ backup_id: "wrong-target" }), backup({ tenant_id: "other-tenant" }),
    backup({ workspace_id: "https://evil.example" }), backup({ state: canary }), backup({ version: "3" }), null]) {
    const p = port({ getBackup: async id => { p.calls.push(["backup", id]); return response(data); } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.deepEqual(p.calls.slice(0, 2), [["session"], ["backup", "backup-a"]]);
      assert.match(visibleText(view.container), /RESOURCE_UNAVAILABLE|응답|서비스|확인할 수 없/u);
      assert.doesNotMatch(view.container.textContent, /wrong-target|other-tenant|https:\/\/evil\.example|S42_표시금지/u);
      noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S42 Session 거부·잘못된 scope는 GET 전에 차단하고 target 뒤 주체 변경도 표시하지 않는다", async () => {
  for (const getSession of [async () => session({ user_id: "" }), async () => session({ tenant_id: null }),
    async () => session({ workspace_id: "" }),
    async () => { throw failure(401, "ACCESS_INVALID"); }]) {
    const p = port({ getSession: async () => { p.calls.push(["session"]); return getSession(); } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.deepEqual(p.calls, [["session"]]); assert.doesNotMatch(visibleText(view.container), /backup-a/u);
      noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
  for (const changes of [{ user_id: "reader-b" }, { tenant_id: "tenant-b" }, { session_id: "session-b" }]) {
    let reads = 0;
    const p = port({ getSession: async () => { p.calls.push(["session"]); return ++reads === 1 ? session() : session(changes); } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.deepEqual(p.calls, [["session"], ["backup", "backup-a"], ["session"]]);
      assert.doesNotMatch(visibleText(view.container), /backup-a/u); noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S42 popstate의 새 선택 이후 늦은 이전 GET은 새 restore 본문을 덮지 않는다", async () => {
  const late = deferred(), p = port({ getBackup: id => { p.calls.push(["backup", id]); return late.promise; } });
  const view = await render("?backup_id=backup-a", p.api);
  try {
    await view.navigate("?restore_request_id=restore-a");
    assert.match(visibleText(view.container), /restore-a/u);
    await view.act(async () => late.resolve(response(backup())));
    assert.match(visibleText(view.container), /restore-a/u);
    assert.doesNotMatch(visibleText(view.container), /backup-a/u);
    assert.deepEqual(p.calls, [["session"], ["backup", "backup-a"], ["session"], ["restore", "restore-a"], ["session"]]);
    noMutations(view, p.writes);
  } finally { late.resolve(response(backup())); await view.cleanup(); }
});

test("S42 adapter 교체는 새 Session부터 확인하고 이전 adapter의 늦은 성공을 버린다", async () => {
  const late = deferred(), old = port({ getBackup: () => late.promise });
  const next = port({ getBackup: async id => { next.calls.push(["backup", id]); return response(backup({ state: "failed" })); } });
  const view = await render("?backup_id=backup-a", old.api);
  try {
    await view.rerender(next.api);
    assert.deepEqual(next.calls, [["session"], ["backup", "backup-a"], ["session"]]);
    assert.match(visibleText(view.container), /failed|실패/u);
    await view.act(async () => late.resolve(response(backup({ state: "ready", raw_text: canary }))));
    assert.match(visibleText(view.container), /failed|실패/u); noMutations(view, old.writes); noMutations(view, next.writes);
  } finally { late.resolve(response(backup())); await view.cleanup(); }
});

test("S42 pagehide 뒤 늦은 성공은 숨긴 본문을 되살리지 않으며 pageshow는 새 Session을 기다린다", async () => {
  const late = deferred(), gate = deferred(); let sessions = 0, targets = 0;
  const p = port({ getSession: () => ++sessions <= 2 ? Promise.resolve(session()) : gate.promise,
    getBackup: () => ++targets === 1 ? Promise.resolve(response(backup())) : late.promise });
  const view = await render("?backup_id=backup-a", p.api);
  try {
    assert.match(visibleText(view.container), /backup-a/u);
    await view.act(async () => {
      view.browser.dispatchEvent(new MinimalEvent("pagehide"));
      assert.doesNotMatch(visibleText(view.container), /backup-a/u, "flush 이전에도 숨김");
      view.browser.dispatchEvent(new MinimalEvent("pageshow"));
      assert.doesNotMatch(visibleText(view.container), /backup-a/u);
    });
    assert.equal(sessions, 3); assert.equal(targets, 1);
    await view.act(async () => gate.resolve(session()));
    await view.act(async () => view.browser.dispatchEvent(new MinimalEvent("pagehide")));
    await view.act(async () => late.resolve(response(backup())));
    assert.doesNotMatch(visibleText(view.container), /backup-a/u); noMutations(view, p.writes);
  } finally { gate.resolve(session()); late.resolve(response(backup())); await view.cleanup(); }
});

test("S42 visibility 재검증은 계정·tenant·Workspace 변경 즉시 이전 본문을 숨기고 새 scope 불일치를 거부한다", async () => {
  for (const changes of [{ user_id: "reader-b" }, { tenant_id: "tenant-b" },
    { session_id: "session-b" }, { workspace_id: "workspace-b" }]) {
    const gate = deferred(); let sessions = 0;
    const p = port({ getSession: () => ++sessions <= 2 ? Promise.resolve(session()) : gate.promise,
      getBackup: async id => { p.calls.push(["backup", id]); return response(backup()); } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.match(visibleText(view.container), /backup-a/u);
      await view.act(async () => {
        if (changes.session_id) {
          view.browser.dispatchEvent(new MinimalEvent("focus"));
          assert.doesNotMatch(visibleText(view.container), /backup-a/u, "focus 재확인 전 즉시 숨김");
        } else {
          view.dom.document.visibilityState = "hidden";
          view.dom.document.dispatchEvent(new MinimalEvent("visibilitychange"));
          assert.doesNotMatch(visibleText(view.container), /backup-a/u, "숨김 이벤트에서 즉시 폐기");
          view.dom.document.visibilityState = "visible";
          view.dom.document.dispatchEvent(new MinimalEvent("visibilitychange"));
        }
      });
      assert.equal(sessions, 3);
      assert.doesNotMatch(visibleText(view.container), /backup-a/u, "새 Session 미확정");
      // 계정만 바뀌면 서버 GET이 현재 권한을 결정한다. 여기서는 새 계정의 실제 거부 포트를 준다.
      if (changes.user_id || changes.session_id) p.api.getBackup = async () => { throw failure(403, "FORBIDDEN"); };
      await view.act(async () => gate.resolve(session(changes)));
      if (changes.workspace_id) {
        assert.match(visibleText(view.container), /backup-a/u, "primary Workspace 변경만으로 서버 허용 대상을 차단하지 않음");
        assert.match(visibleText(view.container), /workspace-a/u);
      } else assert.doesNotMatch(visibleText(view.container), /backup-a/u);
      noMutations(view, p.writes);
    } finally { gate.resolve(session(changes)); await view.cleanup(); }
  }
});

test("S42 unmount 뒤 Session·대상 지연 응답은 GET·본문 복원을 새로 만들지 않는다", async () => {
  for (const phase of ["session", "target"]) {
    const late = deferred(), p = port(phase === "session" ? {
      getSession: () => { p.calls.push(["session"]); return late.promise; },
    } : { getBackup: id => { p.calls.push(["backup", id]); return late.promise; } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.deepEqual(p.calls, phase === "session" ? [["session"]] : [["session"], ["backup", "backup-a"]]);
      await view.unmount(); const before = [...p.calls];
      await view.act(async () => late.resolve(phase === "session" ? session() : response(backup())));
      assert.deepEqual(p.calls, before); assert.equal(view.container.textContent, ""); noMutations(view, p.writes);
    } finally { late.resolve(phase === "session" ? session() : response(backup())); await view.cleanup(); }
  }
});
