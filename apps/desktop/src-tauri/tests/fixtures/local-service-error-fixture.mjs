import { appendFileSync, readFileSync } from "node:fs";
import http from "node:http";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const mode = process.env.DAON_MANAGER_FIXTURE_MODE;
const role = process.env.DAON_MANAGER_FIXTURE_ROLE ?? "parent";
const port = Number(process.env.DAON_MANAGER_FIXTURE_PORT);
const marker = process.env.DAON_MANAGER_FIXTURE_MARKER;

function appendMarker(kind, value) {
  appendFileSync(marker, `${kind}=${value}\n`, "utf8");
}

function serveHealth(ready) {
  const server = http.createServer((_request, response) => {
    const body = JSON.stringify({ status: ready ? "ready" : "unavailable" });
    response.writeHead(ready ? 200 : 503, {
      "content-type": "application/json",
      "content-length": Buffer.byteLength(body),
      connection: "close"
    });
    response.end(body);
  });
  server.listen(port, "127.0.0.1");
  return new Promise((resolve) => server.once("listening", () => resolve(server)));
}

if (role === "listener") {
  appendMarker("listener", process.pid);
  await serveHealth(true);
  await new Promise(() => {});
}

appendMarker("parent", process.pid);
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const firstLine = await new Promise((resolve) => input.once("line", resolve));
const bootstrap = JSON.parse(firstLine);
const launchIndex = readFileSync(marker, "utf8")
  .split(/\r?\n/)
  .filter((line) => line.startsWith("credential=")).length;
appendMarker("credential", bootstrap.app_instance_id);

if (mode === "no_ready") {
  await new Promise(() => {});
}

if (mode === "invalid_then_ready" && launchIndex === 0) {
  process.stdout.write(
    `${JSON.stringify({
      event: "Ready",
      protocol_version: "0",
      app_instance_id: "invalid",
      port
    })}\n`
  );
  process.exit(0);
}

let healthServer;
if (mode === "stubborn_tree") {
  const descendant = spawn(process.execPath, [fileURLToPath(import.meta.url)], {
    env: {
      ...process.env,
      DAON_MANAGER_FIXTURE_MODE: "listener",
      DAON_MANAGER_FIXTURE_ROLE: "listener"
    },
    stdio: "ignore",
    windowsHide: true
  });
  appendMarker("descendant", descendant.pid);
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try {
      await fetch(`http://127.0.0.1:${port}/v1/status`);
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }
} else {
  healthServer = await serveHealth(
    !(mode === "health_fail_then_ready" && launchIndex === 0)
  );
}

process.stdout.write(
  `${JSON.stringify({
    event: "ready",
    protocol_version: "1.0",
    app_instance_id: bootstrap.app_instance_id,
    port
  })}\n`
);

if (mode === "stubborn_tree") {
  await new Promise(() => {});
}

await new Promise((resolve) => input.once("close", resolve));
await new Promise((resolve) => healthServer.close(resolve));
