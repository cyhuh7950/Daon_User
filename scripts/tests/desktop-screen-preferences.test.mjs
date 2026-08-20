import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("Windows screen preference uses a dedicated encrypted Credential target and never Native Session target", async () => {
  const [bridge, commands, credentials, shell, modal, tokens] = await Promise.all([
    read("apps/desktop/src/screen-preferences-bridge.js"),
    read("apps/desktop/src-tauri/src/screen_preferences.rs"),
    read("apps/desktop/src-tauri/src/windows_credential.rs"),
    read("apps/desktop/src/desktop-shell.jsx"),
    read("apps/desktop/src/workspace-settings-modal.jsx"),
    read("apps/desktop/src/workspace-visual-tokens.css"),
  ]);
  assert.match(bridge, /screen_preferences_get/);
  assert.match(bridge, /screen_preferences_save/);
  assert.match(bridge, /screen_preferences_reset/);
  assert.doesNotMatch(bridge, /https?:\/\/|localhost|127\.0\.0\.1|\/api\/v1\//);
  assert.match(commands, /DaonUser\/ScreenPreferences\/v1/);
  assert.match(commands, /read_screen_preference/);
  assert.match(commands, /write_screen_preference/);
  assert.match(commands, /revoke_screen_preference/);
  assert.doesNotMatch(commands, /DaonUser\/NativeSession\/v1/);
  assert.match(credentials, /CRED_PERSIST_LOCAL_MACHINE/);
  assert.match(shell, /prefers-color-scheme/);
  assert.match(shell, /data-theme/);
  assert.match(shell, /createScreenPreferencesBridge/);
  assert.match(shell, /screenPreferenceReady/);
  assert.match(shell, /screenPreferences\.get/);
  assert.match(modal, /화면 설정/);
  assert.match(modal, /resetScreenTheme/);
  assert.match(modal, /화면 설정 초기화/);
  assert.match(tokens, /\[data-theme="dark"\]/);
});
