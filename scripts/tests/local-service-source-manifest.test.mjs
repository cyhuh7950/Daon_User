import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalSourceEntries,
  sourceEntriesHash
} from "../generate-r1-m3-03-source-manifest.mjs";

test("source manifest canonicalizes repository-relative entries deterministically", () => {
  const input = [
    { path: "scripts/z.mjs", bytes: 2, sha256: "B".repeat(64) },
    { path: "apps/desktop/a.rs", bytes: 1, sha256: "A".repeat(64) }
  ];
  const first = canonicalSourceEntries(input);
  const second = canonicalSourceEntries([...input].reverse());
  assert.deepEqual(first.map(({ path }) => path), [
    "apps/desktop/a.rs",
    "scripts/z.mjs"
  ]);
  assert.equal(sourceEntriesHash(first), sourceEntriesHash(second));
  assert.throws(
    () =>
      canonicalSourceEntries([
        { path: "C:/private/source.rs", bytes: 1, sha256: "A".repeat(64) }
      ]),
    /repository-relative/u
  );
});
