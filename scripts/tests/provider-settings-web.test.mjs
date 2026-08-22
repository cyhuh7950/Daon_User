import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";

import { providerSettingsApi } from "../../apps/web/lib/provider-settings-api.js";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("provider settings helper uses same-origin relative BFF paths only", async () => {
  const source = await read("apps/web/lib/provider-settings-api.js");
  for (const path of ["/bff/api/model-profiles", "/bff/api/model-deployments", "/model-policy"]) assert.match(source, new RegExp(path.replaceAll("/", "\\/")));
  assert.match(source, /credentials:\s*["']same-origin["']/);
  assert.doesNotMatch(source, /["'`]\/api\/v1\//);
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL|api[_-]?key|secret_value/i);
});

test("Provider 연결 시험은 same-origin exact route와 safe result만 허용한다", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, options) => {
    requests.push({ path, options });
    return Response.json({
      data: { provider_code: "UPSTAGE", status: "ready", checked_at: "2026-08-15T00:00:00Z" },
      meta: { trace_id: "trace-provider-check" },
    });
  };
  try {
    const result = await providerSettingsApi.checkConnection("workspace-001", "UPSTAGE");
    assert.equal(result.providerCode, "UPSTAGE");
    assert.equal(result.status, "ready");
    assert.deepEqual(requests, [{
      path: "/bff/api/model-profiles/UPSTAGE/connection-check?workspace_id=workspace-001",
      options: { method: "GET", credentials: "same-origin", headers: {}, body: undefined },
    }]);
    globalThis.fetch = async () => Response.json({ data: { provider_code: "UPSTAGE", status: "ready", checked_at: "x", endpoint: "http://internal.invalid" }, meta: { trace_id: "trace" } });
    await assert.rejects(() => providerSettingsApi.checkConnection("workspace-001", "UPSTAGE"), /PROVIDER_CONNECTION_RESPONSE_INVALID/u);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("LLM 설정 실제 React는 조회 후 대표 Provider 연결 시험 결과를 안전하게 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".provider-connection-react-"));
  const dom = installMinimalDom();
  const originalFetch = globalThis.fetch;
  const requests = [];
  let reactRoot;
  globalThis.fetch = async (url, options) => {
    requests.push(String(url));
    if (String(url).startsWith("/bff/api/model-profiles?")) return Response.json({ data: [{ provider_code: "UPSTAGE", base_url: "https://api.upstage.ai/v1", active: true, credential_configured: true, version: 1 }], meta: { trace_id: "trace-profiles" } });
    if (String(url).startsWith("/bff/api/model-deployments?")) return Response.json({ data: [], meta: { trace_id: "trace-deployments" } });
    if (String(url).includes("/model-policy")) return Response.json({ data: { bindings: {}, version: 1 }, meta: { trace_id: "trace-policy" } }, { headers: { etag: '"model-policy:workspace-001:1"' } });
    if (String(url).includes("/UPSTAGE/connection-check")) return Response.json({ data: { provider_code: "UPSTAGE", status: "ready", checked_at: "2026-08-15T00:00:00Z" }, meta: { trace_id: "trace-check" } });
    throw new Error("UNEXPECTED_REQUEST");
  };
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/components/provider-settings-workspace.jsx"), formats: ["es"], fileName: "provider-connection" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("provider-connection") && /\.m?js$/u.test(name));
    const { ProviderSettingsWorkspace } = await import(`${pathToFileURL(path.join(output, entry)).href}?providerConnection=${Date.now()}`);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProviderSettingsWorkspace, { workspaceId: "workspace-001", embedded: true })); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    const button = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "연결 시험")[0];
    assert.ok(button);
    assert.equal(button.disabled, false);
    await act(async () => { button.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /연결 확인됨/u);
    assert.equal(requests.filter((url) => url.includes("/UPSTAGE/connection-check")).length, 1);
    assert.doesNotMatch(container.textContent, /api\.upstage\.ai|internal\.invalid|trace-check/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    globalThis.fetch = originalFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("model connections screen edits approved providers, models, roles and safe credential presence", async () => {
  const [workspace, pane] = await Promise.all([
    read("apps/web/components/actual-workspace.jsx"),
    read("apps/web/components/provider-settings-workspace.jsx")
  ]);
  assert.match(pane, /document_parser/);
  assert.match(pane, /deploymentDrafts/);
  assert.match(pane, /모델 추가/);
  assert.match(pane, /연결 시험/);
  assert.doesNotMatch(pane, /new Map\(deploymentsResult\.payload\.data\.map\(\(item\) => \[item\.provider_code/);
  assert.match(workspace, /ProviderSettingsWorkspace/);
  assert.match(workspace, /providerSettings=/);
  for (const provider of ["CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI", "OPENROUTER", "ANTHROPIC", "OLLAMA"]) assert.match(pane, new RegExp(provider));
  for (const label of ["Endpoint 설정됨", "모델 ID", "역할 매핑", "활성", "선택", "Credential 설정됨"]) assert.match(pane, new RegExp(label));
  assert.doesNotMatch(pane, /api[_-]?key|secret_value|type=["']password["']/i);
});

test("provider settings resolves the authenticated workspace from the same-origin session endpoint", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, options) => {
    requests.push({ path, options });
    return new Response(JSON.stringify({ data: { workspace_id: "workspace-authenticated" } }), {
      status: 200,
      headers: { "content-type": "application/json" }
    });
  };
  try {
    const result = await providerSettingsApi.getSession();
    assert.equal(result.payload.data.workspace_id, "workspace-authenticated");
    assert.deepEqual(requests, [{
      path: "/bff/api/session",
      options: { method: "GET", credentials: "same-origin", headers: {}, body: undefined }
    }]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("model connections has no production workspace fallback and preserves explicit workspace props", async () => {
  const pane = await read("apps/web/components/provider-settings-workspace.jsx");
  assert.match(pane, /providerSettingsApi\.getSession\(\)/);
  assert.match(pane, /workspaceId\s*\?\?/);
  assert.match(pane, /workspaceId\s*\?\s*null\s*:\s*await providerSettingsApi\.getSession\(\)/);
  assert.match(pane, /listProfiles\(activeWorkspaceId\)/);
  assert.match(pane, /workspace_id:\s*activeWorkspaceId/);
  assert.doesNotMatch(pane, /workspaceId\s*=\s*["']workspace-release-one["']/);
  assert.doesNotMatch(pane, /workspace-release-one/);
});
