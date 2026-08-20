export const PRODUCT_WORKSPACE_STATES = Object.freeze([
  "loading",
  "empty",
  "ready",
  "error",
  "forbidden",
  "unavailable"
]);

function assertSafeError(safeError) {
  if (safeError !== null && (typeof safeError !== "string" || !/^[A-Z][A-Z0-9_]{2,63}$/u.test(safeError))) {
    throw new Error("WORKSPACE_SAFE_ERROR_INVALID");
  }
}

export function createProductWorkspaceState({ status = "loading", safeError = null } = {}) {
  if (!PRODUCT_WORKSPACE_STATES.includes(status)) throw new Error("WORKSPACE_STATE_INVALID");
  assertSafeError(safeError);
  return {
    status,
    sources: [],
    selectedSource: null,
    answer: null,
    answerIntent: null,
    studioOutputs: [],
    studioLocks: [],
    studioStatus: "loading",
    studioSafeError: null,
    conversationSafeError: null,
    safeError
  };
}

export function normalizeProductWorkspaceState(state) {
  if (!PRODUCT_WORKSPACE_STATES.includes(state?.status)) throw new Error("WORKSPACE_STATE_INVALID");
  assertSafeError(state.safeError ?? null);
  assertSafeError(state.studioSafeError ?? null);
  assertSafeError(state.conversationSafeError ?? null);
  return state;
}

export function projectQuestionFailureState(current, error) {
  const code = typeof error?.message === "string" && /^[A-Z][A-Z0-9_]{2,63}$/u.test(error.message)
    ? error.message
    : "QUESTION_FAILED";
  return {
    ...current,
    safeError: null,
    conversationSafeError: code,
    answer: null,
    answerIntent: null,
  };
}

const PRODUCT_WORKSPACE_ADAPTER_METHODS = Object.freeze([
  "listSources", "uploadPdf", "getProcessingStatus", "askQuestion",
  "citationUrl", "createReport", "listStudioOutputs"
]);

export function assertProductWorkspaceAdapter(adapter) {
  if (
    !adapter
    || PRODUCT_WORKSPACE_ADAPTER_METHODS.some((method) => typeof adapter[method] !== "function")
  ) throw new Error("WORKSPACE_ADAPTER_INVALID");
  return adapter;
}

export function canCreateGroundedReport(state) {
  return state?.status === "ready"
    && state.answer?.insufficient === false
    && typeof state.answer?.run_id === "string"
    && typeof state.answer?.run_result_id === "string"
    && Array.isArray(state.answer?.citations)
    && state.answer.citations.length > 0
    && state.answer.citations.every((citation) => (
      typeof citation?.source_id === "string" && citation.source_id.length > 0
      && typeof citation?.source_version_id === "string" && citation.source_version_id.length > 0
      && (citation.origin === "daon_knowledge" || (
        citation.origin === "raw_source"
        && citation.source_id === state.selectedSource?.sourceId
        && citation.source_version_id === state.selectedSource?.sourceVersionId
      ))
    ));
}
