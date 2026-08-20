import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("screen preference client keeps exact theme values, applies system changes, and uses only same-origin BFF", async () => {
  const theme = await import("../../apps/web/lib/screen-theme.js");
  const api = await import("../../apps/web/lib/screen-preference-api.js");
  assert.deepEqual(theme.SCREEN_THEMES, ["system", "light", "dark"]);
  assert.equal(theme.resolveScreenTheme("system", true), "dark");
  assert.equal(theme.resolveScreenTheme("system", false), "light");
  assert.equal(theme.resolveScreenTheme("light", true), "light");
  assert.equal(theme.resolveScreenTheme("dark", false), "dark");
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, options) => {
    requests.push({ path, options });
    return Response.json({ data: { theme: "dark" }, meta: { trace_id: "trace-screen-preference" } });
  };
  try {
    assert.deepEqual(await api.getScreenPreferences(), { theme: "dark" });
    assert.deepEqual(await api.saveScreenPreferences("dark"), { theme: "dark" });
    assert.deepEqual(await api.resetScreenPreferences(), { theme: "dark" });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.deepEqual(requests.map(({ path, options }) => ({ path, method: options.method, credentials: options.credentials })), [
    { path: "/bff/api/preferences/screen", method: "GET", credentials: "same-origin" },
    { path: "/bff/api/preferences/screen", method: "PUT", credentials: "same-origin" },
    { path: "/bff/api/preferences/screen", method: "PUT", credentials: "same-origin" },
  ]);
  assert.equal(requests[2].options.body, JSON.stringify({ theme: "system" }));
});

test("screen settings page has early paint, accessible three-way controls and screen-only reset", async () => {
  const [layout, pane, page, css, runtime] = await Promise.all([
    read("apps/web/app/layout.jsx"),
    read("apps/web/components/screen-preferences-pane.jsx"),
    read("apps/web/app/settings/screen/page.jsx"),
    read("apps/web/app/globals.css"),
    read("apps/web/components/screen-theme-runtime.jsx"),
  ]);
  assert.match(layout, /data-theme/);
  assert.match(layout, /prefers-color-scheme/);
  assert.match(pane, /화면 설정/);
  assert.match(pane, /system/);
  assert.match(pane, /light/);
  assert.match(pane, /dark/);
  assert.match(pane, /화면 설정 초기화/);
  assert.match(pane, /aria-live/);
  assert.match(page, /ScreenPreferencesPane/);
  assert.match(layout, /ScreenThemeRuntime/);
  assert.match(runtime, /getScreenPreferences/);
  assert.match(runtime, /watchSystemScreenTheme/);
  assert.match(css, /\[data-theme="dark"\]/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /prefers-reduced-motion/);
});
