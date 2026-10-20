async function safeJson(response) {
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) throw new Error("UPSTREAM_FAILURE");
  const value = await response.json();
  if (!response.ok) throw new Error(value?.error?.code || "UPSTREAM_FAILURE");
  return { data: value.data, etag: response.headers.get("etag") };
}

const POLICY_KEYS = Object.freeze([
  "allowed_destinations", "allowed_provider_kinds", "classification", "masking_required",
  "max_bytes", "mode", "redaction_required", "required_approver",
]);
const EFFECTIVE_KEYS = Object.freeze([
  "allowed_destinations", "allowed_provider_kinds", "classification", "fingerprint",
  "masking_required", "max_bytes", "mode", "organization_binding_id", "organization_etag",
  "organization_policy", "organization_policy_version_id", "parent_locked", "redaction_required",
  "required_approver", "workspace_binding_id", "workspace_etag", "workspace_policy",
  "workspace_policy_version_id",
]);
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const DESTINATION = /^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/u;
const PROVIDER_KINDS = new Set(["external_api", "server_internal", "local_runtime"]);

function exactKeys(value, expected) {
  return value && typeof value === "object" && !Array.isArray(value)
    && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...expected].sort());
}

function policyKeys(value, expected) {
  return exactKeys(value, value && Object.hasOwn(value, "question_step_up_required")
    ? [...expected, "question_step_up_required"] : expected);
}

function normalizePolicy(value) {
  if (!policyKeys(value, POLICY_KEYS)) throw new Error("EGRESS_POLICY_RESPONSE_INVALID");
  const providers = value.allowed_provider_kinds;
  const destinations = value.allowed_destinations;
  if (
    !["deny_external", "allow_approved_external"].includes(value.mode)
    || !Array.isArray(providers) || providers.length > 32 || new Set(providers).size !== providers.length
    || providers.some((item) => !PROVIDER_KINDS.has(item))
    || !Array.isArray(destinations) || destinations.length > 64 || new Set(destinations).size !== destinations.length
    || destinations.some((item) => typeof item !== "string" || !DESTINATION.test(item))
    || !["public", "internal", "confidential", "restricted"].includes(value.classification)
    || !Number.isInteger(value.max_bytes) || value.max_bytes < 0 || value.max_bytes > 104_857_600
    || typeof value.masking_required !== "boolean" || typeof value.redaction_required !== "boolean"
    || !["workspace_manager", "organization_admin"].includes(value.required_approver)
    || (Object.hasOwn(value, "question_step_up_required") && typeof value.question_step_up_required !== "boolean")
    || (value.mode === "deny_external" && value.question_step_up_required === false)
    || (value.mode === "deny_external" && (providers.length || destinations.length || value.max_bytes !== 0))
  ) throw new Error("EGRESS_POLICY_RESPONSE_INVALID");
  return Object.freeze({ ...value, allowed_provider_kinds: Object.freeze([...providers]), allowed_destinations: Object.freeze([...destinations]) });
}

function normalizeEffectivePolicy(value) {
  if (!policyKeys(value, EFFECTIVE_KEYS)) throw new Error("EGRESS_POLICY_RESPONSE_INVALID");
  for (const key of ["organization_policy_version_id", "organization_binding_id", "workspace_policy_version_id", "workspace_binding_id"]) {
    if (typeof value[key] !== "string" || !SAFE_ID.test(value[key])) throw new Error("EGRESS_POLICY_RESPONSE_INVALID");
  }
  if (
    typeof value.parent_locked !== "boolean"
    || typeof value.organization_etag !== "string" || value.organization_etag.length > 512
    || typeof value.workspace_etag !== "string" || value.workspace_etag.length > 512
    || typeof value.fingerprint !== "string" || !/^sha256:[0-9a-f]{64}$/u.test(value.fingerprint)
  ) throw new Error("EGRESS_POLICY_RESPONSE_INVALID");
  const effective = normalizePolicy({ ...Object.fromEntries(POLICY_KEYS.map((key) => [key, value[key]])),
    ...(Object.hasOwn(value, "question_step_up_required") ? { question_step_up_required: value.question_step_up_required } : {}) });
  return Object.freeze({
    ...value, ...effective,
    organization_policy: normalizePolicy(value.organization_policy),
    workspace_policy: normalizePolicy(value.workspace_policy),
  });
}

export async function getEffectiveEgressPolicy({ fetchImpl = fetch, workspaceId, signal }) {
  if (!workspaceId) throw new Error("EGRESS_POLICY_CONTEXT_REQUIRED");
  const result = await safeJson(await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/egress-policy`,
    { method: "GET", credentials: "same-origin", cache: "no-store", signal },
  ));
  return { ...result, data: normalizeEffectivePolicy(result.data) };
}

export async function getOrganizationSettingsContext({ fetchImpl = fetch, signal } = {}) {
  const session = await safeJson(await fetchImpl("/bff/api/session", {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  }));
  return { data: {
    organization_id: session.data.tenant_id,
    workspace_id: session.data.workspace_id,
  }, etag: session.etag };
}

export async function saveOrganizationEgressPolicy({
  fetchImpl = fetch, organizationId, etag, idempotencyKey, draft, sensitive, signal,
}) {
  return saveEgressPolicy({
    fetchImpl, scope: "organizations", scopeId: organizationId,
    etag, idempotencyKey, draft, sensitive, signal,
  });
}

export async function saveWorkspaceEgressPolicy({
  fetchImpl = fetch, workspaceId, etag, idempotencyKey, draft, sensitive, signal,
}) {
  return saveEgressPolicy({
    fetchImpl, scope: "workspaces", scopeId: workspaceId,
    etag, idempotencyKey, draft, sensitive, signal,
  });
}

async function saveEgressPolicy({
  fetchImpl, scope, scopeId, etag, idempotencyKey, draft, sensitive, signal,
}) {
  try {
    const { question_step_up_required: required = true, ...legacyDraft } = draft;
    if (typeof required !== "boolean" || (draft.mode === "deny_external" && !required)) throw new Error("EGRESS_POLICY_PAYLOAD_INVALID");
    const payload = required ? legacyDraft : { ...legacyDraft, question_step_up_required: false };
    const stepUp = await safeJson(await fetchImpl("/bff/api/session/step-up", {
      method: "POST", credentials: "same-origin",
      signal,
      headers: {
        "content-type": "application/json",
        "idempotency-key": idempotencyKey,
      },
      body: JSON.stringify({
        action_group: "organization_security_or_connector_policy_change",
        target_id: scopeId,
        password: sensitive.currentPassword,
      }),
    }));
    sensitive.currentPassword = "";
    sensitive.stepUpAuthorization = stepUp.data.step_up_authorization;
    return await safeJson(await fetchImpl(
      `/bff/api/${scope}/${encodeURIComponent(scopeId)}/egress-policy-versions`,
      {
        method: "POST", credentials: "same-origin",
        signal,
        headers: {
          "content-type": "application/json", "if-match": etag,
          "idempotency-key": idempotencyKey,
        },
        body: JSON.stringify({
          ...payload,
          step_up_authorization_id: sensitive.stepUpAuthorization,
        }),
      },
    ));
  } finally {
    sensitive.currentPassword = "";
    sensitive.stepUpAuthorization = null;
  }
}
