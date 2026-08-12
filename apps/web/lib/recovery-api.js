const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });

function sessionFailure() {
  const error = new Error("RESOURCE_UNAVAILABLE");
  error.code = "RESOURCE_UNAVAILABLE";
  return error;
}

export function parseRecoverySessionContext(payload) {
  const session = payload?.data;
  const values = [session?.user_id, session?.tenant_id, session?.workspace_id];
  if (!values.every((value) => typeof value === "string" && value.trim())) return null;
  return {
    userId: session.user_id,
    tenantId: session.tenant_id,
    workspaceId: session.workspace_id,
    membership: null
  };
}

export async function resolveRecoverySession(adapter, initializePane = (context) => context) {
  const { payload } = await adapter.getSession();
  const context = parseRecoverySessionContext(payload);
  if (!context) throw sessionFailure();
  return initializePane(context);
}

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
  getSession() {
    return request("/bff/api/session");
  },
  listBackups(workspaceId) {
    return request(`/bff/api/backups?workspace_id=${encodeURIComponent(workspaceId)}`);
  },
  createBackup(input, idempotencyKey) {
    return request("/bff/api/backups", {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  getBackup(id) {
    return request(`/bff/api/backups/${encodeURIComponent(id)}`);
  },
  previewRestore(backupId, input, idempotencyKey) {
    return request(`/bff/api/backups/${encodeURIComponent(backupId)}/restore-previews`, {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  getRestore(id) {
    return request(`/bff/api/restore-requests/${encodeURIComponent(id)}`);
  },
  executeRestore(id, input, etag, idempotencyKey) {
    return request(`/bff/api/restore-requests/${encodeURIComponent(id)}/execute`, {
      method: "POST", body: input,
      headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  },
  cancelRestore(id, etag, idempotencyKey) {
    return request(`/bff/api/restore-requests/${encodeURIComponent(id)}/cancel`, {
      method: "POST", headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  }
});
