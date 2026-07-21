export const OUTPUT_TYPES = Object.freeze({
  evidence_report: Object.freeze({ label: "근거 기반 보고서", sections: Object.freeze(["요약", "본문", "결론", "인용", "경고", "미확인 사항"]), formats: Object.freeze(["DOCX", "PDF"]) }),
  compliance_checklist: Object.freeze({ label: "제약·준수 점검표", sections: Object.freeze(["항목", "판정", "근거", "RuleSet", "후속 조치"]), formats: Object.freeze(["XLSX", "CSV", "PDF"]) }),
  comparison_table: Object.freeze({ label: "비교·데이터 표", sections: Object.freeze(["기준", "값", "Cell 근거", "차이", "누락·충돌"]), formats: Object.freeze(["XLSX", "CSV", "PDF"]) }),
  knowledge_map: Object.freeze({ label: "지식 구조도·마인드맵", sections: Object.freeze(["Node", "Edge", "조건", "근거", "신뢰 상태"]), formats: Object.freeze(["JSON", "SVG", "PNG", "PDF"]) }),
  business_draft: Object.freeze({ label: "업무 문서 초안", sections: Object.freeze(["Template", "Section", "근거", "편집·검토 상태"]), formats: Object.freeze(["DOCX", "PDF"]) })
});

export const OUTPUT_VERSION_FIELDS = Object.freeze([
  "id", "outputId", "outputType", "ownerId", "workspaceId", "generationRequestId", "generationSettingsSnapshotId",
  "content", "format", "sourceVersionIds", "knowledgeScopeSnapshotId", "evidenceReferences", "authoritySnapshot",
  "weightSnapshot", "ruleSetSnapshot", "providerLineage", "modelLineage", "promptLineage", "toolLineage", "warnings",
  "unverifiedItems", "confidenceState", "revisionType", "previousVersionId", "changeReason", "reviewState", "approvalState",
  "deliveryState", "knowledgeRegistrationState", "status", "createdAt", "requiresReapproval", "unchangedSections"
]);

const LOCKED_FIELDS = new Set(["ruleSetBindings", "authorityPriority", "effectiveWeights", "dataRegion", "egressPolicy", "organizationReviewRequired"]);
export const MOBILE_STUDIO_ACTIONS = Object.freeze([
  "edit_title", "edit_text_block", "edit_simple_table_cell",
  "review_comment", "request_revision", "approve", "reject", "handle_notification", "open_citation",
  "change_section", "change_layout", "change_table_structure", "change_evidence_link", "change_generation_settings", "regenerate_all"
]);
const CONTENT_MOBILE_ACTIONS = new Set(MOBILE_STUDIO_ACTIONS.slice(0, 3));
const NON_CONTENT_MOBILE_ACTIONS = Object.freeze({ review_comment: "review", request_revision: "review", approve: "approval", reject: "approval", handle_notification: "notification", open_citation: "read" });
const BLOCKED_MOBILE_ACTIONS = new Set(MOBILE_STUDIO_ACTIONS.slice(9));
const ROLE_PERMISSIONS = Object.freeze({
  editor: new Set(["edit", "request_review"]), reviewer: new Set(["review", "request_revision"]), approver: new Set(["approve", "deliver", "download", "register"]), viewer: new Set(["read"])
});

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.freeze(value);
  for (const child of Object.values(value)) deepFreeze(child);
  return value;
}

function safe(code, message, userAction) {
  return { code, message, failedStage: "studio_prototype", impact: "현재 Prototype 동작만 차단", retryable: false, userAction, traceId: "trace-studio-prototype-001" };
}

function defaultSettings(outputType = "evidence_report") {
  const contract = OUTPUT_TYPES[outputType];
  return {
    purpose: "승인 기준선과 실행 증거를 검토 가능한 보고서로 정리",
    audience: "조직 승인자 · 운영 담당자",
    sourceSelection: { sourceIds: ["source-daon-guidance", "source-user-report"], sourceVersionIds: ["source-daon-guidance-v2", "source-user-report-v2"], knowledgeScopeId: "knowledge-scope-release-one" },
    ruleSetBindings: [{ id: "ruleset-mandatory-security", versionId: "ruleset-mandatory-security-v4", type: "mandatory", locked: true, reason: "조직 보안 정책" }],
    structure: { length: "중간", sections: [...contract.sections], tables: outputType.includes("table") || outputType.includes("checklist") ? 2 : 1, diagrams: outputType === "knowledge_map" ? 1 : 0, template: `organization-${outputType}-v3`, defaultSource: "조직 Template" },
    outputFormat: contract.formats[0],
    expertReview: { requested: true, organizationRequired: true, locked: true, reason: "외부 전달 산출물 조직 검토 필수" },
    authorityPriority: "mandatory_ruleset > daon_approved > user_context > internet > llm_knowledge",
    effectiveWeights: [{ sourceId: "source-daon-guidance", requested: 1.9, effective: 1.6, layer: "source", clampReason: "조직 최대 1.6" }],
    dataRegion: "cloud_sync · organization_tenant",
    egressPolicy: "approved_external_only"
  };
}

function cloneState(state) {
  return {
    ...state,
    request: { ...state.request }, settings: structuredClone(state.settings),
    revisions: [...state.revisions], versions: [...state.versions], approvalRequests: state.approvalRequests.map((item) => ({ ...item, audit: item.audit?.map((entry) => ({ ...entry })) ?? [] })),
    delivery: { ...state.delivery }, access: { ...state.access }, mobileDecision: state.mobileDecision ? { ...state.mobileDecision } : null,
    exportPreview: state.exportPreview ? structuredClone(state.exportPreview) : null,
    knowledgeRegistration: state.knowledgeRegistration ? structuredClone(state.knowledgeRegistration) : null,
    knowledgeRegistrations: state.knowledgeRegistrations.map((item) => structuredClone(item))
  };
}

function snapshotFor(state) {
  const sequence = state.snapshotSequence + 1;
  return deepFreeze({
    id: `generation-settings-snapshot-${String(sequence).padStart(3, "0")}`,
    actorId: "actor-prototype-editor-001", workspaceId: "workspace-release-one", outputType: state.selectedOutputType,
    settings: structuredClone(state.settings), policyVersions: { workspace: "workspace-policy-2026.07", lock: "studio-lock-policy-v1", conflict: "ConflictPolicyVersion-2026.07" },
    sourceVersionIds: [...state.settings.sourceSelection.sourceVersionIds], ruleSetVersionIds: state.settings.ruleSetBindings.map((item) => item.versionId),
    effectiveWeights: structuredClone(state.settings.effectiveWeights), reviewConditions: structuredClone(state.settings.expertReview),
    confirmedAt: `2026-07-22T14:${String(sequence).padStart(2, "0")}:00+09:00`
  });
}

function versionFor(state, { revisionType, previousVersionId = null, changeReason, status = "draft", requiresReapproval = false, unchangedSections = [] }) {
  const index = state.versions.length + 1;
  const snapshotId = state.snapshot?.id ?? state.previousSubmittedRequest?.snapshotId ?? "generation-settings-snapshot-fixture-001";
  return deepFreeze({
    id: `output-version-${String(index).padStart(3, "0")}`, outputId: "studio-output-001", outputType: state.selectedOutputType,
    ownerId: "actor-prototype-editor-001", workspaceId: "workspace-release-one", generationRequestId: state.request.id,
    generationSettingsSnapshotId: snapshotId, content: { title: OUTPUT_TYPES[state.selectedOutputType].label, sections: [...OUTPUT_TYPES[state.selectedOutputType].sections] },
    format: state.settings.outputFormat, sourceVersionIds: [...state.settings.sourceSelection.sourceVersionIds], knowledgeScopeSnapshotId: "knowledge-scope-snapshot-001",
    evidenceReferences: [{ evidenceId: "evidence-daon-v2-page-12", sourceVersionId: "source-daon-guidance-v2", position: "12쪽 · 4문단" }],
    authoritySnapshot: ["daon_approved", "user_context"], weightSnapshot: structuredClone(state.settings.effectiveWeights), ruleSetSnapshot: state.settings.ruleSetBindings.map((item) => ({ id: item.id, versionId: item.versionId })),
    providerLineage: "provider-profile-prototype-unavailable", modelLineage: "deployment-prototype-unavailable", promptLineage: "studio-prompt-contract-v1", toolLineage: ["Document Parse · location-validation-only"],
    warnings: ["Prototype Fixture · 실제 LLM 생성 아님"], unverifiedItems: ["실제 파일 내용·Layout은 M8 검증"], confidenceState: "partial",
    revisionType, previousVersionId, changeReason, reviewState: status === "review_requested" ? "requested" : "not_requested", approvalState: status === "approved" ? "approved" : "not_approved",
    deliveryState: "not_delivered", knowledgeRegistrationState: "not_registered", status, createdAt: `2026-07-22T15:${String(index).padStart(2, "0")}:00+09:00`, requiresReapproval, unchangedSections
  });
}

function replaceLastVersion(state, changes) {
  if (state.versions.length === 0) return;
  const current = state.versions.at(-1);
  state.versions[state.versions.length - 1] = deepFreeze({ ...current, ...changes });
}

function replaceVersionById(state, versionId, changes) {
  const index = state.versions.findIndex((version) => version.id === versionId);
  if (index < 0) return null;
  state.versions[index] = deepFreeze({ ...state.versions[index], ...changes });
  return state.versions[index];
}

function ensureApprovedAndAccessible(state, action) {
  const version = state.versions.at(-1);
  if (!version || version.status !== "approved") return safe("OUTPUT_VERSION_NOT_APPROVED", "승인된 특정 OutputVersion만 사용할 수 있습니다.", "request_approval");
  if (version.confidenceState === "blocked" || version.warnings.includes("IMPORTANT_KNOWLEDGE_CONFLICT")) return safe("IMPORTANT_KNOWLEDGE_CONFLICT", "중요 충돌을 해결하기 전에는 진행할 수 없습니다.", "open_conflict_review");
  if (state.access.state === "access_blocked") return safe("CURRENT_ACCESS_DENIED", `현재 Membership·ACL·SourceVersion 권한으로 ${action}할 수 없습니다.`, "request_access_review");
  return null;
}

export function createStudioViewState(existing = {}) {
  const outputType = existing.selectedOutputType ?? "evidence_report";
  const knowledgeRegistrations = existing.knowledgeRegistrations ?? (existing.knowledgeRegistration ? [existing.knowledgeRegistration] : []);
  return {
    selectedOutputType: outputType,
    request: existing.request ? { ...existing.request } : { id: "generation-request-001", status: "configuring", snapshotId: null, runId: null, studioOutputId: null },
    settings: structuredClone(existing.settings ?? defaultSettings(outputType)), snapshot: existing.snapshot ?? null, snapshotSequence: existing.snapshotSequence ?? 0,
    previousSubmittedRequest: existing.previousSubmittedRequest ?? null, revisions: [...(existing.revisions ?? [])], versions: [...(existing.versions ?? [])],
    approvalRequests: (existing.approvalRequests ?? []).map((item) => ({ ...item, audit: item.audit?.map((entry) => ({ ...entry })) ?? [] })), delivery: { status: "not_delivered", ...(existing.delivery ?? {}) },
    access: { state: "available", allowedReferences: ["evidence-daon-v2-page-12"], maskedReferences: [], decisionVersion: 1, ...(existing.access ?? {}) },
    exportPreview: existing.exportPreview ? structuredClone(existing.exportPreview) : null, knowledgeRegistration: existing.knowledgeRegistration ? structuredClone(existing.knowledgeRegistration) : null,
    knowledgeRegistrations: knowledgeRegistrations.map((item) => structuredClone(item)), cursor: existing.cursor ?? "section-2:paragraph-3",
    mobileDecision: existing.mobileDecision ? { ...existing.mobileDecision } : null, safety: existing.safety ?? null, auditPreview: [...(existing.auditPreview ?? [])], daonWrites: 0, automaticRegistrations: 0
  };
}

export function evaluateRoleAction(role, action) {
  const allowed = ROLE_PERMISSIONS[role]?.has(action) ?? false;
  return allowed ? { allowed: true, code: "ROLE_ACTION_ALLOWED" } : { allowed: false, code: action === "download" ? "CURRENT_ACCESS_DENIED" : "ROLE_ACTION_DENIED" };
}

export function evaluateMobileAction(action) {
  if (CONTENT_MOBILE_ACTIONS.has(action)) return { allowed: true, code: "MOBILE_CONTENT_EDIT_ALLOWED", createsContentRevision: true, stateDomain: "content", continueOn: "모바일에서 계속" };
  if (Object.hasOwn(NON_CONTENT_MOBILE_ACTIONS, action)) return { allowed: true, code: "MOBILE_WORKFLOW_ACTION_ALLOWED", createsContentRevision: false, stateDomain: NON_CONTENT_MOBILE_ACTIONS[action], continueOn: "모바일에서 계속" };
  if (BLOCKED_MOBILE_ACTIONS.has(action)) return { allowed: false, code: "MOBILE_STUDIO_ACTION_NOT_ALLOWED", createsContentRevision: false, stateDomain: "none", continueOn: "Web·Windows에서 이어서 작업" };
  return { allowed: false, code: "MOBILE_STUDIO_ACTION_UNKNOWN", createsContentRevision: false, stateDomain: "none", continueOn: "Web·Windows에서 확인" };
}

export function transitionStudioViewState(input, action) {
  const state = cloneState(createStudioViewState(input));
  state.safety = null;
  if (action.type === "select-output" && OUTPUT_TYPES[action.outputType]) {
    state.selectedOutputType = action.outputType; state.settings = defaultSettings(action.outputType);
    state.request = { id: `generation-request-${String(Number(state.request.id.split("-").at(-1)) || 1).padStart(3, "0")}`, status: "configuring", snapshotId: null, runId: null, studioOutputId: null };
    state.snapshot = null; return state;
  }
  if (action.type === "update-setting") {
    if (LOCKED_FIELDS.has(action.field)) { state.safety = safe("LOCKED_POLICY_CANNOT_BE_RELAXED", "조직 정책으로 잠긴 설정은 완화할 수 없습니다.", "review_lock_reason"); return state; }
    const wasSubmitted = state.request.status === "submitted";
    if (wasSubmitted && !action.reason) { state.safety = safe("CHANGE_REASON_REQUIRED", "제출 후 변경에는 변경 사유가 필요합니다.", "provide_change_reason"); return state; }
    state.settings[action.field] = action.value;
    if (wasSubmitted) {
      state.previousSubmittedRequest = { ...state.request };
      const nextRequest = Number(state.request.id.split("-").at(-1)) + 1;
      state.request = { id: `generation-request-${String(nextRequest).padStart(3, "0")}`, status: "configuring", snapshotId: null, runId: null, studioOutputId: null };
      const previous = state.versions.at(-1)?.id ?? null;
      state.revisions.push({ id: `revision-${state.revisions.length + 1}`, type: "ai_regeneration", reason: action.reason, targetSection: "all" });
      state.versions.push(versionFor(state, { revisionType: "ai_regeneration", previousVersionId: previous, changeReason: action.reason, requiresReapproval: true }));
    } else if (state.request.status === "confirmed") state.request.status = "configuring";
    state.snapshot = null; state.request.snapshotId = null; return state;
  }
  if (action.type === "confirm") {
    const contract = OUTPUT_TYPES[state.selectedOutputType];
    if (!state.settings.purpose || !state.settings.audience || !contract.formats.includes(state.settings.outputFormat)) { state.safety = safe("INVALID_GENERATION_SETTINGS", "필수값 또는 허용 출력 형식을 확인하세요.", "fix_settings"); return state; }
    state.snapshot = snapshotFor(state); state.snapshotSequence += 1; state.request.status = "confirmed"; state.request.snapshotId = state.snapshot.id; return state;
  }
  if (action.type === "submit") {
    if (state.request.status !== "confirmed") { state.safety = safe("SETTINGS_NOT_CONFIRMED", "설정 확정 전에는 생성 제출할 수 없습니다.", "confirm_settings"); return state; }
    state.request = { ...state.request, status: "submitted", runId: "run-studio-prototype-unavailable", studioOutputId: "studio-output-prototype-unavailable" }; return state;
  }
  if (action.type === "load-draft-fixture" || action.type === "load-approved-fixture") {
    if (!state.snapshot) { state.snapshot = snapshotFor(state); state.snapshotSequence += 1; }
    state.request = { ...state.request, status: "submitted", snapshotId: state.snapshot.id, runId: "run-studio-prototype-unavailable", studioOutputId: "studio-output-001" };
    state.revisions = [{ id: "revision-1", type: "generation", reason: "Prototype Fixture 생성", targetSection: "all" }];
    state.versions = [versionFor(state, { revisionType: "generation", changeReason: "최초 Prototype Fixture", status: action.type === "load-approved-fixture" ? "approved" : "draft" })];
    return state;
  }
  if (action.type === "user-edit" && state.versions.length) {
    const previous = state.versions.at(-1); state.revisions.push({ id: `revision-${state.revisions.length + 1}`, type: "user_edit", reason: action.reason, targetSection: "결론" });
    state.versions.push(versionFor(state, { revisionType: "user_edit", previousVersionId: previous.id, changeReason: action.reason, requiresReapproval: previous.status === "approved" })); return state;
  }
  if (action.type === "partial-regenerate" && state.versions.length) {
    const previous = state.versions.at(-1); const sections = OUTPUT_TYPES[state.selectedOutputType].sections.filter((item) => item !== action.section);
    state.revisions.push({ id: `revision-${state.revisions.length + 1}`, type: "ai_regeneration", reason: action.reason, targetSection: action.section, evidenceIds: ["evidence-daon-v2-page-12"], runTraceId: "trace-partial-prototype-001" });
    state.versions.push(versionFor(state, { revisionType: "ai_regeneration", previousVersionId: previous.id, changeReason: action.reason, unchangedSections: sections })); return state;
  }
  if (action.type === "request-review" && state.versions.length) { replaceLastVersion(state, { status: "review_requested", reviewState: "requested" }); return state; }
  if (action.type === "start-review" && state.versions.length) { replaceLastVersion(state, { status: "in_review", reviewState: "in_review" }); return state; }
  if (action.type === "request-revision" && state.versions.length) {
    const previous = state.versions.at(-1); replaceLastVersion(state, { status: "revision_requested", reviewState: "revision_requested" });
    state.revisions.push({ id: `revision-${state.revisions.length + 1}`, type: "user_edit", reason: action.reason, targetSection: "review_findings" });
    state.versions.push(versionFor(state, { revisionType: "user_edit", previousVersionId: previous.id, changeReason: action.reason })); return state;
  }
  if (action.type === "request-approval" && state.versions.length) {
    if (state.approvalRequests.at(-1)?.status === "pending") { state.safety = safe("APPROVAL_REQUEST_ALREADY_PENDING", "진행 중인 승인 요청이 있습니다.", "review_pending_request"); return state; }
    const days = Math.max(1, Math.min(30, action.expiresInDays ?? 7)); const id = `approval-request-${String(state.approvalRequests.length + 1).padStart(3, "0")}`;
    replaceLastVersion(state, { status: "review_requested", reviewState: "completed", approvalState: "pending" });
    const requestedAt = `2026-07-22T16:${String(state.approvalRequests.length + 1).padStart(2, "0")}:00+09:00`;
    state.approvalRequests.push({ id, outputVersionId: state.versions.at(-1).id, status: "pending", expiresInDays: days, notifyBeforeHours: 24, automaticApproval: false, auditPreserved: true, audit: [{ event: "request-approval", status: "pending", at: requestedAt }] });
    state.auditPreview.push({ domain: "approval", requestId: id, outputVersionId: state.versions.at(-1).id, status: "pending", at: requestedAt }); return state;
  }
  if (["withdraw-approval", "expire-approval", "approve", "reject"].includes(action.type) && state.approvalRequests.length) {
    const request = state.approvalRequests.at(-1);
    if (request.status !== "pending") { state.safety = safe("APPROVAL_REQUEST_NOT_PENDING", "종료된 ApprovalRequest는 변경할 수 없습니다. 새 승인 요청을 생성하세요.", "request_new_approval"); return state; }
    const map = { "withdraw-approval": "withdrawn", "expire-approval": "expired", approve: "approved", reject: "rejected" }; const status = map[action.type];
    const endedAt = `2026-07-22T16:${String(state.approvalRequests.length + 30).padStart(2, "0")}:00+09:00`;
    state.approvalRequests[state.approvalRequests.length - 1] = { ...request, status, endedAt, audit: [...request.audit, { event: action.type, status, at: endedAt }] };
    state.auditPreview.push({ domain: "approval", requestId: request.id, outputVersionId: request.outputVersionId, status, at: endedAt });
    if (status === "approved") replaceVersionById(state, request.outputVersionId, { status: "approved", approvalState: "approved" });
    if (status === "rejected") {
      const rejected = replaceVersionById(state, request.outputVersionId, { status: "revision_requested", approvalState: "rejected", reviewState: "revision_requested" });
      state.revisions.push({ id: `revision-${state.revisions.length + 1}`, type: "user_edit", reason: "승인 반려 후 수정", targetSection: "approval_findings" });
      state.versions.push(versionFor(state, { revisionType: "user_edit", previousVersionId: rejected.id, changeReason: "승인 반려 후 수정" }));
    }
    return state;
  }
  if (action.type === "post-approval-change" && state.versions.at(-1)?.status === "approved") {
    const previous = state.versions.at(-1); state.revisions.push({ id: `revision-${state.revisions.length + 1}`, type: "ai_regeneration", reason: action.reason, targetSection: "approved_output" });
    state.versions.push(versionFor(state, { revisionType: "ai_regeneration", previousVersionId: previous.id, changeReason: action.reason, requiresReapproval: true })); state.delivery.status = "blocked"; return state;
  }
  if (action.type === "set-access") {
    state.access = { state: action.accessState, allowedReferences: action.accessState === "access_blocked" ? [] : ["evidence-daon-v2-page-12"], maskedReferences: action.accessState === "partially_redacted" ? ["evidence-user-private-page-3"] : [], decisionVersion: state.access.decisionVersion + 1 }; return state;
  }
  if (action.type === "set-important-conflict" && state.versions.length) {
    replaceLastVersion(state, { confidenceState: "blocked", warnings: [...state.versions.at(-1).warnings, "IMPORTANT_KNOWLEDGE_CONFLICT"] }); return state;
  }
  if (["register-knowledge", "reject-registration"].includes(action.type)) {
    const registration = state.knowledgeRegistrations.at(-1);
    if (!registration || registration.status !== "requested") { state.safety = safe("KNOWLEDGE_REGISTRATION_NOT_REQUESTED", "requested 상태의 KnowledgeRegistration만 판정할 수 있습니다.", "request_new_registration"); return state; }
    const status = action.type === "register-knowledge" ? "registered" : "rejected";
    const endedAt = `2026-07-22T16:${String(state.knowledgeRegistrations.length + 40).padStart(2, "0")}:00+09:00`;
    const updated = { ...registration, status, endedAt, audit: [...registration.audit, { event: action.type, status, at: endedAt }] };
    state.knowledgeRegistrations[state.knowledgeRegistrations.length - 1] = updated;
    state.knowledgeRegistration = structuredClone(updated);
    replaceVersionById(state, registration.outputVersionId, { knowledgeRegistrationState: status });
    state.auditPreview.push({ domain: "knowledge_registration", registrationId: registration.id, outputVersionId: registration.outputVersionId, status, at: endedAt, externalWrites: 0 });
    return state;
  }
  if (["preview-export", "deliver", "request-registration"].includes(action.type)) {
    state.access = { ...state.access, decisionVersion: state.access.decisionVersion + 1 };
    const accessDecision = { action: action.type, state: state.access.state, checkedAt: `2026-07-22T16:${String(state.access.decisionVersion).padStart(2, "0")}:00+09:00`, decisionVersion: state.access.decisionVersion };
    const denied = ensureApprovedAndAccessible(state, action.type); if (denied) { state.safety = denied; state.auditPreview.push({ ...accessDecision, result: "denied", code: denied.code }); return state; }
    if (action.type === "preview-export") state.exportPreview = { outputVersionId: state.versions.at(-1).id, generatedAt: "Prototype timestamp", knowledgeScope: state.settings.sourceSelection.knowledgeScopeId, allowedEvidenceAppendix: state.access.allowedReferences, maskedReferences: state.access.maskedReferences, accessDecision, runtime: "Prototype · unavailable", fileCreated: false };
    if (action.type === "deliver") state.delivery = { status: "preview_only", outputVersionId: state.versions.at(-1).id, target: "approved-recipient-fixture", accessDecision, runtime: "Prototype · unavailable", delivered: false };
    if (action.type === "request-registration") {
      const sequence = state.knowledgeRegistrations.length + 1;
      const requestedAt = `2026-07-22T16:${String(sequence + 50).padStart(2, "0")}:00+09:00`;
      const registration = { id: `knowledge-registration-prototype-${String(sequence).padStart(3, "0")}`, outputVersionId: state.versions.at(-1).id, status: "requested", automatic: false, accessDecision, lineage: ["source", "run", "model", "editor", "reviewer", "output_version"], cycleDetection: "prototype_fixture", daonWrite: false, audit: [{ event: "request-registration", status: "requested", at: requestedAt }] };
      state.knowledgeRegistrations.push(registration); state.knowledgeRegistration = structuredClone(registration);
      replaceLastVersion(state, { knowledgeRegistrationState: "requested" });
    }
    return state;
  }
  if (action.type === "set-cursor" && ["section-2:paragraph-3", "section-3:table-1"].includes(action.cursor)) { state.cursor = action.cursor; return state; }
  if (action.type === "mobile-action") {
    const decision = evaluateMobileAction(action.action); state.mobileDecision = decision;
    if (!decision.allowed) { state.safety = safe(decision.code, "모바일 허용 범위 밖 작업입니다.", decision.continueOn); return state; }
    if (decision.createsContentRevision && state.versions.length) {
      const previous = state.versions.at(-1); state.revisions.push({ id: `revision-${state.revisions.length + 1}`, type: "user_edit", reason: `mobile:${action.action}`, targetSection: action.target ?? "existing_content" });
      state.versions.push(versionFor(state, { revisionType: "user_edit", previousVersionId: previous.id, changeReason: `mobile:${action.action}`, requiresReapproval: previous.status === "approved" }));
    } else state.auditPreview.push({ domain: decision.stateDomain, action: action.action, contentRevisionCreated: false });
    return state;
  }
  return state;
}
