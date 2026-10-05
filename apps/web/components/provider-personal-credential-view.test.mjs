import test from "node:test";
import assert from "node:assert/strict";

import { projectPersonalCredentialView } from "./provider-personal-credential-view.js";

const personalConnection = { access_mode: "personal", enabled: true, routeReady: true };

test("legacy personal v1 view explicitly requests Key re-entry", () => {
  const result = projectPersonalCredentialView(personalConnection, {
    configured: false,
    verification_status: "unverified",
  });
  assert.equal(result.needsReentry, true);
  assert.match(result.label, /Key 재입력 필요/u);
  assert.equal(result.verified, false);
});

test("missing personal credential remains distinct from legacy re-entry", () => {
  const result = projectPersonalCredentialView(personalConnection);
  assert.equal(result.needsReentry, undefined);
  assert.match(result.label, /개인 Key 필요/u);
  assert.doesNotMatch(result.label, /재입력/u);
});

test("verified personal v2 keeps the existing usable display path", () => {
  const personal = projectPersonalCredentialView(personalConnection, {
    configured: true,
    verification_status: "verified",
  });
  assert.equal(personal.verified, true);
  assert.equal(personal.needsReentry, undefined);
});
