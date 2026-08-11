import assert from "node:assert/strict";
import test from "node:test";

import { listWorkspaceSources } from "../../apps/web/lib/product-workspace-api.js";

const source = Object.freeze({
  source_id: "source-1", source_version_id: "version-1", filename: "approved.pdf",
  source_state: "ready", processing_state: "completed", job_state: "completed",
});

test("Source 목록은 same-origin GET과 exact Safe DTO만 허용한다", async () => {
  const calls = [];
  const result = await listWorkspaceSources("workspace-1", {
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return Response.json({
        data: { sources: [source] },
        meta: { trace_id: "trace-1", workspace_id: "workspace-1" },
      });
    },
  });
  assert.deepEqual(result, [source]);
  assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/sources");
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.credentials, "same-origin");
});

test("Source 목록은 unknown field와 Workspace 불일치를 거부한다", async () => {
  for (const payload of [
    { data: { sources: [{ ...source, fixture: true }] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } },
    { data: { sources: [source] }, meta: { trace_id: "trace-1", workspace_id: "workspace-other" } },
  ]) {
    await assert.rejects(
      listWorkspaceSources("workspace-1", { fetchImpl: async () => Response.json(payload) }),
      { message: "SOURCE_LIST_RESPONSE_INVALID" },
    );
  }
});
