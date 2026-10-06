"use client";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const SAFE_TRACE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const USER_STATES = new Set(["active", "suspended", "pending_email", "pending_approval"]);
const MUTABLE_STATES = new Set(["active", "suspended"]);
const TENANT_ROLES = new Set(["personal_owner", "organization_admin"]);
const WORKSPACE_ROLES = new Set(["workspace_admin", "editor", "reviewer", "approver", "viewer"]);
export const ADMIN_MEMBERSHIP_REASONS = Object.freeze([
  { code: "ROLE_DUTY_CHANGE", label: "직무·담당 변경" },
  { code: "ACCESS_REVIEW", label: "권한 정기 검토" },
  { code: "SECURITY_RESTRICTION", label: "보안상 권한 제한" },
  { code: "CORRECTION", label: "잘못된 역할 정정" },
  { code: "OTHER", label: "기타" },
]);
const REASON_CODES = new Set(ADMIN_MEMBERSHIP_REASONS.map(({ code }) => code));

export function createAdminUserIdempotencyKey(prefix) {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return `${prefix}-${crypto.randomUUID()}`;
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

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

function validMembershipRole(value, scope) {
  return exact(value, [scope === "tenant" ? "tenant_id" : "workspace_id", "role", "state", "version"])
    && typeof value[scope === "tenant" ? "tenant_id" : "workspace_id"] === "string"
    && SAFE_ID.test(value[scope === "tenant" ? "tenant_id" : "workspace_id"])
    && (scope === "tenant" ? TENANT_ROLES : WORKSPACE_ROLES).has(value.role)
    && value.state === "active" && Number.isSafeInteger(value.version) && value.version >= 1;
}

export async function listAdminMembershipTenants({ fetchImpl = fetch, signal } = {}) {
  const response = await fetchImpl("/bff/api/admin/tenants", { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_MEMBERSHIP_UNAVAILABLE");
  if (!validEnvelope(payload) || !exact(payload.data, ["tenants"]) || !Array.isArray(payload.data.tenants)
      || payload.data.tenants.length > 5000 || !payload.data.tenants.every((tenant) =>
        exact(tenant, ["tenant_id", "name"]) && typeof tenant.tenant_id === "string"
        && SAFE_ID.test(tenant.tenant_id) && typeof tenant.name === "string"
        && tenant.name.length >= 1 && tenant.name.length <= 160)) throw new Error("ADMIN_MEMBERSHIP_RESPONSE_INVALID");
  return payload.data.tenants;
}

export async function getAdminEffectiveMemberships(tenantId, userId, { fetchImpl = fetch, signal } = {}) {
  if (typeof tenantId !== "string" || !SAFE_ID.test(tenantId) || typeof userId !== "string" || !SAFE_ID.test(userId)) {
    throw new Error("ADMIN_MEMBERSHIP_INPUT_INVALID");
  }
  const response = await fetchImpl(`/bff/api/admin/tenants/${encodeURIComponent(tenantId)}/users/${encodeURIComponent(userId)}/memberships`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_MEMBERSHIP_UNAVAILABLE");
  if (!validEnvelope(payload) || !exact(payload.data, ["tenant", "workspaces", "target_is_system_admin"])
      || typeof payload.data.target_is_system_admin !== "boolean"
      || (payload.data.tenant !== null && (!validMembershipRole(payload.data.tenant, "tenant")
        || payload.data.tenant.tenant_id !== tenantId))
      || !Array.isArray(payload.data.workspaces) || payload.data.workspaces.length > 5000
      || !payload.data.workspaces.every((item) => validMembershipRole(item, "workspace"))) {
    throw new Error("ADMIN_MEMBERSHIP_RESPONSE_INVALID");
  }
  return payload.data;
}

export async function changeAdminMembershipRole(tenantId, workspaceId, userId, input, {
  fetchImpl = fetch, signal, idempotencyKey,
} = {}) {
  if (![tenantId, workspaceId, userId].every((id) => typeof id === "string" && SAFE_ID.test(id))
      || !exact(input, ["role", "expected_version", "reason"]) || !WORKSPACE_ROLES.has(input.role)
      || !Number.isSafeInteger(input.expected_version) || input.expected_version < 1
      || !REASON_CODES.has(input.reason) || typeof idempotencyKey !== "string"
      || !SAFE_ID.test(idempotencyKey) || idempotencyKey.length < 16 || idempotencyKey.length > 128) {
    throw new Error("ADMIN_MEMBERSHIP_INPUT_INVALID");
  }
  const response = await fetchImpl(`/bff/api/admin/tenants/${encodeURIComponent(tenantId)}/workspaces/${encodeURIComponent(workspaceId)}/memberships/${encodeURIComponent(userId)}/role`, {
    method: "PATCH", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }, body: JSON.stringify(input),
  });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_MEMBERSHIP_CHANGE_FAILED");
  const result = payload?.data;
  if (!validEnvelope(payload) || !exact(result, ["tenant_id", "workspace_id", "user_id", "role", "state", "version", "acl_version", "replayed"])
      || result.tenant_id !== tenantId || result.workspace_id !== workspaceId || result.user_id !== userId
      || !WORKSPACE_ROLES.has(result.role) || result.state !== "active"
      || !Number.isSafeInteger(result.version) || result.version < input.expected_version
      || !Number.isSafeInteger(result.acl_version) || result.acl_version < 1
      || typeof result.replayed !== "boolean") throw new Error("ADMIN_MEMBERSHIP_RESPONSE_INVALID");
  return result;
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

export async function requestAdminUserPasswordReset(userId, { fetchImpl = fetch, signal, idempotencyKey } = {}) {
  if (typeof userId !== "string" || !SAFE_ID.test(userId)) throw new Error("ADMIN_USER_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/admin/users/${encodeURIComponent(userId)}/password-reset`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: mutationHeaders(idempotencyKey), body: JSON.stringify({}),
  });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_USER_PASSWORD_RESET_FAILED");
  if (!validEnvelope(payload) || !exact(payload.data, ["status", "replayed"])
      || payload.data.status !== "accepted" || typeof payload.data.replayed !== "boolean") {
    throw new Error("ADMIN_USERS_RESPONSE_INVALID");
  }
  return payload.data;
}

export async function deleteAdminUser(userId, { fetchImpl = fetch, signal, idempotencyKey } = {}) {
  if (typeof userId !== "string" || !SAFE_ID.test(userId)) throw new Error("ADMIN_USER_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/admin/users/${encodeURIComponent(userId)}`, {
    method: "DELETE", credentials: "same-origin", cache: "no-store", signal,
    headers: mutationHeaders(idempotencyKey), body: JSON.stringify({}),
  });
  const payload = await bodyOf(response);
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "ADMIN_USER_DELETE_FAILED");
  if (!validEnvelope(payload) || !exact(payload.data, ["user_id", "replayed"]) || typeof payload.data.user_id !== "string"
      || typeof payload.data.replayed !== "boolean") throw new Error("ADMIN_USERS_RESPONSE_INVALID");
  return payload.data;
}
