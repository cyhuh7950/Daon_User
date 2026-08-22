const COPY_STATES = new Set(["available", "approved", "revoked", "expired"]);
const SYNC_STATES = new Set([
  "draft", "awaiting_approval", "approved", "transferring", "conflict", "reindex_requested",
]);

const freezeRequest = (request) => Object.freeze({ ...request });

function validateProjection(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("OFFLINE_SYNC_RESPONSE_REJECTED");
  }
  if ("state" in value && !COPY_STATES.has(value.state) && !SYNC_STATES.has(value.state)) {
    throw new Error("OFFLINE_SYNC_RESPONSE_REJECTED");
  }
  if (Array.isArray(value.items)) value.items.forEach(validateProjection);
  return value;
}

async function invokeWithStepUp(invoke, command, request) {
  const holder = request?.stepUpAuthorizationId;
  const stepUpAuthorizationId = holder && typeof holder === "object" ? holder.value : holder;
  try {
    return validateProjection(await invoke(command, {
      request: freezeRequest({ ...request, stepUpAuthorizationId }),
    }));
  } finally {
    if (holder && typeof holder === "object") holder.value = "";
  }
}

export function createOfflineSyncAdapter({ invoke } = {}) {
  if (typeof invoke !== "function") return null;
  const call = async (command, request) => validateProjection(
    await invoke(command, { request: freezeRequest(request) })
  );
  return Object.freeze({
    listKnowledge: (request) => call("offline_knowledge_list", request),
    provisionKnowledge: (request) => invokeWithStepUp(invoke, "offline_knowledge_provision", request),
    refreshKnowledge: (request) => call("offline_knowledge_refresh", request),
    previewSync: (request) => call("offline_sync_preview", request),
    syncStatus: (request) => call("offline_sync_status", request),
    approveSync: (request) => invokeWithStepUp(invoke, "offline_sync_approve", request),
    transferSync: (request) => call("offline_sync_transfer", request),
    resolveSync: (request) => call("offline_sync_resolve", request),
  });
}
