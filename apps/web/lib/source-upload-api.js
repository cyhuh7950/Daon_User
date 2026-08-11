"use client";

export async function uploadPdfSource(workspaceId, file, { idempotencyKey, signal } = {}) {
  if (!workspaceId || !file || file.type !== "application/pdf") {
    throw new Error("PDF_UPLOAD_INPUT_INVALID");
  }
  const key = idempotencyKey ?? `pdf-${crypto.randomUUID()}`;
  const response = await fetch(
    `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/sources`,
    {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Content-Type": "application/pdf",
        "Idempotency-Key": key,
        "X-Source-Filename": file.name,
      },
      body: file,
      signal,
    },
  );
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.code ?? "PDF_UPLOAD_FAILED");
  }
  return payload.data;
}

export async function getDocumentProcessingStatus(workspaceId, processingRunId, { signal } = {}) {
  if (!workspaceId || !processingRunId) {
    throw new Error("PROCESSING_STATUS_INPUT_INVALID");
  }
  const response = await fetch(
    `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/processing-runs/${encodeURIComponent(processingRunId)}`,
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
