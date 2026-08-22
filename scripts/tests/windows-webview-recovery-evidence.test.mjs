import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = path.resolve("docs/03_evidence/release_1/R1-M8-10-WINDOWS-WEBVIEW-RECOVERY-I001");

test("Windows WebView recovery manifest는 PARTIAL 판정과 current artifact bytes를 결속한다", async () => {
  const manifest = JSON.parse(await readFile(path.join(root, "manifest.json"), "utf8"));
  assert.equal(manifest.issue_id, "R1-M8-10-WINDOWS-WEBVIEW-RECOVERY-I001");
  assert.equal(manifest.status, "PARTIAL");
  assert.equal(manifest.windows_actual_status, "BLOCKED");
  assert.equal(manifest.root_cause, "SANDBOX_GUI_BOUNDARY_LIKELY_MINIMAL_UNSANDBOXED_PASS");
  assert.equal(manifest.minimal_webview_actual, "RECORDED_PASS_PROVENANCE_LIMITED");
  assert.equal(manifest.h2_h3_provenance, "NOT_DURABLY_CAPTURED");
  assert.equal(manifest.product_actual, "HOME_SCREENSHOT_PASS_FLOW_OBSERVATION_UNCORROBORATED");
  assert.equal(manifest.artifacts.length, 8);
  for (const artifact of manifest.artifacts) {
    const bytes = await readFile(path.join(root, artifact.path));
    assert.equal(bytes.length, artifact.bytes, artifact.path);
    assert.equal(createHash("sha256").update(bytes).digest("hex"), artifact.sha256, artifact.path);
  }
  const screenshot = manifest.artifacts.find((artifact) => artifact.path.endsWith(".png"));
  assert.deepEqual({ width: screenshot.width, height: screenshot.height, mime: screenshot.mime }, { width: 1920, height: 1080, mime: "image/png" });
});
