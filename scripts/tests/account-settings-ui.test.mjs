import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

const root = path.resolve(import.meta.dirname, "../..");
const session = Object.freeze({
  user_id: "user-account", login_id: "account-user", tenant_id: "tenant-account",
  workspace_id: "workspace-account", session_id: "session-account", device_id: "device-account",
  client_kind: "web", delivery: "same_origin_secure_cookie", expires_at: "2099-10-17T12:00:00Z",
  recovery_operations: [], password_change_required: false, is_system_admin: false,
});
const props = (node) => node[Object.keys(node).find((key) => key.startsWith("__reactProps"))];
const inputs = (view) => findElements(view.container, (node) => node.tagName === "INPUT" && props(node)?.type === "password");
const emptySettings = (current) => ({ session: current, profile: { display_name: null, version: 0 }, devices: { items: [], next_cursor: null }, sessions: { items: [], next_cursor: null } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const json = (data, status = 200) => new Response(JSON.stringify(data), { status });

async function render(overrides = {}, { route = false, fetchImpl } = {}) {
  const output = await mkdtemp(path.join(root, ".s36-account-react-"));
  const dom = installMinimalDom();
  const priorFetch = globalThis.fetch;
  if (fetchImpl) globalThis.fetch = fetchImpl;
  const redirects = [];
  window.location = { replace: (url) => redirects.push(url) };
  const { build } = await import("vite");
  const { createElement, act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  let reactRoot;
  try {
    await build({ configFile: false, logLevel: "silent", root,
      build: { outDir: output, emptyOutDir: false,
        lib: { entry: path.join(root, route ? "apps/web/app/settings/account/page.jsx" : "apps/web/components/account-settings-workspace.jsx"), formats: ["es"], fileName: "account" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const built = (await readdir(output)).find((name) => /\.m?js$/u.test(name));
    const module = await import(`${pathToFileURL(path.join(output, built)).href}?s36=${Date.now()}`);
    const Component = route ? module.default : module.AccountSettingsWorkspace;
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    const base = { getSession: async () => session, getSettings: async (current) => emptySettings(current), navigate: (url) => redirects.push(url), ...overrides };
    const update = async (value) => { await act(async () => { reactRoot.render(createElement(Component, value)); await Promise.resolve(); }); };
    await update(route ? {} : base);
    return { act, container, redirects, dom, update: (value) => update({ ...base, ...value }),
      cleanup: async () => { await act(async () => reactRoot.unmount()); dom.restore(); globalThis.fetch = priorFetch; await rm(output, { recursive: true, force: true }); } };
  } catch (error) {
    if (reactRoot) await act(async () => reactRoot.unmount());
    dom.restore(); globalThis.fetch = priorFetch; await rm(output, { recursive: true, force: true }); throw error;
  }
}

async function fill(view, current = "qa current input", next = "qa replacement input", confirm = next) {
  const fields = inputs(view);
  assert.equal(fields.length, 3);
  await view.act(async () => {
    [current, next, confirm].forEach((value, index) => props(fields[index]).onChange({ target: { value } }));
  });
}
async function submit(view, times = 1) {
  const form = findElements(view.container, (node) => node.tagName === "FORM")[0];
  assert.ok(form);
  await view.act(async () => { for (let i = 0; i < times; i += 1) form.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
}

test("S36 실제 Route는 기존 same-origin 세션으로 본인 정보만 조회하고 장치 신뢰를 추정하지 않는다", async () => {
  const calls = [];
  const view = await render({}, { route: true, fetchImpl: async (url, options) => {
    calls.push({ url, options }); return json({ data: url.endsWith("/session") ? session : url.includes("/profile") ? { display_name: null, version: 0 } : { items: [], next_cursor: null }, meta: { trace_id: "trace-account" } });
  } });
  try {
    assert.equal(calls.length, 5); assert.equal(calls[0].url, "/bff/api/session");
    assert.deepEqual(calls.map((call) => call.url).sort(), ["/bff/api/account/devices?limit=20", "/bff/api/account/profile", "/bff/api/account/sessions?limit=20", "/bff/api/session", "/bff/api/session"].sort());
    assert.equal(calls[0].options.credentials, "same-origin"); assert.equal(calls[0].options.cache, "no-store");
    for (const value of ["account-user", "user-account", "tenant-account", "workspace-account", "device-account", "session-account", "2099-10-17"]) assert.ok(view.container.textContent.includes(value));
    assert.match(view.container.textContent, /현재 세션의 장치 ID/u);
    assert.match(view.container.textContent, /표시 이름 저장/u);
    assert.match(view.container.textContent, /현재 소속의 본인 등록 장치/u);
    assert.equal(findElements(view.container, (node) => node.tagName === "A" && node.getAttribute("href") === "/notebooks").length, 1);
    assert.doesNotMatch(view.container.textContent, /prototype_fixture|RESOURCE_UNAVAILABLE|신뢰됨/u);
  } finally { await view.cleanup(); }
});

test("S36 조회 실패는 원시 오류와 계정 폼을 노출하지 않고 명시 재시도로 복구한다", async () => {
  let count = 0;
  const view = await render({ getSession: async () => { if (++count === 1) throw new Error("unsafe-secret-value"); return session; } });
  try {
    assert.equal(inputs(view).length, 0); assert.doesNotMatch(view.container.textContent, /unsafe-secret-value|user-account/u);
    assert.match(view.container.textContent, /계정 정보를 불러오지 못했습니다/u);
    await view.act(async () => buttonByText(view.container, "다시 조회").dispatchEvent(new MinimalEvent("click")));
    assert.equal(count, 2); assert.match(view.container.textContent, /user-account/u);
  } finally { await view.cleanup(); }
});

test("S36 세션 401과 강제 변경 세션은 본문/쓰기 없이 해당 인증 화면으로 이동한다", async () => {
  for (const [getSession, target] of [[async () => { throw new Error("AUTHENTICATION_REQUIRED"); }, "/"], [async () => ({ ...session, password_change_required: true }), "/password-change"]]) {
    const view = await render({ getSession });
    try { assert.deepEqual(view.redirects, [target]); assert.equal(inputs(view).length, 0); assert.doesNotMatch(view.container.textContent, /user-account/u); }
    finally { await view.cleanup(); }
  }
});

test("S36 이전 조회의 늦은 성공/401은 새 세션을 덮거나 로그인으로 이동시키지 않는다", async () => {
  for (const reject of [false, true]) {
    const old = deferred(); let oldSignal;
    const view = await render({ getSession: ({ signal }) => { oldSignal = signal; return old.promise; } });
    try {
      await view.update({ getSession: async () => ({ ...session, user_id: "new-account" }) });
      await view.act(async () => { if (reject) old.reject(new Error("AUTHENTICATION_REQUIRED")); else old.resolve(session); await Promise.resolve(); });
      assert.equal(oldSignal.aborted, true); assert.match(view.container.textContent, /new-account/u);
      assert.doesNotMatch(view.container.textContent, /user-account/u); assert.deepEqual(view.redirects, []);
    } finally { await view.cleanup(); }
  }
});

test("S36 비밀번호 유효성·확인 일치 및 같은 tick 중복 제출은 실제 변경 API 호출을 보호한다", async () => {
  const pending = deferred(); const calls = [];
  const view = await render({ changePassword: (...args) => { calls.push(args); return pending.promise; } });
  try {
    assert.match(view.container.textContent, /실제.*비밀번호.*변경/u);
    assert.match(view.container.textContent, /모든 세션.*종료/u);
    for (const values of [["", "qa replacement input"], ["qa current input", "short"], ["qa current input", "x".repeat(257)], ["qa current input", "qa replacement input", "mismatch"]]) {
      await fill(view, ...values); await submit(view); assert.equal(calls.length, 0);
    }
    await fill(view); await submit(view, 2); assert.equal(calls.length, 1);
    assert.deepEqual(calls[0].slice(0, 2), ["qa current input", "qa replacement input"]);
    assert.ok(calls[0][2].signal instanceof AbortSignal);
    assert.ok(inputs(view).every((node) => node.value === "" && props(node).type === "password" && props(node).disabled));
    assert.equal(buttonByText(view.container, "현재 세션 로그아웃").disabled, true);
    await view.act(async () => { pending.resolve({ status: "password_changed" }); await Promise.resolve(); });
    assert.match(view.container.textContent, /비밀번호가 변경되었습니다/u);
    assert.equal(inputs(view).length, 0); assert.doesNotMatch(view.container.textContent, /user-account/u);
    await view.act(async () => buttonByText(view.container, "로그인으로").dispatchEvent(new MinimalEvent("click")));
    assert.deepEqual(view.redirects, ["/"]);
  } finally { await view.cleanup(); }
});

test("S36 비밀번호 실패는 입력/원시 실패값을 폐기하고 성공을 표시하지 않는다", async () => {
  for (const code of ["PASSWORD_POLICY_FAILED", "AUTHENTICATION_REQUIRED", "unsafe-secret-value"]) {
    const view = await render({ changePassword: async () => { throw new Error(code); } });
    try {
      await fill(view); await submit(view);
      assert.ok(inputs(view).every((node) => node.value === ""));
      assert.doesNotMatch(view.container.textContent, /qa current input|qa replacement input|unsafe-secret-value|비밀번호가 변경되었습니다/u);
      assert.ok(findElements(view.container, (node) => node.getAttribute("role") === "alert").length > 0);
      assert.deepEqual(view.redirects, []); assert.match(view.container.textContent, /user-account/u);
    } finally { await view.cleanup(); }
  }
});

test("S36 실제 비밀번호 helper의 body·same-origin과 정상 receipt만 성공으로 처리한다", async () => {
  for (const valid of [true, false]) {
    const calls = [];
    const view = await render({}, { fetchImpl: async (url, options) => {
      calls.push({ url, options }); return json({ data: { status: valid ? "password_changed" : "accepted" }, meta: { trace_id: "trace-account" } });
    } });
    try {
      await fill(view); await submit(view);
      assert.equal(calls.length, 1); assert.equal(calls[0].url, "/bff/api/auth/password/change");
      assert.equal(calls[0].options.credentials, "same-origin");
      assert.deepEqual(JSON.parse(calls[0].options.body), { current_password: "qa current input", new_password: "qa replacement input" });
      assert.equal(view.container.textContent.includes("비밀번호가 변경되었습니다"), valid);
      assert.ok(inputs(view).every((node) => node.value === ""));
    } finally { await view.cleanup(); }
  }
});

test("S36 로그아웃 실패/잘못된 receipt는 현재 정보 보존·중복 방지, 성공만 로그인 이동한다", async () => {
  const pending = deferred(); const calls = [];
  const view = await render({}, { fetchImpl: async (url, options) => {
    calls.push({ url, options }); if (calls.length === 1) return pending.promise;
    return json({ data: { status: "logged_out", replayed: false }, meta: { trace_id: "trace-account" } });
  } });
  try {
    await fill(view);
    const logout = buttonByText(view.container, "현재 세션 로그아웃");
    await view.act(async () => { logout.dispatchEvent(new MinimalEvent("click")); logout.dispatchEvent(new MinimalEvent("click")); });
    assert.equal(calls.length, 1); assert.equal(calls[0].url, "/bff/api/session/logout");
    assert.equal(calls[0].options.body, "{}"); assert.equal(calls[0].options.credentials, "same-origin");
    assert.ok(inputs(view).every((node) => node.value === "" && props(node).disabled));
    await view.act(async () => { pending.resolve(json({ data: { status: "accepted", replayed: false }, meta: { trace_id: "trace-account" } })); await Promise.resolve(); });
    assert.match(view.container.textContent, /user-account/u); assert.match(view.container.textContent, /로그아웃하지 못했습니다/u);
    assert.deepEqual(view.redirects, []);
    await view.act(async () => buttonByText(view.container, "현재 세션 로그아웃").dispatchEvent(new MinimalEvent("click")));
    assert.deepEqual(view.redirects, ["/"]); assert.equal(inputs(view).length, 0);
  } finally { await view.cleanup(); }
});

test("S36 pagehide/복귀는 민감 입력·진행 요청·늦은 응답을 폐기한다", async () => {
  const pending = deferred(); let signal;
  const view = await render({ changePassword: (_old, _new, options) => { signal = options.signal; return pending.promise; } });
  try {
    await fill(view); await submit(view);
    await view.act(async () => view.dom.document.dispatchEvent(new MinimalEvent("pagehide")));
    assert.equal(signal.aborted, true); assert.equal(inputs(view).length, 0);
    await view.act(async () => view.dom.document.dispatchEvent(Object.assign(new MinimalEvent("pageshow"), { persisted: true })));
    assert.match(view.container.textContent, /user-account/u);
    assert.ok(inputs(view).every((node) => node.value === ""));
    await view.act(async () => { pending.resolve({ status: "password_changed" }); await Promise.resolve(); });
    assert.doesNotMatch(view.container.textContent, /비밀번호가 변경되었습니다/u);
    assert.deepEqual(view.redirects, []);
  } finally { await view.cleanup(); }
});

test("S36 실제 Route는 401·변조된 세션 응답을 로그인/조회 실패로 닫는다", async () => {
  for (const status of [401, 200]) {
    const view = await render({}, { route: true, fetchImpl: async () => json(status === 401
      ? { error: { code: "unsafe-secret-value" } }
      : { data: { ...session, access_credential: "unsafe-secret-value" }, meta: { trace_id: "trace-account" } }, status) });
    try {
      assert.equal(inputs(view).length, 0); assert.doesNotMatch(view.container.textContent, /unsafe-secret-value|user-account/u);
      assert.deepEqual(view.redirects, status === 401 ? ["/"] : []);
      if (status === 200) assert.match(view.container.textContent, /계정 정보를 불러오지 못했습니다/u);
    } finally { await view.cleanup(); }
  }
});

test("S36 로그아웃 HTTP 실패는 서버 원문 없이 현재 정보를 보존하고 자동 재요청하지 않는다", async () => {
  let calls = 0;
  const view = await render({}, { fetchImpl: async () => { ++calls; return json({ error: { code: "unsafe-secret-value" } }, 503); } });
  try {
    await view.act(async () => buttonByText(view.container, "현재 세션 로그아웃").dispatchEvent(new MinimalEvent("click")));
    assert.equal(calls, 1); assert.match(view.container.textContent, /user-account/u);
    assert.match(view.container.textContent, /로그아웃하지 못했습니다/u);
    assert.doesNotMatch(view.container.textContent, /unsafe-secret-value/u); assert.deepEqual(view.redirects, []);
  } finally { await view.cleanup(); }
});

test("S36 unmount 뒤 늦은 로그아웃 성공/실패는 이동이나 후속 쓰기를 만들지 않는다", async () => {
  for (const reject of [false, true]) {
    const pending = deferred(); let signal;
    const view = await render({ logout: (options) => { signal = options.signal; return pending.promise; } });
    await view.act(async () => buttonByText(view.container, "현재 세션 로그아웃").dispatchEvent(new MinimalEvent("click")));
    await view.cleanup(); assert.equal(signal.aborted, true);
    await view.act(async () => { if (reject) pending.reject(new Error("unsafe-secret-value")); else pending.resolve({ status: "logged_out", replayed: false }); await Promise.resolve(); });
    assert.deepEqual(view.redirects, []);
  }
});

test("S36 I2 pagehide는 React flush 전에 미제출 password DOM과 보호 본문을 동기 차단한다", async () => {
  const view = await render();
  try {
    await fill(view);
    const fields = inputs(view);
    // React state와 별개로 브라우저 자동완성이 채운 값도 제거해야 한다.
    fields[0].value = "qa autofilled input";
    await view.act(async () => {
      view.dom.document.dispatchEvent(new MinimalEvent("pagehide"));
      const protectedRoot = findElements(view.container, (node) => node.getAttribute("data-session-validated") === "false")[0];
      assert.ok(protectedRoot, "flush 전 보호 DOM이 필요하다");
      assert.equal(protectedRoot.hidden, true); assert.equal(protectedRoot.inert, true);
      assert.equal(protectedRoot.getAttribute("aria-hidden"), "true");
      assert.ok(fields.every((node) => node.value === ""), "flush 전 미제출 password를 비워야 한다");
    });
    await view.act(async () => view.dom.document.dispatchEvent(Object.assign(new MinimalEvent("pageshow"), { persisted: true })));
    assert.ok(inputs(view).every((node) => node.value === ""));
    const visible = findElements(view.container, (node) => node.getAttribute("data-session-validated") === "true")[0];
    assert.ok(visible); assert.equal(visible.hidden, false); assert.equal(visible.inert, false);
  } finally { await view.cleanup(); }
});

test("S36 I2 쓰기 직전 user/tenant/workspace/session/device 변경은 mutation0과 새 표시 전환이다", async () => {
  for (const field of ["user_id", "tenant_id", "workspace_id", "session_id", "device_id"]) {
    for (const kind of ["password", "logout"]) {
      let current = session, calls = 0, reads = 0;
      const view = await render({ getSession: async () => { ++reads; return current; },
        changePassword: async () => { ++calls; return { status: "password_changed" }; },
        logout: async () => { ++calls; return { status: "logged_out", replayed: false }; } });
      try {
        await fill(view); current = { ...session, [field]: `changed-${field}` };
        if (kind === "password") await submit(view, 2);
        else await view.act(async () => buttonByText(view.container, "현재 세션 로그아웃").dispatchEvent(new MinimalEvent("click")));
        assert.equal(reads, 2); assert.equal(calls, 0, `${field}/${kind} mutation 금지`);
        assert.ok(view.container.textContent.includes(`changed-${field}`));
        assert.match(view.container.textContent, /세션이 변경되었습니다/u);
        assert.ok(inputs(view).every((node) => node.value === "")); assert.deepEqual(view.redirects, []);
      } finally { await view.cleanup(); }
    }
  }
});

test("S36 I2 focus/visible/pageshow 복귀는 현재 Cookie의 세션으로 다시 검증한다", async () => {
  for (const event of ["focus", "visibilitychange", "pageshow"]) {
    let current = session, reads = 0;
    const view = await render({ getSession: async () => { ++reads; return current; } });
    try {
      await fill(view); current = { ...session, user_id: "returned-user", session_id: "returned-session" };
      view.dom.document.visibilityState = "visible";
      await view.act(async () => view.dom.document.dispatchEvent(Object.assign(new MinimalEvent(event), { persisted: false })));
      assert.equal(reads, 2); assert.match(view.container.textContent, /returned-user/u);
      assert.doesNotMatch(view.container.textContent, /user-account/u);
      assert.ok(inputs(view).every((node) => node.value === ""));
    } finally { await view.cleanup(); }
  }
});

test("S36 I2 쓰기 전 재검증 중 같은 tick 중복과 늦은 응답은 mutation을 만들지 않는다", async () => {
  let reads = 0, mutations = 0, signal;
  const gate = deferred();
  const view = await render({ getSession: (options) => {
    if (++reads === 1) return Promise.resolve(session); signal = options.signal; return gate.promise;
  }, changePassword: async () => { ++mutations; return { status: "password_changed" }; } });
  try {
    await fill(view); await submit(view, 2);
    assert.equal(reads, 2); assert.equal(mutations, 0);
    assert.ok(inputs(view).every((node) => node.value === "" && props(node).disabled));
    await view.act(async () => buttonByText(view.container, "현재 세션 로그아웃").dispatchEvent(new MinimalEvent("click")));
    assert.equal(reads, 2);
    await view.act(async () => view.dom.document.dispatchEvent(new MinimalEvent("pagehide")));
    assert.equal(signal.aborted, true);
    await view.act(async () => { gate.resolve(session); await Promise.resolve(); });
    assert.equal(mutations, 0); assert.deepEqual(view.redirects, []);
  } finally { await view.cleanup(); }
});

test("S36 I2 mutation AUTH_REQUIRED는 재조회하여 잘못된 현재 비밀번호와 무효 세션을 구분한다", async () => {
  for (const outcome of ["same", "expired", "changed", "unavailable"]) {
    let reads = 0, mutations = 0;
    const view = await render({ getSession: async () => {
      if (++reads < 3 || outcome === "same") return session;
      if (outcome === "expired") throw new Error("AUTHENTICATION_REQUIRED");
      if (outcome === "unavailable") throw new Error("unsafe-secret-value");
      return { ...session, user_id: "after-auth-user", session_id: "after-auth-session" };
    }, changePassword: async () => { ++mutations; throw new Error("AUTHENTICATION_REQUIRED"); } });
    try {
      await fill(view); await submit(view);
      assert.equal(reads, 3); assert.equal(mutations, 1);
      assert.ok(inputs(view).every((node) => node.value === ""));
      assert.doesNotMatch(view.container.textContent, /unsafe-secret-value|qa current input/u);
      if (outcome === "same") { assert.match(view.container.textContent, /현재 비밀번호.*확인/u); assert.match(view.container.textContent, /user-account/u); }
      else if (outcome === "changed") { assert.match(view.container.textContent, /after-auth-user/u); assert.doesNotMatch(view.container.textContent, /user-account/u); }
      else { assert.equal(inputs(view).length, 0); assert.doesNotMatch(view.container.textContent, /user-account/u); }
      assert.deepEqual(view.redirects, outcome === "expired" ? ["/"] : []);
    } finally { await view.cleanup(); }
  }
});

const profileField = (view) => findElements(view.container, (node) => node.tagName === "INPUT" && props(node)?.type === "text")[0];
const formNamed = (view, name) => findElements(view.container, (node) => node.tagName === "FORM" && node.getAttribute("aria-label") === name)[0];
const buttonNamed = (view, name) => findElements(view.container, (node) => node.tagName === "BUTTON" && node.getAttribute("aria-label") === name)[0];
const settingsFixture = (current = session) => ({ ...emptySettings(current), profile: { display_name: "기존 이름", version: 2 },
  devices: { items: [{ device_id: current.device_id, platform: "web", state: "registered", last_seen_at: current.expires_at, is_current: true },
    { device_id: "dev-other", platform: "windows", state: "registered", last_seen_at: current.expires_at, is_current: false }], next_cursor: null },
  sessions: { items: [{ session_id: current.session_id, device_id: current.device_id, client_kind: "web", state: "active", expires_at: current.expires_at, is_current: true },
    { session_id: "ses-other", device_id: "dev-other", client_kind: "native", state: "active", expires_at: current.expires_at, is_current: false }], next_cursor: null } });
const typeProfile = async (view, value) => view.act(async () => props(profileField(view)).onChange({ target: { value } }));
const sendNamed = async (view, name, times = 1) => view.act(async () => { for (let n = 0; n < times; n++) formNamed(view, name).dispatchEvent(new MinimalEvent("submit")); });

test("S36 C4 profile unknown retry reuses its exact key; confirmed save reloads and reentry reads persisted version", async () => {
  let stored = { display_name: "기존 이름", version: 2 };
  const writes = []; let reads = 0;
  const getSettings = async (current) => { reads++; return { ...settingsFixture(current), profile: stored }; };
  const saveProfile = async (payload, options) => {
    writes.push([payload, options.idempotencyKey, options.expectedSessionId]);
    if (writes.length === 1) { stored = { display_name: payload.display_name, version: 3 }; throw new Error("ACCOUNT_RESULT_UNKNOWN"); }
    return { ...stored, replayed: true };
  };
  const view = await render({ getSettings, saveProfile });
  try {
    assert.equal(profileField(view).value, "기존 이름"); await typeProfile(view, "  변경 이름  ");
    await sendNamed(view, "표시 이름 저장", 2);
    assert.equal(writes.length, 1); assert.equal(writes[0][0].display_name, "변경 이름");
    assert.match(view.container.textContent, /자동 재제출하지/u); assert.equal(profileField(view).disabled, true);
    await sendNamed(view, "표시 이름 저장");
    assert.equal(writes.length, 2); assert.equal(writes[0][1], writes[1][1]); assert.deepEqual(writes[0][0], writes[1][0]);
    assert.equal(writes[0][2], session.session_id); assert.equal(writes[1][2], session.session_id);
    assert.equal(reads, 2); assert.equal(profileField(view).value, "변경 이름"); assert.match(view.container.textContent, /표시 이름을 저장했습니다/u);
    await view.act(async () => view.dom.document.dispatchEvent(new MinimalEvent("focus")));
    assert.equal(profileField(view).value, "변경 이름"); assert.match(view.container.textContent, /저장 version: 3/u);
  } finally { await view.cleanup(); }
});

test("S36 C4 profile412 reloads remote version without auto-save; unsafe failure values never render", async () => {
  for (const reason of ["ACCOUNT_VERSION_CONFLICT", "private-secret-value"]) {
    let writes = 0;
    const view = await render({ getSettings: async (current) => ({ ...settingsFixture(current), profile: { display_name: writes ? "원격 변경" : "기존 이름", version: writes ? 3 : 2 } }),
      saveProfile: async () => { writes++; throw new Error(reason); } });
    try {
      await typeProfile(view, "저장 시도"); await sendNamed(view, "표시 이름 저장");
      assert.equal(writes, 1); assert.doesNotMatch(view.container.textContent, /private-secret-value|표시 이름을 저장했습니다/u);
      if (reason === "ACCOUNT_VERSION_CONFLICT") { assert.equal(profileField(view).value, "원격 변경"); assert.match(view.container.textContent, /다른 화면/u); }
    } finally { await view.cleanup(); }
  }
});

test("S36 C4 sensitive action requires reauthentication, deduplicates, reloads and marks sync-key followup partial", async () => {
  let grants = 0, mutations = 0, reads = 0;
  const gate = deferred();
  const view = await render({ getSettings: async (current) => { reads++; return settingsFixture(current); },
    stepUp: async (target, secret, options) => { assert.equal(target, "dev-other"); assert.equal(secret, "transient-reauth"); assert.ok(options.idempotencyKey); grants++; return gate.promise; },
    revokeDevice: async (id, token, options) => { mutations++; assert.equal(token, "step-token"); assert.ok(options.signal); return { device_id: id, state: "revoked", sync_key_revoke_required: true, replayed: false }; } });
  try {
    assert.doesNotMatch(view.container.textContent, /신뢰됨/u);
    await view.act(async () => buttonNamed(view, "장치 철회 dev-other").dispatchEvent(new MinimalEvent("click")));
    const password = inputs(view).at(-1);
    await view.act(async () => props(password).onChange({ target: { value: "transient-reauth" } }));
    await sendNamed(view, "철회 추가 인증", 2);
    assert.equal(grants, 1); assert.equal(mutations, 0); assert.equal(password.value, "");
    await view.act(async () => { gate.resolve({ step_up_authorization: "step-token" }); await Promise.resolve(); });
    assert.equal(mutations, 1); assert.equal(reads, 2); assert.deepEqual(view.redirects, []);
    assert.match(view.container.textContent, /동기화 키 철회.*별도 후속 확인/u);
    assert.doesNotMatch(view.container.textContent, /transient-reauth|step-token/u);
  } finally { await view.cleanup(); }
});

test("S36 C4 trust uncertain retry must reuse the same request key", async () => {
  const keys = [];
  const view = await render({ getSettings: async (current) => settingsFixture(current), trustDevice: async (_id, options) => {
    keys.push(options.idempotencyKey); if (keys.length === 1) throw new Error("ACCOUNT_RESULT_UNKNOWN"); return { state: "trusted" };
  } });
  try {
    await view.act(async () => buttonByText(view.container, "현재 장치 신뢰 등록").dispatchEvent(new MinimalEvent("click")));
    assert.equal(keys.length, 1);
    await view.act(async () => buttonByText(view.container, "현재 장치 신뢰 등록").dispatchEvent(new MinimalEvent("click")));
    assert.equal(keys.length, 2); assert.equal(keys[0], keys[1]);
  } finally { await view.cleanup(); }
});

test("S36 C4 profile/revoke preflight mismatch discards old form and performs mutation0", async () => {
  for (const kind of ["profile", "revoke"]) {
    let current = session, mutations = 0;
    const view = await render({ getSession: async () => current, getSettings: async (value) => settingsFixture(value),
      saveProfile: async () => { mutations++; }, stepUp: async () => { mutations++; } });
    try {
      if (kind === "profile") await typeProfile(view, "이전 사용자 입력");
      else {
        await view.act(async () => buttonNamed(view, "장치 철회 dev-other").dispatchEvent(new MinimalEvent("click")));
        await view.act(async () => props(inputs(view).at(-1)).onChange({ target: { value: "transient-reauth" } }));
      }
      current = { ...session, user_id: "new-profile-user", session_id: "new-profile-session" };
      await sendNamed(view, kind === "profile" ? "표시 이름 저장" : "철회 추가 인증");
      assert.equal(mutations, 0); assert.match(view.container.textContent, /new-profile-user/u);
      assert.ok(inputs(view).every((input) => input.value === "")); assert.doesNotMatch(view.container.textContent, /이전 사용자 입력/u);
    } finally { await view.cleanup(); }
  }
});

test("S36 C4 reauth DOM is synchronously cleared/blocked at pagehide before React flush", async () => {
  const view = await render({ getSettings: async (current) => settingsFixture(current) });
  try {
    await view.act(async () => buttonNamed(view, "장치 철회 dev-other").dispatchEvent(new MinimalEvent("click")));
    const password = inputs(view).at(-1);
    await view.act(async () => props(password).onChange({ target: { value: "transient-reauth" } }));
    const root = findElements(view.container, (node) => node.getAttribute("data-session-validated") === "true")[0];
    await view.act(async () => {
      view.dom.document.dispatchEvent(new MinimalEvent("pagehide"));
      assert.equal(password.value, ""); assert.equal(root.hidden, true); assert.equal(root.inert, true);
    });
    assert.equal(inputs(view).length, 0);
  } finally { await view.cleanup(); }
});

test("S36 C4 list failure renders no guessed list; explicit cursor page appends only returned own rows", async () => {
  let failed = true; const pages = [];
  const view = await render({ getSettings: async (current, options) => {
    pages.push(options.deviceCursor ?? null);
    if (failed) throw new Error("private-profile-value");
    const value = settingsFixture(current);
    value.devices = options.deviceCursor ? { items: [{ device_id: "dev-next", platform: "windows", state: "registered", last_seen_at: session.expires_at, is_current: false }], next_cursor: null }
      : { items: value.devices.items, next_cursor: "opaque-next" };
    return value;
  } });
  try {
    assert.match(view.container.textContent, /프로필·장치 조회 실패/u);
    assert.equal(profileField(view), undefined); assert.doesNotMatch(view.container.textContent, /private-profile-value|dev-other/u);
    failed = false;
    await view.act(async () => buttonByText(view.container, "프로필·장치 다시 조회").dispatchEvent(new MinimalEvent("click")));
    await view.act(async () => buttonByText(view.container, "장치 더 보기").dispatchEvent(new MinimalEvent("click")));
    assert.deepEqual(pages, [null, null, "opaque-next"]);
    assert.match(view.container.textContent, /dev-other/u); assert.match(view.container.textContent, /dev-next/u);
    assert.doesNotMatch(view.container.textContent, /신뢰됨/u);
  } finally { await view.cleanup(); }
});

test("S36 C4 failed reauthentication makes revoke0 and hides private errors; current-session receipt alone redirects", async () => {
  for (const success of [false, true]) {
    let mutations = 0;
    const view = await render({ getSettings: async (current) => settingsFixture(current),
      stepUp: async () => { if (!success) throw new Error("private-secret-value"); return { step_up_authorization: "transient-step-up" }; },
      revokeSession: async (id) => { mutations++; return { session_id: id, state: "revoked", replayed: false }; } });
    try {
      await view.act(async () => buttonNamed(view, `세션 철회 ${session.session_id}`).dispatchEvent(new MinimalEvent("click")));
      await view.act(async () => props(inputs(view).at(-1)).onChange({ target: { value: "transient-reauth" } }));
      await sendNamed(view, "철회 추가 인증");
      assert.equal(mutations, success ? 1 : 0); assert.deepEqual(view.redirects, success ? ["/"] : []);
      assert.doesNotMatch(view.container.textContent, /transient-reauth|transient-step-up|private-secret-value/u);
      if (success) { assert.equal(profileField(view), undefined); assert.equal(inputs(view).length, 0); }
    } finally { await view.cleanup(); }
  }
});

test("S36 C4 late profile success/401 after hiding cannot restore data or navigate", async () => {
  for (const expired of [false, true]) {
    const gate = deferred(); let signal;
    const view = await render({ getSettings: (_current, options) => { signal = options.signal; return gate.promise; } });
    try {
      await view.act(async () => {
        view.dom.document.dispatchEvent(new MinimalEvent("pagehide"));
        if (expired) gate.reject(new Error("AUTHENTICATION_REQUIRED")); else gate.resolve(settingsFixture());
        await Promise.resolve();
      });
      assert.equal(signal.aborted, true); assert.equal(profileField(view), undefined); assert.equal(inputs(view).length, 0);
      assert.deepEqual(view.redirects, []); assert.doesNotMatch(view.container.textContent, /기존 이름|dev-other/u);
    } finally { await view.cleanup(); }
  }
});

test("S36 bound profile: captured A preflight arriving after cookie B rejects and reloads without writing B", async () => {
  const preflight = deferred(); let reads = 0, cookie = session, writes = 0;
  const calls = [];
  const view = await render({ getSession: async () => ++reads === 2 ? preflight.promise : cookie,
    getSettings: async () => settingsFixture(cookie), saveProfile: async (payload, options) => {
      calls.push({ payload, key: options.idempotencyKey, expected: options.expectedSessionId });
      if (options.expectedSessionId !== cookie.session_id) throw new Error("ACCOUNT_SESSION_CHANGED");
      writes++;
      return { display_name: payload.display_name, version: 1, replayed: false };
    } });
  try {
    await typeProfile(view, "이전 화면 입력"); await sendNamed(view, "표시 이름 저장");
    assert.equal(reads, 2); assert.equal(writes, 0);
    cookie = { ...session, user_id: "cookie-b-user", session_id: "cookie-b-session", device_id: "cookie-b-device" };
    await view.act(async () => { preflight.resolve(session); await Promise.resolve(); });
    assert.equal(calls.length, 1); assert.equal(calls[0].expected, session.session_id);
    assert.equal(writes, 0); assert.equal(reads, 3);
    assert.match(view.container.textContent, /cookie-b-user/u); assert.doesNotMatch(profileField(view).value, /이전 화면 입력/u);
  } finally { await view.cleanup(); }
});

test("S36 HTTP crypto: randomUUID 없이 CSPRNG 키로 profile replay와 trust/revoke 중복 보호를 유지한다", async () => {
  const priorCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto"), webCrypto = globalThis.crypto;
  const draws = [], keys = [], saves = [];
  let stored = { display_name: "기존 이름", version: 2 }, mutations = 0;
  const view = await render({ getSettings: async (current) => ({ ...settingsFixture(current), profile: stored }),
    saveProfile: async (payload, options) => {
      saves.push([payload, options.idempotencyKey, options.expectedSessionId]);
      if (saves.length === 1) { keys.push(options.idempotencyKey); throw new Error("ACCOUNT_RESULT_UNKNOWN"); }
      stored = { display_name: payload.display_name, version: 3 }; return { ...stored, replayed: true };
    }, trustDevice: async (_id, options) => { keys.push(options.idempotencyKey); return { state: "trusted" }; },
    stepUp: async (_id, _password, options) => { keys.push(options.idempotencyKey); return { step_up_authorization: "transient-step-up" }; },
    revokeDevice: async (id, _authorization, options) => { keys.push(options.idempotencyKey); mutations++; return { device_id: id, state: "revoked", sync_key_revoke_required: true }; },
    revokeSession: async (id, _authorization, options) => { keys.push(options.idempotencyKey); mutations++; return { session_id: id, state: "revoked" }; },
  });
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: { getRandomValues(bytes) {
    webCrypto.getRandomValues(bytes); draws.push(Array.from(new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength))); return bytes;
  } } });
  try {
    await typeProfile(view, "HTTP 저장 이름"); await sendNamed(view, "표시 이름 저장", 2);
    assert.equal(saves.length, 1); await sendNamed(view, "표시 이름 저장");
    assert.equal(saves.length, 2); assert.deepEqual(saves[0], saves[1]); assert.equal(saves[0][2], session.session_id);
    assert.equal(profileField(view).value, "HTTP 저장 이름");
    await view.act(async () => buttonByText(view.container, "현재 장치 신뢰 등록").dispatchEvent(new MinimalEvent("click")));
    for (const [kind, id] of [["장치", "dev-other"], ["세션", "ses-other"]]) {
      await view.act(async () => buttonNamed(view, `${kind} 철회 ${id}`).dispatchEvent(new MinimalEvent("click")));
      await view.act(async () => props(inputs(view).at(-1)).onChange({ target: { value: "transient-reauth" } }));
      await sendNamed(view, "철회 추가 인증", 2);
    }
    assert.equal(mutations, 2); assert.equal(keys.length, 6); assert.equal(new Set(keys).size, 6);
    assert.equal(draws.length, 6); assert.ok(draws.every((bytes) => bytes.length >= 16));
    for (const key of keys) assert.match(key, /^account-[A-Za-z0-9._:-]{32,120}$/u);
    assert.deepEqual(view.redirects, []); assert.doesNotMatch(view.container.textContent, /TypeError|randomUUID|transient-reauth|transient-step-up/u);
  } finally { await view.cleanup(); Object.defineProperty(globalThis, "crypto", priorCrypto); }
});

test("S36 HTTP crypto: 두 암호 API 불가/예외는 profile·trust·revoke0과 안전한 UI 실패다", async () => {
  const priorCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto"), priorRandom = Math.random, priorError = console.error;
  for (const available of [undefined, {}, { getRandomValues() { throw new Error("private-crypto-value"); } }]) {
    let requests = 0, weak = 0; const errors = [];
    const view = await render({ getSettings: async (current) => settingsFixture(current),
      saveProfile: async () => { requests++; }, trustDevice: async () => { requests++; }, stepUp: async () => { requests++; } });
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: available });
    Math.random = () => { weak++; return 0.25; }; console.error = (...args) => errors.push(args);
    try {
      await typeProfile(view, "저장 불가"); await sendNamed(view, "표시 이름 저장");
      assert.match(view.container.textContent, /보안 난수.*사용할 수 없/u);
      await view.act(async () => buttonByText(view.container, "현재 장치 신뢰 등록").dispatchEvent(new MinimalEvent("click")));
      await view.act(async () => buttonNamed(view, "장치 철회 dev-other").dispatchEvent(new MinimalEvent("click")));
      const password = inputs(view).at(-1);
      await view.act(async () => props(password).onChange({ target: { value: "transient-reauth" } }));
      await sendNamed(view, "철회 추가 인증");
      assert.equal(requests, 0); assert.equal(weak, 0); assert.deepEqual(errors, []); assert.equal(password.value, "");
      assert.match(view.container.textContent, /보안 난수.*사용할 수 없/u);
      assert.doesNotMatch(view.container.textContent, /private-crypto-value|TypeError|randomUUID|transient-reauth/u);
    } finally {
      Math.random = priorRandom; console.error = priorError;
      await view.cleanup(); Object.defineProperty(globalThis, "crypto", priorCrypto);
    }
  }
});
