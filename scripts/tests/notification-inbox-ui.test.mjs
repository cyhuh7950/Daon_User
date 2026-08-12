import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { notificationInboxApi } from "../../apps/web/lib/notification-inbox-api.js";

test("Notification과 Inbox UI는 실제 same-origin API와 안전 상태만 사용한다", async () => {
  const source = await readFile("packages/ui/src/notification-inbox-pane.jsx", "utf8");
  const adapter = await readFile("apps/web/lib/notification-inbox-api.js", "utf8");
  assert.doesNotMatch(source, /\bfetch\s*\(/);
  assert.match(adapter, /fetch\(`\/bff\/api\/notifications/);
  assert.match(adapter, /"\/bff\/api\/inbox/);
  assert.doesNotMatch(adapter, /\/api\/v1/);
  assert.match(adapter, /If-Match/);
  assert.match(adapter, /Idempotency-Key/);
  for (const state of ["loading", "empty", "forbidden", "unavailable", "error", "ready"]) {
    assert.match(source, new RegExp(`\\b${state}\\b`));
  }
  assert.doesNotMatch(source + adapter, /dangerouslySetInnerHTML|NEXT_PUBLIC_API_BASE_URL|https?:\/\/|localhost|127\.0\.0\.1/);
  assert.doesNotMatch(source, /prototype_fixture|deferred_actual|실제 API 미실행/);
});

test("Web route는 Prototype 대신 실제 Notification Inbox Workspace를 연결한다", async () => {
  const notificationPage = await readFile("apps/web/app/notifications/page.jsx", "utf8");
  const inboxPage = await readFile("apps/web/app/inbox/page.jsx", "utf8");
  assert.match(notificationPage, /WebNotificationInboxWorkspace/);
  assert.match(inboxPage, /WebNotificationInboxWorkspace/);
  assert.doesNotMatch(notificationPage + inboxPage, /OperationsRecoveryWorkspace/);
});

test("Notification·Inbox Adapter는 DTO와 request 의미를 보존해 Browser BFF만 호출한다", async () => {
  const originalFetch = globalThis.fetch;
  const originalRandomUUID = crypto.randomUUID;
  const calls = [];
  try {
    crypto.randomUUID = () => "fixed-uuid";
    globalThis.fetch = async (url, init = {}) => {
      calls.push({ url, init });
      return Response.json({ data: { items: [] }, meta: {} });
    };
    await notificationInboxApi.list("notifications");
    await notificationInboxApi.list("inbox");
    await notificationInboxApi.markRead({ id: "notice/1", version: 7 });
  } finally {
    globalThis.fetch = originalFetch;
    crypto.randomUUID = originalRandomUUID;
  }
  assert.deepEqual(calls.map(({ url }) => url), [
    "/bff/api/notifications?limit=50",
    "/bff/api/inbox?limit=50",
    "/bff/api/notifications/notice%2F1",
  ]);
  assert.equal(calls.every(({ init }) => init.credentials === "same-origin"), true);
  assert.equal(calls[2].init.method, "PATCH");
  assert.equal(calls[2].init.headers["If-Match"], '"notification-7"');
  assert.equal(calls[2].init.headers["Idempotency-Key"], "notification-read-fixed-uuid");
  assert.equal(calls[2].init.body, JSON.stringify({ state: "read" }));
  assert.equal(calls.some(({ url }) => url.startsWith("/api/v1")), false);
});
