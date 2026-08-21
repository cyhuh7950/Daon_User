import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";

import {
  assertProductWorkspaceAdapter,
  createProductWorkspaceState,
  canCreateGroundedReport,
  projectQuestionFailureState,
} from "../../packages/ui/src/product-workspace-model.js";

const adapter = Object.freeze({
  listSources() {}, uploadPdf() {}, getProcessingStatus() {}, askQuestion() {},
  citationUrl() {}, createReport() {}, listStudioOutputs() {},
});
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};

async function mountSourceRetryWorkspace(effectAdapter, suffix) {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, `.workspace-source-retry-${suffix}-`));
  const dom = installMinimalDom();
  const { build } = await import("vite");
  const { createElement, act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  await build({ configFile: false, logLevel: "silent", root, build: {
    outDir: output, emptyOutDir: false,
    lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-retry" },
    rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
  } });
  const entry = (await readdir(output)).find((name) => name.startsWith("source-retry") && /\.m?js$/u.test(name));
  const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?sourceRetry=${Date.now()}`);
  const container = dom.document.createElement("div");
  dom.document.body.appendChild(container);
  const reactRoot = createRoot(container);
  await act(async () => {
    reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter }));
    await Promise.resolve();
    await Promise.resolve();
  });
  return {
    container,
    act,
    wait: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    cleanup: async () => {
      await act(async () => reactRoot.unmount());
      dom.restore();
      await rm(output, { recursive: true, force: true });
    },
  };
}

const sourceRetryPayload = {
  source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf",
  source_state: "ready", processing_state: "completed", job_state: "completed",
};

test("Source transient fetch TypeError는 bounded 1회 retry 후 정상 복구한다", async () => {
  let calls = 0;
  const workspace = await mountSourceRetryWorkspace({
    ...adapter,
    async listSources() {
      calls += 1;
      if (calls === 1) throw new TypeError("Failed to fetch");
      return [sourceRetryPayload];
    },
    async listStudioOutputs() { return []; },
  }, "transient");
  try {
    await workspace.act(async () => workspace.wait(350));
    assert.equal(calls, 2);
    assert.match(workspace.container.textContent, /ready\.pdf/u);
    assert.doesNotMatch(workspace.container.textContent, /Source를 불러오지 못했습니다/u);
  } finally {
    await workspace.cleanup();
  }
});

test("Source retry 대기 중 AbortError는 재호출하지 않는다", async () => {
  let calls = 0;
  let observedSignal;
  const workspace = await mountSourceRetryWorkspace({
    ...adapter,
    async listSources({ signal }) {
      calls += 1;
      observedSignal = signal;
      await new Promise((resolve, reject) => {
        signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
      });
      return [sourceRetryPayload];
    },
    async listStudioOutputs() { return []; },
  }, "abort");
  try {
    assert.equal(calls, 1);
    assert.equal(observedSignal.aborted, false);
  } finally {
    await workspace.cleanup();
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.equal(calls, 1);
  }
});

test("Source contract 오류는 retry하지 않고 1회 호출로 안전 오류를 유지한다", async () => {
  let calls = 0;
  const workspace = await mountSourceRetryWorkspace({
    ...adapter,
    async listSources() {
      calls += 1;
      throw new Error("SOURCE_LIST_RESPONSE_INVALID");
    },
    async listStudioOutputs() { return []; },
  }, "contract");
  try {
    await workspace.act(async () => workspace.wait(350));
    assert.equal(calls, 1);
    assert.match(workspace.container.textContent, /Source를 불러오지 못했습니다/u);
  } finally {
    await workspace.cleanup();
  }
});

test("질문 실패는 로드된 Source·Studio 잠금을 보존하고 안전 오류만 투영한다", () => {
  const current = {
    ...createProductWorkspaceState({ status: "ready" }),
    sources: [{ sourceId: "source-1", sourceVersionId: "version-1", filename: "ready.pdf", ready: true }],
    selectedSource: { sourceId: "source-1", sourceVersionId: "version-1", filename: "ready.pdf", ready: true },
    studioLocks: [{ lock_type: "ruleset", version: "v1" }],
    studioOutputs: [{ studio_output_id: "output-1" }],
  };

  const failed = projectQuestionFailureState(current, new Error("TEXT_MODEL_NOT_SELECTED"));

  assert.equal(failed.status, "ready");
  assert.equal(failed.safeError, null);
  assert.equal(failed.conversationSafeError, "TEXT_MODEL_NOT_SELECTED");
  assert.deepEqual(failed.sources, current.sources);
  assert.deepEqual(failed.selectedSource, current.selectedSource);
  assert.deepEqual(failed.studioLocks, current.studioLocks);
  assert.deepEqual(failed.studioOutputs, current.studioOutputs);
});

test("Product Workspace Adapter는 exact 7개 메서드를 요구한다", () => {
  assert.equal(assertProductWorkspaceAdapter(adapter), adapter);
  assert.throws(
    () => assertProductWorkspaceAdapter({ ...adapter, createReport: undefined }),
    /WORKSPACE_ADAPTER_INVALID/,
  );
});

test("근거 보고서는 Raw·Daon 혼합 Context의 sufficient Citation 답변이면 생성 가능하다", () => {
  const ready = {
    ...createProductWorkspaceState({ status: "ready" }),
    selectedSource: { sourceId: "source-1", sourceVersionId: "version-1" },
    answer: {
      run_id: "run-1", run_result_id: "result-1", answer: "근거 답변",
      insufficient: false,
      citations: [{ citation_id: "citation-1", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-1", page: 2, origin: "raw_source", context_item_id: "source-1", locator: { kind: "page", value: "2" } }],
    },
  };
  assert.equal(canCreateGroundedReport(ready), true);
  assert.equal(canCreateGroundedReport({ ...ready, answer: { ...ready.answer, insufficient: true } }), false);
  assert.equal(canCreateGroundedReport({ ...ready, answer: { ...ready.answer, citations: [] } }), false);
  const daonOnly = {
    ...ready, selectedSource: null,
    answer: { ...ready.answer, citations: [{
      ...ready.answer.citations[0], source_id: "source-daon", source_version_id: "version-daon",
      origin: "daon_knowledge", context_item_id: "package-daon", locator: { kind: "section", value: "summary" },
    }] },
  };
  assert.equal(canCreateGroundedReport(daonOnly), true);
  assert.equal(canCreateGroundedReport({ ...daonOnly, answer: { ...daonOnly.answer, citations: [{ ...daonOnly.answer.citations[0], source_version_id: "" }] } }), false);
});

test("Source 작업은 Notebook 연결 해제와 30일 삭제 요청·취소를 분리한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-actions-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-actions" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("source-actions") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?sourceActions=${Date.now()}`);
    const calls = [];
    const effectAdapter = {
      ...adapter,
      bindingEtag: '"notebook-binding:1"',
      async listSources() { return [{ source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }]; },
      async listStudioOutputs() { return []; },
      async unbindSource(source, options) { calls.push(["unbind", source.sourceId, options.bindingEtag]); },
      async requestSourceDeletion(source) { calls.push(["request", source.sourceId]); return { data: { request_id: "request-1", source_id: source.sourceId, state: "grace_period" }, etag: '"deletion:request-1:1"' }; },
      async cancelSourceDeletionRequest(requestId, options) { calls.push(["cancel", requestId, options.etag]); return { data: { request_id: requestId, source_id: "source-1", state: "cancelled" }, etag: '"deletion:request-1:2"' }; },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const action = findElements(container, (node) => node.tagName === "BUTTON" && /ready\.pdf 작업/u.test(node.getAttribute?.("aria-label") ?? ""))[0];
    await act(async () => action.dispatchEvent(new MinimalEvent("click")));
    assert.match(container.textContent, /원본 Source는 보존/u);
    const deletion = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "Source 삭제 요청")[0];
    await act(async () => deletion.dispatchEvent(new MinimalEvent("click")));
    const confirm = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 확인")[0];
    await act(async () => { confirm.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls, [["request", "source-1"]]);
    assert.match(container.textContent, /30일 유예/u);
    const cancel = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 취소")[0];
    await act(async () => { cancel.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls, [["request", "source-1"], ["cancel", "request-1", '"deletion:request-1:1"']]);
    assert.match(container.textContent, /원본 Source는 보존/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("새로고침 뒤 active 삭제 요청을 복원하고 취소 동작을 제공한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-deletion-restore-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "deletion-restore" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("deletion-restore") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?restore=${Date.now()}`);
    const calls = [];
    const effectAdapter = { ...adapter, async listSources() { return []; }, async listStudioOutputs() { return []; },
      async listSourceDeletionRequests() { return [{ request_id: "request-1", source_id: "source-1", state: "grace_period", version: 2, grace_until: "2026-09-20T00:00:00Z", legal_hold_active: false }]; },
      async cancelSourceDeletionRequest(requestId, options) { calls.push([requestId, options.etag]); return { data: { request_id: requestId, source_id: "source-1", state: "cancelled" }, etag: '"deletion:request-1:3"' }; } };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const action = findElements(container, (node) => node.tagName === "BUTTON" && /source-1 삭제 요청 상태/u.test(node.getAttribute?.("aria-label") ?? ""))[0];
    assert.ok(action); await act(async () => action.dispatchEvent(new MinimalEvent("click")));
    assert.match(container.textContent, /30일 유예/u);
    const cancel = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 취소")[0];
    await act(async () => { cancel.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls, [["request-1", '"deletion:request-1:2"']]);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("Source lifecycle pending 응답은 Adapter·Notebook 전환 즉시 폐기된다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-lifecycle-epoch-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "lifecycle-epoch" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("lifecycle-epoch") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?epoch=${Date.now()}`);
    const pending = deferred(); const calls = [];
    const oldAdapter = { ...adapter, notebookContext: { notebook_id: "notebook-old" },
      async listSources() { return [{ source_id: "source-old", source_version_id: "version-old", filename: "old.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }]; },
      async listStudioOutputs() { return []; }, async requestSourceDeletion() { calls.push("old-request"); return pending.promise; } };
    const newAdapter = { ...adapter, notebookContext: { notebook_id: "notebook-new" }, async listSources() { return []; }, async listStudioOutputs() { return []; },
      async listSourceDeletionRequests() { return [{ request_id: "request-new", source_id: "source-new", state: "grace_period", version: 1, grace_until: "2026-09-20T00:00:00Z", legal_hold_active: false }]; },
      async cancelSourceDeletionRequest() { calls.push("new-cancel"); return { data: { request_id: "request-new", source_id: "source-new", state: "cancelled" }, etag: '"deletion:request-new:2"' }; } };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-old", adapter: oldAdapter })); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => findElements(container, (node) => node.tagName === "BUTTON" && /old\.pdf 작업/u.test(node.getAttribute?.("aria-label") ?? ""))[0].dispatchEvent(new MinimalEvent("click")));
    await act(async () => findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "Source 삭제 요청")[0].dispatchEvent(new MinimalEvent("click")));
    await act(async () => findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 확인")[0].dispatchEvent(new MinimalEvent("click")));
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-new", adapter: newAdapter })); await Promise.resolve(); await Promise.resolve(); });
    assert.doesNotMatch(container.textContent, /old\.pdf|요청을 처리하지 못/u);
    pending.resolve({ data: { request_id: "request-old", source_id: "source-old", state: "grace_period" }, etag: '"deletion:request-old:1"' });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    assert.doesNotMatch(container.textContent, /old\.pdf|request-old/u);
    const action = findElements(container, (node) => node.tagName === "BUTTON" && /source-new 삭제 요청 상태/u.test(node.getAttribute?.("aria-label") ?? ""))[0];
    await act(async () => action.dispatchEvent(new MinimalEvent("click")));
    await act(async () => { findElements(container, (node) => node.tagName === "BUTTON" && node.textContent === "삭제 요청 취소")[0].dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls, ["old-request", "new-cancel"]);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("Studio 목록 실패는 ready Source 질문을 보존하고 별도 안전 경고로 투영한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-studio-unavailable-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "studio-unavailable" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("studio-unavailable") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?studioUnavailable=${Date.now()}`);
    const effectAdapter = {
      ...adapter,
      async listSources() { return [{ source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }]; },
      async listStudioOutputs() { throw new Error("STUDIO_DATABASE_UNAVAILABLE http://internal.invalid stack"); },
      async loadNotebookConversation() { throw new Error("CONVERSATION_DATABASE_UNAVAILABLE http://internal.invalid stack"); },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /ready\.pdf/u);
    const questionInput = findElements(container, (node) => node.tagName === "TEXTAREA" && node.parentNode?.textContent.startsWith("질문"))[0];
    assert.equal(questionInput.disabled, false);
    assert.match(container.textContent, /불러오지 못했습니다/u);
    assert.match(container.textContent, /대화를 불러오지 못했습니다/u);
    assert.doesNotMatch(container.textContent, /STUDIO_LIST_FAILED/u);
    assert.doesNotMatch(container.textContent, /internal\.invalid|stack/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    let sourceLoads = 0;
    const retryAdapter = {
      ...adapter,
      async listSources() {
        sourceLoads += 1;
        if (sourceLoads === 1) throw new Error("SOURCE_LIST_FAILED http://internal.invalid stack");
        return [{ source_id: "source-1", source_version_id: "version-1", filename: "retried.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }];
      },
      async listStudioOutputs() { return []; },
    };
    const retryContainer = dom.document.createElement("div"); dom.document.body.appendChild(retryContainer);
    reactRoot = createRoot(retryContainer);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: retryAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const retryButton = findElements(retryContainer, (node) => node.tagName === "BUTTON" && node.textContent === "다시 시도")[0];
    assert.ok(retryButton);
    assert.doesNotMatch(retryContainer.textContent, /Source를 추가해 주세요/u);
    await act(async () => { retryButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
    assert.equal(sourceLoads, 2);
    assert.match(retryContainer.textContent, /retried\.pdf/u);
    assert.doesNotMatch(retryContainer.textContent, /internal\.invalid|stack/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    let transientLoads = 0;
    const initialSourceLoad = deferred();
    const transientAdapter = {
      ...adapter,
      async listSources() {
        transientLoads += 1;
        if (transientLoads === 1) return initialSourceLoad.promise;
        return [];
      },
      async listStudioOutputs() { return []; },
    };
    const transientContainer = dom.document.createElement("div"); dom.document.body.appendChild(transientContainer);
    reactRoot = createRoot(transientContainer);
    await act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: transientAdapter }));
      await Promise.resolve(); await Promise.resolve();
    });
    assert.equal(transientLoads, 1);
    const initialError = new Error("STUDIO_DATABASE_UNAVAILABLE");
    initialError.retryable = true;
    await act(async () => { initialSourceLoad.reject(initialError); await Promise.resolve(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    assert.equal(transientLoads, 2);
    assert.match(transientContainer.textContent, /Source를 추가해 주세요/u);
    assert.doesNotMatch(transientContainer.textContent, /Source를 불러오지 못했습니다/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    const delayedKnowledge = deferred();
    const delayedStudio = deferred();
    let emptyContextSourceLoads = 0;
    const emptyContextContainer = dom.document.createElement("div"); dom.document.body.appendChild(emptyContextContainer);
    const emptyContextAdapter = {
      ...adapter,
      notebookContext: { notebook_id: "notebook-empty", sources: [] },
      async listSources() { emptyContextSourceLoads += 1; throw new Error("STALE_SOURCE_LIST_FAILED"); },
      async listKnowledgePackages() { return delayedKnowledge.promise; },
      async listStudioOutputs() { return delayedStudio.promise; },
    };
    reactRoot = createRoot(emptyContextContainer);
    await act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, {
        workspaceId: "workspace-1",
        adapter: emptyContextAdapter,
        state: createProductWorkspaceState({ status: "error", safeError: "SOURCE_LIST_FAILED" }),
      }));
      await Promise.resolve(); await Promise.resolve();
    });
    assert.match(emptyContextContainer.textContent, /Source를 추가해 주세요/u);
    assert.doesNotMatch(emptyContextContainer.textContent, /Source를 불러오지 못했습니다/u);
    assert.equal(emptyContextSourceLoads, 0);
    delayedKnowledge.resolve([]);
    delayedStudio.resolve([]);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    let cloudOverallStatus = "ready";
    const splitStatusContainer = dom.document.createElement("div"); dom.document.body.appendChild(splitStatusContainer);
    const splitStatusAdapter = {
      ...adapter,
      async listSources() { throw new Error("SOURCE_LIST_FAILED"); },
      async listStudioOutputs() { return []; },
      async getOperationsStatus() { return { overall_status: cloudOverallStatus, components: [] }; },
    };
    reactRoot = createRoot(splitStatusContainer);
    await act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: splitStatusAdapter }));
      await Promise.resolve(); await Promise.resolve();
    });
    assert.match(splitStatusContainer.textContent, /Source 확인 필요/u);
    assert.match(splitStatusContainer.textContent, /Cloud 미확인/u);
    assert.doesNotMatch(splitStatusContainer.textContent, /주의 · Cloud/u);
    const operationsButton = findElements(splitStatusContainer, (node) => node.tagName === "BUTTON" && node.textContent === "운영상태")[0];
    await act(async () => { operationsButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
    assert.match(splitStatusContainer.textContent, /Cloud 정상/u);
    assert.match(splitStatusContainer.textContent, /Source 확인 필요/u);
    cloudOverallStatus = "error";
    const closeButton = findElements(splitStatusContainer, (node) => node.tagName === "BUTTON" && node.getAttribute?.("aria-label") === "닫기")[0];
    await act(async () => { closeButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    await act(async () => { operationsButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
    assert.match(splitStatusContainer.textContent, /Cloud 주의/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("Source 로드는 다른 pane projection 오류와 stale 결과에서 독립적이다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-ownership-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-ownership" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("source-ownership") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?sourceOwnership=${Date.now()}`);
    const source = { source_id: "source-current", source_version_id: "version-current", filename: "current.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" };
    const projectionFailureAdapter = {
      ...adapter,
      async listSources() { return [source]; },
      async listKnowledgePackages() { return { invalid: true }; },
      async listStudioOutputs() { return { outputs: null, studioLocks: [{ lock_type: "ruleset" }] }; },
      async loadNotebookConversation() { throw new Error("CONVERSATION_UNAVAILABLE"); },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-current", adapter: projectionFailureAdapter }));
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    assert.match(container.textContent, /current\.pdf/u);
    assert.doesNotMatch(container.textContent, /Source를 불러오지 못했습니다/u);
    assert.match(container.textContent, /지식을 불러오지 못했습니다/u);

    const oldSource = deferred();
    const oldAdapter = { ...adapter, async listSources() { return oldSource.promise; }, async listStudioOutputs() { return []; } };
    const newAdapter = { ...adapter, async listSources() { return [source]; }, async listStudioOutputs() { return []; } };
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-old", adapter: oldAdapter })); await Promise.resolve(); });
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-current", adapter: newAdapter })); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { oldSource.reject(new Error("OLD_SOURCE_FAILED")); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /current\.pdf/u);
    assert.doesNotMatch(container.textContent, /OLD_SOURCE_FAILED|Source를 불러오지 못했습니다/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("빈 Notebook은 좁은 일반대화만 실행하고 근거 미사용을 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-general-conversation-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "general-conversation" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("general-conversation") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?general=${Date.now()}`);
    const calls = [];
    let authorizeCalls = 0;
    const effectAdapter = {
      ...adapter,
      async authorizeQuestion() { authorizeCalls += 1; throw new Error("QUESTION_AUTHORIZATION_MUST_NOT_RUN"); },
      async listSources() { return []; },
      async listStudioOutputs() { return []; },
      async askQuestion(input) {
        calls.push(input);
        return { run_id: "run-general", run_result_id: "result-general", answer: "안녕하세요.", insufficient: false, citations: [] };
      },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    assert.equal(findElements(container, (node) => node.tagName === "INPUT" && node.getAttribute("autocomplete") === "current-password").length, 0);
    assert.doesNotMatch(container.textContent, /추가 인증|현재 비밀번호/u);
    const textarea = findElements(container, (node) => node.tagName === "TEXTAREA")[0];
    assert.equal(textarea.disabled, false);
    const textareaProps = textarea[Object.keys(textarea).find((key) => key.startsWith("__reactProps$"))];
    textarea.value = "2026년 매출은?";
    await act(async () => textareaProps.onChange({ currentTarget: textarea, target: textarea }));
    const submit = findElements(container, (node) => node.tagName === "BUTTON" && node.getAttribute("aria-label") === "질문 실행")[0];
    assert.equal(submit.disabled, true);
    textarea.value = "안녕하세요!";
    await act(async () => textareaProps.onChange({ currentTarget: textarea, target: textarea }));
    assert.equal(submit.disabled, false);
    const form = findElements(container, (node) => node.tagName === "FORM" && node.getAttribute("class") === "conversation-composer")[0];
    const formProps = form[Object.keys(form).find((key) => key.startsWith("__reactProps$"))];
    await act(async () => formProps.onSubmit({ preventDefault() {} }));
    assert.deepEqual(calls, [{ knowledgeContext: null, question: "안녕하세요!" }]);
    assert.equal(authorizeCalls, 0);
    assert.match(container.textContent, /일반 대화 · 근거 미사용/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    const groundedContainer = dom.document.createElement("div"); dom.document.body.appendChild(groundedContainer);
    const groundedCalls = [];
    const groundedAdapter = {
      ...effectAdapter,
      async listSources() {
        return [{ source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }];
      },
      async askQuestion(input) {
        groundedCalls.push(input);
        return { run_id: "run-grounded", run_result_id: "result-grounded", answer: "근거 부족 모양", insufficient: false, citations: [] };
      },
    };
    reactRoot = createRoot(groundedContainer);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: groundedAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const groundedTextarea = findElements(groundedContainer, (node) => node.tagName === "TEXTAREA")[0];
    const groundedTextareaProps = groundedTextarea[Object.keys(groundedTextarea).find((key) => key.startsWith("__reactProps$"))];
    groundedTextarea.value = "선택 Source를 근거로 답해줘";
    await act(async () => groundedTextareaProps.onChange({ currentTarget: groundedTextarea, target: groundedTextarea }));
    const groundedForm = findElements(groundedContainer, (node) => node.tagName === "FORM" && node.getAttribute("class") === "conversation-composer")[0];
    const groundedFormProps = groundedForm[Object.keys(groundedForm).find((key) => key.startsWith("__reactProps$"))];
    await act(async () => groundedFormProps.onSubmit({ preventDefault() {} }));
    assert.equal(authorizeCalls, 0);
    assert.deepEqual(groundedCalls, [{
      knowledgeContext: {
        mode: "raw_only",
        resources: [{ resourceKind: "source", resourceId: "source-1", versionId: "version-1" }],
      },
      question: "선택 Source를 근거로 답해줘",
    }]);
    assert.doesNotMatch(groundedContainer.textContent, /일반 대화 · 근거 미사용/u);

    await act(async () => reactRoot.unmount());
    reactRoot = null;
    let resolveOldQuestion;
    const oldQuestion = new Promise((resolve) => { resolveOldQuestion = resolve; });
    const staleAdapter = { ...effectAdapter, async askQuestion() { return oldQuestion; } };
    const staleContainer = dom.document.createElement("div"); dom.document.body.appendChild(staleContainer);
    reactRoot = createRoot(staleContainer);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-old", adapter: staleAdapter })); await Promise.resolve(); await Promise.resolve(); });
    const staleTextarea = findElements(staleContainer, (node) => node.tagName === "TEXTAREA")[0];
    const staleTextareaProps = staleTextarea[Object.keys(staleTextarea).find((key) => key.startsWith("__reactProps$"))];
    staleTextarea.value = "안녕하세요!";
    await act(async () => staleTextareaProps.onChange({ currentTarget: staleTextarea, target: staleTextarea }));
    const staleForm = findElements(staleContainer, (node) => node.tagName === "FORM" && node.getAttribute("class") === "conversation-composer")[0];
    const staleFormProps = staleForm[Object.keys(staleForm).find((key) => key.startsWith("__reactProps$"))];
    let oldSubmission;
    await act(async () => { oldSubmission = staleFormProps.onSubmit({ preventDefault() {} }); await Promise.resolve(); });
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-new", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { resolveOldQuestion({ run_id: "run-old", run_result_id: "result-old", answer: "오래된 응답", insufficient: false, citations: [] }); await oldSubmission; });
    assert.doesNotMatch(staleContainer.textContent, /오래된 응답|일반 대화 · 근거 미사용/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("Source·지식 Pane은 검증된 Knowledge와 Raw Source 상태·Version·선택을 함께 표시한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".workspace-source-knowledge-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "source-knowledge" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("source-knowledge") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell, buildQuestionKnowledgeContext } = await import(`${pathToFileURL(path.join(output, entry)).href}?sourceKnowledge=${Date.now()}`);
    const effectAdapter = {
      ...adapter,
      async listSources() {
        return [
          { source_id: "source-ready", source_version_id: "version-ready", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" },
          { source_id: "source-review", source_version_id: "version-review", filename: "review.pdf", source_state: "needs_review", processing_state: "completed", job_state: "completed" },
        ];
      },
      async listKnowledgePackages() {
        return [{
          package_id: "knowledge-package-1", producer: "daon2_5", producer_version: "2.5.7",
          knowledge_registration_id: "knowledge-registration-1", output_version_id: "output-version-7",
          authority: "approved", registration_state: "registered", review_state: "approved",
          digest_sha256: "a".repeat(64), byte_size: 4096,
          content_type: "application/vnd.daon.knowledge+json",
          effective_at: "2026-08-14T00:00:00Z", expires_at: "2027-08-14T00:00:00Z",
        }];
      },
      async listStudioOutputs() { return []; },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /Daon 승인 지식/u);
    assert.match(container.textContent, /Daon 2\.5 · 2\.5\.7/u);
    assert.match(container.textContent, /승인 · 등록됨/u);
    assert.match(container.textContent, /Raw Source/u);
    assert.match(container.textContent, /ready\.pdf/u);
    assert.match(container.textContent, /Version version-ready · 사용 가능/u);
    assert.match(container.textContent, /review\.pdf/u);
    assert.match(container.textContent, /검토 필요/u);

    const knowledgeButton = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("Daon 2.5"))[0];
    assert.ok(knowledgeButton);
    assert.equal(knowledgeButton.getAttribute("aria-pressed"), "false");
    await act(async () => { knowledgeButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(knowledgeButton.getAttribute("aria-pressed"), "true");
    assert.deepEqual(buildQuestionKnowledgeContext(
      { sourceId: "source-ready", sourceVersionId: "version-ready" }, "knowledge-package-1",
    ), {
      mode: "mixed",
      resources: [
        { resourceKind: "knowledge_package", resourceId: "knowledge-package-1" },
        { resourceKind: "source", resourceId: "source-ready", versionId: "version-ready" },
      ],
    });
    assert.match(container.textContent, /질문 컨텍스트 · Daon 승인 지식 \+ Raw Source/u);
    assert.doesNotMatch(container.textContent, /internal\.invalid|digest_sha256|knowledge-registration-1/u);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("Product Studio 실제 React는 근거 충족 전 생성 비활성, 충족 후 보고서 입력·목록을 렌더한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".stage-b-product-react-"));
  try {
    const { build } = await import("vite");
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    await build({
      configFile: false, logLevel: "silent", root,
      build: {
        outDir: output, emptyOutDir: false,
        lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "product-workspace" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/server"] },
      },
    });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-workspace") && /\.m?js$/u.test(name));
    const {
      ProductWorkspaceShell, projectSafeQuestionAnswer, submitGroundedReport,
      submitGroundedReportForm, openNativeCitation,
    } = await import(`${pathToFileURL(path.join(output, entry)).href}?stageB=${Date.now()}`);
    const disabled = renderToStaticMarkup(createElement(ProductWorkspaceShell, {
      workspaceId: "workspace-1", state: createProductWorkspaceState({ status: "loading" }), adapter,
    }));
    assert.match(disabled, /근거 기반 보고서/);
    assert.doesNotMatch(disabled, /빠른 근거 보고서/);
    assert.match(disabled, /disabled/);
    const ready = {
      ...createProductWorkspaceState({ status: "ready" }),
      selectedSource: { sourceId: "source-1", sourceVersionId: "version-1" },
      answer: { run_id: "run-1", run_result_id: "result-1", answer: "근거", insufficient: false,
        citations: [{ citation_id: "citation-1", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-1", page: 2, origin: "raw_source", context_item_id: "source-1", locator: { kind: "page", value: "2" } }] },
      studioOutputs: [{ studio_output_id: "output-1", title: "기존 보고서", status: "draft", citations: [] }],
    };
    const enabled = renderToStaticMarkup(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state: ready, adapter }));
    assert.match(enabled, /보고서 제목/);
    assert.match(enabled, /결과 목적/);
    assert.match(enabled, /기존 보고서/);
    assert.doesNotMatch(enabled, /Prototype|Fixture|Delivery|Registration/);
    assert.throws(() => projectSafeQuestionAnswer(
      { ...ready.answer, citations: [{ ...ready.answer.citations[0], source_version_id: "version-other" }] },
      "workspace-1",
      (citation) => `/bff/api/workspaces/workspace-1/citations/${citation.citation_id}/content#page=${citation.page}`,
      ready.selectedSource,
    ), /QUESTION_RESPONSE_INVALID/);

    let resolveCitation;
    let opened = 0;
    const citationController = new AbortController();
    const lateCitation = openNativeCitation({ preventDefault() {} }, {
      adapter: { citationContent: async () => new Promise((resolve) => { resolveCitation = resolve; }) },
      citation: ready.answer.citations[0], signal: citationController.signal,
      openWindow: () => { opened += 1; }, objectUrl: { createObjectURL: () => "blob:late", revokeObjectURL() {} },
      BlobType: Blob, schedule() {}
    });
    citationController.abort();
    resolveCitation({ content_type: "application/pdf", page: 2, bytes: [0x25, 0x50, 0x44, 0x46, 0x2d] });
    assert.equal(await lateCitation, false);
    assert.equal(opened, 0, "Session 종료 뒤 늦은 Citation PDF를 열면 안 된다");

    let createCalls = 0;
    let listCalls = 0;
    const keys = [];
    const stored = [];
    const behaviorAdapter = {
      ...adapter,
      async createReport(_request, options) {
        createCalls += 1;
        keys.push(options.idempotencyKey);
        if (!stored.length) stored.push({ studio_output_id: "output-2", title: "새 보고서", status: "draft", citations: [] });
      },
      async listStudioOutputs() { listCalls += 1; return [...stored]; },
    };
    const blocked = await submitGroundedReport({
      adapter: behaviorAdapter, state: createProductWorkspaceState({ status: "loading" }),
      title: "차단 보고서", purpose: "차단", idempotencyKey: "report-blocked",
    });
    assert.deepEqual(blocked, { submitted: false, outputs: [] });
    assert.equal(createCalls, 0);
    const mismatched = await submitGroundedReport({
      adapter: behaviorAdapter,
      state: {
        ...ready,
        answer: { ...ready.answer, citations: [{ ...ready.answer.citations[0], source_id: "source-other" }] },
      },
      title: "불일치 보고서", purpose: "불일치", idempotencyKey: "report-mismatch-0001",
    });
    assert.deepEqual(mismatched, { submitted: false, outputs: ready.studioOutputs });
    assert.equal(createCalls, 0);
    const first = await submitGroundedReport({
      adapter: behaviorAdapter, state: ready, title: "새 보고서", purpose: "근거 정리",
      idempotencyKey: "report-replay-1",
    });
    const replay = await submitGroundedReport({
      adapter: behaviorAdapter, state: ready, title: "새 보고서", purpose: "근거 정리",
      idempotencyKey: "report-replay-1",
    });
    assert.equal(first.outputs.length, 1);
    assert.equal(replay.outputs.length, 1);
    assert.deepEqual(keys, ["report-replay-1", "report-replay-1"]);
    assert.equal(createCalls, 2);
    assert.equal(listCalls, 2);

    const formKeys = [];
    const formOutputs = [];
    const formAdapter = {
      ...adapter,
      async createReport(request, options) {
        formKeys.push(options.idempotencyKey);
        if (!formOutputs.some((output) => output.idempotency_key === options.idempotencyKey)) {
          formOutputs.push({
            studio_output_id: `form-output-${formOutputs.length + 1}`, title: request.title,
            status: "draft", citations: [], idempotency_key: options.idempotencyKey,
          });
        }
      },
      async listStudioOutputs() { return formOutputs.map(({ idempotency_key: _key, ...output }) => output); },
    };
    const idempotencyRef = { current: null };
    const uuids = [
      "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002",
      "00000000-0000-4000-8000-000000000003", "00000000-0000-4000-8000-000000000004",
      "00000000-0000-4000-8000-000000000005",
    ];
    let prevented = 0;
    const event = { preventDefault() { prevented += 1; } };
    const submitForm = (nextState, title, purpose) => submitGroundedReportForm(event, {
      adapter: formAdapter, state: nextState, title, purpose, idempotencyRef, uuid: () => uuids.shift(),
    });
    await submitForm(ready, "양식 보고서", "목적 1");
    await submitForm(ready, "양식 보고서", "목적 1");
    await submitForm(ready, "양식 보고서 변경", "목적 1");
    await submitForm(ready, "양식 보고서 변경", "목적 2");
    const sourceChanged = {
      ...ready,
      selectedSource: { sourceId: "source-2", sourceVersionId: "version-2" },
      answer: {
        ...ready.answer,
        citations: [{ ...ready.answer.citations[0], source_id: "source-2", source_version_id: "version-2" }],
      },
    };
    await submitForm(sourceChanged, "양식 보고서 변경", "목적 2");
    const runChanged = { ...sourceChanged, answer: { ...sourceChanged.answer, run_id: "run-2", run_result_id: "result-2" } };
    const accumulated = await submitForm(runChanged, "양식 보고서 변경", "목적 2");
    assert.equal(prevented, 6);
    assert.equal(formKeys[0], formKeys[1]);
    assert.equal(new Set(formKeys).size, 5);
    assert.equal(accumulated.outputs.length, 5);
    assert.ok(formKeys.every((key) => /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/u.test(key)));
    const failedOutputs = [];
    await assert.rejects(() => submitGroundedReport({
      adapter: { ...behaviorAdapter, async createReport() { throw new Error("STUDIO_DATABASE_UNAVAILABLE"); }, async listStudioOutputs() { failedOutputs.push("unexpected"); return []; } },
      state: ready, title: "실패 보고서", purpose: "실패", idempotencyKey: "report-failure-1",
    }), /STUDIO_DATABASE_UNAVAILABLE/);
    assert.deepEqual(failedOutputs, []);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("저장된 공통 Studio DTO 1건으로 재진입해도 신규 Pane과 기존 보고서 계보가 함께 렌더된다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".studio-reentry-react-"));
  try {
    const { build } = await import("vite");
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "product-reentry" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/server"] },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-reentry") && /\.m?js$/u.test(name));
    const { ProductWorkspaceShell } = await import(`${pathToFileURL(path.join(output, entry)).href}?reentry=${Date.now()}`);
    const stored = {
      studio_output_id: "output-1", output_version_id: "version-1", output_type: "comparison_table",
      title: "저장 비교표", status: "draft", citations: 2,
      version: { output_format: "xlsx", rows: [{ key: "항목", baseline: "A", current: "B", evidence: ["citation-1", "citation-2"] }] },
    };
    const text = renderToStaticMarkup(createElement(ProductWorkspaceShell, {
      workspaceId: "workspace-1", adapter,
      state: { ...createProductWorkspaceState({ status: "ready" }), studioOutputs: [stored] },
    }));
    assert.match(text, /저장 비교표/);
    assert.match(text, /저장된 산출물/);
    assert.match(text, /저장된 보고서/);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});
