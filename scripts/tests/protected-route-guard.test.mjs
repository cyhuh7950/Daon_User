import assert from "node:assert/strict";
import test from "node:test";

import { concealProtectedRoute, revealProtectedRoute } from "../../apps/web/lib/protected-route-guard.js";

function rootFixture() {
  const attributes = new Map();
  return {
    hidden: false, inert: false,
    setAttribute(name, value) { attributes.set(name, value); },
    removeAttribute(name) { attributes.delete(name); },
    getAttribute(name) { return attributes.get(name) ?? null; },
  };
}

test("BFCache 보호막은 page lifecycle 같은 tick에서 DOM·a11y·interaction을 모두 숨긴다", () => {
  const root = rootFixture();
  concealProtectedRoute(root);
  assert.equal(root.hidden, true);
  assert.equal(root.inert, true);
  assert.equal(root.getAttribute("aria-hidden"), "true");
  assert.equal(root.getAttribute("data-session-validated"), "false");
  revealProtectedRoute(root);
  assert.equal(root.hidden, false);
  assert.equal(root.inert, false);
  assert.equal(root.getAttribute("aria-hidden"), null);
  assert.equal(root.getAttribute("data-session-validated"), "true");
});
