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

test("BFF forwards only the single bounded Daon session cookie", async () => {
  let forwardedCookie;
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (_url, init) => {
      forwardedCookie = init.headers.get("cookie");
      return Response.json({ data: { status: "ok" }, meta: {} });
    },
  });
  const response = await proxy(new Request("https://app.example.com/bff/api/session", {
    headers: { Cookie: "analytics=secret-other; __Host-daon_session=opaque-session; preference=private" },
  }), ["session"]);
  assert.equal(response.status, 200);
  assert.equal(forwardedCookie, "__Host-daon_session=opaque-session");

  for (const cookie of [
    "__Host-daon_session=one; __Host-daon_session=two",
    `__Host-daon_session=${"x".repeat(4097)}`,
  ]) {
    let calls = 0;
    const rejectingProxy = createBffProxy({
      baseUrl: new URL("https://api.example.com"),
      fetchImpl: async () => { calls += 1; return new Response(); },
    });
    const rejected = await rejectingProxy(new Request("https://app.example.com/bff/api/session", {
      headers: { Cookie: cookie },
    }), ["session"]);
    assert.equal(rejected.status, 400);
    assert.equal((await rejected.json()).error.code, "INVALID_SESSION_COOKIE");
    assert.equal(calls, 0);
  }

  let malformedCalls = 0;
  const malformedHeaders = {
    get(name) {
      if (name.toLowerCase() === "cookie") return "__Host-daon_session=opaque\r\ninjected=value";
      return null;
    },
    *[Symbol.iterator]() {
      yield ["cookie", "__Host-daon_session=opaque\r\ninjected=value"];
    },
  };
  const malformed = await createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { malformedCalls += 1; return new Response(); },
  })({
    method: "GET",
    url: "https://app.example.com/bff/api/session",
    headers: malformedHeaders,
    signal: new AbortController().signal,
  }, ["session"]);
  assert.equal(malformed.status, 400);
  assert.equal(malformedCalls, 0);
});

test("BFF write requests fail closed unless Origin and Fetch Metadata are same-origin", async () => {
  let calls = 0;
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { calls += 1; return Response.json({ data: { allowed: true }, meta: {} }); },
  });
  const writeUrl = "https://app.example.com/bff/api/workspaces/workspace-001/authorization/evaluations";
  for (const headers of [
    {},
    { Origin: "null" },
    { Origin: "https://evil.example.com" },
    { Origin: "https://app.example.com:444" },
    { Origin: "https://app.example.com, https://evil.example.com" },
    { Origin: "https://app.example.com", "Sec-Fetch-Site": "cross-site" },
  ]) {
    const rejected = await proxy(new Request(writeUrl, {
      method: "POST",
      headers: { ...headers, Cookie: "__Host-daon_session=opaque", "Content-Type": "application/json" },
      body: "{}",
    }), ["workspaces", "workspace-001", "authorization", "evaluations"]);
    assert.equal(rejected.status, 403);
    const body = await rejected.text();
    assert.equal(JSON.parse(body).error.code, "CSRF_VALIDATION_FAILED");
    assert.doesNotMatch(body, /opaque/);
  }
  assert.equal(calls, 0);

  const accepted = await proxy(new Request(writeUrl, {
    method: "POST",
    headers: {
      Origin: "https://app.example.com",
      "Sec-Fetch-Site": "same-origin",
      Cookie: "other=private; __Host-daon_session=opaque",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ action: "view", requested_permissions: [] }),
  }), ["workspaces", "workspace-001", "authorization", "evaluations"]);
  assert.equal(accepted.status, 200);
  assert.equal(calls, 1);
});

test("BFF client abort propagates to the upstream fetch signal", async () => {
  const controller = new AbortController();
  let upstreamAborted = false;
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    timeoutMs: 5_000,
    fetchImpl: async (_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () => {
        upstreamAborted = true;
        reject(init.signal.reason);
      }, { once: true });
      controller.abort();
    }),
  });
  const response = await proxy(new Request("https://app.example.com/bff/api/session", {
    signal: controller.signal,
  }), ["session"]);
  assert.equal(response.status, 499);
  assert.equal((await response.json()).error.code, "CLIENT_REQUEST_ABORTED");
  assert.equal(upstreamAborted, true);
});

test("BFF classifies request and response body failures without exposing raw errors", async () => {
  const requestFailureProxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { throw new Error("must not fetch"); },
  });
  const failedRequest = await requestFailureProxy({
    method: "POST",
    url: "https://app.example.com/bff/api/access-decisions",
    headers: new Headers({ Origin: "https://app.example.com" }),
    signal: new AbortController().signal,
    async arrayBuffer() { throw new Error("raw request body secret"); },
  }, ["access-decisions"]);
  assert.equal(failedRequest.status, 400);
  const requestText = await failedRequest.text();
  assert.equal(JSON.parse(requestText).error.code, "INVALID_REQUEST_BODY");
  assert.doesNotMatch(requestText, /raw request body secret/);

  const hangingRequestProxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    timeoutMs: 20,
    fetchImpl: async () => { throw new Error("must not fetch"); },
  });
  const timedOutRequest = await hangingRequestProxy({
    method: "POST",
    url: "https://app.example.com/bff/api/access-decisions",
    headers: new Headers({ Origin: "https://app.example.com" }),
    signal: new AbortController().signal,
    async arrayBuffer() { return new Promise(() => {}); },
  }, ["access-decisions"]);
  assert.equal(timedOutRequest.status, 504);
  assert.equal((await timedOutRequest.json()).error.code, "GATEWAY_TIMEOUT");

  const requestAbortController = new AbortController();
  let requestAbortFetches = 0;
  const abortedRequest = await createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { requestAbortFetches += 1; return new Response(); },
  })({
    method: "POST",
    url: "https://app.example.com/bff/api/access-decisions",
    headers: new Headers({ Origin: "https://app.example.com" }),
    signal: requestAbortController.signal,
    async arrayBuffer() {
      requestAbortController.abort();
      return new Promise(() => {});
    },
  }, ["access-decisions"]);
  assert.equal(abortedRequest.status, 499);
  assert.equal((await abortedRequest.json()).error.code, "CLIENT_REQUEST_ABORTED");
  assert.equal(requestAbortFetches, 0);

  let responseBodyCanceled = false;
  const responseTimeoutProxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    timeoutMs: 20,
    fetchImpl: async () => ({
      status: 200,
      headers: new Headers({ "Content-Type": "application/json" }),
      body: { async cancel() { responseBodyCanceled = true; } },
      arrayBuffer: async () => new Promise(() => {}),
    }),
  });
  const timedOut = await responseTimeoutProxy(new Request("https://app.example.com/bff/api/session"), ["session"]);
  assert.equal(timedOut.status, 504);
  assert.equal((await timedOut.json()).error.code, "GATEWAY_TIMEOUT");
  assert.equal(responseBodyCanceled, true);

  const responseAbortController = new AbortController();
  let abortedResponseBodyCanceled = false;
  const abortedResponse = await createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => ({
      status: 200,
      headers: new Headers(),
      body: { async cancel() { abortedResponseBodyCanceled = true; } },
      async arrayBuffer() {
        responseAbortController.abort();
        return new Promise(() => {});
      },
    }),
  })(new Request("https://app.example.com/bff/api/session", {
    signal: responseAbortController.signal,
  }), ["session"]);
  assert.equal(abortedResponse.status, 499);
  assert.equal((await abortedResponse.json()).error.code, "CLIENT_REQUEST_ABORTED");
  assert.equal(abortedResponseBodyCanceled, true);

  const invalidResponseProxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => ({
      status: 200,
      headers: new Headers(),
      body: null,
      async arrayBuffer() { throw new Error("raw upstream body secret"); },
    }),
  });
  const invalidResponse = await invalidResponseProxy(
    new Request("https://app.example.com/bff/api/session"), ["session"],
  );
  const invalidResponseText = await invalidResponse.text();
  assert.equal(invalidResponse.status, 502);
  assert.equal(JSON.parse(invalidResponseText).error.code, "UPSTREAM_RESPONSE_INVALID");
  assert.doesNotMatch(invalidResponseText, /raw upstream body secret/);
});

test("BFF route configuration and unexpected errors use unique header-matched traces", async () => {
  const route = await import("../../apps/web/app/bff/api/[...path]/route.js");
  const originalBase = process.env.DAON_API_INTERNAL_URL;
  const originalProfile = process.env.DAON_RUNTIME_PROFILE;
  try {
    delete process.env.DAON_API_INTERNAL_URL;
    process.env.DAON_RUNTIME_PROFILE = "production";
    const fixedClientTrace = { "X-Trace-Id": "trace-client-reused" };
    const first = await route.GET(new Request("https://app.example.com/bff/api/session", { headers: fixedClientTrace }), { params: Promise.resolve({ path: ["session"] }) });
    const second = await route.GET(new Request("https://app.example.com/bff/api/session", { headers: fixedClientTrace }), { params: Promise.resolve({ path: ["session"] }) });
    const firstBody = await first.json();
    const secondBody = await second.json();
    assert.equal(first.headers.get("x-trace-id"), firstBody.error.trace_id);
    assert.equal(second.headers.get("x-trace-id"), secondBody.error.trace_id);
    assert.notEqual(firstBody.error.trace_id, secondBody.error.trace_id);
    assert.notEqual(firstBody.error.trace_id, "trace-client-reused");

    process.env.DAON_API_INTERNAL_URL = "https://api.example.com";
    const unexpected = await route.GET(new Request("https://app.example.com/bff/api/session"), {
      params: Promise.reject(new Error("internal route secret")),
    });
    const unexpectedText = await unexpected.text();
    assert.equal(unexpected.status, 500);
    assert.equal(JSON.parse(unexpectedText).error.code, "GATEWAY_UNEXPECTED_ERROR");
    assert.equal(unexpected.headers.get("x-trace-id"), JSON.parse(unexpectedText).error.trace_id);
    assert.doesNotMatch(unexpectedText, /internal route secret/);
  } finally {
    if (originalBase === undefined) delete process.env.DAON_API_INTERNAL_URL;
    else process.env.DAON_API_INTERNAL_URL = originalBase;
    if (originalProfile === undefined) delete process.env.DAON_RUNTIME_PROFILE;
    else process.env.DAON_RUNTIME_PROFILE = originalProfile;
  }
});

test("BFF removes client abort listeners and clears its timeout after success", async () => {
  const controller = new AbortController();
  const signal = controller.signal;
  const originalAdd = signal.addEventListener.bind(signal);
  const originalRemove = signal.removeEventListener.bind(signal);
  let clientAdds = 0;
  let clientRemoves = 0;
  signal.addEventListener = (...args) => { clientAdds += 1; return originalAdd(...args); };
  signal.removeEventListener = (...args) => { clientRemoves += 1; return originalRemove(...args); };
  let upstreamAbortEvents = 0;
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    timeoutMs: 20,
    fetchImpl: async (_url, init) => {
      init.signal.addEventListener("abort", () => { upstreamAbortEvents += 1; }, { once: true });
      return Response.json({ data: { status: "ok" }, meta: {} });
    },
  });
  const response = await proxy({
    method: "GET",
    url: "https://app.example.com/bff/api/session",
    headers: new Headers(),
    signal,
  }, ["session"]);
  assert.equal(response.status, 200);
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.equal(clientAdds, 1);
  assert.equal(clientRemoves, 1);
  assert.equal(upstreamAbortEvents, 0);
});

test("BFF exposes only approved Notification and Inbox paths with same-origin CSRF", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: { items: [], unread_count: 0, next_cursor: null } }, {
        headers: { ETag: '"notification-v1"' },
      });
    },
  });
  const list = await proxy(new Request(
    "https://app.example.com/bff/api/notifications?limit=20&filter=state:unread&search=run",
  ), ["notifications"]);
  const detail = await proxy(new Request(
    "https://app.example.com/bff/api/notifications/notification-001",
  ), ["notifications", "notification-001"]);
  const read = await proxy(new Request(
    "https://app.example.com/bff/api/notifications/notification-001",
    {
      method: "PATCH",
      headers: {
        Origin: "https://app.example.com",
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
        "If-Match": '"notification-v1"',
        "Idempotency-Key": "idem-notification-read-001",
      },
      body: JSON.stringify({ state: "read" }),
    },
  ), ["notifications", "notification-001"]);
  const inbox = await proxy(new Request(
    "https://app.example.com/bff/api/inbox?limit=20",
  ), ["inbox"]);
  assert.deepEqual([list.status, detail.status, read.status, inbox.status], [200, 200, 200, 200]);
  assert.deepEqual(captured, [
    { url: "https://api.example.com/api/v1/notifications?limit=20&filter=state%3Aunread&search=run", method: "GET" },
    { url: "https://api.example.com/api/v1/notifications/notification-001", method: "GET" },
    { url: "https://api.example.com/api/v1/notifications/notification-001", method: "PATCH" },
    { url: "https://api.example.com/api/v1/inbox?limit=20", method: "GET" },
  ]);
  assert.equal(read.headers.get("etag"), '"notification-v1"');

  let crossOriginCalls = 0;
  const rejected = await createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { crossOriginCalls += 1; return new Response(); },
  })(new Request("https://app.example.com/bff/api/notifications/notification-001", {
    method: "PATCH",
    headers: { Origin: "https://evil.example", "Content-Type": "application/json" },
    body: JSON.stringify({ state: "read" }),
  }), ["notifications", "notification-001"]);
  assert.equal(rejected.status, 403);
  assert.equal(crossOriginCalls, 0);
});
