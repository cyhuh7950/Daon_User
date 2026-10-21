"use client";
import { isNotebookModelSettings, isNotebookModelSelection } from "@daon-user/ui/product-workspace-model";
import { parseImageRegion } from "@daon-user/ui/product-workspace-model";
import { parseSheetCell } from "./sheet-cell-locator.js";
import { parseDocxBlock } from "./docx-block-locator.js";
import { parsePptxBlock } from "./pptx-block-locator.js";
import { parseAudioSegment } from "./audio-segment-locator.js";

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
  "password_change_required", "is_system_admin",
]);

function exact(value, required, optional = []) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const keys = Object.keys(value);
  return required.every((key) => keys.includes(key))
    && keys.every((key) => required.includes(key) || optional.includes(key));
}

function safeId(value) { return typeof value === "string" && SAFE_ID.test(value); }
function safeOptionalLoginId(value) { return value === null || value === undefined || (typeof value === "string" && value.length >= 1 && value.length <= 128); }
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
  return exact(value, SESSION_KEYS, ["login_id"])
    && ["user_id", "tenant_id", "workspace_id", "session_id", "device_id"].every((key) => safeId(value[key]))
    && safeOptionalLoginId(value.login_id)
    && value.client_kind === "web" && value.delivery === "same_origin_secure_cookie"
    && timestamp(value.expires_at)
    && Array.isArray(value.recovery_operations)
    && value.recovery_operations.length <= 32
    && new Set(value.recovery_operations).size === value.recovery_operations.length
    && value.recovery_operations.every(safeId)
    && typeof value.password_change_required === "boolean"
    && typeof value.is_system_admin === "boolean";
}

function validCitation(value) {
  return exact(value, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page", "origin", "context_item_id", "locator"])
    && ["citation_id", "source_id", "source_version_id", "evidence_span_id", "context_item_id"].every((key) => safeId(value[key]))
    && Number.isSafeInteger(value.page) && value.page >= 1
    && ["raw_source", "daon_knowledge"].includes(value.origin)
    && exact(value.locator, ["kind", "value"])
    && ["page", "section", "image_region", "sheet_cell", "docx_block", "pptx_block", "audio_segment"].includes(value.locator.kind)
    && typeof value.locator.value === "string" && value.locator.value.length >= 1 && value.locator.value.length <= 255
    && (value.locator.kind !== "page" || value.locator.value === String(value.page))
    && (value.locator.kind !== "image_region" || parseImageRegion(value.locator.value) !== null)
    && (value.locator.kind !== "sheet_cell" || parseSheetCell(value.locator.value) !== null)
    && (value.locator.kind !== "docx_block" || parseDocxBlock(value.locator.value) !== null)
    && (value.locator.kind !== "pptx_block" || parsePptxBlock(value.locator.value) !== null)
    && (value.locator.kind !== "audio_segment" || parseAudioSegment(value.locator.value) !== null);
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
    && value.conversation.answer.citations.every(citation => validCitation(citation)
      && (!["sheet_cell", "docx_block", "pptx_block", "audio_segment"].includes(citation.locator.kind) || citation.origin !== "raw_source"
        || (citation.context_item_id === citation.source_id && Array.isArray(value.sources)
          && value.sources.some(source => source?.source_id === citation.source_id
            && source?.source_version_id === citation.source_version_id))))
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
      && ["grace_period", "blocked_by_hold", "awaiting_ack", "cleanup_pending", "failed", "purged"].includes(item.state)
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

export async function getNotebookContext(workspaceId, notebookId, { fetchImpl = fetch, signal, runId, studioJobId } = {}) {
  if (!safeId(notebookId)) throw new Error("NOTEBOOK_INPUT_INVALID");
  const selected = runId !== undefined ? runId : studioJobId;
  if ((runId !== undefined && studioJobId !== undefined)
    || (selected !== undefined && (!safeId(selected) || /\s/u.test(selected)))) throw new Error("NOTEBOOK_INPUT_INVALID");
  const query = selected === undefined ? "" : `?${runId !== undefined ? "run_id" : "studio_job_id"}=${encodeURIComponent(selected)}`;
  const response = await fetchImpl(`${basePath(workspaceId)}/${encodeURIComponent(notebookId)}/context${query}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  let payload;
  try { payload = await response.json(); } catch { throw new Error("NOTEBOOK_CONTEXT_INVALID"); }
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "NOTEBOOK_UNAVAILABLE");
  if (!exact(payload, ["data", "meta"]) || !validContext(payload.data, notebookId)
      || !validMeta(payload.meta)) throw new Error("NOTEBOOK_CONTEXT_INVALID");
  if (runId !== undefined && payload.data.conversation?.answer?.run_id !== runId) throw new Error("NOTEBOOK_CONTEXT_INVALID");
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

function validConversationTurn(value) {
  const response = value?.response;
  const states = ['pending','awaiting_authorization','completed','failed','uncertain','unavailable','no_knowledge','no_relevant_evidence'];
  const safeModel = value?.model === null || (exact(value?.model,['provider_code','model_id'])
    && ['provider_code','model_id'].every(key => typeof value.model[key] === 'string'
      && value.model[key].length >= 1 && value.model[key].length <= 255 && !/[\x00-\x20\x7f]/u.test(value.model[key])));
  const safeResponse = response === null || (exact(response,['kind','text','insufficient','next_actions','citations'])
    && ['answer','system'].includes(response.kind) && typeof response.text === 'string' && response.text.length >= 1 && response.text.length <= 8000
    && typeof response.insufficient === 'boolean' && Array.isArray(response.next_actions) && response.next_actions.length <= 8
    && response.next_actions.every(item => typeof item === 'string' && item.length <= 255)
    && Array.isArray(response.citations) && response.citations.length <= 10 && response.citations.every(validCitation)
    && (response.kind !== 'system' || (response.citations.length === 0 && response.insufficient === true)));
  return exact(value,['turn_id','question','response','state','submitted_at','model','client_request_id'])
    && (value.client_request_id === null || (safeId(value.client_request_id) && value.client_request_id.length >= 8))
    && safeId(value.turn_id) && typeof value.question === 'string' && value.question.length >= 1 && value.question.length <= 32768
    && states.includes(value.state) && timestamp(value.submitted_at) && safeModel && safeResponse
    && (value.state === 'completed' ? response?.kind === 'answer' : value.model === null)
    && (!['no_knowledge','no_relevant_evidence'].includes(value.state)
      || response?.next_actions.length>=1&&response.next_actions.length<=4
        &&response.next_actions.every(item=>item.trim().length>=1&&item.length<=120))
    && (value.state === 'pending' ? response === null
      : value.state === 'completed' ? response?.kind === 'answer'
      : response?.kind === 'system' && response.insufficient === true && response.citations.length === 0);
}

export async function listNotebookConversationTurns(workspaceId, notebookId, { fetchImpl=fetch, signal, before, limit=50 }={}) {
  const cursor = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,2048}$/u.test(value);
  if (!safeId(notebookId) || !Number.isSafeInteger(limit) || limit < 1 || limit > 100
      || (before !== undefined && before !== null && !cursor(before))) throw new Error('NOTEBOOK_INPUT_INVALID');
  const query = new URLSearchParams({ limit:String(limit) });
  if (before !== undefined && before !== null) query.set('before',before);
  const response = await fetchImpl(`${basePath(workspaceId)}/${encodeURIComponent(notebookId)}/conversation-turns?${query}`, {
    method:'GET',credentials:'same-origin',cache:'no-store',signal,
  });
  let payload;
  try { payload=await response.json(); } catch { throw new Error('NOTEBOOK_CONVERSATION_INVALID'); }
  if (!response.ok) throw new Error(typeof payload?.error?.code === 'string' ? payload.error.code : 'NOTEBOOK_UNAVAILABLE');
  if (!exact(payload,['data','meta']) || !exact(payload.meta,['trace_id','workspace_id','notebook_id'])
      || !safeId(payload.meta.trace_id) || payload.meta.workspace_id !== workspaceId || payload.meta.notebook_id !== notebookId
      || !exact(payload.data,['items','next_cursor']) || !Array.isArray(payload.data.items) || payload.data.items.length > limit
      || new Set(payload.data.items.map(item => item?.turn_id)).size !== payload.data.items.length
      || new Set(payload.data.items.map(item=>item?.client_request_id).filter(id=>id!==null)).size !== payload.data.items.filter(item=>item?.client_request_id!==null).length
      || !payload.data.items.every(validConversationTurn) || !(payload.data.next_cursor === null || cursor(payload.data.next_cursor))
      || (before !== undefined && before !== null && payload.data.next_cursor === before)) {
    throw new Error('NOTEBOOK_CONVERSATION_INVALID');
  }
  return payload.data;
}


async function decodeModelSettings(response,notebookId,saved,signal){
  let body;try{body=await response.json();}catch{throw new Error("MODEL_SETTINGS_RESPONSE_INVALID");}
  signal?.throwIfAborted();
  if(!response.ok)throw new Error(typeof body?.error?.code==="string"?body.error.code:"NOTEBOOK_MODEL_SETTINGS_UNAVAILABLE");
  if(!exact(body,["data","meta"])||!exact(body.meta,["trace_id"])||!safeId(body.meta.trace_id)
    ||!isNotebookModelSettings(body.data,{saved})||body.data.notebook_id!==notebookId)throw new Error("MODEL_SETTINGS_RESPONSE_INVALID");
  return body.data;
}
export async function getNotebookModelSettings(workspaceId,notebookId,{fetchImpl=fetch,signal}={}){
  if(!safeId(notebookId))throw new Error("NOTEBOOK_MODEL_SETTINGS_INVALID");
  signal?.throwIfAborted();
  const response=await fetchImpl(`${basePath(workspaceId)}/${encodeURIComponent(notebookId)}/model-settings`,
    {method:"GET",credentials:"same-origin",cache:"no-store",signal});
  return decodeModelSettings(response,notebookId,false,signal);
}
export async function saveNotebookModelSettings(workspaceId,notebookId,input,{fetchImpl=fetch,signal,idempotencyKey}={}){
  if(!safeId(notebookId)||!safeId(idempotencyKey)||!exact(input,["connection_id","model_id","reasoning_effort","expected_version"])
    ||!isNotebookModelSelection({connection_id:input.connection_id,model_id:input.model_id,reasoning_effort:input.reasoning_effort})
    ||!Number.isSafeInteger(input.expected_version)||input.expected_version<0)throw new Error("NOTEBOOK_MODEL_SETTINGS_INVALID");
  signal?.throwIfAborted();
  const response=await fetchImpl(`${basePath(workspaceId)}/${encodeURIComponent(notebookId)}/model-settings`,
    {method:"PATCH",credentials:"same-origin",cache:"no-store",signal,headers:{"Content-Type":"application/json","Idempotency-Key":idempotencyKey},body:JSON.stringify(input)});
  return decodeModelSettings(response,notebookId,true,signal);
}
