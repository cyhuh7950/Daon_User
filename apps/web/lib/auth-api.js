const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });
const SAFE_TRACE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;

function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

async function request(path, payload) {
  const response = await fetch(`/bff/api/auth/${path}`, {
    method: "POST", credentials: "same-origin", headers: JSON_HEADERS,
    body: JSON.stringify(payload),
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
  login(input) { return request("login", input); },
  verifyEmail(token) { return request("verify-email", { token }); },
  resendVerification(identifier) { return request("resend-verification", { identifier }); },
  requestPasswordReset(identifier) { return request("password-reset/request", { identifier }); },
  confirmPasswordReset(token, newPassword) { return request("password-reset/confirm", { token, new_password: newPassword }); },
});
