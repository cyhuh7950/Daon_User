import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createWindowsWorkspaceAdapter } from "../../apps/desktop/src/windows-workspace-adapter.js";

const view = {
  product: "daon-user", edition: "enterprise", license_id_hint: "…1-001",
  issued_at: "2026-08-14T08:00:00Z", expires_at: "2027-08-15T08:00:00Z", status: "active",
  features: ["citation"], resources: [{ resource: "generation_runs", limit: 100, used: 2, remaining: 98, status: "available" }],
  warning: null, creation_allowed: true, existing_read_allowed: true, existing_export_allowed: true, can_apply: true,
};

test("Windows License adapter는 Native command에 transient document/password만 전달한다", async () => {
  const calls = [];
  const invoke = async (command, args) => { calls.push({ command, args }); return view; };
  const adapter = createWindowsWorkspaceAdapter("workspace-001", { invoke, organizationId: "tenant-001", notebookId: "notebook-001" });
  assert.deepEqual(await adapter.getLicense(), view);
  assert.deepEqual(await adapter.applyLicense({ schema_version: 1 }, "not-a-real-password", { idempotencyKey: "license-apply-idem-0001" }), view);
  assert.deepEqual(calls.map(({ command }) => command), ["workspace_get_license", "workspace_apply_license"]);
  assert.equal(calls[1].args.input.organization_id, "tenant-001");
  assert.equal(calls[1].args.input.password, "not-a-real-password");
});

test("Windows License adapter는 OpenAPI와 다른 safe projection을 fail-close한다", async () => {
  const invalidViews = [
    { ...view, license_id_hint: "not-masked" },
    { ...view, issued_at: "2026/08/14" },
    { ...view, features: ["citation", "citation"] },
    { ...view, resources: [{ ...view.resources[0], resource: "internal_cost" }] },
    { ...view, resources: [{ ...view.resources[0], status: "warning" }] },
    { ...view, resources: [{ ...view.resources[0], remaining: 99 }] },
    { ...view, warning: { code: "INTERNAL_DECISION", action: "unsafe" } },
  ];
  for (const invalid of invalidViews) {
    const adapter = createWindowsWorkspaceAdapter("workspace-001", {
      invoke: async () => invalid, organizationId: "tenant-001", notebookId: "notebook-001",
    });
    await assert.rejects(adapter.getLicense(), /WORKSPACE_RESPONSE_REJECTED/u);
  }
});

test("Windows Native License bridge는 read와 Step-up apply command를 등록한다", async () => {
  const [bridge, runtime, lib] = await Promise.all([
    readFile(new URL("../../apps/desktop/src-tauri/src/workspace_bridge.rs", import.meta.url), "utf8"),
    readFile(new URL("../../apps/desktop/src-tauri/src/native_session.rs", import.meta.url), "utf8"),
    readFile(new URL("../../apps/desktop/src-tauri/src/lib.rs", import.meta.url), "utf8"),
  ]);
  assert.match(bridge, /workspace_get_license/);
  assert.match(bridge, /workspace_apply_license/);
  assert.match(runtime, /GetLicense/);
  assert.match(runtime, /ApplyLicense/);
  assert.match(lib, /workspace_get_license/);
  assert.match(lib, /workspace_apply_license/);
});

test("Desktop License actual harness는 product shell을 재사용하고 제품 entry에 연결되지 않는다", async () => {
  const [entry, config, desktopPackage] = await Promise.all([
    readFile(new URL("../test-harness/desktop-license-settings/main.jsx", import.meta.url), "utf8"),
    readFile(new URL("../test-harness/desktop-license-settings/vite.config.mjs", import.meta.url), "utf8"),
    readFile(new URL("../../apps/desktop/package.json", import.meta.url), "utf8"),
  ]);
  assert.match(entry, /ProductWorkspaceShell/);
  assert.match(entry, /can_apply: mode === "admin"/);
  assert.match(entry, /event\.key === "F8"/);
  assert.match(entry, /event\.key === "F9"/);
  assert.match(entry, /key=\{mode\}/);
  assert.match(config, /DAON_DESKTOP_LICENSE_EVIDENCE_DIST/);
  assert.doesNotMatch(entry, /fetch\(|localhost|127\.0\.0\.1/);
  assert.doesNotMatch(desktopPackage, /desktop-license-settings/);
});
