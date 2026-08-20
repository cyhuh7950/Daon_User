"use client";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const VIEW_KEYS = Object.freeze([
  "product", "edition", "license_id_hint", "issued_at", "expires_at", "status",
  "features", "resources", "warning", "creation_allowed", "existing_read_allowed",
  "existing_export_allowed", "can_apply",
]);
const RESOURCE_KEYS = Object.freeze(["resource", "limit", "used", "remaining", "status"]);
const STATUSES = Object.freeze(["not_configured", "active", "expiring_soon", "expired", "limit_reached"]);
const RESOURCES = Object.freeze(["users", "notebooks", "storage_bytes", "generation_runs", "source_versions", "studio_outputs"]);
const FEATURE = /^[a-z][a-z0-9_]{0,63}$/u;
const UTC_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/u;
const WARNING_CODES = Object.freeze(["LICENSE_NOT_CONFIGURED", "LICENSE_EXPIRED", "LICENSE_RESOURCE_LIMIT_REACHED", "LICENSE_EXPIRES_WITHIN_30_DAYS"]);

function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function safeId(value) { return typeof value === "string" && SAFE_ID.test(value); }
function nullableText(value) { return value === null || (typeof value === "string" && value.length >= 1 && value.length <= 128); }
function nullableDate(value) {
  return value === null || (typeof value === "string" && UTC_TIMESTAMP.test(value)
    && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().replace(".000Z", "Z") === value);
}

function validLicenseView(value) {
  return exact(value, VIEW_KEYS)
    && value.product === "daon-user"
    && nullableText(value.edition)
    && (value.license_id_hint === null || /^…[^\s]{5}$/u.test(value.license_id_hint))
    && nullableDate(value.issued_at) && nullableDate(value.expires_at)
    && STATUSES.includes(value.status)
    && Array.isArray(value.features) && value.features.length <= 64
    && value.features.every((item) => typeof item === "string" && FEATURE.test(item))
    && new Set(value.features).size === value.features.length
    && Array.isArray(value.resources) && value.resources.length <= 64
    && value.resources.every((item) => exact(item, RESOURCE_KEYS)
      && RESOURCES.includes(item.resource) && Number.isSafeInteger(item.limit) && item.limit >= 1
      && Number.isSafeInteger(item.used) && item.used >= 0 && Number.isSafeInteger(item.remaining) && item.remaining >= 0
      && item.remaining === Math.max(0, item.limit - item.used)
      && item.status === (item.used >= item.limit ? "limit_reached" : "available"))
    && (value.warning === null || (exact(value.warning, ["code", "action"])
      && WARNING_CODES.includes(value.warning.code) && typeof value.warning.action === "string"
      && value.warning.action.length >= 1 && value.warning.action.length <= 256))
    && [value.creation_allowed, value.existing_read_allowed, value.existing_export_allowed, value.can_apply]
      .every((item) => typeof item === "boolean");
}

async function responseData(response, fallback) {
  let payload;
  try { payload = await response.json(); } catch { throw new Error(fallback); }
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : fallback);
  if (!exact(payload, ["data", "meta"]) || !validLicenseView(payload.data)) throw new Error(fallback);
  return payload.data;
}

export async function getWorkspaceLicense(workspaceId, { fetchImpl = fetch, signal } = {}) {
  if (!safeId(workspaceId)) throw new Error("LICENSE_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/license`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  return responseData(response, "LICENSE_RESPONSE_INVALID");
}

export async function applyOrganizationLicense(
  organizationId, document, stepUpAuthorizationId,
  { fetchImpl = fetch, signal, idempotencyKey } = {},
) {
  if (!safeId(organizationId) || !document || typeof document !== "object" || Array.isArray(document)
      || !safeId(stepUpAuthorizationId) || !safeId(idempotencyKey) || idempotencyKey.length < 16) {
    throw new Error("LICENSE_APPLY_INPUT_INVALID");
  }
  const response = await fetchImpl(`/bff/api/organizations/${encodeURIComponent(organizationId)}/license`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ document, step_up_authorization_id: stepUpAuthorizationId }),
  });
  return responseData(response, "LICENSE_APPLY_RESPONSE_INVALID");
}

export async function applyCurrentOrganizationLicense(document, stepUpAuthorizationId, options = {}) {
  const fetchImpl = options.fetchImpl ?? fetch;
  const sessionResponse = await fetchImpl("/bff/api/session", {
    method: "GET", credentials: "same-origin", cache: "no-store", signal: options.signal,
  });
  let session;
  try { session = await sessionResponse.json(); } catch { throw new Error("SESSION_RESPONSE_INVALID"); }
  if (!sessionResponse.ok || !safeId(session?.data?.tenant_id)) throw new Error("SESSION_RESPONSE_INVALID");
  return applyOrganizationLicense(session.data.tenant_id, document, stepUpAuthorizationId, { ...options, fetchImpl });
}

export async function applyCurrentOrganizationLicenseWithStepUp(document, password, options = {}) {
  if (typeof password !== "string" || password.length < 12 || password.length > 1024) {
    throw new Error("STEP_UP_INPUT_INVALID");
  }
  const fetchImpl = options.fetchImpl ?? fetch;
  const sessionResponse = await fetchImpl("/bff/api/session", {
    method: "GET", credentials: "same-origin", cache: "no-store", signal: options.signal,
  });
  let session;
  try { session = await sessionResponse.json(); } catch { throw new Error("SESSION_RESPONSE_INVALID"); }
  const tenantId = session?.data?.tenant_id;
  if (!sessionResponse.ok || !safeId(tenantId)) throw new Error("SESSION_RESPONSE_INVALID");
  const idempotencyKey = options.idempotencyKey ?? crypto.randomUUID();
  const stepUpResponse = await fetchImpl("/bff/api/session/step-up", {
    method: "POST", credentials: "same-origin", cache: "no-store", signal: options.signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({
      action_group: "organization_security_or_connector_policy_change",
      target_id: tenantId,
      password,
    }),
  });
  let stepUp;
  try { stepUp = await stepUpResponse.json(); } catch { throw new Error("STEP_UP_RESPONSE_INVALID"); }
  const authorization = stepUp?.data?.step_up_authorization;
  if (!stepUpResponse.ok || !safeId(authorization)) throw new Error("STEP_UP_RESPONSE_INVALID");
  return applyOrganizationLicense(tenantId, document, authorization, { ...options, fetchImpl, idempotencyKey });
}
