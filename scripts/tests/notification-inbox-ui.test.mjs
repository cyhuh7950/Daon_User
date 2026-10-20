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

test("S43 Inbox adapter는 opaque cursor만 인코딩하고 Notifications 첫 조회는 유지한다", async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  const calls = [];
  const page = { data: { items: [], next_cursor: "next/+==?&%" }, meta: {} };
  try {
    globalThis.fetch = async (url, init) => { calls.push({ url, init }); return Response.json(page); };
    const response = await notificationInboxApi.list("inbox", { cursor: "next/+==?&%", signal: controller.signal });
    assert.deepEqual(response, { ok: true, status: 200, payload: page });
    await notificationInboxApi.list("inbox", { cursor: null });
    await notificationInboxApi.list("notifications", { cursor: "next/+==?&%" });
    assert.deepEqual(calls.map(call => call.url), [
      "/bff/api/inbox?limit=50&cursor=next%2F%2B%3D%3D%3F%26%25",
      "/bff/api/inbox?limit=50",
      "/bff/api/notifications?limit=50",
    ]);
    assert.equal(calls[0].init.signal, controller.signal);
    assert.ok(calls.every(call => call.init.credentials === "same-origin" && call.init.cache === "no-store"));
  } finally { globalThis.fetch = originalFetch; }
});

test("Notification 읽음은 HTTP의 randomUUID 부재에서도 기존 CSPRNG로 요청 키를 만든다", async () => {
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const originalFetch = globalThis.fetch;
  const calls = [];
  let randomCalls = 0;
  try {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: {
      getRandomValues(bytes) { randomCalls++; assert.equal(bytes.length, 16); bytes.fill(17); return bytes; },
    } });
    globalThis.fetch = async (url, init) => { calls.push({ url, init }); return Response.json({ data: {}, meta: {} }); };
    await notificationInboxApi.markRead({ id: "notice-http", version: 2 });
    assert.equal(randomCalls, 1);
    assert.equal(calls[0].url, "/bff/api/notifications/notice-http");
    assert.equal(calls[0].init.headers["Idempotency-Key"], "notification-read-11111111-1111-4111-9111-111111111111");
    assert.equal(calls[0].init.headers["If-Match"], '"notification-2"');
    assert.equal(calls[0].init.body, '{"state":"read"}');
  } finally {
    Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
    globalThis.fetch = originalFetch;
  }
});

test("Notification 읽음은 두 암호 API가 모두 없으면 네트워크 전송 없이 안전하게 실패한다", async () => {
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const originalFetch = globalThis.fetch;
  let calls = 0;
  try {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: {} });
    globalThis.fetch = async () => { calls++; throw new Error("전송하면 안 됨"); };
    await assert.rejects(notificationInboxApi.markRead({ id: "notice-http", version: 2 }), /SECURE_RANDOM_UNAVAILABLE/u);
    assert.equal(calls, 0);
  } finally {
    Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
    globalThis.fetch = originalFetch;
  }
});
