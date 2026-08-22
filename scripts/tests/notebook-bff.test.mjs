import assert from "node:assert/strict";
import test from "node:test";

import { createBffProxy } from "../../apps/web/lib/bff-api-proxy.js";

const COOKIE = "__Host-daon_session=opaque-session";
const ORIGIN = "https://app.example.com";

function makeProxy(captured) {
  return createBffProxy({
    baseUrl: new URL("https://api.example.com"),
    publicOrigin: new URL(ORIGIN),
    fetchImpl: async (url, init) => {
      captured.push({
        url: String(url), method: init.method,
        idempotency: init.headers.get("idempotency-key"),
        ifMatch: init.headers.get("if-match"),
      });
      return Response.json({ data: [], meta: {} });
    },
  });
}

test("Notebook BFF는 collection/item exact method를 same-origin server route로 전달한다", async () => {
  const captured = [];
  const proxy = makeProxy(captured);
  const list = await proxy(new Request(`${ORIGIN}/bff/api/workspaces/workspace-1/notebooks`, { headers: { Cookie: COOKIE } }), ["workspaces", "workspace-1", "notebooks"]);
  const create = await proxy(new Request(`${ORIGIN}/bff/api/workspaces/workspace-1/notebooks`, {
    method: "POST", headers: { Cookie: COOKIE, Origin: ORIGIN, "Content-Type": "application/json", "Idempotency-Key": "notebook-create-0001" }, body: '{"title":"Notebook"}',
  }), ["workspaces", "workspace-1", "notebooks"]);
  const get = await proxy(new Request(`${ORIGIN}/bff/api/workspaces/workspace-1/notebooks/notebook-1`, { headers: { Cookie: COOKIE } }), ["workspaces", "workspace-1", "notebooks", "notebook-1"]);
  const update = await proxy(new Request(`${ORIGIN}/bff/api/workspaces/workspace-1/notebooks/notebook-1`, {
    method: "PATCH", headers: { Cookie: COOKIE, Origin: ORIGIN, "Content-Type": "application/json", "Idempotency-Key": "notebook-update-0001", "If-Match": '"notebook:1"' }, body: '{"title":"Renamed"}',
  }), ["workspaces", "workspace-1", "notebooks", "notebook-1"]);
  const context = await proxy(new Request(`${ORIGIN}/bff/api/workspaces/workspace-1/notebooks/notebook-1/context`, {
    headers: { Cookie: COOKIE },
  }), ["workspaces", "workspace-1", "notebooks", "notebook-1", "context"]);

  assert.deepEqual([list.status, create.status, get.status, update.status, context.status], [200, 200, 200, 200, 200]);
  assert.deepEqual(captured, [
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/notebooks", method: "GET", idempotency: null, ifMatch: null },
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/notebooks", method: "POST", idempotency: "notebook-create-0001", ifMatch: null },
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/notebooks/notebook-1", method: "GET", idempotency: null, ifMatch: null },
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/notebooks/notebook-1", method: "PATCH", idempotency: "notebook-update-0001", ifMatch: '"notebook:1"' },
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/notebooks/notebook-1/context", method: "GET", idempotency: null, ifMatch: null },
  ]);
});

test("Notebook BFF는 삭제와 cross-origin write를 upstream 전에 차단한다", async () => {
  const captured = [];
  const proxy = makeProxy(captured);
  const deleted = await proxy(new Request(`${ORIGIN}/bff/api/workspaces/workspace-1/notebooks/notebook-1`, { method: "DELETE", headers: { Cookie: COOKIE, Origin: ORIGIN } }), ["workspaces", "workspace-1", "notebooks", "notebook-1"]);
  const crossOrigin = await proxy(new Request(`${ORIGIN}/bff/api/workspaces/workspace-1/notebooks`, { method: "POST", headers: { Cookie: COOKIE, Origin: "https://evil.example", "Content-Type": "application/json", "Idempotency-Key": "notebook-create-0002" }, body: "{}" }), ["workspaces", "workspace-1", "notebooks"]);
  assert.deepEqual([deleted.status, crossOrigin.status], [405, 403]);
  assert.equal(captured.length, 0);
});

test("Connector Source BFF는 목록·등록·재연결·해제·Source 목록을 API 계약으로 전달한다", async () => {
  const captured = [];
  const proxy = makeProxy(captured);
  const requests = [
    ["GET", ["workspaces", "workspace-1", "connectors"]],
    ["POST", ["workspaces", "workspace-1", "connectors"]],
    ["POST", ["workspaces", "workspace-1", "connectors", "mcp-open-law-go-kr", "reconnect"]],
    ["POST", ["workspaces", "workspace-1", "connectors", "mcp-open-law-go-kr", "disconnect"]],
    ["GET", ["workspaces", "workspace-1", "connectors", "mcp-open-law-go-kr", "sources"]],
  ];
  const responses = [];
  for (const [method, path] of requests) {
    const response = await proxy(new Request(`${ORIGIN}/bff/api/${path.join("/")}`, {
      method,
      headers: method === "POST"
        ? { Cookie: COOKIE, Origin: ORIGIN, "Content-Type": "application/json", "Idempotency-Key": `connector-${responses.length + 1}` }
        : { Cookie: COOKIE },
      body: method === "POST" ? "{}" : undefined,
    }), path);
    responses.push(response.status);
  }
  assert.deepEqual(responses, [200, 200, 200, 200, 200]);
  assert.deepEqual(captured.map(({ url, method }) => ({ url, method })), [
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/connectors", method: "GET" },
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/connectors", method: "POST" },
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/connectors/mcp-open-law-go-kr/reconnect", method: "POST" },
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/connectors/mcp-open-law-go-kr/disconnect", method: "POST" },
    { url: "https://api.example.com/api/v1/workspaces/workspace-1/connectors/mcp-open-law-go-kr/sources", method: "GET" },
  ]);
});
