#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";

const root = path.resolve(import.meta.dirname, "..");
const environment = {
  ...process.env,
  UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? path.join(tmpdir(), "daon-user-uv-cache"),
};

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: root,
    env: environment,
    encoding: "utf8",
    stdio: "inherit",
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`NOTIFICATION_VERIFICATION_FAILED ${command} exit=${result.status ?? "launch"}`);
  }
}

try {
  run("uv", [
    "run", "--project", "services/api", "python", "-m", "unittest", "discover",
    "-s", "services/api/tests", "-p", "test_notification.py", "-v",
  ]);
  run(process.execPath, [
    "--test",
    "scripts/tests/api-bff-runtime.test.mjs",
    "scripts/tests/openapi-contract.test.mjs",
    "scripts/tests/notification-inbox-ui.test.mjs",
  ]);
  console.log("notification api verified: python=10 node=21");
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}
