import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";
import { createBffProxy, createNativeBffProxy } from "../../apps/web/lib/bff-api-proxy.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const transactionId = "and-synthetic-transaction-001";
const appLink = "https://link.example.test/android/callback?code=synthetic-code&state=synthetic-state";
const session = {
  user_id: "user-001", tenant_id: "tenant-001", workspace_id: "workspace-001",
  session_id: "session-001", device_id: "device-001", client_kind: "web",
  delivery: "same_origin_secure_cookie", expires_at: "2099-01-01T00:00:00Z",
  recovery_operations: [], password_change_required: false, is_system_admin: false,
};

async function withPage({ search = `?transaction_id=${transactionId}`, fetchImpl }, run) {
  const output = await mkdtemp(path.join(tmpdir(), "daon-android-web-"));
  const dom = installMinimalDom();
  const previousFetch = globalThis.fetch;
  const navigations = [];
  let reactRoot;
  try {
    dom.document.defaultView.location = {
      search,
      assign(value) { navigations.push(value); },
      replace(value) { navigations.push(value); },
    };
    globalThis.fetch = fetchImpl;
    const { build } = await import("vite");
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/app/auth/android/authorize/page.jsx"), formats: ["es"], fileName: "android-authorize" },
      rollupOptions: {
        external: ["react", "react-dom", "react-dom/client"],
        output: { paths: { react: import.meta.resolve("react") } },
      },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("android-authorize") && /\.m?js$/u.test(name));
    assert.ok(entry);
    const { default: Page } = await import(`${pathToFileURL(path.join(output, entry)).href}?entry=${Date.now()}`);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(Page)); await Promise.resolve(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    const click = async (label) => {
      const button = buttonByText(container, label);
      assert.ok(button, `${label} button이 필요하다`);
      await act(async () => { button.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    };
    await run({ container, click, navigations, act });
  } finally {
    if (reactRoot) {
      const { act } = await import("react");
      await act(async () => { reactRoot.unmount(); });
    }
    globalThis.fetch = previousFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
}

test("Android 승인 화면은 JS 승인 응답을 읽지 않고 명시적 계속용 문서 POST form만 렌더한다", async () => {
  const calls = [];
  await withPage({ fetchImpl: async (url, init) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body });
    if (url === "/bff/api/session") return Response.json({ data: session, meta: { trace_id: "trace-session" } });
    throw new Error(`unexpected fetch: ${url}`);
  } }, async ({ container, click, navigations }) => {
    assert.deepEqual(calls.map((call) => [call.url, call.method]), [["/bff/api/session", "GET"]]);
    assert.deepEqual(navigations, []);
    const forms = findElements(container, (node) => node.tagName === "FORM");
    assert.equal(forms.length, 1);
    assert.equal(forms[0].getAttribute("action"), "/bff/api/session/android/authorize");
    assert.equal(forms[0].getAttribute("method"), "post");
    const fields = findElements(forms[0], (node) => node.tagName === "INPUT");
    assert.deepEqual(fields.map((field) => [field.getAttribute("name") ?? field.name,
      field.getAttribute("value") ?? field.value]),
      [["transaction_id", transactionId]]);
    assert.equal(buttonByText(forms[0], "계속")?.getAttribute("type"), "submit");
    await click("계속");
    assert.deepEqual(calls.map((call) => [call.url, call.method]), [["/bff/api/session", "GET"]]);
    assert.deepEqual(navigations, []);
  });
});

test("미인증 사용자는 기존 AuthPane 로그인 뒤 동일 거래 화면에 남고 자동 코드 발급은 없다", async () => {
  const calls = [];
  let authenticated = false;
  await withPage({ fetchImpl: async (url, init) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body });
    if (url === "/bff/api/session") return authenticated
      ? Response.json({ data: session, meta: { trace_id: "trace-session" } })
      : Response.json({ error: { code: "AUTHENTICATION_REQUIRED" } }, { status: 401 });
    if (url === "/bff/api/auth/login") {
      authenticated = true;
      return Response.json({ data: { workspace_id: "workspace-001" }, meta: { trace_id: "trace-login" } });
    }
    throw new Error(`unexpected fetch: ${url}`);
  } }, async ({ container, click, navigations, act }) => {
    const type = async (name, value) => {
      const field = findElements(container, (node) => node.tagName === "INPUT"
        && (node.getAttribute("name") ?? node.name) === name)[0];
      assert.ok(field);
      field.value = value;
      await act(async () => { field.dispatchEvent(new MinimalEvent("input")); });
    };
    await type("login-id", "synthetic-user");
    await type("password", "synthetic-password");
    await click("로그인");
    assert.deepEqual(navigations, []);
    assert.ok(buttonByText(container, "계속"));
    assert.deepEqual(calls.map((call) => [call.url, call.method]), [
      ["/bff/api/session", "GET"], ["/bff/api/auth/login", "POST"], ["/bff/api/session", "GET"],
    ]);
    assert.deepEqual(JSON.parse(calls[1].body), { login_id: "synthetic-user", password: "synthetic-password" });
  });
});

test("Android 승인 BFF는 문서 form POST만 JSON으로 변환하고 검증된 App Link로 본문 없는 303을 준다", async () => {
  const forwarded = [];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.test"), publicOrigin: new URL("https://app.example.test"),
    fetchImpl: async (url, init) => {
      forwarded.push({ url: String(url), cookie: init.headers.get("cookie"),
        origin: init.headers.get("x-daon-csrf-origin"), referer: init.headers.get("x-daon-csrf-referer"),
        contentType: init.headers.get("content-type"), body: Buffer.from(init.body).toString("utf8") });
      return Response.json({ data: { authorization_url: appLink }, meta: { trace_id: "trace-authorize" } });
    },
  });
  const request = (headers = {}, method = "POST", body = `transaction_id=${transactionId}`) => new Request(
    "https://app.example.test/bff/api/session/android/authorize", {
      method, headers: {
        Origin: "https://app.example.test", Referer: `https://app.example.test/auth/android/authorize?transaction_id=${transactionId}`,
        "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
        "Content-Type": "application/x-www-form-urlencoded",
        Cookie: "__Host-daon_session=synthetic-web-session", ...headers,
      },
      ...(method === "POST" ? { body } : {}),
    },
  );
  const accepted = await proxy(request(), ["session", "android", "authorize"]);
  const noCookie = await proxy(request({ Cookie: "" }), ["session", "android", "authorize"]);
  const badOrigin = await proxy(request({ Origin: "https://evil.example.test" }), ["session", "android", "authorize"]);
  const noReferer = await proxy(request({ Referer: "" }), ["session", "android", "authorize"]);
  const jsFetch = await proxy(request({ "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty" }), ["session", "android", "authorize"]);
  const extraField = await proxy(request({}, "POST", `transaction_id=${transactionId}&returnTo=https%3A%2F%2Fevil.example.test`), ["session", "android", "authorize"]);
  const get = await proxy(request({}, "GET"), ["session", "android", "authorize"]);
  const browserStart = await proxy(request(), ["session", "android", "transactions"]);
  const browserExchange = await proxy(request(), ["session", "android", "exchange"]);
  assert.deepEqual([accepted.status, noCookie.status, badOrigin.status, noReferer.status,
    jsFetch.status, extraField.status, get.status, browserStart.status, browserExchange.status],
  [303, 401, 403, 403, 403, 400, 405, 404, 404]);
  assert.equal(accepted.headers.get("location"), appLink);
  assert.equal(accepted.headers.get("cache-control"), "no-store");
  assert.equal(accepted.headers.get("referrer-policy"), "no-referrer");
  assert.equal(await accepted.text(), "");
  assert.deepEqual(forwarded, [{
    url: "https://api.example.test/api/v1/session/android/authorize",
    cookie: "__Host-daon_session=synthetic-web-session",
    origin: "https://app.example.test",
    referer: `https://app.example.test/auth/android/authorize?transaction_id=${transactionId}`,
    contentType: "application/json",
    body: JSON.stringify({ transaction_id: transactionId }),
  }]);
});

test("Android 승인 BFF는 위조 App Link와 upstream Secret 오류를 브라우저에 전달하지 않는다", async () => {
  const responses = [
    Response.json({ data: { authorization_url: "http://evil.example.test/?code=secret&state=state" }, meta: { trace_id: "trace-up" } }),
    Response.json({ data: { authorization_url: "https://link.example.test/android/callback?code=secret" }, meta: { trace_id: "trace-up" } }),
    Response.json({ data: { authorization_url: "https://link.example.test/android/callback?code=secret&code=again&state=state" }, meta: { trace_id: "trace-up" } }),
    Response.json({ data: { authorization_url: "https://link.example.test/android/callback?code=secret&state=state#" }, meta: { trace_id: "trace-up" } }),
    Response.json({ error: { code: "INVALID_REQUEST", details: { code: "synthetic-secret-code" } } }, { status: 400 }),
  ];
  const proxy = createBffProxy({
    baseUrl: new URL("https://api.example.test"), publicOrigin: new URL("https://app.example.test"),
    fetchImpl: async () => responses.shift(),
  });
  for (const expectedStatus of [502, 502, 502, 502, 400]) {
    const response = await proxy(new Request("https://app.example.test/bff/api/session/android/authorize", {
      method: "POST", headers: {
        Origin: "https://app.example.test",
        Referer: `https://app.example.test/auth/android/authorize?transaction_id=${transactionId}`,
        "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
        "Content-Type": "application/x-www-form-urlencoded", Cookie: "__Host-daon_session=synthetic-web-session",
      }, body: `transaction_id=${transactionId}`,
    }), ["session", "android", "authorize"]);
    assert.equal(response.status, expectedStatus);
    assert.equal(response.headers.get("location"), null);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.doesNotMatch(await response.text(), /secret|authorization_url|evil\.example/u);
  }
});

test("Native 공개 거래·교환은 Web Cookie 없이만 전달되고 브라우저 BFF 승인은 열리지 않는다", async () => {
  const forwarded = [];
  const responseBody = JSON.stringify({ data: {}, meta: {} });
  const proxy = createNativeBffProxy({
    baseUrl: new URL("https://api.example.test"), publicOrigin: new URL("https://app.example.test"),
    fetchImpl: async (url, init) => {
      forwarded.push({ url: String(url), cookie: init.headers.get("cookie"),
        authorization: init.headers.get("authorization") });
      return new Response(responseBody, { status: 201, headers: {
        "content-type": "application/json", "content-length": String(Buffer.byteLength(responseBody)),
      } });
    },
  });
  const post = (segment, headers = {}) => proxy(new Request(`https://app.example.test/api/v1/session/android/${segment}`, {
    method: "POST", headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify({ transaction_id: transactionId }),
  }), ["session", "android", segment]);
  const start = await post("transactions");
  const exchange = await post("exchange");
  const cookieStart = await post("transactions", { Cookie: "__Host-daon_session=synthetic-web-session" });
  const cookieExchange = await post("exchange", { Cookie: "__Host-daon_session=synthetic-web-session" });
  const nativeAuthorize = await post("authorize");
  assert.deepEqual([start.status, exchange.status, cookieStart.status, cookieExchange.status,
    nativeAuthorize.status], [201, 201, 400, 400, 404]);
  assert.deepEqual(forwarded, [
    { url: "https://api.example.test/api/v1/session/android/transactions", cookie: null, authorization: null },
    { url: "https://api.example.test/api/v1/session/android/exchange", cookie: null, authorization: null },
  ]);
});

test("이메일 인증 대기 로그인은 AuthPane 상태를 유지하고 Android 코드를 발급하지 않는다", async () => {
  const calls = [];
  await withPage({ fetchImpl: async (url, init) => {
    calls.push({ url, method: init?.method ?? "GET" });
    if (url === "/bff/api/session") return Response.json({ error: { code: "AUTHENTICATION_REQUIRED" } }, { status: 401 });
    if (url === "/bff/api/auth/login") {
      return Response.json({ error: { code: "EMAIL_VERIFICATION_REQUIRED" } }, { status: 403 });
    }
    throw new Error(`unexpected fetch: ${url}`);
  } }, async ({ container, click, navigations }) => {
    await click("로그인");
    assert.match(container.textContent, /EMAIL_VERIFICATION_REQUIRED/u);
    await click("이메일 인증하기");
    assert.ok(findElements(container, (node) => node.tagName === "INPUT"
      && (node.getAttribute("name") ?? node.name) === "verification-token")[0]);
    assert.deepEqual(calls.map((call) => call.url), ["/bff/api/session", "/bff/api/auth/login"]);
    assert.deepEqual(navigations, []);
  });
});

test("비밀번호 변경 요구 세션은 계속 동작 없이 기존 변경 화면만 안내한다", async () => {
  const calls = [];
  await withPage({ fetchImpl: async (url) => {
    calls.push(url);
    return Response.json({ data: { ...session, password_change_required: true }, meta: { trace_id: "trace-session" } });
  } }, async ({ container, navigations }) => {
    assert.equal(buttonByText(container, "계속"), undefined);
    assert.match(container.textContent, /비밀번호를 변경/u);
    assert.deepEqual(calls, ["/bff/api/session"]);
    assert.deepEqual(navigations, []);
  });
});

test("거래 ID 외 returnTo·중복 query는 거부하고 네트워크 요청도 하지 않는다", async () => {
  for (const search of [
    `?transaction_id=${transactionId}&returnTo=https%3A%2F%2Fevil.example.test`,
    `?transaction_id=${transactionId}&transaction_id=and-other`,
    "?returnTo=https%3A%2F%2Fevil.example.test",
  ]) {
    const calls = [];
    await withPage({ search, fetchImpl: async (url) => { calls.push(url); throw new Error("unexpected fetch"); } },
      async ({ container, navigations }) => {
        assert.match(container.textContent, /인증 거래를 확인할 수 없습니다/u);
        assert.deepEqual(calls, []);
        assert.deepEqual(navigations, []);
      });
  }
});

test("일반 AuthPane 로그인은 Android callback 없이 기존 /notebooks 이동을 보존한다", async () => {
  const output = await mkdtemp(path.join(tmpdir(), "daon-android-auth-pane-normal-"));
  const dom = installMinimalDom();
  const previousFetch = globalThis.fetch;
  const navigations = [];
  let reactRoot;
  try {
    dom.document.defaultView.location = { assign(value) { navigations.push(value); } };
    globalThis.fetch = async (url) => {
      assert.equal(url, "/bff/api/auth/login");
      return Response.json({ data: { workspace_id: "workspace-001" }, meta: { trace_id: "trace-login" } });
    };
    const { build } = await import("vite");
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/lib/auth-pane.jsx"), formats: ["es"], fileName: "auth-pane-normal" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"],
        output: { paths: { react: import.meta.resolve("react") } } },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("auth-pane-normal") && /\.m?js$/u.test(name));
    assert.ok(entry);
    const { AuthPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?normal=${Date.now()}`);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(AuthPane)); });
    const login = buttonByText(container, "로그인");
    assert.ok(login);
    await act(async () => { login.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(navigations, ["/notebooks"]);
  } finally {
    if (reactRoot) {
      const { act } = await import("react");
      await act(async () => { reactRoot.unmount(); });
    }
    globalThis.fetch = previousFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("Native 코드 교환 응답이 code·verifier를 반사하면 BFF가 원문 전달을 거부한다", async () => {
  const secretCode = "synthetic-code-not-for-response";
  const secretVerifier = "v".repeat(43);
  for (const reflectedSecret of [secretCode, secretVerifier]) {
    const reflected = JSON.stringify({ error: { code: "INVALID_REQUEST", details: { value: reflectedSecret } } });
    const proxy = createNativeBffProxy({
      baseUrl: new URL("https://api.example.test"), publicOrigin: new URL("https://app.example.test"),
      fetchImpl: async () => new Response(reflected, { status: 400, headers: {
        "content-type": "application/json", "content-length": String(Buffer.byteLength(reflected)),
      } }),
    });
    const result = await proxy(new Request("https://app.example.test/api/v1/session/android/exchange", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transaction_id: transactionId, code: secretCode,
        code_verifier: secretVerifier, redirect_uri: "https://link.example.test/android/callback" }),
    }), ["session", "android", "exchange"]);
    assert.equal(result.status, 502);
    assert.equal((await result.text()).includes(reflectedSecret), false);
  }
});
