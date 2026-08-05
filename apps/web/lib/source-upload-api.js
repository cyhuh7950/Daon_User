"use client";

export async function uploadPdfSource(workspaceId, file, { idempotencyKey } = {}) {
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
    },
  );
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.code ?? "PDF_UPLOAD_FAILED");
  }
  return payload.data;
}
