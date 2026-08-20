import { writeFile } from "node:fs/promises";

const [portValue, outputPath, mode] = process.argv.slice(2);
const port = Number(portValue);
if (!Number.isInteger(port) || !outputPath || !["readonly", "admin", "expired", "limit"].includes(mode)) {
  throw new Error("BROWSER_LICENSE_CAPTURE_INPUT_INVALID");
}

const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`, {
  signal: AbortSignal.timeout(5_000),
})).json();
const target = targets.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
if (!target) throw new Error("BROWSER_LICENSE_TARGET_UNAVAILABLE");
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error("BROWSER_LICENSE_CDP_TIMEOUT")), 5_000);
  socket.addEventListener("open", () => { clearTimeout(timeout); resolve(); }, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let sequence = 0;
function command(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`BROWSER_LICENSE_${method}_TIMEOUT`)), 5_000);
    const listener = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.id !== id) return;
      socket.removeEventListener("message", listener);
      clearTimeout(timeout);
      if (payload.error) reject(new Error(`BROWSER_LICENSE_${method}_FAILED`));
      else resolve(payload.result);
    };
    socket.addEventListener("message", listener);
    socket.send(JSON.stringify({ id, method, params }));
  });
}

await command("Page.enable");
await command("Emulation.setDeviceMetricsOverride", {
  width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false,
});
await command("Page.navigate", { url: `http://127.0.0.1:4179/?state=${mode}` });
await new Promise((resolve) => setTimeout(resolve, 1_500));
await command("Runtime.evaluate", {
  expression: `(() => {
    const settings = [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === '설정');
    settings?.click();
  })()`,
});
await new Promise((resolve) => setTimeout(resolve, 250));
await command("Runtime.evaluate", {
  expression: `(() => {
    const license = [...document.querySelectorAll('[role="menuitem"]')].find((item) => item.textContent.includes('라이선스'));
    license?.click();
  })()`,
});
await new Promise((resolve) => setTimeout(resolve, 1_000));
const evaluated = await command("Runtime.evaluate", {
  expression: `(() => {
    const dialog = document.querySelector('[role="dialog"]');
    return {
      width: innerWidth, height: innerHeight,
      text: dialog?.innerText ?? '',
      files: dialog?.querySelectorAll('input[type="file"]').length ?? 0,
      passwords: dialog?.querySelectorAll('input[type="password"]').length ?? 0,
      apply: [...(dialog?.querySelectorAll('button') ?? [])].filter((item) => item.textContent.includes('Step-up 후 검증·적용')).length,
    };
  })()`,
  returnByValue: true,
});
const view = evaluated.result?.value;
if (!view || view.width !== 1920 || view.height !== 1080 || !view.text.includes("daon-user") || !view.text.includes("enterprise")) {
  console.error(JSON.stringify(view));
  throw new Error("BROWSER_LICENSE_VIEW_INVALID");
}
if (mode === "readonly" && (view.files !== 0 || view.passwords !== 0 || view.apply !== 0 || !view.text.includes("읽기 전용"))) {
  throw new Error("BROWSER_LICENSE_READONLY_INVALID");
}
if (mode === "admin" && (view.files !== 1 || view.passwords !== 1 || view.apply !== 1)) {
  throw new Error("BROWSER_LICENSE_ADMIN_INVALID");
}
if (mode === "expired" && !view.text.includes("기존 자료 조회와 Export는 계속")) {
  throw new Error("BROWSER_LICENSE_EXPIRED_INVALID");
}
if (mode === "limit" && (!view.text.includes("한도에 도달") || !view.text.includes("잔여 0"))) {
  throw new Error("BROWSER_LICENSE_LIMIT_INVALID");
}
const capture = await command("Page.captureScreenshot", { format: "png", fromSurface: true });
const bytes = Buffer.from(capture.data, "base64");
await writeFile(outputPath, bytes);
socket.close();
console.log(JSON.stringify({ mode, viewport: `${view.width}x${view.height}`, files: view.files, passwords: view.passwords, apply: view.apply, screenshot_bytes: bytes.length }));
