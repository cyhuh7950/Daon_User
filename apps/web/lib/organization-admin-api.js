"use client";

import { secureUuid } from "../../../packages/ui/src/secure-uuid.js";

const API_ROOT = "/bff/api/organization";

const key = () => `org-${secureUuid()}`;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const safeId = value => typeof value === "string" && SAFE_ID.test(value);
const exact = (value, keys) => value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(name => Object.hasOwn(value, name));
const instant = value => typeof value === "string" && /T.*(?:Z|\+00:00)$/u.test(value) && Number.isFinite(Date.parse(value));
const invitationKeys = ["invitation_id", "tenant_id", "workspace_id", "created_by", "expires_at", "max_uses", "used_count", "state", "version", "created_at", "updated_at"];
const memberKeys = ["tenant_id", "user_id", "role", "state", "version", "updated_at", "join_workspace_id", "access_role", "access_state", "access_version"];
function checked(value, kind) {
  let valid;
  if (kind === "tenant" || kind === "workspace") valid = exact(value, [kind === "tenant" ? "tenant_id" : "workspace_id", "display_name"]) && safeId(value[kind === "tenant" ? "tenant_id" : "workspace_id"]) && typeof value.display_name === "string" && value.display_name.length > 0;
  else if (kind === "invitation") valid = exact(value, invitationKeys) && ["invitation_id", "tenant_id", "created_by"].every(name => safeId(value[name])) && (value.workspace_id === null || safeId(value.workspace_id)) && ["active", "expired", "revoked"].includes(value.state) && ["created_at", "updated_at", "expires_at"].every(name => instant(value[name])) && ["max_uses", "version"].every(name => Number.isSafeInteger(value[name]) && value[name] > 0) && Number.isSafeInteger(value.used_count) && value.used_count >= 0 && value.used_count <= value.max_uses;
  else if (kind === "member") valid = exact(value, memberKeys) && safeId(value.tenant_id) && safeId(value.user_id) && typeof value.role === "string" && ["active", "suspended"].includes(value.state) && Number.isSafeInteger(value.version) && value.version > 0 && instant(value.updated_at) && (value.join_workspace_id === null ? value.access_role === null && value.access_state === null && value.access_version === null : safeId(value.join_workspace_id) && (value.access_role === null && value.access_state === null && value.access_version === null || typeof value.access_role === "string" && ["active", "suspended"].includes(value.access_state) && Number.isSafeInteger(value.access_version) && value.access_version > 0));
  else valid = exact(value, ["request_id", "tenant_id", "user_id", "invitation_id", "state", "decision_reason", "decided_by", "version", "created_at", "updated_at"]) && safeId(value.request_id) && safeId(value.tenant_id) && safeId(value.user_id) && (value.invitation_id === null || safeId(value.invitation_id)) && ["pending", "approved", "rejected"].includes(value.state) && Number.isSafeInteger(value.version) && value.version > 0 && instant(value.created_at) && instant(value.updated_at);
  if (!valid) throw new Error("ORGANIZATION_RESPONSE_INVALID");
  return value;
}
const list = async (path, kind, options) => { const values = await call(path, options); if (!Array.isArray(values)) throw new Error("ORGANIZATION_RESPONSE_INVALID"); return values.map(value => checked(value, kind)); };

async function call(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.mutation) headers.set("Idempotency-Key", options.idempotencyKey || key());
  if (options.sessionBound) {
    if (!SAFE_ID.test(options.expectedSessionId || "") || typeof options.idempotencyKey !== "string") throw new Error("ORGANIZATION_REQUEST_BINDING_REQUIRED");
    headers.set("X-Daon-Expected-Session-Id", options.expectedSessionId);
  }
  const response = await fetch(`${API_ROOT}${path}`, {
    method: options.method || "GET", credentials: "same-origin", cache: "no-store", signal: options.signal,
    headers, body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  let payload = null;
  try { payload = await response.json(); } catch { /* empty response */ }
  if (!response.ok) {
    const error = new Error(payload?.error?.code || payload?.code || `ORGANIZATION_REQUEST_${response.status}`);
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
export const listDirectory = (options) => call("/admin/directory", options);
export const listManagedOrganizations = (options = {}) => list("/admin/tenants", "tenant", options);
export const listManagedWorkspaces = (tenantId, options = {}) => list(`/tenants/${encodeURIComponent(tenantId)}/workspaces`, "workspace", options);
export const listInvitations = (tenantId, options = {}) => list(`/tenants/${encodeURIComponent(tenantId)}/invitations`, "invitation", options);
export const decideCreationRequest = (requestId, body, options = {}) => call(`/creation-requests/${encodeURIComponent(requestId)}/decision`, { ...options, method: "POST", body, mutation: true });
export const listJoinRequests = (tenantId, options = {}) => list(`/join-requests${tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ""}`, "join", options);
export const listOwnJoinRequests = (options = {}) => list("/join-requests", "join", options);
export const submitJoinRequest = async (invitationCode, options = {}) => checked(await call("/join-requests", {
  ...options, method: "POST", body: { invitation_code: invitationCode }, mutation: true, sessionBound: true,
}), "join");
export const decideJoinRequest = async (requestId, body, options = {}) => checked(await call(`/join-requests/${encodeURIComponent(requestId)}/decision`, { ...options, method: "POST", body, mutation: true, sessionBound: true }), "join");
export const createInvitation = async (tenantId, body, options = {}) => checked(await call(`/tenants/${encodeURIComponent(tenantId)}/invitations`, { ...options, method: "POST", body, mutation: true, sessionBound: true }), "invitation");
export const revokeInvitation = async (tenantId, invitationId, options = {}) => {
  if (!Number.isSafeInteger(options.expectedVersion) || options.expectedVersion < 1) throw new Error("ORGANIZATION_VERSION_REQUIRED");
  return checked(await call(`/tenants/${encodeURIComponent(tenantId)}/invitations/${encodeURIComponent(invitationId)}?expected_version=${options.expectedVersion}`, { ...options, method: "DELETE", mutation: true, sessionBound: true }), "invitation");
};
export const listMembers = (tenantId, options = {}) => list(`/tenants/${encodeURIComponent(tenantId)}/members`, "member", options);
export const changeMemberRole = async (tenantId, userId, body, options = {}) => checked(await call(`/tenants/${encodeURIComponent(tenantId)}/members/${encodeURIComponent(userId)}/role`, { ...options, method: "PATCH", body, mutation: true, sessionBound: true }), "member");
export const changeMemberState = async (tenantId, userId, active, body, options = {}) => checked(await call(`/tenants/${encodeURIComponent(tenantId)}/members/${encodeURIComponent(userId)}/state?active=${active ? "true" : "false"}`, { ...options, method: "PATCH", body, mutation: true, sessionBound: true }), "member");
