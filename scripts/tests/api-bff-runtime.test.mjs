import assert from "node:assert/strict";
import test from "node:test";

import {
  BffConfigurationError,
  createBffProxy,
  parseInternalApiBase,
} from "../../apps/web/lib/bff-api-proxy.js";

test("BFF internal destination is server-only, fixed and profile constrained", () => {
  assert.throws(() => parseInternalApiBase("http://api.internal:8000", "production"), BffConfigurationError);
  assert.throws(() => parseInternalApiBase("https://user:pass@api.example.com", "production"), BffConfigurationError);
  assert.throws(() => parseInternalApiBase("https://api.example.com/variable/path", "production"), BffConfigurationError);
  assert.equal(parseInternalApiBase("https://api.example.com", "production").origin, "https://api.example.com");
});

test("BFF forwards only approved method path query and headers", async () => {
  let captured;
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured = { url: String(url), init };
      return new Response(JSON.stringify({ data: { status: "ok" }, meta: {} }), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "X-Trace-Id": "trace-bff-001",
          "Server": "internal-banner",
          "Location": "https://api.example.com/private",
        },
      });
    },
  });
  const response = await proxy(
    new Request("https://app.example.com/bff/api/session?ignored=value", {
      headers: {
        Cookie: "__Host-daon_session=opaque",
        Host: "evil.example",
        Forwarded: "for=10.0.0.1",
        "X-Tenant-Id": "tenant-foreign",
        "X-Role": "organization_admin",
        "X-Trace-Id": "trace-bff-001",
      },
    }),
    ["session"],
  );
  assert.equal(captured.url, "https://api.example.com/api/v1/session");
  assert.equal(captured.init.redirect, "manual");
  assert.equal(captured.init.headers.get("cookie"), "__Host-daon_session=opaque");
  assert.equal(captured.init.headers.has("host"), false);
  assert.equal(captured.init.headers.has("forwarded"), false);
  assert.equal(captured.init.headers.has("x-tenant-id"), false);
  assert.equal(captured.init.headers.has("x-role"), false);
  assert.equal(response.headers.has("server"), false);
  assert.equal(response.headers.has("location"), false);
});

test("BFF rejects unapproved route method redirect and connection failures safely", async () => {
  const noFetch = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { throw new TypeError("connect api.internal"); },
  });
  const disallowed = await noFetch(
    new Request("https://app.example.com/bff/api/admin", { method: "GET" }), ["admin"]
  );
  assert.equal(disallowed.status, 404);
  const method = await noFetch(
    new Request("https://app.example.com/bff/api/session", { method: "DELETE" }), ["session"]
  );
  assert.equal(method.status, 405);
  const failed = await noFetch(
    new Request("https://app.example.com/bff/api/session", { method: "GET" }), ["session"]
  );
  assert.equal(failed.status, 503);
  const failedBody = await failed.text();
  assert.equal(JSON.parse(failedBody).error.retryable, true);
  assert.doesNotMatch(failedBody, /api\.internal/i);

  const redirect = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => new Response(null, {
      status: 302, headers: { Location: "http://internal.example/private" },
    }),
  });
  const redirected = await redirect(
    new Request("https://app.example.com/bff/api/session"), ["session"]
  );
  assert.equal(redirected.status, 502);
  assert.equal(redirected.headers.has("location"), false);
});
