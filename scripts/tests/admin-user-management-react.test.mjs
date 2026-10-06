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
    const protectedButton = buttonByText(view.container, "보호됨");
    assert.equal(protectedButton.disabled, true);
    const searchInput = findElements(view.container, (node) => node.tagName === "INPUT" && reactProps(node)?.type === "search")[0];
    assert.equal(reactProps(searchInput).placeholder, "로그인 ID, 사용자 ID 또는 이메일");
    await view.act(async () => { reactProps(searchInput).onChange({ target: { value: "person@example.test" } }); });
    assert.doesNotMatch(view.container.textContent, /보호된 시스템 관리자/u);
    assert.match(view.container.textContent, /person@example\.test/u);
    const action = buttonByText(view.container, "중지");
    await view.act(async () => { action.dispatchEvent(new MinimalEvent("click")); action.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(changes, 1);
    await view.act(async () => { release(); await pending; await Promise.resolve(); });
    assert.match(view.container.textContent, /재활성화/u);
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
    for (const label of ["선택 0건", "선택 삭제", "새로고침", "사용자 등록", "조회", "사용자", "이메일", "상태", "역할", "관리"]) {
      assert.match(view.container.textContent, new RegExp(label, "u"));
    }
    assert.match(view.container.textContent, /2명의 계정을 조회했습니다/u);
    assert.match(view.container.textContent, /관리자/u);
    assert.match(view.container.textContent, /일반 사용자/u);
    assert.doesNotMatch(view.container.textContent, /user-1/u);
    assert.doesNotMatch(view.container.textContent, /전문가/u);
    assert.match(view.container.textContent, /관리자/u);
    assert.match(view.container.textContent, /수정/u);
  } finally { await view.cleanup(); }
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

test("admin console의 단일 삭제는 요청 대기 중 재클릭을 막고 실패 후 잠금을 푼다", async () => {
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
    const single = buttonByText(view.container, "삭제");
    await view.act(async () => { single.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(buttonByText(view.container, "삭제").disabled, true);
    await view.act(async () => { reactProps(single).onClick(); await Promise.resolve(); });
    assert.deepEqual(removed, ["user-1"]);
    assert.equal(confirms, 1);
    await view.act(async () => { rejectPending(new Error("ADMIN_USER_DELETE_FAILED")); await pending.catch(() => {}); await Promise.resolve(); });
    assert.equal(buttonByText(view.container, "삭제").disabled, false);
    assert.match(view.container.textContent, /ADMIN_USER_DELETE_FAILED/u);
    await view.act(async () => { buttonByText(view.container, "삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
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
  }, ".admin-membership-role-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    const roleCells = findElements(view.container, (node) => node.getAttribute?.("class") === "admin-user-role-list");
    assert.deepEqual(roleCells.map((node) => node.textContent), [
      "역할 미조회",
      "역할 미조회",
    ]);
    await view.act(async () => { buttonByText(view.container, "수정").dispatchEvent(new MinimalEvent("click")); });
    assert.equal(findElements(view.container, (node) => node.getAttribute?.("class") === "admin-user-role-fields").length, 0);
    assert.doesNotMatch(view.container.textContent, /전문가|역할 정책은 현재 계정 권한 계약/u);
    assert.match(view.container.textContent, /역할 미조회/u);
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
      await view.act(async () => { buttonByText(view.container, "역할 조회").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
      const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
      if (stage !== "tenants") await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
      if (stage === "patch" || stage === "reread") {
        await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
        await view.act(async () => { reactProps(select("변경 사유")).onChange({ target: { value: "ACCESS_REVIEW" } }); });
        await view.act(async () => { buttonByText(view.container, "역할 변경").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
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
    await view.act(async () => { buttonByText(view.container, "역할 조회").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
    await view.act(async () => { reactProps(select("변경할 역할")).onChange({ target: { value: "editor" } }); reactProps(select("변경 사유")).onChange({ target: { value: "ACCESS_REVIEW" } }); });
    await view.act(async () => { buttonByText(view.container, "역할 변경").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const close = buttonByText(view.container, "닫기");
    assert.equal(close.disabled, true);
    await view.act(async () => { reactProps(close).onClick(); await Promise.resolve(); });
    assert.match(view.container.textContent, /처리 중…/u);
    await view.act(async () => { releaseWrite(); await writePending; await Promise.resolve(); });
    assert.equal(reads, 2);
    assert.match(view.container.textContent, /역할 변경 완료.*version 3/su);
  } finally { releaseWrite(); globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("사용자별 역할 조회 버튼은 대상 이름을 접근성 이름에 포함한다", async () => {
  const users = ["one", "two"].map((login_id) => ({ user_id: login_id, login_id, email: null, has_email: false, state: "active", protected: false }));
  const view = await render("apps/web/components/admin-user-console.jsx", "AdminUserConsole", {
    getSession: async () => ({ user_id: "admin", password_change_required: false, is_system_admin: true }),
    getUsers: async () => users,
  }, ".admin-role-aria-label-");
  try {
    await view.act(async () => { await Promise.resolve(); });
    const buttons = findElements(view.container, (node) => node.tagName === "BUTTON" && node.textContent === "역할 조회");
    assert.deepEqual(buttons.map((button) => reactProps(button)["aria-label"]), ["one 역할 조회", "two 역할 조회"]);
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
    assert.match(view.container.textContent, /역할 미조회/u);
    await view.act(async () => { buttonByText(view.container, "역할 조회").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls, []);
    assert.match(view.container.textContent, /역할 미조회/u);
    assert.match(view.container.textContent, /시스템 관리자 여부.*조회하지 않음/u);
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
    await view.act(async () => { buttonByText(view.container, "역할 변경").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(calls[1][0], "write");
    assert.deepEqual(calls[1].slice(1, 5), ["tenant-a", "workspace-a", "user-1", { role: "editor", expected_version: 2, reason: "ACCESS_REVIEW" }]);
    assert.deepEqual(calls[2], ["read", "tenant-a", "user-1"]);
    assert.match(view.container.textContent, /역할 변경 완료/u);
    assert.match(view.container.textContent, /version 3/u);
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
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
    const roleButtons = findElements(view.container, (node) => node.tagName === "BUTTON" && node.textContent === "역할 조회");
    await view.act(async () => { roleButtons[1].dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
    await view.act(async () => {
      reactProps(select("변경할 역할")).onChange({ target: { value: "editor" } });
      reactProps(select("변경 사유")).onChange({ target: { value: "CORRECTION" } });
    });
    await view.act(async () => { buttonByText(view.container, "역할 변경").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(writes, 1); assert.equal(reads, 2);
    assert.equal(reactProps(select("변경할 역할")).value, "editor");
    assert.equal(reactProps(select("변경 사유")).value, "CORRECTION");
    assert.match(view.container.textContent, /VERSION_CONFLICT/u);
    assert.match(view.container.textContent, /최신.*reviewer.*version 3/su);
    await view.act(async () => { buttonByText(view.container, "닫기").dispatchEvent(new MinimalEvent("click")); });
    await view.act(async () => { roleButtons[0].dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-a" } }); });
    assert.equal(buttonByText(view.container, "역할 변경").disabled, true);
    assert.equal(writes, 1);
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
    await view.act(async () => { buttonByText(view.container, "역할 조회").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const select = (label) => findElements(view.container, (node) => node.tagName === "SELECT" && reactProps(node)?.["aria-label"] === label)[0];
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-a" } }); await Promise.resolve(); });
    await view.act(async () => { reactProps(select("조직 선택")).onChange({ target: { value: "tenant-b" } }); await Promise.resolve(); });
    await view.act(async () => { finishOldRead({ tenant: null, workspaces: [{ workspace_id: "workspace-a", role: "editor", state: "active", version: 9 }] }); await Promise.resolve(); });
    assert.doesNotMatch(view.container.textContent, /workspace-a/u);
    assert.match(view.container.textContent, /workspace-b/u);
    await view.act(async () => { reactProps(select("작업공간 선택")).onChange({ target: { value: "workspace-b" } }); });
    await view.act(async () => { reactProps(select("변경 사유")).onChange({ target: { value: "ACCESS_REVIEW" } }); });
    await view.act(async () => { buttonByText(view.container, "역할 변경").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(writes, 1);
    assert.match(view.container.textContent, /FORBIDDEN/u);
    assert.equal(reactProps(select("변경 사유")).value, "ACCESS_REVIEW");
  } finally { globalThis.window.confirm = previousConfirm; await view.cleanup(); }
});

test("admin console의 단일 삭제는 확인 취소와 보호 계정을 차단한다", async () => {
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
    assert.equal(findElements(view.container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제").length, 1);
    await view.act(async () => { buttonByText(view.container, "삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(removed, []);
    await view.act(async () => { buttonByText(view.container, "삭제").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(confirms, ["one 계정을 삭제하시겠습니까?", "one 계정을 삭제하시겠습니까?"]);
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
    assert.equal(findElements(view.container, (node) => node.tagName === "BUTTON" && node.textContent === "비밀번호 초기화").length, 1);
    await view.act(async () => { buttonByText(view.container, "비밀번호 초기화").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
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
    const action = buttonByText(view.container, "인증 대기");
    assert.equal(action.disabled, true);
    await view.act(async () => { reactProps(action).onClick(); await Promise.resolve(); });
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
