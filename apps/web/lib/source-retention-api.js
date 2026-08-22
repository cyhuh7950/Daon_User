"use client";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const DELETION_ETAG = /^"deletion:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}:[1-9][0-9]*"$/u;
const DELETION_STATES = new Set(["grace_period", "blocked_by_hold", "awaiting_ack", "cleanup_pending", "cancelled", "purged"]);
const ITEM_STATES = new Set(["pending", "awaiting_ack", "completed"]);
const DATA_KEYS = Object.freeze(["request_id", "tenant_id", "workspace_id", "source_id", "state", "version", "requested_at", "grace_until", "source_active", "cleanup_items", "completed_references", "source_version_mutations"]);
const safe = (value) => typeof value === "string" && SAFE_ID.test(value);
const exact = (value, keys) => value && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));

const iso = (value) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u.test(value)
  && Number.isFinite(Date.parse(value));
const validItem = (value) => exact(value, ["kind", "reference_id", "state", "attempt_count", "evidence"])
  && ["original_content", "index", "preview", "cache", "known_local_copy", "sync_reference"].includes(value.kind)
  && safe(value.reference_id) && ITEM_STATES.has(value.state)
  && Number.isSafeInteger(value.attempt_count) && value.attempt_count >= 0
  && (value.evidence === null || (typeof value.evidence === "string" && value.evidence.length <= 255));

async function decode(response, expected = {}) {
  let payload;
  try { payload = await response.json(); } catch { throw new Error("DELETION_RESPONSE_INVALID"); }
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "DELETION_REQUEST_FAILED");
  const data = payload?.data;
  const etag = response.headers.get("etag");
  if (!exact(payload, ["data", "meta"]) || !exact(payload.meta, ["trace_id"]) || !safe(payload.meta.trace_id)
      || !exact(data, DATA_KEYS) || !safe(data.request_id) || !safe(data.tenant_id)
      || !safe(data.workspace_id) || !safe(data.source_id) || !DELETION_STATES.has(data.state)
      || !Number.isSafeInteger(data.version) || data.version < 1 || !iso(data.requested_at) || !iso(data.grace_until)
      || typeof data.source_active !== "boolean" || !Array.isArray(data.cleanup_items)
      || data.cleanup_items.length !== 6 || !data.cleanup_items.every(validItem)
      || !Array.isArray(data.completed_references) || !data.completed_references.every(safe)
      || !Number.isSafeInteger(data.source_version_mutations) || data.source_version_mutations < 0
      || !DELETION_ETAG.test(etag) || etag !== `"deletion:${data.request_id}:${data.version}"`
      || (expected.sourceId && data.source_id !== expected.sourceId)
      || (expected.requestId && data.request_id !== expected.requestId)) throw new Error("DELETION_RESPONSE_INVALID");
  return { data: { ...data, cleanup_items: data.cleanup_items.map((item) => ({ ...item })), completed_references: [...data.completed_references] }, etag };
}

export async function requestSourceDeletion(sourceId, { fetchImpl = fetch, signal, idempotencyKey } = {}) {
  if (!safe(sourceId) || !safe(idempotencyKey) || idempotencyKey.length < 16) throw new Error("DELETION_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/sources/${encodeURIComponent(sourceId)}/deletion-requests`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "If-Match": "*" }, body: "{}",
  });
  return decode(response, { sourceId });
}

export async function getSourceDeletionRequest(requestId, { fetchImpl = fetch, signal } = {}) {
  if (!safe(requestId)) throw new Error("DELETION_INPUT_INVALID");
  return decode(await fetchImpl(`/bff/api/deletion-requests/${encodeURIComponent(requestId)}`, { method: "GET", credentials: "same-origin", cache: "no-store", signal }), { requestId });
}

export async function cancelSourceDeletionRequest(requestId, { fetchImpl = fetch, signal, idempotencyKey, etag } = {}) {
  if (!safe(requestId) || !safe(idempotencyKey) || idempotencyKey.length < 16 || !DELETION_ETAG.test(etag)) throw new Error("DELETION_INPUT_INVALID");
  return decode(await fetchImpl(`/bff/api/deletion-requests/${encodeURIComponent(requestId)}/cancel`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "If-Match": etag }, body: "{}",
  }), { requestId });
}
