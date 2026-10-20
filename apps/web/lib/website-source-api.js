"use client";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const DIGEST = /^[0-9a-f]{64}$/u;
const ERRORS = new Set(["WEBSITE_URL_INVALID", "WEBSITE_ACCESS_DENIED", "WEBSITE_FETCH_BLOCKED",
  "WEBSITE_IDEMPOTENCY_KEY_REUSED", "WEBSITE_IMPORT_IN_PROGRESS", "WEBSITE_CONTENT_TOO_LARGE",
  "WEBSITE_MEDIA_UNSUPPORTED", "WEBSITE_TEXT_UNAVAILABLE", "WEBSITE_FETCH_FAILED",
  "WEBSITE_STORAGE_UNAVAILABLE", "WEBSITE_FETCH_TIMEOUT"]);
const RESULT_KEYS = ["snapshot_id", "source_id", "source_version_id", "source_state", "replayed"];
const SNAPSHOT_KEYS = ["snapshot_id", "source_id", "source_version_id", "requested_url", "final_url", "title",
  "published_at", "fetched_at", "license_status", "license_url", "raw_sha256", "text_sha256", "extraction_version"];
const exact = (value, keys) => value !== null && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const id = value => typeof value === "string" && ID.test(value);
const date = value => typeof value === "string" && value.length <= 64
  && /^\d{4}-\d{2}-\d{2}T/u.test(value) && Number.isFinite(Date.parse(value));

export function validWebsiteUrl(value) {
  if (typeof value !== "string" || !value || Array.from(value).length > 2048
      || /[\u0000-\u0020\u007f?#]/u.test(value) || /%(?:0[0-9a-f]|1[0-9a-f]|7f|25(?:0[0-9a-f]|1[0-9a-f]|7f))/iu.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.search && !url.hash
      && (!url.port || url.port === "443") && url.hostname.includes(".")
      && !url.hostname.includes(":") && !/^[\d.]+$/u.test(url.hostname)
      && url.hostname.split(".").every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/iu.test(label));
  } catch { return false; }
}

export async function readWebsiteJson(response, maximum=32768, failure='WEBSITE_RESPONSE_INVALID', signal) {
  const declared=response.headers.get('content-length');
  if(declared!==null && (!/^\d+$/u.test(declared) || Number(declared)>maximum)) {
    await response.body?.cancel();throw new Error(failure);
  }
  if(!response.body)throw new Error(failure);
  const reader=response.body.getReader(),chunks=[];let size=0;
  const abort=()=>{void reader.cancel().catch(()=>{});};
  signal?.addEventListener('abort',abort,{once:true});
  try {
    while(true){
      if(signal?.aborted)throw new Error();
      const {done,value}=await reader.read();if(done)break;
      if(value.byteLength>maximum-size)throw new Error();
      chunks.push(value);size+=value.byteLength;
    }
    if(signal?.aborted)throw new Error();
    const body=new Uint8Array(size);let offset=0;
    for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.byteLength;}
    return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(body));
  } catch { throw new Error(failure); }
  finally {signal?.removeEventListener('abort',abort);await reader.cancel().catch(()=>{});reader.releaseLock();}
}

async function read(response,signal) {
  const value=await readWebsiteJson(response,32768,'WEBSITE_RESPONSE_INVALID',signal);
  if (!response.ok) throw new Error(ERRORS.has(value?.error?.code) ? value.error.code : "WEBSITE_FETCH_FAILED");
  if (!exact(value, ["data", "meta"]) || !exact(value.meta, ["trace_id"]) || !id(value.meta.trace_id)) {
    throw new Error("WEBSITE_RESPONSE_INVALID");
  }
  return value.data;
}

export async function importWebsiteSource(workspaceId, input, { idempotencyKey, signal, fetchImpl = fetch } = {}) {
  if (!id(workspaceId) || !id(idempotencyKey) || !exact(input, ["notebook_id", "url", "public_page_confirmed"])
      || !id(input.notebook_id) || input.public_page_confirmed !== true || !validWebsiteUrl(input.url)) {
    throw new Error("WEBSITE_URL_INVALID");
  }
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/website-imports`, {
    method: "POST", credentials: "same-origin", cache: "no-store", signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }, body: JSON.stringify(input),
  });
  const data = await read(response,signal); const result = data?.attributes;
  if (!exact(data, ["kind", "id", "version", "attributes"]) || data.kind !== "website_import" || data.version !== "1"
      || !exact(result, RESULT_KEYS) || !id(result.snapshot_id) || data.id !== result.snapshot_id
      || !id(result.source_id) || !id(result.source_version_id) || result.source_state !== "ready"
      || typeof result.replayed !== "boolean" || response.status !== (result.replayed ? 200 : 201)) {
    throw new Error("WEBSITE_RESPONSE_INVALID");
  }
  return result;
}

export function validWebsiteSnapshot(value, sourceVersionId) {
  return !(!exact(value, SNAPSHOT_KEYS) || !id(value.snapshot_id) || !id(value.source_id)
      || value.source_version_id !== sourceVersionId || !validWebsiteUrl(value.requested_url) || !validWebsiteUrl(value.final_url)
      || typeof value.title !== "string" || !value.title.trim() || Array.from(value.title).length > 255
      || !date(value.fetched_at) || value.published_at !== null && !date(value.published_at)
      || !["unknown", "publisher_declared"].includes(value.license_status)
      || value.license_status === "unknown" && value.license_url !== null
      || value.license_url !== null && !validWebsiteUrl(value.license_url)
      || typeof value.raw_sha256 !== "string" || !DIGEST.test(value.raw_sha256)
      || typeof value.text_sha256 !== "string" || !DIGEST.test(value.text_sha256)
      || value.extraction_version !== "website-text-v1");
}

export async function getWebsiteSnapshot(workspaceId, sourceVersionId, { notebookId, signal, fetchImpl = fetch } = {}) {
  if (!id(workspaceId) || !id(sourceVersionId) || !id(notebookId)) throw new Error("WEBSITE_URL_INVALID");
  const response = await fetchImpl(`/bff/api/workspaces/${encodeURIComponent(workspaceId)}/source-versions/${encodeURIComponent(sourceVersionId)}/website-snapshot?notebook_id=${encodeURIComponent(notebookId)}`, {
    method: "GET", credentials: "same-origin", cache: "no-store", signal,
  });
  const value = await read(response,signal);
  if (response.status !== 200 || !validWebsiteSnapshot(value,sourceVersionId)) throw new Error("WEBSITE_RESPONSE_INVALID");
  return value;
}
