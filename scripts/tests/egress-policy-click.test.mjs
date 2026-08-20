import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

const deferred = () => { let resolve; let reject; const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject }; };
const policy = (mode) => ({ mode, allowed_provider_kinds: mode === "deny_external" ? [] : ["external_api"], allowed_destinations: mode === "deny_external" ? [] : ["api.upstage.ai"], classification: mode === "deny_external" ? "restricted" : "internal", max_bytes: mode === "deny_external" ? 0 : 1048576, masking_required: true, redaction_required: true, required_approver: "organization_admin" });
const view = (mode, suffix) => ({ data: { ...policy(mode), organization_policy: policy(mode), workspace_policy: policy(mode), organization_etag: `"org:${suffix}"`, workspace_etag: `"ws:${suffix}"`, parent_locked: mode === "deny_external" }, etag: `"effective:${suffix}"` });

test("정책 React는 조직과 Workspace를 별도 단계·별도 password로 exact save한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(rootPath, ".egress-policy-click-react-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/egress-policy-pane.jsx"), formats: ["es"], fileName: "egress-policy" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("egress-policy") && /\.m?js$/u.test(name)); const { EgressPolicyPane, EgressPolicyPaneInner } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const calls = []; const organization = { mode: "allow_approved_external", allowed_provider_kinds: ["external_api"], allowed_destinations: ["api.example"], classification: "internal", max_bytes: 1024, masking_required: true, redaction_required: true, required_approver: "organization_admin" }; const workspace = { ...organization, mode: "deny_external", allowed_provider_kinds: [], allowed_destinations: [], classification: "restricted", max_bytes: 0 };
    const adapter = { loadContext: async () => ({ data: { organization_id: "tenant-1", workspace_id: "workspace-1" } }), load: async ({ workspaceId }) => { calls.push(["load", workspaceId]); return { data: { ...workspace, organization_policy: organization, workspace_policy: workspace, organization_etag: '"org:1"', workspace_etag: '"ws:1"', parent_locked: false }, etag: '"effective"' }; }, saveOrganization: async (input) => { calls.push(["save-organization", input]); input.sensitive.currentPassword = ""; }, saveWorkspace: async (input) => { calls.push(["save-workspace", input]); input.sensitive.currentPassword = ""; } };
    const keyed = EgressPolicyPane({ organizationId: "tenant-1", workspaceId: "workspace-1", adapter });
    const collisionLeft = EgressPolicyPane({ organizationId: "a:b", workspaceId: "c", adapter });
    const collisionRight = EgressPolicyPane({ organizationId: "a", workspaceId: "b:c", adapter });
    assert.equal(keyed.type, EgressPolicyPaneInner); assert.equal(keyed.key, '["tenant-1","workspace-1"]');
    assert.notEqual(collisionLeft.key, collisionRight.key);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(EgressPolicyPane, { organizationId: "", workspaceId: "", adapter })); await Promise.resolve(); await Promise.resolve(); });
    assert.ok(buttonByText(container, "1. 조직 정책")); assert.ok(buttonByText(container, "2. Workspace 정책"));
    await act(async () => { buttonByText(container, "2. Workspace 정책").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const inputs = findElements(container, (node) => node.tagName === "INPUT"); const password = inputs.at(-1); password.value = "workspace-memory-only";
    const mode = findElements(container, (node) => node.tagName === "SELECT")[0]; mode.value = "allow_approved_external"; await act(async () => mode.dispatchEvent(new MinimalEvent("change")));
    const form = findElements(container, (node) => node.tagName === "FORM")[0]; await act(async () => { form.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); await Promise.resolve(); });
    assert.deepEqual(calls[0], ["load", "workspace-1"]); assert.equal(calls[1][0], "save-workspace"); assert.equal(calls[1][1].workspaceId, "workspace-1"); assert.equal(calls[1][1].etag, '"ws:1"'); assert.equal(password.value, "");
    assert.equal(calls[1][1].sensitive.currentPassword, "");
    await act(async () => buttonByText(container, "1. 조직 정책").dispatchEvent(new MinimalEvent("click")));
    assert.match(container.textContent, /조직 정책을 별도로 저장합니다/u);
    await act(async () => buttonByText(container, "2. Workspace 정책").dispatchEvent(new MinimalEvent("click")));
    assert.match(container.textContent, /Workspace 정책을 별도로 저장합니다/u);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("정책 React는 이전 context load의 reverse response를 무시하고 최신 workspace만 표시한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(rootPath, ".egress-policy-race-react-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { flushSync } = await import("react-dom"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/egress-policy-pane.jsx"), formats: ["es"], fileName: "egress-policy" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("egress-policy") && /\.m?js$/u.test(name)); const { EgressPolicyPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?race=${Date.now()}`);
    const stale = deferred(); const latest = deferred(); const calls = []; let oldCalls = 0;
    const adapter = { load: ({ workspaceId }) => { calls.push(workspaceId); if (workspaceId === "workspace-old") { oldCalls += 1; return oldCalls === 1 ? Promise.resolve(view("deny_external", "initial")) : stale.promise; } return latest.promise; }, saveOrganization: async () => {}, saveWorkspace: async () => {} };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(EgressPolicyPane, { organizationId: "org-old", workspaceId: "workspace-old", adapter })); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /외부 전송 차단/u);
    await act(async () => { reactRoot.render(createElement(EgressPolicyPane, { organizationId: "org-old-reload", workspaceId: "workspace-old", adapter })); await Promise.resolve(); });
    await act(async () => flushSync(() => reactRoot.render(createElement(EgressPolicyPane, { organizationId: "org-new", workspaceId: "workspace-new", adapter }))));
    assert.equal(findElements(container, (node) => node.tagName === "FORM").length, 0);
    assert.equal(findElements(container, (node) => node.tagName === "INPUT").length, 0);
    assert.equal(buttonByText(container, "1. 조직 정책"), undefined);
    await act(async () => { await Promise.resolve(); });
    assert.deepEqual(calls, ["workspace-old", "workspace-old", "workspace-new"]);
    assert.equal(findElements(container, (node) => node.tagName === "FORM").length, 0);
    assert.equal(buttonByText(container, "1. 조직 정책"), undefined);
    assert.equal(buttonByText(container, "2. Workspace 정책"), undefined);
    assert.equal(findElements(container, (node) => node.tagName === "INPUT").length, 0);
    assert.doesNotMatch(container.textContent, /외부 전송 차단|승인된 외부 전송 허용/u);
    await act(async () => { latest.resolve(view("allow_approved_external", "new")); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { stale.resolve(view("deny_external", "old")); await Promise.resolve(); await Promise.resolve(); });
    const status = findElements(container, (node) => node.getAttribute?.("role") === "status")[0];
    assert.match(status.textContent, /승인된 외부 전송 허용/u);
    assert.doesNotMatch(container.textContent, /조직 차단 정책/u);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("정책 React는 save 중 scope navigation을 잠그고 새 scope password를 stale finally로 지우지 않는다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(rootPath, ".egress-policy-save-race-react-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/egress-policy-pane.jsx"), formats: ["es"], fileName: "egress-policy" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("egress-policy") && /\.m?js$/u.test(name)); const { EgressPolicyPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?saveRace=${Date.now()}`);
    const pending = deferred(); const adapter = { load: async () => view("allow_approved_external", "1"), saveOrganization: () => pending.promise, saveWorkspace: async () => {} };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(EgressPolicyPane, { organizationId: "org-1", workspaceId: "workspace-1", adapter })); await Promise.resolve(); await Promise.resolve(); });
    const password = findElements(container, (node) => node.tagName === "INPUT").at(-1); password.value = "memory-only";
    const mode = findElements(container, (node) => node.tagName === "SELECT")[0]; mode.value = "deny_external"; await act(async () => mode.dispatchEvent(new MinimalEvent("change")));
    await act(async () => { findElements(container, (node) => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
    assert.equal(buttonByText(container, "1. 조직 정책").disabled, true);
    assert.equal(buttonByText(container, "2. Workspace 정책").disabled, true);
    pending.resolve({ data: {} });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    assert.equal(password.value, "");
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("정책 React는 context 변경으로 abort된 이전 save의 write와 password clear를 무효화한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(rootPath, ".egress-policy-abort-react-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/egress-policy-pane.jsx"), formats: ["es"], fileName: "egress-policy" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("egress-policy") && /\.m?js$/u.test(name)); const { EgressPolicyPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?abort=${Date.now()}`);
    const pending = deferred(); let writes = 0;
    const adapter = { load: async ({ workspaceId }) => view(workspaceId === "workspace-old" ? "allow_approved_external" : "deny_external", workspaceId), saveOrganization: async ({ signal }) => { await pending.promise; if (signal.aborted) { const error = new Error("aborted"); error.name = "AbortError"; throw error; } writes += 1; }, saveWorkspace: async () => {} };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(EgressPolicyPane, { organizationId: "org-old", workspaceId: "workspace-old", adapter })); await Promise.resolve(); await Promise.resolve(); });
    const oldPassword = findElements(container, (node) => node.tagName === "INPUT").at(-1); oldPassword.value = "old-memory-only";
    const mode = findElements(container, (node) => node.tagName === "SELECT")[0]; mode.value = "deny_external"; await act(async () => mode.dispatchEvent(new MinimalEvent("change")));
    await act(async () => { findElements(container, (node) => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
    await act(async () => { reactRoot.render(createElement(EgressPolicyPane, { organizationId: "org-new", workspaceId: "workspace-new", adapter })); await Promise.resolve(); await Promise.resolve(); });
    const newPassword = findElements(container, (node) => node.tagName === "INPUT").at(-1); newPassword.value = "new-memory-only";
    pending.resolve(); await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    assert.equal(writes, 0); assert.equal(newPassword.value, "new-memory-only");
    assert.match(findElements(container, (node) => node.getAttribute?.("role") === "status")[0].textContent, /외부 전송 차단/u);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});
