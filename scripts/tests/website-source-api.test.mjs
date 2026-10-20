import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createNotebookContextWorkspaceAdapter } from '../../packages/ui/src/notebook-context-adapter.js';
import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";
import { listWorkspaceSources } from "../../apps/web/lib/product-workspace-api.js";

test("BFF selected website query와 bounded metadata 응답", async () => {
  const calls = [];
  const proxy = createBffProxy({ baseUrl: new URL("http://api:8670"), fetchImpl: async url => {
    calls.push(String(url)); return new Response("x".repeat(32769));
  } });
  const source = new Request("https://portal.example/bff/api/workspaces/w/sources?notebook_id=n&website_source_version_id=v");
  await proxy(source, ["workspaces", "w", "sources"]);
  assert.match(calls[0], /website_source_version_id=v/);
  const meta = new Request("https://portal.example/bff/api/workspaces/w/source-versions/v/website-snapshot?notebook_id=n");
  assert.equal((await proxy(meta, ["workspaces", "w", "source-versions", "v", "website-snapshot"])).status, 502);
});

async function api() {
  assert.ok(existsSync("apps/web/lib/website-source-api.js"), "실제 웹사이트 consumer 필요");
  return import("../../apps/web/lib/website-source-api.js");
}

test('Notebook wrapper의 웹 요청/원문 query는 현재 Notebook을 강제한다',async()=>{
  const calls=[];const context={notebook_id:'current',sources:[],knowledge_context_ids:[],conversation_thread_ids:[],studio_output_ids:[],output_version_ids:[],generation_settings_ids:[],source_deletion_requests:[],conversation:null};
  const adapter=createNotebookContextWorkspaceAdapter({importWebsite:async(input,options)=>{calls.push(options);return result;},getWebsiteSnapshot:async(version,options)=>{calls.push(options);return snapshot();}},context);
  await adapter.importWebsite({url:'https://example.com/',publicPageConfirmed:true},{notebookId:'wrong',idempotencyKey:'key'});
  await adapter.getWebsiteSnapshot('version-1',{notebookId:'wrong'});
  assert.equal(calls[0].notebookId,'current');assert.equal(calls[1].notebookId,'current');
});

test('OpenAPI consumer 계약은 import/snapshot과 선택 원문 query를 명시한다',async()=>{
  const contract=JSON.parse(await readFile('packages/contracts/openapi/v1/openapi.json','utf8'));
  assert.ok(contract.paths['/api/v1/workspaces/{id}/website-imports']?.post);
  assert.ok(contract.paths['/api/v1/workspaces/{id}/source-versions/{version_id}/website-snapshot']?.get);
  assert.equal(contract.components.schemas.WebsiteSnapshotView.required.length,13);
  assert.ok(contract.paths['/api/v1/workspaces/{id}/sources'].get.parameters.some(item=>item.name==='website_source_version_id'&&item.required===false));
});
const input = { notebook_id: "notebook-1", url: "https://example.com/", public_page_confirmed: true };
const result = { snapshot_id: "snapshot-1", source_id: "source-1", source_version_id: "version-1", source_state: "ready", replayed: false };
const envelope = () => ({ data: { kind: "website_import", id: "snapshot-1", version: "1", attributes: { ...result } }, meta: { trace_id: "trace-1" } });
const snapshot = () => ({ snapshot_id: "snapshot-1", source_id: "source-1", source_version_id: "version-1",
  requested_url: "https://example.com/", final_url: "https://example.com/", title: "Example",
  published_at: null, fetched_at: "2026-10-15T10:00:00+00:00", license_status: "unknown", license_url: null,
  raw_sha256: "a".repeat(64), text_sha256: "b".repeat(64), extraction_version: "website-text-v1" });

test("import consumer는 정확 body/멱등 키로 same-origin만 호출한다", async () => {
  const m = await api(); const calls = [];
  const value = await m.importWebsiteSource("workspace-1", input, { idempotencyKey: "request-1", fetchImpl: async (url, init) => {
    calls.push({ url, init }); return Response.json(envelope(), { status: 201 });
  } });
  assert.deepEqual(value, result);
  assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/website-imports");
  assert.deepEqual(JSON.parse(calls[0].init.body), input);
  assert.equal(calls[0].init.credentials, "same-origin");
  assert.equal(calls[0].init.headers["Idempotency-Key"], "request-1");
});

test("입력 확인/지원밖 URL/추가 scope는 fetch 전 거부된다", async () => {
  const m = await api(); let calls = 0;
  const fetchImpl = () => { calls++; throw new Error("호출 금지"); };
  for (const bad of [{ ...input, actor_id: "other" }, { ...input, public_page_confirmed: false },
    ...["http://example.com/", "https://127.0.0.1/", "https://[::1]/", "https://example.com/?", "https://example.com/#", "https://user@example.com/", "https://example.com:444/", "https://example.com/%0a"].map(url => ({ ...input, url }))]) {
    await assert.rejects(m.importWebsiteSource("workspace-1", bad, { idempotencyKey: "k", fetchImpl }), /WEBSITE_URL_INVALID/);
  }
  assert.equal(calls, 0);
});

test("부분ready/추가필드/잘못된ID DTO와 위험 오류 원문을 노출하지 않는다", async () => {
  const m = await api();
  for (const mutate of [p => p.data.attributes.source_state = "processing", p => p.data.attributes.secret = "private", p => p.data.id = "other"]) {
    const payload = envelope(); mutate(payload);
    await assert.rejects(m.importWebsiteSource("workspace-1", input, { idempotencyKey: "k", fetchImpl: async () => Response.json(payload, { status: 201 }) }), /WEBSITE_RESPONSE_INVALID/);
  }
  await assert.rejects(m.importWebsiteSource("workspace-1", input, { idempotencyKey: "k", fetchImpl: async () => Response.json({ error: { code: "private upstream password" } }, { status: 503 }) }), /^Error: WEBSITE_FETCH_FAILED$/);
});

test('웹 consumer는 초과 Content-Length를 body allocation 전에 거부한다',async()=>{
  const m=await api();
  await assert.rejects(m.importWebsiteSource('workspace-1',input,{idempotencyKey:'k',fetchImpl:async()=>Response.json(envelope(),{status:201,headers:{'Content-Length':'32769'}})}),/WEBSITE_RESPONSE_INVALID/);
});

test("snapshot exact13 fields만 받고 다른Version/미상License위조/원문추가를 거부한다", async () => {
  const m = await api(); const calls = [];
  const fetchImpl = async url => { calls.push(url); return Response.json({ data: snapshot(), meta: { trace_id: "trace-1" } }); };
  assert.deepEqual(await m.getWebsiteSnapshot("workspace-1", "version-1", { notebookId: "notebook-1", fetchImpl }), snapshot());
  assert.equal(calls[0], "/bff/api/workspaces/workspace-1/source-versions/version-1/website-snapshot?notebook_id=notebook-1");
  for (const mutate of [v => v.text = "unauthorized text", v => v.source_version_id = "other", v => v.license_url = "https://example.com/", v => v.raw_sha256 = "invalid",v=>v.title='']) {
    const value = snapshot(); mutate(value);
    await assert.rejects(m.getWebsiteSnapshot("workspace-1", "version-1", { notebookId: "notebook-1", fetchImpl: async () => Response.json({ data: value, meta: { trace_id: "trace-1" } }) }), /WEBSITE_RESPONSE_INVALID/);
  }
});

test("BFF는 웹사이트 두 경로만 허용하고 browser 세션/CSRF 경계를 유지한다", async () => {
  const calls = [];
  const proxy = createBffProxy({ baseUrl: new URL("http://api:8670"), publicOrigin: new URL("https://portal.example"),
    fetchImpl: async (url, init) => { calls.push({ url: String(url), init }); return Response.json(envelope(), { status: 201 }); } });
  const request = new Request("https://portal.example/bff/api/workspaces/workspace-1/website-imports", { method: "POST",
    headers: { "Content-Type": "application/json", "Origin": "https://portal.example", "Referer": "https://portal.example/notebooks/n", "Cookie": "__Host-daon_session=opaque", "Idempotency-Key": "k" }, body: JSON.stringify(input) });
  const response = await proxy(request, ["workspaces", "workspace-1", "website-imports"]);
  assert.equal(response.status, 201);
  assert.equal(calls[0].url, "http://api:8670/api/v1/workspaces/workspace-1/website-imports");
  assert.equal(calls[0].init.redirect, "manual");
  const metaRequest = new Request("https://portal.example/bff/api/workspaces/workspace-1/source-versions/version-1/website-snapshot?notebook_id=notebook-1", { headers: { Cookie: "__Host-daon_session=opaque" } });
  assert.equal((await proxy(metaRequest, ["workspaces", "workspace-1", "source-versions", "version-1", "website-snapshot"])).status, 201);
  assert.match(calls[1].url, /website-snapshot\?notebook_id=notebook-1$/);
  const denied = new Request(metaRequest.url + "&actor_id=other", { headers: { Cookie: "__Host-daon_session=opaque" } });
  assert.equal((await proxy(denied, ["workspaces", "workspace-1", "source-versions", "version-1", "website-snapshot"])).status, 400);
  assert.equal(calls.length, 2);
});

test("선택 원문 query는 한 Version에만 optional website DTO를 허용하고 기본6필드는 불변이다", async () => {
  const source = { source_id: "source-1", source_version_id: "version-1", filename: "web.txt", source_state: "ready", processing_state: "unavailable", job_state: "unavailable" };
  const calls = [];
  const fetchImpl = async url => { calls.push(url); return Response.json({ data: { sources: [{ ...source, website: { ...snapshot(), text: "원문  공백\n<script>데이터</script>" } }] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } }); };
  const values = await listWorkspaceSources("workspace-1", { notebookId: "notebook-1", websiteSourceVersionId: "version-1", fetchImpl });
  assert.equal(calls[0], "/bff/api/workspaces/workspace-1/sources?notebook_id=notebook-1&website_source_version_id=version-1");
  assert.equal(values[0].website.text, "원문  공백\n<script>데이터</script>");
  await assert.rejects(listWorkspaceSources("workspace-1", { notebookId: "notebook-1", fetchImpl }), /SOURCE_LIST_RESPONSE_INVALID/);
  await assert.rejects(listWorkspaceSources("workspace-1", { notebookId: "notebook-1", websiteSourceVersionId: "other", fetchImpl }), /SOURCE_LIST_RESPONSE_INVALID/);
});
