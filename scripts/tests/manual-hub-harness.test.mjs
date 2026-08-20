import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("manual evidence harness는 test-only same-origin이며 제품 Shell과 정본 assets만 사용한다", async () => {
  const [entry, config, capture, webPackage] = await Promise.all([
    read("scripts/test-harness/manual-hub/main.jsx"),
    read("scripts/test-harness/manual-hub/vite.config.mjs"),
    read("scripts/capture-browser-manual-evidence.mjs"),
    read("apps/web/package.json"),
  ]);
  assert.match(entry, /ProductWorkspaceShell/u);
  assert.match(entry, /getManualManifest/u);
  assert.match(config, /allowlist/u);
  assert.match(config, /DAON_MANUAL_NETWORK_LOG/u);
  assert.match(capture, /1920/u);
  assert.match(capture, /1080/u);
  assert.doesNotMatch(entry, /localhost|127\.0\.0\.1|NEXT_PUBLIC_/u);
  assert.doesNotMatch(webPackage, /manual-hub\/vite\.config/u);
});
