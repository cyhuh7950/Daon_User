import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { providerSettingsApi } from "../../apps/web/lib/provider-settings-api.js";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

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

function controlFor(root, label) {
  const field = findElements(root, (node) => node.tagName === "LABEL" && node.textContent.startsWith(label))[0];
  assert.ok(field, `missing field: ${label}`);
  const control = findElements(field, (node) => ["INPUT", "SELECT", "TEXTAREA"].includes(node.tagName))[0];
  assert.ok(control, `missing control: ${label}`);
  return control;
}

function reactProps(control) {
  const propsKey = Object.keys(control).find((key) => key.startsWith("__reactProps$"));
  assert.ok(propsKey, "React control props unavailable");
  return control[propsKey];
}

async function fill(act, control, value) {
  control.value = value;
  await act(async () => reactProps(control).onChange({ target: control }));
}

async function setChecked(act, control, checked) {
  control.checked = checked;
  await act(async () => reactProps(control).onChange({ target: control }));
}

async function click(act, button) {
  assert.ok(button);
  await act(async () => { button.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
}

function adminFixture({ previewModels = [], previewFails = false, saveFailures = 0, existingConnections = [], refreshResult = null } = {}) {
  const requests = [];
  let connections = existingConnections;
  let pendingSaveFailures = saveFailures;
  return {
    requests,
    async fetch(url, options = {}) {
      const path = String(url);
      const method = options.method ?? "GET";
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ path, method, body });
      if (path === "/bff/api/session") return Response.json({ data: { workspace_id: "workspace-001", is_system_admin: true } });
      if (path === "/bff/api/admin/provider-connections" && method === "GET") return Response.json({ data: connections });
      if (path === "/bff/api/provider-credentials") return Response.json({ data: { credentials: [] } });
      if (path === "/bff/api/admin/provider-health-settings") return Response.json({ data: { interval_minutes: 60, version: 1 } });
      if (path === "/bff/api/admin/provider-connections/model-preview") {
        return previewFails
          ? Response.json({ error: { code: "PROVIDER_CATALOG_UNAVAILABLE" } }, { status: 503 })
          : Response.json({ data: { model_ids: previewModels } });
      }
      if (refreshResult && path === `/bff/api/admin/provider-catalog/${refreshResult.connection_id}/refresh` && method === "POST") {
        connections = [refreshResult];
        return Response.json({ data: refreshResult });
      }
      if (path === "/bff/api/admin/provider-connections" && method === "POST") {
        if (pendingSaveFailures > 0) {
          pendingSaveFailures -= 1;
          return Response.json({ error: { code: "PROVIDER_VERIFICATION_FAILED" } }, { status: 503 });
        }
        const item = { ...body, version: 1, configured: body.access_mode === "public", verification_status: "verified", catalog_status: "ready", catalog_version: 1,
          models: body.allowed_model_ids.map((model_id) => ({ model_id, catalog_status: "ready", catalog_version: 1, effective_capabilities: ["text_generation"] })) };
        delete item.credential;
        delete item.test_credential;
        connections = [item];
        return Response.json({ data: item });
      }
      throw new Error(`UNEXPECTED_REQUEST: ${path}`);
    },
  };
}

async function mountAdminFixture(fixture, fileName) {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, "node_modules", `.provider-${fileName}-`));
  const dom = installMinimalDom();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fixture.fetch;
  const { createElement, act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { ProviderSettingsWorkspace } = await bundleProvider(root, output, fileName);
  const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
  const reactRoot = createRoot(container);
  await act(async () => { reactRoot.render(createElement(ProviderSettingsWorkspace, { workspaceId: "workspace-001", embedded: true })); await Promise.resolve(); await Promise.resolve(); });
  return { container, act, async cleanup() { await act(async () => reactRoot.unmount()); globalThis.fetch = originalFetch; dom.restore(); await rm(output, { recursive: true, force: true }); } };
}

test("system admin can save a Provider connection without a second password", async () => {
  const fixture = adminFixture();
  const view = await mountAdminFixture(fixture, "admin-session-save");
  try {
    assert.equal(findElements(view.container, (node) => node.tagName === "LABEL" && node.textContent.startsWith("관리자 재인증 비밀번호")).length, 0);
    await fill(view.act, controlFor(view.container, "Provider 표시 이름"), "Ollama");
    await fill(view.act, controlFor(view.container, "연결 이름"), "공용 Ollama");
    await fill(view.act, controlFor(view.container, "두 글자 약어"), "OO");
    await fill(view.act, controlFor(view.container, "Endpoint"), "http://ollama.internal:11434");
    assert.equal(buttonByText(view.container, "연결 시험 및 저장").disabled, false);
    await click(view.act, buttonByText(view.container, "연결 시험 및 저장"));
    assert.equal(fixture.requests.filter((item) => item.path === "/bff/api/session/step-up").length, 0);
    const created = fixture.requests.find((item) => item.path === "/bff/api/admin/provider-connections" && item.method === "POST");
    assert.ok(created);
    assert.equal(Object.hasOwn(created.body, "step_up_authorization_id"), false);
  } finally { await view.cleanup(); }
});

test("provider settings helper uses only the approved same-origin admin routes", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url) === "/bff/api/admin/provider-connections" && options.method === "GET") {
      return Response.json({ data: [], meta: { trace_id: "trace-list" } }, { headers: { etag: '"connections:1"' } });
    }
    if (options.method === "DELETE") return new Response(null, { status: 204, headers: { etag: '"connection:3"' } });
    return Response.json({ data: { connection_id: "ollama-lan", version: 2, catalog_version: 3 }, meta: { trace_id: "trace-mutation" } });
  };
  try {
    await providerSettingsApi.listConnections();
    await providerSettingsApi.previewModels({ connection_id: "custom-1", provider_code: "CUSTOM", adapter_type: "openai_compatible", base_url: "https://models.example/v1", credential: "fixture-preview-key" });
    await providerSettingsApi.getModelDefaults("workspace-001");
    await providerSettingsApi.createConnection({ connection_id: "ollama-lan" }, "create-0001");
    await providerSettingsApi.updateConnection("ollama-lan", { expected_version: 1 }, "update-0001");
    await providerSettingsApi.replaceCredential("ollama-lan", { credential: "replacement", expected_version: 2 }, "credential-0001");
    await providerSettingsApi.deleteCredential("ollama-lan", { expected_version: 2 }, "delete-0001");
    await providerSettingsApi.deleteConnection("ollama-lan", { expected_version: 3 }, "connection-delete-0001");
    await providerSettingsApi.refreshCatalog("ollama-lan", { expected_version: 2 }, "refresh-0001");
    await providerSettingsApi.correctCapabilities("ollama-lan", "qwen3", { effective_capabilities: ["text_generation"], expected_version: 3 }, "capability-0001");
    await providerSettingsApi.saveModelDefault("workspace-001", { capability: "text_generation", connection_id: "ollama-lan", model_id: "qwen3", expected_version: 0 }, '"defaults-v0"', "default-0001");
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(requests.map(({ url, options }) => [options.method ?? "GET", url]), [
    ["GET", "/bff/api/admin/provider-connections"],
    ["POST", "/bff/api/admin/provider-connections/model-preview"],
    ["GET", "/bff/api/workspaces/workspace-001/model-defaults"],
    ["POST", "/bff/api/admin/provider-connections"],
    ["PUT", "/bff/api/admin/provider-connections/ollama-lan"],
    ["POST", "/bff/api/admin/provider-connections/ollama-lan/credential"],
    ["DELETE", "/bff/api/admin/provider-connections/ollama-lan/credential"],
    ["DELETE", "/bff/api/admin/provider-connections/ollama-lan"],
    ["POST", "/bff/api/admin/provider-catalog/ollama-lan/refresh"],
    ["PATCH", "/bff/api/admin/provider-models/ollama-lan/qwen3/capabilities"],
    ["PATCH", "/bff/api/workspaces/workspace-001/model-defaults"],
  ]);
  assert.equal(requests.at(-1).options.headers["If-Match"], '"defaults-v0"');
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    connection_id: "custom-1", provider_code: "CUSTOM", adapter_type: "openai_compatible",
    base_url: "https://models.example/v1", credential: "fixture-preview-key",
  });
  for (const request of requests) assert.equal(request.options.credentials, "same-origin");
  const source = await read("apps/web/lib/provider-settings-api.js");
  assert.doesNotMatch(source, /["'`]\/api\/v1\//u);
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/iu);
});

test("new OpenAI-compatible public connection keeps generated ID after preview failure and clears Key only after save", async () => {
  const fixture = adminFixture({ previewFails: true, saveFailures: 1 });
  const view = await mountAdminFixture(fixture, "custom-openai");
  const { container, act } = view;
  try {
    assert.equal(findElements(container, (node) => node.tagName === "LABEL" && node.textContent.startsWith("Connection ID")).length, 0);
    assert.equal(buttonByText(container, "연결 시험 및 저장")?.disabled, true);
    await fill(act, controlFor(container, "호환 방식"), "openai_compatible");
    assert.equal(controlFor(container, "API 유형").value, "Chat Completions");
    assert.doesNotMatch(container.textContent, /Responses API|임베딩 API/u);
    await fill(act, controlFor(container, "Provider 표시 이름"), "자유 Provider");
    await fill(act, controlFor(container, "연결 이름"), "자유 연결");
    await fill(act, controlFor(container, "두 글자 약어"), "CU");
    await fill(act, controlFor(container, "Endpoint"), "https://models.example/v1");
    await fill(act, controlFor(container, "API Key 또는 Client Key"), "fixture-key");
    assert.equal(buttonByText(container, "모델 목록 조회")?.disabled, false);
    await click(act, buttonByText(container, "모델 목록 조회"));
    assert.match(container.textContent, /모델 ID를 직접 입력/u);
    assert.equal(controlFor(container, "API Key 또는 Client Key").value, "fixture-key");
    const generatedId = fixture.requests.find((item) => item.path.endsWith("/model-preview"))?.body.connection_id;
    assert.match(generatedId, /^provider-[A-Za-z0-9._:-]+$/u);
    assert.equal(fixture.requests.some((item) => item.path === "/bff/api/session/step-up"), false);
    assert.deepEqual(fixture.requests.find((item) => item.path.endsWith("/model-preview"))?.body, {
      connection_id: generatedId, provider_code: "CUSTOM", adapter_type: "openai_compatible",
      base_url: "https://models.example/v1", credential: "fixture-key",
    });

    await fill(act, controlFor(container, "모델 ID 직접 입력"), "m1\nm2\nm3\nm4\nm5");
    assert.equal(buttonByText(container, "연결 시험 및 저장")?.disabled, true);
    await fill(act, controlFor(container, "모델 ID 직접 입력"), "manual-a");
    assert.match(container.textContent, /시험 대상 1개 모델.*사용료/u);
    assert.equal(buttonByText(container, "연결 시험 및 저장")?.disabled, false);
    await click(act, buttonByText(container, "연결 시험 및 저장"));
    assert.match(container.textContent, /저장하지 못했습니다/u);
    assert.equal(controlFor(container, "API Key 또는 Client Key").value, "fixture-key");
    await click(act, buttonByText(container, "연결 시험 및 저장"));
    const creates = fixture.requests.filter((item) => item.path === "/bff/api/admin/provider-connections" && item.method === "POST");
    assert.equal(creates.length, 2);
    assert.equal(creates[0].body.connection_id, generatedId);
    assert.equal(creates[1].body.connection_id, generatedId);
    assert.equal(creates[1].body.provider_code, "CUSTOM");
    assert.equal(creates[1].body.adapter_type, "openai_compatible");
    assert.equal(creates[1].body.provider_name, "자유 Provider");
    assert.deepEqual(creates[1].body.allowed_model_ids, ["manual-a"]);
    assert.equal(creates[1].body.credential, "fixture-key");
    assert.equal(creates[1].body.test_credential, undefined);
    assert.equal(controlFor(container, "API Key 또는 Client Key").value, "");
    assert.match(container.textContent, /연결 시험에 성공/u);
    await click(act, buttonByText(container, "새로고침"));
    assert.equal(findElements(container, (node) => node.tagName === "LABEL" && node.textContent.startsWith("Connection ID")).length, 0);
    assert.equal(controlFor(container, "호환 방식").value, "OpenAI 호환");
    assert.equal(reactProps(controlFor(container, "호환 방식")).readOnly, true);
    assert.equal(controlFor(container, "모델 ID 직접 입력").value, "manual-a");
  } finally { await view.cleanup(); }
});

test("new Anthropic-compatible personal connection selects previewed model and sends ephemeral test Key", async () => {
  const fixture = adminFixture({ previewModels: ["anthropic-a", "anthropic-b"] });
  const view = await mountAdminFixture(fixture, "custom-anthropic");
  const { container, act } = view;
  try {
    await fill(act, controlFor(container, "호환 방식"), "anthropic_compatible");
    assert.equal(controlFor(container, "API 유형").value, "Messages");
    assert.match(container.textContent, /첫 페이지만 표시될 수 있습니다/u);
    await fill(act, controlFor(container, "Provider 표시 이름"), "별도 Provider");
    await fill(act, controlFor(container, "연결 이름"), "별도 연결");
    await fill(act, controlFor(container, "두 글자 약어"), "AT");
    await fill(act, controlFor(container, "Endpoint"), "https://anthropic.example/v1");
    await fill(act, controlFor(container, "API Key 또는 Client Key"), "fixture-once-key");
    const publicCheckbox = findElements(container, (node) => node.tagName === "INPUT" && (node.type === "checkbox" || node.getAttribute("type") === "checkbox") && node.parentNode?.textContent.includes("공용 사용"))[0];
    assert.ok(publicCheckbox);
    await setChecked(act, publicCheckbox, false);
    assert.equal(buttonByText(container, "모델 목록 조회")?.disabled, false);
    await click(act, buttonByText(container, "모델 목록 조회"));
    assert.match(container.textContent, /anthropic-a/u);
    const modelCheckbox = findElements(container, (node) => node.tagName === "INPUT" && (node.type === "checkbox" || node.getAttribute("type") === "checkbox") && node.parentNode?.textContent.includes("anthropic-a"))[0];
    assert.ok(modelCheckbox);
    await setChecked(act, modelCheckbox, true);
    assert.match(container.textContent, /시험 대상 1개 모델.*사용료/u);
    await click(act, buttonByText(container, "연결 시험 및 저장"));
    const create = fixture.requests.find((item) => item.path === "/bff/api/admin/provider-connections" && item.method === "POST");
    assert.match(create.body.connection_id, /^provider-[A-Za-z0-9._:-]+$/u);
    assert.equal(create.body.adapter_type, "anthropic_compatible");
    assert.equal(create.body.access_mode, "personal");
    assert.deepEqual(create.body.allowed_model_ids, ["anthropic-a"]);
    assert.equal(create.body.test_credential, "fixture-once-key");
    assert.equal(create.body.credential, undefined);
    assert.equal(controlFor(container, "API Key 또는 Client Key").value, "");
    assert.doesNotMatch(container.textContent, /fixture-once-key/u);
  } finally { await view.cleanup(); }
});

test("saved legacy CUSTOM connection retains its old catalog and credential actions", async () => {
  const fixture = adminFixture({ existingConnections: [{
    connection_id: "legacy-custom", provider_code: "CUSTOM", adapter_type: "CUSTOM", provider_name: "기존 공급자", display_name: "기존 연결",
    base_url: "https://legacy.example/v1", short_code: "LC", access_mode: "public", credential_requirement: "required",
    allowed_model_ids: ["legacy-a"], enabled: true, configured: true, verification_status: "verified", version: 2,
    catalog_status: "ready", models: [{ model_id: "legacy-a", catalog_status: "ready", effective_capabilities: ["text_generation"] }],
  }] });
  const view = await mountAdminFixture(fixture, "legacy-custom");
  try {
    assert.equal(controlFor(view.container, "호환 방식").value, "기존 CUSTOM (OpenAI 호환)");
    assert.equal(reactProps(controlFor(view.container, "호환 방식")).readOnly, true);
    assert.equal(buttonByText(view.container, "모델 목록 조회"), undefined);
    assert.ok(buttonByText(view.container, "모델 조회"));
    assert.ok(buttonByText(view.container, "시스템 키 시험 및 저장"));
    assert.doesNotMatch(view.container.textContent, /시험 대상 .*사용료|모델 ID 직접 입력/u);
  } finally { await view.cleanup(); }
});

test("saved public compatible CUSTOM refreshes with stored Key without replacing its manual allowlist", async () => {
  const connection = {
    connection_id: "custom-public", provider_code: "CUSTOM", adapter_type: "openai_compatible", provider_name: "공용 공급자", display_name: "공용 연결",
    base_url: "https://models.example/v1", short_code: "CP", access_mode: "public", credential_requirement: "required",
    allowed_model_ids: ["manual-a"], enabled: true, configured: true, verification_status: "verified", version: 2,
    catalog_status: "ready", catalog_version: 2,
    models: [{ model_id: "manual-a", catalog_status: "ready", catalog_version: 2, effective_capabilities: ["text_generation"] }],
  };
  const fixture = adminFixture({ existingConnections: [connection], refreshResult: {
    ...connection, version: 3, catalog_version: 3,
    models: [...connection.models, { model_id: "listed-only", catalog_status: "ready", catalog_version: 3, effective_capabilities: ["text_generation"] }],
  } });
  const view = await mountAdminFixture(fixture, "stored-custom-refresh");
  try {
    assert.ok(buttonByText(view.container, "모델 조회"), "stored-Key catalog refresh action is missing");
    assert.ok(buttonByText(view.container, "모델 목록 조회"), "input-Key preview remains separate");
    assert.equal(buttonByText(view.container, "모델 목록 조회").disabled, true);
    await click(view.act, buttonByText(view.container, "모델 조회"));
    const refresh = fixture.requests.find((item) => item.path === "/bff/api/admin/provider-catalog/custom-public/refresh");
    assert.deepEqual(refresh?.body, { expected_version: 2 });
    assert.equal(fixture.requests.some((item) => item.path.endsWith("/model-preview")), false);
    assert.equal(controlFor(view.container, "모델 ID 직접 입력").value, "manual-a");
    assert.match(view.container.textContent, /listed-only/u);
    assert.match(view.container.textContent, /모델 카탈로그를 새로고침했습니다/u);
  } finally { await view.cleanup(); }
});

test("ordinary personal CUSTOM user sees only admin-allowed models and no admin controls", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, "node_modules", ".provider-personal-user-"));
  const dom = installMinimalDom();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url) === "/bff/api/session") return Response.json({ data: { workspace_id: "workspace-001", is_system_admin: false } });
    if (String(url) === "/bff/api/admin/provider-connections") return Response.json({ data: [{
      connection_id: "custom-personal", provider_code: "CUSTOM", adapter_type: "anthropic_compatible", provider_name: "개인 공급자", display_name: "개인 연결",
      base_url: "https://models.example/v1", short_code: "PC", access_mode: "personal", credential_requirement: "required",
      allowed_model_ids: ["allowed-a"], enabled: true, configured: false, verification_status: "verified", catalog_status: "ready", version: 1,
      models: ["allowed-a", "catalog-only-b"].map((model_id) => ({ model_id, catalog_status: "ready", effective_capabilities: ["text_generation"] })),
    }] });
    if (String(url) === "/bff/api/provider-credentials") return Response.json({ data: { credentials: [{ connection_id: "custom-personal", credential_version: 1, verification_status: "verified" }] } });
    throw new Error(`UNEXPECTED_REQUEST: ${url}`);
  };
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProviderSettingsWorkspace } = await bundleProvider(root, output, "provider-personal-user");
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProviderSettingsWorkspace, { workspaceId: "workspace-001", embedded: true })); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /allowed-a|내 계정 키 시험 및 저장/u);
    assert.doesNotMatch(container.textContent, /catalog-only-b|연결 시험 및 저장|모델 목록 조회|관리자 재인증/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    globalThis.fetch = originalFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("system admin sees named multi-connections, password-only secrets, catalogs and capability readiness", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, "node_modules", ".provider-settings-react-"));
  const dom = installMinimalDom();
  const originalFetch = globalThis.fetch;
  let reactRoot;
  const endpoint = "http://192.0.2.44:11434";
  const rawCredential = "fixture-client-key-never-render";
  const connections = [
    {
      connection_id: "ollama-lan", provider_code: "OLLAMA", provider_name: "내부 추론 엔진", display_name: "LAN Ollama",
      base_url: endpoint,
      short_code: "OL", access_mode: "public", credential_requirement: "none", adapter_type: "OLLAMA", allowed_model_ids: ["qwen3"],
      enabled: true, configured: false, credential_version: 0, verification_status: "verified",
      verified_at: "2026-09-17T00:00:00Z", version: 2, catalog_status: "ready", catalog_version: 4,
      models: [{ connection_id: "ollama-lan", model_id: "qwen3", reported_capabilities: ["text_generation", "embedding"], effective_capabilities: ["text_generation", "embedding"], override_applied: false, catalog_status: "ready", catalog_version: 4 }],
    },
    {
      connection_id: "ollama-lab", provider_code: "OLLAMA", display_name: "Lab Ollama",
      short_code: "LA", access_mode: "public", credential_requirement: "none", adapter_type: "OLLAMA", allowed_model_ids: ["qwen3"],
      enabled: true, configured: false, credential_version: 0, verification_status: "verified",
      verified_at: "2026-09-17T00:00:00Z", version: 1, catalog_status: "ready", catalog_version: 1,
      models: [{ connection_id: "ollama-lab", model_id: "qwen3", reported_capabilities: ["text_generation"], effective_capabilities: ["text_generation"], override_applied: false, catalog_status: "ready", catalog_version: 1 }],
    },
  ];
  globalThis.fetch = async (url) => {
    if (String(url) === "/bff/api/session") return Response.json({ data: { workspace_id: "workspace-001", is_system_admin: true }, meta: { trace_id: "trace-session" } });
    if (String(url) === "/bff/api/admin/provider-connections") return Response.json({ data: connections, meta: { trace_id: "trace-list" } }, { headers: { etag: '"provider-connections:4"' } });
    if (String(url) === "/bff/api/provider-credentials") return Response.json({ data: { credentials: [] } });
    if (String(url) === "/bff/api/admin/provider-health-settings") return Response.json({ data: { interval_minutes: 60, version: 1 } });
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

    for (const label of ["Provider 표시 이름", "연결 이름", "Endpoint", "LAN Ollama · qwen3", "Lab Ollama · qwen3", "공용 사용", "연결 삭제", "모델 조회", "텍스트 생성", "임베딩"]) {
      assert.match(container.textContent, new RegExp(label, "u"));
    }
    assert.ok(findElements(container, (node) => node.tagName === "INPUT" && node.value === endpoint).length >= 1);
    assert.ok(findElements(container, (node) => node.tagName === "INPUT" && node.value === "내부 추론 엔진").length >= 1);
    const passwordInputs = findElements(container, (node) => node.tagName === "INPUT" && (node.type === "password" || node.getAttribute("type") === "password"));
    assert.equal(passwordInputs.length, 0);
    assert.doesNotMatch(container.textContent, /관리자 재인증 비밀번호/u);
    assert.doesNotMatch(container.textContent, new RegExp(`${endpoint}|${rawCredential}|역할 매핑 저장|기능별 모델 선택|모델 기능 보정`, "u"));
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    globalThis.fetch = originalFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("ordinary user sees public connection without personal key actions", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, "node_modules", ".provider-settings-workspace-react-"));
  const dom = installMinimalDom();
  const originalFetch = globalThis.fetch;
  const requests = [];
  let reactRoot;
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    if (String(url) === "/bff/api/session") return Response.json({ data: { workspace_id: "workspace-001", is_system_admin: false } });
    if (String(url) === "/bff/api/admin/provider-connections") return Response.json({ data: [{ connection_id: "ollama-lan", provider_code: "OLLAMA", display_name: "LAN Ollama", short_code: "OL", access_mode: "public", credential_requirement: "none", adapter_type: "OLLAMA", allowed_model_ids: ["qwen3"], enabled: true, configured: false, verification_status: "verified", version: 1, models: [{ model_id: "qwen3", catalog_status: "ready", effective_capabilities: ["text_generation"] }] }] });
    if (String(url) === "/bff/api/provider-credentials") return Response.json({ data: { credentials: [] } });
    throw new Error("UNEXPECTED_REQUEST");
  };
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProviderSettingsWorkspace } = await bundleProvider(root, output, "provider-workspace");
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProviderSettingsWorkspace, { workspaceId: "workspace-001", embedded: true })); await Promise.resolve(); await Promise.resolve(); });
    assert.deepEqual(requests, ["/bff/api/session", "/bff/api/admin/provider-connections", "/bff/api/provider-credentials"]);
    assert.equal(findElements(container, (node) => node.tagName === "INPUT" && (node.type === "password" || node.getAttribute("type") === "password")).length, 0);
    assert.match(container.textContent, /공용|qwen3/u);
    assert.doesNotMatch(container.textContent, /내 계정 키 저장|내 계정 키 삭제|연결 저장|연결 삭제|모델 조회/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    globalThis.fetch = originalFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("personal key readiness and UPSTAGE selected models stay separate from the full catalog", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, "node_modules", ".provider-settings-personal-"));
  const dom = installMinimalDom();
  const originalFetch = globalThis.fetch;
  let reactRoot;
  const connection = {
    connection_id: "upstage-personal", provider_code: "UPSTAGE", display_name: "Upstage personal",
    base_url: "https://api.upstage.ai/v1", short_code: "UP", access_mode: "personal",
    credential_requirement: "required", adapter_type: "UPSTAGE", enabled: true,
    configured: false, verification_status: "unverified", version: 2,
    catalog_status: "ready", catalog_version: 3, allowed_model_ids: ["solar-pro4"],
    models: ["solar-pro4", "other-model"].map((model_id) => ({ model_id, catalog_status: "ready", catalog_version: 3, effective_capabilities: ["text_generation"] })),
  };
  globalThis.fetch = async (url) => {
    if (String(url) === "/bff/api/session") return Response.json({ data: { workspace_id: "workspace-001", is_system_admin: false } });
    if (String(url) === "/bff/api/admin/provider-connections") return Response.json({ data: [connection] });
    if (String(url) === "/bff/api/provider-credentials") return Response.json({ data: { credentials: [{ connection_id: "upstage-personal", provider_code: "UPSTAGE", configured: true, credential_version: 1, verification_status: "verified" }] } });
    throw new Error("UNEXPECTED_REQUEST");
  };
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProviderSettingsWorkspace } = await bundleProvider(root, output, "provider-personal");
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProviderSettingsWorkspace, { workspaceId: "workspace-001", embedded: true })); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /비공용 · 개인 Key 필요 · 사용 가능/u);
    assert.match(container.textContent, /내 계정 키 시험 및 저장|내 계정 키 삭제/u);
    assert.match(container.textContent, /solar-pro4/u);
    assert.doesNotMatch(container.textContent, /other-model/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    globalThis.fetch = originalFetch; dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("provider view helpers expose safe status and connection-name model choices only", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, "node_modules", ".provider-settings-helpers-"));
  try {
    const { formatModelChoice, projectProviderConnection, safeProviderErrorMessage } = await bundleProvider(root, output, "provider-helpers");
    assert.equal(formatModelChoice({ display_name: "LAN Ollama" }, { model_id: "qwen3" }), "LAN Ollama · qwen3");
    assert.deepEqual(projectProviderConnection({ enabled: true, configured: true, verification_status: "verified" }), { label: "활성 · Credential 설정됨 · 확인됨", verified: true });
    assert.deepEqual(projectProviderConnection({ enabled: true, access_mode: "personal", credential_requirement: "required", verification_status: "unverified" }), { label: "비공용 · 개인 Key 필요 · 사용 대기", verified: false });
    assert.deepEqual(projectProviderConnection({ enabled: true, access_mode: "personal", credential_requirement: "required", verification_status: "unverified" }, { verification_status: "verified" }), { label: "비공용 · 개인 Key 필요 · 사용 가능", verified: true });
    assert.deepEqual(projectProviderConnection({ enabled: true, access_mode: "public", credential_requirement: "none", verification_status: "verified" }), { label: "공용 · Key 불필요 · 사용 가능", verified: true });
    assert.equal(safeProviderErrorMessage("credential", { code: "INTERNAL_DOCKER_HOST_api:8000" }), "Credential을 저장하지 못했습니다. 다시 시도해 주세요.");
    assert.doesNotMatch(safeProviderErrorMessage("credential", { code: "INTERNAL_DOCKER_HOST_api:8000" }), /api:8000|INTERNAL/iu);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});
