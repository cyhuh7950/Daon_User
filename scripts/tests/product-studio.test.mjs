import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import {
  OUTPUT_TYPES, createProductStudioState, selectOutputType, updateGenerationSettings,
  confirmGenerationSettings, canSubmitGeneration, createStudioGenerationInput, mergeStudioVersion,
} from "../../packages/ui/src/product-studio-model.js";
import {
  createStudioGeneration, createStudioVersion, listProductStudioOutputs, createStudioAction, downloadStudioExport,
  listStudioVersions,
} from "../../apps/web/lib/product-workspace-api.js";

const grounded = {
  sourceId: "source-1", sourceVersionId: "source-version-1",
  sourceVersionIds: ["source-version-1"],
  runId: "run-1", runResultId: "result-1",
};

test("Product Studio는 혼합 Context의 모든 Citation Source Version을 생성 Snapshot에 결속한다", () => {
  const mixed = { ...grounded, sourceVersionIds: ["source-version-daon", "source-version-1"] };
  let state = selectOutputType(createProductStudioState({ grounded: mixed }), "evidence_report");
  assert.deepEqual(state.settings.sourceVersionIds, mixed.sourceVersionIds);
  state = updateGenerationSettings(state, {
    purpose: "혼합 근거 보고", audience: "운영 책임자", sourceVersionIds: mixed.sourceVersionIds,
    rulesetVersionId: null, length: "standard", structure: "summary-body-conclusion",
    outputFormat: "pdf", reviewCondition: "review_required",
  });
  state = confirmGenerationSettings(state);
  assert.deepEqual(createStudioGenerationInput(state).source_version_ids, mixed.sourceVersionIds);
});

test("Product Studio는 다섯 실제 산출물과 생성 전 확인을 강제한다", () => {
  assert.deepEqual(OUTPUT_TYPES.map((item) => item.id), [
    "evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft",
  ]);
  let state = createProductStudioState({ grounded });
  state = selectOutputType(state, "evidence_report");
  assert.equal(canSubmitGeneration(state), false);
  assert.equal(state.settingsConfirmed, false);
  state = updateGenerationSettings(state, {
    purpose: "의사 결정", audience: "운영 책임자", sourceVersionIds: ["source-version-1"],
    rulesetVersionId: null, length: "standard", structure: "summary-body-conclusion",
    outputFormat: "docx", reviewCondition: "review_required",
  });
  assert.equal(canSubmitGeneration(state), false);
  state = confirmGenerationSettings(state);
  assert.equal(canSubmitGeneration(state), true);
  assert.deepEqual(createStudioGenerationInput(state), {
    workspace_id: null, output_type: "evidence_report", source_id: "source-1",
    source_version_ids: ["source-version-1"], run_id: "run-1", run_result_id: "result-1",
    settings: {
      purpose: "의사 결정", audience: "운영 책임자", source_version_ids: ["source-version-1"],
      ruleset_version_id: null, length: "standard", structure: "summary-body-conclusion",
      output_format: "docx", review_condition: "review_required",
    },
  });
});

test("서버 잠금은 표시되며 완화 Handler가 없고 변경은 재확정을 요구한다", () => {
  let state = createProductStudioState({
    grounded,
    locks: [{ field: "rulesetVersionId", value: "ruleset-v3", reason: "ORGANIZATION_POLICY" }],
  });
  state = selectOutputType(state, "business_draft");
  state = updateGenerationSettings(state, {
    purpose: "초안", audience: "고객", sourceVersionIds: ["source-version-1"],
    length: "short", structure: "letter", outputFormat: "pdf",
    reviewCondition: "review_required",
  });
  assert.equal(state.settings.rulesetVersionId, "ruleset-v3");
  state = confirmGenerationSettings(state);
  state = updateGenerationSettings(state, { audience: "내부 검토자" });
  assert.equal(state.settingsConfirmed, false);
  assert.throws(() => updateGenerationSettings(state, { rulesetVersionId: null }), /STUDIO_SETTING_LOCKED/);
  assert.equal("unlock" in state, false);
});

test("새 Version merge는 content를 교체하고 이전 lifecycle link를 전부 제거한다", () => {
  const merged = mergeStudioVersion({ output_version_id: "v1", content: { body: "이전" }, review_request_id: "r1", approval_request_id: "ar1", approval_id: "a1", delivery_id: "d1" }, { output_version_id: "v2", content: { body: "신규" }, status: "draft" });
  assert.equal(merged.output_version_id, "v2"); assert.equal(merged.content.body, "신규");
  for (const field of ["review_request_id", "approval_request_id", "approval_id", "delivery_id"]) assert.equal(merged[field], undefined);
});

test("Product Studio Pane은 다섯 Tile·설정·목록·상세를 실제 Product DOM으로 렌더한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".product-studio-react-"));
  try {
    const { build } = await import("vite");
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    await build({
      configFile: false, logLevel: "silent", root,
      build: { outDir: output, emptyOutDir: false,
        lib: { entry: path.join(root, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/server"] },
      },
    });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name));
    const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const initialState = createProductStudioState({ grounded });
    const home = renderToStaticMarkup(createElement(ProductStudioPane, { state: initialState, adapter: null }));
    assert.doesNotMatch(home, /<form[^>]*studio-config-form/u);
    const html = renderToStaticMarkup(createElement(ProductStudioPane, {
      state: selectOutputType(initialState, "evidence_report"), adapter: null,
    }));
    for (const label of ["근거 기반 보고서", "제약·준수 점검표", "비교·데이터 표", "지식 구조도", "업무 문서 초안"]) {
      assert.match(home, new RegExp(label));
    }
    assert.match(html, /목적/);
    assert.match(html, /독자/);
    assert.match(home, /저장된 산출물/);
    assert.doesNotMatch(html, /Prototype|Fixture/);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("저장된 구조화 산출물을 선택한 재진입 화면은 React child crash 없이 상세를 렌더한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".product-studio-selected-react-"));
  try {
    const { build } = await import("vite");
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    await build({ configFile: false, logLevel: "silent", root,
      build: { outDir: output, emptyOutDir: false,
        lib: { entry: path.join(root, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/server"] },
      },
    });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name));
    const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = createProductStudioState({ grounded, outputs: [{
      studio_output_id: "output-1", output_version_id: "version-1", title: "저장 보고서", status: "draft",
      citations: 2, settings_snapshot_id: "settings-1", content: { summary: "요약", sections: [{ heading: "본문", body: "근거 본문" }] },
    }] });
    state.selectedOutputId = "output-1";
    const html = renderToStaticMarkup(createElement(ProductStudioPane, { state, adapter: null }));
    assert.match(html, /요약/);
    assert.match(html, /근거 본문/);
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("Web Studio Adapter는 same-origin 공통 계약과 파일 bytes만 사용한다", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, method: init.method });
    if (String(url).includes("exports")) return new Response(Uint8Array.from([0x25, 0x50, 0x44, 0x46, 0x2d]), { headers: { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="studio-version-1.pdf"' } });
    if (init.method === "GET") return Response.json({ data: { outputs: [], studio_locks: [] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
    return Response.json({ data: { studio_output_id: "output-1", output_version_id: "version-1", status: "draft" }, meta: { trace_id: "trace-1", workspace_id: "workspace-1", replayed: false } }, { status: 201 });
  };
  await createStudioGeneration("workspace-1", {
    output_type: "evidence_report", source_id: "source-1", source_version_ids: ["source-version-1"],
    run_id: "run-1", run_result_id: "result-1", settings: { purpose: "목적", audience: "독자", source_version_ids: ["source-version-1"], ruleset_version_id: null, length: "short", structure: "summary", output_format: "pdf", review_condition: "review_required" },
  }, { fetchImpl, idempotencyKey: "generation-key-0001" });
  await listProductStudioOutputs("workspace-1", { fetchImpl });
  await createStudioVersion("workspace-1", "output-1", {
    previous_version_id: "version-1", revision_type: "user_edit", change_reason: "표현 정리", content: "변경 내용",
  }, { fetchImpl, idempotencyKey: "version-key-000001" });
  await listStudioVersions("workspace-1", "output-1", { fetchImpl: async (url, init) => {
    calls.push({ url, method: init.method });
    return Response.json({ data: { output_id: "output-1", versions: [{
      output_version_id: "version-1", content_version: 1, previous_version_id: null, status: "draft",
      content: { body: "근거 내용" }, revision_type: "initial", change_reason: "initial_generation",
      settings_snapshot_id: "settings-1", output_format: "pdf", citations: [{ citation_id: "citation-1", source_version_id: "source-version-1", evidence_span_id: "span-1", origin: "raw_source", locator: { kind: "page", value: "2" } }],
      review_request_id: null, approval_request_id: null, approval_id: null, delivery_id: null, knowledge_registration_id: null,
    }] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
  } });
  await createStudioAction("workspace-1", "reviews", { output_version_id: "version-1" }, { fetchImpl, idempotencyKey: "review-key-000001" });
  const exported = await downloadStudioExport("workspace-1", "output-1", "version-1", "pdf", { fetchImpl });
  assert.deepEqual(exported.bytes, [0x25, 0x50, 0x44, 0x46, 0x2d]);
  assert.ok(calls.every((call) => call.url.startsWith("/bff/api/")));
  assert.deepEqual(calls.map((call) => call.url), [
    "/bff/api/studio-generation-requests", "/bff/api/studio-outputs?workspace_id=workspace-1",
    "/bff/api/studio-outputs/output-1/versions",
    "/bff/api/studio-outputs/output-1/versions?workspace_id=workspace-1",
    "/bff/api/reviews", "/bff/api/studio-outputs/output-1/versions/version-1/exports/pdf?workspace_id=workspace-1",
  ]);
});

test("Step-up Adapter는 현재 비밀번호를 요청 body에만 넣고 응답으로 보존하지 않는다", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    return Response.json({ data: { step_up_authorization: "opaque-grant" }, meta: { trace_id: "trace-1" } }, { status: 201 });
  };
  const { issueStudioStepUp } = await import("../../apps/web/lib/product-workspace-api.js");
  const grant = await issueStudioStepUp("external_transfer", "version-1", "current password value", { fetchImpl, idempotencyKey: "step-key-000001" });
  assert.equal(grant, "opaque-grant");
  assert.deepEqual(calls[0].body, { action_group: "external_transfer", target_id: "version-1", ttl_seconds: 300, password: "current password value" });
  assert.doesNotMatch(JSON.stringify(grant), /current password value/);
});
