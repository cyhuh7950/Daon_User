import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const manifestPath = path.join(root, "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/manifest.json");

function jpegDimensions(bytes) {
  assert.deepEqual([...bytes.subarray(0, 2)], [0xff, 0xd8]);
  for (let offset = 2; offset + 8 < bytes.length;) {
    if (bytes[offset] !== 0xff) { offset += 1; continue; }
    const marker = bytes[offset + 1];
    offset += 2;
    if (marker === 0xd8 || marker === 0xd9) continue;
    const length = bytes.readUInt16BE(offset);
    if (new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]).has(marker)) {
      return [bytes.readUInt16BE(offset + 5), bytes.readUInt16BE(offset + 3)];
    }
    offset += length;
  }
  throw new Error("JPEG_DIMENSIONS_NOT_FOUND");
}

test("offline Studio evidence manifest hashes current deterministic artifact bytes", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  assert.equal(manifest.issue_id, "R1-M8-10-WINDOWS-OFFLINE-STUDIO-01-I001");
  assert.equal(manifest.completion_status, "INCOMPLETE");
  assert.equal(manifest.checkout.migration_revision, "0020");
  assert.equal(manifest.phase_c_menu_2_status, "PASS");
  assert.equal(manifest.phase_d_notebook_home_status, "PASS");
  assert.equal(manifest.phase_d_screenshot_gate, "SCREENSHOT_EXACT_1920X1080_JPEG");
  assert.equal(manifest.phase_e_review1_status, "PASS");
  assert.equal(manifest.phase_e_review2_status, "CODE_AND_CONTRACT_PASS");
  assert.equal(manifest.phase_e_review3_status, "CODE_REVIEW_PENDING");
  assert.equal(manifest.phase_e_review4_status, "CODE_REVIEW_PENDING");
  assert.equal(manifest.phase_e_windows_actual_status, "BLOCKED");
  for (const artifact of manifest.artifacts) {
    assert.match(artifact.path, /^docs\/03_evidence\/release_1\/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01\/[A-Za-z0-9._-]+\.(?:json|jsonl|md|png|jpg|py|sh)$/u);
    const bytes = await readFile(path.join(root, artifact.path));
    assert.equal(createHash("sha256").update(bytes).digest("hex").toUpperCase(), artifact.sha256);
  }
  const phaseDScreenshotArtifact = manifest.artifacts.find(
    (artifact) => artifact.path.endsWith("/phase-d-notebook-home-dark-1920x1080.jpg"),
  );
  assert.ok(phaseDScreenshotArtifact);
  assert.equal(phaseDScreenshotArtifact.media_type, "image/jpeg");
  assert.equal(phaseDScreenshotArtifact.bytes, 54_401);
  assert.equal(phaseDScreenshotArtifact.width, 1920);
  assert.equal(phaseDScreenshotArtifact.height, 1080);
  assert.equal(phaseDScreenshotArtifact.capture, "original_no_postprocessing");
  const phaseDScreenshot = await readFile(path.join(root, phaseDScreenshotArtifact.path));
  assert.equal(phaseDScreenshot.length, phaseDScreenshotArtifact.bytes);
  assert.deepEqual(jpegDimensions(phaseDScreenshot), [1920, 1080]);

  const contextProjectionArtifact = manifest.artifacts.find(
    (artifact) => artifact.path.endsWith("/phase-d-notebook-context-actual.json"),
  );
  assert.ok(contextProjectionArtifact);
  const contextProjection = JSON.parse(await readFile(path.join(root, contextProjectionArtifact.path), "utf8"));
  assert.deepEqual(contextProjection.sources, [{ source_id: "source-ctx-1", source_version_id: "source-version-ctx-1" }]);
  assert.deepEqual(contextProjection.conversation_thread_ids, ["conversation-ctx-1"]);
  assert.deepEqual(contextProjection.studio_output_ids, ["studio-output-ctx-1"]);
  assert.deepEqual(contextProjection.output_version_ids, ["output-version-ctx-1"]);

  for (const name of ["existing", "empty"]) {
    const artifact = manifest.artifacts.find(
      (candidate) => candidate.path.endsWith(`/phase-d-notebook-context-${name}-1920x1080.jpg`),
    );
    assert.ok(artifact);
    assert.equal(artifact.media_type, "image/jpeg");
    assert.equal(artifact.width, 1920);
    assert.equal(artifact.height, 1080);
    assert.equal(artifact.capture, "original_no_postprocessing");
    const screenshot = await readFile(path.join(root, artifact.path));
    assert.equal(screenshot.length, artifact.bytes);
    assert.deepEqual(jpegDimensions(screenshot), [1920, 1080]);
  }

  const reviewPgArtifact = manifest.artifacts.find(
    (artifact) => artifact.path.endsWith("/phase-e-review1-postgres-transcript.md"),
  );
  assert.ok(reviewPgArtifact);
  const reviewPg = await readFile(path.join(root, reviewPgArtifact.path), "utf8");
  assert.match(reviewPg, /수집: 18 items, skipped 0, passed 18/u);
  assert.match(reviewPg, /PHASE_E_GATE_CLEANUP db=0 role=0/u);
  assert.doesNotMatch(reviewPg, /postgresql:\/\/|password=|raw SQLSTATE [0-9]/iu);
});
