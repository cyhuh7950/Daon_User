"use client";

const API_ROOT = "/bff/api/organization";

function key() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `org-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function call(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.mutation) headers.set("Idempotency-Key", options.idempotencyKey || key());
  const response = await fetch(`${API_ROOT}${path}`, {
    method: options.method || "GET", credentials: "same-origin", cache: "no-store", signal: options.signal,
    headers, body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  let payload = null;
  try { payload = await response.json(); } catch { /* empty response */ }
  if (!response.ok) {
    const error = new Error(payload?.error?.code || `ORGANIZATION_REQUEST_${response.status}`);
    error.status = response.status;
    throw error;
  }
  if (!payload || !Object.prototype.hasOwnProperty.call(payload, "data")) throw new Error("ORGANIZATION_RESPONSE_INVALID");
  return payload.data;
}

export function getOrganizationSession(options = {}) {
  return fetch("/bff/api/session", { method: "GET", credentials: "same-origin", cache: "no-store", signal: options.signal })
    .then(async (response) => {
      let payload = null;
      try { payload = await response.json(); } catch { /* handled below */ }
      if (!response.ok) { const error = new Error(payload?.error?.code || "AUTHENTICATION_REQUIRED"); error.status = response.status; throw error; }
      return payload?.data || null;
    });
}

export const listCreationRequests = (options) => call("/creation-requests", options);
export const decideCreationRequest = (requestId, body, options = {}) => call(`/creation-requests/${encodeURIComponent(requestId)}/decision`, { ...options, method: "POST", body, mutation: true });
export const listJoinRequests = (tenantId, options = {}) => call(`/join-requests${tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ""}`, options);
export const decideJoinRequest = (requestId, body, options = {}) => call(`/join-requests/${encodeURIComponent(requestId)}/decision`, { ...options, method: "POST", body, mutation: true });
export const createInvitation = (tenantId, body, options = {}) => call(`/tenants/${encodeURIComponent(tenantId)}/invitations`, { ...options, method: "POST", body, mutation: true });
export const revokeInvitation = (tenantId, invitationId, options = {}) => call(`/tenants/${encodeURIComponent(tenantId)}/invitations/${encodeURIComponent(invitationId)}`, { ...options, method: "DELETE", mutation: true });
export const listMembers = (tenantId, options = {}) => call(`/tenants/${encodeURIComponent(tenantId)}/members`, options);
export const changeMemberRole = (tenantId, userId, body, options = {}) => call(`/tenants/${encodeURIComponent(tenantId)}/members/${encodeURIComponent(userId)}/role`, { ...options, method: "PATCH", body, mutation: true });
export const changeMemberState = (tenantId, userId, active, body, options = {}) => call(`/tenants/${encodeURIComponent(tenantId)}/members/${encodeURIComponent(userId)}/state?active=${active ? "true" : "false"}`, { ...options, method: "PATCH", body, mutation: true });
