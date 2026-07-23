const SHELL_VERSION = "r1-m3-01";
const BUILD_ID = "r1-m3-01";

export function createWebShellRuntimeDescriptor({ now = new Date().toISOString() } = {}) {
  return Object.freeze({
    code: "WEB_SHELL_READY",
    ready: true,
    shell_version: SHELL_VERSION,
    build_id: BUILD_ID,
    downstream_state: "deferred_actual",
    observed_at: now
  });
}

export function runtimeMethodNotAllowed() {
  return Object.freeze({
    code: "METHOD_NOT_ALLOWED",
    ready: false,
    downstream_state: "deferred_actual",
    retryable: false
  });
}
