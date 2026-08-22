import assert from "node:assert/strict";
import test from "node:test";

import { createOfflineSyncAdapter } from "../../apps/desktop/src/offline-sync-adapter.js";

test("offline sync adapter exposes exactly eight invoke-only commands", async () => {
  const calls = [];
  const adapter = createOfflineSyncAdapter({ invoke: async (command, payload) => {
    calls.push([command, payload]);
    return { state: "awaiting_approval" };
  }});
  await adapter.listKnowledge({ workspaceId: "workspace-1" });
  await adapter.provisionKnowledge({ workspaceId: "workspace-1", packageId: "package-1", stepUpAuthorizationId: "secret" });
  await adapter.refreshKnowledge({ workspaceId: "workspace-1", packageId: "package-1" });
  await adapter.previewSync({ workspaceId: "workspace-1" });
  await adapter.syncStatus({ operationId: "operation-1" });
  await adapter.approveSync({ operationId: "operation-1", stepUpAuthorizationId: "secret" });
  await adapter.transferSync({ operationId: "operation-1", resume: true });
  await adapter.resolveSync({ operationId: "operation-1", conflictId: "conflict-1", resolution: "keep_local" });
  assert.deepEqual(calls.map(([command]) => command), [
    "offline_knowledge_list", "offline_knowledge_provision", "offline_knowledge_refresh",
    "offline_sync_preview", "offline_sync_status", "offline_sync_approve",
    "offline_sync_transfer", "offline_sync_resolve",
  ]);
});

test("adapter rejects unsafe projections and clears function-local step-up input", async () => {
  let captured;
  const secret = { value: "step-up-secret" };
  const adapter = createOfflineSyncAdapter({ invoke: async (_command, payload) => {
    captured = structuredClone(payload);
    return { state: "automatic_transfer" };
  }});
  await assert.rejects(adapter.approveSync({ operationId: "operation-1", stepUpAuthorizationId: secret }), /OFFLINE_SYNC_RESPONSE_REJECTED/u);
  assert.equal(secret.value, "");
  assert.equal(captured.request.stepUpAuthorizationId, "step-up-secret");
});

test("adapter source has no browser network or internal endpoint", async () => {
  const source = await import("node:fs/promises").then(({ readFile }) => readFile(new URL("../../apps/desktop/src/offline-sync-adapter.js", import.meta.url), "utf8"));
  assert.doesNotMatch(source, /\b(?:fetch|XMLHttpRequest|WebSocket)\s*\(/u);
  assert.doesNotMatch(source, /(?:localhost|127\.0\.0\.1|https?:\/\/|\/api\/v1\/)/u);
});
