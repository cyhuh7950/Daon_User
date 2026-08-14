import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const manifestPath = path.join(root, "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/manifest.json");

test("offline Studio evidence manifest hashes current deterministic artifact bytes", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  assert.equal(manifest.issue_id, "R1-M8-10-WINDOWS-OFFLINE-STUDIO-01-I001");
  assert.equal(manifest.completion_status, "INCOMPLETE");
  assert.equal(manifest.checkout.migration_revision, "0017");
  for (const artifact of manifest.artifacts) {
    assert.match(artifact.path, /^docs\/03_evidence\/release_1\/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01\/[A-Za-z0-9._-]+\.(?:json|md|png|py|sh)$/u);
    const bytes = await readFile(path.join(root, artifact.path));
    assert.equal(createHash("sha256").update(bytes).digest("hex").toUpperCase(), artifact.sha256);
  }
});
