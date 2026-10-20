import assert from "node:assert/strict";
import test from "node:test";

import { createNotebook, getCurrentNotebookSession, getNotebook, getNotebookContext, listNotebooks, updateNotebookTitle } from "../../apps/web/lib/notebook-api.js";
import { listWorkspaceSources } from "../../apps/web/lib/product-workspace-api.js";
import { createNotebookContextWorkspaceAdapter, projectNotebookSelectedContext } from "../../packages/ui/src/notebook-context-adapter.js";

const VIEW = Object.freeze({ notebook_id: "notebook-1", title: "Notebook", source_count: 0, output_count: 0, updated_at: "2026-08-16T01:02:03Z", status: "empty", etag: '"notebook:1"' });
const META = Object.freeze({ trace_id: "trace-1", workspace_id: "workspace-1" });
const json = (body, init = {}) => Response.json(body, init);

test('S42 정확 Run/Job 선택자는 same-origin context GET에 하나만 전달한다', async () => {
  const data = { notebook_id:'notebook-1', sources:[], knowledge_context_ids:[],
    conversation_thread_ids:['thread-1'], studio_output_ids:[], output_version_ids:[], generation_settings_ids:[],
    source_deletion_requests:[], conversation:{ conversation_thread_id:'thread-1',
      answer:{ run_id:'run-selected', run_result_id:'result-1', answer:'저장 답변', insufficient:false, citations:[] } } };
  const calls = [];
  const fetchImpl = async (url, init) => { calls.push([url, init]);
    return json({ data, meta:META }, { headers:{ ETag:'"notebook-binding:1"' } }); };
  await getNotebookContext('workspace-1', 'notebook-1', { fetchImpl, runId:'run-selected' });
  await getNotebookContext('workspace-1', 'notebook-1', { fetchImpl, studioJobId:'job-selected' });
  assert.deepEqual(calls.map(([url]) => url), [
    '/bff/api/workspaces/workspace-1/notebooks/notebook-1/context?run_id=run-selected',
    '/bff/api/workspaces/workspace-1/notebooks/notebook-1/context?studio_job_id=job-selected']);
  assert(calls.every(([,init]) => init.credentials === 'same-origin' && init.cache === 'no-store'));
  for (const options of [{ runId:'' }, { studioJobId:'../escape' },
    { runId:'run-selected', studioJobId:'job-selected' }, { runId:'run-selected\n' }]) {
    await assert.rejects(getNotebookContext('workspace-1','notebook-1',{ fetchImpl, ...options }), /NOTEBOOK_INPUT_INVALID/u);
  }
  assert.equal(calls.length, 2);
  await assert.rejects(getNotebookContext('workspace-1','notebook-1',{ fetchImpl, runId:'other-run' }), /NOTEBOOK_CONTEXT_INVALID/u);
});

function s16DeletionContext(state) {
  return { notebook_id: "notebook-1", sources: [], knowledge_context_ids: [], conversation_thread_ids: [],
    studio_output_ids: [], output_version_ids: [], generation_settings_ids: [], conversation: null,
    source_deletion_requests: [{ request_id: "request-s16", source_id: "source-gone", state,
      version: 3, grace_until: "2026-10-16T12:00:00Z", legal_hold_active: false }] };
}

for (const state of ["grace_period", "blocked_by_hold", "awaiting_ack", "cleanup_pending", "failed", "purged"]) {
  test(`S16 삭제 상태 ${state}는 실제 Web context에서 adapter로 원본 binding 없이 복원된다`, async () => {
    const response = await getNotebookContext("workspace-1", "notebook-1", {
      fetchImpl: async (url, init) => {
        assert.equal(url, "/bff/api/workspaces/workspace-1/notebooks/notebook-1/context");
        assert.equal(init.method, "GET"); assert.equal(init.credentials, "same-origin");
        return json({ data: s16DeletionContext(state), meta: META }, { headers: { ETag: '"notebook-binding:7"' } });
      },
    });
    const adapter = createNotebookContextWorkspaceAdapter({}, response.data);
    assert.equal(adapter.bindingEtag, '"notebook-binding:7"');
    assert.deepEqual(await adapter.listSourceDeletionRequests(), [{ request_id: "request-s16", source_id: "source-gone",
      state, version: 3, grace_until: "2026-10-16T12:00:00Z", legal_hold_active: false }]);
    assert.deepEqual(adapter.notebookContext.sources, []);
  });
}

test("S16 삭제 상태 unknown 및 잘못된 exact 6필드 DTO는 두 검증기가 계속 거부한다", async () => {
  const invalids = [
    ...["completed", "cancelled", "deleted", "unknown", "PURGED", null, 42].map(state => item => { item.state = state; }),
    ...["request_id", "source_id", "state", "version", "grace_until", "legal_hold_active"].map(key => item => { delete item[key]; }),
    item => { item.extra = "unapproved"; },
    item => { item.request_id = "../unsafe"; }, item => { item.source_id = ""; },
    ...[0, -1, 1.5, "3", true, Number.MAX_SAFE_INTEGER + 1].map(version => item => { item.version = version; }),
    ...["not-a-date", null].map(time => item => { item.grace_until = time; }),
    ...["false", null].map(hold => item => { item.legal_hold_active = hold; }),
  ];
  for (const mutate of invalids) {
    const data = s16DeletionContext("cleanup_pending"); mutate(data.source_deletion_requests[0]);
    await assert.rejects(getNotebookContext("workspace-1", "notebook-1", {
      fetchImpl: async () => json({ data, meta: META }, { headers: { ETag: '"notebook-binding:7"' } }),
    }), /NOTEBOOK_CONTEXT_INVALID/u);
    assert.throws(() => projectNotebookSelectedContext(data), /NOTEBOOK_CONTEXT_INVALID/u);
  }
});

test("S16 삭제 상태 복원에서도 다른 Notebook 및 잘못된 binding ETag는 거부한다", async () => {
  for (const [notebookId, etag] of [["notebook-other", '"notebook-binding:7"'], ["notebook-1", null],
    ["notebook-1", '"notebook:7"'], ["notebook-1", '"notebook-binding:0"']]) {
    const data = s16DeletionContext("cleanup_pending"); data.notebook_id = notebookId;
    await assert.rejects(getNotebookContext("workspace-1", "notebook-1", {
      fetchImpl: async () => json({ data, meta: META }, { headers: etag === null ? {} : { ETag: etag } }),
    }), /NOTEBOOK_CONTEXT_INVALID/u);
  }
});

test("Web saved sheet_cell context preserves exact Notebook SourceVersion and rejects bad locator/binding", async () => {
  const citations = ['B2','B3','B3','B2','A3'].map((range, index) => ({
    citation_id: `citation-cell-${index}`, source_id: 'source-1', source_version_id: 'source-version-1',
    evidence_span_id: `span-cell-${index}`, page: 1, origin: 'raw_source', context_item_id: 'source-1',
    locator: { kind: 'sheet_cell', value: JSON.stringify({ sheet: index < 2 ? '납품 계획' : '예산', range }) },
  }));
  const data = { notebook_id: 'notebook-1', sources: [{ source_id: 'source-1', source_version_id: 'source-version-1' }],
    knowledge_context_ids: [], conversation_thread_ids: ['thread-1'], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [], source_deletion_requests: [],
    conversation: { conversation_thread_id: 'thread-1', answer: { run_id: 'run-cell', run_result_id: 'result-cell', answer: '저장된 코드·날짜·예산 답변', insufficient: false, citations } } };
  const calls = [];
  const result = await getNotebookContext('workspace-1', 'notebook-1', { fetchImpl: async (url, init) => {
    calls.push({ url, init }); return json({ data, meta: META }, { headers: { ETag: '"notebook-binding:4"' } });
  } });
  assert.deepEqual(result.data, data);
  assert.equal(result.data.bindingEtag, '"notebook-binding:4"');
  assert.equal(calls[0].url, '/bff/api/workspaces/workspace-1/notebooks/notebook-1/context');
  assert.equal(calls[0].init.credentials, 'same-origin');
  const { createNotebookContextWorkspaceAdapter } = await import('../../packages/ui/src/notebook-context-adapter.js');
  const { citationContentUrl } = await import('../../apps/web/lib/question-answering-api.js');
  const restored = await createNotebookContextWorkspaceAdapter({ citationUrl: (citation, options) => citationContentUrl('workspace-1', citation, options) }, result.data).loadNotebookConversation();
  assert.equal(restored.citations.length, 5);
  assert.deepEqual(restored.citations[0].locator, citations[0].locator);
  assert.equal(restored.citations[0].content_url, '/bff/api/workspaces/workspace-1/citations/citation-cell-0/content?notebook_id=notebook-1');
  const invalids = [
    value => value.conversation.answer.citations[0].locator.value = '{"sheet":"납품 계획","range":"A1:Q16"}',
    value => value.conversation.answer.citations[0].locator.value = '{"sheet":"납품 계획", "range":"B2"}',
    value => value.conversation.answer.citations[0].locator.value = '{"sheet":"납품 계획","range":"XFE1"}',
    value => value.sources = [], value => value.sources[0].source_version_id = 'version-other',
    value => value.sources[0].source_id = 'source-other', value => value.notebook_id = 'notebook-other',
    value => value.conversation.answer.citations[0].context_item_id = 'source-other',
  ];
  for (const mutate of invalids) {
    const invalid = structuredClone(data); mutate(invalid);
    await assert.rejects(getNotebookContext('workspace-1', 'notebook-1', { fetchImpl: async () => json({ data: invalid, meta: META }, { headers: { ETag: '"notebook-binding:4"' } }) }), /NOTEBOOK_CONTEXT_INVALID/);
  }
  await assert.rejects(getNotebookContext('workspace-1', 'notebook-1', { fetchImpl: async () => json({ data, meta: META }, { headers: { ETag: '"notebook:4"' } }) }), /NOTEBOOK_CONTEXT_INVALID/);
});

test("DOCX 저장 답변 재진입은 exact raw SourceVersion binding과 compact locator만 복원한다", async () => {
  const citation={citation_id:'citation-docx',source_id:'source-1',source_version_id:'sv-1',evidence_span_id:'span-1',page:1,origin:'raw_source',context_item_id:'source-1',locator:{kind:'docx_block',value:'{"block":"p:2","start":1,"end":2}'}};
  const data={notebook_id:'notebook-1',sources:[{source_id:'source-1',source_version_id:'sv-1'}],knowledge_context_ids:[],conversation_thread_ids:['thread-1'],studio_output_ids:[],output_version_ids:[],generation_settings_ids:[],source_deletion_requests:[],conversation:{conversation_thread_id:'thread-1',answer:{run_id:'run-1',run_result_id:'result-1',answer:'한😀글',insufficient:false,citations:[citation]}}};
  const get=value=>getNotebookContext('workspace-1','notebook-1',{fetchImpl:async()=>json({data:value,meta:META},{headers:{ETag:'"notebook-binding:1"'}})});
  const result=await get(data);
  const {createNotebookContextWorkspaceAdapter}=await import('../../packages/ui/src/notebook-context-adapter.js');
  const {citationContentUrl}=await import('../../apps/web/lib/question-answering-api.js');
  const answer=await createNotebookContextWorkspaceAdapter({citationUrl:(value,opts)=>citationContentUrl('workspace-1',value,opts)},result.data).loadNotebookConversation();
  assert.equal(answer.citations[0].content_url,'/bff/api/workspaces/workspace-1/citations/citation-docx/content?notebook_id=notebook-1');
  assert.deepEqual(answer.citations[0].locator,citation.locator);
  for(const mutate of [v=>v.sources=[],v=>v.sources[0].source_version_id='old',v=>v.conversation.answer.citations[0].context_item_id='other',v=>v.conversation.answer.citations[0].locator.value='{"block":"p:2","start":false,"end":2}']){
    const invalid=structuredClone(data);mutate(invalid);await assert.rejects(get(invalid),/NOTEBOOK_CONTEXT_INVALID/);
  }
});

test("Notebook client는 same-origin 경로와 exact write headers만 사용한다", async () => {
  const captured = [];
  const fetchImpl = async (url, init) => {
    captured.push({ url, method: init.method, headers: init.headers ?? {}, body: init.body ?? null });
    const data = init.method === "GET" && url.endsWith("/notebooks") ? [VIEW] : VIEW;
    return json({ data, meta: META }, { headers: { ETag: '"notebook:1"' } });
  };
  await listNotebooks("workspace-1", { fetchImpl });
  await createNotebook("workspace-1", { title: "Notebook", description: null }, { fetchImpl, idempotencyKey: "notebook-create-0001" });
  await getNotebook("workspace-1", "notebook-1", { fetchImpl });
  await updateNotebookTitle("workspace-1", "notebook-1", "Renamed", { fetchImpl, idempotencyKey: "notebook-update-0001", etag: '"notebook:1"' });
  assert.deepEqual(captured.map(({ url, method }) => ({ url, method })), [
    { url: "/bff/api/workspaces/workspace-1/notebooks", method: "GET" },
    { url: "/bff/api/workspaces/workspace-1/notebooks", method: "POST" },
    { url: "/bff/api/workspaces/workspace-1/notebooks/notebook-1", method: "GET" },
    { url: "/bff/api/workspaces/workspace-1/notebooks/notebook-1", method: "PATCH" },
  ]);
  assert.equal(captured[1].headers["Idempotency-Key"], "notebook-create-0001");
  assert.equal(captured[3].headers["If-Match"], '"notebook:1"');
});

test("Notebook 목록은 각 항목의 etag를 보존해 삭제 If-Match에 사용할 수 있다", async () => {
  const response = await listNotebooks("workspace-1", {
    fetchImpl: async () => json({ data: [VIEW], meta: META }, { headers: { ETag: '"notebooks:1"' } }),
  });
  assert.equal(response.data[0].etag, '"notebook:1"');
});

test("Notebook client는 create/get/update 응답 ETag를 server exact validator로 제한한다", async () => {
  for (const etag of [null, '"notebook-metadata:1"', 'notebook:1', '"notebook:0"', '"notebook:01"', '"garbage"']) {
    const fetchImpl = async () => json({ data: VIEW, meta: META }, { headers: etag ? { ETag: etag } : {} });
    await assert.rejects(
      () => createNotebook("workspace-1", { title: "Notebook" }, { fetchImpl, idempotencyKey: "notebook-create-etag-0001" }),
      /NOTEBOOK_RESPONSE_INVALID/u,
    );
    await assert.rejects(() => getNotebook("workspace-1", "notebook-1", { fetchImpl }), /NOTEBOOK_RESPONSE_INVALID/u);
    await assert.rejects(
      () => updateNotebookTitle("workspace-1", "notebook-1", "Renamed", { fetchImpl, idempotencyKey: "notebook-update-etag-0001", etag: '"notebook:1"' }),
      /NOTEBOOK_RESPONSE_INVALID/u,
    );
  }
});

test("Notebook client는 invalid If-Match를 Network 전에 fail-close한다", async () => {
  for (const etag of ['"notebook-metadata:1"', 'notebook:1', '"notebook:0"', '"notebook:01"', '"garbage"']) {
    let fetchCount = 0;
    await assert.rejects(
      () => updateNotebookTitle("workspace-1", "notebook-1", "Renamed", {
        fetchImpl: async () => { fetchCount += 1; }, idempotencyKey: "notebook-update-etag-0002", etag,
      }),
      /NOTEBOOK_INPUT_INVALID/u,
    );
    assert.equal(fetchCount, 0);
  }
});

test("Notebook client는 unsafe/extra projection을 fail-close한다", async () => {
  for (const data of [
    { ...VIEW, tenant_id: "tenant-1" },
    { ...VIEW, status: "secret" },
    { ...VIEW, source_count: -1 },
    { ...VIEW, updated_at: "yesterday" },
  ]) {
    let fetchCount = 0;
    await assert.rejects(() => getNotebook("workspace-1", "notebook-1", { fetchImpl: async () => { fetchCount += 1; return json({ data, meta: META }); } }), /NOTEBOOK_RESPONSE_INVALID/u);
    assert.equal(fetchCount, 1);
  }
  let inputFetches = 0;
  await assert.rejects(() => createNotebook("../tenant", { title: "Notebook" }, { idempotencyKey: "notebook-create-0001", fetchImpl: async () => { inputFetches += 1; } }), /NOTEBOOK_INPUT_INVALID/u);
  assert.equal(inputFetches, 0);
});

test("Notebook Home session은 same-origin Web projection만 수용한다", async () => {
  const valid = {
    data: {
      user_id: "user-1", tenant_id: "tenant-1", workspace_id: "workspace-1",
      session_id: "session-1", device_id: "device-1", client_kind: "web",
      delivery: "same_origin_secure_cookie", expires_at: "2026-08-16T09:00:00Z",
      recovery_operations: [], password_change_required: false, is_system_admin: true,
    },
    meta: { trace_id: "trace-1" },
  };
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(valid), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  assert.equal((await getCurrentNotebookSession({ fetchImpl })).workspace_id, "workspace-1");
  assert.deepEqual(calls.map(({ url, init }) => ({ url, method: init.method, credentials: init.credentials })), [
    { url: "/bff/api/session", method: "GET", credentials: "same-origin" },
  ]);
  for (const data of [
    { ...valid.data, client_kind: "native" },
    { ...valid.data, delivery: "native_https_opaque_bearer" },
    { ...valid.data, password_change_required: "false" },
    { ...valid.data, is_system_admin: 1 },
    { ...valid.data, internal_policy: "blocked" },
  ]) {
    await assert.rejects(getCurrentNotebookSession({ fetchImpl: async () => new Response(JSON.stringify({ ...valid, data }), { status: 200 }) }), /SESSION_RESPONSE_INVALID/u);
  }
  await assert.rejects(getCurrentNotebookSession({ fetchImpl: async () => new Response("{}", { status: 401 }) }), /AUTHENTICATION_REQUIRED/u);
});

test("Notebook selected Context는 exact same-origin projection만 수용한다", async () => {
  const citation = {
    citation_id: "citation-1", source_id: "source-1", source_version_id: "source-version-1",
    evidence_span_id: "span-1", page: 1, origin: "raw_source", context_item_id: "source-1",
    locator: { kind: "page", value: "1" },
  };
  const data = {
    notebook_id: "notebook-1", sources: [{ source_id: "source-1", source_version_id: "source-version-1" }], knowledge_context_ids: [],
    conversation_thread_ids: ["thread-1"], studio_output_ids: [], output_version_ids: [],
    generation_settings_ids: [], source_deletion_requests: [], conversation: { conversation_thread_id: "thread-1", answer: {
      run_id: "run-1", run_result_id: "result-1", answer: "근거 답변", insufficient: false, citations: [citation],
    } },
  };
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return json({ data, meta: META }, { status: 200, headers: { ETag: '"notebook-binding:1"' } });
  };
  assert.deepEqual((await getNotebookContext("workspace-1", "notebook-1", { fetchImpl })).data, data);
  assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/notebooks/notebook-1/context");
  for (const value of ["5,7,80,20,120,60", "5,7,120,20,120,60"]) {
    const imageContext = { ...data, conversation: { ...data.conversation, answer: { ...data.conversation.answer,
      citations: [{ ...citation, page: 1, locator: { kind: "image_region", value } }] } } };
    const operation = getNotebookContext("workspace-1", "notebook-1", { fetchImpl: async () => json({ data: imageContext, meta: META }, { headers: { ETag: '"notebook-binding:1"' } }) });
    if (value === "5,7,80,20,120,60") assert.deepEqual((await operation).data, imageContext);
    else await assert.rejects(operation, /NOTEBOOK_CONTEXT_INVALID/u);
  }
  for (const availableContext of [
    { ...data, conversation_thread_ids: ["thread-1", "thread-2"], conversation: null },
    { ...data, conversation_thread_ids: ["thread-1", "thread-2"] },
  ]) {
    const response = await getNotebookContext("workspace-1", "notebook-1", {
      fetchImpl: async () => json({ data: availableContext, meta: META }, { status: 200, headers: { ETag: '"notebook-binding:1"' } }),
    });
    assert.deepEqual(response.data, availableContext);
  }
  for (const invalid of [
    { ...data, secret: "blocked" }, { ...data, notebook_id: "../other" },
    { ...data, conversation: { ...data.conversation, answer: { ...data.conversation.answer, internal_url: "https://internal.invalid" } } },
    { ...data, conversation: { ...data.conversation, answer: { ...data.conversation.answer, citations: [{ ...citation, secret: "blocked" }] } } },
    { ...data, conversation: { ...data.conversation, answer: { ...data.conversation.answer, citations: [{ ...citation, locator: { ...citation.locator, extra: true } }] } } },
  ]) {
    await assert.rejects(getNotebookContext("workspace-1", "notebook-1", {
      fetchImpl: async () => json({ data: invalid, meta: META }, { status: 200, headers: { ETag: '"notebook-binding:1"' } }),
    }), /NOTEBOOK_CONTEXT_INVALID/u);
  }
});

test("Source 목록은 exact DTO를 유지하고 safe retryable을 보존한다", async () => {
  const source = {
    source_id: "source-1", source_version_id: "source-version-1", filename: "guide.pdf",
    source_state: "ready", processing_state: "completed", job_state: "completed",
  };
  const payload = { data: { sources: [source] }, meta: META };
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return json(payload, { status: 200 });
  };
  assert.deepEqual(await listWorkspaceSources("workspace-1", { notebookId: "notebook-1", fetchImpl }), [source]);
  assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/sources?notebook_id=notebook-1");
  assert.equal(calls[0].init.credentials, "same-origin");

  for (const retryable of [false, true]) {
    let error;
    try {
      await listWorkspaceSources("workspace-1", {
        notebookId: "notebook-1",
        fetchImpl: async () => json({ error: { code: "SOURCE_LIST_UNAVAILABLE", retryable } }, { status: 503 }),
      });
    } catch (caught) {
      error = caught;
    }
    assert.match(error?.message ?? "", /SOURCE_LIST_UNAVAILABLE/u);
    assert.equal(error.retryable, retryable);
  }
  await assert.rejects(
    () => listWorkspaceSources("workspace-1", {
      notebookId: "notebook-1", fetchImpl: async () => json({ data: { sources: [] }, meta: { trace_id: "trace-1" } }, { status: 200 }),
    }),
    /SOURCE_LIST_RESPONSE_INVALID/u,
  );
});
