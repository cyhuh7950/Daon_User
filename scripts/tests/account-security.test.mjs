import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const root = path.resolve(".");
const modelPath = path.join(root, "packages/ui/src/account-security-model.js");
const read = (relative) => readFile(path.join(root, relative), "utf8").catch(() => "");
const loadModel = async () => existsSync(modelPath) ? import(`${pathToFileURL(modelPath).href}?t=${Date.now()}`) : {};

test("설계 역할 7개·세부 권한 8개·민감 작업 최소 7종을 정확히 고정한다", async () => {
  const model = await loadModel();
  assert.deepEqual(model.MEMBERSHIP_ROLES, ["personal_owner", "organization_admin", "workspace_admin", "editor", "reviewer", "approver", "viewer"]);
  assert.equal(model.DETAILED_PERMISSIONS?.length, 8);
  assert.equal(model.SENSITIVE_ACTIONS?.length, 7);
  assert.equal(new Set(model.DETAILED_PERMISSIONS).size, 8);
  assert.equal(new Set(model.SENSITIVE_ACTIONS).size, 7);
});

test("NavigationPersona는 MembershipRole과 분리되고 명시 Grant 없이는 권한을 만들지 않는다", async () => {
  const model = await loadModel();
  assert.equal(model.resolveMembershipRole({ persona: "operator", tenantKind: "organization", membership: null }), null);
  assert.equal(model.resolveMembershipRole({ persona: "organization_member", tenantKind: "organization", membership: null }), null);
  assert.equal(model.resolveMembershipRole({ persona: "personal_user", tenantKind: "organization", membership: null }), null);
  assert.equal(model.resolveMembershipRole({ persona: "personal_user", tenantKind: "personal", isOwner: true }), "personal_owner");
  assert.equal(model.resolveMembershipRole({ persona: "workspace_admin", tenantKind: "organization", membership: { role: "viewer", active: true } }), null);
  assert.equal(model.resolveMembershipRole({ persona: "workspace_admin", tenantKind: "organization", membership: { role: "workspace_admin", active: true } }), "workspace_admin");
});

test("역할 기본값과 Membership Grant·Revoke는 독립 권한만 바꾼다", async () => {
  const model = await loadModel();
  let state = model.createAccountSecurityViewState();
  const before = structuredClone(state.membershipGrants.editor);
  state = model.transitionAccountSecurityState(state, { type: "revoke-permission", role: "editor", permission: "internet_search" });
  assert.equal(state.membershipGrants.editor.internet_search, false);
  for (const permission of model.DETAILED_PERMISSIONS.filter((item) => item !== "internet_search")) {
    assert.equal(state.membershipGrants.editor[permission], before[permission], permission);
  }
});

test("Tenant·역할 상승·무권한 정책 변경은 안전 Code와 변경 0건을 만든다", async () => {
  const model = await loadModel();
  const state = model.createAccountSecurityViewState({ selectedRole: "viewer" });
  for (const action of [
    { type: "attempt-tenant-access", tenantId: "tenant-other" },
    { type: "attempt-role-escalation", requestedRole: "organization_admin" },
    { type: "preview-policy-change", role: "viewer", field: "egressPolicy", value: "unrestricted" }
  ]) {
    const next = model.transitionAccountSecurityState(state, action);
    assert.match(next.safety.code, /AUTHORIZATION_DENIED|CURRENT_ACCESS_DENIED/);
    assert.equal(next.domainMutationCount, 0);
    assert.equal(next.externalCallCount, 0);
  }
});

test("조직 정책은 요청값·유효값·잠금 사유·Version을 표시하고 완화를 차단한다", async () => {
  const model = await loadModel();
  const state = model.createAccountSecurityViewState();
  const policy = state.organizationPolicy;
  for (const field of ["ruleSet", "authorityBoost", "models", "providers", "runtimeNodes", "egressPolicy", "region", "localPrivate", "storageLimit", "tokenLimit", "costLimit", "retention", "reviewRequired", "deliveryTargets"]) assert.ok(Object.hasOwn(policy, field), field);
  const blocked = model.transitionAccountSecurityState(state, { type: "request-policy-relaxation", field: "egressPolicy", requestedValue: "unrestricted" });
  assert.equal(blocked.safety.code, "ORGANIZATION_POLICY_LOCKED");
  assert.equal(blocked.policyPreview.requestedValue, "unrestricted");
  assert.equal(blocked.policyPreview.effectiveValue, policy.egressPolicy.effectiveValue);
  assert.ok(blocked.policyPreview.lockReason);
  assert.equal(blocked.policyPreview.policyVersion, state.policyVersion);
});

test("Provider 화면 정본에는 불투명 ID와 정책만 있고 Secret·내부 주소가 없다", async () => {
  const model = await loadModel();
  const state = model.createAccountSecurityViewState();
  const serialized = JSON.stringify(state.providerProfiles);
  assert.doesNotMatch(serialized, /secret|credential|password|api[_-]?key|https?:\/\/|localhost|127\.0\.0\.1|:\d{2,5}/i);
  for (const profile of state.providerProfiles) {
    assert.match(profile.profileId, /^provider-profile-/);
    assert.match(profile.deploymentId, /^deployment-/);
    assert.ok(profile.dataRealm);
    assert.ok(profile.policyState);
  }
});

test("Step-up은 Actor·Action·Target·PolicyVersion 결합·만료·1회 사용을 강제한다", async () => {
  const model = await loadModel();
  let state = model.createAccountSecurityViewState();
  const without = model.transitionAccountSecurityState(state, { type: "perform-sensitive-action", action: "revoke_device", target: "device-windows-001" });
  assert.equal(without.safety.code, "STEP_UP_REQUIRED");
  assert.equal(without.domainMutationCount, 0);

  state = model.transitionAccountSecurityState(state, { type: "issue-step-up", actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion });
  for (const mismatch of [
    { actor: "actor-other", action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion },
    { actor: state.actorId, action: "register_knowledge", target: "device-windows-001", policyVersion: state.policyVersion },
    { actor: state.actorId, action: "revoke_device", target: "device-other", policyVersion: state.policyVersion },
    { actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: "policy-other" }
  ]) {
    const denied = model.transitionAccountSecurityState(state, { type: "perform-sensitive-action", ...mismatch });
    assert.equal(denied.safety.code, "STEP_UP_SCOPE_MISMATCH");
    assert.equal(denied.domainMutationCount, 0);
  }
  state = model.transitionAccountSecurityState(state, { type: "perform-sensitive-action", actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion });
  assert.equal(state.stepUpAuthorizations.at(-1).status, "used");
  assert.equal(state.devices.find((item) => item.id === "device-windows-001").status, "revoked");
  assert.equal(state.actualSessionRevocations, 0);
  assert.equal(state.actualSyncKeyRevocations, 0);
  const reused = model.transitionAccountSecurityState(state, { type: "perform-sensitive-action", actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion });
  assert.equal(reused.safety.code, "STEP_UP_ALREADY_USED");
});

test("만료 Step-up은 실패하고 종료된 Authorization은 불변이다", async () => {
  const model = await loadModel();
  let state = model.createAccountSecurityViewState();
  state = model.transitionAccountSecurityState(state, { type: "issue-step-up", actor: state.actorId, action: "move_data_realm", target: "source-private-001", policyVersion: state.policyVersion, expiresAt: "2026-07-22T10:00:00+09:00" });
  state = model.transitionAccountSecurityState(state, { type: "perform-sensitive-action", actor: state.actorId, action: "move_data_realm", target: "source-private-001", policyVersion: state.policyVersion, now: "2026-07-22T10:01:00+09:00" });
  assert.equal(state.safety.code, "STEP_UP_EXPIRED");
  assert.equal(state.stepUpAuthorizations.at(-1).status, "expired");
  const frozen = structuredClone(state.stepUpAuthorizations.at(-1));
  state = model.transitionAccountSecurityState(state, { type: "perform-sensitive-action", actor: state.actorId, action: "move_data_realm", target: "source-private-001", policyVersion: state.policyVersion });
  assert.deepEqual(state.stepUpAuthorizations.at(-1), frozen);
});

test("과거 결과는 현재 권한으로 매번 재판정하고 원본은 불변이다", async () => {
  const model = await loadModel();
  let state = model.createAccountSecurityViewState();
  const originalOutput = state.historicalOutputVersion;
  const originalRun = state.historicalRunSnapshot;
  const originalEvidence = state.historicalEvidenceReferences;
  for (const accessState of ["available", "partially_redacted", "access_blocked"]) {
    state = model.transitionAccountSecurityState(state, { type: "set-current-access", accessState });
    for (const operation of ["read", "citation", "export", "delivery", "knowledge_registration", "rerun"]) {
      const before = state.accessDecisions.length;
      state = model.transitionAccountSecurityState(state, { type: "evaluate-past-access", operation });
      assert.equal(state.accessDecisions.length, before + 1);
      assert.equal(state.accessDecisions.at(-1).state, accessState);
    }
  }
  assert.equal(state.historicalOutputVersion, originalOutput);
  assert.equal(state.historicalRunSnapshot, originalRun);
  assert.equal(state.historicalEvidenceReferences, originalEvidence);
  assert.equal(Object.isFrozen(originalOutput), true);
  assert.equal(state.accessDecisions.some((item) => item.state === "partially_redacted" && item.maskedReferences.length > 0), true);
  assert.equal(state.accessDecisions.some((item) => item.state === "access_blocked" && item.code === "CURRENT_ACCESS_DENIED"), true);
});

test("Rerun은 과거 결과를 되살리지 않고 현재 정책의 새 Preview 계보를 만든다", async () => {
  const model = await loadModel();
  let state = model.createAccountSecurityViewState();
  state = model.transitionAccountSecurityState(state, { type: "evaluate-past-access", operation: "rerun" });
  assert.equal(state.rerunPreviews.length, 1);
  assert.equal(state.rerunPreviews[0].previousRunId, state.historicalRunSnapshot.id);
  assert.equal(state.rerunPreviews[0].policyVersion, state.policyVersion);
  assert.equal(state.rerunPreviews[0].actualRunCount, 0);
  assert.notEqual(state.rerunPreviews[0].id, state.historicalRunSnapshot.id);
});

test("Local-private→Cloud-sync 이동은 5단계를 건너뛰지 못하고 실제 전송은 0건이다", async () => {
  const model = await loadModel();
  let state = model.createAccountSecurityViewState();
  const skipped = model.transitionAccountSecurityState(state, { type: "advance-realm-move", step: "explicit_approval" });
  assert.equal(skipped.safety.code, "REALM_MOVE_STEP_REQUIRED");
  for (const step of ["target_scope", "authorization_sensitive_check"]) state = model.transitionAccountSecurityState(state, { type: "advance-realm-move", step });
  state = model.transitionAccountSecurityState(state, { type: "issue-step-up", actor: state.actorId, action: "move_data_realm", target: "source-private-001", policyVersion: state.policyVersion });
  state = model.transitionAccountSecurityState(state, { type: "advance-realm-move", step: "explicit_approval" });
  for (const step of ["transfer_preview", "version_audit"]) state = model.transitionAccountSecurityState(state, { type: "advance-realm-move", step });
  assert.deepEqual(state.realmMove.completedSteps, ["target_scope", "authorization_sensitive_check", "explicit_approval", "transfer_preview", "version_audit"]);
  assert.equal(state.actualTransfers, 0);
  assert.equal(state.actualSourceVersionsCreated, 0);
  assert.equal(state.actualReindexes, 0);
});

test("Audit는 append-only이며 기존 Event 수정·삭제를 거부한다", async () => {
  const model = await loadModel();
  let state = model.createAccountSecurityViewState();
  state = model.transitionAccountSecurityState(state, { type: "issue-step-up", actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion });
  const events = state.auditEvents;
  const denied = model.transitionAccountSecurityState(state, { type: "mutate-audit", operation: "delete", eventId: events[0].id });
  assert.equal(denied.safety.code, "AUDIT_APPEND_ONLY");
  assert.deepEqual(denied.auditEvents, events);
});

test("AccountSecurity 상태는 화면 이동·네 폭 Projection 뒤 선택과 Step-up을 보존한다", async () => {
  const model = await loadModel();
  let state = model.createAccountSecurityViewState({ selectedOutputVersionId: "output-version-001" });
  state = model.transitionAccountSecurityState(state, { type: "issue-step-up", actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion });
  state = model.transitionAccountSecurityState(state, { type: "select-role", role: "reviewer" });
  state = model.transitionAccountSecurityState(state, { type: "navigate", screen: "organization" });
  state = model.transitionAccountSecurityState(state, { type: "navigate", screen: "account" });
  for (const width of [1920, 1200, 800, 500]) {
    const projection = model.projectAccountSecurity(state, width);
    assert.equal(projection.state.selectedRole, "reviewer");
    assert.equal(projection.state.selectedOutputVersionId, "output-version-001");
    assert.equal(projection.state.stepUpAuthorizations.length, 1);
  }
});

test("Organization 지원 여부는 화면 폭이 아니라 명시 client_type으로 판정한다", async () => {
  const model = await loadModel();
  for (const width of [1920, 1200, 800, 500]) {
    assert.equal(model.projectAccountSecurity(model.createAccountSecurityViewState({ clientType: "web", screen: "organization" }), width).organizationAvailability, "available");
  }
  for (const clientType of ["android", "ios"]) {
    const projection = model.projectAccountSecurity(model.createAccountSecurityViewState({ clientType, screen: "organization" }), 1920);
    assert.equal(projection.organizationAvailability, "unavailable");
    assert.match(projection.continueOn, /Web|Windows/);
  }
});

test("강제 RuleSet Binding은 조직 관리자만, 선택형은 Workspace 관리자부터 변경한다", async () => {
  const model = await loadModel();
  assert.equal(model.evaluateRuleSetBindingChange("mandatory", "workspace_admin").code, "AUTHORIZATION_DENIED");
  assert.equal(model.evaluateRuleSetBindingChange("mandatory", "organization_admin").allowed, true);
  assert.equal(model.evaluateRuleSetBindingChange("optional", "workspace_admin").allowed, true);
  assert.equal(model.evaluateRuleSetBindingChange("optional", "editor").code, "AUTHORIZATION_DENIED");
});

test("Account·Organization Route와 UI는 정본·정직성·same-origin 계약을 사용한다", async () => {
  const files = [
    "apps/web/app/settings/account/page.jsx",
    "apps/web/app/settings/organization/page.jsx",
    "packages/ui/src/account-security-model.js",
    "packages/ui/src/account-security-pane.jsx",
    "packages/ui/src/index.js"
  ];
  for (const relative of files) assert.ok(existsSync(path.join(root, relative)), `missing ${relative}`);
  const source = (await Promise.all(files.map(read))).join("\n");
  for (const token of ["account_settings", "organization_settings", "MembershipRole", "StepUpAuthorization", "STEP_UP_REQUIRED", "CURRENT_ACCESS_DENIED", "HTTP 403 계약 Preview", "실제 API 미실행", "prototype_fixture", "deferred_actual", "output-version-001"]) assert.match(source, new RegExp(token));
  for (const token of ["aria-describedby", "aria-controls", "role=\"tooltip\"", "Escape"]) assert.match(source, new RegExp(token));
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL|fetch\s*\(/i);
});

test("C01 권한 없는 Actor는 Step-up 발급·소비로 현재 권한을 대체할 수 없다", async () => {
  const model = await loadModel();
  assert.deepEqual(Object.keys(model.SENSITIVE_ACTION_REGISTRY).sort(), [...model.SENSITIVE_ACTIONS].sort());
  const deniedFixtures = [
    { selectedRole: "viewer", membership: { role: "viewer", active: true, tenantId: "tenant-organization-001", workspaceId: "workspace-release-one" } },
    { navigationPersona: "operator", selectedRole: "organization_admin", membership: null },
    { navigationPersona: "organization_member", selectedRole: "editor", membership: null },
    { navigationPersona: "personal_user", tenantKind: "organization", selectedRole: "personal_owner", membership: null },
    { selectedRole: "organization_admin", membership: { role: "organization_admin", active: true, tenantId: "tenant-other", workspaceId: "workspace-release-one" } }
  ];
  for (const fixture of deniedFixtures) {
    const state = model.createAccountSecurityViewState(fixture);
    const next = model.transitionAccountSecurityState(state, { type: "issue-step-up", actor: state.actorId, action: "move_data_realm", target: state.realmMove.sourceId, policyVersion: state.policyVersion });
    assert.match(next.safety?.code ?? "", /AUTHORIZATION_DENIED|CURRENT_ACCESS_DENIED/, JSON.stringify(fixture));
    assert.equal(next.stepUpAuthorizations.length, 0);
    assert.equal(next.domainMutationCount, 0);
    assert.equal(next.realmMove.completedSteps.length, 0);
  }
  const unknown = model.transitionAccountSecurityState(model.createAccountSecurityViewState(), { type: "issue-step-up", actor: "actor-organization-admin-001", action: "arbitrary_sensitive_action", target: "target-001", policyVersion: "organization-policy-2026.07" });
  assert.equal(unknown.safety?.code, "STEP_UP_ACTION_NOT_ALLOWED");
  assert.equal(unknown.stepUpAuthorizations.length, 0);

  let policy = model.createAccountSecurityViewState();
  policy = model.transitionAccountSecurityState(policy, { type: "issue-step-up", actor: policy.actorId, action: "change_organization_policy", target: policy.tenantId, policyVersion: policy.policyVersion });
  assert.equal(policy.stepUpAuthorizations.length, 1);
  policy = model.transitionAccountSecurityState(policy, { type: "select-role", role: "viewer" });
  policy = { ...policy, membership: { ...policy.membership, role: "viewer" } };
  const policyDenied = model.transitionAccountSecurityState(policy, { type: "perform-sensitive-action", actor: policy.actorId, action: "change_organization_policy", target: policy.tenantId, policyVersion: policy.policyVersion });
  assert.equal(policyDenied.safety?.code, "CURRENT_ACCESS_DENIED");
  assert.equal(policyDenied.stepUpAuthorizations.at(-1).status, "issued");
  assert.equal(policyDenied.domainMutationCount, policy.domainMutationCount);

  let realmViewer = model.createAccountSecurityViewState({ selectedRole: "viewer", membership: { role: "viewer", active: true, tenantId: "tenant-organization-001", workspaceId: "workspace-release-one" } });
  realmViewer = model.transitionAccountSecurityState(realmViewer, { type: "advance-realm-move", step: "target_scope" });
  const realmDenied = model.transitionAccountSecurityState(realmViewer, { type: "advance-realm-move", step: "authorization_sensitive_check" });
  assert.equal(realmDenied.safety?.code, "AUTHORIZATION_DENIED");
  assert.deepEqual(realmDenied.realmMove.completedSteps, ["target_scope"]);
});

test("C01 Step-up 발급 뒤 권한·Membership·Scope 변경은 소비와 영역 이동을 차단한다", async () => {
  const model = await loadModel();
  let state = model.createAccountSecurityViewState();
  state = model.transitionAccountSecurityState(state, { type: "issue-step-up", actor: state.actorId, action: "move_data_realm", target: state.realmMove.sourceId, policyVersion: state.policyVersion });
  assert.equal(state.stepUpAuthorizations.length, 1);
  state = model.transitionAccountSecurityState(state, { type: "revoke-permission", role: "organization_admin", permission: "data_realm_move" });
  const denied = model.transitionAccountSecurityState(state, { type: "perform-sensitive-action", actor: state.actorId, action: "move_data_realm", target: state.realmMove.sourceId, policyVersion: state.policyVersion });
  assert.equal(denied.safety?.code, "CURRENT_ACCESS_DENIED");
  assert.equal(denied.stepUpAuthorizations.at(-1).status, "issued");
  assert.equal(denied.domainMutationCount, state.domainMutationCount);

  let move = model.createAccountSecurityViewState();
  for (const step of ["target_scope", "authorization_sensitive_check"]) move = model.transitionAccountSecurityState(move, { type: "advance-realm-move", step });
  move = model.transitionAccountSecurityState(move, { type: "issue-step-up", actor: move.actorId, action: "move_data_realm", target: move.realmMove.sourceId, policyVersion: move.policyVersion });
  move = model.transitionAccountSecurityState(move, { type: "advance-realm-move", step: "explicit_approval" });
  assert.equal(move.realmMove.approvalPreview, true);
  move = model.transitionAccountSecurityState(move, { type: "revoke-permission", role: "organization_admin", permission: "data_realm_move" });
  const transfer = model.transitionAccountSecurityState(move, { type: "advance-realm-move", step: "transfer_preview" });
  assert.equal(transfer.safety?.code, "CURRENT_ACCESS_DENIED");
  assert.deepEqual(transfer.realmMove.completedSteps, move.realmMove.completedSteps);
  assert.equal(transfer.actualTransfers, 0);
  assert.equal(transfer.actualSourceVersionsCreated, 0);
  assert.equal(transfer.actualReindexes, 0);
});

test("C01 현재 화면에 따라 URL Route·Screen 정본을 동적으로 투영한다", async () => {
  const model = await loadModel();
  assert.deepEqual(model.projectAccountSecurityRoute?.("account"), { screen: "account", path: "/settings/account", routeId: "account_settings", screenId: "account_settings", title: "계정·신뢰 장치" });
  assert.deepEqual(model.projectAccountSecurityRoute?.("organization"), { screen: "organization", path: "/settings/organization", routeId: "organization_settings", screenId: "organization_settings", title: "조직 정책·권한" });
  const pane = await read("packages/ui/src/account-security-pane.jsx");
  assert.match(pane, /projectAccountSecurityRoute\(state\.screen\)/);
  assert.doesNotMatch(pane, /data-route-id=\{routeId\}/);
  assert.doesNotMatch(pane, /data-screen-id=\{screenId\}/);
});

test("C01 Browser 증거는 명명 PNG의 Pixel Dimension과 직접 표시 문자열을 선언한다", async () => {
  const evidence = JSON.parse(await read("docs/03_evidence/release_1/R1-M2-06/browser-validation.json"));
  const required = {
    "organization-policy-403-1200x900.png": ["AUTHORIZATION_DENIED", "HTTP 403 계약 Preview · 실제 API 미실행", "Policy Version"],
    "access-partial-rerun-800x900.png": ["AccessDecision 6건", "evidence-reference-002", "CURRENT_ACCESS_DENIED", "새 Run Preview", "실제 Run 0건"],
    "realm-move-state-500x900.png": ["5/5 완료", "Step-up", "Approval Preview", "실제 전송 0건", "대상 SourceVersion 0건", "재색인 0건"]
  };
  for (const [screenshot, strings] of Object.entries(required)) {
    const entry = evidence.viewports?.find((item) => item.screenshot === screenshot);
    assert.ok(entry, screenshot);
    assert.match(entry.pixel_dimensions ?? "", /^\d+x\d+$/);
    for (const value of strings) assert.ok(entry.visible_strings?.includes(value), `${screenshot}: ${value}`);
  }
});

test("C02 정책 Preview는 호출자 Role·Persona·Grant 주입으로 현재 Membership 권한을 상승시키지 않는다", async () => {
  const model = await loadModel();
  const viewer = model.createAccountSecurityViewState({
    navigationPersona: "organization_member",
    selectedRole: "viewer",
    membership: { role: "viewer", active: true, tenantId: "tenant-organization-001", workspaceId: "workspace-release-one" }
  });
  const denied = model.transitionAccountSecurityState(viewer, {
    type: "preview-policy-change",
    role: "organization_admin",
    persona: "organization_admin",
    grants: { final_approval_delivery: true },
    field: "egressPolicy",
    value: "unrestricted"
  });
  assert.match(denied.safety?.code ?? "", /AUTHORIZATION_DENIED|CURRENT_ACCESS_DENIED/);
  assert.equal(denied.policyPreview, null);
  assert.equal(denied.domainMutationCount, viewer.domainMutationCount);
  assert.equal(denied.externalCallCount, viewer.externalCallCount);
  assert.deepEqual(denied.auditEvents, viewer.auditEvents);

  const admin = model.createAccountSecurityViewState();
  const allowed = model.transitionAccountSecurityState(admin, {
    type: "preview-policy-change",
    role: "organization_admin",
    field: "egressPolicy",
    value: "approved_external_only"
  });
  assert.equal(allowed.safety, null);
  assert.equal(allowed.policyPreview?.field, "egressPolicy");
  assert.equal(allowed.policyPreview?.actualApiWrites, 0);
});
