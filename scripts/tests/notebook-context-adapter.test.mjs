import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";
import { createNotebookContextWorkspaceAdapter, projectNotebookSelectedContext } from "../../packages/ui/src/notebook-context-adapter.js";
import { createProductWorkspaceState } from "../../packages/ui/src/product-workspace-model.js";

const EXISTING_CONTEXT = Object.freeze({
  notebook_id: "notebook-existing",
  sources: Object.freeze([{ source_id: "source-bound", source_version_id: "source-version-bound" }]),
  knowledge_context_ids: Object.freeze(["scope-snapshot-bound"]),
  conversation_thread_ids: Object.freeze(["conversation-bound"]),
  conversation: Object.freeze({
    conversation_thread_id: "conversation-bound",
    answer: Object.freeze({ run_id: "run-bound", run_result_id: "result-bound", answer: "보존된 대화", insufficient: false, citations: Object.freeze([]) }),
  }),
  studio_output_ids: Object.freeze(["studio-output-bound"]),
  output_version_ids: Object.freeze(["output-version-bound"]),
  generation_settings_ids: Object.freeze(["generation-settings-bound"]),
  source_deletion_requests: Object.freeze([]),
});

async function bundle(output) {
  const root = path.resolve(import.meta.dirname, "../..");
  const { build } = await import("vite");
  await build({
    configFile: false, logLevel: "silent", root,
    build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/product-workspace-shell.jsx"), formats: ["es"], fileName: "notebook-context-shell" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client", "react-dom/server"] },
    },
  });
  const built = (await readdir(output)).find((name) => name.startsWith("notebook-context-shell") && /\.m?js$/u.test(name));
  return import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
}

test("Notebook context projector는 exact safe shape만 허용하고 empty context를 보존한다", () => {
  assert.deepEqual(projectNotebookSelectedContext(EXISTING_CONTEXT), EXISTING_CONTEXT);
  assert.equal(projectNotebookSelectedContext({ ...EXISTING_CONTEXT, sources: [] }).sources.length, 0);
  for (const invalid of [
    { ...EXISTING_CONTEXT, tenant_id: "tenant-secret" },
    { ...EXISTING_CONTEXT, sources: [{ source_id: "source-bound", source_version_id: "../escape" }] },
    { ...EXISTING_CONTEXT, conversation_thread_ids: ["http://internal"] },
    { ...EXISTING_CONTEXT, conversation: { ...EXISTING_CONTEXT.conversation, answer: { ...EXISTING_CONTEXT.conversation.answer, internal_url: "https://internal.invalid" } } },
    { ...EXISTING_CONTEXT, conversation: { ...EXISTING_CONTEXT.conversation, answer: { ...EXISTING_CONTEXT.conversation.answer, citations: [{ citation_id: "citation-1", source_id: "source-bound", source_version_id: "source-version-bound", evidence_span_id: "span-1", page: 1, origin: "raw_source", context_item_id: "source-bound", locator: { kind: "page", value: "1" }, secret: "blocked" }] } } },
  ]) assert.throws(() => projectNotebookSelectedContext(invalid), /NOTEBOOK_CONTEXT_INVALID/u);
});

test("Notebook context Adapter는 bound Source·Knowledge·Conversation·Library만 전달하고 empty는 base 호출0이다", async () => {
  const calls = [];
  const answer = { run_id: "run-bound", run_result_id: "result-bound", answer: "보존된 대화", insufficient: false, citations: [] };
  const base = {
    listSources: async () => { calls.push("sources"); return [
      { source_id: "source-bound", source_version_id: "source-version-bound", filename: "보존.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" },
      { source_id: "source-other", source_version_id: "source-version-other", filename: "다른.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" },
    ]; },
    resolveKnowledgeContext: async (id) => { calls.push(`knowledge:${id}`); return { package_ids: ["package-bound"] }; },
    listKnowledgePackages: async () => { calls.push("packages"); return [{ package_id: "package-bound" }, { package_id: "package-other" }]; },
    listStudioOutputs: async () => { calls.push("outputs"); return [
      { studio_output_id: "studio-output-bound", output_version_id: "output-version-bound" },
      { studio_output_id: "studio-output-other", output_version_id: "output-version-other" },
    ]; },
  };
  const adapter = createNotebookContextWorkspaceAdapter(base, EXISTING_CONTEXT);
  assert.deepEqual((await adapter.listSources()).map((item) => item.source_id), ["source-bound"]);
  assert.deepEqual((await adapter.listKnowledgePackages()).map((item) => item.package_id), ["package-bound"]);
  assert.deepEqual(await adapter.loadNotebookConversation(), answer);
  assert.deepEqual((await adapter.listStudioOutputs()).map((item) => item.studio_output_id), ["studio-output-bound"]);
  assert.deepEqual(adapter.generationSettingsIds, ["generation-settings-bound"]);
  assert.deepEqual(calls, ["sources", "knowledge:scope-snapshot-bound", "packages", "outputs"]);

  const inputWithBinding = { ...EXISTING_CONTEXT };
  Object.defineProperty(inputWithBinding, "bindingEtag", { enumerable: false, value: '"notebook-binding:2"' });
  const bindingAware = createNotebookContextWorkspaceAdapter(base, inputWithBinding);
  assert.equal(bindingAware.bindingEtag, '"notebook-binding:2"');
  assert.equal(Object.keys(bindingAware).includes("bindingEtag"), false);

  let emptyCalls = 0;
  const empty = createNotebookContextWorkspaceAdapter(new Proxy({}, { get: () => async () => { emptyCalls += 1; return []; } }), {
    notebook_id: "notebook-empty", sources: [], knowledge_context_ids: [], conversation_thread_ids: [],
    studio_output_ids: [], output_version_ids: [], generation_settings_ids: [], source_deletion_requests: [], conversation: null,
  });
  assert.deepEqual(await empty.listSources(), []);
  assert.deepEqual(await empty.listKnowledgePackages(), []);
  assert.equal(await empty.loadNotebookConversation(), null);
  assert.deepEqual(await empty.listStudioOutputs(), []);
  assert.equal(emptyCalls, 0);
});

test("실제 ProductWorkspaceShell은 selected Notebook Adapter의 보존 Source·대화·Library를 행동 렌더한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".notebook-context-shell-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { ProductWorkspaceShell } = await bundle(output);
    let productListOptions;
    const base = {
      listSources: async () => [{ source_id: "source-bound", source_version_id: "source-version-bound", filename: "보존.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }],
      resolveKnowledgeContext: async () => ({ package_ids: ["package-bound"] }),
      listKnowledgePackages: async () => [{ package_id: "package-bound", producer: "daon3", producer_version: "3.0", authority: "approved", registration_state: "registered" }],
      getConversationThread: async () => ({ conversation_thread_id: "conversation-bound", answer: { run_id: "run-bound", run_result_id: "result-bound", answer: "보존된 대화", insufficient: false, citations: [] } }),
      listStudioOutputs: async () => [{ studio_output_id: "studio-output-bound", output_version_id: "output-version-bound", output_type: "evidence_report", title: "보존된 보고서", purpose: "검증", status: "draft", content: "내용", run_id: "run-bound", run_result_id: "result-bound", citations: [] }],
      listProductStudioOutputs: async (options) => {
        productListOptions = options;
        return { outputs: [
          { studio_output_id: "studio-output-bound", output_version_id: "output-version-bound", output_type: "evidence_report", title: "보존된 보고서", purpose: "검증", status: "draft", content: "내용", run_id: "run-bound", run_result_id: "result-bound", citations: [] },
          { studio_output_id: "studio-output-other", output_version_id: "output-version-other", output_type: "evidence_report", title: "다른 Notebook 보고서", purpose: "차단", status: "draft", content: "누출", run_id: "run-other", run_result_id: "result-other", citations: [] },
        ], studioLocks: [] };
      },
    };
    const adapter = createNotebookContextWorkspaceAdapter(base, EXISTING_CONTEXT);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => {
      reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", state: createProductWorkspaceState(), adapter }));
      await Promise.resolve(); await Promise.resolve();
    });
    assert.match(container.textContent, /보존\.pdf/u);
    assert.match(container.textContent, /보존된 대화/u);
    assert.match(container.textContent, /보존된 보고서/u);
    assert.doesNotMatch(container.textContent, /source-other|package-other|studio-output-other/u);
    assert.equal(productListOptions?.notebookId, "notebook-existing");
    assert.equal(findElements(container, (node) => node.getAttribute?.("data-layout") === "source-conversation-studio").length, 1);
    const composer = findElements(container, (node) => node.tagName === "FORM" && node.getAttribute?.("class")?.includes("conversation-composer"))[0];
    assert.ok(composer);
    composer.dispatchEvent(new MinimalEvent("focus"));
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});
