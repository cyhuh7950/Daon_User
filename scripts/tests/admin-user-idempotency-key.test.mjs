import test from "node:test";
import assert from "node:assert/strict";
import { createAdminUserIdempotencyKey } from "../../apps/web/lib/admin-users-api.js";

test("관리자 사용자 작업 키는 randomUUID가 없는 브라우저에서도 생성된다", () => {
  const originalCrypto = globalThis.crypto;
  try {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: {} });
    const key = createAdminUserIdempotencyKey("admin-create");
    assert.match(key, /^admin-create-[0-9]+-[a-z0-9]+$/u);
    assert.ok(key.length >= 16);
  } finally {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: originalCrypto });
  }
});
