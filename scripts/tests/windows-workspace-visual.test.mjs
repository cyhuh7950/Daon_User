import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Windows workspace exposes reusable notebook violet tokens without gradients", async () => {
  const css = await readFile(new URL("../../apps/desktop/src/workspace-visual-tokens.css", import.meta.url), "utf8");
  for (const token of ["--workspace-canvas", "--workspace-surface", "--workspace-border", "--workspace-text", "--workspace-accent", "--workspace-radius", "--workspace-shadow"]) {
    assert.match(css, new RegExp(token));
  }
  assert.match(css, /notebook-violet/u);
  assert.match(css, /prefers-color-scheme:\s*dark/u);
  assert.match(css, /prefers-reduced-motion:\s*reduce/u);
  assert.doesNotMatch(css, /gradient\s*\(/iu);
});

test("Desktop App Bar contains only compact state and the two popup actions", async () => {
  const shell = await readFile(new URL("../../apps/desktop/src/desktop-shell.jsx", import.meta.url), "utf8");
  assert.match(shell, /data-visual-system="notebook-violet"/u);
  assert.match(shell, />운영상태</u);
  assert.match(shell, />설정</u);
  assert.match(shell, /Offline|Cloud 연결/u);
});
