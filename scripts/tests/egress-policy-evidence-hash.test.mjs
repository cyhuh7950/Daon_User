import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const MANIFEST = path.join(
  ROOT,
  "docs/03_evidence/release_1/R1-M8-09-EGRESS-POLICY-C01/manifest.json",
);

test("egress policy evidence manifest hashes every declared artifact", async () => {
  const manifest = JSON.parse(await readFile(MANIFEST, "utf8"));
  assert.ok(manifest.artifacts.length >= 18);
  for (const artifact of manifest.artifacts) {
    const bytes = await readFile(path.join(ROOT, artifact.path));
    const actual = createHash("sha256").update(bytes).digest("hex").toUpperCase();
    assert.equal(actual, artifact.sha256, artifact.path);
  }
});
