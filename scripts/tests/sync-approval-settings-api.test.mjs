import assert from "node:assert/strict";
import test from "node:test";

import { approveWorkspaceSyncOperation, listWorkspaceSyncOperations } from "../../apps/web/lib/sync-approval-settings-api.js";

const workspace = "workspace-sync-settings";
const operation = Object.freeze({
  operation_id: "sync-operation-1", tenant_id: "tenant-1", workspace_id: workspace,
  actor_id: "actor-1", target_area: "cloud_sync", state: "awaiting_approval", version: 1,
  manifest_digest: "a".repeat(64), item_ids: ["item-1"], approved_item_ids: [],
  completed_item_ids: [], batches: [], conflicts: [], target_versions: [], reindex_state: null,
  source_mutations: 0, overwrite_count: 0,
});

function response(data, etag = null, ok = true) {
  return { ok, headers: { get: (name) => name.toLowerCase() === "etag" ? etag : null }, json: async () => data };
}

test("sync settings lists current workspace operations over same-origin", async () => {
  const calls = [];
  const value = await listWorkspaceSyncOperations(workspace, { fetchImpl: async (...args) => {
    calls.push(args); return response({ data: { operations: [operation] }, meta: { trace_id: "trace-1", workspace_id: workspace } });
  } });
  assert.equal(calls[0][0], `/bff/api/workspaces/${workspace}/sync-operations`);
  assert.equal(calls[0][1].method, "GET");
  assert.deepEqual(value[0].item_ids, ["item-1"]);
});

test("sync settings approves only selected preview items with exact step-up and ETag", async () => {
  const calls = [];
  const approved = { ...operation, state: "approved", version: 2, approved_item_ids: ["item-1"] };
  const value = await approveWorkspaceSyncOperation(
    workspace, operation,
    { approvedItemIds: ["item-1"], stepUpAuthorizationId: "step-up-sync-1" },
    { idempotencyKey: "sync-approval-idem-0001", fetchImpl: async (...args) => {
      calls.push(args); return response({ data: approved, meta: { trace_id: "trace-2" } }, '"sync:sync-operation-1:2"');
    } },
  );
  assert.equal(calls[0][0], "/bff/api/sync-operations/sync-operation-1/approve");
  assert.equal(calls[0][1].headers["If-Match"], '"sync:sync-operation-1:1"');
  assert.deepEqual(JSON.parse(calls[0][1].body), { approved_item_ids: ["item-1"], step_up_authorization_id: "step-up-sync-1" });
  assert.equal(value.state, "approved");
});

test("sync settings rejects scope expansion before network", async () => {
  let calls = 0;
  await assert.rejects(
    approveWorkspaceSyncOperation(workspace, operation, { approvedItemIds: ["item-2"], stepUpAuthorizationId: "step-up-sync-1" }, { idempotencyKey: "sync-approval-idem-0002", fetchImpl: async () => { calls += 1; } }),
    /SYNC_SETTINGS_INPUT_INVALID/,
  );
  assert.equal(calls, 0);
});
