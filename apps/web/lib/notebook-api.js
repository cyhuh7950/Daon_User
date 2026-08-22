"use client";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const VIEW_KEYS = Object.freeze(["notebook_id", "title", "source_count", "output_count", "updated_at", "status", "etag"]);
const META_KEYS = Object.freeze(["trace_id", "workspace_id", "replayed"]);
const STATUSES = Object.freeze(["empty", "active", "attention"]);
const DELETION_STATUSES = Object.freeze(["accepted", "deleting", "completed", "failed"]);
const CONTEXT_KEYS = Object.freeze([
  "notebook_id", "sources", "knowledge_context_ids", "conversation_thread_ids",
  "studio_output_ids", "output_version_ids", "generation_settings_ids",
  "source_deletion_requests",
  "conversation",
]);
const SESSION_KEYS = Object.freeze([
  "user_id", "tenant_id", "workspace_id", "session_id", "device_id",
  "client_kind", "delivery", "expires_at", "recovery_operations",
]);

function exact(value, required, optional = []) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const keys = Object.keys(value);
  return required.every((key) => keys.includes(key))
    && keys.every((key) => required.includes(key) || optional.includes(key));
}

function safeId(value) { return typeof value === "string" && SAFE_ID.test(value); }
function timestamp(value) {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/u.test(value)
    && !Number.isNaN(Date.parse(value));
}

function validEtag(value) {
  return typeof value === "string" && /^"notebook:[1-9][0-9]*"$/u.test(value);
}

function validDeletion(value) {
  return exact(value, ["request_id", "notebook_id", "status"], ["current_step", "attempts", "safe_error_code", "requested_at", "completed_at"])
    && safeId(value.request_id) && safeId(value.notebook_id) && DELETION_STATUSES.includes(value.status);
}

function validView(value) {
  return exact(value, VIEW_KEYS)
    && safeId(value.notebook_id)
    && typeof value.title === "string" && value.title.length >= 1 && value.title.length <= 120
    && Number.isSafeInteger(value.source_count) && value.source_count >= 0
    && Number.isSafeInteger(value.output_count) && value.output_count >= 0
    && timestamp(value.updated_at) && STATUSES.includes(value.status) && validEtag(value.etag);
}

function validMeta(value) {
  return exact(value, ["trace_id", "workspace_id"], ["replayed"])
    && safeId(value.trace_id) && safeId(value.workspace_id)
    && (value.replayed === undefined || typeof value.replayed === "boolean");
}

function validSession(value) {
  return exact(value, SESSION_KEYS)
    && ["user_id", "tenant_id", "workspace_id", "session_id", "device_id"].every((key) => safeId(value[key]))
    && value.client_kind === "web" && value.delivery === "same_origin_secure_cookie"
    && timestamp(value.expires_at)
    && Array.isArray(value.recovery_operations)
    && value.recovery_operations.length <= 32
    && new Set(value.recovery_operations).size === value.recovery_operations.length
    && value.recovery_operations.every(safeId);
}

function validCitation(value) {
  return exact(value, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page", "origin", "context_item_id", "locator"])
    && ["citation_id", "source_id", "source_version_id", "evidence_span_id", "context_item_id"].every((key) => safeId(value[key]))
    && Number.isSafeInteger(value.page) && value.page >= 1
    && ["raw_source", "daon_knowledge"].includes(value.origin)
    && exact(value.locator, ["kind", "value"])
    && ["page", "section"].includes(value.locator.kind)
    && typeof value.locator.value === "string" && value.locator.value.length >= 1 && value.locator.value.length <= 255
    && (value.locator.kind !== "page" || value.locator.value === String(value.page));
}

function validContext(value, notebookId) {
  const idList = (items) => Array.isArray(items) && items.length <= 1_000
    && new Set(items).size === items.length && items.every(safeId);
  const conversationValid = value?.conversation === null || (
    Array.isArray(value?.conversation_thread_ids)
    && value.conversation_thread_ids.length > 0
    && exact(value?.conversation, ["conversation_thread_id", "answer"])
    && value.conversation.conversation_thread_id === value.conversation_thread_ids[0]
    && exact(value.conversation.answer, ["run_id", "run_result_id", "answer", "insufficient", "citations"])
    && safeId(value.conversation.answer.run_id) && safeId(value.conversation.answer.run_result_id)
    && typeof value.conversation.answer.answer === "string"
    && value.conversation.answer.answer.length >= 1 && value.conversation.answer.answer.length <= 8_000
    && typeof value.conversation.answer.insufficient === "boolean"
    && Array.isArray(value.conversation.answer.citations)
    && value.conversation.answer.citations.length <= 20
    && value.conversation.answer.citations.every(validCitation)
  );
  return exact(value, CONTEXT_KEYS) && value.notebook_id === notebookId
    && Array.isArray(value.sources) && value.sources.length <= 1_000
    && value.sources.every((item) => exact(item, ["source_id", "source_version_id"])
      && safeId(item.source_id) && safeId(item.source_version_id))
    && idList(value.knowledge_context_ids) && idList(value.conversation_thread_ids)
    && idList(value.studio_output_ids) && idList(value.output_version_ids)
    && idList(value.generation_settings_ids) && conversationValid
    && Array.isArray(value.source_deletion_requests) && value.source_deletion_requests.length <= 1_000
    && value.source_deletion_requests.every((item) => exact(item, ["request_id", "source_id", "state", "version", "grace_until", "legal_hold_active"])
      && safeId(item.request_id) && safeId(item.source_id)
      && ["grace_period", "blocked_by_hold", "awaiting_ack", "cleanup_pending"].includes(item.state)
      && Number.isSafeInteger(item.version) && item.version >= 1 && timestamp(item.grace_until)
      && typeof item.legal_hold_active === "boolean")
    && (value.conversation === null || value.conversation_thread_ids.length > 0);
}

async function decode(response, { list = false, requireEtag = false } = {}) {
  let payload;
  try { payload = await response.json(); } catch { throw new Error("NOTEBOOK_RESPONSE_INVALID"); }
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "NOTEBOOK_UNAVAILABLE");
  const dataValid = list
    ? Array.isArray(payload?.data) && payload.data.length <= 500 && payload.data.every(validView)
    : validView(payload?.data);
  const etag = response.headers.get("etag");
  if (!exact(payload, ["data", "meta"]) || !dataValid || !validMeta(payload.meta)
      || (requireEtag && !validEtag(etag))) throw new Error("NOTEBOOK_RESPONSE_INVALID");
  return { data: payload.data, meta: payload.meta, etag };
}

function basePath(workspaceId) {
  if (!safeId(workspaceId)) throw new Error("NOTEBOOK_INPUT_INVALID");
  return `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/notebooks`;
}

function writeOptions(method, body, idempotencyKey, etag) {
  if (!safeId(idempotencyKey) || idempotencyKey.length < 16 || (etag !== undefined && !validEtag(etag))) {
    throw new Error("NOTEBOOK_INPUT_INVALID");
  }
  const headers = { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey };
  if (etag !== undefined) headers["If-Match"] = etag;
  return { method, credentials: "same-origin", cache: "no-store", headers, body: JSON.stringify(body) };
}

export async function listNotebooks(workspaceId, { fetchImpl = fetch, signal } = {}) {
  const response = await fetchImpl(basePath(workspaceId), { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  return decode(response, { list: true });
}

export async function getCurrentNotebookSession({ fetchImpl = fetch, signal } = {}) {
  const response = await fetchImpl("/bff/api/session", {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  let payload;
  try { payload = await response.json(); } catch { throw new Error("SESSION_RESPONSE_INVALID"); }
  if (response.status === 401) throw new Error("AUTHENTICATION_REQUIRED");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "SESSION_UNAVAILABLE");
  if (!exact(payload, ["data", "meta"]) || !validSession(payload.data) || !exact(payload.meta, ["trace_id"])
      || !safeId(payload.meta.trace_id)) throw new Error("SESSION_RESPONSE_INVALID");
  return payload.data;
}

export async function getNotebook(workspaceId, notebookId, { fetchImpl = fetch, signal } = {}) {
  if (!safeId(notebookId)) throw new Error("NOTEBOOK_INPUT_INVALID");
  const response = await fetchImpl(`${basePath(workspaceId)}/${encodeURIComponent(notebookId)}`, { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  return decode(response, { requireEtag: true });
}

export async function getNotebookContext(workspaceId, notebookId, { fetchImpl = fetch, signal } = {}) {
  if (!safeId(notebookId)) throw new Error("NOTEBOOK_INPUT_INVALID");
  const response = await fetchImpl(`${basePath(workspaceId)}/${encodeURIComponent(notebookId)}/context`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  let payload;
  try { payload = await response.json(); } catch { throw new Error("NOTEBOOK_CONTEXT_INVALID"); }
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "NOTEBOOK_UNAVAILABLE");
  if (!exact(payload, ["data", "meta"]) || !validContext(payload.data, notebookId)
      || !validMeta(payload.meta)) throw new Error("NOTEBOOK_CONTEXT_INVALID");
  const bindingEtag = response.headers.get("etag");
  if (typeof bindingEtag !== "string" || !/^"notebook-binding:[1-9][0-9]*"$/u.test(bindingEtag)) throw new Error("NOTEBOOK_CONTEXT_INVALID");
  Object.defineProperty(payload.data, "bindingEtag", { enumerable: false, value: bindingEtag });
  return payload;
}

export async function createNotebook(workspaceId, input, { fetchImpl = fetch, signal, idempotencyKey } = {}) {
  if (!exact(input, ["title"], ["description"]) || typeof input.title !== "string"
      || (input.description !== undefined && input.description !== null && typeof input.description !== "string")) throw new Error("NOTEBOOK_INPUT_INVALID");
  const response = await fetchImpl(basePath(workspaceId), { ...writeOptions("POST", input, idempotencyKey), signal });
  return decode(response, { requireEtag: true });
}

export async function updateNotebookTitle(workspaceId, notebookId, title, { fetchImpl = fetch, signal, idempotencyKey, etag } = {}) {
  if (!safeId(notebookId) || typeof title !== "string" || typeof etag !== "string") throw new Error("NOTEBOOK_INPUT_INVALID");
  const response = await fetchImpl(`${basePath(workspaceId)}/${encodeURIComponent(notebookId)}`, { ...writeOptions("PATCH", { title }, idempotencyKey, etag), signal });
  return decode(response, { requireEtag: true });
}

export async function requestNotebookDeletion(workspaceId, notebookId, title, { fetchImpl = fetch, signal, idempotencyKey, etag } = {}) {
  if (!safeId(notebookId) || typeof title !== "string" || !title.trim() || typeof etag !== "string" || !safeId(idempotencyKey) || idempotencyKey.length < 16 || !validEtag(etag)) throw new Error("NOTEBOOK_INPUT_INVALID");
  const response = await fetchImpl(`${basePath(workspaceId)}/${encodeURIComponent(notebookId)}`, {
    method: "DELETE", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "If-Match": etag },
    body: JSON.stringify({ title_confirmation: title }),
  });
  let payload;
  try { payload = await response.json(); } catch { throw new Error("NOTEBOOK_RESPONSE_INVALID"); }
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "NOTEBOOK_UNAVAILABLE");
  if (!exact(payload, ["data", "meta"]) || !exact(payload.data, ["deletion_request_id", "status"])
      || !safeId(payload.data.deletion_request_id) || !DELETION_STATUSES.includes(payload.data.status)) throw new Error("NOTEBOOK_RESPONSE_INVALID");
  return payload;
}

export async function getNotebookDeletion(workspaceId, notebookId, requestId, { fetchImpl = fetch, signal } = {}) {
  if (!safeId(notebookId) || !safeId(requestId)) throw new Error("NOTEBOOK_INPUT_INVALID");
  const response = await fetchImpl(`${basePath(workspaceId)}/${encodeURIComponent(notebookId)}/deletion-requests/${encodeURIComponent(requestId)}`, { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  let payload;
  try { payload = await response.json(); } catch { throw new Error("NOTEBOOK_RESPONSE_INVALID"); }
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "NOTEBOOK_UNAVAILABLE");
  if (!exact(payload, ["data", "meta"]) || !validDeletion(payload.data)) throw new Error("NOTEBOOK_RESPONSE_INVALID");
  return payload;
}
