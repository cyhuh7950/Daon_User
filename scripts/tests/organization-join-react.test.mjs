import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

const require = createRequire(import.meta.url);
const props = node => node?.[Object.keys(node).find(key => key.startsWith("__reactProps"))];
const session = { user_id: "member", session_id: "session-a", device_id: "device", tenant_id: "personal", workspace_id: "personal-workspace" };
const row = (state = "pending", id = "join-a") => ({ request_id: id, tenant_id: "tenant-a", user_id: "member", invitation_id: "invitation-a", state, decision_reason: state === "rejected" ? "합성 거부" : null, decided_by: state === "pending" ? null : "admin", version: state === "pending" ? 1 : 2, created_at: "2026-10-17T00:00:00Z", updated_at: "2026-10-17T00:00:00Z" });
function fixture(overrides = {}, rows = []) {
  const state = { rows, writes: [], reads: 0 };
  const api = { getOrganizationSession: async () => session,
    listOwnJoinRequests: async () => { state.reads++; return state.rows; },
    submitJoinRequest: async (...args) => { state.writes.push(args); state.rows = [row("pending", "new-join"), ...state.rows]; return state.rows[0]; }, ...overrides };
  return { api, state };
}
async function render(api) {
  const root = path.resolve(import.meta.dirname, "../.."), temp = "D:/tmp/daon-user-s39-j2-20261017-eoul2";
  assert.equal(path.resolve(process.env.TEMP || ""), path.resolve(temp));
  const output = await mkdtemp(path.join(temp, "join-react-")), dom = installMinimalDom();
  const originalFetch = globalThis.fetch, storage = [];
  const originalStorage = [globalThis.localStorage, globalThis.sessionStorage, window.localStorage, window.sessionStorage];
  const guardedStorage = { setItem(...args) { storage.push(args); }, getItem() { return null; }, removeItem() {} };
  globalThis.localStorage = globalThis.sessionStorage = window.localStorage = window.sessionStorage = guardedStorage;
  globalThis.fetch = async () => { throw new Error("UNEXPECTED_NETWORK_IN_DIRECT_TEST"); };
  let reactRoot;
  const { createElement, act } = await import("react"), { createRoot } = await import("react-dom/client");
  const cleanup = async () => {
    if (reactRoot) await act(async () => reactRoot.unmount());
    [globalThis.localStorage, globalThis.sessionStorage, window.localStorage, window.sessionStorage] = originalStorage;
    globalThis.fetch = originalFetch; dom.restore(); await rm(output, { recursive: true, force: true });
  };
  try {
    const { build } = await import("vite");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/components/organization-join-form.jsx"), formats: ["es"], fileName: "organization-join" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: id => pathToFileURL(require.resolve(id)).href } } } });
    const built = (await readdir(output)).find(file => /\.m?js$/u.test(file));
    const module = await import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(module.OrganizationJoinForm, { api })); await Promise.resolve(); });
    return { container, dom, act, storage, cleanup };
  } catch (error) { await cleanup(); throw error; }
}
async function input(view, value) {
  const node = findElements(view.container, node => node.tagName === "INPUT")[0]; assert.ok(node);
  await view.act(async () => { props(node).onChange({ target: { value } }); }); return node;
}
async function submit(view, twice = false) {
  const form = findElements(view.container, node => node.tagName === "FORM")[0]; assert.ok(form);
  await view.act(async () => { form.dispatchEvent(new MinimalEvent("submit")); if (twice) form.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
}
async function click(view, text) {
  const button = buttonByText(view.container, text); assert.ok(button, text);
  await view.act(async () => { button.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
}

test("J2 본인 목록은 재진입·수동 조회로 pending 승인 거부를 구별하고 자동 신청하지 않는다", async () => {
  const { api, state } = fixture({}, [row()]); let view = await render(api);
  try {
    assert.match(view.container.textContent, /승인 대기/u); assert.equal(state.reads, 1); assert.equal(state.writes.length, 0);
    state.rows = [row("approved")]; await click(view, "상태 새로고침"); assert.match(view.container.textContent, /승인됨/u);
    await view.cleanup(); view = await render(api); assert.match(view.container.textContent, /승인됨/u);
    state.rows = [row("rejected")]; await click(view, "상태 새로고침"); assert.match(view.container.textContent, /거부됨/u);
    assert.equal(state.writes.length, 0);
    await input(view, "j2-synthetic-new"); await submit(view); assert.equal(state.writes.length, 1);
    assert.match(view.container.textContent, /승인 대기/u); assert.equal(view.storage.length, 0);
  } finally { await view.cleanup(); }
});

test("J2 정상 제출은 동기 중복을 막고 원 세션·키를 고정하며 입력을 즉시 비운다", async () => {
  let release; const pending = new Promise(resolve => { release = resolve; });
  const writes = [], { api, state } = fixture({ submitJoinRequest: async (...args) => { writes.push(args); return pending; } });
  const view = await render(api);
  try {
    const node = await input(view, "j2-synthetic-secret"); await submit(view, true);
    assert.equal(writes.length, 1); assert.equal(writes[0][0], "j2-synthetic-secret"); assert.equal(writes[0][1].expectedSessionId, "session-a");
    assert.match(writes[0][1].idempotencyKey, /^org-[a-z0-9-]+$/u); assert.equal(node.value, "");
    await view.act(async () => { state.rows = [row()]; release(row()); await pending; });
    assert.match(view.container.textContent, /승인 대기/u); assert.equal(view.storage.length, 0);
  } finally { release(row()); await view.cleanup(); }
});

for (const interruption of ["network", "401", "403", "503", "pagehide"]) {
  test(`J2 미확정 재확인 ${interruption}는 원 코드·키를 보존하고 새 신청을 차단한다`, async () => {
    let reads = 0, release; const pending = new Promise(resolve => { release = resolve; }); const writes = [];
    const { api } = fixture({ getOrganizationSession: async () => {
      if (++reads !== 3) return session;
      if (interruption === "pagehide") return pending;
      const error = new TypeError("합성 조회 실패"); if (interruption !== "network") error.status = Number(interruption); throw error;
    }, submitJoinRequest: async (...args) => { writes.push(args); if (writes.length === 1) throw new TypeError("응답 유실"); return row(); } });
    const view = await render(api);
    try {
      await input(view, "j2-synthetic-frozen"); await submit(view); assert.match(view.container.textContent, /결과 미확정/u);
      await click(view, "동일 신청 결과 확인");
      if (interruption === "pagehide") {
        await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("pagehide")); view.dom.document.dispatchEvent(new MinimalEvent("pageshow")); await Promise.resolve(); });
        await view.act(async () => { release(session); await pending; });
      } else if (interruption === "401" || interruption === "403") await click(view, "상태 새로고침");
      assert.match(view.container.textContent, /결과 미확정/u);
      await input(view, "must-not-submit"); await submit(view); assert.equal(writes.length, 1);
      await click(view, "동일 신청 결과 확인"); assert.equal(writes.length, 2);
      assert.equal(writes[1][0], writes[0][0]); assert.equal(writes[1][1].idempotencyKey, writes[0][1].idempotencyKey);
      assert.equal(view.storage.length, 0);
    } finally { release(session); await view.cleanup(); }
  });
}

test("J2 pagehide는 flush 전 원문·본문을 숨기고 늦은 본인 목록으로 복원하지 않는다", async () => {
  let release, reads = 0; const late = new Promise(resolve => { release = resolve; });
  const { api } = fixture({ listOwnJoinRequests: async () => ++reads === 1 ? late : [row("rejected", "new-row")] });
  const view = await render(api);
  try {
    const main = findElements(view.container, node => node.tagName === "MAIN")[0];
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("pagehide")); assert.equal(main.hidden, true); assert.equal(main.inert, true); view.dom.document.dispatchEvent(new MinimalEvent("pageshow")); await Promise.resolve(); });
    await view.act(async () => { release([row("approved", "late-secret-row")]); await late; });
    assert.doesNotMatch(view.container.textContent, /late-secret-row/u); assert.match(view.container.textContent, /거부됨/u);
  } finally { release([]); await view.cleanup(); }
});

test("J2 쓰기 전 세션 교체는 mutation0이며 타인 목록도 표시하지 않는다", async () => {
  let reads = 0, writes = 0;
  const other = { ...session, user_id: "other", session_id: "other-session" };
  const { api } = fixture({ getOrganizationSession: async () => ++reads === 1 ? session : other,
    listOwnJoinRequests: async () => [], submitJoinRequest: async () => { writes++; return row(); } });
  const view = await render(api);
  try {
    await input(view, "j2-synthetic-secret"); await submit(view); assert.equal(writes, 0);
    assert.doesNotMatch(view.container.textContent, /j2-synthetic-secret/u);
  } finally { await view.cleanup(); }
  const foreign = await render(fixture({ listOwnJoinRequests: async () => [{ ...row(), user_id: "other", request_id: "foreign-secret-row" }] }).api);
  try { assert.doesNotMatch(foreign.container.textContent, /foreign-secret-row/u); assert.match(foreign.container.textContent, /조회.*실패|조회.*못/u); }
  finally { await foreign.cleanup(); }
});

for (const status of [401, 403, 503]) {
  test(`J2 재확인 전송 ${status}도 앞선 미확정의 원 키를 해제하지 않는다`, async () => {
    const writes = [];
    const { api } = fixture({ submitJoinRequest: async (...args) => {
      writes.push(args);
      if (writes.length === 1) throw new TypeError("최초 응답 유실");
      if (writes.length === 2) { const error = new Error("ORGANIZATION_WORKFLOW_UNAVAILABLE"); error.status = status; throw error; }
      return row();
    } });
    const view = await render(api);
    try {
      await input(view, "j2-synthetic-frozen"); await submit(view); await click(view, "동일 신청 결과 확인");
      if (status === 401 || status === 403) await click(view, "상태 새로고침");
      assert.match(view.container.textContent, /결과 미확정/u);
      await input(view, "must-not-submit"); await submit(view); assert.equal(writes.length, 2);
      await click(view, "동일 신청 결과 확인"); assert.equal(writes.length, 3);
      assert.ok(writes.every(([code, options]) => code === "j2-synthetic-frozen" && options.idempotencyKey === writes[0][1].idempotencyKey));
    } finally { await view.cleanup(); }
  });
}

test("J2 같은 세션 재조회 뒤 늦은 전송 실패도 원 요청을 보존한다", async () => {
  let reject; const pending = new Promise((_resolve, fail) => { reject = fail; }); const writes = [];
  const { api } = fixture({ submitJoinRequest: async (...args) => { writes.push(args); return writes.length === 1 ? pending : row(); } });
  const view = await render(api);
  try {
    await input(view, "j2-synthetic-frozen"); await submit(view);
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("popstate")); await Promise.resolve(); });
    await view.act(async () => { reject(new TypeError("늦은 응답 유실")); await pending.catch(() => {}); });
    assert.match(view.container.textContent, /결과 미확정/u);
    await input(view, "must-not-submit"); await submit(view); assert.equal(writes.length, 1);
    await click(view, "동일 신청 결과 확인"); assert.equal(writes.length, 2);
    assert.equal(writes[1][0], writes[0][0]); assert.equal(writes[1][1].idempotencyKey, writes[0][1].idempotencyKey);
  } finally { await view.cleanup(); }
});

test("J2 미확정 뒤 실제 다른 세션은 이전 코드를 폐기하고 첫 전송 전 실패는 잠그지 않는다", async () => {
  let reads = 0; const changed = { ...session, session_id: "session-b" }, writes = [];
  const { api } = fixture({ getOrganizationSession: async () => ++reads >= 3 ? changed : session,
    submitJoinRequest: async (...args) => { writes.push(args); if (writes.length === 1) throw new TypeError("응답 유실"); return row(); } });
  const view = await render(api);
  try {
    await input(view, "j2-synthetic-old"); await submit(view); await click(view, "동일 신청 결과 확인");
    assert.doesNotMatch(view.container.textContent, /결과 미확정|j2-synthetic-old/u); assert.equal(writes.length, 1);
    await input(view, "j2-synthetic-new"); await submit(view);
    assert.equal(writes.length, 2); assert.equal(writes[1][0], "j2-synthetic-new");
    assert.equal(writes[1][1].expectedSessionId, "session-b"); assert.notEqual(writes[1][1].idempotencyKey, writes[0][1].idempotencyKey);
  } finally { await view.cleanup(); }
  let checks = 0, calls = 0;
  const before = await render(fixture({ getOrganizationSession: async () => { if (++checks === 2) throw new TypeError("아직 미전송"); return session; }, submitJoinRequest: async () => { calls++; return row(); } }).api);
  try {
    await input(before, "j2-synthetic-first"); await submit(before); assert.equal(calls, 0); assert.doesNotMatch(before.container.textContent, /결과 미확정/u);
    await input(before, "j2-synthetic-next"); await submit(before); assert.equal(calls, 1);
  } finally { await before.cleanup(); }
});
