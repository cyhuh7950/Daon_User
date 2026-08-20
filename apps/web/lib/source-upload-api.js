"use client";

export async function uploadPdfSource(workspaceId, file, { notebookId, idempotencyKey, signal } = {}) {
  if (!workspaceId || !notebookId || !file || file.type !== "application/pdf") {
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
        "X-Notebook-Id": notebookId,
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
