import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";

test("Recovery Web Adapter는 정확한 Cloud 7 Route를 same-origin으로만 호출한다", async () => {
  const source = await readFile("apps/web/lib/recovery-api.js", "utf8");
  for (const token of [
    "/api/v1/backups", "/restore-previews", "/api/v1/restore-requests/",
    "/execute", "/cancel", "Idempotency-Key", "If-Match", "same-origin"
  ]) assert.match(source, new RegExp(token.replaceAll("/", "\\/")));
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
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
