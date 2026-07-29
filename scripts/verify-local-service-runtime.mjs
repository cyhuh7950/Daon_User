import { createHmac, randomBytes } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { generatedSidecarPath } from "./build-local-service-sidecar.mjs";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MAX_READY_BYTES = 4096;
const MAX_RUNTIME_OUTPUT_BYTES = 65_536;

function captureBounded(stream) {
  const chunks = [];
  let bytes = 0;
  let truncated = false;
  stream.on("data", (chunk) => {
    const remaining = MAX_RUNTIME_OUTPUT_BYTES - bytes;
    if (remaining <= 0) {
      truncated = true;
      return;
    }
    const accepted = chunk.subarray(0, remaining);
    chunks.push(accepted);
    bytes += accepted.length;
    if (accepted.length !== chunk.length) truncated = true;
  });
  return () => ({
    text: Buffer.concat(chunks).toString("utf8"),
    truncated
  });
}

function waitForReady(child, expectedInstance, timeoutMs = 15_000) {
  return new Promise((resolve, reject) => {
    let buffered = Buffer.alloc(0);
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("ready envelope timeout"));
    }, timeoutMs);
    const cleanup = () => {
      clearTimeout(timer);
      child.stdout.off("data", onData);
      child.off("exit", onExit);
    };
    const onExit = (code) => {
      cleanup();
      reject(new Error(`sidecar exited before ready: ${code}`));
    };
    const onData = (chunk) => {
      buffered = Buffer.concat([buffered, chunk]);
      if (buffered.length > MAX_READY_BYTES) {
        cleanup();
        reject(new Error("ready envelope exceeded maximum size"));
        return;
      }
      const newline = buffered.indexOf(0x0a);
      if (newline === -1) return;
      cleanup();
      try {
        const ready = JSON.parse(buffered.subarray(0, newline).toString("utf8"));
        assertReadyEnvelope(ready, expectedInstance);
        resolve(ready);
      } catch (error) {
        reject(error);
      }
    };
    child.stdout.on("data", onData);
    child.once("exit", onExit);
  });
}

function assertReadyEnvelope(value, expectedInstance) {
  const keys = Object.keys(value).sort();
  if (
    JSON.stringify(keys) !==
      JSON.stringify(["app_instance_id", "event", "port", "protocol_version"]) ||
    value.event !== "ready" ||
    value.protocol_version !== "1.0" ||
    value.app_instance_id !== expectedInstance ||
    !Number.isInteger(value.port) ||
    value.port < 1 ||
    value.port > 65_535
  ) {
    throw new Error("invalid ready envelope");
  }
}

export function assertRuntimeOutputBoundary(
  { stdout, stderr, stdoutTruncated, stderrTruncated },
  { rootSecret, storageRootKey, requestTokens, appInstanceId }
) {
  if (stdoutTruncated || stderrTruncated) {
    throw new Error("runtime output exceeded bounded inspection buffer");
  }
  const secrets = [rootSecret, storageRootKey, ...requestTokens];
  if (secrets.some((secret) => stdout.includes(secret) || stderr.includes(secret))) {
    throw new Error("credential appeared in runtime output");
  }
  const newline = stdout.indexOf("\n");
  const readyLine = newline === -1 ? stdout : stdout.slice(0, newline);
  const afterReady = newline === -1 ? "" : stdout.slice(newline + 1);
  let ready;
  try {
    ready = JSON.parse(readyLine);
  } catch {
    throw new Error("runtime stdout did not begin with ready envelope");
  }
  if (
    ready.app_instance_id !== appInstanceId
    || afterReady.includes(appInstanceId)
    || stderr.includes(appInstanceId)
  ) {
    throw new Error("instance appeared outside ready envelope");
  }
  return {
    token_emitted: false,
    instance_ready_envelope_only: true,
    output_truncated: false
  };
}

export function issueRequestToken({
  rootSecret,
  appInstanceId,
  capability,
  command,
  issuedAt = Math.floor(Date.now() / 1000)
}) {
  const expiresAt = issuedAt + 60;
  const nonce = randomBytes(32).toString("hex");
  const unsigned = [
    "lt1",
    issuedAt,
    expiresAt,
    appInstanceId,
    capability,
    command,
    nonce
  ].join("|");
  const signature = createHmac("sha256", Buffer.from(rootSecret, "hex"))
    .update(unsigned, "utf8")
    .digest("hex");
  return `${unsigned}|${signature}`;
}

export function assertLoopbackListener(rows, expectedPort) {
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error("listener attestation returned no rows");
  }
  if (rows.some((row) => row.LocalAddress !== "127.0.0.1")) {
    throw new Error("non-loopback listener detected");
  }
  if (
    expectedPort !== undefined &&
    !rows.some((row) => Number(row.LocalPort) === expectedPort)
  ) {
    throw new Error("ready port is not owned by the sidecar");
  }
}

function attestListeners(processId, expectedPort) {
  const command = [
    `$ids = [System.Collections.Generic.HashSet[uint32]]::new(); [void]$ids.Add(${processId});`,
    "do { $added = $false; Get-CimInstance Win32_Process | Where-Object { $ids.Contains([uint32]$_.ParentProcessId) -and -not $ids.Contains([uint32]$_.ProcessId) } | ForEach-Object { [void]$ids.Add([uint32]$_.ProcessId); $added = $true } } while ($added);",
    "$rows = @(Get-NetTCPConnection -State Listen -ErrorAction Stop",
    "| Where-Object { $ids.Contains([uint32]$_.OwningProcess) }",
    "| Select-Object LocalAddress,LocalPort,State,OwningProcess);",
    "if ($rows.Count -eq 0) { throw 'No listener owned by sidecar process tree.' };",
    "$rows | ConvertTo-Json -Compress"
  ].join(" ");
  const result = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", command],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
      windowsHide: true
    }
  );
  if (result.status !== 0) {
    throw new Error(
      `listener attestation failed: ${result.status ?? "spawn"} ${result.stderr?.trim() ?? ""}`
    );
  }
  const parsed = JSON.parse(result.stdout.trim());
  const rows = Array.isArray(parsed) ? parsed : [parsed];
  assertLoopbackListener(rows, expectedPort);
  return rows;
}

async function request(port, { token, method = "GET", pathName = "/v1/status", extraHeaders = {} }) {
  return new Promise((resolve, reject) => {
    const headers = { ...extraHeaders };
    if (token !== undefined) headers.authorization = `Bearer ${token}`;
    const clientRequest = http.request(
      { hostname: "127.0.0.1", port, path: pathName, method, headers, timeout: 3_000 },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.once("end", () => resolve({
          status: response.statusCode,
          body: Buffer.concat(chunks).toString("utf8")
        }));
      }
    );
    clientRequest.once("timeout", () => clientRequest.destroy(new Error("HTTP timeout")));
    clientRequest.once("error", reject);
    clientRequest.end();
  });
}

function rawRequest(port, payload, label) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    const chunks = [];
    let bytes = 0;
    const timer = setTimeout(() => {
      socket.destroy();
      reject(new Error("raw HTTP response timeout"));
    }, 3_000);
    const finish = (error) => {
      clearTimeout(timer);
      if (error) {
        reject(error);
        return;
      }
      const response = Buffer.concat(chunks).toString("utf8");
      const match = response.match(/^HTTP\/1\.1\s+(\d{3})/u);
      if (!match) {
        reject(
          new Error(
            `raw HTTP response missing status for ${label}: bytes=${Buffer.byteLength(response)}`
          )
        );
        return;
      }
      resolve({ status: Number(match[1]), response });
    };
    socket.on("connect", () => socket.write(payload));
    socket.on("data", (chunk) => {
      bytes += chunk.length;
      if (bytes > MAX_RUNTIME_OUTPUT_BYTES) {
        socket.destroy(new Error("raw HTTP response exceeded maximum size"));
        return;
      }
      chunks.push(chunk);
    });
    socket.once("error", finish);
    socket.once("end", () => finish());
  });
}

function authenticatedRawHeaders({ port, token, target = "/v1/status", extra = [] }) {
  return [
    `GET ${target} HTTP/1.1`,
    `Host: 127.0.0.1:${port}`,
    `Authorization: Bearer ${token}`,
    ...extra,
    "Connection: close",
    "",
    ""
  ].join("\r\n");
}

function waitForExit(child, timeoutMs = 5_000) {
  return new Promise((resolve, reject) => {
    if (child.exitCode !== null) {
      resolve(child.exitCode);
      return;
    }
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("sidecar did not exit after parent stdin EOF"));
    }, timeoutMs);
    child.once("exit", (code) => {
      clearTimeout(timer);
      resolve(code);
    });
  });
}

async function assertPortClosed(port) {
  try {
    await fetch(`http://127.0.0.1:${port}/v1/status`, {
      signal: AbortSignal.timeout(1_000)
    });
  } catch {
    return true;
  }
  throw new Error("listener remained reachable after parent stdin EOF");
}

async function runSingleLifecycle(
  executablePath,
  previousRunToken,
  storageRoot,
  storageRootKey
) {
  const appInstanceId = randomBytes(16).toString("hex");
  const rootSecret = randomBytes(32).toString("hex");
  const requestTokens = [];
  const tokenFor = (command = "runtime.status.read", capability = "runtime.read") => {
    const token = issueRequestToken({ rootSecret, appInstanceId, capability, command });
    requestTokens.push(token);
    return token;
  };
  const child = spawn(executablePath, [], {
    cwd: repositoryRoot,
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true
  });
  const capturedStdout = captureBounded(child.stdout);
  const capturedStderr = captureBounded(child.stderr);
  try {
    child.stdin.write(
      `${JSON.stringify({
        protocol_version: "1.0",
        app_instance_id: appInstanceId,
        root_secret: rootSecret,
        storage_root_key: storageRootKey,
        storage_root: storageRoot,
        parent_process_id: process.pid
      })}\n`
    );
    const ready = await waitForReady(child, appInstanceId);
    const listeners = attestListeners(child.pid, ready.port);
    const missingAuth = await request(ready.port, {});
    const validStatusToken = tokenFor();
    const wrongToken = await request(ready.port, {
      token: `${validStatusToken.slice(0, -1)}${validStatusToken.endsWith("0") ? "1" : "0"}`
    });
    const wrongCommand = await request(ready.port, {
      token: tokenFor("runtime.capabilities.read")
    });
    const authorized = await request(ready.port, { token: validStatusToken });
    const replayed = await request(ready.port, { token: validStatusToken });
    const capabilities = await request(ready.port, {
      token: tokenFor("runtime.capabilities.read"),
      pathName: "/v1/capabilities"
    });
    const previousRun = previousRunToken === undefined
      ? { status: 401 }
      : await request(ready.port, { token: previousRunToken });
    const unknown = await request(ready.port, {
      token: tokenFor(),
      pathName: "/v1/unknown"
    });
    const wrongMethod = await request(ready.port, {
      token: tokenFor(),
      method: "POST"
    });
    const browserOrigin = await request(ready.port, {
      token: tokenFor(),
      extraHeaders: { origin: "https://attacker.invalid" }
    });
    const zeroBody = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        port: ready.port,
        token: tokenFor(),
        extra: ["Content-Length: 0"]
      }),
      "content-length-zero"
    );
    const bodyRejected = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        port: ready.port,
        token: tokenFor(),
        extra: ["Content-Length: 1"]
      }).replace(/\r\n\r\n$/u, "\r\n\r\nx"),
      "one-byte-body"
    );
    const invalidLength = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        port: ready.port,
        token: tokenFor(),
        extra: ["Content-Length: invalid"]
      }),
      "invalid-content-length"
    );
    const transferEncoding = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        port: ready.port,
        token: tokenFor(),
        extra: ["Transfer-Encoding: chunked"]
      }),
      "transfer-encoding"
    );
    const acceptedHeaderBoundary = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        port: ready.port,
        token: tokenFor(),
        extra: [`X-Boundary: ${"a".repeat(7000)}`]
      }),
      "accepted-header"
    );
    const rejectedHeaderBoundary = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        port: ready.port,
        token: tokenFor(),
        extra: [`X-Boundary: ${"a".repeat(9000)}`]
      }),
      "rejected-header"
    );
    const externalHost = await rawRequest(
      ready.port,
      authenticatedRawHeaders({ port: ready.port, token: tokenFor() })
        .replace(`Host: 127.0.0.1:${ready.port}`, "Host: attacker.invalid"),
      "external-host"
    );
    const forwarded = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        port: ready.port,
        token: tokenFor(),
        extra: ["Forwarded: host=attacker.invalid"]
      }),
      "forwarded"
    );
    const absoluteTarget = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        port: ready.port,
        token: tokenFor(),
        target: `http://127.0.0.1:${ready.port}/v1/status`
      }),
      "absolute-target"
    );
    const queryBypass = await rawRequest(
      ready.port,
      authenticatedRawHeaders({ port: ready.port, token: tokenFor(), target: "/v1/status?x=1" }),
      "query-bypass"
    );
    const encodedPath = await rawRequest(
      ready.port,
      authenticatedRawHeaders({ port: ready.port, token: tokenFor(), target: "/%76%31/status" }),
      "encoded-path"
    );
    const responseBody = JSON.parse(authorized.body);
    const observedStatuses = {
      missing_auth: missingAuth.status,
      wrong_token: wrongToken.status,
      wrong_command: wrongCommand.status,
      authorized: authorized.status,
      replayed: replayed.status,
      capabilities: capabilities.status,
      previous_run_token: previousRun.status,
      unknown_path: unknown.status,
      wrong_method: wrongMethod.status,
      browser_origin: browserOrigin.status,
      content_length_zero: zeroBody.status,
      body_one_byte: bodyRejected.status,
      invalid_content_length: invalidLength.status,
      transfer_encoding: transferEncoding.status,
      accepted_header: acceptedHeaderBoundary.status,
      rejected_header: rejectedHeaderBoundary.status,
      external_host: externalHost.status,
      forwarded: forwarded.status,
      absolute_target: absoluteTarget.status,
      query_bypass: queryBypass.status,
      encoded_path: encodedPath.status
    };
    if (
      missingAuth.status !== 401 ||
      wrongToken.status !== 401 ||
      wrongCommand.status !== 401 ||
      authorized.status !== 200 ||
      replayed.status !== 401 ||
      capabilities.status !== 200 ||
      previousRun.status !== 401 ||
      unknown.status !== 404 ||
      wrongMethod.status !== 405 ||
      browserOrigin.status !== 403 ||
      zeroBody.status !== 200 ||
      bodyRejected.status !== 413 ||
      invalidLength.status !== 400 ||
      transferEncoding.status !== 400 ||
      acceptedHeaderBoundary.status !== 200 ||
      ![400, 431].includes(rejectedHeaderBoundary.status) ||
      externalHost.status !== 400 ||
      forwarded.status !== 400 ||
      absoluteTarget.status !== 400 ||
      queryBypass.status !== 400 ||
      encodedPath.status !== 400 ||
      Object.hasOwn(responseBody, "token") ||
      Object.hasOwn(responseBody, "port") ||
      Object.hasOwn(responseBody, "app_instance_id") ||
      Object.hasOwn(responseBody, "process_id")
    ) {
      throw new Error(
        `runtime authentication or allowlist contract failed: ${JSON.stringify(observedStatuses)}`
      );
    }
    child.stdin.end();
    const exitCode = await waitForExit(child);
    await assertPortClosed(ready.port);
    const stdout = capturedStdout();
    const stderr = capturedStderr();
    if (exitCode !== 0) throw new Error(`sidecar exit code ${exitCode}: ${stderr.text}`);
    const outputAttestation = assertRuntimeOutputBoundary(
      {
        stdout: stdout.text,
        stderr: stderr.text,
        stdoutTruncated: stdout.truncated,
        stderrTruncated: stderr.truncated
      },
      { rootSecret, storageRootKey, requestTokens, appInstanceId }
    );
    return {
      appInstanceId,
      rootSecret,
      replayProbeToken: validStatusToken,
      port: ready.port,
      listener_count: listeners.length,
      statuses: {
        missing_auth: missingAuth.status,
        wrong_token: wrongToken.status,
        wrong_command: wrongCommand.status,
        authorized: authorized.status,
        replayed: replayed.status,
        previous_run_token: previousRun.status,
        capabilities: capabilities.status,
        unknown_path: unknown.status,
        wrong_method: wrongMethod.status,
        browser_origin: browserOrigin.status,
        external_host: externalHost.status,
        forwarded: forwarded.status,
        absolute_target: absoluteTarget.status,
        query_bypass: queryBypass.status,
        encoded_path: encodedPath.status
      },
      http_boundaries: {
        content_length_zero: zeroBody.status,
        body_one_byte: bodyRejected.status,
        invalid_content_length: invalidLength.status,
        transfer_encoding: transferEncoding.status,
        accepted_header_bytes: 7000,
        accepted_header_status: acceptedHeaderBoundary.status,
        rejected_header_bytes: 9000,
        rejected_header_status: rejectedHeaderBoundary.status
      },
      output_attestation: outputAttestation,
      clean_exit: true,
      listener_closed: true
    };
  } catch (error) {
    child.stdin.destroy();
    if (child.exitCode === null) child.kill();
    throw error;
  }
}

export async function runPackagedLocalServiceLifecycle(
  executablePath = generatedSidecarPath
) {
  const storageRoot = await mkdtemp(path.join(os.tmpdir(), "daon-local-storage-runtime-"));
  const storageRootKey = randomBytes(32).toString("hex");
  try {
    const first = await runSingleLifecycle(
      executablePath,
      undefined,
      storageRoot,
      storageRootKey
    );
    const second = await runSingleLifecycle(
      executablePath,
      first.replayProbeToken,
      storageRoot,
      storageRootKey
    );
    if (first.appInstanceId === second.appInstanceId || first.rootSecret === second.rootSecret) {
      throw new Error("per-instance credentials were reused");
    }
    return {
      schema_version: "1.0",
      executable: path.relative(repositoryRoot, executablePath).split(path.sep).join("/"),
      runs: [
        {
          port: first.port,
          listener_count: first.listener_count,
          statuses: first.statuses,
          http_boundaries: first.http_boundaries,
          output_attestation: first.output_attestation,
          clean_exit: first.clean_exit,
          listener_closed: first.listener_closed
        },
        {
          port: second.port,
          listener_count: second.listener_count,
          statuses: second.statuses,
          http_boundaries: second.http_boundaries,
          output_attestation: second.output_attestation,
          clean_exit: second.clean_exit,
          listener_closed: second.listener_closed
        }
      ],
      credentials_unique: true,
      token_emitted: false,
      instance_emission_scope: "ready_envelope_only",
      output_buffers_complete: true,
      storage_restart_unlock: true
    };
  } finally {
    await rm(storageRoot, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100
    });
  }
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  runPackagedLocalServiceLifecycle()
    .then((result) => console.log(JSON.stringify(result)))
    .catch((error) => {
      console.error(`LOCAL_SERVICE_RUNTIME_ERROR ${error.message}`);
      process.exitCode = 1;
    });
}
