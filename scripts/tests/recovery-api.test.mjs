import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Recovery Web Adapter는 정확한 Cloud 7 Route를 same-origin으로만 호출한다", async () => {
  const source = await readFile("apps/web/lib/recovery-api.js", "utf8");
  for (const token of [
    "/api/v1/backups", "/restore-previews", "/api/v1/restore-requests/",
    "/execute", "/cancel", "Idempotency-Key", "If-Match", "same-origin"
  ]) assert.match(source, new RegExp(token.replaceAll("/", "\\/")));
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
});
