export const OUTPUT_TYPES = Object.freeze([
  Object.freeze({ id: "evidence_report", label: "근거 기반 보고서", formats: ["docx", "pdf"] }),
  Object.freeze({ id: "compliance_checklist", label: "제약·준수 점검표", formats: ["xlsx", "csv", "pdf"] }),
  Object.freeze({ id: "comparison_table", label: "비교·데이터 표", formats: ["xlsx", "csv", "pdf"] }),
  Object.freeze({ id: "knowledge_map", label: "지식 구조도", formats: ["json", "svg", "png", "pdf"] }),
  Object.freeze({ id: "business_draft", label: "업무 문서 초안", formats: ["docx", "pdf"] }),
  Object.freeze({ id: "slides", label: "슬라이드", formats: ["pdf", "json"] }),
  Object.freeze({ id: "infographic", label: "인포그래픽", formats: ["svg", "png", "pdf", "json"] }),
  Object.freeze({ id: "flashcards", label: "플래시카드", formats: ["json", "csv", "pdf"] }),
  Object.freeze({ id: "quiz", label: "퀴즈", formats: ["json", "csv", "pdf"] }),
  Object.freeze({ id: "audio", label: "AI 오디오", formats: ["json"] }),
  Object.freeze({ id: "video", label: "동영상", formats: ["json"] }),
]);

const OUTPUT_TYPE_IDS = new Set(OUTPUT_TYPES.map((item) => item.id));
const REQUIRED_SETTINGS = Object.freeze([
  "purpose", "audience", "language", "sourceVersionIds", "length", "structure", "outputFormat", "reviewCondition",
]);

function cloneLocks(locks) {
  return (Array.isArray(locks) ? locks : []).map((lock) => Object.freeze({
    field: lock.field, value: lock.value, reason: lock.reason,
  }));
}

export function createProductStudioState({ workspaceId = null, grounded = null, sources = [], selectedSourceVersionIds = [], locks = [], outputs = [], status = "ready", safeError = null } = {}) {
  const groundedVersions = grounded?.sourceVersionIds ?? (grounded?.sourceVersionId ? [grounded.sourceVersionId] : []);
  const sourceVersions = Array.isArray(selectedSourceVersionIds) && selectedSourceVersionIds.length
    ? selectedSourceVersionIds
    : groundedVersions;
  return {
    status, workspaceId, grounded, sources: Array.isArray(sources) ? sources : [], locks: cloneLocks(locks), selectedOutputType: null,
    settings: sourceVersions.length ? { sourceVersionIds: [...sourceVersions] } : {}, settingsConfirmed: false, settingsSnapshot: null,
    outputs: Array.isArray(outputs) ? outputs : [], selectedOutputId: null, pending: false, safeError,
  };
}

export function selectOutputType(state, outputType) {
  if (!OUTPUT_TYPE_IDS.has(outputType)) throw new Error("STUDIO_OUTPUT_TYPE_INVALID");
  return {
    ...state, selectedOutputType: outputType,
    settings: state.settings?.sourceVersionIds?.length
      ? { language: "ko", sourceVersionIds: [...state.settings.sourceVersionIds] }
      : state.grounded?.sourceVersionIds?.length
      ? { language: "ko", sourceVersionIds: [...state.grounded.sourceVersionIds] }
      : state.grounded?.sourceVersionId ? { language: "ko", sourceVersionIds: [state.grounded.sourceVersionId] } : { language: "ko" },
    settingsConfirmed: false, settingsSnapshot: null,
  };
}

export function updateGenerationSettings(state, patch) {
  if (!state?.selectedOutputType || !patch || typeof patch !== "object") throw new Error("STUDIO_SETTINGS_INVALID");
  const locked = new Map(state.locks.map((lock) => [lock.field, lock]));
  for (const [field, value] of Object.entries(patch)) {
    if (locked.has(field) && value !== locked.get(field).value) throw new Error("STUDIO_SETTING_LOCKED");
  }
  const settings = { ...state.settings, ...patch };
  for (const lock of state.locks) settings[lock.field] = lock.value;
  return { ...state, settings, settingsConfirmed: false, settingsSnapshot: null };
}

function completeSettings(state) {
  if (!state?.selectedOutputType) return false;
  if (!REQUIRED_SETTINGS.every((field) => {
    const value = state.settings[field];
    return Array.isArray(value) ? value.length > 0 : typeof value === "string" && value.trim().length > 0;
  })) return false;
  const type = OUTPUT_TYPES.find((item) => item.id === state.selectedOutputType);
  const groundedVersions = state.grounded
    ? (state.grounded.sourceVersionIds ?? [state.grounded.sourceVersionId])
    : (Array.isArray(state.sources) ? state.sources.filter((source) => source.ready).map((source) => source.sourceVersionId) : []);
  return type.formats.includes(state.settings.outputFormat)
    && state.settings.sourceVersionIds.every((versionId) => groundedVersions.includes(versionId))
    && state.settings.sourceVersionIds.length > 0;
}

export function confirmGenerationSettings(state) {
  if (!completeSettings(state)) throw new Error("STUDIO_SETTINGS_INCOMPLETE");
  return { ...state, settingsConfirmed: true, settingsSnapshot: Object.freeze({ ...state.settings }) };
}

export function canSubmitGeneration(state) {
  return state?.settingsConfirmed === true && completeSettings(state) && state.pending !== true;
}

export function createStudioGenerationInput(state) {
  if (!canSubmitGeneration(state)) throw new Error("STUDIO_SETTINGS_NOT_CONFIRMED");
  return {
    workspace_id: state.workspaceId,
    output_type: state.selectedOutputType,
    source_only: !state.grounded,
    source_id: state.grounded?.sourceId ?? state.sources.find((source) => source.sourceVersionId === state.settingsSnapshot.sourceVersionIds[0])?.sourceId ?? null,
    source_version_ids: [...state.settingsSnapshot.sourceVersionIds],
    run_id: state.grounded?.runId ?? null,
    run_result_id: state.grounded?.runResultId ?? null,
    settings: {
      purpose: state.settingsSnapshot.purpose,
      audience: state.settingsSnapshot.audience,
      language: state.settingsSnapshot.language,
      source_version_ids: [...state.settingsSnapshot.sourceVersionIds],
      ruleset_version_id: state.settingsSnapshot.rulesetVersionId ?? null,
      length: state.settingsSnapshot.length,
      structure: state.settingsSnapshot.structure,
      output_format: state.settingsSnapshot.outputFormat,
      review_condition: state.settingsSnapshot.reviewCondition,
    },
  };
}

export function mergeStudioVersion(output, version) {
  if (!output || !version || typeof version.output_version_id !== "string") throw new Error("STUDIO_VERSION_INVALID");
  const merged = { ...output, ...version };
  for (const field of ["review_request_id", "approval_request_id", "approval_id", "delivery_id", "knowledge_registration_id"]) delete merged[field];
  return merged;
}
