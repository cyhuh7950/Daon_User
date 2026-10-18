import { getWorkspaceOperationsStatus } from "./product-workspace-api.js";

const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });

function requestError(response, payload) {
  const error = new Error(payload?.error?.code ?? "RESOURCE_UNAVAILABLE");
  error.code = payload?.error?.code ?? "RESOURCE_UNAVAILABLE";
  error.status = response.status;
  error.traceId = payload?.error?.trace_id ?? response.headers.get("x-trace-id");
  return error;
}

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

async function request(path, { method = "GET", body, headers = {}, signal } = {}) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    signal,
    headers: body === undefined ? headers : { ...JSON_HEADERS, ...headers },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({
    error: { code: "RESOURCE_UNAVAILABLE", trace_id: response.headers.get("x-trace-id") }
  }));
  if (!response.ok) {
    throw requestError(response, payload);
  }
  return { payload, etag: response.headers.get("etag") };
}

export async function issueRestoreStepUp(requestId, password, { idempotencyKey, signal } = {}) {
  if (typeof requestId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(requestId)
    || typeof password !== "string" || password.length < 12 || password.length > 1024
    || typeof idempotencyKey !== "string" || !/^[A-Za-z0-9_-]{16,128}$/u.test(idempotencyKey)) {
    throw sessionFailure();
  }
  const { payload } = await request("/bff/api/session/step-up", {
    method: "POST", signal, headers: { "Idempotency-Key": idempotencyKey },
    body: { action_group: "permanent_delete_or_restore_rollback", target_id: requestId, password, ttl_seconds: 300 }
  });
  const token = payload?.data?.step_up_authorization;
  if (typeof token !== "string" || !token.trim() || token.length > 4096 || /[\u0000-\u0020\u007f]/u.test(token)) {
    throw sessionFailure();
  }
  return token;
}

export const recoveryApi = Object.freeze({
  issueRestoreStepUp,
  getOperationsStatus(workspaceId, { signal } = {}) {
    return getWorkspaceOperationsStatus(workspaceId, { signal, fetchImpl: async (path, init) => {
      const response = await fetch(path, init);
      if (!response.ok) throw requestError(response, await response.json().catch(() => null));
      return response;
    } });
  },
  getSession({ signal } = {}) {
    return request("/bff/api/session", { signal });
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
  getRestore(id, { signal } = {}) {
    return request(`/bff/api/restore-requests/${encodeURIComponent(id)}`, { signal });
  },
  executeRestore(id, input, etag, idempotencyKey, { signal } = {}) {
    return request(`/bff/api/restore-requests/${encodeURIComponent(id)}/execute`, {
      method: "POST", body: input, signal,
      headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  },
  cancelRestore(id, etag, idempotencyKey, { signal } = {}) {
    return request(`/bff/api/restore-requests/${encodeURIComponent(id)}/cancel`, {
      method: "POST", signal, headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  }
});
