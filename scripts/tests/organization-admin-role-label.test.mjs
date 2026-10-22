import assert from "node:assert/strict";
import { lstat, realpath, mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { installMinimalDom, findElements } from "./product-studio-dom.mjs";
import { changeMemberRole, changeMemberState } from "../../apps/web/lib/organization-admin-api.js";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("기존 미연결 조직 역할은 실제 컴포넌트에서 표기 역할·읽기 전용으로 구분한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const temp = root;
  assert.ok(path.isAbsolute(temp));
  assert.equal(await realpath(temp), temp, "소유 root의 링크 우회를 거부한다");
  const output = await mkdtemp(path.join(temp, ".r4-role-label-"));
  const dom = installMinimalDom(); window.location = { search: "" };
  let reactRoot;
  const { createElement, act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  try {
    const { build } = await import("vite");
    const require = createRequire(import.meta.url);
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/components/organization-admin-console.jsx"), formats: ["es"], fileName: "role-label" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: id => pathToFileURL(require.resolve(id)).href } } } });
    const built = (await readdir(output)).find(file => /\.m?js$/u.test(file));
    const Component = (await import(pathToFileURL(path.join(output, built)).href)).default;
    const api = { getOrganizationSession: async () => ({ user_id: "admin", tenant_id: "tenant-a", session_id: "session-a", is_system_admin: false }),
      listManagedWorkspaces: async () => [], listJoinRequests: async () => [], listInvitations: async () => [],
      listMembers: async () => [{ tenant_id: "tenant-a", user_id: "legacy-user", role: "organization_admin", state: "active", version: 1, updated_at: "2026-10-17T00:00:00+00:00", join_workspace_id: null, access_role: null, access_state: null, access_version: null }] };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(Component, { api })); await Promise.resolve(); });
    assert.match(container.textContent, /기존 기록—Workspace 미연결/u);
    assert.match(container.textContent, /표기 역할 organization_admin · 실제 실행 권한과 별개/u);
    assert.equal(findElements(container, node => node.tagName === "BUTTON" && node.textContent === "역할 저장").length, 0);
    assert.equal(findElements(container, node => node.tagName === "SELECT" && node.getAttribute("aria-label") === "legacy-user 실제 역할").length, 0);
  } finally { if (reactRoot) await act(async () => reactRoot.unmount()); dom.restore(); assert.equal(path.dirname(output), temp); assert.equal(await realpath(output), output); assert.equal((await lstat(output)).isSymbolicLink(), false); await rm(output, { recursive: true, force: true }); }
});

test("기존 조직 역할 변경과 상태 변경 요청은 그대로 분리된다", async () => {
  const original = globalThis.fetch, calls = [];
  const member = { tenant_id: "tenant-a", user_id: "user-a", role: "member", state: "active", version: 2, updated_at: "2026-10-17T00:00:00+00:00", join_workspace_id: "workspace-a", access_role: "editor", access_state: "active", access_version: 2 };
  globalThis.fetch = async (url, init) => { calls.push([url, init.method, JSON.parse(init.body)]); return Response.json({ data: member }); };
  try {
    const options = { idempotencyKey: "l3-role-state-test-0001", expectedSessionId: "session-a" };
    const role = { workspace_id: "workspace-a", role: "editor", expected_version: 1, expected_auth_version: 1, reason: "업무 검토" };
    const state = { expected_version: 2, reason: "조직 중지" };
    await changeMemberRole("tenant-a", "user-a", role, options);
    await changeMemberState("tenant-a", "user-a", false, state, { ...options, idempotencyKey: "l3-role-state-test-0002" });
    assert.deepEqual(calls, [["/bff/api/organization/tenants/tenant-a/members/user-a/role", "PATCH", role], ["/bff/api/organization/tenants/tenant-a/members/user-a/state?active=false", "PATCH", state]]);
  } finally { globalThis.fetch = original; }
});

test("C9 관리자 사용자 화면은 구 조직 역할 변경 API를 호출하지 않는다", async () => {
  const source = await read("apps/web/components/admin-user-console.jsx");
  assert.doesNotMatch(source, /organization-admin-api|changeMemberRole|\/organization\/tenants\//u);
});
