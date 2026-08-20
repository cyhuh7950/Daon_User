import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("license evidence harness is test-only, same-origin, and reuses the product shell", async () => {
  const [entry, config, webPackage] = await Promise.all([
    read("scripts/test-harness/license-settings/main.jsx"),
    read("scripts/test-harness/license-settings/vite.config.mjs"),
    read("apps/web/package.json"),
  ]);
  assert.match(entry, /ProductWorkspaceShell/);
  assert.match(entry, /getWorkspaceLicense/);
  assert.match(config, /\/bff\/api\/workspaces\/workspace-license-evidence\/license/);
  assert.match(config, /can_apply/);
  assert.match(config, /DAON_LICENSE_NETWORK_LOG/);
  assert.match(config, /originalUrl/);
  assert.doesNotMatch(entry, /localhost|127\.0\.0\.1|NEXT_PUBLIC_/);
  assert.doesNotMatch(webPackage, /license-settings\/vite\.config/);
});
