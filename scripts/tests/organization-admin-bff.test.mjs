import assert from "node:assert/strict";
import test from "node:test";
import { createBffProxy, parseInternalApiBase } from "../../apps/web/lib/bff-api-proxy.js";
import * as organizationApi from "../../apps/web/lib/organization-admin-api.js";

test("L3 조직 세 GET은 정확 경로와 query 없는 요청만 전달한다", async () => {
  const calls = [];
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"), publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url) => { calls.push(String(url)); return Response.json({ data: [], meta: {} }); } });
  for (const suffix of ["admin/tenants", "tenants/tenant-a/workspaces", "tenants/tenant-a/invitations"]) {
    const segments = ["organization", ...suffix.split("/")];
    assert.equal((await proxy(new Request(`https://app.example.com/bff/api/organization/${suffix}`), segments)).status, 200);
    assert.equal((await proxy(new Request(`https://app.example.com/bff/api/organization/${suffix}?tenant_id=foreign`), segments)).status, 400);
  }
  assert.deepEqual(calls, ["https://api.example.com/api/v1/organization/admin/tenants", "https://api.example.com/api/v1/organization/tenants/tenant-a/workspaces", "https://api.example.com/api/v1/organization/tenants/tenant-a/invitations"]);
});

test("L3 여섯 쓰기는 원 세션·작업키와 현재 폐기 version만 전달한다", async () => {
  const calls = [];
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"), publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url, init) => { calls.push([String(url), init.headers.get("x-daon-expected-session-id"), init.headers.get("idempotency-key")]); return Response.json({ data: {}, meta: {} }); } });
  const mutations = [["POST", "join-requests"], ["POST", "join-requests/request-a/decision"], ["POST", "tenants/tenant-a/invitations"],
    ["DELETE", "tenants/tenant-a/invitations/invitation-a?expected_version=2"], ["PATCH", "tenants/tenant-a/members/user-a/role"], ["PATCH", "tenants/tenant-a/members/user-a/state?active=false"]];
  for (const [method, suffix] of mutations) {
    const segments = ["organization", ...suffix.split("?")[0].split("/")];
    const send = (extra = {}) => proxy(new Request(`https://app.example.com/bff/api/organization/${suffix}`, { method,
      headers: { Origin: "https://app.example.com", "Content-Type": "application/json", "Idempotency-Key": "l3-bff-operation-0001", ...extra }, ...(method !== "DELETE" ? { body: "{}" } : {}) }), segments);
    assert.equal((await send()).status, 422);
    assert.equal((await send({ "X-Daon-Expected-Session-Id": "original-session" })).status, 200);
    assert.equal((await send({ Origin: "https://attacker.example", "X-Daon-Expected-Session-Id": "original-session" })).status, 403);
  }
  assert.equal(calls.length, 6);
  assert.ok(calls.every(([, session, key]) => session === "original-session" && key === "l3-bff-operation-0001"));
  assert.equal(calls[3][0], "https://api.example.com/api/v1/organization/tenants/tenant-a/invitations/invitation-a?expected_version=2");
  const segments = ["organization", "tenants", "tenant-a", "invitations", "invitation-a"];
  for (const query of ["", "?expected_version=bad", "?expected_version=0", "?expected_version=2&actor=foreign", "?expected_version=1&expected_version=2"]) {
    const result = await proxy(new Request(`https://app.example.com/bff/api/organization/tenants/tenant-a/invitations/invitation-a${query}`, { method: "DELETE", headers: { Origin: "https://app.example.com", "X-Daon-Expected-Session-Id": "original-session", "Idempotency-Key": "l3-bff-operation-0001" } }), segments);
    assert.equal(result.status, 422);
  }
  assert.equal(calls.length, 6);
});

test("L3 외부 주소·경로 주입은 upstream 전송 전에 거부한다", async () => {
  let calls = 0;
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"), fetchImpl: async () => { calls++; return Response.json({ data: {} }); } });
  assert.equal((await proxy(new Request("https://app.example.com/bff/api/organization/tenants/x/workspaces"), ["organization", "tenants", "https://attacker.example", "workspaces"])).status, 404);
  assert.throws(() => parseInternalApiBase("https://attacker.example/path?target=secret"));
  assert.equal(calls, 0);
});

test("L3 실제 client helper는 same-origin과 고정 헤더·현재 version 및 안전 DTO를 사용한다", async () => {
  const original = globalThis.fetch, calls = [];
  const stamp = "2026-10-17T00:00:00+00:00";
  const invitation = { invitation_id: "invitation-a", tenant_id: "tenant-a", workspace_id: "workspace-a", created_by: "admin", expires_at: stamp, max_uses: 1, used_count: 0, state: "active", version: 2, created_at: stamp, updated_at: stamp };
  globalThis.fetch = async (url, init) => { calls.push([url, init]); return Response.json({ data: invitation, meta: { trace_id: "trace-a" } }); };
  try {
    const options = { idempotencyKey: "l3-client-frozen-key-0001", expectedSessionId: "session-a", expectedVersion: 2 };
    assert.deepEqual(await organizationApi.revokeInvitation("tenant-a", "invitation-a", options), invitation);
    assert.equal(calls[0][0], "/bff/api/organization/tenants/tenant-a/invitations/invitation-a?expected_version=2");
    assert.equal(calls[0][1].headers.get("X-Daon-Expected-Session-Id"), "session-a");
    assert.equal(calls[0][1].headers.get("Idempotency-Key"), "l3-client-frozen-key-0001");
    assert.equal(calls[0][1].credentials, "same-origin");
    await assert.rejects(organizationApi.revokeInvitation("tenant-a", "invitation-a", { ...options, expectedVersion: undefined }));
    assert.equal(calls.length, 1);
    globalThis.fetch = async () => Response.json({ data: [{ tenant_id: null, display_name: "잘못된 조직" }] });
    await assert.rejects(organizationApi.listManagedOrganizations(), /ORGANIZATION_RESPONSE_INVALID/u);
    globalThis.fetch = async () => Response.json({ data: [{ ...invitation, code_digest: "must-not-consume" }] });
    await assert.rejects(organizationApi.listInvitations("tenant-a"), /ORGANIZATION_RESPONSE_INVALID/u);
  } finally { globalThis.fetch = original; }
});
