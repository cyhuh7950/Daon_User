"use client";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const SAFE_TRACE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const STATES = new Set(["active", "suspended"]);

function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function validUser(value) {
  return exact(value, ["user_id", "login_id", "has_email", "state", "protected"])
    && typeof value.user_id === "string" && SAFE_ID.test(value.user_id)
    && (value.login_id === null || (typeof value.login_id === "string" && value.login_id.length <= 255))
    && typeof value.has_email === "boolean" && STATES.has(value.state) && typeof value.protected === "boolean";
}

async function bodyOf(response) {
  try { return await response.json(); } catch { throw new Error("ADMIN_USERS_RESPONSE_INVALID"); }
}

function validEnvelope(payload) {
  return exact(payload, ["data", "meta"]) && exact(payload.meta, ["trace_id"])
    && typeof payload.meta.trace_id === "string" && SAFE_TRACE_ID.test(payload.meta.trace_id);
}

export async function listAdminUsers({ fetchImpl = fetch, signal } = {}) {
  const response = await fetchImpl("/bff/api/admin/users", { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_USERS_UNAVAILABLE");
  if (!validEnvelope(payload) || !exact(payload.data, ["users"]) || !Array.isArray(payload.data.users)
      || payload.data.users.length > 500 || !payload.data.users.every(validUser)) throw new Error("ADMIN_USERS_RESPONSE_INVALID");
  return payload.data.users;
}

export async function changeAdminUserState(userId, state, { fetchImpl = fetch, signal, idempotencyKey } = {}) {
  if (typeof userId !== "string" || !SAFE_ID.test(userId) || !STATES.has(state)
      || typeof idempotencyKey !== "string" || idempotencyKey.length < 16 || idempotencyKey.length > 128) {
    throw new Error("ADMIN_USER_INPUT_INVALID");
  }
  const response = await fetchImpl(`/bff/api/admin/users/${encodeURIComponent(userId)}/state`, {
    method: "PATCH", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ state }),
  });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_USER_STATE_FAILED");
  if (!validEnvelope(payload) || !exact(payload.data, ["user", "replayed"]) || !validUser(payload.data.user)
      || typeof payload.data.replayed !== "boolean") throw new Error("ADMIN_USERS_RESPONSE_INVALID");
  return payload.data.user;
}
