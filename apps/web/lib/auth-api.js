const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });
const SAFE_TRACE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;

function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function validAuthSession(value) {
  const keys = ["user_id", "tenant_id", "workspace_id", "session_id", "device_id", "client_kind", "delivery",
    "expires_at", "recovery_operations", "password_change_required", "is_system_admin"];
  const safeId = (id) => typeof id === "string" && SAFE_TRACE_ID.test(id);
  return (exact(value, keys) || exact(value, [...keys, "login_id"]))
    && ["user_id", "tenant_id", "session_id", "device_id"].every((key) => safeId(value[key]))
    && (value.login_id == null || (typeof value.login_id === "string" && value.login_id.length >= 1 && value.login_id.length <= 128))
    && typeof value.is_system_admin === "boolean" && typeof value.password_change_required === "boolean"
    && Array.isArray(value.recovery_operations) && value.recovery_operations.length <= 32
    && new Set(value.recovery_operations).size === value.recovery_operations.length && value.recovery_operations.every(safeId)
    && (safeId(value.workspace_id) || (value.workspace_id === null && value.is_system_admin === true && value.recovery_operations.length === 0))
    && value.client_kind === "web" && value.delivery === "same_origin_secure_cookie"
    && typeof value.expires_at === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/u.test(value.expires_at)
    && !Number.isNaN(Date.parse(value.expires_at));
}

export function authEntryPath(session) {
  if (!validAuthSession(session)) throw new Error("SESSION_RESPONSE_INVALID");
  return session.password_change_required ? "/password-change" : session.workspace_id === null ? "/operations" : "/notebooks";
}

export async function getCurrentAuthSession({ fetchImpl = fetch, signal, expectedIdentity } = {}) {
  const read = async () => {
    const response = await fetchImpl("/bff/api/session", { method: "GET", credentials: "same-origin", cache: "no-store", signal });
    if (response.status === 401) throw new Error("AUTHENTICATION_REQUIRED");
    if (!response.ok) throw new Error("SESSION_UNAVAILABLE");
    const body = await response.json().catch(() => null);
    if (!exact(body, ["data", "meta"]) || !validAuthSession(body.data)
      || !exact(body.meta, ["trace_id"]) || typeof body.meta.trace_id !== "string" || !SAFE_TRACE_ID.test(body.meta.trace_id)) {
      throw new Error("SESSION_RESPONSE_INVALID");
    }
    return body.data;
  };
  const before = await read(), after = await read();
  if (signal?.aborted) throw new Error("SESSION_CHANGED");
  if (!["user_id", "tenant_id", "workspace_id", "session_id", "device_id", "is_system_admin", "password_change_required"]
    .every((key) => before[key] === after[key]) || (expectedIdentity &&
      (after.user_id !== expectedIdentity.user_id || after.tenant_id !== expectedIdentity.tenant_id))) throw new Error("SESSION_CHANGED");
  return Object.freeze({ ...after, recovery_operations: Object.freeze([...after.recovery_operations]) });
}

async function request(path, payload, { signal } = {}) {
  const response = await fetch(`/bff/api/auth/${path}`, {
    method: "POST", credentials: "same-origin", headers: JSON_HEADERS,
    body: JSON.stringify(payload), signal,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body?.error?.code || "REQUEST_FAILED");
    error.code = body?.error?.code || "REQUEST_FAILED";
    throw error;
  }
  return body;
}

export async function logoutCurrentSession(options = {}) {
  const response = await (options.fetchImpl ?? fetch)("/bff/api/session/logout", {
    method: "POST", credentials: "same-origin", headers: JSON_HEADERS,
    body: "{}",
    signal: options.signal,
  });
  const body = await response.json().catch(() => ({}));
  if (
    !response.ok || !exact(body, ["data", "meta"])
    || !exact(body.data, ["status", "replayed"])
    || !exact(body.meta, ["trace_id"])
    || body.data.status !== "logged_out"
    || typeof body.data.replayed !== "boolean"
    || typeof body.meta.trace_id !== "string" || !SAFE_TRACE_ID.test(body.meta.trace_id)
  ) {
    const error = new Error(response.ok ? "LOGOUT_RESPONSE_INVALID" : body?.error?.code || "LOGOUT_FAILED");
    error.code = error.message;
    throw error;
  }
  return Object.freeze({ status: body.data.status, replayed: body.data.replayed });
}

export async function listSessionTenants(options = {}) {
  const response = await (options.fetchImpl ?? fetch)("/bff/api/session/tenants", {
    method: "GET", credentials: "same-origin", cache: "no-store", signal: options.signal,
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) throw new Error("AUTHENTICATION_REQUIRED");
  if (!response.ok) throw new Error(response.status === 503 ? "TENANT_SCOPE_UNAVAILABLE" : "TENANT_SCOPE_FAILED");
  const validItem = (item) => exact(item, ["tenant_id", "display_name", "kind", "workspace_id"])
    && SAFE_TRACE_ID.test(item.tenant_id) && SAFE_TRACE_ID.test(item.workspace_id)
    && typeof item.display_name === "string" && item.display_name.trim().length > 0
    && item.display_name.length <= 256 && new Set(["personal", "organization"]).has(item.kind);
  if (!exact(body, ["data", "meta"]) || !exact(body.data, ["current_tenant_id", "tenants"])
      || !exact(body.meta, ["trace_id"]) || !SAFE_TRACE_ID.test(body.meta.trace_id)
      || !SAFE_TRACE_ID.test(body.data.current_tenant_id)
      || !Array.isArray(body.data.tenants) || body.data.tenants.length > 1000
      || !body.data.tenants.every(validItem)
      || new Set(body.data.tenants.map((item) => item.tenant_id)).size !== body.data.tenants.length) {
    throw new Error("TENANT_SCOPE_RESPONSE_INVALID");
  }
  return Object.freeze({ current_tenant_id: body.data.current_tenant_id,
    tenants: Object.freeze(body.data.tenants.map((item) => Object.freeze({ ...item }))) });
}

export async function switchSessionTenant(tenantId, options = {}) {
  if (typeof tenantId !== "string" || !SAFE_TRACE_ID.test(tenantId)) throw new Error("TENANT_SCOPE_INPUT_INVALID");
  const response = await (options.fetchImpl ?? fetch)("/bff/api/session/tenant", {
    method: "POST", credentials: "same-origin", cache: "no-store", headers: JSON_HEADERS,
    body: JSON.stringify({ tenant_id: tenantId }), signal: options.signal,
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) throw new Error("AUTHENTICATION_REQUIRED");
  if (!response.ok) throw new Error(new Set([403, 404, 422, 503]).has(response.status)
    ? `TENANT_SCOPE_${response.status}` : "TENANT_SCOPE_FAILED");
  if (!exact(body, ["data", "meta"]) || !exact(body.data, ["user_id", "tenant_id", "workspace_id", "session_id"])
      || !exact(body.meta, ["trace_id"]) || !SAFE_TRACE_ID.test(body.meta.trace_id)
      || !["user_id", "tenant_id", "workspace_id", "session_id"].every((key) => SAFE_TRACE_ID.test(body.data[key]))
      || body.data.tenant_id !== tenantId) throw new Error("TENANT_SCOPE_RESPONSE_INVALID");
  return Object.freeze({ ...body.data });
}

export async function changeCurrentPassword(currentPassword, newPassword, options = {}) {
  if (typeof currentPassword !== "string" || typeof newPassword !== "string") throw new Error("PASSWORD_CHANGE_INPUT_INVALID");
  const response = await (options.fetchImpl ?? fetch)("/bff/api/auth/password/change", {
    method: "POST", credentials: "same-origin", cache: "no-store", headers: JSON_HEADERS,
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }), signal: options.signal,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const safeCode = new Set(["AUTHENTICATION_REQUIRED", "PASSWORD_POLICY_FAILED"]).has(body?.error?.code)
      ? body.error.code : "PASSWORD_CHANGE_FAILED";
    throw new Error(safeCode);
  }
  if (!exact(body, ["data", "meta"]) || !exact(body.data, ["status"]) || !exact(body.meta, ["trace_id"])
      || body.data.status !== "password_changed" || typeof body.meta.trace_id !== "string" || !SAFE_TRACE_ID.test(body.meta.trace_id)) {
    throw new Error("PASSWORD_CHANGE_RESPONSE_INVALID");
  }
  return Object.freeze({ status: body.data.status });
}

export const authApi = Object.freeze({
  signup(input) { return request("signup", input); },
  login(input, options) { return request("login", input, options); },
  verifyEmail(token) { return request("verify-email", { token }); },
  resendVerification(identifier) { return request("resend-verification", { identifier }); },
  requestPasswordReset(identifier) { return request("password-reset/request", { identifier }); },
  confirmPasswordReset(token, newPassword) { return request("password-reset/confirm", { token, new_password: newPassword }); },
});
