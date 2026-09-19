"use client";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const SAFE_TRACE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const USER_STATES = new Set(["active", "suspended", "pending_email", "pending_approval"]);
const MUTABLE_STATES = new Set(["active", "suspended"]);

function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function validUser(value, states) {
  return exact(value, ["user_id", "login_id", "email", "has_email", "state", "protected"])
    && typeof value.user_id === "string" && SAFE_ID.test(value.user_id)
    && (value.login_id === null || (typeof value.login_id === "string" && value.login_id.length <= 255))
    && (value.email === null || (typeof value.email === "string" && value.email.length <= 320))
    && typeof value.has_email === "boolean" && value.has_email === (value.email !== null)
    && states.has(value.state) && typeof value.protected === "boolean";
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
      || payload.data.users.length > 500 || !payload.data.users.every((user) => validUser(user, USER_STATES))) throw new Error("ADMIN_USERS_RESPONSE_INVALID");
  return payload.data.users;
}

export async function changeAdminUserState(userId, state, { fetchImpl = fetch, signal, idempotencyKey } = {}) {
  if (typeof userId !== "string" || !SAFE_ID.test(userId) || !MUTABLE_STATES.has(state)
      || typeof idempotencyKey !== "string" || idempotencyKey.length < 16 || idempotencyKey.length > 128) {
    throw new Error("ADMIN_USER_INPUT_INVALID");
  }
  const response = await fetchImpl(`/bff/api/admin/users/${encodeURIComponent(userId)}/state`, {
    method: "PATCH", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ state }),
  });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_USER_STATE_FAILED");
  if (!validEnvelope(payload) || !exact(payload.data, ["user", "replayed"]) || !validUser(payload.data.user, MUTABLE_STATES)
      || typeof payload.data.replayed !== "boolean") throw new Error("ADMIN_USERS_RESPONSE_INVALID");
  return payload.data.user;
}

function mutationHeaders(idempotencyKey) {
  if (typeof idempotencyKey !== "string" || idempotencyKey.length < 16 || idempotencyKey.length > 128) {
    throw new Error("ADMIN_USER_INPUT_INVALID");
  }
  return { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey };
}

async function mutation(path, method, body, { fetchImpl = fetch, signal, idempotencyKey } = {}) {
  const response = await fetchImpl(path, {
    method, credentials: "same-origin", cache: "no-store", signal,
    headers: mutationHeaders(idempotencyKey), body: JSON.stringify(body),
  });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_USER_MUTATION_FAILED");
  if (!validEnvelope(payload) || !payload.data || typeof payload.data.user !== "object"
      || !validUser(payload.data.user, USER_STATES) || typeof payload.data.replayed !== "boolean") {
    throw new Error("ADMIN_USERS_RESPONSE_INVALID");
  }
  return payload.data.user;
}

export function createAdminUser(input, options = {}) {
  if (!input || typeof input.login_id !== "string" || typeof input.email !== "string"
      || typeof input.initial_password !== "string") throw new Error("ADMIN_USER_INPUT_INVALID");
  return mutation("/bff/api/admin/users", "POST", input, options);
}

export function updateAdminUser(userId, input, options = {}) {
  if (typeof userId !== "string" || !SAFE_ID.test(userId) || !input || typeof input.email !== "string") {
    throw new Error("ADMIN_USER_INPUT_INVALID");
  }
  return mutation(`/bff/api/admin/users/${encodeURIComponent(userId)}`, "PATCH", input, options);
}

export function approveAdminUser(userId, options = {}) {
  if (typeof userId !== "string" || !SAFE_ID.test(userId)) throw new Error("ADMIN_USER_INPUT_INVALID");
  return mutation(`/bff/api/admin/users/${encodeURIComponent(userId)}/approve`, "POST", {}, options);
}

export async function deleteAdminUser(userId, { fetchImpl = fetch, signal, idempotencyKey } = {}) {
  if (typeof userId !== "string" || !SAFE_ID.test(userId)) throw new Error("ADMIN_USER_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/admin/users/${encodeURIComponent(userId)}`, {
    method: "DELETE", credentials: "same-origin", cache: "no-store", signal,
    headers: mutationHeaders(idempotencyKey), body: JSON.stringify({}),
  });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_USER_DELETE_FAILED");
  if (!validEnvelope(payload) || !payload.data || typeof payload.data.user_id !== "string"
      || typeof payload.data.replayed !== "boolean") throw new Error("ADMIN_USERS_RESPONSE_INVALID");
  return payload.data;
}
