import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import http from "node:http";
import path from "node:path";
import test from "node:test";

const SESSION = {
  user_id: "user-001", tenant_id: "tenant-001", workspace_id: "workspace-001",
  session_id: "session-001", device_id: "device-001", client_kind: "web",
  delivery: "same_origin_secure_cookie", expires_at: "2026-09-15T12:00:00Z",
  recovery_operations: [], password_change_required: false,
};

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

async function waitForNext(origin, output) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(origin, { redirect: "manual" });
      if (response.status > 0) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`NEXT_SERVER_NOT_READY\n${output.join("")}`);
}

test("actual Next /admin response redirects unauthenticated and returns 403 before rendering member data", { timeout: 60_000 }, async () => {
  let adminUserCalls = 0;
  const sessionRequests = [];
  const upstream = http.createServer((request, response) => {
    if (request.url === "/api/v1/admin/users") {
      adminUserCalls += 1;
      response.writeHead(500).end(); return;
    }
    if (request.url !== "/api/v1/session") {
      response.writeHead(404).end(); return;
    }
    const cookie = request.headers.cookie ?? "";
    sessionRequests.push({ cookie, transport: request.headers["x-daon-bff-transport"] });
    if (!cookie.includes("__Host-daon_session=")) {
      response.writeHead(401, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { code: "AUTHENTICATION_REQUIRED" } })); return;
    }
    const isSystemAdmin = cookie.includes("__Host-daon_session=admin");
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ data: { ...SESSION, is_system_admin: isSystemAdmin }, meta: { trace_id: "trace-session-001" } }));
  });
  const upstreamPort = await listen(upstream);
  const reservation = http.createServer(); const webPort = await listen(reservation);
  await new Promise((resolve) => reservation.close(resolve));
  const output = [];
  const child = spawn(process.execPath, [path.resolve("node_modules/next/dist/bin/next"), "dev", "apps/web", "--hostname", "127.0.0.1", "--port", String(webPort)], {
    cwd: path.resolve("."), stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      DAON_API_INTERNAL_URL: `http://127.0.0.1:${upstreamPort}`,
      DAON_RUNTIME_PROFILE: "local_test",
      DAON_PUBLIC_GATEWAY_URL: `http://127.0.0.1:${webPort}`,
      DAON_BFF_PROFILE: "local_test",
    },
  });
  child.stdout.on("data", (chunk) => output.push(String(chunk)));
  child.stderr.on("data", (chunk) => output.push(String(chunk)));
  try {
    const origin = `http://127.0.0.1:${webPort}`;
    await waitForNext(origin, output);
    const unauthenticated = await fetch(`${origin}/admin`, { redirect: "manual" });
    const member = await fetch(`${origin}/admin`, { headers: { Cookie: "__Host-daon_session=member" }, redirect: "manual" });
    const admin = await fetch(`${origin}/admin`, { headers: { Cookie: "__Host-daon_session=admin" }, redirect: "manual" });
    const memberHtml = await member.text();
    assert.equal(unauthenticated.status, 307);
    assert.equal(new URL(unauthenticated.headers.get("location"), origin).pathname, "/");
    assert.equal(member.status, 403);
    assert.doesNotMatch(memberHtml, /DAON ADMIN|user-001/u);
    assert.equal(admin.status, 200);
    assert.equal(adminUserCalls, 0);
    assert.deepEqual(sessionRequests, [
      { cookie: "__Host-daon_session=member", transport: "internal" },
      { cookie: "__Host-daon_session=admin", transport: "internal" },
    ]);
  } finally {
    if (child.exitCode === null) {
      child.kill();
      await new Promise((resolve) => child.once("exit", resolve));
    }
    await new Promise((resolve) => upstream.close(resolve));
  }
});
