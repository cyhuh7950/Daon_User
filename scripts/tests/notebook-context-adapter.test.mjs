import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";
import { createNotebookContextWorkspaceAdapter, projectNotebookSelectedContext } from "../../packages/ui/src/notebook-context-adapter.js";
import { createProductWorkspaceState } from "../../packages/ui/src/product-workspace-model.js";
import { citationContentUrl } from "../../apps/web/lib/question-answering-api.js";

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

test("S18 질문 추가 인증 adapter는 발급과 제출을 동일 현재 Notebook에 결속한다", async () => {
  const calls = [];
  const base = { authorizeQuestion: async (input, options) => { calls.push([input, options]); return { step_up_authorization_id: "grant-s18" }; },
    askQuestion: async (input, options) => { calls.push([input, options]); return { answer: "응답" }; } };
  const adapter = createNotebookContextWorkspaceAdapter(base, EXISTING_CONTEXT);
  const controller = new AbortController();
  const options = { idempotencyKey: "question-s18", signal: controller.signal };
  const input = { notebookId: "notebook-wrong", question: "근거는?" };
  const grant = await adapter.authorizeQuestion({ ...input, password: "synthetic-password" }, options);
  await adapter.askQuestion(input, { ...options, stepUpAuthorizationId: grant.step_up_authorization_id });
  assert.equal(calls[0][0].notebookId, "notebook-existing"); assert.equal(calls[1][0].notebookId, "notebook-existing");
  assert.equal(calls[0][1].signal, controller.signal); assert.equal(calls[0][1].idempotencyKey, calls[1][1].idempotencyKey);
});

test("S18 질문 저장 PDF Citation은 현재 Notebook URL과 정확한 page fragment를 복원한다", async () => {
  const citation = { citation_id: "citation-pdf", source_id: "source-bound", source_version_id: "source-version-bound",
    evidence_span_id: "span-pdf", page: 3, origin: "raw_source", context_item_id: "source-bound", locator: { kind: "page", value: "3" } };
  const context = { ...EXISTING_CONTEXT, knowledge_context_ids: [], conversation: {
    conversation_thread_id: "conversation-bound", answer: { ...EXISTING_CONTEXT.conversation.answer, citations: [citation] },
  } };
  const adapter = createNotebookContextWorkspaceAdapter({ citationUrl: (item, options) => citationContentUrl("workspace-1", item, options) }, context);
  const answer = await adapter.loadNotebookConversation();
  assert.equal(answer.citations[0].content_url, "/bff/api/workspaces/workspace-1/citations/citation-pdf/content?notebook_id=notebook-existing#page=3");
  assert.deepEqual(answer.citations[0].locator, { kind: "page", value: "3" });
  for (const altered of [{ source_version_id: "other-version" }, { source_id: "other-source" },
    { locator: { kind: "unknown", value: "3" } }, { locator: { kind: "page", value: "2" } }]) {
    assert.throws(() => createNotebookContextWorkspaceAdapter({}, { ...context, conversation: { ...context.conversation,
      answer: { ...context.conversation.answer, citations: [{ ...citation, ...altered }] },
    } }), /NOTEBOOK_CONTEXT_INVALID/u);
  }
});

test('PPTX saved answer reentry restores same Notebook URL and rejects wrong Version',async()=>{
 const citation={citation_id:'citation-pptx',source_id:'source-bound',source_version_id:'source-version-bound',evidence_span_id:'span-pptx',page:1,origin:'raw_source',context_item_id:'source-bound',locator:{kind:'pptx_block',value:'{"block":"s:1:sp:2:p:1","start":0,"end":5}'}};
 const context={...EXISTING_CONTEXT,conversation:{...EXISTING_CONTEXT.conversation,answer:{...EXISTING_CONTEXT.conversation.answer,citations:[citation]}}};
 const {citationContentUrl}=await import('../../apps/web/lib/question-answering-api.js');
 const answer=await createNotebookContextWorkspaceAdapter({citationUrl:(value,options)=>citationContentUrl('workspace-1',value,options)},context).loadNotebookConversation();
 assert.deepEqual(answer.citations[0].locator,citation.locator);
 assert.equal(answer.citations[0].content_url,'/bff/api/workspaces/workspace-1/citations/citation-pptx/content?notebook_id=notebook-existing');
 assert.throws(()=>projectNotebookSelectedContext({...context,sources:[]}),/NOTEBOOK_CONTEXT_INVALID/);
});

test("저장된 Image Region은 Notebook context에서 같은 출처 원문 URL을 복원한다", async () => {
  const citation = { citation_id: "citation-image", source_id: "source-bound", source_version_id: "source-version-bound", evidence_span_id: "span-image", page: 1, origin: "raw_source", context_item_id: "source-bound", locator: { kind: "image_region", value: "5,7,80,20,120,60" } };
  const context = { ...EXISTING_CONTEXT, conversation: { ...EXISTING_CONTEXT.conversation, answer: { ...EXISTING_CONTEXT.conversation.answer, citations: [citation] } } };
  const { citationContentUrl } = await import("../../apps/web/lib/question-answering-api.js");
  const base = { citationUrl: (value, options) => citationContentUrl("workspace-1", value, options) };
  const answer = await createNotebookContextWorkspaceAdapter(base, context).loadNotebookConversation();
  assert.equal(answer.citations[0].content_url, "/bff/api/workspaces/workspace-1/citations/citation-image/content?notebook_id=notebook-existing");
  assert.equal(answer.citations[0].locator.value, citation.locator.value);
  assert.throws(() => projectNotebookSelectedContext({ ...context, conversation: { ...context.conversation, answer: { ...context.conversation.answer, citations: [{ ...citation, locator: { kind: "image_region", value: "5,7,180,20,120,60" } }] } } }), /NOTEBOOK_CONTEXT_INVALID/);
});

test("저장된 sheet_cell은 같은 Notebook 원문 URL과 locator를 복원하고 변조를 거부한다", async () => {
  const citation = { citation_id: 'citation-cell', source_id: 'source-bound', source_version_id: 'source-version-bound', evidence_span_id: 'span-cell', page: 1, origin: 'raw_source', context_item_id: 'source-bound', locator: { kind: 'sheet_cell', value: '{"sheet":"예산","range":"A1"}' } };
  const context = { ...EXISTING_CONTEXT, conversation: { ...EXISTING_CONTEXT.conversation, answer: { ...EXISTING_CONTEXT.conversation.answer, citations: [citation] } } };
  const { citationContentUrl } = await import('../../apps/web/lib/question-answering-api.js');
  const restored = await createNotebookContextWorkspaceAdapter({ citationUrl: (value, options) => citationContentUrl('workspace-1', value, options) }, context).loadNotebookConversation();
  assert.equal(restored.citations[0].content_url, '/bff/api/workspaces/workspace-1/citations/citation-cell/content?notebook_id=notebook-existing');
  assert.deepEqual(restored.citations[0].locator, citation.locator);
  assert.throws(() => projectNotebookSelectedContext({ ...context, conversation: { ...context.conversation, answer: { ...context.conversation.answer, citations: [{ ...citation, locator: { kind: 'sheet_cell', value: '{"sheet":"예산","range":"A1:Q16"}' } }] } } }), /NOTEBOOK_CONTEXT_INVALID/);
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

test("Notebook context Adapter는 Notebook API의 최신 Source 목록을 유지하고 empty도 업로드 후 재조회할 수 있다", async () => {
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
  assert.deepEqual((await adapter.listSources()).map((item) => item.source_id), ["source-bound", "source-other"]);
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
  assert.equal(emptyCalls, 1);
});

test("대화가 없거나 여러 thread인 Notebook도 Source 질문 컨텍스트를 차단하지 않는다", async () => {
  const base = {
    listSources: async () => [{ source_id: "source-bound", source_version_id: "source-version-bound" }],
  };
  for (const context of [
    { ...EXISTING_CONTEXT, conversation_thread_ids: ["conversation-bound", "conversation-next"], conversation: null },
    { ...EXISTING_CONTEXT, conversation_thread_ids: ["conversation-bound", "conversation-next"] },
  ]) {
    const adapter = createNotebookContextWorkspaceAdapter(base, context);
    assert.deepEqual(await adapter.listSources(), [{ source_id: "source-bound", source_version_id: "source-version-bound" }]);
    assert.equal(await adapter.loadNotebookConversation(), null);
  }
});

test("Product Studio 목록은 초기 Notebook context가 오래되어도 API의 notebook 범위 결과를 보존한다", async () => {
  const base = {
    listProductStudioOutputs: async (options) => {
      assert.equal(options.notebookId, "notebook-existing");
      return {
        outputs: [{ studio_output_id: "studio-output-new", output_version_id: "output-version-new" }],
        studioLocks: [],
      };
    },
  };
  const adapter = createNotebookContextWorkspaceAdapter(base, EXISTING_CONTEXT);
  assert.deepEqual(await adapter.listProductStudioOutputs(), {
    outputs: [{ studio_output_id: "studio-output-new", output_version_id: "output-version-new" }],
    studioLocks: [],
  });
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
