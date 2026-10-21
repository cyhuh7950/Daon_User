import {getRoutingPolicy,saveRoutingPolicy} from "./question-answering-api.js";
const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });

async function request(path, { method = "GET", body, headers = {} } = {}) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? headers : { ...JSON_HEADERS, ...headers },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({
    error: { code: "RESOURCE_UNAVAILABLE", trace_id: response.headers.get("x-trace-id") }
  }));
  if (!response.ok) {
    const error = new Error(payload?.error?.code ?? "RESOURCE_UNAVAILABLE");
    error.code = payload?.error?.code ?? "RESOURCE_UNAVAILABLE";
    error.traceId = payload?.error?.trace_id ?? response.headers.get("x-trace-id");
    throw error;
  }
  return { payload, etag: response.headers.get("etag") };
}


// 기존 서버 조회의 안전 참조만 결합한다. 이 목록은 사용 권한이나 실행 준비의 증명이 아니다.
function projectRoutingCandidates(metadata,existing=[]) {
  const record=x=>x!==null && typeof x==='object' && !Array.isArray(x);
  const id=x=>typeof x==='string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(x);
  const modelId=x=>typeof x==='string' && x.length>0 && x.length<=255 && !/[\x00-\x20\x7f]/u.test(x);
  const version=(x,min=1)=>Number.isSafeInteger(x) && x>=min && x<=2147483647;
  const unique=(items,key)=>Array.isArray(items) && new Set(items.map(key)).size===items.length;
  const capabilities=x=>Array.isArray(x) && x.every(v=>typeof v==='string') && new Set(x).size===x.length && x.includes('text_generation');
  if(!record(metadata) || !id(metadata.workspaceId) || metadata.metadataWorkspaceId!==metadata.workspaceId
    || !record(metadata.modelDefaults) || metadata.modelDefaults.workspace_id!==metadata.workspaceId
    || !Array.isArray(existing) || existing.length>=8 || !existing.every(record)
    || !unique(metadata.connections,x=>x?.connection_id) || !unique(metadata.credentials,x=>x?.connection_id)
    || !metadata.credentials.every(x=>record(x) && id(x.connection_id))
    || !unique(metadata.modelDefaults.available_models,x=>JSON.stringify([x?.connection_id,x?.model_id])))return [];
  const result=[];
  for(const available of metadata.modelDefaults.available_models){
    if(!record(available) || !id(available.connection_id) || !modelId(available.model_id) || available.catalog_status!=='ready' || !capabilities(available.effective_capabilities))continue;
    const connection=metadata.connections.find(x=>x?.connection_id===available.connection_id);
    if(!record(connection) || connection.enabled!==true || !['public','personal'].includes(connection.access_mode)
      || !['required','none'].includes(connection.credential_requirement) || !/^[A-Z][A-Z0-9_]{0,63}$/u.test(connection.provider_code??'')
      || available.provider_code!==connection.provider_code || available.connection_version!==connection.version || !version(connection.version)
      || !version(connection.credential_version,0) || available.credential_version!==connection.credential_version
      || typeof connection.configured!=='boolean' || available.configured!==connection.configured
      || available.verification_status!==connection.verification_status
      || !unique(connection.allowed_model_ids,x=>x) || !connection.allowed_model_ids.every(modelId) || !connection.allowed_model_ids.includes(available.model_id)
      || !unique(connection.models,x=>x?.model_id))continue;
    const model=connection.models.find(x=>x?.model_id===available.model_id);
    if(!record(model) || model.catalog_status!=='ready' || !version(model.catalog_version)
      || model.catalog_version!==available.catalog_version || !capabilities(model.effective_capabilities))continue;
    let credentialVersion=connection.credential_version;
    if(connection.access_mode==='public'){
      if(connection.verification_status!=='verified' || (credentialVersion===0
        ? connection.credential_requirement!=='none' || connection.configured!==false : connection.configured!==true))continue;
    }else{
      const personal=metadata.credentials.find(x=>x?.connection_id===connection.connection_id);
      if(connection.credential_requirement!=='required' || !record(personal) || personal.provider_code!==connection.provider_code
        || personal.configured!==true || personal.verification_status!=='verified' || !version(personal.credential_version))continue;
      credentialVersion=personal.credential_version;
    }
    if(existing.some(x=>x.connection_id===available.connection_id && x.model_id===available.model_id))continue;
    result.push({connection_id:connection.connection_id,model_id:model.model_id,credential_source:connection.access_mode==='personal'?'personal':'workspace',
      connection_version:connection.version,catalog_version:model.catalog_version,credential_version:credentialVersion,endpoint_version:connection.version});
  }
  return result;
}

export const providerSettingsApi = Object.freeze({
  projectRoutingCandidates,
  getRoutingPolicy(workspaceId,options) { return getRoutingPolicy(workspaceId,{...options,includePermissions:true}); },
  saveRoutingPolicy(workspaceId,body,options) { return saveRoutingPolicy(workspaceId,body,options); },
  getSession() {
    return request("/bff/api/session");
  },
  listConnections() {
    return request("/bff/api/admin/provider-connections");
  },
  previewModels(input) {
    return request("/bff/api/admin/provider-connections/model-preview", {
      method: "POST", body: input,
    });
  },
  getHealthSettings() {
    return request("/bff/api/admin/provider-health-settings");
  },
  saveHealthSettings(input) {
    return request("/bff/api/admin/provider-health-settings", {
      method: "PATCH", body: input,
    });
  },
  listUserCredentials() {
    return request("/bff/api/provider-credentials");
  },
  replaceUserCredential(connectionId, input) {
    return request(`/bff/api/provider-credentials/${encodeURIComponent(connectionId)}`, {
      method: "PUT", body: input,
    });
  },
  deleteUserCredential(connectionId, input) {
    return request(`/bff/api/provider-credentials/${encodeURIComponent(connectionId)}`, {
      method: "DELETE", body: input,
    });
  },
  createConnection(input, idempotencyKey) {
    return request("/bff/api/admin/provider-connections", {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  updateConnection(connectionId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-connections/${encodeURIComponent(connectionId)}`, {
      method: "PUT", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  replaceCredential(connectionId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-connections/${encodeURIComponent(connectionId)}/credential`, {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  deleteCredential(connectionId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-connections/${encodeURIComponent(connectionId)}/credential`, {
      method: "DELETE", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  deleteConnection(connectionId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-connections/${encodeURIComponent(connectionId)}`, {
      method: "DELETE", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  refreshCatalog(connectionId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-catalog/${encodeURIComponent(connectionId)}/refresh`, {
      method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  correctCapabilities(connectionId, modelId, input, idempotencyKey) {
    return request(`/bff/api/admin/provider-models/${encodeURIComponent(connectionId)}/${encodeURIComponent(modelId)}/capabilities`, {
      method: "PATCH", body: input, headers: { "Idempotency-Key": idempotencyKey }
    });
  },
  getModelDefaults(workspaceId) {
    return request(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/model-defaults`);
  },
  saveModelDefault(workspaceId, input, etag, idempotencyKey) {
    return request(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/model-defaults`, {
      method: "PATCH",
      body: input,
      headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey }
    });
  }
});
