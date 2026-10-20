import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

const require = createRequire(import.meta.url);
const personal = { tenant_id: "personal", display_name: "개인", kind: "personal", workspace_id: "personal-workspace" };
const organization = { tenant_id: "organization", display_name: "승인 조직", kind: "organization", workspace_id: "org-workspace" };
const session = (tenant = "personal", id = "session-a") => ({ user_id: "member", login_id: "member", tenant_id: tenant,
  workspace_id: tenant === "personal" ? "personal-workspace" : "org-workspace", session_id: id, device_id: "device", is_system_admin: false, password_change_required: false });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

async function render({ getSession = async () => session(), getNotebooks = async () => ({ data: [] }), tenants = () => ({ current_tenant_id: "personal", tenants: [personal] }), switchTenant, createNotebook } = {}) {
  const temp = "D:/tmp/daon-user-s39-j3-20261017-eoul2", root = path.resolve(import.meta.dirname, "../..");
  assert.equal(path.resolve(process.env.TEMP || ""), path.resolve(temp));
  const output = await mkdtemp(path.join(temp, "journey-react-")), dom = installMinimalDom();
  const oldFetch = globalThis.fetch, requests = [], navigations = [];
  window.location = { replace: value => navigations.push(value), assign: value => navigations.push(value) };
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    if (url === "/bff/api/session/tenants") return Response.json({ data: await tenants(), meta: { trace_id: "j3-trace" } });
    if (url === "/bff/api/session/tenant") return switchTenant(JSON.parse(init.body).tenant_id);
    if (url === "/bff/api/workspaces/personal-workspace/notebooks" && init.method === "POST") return createNotebook(init);
    throw new Error("UNEXPECTED_J3_NETWORK");
  };
  const { createElement, act } = await import("react"), { createRoot } = await import("react-dom/client");
  let reactRoot;
  const cleanup = async () => { if (reactRoot) await act(async () => reactRoot.unmount()); globalThis.fetch = oldFetch; dom.restore(); await rm(output, { recursive: true, force: true }); };
  try {
    const { build } = await import("vite");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/components/notebook-home-workspace.jsx"), formats: ["es"], fileName: "journey" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: id => pathToFileURL(require.resolve(id)).href } } } });
    const built = (await readdir(output)).find(file => /\.m?js$/u.test(file));
    const module = await import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(module.NotebookHomeWorkspace, { getSession, getNotebooks, navigate: value => navigations.push(value) })); });
    return { container, dom, act, requests, navigations, cleanup };
  } catch (error) { await cleanup(); throw error; }
}
async function click(view, text, twice = false) {
  const button = buttonByText(view.container, text); assert.ok(button, text);
  await view.act(async () => { button.dispatchEvent(new MinimalEvent("click")); if (twice) button.dispatchEvent(new MinimalEvent("click")); });
}

test("J3 중지 조직 Notebook 거부와 현재조직 목록제외에도 개인 복귀 메뉴를 유지한다", async () => {
  const view = await render({ getSession: async () => session("organization"),
    getNotebooks: async () => { throw new Error("ORGANIZATION_MEMBERSHIP_SUSPENDED"); },
    tenants: () => ({ current_tenant_id: "organization", tenants: [personal] }) });
  try {
    assert.match(view.container.textContent, /Notebook을 불러오지 못했습니다/u);
    await click(view, "⚙ 설정"); assert.ok(buttonByText(view.container, "개인 · 개인"));
    assert.equal(view.navigations.length, 0);
  } finally { await view.cleanup(); }
});

test("J3 개인전용에서 명시 새로고침으로 승인 조직을 표시하고 자동 전환하지 않는다", async () => {
  let approved = false;
  const view = await render({ tenants: () => ({ current_tenant_id: "personal", tenants: approved ? [personal, organization] : [personal] }) });
  try {
    await click(view, "⚙ 설정"); assert.equal(buttonByText(view.container, "조직 · 승인 조직"), undefined);
    approved = true; await click(view, "작업 범위 새로고침");
    assert.ok(buttonByText(view.container, "조직 · 승인 조직"));
    assert.equal(view.requests.filter(row => row.init.method === "POST").length, 0);
    assert.equal(view.requests.filter(row => row.init.method === "GET").length, 2);
  } finally { await view.cleanup(); }
});

test("J3 pagehide 직후 동기 conceal하고 늦은 Notebook 성공으로 이전자료를 복원하지 않는다", async () => {
  const late = deferred(); const view = await render({ getNotebooks: () => late.promise });
  try {
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("pagehide")); });
    const root = view.container.firstChild;
    assert.equal(root.getAttribute("hidden"), "");
    await view.act(async () => { late.resolve({ data: [{ notebook_id: "old", title: "이전 비공개 자료", status: "active" }] }); });
    assert.equal(root.getAttribute("hidden"), "");
    assert.doesNotMatch(view.container.textContent, /이전 비공개 자료/u);
  } finally { await view.cleanup(); }
});

test("J3 전환 응답유실은 본인 세션 확인 전 다른 조직 요청과 동기 중복을 차단한다", async () => {
  const verify = deferred(); let reads = 0, posted = 0;
  const view = await render({ getSession: async () => { if (++reads > 2) return verify.promise; return session(); },
    tenants: () => ({ current_tenant_id: "personal", tenants: [personal, organization] }),
    switchTenant: async () => { posted++; throw new TypeError("합성 응답 유실"); } });
  try {
    await click(view, "⚙ 설정"); await click(view, "조직 · 승인 조직", true);
    assert.equal(posted, 1); assert.equal(reads, 3); assert.equal(view.navigations.length, 0);
    await view.act(async () => { verify.reject(new TypeError("합성 세션 조회 실패")); });
    await click(view, "⚙ 설정");
    assert.match(view.container.textContent, /전환 결과.*확인/u);
    assert.equal(posted, 1);
  } finally { verify.resolve(session()); await view.cleanup(); }
});

test("J3 scope 조회 실패는 이전 Notebook을 남기지 않으며 실패에서 권한을 추론하지 않는다", async () => {
  let fail = false;
  const view = await render({ getNotebooks: async () => ({ data: [{ notebook_id: "old", title: "이전 조직 자료", status: "active" }] }),
    tenants: async () => { if (fail) throw new Error("합성 목록 실패"); return { current_tenant_id: "personal", tenants: [personal] }; } });
  try {
    assert.match(view.container.textContent, /이전 조직 자료/u); fail = true;
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("popstate")); });
    assert.doesNotMatch(view.container.textContent, /이전 조직 자료/u);
  } finally { await view.cleanup(); }
});

test("J3 전송 중 같은 세션 재조회는 잠금을 해제하지 않고 늦은 응답유실을 표시한다", async () => {
  const sent = deferred(); let posted = 0;
  const view = await render({ tenants: () => ({ current_tenant_id: "personal", tenants: [personal, organization] }),
    switchTenant: async () => { posted++; return sent.promise; } });
  try {
    await click(view, "⚙ 설정"); await click(view, "조직 · 승인 조직");
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("popstate")); });
    await click(view, "⚙ 설정");
    assert.equal(buttonByText(view.container, "조직 · 승인 조직").disabled, true);
    await view.act(async () => { sent.reject(new TypeError("합성 늦은 응답 유실")); });
    assert.match(view.container.textContent, /전환 결과.*확인/u);
    assert.equal(buttonByText(view.container, "조직 · 승인 조직").disabled, true);
    assert.equal(posted, 1); assert.equal(view.navigations.length, 0);
  } finally { sent.resolve(Response.json({})); await view.cleanup(); }
});

test("J3 전환 성공은 이전 Notebook을 폐기하고 새 세션 scope 재진입으로 보낸다", async () => {
  const view = await render({ getNotebooks: async () => ({ data: [{ notebook_id: "old", title: "이전 개인 자료", status: "active" }] }),
    tenants: () => ({ current_tenant_id: "personal", tenants: [personal, organization] }),
    switchTenant: async target => Response.json({ data: { user_id: "member", tenant_id: target, workspace_id: "org-workspace", session_id: "session-new" }, meta: { trace_id: "trace" } }) });
  try {
    await click(view, "⚙ 설정"); await click(view, "조직 · 승인 조직");
    assert.deepEqual(view.navigations, ["/notebooks"]);
    assert.doesNotMatch(view.container.textContent, /이전 개인 자료/u);
    assert.equal(view.requests.filter(row => row.init.method === "POST").length, 1);
  } finally { await view.cleanup(); }
});

test("J3 다른 탭 세션 변경을 전송 직전 확인하면 이전 선택을 전송하지 않는다", async () => {
  let reads = 0;
  const view = await render({ getSession: async () => ++reads === 1 ? session() : { ...session("organization", "session-b"), user_id: "member-b" },
    tenants: () => ({ current_tenant_id: reads === 1 ? "personal" : "organization", tenants: [personal, organization] }) });
  try {
    await click(view, "⚙ 설정"); await click(view, "조직 · 승인 조직");
    assert.equal(view.requests.filter(row => row.init.method === "POST").length, 0);
    assert.equal(view.navigations.length, 0);
  } finally { await view.cleanup(); }
});

test("J3 focus 재검증의 새 세션보다 늦은 이전 Notebook 응답은 폐기한다", async () => {
  const late = deferred(); let reads = 0, notebooks = 0;
  const view = await render({ getSession: async () => ++reads === 1 ? session() : session("organization", "session-b"),
    getNotebooks: async () => ++notebooks === 1 ? late.promise : { data: [] },
    tenants: () => ({ current_tenant_id: reads === 1 ? "personal" : "organization", tenants: [personal, organization] }) });
  try {
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
    await view.act(async () => { late.resolve({ data: [{ notebook_id: "old", title: "이전 세션 자료", status: "active" }] }); });
    assert.doesNotMatch(view.container.textContent, /이전 세션 자료/u);
    await click(view, "⚙ 설정"); assert.match(view.container.textContent, /현재 범위: 승인 조직/u);
  } finally { late.resolve({ data: [] }); await view.cleanup(); }
});

test("J3 전송 중 확인된 실제 다른 세션에는 이전 미확정 결과나 잠금을 노출하지 않는다", async () => {
  const sent = deferred(); let changed = false;
  const view = await render({ getSession: async () => changed ? { ...session("organization", "session-b"), user_id: "member-b", login_id: "member-b" } : session(),
    tenants: () => ({ current_tenant_id: changed ? "organization" : "personal", tenants: [personal, organization] }), switchTenant: () => sent.promise });
  try {
    await click(view, "⚙ 설정"); await click(view, "조직 · 승인 조직"); changed = true;
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
    await view.act(async () => { sent.reject(new TypeError("합성 이전 세션 응답 유실")); });
    await click(view, "⚙ 설정");
    assert.match(view.container.textContent, /member-b/u);
    assert.doesNotMatch(view.container.textContent, /전환 결과.*확인/u);
    assert.equal(buttonByText(view.container, "개인 · 개인").disabled, false);
  } finally { sent.resolve(Response.json({})); await view.cleanup(); }
});

test("J3 같은 세션 재조회 뒤 늦은 전환 성공도 이전 범위 본문을 즉시 폐기한다", async () => {
  const sent = deferred();
  const view = await render({ tenants: () => ({ current_tenant_id: "personal", tenants: [personal, organization] }),
    getNotebooks: async () => ({ data: [{ notebook_id: "old", title: "이전 범위 비공개자료", status: "active" }] }), switchTenant: () => sent.promise });
  try {
    await click(view, "⚙ 설정"); await click(view, "조직 · 승인 조직");
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("popstate")); });
    assert.match(view.container.textContent, /이전 범위 비공개자료/u);
    await view.act(async () => { sent.resolve(Response.json({ data: { user_id: "member", tenant_id: "organization", workspace_id: "org-workspace", session_id: "session-new" }, meta: { trace_id: "trace" } })); });
    assert.doesNotMatch(view.container.textContent, /이전 범위 비공개자료/u);
  } finally { sent.resolve(Response.json({})); await view.cleanup(); }
});

const createdNotebook = () => Response.json({ data: { notebook_id: "created-a", title: "A 생성 결과", source_count: 0, output_count: 0,
  updated_at: "2026-10-17T00:00:00Z", status: "empty", etag: '"notebook:1"' },
  meta: { trace_id: "j3-create", workspace_id: "personal-workspace", replayed: false } }, { status: 201, headers: { ETag: '"notebook:1"' } });
async function create(view) {
  await click(view, "＋ 새 Notebook");
  const input = findElements(view.container, node => node.tagName === "INPUT" && node.hasAttribute("required"))[0];
  assert.ok(input);
  const props = input[Object.keys(input).find(key => key.startsWith("__reactProps"))];
  await view.act(async () => { props.onChange({ target: { value: "합성 생성 요청" } }); });
  const form = findElements(view.container, node => node.tagName === "FORM")[0]; assert.ok(form);
  await view.act(async () => { form.dispatchEvent(new MinimalEvent("submit")); });
}
for (const interruption of ["same-session-focus", "other-account", "other-scope", "cookie-only"]) {
  test(`J3 생성 대기 ${interruption} 이후 A 성공은 목록·자동열기 모두 폐기한다`, async () => {
    const sent = deferred(); let changed = false;
    const current = () => changed && interruption !== "same-session-focus"
      ? { ...session("organization", "session-b"), user_id: interruption === "other-scope" ? "member" : "member-b", login_id: "새 사용자" } : session();
    const view = await render({ getSession: async () => current(), createNotebook: () => sent.promise,
      tenants: () => ({ current_tenant_id: current().tenant_id, tenants: [personal, organization] }) });
    try {
      await create(view); changed = true;
      if (interruption !== "cookie-only") await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
      await view.act(async () => { sent.resolve(createdNotebook()); });
      assert.doesNotMatch(view.container.textContent, /A 생성 결과/u);
      assert.deepEqual(view.navigations, []);
      assert.equal(view.requests.filter(row => row.init.method === "POST").length, 1);
    } finally { sent.resolve(createdNotebook()); await view.cleanup(); }
  });
}
test("J3 정상 생성은 검증된 동일 세션에서 목록 반영과 자동열기를 유지한다", async () => {
  const view = await render({ createNotebook: async () => createdNotebook() });
  try {
    await create(view);
    assert.match(view.container.textContent, /A 생성 결과/u);
    assert.deepEqual(view.navigations, ["/notebooks/created-a"]);
  } finally { await view.cleanup(); }
});
