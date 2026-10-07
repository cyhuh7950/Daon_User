import assert from "node:assert/strict";
import test from "node:test";

import { listSessionTenants, switchSessionTenant } from "../../apps/web/lib/auth-api.js";

test("Web session tenant helper uses same-origin BFF and accepts only safe projections", async () => {
  const requests = [];
  const fetchImpl = async (path, init) => {
    requests.push({ path, method: init.method, credentials: init.credentials, body: init.body });
    if (init.method === "GET") return Response.json({
      data: { current_tenant_id: "tenant-personal", tenants: [
        { tenant_id: "tenant-personal", display_name: "개인", kind: "personal", workspace_id: "workspace-personal" },
        { tenant_id: "tenant-org", display_name: "조직", kind: "organization", workspace_id: "workspace-org" },
      ] }, meta: { trace_id: "trace-001" },
    });
    return Response.json({
      data: { user_id: "user-001", tenant_id: "tenant-org", workspace_id: "workspace-org", session_id: "session-new" },
      meta: { trace_id: "trace-002" },
    });
  };
  const listed = await listSessionTenants({ fetchImpl });
  const switched = await switchSessionTenant("tenant-org", { fetchImpl });

  assert.equal(listed.current_tenant_id, "tenant-personal");
  assert.equal(listed.tenants[1].display_name, "조직");
  assert.deepEqual(switched, { user_id: "user-001", tenant_id: "tenant-org",
    workspace_id: "workspace-org", session_id: "session-new" });
  assert.deepEqual(requests, [
    { path: "/bff/api/session/tenants", method: "GET", credentials: "same-origin", body: undefined },
    { path: "/bff/api/session/tenant", method: "POST", credentials: "same-origin",
      body: '{"tenant_id":"tenant-org"}' },
  ]);
});
