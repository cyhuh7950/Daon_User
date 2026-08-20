import { writeFile } from "node:fs/promises";

const [debugPortValue, appPortValue, outputPath, mode] = process.argv.slice(2);
const debugPort = Number(debugPortValue);
const appPort = Number(appPortValue);
if (!Number.isInteger(debugPort) || !Number.isInteger(appPort) || !outputPath || !["list", "read", "download"].includes(mode)) throw new Error("BROWSER_MANUAL_CAPTURE_INPUT_INVALID");
const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`, { signal: AbortSignal.timeout(5_000) })).json();
const target = targets.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
if (!target) throw new Error("BROWSER_MANUAL_TARGET_UNAVAILABLE");
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error("BROWSER_MANUAL_CDP_TIMEOUT")), 5_000);
  socket.addEventListener("open", () => { clearTimeout(timeout); resolve(); }, { once: true });
  socket.addEventListener("error", reject, { once: true });
});
let sequence = 0;
function command(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`BROWSER_MANUAL_${method}_TIMEOUT`)), 5_000);
    const listener = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.id !== id) return;
      socket.removeEventListener("message", listener);
      clearTimeout(timeout);
      if (payload.error) reject(new Error(`BROWSER_MANUAL_${method}_FAILED`)); else resolve(payload.result);
    };
    socket.addEventListener("message", listener);
    socket.send(JSON.stringify({ id, method, params }));
  });
}
await command("Page.enable");
await command("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
await command("Page.navigate", { url: `http://127.0.0.1:${appPort}/` });
await new Promise((resolve) => setTimeout(resolve, 1_200));
await command("Runtime.evaluate", { expression: `(() => [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === '설정')?.click())()` });
await new Promise((resolve) => setTimeout(resolve, 200));
await command("Runtime.evaluate", { expression: `(() => [...document.querySelectorAll('[role="menuitem"]')].find((item) => item.textContent.includes('사용자 설명서'))?.click())()` });
await new Promise((resolve) => setTimeout(resolve, 900));
if (mode === "read") {
  await command("Runtime.evaluate", { expression: `(() => { const search = document.querySelector('.manual-hub input[type="search"]'); const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; setter.call(search, '지식'); search.dispatchEvent(new Event('input', { bubbles: true })); })()` });
  await new Promise((resolve) => setTimeout(resolve, 250));
  await command("Runtime.evaluate", { expression: `(() => [...document.querySelectorAll('.manual-document-list article > button:first-child')].find((item) => item.textContent.includes('지식·LLM'))?.click())()` });
  await new Promise((resolve) => setTimeout(resolve, 700));
}
if (mode === "download") {
  await command("Runtime.evaluate", { expression: `(() => document.querySelector('.manual-document-list article > div button:first-child')?.click())()` });
  await new Promise((resolve) => setTimeout(resolve, 900));
  await command("Runtime.evaluate", { expression: `(() => document.querySelector('.manual-document-list article > div button:last-child')?.click())()` });
  await new Promise((resolve) => setTimeout(resolve, 900));
}
const evaluated = await command("Runtime.evaluate", { expression: `(() => { const dialog = document.querySelector('[role="dialog"]'); return { width: innerWidth, height: innerHeight, text: dialog?.innerText ?? '', documents: dialog?.querySelectorAll('.manual-document-list article').length ?? 0, reader: dialog?.querySelector('.manual-reader-body')?.innerText ?? '' }; })()`, returnByValue: true });
const view = evaluated.result?.value;
if (!view || view.width !== 1920 || view.height !== 1080 || !view.text.includes("Daon 문서 Hub") || !view.text.includes("Release 1.0.0")) throw new Error("BROWSER_MANUAL_VIEW_INVALID");
if (mode === "list" && (view.documents !== 3 || !view.text.includes("DOCX") || !view.text.includes("PDF"))) throw new Error("BROWSER_MANUAL_LIST_INVALID");
if (mode === "read" && (view.documents !== 1 || !view.reader.includes("Daon 지식·LLM 활용 가이드") || !view.reader.includes("Citation"))) throw new Error("BROWSER_MANUAL_READ_INVALID");
if (mode === "download" && (view.documents !== 3 || !view.text.includes("Daon Getting Started"))) throw new Error("BROWSER_MANUAL_DOWNLOAD_INVALID");
const capture = await command("Page.captureScreenshot", { format: "png", fromSurface: true });
const bytes = Buffer.from(capture.data, "base64");
await writeFile(outputPath, bytes);
socket.close();
console.log(JSON.stringify({ mode, viewport: `${view.width}x${view.height}`, documents: view.documents, reader_chars: view.reader.length, screenshot_bytes: bytes.length }));
