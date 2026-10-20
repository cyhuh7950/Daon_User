import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
test("M2-S system API uses same-origin signal and rejects malformed DTO",async()=>{
  assert.equal(typeof recoveryApi.getSystemOperationsStatus,"function");
  const previous=globalThis.fetch,calls=[];const controller=new AbortController();
  globalThis.fetch=async(path,init)=>{calls.push([path,init]);return Response.json({data:{schema_version:1,secret:"PRIVATE_CANARY"}});};
  try{
    await assert.rejects(()=>recoveryApi.getSystemOperationsStatus({signal:controller.signal}),/RESOURCE_UNAVAILABLE/u);
    assert.equal(calls[0][0],"/bff/api/system/operations/status");
    assert.deepEqual([calls[0][1].credentials,calls[0][1].cache,calls[0][1].signal],["same-origin","no-store",controller.signal]);
  }finally{globalThis.fetch=previous;}
});

import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";
import { parseRecoverySessionContext, parseSystemOperationsStatus, recoveryApi, resolveRecoverySession } from "../../apps/web/lib/recovery-api.js";

function hostSystemStatus(){
  const kinds=['document','studio','object'];
  const common={state:'not_collected',observed_at:null,received_at:null,safe_code:'TELEMETRY_NOT_COLLECTED'};
  return {schema_version:1,checked_at:'2026-10-20T00:00:00Z',
    health:['api','database','object_storage'].map(component_id=>({component_id,state:'ready',checked_at:'2026-10-20T00:00:00Z',safe_code:component_id.toUpperCase()+'_READY'})),
    worker_samples:kinds.map(worker_kind=>({...common,worker_kind,process_state:'unknown',poll_started_at:null,poll_finished_at:null,active_started_at:null})),
    resource_samples:[...kinds.flatMap(worker_kind=>['cpu_quota_millicores','memory_limit_bytes','memory_current_bytes','runtime_available_bytes'].map((metric_id,i)=>({...common,worker_kind,metric_id,unit:i===0?'millicores':'bytes',value:null}))),
      ...[['cpu_logical_millicores','millicores'],['cpu_usage_basis_points','basis_points'],['memory_total_bytes','bytes'],['memory_available_bytes','bytes']].map(([metric_id,unit])=>({...common,worker_kind:'server_host',metric_id,unit,value:null}))],
    deployment_summary:{...common,operation:null,code_revision:null,image_digest:null,previous_image_digest:null,result:null},unimplemented:[]};
}

test('S44 system host contract accepts exact opt-in sixteen and no private host fields',async()=>{
  const data=hostSystemStatus();
  assert.equal(parseSystemOperationsStatus(data,{includeCapacity:true}).resource_samples.length,16);
  assert.throws(()=>parseSystemOperationsStatus(data),/RESOURCE_UNAVAILABLE/u);
  const previous=globalThis.fetch,calls=[];
  globalThis.fetch=async(path,init)=>{calls.push([path,init]);return Response.json({data});};
  try{
    assert.equal((await recoveryApi.getSystemOperationsStatus({includeCapacity:true})).resource_samples.length,16);
    assert.equal(calls[0][0],'/bff/api/system/operations/status?include_capacity=1');
    for(const [index,key,value] of [[12,'boot_id','PRIVATE_BOOT'],[13,'unit','percent'],[15,'value',true]]){
      const broken=structuredClone(data);broken.resource_samples[index][key]=value;
      assert.throws(()=>parseSystemOperationsStatus(broken,{includeCapacity:true}),/RESOURCE_UNAVAILABLE/u);
    }
  }finally{globalThis.fetch=previous;}
});

function labelContext(sources) {
  return Response.json({ data: { notebook_id: "notebook-a", sources,
    knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [],
    source_deletion_requests: [], conversation: null }, meta: { workspace_id: "workspace-a", trace_id: "trace-label" } },
  { headers: { ETag: '"notebook-binding:7"' } });
}
const labelSource = (source_id, source_version_id, filename = "동일이름.txt") => ({
  source_id, source_version_id, filename, source_state: "ready", processing_state: "completed", job_state: "completed",
});
const labelList = (sources, workspace_id = "workspace-a") => Response.json({ data: { sources }, meta: { workspace_id, trace_id: "trace-label" } });

test("S44 label 정확 두ID만 filename 보강하고 Context 집합 순서 bindingEtag를 보존한다", async () => {
  const previous = globalThis.fetch, calls = [], controller = new AbortController();
  globalThis.fetch = async (url, init) => {
    calls.push([url, init]);
    return url.endsWith("/context") ? labelContext([
      { source_id: "source-b", source_version_id: "version-b" }, { source_id: "source-a", source_version_id: "version-a" },
    ]) : labelList([labelSource("source-a", "version-a"), labelSource("source-extra", "version-extra", "추가금지.txt"), labelSource("source-b", "version-b")]);
  };
  try {
    assert.deepEqual(await recoveryApi.getSelectionSources("workspace-a", "notebook-a", { signal: controller.signal }), {
      notebookId: "notebook-a", bindingEtag: '"notebook-binding:7"', sources: [
        { sourceId: "source-b", versionId: "version-b", filename: "동일이름.txt" },
        { sourceId: "source-a", versionId: "version-a", filename: "동일이름.txt" },
      ],
    });
    assert.deepEqual(calls.map(([url]) => url).sort(), ["/bff/api/workspaces/workspace-a/notebooks/notebook-a/context",
      "/bff/api/workspaces/workspace-a/sources?notebook_id=notebook-a"]);
    for (const [, init] of calls) assert.deepEqual([init.method, init.credentials, init.cache, init.signal], ["GET", "same-origin", "no-store", controller.signal]);
  } finally { globalThis.fetch = previous; }
});

test("S44 label 목록누락만 ID fallback하며 목록만 있는 자료는 선택에 추가하지 않는다", async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async url => url.endsWith("/context") ? labelContext([{ source_id: "source-a", source_version_id: "version-a" }])
    : labelList([labelSource("source-other", "version-other")]);
  try {
    assert.deepEqual(await recoveryApi.getSelectionSources("workspace-a", "notebook-a"), {
      notebookId: "notebook-a", bindingEtag: '"notebook-binding:7"', sources: [{ sourceId: "source-a", versionId: "version-a" }],
    });
  } finally { globalThis.fetch = previous; }
});

test("S44 label Context 동일Source 복수Version과 최신목록은 정확pair만 보강한다", async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async url => url.endsWith("/context") ? labelContext([
    { source_id: "source-a", source_version_id: "version-1" },
    { source_id: "source-a", source_version_id: "version-2" },
  ]) : labelList([labelSource("source-a", "version-2", "현재파일.txt")]);
  try {
    assert.deepEqual(await recoveryApi.getSelectionSources("workspace-a", "notebook-a"), {
      notebookId: "notebook-a", bindingEtag: '"notebook-binding:7"', sources: [
        { sourceId: "source-a", versionId: "version-1" },
        { sourceId: "source-a", versionId: "version-2", filename: "현재파일.txt" },
      ],
    });
  } finally { globalThis.fetch = previous; }
});

test("S44 label 중복 모호 다른Version Source불일치는 첫항목 채택 없이 거부한다", async () => {
  const previous = globalThis.fetch;
  try {
    for (const rows of [
      [labelSource("source-a", "version-a"), labelSource("source-a", "version-a")],
      [labelSource("source-a", "version-new")], [labelSource("source-other", "version-a")],
      [labelSource("source-a", "version-a"), labelSource("source-a", "version-new")],
      [labelSource("source-a", "version-a"), labelSource("source-other", "version-a")],
    ]) {
      globalThis.fetch = async url => url.endsWith("/context") ? labelContext([{ source_id: "source-a", source_version_id: "version-a" }]) : labelList(rows);
      await assert.rejects(() => recoveryApi.getSelectionSources("workspace-a", "notebook-a"));
    }
  } finally { globalThis.fetch = previous; }
});

test("S44 label 기존 filename validator와 Workspace 검증을 유지한다", async () => {
  const previous = globalThis.fetch;
  try {
    for (const [filename, workspace] of [["../private.txt", "workspace-a"], ["bad\\path.txt", "workspace-a"],
      ["bad\nname.txt", "workspace-a"], ["x".repeat(256), "workspace-a"], ["정상.txt", "workspace-other"]]) {
      globalThis.fetch = async url => url.endsWith("/context") ? labelContext([{ source_id: "source-a", source_version_id: "version-a" }])
        : labelList([labelSource("source-a", "version-a", filename)], workspace);
      await assert.rejects(() => recoveryApi.getSelectionSources("workspace-a", "notebook-a"));
    }
  } finally { globalThis.fetch = previous; }
});

test("S44 label 목록401 403 취소는 filename 누락으로 삼키지 않는다", async () => {
  const previous = globalThis.fetch;
  try {
    for (const status of [401, 403]) {
      globalThis.fetch = async url => url.endsWith("/context") ? labelContext([{ source_id: "source-a", source_version_id: "version-a" }])
        : Response.json({ error: { code: "SOURCE_ACCESS_DENIED" } }, { status });
      await assert.rejects(() => recoveryApi.getSelectionSources("workspace-a", "notebook-a"), error => error.status === status);
    }
    const controller = new AbortController();
    globalThis.fetch = async url => {
      if (url.endsWith("/context")) return labelContext([{ source_id: "source-a", source_version_id: "version-a" }]);
      controller.abort();
      return labelList([labelSource("source-a", "version-a")]);
    };
    await assert.rejects(() => recoveryApi.getSelectionSources("workspace-a", "notebook-a", { signal: controller.signal }), error => error.name === "AbortError");
  } finally { globalThis.fetch = previous; }
});

test("R4 v3 사전확인과 명시동의 exact6만 same-origin 전송하고 혼합/무동의는 fetch0", async () => {
  assert.equal(typeof recoveryApi.previewBackupSelection, "function");
  assert.equal(typeof recoveryApi.createLineageBackup, "function");
  const previous = globalThis.fetch, calls = [], controller = new AbortController();
  globalThis.fetch = async (url, init) => { calls.push([url, init]); return Response.json({ data: {} }); };
  try {
    const preflight = { workspace_id: "workspace-a", notebook_id: "notebook-a", source_version_ids: ["version-b", "version-a"], lineage_mode: "exact" };
    await recoveryApi.previewBackupSelection(preflight, { signal: controller.signal });
    const create = { ...preflight, trigger: "manual", closure_consent: { include_prior_versions: true, closure_fingerprint: "a".repeat(64) } };
    await recoveryApi.createLineageBackup(create, "r4-lineage-create-key", { signal: controller.signal });
    assert.deepEqual(calls.map(([url]) => url), ["/bff/api/backups/selection-previews", "/bff/api/backups"]);
    assert.deepEqual(JSON.parse(calls[0][1].body), { ...preflight, source_version_ids: ["version-a", "version-b"] });
    assert.deepEqual(JSON.parse(calls[1][1].body), { ...create, source_version_ids: ["version-a", "version-b"] });
    for (const [, init] of calls) assert.deepEqual([init.credentials, init.cache, init.signal], ["same-origin", "no-store", controller.signal]);
    for (const change of [{ closure_consent: { ...create.closure_consent, include_prior_versions: false } },
      { closure_consent: { ...create.closure_consent, extra: true } }, { closure_consent: { include_prior_versions: true, closure_fingerprint: "bad" } },
      { objects: [] }, { source_version_ids: ["version-a", "version-a"] }, { lineage_mode: "loose" }]) {
      await assert.rejects(async () => recoveryApi.createLineageBackup({ ...create, ...change }, "r4-lineage-create-key"));
    }
    assert.equal(calls.length, 2);
  } finally { globalThis.fetch = previous; }
});

test("R4 등록목적지 GET와 Preview exact4는 abort/backup대상 token/멱등을 보존한다", async () => {
  assert.equal(typeof recoveryApi.getRestoreDestinations, "function");
  assert.equal(typeof recoveryApi.previewRegisteredRestore, "function");
  const previous = globalThis.fetch, calls = [], controller = new AbortController();
  globalThis.fetch = async (url, init) => { calls.push([url, init]); return Response.json({ data: [] }); };
  try {
    await recoveryApi.getRestoreDestinations("backup-a", { signal: controller.signal });
    const input = { workspace_id: "workspace-a", destination_id: "destination-a", destination_version: 1, step_up_authorization_id: "preview-token" };
    await recoveryApi.previewRegisteredRestore("backup-a", input, "r4-preview-idem-key", { signal: controller.signal });
    assert.deepEqual(calls.map(([url]) => url), ["/bff/api/backups/backup-a/restore-destinations", "/bff/api/backups/backup-a/restore-previews"]);
    assert.deepEqual(JSON.parse(calls[1][1].body), input);
    assert.equal(calls[1][1].headers["Idempotency-Key"], "r4-preview-idem-key");
    for (const [, init] of calls) assert.deepEqual([init.credentials, init.cache, init.signal], ["same-origin", "no-store", controller.signal]);
    for (const change of [{ destination_version: true }, { destination_version: 0 }, { source_actor: "forged" }, { destination: {} }, { step_up_authorization_id: "" }, {step_up_authorization_id:"x".repeat(513)}]) {
      await assert.rejects(async () => recoveryApi.previewRegisteredRestore("backup-a", { ...input, ...change }, "r4-preview-idem-key"));
    }
    assert.equal(calls.length, 2);
  } finally { globalThis.fetch = previous; }
});

test("R4 backup exact GET forwards abort without changing legacy call signature",async()=>{
  const previous=globalThis.fetch,controller=new AbortController();let observed;
  globalThis.fetch=async(url,init)=>{observed=[url,init];return Response.json({data:{}});};
  try{
    await recoveryApi.getBackup("backup-a",{signal:controller.signal});
    assert.equal(observed[0],"/bff/api/backups/backup-a");assert.equal(observed[1].signal,controller.signal);
  }finally{globalThis.fetch=previous;}
});

test("S44 L1 Web은 기존 Notebook/context를 읽고 새 정확4필드만 same-origin POST한다", async () => {
  const previous = globalThis.fetch, calls = [], controller = new AbortController();
  globalThis.fetch = async (url, init) => {
    calls.push([url, init]);
    if (url.endsWith("/notebooks")) return Response.json({ data: [{ notebook_id: "notebook-a", title: "Notebook", source_count: 1, output_count: 0,
      updated_at: "2026-10-18T00:00:00Z", status: "active", etag: '"notebook:1"' }], meta: { workspace_id: "workspace-a", trace_id: "trace-a" } });
    if (url.endsWith("/context")) return Response.json({ data: { notebook_id: "notebook-a", sources: [{ source_id: "source-a", source_version_id: "version-a" }],
      knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [],
      source_deletion_requests: [], conversation: null }, meta: { workspace_id: "workspace-a", trace_id: "trace-a" } }, { headers: { ETag: '"notebook-binding:1"' } });
    if (url.endsWith("/sources?notebook_id=notebook-a")) return labelList([]);
    return Response.json({ error: { code: "RESOURCE_UNAVAILABLE" } }, { status: 503 });
  };
  try {
    assert.deepEqual(await recoveryApi.listSelectionNotebooks("workspace-a", { signal: controller.signal }), [{ id: "notebook-a", title: "Notebook" }]);
    assert.deepEqual(await recoveryApi.getSelectionSources("workspace-a", "notebook-a", { signal: controller.signal }), {
      notebookId: "notebook-a", bindingEtag: '"notebook-binding:1"', sources: [{ sourceId: "source-a", versionId: "version-a" }] });
    const input = { workspace_id: "workspace-a", notebook_id: "notebook-a", trigger: "manual", source_version_ids: ["version-a"] };
    await assert.rejects(recoveryApi.createBackupSelection(input, "l1-create-key-00001", { signal: controller.signal }), e => e.status === 503);
    assert.deepEqual(calls.map(([url]) => url), ["/bff/api/workspaces/workspace-a/notebooks", "/bff/api/workspaces/workspace-a/notebooks/notebook-a/context",
      "/bff/api/workspaces/workspace-a/sources?notebook_id=notebook-a", "/bff/api/backups"]);
    for (const [, init] of calls) assert.deepEqual([init.credentials, init.cache, init.signal], ["same-origin", "no-store", controller.signal]);
    assert.deepEqual(JSON.parse(calls[3][1].body), input);
    assert.equal(calls[3][1].headers["Idempotency-Key"], "l1-create-key-00001");
    const count = calls.length;
    for (const change of [{ schema_revision: "forged" }, { objects: [] }, { source_version_ids: [] },
      { source_version_ids: ["version-a", "version-a"] }, { source_version_ids: Array.from({ length: 101 }, (_, i) => `v-${i}`) },
      { source_version_ids: [1] }, { notebook_id: "../invalid" }, { trigger: "automatic" }]) {
      await assert.rejects(async () => recoveryApi.createBackupSelection({ ...input, ...change }, "l1-create-key-00001"));
    }
    assert.equal(calls.length, count, "거부 입력은 fetch0");
  } finally { globalThis.fetch = previous; }
});

test("S44 fixpass Session helper는 기존 무인자 GET과 optional AbortSignal을 함께 유지한다", async () => {
  const previous = globalThis.fetch, controller = new AbortController(), calls = [];
  try {
    globalThis.fetch = async (url, init) => { calls.push([url, init]); return Response.json({ data: {} }); };
    await recoveryApi.getSession();
    await recoveryApi.getSession({ signal: controller.signal });
    for (const [url, init] of calls) {
      assert.equal(url, "/bff/api/session");
      assert.deepEqual([init.method, init.credentials, init.cache], ["GET", "same-origin", "no-store"]);
    }
    assert.equal(calls[0][1].signal, undefined);
    assert.equal(calls[1][1].signal, controller.signal);
  } finally { globalThis.fetch = previous; }
});

test("S44 기존복구 Step-up은 정확 요청에 새 비밀번호 인증과 abort를 결속한다", async () => {
  const previous = globalThis.fetch, calls = [], controller = new AbortController();
  try {
    globalThis.fetch = async (url, init) => {
      calls.push([url, init]);
      return Response.json({ data: { step_up_authorization: "synthetic-execute-token",
        issued_at: "2026-10-18T00:00:00Z", expires_at: "2026-10-18T00:05:00Z" } });
    };
    assert.equal(typeof recoveryApi.issueRestoreStepUp, "function");
    const token = await recoveryApi.issueRestoreStepUp("restore-a", "synthetic-password", {
      idempotencyKey: "restore-step-up-key-0001", signal: controller.signal,
    });
    assert.equal(token, "synthetic-execute-token");
    const [url, init] = calls[0];
    assert.equal(url, "/bff/api/session/step-up");
    assert.deepEqual(JSON.parse(init.body), { action_group: "permanent_delete_or_restore_rollback",
      target_id: "restore-a", password: "synthetic-password", ttl_seconds: 300 });
    assert.deepEqual([init.method, init.credentials, init.cache, init.signal], ["POST", "same-origin", "no-store", controller.signal]);
    assert.equal(init.headers["Idempotency-Key"], "restore-step-up-key-0001");
  } finally { globalThis.fetch = previous; }
});

test("S44 기존복구 Step-up 잘못된 입력·응답을 거부하고 HTTP 오류 status를 보존한다", async () => {
  const previous = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { ++calls; return Response.json({ data: {} }); };
    for (const [id, password, key] of [["https://private", "synthetic-password", "key-stepup-0000001"],
      ["restore-a\n", "synthetic-password", "key-stepup-0000001"],
      ["restore-a", "synthetic-password", "key-stepup-0000001\n"],
      ["restore-a", "short", "key-stepup-0000001"], ["restore-a", "x".repeat(1025), "key-stepup-0000001"],
      ["restore-a", "synthetic-password", "bad"]]) {
      await assert.rejects(async () => recoveryApi.issueRestoreStepUp(id, password, { idempotencyKey: key }));
    }
    assert.equal(calls, 0);
    for (const value of [null, "", "x".repeat(4097), {}, "bad\ntoken"]) {
      globalThis.fetch = async () => Response.json({ data: { step_up_authorization: value } });
      await assert.rejects(async () => recoveryApi.issueRestoreStepUp("restore-a", "synthetic-password", { idempotencyKey: "key-stepup-0000001" }));
    }
    for (const status of [401, 403, 503]) {
      globalThis.fetch = async () => Response.json({ error: { code: "STEP_UP_REQUIRED" } }, { status });
      await assert.rejects(async () => recoveryApi.issueRestoreStepUp("restore-a", "synthetic-password", { idempotencyKey: "key-stepup-0000001" }), e => e.status === status);
    }
  } finally { globalThis.fetch = previous; }
});

test("S44 기존복구 정확 GET·Execute·Cancel의 optional AbortSignal과 기존 body/header 계약을 유지한다", async () => {
  const previous = globalThis.fetch, calls = [], controller = new AbortController();
  try {
    globalThis.fetch = async (url, init) => { calls.push([url, init]); return Response.json({ data: {} }); };
    await recoveryApi.getRestore("restore-a", { signal: controller.signal });
    await recoveryApi.executeRestore("restore-a", { preview_version: 1, step_up_authorization_id: "synthetic-token" }, '"restore:restore-a:2"', "key-execute-00001", { signal: controller.signal });
    await recoveryApi.cancelRestore("restore-a", '"restore:restore-a:2"', "key-cancel-000001", { signal: controller.signal });
    for (const [, init] of calls) assert.equal(init.signal, controller.signal);
    assert.equal(calls[1][1].headers["If-Match"], '"restore:restore-a:2"');
    assert.deepEqual(JSON.parse(calls[1][1].body), { preview_version: 1, step_up_authorization_id: "synthetic-token" });
    assert.equal(calls[2][1].body, undefined);
  } finally { globalThis.fetch = previous; }
});

test("S44 상태 조회는 기존 엄격한 5component helper와 same-origin GET을 사용한다", async () => {
  const previous = globalThis.fetch, calls = [];
  const unknown = { state: "not_collected", observed_at: null, received_at: null, safe_code: "TELEMETRY_NOT_COLLECTED" };
  const personal = { ...unknown, coverage: "actor_accessible_records" };
  const data = { workspace_id: "workspace-a", overall_status: "ready", checked_at: "2026-10-18T00:00:00Z",
    components: ["provider", "api", "storage", "sync", "queue"].map(component_id => ({ component_id, status: "ready", safe_code: "COMPONENT_READY", pending_count: 0, recovery_action: "none" })),
    observations: ["document_pending", "document_retry_wait", "document_leased", "document_dead_letter", "object_pending", "object_retry_wait", "object_leased", "object_dead_letter", "object_oldest_pending_seconds", "source_waiting_model", "index_records_current", "source_storage_bytes", "reported_prompt_tokens", "reported_completion_tokens", "reported_total_tokens", "attempts_without_reported_usage"]
      .map((metric_id,index)=>({metric_id,state:"not_collected",value:null,unit:index===8?"seconds":index===11?"bytes":index>=12&&index<=14?"tokens":"count",
        observed_at:null,coverage:index<9?"workspace_operational_counts":"actor_accessible_records",safe_code:"METRIC_NOT_COLLECTED"})),
    worker_samples: ["document", "studio", "object"].map(worker_kind => ({ ...unknown, coverage: "workspace_operational_counts",
      worker_kind, process_state: "unknown", poll_started_at: null, poll_finished_at: null, active_started_at: null })),
    resource_samples: ["document", "studio", "object"].flatMap(worker_kind =>
      ["cpu_quota_millicores", "memory_limit_bytes", "memory_current_bytes", "runtime_available_bytes"].map((metric_id, i) => ({
        ...unknown, coverage: "workspace_operational_counts", worker_kind, metric_id, value: null, unit: i === 0 ? "millicores" : "bytes" }))),
    event_counts: ["manual_reprocess_pending", "readiness_reprocess_pending", "retry_wait", "backoff_wait", "step_up_denied_24h", "step_up_expired_24h",
      "access_blocked_24h", "access_masked_24h", "egress_denied_24h"].map(metric_id => ({ ...personal, metric_id, value: null, unit: "count", window_start: null, window_end: null })),
    cost_summary: { ...personal, known_amounts: [], unknown_attempt_count: null, unconfirmed_run_count: null, coverage_start: null, coverage_end: null },
    deployment_summary: { ...personal, operation: null, code_revision: null, image_digest: null, previous_image_digest: null, result: null },
    unimplemented: [{ producer_id: "readiness_event_producer", safe_code: "PRODUCER_NOT_IMPLEMENTED", impact_code: "AUTOMATIC_REPROCESS_UNOBSERVED" }],
  };
  const controller = new AbortController();
  let value = data;
  try {
    globalThis.fetch = async (url, init) => { calls.push([url, init]); return Response.json({ data: value, meta: { workspace_id: "workspace-a", trace_id: "trace-status" } }); };
    assert.deepEqual(await recoveryApi.getOperationsStatus("workspace-a", { signal: controller.signal }), data);
    assert.equal(calls[0][0], "/bff/api/workspaces/workspace-a/operations/status");
    assert.equal(calls[0][1].signal, controller.signal);
    assert.deepEqual([calls[0][1].method, calls[0][1].credentials, calls[0][1].cache], ["GET", "same-origin", "no-store"]);
    assert.deepEqual(await recoveryApi.getOperationsStatus("workspace-a", { signal: controller.signal, scope: "workspace_visible" }), data);
    assert.equal(calls[1][0], "/bff/api/workspaces/workspace-a/operations/status?scope=workspace_visible");
    for (value of [{ ...data, workspace_id: "workspace-b" }, { ...data, components: data.components.slice(1) },
      { ...data, components: data.components.map(c => ({ ...c, pending_count: -1 })) }]) {
      await assert.rejects(recoveryApi.getOperationsStatus("workspace-a"), /OPERATIONS_STATUS_RESPONSE_INVALID/u);
    }
    for (const field of ["worker_samples", "resource_samples", "event_counts", "cost_summary", "deployment_summary", "unimplemented"]) {
      value = structuredClone(data); delete value[field];
      await assert.rejects(recoveryApi.getOperationsStatus("workspace-a"), /OPERATIONS_STATUS_RESPONSE_INVALID/u, `필수 ${field}`);
    }
    for (const change of [
      row => { row.worker_samples[0].process_state = "alive"; },
      row => { row.resource_samples[0].value = 0; },
      row => { row.cost_summary.known_amounts = [{ currency: "USD", amount: "0" }]; },
      row => { row.deployment_summary.result = "succeeded"; },
      row => { row.unimplemented[0].safe_code = "PRODUCER_READY"; },
    ]) {
      value = structuredClone(data); change(value);
      await assert.rejects(recoveryApi.getOperationsStatus("workspace-a"), /OPERATIONS_STATUS_RESPONSE_INVALID/u);
    }
  } finally { globalThis.fetch = previous; }
});

test("S44 상태 거부는 실제 HTTP status만 보존하고 목록 GET도 캐시를 사용하지 않는다", async () => {
  const previous = globalThis.fetch;
  try {
    for (const status of [401, 403, 503]) {
      globalThis.fetch = async () => Response.json({ error: { code: "RESOURCE_UNAVAILABLE" } }, { status });
      await assert.rejects(recoveryApi.getOperationsStatus("workspace-a"), e => e.status === status && e.code === "RESOURCE_UNAVAILABLE");
    }
    globalThis.fetch = async (url, init) => {
      assert.equal(url, "/bff/api/backups?workspace_id=workspace-a");
      assert.deepEqual([init.method, init.credentials, init.cache], ["GET", "same-origin", "no-store"]);
      return Response.json({ data: [] });
    };
    assert.deepEqual((await recoveryApi.listBackups("workspace-a")).payload.data, []);
  } finally { globalThis.fetch = previous; }
});

for (const status of [404, 503]) {
  test(`정확 복구 조회는 같은 오류 코드의 실제 HTTP ${status}를 보존한다`, async () => {
    const previous = globalThis.fetch;
    try {
      globalThis.fetch = async () => Response.json({ error: { code: "RESOURCE_UNAVAILABLE" } }, { status });
      await assert.rejects(recoveryApi.getRestore("restore-read-target"), error =>
        error.code === "RESOURCE_UNAVAILABLE" && error.status === status);
    } finally { globalThis.fetch = previous; }
  });
}

test("정확 복구·본인 session GET은 캐시를 재사용하지 않고 same-origin을 유지한다", async () => {
  const previous = globalThis.fetch, calls = [];
  try {
    globalThis.fetch = async (url, options) => { calls.push([url, options]); return Response.json({ data: {} }); };
    await recoveryApi.getSession();
    await recoveryApi.getBackup("backup-read-target");
    await recoveryApi.getRestore("restore-read-target");
  } finally { globalThis.fetch = previous; }
  assert.equal(calls.length, 3);
  for (const [url, options] of calls) {
    assert.ok(url.startsWith("/bff/api/"));
    assert.equal(options.method, "GET");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
  }
});

test("Recovery Web Adapter는 정확한 Cloud 7 Route를 same-origin으로만 호출한다", async () => {
  const source = await readFile("apps/web/lib/recovery-api.js", "utf8");
  for (const token of [
    "/bff/api/backups", "/restore-previews", "/bff/api/restore-requests/",
    "/execute", "/cancel", "Idempotency-Key", "If-Match", "same-origin"
  ]) assert.match(source, new RegExp(token.replaceAll("/", "\\/")));
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
});

test("Recovery Web Adapter는 same-origin Session Context를 먼저 조회한다", async () => {
  const source = await readFile("apps/web/lib/recovery-api.js", "utf8");
  assert.match(source, /getSession\([^)]*\)\s*\{\s*return request\("\/bff\/api\/session"/);
  assert.doesNotMatch(source, /\/api\/v1/);
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
});

test("Recovery Adapter는 Session과 Cloud 7 operation을 Browser BFF로만 전달한다", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  try {
    globalThis.fetch = async (url, init = {}) => {
      calls.push({ url, init });
      return Response.json({ data: {}, meta: {} }, { headers: { ETag: '"recovery-v1"' } });
    };
    await recoveryApi.getSession();
    await recoveryApi.listBackups("workspace/1");
    await recoveryApi.createBackup({ workspace_id: "workspace/1" }, "idem-create");
    await recoveryApi.getBackup("backup/1");
    await recoveryApi.previewRestore("backup/1", { mode: "preview" }, "idem-preview");
    await recoveryApi.getRestore("restore/1");
    await recoveryApi.executeRestore("restore/1", { confirmation: "approved" }, '"restore:1"', "idem-execute");
    await recoveryApi.cancelRestore("restore/1", '"restore:1"', "idem-cancel");
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.deepEqual(calls.map(({ url }) => url), [
    "/bff/api/session",
    "/bff/api/backups?workspace_id=workspace%2F1",
    "/bff/api/backups",
    "/bff/api/backups/backup%2F1",
    "/bff/api/backups/backup%2F1/restore-previews",
    "/bff/api/restore-requests/restore%2F1",
    "/bff/api/restore-requests/restore%2F1/execute",
    "/bff/api/restore-requests/restore%2F1/cancel",
  ]);
  assert.equal(calls.every(({ url, init }) => !url.startsWith("/api/v1") && init.credentials === "same-origin"), true);
  assert.deepEqual(calls.slice(2).map(({ init }) => init.method), ["POST", "GET", "POST", "GET", "POST", "POST"]);
  assert.equal(calls[2].init.headers["Idempotency-Key"], "idem-create");
  assert.equal(calls[4].init.headers["Idempotency-Key"], "idem-preview");
  assert.equal(calls[6].init.headers["If-Match"], '"restore:1"');
  assert.equal(calls[6].init.body, JSON.stringify({ confirmation: "approved" }));
});

test("Recovery Session coordinator는 유효 Context만 Pane 초기화 뒤 Backup 목록에 전달한다", async () => {
  const calls = [];
  const adapter = {
    async getSession() {
      calls.push("session");
      return { payload: { data: { user_id: "user-real", tenant_id: "tenant-real", workspace_id: "workspace-real" } } };
    },
    async listBackups(workspaceId) {
      calls.push(`backups:${workspaceId}`);
      return { payload: { data: [] } };
    }
  };
  const context = await resolveRecoverySession(adapter, async (resolved) => {
    calls.push(`pane:${resolved.workspaceId}`);
    await adapter.listBackups(resolved.workspaceId);
    return resolved;
  });
  assert.deepEqual(context, { userId: "user-real", tenantId: "tenant-real", workspaceId: "workspace-real", membership: null });
  assert.deepEqual(calls, ["session", "pane:workspace-real", "backups:workspace-real"]);
});

test("Recovery Session coordinator는 malformed 또는 거부된 Session에서 Pane·Backup 호출 없이 fail-close한다", async () => {
  assert.equal(parseRecoverySessionContext({ data: { user_id: "user", tenant_id: "", workspace_id: "workspace" } }), null);
  let initialized = 0;
  await assert.rejects(
    resolveRecoverySession({ async getSession() { return { payload: { data: {} } }; } }, () => { initialized += 1; }),
    { code: "RESOURCE_UNAVAILABLE" }
  );
  await assert.rejects(
    resolveRecoverySession({ async getSession() { const error = new Error("denied"); error.code = "ACCESS_INVALID"; throw error; } }, () => { initialized += 1; }),
    { code: "ACCESS_INVALID" }
  );
  assert.equal(initialized, 0);
});

test("두 Next catch-all은 Cloud Recovery POST를 공통 Proxy로 연결한다", async () => {
  const directRoute = await import("../../apps/web/app/api/v1/[...path]/route.js");
  const legacyRoute = await import("../../apps/web/app/bff/api/[...path]/route.js");
  assert.deepEqual([typeof directRoute.POST, typeof legacyRoute.POST], ["function", "function"]);
});

test("BFF allowlist는 Cloud Recovery 7 Operation만 연결한다", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: {}, meta: {} }, { headers: { ETag: '"recovery-v1"' } });
    },
  });
  const write = (path, segments) => proxy(new Request(`https://app.example.com/api/v1/${path}`, {
    method: "POST",
    headers: {
      Origin: "https://app.example.com",
      "Sec-Fetch-Site": "same-origin",
      "Content-Type": "application/json",
      "Idempotency-Key": `idem-${path}`,
      "If-Match": '"restore:fixture-restore:1"',
    },
    body: "{}",
  }), segments);
  const responses = [
    await write("backups", ["backups"]),
    await proxy(new Request("https://app.example.com/api/v1/backups?workspace_id=fixture-workspace"), ["backups"]),
    await proxy(new Request("https://app.example.com/api/v1/backups/fixture-backup"), ["backups", "fixture-backup"]),
    await write("backups/fixture-backup/restore-previews", ["backups", "fixture-backup", "restore-previews"]),
    await proxy(new Request("https://app.example.com/api/v1/restore-requests/fixture-restore"), ["restore-requests", "fixture-restore"]),
    await write("restore-requests/fixture-restore/execute", ["restore-requests", "fixture-restore", "execute"]),
    await write("restore-requests/fixture-restore/cancel", ["restore-requests", "fixture-restore", "cancel"]),
  ];
  assert.deepEqual(responses.map((response) => response.status), [200, 200, 200, 200, 200, 200, 200]);
  assert.deepEqual(captured, [
    { url: "https://api.example.com/api/v1/backups", method: "POST" },
    { url: "https://api.example.com/api/v1/backups?workspace_id=fixture-workspace", method: "GET" },
    { url: "https://api.example.com/api/v1/backups/fixture-backup", method: "GET" },
    { url: "https://api.example.com/api/v1/backups/fixture-backup/restore-previews", method: "POST" },
    { url: "https://api.example.com/api/v1/restore-requests/fixture-restore", method: "GET" },
    { url: "https://api.example.com/api/v1/restore-requests/fixture-restore/execute", method: "POST" },
    { url: "https://api.example.com/api/v1/restore-requests/fixture-restore/cancel", method: "POST" },
  ]);

  let rejectedCalls = 0;
  const rejectingProxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { rejectedCalls += 1; return new Response(); },
  });
  const invalidMethod = await rejectingProxy(
    new Request("https://app.example.com/api/v1/backups/fixture-backup", { method: "DELETE" }),
    ["backups", "fixture-backup"],
  );
  const invalidPath = await rejectingProxy(
    new Request("https://app.example.com/api/v1/backups/fixture-backup/raw"),
    ["backups", "fixture-backup", "raw"],
  );
  assert.deepEqual([invalidMethod.status, invalidPath.status, rejectedCalls], [405, 404, 0]);
});
