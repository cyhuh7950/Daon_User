import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";
import { parseRecoverySessionContext, recoveryApi, resolveRecoverySession } from "../../apps/web/lib/recovery-api.js";

test("S44 L1 Web은 기존 Notebook/context를 읽고 새 정확4필드만 same-origin POST한다", async () => {
  const previous = globalThis.fetch, calls = [], controller = new AbortController();
  globalThis.fetch = async (url, init) => {
    calls.push([url, init]);
    if (url.endsWith("/notebooks")) return Response.json({ data: [{ notebook_id: "notebook-a", title: "Notebook", source_count: 1, output_count: 0,
      updated_at: "2026-10-18T00:00:00Z", status: "active", etag: '"notebook:1"' }], meta: { workspace_id: "workspace-a", trace_id: "trace-a" } });
    if (url.endsWith("/context")) return Response.json({ data: { notebook_id: "notebook-a", sources: [{ source_id: "source-a", source_version_id: "version-a" }],
      knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [],
      source_deletion_requests: [], conversation: null }, meta: { workspace_id: "workspace-a", trace_id: "trace-a" } }, { headers: { ETag: '"notebook-binding:1"' } });
    return Response.json({ error: { code: "RESOURCE_UNAVAILABLE" } }, { status: 503 });
  };
  try {
    assert.deepEqual(await recoveryApi.listSelectionNotebooks("workspace-a", { signal: controller.signal }), [{ id: "notebook-a", title: "Notebook" }]);
    assert.deepEqual(await recoveryApi.getSelectionSources("workspace-a", "notebook-a", { signal: controller.signal }), {
      notebookId: "notebook-a", bindingEtag: '"notebook-binding:1"', sources: [{ sourceId: "source-a", versionId: "version-a" }] });
    const input = { workspace_id: "workspace-a", notebook_id: "notebook-a", trigger: "manual", source_version_ids: ["version-a"] };
    await assert.rejects(recoveryApi.createBackupSelection(input, "l1-create-key-00001", { signal: controller.signal }), e => e.status === 503);
    assert.deepEqual(calls.map(([url]) => url), ["/bff/api/workspaces/workspace-a/notebooks", "/bff/api/workspaces/workspace-a/notebooks/notebook-a/context", "/bff/api/backups"]);
    for (const [, init] of calls) assert.deepEqual([init.credentials, init.cache, init.signal], ["same-origin", "no-store", controller.signal]);
    assert.deepEqual(JSON.parse(calls[2][1].body), input);
    assert.equal(calls[2][1].headers["Idempotency-Key"], "l1-create-key-00001");
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
  const data = { workspace_id: "workspace-a", overall_status: "ready", checked_at: "2026-10-18T00:00:00Z",
    components: ["provider", "api", "storage", "sync", "queue"].map(component_id => ({ component_id, status: "ready", safe_code: "COMPONENT_READY", pending_count: 0, recovery_action: "none" })) };
  const controller = new AbortController();
  let value = data;
  try {
    globalThis.fetch = async (url, init) => { calls.push([url, init]); return Response.json({ data: value, meta: { workspace_id: "workspace-a", trace_id: "trace-status" } }); };
    assert.deepEqual(await recoveryApi.getOperationsStatus("workspace-a", { signal: controller.signal }), data);
    assert.equal(calls[0][0], "/bff/api/workspaces/workspace-a/operations/status");
    assert.equal(calls[0][1].signal, controller.signal);
    assert.deepEqual([calls[0][1].method, calls[0][1].credentials, calls[0][1].cache], ["GET", "same-origin", "no-store"]);
    for (value of [{ ...data, workspace_id: "workspace-b" }, { ...data, components: data.components.slice(1) },
      { ...data, components: data.components.map(c => ({ ...c, pending_count: -1 })) }]) {
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
