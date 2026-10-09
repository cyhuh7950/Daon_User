import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const privateEvidence = [
  "docs/03_evidence/release_1/R1-M4-03-C01/identity-core-summary.json",
  "docs/03_evidence/release_1/R1-M4-04/authorization-core-summary.json",
];

function snapshotEvidence(relative) {
  const absolute = path.join(root, relative);
  if (!existsSync(absolute)) return { exists: false };
  const metadata = statSync(absolute);
  return {
    exists: true,
    bytes: readFileSync(absolute).toString("base64"),
    size: metadata.size,
    mtimeMs: metadata.mtimeMs,
  };
}

test("authorization check-only는 실제 identity·authorization을 검증하고 private evidence를 변경하지 않는다", () => {
  const before = Object.fromEntries(privateEvidence.map((relative) => [relative, snapshotEvidence(relative)]));
  const result = spawnSync(process.execPath, ["scripts/verify-api-authorization.mjs", "--check-only"], {
    cwd: root,
    encoding: "utf8",
    env: process.env,
    maxBuffer: 16 * 1024 * 1024,
  });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const identity = result.stdout.match(/api identity verified: tests=(\d+) actions=7 sha256=[A-F0-9]{64}/u);
  const authorization = result.stdout.match(/api authorization verified: tests=(\d+) roles=7 permissions=8 sha256=[A-F0-9]{64}/u);
  assert.ok(identity, result.stdout);
  assert.ok(authorization, result.stdout);
  assert.ok(Number(identity[1]) >= 34, "Identity 회귀 시험 수가 승인 기준보다 적습니다.");
  assert.ok(Number(authorization[1]) >= 25, "Authorization 회귀 시험 수가 승인 기준보다 적습니다.");
  for (const relative of privateEvidence) {
    assert.deepEqual(snapshotEvidence(relative), before[relative], `${relative} was changed`);
  }
});

test("quality gate는 공개 checkout에서 API verifier를 check-only로 실행한다", () => {
  const policy = JSON.parse(readFileSync(path.join(root, "quality-gate-policy.json"), "utf8"));
  const api = policy.components.find((component) => component.id === "services/api");
  assert.deepEqual(api.capabilities.lint.command.command, ["node", "scripts/verify-api-authorization.mjs", "--check-only"]);
  assert.deepEqual(api.capabilities.type.command.command, ["node", "scripts/verify-api-authorization.mjs", "--check-only"]);
  assert.deepEqual(api.capabilities.unit.command.command, ["node", "scripts/verify-api-authorization.mjs", "--check-only"]);
  assert.deepEqual(api.capabilities.build.command.command, ["npm", "run", "verify:api-runtime", "--", "--check-only"]);
});
