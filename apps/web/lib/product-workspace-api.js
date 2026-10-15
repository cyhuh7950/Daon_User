"use client";

import { secureUuid } from "@daon-user/ui/secure-uuid";
import { parseImageRegion } from "@daon-user/ui/product-workspace-model";
import { parseSheetCell } from "./sheet-cell-locator.js";
import { parseDocxBlock } from "./docx-block-locator.js";
import { parsePptxBlock } from "./pptx-block-locator.js";
import { parseAudioSegment } from "./audio-segment-locator.js";
import { validWebsiteSnapshot, readWebsiteJson } from "./website-source-api.js";
import { citationContentUrl } from './question-answering-api.js';

const LAW_FIELDS = ["mst","law_id","title","effective_date","promulgation_date","ministry"];
const LAW_SNAPSHOT_FIELDS = ["snapshot_id","connector_id","source_id","source_version_id",...LAW_FIELDS,"article_code",
  "origin_uri","fetched_at","raw_sha256","text_sha256","extraction_version","license_status"];
const LAW_HASH = /^[a-f0-9]{64}$/u;
const LAW_ERRORS=new Set(['OPEN_LAW_INPUT_INVALID','OPEN_LAW_ACCESS_DENIED','OPEN_LAW_EGRESS_BLOCKED','OPEN_LAW_NOT_FOUND',
  'OPEN_LAW_PREVIEW_CHANGED','OPEN_LAW_IMPORT_CONFLICT','OPEN_LAW_IMPORT_IN_PROGRESS','OPEN_LAW_BINDING_CONFLICT',
  'OPEN_LAW_CONTENT_TOO_LARGE','OPEN_LAW_TEXT_UNAVAILABLE','OPEN_LAW_PROTOCOL_INVALID','OPEN_LAW_UPSTREAM_FAILED',
  'OPEN_LAW_CREDENTIAL_REQUIRED','OPEN_LAW_UNAVAILABLE','OPEN_LAW_BUSY','OPEN_LAW_STORAGE_UNAVAILABLE','OPEN_LAW_TIMEOUT']);
const lawDate = value => typeof value === 'string' && /^[0-9]{8}$/u.test(value)
  && Number.isFinite(Date.parse(`${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}T00:00:00Z`))
  && new Date(`${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}T00:00:00Z`).toISOString().slice(0,10).replaceAll('-','') === value;
const lawId = value => typeof value === 'string' && /^[0-9]{1,20}$/u.test(value);
const lawBoundId = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(value);
const lawString = (value,max) => typeof value === 'string' && value.trim().length>0 && Array.from(value).length<=max && !/[\u0000-\u001f\u007f]/u.test(value);
const lawSelection = value => lawId(value.mst) && lawDate(value.effective_date)
  && (value.article_code === null || typeof value.article_code === 'string' && /^[0-9]{6}$/u.test(value.article_code));
function validLaw(value, article=true) {
  return exact(value,article?[...LAW_FIELDS,'article_code']:LAW_FIELDS) && lawId(value.mst) && lawId(value.law_id)
    && lawString(value.title,300) && lawDate(value.effective_date)
    && (value.promulgation_date===null || lawDate(value.promulgation_date))
    && (value.ministry===null || lawString(value.ministry,200)) && (!article || lawSelection(value));
}
const lawPair = (source,version) => /^law-source-[0-9a-f]{32}$/u.test(source)
  && version===`law-version-${source.slice('law-source-'.length)}`;
const lawStamp = value => typeof value === 'string' && value.length<=64 && /^\d{4}-\d{2}-\d{2}T/u.test(value) && Number.isFinite(Date.parse(value));
function lawSha256(bytes) {
  // SHA-256 on the already bounded UTF-8 bytes, including HTTP development
  // origins where SubtleCrypto is unavailable. No server hash is trusted alone.
  const constants=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const state=new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
  const padded=new Uint8Array(Math.ceil((bytes.length+9)/64)*64);padded.set(bytes);padded[bytes.length]=0x80;
  const view=new DataView(padded.buffer);view.setUint32(padded.length-4,bytes.length*8);
  const words=new Uint32Array(64),rotate=(value,count)=>(value>>>count)|(value<<(32-count));
  for(let offset=0;offset<padded.length;offset+=64){
    for(let i=0;i<16;i++)words[i]=view.getUint32(offset+i*4);
    for(let i=16;i<64;i++){const x=words[i-15],y=words[i-2];words[i]=(words[i-16]+(rotate(x,7)^rotate(x,18)^(x>>>3))+words[i-7]+(rotate(y,17)^rotate(y,19)^(y>>>10)))>>>0;}
    let [a,b,c,d,e,f,g,h]=state;
    for(let i=0;i<64;i++){const t1=(h+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+constants[i]+words[i])>>>0;
      const t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))>>>0;
      h=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;}
    [a,b,c,d,e,f,g,h].forEach((value,i)=>{state[i]=(state[i]+value)>>>0;});
  }
  return Array.from(state,value=>value.toString(16).padStart(8,'0')).join('');
}
async function lawText(text,digest) {
  if(typeof text!=='string' || !text.trim() || !LAW_HASH.test(digest))return false;
  const bytes=new TextEncoder().encode(text);if(bytes.length>524288)return false;
  return lawSha256(bytes)===digest;
}
async function lawResponse(response,signal) {
  const payload=await readWebsiteJson(response,4*1024*1024,'OPEN_LAW_RESPONSE_INVALID',signal);
  if(!response.ok){
    const code=payload?.error?.code;
    throw Error(LAW_ERRORS.has(code) ? code : 'OPEN_LAW_REQUEST_FAILED');
  }
  if(!exact(payload,['data','meta']) || !exact(payload.meta,['trace_id']) || !lawBoundId(payload.meta.trace_id))throw Error('OPEN_LAW_RESPONSE_INVALID');
  return payload.data;
}
async function lawPost(workspaceId,route,input,{fetchImpl=fetch,signal,idempotencyKey}={}) {
  if(!lawBoundId(workspaceId))throw Error('OPEN_LAW_INPUT_INVALID');
  const headers={'Content-Type':'application/json'};if(idempotencyKey)headers['Idempotency-Key']=idempotencyKey;
  const response=await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/connectors/mcp-open-law-go-kr/${route}`,
    {method:'POST',credentials:'same-origin',cache:'no-store',headers,body:JSON.stringify(input),signal});
  return {data:await lawResponse(response,signal),status:response.status};
}
export async function searchOpenLaw(workspaceId,input,options={}) {
  if(!exact(input,['notebook_id','query','page','public_query_confirmed']) || !lawBoundId(input.notebook_id)
    || !lawString(input.query,100) || !Number.isSafeInteger(input.page) || input.page<1 || input.page>100 || input.public_query_confirmed!==true)throw Error('OPEN_LAW_INPUT_INVALID');
  const {data,status}=await lawPost(workspaceId,'law-search',input,options);
  if(status!==200 || !exact(data,['items','page','total_count']) || !Array.isArray(data.items) || data.items.length>20
    || !data.items.every(item=>validLaw(item,false)) || data.page!==input.page || !Number.isSafeInteger(data.total_count) || data.total_count<0)throw Error('OPEN_LAW_RESPONSE_INVALID');
  return data;
}
export async function previewOpenLaw(workspaceId,input,options={}) {
  if(!exact(input,['notebook_id','mst','effective_date','article_code']) || !lawBoundId(input.notebook_id) || !lawSelection(input))throw Error('OPEN_LAW_INPUT_INVALID');
  const {data,status}=await lawPost(workspaceId,'law-preview',input,options);
  if(status!==200 || !exact(data,['law','text','raw_sha256','text_sha256','fetched_at','extraction_version']) || !validLaw(data.law)
    || ['mst','effective_date','article_code'].some(key=>data.law[key]!==input[key]) || !LAW_HASH.test(data.raw_sha256)
    || !lawStamp(data.fetched_at) || data.extraction_version!=='open-law-text-v1' || !await lawText(data.text,data.text_sha256))throw Error('OPEN_LAW_RESPONSE_INVALID');
  return data;
}
export async function importOpenLaw(workspaceId,input,options={}) {
  if(!exact(input,['notebook_id','mst','effective_date','article_code','expected_raw_sha256','expected_text_sha256']) || !lawBoundId(input.notebook_id)
    || !lawSelection(input) || !LAW_HASH.test(input.expected_raw_sha256) || !LAW_HASH.test(input.expected_text_sha256)
    || !safeIdempotencyKey(options.idempotencyKey))throw Error('OPEN_LAW_INPUT_INVALID');
  const {data,status}=await lawPost(workspaceId,'law-imports',input,options),result=data?.attributes;
  if(!exact(data,['kind','id','version','attributes']) || data.kind!=='open_law_import' || data.version!=='1'
    || !exact(result,['snapshot_id','source_id','source_version_id','source_state','replayed']) || !/^law-snapshot-[0-9a-f]{32}$/u.test(result.snapshot_id)
    || data.id!==result.snapshot_id || !lawPair(result.source_id,result.source_version_id) || result.source_state!=='ready'
    || typeof result.replayed!=='boolean' || status!==(result.replayed?200:201))throw Error('OPEN_LAW_RESPONSE_INVALID');
  return result;
}
export async function getOpenLawSnapshot(workspaceId,sourceVersionId,{notebookId,fetchImpl=fetch,signal}={}) {
  if(!lawBoundId(workspaceId) || !lawBoundId(notebookId) || !/^law-version-[0-9a-f]{32}$/u.test(sourceVersionId))throw Error('OPEN_LAW_INPUT_INVALID');
  const response=await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/source-versions/${encodeURIComponent(sourceVersionId)}/law-snapshot?notebook_id=${encodeURIComponent(notebookId)}`,
    {method:'GET',credentials:'same-origin',cache:'no-store',signal});
  const data=await lawResponse(response,signal),s=data?.snapshot;
  if(response.status!==200 || !exact(data,['snapshot','text']) || !exact(s,LAW_SNAPSHOT_FIELDS) || !validLaw(Object.fromEntries([...LAW_FIELDS,'article_code'].map(key=>[key,s[key]])))
    || !/^law-snapshot-[0-9a-f]{32}$/u.test(s.snapshot_id) || s.connector_id!=='mcp-open-law-go-kr' || !lawPair(s.source_id,s.source_version_id)
    || s.source_version_id!==sourceVersionId || s.origin_uri!==`https${URL_SEPARATOR}www.law.go.kr/LSW/lsInfoP.do?lsiSeq=${s.mst}&efYd=${s.effective_date}`
    || !lawStamp(s.fetched_at) || !LAW_HASH.test(s.raw_sha256) || s.extraction_version!=='open-law-text-v1' || s.license_status!=='unknown'
    || !await lawText(data.text,s.text_sha256))throw Error('OPEN_LAW_RESPONSE_INVALID');
  return data;
}

export async function getOpenLawCitation(workspaceId,citation,{notebookId,fetchImpl=fetch,signal}={}) {
  const keys=['citation_id','source_id','source_version_id','evidence_span_id','page','origin','context_item_id','locator'];
  if(!(exact(citation,keys) || exact(citation,[...keys,'content_url'])) || !lawBoundId(workspaceId) || !lawBoundId(notebookId)
    || !lawBoundId(citation.citation_id) || !lawPair(citation.source_id,citation.source_version_id)
    || citation.origin!=='raw_source' || citation.context_item_id!==citation.source_id || citation.page!==1
    || !/^law-span-[0-9a-f]{32}$/u.test(citation.evidence_span_id) || !exact(citation.locator,['kind','value'])
    || citation.locator.kind!=='section' || citation.locator.value!==citation.evidence_span_id)throw Error('OPEN_LAW_INPUT_INVALID');
  const url=citationContentUrl(workspaceId,citation,{notebookId});
  if(citation.content_url!==undefined && citation.content_url!==url)throw Error('OPEN_LAW_INPUT_INVALID');
  const response=await fetchImpl(url,{method:'GET',credentials:'same-origin',cache:'no-store',signal});
  if(!response.ok)await lawResponse(response,signal);
  const declared=response.headers.get('content-length');
  if(response.headers.get('content-type')?.split(';',1)[0].trim().toLowerCase()!=='text/plain'
    || response.headers.get('x-citation-locator-kind')!=='section' || !response.body
    || declared!==null && (!/^[0-9]+$/u.test(declared) || Number(declared)>524288)){
    await response.body?.cancel();throw Error('OPEN_LAW_RESPONSE_INVALID');
  }
  const reader=response.body.getReader(),chunks=[];let size=0,original;
  const abort=()=>{void reader.cancel().catch(()=>{});};signal?.addEventListener('abort',abort,{once:true});
  try{
    while(true){if(signal?.aborted)throw Error();const {done,value}=await reader.read();if(done)break;
      if(value.byteLength>524288-size)throw Error();chunks.push(value);size+=value.byteLength;}
    if(signal?.aborted)throw Error();const raw=new Uint8Array(size);let offset=0;
    for(const chunk of chunks){raw.set(chunk,offset);offset+=chunk.byteLength;}
    original=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(raw);
  }catch{throw Error('OPEN_LAW_RESPONSE_INVALID');}
  finally{signal?.removeEventListener('abort',abort);await reader.cancel().catch(()=>{});reader.releaseLock();}
  const data=await getOpenLawSnapshot(workspaceId,citation.source_version_id,{notebookId,fetchImpl,signal});
  if(data.snapshot.source_id!==citation.source_id || data.text!==original || !await lawText(original,data.snapshot.text_sha256))throw Error('OPEN_LAW_RESPONSE_INVALID');
  const points=Array.from(original),matches=[];
  for(let start=0;start<points.length;start+=1024){
    const suffix=lawSha256(new TextEncoder().encode(`${citation.source_version_id}|${start}`)).slice(0,32);
    if(`law-span-${suffix}`===citation.evidence_span_id && points.slice(start,start+1024).join('').trim())matches.push(start);
  }
  if(signal?.aborted || matches.length!==1)throw Error('OPEN_LAW_RESPONSE_INVALID');
  return {source_version_id:citation.source_version_id,text:original,start:matches[0],end:Math.min(points.length,matches[0]+1024)};
}

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const SAFE_FILENAME = /^[^/\\\u0000-\u001f]{1,255}$/u;
const URL_SEPARATOR = String.fromCharCode(58, 47, 47);
const INTERNAL_VALUE = new RegExp(
  `(?:https?${URL_SEPARATOR}|local${"host"}|127\\.0\\.0\\.1|password|authorization|credential)`,
  "iu",
);
const SOURCE_KEYS = Object.freeze([
  "source_id", "source_version_id", "filename", "source_state", "processing_state", "job_state"
]);
const SOURCE_STATES = Object.freeze([
  "registered", "security_check", "processing", "indexing", "ready", "unavailable", "waiting_model",
  "partial_understanding", "needs_review", "failed", "expired", "disabled", "deleting", "deleted",
]);
const CONNECTOR_STATUSES = Object.freeze(["connected", "disconnected", "unavailable"]);
const CONNECTOR_KINDS = Object.freeze(["mcp", "daon_approved_knowledge"]);
const CONNECTOR_KEYS = Object.freeze([
  "connector_id", "kind", "name", "status", "source_count", "endpoint_label", "last_checked_at", "error_code",
]);
const KNOWLEDGE_PACKAGE_KEYS = Object.freeze([
  "package_id", "producer", "producer_version", "knowledge_registration_id",
  "output_version_id", "authority", "registration_state", "review_state",
  "digest_sha256", "byte_size", "content_type", "effective_at", "expires_at",
]);
const KNOWLEDGE_PRODUCERS = Object.freeze(["daon2", "daon2_5", "daon3"]);
const CITATION_KEYS = Object.freeze([
  "citation_id", "source_id", "source_version_id", "evidence_span_id", "page"
]);
const OUTPUT_KEYS = Object.freeze([
  "studio_output_id", "output_version_id", "output_type", "title", "purpose", "status",
  "content", "run_id", "run_result_id", "citations"
]);
const OPERATIONS_COMPONENT_IDS = Object.freeze(["provider", "api", "storage", "sync", "queue"]);
const OPERATIONS_COMPONENT_KEYS = Object.freeze([
  "component_id", "status", "safe_code", "pending_count", "recovery_action",
]);
const OUTPUT_SETTING_TYPES = Object.freeze([
  "evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft",
]);
const OUTPUT_SETTING_FORMATS = Object.freeze({
  evidence_report: Object.freeze(["pdf", "docx"]),
  compliance_checklist: Object.freeze(["xlsx", "csv", "pdf"]),
  comparison_table: Object.freeze(["xlsx", "csv", "pdf"]),
  knowledge_map: Object.freeze(["json", "svg", "png", "pdf"]),
  business_draft: Object.freeze(["docx", "pdf"]),
});

function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exact(value, keys) {
  if (!record(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function safeId(value) {
  return typeof value === "string" && SAFE_ID.test(value);
}

function safeIdempotencyKey(value) {
  return typeof value === "string"
    && value.length >= 16
    && value.length <= 128
    && SAFE_ID.test(value);
}

function requiredWorkspace(value, code) {
  if (!safeId(value)) throw new Error(code);
  return value;
}

function safeText(value, minimum, maximum) {
  return typeof value === "string" && value.length >= minimum && value.length <= maximum && !INTERNAL_VALUE.test(value);
}

function safeConnectorErrorCode(value) {
  return value === null || (typeof value === "string" && /^[A-Z][A-Z0-9_]{2,127}$/u.test(value));
}

function validMeta(meta, workspaceId, { replay = false } = {}) {
  const keys = replay ? ["trace_id", "workspace_id", "replayed"] : ["trace_id", "workspace_id"];
  return exact(meta, keys) && safeId(meta.trace_id) && meta.workspace_id === workspaceId
    && (!replay || typeof meta.replayed === "boolean");
}

function validCitation(value) {
  return exact(value, CITATION_KEYS)
    && safeId(value.citation_id) && safeId(value.source_id) && safeId(value.source_version_id)
    && safeId(value.evidence_span_id) && Number.isSafeInteger(value.page) && value.page >= 1;
}

function validOutput(value) {
  return exact(value, OUTPUT_KEYS)
    && safeId(value.studio_output_id) && safeId(value.output_version_id)
    && STUDIO_OUTPUT_TYPES.has(value.output_type) && ["draft", "generating", "in_review", "approved", "delivered", "revision_requested", "failed", "unavailable"].includes(value.status)
    && safeText(value.title, 1, 200) && safeText(value.purpose, 1, 500)
    && ((record(value.content) && JSON.stringify(value.content).length <= 20_000) || safeText(value.content, 1, 20_000))
    && safeId(value.run_id) && safeId(value.run_result_id)
    && Array.isArray(value.citations) && value.citations.length >= 1
    && value.citations.length <= 20 && value.citations.every(validCitation);
}

async function json(response, fallback) {
  try {
    return await response.json();
  } catch {
    throw new Error(fallback);
  }
}

function safeResponseError(payload, fallback) {
  const error = new Error(typeof payload?.error?.code === "string" ? payload.error.code : fallback);
  Object.defineProperty(error, "retryable", {
    configurable: false, enumerable: false, value: payload?.error?.retryable === true, writable: false,
  });
  return error;
}

export async function listWorkspaceSources(workspaceId, { notebookId, websiteSourceVersionId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "SOURCE_LIST_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "SOURCE_LIST_INPUT_INVALID");
  if (websiteSourceVersionId !== undefined && !safeId(websiteSourceVersionId)) throw new Error("SOURCE_LIST_INPUT_INVALID");
  const selectedQuery = websiteSourceVersionId === undefined ? "" : `&website_source_version_id=${encodeURIComponent(websiteSourceVersionId)}`;
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/sources?notebook_id=${encodeURIComponent(notebook)}${selectedQuery}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = websiteSourceVersionId === undefined ? await json(response, "SOURCE_LIST_RESPONSE_INVALID")
    : await readWebsiteJson(response,8388608,'SOURCE_LIST_RESPONSE_INVALID',signal);
  if (!response.ok) throw safeResponseError(payload, "SOURCE_LIST_FAILED");
  const valid = exact(payload, ["data", "meta"])
    && exact(payload.data, ["sources"])
    && Array.isArray(payload.data.sources)
    && payload.data.sources.length <= 1_000
    && validMeta(payload.meta, workspace)
    && payload.data.sources.every((source) => (exact(source, SOURCE_KEYS)
      || (websiteSourceVersionId !== undefined && source.source_version_id === websiteSourceVersionId
        && exact(source, [...SOURCE_KEYS, "website"]) && source.source_state === "ready"
        && typeof source.website?.text === "string" && new TextEncoder().encode(source.website.text).length <= 524288
        && source.website.text.length > 0 && source.website.source_id === source.source_id
        && validWebsiteSnapshot(Object.fromEntries(Object.entries(source.website).filter(([key]) => key !== "text")), websiteSourceVersionId)))
      && safeId(source.source_id) && safeId(source.source_version_id)
      && typeof source.filename === "string" && SAFE_FILENAME.test(source.filename)
      && SOURCE_STATES.includes(source.source_state)
      && typeof source.processing_state === "string" && typeof source.job_state === "string");
  if (!valid || (websiteSourceVersionId !== undefined && (new TextEncoder().encode(JSON.stringify(payload)).length > 8388608
      || payload.data.sources.filter(source => Object.hasOwn(source,"website")).length !== 1))) throw new Error("SOURCE_LIST_RESPONSE_INVALID");
  return payload.data.sources;
}

function validConnector(value) {
  return exact(value, CONNECTOR_KEYS) && safeId(value.connector_id)
    && CONNECTOR_KINDS.includes(value.kind) && safeText(value.name, 1, 160)
    && CONNECTOR_STATUSES.includes(value.status) && Number.isSafeInteger(value.source_count)
    && value.source_count >= 0 && value.source_count <= 100_000
    && safeText(value.endpoint_label, 1, 160)
    && (value.last_checked_at === null || safeText(value.last_checked_at, 1, 64))
    && safeConnectorErrorCode(value.error_code);
}

async function connectorRequest(workspaceId, path, options = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  const requestFetch = options.fetchImpl ?? fetch;
  const response = await requestFetch(path, options.init);
  const payload = await json(response, "CONNECTOR_RESPONSE_INVALID");
  if (!response.ok) throw safeResponseError(payload, "CONNECTOR_REQUEST_FAILED");
  if (!exact(payload, ["data", "meta"]) || !validMeta(payload.meta, workspace)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return payload.data;
}

export async function listWorkspaceConnectorSources(workspaceId,connectorId,{fetchImpl=fetch,signal}={}) {
  const workspace=requiredWorkspace(workspaceId,'CONNECTOR_INPUT_INVALID');
  if(!safeId(connectorId))throw Error('CONNECTOR_INPUT_INVALID');
  const data=await connectorRequest(workspace,`/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors/${encodeURIComponent(connectorId)}/sources`,{
    fetchImpl,init:{method:'GET',credentials:'same-origin',cache:'no-store',signal}});
  if(!exact(data,['sources']) || !Array.isArray(data.sources)
    || data.sources.length>1000 || !data.sources.every(item=>exact(item,['source_id','connector_id','title','source_state','usable','content_digest'])
      && safeId(item.source_id) && item.connector_id===connectorId && typeof item.title==='string' && item.title.length<=300
      && typeof item.source_state==='string' && typeof item.usable==='boolean' && (item.content_digest===null || LAW_HASH.test(item.content_digest))))throw Error('CONNECTOR_RESPONSE_INVALID');
  return data.sources;
}

export async function listWorkspaceConnectors(workspaceId, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "CONNECTOR_RESPONSE_INVALID");
  if (!response.ok) throw safeResponseError(payload, "CONNECTOR_LIST_FAILED");
  if (!exact(payload, ["data", "meta"]) || !exact(payload.data, ["connectors"])
      || !Array.isArray(payload.data.connectors) || !payload.data.connectors.every(validConnector)
      || !validMeta(payload.meta, workspace)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return payload.data.connectors;
}

export async function registerWorkspaceConnector(workspaceId, request, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  if (!record(request) || !CONNECTOR_KINDS.includes(request.kind) || typeof request.name !== "string"
      || !request.name.trim() || request.name.length > 160) throw new Error("CONNECTOR_INPUT_INVALID");
  const data = await connectorRequest(workspace, `/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors`, {
    fetchImpl,
    init: { method: "POST", credentials: "same-origin", cache: "no-store", signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) },
  });
  if (!validConnector(data)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return data;
}

export async function reconnectWorkspaceConnector(workspaceId, connectorId, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  if (!safeId(connectorId)) throw new Error("CONNECTOR_INPUT_INVALID");
  const data = await connectorRequest(workspace, `/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors/${encodeURIComponent(connectorId)}/reconnect`, {
    fetchImpl,
    init: { method: "POST", credentials: "same-origin", cache: "no-store", signal,
      ...(connectorId === "mcp-open-law-go-kr" ? { headers: { "Content-Type": "application/json" }, body: "{}" } : {}) },
  });
  if (!validConnector(data)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return data;
}

export async function disconnectWorkspaceConnector(workspaceId, connectorId, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  if (!safeId(connectorId)) throw new Error("CONNECTOR_INPUT_INVALID");
  const data = await connectorRequest(workspace, `/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors/${encodeURIComponent(connectorId)}/disconnect`, {
    fetchImpl,
    init: { method: "POST", credentials: "same-origin", cache: "no-store", signal,
      ...(connectorId === "mcp-open-law-go-kr" ? { headers: { "Content-Type": "application/json" }, body: "{}" } : {}) },
  });
  if (!validConnector(data)) throw new Error("CONNECTOR_RESPONSE_INVALID");
  return data;
}

export async function unregisterWorkspaceConnector(workspaceId, connectorId, { fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "CONNECTOR_INPUT_INVALID");
  if (!safeId(connectorId)) throw new Error("CONNECTOR_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/connectors/${encodeURIComponent(connectorId)}`, {
    method: "DELETE", credentials: "same-origin", cache: "no-store", signal,
    ...(connectorId === "mcp-open-law-go-kr" ? { headers: { "Content-Type": "application/json" }, body: "{}" } : {}),
  });
  if (!response.ok) {
    const payload = await json(response, "CONNECTOR_DELETE_FAILED");
    throw safeResponseError(payload, "CONNECTOR_DELETE_FAILED");
  }
}

export async function unbindWorkspaceSource(workspaceId, source, { notebookId, etag, idempotencyKey, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "SOURCE_UNBIND_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "SOURCE_UNBIND_INPUT_INVALID");
  if (!record(source) || !exact(source, ["sourceId", "sourceVersionId"])
      || !safeId(source.sourceId) || !safeId(source.sourceVersionId)
      || typeof etag !== "string" || !/^"notebook-binding:[1-9][0-9]*"$/u.test(etag)
      || !safeIdempotencyKey(idempotencyKey)) throw new Error("SOURCE_UNBIND_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/notebooks/${encodeURIComponent(notebook)}/source-unbindings`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "If-Match": etag },
    body: JSON.stringify({ source_id: source.sourceId, source_version_id: source.sourceVersionId }),
  });
  const payload = await json(response, "SOURCE_UNBIND_RESPONSE_INVALID");
  if (!response.ok) throw safeResponseError(payload, "SOURCE_UNBIND_FAILED");
  const valid = exact(payload, ["data", "meta"])
    && exact(payload.data, ["notebook_id", "source_id", "source_version_id", "status"])
    && payload.data.notebook_id === notebook && payload.data.source_id === source.sourceId
    && payload.data.source_version_id === source.sourceVersionId && payload.data.status === "unbound"
    && validMeta(payload.meta, workspace, { replay: true });
  if (!valid) throw new Error("SOURCE_UNBIND_RESPONSE_INVALID");
  return { ...payload.data, etag: response.headers.get("etag") };
}

export async function listWorkspaceKnowledgePackages(
  workspaceId,
  { fetchImpl = fetch, signal, now = Date.now } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "KNOWLEDGE_PACKAGE_LIST_INPUT_INVALID");
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/knowledge-packages`,
    { method: "GET", credentials: "same-origin", cache: "no-store", signal },
  );
  const payload = await json(response, "KNOWLEDGE_PACKAGE_LIST_RESPONSE_INVALID");
  if (!response.ok) {
    throw new Error(
      typeof payload?.error?.code === "string"
        ? payload.error.code
        : "KNOWLEDGE_PACKAGE_LIST_FAILED",
    );
  }
  const checkedAt = now();
  const valid = exact(payload, ["data", "meta"])
    && exact(payload.data, ["items"])
    && Array.isArray(payload.data.items)
    && payload.data.items.length <= 1_000
    && record(payload.meta)
    && safeId(payload.meta.trace_id)
    && payload.data.items.every((item) => {
      const effectiveAt = Date.parse(item.effective_at);
      const expiresAt = Date.parse(item.expires_at);
      return exact(item, KNOWLEDGE_PACKAGE_KEYS)
        && safeId(item.package_id)
        && KNOWLEDGE_PRODUCERS.includes(item.producer)
        && safeText(item.producer_version, 1, 128)
        && safeId(item.knowledge_registration_id)
        && safeId(item.output_version_id)
        && item.authority === "approved"
        && item.registration_state === "registered"
        && item.review_state === "approved"
        && /^[0-9a-f]{64}$/u.test(item.digest_sha256)
        && Number.isSafeInteger(item.byte_size)
        && item.byte_size >= 1
        && item.byte_size <= 8 * 1024 * 1024
        && safeText(item.content_type, 1, 255)
        && Number.isFinite(effectiveAt)
        && Number.isFinite(expiresAt)
        && effectiveAt <= checkedAt
        && checkedAt < expiresAt;
    });
  if (!valid) throw new Error("KNOWLEDGE_PACKAGE_LIST_RESPONSE_INVALID");
  return payload.data.items;
}

export async function getWorkspaceOperationsStatus(
  workspaceId, { fetchImpl = fetch, signal } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "OPERATIONS_STATUS_INPUT_INVALID");
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/operations/status`,
    { method: "GET", credentials: "same-origin", cache: "no-store", signal },
  );
  const payload = await json(response, "OPERATIONS_STATUS_RESPONSE_INVALID");
  if (!response.ok) {
    throw new Error(
      typeof payload?.error?.code === "string"
        ? payload.error.code
        : "OPERATIONS_STATUS_UNAVAILABLE",
    );
  }
  const data = payload?.data;
  const components = data?.components;
  const valid = exact(payload, ["data", "meta"])
    && exact(data, ["workspace_id", "overall_status", "checked_at", "components"])
    && data.workspace_id === workspace
    && new Set(["ready", "warning", "error"]).has(data.overall_status)
    && typeof data.checked_at === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u.test(data.checked_at)
    && Array.isArray(components) && components.length === 5
    && components.every((item, index) => exact(item, OPERATIONS_COMPONENT_KEYS)
      && item.component_id === OPERATIONS_COMPONENT_IDS[index]
      && new Set(["ready", "warning", "error"]).has(item.status)
      && typeof item.safe_code === "string" && /^[A-Z][A-Z0-9_]{2,63}$/u.test(item.safe_code)
      && Number.isSafeInteger(item.pending_count) && item.pending_count >= 0
      && new Set(["none", "open_llm_settings", "open_sync_settings", "refresh_status"]).has(item.recovery_action))
    && validMeta(payload.meta, workspace);
  if (!valid) throw new Error("OPERATIONS_STATUS_RESPONSE_INVALID");
  return data;
}

function validOutputVersionSettings(data, workspace) {
  return exact(data, ["workspace_id", "default_formats", "version_save_mode", "version"])
    && data.workspace_id === workspace
    && exact(data.default_formats, OUTPUT_SETTING_TYPES)
    && OUTPUT_SETTING_TYPES.every((type) => OUTPUT_SETTING_FORMATS[type].includes(data.default_formats[type]))
    && data.version_save_mode === "append_only"
    && Number.isSafeInteger(data.version) && data.version >= 0;
}

export async function getWorkspaceOutputVersionSettings(
  workspaceId, { fetchImpl = fetch, signal } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "OUTPUT_VERSION_SETTINGS_INPUT_INVALID");
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/output-version-settings`,
    { method: "GET", credentials: "same-origin", cache: "no-store", signal },
  );
  const payload = await json(response, "OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "OUTPUT_VERSION_SETTINGS_UNAVAILABLE");
  if (!exact(payload, ["data", "meta"]) || !validOutputVersionSettings(payload.data, workspace)
      || !validMeta(payload.meta, workspace)) throw new Error("OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  const etag = response.headers?.get?.("etag");
  if (typeof etag !== "string" || etag !== `"output-version-settings:${workspace}:${payload.data.version}"`) {
    throw new Error("OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  }
  return { ...payload.data, etag };
}

export async function saveWorkspaceOutputVersionSettings(
  workspaceId, settings,
  { fetchImpl = fetch, idempotencyKey = secureUuid(), signal } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "OUTPUT_VERSION_SETTINGS_INPUT_INVALID");
  if (!record(settings) || !exact(settings, ["default_formats", "version", "etag"])
      || !validOutputVersionSettings({ workspace_id: workspace, default_formats: settings.default_formats, version_save_mode: "append_only", version: settings.version }, workspace)
      || settings.etag !== `"output-version-settings:${workspace}:${settings.version}"`
      || !safeIdempotencyKey(idempotencyKey)) throw new Error("OUTPUT_VERSION_SETTINGS_INPUT_INVALID");
  const response = await fetchImpl(
    `/bff/api/workspaces/${encodeURIComponent(workspace)}/output-version-settings`,
    {
      method: "PATCH", credentials: "same-origin", cache: "no-store", signal,
      headers: { "Content-Type": "application/json", "If-Match": settings.etag, "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ default_formats: settings.default_formats, expected_version: settings.version }),
    },
  );
  const payload = await json(response, "OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "OUTPUT_VERSION_SETTINGS_UNAVAILABLE");
  if (!exact(payload, ["data", "meta"]) || !validOutputVersionSettings(payload.data, workspace)
      || !validMeta(payload.meta, workspace)) throw new Error("OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  const etag = response.headers?.get?.("etag");
  if (typeof etag !== "string" || etag !== `"output-version-settings:${workspace}:${payload.data.version}"`) {
    throw new Error("OUTPUT_VERSION_SETTINGS_RESPONSE_INVALID");
  }
  return { ...payload.data, etag };
}

function validCreateRequest(request) {
  return exact(request, ["source_id", "source_version_id", "run_id", "run_result_id", "title", "purpose"])
    && safeId(request.source_id) && safeId(request.source_version_id)
    && safeId(request.run_id) && safeId(request.run_result_id)
    && safeText(request.title, 1, 200) && safeText(request.purpose, 1, 500);
}

export async function createGroundedReport(
  workspaceId, request, { notebookId, fetchImpl = fetch, idempotencyKey = secureUuid(), signal } = {},
) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  if (!safeId(notebookId) || !validCreateRequest(request) || !safeIdempotencyKey(idempotencyKey)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/studio/reports`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ notebook_id: notebookId, ...request }),
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_CREATE_FAILED");
  if (!exact(payload, ["data", "meta"]) || !validOutput(payload.data) || !validMeta(payload.meta, workspace, { replay: true })) {
    throw new Error("STUDIO_RESPONSE_INVALID");
  }
  return payload.data;
}

export async function listStudioOutputs(workspaceId, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  if (!safeId(notebookId)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspace)}/studio/outputs?notebook_id=${encodeURIComponent(notebookId)}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_LIST_FAILED");
  if (
    !exact(payload, ["data", "meta"]) || !exact(payload.data, ["outputs"])
    || !Array.isArray(payload.data.outputs) || payload.data.outputs.length > 1_000
    || !payload.data.outputs.every(validOutput) || !validMeta(payload.meta, workspace)
  ) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data.outputs;
}

// Keep this list aligned with packages/ui/src/product-studio-model.js and the
// server Studio contract. Audio/video are intentionally accepted here so the
// server can return the explicit STUDIO_OUTPUT_UNAVAILABLE result when no
// media provider is configured; they must not be silently treated as success.
const STUDIO_OUTPUT_TYPES = new Set([
  "evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft",
  "slides", "infographic", "flashcards", "quiz", "audio", "video",
]);
const STUDIO_ACTIONS = new Set(["reviews", "approval-requests", "approvals", "deliveries", "knowledge-registrations"]);

export async function issueStudioStepUp(actionGroup, targetId, password, { fetchImpl = fetch, idempotencyKey = secureUuid(), signal } = {}) {
  if (!new Set(["final_approval_or_knowledge_registration", "external_transfer", "data_area_move"]).has(actionGroup) || !safeId(targetId) || typeof password !== "string" || password.length < 12 || password.length > 1024) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl("/bff/api/session/step-up", {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ action_group: actionGroup, target_id: targetId, ttl_seconds: 300, password }),
  });
  const payload = await json(response, "STEP_UP_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STEP_UP_REQUIRED");
  if (typeof payload?.data?.step_up_authorization !== "string") throw new Error("STEP_UP_RESPONSE_INVALID");
  return payload.data.step_up_authorization;
}

function validGeneration(request, workspaceId) {
  const sourceOnly = request?.source_only === true;
  const runValid = sourceOnly
    ? request.run_id == null && request.run_result_id == null
    : safeId(request?.run_id) && safeId(request?.run_result_id);
  return record(request) && STUDIO_OUTPUT_TYPES.has(request.output_type) && (sourceOnly ? (request.source_id == null || safeId(request.source_id)) : safeId(request.source_id))
    && runValid
    && Array.isArray(request.source_version_ids) && request.source_version_ids.length > 0
    && request.source_version_ids.every(safeId) && record(request.settings)
    && request.settings.source_version_ids?.every?.(safeId)
    && (request.workspace_id === undefined || request.workspace_id === null || request.workspace_id === workspaceId);
}

export async function createStudioGeneration(workspaceId, request, { notebookId, fetchImpl = fetch, idempotencyKey = secureUuid(), signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!validGeneration(request, workspace) || !safeIdempotencyKey(idempotencyKey)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl("/bff/api/studio-generation-requests", {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ ...request, workspace_id: workspace, notebook_id: notebook }),
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_CREATE_FAILED");
  if (!record(payload?.data) || !safeId(payload.data.job_id) || !["queued", "leased", "generating", "completed", "failed", "unavailable"].includes(payload.data.status)) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data;
}

export async function getStudioGenerationJob(workspaceId, jobId, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!safeId(jobId)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-generation-jobs/${encodeURIComponent(jobId)}?workspace_id=${encodeURIComponent(workspace)}&notebook_id=${encodeURIComponent(notebook)}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_JOB_FAILED");
  if (!record(payload?.data) || !safeId(payload.data.job_id) || !["queued", "leased", "generating", "completed", "failed", "unavailable"].includes(payload.data.status)) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data;
}

export async function listProductStudioOutputs(workspaceId, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-outputs?workspace_id=${encodeURIComponent(workspace)}&notebook_id=${encodeURIComponent(notebook)}`, { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_LIST_FAILED");
  if (!Array.isArray(payload?.data?.outputs) || !Array.isArray(payload?.data?.studio_locks)) throw new Error("STUDIO_RESPONSE_INVALID");
  return { outputs: payload.data.outputs, studioLocks: payload.data.studio_locks };
}

export async function createStudioVersion(workspaceId, outputId, request, { notebookId, fetchImpl = fetch, idempotencyKey = secureUuid(), signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!safeId(outputId) || !record(request) || !safeId(request.previous_version_id)
      || !new Set(["user_edit", "ai_regeneration", "settings_change"]).has(request.revision_type)
      || typeof request.change_reason !== "string" || !request.change_reason.trim()
      || typeof request.content !== "string" || !request.content.trim()
      || (request.revision_type === "settings_change" && (!record(request.settings) || !Array.isArray(request.settings.source_version_ids)))
      || !safeIdempotencyKey(idempotencyKey)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-outputs/${encodeURIComponent(outputId)}/versions`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ ...request, workspace_id: workspace, notebook_id: notebook }),
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_VERSION_FAILED");
  if (!record(payload?.data) || !safeId(payload.data.output_version_id)) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data;
}

const STUDIO_VERSION_KEYS = Object.freeze([
  "output_version_id", "content_version", "previous_version_id", "status", "content",
  "revision_type", "change_reason", "settings_snapshot_id", "citations", "review_request_id",
  "approval_request_id", "approval_id", "delivery_id", "knowledge_registration_id", "output_format",
]);
const STUDIO_CITATION_KEYS = Object.freeze([
  "citation_id", "source_version_id", "evidence_span_id", "origin", "locator",
]);

function validStudioCitation(value) {
  return exact(value, STUDIO_CITATION_KEYS)
    && safeId(value.citation_id) && safeId(value.source_version_id) && safeId(value.evidence_span_id)
    && new Set(["raw_source", "daon_knowledge"]).has(value.origin)
    && record(value.locator) && exact(value.locator, ["kind", "value"])
    && new Set(["page", "section", "image_region", "sheet_cell", "docx_block", "pptx_block", "audio_segment"]).has(value.locator.kind)
    && safeText(value.locator.value, 1, 500)
    && (value.locator.kind !== "image_region" || parseImageRegion(value.locator.value) !== null)
    && (value.locator.kind !== "sheet_cell" || parseSheetCell(value.locator.value) !== null)
    && (value.locator.kind !== "docx_block" || parseDocxBlock(value.locator.value) !== null)
    && (value.locator.kind !== "pptx_block" || parsePptxBlock(value.locator.value) !== null)
    && (value.locator.kind !== "audio_segment" || parseAudioSegment(value.locator.value) !== null);
}

function validStudioVersion(value) {
  const nullableIds = ["previous_version_id", "review_request_id", "approval_request_id", "approval_id", "delivery_id", "knowledge_registration_id"];
  let serialized;
  try { serialized = JSON.stringify(value.content); } catch { return false; }
  return exact(value, STUDIO_VERSION_KEYS) && safeId(value.output_version_id)
    && Number.isSafeInteger(value.content_version) && value.content_version >= 1
    && nullableIds.every((field) => value[field] === null || safeId(value[field]))
    && new Set(["draft", "review_requested", "in_review", "approved", "revision_requested", "delivered"]).has(value.status)
    && (typeof value.content === "string" || record(value.content))
    && safeText(serialized, 1, 8 * 1024 * 1024)
    && new Set(["initial", "user_edit", "ai_regeneration", "settings_change"]).has(value.revision_type)
    && safeText(value.change_reason, 1, 500) && safeId(value.settings_snapshot_id)
    && Array.isArray(value.citations) && value.citations.length <= 1_000
    && value.citations.every(validStudioCitation)
    && new Set(["docx", "pdf", "xlsx", "csv", "json", "svg", "png"]).has(value.output_format);
}

export async function listStudioVersions(workspaceId, outputId, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!safeId(outputId)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-outputs/${encodeURIComponent(outputId)}/versions?workspace_id=${encodeURIComponent(workspace)}&notebook_id=${encodeURIComponent(notebook)}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_LIST_FAILED");
  if (!exact(payload, ["data", "meta"]) || !exact(payload.data, ["output_id", "versions"])
      || payload.data.output_id !== outputId || !Array.isArray(payload.data.versions)
      || payload.data.versions.length > 1_000 || !payload.data.versions.every(validStudioVersion)
      || !validMeta(payload.meta, workspace)) throw new Error("STUDIO_RESPONSE_INVALID");
  return payload.data.versions;
}

export async function createStudioAction(workspaceId, action, request, { notebookId, fetchImpl = fetch, idempotencyKey = secureUuid(), signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!STUDIO_ACTIONS.has(action) || !record(request) || !safeIdempotencyKey(idempotencyKey)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/${action}`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ ...request, workspace_id: workspace, notebook_id: notebook }),
  });
  const payload = await json(response, "STUDIO_RESPONSE_INVALID");
  if (!response.ok) throw new Error(typeof payload?.error?.code === "string" ? payload.error.code : "STUDIO_ACTION_FAILED");
  return payload.data;
}

export async function downloadStudioExport(workspaceId, outputId, versionId, format, { notebookId, fetchImpl = fetch, signal } = {}) {
  const workspace = requiredWorkspace(workspaceId, "STUDIO_INPUT_INVALID");
  const notebook = requiredWorkspace(notebookId, "STUDIO_INPUT_INVALID");
  if (!safeId(outputId) || !safeId(versionId) || !new Set(["docx", "pdf", "xlsx", "csv", "json", "svg", "png"]).has(format)) throw new Error("STUDIO_INPUT_INVALID");
  const response = await fetchImpl(`/bff/api/studio-outputs/${encodeURIComponent(outputId)}/versions/${encodeURIComponent(versionId)}/exports/${format}?workspace_id=${encodeURIComponent(workspace)}&notebook_id=${encodeURIComponent(notebook)}`, { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  if (!response.ok) throw new Error("STUDIO_EXPORT_FAILED");
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength < 4 || buffer.byteLength > 8 * 1024 * 1024) throw new Error("STUDIO_EXPORT_INVALID");
  return { bytes: [...new Uint8Array(buffer)], contentType: response.headers.get("content-type"), disposition: response.headers.get("content-disposition") };
}
