export const MEMBERSHIP_ROLES = Object.freeze([
  "personal_owner", "organization_admin", "workspace_admin", "editor", "reviewer", "approver", "viewer"
]);

export const DETAILED_PERMISSIONS = Object.freeze([
  "external_llm", "internet_search", "local_internal_llm", "daon_approved_knowledge",
  "file_download_share", "knowledge_registration", "data_realm_move", "final_approval_delivery"
]);

export const SENSITIVE_ACTIONS = Object.freeze([
  "approve_external_egress", "move_data_realm", "external_share_download",
  "final_approval_registration", "change_organization_policy", "revoke_device", "purge_restore"
]);

export const SENSITIVE_ACTION_REGISTRY = deepFreeze({
  approve_external_egress: { requiredPermission: "final_approval_delivery", allowedRoles: ["organization_admin", "approver"], targetPrefixes: ["output-", "export-"] },
  move_data_realm: { requiredPermission: "data_realm_move", allowedRoles: ["personal_owner", "organization_admin", "workspace_admin"], targetPrefixes: ["source-"] },
  external_share_download: { requiredPermission: "file_download_share", allowedRoles: ["personal_owner", "organization_admin", "workspace_admin", "editor"], targetPrefixes: ["output-", "file-"] },
  final_approval_registration: { requiredPermission: "final_approval_delivery", allowedRoles: ["personal_owner", "organization_admin", "approver"], targetPrefixes: ["output-", "knowledge-"] },
  change_organization_policy: { requiredPermission: "final_approval_delivery", allowedRoles: ["organization_admin"], targetPrefixes: ["tenant-organization-"] },
  revoke_device: { requiredPermission: null, allowedRoles: ["personal_owner", "organization_admin"], targetPrefixes: ["device-"] },
  purge_restore: { requiredPermission: "final_approval_delivery", allowedRoles: ["personal_owner", "organization_admin"], targetPrefixes: ["source-", "backup-"] }
});

export const REALM_MOVE_STEPS = Object.freeze([
  "target_scope", "authorization_sensitive_check", "explicit_approval", "transfer_preview", "version_audit"
]);

const ROLE_DEFAULTS = Object.freeze({
  personal_owner: [true, true, true, true, true, true, true, true],
  organization_admin: [true, true, true, true, true, true, true, true],
  workspace_admin: [false, true, true, true, true, true, true, false],
  editor: [false, true, true, true, true, false, false, false],
  reviewer: [false, false, true, true, false, false, false, false],
  approver: [false, false, true, true, true, true, false, true],
  viewer: [false, false, false, true, false, false, false, false]
});

function permissionMap(values) {
  return Object.fromEntries(DETAILED_PERMISSIONS.map((permission, index) => [permission, Boolean(values[index])]));
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.freeze(value);
  for (const child of Object.values(value)) deepFreeze(child);
  return value;
}

function safe(code, message, userAction = "review_access") {
  return { code, message, failedStage: "account_security_prototype", impact: "Prototype 변경 0건", retryable: false, userAction, traceId: "trace-security-prototype-001" };
}

function auditEvent(state, action, target, decision, code = "OK") {
  const sequence = state.auditEvents.length + 1;
  return deepFreeze({
    id: `audit-security-${String(sequence).padStart(3, "0")}`,
    actor: state.actorId,
    navigationPersona: state.navigationPersona,
    membershipRole: state.selectedRole,
    action,
    target,
    policyVersion: state.policyVersion,
    traceId: `trace-security-${String(sequence).padStart(3, "0")}`,
    decision,
    code,
    occurredAt: `2026-07-22T20:${String(sequence).padStart(2, "0")}:00+09:00`
  });
}

function organizationPolicy() {
  const locked = (effectiveValue, lockReason) => ({ requestedValue: effectiveValue, effectiveValue, locked: true, lockReason });
  return {
    ruleSet: locked("ruleset-security-v4 · mandatory", "조직 강제 RuleSet"),
    authorityBoost: locked("minimum 1.4", "Daon 승인 지식 최소 Boost"),
    models: locked("deployment-text-001, deployment-vision-001", "승인 Model Allowlist"),
    providers: locked("provider-profile-org-001", "승인 Provider Profile"),
    runtimeNodes: locked("runtime-node-org-001", "조직 관리 Runtime Node"),
    egressPolicy: locked("approved_external_only", "외부 전송 금지·Masking 정책"),
    region: locked("KR", "데이터 Residency"),
    localPrivate: locked("sensitive_sources_required", "민감 자료 Local-private 강제"),
    storageLimit: locked("100 GB", "조직 저장 한도"),
    tokenLimit: locked("2M/month", "조직 Token 한도"),
    costLimit: locked("KRW 300000/month", "조직 비용 한도"),
    retention: locked("365 days", "보존·Legal Hold 정책"),
    reviewRequired: locked("external_delivery", "외부 전달 검토 의무"),
    deliveryTargets: locked("organization_allowlist", "공유·다운로드·전달 대상 제한")
  };
}

export function resolveMembershipRole({ persona, tenantKind, membership, isOwner = false }) {
  if (persona === "personal_user") return tenantKind === "personal" && isOwner ? "personal_owner" : null;
  if (persona === "organization_member") return membership?.active && ["editor", "viewer"].includes(membership.role) ? membership.role : null;
  if (["workspace_admin", "reviewer", "approver", "organization_admin"].includes(persona)) {
    return membership?.active && membership.role === persona ? persona : null;
  }
  return null;
}

export function authorizeAccountAction(state, { role, permission, tenantId = state.tenantId, workspaceId = state.workspaceId }) {
  if (tenantId !== state.tenantId || workspaceId !== state.workspaceId) return { allowed: false, code: "CURRENT_ACCESS_DENIED", reason: "대상 Tenant·Workspace를 공개하지 않습니다." };
  const membership = state.membership;
  if (!membership?.active || membership.tenantId !== tenantId || membership.workspaceId !== workspaceId) return { allowed: false, code: "CURRENT_ACCESS_DENIED", reason: "현재 활성 Membership과 대상 Scope가 일치하지 않습니다." };
  if (role !== undefined && role !== membership.role) return { allowed: false, code: "AUTHORIZATION_DENIED", reason: "호출자가 지정한 역할은 현재 MembershipRole을 대체할 수 없습니다." };
  if (!MEMBERSHIP_ROLES.includes(membership.role) || !state.membershipGrants[membership.role]?.[permission]) return { allowed: false, code: "AUTHORIZATION_DENIED", reason: "현재 MembershipRole에 Capability가 없습니다." };
  return { allowed: true, code: "AUTHORIZED", reason: "명시 Membership Grant" };
}

export function evaluateRuleSetBindingChange(bindingType, role) {
  const allowed = bindingType === "mandatory" ? role === "organization_admin" : bindingType === "optional" && ["workspace_admin", "organization_admin"].includes(role);
  return allowed ? { allowed: true, code: "AUTHORIZED" } : { allowed: false, code: "AUTHORIZATION_DENIED" };
}

export function projectAccountSecurityRoute(screen) {
  if (screen === "organization") return { screen, path: "/settings/organization", routeId: "organization_settings", screenId: "organization_settings", title: "조직 정책·권한" };
  return { screen: "account", path: "/settings/account", routeId: "account_settings", screenId: "account_settings", title: "계정·신뢰 장치" };
}

const HISTORICAL_OUTPUT = deepFreeze({ id: "output-version-001", status: "approved", title: "승인 운영 보고서", approvalRequestId: "approval-request-001", evidenceReferenceIds: ["evidence-reference-001", "evidence-reference-002"] });
const HISTORICAL_RUN = deepFreeze({ id: "run-studio-001", policyVersion: "organization-policy-2026.07", accessSnapshot: "historical_evidence_only", costLimit: "KRW 1200" });
const HISTORICAL_EVIDENCE = deepFreeze([
  { id: "evidence-reference-001", sourceVersionId: "source-daon-guidance-v2", authorized: true },
  { id: "evidence-reference-002", sourceVersionId: "source-restricted-v1", authorized: false }
]);

export function createAccountSecurityViewState(existing = {}) {
  const grants = Object.fromEntries(MEMBERSHIP_ROLES.map((role) => [role, permissionMap(ROLE_DEFAULTS[role])]));
  const base = {
    screen: "account",
    clientType: "web",
    actorId: "actor-organization-admin-001",
    navigationPersona: "organization_admin",
    tenantId: "tenant-organization-001",
    tenantKind: "organization",
    workspaceId: "workspace-release-one",
    selectedRole: "organization_admin",
    selectedPolicy: "egressPolicy",
    selectedOutputVersionId: "output-version-001",
    membership: { id: "membership-organization-admin-001", role: "organization_admin", active: true, tenantId: "tenant-organization-001", workspaceId: "workspace-release-one" },
    membershipGrants: grants,
    policyVersion: "organization-policy-2026.07",
    organizationPolicy: organizationPolicy(),
    providerProfiles: [
      { profileId: "provider-profile-org-001", deploymentId: "deployment-text-001", allowed: true, dataRealm: "cloud_sync", policyState: "organization_allowed" },
      { profileId: "provider-profile-local-001", deploymentId: "deployment-vision-001", allowed: true, dataRealm: "local_private", policyState: "local_only" }
    ],
    devices: [
      { id: "device-windows-001", type: "Windows", status: "trusted", lastVerifiedAt: "2026-07-22T18:00:00+09:00", trustReason: "registered_device", sessionCount: 2, syncKeyState: "active" },
      { id: "device-mobile-001", type: "Android", status: "attention_required", lastVerifiedAt: "2026-07-20T09:00:00+09:00", trustReason: "verification_due", sessionCount: 1, syncKeyState: "active" },
      { id: "device-old-001", type: "iOS", status: "revoked", lastVerifiedAt: "2026-06-10T09:00:00+09:00", trustReason: "owner_revoked", sessionCount: 0, syncKeyState: "revoked" }
    ],
    stepUpAuthorizations: [],
    accessState: "available",
    accessDecisions: [],
    historicalOutputVersion: HISTORICAL_OUTPUT,
    historicalRunSnapshot: HISTORICAL_RUN,
    historicalEvidenceReferences: HISTORICAL_EVIDENCE,
    rerunPreviews: [],
    realmMove: { sourceId: "source-private-001", from: "local_private", to: "cloud_sync", tenantId: "tenant-organization-001", workspaceId: "workspace-release-one", completedSteps: [], approvalPreview: false, stepUpAuthorizationId: null },
    auditEvents: [],
    safety: null,
    policyPreview: null,
    deviceRevocationPreview: null,
    domainMutationCount: 0,
    externalCallCount: 0,
    actualSessionRevocations: 0,
    actualSyncKeyRevocations: 0,
    actualTransfers: 0,
    actualSourceVersionsCreated: 0,
    actualReindexes: 0,
    prototypeMode: "prototype_fixture",
    deferredActual: "deferred_actual · M3/M4/M5/M6/M8"
  };
  return cloneState({ ...base, ...existing, membershipGrants: existing.membershipGrants ?? grants });
}

function cloneState(state) {
  return {
    ...state,
    membership: { ...state.membership },
    membershipGrants: Object.fromEntries(Object.entries(state.membershipGrants).map(([role, grants]) => [role, { ...grants }])),
    organizationPolicy: structuredClone(state.organizationPolicy),
    providerProfiles: state.providerProfiles.map((profile) => ({ ...profile })),
    devices: state.devices.map((device) => ({ ...device })),
    stepUpAuthorizations: state.stepUpAuthorizations.map((authorization) => ({ ...authorization })),
    accessDecisions: state.accessDecisions.map((decision) => structuredClone(decision)),
    rerunPreviews: state.rerunPreviews.map((preview) => ({ ...preview })),
    realmMove: { ...state.realmMove, completedSteps: [...state.realmMove.completedSteps] },
    auditEvents: [...state.auditEvents],
    policyPreview: state.policyPreview ? { ...state.policyPreview } : null,
    deviceRevocationPreview: state.deviceRevocationPreview ? { ...state.deviceRevocationPreview } : null
  };
}

function latestStepUp(state) {
  return state.stepUpAuthorizations.at(-1) ?? null;
}

function sensitiveActionDefinition(state, actionName) {
  if (Object.hasOwn(SENSITIVE_ACTION_REGISTRY, actionName)) return SENSITIVE_ACTION_REGISTRY[actionName];
  return state.organizationSensitiveActions?.find((item) => item.action === actionName) ?? null;
}

function authorizeSensitiveAction(state, action, deniedCode = "AUTHORIZATION_DENIED") {
  const definition = sensitiveActionDefinition(state, action.action);
  if (!definition || !definition.targetPrefixes?.some((prefix) => action.target?.startsWith(prefix))) {
    return safe("STEP_UP_ACTION_NOT_ALLOWED", "승인된 민감 Action Registry와 Target 종류에 없는 작업입니다.", "select_allowed_sensitive_action");
  }
  if ((action.actor ?? state.actorId) !== state.actorId || (action.policyVersion ?? state.policyVersion) !== state.policyVersion) {
    return safe("CURRENT_ACCESS_DENIED", "현재 Actor 또는 Policy Version과 일치하지 않습니다.");
  }
  const tenantId = action.tenantId ?? state.tenantId;
  const workspaceId = action.workspaceId ?? state.workspaceId;
  const membership = state.membership;
  const role = action.role ?? state.selectedRole;
  if (!membership?.active || membership.role !== role || membership.tenantId !== tenantId || membership.workspaceId !== workspaceId || tenantId !== state.tenantId || workspaceId !== state.workspaceId) {
    return safe(tenantId !== state.tenantId || workspaceId !== state.workspaceId || membership?.tenantId !== state.tenantId || membership?.workspaceId !== state.workspaceId ? "CURRENT_ACCESS_DENIED" : deniedCode, "현재 활성 Membership과 Tenant·Workspace Scope가 일치하지 않습니다.");
  }
  if (!definition.allowedRoles.includes(role)) return safe(deniedCode, "현재 MembershipRole은 이 민감 작업을 수행할 수 없습니다.");
  if (definition.requiredPermission) {
    const decision = authorizeAccountAction(state, { role, permission: definition.requiredPermission, tenantId, workspaceId });
    if (!decision.allowed) return safe(deniedCode === "CURRENT_ACCESS_DENIED" ? deniedCode : decision.code, decision.reason);
  }
  return null;
}

function currentRealmMoveAuthorizationDenial(state) {
  const denial = authorizeSensitiveAction(state, { action: "move_data_realm", actor: state.actorId, target: state.realmMove.sourceId, policyVersion: state.policyVersion, tenantId: state.realmMove.tenantId, workspaceId: state.realmMove.workspaceId }, "CURRENT_ACCESS_DENIED");
  if (denial) return denial;
  const authorization = state.stepUpAuthorizations.find((item) => item.id === state.realmMove.stepUpAuthorizationId);
  if (!authorization || authorization.status !== "used" || authorization.actor !== state.actorId || authorization.action !== "move_data_realm" || authorization.target !== state.realmMove.sourceId || authorization.policyVersion !== state.policyVersion) {
    return safe("CURRENT_ACCESS_DENIED", "영역 이동 승인에 사용한 StepUpAuthorization의 현재 Scope가 유효하지 않습니다.");
  }
  return null;
}

function validateStepUp(state, action) {
  const authorization = latestStepUp(state);
  if (!authorization) return safe("STEP_UP_REQUIRED", "민감 작업 시작 전에 단기 추가 인증이 필요합니다.", "issue_step_up");
  if (authorization.status === "used") return safe("STEP_UP_ALREADY_USED", "한 번 사용한 StepUpAuthorization은 재사용할 수 없습니다.", "issue_new_step_up");
  if (authorization.status === "expired" || new Date(action.now ?? "2026-07-22T20:30:00+09:00") > new Date(authorization.expiresAt)) return safe("STEP_UP_EXPIRED", "StepUpAuthorization이 만료되었습니다.", "issue_new_step_up");
  const actor = action.actor ?? state.actorId;
  const policyVersion = action.policyVersion ?? state.policyVersion;
  if (authorization.actor !== actor || authorization.action !== action.action || authorization.target !== action.target || authorization.policyVersion !== policyVersion) {
    return safe("STEP_UP_SCOPE_MISMATCH", "Actor·Action·Target·Policy Version이 발급 범위와 다릅니다.", "issue_scoped_step_up");
  }
  return null;
}

function consumeStepUp(state, action) {
  const index = state.stepUpAuthorizations.length - 1;
  const authorization = state.stepUpAuthorizations[index];
  state.stepUpAuthorizations[index] = deepFreeze({ ...authorization, status: "used", usedAt: action.now ?? "2026-07-22T20:31:00+09:00" });
}

function accessDecision(state, operation) {
  const sequence = state.accessDecisions.length + 1;
  const blocked = state.accessState === "access_blocked";
  const partial = state.accessState === "partially_redacted";
  return deepFreeze({
    id: `access-decision-${String(sequence).padStart(3, "0")}`,
    actor: state.actorId,
    action: operation,
    resource: state.selectedOutputVersionId,
    membershipRole: state.selectedRole,
    workspaceAcl: state.workspaceId,
    sourceVersionPolicy: "current_only",
    policyVersion: state.policyVersion,
    state: state.accessState,
    code: blocked ? "CURRENT_ACCESS_DENIED" : "ACCESS_EVALUATED",
    maskedReferences: partial ? ["evidence-reference-002"] : [],
    reason: blocked ? "비인가 근거에 결정적으로 의존" : partial ? "현재 권한 없는 근거 구간 마스킹" : "현재 권한 허용",
    evaluatedAt: `2026-07-22T21:${String(sequence).padStart(2, "0")}:00+09:00`
  });
}

export function projectAccountSecurity(state, width) {
  const layoutMode = width >= 1440 ? "wide" : width >= 1024 ? "desktop" : width >= 600 ? "tablet" : "mobile";
  const organizationAvailability = ["web", "windows"].includes(state.clientType) ? "available" : "unavailable";
  return { state, layoutMode, columns: layoutMode === "wide" ? 3 : layoutMode === "desktop" ? 2 : 1, organizationAvailability, continueOn: organizationAvailability === "unavailable" ? "Web·Windows에서 이어서 작업" : null };
}

export function transitionAccountSecurityState(current, action) {
  const state = cloneState(current);
  state.safety = null;
  switch (action.type) {
    case "navigate":
      if (!["account", "organization"].includes(action.screen)) return current;
      state.screen = action.screen;
      return state;
    case "select-role":
      if (!MEMBERSHIP_ROLES.includes(action.role)) return current;
      state.selectedRole = action.role;
      return state;
    case "select-policy":
      if (!Object.hasOwn(state.organizationPolicy, action.policy)) return current;
      state.selectedPolicy = action.policy;
      return state;
    case "select-output-version":
      if (action.outputVersionId !== HISTORICAL_OUTPUT.id) return current;
      state.selectedOutputVersionId = action.outputVersionId;
      return state;
    case "revoke-permission":
      if (!MEMBERSHIP_ROLES.includes(action.role) || !DETAILED_PERMISSIONS.includes(action.permission)) return current;
      state.membershipGrants[action.role][action.permission] = false;
      state.auditEvents.push(auditEvent(state, "revoke_permission", `${action.role}:${action.permission}`, "previewed"));
      return state;
    case "attempt-tenant-access":
      state.safety = safe("CURRENT_ACCESS_DENIED", "다른 Tenant·Workspace 리소스 존재 여부를 공개하지 않습니다.");
      return state;
    case "attempt-role-escalation":
      state.safety = safe("AUTHORIZATION_DENIED", "자기 역할을 상위 MembershipRole로 변경할 수 없습니다.");
      return state;
    case "preview-policy-change": {
      const decision = authorizeAccountAction(state, { role: action.role, permission: "final_approval_delivery" });
      if (!decision.allowed || state.membership.role !== "organization_admin") {
        state.policyPreview = null;
        state.safety = safe("AUTHORIZATION_DENIED", "HTTP 403 계약 Preview · 실제 API 미실행 · 정책 변경 권한이 없습니다.");
        return state;
      }
      state.policyPreview = { field: action.field, requestedValue: action.value, effectiveValue: state.organizationPolicy[action.field]?.effectiveValue, policyVersion: state.policyVersion, actualApiWrites: 0 };
      return state;
    }
    case "request-policy-relaxation": {
      const policy = state.organizationPolicy[action.field];
      if (!policy) return current;
      state.policyPreview = { field: action.field, requestedValue: action.requestedValue, effectiveValue: policy.effectiveValue, lockReason: policy.lockReason, policyVersion: state.policyVersion, actualApiWrites: 0 };
      state.safety = safe("ORGANIZATION_POLICY_LOCKED", "조직 정책보다 완화된 값을 저장할 수 없습니다.", "review_lock_reason");
      return state;
    }
    case "issue-step-up": {
      const authorizationDenial = authorizeSensitiveAction(state, action);
      if (authorizationDenial) {
        state.safety = authorizationDenial;
        state.auditEvents.push(auditEvent(state, action.action, action.target, "denied", authorizationDenial.code));
        return state;
      }
      const sequence = state.stepUpAuthorizations.length + 1;
      state.stepUpAuthorizations.push(deepFreeze({
        id: `step-up-authorization-${String(sequence).padStart(3, "0")}`,
        actor: action.actor,
        action: action.action,
        target: action.target,
        policyVersion: action.policyVersion,
        issuedAt: "2026-07-22T20:20:00+09:00",
        expiresAt: action.expiresAt ?? "2026-07-22T20:40:00+09:00",
        usedAt: null,
        status: "issued",
        verification: "prototype_fixture · 실제 MFA/OIDC Token 미발급"
      }));
      state.auditEvents.push(auditEvent(state, "issue_step_up", action.target, "prototype_fixture", "STEP_UP_ISSUED_PREVIEW"));
      return state;
    }
    case "perform-sensitive-action": {
      const authorization = latestStepUp(state);
      if (authorization && authorization.policyVersion !== state.policyVersion) {
        state.safety = safe("CURRENT_ACCESS_DENIED", "발급 뒤 현재 Policy Version이 변경되어 다시 권한을 확인해야 합니다.");
        state.auditEvents.push(auditEvent(state, action.action, action.target, "denied", state.safety.code));
        return state;
      }
      const denial = validateStepUp(state, action);
      if (denial) {
        state.safety = denial;
        if (denial.code === "STEP_UP_EXPIRED") {
          const index = state.stepUpAuthorizations.length - 1;
          const authorization = state.stepUpAuthorizations[index];
          if (authorization.status === "issued") state.stepUpAuthorizations[index] = deepFreeze({ ...authorization, status: "expired" });
        }
        state.auditEvents.push(auditEvent(state, action.action, action.target, "denied", denial.code));
        return state;
      }
      const authorizationDenial = authorizeSensitiveAction(state, action, "CURRENT_ACCESS_DENIED");
      if (authorizationDenial) {
        state.safety = authorizationDenial;
        state.auditEvents.push(auditEvent(state, action.action, action.target, "denied", authorizationDenial.code));
        return state;
      }
      consumeStepUp(state, action);
      if (action.action === "revoke_device") {
        const device = state.devices.find((item) => item.id === action.target);
        if (device && device.status !== "revoked") {
          device.status = "revoked";
          device.sessionCount = 0;
          device.syncKeyState = "revoked";
          device.trustReason = "revocation_preview";
        }
        state.deviceRevocationPreview = { deviceId: action.target, sessionState: "revoked_preview", syncKeyState: "revoked_preview", actualRevocations: 0, reRegistration: "new_device_registration_preview_required" };
      }
      state.domainMutationCount += 1;
      state.auditEvents.push(auditEvent(state, action.action, action.target, "previewed", "STEP_UP_USED"));
      return state;
    }
    case "set-current-access":
      if (!["available", "partially_redacted", "access_blocked"].includes(action.accessState)) return current;
      state.accessState = action.accessState;
      return state;
    case "evaluate-past-access": {
      const decision = accessDecision(state, action.operation);
      state.accessDecisions.push(decision);
      state.auditEvents.push(auditEvent(state, `past_output_${action.operation}`, state.selectedOutputVersionId, decision.state, decision.code));
      if (action.operation === "rerun" && decision.state !== "access_blocked") {
        const sequence = state.rerunPreviews.length + 1;
        state.rerunPreviews.push(deepFreeze({
          id: `run-preview-current-policy-${String(sequence).padStart(3, "0")}`,
          previousRunId: state.historicalRunSnapshot.id,
          actor: state.actorId,
          workspaceId: state.workspaceId,
          aclSnapshot: `current:${state.accessState}`,
          dataRealm: "cloud_sync",
          policyVersion: state.policyVersion,
          costLimit: "KRW 1200",
          actualRunCount: 0,
          status: "prototype_fixture"
        }));
      }
      if (decision.state === "access_blocked") state.safety = safe("CURRENT_ACCESS_DENIED", "현재 권한으로 과거 결과 작업을 수행할 수 없습니다.");
      return state;
    }
    case "advance-realm-move": {
      const expected = REALM_MOVE_STEPS[state.realmMove.completedSteps.length];
      if (action.step !== expected) {
        state.safety = safe("REALM_MOVE_STEP_REQUIRED", `영역 이동은 ${expected} 단계를 먼저 완료해야 합니다.`, "complete_required_step");
        return state;
      }
      if (["authorization_sensitive_check", "explicit_approval"].includes(action.step)) {
        const currentAccessDenial = authorizeSensitiveAction(state, { action: "move_data_realm", actor: state.actorId, target: state.realmMove.sourceId, policyVersion: state.policyVersion, tenantId: state.realmMove.tenantId, workspaceId: state.realmMove.workspaceId }, action.step === "explicit_approval" ? "CURRENT_ACCESS_DENIED" : "AUTHORIZATION_DENIED");
        if (currentAccessDenial) {
          state.safety = currentAccessDenial;
          state.auditEvents.push(auditEvent(state, `realm_move_${action.step}`, state.realmMove.sourceId, "denied", currentAccessDenial.code));
          return state;
        }
      }
      if (action.step === "explicit_approval") {
        const scoped = { actor: state.actorId, action: "move_data_realm", target: state.realmMove.sourceId, policyVersion: state.policyVersion, now: action.now };
        const denial = validateStepUp(state, scoped);
        if (denial) {
          state.safety = denial;
          return state;
        }
        const authorizationId = latestStepUp(state).id;
        consumeStepUp(state, scoped);
        state.realmMove.approvalPreview = true;
        state.realmMove.stepUpAuthorizationId = authorizationId;
      }
      if (["transfer_preview", "version_audit"].includes(action.step)) {
        const currentAccessDenial = currentRealmMoveAuthorizationDenial(state);
        if (currentAccessDenial) {
          state.safety = currentAccessDenial;
          state.auditEvents.push(auditEvent(state, `realm_move_${action.step}`, state.realmMove.sourceId, "denied", currentAccessDenial.code));
          return state;
        }
      }
      state.realmMove.completedSteps.push(action.step);
      state.auditEvents.push(auditEvent(state, `realm_move_${action.step}`, state.realmMove.sourceId, "prototype_fixture"));
      return state;
    }
    case "mutate-audit":
      state.safety = safe("AUDIT_APPEND_ONLY", "Audit Event는 수정·삭제할 수 없습니다.", "append_new_event_only");
      return state;
    default:
      return current;
  }
}
