export const SOURCE_TYPES = Object.freeze([
  "user_material",
  "internet",
  "llm_knowledge",
  "daon_approved",
  "produced_knowledge"
]);

export const RULESET_TYPE = "ruleset";

export const AUTHORITY_ORDER = Object.freeze([
  "mandatory_ruleset",
  "daon_approved",
  "user_context",
  "verified_internet",
  "llm_knowledge"
]);

export const WEIGHT_CONTRACT = Object.freeze({
  minimum: 0.5,
  maximum: 2,
  step: 0.1,
  defaultValue: 1
});

export const PROCESSING_PATHS = Object.freeze({
  document: Object.freeze({
    label: "Vision/LLM-first",
    steps: Object.freeze(["vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation", "indexing"]),
    evidence: "Page · Cell · Region"
  }),
  audio_direct: Object.freeze({
    label: "Audio LLM 직접 이해",
    steps: Object.freeze(["audio_llm_understanding", "transcript_timecode_validation", "evidence_reconciliation", "indexing"]),
    evidence: "시간 구간"
  }),
  audio_asr_llm: Object.freeze({
    label: "ASR + LLM 의미 이해",
    steps: Object.freeze(["speech_to_text", "llm_semantic_understanding", "transcript_timecode_validation", "evidence_reconciliation", "indexing"]),
    evidence: "Transcript · 시간 구간"
  })
});

const SEVERITY_ORDER = Object.freeze(["informational", "material", "critical"]);
const FINALIZATION_ACTIONS = Object.freeze(["approval", "external_delivery", "knowledge_registration"]);
export const CONFLICT_POLICY_VERSION = Object.freeze({ id: "ConflictPolicyVersion-2026.07" });

export function compareAuthority(left, right) {
  return AUTHORITY_ORDER.indexOf(left) - AUTHORITY_ORDER.indexOf(right);
}

function normalizeWeight(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < WEIGHT_CONTRACT.minimum || numeric > WEIGHT_CONTRACT.maximum)
    throw new RangeError("가중치는 0.5~2.0 범위여야 합니다");
  const steps = Math.round((numeric - WEIGHT_CONTRACT.minimum) / WEIGHT_CONTRACT.step);
  const normalized = Number((WEIGHT_CONTRACT.minimum + steps * WEIGHT_CONTRACT.step).toFixed(1));
  if (Math.abs(normalized - numeric) > Number.EPSILON * 10)
    throw new RangeError("가중치는 0.1 단위여야 합니다");
  return normalized;
}

export function resolveWeight(profile = {}) {
  const candidates = [
    ["source", profile.source],
    ["group", profile.group],
    ["type", profile.type],
    ["default", profile.defaultValue ?? WEIGHT_CONTRACT.defaultValue]
  ];
  const [layer, rawValue] = candidates.find(([, value]) => value !== undefined);
  const requested = normalizeWeight(rawValue);
  const minimum = normalizeWeight(profile.organizationMinimum ?? WEIGHT_CONTRACT.minimum);
  const maximum = normalizeWeight(profile.organizationMaximum ?? WEIGHT_CONTRACT.maximum);
  const applied = Math.min(maximum, Math.max(minimum, requested));
  return {
    requested,
    applied,
    layer,
    clampReason: applied === requested ? null : `조직 허용 범위 ${minimum.toFixed(1)}~${maximum.toFixed(1)} 적용`,
    multiplied: false
  };
}

export function evaluateReadyGate(pathId, completedSteps = []) {
  const path = PROCESSING_PATHS[pathId];
  if (!path) throw new RangeError(`지원하지 않는 처리 경로: ${pathId}`);
  const missingSteps = path.steps.filter((step) => !completedSteps.includes(step));
  return {
    ready: missingSteps.length === 0,
    missingSteps,
    result: missingSteps.length === 0 ? "ready" : "needs_review"
  };
}

export function toggleRuleSet(ruleset, enabled) {
  if (ruleset.locked) return ruleset;
  return { ...ruleset, enabled: Boolean(enabled) };
}

export function raiseConflictSeverity(conflict, severity, reviewer = "reviewer-prototype") {
  const currentIndex = SEVERITY_ORDER.indexOf(conflict.severity);
  const nextIndex = SEVERITY_ORDER.indexOf(severity);
  if (nextIndex <= currentIndex) return conflict;
  return {
    ...conflict,
    severity,
    reviewRequired: nextIndex >= 1,
    audit: [
      ...(conflict.audit ?? []),
      { action: "severity_raised", reviewer, from: conflict.severity, to: severity, policyVersion: conflict.policyVersion }
    ]
  };
}

export function resolveConflict(conflict, reviewer) {
  return {
    ...conflict,
    resolution: { status: "resolved", reviewer, action: "authority_applied_alternative_disclosed" },
    audit: [
      ...(conflict.audit ?? []),
      { action: "conflict_resolved", reviewer, result: "authority_applied_alternative_disclosed", policyVersion: conflict.policyVersion }
    ]
  };
}

export function getFinalizationLocks(conflicts = []) {
  const unresolvedImportant = conflicts.some((conflict) =>
    ["material", "critical"].includes(conflict.severity) && conflict.resolution?.status !== "resolved"
  );
  return unresolvedImportant ? [...FINALIZATION_ACTIONS] : [];
}

export function classifyConflict(facts = {}) {
  if (!facts.unresolved || !facts.affectsOutcome) return "informational";
  if (facts.mandatoryRuleSetActive || facts.daonApprovedInvolved) return "critical";
  if (facts.sameTier && facts.importantClaim) return "material";
  return "informational";
}

export function createNextSourceVersion(previousVersion, changes) {
  const versionNumber = Number(previousVersion.version ?? 1) + 1;
  return Object.freeze({
    ...previousVersion,
    ...changes,
    id: `${previousVersion.sourceId}-v${versionNumber}`,
    version: versionNumber,
    previousVersionId: previousVersion.id,
    immutable: true
  });
}

function version(sourceId, number, digest, capturedAt, evidence, previousVersionId = null) {
  return Object.freeze({
    id: `${sourceId}-v${number}`,
    sourceId,
    version: number,
    digest,
    capturedAt,
    previousVersionId,
    evidence: Object.freeze({ ...evidence }),
    immutable: true
  });
}

export function createSourcePrototypeSeed() {
  const sources = [
    {
      id: "source-daon-guidance", name: "승인 운영 지침.pdf", type: "daon_approved", group: "운영 기준", authority: "daon_approved", status: "ready", modality: "document", sensitivity: "조직 내부", region: "Cloud-sync", owner: "Release 1 Workspace", origin: "Daon 승인 지식 Connector", checkedAt: "2026-07-21 16:30 KST", active: true,
      versions: [version("source-daon-guidance", 2, "sha256:daon-approved-v2", "2026-07-21", { id: "evidence-daon-v2-page-12", name: "승인 운영 지침.pdf", position: "12쪽 · 4문단", excerpt: "승인된 기준선과 검증 증거를 함께 보존합니다.", kind: "document-region" })],
      processingRun: { status: "completed", currentStep: "ready", completedSteps: [...PROCESSING_PATHS.document.steps], evidence: "12쪽 · 4문단", readyGate: "passed" },
      weightProfile: { source: 1.9, group: 1.7, type: 1.5, organizationMinimum: 0.8, organizationMaximum: 1.6 }
    },
    {
      id: "source-user-report", name: "분기 운영 보고서.docx", type: "user_material", group: "사용자 자료", authority: "user_context", status: "ready", modality: "document", sensitivity: "일반", region: "Local-private", owner: "신산", origin: "사용자 파일", checkedAt: "2026-07-21 16:25 KST", active: true,
      versions: [
        version("source-user-report", 1, "sha256:user-report-v1", "2026-07-14", { id: "evidence-user-report-v1-table", name: "분기 운영 보고서.docx · v1", position: "2쪽 · 표 1 · Cell B4", excerpt: "초기 검토 단계는 두 단계입니다.", kind: "document-cell" }),
        version("source-user-report", 2, "sha256:user-report-v2", "2026-07-21", { id: "evidence-user-report-v2-region", name: "분기 운영 보고서.docx · v2", position: "3쪽 · 표 1 · Region B", excerpt: "최신 검토 단계와 근거 위치입니다.", kind: "document-region" }, "source-user-report-v1")
      ],
      processingRun: { status: "completed", currentStep: "ready", completedSteps: [...PROCESSING_PATHS.document.steps], evidence: "3쪽 · 표 1 · Region B", readyGate: "passed" },
      weightProfile: { group: 1.3, type: 1.2 }
    },
    {
      id: "source-audio-direct", name: "현장 회의 직접 이해.m4a", type: "user_material", group: "음성 메모", authority: "user_context", status: "ready", modality: "audio_direct", sensitivity: "일반", region: "Local-private", owner: "신산", origin: "음성 메모", checkedAt: "2026-07-21 16:20 KST", active: true,
      versions: [
        version("source-audio-direct", 1, "sha256:audio-direct-v1", "2026-07-15", { id: "evidence-audio-direct-v1-time", name: "현장 회의 직접 이해.m4a · v1", position: "00:05~00:21", excerpt: "Audio LLM이 직접 이해한 이전 회의 구간입니다.", kind: "audio-direct-timecode" }),
        version("source-audio-direct", 2, "sha256:audio-direct-v2", "2026-07-21", { id: "evidence-audio-direct-v2-time", name: "현장 회의 직접 이해.m4a · v2", position: "00:12~00:38", excerpt: "Audio LLM이 직접 이해한 최신 회의 구간입니다.", kind: "audio-direct-timecode" }, "source-audio-direct-v1")
      ],
      processingRun: { status: "completed", currentStep: "ready", completedSteps: [...PROCESSING_PATHS.audio_direct.steps], evidence: "00:12~00:38", readyGate: "passed" },
      weightProfile: { type: 1.1 }
    },
    {
      id: "source-audio-asr", name: "고객 인터뷰 ASR.wav", type: "user_material", group: "음성 메모", authority: "user_context", status: "ready", modality: "audio_asr_llm", sensitivity: "일반", region: "Local-private", owner: "신산", origin: "음성 메모", checkedAt: "2026-07-21 16:18 KST", active: true,
      versions: [version("source-audio-asr", 3, "sha256:audio-asr-v3", "2026-07-21", { id: "evidence-audio-asr-v3-transcript", name: "고객 인터뷰 ASR.wav · Transcript v3", position: "Transcript 8~12행 · 01:04~01:31", excerpt: "ASR 전사를 LLM이 의미 이해하고 검증한 구간입니다.", kind: "audio-transcript-timecode" })],
      processingRun: { status: "completed", currentStep: "ready", completedSteps: [...PROCESSING_PATHS.audio_asr_llm.steps], evidence: "Transcript v3 · 01:04~01:31", readyGate: "passed" },
      weightProfile: { source: 1.4 }
    },
    {
      id: "source-runtime-wait", name: "모델 대기 이미지.png", type: "user_material", group: "이미지", authority: "user_context", status: "waiting_model", modality: "document", sensitivity: "일반", region: "Cloud-sync", owner: "신산", origin: "사용자 파일", checkedAt: "2026-07-21 16:12 KST", active: true,
      versions: [version("source-runtime-wait", 1, "sha256:waiting-model-v1", "2026-07-21", { id: "evidence-waiting-region", name: "모델 대기 이미지.png", position: "Region 전체", excerpt: "이해 모델 대기 중이라 근거가 확정되지 않았습니다.", kind: "document-region" })],
      processingRun: { status: "failed", code: "NO_AVAILABLE_UNDERSTANDING_MODEL", currentStep: "vision_llm_understanding", completedSteps: [], evidence: "Region 전체", readyGate: "blocked" },
      retryAction: { label: "재처리 진입", availability: "unavailable", reason: "M2-07에서 자동·수동 새 Run을 연결합니다" },
      weightProfile: { defaultValue: 1 }
    },
    {
      id: "source-partial", name: "부분 이해 제안서.pptx", type: "user_material", group: "사용자 자료", authority: "user_context", status: "partial_understanding", modality: "document", sensitivity: "일반", region: "Cloud-sync", owner: "신산", origin: "사용자 파일", checkedAt: "2026-07-21 16:08 KST", active: false,
      versions: [version("source-partial", 1, "sha256:partial-v1", "2026-07-21", { id: "evidence-partial-slides", name: "부분 이해 제안서.pptx", position: "Slide 1~4", excerpt: "성공한 Slide 범위만 표시합니다.", kind: "document-region" })],
      processingRun: { status: "partial_understanding", currentStep: "vision_llm_understanding", completedSteps: ["vision_llm_understanding"], evidence: "성공 Slide 1~4 · 누락 Slide 5~7", readyGate: "excluded_from_search_and_generation" },
      recoveryOptions: ["재처리 요청 · unavailable", "검토 요청", "사용 중지"], weightProfile: { group: 1.1 }
    },
    {
      id: "source-policy-review", name: "정책 차단 표.xlsx", type: "user_material", group: "사용자 자료", authority: "user_context", status: "needs_review", modality: "document", sensitivity: "민감", region: "Local-private", owner: "신산", origin: "사용자 파일", checkedAt: "2026-07-21 16:05 KST", active: false,
      versions: [version("source-policy-review", 1, "sha256:policy-review-v1", "2026-07-21", { id: "evidence-policy-sheet", name: "정책 차단 표.xlsx", position: "Sheet 1", excerpt: "정책 허용 후보가 없어 검토가 필요합니다.", kind: "document-cell" })],
      processingRun: { status: "policy_blocked", code: "NO_POLICY_ALLOWED_CANDIDATE", currentStep: "vision_llm_understanding", completedSteps: [], evidence: "Sheet 1", readyGate: "blocked" },
      weightProfile: { defaultValue: 1 }
    },
    {
      id: "source-validation-failed", name: "검증 불일치 스캔.pdf", type: "internet", group: "인터넷 Snapshot", authority: "verified_internet", status: "needs_review", modality: "document", sensitivity: "일반", region: "Cloud-sync", owner: "Release 1 Workspace", origin: "인터넷 검색 Snapshot", checkedAt: "2026-07-21 15:58 KST", active: false,
      versions: [version("source-validation-failed", 1, "sha256:failed-v1", "2026-07-21", { id: "evidence-failed-region", name: "검증 불일치 스캔.pdf", position: "2쪽 · Region C", excerpt: "의미 이해와 Parser/OCR 검증 결과가 불일치합니다.", kind: "document-region" })],
      processingRun: { status: "needs_review", code: "EVIDENCE_RECONCILIATION_FAILED", currentStep: "evidence_reconciliation", completedSteps: ["vision_llm_understanding", "parser_ocr_validation"], evidence: "2쪽 · Region C · 양쪽 결과 보존", readyGate: "needs_review" },
      recoveryOptions: ["재처리 요청 · unavailable", "검토 요청", "사용 중지"],
      weightProfile: { type: 0.9 }
    },
    {
      id: "source-processing-failed", name: "처리 실패 첨부 자료.pdf", type: "user_material", group: "사용자 자료", authority: "user_context", status: "failed", modality: "document", sensitivity: "일반", region: "Local-private", owner: "신산", origin: "사용자 파일", checkedAt: "2026-07-21 15:55 KST", active: false,
      versions: [version("source-processing-failed", 1, "sha256:processing-failed-v1", "2026-07-21", { id: "evidence-processing-failed-region", name: "처리 실패 첨부 자료.pdf", position: "1쪽 · Region 전체", excerpt: "원본 형식 오류로 의미 이해 실행을 완료하지 못했습니다.", kind: "document-region" })],
      processingRun: { status: "failed", code: "SOURCE_PROCESSING_FAILED", currentStep: "vision_llm_understanding", completedSteps: [], evidence: "1쪽 · Region 전체", readyGate: "blocked" },
      retryAction: { label: "재처리 진입", availability: "unavailable", reason: "원인을 확인한 뒤 M2-07에서 새 ProcessingRun을 연결합니다" },
      weightProfile: { group: 1 }
    },
    {
      id: "source-expired", name: "만료된 시장 동향", type: "llm_knowledge", group: "LLM 일반지식", authority: "llm_knowledge", status: "expired", modality: "document", sensitivity: "일반", region: "Cloud-sync", owner: "Release 1 Workspace", origin: "LLM 자체 지식", checkedAt: "2026-06-20 09:00 KST", active: false,
      versions: [version("source-expired", 1, "sha256:expired-v1", "2026-06-20", { id: "evidence-expired-knowledge", name: "만료된 시장 동향", position: "LLM 지식 Snapshot", excerpt: "신선도 정책으로 만료된 지식입니다.", kind: "knowledge-snapshot" })],
      processingRun: { status: "expired", code: "SOURCE_FRESHNESS_EXPIRED", currentStep: "expired", completedSteps: [], evidence: "문서 근거 아님", readyGate: "blocked" },
      retryAction: { label: "재등록 진입", availability: "unavailable", reason: "최신 원천을 선택한 뒤 M6에서 새 SourceVersion 등록을 연결합니다" },
      weightProfile: { type: 0.8 }
    },
    {
      id: "source-produced-draft", name: "생산 지식 후보 · 운영 메모", type: "produced_knowledge", group: "생산 지식", authority: "user_context", status: "needs_review", modality: "document", sensitivity: "조직 내부", region: "Cloud-sync", owner: "신산", origin: "OutputVersion output-v4", checkedAt: "2026-07-21 15:52 KST", active: false,
      versions: [version("source-produced-draft", 1, "sha256:produced-candidate-v1", "2026-07-21", { id: "evidence-produced-output-v4", name: "운영 메모 · OutputVersion v4", position: "Section 2 · Paragraph 3", excerpt: "명시 등록 전 생산 지식 후보입니다.", kind: "output-version" })],
      processingRun: { status: "registered_candidate", currentStep: "explicit_registration_required", completedSteps: [], evidence: "OutputVersion v4", readyGate: "not_registered" },
      registration: "explicit_required", daonPromotion: "not_automatic", weightProfile: { group: 1.2 }
    }
  ];

  const rulesets = [
    { id: "ruleset-mandatory-ops", name: "운영 안전 RuleSet", binding: "mandatory", locked: true, enabled: true, condition: "외부 전달·승인", version: "rs-ops-v7", failureMode: "block" },
    { id: "ruleset-optional-style", name: "문서 표현 권고", binding: "optional", locked: false, enabled: true, condition: "보고서 생성", version: "rs-style-v3", failureMode: "warn_and_skip" },
    { id: "ruleset-optional-compliance", name: "선택 준수 점검", binding: "optional", locked: false, enabled: false, condition: "조직 자료", version: "rs-check-v2", failureMode: "block" }
  ];

  const conflictSeeds = [
    { id: "conflict-critical-001", facts: { unresolved: true, affectsOutcome: true, daonApprovedInvolved: true, mandatoryRuleSetActive: false }, claim: "외부 전달 가능 시점", sources: ["source-daon-guidance-v2", "source-user-report-v2"], applied: "Daon 승인 지식 v2", excluded: "사용자 보고서 v2", reason: "최종 전달 조건에 영향을 주는 Daon 승인 지식 상충", resolution: { status: "unresolved" } },
    { id: "conflict-material-001", facts: { unresolved: true, affectsOutcome: true, sameTier: true, importantClaim: true }, claim: "검토 단계 수", sources: ["source-user-report-v2", "source-produced-draft-v1"], applied: "사용자 보고서 v2", excluded: "생산 지식 후보", reason: "동일 Tier 중요 주장에 대한 검토 단계 불일치", resolution: { status: "unresolved" } },
    { id: "conflict-info-001", facts: { unresolved: true, affectsOutcome: false }, claim: "용어 표기", sources: ["source-daon-guidance-v2", "source-validation-failed-v1"], applied: "승인 표기", excluded: "외부 표기", reason: "결론에 영향 없는 표현 차이", resolution: { status: "alternative_disclosed" } }
  ];
  const conflicts = conflictSeeds.map((conflict) => {
    const facts = Object.freeze({ ...conflict.facts });
    const severity = classifyConflict(facts);
    return { ...conflict, facts, severity, policyVersion: CONFLICT_POLICY_VERSION.id, reviewRequired: severity !== "informational" };
  });

  return { sources, rulesets, conflicts };
}

export function selectEvidenceSnapshot(source, versionId) {
  const selectedVersion = source.versions.find((item) => item.id === versionId) ?? source.versions.at(-1);
  return Object.freeze({
    id: selectedVersion.evidence.id,
    sourceId: source.id,
    sourceVersionId: selectedVersion.id,
    name: selectedVersion.evidence.name,
    position: selectedVersion.evidence.position,
    excerpt: selectedVersion.evidence.excerpt,
    kind: selectedVersion.evidence.kind
  });
}

export function projectSourceState(source, sourceState = {}) {
  return {
    ...source,
    ...sourceState,
    audit: [...(sourceState.audit ?? source.audit ?? [])]
  };
}

export function createSourceKnowledgeViewState(existing = {}) {
  const seed = createSourcePrototypeSeed();
  return {
    activeTab: existing.activeTab ?? "overview",
    registrationOpen: existing.registrationOpen ?? false,
    versionBySource: existing.versionBySource
      ? { ...existing.versionBySource }
      : Object.fromEntries(seed.sources.map((source) => [source.id, source.versions.at(-1).id])),
    weightOverrides: { ...(existing.weightOverrides ?? {}) },
    rulesets: (existing.rulesets ?? seed.rulesets).map((ruleset) => ({ ...ruleset })),
    conflicts: (existing.conflicts ?? seed.conflicts).map((conflict) => ({ ...conflict, resolution: { ...conflict.resolution }, audit: [...(conflict.audit ?? [])] })),
    sourceStateById: existing.sourceStateById
      ? Object.fromEntries(Object.entries(existing.sourceStateById).map(([id, value]) => [id, { ...value, audit: [...(value.audit ?? [])] }]))
      : Object.fromEntries(seed.sources.map((source) => [source.id, { status: source.status, active: source.active, audit: [] }]))
  };
}

export function transitionSourceKnowledgeState(state, action) {
  const next = createSourceKnowledgeViewState(state);
  switch (action.type) {
    case "set-tab":
      next.activeTab = action.tab;
      return next;
    case "set-registration-open":
      next.registrationOpen = Boolean(action.open);
      return next;
    case "select-version":
      next.versionBySource[action.sourceId] = action.versionId;
      return next;
    case "set-weight-override":
      next.weightOverrides[action.sourceId] = normalizeWeight(action.value);
      return next;
    case "clear-weight-override":
      delete next.weightOverrides[action.sourceId];
      return next;
    case "toggle-ruleset":
      next.rulesets = next.rulesets.map((ruleset) => ruleset.id === action.rulesetId ? toggleRuleSet(ruleset, action.enabled) : ruleset);
      return next;
    case "resolve-conflict":
      next.conflicts = next.conflicts.map((conflict) => conflict.id === action.conflictId ? resolveConflict(conflict, action.reviewer ?? "reviewer-prototype") : conflict);
      return next;
    case "raise-conflict-severity":
      next.conflicts = next.conflicts.map((conflict) => conflict.id === action.conflictId ? raiseConflictSeverity(conflict, action.severity, action.reviewer ?? "reviewer-prototype") : conflict);
      return next;
    case "request-review": {
      const current = next.sourceStateById[action.sourceId];
      if (!current) return state;
      next.sourceStateById[action.sourceId] = { ...current, status: "needs_review", audit: [...current.audit, { action: "review_requested", result: "needs_review" }] };
      return next;
    }
    case "disable-source": {
      const current = next.sourceStateById[action.sourceId];
      if (!current) return state;
      next.sourceStateById[action.sourceId] = { ...current, active: false, audit: [...current.audit, { action: "source_disabled", result: "excluded_from_search_and_generation" }] };
      return next;
    }
    default:
      return state;
  }
}
