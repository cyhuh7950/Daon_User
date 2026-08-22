import assert from "node:assert/strict";
import test from "node:test";

import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";

test("License BFF는 exact same-origin read/apply route만 server-side API로 전달한다", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method, cookie: init.headers.get("cookie") });
      return Response.json({ data: { product: "daon-user" }, meta: {} });
    },
  });
  const cookie = "__Host-daon_session=opaque-session";
  const read = await proxy(new Request("https://app.example.com/bff/api/workspaces/workspace-1/license", {
    headers: { Cookie: cookie },
  }), ["workspaces", "workspace-1", "license"]);
  const applied = await proxy(new Request("https://app.example.com/bff/api/organizations/tenant-1/license", {
    method: "POST",
    headers: { Cookie: cookie, Origin: "https://app.example.com", "Content-Type": "application/json", "Idempotency-Key": "license-apply-idem-0001" },
    body: JSON.stringify({ document: {}, step_up_authorization_id: "step-up" }),
  }), ["organizations", "tenant-1", "license"]);
  const crossOrigin = await proxy(new Request("https://app.example.com/bff/api/organizations/tenant-1/license", {
    method: "POST",
    headers: { Cookie: cookie, Origin: "https://evil.example", "Content-Type": "application/json", "Idempotency-Key": "license-apply-idem-0002" },
    body: "{}",
  }), ["organizations", "tenant-1", "license"]);

  assert.deepEqual([read.status, applied.status, crossOrigin.status], [200, 200, 403]);
  assert.deepEqual(captured, [
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/license", method: "GET", cookie },
    { url: "https://api.example.com/api/v1/organizations/tenant-1/license", method: "POST", cookie },
  ]);
});
