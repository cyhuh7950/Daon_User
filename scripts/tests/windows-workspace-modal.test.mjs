import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Workspace modal fixes dialog semantics, focus trap, escape, inert and focus return", async () => {
  const modal = await readFile(new URL("../../apps/desktop/src/workspace-modal.jsx", import.meta.url), "utf8");
  assert.match(modal, /role="dialog"/u);
  assert.match(modal, /aria-modal="true"/u);
  assert.match(modal, /aria-labelledby/u);
  assert.match(modal, /event\.key === "Tab"/u);
  assert.match(modal, /event\.shiftKey/u);
  assert.match(modal, /event\.key === "Escape"/u);
  assert.match(modal, /opener.*focus/u);
  const shell = await readFile(new URL("../../apps/desktop/src/desktop-shell.jsx", import.meta.url), "utf8");
  assert.match(shell, /inert=/u);
});

test("Settings dirty close remains inside modal until Save Discard or continue", async () => {
  const settings = await readFile(new URL("../../apps/desktop/src/workspace-settings-modal.jsx", import.meta.url), "utf8");
  assert.match(settings, /Save|저장/u);
  assert.match(settings, /Discard|버리기/u);
  assert.match(settings, /Continue editing|계속 편집/u);
  assert.match(settings, /dirty/u);
  assert.match(settings, /RuleSet/u);
  assert.match(settings, /Egress/u);
  assert.match(settings, /offlineState/u);
  assert.match(settings, /onSave/u);
  assert.doesNotMatch(settings, /현재 검증 모델/u);
});

test("Operations modal never resets actual Offline state or claims unknown storage is connected", async () => {
  const shell = await readFile(new URL("../../apps/desktop/src/desktop-shell.jsx", import.meta.url), "utf8");
  const operations = await readFile(new URL("../../apps/desktop/src/workspace-operations-modal.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(shell, /offlineState=\{createOfflineStudioState\(\)\}/u);
  assert.match(operations, /unknown|unavailable/u);
  assert.doesNotMatch(operations, /Encrypted storage<\/dt><dd>◆ 연결됨/u);
});

test("Operations and Settings report only persisted or confirmed state", async () => {
  const shell = await readFile(new URL("../../apps/desktop/src/desktop-shell.jsx", import.meta.url), "utf8");
  const operations = await readFile(new URL("../../apps/desktop/src/workspace-operations-modal.jsx", import.meta.url), "utf8");
  const settings = await readFile(new URL("../../apps/desktop/src/workspace-settings-modal.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(shell, /Offline · Cloud 연결/u);
  assert.match(shell, /Cloud 인증됨/u);
  assert.match(operations, /settingsConfirmed/u);
  assert.match(operations, /readiness === "ready"/u);
  assert.doesNotMatch(operations, /selectedModelDeploymentId \? "● 준비"/u);
  assert.match(settings, /현재 고정 정책/u);
  assert.doesNotMatch(settings, /<select defaultValue="document"/u);
  assert.doesNotMatch(settings, /<select defaultValue="append"/u);
  assert.doesNotMatch(settings, /<select defaultValue="explicit"/u);
});
