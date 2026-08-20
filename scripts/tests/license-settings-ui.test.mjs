import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("License client는 safe projection만 받고 exact same-origin BFF를 사용한다", async () => {
  const api = await import("../../apps/web/lib/license-api.js");
  const safe = {
    product: "daon-user", edition: "enterprise", license_id_hint: "…1-001",
    issued_at: "2026-08-14T08:00:00Z", expires_at: "2027-08-15T08:00:00Z", status: "active",
    features: ["citation"], resources: [{ resource: "generation_runs", limit: 100, used: 2, remaining: 98, status: "available" }],
    warning: null, creation_allowed: true, existing_read_allowed: true, existing_export_allowed: true, can_apply: true,
  };
  const requests = [];
  const fetchImpl = async (path, options) => {
    requests.push({ path, method: options.method, body: options.body });
    return Response.json({ data: safe, meta: { trace_id: "trace-license-001", workspace_id: "workspace-001" } });
  };
  assert.deepEqual(await api.getWorkspaceLicense("workspace-001", { fetchImpl }), safe);
  await api.applyOrganizationLicense("tenant-001", { schema_version: 1 }, "step-up", { fetchImpl, idempotencyKey: "license-apply-idem-0001" });
  assert.deepEqual(requests.map(({ path, method }) => ({ path, method })), [
    { path: "/bff/api/workspaces/workspace-001/license", method: "GET" },
    { path: "/bff/api/organizations/tenant-001/license", method: "POST" },
  ]);
  assert.match(requests[1].body, /step_up_authorization_id/);

  const invalidViews = [
    { ...safe, license_id_hint: "…too-long" },
    { ...safe, issued_at: "August 14, 2026" },
    { ...safe, features: ["citation", "citation"] },
    { ...safe, resources: [{ ...safe.resources[0], status: "warning" }] },
    { ...safe, resources: [{ ...safe.resources[0], remaining: 99 }] },
    { ...safe, warning: { code: "INTERNAL_DECISION", action: "unsafe" } },
  ];
  for (const data of invalidViews) {
    await assert.rejects(
      api.getWorkspaceLicense("workspace-001", {
        fetchImpl: async () => Response.json({ data, meta: { trace_id: "trace-license-invalid" } }),
      }),
      /LICENSE_RESPONSE_INVALID/u,
    );
  }
});

test("License OpenAPI projection은 client가 fail-close하는 enum과 형식을 고정한다", async () => {
  const contract = JSON.parse(await read("packages/contracts/openapi/v1/openapi.json"));
  const schemas = contract.components.schemas;
  assert.deepEqual(schemas.LicenseView.properties.status.enum, ["not_configured", "active", "expiring_soon", "expired", "limit_reached"]);
  assert.equal(schemas.LicenseView.properties.license_id_hint.oneOf[0].pattern, "^…[^\\s]{5}$");
  assert.equal(schemas.LicenseView.properties.issued_at.oneOf[0].format, "date-time");
  assert.deepEqual(schemas.LicenseResourceUsage.properties.status.enum, ["available", "limit_reached"]);
  assert.equal(schemas.LicenseView.properties.features.items.pattern, "^[a-z][a-z0-9_]{0,63}$");
});

test("Workspace 설정에는 일반 사용자 read-only와 관리자 Step-up 적용 경계가 있다", async () => {
  const [shell, actual, css] = await Promise.all([
    read("packages/ui/src/product-workspace-shell.jsx"),
    read("apps/web/components/actual-workspace.jsx"),
    read("packages/ui/src/workspace.css"),
  ]);
  assert.match(shell, /라이선스/);
  assert.match(shell, /can_apply/);
  assert.match(shell, /licenseFileRef/);
  assert.match(shell, /current-password/);
  assert.match(shell, /일반 사용자는 라이선스 정보를 읽기 전용으로 확인합니다/);
  assert.match(actual, /getWorkspaceLicense/);
  assert.match(actual, /applyCurrentOrganizationLicenseWithStepUp/);
  assert.match(css, /license-settings/);
});
