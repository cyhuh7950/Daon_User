import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const edge = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitForReady(url, child) {
  for (let attempt = 0; attempt < 90; attempt += 1) {
    assert.equal(child.exitCode, null, "Next 개발 서버가 조기 종료되면 안 됩니다");
    try {
      if ((await fetch(url)).status === 200) return;
    } catch { /* The owned server is still starting. */ }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.fail("Next 개발 서버가 시험 시간 안에 준비되어야 합니다");
}

test("저장된 어두운 테마가 첫 지정부터 유지되고 hydration 경고가 없다", { skip: !existsSync(edge) }, async () => {
  const port = await freePort();
  const url = `http://127.0.0.1:${port}/settings/screen`;
  const profile = mkdtempSync(path.join(tmpdir(), "daon-theme-hydration-"));
  const server = spawn(process.execPath, [path.join(root, "node_modules/next/dist/bin/next"), "dev", "--hostname", "127.0.0.1", "--port", String(port)], {
    cwd: path.join(root, "apps/web"), windowsHide: true, stdio: "ignore",
    env: { ...process.env, DAON_RUNTIME_PROFILE: "local_test", DAON_BFF_PROFILE: "local_test" },
  });
  let browser;
  let socket;
  let send;
  try {
    await waitForReady(url, server);
    browser = spawn(edge, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
      "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore", windowsHide: true });
    let cdpPort;
    for (let attempt = 0; attempt < 50; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 200));
      try { cdpPort = Number(readFileSync(path.join(profile, "DevToolsActivePort"), "utf8").split(/\r?\n/u)[0]); break; }
      catch { /* The owned browser is still starting. */ }
    }
    assert.ok(cdpPort, "격리 브라우저 CDP 포트가 열려야 합니다");
    const targets = await (await fetch(`http://127.0.0.1:${cdpPort}/json/list`)).json();
    const target = targets.find((entry) => entry.type === "page");
    assert.ok(target?.webSocketDebuggerUrl);
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    let sequence = 0;
    const pending = new Map();
    const consoleMessages = [];
    socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      if (message.method === "Runtime.consoleAPICalled") {
        consoleMessages.push(message.params.args.map((entry) => entry.value ?? entry.description ?? "").join(" "));
      }
      const request = pending.get(message.id);
      if (!request) return;
      pending.delete(message.id);
      message.error ? request.reject(new Error(JSON.stringify(message.error))) : request.resolve(message.result);
    });
    send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 10_000);
      timeout.unref();
      pending.set(id, { resolve: (value) => { clearTimeout(timeout); resolve(value); },
        reject: (error) => { clearTimeout(timeout); reject(error); } });
      socket.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.addScriptToEvaluateOnNewDocument", {
      source: `localStorage.setItem('daon.screen-preference.v1','dark');
        const originalSetAttribute=Element.prototype.setAttribute;
        Element.prototype.setAttribute=function(name,value){
          if(this===document.documentElement&&name==='data-theme'&&window.__firstThemeAssignment===undefined){
            window.__firstThemeAssignment=value;
          }
          return originalSetAttribute.apply(this,arguments);
        };`,
    });
    await send("Page.navigate", { url });
    let theme;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const result = await send("Runtime.evaluate", {
        expression: "({ready:document.readyState,first:window.__firstThemeAssignment,theme:document.documentElement.dataset.theme,color:document.documentElement.style.colorScheme})",
        returnByValue: true,
      });
      theme = result.result.value;
      if (theme?.ready === "complete" && theme.theme === "dark") break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert.equal(theme?.first, "dark", "문서의 첫 테마 지정부터 저장된 값이어야 합니다");
    assert.equal(theme?.theme, "dark");
    assert.equal(theme?.color, "dark");
    await new Promise((resolve) => setTimeout(resolve, 1000));
    assert.deepEqual(consoleMessages.filter((value) => /hydrated but some attributes|hydration mismatch/iu.test(value)), []);
  } finally {
    if (send) await Promise.race([send("Browser.close").catch(() => {}),
      new Promise((resolve) => setTimeout(resolve, 1000))]);
    socket?.close();
    browser?.kill();
    server.kill();
    await Promise.all([browser, server].filter(Boolean).map((child) => child.exitCode !== null
      ? Promise.resolve()
      : Promise.race([new Promise((resolve) => child.once("exit", resolve)),
        new Promise((resolve) => setTimeout(resolve, 5000))])));
    assert.ok(profile.startsWith(`${tmpdir()}${path.sep}`));
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try { rmSync(profile, { recursive: true, force: true, maxRetries: 2, retryDelay: 100 }); break; }
      catch (error) {
        if (attempt === 19) throw error;
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
    }
  }
});
