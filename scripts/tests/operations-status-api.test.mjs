import assert from "node:assert/strict";
import test from "node:test";

import { getWorkspaceOperationsStatus } from "../../apps/web/lib/product-workspace-api.js";

const payload = {
  data: {
    workspace_id: "workspace-1",
    overall_status: "warning",
    checked_at: "2026-08-15T00:00:00Z",
    components: [
      { component_id: "provider", status: "ready", safe_code: "PROVIDER_READY", pending_count: 0, recovery_action: "none" },
      { component_id: "api", status: "ready", safe_code: "API_READY", pending_count: 0, recovery_action: "none" },
      { component_id: "storage", status: "ready", safe_code: "STORAGE_READY", pending_count: 0, recovery_action: "none" },
      { component_id: "sync", status: "warning", safe_code: "SYNC_PENDING", pending_count: 2, recovery_action: "open_sync_settings" },
      { component_id: "queue", status: "warning", safe_code: "QUEUE_ATTENTION_REQUIRED", pending_count: 3, recovery_action: "refresh_status" },
    ],
  },
  meta: { trace_id: "trace-1", workspace_id: "workspace-1" },
};

test("운영상태 Adapter는 exact 5개 안전 상태만 same-origin으로 수용한다", async () => {
  const calls = [];
  const result = await getWorkspaceOperationsStatus("workspace-1", {
    fetchImpl: async (url, init) => {
      calls.push({ url, method: init.method, credentials: init.credentials });
      return Response.json(payload);
    },
  });
  assert.equal(result.overall_status, "warning");
  assert.deepEqual(result.components.map((item) => item.component_id), ["provider", "api", "storage", "sync", "queue"]);
  assert.deepEqual(calls, [{
    url: "/bff/api/workspaces/workspace-1/operations/status",
    method: "GET",
    credentials: "same-origin",
  }]);
});

test("운영상태 Adapter는 unknown 필드와 내부값을 거부한다", async () => {
  await assert.rejects(
    getWorkspaceOperationsStatus("workspace-1", {
      fetchImpl: async () => Response.json({
        ...payload,
        data: { ...payload.data, internal_url: "http://database:5432" },
      }),
    }),
    /OPERATIONS_STATUS_RESPONSE_INVALID/u,
  );
});
