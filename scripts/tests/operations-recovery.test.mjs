import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const root = path.resolve(".");
const modelPath = path.join(root, "packages/ui/src/operations-recovery-model.js");
const read = (relative) => readFile(path.join(root, relative), "utf8").catch(() => "");
const loadModel = async () => existsSync(modelPath) ? import(`${pathToFileURL(modelPath).href}?t=${Date.now()}`) : {};

test("운영 정본은 Service·Queue·Model·Node·Connector·비용·Backup·Update와 Prototype 경계를 가진다", async () => {
  const model = await loadModel();
  const state = model.createOperationsRecoveryViewState();
  assert.deepEqual(state.services.map(({ id }) => id), ["api", "worker", "database", "object_storage"]);
  assert.deepEqual(state.queues.map(({ id }) => id), ["source_processing", "index_build", "failed", "automatic_retry", "manual_retry"]);
  assert.deepEqual(state.modelDeployments.map(({ kind }) => kind), ["local", "internal", "external"]);
  assert.ok(state.runtimeNodes.length > 0);
  assert.deepEqual(state.connectors.map(({ kind }) => kind), ["daon", "internet"]);
  assert.equal(state.costs.some(({ code }) => code === "COST_LIMIT_EXCEEDED"), true);
  assert.ok(state.backup.restoreDrill);
  assert.ok(state.update.rollbackAvailable);
  assert.deepEqual(state.actualEffects, { apiWrites: 0, processingRuns: 0, backups: 0, restores: 0, updates: 0, rollbacks: 0 });
  assert.equal(state.adapterBoundary.mode, "prototype_fixture");
  assert.equal(state.adapterBoundary.actual, "deferred_actual");
});

test("Alert 안정 Key는 활성 Incident 반복 신호를 Count로 억제하고 복구 후 새 세대로 분리한다", async () => {
  const model = await loadModel();
  let state = model.createOperationsRecoveryViewState();
  const signal = { tenantId: state.tenantId, workspaceId: state.workspaceId, resource: "model-deployment-vision-001", safeCode: "MODEL_UNAVAILABLE", policyVersion: state.policyVersion };
  state = model.transitionOperationsRecovery(state, { type: "signal-alert", ...signal });
  state = model.transitionOperationsRecovery(state, { type: "signal-alert", ...signal });
  assert.equal(state.alerts.length, 1);
  assert.equal(state.alerts[0].count, 2);
  const incidentId = state.alerts[0].incidentId;
  for (const status of ["warning", "restricted", "recovering", "recovered"]) state = model.transitionOperationsRecovery(state, { type: "advance-incident", incidentId, status });
  state = model.transitionOperationsRecovery(state, { type: "signal-alert", ...signal });
  assert.equal(state.alerts.length, 2);
  assert.equal(state.alerts[1].generation, 2);
  assert.notEqual(state.alerts[1].incidentId, incidentId);
});

test("Incident는 detected→warning→restricted→recovering→recovered 순서를 건너뛰지 않는다", async () => {
  const model = await loadModel();
  let state = model.createOperationsRecoveryViewState();
  const incidentId = state.incidents[0].id;
  const skipped = model.transitionOperationsRecovery(state, { type: "advance-incident", incidentId, status: "recovering" });
  assert.equal(skipped.safety.code, "INCIDENT_TRANSITION_REQUIRED");
  assert.deepEqual(skipped.incidents, state.incidents);
  for (const status of ["warning", "restricted", "recovering", "recovered"]) state = model.transitionOperationsRecovery(state, { type: "advance-incident", incidentId, status });
  assert.equal(state.incidents[0].status, "recovered");
  assert.ok(state.incidents[0].resolvedAt);
});

test("auto와 허용 local_only만 healthy Readiness Event에서 새 ProcessingRun 한 건을 자동 Queue한다", async () => {
  const model = await loadModel();
  for (const mode of ["auto", "local_only"]) {
    let state = model.createOperationsRecoveryViewState({ waitingSource: { selectionMode: mode, localOnlyAutoRetryAllowed: true } });
    const prior = structuredClone(state.processingRuns[0]);
    state = model.transitionOperationsRecovery(state, { type: "readiness-event", eventId: `readiness-${mode}`, deploymentState: "ready", nodeState: "healthy", providerState: "healthy", requiredRole: "vision" });
    assert.equal(state.processingRuns.length, 2, mode);
    assert.deepEqual(state.processingRuns[0], prior, mode);
    assert.equal(state.processingRuns[1].retryOfProcessingRunId, prior.id);
    assert.equal(state.processingRuns[1].triggerType, "readiness_event");
    assert.equal(state.processingRuns[1].triggerEventId, `readiness-${mode}`);
    assert.equal(state.queues.find(({ id }) => id === "automatic_retry").count, 1);
    assert.equal(state.actualEffects.processingRuns, 0);
  }
});

test("pinned·직접 선택은 Readiness Event 자동 실행 0건이고 수동 재처리만 허용한다", async () => {
  const model = await loadModel();
  for (const mode of ["pinned", "direct"]) {
    let state = model.createOperationsRecoveryViewState({ waitingSource: { selectionMode: mode } });
    state = model.transitionOperationsRecovery(state, { type: "readiness-event", eventId: `readiness-${mode}`, deploymentState: "ready", nodeState: "healthy", providerState: "healthy", requiredRole: "vision" });
    assert.equal(state.processingRuns.length, 1);
    assert.equal(state.safety.code, "MANUAL_RETRY_REQUIRED");
    state = model.transitionOperationsRecovery(state, { type: "manual-retry", idempotencyKey: `manual-${mode}`, role: state.membership.role, capability: "processing.retry", tenantId: state.tenantId, workspaceId: state.workspaceId, sourceId: state.waitingSource.sourceId });
    assert.equal(state.processingRuns.length, 2);
    assert.equal(state.processingRuns[1].triggerType, "manual");
  }
});

test("수동 재처리는 현재 Membership·Capability·Tenant·Workspace·Source ACL을 다시 검사하고 Payload 권한 주입을 거부한다", async () => {
  const model = await loadModel();
  const fixtures = [
    { membership: { role: "viewer", active: true, capabilities: [] }, role: "operator", grants: ["processing.retry"] },
    { membership: null, role: "operator", grants: ["processing.retry"] },
    { tenantId: "tenant-other" },
    { workspaceId: "workspace-other" },
    { sourceId: "source-other" }
  ];
  for (const fixture of fixtures) {
    let state = model.createOperationsRecoveryViewState(fixture.membership === undefined ? {} : { membership: fixture.membership });
    const next = model.transitionOperationsRecovery(state, { type: "manual-retry", idempotencyKey: "manual-denied", role: fixture.role ?? state.membership?.role, grants: fixture.grants, capability: "processing.retry", tenantId: fixture.tenantId ?? state.tenantId, workspaceId: fixture.workspaceId ?? state.workspaceId, sourceId: fixture.sourceId ?? state.waitingSource.sourceId });
    assert.match(next.safety.code, /AUTHORIZATION_DENIED|CURRENT_ACCESS_DENIED/);
    assert.equal(next.processingRuns.length, state.processingRuns.length);
  }
});

test("Event·Idempotency·활성 Run·Backoff 중복은 안정 Code와 변경 0건으로 억제한다", async () => {
  const model = await loadModel();
  for (const expected of ["DUPLICATE_TRIGGER_EVENT", "DUPLICATE_IDEMPOTENCY_KEY", "ACTIVE_PROCESSING_RUN_EXISTS", "RETRY_BACKOFF_ACTIVE"]) {
    let state = model.createOperationsRecoveryViewState();
    const action = model.retrySuppressionFixture(expected, state);
    const before = structuredClone(state.processingRuns);
    state = model.transitionOperationsRecovery(state, action);
    assert.equal(state.safety.code, expected);
    assert.deepEqual(state.processingRuns, before);
    assert.equal(state.suppressedRetries.at(-1).code, expected);
  }
});

test("재처리 결과는 ready·policy_blocked·Runtime 재소진을 서로 다른 Source/Run 상태로 투영한다", async () => {
  const model = await loadModel();
  for (const [outcome, sourceStatus, runStatus, code] of [
    ["ready", "ready", "completed", "READY_GATE_PASSED"],
    ["policy_blocked", "needs_review", "policy_blocked", "NO_POLICY_CANDIDATE"],
    ["runtime_exhausted", "waiting_model", "failed", "NO_AVAILABLE_UNDERSTANDING_MODEL"]
  ]) {
    let state = model.createOperationsRecoveryViewState();
    state = model.transitionOperationsRecovery(state, { type: "manual-retry", idempotencyKey: `manual-${outcome}`, role: state.membership.role, capability: "processing.retry", tenantId: state.tenantId, workspaceId: state.workspaceId, sourceId: state.waitingSource.sourceId });
    state = model.transitionOperationsRecovery(state, { type: "complete-retry-preview", runId: state.processingRuns.at(-1).id, outcome });
    assert.equal(state.waitingSource.status, sourceStatus);
    assert.equal(state.processingRuns.at(-1).status, runStatus);
    assert.equal(state.processingRuns.at(-1).code, code);
  }
});

test("여섯 장애는 허용 범위만 축소하고 금지 우회·실제 외부 효과를 만들지 않는다", async () => {
  const model = await loadModel();
  const expected = {
    daon: "DAON_ONLY_DEGRADED",
    external_llm: "FROZEN_POLICY_CANDIDATES_ONLY",
    local_llm: "EXTERNAL_AUTO_SWITCH_FORBIDDEN",
    internet: "INTERNET_DEPENDENT_ONLY",
    index: "READY_SOURCES_ONLY",
    evidence_store: "APPROVAL_DELIVERY_BLOCKED"
  };
  for (const [failure, invariant] of Object.entries(expected)) {
    const initial = model.createOperationsRecoveryViewState();
    const next = model.transitionOperationsRecovery(initial, { type: "inject-failure", failure });
    assert.equal(next.degradation.failure, failure);
    assert.equal(next.degradation.invariant, invariant);
    assert.equal(next.degradation.unauthorizedFallbackCount, 0);
    assert.deepEqual(next.actualEffects, initial.actualEffects);
  }
});

test("비용 한도·Step-up·AccessDecision 신호와 Backup/Restore/Update/Rollback Preview가 운영 정직성을 지킨다", async () => {
  const model = await loadModel();
  let state = model.createOperationsRecoveryViewState();
  state = model.transitionOperationsRecovery(state, { type: "preview-recovery", action: "restore" });
  assert.equal(state.safety.code, "STEP_UP_REQUIRED");
  assert.equal(state.actualEffects.restores, 0);
  state = model.transitionOperationsRecovery(state, { type: "preview-recovery", action: "restore", stepUpAuthorizationId: "stepup-restore-001", approvalId: null });
  assert.equal(state.safety.code, "G9_DRILL_APPROVAL_REQUIRED");
  assert.equal(state.actualEffects.restores, 0);
  assert.equal(state.securitySignals.cost.code, "COST_LIMIT_EXCEEDED");
  assert.deepEqual(state.securitySignals.accessDecision.states, ["partially_redacted", "access_blocked"]);
  assert.deepEqual(state.securitySignals.stepUp.states, ["failed", "expired"]);
});

test("C01 immutable 최초 Run 완료 시도는 Source·Run·Queue·Audit를 변경하지 않는다", async () => {
  const model = await loadModel();
  const state = model.createOperationsRecoveryViewState();
  const before = {
    source: structuredClone(state.waitingSource),
    runs: structuredClone(state.processingRuns),
    queues: structuredClone(state.queues),
    audits: structuredClone(state.auditEvents)
  };
  const next = model.transitionOperationsRecovery(state, { type: "complete-retry-preview", runId: "processing-run-failed-001", outcome: "ready" });
  assert.equal(next.safety.code, "IMMUTABLE_PROCESSING_RUN");
  assert.deepEqual(next.waitingSource, before.source);
  assert.deepEqual(next.processingRuns, before.runs);
  assert.deepEqual(next.queues, before.queues);
  assert.deepEqual(next.auditEvents, before.audits);
});

test("C01 두 번 연속 Runtime 실패 재처리는 직전 실패 Run을 부모로 삼고 각 부모를 불변 보존한다", async () => {
  const model = await loadModel();
  let state = model.createOperationsRecoveryViewState();
  state = model.transitionOperationsRecovery(state, { type: "manual-retry", idempotencyKey: "c01-first", role: state.membership.role, capability: "processing.retry", tenantId: state.tenantId, workspaceId: state.workspaceId, sourceId: state.waitingSource.sourceId });
  const original = structuredClone(state.processingRuns[0]);
  const firstRetryId = state.processingRuns.at(-1).id;
  state = model.transitionOperationsRecovery(state, { type: "complete-retry-preview", runId: firstRetryId, outcome: "runtime_exhausted" });
  const firstFailed = structuredClone(state.processingRuns.at(-1));
  state = model.transitionOperationsRecovery(state, { type: "manual-retry", idempotencyKey: "c01-second", role: state.membership.role, capability: "processing.retry", tenantId: state.tenantId, workspaceId: state.workspaceId, sourceId: state.waitingSource.sourceId });
  assert.equal(state.processingRuns.at(-1).retryOfProcessingRunId, firstRetryId);
  assert.deepEqual(state.processingRuns[0], original);
  assert.deepEqual(state.processingRuns.find(({ id }) => id === firstRetryId), firstFailed);
});

test("C01 unknown Mode와 waiting_model 아닌 Source는 Event·Run·Queue를 바꾸지 않고 fail-close한다", async () => {
  const model = await loadModel();
  let state = model.createOperationsRecoveryViewState();
  const modeBefore = state.waitingSource.selectionMode;
  state = model.transitionOperationsRecovery(state, { type: "set-selection-mode", mode: "attacker_mode" });
  assert.equal(state.safety.code, "INVALID_SELECTION_MODE");
  assert.equal(state.waitingSource.selectionMode, modeBefore);
  const unknown = model.createOperationsRecoveryViewState({ waitingSource: { selectionMode: "attacker_mode" } });
  const unknownBefore = { events: structuredClone(unknown.readinessEvents), runs: structuredClone(unknown.processingRuns), queues: structuredClone(unknown.queues) };
  const unknownNext = model.transitionOperationsRecovery(unknown, { type: "readiness-event", eventId: "event-unknown-mode", deploymentState: "ready", nodeState: "healthy", providerState: "healthy", requiredRole: "vision" });
  assert.equal(unknownNext.safety.code, "INVALID_SELECTION_MODE");
  assert.deepEqual(unknownNext.readinessEvents, unknownBefore.events);
  assert.deepEqual(unknownNext.processingRuns, unknownBefore.runs);
  assert.deepEqual(unknownNext.queues, unknownBefore.queues);
  for (const status of ["ready", "needs_review"]) {
    const initial = model.createOperationsRecoveryViewState({ waitingSource: { status } });
    const next = model.transitionOperationsRecovery(initial, { type: "readiness-event", eventId: `event-${status}`, deploymentState: "ready", nodeState: "healthy", providerState: "healthy", requiredRole: "vision" });
    assert.equal(next.safety.code, "SOURCE_NOT_WAITING_MODEL");
    assert.deepEqual(next.readinessEvents, initial.readinessEvents);
    assert.deepEqual(next.processingRuns, initial.processingRuns);
    assert.deepEqual(next.queues, initial.queues);
  }
});

test("C01 동일 Readiness Event는 Event·Run·Queue에 중복을 추가하지 않는다", async () => {
  const model = await loadModel();
  let state = model.createOperationsRecoveryViewState();
  const action = { type: "readiness-event", eventId: "event-c01-duplicate", deploymentState: "ready", nodeState: "healthy", providerState: "healthy", requiredRole: "vision" };
  state = model.transitionOperationsRecovery(state, action);
  const before = { events: structuredClone(state.readinessEvents), processed: structuredClone(state.processedEventIds), runs: structuredClone(state.processingRuns), queues: structuredClone(state.queues) };
  state = model.transitionOperationsRecovery(state, action);
  assert.equal(state.safety.code, "DUPLICATE_TRIGGER_EVENT");
  assert.deepEqual(state.readinessEvents, before.events);
  assert.deepEqual(state.processedEventIds, before.processed);
  assert.deepEqual(state.processingRuns, before.runs);
  assert.deepEqual(state.queues, before.queues);
});

test("C01 unknown Outcome은 예외 없이 안전 Code로 Source·Run·Queue·성공 Audit 변경 0건이다", async () => {
  const model = await loadModel();
  let state = model.createOperationsRecoveryViewState();
  state = model.transitionOperationsRecovery(state, { type: "manual-retry", idempotencyKey: "c01-invalid-outcome", role: state.membership.role, capability: "processing.retry", tenantId: state.tenantId, workspaceId: state.workspaceId, sourceId: state.waitingSource.sourceId });
  const before = { source: structuredClone(state.waitingSource), runs: structuredClone(state.processingRuns), queues: structuredClone(state.queues), audits: structuredClone(state.auditEvents) };
  assert.doesNotThrow(() => { state = model.transitionOperationsRecovery(state, { type: "complete-retry-preview", runId: state.processingRuns.at(-1).id, outcome: "attacker_outcome" }); });
  assert.equal(state.safety.code, "INVALID_RETRY_OUTCOME");
  assert.deepEqual(state.waitingSource, before.source);
  assert.deepEqual(state.processingRuns, before.runs);
  assert.deepEqual(state.queues, before.queues);
  assert.deepEqual(state.auditEvents, before.audits);
});

test("C01 Recovery Preview는 현재 Membership·Capability·Tenant·Workspace를 재검증하고 Payload 권한 주입을 거부한다", async () => {
  const model = await loadModel();
  for (const existing of [
    { membership: { role: "viewer", active: true, capabilities: [] } },
    { membership: { role: "organization_admin", active: false, capabilities: ["recovery.restore.preview"] } },
    { membership: { role: "organization_admin", active: true, tenantId: "tenant-other", capabilities: ["recovery.restore.preview"] } },
    { membership: { role: "organization_admin", active: true, workspaceId: "workspace-other", capabilities: ["recovery.restore.preview"] } }
  ]) {
    const state = model.createOperationsRecoveryViewState(existing);
    const next = model.transitionOperationsRecovery(state, { type: "preview-recovery", action: "restore", role: "organization_admin", grants: ["recovery.restore.preview"], tenantId: state.tenantId, workspaceId: state.workspaceId, stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" });
    assert.equal(next.safety.code, "RECOVERY_AUTHORIZATION_DENIED");
    assert.equal(next.recoveryPreview, null);
    assert.equal(next.auditEvents.some(({ action }) => action === "recovery_preview"), false);
    assert.deepEqual(next.actualEffects, state.actualEffects);
  }
});

test("C01 위조·만료·사용됨·Scope 불일치 Step-up/G9는 Preview·성공 Audit·외부 효과 0건이다", async () => {
  const model = await loadModel();
  const cases = [
    { mutate: () => {}, action: { stepUpAuthorizationId: "forged-stepup", approvalId: "g9-drill-001" }, code: "STEP_UP_REQUIRED" },
    { mutate: (state) => { state.stepUpAuthorizations[0].expiresAt = "2026-07-22T19:00:00+09:00"; }, action: { stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" }, code: "STEP_UP_AUTHORIZATION_INVALID" },
    { mutate: (state) => { state.stepUpAuthorizations[0].usedAt = "2026-07-22T20:30:00+09:00"; }, action: { stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" }, code: "STEP_UP_AUTHORIZATION_INVALID" },
    { mutate: (state) => { state.stepUpAuthorizations[0].target = "workspace-other"; }, action: { stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" }, code: "STEP_UP_AUTHORIZATION_INVALID" },
    { mutate: () => {}, action: { stepUpAuthorizationId: "stepup-restore-001", approvalId: "forged-g9" }, code: "G9_DRILL_APPROVAL_REQUIRED" },
    { mutate: (state) => { state.g9Approvals[0].workspaceId = "workspace-other"; }, action: { stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" }, code: "G9_DRILL_APPROVAL_REQUIRED" }
  ];
  for (const fixture of cases) {
    const state = model.createOperationsRecoveryViewState();
    fixture.mutate(state);
    const next = model.transitionOperationsRecovery(state, { type: "preview-recovery", action: "restore", tenantId: state.tenantId, workspaceId: state.workspaceId, ...fixture.action });
    assert.equal(next.safety.code, fixture.code);
    assert.equal(next.recoveryPreview, null);
    assert.equal(next.auditEvents.some(({ action }) => action === "recovery_preview"), false);
    assert.deepEqual(next.actualEffects, state.actualEffects);
  }
});

test("C01 유효 Membership·Step-up·정확 G9는 Preview only와 Step-up 1회 사용을 보장한다", async () => {
  const model = await loadModel();
  let state = model.createOperationsRecoveryViewState();
  state = model.transitionOperationsRecovery(state, { type: "preview-recovery", action: "restore", tenantId: state.tenantId, workspaceId: state.workspaceId, stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" });
  assert.equal(state.safety.code, "RECOVERY_PREVIEW_ONLY");
  assert.equal(state.recoveryPreview.mode, "prototype_fixture");
  assert.equal(state.recoveryPreview.actual, "deferred_actual");
  assert.equal(state.actualEffects.restores, 0);
  assert.ok(state.stepUpAuthorizations[0].usedAt);
  assert.equal(state.auditEvents.at(-1).stepUpAuthorizationId, "stepup-restore-001");
  assert.equal(state.auditEvents.at(-1).approvalId, "g9-drill-001");
  const before = { preview: structuredClone(state.recoveryPreview), audits: structuredClone(state.auditEvents), effects: structuredClone(state.actualEffects) };
  state = model.transitionOperationsRecovery(state, { type: "preview-recovery", action: "restore", tenantId: state.tenantId, workspaceId: state.workspaceId, stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" });
  assert.equal(state.safety.code, "STEP_UP_AUTHORIZATION_INVALID");
  assert.deepEqual(state.recoveryPreview, before.preview);
  assert.deepEqual(state.auditEvents, before.audits);
  assert.deepEqual(state.actualEffects, before.effects);
});

test("C02 viewer는 Recovery Capability와 유효 Step-up·G9를 보유해도 Preview를 만들지 못한다", async () => {
  const model = await loadModel();
  const state = model.createOperationsRecoveryViewState({ membership: { role: "viewer", active: true, capabilities: ["recovery.restore.preview"] } });
  const before = {
    preview: state.recoveryPreview,
    audits: structuredClone(state.auditEvents),
    stepUp: structuredClone(state.stepUpAuthorizations),
    effects: structuredClone(state.actualEffects)
  };
  const next = model.transitionOperationsRecovery(state, { type: "preview-recovery", action: "restore", stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" });
  assert.equal(next.safety.code, "RECOVERY_AUTHORIZATION_DENIED");
  assert.equal(next.recoveryPreview, before.preview);
  assert.deepEqual(next.auditEvents, before.audits);
  assert.deepEqual(next.stepUpAuthorizations, before.stepUp);
  assert.deepEqual(next.actualEffects, before.effects);
});

test("C03 operator Persona 문자열은 MembershipRole·Recovery 권한으로 승격되지 않는다", async () => {
  const model = await loadModel();
  const state = model.createOperationsRecoveryViewState({ membership: { role: "operator", active: true, capabilities: ["recovery.restore.preview"] } });
  const before = {
    preview: state.recoveryPreview,
    audits: structuredClone(state.auditEvents),
    stepUp: structuredClone(state.stepUpAuthorizations),
    effects: structuredClone(state.actualEffects)
  };
  const next = model.transitionOperationsRecovery(state, { type: "preview-recovery", action: "restore", stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" });
  assert.equal(next.safety.code, "RECOVERY_AUTHORIZATION_DENIED");
  assert.equal(next.recoveryPreview, before.preview);
  assert.deepEqual(next.auditEvents, before.audits);
  assert.deepEqual(next.stepUpAuthorizations, before.stepUp);
  assert.deepEqual(next.actualEffects, before.effects);
});

test("C02 실패 부모 Run 0건인 수동 재시도는 예외 없이 상태 변경 0건으로 fail-close한다", async () => {
  const model = await loadModel();
  const state = model.createOperationsRecoveryViewState({ processingRuns: [] });
  const before = { runs: structuredClone(state.processingRuns), queues: structuredClone(state.queues), keys: structuredClone(state.idempotencyKeys) };
  let next;
  assert.doesNotThrow(() => { next = model.transitionOperationsRecovery(state, { type: "manual-retry", idempotencyKey: "c02-no-parent", role: state.membership.role, tenantId: state.tenantId, workspaceId: state.workspaceId, sourceId: state.waitingSource.sourceId }); });
  assert.equal(next.safety.code, "RETRY_PARENT_NOT_FOUND");
  assert.deepEqual(next.processingRuns, before.runs);
  assert.deepEqual(next.queues, before.queues);
  assert.deepEqual(next.idempotencyKeys, before.keys);
});

test("C02 완료 Run만 있는 수동 재시도는 RETRY_PARENT_NOT_FOUND로 fail-close한다", async () => {
  const model = await loadModel();
  const completed = { id: "processing-run-completed-001", sourceVersionId: "source-waiting-model-001-v3", requiredRole: "vision", status: "completed", code: "READY_GATE_PASSED", immutable: false, actualRunCreated: false, retryOfProcessingRunId: "processing-run-failed-000" };
  const state = model.createOperationsRecoveryViewState({ processingRuns: [completed] });
  const before = { runs: structuredClone(state.processingRuns), queues: structuredClone(state.queues), keys: structuredClone(state.idempotencyKeys) };
  const next = model.transitionOperationsRecovery(state, { type: "manual-retry", idempotencyKey: "c02-completed-only", role: state.membership.role, tenantId: state.tenantId, workspaceId: state.workspaceId, sourceId: state.waitingSource.sourceId });
  assert.equal(next.safety.code, "RETRY_PARENT_NOT_FOUND");
  assert.deepEqual(next.processingRuns, before.runs);
  assert.deepEqual(next.queues, before.queues);
  assert.deepEqual(next.idempotencyKeys, before.keys);
});

test("C02 실패 부모 Run 0건인 적격 Readiness Event는 Event·Run·Queue·처리 표식을 바꾸지 않는다", async () => {
  const model = await loadModel();
  const state = model.createOperationsRecoveryViewState({ processingRuns: [] });
  const before = { events: structuredClone(state.readinessEvents), processed: structuredClone(state.processedEventIds), keys: structuredClone(state.idempotencyKeys), runs: structuredClone(state.processingRuns), queues: structuredClone(state.queues) };
  let next;
  assert.doesNotThrow(() => { next = model.transitionOperationsRecovery(state, { type: "readiness-event", eventId: "c02-readiness-no-parent", deploymentState: "ready", nodeState: "healthy", providerState: "healthy", requiredRole: "vision" }); });
  assert.equal(next.safety.code, "RETRY_PARENT_NOT_FOUND");
  assert.deepEqual(next.readinessEvents, before.events);
  assert.deepEqual(next.processedEventIds, before.processed);
  assert.deepEqual(next.idempotencyKeys, before.keys);
  assert.deepEqual(next.processingRuns, before.runs);
  assert.deepEqual(next.queues, before.queues);
});

test("C02 승인 역할 Recovery Preview와 정상 실패 부모 재시도는 기존 Green을 유지한다", async () => {
  const model = await loadModel();
  let recovery = model.createOperationsRecoveryViewState();
  recovery = model.transitionOperationsRecovery(recovery, { type: "preview-recovery", action: "restore", stepUpAuthorizationId: "stepup-restore-001", approvalId: "g9-drill-001" });
  assert.equal(recovery.safety.code, "RECOVERY_PREVIEW_ONLY");
  let retry = model.createOperationsRecoveryViewState();
  retry = model.transitionOperationsRecovery(retry, { type: "manual-retry", idempotencyKey: "c02-normal-parent", role: retry.membership.role, tenantId: retry.tenantId, workspaceId: retry.workspaceId, sourceId: retry.waitingSource.sourceId });
  assert.equal(retry.safety.code, "RETRY_PREVIEW_QUEUED");
  assert.equal(retry.processingRuns.at(-1).retryOfProcessingRunId, "processing-run-failed-001");
});

test("Operations↔Notifications·네 폭 전환은 선택·Filter·읽음·Recovery 상태를 보존하고 Native 제한은 client_type으로 판정한다", async () => {
  const model = await loadModel();
  let state = model.createOperationsRecoveryViewState();
  state = model.transitionOperationsRecovery(state, { type: "select-incident", incidentId: state.incidents[0].id });
  state = model.transitionOperationsRecovery(state, { type: "set-queue-filter", queueFilter: "automatic_retry" });
  state = model.transitionOperationsRecovery(state, { type: "mark-notification-read", notificationId: state.notifications[0].id });
  state = model.transitionOperationsRecovery(state, { type: "navigate", screen: "notifications" });
  for (const width of [1920, 1200, 800, 500]) {
    const projection = model.projectOperationsRecovery(state, width);
    assert.equal(projection.state.selectedIncidentId, state.incidents[0].id);
    assert.equal(projection.state.queueFilter, "automatic_retry");
    assert.equal(projection.state.notifications[0].read, true);
    assert.equal(projection.availability, "available");
  }
  for (const clientType of ["android", "ios"]) {
    const projection = model.projectOperationsRecovery(model.createOperationsRecoveryViewState({ clientType }), 1920);
    assert.equal(projection.availability, "unavailable");
  }
});

test("Operations·Notifications Route와 Pane은 정본·접근성·same-origin·민감정보 금지 계약을 사용한다", async () => {
  const files = [
    "apps/web/app/operations/page.jsx",
    "apps/web/app/notifications/page.jsx",
    "packages/ui/src/operations-recovery-model.js",
    "packages/ui/src/operations-recovery-pane.jsx",
    "packages/ui/src/index.js"
  ];
  for (const relative of files) assert.ok(existsSync(path.join(root, relative)), `missing ${relative}`);
  const source = (await Promise.all(files.map(read))).join("\n");
  for (const token of ["operations", "notifications", "OperationsRecoveryViewState", "waiting_model", "Readiness Event", "retry_of_processing_run_id", "prototype_fixture", "deferred_actual", "실제 API 미실행", "actualEffects"]) assert.match(source, new RegExp(token));
  for (const token of ["Alert Count", "Incident 상태", "Deep Link"]) assert.match(source, new RegExp(token));
  for (const token of ["aria-live", "aria-describedby", "aria-controls", "role=\"tooltip\"", "Escape"]) assert.match(source, new RegExp(token));
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL|fetch\s*\(/i);
  assert.doesNotMatch(source, /password|cookie|api[_-]?key|credential|stack trace|database host/i);
});

test("Windows Operations Pane은 Local Scan→Job 조회→명시 Repair를 Cloud와 분리한다", async () => {
  const pane = await read("packages/ui/src/operations-recovery-pane.jsx");
  for (const token of ["LocalRecoveryPanel", "startRecoveryScan", "getRecoveryJob", "repairRecoveryJob", "LOCAL_SERVICE_UNAVAILABLE", "manual_recovery_required", "repairable"]) {
    assert.match(pane, new RegExp(token));
  }
  assert.match(pane, /disabled=\{[^}]*state !== "repairable"/);
  assert.doesNotMatch(pane, /fetch\s*\(|XMLHttpRequest|WebSocket|https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_/i);
});

test("Recovery Pane은 Cloud 7종의 조회·생성·Preview·상태 조회·실행·취소를 명시 동작으로 제공한다", async () => {
  const pane = await read("packages/ui/src/operations-recovery-pane.jsx");
  for (const token of ["listBackups", "createBackup", "getBackup", "previewRestore", "getRestore", "executeRestore", "cancelRestore", "Restore 상태 새로고침", "Restore 취소 요청"]) {
    assert.match(pane, new RegExp(token));
  }
});

test("운영 Web Wrapper는 Stage A Safe surface이고 실제 Recovery 자산은 Stage C용으로 보존한다", async () => {
  const [wrapper, pane] = await Promise.all([
    read("apps/web/app/operations/recovery-workspace.jsx"),
    read("packages/ui/src/operations-recovery-pane.jsx")
  ]);
  assert.match(wrapper, /RESOURCE_UNAVAILABLE/);
  assert.match(wrapper, /후속 Stage C/);
  assert.doesNotMatch(wrapper, /recoveryApi|resolveRecoverySession|OperationsRecoveryWorkspace|useEffect|fetch\s*\(/);
  assert.match(pane, /sessionContext/);
  for (const token of ["actorId: sessionContext.userId", "tenantId: sessionContext.tenantId", "workspaceId: sessionContext.workspaceId", "membership: sessionContext.membership ?? null"]) {
    assert.ok(pane.includes(token));
  }
});

test("Windows Native Session 부재 상태는 fixture 관리자 권한으로 초기화하지 않는다", async () => {
  const pane = await read("packages/ui/src/operations-recovery-pane.jsx");
  assert.match(pane, /clientType === "windows" && !sessionContext/);
  assert.match(pane, /membership: null/);
  assert.match(pane, /native-session-unavailable/);
});

test("Windows Cloud Recovery 권한 Guard는 버튼 Handler 진입에서도 미허용 invoke를 0건으로 차단한다", async () => {
  const model = await loadModel();
  let invokeCount = 0;
  const invoke = async () => { invokeCount += 1; return "invoked"; };
  const fullOperations = ["cloud_backup_create", "cloud_backup_get", "cloud_backup_list", "cloud_restore_cancel", "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview"];
  for (const recoveryOperations of [[], null, ["cloud_backup_list"], ["cloud_backup_list", "unknown"]]) {
    for (const operation of fullOperations) {
      await assert.rejects(
        model.invokeAuthorizedCloudRecovery({ recoveryOperations, operation, invoke }),
        { code: "AUTHENTICATION_REQUIRED" }
      );
    }
  }
  assert.equal(invokeCount, 0);
  assert.equal(
    await model.invokeAuthorizedCloudRecovery({ recoveryOperations: fullOperations, operation: "cloud_restore_execute", invoke }),
    "invoked"
  );
  assert.equal(invokeCount, 1);
});

test("실제 Session Scope는 fixture 관리자 Membership 없이 Preview·retry를 fail-close한다", async () => {
  const model = await loadModel();
  const state = model.createOperationsRecoveryViewState({
    actorId: "user-real",
    tenantId: "tenant-real",
    workspaceId: "workspace-real",
    membership: null
  });
  const retry = model.transitionOperationsRecovery(state, {
    type: "manual-retry",
    idempotencyKey: "real-session-retry",
    tenantId: state.tenantId,
    workspaceId: state.workspaceId,
    sourceId: state.waitingSource.sourceId
  });
  const preview = model.transitionOperationsRecovery(state, {
    type: "preview-recovery",
    action: "restore",
    tenantId: state.tenantId,
    workspaceId: state.workspaceId,
    stepUpAuthorizationId: "stepup-restore-001",
    approvalId: "g9-drill-001"
  });
  assert.equal(state.membership, null);
  assert.match(retry.safety.code, /CURRENT_ACCESS_DENIED|AUTHORIZATION_DENIED/);
  assert.equal(preview.safety.code, "RECOVERY_AUTHORIZATION_DENIED");
  assert.equal(preview.recoveryPreview, null);
});
