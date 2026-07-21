import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const root = path.resolve(".");
const modelPath = path.join(root, "packages/ui/src/studio-workflow-model.js");
const modelUrl = pathToFileURL(modelPath).href;
const loadModel = () => import(`${modelUrl}?t=${Date.now()}`);
const read = (relative) => readFile(path.join(root, relative), "utf8");

test("다섯 Tile은 필수 구성과 허용 출력 형식을 정확히 고정한다", async () => {
  const model = await loadModel();
  assert.deepEqual(Object.keys(model.OUTPUT_TYPES), ["evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft"]);
  assert.deepEqual(model.OUTPUT_TYPES.evidence_report.formats, ["DOCX", "PDF"]);
  assert.deepEqual(model.OUTPUT_TYPES.compliance_checklist.formats, ["XLSX", "CSV", "PDF"]);
  assert.deepEqual(model.OUTPUT_TYPES.comparison_table.formats, ["XLSX", "CSV", "PDF"]);
  assert.deepEqual(model.OUTPUT_TYPES.knowledge_map.formats, ["JSON", "SVG", "PNG", "PDF"]);
  assert.deepEqual(model.OUTPUT_TYPES.business_draft.formats, ["DOCX", "PDF"]);
  assert.deepEqual(model.OUTPUT_TYPES.evidence_report.sections, ["요약", "본문", "결론", "인용", "경고", "미확인 사항"]);
});

test("Tile 선택은 configuring만 만들고 확정 전 제출과 정책 완화를 거부한다", async () => {
  const model = await loadModel();
  let state = model.createStudioViewState();
  state = model.transitionStudioViewState(state, { type: "select-output", outputType: "knowledge_map" });
  assert.equal(state.request.status, "configuring");
  assert.equal(state.revisions.length, 0);
  assert.equal(state.versions.length, 0);
  assert.equal(model.transitionStudioViewState(state, { type: "submit" }).safety.code, "SETTINGS_NOT_CONFIRMED");
  const locked = model.transitionStudioViewState(state, { type: "update-setting", field: "egressPolicy", value: "unrestricted" });
  assert.equal(locked.settings.egressPolicy, state.settings.egressPolicy);
  assert.equal(locked.safety.code, "LOCKED_POLICY_CANNOT_BE_RELAXED");
});

test("확정 Snapshot은 전체 설정·잠금 버전을 깊은 불변으로 고정한다", async () => {
  const model = await loadModel();
  const confirmed = model.transitionStudioViewState(model.createStudioViewState(), { type: "confirm" });
  assert.equal(confirmed.request.status, "confirmed");
  assert.equal(Object.isFrozen(confirmed.snapshot), true);
  assert.equal(Object.isFrozen(confirmed.snapshot.settings.sourceSelection.sourceVersionIds), true);
  for (const field of ["actorId", "workspaceId", "outputType", "settings", "policyVersions", "sourceVersionIds", "ruleSetVersionIds", "effectiveWeights", "reviewConditions", "confirmedAt"]) assert.ok(Object.hasOwn(confirmed.snapshot, field), field);
  assert.equal(confirmed.snapshot.settings.ruleSetBindings[0].locked, true);
  assert.equal(confirmed.snapshot.settings.expertReview.organizationRequired, true);
});

test("제출 전 변경은 확정을 무효화하고 제출 후 변경은 새 Request·Revision·Version Preview를 만든다", async () => {
  const model = await loadModel();
  let state = model.transitionStudioViewState(model.createStudioViewState(), { type: "confirm" });
  const firstSnapshot = state.snapshot;
  state = model.transitionStudioViewState(state, { type: "update-setting", field: "purpose", value: "변경 목적" });
  assert.equal(state.request.status, "configuring");
  assert.equal(state.snapshot, null);
  assert.equal(state.revisions.length, 0);
  state = model.transitionStudioViewState(state, { type: "confirm" });
  assert.notEqual(state.snapshot.id, firstSnapshot.id);
  state = model.transitionStudioViewState(state, { type: "submit" });
  assert.equal(state.request.status, "submitted");
  assert.equal(state.request.snapshotId, state.snapshot.id);
  assert.equal(state.request.runId, "run-studio-prototype-unavailable");
  const submittedSnapshot = state.snapshot;
  state = model.transitionStudioViewState(state, { type: "update-setting", field: "purpose", value: "제출 후 변경", reason: "대상 업무 변경" });
  assert.equal(state.request.status, "configuring");
  assert.notEqual(state.request.id, "generation-request-001");
  assert.equal(state.previousSubmittedRequest.snapshotId, submittedSnapshot.id);
  assert.equal(state.revisions.at(-1).type, "ai_regeneration");
  assert.equal(state.versions.at(-1).changeReason, "대상 업무 변경");
});

test("OutputVersion은 §13.4 공통 계약과 불변 previous Version 계보를 가진다", async () => {
  const model = await loadModel();
  let state = model.createStudioViewState();
  state = model.transitionStudioViewState(state, { type: "load-draft-fixture" });
  state = model.transitionStudioViewState(state, { type: "user-edit", reason: "결론 명료화" });
  assert.deepEqual(state.revisions.map((item) => item.type), ["generation", "user_edit"]);
  const version = state.versions.at(-1);
  assert.equal(Object.isFrozen(state.versions[0]), true);
  assert.equal(version.previousVersionId, state.versions[0].id);
  for (const field of model.OUTPUT_VERSION_FIELDS) assert.ok(Object.hasOwn(version, field), `missing ${field}`);
  assert.equal(version.changeReason, "결론 명료화");
  const partial = model.transitionStudioViewState(state, { type: "partial-regenerate", section: "결론", reason: "근거 보강" });
  assert.equal(partial.versions.at(-1).revisionType, "ai_regeneration");
  assert.deepEqual(partial.versions.at(-1).unchangedSections, ["요약", "본문", "인용", "경고", "미확인 사항"]);
});

test("검토·승인·반려·만료·회수·재요청과 승인 후 재승인 전이를 분리한다", async () => {
  const model = await loadModel();
  let state = model.transitionStudioViewState(model.createStudioViewState(), { type: "load-draft-fixture" });
  state = model.transitionStudioViewState(state, { type: "request-review" });
  state = model.transitionStudioViewState(state, { type: "start-review" });
  state = model.transitionStudioViewState(state, { type: "request-revision", reason: "근거 보강" });
  assert.equal(state.versions.at(-2).status, "revision_requested");
  assert.equal(state.versions.at(-1).status, "draft");
  state = model.transitionStudioViewState(state, { type: "request-approval", expiresInDays: 7 });
  const firstApprovalId = state.approvalRequests.at(-1).id;
  state = model.transitionStudioViewState(state, { type: "withdraw-approval" });
  assert.equal(state.approvalRequests.at(-1).status, "withdrawn");
  assert.equal(state.versions.at(-1).status, "review_requested");
  state = model.transitionStudioViewState(state, { type: "request-approval", expiresInDays: 7 });
  assert.notEqual(state.approvalRequests.at(-1).id, firstApprovalId);
  state = model.transitionStudioViewState(state, { type: "approve" });
  assert.equal(state.versions.at(-1).status, "approved");
  state = model.transitionStudioViewState(state, { type: "post-approval-change", reason: "RuleSet 변경" });
  assert.equal(state.versions.at(-1).status, "draft");
  assert.equal(state.versions.at(-1).requiresReapproval, true);
  assert.equal(state.delivery.status, "blocked");
  const expired = model.transitionStudioViewState(model.transitionStudioViewState(model.transitionStudioViewState(model.createStudioViewState(), { type: "load-draft-fixture" }), { type: "request-review" }), { type: "request-approval", expiresInDays: 7 });
  assert.equal(model.transitionStudioViewState(expired, { type: "expire-approval" }).approvalRequests.at(-1).status, "expired");
});

test("Export·Delivery·등록은 현재 AccessDecision과 승인 Version을 매 요청 재검증한다", async () => {
  const model = await loadModel();
  let state = model.transitionStudioViewState(model.createStudioViewState(), { type: "load-approved-fixture" });
  state = model.transitionStudioViewState(state, { type: "set-access", accessState: "access_blocked" });
  for (const action of ["preview-export", "deliver", "request-registration"]) {
    const denied = model.transitionStudioViewState(state, { type: action });
    assert.equal(denied.safety.code, "CURRENT_ACCESS_DENIED");
  }
  state = model.transitionStudioViewState(state, { type: "set-access", accessState: "partially_redacted" });
  state = model.transitionStudioViewState(state, { type: "preview-export" });
  assert.equal(state.exportPreview.runtime, "Prototype · unavailable");
  assert.equal(state.exportPreview.accessDecision.state, "partially_redacted");
  assert.ok(state.exportPreview.maskedReferences.length > 0);
  state = model.transitionStudioViewState(state, { type: "request-registration" });
  assert.equal(state.knowledgeRegistration.status, "requested");
  assert.equal(state.knowledgeRegistration.automatic, false);
  assert.equal(state.daonWrites, 0);
});

test("역할 Matrix와 모바일 Allowlist는 UI 숨김이 아닌 순수 안전 Code로 판정한다", async () => {
  const model = await loadModel();
  assert.equal(model.evaluateRoleAction("editor", "approve").code, "ROLE_ACTION_DENIED");
  assert.equal(model.evaluateRoleAction("approver", "approve").allowed, true);
  assert.equal(model.evaluateRoleAction("viewer", "download").code, "CURRENT_ACCESS_DENIED");
  for (const action of ["edit_title", "edit_text_block", "edit_simple_table_cell"]) {
    const decision = model.evaluateMobileAction(action);
    assert.deepEqual([decision.allowed, decision.createsContentRevision], [true, true]);
  }
  for (const action of ["review_comment", "request_revision", "approve", "reject", "handle_notification", "open_citation"]) {
    const decision = model.evaluateMobileAction(action);
    assert.equal(decision.allowed, true);
    assert.equal(decision.createsContentRevision, false);
  }
  for (const action of ["change_section", "change_layout", "change_table_structure", "change_evidence_link", "change_generation_settings", "regenerate_all"]) {
    const decision = model.evaluateMobileAction(action);
    assert.equal(decision.allowed, false);
    assert.equal(decision.code, "MOBILE_STUDIO_ACTION_NOT_ALLOWED");
    assert.match(decision.continueOn, /Web|Windows/);
  }
});

test("승인 반려는 대상 Version 계보를 보존하고 새 Draft Version을 만든다", async () => {
  const model = await loadModel();
  let state = model.transitionStudioViewState(model.createStudioViewState(), { type: "load-draft-fixture" });
  state = model.transitionStudioViewState(state, { type: "request-review" });
  state = model.transitionStudioViewState(state, { type: "request-approval", expiresInDays: 7 });
  const rejectedId = state.versions.at(-1).id;
  state = model.transitionStudioViewState(state, { type: "reject" });
  assert.equal(state.versions.at(-2).id, rejectedId);
  assert.equal(state.versions.at(-2).status, "revision_requested");
  assert.equal(state.versions.at(-1).previousVersionId, rejectedId);
  assert.equal(state.versions.at(-1).status, "draft");
});

test("각 Export·Delivery·등록 요청은 별도 현재 AccessDecision을 만든다", async () => {
  const model = await loadModel();
  let state = model.transitionStudioViewState(model.createStudioViewState(), { type: "load-approved-fixture" });
  const before = state.access.decisionVersion;
  state = model.transitionStudioViewState(state, { type: "preview-export" });
  const exportDecision = state.exportPreview.accessDecision.decisionVersion;
  state = model.transitionStudioViewState(state, { type: "deliver" });
  const deliveryDecision = state.delivery.accessDecision.decisionVersion;
  state = model.transitionStudioViewState(state, { type: "request-registration" });
  const registrationDecision = state.knowledgeRegistration.accessDecision.decisionVersion;
  assert.deepEqual([exportDecision, deliveryDecision, registrationDecision], [before + 1, before + 2, before + 3]);
});

test("중요 충돌은 승인 Version이어도 Export·Delivery·등록을 전부 차단한다", async () => {
  const model = await loadModel();
  let state = model.transitionStudioViewState(model.createStudioViewState(), { type: "load-approved-fixture" });
  state = model.transitionStudioViewState(state, { type: "set-important-conflict" });
  for (const type of ["preview-export", "deliver", "request-registration"]) {
    const blocked = model.transitionStudioViewState(state, { type });
    assert.equal(blocked.safety.code, "IMPORTANT_KNOWLEDGE_CONFLICT");
  }
});

test("StudioWorkflowViewState는 Pane 전환·폭 Projection 뒤에도 Draft·Version·검토 상태를 보존한다", async () => {
  const workspace = await import(`${pathToFileURL(path.join(root, "packages/ui/src/workspace-model.js")).href}?t=${Date.now()}`);
  let state = workspace.createWorkspaceViewState();
  state = workspace.transitionWorkspace(state, { type: "studio-workflow", domainAction: { type: "load-draft-fixture" } }, "studio-draft");
  state = workspace.transitionWorkspace(state, { type: "studio-workflow", domainAction: { type: "request-review" } }, "studio-review");
  const studio = state.studio_workflow;
  for (const width of [1920, 1200, 800, 500]) {
    const projected = workspace.projectWorkspace(state, width);
    assert.equal(projected.state.studio_workflow, studio);
    assert.equal(projected.state.studio_workflow.versions.at(-1).status, "review_requested");
  }
  const switched = workspace.transitionWorkspace(state, { type: "activate-pane", pane: "knowledge" }, "switch-away");
  assert.deepEqual(switched.studio_workflow, studio);
});

test("Studio UI·Workspace 연결은 설정·수명주기·권한·모바일·정직성 계약을 표시한다", async () => {
  const source = `${await read("packages/ui/src/studio-workflow-model.js")}\n${await read("packages/ui/src/studio-workflow-pane.jsx")}\n${await read("packages/ui/src/adaptive-workspace.jsx")}\n${await read("packages/ui/src/workspace-model.js")}`;
  for (const token of ["evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft", "GenerationSettingsSnapshot", "ApprovalRequest", "CURRENT_ACCESS_DENIED", "KnowledgeRegistration", "MOBILE_STUDIO_ACTION_NOT_ALLOWED", "Prototype · unavailable", "실제 API·DB·LLM·파일 Export·전달·지식 Index 0건"]) assert.match(source, new RegExp(token));
  assert.match(source, /onStudioAction/);
  assert.match(source, /studio_workflow/);
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL|fetch\s*\(/i);
});

test("C01 종료된 ApprovalRequest는 불변이며 새 요청만 승인할 수 있다", async () => {
  const model = await loadModel();
  const pendingState = () => model.transitionStudioViewState(
    model.transitionStudioViewState(model.createStudioViewState(), { type: "load-draft-fixture" }),
    { type: "request-approval", expiresInDays: 7 }
  );

  for (const terminalAction of ["expire-approval", "withdraw-approval"]) {
    let state = model.transitionStudioViewState(pendingState(), { type: terminalAction });
    const closedRequest = structuredClone(state.approvalRequests.at(-1));
    const preservedVersions = state.versions;
    state = model.transitionStudioViewState(state, { type: "approve" });
    assert.equal(state.safety.code, "APPROVAL_REQUEST_NOT_PENDING");
    assert.deepEqual(state.approvalRequests.at(-1), closedRequest);
    assert.deepEqual(state.versions, preservedVersions);
  }

  let approved = model.transitionStudioViewState(pendingState(), { type: "approve" });
  const approvedRequest = structuredClone(approved.approvalRequests.at(-1));
  const approvedVersions = approved.versions;
  approved = model.transitionStudioViewState(approved, { type: "reject" });
  assert.equal(approved.safety.code, "APPROVAL_REQUEST_NOT_PENDING");
  assert.deepEqual(approved.approvalRequests.at(-1), approvedRequest);
  assert.deepEqual(approved.versions, approvedVersions);

  let renewed = model.transitionStudioViewState(pendingState(), { type: "expire-approval" });
  const expiredId = renewed.approvalRequests.at(-1).id;
  renewed = model.transitionStudioViewState(renewed, { type: "request-approval", expiresInDays: 7 });
  assert.notEqual(renewed.approvalRequests.at(-1).id, expiredId);
  assert.equal(renewed.approvalRequests.at(-1).status, "pending");
  renewed = model.transitionStudioViewState(renewed, { type: "approve" });
  assert.equal(renewed.approvalRequests.at(-1).status, "approved");
  assert.deepEqual(renewed.approvalRequests.at(-1).audit.map((entry) => entry.status), ["pending", "approved"]);
});

test("C01 KnowledgeRegistration은 requested에서 등록·거부되고 특정 Version을 보존한다", async () => {
  const model = await loadModel();
  let registered = model.transitionStudioViewState(model.createStudioViewState(), { type: "load-approved-fixture" });
  registered = model.transitionStudioViewState(registered, { type: "request-registration" });
  const registrationId = registered.knowledgeRegistration.id;
  const registeredVersionId = registered.knowledgeRegistration.outputVersionId;
  registered = model.transitionStudioViewState(registered, { type: "register-knowledge" });
  assert.equal(registered.knowledgeRegistration.status, "registered");
  assert.equal(registered.knowledgeRegistration.outputVersionId, registeredVersionId);
  assert.equal(registered.daonWrites, 0);
  registered = model.transitionStudioViewState(registered, { type: "user-edit", reason: "등록 뒤 원본 편집" });
  assert.notEqual(registered.versions.at(-1).id, registeredVersionId);
  assert.equal(registered.knowledgeRegistration.outputVersionId, registeredVersionId);

  let rejected = model.transitionStudioViewState(model.createStudioViewState(), { type: "load-approved-fixture" });
  rejected = model.transitionStudioViewState(rejected, { type: "request-registration" });
  rejected = model.transitionStudioViewState(rejected, { type: "reject-registration" });
  const rejectedRecord = structuredClone(rejected.knowledgeRegistration);
  rejected = model.transitionStudioViewState(rejected, { type: "register-knowledge" });
  assert.equal(rejected.safety.code, "KNOWLEDGE_REGISTRATION_NOT_REQUESTED");
  assert.deepEqual(rejected.knowledgeRegistration, rejectedRecord);
  rejected = model.transitionStudioViewState(rejected, { type: "request-registration" });
  assert.notEqual(rejected.knowledgeRegistration.id, registrationId);
  assert.equal(rejected.knowledgeRegistration.status, "requested");
  assert.equal(rejected.knowledgeRegistrations.length, 2);
});

test("C01 모바일 Domain과 UI Matrix는 필수 15개 작업을 같은 순서로 표시한다", async () => {
  const model = await loadModel();
  const expected = [
    "edit_title", "edit_text_block", "edit_simple_table_cell",
    "review_comment", "request_revision", "approve", "reject", "handle_notification", "open_citation",
    "change_section", "change_layout", "change_table_structure", "change_evidence_link", "change_generation_settings", "regenerate_all"
  ];
  assert.deepEqual(model.MOBILE_STUDIO_ACTIONS, expected);
  assert.equal(new Set(model.MOBILE_STUDIO_ACTIONS).size, 15);
  for (const action of expected) {
    const decision = model.evaluateMobileAction(action);
    for (const field of ["allowed", "stateDomain", "createsContentRevision", "code", "continueOn"]) assert.ok(Object.hasOwn(decision, field), `${action}.${field}`);
  }
  const pane = await read("packages/ui/src/studio-workflow-pane.jsx");
  assert.match(pane, /MOBILE_STUDIO_ACTIONS\.map/);
  assert.doesNotMatch(pane, /const mobileActions\s*=/);
  assert.match(pane, /decision\.code/);
  assert.match(pane, /decision\.continueOn/);
});

test("C01 Studio Cursor는 state와 Rendered select value로 네 폭에서 보존된다", async () => {
  const model = await loadModel();
  const workspace = await import(`${pathToFileURL(path.join(root, "packages/ui/src/workspace-model.js")).href}?t=${Date.now()}`);
  let state = workspace.createWorkspaceViewState();
  state = workspace.transitionWorkspace(state, { type: "studio-workflow", domainAction: { type: "set-cursor", cursor: "section-3:table-1" } }, "cursor-change");
  assert.equal(state.studio_workflow.cursor, "section-3:table-1");
  for (const width of [1920, 1200, 800, 500]) {
    const projected = workspace.projectWorkspace(state, width);
    assert.equal(projected.state.studio_workflow.cursor, "section-3:table-1");
  }
  const remounted = workspace.createWorkspaceViewState(state);
  assert.equal(remounted.studio_workflow.cursor, "section-3:table-1");
  const pane = await read("packages/ui/src/studio-workflow-pane.jsx");
  const adaptive = await read("packages/ui/src/adaptive-workspace.jsx");
  assert.match(pane, /select value=\{state\.cursor\}/);
  assert.match(pane, /type:\s*"set-cursor"/);
  assert.match(adaptive, /studio-workflow/);
  assert.equal(model.createStudioViewState({ cursor: "section-3:table-1" }).cursor, "section-3:table-1");
});
