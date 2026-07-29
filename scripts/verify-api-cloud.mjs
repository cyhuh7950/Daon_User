#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiRoot = path.join(root, "services", "api");
const source = path.join(apiRoot, "src");
const tests = path.join(apiRoot, "tests");

function run(arguments_) {
  const result = spawnSync("uv", ["run", "--project", apiRoot, "python", ...arguments_], {
    cwd: root,
    env: {
      ...process.env,
      PYTHONPATH: [source, tests].join(path.delimiter),
      UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? path.join(tmpdir(), "daon-user-r1-m5-01-uv-cache"),
    },
    encoding: "utf8",
    stdio: "inherit",
  });
  if (result.error || result.status !== 0) {
    throw new Error(`API_CLOUD_VERIFICATION_FAILED exit=${result.status ?? "launch"}`);
  }
}

run(["-m", "compileall", "-q", source, path.join(apiRoot, "migrations")]);
run(["-m", "unittest", "discover", "-s", tests, "-p", "test_cloud_storage.py", "-v"]);
console.log(`api cloud verified: postgres_integration=${process.env.DAON_TEST_POSTGRES_DSN ? "executed" : "skipped"}`);
