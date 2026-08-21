"use client";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const SAFE_DIGEST = /^[0-9a-f]{64}$/u;
const MAX_PDF_BYTES = 25 * 1024 * 1024;

function uploadResponseError(payload, fallback) {
  const error = new Error(typeof payload?.error?.code === "string" ? payload.error.code : fallback);
  Object.defineProperty(error, "retryable", {
    configurable: false, enumerable: false, value: payload?.error?.retryable === true, writable: false,
  });
  return error;
}

function validUploadData(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return false;
  const keys = Object.keys(data).sort();
  const expected = [
    "byte_size", "digest_sha256", "job_state", "object_id", "processing_run_id",
    "processing_state", "replayed", "source_id", "source_version_id", "status",
  ].sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index])
    && SAFE_ID.test(data.source_id) && SAFE_ID.test(data.source_version_id)
    && /^[0-9a-f]{32}$/u.test(data.object_id) && SAFE_DIGEST.test(data.digest_sha256)
    && Number.isSafeInteger(data.byte_size) && data.byte_size >= 1 && data.byte_size <= MAX_PDF_BYTES
    && data.status === "accepted" && typeof data.replayed === "boolean"
    && SAFE_ID.test(data.processing_run_id) && typeof data.processing_state === "string"
    && (data.job_state === null || typeof data.job_state === "string");
}

export async function uploadPdfSource(workspaceId, file, { notebookId, idempotencyKey, signal, fetchImpl = fetch } = {}) {
  if (!workspaceId || !notebookId || !file || file.type !== "application/pdf"
      || typeof file.name !== "string" || !file.name || file.name.length > 255
      || /[/\\\u0000-\u001f]/u.test(file.name) || !Number.isSafeInteger(file.size)
      || file.size < 1 || file.size > MAX_PDF_BYTES) {
    throw new Error("PDF_UPLOAD_INPUT_INVALID");
  }
  const key = idempotencyKey ?? `pdf-${crypto.randomUUID()}`;
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/sources`,
    {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Content-Type": "application/pdf",
        "Idempotency-Key": key,
        "X-Source-Filename": file.name,
        "X-Notebook-Id": notebookId,
      },
      body: file,
      signal,
    },
  );
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("PDF_UPLOAD_RESPONSE_INVALID");
  }
  if (!response.ok) throw uploadResponseError(payload, "SOURCE_UPLOAD_REJECTED");
  if (!payload || typeof payload !== "object" || Array.isArray(payload)
      || !validUploadData(payload.data)) throw new Error("PDF_UPLOAD_RESPONSE_INVALID");
  return payload.data;
}

export async function getDocumentProcessingStatus(workspaceId, processingRunId, { notebookId, signal } = {}) {
  if (!workspaceId || !processingRunId || !notebookId) {
    throw new Error("PROCESSING_STATUS_INPUT_INVALID");
  }
  const response = await fetch(
    `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/processing-runs/${encodeURIComponent(processingRunId)}?notebook_id=${encodeURIComponent(notebookId)}`,
    {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      signal,
    },
  );
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.code ?? "PROCESSING_STATUS_FAILED");
  }
  return payload.data;
}
