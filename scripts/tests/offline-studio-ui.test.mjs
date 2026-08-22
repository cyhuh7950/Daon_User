import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  canConfirmOfflineStudioSettings,
  createOfflineStudioState,
  reduceOfflineStudioState,
} from "../../apps/desktop/src/offline-studio-model.js";


test("offline Studio state is immutable and stale async responses are ignored", () => {
  const initial = createOfflineStudioState();
  assert.equal(Object.isFrozen(initial), true);
  assert.equal(initial.context.mode, "daon_priority");
  const requested = reduceOfflineStudioState(initial, { type: "request_started", revision: 2 });
  const stale = reduceOfflineStudioState(requested, {
    type: "context_ready", revision: 1,
    context: { mode: "mixed", snapshotId: "stale", items: [], warnings: [] },
  });
  assert.equal(stale, requested);
});

test("settings require eligible local model and mode-specific knowledge", () => {
  const model = { deployment_id: "deployment-1", provider_code: "OLLAMA", provider_kind: "server_internal", readiness: "ready" };
  let state = createOfflineStudioState({ models: [model], selectedModelDeploymentId: "deployment-1" });
  assert.equal(canConfirmOfflineStudioSettings(state), false);
  state = reduceOfflineStudioState(state, {
    type: "context_changed",
    context: {
      mode: "mixed", snapshotId: "scope-1", warnings: [],
      items: [
        { origin: "daon_knowledge", version_id: "knowledge-1" },
        { origin: "raw_source", version_id: "source-1" },
      ],
    },
  });
  assert.equal(canConfirmOfflineStudioSettings(state), true);
  const rawOnly = reduceOfflineStudioState(state, {
    type: "context_changed",
    context: { mode: "raw_only", snapshotId: "scope-2", items: [{ origin: "raw_source", version_id: "source-1" }], warnings: [] },
  });
  assert.deepEqual(rawOnly.context.warnings, ["RAW_SOURCE_ONLY"]);
  const legacy = createOfflineStudioState({
    context: state.context,
    models: [{ deployment_id: "legacy", provider_kind: "local_runtime", readiness: "ready" }],
    selectedModelDeploymentId: "legacy",
  });
  assert.equal(canConfirmOfflineStudioSettings(legacy), false);
});

test("failure preserves draft versions and sources while safe error changes", () => {
  const current = createOfflineStudioState({
    status: "ready", draft: { draft_id: "draft-1" },
    versions: [{ output_version_id: "version-1" }],
    sources: [{ source_version_id: "source-1" }], requestRevision: 4,
  });
  const failed = reduceOfflineStudioState(current, {
    type: "request_failed", revision: 4, safeError: "LOCAL_MODEL_TIMEOUT",
  });
  assert.deepEqual(failed.draft, current.draft);
  assert.deepEqual(failed.versions, current.versions);
  assert.deepEqual(failed.sources, current.sources);
  assert.equal(failed.safeError, "LOCAL_MODEL_TIMEOUT");
});

test("only imported local raw sources can be selected into context", () => {
  let state = createOfflineStudioState();
  state = reduceOfflineStudioState(state, {
    type: "raw_sources_ready",
    rawSources: [{
      source_version_id: "raw-source-1:v1",
      filename: "evidence.txt",
      digest_sha256: "a".repeat(64),
      quality_state: "unverified",
    }],
  });
  state = reduceOfflineStudioState(state, {
    type: "raw_source_selected",
    sourceVersionId: "raw-source-1:v1",
    selected: true,
  });
  assert.deepEqual(state.selectedRawSourceVersionIds, ["raw-source-1:v1"]);
  assert.deepEqual(state.context.items, [{
    origin: "raw_source",
    item_id: "raw-source-1:v1",
    version_id: "raw-source-1:v1",
    authority: "user_source",
    quality_state: "unverified",
    digest: "a".repeat(64),
  }]);
  const unchanged = reduceOfflineStudioState(state, {
    type: "raw_source_selected",
    sourceVersionId: "cloud-source-version-1",
    selected: true,
  });
  assert.equal(unchanged, state);
});

test("Desktop pane keeps three-pane slot and contains no browser network or persistent secret storage", async () => {
  const pane = await readFile(new URL("../../apps/desktop/src/offline-studio-pane.jsx", import.meta.url), "utf8");
  const shell = await readFile(new URL("../../packages/ui/src/product-workspace-shell.jsx", import.meta.url), "utf8");
  assert.match(shell, /desktopOfflineStudio/);
  assert.match(shell, /product-pane-sources/);
  assert.match(shell, /product-pane-conversation/);
  assert.match(shell, /product-pane-studio/);
  assert.match(pane, /Daon 지식 우선/);
  assert.match(pane, /Raw Source만/);
  assert.match(pane, /data-offline-editor/);
  assert.doesNotMatch(pane, /fetch\s*\(|XMLHttpRequest|WebSocket|localStorage|sessionStorage|NEXT_PUBLIC_|https?:\/\/|localhost|127\.0\.0\.1/iu);
});

test("Desktop Studio source wires the exact prepare confirm generate edit queue flow", async () => {
  const pane = await readFile(new URL("../../apps/desktop/src/offline-studio-pane.jsx", import.meta.url), "utf8");
  for (const call of ["listRawSources", "importRawSource", "prepareContext", "confirmSettings", "generateDraft", "appendEdit", "queueSync"]) {
    assert.match(pane, new RegExp(`studioAdapter\\.${call}`));
  }
  assert.match(pane, /context_snapshot_id/u);
  assert.doesNotMatch(pane, /knowledge_context_snapshot_id/u);
  assert.match(pane, /request_id/u);
  assert.match(pane, /previous_version_id/u);
  assert.match(pane, /output_version_id/u);
});
