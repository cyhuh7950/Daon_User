import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const root = path.resolve(".");
const modelPath = path.join(root, "packages/ui/src/source-knowledge-model.js");
const controlsPath = path.join(root, "packages/ui/src/source-knowledge-controls.js");
const workspaceModelPath = path.join(root, "packages/ui/src/workspace-model.js");

async function loadModel() {
  assert.ok(existsSync(modelPath), "source-knowledge-model.js가 필요하다");
  return import(`${pathToFileURL(modelPath).href}?t=${Date.now()}`);
}

async function loadControls() {
  assert.ok(existsSync(controlsPath), "React 렌더 계약을 가진 source-knowledge-controls.js가 필요하다");
  return import(`${pathToFileURL(controlsPath).href}?t=${Date.now()}`);
}

async function loadWorkspaceModel() {
  return import(`${pathToFileURL(workspaceModelPath).href}?t=${Date.now()}`);
}

async function read(relative) {
  return readFile(path.join(root, relative), "utf8");
}

test("다섯 지식 원천과 RuleSet은 별도 계약이며 권위 순서를 바꿀 수 없다", async () => {
  const model = await loadModel();
  assert.deepEqual(model.SOURCE_TYPES, ["user_material", "internet", "llm_knowledge", "daon_approved", "produced_knowledge"]);
  assert.equal(model.RULESET_TYPE, "ruleset");
  assert.ok(!model.SOURCE_TYPES.includes(model.RULESET_TYPE));
  assert.deepEqual(model.AUTHORITY_ORDER, ["mandatory_ruleset", "daon_approved", "user_context", "verified_internet", "llm_knowledge"]);
  assert.equal(model.compareAuthority("daon_approved", "llm_knowledge") < 0, true);
});

test("가중치는 0.5~2.0과 0.1 단위를 강제하고 가장 구체적인 값 하나만 Clamp한다", async () => {
  const model = await loadModel();
  assert.deepEqual(model.WEIGHT_CONTRACT, { minimum: 0.5, maximum: 2, step: 0.1, defaultValue: 1 });
  const resolved = model.resolveWeight({
    source: 1.9,
    group: 1.7,
    type: 1.5,
    defaultValue: 1,
    organizationMinimum: 0.8,
    organizationMaximum: 1.6
  });
  assert.deepEqual(resolved, {
    requested: 1.9,
    applied: 1.6,
    layer: "source",
    clampReason: "조직 허용 범위 0.8~1.6 적용",
    multiplied: false
  });
  assert.throws(() => model.resolveWeight({ source: 1.55 }), /0.1/);
  assert.throws(() => model.resolveWeight({ source: 0 }), /0.5~2.0/);
});

test("문서와 오디오 두 경로는 의미 이해와 검증을 모두 통과해야 ready다", async () => {
  const model = await loadModel();
  const document = model.PROCESSING_PATHS.document;
  const audioDirect = model.PROCESSING_PATHS.audio_direct;
  const audioAsr = model.PROCESSING_PATHS.audio_asr_llm;
  assert.deepEqual(document.steps, ["vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation", "indexing"]);
  assert.deepEqual(audioDirect.steps, ["audio_llm_understanding", "transcript_timecode_validation", "evidence_reconciliation", "indexing"]);
  assert.deepEqual(audioAsr.steps, ["speech_to_text", "llm_semantic_understanding", "transcript_timecode_validation", "evidence_reconciliation", "indexing"]);
  assert.equal(model.evaluateReadyGate("document", ["parser_ocr_validation", "evidence_reconciliation", "indexing"]).ready, false);
  assert.equal(model.evaluateReadyGate("audio_asr_llm", ["speech_to_text", "transcript_timecode_validation", "evidence_reconciliation", "indexing"]).ready, false);
  assert.equal(model.evaluateReadyGate("audio_direct", audioDirect.steps).ready, true);
  assert.equal(model.evaluateReadyGate("audio_asr_llm", audioAsr.steps).ready, true);
});

test("정책 후보 0과 Runtime 소진, partial·failed·expired를 서로 다른 Source 상태로 공개한다", async () => {
  const model = await loadModel();
  const seeds = model.createSourcePrototypeSeed();
  const sourceStates = new Set(seeds.sources.map((source) => source.status));
  for (const status of ["ready", "waiting_model", "partial_understanding", "needs_review", "failed", "expired"])
    assert.ok(sourceStates.has(status), `missing ${status}`);
  const policyBlocked = seeds.sources.find((source) => source.processingRun?.status === "policy_blocked");
  assert.equal(policyBlocked.status, "needs_review");
  const runtimeExhausted = seeds.sources.find((source) => source.processingRun?.code === "NO_AVAILABLE_UNDERSTANDING_MODEL");
  assert.equal(runtimeExhausted.status, "waiting_model");
  assert.equal(runtimeExhausted.retryAction.availability, "unavailable");
});

test("강제 RuleSet은 잠기고 선택형 실패 방식은 구분된다", async () => {
  const model = await loadModel();
  const rulesets = model.createSourcePrototypeSeed().rulesets;
  const mandatory = rulesets.find((ruleset) => ruleset.binding === "mandatory");
  assert.equal(mandatory.locked, true);
  assert.equal(model.toggleRuleSet(mandatory, false), mandatory);
  assert.deepEqual(new Set(rulesets.filter((ruleset) => ruleset.binding === "optional").map((ruleset) => ruleset.failureMode)), new Set(["warn_and_skip", "block"]));
});

test("중요 충돌은 자동 판정·상향만 허용하고 해결 전 최종화 3종을 차단한다", async () => {
  const model = await loadModel();
  const conflict = model.createSourcePrototypeSeed().conflicts.find((item) => item.severity === "critical");
  assert.equal(conflict.reviewRequired, true);
  assert.deepEqual(model.getFinalizationLocks([conflict]), ["approval", "external_delivery", "knowledge_registration"]);
  assert.equal(model.raiseConflictSeverity(conflict, "material"), conflict);
  assert.equal(model.raiseConflictSeverity(conflict, "critical"), conflict);
  const resolved = model.resolveConflict(conflict, "reviewer-prototype");
  assert.equal(resolved.resolution.status, "resolved");
  assert.deepEqual(model.getFinalizationLocks([resolved]), []);
});

test("Source Version은 불변이며 생산 지식은 명시 등록 상태만 가진다", async () => {
  const model = await loadModel();
  const seeds = model.createSourcePrototypeSeed();
  const produced = seeds.sources.find((source) => source.type === "produced_knowledge");
  assert.equal(produced.registration, "explicit_required");
  assert.equal(produced.daonPromotion, "not_automatic");
  const original = seeds.sources[0].versions[0];
  const next = model.createNextSourceVersion(original, { digest: "sha256:new-prototype" });
  assert.notEqual(next.id, original.id);
  assert.equal(original.digest, seeds.sources[0].versions[0].digest);
  assert.equal(next.previousVersionId, original.id);
});

test("자료·지식 Prototype은 접근성·반응형·unavailable·금지 URL 0건 계약을 가진다", async () => {
  const files = [
    "packages/ui/src/source-knowledge-pane.jsx",
    "packages/ui/src/source-knowledge-controls.js",
    "packages/ui/src/source-knowledge-model.js",
    "packages/ui/src/adaptive-workspace.jsx",
    "packages/ui/src/workspace.css"
  ];
  for (const file of files) assert.ok(existsSync(path.join(root, file)), `missing ${file}`);
  const source = (await Promise.all(files.map(read))).join("\n");
  for (const text of ["프로토타입 데이터", "unavailable", "Vision/LLM-first", "ASR + LLM", "review_required=true", "ConflictPolicyVersion"])
    assert.match(source, new RegExp(text.replace(/[+/]/g, "\\$&")));
  assert.match(source, /aria-label=/);
  assert.match(source, /role="tooltip"/);
  assert.match(source, /onKeyDown[\s\S]*Escape/);
  assert.match(source, /data-modal-initial-focus/);
  assert.match(source, /44px|--daon-target-touch-control/);
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL|fetch\s*\(/i);
});

test("가중치 UI는 group·type·default 실제 계층과 Source Override 추가·해제를 React 렌더한다", async () => {
  const model = await loadModel();
  const { WeightControl } = await loadControls();
  const seed = model.createSourcePrototypeSeed();
  const cases = [
    ["source-user-report", "group"],
    ["source-audio-direct", "type"],
    ["source-runtime-wait", "default"]
  ];
  for (const [sourceId, expectedLayer] of cases) {
    const source = seed.sources.find((item) => item.id === sourceId);
    const html = renderToStaticMarkup(createElement(WeightControl, { source, overrideValue: undefined }));
    assert.match(html, new RegExp(`data-weight-layer="${expectedLayer}"`));
    assert.match(html, /개별 Source Override 추가/);
  }
  const source = seed.sources.find((item) => item.id === "source-user-report");
  const overridden = renderToStaticMarkup(createElement(WeightControl, { source, overrideValue: 1.8 }));
  assert.match(overridden, /data-weight-layer="source"/);
  assert.match(overridden, /개별 Source Override 해제/);
  const cleared = model.resolveWeight(source.weightProfile);
  assert.equal(cleared.layer, "group");
});

test("Evidence Snapshot은 선택 Source·Version·근거를 Workspace 상태에 함께 기록한다", async () => {
  const model = await loadModel();
  const workspace = await loadWorkspaceModel();
  const seed = model.createSourcePrototypeSeed();
  const document = seed.sources.find((item) => item.id === "source-user-report");
  const audioDirect = seed.sources.find((item) => item.id === "source-audio-direct");
  const audioAsr = seed.sources.find((item) => item.id === "source-audio-asr");
  const snapshots = [document, audioDirect, audioAsr].map((source) => model.selectEvidenceSnapshot(source, source.versions.at(-1).id));
  assert.equal(new Set(snapshots.map((item) => item.sourceId)).size, 3);
  assert.equal(new Set(snapshots.map((item) => item.position)).size, 3);
  let state = workspace.createWorkspaceViewState();
  state = workspace.transitionWorkspace(state, { type: "open-evidence", evidence: snapshots[1] }, "evidence-open");
  assert.equal(state.evidence_source_id, audioDirect.id);
  assert.equal(state.evidence_source_version_id, audioDirect.versions.at(-1).id);
  assert.equal(state.evidence_id, snapshots[1].id);
  assert.equal(state.evidence_position, snapshots[1].position);
  state = workspace.transitionWorkspace(state, { type: "close-overlay" }, "evidence-close");
  state = workspace.transitionWorkspace(state, { type: "open-evidence" }, "evidence-reopen");
  assert.equal(state.evidence_source_version_id, audioDirect.versions.at(-1).id);
  assert.equal(state.evidence_position, snapshots[1].position);
});

test("SourceKnowledgeViewState는 Pane 재마운트와 폭 전환 뒤 Domain 상태를 보존한다", async () => {
  const model = await loadModel();
  const workspace = await loadWorkspaceModel();
  let state = workspace.createWorkspaceViewState();
  const transitions = [
    { type: "set-tab", tab: "authority" },
    { type: "set-registration-open", open: true },
    { type: "select-version", sourceId: "source-user-report", versionId: "source-user-report-v1" },
    { type: "set-weight-override", sourceId: "source-user-report", value: 1.8 },
    { type: "toggle-ruleset", rulesetId: "ruleset-optional-style", enabled: false },
    { type: "resolve-conflict", conflictId: "conflict-material-001", reviewer: "reviewer-prototype" }
  ];
  for (const domainAction of transitions)
    state = workspace.transitionWorkspace(state, { type: "source-knowledge", domainAction }, `domain-${domainAction.type}`);
  for (const width of [500, 800, 1200, 1920]) workspace.projectWorkspace(state, width);
  const restored = model.createSourceKnowledgeViewState(state.source_knowledge);
  assert.equal(restored.activeTab, "authority");
  assert.equal(restored.registrationOpen, true);
  assert.equal(restored.versionBySource["source-user-report"], "source-user-report-v1");
  assert.equal(restored.weightOverrides["source-user-report"], 1.8);
  assert.equal(restored.rulesets.find((item) => item.id === "ruleset-optional-style").enabled, false);
  assert.equal(restored.conflicts.find((item) => item.id === "conflict-material-001").resolution.status, "resolved");
});

test("활성 Recovery Control은 Audit·상태를 바꾸고 실행 범위 밖 최종화는 항상 unavailable이다", async () => {
  const model = await loadModel();
  let domain = model.createSourceKnowledgeViewState();
  domain = model.transitionSourceKnowledgeState(domain, { type: "request-review", sourceId: "source-partial" });
  assert.equal(domain.sourceStateById["source-partial"].status, "needs_review");
  assert.match(domain.sourceStateById["source-partial"].audit.at(-1).action, /review/);
  domain = model.transitionSourceKnowledgeState(domain, { type: "disable-source", sourceId: "source-partial" });
  assert.equal(domain.sourceStateById["source-partial"].active, false);
  assert.equal(domain.sourceStateById["source-partial"].status, "disabled");
  assert.match(domain.sourceStateById["source-partial"].audit.at(-1).action, /disable/);
  const paneSource = await read("packages/ui/src/source-knowledge-pane.jsx");
  assert.match(paneSource, /Prototype · unavailable/);
  assert.doesNotMatch(paneSource, /disabled=\{locks\.includes\(id\)\}/);
});

test("문서와 오디오 Source는 현재·과거 불변 Version별 Digest·시각·Evidence를 제공한다", async () => {
  const model = await loadModel();
  const seed = model.createSourcePrototypeSeed();
  for (const sourceId of ["source-user-report", "source-audio-direct"]) {
    const source = seed.sources.find((item) => item.id === sourceId);
    assert.ok(source.versions.length >= 2, `${sourceId}는 과거 Version이 필요하다`);
    const [past, current] = source.versions.slice(-2);
    assert.equal(Object.isFrozen(past), true);
    assert.equal(Object.isFrozen(current), true);
    assert.equal(current.previousVersionId, past.id);
    assert.notEqual(current.digest, past.digest);
    assert.notEqual(current.capturedAt, past.capturedAt);
    assert.notEqual(current.evidence.position, past.evidence.position);
  }
});

test("ConflictPolicyVersion은 입력 사실만으로 informational·material·critical을 결정한다", async () => {
  const model = await loadModel();
  assert.equal(Object.isFrozen(model.CONFLICT_POLICY_VERSION), true);
  assert.equal(model.classifyConflict({ affectsOutcome: false, unresolved: true }), "informational");
  assert.equal(model.classifyConflict({ affectsOutcome: true, unresolved: true, sameTier: true, importantClaim: true }), "material");
  assert.equal(model.classifyConflict({ affectsOutcome: true, unresolved: true, mandatoryRuleSetActive: true }), "critical");
  assert.equal(model.classifyConflict({ affectsOutcome: true, unresolved: true, daonApprovedInvolved: true }), "critical");
  const seed = model.createSourcePrototypeSeed();
  for (const conflict of seed.conflicts) assert.equal(conflict.severity, model.classifyConflict(conflict.facts));
});

test("Tooltip은 열린 때만 aria-describedby로 실제 Tooltip ID를 연결한다", async () => {
  const { Help } = await loadControls();
  const closed = renderToStaticMarkup(createElement(Help, { id: "weight", label: "가중치 설명", initialOpen: false }, "설명"));
  assert.doesNotMatch(closed, /aria-describedby=/);
  assert.doesNotMatch(closed, /role="tooltip"/);
  const open = renderToStaticMarkup(createElement(Help, { id: "weight", label: "가중치 설명", initialOpen: true }, "설명"));
  assert.match(open, /aria-describedby="source-help-weight"/);
  assert.match(open, /id="source-help-weight"[^>]*role="tooltip"/);
  const controlsSource = await read("packages/ui/src/source-knowledge-controls.js");
  for (const eventName of ["onFocus", "onPointerEnter", "onClick", "onBlur", "Escape"])
    assert.match(controlsSource, new RegExp(eventName));
});

test("Tooltip의 첫 Click은 선행 Focus 뒤에도 열린 상태를 유지한다", async () => {
  const controls = await loadControls();
  const afterFocus = controls.reduceHelpOpen(false, "focus");
  const afterClick = controls.reduceHelpOpen(afterFocus, "open");
  assert.equal(afterFocus, true);
  assert.equal(afterClick, true);
  assert.match(await read("packages/ui/src/source-knowledge-controls.js"), /reduceHelpOpen\(current, action\)/);
});

test("검토 요청 후 Source 목록과 선택 상세는 같은 needs_review 상태를 표시한다", async () => {
  const model = await loadModel();
  let domain = model.createSourceKnowledgeViewState();
  domain = model.transitionSourceKnowledgeState(domain, { type: "request-review", sourceId: "source-partial" });
  const seedSource = model.createSourcePrototypeSeed().sources.find((source) => source.id === "source-partial");
  const projected = model.projectSourceState(seedSource, domain.sourceStateById[seedSource.id]);
  assert.equal(projected.status, "needs_review");
  assert.equal(projected.active, false);
  assert.match(await read("packages/ui/src/source-knowledge-pane.jsx"), /seed\.sources\.map[\s\S]*projectSourceState/);
});

test("Parser/OCR 불일치는 needs_review이며 failed·expired는 다음 복구 진입을 공개한다", async () => {
  const model = await loadModel();
  const seed = model.createSourcePrototypeSeed();
  const mismatch = seed.sources.find((source) => source.processingRun?.code === "EVIDENCE_RECONCILIATION_FAILED");
  const failed = seed.sources.find((source) => source.status === "failed");
  const expired = seed.sources.find((source) => source.status === "expired");
  assert.equal(mismatch.status, "needs_review");
  assert.equal(mismatch.processingRun.status, "needs_review");
  assert.equal(mismatch.processingRun.readyGate, "needs_review");
  assert.match(failed.retryAction.label, /재처리|재등록/);
  assert.match(expired.retryAction.label, /재처리|재등록/);
  const paneSource = await read("packages/ui/src/source-knowledge-pane.jsx");
  assert.match(paneSource, /source\.retryAction/);
  assert.match(paneSource, /source\.processingRun\.code/);
});

test("충돌 검토자는 심각도를 상향하고 해결·상향 행동을 Audit Preview에서 확인한다", async () => {
  const model = await loadModel();
  let domain = model.createSourceKnowledgeViewState({ activeTab: "conflicts" });
  domain = model.transitionSourceKnowledgeState(domain, {
    type: "raise-conflict-severity",
    conflictId: "conflict-info-001",
    severity: "material",
    reviewer: "reviewer-prototype"
  });
  domain = model.transitionSourceKnowledgeState(domain, {
    type: "resolve-conflict",
    conflictId: "conflict-material-001",
    reviewer: "reviewer-prototype"
  });
  const raised = domain.conflicts.find((conflict) => conflict.id === "conflict-info-001");
  const resolved = domain.conflicts.find((conflict) => conflict.id === "conflict-material-001");
  assert.equal(raised.severity, "material");
  assert.equal(raised.audit.at(-1).action, "severity_raised");
  assert.equal(raised.audit.at(-1).reviewer, "reviewer-prototype");
  assert.equal(resolved.resolution.status, "resolved");
  assert.equal(resolved.audit.at(-1).action, "conflict_resolved");
  const paneSource = await read("packages/ui/src/source-knowledge-pane.jsx");
  assert.match(paneSource, /raise-conflict-severity/);
  assert.match(paneSource, /critical로 상향/);
  assert.match(paneSource, /conflict\.audit/);
  assert.match(paneSource, /resolution\.action/);
});

test("해결된 중요 충돌을 상향하면 해결이 무효화되고 최종화 차단이 다시 열린다", async () => {
  const model = await loadModel();
  const material = model.createSourcePrototypeSeed().conflicts.find((conflict) => conflict.id === "conflict-material-001");
  const resolved = model.resolveConflict(material, "reviewer-prototype");
  assert.deepEqual(model.getFinalizationLocks([resolved]), []);
  const raised = model.raiseConflictSeverity(resolved, "critical", "reviewer-prototype");
  assert.equal(raised.severity, "critical");
  assert.equal(raised.resolution.status, "unresolved");
  assert.equal(raised.audit.at(-1).result, "review_reopened");
  assert.deepEqual(model.getFinalizationLocks([raised]), ["approval", "external_delivery", "knowledge_registration"]);
});
