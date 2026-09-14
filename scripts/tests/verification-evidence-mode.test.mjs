import assert from "node:assert/strict";
import test from "node:test";

import { resolveEvidenceMode } from "../lib/verification-evidence-mode.mjs";

test("검증 evidence 모드는 compare가 기본이고 write와 check-only를 명시적으로 분리한다", () => {
  assert.equal(resolveEvidenceMode([]), "compare");
  assert.equal(resolveEvidenceMode(["--no-write"]), "compare");
  assert.equal(resolveEvidenceMode(["--write"]), "write");
  assert.equal(resolveEvidenceMode(["--check-only"]), "check-only");
});

test("서로 다른 evidence 모드를 동시에 요청하면 거부한다", () => {
  assert.throws(() => resolveEvidenceMode(["--write", "--check-only"]), /mutually exclusive/u);
  assert.throws(() => resolveEvidenceMode(["--no-write", "--check-only"]), /mutually exclusive/u);
  assert.throws(() => resolveEvidenceMode(["--write", "--no-write"]), /mutually exclusive/u);
});
