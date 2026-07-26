import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalSourceEntries,
  readSourceBytes,
  snapshotMetadata,
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

test("source manifest records an exact implementation commit against its approved base", () => {
  assert.deepEqual(
    snapshotMetadata({ exactBase: "A".repeat(40), head: "B".repeat(40) }),
    {
      status: "EXACT_IMPLEMENTATION_COMMIT",
      dirty_snapshot: false,
      base_head: "B".repeat(40),
      comparison_base: "A".repeat(40),
      git_sha_role: "exact implementation commit"
    }
  );
});

test("exact source entries hash commit blobs rather than checkout-normalized bytes", async () => {
  const bytes = await readSourceBytes({
    exactBase: "A".repeat(40),
    head: "B".repeat(40),
    relativePath: "apps/desktop/source.rs",
    readWorkingFile: async () => Buffer.from("working-crlf\r\n"),
    readCommitBlob: () => Buffer.from("commit-lf\n")
  });
  assert.equal(bytes.toString("utf8"), "commit-lf\n");
});
