import { secureUuid } from "@daon-user/ui/secure-uuid";
import { parseImageRegion } from "@daon-user/ui/product-workspace-model";
import { parseSheetCell } from "./sheet-cell-locator.js";
import { parseDocxBlock } from "./docx-block-locator.js";
import { parsePptxBlock } from "./pptx-block-locator.js";
import { parseAudioSegment } from "./audio-segment-locator.js";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;

function requiredId(value) {
  if (typeof value !== "string" || !SAFE_ID.test(value)) {
    throw new Error("QUESTION_INPUT_INVALID");
  }
  return value;
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value, keys) {
  if (!isRecord(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isSafeId(value) {
  return typeof value === "string" && SAFE_ID.test(value);
}

function isStepUpToken(value) {
  return typeof value === "string" && value.length >= 1 && value.length <= 4096
    && !/[\s\u0000-\u001f\u007f]/u.test(value);
}

function isCitationLocator(value) {
  return hasExactKeys(value, ["kind", "value"])
    && typeof value.kind === "string"
    && /^[a-z][a-z0-9_]{0,31}$/u.test(value.kind)
    && typeof value.value === "string"
    && value.value.length >= 1
    && value.value.length <= 255
    && !/[\u0000-\u001f\u007f]/u.test(value.value)
    && (value.kind !== "image_region" || parseImageRegion(value.value) !== null)
    && (value.kind !== "sheet_cell" || parseSheetCell(value.value) !== null)
    && (value.kind !== "docx_block" || parseDocxBlock(value.value) !== null)
    && (value.kind !== "pptx_block" || parsePptxBlock(value.value) !== null)
    && (value.kind !== "audio_segment" || parseAudioSegment(value.value) !== null);
}

function isGroundedCitation(value) {
  return hasExactKeys(value, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page", "origin", "context_item_id", "locator"])
    && isSafeId(value.citation_id)
    && isSafeId(value.source_id)
    && isSafeId(value.source_version_id)
    && isSafeId(value.evidence_span_id)
    && ["raw_source", "daon_knowledge"].includes(value.origin)
    && isSafeId(value.context_item_id)
    && isCitationLocator(value.locator)
    && Number.isSafeInteger(value.page)
    && value.page >= 1
    && (value.locator.kind !== "page" || value.locator.value === String(value.page));
}

function isQuestionResponseMetadata(value) {
  const modeOk = ["work_support", "explicit_source_lookup", "source_backed_action", "approved_web_research"].includes(value.mode);
  const groundingOk = ["source_backed", "ungrounded", "source_evidence_unavailable", "web_backed"].includes(value.grounding);
  const summaryOk = value.source_scope_summary === null
    || typeof value.source_scope_summary === "string"
      && value.source_scope_summary.length <= 1_000
      && !/[\u0000-\u001f\u007f]/u.test(value.source_scope_summary);
  const mismatchOk = value.mismatch === null || (
    hasExactKeys(value.mismatch, ["code", "detail"])
    && value.mismatch.code === "SOURCE_SCOPE_MISMATCH"
    && typeof value.mismatch.detail === "string"
    && value.mismatch.detail.length >= 1
    && value.mismatch.detail.length <= 1_000
  );
  const actionsOk = Array.isArray(value.next_actions)
    && value.next_actions.length <= 4
    && value.next_actions.every((item) => typeof item === "string" && item.length >= 1 && item.length <= 120);
  return modeOk && groundingOk && summaryOk && mismatchOk && actionsOk
    && (value.grounding !== "source_evidence_unavailable" || value.mismatch !== null && value.next_actions.length > 0);
}

function knowledgeContextBody(value) {
  if (!isRecord(value) || !["raw_only", "daon_priority", "mixed"].includes(value.mode)) {
    throw new Error("QUESTION_INPUT_INVALID");
  }
  const inputResources = Array.isArray(value.resources) ? value.resources : [];
  if (inputResources.length < 1 || inputResources.length > 8) {
    throw new Error("QUESTION_INPUT_INVALID");
  }
  const resources = inputResources.map((item) => {
    if (!isRecord(item) || !/^[a-z][a-z0-9_]{0,63}$/u.test(item.resourceKind || "")) {
      throw new Error("QUESTION_INPUT_INVALID");
    }
    return {
      resource_kind: item.resourceKind,
      resource_id: requiredId(item.resourceId),
      ...(item.versionId ? { version_id: requiredId(item.versionId) } : {}),
    };
  });
  const knowledgeCount = resources.filter((item) => item.resource_kind === "knowledge_package").length;
  const otherCount = resources.length - knowledgeCount;
  const valid = value.mode === "raw_only" && otherCount && !knowledgeCount
    || value.mode === "daon_priority" && knowledgeCount
    || value.mode === "mixed" && otherCount && knowledgeCount;
  if (!valid) throw new Error("QUESTION_INPUT_INVALID");
  return { mode: value.mode, resources };
}

function questionSourceBody({ sourceId, sourceVersionId, knowledgeContext }) {
  if (knowledgeContext) {
    if (sourceId || sourceVersionId) throw new Error("QUESTION_INPUT_INVALID");
    return { knowledge_context: knowledgeContextBody(knowledgeContext) };
  }
  if (sourceId || sourceVersionId) {
    return { source_id: requiredId(sourceId), source_version_id: requiredId(sourceVersionId) };
  }
  // Source 미선택 질문은 일반 업무 상담/LLM 경로로 전달한다. 질문의
  // 의미를 클라이언트 allowlist로 판정하지 않고, 서버가 일반 상담과
  // 선택된 Source grounding을 최종 분기한다.
  return {};
}

function isGroundedAnswer(value) {
  const baseKeys = ["run_id", "run_result_id", "answer", "insufficient", "citations"];
  const metadataKeys = ["mode", "grounding", "source_scope_summary", "mismatch", "next_actions"];
  const legacyShape = hasExactKeys(value, baseKeys);
  const enrichedShape = hasExactKeys(value, [...baseKeys, ...metadataKeys]);
  return (legacyShape || enrichedShape)
    && (!enrichedShape || isQuestionResponseMetadata(value))
    && isSafeId(value.run_id)
    && isSafeId(value.run_result_id)
    && typeof value.answer === "string"
    && value.answer.length >= 1
    && value.answer.length <= 8_000
    && typeof value.insufficient === "boolean"
    && Array.isArray(value.citations)
    && value.citations.length <= 10
    && value.citations.every(isGroundedCitation);
}

async function responseData(response, signal) {
  signal?.throwIfAborted();
  const contentType = response.headers?.get?.("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (contentType !== "application/json") {
    if (response.status === 504) throw new Error("GATEWAY_TIMEOUT");
    if (!response.ok) throw new Error("UPSTREAM_FAILURE");
    throw new Error("QUESTION_RESPONSE_INVALID");
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    signal?.throwIfAborted();
    throw new Error("QUESTION_RESPONSE_INVALID");
  }
  signal?.throwIfAborted();
  if (!response.ok) {
    const code = payload?.error?.code;
    throw new Error(typeof code === "string" ? code : "QUESTION_FAILED");
  }
  if (
    !hasExactKeys(payload, ["data", "meta"])
    || !isGroundedAnswer(payload.data)
    || !isRecord(payload.meta)
    || !isSafeId(payload.meta.trace_id)
    || !isSafeId(payload.meta.workspace_id)
  ) {
    throw new Error("QUESTION_RESPONSE_INVALID");
  }
  return payload.data;
}

export async function askGroundedQuestion(
  workspaceId,
  { notebookId, sourceId, sourceVersionId, knowledgeContext, question, routingMode, routingPolicyVersionId },
  { fetchImpl = fetch, idempotencyKey = secureUuid(), stepUpAuthorizationId, routingPlanId, routingPlanAuthorization, signal } = {},
) {
  const workspace = requiredId(workspaceId);
  const notebook = requiredId(notebookId);
  const sourceBody = questionSourceBody({ sourceId, sourceVersionId, knowledgeContext });
  const routeBody = routingQuestionBody({routingMode,routingPolicyVersionId,routingPlanId,routingPlanAuthorization,stepUpAuthorizationId},true);
  if (
    typeof question !== "string" || !question.trim()
    || question.length > 2_000 || !SAFE_ID.test(idempotencyKey)
    || stepUpAuthorizationId !== undefined && !isStepUpToken(stepUpAuthorizationId)
  ) {
    throw new Error("QUESTION_INPUT_INVALID");
  }
  signal?.throwIfAborted();
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/questions`,
    {
      method: "POST",
      credentials: "same-origin", cache: "no-store", signal,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({
        notebook_id: notebook, ...sourceBody, question: question.trim(), ...routeBody,
        ...(stepUpAuthorizationId ? { step_up_authorization_id: stepUpAuthorizationId } : {}),
      }),
    },
  );
  return responseData(response, signal);
}

export async function authorizeGroundedQuestion(
  workspaceId, { notebookId, sourceId, sourceVersionId, knowledgeContext, question, password, routingMode, routingPolicyVersionId },
  { fetchImpl = fetch, idempotencyKey, signal } = {},
) {
  const workspace = requiredId(workspaceId);
  const notebook = requiredId(notebookId);
  const routeBody=routingQuestionBody({routingMode,routingPolicyVersionId},false);
  if (!SAFE_ID.test(idempotencyKey || "") || (routingMode !== "auto" && (typeof password !== "string" || !password))
    || (password !== undefined && (typeof password !== "string" || !password))
    || typeof question !== "string" || !question.trim() || question.length > 2_000) {
    throw new Error("QUESTION_AUTHORIZATION_INPUT_INVALID");
  }
  signal?.throwIfAborted();
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/questions/authorization`,
    { method: "POST", credentials: "same-origin", cache: "no-store", signal, headers: {
      "Content-Type": "application/json", "Idempotency-Key": idempotencyKey,
    }, body: JSON.stringify({
      notebook_id: notebook,
      ...questionSourceBody({ sourceId, sourceVersionId, knowledgeContext }),
      question: question.trim(), ...routeBody, ...(password !== undefined ? {password} : {}),
    }) },
  );
  signal?.throwIfAborted();
  let payload;
  try { payload = await response.json(); }
  catch { signal?.throwIfAborted(); throw new Error("QUESTION_AUTHORIZATION_RESPONSE_INVALID"); }
  signal?.throwIfAborted();
  if (!response.ok) throw new Error(payload?.error?.code || "QUESTION_AUTHORIZATION_FAILED");
  if(routingMode === "auto") {
    const data=payload?.data;
    if(!hasExactKeys(payload,['data','meta']) || !hasExactKeys(payload.meta,['trace_id']) || !isSafeId(payload.meta.trace_id)
      || !hasExactKeys(data,["routing_plan_id","routing_plan_authorization","expires_at","run_id","plan_digest","requires_step_up","routing_preview"])
      || !isSafeId(data.routing_plan_id) || !isSafeId(data.run_id) || !isStepUpToken(data.routing_plan_authorization)
      || !isDate(data.expires_at) || typeof data.requires_step_up !== "boolean"
      || !isRoutingPreview(data.routing_preview) || data.plan_digest !== data.routing_preview.plan_digest
      || data.routing_preview.routing_policy_version_id !== routingPolicyVersionId) throw new Error("QUESTION_AUTHORIZATION_RESPONSE_INVALID");
  } else if (!isStepUpToken(payload?.data?.step_up_authorization_id)) throw new Error("QUESTION_AUTHORIZATION_RESPONSE_INVALID");
  return payload.data;
}

export function citationContentUrl(workspaceId, citation, { notebookId } = {}) {
  const workspace = requiredId(workspaceId);
  const citationId = requiredId(citation?.citation_id);
  const notebook = requiredId(notebookId);
  const page = Number(citation?.page);
  if (!Number.isSafeInteger(page) || page < 1) throw new Error("CITATION_INPUT_INVALID");
  const base = `/bff/api/workspaces/${encodeURIComponent(workspace)}/citations/${encodeURIComponent(citationId)}/content?notebook_id=${encodeURIComponent(notebook)}`;
  return citation?.locator?.kind === "page" ? `${base}#page=${page}` : base;
}
import { isGeneralConversationIntent } from "@daon-user/ui/conversation-intent";


const HEX=/^[0-9a-f]{64}$/u;
const EXCLUSION_CODES=new Set(['OBSERVATION_MISSING','OBSERVATION_BINDING_INVALID','BUDGET_UNVERIFIED','OBSERVATION_IDENTITY_MISMATCH',
 'PROVENANCE_UNCONFIRMED','OBSERVATION_STALE','OBSERVATION_INCOMPARABLE','OBSERVATION_UNIT_INVALID','QUALITY_BELOW_MINIMUM',
 'CURRENT_CANDIDATE_BLOCKED','PRICE_UNCONFIRMED','BUDGET_EXCEEDED','DATA_REALM_BLOCKED']);
const ATTEMPT_CODES=new Set(['RATE_LIMITED','TEMPORARILY_UNAVAILABLE','CAPACITY_UNAVAILABLE','TIMEOUT','AUTH_DENIED','EGRESS_POLICY_DENIED',
 'POLICY_CHANGED','INVALID_REQUEST','INVALID_RESPONSE','CANCELLED','TRANSPORT_UNCERTAIN','RESULT_PERSISTENCE_UNCERTAIN']);
const isDate=value=>typeof value==='string' && /(?:Z|[+-]\d{2}:\d{2})$/u.test(value) && Number.isFinite(Date.parse(value));
const isModel=value=>typeof value==='string' && value.length>0 && value.length<=255 && !/[\u0000-\u001f\u007f]/u.test(value);
const isOrder=value=>Number.isInteger(value) && value>=1 && value<=8;
function isCandidates(items) {
 return Array.isArray(items) && items.length<=8 && items.every((x,i)=>hasExactKeys(x,['candidate_order','model_id','deployment_id','provider_kind'])
  && x.candidate_order===i+1 && isModel(x.model_id) && isSafeId(x.deployment_id) && ['local','local_runtime','server_internal','external_api'].includes(x.provider_kind));
}
function isExclusions(items) {
 return Array.isArray(items) && items.length<=8 && items.every(x=>hasExactKeys(x,['deployment_id','reason']) && isSafeId(x.deployment_id) && EXCLUSION_CODES.has(x.reason));
}
function isRoutingPreview(value) {
 return hasExactKeys(value,['schema_version','routing_policy_version_id','plan_digest','candidates','exclusions']) && value.schema_version===1
  && isSafeId(value.routing_policy_version_id) && HEX.test(value.plan_digest) && isCandidates(value.candidates) && value.candidates.length>0 && isExclusions(value.exclusions);
}
function routingQuestionBody({routingMode,routingPolicyVersionId,routingPlanId,routingPlanAuthorization,stepUpAuthorizationId},execute) {
 if(routingMode !== undefined && !['pinned','auto'].includes(routingMode)) throw new Error('QUESTION_INPUT_INVALID');
 if(routingMode !== 'auto') {
  if([routingPolicyVersionId,routingPlanId,routingPlanAuthorization].some(x=>x!==undefined))throw new Error('QUESTION_INPUT_INVALID');
  return {};
 }
 if(!isSafeId(routingPolicyVersionId) || stepUpAuthorizationId!==undefined)throw new Error('QUESTION_INPUT_INVALID');
 const result={routing_mode:'auto',routing_policy_version_id:routingPolicyVersionId};
 if(execute){if(!isSafeId(routingPlanId)||!isStepUpToken(routingPlanAuthorization))throw new Error('QUESTION_INPUT_INVALID');
  result.routing_plan_id=routingPlanId;result.routing_plan_authorization=routingPlanAuthorization;}
 return result;
}
function isRoutingRead(value,runId) {
 if(!hasExactKeys(value,['schema_version','run_id','routing_mode','routing_policy_version_id','routing_plan_id','plan_digest','run_state','candidates','exclusions','attempts','final_selection'])
   || value.schema_version!==1 || value.run_id!==runId || !['auto','pinned'].includes(value.routing_mode)
   || !['accepted','planning','retrieving','generating','validating','completed','failed','cancelled'].includes(value.run_state)
   || !isCandidates(value.candidates) || !isExclusions(value.exclusions) || !Array.isArray(value.attempts) || value.attempts.length>8)return false;
 if(value.routing_mode==='auto' ? !isSafeId(value.routing_policy_version_id)||!isSafeId(value.routing_plan_id)||!HEX.test(value.plan_digest)
   : [value.routing_policy_version_id,value.routing_plan_id,value.plan_digest].some(x=>x!==null))return false;
 const ids=new Set(),orders=new Set();
 for(const x of value.attempts) {
  if(!hasExactKeys(x,['attempt_id','candidate_order','state','started_at','finished_at','failure_code','cost_state','cost_amount','currency'])
    || !isSafeId(x.attempt_id)||!isOrder(x.candidate_order)||x.candidate_order>value.candidates.length||ids.has(x.attempt_id)||orders.has(x.candidate_order)
    || !['succeeded','failed','uncertain','dispatch_claimed'].includes(x.state)||!isDate(x.started_at)
    || (x.finished_at!==null && (!isDate(x.finished_at)||Date.parse(x.finished_at)<Date.parse(x.started_at)))
    || (x.failure_code!==null && !ATTEMPT_CODES.has(x.failure_code)) || !['known','unknown','uncertain'].includes(x.cost_state))return false;
  if(x.cost_state==='known' ? typeof x.cost_amount!=='string'||!/^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$/u.test(x.cost_amount)||!(/^[A-Z]{3}$/u.test(x.currency??''))
    : x.cost_amount!==null||x.currency!==null)return false;
  ids.add(x.attempt_id);orders.add(x.candidate_order);
 }
 const final=value.final_selection;
 return final===null || value.run_state==='completed' && hasExactKeys(final,['candidate_order','model_id','attempt_id'])
  && value.attempts.some(x=>x.attempt_id===final.attempt_id && x.candidate_order===final.candidate_order && x.state==='succeeded')
  && value.candidates[final.candidate_order-1]?.model_id===final.model_id;
}
async function routingResponse(response,workspaceId,signal) {
 signal?.throwIfAborted();let payload;
 try{payload=await response.json();}catch{throw new Error('ROUTING_RESPONSE_INVALID');}
 signal?.throwIfAborted();
 if(!response.ok)throw new Error(/^[A-Z][A-Z0-9_]{2,63}$/u.test(payload?.error?.code??'')?payload.error.code:'ROUTING_EXECUTION_UNAVAILABLE');
 if(!hasExactKeys(payload,['data','meta']) || !isRecord(payload.meta) || payload.meta.workspace_id!==workspaceId || !isSafeId(payload.meta.trace_id))throw new Error('ROUTING_RESPONSE_INVALID');
 return payload.data;
}
export async function getRunRoutingDecision(workspaceId,runId,{fetchImpl=fetch,signal}={}) {
 requiredId(workspaceId);requiredId(runId);signal?.throwIfAborted();
 const response=await fetchImpl(`/bff/api/runs/${encodeURIComponent(runId)}/routing-decision?workspace_id=${encodeURIComponent(workspaceId)}`,{credentials:'same-origin',cache:'no-store',signal});
 const data=await routingResponse(response,workspaceId,signal);
 if(!isRoutingRead(data,runId))throw new Error('ROUTING_RESPONSE_INVALID');return data;
}
function isRoutingPolicyBody(body) {
 const positive=(v,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=1&&v<=max;
 const reference=v=>isSafeId(v)&&v.length<=128;
 if(!hasExactKeys(body,['schema_version','expected_version','enabled','capability','candidates','ranking_profile','fallback_policy','budget'])
  || body.schema_version!==1 || !Number.isSafeInteger(body.expected_version)||body.expected_version<0 || typeof body.enabled!=='boolean'
  || body.capability!=='text_generation' || !Array.isArray(body.candidates)||!positive(body.candidates.length,8))return false;
 const seen=new Set();
 for(const c of body.candidates){
  if(!hasExactKeys(c,['connection_id','model_id','credential_source','connection_version','catalog_version','credential_version','endpoint_version'])
   || !reference(c.connection_id)||!isModel(c.model_id)||/[\s]/u.test(c.model_id)||!['workspace','personal'].includes(c.credential_source)
   || !positive(c.connection_version)||!positive(c.catalog_version)||!positive(c.endpoint_version)||!Number.isSafeInteger(c.credential_version)||c.credential_version<0)return false;
  const key=JSON.stringify([c.connection_id,c.model_id]);if(seen.has(key))return false;seen.add(key);
 }
 const profile=body.ranking_profile;
 if(!hasExactKeys(profile,['version','evaluation_set','minimum_quality','observation_window','input_class','minimum_samples','max_age_seconds','tier_mapping_version','locality_mapping_version'])
  || !['version','evaluation_set','observation_window','input_class','tier_mapping_version','locality_mapping_version'].every(k=>reference(profile[k]))
  || !Number.isSafeInteger(profile.minimum_quality)||profile.minimum_quality<0||profile.minimum_quality>10000||!positive(profile.minimum_samples,10000000)||!positive(profile.max_age_seconds,3600))return false;
 return hasExactKeys(body.fallback_policy,['mode'])&&body.fallback_policy.mode==='transient_only'
  && hasExactKeys(body.budget,['currency','run_limit','max_attempts'])&&/^[A-Z]{3}$/u.test(body.budget.currency??'')
  && typeof body.budget.run_limit==='string'&&/^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$/u.test(body.budget.run_limit)
  && positive(body.budget.max_attempts,body.candidates.length);
}
export async function getRoutingPolicy(workspaceId,{fetchImpl=fetch,signal}={}) {
 requiredId(workspaceId);signal?.throwIfAborted();
 const response=await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/routing-policy`,{credentials:'same-origin',cache:'no-store',signal});
 const data=await routingResponse(response,workspaceId,signal),etag=response.headers?.get?.('etag');
 if(!hasExactKeys(data,['version','policy_version_id','digest_sha256','body','execution_enabled','readiness']) || !Number.isSafeInteger(data.version)||data.version<0
   || data.execution_enabled!==false || !['policy_absent','disabled','observations_unavailable'].includes(data.readiness)||!etag
   || (data.version===0 ? data.policy_version_id!==null||data.digest_sha256!==null||data.body!==null : !isSafeId(data.policy_version_id)||!HEX.test(data.digest_sha256)||!isRoutingPolicyBody(data.body)))throw new Error('ROUTING_RESPONSE_INVALID');
 return {...data,etag};
}
