"use client";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const SAFE_FILENAME = /^[^/\\\u0000-\u001f]{1,255}$/u;
const INTERNAL_VALUE = /(?:https?:\/\/|localhost|127\.0\.0\.1|password|authorization|credential)/iu;
const SOURCE_KEYS = Object.freeze([
  "source_id", "source_version_id", "filename", "source_state", "processing_state", "job_state"
]);
const CITATION_KEYS = Object.freeze([
  "citation_id", "source_id", "source_version_id", "evidence_span_id", "page"
]);
const OUTPUT_KEYS = Object.freeze([
  "studio_output_id", "output_version_id", "output_type", "title", "purpose", "status",
  "content", "run_id", "run_result_id", "citations"
]);

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
    && value.output_type === "evidence_report" && value.status === "draft"
    && safeText(value.title, 1, 200) && safeText(value.purpose, 1, 500)
    && safeText(value.content, 1, 20_000) && safeId(value.run_id) && safeId(value.run_result_id)
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

export async function listWorkspaceSources(workspaceId, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "SOURCE_LIST_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/sources`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "SOURCE_LIST_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "SOURCE_LIST_FAILED");
  const valid = exact(payload, ["data", "meta"])
    && exact(payload.data, ["sources"])
    && Array.isArray(payload.data.sources)
    && payload.data.sources.length <= 1_000
    && validMeta(payload.meta, workspace)
    && payload.data.sources.every((source) => exact(source, SOURCE_KEYS)
      && safeId(source.source_id) && safeId(source.source_version_id)
      && typeof source.filename === "string" && SAFE_FILENAME.test(source.filename)
      && ["registered", "security_check", "processing", "indexing", "ready"].includes(source.source_state)
      && typeof source.processing_state === "string" && typeof source.job_state === "string");
  if (!valid) throw new Error("SOURCE_LIST_RESPONSE_INVALID");
  return payload.data.sources;
}

function validCreateRequest(request) {
  return exact(request, ["source_id", "source_version_id", "run_id", "run_result_id", "title", "purpose"])
    && safeId(request.source_id) && safeId(request.source_version_id)
    && safeId(request.run_id) && safeId(request.run_result_id)
    && safeText(request.title, 1, 200) && safeText(request.purpose, 1, 500);
}

export async function createGroundedReport(
  workspaceId, request, { fetchImpl = fetch, idempotencyKey = crypto.randomUUID(), signal } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  if (!validCreateRequest(request) || !safeIdempotencyKey(idempotencyKey)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/studio/reports`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(request),
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_CREATE_FAILED");
  if (!exact(payload, ["data", "meta"]) || !validOutput(payload.data) || !validMeta(payload.meta, workspace, { replay: true })) {
    throw new Error("STUDIO_RESPONSE_INVALID");
  }
  return payload.data;
}

export async function listStudioOutputs(workspaceId, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/studio/outputs`, {
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
