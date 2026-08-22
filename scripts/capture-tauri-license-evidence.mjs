import { writeFile } from "node:fs/promises";

const [portValue, outputPath, expectedMode] = process.argv.slice(2);
const port = Number(portValue);
if (!Number.isInteger(port) || port < 1024 || port > 65535 || !outputPath || !["readonly", "admin", "expired"].includes(expectedMode)) {
  throw new Error("TAURI_LICENSE_CAPTURE_INPUT_INVALID");
}

const targetsResponse = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(5_000) });
const targets = await targetsResponse.json();
const target = targets.find((candidate) => candidate.type === "page" && ["http://tauri.localhost/", "http://127.0.0.1:4199/"].includes(candidate.url));
if (!target?.webSocketDebuggerUrl) throw new Error("TAURI_LICENSE_TARGET_UNAVAILABLE");

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error("TAURI_LICENSE_CDP_TIMEOUT")), 5_000);
  socket.addEventListener("open", () => { clearTimeout(timeout); resolve(); }, { once: true });
  socket.addEventListener("error", () => { clearTimeout(timeout); reject(new Error("TAURI_LICENSE_CDP_UNAVAILABLE")); }, { once: true });
});

let sequence = 0;
function command(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`TAURI_LICENSE_CDP_${method}_TIMEOUT`)), 5_000);
    const onMessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.id !== id) return;
      socket.removeEventListener("message", onMessage);
      clearTimeout(timeout);
      if (payload.error) reject(new Error(`TAURI_LICENSE_CDP_${method}_FAILED`));
      else resolve(payload.result);
    };
    socket.addEventListener("message", onMessage);
    socket.send(JSON.stringify({ id, method, params }));
  });
}

await command("Page.bringToFront");

const projection = await command("Runtime.evaluate", {
  expression: `(() => {
    const dialog = document.querySelector('[role="dialog"]');
    return {
      title: document.title,
      outerWidth,
      outerHeight,
      viewportWidth: innerWidth,
      viewportHeight: innerHeight,
      dialogText: dialog?.innerText ?? "",
      fileControls: dialog?.querySelectorAll('input[type="file"]').length ?? 0,
      passwordControls: dialog?.querySelectorAll('input[type="password"]').length ?? 0,
      applyControls: [...(dialog?.querySelectorAll('button') ?? [])].filter((button) => button.textContent.includes('Step-up 후 검증·적용')).length,
      readonlyNote: dialog?.innerText.includes('일반 사용자는 라이선스 정보를 읽기 전용으로 확인합니다.') ?? false,
      expiredNote: dialog?.innerText.includes('새 생성은 중단되며 기존 자료 조회와 Export는 계속 사용할 수 있습니다.') ?? false,
      activeText: document.activeElement?.textContent?.trim() ?? "",
    };
  })()`,
  returnByValue: true,
});
const view = projection.result?.value;
if (!view || !view.dialogText.includes("daon-user") || !view.dialogText.includes("enterprise")) {
  throw new Error("TAURI_LICENSE_DIALOG_INVALID");
}
if (expectedMode === "readonly" && (!view.readonlyNote || view.fileControls !== 0 || view.passwordControls !== 0 || view.applyControls !== 0)) {
  throw new Error("TAURI_LICENSE_READONLY_INVALID");
}
if (expectedMode === "admin" && (view.fileControls !== 1 || view.passwordControls !== 1 || view.applyControls !== 1)) {
  throw new Error("TAURI_LICENSE_ADMIN_INVALID");
}
if (expectedMode === "expired" && !view.expiredNote) throw new Error("TAURI_LICENSE_EXPIRED_INVALID");

await command("Page.enable");
const screenshot = await command("Page.captureScreenshot", { format: "png", fromSurface: true });
const bytes = Buffer.from(screenshot.data, "base64");
if (bytes.length < 10_000) throw new Error("TAURI_LICENSE_SCREENSHOT_INVALID");
await writeFile(outputPath, bytes);
socket.close();
console.log(JSON.stringify({
  mode: expectedMode,
  outer: `${view.outerWidth}x${view.outerHeight}`,
  viewport: `${view.viewportWidth}x${view.viewportHeight}`,
  file_controls: view.fileControls,
  password_controls: view.passwordControls,
  apply_controls: view.applyControls,
  readonly_note: view.readonlyNote,
  expired_note: view.expiredNote,
  screenshot_bytes: bytes.length,
}));
