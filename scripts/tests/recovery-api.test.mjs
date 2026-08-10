import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";
import { parseRecoverySessionContext, resolveRecoverySession } from "../../apps/web/lib/recovery-api.js";

test("Recovery Web Adapter는 정확한 Cloud 7 Route를 same-origin으로만 호출한다", async () => {
  const source = await readFile("apps/web/lib/recovery-api.js", "utf8");
  for (const token of [
    "/api/v1/backups", "/restore-previews", "/api/v1/restore-requests/",
    "/execute", "/cancel", "Idempotency-Key", "If-Match", "same-origin"
  ]) assert.match(source, new RegExp(token.replaceAll("/", "\\/")));
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
});

test("Recovery Web Adapter는 same-origin Session Context를 먼저 조회한다", async () => {
  const source = await readFile("apps/web/lib/recovery-api.js", "utf8");
  assert.match(source, /getSession\(\)\s*\{\s*return request\("\/api\/v1\/session"\)/);
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
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
