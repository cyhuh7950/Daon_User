import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { createProductStudioState, selectOutputType } from "../../packages/ui/src/product-studio-model.js";
import { createProductWorkspaceState } from "../../packages/ui/src/product-workspace-model.js";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

const PROVIDERS = ["CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI", "OPENROUTER", "ANTHROPIC", "OLLAMA"];

async function bundle(entry, fileName, output) {
  const root = path.resolve(import.meta.dirname, "../..");
  const { build } = await import("vite");
  await build({
    configFile: false,
    logLevel: "silent",
    root,
    build: {
      outDir: output,
      emptyOutDir: false,
      lib: { entry: path.join(root, entry), formats: ["es"], fileName },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client", "react-dom/server"] },
    },
  });
  const built = (await readdir(output)).find((name) => name.startsWith(fileName) && /\.m?js$/u.test(name));
  return import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
}

test("Web Workspace는 단일 App Bar와 3면 최종형을 렌더하고 상시 설명·내부 정책 코드를 숨긴다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-shell-react-"));
  try {
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { ProductWorkspaceShell } = await bundle("packages/ui/src/product-workspace-shell.jsx", "web-final-shell", output);
    const state = {
      ...createProductWorkspaceState({ status: "ready" }),
      sources: [{ sourceId: "source-1", sourceVersionId: "version-1", filename: "운영 기준.pdf", ready: true }],
      selectedSource: { sourceId: "source-1", sourceVersionId: "version-1", filename: "운영 기준.pdf", ready: true },
      answer: { run_id: "run-1", run_result_id: "result-1", answer: "근거가 결속된 답변입니다.", insufficient: false, citations: [] },
    };
    const html = renderToStaticMarkup(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state, adapter: {} }));
    assert.match(html, /class="[^"]*workspace-app-bar[^"]*"/u);
    assert.match(html, /class="workspace-panes[^"]*"[^>]*data-layout="source-conversation-studio"/u);
    assert.match(html, /class="source-list-row/u);
    assert.match(html, /class="conversation-transcript/u);
    assert.match(html, /class="conversation-composer/u);
    assert.match(html, /설정/u);
    assert.doesNotMatch(html, /visible-state|WORKSPACE_POLICY|WEIGHT_PROFILE|RULESET_BINDING/u);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("보고서 Tile 선택은 설정 View를 열고 3열 Grid·Library·상태 배타성을 유지한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-studio-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProductStudioPane } = await bundle("packages/ui/src/product-studio-pane.jsx", "web-final-studio", output);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => {
      reactRoot.render(createElement(ProductStudioPane, {
        adapter: null,
        state: createProductStudioState({ status: "ready", grounded: { sourceId: "source-1", sourceVersionId: "version-1", runId: "run-1", runResultId: "result-1" } }),
      }));
    });
    assert.equal(findElements(container, (node) => node.getAttribute?.("data-studio-view") === "config").length, 0);
    const grid = findElements(container, (node) => node.getAttribute?.("data-columns") === "3")[0];
    assert.ok(grid, "Studio 생성 유형은 3열 Grid여야 한다");
    for (const label of ["슬라이드", "인포그래픽", "플래시카드", "퀴즈", "AI 오디오", "동영상"]) assert.match(container.textContent, new RegExp(label, "u"));
    await act(async () => { buttonByText(container, "근거 기반 보고서").dispatchEvent(new MinimalEvent("click")); });
    const config = findElements(container, (node) => node.getAttribute?.("data-studio-view") === "config")[0];
    assert.ok(config);
    for (const label of ["목적", "독자", "분량", "구성", "출력 형식", "검토 조건", "현재 모델", "정책 요약", "뒤로", "설정 확인", "생성"]) {
      assert.match(config.textContent, new RegExp(label, "u"));
    }
    assert.equal(findElements(container, (node) => node.getAttribute?.("data-generation-state") === "idle").length, 1);
    assert.equal(findElements(container, (node) => ["pending", "failed", "completed"].includes(node.getAttribute?.("data-generation-state"))).length, 0);
    assert.match(container.textContent, /저장된 산출물/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("설정 anchored menu에서 LLM 설정 accessible modal을 열고 9 Provider를 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-settings-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProductWorkspaceShell } = await bundle("packages/ui/src/product-workspace-shell.jsx", "web-final-settings", output);
    const providerSettings = createElement("div", { className: "provider-card-grid" }, PROVIDERS.map((provider) => createElement("button", { key: provider, type: "button" }, provider)));
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state: createProductWorkspaceState({ status: "ready" }), adapter: {}, providerSettings })); });
    const settingsButton = buttonByText(container, "설정");
    await act(async () => { settingsButton.dispatchEvent(new MinimalEvent("click")); });
    assert.match(container.textContent, /LLM 설정/u);
    const llmSettings = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("LLM 설정"))[0];
    await act(async () => { llmSettings.dispatchEvent(new MinimalEvent("click")); });
    const dialog = findElements(container, (node) => node.getAttribute?.("role") === "dialog" && node.getAttribute?.("aria-modal") === "true")[0];
    assert.ok(dialog);
    for (const provider of PROVIDERS) assert.match(dialog.textContent, new RegExp(provider, "u"));
    assert.ok(findElements(container, (node) => node.getAttribute?.("inert") !== null).length >= 1);
    const focusables = findElements(dialog, (node) => node.tagName === "BUTTON" && !node.disabled);
    dialog.querySelectorAll = () => focusables;
    focusables.at(-1).focus();
    const tab = Object.assign(new MinimalEvent("keydown"), { key: "Tab", shiftKey: false });
    await act(async () => { dialog.dispatchEvent(tab); });
    assert.equal(dom.document.activeElement, focusables[0]);
    assert.equal(tab.defaultPrevented, true);
    focusables[0].focus();
    const shiftTab = Object.assign(new MinimalEvent("keydown"), { key: "Tab", shiftKey: true });
    await act(async () => { dialog.dispatchEvent(shiftTab); });
    assert.equal(dom.document.activeElement, focusables.at(-1));
    assert.equal(shiftTab.defaultPrevented, true);
    const escape = Object.assign(new MinimalEvent("keydown"), { key: "Escape", shiftKey: false });
    await act(async () => { dialog.dispatchEvent(escape); await Promise.resolve(); });
    assert.equal(findElements(container, (node) => node.getAttribute?.("role") === "dialog").length, 0);
    assert.equal(dom.document.activeElement, settingsButton);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("생성 중·실패·완료는 동시에 노출되지 않고 안전한 사용자 상태만 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-states-react-"));
  try {
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { ProductStudioPane } = await bundle("packages/ui/src/product-studio-pane.jsx", "web-final-states", output);
    const grounded = { sourceId: "source-1", sourceVersionId: "version-1", runId: "run-1", runResultId: "result-1" };
    const base = selectOutputType(createProductStudioState({ grounded }), "evidence_report");
    const states = [
      ["pending", { ...base, pending: true }],
      ["failed", { ...base, safeError: "STUDIO_CREATE_FAILED" }],
      ["completed", { ...base, outputs: [{ studio_output_id: "output-1", output_version_id: "version-1", title: "완료 보고서", status: "draft" }], selectedOutputId: "output-1" }],
    ];
    for (const [expected, state] of states) {
      const html = renderToStaticMarkup(createElement(ProductStudioPane, { state, adapter: null }));
      assert.equal((html.match(/data-generation-state=/gu) ?? []).length, 1);
      assert.match(html, new RegExp(`data-generation-state="${expected}"`, "u"));
      assert.doesNotMatch(html, /STUDIO_CREATE_FAILED|WORKSPACE_POLICY|RULESET_BINDING/u);
    }
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("실제 Provider 설정 View는 9개 카드와 한 Provider 상세만 렌더한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".web-final-provider-react-"));
  try {
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { ProviderSettingsWorkspace, projectProviderConnection, projectProviderEndpoint, safeProviderErrorMessage } = await bundle("apps/web/components/provider-settings-workspace.jsx", "web-final-provider", output);
    const html = renderToStaticMarkup(createElement(ProviderSettingsWorkspace, { workspaceId: "workspace-1", embedded: true }));
    for (const provider of PROVIDERS) assert.match(html, new RegExp(provider, "u"));
    assert.equal((html.match(/class="provider-card"/gu) ?? []).length, 9);
    assert.equal((html.match(/>Endpoint</gu) ?? []).length, 1);
    assert.match(html, /Endpoint 변경/u);
    assert.match(html, /UPSTAGE/u);
    assert.match(html, /Credential 미설정|미설정/u);
    assert.doesNotMatch(html, /api[_-]?key|secret_value|type="password"/iu);
    assert.deepEqual(projectProviderConnection({ active: true, credential_configured: true }), { label: "활성 · Credential 설정됨 · 연결 미확인", verified: false });
    assert.equal(projectProviderEndpoint("http://api:8000/internal"), "Endpoint 설정됨");
    assert.doesNotMatch(projectProviderEndpoint("http://api:8000/internal"), /api:8000|http/u);
    assert.equal(safeProviderErrorMessage("provider", { code: "INTERNAL_DOCKER_HOST_api:8000" }), "Provider 설정을 저장하지 못했습니다. 다시 시도해 주세요.");
    const source = await readFile(path.join(root, "apps/web/components/provider-settings-workspace.jsx"), "utf8");
    assert.doesNotMatch(source, /value=\{selectedDraft\.base_url\}|\$\{error\.code/u);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("Workspace 시각 규칙은 reduced motion과 9px 이상 보조 글꼴을 유지한다", async () => {
  const css = await readFile(path.resolve(import.meta.dirname, "../../packages/ui/src/workspace.css"), "utf8");
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*animation-duration:\s*\.01ms/u);
  const finalWorkspaceCss = css.slice(css.indexOf(".adaptive-workspace[data-product-workspace-state]"));
  assert.doesNotMatch(finalWorkspaceCss, /font-size:\s*8px/u);
});
