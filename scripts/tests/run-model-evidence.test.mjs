import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const root = path.resolve(".");
const modelPath = path.join(root, "packages/ui/src/run-model-evidence-model.js");
const modelUrl = pathToFileURL(modelPath).href;
const loadModel = () => import(`${modelUrl}?t=${Date.now()}`);
const read = (relative) => readFile(path.join(root, relative), "utf8");

test("네 Mode는 불투명 Deployment ID만 허용하고 후보 집합을 제한한다", async () => {
  const model = await loadModel();
  const seed = model.createRunPrototypeSeed();
  assert.deepEqual(model.allowedCandidates(seed.deployments, { mode: "local_only", localScope: "device_only" }).map((item) => item.id), ["dep-device-text"]);
  assert.deepEqual(model.allowedCandidates(seed.deployments, { mode: "local_only", localScope: "private_org_allowed" }).map((item) => item.id), ["dep-device-text", "dep-org-text"]);
  assert.equal(model.allowedCandidates(seed.deployments, { mode: "pinned", pinnedDeploymentId: "dep-org-text" }).length, 1);
  assert.ok(model.allowedCandidates(seed.deployments, { mode: "auto" }).length > 1);
  assert.doesNotMatch(JSON.stringify(seed), /https?:\/\/|localhost|127\.0\.0\.1|secret|api[_-]?key/i);
});

test("RunSnapshot은 Routing·Source·가중치·비용 계약을 깊은 불변 값으로 고정한다", async () => {
  const model = await loadModel();
  const draft = model.createRunPrototypeSeed();
  const run = model.startPrototypeRun(draft);
  assert.equal(Object.isFrozen(run.snapshot), true);
  assert.equal(Object.isFrozen(run.snapshot.routing.allowedCandidateIds), true);
  assert.deepEqual(run.snapshot.sourceVersions, ["source-daon-guidance-v2", "source-user-report-v2"]);
  assert.deepEqual(run.snapshot.weightApplications[0], { sourceId: "source-daon-guidance", requested: 1.9, effective: 1.6, layer: "source", clamped: true });
  assert.equal(run.snapshot.routing.policyVersion, "routing-policy-2026.07");
  assert.equal(run.snapshot.promptContractVersion, "prompt-contract-v1");
  draft.settings.mode = "pinned";
  assert.equal(run.snapshot.routing.mode, "auto");
  assert.equal(model.transitionRun(run, { type: "apply-next-settings", mode: "pinned", pinnedDeploymentId: "dep-org-text" }).snapshot.routing.mode, "auto");
});

test("Hard·Readiness 제외 코드와 stable ID tie-break 정렬을 구분한다", async () => {
  const model = await loadModel();
  const decision = model.buildRoutingDecision(model.createRunPrototypeSeed(), { mode: "auto" });
  assert.ok(decision.policyExcluded.every((item) => item.kind === "policy"));
  assert.ok(decision.runtimeExcluded.every((item) => item.kind === "runtime"));
  const ids = decision.approvedCandidates.map((item) => item.id);
  assert.deepEqual(ids, [...ids].sort((a, b) => a.localeCompare(b)), "동점 후보는 stable deployment ID로 정렬되어야 한다");
});

test("Fallback은 일시 장애만 우회하고 pinned·local-private·stream·인증 오류는 우회하지 않는다", async () => {
  const model = await loadModel();
  assert.equal(model.createFixtureRun("fallback").attempts.length, 2);
  assert.equal(model.applyAttemptFailure(model.createFixtureRun("fallback"), "AUTH_ERROR").status, "failed");
  const pinned = model.applyAttemptFailure(model.createFixtureRun("pinned_wait"), "CAPACITY_UNAVAILABLE");
  assert.equal(pinned.status, "waiting_user");
  assert.deepEqual(pinned.nextActions, ["retry", "choose_allowed_model"]);
  assert.equal(model.applyAttemptFailure(model.createFixtureRun("local_private"), "TIMEOUT").attempts.some((attempt) => attempt.providerType === "external_api"), false);
  assert.equal(model.applyAttemptFailure(model.createFixtureRun("stream_started"), "TIMEOUT").attempts.length, 1);
});

test("Fallback·waiting_user UI Fixture는 실패 적용 뒤의 사용자 상태를 직접 연다", async () => {
  const model = await loadModel();
  const fallback = model.createFixtureRun("fallback");
  assert.equal(fallback.status, "generating");
  assert.equal(fallback.attempts.length, 2);
  assert.equal(fallback.attempts[1].trigger, "automatic_fallback");
  assert.deepEqual(fallback.ledger.deployments, ["dep-external-text"]);
  assert.deepEqual(fallback.ledger.roleFinalModels, { text: "승인 External Text 모델" });
  const pinned = model.createFixtureRun("pinned_wait");
  assert.equal(pinned.status, "waiting_user");
  assert.deepEqual(pinned.nextActions, ["retry", "choose_allowed_model"]);
  const view = model.transitionRunViewState(model.createRunViewState(), { type: "select-scenario", scenario: "pinned_wait" });
  assert.equal(view.settings.mode, "pinned");
});

test("정상 6단계와 5개 분기·waiting_approval 경계를 결정론적으로 표현한다", async () => {
  const model = await loadModel();
  assert.deepEqual(model.RUN_STAGES, ["accepted", "planning", "retrieving", "generating", "validating", "completed"]);
  assert.deepEqual(model.BRANCH_STATES, ["waiting_user", "waiting_approval", "policy_blocked", "failed", "cancelled"]);
  let run = model.createFixtureRun("normal");
  for (let index = 1; index < model.RUN_STAGES.length; index += 1) run = model.transitionRun(run, { type: "advance" });
  assert.equal(run.status, "completed");
  assert.equal(model.transitionRun(run, { type: "advance" }).status, "completed");
  assert.equal(model.createFixtureRun("approval").approvalPurpose, "pre_run_policy");
  assert.notEqual(model.createFixtureRun("approval").approvalPurpose, "output_version");
});

test("비용 초과는 호출 전 차단하고 자동 재시도와 미완성 결과를 노출하지 않는다", async () => {
  const model = await loadModel();
  const blocked = model.preflightCost(model.createFixtureRun("cost_limit"));
  assert.equal(blocked.status, "policy_blocked");
  assert.equal(blocked.error.code, "COST_LIMIT_EXCEEDED");
  assert.equal(blocked.attempts.length, 0);
  assert.equal(blocked.result, null);
  assert.equal(blocked.autoRetrySameContext, false);
  assert.deepEqual(blocked.nextActions, ["create_new_run_after_authorized_change"]);
});

test("Citation은 기존 Evidence Viewer 계보를 보존하고 중요 충돌은 최종화를 차단한다", async () => {
  const model = await loadModel();
  const run = model.createFixtureRun("normal");
  const citation = run.citations[0];
  assert.deepEqual(citation, { id: "citation-run-001", sourceId: "source-daon-guidance", sourceVersionId: "source-daon-guidance-v2", evidenceId: "evidence-daon-v2-page-12", name: "승인 운영 지침.pdf", position: "12쪽 · 4문단", excerpt: "승인된 기준선과 검증 증거를 함께 보존합니다.", kind: "document-region", runSnapshotId: run.snapshot.id });
  assert.deepEqual(model.EVIDENCE_STATES, ["sufficient", "partial", "insufficient"]);
  assert.equal(model.createFixtureRun("important_conflict").finalization.blocked, true);
  assert.equal(model.createFixtureRun("important_conflict").error.code, "IMPORTANT_KNOWLEDGE_CONFLICT");
});

test("결정 원장은 TS-MDL-040 필드와 안전 오류 계약을 빠짐없이 가진다", async () => {
  const model = await loadModel();
  const ledger = model.createFixtureRun("fallback").ledger;
  for (const field of model.DECISION_LEDGER_FIELDS) assert.ok(Object.hasOwn(ledger, field), `missing ${field}`);
  const safe = model.createFixtureRun("auth_error").error;
  assert.deepEqual(Object.keys(safe).sort(), ["code", "failedStage", "impact", "message", "retryable", "traceId", "userAction"].sort());
  assert.doesNotMatch(JSON.stringify(safe), /stack|host|secret|provider raw/i);
});

test("Run UI는 Mode·Preview·단계·원장·근거·안전 분기와 접근성 Control을 연결한다", async () => {
  const source = `${await read("packages/ui/src/run-model-evidence-pane.jsx")}\n${await read("packages/ui/src/adaptive-workspace.jsx")}\n${await read("packages/ui/src/workspace.css")}`;
  for (const text of ["auto", "device_only", "private_org_allowed", "pinned", "Frozen RoutingContext", "실행 결정 원장", "COST_LIMIT_EXCEEDED", "IMPORTANT_KNOWLEDGE_CONFLICT", "근거 충분", "근거 부분", "근거 부족", "Prototype · unavailable"]) assert.match(source, new RegExp(text));
  assert.match(source, /재시도 · 새 Run/);
  assert.match(source, /허용 모델 변경/);
  assert.match(source, /role="tooltip"/);
  assert.match(source, /aria-describedby=/);
  assert.match(source, /onKeyDown=/);
  assert.doesNotMatch(source, /fetch\s*\(|https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
});

test("RunViewState는 Pane 언마운트와 네 폭 Projection 뒤에도 보존된다", async () => {
  const workspace = await import(`${pathToFileURL(path.join(root, "packages/ui/src/workspace-model.js")).href}?t=${Date.now()}`);
  const initial = workspace.createWorkspaceViewState();
  const cost = workspace.transitionWorkspace(initial, { type: "run-model", domainAction: { type: "select-scenario", scenario: "cost_limit" } }, "run-cost");
  const studio = workspace.transitionWorkspace(cost, { type: "activate-pane", pane: "studio" }, "hide-run-pane");
  for (const width of [1920, 1200, 800, 500]) assert.equal(workspace.projectWorkspace(studio, width).state.run_model.run.error.code, "COST_LIMIT_EXCEEDED");
  const returned = workspace.transitionWorkspace(studio, { type: "activate-pane", pane: "conversation" }, "show-run-pane");
  assert.equal(returned.run_id, "run-prototype-001");
  assert.equal(returned.run_status, "policy_blocked");
});

test("Run Citation은 Citation ID가 아니라 기존 Viewer의 Evidence ID를 연다", async () => {
  const workspace = await import(`${pathToFileURL(path.join(root, "packages/ui/src/workspace-model.js")).href}?t=${Date.now()}`);
  const initial = workspace.createWorkspaceViewState();
  const citation = initial.run_model.run.citations[0];
  const opened = workspace.transitionWorkspace(initial, { type: "open-evidence", evidence: citation }, "run-citation");
  assert.equal(opened.evidence_id, "evidence-daon-v2-page-12");
  assert.equal(opened.evidence_source_version_id, "source-daon-guidance-v2");
  assert.equal(opened.evidence_position, "12쪽 · 4문단");
});

test("C01 결정론 정렬은 7개 우선 기준 뒤에만 stable deployment ID를 사용한다", async () => {
  const model = await loadModel();
  const base = {
    displayName: "후보", providerType: "server_internal", locality: "organization", readiness: "ready", policyAllowed: true,
    role: "text", artifactId: "artifact-test", artifactDigest: "sha256:test", privacyTier: 2, minimumQuality: 0.8,
    localityPreference: 2, reliability: 0.95, latency: 120, cost: 0.05, currentLoad: 0.4,
  };
  const cases = [
    ["privacyTier", 1, 3], ["minimumQuality", 0.9, 0.7], ["localityPreference", 3, 1],
    ["reliability", 0.99, 0.8], ["latency", 80, 200], ["cost", 0.02, 0.09], ["currentLoad", 0.1, 0.8],
  ];
  for (const [field, better, worse] of cases) {
    const seed = model.createRunPrototypeSeed();
    seed.deployments = [{ ...base, id: "a-worse", [field]: worse }, { ...base, id: "z-better", [field]: better }];
    assert.equal(model.buildRoutingDecision(seed, { mode: "auto" }).selectedDeploymentId, "z-better", `${field} 우선순위`);
  }
  const tied = model.createRunPrototypeSeed();
  tied.deployments = [{ ...base, id: "dep-z" }, { ...base, id: "dep-a" }];
  assert.equal(model.buildRoutingDecision(tied, { mode: "auto" }).selectedDeploymentId, "dep-a");
});

test("C01 Hard Filter 5종과 Runtime Readiness 4종은 독립 실제 후보와 안전 Code를 가진다", async () => {
  const model = await loadModel();
  const seed = model.createRunPrototypeSeed();
  const base = { ...seed.deployments[0], policyAllowed: true, readiness: "ready", ownershipAllowed: true, providerModeAllowed: true, roleCompatible: true, artifactLicenseAllowed: true, egressAllowed: true, deploymentReady: true, artifactInstalled: true, credentialReady: true, healthCapacityReady: true };
  seed.deployments = [
    { ...base, id: "hard-owner", ownershipAllowed: false },
    { ...base, id: "hard-provider", providerModeAllowed: false },
    { ...base, id: "hard-role", roleCompatible: false },
    { ...base, id: "hard-artifact", artifactLicenseAllowed: false },
    { ...base, id: "hard-egress", egressAllowed: false },
    { ...base, id: "runtime-deployment", deploymentReady: false },
    { ...base, id: "runtime-artifact", artifactInstalled: false },
    { ...base, id: "runtime-credential", credentialReady: false },
    { ...base, id: "runtime-health", healthCapacityReady: false },
    { ...base, id: "allowed" },
  ];
  const decision = model.buildRoutingDecision(seed, { mode: "auto" });
  assert.deepEqual(decision.policyExcluded.map((item) => item.code), ["OWNERSHIP_SCOPE_DENIED", "PROVIDER_MODE_DENIED", "ROLE_MODALITY_CONTEXT_MISMATCH", "ARTIFACT_LICENSE_DENIED", "RESIDENCY_EGRESS_DENIED"]);
  assert.deepEqual(decision.runtimeExcluded.map((item) => item.code), ["DEPLOYMENT_NODE_NOT_READY", "ARTIFACT_DIGEST_INSTALLATION_UNAVAILABLE", "CREDENTIAL_PROVIDER_AUTH_NOT_READY", "HEALTH_CAPACITY_CIRCUIT_UNAVAILABLE"]);
  assert.deepEqual(decision.approvedCandidates.map((item) => item.id), ["allowed"]);
});

test("C01 Runtime 소진·pinned Runtime·인증·잘못된 요청 종료 계약을 고정한다", async () => {
  const model = await loadModel();
  const generic = model.createFixtureRun("runtime_exhausted");
  assert.deepEqual([generic.status, generic.error.code, generic.error.retryable, generic.attempts.length, generic.nextActions], ["failed", "NO_AVAILABLE_DEPLOYMENT", true, 1, ["retry_new_run"]]);
  const understanding = model.createFixtureRun("understanding_runtime_exhausted");
  assert.deepEqual([understanding.status, understanding.error.code, understanding.error.retryable, understanding.attempts.length], ["failed", "NO_AVAILABLE_UNDERSTANDING_MODEL", true, 1]);
  for (const scenario of ["pinned_offline", "pinned_health", "pinned_capacity"]) {
    const pinned = model.createFixtureRun(scenario);
    assert.deepEqual([pinned.status, pinned.attempts.length, pinned.nextActions], ["waiting_user", 1, ["retry", "choose_allowed_model"]]);
  }
  for (const scenario of ["auth_error", "bad_request"]) {
    const failed = model.createFixtureRun(scenario);
    assert.deepEqual([failed.status, failed.error.retryable, failed.attempts.length, failed.nextActions], ["failed", false, 1, []]);
  }
});

test("C01 비용 차단은 입력 값·시점·Attempt 0을 Domain 원장에 그대로 기록한다", async () => {
  const model = await loadModel();
  const blocked = model.createFixtureRun("cost_limit");
  assert.deepEqual({ status: blocked.status, code: blocked.error.code, attempts: blocked.attempts.length, result: blocked.result, retry: blocked.autoRetrySameContext }, { status: "policy_blocked", code: "COST_LIMIT_EXCEEDED", attempts: 0, result: null, retry: false });
  assert.deepEqual({ accumulated: blocked.ledger.accumulatedCost, estimated: blocked.ledger.estimatedCost, limit: blocked.ledger.costLimit, blockedAt: blocked.ledger.costBlockedAt }, { accumulated: 0.17, estimated: 0.03, limit: 0.18, blockedAt: "pre_attempt_cost_check" });
});

test("C01 Fallback 뒤 Decision·Provider·Artifact·역할·Understanding·Attempt가 최종 External 계보와 일치한다", async () => {
  const model = await loadModel();
  const fallback = model.createFixtureRun("fallback");
  assert.equal(fallback.attempts[0].result, "TIMEOUT");
  assert.equal(fallback.decision.selectedDeploymentId, "dep-external-text");
  assert.deepEqual(fallback.ledger.providerProfiles, ["profile-external-approved-001"]);
  assert.deepEqual(fallback.ledger.deployments, ["dep-external-text"]);
  assert.deepEqual(fallback.ledger.modelArtifacts, ["artifact-dep-external-text"]);
  assert.deepEqual(fallback.ledger.artifactDigests, ["sha256:dep-external-text-digest"]);
  assert.deepEqual(fallback.ledger.roleFinalModels, { text: "승인 External Text 모델" });
  assert.deepEqual(fallback.ledger.understandingModels, { text: "승인 External Text 모델", deploymentId: "dep-external-text" });
  assert.deepEqual(fallback.ledger.modelAttempts, fallback.attempts);
});

test("C01 Frozen Preview는 Snapshot 전체 계약을 표시하고 다음 Run 설정 변경에도 현재 pinned 값을 유지한다", async () => {
  const model = await loadModel();
  let view = model.transitionRunViewState(model.createRunViewState(), { type: "select-scenario", scenario: "pinned_wait" });
  const frozen = view.run.snapshot;
  view = model.transitionRunViewState(view, { type: "set-mode", mode: "auto" });
  assert.equal(view.settings.mode, "auto");
  assert.equal(view.run.snapshot, frozen);
  assert.equal(view.run.snapshot.routing.mode, "pinned");
  assert.equal(view.run.snapshot.routing.pinnedDeploymentId, "dep-org-text");
  const source = await read("packages/ui/src/run-model-evidence-pane.jsx");
  for (const label of ["Mode · Local Scope · Pinned", "Actor · Tenant · Workspace", "역할 · 분류 · 데이터 영역 · Egress", "Routing · Workspace 정책", "정책 기한", "허용 후보", "정렬 순서", "Fallback 계획", "SourceVersion · 권위", "가중치 계층 · 요청 · 유효 · Clamp", "RuleSet", "Prompt · Tool"]) assert.match(source, new RegExp(label));
});
