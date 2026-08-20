import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("screen preference actual harness is test-only and reuses production boundaries without auth impersonation", async () => {
  const [entry, html, webPackage] = await Promise.all([
    read("scripts/test-harness/screen-preferences/main.jsx"),
    read("scripts/test-harness/screen-preferences/index.html"),
    read("apps/web/package.json"),
  ]);
  assert.match(entry, /apps\/web\/components\/screen-preferences-pane/);
  assert.match(entry, /apps\/web\/lib\/screen-preference-api/);
  assert.match(entry, /Test Notebook/);
  assert.match(entry, /fixtureHash/);
  assert.match(entry, /matchMedia/);
  assert.match(entry, /운영체제 밝게/);
  assert.doesNotMatch(entry, /auth(?:orization)?|password|session|bearer|access[_-]?token/i);
  assert.match(html, /main\.jsx/);
  assert.doesNotMatch(webPackage, /test-harness/);
});

test("desktop screen preference evidence harness uses the real Tauri bridge and stays outside the product entry", async () => {
  const [entry, html, config, desktopMain, desktopPackage] = await Promise.all([
    read("scripts/test-harness/desktop-screen-preferences/main.jsx"),
    read("scripts/test-harness/desktop-screen-preferences/index.html"),
    read("scripts/test-harness/desktop-screen-preferences/vite.config.mjs"),
    read("apps/desktop/src/main.jsx"),
    read("apps/desktop/package.json"),
  ]);
  assert.match(entry, /apps\/desktop\/src\/workspace-settings-modal/);
  assert.match(entry, /window\.__TAURI_INTERNALS__\?\.invoke/);
  assert.match(entry, /Test Notebook/);
  assert.match(entry, /fixtureHash/);
  assert.match(entry, /prefers-color-scheme/);
  assert.match(entry, /devicePixelRatio/);
  assert.match(entry, /visualScale/);
  assert.match(entry, /event\.ctrlKey/);
  assert.doesNotMatch(entry, /auth(?:orization)?|password|session|bearer|access[_-]?token/i);
  assert.match(html, /main\.jsx/);
  assert.match(config, /DAON_SCREEN_EVIDENCE_DIST/);
  assert.doesNotMatch(desktopMain, /desktop-screen-preferences/);
  assert.doesNotMatch(desktopPackage, /desktop-screen-preferences/);
});
