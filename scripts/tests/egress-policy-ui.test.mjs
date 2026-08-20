import assert from "node:assert/strict";
import test from "node:test";

import {
  createEgressPolicyDraft,
  egressPolicyReducer,
} from "../../packages/ui/src/egress-policy-model.js";
import {
  getEffectiveEgressPolicy,
  getOrganizationSettingsContext,
  saveOrganizationEgressPolicy,
  saveWorkspaceEgressPolicy,
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

test("context identity loading clears every previous editable policy state", () => {
  const loaded = egressPolicyReducer(undefined, { type: "loaded", data: {
    ...createEgressPolicyDraft({ mode: "allow_approved_external", allowed_provider_kinds: ["external_api"], allowed_destinations: ["api.upstage.ai"], classification: "internal", max_bytes: 1048576 }),
    organization_policy: createEgressPolicyDraft(), workspace_policy: createEgressPolicyDraft(),
    parent_locked: false, organization_etag: '"org:1"', workspace_etag: '"ws:1"', editable_scope: "organization",
  } });
  const drafted = egressPolicyReducer(loaded, { type: "drafted", draft: { ...loaded.draft, allowed_destinations: ["stale.example"] } });
  const reset = egressPolicyReducer(drafted, { type: "context_loading" });
  assert.equal(reset.status, "loading"); assert.equal(reset.effective, null);
  assert.deepEqual(reset.draft, createEgressPolicyDraft()); assert.equal(reset.canSave, false); assert.equal(reset.errorCode, null);
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
    if ((init.method ?? "GET") === "GET") {
      const deny = createEgressPolicyDraft();
      return Response.json({ data: {
        organization_policy_version_id: "org-policy-v1", organization_binding_id: "org-binding-v1",
        workspace_policy_version_id: "workspace-policy-v1", workspace_binding_id: "workspace-binding-v1",
        ...deny, fingerprint: `sha256:${"a".repeat(64)}`, parent_locked: true,
        organization_etag: '"egress:org:v1"', workspace_etag: '"egress:workspace:v1"',
        organization_policy: deny, workspace_policy: deny,
      }, meta: { trace_id: "trace-1" } }, { headers: { ETag: '"egress:effective:v1"' } });
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

test("effective egress adapter는 exact read-only projection만 수용하고 내부 필드를 거부한다", async () => {
  const payload = {
    mode: "allow_approved_external", allowed_provider_kinds: ["external_api"],
    allowed_destinations: ["api.example.com"], classification: "internal", max_bytes: 4096,
    masking_required: true, redaction_required: false, required_approver: "organization_admin",
  };
  const projection = {
    organization_policy_version_id: "org-policy-v1", organization_binding_id: "org-binding-v1",
    workspace_policy_version_id: "workspace-policy-v1", workspace_binding_id: "workspace-binding-v1",
    ...payload, fingerprint: `sha256:${"a".repeat(64)}`, parent_locked: false,
    organization_etag: '"org:1"', workspace_etag: '"workspace:1"',
    organization_policy: payload, workspace_policy: payload,
  };
  const valid = await getEffectiveEgressPolicy({
    workspaceId: "workspace-1",
    fetchImpl: async () => Response.json({ data: projection, meta: { trace_id: "trace-1" } }, { headers: { ETag: '"effective:1"' } }),
  });
  assert.deepEqual(valid.data.organization_policy, payload);
  assert.equal(valid.etag, '"effective:1"');

  await assert.rejects(() => getEffectiveEgressPolicy({
    workspaceId: "workspace-1",
    fetchImpl: async () => Response.json({ data: { ...projection, internal_url: "http://internal.invalid" }, meta: { trace_id: "trace-1" } }),
  }), /EGRESS_POLICY_RESPONSE_INVALID/u);
});

test("workspace policy uses a separate Step-up and exact same-origin endpoint", async () => {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, init });
    if (String(url).endsWith("/session/step-up")) {
      return Response.json({ data: { step_up_authorization: "step-up-secret" }, meta: {} });
    }
    return Response.json({ data: { scope_type: "workspace" }, meta: {} }, { status: 201 });
  };
  const sensitive = { currentPassword: "workspace-memory-only", stepUpAuthorization: null };
  await saveWorkspaceEgressPolicy({
    fetchImpl, workspaceId: "workspace-1", etag: '"egress:workspace:v1"',
    idempotencyKey: "idem-egress-policy-workspace-0001",
    draft: createEgressPolicyDraft({ mode: "deny_external" }), sensitive,
  });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "/bff/api/session/step-up");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    action_group: "organization_security_or_connector_policy_change",
    target_id: "workspace-1", password: "workspace-memory-only",
  });
  assert.equal(calls[1].url, "/bff/api/workspaces/workspace-1/egress-policy-versions");
  assert.equal(calls[1].init.headers["if-match"], '"egress:workspace:v1"');
  assert.equal("workspace_id" in JSON.parse(calls[1].init.body), false);
  assert.equal(sensitive.currentPassword, "");
  assert.equal(sensitive.stepUpAuthorization, null);
  assert.ok(!JSON.stringify(calls[1]).includes("workspace-memory-only"));
});

test("aborted deferred Step-up clears sensitive values and sends no policy POST", async () => {
  const controller = new AbortController(); const calls = []; let rejectStepUp;
  const stepUp = new Promise((_, reject) => { rejectStepUp = reject; });
  const fetchImpl = async (url) => { calls.push(String(url)); return stepUp; };
  const sensitive = { currentPassword: "memory-only", stepUpAuthorization: "stale-token" };
  const request = saveWorkspaceEgressPolicy({
    fetchImpl, workspaceId: "workspace-old", etag: '"ws:old"', idempotencyKey: "idem-egress-policy-abort-0001",
    draft: createEgressPolicyDraft(), sensitive, signal: controller.signal,
  });
  controller.abort(); const error = new Error("aborted"); error.name = "AbortError"; rejectStepUp(error);
  await assert.rejects(request, /aborted/u);
  assert.deepEqual(calls, ["/bff/api/session/step-up"]);
  assert.equal(sensitive.currentPassword, ""); assert.equal(sensitive.stepUpAuthorization, null);
});

test("organization settings context GET forwards AbortSignal", async () => {
  const controller = new AbortController(); let received;
  const fetchImpl = async (_url, init) => {
    received = init.signal;
    return Response.json({ data: { tenant_id: "tenant-1", workspace_id: "workspace-1" }, meta: {} });
  };
  await getOrganizationSettingsContext({ fetchImpl, signal: controller.signal });
  assert.equal(received, controller.signal);
});
