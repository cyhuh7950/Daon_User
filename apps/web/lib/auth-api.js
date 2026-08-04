const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });

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

export const authApi = Object.freeze({
  signup(input) { return request("signup", input); },
  login(input) { return request("login", input); },
  verifyEmail(token) { return request("verify-email", { token }); },
  resendVerification(identifier) { return request("resend-verification", { identifier }); },
  requestPasswordReset(identifier) { return request("password-reset/request", { identifier }); },
  confirmPasswordReset(token, newPassword) { return request("password-reset/confirm", { token, new_password: newPassword }); },
});
