import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import {
  assertProductWorkspaceAdapter,
  createProductWorkspaceState,
  canCreateGroundedReport,
} from "../../packages/ui/src/product-workspace-model.js";

const adapter = Object.freeze({
  listSources() {}, uploadPdf() {}, getProcessingStatus() {}, askQuestion() {},
  citationUrl() {}, createReport() {}, listStudioOutputs() {},
});

test("Product Workspace Adapter는 exact 7개 메서드를 요구한다", () => {
  assert.equal(assertProductWorkspaceAdapter(adapter), adapter);
  assert.throws(
    () => assertProductWorkspaceAdapter({ ...adapter, createReport: undefined }),
    /WORKSPACE_ADAPTER_INVALID/,
  );
});

test("근거 보고서는 ready Source와 sufficient Citation 답변이 모두 있어야 생성 가능하다", () => {
  const ready = {
    ...createProductWorkspaceState({ status: "ready" }),
    selectedSource: { sourceId: "source-1", sourceVersionId: "version-1" },
    answer: {
      run_id: "run-1", run_result_id: "result-1", answer: "근거 답변",
      insufficient: false,
      citations: [{ citation_id: "citation-1", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-1", page: 2 }],
    },
  };
  assert.equal(canCreateGroundedReport(ready), true);
  assert.equal(canCreateGroundedReport({ ...ready, answer: { ...ready.answer, insufficient: true } }), false);
  assert.equal(canCreateGroundedReport({ ...ready, answer: { ...ready.answer, citations: [] } }), false);
  assert.equal(canCreateGroundedReport({ ...ready, selectedSource: null }), false);
  assert.equal(canCreateGroundedReport({
    ...ready,
    answer: { ...ready.answer, citations: [{ ...ready.answer.citations[0], source_version_id: "version-other" }] },
  }), false);
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
    assert.match(disabled, /보고서 생성/);
    assert.match(disabled, /disabled/);
    const ready = {
      ...createProductWorkspaceState({ status: "ready" }),
      selectedSource: { sourceId: "source-1", sourceVersionId: "version-1" },
      answer: { run_id: "run-1", run_result_id: "result-1", answer: "근거", insufficient: false,
        citations: [{ citation_id: "citation-1", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-1", page: 2 }] },
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
