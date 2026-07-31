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

export const recoveryApi = Object.freeze({
  listBackups(workspaceId) {
    return request(`/api/v1/backups?workspace_id=${encodeURIComponent(workspaceId)}`);
  },
  createBackup(input, idempotencyKey) {
    return request("/api/v1/backups", {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  getBackup(id) {
    return request(`/api/v1/backups/${encodeURIComponent(id)}`);
  },
  previewRestore(backupId, input, idempotencyKey) {
    return request(`/api/v1/backups/${encodeURIComponent(backupId)}/restore-previews`, {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  getRestore(id) {
    return request(`/api/v1/restore-requests/${encodeURIComponent(id)}`);
  },
  executeRestore(id, input, etag, idempotencyKey) {
    return request(`/api/v1/restore-requests/${encodeURIComponent(id)}/execute`, {
      method: "POST", body: input,
      headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  },
  cancelRestore(id, etag, idempotencyKey) {
    return request(`/api/v1/restore-requests/${encodeURIComponent(id)}/cancel`, {
      method: "POST", headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  }
});
