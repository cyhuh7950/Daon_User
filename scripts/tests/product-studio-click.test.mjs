import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

test("실제 React click은 저장 구조 산출물을 선택하고 검토·승인요청 adapter를 호출한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-click-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const calls = []; const adapter = { createStudioVersion: async (_id, payload) => { calls.push(["version", payload.revision_type]); return { output_version_id: "version-2", status: "draft" }; }, createStudioAction: async (action, payload) => { calls.push([action, payload.decision]); return { record_id: `${action}-1` }; }, issueStudioStepUp: async (_group, _target, password) => { calls.push(["step-up", password]); return "grant"; } };
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{ studio_output_id: "output-1", output_version_id: "version-1", title: "반려 보고서", status: "revision_requested", citations: 1, review_request_id: "review-old", approval_request_id: "approval-old", content: { summary: "반려" } }], selectedOutputId: "output-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter })); });
    await act(async () => { buttonByText(container, "반려 보고서").dispatchEvent(new MinimalEvent("click")); });
    assert.match(container.textContent, /반려/);
    assert.doesNotMatch(container.textContent, /추가 인증 비밀번호/);
    await act(async () => { buttonByText(container, "검토 요청").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); }); await act(async () => { buttonByText(container, "승인 요청").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    await act(async () => { buttonByText(container, "승인").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls.map((item) => item[0]), ["reviews", "approval-requests", "approvals"]);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("Library 재진입은 Version 이력과 Raw·Daon Citation을 실제 DOM에 복원한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-history-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const adapter = { listStudioVersions: async () => [{
      output_version_id: "version-2", content_version: 2, previous_version_id: "version-1", status: "approved",
      content: { body: "승인된 혼합 근거 보고서" }, revision_type: "user_edit", change_reason: "검토 반영",
      settings_snapshot_id: "settings-2", output_format: "pdf", review_request_id: "review-1",
      approval_request_id: "approval-request-1", approval_id: "approval-1", delivery_id: null, knowledge_registration_id: null,
      citations: [
        { citation_id: "citation-daon", source_version_id: "source-version-daon", evidence_span_id: "span-daon", origin: "daon_knowledge", locator: { kind: "section", value: "핵심 요약" } },
        { citation_id: "citation-raw", source_version_id: "source-version-raw", evidence_span_id: "span-raw", origin: "raw_source", locator: { kind: "page", value: "2" } },
      ],
    }] };
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{ studio_output_id: "output-1", output_version_id: "version-1", title: "혼합 보고서", status: "draft" }], selectedOutputId: null, pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter })); });
    await act(async () => { buttonByText(container, "혼합 보고서").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /승인된 혼합 근거 보고서/);
    assert.match(container.textContent, /Version 2/);
    assert.match(container.textContent, /Daon 생성 지식 · 핵심 요약/);
    assert.match(container.textContent, /원본 지식 · 2쪽/);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("제약·준수 점검표 상세는 항목·판정·근거·조치를 구조화 표로 렌더한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-compliance-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{
      studio_output_id: "output-check-1", output_version_id: "version-check-1", output_type: "compliance_checklist",
      title: "제약·준수 점검표", status: "draft", settings_snapshot_id: "settings-check-1", citations: 1,
      content: { items: [{ item_id: "check-1", judgement: "needs_review", evidence: "citation-1 page 2", ruleset_id: "ruleset-v3", action: "전문가 검토" }], warnings: [], lineage: { ruleset_id: "ruleset-v3" } },
    }], selectedOutputId: "output-check-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: {} })); });
    assert.match(container.textContent, /제약·준수 점검 결과/);
    for (const label of ["항목", "판정", "근거", "조치", "check-1", "검토 필요", "citation-1 page 2", "전문가 검토"]) assert.match(container.textContent, new RegExp(label));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("비교·데이터 표 상세는 기준·현재·차이 상태·양쪽 근거를 구조화 표로 렌더한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-comparison-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{
      studio_output_id: "output-table-1", output_version_id: "version-table-1", output_type: "comparison_table",
      title: "비교·데이터 표", status: "draft", settings_snapshot_id: "settings-table-1", citations: 2,
      content: { rows: [{ key: "시장 규모", baseline: "100", current: "120", difference: ["100", "120"], state: "changed", evidence: ["citation-a page 1", "citation-b page 2"], baseline_version: "evidence", current_version: "result-1" }] },
    }], selectedOutputId: "output-table-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: {} })); });
    assert.match(container.textContent, /비교·데이터 결과/);
    for (const label of ["항목", "기준", "현재", "상태", "근거", "시장 규모", "100", "120", "변경", "citation-a page 1", "citation-b page 2"]) assert.match(container.textContent, new RegExp(label));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("지식 구조도 상세는 근거 노드와 관계를 배타적인 구조 뷰로 렌더한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-map-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{
      studio_output_id: "output-map-1", output_version_id: "version-map-1", output_type: "knowledge_map",
      title: "지식 구조도", status: "draft", settings_snapshot_id: "settings-map-1", citations: 2,
      content: { nodes: [{ id: "node-1", label: "승인 지식", confidence: "verified", evidence: "citation-daon section" }, { id: "node-2", label: "원본 PDF", confidence: "verified", evidence: "citation-raw page 2" }], edges: [{ id: "edge-1", source: "node-1", target: "node-2", condition: "근거 순서" }] },
    }], selectedOutputId: "output-map-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: {} })); });
    assert.match(container.textContent, /지식 구조 결과/);
    for (const label of ["승인 지식", "검증됨", "citation-daon section", "원본 PDF", "citation-raw page 2", "승인 지식 → 원본 PDF", "근거 순서"]) assert.match(container.textContent, new RegExp(label));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("업무 문서 초안 상세는 섹션 본문·근거·검토 상태를 문서 구조로 렌더한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-draft-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{
      studio_output_id: "output-draft-1", output_version_id: "version-draft-1", output_type: "business_draft",
      title: "업무 문서 초안", status: "draft", settings_snapshot_id: "settings-draft-1", citations: 2,
      content: { template_id: "letter", review_state: "draft", sections: [{ title: "요약", body: "승인 지식과 원본을 결합한 업무 요약", evidence: ["citation-daon page 1", "citation-raw page 2"] }], warnings: [], lineage: { request_id: "generation-1" } },
    }], selectedOutputId: "output-draft-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: {} })); });
    assert.match(container.textContent, /업무 문서 결과/);
    for (const label of ["초안", "요약", "승인 지식과 원본을 결합한 업무 요약", "citation-daon page 1", "citation-raw page 2"]) assert.match(container.textContent, new RegExp(label));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("Library는 다섯 산출물 유형을 구분하고 선택한 산출물의 통합 관리 화면을 연다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-library-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const outputs = [
      ["evidence_report", "근거 보고서", "pdf"], ["compliance_checklist", "준수 점검표", "xlsx"],
      ["comparison_table", "비교표", "xlsx"], ["knowledge_map", "지식 구조도", "json"],
      ["business_draft", "업무 문서", "docx"],
    ].map(([output_type, title, output_format], index) => ({
      studio_output_id: `output-${index + 1}`, output_version_id: `version-${index + 1}`,
      output_type, output_format, title, status: index === 0 ? "approved" : "draft",
      content_version: index + 1, source_count: index + 1, created_at: `2026-08-15T0${index}:00:00Z`,
      citations: [], content: { body: title },
    }));
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs, selectedOutputId: null, pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: { listStudioVersions: async () => [{ ...outputs[0], change_reason: "initial_generation" }] } })); });
    const library = findElements(container, (element) => element.getAttribute?.("class") === "studio-library")[0];
    for (const label of ["근거 기반 보고서", "제약·준수 점검표", "비교·데이터 표", "지식 구조도", "업무 문서 초안"]) assert.match(library.textContent, new RegExp(label));
    const reportButton = findElements(container, (element) => element.tagName === "BUTTON" && element.textContent.includes("근거 보고서"))[0];
    await act(async () => { reportButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.ok(findElements(container, (element) => element.getAttribute?.("aria-label") === "선택 산출물 상세").length);
    for (const action of ["편집 새 Version", "AI 재생성 새 Version", "검토 요청", "승인 요청", "내보내기"]) assert.match(container.textContent, new RegExp(action));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});
