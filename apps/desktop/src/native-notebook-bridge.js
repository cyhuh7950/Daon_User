const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const SAFE_ERRORS = new Set(["AUTHENTICATION_REQUIRED", "FORBIDDEN", "INVALID_REQUEST", "CONFLICT", "RESOURCE_UNAVAILABLE", "WORKSPACE_REQUEST_FAILED", "WORKSPACE_RESPONSE_REJECTED"]);

const exact = (value, keys) => value !== null && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const id = (value) => typeof value === "string" && SAFE_ID.test(value);
const timestamp = (value) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/u.test(value);
const fail = (code) => Object.assign(new Error(code), { code });

function projectView(value) {
  if (!exact(value, ["notebook_id", "title", "source_count", "output_count", "updated_at", "status"])
      || !id(value.notebook_id) || typeof value.title !== "string" || value.title.length < 1 || value.title.length > 120
      || !Number.isSafeInteger(value.source_count) || value.source_count < 0
      || !Number.isSafeInteger(value.output_count) || value.output_count < 0
      || !timestamp(value.updated_at) || !["empty", "active", "attention"].includes(value.status)) {
    throw fail("NOTEBOOK_RESPONSE_INVALID");
  }
  return { notebook_id: value.notebook_id, title: value.title, source_count: value.source_count, output_count: value.output_count, updated_at: value.updated_at, status: value.status };
}

function projectContext(value, notebookId) {
  const keys = ["notebook_id", "sources", "knowledge_context_ids", "conversation_thread_ids", "studio_output_ids", "output_version_ids", "generation_settings_ids", "conversation"];
  const idList = (items) => Array.isArray(items) && items.length <= 1000 && items.every(id);
  if (!exact(value, keys) || value.notebook_id !== notebookId || !Array.isArray(value.sources)
      || !value.sources.every((item) => exact(item, ["source_id", "source_version_id"]) && id(item.source_id) && id(item.source_version_id))
      || !idList(value.knowledge_context_ids) || !idList(value.conversation_thread_ids)
      || !idList(value.studio_output_ids) || !idList(value.output_version_ids) || !idList(value.generation_settings_ids)) {
    throw fail("NOTEBOOK_CONTEXT_INVALID");
  }
  if (value.conversation !== null) {
    if (!exact(value.conversation, ["conversation_thread_id", "answer"])
        || value.conversation.conversation_thread_id !== value.conversation_thread_ids[0]) throw fail("NOTEBOOK_CONTEXT_INVALID");
  } else if (value.conversation_thread_ids.length !== 0) throw fail("NOTEBOOK_CONTEXT_INVALID");
  try { return projectNotebookSelectedContext(value); }
  catch { throw fail("NOTEBOOK_CONTEXT_INVALID"); }
}

function normalize(error) {
  const code = typeof error?.code === "string" && SAFE_ERRORS.has(error.code) ? error.code : "NOTEBOOK_UNAVAILABLE";
  return fail(code);
}

export function createNativeNotebookBridge({ invoke } = {}) {
  const call = typeof invoke === "function" ? invoke : window.__TAURI_INTERNALS__?.invoke;
  if (typeof call !== "function") throw fail("AUTHENTICATION_REQUIRED");
  const selected = (workspaceId, notebookId) => {
    if (!id(workspaceId) || !id(notebookId)) throw fail("NOTEBOOK_INPUT_INVALID");
    return { workspace_id: workspaceId, notebook_id: notebookId };
  };
  return Object.freeze({
    async list(workspaceId) {
      if (!id(workspaceId)) throw fail("NOTEBOOK_INPUT_INVALID");
      try {
        const values = await call("notebook_list", { input: { workspace_id: workspaceId } });
        if (!Array.isArray(values) || values.length > 500) throw fail("NOTEBOOK_RESPONSE_INVALID");
        return values.map(projectView);
      } catch (error) { if (error?.code?.startsWith?.("NOTEBOOK_")) throw error; throw normalize(error); }
    },
    async create(workspaceId, input, requestIdempotencyKey) {
      if (!exact(input, ["title", "description"]) || typeof input.title !== "string" || !id(requestIdempotencyKey)) throw fail("NOTEBOOK_INPUT_INVALID");
      try {
        return projectView(await call("notebook_create", { input: { workspace_id: workspaceId, title: input.title, description: input.description, request_idempotency_key: requestIdempotencyKey } }));
      } catch (error) { if (error?.code?.startsWith?.("NOTEBOOK_")) throw error; throw normalize(error); }
    },
    async get(workspaceId, notebookId) {
      try { return projectView(await call("notebook_get", { input: selected(workspaceId, notebookId) })); }
      catch (error) { if (error?.code?.startsWith?.("NOTEBOOK_")) throw error; throw normalize(error); }
    },
    async context(workspaceId, notebookId) {
      try { return projectContext(await call("notebook_context", { input: selected(workspaceId, notebookId) }), notebookId); }
      catch (error) { if (error?.code?.startsWith?.("NOTEBOOK_")) throw error; throw normalize(error); }
    },
  });
}
import { projectNotebookSelectedContext } from "@daon-user/ui/notebook-context-adapter";
