const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });

async function request(path, { method = "GET", body, headers = {} } = {}) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? headers : { ...JSON_HEADERS, ...headers },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({
    error: { code: "RESOURCE_UNAVAILABLE", trace_id: response.headers.get("x-trace-id") }
  }));
  if (!response.ok) {
    const error = new Error(payload?.error?.code ?? "RESOURCE_UNAVAILABLE");
    error.code = payload?.error?.code ?? "RESOURCE_UNAVAILABLE";
    error.traceId = payload?.error?.trace_id ?? response.headers.get("x-trace-id");
    throw error;
  }
  return { payload, etag: response.headers.get("etag") };
}

function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("|") === [...keys].sort().join("|");
}

function providerConnectionResult(payload, expectedProvider) {
  const data = payload?.data;
  if (
    !exactKeys(payload, ["data", "meta"])
    || !exactKeys(data, ["provider_code", "status", "checked_at"])
    || data.provider_code !== expectedProvider
    || !new Set(["ready", "unconfigured", "unavailable"]).has(data.status)
    || typeof data.checked_at !== "string"
    || !data.checked_at
  ) {
    throw new Error("PROVIDER_CONNECTION_RESPONSE_INVALID");
  }
  return { providerCode: data.provider_code, status: data.status, checkedAt: data.checked_at };
}

export const providerSettingsApi = Object.freeze({
  getSession() {
    return request("/bff/api/session");
  },
  listProfiles(workspaceId) {
    return request(`/bff/api/model-profiles?workspace_id=${encodeURIComponent(workspaceId)}`);
  },
  async checkConnection(workspaceId, providerCode) {
    const result = await request(`/bff/api/model-profiles/${encodeURIComponent(providerCode)}/connection-check?workspace_id=${encodeURIComponent(workspaceId)}`);
    return providerConnectionResult(result.payload, providerCode);
  },
  saveProfile(input, idempotencyKey) {
    return request("/bff/api/model-profiles", {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  listDeployments(workspaceId) {
    return request(`/bff/api/model-deployments?workspace_id=${encodeURIComponent(workspaceId)}`);
  },
  saveDeployment(input, idempotencyKey) {
    return request("/bff/api/model-deployments", {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  getModelPolicy(workspaceId) {
    return request(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/model-policy`);
  },
  saveModelPolicy(workspaceId, input, etag, idempotencyKey) {
    return request(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/model-policy`, {
      method: "PATCH", body: input,
      headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  }
});
