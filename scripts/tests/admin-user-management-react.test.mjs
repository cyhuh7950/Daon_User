import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

async function bundle(entry, output, name) {
  const root = path.resolve(import.meta.dirname, "../..");
  const { build } = await import("vite");
  await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
    lib: { entry: path.join(root, entry), formats: ["es"], fileName: name },
    rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
  const built = (await readdir(output)).find((file) => file.startsWith(name) && /\.m?js$/u.test(file));
  return import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
}

function reactProps(element) { return element[Object.keys(element).find((key) => key.startsWith("__reactProps"))]; }

async function render(entry, exportName, props, prefix) {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, prefix));
  const dom = installMinimalDom();
  dom.document.defaultView.location = { assign() {}, replace() {}, reload() {} };
  globalThis.window.location = dom.document.defaultView.location;
  const { createElement, act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const module = await bundle(entry, output, exportName);
  const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
  const reactRoot = createRoot(container);
  await act(async () => { reactRoot.render(createElement(module[exportName], props)); await Promise.resolve(); });
  return { act, container, dom, output, reactRoot, cleanup: async () => { await act(async () => reactRoot.unmount()); dom.restore(); await rm(output, { recursive: true, force: true }); } };
}

function userRows(container) { return findElements(container, (node) => node.tagName === "TR" && findElements(node, (child) => child.tagName === "TD").length > 0); }
async function openUser(view, index = 0) {
  await view.act(async () => { userRows(view.container)[index].dispatchEvent(new MinimalEvent("dblclick")); await Promise.resolve(); });
}
function selectByLabel(view, label) { return findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0]; }
async function selectValue(view, label, value) {
  await view.act(async () => { reactProps(selectByLabel(view, label)).onChange({ target: { value } }); await Promise.resolve(); });
}
async function submitUser(view) {
  await view.act(async () => { findElements(view.container, (node) => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
}

async function selectAllUsers(view) {
  const checkbox = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.["aria-label"] === "전체 사용자 선택")[0];
  await view.act(async () => { reactProps(checkbox).onChange({ target: { checked: true } }); });
}

test("S38 initial admin 표시는 서버 protected만 따르고 조직 조회 없이 읽기 전용 보호를 유지한다", async () => {
  const protectedUser = { user_id: "initial-user", login_id: "root-account", email: null, has_email: false, state: "active", protected: true };
  const calls = [];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "operator", is_system_admin: true }), getUsers: async () => [protectedUser],
    getTenants: async () => { calls.push("tenants"); return []; }, getMemberships: async () => { calls.push("membership"); },
    editUser: async () => { calls.push("edit"); }, setUserState: async () => { calls.push("state"); },
    setMembershipRole: async () => { calls.push("role"); }, resetPassword: async () => { calls.push("reset"); }, removeUser: async () => { calls.push("delete"); },
  }, ".s38-initial-admin-display-");
  const confirm = window.confirm; window.confirm = () => true;
  try {
    const row = userRows(view.container)[0];
    assert.equal(findElements(row, (node) => node.getAttribute?.("class") === "admin-user-role-list")[0].textContent, "시스템 관리자");
    const checkbox = findElements(row, (node) => node.tagName === "INPUT")[0];
    assert.equal(checkbox.disabled, true);
    await view.act(async () => { reactProps(checkbox).onChange({ target: { checked: true } }); });
    assert.match(view.container.textContent, /선택 0건/u);
    await openUser(view);
    assert.deepEqual(calls, [], "보호 초기 관리자 열기는 조직/멤버십 API를 호출하지 않는다");
    assert.match(view.container.textContent, /시스템 관리자 권한은 조직·Workspace 멤버십 역할과 별도입니다/u);
    assert.match(view.container.textContent, /읽기 전용/u);
    assert.doesNotMatch(view.container.textContent, /조직·Workspace 선택 필요|조직을 선택하세요|Workspace를 선택하세요|전체 관리|전역 접근/u);
    assert.equal(selectByLabel(view, "조직 선택"), undefined); assert.equal(selectByLabel(view, "작업공간 선택"), undefined);
    assert.equal(buttonByText(view.container, "수정").disabled, true);
    assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, true);
    assert.equal(selectByLabel(view, "계정 상태").disabled, true);
    await submitUser(view);
    await view.act(async () => { reactProps(buttonByText(view.container, "비밀번호 초기화")).onClick(); });
    assert.deepEqual(calls, []);
  } finally { window.confirm = confirm; await view.cleanup(); }
});

test("S38 initial admin 전환은 늦은 일반 사용자 역할을 폐기하고 admin 이름만으로 권한 표시를 부여하지 않는다", async () => {
  let tenants = 0, memberships = 0, finishOld;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "operator", is_system_admin: true }),
    getUsers: async () => [
      { user_id: "initial-user", login_id: "root-account", email: null, has_email: false, state: "active", protected: true },
      { user_id: "ordinary-user", login_id: "admin", email: "ordinary@example.test", has_email: true, state: "active", protected: false },
    ],
    getTenants: async () => { tenants++; return [{ tenant_id: "ta", name: "QA 조직" }, { tenant_id: "tb", name: "다른 조직" }]; },
    getMemberships: async (tenant) => { memberships++; return tenant === "tb" ? new Promise((resolve) => { finishOld = resolve; })
      : { tenant: null, target_is_system_admin: false, workspaces: [{ workspace_id: "wa", role: "viewer", state: "active", version: 21 }] }; },
  }, ".s38-initial-admin-transition-");
  try {
    const ordinary = userRows(view.container)[1];
    assert.match(ordinary.textContent, /조직·Workspace 선택 필요/u);
    assert.doesNotMatch(ordinary.textContent, /시스템 관리자/u);
    await openUser(view, 1); await selectValue(view, "조직 선택", "ta"); await selectValue(view, "작업공간 선택", "wa");
    assert.match(userRows(view.container)[1].textContent, /마지막 조회.*열람자.*21/u);
    assert.equal(buttonByText(view.container, "수정").disabled, false);
    await selectValue(view, "조직 선택", "tb");
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await openUser(view, 0);
    assert.equal(tenants, 1, "초기 관리자 전환 때 조직 API를 추가 호출하지 않는다"); assert.equal(memberships, 2);
    await view.act(async () => { finishOld({ tenant: null, target_is_system_admin: false, workspaces: [{ workspace_id: "stale-wa", role: "editor", state: "active", version: 99 }] }); await Promise.resolve(); });
    assert.equal(selectByLabel(view, "조직 선택"), undefined);
    assert.doesNotMatch(view.container.textContent, /stale-wa|version 99/u);
    assert.equal(findElements(userRows(view.container)[0], (node) => node.getAttribute?.("class") === "admin-user-role-list")[0].textContent, "시스템 관리자");
  } finally { finishOld?.({ tenant: null, workspaces: [] }); await view.cleanup(); }
});

for (const operation of ["save", "reset"]) {
  test(`S38 review2 ${operation} 진행 중 동일 세션 재검증·목록 조회 뒤 늦은 응답 유실도 새 요청을 차단한다`, async () => {
    for (const eventType of ["popstate", "pageshow"]) for (const listTiming of ["before", "after"]) {
      let user = { user_id: "one", login_id: "one", email: "old@example.test", has_email: true, state: "active", protected: false };
      let reads = 0, failMutation, finishList;
      const calls = [];
      const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
        getSession: async () => ({ user_id: "admin", session_id: "session-a", is_system_admin: true }),
        getUsers: async () => { reads++; const snapshot = [{ ...user }]; return reads === 2 && listTiming === "after" ? new Promise((resolve) => { finishList = () => resolve(snapshot); }) : snapshot; },
        getTenants: async () => [],
        editUser: async (id, input, options) => { calls.push(["save", id, options.idempotencyKey]); return new Promise((_resolve, reject) => { failMutation = () => { user = { ...user, ...input }; reject(new Error("ADMIN_USERS_RESPONSE_INVALID")); }; }); },
        resetPassword: async (id, options) => { calls.push(["reset", id, options.idempotencyKey]); return new Promise((_resolve, reject) => { failMutation = () => reject(new Error("ADMIN_USERS_RESPONSE_INVALID")); }); },
      }, `.s38-review2-${operation}-${eventType}-${listTiming}-`);
      const confirm = window.confirm; window.confirm = () => true;
      try {
        await openUser(view);
        if (operation === "save") {
          const email = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0];
          await view.act(async () => { reactProps(email).onChange({ target: { value: "new@example.test" } }); });
          await submitUser(view);
        } else await view.act(async () => { buttonByText(view.container, "비밀번호 초기화").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
        assert.equal(calls.length, 1);
        await view.act(async () => { const event = new MinimalEvent(eventType); event.persisted = true; document.dispatchEvent(event); await Promise.resolve(); });
        assert.equal(reads, 2);
        await view.act(async () => { failMutation(); await Promise.resolve(); });
        if (finishList) await view.act(async () => { finishList(); await Promise.resolve(); });
        await openUser(view);
        assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, true, `${operation}/${eventType}/${listTiming}`);
        if (operation === "save") {
          assert.equal(buttonByText(view.container, "수정").disabled, true);
          assert.match(view.container.textContent, /저장 결과 미확정/u);
          const email = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0];
          await view.act(async () => { reactProps(email).onChange({ target: { value: "again@example.test" } }); });
          await submitUser(view);
        } else assert.match(view.container.textContent, /초기화 메일.*미확정/u);
        await view.act(async () => { reactProps(buttonByText(view.container, "비밀번호 초기화")).onClick(); await Promise.resolve(); });
        assert.equal(calls.length, 1, "진행 중에 읽은 목록은 늦게 확정된 전송 결과를 증명하지 않는다");
        assert.doesNotMatch(view.container.textContent, /메일을 발송했습니다/u);
      } finally { failMutation?.(); finishList?.(); window.confirm = confirm; await view.cleanup(); }
    }
  });
}

test("S38 review2 다른 세션·사용자 전환 뒤 이전 요청의 늦은 실패는 새 화면에 미확정을 남기지 않는다", async () => {
  for (const operation of ["save", "reset"]) for (const changed of ["session", "user"]) {
    let actor = "admin-a", session = "session-a", failMutation;
    const user = { user_id: "one", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false };
    let calls = 0;
    const failLater = async () => { calls++; return new Promise((_resolve, reject) => { failMutation = () => reject(new Error("ADMIN_USERS_RESPONSE_INVALID")); }); };
    const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
      getSession: async () => ({ user_id: actor, session_id: session, is_system_admin: true }),
      getUsers: async () => [user], getTenants: async () => [], editUser: failLater, resetPassword: failLater,
    }, `.s38-review2-context-${operation}-${changed}-`);
    const confirm = window.confirm; window.confirm = () => true;
    try {
      await openUser(view);
      if (operation === "save") {
        const email = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0];
        await view.act(async () => { reactProps(email).onChange({ target: { value: "new@example.test" } }); });
        await submitUser(view);
      } else await view.act(async () => { buttonByText(view.container, "비밀번호 초기화").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
      await view.act(async () => { if (changed === "session") session = "session-b"; else actor = "admin-b"; document.dispatchEvent(new MinimalEvent("popstate")); await Promise.resolve(); });
      await view.act(async () => { failMutation(); await Promise.resolve(); });
      await openUser(view);
      assert.equal(buttonByText(view.container, "수정").disabled, false);
      assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, false);
      assert.doesNotMatch(view.container.textContent, /미확정|ADMIN_USER_PASSWORD_RESET_FAILED/u);
      assert.equal(calls, 1, "인증 문맥 변경으로 자동 재전송하지 않는다");
    } finally { failMutation?.(); window.confirm = confirm; await view.cleanup(); }
  }
});

test("S38 review I1 저장 미확정은 닫기·재열기 뒤에도 실제 목록 재조회 성공 전 저장과 reset을 차단한다", async () => {
  let user = { user_id: "one", login_id: "one", email: "old@example.test", has_email: true, state: "active", protected: false };
  const other = { ...user, user_id: "two", login_id: "two", email: "two@example.test" };
  let writes = 0, resets = 0, reads = 0, failRead = false;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", session_id: "session-a", is_system_admin: true }),
    getUsers: async () => { reads++; if (failRead) throw new Error("ADMIN_USERS_UNAVAILABLE"); return [user, other]; },
    getTenants: async () => [],
    editUser: async (_id, input) => { writes++; user = { ...user, ...input }; throw new Error("ADMIN_USERS_RESPONSE_INVALID"); },
    resetPassword: async () => { resets++; },
  }, ".s38-review-i1-");
  const confirm = window.confirm; window.confirm = () => true;
  try {
    await openUser(view);
    assert.match(view.container.textContent, /일부 항목만 저장될 수 있습니다/u);
    const email = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0];
    await view.act(async () => { reactProps(email).onChange({ target: { value: "new@example.test" } }); });
    await submitUser(view);
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await openUser(view);
    assert.equal(reads, 1, "재열기는 캐시를 쓰므로 저장 결과 검증이 아니다");
    assert.equal(buttonByText(view.container, "수정").disabled, true);
    assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, true);
    assert.match(view.container.textContent, /저장 결과 미확정/u);
    await submitUser(view);
    await view.act(async () => { reactProps(buttonByText(view.container, "비밀번호 초기화")).onClick(); });
    assert.equal(writes, 1); assert.equal(resets, 0);
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await openUser(view, 1);
    assert.equal(buttonByText(view.container, "수정").disabled, false, "다른 대상에게 미확정을 전파하지 않는다");
    assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, false);
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await openUser(view);
    failRead = true;
    await view.act(async () => { buttonByText(view.container, "서버 상태 다시 조회").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(buttonByText(view.container, "수정").disabled, true);
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await openUser(view); assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, true);
    failRead = false;
    await view.act(async () => { buttonByText(view.container, "서버 상태 다시 조회").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(reads, 3); assert.equal(buttonByText(view.container, "수정").disabled, false);
    assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, false);
    assert.match(view.container.textContent, /저장된 이메일: new@example.test/u);
    await submitUser(view); assert.equal(writes, 1); assert.equal(resets, 0);
  } finally { window.confirm = confirm; await view.cleanup(); }
});

test("S38 review I2 reset 응답 유실은 닫기·재열기·목록 조회에도 새 키 중복 송신을 막고 세션 전환 때 누출하지 않는다", async () => {
  const user = { user_id: "one", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false };
  let actor = "admin-a", sessionId = "session-a", reads = 0;
  const sends = [];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: actor, session_id: sessionId, is_system_admin: true }),
    getUsers: async () => { reads++; return [user]; }, getTenants: async () => [],
    resetPassword: async (id, options) => { sends.push([id, options.idempotencyKey]); throw new Error("ADMIN_USERS_RESPONSE_INVALID"); },
  }, ".s38-review-i2-");
  const confirm = window.confirm; window.confirm = () => true;
  try {
    await openUser(view);
    await view.act(async () => { buttonByText(view.container, "비밀번호 초기화").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    await view.act(async () => { reactProps(buttonByText(view.container, "비밀번호 초기화")).onClick(); await Promise.resolve(); });
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await openUser(view);
    await view.act(async () => { reactProps(buttonByText(view.container, "비밀번호 초기화")).onClick(); await Promise.resolve(); });
    assert.equal(sends.length, 1, "미확정 reset은 새 멱등키로 재전송하지 않는다");
    assert.match(sends[0][1], /^admin-password-reset-/u);
    assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, true);
    assert.match(view.container.textContent, /초기화 메일.*미확정/u);
    assert.doesNotMatch(view.container.textContent, /메일을 발송했습니다/u);
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await view.act(async () => { buttonByText(view.container, "새로고침").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    await openUser(view); assert.equal(reads, 2);
    assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, true, "목록 조회는 메일 송신 확인이 아니다");
    await view.act(async () => { const event = new MinimalEvent("pageshow"); event.persisted = true; sessionId = "session-b"; document.dispatchEvent(event); await Promise.resolve(); });
    await openUser(view);
    assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, false, "이전 세션의 미확정을 새 세션에 노출하지 않는다");
    assert.doesNotMatch(view.container.textContent, /초기화 메일.*미확정/u);
    await view.act(async () => { buttonByText(view.container, "비밀번호 초기화").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(sends.length, 2);
    await view.act(async () => { const event = new MinimalEvent("pageshow"); event.persisted = true; actor = "admin-b"; document.dispatchEvent(event); await Promise.resolve(); });
    await openUser(view); assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, false);
    assert.doesNotMatch(view.container.textContent, /초기화 메일.*미확정/u);
    assert.equal(sends.length, 2, "세션/사용자 변경 자체로 자동 송신하지 않는다");
  } finally { window.confirm = confirm; await view.cleanup(); }
});

test("S38 review Minor1 성공한 빈 Workspace 목록만 선택 범위의 역할 없음으로 표시한다", async () => {
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true }),
    getUsers: async () => [{ user_id: "one", login_id: "one", email: "one@example.test", state: "active", protected: false }],
    getTenants: async () => [{ tenant_id: "ta", name: "QA 조직" }, { tenant_id: "tb", name: "다른 조직" }],
    getMemberships: async (tenant) => { if (tenant === "tb") throw new Error("RESOURCE_UNAVAILABLE"); return { tenant: null, target_is_system_admin: false, workspaces: [] }; },
  }, ".s38-review-empty-scope-");
  try {
    await openUser(view);
    assert.doesNotMatch(view.container.textContent, /Workspace 역할 없음|조직 역할 없음/u);
    await selectValue(view, "조직 선택", "ta");
    assert.match(view.container.textContent, /선택한 조직 범위에 Workspace 역할 없음/u);
    assert.match(view.container.textContent, /조직 역할 없음/u);
    await selectValue(view, "조직 선택", "tb");
    assert.match(view.container.textContent, /선택 범위의 역할 조회 실패/u);
    assert.doesNotMatch(view.container.textContent, /Workspace 역할 없음|조직 역할 없음/u);
  } finally { await view.cleanup(); }
});

test("S38 목록은 관리 열·행별 action 없이 행과 키보드로 통합 수정 팝업에 진입한다", async () => {
  const user = { user_id: "u1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false };
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true, password_change_required: false }), getUsers: async () => [user], getTenants: async () => [],
  }, ".s38-row-modal-");
  try {
    assert.deepEqual(findElements(view.container, (node) => node.tagName === "TH").map((node) => node.textContent), ["", "사용자", "이메일", "상태", "역할"]);
    const row = userRows(view.container)[0];
    assert.equal(findElements(row, (node) => node.tagName === "BUTTON").length, 0);
    const checkbox = findElements(row, (node) => node.tagName === "INPUT")[0];
    await view.act(async () => { checkbox.dispatchEvent(new MinimalEvent("dblclick")); });
    assert.equal(findElements(view.container, (node) => node.getAttribute?.("role") === "dialog").length, 0);
    row.focus();
    await view.act(async () => { reactProps(row).onKeyDown({ key: "Enter", target: row, currentTarget: row, preventDefault() {} }); await Promise.resolve(); });
    const dialog = findElements(view.container, (node) => node.getAttribute?.("role") === "dialog")[0];
    assert.ok(dialog);
    assert.equal(dialog.getAttribute("aria-modal"), "true");
    assert.match(dialog.textContent, /사용자 수정/u);
    assert.equal(view.dom.document.activeElement, buttonByText(dialog, "취소"));
    await view.act(async () => { reactProps(dialog).onKeyDown({ key: "Escape", preventDefault() {} }); });
    assert.equal(findElements(view.container, (node) => node.getAttribute?.("role") === "dialog").length, 0);
    assert.equal(view.dom.document.activeElement, row);
    await openUser(view);
    assert.equal(findElements(view.container, (node) => node.tagName === "FORM").length, 1);
    assert.equal(buttonByText(view.container, "역할 변경"), undefined);
    assert.ok(buttonByText(view.container, "수정"));
  } finally { await view.cleanup(); }
});

test("S38 검증된 Workspace 역할은 범위·마지막 조회로 목록에 남고 tenant null은 역할 없음이다", async () => {
  let reads = 0;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true, password_change_required: false }),
    getUsers: async () => [{ user_id: "u1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false }],
    getTenants: async () => [{ tenant_id: "ta", name: "QA 조직" }],
    getMemberships: async () => { reads++; return { tenant: null, target_is_system_admin: false, workspaces: [{ workspace_id: "wa", role: "viewer", state: "active", version: 21 }] }; },
  }, ".s38-scope-summary-");
  try {
    assert.match(userRows(view.container)[0].textContent, /조직·Workspace 선택 필요/u);
    await openUser(view);
    assert.equal(reads, 0);
    await selectValue(view, "조직 선택", "ta");
    assert.match(view.container.textContent, /조직 역할 없음/u);
    assert.match(view.container.textContent, /Workspace를 선택/u);
    await selectValue(view, "작업공간 선택", "wa");
    assert.match(userRows(view.container)[0].textContent, /마지막 조회.*QA 조직.*ta.*Workspace wa.*열람자.*21/u);
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    assert.match(userRows(view.container)[0].textContent, /마지막 조회.*열람자/u);
  } finally { await view.cleanup(); }
});

test("S38 통합 수정은 사전검증 뒤 변경 항목만 순차 저장하고 reset은 자동 실행하지 않는다", async () => {
  const user = { user_id: "u1", login_id: "one", email: "old@example.test", has_email: true, state: "active", protected: false };
  const calls = []; let reads = 0;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true, password_change_required: false }), getUsers: async () => [user],
    getTenants: async () => [{ tenant_id: "ta", name: "QA 조직" }],
    getMemberships: async () => ({ tenant: null, target_is_system_admin: false, workspaces: [{ workspace_id: "wa", role: ++reads === 1 ? "viewer" : "editor", state: "active", version: reads === 1 ? 21 : 22 }] }),
    editUser: async (id, input) => { calls.push(["email", id, input]); return { ...user, email: input.email }; },
    setUserState: async (id, state) => { calls.push(["state", id, state]); return { ...user, email: "new@example.test", state }; },
    setMembershipRole: async (...args) => { calls.push(["role", ...args]); return { version: 22 }; }, resetPassword: async () => { calls.push(["reset"]); },
  }, ".s38-integrated-save-");
  const confirm = globalThis.window.confirm; globalThis.window.confirm = () => true;
  try {
    await openUser(view); await selectValue(view, "조직 선택", "ta"); await selectValue(view, "작업공간 선택", "wa");
    const email = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0];
    await view.act(async () => { reactProps(email).onChange({ target: { value: "new@example.test" } }); });
    await selectValue(view, "계정 상태", "suspended"); await selectValue(view, "변경할 역할", "editor");
    await submitUser(view); assert.deepEqual(calls, []);
    assert.match(view.container.textContent, /사유/u);
    await selectValue(view, "변경 사유", "ACCESS_REVIEW"); await submitUser(view);
    assert.deepEqual(calls.map((call) => call[0]), ["email", "role", "state"]);
    assert.deepEqual(calls[1].slice(1, 5), ["ta", "wa", "u1", { role: "editor", expected_version: 21, reason: "ACCESS_REVIEW" }]);
    assert.equal(findElements(view.container, (node) => node.tagName === "FORM").length, 0);
    assert.match(userRows(view.container)[0].textContent, /마지막 조회.*편집자/u);
  } finally { globalThis.window.confirm = confirm; await view.cleanup(); }
});

test("S38 부분 성공·통신 유실은 후속 저장과 reset을 막고 명시 재조회 뒤 성공분을 재전송하지 않는다", async () => {
  for (const failure of ["email", "role", "state"]) {
    let user = { user_id: "u1", login_id: "one", email: "old@example.test", has_email: true, state: "active", protected: false };
    let role = "viewer", version = 21;
    const calls = [];
    const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
      getSession: async () => ({ user_id: "admin", is_system_admin: true }), getUsers: async () => [user],
      getTenants: async () => [{ tenant_id: "ta", name: "QA 조직" }],
      getMemberships: async () => ({ tenant: null, target_is_system_admin: false, workspaces: [{ workspace_id: "wa", role, version, state: "active" }] }),
      editUser: async (_id, input) => { calls.push("email"); user = { ...user, ...input }; if (failure === "email") throw new Error("private network detail"); return user; },
      setMembershipRole: async () => { calls.push("role"); role = "editor"; version++; if (failure === "role") throw new Error("private network detail"); return { version }; },
      setUserState: async (_id, state) => { calls.push("state"); user = { ...user, state }; throw new Error("private network detail"); },
      resetPassword: async () => { calls.push("reset"); },
    }, `.s38-partial-${failure}-`);
    const confirm = window.confirm; window.confirm = () => true;
    try {
      await openUser(view); await selectValue(view, "조직 선택", "ta"); await selectValue(view, "작업공간 선택", "wa");
      const email = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0];
      await view.act(async () => { reactProps(email).onChange({ target: { value: "new@example.test" } }); });
      await selectValue(view, "변경할 역할", "editor"); await selectValue(view, "변경 사유", "ACCESS_REVIEW"); await selectValue(view, "계정 상태", "suspended");
      await submitUser(view);
      const expected = failure === "email" ? ["email"] : failure === "role" ? ["email", "role"] : ["email", "role", "state"];
      assert.deepEqual(calls, expected, failure);
      assert.match(view.container.textContent, failure === "email" ? /완료 항목: 없음/u : failure === "role" ? /완료 항목: 이메일/u : /완료 항목: 이메일, 역할/u);
      assert.match(view.container.textContent, /실패 또는 결과 미확정/u);
      assert.doesNotMatch(view.container.textContent, /private network detail/u);
      assert.equal(buttonByText(view.container, "수정").disabled, true);
      assert.equal(buttonByText(view.container, "비밀번호 초기화").disabled, true);
      assert.doesNotMatch(userRows(view.container)[0].textContent, /마지막 조회/u);
      assert.doesNotMatch(view.container.textContent, /현재 역할:/u, "실패 뒤 기존 역할은 현재 성공값이 아니다");
      await submitUser(view);
      await view.act(async () => { reactProps(buttonByText(view.container, "비밀번호 초기화")).onClick(); });
      assert.deepEqual(calls, expected);
      await view.act(async () => { buttonByText(view.container, "서버 상태 다시 조회").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
      assert.equal(reactProps(selectByLabel(view, "계정 상태")).value, user.state);
      assert.match(view.container.textContent, /미저장 입력은 버렸습니다/u);
      await submitUser(view);
      assert.deepEqual(calls, expected, "서버에 반영된 항목은 다시 보내지 않는다");
      assert.equal(findElements(view.container, (node) => node.tagName === "FORM").length, 0);
    } finally { window.confirm = confirm; await view.cleanup(); }
  }
});

test("S38 reset은 저장 이메일만 안내하고 미저장 입력·실행중 중복·닫기를 차단한다", async () => {
  const user = { user_id: "u1", login_id: "one", email: "saved@example.test", has_email: true, state: "active", protected: false };
  let resets = 0, release; const pending = new Promise((resolve) => { release = resolve; });
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true }), getUsers: async () => [user], getTenants: async () => [],
    resetPassword: async () => { resets++; await pending; },
  }, ".s38-reset-protection-");
  const confirm = window.confirm; window.confirm = () => true;
  try {
    await openUser(view);
    assert.match(view.container.textContent, /저장된 이메일: saved@example.test/u);
    const email = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0];
    await view.act(async () => { reactProps(email).onChange({ target: { value: "unsaved@example.test" } }); });
    const reset = buttonByText(view.container, "비밀번호 초기화");
    assert.equal(reset.getAttribute("type"), "button"); assert.equal(reset.disabled, true);
    await view.act(async () => { reactProps(reset).onClick(); }); assert.equal(resets, 0);
    await view.act(async () => { reactProps(email).onChange({ target: { value: user.email } }); });
    await view.act(async () => { reactProps(reset).onClick(); reactProps(reset).onClick(); await Promise.resolve(); });
    assert.equal(resets, 1); assert.equal(reset.disabled, true);
    assert.equal(buttonByText(view.container, "취소").disabled, true);
    const dialog = findElements(view.container, (node) => node.getAttribute?.("role") === "dialog")[0];
    await view.act(async () => { reactProps(dialog).onKeyDown({ key: "Escape", preventDefault() {} }); });
    assert.ok(view.container.contains(dialog));
    await view.act(async () => { release(); await pending; await Promise.resolve(); });
    assert.match(view.container.textContent, /비밀번호 초기화 메일을 발송했습니다/u);
  } finally { release(); window.confirm = confirm; await view.cleanup(); }
});

test("S38 선택 범위 조회 중·404 실패는 역할 없음이나 과거 성공으로 표시하지 않는다", async () => {
  let release;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true }),
    getUsers: async () => [{ user_id: "u1", login_id: "one", email: "one@example.test", state: "active", protected: false }],
    getTenants: async () => [{ tenant_id: "ta", name: "QA 조직" }, { tenant_id: "tb", name: "다른 조직" }],
    getMemberships: async (tenantId) => tenantId === "tb" ? new Promise((_resolve, reject) => { release = reject; })
      : { tenant: null, workspaces: [{ workspace_id: "wa", role: "viewer", state: "active", version: 21 }] },
  }, ".s38-scope-failure-");
  try {
    await openUser(view); await selectValue(view, "조직 선택", "ta"); await selectValue(view, "작업공간 선택", "wa");
    assert.match(userRows(view.container)[0].textContent, /마지막 조회.*열람자/u);
    await selectValue(view, "작업공간 선택", "");
    assert.doesNotMatch(userRows(view.container)[0].textContent, /마지막 조회/u);
    await selectValue(view, "작업공간 선택", "wa"); await selectValue(view, "조직 선택", "tb");
    assert.match(userRows(view.container)[0].textContent, /조회 중/u);
    assert.doesNotMatch(view.container.textContent, /마지막 조회|현재 역할: viewer|조직 역할 없음/u);
    await view.act(async () => { release(new Error("RESOURCE_UNAVAILABLE")); await Promise.resolve(); });
    assert.match(userRows(view.container)[0].textContent, /조회 실패/u);
    assert.match(view.container.textContent, /RESOURCE_UNAVAILABLE/u);
    assert.doesNotMatch(view.container.textContent, /마지막 조회|조직 역할 없음/u);
  } finally { release?.(new Error("TEST_CLEANUP")); await view.cleanup(); }
});

test("S38 행 키보드·팝업 Tab 경계와 필터 후 실제 삭제 대상 수가 일치한다", async () => {
  const removed = [];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true }),
    getUsers: async () => ["one", "two"].map((id) => ({ user_id: id, login_id: id, email: `${id}@example.test`, state: "active", protected: false })),
    getTenants: async () => [], removeUser: async (id) => { removed.push(id); },
  }, ".s38-focus-filter-");
  const confirm = window.confirm; window.confirm = () => true;
  try {
    const row = userRows(view.container)[0], checkbox = findElements(row, (node) => node.tagName === "INPUT")[0];
    await view.act(async () => { reactProps(row).onKeyDown({ key: "Enter", target: checkbox, currentTarget: row, preventDefault() { assert.fail("control 키는 소비하지 않는다"); } }); });
    assert.equal(findElements(view.container, (node) => node.tagName === "FORM").length, 0);
    await view.act(async () => { reactProps(row).onKeyDown({ key: " ", target: row, currentTarget: row, preventDefault() {} }); });
    const dialog = findElements(view.container, (node) => node.getAttribute?.("role") === "dialog")[0];
    const first = findElements(dialog, (node) => node.tagName === "INPUT")[0], last = buttonByText(dialog, "수정");
    last.focus(); reactProps(dialog).onKeyDown({ key: "Tab", currentTarget: dialog, preventDefault() {} }); assert.equal(document.activeElement, first);
    first.focus(); reactProps(dialog).onKeyDown({ key: "Tab", shiftKey: true, currentTarget: dialog, preventDefault() {} }); assert.equal(document.activeElement, last);
    await view.act(async () => { buttonByText(dialog, "취소").dispatchEvent(new MinimalEvent("click")); });
    assert.equal(document.activeElement, row);
    await selectAllUsers(view); assert.match(view.container.textContent, /선택 2건/u);
    const search = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "search")[0];
    await view.act(async () => { reactProps(search).onChange({ target: { value: "two" } }); });
    assert.match(view.container.textContent, /선택 1건/u);
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(removed, ["two"]); assert.match(view.container.textContent, /선택 0건/u);
    await view.act(async () => { reactProps(search).onChange({ target: { value: "" } }); });
    assert.equal(buttonByText(view.container, "선택 삭제").disabled, true);
    assert.equal(reactProps(findElements(userRows(view.container)[0], (node) => node.tagName === "INPUT")[0]).checked, false);
  } finally { window.confirm = confirm; await view.cleanup(); }
});

test("S38 대상·세션 전환 뒤 늦은 역할 응답은 이전 역할을 복원하지 않는다", async () => {
  let resolveOld, sessions = 0;
  const users = ["one", "two"].map((id) => ({ user_id: id, login_id: id, email: `${id}@example.test`, state: "active", protected: false }));
  const redirects = [];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: ++sessions === 1 }), getUsers: async () => users,
    getTenants: async () => [{ tenant_id: "ta", name: "QA 조직" }],
    getMemberships: async (_tenant, id) => id === "one" ? new Promise((resolve) => { resolveOld = resolve; })
      : { tenant: null, workspaces: [{ workspace_id: "wb", role: "viewer", state: "active", version: 8 }] },
    navigate: (path) => { redirects.push(path); },
  }, ".s38-late-target-session-");
  try {
    await openUser(view, 0); await selectValue(view, "조직 선택", "ta");
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await openUser(view, 1); await selectValue(view, "조직 선택", "ta"); await selectValue(view, "작업공간 선택", "wb");
    await view.act(async () => { resolveOld({ tenant: null, workspaces: [{ workspace_id: "stale-wa", role: "editor", state: "active", version: 99 }] }); await Promise.resolve(); });
    assert.doesNotMatch(view.container.textContent, /stale-wa|version 99/u);
    assert.match(userRows(view.container)[1].textContent, /마지막 조회.*wb.*열람자/u);
    const protectedArea = findElements(view.container, (node) => node.hasAttribute?.("data-session-validated"))[0];
    document.dispatchEvent(new MinimalEvent("pagehide"));
    assert.equal(protectedArea.hidden, true, "React flush 전 DOM 차단"); assert.equal(protectedArea.inert, true);
    await view.act(async () => { const event = new MinimalEvent("pageshow"); event.persisted = true; document.dispatchEvent(event); await Promise.resolve(); });
    assert.deepEqual(redirects, ["/notebooks"]); assert.equal(protectedArea.hidden, true);
    assert.doesNotMatch(protectedArea.textContent, /마지막 조회|one@example.test|two@example.test|stale-wa/u);
  } finally { resolveOld?.({ tenant: null, workspaces: [] }); await view.cleanup(); }
});

test("S38 저장 첫 요청 전에 활성 membership·사유·본인 상태·보호 계정을 검증한다", async () => {
  for (const kind of ["inactive", "reason", "self", "protected"]) {
    const user = { user_id: kind === "self" ? "admin" : "one", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: kind === "protected" };
    const calls = [];
    const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
      getSession: async () => ({ user_id: "admin", is_system_admin: true }), getUsers: async () => [user],
      getTenants: async () => [{ tenant_id: "ta", name: "QA 조직" }],
      getMemberships: async () => ({ tenant: null, workspaces: [{ workspace_id: "wa", role: "viewer", state: kind === "inactive" ? "suspended" : "active", version: 21 }] }),
      editUser: async () => { calls.push("email"); }, setUserState: async () => { calls.push("state"); },
      setMembershipRole: async () => { calls.push("role"); },
    }, `.s38-validation-${kind}-`);
    const confirm = window.confirm; window.confirm = () => true;
    try {
      await openUser(view);
      if (kind !== "protected") { await selectValue(view, "조직 선택", "ta"); await selectValue(view, "작업공간 선택", "wa"); }
      const email = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0];
      await view.act(async () => { reactProps(email).onChange({ target: { value: "new@example.test" } }); });
      if (kind !== "protected") { await selectValue(view, "변경할 역할", "editor"); await selectValue(view, "변경 사유", kind === "reason" ? "NOT_APPROVED" : "ACCESS_REVIEW"); }
      if (kind === "self") { assert.equal(selectByLabel(view, "계정 상태").disabled, true); await selectValue(view, "계정 상태", "suspended"); }
      if (kind === "protected") {
        assert.equal(email.disabled, true); assert.equal(selectByLabel(view, "변경할 역할"), undefined);
        assert.equal(selectByLabel(view, "계정 상태").disabled, true);
        assert.equal(buttonByText(view.container, "수정").disabled, true); assert.match(view.container.textContent, /읽기 전용/u);
      }
      await submitUser(view); assert.deepEqual(calls, [], kind);
    } finally { window.confirm = confirm; await view.cleanup(); }
  }
});

test("S38 역할 응답 검증 실패는 상태 변경을 멈추고 오래된 성공 표시를 버린다", async () => {
  let writes = 0, states = 0, reads = 0;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true }),
    getUsers: async () => [{ user_id: "one", login_id: "one", email: "one@example.test", state: "active", protected: false }],
    getTenants: async () => [{ tenant_id: "ta", name: "QA 조직" }],
    getMemberships: async () => { reads++; return { tenant: null, workspaces: [{ workspace_id: "wa", role: "viewer", state: "active", version: 21 }] }; },
    setMembershipRole: async () => { writes++; return { version: 22 }; }, setUserState: async () => { states++; },
  }, ".s38-role-invalid-response-");
  const confirm = window.confirm; window.confirm = () => true;
  try {
    await openUser(view); await selectValue(view, "조직 선택", "ta"); await selectValue(view, "작업공간 선택", "wa");
    await selectValue(view, "변경할 역할", "editor"); await selectValue(view, "변경 사유", "ACCESS_REVIEW"); await selectValue(view, "계정 상태", "suspended");
    await submitUser(view);
    assert.equal(writes, 1); assert.equal(states, 0); assert.equal(reads, 3);
    assert.match(view.container.textContent, /ADMIN_MEMBERSHIP_RESPONSE_INVALID/u);
    assert.match(userRows(view.container)[0].textContent, /저장 결과 미확정/u);
    assert.doesNotMatch(userRows(view.container)[0].textContent, /마지막 조회/u);
    await submitUser(view); assert.equal(writes, 1);
  } finally { window.confirm = confirm; await view.cleanup(); }
});

test("S38 승인 대기는 팝업의 기존 승인 API만 사용하고 실행 중 중복을 막는다", async () => {
  const user = { user_id: "one", login_id: "one", email: "one@example.test", has_email: true, state: "pending_approval", protected: false };
  let calls = 0, release; const pending = new Promise((resolve) => { release = resolve; });
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true }), getUsers: async () => [user], getTenants: async () => [],
    approveUser: async (id, options) => { calls++; assert.equal(id, user.user_id); assert.match(options.idempotencyKey, /^admin-approve-/u); await pending; return { ...user, state: "active" }; },
    setUserState: async () => { assert.fail("승인은 상태변경 API로 대체하지 않는다"); },
  }, ".s38-approval-");
  try {
    assert.equal(buttonByText(view.container, "승인"), undefined);
    await openUser(view); assert.equal(selectByLabel(view, "계정 상태").disabled, true);
    const approve = buttonByText(view.container, "승인"); assert.equal(approve.getAttribute("type"), "button");
    await view.act(async () => { reactProps(approve).onClick(); reactProps(approve).onClick(); await Promise.resolve(); });
    assert.equal(calls, 1); assert.equal(buttonByText(view.container, "취소").disabled, true);
    await view.act(async () => { release(); await pending; await Promise.resolve(); });
    assert.equal(reactProps(selectByLabel(view, "계정 상태")).value, "active");
    assert.equal(buttonByText(view.container, "승인"), undefined);
    assert.equal(selectByLabel(view, "계정 상태").disabled, false);
  } finally { release(); await view.cleanup(); }
});

test("S38 저장 실패 전에 시작한 늦은 범위 조회는 실패 후 검증 표시를 되살리지 않는다", async () => {
  let release;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", is_system_admin: true }),
    getUsers: async () => [{ user_id: "one", login_id: "one", email: "one@example.test", state: "active", protected: false }],
    getTenants: async () => [{ tenant_id: "ta", name: "QA 조직" }],
    getMemberships: async () => new Promise((resolve) => { release = resolve; }),
    editUser: async () => { throw new Error("ADMIN_USERS_RESPONSE_INVALID"); },
  }, ".s38-late-after-failure-");
  try {
    await openUser(view); await selectValue(view, "조직 선택", "ta");
    const email = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0];
    await view.act(async () => { reactProps(email).onChange({ target: { value: "new@example.test" } }); });
    await submitUser(view);
    await view.act(async () => { release({ tenant: null, target_is_system_admin: true, workspaces: [{ workspace_id: "stale-wa", role: "viewer", state: "active", version: 21 }] }); await Promise.resolve(); });
    assert.doesNotMatch(view.container.textContent, /별도 권한\): 예|조직 역할 없음|stale-wa/u);
    assert.match(userRows(view.container)[0].textContent, /저장 결과 미확정/u);
    assert.equal(buttonByText(view.container, "수정").disabled, true);
  } finally { release?.({ tenant: null, workspaces: [] }); await view.cleanup(); }
});

test("로그인은 초기 admin 단축 비밀번호를 허용하고 새 비밀번호 입력만 12자를 강제한다", async () => {
  const view = await render("apps/web/lib/auth-pane.jsx", "AuthPane", {}, ".admin-initial-login-");
  try {
    let passwordInputs = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "password");
    assert.equal(passwordInputs.length, 1);
    assert.equal(reactProps(passwordInputs[0]).minLength, undefined);

    await view.act(async () => { buttonByText(view.container, "가입하기").dispatchEvent(new MinimalEvent("click")); });
    passwordInputs = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "password");
    assert.equal(passwordInputs.length, 1);
    assert.equal(reactProps(passwordInputs[0]).minLength, 12);
  } finally { await view.cleanup(); }
});

test("Notebook 설정은 system admin에게만 사용자 관리를 표시하고 조직 항목은 노출하지 않는다", async () => {
  for (const [showUserManagement, expected] of [[false, false], [true, true]]) {
    const view = await render("packages/ui/src/notebook-home.jsx", "NotebookHome", { notebooks: [], showUserManagement }, `.admin-menu-${showUserManagement}-`);
    try {
      await view.act(async () => { buttonByText(view.container, "⚙ 설정").dispatchEvent(new MinimalEvent("click")); });
      assert.equal(Boolean(buttonByText(view.container, "사용자 관리")), expected);
      assert.doesNotMatch(view.container.textContent, /조직 가입|조직 관리자/u);
    } finally { await view.cleanup(); }
  }
});

test("일반 사용자의 admin 직접 접근은 목록 호출·사용자 렌더 전에 fail-closed한다", async () => {
  let listCalls = 0; const redirects = [];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: false }),
    getUsers: async () => { listCalls += 1; return [{ user_id: "secret-user", login_id: "secret", email: "secret@example.test", has_email: true, state: "active", protected: false }]; },
    navigate: (path) => redirects.push(path),
  }, ".admin-denial-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    assert.equal(listCalls, 0);
    assert.deepEqual(redirects, ["/notebooks"]);
    assert.doesNotMatch(view.container.textContent, /secret-user|secret/u);
  } finally { await view.cleanup(); }
});

test("비밀번호 제한 session은 Notebook home 목록 API를 호출하지 않는다", async () => {
  const calls = { session: 0, list: 0 }; const redirects = [];
  const view = await render("apps/web/components/notebook-home-workspace.jsx", "NotebookHomeWorkspace", {
    getSession: async () => { calls.session += 1; return { password_change_required: true }; },
    getNotebooks: async () => { calls.list += 1; throw new Error("MUST_NOT_BE_CALLED"); },
    navigate: (path) => redirects.push(path),
  }, ".restricted-home-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    assert.deepEqual(calls, { session: 1, list: 0 });
    assert.deepEqual(redirects, ["/password-change"]);
  } finally { await view.cleanup(); }
});

test("현재 workspace 조회가 거부돼도 본인 조직 전환 메뉴로 복구할 수 있다", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options?.method });
    if (url === "/bff/api/session/tenants") return Response.json({
      data: { current_tenant_id: "organization-old", tenants: [
        { tenant_id: "organization-new", display_name: "이동할 조직", kind: "organization", workspace_id: "workspace-new" },
      ] }, meta: { trace_id: "trace-scope-test" },
    });
    if (url === "/bff/api/session/tenant") return Response.json({
      data: { user_id: "qa-user", tenant_id: "organization-new", workspace_id: "workspace-new", session_id: "new-session" },
      meta: { trace_id: "trace-switch-test" },
    });
    throw new Error("unexpected request");
  };
  let view;
  try {
    view = await render("apps/web/components/notebook-home-workspace.jsx", "NotebookHomeWorkspace", {
      getSession: async () => ({ workspace_id: "workspace-old", user_id: "qa-user", is_system_admin: false,
        password_change_required: false }),
      getNotebooks: async () => { throw new Error("NOTEBOOK_UNAVAILABLE"); },
    }, ".notebook-scope-recovery-");
    const redirects = [];
    globalThis.window.location.replace = (path) => redirects.push(path);
    assert.match(view.container.textContent, /NOTEBOOK_UNAVAILABLE/u);
    await view.act(async () => { buttonByText(view.container, "⚙ 설정").dispatchEvent(new MinimalEvent("click")); });
    assert.ok(buttonByText(view.container, "조직 · 이동할 조직"));
    await view.act(async () => { buttonByText(view.container, "조직 · 이동할 조직").dispatchEvent(new MinimalEvent("click")); });
    assert.deepEqual(redirects, ["/notebooks"]);
    assert.deepEqual(calls.map((call) => call.url), ["/bff/api/session/tenants", "/bff/api/session/tenant"]);
  } finally {
    if (view) await view.cleanup();
    globalThis.fetch = originalFetch;
  }
});

test("전환 commit 뒤 응답 본문이 깨지면 이전 조직 노트북을 즉시 숨기고 재로그인한다", async () => {
  const originalFetch = globalThis.fetch;
  let serverTenant = "organization-old";
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options?.method, credentials: options?.credentials });
    if (url === "/bff/api/session/tenants") return Response.json({
      data: { current_tenant_id: serverTenant, tenants: [
        { tenant_id: "organization-old", display_name: "이전 조직", kind: "organization", workspace_id: "workspace-old" },
        { tenant_id: "organization-new", display_name: "새 조직", kind: "organization", workspace_id: "workspace-new" },
      ] }, meta: { trace_id: "trace-scope-test" },
    });
    if (url === "/bff/api/session/tenant") {
      serverTenant = "organization-new";
      return Response.json({ data: { tenant_id: "organization-new" }, meta: { trace_id: "trace-switch-test" } });
    }
    throw new Error("unexpected request");
  };
  let view;
  try {
    view = await render("apps/web/components/notebook-home-workspace.jsx", "NotebookHomeWorkspace", {
      getSession: async () => ({ workspace_id: "workspace-old", user_id: "qa-user", is_system_admin: false,
        password_change_required: false }),
      getNotebooks: async () => ({ data: [{ notebook_id: "notebook-old", title: "이전 노트북", source_count: 0,
        output_count: 0, updated_at: "2026-10-08T00:00:00Z", status: "empty", etag: '"notebook:1"' }] }),
    }, ".notebook-scope-uncertain-body-");
    const redirects = [];
    globalThis.window.location.replace = (path) => redirects.push(path);
    assert.match(view.container.textContent, /이전 노트북/u);
    await view.act(async () => { buttonByText(view.container, "⚙ 설정").dispatchEvent(new MinimalEvent("click")); });
    await view.act(async () => { buttonByText(view.container, "조직 · 새 조직").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const protectedRoot = view.container.firstChild;
    assert.equal(serverTenant, "organization-new");
    assert.equal(protectedRoot.hidden, true);
    assert.equal(protectedRoot.inert, true);
    assert.doesNotMatch(view.container.textContent, /이전 노트북|이전 조직/u);
    assert.deepEqual(redirects, ["/"]);
    assert.deepEqual(requests.map(({ url, method, credentials }) => [url, method, credentials]), [
      ["/bff/api/session/tenants", "GET", "same-origin"],
      ["/bff/api/session/tenant", "POST", "same-origin"],
    ]);
  } finally { if (view) await view.cleanup(); globalThis.fetch = originalFetch; }
});

test("전환 네트워크 유실은 이전 화면을 숨기고, 명시적 403·404·422는 기존 화면을 보존한다", async () => {
  const originalFetch = globalThis.fetch;
  try {
  for (const [outcome, expectedRedirect, status] of [
    ["lost", true, null], ["denied-403", false, 403],
    ["denied-404", false, 404], ["denied-422", false, 422],
  ]) {
    let view;
    globalThis.fetch = async (url) => {
      if (url === "/bff/api/session/tenants") return Response.json({
        data: { current_tenant_id: "organization-old", tenants: [
          { tenant_id: "organization-old", display_name: "이전 조직", kind: "organization", workspace_id: "workspace-old" },
          { tenant_id: "organization-new", display_name: "새 조직", kind: "organization", workspace_id: "workspace-new" },
        ] }, meta: { trace_id: "trace-scope-test" },
      });
      if (outcome === "lost") throw new TypeError("network response lost");
      return Response.json({ error: { code: "TENANT_UNAVAILABLE" } }, { status });
    };
    try {
      view = await render("apps/web/components/notebook-home-workspace.jsx", "NotebookHomeWorkspace", {
        getSession: async () => ({ workspace_id: "workspace-old", user_id: "qa-user", is_system_admin: false,
          password_change_required: false }),
        getNotebooks: async () => ({ data: [{ notebook_id: "notebook-old", title: "이전 노트북", source_count: 0,
          output_count: 0, updated_at: "2026-10-08T00:00:00Z", status: "empty", etag: '"notebook:1"' }] }),
      }, `.notebook-scope-${outcome}-`);
      const redirects = [];
      globalThis.window.location.replace = (path) => redirects.push(path);
      await view.act(async () => { buttonByText(view.container, "⚙ 설정").dispatchEvent(new MinimalEvent("click")); });
      await view.act(async () => { buttonByText(view.container, "조직 · 새 조직").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
      const protectedRoot = view.container.firstChild;
      assert.equal(protectedRoot.hidden, expectedRedirect);
      assert.equal(protectedRoot.inert, expectedRedirect);
      assert.equal(view.container.textContent.includes("이전 노트북"), !expectedRedirect);
      assert.deepEqual(redirects, expectedRedirect ? ["/"] : []);
      if (!expectedRedirect) {
        await view.act(async () => { buttonByText(view.container, "⚙ 설정").dispatchEvent(new MinimalEvent("click")); });
        assert.match(view.container.textContent, /조직 범위를 확인할 수 없습니다/u);
      }
    } finally { if (view) await view.cleanup(); }
  }
  } finally { globalThis.fetch = originalFetch; }
});

test("HTTP 테스트 화면은 randomUUID 없이도 새 Notebook 요청을 전송한다", async () => {
  const view = await render("apps/web/components/notebook-home-workspace.jsx", "NotebookHomeWorkspace", {
    getSession: async () => ({
      workspace_id: "workspace-test", login_id: "qa-user",
      is_system_admin: false, password_change_required: false,
    }),
    getNotebooks: async () => ({ data: [] }),
  }, ".notebook-http-create-");
  const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const originalFetch = globalThis.fetch;
  const requests = [];
  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: { getRandomValues(bytes) { bytes.fill(7); return bytes; } },
    });
    globalThis.fetch = async (url, options) => {
      requests.push({ url, options });
      return Response.json({
        data: {
          notebook_id: "notebook-test", title: "HTTP Notebook", source_count: 0,
          output_count: 0, updated_at: "2026-10-03T00:00:00Z", status: "empty",
          etag: '"notebook:1"',
        },
        meta: { trace_id: "trace-test", workspace_id: "workspace-test", replayed: false },
      }, { status: 201, headers: { ETag: '"notebook:1"' } });
    };
    await view.act(async () => { buttonByText(view.container, "＋ 새 Notebook").dispatchEvent(new MinimalEvent("click")); });
    const dialog = findElements(view.container, (node) => node.getAttribute?.("role") === "dialog")[0];
    const title = findElements(dialog, (node) => node.tagName === "INPUT")[0];
    await view.act(async () => { reactProps(title).onChange({ target: { value: "HTTP Notebook" } }); });
    await view.act(async () => {
      findElements(dialog, (node) => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit"));
      await Promise.resolve();
    });
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/bff/api/workspaces/workspace-test/notebooks");
    assert.match(requests[0].options.headers["Idempotency-Key"], /^notebook-[a-f0-9]{32}$/u);
    assert.equal(findElements(view.container, (node) => node.getAttribute?.("role") === "dialog").length, 0);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto);
    else delete globalThis.crypto;
    await view.cleanup();
  }
});

test("비밀번호 제한 session은 selected Notebook get/context API를 호출하지 않는다", async () => {
  const calls = { session: 0, get: 0, context: 0 }; const redirects = [];
  const view = await render("apps/web/components/notebook-product-workspace.jsx", "NotebookProductWorkspace", {
    notebookId: "notebook-001",
    getSession: async () => { calls.session += 1; return { password_change_required: true }; },
    getSelectedNotebook: async () => { calls.get += 1; throw new Error("MUST_NOT_BE_CALLED"); },
    getSelectedContext: async () => { calls.context += 1; throw new Error("MUST_NOT_BE_CALLED"); },
    navigate: (path) => redirects.push(path),
  }, ".restricted-selected-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    assert.deepEqual(calls, { session: 1, get: 0, context: 0 });
    assert.deepEqual(redirects, ["/password-change"]);
  } finally { await view.cleanup(); }
});

function s42SelectedContext() {
  return { notebook_id:'notebook-s42', sources:[], knowledge_context_ids:[], conversation_thread_ids:['thread-s42'],
    studio_output_ids:[], output_version_ids:[], generation_settings_ids:[], source_deletion_requests:[],
    conversation:{ conversation_thread_id:'thread-s42', answer:{ run_id:'run-previous', run_result_id:'result-previous',
      answer:'S42 이전 정상 답변', insufficient:false, citations:[] } } };
}

const s42Session = (values = {}) => ({ user_id:'user-s42', session_id:'session-s42', tenant_id:'tenant-s42',
  workspace_id:'workspace-s42', password_change_required:false, ...values });

test('S42 실패 Run 복귀는 검증된 실패 안내와 기존 정상 대화를 함께 보존한다', async () => {
  const calls = [];
  const view = await render('apps/web/components/notebook-product-workspace.jsx','NotebookProductWorkspace',{
    notebookId:'notebook-s42', getLocationSearch:()=>'?run_id=run-failed',
    getSession:async()=>s42Session(),
    getSelectedNotebook:async()=>({ data:{ title:'S42 QA Notebook' } }),
    getSelectedContext:async(workspace,notebook,options)=>{ calls.push(options.runId ?? null);
      if(options.runId) throw new Error('QUESTION_FAILED'); return { data:s42SelectedContext() }; },
  },'.s42-run-target-');
  try {
    await view.act(async()=>{ await new Promise(resolve=>setTimeout(resolve,0)); });
    assert.deepEqual(calls,['run-failed',null]);
    assert.match(view.container.textContent,/선택한 질문 처리가 실패했습니다/u);
    assert.match(view.container.textContent,/S42 이전 정상 답변/u);
  } finally { await view.cleanup(); }
});

test('S42 Job 복귀는 서버 selector 검증 뒤 같은 Notebook의 정확 Job만 표시한다', async () => {
  const calls=[];
  const view=await render('apps/web/components/notebook-product-workspace.jsx','NotebookProductWorkspace',{
    notebookId:'notebook-s42', getLocationSearch:()=>'?studio_job_id=job-selected',
    getSession:async()=>s42Session(),
    getSelectedNotebook:async()=>({ data:{ title:'S42 QA Notebook' } }),
    getSelectedContext:async(workspace,notebook,options)=>{ calls.push(['context',options.studioJobId]); return {data:s42SelectedContext()}; },
    getSelectedJob:async(workspace,job,options)=>{ calls.push(['job',workspace,job,options.notebookId]);
      return {job_id:job,status:'failed',error_code:'INTERNAL_SECRET_MUST_NOT_RENDER'}; },
  },'.s42-job-target-');
  try {
    await view.act(async()=>{ await new Promise(resolve=>setTimeout(resolve,0)); });
    assert.deepEqual(calls,[['context','job-selected'],['job','workspace-s42','job-selected','notebook-s42']]);
    assert.match(view.container.textContent,/선택한 Studio 작업이 실패했습니다/u);
    assert.doesNotMatch(view.container.textContent,/INTERNAL_SECRET_MUST_NOT_RENDER/u);
    assert.match(view.container.textContent,/S42 이전 정상 답변/u);
  } finally { await view.cleanup(); }
});

test('S42 pagehide 뒤 늦은 selector 응답은 보호 화면을 다시 표시하지 않는다', async()=>{
  let resolveContext; const held = new Promise(resolve=>{resolveContext=resolve;});
  const view=await render('apps/web/components/notebook-product-workspace.jsx','NotebookProductWorkspace',{
    notebookId:'notebook-s42', getLocationSearch:()=>'?run_id=run-previous',
    getSession:async()=>s42Session(),
    getSelectedNotebook:async()=>({data:{title:'S42 늦은 응답'}}), getSelectedContext:async()=>held,
  },'.s42-late-target-');
  try {
    await view.act(async()=>{ view.dom.document.dispatchEvent(new MinimalEvent('pagehide')); resolveContext({data:s42SelectedContext()}); await held; });
    assert.doesNotMatch(view.container.textContent,/S42 이전 정상 답변/u);
    assert.equal(findElements(view.container,node=>node.getAttribute?.('data-session-validated')==='true').length,0);
  } finally { await view.cleanup(); }
});

test('S42 지연 selector 응답은 다른 탭에서 바뀐 세션의 화면에 이전 본문을 표시하지 않는다', async()=>{
  let current=s42Session(), resolveContext;
  const held=new Promise(resolve=>{resolveContext=resolve;});
  const calls=[];
  const view=await render('apps/web/components/notebook-product-workspace.jsx','NotebookProductWorkspace',{
    notebookId:'notebook-s42', getLocationSearch:()=>'?run_id=run-previous',
    getSession:async()=>{calls.push(current.session_id);return {...current};},
    getSelectedNotebook:async()=>({data:{title:'S42 세션 결속'}}), getSelectedContext:async()=>held,
  },'.s42-session-target-');
  try {
    current=s42Session({user_id:'other-user',session_id:'other-session'});
    await view.act(async()=>{resolveContext({data:s42SelectedContext()});await held;});
    assert.deepEqual(calls,['session-s42','other-session']);
    assert.doesNotMatch(view.container.textContent,/S42 이전 정상 답변/u);
    assert.equal(findElements(view.container,node=>node.getAttribute?.('data-session-validated')==='true').length,0);
  } finally {await view.cleanup();}
});

for (const event of ['focus', 'visibilitychange']) test(`S42 ${event} 복귀는 이전 본문부터 숨기고 현재 세션을 재검증한다`, async()=>{
  let revalidate=false, release;
  const held=new Promise(resolve=>{release=resolve;}); let calls=0;
  const view=await render('apps/web/components/notebook-product-workspace.jsx','NotebookProductWorkspace',{
    notebookId:'notebook-s42', getLocationSearch:()=>'?run_id=run-previous',
    getSession:async()=>{calls+=1;return revalidate?held:s42Session();},
    getSelectedNotebook:async()=>({data:{title:'S42 현재 세션'}}),
    getSelectedContext:async()=>{if(revalidate)throw Error('NOTEBOOK_NOT_FOUND');return {data:s42SelectedContext()};},
  },'.s42-focus-target-');
  try {
    await view.act(async()=>{await Promise.resolve();});
    assert.match(view.container.textContent,/S42 이전 정상 답변/u); assert.equal(calls,2);
    revalidate=true;
    await view.act(async()=>{view.dom.document.dispatchEvent(new MinimalEvent(event));});
    assert.doesNotMatch(view.container.textContent,/S42 이전 정상 답변/u); assert.equal(calls,3);
    await view.act(async()=>{release(s42Session({user_id:'other-user',session_id:'other-session'}));await held;});
    assert.doesNotMatch(view.container.textContent,/S42 이전 정상 답변/u);
  } finally {release(s42Session());await view.cleanup();}
});

function s42TargetAdapter(sources = [], outputs = [], versions = []) {
  const unused = async()=>{throw Error('UNREQUESTED_WRITE');};
  return { listSources:async()=>sources, listKnowledgePackages:async()=>[], listConnectors:async()=>[],
    listStudioOutputs:async()=>outputs, listProductStudioOutputs:async()=>({outputs,studioLocks:[]}),
    listStudioVersions:async()=>versions, uploadPdf:unused, getProcessingStatus:unused,
    askQuestion:unused, createReport:unused, citationUrl:()=>'/safe-citation' };
}

test('S42 Source 알림은 첫 자료 대신 지정한 현재 SourceVersion을 선택한다',async()=>{
  const sources=['other','wanted'].map(id=>({source_id:`source-${id}`,source_version_id:`version-${id}`,
    filename:`S42 QA ${id}`,source_state:'ready',processing_state:'completed',job_state:'completed'}));
  const data={...s42SelectedContext(),conversation:null,conversation_thread_ids:[],
    sources:sources.map(item=>({source_id:item.source_id,source_version_id:item.source_version_id}))};
  const calls=[];const base=s42TargetAdapter(sources);
  const view=await render('apps/web/components/notebook-product-workspace.jsx','NotebookProductWorkspace',{
    notebookId:'notebook-s42',getLocationSearch:()=>'?source_version_id=version-wanted',getSession:async()=>s42Session(),
    getSelectedNotebook:async()=>({data:{title:'S42 원 자료'}}),getSelectedContext:async()=>({data}),
    getTargetSources:async(workspace,options)=>{calls.push([workspace,options.notebookId]);return sources;},
    createWorkspaceAdapter:()=>base,
  },'.s42-source-target-');
  try {
    await view.act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
    assert.deepEqual(calls,[['workspace-s42','notebook-s42']]);
    const pressed=findElements(view.container,node=>node.tagName==='BUTTON'&&node.getAttribute?.('aria-pressed')==='true');
    assert(pressed.some(node=>node.textContent.includes('S42 QA wanted')),'정확 Version 행을 선택한다');
    assert(!pressed.some(node=>node.textContent.includes('S42 QA other')),'다른 자료 fallback 금지');
  }finally{await view.cleanup();}
});

test('S42 Studio 알림은 최신 버전 대신 지정한 과거 OutputVersion 상세를 연다',async()=>{
  const sources=['first','next'].map(id=>({source_id:`source-${id}`,source_version_id:`version-source-${id}`,
    filename:`S42 자료 ${id}`,source_state:'ready',processing_state:'completed',job_state:'completed'}));
  const output={studio_output_id:'output-s42',output_type:'comparison_table',title:'S42 QA 비교표',output_version_id:'version-new',status:'draft'};
  const versions=['new','wanted'].map(id=>({output_version_id:`version-${id}`,status:'draft',output_format:'csv',
    content_version:id==='wanted'?1:2,content:{rows:[{key:`S42 본문 ${id}`,baseline:'기준',current:'합성 QA',state:'same',evidence:[]}]}}));
  const data={...s42SelectedContext(),conversation:null,conversation_thread_ids:[],studio_output_ids:['output-s42'],
    sources:sources.map(item=>({source_id:item.source_id,source_version_id:item.source_version_id})),
    output_version_ids:['version-new','version-wanted']};
  const calls=[];const base=s42TargetAdapter(sources, [output], versions);
  const view=await render('apps/web/components/notebook-product-workspace.jsx','NotebookProductWorkspace',{
    notebookId:'notebook-s42',getLocationSearch:()=>'?output_version_id=version-wanted',getSession:async()=>s42Session(),
    getSelectedNotebook:async()=>({data:{title:'S42 산출물'}}),getSelectedContext:async()=>({data}),
    getTargetOutputs:async()=>({outputs:[output],studioLocks:[]}),
    getTargetVersions:async(workspace,outputId,options)=>{calls.push([workspace,outputId,options.notebookId]);return versions;},
    createWorkspaceAdapter:()=>base,
  },'.s42-version-target-');
  try {
    await view.act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
    assert.deepEqual(calls,[['workspace-s42','output-s42','notebook-s42']]);
    assert.match(view.container.textContent,/S42 본문 wanted/u);
    assert.doesNotMatch(view.container.textContent,/S42 본문 new/u);
    await view.act(async()=>{findElements(view.container,node=>node.tagName==='BUTTON'&&node.textContent.includes('S42 자료 next'))[0].dispatchEvent(new MinimalEvent('click'));await Promise.resolve();});
    assert.doesNotMatch(view.container.textContent,/S42 본문 wanted/u,'다른 자료를 명시 선택한 뒤 예전 알림 상세를 다시 강제하지 않는다');
  }finally{await view.cleanup();}
});

for (const query of ['run_id=a&run_id=b','run_id=a&studio_job_id=b',
  'source_version_id=a&output_version_id=b','output_version_id=','source_version_id=a%0A']) {
  test(`S42 잘못된 목적 query ${query}는 조회 전에 거부한다`,async()=>{
    let calls=0;
    const view=await render('apps/web/components/notebook-product-workspace.jsx','NotebookProductWorkspace',{
      notebookId:'notebook-s42',getLocationSearch:()=>'?'+query,getSession:async()=>{calls+=1;return s42Session();},
      getSelectedNotebook:async()=>{calls+=1;},getSelectedContext:async()=>{calls+=1;},
    },'.s42-invalid-target-');
    try {assert.equal(calls,0);assert.match(view.container.textContent,/NOTEBOOK_CONTEXT_INVALID/u);}
    finally {await view.cleanup();}
  });
}

for (const target of ['source_version_id','output_version_id']) test(`S42 현재 접근에서 사라진 ${target}는 다른 자료나 최신 버전으로 대체하지 않는다`,async()=>{
  const data={...s42SelectedContext(),sources:[{source_id:'source-wanted',source_version_id:'version-wanted'}],
    studio_output_ids:['output-s42'],output_version_ids:['version-wanted']};
  let sourceCalls=0,versionCalls=0;
  const view=await render('apps/web/components/notebook-product-workspace.jsx','NotebookProductWorkspace',{
    notebookId:'notebook-s42',getLocationSearch:()=>`?${target}=version-wanted`,getSession:async()=>s42Session(),
    getSelectedNotebook:async()=>({data:{title:'S42 거부'}}),getSelectedContext:async()=>({data}),
    getTargetSources:async()=>{sourceCalls+=1;return [];},
    getTargetOutputs:async()=>({outputs:[{studio_output_id:'output-s42'}],studioLocks:[]}),
    getTargetVersions:async()=>{versionCalls+=1;return [{output_version_id:'other-version'}];},
  },'.s42-denied-target-');
  try {
    assert.match(view.container.textContent,/NOTEBOOK_NOT_FOUND/u);
    assert.doesNotMatch(view.container.textContent,/S42 이전 정상 답변/u);
    assert.equal(sourceCalls,target==='source_version_id'?1:0);assert.equal(versionCalls,target==='output_version_id'?1:0);
  }finally{await view.cleanup();}
});

test("admin console은 안전한 loading·empty·error 상태만 표시한다", async () => {
  let releaseSession;
  const heldSession = new Promise((resolve) => { releaseSession = resolve; });
  const loading = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => heldSession, getUsers: async () => [],
  }, ".admin-loading-");
  try {
    assert.match(loading.container.textContent, /사용자 목록을 불러오는 중/u);
    const loadingStatus = findElements(loading.container, (node) => node.getAttribute?.("role") === "status")[0];
    assert.equal(loadingStatus.parentNode.hidden, false);
    await loading.act(async () => { releaseSession({ password_change_required: false, is_system_admin: true }); await heldSession; await Promise.resolve(); });
    assert.match(loading.container.textContent, /조건에 맞는 사용자가 없습니다/u);
  } finally { await loading.cleanup(); }

  const failed = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => { throw new Error("database host secret"); },
  }, ".admin-error-");
  try {
    await failed.act(async () => { await Promise.resolve(); });
    assert.match(failed.container.textContent, /ADMIN_USERS_UNAVAILABLE/u);
    assert.doesNotMatch(failed.container.textContent, /database host secret/u);
  } finally { await failed.cleanup(); }
});

test("admin console은 검색·필터·보호 표시와 상태 변경 double-submit 안전성을 제공한다", async () => {
  let changes = 0; let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const users = [
    { user_id: "admin", login_id: "admin", email: null, has_email: false, state: "active", protected: true },
    { user_id: "user-1", login_id: "person", email: "person@example.test", has_email: true, state: "active", protected: false },
  ];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    setUserState: async () => { changes += 1; await pending; return { ...users[1], state: "suspended" }; },
  }, ".admin-console-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    assert.match(view.container.textContent, /admin.*보호된 시스템 관리자/su);
    assert.match(view.container.textContent, /person@example\.test/u);
    assert.match(view.container.textContent, /admin.*이메일 없음/su);
    const protectedButton = findElements(userRows(view.container)[0], (node) => node.tagName === "INPUT")[0];
    assert.equal(protectedButton.disabled, true);
    const searchInput = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "search")[0];
    assert.equal(reactProps(searchInput).placeholder, "로그인 ID, 사용자 ID 또는 이메일");
    await view.act(async () => { reactProps(searchInput).onChange({ target: { value: "person@example.test" } }); });
    assert.doesNotMatch(view.container.textContent, /보호된 시스템 관리자/u);
    assert.match(view.container.textContent, /person@example\.test/u);
    await openUser(view); await selectValue(view, "계정 상태", "suspended");
    const action = findElements(view.container, (node) => node.tagName === "FORM")[0];
    await view.act(async () => { action.dispatchEvent(new MinimalEvent("submit")); action.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
    assert.equal(changes, 1);
    await view.act(async () => { release(); await pending; await Promise.resolve(); });
    assert.match(userRows(view.container)[0].textContent, /중지/u);
  } finally { await view.cleanup(); }
});

test("admin console은 사용자 계정 관리 표와 일괄 작업 도구를 표시한다", async () => {
  const users = [
    { user_id: "admin", login_id: "admin", email: "admin@example.test", has_email: true, state: "active", protected: true },
    { user_id: "user-1", login_id: "cyhuh", email: "cyhuh@example.test", has_email: true, state: "active", protected: false },
  ];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
  }, ".admin-table-layout-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    for (const label of ["선택 0건", "선택 삭제", "새로고침", "사용자 등록", "조회", "사용자", "이메일", "상태", "역할"]) {
      assert.match(view.container.textContent, new RegExp(label, "u"));
    }
    assert.match(view.container.textContent, /2명의 계정을 조회했습니다/u);
    assert.match(view.container.textContent, /관리자/u);
    assert.match(view.container.textContent, /일반 사용자/u);
    assert.doesNotMatch(view.container.textContent, /user-1/u);
    assert.doesNotMatch(view.container.textContent, /전문가/u);
    assert.match(view.container.textContent, /관리자/u);
    assert.deepEqual(userRows(view.container).map((row) => reactProps(row).tabIndex), [0, 0]);
  } finally { await view.cleanup(); }
});

for (const kind of ["create", "edit"]) {
  test(`admin console ${kind} 폼은 올바른 제출 문구와 기존 저장 경로를 사용한다`, async () => {
    const user = { user_id: "user-1", login_id: "qa-label", email: "before@example.test", has_email: true, state: "active", protected: false };
    const calls = [];
    const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
      getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
      getUsers: async () => kind === "create" ? [] : [user],
      registerUser: async (payload, options) => { calls.push({ kind: "create", payload, options }); return { ...user, login_id: payload.login_id, email: payload.email, state: "pending_approval" }; },
      editUser: async (userId, payload, options) => { calls.push({ kind: "edit", userId, payload, options }); return { ...user, ...payload }; },
      setUserState: async () => { assert.fail("이메일 수정은 상태 변경을 호출하지 않는다"); },
    }, `.admin-submit-label-${kind}-`);
    try {
      if (kind === "create") await view.act(async () => { buttonByText(view.container, "사용자 등록").dispatchEvent(new MinimalEvent("click")); });
      else await openUser(view);
      const form = findElements(view.container, (node) => node.tagName === "FORM")[0];
      assert.equal(findElements(form, (node) => node.tagName === "H2")[0].textContent, kind === "create" ? "사용자 등록" : "사용자 수정");
      const submit = findElements(form, (node) => node.tagName === "BUTTON" && node.getAttribute("type") === "submit")[0];
      assert.equal(submit.textContent, kind === "create" ? "등록" : "수정");
      const values = kind === "create" ? ["qa-label", "after@example.test", "fixture-password-only"] : ["after@example.test"];
      const inputs = findElements(form, (node) => node.tagName === "INPUT");
      for (let index = 0; index < inputs.length; index += 1) {
        await view.act(async () => { reactProps(inputs[index]).onChange({ target: { value: values[index] } }); });
      }
      await view.act(async () => { form.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
      assert.equal(calls.length, 1);
      assert.equal(calls[0].kind, kind);
      assert.match(calls[0].options.idempotencyKey, new RegExp(`^admin-${kind === "create" ? "create" : "update"}-`, "u"));
      if (kind === "create") {
        assert.deepEqual(calls[0].payload, { login_id: values[0], email: values[1], initial_password: values[2] });
      } else {
        assert.equal(calls[0].userId, user.user_id);
        assert.deepEqual(calls[0].payload, { email: values[0] });
      }
      assert.equal(findElements(view.container, (node) => node.tagName === "FORM").length, 0);
      assert.match(view.container.textContent, /after@example\.test/u);
    } finally { await view.cleanup(); }
  });
}

test("admin console 등록은 실제 client helper로 서버의 세 필드만 POST하고 승인 대기 응답을 표시한다", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  let view;
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options, body: JSON.parse(options.body) });
    return new Response(JSON.stringify({
      data: { user: { user_id: "qa-created", login_id: "qa-create-contract", email: "create@example.test", has_email: true, state: "pending_approval", protected: false }, replayed: false },
      meta: { trace_id: "qa-create-contract" },
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
      getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
      getUsers: async () => [],
    }, ".admin-create-payload-");
    await view.act(async () => { buttonByText(view.container, "사용자 등록").dispatchEvent(new MinimalEvent("click")); });
    const form = findElements(view.container, (node) => node.tagName === "FORM")[0];
    assert.equal(findElements(form, (node) => node.tagName === "BUTTON" && node.getAttribute("type") === "submit")[0].textContent, "등록");
    const inputs = findElements(form, (node) => node.tagName === "INPUT");
    const values = ["qa-create-contract", "create@example.test", "synthetic-password-only"];
    for (let index = 0; index < inputs.length; index += 1) {
      await view.act(async () => { reactProps(inputs[index]).onChange({ target: { value: values[index] } }); });
    }
    await view.act(async () => { form.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/bff/api/admin/users");
    assert.equal(requests[0].options.method, "POST");
    assert.equal(requests[0].options.credentials, "same-origin");
    assert.match(requests[0].options.headers["Idempotency-Key"], /^admin-create-/u);
    assert.deepEqual(Object.keys(requests[0].body).sort(), ["email", "initial_password", "login_id"]);
    assert.deepEqual(requests[0].body, { login_id: values[0], email: values[1], initial_password: values[2] });
    assert.equal(findElements(view.container, (node) => node.tagName === "FORM").length, 0);
    assert.match(view.container.textContent, /create@example\.test/u);
    assert.match(view.container.textContent, /승인 대기/u);
    assert.doesNotMatch(view.container.textContent, /synthetic-password-only/u);
  } finally {
    try { if (view) await view.cleanup(); } finally { globalThis.fetch = originalFetch; }
  }
});

test("admin console은 일반 사용자에게 비밀번호 초기화 메일 action과 성공 상태를 표시한다", async () => {
  let resets = 0;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => [{ user_id: "user-1", login_id: "cyhuh", email: "cyhuh@example.test", has_email: true, state: "active", protected: false }],
    resetPassword: async () => { resets += 1; return { status: "accepted", replayed: false }; },
  }, ".admin-password-reset-");
  const previousConfirm = globalThis.window.confirm;
  globalThis.window.confirm = () => true;
  try {
    await view.act(async () => { await Promise.resolve(); });
    await openUser(view);
    await view.act(async () => { buttonByText(view.container, "비밀번호 초기화").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(resets, 1);
    assert.match(view.container.textContent, /비밀번호 초기화 메일을 발송했습니다/u);
  } finally {
    globalThis.window.confirm = previousConfirm;
    await view.cleanup();
  }
});

test("admin console의 복수 삭제는 확인 1회로 비보호 계정만 삭제한다", async () => {
  const users = [
    { user_id: "admin", login_id: "admin", email: null, has_email: false, state: "active", protected: true },
    { user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false },
    { user_id: "user-2", login_id: "two", email: "two@example.test", has_email: true, state: "active", protected: false },
  ];
  const removed = []; const confirms = [];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    removeUser: async (id) => { removed.push(id); },
  }, ".admin-bulk-delete-");
  const previousConfirm = globalThis.window.confirm;
  globalThis.window.confirm = (message) => { confirms.push(message); return true; };
  try {
    await view.act(async () => { await Promise.resolve(); });
    const selectAll = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "checkbox" && reactProps(node)?.["aria-label"] === "전체 사용자 선택")[0];
    await view.act(async () => { reactProps(selectAll).onChange({ target: { checked: true } }); });
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(confirms, ["2명의 계정을 삭제하시겠습니까?"]);
    assert.deepEqual(removed, ["user-1", "user-2"]);
    assert.match(view.container.textContent, /보호된 시스템 관리자/u);
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console의 복수 삭제 취소는 삭제 요청을 보내지 않는다", async () => {
  let removed = 0;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => [{ user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false }],
    removeUser: async () => { removed += 1; },
  }, ".admin-bulk-cancel-");
  const previousConfirm = globalThis.window.confirm;
  globalThis.window.confirm = () => false;
  try {
    await view.act(async () => { await Promise.resolve(); });
    const selectAll = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "checkbox" && reactProps(node)?.["aria-label"] === "전체 사용자 선택")[0];
    await view.act(async () => { reactProps(selectAll).onChange({ target: { checked: true } }); });
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(removed, 0);
    assert.match(view.container.textContent, /선택 1건/u);
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console의 복수 삭제 실패는 후속 요청을 중단한다", async () => {
  const users = ["one", "two"].map((name, index) => ({ user_id: `user-${index + 1}`, login_id: name, email: `${name}@example.test`, has_email: true, state: "active", protected: false }));
  const removed = [];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    removeUser: async (id) => { removed.push(id); throw new Error("ADMIN_USER_DELETE_FAILED"); },
  }, ".admin-bulk-failure-");
  const previousConfirm = globalThis.window.confirm;
  globalThis.window.confirm = () => true;
  try {
    await view.act(async () => { await Promise.resolve(); });
    const selectAll = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "checkbox" && reactProps(node)?.["aria-label"] === "전체 사용자 선택")[0];
    await view.act(async () => { reactProps(selectAll).onChange({ target: { checked: true } }); });
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(removed, ["user-1"]);
    assert.match(view.container.textContent, /ADMIN_USER_DELETE_FAILED/u);
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console의 복수 삭제는 부분 성공 뒤 실패·미처리 계정만 선택해 남긴다", async () => {
  const users = ["one", "two", "three"].map((name, index) => ({ user_id: `user-${index + 1}`, login_id: name, email: `${name}@example.test`, has_email: true, state: "active", protected: false }));
  const removed = [];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    removeUser: async (id) => { removed.push(id); if (id === "user-2") throw new Error("ADMIN_USER_DELETE_FAILED"); },
  }, ".admin-bulk-partial-failure-");
  const previousConfirm = globalThis.window.confirm;
  globalThis.window.confirm = () => true;
  try {
    await view.act(async () => { await Promise.resolve(); });
    const selectAll = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.["aria-label"] === "전체 사용자 선택")[0];
    await view.act(async () => { reactProps(selectAll).onChange({ target: { checked: true } }); });
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(removed, ["user-1", "user-2"]);
    const selectedUsers = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "checkbox" && reactProps(node)?.["aria-label"] !== "전체 사용자 선택" && reactProps(node)?.checked)
      .map((node) => reactProps(node)["aria-label"]);
    assert.deepEqual(selectedUsers, ["two 선택", "three 선택"]);
    assert.match(view.container.textContent, /선택 2건/u);
    assert.match(view.container.textContent, /ADMIN_USER_DELETE_FAILED/u);
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console의 복수 삭제는 요청 대기 중 재클릭해도 같은 계정을 한 번만 삭제한다", async () => {
  const users = ["one", "two"].map((name, index) => ({ user_id: `user-${index + 1}`, login_id: name, email: `${name}@example.test`, has_email: true, state: "active", protected: false }));
  const removed = []; let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    removeUser: async (id) => { removed.push(id); if (id === "user-1") await pending; },
  }, ".admin-bulk-inflight-");
  const previousConfirm = globalThis.window.confirm;
  let confirms = 0; globalThis.window.confirm = () => { confirms += 1; return true; };
  try {
    await view.act(async () => { await Promise.resolve(); });
    const selectAll = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.["aria-label"] === "전체 사용자 선택")[0];
    await view.act(async () => { reactProps(selectAll).onChange({ target: { checked: true } }); });
    const bulk = buttonByText(view.container, "선택 삭제");
    await view.act(async () => { bulk.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(buttonByText(view.container, "선택 삭제").disabled, true);
    await view.act(async () => { reactProps(bulk).onClick(); await Promise.resolve(); });
    assert.deepEqual(removed, ["user-1"]);
    assert.equal(confirms, 1);
    await view.act(async () => { release(); await pending; await Promise.resolve(); });
    assert.deepEqual(removed, ["user-1", "user-2"]);
  } finally { release(); globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console의 단일 선택 일괄 삭제는 요청 대기 중 재클릭을 막고 실패 후 잠금을 푼다", async () => {
  const user = { user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false };
  let rejectPending; const removed = [];
  const pending = new Promise((resolve, reject) => { rejectPending = reject; });
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => [user],
    removeUser: async (id) => { removed.push(id); if (removed.length === 1) await pending; },
  }, ".admin-single-inflight-");
  const previousConfirm = globalThis.window.confirm;
  let confirms = 0; globalThis.window.confirm = () => { confirms += 1; return true; };
  try {
    await view.act(async () => { await Promise.resolve(); });
    await selectAllUsers(view);
    const single = buttonByText(view.container, "선택 삭제");
    await view.act(async () => { single.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(buttonByText(view.container, "선택 삭제").disabled, true);
    await view.act(async () => { reactProps(single).onClick(); await Promise.resolve(); });
    assert.deepEqual(removed, ["user-1"]);
    assert.equal(confirms, 1);
    await view.act(async () => { rejectPending(new Error("ADMIN_USER_DELETE_FAILED")); await pending.catch(() => {}); await Promise.resolve(); });
    assert.equal(buttonByText(view.container, "선택 삭제").disabled, false);
    assert.match(view.container.textContent, /ADMIN_USER_DELETE_FAILED/u);
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(removed, ["user-1", "user-1"]);
  } finally { rejectPending(new Error("TEST_CLEANUP")); globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console의 복수 삭제 실패는 선택을 유지하고 잠금을 해제한다", async () => {
  const user = { user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false };
  let rejectPending; const removed = [];
  const pending = new Promise((resolve, reject) => { rejectPending = reject; });
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => [user],
    removeUser: async (id) => { removed.push(id); if (removed.length === 1) await pending; },
  }, ".admin-bulk-retry-");
  const previousConfirm = globalThis.window.confirm;
  globalThis.window.confirm = () => true;
  try {
    await view.act(async () => { await Promise.resolve(); });
    const selectAll = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.["aria-label"] === "전체 사용자 선택")[0];
    await view.act(async () => { reactProps(selectAll).onChange({ target: { checked: true } }); });
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(buttonByText(view.container, "선택 삭제").disabled, true);
    await view.act(async () => { rejectPending(new Error("ADMIN_USER_DELETE_FAILED")); await pending.catch(() => {}); await Promise.resolve(); });
    assert.match(view.container.textContent, /선택 1건/u);
    assert.equal(buttonByText(view.container, "선택 삭제").disabled, false);
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(removed, ["user-1", "user-1"]);
  } finally { rejectPending(new Error("TEST_CLEANUP")); globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console은 membership 역할을 추정하거나 편집 가능한 역할처럼 표시하지 않는다", async () => {
  const users = [
    { user_id: "admin", login_id: "admin", email: "admin@example.test", has_email: true, state: "active", protected: true },
    { user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false },
  ];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    getTenants: async () => [],
  }, ".admin-membership-role-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    const roleCells = findElements(view.container, (node) => node.getAttribute?.("class") === "admin-user-role-list");
    assert.deepEqual(roleCells.map((node) => node.textContent), [
      "시스템 관리자",
      "조직·Workspace 선택 필요",
    ]);
    await openUser(view, 1);
    assert.equal(findElements(view.container, (node) => node.getAttribute?.("class") === "admin-user-role-fields").length, 0);
    assert.doesNotMatch(view.container.textContent, /전문가|역할 정책은 현재 계정 권한 계약/u);
    assert.match(view.container.textContent, /조직·Workspace 선택 필요/u);
  } finally { await view.cleanup(); }
});

test("역할 목록·상세·변경의 인증 만료는 기존 사용자와 역할을 즉시 숨기고 로그인으로 이동한다", async () => {
  const user = { user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false };
  const membership = { tenant: null, workspaces: [{ workspace_id: "workspace-a", role: "viewer", state: "active", version: 2 }] };
  for (const stage of ["tenants", "memberships", "patch", "reread"]) {
    const redirects = []; let reads = 0;
    const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
      getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
      getUsers: async () => [user],
      getTenants: async () => { if (stage === "tenants") throw new Error("AUTHENTICATION_REQUIRED"); return [{ tenant_id: "tenant-a", name: "Tenant A" }]; },
      getMemberships: async () => { reads += 1; if (stage === "memberships" || (stage === "reread" && reads > 1)) throw new Error("AUTHENTICATION_REQUIRED"); return membership; },
      setMembershipRole: async () => { if (stage === "patch") throw new Error("AUTHENTICATION_REQUIRED"); return { version: 2 }; },
      navigate: (path) => redirects.push(path),
    }, `.admin-role-auth-${stage}-`);
    const previousConfirm = globalThis.window.confirm; globalThis.window.confirm = () => true;
    try {
      await view.act(async () => { await Promise.resolve(); });
      assert.match(view.container.textContent, /one@example\.test/u);
      await openUser(view);
      const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
      if (stage !== "tenants") await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
      if (stage === "patch" || stage === "reread") {
        await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
        await selectValue(view, "변경할 역할", "editor");
        await view.act(async () => { reactProps(select("변경 사유")).onChange({ target: { value: "ACCESS_REVIEW" } }); });
        await submitUser(view);
      }
      assert.deepEqual(redirects, ["/"], stage);
      const protectedArea = findElements(view.container, (node) => node.getAttribute?.("data-session-validated") !== null)[0];
      assert.equal(protectedArea.hidden, true, stage);
      assert.doesNotMatch(protectedArea.textContent, /one@example\.test|workspace-a|viewer/u, stage);
    } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
  }
});

test("역할 저장 중 닫기를 막고 저장 완료 후 서버 재조회 결과를 보여준다", async () => {
  const user = { user_id: "user-1", login_id: "one", email: null, has_email: false, state: "active", protected: false };
  let releaseWrite; let reads = 0;
  const writePending = new Promise((resolve) => { releaseWrite = resolve; });
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
    getUsers: async () => [user],
    getTenants: async () => [{ tenant_id: "tenant-a", name: "Tenant A" }],
    getMemberships: async () => { reads += 1; return { tenant: null, workspaces: [{ workspace_id: "workspace-a", role: reads === 1 ? "viewer" : "editor", state: "active", version: reads === 1 ? 2 : 3 }] }; },
    setMembershipRole: async () => { await writePending; return { version: 3 }; },
  }, ".admin-role-close-pending-");
  const previousConfirm = globalThis.window.confirm; globalThis.window.confirm = () => true;
  try {
    await view.act(async () => { await Promise.resolve(); });
    await openUser(view);
    assert.equal(reactProps(findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "email")[0]).required, false,
      "변경하지 않은 빈 이메일은 역할만 수정하는 브라우저 submit을 막지 않는다");
    const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
    await view.act(async () => { reactProps(select("변경할 역할")).onChange({ target: { value: "editor" } }); reactProps(select("변경 사유")).onChange({ target: { value: "ACCESS_REVIEW" } }); });
    await submitUser(view);
    const close = buttonByText(view.container, "취소");
    assert.equal(close.disabled, true);
    await view.act(async () => { reactProps(close).onClick(); await Promise.resolve(); });
    assert.match(view.container.textContent, /처리 중…/u);
    await view.act(async () => { releaseWrite(); await writePending; await Promise.resolve(); });
    assert.equal(reads, 2);
    assert.match(userRows(view.container)[0].textContent, /마지막 조회.*편집자.*version 3/su);
  } finally { releaseWrite(); globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("사용자별 수정 진입 행은 대상 이름을 접근성 이름에 포함한다", async () => {
  const users = ["one", "two"].map((login_id) => ({ user_id: login_id, login_id, email: null, has_email: false, state: "active", protected: false }));
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
  }, ".admin-role-aria-label-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    const buttons = userRows(view.container);
    assert.deepEqual(buttons.map((row) => reactProps(row)["aria-label"]), ["one 사용자 수정", "two 사용자 수정"]);
    assert.equal(findElements(view.container, (node) => node.tagName === "BUTTON" && node.textContent === "역할 조회").length, 0);
  } finally { await view.cleanup(); }
});

test("admin console은 조직을 명시 선택한 뒤에만 유효 역할을 조회하고 확인 후 변경·재조회한다", async () => {
  const user = { user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false };
  const calls = []; let reads = 0;
  const membership = (role, version) => ({
    tenant: { tenant_id: "tenant-a", role: "organization_admin", state: "active", version: 1 },
    workspaces: [{ workspace_id: "workspace-a", role, state: "active", version }],
  });
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
    getUsers: async () => [user],
    getTenants: async () => [{ tenant_id: "tenant-a", name: "Tenant A" }, { tenant_id: "tenant-b", name: "Tenant B" }],
    getMemberships: async (tenantId, userId) => { calls.push(["read", tenantId, userId]); reads += 1; return membership(reads === 1 ? "viewer" : "editor", reads === 1 ? 2 : 3); },
    setMembershipRole: async (...args) => { calls.push(["write", ...args]); return { version: 3 }; },
  }, ".admin-role-flow-");
  const previousConfirm = globalThis.window.confirm; globalThis.window.confirm = () => true;
  try {
    await view.act(async () => { await Promise.resolve(); });
    assert.match(view.container.textContent, /조직·Workspace 선택 필요/u);
    await openUser(view);
    assert.deepEqual(calls, []);
    assert.match(view.container.textContent, /조직·Workspace 선택 필요/u);
    assert.match(view.container.textContent, /시스템 관리자 여부.*미조회/u);
    const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
    assert.deepEqual(calls, [["read", "tenant-a", "user-1"]]);
    assert.match(view.container.textContent, /organization_admin|조직 관리자/u);
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
    assert.match(view.container.textContent, /viewer|열람자/u);
    await view.act(async () => {
      reactProps(select("변경할 역할")).onChange({ target: { value: "editor" } });
      reactProps(select("변경 사유")).onChange({ target: { value: "ACCESS_REVIEW" } });
    });
    await submitUser(view);
    assert.equal(calls[1][0], "write");
    assert.deepEqual(calls[1].slice(1, 5), ["tenant-a", "workspace-a", "user-1", { role: "editor", expected_version: 2, reason: "ACCESS_REVIEW" }]);
    assert.deepEqual(calls[2], ["read", "tenant-a", "user-1"]);
    assert.match(view.container.textContent, /마지막 조회.*편집자/u);
    assert.match(view.container.textContent, /version 3/u);
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("대상 시스템 관리자 여부는 선택 조직의 성공 조회값만 표시하고 실패·대상 전환 때 숨긴다", async () => {
  const users = [
    { user_id: "one", login_id: "one", email: null, has_email: false, state: "active", protected: false },
    { user_id: "two", login_id: "two", email: null, has_email: false, state: "active", protected: true },
    { user_id: "three", login_id: "three", email: null, has_email: false, state: "active", protected: false },
  ];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    getTenants: async () => [{ tenant_id: "tenant-a", name: "Tenant A" }, { tenant_id: "tenant-b", name: "Tenant B" }],
    getMemberships: async (tenantId, userId) => {
      if (tenantId === "tenant-b") throw new Error("ADMIN_MEMBERSHIP_UNAVAILABLE");
      return { tenant: null, workspaces: [{ workspace_id: "workspace-a", role: "viewer", state: "active", version: 1 }],
        target_is_system_admin: userId === "one" };
    },
  }, ".admin-role-system-admin-projection-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    const buttons = userRows(view.container);
    const adminStatus = () => findElements(view.container, (node) => node.tagName === "P"
      && node.textContent.startsWith("시스템 관리자 여부 (별도 권한):"))[0]?.textContent;
    const selectTenant = async (tenantId) => {
      const select = findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === "조직 선택")[0];
      await view.act(async () => { reactProps(select).onChange({ target: { value: tenantId } }); await Promise.resolve(); });
    };
    await view.act(async () => { buttons[0].dispatchEvent(new MinimalEvent("dblclick")); await Promise.resolve(); });
    assert.equal(adminStatus(), "시스템 관리자 여부 (별도 권한): 미조회");
    await selectTenant("tenant-a");
    assert.equal(adminStatus(), "시스템 관리자 여부 (별도 권한): 예");
    await selectTenant("tenant-b");
    assert.equal(adminStatus(), "시스템 관리자 여부 (별도 권한): 미조회");
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await view.act(async () => { buttons[1].dispatchEvent(new MinimalEvent("dblclick")); await Promise.resolve(); });
    assert.equal(adminStatus(), undefined);
    assert.equal(selectByLabel(view, "조직 선택"), undefined);
    assert.match(view.container.textContent, /시스템 관리자 권한은 조직·Workspace 멤버십 역할과 별도입니다/u);
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await view.act(async () => { buttons[2].dispatchEvent(new MinimalEvent("dblclick")); await Promise.resolve(); });
    assert.equal(adminStatus(), "시스템 관리자 여부 (별도 권한): 미조회");
    await selectTenant("tenant-a");
    assert.equal(adminStatus(), "시스템 관리자 여부 (별도 권한): 아니요");
  } finally { await view.cleanup(); }
});

test("역할 저장 중·412 실패 후 작업공간 재선택은 과거 관리자 여부를 되살리지 않는다", async () => {
  const user = { user_id: "user-1", login_id: "one", email: null, has_email: false, state: "active", protected: false };
  let rejectWrite; let reads = 0;
  const pendingWrite = new Promise((resolve, reject) => { rejectWrite = reject; });
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
    getUsers: async () => [user],
    getTenants: async () => [{ tenant_id: "tenant-a", name: "Tenant A" }],
    getMemberships: async () => {
      reads += 1;
      return { tenant: null, workspaces: [{ workspace_id: "workspace-a", role: reads === 1 ? "viewer" : "reviewer", state: "active", version: reads === 1 ? 2 : 3 }],
        target_is_system_admin: true };
    },
    setMembershipRole: async () => pendingWrite,
  }, ".admin-role-status-stale-");
  const previousConfirm = globalThis.window.confirm; globalThis.window.confirm = () => true;
  try {
    await view.act(async () => { await Promise.resolve(); });
    await openUser(view);
    const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
    const adminStatus = () => findElements(view.container, (node) => node.tagName === "P"
      && node.textContent.startsWith("시스템 관리자 여부 (별도 권한):"))[0]?.textContent;
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
    assert.equal(adminStatus(), "시스템 관리자 여부 (별도 권한): 예");
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
    await view.act(async () => { reactProps(select("변경할 역할")).onChange({ target: { value: "editor" } }); reactProps(select("변경 사유")).onChange({ target: { value: "CORRECTION" } }); });
    await submitUser(view);
    assert.equal(adminStatus(), "시스템 관리자 여부 (별도 권한): 미조회");
    await view.act(async () => { rejectWrite(new Error("VERSION_CONFLICT")); await pendingWrite.catch(() => {}); await Promise.resolve(); });
    assert.equal(reads, 2);
    assert.equal(adminStatus(), "시스템 관리자 여부 (별도 권한): 미조회");
    assert.match(view.container.textContent, /최신.*reviewer.*version 3/su);
    assert.equal(reactProps(select("변경할 역할")).value, "editor");
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "" } }); });
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
    assert.equal(reads, 2);
    assert.equal(adminStatus(), "시스템 관리자 여부 (별도 권한): 미조회");
  } finally { rejectWrite(new Error("TEST_CLEANUP")); globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console은 412 후 입력을 보존하고 최신 역할을 비교하며 보호·자기 계정 변경을 막는다", async () => {
  const users = [
    { user_id: "admin", login_id: "admin", email: null, has_email: false, state: "active", protected: true },
    { user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false },
  ];
  let reads = 0; let writes = 0;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    getTenants: async () => [{ tenant_id: "tenant-a", name: "Tenant A" }],
    getMemberships: async () => { reads += 1; return { tenant: null, workspaces: [
      { workspace_id: "workspace-a", role: reads === 1 ? "viewer" : "reviewer", state: "active", version: reads === 1 ? 2 : 3 },
    ] }; },
    setMembershipRole: async () => { writes += 1; throw new Error("VERSION_CONFLICT"); },
  }, ".admin-role-conflict-");
  const previousConfirm = globalThis.window.confirm; globalThis.window.confirm = () => true;
  try {
    await view.act(async () => { await Promise.resolve(); });
    const roleButtons = userRows(view.container);
    await view.act(async () => { roleButtons[1].dispatchEvent(new MinimalEvent("dblclick")); await Promise.resolve(); });
    const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
    await view.act(async () => {
      reactProps(select("변경할 역할")).onChange({ target: { value: "editor" } });
      reactProps(select("변경 사유")).onChange({ target: { value: "CORRECTION" } });
    });
    await submitUser(view);
    assert.equal(writes, 1); assert.equal(reads, 2);
    assert.equal(reactProps(select("변경할 역할")).value, "editor");
    assert.equal(reactProps(select("변경 사유")).value, "CORRECTION");
    assert.match(view.container.textContent, /VERSION_CONFLICT/u);
    assert.match(view.container.textContent, /최신.*reviewer.*version 3/su);
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await view.act(async () => { roleButtons[0].dispatchEvent(new MinimalEvent("dblclick")); await Promise.resolve(); });
    assert.equal(select("조직 선택"), undefined);
    assert.equal(select("작업공간 선택"), undefined);
    assert.equal(buttonByText(view.container, "수정").disabled, true);
    await submitUser(view);
    assert.equal(writes, 1); assert.equal(reads, 2);
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console은 조직 전환의 오래된 응답을 숨기고 서버의 관리자 보호 거부를 안전하게 표시한다", async () => {
  const user = { user_id: "user-1", login_id: "one", email: null, has_email: false, state: "active", protected: false };
  let finishOldRead; let writes = 0;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
    getUsers: async () => [user],
    getTenants: async () => [{ tenant_id: "tenant-a", name: "Tenant A" }, { tenant_id: "tenant-b", name: "Tenant B" }],
    getMemberships: async (tenantId) => tenantId === "tenant-a"
      ? new Promise((resolve) => { finishOldRead = resolve; })
      : { tenant: null, workspaces: [{ workspace_id: "workspace-b", role: "viewer", state: "active", version: 2 }] },
    setMembershipRole: async () => { writes += 1; throw new Error("FORBIDDEN"); },
  }, ".admin-role-scope-");
  const previousConfirm = globalThis.window.confirm; globalThis.window.confirm = () => true;
  try {
    await view.act(async () => { await Promise.resolve(); });
    await openUser(view);
    const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-b" } }); await Promise.resolve(); });
    await view.act(async () => { finishOldRead({ tenant: null, workspaces: [{ workspace_id: "workspace-a", role: "editor", state: "active", version: 9 }] }); await Promise.resolve(); });
    assert.doesNotMatch(view.container.textContent, /workspace-a/u);
    assert.match(view.container.textContent, /workspace-b/u);
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-b" } }); });
    await selectValue(view, "변경할 역할", "editor");
    await view.act(async () => { reactProps(select("변경 사유")).onChange({ target: { value: "ACCESS_REVIEW" } }); });
    await submitUser(view);
    assert.equal(writes, 1);
    assert.match(view.container.textContent, /FORBIDDEN/u);
    assert.equal(reactProps(select("변경 사유")).value, "ACCESS_REVIEW");
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console의 단일 선택 일괄 삭제는 확인 취소와 보호 계정을 차단한다", async () => {
  const users = [
    { user_id: "admin", login_id: "admin", email: "admin@example.test", has_email: true, state: "active", protected: true },
    { user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false },
  ];
  const removed = []; const confirms = [];
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    removeUser: async (id) => { removed.push(id); },
  }, ".admin-single-delete-");
  const previousConfirm = globalThis.window.confirm;
  globalThis.window.confirm = (message) => { confirms.push(message); return confirms.length > 1; };
  try {
    await view.act(async () => { await Promise.resolve(); });
    assert.equal(findElements(view.container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제").length, 0);
    const protectedCheckbox = findElements(userRows(view.container)[0], (node) => node.tagName === "INPUT")[0];
    assert.equal(protectedCheckbox.disabled, true);
    await view.act(async () => { reactProps(protectedCheckbox).onChange({ target: { checked: true } }); });
    assert.match(view.container.textContent, /선택 0건/u);
    await selectAllUsers(view);
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(removed, []);
    await view.act(async () => { buttonByText(view.container, "선택 삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(confirms, ["1명의 계정을 삭제하시겠습니까?", "1명의 계정을 삭제하시겠습니까?"]);
    assert.deepEqual(removed, ["user-1"]);
    assert.match(view.container.textContent, /보호된 시스템 관리자/u);
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console의 reset은 확인 취소와 보호 계정에 요청을 보내지 않는다", async () => {
  const users = [
    { user_id: "admin", login_id: "admin", email: "admin@example.test", has_email: true, state: "active", protected: true },
    { user_id: "user-1", login_id: "one", email: "one@example.test", has_email: true, state: "active", protected: false },
  ];
  let resets = 0;
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
    resetPassword: async () => { resets += 1; },
  }, ".admin-reset-cancel-");
  const previousConfirm = globalThis.window.confirm;
  globalThis.window.confirm = () => false;
  try {
    await view.act(async () => { await Promise.resolve(); });
    await openUser(view, 1);
    assert.equal(findElements(view.container, (node) => node.tagName === "BUTTON" && node.textContent === "비밀번호 초기화").length, 1);
    await view.act(async () => { buttonByText(view.container, "비밀번호 초기화").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(resets, 0);
    await view.act(async () => { buttonByText(view.container, "취소").dispatchEvent(new MinimalEvent("click")); });
    await openUser(view, 0);
    const reset = buttonByText(view.container, "비밀번호 초기화");
    assert.equal(reset.disabled, true);
    await view.act(async () => { reactProps(reset).onClick(); await Promise.resolve(); });
    assert.equal(resets, 0);
    assert.doesNotMatch(view.container.textContent, /비밀번호 초기화 메일을 발송했습니다/u);
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console은 pending_email을 이메일 인증 대기로 표시하고 상태 변경을 허용하지 않는다", async () => {
  let changes = 0;
  const pendingEmailUser = { user_id: "user-pending", login_id: "pending-person", email: "pending@example.test", has_email: true, state: "pending_email", protected: false };
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ password_change_required: false, is_system_admin: true }),
    getUsers: async () => [pendingEmailUser],
    setUserState: async () => { changes += 1; return { ...pendingEmailUser, state: "active" }; },
  }, ".admin-pending-email-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    assert.match(view.container.textContent, /pending-person.*이메일 인증 대기/su);
    await openUser(view);
    const action = selectByLabel(view, "계정 상태");
    assert.equal(action.disabled, true);
    await selectValue(view, "계정 상태", "active");
    await submitUser(view);
    assert.equal(changes, 0);
  } finally { await view.cleanup(); }
});

test("강제 변경 화면은 확인 불일치와 double-submit을 막고 성공 시 로그인으로 복귀한다", async () => {
  let changes = 0; const redirects = []; let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const view = await render("apps/web/components/password-change-workspace.jsx", "PasswordChangeWorkspace", {
    getSession: async () => ({ password_change_required: true, is_system_admin: true }),
    changePassword: async () => { changes += 1; await pending; return { status: "password_changed" }; },
    navigate: (path) => redirects.push(path),
  }, ".password-change-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    const inputs = findElements(view.container, (node) => node.tagName === "INPUT");
    await view.act(async () => {
      reactProps(inputs[0]).onChange({ target: { value: "admin" } });
      reactProps(inputs[1]).onChange({ target: { value: "a strong replacement" } });
      reactProps(inputs[2]).onChange({ target: { value: "different value" } });
    });
    assert.match(view.container.textContent, /새 비밀번호가 일치하지 않습니다/u);
    assert.equal(buttonByText(view.container, "비밀번호 변경").disabled, true);
    await view.act(async () => {
      reactProps(inputs[2]).onChange({ target: { value: "a strong replacement" } });
    });
    const form = findElements(view.container, (node) => node.tagName === "FORM")[0];
    await view.act(async () => { form.dispatchEvent(new MinimalEvent("submit")); form.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
    assert.equal(changes, 1);
    await view.act(async () => { release(); await pending; await Promise.resolve(); });
    assert.deepEqual(redirects, ["/"]);
  } finally { await view.cleanup(); }
});
