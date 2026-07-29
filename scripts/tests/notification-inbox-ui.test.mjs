import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Notification과 Inbox UI는 실제 same-origin API와 안전 상태만 사용한다", async () => {
  const source = await readFile("packages/ui/src/notification-inbox-pane.jsx", "utf8");
  const adapter = await readFile("apps/web/lib/notification-inbox-api.js", "utf8");
  assert.doesNotMatch(source, /\bfetch\s*\(/);
  assert.match(adapter, /fetch\(`\/api\/v1\/notifications/);
  assert.match(adapter, /"\/api\/v1\/inbox/);
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
