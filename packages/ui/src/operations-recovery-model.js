import { MEMBERSHIP_ROLES } from "./account-security-model.js";

const INCIDENT_STATES = Object.freeze(["detected", "warning", "restricted", "recovering", "recovered"]);
const ACTIVE_RUN_STATES = new Set(["queued", "accepted", "planning", "processing", "indexing"]);
const RETRY_ROLES = new Set(["personal_owner", "organization_admin", "workspace_admin"]);
const MEMBERSHIP_ROLE_SET = new Set(MEMBERSHIP_ROLES);
const RECOVERY_ROLES = new Set(["organization_admin"]);
const SELECTION_MODES = new Set(["auto", "local_only", "pinned", "direct"]);
const RETRY_OUTCOMES = new Set(["ready", "policy_blocked", "runtime_exhausted"]);
const RECOVERY_CAPABILITIES = Object.freeze({
  restore: "recovery.restore.preview",
  rollback: "recovery.rollback.preview",
  update: "recovery.update.preview"
});
const RECOVERY_NOW = "2026-07-22T21:00:00+09:00";

export const OPERATIONS_STATES = Object.freeze(["loading", "empty", "ready", "warning", "error", "forbidden", "unavailable"]);
export const OPERATIONS_RECOVERY_ADAPTERS = Object.freeze({
  status: "OperationsStatusAdapter · R1-M9-02",
  notification: "NotificationAdapter · R1-M4-07",
  processing: "SourceProcessingAdapter · R1-M6-14",
  backup: "BackupRestoreAdapter · R1-M5-07/R1-M9-07",
  update: "ReleaseUpdateAdapter · R1-M9-03~06"
});

function safe(code, message, retryable = false, userAction = "review_operations") {
  return { code, message, failedStage: "operations_recovery_prototype", impact: "Prototype 변경 0건", retryable, userAction, traceId: "trace-operations-prototype-001" };
}

function clone(value) {
  return structuredClone(value);
}

function audit(state, action, target, decision, code = "OK") {
  const sequence = state.auditEvents.length + 1;
  return {
    id: `audit-operations-${String(sequence).padStart(3, "0")}`,
    actorId: state.actorId,
    membershipRole: state.membership?.role ?? "none",
    action,
    target,
    policyVersion: state.policyVersion,
    decision,
    code,
    traceId: `trace-operations-${String(sequence).padStart(3, "0")}`,
    occurredAt: `2026-07-22T21:${String(sequence).padStart(2, "0")}:00+09:00`
  };
}

function notification(state, { kind, severity = "warning", status = "active", target, safeCode, incidentId = null, message }) {
  const sequence = state.notifications.length + 1;
  return {
    id: `notification-operations-${String(sequence).padStart(3, "0")}`,
    kind,
    severity,
    status,
    target,
    safeCode,
    traceId: `trace-notification-${String(sequence).padStart(3, "0")}`,
    incidentId,
    message,
    read: false,
    occurredAt: `2026-07-22T21:${String(sequence).padStart(2, "0")}:30+09:00`,
    deepLink: incidentId ? `/operations?incident=${incidentId}` : "/operations"
  };
}

function normalizeMembership(existing, base) {
  if (existing === null) return null;
  return {
    id: "membership-operations-admin-001",
    role: "organization_admin",
    active: true,
    tenantId: base.tenantId,
    workspaceId: base.workspaceId,
    capabilities: ["operations.view", "processing.retry", "recovery.preview", "recovery.restore.preview", "recovery.rollback.preview", "recovery.update.preview"],
    sourceAcl: ["source-waiting-model-001"],
    ...(existing ?? {})
  };
}

export function createOperationsRecoveryViewState(existing = {}) {
  const base = {
    screen: "operations",
    clientType: "web",
    viewStatus: "warning",
    actorId: "actor-operations-admin-001",
    navigationPersona: "organization_admin",
    tenantId: "tenant-organization-001",
    workspaceId: "workspace-release-one",
    policyVersion: "routing-policy-2026.07",
    selectedIncidentId: "incident-runtime-001",
    selectedServiceId: "worker",
    selectedSourceVersionId: "source-waiting-model-001-v3",
    selectedProcessingRunId: "processing-run-failed-001",
    selectedReadinessEventId: null,
    queueFilter: "all",
    recoveryStep: "detected",
    services: [
      { id: "api", label: "API", status: "healthy", safeCode: "SERVICE_HEALTHY" },
      { id: "worker", label: "Worker", status: "degraded", safeCode: "QUEUE_DELAYED" },
      { id: "database", label: "DB", status: "healthy", safeCode: "SERVICE_HEALTHY" },
      { id: "object_storage", label: "Object Storage", status: "healthy", safeCode: "SERVICE_HEALTHY" }
    ],
    queues: [
      { id: "source_processing", label: "Source Processing", count: 4, status: "processing" },
      { id: "index_build", label: "Index Build", count: 2, status: "processing" },
      { id: "failed", label: "실패 Queue", count: 1, status: "warning" },
      { id: "automatic_retry", label: "자동 재처리", count: 0, status: "ready" },
      { id: "manual_retry", label: "수동 재처리", count: 0, status: "ready" }
    ],
    modelDeployments: [
      { id: "model-deployment-local-001", kind: "local", role: "vision", health: "ready", capacity: "available", runtimeNodeId: "runtime-node-local-001" },
      { id: "model-deployment-internal-001", kind: "internal", role: "text", health: "ready", capacity: "available", runtimeNodeId: "runtime-node-organization-001" },
      { id: "model-deployment-external-001", kind: "external", role: "vision", health: "degraded", capacity: "limited", runtimeNodeId: null }
    ],
    runtimeNodes: [
      { id: "runtime-node-local-001", status: "healthy", capacity: "available" },
      { id: "runtime-node-organization-001", status: "healthy", capacity: "available" }
    ],
    connectors: [
      { id: "connector-daon-001", kind: "daon", status: "healthy", safeCode: "CONNECTOR_READY" },
      { id: "connector-internet-001", kind: "internet", status: "healthy", safeCode: "CONNECTOR_READY" }
    ],
    costs: [
      { scope: "user", storage: "4.2 GB / 10 GB", tokens: "48K / 100K", amount: "KRW 8,400 / 20,000", code: "WITHIN_LIMIT" },
      { scope: "organization", storage: "78 GB / 100 GB", tokens: "2M / 2M", amount: "KRW 300,000 / 300,000", code: "COST_LIMIT_EXCEEDED" }
    ],
    backup: { status: "verified", lastSuccessfulAt: "2026-07-21T03:00:00+09:00", lastVerifiedAt: "2026-07-21T04:00:00+09:00", rpo: "24h", rto: "4h", restoreDrill: "passed_fixture", requestAvailable: true },
    update: { channel: "pilot", currentVersion: "0.0.0-prototype", candidateVersion: "deferred", status: "preview_only", rollbackAvailable: true },
    waitingSource: {
      sourceId: "source-waiting-model-001",
      sourceVersionId: "source-waiting-model-001-v3",
      status: "waiting_model",
      requiredRole: "vision",
      selectionMode: "auto",
      localOnlyAutoRetryAllowed: true,
      dataRealm: "cloud_sync",
      aclVersion: "acl-source-v4",
      egressPolicy: "approved_external_only",
      costLimit: "KRW 1200",
      backoffUntil: "2026-07-22T20:00:00+09:00"
    },
    processingRuns: [{
      id: "processing-run-failed-001",
      sourceVersionId: "source-waiting-model-001-v3",
      requiredRole: "vision",
      status: "failed",
      code: "NO_AVAILABLE_UNDERSTANDING_MODEL",
      immutable: true,
      routingPolicyVersion: "routing-policy-2026.06"
    }],
    readinessEvents: [],
    processedEventIds: [],
    idempotencyKeys: [],
    suppressedRetries: [],
    incidents: [{ id: "incident-runtime-001", generation: 1, resource: "model-deployment-external-001", safeCode: "MODEL_UNAVAILABLE", status: "detected", createdAt: "2026-07-22T20:00:00+09:00", resolvedAt: null }],
    alerts: [],
    notifications: [{ id: "notification-operations-001", kind: "warning", severity: "warning", status: "active", target: "worker", safeCode: "QUEUE_DELAYED", traceId: "trace-notification-001", incidentId: "incident-runtime-001", message: "처리 Queue 지연을 확인하세요.", read: false, occurredAt: "2026-07-22T20:01:00+09:00", deepLink: "/operations?incident=incident-runtime-001" }],
    auditEvents: [],
    degradation: null,
    recoveryPreview: null,
    stepUpAuthorizations: [
      { id: "stepup-restore-001", actorId: "actor-operations-admin-001", action: "restore", target: "workspace-release-one", tenantId: "tenant-organization-001", workspaceId: "workspace-release-one", policyVersion: "routing-policy-2026.07", status: "issued", issuedAt: "2026-07-22T20:30:00+09:00", expiresAt: "2026-07-22T22:00:00+09:00", usedAt: null },
      { id: "stepup-rollback-001", actorId: "actor-operations-admin-001", action: "rollback", target: "workspace-release-one", tenantId: "tenant-organization-001", workspaceId: "workspace-release-one", policyVersion: "routing-policy-2026.07", status: "issued", issuedAt: "2026-07-22T20:30:00+09:00", expiresAt: "2026-07-22T22:00:00+09:00", usedAt: null },
      { id: "stepup-update-001", actorId: "actor-operations-admin-001", action: "update", target: "workspace-release-one", tenantId: "tenant-organization-001", workspaceId: "workspace-release-one", policyVersion: "routing-policy-2026.07", status: "issued", issuedAt: "2026-07-22T20:30:00+09:00", expiresAt: "2026-07-22T22:00:00+09:00", usedAt: null }
    ],
    g9Approvals: [
      { approvalId: "g9-drill-001", kind: "drill", target: "workspace-release-one", tenantId: "tenant-organization-001", workspaceId: "workspace-release-one", policyVersion: "routing-policy-2026.07", status: "approved", approvedBy: "actor-release-approver-001", approvedAt: "2026-07-22T20:00:00+09:00" },
      { approvalId: "g9-deploy-001", kind: "deploy", target: "workspace-release-one", tenantId: "tenant-organization-001", workspaceId: "workspace-release-one", policyVersion: "routing-policy-2026.07", status: "approved", approvedBy: "actor-release-approver-001", approvedAt: "2026-07-22T20:00:00+09:00" }
    ],
    securitySignals: {
      cost: { code: "COST_LIMIT_EXCEEDED", retryableInFrozenContext: false },
      stepUp: { states: ["failed", "expired"], actualWrites: 0 },
      accessDecision: { states: ["partially_redacted", "access_blocked"], originalOutputMutations: 0 }
    },
    safety: null,
    adapterBoundary: { mode: "prototype_fixture", actual: "deferred_actual", adapters: OPERATIONS_RECOVERY_ADAPTERS },
    actualEffects: { apiWrites: 0, processingRuns: 0, backups: 0, restores: 0, updates: 0, rollbacks: 0 }
  };
  const state = { ...base, ...existing };
  state.waitingSource = { ...base.waitingSource, ...(existing.waitingSource ?? {}) };
  state.membership = normalizeMembership(existing.membership, state);
  return state;
}

function alertKey({ tenantId, workspaceId, resource, safeCode, policyVersion }) {
  return [tenantId, workspaceId, resource, safeCode, policyVersion].join("|");
}

function authorizeRetry(state, action) {
  const membership = state.membership;
  if (action.tenantId !== state.tenantId || action.workspaceId !== state.workspaceId || action.sourceId !== state.waitingSource.sourceId) return { allowed: false, code: "CURRENT_ACCESS_DENIED" };
  if (!membership?.active || membership.tenantId !== state.tenantId || membership.workspaceId !== state.workspaceId) return { allowed: false, code: "CURRENT_ACCESS_DENIED" };
  if (action.role !== undefined && action.role !== membership.role) return { allowed: false, code: "AUTHORIZATION_DENIED" };
  if (!RETRY_ROLES.has(membership.role) || !membership.capabilities?.includes("processing.retry") || !membership.sourceAcl?.includes(action.sourceId)) return { allowed: false, code: "AUTHORIZATION_DENIED" };
  return { allowed: true, code: "AUTHORIZED" };
}

function suppress(state, code, action) {
  state.safety = safe(code, "재처리 중복 또는 정책 조건으로 새 Run을 만들지 않았습니다.", code === "RETRY_BACKOFF_ACTIVE", "review_retry_condition");
  state.suppressedRetries.push({ code, eventId: action.eventId ?? null, idempotencyKey: action.idempotencyKey ?? null, changedRuns: 0 });
  state.auditEvents.push(audit(state, "retry_suppressed", state.waitingSource.sourceVersionId, "denied", code));
  return state;
}

function findRetryParent(state) {
  return state.processingRuns.findLast(({ sourceVersionId, requiredRole, status }) => sourceVersionId === state.waitingSource.sourceVersionId && requiredRole === state.waitingSource.requiredRole && status === "failed");
}

function rejectMissingRetryParent(state) {
  state.safety = safe("RETRY_PARENT_NOT_FOUND", "같은 SourceVersion·필수 역할의 재시도 가능한 실패 Run이 없어 새 Run을 만들지 않았습니다.");
  return state;
}

function createRetryRun(state, { triggerType, triggerEventId = null, idempotencyKey, previous }) {
  const sequence = state.processingRuns.length + 1;
  const run = {
    id: `processing-run-retry-${String(sequence).padStart(3, "0")}`,
    sourceVersionId: state.waitingSource.sourceVersionId,
    requiredRole: state.waitingSource.requiredRole,
    status: "queued",
    code: "RETRY_QUEUED",
    retryOfProcessingRunId: previous.id,
    retry_of_processing_run_id: previous.id,
    triggerType,
    trigger_type: triggerType,
    triggerEventId,
    trigger_event_id: triggerEventId,
    idempotencyKey,
    snapshot: {
      actorId: state.actorId,
      tenantId: state.tenantId,
      workspaceId: state.workspaceId,
      aclVersion: state.waitingSource.aclVersion,
      dataRealm: state.waitingSource.dataRealm,
      routingPolicyVersion: state.policyVersion,
      costLimit: state.waitingSource.costLimit,
      egressPolicy: state.waitingSource.egressPolicy
    },
    actualRunCreated: false
  };
  state.processingRuns.push(run);
  state.selectedProcessingRunId = run.id;
  state.idempotencyKeys.push(idempotencyKey);
  if (triggerEventId) state.processedEventIds.push(triggerEventId);
  const queueId = triggerType === "manual" ? "manual_retry" : "automatic_retry";
  state.queues.find(({ id }) => id === queueId).count += 1;
  state.safety = safe("RETRY_PREVIEW_QUEUED", "새 ProcessingRun Domain Preview를 Queue했습니다.", false, "inspect_retry_lineage");
  state.auditEvents.push(audit(state, `${triggerType}_retry_queued`, run.id, "preview", "RETRY_PREVIEW_QUEUED"));
  state.notifications.push(notification(state, { kind: "retry", status: "queued", target: state.waitingSource.sourceId, safeCode: "RETRY_PREVIEW_QUEUED", message: `${triggerType} 새 Run Preview가 Queue되었습니다.` }));
  return state;
}

function commonSuppression(state, action, idempotencyKey) {
  if (action.forceSuppression) return action.forceSuppression;
  if (action.eventId && state.processedEventIds.includes(action.eventId)) return "DUPLICATE_TRIGGER_EVENT";
  if (state.idempotencyKeys.includes(idempotencyKey)) return "DUPLICATE_IDEMPOTENCY_KEY";
  if (state.processingRuns.some(({ status, sourceVersionId, requiredRole }) => ACTIVE_RUN_STATES.has(status) && sourceVersionId === state.waitingSource.sourceVersionId && requiredRole === state.waitingSource.requiredRole)) return "ACTIVE_PROCESSING_RUN_EXISTS";
  const now = new Date(action.now ?? "2026-07-22T21:00:00+09:00");
  if (new Date(state.waitingSource.backoffUntil) > now) return "RETRY_BACKOFF_ACTIVE";
  return null;
}

export function retrySuppressionFixture(code, state) {
  if (code === "DUPLICATE_TRIGGER_EVENT") state.processedEventIds.push("event-duplicate");
  if (code === "DUPLICATE_IDEMPOTENCY_KEY") state.idempotencyKeys.push("key-duplicate");
  return code === "DUPLICATE_IDEMPOTENCY_KEY"
    ? { type: "manual-retry", idempotencyKey: "key-duplicate", role: state.membership.role, capability: "processing.retry", tenantId: state.tenantId, workspaceId: state.workspaceId, sourceId: state.waitingSource.sourceId }
    : { type: "readiness-event", eventId: code === "DUPLICATE_TRIGGER_EVENT" ? "event-duplicate" : `event-${code.toLowerCase()}`, deploymentState: "ready", nodeState: "healthy", providerState: "healthy", requiredRole: state.waitingSource.requiredRole, forceSuppression: code };
}

function applyAlertSignal(state, action) {
  const key = alertKey(action);
  const activeAlert = state.alerts.find((item) => item.alertKey === key && state.incidents.find(({ id }) => id === item.incidentId)?.status !== "recovered");
  if (activeAlert) {
    activeAlert.count += 1;
    activeAlert.lastSeenAt = action.occurredAt ?? "2026-07-22T21:10:00+09:00";
    state.auditEvents.push(audit(state, "alert_signal_deduplicated", activeAlert.incidentId, "count_updated", action.safeCode));
    return state;
  }
  const generation = Math.max(0, ...state.alerts.filter((item) => item.alertKey === key).map(({ generation: value }) => value)) + 1;
  const incidentId = `incident-alert-${String(state.incidents.length + 1).padStart(3, "0")}`;
  state.incidents.push({ id: incidentId, generation, resource: action.resource, safeCode: action.safeCode, status: "detected", createdAt: action.occurredAt ?? "2026-07-22T21:09:00+09:00", resolvedAt: null });
  state.alerts.push({ alertKey: key, incidentId, generation, severity: action.severity ?? "warning", status: "active", scope: `${action.tenantId}/${action.workspaceId}`, retryable: true, userAction: "inspect_incident", safeCode: action.safeCode, traceId: `trace-alert-${generation}`, count: 1, lastSeenAt: action.occurredAt ?? "2026-07-22T21:09:00+09:00" });
  state.notifications.push(notification(state, { kind: "warning", incidentId, target: action.resource, safeCode: action.safeCode, message: "새 운영 Incident가 감지되었습니다." }));
  state.selectedIncidentId = incidentId;
  return state;
}

const DEGRADATIONS = Object.freeze({
  daon: { invariant: "DAON_ONLY_DEGRADED", message: "Daon 지식·엔진만 비활성. 유효 RuleSet Snapshot은 계속 적용합니다." },
  external_llm: { invariant: "FROZEN_POLICY_CANDIDATES_ONLY", message: "auto는 Frozen Policy 안의 승인 후보만 검토하고 pinned는 제안만 표시합니다." },
  local_llm: { invariant: "EXTERNAL_AUTO_SWITCH_FORBIDDEN", message: "명시 전송 승인 없는 External 자동 전환은 금지됩니다." },
  internet: { invariant: "INTERNET_DEPENDENT_ONLY", message: "인터넷 의존 기능만 축소하고 보유 지식 범위를 안내합니다." },
  index: { invariant: "READY_SOURCES_ONLY", message: "Ready Source만 사용하고 누락 범위를 표시합니다." },
  evidence_store: { invariant: "APPROVAL_DELIVERY_BLOCKED", message: "근거 무결성 보호를 위해 승인·전달을 차단합니다." }
});

export function transitionOperationsRecovery(current, action) {
  const state = clone(current);
  state.safety = null;
  if (action.type === "navigate") {
    state.screen = action.screen === "notifications" ? "notifications" : "operations";
    return state;
  }
  if (action.type === "select-incident") {
    state.selectedIncidentId = action.incidentId;
    return state;
  }
  if (action.type === "set-queue-filter") {
    state.queueFilter = action.queueFilter;
    return state;
  }
  if (action.type === "set-selection-mode") {
    if (!SELECTION_MODES.has(action.mode)) {
      state.safety = safe("INVALID_SELECTION_MODE", "허용되지 않은 재처리 Mode입니다.");
      return state;
    }
    state.waitingSource.selectionMode = action.mode;
    state.safety = safe("SELECTION_MODE_PREVIEW", `${action.mode} 재처리 계약을 선택했습니다.`);
    return state;
  }
  if (action.type === "mark-notification-read") {
    const item = state.notifications.find(({ id }) => id === action.notificationId);
    if (item) item.read = true;
    return state;
  }
  if (action.type === "signal-alert") return applyAlertSignal(state, action);
  if (action.type === "advance-incident") {
    const incident = state.incidents.find(({ id }) => id === action.incidentId);
    const expected = INCIDENT_STATES[INCIDENT_STATES.indexOf(incident?.status) + 1];
    if (!incident || action.status !== expected) {
      state.safety = safe("INCIDENT_TRANSITION_REQUIRED", `다음 허용 상태는 ${expected ?? "없음"}입니다.`);
      return state;
    }
    incident.status = action.status;
    state.recoveryStep = action.status;
    if (action.status === "recovered") incident.resolvedAt = action.resolvedAt ?? "2026-07-22T21:30:00+09:00";
    const alert = state.alerts.find(({ incidentId }) => incidentId === incident.id);
    if (alert) alert.status = action.status === "recovered" ? "recovered" : action.status;
    state.auditEvents.push(audit(state, "incident_transition", incident.id, action.status, incident.safeCode));
    state.notifications.push(notification(state, { kind: action.status === "recovered" ? "recovery" : "progress", severity: action.status === "recovered" ? "info" : "warning", status: action.status, target: incident.resource, safeCode: incident.safeCode, incidentId: incident.id, message: `Incident가 ${action.status} 상태로 전환되었습니다.` }));
    return state;
  }
  if (action.type === "readiness-event") {
    if (!SELECTION_MODES.has(state.waitingSource.selectionMode)) {
      state.safety = safe("INVALID_SELECTION_MODE", "허용되지 않은 재처리 Mode에서는 자동 Queue를 만들지 않습니다.");
      return state;
    }
    if (state.waitingSource.status !== "waiting_model") {
      state.safety = safe("SOURCE_NOT_WAITING_MODEL", "waiting_model Source만 Readiness Event 자동 재처리를 평가합니다.");
      return state;
    }
    if (state.readinessEvents.some(({ id }) => id === action.eventId) || state.processedEventIds.includes(action.eventId)) return suppress(state, "DUPLICATE_TRIGGER_EVENT", action);
    if (action.requiredRole !== state.waitingSource.requiredRole || action.deploymentState !== "ready" || action.nodeState !== "healthy" || action.providerState !== "healthy") {
      state.readinessEvents.push({ id: action.eventId, requiredRole: action.requiredRole, deploymentState: action.deploymentState, nodeState: action.nodeState, providerState: action.providerState, processed: false });
      state.selectedReadinessEventId = action.eventId;
      state.safety = safe("READINESS_EVENT_NOT_ELIGIBLE", "필수 역할과 모든 Runtime 상태가 ready/healthy여야 합니다.", true);
      return state;
    }
    if (state.waitingSource.selectionMode === "pinned" || state.waitingSource.selectionMode === "direct") {
      state.readinessEvents.push({ id: action.eventId, requiredRole: action.requiredRole, deploymentState: action.deploymentState, nodeState: action.nodeState, providerState: action.providerState, processed: false });
      state.selectedReadinessEventId = action.eventId;
      state.safety = safe("MANUAL_RETRY_REQUIRED", "직접 선택 Mode는 자동 실행하지 않습니다.", true, "request_manual_retry");
      return state;
    }
    if (state.waitingSource.selectionMode === "local_only" && !state.waitingSource.localOnlyAutoRetryAllowed) {
      state.readinessEvents.push({ id: action.eventId, requiredRole: action.requiredRole, deploymentState: action.deploymentState, nodeState: action.nodeState, providerState: action.providerState, processed: false });
      state.selectedReadinessEventId = action.eventId;
      state.safety = safe("MANUAL_RETRY_REQUIRED", "현재 local_only 정책은 자동 재처리를 허용하지 않습니다.", true, "request_manual_retry");
      return state;
    }
    const key = `auto:${action.eventId}`;
    const code = commonSuppression(state, action, key);
    if (code) return suppress(state, code, action);
    const previous = findRetryParent(state);
    if (!previous) return rejectMissingRetryParent(state);
    state.readinessEvents.push({ id: action.eventId, requiredRole: action.requiredRole, deploymentState: action.deploymentState, nodeState: action.nodeState, providerState: action.providerState, processed: false });
    state.selectedReadinessEventId = action.eventId;
    return createRetryRun(state, { triggerType: "readiness_event", triggerEventId: action.eventId, idempotencyKey: key, previous });
  }
  if (action.type === "manual-retry") {
    const authorization = authorizeRetry(state, action);
    if (!authorization.allowed) {
      state.safety = safe(authorization.code, "현재 Membership·Capability·Source ACL 재검증에서 거부되었습니다.");
      state.auditEvents.push(audit(state, "manual_retry", action.sourceId, "denied", authorization.code));
      return state;
    }
    const code = commonSuppression(state, action, action.idempotencyKey);
    if (code) return suppress(state, code, action);
    const previous = findRetryParent(state);
    if (!previous) return rejectMissingRetryParent(state);
    return createRetryRun(state, { triggerType: "manual", idempotencyKey: action.idempotencyKey, previous });
  }
  if (action.type === "complete-retry-preview") {
    const run = state.processingRuns.find(({ id }) => id === action.runId);
    if (!run) {
      state.safety = safe("PROCESSING_RUN_NOT_FOUND", "대상 Run Preview를 찾을 수 없습니다.");
      return state;
    }
    if (!RETRY_OUTCOMES.has(action.outcome)) {
      state.safety = safe("INVALID_RETRY_OUTCOME", "허용되지 않은 재처리 결과는 적용하지 않습니다.");
      return state;
    }
    if (run.immutable) {
      state.safety = safe("IMMUTABLE_PROCESSING_RUN", "최초 또는 과거 ProcessingRun은 변경할 수 없습니다.");
      return state;
    }
    if (run.actualRunCreated !== false || !run.retryOfProcessingRunId) {
      state.safety = safe("RETRY_RUN_NOT_ELIGIBLE", "Prototype 재처리 Run만 완료할 수 있습니다.");
      return state;
    }
    if (run.sourceVersionId !== state.waitingSource.sourceVersionId || run.requiredRole !== state.waitingSource.requiredRole) {
      state.safety = safe("PROCESSING_RUN_SCOPE_MISMATCH", "현재 SourceVersion·역할과 다른 Run은 변경하지 않습니다.");
      return state;
    }
    if (!ACTIVE_RUN_STATES.has(run.status)) {
      state.safety = safe("PROCESSING_RUN_NOT_ACTIVE", "이미 종료된 재처리 Run은 다시 완료할 수 없습니다.");
      return state;
    }
    const outcomes = {
      ready: { source: "ready", run: "completed", code: "READY_GATE_PASSED" },
      policy_blocked: { source: "needs_review", run: "policy_blocked", code: "NO_POLICY_CANDIDATE" },
      runtime_exhausted: { source: "waiting_model", run: "failed", code: "NO_AVAILABLE_UNDERSTANDING_MODEL" }
    };
    const outcome = outcomes[action.outcome];
    run.status = outcome.run;
    run.code = outcome.code;
    state.waitingSource.status = outcome.source;
    state.auditEvents.push(audit(state, "retry_result", run.id, outcome.run, outcome.code));
    state.notifications.push(notification(state, { kind: "retry_result", severity: action.outcome === "ready" ? "info" : "warning", status: outcome.run, target: run.id, safeCode: outcome.code, message: `재처리 Preview 결과: ${outcome.code}` }));
    return state;
  }
  if (action.type === "inject-failure") {
    const contract = DEGRADATIONS[action.failure];
    if (!contract) {
      state.safety = safe("FAILURE_FIXTURE_NOT_ALLOWED", "허용된 독립 장애 Fixture가 아닙니다.");
      return state;
    }
    state.degradation = { failure: action.failure, ...contract, unauthorizedFallbackCount: 0, actualServiceChanges: 0, state: "restricted" };
    state.safety = safe(`DEGRADED_${action.failure.toUpperCase()}`, contract.message, true, "inspect_degraded_scope");
    state.auditEvents.push(audit(state, "failure_fixture", action.failure, "restricted", state.safety.code));
    state.notifications.push(notification(state, { kind: "warning", target: action.failure, safeCode: state.safety.code, message: contract.message }));
    return state;
  }
  if (action.type === "preview-recovery") {
    const capability = RECOVERY_CAPABILITIES[action.action];
    const membership = state.membership;
    const membershipAllowed = capability
      && membership?.active
      && membership.tenantId === state.tenantId
      && membership.workspaceId === state.workspaceId
      && MEMBERSHIP_ROLE_SET.has(membership.role)
      && RECOVERY_ROLES.has(membership.role)
      && membership.capabilities?.includes(capability)
      && (action.tenantId === undefined || action.tenantId === state.tenantId)
      && (action.workspaceId === undefined || action.workspaceId === state.workspaceId);
    if (!membershipAllowed) {
      state.safety = safe("RECOVERY_AUTHORIZATION_DENIED", "현재 Membership·Capability·영역 재검증에서 Recovery Preview가 거부되었습니다.");
      return state;
    }
    const stepUp = state.stepUpAuthorizations.find(({ id }) => id === action.stepUpAuthorizationId);
    if (!stepUp) {
      state.safety = safe("STEP_UP_REQUIRED", "유효한 단기 추가 인증 없이는 Recovery Preview를 시작하지 않습니다.");
      return state;
    }
    const stepUpValid = stepUp.actorId === state.actorId
      && stepUp.action === action.action
      && stepUp.target === state.workspaceId
      && stepUp.tenantId === state.tenantId
      && stepUp.workspaceId === state.workspaceId
      && stepUp.policyVersion === state.policyVersion
      && stepUp.status === "issued"
      && !stepUp.usedAt
      && new Date(stepUp.expiresAt) > new Date(action.now ?? RECOVERY_NOW);
    if (!stepUpValid) {
      state.safety = safe("STEP_UP_AUTHORIZATION_INVALID", "Step-up 정본의 Actor·Action·Target·정책·상태·만료·사용 여부가 일치하지 않습니다.");
      return state;
    }
    const requiredKind = ["restore", "rollback"].includes(action.action) ? "drill" : "deploy";
    const approval = state.g9Approvals.find(({ approvalId }) => approvalId === action.approvalId);
    const approvalValid = approval
      && approval.kind === requiredKind
      && approval.target === state.workspaceId
      && approval.tenantId === state.tenantId
      && approval.workspaceId === state.workspaceId
      && approval.policyVersion === state.policyVersion
      && approval.status === "approved"
      && approval.approvedBy
      && approval.approvedAt;
    if (!approvalValid) {
      const code = requiredKind === "drill" ? "G9_DRILL_APPROVAL_REQUIRED" : "G9_DEPLOY_APPROVAL_REQUIRED";
      state.safety = safe(code, requiredKind === "drill" ? "정확한 G9-DRILL 승인 정본이 필요합니다." : "정확한 G9-DEPLOY 승인 정본이 필요합니다.");
      return state;
    }
    const actualKey = action.action === "restore" ? "restores" : action.action === "rollback" ? "rollbacks" : action.action === "update" ? "updates" : "apiWrites";
    state.recoveryPreview = { action: action.action, mode: "prototype_fixture", actual: "deferred_actual", actualCount: state.actualEffects[actualKey], approvalId: action.approvalId ?? null };
    stepUp.usedAt = action.now ?? RECOVERY_NOW;
    state.safety = safe("RECOVERY_PREVIEW_ONLY", "요청 Preview만 만들었으며 실제 작업은 실행하지 않았습니다.");
    state.auditEvents.push({ ...audit(state, "recovery_preview", action.action, "preview", state.safety.code), stepUpAuthorizationId: stepUp.id, approvalId: approval.approvalId });
    return state;
  }
  return state;
}

export function projectOperationsRecovery(state, width) {
  const layoutMode = width >= 1440 ? "wide-dashboard" : width >= 1024 ? "two-column" : width >= 600 ? "single-column" : "compact-stack";
  const availability = state.clientType === "web" || state.clientType === "windows" ? "available" : "unavailable";
  return {
    state,
    layoutMode,
    availability,
    continueOn: availability === "unavailable" ? "Web·Windows에서 이어서 작업" : null,
    route: state.screen === "notifications" ? { path: "/notifications", routeId: "notifications", screenId: "notifications", title: "알림" } : { path: "/operations", routeId: "operations", screenId: "operations", title: "운영 상태·복구" }
  };
}

export function projectOperationsRecoveryRoute(screen) {
  return screen === "notifications"
    ? { screen: "notifications", path: "/notifications", routeId: "notifications", screenId: "notifications", title: "알림" }
    : { screen: "operations", path: "/operations", routeId: "operations", screenId: "operations", title: "운영 상태·복구" };
}
