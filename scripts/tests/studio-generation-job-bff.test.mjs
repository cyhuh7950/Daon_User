import assert from "node:assert/strict";
import test from "node:test";
import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";

const segments = ["studio-generation-jobs", "studio-job-1"];
const origin = "https://app.example.com";

test("Studio job GET forwards exact scope and preserves a failed job response", async () => {
  const calls = [];
  const failed = { data: { job_id: "studio-job-1", status: "failed", error_code: "POLICY_PROJECTION_MISMATCH" }, meta: { trace_id: "trace-1" } };
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"), fetchImpl: async (url, init) => {
    calls.push({ url: String(url), init });
    return Response.json(failed);
  } });
  const response = await proxy(new Request(`${origin}/bff/api/studio-generation-jobs/studio-job-1?workspace_id=workspace-1&notebook_id=notebook-1`, {
    headers: { Cookie: "__Host-daon_session=fixture" },
  }), segments);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), failed);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.example.com/api/v1/studio-generation-jobs/studio-job-1?workspace_id=workspace-1&notebook_id=notebook-1");
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.headers.get("cookie"), "__Host-daon_session=fixture");
});

test("Studio job GET rejects missing duplicate empty malformed and unknown scope queries before forwarding", async () => {
  let calls = 0;
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"), fetchImpl: async () => { calls++; return Response.json({}); } });
  for (const query of ["", "workspace_id=workspace-1", "notebook_id=notebook-1",
    "workspace_id=&notebook_id=notebook-1", "workspace_id=workspace-1&notebook_id=",
    "workspace_id=workspace-1&notebook_id=notebook-1&notebook_id=notebook-2",
    "workspace_id=workspace-1&workspace_id=workspace-2&notebook_id=notebook-1",
    "workspace_id=workspace-1&notebook_id=../other",
    "workspace_id=workspace-1&notebook_id=notebook-1&extra=1"]) {
    const response = await proxy(new Request(`${origin}/bff/api/studio-generation-jobs/studio-job-1?${query}`), segments);
    assert.equal(response.status, 400, query);
  }
  assert.equal(calls, 0);
});

test("Studio job route permits only GET and retains upstream authentication and scope failures", async () => {
  let calls = 0;
  const proxy = createBffProxy({ baseUrl: new URL("https://api.example.com"), fetchImpl: async () => {
    calls++;
    return Response.json({ error: { code: "RESOURCE_UNAVAILABLE" } }, { status: 404 });
  } });
  const url = `${origin}/bff/api/studio-generation-jobs/studio-job-1?workspace_id=workspace-1&notebook_id=notebook-2`;
  assert.equal((await proxy(new Request(url, { method: "POST" }), segments)).status, 405);
  assert.equal(calls, 0);
  assert.equal((await proxy(new Request(url), segments)).status, 404);
  assert.equal(calls, 1);
  const unauthenticated = createBffProxy({ baseUrl: new URL("https://api.example.com"), fetchImpl: async () =>
    Response.json({ error: { code: "AUTHENTICATION_REQUIRED" } }, { status: 401 }) });
  assert.equal((await unauthenticated(new Request(url), segments)).status, 401);
});


test('E 생성 인증 BFF는 정확POST만 허용하고 같은 논리키와 세션을 전달한다',async()=>{
  const calls=[];const proxy=createBffProxy({baseUrl:new URL('https://api.example.com'),fetchImpl:async(url,init)=>{calls.push({url:String(url),init});return Response.json({data:{}},{status:201});}});
  const path=['studio-generation-requests','authorization'],url=origin+'/bff/api/'+path.join('/');
  const request=new Request(url,{method:'POST',headers:{Origin:origin,Cookie:'__Host-daon_session=fixture','Content-Type':'application/json','Idempotency-Key':'studio-generation-auth-key-1'},body:JSON.stringify({operation:'generate'})});
  const response=await proxy(request,path);assert.equal(response.status,201);
  assert.equal(calls.length,1);assert.equal(calls[0].url,'https://api.example.com/api/v1/studio-generation-requests/authorization');
  assert.equal(calls[0].init.headers.get('idempotency-key'),'studio-generation-auth-key-1');
  assert.equal((await proxy(new Request(url),path)).status,405);
  assert.equal(calls.length,1);
});
