export const RUN_STAGES = Object.freeze(["accepted", "planning", "retrieving", "generating", "validating", "completed"]);
export const BRANCH_STATES = Object.freeze(["waiting_user", "waiting_approval", "policy_blocked", "failed", "cancelled"]);
export const EVIDENCE_STATES = Object.freeze(["sufficient", "partial", "insufficient"]);
export const DECISION_LEDGER_FIELDS = Object.freeze([
  "mode", "routingPolicyVersion", "candidates", "policyExclusions", "runtimeExclusions", "selectionReason",
  "providerProfiles", "deployments", "modelArtifacts", "artifactDigests", "roleFinalModels",
  "understandingModels", "auxiliaryTools", "auxiliaryToolReasons", "crossValidation",
  "costLimit", "currency", "accumulatedCost", "estimatedCost", "costBlockedAt",
  "allowedCandidateIds", "sortingOrder", "fallbackPlan", "modelAttempts", "dataRegion", "egressDecision",
  "knowledgeSnapshot", "ruleSetSnapshot", "nodeId", "actorId", "tenantId", "workspaceId", "traceId",
  "requestId", "runId", "tokenUsage", "byteUsage", "latencyMs", "costUsage", "promptContractVersion", "toolContractVersion"
]);

const TRANSIENT_FAILURES = new Set(["TIMEOUT", "RATE_LIMIT", "TEMPORARY_FAILURE", "CAPACITY_UNAVAILABLE"]);
const POLICY_CODES = Object.freeze(["OWNERSHIP_SCOPE_DENIED", "PROVIDER_MODE_DENIED", "ROLE_MODALITY_CONTEXT_MISMATCH", "ARTIFACT_LICENSE_DENIED", "RESIDENCY_EGRESS_DENIED"]);
const RUNTIME_CODES = Object.freeze(["DEPLOYMENT_NODE_NOT_READY", "ARTIFACT_DIGEST_INSTALLATION_UNAVAILABLE", "CREDENTIAL_PROVIDER_AUTH_NOT_READY", "HEALTH_CAPACITY_CIRCUIT_UNAVAILABLE"]);
const PINNED_RUNTIME_FAILURES = new Set(["OFFLINE", "HEALTH_UNAVAILABLE", "CAPACITY_UNAVAILABLE", "TIMEOUT", "TEMPORARY_FAILURE"]);

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.freeze(value);
  for (const child of Object.values(value)) deepFreeze(child);
  return value;
}

function deployment(id, displayName, providerType, locality, options = {}) {
  return Object.freeze({
    id, displayName, providerType, locality, readiness: "ready", policyAllowed: true, role: "text",
    artifactId: `artifact-${id}`, artifactDigest: `sha256:${id}-digest`, providerProfileId: `profile-${id}`,
    privacyTier: 1, minimumQuality: 0.9, localityPreference: locality === "device" ? 3 : locality === "organization" ? 2 : 1,
    reliability: 0.98, latency: 60, cost: 0.02, currentLoad: 0.3,
    ownershipAllowed: true, providerModeAllowed: true, roleCompatible: true, artifactLicenseAllowed: true, egressAllowed: true,
    deploymentReady: true, artifactInstalled: true, credentialReady: true, healthCapacityReady: true,
    ...options,
  });
}

export function createRunPrototypeSeed() {
  return {
    settings: { mode: "auto", localScope: "device_only", pinnedDeploymentId: null, costLimit: 0.18, currency: "USD", scope: "run" },
    context: { actorId: "actor-prototype-001", tenantId: "tenant-release-one", workspaceId: "workspace-release-one", role: "text", classification: "organization_internal", dataRegion: "cloud_sync", egressPolicy: "approved_external_only", policyVersion: "workspace-policy-2026.07", deadline: "2026-07-21T23:59:00+09:00" },
    deployments: [
      deployment("dep-device-text", "이 장치 Text 모델", "local_runtime", "device", { providerProfileId: "profile-device-approved-001", privacyTier: 1, minimumQuality: 0.92, reliability: 0.99, latency: 35, cost: 0.01, currentLoad: 0.15 }),
      deployment("dep-external-text", "승인 External Text 모델", "external_api", "external", { providerProfileId: "profile-external-approved-001", privacyTier: 2, minimumQuality: 0.97, reliability: 0.995, latency: 75, cost: 0.03, currentLoad: 0.1 }),
      deployment("dep-org-text", "사내 Text 모델", "server_internal", "organization", { providerProfileId: "profile-org-approved-001", privacyTier: 3, minimumQuality: 0.94, reliability: 0.98, latency: 50, cost: 0.02, currentLoad: 0.2 }),
      deployment("dep-hard-owner", "소유권 제외 모델", "server_internal", "organization", { ownershipAllowed: false }),
      deployment("dep-hard-provider", "Provider 제외 모델", "external_api", "external", { providerModeAllowed: false }),
      deployment("dep-hard-role", "역할 제외 모델", "server_internal", "organization", { roleCompatible: false }),
      deployment("dep-hard-artifact", "Artifact 제외 모델", "server_internal", "organization", { artifactLicenseAllowed: false }),
      deployment("dep-hard-egress", "전송 제외 모델", "external_api", "external", { egressAllowed: false }),
      deployment("dep-runtime-deployment", "Deployment 준비 제외 모델", "server_internal", "organization", { deploymentReady: false, readiness: "offline" }),
      deployment("dep-runtime-artifact", "Artifact 설치 제외 모델", "server_internal", "organization", { artifactInstalled: false }),
      deployment("dep-runtime-credential", "인증 준비 제외 모델", "external_api", "external", { credentialReady: false }),
      deployment("dep-runtime-health", "Health 제외 모델", "server_internal", "organization", { healthCapacityReady: false })
    ],
    sourceSnapshot: {
      sourceVersions: ["source-daon-guidance-v2", "source-user-report-v2"],
      authorityTiers: ["daon_approved", "user_context"],
      weightApplications: [{ sourceId: "source-daon-guidance", requested: 1.9, effective: 1.6, layer: "source", clamped: true }],
      conflictPolicyVersion: "ConflictPolicyVersion-2026.07",
      ruleSetSnapshotIds: ["ruleset-mandatory-security-v4"]
    }
  };
}

function policyExclusionCode(item, settings) {
  if (item.ownershipAllowed === false || item.policyAllowed === false) return POLICY_CODES[0];
  if (item.providerModeAllowed === false) return POLICY_CODES[1];
  if (settings.mode === "local_only" && settings.localScope === "device_only" && item.locality !== "device") return POLICY_CODES[1];
  if (settings.mode === "local_only" && settings.localScope === "private_org_allowed" && !["device", "organization"].includes(item.locality)) return POLICY_CODES[1];
  if (item.roleCompatible === false) return POLICY_CODES[2];
  if (item.artifactLicenseAllowed === false) return POLICY_CODES[3];
  if (item.egressAllowed === false) return POLICY_CODES[4];
  if (settings.mode === "pinned" && item.id !== settings.pinnedDeploymentId) return POLICY_CODES[0];
  return null;
}

function runtimeExclusionCode(item) {
  if (item.deploymentReady === false || item.readiness !== "ready") return RUNTIME_CODES[0];
  if (item.artifactInstalled === false) return RUNTIME_CODES[1];
  if (item.credentialReady === false) return RUNTIME_CODES[2];
  if (item.healthCapacityReady === false) return RUNTIME_CODES[3];
  return null;
}

export function compareDeployments(left, right) {
  const comparisons = [
    left.privacyTier - right.privacyTier,
    right.minimumQuality - left.minimumQuality,
    right.localityPreference - left.localityPreference,
    right.reliability - left.reliability,
    left.latency - right.latency,
    left.cost - right.cost,
    left.currentLoad - right.currentLoad,
  ];
  return comparisons.find((value) => value !== 0) ?? left.id.localeCompare(right.id);
}

export function allowedCandidates(deployments, settings) {
  return deployments.filter((item) => !policyExclusionCode(item, settings) && !runtimeExclusionCode(item)).sort(compareDeployments);
}

export function buildRoutingDecision(seed, settings = seed.settings) {
  const policyExcluded = seed.deployments.flatMap((item) => {
    const code = policyExclusionCode(item, settings);
    return code ? [{ deploymentId: item.id, kind: "policy", code }] : [];
  });
  const runtimeExcluded = seed.deployments.flatMap((item) => {
    if (policyExclusionCode(item, settings)) return [];
    const code = runtimeExclusionCode(item);
    return code ? [{ deploymentId: item.id, kind: "runtime", code }] : [];
  });
  const approvedCandidates = allowedCandidates(seed.deployments, settings);
  return { policyExcluded, runtimeExcluded, approvedCandidates, selectedDeploymentId: approvedCandidates[0]?.id ?? null, selectionReason: "Frozen Policy와 7개 우선 기준·stable deployment ID 정렬 결과" };
}

function snapshotFrom(seed, settings, id = "run-snapshot-001") {
  const decision = buildRoutingDecision(seed, settings);
  return deepFreeze({
    id,
    actorId: seed.context.actorId, tenantId: seed.context.tenantId, workspaceId: seed.context.workspaceId,
    role: seed.context.role, classification: seed.context.classification, dataRegion: seed.context.dataRegion,
    egressPolicy: seed.context.egressPolicy, workspacePolicyVersion: seed.context.policyVersion, deadline: seed.context.deadline,
    sourceVersions: [...seed.sourceSnapshot.sourceVersions], authorityTiers: [...seed.sourceSnapshot.authorityTiers],
    weightApplications: seed.sourceSnapshot.weightApplications.map((item) => ({ ...item })), conflictPolicyVersion: seed.sourceSnapshot.conflictPolicyVersion,
    ruleSetSnapshotIds: [...seed.sourceSnapshot.ruleSetSnapshotIds], cost: { limit: settings.costLimit, currency: settings.currency, scope: settings.scope },
    routing: { mode: settings.mode, localScope: settings.localScope, pinnedDeploymentId: settings.pinnedDeploymentId, policyVersion: "routing-policy-2026.07", allowedCandidateIds: decision.approvedCandidates.map((item) => item.id), sortingOrder: ["privacy_tier", "minimum_quality", "locality_preference", "reliability", "latency", "cost", "current_load", "stable_deployment_id"], fallbackPlan: decision.approvedCandidates.slice(1).map((item) => item.id) },
    promptContractVersion: "prompt-contract-v1", toolContractVersion: "tool-contract-v1"
  });
}

function safeError(code, retryable, userAction, failedStage = "routing") {
  return { code, message: code === "COST_LIMIT_EXCEEDED" ? "비용 한도 때문에 새 호출을 시작하지 않았습니다." : "안전한 실행 경계에서 요청이 중단되었습니다.", failedStage, impact: "현재 Run만 중단", retryable, userAction, traceId: "trace-run-001" };
}

function ledgerFor(snapshot, decision, attempts = []) {
  const selected = decision.approvedCandidates[0];
  const values = {
    mode: snapshot.routing.mode, routingPolicyVersion: snapshot.routing.policyVersion, candidates: decision.approvedCandidates.map((item) => item.id), policyExclusions: decision.policyExcluded, runtimeExclusions: decision.runtimeExcluded, selectionReason: decision.selectionReason,
    providerProfiles: selected ? [selected.providerProfileId] : [], deployments: selected ? [selected.id] : [], modelArtifacts: selected ? [selected.artifactId] : [], artifactDigests: selected ? [selected.artifactDigest] : [], roleFinalModels: { text: selected?.displayName ?? null },
    understandingModels: { text: selected?.displayName ?? null, deploymentId: selected?.id ?? null }, auxiliaryTools: [{ name: "Document Parse", version: "prototype-v1" }], auxiliaryToolReasons: ["원문 위치 교차 검증"], crossValidation: { mismatch: false, supplementation: "없음", review: "통과" },
    costLimit: snapshot.cost.limit, currency: snapshot.cost.currency, accumulatedCost: 0.04, estimatedCost: 0.03, costBlockedAt: null,
    allowedCandidateIds: snapshot.routing.allowedCandidateIds, sortingOrder: snapshot.routing.sortingOrder, fallbackPlan: snapshot.routing.fallbackPlan, modelAttempts: attempts,
    dataRegion: snapshot.dataRegion, egressDecision: "approved_within_snapshot", knowledgeSnapshot: snapshot.sourceVersions, ruleSetSnapshot: snapshot.ruleSetSnapshotIds,
    nodeId: "node-prototype-001", actorId: snapshot.actorId, tenantId: snapshot.tenantId, workspaceId: snapshot.workspaceId, traceId: "trace-run-001", requestId: "request-run-001", runId: "run-prototype-001",
    tokenUsage: { input: 820, output: 240 }, byteUsage: 48120, latencyMs: 1840, costUsage: 0.04, promptContractVersion: snapshot.promptContractVersion, toolContractVersion: snapshot.toolContractVersion
  };
  return Object.fromEntries(DECISION_LEDGER_FIELDS.map((field) => [field, values[field]]));
}

function baseRun(settings = createRunPrototypeSeed().settings, seed = createRunPrototypeSeed()) {
  const snapshot = snapshotFrom(seed, settings);
  const decision = buildRoutingDecision(seed, settings);
  const selected = decision.approvedCandidates[0];
  const attempts = selected ? [{ id: "attempt-001", deploymentId: selected.id, providerType: selected.providerType, trigger: "automatic", result: "selected", streamed: false }] : [];
  return {
    id: "run-prototype-001", snapshot, status: "accepted", currentStageIndex: 0, startedAt: "2026-07-21T23:20:00+09:00", traceId: "trace-run-001", cancellable: true,
    attempts, decision, ledger: ledgerFor(snapshot, decision, attempts), evidenceState: "sufficient", conflicts: [], finalization: { blocked: false }, error: null, result: null, nextActions: [], autoRetrySameContext: false,
    citations: [{ id: "citation-run-001", sourceId: "source-daon-guidance", sourceVersionId: "source-daon-guidance-v2", evidenceId: "evidence-daon-v2-page-12", name: "승인 운영 지침.pdf", position: "12쪽 · 4문단", excerpt: "승인된 기준선과 검증 증거를 함께 보존합니다.", kind: "document-region", runSnapshotId: snapshot.id }]
  };
}

export function startPrototypeRun(seed = createRunPrototypeSeed()) {
  return baseRun({ ...seed.settings });
}

export function createFixtureRun(name) {
  if (name === "pinned_wait") return applyAttemptFailure(baseRun({ ...createRunPrototypeSeed().settings, mode: "pinned", pinnedDeploymentId: "dep-org-text" }), "CAPACITY_UNAVAILABLE");
  if (["pinned_offline", "pinned_health", "pinned_capacity"].includes(name)) {
    const code = name === "pinned_offline" ? "OFFLINE" : name === "pinned_health" ? "HEALTH_UNAVAILABLE" : "CAPACITY_UNAVAILABLE";
    return applyAttemptFailure(baseRun({ ...createRunPrototypeSeed().settings, mode: "pinned", pinnedDeploymentId: "dep-org-text" }), code);
  }
  if (name === "local_private") return baseRun({ ...createRunPrototypeSeed().settings, mode: "local_only", localScope: "device_only" });
  const run = baseRun();
  if (name === "fallback") {
    const failedAttempt = { ...run.attempts[0], result: "TIMEOUT" };
    return applyAttemptFailure({ ...run, attempts: [failedAttempt], ledger: ledgerFor(run.snapshot, run.decision, [failedAttempt]) }, "TIMEOUT");
  }
  if (name === "stream_started") return { ...run, attempts: [{ ...run.attempts[0], result: "partial_stream", streamed: true }] };
  if (name === "runtime_exhausted") return exhaustedRun(run, "NO_AVAILABLE_DEPLOYMENT");
  if (name === "understanding_runtime_exhausted") {
    const seed = createRunPrototypeSeed();
    seed.context = { ...seed.context, role: "vision" };
    return exhaustedRun(baseRun(seed.settings, seed), "NO_AVAILABLE_UNDERSTANDING_MODEL");
  }
  if (name === "cost_limit") return preflightCost({ ...run, attempts: [], accumulatedCost: 0.17, estimatedNextCost: 0.03 });
  if (name === "approval") return { ...run, status: "waiting_approval", approvalPurpose: "pre_run_policy", nextActions: ["request_policy_approval"] };
  if (name === "important_conflict") return { ...run, status: "waiting_user", evidenceState: "partial", conflicts: [{ id: "conflict-critical-001", severity: "critical", unresolved: true }], finalization: { blocked: true }, error: safeError("IMPORTANT_KNOWLEDGE_CONFLICT", false, "open_conflict_review", "validating") };
  if (name === "auth_error") return applyAttemptFailure(run, "PROVIDER_AUTHENTICATION_FAILED");
  if (name === "bad_request") return applyAttemptFailure(run, "INVALID_REQUEST");
  return run;
}

function exhaustedRun(run, code) {
  const attempts = run.attempts.map((attempt) => ({ ...attempt, result: "CAPACITY_UNAVAILABLE" }));
  return { ...run, status: "failed", attempts, nextActions: ["retry_new_run"], error: safeError(code, true, "retry_new_run"), ledger: { ...run.ledger, modelAttempts: attempts } };
}

export function transitionRun(run, action) {
  if (action.type === "apply-next-settings") return { ...run, nextRunSettings: { mode: action.mode, pinnedDeploymentId: action.pinnedDeploymentId } };
  if (action.type === "cancel" && run.status !== "completed") return { ...run, status: "cancelled", cancellable: false, error: safeError("RUN_CANCELLED", true, "create_new_run", RUN_STAGES[run.currentStageIndex]) };
  if (action.type !== "advance" || run.status === "completed" || BRANCH_STATES.includes(run.status)) return run;
  const nextIndex = Math.min(RUN_STAGES.length - 1, run.currentStageIndex + 1);
  return { ...run, currentStageIndex: nextIndex, status: RUN_STAGES[nextIndex], cancellable: nextIndex < RUN_STAGES.length - 1, result: nextIndex === RUN_STAGES.length - 1 ? { summary: "Prototype 결정론적 완료 결과", complete: true } : null };
}

export function applyAttemptFailure(run, code) {
  const current = run.attempts.at(-1);
  if (run.snapshot.routing.mode === "pinned" && PINNED_RUNTIME_FAILURES.has(code)) return { ...run, status: "waiting_user", nextActions: ["retry", "choose_allowed_model"], error: safeError("PINNED_DEPLOYMENT_UNAVAILABLE", true, "retry_or_choose_allowed_model") };
  if (!TRANSIENT_FAILURES.has(code) || current?.streamed) return { ...run, status: "failed", nextActions: [], error: safeError(current?.streamed ? "PARTIAL_STREAM_ABORTED" : code, false, current?.streamed ? "create_new_run" : "contact_workspace_admin") };
  const used = new Set(run.attempts.map((attempt) => attempt.deploymentId));
  const nextId = run.snapshot.routing.fallbackPlan.find((id) => !used.has(id));
  if (!nextId) return { ...run, status: "failed", error: safeError("NO_AVAILABLE_DEPLOYMENT", true, "create_new_run") };
  const seed = createRunPrototypeSeed();
  const nextDeployment = seed.deployments.find((item) => item.id === nextId);
  if (run.snapshot.routing.mode === "local_only" && nextDeployment?.providerType === "external_api") return { ...run, status: "failed", error: safeError("NO_AVAILABLE_DEPLOYMENT", true, "create_new_run") };
  const attempts = [...run.attempts, { id: `attempt-${String(run.attempts.length + 1).padStart(3, "0")}`, deploymentId: nextDeployment.id, providerType: nextDeployment.providerType, trigger: "automatic_fallback", result: "selected", streamed: false }];
  return {
    ...run,
    status: "generating",
    attempts,
    ledger: {
      ...run.ledger,
      providerProfiles: [nextDeployment.providerProfileId],
      deployments: [nextDeployment.id],
      modelArtifacts: [nextDeployment.artifactId],
      artifactDigests: [nextDeployment.artifactDigest],
      roleFinalModels: { ...run.ledger.roleFinalModels, text: nextDeployment.displayName },
      understandingModels: { text: nextDeployment.displayName, deploymentId: nextDeployment.id },
      selectionReason: `Fallback selected after ${code}`,
      modelAttempts: attempts,
    },
    decision: { ...run.decision, selectedDeploymentId: nextDeployment.id, selectionReason: `Fallback selected after ${code}` },
  };
}

export function preflightCost(run) {
  const limit = run.snapshot.cost.limit;
  if ((run.accumulatedCost ?? 0) + (run.estimatedNextCost ?? 0) <= limit) return run;
  return {
    ...run,
    status: "policy_blocked",
    attempts: [],
    result: null,
    autoRetrySameContext: false,
    nextActions: ["create_new_run_after_authorized_change"],
    error: safeError("COST_LIMIT_EXCEEDED", false, "request_authorized_limit_change_then_create_new_run", "pre_attempt_cost_check"),
    ledger: { ...run.ledger, accumulatedCost: run.accumulatedCost, estimatedCost: run.estimatedNextCost, costBlockedAt: "pre_attempt_cost_check", modelAttempts: [] },
  };
}

export function createRunViewState(existing = {}) {
  return { settings: { ...createRunPrototypeSeed().settings, ...(existing.settings ?? {}) }, selectedScenario: existing.selectedScenario ?? "normal", run: existing.run ? { ...existing.run } : createFixtureRun("normal") };
}

export function transitionRunViewState(state, action) {
  const next = createRunViewState(state);
  if (action.type === "set-mode") next.settings = { ...next.settings, mode: action.mode, localScope: action.localScope ?? next.settings.localScope, pinnedDeploymentId: action.pinnedDeploymentId ?? null };
  if (action.type === "select-scenario") {
    next.selectedScenario = action.scenario;
    next.run = createFixtureRun(action.scenario);
    if (action.scenario === "pinned_wait") next.settings = { ...next.settings, mode: "pinned", pinnedDeploymentId: "dep-org-text" };
  }
  if (action.type === "start") next.run = startPrototypeRun({ ...createRunPrototypeSeed(), settings: { ...next.settings } });
  if (action.type === "advance" || action.type === "cancel") next.run = transitionRun(next.run, action);
  return next;
}
