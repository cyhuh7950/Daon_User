import assert from "node:assert/strict";
import test from "node:test";

import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";


test("egress policy BFF exposes exact GET and POST methods only", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.internal.example"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: {}, meta: {} });
    },
  });
  const get = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-1/egress-policy",
  ), ["workspaces", "workspace-1", "egress-policy"]);
  const post = await proxy(new Request(
    "https://app.example.com/bff/api/organizations/org-1/egress-policy-versions",
    {
      method: "POST",
      headers: {
        Origin: "https://app.example.com",
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
        "Idempotency-Key": "idem-1",
        "If-Match": '"egress-policy:v1"',
      },
      body: "{}",
    },
  ), ["organizations", "org-1", "egress-policy-versions"]);
  assert.deepEqual([get.status, post.status], [200, 200]);
  assert.deepEqual(captured, [
    { url: "https://api.internal.example/api/v1/workspaces/workspace-1/egress-policy", method: "GET" },
    { url: "https://api.internal.example/api/v1/organizations/org-1/egress-policy-versions", method: "POST" },
  ]);

  let calls = 0;
  const rejecting = createBffProxy({
    baseUrl: new URL("https://api.internal.example"),
    fetchImpl: async () => { calls += 1; return new Response(); },
  });
  const invalidGet = await rejecting(new Request(
    "https://app.example.com/bff/api/organizations/org-1/egress-policy-versions",
  ), ["organizations", "org-1", "egress-policy-versions"]);
  const invalidPost = await rejecting(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-1/egress-policy",
    { method: "POST", headers: { Origin: "https://app.example.com" } },
  ), ["workspaces", "workspace-1", "egress-policy"]);
  assert.deepEqual([invalidGet.status, invalidPost.status], [405, 405]);
  assert.equal(calls, 0);
});
