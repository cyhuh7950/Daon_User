import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { createWebShellRuntimeDescriptor, runtimeMethodNotAllowed } from "../../apps/web/lib/web-shell-runtime.js";
import { createWebShellRuntimeState, transitionWebShellRuntime } from "../../packages/ui/src/web-shell-runtime-model.js";
import { digestFile } from "../lib/portable-evidence.mjs";

const failureServerMode = process.argv.includes("--failure-server");
const shellTest = failureServerMode ? () => {} : test;

shellTest("server-only runtime descriptor는 shell 준비와 downstream 미구현을 분리한다", () => {
  const descriptor = createWebShellRuntimeDescriptor({ now: "2026-07-23T01:00:00.000Z" });
  assert.deepEqual(descriptor, {
    code: "WEB_SHELL_READY", ready: true, shell_version: "r1-m3-01", build_id: "r1-m3-01",
    downstream_state: "deferred_actual", observed_at: "2026-07-23T01:00:00.000Z"
  });
  assert.doesNotMatch(JSON.stringify(descriptor), /https?:\/\/|localhost|127\.0\.0\.1|host|port|secret|token|password|stack|provider/i);
});

shellTest("허용하지 않은 method는 내부 정보 없는 안정 오류로 거부한다", () => {
  assert.deepEqual(runtimeMethodNotAllowed(), { code: "METHOD_NOT_ALLOWED", ready: false, downstream_state: "deferred_actual", retryable: false });
});

shellTest("browser component는 유일한 same-origin 상대 BFF 경로만 호출한다", () => {
  const component = fs.readFileSync("packages/ui/src/web-shell-runtime-status.jsx", "utf8");
  const route = fs.readFileSync("apps/web/app/bff/shell/runtime/route.js", "utf8");
  const layout = fs.readFileSync("apps/web/app/layout.jsx", "utf8");
  assert.match(component, /fetch\("\/bff\/shell\/runtime"/);
  assert.doesNotMatch(component, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL|process\.env/);
  assert.match(route, /web-shell-runtime\.js/);
  assert.doesNotMatch(route, /fetch\s*\(|https?:\/\/|localhost|127\.0\.0\.1|process\.env/);
  assert.match(layout, /<WebShellRuntimeStatus\s*\/>/);
  assert.match(component, /aria-live="polite"/);
  assert.match(component, />재시도</);
});

shellTest("실제 route handler는 no-store GET과 안전한 405를 반환한다", async () => {
  const route = await import("../../apps/web/app/bff/shell/runtime/route.js");
  const getResponse = await route.GET();
  assert.equal(getResponse.status, 200);
  assert.equal(getResponse.headers.get("cache-control"), "no-store");
  assert.equal((await getResponse.json()).downstream_state, "deferred_actual");
  const postResponse = await route.POST();
  assert.equal(postResponse.status, 405);
  assert.equal(postResponse.headers.get("allow"), "GET, HEAD");
  assert.deepEqual(await postResponse.json(), runtimeMethodNotAllowed());
});

shellTest("runtime 조회 실패는 마지막 성공을 보존하고 성공으로 표시하지 않는다", () => {
  let state = createWebShellRuntimeState();
  state = transitionWebShellRuntime(state, { type: "request-started" });
  state = transitionWebShellRuntime(state, { type: "request-succeeded", descriptor: createWebShellRuntimeDescriptor({ now: "2026-07-23T01:00:00.000Z" }) });
  state = transitionWebShellRuntime(state, { type: "request-failed", code: "RUNTIME_UNAVAILABLE" });
  assert.equal(state.status, "recovering");
  assert.equal(state.ready, false);
  assert.equal(state.last_success.observed_at, "2026-07-23T01:00:00.000Z");
  assert.equal(state.error.code, "RUNTIME_UNAVAILABLE");
  assert.equal(state.retryable, true);
});

shellTest("navigation, screen, token과 M2 model/reducer 정본은 변경하지 않는다", () => {
  const expected = {
    "packages/contracts/navigation.json": "A328A3882BEDDA9261407673BBFDD1F4671E7DAE357E44A26A379A064D7B2845",
    "packages/contracts/screens.json": "4A3FC57A73C29E3657E09AB8C32C609F2529CA6D01E9AC5333ED9F9E05BFB1E1",
    "packages/design-tokens/tokens.css": "5AC29A2FCDF1F881180E2E71EBA3018C2540E6DDA36C8D8A8CC3A6007A89B309",
    "packages/ui/src/workspace-model.js": "8106A200747F0BBCBBC65C7E78BB840670B345CBFC8EC9EFED92C419B0F57D0A",
    "packages/ui/src/source-knowledge-model.js": "B88FE2EF4C76E4EBFF329946A5937A213301E18E2B6DC6521101038B7188771A",
    "packages/ui/src/run-model-evidence-model.js": "E9215A565DF2CDBD3AD9B5F1360181C646D759F94772959C98DAC710E565B54B",
    "packages/ui/src/studio-workflow-model.js": "3BE631BA653518CE76012E3FB8E69E505939F4684F7BA0966B00AC855472C8C0",
    "packages/ui/src/account-security-model.js": "34087194643209501ED58E61699AB5FC707E1E7A50D117367E6264066CEA6567",
    "packages/ui/src/operations-recovery-model.js": "EF11DB5178794004D548DE0C7772BFB91664A21A89AB1FDD3FB9CD219E41DC02"
  };
  for (const [file, hash] of Object.entries(expected)) {
    assert.equal(digestFile(file, "portable_utf8_lf").sha256, hash, file);
  }
});

if (failureServerMode) {
  const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const homeHtml = path.join(repositoryRoot, "apps/web/.next/server/app/index.html");
  const staticRoot = path.join(repositoryRoot, "apps/web/.next/static");
  const contentTypes = { ".css": "text/css", ".js": "text/javascript", ".json": "application/json", ".woff2": "font/woff2" };
  const server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url, "http://127.0.0.1");
    if (requestUrl.pathname === "/bff/shell/runtime") {
      response.writeHead(503, { "Cache-Control": "no-store", "Content-Type": "application/json" });
      response.end(JSON.stringify({ code: "RUNTIME_UNAVAILABLE", ready: false, downstream_state: "deferred_actual", retryable: true }));
      return;
    }
    const target = requestUrl.pathname === "/"
      ? homeHtml
      : requestUrl.pathname.startsWith("/_next/static/")
        ? path.join(staticRoot, requestUrl.pathname.slice("/_next/static/".length))
        : null;
    if (!target || (target !== homeHtml && !path.resolve(target).startsWith(path.resolve(staticRoot) + path.sep)) || !fs.existsSync(target)) {
      response.writeHead(404, { "Content-Type": "text/plain" });
      response.end("Not Found");
      return;
    }
    response.writeHead(200, { "Content-Type": target === homeHtml ? "text/html; charset=utf-8" : (contentTypes[path.extname(target)] ?? "application/octet-stream") });
    fs.createReadStream(target).pipe(response);
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(4180, "127.0.0.1", resolve);
  });
  console.log("R1_M3_01_FAILURE_SERVER_READY 127.0.0.1:4180");
  await new Promise(() => {});
}
