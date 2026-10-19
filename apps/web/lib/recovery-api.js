import { getWorkspaceOperationsStatus } from "./product-workspace-api.js";
import { getNotebookContext, listNotebooks } from "./notebook-api.js";

const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });
const selectionId = value => typeof value === "string" && !/[\s\u0000-\u001f\u007f]/u.test(value)
  && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(value);

async function selectionFetch(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw requestError(response, await response.json().catch(() => null));
  return response;
}

function requestError(response, payload) {
  const error = new Error(payload?.error?.code ?? "RESOURCE_UNAVAILABLE");
  error.code = payload?.error?.code ?? "RESOURCE_UNAVAILABLE";
  error.status = response.status;
  error.traceId = payload?.error?.trace_id ?? response.headers.get("x-trace-id");
  return error;
}

function sessionFailure() {
  const error = new Error("RESOURCE_UNAVAILABLE");
  error.code = "RESOURCE_UNAVAILABLE";
  return error;
}

function exactFields(input, fields) {
  return input && typeof input === "object" && !Array.isArray(input)
    && Object.keys(input).length === fields.length && fields.every(key => Object.hasOwn(input, key));
}

export function parseSystemOperationsStatus(value) {
  const kinds=["document","studio","object"],metrics=["cpu_quota_millicores","memory_limit_bytes","memory_current_bytes","runtime_available_bytes"];
  const utc=v=>typeof v==="string"&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/u.test(v)
    &&Number.isFinite(Date.parse(v))&&new Date(v).toISOString().slice(0,19)===v.slice(0,19);
  const nullable=v=>v===null||utc(v);
  const common=["state","observed_at","received_at","safe_code"];
  const commonValid=r=>r&&["fresh","stale","measured","partial","not_collected","not_reported","unavailable"].includes(r.state)
    &&r.safe_code===`TELEMETRY_${r.state.toUpperCase()}`&&nullable(r.observed_at)&&nullable(r.received_at)
    &&(["not_collected","not_reported","unavailable"].includes(r.state)
      ?r.observed_at===null&&r.received_at===null:utc(r.observed_at)&&utc(r.received_at));
  const invalid=()=>{throw sessionFailure();};
  if(!exactFields(value,["schema_version","checked_at","health","worker_samples","resource_samples","deployment_summary","unimplemented"])
    ||value.schema_version!==1||!utc(value.checked_at)||!Array.isArray(value.health)||value.health.length!==3
    ||!Array.isArray(value.worker_samples)||value.worker_samples.length!==3||!Array.isArray(value.resource_samples)||value.resource_samples.length!==12)invalid();
  for(const [i,id] of ["api","database","object_storage"].entries()){
    const r=value.health[i];if(!exactFields(r,["component_id","state","checked_at","safe_code"])||r.component_id!==id
      ||!["ready","unavailable"].includes(r.state)||r.checked_at!==value.checked_at||r.safe_code!==`${id.toUpperCase()}_${r.state.toUpperCase()}`)invalid();
  }
  for(const [i,kind] of kinds.entries()){
    const r=value.worker_samples[i];if(!exactFields(r,[...common,"worker_kind","process_state","poll_started_at","poll_finished_at","active_started_at"])
      ||!commonValid(r)||!["fresh","stale","not_collected","unavailable"].includes(r.state)||r.worker_kind!==kind
      ||!["alive","processing","poll_stale","stopped","unknown"].includes(r.process_state)
      ||![r.poll_started_at,r.poll_finished_at,r.active_started_at].every(nullable)
      ||(["not_collected","unavailable"].includes(r.state)&&(r.process_state!=="unknown"||[r.poll_started_at,r.poll_finished_at,r.active_started_at].some(v=>v!==null)))
      ||(r.process_state==="processing"&&r.active_started_at===null))invalid();
    for(const [j,metric] of metrics.entries()){
      const m=value.resource_samples[i*4+j];if(!exactFields(m,[...common,"worker_kind","metric_id","value","unit"])||!commonValid(m)
        ||m.worker_kind!==kind||m.metric_id!==metric||m.unit!==(j===0?"millicores":"bytes")
        ||(m.value!==null&&(!Number.isSafeInteger(m.value)||m.value<0))
        ||(["not_collected","not_reported","unavailable"].includes(m.state)?m.value!==null:m.value===null))invalid();
    }
  }
  const d=value.deployment_summary;
  if(!exactFields(d,[...common,"operation","code_revision","image_digest","previous_image_digest","result"])||!commonValid(d)
    ||!(d.operation===null||["deploy","rollback"].includes(d.operation))||!(d.result===null||["succeeded","failed","uncertain"].includes(d.result))
    ||!(d.code_revision===null||typeof d.code_revision==="string"&&/^[a-f0-9]{40}$/u.test(d.code_revision))
    ||![d.image_digest,d.previous_image_digest].every(v=>v===null||typeof v==="string"&&/^sha256:[a-f0-9]{64}$/u.test(v))
    ||(["not_collected","not_reported","unavailable"].includes(d.state)&&[d.operation,d.code_revision,d.image_digest,d.previous_image_digest,d.result].some(v=>v!==null)))invalid();
  const ids=["worker_observation_producer","resource_observation_producer","deployment_receipt_writer"],impacts=["WORKER_STATE_UNKNOWN","CAPACITY_UNKNOWN","DEPLOYMENT_HISTORY_UNKNOWN"];
  if(!Array.isArray(value.unimplemented)||value.unimplemented.length>3)invalid();
  let previous=-1;
  for(const r of value.unimplemented){const i=ids.indexOf(r?.producer_id);
    if(!exactFields(r,["producer_id","safe_code","impact_code"])||i<=previous||i<0||r.safe_code!=="PRODUCER_NOT_IMPLEMENTED"||r.impact_code!==impacts[i])invalid();previous=i;
  }
  return structuredClone(value);
}

function lineageSelection(input, create) {
  const fields = ["workspace_id", "notebook_id", "source_version_ids", "lineage_mode",
    ...(create ? ["trigger", "closure_consent"] : [])];
  if (!exactFields(input, fields) || !selectionId(input.workspace_id) || !selectionId(input.notebook_id)
    || input.lineage_mode !== "exact" || !Array.isArray(input.source_version_ids)
    || input.source_version_ids.length < 1 || input.source_version_ids.length > 100
    || !input.source_version_ids.every(selectionId) || new Set(input.source_version_ids).size !== input.source_version_ids.length) throw sessionFailure();
  if (create && (input.trigger !== "manual" || !exactFields(input.closure_consent, ["include_prior_versions", "closure_fingerprint"])
    || input.closure_consent.include_prior_versions !== true || !/^[0-9a-f]{64}$/u.test(input.closure_consent.closure_fingerprint))) throw sessionFailure();
  return { workspace_id: input.workspace_id, notebook_id: input.notebook_id,
    source_version_ids: [...input.source_version_ids].sort(), lineage_mode: "exact",
    ...(create ? { trigger: "manual", closure_consent: { include_prior_versions: true,
      closure_fingerprint: input.closure_consent.closure_fingerprint } } : {}) };
}

export function parseRecoverySessionContext(payload) {
  const session = payload?.data;
  const values = [session?.user_id, session?.tenant_id, session?.workspace_id];
  if (!values.every((value) => typeof value === "string" && value.trim())) return null;
  return {
    userId: session.user_id,
    tenantId: session.tenant_id,
    workspaceId: session.workspace_id,
    membership: null
  };
}

export async function resolveRecoverySession(adapter, initializePane = (context) => context) {
  const { payload } = await adapter.getSession();
  const context = parseRecoverySessionContext(payload);
  if (!context) throw sessionFailure();
  return initializePane(context);
}

async function request(path, { method = "GET", body, headers = {}, signal } = {}) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    signal,
    headers: body === undefined ? headers : { ...JSON_HEADERS, ...headers },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({
    error: { code: "RESOURCE_UNAVAILABLE", trace_id: response.headers.get("x-trace-id") }
  }));
  if (!response.ok) {
    throw requestError(response, payload);
  }
  return { payload, etag: response.headers.get("etag") };
}

export async function issueRestoreStepUp(requestId, password, { idempotencyKey, signal } = {}) {
  if (typeof requestId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(requestId)
    || typeof password !== "string" || password.length < 12 || password.length > 1024
    || typeof idempotencyKey !== "string" || !/^[A-Za-z0-9_-]{16,128}$/u.test(idempotencyKey)) {
    throw sessionFailure();
  }
  const { payload } = await request("/bff/api/session/step-up", {
    method: "POST", signal, headers: { "Idempotency-Key": idempotencyKey },
    body: { action_group: "permanent_delete_or_restore_rollback", target_id: requestId, password, ttl_seconds: 300 }
  });
  const token = payload?.data?.step_up_authorization;
  if (typeof token !== "string" || !token.trim() || token.length > 4096 || /[\u0000-\u0020\u007f]/u.test(token)) {
    throw sessionFailure();
  }
  return token;
}

export const recoveryApi = Object.freeze({
  async getSystemOperationsStatus({signal}={}) {
    const {payload}=await request("/bff/api/system/operations/status",{signal});
    return parseSystemOperationsStatus(payload?.data);
  },
  issueRestoreStepUp,
  previewBackupSelection(input, { signal } = {}) {
    return request("/bff/api/backups/selection-previews", { method: "POST", signal, body: lineageSelection(input, false) });
  },
  createLineageBackup(input, idempotencyKey, { signal } = {}) {
    if (!selectionId(idempotencyKey) || idempotencyKey.length < 16) throw sessionFailure();
    return request("/bff/api/backups", { method: "POST", signal, body: lineageSelection(input, true),
      headers: { "Idempotency-Key": idempotencyKey } });
  },
  getRestoreDestinations(backupId, { signal } = {}) {
    if (!selectionId(backupId)) throw sessionFailure();
    return request(`/bff/api/backups/${encodeURIComponent(backupId)}/restore-destinations`, { signal });
  },
  previewRegisteredRestore(backupId, input, idempotencyKey, { signal } = {}) {
    if (!selectionId(backupId) || !selectionId(idempotencyKey) || idempotencyKey.length < 16
      || !exactFields(input, ["workspace_id", "destination_id", "destination_version", "step_up_authorization_id"])
      || !selectionId(input.workspace_id) || !selectionId(input.destination_id)
      || !Number.isSafeInteger(input.destination_version) || input.destination_version < 1 || input.destination_version > 2147483647
      || typeof input.step_up_authorization_id !== "string" || !input.step_up_authorization_id
      || input.step_up_authorization_id.length > 512 || /[\u0000-\u0020\u007f]/u.test(input.step_up_authorization_id)) throw sessionFailure();
    return request(`/bff/api/backups/${encodeURIComponent(backupId)}/restore-previews`, { method: "POST", signal,
      body: { workspace_id: input.workspace_id, destination_id: input.destination_id,
        destination_version: input.destination_version, step_up_authorization_id: input.step_up_authorization_id },
      headers: { "Idempotency-Key": idempotencyKey } });
  },
  async listSelectionNotebooks(workspaceId, { signal } = {}) {
    const payload = await listNotebooks(workspaceId, { signal, fetchImpl: selectionFetch });
    if (payload.meta.workspace_id !== workspaceId || new Set(payload.data.map(item => item.notebook_id)).size !== payload.data.length) throw sessionFailure();
    return payload.data.map(item => ({ id: item.notebook_id, title: item.title }));
  },
  async getSelectionSources(workspaceId, notebookId, { signal } = {}) {
    const payload = await getNotebookContext(workspaceId, notebookId, { signal, fetchImpl: selectionFetch });
    if (payload.meta.workspace_id !== workspaceId || new Set(payload.data.sources.map(item => item.source_version_id)).size !== payload.data.sources.length) throw sessionFailure();
    return { notebookId, bindingEtag: payload.data.bindingEtag,
      sources: payload.data.sources.map(item => ({ sourceId: item.source_id, versionId: item.source_version_id })) };
  },
  createBackupSelection(input, idempotencyKey, { signal } = {}) {
    if (!input || Object.keys(input).length !== 4 || !["workspace_id", "notebook_id", "trigger", "source_version_ids"].every(key => Object.hasOwn(input, key))
      || !selectionId(input.workspace_id) || !selectionId(input.notebook_id) || input.trigger !== "manual"
      || !Array.isArray(input.source_version_ids) || input.source_version_ids.length < 1 || input.source_version_ids.length > 100
      || !input.source_version_ids.every(selectionId) || new Set(input.source_version_ids).size !== input.source_version_ids.length
      || !selectionId(idempotencyKey) || idempotencyKey.length < 16) throw sessionFailure();
    return request("/bff/api/backups", { method: "POST", signal, body: {
      workspace_id: input.workspace_id, notebook_id: input.notebook_id, trigger: "manual", source_version_ids: [...input.source_version_ids].sort(),
    }, headers: { "Idempotency-Key": idempotencyKey } });
  },
  getOperationsStatus(workspaceId, { signal, scope="self" } = {}) {
    return getWorkspaceOperationsStatus(workspaceId, { signal, scope, fetchImpl: async (path, init) => {
      const response = await fetch(path, init);
      if (!response.ok) throw requestError(response, await response.json().catch(() => null));
      return response;
    } });
  },
  getSession({ signal } = {}) {
    return request("/bff/api/session", { signal });
  },
  listBackups(workspaceId) {
    return request(`/bff/api/backups?workspace_id=${encodeURIComponent(workspaceId)}`);
  },
  createBackup(input, idempotencyKey) {
    return request("/bff/api/backups", {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  getBackup(id, { signal } = {}) {
    return request(`/bff/api/backups/${encodeURIComponent(id)}`, { signal });
  },
  previewRestore(backupId, input, idempotencyKey) {
    return request(`/bff/api/backups/${encodeURIComponent(backupId)}/restore-previews`, {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  getRestore(id, { signal } = {}) {
    return request(`/bff/api/restore-requests/${encodeURIComponent(id)}`, { signal });
  },
  executeRestore(id, input, etag, idempotencyKey, { signal } = {}) {
    return request(`/bff/api/restore-requests/${encodeURIComponent(id)}/execute`, {
      method: "POST", body: input, signal,
      headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  },
  cancelRestore(id, etag, idempotencyKey, { signal } = {}) {
    return request(`/bff/api/restore-requests/${encodeURIComponent(id)}/cancel`, {
      method: "POST", signal, headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  }
});
