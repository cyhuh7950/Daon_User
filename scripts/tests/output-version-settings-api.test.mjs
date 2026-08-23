import assert from "node:assert/strict";
import test from "node:test";

import {
  getWorkspaceOutputVersionSettings,
  saveWorkspaceOutputVersionSettings,
} from "../../apps/web/lib/product-workspace-api.js";

const workspace = "workspace-output-settings";
const defaults = Object.freeze({
  evidence_report: "pdf",
  compliance_checklist: "xlsx",
  comparison_table: "xlsx",
  knowledge_map: "json",
  business_draft: "docx",
});

function response(data, version, ok = true) {
  return {
    ok,
    headers: { get: (name) => name.toLowerCase() === "etag" ? `"output-version-settings:${workspace}:${version}"` : null },
    json: async () => ({ data: { workspace_id: workspace, default_formats: data, version_save_mode: "append_only", version }, meta: { trace_id: "trace-output-settings", workspace_id: workspace } }),
  };
}

test("output settings GET uses same-origin and validates the append-only projection", async () => {
  const calls = [];
  const value = await getWorkspaceOutputVersionSettings(workspace, { fetchImpl: async (...args) => { calls.push(args); return response(defaults, 0); } });
  assert.equal(calls[0][0], `/bff/api/workspaces/${workspace}/output-version-settings`);
  assert.equal(calls[0][1].method, "GET");
  assert.equal(value.default_formats.knowledge_map, "json");
  assert.equal(value.etag, `"output-version-settings:${workspace}:0"`);
});

test("output settings PATCH sends exact ETag, idempotency and supported formats", async () => {
  const calls = [];
  const formats = { ...defaults, evidence_report: "docx" };
  const value = await saveWorkspaceOutputVersionSettings(
    workspace,
    { default_formats: formats, version: 0, etag: `"output-version-settings:${workspace}:0"` },
    { idempotencyKey: "output-settings-idem-0001", fetchImpl: async (...args) => { calls.push(args); return response(formats, 1); } },
  );
  assert.equal(calls[0][1].method, "PATCH");
  assert.equal(calls[0][1].headers["If-Match"], `"output-version-settings:${workspace}:0"`);
  assert.deepEqual(JSON.parse(calls[0][1].body), { default_formats: formats, expected_version: 0 });
  assert.equal(value.version, 1);
});

test("output settings rejects unknown formats and mismatched ETags before fetch", async () => {
  let calls = 0;
  await assert.rejects(
    saveWorkspaceOutputVersionSettings(workspace, { default_formats: { ...defaults, knowledge_map: "docx" }, version: 0, etag: `"output-version-settings:${workspace}:0"` }, { idempotencyKey: "output-settings-idem-0002", fetchImpl: async () => { calls += 1; } }),
    /OUTPUT_VERSION_SETTINGS_INPUT_INVALID/,
  );
  assert.equal(calls, 0);
});
