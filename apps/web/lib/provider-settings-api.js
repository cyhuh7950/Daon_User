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

export const providerSettingsApi = Object.freeze({
  listProfiles(workspaceId) {
    return request(`/bff/api/model-profiles?workspace_id=${encodeURIComponent(workspaceId)}`);
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
