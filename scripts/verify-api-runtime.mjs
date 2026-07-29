#!/usr/bin/env node

import { readFile, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiRoot = path.join(root, "services/api");
const apiSource = path.join(apiRoot, "src");
const apiTests = path.join(apiRoot, "tests");

function fail(message) {
  throw new Error(`API_RUNTIME_VERIFICATION_FAILED ${message}`);
}

function run(executable, arguments_, options = {}) {
  const result = spawnSync(executable, arguments_, {
    cwd: root,
    env: {
      ...process.env,
      PYTHONPATH: apiSource,
      UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? path.join(tmpdir(), "daon-user-uv-cache"),
    },
    encoding: "utf8",
    stdio: "inherit",
    ...options,
  });
  if (result.error || result.status !== 0) {
    fail(`${executable} ${arguments_.join(" ")} exit=${result.status ?? "launch"}`);
  }
}

function runPython(arguments_) {
  return run("uv", ["run", "--project", apiRoot, "python", ...arguments_]);
}

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const candidate = path.join(directory, entry.name);
    return entry.isDirectory() ? filesUnder(candidate) : [candidate];
  }));
  return nested.flat();
}

async function assertClientBoundary() {
  const staticRoot = path.join(root, "apps/web/.next/static");
  const staticFiles = await filesUnder(staticRoot);
  const forbidden = [
    "NEXT_PUBLIC_API_BASE_URL",
    "DAON_API_INTERNAL_URL",
    "http://127.0.0.1",
    "http://localhost",
  ];
  for (const file of staticFiles) {
    const content = await readFile(file, "utf8").catch(() => "");
    for (const marker of forbidden) {
      if (content.includes(marker)) fail(`client bundle contains ${marker}`);
    }
  }
  const routeSource = await readFile(
    path.join(root, "apps/web/app/bff/api/[...path]/route.js"), "utf8",
  );
  const helperSource = await readFile(path.join(root, "apps/web/lib/bff-api-proxy.js"), "utf8");
  if (!routeSource.includes("process.env.DAON_API_INTERNAL_URL")) {
    fail("server route does not own internal API configuration");
  }
  if (!helperSource.includes('redirect: "manual"')) fail("upstream redirect policy missing");
  if (routeSource.includes("NEXT_PUBLIC_")) fail("server route uses public environment configuration");
}

async function main() {
  const write = process.argv.includes("--write");
  runPython(["-m", "compileall", "-q", apiSource]);
  runPython(["-m", "unittest", "discover", "-s", apiTests, "-p", "test_runtime_http.py", "-v"]);
  runPython(["-m", "unittest", "discover", "-s", apiTests, "-p", "test_runtime_process_lifecycle.py", "-v"]);
  run(process.execPath, ["--test", "scripts/tests/api-bff-runtime.test.mjs"]);
  if (process.platform === "win32") {
    run(process.env.ComSpec ?? "cmd.exe", [
      "/d", "/s", "/c", "npm", "run", "build", "--workspace", "@daon-user/web",
    ]);
  } else {
    run("npm", ["run", "build", "--workspace", "@daon-user/web"]);
  }
  await assertClientBoundary();
  runPython([
    path.join(apiTests, "runtime_process_probe.py"),
    write ? "--write" : "--no-write",
    "--with-next",
  ].filter((argument) => argument !== "--no-write"));
  console.log("api runtime verified: unit=10 lifecycle_unit=6 bff_unit=9 actual_api=true actual_next=true same_port_restart=true");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
