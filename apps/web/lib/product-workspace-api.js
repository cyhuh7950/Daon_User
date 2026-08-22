"use client";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const SAFE_FILENAME = /^[^/\\\u0000-\u001f]{1,255}$/u;
const URL_SEPARATOR = String.fromCharCode(58, 47, 47);
const INTERNAL_VALUE = new RegExp(
  `(?:https?${URL_SEPARATOR}|local${"host"}|127\\.0\\.0\\.1|password|authorization|credential)`,
  "iu",
);
const SOURCE_KEYS = Object.freeze([
  "source_id", "source_version_id", "filename", "source_state", "processing_state", "job_state"
]);
const SOURCE_STATES = Object.freeze([
  "registered", "security_check", "processing", "indexing", "ready", "unavailable", "waiting_model",
  "partial_understanding", "needs_review", "failed", "expired", "disabled", "deleting", "deleted",
]);
const CONNECTOR_STATUSES = Object.freeze(["connected", "disconnected", "unavailable"]);
const CONNECTOR_KINDS = Object.freeze(["mcp", "daon_approved_knowledge"]);
const CONNECTOR_KEYS = Object.freeze([
  "connector_id", "kind", "name", "status", "source_count", "endpoint_label", "last_checked_at", "error_code",
]);
const KNOWLEDGE_PACKAGE_KEYS = Object.freeze([
  "package_id", "producer", "producer_version", "knowledge_registration_id",
  "output_version_id", "authority", "registration_state", "review_state",
  "digest_sha256", "byte_size", "content_type", "effective_at", "expires_at",
]);
const KNOWLEDGE_PRODUCERS = Object.freeze(["daon2", "daon2_5", "daon3"]);
const CITATION_KEYS = Object.freeze([
  "citation_id", "source_id", "source_version_id", "evidence_span_id", "page"
]);
const OUTPUT_KEYS = Object.freeze([
  "studio_output_id", "output_version_id", "output_type", "title", "purpose", "status",
  "content", "run_id", "run_result_id", "citations"
]);
const OPERATIONS_COMPONENT_IDS = Object.freeze(["provider", "api", "storage", "sync", "queue"]);
const OPERATIONS_COMPONENT_KEYS = Object.freeze([
  "component_id", "status", "safe_code", "pending_count", "recovery_action",
]);
const OUTPUT_SETTING_TYPES = Object.freeze([
  "evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "knowledge_graph", "business_draft",
  "slides", "infographic", "flashcards", "quiz", "audio", "video",
]);
const OUTPUT_SETTING_FORMATS = Object.freeze({
  evidence_report: Object.freeze(["pdf", "docx"]),
  compliance_checklist: Object.freeze(["xlsx", "csv", "pdf"]),
  comparison_table: Object.freeze(["xlsx", "csv", "pdf"]),
  knowledge_graph: Object.freeze(["json", "svg", "png"]),
  knowledge_map: Object.freeze(["json", "svg", "png", "pdf"]),
  business_draft: Object.freeze(["docx", "pdf"]),
  slides: Object.freeze(["pdf", "json"]),
  infographic: Object.freeze(["svg", "png", "pdf"]),
  flashcards: Object.freeze(["json", "csv", "pdf"]),
  quiz: Object.freeze(["json", "csv", "pdf"]),
  audio: Object.freeze(["json"]),
  video: Object.freeze(["json"]),
});

function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exact(value, keys) {
  if (!record(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function safeId(value) {
  return typeof value === "string" && SAFE_ID.test(value);
}

function safeIdempotencyKey(value) {
  return typeof value === "string"
    && value.length >= 16
    && value.length <= 128
    && SAFE_ID.test(value);
}

function requiredWorkspace(value, code) {
  if (!safeId(value)) throw new Error(code);
  return value;
}

function safeText(value, minimum, maximum) {
  return typeof value === "string" && value.length >= minimum && value.length <= maximum && !INTERNAL_VALUE.test(value);
}

function validMeta(meta, workspaceId, { replay = false } = {}) {
  const keys = replay ? ["trace_id", "workspace_id", "replayed"] : ["trace_id", "workspace_id"];
  return exact(meta, keys) && safeId(meta.trace_id) && meta.workspace_id === workspaceId
    && (!replay || typeof meta.replayed === "boolean");
}

function validCitation(value) {
  return exact(value, CITATION_KEYS)
    && safeId(value.citation_id) && safeId(value.source_id) && safeId(value.source_version_id)
    && safeId(value.evidence_span_id) && Number.isSafeInteger(value.page) && value.page >= 1;
}

function validOutput(value) {
  return exact(value, OUTPUT_KEYS)
    && safeId(value.studio_output_id) && safeId(value.output_version_id)
    && STUDIO_OUTPUT_TYPES.has(value.output_type) && ["draft", "generating", "in_review", "approved", "delivered", "revision_requested", "failed", "unavailable"].includes(value.status)
    && safeText(value.title, 1, 200) && safeText(value.purpose, 1, 500)
    && ((record(value.content) && JSON.stringify(value.content).length <= 20_000) || safeText(value.content, 1, 20_000))
    && safeId(value.run_id) && safeId(value.run_result_id)
    && Array.isArray(value.citations) && value.citations.length >= 1
    && value.citations.length <= 20 && value.citations.every(validCitation);
}

async function json(response, fallback) {
  try {
    return await response.json();
  } catch {
    throw new Error(fallback);
  }
}

function safeResponseError(payload, fallback) {
  const error = new Error(typeof payload?.error?.code === "string" ? payload.error.code : fallback);
  Object.defineProperty(error, "retryable", {
    configurable: false, enumerable: false, value: payload?.error?.retryable === true, writable: false,
  });
  return error;
}

export async function listWorkspaceSources(workspaceId, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "SOURCE_LIST_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "SOURCE_LIST_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/sources?notebook_id=${encodeURIComponent(notebook)}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "SOURCE_LIST_RESPONSE_INVALID");
  if (!response.ok) throw safeResponseError(payload, "SOURCE_LIST_FAILED");
  const valid = exact(payload, ["data", "meta"])
    && exact(payload.data, ["sources"])
    && Array.isArray(payload.data.sources)
    && payload.data.sources.length <= 1_000
    && validMeta(payload.meta, workspace)
    && payload.data.sources.every((source) => exact(source, SOURCE_KEYS)
      && safeId(source.source_id) && safeId(source.source_version_id)
      && typeof source.filename === "string" && SAFE_FILENAME.test(source.filename)
      && SOURCE_STATES.includes(source.source_state)
      && typeof source.processing_state === "string" && typeof source.job_state === "string");
  if (!valid) throw new Error("SOURCE_LIST_RESPONSE_INVALID");
  return payload.data.sources;
}

function validConnector(value) {
  return exact(value, CONNECTOR_KEYS) && safeId(value.connector_id)
    && CONNECTOR_KINDS.includes(value.kind) && safeText(value.name, 1, 160)
    && CONNECTOR_STATUSES.includes(value.status) && Number.isSafeInteger(value.source_count)
    && value.source_count >= 0 && value.source_count <= 100_000
    && safeText(value.endpoint_label, 1, 160)
    && (value.last_checked_at === null || safeText(value.last_checked_at, 1, 64))
    && (value.error_code === null || safeText(value.error_code, 1, 128));
}

async function connectorRequest(workspaceId, path, options = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  const requestFetch = options.fetchImpl ?? fetch;
  const response = await requestFetch(path, options.init);
  const payload = await json(response, "CONNECTOR_RESPONSE_INVALID");
  if (!response.ok) throw safeResponseError(payload, "CONNECTOR_REQUEST_FAILED");
  if (!exact(payload, ["data", "meta"]) || !validMeta(payload.meta, workspace)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return payload.data;
}

export async function listWorkspaceConnectors(workspaceId, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "CONNECTOR_RESPONSE_INVALID");
  if (!response.ok) throw safeResponseError(payload, "CONNECTOR_LIST_FAILED");
  if (!exact(payload, ["data", "meta"]) || !exact(payload.data, ["connectors"])
      || !Array.isArray(payload.data.connectors) || !payload.data.connectors.every(validConnector)
      || !validMeta(payload.meta, workspace)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return payload.data.connectors;
}

export async function registerWorkspaceConnector(workspaceId, request, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  if (!record(request) || !CONNECTOR_KINDS.includes(request.kind) || typeof request.name !== "string"
      || !request.name.trim() || request.name.length > 160) throw new Error("CONNECTOR_INPUT_INVALID");
  const data = await connectorRequest(workspace, `/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors`, {
    fetchImpl,
    init: { method: "POST", credentials: "same-origin", cache: "no-store", signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) },
  });
  if (!validConnector(data)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return data;
}

export async function reconnectWorkspaceConnector(workspaceId, connectorId, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  if (!safeId(connectorId)) throw new Error("CONNECTOR_INPUT_INVALID");
  const data = await connectorRequest(workspace, `/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors/${encodeURIComponent(connectorId)}/reconnect`, {
    fetchImpl,
    init: { method: "POST", credentials: "same-origin", cache: "no-store", signal },
  });
  if (!validConnector(data)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return data;
}

export async function disconnectWorkspaceConnector(workspaceId, connectorId, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  if (!safeId(connectorId)) throw new Error("CONNECTOR_INPUT_INVALID");
  const data = await connectorRequest(workspace, `/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors/${encodeURIComponent(connectorId)}/disconnect`, {
    fetchImpl,
    init: { method: "POST", credentials: "same-origin", cache: "no-store", signal },
  });
  if (!validConnector(data)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return data;
}

export async function unbindWorkspaceSource(workspaceId, source, { notebookId, etag, idempotencyKey, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "SOURCE_UNBIND_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "SOURCE_UNBIND_INPUT_INVALID");
  if (!record(source) || !exact(source, ["sourceId", "sourceVersionId"])
      || !safeId(source.sourceId) || !safeId(source.sourceVersionId)
      || typeof etag !== "string" || !/^"notebook-binding:[1-9][0-9]*"$/u.test(etag)
      || !safeIdempotencyKey(idempotencyKey)) throw new Error("SOURCE_UNBIND_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/notebooks/${encodeURIComponent(notebook)}/source-unbindings`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "If-Match": etag },
    body: JSON.stringify({ source_id: source.sourceId, source_version_id: source.sourceVersionId }),
  });
  const payload = await json(response, "SOURCE_UNBIND_RESPONSE_INVALID");
  if (!response.ok) throw safeResponseError(payload, "SOURCE_UNBIND_FAILED");
  const valid = exact(payload, ["data", "meta"])
    && exact(payload.data, ["notebook_id", "source_id", "source_version_id", "status"])
    && payload.data.notebook_id === notebook && payload.data.source_id === source.sourceId
    && payload.data.source_version_id === source.sourceVersionId && payload.data.status === "unbound"
    && validMeta(payload.meta, workspace, { replay: true });
  if (!valid) throw new Error("SOURCE_UNBIND_RESPONSE_INVALID");
  return { ...payload.data, etag: response.headers.get("etag") };
}

export async function listWorkspaceKnowledgePackages(
  workspaceId,
  { fetchImpl = fetch, signal, now = Date.now } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "KNOWLEDGE_PACKAGE_LIST_INPUT_INVALID");
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/knowledge-packages`,
    { method: "GET", credentials: "same-origin", cache: "no-store", signal },
  );
  const payload = await json(response, "KNOWLEDGE_PACKAGE_LIST_RESPONSE_INVALID");
  if (!response.ok) {
    throw new Error(
      typeof payload?.error?.code === "string"
        ? payload.error.code
        : "KNOWLEDGE_PACKAGE_LIST_FAILED",
    );
  }
  const checkedAt = now();
  const valid = exact(payload, ["data", "meta"])
    && exact(payload.data, ["items"])
    && Array.isArray(payload.data.items)
    && payload.data.items.length <= 1_000
    && record(payload.meta)
    && safeId(payload.meta.trace_id)
    && payload.data.items.every((item) => {
      const effectiveAt = Date.parse(item.effective_at);
      const expiresAt = Date.parse(item.expires_at);
      return exact(item, KNOWLEDGE_PACKAGE_KEYS)
        && safeId(item.package_id)
        && KNOWLEDGE_PRODUCERS.includes(item.producer)
        && safeText(item.producer_version, 1, 128)
        && safeId(item.knowledge_registration_id)
        && safeId(item.output_version_id)
        && item.authority === "approved"
        && item.registration_state === "registered"
        && item.review_state === "approved"
        && /^[0-9a-f]{64}$/u.test(item.digest_sha256)
        && Number.isSafeInteger(item.byte_size)
        && item.byte_size >= 1
        && item.byte_size <= 8 * 1024 * 1024
        && safeText(item.content_type, 1, 255)
        && Number.isFinite(effectiveAt)
        && Number.isFinite(expiresAt)
        && effectiveAt <= checkedAt
        && checkedAt < expiresAt;
    });
  if (!valid) throw new Error("KNOWLEDGE_PACKAGE_LIST_RESPONSE_INVALID");
  return payload.data.items;
}

export async function getWorkspaceOperationsStatus(
  workspaceId, { fetchImpl = fetch, signal } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "OPERATIONS_STATUS_INPUT_INVALID");
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/operations/status`,
    { method: "GET", credentials: "same-origin", cache: "no-store", signal },
  );
  const payload = await json(response, "OPERATIONS_STATUS_RESPONSE_INVALID");
  if (!response.ok) {
    throw new Error(
      typeof payload?.error?.code === "string"
        ? payload.error.code
        : "OPERATIONS_STATUS_UNAVAILABLE",
    );
  }
  const data = payload?.data;
  const components = data?.components;
  const valid = exact(payload, ["data", "meta"])
    && exact(data, ["workspace_id", "overall_status", "checked_at", "components"])
    && data.workspace_id === workspace
    && new Set(["ready", "warning", "error"]).has(data.overall_status)
    && typeof data.checked_at === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u.test(data.checked_at)
    && Array.isArray(components) && components.length === 5
    && components.every((item, index) => exact(item, OPERATIONS_COMPONENT_KEYS)
      && item.component_id === OPERATIONS_COMPONENT_IDS[index]
      && new Set(["ready", "warning", "error"]).has(item.status)
      && typeof item.safe_code === "string" && /^[A-Z][A-Z0-9_]{2,63}$/u.test(item.safe_code)
      && Number.isSafeInteger(item.pending_count) && item.pending_count >= 0
      && new Set(["none", "open_llm_settings", "open_sync_settings", "refresh_status"]).has(item.recovery_action))
    && validMeta(payload.meta, workspace);
  if (!valid) throw new Error("OPERATIONS_STATUS_RESPONSE_INVALID");
  return data;
}

function validOutputVersionSettings(data, workspace) {
  return exact(data, ["workspace_id", "default_formats", "version_save_mode", "version"])
    && data.workspace_id === workspace
    && exact(data.default_formats, OUTPUT_SETTING_TYPES)
    && OUTPUT_SETTING_TYPES.every((type) => OUTPUT_SETTING_FORMATS[type].includes(data.default_formats[type]))
    && data.version_save_mode === "append_only"
    && Number.isSafeInteger(data.version) && data.version >= 0;
}

export async function getWorkspaceOutputVersionSettings(
  workspaceId, { fetchImpl = fetch, signal } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "OUTPUT_VERSION_SETTINGS_INPUT_INVALID");
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/output-version-settings`,
    { method: "GET", credentials: "same-origin", cache: "no-store", signal },
  );
  const payload = await json(response, "OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "OUTPUT_VERSION_SETTINGS_UNAVAILABLE");
  if (!exact(payload, ["data", "meta"]) || !validOutputVersionSettings(payload.data, workspace)
      || !validMeta(payload.meta, workspace)) throw new Error("OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  const etag = response.headers?.get?.("etag");
  if (typeof etag !== "string" || etag !== `"output-version-settings:${workspace}:${payload.data.version}"`) {
    throw new Error("OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  }
  return { ...payload.data, etag };
}

export async function saveWorkspaceOutputVersionSettings(
  workspaceId, settings,
  { fetchImpl = fetch, idempotencyKey = crypto.randomUUID(), signal } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "OUTPUT_VERSION_SETTINGS_INPUT_INVALID");
  if (!record(settings) || !exact(settings, ["default_formats", "version", "etag"])
      || !validOutputVersionSettings({ workspace_id: workspace, default_formats: settings.default_formats, version_save_mode: "append_only", version: settings.version }, workspace)
      || settings.etag !== `"output-version-settings:${workspace}:${settings.version}"`
      || !safeIdempotencyKey(idempotencyKey)) throw new Error("OUTPUT_VERSION_SETTINGS_INPUT_INVALID");
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/output-version-settings`,
    {
      method: "PATCH", credentials: "same-origin", cache: "no-store", signal,
      headers: { "Content-Type": "application/json", "If-Match": settings.etag, "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ default_formats: settings.default_formats, expected_version: settings.version }),
    },
  );
  const payload = await json(response, "OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "OUTPUT_VERSION_SETTINGS_UNAVAILABLE");
  if (!exact(payload, ["data", "meta"]) || !validOutputVersionSettings(payload.data, workspace)
      || !validMeta(payload.meta, workspace)) throw new Error("OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  const etag = response.headers?.get?.("etag");
  if (typeof etag !== "string" || etag !== `"output-version-settings:${workspace}:${payload.data.version}"`) {
    throw new Error("OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  }
  return { ...payload.data, etag };
}

function validCreateRequest(request) {
  return exact(request, ["source_id", "source_version_id", "run_id", "run_result_id", "title", "purpose"])
    && safeId(request.source_id) && safeId(request.source_version_id)
    && safeId(request.run_id) && safeId(request.run_result_id)
    && safeText(request.title, 1, 200) && safeText(request.purpose, 1, 500);
}

export async function createGroundedReport(
  workspaceId, request, { notebookId, fetchImpl = fetch, idempotencyKey = crypto.randomUUID(), signal } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  if (!safeId(notebookId) || !validCreateRequest(request) || !safeIdempotencyKey(idempotencyKey)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/studio/reports`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ notebook_id: notebookId, ...request }),
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_CREATE_FAILED");
  if (!exact(payload, ["data", "meta"]) || !validOutput(payload.data) || !validMeta(payload.meta, workspace, { replay: true })) {
    throw new Error("STUDIO_RESPONSE_INVALID");
  }
  return payload.data;
}

export async function listStudioOutputs(workspaceId, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  if (!safeId(notebookId)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/studio/outputs?notebook_id=${encodeURIComponent(notebookId)}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_LIST_FAILED");
  if (
    !exact(payload, ["data", "meta"]) || !exact(payload.data, ["outputs"])
    || !Array.isArray(payload.data.outputs) || payload.data.outputs.length > 1_000
    || !payload.data.outputs.every(validOutput) || !validMeta(payload.meta, workspace)
  ) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data.outputs;
}

const STUDIO_OUTPUT_TYPES = new Set(["evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft"]);
const STUDIO_ACTIONS = new Set(["reviews", "approval-requests", "approvals", "deliveries", "knowledge-registrations"]);

export async function issueStudioStepUp(actionGroup, targetId, password, { fetchImpl = fetch, idempotencyKey = crypto.randomUUID(), signal } = {}) {
  if (!new Set(["final_approval_or_knowledge_registration", "external_transfer", "data_area_move"]).has(actionGroup) || !safeId(targetId) || typeof password !== "string" || password.length < 12 || password.length > 1024) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl("/bff/api/session/step-up", {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ action_group: actionGroup, target_id: targetId, ttl_seconds: 300, password }),
  });
  const payload = await json(response, "STEP_UP_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STEP_UP_REQUIRED");
  if (typeof payload?.data?.step_up_authorization !== "string") throw new Error("STEP_UP_RESPONSE_INVALID");
  return payload.data.step_up_authorization;
}

function validGeneration(request, workspaceId) {
  return record(request) && STUDIO_OUTPUT_TYPES.has(request.output_type) && safeId(request.source_id)
    && safeId(request.run_id) && safeId(request.run_result_id)
    && Array.isArray(request.source_version_ids) && request.source_version_ids.length > 0
    && request.source_version_ids.every(safeId) && record(request.settings)
    && request.settings.source_version_ids?.every?.(safeId)
    && (request.workspace_id === undefined || request.workspace_id === null || request.workspace_id === workspaceId);
}

export async function createStudioGeneration(workspaceId, request, { notebookId, fetchImpl = fetch, idempotencyKey = crypto.randomUUID(), signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!validGeneration(request, workspace) || !safeIdempotencyKey(idempotencyKey)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl("/bff/api/studio-generation-requests", {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ ...request, workspace_id: workspace, notebook_id: notebook }),
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_CREATE_FAILED");
  if (!record(payload?.data) || !safeId(payload.data.job_id) || !["queued", "leased", "completed", "failed", "unavailable"].includes(payload.data.status)) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data;
}

export async function getStudioGenerationJob(workspaceId, jobId, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!safeId(jobId)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-generation-jobs/${encodeURIComponent(jobId)}?workspace_id=${encodeURIComponent(workspace)}&notebook_id=${encodeURIComponent(notebook)}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_JOB_FAILED");
  if (!record(payload?.data) || !safeId(payload.data.job_id) || !["queued", "leased", "completed", "failed", "unavailable"].includes(payload.data.status)) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data;
}

export async function listProductStudioOutputs(workspaceId, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-outputs?workspace_id=${encodeURIComponent(workspace)}&notebook_id=${encodeURIComponent(notebook)}`, { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_LIST_FAILED");
  if (!Array.isArray(payload?.data?.outputs) || !Array.isArray(payload?.data?.studio_locks)) throw new Error("STUDIO_RESPONSE_INVALID");
  return { outputs: payload.data.outputs, studioLocks: payload.data.studio_locks };
}

export async function createStudioVersion(workspaceId, outputId, request, { notebookId, fetchImpl = fetch, idempotencyKey = crypto.randomUUID(), signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!safeId(outputId) || !record(request) || !safeId(request.previous_version_id)
      || !new Set(["user_edit", "ai_regeneration", "settings_change"]).has(request.revision_type)
      || typeof request.change_reason !== "string" || !request.change_reason.trim()
      || typeof request.content !== "string" || !request.content.trim()
      || (request.revision_type === "settings_change" && (!record(request.settings) || !Array.isArray(request.settings.source_version_ids)))
      || !safeIdempotencyKey(idempotencyKey)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-outputs/${encodeURIComponent(outputId)}/versions`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ ...request, workspace_id: workspace, notebook_id: notebook }),
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_VERSION_FAILED");
  if (!record(payload?.data) || !safeId(payload.data.output_version_id)) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data;
}

const STUDIO_VERSION_KEYS = Object.freeze([
  "output_version_id", "content_version", "previous_version_id", "status", "content",
  "revision_type", "change_reason", "settings_snapshot_id", "citations", "review_request_id",
  "approval_request_id", "approval_id", "delivery_id", "knowledge_registration_id", "output_format",
]);
const STUDIO_CITATION_KEYS = Object.freeze([
  "citation_id", "source_version_id", "evidence_span_id", "origin", "locator",
]);

function validStudioCitation(value) {
  return exact(value, STUDIO_CITATION_KEYS)
    && safeId(value.citation_id) && safeId(value.source_version_id) && safeId(value.evidence_span_id)
    && new Set(["raw_source", "daon_knowledge"]).has(value.origin)
    && record(value.locator) && exact(value.locator, ["kind", "value"])
    && new Set(["page", "section"]).has(value.locator.kind)
    && safeText(value.locator.value, 1, 500);
}

function validStudioVersion(value) {
  const nullableIds = ["previous_version_id", "review_request_id", "approval_request_id", "approval_id", "delivery_id", "knowledge_registration_id"];
  let serialized;
  try { serialized = JSON.stringify(value.content); } catch { return false; }
  return exact(value, STUDIO_VERSION_KEYS) && safeId(value.output_version_id)
    && Number.isSafeInteger(value.content_version) && value.content_version >= 1
    && nullableIds.every((field) => value[field] === null || safeId(value[field]))
    && new Set(["draft", "review_requested", "in_review", "approved", "revision_requested", "delivered"]).has(value.status)
    && (typeof value.content === "string" || record(value.content))
    && safeText(serialized, 1, 8 * 1024 * 1024)
    && new Set(["initial", "user_edit", "ai_regeneration", "settings_change"]).has(value.revision_type)
    && safeText(value.change_reason, 1, 500) && safeId(value.settings_snapshot_id)
    && Array.isArray(value.citations) && value.citations.length <= 1_000
    && value.citations.every(validStudioCitation)
    && new Set(["docx", "pdf", "xlsx", "csv", "json", "svg", "png"]).has(value.output_format);
}

export async function listStudioVersions(workspaceId, outputId, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!safeId(outputId)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-outputs/${encodeURIComponent(outputId)}/versions?workspace_id=${encodeURIComponent(workspace)}&notebook_id=${encodeURIComponent(notebook)}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_LIST_FAILED");
  if (!exact(payload, ["data", "meta"]) || !exact(payload.data, ["output_id", "versions"])
      || payload.data.output_id !== outputId || !Array.isArray(payload.data.versions)
      || payload.data.versions.length > 1_000 || !payload.data.versions.every(validStudioVersion)
      || !validMeta(payload.meta, workspace)) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data.versions;
}

export async function createStudioAction(workspaceId, action, request, { notebookId, fetchImpl = fetch, idempotencyKey = crypto.randomUUID(), signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!STUDIO_ACTIONS.has(action) || !record(request) || !safeIdempotencyKey(idempotencyKey)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/${action}`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ ...request, workspace_id: workspace, notebook_id: notebook }),
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_ACTION_FAILED");
  return payload.data;
}

export async function downloadStudioExport(workspaceId, outputId, versionId, format, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!safeId(outputId) || !safeId(versionId) || !new Set(["docx", "pdf", "xlsx", "csv", "json", "svg", "png"]).has(format)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-outputs/${encodeURIComponent(outputId)}/versions/${encodeURIComponent(versionId)}/exports/${format}?workspace_id=${encodeURIComponent(workspace)}&notebook_id=${encodeURIComponent(notebook)}`, { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  if (!response.ok) throw new Error("STUDIO_EXPORT_FAILED");
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength < 4 || buffer.byteLength > 8 * 1024 * 1024) throw new Error("STUDIO_EXPORT_INVALID");
  return { bytes: [...new Uint8Array(buffer)], contentType: response.headers.get("content-type"), disposition: response.headers.get("content-disposition") };
}
