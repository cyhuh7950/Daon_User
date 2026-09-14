import assert from "node:assert/strict";
import test from "node:test";

import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";


test("system user BFF maps only list and same-origin state PATCH", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({
        url: String(url),
        method: init.method,
        idempotencyKey: init.headers.get("idempotency-key"),
      });
      return Response.json({ data: {}, meta: {} });
    },
  });

  const listed = await proxy(
    new Request("https://app.example.com/bff/api/admin/users"),
    ["admin", "users"],
  );
  const changed = await proxy(new Request(
    "https://app.example.com/bff/api/admin/users/user-001/state",
    {
      method: "PATCH",
      headers: {
        Origin: "https://app.example.com",
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
        "Idempotency-Key": "admin-state-change-0001",
      },
      body: JSON.stringify({ state: "suspended" }),
    },
  ), ["admin", "users", "user-001", "state"]);
  const crossOrigin = await proxy(new Request(
    "https://app.example.com/bff/api/admin/users/user-001/state",
    {
      method: "PATCH",
      headers: {
        Origin: "https://attacker.example",
        "Sec-Fetch-Site": "cross-site",
        "Content-Type": "application/json",
        "Idempotency-Key": "cross-origin-denied-0001",
      },
      body: JSON.stringify({ state: "suspended" }),
    },
  ), ["admin", "users", "user-001", "state"]);
  assert.equal(crossOrigin.status, 403);
  assert.equal(captured.length, 2);
  const deleteUser = await proxy(new Request(
    "https://app.example.com/bff/api/admin/users/user-001",
    { method: "DELETE", headers: { Origin: "https://app.example.com" } },
  ), ["admin", "users", "user-001"]);
  const wrongMethod = await proxy(new Request(
    "https://app.example.com/bff/api/admin/users/user-001/state",
    { method: "POST", headers: { Origin: "https://app.example.com" } },
  ), ["admin", "users", "user-001", "state"]);

  assert.deepEqual([listed.status, changed.status, deleteUser.status, wrongMethod.status], [200, 200, 404, 405]);
  assert.deepEqual(captured, [
    { url: "https://api.example.com/api/v1/admin/users", method: "GET", idempotencyKey: null },
    {
      url: "https://api.example.com/api/v1/admin/users/user-001/state",
      method: "PATCH",
      idempotencyKey: "admin-state-change-0001",
    },
  ]);
});

test("authenticated password change BFF maps only same-origin POST", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: { status: "password_changed" }, meta: { trace_id: "trace-1" } });
    },
  });
  const request = (origin, method = "POST") => new Request(
    "https://app.example.com/bff/api/auth/password/change",
    {
      method,
      headers: {
        Origin: origin,
        "Sec-Fetch-Site": origin === "https://app.example.com" ? "same-origin" : "cross-site",
        "Content-Type": "application/json",
      },
      body: method === "POST" ? JSON.stringify({ current_password: "old password", new_password: "new password value" }) : undefined,
    },
  );
  assert.equal((await proxy(request("https://app.example.com"), ["auth", "password", "change"])).status, 200);
  assert.equal((await proxy(request("https://attacker.example"), ["auth", "password", "change"])).status, 403);
  assert.equal((await proxy(request("https://app.example.com", "GET"), ["auth", "password", "change"])).status, 405);
  assert.deepEqual(captured, [{ url: "https://api.example.com/api/v1/auth/password/change", method: "POST" }]);
});
