import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

test("실제 React click은 저장 구조 산출물을 선택하고 검토·승인요청 adapter를 호출한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-click-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const calls = []; const adapter = { createStudioVersion: async (_id, payload) => { calls.push(["version", payload.revision_type]); return { output_version_id: "version-2", status: "draft" }; }, createStudioAction: async (action, payload) => { calls.push([action, payload.decision]); return { record_id: `${action}-1` }; }, issueStudioStepUp: async (_group, _target, password) => { calls.push(["step-up", password]); return "grant"; } };
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{ studio_output_id: "output-1", output_version_id: "version-1", title: "반려 보고서", status: "revision_requested", citations: 1, review_request_id: "review-old", approval_request_id: "approval-old", content: { summary: "반려" } }], selectedOutputId: "output-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter })); });
    await act(async () => { buttonByText(container, "반려 보고서").dispatchEvent(new MinimalEvent("click")); });
    assert.match(container.textContent, /반려/);
    await act(async () => { buttonByText(container, "검토 요청").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); }); await act(async () => { buttonByText(container, "승인 요청").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls.map((item) => item[0]), ["reviews", "approval-requests"]);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});
