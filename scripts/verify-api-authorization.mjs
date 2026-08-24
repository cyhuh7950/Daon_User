#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiRoot = path.join(root, "services/api");
const sourceRoot = path.join(apiRoot, "src");
const testRoot = path.join(apiRoot, "tests");
const sourcePath = path.join(sourceRoot, "daon_user_api/authorization.py");
const evidencePath = path.join(
  root, "docs/03_evidence/release_1/R1-M4-04/authorization-core-summary.json"
);

function fail(message) {
  throw new Error(`API_AUTHORIZATION_VERIFICATION_FAILED ${message}`);
}

function runPython(arguments_, { capture = false } = {}) {
  const projectPython = path.join(
    root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python"
  );
  const projectPythonAvailable = existsSync(projectPython)
    && spawnSync(projectPython, ["--version"], { cwd: root, stdio: "ignore" }).status === 0;
  const executable = projectPythonAvailable ? projectPython : "uv";
  const launcherArguments = projectPythonAvailable
    ? arguments_
    : ["run", "--project", apiRoot, "python", ...arguments_];
  const result = spawnSync(executable, launcherArguments, {
    cwd: root,
    encoding: "utf8",
    env: {
      ...process.env,
      PYTHONPATH: sourceRoot,
      UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? path.join(tmpdir(), "daon-user-uv-cache"),
      UV_PYTHON_INSTALL_DIR: process.env.UV_PYTHON_INSTALL_DIR ?? path.join(tmpdir(), "daon-user-uv-python"),
      UV_PROJECT_ENVIRONMENT: process.env.UV_PROJECT_ENVIRONMENT ?? path.join(tmpdir(), "daon-user-api-uv-env")
    },
    stdio: capture ? "pipe" : "inherit"
  });
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

async function main() {
  const args = process.argv.slice(2).filter((argument) => argument !== "--");
  const write = args.includes("--write");
  if (write && args.includes("--no-write")) fail("--write and --no-write are mutually exclusive");

  runPython(["-m", "compileall", "-q", sourceRoot]);
  const identity = spawnSync(
    process.execPath,
    [path.join(root, "scripts/verify-api-identity.mjs"), "--no-write"],
    {
      cwd: root,
      encoding: "utf8",
      env: {
        ...process.env,
        UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? path.join(tmpdir(), "daon-user-uv-cache"),
        UV_PYTHON_INSTALL_DIR: process.env.UV_PYTHON_INSTALL_DIR ?? path.join(tmpdir(), "daon-user-uv-python"),
        UV_PROJECT_ENVIRONMENT: process.env.UV_PROJECT_ENVIRONMENT ?? path.join(tmpdir(), "daon-user-api-uv-env")
      },
      stdio: "inherit"
    }
  );
  if (identity.error || identity.status !== 0) fail("identity regression verification failed");
  const tests = runPython(
    ["-m", "unittest", "discover", "-s", testRoot, "-p", "test_authorization*.py", "-v"],
    { capture: true }
  );
  if (tests.stdout) process.stdout.write(tests.stdout);
  if (tests.stderr) process.stderr.write(tests.stderr);
  const countMatch = `${tests.stdout ?? ""}\n${tests.stderr ?? ""}`.match(/Ran (\d+) tests?/);
  if (!countMatch) fail("test count missing");

  const contractResult = runPython(
    [
      "-c",
      "import json; from daon_user_api.authorization import authorization_contract_summary; print(json.dumps(authorization_contract_summary(), sort_keys=True))"
    ],
    { capture: true }
  );
  let contract;
  try {
    contract = JSON.parse(contractResult.stdout.trim());
  } catch {
    fail("contract summary JSON invalid");
  }
  const source = (await readFile(sourcePath, "utf8")).replaceAll("\r\n", "\n");
  const summary = {
    ...contract,
    source_sha256: createHash("sha256").update(source).digest("hex").toUpperCase(),
    test_count: Number(countMatch[1])
  };
  const expected = `${JSON.stringify(summary, null, 2)}\n`;
  if (write) {
    await mkdir(path.dirname(evidencePath), { recursive: true });
    await writeFile(evidencePath, expected, "utf8");
  } else {
    const actual = (await readFile(evidencePath, "utf8")).replaceAll("\r\n", "\n");
    if (actual !== expected) fail("deterministic evidence mismatch; run with --write");
  }
  console.log(
    `api authorization verified: tests=${summary.test_count} roles=${summary.roles.length} permissions=${summary.permissions.length} sha256=${summary.source_sha256}`
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
