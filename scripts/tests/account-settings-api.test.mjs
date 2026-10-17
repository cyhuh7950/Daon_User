import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { validateOpenApiDocument } from "../verify-openapi-contract.mjs";
import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";
import { getAccountProfile, saveAccountProfile, listAccountDevices, listAccountSessions, requestAccountStepUp, revokeAccountDevice, revokeAccountSession, trustAccountDevice } from "../../apps/web/lib/account-settings-api.js";

test("S36 BFF exact account paths preserve own-scope queries and reject foreign writes", async () => {
  const calls = [];
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"), publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url, init) => { calls.push([String(url), init]); return Response.json({ data: {}, meta: {} }); } });
  for (const kind of ["profile", "devices", "sessions"]) {
    const path = `/bff/api/account/${kind}${kind === "profile" ? "" : "?limit=20&cursor=opaque-page"}`;
    assert.equal((await proxy(new Request(`https://app.example.com${path}`), ["account", kind])).status, 200);
  }
  const write = (path, method, origin = "https://app.example.com") => new Request(`https://app.example.com/bff/api/${path}`, {
    method, headers: { "Content-Type": "application/json", "Idempotency-Key": "s36-bff-key-0001", Origin: origin,
      Referer: `${origin}/settings/account`, "X-Daon-Expected-Session-Id": "ses-original", "Sec-Fetch-Site": origin === "https://app.example.com" ? "same-origin" : "cross-site" }, body: "{}" });
  for (const [path, method] of [["account/profile", "PATCH"], ["devices/dev-own/trust", "POST"], ["devices/dev-own/revoke", "POST"], ["session/revoke", "POST"]]) {
    const segments = path.split("/");
    assert.equal((await proxy(write(path, method, "https://foreign.example"), segments)).status, 403);
    assert.equal((await proxy(write(path, method), segments)).status, 200);
    assert.equal(calls.at(-1)[1].headers.get("x-daon-csrf-referer"), "https://app.example.com/settings/account");
  }
  assert.equal(calls.length, 7);
  const before = calls.length;
  for (const [path, segments] of [["account/devices?user_id=other", ["account", "devices"]], ["account/profile?tenant_id=other", ["account", "profile"]], ["account/unknown", ["account", "unknown"]]]) {
    assert.ok((await proxy(new Request(`https://app.example.com/bff/api/${path}`), segments)).status >= 400);
  }
  assert.equal(calls.length, before);
});

test("S36 client validates exact own profile/projections and sends only same-origin scoped payloads", async () => {
  const calls = [];
  const trace = { trace_id: "trace-account-client" };
  let receipt = { data: { display_name: null, version: 0 }, meta: trace };
  const fetchImpl = async (url, init) => { calls.push([url, init]); return Response.json(receipt); };
  assert.deepEqual(await getAccountProfile({ fetchImpl }), receipt.data);
  receipt = { data: { display_name: "이름 😀", version: 1, replayed: false }, meta: trace };
  assert.deepEqual(await saveAccountProfile({ display_name: "이름 😀", expected_version: 0 }, { fetchImpl, idempotencyKey: "profile-client-0001", expectedSessionId: "ses-original" }), receipt.data);
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body), { display_name: "이름 😀", expected_version: 0 });
  for (const [list, item] of [[listAccountDevices, { device_id: "dev-a", platform: "web", state: "registered", last_seen_at: "2026-10-17T00:00:00+00:00", is_current: true }],
      [listAccountSessions, { session_id: "ses-a", device_id: "dev-a", client_kind: "web", state: "active", expires_at: "2026-10-17T00:00:00+00:00", is_current: true }]]) {
    receipt = { data: { items: [item], next_cursor: null }, meta: trace };
    assert.deepEqual(await list({}, { fetchImpl }), receipt.data);
    receipt.data.items[0].access_digest = "forbidden";
    await assert.rejects(list({}, { fetchImpl }), /ACCOUNT_RESPONSE_INVALID/u);
  }
  for (const [url, init] of calls) {
    assert.match(url, /^\/bff\/api\/account\//u); assert.equal(init.credentials, "same-origin"); assert.equal(init.cache, "no-store");
    assert.ok(!/[?&](actor|user|tenant)_id=/u.test(url));
  }
});

test("S36 client rejects malformed receipts/raw failures and keeps Step-up out of action payload except its required field", async () => {
  const options = { idempotencyKey: "s36-client-action-0001", expectedSessionId: "ses-original", fetchImpl: async () => Response.json({ error: { code: "unsafe-private-value" } }, { status: 503 }) };
  await assert.rejects(getAccountProfile(options), /ACCOUNT_UNAVAILABLE/u);
  let body;
  options.fetchImpl = async (_url, init) => { body = JSON.parse(init.body); return Response.json({ data: { step_up_authorization: "a".repeat(48), issued_at: "2026-10-17T00:00:00Z", expires_at: "2026-10-17T00:05:00Z" }, meta: { trace_id: "trace-step-up" } }); };
  assert.equal((await requestAccountStepUp("dev-a", "transient-input", options)).step_up_authorization.length, 48);
  assert.equal(body.action_group, "device_session_or_sync_key_revoke");
  for (const [mutate, data] of [[revokeAccountDevice, { device_id: "dev-a", state: "revoked", sync_key_revoke_required: true }], [revokeAccountSession, { session_id: "ses-a", state: "revoked" }]]) {
    const id = data.device_id ?? data.session_id;
    options.fetchImpl = async (_url, init) => { body = JSON.parse(init.body); return Response.json({ data, meta: { trace_id: "trace-revoke", replayed: false } }); };
    assert.equal((await mutate(id, "a".repeat(48), options)).state, "revoked");
    assert.equal(body.step_up_authorization.length, 48); assert.equal("password" in body, false);
  }
  options.fetchImpl = async () => Response.json({ data: { id: "dev-a", kind: "device", version: "2026-10-17T00:00:00Z", attributes: { state: "trusted", replayed: false } }, trace_id: "trace-trust" });
  assert.equal((await trustAccountDevice("dev-a", options)).state, "trusted");
  options.fetchImpl = async () => { throw new Error("unsafe-private-value"); };
  await assert.rejects(saveAccountProfile({ display_name: "x", expected_version: 0 }, options), /ACCOUNT_RESULT_UNKNOWN/u);
});

test("S36 OpenAPI fixes exact fields/own scope/versioned write without relaxing existing checks", async () => {
  const document = JSON.parse(await readFile(new URL("../../packages/contracts/openapi/v1/openapi.json", import.meta.url), "utf8"));
  assert.ok(document.paths["/api/v1/account/profile"]?.patch);
  validateOpenApiDocument(document);
  for (const alter of [
    (copy) => { copy.components.schemas.AccountDevice.properties.access_digest = { type: "string" }; },
    (copy) => { copy.components.schemas.AccountProfilePatch.additionalProperties = true; },
    (copy) => { copy.paths["/api/v1/account/devices"].get.parameters.push({ name: "tenant_id", in: "query", schema: { type: "string" } }); },
    (copy) => { copy.components.schemas.AccountSession.properties.expires_at.format = "string"; },
    (copy) => { copy.paths["/api/v1/account/profile"].patch.parameters = copy.paths["/api/v1/account/profile"].patch.parameters.filter((p) => p.$ref !== "#/components/parameters/AccountExpectedSessionId"); },
  ]) { const copy = structuredClone(document); alter(copy); assert.throws(() => validateOpenApiDocument(copy)); }
});

test("S36 profile binding header is mandatory only on PATCH and reaches only its BFF route", async () => {
  const forwarded = [];
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"), publicOrigin: new URL("https://app.example.com"), fetchImpl: async (url, init) => {
    forwarded.push([String(url), init.headers.get("x-daon-expected-session-id")]); return Response.json({ data: {}, meta: {} });
  } });
  const send = (path, method, expected) => proxy(new Request(`https://app.example.com/bff/api/${path}`, { method, headers: {
    "Content-Type": "application/json", Origin: "https://app.example.com", Referer: "https://app.example.com/settings/account", "Sec-Fetch-Site": "same-origin",
    ...(expected !== undefined ? { "X-Daon-Expected-Session-Id": expected } : {}),
  }, ...(method !== "GET" ? { body: "{}" } : {}) }), path.split("/"));
  for (const bad of [undefined, "", "bad/id", "x".repeat(129), "ses-a, ses-b"]) {
    assert.equal((await send("account/profile", "PATCH", bad)).status, 422);
  }
  assert.equal(forwarded.length, 0);
  for (const [path, method] of [["account/profile", "PATCH"], ["account/profile", "GET"], ["auth/password/change", "POST"], ["session/logout", "POST"], ["devices/dev-a/trust", "POST"]]) {
    assert.equal((await send(path, method, "ses-frozen")).status, 200);
    assert.equal(forwarded.at(-1)[1], path === "account/profile" && method === "PATCH" ? "ses-frozen" : null);
  }
});

test("S36 profile client requires original session binding and preserves session-changed as a safe error", async () => {
  const payload = { display_name: "x", expected_version: 0 };
  let calls = 0;
  const options = { idempotencyKey: "profile-header-0001", fetchImpl: async (_url, init) => {
    calls++; assert.equal(init.headers["X-Daon-Expected-Session-Id"], "ses-frozen");
    assert.deepEqual(JSON.parse(init.body), payload);
    return Response.json({ error: { code: "ACCOUNT_SESSION_CHANGED" } }, { status: 409 });
  } };
  for (const bad of [undefined, 123, "bad/id", "x".repeat(129)]) await assert.rejects(saveAccountProfile(payload, { ...options, expectedSessionId: bad }), /ACCOUNT_INPUT_INVALID/u);
  assert.equal(calls, 0);
  await assert.rejects(saveAccountProfile(payload, { ...options, expectedSessionId: "ses-frozen" }), /ACCOUNT_SESSION_CHANGED/u);
  assert.equal(calls, 1);
});

test("S36 strict parser rejects numeric IDs and trace instead of coercing them to strings", async () => {
  const meta = { trace_id: "trace-account-types" };
  const device = { device_id: 123, platform: "web", state: "registered", last_seen_at: "2026-10-17T00:00:00Z", is_current: true };
  const options = { fetchImpl: async () => Response.json({ data: { items: [device], next_cursor: null }, meta }) };
  await assert.rejects(listAccountDevices({}, options), /ACCOUNT_RESPONSE_INVALID/u);
  options.fetchImpl = async () => Response.json({ data: { display_name: null, version: 0 }, meta: { trace_id: 123 } });
  await assert.rejects(getAccountProfile(options), /ACCOUNT_RESPONSE_INVALID/u);
  let calls = 0;
  options.idempotencyKey = "s36-input-type-0001";
  options.fetchImpl = async () => { calls++; throw new Error("unexpected transport"); };
  await assert.rejects(trustAccountDevice(123, options), /ACCOUNT_INPUT_INVALID/u);
  await assert.rejects(requestAccountStepUp(123, "transient-input", options), /ACCOUNT_INPUT_INVALID/u);
  await assert.rejects(revokeAccountSession(123, "a".repeat(48), options), /ACCOUNT_INPUT_INVALID/u);
  assert.equal(calls, 0);
});
