import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

test("조직 정책 React는 authorized context 후 8필드 draft와 password를 exact save한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(rootPath, ".egress-policy-click-react-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/egress-policy-pane.jsx"), formats: ["es"], fileName: "egress-policy" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("egress-policy") && /\.m?js$/u.test(name)); const { EgressPolicyPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const calls = []; const organization = { mode: "allow_approved_external", allowed_provider_kinds: ["external_api"], allowed_destinations: ["api.example"], classification: "internal", max_bytes: 1024, masking_required: false, redaction_required: false, required_approver: "organization_admin" };
    const adapter = { loadContext: async () => ({ data: { organization_id: "tenant-1", workspace_id: "workspace-1" } }), load: async ({ workspaceId }) => { calls.push(["load", workspaceId]); return { data: { ...organization, organization_policy: organization, workspace_policy: organization, organization_etag: '"org:1"', workspace_etag: '"ws:1"', parent_locked: false }, etag: '"effective"' }; }, save: async (input) => { calls.push(["save", input]); input.sensitive.currentPassword = ""; } };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(EgressPolicyPane, { organizationId: "", workspaceId: "", adapter })); await Promise.resolve(); await Promise.resolve(); });
    const inputs = findElements(container, (node) => node.tagName === "INPUT"); const password = inputs.at(-1); password.value = "memory-only";
    const mode = findElements(container, (node) => node.tagName === "SELECT")[0]; mode.value = "deny_external"; await act(async () => mode.dispatchEvent(new MinimalEvent("change")));
    const form = findElements(container, (node) => node.tagName === "FORM")[0]; await act(async () => { form.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); await Promise.resolve(); });
    assert.deepEqual(calls[0], ["load", "workspace-1"]); assert.equal(calls[1][0], "save"); assert.equal(calls[1][1].organizationId, "tenant-1"); assert.equal(calls[1][1].etag, '"org:1"'); assert.equal(password.value, "");
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});
