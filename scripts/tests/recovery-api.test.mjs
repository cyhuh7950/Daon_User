import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";
import { parseRecoverySessionContext, recoveryApi, resolveRecoverySession } from "../../apps/web/lib/recovery-api.js";

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
  assert.match(source, /getSession\(\)\s*\{\s*return request\("\/bff\/api\/session"\)/);
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
