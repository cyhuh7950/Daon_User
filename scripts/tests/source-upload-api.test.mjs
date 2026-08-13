import assert from "node:assert/strict";
import test from "node:test";

import { listWorkspaceSources } from "../../apps/web/lib/product-workspace-api.js";

const source = Object.freeze({
  source_id: "source-1", source_version_id: "version-1", filename: "approved.pdf",
  source_state: "ready", processing_state: "completed", job_state: "completed",
});

const canonSourceStates = Object.freeze([
  "registered", "security_check", "processing", "indexing", "ready", "waiting_model",
  "partial_understanding", "needs_review", "failed", "expired", "disabled", "deleting", "deleted",
]);

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

test("Source 목록은 Canon의 진행·분기·종료 상태를 모두 안전 DTO로 반환한다", async () => {
  for (const source_state of canonSourceStates) {
    const projected = { ...source, source_state };
    const result = await listWorkspaceSources("workspace-1", {
      fetchImpl: async () => Response.json({
        data: { sources: [projected] },
        meta: { trace_id: "trace-1", workspace_id: "workspace-1" },
      }),
    });
    assert.deepEqual(result, [projected]);
  }
});

test("Source 목록은 Canon 밖의 unknown state를 계속 거부한다", async () => {
  await assert.rejects(
    listWorkspaceSources("workspace-1", {
      fetchImpl: async () => Response.json({
        data: { sources: [{ ...source, source_state: "unexpected" }] },
        meta: { trace_id: "trace-1", workspace_id: "workspace-1" },
      }),
    }),
    { message: "SOURCE_LIST_RESPONSE_INVALID" },
  );
});
