import assert from "node:assert/strict";
import test from "node:test";

import { createNotebook, getCurrentNotebookSession, getNotebook, getNotebookContext, listNotebooks, updateNotebookTitle } from "../../apps/web/lib/notebook-api.js";

const VIEW = Object.freeze({ notebook_id: "notebook-1", title: "Notebook", source_count: 0, output_count: 0, updated_at: "2026-08-16T01:02:03Z", status: "empty" });
const META = Object.freeze({ trace_id: "trace-1", workspace_id: "workspace-1" });
const json = (body, init = {}) => Response.json(body, init);

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
      recovery_operations: [],
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
    generation_settings_ids: [], conversation: { conversation_thread_id: "thread-1", answer: {
      run_id: "run-1", run_result_id: "result-1", answer: "근거 답변", insufficient: false, citations: [citation],
    } },
  };
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return json({ data, meta: META });
  };
  assert.deepEqual((await getNotebookContext("workspace-1", "notebook-1", { fetchImpl })).data, data);
  assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/notebooks/notebook-1/context");
  for (const invalid of [
    { ...data, secret: "blocked" }, { ...data, notebook_id: "../other" },
    { ...data, conversation: { ...data.conversation, answer: { ...data.conversation.answer, internal_url: "https://internal.invalid" } } },
    { ...data, conversation: { ...data.conversation, answer: { ...data.conversation.answer, citations: [{ ...citation, secret: "blocked" }] } } },
    { ...data, conversation: { ...data.conversation, answer: { ...data.conversation.answer, citations: [{ ...citation, locator: { ...citation.locator, extra: true } }] } } },
  ]) {
    await assert.rejects(getNotebookContext("workspace-1", "notebook-1", {
      fetchImpl: async () => json({ data: invalid, meta: META }),
    }), /NOTEBOOK_CONTEXT_INVALID/u);
  }
});
