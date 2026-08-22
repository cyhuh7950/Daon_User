const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const SAFE_IDEMPOTENCY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/u;
const STATES = new Set(["awaiting_approval", "approved", "transferring", "conflict", "reindex_requested", "failed", "cancelled"]);
const OPERATION_KEYS = Object.freeze([
  "operation_id", "tenant_id", "workspace_id", "actor_id", "target_area", "state", "version",
  "manifest_digest", "item_ids", "approved_item_ids", "completed_item_ids", "batches", "conflicts",
  "target_versions", "reindex_state", "source_mutations", "overwrite_count",
]);

function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function safeId(value) { return typeof value === "string" && SAFE_ID.test(value); }
function safeIds(value) { return Array.isArray(value) && value.length <= 1_000 && value.every(safeId) && new Set(value).size === value.length; }
function recordArray(value) { return Array.isArray(value) && value.length <= 1_000 && value.every((item) => item && typeof item === "object" && !Array.isArray(item)); }

function validOperation(value, workspaceId = null) {
  return exact(value, OPERATION_KEYS)
    && [value.operation_id, value.tenant_id, value.workspace_id, value.actor_id].every(safeId)
    && (workspaceId === null || value.workspace_id === workspaceId)
    && value.target_area === "cloud_sync" && STATES.has(value.state)
    && Number.isSafeInteger(value.version) && value.version >= 1
    && typeof value.manifest_digest === "string" && /^[0-9a-f]{64}$/u.test(value.manifest_digest)
    && safeIds(value.item_ids) && safeIds(value.approved_item_ids) && safeIds(value.completed_item_ids)
    && value.approved_item_ids.every((id) => value.item_ids.includes(id))
    && value.completed_item_ids.every((id) => value.item_ids.includes(id))
    && recordArray(value.batches) && recordArray(value.conflicts) && recordArray(value.target_versions)
    && (value.reindex_state === null || value.reindex_state === "reindex_requested")
    && value.source_mutations === 0 && value.overwrite_count === 0;
}

async function safeJson(response, fallback) {
  try { return await response.json(); } catch { throw new Error(fallback); }
}

export async function listWorkspaceSyncOperations(workspaceId, { fetchImpl = fetch, signal } = {}) {
  if (!safeId(workspaceId)) throw new Error("SYNC_SETTINGS_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/sync-operations`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await safeJson(response, "SYNC_SETTINGS_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "SYNC_SETTINGS_LIST_FAILED");
  if (!exact(payload, ["data", "meta"]) || !exact(payload.data, ["operations"])
      || !Array.isArray(payload.data.operations) || payload.data.operations.length > 200
      || !payload.data.operations.every((item) => validOperation(item, workspaceId))) {
    throw new Error("SYNC_SETTINGS_RESPONSE_INVALID");
  }
  return payload.data.operations;
}

export async function approveWorkspaceSyncOperation(
  workspaceId, operation, { approvedItemIds, stepUpAuthorizationId },
  { fetchImpl = fetch, idempotencyKey = crypto.randomUUID(), signal } = {},
) {
  if (!safeId(workspaceId) || !validOperation(operation, workspaceId)
      || operation.state !== "awaiting_approval" || !safeIds(approvedItemIds) || approvedItemIds.length === 0
      || !approvedItemIds.every((id) => operation.item_ids.includes(id))
      || !safeId(stepUpAuthorizationId) || !SAFE_IDEMPOTENCY.test(idempotencyKey)) {
    throw new Error("SYNC_SETTINGS_INPUT_INVALID");
  }
  const response = await fetchImpl(`/bff/api/sync-operations/${encodeURIComponent(operation.operation_id)}/approve`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: {
      "Content-Type": "application/json", "If-Match": `"sync:${operation.operation_id}:${operation.version}"`,
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ approved_item_ids: approvedItemIds, step_up_authorization_id: stepUpAuthorizationId }),
  });
  const payload = await safeJson(response, "SYNC_SETTINGS_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "SYNC_SETTINGS_APPROVAL_FAILED");
  if (!exact(payload, ["data", "meta"]) || !validOperation(payload.data, workspaceId)) throw new Error("SYNC_SETTINGS_RESPONSE_INVALID");
  return payload.data;
}
