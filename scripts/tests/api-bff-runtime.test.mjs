import assert from "node:assert/strict";
import test from "node:test";

import {
  BffConfigurationError,
  createBffProxy,
  parseInternalApiBase,
  parsePublicGatewayOrigin,
} from "../../apps/web/lib/bff-api-proxy.js";

test("BFF local_test public origin은 exact loopback HTTP만 허용하고 production은 계속 HTTPS를 강제한다", () => {
  assert.equal(parsePublicGatewayOrigin("http://localhost:3080", "local_test").origin, "http://localhost:3080");
  assert.equal(parsePublicGatewayOrigin("http://127.0.0.1:3080", "local_test").origin, "http://127.0.0.1:3080");
  for (const invalid of ["http://0.0.0.0:3080", "http://host.docker.internal:3080", "http://localhost:3080/path", "https://localhost:3080"]) {
    assert.throws(() => parsePublicGatewayOrigin(invalid, "local_test"), BffConfigurationError);
  }
  assert.throws(() => parsePublicGatewayOrigin("http://localhost:3080", "production"), BffConfigurationError);
  assert.equal(parsePublicGatewayOrigin("https://app.example.com", "production").origin, "https://app.example.com");
});

test("Native BFF는 login·refresh만 무자격이고 exact 보호 Route에는 단일 Bearer를 요구한다", async () => {
  const { createNativeBffProxy } = await import("../../apps/web/lib/bff-api-proxy.js");
  assert.equal(typeof createNativeBffProxy, "function");
  const captured = [];
  const proxy = createNativeBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({
        url: String(url), method: init.method,
        authorization: init.headers.get("authorization"),
        cookie: init.headers.get("cookie"),
      });
      return Response.json({ data: { status: "accepted" }, meta: {} });
    },
  });

  const login = await proxy(new Request("https://app.example.com/api/v1/auth/native/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer blocked-on-exchange" },
    body: JSON.stringify({ login_id: "user", password: "not-a-real-password" }),
  }), ["auth", "native", "login"]);
  const refresh = await proxy(new Request("https://app.example.com/api/v1/session/refresh", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  }), ["session", "refresh"]);
  const session = await proxy(new Request("https://app.example.com/api/v1/session", {
    headers: { Authorization: "Bearer opaque-native-credential" },
  }), ["session"]);

  assert.deepEqual([login.status, refresh.status, session.status], [200, 200, 200]);
  assert.deepEqual(captured, [
    { url: "https://api.example.com/api/v1/auth/native/login", method: "POST", authorization: null, cookie: null },
    { url: "https://api.example.com/api/v1/session/refresh", method: "POST", authorization: null, cookie: null },
    { url: "https://api.example.com/api/v1/session", method: "GET", authorization: "Bearer opaque-native-credential", cookie: null },
  ]);
});

test("Native BFF exact Workspace·Recovery Route와 query만 허용하고 다른 입력은 upstream 0이다", async () => {
  const { createNativeBffProxy } = await import("../../apps/web/lib/bff-api-proxy.js");
  assert.equal(typeof createNativeBffProxy, "function");
  const captured = [];
  const proxy = createNativeBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return String(url).endsWith("/content")
        ? new Response(Buffer.from("%PDF-1.4"), { headers: { "Content-Type": "application/pdf" } })
        : Response.json({ data: {}, meta: {} });
    },
  });
  const bearer = { Authorization: "Bearer opaque-native-credential" };
  const cases = [
    ["GET", "workspaces/workspace-1/sources", ["workspaces", "workspace-1", "sources"]],
    ["POST", "workspaces/workspace-1/sources", ["workspaces", "workspace-1", "sources"]],
    ["GET", "workspaces/workspace-1/processing-runs/run-1", ["workspaces", "workspace-1", "processing-runs", "run-1"]],
    ["POST", "workspaces/workspace-1/questions", ["workspaces", "workspace-1", "questions"]],
    ["GET", "workspaces/workspace-1/citations/citation-1/content", ["workspaces", "workspace-1", "citations", "citation-1", "content"]],
    ["POST", "workspaces/workspace-1/studio/reports", ["workspaces", "workspace-1", "studio", "reports"]],
    ["GET", "workspaces/workspace-1/studio/outputs", ["workspaces", "workspace-1", "studio", "outputs"]],
    ["POST", "backups", ["backups"]],
    ["GET", "backups?workspace_id=workspace-1", ["backups"]],
    ["GET", "backups/backup-1", ["backups", "backup-1"]],
    ["POST", "backups/backup-1/restore-previews", ["backups", "backup-1", "restore-previews"]],
    ["GET", "restore-requests/restore-1", ["restore-requests", "restore-1"]],
    ["POST", "restore-requests/restore-1/execute", ["restore-requests", "restore-1", "execute"]],
    ["POST", "restore-requests/restore-1/cancel", ["restore-requests", "restore-1", "cancel"]],
  ];
  for (const [method, relative, segments] of cases) {
    const sourceUpload = method === "POST" && relative.endsWith("/sources");
    const request = new Request(`https://app.example.com/api/v1/${relative}`, {
      method, headers: { ...bearer, "Content-Type": sourceUpload ? "application/pdf" : "application/json" },
      body: method === "POST" ? (sourceUpload ? "%PDF-1.4" : "{}") : undefined,
    });
    assert.equal((await proxy(request, segments)).status, 200, `${method} ${relative}`);
  }
  const acceptedCalls = captured.length;
  for (const [request, segments, status] of [
    [new Request("https://app.example.com/api/v1/session"), ["session"], 401],
    [new Request("https://app.example.com/api/v1/session", { headers: { Authorization: "Basic opaque" } }), ["session"], 401],
    [new Request("https://app.example.com/api/v1/session", { headers: { Authorization: "Bearer short" } }), ["session"], 401],
    [new Request("https://app.example.com/api/v1/session", { headers: { Authorization: "Bearer opaque-native-credential", Cookie: "x=y" } }), ["session"], 400],
    [new Request("https://app.example.com/api/v1/admin", { headers: bearer }), ["admin"], 404],
    [new Request("https://app.example.com/api/v1/session", { method: "POST", headers: bearer }), ["session"], 405],
    [new Request("https://app.example.com/api/v1/backups?workspace_id=workspace-1&extra=x", { headers: bearer }), ["backups"], 404],
  ]) {
    assert.equal((await proxy(request, segments)).status, status);
  }
  assert.equal(captured.length, acceptedCalls);
});

test("Native BFF는 Set-Cookie·redirect·oversize·Credential 반사를 fail-close한다", async () => {
  const { createNativeBffProxy } = await import("../../apps/web/lib/bff-api-proxy.js");
  assert.equal(typeof createNativeBffProxy, "function");
  const request = () => new Request("https://app.example.com/api/v1/session", {
    headers: { Authorization: "Bearer opaque-native-credential" },
  });
  for (const upstream of [
    new Response("", { status: 302, headers: { Location: "https://internal.example/private" } }),
    Response.json({ data: {} }, { headers: { "Set-Cookie": "secret=value" } }),
    Response.json({ data: { reflected: "opaque-native-credential" } }),
    new Response("x".repeat(131_073), { headers: { "Content-Type": "application/json" } }),
    new Response("not-json", { headers: { "Content-Type": "text/plain" } }),
    Response.json({ data: {} }, { headers: { "X-Trace-Id": "opaque-native-credential" } }),
  ]) {
    const response = await createNativeBffProxy({
      baseUrl: new URL("https://api.example.com"), fetchImpl: async () => upstream,
    })(request(), ["session"]);
    assert.equal(response.status, 502);
    const text = await response.text();
    assert.doesNotMatch(text, /opaque-native-credential|internal\.example|secret=value/);
    assert.equal(response.headers.has("set-cookie"), false);
    assert.equal(response.headers.has("location"), false);
  }

  let cookieLoginCalls = 0;
  const cookieLogin = await createNativeBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { cookieLoginCalls += 1; return Response.json({ data: {} }); },
  })(new Request("https://app.example.com/api/v1/auth/native/login", {
    method: "POST", headers: { Cookie: "session=blocked", "Content-Type": "application/json" }, body: "{}",
  }), ["auth", "native", "login"]);
  assert.equal(cookieLogin.status, 400);
  assert.equal(cookieLoginCalls, 0);
});

test("Native Recovery JSON은 1MiB, 일반 JSON은 128KiB 응답 상한을 적용한다", async () => {
  const { createNativeBffProxy } = await import("../../apps/web/lib/bff-api-proxy.js");
  const payload = JSON.stringify({ data: "x".repeat(200_000) });
  const fetchImpl = async () => new Response(payload, { headers: { "Content-Type": "application/json" } });
  const bearer = { Authorization: "Bearer opaque-native-credential" };
  const recovery = await createNativeBffProxy({ baseUrl: new URL("https://api.example.com"), fetchImpl })(
    new Request("https://app.example.com/api/v1/backups?workspace_id=workspace-1", { headers: bearer }), ["backups"],
  );
  const ordinary = await createNativeBffProxy({ baseUrl: new URL("https://api.example.com"), fetchImpl })(
    new Request("https://app.example.com/api/v1/session", { headers: bearer }), ["session"],
  );
  assert.deepEqual([recovery.status, ordinary.status], [200, 502]);
  assert.equal(recovery.headers.get("content-length"), String(Buffer.byteLength(payload)));
  assert.equal(recovery.headers.has("transfer-encoding"), false);

  const tooLarge = JSON.stringify({ data: "x".repeat(1024 * 1024) });
  const rejected = await createNativeBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => new Response(tooLarge, { headers: { "Content-Type": "application/json" } }),
  })(new Request("https://app.example.com/api/v1/backups?workspace_id=workspace-1", { headers: bearer }), ["backups"]);
  assert.equal(rejected.status, 502);
});

test("Native media type은 parameter 제거 후 exact JSON/PDF만 허용하고 framing을 계산한다", async () => {
  const { createNativeBffProxy } = await import("../../apps/web/lib/bff-api-proxy.js");
  const calls = [];
  const proxy = createNativeBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (_url, init) => {
      calls.push(init);
      return new Response(JSON.stringify({ data: {} }), {
        headers: { "Content-Type": "application/json; charset=utf-8", "Transfer-Encoding": "chunked" },
      });
    },
  });
  const login = await proxy(new Request("https://app.example.com/api/v1/auth/native/login", {
    method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify({ login_id: "user", password: "sensitive-login-value" }),
  }), ["auth", "native", "login"]);
  assert.equal(login.status, 200);
  assert.equal(login.headers.get("content-type"), "application/json; charset=utf-8");
  assert.equal(login.headers.get("content-length"), String(Buffer.byteLength(JSON.stringify({ data: {} }))));
  assert.equal(login.headers.has("transfer-encoding"), false);

  let invalidCalls = 0;
  const invalid = await createNativeBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { invalidCalls += 1; return Response.json({ data: {} }); },
  })(new Request("https://app.example.com/api/v1/auth/native/login", {
    method: "POST", headers: { "Content-Type": "application/jsonx" }, body: JSON.stringify({ password: "sensitive-login-value" }),
  }), ["auth", "native", "login"]);
  assert.deepEqual([invalid.status, invalidCalls], [415, 0]);

  const reflected = await createNativeBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => new Response(JSON.stringify({ error: "sensitive-login-value" }), { headers: { "Content-Type": "application/json; charset=utf-8" } }),
  })(new Request("https://app.example.com/api/v1/auth/native/login", {
    method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify({ password: "sensitive-login-value" }),
  }), ["auth", "native", "login"]);
  assert.equal(reflected.status, 502);
});

test("public /api/v1 Route Handler는 Native proxy를 실행해 login non-404·Session 401·Cookie upstream0을 보장한다", async () => {
  const route = await import("../../apps/web/app/api/v1/[...path]/route.js");
  const originalBase = process.env.DAON_API_INTERNAL_URL;
  const originalGateway = process.env.DAON_PUBLIC_GATEWAY_URL;
  const originalProfile = process.env.DAON_RUNTIME_PROFILE;
  const originalFetch = globalThis.fetch;
  let calls = 0;
  try {
    process.env.DAON_API_INTERNAL_URL = "https://api.example.com";
    process.env.DAON_PUBLIC_GATEWAY_URL = "https://app.example.com";
    process.env.DAON_RUNTIME_PROFILE = "production";
    globalThis.fetch = async () => {
      calls += 1;
      return Response.json({ error: { code: "INPUT_INVALID", message: "safe" } }, { status: 422 });
    };
    const login = await route.POST(new Request("https://app.example.com/api/v1/auth/native/login", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    }), { params: Promise.resolve({ path: ["auth", "native", "login"] }) });
    const session = await route.GET(new Request("https://app.example.com/api/v1/session"), {
      params: Promise.resolve({ path: ["session"] }),
    });
    const cookie = await route.GET(new Request("https://app.example.com/api/v1/session", {
      headers: { Authorization: "Bearer opaque-native-credential", Cookie: "x=y" },
    }), { params: Promise.resolve({ path: ["session"] }) });
    assert.equal(login.status, 422);
    assert.equal(session.status, 401);
    assert.equal(cookie.status, 400);
    assert.equal(calls, 1);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalBase === undefined) delete process.env.DAON_API_INTERNAL_URL; else process.env.DAON_API_INTERNAL_URL = originalBase;
    if (originalGateway === undefined) delete process.env.DAON_PUBLIC_GATEWAY_URL; else process.env.DAON_PUBLIC_GATEWAY_URL = originalGateway;
    if (originalProfile === undefined) delete process.env.DAON_RUNTIME_PROFILE; else process.env.DAON_RUNTIME_PROFILE = originalProfile;
  }
});

test("BFF internal destination is server-only, fixed and profile constrained", () => {
  assert.throws(() => parseInternalApiBase("http://api.internal:8000", "production"), BffConfigurationError);
  assert.equal(parseInternalApiBase("http://api:8000", "production").origin, "http://api:8000");
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
  const originalGateway = process.env.DAON_PUBLIC_GATEWAY_URL;
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
    process.env.DAON_PUBLIC_GATEWAY_URL = "https://app.example.com";
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
    if (originalGateway === undefined) delete process.env.DAON_PUBLIC_GATEWAY_URL;
    else process.env.DAON_PUBLIC_GATEWAY_URL = originalGateway;
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

test("BFF는 grounded question만 model 상한 100초를 사용하고 일반 route 10초는 유지한다", async () => {
  const calls = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://app.example.com"),
    timeoutMs: 20,
    questionTimeoutMs: 60,
    fetchImpl: async (_url, init) => {
      calls.push(init.signal);
      await new Promise((resolve) => setTimeout(resolve, 30));
      return new Response(JSON.stringify({ data: {}, meta: {} }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    },
  });
  const question = await proxy(new Request("https://app.example.com/bff/api/workspaces/workspace-a/questions", {
    method: "POST", headers: { Origin: "https://app.example.com" }, body: "{}",
  }), ["workspaces", "workspace-a", "questions"]);
  assert.equal(question.status, 200);
  const ordinary = await proxy(new Request("https://app.example.com/bff/api/access-decisions", {
    method: "POST", headers: { Origin: "https://app.example.com" }, body: "{}",
  }), ["access-decisions"]);
  assert.equal(ordinary.status, 504);
  assert.equal((await ordinary.json()).error.code, "GATEWAY_TIMEOUT");
});

test("BFF uses its configured public HTTPS origin behind a reverse proxy", async () => {
  let calls = 0;
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://daon-user.sinsan.kr"),
    fetchImpl: async () => { calls += 1; return Response.json({ data: { status: "ok" }, meta: {} }); },
  });

  const accepted = await proxy(new Request("http://daon-web:3330/bff/api/auth/signup", {
    method: "POST",
    headers: {
      Origin: "https://daon-user.sinsan.kr",
      "Sec-Fetch-Site": "same-origin",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ login_id: "test-user", email: "test@example.com", password: "valid-password" }),
  }), ["auth", "signup"]);
  const rejected = await proxy(new Request("http://daon-web:3330/bff/api/auth/signup", {
    method: "POST",
    headers: {
      Origin: "https://untrusted.example",
      "Sec-Fetch-Site": "same-origin",
      "Content-Type": "application/json",
    },
    body: "{}",
  }), ["auth", "signup"]);

  assert.equal(accepted.status, 200);
  assert.equal(rejected.status, 403);
  assert.equal(calls, 1);
});

test("BFF exposes only bounded Provider settings paths and query", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: {}, meta: {} });
    },
  });
  const getProfiles = await proxy(new Request(
    "https://app.example.com/bff/api/model-profiles?workspace_id=workspace-001&secret=blocked",
  ), ["model-profiles"]);
  const getDeployments = await proxy(new Request(
    "https://app.example.com/bff/api/model-deployments?workspace_id=workspace-001&internal=blocked",
  ), ["model-deployments"]);
  const checkConnection = await proxy(new Request(
    "https://app.example.com/bff/api/model-profiles/UPSTAGE/connection-check?workspace_id=workspace-001&secret=blocked",
  ), ["model-profiles", "UPSTAGE", "connection-check"]);
  const getPolicy = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/model-policy",
  ), ["workspaces", "workspace-001", "model-policy"]);
  const patchPolicy = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/model-policy",
    {
      method: "PATCH",
      headers: {
        Origin: "https://app.example.com",
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
        "If-Match": '"policy-v1"',
        "Idempotency-Key": "idem-policy-001",
      },
      body: JSON.stringify({ selected_deployment_ids: [] }),
    },
  ), ["workspaces", "workspace-001", "model-policy"]);
  assert.deepEqual(
    [getProfiles.status, getDeployments.status, checkConnection.status, getPolicy.status, patchPolicy.status],
    [200, 200, 200, 200, 200],
  );
  assert.deepEqual(captured, [
    { url: "https://api.example.com/api/v1/model-profiles?workspace_id=workspace-001", method: "GET" },
    { url: "https://api.example.com/api/v1/model-deployments?workspace_id=workspace-001", method: "GET" },
    { url: "https://api.example.com/api/v1/model-profiles/UPSTAGE/connection-check?workspace_id=workspace-001", method: "GET" },
    { url: "https://api.example.com/api/v1/workspaces/workspace-001/model-policy", method: "GET" },
    { url: "https://api.example.com/api/v1/workspaces/workspace-001/model-policy", method: "PATCH" },
  ]);

  let rejectedCalls = 0;
  const rejectingProxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { rejectedCalls += 1; return new Response(); },
  });
  const invalidId = await rejectingProxy(new Request(
    "https://app.example.com/bff/api/workspaces/%2Fetc/model-policy",
  ), ["workspaces", "/etc", "model-policy"]);
  const invalidMethod = await rejectingProxy(new Request(
    "https://app.example.com/bff/api/model-profiles",
    { method: "DELETE", headers: { Origin: "https://app.example.com" } },
  ), ["model-profiles"]);
  assert.equal(invalidId.status, 404);
  assert.equal(invalidMethod.status, 405);
  assert.equal(rejectedCalls, 0);
});

test("BFF는 검증된 Workspace Knowledge 목록 GET만 same-origin으로 노출한다", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: { items: [] }, meta: {} });
    },
  });
  const listed = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/knowledge-packages?secret=blocked",
  ), ["workspaces", "workspace-001", "knowledge-packages"]);
  assert.equal(listed.status, 200);
  assert.deepEqual(captured, [{
    url: "https://api.example.com/api/v1/workspaces/workspace-001/knowledge-packages",
    method: "GET",
  }]);

  const denied = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/knowledge-packages",
    { method: "POST", headers: { Origin: "https://app.example.com", "Sec-Fetch-Site": "same-origin" } },
  ), ["workspaces", "workspace-001", "knowledge-packages"]);
  assert.equal(denied.status, 405);
  assert.equal(captured.length, 1);
});

test("BFF는 Workspace 운영상태 GET만 same-origin 안전 경로로 전달한다", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: {}, meta: {} });
    },
  });
  const response = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/operations/status?secret=blocked",
  ), ["workspaces", "workspace-001", "operations", "status"]);
  assert.equal(response.status, 200);
  assert.deepEqual(captured, [{
    url: "https://api.example.com/api/v1/workspaces/workspace-001/operations/status",
    method: "GET",
  }]);
  const denied = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/operations/status",
    { method: "POST", headers: { Origin: "https://app.example.com", "Content-Type": "application/json" }, body: "{}" },
  ), ["workspaces", "workspace-001", "operations", "status"]);
  assert.equal(denied.status, 405);
  assert.equal(captured.length, 1);
});

test("BFF는 출력·버전 설정 GET과 same-origin PATCH만 전달한다", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: {}, meta: {} });
    },
  });
  const segments = ["workspaces", "workspace-001", "output-version-settings"];
  assert.equal((await proxy(new Request("https://app.example.com/bff/api/workspaces/workspace-001/output-version-settings"), segments)).status, 200);
  assert.equal((await proxy(new Request("https://app.example.com/bff/api/workspaces/workspace-001/output-version-settings", {
    method: "PATCH",
    headers: { Origin: "https://app.example.com", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json", "If-Match": '"output-version-settings:workspace-001:0"', "Idempotency-Key": "output-settings-idem-0001" },
    body: JSON.stringify({ default_formats: {}, expected_version: 0 }),
  }), segments)).status, 200);
  assert.deepEqual(captured.map((item) => item.method), ["GET", "PATCH"]);
  assert.ok(captured.every((item) => item.url === "https://api.example.com/api/v1/workspaces/workspace-001/output-version-settings"));
  assert.equal((await proxy(new Request("https://app.example.com/bff/api/workspaces/workspace-001/output-version-settings", { method: "DELETE" }), segments)).status, 405);
  assert.equal(captured.length, 2);
});

test("BFF는 동기화 목록과 same-origin 승인만 전달한다", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: {}, meta: {} });
    },
  });
  const listSegments = ["workspaces", "workspace-001", "sync-operations"];
  assert.equal((await proxy(new Request("https://app.example.com/bff/api/workspaces/workspace-001/sync-operations"), listSegments)).status, 200);
  const approveSegments = ["sync-operations", "sync-operation-001", "approve"];
  assert.equal((await proxy(new Request("https://app.example.com/bff/api/sync-operations/sync-operation-001/approve", {
    method: "POST",
    headers: { Origin: "https://app.example.com", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json", "If-Match": '"sync:sync-operation-001:1"', "Idempotency-Key": "sync-approval-idem-0001" },
    body: "{}",
  }), approveSegments)).status, 200);
  assert.deepEqual(captured.map((item) => item.url), [
    "https://api.example.com/api/v1/workspaces/workspace-001/sync-operations",
    "https://api.example.com/api/v1/sync-operations/sync-operation-001/approve",
  ]);
});

test("BFF uploads a bounded PDF only through the approved workspace source route", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({
        url: String(url),
        method: init.method,
        type: init.headers.get("content-type"),
        filename: init.headers.get("x-source-filename"),
        bytes: init.body.byteLength,
      });
      return Response.json({ data: { status: "accepted" }, meta: {} }, { status: 202 });
    },
  });
  const response = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/sources",
    {
      method: "POST",
      headers: {
        Origin: "https://app.example.com",
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/pdf",
        "Idempotency-Key": "pdf-upload-001",
        "X-Source-Filename": "fixture.pdf",
      },
      body: Buffer.from("%PDF-1.7\nfixture"),
    },
  ), ["workspaces", "workspace-001", "sources"]);
  assert.equal(response.status, 202);
  assert.deepEqual(captured, [{
    url: "https://api.example.com/api/v1/workspaces/workspace-001/sources",
    method: "POST",
    type: "application/pdf",
    filename: "fixture.pdf",
    bytes: 16,
  }]);

  let calls = 0;
  const rejected = await createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async () => { calls += 1; return new Response(); },
  })(new Request("https://app.example.com/bff/api/workspaces/%2Fetc/sources", {
    method: "POST",
    headers: { Origin: "https://app.example.com", "Content-Type": "application/pdf" },
    body: Buffer.from("%PDF-1.7"),
  }), ["workspaces", "/etc", "sources"]);
  assert.equal(rejected.status, 404);
  assert.equal(calls, 0);
});

test("BFF source route는 실제 Runtime과 동일하게 GET 목록과 POST PDF만 허용한다", async () => {
  const calls = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url, init) => {
      calls.push({ url: String(url), method: init.method });
      return Response.json({ data: { sources: [] }, meta: {} });
    },
  });
  const response = await proxy(new Request("https://app.example.com/bff/api/workspaces/workspace-001/sources"), ["workspaces", "workspace-001", "sources"]);
  assert.equal(response.status, 200);
  assert.deepEqual(calls, [{ url: "https://api.example.com/api/v1/workspaces/workspace-001/sources", method: "GET" }]);
});

test("BFF exposes only same-origin document processing status reads", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return Response.json({ data: { job_state: "leased" }, meta: {} });
    },
  });
  const status = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/processing-runs/run-001",
  ), ["workspaces", "workspace-001", "processing-runs", "run-001"]);
  assert.equal(status.status, 200);
  assert.deepEqual(captured, [{
    url: "https://api.example.com/api/v1/workspaces/workspace-001/processing-runs/run-001",
    method: "GET",
  }]);

  const write = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/processing-runs/run-001",
    { method: "POST", headers: { Origin: "https://app.example.com" } },
  ), ["workspaces", "workspace-001", "processing-runs", "run-001"]);
  assert.equal(write.status, 405);
});

test("BFF exposes grounded questions and typed Citation content only through approved same-origin routes", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL("https://app.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return String(url).endsWith("/content")
        ? new Response(Buffer.from("Daon knowledge section"), { headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "X-Citation-Locator-Kind": "section",
        } })
        : Response.json({ data: { answer: "ORANGE-COMPASS-42" }, meta: {} });
    },
  });
  const question = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/questions",
    {
      method: "POST",
      headers: {
        Origin: "https://app.example.com", "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ source_id: "source-cp3", source_version_id: "version-cp3", question: "phrase?" }),
    },
  ), ["workspaces", "workspace-001", "questions"]);
  const citation = await proxy(new Request(
    "https://app.example.com/bff/api/workspaces/workspace-001/citations/citation-cp3/content",
  ), ["workspaces", "workspace-001", "citations", "citation-cp3", "content"]);

  assert.equal(question.status, 200);
  assert.equal(citation.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.equal(citation.headers.get("x-citation-locator-kind"), "section");
  assert.deepEqual(captured, [
    { url: "https://api.example.com/api/v1/workspaces/workspace-001/questions", method: "POST" },
    { url: "https://api.example.com/api/v1/workspaces/workspace-001/citations/citation-cp3/content", method: "GET" },
  ]);
});

test("BFF exposes Question authorization as exact POST only", async () => {
  const captured = [];
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"), publicOrigin: new URL("https://app.example.com"), fetchImpl: async (url, init) => { captured.push({ url: String(url), method: init.method }); return Response.json({ data: {}, meta: {} }, { status: 201 }); } });
  const response = await proxy(new Request("https://app.example.com/bff/api/workspaces/workspace-001/questions/authorization", { method: "POST", headers: { Origin: "https://app.example.com", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json" }, body: JSON.stringify({ source_id: "source", source_version_id: "version", question: "q", password: "memory-only" }) }), ["workspaces", "workspace-001", "questions", "authorization"]);
  assert.equal(response.status, 201);
  assert.deepEqual(captured, [{ url: "https://api.example.com/api/v1/workspaces/workspace-001/questions/authorization", method: "POST" }]);
});

test("BFF allowlists Product Studio lifecycle and bounded export paths", async () => {
  const captured = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    fetchImpl: async (url, init) => {
      captured.push({ url: String(url), method: init.method });
      return String(url).includes("/exports/")
        ? new Response(Buffer.from("%PDF-1.4"), { headers: {
          "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="studio-version-1.pdf"',
          "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", ETag: '"sha256:test"',
        } })
        : Response.json({ data: {}, meta: {} });
    },
  });
  const post = (path, segments) => proxy(new Request(`https://app.example.com/bff/api/${path}`, {
    method: "POST", headers: { Origin: "https://app.example.com", "Content-Type": "application/json", "Idempotency-Key": "studio-action-0001" }, body: "{}",
  }), segments);
  const generation = await post("studio-generation-requests", ["studio-generation-requests"]);
  const review = await post("reviews", ["reviews"]);
  const versions = await proxy(new Request("https://app.example.com/bff/api/studio-outputs/output-1/versions?workspace_id=workspace-1"), ["studio-outputs", "output-1", "versions"]);
  const exportResponse = await proxy(new Request("https://app.example.com/bff/api/studio-outputs/output-1/versions/version-1/exports/pdf?workspace_id=workspace-1"), ["studio-outputs", "output-1", "versions", "version-1", "exports", "pdf"]);
  assert.equal(generation.status, 200);
  assert.equal(review.status, 200);
  assert.equal(versions.status, 200);
  assert.equal(exportResponse.headers.get("content-type"), "application/pdf");
  assert.equal(exportResponse.headers.get("x-content-type-options"), "nosniff");
  assert.equal(exportResponse.headers.get("content-disposition"), 'attachment; filename="studio-version-1.pdf"');
  assert.equal(exportResponse.headers.get("etag"), '"sha256:test"');
  const wrongMethods = await Promise.all([
    proxy(new Request("https://app.example.com/bff/api/reviews"), ["reviews"]),
    post("studio-outputs", ["studio-outputs"]),
  ]);
  assert.deepEqual(wrongMethods.map((response) => response.status), [405, 405]);
  assert.deepEqual(captured, [
    { url: "https://api.example.com/api/v1/studio-generation-requests", method: "POST" },
    { url: "https://api.example.com/api/v1/reviews", method: "POST" },
    { url: "https://api.example.com/api/v1/studio-outputs/output-1/versions?workspace_id=workspace-1", method: "GET" },
    { url: "https://api.example.com/api/v1/studio-outputs/output-1/versions/version-1/exports/pdf?workspace_id=workspace-1", method: "GET" },
  ]);
});
