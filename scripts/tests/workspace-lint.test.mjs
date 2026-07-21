import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const root = path.resolve(".");
const lintScript = path.join(root, "scripts/lint-workspace.mjs");

test("Workspace Lint는 정상 Source를 실제 Parse·정적 검사한다", () => {
  const result = spawnSync(process.execPath, [lintScript], { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /workspace lint passed/);
});

test("Workspace Lint는 비정상 Fixture를 Exit 1로 거부한다", async () => {
  const fixtureDir = await mkdtemp(path.join(tmpdir(), "daon-workspace-lint-"));
  const fixture = path.join(fixtureDir, "invalid.jsx");
  try {
    await writeFile(fixture, "export default function Broken( { debugger; fetch('http://localhost/api'); eval('1'); }", "utf8");
    const result = spawnSync(process.execPath, [lintScript, fixture], { cwd: root, encoding: "utf8" });
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stdout + result.stderr, /parse|debugger|eval|fetch|forbidden/i);
  } finally {
    await rm(fixtureDir, { recursive: true, force: true });
  }
});
