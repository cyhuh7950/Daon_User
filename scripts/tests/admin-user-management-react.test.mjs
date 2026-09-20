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
