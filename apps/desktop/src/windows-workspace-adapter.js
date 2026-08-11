const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const PDF_MAX_BYTES = 25 * 1024 * 1024;
const SAFE_ERROR_CODES = new Set([
  "AUTHENTICATION_REQUIRED", "FORBIDDEN", "INVALID_REQUEST", "REQUEST_TOO_LARGE", "CONFLICT",
  "RESOURCE_UNAVAILABLE", "WORKSPACE_REQUEST_FAILED", "WORKSPACE_RESPONSE_REJECTED", "WORKSPACE_REQUEST_ABORTED"
]);

function nativeInvoke() {
  if (typeof window === "undefined") return null;
  return window.__TAURI_INTERNALS__?.invoke ?? null;
}

function fail(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function exact(value, keys) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function normalizeError(error) {
  if (error instanceof Error && SAFE_ERROR_CODES.has(error.code)) return fail(error.code);
  if (exact(error, ["code", "retryable"]) && SAFE_ERROR_CODES.has(error.code) && typeof error.retryable === "boolean") {
    return fail(error.code);
  }
  return fail("WORKSPACE_RESPONSE_REJECTED");
}

function id(value) {
  return typeof value === "string" && SAFE_ID.test(value);
}

function invokeAbortable(call, command, input, signal) {
  if (signal?.aborted) return Promise.reject(fail("WORKSPACE_REQUEST_ABORTED"));
  const pending = call(command, { input });
  if (!signal) return pending.catch((error) => Promise.reject(normalizeError(error)));
  return new Promise((resolve, reject) => {
    const abort = () => reject(fail("WORKSPACE_REQUEST_ABORTED"));
    signal.addEventListener("abort", abort, { once: true });
    pending.then(resolve, (error) => reject(normalizeError(error))).finally(() => signal.removeEventListener("abort", abort));
  });
}

function assertArray(value, code) {
  if (!Array.isArray(value)) throw fail(code);
  return value;
}

function validCitation(value) {
  return exact(value, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page"])
    && id(value.citation_id) && id(value.source_id) && id(value.source_version_id)
    && id(value.evidence_span_id) && Number.isSafeInteger(value.page) && value.page >= 1;
}

function validOutput(value) {
  return exact(value, ["studio_output_id", "output_version_id", "output_type", "title", "purpose", "status", "content", "run_id", "run_result_id", "citations"])
    && id(value.studio_output_id) && id(value.output_version_id) && value.output_type === "evidence_report"
    && typeof value.title === "string" && value.title.length >= 1 && value.title.length <= 200
    && typeof value.purpose === "string" && value.purpose.length >= 1 && value.purpose.length <= 500
    && value.status === "draft" && typeof value.content === "string" && value.content.length >= 1
    && value.content.length <= 20_000 && id(value.run_id) && id(value.run_result_id)
    && Array.isArray(value.citations) && value.citations.length >= 1 && value.citations.length <= 20
    && value.citations.every(validCitation);
}

export function createWindowsWorkspaceAdapter(workspaceId, { invoke = nativeInvoke() } = {}) {
  if (!id(workspaceId) || typeof invoke !== "function") throw fail("WORKSPACE_ADAPTER_UNAVAILABLE");
  const call = (command, input, signal) => invokeAbortable(invoke, command, input, signal);
  const adapter = {
    async listSources({ signal } = {}) {
      const value = assertArray(await call("workspace_list_sources", { workspace_id: workspaceId }, signal), "SOURCE_LIST_RESPONSE_INVALID");
      if (value.length > 1_000 || !value.every((source) => exact(source, ["source_id", "source_version_id", "filename", "source_state", "processing_state", "job_state"]) && id(source.source_id) && id(source.source_version_id))) {
        throw fail("SOURCE_LIST_RESPONSE_INVALID");
      }
      return value;
    },
    async uploadPdf(file, { signal } = {}) {
      if (!file || file.type !== "application/pdf" || typeof file.name !== "string" || file.size < 5 || file.size > PDF_MAX_BYTES || typeof file.arrayBuffer !== "function") {
        throw fail("PDF_UPLOAD_INPUT_INVALID");
      }
      const bytes = new Uint8Array(await file.arrayBuffer());
      if (bytes.byteLength !== file.size || bytes[0] !== 0x25 || bytes[1] !== 0x50 || bytes[2] !== 0x44 || bytes[3] !== 0x46 || bytes[4] !== 0x2d) {
        throw fail("PDF_UPLOAD_INPUT_INVALID");
      }
      return call("workspace_upload_pdf", { workspace_id: workspaceId, filename: file.name, mime_type: file.type, bytes: [...bytes] }, signal);
    },
    getProcessingStatus(processingRunId, { signal } = {}) {
      if (!id(processingRunId)) return Promise.reject(fail("PROCESSING_STATUS_INPUT_INVALID"));
      return call("workspace_processing_status", { workspace_id: workspaceId, processing_run_id: processingRunId }, signal);
    },
    askQuestion({ sourceId, sourceVersionId, question }, { signal } = {}) {
      if (!id(sourceId) || !id(sourceVersionId) || typeof question !== "string" || !question.trim() || question.length > 2_000) return Promise.reject(fail("QUESTION_INPUT_INVALID"));
      return call("workspace_ask_question", { workspace_id: workspaceId, source_id: sourceId, source_version_id: sourceVersionId, question: question.trim() }, signal);
    },
    citationUrl(citation) {
      if (!id(citation?.citation_id) || !Number.isSafeInteger(citation?.page) || citation.page < 1) throw fail("CITATION_INPUT_INVALID");
      return `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/citations/${encodeURIComponent(citation.citation_id)}/content#page=${citation.page}`;
    },
    async citationContent(citation, { signal } = {}) {
      if (!id(citation?.citation_id) || !Number.isSafeInteger(citation?.page) || citation.page < 1) throw fail("CITATION_INPUT_INVALID");
      const value = await call("workspace_citation_content", { workspace_id: workspaceId, citation_id: citation.citation_id, page: citation.page }, signal);
      if (!exact(value, ["content_type", "page", "bytes"]) || value.content_type !== "application/pdf" || value.page !== citation.page || !Array.isArray(value.bytes) || value.bytes.length < 5 || value.bytes.length > PDF_MAX_BYTES || value.bytes.slice(0, 5).some((byte, index) => byte !== [0x25, 0x50, 0x44, 0x46, 0x2d][index])) {
        throw fail("CITATION_RESPONSE_INVALID");
      }
      return value;
    },
    createReport(input, { signal, idempotencyKey } = {}) {
      if (typeof idempotencyKey !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/u.test(idempotencyKey)) {
        return Promise.reject(fail("STUDIO_INPUT_INVALID"));
      }
      return call("workspace_create_report", { workspace_id: workspaceId, ...input, request_idempotency_key: idempotencyKey }, signal).then((value) => {
        if (!validOutput(value)) throw fail("STUDIO_RESPONSE_INVALID");
        return value;
      });
    },
    listStudioOutputs({ signal } = {}) {
      return call("workspace_list_studio_outputs", { workspace_id: workspaceId }, signal).then((value) => {
        const outputs = assertArray(value, "STUDIO_RESPONSE_INVALID");
        if (outputs.length > 1_000 || !outputs.every(validOutput)) throw fail("STUDIO_RESPONSE_INVALID");
        return outputs;
      });
    }
  };
  return Object.freeze(adapter);
}
