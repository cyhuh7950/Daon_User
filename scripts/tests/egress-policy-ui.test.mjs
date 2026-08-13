import assert from "node:assert/strict";
import test from "node:test";

import {
  createEgressPolicyDraft,
  egressPolicyReducer,
} from "../../packages/ui/src/egress-policy-model.js";
import {
  getEffectiveEgressPolicy,
  saveOrganizationEgressPolicy,
} from "../../apps/web/lib/egress-policy-api.js";


test("organization editor may replace its own deny while workspace parent lock remains explicit", () => {
  const loaded = egressPolicyReducer(undefined, { type: "loaded", data: {
    mode: "deny_external", parent_locked: true, allowed_provider_kinds: [],
    allowed_destinations: [], classification: "restricted", max_bytes: 0,
    masking_required: true, redaction_required: true,
    required_approver: "organization_admin", organization_etag: '"egress:org:v1"',
    workspace_etag: '"egress:workspace:v1"', editable_scope: "organization",
  } });
  const drafted = egressPolicyReducer(loaded, {
    type: "drafted", draft: createEgressPolicyDraft({ mode: "allow_approved_external" }),
  });
  assert.equal(drafted.effective.parent_locked, true);
  assert.equal(drafted.canSave, true);
  const failed = egressPolicyReducer(drafted, { type: "failed", code: "VERSION_CONFLICT" });
  assert.equal(failed.effective.mode, "deny_external");
  assert.equal(failed.errorCode, "VERSION_CONFLICT");
});


test("same-origin adapter clears current password and step-up token after save", async () => {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, init });
    if (String(url).endsWith("/session/step-up")) {
      const body = JSON.parse(init.body);
      assert.equal(init.headers["idempotency-key"], "idem-egress-policy-0001");
      assert.deepEqual(Object.keys(body).sort(), ["action_group", "password", "target_id"]);
      assert.equal(body.password, "memory-only");
      assert.equal("current_password" in body, false);
      return Response.json({ data: { step_up_authorization: "step-up-secret" }, meta: {} });
    }
    if (init.method === "POST") {
      const body = JSON.parse(init.body);
      assert.equal("workspace_id" in body, false);
      assert.equal(init.headers["if-match"], '"egress:org:v1"');
    }
    return Response.json({ data: { mode: "deny_external" }, meta: {} }, {
      status: 201, headers: { ETag: '"egress:v2"' },
    });
  };
  const sensitive = { currentPassword: "memory-only", stepUpAuthorization: null };
  await saveOrganizationEgressPolicy({
    fetchImpl, organizationId: "org-1", workspaceId: "workspace-1",
    etag: '"egress:org:v1"', idempotencyKey: "idem-egress-policy-0001",
    draft: createEgressPolicyDraft({ mode: "deny_external" }), sensitive,
  });
  assert.equal(calls[0].url, "/bff/api/session/step-up");
  assert.equal(calls[1].url, "/bff/api/organizations/org-1/egress-policy-versions");
  assert.equal(sensitive.currentPassword, "");
  assert.equal(sensitive.stepUpAuthorization, null);
  assert.ok(!JSON.stringify(calls[1]).includes("memory-only"));

  await getEffectiveEgressPolicy({ fetchImpl, workspaceId: "workspace-1" });
  assert.equal(calls[2].url, "/bff/api/workspaces/workspace-1/egress-policy");
});
