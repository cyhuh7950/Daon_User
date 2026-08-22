const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;

const freezeRequest = (request) => Object.freeze({ ...request });
const isSafeId = (value) => typeof value === "string" && SAFE_ID.test(value);
const RAW_CONTENT_TYPES = new Set(["application/pdf", "text/plain", "text/markdown"]);

function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 32_768;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

async function sha256(bytes) {
  const digest = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export function createOfflineStudioAdapter({ invoke } = {}) {
  if (typeof invoke !== "function") return null;
  return Object.freeze({
    listModels: (workspaceId) => {
      if (!isSafeId(workspaceId)) return Promise.reject(new Error("LOCAL_STUDIO_INPUT_INVALID"));
      return invoke("offline_studio_list_models", {
        request: freezeRequest({ workspace_id: workspaceId }),
      });
    },
    listRawSources: (workspaceId) => {
      if (!isSafeId(workspaceId)) return Promise.reject(new Error("LOCAL_STUDIO_INPUT_INVALID"));
      return invoke("offline_studio_list_raw_sources", {
        request: freezeRequest({ workspace_id: workspaceId }),
      });
    },
    importRawSource: async (request) => {
      if (
        !isSafeId(request?.workspace_id)
        || !isSafeId(request?.idempotency_key)
        || typeof request?.filename !== "string"
        || !request.filename
        || request.filename.length > 255
        || request.filename !== request.filename.trim()
        || /[\\/\0]/u.test(request.filename)
        || !RAW_CONTENT_TYPES.has(request?.content_type)
        || !(request?.bytes instanceof Uint8Array)
        || request.bytes.length === 0
        || request.bytes.length > 25 * 1024 * 1024
      ) {
        throw new Error("LOCAL_STUDIO_INPUT_INVALID");
      }
      const wire = {
        workspace_id: request.workspace_id,
        filename: request.filename,
        content_type: request.content_type,
        content_base64: bytesToBase64(request.bytes),
        content_digest_sha256: await sha256(request.bytes),
        idempotency_key: request.idempotency_key,
      };
      try {
        return await invoke("offline_studio_import_raw_source", { request: wire });
      } finally {
        request.bytes.fill(0);
        wire.content_base64 = "";
      }
    },
    prepareContext: (request) => invoke(
      "offline_studio_prepare_context", { request: freezeRequest(request) }
    ),
    confirmSettings: (request) => invoke(
      "offline_studio_confirm_settings", { request: freezeRequest(request) }
    ),
    generateDraft: (request) => invoke(
      "offline_studio_generate_draft", { request: freezeRequest(request) }
    ),
    getDraft: (request) => {
      if (!isSafeId(request?.workspace_id) || !isSafeId(request?.draft_id)) {
        return Promise.reject(new Error("LOCAL_STUDIO_INPUT_INVALID"));
      }
      return invoke("offline_studio_get_draft", { request: freezeRequest(request) });
    },
    appendEdit: (request) => invoke(
      "offline_studio_append_edit", { request: freezeRequest(request) }
    ),
    queueSync: (request) => invoke(
      "offline_studio_queue_sync", { request: freezeRequest(request) }
    ),
  });
}
