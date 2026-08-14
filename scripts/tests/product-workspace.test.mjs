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

test("질문 실패는 로드된 Source·Studio 잠금을 보존하고 안전 오류만 투영한다", () => {
  const current = {
    ...createProductWorkspaceState({ status: "ready" }),
    sources: [{ sourceId: "source-1", sourceVersionId: "version-1", filename: "ready.pdf", ready: true }],
    selectedSource: { sourceId: "source-1", sourceVersionId: "version-1", filename: "ready.pdf", ready: true },
    studioLocks: [{ lock_type: "ruleset", version: "v1" }],
    studioOutputs: [{ studio_output_id: "output-1" }],
  };

  const failed = projectQuestionFailureState(current, new Error("TEXT_MODEL_NOT_SELECTED"));

  assert.equal(failed.status, "error");
  assert.equal(failed.safeError, "TEXT_MODEL_NOT_SELECTED");
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
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductWorkspaceShell, { workspaceId: "workspace-1", adapter: effectAdapter })); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /ready\.pdf/u);
    const questionInput = findElements(container, (node) => node.tagName === "TEXTAREA" && node.parentNode?.textContent.startsWith("질문"))[0];
    assert.equal(questionInput.disabled, false);
    assert.match(container.textContent, /불러오지 못했습니다/u);
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
