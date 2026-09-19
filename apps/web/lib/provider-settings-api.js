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
  getSession() {
    return request("/bff/api/session");
  },
  listConnections() {
    return request("/bff/api/admin/provider-connections");
  },
  listUserCredentials() {
    return request("/bff/api/provider-credentials");
  },
  replaceUserCredential(connectionId, input) {
    return request(`/bff/api/provider-credentials/${encodeURIComponent(connectionId)}`, {
      method: "PUT", body: input,
    });
  },
  deleteUserCredential(connectionId, input) {
    return request(`/bff/api/provider-credentials/${encodeURIComponent(connectionId)}`, {
      method: "DELETE", body: input,
    });
  },
  createConnection(input, idempotencyKey) {
    return request("/bff/api/admin/provider-connections", {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  updateConnection(connectionId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-connections/${encodeURIComponent(connectionId)}`, {
      method: "PUT", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  replaceCredential(connectionId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-connections/${encodeURIComponent(connectionId)}/credential`, {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  deleteCredential(connectionId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-connections/${encodeURIComponent(connectionId)}`, {
      method: "DELETE", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  refreshCatalog(connectionId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-catalog/${encodeURIComponent(connectionId)}/refresh`, {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  correctCapabilities(connectionId, modelId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-models/${encodeURIComponent(connectionId)}/${encodeURIComponent(modelId)}/capabilities`, {
      method: "PATCH", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  getModelDefaults(workspaceId) {
    return request(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/model-defaults`);
  },
  saveModelDefault(workspaceId, input, etag, idempotencyKey) {
    return request(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/model-defaults`, {
      method: "PATCH",
      body: input,
      headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  }
});
