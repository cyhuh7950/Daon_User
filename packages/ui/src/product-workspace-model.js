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
    studioOutputs: [],
    safeError
  };
}

export function normalizeProductWorkspaceState(state) {
  if (!PRODUCT_WORKSPACE_STATES.includes(state?.status)) throw new Error("WORKSPACE_STATE_INVALID");
  assertSafeError(state.safeError ?? null);
  return state;
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
    && typeof state.selectedSource?.sourceId === "string"
    && typeof state.selectedSource?.sourceVersionId === "string"
    && state.answer?.insufficient === false
    && typeof state.answer?.run_id === "string"
    && typeof state.answer?.run_result_id === "string"
    && Array.isArray(state.answer?.citations)
    && state.answer.citations.length > 0
    && state.answer.citations.every((citation) => (
      citation?.source_id === state.selectedSource.sourceId
      && citation?.source_version_id === state.selectedSource.sourceVersionId
    ));
}
