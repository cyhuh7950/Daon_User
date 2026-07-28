#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiRoot = path.join(root, "services/api");
const sourceRoot = path.join(apiRoot, "src");
const testRoot = path.join(apiRoot, "tests");
const evidencePath = path.join(root, "docs/03_evidence/release_1/R1-M4-02/audit-core-summary.json");

function fail(message) {
  throw new Error(`API_AUDIT_VERIFICATION_FAILED ${message}`);
}

function runPython(arguments_, { capture = false } = {}) {
  const result = spawnSync(
    "uv",
    ["run", "--project", apiRoot, "python", ...arguments_],
    {
      cwd: root,
      encoding: "utf8",
      env: { ...process.env, PYTHONPATH: sourceRoot },
      stdio: capture ? "pipe" : "inherit"
    }
  );
  if (result.error) fail(`python launch ${result.error.code ?? result.error.name}`);
  if (result.status !== 0) {
    if (capture) {
      if (result.stdout) process.stdout.write(result.stdout);
      if (result.stderr) process.stderr.write(result.stderr);
    }
    fail(`python exit ${result.status}`);
  }
  return result;
}

function canonicalJson(value) {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (value === null || typeof value !== "object") return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalJson(value[key])]));
}

async function main() {
  const args = process.argv.slice(2).filter((argument) => argument !== "--");
  const write = args.includes("--write");
  if (write && args.includes("--no-write")) fail("--write and --no-write are mutually exclusive");

  const tests = runPython(
    ["-m", "unittest", "discover", "-s", testRoot, "-p", "test_audit*.py", "-v"],
    { capture: true }
  );
  if (tests.stdout) process.stdout.write(tests.stdout);
  if (tests.stderr) process.stderr.write(tests.stderr);
  const testOutput = `${tests.stdout ?? ""}\n${tests.stderr ?? ""}`;
  const countMatch = testOutput.match(/Ran (\d+) tests?/);
  if (!countMatch) fail("test count missing");

  const summaryResult = runPython(["-m", "daon_user_api.audit", "--summary-json"], { capture: true });
  let contract;
  try {
    contract = JSON.parse(summaryResult.stdout.trim());
  } catch {
    fail("summary JSON invalid");
  }
  const canonicalContract = JSON.stringify(canonicalJson(contract));
  const source = (await readFile(path.join(sourceRoot, "daon_user_api/audit.py"), "utf8")).replaceAll("\r\n", "\n");
  const summary = {
    ...contract,
    contract_sha256: createHash("sha256").update(canonicalContract).digest("hex").toUpperCase(),
    source_sha256: createHash("sha256").update(source).digest("hex").toUpperCase(),
    test_count: Number(countMatch[1])
  };
  const expected = `${JSON.stringify(summary, null, 2)}\n`;
  if (write) {
    await mkdir(path.dirname(evidencePath), { recursive: true });
    await writeFile(evidencePath, expected, "utf8");
  } else {
    const actual = await readFile(evidencePath, "utf8");
    if (actual !== expected) fail("deterministic evidence mismatch; run with --write");
  }
  console.log(`api audit verified: tests=${summary.test_count} fields=${summary.event_fields.length} integrity_codes=${summary.integrity_codes.length} sha256=${summary.contract_sha256}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
