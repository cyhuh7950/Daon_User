import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, createHmac, randomBytes } from "node:crypto";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";

const binary = path.resolve(
  "apps/desktop/src-tauri/binaries/daon-user-local-service-x86_64-pc-windows-msvc.exe",
);
const workspaceId = "77777777-7777-4777-8777-777777777777";

function hmac(secretHex, value) {
  return createHmac("sha256", Buffer.from(secretHex, "hex")).update(value).digest("hex");
}

function authorization(rootSecret, instanceId, capability, command, workspace) {
  const issuedAt = Math.floor(Date.now() / 1000);
  const unsigned = [
    "lt1", issuedAt, issuedAt + 60, instanceId, capability, command,
    randomBytes(32).toString("hex"),
  ].join("|");
  const token = `${unsigned}|${hmac(rootSecret, unsigned)}`;
  return {
    authorization: `Bearer ${token}`,
    "x-daon-workspace-id": workspace,
    "x-daon-workspace-proof": hmac(rootSecret, `${token}|${workspace}`),
  };
}

function requestJson(port, method, route, headers, body = null) {
  const encoded = body === null ? Buffer.alloc(0) : Buffer.from(JSON.stringify(body));
  return new Promise((resolve, reject) => {
    const request = http.request({
      host: "127.0.0.1",
      port,
      method,
      path: route,
      headers: {
        ...headers,
        ...(body === null ? {} : { "content-type": "application/json" }),
        "content-length": String(encoded.length),
      },
      timeout: 10_000,
    }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        try {
          resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(chunks)) });
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("timeout", () => request.destroy(new Error("sidecar request timeout")));
    request.on("error", reject);
    request.end(encoded);
  });
}

async function startSidecar(storageRoot, storageKey) {
  const rootSecret = randomBytes(32).toString("hex");
  const instanceId = `raw-source-probe-${randomBytes(8).toString("hex")}`;
  const child = spawn(binary, [], { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr = `${stderr}${chunk.toString("utf8")}`.slice(-2048);
  });
  child.stdin.write(`${JSON.stringify({
    protocol_version: "1.1",
    app_instance_id: instanceId,
    root_secret: rootSecret,
    storage_root_key: storageKey,
    storage_root: storageRoot,
    parent_process_id: process.pid,
  })}\n`);
  const ready = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error("sidecar ready timeout")), 15_000);
    child.stdout.on("data", (chunk) => {
      output += chunk.toString("utf8");
      const newline = output.indexOf("\n");
      if (newline < 0) return;
      clearTimeout(timer);
      try { resolve(JSON.parse(output.slice(0, newline))); } catch (error) { reject(error); }
    });
    child.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`sidecar exited before ready:${code}`));
    });
  });
  assert.equal(ready.event, "ready");
  assert.equal(ready.app_instance_id, instanceId);
  return { child, instanceId, port: ready.port, rootSecret, stderr: () => stderr };
}

async function stopSidecar(child) {
  if (child.exitCode !== null) return;
  child.stdin.end();
  try {
    await Promise.race([
      new Promise((resolve) => child.once("exit", resolve)),
      new Promise((_, reject) => setTimeout(() => reject(new Error("sidecar stop timeout")), 10_000)),
    ]);
  } catch (error) {
    child.kill();
    throw error;
  }
}

async function allFiles(root) {
  const result = [];
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const candidate = path.join(root, entry.name);
    if (entry.isDirectory()) result.push(...await allFiles(candidate));
    else result.push(candidate);
  }
  return result;
}

const storageRoot = await mkdtemp(path.join(os.tmpdir(), "daon-raw-source-sidecar-"));
const storageKey = randomBytes(32).toString("hex");
const plaintext = Buffer.from("actual encrypted sidecar evidence");
let first;
let second;
let stage = "start-first";
try {
  first = await startSidecar(storageRoot, storageKey);
  stage = "import";
  const created = await requestJson(
    first.port,
    "POST",
    "/local/v1/studio/raw-sources",
    authorization(
      first.rootSecret, first.instanceId, "studio.write", "studio_raw_source_import", workspaceId,
    ),
    {
      workspace_id: workspaceId,
      filename: "actual.txt",
      content_type: "text/plain",
      content_base64: plaintext.toString("base64"),
      content_digest_sha256: createHash("sha256").update(plaintext).digest("hex"),
      idempotency_key: "actual-sidecar-raw-source-0001",
    },
  );
  assert.equal(created.status, 200);
  assert.equal(created.body.data.filename, "actual.txt");
  stage = "stop-first";
  await stopSidecar(first.child);
  first = null;

  stage = "start-second";
  second = await startSidecar(storageRoot, storageKey);
  stage = "list-after-restart";
  const listed = await requestJson(
    second.port,
    "GET",
    "/local/v1/studio/raw-sources",
    authorization(
      second.rootSecret, second.instanceId, "studio.read", "studio_raw_sources_list", workspaceId,
    ),
  );
  assert.equal(listed.status, 200);
  assert.equal(listed.body.data.length, 1);
  assert.equal(listed.body.data[0].source_version_id, created.body.data.source_version_id);
  const persisted = await Promise.all((await allFiles(storageRoot)).map((file) => readFile(file)));
  assert.equal(persisted.some((bytes) => bytes.includes(plaintext)), false);
  process.stdout.write(JSON.stringify({
    status: "PASS",
    imported: 1,
    listed_after_restart: 1,
    plaintext_at_rest_matches: 0,
  }) + "\n");
} catch (error) {
  const code = typeof error?.code === "string" ? error.code : "PROBE_FAILED";
  const childLog = (second?.stderr?.() || first?.stderr?.() || "")
    .replaceAll(/[0-9a-f]{64}/giu, "[redacted]")
    .slice(-512);
  process.stderr.write(`RAW_SOURCE_SIDECAR_PROBE_FAILED stage=${stage} code=${code} ${childLog}\n`);
  throw error;
} finally {
  if (first) await stopSidecar(first.child).catch(() => {});
  if (second) await stopSidecar(second.child).catch(() => {});
  plaintext.fill(0);
  await rm(storageRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
