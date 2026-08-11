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
