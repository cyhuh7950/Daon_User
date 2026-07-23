export function createWebShellRuntimeState() {
  return Object.freeze({ status: "starting", ready: false, downstream_state: "deferred_actual", last_success: null, error: null, retryable: false });
}

function isSafeDescriptor(descriptor) {
  return descriptor?.code === "WEB_SHELL_READY"
    && descriptor?.ready === true
    && descriptor?.downstream_state === "deferred_actual"
    && typeof descriptor?.shell_version === "string"
    && typeof descriptor?.build_id === "string"
    && typeof descriptor?.observed_at === "string";
}

export function transitionWebShellRuntime(state, action) {
  if (action?.type === "request-started") {
    return Object.freeze({ ...state, status: state.last_success ? "recovering" : "starting", ready: false, error: null, retryable: false });
  }
  if (action?.type === "request-succeeded" && isSafeDescriptor(action.descriptor)) {
    return Object.freeze({ status: "ready", ready: true, downstream_state: "deferred_actual", last_success: Object.freeze({ ...action.descriptor }), error: null, retryable: false });
  }
  if (action?.type === "request-failed" || action?.type === "request-succeeded") {
    return Object.freeze({ ...state, status: state.last_success ? "recovering" : "unavailable", ready: false, error: Object.freeze({ code: action.code ?? "RUNTIME_UNAVAILABLE" }), retryable: true });
  }
  return state;
}
