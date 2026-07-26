import { randomBytes } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import net from "node:net";
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
  { token, appInstanceId }
) {
  if (stdoutTruncated || stderrTruncated) {
    throw new Error("runtime output exceeded bounded inspection buffer");
  }
  if (stdout.includes(token) || stderr.includes(token)) {
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

async function request(port, { token, appInstanceId, method = "GET", pathName = "/v1/status" }) {
  const headers = {};
  if (token !== undefined) headers.authorization = `Bearer ${token}`;
  if (appInstanceId !== undefined) headers["x-daon-app-instance"] = appInstanceId;
  const response = await fetch(`http://127.0.0.1:${port}${pathName}`, {
    method,
    headers,
    signal: AbortSignal.timeout(3_000)
  });
  const body = await response.text();
  return { status: response.status, body };
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

function authenticatedRawHeaders({ token, appInstanceId, extra = [] }) {
  return [
    "GET /v1/status HTTP/1.1",
    "Host: 127.0.0.1",
    `Authorization: Bearer ${token}`,
    `X-Daon-App-Instance: ${appInstanceId}`,
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

async function runSingleLifecycle(executablePath) {
  const appInstanceId = randomBytes(16).toString("hex");
  const token = randomBytes(32).toString("hex");
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
        token
      })}\n`
    );
    const ready = await waitForReady(child, appInstanceId);
    const listeners = attestListeners(child.pid, ready.port);
    const missingAuth = await request(ready.port, {});
    const wrongToken = await request(ready.port, {
      token: `${token.slice(0, -1)}${token.endsWith("0") ? "1" : "0"}`,
      appInstanceId
    });
    const wrongInstance = await request(ready.port, {
      token,
      appInstanceId: `${appInstanceId}-wrong`
    });
    const authorized = await request(ready.port, { token, appInstanceId });
    const unknown = await request(ready.port, {
      token,
      appInstanceId,
      pathName: "/v1/unknown"
    });
    const wrongMethod = await request(ready.port, {
      token,
      appInstanceId,
      method: "POST"
    });
    const zeroBody = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        token,
        appInstanceId,
        extra: ["Content-Length: 0"]
      }),
      "content-length-zero"
    );
    const bodyRejected = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        token,
        appInstanceId,
        extra: ["Content-Length: 1"]
      }).replace(/\r\n\r\n$/u, "\r\n\r\nx"),
      "one-byte-body"
    );
    const invalidLength = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        token,
        appInstanceId,
        extra: ["Content-Length: invalid"]
      }),
      "invalid-content-length"
    );
    const transferEncoding = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        token,
        appInstanceId,
        extra: ["Transfer-Encoding: chunked"]
      }),
      "transfer-encoding"
    );
    const acceptedHeaderBoundary = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        token,
        appInstanceId,
        extra: [`X-Boundary: ${"a".repeat(7000)}`]
      }),
      "accepted-header"
    );
    const rejectedHeaderBoundary = await rawRequest(
      ready.port,
      authenticatedRawHeaders({
        token,
        appInstanceId,
        extra: [`X-Boundary: ${"a".repeat(9000)}`]
      }),
      "rejected-header"
    );
    const responseBody = JSON.parse(authorized.body);
    if (
      missingAuth.status !== 401 ||
      wrongToken.status !== 401 ||
      wrongInstance.status !== 401 ||
      authorized.status !== 200 ||
      unknown.status !== 404 ||
      wrongMethod.status !== 405 ||
      zeroBody.status !== 200 ||
      bodyRejected.status !== 413 ||
      invalidLength.status !== 400 ||
      transferEncoding.status !== 400 ||
      acceptedHeaderBoundary.status !== 200 ||
      ![400, 431].includes(rejectedHeaderBoundary.status) ||
      Object.hasOwn(responseBody, "token") ||
      Object.hasOwn(responseBody, "port")
    ) {
      throw new Error("runtime authentication or allowlist contract failed");
    }
    child.stdin.end();
    const exitCode = await waitForExit(child);
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
      { token, appInstanceId }
    );
    return {
      appInstanceId,
      token,
      port: ready.port,
      listener_count: listeners.length,
      statuses: {
        missing_auth: missingAuth.status,
        wrong_token: wrongToken.status,
        wrong_instance: wrongInstance.status,
        authorized: authorized.status,
        unknown_path: unknown.status,
        wrong_method: wrongMethod.status
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
      clean_exit: true
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
  const first = await runSingleLifecycle(executablePath);
  const second = await runSingleLifecycle(executablePath);
  if (first.appInstanceId === second.appInstanceId || first.token === second.token) {
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
        clean_exit: first.clean_exit
      },
      {
        port: second.port,
        listener_count: second.listener_count,
        statuses: second.statuses,
        http_boundaries: second.http_boundaries,
        output_attestation: second.output_attestation,
        clean_exit: second.clean_exit
      }
    ],
    credentials_unique: true,
    token_emitted: false,
    instance_emission_scope: "ready_envelope_only",
    output_buffers_complete: true
  };
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  runPackagedLocalServiceLifecycle()
    .then((result) => console.log(JSON.stringify(result)))
    .catch((error) => {
      console.error(`LOCAL_SERVICE_RUNTIME_ERROR ${error.message}`);
      process.exitCode = 1;
    });
}
