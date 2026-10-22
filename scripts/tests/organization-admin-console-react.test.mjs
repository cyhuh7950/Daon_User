import assert from "node:assert/strict";
import { lstat, realpath, mkdtemp, readdir, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

const require = createRequire(import.meta.url);
const props = (node) => node?.[Object.keys(node).find(key => key.startsWith("__reactProps"))];
const session = { user_id: "system-admin", tenant_id: "internal-admin", session_id: "s39-session", device_id: "device", workspace_id: null, is_system_admin: true };
const now = "2026-10-17T00:00:00+00:00";
function fixture(overrides = {}) {
  const calls = [];
  const api = {
    getOrganizationSession: async () => { calls.push(["session"]); return session; },
    listManagedOrganizations: async () => { calls.push(["tenants"]); return [{ tenant_id: "tenant-a", display_name: "조직 A" }, { tenant_id: "tenant-b", display_name: "조직 B" }]; },
    listManagedWorkspaces: async tenant => { calls.push(["workspaces", tenant]); return [{ workspace_id: `workspace-${tenant}`, display_name: `작업공간 ${tenant}` }]; },
    listJoinRequests: async tenant => { calls.push(["joins", tenant]); return [{ request_id: "request-a", tenant_id: tenant, user_id: `신청자-${tenant}`, invitation_id: "invitation-a", state: "pending", decision_reason: null, decided_by: null, version: 1, created_at: now, updated_at: now }]; },
    listMembers: async tenant => { calls.push(["members", tenant]); return [{ tenant_id: tenant, user_id: "linked-user", role: "member", state: "active", version: 1, updated_at: now, join_workspace_id: `workspace-${tenant}`, access_role: "viewer", access_state: "active", access_version: 1 },
      { tenant_id: tenant, user_id: "legacy-user", role: "organization_admin", state: "active", version: 1, updated_at: now, join_workspace_id: null, access_role: null, access_state: null, access_version: null }]; },
    listInvitations: async tenant => { calls.push(["invitations", tenant]); return [{ invitation_id: "invitation-a", tenant_id: tenant, workspace_id: `workspace-${tenant}`, created_by: "admin", expires_at: "2099-01-01T00:00:00+00:00", max_uses: 1, used_count: 0, state: "active", version: 2, created_at: now, updated_at: now }]; },
    ...Object.fromEntries(["decideJoinRequest", "changeMemberRole", "changeMemberState", "createInvitation", "revokeInvitation"].map(name => [name, async (...args) => { calls.push([name, ...args]); return {}; }])),
    ...overrides,
  };
  return { api, calls };
}
async function render(api, mode = "organization") {
  const root = path.resolve(import.meta.dirname, "../..");
  const temp = root;
  assert.ok(path.isAbsolute(temp));
  assert.equal(await realpath(temp), temp, "소유 root의 링크 우회를 거부한다");
  const output = await mkdtemp(path.join(temp, ".r4-react-"));
  const dom = installMinimalDom();
  window.location = { search: "", replace() {} };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("UNEXPECTED_NETWORK_IN_DIRECT_TEST"); };
  let reactRoot;
  const { createElement, act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const cleanup = async () => { if (reactRoot) await act(async () => reactRoot.unmount()); globalThis.fetch = originalFetch; dom.restore(); assert.equal(path.dirname(output), temp); assert.equal(await realpath(output), output); assert.equal((await lstat(output)).isSymbolicLink(), false); await rm(output, { recursive: true, force: true }); };
  try {
    const { build } = await import("vite");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/components/organization-admin-console.jsx"), formats: ["es"], fileName: "organization-console" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: id => pathToFileURL(require.resolve(id)).href } } } });
    const built = (await readdir(output)).find(file => /\.m?js$/u.test(file));
    const module = await import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(module.default, { api, mode })); await Promise.resolve(); });
    return { container, dom, act, cleanup };
  } catch (error) { await cleanup(); throw error; }
}
async function select(view, label, value) {
  const node = findElements(view.container, node => node.tagName === "SELECT" && props(node)?.["aria-label"] === label)[0];
  assert.ok(node, label);
  await view.act(async () => { props(node).onChange({ target: { value } }); await Promise.resolve(); });
}
async function click(view, label) {
  const node = buttonByText(view.container, label); assert.ok(node, label);
  await view.act(async () => { node.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
}
async function input(view, label, value) {
  const node = findElements(view.container, node => node.tagName === "INPUT" && props(node)?.["aria-label"] === label)[0];
  assert.ok(node, label);
  await view.act(async () => { props(node).onChange({ target: { value } }); });
}

test("L3 admin은 내부 tenant 자동진입 없이 조직 선택 전 정확히 한 번만 조회한다", async () => {
  const { api, calls } = fixture(); const view = await render(api);
  try {
    assert.deepEqual(calls, [["session"], ["tenants"]]);
    assert.match(view.container.textContent, /관리할 조직을 선택하세요/u);
    await select(view, "관리 조직", "tenant-a");
    assert.match(view.container.textContent, /신청자-tenant-a/u);
    assert.match(view.container.textContent, /기존 기록—Workspace 미연결/u);
    assert.match(view.container.textContent, /조회자/u);
    assert.equal(calls.filter(([name]) => name === "tenants").length, 1);
    assert.equal(calls.filter(([name]) => name === "joins").length, 1);
  } finally { await view.cleanup(); }
});

test("L3 일반 관리자는 본인 scope만 읽고 전체 조직 목록을 호출하지 않는다", async () => {
  const { api, calls } = fixture({ getOrganizationSession: async () => ({ ...session, user_id: "org-admin", tenant_id: "tenant-a", is_system_admin: false }) });
  const view = await render(api);
  try { assert.match(view.container.textContent, /신청자-tenant-a/u); assert.equal(calls.some(([name]) => name === "tenants"), false); }
  finally { await view.cleanup(); }
});

test("L3 조직 변경 뒤 늦은 이전 성공은 새 조직 화면을 복원하지 않는다", async () => {
  let release;
  const old = new Promise(resolve => { release = resolve; });
  const { api } = fixture({ listJoinRequests: tenant => tenant === "tenant-a" ? old : Promise.resolve([]) });
  const view = await render(api);
  try {
    await select(view, "관리 조직", "tenant-a");
    await select(view, "관리 조직", "tenant-b");
    await view.act(async () => { release([{ request_id: "old", tenant_id: "tenant-a", user_id: "이전 조직 비밀", state: "pending", version: 1 }]); await old; });
    assert.doesNotMatch(view.container.textContent, /이전 조직 비밀/u);
    assert.match(view.container.textContent, /처리할 가입 신청이 없습니다/u);
  } finally { await view.cleanup(); }
});

test("L3 응답 유실은 중복·재진입 새 요청을 막고 명시 동일 요청만 확인한다", async () => {
  let reject; const pending = new Promise((_resolve, fail) => { reject = fail; }); const mutations = [];
  const { api } = fixture({ decideJoinRequest: async (...args) => { mutations.push(args); if (mutations.length === 1) return pending; return {}; } });
  const view = await render(api);
  try {
    await select(view, "관리 조직", "tenant-a");
    await click(view, "승인"); await click(view, "승인");
    assert.equal(mutations.length, 1);
    await view.act(async () => { reject(new TypeError("연결 유실")); await pending.catch(() => {}); });
    assert.match(view.container.textContent, /결과 미확정/u);
    await select(view, "관리 조직", "tenant-b"); await select(view, "관리 조직", "tenant-a");
    await click(view, "승인"); assert.equal(mutations.length, 1);
    await click(view, "동일 요청 결과 확인"); assert.equal(mutations.length, 2);
    assert.equal(mutations[0][2].idempotencyKey, mutations[1][2].idempotencyKey);
    assert.equal(mutations[0][2].expectedSessionId, "s39-session");
    assert.deepEqual(mutations[0][1], mutations[1][1]);
  } finally { await view.cleanup(); }
});

test("L3 pagehide는 flush 전에 본문과 미제출 코드를 차단하고 복귀 재검증 동안 이전 조직을 노출하지 않는다", async () => {
  let release, count = 0;
  const checking = new Promise(resolve => { release = resolve; });
  const { api } = fixture({ getOrganizationSession: async () => ++count === 1 ? session : checking });
  const view = await render(api);
  try {
    await select(view, "관리 조직", "tenant-a");
    const input = findElements(view.container, node => node.tagName === "INPUT" && props(node)?.["aria-label"] === "초대코드")[0];
    await view.act(async () => { props(input).onChange({ target: { value: "미제출 합성 코드" } }); });
    input.value = "미제출 합성 코드";
    const main = findElements(view.container, node => node.tagName === "MAIN")[0];
    await view.act(async () => {
      view.dom.document.dispatchEvent(new MinimalEvent("pagehide"));
      assert.equal(main.hidden, true); assert.equal(main.inert, true); assert.equal(input.value, "");
    });
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("pageshow")); await Promise.resolve(); });
    assert.doesNotMatch(view.container.textContent, /조직 A|조직 B|신청자-tenant-a|legacy-user/u);
    await view.act(async () => { release({ ...session, user_id: "other-admin", session_id: "other-session" }); await checking; });
    assert.doesNotMatch(view.container.textContent, /신청자-tenant-a|legacy-user|미제출 합성 코드/u);
  } finally { release(session); await view.cleanup(); }
});

test("L3 403과 늦은 성공은 거부된 본문을 복원하지 않는다", async () => {
  let release; const late = new Promise(resolve => { release = resolve; });
  const { api } = fixture({ listJoinRequests: () => late, listMembers: async () => { const error = new Error("DENIED"); error.status = 403; throw error; } });
  const view = await render(api);
  try {
    await select(view, "관리 조직", "tenant-a");
    assert.match(view.container.textContent, /관리할 권한이 없습니다/u);
    assert.doesNotMatch(view.container.textContent, /조직 A|조직 B/u);
    await view.act(async () => { release([{ tenant_id: "tenant-a", user_id: "금지된 이전 사용자" }]); await late; });
    assert.doesNotMatch(view.container.textContent, /금지된 이전 사용자|초대코드/u);
  } finally { await view.cleanup(); }
});

test("L3 같은 세션 popstate 읽기와 겹친 늦은 전송 실패도 원 요청을 보존한다", async () => {
  let reject; const pending = new Promise((_resolve, fail) => { reject = fail; }); let writes = 0;
  const { api } = fixture({ decideJoinRequest: async () => { writes++; return pending; } });
  const view = await render(api);
  try {
    await select(view, "관리 조직", "tenant-a"); await click(view, "승인");
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("popstate")); await Promise.resolve(); });
    await view.act(async () => { reject(new TypeError("응답 유실")); await pending.catch(() => {}); });
    assert.match(view.container.textContent, /결과 미확정/u);
    await click(view, "승인"); assert.equal(writes, 1);
  } finally { await view.cleanup(); }
});

test("L3 쓰기 직전 다른 세션이면 mutation 없이 이전 입력을 폐기한다", async () => {
  let reads = 0, writes = 0;
  const { api } = fixture({ getOrganizationSession: async () => ++reads === 1 ? session : { ...session, user_id: "other-admin", session_id: "other-session" }, decideJoinRequest: async () => { writes++; } });
  const view = await render(api);
  try { await select(view, "관리 조직", "tenant-a"); await click(view, "승인"); assert.equal(writes, 0); assert.doesNotMatch(view.container.textContent, /신청자-tenant-a|legacy-user/u); }
  finally { await view.cleanup(); }
});

test("L3 flag OFF 거부 뒤에는 조직 전환으로 쓰기 차단을 우회하지 않는다", async () => {
  let writes = 0;
  const { api } = fixture({ decideJoinRequest: async () => { writes++; const error = new Error("ORGANIZATION_WORKFLOW_UNAVAILABLE"); error.status = 503; throw error; } });
  const view = await render(api);
  try {
    await select(view, "관리 조직", "tenant-a"); await click(view, "승인");
    assert.match(view.container.textContent, /쓰기 기능이 준비되지 않았습니다/u);
    await select(view, "관리 조직", "tenant-b"); await click(view, "승인");
    assert.equal(writes, 1);
  } finally { await view.cleanup(); }
});

for (const action of ["승인", "거부", "역할 저장", "중지", "생성", "폐기"]) {
  test(`L3 실제 화면 ${action}은 정확 대상·version·원 세션으로 저장하고 같은 조직을 재조회한다`, async () => {
    const { api, calls } = fixture(); const view = await render(api);
    try {
      await select(view, "관리 조직", "tenant-a");
      if (action === "역할 저장") { await select(view, "linked-user 실제 역할", "editor"); await input(view, "변경 사유", "업무 조정"); }
      if (action === "생성") {
        await select(view, "대상 Workspace", "workspace-tenant-a");
        await input(view, "초대코드", "synthetic-ui-invitation");
        const daysInput = findElements(view.container, node => node.tagName === "INPUT" && props(node)?.["aria-label"] === "유효기간")[0];
        const countInput = findElements(view.container, node => node.tagName === "INPUT" && props(node)?.["aria-label"] === "최대 사용 횟수")[0];
        assert.equal(Number(daysInput.value), 1); assert.equal(Number(countInput.value), 1);
        await view.act(async () => { findElements(view.container, node => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
      } else await click(view, action);
      const writes = calls.filter(([name]) => ["decideJoinRequest", "changeMemberRole", "changeMemberState", "createInvitation", "revokeInvitation"].includes(name));
      assert.equal(writes.length, 1);
      const call = writes[0], options = call.at(-1);
      assert.equal(options.expectedSessionId, "s39-session"); assert.match(options.idempotencyKey, /^org-[a-z0-9-]+$/u);
      if (action === "승인" || action === "거부") assert.deepEqual(call.slice(0, -1), ["decideJoinRequest", "request-a", { approved: action === "승인", role: "member", expected_version: 1 }]);
      if (action === "역할 저장") assert.deepEqual(call.slice(0, -1), ["changeMemberRole", "tenant-a", "linked-user", { workspace_id: "workspace-tenant-a", role: "editor", expected_version: 1, expected_auth_version: 1, reason: "업무 조정" }]);
      if (action === "중지") assert.deepEqual(call.slice(0, -1), ["changeMemberState", "tenant-a", "linked-user", false, { expected_version: 1, reason: null }]);
      if (action === "생성") {
        assert.equal(call[0], "createInvitation"); assert.equal(call[1], "tenant-a");
        assert.equal(call[2].workspace_id, "workspace-tenant-a"); assert.equal(call[2].max_uses, 1);
        assert.equal(call[2].code, "synthetic-ui-invitation");
        assert.ok(Date.parse(call[2].expires_at) > Date.now()+86300000);
        const codeInput = findElements(view.container, node => node.tagName === "INPUT" && props(node)?.["aria-label"] === "초대코드")[0];
        assert.equal(codeInput.value, "");
      }
      if (action === "폐기") { assert.deepEqual(call.slice(0, -1), ["revokeInvitation", "tenant-a", "invitation-a"]); assert.equal(options.expectedVersion, 2); }
      assert.equal(calls.filter(([name]) => name === "joins").length, 2);
      assert.match(view.container.textContent, /저장했습니다/u);
    } finally { await view.cleanup(); }
  });
}

test("L3 첫 session 조회 중 pagehide·pageshow도 새 검증을 시작하고 늦은 원 session을 버린다", async () => {
  let release, reads = 0;
  const original = new Promise(resolve => { release = resolve; });
  const { api } = fixture({ getOrganizationSession: async () => ++reads === 1 ? original : session });
  const view = await render(api);
  try {
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("pagehide")); view.dom.document.dispatchEvent(new MinimalEvent("pageshow")); await Promise.resolve(); });
    assert.equal(reads, 2);
    assert.match(view.container.textContent, /관리할 조직을 선택하세요/u);
    await view.act(async () => { release({ ...session, session_id: "old-session" }); await original; });
    assert.equal(reads, 2);
    await select(view, "관리 조직", "tenant-a");
    assert.match(view.container.textContent, /신청자-tenant-a/u);
  } finally { release(session); await view.cleanup(); }
});

test("L3 저장 성공 뒤 재조회 실패는 조회 완료나 전체 실패로 가장하지 않는다", async () => {
  let reads = 0;
  const { api } = fixture({ listJoinRequests: async tenant => {
    if (++reads === 1) return [{ request_id: "request-a", tenant_id: tenant, user_id: "pending-user", state: "pending", version: 1 }];
    throw new TypeError("조회 연결 실패");
  } });
  const view = await render(api);
  try {
    await select(view, "관리 조직", "tenant-a"); await click(view, "승인");
    assert.match(view.container.textContent, /저장은 확인.*다시 조회/u);
    assert.doesNotMatch(view.container.textContent, /현재 조직을 다시 조회했습니다/u);
  } finally { await view.cleanup(); }
});

for (const interruption of ["network", "401", "403", "503", "malformed", "pagehide", "tenant"]) {
  test(`L3 미확정 재확인 사전조회 ${interruption} 뒤에도 원 키와 본문을 보존한다`, async () => {
    let reads = 0, release;
    const checking = new Promise(resolve => { release = resolve; });
    const writes = [];
    const { api } = fixture({
      getOrganizationSession: async () => {
        if (++reads !== 3) return session;
        if (interruption === "pagehide" || interruption === "tenant") return checking;
        if (interruption === "malformed") return null;
        const error = new TypeError("합성 재확인 조회 실패");
        if (interruption !== "network") error.status = Number(interruption);
        throw error;
      },
      decideJoinRequest: async (...args) => { writes.push(args); if (writes.length === 1) throw new TypeError("최초 응답 유실"); return {}; },
    });
    const view = await render(api);
    try {
      await select(view, "관리 조직", "tenant-a"); await click(view, "승인");
      assert.match(view.container.textContent, /결과 미확정/u);
      await click(view, "동일 요청 결과 확인");
      if (interruption === "pagehide") {
        await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("pagehide")); view.dom.document.dispatchEvent(new MinimalEvent("pageshow")); await Promise.resolve(); });
      } else if (interruption === "tenant") {
        await select(view, "관리 조직", "tenant-b"); await select(view, "관리 조직", "tenant-a");
      } else if (interruption === "401" || interruption === "403" || interruption === "malformed") {
        assert.doesNotMatch(view.container.textContent, /신청자-tenant-a/u);
        await click(view, "다시 조회");
      }
      if (interruption === "pagehide" || interruption === "tenant") await view.act(async () => { release(session); await checking; });
      assert.match(view.container.textContent, /결과 미확정/u);
      await click(view, "승인"); assert.equal(writes.length, 1, "새 키의 저장은 금지한다");
      await click(view, "동일 요청 결과 확인");
      assert.equal(writes.length, 2);
      assert.deepEqual(writes[1].slice(0, -1), writes[0].slice(0, -1));
      assert.equal(writes[1].at(-1).idempotencyKey, writes[0].at(-1).idempotencyKey);
      assert.equal(writes[1].at(-1).expectedSessionId, "s39-session");
    } finally { release(session); await view.cleanup(); }
  });
}

for (const status of [403, 503]) {
  test(`L3 미확정 재확인 전송 ${status}도 이전 미확정을 확정 실패로 지우지 않는다`, async () => {
    const writes = [];
    const { api } = fixture({ decideJoinRequest: async (...args) => {
      writes.push(args);
      if (writes.length === 1) throw new TypeError("최초 응답 유실");
      if (writes.length === 2) { const error = new Error("ORGANIZATION_WORKFLOW_UNAVAILABLE"); error.status = status; throw error; }
      return {};
    } });
    const view = await render(api);
    try {
      await select(view, "관리 조직", "tenant-a"); await click(view, "승인"); await click(view, "동일 요청 결과 확인");
      if (status === 403) await click(view, "다시 조회");
      assert.match(view.container.textContent, /결과 미확정/u);
      await click(view, "승인"); assert.equal(writes.length, 2);
      await click(view, "동일 요청 결과 확인"); assert.equal(writes.length, 3);
      for (const write of writes.slice(1)) {
        assert.equal(write.at(-1).idempotencyKey, writes[0].at(-1).idempotencyKey);
        assert.deepEqual(write.slice(0, -1), writes[0].slice(0, -1));
      }
    } finally { await view.cleanup(); }
  });
}

test("L3 최초 전송 전 조회 실패는 미확정이 아니므로 안전하게 새 제출할 수 있다", async () => {
  let reads = 0, writes = 0;
  const { api } = fixture({ getOrganizationSession: async () => { if (++reads === 2) throw new TypeError("전송 전 조회 실패"); return session; }, decideJoinRequest: async () => { writes++; return {}; } });
  const view = await render(api);
  try {
    await select(view, "관리 조직", "tenant-a"); await click(view, "승인");
    assert.equal(writes, 0); assert.doesNotMatch(view.container.textContent, /결과 미확정/u);
    await click(view, "승인"); assert.equal(writes, 1);
  } finally { await view.cleanup(); }
});

test("L3 미확정 재확인에서 확인된 다른 세션만 원 요청을 폐기한다", async () => {
  let reads = 0;
  const changed = { ...session, user_id: "other-admin", session_id: "other-session" }, writes = [];
  const { api } = fixture({ getOrganizationSession: async () => ++reads >= 3 ? changed : session,
    decideJoinRequest: async (...args) => { writes.push(args); if (writes.length === 1) throw new TypeError("응답 유실"); return {}; } });
  const view = await render(api);
  try {
    await select(view, "관리 조직", "tenant-a"); await click(view, "승인"); await click(view, "동일 요청 결과 확인");
    assert.doesNotMatch(view.container.textContent, /결과 미확정|신청자-tenant-a/u);
    assert.equal(writes.length, 1);
    await select(view, "관리 조직", "tenant-a"); await click(view, "승인");
    assert.equal(writes.length, 2); assert.equal(writes[1].at(-1).expectedSessionId, "other-session");
    assert.notEqual(writes[1].at(-1).idempotencyKey, writes[0].at(-1).idempotencyKey);
  } finally { await view.cleanup(); }
});
