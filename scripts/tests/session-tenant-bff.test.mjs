import assert from "node:assert/strict";
import test from "node:test";

import { createBffProxy, createNativeBffProxy } from "../../apps/web/lib/bff-api-proxy.js";

test("session tenant BFF forwards own-scope routes and denies cross-origin switch", async () => {
  const forwarded = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url, init) => {
      forwarded.push({ url: String(url), method: init.method,
        csrfOrigin: init.headers.get("x-daon-csrf-origin"),
        csrfReferer: init.headers.get("x-daon-csrf-referer") });
      return Response.json({ data: {}, meta: {} });
    },
  });
  const list = await proxy(
    new Request("https://app.example.com/bff/api/session/tenants"),
    ["session", "tenants"],
  );
  const switchRequest = (origin, referer) => new Request(
    "https://app.example.com/bff/api/session/tenant",
    { method: "POST", headers: {
      Origin: origin, "Sec-Fetch-Site": origin === "https://app.example.com" ? "same-origin" : "cross-site",
      ...(referer ? { Referer: referer } : {}), "Content-Type": "application/json",
    }, body: JSON.stringify({ tenant_id: "tenant-002" }) },
  );
  const segments = ["session", "tenant"];
  const denied = await proxy(switchRequest("https://attacker.example", "https://app.example.com/notebooks"), segments);
  const missingReferer = await proxy(switchRequest("https://app.example.com"), segments);
  const switched = await proxy(switchRequest("https://app.example.com", "https://app.example.com/notebooks"), segments);
  assert.deepEqual([list.status, denied.status, missingReferer.status, switched.status], [200, 403, 403, 200]);
  assert.deepEqual(forwarded, [
    { url: "https://api.example.com/api/v1/session/tenants", method: "GET", csrfOrigin: null, csrfReferer: null },
    { url: "https://api.example.com/api/v1/session/tenant", method: "POST",
      csrfOrigin: "https://app.example.com", csrfReferer: "https://app.example.com/notebooks" },
  ]);
});

test("Native gateway exposes only bearer-protected session tenant routes", async () => {
  const forwarded = [];
  const proxy = createNativeBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      forwarded.push({ url: String(url), method: init.method,
        authorization: init.headers.get("authorization") });
      return Response.json({ data: {}, meta: {} });
    },
  });
  const list = await proxy(new Request("https://app.example.com/api/v1/session/tenants", {
    headers: { Authorization: "Bearer opaque-native-credential" },
  }), ["session", "tenants"]);
  const switchRequest = new Request("https://app.example.com/api/v1/session/tenant", {
    method: "POST", headers: { Authorization: "Bearer opaque-native-credential", "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_id: "tenant-002" }),
  });
  const switched = await proxy(switchRequest, ["session", "tenant"]);
  const missingBearer = await proxy(new Request("https://app.example.com/api/v1/session/tenants"), ["session", "tenants"]);
  assert.deepEqual([list.status, switched.status, missingBearer.status], [200, 200, 401]);
  assert.deepEqual(forwarded, [
    { url: "https://api.example.com/api/v1/session/tenants", method: "GET", authorization: "Bearer opaque-native-credential" },
    { url: "https://api.example.com/api/v1/session/tenant", method: "POST", authorization: "Bearer opaque-native-credential" },
  ]);
});

test("새 전환 BFF 경로는 query를 API에 보존하고 Native 잘못된 media type을 422로 거부한다", async () => {
  const upstreamUrls = [];
  const fetchImpl = async (url) => {
    upstreamUrls.push(String(url));
    return Response.json({ error: { code: "INVALID_INPUT" } }, { status: 422 });
  };
  const web = createBffProxy({ baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://app.example.com"), fetchImpl });
  const native = createNativeBffProxy({ baseUrl: new URL("https://api.example.com"), fetchImpl });
  const list = await web(new Request("https://app.example.com/bff/api/session/tenants?other=1"),
    ["session", "tenants"]);
  const switched = await web(new Request("https://app.example.com/bff/api/session/tenant?other=1", {
    method: "POST", headers: { Origin: "https://app.example.com", Referer: "https://app.example.com/notebooks",
      "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_id: "tenant-002" }),
  }), ["session", "tenant"]);
  const wrongType = await native(new Request("https://app.example.com/api/v1/session/tenant", {
    method: "POST", headers: { Authorization: "Bearer opaque-native-credential", "Content-Type": "text/plain" },
    body: "tenant-002",
  }), ["session", "tenant"]);
  assert.deepEqual([list.status, switched.status, wrongType.status], [422, 422, 422]);
  assert.deepEqual(upstreamUrls, [
    "https://api.example.com/api/v1/session/tenants?other=1",
    "https://api.example.com/api/v1/session/tenant?other=1",
    "https://api.example.com/api/v1/session/tenant",
  ]);
});

test("잘못된 GET query도 BFF가 오래된 credential의 API 401을 가리지 않는다", async () => {
  const forwarded = [];
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url) => {
      forwarded.push(String(url));
      return Response.json({ error: { code: "SESSION_REVOKED" } }, { status: 401 });
    },
  });
  const response = await proxy(new Request("https://app.example.com/bff/api/session/tenants?unexpected=1", {
    headers: { Cookie: "__Host-daon_session=stale-credential" },
  }), ["session", "tenants"]);
  assert.equal(response.status, 401);
  assert.deepEqual(forwarded, ["https://api.example.com/api/v1/session/tenants?unexpected=1"]);
});

test("Native 잘못된 전환 media type도 오래된 bearer의 API 401을 가리지 않는다", async () => {
  const forwarded = [];
  const proxy = createNativeBffProxy({ baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      forwarded.push({ url: String(url), type: init.headers.get("content-type") });
      return Response.json({ error: { code: "SESSION_REVOKED" } }, { status: 401 });
    },
  });
  const response = await proxy(new Request("https://app.example.com/api/v1/session/tenant", {
    method: "POST", headers: { Authorization: "Bearer stale-native-credential", "Content-Type": "text/plain" },
    body: "bad-body",
  }), ["session", "tenant"]);
  assert.equal(response.status, 401);
  assert.deepEqual(forwarded, [{ url: "https://api.example.com/api/v1/session/tenant", type: "text/plain" }]);
});
