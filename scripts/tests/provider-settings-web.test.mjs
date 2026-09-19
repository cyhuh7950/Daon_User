import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { providerSettingsApi } from "../../apps/web/lib/provider-settings-api.js";
import { findElements, installMinimalDom } from "./product-studio-dom.mjs";

const read = (file) => readFile(new URL(`../../${file}`, import.meta.url), "utf8");

async function bundleProvider(root, output, fileName) {
  const { build } = await import("vite");
  await build({ configFile: false, logLevel: "silent", root, build: {
    outDir: output, emptyOutDir: false,
    lib: { entry: path.join(root, "apps/web/components/provider-settings-workspace.jsx"), formats: ["es"], fileName },
    rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
  } });
  const entry = (await readdir(output)).find((name) => name.startsWith(fileName) && /\.m?js$/u.test(name));
  return import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
}

test("provider settings helper uses only the approved same-origin admin routes", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url) === "/bff/api/admin/provider-connections" && options.method === "GET") {
      return Response.json({ data: [], meta: { trace_id: "trace-list" } }, { headers: { etag: '"connections:1"' } });
    }
    if (String(url) === "/bff/api/session/step-up") {
      return Response.json({ data: { step_up_authorization: "step-up-token", issued_at: "2026-09-17T00:00:00Z", expires_at: "2026-09-17T00:05:00Z" }, meta: { trace_id: "trace-step-up" } });
    }
    if (options.method === "DELETE") return new Response(null, { status: 204, headers: { etag: '"connection:3"' } });
    return Response.json({ data: { connection_id: "ollama-lan", version: 2, catalog_version: 3 }, meta: { trace_id: "trace-mutation" } });
  };
  try {
    await providerSettingsApi.listConnections();
    await providerSettingsApi.getModelDefaults("workspace-001");
    await providerSettingsApi.issueStepUp("provider-connection:ollama-lan", "administrator-password", "stepup-0001");
    await providerSettingsApi.createConnection({ connection_id: "ollama-lan" }, "create-0001");
    await providerSettingsApi.updateConnection("ollama-lan", { expected_version: 1 }, "update-0001");
    await providerSettingsApi.replaceCredential("ollama-lan", { credential: "replacement", expected_version: 2 }, "credential-0001");
    await providerSettingsApi.deleteCredential("ollama-lan", { expected_version: 2 }, "delete-0001");
    await providerSettingsApi.refreshCatalog("ollama-lan", { expected_version: 2 }, "refresh-0001");
    await providerSettingsApi.correctCapabilities("ollama-lan", "qwen3", { effective_capabilities: ["text_generation"], expected_version: 3 }, "capability-0001");
    await providerSettingsApi.saveModelDefault("workspace-001", { capability: "text_generation", connection_id: "ollama-lan", model_id: "qwen3", expected_version: 0 }, '"defaults-v0"', "default-0001");
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(requests.map(({ url, options }) => [options.method ?? "GET", url]), [
    ["GET", "/bff/api/admin/provider-connections"],
    ["GET", "/bff/api/workspaces/workspace-001/model-defaults"],
    ["POST", "/bff/api/session/step-up"],
    ["POST", "/bff/api/admin/provider-connections"],
    ["PUT", "/bff/api/admin/provider-connections/ollama-lan"],
    ["POST", "/bff/api/admin/provider-connections/ollama-lan/credential"],
    ["DELETE", "/bff/api/admin/provider-connections/ollama-lan"],
    ["POST", "/bff/api/admin/provider-catalog/ollama-lan/refresh"],
    ["PATCH", "/bff/api/admin/provider-models/ollama-lan/qwen3/capabilities"],
    ["PATCH", "/bff/api/workspaces/workspace-001/model-defaults"],
  ]);
  assert.equal(JSON.parse(requests[2].options.body).password, "administrator-password");
  assert.equal(requests.at(-1).options.headers["If-Match"], '"defaults-v0"');
  for (const request of requests) assert.equal(request.options.credentials, "same-origin");
  const source = await read("apps/web/lib/provider-settings-api.js");
  assert.doesNotMatch(source, /["'`]\/api\/v1\//u);
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/iu);
});

test("system admin sees named multi-connections, password-only secrets, catalogs and capability readiness", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(tmpdir(), "provider-settings-react-"));
  const dom = installMinimalDom();
  const originalFetch = globalThis.fetch;
  let reactRoot;
  const endpoint = "http://192.0.2.44:11434";
  const rawCredential = "fixture-client-key-never-render";
  const connections = [
    {
      connection_id: "ollama-lan", provider_code: "OLLAMA", display_name: "LAN Ollama",
      enabled: true, configured: false, credential_version: 0, verification_status: "verified",
      verified_at: "2026-09-17T00:00:00Z", version: 2, catalog_status: "ready", catalog_version: 4,
      models: [{ connection_id: "ollama-lan", model_id: "qwen3", reported_capabilities: ["text_generation", "embedding"], effective_capabilities: ["text_generation", "embedding"], override_applied: false, catalog_status: "ready", catalog_version: 4 }],
    },
    {
      connection_id: "ollama-lab", provider_code: "OLLAMA", display_name: "Lab Ollama",
      enabled: true, configured: false, credential_version: 0, verification_status: "verified",
      verified_at: "2026-09-17T00:00:00Z", version: 1, catalog_status: "ready", catalog_version: 1,
      models: [{ connection_id: "ollama-lab", model_id: "qwen3", reported_capabilities: ["text_generation"], effective_capabilities: ["text_generation"], override_applied: false, catalog_status: "ready", catalog_version: 1 }],
    },
  ];
  globalThis.fetch = async (url) => {
    if (String(url) === "/bff/api/session") return Response.json({ data: { workspace_id: "workspace-001", is_system_admin: true }, meta: { trace_id: "trace-session" } });
    if (String(url) === "/bff/api/admin/provider-connections") return Response.json({ data: connections, meta: { trace_id: "trace-list" } }, { headers: { etag: '"provider-connections:4"' } });
    if (String(url) === "/bff/api/workspaces/workspace-001/model-defaults") return Response.json({ data: { workspace_id: "workspace-001", available_models: connections.flatMap((connection) => connection.models.map((model) => ({ ...model, display_name: connection.display_name, provider_code: connection.provider_code, configured: connection.configured, connection_version: connection.version }))), defaults: [], version: 0 }, meta: { trace_id: "trace-defaults" } }, { headers: { etag: '"defaults-v0"' } });
    throw new Error("UNEXPECTED_REQUEST");
  };
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProviderSettingsWorkspace } = await bundleProvider(root, output, "provider-settings");
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => {
      reactRoot.render(createElement(ProviderSettingsWorkspace, { workspaceId: "workspace-001", embedded: true }));
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    for (const label of ["연결 이름", "Endpoint", "LAN Ollama · qwen3", "Lab Ollama · qwen3", "키 저장 및 연결 확인", "키 삭제", "모델 조회", "텍스트 생성", "임베딩"]) {
      assert.match(container.textContent, new RegExp(label, "u"));
    }
    assert.ok(findElements(container, (node) => node.tagName === "INPUT" && node.value === endpoint).length >= 1);
    const passwordInputs = findElements(container, (node) => node.tagName === "INPUT" && (node.type === "password" || node.getAttribute("type") === "password"));
    assert.equal(passwordInputs.length, 2);
    assert.doesNotMatch(container.textContent, new RegExp(`${endpoint}|${rawCredential}|역할 매핑 저장|기능별 모델 선택|모델 기능 보정`, "u"));
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    globalThis.fetch = originalFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("non-system admin never receives credential mutation controls or calls the admin list", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(tmpdir(), "provider-settings-workspace-react-"));
  const dom = installMinimalDom();
  const originalFetch = globalThis.fetch;
  const requests = [];
  let reactRoot;
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    return Response.json({ data: { workspace_id: "workspace-001", is_system_admin: false }, meta: { trace_id: "trace-session" } });
  };
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProviderSettingsWorkspace } = await bundleProvider(root, output, "provider-workspace");
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProviderSettingsWorkspace, { workspaceId: "workspace-001", embedded: true })); await Promise.resolve(); await Promise.resolve(); });
    assert.deepEqual(requests, ["/bff/api/session", "/bff/api/workspaces/workspace-001/model-defaults"]);
    assert.equal(findElements(container, (node) => node.tagName === "INPUT" && (node.type === "password" || node.getAttribute("type") === "password")).length, 0);
    assert.doesNotMatch(container.textContent, /키 저장 및 연결 확인|키 삭제|모델 조회|역할 매핑 저장|기능별 모델 선택/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    globalThis.fetch = originalFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("provider view helpers expose safe status and connection-name model choices only", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(tmpdir(), "provider-settings-helpers-"));
  try {
    const { formatModelChoice, projectProviderConnection, safeProviderErrorMessage } = await bundleProvider(root, output, "provider-helpers");
    assert.equal(formatModelChoice({ display_name: "LAN Ollama" }, { model_id: "qwen3" }), "LAN Ollama · qwen3");
    assert.deepEqual(projectProviderConnection({ enabled: true, configured: true, verification_status: "verified" }), { label: "활성 · Credential 설정됨 · 확인됨", verified: true });
    assert.equal(safeProviderErrorMessage("credential", { code: "INTERNAL_DOCKER_HOST_api:8000" }), "Credential을 저장하지 못했습니다. 다시 시도해 주세요.");
    assert.doesNotMatch(safeProviderErrorMessage("credential", { code: "INTERNAL_DOCKER_HOST_api:8000" }), /api:8000|INTERNAL/iu);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});
