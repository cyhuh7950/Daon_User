async function safeJson(response) {
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) throw new Error("UPSTREAM_FAILURE");
  const value = await response.json();
  if (!response.ok) throw new Error(value?.error?.code || "UPSTREAM_FAILURE");
  return { data: value.data, etag: response.headers.get("etag") };
}

export async function getEffectiveEgressPolicy({ fetchImpl = fetch, workspaceId }) {
  if (!workspaceId) throw new Error("EGRESS_POLICY_CONTEXT_REQUIRED");
  return safeJson(await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/egress-policy`,
    { method: "GET", credentials: "same-origin", cache: "no-store" },
  ));
}

export async function getOrganizationSettingsContext({ fetchImpl = fetch } = {}) {
  const session = await safeJson(await fetchImpl("/bff/api/session", {
    method: "GET", credentials: "same-origin", cache: "no-store",
  }));
  return { data: {
    organization_id: session.data.tenant_id,
    workspace_id: session.data.workspace_id,
  }, etag: session.etag };
}

export async function saveOrganizationEgressPolicy({
  fetchImpl = fetch, organizationId, etag, idempotencyKey, draft, sensitive,
}) {
  try {
    const stepUp = await safeJson(await fetchImpl("/bff/api/session/step-up", {
      method: "POST", credentials: "same-origin",
      headers: {
        "content-type": "application/json",
        "idempotency-key": idempotencyKey,
      },
      body: JSON.stringify({
        action_group: "organization_security_or_connector_policy_change",
        target_id: organizationId,
        password: sensitive.currentPassword,
      }),
    }));
    sensitive.currentPassword = "";
    sensitive.stepUpAuthorization = stepUp.data.step_up_authorization;
    return await safeJson(await fetchImpl(
      `/bff/api/organizations/${encodeURIComponent(organizationId)}/egress-policy-versions`,
      {
        method: "POST", credentials: "same-origin",
        headers: {
          "content-type": "application/json", "if-match": etag,
          "idempotency-key": idempotencyKey,
        },
        body: JSON.stringify({
          ...draft,
          step_up_authorization_id: sensitive.stepUpAuthorization,
        }),
      },
    ));
  } finally {
    sensitive.currentPassword = "";
    sensitive.stepUpAuthorization = null;
  }
}
