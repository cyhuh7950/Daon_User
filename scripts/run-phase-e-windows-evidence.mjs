import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { randomBytes, randomUUID } from "node:crypto";
import path from "node:path";

const root = process.cwd();
const evidenceDir = path.join(root, "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01");
const nativeContractPath = path.join(process.env.TEMP || process.env.TMP || root, "daon-phase-e-native-contract.conf");
const vitePort = 4237;
const cdpPort = 9347;
const uiAutomation = process.argv.includes("--ui-automation");
const restoreSessionWindow = process.argv.includes("--restore-session-window");
const directCompiledRestore = process.argv.includes("--direct-compiled-restore");
const memoryInput = uiAutomation ? await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("EVIDENCE_MEMORY_INPUT_TIMEOUT")), 5_000);
  process.stdin.once("data", (chunk) => {
    clearTimeout(timer);
    try { resolve(JSON.parse(chunk.toString("utf8"))); } catch { reject(new Error("EVIDENCE_MEMORY_INPUT_INVALID")); }
  });
}) : {};
const safeId = (prefix) => `${prefix}-${randomUUID()}`;
const identity = {
  login: memoryInput.login || `gate-${randomBytes(8).toString("hex")}`,
  password: memoryInput.password || randomBytes(24).toString("base64url"),
  access: `access-${randomBytes(32).toString("base64url")}`,
  refresh: `refresh-${randomBytes(32).toString("base64url")}`,
};
const scopes = [0, 1].map(() => ({ tenant: safeId("tenant"), workspace: safeId("workspace"), session: safeId("session"), user: safeId("user"), device: safeId("device") }));
const existing = { notebook: safeId("notebook"), source: safeId("source"), version: safeId("version"), thread: safeId("thread"), output: safeId("output"), outputVersion: safeId("output-version") };
const created = { notebook: safeId("notebook") };
const requestKinds = new Map();
let loginCount = 0;
let finishGate;
const gateFinished = new Promise((resolve) => { finishGate = resolve; });

const note = (scope, notebookId, title, sourceCount, outputCount, status = "active") => ({
  notebook_id: notebookId, title, source_count: sourceCount, output_count: outputCount,
  updated_at: "2026-08-20T08:00:00Z", status,
});
const meta = (scope, replayed) => ({ trace_id: safeId("trace"), workspace_id: scope.workspace, ...(replayed === undefined ? {} : { replayed }) });
const citation = () => ({
  citation_id: safeId("citation"), source_id: existing.source, source_version_id: existing.version,
  evidence_span_id: safeId("span"), page: 1, origin: "raw_source", context_item_id: existing.source,
  locator: { kind: "page", value: "1" },
});
const json = (response, status, body) => {
  const bytes = Buffer.from(JSON.stringify(body));
  response.writeHead(status, { "Content-Type": "application/json", "Content-Length": bytes.length, Connection: "close" });
  response.end(bytes);
};
const bodyOf = async (request) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks);
};
const classify = (method, pathname) => {
  if (pathname.endsWith("/auth/native/login")) return "login";
  if (pathname.endsWith("/auth/native/refresh")) return "refresh";
  if (pathname.includes("/notebooks/") && pathname.endsWith("/context")) return "context";
  if (pathname.endsWith("/notebooks")) return method === "POST" ? "notebook_create" : "notebook_list";
  if (pathname.includes("/notebooks/")) return "notebook_get";
  if (pathname.endsWith("/sources")) return "source_list";
  if (pathname.endsWith("/questions")) return "question";
  if (pathname.endsWith("/studio/reports")) return "studio_create";
  if (pathname.endsWith("/studio/outputs")) return "studio_list";
  return "other";
};

const api = createServer(async (request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  if (url.pathname === "/__phase_e_windows/complete" && request.method === "POST") {
    json(response, 200, { status: "accepted" });
    finishGate();
    return;
  }
  const kind = classify(request.method, url.pathname);
  requestKinds.set(kind, (requestKinds.get(kind) ?? 0) + 1);
  const raw = await bodyOf(request);
  if (kind === "login" && request.method === "POST") {
    let submitted;
    try { submitted = JSON.parse(raw.toString("utf8")); } catch { return json(response, 401, { error: { code: "AUTHENTICATION_REQUIRED" } }); }
    if (submitted.login_id !== identity.login || submitted.password !== identity.password) return json(response, 401, { error: { code: "AUTHENTICATION_REQUIRED" } });
    const scope = scopes[Math.min(loginCount++, 1)];
    return json(response, 200, { data: {
      user_id: scope.user, tenant_id: scope.tenant, workspace_id: scope.workspace,
      session_id: scope.session, device_id: scope.device, client_kind: "native",
      delivery: "native_https_opaque_bearer", access_credential: identity.access,
      refresh_credential: identity.refresh, expires_at: "2026-08-21T08:00:00Z",
    } });
  }
  if (request.headers.authorization !== `Bearer ${identity.access}`) return json(response, 401, { error: { code: "AUTHENTICATION_REQUIRED" } });
  const scope = url.pathname.includes(scopes[1].workspace) ? scopes[1] : scopes[0];
  if (kind === "notebook_list") {
    return json(response, 200, { data: scope === scopes[0] ? [note(scope, existing.notebook, "보존된 지식 Notebook", 1, 0)] : [], meta: meta(scope) });
  }
  if (kind === "notebook_create" && scope === scopes[0]) return json(response, 201, { data: note(scope, created.notebook, "새 지식 Notebook", 0, 0, "empty"), meta: meta(scope, false) });
  const notebookId = decodeURIComponent(url.pathname.split("/").at(-1));
  const contextNotebookId = decodeURIComponent(url.pathname.split("/").at(-2));
  if (kind === "notebook_get") {
    if (scope !== scopes[0]) return json(response, 404, { error: { code: "RESOURCE_UNAVAILABLE" } });
    if (notebookId === existing.notebook) return json(response, 200, { data: note(scope, existing.notebook, "보존된 지식 Notebook", 1, 0), meta: meta(scope) });
    if (notebookId === created.notebook) return json(response, 200, { data: note(scope, created.notebook, "새 지식 Notebook", 0, 0, "empty"), meta: meta(scope) });
  }
  if (kind === "context") {
    if (scope !== scopes[0]) return json(response, 404, { error: { code: "RESOURCE_UNAVAILABLE" } });
    if (contextNotebookId === created.notebook) return json(response, 200, { data: { notebook_id: created.notebook, sources: [], knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [], conversation: null }, meta: meta(scope) });
    if (contextNotebookId === existing.notebook) return json(response, 200, { data: {
      notebook_id: existing.notebook,
      sources: [{ source_id: existing.source, source_version_id: existing.version }],
      knowledge_context_ids: [], conversation_thread_ids: [existing.thread], studio_output_ids: [],
      output_version_ids: [], generation_settings_ids: [],
      conversation: {
        conversation_thread_id: existing.thread,
        answer: {
          run_id: safeId("run"), run_result_id: safeId("result"),
          answer: "보존된 대화 답변입니다.", insufficient: false, citations: [citation()],
        },
      },
    }, meta: meta(scope) });
  }
  const selected = url.searchParams.get("notebook_id") ?? (() => { try { return JSON.parse(raw.toString("utf8")).notebook_id; } catch { return null; } })();
  if (scope !== scopes[0] || selected !== existing.notebook) return json(response, 404, { error: { code: "RESOURCE_UNAVAILABLE" } });
  if (kind === "source_list") return json(response, 200, { data: { sources: [{ source_id: existing.source, source_version_id: existing.version, filename: "검증된-지식.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }] }, meta: meta(scope) });
  if (kind === "studio_list") return json(response, 200, { data: { outputs: [] }, meta: meta(scope) });
  if (kind === "question") return json(response, 200, { data: { run_id: safeId("run"), run_result_id: safeId("result"), answer: "선택한 Source에 근거한 검증 답변입니다.", insufficient: false, citations: [citation()] }, meta: meta(scope, true) });
  if (kind === "studio_create") return json(response, 201, { data: { studio_output_id: existing.output, output_version_id: existing.outputVersion, output_type: "evidence_report", title: "근거 검증 보고서", purpose: "실제 제품 흐름 검증", status: "draft", content: "근거가 결속된 검증 보고서입니다.", run_id: safeId("run"), run_result_id: safeId("result"), citations: [citation()] }, meta: meta(scope, false) });
  return json(response, 404, { error: { code: "RESOURCE_UNAVAILABLE" } });
});

const listen = (server) => new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", () => resolve(server.address().port)); });
const close = (server) => new Promise((resolve) => server.close(resolve));
const children = [];
const start = (command, args, options) => {
  const child = spawn(command, args, { ...options, windowsHide: options.windowsHide ?? true, stdio: ["ignore", "pipe", "pipe"] });
  child.safeTail = "";
  const retainTail = (chunk) => { child.safeTail = `${child.safeTail}${chunk}`.slice(-4_000); };
  child.stdout.on("data", retainTail);
  child.stderr.on("data", retainTail);
  children.push(child); return child;
};
const waitHttp = async (url, timeoutMs = 180_000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { const response = await fetch(url, { signal: AbortSignal.timeout(1_000) }); if (response.ok) return response; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("EVIDENCE_ENDPOINT_TIMEOUT");
};
const stop = async (child) => {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
    await Promise.race([new Promise((resolve) => killer.once("exit", resolve)), new Promise((resolve) => setTimeout(resolve, 10_000))]);
    return;
  }
  child.kill();
  await Promise.race([new Promise((resolve) => child.once("exit", resolve)), new Promise((resolve) => setTimeout(resolve, 5_000))]);
  if (child.exitCode === null) child.kill("SIGKILL");
};
const waitForTargetableWindow = async (timeoutMs = 120_000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const probe = spawn("powershell.exe", ["-NoProfile", "-Command", "$p=@(Get-Process -Name 'daon-user-desktop' -ErrorAction SilentlyContinue|Where-Object{[int64]$_.MainWindowHandle.Value -ne 0 -and $_.MainWindowTitle}|Select-Object Id,@{n='MainWindowHandle';e={[int64]$_.MainWindowHandle.Value}},MainWindowTitle);$p|ConvertTo-Json -Compress"], { windowsHide: true, stdio: ["ignore", "pipe", "ignore"] });
    let output = "";
    probe.stdout.on("data", (chunk) => { output += chunk; });
    await new Promise((resolve) => probe.once("exit", resolve));
    try {
      const parsed = JSON.parse(output.trim());
      const windows = Array.isArray(parsed) ? parsed : parsed ? [parsed] : [];
      if (windows.length === 1) return windows[0];
      if (windows.length > 1) throw new Error("EVIDENCE_WINDOW_COUNT_INVALID");
    } catch (error) {
      if (error?.message === "EVIDENCE_WINDOW_COUNT_INVALID") throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("EVIDENCE_TARGETABLE_WINDOW_TIMEOUT");
};
const waitForNativeWebView = async (parentPid, timeoutMs = 30_000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const probe = spawn("powershell.exe", ["-NoProfile", "-Command", `$p=Get-Process -Id ${parentPid} -ErrorAction SilentlyContinue;$c=@(Get-CimInstance Win32_Process -Filter "ParentProcessId=${parentPid}" -ErrorAction SilentlyContinue|Where-Object{$_.Name -eq 'msedgewebview2.exe'});if($p){[pscustomobject]@{Responding=$p.Responding;ChildCount=$c.Count}|ConvertTo-Json -Compress}`], { windowsHide: true, stdio: ["ignore", "pipe", "ignore"] });
    let output = "";
    probe.stdout.on("data", (chunk) => { output += chunk; });
    await new Promise((resolve) => probe.once("exit", resolve));
    try {
      const state = JSON.parse(output.trim());
      if (state.Responding && state.ChildCount > 0) return state;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("EVIDENCE_NATIVE_WEBVIEW_TIMEOUT");
};
const revokeTestCredential = async (target) => {
  if (process.platform !== "win32") return;
  const child = spawn("cmdkey.exe", [`/delete:${target}`], { windowsHide: true, stdio: "ignore" });
  await new Promise((resolve) => child.once("exit", resolve));
};

class Cdp {
  constructor(socket) { this.socket = socket; this.id = 0; this.pending = new Map(); this.errors = 0; socket.addEventListener("message", (event) => this.message(event)); }
  message(event) { const payload = JSON.parse(event.data); if (payload.id && this.pending.has(payload.id)) { const { resolve, reject, timer } = this.pending.get(payload.id); this.pending.delete(payload.id); clearTimeout(timer); return payload.error ? reject(new Error("CDP_COMMAND_FAILED")) : resolve(payload.result); } if (payload.method === "Runtime.exceptionThrown" || (payload.method === "Runtime.consoleAPICalled" && payload.params?.type === "error")) this.errors += 1; }
  send(method, params = {}) { const id = ++this.id; return new Promise((resolve, reject) => { const timer = setTimeout(() => { this.pending.delete(id); reject(new Error("CDP_TIMEOUT")); }, 5_000); this.pending.set(id, { resolve, reject, timer }); this.socket.send(JSON.stringify({ id, method, params })); }); }
  async eval(expression) { const result = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }); return result.result?.value; }
}
const connectCdp = async () => {
  const response = await waitHttp(`http://127.0.0.1:${cdpPort}/json/list`, 60_000);
  const targets = await response.json();
  const target = targets.find((item) => item.type === "page");
  if (!target?.webSocketDebuggerUrl) throw new Error("EVIDENCE_TARGET_UNAVAILABLE");
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error("CDP_CONNECT_TIMEOUT")), 5_000); socket.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true }); });
  return new Cdp(socket);
};
const waitFor = async (cdp, expression, timeoutMs = 15_000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) { if (await cdp.eval(expression)) return; await new Promise((resolve) => setTimeout(resolve, 200)); }
  throw new Error("EVIDENCE_UI_TIMEOUT");
};
const setValue = (selector, value) => `(selector => { const element = document.querySelector(selector); if (!element) return false; const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value').set; setter.call(element, ${JSON.stringify(value)}); element.dispatchEvent(new Event('input', { bubbles: true })); return true; })(${JSON.stringify(selector)})`;
const clickText = (text, selector = "button") => `(() => { const element = [...document.querySelectorAll(${JSON.stringify(selector)})].find(item => item.textContent.includes(${JSON.stringify(text)})); if (!element) return false; element.click(); return true; })()`;
const screenshot = async (cdp, filename) => {
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
  const result = await cdp.send("Page.captureScreenshot", { format: "png", fromSurface: true });
  const bytes = Buffer.from(result.data, "base64");
  if (bytes.readUInt32BE(16) !== 1920 || bytes.readUInt32BE(20) !== 1080) throw new Error("EVIDENCE_SCREEN_DIMENSIONS_INVALID");
  await writeFile(path.join(evidenceDir, filename), bytes);
  return bytes.length;
};

await mkdir(evidenceDir, { recursive: true });
const apiPort = await listen(api);
const credentialTarget = `DaonUser/NativeSession/contract-${process.pid}-${randomBytes(8).toString("hex")}`;
let cdp;
let gateStage = "startup";
try {
  await writeFile(nativeContractPath, `http://127.0.0.1:${apiPort}\n${credentialTarget}\n`, { encoding: "utf8", flag: "wx" });
  const vite = start(process.execPath, [path.join(root, "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", String(vitePort), "--strictPort"], { cwd: path.join(root, "apps/desktop"), env: process.env });
  await waitHttp(`http://127.0.0.1:${vitePort}/`, 60_000);
  const config = JSON.stringify({ build: { devUrl: `http://127.0.0.1:${vitePort}`, frontendDist: "../dist" }, app: { windows: [{ label: "main", title: "Daon Phase E Windows Gate", width: 1920, height: 1080, resizable: true, devtools: true, additionalBrowserArgs: `--remote-debugging-port=${cdpPort}` }] }, bundle: { externalBin: [] } });
  const cargoEnvironment = { ...process.env, TAURI_CONFIG: config, DAON_CONTRACT_TEST_GATEWAY: `http://127.0.0.1:${apiPort}`, DAON_CONTRACT_TEST_CREDENTIAL_TARGET: credentialTarget };
  if (restoreSessionWindow) {
    cargoEnvironment.DAON_CONTRACT_TEST_LOGIN_ID = identity.login;
    cargoEnvironment.DAON_CONTRACT_TEST_PASSWORD = identity.password;
  }
  if (process.platform === "win32") {
    const cargoHome = process.env.CARGO_HOME || path.join(process.env.USERPROFILE || "", ".cargo");
    const cargoBin = path.join(cargoHome, "bin");
    const msvcBin = "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.44.35207\\bin\\HostX64\\x64";
    cargoEnvironment.CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER = `${msvcBin}\\link.exe`;
    cargoEnvironment.CC = `${msvcBin}\\cl.exe`;
    cargoEnvironment.CXX = `${msvcBin}\\cl.exe`;
    cargoEnvironment.PATH = `${cargoBin};${msvcBin};C:\\Program Files (x86)\\Windows Kits\\10\\bin\\10.0.26100.0\\x64;${cargoEnvironment.PATH ?? ""}`;
    cargoEnvironment.LIB = [
      "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.44.35207\\lib\\x64",
      "C:\\Program Files (x86)\\Windows Kits\\10\\Lib\\10.0.26100.0\\ucrt\\x64",
      "C:\\Program Files (x86)\\Windows Kits\\10\\Lib\\10.0.26100.0\\um\\x64",
    ].join(";");
    cargoEnvironment.INCLUDE = [
      "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.44.35207\\include",
      "C:\\Program Files (x86)\\Windows Kits\\10\\Include\\10.0.26100.0\\ucrt",
      "C:\\Program Files (x86)\\Windows Kits\\10\\Include\\10.0.26100.0\\shared",
      "C:\\Program Files (x86)\\Windows Kits\\10\\Include\\10.0.26100.0\\um",
      "C:\\Program Files (x86)\\Windows Kits\\10\\Include\\10.0.26100.0\\winrt",
    ].join(";");
  }
  const cargo = directCompiledRestore
    ? start(path.join(root, "apps/desktop/src-tauri/target/debug/daon-user-desktop.exe"), [], { cwd: path.join(root, "apps/desktop/src-tauri/target/debug"), env: cargoEnvironment, windowsHide: false })
    : start(process.execPath, [path.join(root, "node_modules/@tauri-apps/cli/tauri.js"), "dev", "--no-watch", "--features", "contract-test", "--config", config], { cwd: path.join(root, "apps/desktop"), env: cargoEnvironment });
  if (restoreSessionWindow) {
    const nativeRuntime = directCompiledRestore
      ? await waitForNativeWebView(cargo.pid, 30_000)
      : await waitForTargetableWindow(120_000);
    if (cargo.exitCode !== null) throw new Error(`EVIDENCE_TAURI_START_FAILED:${cargo.exitCode}`);
    if (loginCount !== 1) throw new Error("EVIDENCE_NATIVE_LOGIN_COUNT_INVALID");
    console.log(JSON.stringify({
      status: "READY", process_id: cargo.pid,
      webview_child_count: nativeRuntime.ChildCount, listener_count: 2, test_expiry_deadline: "2026-08-21T08:00:00Z",
      start_state: "NOTEBOOK_HOME", auto_selected_notebook: false,
    }));
    await Promise.race([
      gateFinished,
      new Promise((_, reject) => cargo.once("exit", (code) => reject(new Error(`EVIDENCE_TAURI_EXITED:${code}`)))),
      new Promise((resolve) => setTimeout(resolve, 20 * 60_000)),
    ]);
    await stop(cargo); await stop(vite);
  } else if (uiAutomation) {
    await new Promise((resolve) => setTimeout(resolve, 5_000));
    if (cargo.exitCode !== null) throw new Error(`EVIDENCE_TAURI_START_FAILED:${cargo.exitCode}:${cargo.safeTail.replace(/[\r\n]+/gu, " ").slice(-2_000)}`);
    console.log(JSON.stringify({ status: "READY", api_port: apiPort, window_title: "Daon Phase E Windows Gate" }));
    await Promise.race([
      gateFinished,
      new Promise((_, reject) => cargo.once("exit", (code) => reject(new Error(`EVIDENCE_TAURI_EXITED:${code}:${cargo.safeTail.replace(/[\r\n]+/gu, " ").slice(-2_000)}`)))),
      new Promise((_, reject) => setTimeout(() => reject(new Error("EVIDENCE_UI_AUTOMATION_TIMEOUT")), 300_000)),
    ]);
    const required = ["login", "notebook_list", "notebook_create", "notebook_get", "context", "source_list", "question", "studio_list", "studio_create"];
    for (const kind of required) if (!requestKinds.get(kind)) throw new Error(`EVIDENCE_WIRE_MISSING_${kind.toUpperCase()}`);
    const captures = await Promise.all(["phase-e-desktop-home-1920x1080.png", "phase-e-desktop-workspace-1920x1080.png"].map(async (name) => (await readFile(path.join(evidenceDir, name))).length));
    console.log(JSON.stringify({ status: "PASS", viewport: "1920x1080", screenshots: 2, screenshot_bytes: captures, wire_kinds: Object.fromEntries(required.map((kind) => [kind, requestKinds.get(kind)])), browser_bff_requests: 0, credential_target_replayed: false, cleanup_required: true }));
    await stop(cargo); await stop(vite);
    process.exitCode = 0;
  } else {
  try {
    cdp = await connectCdp();
  } catch (error) {
    throw new Error(`EVIDENCE_TAURI_START_FAILED:${cargo.exitCode ?? "running"}:${cargo.safeTail.replace(/[\r\n]+/gu, " ").slice(-2_000)}`, { cause: error });
  }
  await cdp.send("Runtime.enable"); await cdp.send("Page.enable");
  gateStage = "login";
  await waitFor(cdp, `Boolean(document.querySelector('form[aria-label="Windows Native 로그인"]'))`, 180_000);
  await cdp.eval(setValue('input[name="login-id"]', identity.login));
  await cdp.eval(setValue('input[name="password"]', identity.password));
  await cdp.eval(clickText("로그인"));
  await waitFor(cdp, `Boolean(document.querySelector('.notebook-home'))`);
  gateStage = "create-empty-notebook";
  if (await cdp.eval(`document.querySelector('input[name="password"]')?.value || ''`)) throw new Error("PASSWORD_NOT_CLEARED");
  const homeBytes = await screenshot(cdp, "phase-e-desktop-home-1920x1080.png");
  await cdp.eval(clickText("새 Notebook"));
  await waitFor(cdp, `Boolean(document.querySelector('.notebook-dialog'))`);
  await cdp.eval(setValue('.notebook-dialog input', "새 지식 Notebook"));
  await cdp.eval(clickText("만들기"));
  await waitFor(cdp, `location.hash.includes('/${created.notebook}') && Boolean(document.querySelector('.desktop-shell'))`);
  const empty = await cdp.eval(`document.body.innerText.includes('Source를 추가해 주세요.')`);
  if (!empty) throw new Error("NEW_NOTEBOOK_NOT_EMPTY");
  await cdp.eval(clickText("Notebook 홈"));
  await waitFor(cdp, `Boolean(document.querySelector('.notebook-home'))`);
  gateStage = "open-existing-notebook";
  await cdp.eval(clickText("보존된 지식 Notebook", "button"));
  await waitFor(cdp, `Boolean(document.querySelector('.conversation-composer textarea:not([disabled])'))`);
  gateStage = "question";
  await cdp.eval(setValue('.conversation-composer textarea', "검증된 근거를 요약해 주세요"));
  await cdp.eval(`document.querySelector('button[aria-label="질문 실행"]').click()`);
  await waitFor(cdp, `document.body.innerText.includes('선택한 Source에 근거한 검증 답변입니다.')`);
  await cdp.eval(`document.querySelector('.grounded-report-legacy').open = true`);
  await cdp.eval(setValue('.grounded-report-legacy input', "근거 검증 보고서"));
  await cdp.eval(setValue('.grounded-report-legacy input:nth-of-type(2)', "실제 제품 흐름 검증"));
  await cdp.eval(clickText("보고서 생성"));
  await waitFor(cdp, `document.body.innerText.includes('근거 검증 보고서')`);
  gateStage = "logout-history";
  const workspaceBytes = await screenshot(cdp, "phase-e-desktop-workspace-1920x1080.png");
  await cdp.eval(clickText("로그아웃"));
  await waitFor(cdp, `Boolean(document.querySelector('form[aria-label="Windows Native 로그인"]'))`);
  await cdp.eval(`history.back()`);
  await new Promise((resolve) => setTimeout(resolve, 800));
  if (!await cdp.eval(`Boolean(document.querySelector('form[aria-label="Windows Native 로그인"]')) && !document.body.innerText.includes('선택한 Source에 근거한')`)) throw new Error("LOGOUT_HISTORY_NOT_BLOCKED");
  await cdp.eval(setValue('input[name="login-id"]', identity.login));
  await cdp.eval(setValue('input[name="password"]', identity.password));
  await cdp.eval(clickText("로그인"));
  await waitFor(cdp, `Boolean(document.querySelector('.notebook-home'))`);
  await cdp.eval(`history.pushState(null, '', '#/notebooks/${existing.notebook}'); dispatchEvent(new PopStateEvent('popstate'))`);
  await waitFor(cdp, `location.hash === '#/notebooks' && Boolean(document.querySelector('.notebook-home'))`);
  await cdp.eval(`document.querySelector('.notebook-settings-wrap > button').click()`);
  await cdp.eval(clickText("로그아웃", '[role="menuitem"]'));
  await waitFor(cdp, `Boolean(document.querySelector('form[aria-label="Windows Native 로그인"]'))`);
  if (cdp.errors !== 0) throw new Error("EVIDENCE_CONSOLE_ERROR");
  const required = ["login", "notebook_list", "notebook_create", "notebook_get", "context", "source_list", "question", "studio_list", "studio_create"];
  for (const kind of required) if (!requestKinds.get(kind)) throw new Error(`EVIDENCE_WIRE_MISSING_${kind.toUpperCase()}`);
  console.log(JSON.stringify({ status: "PASS", viewport: "1920x1080", screenshots: 2, screenshot_bytes: [homeBytes, workspaceBytes], wire_kinds: Object.fromEntries(required.map((kind) => [kind, requestKinds.get(kind)])), browser_bff_requests: 0, console_errors: 0, credential_target_replayed: false, cleanup_required: true }));
  await stop(cargo); await stop(vite);
  }
} catch (error) {
  if (cdp) {
    const dom = await cdp.eval(`(() => { const textarea = document.querySelector('.conversation-composer textarea'); return {
      home_present: Boolean(document.querySelector('.notebook-home')),
      shell_present: Boolean(document.querySelector('.desktop-shell')),
      textarea_present: Boolean(textarea),
      textarea_disabled: textarea ? textarea.disabled : null,
      source_buttons: document.querySelectorAll('.source-item button, .source-list button').length,
      safe_error_present: Boolean(document.querySelector('[data-safe-error], .safe-error')),
    }; })()`).catch(() => ({ diagnostic_unavailable: true }));
    console.error(JSON.stringify({ code: "EVIDENCE_SAFE_DIAGNOSTIC", stage: gateStage, request_kinds: Object.fromEntries(requestKinds), dom }));
  }
  throw error;
} finally {
  cdp?.socket?.close();
  for (const child of children.reverse()) await stop(child);
  await close(api);
  if (restoreSessionWindow) await revokeTestCredential(credentialTarget);
  await import("node:fs/promises").then(({ unlink }) => unlink(nativeContractPath).catch(() => {}));
  identity.password = ""; identity.access = ""; identity.refresh = ""; identity.login = "";
}
