const STATUS_COMMAND = "native_session_status";
const LOGIN_COMMAND = "native_login";
const LOGOUT_COMMAND = "native_logout";
const AUTHORIZATION_COMMAND = "native_recovery_authorization_status";
const DEFAULT_POLL_INTERVAL_MS = 1000;
const SESSION_FIELDS = Object.freeze(["user_id", "tenant_id", "workspace_id", "session_id", "device_id", "expires_at"]);
const RECOVERY_OPERATIONS = Object.freeze([
  "cloud_backup_create", "cloud_backup_get", "cloud_backup_list", "cloud_restore_cancel",
  "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview"
]);

function nativeInvoke() {
  if (typeof window === "undefined") return null;
  return window.__TAURI_INTERNALS__?.invoke ?? null;
}

function authenticationFailure() {
  const error = new Error("AUTHENTICATION_REQUIRED");
  error.code = "AUTHENTICATION_REQUIRED";
  return error;
}

function safeText(value) {
  return typeof value === "string" && value.length > 0 && value.length <= 512 && !/[\u0000-\u001f\u007f]/u.test(value);
}

function hasExactFields(value, fields) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const expected = [...fields].sort();
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function projectStatus(value) {
  if (!hasExactFields(value, ["authenticated", "session"])) throw authenticationFailure();
  if (value.authenticated === false && value.session === null) return { authenticated: false };
  if (value.authenticated !== true) throw authenticationFailure();
  const session = value?.session;
  if (!hasExactFields(session, SESSION_FIELDS) || !SESSION_FIELDS.every((field) => safeText(session[field]))) throw authenticationFailure();
  return {
    authenticated: true,
    userId: session.user_id,
    tenantId: session.tenant_id,
    workspaceId: session.workspace_id,
    sessionId: session.session_id,
    deviceId: session.device_id,
    expiresAt: session.expires_at
  };
}

function projectAuthorization(value) {
  if (!hasExactFields(value, ["recovery_operations"])) throw authenticationFailure();
  const operations = value.recovery_operations;
  const exact = Array.isArray(operations) && (operations.length === 0
    || (operations.length === RECOVERY_OPERATIONS.length
      && operations.every((operation, index) => operation === RECOVERY_OPERATIONS[index])));
  if (!exact) throw authenticationFailure();
  return { recoveryOperations: [...operations] };
}

export async function submitNativeLogin({ sessionBridge, loginId, passwordInput, onSessionChange }) {
  let password = passwordInput?.value ?? "";
  if (passwordInput) passwordInput.value = "";
  const pending = sessionBridge.login(loginId, password);
  password = "";
  const status = await pending;
  onSessionChange(status);
  return status;
}

export async function logoutNativeSession({ sessionBridge, onSessionChange }) {
  onSessionChange({ authenticated: false });
  return sessionBridge.logout();
}

export function createNativeSessionBridge({ invoke = nativeInvoke() } = {}) {
  const call = typeof invoke === "function" ? invoke : null;
  let logoutFailClosed = false;
  const execute = async (command, args) => {
    if (!call) throw authenticationFailure();
    try {
      return projectStatus(await call(command, args));
    } catch {
      throw authenticationFailure();
    }
  };
  const executeAuthorization = async () => {
    if (!call) throw authenticationFailure();
    try {
      return projectAuthorization(await call(AUTHORIZATION_COMMAND));
    } catch {
      throw authenticationFailure();
    }
  };
  const readStatus = async () => {
    const status = await execute(STATUS_COMMAND);
    return logoutFailClosed && status.authenticated ? { authenticated: false } : status;
  };
  return Object.freeze({
    status: readStatus,
    login: async (loginId, password) => {
      if (!safeText(loginId) || !safeText(password)) return Promise.reject(authenticationFailure());
      const status = await execute(LOGIN_COMMAND, { loginId, password });
      logoutFailClosed = false;
      return status;
    },
    logout: () => {
      logoutFailClosed = true;
      return execute(LOGOUT_COMMAND);
    },
    recoveryAuthorizationStatus: executeAuthorization,
    watch(onStatus, { intervalMs = DEFAULT_POLL_INTERVAL_MS, schedule = setTimeout, cancel = clearTimeout } = {}) {
      let active = true;
      let timer = null;
      let previous = null;
      const poll = async () => {
        let status;
        try { status = await readStatus(); }
        catch { status = { authenticated: false }; }
        if (!active) return;
        const serialized = JSON.stringify(status);
        if (serialized !== previous) {
          previous = serialized;
          onStatus(status);
        }
        timer = schedule(poll, intervalMs);
      };
      void poll();
      return () => {
        active = false;
        if (timer !== null) cancel(timer);
      };
    }
  });
}
