import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export function resolveRepositoryRoot(moduleUrl) {
  return path.resolve(path.dirname(fileURLToPath(moduleUrl)), "..");
}

const root = resolveRepositoryRoot(import.meta.url);
const evidenceDir = path.join(root, "docs/03_evidence/release_1/R1-M8-10-WINDOWS-WEBVIEW-RECOVERY-I001");

export function buildSmokeConfig({ frontendPort, title }) {
  if (!Number.isInteger(frontendPort) || frontendPort < 1024 || frontendPort > 65535) throw new Error("SMOKE_CONFIG_INVALID");
  if (!/^[A-Za-z0-9 ]{3,80}$/u.test(title)) throw new Error("SMOKE_CONFIG_INVALID");
  return {
    build: { devUrl: `http://127.0.0.1:${frontendPort}`, frontendDist: "../dist" },
    app: { windows: [{ label: "main", title, width: 1920, height: 1080, resizable: true, devtools: false }] },
    bundle: { externalBin: [] },
  };
}

export function createSmokeExecutionPlan(hypothesis) {
  const features = {
    "production-config-no-bootstrap": [],
    "minimal-builder": ["--features", "webview-smoke"],
    "minimal-builder-unsandboxed": ["--features", "webview-smoke"],
  }[hypothesis];
  if (!features) throw new Error("SMOKE_HYPOTHESIS_INVALID");
  return {
    hypothesis,
    featureArguments: features,
    contractBootstrap: false,
    credentialAccess: false,
    userInput: false,
    timeoutMs: 120_000,
  };
}

export function classifySmokeState({ parentAlive, targetableWindow, directWebViewChildren }) {
  if (!parentAlive) return { status: "RED", safeCode: "SMOKE_PARENT_NOT_ALIVE" };
  if (!targetableWindow) return { status: "RED", safeCode: "SMOKE_WINDOW_NOT_TARGETABLE" };
  if (!Number.isInteger(directWebViewChildren) || directWebViewChildren < 1) return { status: "RED", safeCode: "SMOKE_WEBVIEW_CHILD_MISSING" };
  return { status: "PASS", safeCode: null };
}

export function selectOwnedSmokeCandidate(processes, title, launcherPid) {
  if (!Array.isArray(processes) || typeof title !== "string" || !Number.isInteger(launcherPid)) return null;
  const byPid = new Map(processes.map((process) => [process.pid, process]));
  const descendsFromLauncher = (process) => {
    let parentId = process.parentProcessId;
    const visited = new Set();
    while (Number.isInteger(parentId) && parentId > 0 && !visited.has(parentId)) {
      if (parentId === launcherPid) return true;
      visited.add(parentId);
      parentId = byPid.get(parentId)?.parentProcessId;
    }
    return false;
  };
  return processes.find((process) => process.title === title && descendsFromLauncher(process)) ?? null;
}

function start(command, args, options) {
  const child = spawn(command, args, { ...options, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let safeTail = "";
  const retain = (chunk) => { safeTail = `${safeTail}${chunk}`.slice(-2_000); };
  child.stdout.on("data", retain);
  child.stderr.on("data", retain);
  return { child, safeTail: () => safeTail };
}

async function stopOwnedProcess(pid) {
  if (!Number.isInteger(pid) || pid < 1) return;
  const killer = spawn("taskkill.exe", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
  await new Promise((resolve) => killer.once("exit", resolve));
}

async function powershellJson(script) {
  const child = spawn("powershell.exe", ["-NoProfile", "-Command", script], { windowsHide: true, stdio: ["ignore", "pipe", "ignore"] });
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  await new Promise((resolve) => child.once("exit", resolve));
  if (!output.trim()) return null;
  return JSON.parse(output.trim());
}

async function probeSmoke(title, launcherPid) {
  const escapedTitle = title.replaceAll("'", "''");
  const script = [
    `$launcher=${launcherPid}`,
    "$all=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)",
    "function Test-Owned($candidate){$seen=@{};$parent=[int]$candidate.ParentProcessId;for($depth=0;$depth -lt 32 -and $parent -gt 0;$depth++){if($parent -eq $launcher){return $true};if($seen.ContainsKey($parent)){return $false};$seen[$parent]=$true;$next=$all|Where-Object{$_.ProcessId -eq $parent}|Select-Object -First 1;if(-not $next){return $false};$parent=[int]$next.ParentProcessId};return $false}",
    `$apps=@(Get-Process -Name 'daon-user-desktop' -ErrorAction SilentlyContinue|Where-Object{$_.MainWindowTitle -eq '${escapedTitle}'})`,
    "$app=$apps|Where-Object{$appId=$_.Id;$candidate=$all|Where-Object{$_.ProcessId -eq $appId}|Select-Object -First 1;Test-Owned $candidate}|Select-Object -First 1",
    "if($app){$children=@(Get-CimInstance Win32_Process -Filter (\"ParentProcessId=\"+$app.Id) -ErrorAction SilentlyContinue|Where-Object{$_.Name -eq 'msedgewebview2.exe'});[pscustomobject]@{pid=$app.Id;parentAlive=$true;targetableWindow=([int64]$app.MainWindowHandle -ne 0 -and [bool]$app.MainWindowTitle);directWebViewChildren=$children.Count;responding=$app.Responding}|ConvertTo-Json -Compress}",
  ].join(";");
  return await powershellJson(script);
}

async function waitForSmoke(title, child, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let last = { parentAlive: false, targetableWindow: false, directWebViewChildren: 0 };
  while (Date.now() < deadline) {
    if (child.exitCode !== null) return { ...last, processExited: true, exitCode: child.exitCode };
    const observed = await probeSmoke(title, child.pid);
    if (observed) last = observed;
    if (classifySmokeState(last).status === "PASS") return last;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return last;
}

async function run() {
  const argument = process.argv.find((value) => value.startsWith("--hypothesis="));
  const plan = createSmokeExecutionPlan(argument?.slice("--hypothesis=".length) ?? "");
  if (process.platform !== "win32") throw new Error("SMOKE_WINDOWS_REQUIRED");

  const title = "Daon WebView Smoke";
  const server = createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
    response.end("<!doctype html><html><head><title>Daon WebView Smoke</title></head><body><main data-webview-smoke=\"ready\">Daon WebView Smoke</main></body></html>");
  });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const frontendPort = server.address().port;
  const overlay = buildSmokeConfig({ frontendPort, title });
  const baseConfig = JSON.parse(await readFile(path.join(root, "apps/desktop/src-tauri/tauri.conf.json"), "utf8"));
  await mkdir(evidenceDir, { recursive: true });
  const artifactSuffix = {
    "production-config-no-bootstrap": "1",
    "minimal-builder": "2",
    "minimal-builder-unsandboxed": "3",
  }[plan.hypothesis];
  await writeFile(path.join(evidenceDir, `smoke-hypothesis-${artifactSuffix}-config.json`), `${JSON.stringify({ hypothesis: plan.hypothesis, base: { identifier: baseConfig.identifier, productName: baseConfig.productName }, overlay }, null, 2)}\n`);

  const tauri = start(process.execPath, [path.join(root, "node_modules/@tauri-apps/cli/tauri.js"), "dev", "--no-watch", ...plan.featureArguments, "--config", JSON.stringify(overlay)], {
    cwd: path.join(root, "apps/desktop"),
    env: { ...process.env, TAURI_CONFIG: JSON.stringify(overlay) },
  });
  let observed;
  try {
    observed = await waitForSmoke(title, tauri.child, plan.timeoutMs);
    const classification = classifySmokeState(observed);
    const result = {
      hypothesis: plan.hypothesis,
      status: classification.status,
      safe_code: classification.safeCode,
      parent_alive: Boolean(observed.parentAlive),
      parent_responding: Boolean(observed.responding),
      targetable_window: Boolean(observed.targetableWindow),
      direct_webview_children: Number(observed.directWebViewChildren) || 0,
      process_exited: Boolean(observed.processExited),
      input_events: 0,
      credential_access: 0,
      timeout_ms: plan.timeoutMs,
    };
    await writeFile(path.join(evidenceDir, `smoke-hypothesis-${artifactSuffix}-result.json`), `${JSON.stringify(result, null, 2)}\n`);
    console.log(JSON.stringify(result));
    if (classification.status !== "PASS") process.exitCode = 2;
  } finally {
    await stopOwnedProcess(tauri.child.pid);
    await new Promise((resolve) => server.close(resolve));
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) await run();
