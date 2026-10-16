import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import * as studioApi from '../../apps/web/lib/product-workspace-api.js';

test('S24 lifecycle helpers bind exact policy withdraw and notice contracts with abort signals',async()=>{
  assert.equal(typeof studioApi.getStudioApprovalPolicy,'function');
  const calls=[],signal=new AbortController().signal;
  const target='studio-approval-policy-'+'a'.repeat(64);
  const fetchImpl=async(url,init)=>{
    calls.push({url,init,body:init.body?JSON.parse(init.body):null});
    let data;
    if(url.includes('approval-policy')) data={ttl_days:7,policy_version:0,can_manage:true,step_up_target_id:target};
    else if(url.includes('/withdraw')) data={record_id:'request-1',action:'withdraw',status:'withdrawn',output_version_id:'version-1'};
    else if(init.method==='PATCH') data={notification_id:'notice-1',version:2,read_at:'2026-10-16T00:00:00Z'};
    else data={items:[],unread_count:0};
    return Response.json({data,meta:{trace_id:'trace-1',workspace_id:'workspace-1',...(init.method==='GET'?{}:{replayed:false})}});
  };
  const options={notebookId:'notebook-1',fetchImpl,signal,idempotencyKey:'s24-lifecycle-wire-001'};
  const policy=await studioApi.getStudioApprovalPolicy('workspace-1',options);
  assert.equal(policy.step_up_target_id,target);
  await studioApi.saveStudioApprovalPolicy('workspace-1',{ttl_days:7,expected_policy_version:0,step_up_authorization:'opaque-fixture'},options);
  await studioApi.withdrawStudioApprovalRequest('workspace-1','request-1','version-1',options);
  await studioApi.listStudioApprovalNotices('workspace-1',options);
  await studioApi.markStudioApprovalNoticeRead('workspace-1','notice-1',1,options);
  assert.deepEqual(calls[2].body,{workspace_id:'workspace-1',notebook_id:'notebook-1',output_version_id:'version-1'});
  assert.deepEqual(calls[4].body,{notebook_id:'notebook-1',state:'read'});
  assert.equal(calls[4].init.headers['If-Match'],'"studio-approval-notice:notice-1:1"');
  assert.ok(calls.every(({url,init})=>url.startsWith('/bff/api/')&&init.signal===signal&&init.credentials==='same-origin'&&init.cache==='no-store'));
  assert.ok(calls.every(({url})=>!url.includes('opaque-fixture')));
  await assert.rejects(studioApi.saveStudioApprovalPolicy('workspace-1',{ttl_days:true,expected_policy_version:0,step_up_authorization:'opaque-fixture'},options),/STUDIO_INPUT_INVALID/);
});

test('S24 lifecycle strict policy and notice responses reject extra recipient malformed counts and foreign scope',async()=>{
  assert.equal(typeof studioApi.listStudioApprovalNotices,'function');
  for(const data of [{items:[],unread_count:1},{items:[],unread_count:0,recipient_id:'other'}]) {
    await assert.rejects(studioApi.listStudioApprovalNotices('workspace-1',{notebookId:'notebook-1',fetchImpl:async()=>Response.json({data,meta:{trace_id:'trace-1',workspace_id:'workspace-1'}})}),/STUDIO_RESPONSE_INVALID/);
  }
});

import {
  OUTPUT_TYPES, createProductStudioState, selectOutputType, updateGenerationSettings,
  confirmGenerationSettings, canSubmitGeneration, createStudioGenerationInput, mergeStudioVersion,
} from "../../packages/ui/src/product-studio-model.js";
import {
  createStudioGeneration, createStudioVersion, listProductStudioOutputs, createStudioAction, downloadStudioExport,
  listStudioVersions,
} from "../../apps/web/lib/product-workspace-api.js";

const grounded = {
  sourceId: "source-1", sourceVersionId: "source-version-1",
  sourceVersionIds: ["source-version-1"],
  runId: "run-1", runResultId: "result-1",
};

test('S24 export step-up hashes the exact scope and sends token and key only as headers without SubtleCrypto', async () => {
  assert.equal(typeof studioApi.issueStudioExportStepUp, 'function');
  const crypto = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  const calls = [], controller = new AbortController();
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return init.method === 'POST' ? Response.json({ data: { step_up_authorization: 'opaque-s24-grant' } })
      : new Response('%PDF-', { headers: { 'content-type': 'application/pdf' } });
  };
  try {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: undefined });
    const token = await studioApi.issueStudioExportStepUp('workspace-1','output-1','version-1','pdf','fixture password only',
      { notebookId: 'notebook-1', fetchImpl, idempotencyKey: 's24-issue-key-0001', signal: controller.signal });
    const body = JSON.parse(calls[0].init.body);
    const expected = 'studio-export-' + createHash('sha256').update(JSON.stringify(['workspace-1','notebook-1','output-1','version-1','pdf'])).digest('hex');
    assert.deepEqual(body, { action_group: 'final_approval_or_knowledge_registration', target_id: expected, ttl_seconds: 300, password: 'fixture password only' });
    const result = await downloadStudioExport('workspace-1','output-1','version-1','pdf',
      { notebookId: 'notebook-1', fetchImpl, stepUpAuthorization: token, idempotencyKey: 's24-file-key-00001', signal: controller.signal });
    assert.equal(new Headers(calls[1].init.headers).get('X-Daon-Step-Up-Authorization'), token);
    assert.equal(new Headers(calls[1].init.headers).get('Idempotency-Key'), 's24-file-key-00001');
    assert.equal(calls[1].init.credentials, 'same-origin'); assert.equal(calls[1].init.cache, 'no-store');
    assert.equal(calls[1].init.signal, controller.signal);
    assert.doesNotMatch(calls[1].url, /opaque|password|step.up/i);
    assert.deepEqual(result.bytes, [...new TextEncoder().encode('%PDF-')]);
  } finally { if (crypto) Object.defineProperty(globalThis, 'crypto', crypto); else delete globalThis.crypto; }
});

test('S24 export rejects missing tokens invalid scope format and keys before network', async () => {
  let calls = 0; const fetchImpl = async () => { calls++; return new Response('%PDF-'); };
  for (const patch of [{stepUpAuthorization:undefined},{idempotencyKey:'short'},{notebookId:'../other'}]) {
    await assert.rejects(downloadStudioExport('workspace-1','output-1','version-1','pdf',
      {notebookId:'notebook-1', stepUpAuthorization:'opaque-grant', idempotencyKey:'s24-file-key-0001', fetchImpl, ...patch}));
  }
  assert.equal(typeof studioApi.issueStudioExportStepUp, 'function');
  for (const [out, version, format] of [['../out','v','pdf'],['out','../v','pdf'],['out','v','exe']]) {
    await assert.rejects(studioApi.issueStudioExportStepUp('workspace-1',out,version,format,'fixture password only',
      {notebookId:'notebook-1',fetchImpl}));
  }
  assert.equal(calls, 0);
});

test('S24 actual Web adapter binds export authorization and bytes to its current Notebook', async () => {
  const root=path.resolve(import.meta.dirname,'../..');
  const output=await mkdtemp(path.join(root,'.product-studio-s24-adapter-'));
  try {
    const {build}=await import('vite');
    await build({configFile:false,logLevel:'silent',root,build:{outDir:output,emptyOutDir:false,
      lib:{entry:path.join(root,'apps/web/components/actual-workspace.jsx'),formats:['es'],fileName:'s24-adapter'},
      rollupOptions:{external:['react','react-dom','react-dom/client']}}});
    const entry=(await readdir(output)).find(name=>name.startsWith('s24-adapter')&&/\.m?js$/u.test(name));
    const {createWebProductWorkspaceAdapter}=await import(pathToFileURL(path.join(output,entry)).href);
    const adapter=createWebProductWorkspaceAdapter('workspace-1','notebook-current'),calls=[];
    const fetchImpl=async(url,init)=>{calls.push({url,init});return init.method==='POST'
      ?Response.json({data:{step_up_authorization:'s24-current-proof'}}):new Response('%PDF-');};
    const signal=new AbortController().signal;
    const options={notebookId:'notebook-wrong',fetchImpl,idempotencyKey:'s24-adapter-key-001',signal};
    const token=await adapter.issueStudioExportStepUp('output-1','version-1','pdf','fixture password only',options);
    await adapter.downloadStudioExport('output-1','version-1','pdf',{...options,stepUpAuthorization:token});
    const expected='studio-export-'+createHash('sha256').update(JSON.stringify(['workspace-1','notebook-current','output-1','version-1','pdf'])).digest('hex');
    assert.equal(JSON.parse(calls[0].init.body).target_id,expected);
    assert.match(calls[1].url,/notebook_id=notebook-current$/);
    assert.equal(new Headers(calls[1].init.headers).get('X-Daon-Step-Up-Authorization'),token);
    assert.ok(calls.every(call=>call.init.signal===signal));
  } finally {await rm(output,{recursive:true,force:true});}
});

test('S22 type selection projects organization locks before the first edit', () => {
  const locks = [
    { field: 'reviewCondition', value: 'review_required', reason: 'WORKSPACE_POLICY' },
    { field: 'rulesetVersionId', value: 'ruleset-v3', reason: 'RULESET_BINDING' },
  ];
  const initial = createProductStudioState({ grounded, locks });
  const selected = selectOutputType(initial, 'comparison_table');
  assert.equal(selected.settings.reviewCondition, 'review_required');
  assert.equal(selected.settings.rulesetVersionId, 'ruleset-v3');
  assert.deepEqual(initial.settings, { sourceVersionIds: ['source-version-1'] });
  assert.throws(() => updateGenerationSettings(selected, { reviewCondition: 'approval_required' }), /STUDIO_SETTING_LOCKED/);
});

test('S22 new type separates previous output completion without losing the library', () => {
  const output = { studio_output_id: 'saved-output', title: '이전 비교표', status: 'approved' };
  const state = { ...createProductStudioState({ grounded, outputs: [output] }), selectedOutputId: 'saved-output', safeError: 'STUDIO_CREATE_FAILED' };
  const selected = selectOutputType(state, 'comparison_table');
  assert.equal(selected.selectedOutputId, null);
  assert.equal(selected.safeError, null);
  assert.deepEqual(selected.outputs, [output]);
  assert.equal(selected.settingsConfirmed, false);
  assert.equal(selected.settingsSnapshot, null);
  assert.equal(canSubmitGeneration(selected), false);
});

test('S21 audio/video new output selection is rejected without changing saved types',()=>{
  const state=createProductStudioState({grounded});
  for(const type of ['audio','video']){
    assert.throws(()=>selectOutputType(state,type),/^Error: STUDIO_OUTPUT_UNAVAILABLE$/);
  }
  assert.equal(state.selectedOutputType,null);
  assert.throws(()=>selectOutputType(state,'unknown'),/^Error: STUDIO_OUTPUT_TYPE_INVALID$/);
});

test('S21 audio/video forged confirmed settings cannot bypass generation guard',()=>{
  for(const type of ['audio','video']){
    const settings={purpose:'기존 자료',audience:'담당자',language:'ko',sourceVersionIds:['source-version-1'],
      length:'standard',structure:'summary-body-conclusion',outputFormat:'json',reviewCondition:'review_required'};
    const state={...createProductStudioState({grounded}),selectedOutputType:type,settings,settingsSnapshot:{...settings},settingsConfirmed:true};
    assert.equal(canSubmitGeneration(state),false,`${type} submit 우회 금지`);
    assert.throws(()=>confirmGenerationSettings(state),/^Error: STUDIO_SETTINGS_INCOMPLETE$/);
    assert.throws(()=>createStudioGenerationInput(state),/^Error: STUDIO_SETTINGS_NOT_CONFIRMED$/);
  }
});

test('S21 existing nine structured output choices preserve settings and input contracts',()=>{
  for(const [type,format] of [
    ['evidence_report','docx'],['compliance_checklist','xlsx'],['comparison_table','csv'],
    ['knowledge_map','json'],['business_draft','pdf'],['slides','json'],['infographic','svg'],
    ['flashcards','csv'],['quiz','json'],
  ]){
    let state=selectOutputType(createProductStudioState({grounded}),type);
    state=updateGenerationSettings(state,{purpose:'기존 자료',audience:'담당자',sourceVersionIds:['source-version-1'],
      length:'standard',structure:'summary-body-conclusion',outputFormat:format,reviewCondition:'review_required'});
    state=confirmGenerationSettings(state);
    assert.equal(canSubmitGeneration(state),true,type);
    const input=createStudioGenerationInput(state);
    assert.equal(input.output_type,type);assert.equal(input.settings.output_format,format);
    assert.deepEqual(input.source_version_ids,['source-version-1']);
    assert.equal(input.run_id,'run-1');assert.equal(input.run_result_id,'result-1');
  }
});

test("Product Studio는 혼합 Context의 모든 Citation Source Version을 생성 Snapshot에 결속한다", () => {
  const mixed = { ...grounded, sourceVersionIds: ["source-version-daon", "source-version-1"] };
  let state = selectOutputType(createProductStudioState({ grounded: mixed }), "evidence_report");
  assert.deepEqual(state.settings.sourceVersionIds, mixed.sourceVersionIds);
  state = updateGenerationSettings(state, {
    purpose: "혼합 근거 보고", audience: "운영 책임자", sourceVersionIds: mixed.sourceVersionIds,
    rulesetVersionId: null, length: "standard", structure: "summary-body-conclusion",
    outputFormat: "pdf", reviewCondition: "review_required",
  });
  state = confirmGenerationSettings(state);
  assert.deepEqual(createStudioGenerationInput(state).source_version_ids, mixed.sourceVersionIds);
});

test("HTTP QA Studio version과 review action은 randomUUID 없이 secure key로 호출한다", async () => {
  const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const calls = [];
  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: { getRandomValues(bytes) { bytes.fill(7); return bytes; } },
    });
    const fetchImpl = async (url, init) => {
      calls.push({ url, key: init.headers["Idempotency-Key"] });
      return Response.json({ data: { output_version_id: "version-2", status: "draft" }, meta: {
        trace_id: "trace-1", workspace_id: "workspace-1", replayed: false,
      } }, { status: 201 });
    };
    await createStudioVersion("workspace-1", "output-1", {
      previous_version_id: "version-1", revision_type: "user_edit",
      change_reason: "내용 정리", content: "검토할 내용",
    }, { notebookId: "notebook-1", fetchImpl });
    await createStudioAction("workspace-1", "reviews", {
      output_version_id: "version-2",
    }, { notebookId: "notebook-1", fetchImpl });
    assert.deepEqual(calls.map((call) => call.url), [
      "/bff/api/studio-outputs/output-1/versions", "/bff/api/reviews",
    ]);
    for (const call of calls) assert.match(call.key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u);
  } finally {
    if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto);
    else delete globalThis.crypto;
  }
});

test("Product Studio는 11개 실제 산출물과 생성 전 확인을 강제한다", () => {
  assert.deepEqual(OUTPUT_TYPES.map((item) => item.id), [
    "evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft",
    "slides", "infographic", "flashcards", "quiz", "audio", "video",
  ]);
  let state = createProductStudioState({ grounded });
  state = selectOutputType(state, "evidence_report");
  assert.equal(canSubmitGeneration(state), false);
  assert.equal(state.settingsConfirmed, false);
  state = updateGenerationSettings(state, {
    purpose: "의사 결정", audience: "운영 책임자", sourceVersionIds: ["source-version-1"],
    rulesetVersionId: null, length: "standard", structure: "summary-body-conclusion",
    outputFormat: "docx", reviewCondition: "review_required",
  });
  assert.equal(canSubmitGeneration(state), false);
  state = confirmGenerationSettings(state);
  assert.equal(canSubmitGeneration(state), true);
  assert.deepEqual(createStudioGenerationInput(state), {
    workspace_id: null, output_type: "evidence_report", source_id: "source-1",
    source_only: false,
    source_version_ids: ["source-version-1"], run_id: "run-1", run_result_id: "result-1",
    settings: {
      purpose: "의사 결정", audience: "운영 책임자", source_version_ids: ["source-version-1"],
      language: "ko",
      ruleset_version_id: null, length: "standard", structure: "summary-body-conclusion",
      output_format: "docx", review_condition: "review_required",
    },
  });
});

test("질문 실행 없이 선택한 Source Version만으로 Studio 생성 입력을 만든다", () => {
  const sources = [
    { sourceId: "source-1", sourceVersionId: "source-version-1", filename: "guide.pdf", ready: true },
    { sourceId: "source-2", sourceVersionId: "source-version-2", filename: "unavailable.pdf", ready: false },
  ];
  let state = createProductStudioState({ workspaceId: "workspace-1", sources, selectedSourceVersionIds: ["source-version-1"] });
  state = selectOutputType(state, "evidence_report");
  state = updateGenerationSettings(state, {
    purpose: "업무 결정", audience: "운영 담당자", sourceVersionIds: ["source-version-1"],
    length: "short", structure: "executive-summary", outputFormat: "pdf", reviewCondition: "review_required",
  });
  state = confirmGenerationSettings(state);
  assert.equal(canSubmitGeneration(state), true);
  assert.deepEqual(createStudioGenerationInput(state), {
    workspace_id: "workspace-1", output_type: "evidence_report", source_only: true,
    source_id: "source-1", source_version_ids: ["source-version-1"], run_id: null, run_result_id: null,
    settings: {
      purpose: "업무 결정", audience: "운영 담당자", language: "ko", source_version_ids: ["source-version-1"],
      ruleset_version_id: null, length: "short", structure: "executive-summary", output_format: "pdf", review_condition: "review_required",
    },
  });
});

test("서버 잠금은 표시되며 완화 Handler가 없고 변경은 재확정을 요구한다", () => {
  let state = createProductStudioState({
    grounded,
    locks: [{ field: "rulesetVersionId", value: "ruleset-v3", reason: "ORGANIZATION_POLICY" }],
  });
  state = selectOutputType(state, "business_draft");
  state = updateGenerationSettings(state, {
    purpose: "초안", audience: "고객", sourceVersionIds: ["source-version-1"],
    length: "short", structure: "letter", outputFormat: "pdf",
    reviewCondition: "review_required",
  });
  assert.equal(state.settings.rulesetVersionId, "ruleset-v3");
  state = confirmGenerationSettings(state);
  state = updateGenerationSettings(state, { audience: "내부 검토자" });
  assert.equal(state.settingsConfirmed, false);
  assert.throws(() => updateGenerationSettings(state, { rulesetVersionId: null }), /STUDIO_SETTING_LOCKED/);
  assert.equal("unlock" in state, false);
});

test("새 Version merge는 content를 교체하고 이전 lifecycle link를 전부 제거한다", () => {
  const merged = mergeStudioVersion({ output_version_id: "v1", content: { body: "이전" }, review_request_id: "r1", approval_request_id: "ar1", approval_id: "a1", delivery_id: "d1" }, { output_version_id: "v2", content: { body: "신규" }, status: "draft" });
  assert.equal(merged.output_version_id, "v2"); assert.equal(merged.content.body, "신규");
  for (const field of ["review_request_id", "approval_request_id", "approval_id", "delivery_id"]) assert.equal(merged[field], undefined);
});

test("Product Studio Pane은 다섯 Tile·설정·목록·상세를 실제 Product DOM으로 렌더한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".product-studio-react-"));
  try {
    const { build } = await import("vite");
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    await build({
      configFile: false, logLevel: "silent", root,
      build: { outDir: output, emptyOutDir: false,
        lib: { entry: path.join(root, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/server"] },
      },
    });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name));
    const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const initialState = createProductStudioState({ grounded });
    const home = renderToStaticMarkup(createElement(ProductStudioPane, { state: initialState, adapter: null }));
    assert.doesNotMatch(home, /<form[^>]*studio-config-form/u);
    const html = renderToStaticMarkup(createElement(ProductStudioPane, {
      state: selectOutputType(initialState, "evidence_report"), adapter: null,
    }));
    for (const label of ["근거 기반 보고서", "제약·준수 점검표", "비교·데이터 표", "지식 구조도", "업무 문서 초안"]) {
      assert.match(home, new RegExp(label));
    }
    assert.match(html, /목적/);
    assert.match(html, /독자/);
    assert.match(home, /저장된 산출물/);
    assert.doesNotMatch(html, /Prototype|Fixture/);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("저장된 구조화 산출물을 선택한 재진입 화면은 React child crash 없이 상세를 렌더한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".product-studio-selected-react-"));
  try {
    const { build } = await import("vite");
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    await build({ configFile: false, logLevel: "silent", root,
      build: { outDir: output, emptyOutDir: false,
        lib: { entry: path.join(root, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/server"] },
      },
    });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name));
    const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = createProductStudioState({ grounded, outputs: [{
      studio_output_id: "output-1", output_version_id: "version-1", title: "저장 보고서", status: "draft",
      citations: 2, settings_snapshot_id: "settings-1", content: { summary: "요약", sections: [{ heading: "본문", body: "근거 본문" }] },
    }] });
    state.selectedOutputId = "output-1";
    const html = renderToStaticMarkup(createElement(ProductStudioPane, { state, adapter: null }));
    assert.match(html, /요약/);
    assert.match(html, /근거 본문/);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("Web Studio Adapter는 same-origin 공통 계약과 파일 bytes만 사용한다", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, method: init.method });
    if (String(url).includes("exports")) return new Response(Uint8Array.from([0x25, 0x50, 0x44, 0x46, 0x2d]), { headers: { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="studio-version-1.pdf"' } });
    if (init.method === "GET") return Response.json({ data: { outputs: [], studio_locks: [] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
    if (String(url).includes("studio-generation-requests")) return Response.json({ data: { job_id: "studio-job-1", status: "queued", output_type: "evidence_report" }, meta: { trace_id: "trace-1", workspace_id: "workspace-1", replayed: false } }, { status: 202 });
    return Response.json({ data: { output_version_id: "version-2", status: "draft" }, meta: { trace_id: "trace-1", workspace_id: "workspace-1", replayed: false } }, { status: 201 });
  };
  await createStudioGeneration("workspace-1", {
    output_type: "evidence_report", source_id: "source-1", source_version_ids: ["source-version-1"],
    run_id: "run-1", run_result_id: "result-1", settings: { purpose: "목적", audience: "독자", language: "ko", source_version_ids: ["source-version-1"], ruleset_version_id: null, length: "short", structure: "summary", output_format: "pdf", review_condition: "review_required" },
  }, { notebookId: "notebook-1", fetchImpl, idempotencyKey: "generation-key-0001" });
  await listProductStudioOutputs("workspace-1", { notebookId: "notebook-1", fetchImpl });
  await createStudioVersion("workspace-1", "output-1", {
    previous_version_id: "version-1", revision_type: "user_edit", change_reason: "표현 정리", content: "변경 내용",
  }, { notebookId: "notebook-1", fetchImpl, idempotencyKey: "version-key-000001" });
  await listStudioVersions("workspace-1", "output-1", { notebookId: "notebook-1", fetchImpl: async (url, init) => {
    calls.push({ url, method: init.method });
    return Response.json({ data: { output_id: "output-1", versions: [{
      output_version_id: "version-1", content_version: 1, previous_version_id: null, status: "draft",
      content: { body: "근거 내용" }, revision_type: "initial", change_reason: "initial_generation",
      settings_snapshot_id: "settings-1", output_format: "pdf", citations: [{ citation_id: "citation-1", source_version_id: "source-version-1", evidence_span_id: "span-1", origin: "raw_source", locator: { kind: "page", value: "2" } }],
      review_request_id: null, approval_request_id: null, approval_id: null, delivery_id: null, knowledge_registration_id: null,
    }] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
  } });
  for (const region of ["5,7,80,20,120,60", "5,7,180,20,120,60"]) {
    const operation = listStudioVersions("workspace-1", "output-1", { notebookId: "notebook-1", fetchImpl: async () => Response.json({ data: { output_id: "output-1", versions: [{
      output_version_id: "version-1", content_version: 1, previous_version_id: null, status: "draft", content: { body: "이미지 근거" }, revision_type: "initial", change_reason: "initial_generation", settings_snapshot_id: "settings-1", output_format: "pdf",
      citations: [{ citation_id: "citation-image", source_version_id: "source-version-1", evidence_span_id: "span-image", origin: "raw_source", locator: { kind: "image_region", value: region } }], review_request_id: null, approval_request_id: null, approval_id: null, delivery_id: null, knowledge_registration_id: null,
    }] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } }) });
    if (region === "5,7,80,20,120,60") assert.equal((await operation)[0].citations[0].locator.value, region);
    else await assert.rejects(operation, /STUDIO_RESPONSE_INVALID/);
  }
  await createStudioAction("workspace-1", "reviews", { output_version_id: "version-1" }, { notebookId: "notebook-1", fetchImpl, idempotencyKey: "review-key-000001" });
  const exported = await downloadStudioExport("workspace-1", "output-1", "version-1", "pdf", { notebookId: "notebook-1", fetchImpl, stepUpAuthorization: "opaque-export-fixture", idempotencyKey: "studio-export-key-001" });
  assert.deepEqual(exported.bytes, [0x25, 0x50, 0x44, 0x46, 0x2d]);
  assert.ok(calls.every((call) => call.url.startsWith("/bff/api/")));
  assert.deepEqual(calls.map((call) => call.url), [
    "/bff/api/studio-generation-requests", "/bff/api/studio-outputs?workspace_id=workspace-1&notebook_id=notebook-1",
    "/bff/api/studio-outputs/output-1/versions",
    "/bff/api/studio-outputs/output-1/versions?workspace_id=workspace-1&notebook_id=notebook-1",
    "/bff/api/reviews", "/bff/api/studio-outputs/output-1/versions/version-1/exports/pdf?workspace_id=workspace-1&notebook_id=notebook-1",
  ]);
});

test("Step-up Adapter는 현재 비밀번호를 요청 body에만 넣고 응답으로 보존하지 않는다", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    return Response.json({ data: { step_up_authorization: "opaque-grant" }, meta: { trace_id: "trace-1" } }, { status: 201 });
  };
  const { issueStudioStepUp } = await import("../../apps/web/lib/product-workspace-api.js");
  const grant = await issueStudioStepUp("external_transfer", "version-1", "current password value", { fetchImpl, idempotencyKey: "step-key-000001" });
  assert.equal(grant, "opaque-grant");
  assert.deepEqual(calls[0].body, { action_group: "external_transfer", target_id: "version-1", ttl_seconds: 300, password: "current password value" });
  assert.doesNotMatch(JSON.stringify(grant), /current password value/);
});
