import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const root = path.resolve(".");
const modelPath = path.join(root, "packages/ui/src/workspace-model.js");
const componentPath = path.join(root, "packages/ui/src/adaptive-workspace.jsx");
const sourcePanePath = path.join(root, "packages/ui/src/source-knowledge-pane.jsx");
const sourceModelPath = path.join(root, "packages/ui/src/source-knowledge-model.js");
const stylePath = path.join(root, "packages/ui/src/workspace.css");
const interactionPath = path.join(root, "packages/ui/src/workspace-interaction.js");
const productModelPath = path.join(root, "packages/ui/src/product-workspace-model.js");
const webFiles = [
  "apps/web/app/layout.jsx",
  "apps/web/app/page.jsx",
  "apps/web/app/workspaces/[workspace_id]/page.jsx",
  "apps/web/app/globals.css",
  "apps/web/next.config.mjs"
];

async function loadModel() {
  if (!existsSync(modelPath)) return null;
  return import(`${pathToFileURL(modelPath).href}?t=${Date.now()}`);
}

async function read(relative) {
  return readFile(path.join(root, relative), "utf8");
}

async function loadInteraction() {
  if (!existsSync(interactionPath)) return null;
  return import(`${pathToFileURL(interactionPath).href}?t=${Date.now()}`);
}

test("6개 경계값은 정확한 네 layout_mode를 결정한다", async () => {
  const model = await loadModel();
  assert.ok(model, "workspace-model.js가 아직 없다");
  assert.equal(model.getLayoutMode(599), "bottom-tabs");
  assert.equal(model.getLayoutMode(600), "single-pane");
  assert.equal(model.getLayoutMode(1023), "single-pane");
  assert.equal(model.getLayoutMode(1024), "two-pane");
  assert.equal(model.getLayoutMode(1439), "two-pane");
  assert.equal(model.getLayoutMode(1440), "three-pane");
});

test("WorkspaceViewState 정본은 모든 업무 상태를 소유한다", async () => {
  const model = await loadModel();
  assert.ok(model, "workspace-model.js가 아직 없다");
  const state = model.createWorkspaceViewState();
  const fields = [
    "workspace_id", "active_pane", "secondary_pane", "open_drawer",
    "selected_source_id", "conversation_id", "run_id", "run_status",
    "artifact_id", "artifact_cursor", "evidence_id", "evidence_position",
    "pane_sizes", "last_transition"
  ];
  for (const field of fields) assert.ok(Object.hasOwn(state, field), `missing ${field}`);
  assert.equal(state.run_status, "unavailable");
});

test("폭·Pane·Drawer 전환은 업무 상태와 숨은 면 상태를 초기화하지 않는다", async () => {
  const model = await loadModel();
  assert.ok(model, "workspace-model.js가 아직 없다");
  const before = model.createWorkspaceViewState();
  const businessFields = [
    "workspace_id", "selected_source_id", "conversation_id", "run_id", "run_status",
    "artifact_id", "artifact_cursor", "evidence_id", "evidence_position"
  ];
  const projected = model.projectWorkspace(before, 800);
  const changed = model.transitionWorkspace(before, { type: "activate-pane", pane: "studio" }, "test-transition");
  const drawer = model.transitionWorkspace(changed, { type: "open-drawer", pane: "knowledge" }, "test-drawer");
  for (const field of businessFields) {
    assert.deepEqual(projected.state[field], before[field], `projection changed ${field}`);
    assert.deepEqual(drawer[field], before[field], `transition changed ${field}`);
  }
  assert.deepEqual(model.projectWorkspace(changed, 1200).visiblePanes, ["conversation", "studio"]);
  assert.deepEqual(model.projectWorkspace(drawer, 800).visiblePanes, ["studio"]);
});

test("근거 Viewer 위치와 Studio 편집 Cursor는 닫기·재열기 뒤 복원된다", async () => {
  const model = await loadModel();
  assert.ok(model, "workspace-model.js가 아직 없다");
  const before = model.createWorkspaceViewState();
  const moved = model.transitionWorkspace(before, { type: "set-evidence-position", position: "page-18:paragraph-2" }, "test-position");
  const opened = model.transitionWorkspace(moved, { type: "open-evidence" }, "test-open");
  const closed = model.transitionWorkspace(opened, { type: "close-overlay" }, "test-close");
  const reopened = model.transitionWorkspace(closed, { type: "open-evidence" }, "test-reopen");
  assert.equal(reopened.evidence_position, "page-18:paragraph-2");
  assert.equal(reopened.artifact_cursor, before.artifact_cursor);
  assert.equal(reopened.open_drawer, "evidence");
});

test("Drawer에서 연 Viewer를 닫으면 Drawer 문맥으로 한 단계만 복귀한다", async () => {
  const model = await loadModel();
  const initial = model.createWorkspaceViewState();
  const drawer = model.transitionWorkspace(initial, { type: "open-drawer", pane: "knowledge" }, "drawer");
  const viewer = model.transitionWorkspace(drawer, { type: "open-evidence" }, "viewer");
  assert.equal(viewer.open_drawer, "evidence");
  assert.equal(viewer.return_drawer, "knowledge");
  const returned = model.transitionWorkspace(viewer, { type: "close-overlay" }, "return");
  assert.equal(returned.open_drawer, "knowledge");
  assert.equal(returned.return_drawer, null);
});

test("Resize는 양쪽 Pane을 20~55로 제한하고 합계를 보존한다", async () => {
  const model = await loadModel();
  assert.equal(typeof model?.resizePaneSizes, "function", "검증 가능한 Resize 상태 함수가 필요하다");
  const cases = [
    [{ knowledge: 30, conversation: 38, studio: 32 }, "knowledge", 100],
    [{ knowledge: 30, conversation: 38, studio: 32 }, "knowledge", -100],
    [{ knowledge: 55, conversation: 13, studio: 32 }, "knowledge", 100],
    [{ knowledge: 55, conversation: 48, studio: -3 }, "conversation", 100]
  ];
  for (const [before, pane, delta] of cases) {
    const beforeSum = Object.values(before).reduce((sum, value) => sum + value, 0);
    let after = model.resizePaneSizes(before, pane, delta);
    for (let index = 0; index < 20; index += 1) after = model.resizePaneSizes(after, pane, index % 2 ? -100 : 100);
    const afterSum = Object.values(after).reduce((sum, value) => sum + value, 0);
    assert.ok(Object.values(after).every((value) => value >= 20 && value <= 55), JSON.stringify(after));
    assert.ok(Math.abs(afterSum - beforeSum) < 0.000001, `${beforeSum} != ${afterSum}`);
  }
});

test("Modal helper는 최초 Focus·Tab 순환·배경 inert를 실제 객체에 적용한다", async () => {
  const interaction = await loadInteraction();
  assert.ok(interaction, "workspace-interaction.js가 필요하다");
  const focused = [];
  const first = { focus: () => focused.push("first") };
  const last = { focus: () => focused.push("last") };
  const modal = {
    querySelector: () => first,
    querySelectorAll: () => [first, last]
  };
  interaction.focusInitialModalControl(modal);
  const forward = { key: "Tab", shiftKey: false, preventDefault() { this.prevented = true; } };
  interaction.trapModalTab(modal, forward, last);
  const backward = { key: "Tab", shiftKey: true, preventDefault() { this.prevented = true; } };
  interaction.trapModalTab(modal, backward, first);
  const background = { inert: false, attributes: {}, setAttribute(name, value) { this.attributes[name] = value; }, removeAttribute(name) { delete this.attributes[name]; } };
  interaction.setBackgroundInert(background, true);
  assert.deepEqual(focused, ["first", "first", "last"]);
  assert.equal(forward.prevented, true);
  assert.equal(backward.prevented, true);
  assert.equal(background.inert, true);
  assert.equal(background.attributes.inert, "");
  assert.equal(background.attributes["aria-hidden"], "true");
  interaction.setBackgroundInert(background, false);
  assert.equal(background.inert, false);
  assert.equal(Object.hasOwn(background.attributes, "inert"), false);
  assert.equal(Object.hasOwn(background.attributes, "aria-hidden"), false);
});

test("Help 상태는 Hover·Focus·Touch/Click으로 열리고 Escape·Blur로 닫힌다", async () => {
  const interaction = await loadInteraction();
  assert.equal(typeof interaction?.transitionHelp, "function");
  for (const action of ["pointer-enter", "focus", "toggle"]) assert.equal(interaction.transitionHelp(false, action), true, action);
  for (const action of ["escape", "blur", "pointer-leave", "close"]) assert.equal(interaction.transitionHelp(true, action), false, action);
});

test("세 Pane·Drawer·Bottom Tab·Evidence Viewer와 접근성 동작 계약이 존재한다", async () => {
  assert.ok(existsSync(componentPath), "adaptive-workspace.jsx가 아직 없다");
  assert.ok(existsSync(stylePath), "workspace.css가 아직 없다");
  const source = `${await read("packages/ui/src/adaptive-workspace.jsx")}\n${await read("packages/ui/src/source-knowledge-pane.jsx")}\n${await read("packages/ui/src/run-model-evidence-pane.jsx")}\n${await read("packages/ui/src/studio-workflow-pane.jsx")}\n${await read("packages/ui/src/workspace.css")}`;
  for (const id of ["pane-knowledge", "pane-conversation", "pane-studio", "workspace-drawer", "bottom-tabs", "evidence-viewer"])
    assert.match(source, new RegExp(id), `missing ${id}`);
  assert.match(source, /role="separator"/);
  assert.match(source, /aria-label=/);
  assert.match(source, /role="tooltip"/);
  assert.match(source, /aria-describedby=/);
  assert.match(source, /aria-expanded=/);
  assert.doesNotMatch(source, /aria-label=\{label\}[^>]*title=/);
  assert.match(source, /Escape/);
  assert.match(source, /\.focus\(\)/);
  assert.match(source, /overlayTriggerId/);
  assert.match(source, /getElementById/);
  assert.match(source, /accessibility-contract\.json/);
  assert.match(source, /tokens\.css/);
  assert.match(source, /프로토타입 데이터/);
  assert.match(source, /unavailable/);
  for (const attribute of ["data-run-id", "data-artifact-id", "data-evidence-id", "data-evidence-position"])
    assert.match(source, new RegExp(attribute));
});

test("Web Home은 비인증 AuthPane만 제공하고 내부 API 주소를 갖지 않는다", async () => {
  for (const relative of webFiles) assert.ok(existsSync(path.join(root, relative)), `missing ${relative}`);
  const browserSource = [
    await read("packages/ui/src/adaptive-workspace.jsx"),
    await read("packages/ui/src/source-knowledge-pane.jsx"),
    await read("packages/ui/src/source-knowledge-model.js"),
    await read("packages/ui/src/run-model-evidence-pane.jsx"),
    await read("packages/ui/src/run-model-evidence-model.js"),
    await read("packages/ui/src/workspace-model.js"),
    ...(await Promise.all(webFiles.map(read)))
  ].join("\n");
  assert.doesNotMatch(browserSource, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL|fetch\s*\(/i);
  const home = await read("apps/web/app/page.jsx");
  const workspace = await read("apps/web/app/workspaces/[workspace_id]/page.jsx");
  assert.match(home, /AuthPane/);
  assert.doesNotMatch(home, /ProductionBoundEvidenceHub|AdaptiveWorkspace|navigation\.json|screens\.json/);
  assert.match(workspace, /ActualWorkspace/);
  assert.match(workspace, /workspace_detail/);
});

test("Product Workspace는 승인된 Safe 상태만 만들고 Fixture 성공 데이터를 생성하지 않는다", async () => {
  assert.equal(existsSync(productModelPath), true, "product-workspace-model.js가 필요하다");
  const model = await import(`${pathToFileURL(productModelPath).href}?t=${Date.now()}`);
  assert.deepEqual(model.PRODUCT_WORKSPACE_STATES, ["loading", "empty", "ready", "error", "forbidden", "unavailable"]);
  for (const status of model.PRODUCT_WORKSPACE_STATES) {
    const state = model.createProductWorkspaceState({ status });
    assert.deepEqual(state, { status, sources: [], selectedSource: null, answer: null, studioOutputs: [], safeError: null });
  }
  assert.throws(() => model.createProductWorkspaceState({ status: "prototype" }), /WORKSPACE_STATE_INVALID/);
  assert.throws(() => model.normalizeProductWorkspaceState({ status: "error", sources: [], selectedSource: null, answer: null, studioOutputs: [], safeError: "http://internal:8000 password=secret" }), /WORKSPACE_SAFE_ERROR_INVALID/);
});

test("Product Workspace Shell은 3면 Safe 상태를 렌더하고 Prototype Token을 포함하지 않는다", async () => {
  const shell = await read("packages/ui/src/product-workspace-shell.jsx");
  for (const pane of ["product-pane-sources", "product-pane-conversation", "product-pane-studio"]) assert.match(shell, new RegExp(pane));
  assert.match(shell, /loading|empty|ready|error|forbidden|unavailable/);
  assert.doesNotMatch(shell, /ProductionBoundEvidenceHub|prototype_fixture|deferred_actual|Mock Adapter/);
});

test("Stage A 실제 Workspace Route는 로그인 Workspace ID와 Safe Shell만 연결한다", async () => {
  const page = await read("apps/web/app/workspaces/[workspace_id]/page.jsx");
  const actualWorkspace = await read("apps/web/components/actual-workspace.jsx");
  const uploadClient = await read("apps/web/lib/source-upload-api.js");
  const authPane = await read("apps/web/lib/auth-pane.jsx");

  assert.match(page, /params/);
  assert.match(page, /workspace_id/);
  assert.match(page, /ActualWorkspace/);
  assert.match(actualWorkspace, /ProductWorkspaceShell/);
  assert.match(actualWorkspace, /createProductWorkspaceState/);
  assert.match(actualWorkspace, /status:\s*"unavailable"/);
  assert.match(actualWorkspace, /workspaceId/);
  assert.match(actualWorkspace, /uploadPdfSource/);
  assert.match(actualWorkspace, /getDocumentProcessingStatus/);
  assert.match(actualWorkspace, /askGroundedQuestion/);
  assert.match(actualWorkspace, /citationContentUrl/);
  assert.match(authPane, /result\?\.data\?\.workspace_id/);
  assert.match(authPane, /window\.location\.assign\(`\/workspaces\/\$\{encodeURIComponent\(result\.data\.workspace_id\)\}`\)/);
  assert.match(authPane, /WORKSPACE_REQUIRED/);
  assert.doesNotMatch(authPane, /error\.message|error\.stack|console\./);
  assert.match(uploadClient, /\/bff\/api\/workspaces\/\$\{encodeURIComponent\(workspaceId\)\}\/sources/);
  assert.match(uploadClient, /\/bff\/api\/workspaces\/\$\{encodeURIComponent\(workspaceId\)\}\/processing-runs\/\$\{encodeURIComponent\(processingRunId\)\}/);
  assert.doesNotMatch(`${page}\n${actualWorkspace}\n${uploadClient}`, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
});

test("M2-01 Route·Screen·Token 정본은 수정 없이 직접 소비된다", async () => {
  const component = existsSync(componentPath) ? await read("packages/ui/src/adaptive-workspace.jsx") : "";
  const web = existsSync(path.join(root, "apps/web/app/workspaces/[workspace_id]/page.jsx")) ? await read("apps/web/app/workspaces/[workspace_id]/page.jsx") : "";
  assert.match(component, /tokens\.css/);
  assert.match(component, /accessibility-contract\.json/);
  assert.match(web, /navigation\.json/);
  assert.match(web, /screens\.json/);
});

test("Breakpoint와 Control Target CSS는 M2-01 Token 정본에 고정된다", async () => {
  const tokens = JSON.parse(await read("packages/design-tokens/tokens.json"));
  const css = await read("packages/ui/src/workspace.css");
  assert.match(css, /\.resize-handle\s*\{[^}]*width:\s*var\(--daon-target-minimum\)/s);
  assert.match(css, /\.icon-button[^}]*width:\s*var\(--daon-target-desktop-control\)/s);
  assert.match(css, /select\s*\{[^}]*min-height:\s*var\(--daon-target-desktop-control\)/s);
  assert.match(css, /@media \(max-width: 1439px\)/);
  assert.match(css, /@media \(max-width: 1023px\)/);
  assert.match(css, /@media \(max-width: 599px\)/);
  assert.deepEqual(tokens.breakpoints, {wide:{min:1440},desktop:{min:1024,max:1439},tablet:{min:600,max:1023},mobile:{max:599}});
  assert.deepEqual(tokens.target_size, {minimum:"24px",desktop_control:"32px",touch_control:"44px"});
});
